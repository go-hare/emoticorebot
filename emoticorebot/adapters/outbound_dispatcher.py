"""Outbound message delivery adapter."""

from __future__ import annotations

from emoticorebot.runtime.transport_bus import OutboundMessage, TransportBus


class OutboundDispatcher:
    """Single place that publishes outbound messages to the transport bus."""

    def __init__(self, bus: TransportBus):
        self.bus = bus

    async def publish(self, message: OutboundMessage) -> None:
        await self.bus.publish_outbound(message)


__all__ = ["OutboundDispatcher"]
