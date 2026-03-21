from __future__ import annotations

import asyncio

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.executor.store import ExecutorRecord, ExecutorStore
from emoticorebot.protocol.commands import BrainReplyRequestPayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import (
    DeliveryTargetPayload,
    BrainReplyReadyPayload,
    ExecutorResultPayload,
    TurnInputPayload,
)
from emoticorebot.protocol.task_models import MessageRef, TaskRequestSpec
from emoticorebot.protocol.topics import EventType
from emoticorebot.session.runtime import SessionRuntime
from emoticorebot.world_model.store import WorldModelStore


async def _drain(bus: PriorityPubSubBus) -> None:
    await bus.drain()
    await asyncio.sleep(0)
    await bus.drain()


async def _exercise_session_runtime_routes_turn_to_brain(tmp_path) -> None:
    bus = PriorityPubSubBus()
    session = SessionRuntime(bus=bus, task_store=ExecutorStore(), world_model_store=WorldModelStore(tmp_path))
    session.register()

    requests: list[BusEnvelope[BrainReplyRequestPayload]] = []

    async def _capture(event: BusEnvelope[BrainReplyRequestPayload]) -> None:
        requests.append(event)

    bus.subscribe(consumer="brain_runtime", event_type=EventType.BRAIN_COMMAND_REPLY_REQUESTED, handler=_capture)

    await bus.publish(
        build_envelope(
            event_type=EventType.INPUT_TURN_RECEIVED,
            source="input_normalizer",
            target="broadcast",
            session_id="sess_turn",
            turn_id="turn_turn",
            correlation_id="turn_turn",
            payload=TurnInputPayload(
                input_id="turn_turn",
                input_mode="turn",
                session_mode="turn_chat",
                channel_kind="chat",
                input_kind="text",
                message=MessageRef(channel="cli", chat_id="direct", message_id="msg_turn"),
                user_text="你好呀",
            ),
        )
    )
    await _drain(bus)

    assert len(requests) == 1
    assert requests[0].payload.turn_input is not None
    assert requests[0].payload.executor_result is None
    assert requests[0].payload.turn_input.user_text == "你好呀"


def test_session_runtime_routes_turn_to_brain(tmp_path) -> None:
    asyncio.run(_exercise_session_runtime_routes_turn_to_brain(tmp_path))


async def _exercise_session_runtime_dispatches_single_execute_request(tmp_path) -> None:
    bus = PriorityPubSubBus()
    session = SessionRuntime(bus=bus, task_store=ExecutorStore(), world_model_store=WorldModelStore(tmp_path))
    session.register()

    job_requests: list[BusEnvelope[object]] = []

    async def _capture(event: BusEnvelope[object]) -> None:
        job_requests.append(event)

    bus.subscribe(consumer="executor_runtime", event_type=EventType.EXECUTOR_COMMAND_JOB_REQUESTED, handler=_capture)

    await bus.publish(
        build_envelope(
            event_type=EventType.BRAIN_EVENT_REPLY_READY,
            source="brain_runtime",
            target="broadcast",
            session_id="sess_single",
            turn_id="turn_single",
            correlation_id="turn_single",
            payload=BrainReplyReadyPayload(
                request_id="brain_reply_single",
                reply_text="我先看错误日志。",
                reply_kind="status",
                delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct"),
                invoke_executor=True,
                executor_requests=[
                    {
                        "job_id": "job_single_1",
                        "job_action": "execute",
                        "request_text": "检查错误日志",
                        "goal": "检查日志",
                        "mainline": ["看问题", "收口"],
                        "current_stage": "看问题",
                        "current_checks": ["检查错误日志"],
                        "delivery_target": {"delivery_mode": "inline", "channel": "cli", "chat_id": "direct"},
                        "context": {},
                    }
                ],
            ),
        )
    )
    await _drain(bus)

    assert len(job_requests) == 1
    task_id = str(job_requests[0].payload.task_id or "")
    assert task_id
    assert str(job_requests[0].payload.request_text or "") == "检查错误日志"

    world_model = session.world_model_snapshot("sess_single")
    assert world_model.current_topic == "检查日志"
    assert world_model.current_task is not None
    assert world_model.current_task.task_id == task_id
    assert world_model.current_task.goal == "检查日志"
    assert world_model.current_task.current_batch_id == "job_single_1"
    assert [item.title for item in world_model.current_task.current_checks] == ["检查错误日志"]
    assert [item.status for item in world_model.current_task.current_checks] == ["pending"]


def test_session_runtime_dispatches_single_execute_request(tmp_path) -> None:
    asyncio.run(_exercise_session_runtime_dispatches_single_execute_request(tmp_path))


async def _exercise_session_runtime_routes_executor_result_back_to_brain(tmp_path) -> None:
    bus = PriorityPubSubBus()
    store = ExecutorStore()
    store.add(
        ExecutorRecord(
            task_id="task_result_1",
            session_id="sess_result",
            turn_id="turn_result",
            job_id="job_result_1",
            request=TaskRequestSpec(request="跑测试", goal="修复 bug", current_checks=["跑测试"]),
            title="修复 bug",
            origin_message=MessageRef(channel="cli", chat_id="direct", message_id="msg_result"),
            delivery_target=DeliveryTargetPayload(delivery_mode="push", channel="telegram", chat_id="123456"),
        )
    )
    session = SessionRuntime(bus=bus, task_store=store, world_model_store=WorldModelStore(tmp_path))
    session.register()

    brain_requests: list[BusEnvelope[BrainReplyRequestPayload]] = []

    async def _capture(event: BusEnvelope[BrainReplyRequestPayload]) -> None:
        brain_requests.append(event)

    bus.subscribe(consumer="brain_runtime", event_type=EventType.BRAIN_COMMAND_REPLY_REQUESTED, handler=_capture)

    await bus.publish(
        build_envelope(
            event_type=EventType.EXECUTOR_EVENT_RESULT_READY,
            source="executor_runtime",
            target="broadcast",
            session_id="sess_result",
            turn_id="turn_result",
            task_id="task_result_1",
            correlation_id="task_result_1",
            payload=ExecutorResultPayload(
                job_id="job_result_1",
                decision="accept",
                summary="测试失败，准备换路。",
                result_text="pytest 失败，下一步看日志。",
                delivery_target=DeliveryTargetPayload(delivery_mode="push", channel="telegram", chat_id="123456"),
                metadata={"result": "failed"},
            ),
        )
    )
    await _drain(bus)

    assert len(brain_requests) == 1
    request = brain_requests[0].payload
    assert request.turn_input is None
    assert request.executor_result is not None
    assert request.executor_result.source_event == EventType.EXECUTOR_EVENT_RESULT_READY
    assert request.executor_result.summary == "测试失败，准备换路。"
    assert request.executor_result.delivery_target.chat_id == "123456"

    world_model = session.world_model_snapshot("sess_result")
    assert world_model.current_task is not None
    assert world_model.current_task.task_id == "task_result_1"
    assert world_model.current_task.current_batch_id == "job_result_1"
    assert [item.title for item in world_model.current_task.current_checks] == ["跑测试"]
    assert [item.status for item in world_model.current_task.current_checks] == ["failed"]
    assert world_model.current_task.current_checks[0].error == "测试失败，准备换路。"


def test_session_runtime_routes_executor_result_back_to_brain(tmp_path) -> None:
    asyncio.run(_exercise_session_runtime_routes_executor_result_back_to_brain(tmp_path))


async def _exercise_session_runtime_finalizes_task_after_executor_result_reply(tmp_path) -> None:
    bus = PriorityPubSubBus()
    session = SessionRuntime(bus=bus, task_store=ExecutorStore(), world_model_store=WorldModelStore(tmp_path))
    session.register()

    await bus.publish(
        build_envelope(
            event_type=EventType.BRAIN_EVENT_REPLY_READY,
            source="brain_runtime",
            target="broadcast",
            session_id="sess_finish",
            turn_id="turn_finish_1",
            correlation_id="turn_finish_1",
            payload=BrainReplyReadyPayload(
                request_id="brain_reply_finish_1",
                reply_text="我先开始处理。",
                reply_kind="status",
                delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct"),
                invoke_executor=True,
                executor_requests=[
                    {
                        "job_id": "job_finish_1",
                        "job_action": "execute",
                        "task_id": "task_finish_1",
                        "goal": "修复 bug",
                        "current_checks": ["读取错误日志"],
                        "delivery_target": {"delivery_mode": "inline", "channel": "cli", "chat_id": "direct"},
                        "context": {},
                    }
                ],
                related_task_id="task_finish_1",
            ),
        )
    )
    await _drain(bus)

    mid_model = session.world_model_snapshot("sess_finish")
    assert mid_model.current_task is not None
    assert mid_model.current_task.task_id == "task_finish_1"

    await bus.publish(
        build_envelope(
            event_type=EventType.BRAIN_EVENT_REPLY_READY,
            source="brain_runtime",
            target="broadcast",
            session_id="sess_finish",
            turn_id="turn_finish_2",
            task_id="task_finish_1",
            correlation_id="task_finish_1",
            payload=BrainReplyReadyPayload(
                request_id="brain_reply_finish_2",
                reply_text="这件事先收口。",
                reply_kind="answer",
                delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct"),
                invoke_executor=False,
                related_task_id="task_finish_1",
                metadata={
                    "brain_source": "executor_result",
                    "source_event": EventType.EXECUTOR_EVENT_RESULT_READY,
                    "source_decision": "accept",
                    "job_id": "job_finish_1",
                },
            ),
        )
    )
    await _drain(bus)

    final_model = session.world_model_snapshot("sess_finish")
    assert final_model.current_task is None


def test_session_runtime_finalizes_task_after_executor_result_reply(tmp_path) -> None:
    asyncio.run(_exercise_session_runtime_finalizes_task_after_executor_result_reply(tmp_path))


async def _exercise_session_runtime_executor_result_new_task_replaces_current_task(tmp_path) -> None:
    bus = PriorityPubSubBus()
    session = SessionRuntime(bus=bus, task_store=ExecutorStore(), world_model_store=WorldModelStore(tmp_path))
    session.register()

    job_requests: list[BusEnvelope[object]] = []

    async def _capture(event: BusEnvelope[object]) -> None:
        job_requests.append(event)

    bus.subscribe(consumer="executor_runtime", event_type=EventType.EXECUTOR_COMMAND_JOB_REQUESTED, handler=_capture)

    await bus.publish(
        build_envelope(
            event_type=EventType.BRAIN_EVENT_REPLY_READY,
            source="brain_runtime",
            target="broadcast",
            session_id="sess_executor_result_new",
            turn_id="turn_seed_old",
            task_id="task_old",
            correlation_id="task_old",
            payload=BrainReplyReadyPayload(
                request_id="brain_reply_seed_old",
                reply_text="先继续旧任务。",
                reply_kind="status",
                delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct"),
                invoke_executor=True,
                related_task_id="task_old",
                executor_requests=[
                    {
                        "job_id": "job_seed_old",
                        "job_action": "execute",
                        "task_id": "task_old",
                        "goal": "修旧问题",
                        "current_checks": ["补最后一个验证"],
                        "delivery_target": {"delivery_mode": "inline", "channel": "cli", "chat_id": "direct"},
                        "context": {},
                    },
                ],
            ),
        )
    )
    await _drain(bus)

    await bus.publish(
        build_envelope(
            event_type=EventType.BRAIN_EVENT_REPLY_READY,
            source="brain_runtime",
            target="broadcast",
            session_id="sess_executor_result_new",
            turn_id="turn_executor_result_new",
            task_id="task_old",
            correlation_id="task_old",
            payload=BrainReplyReadyPayload(
                request_id="brain_reply_executor_result_new",
                reply_text="旧任务收口，我切到一个新任务。",
                reply_kind="status",
                delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct"),
                invoke_executor=True,
                related_task_id="task_old",
                executor_requests=[
                    {
                        "job_id": "job_executor_result_new",
                        "job_action": "execute",
                        "task_id": "new",
                        "goal": "开始新问题",
                        "current_checks": ["读取新任务日志"],
                        "delivery_target": {"delivery_mode": "inline", "channel": "cli", "chat_id": "direct"},
                        "context": {},
                    },
                ],
                metadata={
                    "brain_source": "executor_result",
                    "source_event": EventType.EXECUTOR_EVENT_RESULT_READY,
                    "source_decision": "accept",
                    "job_id": "job_old",
                },
            ),
        )
    )
    await _drain(bus)

    world_model = session.world_model_snapshot("sess_executor_result_new")
    assert len(job_requests) == 3
    assert str(job_requests[0].payload.task_id or "") == "task_old"
    assert str(job_requests[1].payload.job_action or "") == "cancel"
    assert str(job_requests[1].payload.task_id or "") == "task_old"
    new_task_id = str(job_requests[2].payload.task_id or "")
    assert new_task_id.startswith("task_")
    assert new_task_id != "task_old"
    assert world_model.current_task is not None
    assert world_model.current_task.task_id == new_task_id
    assert world_model.current_task.goal == "开始新问题"


def test_session_runtime_executor_result_new_task_replaces_current_task(tmp_path) -> None:
    asyncio.run(_exercise_session_runtime_executor_result_new_task_replaces_current_task(tmp_path))


async def _exercise_session_runtime_executor_result_updates_only_its_task(tmp_path) -> None:
    bus = PriorityPubSubBus()
    store = ExecutorStore()
    store.add(
        ExecutorRecord(
            task_id="task_old",
            session_id="sess_result_focus",
            turn_id="turn_result_focus",
            job_id="job_old",
            request=TaskRequestSpec(request="补最后一个验证", goal="修旧问题", current_checks=["补最后一个验证"]),
            title="修旧问题",
            origin_message=MessageRef(channel="cli", chat_id="direct", message_id="msg_old"),
            delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct"),
        )
    )
    session = SessionRuntime(bus=bus, task_store=store, world_model_store=WorldModelStore(tmp_path))
    session.register()

    await bus.publish(
        build_envelope(
            event_type=EventType.BRAIN_EVENT_REPLY_READY,
            source="brain_runtime",
            target="broadcast",
            session_id="sess_result_focus",
            turn_id="turn_seed_focus",
            task_id="task_old",
            correlation_id="task_old",
            payload=BrainReplyReadyPayload(
                request_id="brain_reply_seed_focus",
                reply_text="切到新任务。",
                reply_kind="status",
                delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct"),
                invoke_executor=True,
                related_task_id="task_old",
                executor_requests=[
                    {
                        "job_id": "job_seed_new",
                        "job_action": "execute",
                        "task_id": "new",
                        "goal": "开始新问题",
                        "current_checks": ["读取新任务日志"],
                        "delivery_target": {"delivery_mode": "inline", "channel": "cli", "chat_id": "direct"},
                        "context": {},
                    },
                ],
                metadata={
                    "brain_source": "executor_result",
                    "source_event": EventType.EXECUTOR_EVENT_RESULT_READY,
                    "source_decision": "accept",
                    "job_id": "job_old",
                },
            ),
        )
    )
    await _drain(bus)

    seeded = session.world_model_snapshot("sess_result_focus")
    assert seeded.current_task is not None
    current_task_id = seeded.current_task.task_id
    assert current_task_id.startswith("task_")
    assert current_task_id != "task_old"

    await bus.publish(
        build_envelope(
            event_type=EventType.EXECUTOR_EVENT_RESULT_READY,
            source="executor_runtime",
            target="broadcast",
            session_id="sess_result_focus",
            turn_id="turn_result_focus",
            task_id="task_old",
            correlation_id="task_old",
            payload=ExecutorResultPayload(
                job_id="job_seed_old",
                decision="accept",
                summary="旧任务验证完成。",
                result_text="旧任务这一步已经做完。",
                delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct"),
            ),
        )
    )
    await _drain(bus)

    final_model = session.world_model_snapshot("sess_result_focus")
    assert final_model.current_task is not None
    assert final_model.current_task.task_id == current_task_id
    assert final_model.current_task.last_result != "旧任务验证完成。"


def test_session_runtime_executor_result_updates_only_its_task(tmp_path) -> None:
    asyncio.run(_exercise_session_runtime_executor_result_updates_only_its_task(tmp_path))
