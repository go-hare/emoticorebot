"""Execution runtime that owns execution runs and lifecycle control."""

from __future__ import annotations

import asyncio
from typing import Any, Mapping
from uuid import uuid4

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.commands import ExecutionTaskRequestPayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import (
    DeliveryTargetPayload,
    ExecutionAcceptedPayload,
    ExecutionProgressPayload,
    ExecutionRejectedPayload,
    ExecutionResultPayload,
    MemoryCandidatePayload,
)
from emoticorebot.protocol.reflection_models import ReflectionSignalPayload
from emoticorebot.protocol.task_models import ContentBlock, MessageRef, TaskRequestSpec
from emoticorebot.protocol.topics import EventType

from .executor import ExecutionExecutor
from .hooks import AuditInterrupt, AuditSignal
from .state import ExecutionState
from .store import ExecutionRecord, ExecutionStore


class ExecutionRuntime:
    DEFAULT_TASK_TIMEOUT_S = 120.0

    def __init__(
        self,
        *,
        bus: PriorityPubSubBus,
        executor_llm: Any | None = None,
        context_builder: Any | None = None,
        tool_registry: Any | None = None,
        task_store: ExecutionStore | None = None,
        executor: ExecutionExecutor | None = None,
    ) -> None:
        self._bus = bus
        self._tasks = task_store or ExecutionStore()
        self._executor = executor
        if self._executor is None and executor_llm is not None and context_builder is not None:
            self._executor = ExecutionExecutor(
                executor_llm=executor_llm,
                tool_registry=tool_registry,
                context_builder=context_builder,
            )
        self._active_runs: dict[str, asyncio.Task[None]] = {}

    @property
    def task_store(self) -> ExecutionStore:
        return self._tasks

    def register(self) -> None:
        self._bus.subscribe(
            consumer="execution_runtime",
            event_type=EventType.EXECUTION_COMMAND_TASK_REQUESTED,
            handler=self._dispatch_task,
        )

    async def stop(self) -> None:
        active = list(self._active_runs.values())
        for task in active:
            if not task.done():
                task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        self._active_runs.clear()

    async def _dispatch_task(self, event: BusEnvelope[ExecutionTaskRequestPayload]) -> None:
        payload = event.payload
        if payload.job_action == "cancel_task":
            await self._cancel_task(event)
            return

        record = self._create_record(event)
        if self._executor is None:
            await self._publish_rejected(event=event, task_id=record.task_id, job_id=record.job_id, reason="execution executor unavailable")
            record.mark_done(result="failed", summary="execution executor unavailable", error="execution executor unavailable", decision="reject")
            return

        active = self._active_runs.get(record.task_id)
        if active is not None and not active.done():
            await self._publish_rejected(event=event, task_id=record.task_id, job_id=record.job_id, reason="execution task already running")
            return

        run_task = asyncio.create_task(self._run_record(record, event), name=f"execution:{record.task_id}")
        self._active_runs[record.task_id] = run_task
        run_task.add_done_callback(lambda finished, task_id=record.task_id: self._active_runs.pop(task_id, None))
        run_task.add_done_callback(self._consume_background_task_result)

    def _create_record(self, event: BusEnvelope[ExecutionTaskRequestPayload]) -> ExecutionRecord:
        payload = event.payload
        request_text = str(payload.request_text or payload.source_text or "").strip()
        if not request_text:
            raise RuntimeError("create_task requires request_text")
        context = dict(payload.context or {})
        task_id = str(payload.task_id or "").strip() or f"task_{uuid4().hex[:12]}"
        origin_message = self._origin_message_from_context(context)
        record = ExecutionRecord(
            task_id=task_id,
            session_id=str(event.session_id or "").strip(),
            turn_id=event.turn_id,
            job_id=payload.job_id,
            request=self._build_request_spec(payload, context),
            title=str(context.get("title", "") or payload.goal or request_text[:48]).strip(),
            origin_message=origin_message,
            delivery_target=self._resolve_delivery_target(payload=payload, origin_message=origin_message),
            job_kind=str(payload.job_kind or "execution_review").strip() or "execution_review",
            source_text=str(payload.source_text or "").strip(),
            raw_context=context,
            metadata=dict(payload.metadata or {}),
            suppress_delivery=bool(context.get("suppress_delivery")),
        )
        self._tasks.add(record)
        return record

    @staticmethod
    def _build_request_spec(payload: ExecutionTaskRequestPayload, context: Mapping[str, Any]) -> TaskRequestSpec:
        return TaskRequestSpec(
            request=str(payload.request_text or payload.source_text or "").strip(),
            title=str(context.get("title", "") or "").strip() or None,
            goal=str(payload.goal or "").strip() or None,
            expected_output=str(context.get("expected_output", "") or "").strip() or None,
            constraints=ExecutionRuntime._string_list(context.get("constraints")),
            success_criteria=ExecutionRuntime._string_list(context.get("success_criteria")),
            history_context=str(context.get("history_context", "") or "").strip() or None,
            content_blocks=ExecutionRuntime._content_blocks(context.get("content_blocks")),
            memory_refs=ExecutionRuntime._string_list(context.get("memory_refs")),
            skill_hints=ExecutionRuntime._string_list(context.get("skill_hints")),
        )

    @staticmethod
    def _origin_message_from_context(context: Mapping[str, Any]) -> MessageRef | None:
        value = context.get("origin_message")
        if not isinstance(value, Mapping):
            return None
        return MessageRef.model_validate(dict(value))

    def _resolve_delivery_target(
        self,
        *,
        payload: ExecutionTaskRequestPayload,
        origin_message: MessageRef | None,
    ) -> DeliveryTargetPayload:
        target = payload.delivery_target
        delivery_mode = str(target.delivery_mode or "").strip()
        if delivery_mode not in {"inline", "push", "stream"}:
            raise RuntimeError(f"invalid execution delivery_mode: {delivery_mode!r}")
        return DeliveryTargetPayload(
            delivery_mode=delivery_mode,  # type: ignore[arg-type]
            channel=str(target.channel or "").strip() or (origin_message.channel if origin_message is not None else None),
            chat_id=str(target.chat_id or "").strip() or (origin_message.chat_id if origin_message is not None else None),
        )

    async def _run_record(self, record: ExecutionRecord, event: BusEnvelope[ExecutionTaskRequestPayload]) -> None:
        assert self._executor is not None

        async def on_update(message: str, payload: dict[str, Any]) -> None:
            await self._publish_progress(record=record, source_event=event, message=message, payload=payload)

        async def on_audit(signal: AuditSignal) -> None:
            await self._handle_audit_signal(record=record, source_event=event, signal=signal)

        self._executor.run_hooks.bind_audit_handler(on_audit)
        try:
            result = await self._executor.execute(
                self._build_executor_task_spec(record),
                task_id=record.task_id,
                progress_reporter=on_update,
                trace_reporter=on_update,
            )
            if record.state is not ExecutionState.DONE:
                await self._handle_execution_result(record=record, source_event=event, result=result)
        except AuditInterrupt:
            return
        finally:
            self._executor.run_hooks.clear()

    def _build_executor_task_spec(self, record: ExecutionRecord) -> dict[str, Any]:
        short_term = self._string_list(record.raw_context.get("short_term_memory"))
        long_term = self._string_list(record.raw_context.get("long_term_memory"))
        tool_context = self._tool_context(record.raw_context.get("tool_context"))
        if not tool_context.get("available_tools") and self._executor is not None and getattr(self._executor, "tools", None) is not None:
            tool_context["available_tools"] = [str(name).strip() for name in list(getattr(self._executor.tools, "tool_names", []) or []) if str(name).strip()]

        memory_context_parts: list[str] = []
        if short_term:
            memory_context_parts.append("短期记忆摘要：\n- " + "\n- ".join(short_term[:6]))
        if long_term:
            memory_context_parts.append("长期记忆摘要：\n- " + "\n- ".join(long_term[:6]))
        if tool_context:
            available = self._string_list(tool_context.get("available_tools"))
            constraints = self._string_list(tool_context.get("tool_constraints"))
            if available:
                memory_context_parts.append("可用工具：\n- " + "\n- ".join(available[:10]))
            if constraints:
                memory_context_parts.append("工具约束：\n- " + "\n- ".join(constraints[:10]))

        history_context = str(record.request.history_context or "").strip()
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
            "history": self._recent_turns(record.raw_context.get("recent_turns")),
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
        record: ExecutionRecord,
        source_event: BusEnvelope[ExecutionTaskRequestPayload],
        signal: AuditSignal,
    ) -> None:
        if signal.decision == "accept":
            await self._ensure_accepted(record=record, source_event=source_event, reason=signal.reason or "execution accepted", stage="execute", metadata=dict(signal.metadata or {}))
            return
        if signal.decision == "reject":
            await self._complete_rejected(record=record, source_event=source_event, reason=signal.reason or signal.summary or "execution rejected", summary=signal.summary or signal.reason or "execution rejected", metadata=dict(signal.metadata or {}))
            return

        await self._publish_result(
            event=source_event,
            record=record,
            decision="answer_only",
            summary=signal.summary or signal.reason or "execution returned answer-only result",
            result_text=signal.result_text or signal.summary or signal.reason,
            metadata=dict(signal.metadata or {}),
        )
        await self._trigger_reflection(record=record, source_event=source_event, reason="execution_answer_only")

    async def _publish_progress(
        self,
        *,
        record: ExecutionRecord,
        source_event: BusEnvelope[ExecutionTaskRequestPayload],
        message: str,
        payload: Mapping[str, Any],
    ) -> None:
        if record.state is ExecutionState.DONE or not record.accepted:
            return
        summary = str(message or "").strip()
        if not summary:
            return
        progress = self._coerce_progress(payload)
        next_step = self._payload_next_step(payload)
        metadata = self._progress_metadata(payload)
        record.summary = summary
        record.last_progress = summary
        record.progress = progress
        record.next_step = next_step
        record.touch()
        record.append_trace(kind=self._progress_trace_kind(metadata), message=summary, data={"stage": "execute", "progress": progress, "next_step": next_step, **metadata}, ts=record.updated_at)
        await self._bus.publish(
            build_envelope(
                event_type=EventType.EXECUTION_EVENT_PROGRESS,
                source="execution_runtime",
                target="broadcast",
                session_id=record.session_id,
                turn_id=record.turn_id,
                task_id=record.task_id,
                correlation_id=record.task_id,
                causation_id=source_event.event_id,
                payload=ExecutionProgressPayload(
                    job_id=record.job_id,
                    stage="execute",
                    summary=summary,
                    progress=progress,
                    next_step=next_step or None,
                    delivery_target=record.delivery_target,
                    metadata={"job_kind": record.job_kind, **metadata},
                ),
            )
        )

    async def _handle_execution_result(
        self,
        *,
        record: ExecutionRecord,
        source_event: BusEnvelope[ExecutionTaskRequestPayload],
        result: Mapping[str, Any],
    ) -> None:
        control_state = str(result.get("control_state", "") or "").strip()
        status = str(result.get("status", "") or "").strip()
        message = str(result.get("message", "") or "").strip()
        analysis = str(result.get("analysis", "") or "").strip()
        summary = analysis or message or "execution completed"
        if control_state not in {"completed", "failed"}:
            raise RuntimeError(f"unsupported execution control_state: {control_state!r}")
        if not record.accepted:
            raise RuntimeError("execution must call audit_tool(decision=\"accept\") before returning a result")
        for item in list(result.get("task_trace", []) or []):
            if isinstance(item, Mapping):
                record.trace_log.append(dict(item))
        if control_state == "failed" or status == "failed":
            await self._publish_result(event=source_event, record=record, decision="accept", summary=summary, result_text=message or analysis or "execution failed", metadata={"result": "failed", "failure_reason": message or analysis})
            await self._trigger_reflection(record=record, source_event=source_event, reason="execution_failed")
            return
        await self._publish_result(event=source_event, record=record, decision="accept", summary=summary, result_text=message or summary, metadata={"result": "success", "control_state": control_state, "status": status})
        await self._trigger_reflection(record=record, source_event=source_event, reason="execution_result")

    async def _cancel_task(self, event: BusEnvelope[ExecutionTaskRequestPayload]) -> None:
        task_id = str(event.payload.task_id or "").strip()
        if not task_id:
            await self._publish_rejected(event=event, task_id=None, job_id=event.payload.job_id, reason="cancel_task requires task_id")
            return
        record = self._tasks.get(task_id)
        if record is None:
            await self._publish_rejected(event=event, task_id=task_id, job_id=event.payload.job_id, reason=f"unknown task_id: {task_id}")
            return
        if record.state is ExecutionState.DONE:
            await self._publish_rejected(event=event, task_id=task_id, job_id=event.payload.job_id, reason="task already completed")
            return
        reason = str(event.payload.request_text or event.payload.context.get("reason", "") or "user_cancelled").strip()
        await self._publish_result(event=event, record=record, decision="accept", summary="execution cancelled", result_text=reason, metadata={"result": "cancelled", "cancel_reason": reason})
        await self._trigger_reflection(record=record, source_event=event, reason="execution_cancelled")
        active = self._active_runs.get(task_id)
        if active is not None and not active.done():
            active.cancel()

    async def _ensure_accepted(
        self,
        *,
        record: ExecutionRecord,
        source_event: BusEnvelope[ExecutionTaskRequestPayload],
        reason: str,
        stage: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if record.accepted or record.state is ExecutionState.DONE:
            return
        record.accepted = True
        record.summary = str(reason or "").strip() or record.summary
        record.last_progress = record.last_progress or record.summary
        record.touch()
        record.append_trace(kind="status", message=reason or "task accepted", data={"decision": "accept", "stage": stage, **dict(metadata or {})}, ts=record.updated_at)
        await self._bus.publish(
            build_envelope(
                event_type=EventType.EXECUTION_EVENT_TASK_ACCEPTED,
                source="execution_runtime",
                target="broadcast",
                session_id=record.session_id,
                turn_id=record.turn_id,
                task_id=record.task_id,
                correlation_id=record.task_id,
                causation_id=source_event.event_id,
                payload=ExecutionAcceptedPayload(
                    job_id=record.job_id,
                    stage=stage,
                    reason=reason or None,
                    estimated_duration_s=self._estimated_duration(record.raw_context),
                    delivery_target=record.delivery_target,
                    metadata={"job_kind": record.job_kind, **dict(metadata or {})},
                ),
            )
        )

    async def _publish_rejected(
        self,
        *,
        event: BusEnvelope[ExecutionTaskRequestPayload],
        task_id: str | None,
        job_id: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._bus.publish(
            build_envelope(
                event_type=EventType.EXECUTION_EVENT_TASK_REJECTED,
                source="execution_runtime",
                target="broadcast",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=task_id,
                correlation_id=task_id or event.correlation_id or event.turn_id or job_id,
                causation_id=event.event_id,
                payload=ExecutionRejectedPayload(
                    job_id=job_id,
                    reason=str(reason or "").strip() or "execution rejected",
                    delivery_target=event.payload.delivery_target,
                    metadata={"job_kind": str(event.payload.job_kind or "execution_review").strip() or "execution_review", **dict(metadata or {})},
                ),
            )
        )

    async def _publish_result(
        self,
        *,
        event: BusEnvelope[ExecutionTaskRequestPayload],
        record: ExecutionRecord,
        decision: str,
        summary: str,
        result_text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        final_metadata = dict(metadata or {})
        outcome = str(final_metadata.get("result", "") or "").strip()
        if outcome == "failed":
            result = "failed"
            error = result_text or summary or "execution_failed"
        elif outcome == "cancelled":
            result = "cancelled"
            error = result_text or summary or "execution_cancelled"
        else:
            result = "success"
            error = ""
        record.mark_done(result=result, summary=summary, error=error or None, decision=decision, result_text=result_text)
        record.append_trace(kind="summary" if result == "success" else ("warning" if result == "cancelled" else "error"), message=result_text or summary or "execution completed", data={"decision": decision, "result": result, **final_metadata}, ts=record.updated_at)
        await self._bus.publish(
            build_envelope(
                event_type=EventType.EXECUTION_EVENT_RESULT_READY,
                source="execution_runtime",
                target="broadcast",
                session_id=record.session_id,
                turn_id=record.turn_id,
                task_id=record.task_id,
                correlation_id=record.task_id,
                causation_id=event.event_id,
                payload=ExecutionResultPayload(
                    job_id=record.job_id,
                    decision=decision,  # type: ignore[arg-type]
                    summary=summary or None,
                    result_text=result_text or None,
                    artifacts=[],
                    delivery_target=record.delivery_target,
                    memory_candidate=MemoryCandidatePayload(kind="execution", summary=summary or result_text or f"{record.task_id} completed"),
                    metadata={"result": result, "job_kind": record.job_kind, **final_metadata},
                ),
            )
        )

    async def _trigger_reflection(
        self,
        *,
        record: ExecutionRecord,
        source_event: BusEnvelope[ExecutionTaskRequestPayload],
        reason: str,
    ) -> None:
        await self._bus.publish(
            build_envelope(
                event_type=EventType.REFLECTION_LIGHT,
                source="execution_runtime",
                target="reflection_governor",
                session_id=record.session_id,
                turn_id=record.turn_id,
                task_id=record.task_id,
                correlation_id=record.task_id,
                causation_id=source_event.event_id,
                payload=ReflectionSignalPayload(
                    trigger_id=f"reflection_{uuid4().hex[:12]}",
                    reason=reason,
                    source_event_id=source_event.event_id,
                    task_id=record.task_id,
                    metadata={
                        "execution_summary": {
                            "session_id": record.session_id,
                            "turn_id": record.turn_id or "",
                            "origin_message": record.origin_message.model_dump(exclude_none=True) if record.origin_message is not None else {},
                            "request_text": record.request.request,
                            "summary": record.summary,
                            "result_text": record.final_result_text,
                            "result": record.result,
                            "decision": record.terminal_decision or "",
                            "error": record.error,
                            "cancel_reason": record.final_result_text if record.result == "cancelled" else "",
                            "source_event_type": EventType.EXECUTION_EVENT_RESULT_READY if record.terminal_decision != "reject" else EventType.EXECUTION_EVENT_TASK_REJECTED,
                            "task_trace": list(record.trace_log[-20:]),
                            "recent_turns": self._recent_turns(record.raw_context.get("recent_turns")),
                            "short_term_memory": self._string_list(record.raw_context.get("short_term_memory"))[:6],
                            "long_term_memory": self._string_list(record.raw_context.get("long_term_memory"))[:6],
                            "memory_refs": list(record.request.memory_refs),
                            "tool_context": self._tool_context(record.raw_context.get("tool_context")),
                        },
                    },
                ),
            )
        )

    async def _complete_rejected(
        self,
        *,
        record: ExecutionRecord,
        source_event: BusEnvelope[ExecutionTaskRequestPayload],
        reason: str,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        final_reason = str(reason or summary or "").strip() or "execution rejected"
        await self._publish_rejected(event=source_event, task_id=record.task_id, job_id=record.job_id, reason=final_reason, metadata=metadata)
        record.mark_done(result="failed", summary=summary or final_reason, error=final_reason, decision="reject")
        record.append_trace(kind="warning", message=final_reason, data={"decision": "reject", **dict(metadata or {})}, ts=record.updated_at)
        await self._trigger_reflection(record=record, source_event=source_event, reason="execution_rejected")

    @staticmethod
    def _recent_turns(value: object) -> list[dict[str, Any]]:
        turns: list[dict[str, Any]] = []
        if not isinstance(value, list):
            return turns
        for item in value[-10:]:
            if not isinstance(item, Mapping):
                continue
            role = str(item.get("role", "") or "").strip()
            if not role:
                continue
            turns.append({"role": role, "content": item.get("content", "")})
        return turns

    @staticmethod
    def _tool_context(value: object) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        return {
            "available_tools": ExecutionRuntime._string_list(value.get("available_tools")),
            "tool_constraints": ExecutionRuntime._string_list(value.get("tool_constraints")),
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
            if isinstance(item, ContentBlock):
                blocks.append(item)
            elif isinstance(item, Mapping):
                blocks.append(ContentBlock.model_validate(dict(item)))
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
            timeout = ExecutionRuntime.DEFAULT_TASK_TIMEOUT_S
        if timeout <= 0:
            raise RuntimeError("task timeout must be positive")
        return timeout

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
        raw_value = payload.get("progress")
        nested = payload.get("payload")
        if raw_value is None and isinstance(nested, Mapping):
            raw_value = nested.get("progress")
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
    def _progress_trace_kind(metadata: Mapping[str, Any]) -> str:
        event = str(metadata.get("event", "") or "").strip()
        if event == "task.tool":
            return "tool"
        return "progress"

    @staticmethod
    def _consume_background_task_result(task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            return


__all__ = ["ExecutionRuntime"]
