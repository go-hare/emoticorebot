from __future__ import annotations

from emoticorebot.brain.dialogue_policy import DialoguePolicy
from emoticorebot.brain.right_brain_policy import TaskPolicy
from emoticorebot.protocol.events import TaskAskPayload, TaskEndPayload
from emoticorebot.protocol.task_models import TaskRequestSpec
from emoticorebot.right.state_machine import RightBrainState
from emoticorebot.right.store import RightBrainRecord


def _task(
    *,
    task_id: str = "task_1",
    title: str = "创建 add.py",
    state: RightBrainState = RightBrainState.RUNNING,
    result: str = "none",
    summary: str = "",
    last_progress: str = "",
) -> RightBrainRecord:
    return RightBrainRecord(
        task_id=task_id,
        session_id="cli:direct",
        turn_id="turn_1",
        job_id="job_1",
        request=TaskRequestSpec(request="请新增 add.py 新增方法 add(a,b) 返回 a+b", title=title),
        title=title,
        state=state,
        result=result,
        summary=summary,
        last_progress=last_progress,
    )


def test_task_policy_detects_explicit_code_task() -> None:
    directive = TaskPolicy().decide("修改 sub.py 新增 subOne(a,b,c) 返回 a+b+c", [])

    assert directive.action == "create_task"
    assert directive.title == "修改 sub.py"


def test_task_policy_returns_status_for_existing_active_task() -> None:
    directive = TaskPolicy().decide("add 文件创建好了吗", [_task(last_progress="正在执行内部任务")])

    assert directive.action == "status"
    assert directive.task_id == "task_1"


def test_task_policy_cancels_active_task() -> None:
    directive = TaskPolicy().decide("取消吧", [_task()])

    assert directive.action == "cancel_task"
    assert directive.task_id == "task_1"


def test_dialogue_policy_formats_running_and_failed_status() -> None:
    running = DialoguePolicy.status(_task(last_progress="正在执行内部任务"))
    failed = DialoguePolicy.status(_task(state=RightBrainState.DONE, result="failed", summary="命令执行失败"))

    assert "执行中" in running
    assert "正在执行内部任务" in running
    assert "失败" in failed


def test_dialogue_policy_formats_task_events_and_right_brain_messages() -> None:
    ask = DialoguePolicy.task_ask(
        _task(),
        TaskAskPayload(
            task_id="task_1",
            question="你想查哪个城市？",
            field="city",
            why="已经识别到你想查天气。",
        ),
    )
    end = DialoguePolicy().task_end(
        _task(state=RightBrainState.DONE, result="success"),
        TaskEndPayload(
            task_id="task_1",
            result="success",
            summary="文件已经写入工作区。",
            output="add.py 已创建完成。",
        ),
    )
    accepted = DialoguePolicy.right_brain_accepted(_task(), reason="audit_tool 返回任务可以开始。")
    progress = DialoguePolicy.right_brain_progress(_task(), summary="已完成扫描。", next_step="整理输出")

    assert "需要你补充信息" in ask
    assert "已完成" in end
    assert "已开始处理" in accepted
    assert "整理输出" in progress
