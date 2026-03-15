"""Subscriber routing for the priority pub/sub bus."""

from __future__ import annotations

from collections import defaultdict

from emoticorebot.protocol.envelope import BusEnvelope
from emoticorebot.protocol.task_models import ProtocolModel

from .subscriptions import SubscriberHandler, Subscription


class EventRouter:
    """Matches envelopes to subscribers by topic, event type, and target."""

    def __init__(self) -> None:
        self._by_topic: dict[str, list[Subscription]] = defaultdict(list)
        self._wildcard: list[Subscription] = []

    def subscribe(
        self,
        *,
        consumer: str,
        handler: SubscriberHandler,
        topic: str | None = None,
        event_type: str | None = None,
    ) -> Subscription:
        subscription = Subscription(consumer=consumer, handler=handler, topic=topic, event_type=event_type)
        if topic is None:
            self._wildcard.append(subscription)
        else:
            self._by_topic[topic].append(subscription)
        return subscription

    def match(self, event: BusEnvelope[ProtocolModel]) -> list[Subscription]:
        candidates = [*self._wildcard, *self._by_topic.get(event.topic, [])]
        return [subscription for subscription in candidates if subscription.matches(event)]


__all__ = ["EventRouter"]
