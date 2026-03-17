from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from emoticorebot.bootstrap import RuntimeHost
from emoticorebot.protocol.envelope import build_envelope
from emoticorebot.protocol.events import RepliedPayload, ReplyReadyPayload
from emoticorebot.protocol.task_models import MessageRef, ReplyDraft, TaskRequestSpec
from emoticorebot.protocol.topics import EventType
from emoticorebot.session.thread_store import ThreadStore


class _FakeTask:
    def __init__(self) -> None:
        self.task_id = "task_1"
        self.title = "完成任务"
        self.request = TaskRequestSpec(request="完成任务", title="完成任务")

    def snapshot(self):
        return SimpleNamespace(
            model_dump=lambda exclude_none=True: {
                "task_id": "task_1",
                "title": "完成任务",
                "state": "done",
                "result": "success",
                "summary": "任务已完成",
                "error": "",
                "last_progress": "输出最终结果",
            }
        )


class _FakeKernel:
    def __init__(self) -> None:
        self._task = _FakeTask()
        self.session_runtime = SimpleNamespace(task_view=lambda session_id, task_id: None)

    def get_task(self, task_id: str):
        return self._task if task_id == "task_1" else None

    def latest_task_for_session(self, session_id: str, *, include_terminal: bool = True):
        return self._task if session_id == "sess_1" else None


async def _exercise_task_origin_internal_persistence(tmp_path: Path) -> None:
    host = RuntimeHost.__new__(RuntimeHost)
    host.thread_store = ThreadStore(tmp_path)
    host.kernel = _FakeKernel()
    host._pending_task_origin_replies = {}

    ready = build_envelope(
        event_type=EventType.OUTPUT_REPLY_APPROVED,
        source="brain",
        target="broadcast",
        session_id="sess_1",
        turn_id="turn_task_1",
        task_id="task_1",
        correlation_id="task_1",
        payload=ReplyReadyPayload(
            reply=ReplyDraft(
                reply_id="reply_task_1",
                kind="status",
                plain_text="任务完成了，我把结果整理好了。",
                metadata={
                    "front_origin": "task",
                    "task_event_id": "evt_task_end_1",
                    "task_event_type": EventType.TASK_END,
                },
            ),
            related_task_id="task_1",
            origin_message=MessageRef(channel="cli", chat_id="direct", message_id="msg_1"),
        ),
    )
    replied = build_envelope(
        event_type=EventType.OUTPUT_REPLIED,
        source="delivery",
        target="broadcast",
        session_id="sess_1",
        turn_id="turn_task_1",
        task_id="task_1",
        correlation_id="task_1",
        payload=RepliedPayload(
            reply_id="reply_task_1",
            delivery_message=MessageRef(
                channel="cli",
                chat_id="direct",
                message_id="delivery_reply_task_1",
                reply_to_message_id="msg_1",
            ),
            delivery_mode="chat",
            delivered_at="2026-03-18T12:00:00",
        ),
    )

    await host._remember_task_origin_reply(ready)
    await host._persist_task_origin_internal_reply(replied)

    internal = host.thread_store.get_internal_messages("sess_1")
    thread = host.thread_store.get("sess_1")

    assert len(internal) >= 2
    assert all(record["source"] == "task" for record in internal)
    assert any(RuntimeHost._internal_summary_text(record) for record in internal)
    assert thread is None


def test_runtime_host_persists_task_origin_replies_only_to_internal_history(tmp_path) -> None:
    asyncio.run(_exercise_task_origin_internal_persistence(tmp_path))
