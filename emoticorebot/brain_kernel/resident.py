"""Resident runtime helpers for the brain kernel."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .memory import make_id
from .models import BrainEvent, BrainEventType, BrainOutput, BrainOutputType


@dataclass
class PendingSleepJob:
    conversation_id: str
    user_id: str
    turn_id: str
    latest_user_text: str
    latest_front_reply: str


class BrainKernelResidentMixin:
    async def _resident_loop(self) -> None:
        assert self._event_queue is not None
        assert self._output_queue is not None

        try:
            while True:
                event = await self._event_queue.get()
                if event.type == BrainEventType.shutdown:
                    await self._shutdown_conversation_workers()
                    await self._shutdown_sleep_worker()
                    await self._output_queue.put(
                        BrainOutput(
                            event_id=event.event_id,
                            type=BrainOutputType.stopped,
                        )
                    )
                    break

                try:
                    await self._dispatch_event(event)
                except Exception as exc:
                    await self._output_queue.put(
                        BrainOutput(
                            event_id=event.event_id,
                            type=BrainOutputType.error,
                            error=str(exc),
                        )
                    )
        finally:
            self._resident_task = None
            self._conversation_foregrounds = {}
            self._conversation_tasks = {}
            self._conversation_queues = {}
            self._sleep_worker_task = None
            self._sleep_queue = None

    async def _put_event(self, event: BrainEvent) -> None:
        if self._event_queue is None or self._resident_task is None or self._resident_task.done():
            raise RuntimeError("BrainKernel is not running. Call start() first.")
        await self._event_queue.put(event)

    async def _dispatch_event(self, event: BrainEvent) -> None:
        conversation_id = event.conversation_id.strip()
        if not conversation_id:
            raise RuntimeError(f"Brain event {event.event_id} is missing conversation_id.")

        queue = self._ensure_conversation_queue(conversation_id)
        await queue.put(event)

    async def _shutdown_conversation_workers(self) -> None:
        shutdown_events = [
            queue.put(BrainEvent(type=BrainEventType.shutdown, conversation_id=conversation_id))
            for conversation_id, queue in self._conversation_queues.items()
        ]
        if shutdown_events:
            await asyncio.gather(*shutdown_events)

        tasks = [task for task in self._conversation_tasks.values() if not task.done()]
        if tasks:
            await asyncio.gather(*tasks)

    async def _shutdown_sleep_worker(self) -> None:
        if self._sleep_worker_task is None:
            return
        if self._sleep_queue is not None and not self._sleep_worker_task.done():
            await self._sleep_queue.put(None)
        await self._sleep_worker_task
        self._sleep_worker_task = None
        self._sleep_queue = None

    def _ensure_conversation_queue(self, conversation_id: str) -> asyncio.Queue[BrainEvent]:
        queue = self._conversation_queues.get(conversation_id)
        if queue is None:
            queue = asyncio.Queue()
            self._conversation_queues[conversation_id] = queue

        task = self._conversation_tasks.get(conversation_id)
        if task is None or task.done():
            self._conversation_tasks[conversation_id] = asyncio.create_task(
                self._conversation_loop(conversation_id, queue)
            )
        return queue

    async def _conversation_loop(self, conversation_id: str, queue: asyncio.Queue[BrainEvent]) -> None:
        assert self._output_queue is not None

        try:
            while True:
                event = await queue.get()
                if event.type == BrainEventType.shutdown:
                    break

                try:
                    output = await self._process_event(event)
                except Exception as exc:
                    output = BrainOutput(
                        event_id=event.event_id,
                        type=BrainOutputType.error,
                        error=str(exc),
                    )
                await self._output_queue.put(output)
        finally:
            task = self._conversation_tasks.get(conversation_id)
            current_task = asyncio.current_task()
            if task is current_task:
                self._conversation_tasks.pop(conversation_id, None)
            self._conversation_queues.pop(conversation_id, None)

    async def _sleep_worker_loop(self) -> None:
        assert self._sleep_queue is not None

        while True:
            job = await self._sleep_queue.get()
            if job is None:
                break

            try:
                await self.run_sleep_cycle(
                    conversation_id=job.conversation_id,
                    user_id=job.user_id,
                    turn_id=job.turn_id,
                    latest_user_text=job.latest_user_text,
                    latest_front_reply=job.latest_front_reply,
                )
            except Exception as exc:
                if self._output_queue is not None:
                    await self._output_queue.put(
                        BrainOutput(
                            event_id=make_id("sleep_job"),
                            type=BrainOutputType.error,
                            error=f"Sleep worker failed: {exc}",
                        )
                    )

    async def _process_event(self, event: BrainEvent) -> BrainOutput:
        if event.type == BrainEventType.user_input:
            response = await self.handle_user_input(
                conversation_id=event.conversation_id,
                text=event.text,
                user_id=event.user_id,
                turn_id=event.turn_id,
                latest_front_reply=event.latest_front_reply,
                background=event.background,
                target_run_id=event.target_run_id,
                metadata=event.metadata,
            )
        elif event.type == BrainEventType.observation:
            response = await self.handle_observation(
                conversation_id=event.conversation_id,
                text=event.text,
                user_id=event.user_id,
                turn_id=event.turn_id,
                latest_front_reply=event.latest_front_reply,
                background=event.background,
                metadata=event.metadata,
            )
        elif event.type == BrainEventType.tool_results:
            response = await self.handle_tool_results(
                run_id=event.run_id,
                tool_results=event.tool_results,
                latest_front_reply=event.latest_front_reply,
            )
        elif event.type == BrainEventType.front_event:
            if event.front_event is None:
                raise RuntimeError(f"front_event payload is required for event {event.event_id}")
            await self.handle_front_event(
                conversation_id=event.conversation_id,
                front_event=event.front_event,
                user_id=event.user_id,
                turn_id=event.turn_id,
            )
            return BrainOutput(
                event_id=event.event_id,
                type=BrainOutputType.recorded,
            )
        else:
            raise RuntimeError(f"Unsupported brain event type: {event.type}")

        return BrainOutput(
            event_id=event.event_id,
            type=BrainOutputType.response,
            response=response,
        )

    def _resolve_run_conversation_id(self, run_id: str) -> str:
        state = self._pending_runs.get(run_id)
        if state is not None:
            return state.context.conversation_id

        run = self.run_store.get_run(run_id)
        if run is not None and run.conversation_id:
            return run.conversation_id

        raise RuntimeError(f"Run {run_id} does not exist.")
