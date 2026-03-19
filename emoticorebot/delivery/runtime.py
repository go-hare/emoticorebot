"""Delivery runtime around the transport delivery service."""

from __future__ import annotations

from typing import Callable

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.delivery.service import DeliveryService
from emoticorebot.protocol.envelope import BusEnvelope
from emoticorebot.protocol.events import OutputReadyPayloadBase
from emoticorebot.runtime.transport_bus import TransportBus


class DeliveryRuntime:
    """Owns outbound reply delivery for approved and redacted replies."""

    def __init__(
        self,
        *,
        bus: PriorityPubSubBus,
        transport: TransportBus | None = None,
        should_deliver: Callable[[BusEnvelope[OutputReadyPayloadBase]], bool] | None = None,
    ) -> None:
        self._service = DeliveryService(bus=bus, transport=transport, should_deliver=should_deliver)

    def register(self) -> None:
        self._service.register()

    @property
    def service(self) -> DeliveryService:
        return self._service


__all__ = ["DeliveryRuntime"]
