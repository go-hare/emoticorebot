"""Event-driven executive brain for the documented v3 architecture."""

from __future__ import annotations

import asyncio
import re
from typing import Any, cast
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.brain.decision_packet import BrainControlPacket, normalize_brain_packet, parse_raw_brain_json
from emoticorebot.protocol.commands import BrainCancelTaskPayload, BrainCreateTaskPayload, BrainResumeTaskPayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import (
    InterruptPayload,
    ReplyBlockedPayload,
    TaskCancelledEventPayload,
    TaskFailedEventPayload,
    TaskNeedInputEventPayload,
    TaskProgressEventPayload,
    TaskResultEventPayload,
    UserMessagePayload,
)
from emoticorebot.protocol.memory_models import ReflectSignalPayload
from emoticorebot.protocol.task_models import MessageRef, ProvidedInputBundle, ProvidedInputItem
from emoticorebot.protocol.topics import EventType, Topic
from emoticorebot.runtime.task_store import TaskStore

from .dialogue_policy import DialoguePolicy
from .reply_builder import ReplyBuilder

_USER_TAG = "####user####"
_TASK_TAG = "####task####"
_STREAM_FLUSH_RE = re.compile(r"[。！？.!?\n]")


def _new_command_id() -> str:
    return f"cmd_{uuid4().hex[:12]}"


def _chunk_text(chunk: Any) -> str:
    content = getattr(chunk, "content", chunk)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content or "")


def _task_tag_overlap(text: str) -> int:
    max_overlap = min(len(text), len(_TASK_TAG) - 1)
    for size in range(max_overlap, 0, -1):
        if _TASK_TAG.startswith(text[-size:]):
            return size
    return 0


def _extract_streamable_user_text(full_text: str) -> tuple[str | None, bool]:
    start = full_text.find(_USER_TAG)
    if start < 0:
        return None, False
    body = full_text[start + len(_USER_TAG) :]
    if body.startswith("\r\n"):
        body = body[2:]
    elif body.startswith("\n"):
        body = body[1:]
    task_pos = body.find(_TASK_TAG)
    if task_pos >= 0:
        return body[:task_pos].rstrip(), True
    overlap = _task_tag_overlap(body)
    if overlap:
        return body[:-overlap], False
    return body, False


class _UserReplyStreamer:
    def __init__(self) -> None:
        self._emitted_chars = 0
        self._pending = ""

    def feed(self, full_text: str) -> list[str]:
        candidate, completed = _extract_streamable_user_text(full_text)
        if candidate is None:
            return []
        fresh = candidate[self._emitted_chars :]
        self._emitted_chars = len(candidate)
        if fresh:
            self._pending += fresh
        if not self._pending:
            return []
        if completed or len(self._pending) >= 24 or _STREAM_FLUSH_RE.search(self._pending):
            chunk = self._pending
            self._pending = ""
            return [chunk]
        return []

    def finish(self) -> list[str]:
        if not self._pending:
            return []
        chunk = self._pending
        self._pending = ""
        return [chunk]


class ExecutiveBrain:
    """Listens to bus events and emits brain commands for runtime execution."""

    def __init__(
        self,
        *,
        bus: PriorityPubSubBus,
        task_store: TaskStore,
        brain_llm: Any | None = None,
        context_builder: Any | None = None,
        dialogue: DialoguePolicy | None = None,
        replies: ReplyBuilder | None = None,
    ) -> None:
        self._bus = bus
        self._tasks = task_store
        self._brain_llm = brain_llm
        self._context_builder = context_builder
        self._dialogue = dialogue or DialoguePolicy()
        self._replies = replies or ReplyBuilder()
        self._session_origins: dict[str, MessageRef] = {}
        self._cancel_replied_task_ids: set[str] = set()
        self._last_progress_replies: dict[str, str] = {}
        self._tasks_in_flight: set[asyncio.Task[None]] = set()
        self._active_user_turns: dict[str, asyncio.Task[None]] = {}

    def register(self) -> None:
        self._bus.subscribe(consumer="brain", topic=Topic.INPUT_EVENT, handler=self._on_input_event)
        self._bus.subscribe(consumer="brain", event_type=EventType.TASK_EVENT_NEED_INPUT, handler=self._on_need_input)
        self._bus.subscribe(consumer="brain", event_type=EventType.TASK_EVENT_PROGRESS, handler=self._on_task_progress)
        self._bus.subscribe(consumer="brain", event_type=EventType.TASK_EVENT_RESULT, handler=self._on_task_result)
        self._bus.subscribe(consumer="brain", event_type=EventType.TASK_EVENT_FAILED, handler=self._on_task_failed)
        self._bus.subscribe(consumer="brain", event_type=EventType.TASK_EVENT_CANCELLED, handler=self._on_task_cancelled)
        self._bus.subscribe(consumer="brain", event_type=EventType.OUTPUT_REPLY_BLOCKED, handler=self._on_reply_blocked)
        self._bus.subscribe(consumer="brain", event_type=EventType.SAFETY_BLOCKED, handler=self._ignore)
        self._bus.subscribe(consumer="brain", event_type=EventType.MEMORY_UPDATE_PERSONA, handler=self._ignore)
        self._bus.subscribe(consumer="brain", event_type=EventType.MEMORY_UPDATE_USER_MODEL, handler=self._ignore)

    async def _on_input_event(self, event: BusEnvelope[object]) -> None:
        if event.event_type == EventType.INPUT_INTERRUPT:
            self._cancel_active_user_turn(event.session_id or "")
            await self._on_interrupt(cast(BusEnvelope[InterruptPayload], event))
            return
        if event.event_type != EventType.INPUT_USER_MESSAGE:
            return
        session_id = event.session_id or ""
        self._cancel_active_user_turn(session_id)
        self._spawn_user_turn(session_id, cast(BusEnvelope[UserMessagePayload], event))

    def _spawn_user_turn(self, session_id: str, event: BusEnvelope[UserMessagePayload]) -> None:
        task = asyncio.create_task(self._run_user_turn(event), name=f"brain-user-turn:{session_id or 'default'}")
        self._tasks_in_flight.add(task)
        self._active_user_turns[session_id] = task
        task.add_done_callback(self._tasks_in_flight.discard)
        task.add_done_callback(lambda finished, key=session_id: self._discard_user_turn(key, finished))

    def _discard_user_turn(self, session_id: str, task: asyncio.Task[None]) -> None:
        if self._active_user_turns.get(session_id) is task:
            self._active_user_turns.pop(session_id, None)
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            return

    def _cancel_active_user_turn(self, session_id: str) -> None:
        task = self._active_user_turns.get(session_id)
        if task is not None and not task.done():
            task.cancel()

    async def _run_user_turn(self, event: BusEnvelope[UserMessagePayload]) -> None:
        payload = event.payload
        user_text = self._user_text(payload)
        session_id = event.session_id or ""
        self._session_origins[session_id] = payload.message
        tasks = self._tasks.for_session(session_id)
        origin = payload.message
        suppress_delivery = self._suppress_delivery_from_metadata(payload.metadata)
        stream_id = f"stream_{event.turn_id or uuid4().hex[:12]}"
        stream_counter = 0
        stream_enabled = self._can_stream_user_reply(origin)

        async def _publish_stream_chunk(text: str) -> None:
            nonlocal stream_counter
            chunk = str(text or "")
            if not chunk:
                return
            stream_counter += 1
            await self._bus.publish(
                self._replies.reply(
                    session_id=session_id,
                    turn_id=event.turn_id,
                    text=chunk,
                    origin_message=origin,
                    causation_id=event.event_id,
                    correlation_id=event.turn_id,
                    kind="answer",
                    reply_id=f"{stream_id}_{stream_counter}",
                    reply_metadata=self._reply_metadata(
                        extra={
                            "stream_id": stream_id,
                            "stream_state": "delta",
                            "stream_index": stream_counter,
                        },
                        suppress_delivery=suppress_delivery,
                    ),
                )
            )

        packet = await self._decide_user_turn(
            event=event,
            user_text=user_text,
            tasks=tasks,
            on_user_stream=_publish_stream_chunk if stream_enabled else None,
        )
        task_action = str(packet.get("task_action", "none") or "none").strip()
        reply_text = str(packet.get("final_message", "") or "").strip()
        if not reply_text:
            reply_text = self._default_user_turn_reply(
                task_action=task_action,
                final_decision=str(packet.get("final_decision", "") or "").strip(),
            )
        reflection_task = None
        final_reply_metadata = (
            self._reply_metadata(
                extra={
                    "stream_id": stream_id,
                    "stream_state": "final",
                },
                suppress_delivery=suppress_delivery,
            )
            if stream_enabled and stream_counter > 0
            else self._reply_metadata(suppress_delivery=suppress_delivery)
        )

        if task_action == "create_task":
            task = packet.get("task", {}) if isinstance(packet.get("task"), dict) else {}
            await self._bus.publish(
                build_envelope(
                    event_type=EventType.BRAIN_CREATE_TASK,
                    source="brain",
                    target="runtime",
                    session_id=session_id,
                    turn_id=event.turn_id,
                    correlation_id=event.turn_id,
                    causation_id=event.event_id,
                    payload=BrainCreateTaskPayload(
                        command_id=_new_command_id(),
                        title=str(task.get("title", "") or "").strip() or None,
                        request=str(task.get("request", "") or "").strip() or user_text,
                        goal=str(task.get("goal", "") or "").strip() or None,
                        expected_output=str(task.get("expected_output", "") or "").strip() or None,
                        constraints=list(task.get("constraints", []) or []),
                        success_criteria=list(task.get("success_criteria", []) or []),
                        history_context=str(task.get("history_context", "") or "").strip()
                        or str(payload.metadata.get("history_context", "") or "").strip()
                        or None,
                        memory_refs=list(task.get("memory_refs", []) or []),
                        skill_hints=list(task.get("skill_hints", []) or []),
                        content_blocks=list(payload.content_blocks),
                        review_policy=str(task.get("review_policy", "") or "").strip() or self._review_policy(payload),
                        preferred_agent=str(task.get("preferred_agent", "") or "").strip() or self._preferred_agent(payload),
                        origin_message=origin,
                        metadata=self._command_metadata(suppress_delivery=suppress_delivery),
                    ),
                )
            )
            await self._publish_user_turn_reply(
                session_id=session_id,
                turn_id=event.turn_id,
                origin_message=origin,
                text=reply_text,
                causation_id=event.event_id,
                correlation_id=event.turn_id,
                kind="status",
                reply_metadata=final_reply_metadata,
            )
        elif task_action == "resume_task":
            task_id = str((packet.get("task") or {}).get("task_id", "") or "").strip()
            reflection_task = self._tasks.get(task_id)
            suppress_task_reply = suppress_delivery or self._task_suppress_delivery(reflection_task)
            await self._bus.publish(
                build_envelope(
                    event_type=EventType.BRAIN_RESUME_TASK,
                    source="brain",
                    target="runtime",
                    session_id=session_id,
                    turn_id=event.turn_id,
                    task_id=task_id,
                    correlation_id=task_id or event.turn_id,
                    causation_id=event.event_id,
                    payload=BrainResumeTaskPayload(
                        command_id=_new_command_id(),
                        task_id=task_id,
                        user_input=user_text,
                        provided_inputs=ProvidedInputBundle(
                            plain_text=user_text,
                            items=[
                                ProvidedInputItem(
                                    field="user_input",
                                    value_text=user_text,
                                    source="user_message",
                                )
                            ],
                            source_message=origin,
                            source_event_id=event.event_id,
                        ),
                        origin_message=origin,
                        resume_reason=str(packet.get("task_reason", "") or "").strip() or "user_follow_up",
                        metadata=self._command_metadata(suppress_delivery=suppress_task_reply),
                    ),
                )
            )
            await self._publish_user_turn_reply(
                session_id=session_id,
                turn_id=event.turn_id,
                origin_message=origin,
                text=reply_text,
                related_task_id=task_id,
                causation_id=event.event_id,
                correlation_id=task_id or event.turn_id,
                kind="status",
                reply_metadata=self._reply_metadata(
                    extra=final_reply_metadata,
                    suppress_delivery=suppress_task_reply,
                ),
            )
        elif task_action == "cancel_task":
            task_id = str((packet.get("task") or {}).get("task_id", "") or "").strip()
            task = self._tasks.get(task_id)
            reflection_task = task
            suppress_task_reply = suppress_delivery or self._task_suppress_delivery(task)
            await self._bus.publish(
                build_envelope(
                    event_type=EventType.BRAIN_CANCEL_TASK,
                    source="brain",
                    target="runtime",
                    session_id=session_id,
                    turn_id=event.turn_id,
                    task_id=task_id,
                    correlation_id=task_id or event.turn_id,
                    causation_id=event.event_id,
                    payload=BrainCancelTaskPayload(
                        command_id=_new_command_id(),
                        task_id=task_id,
                        reason=str(packet.get("task_reason", "") or "").strip() or "user_cancelled",
                        user_visible_reason=reply_text,
                        hard_stop=False,
                        origin_message=origin,
                        metadata=self._command_metadata(suppress_delivery=suppress_task_reply),
                    ),
                )
            )
            self._cancel_replied_task_ids.add(task_id)
            await self._publish_user_turn_reply(
                session_id=session_id,
                turn_id=event.turn_id,
                origin_message=origin,
                text=reply_text,
                related_task_id=task_id,
                causation_id=event.event_id,
                correlation_id=task_id or event.turn_id,
                kind="status",
                reply_metadata=self._reply_metadata(
                    extra=final_reply_metadata,
                    suppress_delivery=suppress_task_reply,
                ),
            )
        else:
            await self._publish_user_turn_reply(
                session_id=session_id,
                turn_id=event.turn_id,
                origin_message=origin,
                text=reply_text,
                causation_id=event.event_id,
                correlation_id=event.turn_id,
                kind="ask_user" if packet.get("final_decision") == "ask_user" else "answer",
                reply_metadata=final_reply_metadata,
            )

        await self._publish_reflection(
            event,
            reason="user_turn",
            metadata=self._user_turn_reflection_metadata(
                event=event,
                user_text=user_text,
                reply_text=reply_text,
                brain_packet=packet,
                task=reflection_task,
                execution=self._execution_from_packet(packet, task=reflection_task),
            ),
        )

    async def _on_interrupt(self, event: BusEnvelope[InterruptPayload]) -> None:
        task_id = str(event.payload.target_task_id or "").strip()
        if not task_id:
            task = self._tasks.latest_for_session(event.session_id or "", include_terminal=False)
            if task is None:
                return
            task_id = task.task_id
        task = self._tasks.get(task_id)
        if task is None:
            return
        self._cancel_replied_task_ids.add(task_id)
        self._last_progress_replies.pop(task_id, None)
        await self._bus.publish(
            build_envelope(
                event_type=EventType.BRAIN_CANCEL_TASK,
                source="brain",
                target="runtime",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=task_id,
                correlation_id=task_id,
                causation_id=event.event_id,
                payload=BrainCancelTaskPayload(
                    command_id=_new_command_id(),
                    task_id=task_id,
                    reason="interrupted_by_new_user_message",
                    hard_stop=True,
                    origin_message=event.payload.message,
                    metadata=self._merge_reply_metadata(
                        {"interrupt_type": event.payload.interrupt_type},
                        self._command_metadata(
                            suppress_delivery=(
                                self._suppress_delivery_from_metadata(event.payload.metadata)
                                or self._task_suppress_delivery(task)
                            ),
                        ),
                    )
                    or {},
                ),
            )
        )

    async def _on_need_input(self, event: BusEnvelope[TaskNeedInputEventPayload]) -> None:
        task = self._tasks.get(event.payload.task_id)
        await self._bus.publish(
            self._replies.ask_user(
                session_id=event.session_id or "",
                turn_id=event.turn_id,
                text=self._dialogue.need_input(task, event.payload),
                origin_message=self._origin_for(event.session_id or "", task),
                related_task_id=event.payload.task_id,
                causation_id=event.event_id,
                correlation_id=event.correlation_id or event.payload.task_id,
                reply_metadata=self._reply_metadata(task=task),
            )
        )

    async def _on_task_progress(self, event: BusEnvelope[TaskProgressEventPayload]) -> None:
        tool_name = str(event.payload.metadata.get("tool_name", "") or "").strip()
        summary = str(event.payload.summary or "").strip()
        if not summary or tool_name not in {"write_file", "edit_file", "insert_lines", "replace_lines", "delete_lines", "exec"}:
            return
        if self._last_progress_replies.get(event.payload.task_id) == summary:
            return
        self._last_progress_replies[event.payload.task_id] = summary
        task = self._tasks.get(event.payload.task_id)
        await self._bus.publish(
            self._replies.reply(
                session_id=event.session_id or "",
                turn_id=event.turn_id,
                text=self._dialogue.task_progress(task, event.payload),
                origin_message=self._origin_for(event.session_id or "", task),
                related_task_id=event.payload.task_id,
                causation_id=event.event_id,
                correlation_id=event.correlation_id or event.payload.task_id,
                kind="status",
                reply_metadata=self._reply_metadata(task=task),
            )
        )

    async def _on_task_result(self, event: BusEnvelope[TaskResultEventPayload]) -> None:
        self._last_progress_replies.pop(event.payload.task_id, None)
        task = self._tasks.get(event.payload.task_id)
        reply_text = self._dialogue.task_result(task, event.payload)
        await self._bus.publish(
            self._replies.reply(
                session_id=event.session_id or "",
                turn_id=event.turn_id,
                text=reply_text,
                origin_message=self._origin_for(event.session_id or "", task),
                related_task_id=event.payload.task_id,
                causation_id=event.event_id,
                correlation_id=event.correlation_id or event.payload.task_id,
                reply_metadata=self._reply_metadata(task=task),
            )
        )
        metadata = self._task_event_reflection_metadata(
            event=event,
            task=task,
            reply_text=reply_text,
            execution=self._execution_payload(
                status="done",
                summary=event.payload.summary or reply_text,
                confidence=event.payload.confidence,
            ),
        )
        await self._publish_reflection(event, reason="task_result", metadata=metadata)
        await self._publish_reflection(
            event,
            reason="task_result",
            event_type=EventType.MEMORY_REFLECT_DEEP,
            metadata=metadata,
        )

    async def _on_task_failed(self, event: BusEnvelope[TaskFailedEventPayload]) -> None:
        self._last_progress_replies.pop(event.payload.task_id, None)
        task = self._tasks.get(event.payload.task_id)
        reply_text = self._dialogue.task_failed(task, event.payload)
        await self._bus.publish(
            self._replies.reply(
                session_id=event.session_id or "",
                turn_id=event.turn_id,
                text=reply_text,
                origin_message=self._origin_for(event.session_id or "", task),
                related_task_id=event.payload.task_id,
                causation_id=event.event_id,
                correlation_id=event.correlation_id or event.payload.task_id,
                reply_metadata=self._reply_metadata(task=task),
            )
        )
        metadata = self._task_event_reflection_metadata(
            event=event,
            task=task,
            reply_text=reply_text,
            execution=self._execution_payload(
                status="failed",
                summary=event.payload.summary or reply_text,
                failure_reason=event.payload.reason,
            ),
        )
        await self._publish_reflection(event, reason="task_failed", metadata=metadata)
        await self._publish_reflection(
            event,
            reason="task_failed",
            event_type=EventType.MEMORY_REFLECT_DEEP,
            metadata=metadata,
        )

    async def _on_task_cancelled(self, event: BusEnvelope[TaskCancelledEventPayload]) -> None:
        self._last_progress_replies.pop(event.payload.task_id, None)
        if event.payload.task_id in self._cancel_replied_task_ids:
            self._cancel_replied_task_ids.discard(event.payload.task_id)
            return
        task = self._tasks.get(event.payload.task_id)
        reply_text = self._dialogue.cancelled_event(task, event.payload)
        await self._bus.publish(
            self._replies.reply(
                session_id=event.session_id or "",
                turn_id=event.turn_id,
                text=reply_text,
                origin_message=self._origin_for(event.session_id or "", task),
                related_task_id=event.payload.task_id,
                causation_id=event.event_id,
                correlation_id=event.correlation_id or event.payload.task_id,
                kind="status",
                reply_metadata=self._reply_metadata(task=task),
            )
        )
        metadata = self._task_event_reflection_metadata(
            event=event,
            task=task,
            reply_text=reply_text,
            execution=self._execution_payload(
                status="failed",
                summary=reply_text,
                failure_reason=event.payload.reason or "task_cancelled",
            ),
        )
        await self._publish_reflection(event, reason="task_cancelled", metadata=metadata)
        await self._publish_reflection(
            event,
            reason="task_cancelled",
            event_type=EventType.MEMORY_REFLECT_DEEP,
            metadata=metadata,
        )

    async def _on_reply_blocked(self, event: BusEnvelope[ReplyBlockedPayload]) -> None:
        task = self._tasks.get(event.task_id or "")
        await self._bus.publish(
            self._replies.reply(
                session_id=event.session_id or "",
                turn_id=event.turn_id,
                text=self._dialogue.safe_fallback(event.payload),
                origin_message=self._origin_for(event.session_id or "", task),
                related_task_id=event.task_id,
                causation_id=event.event_id,
                correlation_id=event.correlation_id or event.task_id or event.turn_id,
                kind="safety_fallback",
                safe_fallback=True,
                reply_metadata=self._reply_metadata(task=task),
            )
        )

    async def _publish_reflection(
        self,
        event: BusEnvelope[object],
        *,
        reason: str,
        event_type: str = EventType.MEMORY_REFLECT_TURN,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._bus.publish(
            build_envelope(
                event_type=event_type,
                source="brain",
                target="memory_governor",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=event.task_id,
                correlation_id=event.correlation_id or event.task_id or event.turn_id,
                causation_id=event.event_id,
                payload=ReflectSignalPayload(
                    trigger_id=f"reflect_{uuid4().hex[:12]}",
                    reason=reason,
                    source_event_id=event.event_id,
                    task_id=event.task_id,
                    metadata=metadata or {},
                ),
            )
        )

    @staticmethod
    async def _ignore(_event: BusEnvelope[object]) -> None:
        return None

    @staticmethod
    def _user_text(payload: UserMessagePayload) -> str:
        if payload.plain_text:
            return payload.plain_text
        parts = [block.text for block in payload.content_blocks if block.type == "text" and block.text]
        return "\n".join(parts).strip()

    @staticmethod
    def _review_policy(payload: UserMessagePayload) -> str | None:
        value = str(payload.metadata.get("review_policy", "") or "").strip()
        return value if value in {"skip", "optional", "required"} else None

    @staticmethod
    def _preferred_agent(payload: UserMessagePayload) -> str | None:
        value = str(payload.metadata.get("preferred_agent", "") or "").strip()
        return value if value in {"planner", "worker"} else None

    def _origin_for(self, session_id: str, task: object) -> MessageRef | None:
        if task is not None and getattr(task, "origin_message", None) is not None:
            return getattr(task, "origin_message")
        return self._session_origins.get(session_id)

    @staticmethod
    def _suppress_delivery_from_metadata(metadata: dict[str, Any] | None) -> bool:
        return bool((metadata or {}).get("suppress_delivery"))

    @staticmethod
    def _task_suppress_delivery(task: object | None) -> bool:
        return bool(getattr(task, "suppress_delivery", False)) if task is not None else False

    @staticmethod
    def _command_metadata(*, suppress_delivery: bool) -> dict[str, Any]:
        return {"suppress_delivery": True} if suppress_delivery else {}

    @staticmethod
    def _merge_reply_metadata(*parts: dict[str, Any] | None) -> dict[str, Any] | None:
        merged: dict[str, Any] = {}
        for part in parts:
            if part:
                merged.update(part)
        return merged or None

    def _reply_metadata(
        self,
        *,
        extra: dict[str, Any] | None = None,
        suppress_delivery: bool = False,
        task: object | None = None,
    ) -> dict[str, Any] | None:
        return self._merge_reply_metadata(
            extra,
            self._command_metadata(
                suppress_delivery=suppress_delivery or self._task_suppress_delivery(task),
            ),
        )

    async def _decide_user_turn(
        self,
        *,
        event: BusEnvelope[UserMessagePayload],
        user_text: str,
        tasks: list[object],
        on_user_stream: Any | None = None,
    ) -> BrainControlPacket:
        if self._brain_llm is None:
            raise RuntimeError("ExecutiveBrain requires brain_llm for user-turn decisions")

        messages = self._build_brain_decision_messages(
            user_text=user_text,
            history_context=str(event.payload.metadata.get("history_context", "") or "").strip(),
            tasks=tasks,
        )
        response = await self._invoke_brain(messages, on_user_stream=on_user_stream)
        raw_payload = parse_raw_brain_json(response)
        return normalize_brain_packet(
            raw_payload,
            current_context={
                "user_input": user_text,
                "history_context": str(event.payload.metadata.get("history_context", "") or "").strip(),
                "review_policy": self._review_policy(event.payload) or "",
                "preferred_agent": self._preferred_agent(event.payload) or "",
                "waiting_task_id": self._latest_waiting_task_id(tasks),
                "active_task_id": self._latest_active_task_id(tasks),
                "latest_task_id": self._latest_task_id(tasks),
            },
        )

    async def _invoke_brain(
        self,
        messages: list[SystemMessage | HumanMessage],
        *,
        on_user_stream: Any | None = None,
    ) -> Any:
        if on_user_stream is not None and hasattr(self._brain_llm, "astream"):
            full_text = ""
            streamer = _UserReplyStreamer()
            async for chunk in self._brain_llm.astream(messages):
                text = _chunk_text(chunk)
                if not text:
                    continue
                full_text += text
                for item in streamer.feed(full_text):
                    await on_user_stream(item)
            for item in streamer.finish():
                await on_user_stream(item)
            return full_text
        if hasattr(self._brain_llm, "ainvoke"):
            return await self._brain_llm.ainvoke(messages)
        if hasattr(self._brain_llm, "invoke"):
            return self._brain_llm.invoke(messages)
        raise RuntimeError("brain_llm does not support invoke/ainvoke")

    @staticmethod
    def _can_stream_user_reply(origin: MessageRef) -> bool:
        return bool(str(origin.channel or "").strip() and str(origin.chat_id or "").strip())

    def _build_brain_decision_messages(
        self,
        *,
        user_text: str,
        history_context: str,
        tasks: list[object],
    ) -> list[SystemMessage | HumanMessage]:
        system_prompt = ""
        if self._context_builder is not None and hasattr(self._context_builder, "build_brain_decision_system_prompt"):
            system_prompt = self._context_builder.build_brain_decision_system_prompt(query=user_text)
        elif self._context_builder is not None and hasattr(self._context_builder, "build_brain_system_prompt"):
            system_prompt = self._context_builder.build_brain_system_prompt(query=user_text)
        messages: list[SystemMessage | HumanMessage] = []
        if system_prompt.strip():
            messages.append(SystemMessage(content=system_prompt.strip()))
        messages.append(HumanMessage(content=self._user_turn_instruction(history_context, tasks, user_text)))
        return messages

    def _user_turn_instruction(self, history_context: str, tasks: list[object], user_text: str) -> str:
        lines = [
            "## 当前轮执行要求",
            "你现在要对这条用户输入做一次完整决策，并且只能输出两个文本区块：`####user####` 和 `####task####`。",
            "不要输出 markdown，不要输出解释，不要输出额外文本。",
            "格式固定如下：",
            "####user####",
            "<给用户看的自然语言回复>",
            "",
            "####task####",
            "mode=<answer|ask_user|continue>",
            "action=<none|create_task|resume_task|cancel_task>",
            "task_id=<仅 resume_task / cancel_task 时填写>",
            "",
            "`####user####` 里的内容会直接发给用户，必须自然、简洁。",
            "`####task####` 只给系统看，必须是紧凑 key=value 行。",
            "`create_task` 默认不要写 request；runtime 会直接用用户原始请求创建任务。",
            "如果只是简单问答、闲聊、解释、计算，输出 `mode=answer` 和 `action=none`。",
            "如果需要追问但不创建任务，输出 `mode=ask_user` 和 `action=none`。",
            "如果确实需要进入任务执行，再输出 `mode=continue`，并把 `action` 设为 `create_task / resume_task / cancel_task`。",
            "凡是用户要求创建文件、修改文件、运行命令、检查环境、调用工具、生成产物，必须输出 `action=create_task`。",
            "不要假装任务已经完成；只要还没有经过 runtime/worker 执行，就不能在 `####user####` 里声称文件已创建、命令已运行或结果已落盘。",
        ]
        task_lines = self._task_context_lines(tasks)
        if history_context:
            lines.extend(["", "## 最近对话摘要", history_context])
        if task_lines:
            lines.extend(["", "## 当前 session 任务上下文", *task_lines])
        lines.extend(["", "## 用户消息", user_text])
        return "\n".join(lines).strip()

    @staticmethod
    def _task_context_lines(tasks: list[object]) -> list[str]:
        lines: list[str] = []
        for task in tasks[-5:]:
            title = str(getattr(task, "title", "") or getattr(task, "task_id", "")).strip()
            task_id = str(getattr(task, "task_id", "") or "").strip()
            status = str(getattr(getattr(task, "status", None), "value", "") or "").strip()
            summary = str(getattr(task, "summary", "") or getattr(task, "last_progress", "") or "").strip()
            request = ""
            if getattr(task, "request", None) is not None:
                request = str(getattr(task, "request").request or "").strip()
            item = {
                "task_id": task_id,
                "title": title,
                "status": status,
                "summary": summary,
                "request": request,
            }
            lines.append(f"- {item}")
        return lines

    @staticmethod
    def _default_user_turn_reply(*, task_action: str, final_decision: str) -> str:
        if task_action == "create_task":
            return "收到，我开始处理。"
        if task_action == "resume_task":
            return "收到，我继续处理。"
        if task_action == "cancel_task":
            return "收到，我先停下这个任务。"
        if final_decision == "ask_user":
            return "你再补充一点信息，我就继续。"
        return "收到。"

    async def _publish_user_turn_reply(
        self,
        *,
        session_id: str,
        turn_id: str | None,
        origin_message: MessageRef | None,
        text: str,
        related_task_id: str | None = None,
        causation_id: str | None = None,
        correlation_id: str | None = None,
        kind: str = "answer",
        reply_metadata: dict[str, Any] | None = None,
    ) -> None:
        if not text:
            return
        builder = self._replies.ask_user if kind == "ask_user" else self._replies.reply
        await self._bus.publish(
            builder(
                session_id=session_id,
                turn_id=turn_id,
                text=text,
                origin_message=origin_message,
                related_task_id=related_task_id,
                causation_id=causation_id,
                correlation_id=correlation_id,
                kind=("status" if kind == "status" else "answer") if kind != "ask_user" else "ask_user",
                reply_metadata=reply_metadata,
            )
        )

    async def stop(self) -> None:
        for task in list(self._tasks_in_flight):
            task.cancel()
        if self._tasks_in_flight:
            await asyncio.gather(*self._tasks_in_flight, return_exceptions=True)

    def _user_turn_reflection_metadata(
        self,
        *,
        event: BusEnvelope[UserMessagePayload],
        user_text: str,
        reply_text: str,
        brain_packet: BrainControlPacket,
        task: object,
        execution: dict[str, Any] | None,
    ) -> dict[str, Any]:
        origin = event.payload.message
        return {
            "reflection_input": {
                "session_id": event.session_id or "",
                "turn_id": event.turn_id or "",
                "message_id": origin.message_id or "",
                "source_type": "user_turn",
                "user_input": user_text,
                "output": reply_text,
                "assistant_output": reply_text,
                "channel": origin.channel or "",
                "chat_id": origin.chat_id or "",
                "brain": dict(brain_packet),
                "task": self._task_snapshot(task),
                "execution": execution or {},
                "metadata": {"source_event_type": event.event_type},
            }
        }

    def _task_event_reflection_metadata(
        self,
        *,
        event: BusEnvelope[TaskResultEventPayload | TaskFailedEventPayload | TaskCancelledEventPayload],
        task: object,
        reply_text: str,
        execution: dict[str, Any],
    ) -> dict[str, Any]:
        origin = self._origin_for(event.session_id or "", task) or MessageRef()
        user_input = ""
        if task is not None and getattr(task, "request", None) is not None:
            user_input = str(getattr(task, "request").request or "").strip()
        return {
            "reflection_input": {
                "session_id": event.session_id or "",
                "turn_id": event.turn_id or "",
                "message_id": origin.message_id or "",
                "source_type": "task_event",
                "user_input": user_input,
                "output": reply_text,
                "assistant_output": reply_text,
                "channel": origin.channel or "",
                "chat_id": origin.chat_id or "",
                "task": event.payload.state.model_dump(),
                "execution": execution,
                "metadata": {"source_event_type": event.event_type, "related_task_id": event.task_id or ""},
            }
        }

    @staticmethod
    def _execution_payload(
        *,
        status: str,
        summary: str,
        invoked: bool = True,
        confidence: float | None = None,
        failure_reason: str | None = None,
    ) -> dict[str, Any]:
        return {
            "invoked": invoked,
            "status": status,
            "summary": summary,
            "confidence": confidence or 0.0,
            "attempt_count": 1 if invoked else 0,
            "missing": [],
            "failure_reason": failure_reason or "",
            "recommended_action": "",
        }

    @staticmethod
    def _task_snapshot(task: object) -> dict[str, Any]:
        if task is None or not hasattr(task, "snapshot"):
            return {}
        return getattr(task, "snapshot")().model_dump()

    @staticmethod
    def _latest_waiting_task_id(tasks: list[object]) -> str:
        for task in reversed(tasks):
            status = str(getattr(getattr(task, "status", None), "value", "") or "")
            if status == "waiting_input":
                return str(getattr(task, "task_id", "") or "").strip()
        return ""

    @staticmethod
    def _latest_active_task_id(tasks: list[object]) -> str:
        for task in reversed(tasks):
            status = str(getattr(getattr(task, "status", None), "value", "") or "")
            if status not in {"done", "failed", "cancelled", "archived"}:
                return str(getattr(task, "task_id", "") or "").strip()
        return ""

    @staticmethod
    def _latest_task_id(tasks: list[object]) -> str:
        if not tasks:
            return ""
        return str(getattr(tasks[-1], "task_id", "") or "").strip()

    @staticmethod
    def _execution_from_packet(packet: BrainControlPacket, *, task: object) -> dict[str, Any]:
        action = str(packet.get("task_action", "none") or "none").strip()
        summary = "主脑完成了当前轮判断。"
        if action == "create_task":
            return ExecutiveBrain._execution_payload(status="running", summary=summary, invoked=True)
        if action == "resume_task":
            return ExecutiveBrain._execution_payload(status="running", summary=summary, invoked=True)
        if action == "cancel_task":
            return ExecutiveBrain._execution_payload(
                status="failed",
                summary=summary,
                invoked=bool(task is not None),
                failure_reason=str(packet.get("task_reason", "") or "").strip() or "user_cancelled",
            )
        return ExecutiveBrain._execution_payload(status="none", summary=summary, invoked=False)


__all__ = ["ExecutiveBrain"]
