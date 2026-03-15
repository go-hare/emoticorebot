"""Subscription primitives for the priority pub/sub bus."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from emoticorebot.protocol.envelope import BusEnvelope
from emoticorebot.protocol.task_models import ProtocolModel

SubscriberHandler = Callable[[BusEnvelope[ProtocolModel]], Awaitable[None]]


@dataclass(slots=True, frozen=True)
class Subscription:
    consumer: str
    handler: SubscriberHandler
    topic: str | None = None
    event_type: str | None = None

    def matches(self, event: BusEnvelope[ProtocolModel]) -> bool:
        if self.topic is not None and event.topic != self.topic:
            return False
        if self.event_type is not None and event.event_type != self.event_type:
            return False
        return event.target in {"broadcast", self.consumer}


__all__ = ["SubscriberHandler", "Subscription"]
