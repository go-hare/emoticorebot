from __future__ import annotations

from emoticorebot.session.models import SessionTaskView, SessionTraceRecord
from emoticorebot.utils.right_brain_projection import (
    normalize_task_result,
    normalize_task_state,
    project_task_from_runtime_snapshot,
    project_task_from_session_view,
)


def test_task_projection_maps_runtime_state_to_three_state_view() -> None:
    assert normalize_task_state("running") == "running"
    assert normalize_task_state("done") == "done"
    assert normalize_task_result("done", "success") == "success"
    assert normalize_task_result("done", "cancelled") == "cancelled"
    assert normalize_task_result("running", "success") == "none"


def test_project_task_from_runtime_snapshot_uses_compact_right_brain_fields() -> None:
    projected = project_task_from_runtime_snapshot(
        {
            "task_id": "task_1",
            "title": "创建 add.py",
            "state": "running",
            "result": "none",
            "summary": "正在执行",
            "last_progress": "已完成扫描",
        },
        params={"request": "创建 add.py"},
    )

    assert projected == {
        "invoked": True,
        "task_id": "task_1",
        "title": "创建 add.py",
        "state": "running",
        "result": "none",
        "summary": "正在执行",
        "stage": "已完成扫描",
        "params": {"request": "创建 add.py"},
    }


def test_project_task_from_session_view_keeps_trace_and_result() -> None:
    view = SessionTaskView(
        task_id="task_1",
        title="创建 add.py",
        state="done",
        result="success",
        summary="已完成",
        trace=[
            SessionTraceRecord(trace_id="trace_1", task_id="task_1", kind="progress", message="正在写文件", ts="1"),
            SessionTraceRecord(trace_id="trace_2", task_id="task_1", kind="summary", message="写入完成", ts="2"),
        ],
    )

    projected = project_task_from_session_view(view, params={"request": "创建 add.py"})

    assert projected["state"] == "done"
    assert projected["result"] == "success"
    assert projected["params"] == {"request": "创建 add.py"}
    assert projected["task_trace"][0]["message"] == "正在写文件"
    assert projected["task_trace"][1]["message"] == "写入完成"
