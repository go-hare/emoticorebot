from __future__ import annotations

import asyncio

from emoticorebot.brain.runtime import BrainRuntime
from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.commands import BrainReplyRequestPayload, ExecutorResultContextPayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import (
    DeliveryTargetPayload,
    BrainReplyReadyPayload,
    BrainStreamDeltaPayload,
    InputSlots,
    TurnInputPayload,
)
from emoticorebot.protocol.task_models import MessageRef, TaskRequestSpec
from emoticorebot.protocol.topics import EventType
from emoticorebot.executor.store import ExecutorRecord, ExecutorStore
from emoticorebot.world_model.schema import WorldModel, WorldTask


class _StreamingBrainLLM:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = list(chunks)

    async def astream(self, _prompt):
        for chunk in self._chunks:
            yield chunk


class _CapturingBrainLLM:
    def __init__(self, response: str | list[str]) -> None:
        self._responses = [response] if isinstance(response, str) else list(response)
        self.prompts = []

    async def ainvoke(self, prompt):
        self.prompts.append(prompt)
        if not self._responses:
            raise AssertionError("unexpected extra brain invocation")
        return self._responses.pop(0)


class _SessionRuntimeStub:
    def __init__(self, model: WorldModel) -> None:
        self._model = model

    def world_model_snapshot(self, _session_id: str) -> WorldModel:
        return self._model


def _brain_reply_request(
    *,
    session_id: str,
    turn_id: str,
    task_id: str | None = None,
    turn_input: TurnInputPayload | None = None,
    executor_result: ExecutorResultContextPayload | None = None,
) -> BusEnvelope[BrainReplyRequestPayload]:
    return build_envelope(
        event_type=EventType.BRAIN_COMMAND_REPLY_REQUESTED,
        source="session",
        target="brain_runtime",
        session_id=session_id,
        turn_id=turn_id,
        task_id=task_id,
        correlation_id=task_id or turn_id,
        payload=BrainReplyRequestPayload(
            request_id=f"brain_req_{turn_id}",
            turn_input=turn_input,
            executor_result=executor_result,
        ),
    )


def _store() -> ExecutorStore:
    store = ExecutorStore()
    store.add(
        ExecutorRecord(
            task_id="task_exec_1",
            session_id="sess_exec_1",
            turn_id="turn_exec_1",
            job_id="job_exec_1",
            request=TaskRequestSpec(
                request="整理模块",
                title="整理模块",
                goal="整理模块",
                mainline=["看问题", "修问题", "跑测试"],
                current_stage="看问题",
                current_checks=["读取错误日志"],
            ),
            title="整理模块",
            origin_message=MessageRef(channel="cli", chat_id="direct", message_id="msg_exec_1"),
            delivery_target=DeliveryTargetPayload(delivery_mode="push", channel="telegram", chat_id="123456"),
        )
    )
    return store


async def _drain(bus: PriorityPubSubBus) -> None:
    await bus.drain()
    await asyncio.sleep(0)
    await bus.drain()


async def _exercise_brain_runtime_emits_execute_request_for_user_turn() -> None:
    bus = PriorityPubSubBus()
    brain_runtime = BrainRuntime(
        bus=bus,
        task_store=ExecutorStore(),
        brain_llm=_CapturingBrainLLM(
            (
                "#####user######\n我先检查一下这个问题。\n\n"
                "#####Action######\n"
                "{\"type\":\"execute\",\"task_id\":\"new\",\"goal\":\"修复 bug\","
                "\"mainline\":[\"看问题\",\"解决问题\",\"跑测试\"],"
                "\"current_stage\":\"看问题\",\"current_checks\":[\"读取错误日志并定位问题\"]}"
            )
        ),
        session_runtime=_SessionRuntimeStub(WorldModel(session_id="sess_user_turn")),
    )
    brain_runtime.register()

    replies: list[BusEnvelope[BrainReplyReadyPayload]] = []

    async def _capture_reply(event: BusEnvelope[BrainReplyReadyPayload]) -> None:
        replies.append(event)

    bus.subscribe(consumer="test:user-turn", event_type=EventType.BRAIN_EVENT_REPLY_READY, handler=_capture_reply)

    await bus.publish(
        _brain_reply_request(
            session_id="sess_user_turn",
            turn_id="turn_user_turn",
            turn_input=TurnInputPayload(
                input_id="turn_user_turn",
                input_mode="turn",
                session_mode="turn_chat",
                channel_kind="chat",
                input_kind="text",
                message=MessageRef(channel="cli", chat_id="direct", message_id="msg_user_turn"),
                user_text="帮我修一下这个 bug",
                input_slots=InputSlots(),
            ),
        )
    )
    await _drain(bus)

    assert len(replies) == 1
    reply = replies[0].payload
    assert reply.reply_text == "我先检查一下这个问题。"
    assert reply.invoke_executor is True
    assert len(reply.executor_requests) == 1
    assert reply.executor_requests[0]["goal"] == "修复 bug"
    assert reply.executor_requests[0]["current_checks"] == ["读取错误日志并定位问题"]
    assert reply.executor_requests[0]["mainline"] == ["看问题", "解决问题", "跑测试"]


def test_brain_runtime_emits_execute_request_for_user_turn() -> None:
    asyncio.run(_exercise_brain_runtime_emits_execute_request_for_user_turn())


async def _exercise_brain_runtime_redecides_after_executor_result() -> None:
    bus = PriorityPubSubBus()
    brain_runtime = BrainRuntime(
        bus=bus,
        task_store=_store(),
        brain_llm=_CapturingBrainLLM(
            (
                "#####user######\n这一步失败了，我换一个检查项继续。\n\n"
                "#####Action######\n"
                "[{\"type\":\"execute\",\"task_id\":\"task_exec_1\",\"goal\":\"整理模块\","
                "\"mainline\":[\"看问题\",\"修问题\",\"跑测试\"],"
                "\"current_stage\":\"看问题\",\"current_checks\":[\"读取新的错误日志并定位问题\"]}]"
            )
        ),
        session_runtime=_SessionRuntimeStub(
            WorldModel(
                session_id="sess_exec_1",
                current_task=WorldTask(task_id="task_exec_1", goal="整理模块", current_checks=["读取错误日志"]),
            )
        ),
    )
    brain_runtime.register()

    replies: list[BusEnvelope[BrainReplyReadyPayload]] = []

    async def _capture_reply(event: BusEnvelope[BrainReplyReadyPayload]) -> None:
        replies.append(event)

    bus.subscribe(consumer="test:executor-result", event_type=EventType.BRAIN_EVENT_REPLY_READY, handler=_capture_reply)

    await bus.publish(
        _brain_reply_request(
            session_id="sess_exec_1",
            turn_id="turn_exec_2",
            task_id="task_exec_1",
            executor_result=ExecutorResultContextPayload(
                source_event=EventType.EXECUTOR_EVENT_RESULT_READY,
                job_id="job_exec_1",
                decision="accept",
                summary="当前 check 失败了。",
                result_text="pytest 失败，先看错误日志。",
                delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct"),
                metadata={"result": "failed"},
            ),
        )
    )
    await _drain(bus)

    assert len(replies) == 1
    reply = replies[0].payload
    assert reply.reply_text == "这一步失败了，我换一个检查项继续。"
    assert reply.reply_kind == "status"
    assert reply.invoke_executor is True
    assert reply.executor_requests[0]["task_id"] == "task_exec_1"
    assert reply.executor_requests[0]["current_stage"] == "看问题"
    assert reply.executor_requests[0]["current_checks"] == ["读取新的错误日志并定位问题"]
    assert reply.metadata["brain_source"] == "executor_result"
    assert reply.metadata["source_event"] == EventType.EXECUTOR_EVENT_RESULT_READY


def test_brain_runtime_redecides_after_executor_result() -> None:
    asyncio.run(_exercise_brain_runtime_redecides_after_executor_result())


async def _exercise_brain_runtime_streams_user_reply() -> None:
    bus = PriorityPubSubBus()
    brain_runtime = BrainRuntime(
        bus=bus,
        task_store=ExecutorStore(),
        brain_llm=_StreamingBrainLLM(
            [
                "#####user######\n我",
                "先帮你看一下。",
                "\n\n#####Action######\n{\"type\":\"none\"}",
            ]
        ),
        session_runtime=_SessionRuntimeStub(WorldModel(session_id="sess_stream")),
    )
    brain_runtime.register()

    deltas: list[BusEnvelope[BrainStreamDeltaPayload]] = []
    replies: list[BusEnvelope[BrainReplyReadyPayload]] = []

    async def _capture_delta(event: BusEnvelope[BrainStreamDeltaPayload]) -> None:
        deltas.append(event)

    async def _capture_reply(event: BusEnvelope[BrainReplyReadyPayload]) -> None:
        replies.append(event)

    bus.subscribe(consumer="test:delta", event_type=EventType.BRAIN_EVENT_STREAM_DELTA_READY, handler=_capture_delta)
    bus.subscribe(consumer="test:reply", event_type=EventType.BRAIN_EVENT_REPLY_READY, handler=_capture_reply)

    await bus.publish(
        _brain_reply_request(
            session_id="sess_stream",
            turn_id="turn_stream",
            turn_input=TurnInputPayload(
                input_id="turn_stream",
                input_mode="turn",
                session_mode="turn_chat",
                channel_kind="chat",
                input_kind="text",
                message=MessageRef(channel="cli", chat_id="direct", message_id="msg_stream"),
                user_text="你好呀",
                input_slots=InputSlots(),
                metadata={
                    "source_input_mode": "stream",
                    "current_delivery_mode": "stream",
                    "available_delivery_modes": ["stream", "inline"],
                },
            ),
        )
    )
    await _drain(bus)

    assert deltas
    assert "".join(event.payload.delta_text for event in deltas).strip() == "我先帮你看一下。"
    assert len(replies) == 1
    assert replies[0].payload.reply_text == "我先帮你看一下。"
    assert replies[0].payload.stream_state == "close"


def test_brain_runtime_streams_user_reply() -> None:
    asyncio.run(_exercise_brain_runtime_streams_user_reply())
