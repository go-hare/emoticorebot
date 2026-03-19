"""Event-driven left-brain runtime for the documented v3 architecture."""

from __future__ import annotations

import asyncio
from typing import Any, cast
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.left_brain.packet import DecisionPacket, normalize_decision_packet, parse_decision_packet
from emoticorebot.protocol.commands import FollowupContextPayload, LeftReplyRequestPayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import (
    LeftFollowupReadyPayload,
    LeftReplyReadyPayload,
    LeftStreamDeltaPayload,
    TurnInputPayload,
)
from emoticorebot.protocol.reflection_models import ReflectionSignalPayload
from emoticorebot.protocol.task_models import MessageRef
from emoticorebot.protocol.topics import EventType
from emoticorebot.right_brain.store import RightBrainStore
from emoticorebot.utils.right_brain_projection import (
    normalize_task_state,
    project_task_from_runtime_snapshot,
    project_task_from_session_view,
)

from .reply_policy import ReplyPolicy

_USER_TAG = "####user####"
_TASK_TAG = "####task####"
_STREAM_FLUSH_CHARS = "。！？.!?\n"

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


def _strip_leading_newline(text: str) -> str:
    if text.startswith("\r\n"):
        return text[2:]
    if text.startswith("\n"):
        return text[1:]
    return text


def _extract_streamable_user_text(full_text: str) -> str | None:
    user_start = full_text.find(_USER_TAG)
    if user_start < 0:
        return None
    body = _strip_leading_newline(full_text[user_start + len(_USER_TAG) :])
    task_pos = body.find(_TASK_TAG)
    if task_pos >= 0:
        return body[:task_pos]
    return body


class _UserReplyStreamer:
    def __init__(self) -> None:
        self._emitted_chars = 0
        self._pending = ""

    def feed(self, full_text: str) -> list[str]:
        candidate = _extract_streamable_user_text(full_text)
        if candidate is None:
            return []
        fresh = candidate[self._emitted_chars :]
        self._emitted_chars = len(candidate)
        if fresh:
            self._pending += fresh
        if not self._pending:
            return []
        if len(self._pending) >= 24 or any(char in _STREAM_FLUSH_CHARS for char in self._pending):
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


class LeftBrainRuntime:
    """Owns left-brain turn processing and emits runtime events."""

    def __init__(
        self,
        *,
        bus: PriorityPubSubBus,
        task_store: RightBrainStore,
        left_brain_llm: Any | None = None,
        context_builder: Any | None = None,
        session_runtime: Any | None = None,
        reply_policy: ReplyPolicy | None = None,
    ) -> None:
        self._bus = bus
        self._tasks = task_store
        self._left_brain_llm = left_brain_llm
        self._context_builder = context_builder
        self._session_runtime = session_runtime
        self._reply_policy = reply_policy or ReplyPolicy()
        self._session_origins: dict[str, MessageRef] = {}
        self._tasks_in_flight: set[asyncio.Task[None]] = set()
        self._active_user_turns: dict[str, asyncio.Task[None]] = {}

    def register(self) -> None:
        self._bus.subscribe(
            consumer="left_runtime",
            event_type=EventType.LEFT_COMMAND_REPLY_REQUESTED,
            handler=self._on_left_reply_request,
        )
        self._bus.subscribe(consumer="left_runtime", event_type=EventType.REFLECTION_UPDATE_PERSONA, handler=self._ignore)
        self._bus.subscribe(consumer="left_runtime", event_type=EventType.REFLECTION_UPDATE_USER_MODEL, handler=self._ignore)

    async def _on_left_reply_request(self, event: BusEnvelope[object]) -> None:
        if event.event_type != EventType.LEFT_COMMAND_REPLY_REQUESTED:
            return
        command_event = cast(BusEnvelope[LeftReplyRequestPayload], event)
        if command_event.payload.followup_context is not None:
            self._spawn_followup_turn(command_event.session_id or "", command_event)
            return
        turn_event = self._turn_event_from_left_command(command_event)
        session_id = turn_event.session_id or ""
        self._cancel_active_user_turn(session_id)
        self._spawn_user_turn(session_id, turn_event)

    @staticmethod
    def _turn_event_from_left_command(event: BusEnvelope[LeftReplyRequestPayload]) -> BusEnvelope[TurnInputPayload]:
        if event.payload.turn_input is None:
            raise RuntimeError("left turn requests require turn_input")
        return build_envelope(
            event_type=EventType.INPUT_TURN_RECEIVED,
            source=event.source,
            target="left_runtime",
            session_id=event.session_id,
            turn_id=event.turn_id,
            task_id=event.task_id,
            correlation_id=event.correlation_id or event.turn_id,
            causation_id=event.event_id,
            payload=event.payload.turn_input,
        )

    def _spawn_user_turn(self, session_id: str, event: BusEnvelope[TurnInputPayload]) -> None:
        task = asyncio.create_task(self._run_user_turn(event), name=f"left-user-turn:{session_id or 'default'}")
        self._tasks_in_flight.add(task)
        self._active_user_turns[session_id] = task
        task.add_done_callback(self._tasks_in_flight.discard)
        task.add_done_callback(lambda finished, key=session_id: self._discard_user_turn(key, finished))

    def _spawn_followup_turn(self, session_id: str, event: BusEnvelope[LeftReplyRequestPayload]) -> None:
        task = asyncio.create_task(self._run_followup_turn(event), name=f"left-followup-turn:{session_id or 'default'}")
        self._tasks_in_flight.add(task)
        task.add_done_callback(self._tasks_in_flight.discard)
        task.add_done_callback(self._swallow_background_turn_result)

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

    @staticmethod
    def _swallow_background_turn_result(task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            return

    async def _run_user_turn(self, event: BusEnvelope[TurnInputPayload]) -> None:
        payload = event.payload
        user_text = self._user_text(payload)
        session_id = event.session_id or ""
        self._session_origins[session_id] = payload.message
        tasks = self._tasks.for_session(session_id)
        origin = payload.message
        suppress_delivery = self._suppress_delivery_from_metadata(payload.metadata)
        stream_id = f"stream_{event.turn_id or uuid4().hex[:12]}"
        stream_counter = 0
        current_reply_target = self._current_reply_delivery_target(payload=payload, origin=origin)
        current_delivery_mode = str(current_reply_target.get("delivery_mode", "") or "").strip()
        stream_enabled = self._can_stream_user_reply(payload=payload, origin=origin)

        async def _publish_stream_chunk(text: str) -> None:
            nonlocal stream_counter
            chunk = str(text or "")
            if not chunk:
                return
            stream_state = "open" if stream_counter == 0 else "delta"
            stream_counter += 1
            await self._publish_left_stream_delta_ready(
                session_id=session_id,
                turn_id=event.turn_id,
                causation_id=event.event_id,
                correlation_id=event.turn_id,
                stream_id=stream_id,
                delta_text=chunk,
                stream_state=stream_state,
                stream_index=stream_counter,
                origin_message=origin,
                metadata=self._reply_metadata(suppress_delivery=suppress_delivery),
            )

        packet = await self._decide_user_turn(
            event=event,
            user_text=user_text,
            tasks=tasks,
            on_user_stream=_publish_stream_chunk if stream_enabled else None,
        )
        task_action = str(packet.get("task_action", "none") or "none").strip()
        task_mode = str(packet.get("task_mode", "skip") or "skip").strip()
        reply_text = str(packet.get("final_message", "") or "").strip()
        invoke_right_brain = task_action in {"create_task", "cancel_task"}
        if not reply_text:
            reply_text = self._default_user_turn_reply(task_action=task_action)
        reflection_task = None
        final_reply_metadata = (
            self._reply_metadata(suppress_delivery=suppress_delivery)
            if stream_enabled and stream_counter > 0
            else self._reply_metadata(suppress_delivery=suppress_delivery)
        )

        task = packet.get("task", {}) if isinstance(packet.get("task"), dict) else {}
        task_id = str(task.get("task_id", "") or "").strip()
        if task_action == "cancel_task" and task_id:
            reflection_task = self._tasks.get(task_id)

        reply_kind = "status" if task_action in {"create_task", "cancel_task"} else "answer"
        right_brain_request = self._build_right_brain_request(
            event=event,
            payload=payload,
            user_text=user_text,
            task_action=task_action,
            task_mode=task_mode,
            task=task,
            suppress_delivery=suppress_delivery,
        )
        await self._publish_left_reply_ready(
            event=event,
            reply_text=reply_text,
            reply_kind=reply_kind,
            delivery_target=current_reply_target,
            origin_message=origin,
            invoke_right_brain=invoke_right_brain,
            right_brain_request=right_brain_request,
            related_task_id=task_id or None,
            stream_id=stream_id if current_delivery_mode == "stream" else None,
            stream_state="close" if current_delivery_mode == "stream" else None,
            metadata=self._merge_reply_metadata(
                final_reply_metadata,
                {
                    "task_action": task_action,
                    "task_mode": task_mode,
                    "task_reason": str(packet.get("task_reason", "") or "").strip(),
                },
            ),
        )

        await self._publish_reflection(
            event,
            reason="user_turn",
            metadata=self._user_turn_reflection_metadata(
                event=event,
                user_text=user_text,
                reply_text=reply_text,
                decision_packet=packet,
                task=reflection_task,
                execution=self._execution_from_packet(packet, task=reflection_task),
            ),
        )

    async def _run_followup_turn(self, event: BusEnvelope[LeftReplyRequestPayload]) -> None:
        followup = event.payload.followup_context
        if followup is None:
            return
        task = self._tasks.get(event.task_id or "")
        session_id = event.session_id or ""
        origin = self._origin_for(session_id, task)
        if origin is not None:
            self._session_origins[session_id] = origin
        reply_text, reply_kind = self._followup_reply(task=task, followup=followup)
        if not reply_text:
            return
        await self._publish_left_followup_ready(
            event=event,
            followup=followup,
            reply_text=reply_text,
            reply_kind=reply_kind,
            origin_message=origin,
            task=task,
        )

    async def _publish_reflection(
        self,
        event: BusEnvelope[object],
        *,
        reason: str,
        event_type: str = EventType.REFLECTION_LIGHT,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._bus.publish(
            build_envelope(
                event_type=event_type,
                source="left_runtime",
                target="reflection_governor",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=event.task_id,
                correlation_id=event.correlation_id or event.task_id or event.turn_id,
                causation_id=event.event_id,
                payload=ReflectionSignalPayload(
                    trigger_id=f"reflection_{uuid4().hex[:12]}",
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
    def _user_text(payload: TurnInputPayload) -> str:
        if payload.user_text:
            return payload.user_text
        if payload.input_slots.user:
            return payload.input_slots.user
        parts = [block.text for block in payload.content_blocks if block.type == "text" and block.text]
        return "\n".join(parts).strip()

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
    def _delivery_flag(*, suppress_delivery: bool) -> dict[str, Any]:
        return {"suppress_delivery": True} if suppress_delivery else {}

    @staticmethod
    def _suppress_followup_output(followup: FollowupContextPayload) -> bool:
        delivery_mode = str(getattr(followup.delivery_target, "delivery_mode", "") or "").strip()
        if delivery_mode == "push":
            return False
        return followup.source_event in {
            str(EventType.RIGHT_EVENT_JOB_ACCEPTED),
            str(EventType.RIGHT_EVENT_PROGRESS),
        }

    def _task_create_context(
        self,
        *,
        payload: TurnInputPayload,
        user_text: str,
        origin: MessageRef,
        suppress_delivery: bool,
    ) -> dict[str, Any]:
        return self._compact_dict(
            {
                "history_context": str(payload.metadata.get("history_context", "") or "").strip() or None,
                "recent_turns": list(payload.metadata.get("recent_turns", []) or []),
                "short_term_memory": list(payload.metadata.get("short_term_memory", []) or []),
                "long_term_memory": list(payload.metadata.get("long_term_memory", []) or []),
                "tool_context": dict(payload.metadata.get("tool_context", {}) or {}),
                "source_input_mode": self._source_input_mode(payload),
                "current_delivery_mode": self._current_delivery_mode(payload),
                "available_delivery_modes": self._available_delivery_modes(payload),
                "memory_refs": self._task_memory_refs(query=user_text),
                "skill_hints": self._task_skill_hints(query=user_text),
                "content_blocks": list(payload.content_blocks) + list(payload.attachments),
                "origin_message": origin.model_dump(exclude_none=True),
                "suppress_delivery": True if suppress_delivery else None,
            }
        )

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
            self._delivery_flag(
                suppress_delivery=suppress_delivery or self._task_suppress_delivery(task),
            ),
        )

    def _followup_reply(self, *, task: object | None, followup: FollowupContextPayload) -> tuple[str, str]:
        runtime_task = task if task is None or hasattr(task, "task_id") else None
        if followup.source_event == str(EventType.RIGHT_EVENT_JOB_ACCEPTED):
            return (
                self._reply_policy.right_brain_accepted(
                    runtime_task,
                    reason=str(followup.reason or "").strip() or None,
                ),
                "status",
            )
        if followup.source_event == str(EventType.RIGHT_EVENT_PROGRESS):
            return (
                self._reply_policy.right_brain_progress(
                    runtime_task,
                    summary=str(followup.summary or "").strip() or "右脑正在继续处理。",
                    next_step=str(followup.next_step or "").strip() or None,
                ),
                "status",
            )
        if followup.source_event == str(EventType.RIGHT_EVENT_JOB_REJECTED):
            return (
                self._reply_policy.right_brain_rejected(
                    runtime_task,
                    reason=str(followup.reason or "").strip() or "当前无法处理。",
                ),
                "status",
            )
        outcome = str(followup.metadata.get("result", "") or "").strip() or None
        return (
            self._reply_policy.right_brain_result(
                runtime_task,
                decision=followup.decision,
                summary=str(followup.summary or "").strip() or None,
                result_text=str(followup.result_text or "").strip() or None,
                outcome=outcome,
            ),
            "answer" if followup.decision == "answer_only" else "status",
        )

    @staticmethod
    def _string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item.strip() for item in (str(entry or "") for entry in value) if item.strip()]

    @staticmethod
    def _merge_string_lists(*parts: list[str]) -> list[str]:
        merged: list[str] = []
        for part in parts:
            for item in part:
                text = str(item or "").strip()
                if text and text not in merged:
                    merged.append(text)
        return merged

    @staticmethod
    def _compact_dict(payload: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in payload.items() if value not in ("", None, [], {})}

    def _right_brain_delivery_target(
        self,
        *,
        payload: TurnInputPayload | None,
        origin: MessageRef | None,
        task_mode: str,
        task: object | None = None,
    ) -> dict[str, Any]:
        task_target = getattr(task, "delivery_target", None) if task is not None else None
        delivery_mode = self._task_delivery_mode(payload=payload, task_mode=task_mode)
        channel = None
        chat_id = None
        if task_target is not None:
            channel = str(getattr(task_target, "channel", "") or "").strip() or None
            chat_id = str(getattr(task_target, "chat_id", "") or "").strip() or None
        if channel is None and origin is not None:
            channel = str(origin.channel or "").strip() or None
        if chat_id is None and origin is not None:
            chat_id = str(origin.chat_id or "").strip() or None
        return self._compact_dict(
            {
                "delivery_mode": delivery_mode,
                "channel": channel,
                "chat_id": chat_id,
            }
        )

    @staticmethod
    def _source_input_mode(payload: TurnInputPayload) -> str:
        source_mode = str((payload.metadata or {}).get("source_input_mode", "") or "").strip()
        if source_mode in {"turn", "stream"}:
            return source_mode
        input_mode = str(getattr(payload, "input_mode", "") or "").strip()
        return input_mode if input_mode in {"turn", "stream"} else "turn"

    @classmethod
    def _current_delivery_mode(cls, payload: TurnInputPayload) -> str:
        current_mode = str((payload.metadata or {}).get("current_delivery_mode", "") or "").strip()
        if current_mode in {"inline", "push", "stream"}:
            return current_mode
        return "stream" if cls._source_input_mode(payload) == "stream" else "inline"

    @staticmethod
    def _delivery_modes(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        modes: list[str] = []
        for item in value:
            mode = str(item or "").strip()
            if mode in {"inline", "push", "stream"} and mode not in modes:
                modes.append(mode)
        return modes

    @classmethod
    def _available_delivery_modes(cls, payload: TurnInputPayload) -> list[str]:
        modes = cls._delivery_modes((payload.metadata or {}).get("available_delivery_modes"))
        if not modes:
            modes = ["stream", "inline", "push"] if cls._source_input_mode(payload) == "stream" else ["inline", "push"]
        current_mode = cls._current_delivery_mode(payload)
        if current_mode not in modes:
            modes.insert(0, current_mode)
        return modes

    @classmethod
    def _task_delivery_mode(cls, *, payload: TurnInputPayload | None, task_mode: str) -> str:
        if task_mode == "async":
            return "push"
        if payload is None:
            return "inline"
        current_mode = cls._current_delivery_mode(payload)
        return current_mode if current_mode in {"inline", "stream"} else "inline"

    def _current_reply_delivery_target(
        self,
        *,
        payload: TurnInputPayload,
        origin: MessageRef | None,
    ) -> dict[str, Any]:
        return self._compact_dict(
            {
                "delivery_mode": self._current_delivery_mode(payload),
                "channel": str(origin.channel or "").strip() or None if origin is not None else None,
                "chat_id": str(origin.chat_id or "").strip() or None if origin is not None else None,
            }
        )

    def _build_right_brain_request(
        self,
        *,
        event: BusEnvelope[TurnInputPayload],
        payload: TurnInputPayload,
        user_text: str,
        task_action: str,
        task_mode: str,
        task: dict[str, Any],
        suppress_delivery: bool,
    ) -> dict[str, Any]:
        if task_action not in {"create_task", "cancel_task"}:
            return {}
        origin = payload.message
        if task_action == "create_task":
            return {
                "job_id": f"job_{uuid4().hex[:12]}",
                "job_action": "create_task",
                "job_kind": "execution_review",
                "source_text": user_text,
                "request_text": user_text,
                "delivery_target": self._right_brain_delivery_target(
                    payload=payload,
                    origin=origin,
                    task_mode=task_mode,
                ),
                "scores": {},
                "context": self._merge_reply_metadata(
                    self._task_create_context(
                        payload=payload,
                        user_text=user_text,
                        origin=payload.message,
                        suppress_delivery=suppress_delivery,
                    ),
                    {"task_mode": task_mode},
                )
                or {},
            }
        task_id = str(task.get("task_id", "") or "").strip()
        runtime_task = self._tasks.get(task_id) if task_id else None
        reason = str(packet_reason if (packet_reason := task.get("reason")) else "").strip() or user_text or "user_cancelled"
        return self._compact_dict(
            {
                "job_id": f"job_{uuid4().hex[:12]}",
                "job_action": "cancel_task",
                "job_kind": "execution_review",
                "task_id": task_id or None,
                "source_text": user_text,
                "request_text": reason,
                "delivery_target": self._right_brain_delivery_target(
                    payload=payload,
                    origin=origin,
                    task_mode=task_mode,
                    task=runtime_task,
                ),
                "scores": {},
                "context": {
                    "reason": reason,
                    "source_event_id": event.event_id,
                    "source_input_mode": self._source_input_mode(payload),
                    "task_mode": task_mode,
                },
            }
        )

    def _task_memory_refs(self, *, query: str) -> list[str]:
        bundle = self._task_memory_bundle(query=query)
        refs: list[str] = []
        for key in ("relevant_task_memories", "relevant_tool_memories"):
            for record in list(bundle.get(key, []) or []):
                text = self._format_memory_ref(record)
                if text and text not in refs:
                    refs.append(text)
        return refs[:6]

    def _task_skill_hints(self, *, query: str) -> list[str]:
        bundle = self._task_memory_bundle(query=query)
        hints: list[str] = []
        for record in list(bundle.get("skill_hints", []) or []):
            text = self._format_skill_hint(record)
            if text and text not in hints:
                hints.append(text)
        return hints[:6]

    def _task_memory_bundle(self, *, query: str) -> dict[str, Any]:
        text = str(query or "").strip()
        if not text or self._context_builder is None or not hasattr(self._context_builder, "build_task_memory_bundle"):
            return {}
        try:
            bundle = self._context_builder.build_task_memory_bundle(query=text, limit=6)
        except Exception:
            return {}
        return dict(bundle) if isinstance(bundle, dict) else {}

    @staticmethod
    def _format_memory_ref(record: object) -> str:
        if not isinstance(record, dict):
            return ""
        summary = str(record.get("summary", "") or record.get("detail", "") or "").strip()
        record_type = str(record.get("memory_type", "") or record.get("subtype", "") or "").strip()
        if not summary:
            return ""
        if record_type:
            return f"[{record_type}] {summary}"
        return summary

    @staticmethod
    def _format_skill_hint(record: object) -> str:
        if not isinstance(record, dict):
            return ""
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        skill_name = str(metadata.get("skill_name", "") or "").strip()
        hint = str(metadata.get("hint", "") or record.get("summary", "") or "").strip()
        trigger = str(metadata.get("trigger", "") or "").strip()
        parts: list[str] = []
        if skill_name:
            parts.append(f"技能 `{skill_name}`")
        if trigger:
            parts.append(f"触发: {trigger}")
        if hint:
            parts.append(hint)
        if parts:
            return " | ".join(parts)
        return str(record.get("summary", "") or record.get("detail", "") or "").strip()

    async def _decide_user_turn(
        self,
        *,
        event: BusEnvelope[TurnInputPayload],
        user_text: str,
        tasks: list[object],
        on_user_stream: Any | None = None,
    ) -> DecisionPacket:
        if self._left_brain_llm is None:
            raise RuntimeError("LeftBrainRuntime requires left_brain_llm for user-turn decisions")

        messages = await self._build_decision_messages(
            session_id=event.session_id or "",
            user_text=user_text,
            history_context=str(event.payload.metadata.get("history_context", "") or "").strip(),
            tasks=tasks,
            source_input_mode=self._source_input_mode(event.payload),
            current_delivery_mode=self._current_delivery_mode(event.payload),
            available_delivery_modes=self._available_delivery_modes(event.payload),
        )
        response = await self._invoke_model(messages, on_user_stream=on_user_stream)
        raw_payload = parse_decision_packet(response)
        return normalize_decision_packet(
            raw_payload,
            current_context={
                "user_input": user_text,
                "history_context": str(event.payload.metadata.get("history_context", "") or "").strip(),
                "active_task_id": self._latest_active_task_id(event.session_id or "", tasks),
                "latest_task_id": self._latest_task_id(event.session_id or "", tasks),
                "source_input_mode": self._source_input_mode(event.payload),
                "current_delivery_mode": self._current_delivery_mode(event.payload),
                "available_delivery_modes": self._available_delivery_modes(event.payload),
            },
        )

    async def _invoke_model(
        self,
        messages: list[SystemMessage | HumanMessage],
        *,
        on_user_stream: Any | None = None,
    ) -> Any:
        if on_user_stream is not None and hasattr(self._left_brain_llm, "astream"):
            full_text = ""
            streamer = _UserReplyStreamer()
            async for chunk in self._left_brain_llm.astream(messages):
                text = _chunk_text(chunk)
                if not text:
                    continue
                full_text += text
                for item in streamer.feed(full_text):
                    await on_user_stream(item)
            for item in streamer.finish():
                await on_user_stream(item)
            return full_text
        if hasattr(self._left_brain_llm, "ainvoke"):
            return await self._left_brain_llm.ainvoke(messages)
        if hasattr(self._left_brain_llm, "invoke"):
            return self._left_brain_llm.invoke(messages)
        raise RuntimeError("left_brain_llm does not support invoke/ainvoke")

    @classmethod
    def _can_stream_user_reply(cls, *, payload: TurnInputPayload, origin: MessageRef) -> bool:
        return (
            cls._current_delivery_mode(payload) == "stream"
            and bool(str(origin.channel or "").strip() and str(origin.chat_id or "").strip())
        )

    async def _build_decision_messages(
        self,
        *,
        session_id: str,
        user_text: str,
        history_context: str,
        tasks: list[object],
        source_input_mode: str,
        current_delivery_mode: str,
        available_delivery_modes: list[str],
    ) -> list[SystemMessage | HumanMessage]:
        system_prompt = ""
        if self._context_builder is not None and hasattr(self._context_builder, "build_left_brain_system_prompt"):
            system_prompt = self._context_builder.build_left_brain_system_prompt(query=user_text)
        elif self._context_builder is not None and hasattr(self._context_builder, "build_left_brain_decision_system_prompt"):
            system_prompt = self._context_builder.build_left_brain_decision_system_prompt(query=user_text)
        messages: list[SystemMessage | HumanMessage] = []
        if system_prompt.strip():
            messages.append(SystemMessage(content=system_prompt.strip()))
        messages.append(
            HumanMessage(
                content=await self._user_turn_instruction(
                    session_id,
                    history_context,
                    tasks,
                    user_text,
                    source_input_mode,
                    current_delivery_mode,
                    available_delivery_modes,
                )
            )
        )
        return messages

    async def _user_turn_instruction(
        self,
        session_id: str,
        history_context: str,
        tasks: list[object],
        user_text: str,
        source_input_mode: str,
        current_delivery_mode: str,
        available_delivery_modes: list[str],
    ) -> str:
        lines = [
            "## 当前轮执行要求",
            "你现在要对这条用户输入做一次完整决策，并且只能输出两个文本区块：`####user####` 和 `####task####`。",
            "不要输出 markdown，不要输出解释，不要输出额外文本。",
            "格式固定如下，并且必须先输出 `####user####`，再输出 `####task####`：",
            "####user####",
            "<给用户看的自然语言回复>",
            "",
            "####task####",
            "action=<none|create_task|cancel_task>",
            "task_mode=<skip|sync|async>",
            "task_id=<仅 cancel_task 时填写>",
            "reason=<可选>",
            "",
            "`####user####` 会直接发给用户，并且可能被流式输出，必须自然、简洁。",
            "`####task####` 只给系统看，必须是紧凑 key=value 行。",
            "当前环境事实不是裁决结果，你必须结合用户语义自己决定 `task_mode`。",
            "如果只是简单问答、闲聊、解释、计算，输出 `action=none` 且 `task_mode=skip`。",
            "如果确实需要进入任务执行，再输出 `action=create_task / cancel_task`。",
            "如果任务要留在当前会话链路里继续收束，输出 `task_mode=sync`。",
            "如果当前轮先结束、结果稍后再通知，输出 `task_mode=async`。",
            "凡是用户要求创建文件、修改文件、运行命令、检查环境、调用工具、生成产物，必须输出 `action=create_task`。",
            "不要假装任务已经完成；只要还没有经过 runtime/右脑执行，就不能在 `####user####` 里声称文件已创建、命令已运行或结果已落盘。",
        ]
        task_lines = await self._task_context_lines(session_id, tasks)
        lines.extend(
            [
                "",
                "## 当前环境",
                f"- 输入形态: {source_input_mode}",
                f"- 当前前台投递: {current_delivery_mode}",
                f"- 可用投递: {', '.join(available_delivery_modes)}",
            ]
        )
        if history_context:
            lines.extend(["", "## 最近对话摘要", history_context])
        if task_lines:
            lines.extend(["", "## 当前 session 任务上下文", *task_lines])
        lines.extend(["", "## 用户消息", user_text])
        return "\n".join(lines).strip()

    async def _task_context_lines(self, session_id: str, tasks: list[object]) -> list[str]:
        lines: list[str] = []
        for task in tasks[-5:]:
            title = str(getattr(task, "title", "") or getattr(task, "task_id", "")).strip()
            task_id = str(getattr(task, "task_id", "") or "").strip()
            state = str(getattr(getattr(task, "state", None), "value", "") or "").strip()
            visible_status = normalize_task_state(state) if state else "running"
            summary = str(getattr(task, "summary", "") or getattr(task, "last_progress", "") or "").strip()
            request = ""
            if getattr(task, "request", None) is not None:
                request = str(getattr(task, "request").request or "").strip()
            session_task = self._session_task_view(session_id, task_id)
            if session_task is not None:
                visible_status = session_task.state
                summary = session_task.summary or summary
            item = {
                "task_id": task_id,
                "title": title,
                "status": visible_status,
                "summary": summary,
                "request": request,
            }
            trace_summary = await self._task_trace_summary(session_id, task_id)
            if trace_summary:
                item["trace"] = trace_summary
            lines.append(f"- {item}")
        return lines

    @staticmethod
    def _default_user_turn_reply(*, task_action: str) -> str:
        if task_action == "create_task":
            return "收到，我开始处理。"
        if task_action == "cancel_task":
            return "收到，我先停下这个任务。"
        return "收到。"

    async def _publish_left_stream_delta_ready(
        self,
        *,
        session_id: str,
        turn_id: str | None,
        causation_id: str | None,
        correlation_id: str | None,
        stream_id: str,
        delta_text: str,
        stream_state: str = "delta",
        stream_index: int | None = None,
        origin_message: MessageRef | None = None,
        metadata: dict[str, Any] | None = None,
        related_task_id: str | None = None,
    ) -> None:
        if not delta_text:
            return
        await self._bus.publish(
            build_envelope(
                event_type=EventType.LEFT_EVENT_STREAM_DELTA_READY,
                source="left_runtime",
                target="broadcast",
                session_id=session_id,
                turn_id=turn_id,
                task_id=related_task_id,
                correlation_id=correlation_id or related_task_id or turn_id,
                causation_id=causation_id,
                payload=LeftStreamDeltaPayload(
                    stream_id=stream_id,
                    delta_text=delta_text,
                    stream_state=stream_state if stream_state in {"open", "delta", "close", "superseded"} else "delta",
                    stream_index=stream_index,
                    origin_message=origin_message,
                    metadata=dict(metadata or {}),
                ),
            )
        )

    async def _publish_left_reply_ready(
        self,
        *,
        event: BusEnvelope[TurnInputPayload],
        reply_text: str,
        reply_kind: str,
        delivery_target: dict[str, Any],
        origin_message: MessageRef | None,
        invoke_right_brain: bool,
        right_brain_request: dict[str, Any],
        related_task_id: str | None,
        stream_id: str | None = None,
        stream_state: str | None = None,
        stream_index: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._bus.publish(
            build_envelope(
                event_type=EventType.LEFT_EVENT_REPLY_READY,
                source="left_runtime",
                target="broadcast",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=related_task_id or event.task_id,
                correlation_id=related_task_id or event.correlation_id or event.turn_id,
                causation_id=event.event_id,
                payload=LeftReplyReadyPayload(
                    request_id=f"left_reply_{uuid4().hex[:12]}",
                    reply_text=reply_text,
                    reply_kind=reply_kind if reply_kind in {"answer", "status"} else "answer",
                    delivery_target=dict(delivery_target or {}),
                    origin_message=origin_message,
                    invoke_right_brain=invoke_right_brain,
                    right_brain_request=dict(right_brain_request or {}),
                    related_task_id=related_task_id,
                    stream_id=stream_id,
                    stream_state=stream_state if stream_state in {"open", "delta", "close", "superseded"} else None,
                    stream_index=stream_index,
                    metadata=dict(metadata or {}),
                ),
            )
        )

    async def _publish_left_followup_ready(
        self,
        *,
        event: BusEnvelope[LeftReplyRequestPayload],
        followup: FollowupContextPayload,
        reply_text: str,
        reply_kind: str,
        origin_message: MessageRef | None,
        task: object | None,
    ) -> None:
        await self._bus.publish(
            build_envelope(
                event_type=EventType.LEFT_EVENT_FOLLOWUP_READY,
                source="left_runtime",
                target="broadcast",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=event.task_id,
                correlation_id=event.task_id or event.correlation_id or followup.job_id,
                causation_id=event.event_id,
                payload=LeftFollowupReadyPayload(
                    job_id=followup.job_id,
                    source_event=followup.source_event,
                    source_decision=followup.decision,
                    reply_text=reply_text,
                    reply_kind=reply_kind if reply_kind in {"answer", "status"} else "status",
                    delivery_target=followup.delivery_target,
                    origin_message=origin_message,
                    related_task_id=event.task_id,
                    metadata=self._merge_reply_metadata(
                        dict(followup.metadata or {}),
                        {"followup_source": followup.source_event},
                        {"suppress_output": True} if self._suppress_followup_output(followup) else None,
                        self._delivery_flag(suppress_delivery=self._task_suppress_delivery(task)),
                    )
                    or {},
                ),
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
        event: BusEnvelope[TurnInputPayload],
        user_text: str,
        reply_text: str,
        decision_packet: DecisionPacket,
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
                "left_brain": dict(decision_packet),
                "task": self._task_snapshot(task, session_id=event.session_id or ""),
                "execution": execution or {},
                "metadata": {"source_event_type": event.event_type},
            }
        }

    def _followup_reflection_metadata(
        self,
        *,
        event: BusEnvelope[LeftReplyRequestPayload],
        followup: FollowupContextPayload,
        task: object,
        reply_text: str,
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
                "task": self._task_snapshot(task, session_id=event.session_id or ""),
                "execution": self._followup_execution(followup=followup, reply_text=reply_text),
                "metadata": {
                    "source_event_type": followup.source_event,
                    "decision": followup.decision,
                    "related_task_id": event.task_id or "",
                },
            }
        }

    @staticmethod
    def _execution_payload(
        *,
        status: str,
        summary: str,
        invoked: bool = True,
        failure_reason: str | None = None,
    ) -> dict[str, Any]:
        return {
            "invoked": invoked,
            "status": status,
            "summary": summary,
            "failure_reason": failure_reason or "",
        }

    @staticmethod
    def _followup_reason(followup: FollowupContextPayload) -> str:
        if followup.source_event == str(EventType.RIGHT_EVENT_JOB_ACCEPTED):
            return "right_brain_accepted"
        if followup.source_event == str(EventType.RIGHT_EVENT_PROGRESS):
            return "right_brain_progress"
        if followup.source_event == str(EventType.RIGHT_EVENT_JOB_REJECTED):
            return "task_rejected"
        if followup.decision == "answer_only":
            return "right_brain_answer_only"
        outcome = str(followup.metadata.get("result", "") or "").strip()
        if outcome == "cancelled":
            return "task_cancelled"
        if outcome == "failed":
            return "task_failed"
        return "task_result"

    def _followup_execution(self, *, followup: FollowupContextPayload, reply_text: str) -> dict[str, Any]:
        if followup.source_event == str(EventType.RIGHT_EVENT_JOB_ACCEPTED):
            return {
                "invoked": True,
                "status": "running",
                "summary": str(followup.reason or reply_text or "").strip(),
                "failure_reason": "",
            }
        if followup.source_event == str(EventType.RIGHT_EVENT_PROGRESS):
            return {
                "invoked": True,
                "status": "running",
                "summary": str(followup.summary or reply_text or "").strip(),
                "failure_reason": "",
            }
        if followup.source_event == str(EventType.RIGHT_EVENT_JOB_REJECTED):
            return self._execution_payload(
                status="failed",
                summary=str(followup.reason or reply_text or "").strip() or "rejected",
                failure_reason=str(followup.reason or "rejected").strip(),
            )
        outcome = str(followup.metadata.get("result", "") or "").strip()
        if outcome == "cancelled":
            return self._execution_payload(
                status="failed",
                summary=str(followup.summary or reply_text or "").strip() or "cancelled",
                failure_reason="task_cancelled",
            )
        if outcome == "failed":
            return self._execution_payload(
                status="failed",
                summary=str(followup.summary or reply_text or "").strip() or "failed",
                failure_reason=str(followup.result_text or followup.summary or "task_failed").strip(),
            )
        return self._execution_payload(
            status="done",
            summary=str(followup.summary or reply_text or "").strip() or "done",
        )

    def _task_snapshot(self, task: object, *, session_id: str = "") -> dict[str, Any]:
        if task is None or not hasattr(task, "task_id"):
            return {}
        params = {}
        if getattr(task, "request", None) is not None:
            params = getattr(task, "request").model_dump(exclude_none=True)
        task_id = str(getattr(task, "task_id", "") or "").strip()
        if self._session_runtime is not None and session_id and task_id:
            task_view = self._session_runtime.task_view(session_id, task_id)
            if task_view is not None:
                return project_task_from_session_view(task_view, params=params)
        return project_task_from_runtime_snapshot(
            {
                "task_id": task_id,
                "state": str(getattr(getattr(task, "state", None), "value", "") or "").strip(),
                "result": str(getattr(task, "result", "") or "").strip(),
                "state_version": getattr(task, "state_version", 1),
                "title": str(getattr(task, "title", "") or "").strip(),
                "summary": str(getattr(task, "summary", "") or "").strip(),
                "error": str(getattr(task, "error", "") or "").strip(),
                "last_progress": str(getattr(task, "last_progress", "") or "").strip(),
                "updated_at": str(getattr(task, "updated_at", "") or "").strip(),
            },
            params=params,
            trace=[dict(item) for item in list(getattr(task, "trace_log", []) or []) if isinstance(item, dict)],
        )

    def _latest_active_task_id(self, session_id: str, tasks: list[object]) -> str:
        if self._session_runtime is not None:
            task_id = str(self._session_runtime.latest_active_task_id(session_id) or "").strip()
            if task_id:
                return task_id
        for task in reversed(tasks):
            state = str(getattr(getattr(task, "state", None), "value", "") or "")
            if state != "done":
                return str(getattr(task, "task_id", "") or "").strip()
        return ""

    def _latest_task_id(self, session_id: str, tasks: list[object]) -> str:
        if self._session_runtime is not None:
            task_id = str(self._session_runtime.latest_task_id(session_id) or "").strip()
            if task_id:
                return task_id
        if not tasks:
            return ""
        return str(getattr(tasks[-1], "task_id", "") or "").strip()

    def _session_task_view(self, session_id: str, task_id: str) -> Any | None:
        if self._session_runtime is None or not session_id or not task_id:
            return None
        return self._session_runtime.task_view(session_id, task_id)

    async def _task_trace_summary(self, session_id: str, task_id: str) -> str:
        if self._session_runtime is None or not session_id or not task_id:
            return ""
        return " | ".join(await self._session_runtime.consume_task_trace_summary(session_id, task_id, limit=2))

    @staticmethod
    def _execution_from_packet(packet: DecisionPacket, *, task: object) -> dict[str, Any]:
        action = str(packet.get("task_action", "none") or "none").strip()
        summary = "主脑完成了当前轮判断。"
        if action == "create_task":
            return LeftBrainRuntime._execution_payload(status="running", summary=summary, invoked=True)
        if action == "cancel_task":
            return LeftBrainRuntime._execution_payload(
                status="failed",
                summary=summary,
                invoked=bool(task is not None),
                failure_reason=str(packet.get("task_reason", "") or "").strip() or "user_cancelled",
            )
        return LeftBrainRuntime._execution_payload(status="none", summary=summary, invoked=False)


__all__ = ["LeftBrainRuntime"]





