from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from emoticorebot.bootstrap import RuntimeHost
from emoticorebot.protocol.commands import RightBrainJobRequestPayload
from emoticorebot.protocol.envelope import build_envelope
from emoticorebot.protocol.events import DeliveryTargetPayload, RightBrainProgressPayload, RightBrainResultPayload
from emoticorebot.protocol.topics import EventType
from emoticorebot.runtime.transport_bus import TransportBus
from emoticorebot.session.models import SessionTaskView, SessionTraceRecord
from emoticorebot.session.thread_store import ThreadStore


def test_runtime_host_uses_configured_heartbeat_interval(tmp_path) -> None:
    host = RuntimeHost.__new__(RuntimeHost)
    host.workspace = Path(tmp_path)
    host.tool_manager = SimpleNamespace(cron_service=None)
    host.bus = TransportBus()
    host.subconscious = None
    host.heartbeat = None

    host.initialize_subconscious(
        enable_reflection=False,
        enable_heartbeat=True,
        heartbeat_interval_s=123,
    )

    assert host.heartbeat is not None
    assert host.heartbeat.interval_s == 123


def test_runtime_host_build_right_turn_records_reads_real_turn_history(tmp_path) -> None:
    host = RuntimeHost.__new__(RuntimeHost)
    host.thread_store = ThreadStore(tmp_path)

    host.thread_store.append_right_messages(
        "cli:direct",
        [
            {
                "role": "assistant",
                "turn_id": "turn_keep",
                "content": "准备调用工具：write_file",
                "event_type": "progress",
            },
            {
                "role": "assistant",
                "turn_id": "turn_other",
                "content": "不属于当前轮",
                "event_type": "result_ready",
            },
        ],
    )

    records = host._build_right_turn_records(
        {"task": {"task_id": "task_1", "state": "running", "result": "none"}},
        session_id="cli:direct",
        user_id="user",
        turn_id="turn_keep",
        assistant_timestamp="2026-03-19T22:00:00",
        message_id="msg_1",
    )

    assert len(records) == 1
    assert records[0]["content"] == "准备调用工具：write_file"
    assert records[0]["event_type"] == "progress"


def test_runtime_host_persists_progress_as_clean_update(tmp_path) -> None:
    host = RuntimeHost.__new__(RuntimeHost)
    host.thread_store = ThreadStore(tmp_path)

    event = build_envelope(
        event_type=EventType.RIGHT_EVENT_PROGRESS,
        source="right_runtime",
        target="broadcast",
        session_id="cli:direct",
        turn_id="turn_1",
        task_id="task_1",
        correlation_id="task_1",
        payload=RightBrainProgressPayload(
            job_id="job_1",
            decision="accept",
            stage="execute",
            summary="准备调用工具：write_file",
            delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct"),
            metadata={
                "event": "task.trace",
                "payload": {
                    "role": "assistant",
                    "tool_calls": [{"name": "write_file"}],
                },
            },
        ),
    )

    asyncio.run(host._persist_right_history_event(event))

    right_history = host.thread_store.get_right_messages("cli:direct")

    assert len(right_history) == 1
    assert right_history[0]["event_type"] == "progress"
    assert right_history[0]["content"] == "准备调用工具：write_file"
    assert right_history[0]["metadata"]["update"]["kind"] == "message"
    assert right_history[0]["metadata"]["update"]["tool_name"] == "write_file"
    assert "task" not in right_history[0]["metadata"]
    assert "payload" not in right_history[0]["metadata"]


def test_runtime_host_persists_job_requested_as_first_right_update(tmp_path) -> None:
    host = RuntimeHost.__new__(RuntimeHost)
    host.thread_store = ThreadStore(tmp_path)

    event = build_envelope(
        event_type=EventType.RIGHT_COMMAND_JOB_REQUESTED,
        source="session",
        target="right_runtime",
        session_id="cli:direct",
        turn_id="turn_1",
        correlation_id="turn_1",
        payload=RightBrainJobRequestPayload(
            job_id="job_1",
            job_action="create_task",
            job_kind="execution_review",
            request_text="创建 add11.py",
            delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct"),
        ),
    )

    asyncio.run(host._persist_right_history_event(event))

    right_history = host.thread_store.get_right_messages("cli:direct")

    assert len(right_history) == 1
    assert right_history[0]["event_type"] == "job_requested"
    assert right_history[0]["content"] == "创建 add11.py"
    assert right_history[0]["metadata"]["job"]["job_action"] == "create_task"
    assert right_history[0]["metadata"]["job"]["job_kind"] == "execution_review"


def test_runtime_host_skips_execution_result_schema_trace_in_history(tmp_path) -> None:
    host = RuntimeHost.__new__(RuntimeHost)
    host.thread_store = ThreadStore(tmp_path)

    event = build_envelope(
        event_type=EventType.RIGHT_EVENT_PROGRESS,
        source="right_runtime",
        target="broadcast",
        session_id="cli:direct",
        turn_id="turn_1",
        task_id="task_1",
        correlation_id="task_1",
        payload=RightBrainProgressPayload(
            job_id="job_1",
            decision="accept",
            stage="execute",
            summary="ExecutionResultSchema 返回：Returning structured response",
            delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct"),
            metadata={
                "event": "task.trace",
                "payload": {
                    "role": "tool",
                    "name": "ExecutionResultSchema",
                },
            },
        ),
    )

    asyncio.run(host._persist_right_history_event(event))

    assert host.thread_store.get_right_messages("cli:direct") == []


def test_runtime_host_persists_real_right_result_to_history(tmp_path) -> None:
    host = RuntimeHost.__new__(RuntimeHost)
    host.thread_store = ThreadStore(tmp_path)

    task = SimpleNamespace(
        task_id="task_1",
        request=SimpleNamespace(model_dump=lambda **_: {"task_id": "task_1", "request": "创建 add10.py"}),
    )
    task_view = SessionTaskView(
        task_id="task_1",
        title="创建 add10.py",
        request="创建 add10.py",
        state="done",
        result="success",
        summary="已成功创建 add10.py 文件",
        updated_at="2026-03-19T22:01:00",
        trace=[
            SessionTraceRecord(
                trace_id="trace_1",
                task_id="task_1",
                kind="summary",
                message="已成功创建 add10.py 文件",
                ts="2026-03-19T22:01:00",
                data={"source_event": str(EventType.RIGHT_EVENT_RESULT_READY)},
            )
        ],
    )
    host.kernel = SimpleNamespace(
        get_task=lambda task_id: task if task_id == "task_1" else None,
        latest_task_for_session=lambda session_id, include_terminal=True: task,
        session_runtime=SimpleNamespace(task_view=lambda session_id, task_id: task_view if task_id == "task_1" else None),
    )

    event = build_envelope(
        event_type=EventType.RIGHT_EVENT_RESULT_READY,
        source="right_runtime",
        target="broadcast",
        session_id="cli:direct",
        turn_id="turn_1",
        task_id="task_1",
        correlation_id="task_1",
        payload=RightBrainResultPayload(
            job_id="job_1",
            decision="accept",
            summary="已成功创建 add10.py 文件",
            result_text="已成功创建 add10.py 文件",
            delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct"),
            metadata={"result": "success"},
        ),
    )

    asyncio.run(host._persist_right_history_event(event))

    right_history = host.thread_store.get_right_messages("cli:direct")

    assert len(right_history) == 1
    assert right_history[0]["event_type"] == "result_ready"
    assert right_history[0]["content"] == "已成功创建 add10.py 文件"
    assert right_history[0]["metadata"]["source_event"] == str(EventType.RIGHT_EVENT_RESULT_READY)
    assert right_history[0]["metadata"]["task"]["state"] == "done"
    assert right_history[0]["metadata"]["task"]["result"] == "success"
    assert "task_trace" not in right_history[0]["metadata"]["task"]
