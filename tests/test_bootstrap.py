from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from emoticorebot.bootstrap import RuntimeHost
from emoticorebot.protocol.envelope import build_envelope
from emoticorebot.protocol.events import DeliveryTargetPayload, ExecutorResultPayload
from emoticorebot.protocol.topics import EventType
from emoticorebot.runtime.transport_bus import InboundMessage, TransportBus
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


def test_runtime_host_build_executor_turn_records_reads_real_turn_history(tmp_path) -> None:
    host = RuntimeHost.__new__(RuntimeHost)
    host.thread_store = ThreadStore(tmp_path)

    host.thread_store.append_executor_messages(
        "cli:direct",
        [
            {
                "role": "assistant",
                "turn_id": "turn_keep",
                "content": "已成功创建 add10.py 文件",
                "event_type": "result_ready",
            },
            {
                "role": "assistant",
                "turn_id": "turn_other",
                "content": "不属于当前轮",
                "event_type": "result_ready",
            },
        ],
    )

    records = host._build_executor_turn_records(
        {"task": {"task_id": "task_1", "state": "running", "result": "none"}},
        session_id="cli:direct",
        user_id="user",
        turn_id="turn_keep",
        assistant_timestamp="2026-03-19T22:00:00",
        message_id="msg_1",
    )

    assert len(records) == 1
    assert records[0]["content"] == "已成功创建 add10.py 文件"
    assert records[0]["event_type"] == "result_ready"


def test_runtime_host_persists_terminal_result_as_clean_update(tmp_path) -> None:
    host = RuntimeHost.__new__(RuntimeHost)
    host.thread_store = ThreadStore(tmp_path)
    host.kernel = SimpleNamespace(
        get_task=lambda task_id: None,
        latest_task_for_session=lambda session_id, include_terminal=True: None,
    )

    event = build_envelope(
        event_type=EventType.EXECUTOR_EVENT_RESULT_READY,
        source="executor_runtime",
        target="broadcast",
        session_id="cli:direct",
        turn_id="turn_1",
        task_id="task_1",
        correlation_id="task_1",
        payload=ExecutorResultPayload(
            job_id="job_1",
            decision="accept",
            summary="已成功创建 add10.py 文件",
            result_text="已成功创建 add10.py 文件",
            delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct"),
            metadata={"result": "success"},
        ),
    )

    asyncio.run(host._persist_executor_history_event(event))

    executor_history = host.thread_store.get_executor_messages("cli:direct")

    assert len(executor_history) == 1
    assert executor_history[0]["event_type"] == "result_ready"
    assert executor_history[0]["content"] == "已成功创建 add10.py 文件"
    assert "task" not in executor_history[0]["metadata"]


def test_runtime_host_does_not_persist_job_requested_to_executor_history(tmp_path) -> None:
    host = RuntimeHost.__new__(RuntimeHost)
    host.thread_store = ThreadStore(tmp_path)

    executor_history = host.thread_store.get_executor_messages("cli:direct")
    assert executor_history == []


def test_runtime_host_persists_real_executor_result_to_history(tmp_path) -> None:
    host = RuntimeHost.__new__(RuntimeHost)
    host.thread_store = ThreadStore(tmp_path)

    task = SimpleNamespace(
        task_id="task_1",
        state=SimpleNamespace(value="done"),
        result="success",
        state_version=1,
        title="创建 add10.py",
        summary="已成功创建 add10.py 文件",
        error="",
        updated_at="2026-03-19T22:01:00",
        trace_log=[
            {
                "trace_id": "trace_1",
                "task_id": "task_1",
                "kind": "summary",
                "message": "已成功创建 add10.py 文件",
                "ts": "2026-03-19T22:01:00",
                "data": {"source_event": str(EventType.EXECUTOR_EVENT_RESULT_READY)},
            }
        ],
        request=SimpleNamespace(model_dump=lambda **_: {"task_id": "task_1", "request": "创建 add10.py"}),
    )
    host.kernel = SimpleNamespace(
        get_task=lambda task_id: task if task_id == "task_1" else None,
        latest_task_for_session=lambda session_id, include_terminal=True: task,
    )

    event = build_envelope(
        event_type=EventType.EXECUTOR_EVENT_RESULT_READY,
        source="executor_runtime",
        target="broadcast",
        session_id="cli:direct",
        turn_id="turn_1",
        task_id="task_1",
        correlation_id="task_1",
        payload=ExecutorResultPayload(
            job_id="job_1",
            decision="accept",
            summary="已成功创建 add10.py 文件",
            result_text="已成功创建 add10.py 文件",
            delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct"),
            metadata={"result": "success"},
        ),
    )

    asyncio.run(host._persist_executor_history_event(event))

    executor_history = host.thread_store.get_executor_messages("cli:direct")

    assert len(executor_history) == 1
    assert executor_history[0]["event_type"] == "result_ready"
    assert executor_history[0]["content"] == "已成功创建 add10.py 文件"
    assert executor_history[0]["metadata"]["source_event"] == str(EventType.EXECUTOR_EVENT_RESULT_READY)
    assert executor_history[0]["metadata"]["task"]["state"] == "done"
    assert executor_history[0]["metadata"]["task"]["result"] == "success"
    assert "task_trace" not in executor_history[0]["metadata"]["task"]


def test_runtime_host_persists_user_message_even_if_turn_fails(tmp_path) -> None:
    host = RuntimeHost.__new__(RuntimeHost)
    host.thread_store = ThreadStore(tmp_path)
    host.tool_manager = SimpleNamespace(set_context=lambda *args, **kwargs: None)
    host.context = SimpleNamespace(build_media_context=lambda media: [])
    host._snapshot_turn_input = lambda session_id: ([], [])

    async def _fail_run_user_message(**_kwargs):
        raise RuntimeError("boom")

    host._run_user_message = _fail_run_user_message

    message = InboundMessage(
        channel="cli",
        sender_id="user",
        chat_id="direct",
        content="先把这句记下来",
        metadata={"message_id": "msg_fail"},
    )

    try:
        asyncio.run(host._process_message(message, session_key="cli:direct"))
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("expected _process_message to surface failure")

    thread = host.thread_store.get_or_create("cli:direct")
    assert len(thread.messages) == 1
    assert thread.messages[0]["role"] == "user"
    assert thread.messages[0]["content"] == "先把这句记下来"
    assert thread.messages[0]["message_id"] == "msg_fail"
