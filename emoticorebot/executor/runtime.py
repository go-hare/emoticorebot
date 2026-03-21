"""Executor runtime that owns execution runs and lifecycle control."""

from __future__ import annotations

import asyncio
from typing import Any, Mapping
from uuid import uuid4

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.commands import ExecutorJobRequestPayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import (
    DeliveryTargetPayload,
    ExecutorRejectedPayload,
    ExecutorResultPayload,
)
from emoticorebot.protocol.task_models import ContentBlock, MessageRef, TaskRequestSpec
from emoticorebot.protocol.topics import EventType
from emoticorebot.executor.agent import ExecutorAgent
from emoticorebot.executor.state import ExecutorState
from emoticorebot.executor.store import ExecutorRecord, ExecutorStore

class ExecutorRuntime:
    """Constant resident runtime that starts and controls executor runs."""

    DEFAULT_TASK_TIMEOUT_S = 120.0

    def __init__(
        self,
        *,
        bus: PriorityPubSubBus,
        executor_llm: Any | None = None,
        context_builder: Any | None = None,
        tool_registry: Any | None = None,
        task_store: ExecutorStore | None = None,
        executor: ExecutorAgent | None = None,
    ) -> None:
        self._bus = bus
        self._tasks = task_store or ExecutorStore()
        self._executor = executor
        if self._executor is None and executor_llm is not None and context_builder is not None:
            self._executor = ExecutorAgent(executor_llm=executor_llm, tool_registry=tool_registry, context_builder=context_builder)
        self._active_runs: dict[str, asyncio.Task[None]] = {}

    def register(self) -> None:
        self._bus.subscribe(
            consumer="executor_runtime",
            event_type=EventType.EXECUTOR_COMMAND_JOB_REQUESTED,
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
    def task_store(self) -> ExecutorStore:
        return self._tasks

    async def _dispatch_right_job(self, event: BusEnvelope[ExecutorJobRequestPayload]) -> None:
        payload = event.payload
        if payload.job_action == "cancel":
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
                reason="ExecutorRuntime 当前没有可用的执行器。",
            )
            record.mark_done(
                result="failed",
                summary="执行器不可用",
                error="ExecutorRuntime 当前没有可用的执行器。",
                decision="reject",
            )
            return

        active = self._active_runs.get(record.task_id)
        if active is not None and not active.done():
            await self._publish_rejected(
                event=event,
                task_id=record.task_id,
                job_id=record.job_id,
                reason="当前执行 run 仍在进行中，不能重复启动。",
            )
            return

        run_task = asyncio.create_task(self._run_record(record, event), name=f"executor:{record.task_id}")
        self._active_runs[record.task_id] = run_task
        run_task.add_done_callback(lambda finished, task_id=record.task_id: self._active_runs.pop(task_id, None))
        run_task.add_done_callback(self._consume_background_task_result)

    def _prepare_record(self, event: BusEnvelope[ExecutorJobRequestPayload]) -> ExecutorRecord:
        payload = event.payload
        if payload.job_action == "execute":
            return self._create_record(event)
        raise RuntimeError(f"unsupported executor job_action: {payload.job_action}")

    def _create_record(self, event: BusEnvelope[ExecutorJobRequestPayload]) -> ExecutorRecord:
        payload = event.payload
        request_text = str(payload.request_text or payload.source_text or "").strip()
        if not request_text:
            raise RuntimeError("execute requires request_text")

        context = dict(payload.context or {})
        task_id = str(payload.task_id or "").strip() or f"task_{uuid4().hex[:12]}"
        origin_message = self._origin_message_from_context(context)
        delivery_target = self._resolve_delivery_target(payload=payload, origin_message=origin_message)
        title = (
            str(context.get("title", "") or "").strip()
            or str(payload.goal or "").strip()
            or request_text[:48]
        )
        record = ExecutorRecord(
            task_id=task_id,
            session_id=str(event.session_id or "").strip(),
            turn_id=event.turn_id,
            job_id=payload.job_id,
            request=self._build_request_spec(payload, context),
            title=title,
            origin_message=origin_message,
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
    def _build_request_spec(payload: ExecutorJobRequestPayload, context: Mapping[str, Any]) -> TaskRequestSpec:
        return TaskRequestSpec(
            request=str(payload.request_text or payload.source_text or "").strip(),
            title=str(context.get("title", "") or "").strip() or None,
            goal=str(payload.goal or "").strip() or None,
            mainline=list(payload.mainline or []),
            current_stage=payload.current_stage,
            current_checks=[str(item).strip() for item in list(payload.current_checks or []) if str(item).strip()],
            expected_output=str(context.get("expected_output", "") or "").strip() or None,
            constraints=ExecutorRuntime._string_list(context.get("constraints")),
            success_criteria=ExecutorRuntime._string_list(context.get("success_criteria")),
            history_context=str(context.get("history_context", "") or "").strip() or None,
            content_blocks=ExecutorRuntime._content_blocks(context.get("content_blocks")),
            memory_refs=ExecutorRuntime._string_list(context.get("memory_refs")),
            skill_hints=ExecutorRuntime._string_list(context.get("skill_hints")),
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
        payload: ExecutorJobRequestPayload,
        origin_message: MessageRef | None,
    ) -> DeliveryTargetPayload:
        target = payload.delivery_target
        delivery_mode = str(target.delivery_mode or "").strip()
        if delivery_mode not in {"inline", "push", "stream"}:
            raise RuntimeError(f"invalid executor delivery_mode: {delivery_mode!r}")
        return DeliveryTargetPayload(
            delivery_mode=delivery_mode,  # type: ignore[arg-type]
            channel=str(target.channel or "").strip() or (origin_message.channel if origin_message is not None else None),
            chat_id=str(target.chat_id or "").strip() or (origin_message.chat_id if origin_message is not None else None),
        )

    async def _run_record(self, record: ExecutorRecord, event: BusEnvelope[ExecutorJobRequestPayload]) -> None:
        assert self._executor is not None
        record.summary = str(record.request.request or record.request.goal or record.title or "").strip()
        record.touch()
        record.append_trace(
            kind="status",
            message="执行层开始处理当前 check。",
            data={"decision": "accept"},
            ts=record.updated_at,
        )
        try:
            result = await self._executor.execute(
                self._build_executor_task_spec(record),
                task_id=record.task_id,
            )
            if record.state is ExecutorState.DONE:
                return
            await self._handle_execution_result(record=record, source_event=event, result=result)
        except asyncio.CancelledError:
            if record.state is ExecutorState.DONE:
                return
            raise
        except Exception as exc:
            if record.state is ExecutorState.DONE:
                return
            await self._publish_result(
                event=event,
                record=record,
                decision="accept",
                summary="执行层执行失败",
                result_text=str(exc),
                metadata={"result": "failed", "failure_reason": str(exc)},
            )
            return

    def _build_executor_task_spec(self, record: ExecutorRecord) -> dict[str, Any]:
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
            "mainline": list(record.request.mainline),
            "current_stage": record.request.current_stage,
            "current_checks": list(record.request.current_checks),
            "expected_output": record.request.expected_output,
            "constraints": list(record.request.constraints),
            "success_criteria": list(record.request.success_criteria),
            "history": recent_turns,
            "history_context": "\n\n".join(part for part in context_blocks if part).strip(),
            "memory_refs": list(record.request.memory_refs),
            "skill_hints": list(record.request.skill_hints),
            "task_context": {
                "mainline": list(record.request.mainline),
                "current_stage": record.request.current_stage,
                "current_checks": list(record.request.current_checks),
                "short_term_memory": short_term[:6],
                "long_term_memory": long_term[:6],
                "tool_context": tool_context,
            },
            "media": self._media_paths(record.request.content_blocks),
            "timeout_s": self._task_timeout(record.raw_context),
        }

    async def _handle_execution_result(
        self,
        *,
        record: ExecutorRecord,
        source_event: BusEnvelope[ExecutorJobRequestPayload],
        result: Mapping[str, Any],
    ) -> None:
        status = str(result.get("status", "") or "").strip()
        control_state = str(result.get("control_state", "") or "").strip()
        message = str(result.get("message", "") or "").strip()
        analysis = str(result.get("analysis", "") or "").strip()
        summary = analysis or message or "执行任务已完成。"

        if control_state not in {"completed", "failed"}:
            raise RuntimeError(
                "unsupported executor control_state: "
                f"{control_state!r}; executor must return completed or failed"
            )

        for item in list(result.get("task_trace", []) or []):
            if isinstance(item, Mapping):
                record.trace_log.append(dict(item))

        if control_state == "failed" or status == "failed":
            await self._publish_result(
                event=source_event,
                record=record,
                decision="accept",
                summary=summary or "执行层执行失败。",
                result_text=message or analysis or "执行层执行失败。",
                metadata={"result": "failed", "failure_reason": message or analysis},
            )
            return

        await self._publish_result(
            event=source_event,
            record=record,
            decision="accept",
            summary=summary,
            result_text=message or summary,
            metadata={"result": "success" if status != "partial" else "success", "control_state": control_state, "status": status},
        )

    async def _cancel_job(self, event: BusEnvelope[ExecutorJobRequestPayload]) -> None:
        task_id = str(event.payload.task_id or "").strip()
        if not task_id:
            await self._publish_rejected(
                event=event,
                task_id=None,
                job_id=event.payload.job_id,
                reason="cancel requires task_id",
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
        if record.state is ExecutorState.DONE:
            await self._publish_rejected(
                event=event,
                task_id=task_id,
                job_id=event.payload.job_id,
                reason="该执行任务已经结束，无法再取消。",
            )
            return

        reason = str(event.payload.request_text or event.payload.context.get("reason", "") or "user_cancelled").strip()
        await self._publish_result(
            event=event,
            record=record,
            decision="accept",
            summary="执行任务已取消。",
            result_text=reason,
            metadata={"result": "cancelled", "cancel_reason": reason},
        )

        active = self._active_runs.get(task_id)
        if active is not None and not active.done():
            active.cancel()

    async def _publish_rejected(
        self,
        *,
        event: BusEnvelope[ExecutorJobRequestPayload],
        task_id: str | None,
        job_id: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._bus.publish(
            build_envelope(
                event_type=EventType.EXECUTOR_EVENT_JOB_REJECTED,
                source="executor_runtime",
                target="broadcast",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=task_id,
                correlation_id=task_id or event.correlation_id or event.turn_id or job_id,
                causation_id=event.event_id,
                payload=ExecutorRejectedPayload(
                    job_id=job_id,
                    reason=str(reason or "").strip() or "执行层拒绝执行当前请求。",
                    delivery_target=event.payload.delivery_target,
                    metadata={
                        "job_kind": str(event.payload.job_kind or "execution_review").strip() or "execution_review",
                        **dict(metadata or {}),
                    },
                ),
            )
        )

    async def _publish_result(
        self,
        *,
        event: BusEnvelope[ExecutorJobRequestPayload],
        record: ExecutorRecord,
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
            error = final_text or final_summary or "executor_failed"
        elif outcome == "cancelled":
            result = "cancelled"
            error = final_text or final_summary or "executor_cancelled"
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
            message=final_text or final_summary or "执行任务结束",
            data={"decision": decision, "result": result, **final_metadata},
            ts=record.updated_at,
        )

        await self._bus.publish(
            build_envelope(
                event_type=EventType.EXECUTOR_EVENT_RESULT_READY,
                source="executor_runtime",
                target="broadcast",
                session_id=record.session_id,
                turn_id=record.turn_id,
                task_id=record.task_id,
                correlation_id=record.task_id,
                causation_id=event.event_id,
                payload=ExecutorResultPayload(
                    job_id=record.job_id,
                    decision=decision,  # type: ignore[arg-type]
                    summary=final_summary,
                    result_text=final_text,
                    artifacts=[],
                    delivery_target=record.delivery_target,
                    metadata={
                        "result": result,
                        "job_kind": record.job_kind,
                        **final_metadata,
                    },
                ),
            )
        )

    async def _complete_rejected(
        self,
        *,
        record: ExecutorRecord,
        source_event: BusEnvelope[ExecutorJobRequestPayload],
        reason: str,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        final_reason = str(reason or summary or "").strip() or "执行层拒绝执行当前请求。"
        final_summary = str(summary or reason or "").strip() or final_reason
        final_metadata = dict(metadata or {})
        await self._publish_rejected(
            event=source_event,
            task_id=record.task_id,
            job_id=record.job_id,
            reason=final_reason,
            metadata=final_metadata,
        )
        record.mark_done(
            result="failed",
            summary=final_summary,
            error=final_reason,
            decision="reject",
        )
        record.append_trace(
            kind="warning",
            message=final_reason,
            data={"decision": "reject", **final_metadata},
            ts=record.updated_at,
        )

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
            "available_tools": ExecutorRuntime._string_list(value.get("available_tools")),
            "tool_constraints": ExecutorRuntime._string_list(value.get("tool_constraints")),
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
            timeout = ExecutorRuntime.DEFAULT_TASK_TIMEOUT_S
        if timeout <= 0:
            return ExecutorRuntime.DEFAULT_TASK_TIMEOUT_S
        return timeout

    @staticmethod
    def _consume_background_task_result(task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            return


__all__ = ["ExecutorRuntime"]
