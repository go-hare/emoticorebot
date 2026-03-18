"""Right-brain runtime that owns DeepAgent runs and lifecycle control."""

from __future__ import annotations

import asyncio
from typing import Any, Mapping
from uuid import uuid4

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.commands import RightBrainJobRequestPayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import (
    DeliveryTargetPayload,
    MemoryCandidatePayload,
    RightBrainAcceptedPayload,
    RightBrainProgressPayload,
    RightBrainRejectedPayload,
    RightBrainResultPayload,
)
from emoticorebot.protocol.memory_models import ReflectSignalPayload
from emoticorebot.protocol.task_models import ContentBlock, MessageRef, TaskRequestSpec
from emoticorebot.protocol.topics import EventType
from emoticorebot.right.deep_agent_executor import DeepAgentExecutor
from emoticorebot.right.state_machine import RightBrainState
from emoticorebot.right.store import RightBrainRecord, RightBrainStore
from emoticorebot.right.tool_runtime import AuditInterrupt, AuditSignal
from emoticorebot.utils.right_brain_projection import project_task_from_runtime_snapshot


class RightBrainRuntime:
    """Constant resident runtime that starts and controls DeepAgent runs."""

    DEFAULT_TASK_TIMEOUT_S = 120.0

    def __init__(
        self,
        *,
        bus: PriorityPubSubBus,
        worker_llm: Any | None = None,
        context_builder: Any | None = None,
        tool_registry: Any | None = None,
        task_store: RightBrainStore | None = None,
        executor: DeepAgentExecutor | None = None,
    ) -> None:
        self._bus = bus
        self._tasks = task_store or RightBrainStore()
        self._executor = executor
        if self._executor is None and worker_llm is not None and context_builder is not None:
            self._executor = DeepAgentExecutor(worker_llm=worker_llm, tool_registry=tool_registry, context_builder=context_builder)
        self._active_runs: dict[str, asyncio.Task[None]] = {}

    def register(self) -> None:
        self._bus.subscribe(
            consumer="right_runtime",
            event_type=EventType.RIGHT_COMMAND_JOB_REQUESTED,
            handler=self._dispatch_right_job,
        )

    async def stop(self) -> None:
        active = list(self._active_runs.values())
        for task in active:
            if not task.done():
                task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        self._active_runs.clear()

    @property
    def task_store(self) -> RightBrainStore:
        return self._tasks

    async def _dispatch_right_job(self, event: BusEnvelope[RightBrainJobRequestPayload]) -> None:
        payload = event.payload
        if payload.job_action == "cancel_task":
            await self._cancel_job(event)
            return

        try:
            record = self._prepare_record(event)
        except Exception as exc:
            await self._publish_rejected(
                event=event,
                task_id=str(payload.task_id or "").strip() or None,
                job_id=payload.job_id,
                reason=str(exc),
            )
            return

        if self._executor is None:
            await self._publish_rejected(
                event=event,
                task_id=record.task_id,
                job_id=record.job_id,
                reason="RightBrainRuntime 当前没有可用的 DeepAgent 执行器。",
            )
            record.mark_done(
                result="failed",
                summary="DeepAgent 不可用",
                error="RightBrainRuntime 当前没有可用的 DeepAgent 执行器。",
                decision="reject",
            )
            return

        active = self._active_runs.get(record.task_id)
        if active is not None and not active.done():
            await self._publish_rejected(
                event=event,
                task_id=record.task_id,
                job_id=record.job_id,
                reason="当前右脑 run 仍在执行中，不能重复启动。",
            )
            return

        run_task = asyncio.create_task(self._run_record(record, event), name=f"right-brain:{record.task_id}")
        self._active_runs[record.task_id] = run_task
        run_task.add_done_callback(lambda finished, task_id=record.task_id: self._active_runs.pop(task_id, None))
        run_task.add_done_callback(self._consume_background_task_result)

    def _prepare_record(self, event: BusEnvelope[RightBrainJobRequestPayload]) -> RightBrainRecord:
        payload = event.payload
        if payload.job_action == "create_task":
            return self._create_record(event)
        if payload.job_action != "resume_task":
            raise RuntimeError(f"unsupported right brain job_action: {payload.job_action}")

        task_id = str(payload.task_id or "").strip()
        if not task_id:
            raise RuntimeError("resume_task requires task_id")
        record = self._tasks.get(task_id)
        if record is None:
            raise RuntimeError(f"unknown task_id: {task_id}")
        if record.state is RightBrainState.DONE:
            raise RuntimeError("该右脑任务已经结束，不能继续 resume_task")

        self._merge_resume_request(record, payload)
        return record

    def _create_record(self, event: BusEnvelope[RightBrainJobRequestPayload]) -> RightBrainRecord:
        payload = event.payload
        request_text = str(payload.request_text or payload.source_text or "").strip()
        if not request_text:
            raise RuntimeError("create_task requires request_text")

        context = dict(payload.context or {})
        task_id = str(payload.task_id or "").strip() or f"task_{uuid4().hex[:12]}"
        origin_message = self._origin_message_from_context(context)
        delivery_target = self._resolve_delivery_target(payload=payload, origin_message=origin_message)
        title = (
            str(context.get("title", "") or "").strip()
            or str(payload.goal or "").strip()
            or request_text[:48]
        )
        record = RightBrainRecord(
            task_id=task_id,
            session_id=str(event.session_id or "").strip(),
            turn_id=event.turn_id,
            job_id=payload.job_id,
            request=self._build_request_spec(payload, context),
            title=title,
            origin_message=origin_message,
            right_brain_strategy=payload.right_brain_strategy,
            preferred_delivery_mode=delivery_target.delivery_mode,
            delivery_target=delivery_target,
            job_kind=str(payload.job_kind or "execution_review").strip() or "execution_review",
            source_text=str(payload.source_text or "").strip(),
            raw_context=context,
            metadata=dict(payload.metadata or {}),
            suppress_delivery=bool(context.get("suppress_delivery")),
        )
        self._tasks.add(record)
        return record

    @staticmethod
    def _build_request_spec(payload: RightBrainJobRequestPayload, context: Mapping[str, Any]) -> TaskRequestSpec:
        return TaskRequestSpec(
            request=str(payload.request_text or payload.source_text or "").strip(),
            title=str(context.get("title", "") or "").strip() or None,
            goal=str(payload.goal or "").strip() or None,
            expected_output=str(context.get("expected_output", "") or "").strip() or None,
            constraints=RightBrainRuntime._string_list(context.get("constraints")),
            success_criteria=RightBrainRuntime._string_list(context.get("success_criteria")),
            history_context=str(context.get("history_context", "") or "").strip() or None,
            content_blocks=RightBrainRuntime._content_blocks(context.get("content_blocks")),
            memory_refs=RightBrainRuntime._string_list(context.get("memory_refs")),
            skill_hints=RightBrainRuntime._string_list(context.get("skill_hints")),
            review_policy=None,
            preferred_agent=None,
        )

    @staticmethod
    def _origin_message_from_context(context: Mapping[str, Any]) -> MessageRef | None:
        value = context.get("origin_message")
        if not isinstance(value, Mapping):
            return None
        try:
            return MessageRef.model_validate(dict(value))
        except Exception:
            return None

    def _resolve_delivery_target(
        self,
        *,
        payload: RightBrainJobRequestPayload,
        origin_message: MessageRef | None,
    ) -> DeliveryTargetPayload:
        target = payload.delivery_target
        if target is not None:
            delivery_mode = str(target.delivery_mode or "").strip()
            if delivery_mode in {"inline", "push", "stream"}:
                return target
        return DeliveryTargetPayload(
            delivery_mode=self._preferred_delivery_mode(payload.right_brain_strategy),
            channel=origin_message.channel if origin_message is not None else None,
            chat_id=origin_message.chat_id if origin_message is not None else None,
        )

    async def _run_record(self, record: RightBrainRecord, event: BusEnvelope[RightBrainJobRequestPayload]) -> None:
        assert self._executor is not None

        async def _on_progress(message: str, payload: dict[str, Any]) -> None:
            await self._handle_progress(record=record, source_event=event, message=message, payload=payload)

        async def _on_audit(signal: AuditSignal) -> None:
            await self._handle_audit_signal(record=record, source_event=event, signal=signal)

        self._executor.tool_runtime.bind_audit_handler(_on_audit)
        try:
            result = await self._executor.execute(
                self._build_executor_task_spec(record),
                task_id=record.task_id,
                progress_reporter=_on_progress,
                trace_reporter=_on_progress,
            )
        except AuditInterrupt:
            return
        except asyncio.CancelledError:
            if record.state is RightBrainState.DONE:
                return
            raise
        except Exception as exc:
            if record.state is RightBrainState.DONE:
                return
            if not record.accepted:
                await self._publish_rejected(
                    event=event,
                    task_id=record.task_id,
                    job_id=record.job_id,
                    reason=str(exc),
                )
                record.mark_done(
                    result="failed",
                    summary=str(exc),
                    error=str(exc),
                    decision="reject",
                )
                await self._trigger_reflection(record=record, source_event=event, reason="right_brain_rejected")
                return
            await self._publish_result(
                event=event,
                record=record,
                decision="accept",
                summary="右脑执行失败",
                result_text=str(exc),
                metadata={"result": "failed", "failure_reason": str(exc)},
            )
            await self._trigger_reflection(record=record, source_event=event, reason="right_brain_failed")
            return
        finally:
            self._executor.tool_runtime.clear()

        if record.state is RightBrainState.DONE:
            return

        await self._handle_execution_result(record=record, source_event=event, result=result)

    def _build_executor_task_spec(self, record: RightBrainRecord) -> dict[str, Any]:
        recent_turns = self._recent_turns(record.raw_context.get("recent_turns"))
        short_term = self._string_list(record.raw_context.get("short_term_memory"))
        long_term = self._string_list(record.raw_context.get("long_term_memory"))
        tool_context = self._tool_context(record.raw_context.get("tool_context"))
        if not tool_context.get("available_tools") and self._executor is not None and getattr(self._executor, "tools", None) is not None:
            tool_names = list(getattr(self._executor.tools, "tool_names", []) or [])
            tool_context["available_tools"] = [str(name).strip() for name in tool_names if str(name).strip()]

        history_context = str(record.request.history_context or "").strip()
        memory_context_parts: list[str] = []
        if short_term:
            memory_context_parts.append("短期记忆摘要：\n- " + "\n- ".join(short_term[:6]))
        if long_term:
            memory_context_parts.append("长期记忆摘要：\n- " + "\n- ".join(long_term[:6]))
        if tool_context:
            tool_lines: list[str] = []
            available = self._string_list(tool_context.get("available_tools"))
            constraints = self._string_list(tool_context.get("tool_constraints"))
            if available:
                tool_lines.append("可用工具：\n- " + "\n- ".join(available[:10]))
            if constraints:
                tool_lines.append("工具约束：\n- " + "\n- ".join(constraints[:10]))
            if tool_lines:
                memory_context_parts.append("\n".join(tool_lines))

        context_blocks = [history_context] if history_context else []
        context_blocks.extend(memory_context_parts)

        return {
            "session_id": record.session_id,
            "channel": record.origin_message.channel if record.origin_message is not None else "",
            "chat_id": record.origin_message.chat_id if record.origin_message is not None else "",
            "request": record.request.request,
            "goal": record.request.goal,
            "expected_output": record.request.expected_output,
            "constraints": list(record.request.constraints),
            "success_criteria": list(record.request.success_criteria),
            "history": recent_turns,
            "history_context": "\n\n".join(part for part in context_blocks if part).strip(),
            "memory_refs": list(record.request.memory_refs),
            "skill_hints": list(record.request.skill_hints),
            "task_context": {
                "short_term_memory": short_term[:6],
                "long_term_memory": long_term[:6],
                "tool_context": tool_context,
            },
            "media": self._media_paths(record.request.content_blocks),
            "timeout_s": self._task_timeout(record.raw_context),
        }

    async def _handle_audit_signal(
        self,
        *,
        record: RightBrainRecord,
        source_event: BusEnvelope[RightBrainJobRequestPayload],
        signal: AuditSignal,
    ) -> None:
        if signal.decision == "accept":
            await self._ensure_accepted(
                record=record,
                source_event=source_event,
                reason=signal.reason or "audit_tool 返回任务可以开始。",
                stage="execute",
                metadata=dict(signal.metadata or {}),
            )
            return
        if signal.decision == "reject":
            await self._publish_rejected(
                event=source_event,
                task_id=record.task_id,
                job_id=record.job_id,
                reason=signal.reason or signal.summary or "右脑拒绝执行当前请求。",
                metadata=dict(signal.metadata or {}),
            )
            record.mark_done(
                result="failed",
                summary=signal.summary or signal.reason or "右脑拒绝执行当前请求。",
                error=signal.reason or signal.summary or "right_brain_rejected",
                decision="reject",
            )
            record.append_trace(
                kind="warning",
                message=signal.reason or signal.summary or "右脑拒绝执行当前请求。",
                data={"decision": "reject", **dict(signal.metadata or {})},
            )
            await self._trigger_reflection(record=record, source_event=source_event, reason="right_brain_rejected")
            return

        await self._publish_result(
            event=source_event,
            record=record,
            decision="answer_only",
            summary=signal.summary or signal.reason or "右脑直接返回理性答案素材。",
            result_text=signal.result_text or signal.summary or signal.reason,
            metadata=dict(signal.metadata or {}),
        )
        await self._trigger_reflection(record=record, source_event=source_event, reason="right_brain_answer_only")

    async def _handle_progress(
        self,
        *,
        record: RightBrainRecord,
        source_event: BusEnvelope[RightBrainJobRequestPayload],
        message: str,
        payload: Mapping[str, Any],
    ) -> None:
        if record.state is RightBrainState.DONE:
            return
        await self._ensure_accepted(
            record=record,
            source_event=source_event,
            reason="右脑 run 已进入执行阶段。",
            stage="execute",
            metadata={"auto_accepted": True},
        )

        summary = str(message or "").strip()
        if not summary:
            return
        progress_value = self._coerce_progress(payload)
        next_step = self._payload_next_step(payload)
        stage = self._payload_stage(payload)
        metadata = self._progress_metadata(payload)

        record.summary = summary
        record.last_progress = summary
        record.progress = progress_value
        record.next_step = next_step
        record.touch()
        record.append_trace(
            kind=self._trace_kind_for_progress(metadata),
            message=summary,
            data={
                "stage": stage,
                "progress": progress_value,
                "next_step": next_step,
                **metadata,
            },
            ts=record.updated_at,
        )

        await self._bus.publish(
            build_envelope(
                event_type=EventType.RIGHT_EVENT_PROGRESS,
                source="right_runtime",
                target="broadcast",
                session_id=record.session_id,
                turn_id=record.turn_id,
                task_id=record.task_id,
                correlation_id=record.task_id,
                causation_id=source_event.event_id,
                payload=RightBrainProgressPayload(
                    job_id=record.job_id,
                    stage=stage,
                    summary=summary,
                    progress=progress_value,
                    next_step=next_step or None,
                    metadata={
                        "right_brain_strategy": record.right_brain_strategy,
                        "job_kind": record.job_kind,
                        **metadata,
                    },
                ),
            )
        )

    async def _handle_execution_result(
        self,
        *,
        record: RightBrainRecord,
        source_event: BusEnvelope[RightBrainJobRequestPayload],
        result: Mapping[str, Any],
    ) -> None:
        status = str(result.get("status", "") or "").strip()
        control_state = str(result.get("control_state", "") or "").strip()
        message = str(result.get("message", "") or "").strip()
        analysis = str(result.get("analysis", "") or "").strip()
        summary = analysis or message or "右脑任务执行完成。"

        await self._ensure_accepted(
            record=record,
            source_event=source_event,
            reason="right runtime 收到执行结果，任务已开始。",
            stage="execute",
            metadata={"auto_accepted": True},
        )

        for item in list(result.get("task_trace", []) or []):
            if isinstance(item, Mapping):
                record.trace_log.append(dict(item))

        if control_state == "failed" or status == "failed":
            await self._publish_result(
                event=source_event,
                record=record,
                decision="accept",
                summary=summary or "右脑执行失败。",
                result_text=message or analysis or "右脑执行失败。",
                metadata={"result": "failed", "failure_reason": message or analysis},
            )
            await self._trigger_reflection(record=record, source_event=source_event, reason="right_brain_failed")
            return
        if control_state == "waiting_input":
            await self._publish_result(
                event=source_event,
                record=record,
                decision="accept",
                summary=summary or "右脑执行中发现缺少必要信息。",
                result_text=message or analysis or "当前右脑 run 缺少继续执行所需的信息。",
                metadata={"result": "failed", "failure_reason": "missing_required_input"},
            )
            await self._trigger_reflection(record=record, source_event=source_event, reason="right_brain_failed")
            return

        await self._publish_result(
            event=source_event,
            record=record,
            decision="accept",
            summary=summary,
            result_text=message or summary,
            metadata={"result": "success" if status != "partial" else "success", "control_state": control_state, "status": status},
        )
        await self._trigger_reflection(record=record, source_event=source_event, reason="right_brain_result")

    async def _cancel_job(self, event: BusEnvelope[RightBrainJobRequestPayload]) -> None:
        task_id = str(event.payload.task_id or "").strip()
        if not task_id:
            await self._publish_rejected(
                event=event,
                task_id=None,
                job_id=event.payload.job_id,
                reason="cancel_task requires task_id",
            )
            return

        record = self._tasks.get(task_id)
        if record is None:
            await self._publish_rejected(
                event=event,
                task_id=task_id,
                job_id=event.payload.job_id,
                reason=f"unknown task_id: {task_id}",
            )
            return
        if record.state is RightBrainState.DONE:
            await self._publish_rejected(
                event=event,
                task_id=task_id,
                job_id=event.payload.job_id,
                reason="该右脑任务已经结束，无法再取消。",
            )
            return

        reason = str(event.payload.request_text or event.payload.context.get("reason", "") or "user_cancelled").strip()
        await self._publish_result(
            event=event,
            record=record,
            decision="accept",
            summary="右脑任务已取消。",
            result_text=reason,
            metadata={"result": "cancelled", "cancel_reason": reason},
        )
        await self._trigger_reflection(record=record, source_event=event, reason="right_brain_cancelled")

        active = self._active_runs.get(task_id)
        if active is not None and not active.done():
            active.cancel()

    async def _ensure_accepted(
        self,
        *,
        record: RightBrainRecord,
        source_event: BusEnvelope[RightBrainJobRequestPayload],
        reason: str,
        stage: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if record.accepted or record.state is RightBrainState.DONE:
            return
        record.accepted = True
        record.summary = str(reason or "").strip() or record.summary
        record.last_progress = record.last_progress or record.summary
        record.touch()
        record.append_trace(
            kind="status",
            message=reason or "任务可以开始。",
            data={"decision": "accept", "stage": stage, **dict(metadata or {})},
            ts=record.updated_at,
        )
        await self._bus.publish(
            build_envelope(
                event_type=EventType.RIGHT_EVENT_JOB_ACCEPTED,
                source="right_runtime",
                target="broadcast",
                session_id=record.session_id,
                turn_id=record.turn_id,
                task_id=record.task_id,
                correlation_id=record.task_id,
                causation_id=source_event.event_id,
                payload=RightBrainAcceptedPayload(
                    job_id=record.job_id,
                    stage=stage,
                    reason=reason or None,
                    estimated_duration_s=self._estimated_duration(record.raw_context),
                    metadata={
                        "right_brain_strategy": record.right_brain_strategy,
                        "job_kind": record.job_kind,
                        **dict(metadata or {}),
                    },
                ),
            )
        )

    async def _publish_rejected(
        self,
        *,
        event: BusEnvelope[RightBrainJobRequestPayload],
        task_id: str | None,
        job_id: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._bus.publish(
            build_envelope(
                event_type=EventType.RIGHT_EVENT_JOB_REJECTED,
                source="right_runtime",
                target="broadcast",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=task_id,
                correlation_id=task_id or event.correlation_id or event.turn_id or job_id,
                causation_id=event.event_id,
                payload=RightBrainRejectedPayload(
                    job_id=job_id,
                    reason=str(reason or "").strip() or "右脑拒绝执行当前请求。",
                    metadata={
                        "right_brain_strategy": event.payload.right_brain_strategy,
                        "job_kind": str(event.payload.job_kind or "execution_review").strip() or "execution_review",
                        **dict(metadata or {}),
                    },
                ),
            )
        )

    async def _publish_result(
        self,
        *,
        event: BusEnvelope[RightBrainJobRequestPayload],
        record: RightBrainRecord,
        decision: str,
        summary: str,
        result_text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        final_summary = str(summary or "").strip() or None
        final_text = str(result_text or "").strip() or None
        final_metadata = dict(metadata or {})
        outcome = str(final_metadata.get("result", "") or "").strip()

        if outcome == "failed":
            result = "failed"
            error = final_text or final_summary or "right_brain_failed"
        elif outcome == "cancelled":
            result = "cancelled"
            error = final_text or final_summary or "right_brain_cancelled"
        else:
            result = "success"
            error = ""

        record.mark_done(
            result=result,
            summary=final_summary,
            error=error or None,
            decision=decision,
            result_text=final_text,
        )
        record.append_trace(
            kind="summary" if result == "success" else ("warning" if result == "cancelled" else "error"),
            message=final_text or final_summary or "右脑任务结束",
            data={"decision": decision, "result": result, **final_metadata},
            ts=record.updated_at,
        )

        await self._bus.publish(
            build_envelope(
                event_type=EventType.RIGHT_EVENT_RESULT_READY,
                source="right_runtime",
                target="broadcast",
                session_id=record.session_id,
                turn_id=record.turn_id,
                task_id=record.task_id,
                correlation_id=record.task_id,
                causation_id=event.event_id,
                payload=RightBrainResultPayload(
                    job_id=record.job_id,
                    decision=decision,  # type: ignore[arg-type]
                    summary=final_summary,
                    result_text=final_text,
                    artifacts=[],
                    delivery_target=record.delivery_target,
                    memory_candidate=MemoryCandidatePayload(
                        kind="execution",
                        summary=final_summary or final_text or f"{record.task_id} completed",
                    ),
                    metadata={
                        "result": result,
                        "right_brain_strategy": record.right_brain_strategy,
                        "job_kind": record.job_kind,
                        **final_metadata,
                    },
                ),
            )
        )

    async def _trigger_reflection(
        self,
        *,
        record: RightBrainRecord,
        source_event: BusEnvelope[RightBrainJobRequestPayload],
        reason: str,
    ) -> None:
        trace = list(record.trace_log[-20:])
        recent_turns = self._recent_turns(record.raw_context.get("recent_turns"))
        short_term = self._string_list(record.raw_context.get("short_term_memory"))[:6]
        long_term = self._string_list(record.raw_context.get("long_term_memory"))[:6]
        tool_context = self._tool_context(record.raw_context.get("tool_context"))
        task_projection = project_task_from_runtime_snapshot(
            record.snapshot().model_dump(exclude_none=True),
            params=record.request.model_dump(exclude_none=True),
            trace=trace,
        )
        await self._bus.publish(
            build_envelope(
                event_type=EventType.REFLECT_LIGHT,
                source="right_runtime",
                target="memory_governor",
                session_id=record.session_id,
                turn_id=record.turn_id,
                task_id=record.task_id,
                correlation_id=record.task_id,
                causation_id=source_event.event_id,
                payload=ReflectSignalPayload(
                    trigger_id=f"reflect_{uuid4().hex[:12]}",
                    reason=reason,
                    source_event_id=source_event.event_id,
                    task_id=record.task_id,
                    metadata={
                        "right_brain_summary": {
                            "session_id": record.session_id,
                            "turn_id": record.turn_id or "",
                            "origin_message": record.origin_message.model_dump(exclude_none=True)
                            if record.origin_message is not None
                            else {},
                            "request_text": record.request.request,
                            "summary": record.summary,
                            "result_text": record.final_result_text,
                            "result": record.result,
                            "decision": record.final_decision or "",
                            "error": record.error,
                            "cancel_reason": record.final_result_text if record.result == "cancelled" else "",
                            "source_event_type": (
                                EventType.RIGHT_EVENT_RESULT_READY
                                if record.final_decision != "reject"
                                else EventType.RIGHT_EVENT_JOB_REJECTED
                            ),
                            "task": task_projection,
                            "task_trace": trace,
                            "tool_usage_summary": self._tool_usage_summary(trace),
                            "recent_turns": recent_turns,
                            "short_term_memory": short_term,
                            "long_term_memory": long_term,
                            "memory_refs": list(record.request.memory_refs),
                            "tool_context": tool_context,
                        },
                    },
                ),
            )
        )

    def _merge_resume_request(self, record: RightBrainRecord, payload: RightBrainJobRequestPayload) -> None:
        if payload.request_text:
            record.source_text = str(payload.request_text or "").strip()
        extra_context = dict(payload.context or {})
        if extra_context:
            record.raw_context.update(extra_context)
        if payload.delivery_target is not None:
            record.delivery_target = self._resolve_delivery_target(payload=payload, origin_message=record.origin_message)
            record.preferred_delivery_mode = record.delivery_target.delivery_mode
        record.job_id = payload.job_id
        record.right_brain_strategy = payload.right_brain_strategy
        record.metadata.update(dict(payload.metadata or {}))
        record.touch()

    @staticmethod
    def _recent_turns(value: object) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        turns: list[dict[str, Any]] = []
        for item in value[-10:]:
            if not isinstance(item, Mapping):
                continue
            role = str(item.get("role", "") or "").strip()
            content = item.get("content", "")
            if not role:
                continue
            turns.append({"role": role, "content": content})
        return turns

    @staticmethod
    def _tool_context(value: object) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        return {
            "available_tools": RightBrainRuntime._string_list(value.get("available_tools")),
            "tool_constraints": RightBrainRuntime._string_list(value.get("tool_constraints")),
        }

    @staticmethod
    def _string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        items: list[str] = []
        for entry in value:
            text = str(entry or "").strip()
            if text and text not in items:
                items.append(text)
        return items

    @staticmethod
    def _content_blocks(value: object) -> list[ContentBlock]:
        if not isinstance(value, list):
            return []
        blocks: list[ContentBlock] = []
        for item in value:
            try:
                if isinstance(item, ContentBlock):
                    blocks.append(item)
                elif isinstance(item, Mapping):
                    blocks.append(ContentBlock.model_validate(dict(item)))
            except Exception:
                continue
        return blocks

    @staticmethod
    def _media_paths(blocks: list[ContentBlock]) -> list[str]:
        media: list[str] = []
        for block in blocks:
            if block.path and block.path not in media:
                media.append(block.path)
        return media

    @staticmethod
    def _task_timeout(context: Mapping[str, Any]) -> float:
        raw_timeout = context.get("timeout_s")
        try:
            timeout = float(raw_timeout)
        except (TypeError, ValueError):
            timeout = RightBrainRuntime.DEFAULT_TASK_TIMEOUT_S
        if timeout <= 0:
            return RightBrainRuntime.DEFAULT_TASK_TIMEOUT_S
        return timeout

    @staticmethod
    def _preferred_delivery_mode(strategy: str) -> str:
        return "inline" if str(strategy or "").strip() == "sync" else "push"

    @staticmethod
    def _estimated_duration(context: Mapping[str, Any]) -> int | None:
        raw_value = context.get("estimated_duration_s")
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    @staticmethod
    def _coerce_progress(payload: Mapping[str, Any]) -> float | None:
        nested = payload.get("payload")
        raw_value = None
        if isinstance(nested, Mapping):
            raw_value = nested.get("progress")
        if raw_value is None:
            raw_value = payload.get("progress")
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return None
        return max(0.0, min(1.0, value))

    @staticmethod
    def _payload_next_step(payload: Mapping[str, Any]) -> str:
        nested = payload.get("payload")
        if isinstance(nested, Mapping):
            value = str(nested.get("next_step", "") or "").strip()
            if value:
                return value
        return str(payload.get("next_step", "") or "").strip()

    @staticmethod
    def _payload_stage(payload: Mapping[str, Any]) -> str:
        stage = str(payload.get("phase", "") or "").strip()
        if stage:
            return "execute"
        return "execute"

    @staticmethod
    def _progress_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        for key in ("event", "producer", "phase", "tool_name"):
            value = str(payload.get(key, "") or "").strip()
            if value:
                metadata[key] = value
        nested = payload.get("payload")
        if isinstance(nested, Mapping):
            metadata["payload"] = dict(nested)
        return metadata

    @staticmethod
    def _trace_kind_for_progress(metadata: Mapping[str, Any]) -> str:
        event = str(metadata.get("event", "") or "").strip()
        if event == "task.trace":
            payload = metadata.get("payload")
            if isinstance(payload, Mapping):
                role = str(payload.get("role", "") or "").strip()
                if role == "tool":
                    return "tool"
                if role == "assistant":
                    return "message"
        if event == "task.tool":
            return "tool"
        return "progress"

    @staticmethod
    def _tool_usage_summary(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        items: list[dict[str, Any]] = []
        for entry in trace:
            if not isinstance(entry, Mapping):
                continue
            data = entry.get("data")
            payload = data if isinstance(data, Mapping) else {}
            tool_name = str(payload.get("tool_name", "") or entry.get("tool_name", "") or "").strip()
            if not tool_name:
                continue
            message = str(entry.get("message", "") or "").strip()
            pair = (tool_name, message)
            if pair in seen:
                continue
            seen.add(pair)
            items.append(
                {
                    "tool_name": tool_name,
                    "message": message,
                    "phase": str(payload.get("phase", "") or "").strip(),
                    "event": str(payload.get("event", "") or "").strip(),
                }
            )
        return items[:10]

    @staticmethod
    def _consume_background_task_result(task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            return


__all__ = ["RightBrainRuntime"]
