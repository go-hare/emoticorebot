"""Gateway that connects front, core, execution, and state."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from emoticorebot.core.runtime import CoreRuntime
from emoticorebot.execution.runtime import ExecutionRuntime
from emoticorebot.front.service import FrontService
from emoticorebot.state import MemoryStore, WorldStateStore
from emoticorebot.state.schemas import ReflectionRequest, UserEvent, make_id


@dataclass(slots=True)
class SessionHandle:
    session_id: str
    user_id: str
    reply_handler: Callable[[str], Awaitable[None]]


class GatewayService:
    """Runtime entry for user events and background followups."""

    def __init__(
        self,
        workspace: Path,
        front: FrontService,
        core: CoreRuntime,
        execution: ExecutionRuntime,
        memory_store: MemoryStore,
        world_state_store: WorldStateStore,
    ):
        self.workspace = workspace
        self.front = front
        self.core = core
        self.execution = execution
        self.memory_store = memory_store
        self.world_state_store = world_state_store
        self.sessions: dict[str, SessionHandle] = {}
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
        reply_handler: Callable[[str], Awaitable[None]],
    ) -> str:
        self.sessions[thread_id] = SessionHandle(session_id=session_id, user_id=user_id, reply_handler=reply_handler)
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
        self.launch_background_task(thread_id, self.core.handle_user_event(event, front_reply))
        return front_reply

    async def send_followup(self, thread_id: str, intent_text: str) -> None:
        session = self.sessions[thread_id]
        memory = self.memory_store.build_memory_view(thread_id, session.session_id, intent_text)
        followup = await self.front.followup(intent_text=intent_text, memory=memory)
        await session.reply_handler(followup)
        self.memory_store.append_brain_record(
            thread_id,
            {
                "role": "assistant",
                "content": followup,
                "event_type": "front_followup",
                "session_id": session.session_id,
                "user_id": session.user_id,
                "turn_id": make_id("followup"),
            },
        )

    async def dispatch_checks(self, checks: list[dict]) -> None:
        for check in checks:
            payload = check.model_dump() if hasattr(check, "model_dump") else dict(check)
            thread_id = str(payload.get("thread_id", "") or "").strip()
            self.launch_background_task(thread_id, self.execution.run_check(payload))

    async def launch_reflection(self, request: ReflectionRequest) -> None:
        self.launch_background_task(request.thread_id, self.core.handle_reflection_request(request))

    async def handle_execution_result(self, payload: dict) -> None:
        thread_id = self.thread_id_for_result(payload)
        trace = [row for row in list(payload.get("trace", []) or []) if isinstance(row, dict)]
        self.memory_store.append_executor_records(thread_id, trace)
        session = self.sessions[thread_id]
        self.launch_background_task(
            thread_id,
            self.core.handle_execution_result(
                thread_id=thread_id,
                session_id=session.session_id,
                user_id=session.user_id,
                result_payload=payload,
            )
        )

    def launch_background_task(self, thread_id: str, coroutine: Awaitable[Any]) -> None:
        if not thread_id:
            raise RuntimeError("thread_id is required for background task tracking")
        task = asyncio.create_task(coroutine)
        bucket = self.background_tasks.setdefault(thread_id, set())
        bucket.add(task)

        def finalize(done_task: asyncio.Task[Any]) -> None:
            bucket.discard(done_task)
            if not bucket:
                self.background_tasks.pop(thread_id, None)
            if done_task.cancelled():
                return
            exception = done_task.exception()
            if exception is None:
                return
            self.background_errors.setdefault(thread_id, []).append(exception)

        task.add_done_callback(finalize)

    async def wait_for_thread_idle(self, thread_id: str, timeout: float = 120.0) -> None:
        async with asyncio.timeout(timeout):
            while True:
                tasks = list(self.background_tasks.get(thread_id, set()))
                if not tasks:
                    errors = self.background_errors.pop(thread_id, [])
                    if errors:
                        raise errors[0]
                    return
                await asyncio.gather(*tasks, return_exceptions=False)

    def thread_id_for_result(self, payload: dict) -> str:
        explicit_thread_id = str(payload.get("thread_id", "") or "").strip()
        if explicit_thread_id:
            return explicit_thread_id
        job_id = str(payload.get("job_id", "") or "").strip()
        world_state = self.world_state_store.load()
        running_job = world_state.running_jobs.get(job_id)
        if running_job is not None and running_job.thread_id:
            return running_job.thread_id
        task_id = str(payload.get("task_id", "") or "").strip()
        for thread_id in self.sessions:
            if task_id and task_id in world_state.tasks:
                return thread_id
        if self.sessions:
            return next(iter(self.sessions.keys()))
        raise RuntimeError("No active session for execution result")
