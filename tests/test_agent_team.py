from __future__ import annotations

import asyncio

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.execution.team import WorkerAgent
from emoticorebot.protocol.commands import AssignAgentPayload, CancelAgentPayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import TaskCancelledReportPayload, TaskResultReportPayload
from emoticorebot.protocol.task_models import MessageRef, TaskRequestSpec
from emoticorebot.protocol.topics import EventType
from emoticorebot.runtime.state_machine import TaskStatus
from emoticorebot.runtime.task_store import RuntimeTaskRecord, TaskStore


class _SlowExecutor:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def execute(self, _task_spec, *, task_id: str, progress_reporter=None):
        del task_id, progress_reporter
        self.started.set()
        await asyncio.Event().wait()


async def _exercise_worker_cancel_command_stops_active_run() -> None:
    bus = PriorityPubSubBus()
    store = TaskStore()
    store.add(
        RuntimeTaskRecord(
            task_id="task_1",
            session_id="sess_1",
            turn_id="turn_1",
            request=TaskRequestSpec(request="创建 add.py", title="创建 add.py"),
            origin_message=MessageRef(channel="cli", chat_id="direct", message_id="msg_1"),
            title="创建 add.py",
            status=TaskStatus.ASSIGNED,
            assignee="worker",
            current_assignment_id="assign_1",
        )
    )
    worker = WorkerAgent(
        bus,
        store,
        worker_llm=None,
        context_builder=object(),
        tool_registry=None,
    )
    worker._executor = _SlowExecutor()
    worker.register()

    cancelled: list[BusEnvelope[TaskCancelledReportPayload]] = []
    results: list[BusEnvelope[TaskResultReportPayload]] = []

    async def _capture_cancelled(event: BusEnvelope[TaskCancelledReportPayload]) -> None:
        cancelled.append(event)

    async def _capture_result(event: BusEnvelope[TaskResultReportPayload]) -> None:
        results.append(event)

    bus.subscribe(consumer="runtime", event_type=EventType.TASK_REPORT_CANCELLED, handler=_capture_cancelled)
    bus.subscribe(consumer="runtime", event_type=EventType.TASK_REPORT_RESULT, handler=_capture_result)

    await bus.start()
    try:
        await bus.publish(
            build_envelope(
                event_type=EventType.RUNTIME_ASSIGN_AGENT,
                source="runtime",
                target="worker",
                session_id="sess_1",
                turn_id="turn_1",
                task_id="task_1",
                correlation_id="task_1",
                payload=AssignAgentPayload(
                    assignment_id="assign_1",
                    task_id="task_1",
                    agent_role="worker",
                    task_state=store.require("task_1").snapshot(),
                    task_request=store.require("task_1").request,
                ),
            )
        )

        await asyncio.wait_for(worker._executor.started.wait(), timeout=1.0)

        await bus.publish(
            build_envelope(
                event_type=EventType.RUNTIME_CANCEL_AGENT,
                source="runtime",
                target="worker",
                session_id="sess_1",
                turn_id="turn_1",
                task_id="task_1",
                correlation_id="task_1",
                payload=CancelAgentPayload(
                    task_id="task_1",
                    agent_role="worker",
                    reason="interrupt",
                    hard_stop=True,
                ),
            )
        )

        deadline = asyncio.get_running_loop().time() + 1.0
        while not cancelled and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

        assert len(cancelled) == 1
        assert cancelled[0].payload.task_id == "task_1"
        assert results == []
    finally:
        await worker.stop()
        await bus.stop()


def test_worker_cancel_command_stops_active_run() -> None:
    asyncio.run(_exercise_worker_cancel_command_stops_active_run())
