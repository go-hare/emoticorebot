from __future__ import annotations

from emoticorebot.left_brain.reply_policy import ReplyPolicy
from emoticorebot.protocol.events import DeliveryTargetPayload
from emoticorebot.protocol.task_models import TaskRequestSpec
from emoticorebot.right_brain.state import RightBrainState
from emoticorebot.right_brain.store import RightBrainRecord


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
        delivery_target=DeliveryTargetPayload(delivery_mode="push", channel="cli", chat_id="direct"),
        state=state,
        result=result,
        summary=summary,
        last_progress=last_progress,
    )


def test_reply_policy_formats_running_and_failed_status() -> None:
    running = ReplyPolicy.status(_task(last_progress="正在执行内部任务"))
    failed = ReplyPolicy.status(_task(state=RightBrainState.DONE, result="failed", summary="命令执行失败"))

    assert "执行中" in running
    assert "正在执行内部任务" in running
    assert "失败" in failed


def test_reply_policy_formats_right_brain_messages() -> None:
    accepted = ReplyPolicy.right_brain_accepted(_task(), reason="audit_tool 返回任务可以开始。")
    progress = ReplyPolicy.right_brain_progress(_task(), summary="已完成扫描。", next_step="整理输出")
    result = ReplyPolicy.right_brain_result(
        _task(state=RightBrainState.DONE, result="success"),
        decision="accept",
        summary="文件已经写入工作区。",
        result_text="add.py 已创建完成。",
        outcome="success",
    )

    assert "已开始处理" in accepted
    assert "整理输出" in progress
    assert "已完成" in result
