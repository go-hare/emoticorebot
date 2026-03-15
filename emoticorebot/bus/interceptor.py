"""Interceptor chain support for the priority pub/sub bus."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Awaitable, Callable

from emoticorebot.protocol.envelope import BusEnvelope
from emoticorebot.protocol.task_models import ProtocolModel

InterceptorHandler = Callable[["InterceptorOutcome"], Awaitable["InterceptorOutcome"]]


class InterceptorAction(StrEnum):
    ALLOW = "allow"
    REDACT = "redact"
    BLOCK = "block"


@dataclass(slots=True)
class InterceptorOutcome:
    action: InterceptorAction
    event: BusEnvelope[ProtocolModel]
    audit_events: list[BusEnvelope[ProtocolModel]] = field(default_factory=list)


@dataclass(slots=True)
class _InterceptorEntry:
    order: int
    handler: InterceptorHandler


class InterceptorChain:
    """Serially applies interceptors for a given topic."""

    def __init__(self) -> None:
        self._by_topic: dict[str, list[_InterceptorEntry]] = {}

    def register(self, *, topic: str, handler: InterceptorHandler, order: int = 100) -> None:
        entries = self._by_topic.setdefault(topic, [])
        entries.append(_InterceptorEntry(order=order, handler=handler))
        entries.sort(key=lambda entry: entry.order)

    async def run(self, event: BusEnvelope[ProtocolModel]) -> InterceptorOutcome:
        outcome = InterceptorOutcome(action=InterceptorAction.ALLOW, event=event)
        for entry in self._by_topic.get(event.topic, []):
            outcome = await entry.handler(outcome)
            if outcome.action is InterceptorAction.BLOCK:
                return outcome
        return outcome


def allow(event: BusEnvelope[ProtocolModel], *audit_events: BusEnvelope[ProtocolModel]) -> InterceptorOutcome:
    return InterceptorOutcome(action=InterceptorAction.ALLOW, event=event, audit_events=list(audit_events))


def redact(event: BusEnvelope[ProtocolModel], *audit_events: BusEnvelope[ProtocolModel]) -> InterceptorOutcome:
    return InterceptorOutcome(action=InterceptorAction.REDACT, event=event, audit_events=list(audit_events))


def block(event: BusEnvelope[ProtocolModel], *audit_events: BusEnvelope[ProtocolModel]) -> InterceptorOutcome:
    return InterceptorOutcome(action=InterceptorAction.BLOCK, event=event, audit_events=list(audit_events))


__all__ = [
    "InterceptorAction",
    "InterceptorChain",
    "InterceptorHandler",
    "InterceptorOutcome",
    "allow",
    "block",
    "redact",
]
