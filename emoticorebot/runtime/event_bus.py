"""Current-process runtime event bus primitives."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class InboundMessage:
    """Message received from a chat channel."""

    channel: str
    sender_id: str
    chat_id: str
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    session_key_override: str | None = None

    @property
    def session_key(self) -> str:
        """Unique key for runtime session routing."""
        return self.session_key_override or f"{self.channel}:{self.chat_id}"


@dataclass
class OutboundMessage:
    """Message to send through a chat channel."""

    channel: str
    chat_id: str
    content: str
    reply_to: str | None = None
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskSignal:
    """In-process staged task signal emitted by internal execution."""

    session_id: str
    message_id: str = ""
    task_id: str = ""
    event: str = "task.progress"
    content: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


class RuntimeEventBus:
    """Async bus for routing runtime ingress and egress messages."""

    def __init__(self):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        self._task_signal_subscribers: set[asyncio.Queue[TaskSignal]] = set()

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish an inbound message from a channel."""
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message."""
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish an outbound message for a channel."""
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message."""
        return await self.outbound.get()

    async def publish_task_signal(self, signal: TaskSignal) -> None:
        """Fan out a staged task signal to current in-process subscribers."""
        for queue in list(self._task_signal_subscribers):
            await queue.put(signal)

    def subscribe_task_signals(self) -> asyncio.Queue[TaskSignal]:
        """Subscribe to staged task signals for the current process."""
        queue: asyncio.Queue[TaskSignal] = asyncio.Queue()
        self._task_signal_subscribers.add(queue)
        return queue

    def unsubscribe_task_signals(self, queue: asyncio.Queue[TaskSignal]) -> None:
        """Remove a staged task signal subscriber queue."""
        self._task_signal_subscribers.discard(queue)

    @property
    def inbound_size(self) -> int:
        """Number of pending inbound messages."""
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """Number of pending outbound messages."""
        return self.outbound.qsize()

    @property
    def task_signal_subscriber_count(self) -> int:
        """Number of active staged task signal subscribers."""
        return len(self._task_signal_subscribers)


__all__ = ["RuntimeEventBus", "InboundMessage", "OutboundMessage", "TaskSignal"]
