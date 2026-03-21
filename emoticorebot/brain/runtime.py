"""Event-driven brain runtime for the documented v3 architecture."""

from __future__ import annotations

import asyncio
from typing import Any, cast
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.brain.packet import DecisionPacket, normalize_decision_packet, parse_decision_packet
from emoticorebot.protocol.commands import BrainReplyRequestPayload, ExecutorResultContextPayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import (
    BrainReplyReadyPayload,
    BrainStreamDeltaPayload,
    SystemSignalPayload,
    TurnInputPayload,
)
from emoticorebot.protocol.reflection_models import ReflectionSignalPayload
from emoticorebot.protocol.task_models import MessageRef
from emoticorebot.protocol.topics import EventType
from emoticorebot.executor.store import ExecutorStore
from emoticorebot.utils.executor_projection import project_task_from_runtime_snapshot

from .prompt_builder import BrainPromptBuilder
_USER_TAG = "#####user######"
_ACTION_TAG = "#####Action######"
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
    action_pos = body.find(_ACTION_TAG)
    if action_pos >= 0:
        return body[:action_pos]
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


class BrainRuntime:
    """Owns brain turn processing and emits runtime events."""

    def __init__(
        self,
        *,
        bus: PriorityPubSubBus,
        task_store: ExecutorStore,
        brain_llm: Any | None = None,
        context_builder: Any | None = None,
        session_runtime: Any | None = None,
    ) -> None:
        self._bus = bus
        self._tasks = task_store
        self._brain_llm = brain_llm
        self._context_builder = context_builder
        self._session_runtime = session_runtime
        self._prompt_builder = BrainPromptBuilder(
            session_runtime=session_runtime,
            task_snapshot=self._task_snapshot,
        )
        self._session_origins: dict[str, MessageRef] = {}
        self._tasks_in_flight: set[asyncio.Task[None]] = set()
        self._active_user_turns: dict[str, asyncio.Task[None]] = {}

    def register(self) -> None:
        self._bus.subscribe(
            consumer="brain_runtime",
            event_type=EventType.BRAIN_COMMAND_REPLY_REQUESTED,
            handler=self._on_brain_reply_request,
        )
        self._bus.subscribe(consumer="brain_runtime", event_type=EventType.REFLECTION_UPDATE_PERSONA, handler=self._ignore)
        self._bus.subscribe(consumer="brain_runtime", event_type=EventType.REFLECTION_UPDATE_USER_MODEL, handler=self._ignore)

    async def _on_brain_reply_request(self, event: BusEnvelope[object]) -> None:
        if event.event_type != EventType.BRAIN_COMMAND_REPLY_REQUESTED:
            return
        command_event = cast(BusEnvelope[BrainReplyRequestPayload], event)
        if command_event.payload.executor_result is not None:
            self._spawn_executor_result_turn(command_event.session_id or "", command_event)
            return
        turn_event = self._turn_event_from_brain_command(command_event)
        session_id = turn_event.session_id or ""
        self._cancel_active_user_turn(session_id)
        self._spawn_user_turn(session_id, turn_event)

    @staticmethod
    def _turn_event_from_brain_command(event: BusEnvelope[BrainReplyRequestPayload]) -> BusEnvelope[TurnInputPayload]:
        if event.payload.turn_input is None:
            raise RuntimeError("brain turn requests require turn_input")
        return build_envelope(
            event_type=EventType.INPUT_TURN_RECEIVED,
            source=event.source,
            target="brain_runtime",
            session_id=event.session_id,
            turn_id=event.turn_id,
            task_id=event.task_id,
            correlation_id=event.correlation_id or event.turn_id,
            causation_id=event.event_id,
            payload=event.payload.turn_input,
        )

    def _spawn_user_turn(self, session_id: str, event: BusEnvelope[TurnInputPayload]) -> None:
        task = asyncio.create_task(self._run_user_turn(event), name=f"brain-user-turn:{session_id or 'default'}")
        self._tasks_in_flight.add(task)
        self._active_user_turns[session_id] = task
        task.add_done_callback(self._tasks_in_flight.discard)
        task.add_done_callback(lambda finished, key=session_id: self._discard_user_turn(key, finished))

    def _spawn_executor_result_turn(self, session_id: str, event: BusEnvelope[BrainReplyRequestPayload]) -> None:
        task = asyncio.create_task(
            self._run_executor_result_turn(event),
            name=f"brain-executor-result:{session_id or 'default'}",
        )
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
        try:
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
                await self._publish_brain_stream_delta_ready(
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
            reply_text = str(packet.get("final_message", "") or "").strip()
            execute_actions = self._execute_actions(packet)
            invoke_executor = bool(execute_actions)
            related_task_id = self._related_task_id_from_actions(packet)
            if not reply_text:
                raise RuntimeError("Brain output requires a non-empty #####user###### section")
            reflection_task = self._reflection_task_from_actions(execute_actions)
            final_reply_metadata = self._reply_metadata(
                extra={"stream_close_without_body": True} if stream_enabled and stream_counter > 0 else None,
                suppress_delivery=suppress_delivery,
            )

            reply_kind = "status" if invoke_executor else "answer"
            executor_requests = self._build_executor_requests(
                event=event,
                payload=payload,
                user_text=user_text,
                actions=execute_actions,
                suppress_delivery=suppress_delivery,
            )
            await self._publish_brain_reply_ready(
                event=event,
                reply_text=reply_text,
                reply_kind=reply_kind,
                delivery_target=current_reply_target,
                origin_message=origin,
                invoke_executor=invoke_executor,
                executor_requests=executor_requests,
                related_task_id=related_task_id,
                stream_id=stream_id if current_delivery_mode == "stream" else None,
                stream_state="close" if current_delivery_mode == "stream" else None,
                metadata=self._merge_reply_metadata(
                    final_reply_metadata,
                    {"actions": [dict(item) for item in self._packet_actions(packet)]},
                ),
            )

            if self._should_trigger_reflection(packet):
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
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._publish_runtime_warning(event, phase="user_turn", exc=exc)
            raise

    async def _run_executor_result_turn(self, event: BusEnvelope[BrainReplyRequestPayload]) -> None:
        try:
            executor_result = event.payload.executor_result
            if executor_result is None:
                return
            task = self._tasks.get(event.task_id or "")
            session_id = event.session_id or ""
            origin = self._origin_for(session_id, task)
            if origin is not None:
                self._session_origins[session_id] = origin
            packet = await self._decide_executor_result(
                event=event,
                executor_result=executor_result,
                task=task,
            )
            reply_text = str(packet.get("final_message", "") or "").strip()
            if not reply_text:
                raise RuntimeError("Brain output requires a non-empty #####user###### section")
            execute_actions = self._execute_actions(packet)
            invoke_executor = bool(execute_actions)
            await self._publish_executor_result_reply_ready(
                event=event,
                executor_result=executor_result,
                reply_text=reply_text,
                reply_kind="status" if invoke_executor else "answer",
                origin_message=origin,
                task=task,
                invoke_executor=invoke_executor,
                executor_requests=self._build_executor_result_requests(
                    event=event,
                    executor_result=executor_result,
                    task=task,
                    origin=origin,
                    actions=execute_actions,
                ),
                metadata=self._merge_reply_metadata(
                    {"actions": [dict(item) for item in self._packet_actions(packet)]},
                    self._executor_result_reply_metadata(executor_result=executor_result, task=task),
                )
                or {},
            )
            if self._should_trigger_reflection(packet):
                await self._publish_reflection(
                    event,
                    reason=self._executor_result_reason(executor_result),
                    metadata=self._executor_result_reflection_metadata(
                        event=event,
                        executor_result=executor_result,
                        task=task,
                        reply_text=reply_text,
                        decision_packet=packet,
                    ),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._publish_runtime_warning(event, phase="executor_result", exc=exc)
            raise

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
                source="brain_runtime",
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

    def _executor_result_reply_metadata(
        self,
        *,
        executor_result: ExecutorResultContextPayload,
        task: object | None,
    ) -> dict[str, Any] | None:
        return self._merge_reply_metadata(
            {
                "brain_source": "executor_result",
                "source_event": executor_result.source_event,
                "source_decision": executor_result.decision,
                "job_id": executor_result.job_id,
            },
            self._delivery_flag(suppress_delivery=self._task_suppress_delivery(task)),
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

    def _executor_delivery_target(
        self,
        *,
        payload: TurnInputPayload | None,
        origin: MessageRef | None,
        task: object | None = None,
        delivery_mode: str | None = None,
    ) -> dict[str, Any]:
        task_target = getattr(task, "delivery_target", None) if task is not None else None
        resolved_delivery_mode = str(delivery_mode or "").strip()
        if resolved_delivery_mode not in {"inline", "push", "stream"}:
            resolved_delivery_mode = "inline" if payload is None else self._current_delivery_mode(payload)
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
                "delivery_mode": resolved_delivery_mode,
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

    def _build_executor_requests(
        self,
        *,
        event: BusEnvelope[TurnInputPayload],
        payload: TurnInputPayload,
        user_text: str,
        actions: list[dict[str, Any]],
        suppress_delivery: bool,
    ) -> list[dict[str, Any]]:
        origin = payload.message
        base_context = self._task_create_context(
            payload=payload,
            user_text=user_text,
            origin=origin,
            suppress_delivery=suppress_delivery,
        )
        requests: list[dict[str, Any]] = []
        for action in actions:
            request = self._build_executor_request_payload(
                action=action,
                source_text=user_text,
                origin=origin,
                runtime_task=self._task_for_action(action),
                delivery_mode=self._current_delivery_mode(payload),
                source_input_mode=self._source_input_mode(payload),
                source_event_id=event.event_id,
                context_base=base_context,
            )
            if request:
                requests.append(request)
        return requests

    def _build_executor_result_requests(
        self,
        *,
        event: BusEnvelope[BrainReplyRequestPayload],
        executor_result: ExecutorResultContextPayload,
        task: object | None,
        origin: MessageRef | None,
        actions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        source_text = self._executor_result_source_text(executor_result=executor_result, task=task)
        base_context = self._executor_result_task_context(
            event=event,
            executor_result=executor_result,
            task=task,
            origin=origin,
        )
        requests: list[dict[str, Any]] = []
        for action in actions:
            request = self._build_executor_request_payload(
                action=action,
                source_text=source_text,
                origin=origin,
                runtime_task=self._task_for_action(action),
                delivery_mode=str(executor_result.delivery_target.delivery_mode or "").strip() or "inline",
                source_input_mode="executor_result",
                source_event_id=event.event_id,
                context_base=base_context,
            )
            if request:
                requests.append(request)
        return requests

    def _build_executor_request_payload(
        self,
        *,
        action: dict[str, Any],
        source_text: str,
        origin: MessageRef | None,
        runtime_task: object | None,
        delivery_mode: str,
        source_input_mode: str,
        source_event_id: str | None,
        context_base: dict[str, Any],
    ) -> dict[str, Any]:
        operation = self._execute_operation(action)
        task_id = self._action_task_id(action)
        request = getattr(runtime_task, "request", None)
        if operation == "run":
            goal = (
                str(action.get("goal", "") or "").strip()
                or str(getattr(request, "goal", "") or "").strip()
                or source_text
            )
            current_checks = self._string_list(action.get("current_checks"))
            mainline = list(action.get("mainline") or getattr(request, "mainline", []) or [])
            current_stage = action.get("current_stage")
            if current_stage in (None, "", []):
                current_stage = getattr(request, "current_stage", None)
            request_text = "\n".join(current_checks) if current_checks else goal
            return {
                "job_id": f"job_{uuid4().hex[:12]}",
                "job_action": "execute",
                "job_kind": "execution_review",
                "source_text": source_text,
                "request_text": request_text,
                "task_id": None if task_id in {"", "new"} else task_id,
                "goal": goal,
                "mainline": mainline,
                "current_stage": current_stage,
                "current_checks": current_checks,
                "delivery_target": self._executor_delivery_target(
                    payload=None,
                    origin=origin,
                    task=runtime_task,
                    delivery_mode=delivery_mode,
                ),
                "scores": {},
                "context": self._merge_reply_metadata(
                    context_base,
                    {
                        "title": goal,
                        "goal": goal,
                        "mainline": mainline,
                        "current_stage": current_stage,
                        "current_checks": current_checks,
                    },
                )
                or {},
            }
        reason = str(action.get("reason", "") or "").strip() or source_text or "user_cancelled"
        return self._compact_dict(
            {
                "job_id": f"job_{uuid4().hex[:12]}",
                "job_action": "cancel",
                "job_kind": "execution_review",
                "task_id": task_id or None,
                "source_text": source_text,
                "request_text": reason,
                "delivery_target": self._executor_delivery_target(
                    payload=None,
                    origin=origin,
                    task=runtime_task,
                    delivery_mode=delivery_mode,
                ),
                "scores": {},
                "context": self._merge_reply_metadata(
                    context_base,
                    {
                        "reason": reason,
                        "source_event_id": source_event_id,
                        "source_input_mode": source_input_mode,
                    },
                )
                or {},
            }
        )

    def _executor_result_task_context(
        self,
        *,
        event: BusEnvelope[BrainReplyRequestPayload],
        executor_result: ExecutorResultContextPayload,
        task: object | None,
        origin: MessageRef | None,
    ) -> dict[str, Any]:
        request = getattr(task, "request", None)
        raw_context = dict(getattr(task, "raw_context", {}) or {}) if task is not None else {}
        content_blocks = list(getattr(request, "content_blocks", []) or [])
        origin_payload = origin.model_dump(exclude_none=True) if origin is not None else None
        return self._compact_dict(
            {
                "history_context": str(getattr(request, "history_context", "") or "").strip() or None,
                "recent_turns": list(raw_context.get("recent_turns", []) or []),
                "short_term_memory": list(raw_context.get("short_term_memory", []) or []),
                "long_term_memory": list(raw_context.get("long_term_memory", []) or []),
                "tool_context": dict(raw_context.get("tool_context", {}) or {}),
                "source_input_mode": "executor_result",
                "current_delivery_mode": str(executor_result.delivery_target.delivery_mode or "").strip() or None,
                "available_delivery_modes": [str(executor_result.delivery_target.delivery_mode or "").strip()]
                if str(executor_result.delivery_target.delivery_mode or "").strip()
                else [],
                "memory_refs": list(getattr(request, "memory_refs", []) or []),
                "skill_hints": list(getattr(request, "skill_hints", []) or []),
                "content_blocks": content_blocks,
                "origin_message": origin_payload,
                "suppress_delivery": True if self._task_suppress_delivery(task) else None,
                "executor_result_source_event": executor_result.source_event,
            }
        )

    def _executor_result_source_text(self, *, executor_result: ExecutorResultContextPayload, task: object | None) -> str:
        request = getattr(task, "request", None)
        return (
            str(getattr(request, "request", "") or "").strip()
            or str(getattr(request, "goal", "") or "").strip()
            or str(executor_result.result_text or executor_result.summary or executor_result.reason or "").strip()
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
        if self._brain_llm is None:
            raise RuntimeError("BrainRuntime requires brain_llm for user-turn decisions")

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
        current_task_id = self._current_task_id(session_id=event.session_id or "")
        return normalize_decision_packet(
            raw_payload,
            current_context={
                "user_input": user_text,
                "history_context": str(event.payload.metadata.get("history_context", "") or "").strip(),
                "current_task_id": current_task_id,
                "source_input_mode": self._source_input_mode(event.payload),
                "current_delivery_mode": self._current_delivery_mode(event.payload),
                "available_delivery_modes": self._available_delivery_modes(event.payload),
            },
        )

    async def _decide_executor_result(
        self,
        *,
        event: BusEnvelope[BrainReplyRequestPayload],
        executor_result: ExecutorResultContextPayload,
        task: object | None,
    ) -> DecisionPacket:
        if self._brain_llm is None:
            raise RuntimeError("BrainRuntime requires brain_llm for executor-result decisions")

        session_id = str(event.session_id or "").strip()
        messages = await self._build_executor_result_messages(
            session_id=session_id,
            event=event,
            executor_result=executor_result,
            task=task,
        )
        response = await self._invoke_model(messages)
        raw_payload = parse_decision_packet(response)
        current_task_id = self._current_task_id(
            session_id=session_id,
            preferred_task_id=str(event.task_id or "").strip(),
        )
        return normalize_decision_packet(
            raw_payload,
            current_context={
                "user_input": self._executor_result_source_text(executor_result=executor_result, task=task),
                "history_context": str(getattr(getattr(task, "request", None), "history_context", "") or "").strip(),
                "current_task_id": current_task_id,
                "source_input_mode": "executor_result",
                "current_delivery_mode": str(executor_result.delivery_target.delivery_mode or "").strip() or "inline",
                "available_delivery_modes": [str(executor_result.delivery_target.delivery_mode or "").strip() or "inline"],
            },
        )

    async def _invoke_model(
        self,
        messages: list[SystemMessage | HumanMessage],
        *,
        on_user_stream: Any | None = None,
    ) -> Any:
        if hasattr(self._brain_llm, "astream"):
            full_text = ""
            streamer = _UserReplyStreamer() if on_user_stream is not None else None
            async for chunk in self._brain_llm.astream(messages):
                text = _chunk_text(chunk)
                if not text:
                    continue
                full_text += text
                if streamer is not None:
                    for item in streamer.feed(full_text):
                        await on_user_stream(item)
            if streamer is not None:
                for item in streamer.finish():
                    await on_user_stream(item)
            return full_text
        if hasattr(self._brain_llm, "ainvoke"):
            return await self._brain_llm.ainvoke(messages)
        if hasattr(self._brain_llm, "invoke"):
            return self._brain_llm.invoke(messages)
        raise RuntimeError("brain_llm does not support invoke/ainvoke")

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
        if self._context_builder is not None and hasattr(self._context_builder, "build_brain_system_prompt"):
            system_prompt = self._context_builder.build_brain_system_prompt(query=user_text)
        elif self._context_builder is not None and hasattr(self._context_builder, "build_brain_decision_system_prompt"):
            system_prompt = self._context_builder.build_brain_decision_system_prompt(query=user_text)
        messages: list[SystemMessage | HumanMessage] = []
        if system_prompt.strip():
            messages.append(SystemMessage(content=system_prompt.strip()))
        messages.append(
            HumanMessage(
                content=await self._prompt_builder.build_user_turn_instruction(
                    session_id=session_id,
                    history_context=history_context,
                    tasks=tasks,
                    user_text=user_text,
                    source_input_mode=source_input_mode,
                    current_delivery_mode=current_delivery_mode,
                    available_delivery_modes=available_delivery_modes,
                )
            )
        )
        return messages

    async def _build_executor_result_messages(
        self,
        *,
        session_id: str,
        event: BusEnvelope[BrainReplyRequestPayload],
        executor_result: ExecutorResultContextPayload,
        task: object | None,
    ) -> list[SystemMessage | HumanMessage]:
        query = (
            self._executor_result_source_text(executor_result=executor_result, task=task)
            or str(executor_result.summary or executor_result.reason or "").strip()
        )
        system_prompt = ""
        if self._context_builder is not None and hasattr(self._context_builder, "build_brain_system_prompt"):
            system_prompt = self._context_builder.build_brain_system_prompt(query=query)
        elif self._context_builder is not None and hasattr(self._context_builder, "build_brain_decision_system_prompt"):
            system_prompt = self._context_builder.build_brain_decision_system_prompt(query=query)
        messages: list[SystemMessage | HumanMessage] = []
        if system_prompt.strip():
            messages.append(SystemMessage(content=system_prompt.strip()))
        history_context = str(getattr(getattr(task, "request", None), "history_context", "") or "").strip()
        tasks = self._tasks.for_session(session_id)
        messages.append(
            HumanMessage(
                content=await self._prompt_builder.build_executor_result_instruction(
                    session_id=session_id,
                    event=event,
                    executor_result=executor_result,
                    task=task,
                    tasks=tasks,
                    history_context=history_context,
                )
            )
        )
        return messages

    async def _publish_brain_stream_delta_ready(
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
                event_type=EventType.BRAIN_EVENT_STREAM_DELTA_READY,
                source="brain_runtime",
                target="broadcast",
                session_id=session_id,
                turn_id=turn_id,
                task_id=related_task_id,
                correlation_id=correlation_id or related_task_id or turn_id,
                causation_id=causation_id,
                payload=BrainStreamDeltaPayload(
                    stream_id=stream_id,
                    delta_text=delta_text,
                    stream_state=stream_state if stream_state in {"open", "delta", "close", "superseded"} else "delta",
                    stream_index=stream_index,
                    origin_message=origin_message,
                    metadata=dict(metadata or {}),
                ),
            )
        )

    async def _publish_brain_reply_ready(
        self,
        *,
        event: BusEnvelope[TurnInputPayload],
        reply_text: str,
        reply_kind: str,
        delivery_target: dict[str, Any],
        origin_message: MessageRef | None,
        invoke_executor: bool,
        executor_requests: list[dict[str, Any]],
        related_task_id: str | None,
        stream_id: str | None = None,
        stream_state: str | None = None,
        stream_index: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._bus.publish(
            build_envelope(
                event_type=EventType.BRAIN_EVENT_REPLY_READY,
                source="brain_runtime",
                target="broadcast",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=related_task_id or event.task_id,
                correlation_id=related_task_id or event.correlation_id or event.turn_id,
                causation_id=event.event_id,
                payload=BrainReplyReadyPayload(
                    request_id=f"brain_reply_{uuid4().hex[:12]}",
                    reply_text=reply_text,
                    reply_kind=reply_kind if reply_kind in {"answer", "status"} else "answer",
                    delivery_target=dict(delivery_target or {}),
                    origin_message=origin_message,
                    invoke_executor=invoke_executor,
                    executor_requests=[dict(item) for item in executor_requests],
                    related_task_id=related_task_id,
                    stream_id=stream_id,
                    stream_state=stream_state if stream_state in {"open", "delta", "close", "superseded"} else None,
                    stream_index=stream_index,
                    metadata=dict(metadata or {}),
                ),
            )
        )

    async def _publish_executor_result_reply_ready(
        self,
        *,
        event: BusEnvelope[BrainReplyRequestPayload],
        executor_result: ExecutorResultContextPayload,
        reply_text: str,
        reply_kind: str,
        origin_message: MessageRef | None,
        task: object | None,
        invoke_executor: bool,
        executor_requests: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._bus.publish(
            build_envelope(
                event_type=EventType.BRAIN_EVENT_REPLY_READY,
                source="brain_runtime",
                target="broadcast",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=event.task_id,
                correlation_id=event.task_id or event.correlation_id or executor_result.job_id,
                causation_id=event.event_id,
                payload=BrainReplyReadyPayload(
                    request_id=f"brain_reply_{uuid4().hex[:12]}",
                    reply_text=reply_text,
                    reply_kind=reply_kind if reply_kind in {"answer", "status"} else "status",
                    delivery_target=executor_result.delivery_target,
                    origin_message=origin_message,
                    invoke_executor=invoke_executor,
                    executor_requests=[dict(item) for item in executor_requests],
                    related_task_id=event.task_id,
                    metadata=dict(metadata or {}),
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
        execute_actions = self._execute_actions(decision_packet)
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
                "brain": dict(decision_packet),
                "task": self._task_snapshot(task, session_id=event.session_id or ""),
                "execution": execution or {},
                "metadata": {
                    "source_event_type": event.event_type,
                    "execute_action_count": len(execute_actions),
                    "execute_actions": [dict(item) for item in execute_actions],
                    "task_snapshots": self._task_snapshots_for_actions(
                        session_id=event.session_id or "",
                        actions=execute_actions,
                    ),
                },
            }
        }

    def _executor_result_reflection_metadata(
        self,
        *,
        event: BusEnvelope[BrainReplyRequestPayload],
        executor_result: ExecutorResultContextPayload,
        task: object,
        reply_text: str,
        decision_packet: DecisionPacket,
    ) -> dict[str, Any]:
        origin = self._origin_for(event.session_id or "", task) or MessageRef()
        user_input = ""
        if task is not None and getattr(task, "request", None) is not None:
            user_input = str(getattr(task, "request").request or "").strip()
        execute_actions = self._execute_actions(decision_packet)
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
                "brain": dict(decision_packet),
                "task": self._task_snapshot(task, session_id=event.session_id or ""),
                "execution": self._execution_from_packet(packet=decision_packet, task=task)
                if self._execute_actions(decision_packet)
                else self._executor_result_execution(executor_result=executor_result, reply_text=reply_text),
                "metadata": {
                    "source_event_type": executor_result.source_event,
                    "decision": executor_result.decision,
                    "related_task_id": event.task_id or "",
                    "execute_action_count": len(execute_actions),
                    "execute_actions": [dict(item) for item in execute_actions],
                    "task_snapshots": self._task_snapshots_for_actions(
                        session_id=event.session_id or "",
                        actions=execute_actions,
                    ),
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
    def _executor_result_reason(executor_result: ExecutorResultContextPayload) -> str:
        if executor_result.source_event == str(EventType.EXECUTOR_EVENT_JOB_REJECTED):
            return "task_rejected"
        outcome = str(executor_result.metadata.get("result", "") or "").strip()
        if outcome == "cancelled":
            return "task_cancelled"
        if outcome == "failed":
            return "task_failed"
        return "task_result"

    def _executor_result_execution(
        self,
        *,
        executor_result: ExecutorResultContextPayload,
        reply_text: str,
    ) -> dict[str, Any]:
        if executor_result.source_event == str(EventType.EXECUTOR_EVENT_JOB_REJECTED):
            return self._execution_payload(
                status="failed",
                summary=str(executor_result.reason or reply_text or "").strip() or "rejected",
                failure_reason=str(executor_result.reason or "rejected").strip(),
            )
        outcome = str(executor_result.metadata.get("result", "") or "").strip()
        if outcome == "cancelled":
            return self._execution_payload(
                status="failed",
                summary=str(executor_result.summary or reply_text or "").strip() or "cancelled",
                failure_reason="task_cancelled",
            )
        if outcome == "failed":
            return self._execution_payload(
                status="failed",
                summary=str(executor_result.summary or reply_text or "").strip() or "failed",
                failure_reason=str(executor_result.result_text or executor_result.summary or "task_failed").strip(),
            )
        return self._execution_payload(
            status="done",
            summary=str(executor_result.summary or reply_text or "").strip() or "done",
        )

    def _task_snapshot(self, task: object, *, session_id: str = "") -> dict[str, Any]:
        if task is None or not hasattr(task, "task_id"):
            return {}
        params = {}
        if getattr(task, "request", None) is not None:
            params = getattr(task, "request").model_dump(exclude_none=True)
        task_id = str(getattr(task, "task_id", "") or "").strip()
        return project_task_from_runtime_snapshot(
            {
                "task_id": task_id,
                "state": str(getattr(getattr(task, "state", None), "value", "") or "").strip(),
                "result": str(getattr(task, "result", "") or "").strip(),
                "state_version": getattr(task, "state_version", 1),
                "title": str(getattr(task, "title", "") or "").strip(),
                "summary": str(getattr(task, "summary", "") or "").strip(),
                "error": str(getattr(task, "error", "") or "").strip(),
                "updated_at": str(getattr(task, "updated_at", "") or "").strip(),
            },
            params=params,
            trace=[dict(item) for item in list(getattr(task, "trace_log", []) or []) if isinstance(item, dict)],
        )

    def _world_current_task_id(self, session_id: str) -> str:
        if self._session_runtime is None or not hasattr(self._session_runtime, "world_model_snapshot"):
            return ""
        try:
            model = self._session_runtime.world_model_snapshot(session_id)
        except Exception:
            return ""
        current_task = getattr(model, "current_task", None)
        if current_task is None:
            return ""
        return str(getattr(current_task, "task_id", "") or "").strip()

    def _current_task_id(
        self,
        *,
        session_id: str,
        preferred_task_id: str = "",
    ) -> str:
        task_id = str(preferred_task_id or "").strip()
        if task_id:
            return task_id
        return self._world_current_task_id(session_id)

    @staticmethod
    def _execution_from_packet(packet: DecisionPacket, *, task: object) -> dict[str, Any]:
        actions = BrainRuntime._execute_actions(packet)
        summary = "主脑完成了当前轮判断。"
        if any(BrainRuntime._execute_operation(action) == "run" for action in actions):
            return BrainRuntime._execution_payload(status="running", summary=summary, invoked=True)
        cancel_action = next(
            (action for action in actions if BrainRuntime._execute_operation(action) == "cancel"),
            None,
        )
        if cancel_action is not None:
            return BrainRuntime._execution_payload(
                status="failed",
                summary=summary,
                invoked=bool(task is not None),
                failure_reason=str(cancel_action.get("reason", "") or "").strip() or "user_cancelled",
            )
        return BrainRuntime._execution_payload(status="none", summary=summary, invoked=False)

    @classmethod
    def _execute_actions(cls, packet: DecisionPacket) -> list[dict[str, Any]]:
        return [
            action
            for action in cls._packet_actions(packet)
            if str(action.get("type", "") or "").strip() == "execute"
        ]

    @staticmethod
    def _packet_actions(packet: DecisionPacket) -> list[dict[str, Any]]:
        value = packet.get("actions")
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]

    @classmethod
    def _primary_execute_action(cls, packet: DecisionPacket) -> dict[str, Any] | None:
        actions = cls._execute_actions(packet)
        return actions[0] if actions else None

    @classmethod
    def _should_trigger_reflection(cls, packet: DecisionPacket) -> bool:
        for action in cls._packet_actions(packet):
            if str(action.get("type", "") or "").strip() == "reflect":
                return True
        return False

    @staticmethod
    def _execute_operation(action: dict[str, Any] | None) -> str:
        if not isinstance(action, dict):
            return "run"
        operation = str(action.get("operation", "") or "run").strip()
        return operation if operation in {"run", "cancel"} else "run"

    @staticmethod
    def _action_task_id(action: dict[str, Any] | None) -> str | None:
        if not isinstance(action, dict):
            return None
        task_id = str(action.get("task_id", "") or "").strip()
        if not task_id or task_id == "new":
            return None
        return task_id

    @classmethod
    def _related_task_id_from_actions(cls, packet: DecisionPacket) -> str | None:
        for action in cls._execute_actions(packet):
            task_id = cls._action_task_id(action)
            if task_id:
                return task_id
        return None

    def _task_for_action(self, action: dict[str, Any] | None) -> object | None:
        task_id = self._action_task_id(action)
        if task_id:
            return self._tasks.get(task_id)
        return None

    def _reflection_task_from_actions(self, actions: list[dict[str, Any]]) -> object | None:
        for action in actions:
            task = self._task_for_action(action)
            if task is not None:
                return task
        return None

    def _task_snapshots_for_actions(
        self,
        *,
        session_id: str,
        actions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        snapshots: list[dict[str, Any]] = []
        for action in actions:
            snapshot = self._task_snapshot_from_action(session_id=session_id, action=action)
            if snapshot:
                snapshots.append(snapshot)
        return snapshots

    def _task_snapshot_from_action(self, *, session_id: str, action: dict[str, Any]) -> dict[str, Any]:
        task = self._task_for_action(action)
        if task is not None:
            return self._task_snapshot(task, session_id=session_id)

        operation = self._execute_operation(action)
        task_id = str(action.get("task_id", "") or "").strip()
        if operation == "cancel":
            return {
                "task_id": task_id,
                "state": "cancelled",
                "summary": str(action.get("reason", "") or "").strip(),
            }

        goal = str(action.get("goal", "") or "").strip()
        current_checks = [str(item).strip() for item in list(action.get("current_checks", []) or []) if str(item).strip()]
        snapshot = {
            "task_id": task_id or "new",
            "goal": goal,
            "mainline": list(action.get("mainline") or []),
            "current_stage": action.get("current_stage"),
            "current_checks": current_checks,
            "summary": goal or "planned_execute",
        }
        return {key: value for key, value in snapshot.items() if value not in ("", None, [], {})}

    async def _publish_runtime_warning(
        self,
        event: BusEnvelope[object],
        *,
        phase: str,
        exc: Exception,
    ) -> None:
        error_message = str(exc or "").strip() or exc.__class__.__name__
        await self._bus.publish(
            build_envelope(
                event_type=EventType.SYSTEM_WARNING,
                source="brain_runtime",
                target="broadcast",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=event.task_id,
                correlation_id=event.correlation_id or event.task_id or event.turn_id,
                causation_id=event.event_id,
                payload=SystemSignalPayload(
                    signal_id=f"signal_{uuid4().hex[:12]}",
                    signal_type="warning",
                    reason="brain_decision_failed",
                    related_event_id=event.event_id,
                    related_task_id=event.task_id,
                    severity="error",
                    metadata={
                        "component": "brain_runtime",
                        "phase": phase,
                        "error": error_message,
                    },
                ),
            )
        )


__all__ = ["BrainRuntime"]
