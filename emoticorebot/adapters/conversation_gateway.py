"""Inbound conversation entrypoint and dispatch coordination."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from loguru import logger

from emoticorebot.runtime.transport_bus import InboundMessage, OutboundMessage, TransportBus

MessageProcessor = Callable[
    [InboundMessage, str | None, Callable[..., Awaitable[None]] | None],
    Awaitable[OutboundMessage | None],
]


class ConversationGateway:
    """Handles inbound dispatch with per-session preemption and direct processing."""

    def __init__(
        self,
        *,
        bus: TransportBus,
        message_processor: MessageProcessor,
    ):
        self.bus = bus
        self._message_processor = message_processor
        self._dispatch_tasks: set[asyncio.Task] = set()
        self._active_dispatch_by_session: dict[str, asyncio.Task[None]] = {}

    async def run_forever(self, should_continue: Callable[[], bool], idle_timeout: float = 1.0) -> None:
        while should_continue():
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=idle_timeout)
            except asyncio.TimeoutError:
                continue
            self.spawn_dispatch(msg)

    def spawn_dispatch(self, msg: InboundMessage) -> None:
        active_task = self._active_dispatch_by_session.get(msg.session_key)
        if active_task is not None and not active_task.done():
            active_task.cancel()
        task = asyncio.create_task(self.dispatch(msg), name=f"conversation:{msg.session_key}")
        self._active_dispatch_by_session[msg.session_key] = task
        self._dispatch_tasks.add(task)
        task.add_done_callback(lambda finished, session_key=msg.session_key: self._on_dispatch_done(session_key, finished))

    async def dispatch(self, msg: InboundMessage) -> None:
        await self._message_processor(msg, None, None)

    def _on_dispatch_done(self, session_key: str, task: asyncio.Task) -> None:
        self._dispatch_tasks.discard(task)
        if self._active_dispatch_by_session.get(session_key) is task:
            self._active_dispatch_by_session.pop(session_key, None)
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
        self._active_dispatch_by_session.clear()


__all__ = ["ConversationGateway"]
