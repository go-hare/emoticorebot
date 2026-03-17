"""Safety interceptor layer for replies and task outputs."""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from emoticorebot.bus.interceptor import InterceptorOutcome, allow, block, redact
from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import ReplyBlockedPayload, SystemSignalPayload, TaskEndPayload
from emoticorebot.protocol.safety_models import SafetyAuditPayload
from emoticorebot.protocol.task_models import ContentBlock, ProtocolModel, ReplyDraft
from emoticorebot.protocol.topics import EventType, Topic


class SafetyGuard:
    """Applies a small set of secret-leak rules on user-visible output."""

    _BLOCK_PATTERNS = [
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ]
    _REDACT_PATTERNS = [
        re.compile(r"(?i)(api[_ -]?key\s*[:=]\s*)([^\s,;]+)"),
        re.compile(r"(?i)(password\s*[:=]\s*)([^\s,;]+)"),
        re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    ]

    def __init__(self, *, bus: PriorityPubSubBus) -> None:
        self._bus = bus

    def register(self) -> None:
        self._bus.register_interceptor(topic=Topic.OUTPUT_EVENT, handler=self._intercept_output)
        self._bus.register_interceptor(topic=Topic.TASK_EVENT, handler=self._intercept_task_event)
        self._bus.subscribe(consumer="safety", topic=Topic.CONTROL_COMMAND, handler=self._observe_control)

    async def _intercept_output(self, outcome: InterceptorOutcome) -> InterceptorOutcome:
        event = outcome.event
        if event.event_type != EventType.OUTPUT_REPLY_READY:
            return outcome

        reply = event.payload.reply
        text = self._reply_surface_text(reply)
        if event.payload.reply.safe_fallback:
            if self._contains_secret(text):
                return block(event, self._warning_event(event, reason="safe_fallback_rejected"))
            return allow(self._rewrite_reply_event(event, EventType.OUTPUT_REPLY_APPROVED), self._audit(event, "allowed"))

        if self._matches_block(text):
            blocked = self._rewrite_reply_event(
                event,
                EventType.OUTPUT_REPLY_BLOCKED,
                payload=ReplyBlockedPayload(
                    reply=event.payload.reply,
                    block_reason="sensitive_material",
                    policy_name="secret_filter",
                    redaction_hint="请移除密钥、密码或私钥后再试。",
                ),
            )
            return allow(blocked, self._audit(event, "blocked"))

        if self._contains_secret(text):
            reply = self._redact_reply(reply)
            redacted_event = self._rewrite_reply_event(
                event,
                EventType.OUTPUT_REPLY_REDACTED,
                payload=event.payload.model_copy(update={"reply": reply}),
            )
            return redact(redacted_event, self._audit(event, "redacted"))

        approved = self._rewrite_reply_event(event, EventType.OUTPUT_REPLY_APPROVED)
        return allow(approved, self._audit(event, "allowed"))

    async def _intercept_task_event(self, outcome: InterceptorOutcome) -> InterceptorOutcome:
        event = outcome.event
        if event.event_type != EventType.TASK_END:
            return outcome

        payload = event.payload
        text = self._task_surface_text(payload)

        if not self._contains_secret(text):
            return allow(event, self._audit(event, "allowed"))

        updated_payload = self._redact_task_payload(payload)
        return redact(event.model_copy(update={"payload": updated_payload}), self._audit(event, "redacted"))

    @staticmethod
    async def _observe_control(_event: BusEnvelope[ProtocolModel]) -> None:
        return None

    def _audit(self, event: BusEnvelope[ProtocolModel], decision: str) -> BusEnvelope[SafetyAuditPayload]:
        event_type = {
            "allowed": EventType.SAFETY_ALLOWED,
            "redacted": EventType.SAFETY_REDACTED,
            "blocked": EventType.SAFETY_BLOCKED,
        }[decision]
        return build_envelope(
            event_type=event_type,
            source="safety",
            target="broadcast",
            session_id=event.session_id,
            turn_id=event.turn_id,
            task_id=event.task_id,
            correlation_id=event.correlation_id,
            causation_id=event.event_id,
            payload=SafetyAuditPayload(
                decision_id=f"safety_{uuid4().hex[:12]}",
                decision=decision,
                intercepted_event_type=event.event_type,
                policy_name="secret_filter",
            ),
        )

    def _warning_event(self, event: BusEnvelope[ProtocolModel], *, reason: str) -> BusEnvelope[SystemSignalPayload]:
        return build_envelope(
            event_type=EventType.SYSTEM_WARNING,
            source="safety",
            target="broadcast",
            session_id=event.session_id,
            turn_id=event.turn_id,
            task_id=event.task_id,
            correlation_id=event.correlation_id,
            causation_id=event.event_id,
            payload=SystemSignalPayload(
                signal_id=f"signal_{uuid4().hex[:12]}",
                signal_type="warning",
                reason=reason,
                related_event_id=event.event_id,
                related_task_id=event.task_id,
                severity="warning",
            ),
        )

    @staticmethod
    def _rewrite_reply_event(
        event: BusEnvelope[ProtocolModel],
        event_type: str,
        *,
        payload: ProtocolModel | None = None,
    ) -> BusEnvelope[ProtocolModel]:
        return build_envelope(
            event_type=event_type,
            source="safety",
            target="broadcast",
            session_id=event.session_id,
            turn_id=event.turn_id,
            task_id=event.task_id,
            correlation_id=event.correlation_id,
            causation_id=event.event_id,
            payload=payload or event.payload,
        )

    def _matches_block(self, text: str) -> bool:
        return any(pattern.search(text) for pattern in self._BLOCK_PATTERNS)

    def _contains_secret(self, text: str) -> bool:
        return self._matches_block(text) or any(pattern.search(text) for pattern in self._REDACT_PATTERNS)

    def _redact_text(self, text: str) -> str:
        redacted = text
        for pattern in self._REDACT_PATTERNS:
            if pattern.groups >= 2:
                redacted = pattern.sub(r"\1[REDACTED]", redacted)
            else:
                redacted = pattern.sub("[REDACTED]", redacted)
        return redacted

    def _task_surface_text(self, payload: ProtocolModel) -> str:
        parts: list[str] = []
        for field in ("output", "error"):
            value = getattr(payload, field, None)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        return "\n".join(part for part in parts if part)

    def _redact_task_payload(self, payload: ProtocolModel) -> ProtocolModel:
        updates: dict[str, Any] = {}
        for field in ("output", "error"):
            value = getattr(payload, field, None)
            if isinstance(value, str) and value:
                updates[field] = self._redact_text(value)
        return payload.model_copy(update=updates)

    def _reply_surface_text(self, reply: ReplyDraft) -> str:
        parts: list[str] = []
        if reply.plain_text:
            parts.append(reply.plain_text.strip())
        parts.extend(self._content_block_text(block) for block in reply.content_blocks)
        return "\n".join(part for part in parts if part)

    def _redact_reply(self, reply: ReplyDraft) -> ReplyDraft:
        updates: dict[str, Any] = {}
        if reply.plain_text:
            updates["plain_text"] = self._redact_text(reply.plain_text)
        if reply.content_blocks:
            updates["content_blocks"] = [self._redact_content_block(block) for block in reply.content_blocks]
        if not updates:
            return reply
        return reply.model_copy(update=updates)

    def _content_block_text(self, block: ContentBlock) -> str:
        parts: list[str] = []
        for field in ("text", "url", "path", "name"):
            value = getattr(block, field, None)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        metadata_text = self._flatten_text_values(block.metadata)
        if metadata_text:
            parts.append(metadata_text)
        return "\n".join(parts)

    def _redact_content_block(self, block: ContentBlock) -> ContentBlock:
        updates: dict[str, Any] = {}
        for field in ("text", "url", "path", "name"):
            value = getattr(block, field, None)
            if isinstance(value, str) and value:
                updates[field] = self._redact_text(value)
        metadata = self._redact_value(block.metadata)
        if metadata != block.metadata:
            updates["metadata"] = metadata
        if not updates:
            return block
        return block.model_copy(update=updates)

    def _flatten_text_values(self, value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            return "\n".join(filter(None, (self._flatten_text_values(item) for item in value.values())))
        if isinstance(value, list):
            return "\n".join(filter(None, (self._flatten_text_values(item) for item in value)))
        return ""

    def _redact_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._redact_text(value)
        if isinstance(value, dict):
            return {key: self._redact_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact_value(item) for item in value]
        return value


__all__ = ["SafetyGuard"]
