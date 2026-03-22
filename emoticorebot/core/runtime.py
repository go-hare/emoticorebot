"""Core runtime that applies decisions and schedules execution/reflection."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from emoticorebot.core.main_agent import CoreMainAgent
from emoticorebot.core.reflection_agent import CoreReflectionAgent
from emoticorebot.core.schemas import DispatchCheck, MainDecision
from emoticorebot.state import MemoryStore, SkillStore, WorldStateStore
from emoticorebot.state.schemas import MemoryView, ReflectionRequest, StatePatch, UserEvent, WorldState, make_id, now_iso


class CoreRuntime:
    """Read state, call agents, apply patches, and emit work."""

    def __init__(
        self,
        workspace: Path,
        main_model: Any,
        reflection_model: Any,
        memory_store: MemoryStore,
        world_state_store: WorldStateStore,
        speak_handler: Callable[[str, str], Awaitable[None]],
        dispatch_handler: Callable[[list[DispatchCheck]], Awaitable[None]],
        reflection_handler: Callable[[ReflectionRequest], Awaitable[None]],
    ):
        self.workspace = workspace
        self.main_agent = CoreMainAgent(workspace, main_model)
        self.reflection_agent = CoreReflectionAgent(workspace, reflection_model)
        self.memory_store = memory_store
        self.skill_store = SkillStore(workspace)
        self.world_state_store = world_state_store
        self.speak_handler = speak_handler
        self.dispatch_handler = dispatch_handler
        self.reflection_handler = reflection_handler

    async def handle_user_event(self, event: UserEvent, front_reply: str) -> None:
        memory = self.memory_store.build_memory_view(event.thread_id, event.session_id, event.user_text)
        world_state = self.world_state_store.load()
        trigger = {"event_type": "user_event", "payload": event.model_dump()}
        decision = await self.main_agent.decide(
            trigger=trigger,
            memory=memory,
            world_state=world_state,
            front_observation={"latest_user_text": event.user_text, "latest_front_reply": front_reply},
        )
        await self.apply_decision(event.thread_id, event.session_id, event.user_id, trigger, memory, world_state, decision)

    async def handle_execution_result(
        self,
        *,
        thread_id: str,
        session_id: str,
        user_id: str,
        result_payload: dict[str, Any],
    ) -> None:
        summary = str(result_payload.get("summary", "") or "").strip()
        error = str(result_payload.get("error", "") or "").strip()
        self.world_state_store.apply_execution_result(
            task_id=str(result_payload.get("task_id", "") or "").strip(),
            check_id=str(result_payload.get("check_id", "") or "").strip(),
            job_id=str(result_payload.get("job_id", "") or "").strip(),
            status=str(result_payload.get("status", "") or "").strip(),
            summary=summary,
            error=error,
            artifacts=list(result_payload.get("artifacts", []) or []),
        )
        memory = self.memory_store.build_memory_view(thread_id, session_id, summary or error or "execution_result")
        world_state = self.world_state_store.load()
        trigger = {"event_type": "execution_result", "payload": result_payload}
        decision = await self.main_agent.decide(
            trigger=trigger,
            memory=memory,
            world_state=world_state,
            front_observation={"latest_user_text": "", "latest_front_reply": ""},
        )
        await self.apply_decision(thread_id, session_id, user_id, trigger, memory, world_state, decision)

    async def handle_reflection_request(self, request: ReflectionRequest) -> None:
        result = await self.run_reflection_pipeline(request)
        memory = self.memory_store.build_memory_view(
            request.thread_id,
            request.session_id,
            request.reason or request.trigger.get("event_type", "reflection_result"),
        )
        world_state = self.world_state_store.load()
        trigger = {"event_type": "reflection_result", "payload": result.model_dump()}
        decision = await self.main_agent.decide(
            trigger=trigger,
            memory=memory,
            world_state=world_state,
            front_observation={"latest_user_text": "", "latest_front_reply": ""},
        )
        await self.apply_decision(request.thread_id, request.session_id, request.user_id, trigger, memory, world_state, decision)

    async def run_reflection_pipeline(self, request: ReflectionRequest) -> Any:
        current_request = request
        latest_result = None
        for _ in range(3):
            latest_result = await self.reflection_agent.reflect(current_request)
            self.memory_store.append_patch(latest_result.memory_patch)
            if latest_result.mode == "crystallize":
                self.skill_store.write_from_memory_patch(latest_result.memory_patch, current_request.reason)
            self.world_state_store.apply_patch(latest_result.world_state_suggestion)
            next_reason = self.next_reflection_reason(latest_result)
            if not next_reason:
                return latest_result
            current_request = ReflectionRequest(
                thread_id=request.thread_id,
                session_id=request.session_id,
                user_id=request.user_id,
                reason=next_reason,
                trigger=request.trigger,
                memory=self.memory_store.build_memory_view(
                    request.thread_id,
                    request.session_id,
                    next_reason,
                ),
                world_state=self.world_state_store.load(),
            )
        if latest_result is None:
            raise RuntimeError("Reflection pipeline did not produce a result")
        return latest_result

    def next_reflection_reason(self, result: Any) -> str:
        if result.mode == "light":
            has_deep_signal = any(item.needs_deep_reflection for item in result.memory_patch.cognitive_append)
            return "deep_reflection" if has_deep_signal else ""
        if result.mode == "deep":
            return "crystallize_reflection"
        return ""

    async def apply_decision(
        self,
        thread_id: str,
        session_id: str,
        user_id: str,
        trigger: dict[str, Any],
        memory: MemoryView,
        world_state: WorldState,
        decision: MainDecision,
    ) -> None:
        prepared_patch = self.prepare_state_patch(decision.state_patch)
        updated_world_state = self.world_state_store.apply_patch(prepared_patch)
        self.memory_store.append_patch(decision.memory_patch)
        updated_memory = self.memory_store.build_memory_view(thread_id, session_id, trigger["event_type"])

        should_speak = decision.speak_intent.mode != "none" and decision.speak_intent.text.strip()
        if trigger["event_type"] == "user_event":
            should_speak = False
        if should_speak:
            await self.speak_handler(thread_id, decision.speak_intent.text.strip())

        checks = self.prepare_dispatch_checks(decision.dispatch_checks, prepared_patch, thread_id)
        if checks:
            await self.dispatch_handler(checks)

        if decision.run_reflection:
            request = ReflectionRequest(
                thread_id=thread_id,
                session_id=session_id,
                user_id=user_id,
                reason=decision.reflection_reason or trigger["event_type"],
                trigger=trigger,
                memory=updated_memory,
                world_state=updated_world_state,
            )
            await self.reflection_handler(request)

    def prepare_state_patch(self, patch: StatePatch) -> StatePatch:
        prepared = StatePatch.model_validate(patch.model_dump())
        for task in prepared.upsert_tasks:
            if not task.task_id:
                task.task_id = make_id("task")
        for check in prepared.upsert_checks:
            if not check.task_id:
                continue
            if not check.check_id:
                check.check_id = make_id("check")
            check.updated_at = now_iso()
        for job in prepared.upsert_running_jobs:
            if not job.job_id:
                job.job_id = make_id("job")
        return prepared

    def prepare_dispatch_checks(self, checks: list[DispatchCheck], state_patch: StatePatch, thread_id: str) -> list[DispatchCheck]:
        prepared: list[DispatchCheck] = []
        latest_task_id = state_patch.upsert_tasks[-1].task_id if state_patch.upsert_tasks else ""
        for check in checks:
            item = DispatchCheck.model_validate(check.model_dump())
            if not item.task_id:
                item.task_id = latest_task_id
            if not item.check_id:
                item.check_id = make_id("check")
            if not item.job_id:
                item.job_id = make_id("job")
            item.thread_id = thread_id
            if not item.workspace:
                item.workspace = str(self.workspace)
            prepared.append(item)
        if not prepared:
            return prepared
        patch = StatePatch(
            upsert_checks=[
                {
                    "check_id": item.check_id,
                    "task_id": item.task_id,
                    "goal": item.goal,
                    "instructions": item.instructions,
                    "status": "running",
                }
                for item in prepared
            ],
            upsert_running_jobs=[
                {
                    "job_id": item.job_id,
                    "task_id": item.task_id,
                    "check_id": item.check_id,
                    "thread_id": item.thread_id,
                    "goal": item.goal,
                    "workspace": item.workspace,
                }
                for item in prepared
            ],
        )
        self.world_state_store.apply_patch(StatePatch.model_validate(patch.model_dump()))
        return prepared
