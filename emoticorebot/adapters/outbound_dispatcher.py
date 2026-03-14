"""Outbound message delivery adapter."""

from __future__ import annotations

from emoticorebot.runtime.event_bus import OutboundMessage, RuntimeEventBus


class OutboundDispatcher:
    """Single place that publishes outbound messages to the runtime bus."""

    def __init__(self, bus: RuntimeEventBus):
        self.bus = bus

    async def publish(self, message: OutboundMessage) -> None:
        await self.bus.publish_outbound(message)


__all__ = ["OutboundDispatcher"]
