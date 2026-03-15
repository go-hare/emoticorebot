"""Inbound conversation entrypoint and dispatch coordination."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from loguru import logger

from emoticorebot.adapters.outbound_dispatcher import OutboundDispatcher
from emoticorebot.runtime.transport_bus import InboundMessage, OutboundMessage, TransportBus

MessageProcessor = Callable[
    [InboundMessage, str | None, Callable[..., Awaitable[None]] | None],
    Awaitable[OutboundMessage | None],
]


class ConversationGateway:
    """Handles inbound dispatch, session locking, and direct processing."""

    def __init__(
        self,
        *,
        bus: TransportBus,
        dispatcher: OutboundDispatcher,
        message_processor: MessageProcessor,
    ):
        self.bus = bus
        self.dispatcher = dispatcher
        self._message_processor = message_processor
        self._dispatch_tasks: set[asyncio.Task] = set()
        self._session_locks: dict[str, asyncio.Lock] = {}

    async def run_forever(self, should_continue: Callable[[], bool], idle_timeout: float = 1.0) -> None:
        while should_continue():
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=idle_timeout)
            except asyncio.TimeoutError:
                continue
            self.spawn_dispatch(msg)

    def spawn_dispatch(self, msg: InboundMessage) -> None:
        task = asyncio.create_task(self.dispatch(msg), name=f"conversation:{msg.session_key}")
        self._dispatch_tasks.add(task)
        task.add_done_callback(self._on_dispatch_done)

    async def dispatch(self, msg: InboundMessage) -> None:
        lock = self._session_locks.setdefault(msg.session_key, asyncio.Lock())
        async with lock:
            await self._message_processor(msg, None, None)

    def _on_dispatch_done(self, task: asyncio.Task) -> None:
        self._dispatch_tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.exception("Conversation dispatch failed: {}", exc)

    async def process_direct(
        self,
        msg: InboundMessage,
        *,
        session_key: str | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        return await self._message_processor(msg, session_key, on_progress)

    def stop(self) -> None:
        for task in list(self._dispatch_tasks):
            task.cancel()


__all__ = ["ConversationGateway"]
