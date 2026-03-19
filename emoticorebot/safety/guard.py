"""Synchronous reply/task output safety filtering for the left/task pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from emoticorebot.protocol.envelope import BusEnvelope
from emoticorebot.protocol.events import OutputReadyPayloadBase, ReplyBlockedPayload
from emoticorebot.protocol.task_models import ContentBlock, ReplyDraft
from emoticorebot.protocol.topics import EventType


@dataclass(slots=True)
class ReplyGuardResult:
    decision: str
    event: BusEnvelope[OutputReadyPayloadBase] | None = None
    blocked: ReplyBlockedPayload | None = None


class SafetyGuard:
    """Applies a small set of secret-leak rules on user-visible output."""

    _READY_EVENT_TYPES = {
        str(EventType.OUTPUT_INLINE_READY),
        str(EventType.OUTPUT_PUSH_READY),
        str(EventType.OUTPUT_STREAM_OPEN),
        str(EventType.OUTPUT_STREAM_DELTA),
        str(EventType.OUTPUT_STREAM_CLOSE),
    }

    _BLOCK_PATTERNS = [
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ]
    _REDACT_PATTERNS = [
        re.compile(r"(?i)(api[_ -]?key\s*[:=]\s*)([^\s,;]+)"),
        re.compile(r"(?i)(password\s*[:=]\s*)([^\s,;]+)"),
        re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    ]

    def guard_reply_event(self, event: BusEnvelope[OutputReadyPayloadBase]) -> ReplyGuardResult:
        if str(event.event_type) not in self._READY_EVENT_TYPES:
            return ReplyGuardResult(decision="allow", event=event)

        reply = event.payload.content
        text = self._reply_surface_text(reply)
        if event.payload.content.safe_fallback:
            if self._contains_secret(text):
                return ReplyGuardResult(
                    decision="block",
                    blocked=ReplyBlockedPayload(
                        reply=reply,
                        block_reason="safe_fallback_rejected",
                        policy_name="secret_filter",
                        redaction_hint="请移除密钥、密码或私钥后再试。",
                    ),
                )
            return ReplyGuardResult(decision="allow", event=event)

        if self._matches_block(text):
            return ReplyGuardResult(
                decision="block",
                blocked=ReplyBlockedPayload(
                    reply=event.payload.content,
                    block_reason="sensitive_material",
                    policy_name="secret_filter",
                    redaction_hint="请移除密钥、密码或私钥后再试。",
                ),
            )

        if self._contains_secret(text):
            reply = self._redact_reply(reply)
            return ReplyGuardResult(
                decision="redact",
                event=event.model_copy(update={"payload": event.payload.model_copy(update={"content": reply})}),
            )

        return ReplyGuardResult(decision="allow", event=event)

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

__all__ = ["ReplyGuardResult", "SafetyGuard"]
