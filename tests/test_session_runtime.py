from __future__ import annotations

import asyncio

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.commands import LeftReplyRequestPayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import (
    DeliveryTargetPayload,
    RightBrainAcceptedPayload,
    RightBrainProgressPayload,
    RightBrainResultPayload,
    TurnInputPayload,
)
from emoticorebot.protocol.task_models import MessageRef, TaskRequestSpec
from emoticorebot.protocol.topics import EventType
from emoticorebot.right_brain.state import RightBrainState
from emoticorebot.right_brain.store import RightBrainRecord, RightBrainStore
from emoticorebot.session.runtime import SessionRuntime


async def _drain(bus: PriorityPubSubBus) -> None:
    await bus.drain()
    await asyncio.sleep(0)
    await bus.drain()


async def _exercise_session_runtime_tracks_right_brain_flow() -> None:
    bus = PriorityPubSubBus()
    store = RightBrainStore()
    record = store.add(
        RightBrainRecord(
            task_id="task_session_1",
            session_id="sess_session_1",
            turn_id="turn_session_1",
            job_id="job_session_1",
            request=TaskRequestSpec(request="整理模块", title="整理模块"),
            title="整理模块",
            origin_message=MessageRef(channel="cli", chat_id="direct", message_id="msg_session_1"),
            delivery_target=DeliveryTargetPayload(delivery_mode="push", channel="telegram", chat_id="123456"),
        )
    )
    session = SessionRuntime(bus=bus, task_store=store)
    session.register()

    left_requests: list[BusEnvelope[LeftReplyRequestPayload]] = []

    async def _capture(event: BusEnvelope[LeftReplyRequestPayload]) -> None:
        left_requests.append(event)

    bus.subscribe(consumer="left_runtime", event_type=EventType.LEFT_COMMAND_REPLY_REQUESTED, handler=_capture)

    await bus.publish(
        build_envelope(
            event_type=EventType.INPUT_TURN_RECEIVED,
            source="input_normalizer",
            target="broadcast",
            session_id="sess_session_1",
            turn_id="turn_session_1",
            correlation_id="turn_session_1",
            payload=TurnInputPayload(
                input_id="turn_session_1",
                input_mode="turn",
                session_mode="turn_chat",
                message=MessageRef(channel="cli", chat_id="direct", sender_id="user", message_id="msg_session_1"),
                user_text="开始整理模块",
                metadata={"channel_kind": "chat"},
            ),
        )
    )
    await _drain(bus)

    record.accepted = True
    record.summary = "audit_tool 返回任务可以开始。"
    record.touch()
    await bus.publish(
        build_envelope(
            event_type=EventType.RIGHT_EVENT_JOB_ACCEPTED,
            source="right_runtime",
            target="broadcast",
            session_id="sess_session_1",
            turn_id="turn_session_1",
            task_id="task_session_1",
            correlation_id="task_session_1",
            payload=RightBrainAcceptedPayload(
                job_id="job_session_1",
                stage="execute",
                reason="audit_tool 返回任务可以开始。",
                delivery_target=DeliveryTargetPayload(delivery_mode="push", channel="telegram", chat_id="123456"),
            ),
        )
    )
    await _drain(bus)

    record.summary = "已完成右脑扫描。"
    record.last_progress = "已完成右脑扫描。"
    record.touch()
    await bus.publish(
        build_envelope(
            event_type=EventType.RIGHT_EVENT_PROGRESS,
            source="right_runtime",
            target="broadcast",
            session_id="sess_session_1",
            turn_id="turn_session_1",
            task_id="task_session_1",
            correlation_id="task_session_1",
            payload=RightBrainProgressPayload(
                job_id="job_session_1",
                stage="execute",
                summary="已完成右脑扫描。",
                progress=0.4,
                next_step="开始整理输出",
                delivery_target=DeliveryTargetPayload(delivery_mode="push", channel="telegram", chat_id="123456"),
            ),
        )
    )
    await _drain(bus)

    record.mark_done(result="success", summary="模块整理完成。", decision="accept", result_text="结果已准备好。")
    await bus.publish(
        build_envelope(
            event_type=EventType.RIGHT_EVENT_RESULT_READY,
            source="right_runtime",
            target="broadcast",
            session_id="sess_session_1",
            turn_id="turn_session_1",
            task_id="task_session_1",
            correlation_id="task_session_1",
            payload=RightBrainResultPayload(
                job_id="job_session_1",
                decision="accept",
                summary="模块整理完成。",
                result_text="结果已准备好。",
                delivery_target=DeliveryTargetPayload(delivery_mode="push", channel="telegram", chat_id="123456"),
                metadata={"result": "success"},
            ),
        )
    )
    await _drain(bus)

    followups = [event for event in left_requests if event.payload.followup_context is not None]
    assert [event.payload.followup_context.source_event for event in followups] == [
        EventType.RIGHT_EVENT_JOB_ACCEPTED,
        EventType.RIGHT_EVENT_PROGRESS,
        EventType.RIGHT_EVENT_RESULT_READY,
    ]
    assert followups[1].payload.followup_context.progress == 0.4
    assert followups[0].payload.followup_context.delivery_target.channel == "telegram"
    assert followups[1].payload.followup_context.delivery_target.chat_id == "123456"
    assert followups[2].payload.followup_context.delivery_target.delivery_mode == "push"

    snapshot = session.snapshot("sess_session_1")
    view = snapshot.tasks["task_session_1"]
    assert view.state == "done"
    assert view.result == "success"
    assert view.summary == "模块整理完成。"
    assert session.task_trace_summary("task_session_1", limit=3) == [
        "audit_tool 返回任务可以开始。",
        "已完成右脑扫描。",
        "结果已准备好。",
    ]


def test_session_runtime_tracks_right_brain_flow() -> None:
    asyncio.run(_exercise_session_runtime_tracks_right_brain_flow())


async def _exercise_session_runtime_keeps_answer_only_delivery_mode() -> None:
    bus = PriorityPubSubBus()
    store = RightBrainStore()
    record = store.add(
        RightBrainRecord(
            task_id="task_session_2",
            session_id="sess_session_2",
            turn_id="turn_session_2",
            job_id="job_session_2",
            request=TaskRequestSpec(request="解释架构", title="解释架构"),
            title="解释架构",
            delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="telegram", chat_id="654321"),
        )
    )
    session = SessionRuntime(bus=bus, task_store=store)
    session.register()

    left_requests: list[BusEnvelope[LeftReplyRequestPayload]] = []

    async def _capture(event: BusEnvelope[LeftReplyRequestPayload]) -> None:
        left_requests.append(event)

    bus.subscribe(consumer="left_runtime", event_type=EventType.LEFT_COMMAND_REPLY_REQUESTED, handler=_capture)

    record.mark_done(result="success", summary="更适合直接回答。", decision="answer_only", result_text="这是理性答案。")
    await bus.publish(
        build_envelope(
            event_type=EventType.RIGHT_EVENT_RESULT_READY,
            source="right_runtime",
            target="broadcast",
            session_id="sess_session_2",
            turn_id="turn_session_2",
            task_id="task_session_2",
            correlation_id="task_session_2",
            payload=RightBrainResultPayload(
                job_id="job_session_2",
                decision="answer_only",
                summary="更适合直接回答。",
                result_text="这是理性答案。",
                delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="telegram", chat_id="654321"),
                metadata={"result": "success"},
            ),
        )
    )
    await _drain(bus)

    followup = left_requests[-1].payload.followup_context
    assert followup is not None
    assert followup.decision == "answer_only"
    assert followup.delivery_target.delivery_mode == "inline"
    assert followup.delivery_target.chat_id == "654321"
    assert followup.result_text == "这是理性答案。"


def test_session_runtime_keeps_answer_only_delivery_mode() -> None:
    asyncio.run(_exercise_session_runtime_keeps_answer_only_delivery_mode())

