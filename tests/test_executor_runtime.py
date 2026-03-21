from __future__ import annotations

import asyncio

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.commands import ExecutorJobRequestPayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import DeliveryTargetPayload, ExecutorRejectedPayload, ExecutorResultPayload
from emoticorebot.protocol.topics import EventType
from emoticorebot.executor.runtime import ExecutorRuntime


async def _drain(bus: PriorityPubSubBus) -> None:
    await bus.drain()
    await asyncio.sleep(0)
    await bus.drain()


class _SuccessfulExecutor:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def execute(self, task_spec, *, task_id: str):
        self.calls.append({"task_spec": dict(task_spec), "task_id": task_id})
        return {
            "control_state": "completed",
            "status": "success",
            "analysis": "整理完成",
            "message": "产物已生成",
            "task_trace": [],
        }


class _FailingExecutor:
    async def execute(self, task_spec, *, task_id: str):
        del task_spec, task_id
        return {
            "control_state": "failed",
            "status": "failed",
            "analysis": "执行失败",
            "message": "测试失败，需要换路。",
            "task_trace": [],
        }


class _BlockingExecutor:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def execute(self, task_spec, *, task_id: str):
        del task_spec, task_id
        self.started.set()
        await asyncio.Future()


async def _capture(bus: PriorityPubSubBus, event_type: str):
    events: list[BusEnvelope[object]] = []

    async def _handler(event: BusEnvelope[object]) -> None:
        events.append(event)

    bus.subscribe(consumer=f"test:{event_type}", event_type=event_type, handler=_handler)
    return events


async def _exercise_executor_runtime_emits_success_result() -> None:
    bus = PriorityPubSubBus()
    executor = _SuccessfulExecutor()
    runtime = ExecutorRuntime(bus=bus, executor=executor)
    runtime.register()

    results = await _capture(bus, EventType.EXECUTOR_EVENT_RESULT_READY)
    rejected = await _capture(bus, EventType.EXECUTOR_EVENT_JOB_REJECTED)

    await bus.publish(
        build_envelope(
            event_type=EventType.EXECUTOR_COMMAND_JOB_REQUESTED,
            source="session",
            target="executor_runtime",
            session_id="sess_exec_1",
            turn_id="turn_exec_1",
            correlation_id="turn_exec_1",
            payload=ExecutorJobRequestPayload(
                job_id="job_exec_1",
                job_action="execute",
                job_kind="execution_review",
                source_text="帮我整理一下项目结构",
                request_text="检查项目结构并整理要点",
                task_id="task_exec_1",
                goal="整理项目结构",
                mainline=["看问题", "解决问题", "跑测试"],
                current_stage="看问题",
                current_checks=["检查项目结构并整理要点"],
                delivery_target=DeliveryTargetPayload(delivery_mode="push", channel="cli", chat_id="direct"),
                context={
                    "title": "整理项目结构",
                    "recent_turns": [{"role": "user", "content": "帮我整理一下项目结构"}],
                    "short_term_memory": ["用户正在清理旧架构"],
                    "long_term_memory": ["用户偏好模块化实现"],
                },
            ),
        )
    )
    await _drain(bus)

    assert rejected == []
    assert len(results) == 1
    result = results[0].payload
    assert result.decision == "accept"
    assert result.summary == "整理完成"
    assert result.result_text == "产物已生成"
    assert result.metadata["result"] == "success"

    record = runtime.task_store.require("task_exec_1")
    assert record.state.value == "done"
    assert record.result == "success"
    assert record.request.goal == "整理项目结构"
    assert record.request.mainline == ["看问题", "解决问题", "跑测试"]
    assert record.request.current_checks == ["检查项目结构并整理要点"]

    assert len(executor.calls) == 1
    task_spec = executor.calls[0]["task_spec"]
    assert task_spec["goal"] == "整理项目结构"
    assert task_spec["mainline"] == ["看问题", "解决问题", "跑测试"]
    assert task_spec["current_checks"] == ["检查项目结构并整理要点"]


def test_executor_runtime_emits_success_result() -> None:
    asyncio.run(_exercise_executor_runtime_emits_success_result())


async def _exercise_executor_runtime_emits_failure_result() -> None:
    bus = PriorityPubSubBus()
    runtime = ExecutorRuntime(bus=bus, executor=_FailingExecutor())
    runtime.register()

    results = await _capture(bus, EventType.EXECUTOR_EVENT_RESULT_READY)

    await bus.publish(
        build_envelope(
            event_type=EventType.EXECUTOR_COMMAND_JOB_REQUESTED,
            source="session",
            target="executor_runtime",
            session_id="sess_exec_fail",
            turn_id="turn_exec_fail",
            correlation_id="turn_exec_fail",
            payload=ExecutorJobRequestPayload(
                job_id="job_exec_fail",
                job_action="execute",
                request_text="跑测试",
                task_id="task_exec_fail",
                delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct"),
            ),
        )
    )
    await _drain(bus)

    assert len(results) == 1
    assert results[0].payload.metadata["result"] == "failed"
    assert results[0].payload.result_text == "测试失败，需要换路。"
    record = runtime.task_store.require("task_exec_fail")
    assert record.result == "failed"
    assert record.error == "测试失败，需要换路。"


def test_executor_runtime_emits_failure_result() -> None:
    asyncio.run(_exercise_executor_runtime_emits_failure_result())


async def _exercise_executor_runtime_rejects_empty_request() -> None:
    bus = PriorityPubSubBus()
    runtime = ExecutorRuntime(bus=bus, executor=_SuccessfulExecutor())
    runtime.register()

    rejected = await _capture(bus, EventType.EXECUTOR_EVENT_JOB_REJECTED)

    await bus.publish(
        build_envelope(
            event_type=EventType.EXECUTOR_COMMAND_JOB_REQUESTED,
            source="session",
            target="executor_runtime",
            session_id="sess_reject",
            turn_id="turn_reject",
            correlation_id="turn_reject",
            payload=ExecutorJobRequestPayload(
                job_id="job_reject",
                job_action="execute",
                delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct"),
            ),
        )
    )
    await _drain(bus)

    assert len(rejected) == 1
    assert "request_text" in rejected[0].payload.reason


def test_executor_runtime_rejects_empty_request() -> None:
    asyncio.run(_exercise_executor_runtime_rejects_empty_request())


async def _exercise_executor_runtime_cancels_running_task() -> None:
    bus = PriorityPubSubBus()
    executor = _BlockingExecutor()
    runtime = ExecutorRuntime(bus=bus, executor=executor)
    runtime.register()

    results = await _capture(bus, EventType.EXECUTOR_EVENT_RESULT_READY)

    await bus.publish(
        build_envelope(
            event_type=EventType.EXECUTOR_COMMAND_JOB_REQUESTED,
            source="session",
            target="executor_runtime",
            session_id="sess_cancel",
            turn_id="turn_cancel_1",
            correlation_id="turn_cancel_1",
            payload=ExecutorJobRequestPayload(
                job_id="job_cancel_1",
                job_action="execute",
                request_text="运行一个长任务",
                task_id="task_cancel_1",
                delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct"),
            ),
        )
    )
    await _drain(bus)
    await asyncio.wait_for(executor.started.wait(), timeout=1.0)

    await bus.publish(
        build_envelope(
            event_type=EventType.EXECUTOR_COMMAND_JOB_REQUESTED,
            source="session",
            target="executor_runtime",
            session_id="sess_cancel",
            turn_id="turn_cancel_2",
            task_id="task_cancel_1",
            correlation_id="task_cancel_1",
            payload=ExecutorJobRequestPayload(
                job_id="job_cancel_2",
                job_action="cancel",
                task_id="task_cancel_1",
                request_text="别修了，结束当前任务",
                delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct"),
            ),
        )
    )
    await _drain(bus)

    assert len(results) == 1
    assert results[0].payload.metadata["result"] == "cancelled"
    record = runtime.task_store.require("task_cancel_1")
    assert record.result == "cancelled"
    assert record.final_result_text == "别修了，结束当前任务"

    await runtime.stop()


def test_executor_runtime_cancels_running_task() -> None:
    asyncio.run(_exercise_executor_runtime_cancels_running_task())
