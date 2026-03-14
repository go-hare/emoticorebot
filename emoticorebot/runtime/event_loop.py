"""Task event consumers for session runtimes."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger

from emoticorebot.adapters.outbound_dispatcher import OutboundDispatcher
from emoticorebot.brain.event_narrator import EventNarrator
from emoticorebot.protocol.events import TaskEvent
from emoticorebot.protocol.task_models import TaskSpec, TaskState
from emoticorebot.runtime.event_bus import OutboundMessage
from emoticorebot.runtime.manager import RuntimeManager

if TYPE_CHECKING:
    from emoticorebot.models.emotion_state import EmotionStateManager
    from emoticorebot.session.thread_store import ConversationThread, ThreadStore


class TaskEventLoop:
    """Consumes task events from SessionRuntime instances and forwards them."""

    def __init__(
        self,
        *,
        runtime_manager: RuntimeManager,
        thread_store: "ThreadStore",
        dispatcher: OutboundDispatcher,
        event_narrator: EventNarrator,
        emotion_mgr: "EmotionStateManager",
        memory_window: int,
        new_message_id: Callable[[], str],
        schedule_turn_reflection: Callable[..., None],
        session_lock_for: Callable[[str], asyncio.Lock],
    ):
        self.runtime_manager = runtime_manager
        self.thread_store = thread_store
        self.dispatcher = dispatcher
        self.event_narrator = event_narrator
        self.emotion_mgr = emotion_mgr
        self.memory_window = memory_window
        self._new_message_id = new_message_id
        self._schedule_turn_reflection = schedule_turn_reflection
        self._session_lock_for = session_lock_for
        self._task_consumers: dict[str, asyncio.Task] = {}

    def ensure_consumer(self, session_id: str, runtime=None) -> None:
        key = str(session_id or "__default__").strip() or "__default__"
        existing = self._task_consumers.get(key)
        if existing is not None and not existing.done():
            return

        session_runtime = runtime or self.runtime_manager.get_or_create_runtime(key)
        task = asyncio.create_task(
            self._consume_task_events(session_id=key, runtime=session_runtime),
            name=f"task-consumer:{key}",
        )
        self._task_consumers[key] = task

        def _cleanup(done_task: asyncio.Task, consumer_key: str = key) -> None:
            current = self._task_consumers.get(consumer_key)
            if current is done_task:
                self._task_consumers.pop(consumer_key, None)

        task.add_done_callback(_cleanup)

    async def _consume_task_events(self, *, session_id: str, runtime) -> None:
        while True:
            event = await runtime.to_main_queue.get()
            should_exit = False
            try:
                async with self._session_lock_for(session_id):
                    channel = str(event.get("channel", "") or "").strip()
                    chat_id = str(event.get("chat_id", "") or "").strip()
                    if not channel or not chat_id:
                        thread = self.thread_store.get(session_id)
                        if thread is not None:
                            await self._handle_task_event_internal(thread, event, session_id)
                    else:
                        thread = self.thread_store.get(session_id)
                        history = (
                            thread.get_history(max_messages=self.memory_window, include_task_context=False)
                            if thread is not None
                            else []
                        )
                        pad = {
                            "pleasure": float(self.emotion_mgr.pad.pleasure),
                            "arousal": float(self.emotion_mgr.pad.arousal),
                            "dominance": float(self.emotion_mgr.pad.dominance),
                        }
                        brain_packet = await self.event_narrator.handle_task_event(
                            event=event,
                            history=history,
                            emotion=self.emotion_mgr.get_emotion_label(),
                            pad=pad,
                            task_system=runtime,
                            channel=channel,
                            chat_id=chat_id,
                            session_id=session_id,
                        )
                        if brain_packet:
                            content = str(brain_packet.get("final_message", "") or "").strip()
                            if content:
                                task_id = str(event.get("task_id", "") or "").strip()
                                origin_message_id = str(event.get("message_id", "") or "").strip()
                                await self.dispatcher.publish(
                                    OutboundMessage(
                                        channel=channel,
                                        chat_id=chat_id,
                                        content=content,
                                        reply_to=origin_message_id or None,
                                        metadata={
                                            "task_id": task_id,
                                            "task_event": str(event.get("type", "") or "").strip(),
                                            "producer": "session_runtime",
                                            "message_id": origin_message_id,
                                        },
                                    )
                                )

                                if thread is not None:
                                    await self._persist_and_reflect_routed_event(
                                        thread=thread,
                                        event=event,
                                        session_id=session_id,
                                        content=content,
                                        brain_packet=dict(brain_packet),
                                        channel=channel,
                                        chat_id=chat_id,
                                    )
                    should_exit = runtime.is_idle()
                    if should_exit and self.runtime_manager.get(session_id) is runtime:
                        self.runtime_manager.remove(session_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Task consumer failed for {}: {}", session_id, exc)
            if should_exit:
                break

    async def _persist_and_reflect_routed_event(
        self,
        *,
        thread: "ConversationThread",
        event: TaskEvent,
        session_id: str,
        content: str,
        brain_packet: dict[str, Any],
        channel: str,
        chat_id: str,
    ) -> None:
        assistant_message_id = self._new_message_id()
        assistant_timestamp = datetime.now().isoformat()

        event_type = str(event.get("type", "") or "").strip().lower()
        task_status = "running"
        task_control_state = str(event.get("control_state", "running") or "").strip()
        task_result_status = str(event.get("result_status", "pending") or "pending").strip()

        if event_type == "need_input":
            task_status = "waiting_input"
            task_control_state = "waiting_input"
        elif event_type == "done":
            task_status = "done"
            task_control_state = str(event.get("control_state", "completed") or "completed").strip()
            task_result_status = str(event.get("result_status", "success") or "success").strip()
        elif event_type == "failed":
            task_status = "failed"
            task_control_state = "failed"
            task_result_status = "failed"

        raw_missing = list(event.get("missing", []) or ([event.get("field")] if event.get("field") else []))
        missing_list = [str(item).strip() for item in raw_missing if str(item).strip()]

        try:
            confidence_val = float(event.get("confidence", 0.8))
        except (TypeError, ValueError):
            confidence_val = 0.8 if task_status == "done" else 0.5

        try:
            attempt_count_val = int(event.get("attempt_count", 1))
        except (TypeError, ValueError):
            attempt_count_val = 1

        task_snapshot = self._build_task_snapshot_from_event(
            event=event,
            status=task_status,
            result_status=task_result_status,
            control_state=task_control_state,
            missing=missing_list,
            confidence=confidence_val,
            attempt_count=attempt_count_val,
        )
        thread.add_message(
            "assistant",
            [{"type": "text", "text": content}],
            message_id=assistant_message_id,
            timestamp=assistant_timestamp,
            task=task_snapshot,
            brain=dict(brain_packet),
        )
        self.thread_store.save(thread)

        task_state = {
            "source_type": "task_event",
            "user_input": str(event.get("summary", "") or event.get("question", "") or ""),
            "output": content,
            "assistant_output": content,
            "session_id": session_id,
            "execution_summary": str(brain_packet.get("execution_summary", "") or "").strip(),
            "brain": dict(brain_packet),
            "metadata": {
                "message_id": assistant_message_id,
                "execution": {
                    "summary": str(brain_packet.get("execution_summary", "") or "").strip(),
                    "brain_decision": str(brain_packet.get("final_decision", "answer") or "answer").strip(),
                    "task_action": str(brain_packet.get("task_action", "none") or "none").strip(),
                },
                "channel": channel,
                "chat_id": chat_id,
                "task": dict(task_snapshot),
            },
            "task": task_snapshot,
            "task_trace": list(event.get("task_trace", []) or []),
        }
        self._schedule_turn_reflection(session_key=session_id, state=task_state)

    async def _handle_task_event_internal(
        self,
        _thread: "ConversationThread",
        event: TaskEvent,
        session_id: str,
    ) -> None:
        event_type = str(event.get("type", "") or "").strip().lower()
        task_id = str(event.get("task_id", "") or "").strip()

        task_status = "running"
        task_control_state = str(event.get("control_state", "running") or "running").strip()
        task_result_status = str(event.get("result_status", "pending") or "pending").strip()
        if event_type == "need_input":
            task_status = "waiting_input"
            task_control_state = "waiting_input"
        elif event_type == "done":
            task_status = "done"
            task_control_state = str(event.get("control_state", "completed") or "completed").strip()
            task_result_status = str(event.get("result_status", "success") or "success").strip()
        elif event_type == "failed":
            task_status = "failed"
            task_control_state = "failed"
            task_result_status = "failed"

        raw_missing = list(event.get("missing", []) or ([event.get("field")] if event.get("field") else []))
        missing_list = [str(item).strip() for item in raw_missing if str(item).strip()]

        try:
            confidence_val = float(event.get("confidence", 0.8))
        except (TypeError, ValueError):
            confidence_val = 0.8 if task_status == "done" else 0.5

        try:
            attempt_count_val = int(event.get("attempt_count", 1))
        except (TypeError, ValueError):
            attempt_count_val = 1

        task_snapshot = self._build_task_snapshot_from_event(
            event=event,
            status=task_status,
            result_status=task_result_status,
            control_state=task_control_state,
            missing=missing_list,
            confidence=confidence_val,
            attempt_count=attempt_count_val,
        )

        task_state = {
            "source_type": "internal_task_event",
            "user_input": str(event.get("summary", "") or event.get("question", "") or ""),
            "output": f"[内部任务事件] {task_snapshot.get('summary', '')}",
            "assistant_output": f"[内部任务事件] {task_snapshot.get('summary', '')}",
            "session_id": session_id,
            "execution_summary": self._build_task_execution_summary(event, task_status),
            "metadata": {
                "message_id": f"internal_{task_id}_{event_type}",
                "execution": {
                    "summary": self._build_task_execution_summary(event, task_status),
                    "brain_decision": "internal_task_event",
                },
                "task": dict(task_snapshot),
            },
            "task": task_snapshot,
            "task_trace": task_snapshot.get("task_trace", []),
        }
        self._schedule_turn_reflection(session_key=session_id, state=task_state)

    def _build_task_snapshot_from_event(
        self,
        *,
        event: TaskEvent,
        status: str,
        result_status: str,
        control_state: str,
        missing: list[str],
        confidence: float,
        attempt_count: int,
    ) -> TaskState:
        raw_params = event.get("params")
        params = self._compact_task_spec_for_session(raw_params if isinstance(raw_params, dict) else None)
        input_request = {}
        if status == "waiting_input":
            field = str(event.get("field", "") or "").strip()
            question = str(event.get("question", "") or "").strip()
            if field or question:
                input_request = {"field": field, "question": question}
        event_type = str(event.get("type", "") or "").strip()
        recommended_action = str(event.get("recommended_action", "") or "").strip()
        if not recommended_action:
            recommended_action = self._get_task_recommended_action(event_type, status)
        task_snapshot: TaskState = {
            "invoked": True,
            "task_id": str(event.get("task_id", "") or "").strip(),
            "title": str(event.get("title", "") or params.get("title", "") or "").strip(),
            "status": status,
            "result_status": result_status,
            "control_state": control_state,
            "summary": str(event.get("summary", "") or event.get("message", "") or "").strip(),
            "analysis": str(event.get("analysis", "") or "").strip(),
            "error": str(event.get("reason", "") or "").strip(),
            "missing": missing,
            "stage_info": str(event.get("message", "") or "").strip() if event_type == "progress" else "",
            "pending_review": [item for item in list(event.get("pending_review", []) or []) if isinstance(item, dict)],
            "recommended_action": recommended_action,
            "confidence": confidence,
            "attempt_count": attempt_count,
            "task_trace": [item for item in list(event.get("task_trace", []) or []) if isinstance(item, dict)],
        }
        if params:
            task_snapshot["params"] = params
        if input_request:
            task_snapshot["input_request"] = input_request
        return task_snapshot

    @staticmethod
    def _compact_task_spec_for_session(task_spec: dict[str, Any] | None) -> TaskSpec:
        if not isinstance(task_spec, dict):
            return {}

        compact: TaskSpec = {}
        for key in (
            "task_id",
            "origin_message_id",
            "title",
            "request",
            "goal",
            "expected_output",
            "history_context",
            "channel",
            "chat_id",
            "session_id",
        ):
            value = task_spec.get(key)
            if value not in ("", None, [], {}):
                compact[key] = str(value).strip() if isinstance(value, str) else value

        for key in ("constraints", "success_criteria", "memory_bundle_ids", "skill_hints", "media"):
            values = [str(item).strip() for item in list(task_spec.get(key, []) or []) if str(item).strip()]
            if values:
                compact[key] = values

        task_context = task_spec.get("task_context")
        if isinstance(task_context, dict) and task_context:
            compact["task_context"] = dict(task_context)
        return compact

    @staticmethod
    def _build_task_execution_summary(event: TaskEvent, status: str) -> str:
        event_type = str(event.get("type", "")).strip()
        task_id = str(event.get("task_id", "")).strip()

        if event_type == "done":
            summary = str(event.get("summary", "")).strip()
            return f"任务 {task_id} 已完成：{summary}" if summary else f"任务 {task_id} 已完成"
        if event_type == "failed":
            reason = str(event.get("reason", "")).strip()
            return f"任务 {task_id} 执行失败：{reason}" if reason else f"任务 {task_id} 执行失败"
        if event_type == "need_input":
            summary = str(event.get("summary", "")).strip()
            question = str(event.get("question", "")).strip()
            field = str(event.get("field", "")).strip()
            if summary and question:
                return f"任务 {task_id} 已完成部分结果：{summary}；仍需用户补充：{question}"
            if summary:
                return f"任务 {task_id} 已完成部分结果：{summary}"
            if question:
                return f"任务 {task_id} 需要用户提供信息：{question}"
            if field:
                return f"任务 {task_id} 需要用户提供：{field}"
            return f"任务 {task_id} 需要更多信息"
        if event_type == "progress":
            message = str(event.get("message", "")).strip()
            return f"任务 {task_id} 进展：{message}" if message else f"任务 {task_id} 执行中"
        return f"任务 {task_id} 状态更新"

    @staticmethod
    def _get_task_recommended_action(event_type: str, status: str) -> str:
        if event_type == "need_input":
            return "等待用户提供所需信息"
        if event_type == "failed":
            return "分析失败原因，考虑重试或调整策略"
        if event_type == "done":
            return ""
        if status == "waiting_input":
            return "等待用户补充信息"
        return ""

    def stop(self) -> None:
        for task in list(self._task_consumers.values()):
            task.cancel()

    def release_session(self, session_id: str, *, runtime=None) -> None:
        key = str(session_id or "__default__").strip() or "__default__"
        consumer = self._task_consumers.pop(key, None)
        if consumer is not None:
            consumer.cancel()
        session_runtime = runtime or self.runtime_manager.get(key)
        if session_runtime is not None and session_runtime.is_idle() and self.runtime_manager.get(key) is session_runtime:
            self.runtime_manager.remove(key)


__all__ = ["TaskEventLoop"]
