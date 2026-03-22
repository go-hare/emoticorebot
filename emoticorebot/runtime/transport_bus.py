"""Lightweight transport bus for channel integrations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class InboundMessage:
    channel: str
    sender_id: str
    chat_id: str
    content: str
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    session_key_override: str | None = None


@dataclass(slots=True)
class OutboundMessage:
    channel: str
    chat_id: str
    content: str
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class TransportBus:
    """Simple async queues for inbound and outbound channel messages."""

    def __init__(self) -> None:
        self.inbound_queue: Any = asyncio.Queue()
        self.outbound_queue: Any = asyncio.Queue()

    async def publish_inbound(self, message: InboundMessage) -> None:
        await self.inbound_queue.put(message)

    async def consume_inbound(self) -> InboundMessage:
        return await self.inbound_queue.get()

    async def publish_outbound(self, message: OutboundMessage) -> None:
        await self.outbound_queue.put(message)

    async def consume_outbound(self) -> OutboundMessage:
        return await self.outbound_queue.get()
