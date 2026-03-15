"""Reply command builders for the executive brain."""

from __future__ import annotations

from uuid import uuid4

from emoticorebot.protocol.commands import BrainReplyPayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.task_models import MessageRef, ReplyDraft, ReplyKind
from emoticorebot.protocol.topics import EventType


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class ReplyBuilder:
    """Creates brain reply and ask-user commands with stable protocol fields."""

    def build(
        self,
        *,
        event_type: str,
        session_id: str,
        turn_id: str | None,
        text: str,
        origin_message: MessageRef | None,
        related_task_id: str | None = None,
        causation_id: str | None = None,
        correlation_id: str | None = None,
        kind: ReplyKind = "answer",
        safe_fallback: bool = False,
    ) -> BusEnvelope[BrainReplyPayload]:
        reply = ReplyDraft(
            reply_id=_new_id("reply"),
            kind=kind,
            plain_text=text,
            safe_fallback=safe_fallback,
            reply_to_message_id=(origin_message.message_id if origin_message is not None else None),
        )
        return build_envelope(
            event_type=event_type,
            source="brain",
            target="runtime",
            session_id=session_id,
            turn_id=turn_id,
            task_id=related_task_id,
            correlation_id=correlation_id or related_task_id or turn_id,
            causation_id=causation_id,
            payload=BrainReplyPayload(
                command_id=_new_id("cmd"),
                reply=reply,
                related_task_id=related_task_id,
                origin_message=origin_message,
            ),
        )

    def reply(self, **kwargs: object) -> BusEnvelope[BrainReplyPayload]:
        return self.build(event_type=EventType.BRAIN_REPLY, **kwargs)

    def ask_user(self, **kwargs: object) -> BusEnvelope[BrainReplyPayload]:
        return self.build(event_type=EventType.BRAIN_ASK_USER, kind="ask_user", **kwargs)


__all__ = ["ReplyBuilder"]
