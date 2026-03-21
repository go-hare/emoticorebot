"""Mutable stream/reply state transitions for SessionContext."""

from __future__ import annotations

from typing import Any, Mapping

from emoticorebot.protocol.envelope import BusEnvelope
from emoticorebot.protocol.events import (
    OutputReadyPayloadBase,
    StreamChunkPayload,
    StreamCommitPayload,
    StreamInterruptedPayload,
    StreamStartPayload,
    TurnInputPayload,
    InputSlots,
)

from .models import SessionContext


class SessionStreamRuntime:
    """Owns input-stream, reply-stream, and archive state transitions."""

    def apply_user_turn(self, *, context: SessionContext, event: BusEnvelope[TurnInputPayload]) -> str:
        user_text = self.user_text(event.payload)
        context.last_turn_id = event.turn_id
        context.channel_kind = self.channel_kind(event.payload)
        if self._should_supersede_active_reply(event.payload):
            context.active_reply_stream_id = None
        context.last_user_input = user_text
        if event.turn_id:
            context.last_brain_instance_id = f"brain_{event.turn_id}"
        context.archived = self.should_archive(context)
        return user_text

    def apply_input_stream_started(
        self,
        *,
        context: SessionContext,
        event: BusEnvelope[StreamStartPayload],
    ) -> None:
        if event.payload.stream_id in context.interrupted_input_stream_ids:
            context.archived = self.should_archive(context)
            return
        payload_metadata = dict(event.payload.metadata or {})
        payload_metadata.setdefault("session_mode", event.payload.session_mode)
        context.active_input_stream_id = event.payload.stream_id
        context.active_input_stream_message = event.payload.message.model_copy(deep=True)
        context.active_input_stream_metadata = payload_metadata
        context.active_input_stream_text = ""
        context.input_stream_commit_count = 0
        context.channel_kind = self._stream_channel_kind(payload_metadata, fallback=context.channel_kind or "voice")
        if self._should_supersede_active_reply_from_stream():
            context.active_reply_stream_id = None
        context.archived = self.should_archive(context)

    def apply_input_stream_chunk(
        self,
        *,
        context: SessionContext,
        event: BusEnvelope[StreamChunkPayload],
    ) -> None:
        if event.payload.stream_id in context.interrupted_input_stream_ids:
            return
        if context.active_input_stream_id != event.payload.stream_id:
            return
        context.active_input_stream_text += str(event.payload.chunk_text or "")
        context.archived = self.should_archive(context)

    def build_committed_turn(
        self,
        *,
        context: SessionContext,
        event: BusEnvelope[StreamCommitPayload],
    ) -> tuple[str, TurnInputPayload] | None:
        if event.payload.stream_id in context.interrupted_input_stream_ids:
            return None
        if context.active_input_stream_id != event.payload.stream_id:
            return None
        if context.active_input_stream_message is None:
            return None

        commit_index = context.input_stream_commit_count + 1
        committed_text = str(event.payload.committed_text or "").strip() or str(context.active_input_stream_text or "").strip()
        if not committed_text:
            return None

        stream_metadata = dict(context.active_input_stream_metadata or {})
        commit_metadata = dict(event.payload.metadata or {})
        merged_metadata = dict(stream_metadata)
        merged_metadata.update(commit_metadata)

        channel_kind = self._stream_channel_kind(merged_metadata, fallback=context.channel_kind or "voice")
        input_kind = self._stream_input_kind(merged_metadata)
        session_mode = self._stream_session_mode(merged_metadata, fallback_channel_kind=channel_kind)
        barge_in = bool(merged_metadata.get("barge_in", False))
        turn_id = str(event.turn_id or "").strip() or f"turn_stream_{event.payload.stream_id}_{commit_index}"

        turn_metadata = dict(merged_metadata)
        turn_metadata.update(
            {
                "source_input_mode": "stream",
                "source_stream_id": event.payload.stream_id,
                "stream_commit": True,
                "stream_commit_index": commit_index,
            }
        )

        context.input_stream_commit_count = commit_index
        context.active_input_stream_text = ""
        context.archived = self.should_archive(context)

        payload = TurnInputPayload(
            input_id=turn_id,
            input_mode="turn",
            session_mode=session_mode,
            channel_kind=channel_kind,
            input_kind=input_kind,
            barge_in=barge_in,
            message=context.active_input_stream_message.model_copy(deep=True),
            user_text=committed_text,
            input_slots=InputSlots(),
            metadata=turn_metadata,
        )
        return turn_id, payload

    def apply_input_stream_interrupted(
        self,
        *,
        context: SessionContext,
        event: BusEnvelope[StreamInterruptedPayload],
    ) -> None:
        context.interrupted_input_stream_ids.add(event.payload.stream_id)
        if context.active_input_stream_id != event.payload.stream_id:
            context.archived = self.should_archive(context)
            return
        self._clear_active_input_stream(context)
        context.archived = self.should_archive(context)

    def apply_reply_output(self, *, context: SessionContext, payload: OutputReadyPayloadBase) -> None:
        stream_state = self.reply_stream_state(payload)
        stream_id = str(getattr(payload, "stream_id", "") or "").strip() or payload.content.reply_id

        if stream_state in {"open", "delta"}:
            context.active_reply_stream_id = stream_id
        elif stream_state in {"close", "superseded", "final"}:
            if context.active_reply_stream_id in {None, "", stream_id}:
                context.active_reply_stream_id = None

        if stream_state in {"open", "delta"}:
            context.archived = self.should_archive(context)
            return

        text = self.reply_text(payload)
        if not text:
            context.archived = self.should_archive(context)
            return
        context.last_assistant_output = text
        context.session_summary = text
        context.archived = self.should_archive(context)

    @staticmethod
    def should_archive(context: SessionContext) -> bool:
        return (
            not context.active_reply_stream_id
            and not context.active_input_stream_id
        )

    @staticmethod
    def reply_text(payload: OutputReadyPayloadBase) -> str:
        if payload.content.plain_text:
            return str(payload.content.plain_text).strip()
        parts = [block.text for block in payload.content.content_blocks if block.type == "text" and block.text]
        return "\n".join(str(part).strip() for part in parts if str(part).strip()).strip()

    @staticmethod
    def reply_stream_state(payload: OutputReadyPayloadBase) -> str:
        return str(getattr(payload, "stream_state", "") or "").strip()

    @staticmethod
    def user_text(payload: TurnInputPayload) -> str:
        if payload.user_text:
            return str(payload.user_text).strip()
        if payload.input_slots.user:
            return str(payload.input_slots.user).strip()
        parts = [block.text for block in payload.content_blocks if block.type == "text" and block.text]
        return "\n".join(str(part).strip() for part in parts if str(part).strip()).strip()

    @staticmethod
    def channel_kind(payload: TurnInputPayload) -> str:
        return str(getattr(payload, "channel_kind", "") or "chat").strip() or "chat"

    @staticmethod
    def _stream_channel_kind(metadata: Mapping[str, Any] | None, *, fallback: str) -> str:
        value = str((metadata or {}).get("channel_kind", "") or "").strip()
        if value in {"chat", "voice", "video"}:
            return value
        return fallback or "voice"

    @staticmethod
    def _stream_input_kind(metadata: Mapping[str, Any] | None) -> str:
        value = str((metadata or {}).get("input_kind", "") or "").strip()
        return value if value in {"text", "voice", "multimodal"} else "voice"

    @classmethod
    def _stream_session_mode(cls, metadata: Mapping[str, Any] | None, *, fallback_channel_kind: str) -> str:
        value = str((metadata or {}).get("session_mode", "") or "").strip()
        if value in {"turn_chat", "realtime_chat"}:
            return value
        return "turn_chat" if fallback_channel_kind == "chat" else "realtime_chat"

    @staticmethod
    def _should_supersede_active_reply(payload: TurnInputPayload) -> bool:
        barge_in = bool(getattr(payload, "barge_in", False))
        if barge_in:
            return True
        return True

    @staticmethod
    def _should_supersede_active_reply_from_stream() -> bool:
        return True

    @staticmethod
    def _clear_active_input_stream(context: SessionContext) -> None:
        context.active_input_stream_id = None
        context.active_input_stream_message = None
        context.active_input_stream_metadata = {}
        context.active_input_stream_text = ""
        context.input_stream_commit_count = 0
