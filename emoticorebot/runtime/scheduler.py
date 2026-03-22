"""Runtime scheduler for Front -> Scheduler -> Core."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from emoticorebot.core.runtime import CoreRuntime
from emoticorebot.front.service import FrontService
from emoticorebot.state import MemoryStore, WorldModelStore
from emoticorebot.state.schemas import UserEvent, WorldModelUpdate, make_id


class RuntimeScheduler:
    """Pure scheduler: front replies first, core continues in background."""

    def __init__(
        self,
        workspace: Path,
        front: FrontService,
        core: CoreRuntime,
        memory_store: MemoryStore,
        world_model_store: WorldModelStore,
    ):
        self.workspace = workspace
        self.front = front
        self.core = core
        self.memory_store = memory_store
        self.world_model_store = world_model_store
        self.thread_locks: dict[str, asyncio.Lock] = {}
        self.background_tasks: dict[str, set[asyncio.Task[Any]]] = {}
        self.background_errors: dict[str, list[BaseException]] = {}

    async def handle_user_text(
        self,
        *,
        thread_id: str,
        session_id: str,
        user_id: str,
        user_text: str,
        stream_handler: Callable[[str], Awaitable[None]] | None,
    ) -> str:
        event = UserEvent(
            event_id=make_id("user"),
            thread_id=thread_id,
            session_id=session_id,
            user_id=user_id,
            user_text=user_text,
        )
        self.memory_store.append_brain_record(
            thread_id,
            {
                "role": "user",
                "content": user_text,
                "event_type": "user_event",
                "session_id": session_id,
                "user_id": user_id,
                "turn_id": event.event_id,
            },
        )
        memory = self.memory_store.build_memory_view(thread_id, session_id, user_text)
        front_reply = await self.front.reply(user_text=user_text, memory=memory, stream_handler=stream_handler)
        self.memory_store.append_brain_record(
            thread_id,
            {
                "role": "assistant",
                "content": front_reply,
                "event_type": "front_reply",
                "session_id": session_id,
                "user_id": user_id,
                "turn_id": event.event_id,
            },
        )
        self.launch_background_task(thread_id, self.process_user_event(event, front_reply))
        return front_reply

    async def process_user_event(self, event: UserEvent, front_reply: str) -> None:
        lock = self.thread_locks.setdefault(event.thread_id, asyncio.Lock())
        async with lock:
            self.world_model_store.update(
                WorldModelUpdate(
                    mode="acting",
                    recent_intent=event.user_text,
                )
            )
            result = await self.core.run_user_event(event, front_reply)
            self.memory_store.append_tool_record(
                event.thread_id,
                {
                    "role": "tool",
                    "tool_name": "core",
                    "params": {"event_id": event.event_id},
                    "success": True,
                    "content": result.summary,
                    "turn_id": event.event_id,
                    "session_id": event.session_id,
                    "user_id": event.user_id,
                    "event_type": "core_summary",
                },
            )
            current_world_model = self.world_model_store.load()
            if current_world_model.mode == "acting":
                self.world_model_store.update(WorldModelUpdate(mode="chat"))

    def launch_background_task(self, thread_id: str, coroutine: Awaitable[Any]) -> None:
        task = asyncio.create_task(coroutine)
        bucket = self.background_tasks.setdefault(thread_id, set())
        bucket.add(task)
        task.add_done_callback(lambda done_task: self.finalize_task(thread_id, done_task))

    def finalize_task(
        self,
        thread_id: str,
        task: asyncio.Task[Any],
    ) -> None:
        bucket = self.background_tasks.get(thread_id, set())
        bucket.discard(task)
        if not bucket and thread_id in self.background_tasks:
            self.background_tasks.pop(thread_id, None)
        if task.cancelled():
            return
        exception = task.exception()
        if exception is None:
            return
        self.background_errors.setdefault(thread_id, []).append(exception)

    async def wait_for_thread_idle(self, thread_id: str, timeout: float = 600.0) -> None:
        async with asyncio.timeout(timeout):
            while True:
                tasks = list(self.background_tasks.get(thread_id, set()))
                if not tasks:
                    errors = self.background_errors.pop(thread_id, [])
                    if errors:
                        raise errors[0]
                    return
                await asyncio.gather(*tasks, return_exceptions=False)
