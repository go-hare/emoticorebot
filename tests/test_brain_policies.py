from __future__ import annotations

from emoticorebot.brain.dialogue_policy import DialoguePolicy
from emoticorebot.brain.task_policy import TaskPolicy
from emoticorebot.protocol.events import TaskAskPayload, TaskEndPayload
from emoticorebot.protocol.task_models import TaskRequestSpec
from emoticorebot.runtime.state_machine import TaskState
from emoticorebot.runtime.task_store import RuntimeTaskRecord


def _task(
    *,
    task_id: str = "task_1",
    title: str = "创建 add.py",
    state: TaskState = TaskState.RUNNING,
    result: str = "none",
    summary: str = "",
    last_progress: str = "",
) -> RuntimeTaskRecord:
    return RuntimeTaskRecord(
        task_id=task_id,
        session_id="cli:direct",
        turn_id="turn_1",
        request=TaskRequestSpec(request="请新增 add.py 新增方法 add(a,b) 返回 a+b", title=title),
        origin_message=None,
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


def test_task_policy_uses_waiting_task_as_resume_target() -> None:
    waiting = _task(state=TaskState.WAITING, summary="需要城市")

    directive = TaskPolicy().decide("上海", [waiting])

    assert directive.action == "resume_task"
    assert directive.task_id == "task_1"


def test_task_policy_cancels_active_task() -> None:
    directive = TaskPolicy().decide("取消吧", [_task()])

    assert directive.action == "cancel_task"
    assert directive.task_id == "task_1"


def test_dialogue_policy_greeting_mentions_active_task() -> None:
    text = DialoguePolicy().direct_reply("你好", _task(last_progress="正在执行内部任务"))

    assert "创建 add.py" in text


def test_dialogue_policy_formats_status_with_progress() -> None:
    text = DialoguePolicy.status(_task(last_progress="正在执行内部任务"))

    assert "执行中" in text
    assert "正在执行内部任务" in text


def test_dialogue_policy_uses_compact_task_states_and_results() -> None:
    running = DialoguePolicy.status(_task(state=TaskState.RUNNING))
    waiting = DialoguePolicy.status(_task(state=TaskState.WAITING))
    failed = DialoguePolicy.status(_task(state=TaskState.DONE, result="failed", summary="命令执行失败"))

    assert "执行中" in running
    assert "等你补充信息" in waiting
    assert "失败" in failed


def test_dialogue_policy_formats_need_input() -> None:
    text = DialoguePolicy.task_ask(
        _task(state=TaskState.WAITING),
        TaskAskPayload(
            task_id="task_1",
            question="你想查哪个城市？",
            field="city",
            why="已经识别到你想查天气。",
        ),
    )

    assert "需要你补充信息" in text
    assert "你想查哪个城市" in text


def test_dialogue_policy_formats_task_result() -> None:
    text = DialoguePolicy().task_end(
        _task(state=TaskState.DONE),
        TaskEndPayload(
            task_id="task_1",
            result="success",
            summary="文件已经写入工作区。",
            output="add.py 已创建完成。",
        ),
    )

    assert "已完成" in text
    assert "add.py 已创建完成" in text
