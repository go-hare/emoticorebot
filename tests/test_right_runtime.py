from __future__ import annotations

import asyncio

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.commands import RightBrainJobRequestPayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import (
    RightBrainAcceptedPayload,
    RightBrainProgressPayload,
    RightBrainRejectedPayload,
    RightBrainResultPayload,
)
from emoticorebot.protocol.memory_models import ReflectSignalPayload
from emoticorebot.protocol.topics import EventType
from emoticorebot.right.runtime import RightBrainRuntime
from emoticorebot.right.tool_runtime import ExecutionToolRuntime


async def _drain(bus: PriorityPubSubBus) -> None:
    await bus.drain()
    await asyncio.sleep(0)
    await bus.drain()


class _AcceptingExecutor:
    def __init__(self) -> None:
        self.tool_runtime = ExecutionToolRuntime()
        self.calls: list[dict[str, object]] = []

    async def execute(self, task_spec, *, task_id: str, progress_reporter=None, trace_reporter=None):
        self.calls.append({"task_spec": task_spec, "task_id": task_id})
        await self.tool_runtime.audit(decision="accept", reason="audit_tool 返回任务可以开始。")
        if progress_reporter is not None:
            await progress_reporter(
                "已完成项目扫描，开始整理输出。",
                {
                    "event": "task.tool",
                    "producer": "worker",
                    "phase": "tool",
                    "tool_name": "read_file",
                    "payload": {"progress": 0.35, "next_step": "整理结果"},
                },
            )
        return {
            "control_state": "completed",
            "status": "success",
            "analysis": "整理完成",
            "message": "产物已生成",
            "task_trace": [],
        }


class _TerminalAuditExecutor:
    def __init__(self, *, decision: str, reason: str, summary: str = "", result_text: str = "") -> None:
        self.tool_runtime = ExecutionToolRuntime()
        self._decision = decision
        self._reason = reason
        self._summary = summary
        self._result_text = result_text

    async def execute(self, task_spec, *, task_id: str, progress_reporter=None, trace_reporter=None):
        del task_spec, task_id, progress_reporter, trace_reporter
        await self.tool_runtime.audit(
            decision=self._decision,  # type: ignore[arg-type]
            reason=self._reason,
            summary=self._summary,
            result_text=self._result_text,
        )
        raise AssertionError("terminal audit should interrupt the run")


class _BlockingExecutor:
    def __init__(self) -> None:
        self.tool_runtime = ExecutionToolRuntime()
        self.accepted = asyncio.Event()

    async def execute(self, task_spec, *, task_id: str, progress_reporter=None, trace_reporter=None):
        del task_spec, task_id, progress_reporter, trace_reporter
        await self.tool_runtime.audit(decision="accept", reason="audit_tool 返回任务可以开始。")
        self.accepted.set()
        await asyncio.Future()


async def _capture(bus: PriorityPubSubBus, event_type: str):
    events: list[BusEnvelope[object]] = []

    async def _handler(event: BusEnvelope[object]) -> None:
        events.append(event)

    consumer = "memory_governor" if str(event_type) == str(EventType.REFLECT_LIGHT) else f"test:{event_type}"
    bus.subscribe(consumer=consumer, event_type=event_type, handler=_handler)
    return events


async def _exercise_right_runtime_emits_accept_progress_result() -> None:
    bus = PriorityPubSubBus()
    runtime = RightBrainRuntime(bus=bus, executor=_AcceptingExecutor())
    runtime.register()

    accepted = await _capture(bus, EventType.RIGHT_EVENT_JOB_ACCEPTED)
    progress = await _capture(bus, EventType.RIGHT_EVENT_PROGRESS)
    results = await _capture(bus, EventType.RIGHT_EVENT_RESULT_READY)
    reflections = await _capture(bus, EventType.REFLECT_LIGHT)

    await bus.publish(
        build_envelope(
            event_type=EventType.RIGHT_COMMAND_JOB_REQUESTED,
            source="session",
            target="right_runtime",
            session_id="sess_right_1",
            turn_id="turn_right_1",
            correlation_id="turn_right_1",
            payload=RightBrainJobRequestPayload(
                job_id="job_right_1",
                right_brain_strategy="async",
                job_action="create_task",
                job_kind="execution_review",
                source_text="帮我整理一下项目结构",
                request_text="检查项目结构并整理要点",
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

    assert len(accepted) == 1
    assert accepted[0].payload.reason == "audit_tool 返回任务可以开始。"
    assert accepted[0].payload.metadata["job_kind"] == "execution_review"

    assert len(progress) == 1
    assert progress[0].payload.summary == "已完成项目扫描，开始整理输出。"
    assert progress[0].payload.progress == 0.35
    assert progress[0].payload.next_step == "整理结果"

    assert len(results) == 1
    assert results[0].payload.decision == "accept"
    assert results[0].payload.summary == "整理完成"
    assert results[0].payload.result_text == "产物已生成"
    assert results[0].payload.metadata["result"] == "success"

    task_id = str(results[0].task_id or "")
    record = runtime.task_store.require(task_id)
    assert record.state.value == "done"
    assert record.result == "success"
    assert record.final_decision == "accept"

    assert len(reflections) == 1
    assert reflections[0].payload.reason == "right_brain_result"
    assert "reflection_input" not in reflections[0].payload.metadata
    assert reflections[0].payload.metadata["right_brain_summary"]["recent_turns"][0]["content"] == "帮我整理一下项目结构"
    assert reflections[0].payload.metadata["right_brain_summary"]["tool_usage_summary"][0]["tool_name"] == "read_file"


def test_right_runtime_emits_accept_progress_result() -> None:
    asyncio.run(_exercise_right_runtime_emits_accept_progress_result())


async def _exercise_right_runtime_emits_terminal_audit_decisions() -> None:
    bus = PriorityPubSubBus()
    runtime = RightBrainRuntime(
        bus=bus,
        executor=_TerminalAuditExecutor(
            decision="answer_only",
            reason="这个请求更适合直接回答。",
            summary="右脑返回理性答案素材。",
            result_text="这是给左脑的答案素材。",
        ),
    )
    runtime.register()

    results = await _capture(bus, EventType.RIGHT_EVENT_RESULT_READY)
    reflections = await _capture(bus, EventType.REFLECT_LIGHT)

    await bus.publish(
        build_envelope(
            event_type=EventType.RIGHT_COMMAND_JOB_REQUESTED,
            source="session",
            target="right_runtime",
            session_id="sess_right_2",
            turn_id="turn_right_2",
            correlation_id="turn_right_2",
            payload=RightBrainJobRequestPayload(
                job_id="job_right_2",
                right_brain_strategy="sync",
                job_action="create_task",
                request_text="解释一下当前架构",
            ),
        )
    )
    await _drain(bus)

    assert len(results) == 1
    assert results[0].payload.decision == "answer_only"
    assert results[0].payload.summary == "右脑返回理性答案素材。"
    assert results[0].payload.result_text == "这是给左脑的答案素材。"
    assert len(reflections) == 1
    assert reflections[0].payload.reason == "right_brain_answer_only"


def test_right_runtime_emits_terminal_audit_decisions() -> None:
    asyncio.run(_exercise_right_runtime_emits_terminal_audit_decisions())


async def _exercise_right_runtime_cancels_active_run() -> None:
    bus = PriorityPubSubBus()
    executor = _BlockingExecutor()
    runtime = RightBrainRuntime(bus=bus, executor=executor)
    runtime.register()

    accepted = await _capture(bus, EventType.RIGHT_EVENT_JOB_ACCEPTED)
    results = await _capture(bus, EventType.RIGHT_EVENT_RESULT_READY)

    await bus.publish(
        build_envelope(
            event_type=EventType.RIGHT_COMMAND_JOB_REQUESTED,
            source="session",
            target="right_runtime",
            session_id="sess_right_3",
            turn_id="turn_right_3",
            correlation_id="turn_right_3",
            payload=RightBrainJobRequestPayload(
                job_id="job_right_3",
                right_brain_strategy="async",
                job_action="create_task",
                request_text="启动一个长耗时任务",
            ),
        )
    )
    await _drain(bus)
    await asyncio.wait_for(executor.accepted.wait(), timeout=1.0)
    await _drain(bus)

    assert len(accepted) == 1
    task_id = str(accepted[0].task_id or "")

    await bus.publish(
        build_envelope(
            event_type=EventType.RIGHT_COMMAND_JOB_REQUESTED,
            source="session",
            target="right_runtime",
            session_id="sess_right_3",
            turn_id="turn_right_4",
            correlation_id=task_id,
            task_id=task_id,
            payload=RightBrainJobRequestPayload(
                job_id="job_right_4",
                right_brain_strategy="sync",
                job_action="cancel_task",
                task_id=task_id,
                request_text="用户要求停止",
                context={"reason": "用户要求停止"},
            ),
        )
    )
    await _drain(bus)

    cancelled = [event for event in results if event.payload.metadata.get("result") == "cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0].payload.result_text == "用户要求停止"
    assert runtime.task_store.require(task_id).result == "cancelled"


def test_right_runtime_cancels_active_run() -> None:
    asyncio.run(_exercise_right_runtime_cancels_active_run())
