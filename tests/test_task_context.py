from __future__ import annotations

from emoticorebot.utils.task_context import build_task_context


def test_build_task_context_uses_state_and_result() -> None:
    text = build_task_context(
        {
            "task": {
                "task_id": "task_1",
                "title": "创建 add.py",
                "state": "done",
                "result": "success",
                "summary": "add.py 已创建",
            }
        }
    )

    assert "任务: 创建 add.py" in text
    assert "状态: done" in text
    assert "结果: success" in text
    assert "总结: add.py 已创建" in text
