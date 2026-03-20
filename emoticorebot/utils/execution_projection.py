"""Small helpers that project internal task objects into the public 3-state view."""

from __future__ import annotations

from typing import Any, Mapping

def normalize_task_state(state: str) -> str:
    normalized = str(state or "").strip()
    if normalized == "done":
        return "done"
    if normalized == "running":
        return "running"
    return "running"


def normalize_task_result(state: str, result: str = "none") -> str:
    normalized = str(state or "").strip()
    normalized_result = str(result or "").strip() or "none"
    if normalized != "done":
        return "none"
    if normalized_result in {"success", "failed", "cancelled"}:
        return normalized_result
    if normalized == "done":
        return "success"
    return "none"


def project_task_from_runtime_snapshot(
    snapshot: Mapping[str, Any] | None,
    *,
    params: Mapping[str, Any] | None = None,
    trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = dict(snapshot or {})
    state = str(payload.get("state", "") or "").strip()
    result = str(payload.get("result", "") or "").strip()
    task: dict[str, Any] = {
        "invoked": True,
        "task_id": str(payload.get("task_id", "") or "").strip(),
        "title": str(payload.get("title", "") or "").strip(),
        "state": normalize_task_state(state),
        "result": normalize_task_result(state, result),
        "summary": str(payload.get("summary", "") or "").strip(),
        "error": str(payload.get("error", "") or "").strip(),
        "stage": str(payload.get("last_progress", "") or "").strip(),
    }
    if params:
        task["params"] = dict(params)
    if trace:
        task["task_trace"] = list(trace)
    return _drop_empty(task)


def project_task_from_session_view(
    task_view: Any,
    *,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if task_view is None:
        return {}
    trace = [
        _drop_empty(
            {
                "trace_id": str(getattr(item, "trace_id", "") or "").strip(),
                "kind": str(getattr(item, "kind", "") or "").strip(),
                "message": str(getattr(item, "message", "") or "").strip(),
                "ts": str(getattr(item, "ts", "") or "").strip(),
                "data": dict(getattr(item, "data", {}) or {}),
            }
        )
        for item in list(getattr(task_view, "trace", []) or [])
    ]
    task = {
        "invoked": True,
        "task_id": str(getattr(task_view, "task_id", "") or "").strip(),
        "title": str(getattr(task_view, "title", "") or "").strip(),
        "state": str(getattr(task_view, "state", "") or "running").strip() or "running",
        "result": str(getattr(task_view, "result", "") or "none").strip() or "none",
        "summary": str(getattr(task_view, "summary", "") or "").strip(),
        "stage": str(getattr(task_view, "summary", "") or "").strip(),
    }
    if params:
        task["params"] = dict(params)
    if trace:
        task["task_trace"] = trace
    return _drop_empty(task)


def project_task_for_memory(
    task: Mapping[str, Any] | None,
    *,
    execution: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(task or {})
    execution_payload = dict(execution or {})
    state = str(payload.get("state", "") or "").strip()
    if not state:
        state = _task_state_from_execution(execution_payload)
    state = normalize_task_state(state)
    result = str(payload.get("result", "") or "").strip()
    if not result:
        result = _task_result_from_execution(execution_payload, state=state)
    summary = str(payload.get("summary", "") or execution_payload.get("summary", "") or "").strip()
    return _drop_empty(
        {
            "used": bool(execution_payload.get("invoked")) or bool(payload),
            "state": state or "running",
            "result": result or "none",
            "summary": summary,
        }
    )

def _task_state_from_execution(execution: Mapping[str, Any]) -> str:
    status = str(execution.get("status", "") or "").strip()
    if status == "failed":
        return "done"
    if status in {"done", "completed", "success", "partial"}:
        return "done"
    if status == "none":
        return "running"
    return "running"


def _task_result_from_execution(execution: Mapping[str, Any], *, state: str) -> str:
    status = str(execution.get("status", "") or "").strip()
    if state != "done":
        return "none"
    if status == "failed":
        return "failed"
    if status in {"done", "completed", "success", "partial"}:
        return "success"
    return "none"


def _drop_empty(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value not in ("", None, [], {})}


__all__ = [
    "normalize_task_result",
    "normalize_task_state",
    "project_task_for_memory",
    "project_task_from_runtime_snapshot",
    "project_task_from_session_view",
]
