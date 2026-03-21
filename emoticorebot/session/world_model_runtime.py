"""Stateful world-model helper used by SessionRuntime."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from emoticorebot.executor.store import ExecutorStore
from emoticorebot.protocol.envelope import BusEnvelope
from emoticorebot.protocol.events import (
    ExecutorRejectedPayload,
    ExecutorResultPayload,
)
from emoticorebot.protocol.topics import EventType
from emoticorebot.world_model import (
    WorldModel,
    WorldModelStore,
    apply_executor_terminal,
    build_task_blueprint,
    clear_current_task,
    project_task_from_blueprint,
    project_task_from_executor_record,
    set_current_task,
    touch_current_topic,
)


class SessionWorldModelRuntime:
    """Owns per-session world-model cache and executor focus state."""

    def __init__(self, *, task_store: ExecutorStore, world_model_store: WorldModelStore) -> None:
        self._task_store = task_store
        self._world_model_store = world_model_store
        self._world_models: dict[str, WorldModel] = {}
        self._task_blueprints_by_job: dict[tuple[str, str], dict[str, Any]] = {}
        self._current_task_blueprints: dict[str, dict[str, Any]] = {}

    def clear_session(self, session_id: str) -> None:
        session_key = str(session_id or "").strip()
        self._world_models.pop(session_key, None)
        self._world_model_store.clear(session_key)
        for key in [key for key in self._task_blueprints_by_job if key[0] == session_key]:
            self._task_blueprints_by_job.pop(key, None)
        self._current_task_blueprints.pop(session_key, None)

    def snapshot(self, session_id: str) -> WorldModel:
        return deepcopy(self._world_model(session_id))

    def on_user_input(self, *, session_id: str, user_text: str) -> None:
        model = self._world_model(session_id)
        if model.current_task is None:
            touch_current_topic(model, user_text)
        self._save_world_model(session_id)

    def record_job_blueprint(self, *, session_id: str, job_id: str, request: Mapping[str, Any]) -> None:
        session_key = str(session_id or "").strip()
        job_key = str(job_id or "").strip()
        if not session_key or not job_key:
            return
        self._task_blueprints_by_job[(session_key, job_key)] = build_task_blueprint(request)

    def finalize_completed_task(self, *, session_id: str, task_id: str | None) -> None:
        task_key = str(task_id or "").strip()
        if not task_key:
            return
        model = self._world_model(session_id)
        previous_task_id = str(model.current_task.task_id if model.current_task is not None else "").strip()
        clear_current_task(model, task_key)
        self._save_world_model(session_id)
        session_key = str(session_id or "").strip()
        if previous_task_id == task_key and model.current_task is None:
            self._current_task_blueprints.pop(session_key, None)

    def apply_executor_event(
        self,
        *,
        session_id: str,
        event: BusEnvelope[ExecutorRejectedPayload | ExecutorResultPayload],
    ) -> None:
        session_key = str(session_id or "").strip()
        if not session_key:
            return
        model = self._world_model(session_key)
        task_id = str(event.task_id or "").strip()
        job_id = str(getattr(event.payload, "job_id", "") or "").strip()
        record = self._task_store.get(task_id) if task_id else None
        blueprint: dict[str, Any] = {}
        if job_id:
            blueprint = dict(self._task_blueprints_by_job.get((session_key, job_id), {}) or {})
        if not blueprint:
            blueprint = dict(self._current_task_blueprints.get(session_key, {}) or {})
        current_task = model.current_task
        if task_id and current_task is None:
            if record is not None:
                set_current_task(model, project_task_from_executor_record(record, blueprint=blueprint))
            elif blueprint:
                set_current_task(model, project_task_from_blueprint(task_id, blueprint))
            current_task = model.current_task
        if task_id and current_task is not None and current_task.task_id != task_id:
            self._save_world_model(session_key)
            if job_id and event.event_type in {
                EventType.EXECUTOR_EVENT_JOB_REJECTED,
                EventType.EXECUTOR_EVENT_RESULT_READY,
            }:
                self._task_blueprints_by_job.pop((session_key, job_id), None)
            return

        if event.event_type == EventType.EXECUTOR_EVENT_JOB_REJECTED:
            if task_id:
                apply_executor_terminal(
                    model,
                    task_id=task_id,
                    record=record,
                    summary=str(event.payload.reason or "").strip(),
                    terminal_status="failed",
                )
        elif event.event_type == EventType.EXECUTOR_EVENT_RESULT_READY:
            if task_id:
                metadata = dict(getattr(event.payload, "metadata", {}) or {})
                result_status = str(metadata.get("result", "") or "").strip() or "success"
                apply_executor_terminal(
                    model,
                    task_id=task_id,
                    record=record,
                    summary=str(event.payload.summary or "").strip(),
                    result_text=str(event.payload.result_text or "").strip(),
                    terminal_status=result_status,
                    artifacts=list(event.payload.artifacts or []),
                )

        self._save_world_model(session_key)
        if job_id and event.event_type in {
            EventType.EXECUTOR_EVENT_JOB_REJECTED,
            EventType.EXECUTOR_EVENT_RESULT_READY,
        }:
            self._task_blueprints_by_job.pop((session_key, job_id), None)

    def apply_brain_focus_requests(
        self,
        *,
        session_id: str,
        requests: list[dict[str, Any]],
    ) -> None:
        session_key = str(session_id or "").strip()
        if not session_key:
            return
        model = self._world_model(session_id)
        execute_request = self._execute_request(requests)
        if execute_request is None:
            self._save_world_model(session_id)
            return
        task_id = str(execute_request.get("task_id", "") or "").strip()
        if not task_id:
            self._save_world_model(session_id)
            return
        blueprint = build_task_blueprint(execute_request)
        self._current_task_blueprints[session_key] = blueprint
        set_current_task(model, project_task_from_blueprint(task_id, blueprint))
        goal = self._request_goal(execute_request)
        if goal:
            touch_current_topic(model, goal)
        self._save_world_model(session_id)

    def _world_model(self, session_id: str) -> WorldModel:
        session_key = str(session_id or "").strip()
        model = self._world_models.get(session_key)
        if model is None:
            model = self._world_model_store.load(session_key)
            self._world_models[session_key] = model
        return model

    def _save_world_model(self, session_id: str) -> None:
        session_key = str(session_id or "").strip()
        if not session_key:
            return
        self._world_model_store.save(self._world_model(session_key))

    @staticmethod
    def _execute_request(requests: list[dict[str, Any]]) -> dict[str, Any] | None:
        for request in requests:
            if str(request.get("job_action", "") or "").strip() == "execute":
                return request
        return None

    @staticmethod
    def _request_goal(request: Mapping[str, Any] | None) -> str:
        payload = dict(request or {})
        context = payload.get("context")
        if not isinstance(context, Mapping):
            context = {}
        return (
            str(payload.get("goal", "") or "").strip()
            or str(context.get("goal", "") or "").strip()
            or str(context.get("title", "") or "").strip()
        )
