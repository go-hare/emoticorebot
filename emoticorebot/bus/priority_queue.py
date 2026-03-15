"""Async priority queue for bus envelopes."""

from __future__ import annotations

import asyncio
import heapq
from dataclasses import dataclass, field

from emoticorebot.protocol.envelope import BusEnvelope
from emoticorebot.protocol.task_models import ProtocolModel


@dataclass(order=True)
class _QueueItem:
    priority: int
    sequence: int
    event: BusEnvelope[ProtocolModel] = field(compare=False)


class PriorityEventQueue:
    """Stable priority queue that preserves FIFO order within the same priority."""

    def __init__(self) -> None:
        self._heap: list[_QueueItem] = []
        self._sequence = 0
        self._condition = asyncio.Condition()

    async def put(self, event: BusEnvelope[ProtocolModel]) -> None:
        async with self._condition:
            heapq.heappush(self._heap, _QueueItem(priority=event.priority, sequence=self._sequence, event=event))
            self._sequence += 1
            self._condition.notify()

    async def get(self) -> BusEnvelope[ProtocolModel]:
        async with self._condition:
            while not self._heap:
                await self._condition.wait()
            return heapq.heappop(self._heap).event

    def qsize(self) -> int:
        return len(self._heap)

    def empty(self) -> bool:
        return not self._heap


__all__ = ["PriorityEventQueue"]
