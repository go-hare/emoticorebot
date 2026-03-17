from __future__ import annotations

from emoticorebot.session.models import SessionTaskView, SessionTraceRecord
from emoticorebot.utils.task_projection import (
    project_task_from_runtime_snapshot,
    project_task_from_session_view,
    task_result_from_status,
    task_state_from_status,
)


def test_task_projection_maps_runtime_status_to_three_state_view() -> None:
    assert task_state_from_status("assigned") == "running"
    assert task_state_from_status("waiting_input") == "waiting"
    assert task_state_from_status("failed") == "done"
    assert task_result_from_status("done") == "success"
    assert task_result_from_status("cancelled") == "cancelled"
    assert task_result_from_status("running") == "none"


def test_project_task_from_runtime_snapshot_uses_new_fields() -> None:
    projected = project_task_from_runtime_snapshot(
        {
            "task_id": "task_1",
            "title": "创建 add.py",
            "status": "waiting_input",
            "summary": "等待路径",
            "last_progress": "需要用户输入",
            "input_request": {"field": "path", "question": "文件放哪里？"},
        },
        params={"request": "创建 add.py"},
    )

    assert projected == {
        "invoked": True,
        "task_id": "task_1",
        "title": "创建 add.py",
        "state": "waiting",
        "result": "none",
        "summary": "等待路径",
        "stage": "需要用户输入",
        "input_request": {"field": "path", "question": "文件放哪里？"},
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
            SessionTraceRecord(trace_id="trace_2", task_id="task_1", kind="result", message="写入完成", ts="2"),
        ],
    )

    projected = project_task_from_session_view(view, params={"request": "创建 add.py"})

    assert projected["state"] == "done"
    assert projected["result"] == "success"
    assert projected["params"] == {"request": "创建 add.py"}
    assert projected["task_trace"][0]["message"] == "正在写文件"
    assert projected["task_trace"][1]["message"] == "写入完成"

