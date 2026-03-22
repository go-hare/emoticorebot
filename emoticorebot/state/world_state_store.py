"""World state storage and patch application."""

from __future__ import annotations

from pathlib import Path

from emoticorebot.state.io import read_json, write_json
from emoticorebot.state.schemas import Artifact, CheckState, RunningJob, StatePatch, TaskState, WorldState, now_iso


class WorldStateStore:
    """Owns world_state.json and applies patches."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.path = self.workspace / "state" / "world_state.json"

    def load(self) -> WorldState:
        return WorldState.model_validate(read_json(self.path, WorldState().model_dump()))

    def save(self, state: WorldState) -> None:
        write_json(self.path, state.model_dump())

    def apply_patch(self, patch: StatePatch) -> WorldState:
        state = self.load()
        if patch.focus_task_id:
            state.focus_task_id = patch.focus_task_id

        for task_id in patch.remove_task_ids:
            state.tasks.pop(task_id, None)
            if state.focus_task_id == task_id:
                state.focus_task_id = ""

        for job_id in patch.remove_job_ids:
            state.running_jobs.pop(job_id, None)

        for check_id in patch.remove_check_ids:
            for task in state.tasks.values():
                task.checks.pop(check_id, None)

        for task in patch.upsert_tasks:
            current = state.tasks.get(task.task_id, TaskState(task_id=task.task_id))
            merged_data = current.model_dump()
            merged_data.update(task.model_dump(exclude_none=True))
            merged = TaskState.model_validate(merged_data)
            merged.updated_at = now_iso()
            state.tasks[task.task_id] = merged

        for check in patch.upsert_checks:
            task = state.tasks.get(check.task_id, TaskState(task_id=check.task_id))
            current = task.checks.get(check.check_id, CheckState(check_id=check.check_id, task_id=check.task_id))
            merged_data = current.model_dump()
            merged_data.update(check.model_dump(exclude_none=True))
            merged = CheckState.model_validate(merged_data)
            merged.updated_at = now_iso()
            task.checks[check.check_id] = merged
            task.updated_at = now_iso()
            state.tasks[task.task_id] = task

        for job in patch.upsert_running_jobs:
            state.running_jobs[job.job_id] = RunningJob.model_validate(job.model_dump())

        self.save(state)
        return state

    def apply_execution_result(
        self,
        *,
        task_id: str,
        check_id: str,
        job_id: str,
        status: str,
        summary: str,
        error: str,
        artifacts: list[dict],
    ) -> WorldState:
        normalized_artifacts = [Artifact.model_validate(item) for item in artifacts]
        patch = StatePatch(remove_job_ids=[job_id])
        patch.upsert_checks.append(
            CheckState(
                check_id=check_id,
                task_id=task_id,
                status="done" if status == "done" else "failed",
                summary=summary,
                error=error,
                artifacts=normalized_artifacts,
            )
        )
        return self.apply_patch(patch)
