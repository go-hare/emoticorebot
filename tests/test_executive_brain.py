from __future__ import annotations

import asyncio

from emoticorebot.brain.executive import ExecutiveBrain
from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.commands import FollowupContextPayload, LeftReplyRequestPayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import LeftFollowupReadyPayload
from emoticorebot.protocol.task_models import MessageRef, TaskRequestSpec
from emoticorebot.protocol.topics import EventType
from emoticorebot.right.store import RightBrainRecord, RightBrainStore


def _left_reply_request(
    *,
    session_id: str,
    turn_id: str,
    task_id: str | None = None,
    followup_context: FollowupContextPayload | None = None,
) -> BusEnvelope[LeftReplyRequestPayload]:
    return build_envelope(
        event_type=EventType.LEFT_COMMAND_REPLY_REQUESTED,
        source="session",
        target="brain",
        session_id=session_id,
        turn_id=turn_id,
        task_id=task_id,
        correlation_id=task_id or turn_id,
        payload=LeftReplyRequestPayload(
            request_id=f"left_req_{turn_id}",
            followup_context=followup_context,
        ),
    )


def _store() -> RightBrainStore:
    store = RightBrainStore()
    store.add(
        RightBrainRecord(
            task_id="task_exec_1",
            session_id="sess_exec_1",
            turn_id="turn_exec_1",
            job_id="job_exec_1",
            request=TaskRequestSpec(request="整理模块", title="整理模块"),
            title="整理模块",
            origin_message=MessageRef(channel="cli", chat_id="direct", message_id="msg_exec_1"),
        )
    )
    return store


async def _drain(bus: PriorityPubSubBus) -> None:
    await bus.drain()
    await asyncio.sleep(0)
    await bus.drain()


async def _exercise_executive_brain_formats_right_brain_followups() -> None:
    bus = PriorityPubSubBus()
    brain = ExecutiveBrain(bus=bus, task_store=_store())
    brain.register()

    followups: list[BusEnvelope[LeftFollowupReadyPayload]] = []
    reflections: list[str] = []

    async def _capture_followup(event: BusEnvelope[LeftFollowupReadyPayload]) -> None:
        followups.append(event)

    async def _capture_reflection(event: BusEnvelope[object]) -> None:
        reflections.append(str(event.event_type))

    bus.subscribe(consumer="test:followup", event_type=EventType.LEFT_EVENT_FOLLOWUP_READY, handler=_capture_followup)
    bus.subscribe(consumer="memory_governor", event_type=EventType.REFLECT_LIGHT, handler=_capture_reflection)

    await bus.publish(
        _left_reply_request(
            session_id="sess_exec_1",
            turn_id="turn_exec_1",
            task_id="task_exec_1",
            followup_context=FollowupContextPayload(
                source_event=EventType.RIGHT_EVENT_JOB_ACCEPTED,
                job_id="job_exec_1",
                decision="accept",
                reason="audit_tool 返回任务可以开始。",
                preferred_delivery_mode="push",
            ),
        )
    )
    await bus.publish(
        _left_reply_request(
            session_id="sess_exec_1",
            turn_id="turn_exec_2",
            task_id="task_exec_1",
            followup_context=FollowupContextPayload(
                source_event=EventType.RIGHT_EVENT_PROGRESS,
                job_id="job_exec_1",
                decision="accept",
                summary="已完成扫描。",
                progress=0.4,
                next_step="整理输出",
                preferred_delivery_mode="push",
            ),
        )
    )
    await bus.publish(
        _left_reply_request(
            session_id="sess_exec_1",
            turn_id="turn_exec_3",
            task_id="task_exec_1",
            followup_context=FollowupContextPayload(
                source_event=EventType.RIGHT_EVENT_RESULT_READY,
                job_id="job_exec_1",
                decision="answer_only",
                summary="更适合直接回答。",
                result_text="这是右脑给左脑的答案素材。",
                preferred_delivery_mode="inline",
                metadata={"result": "success"},
            ),
        )
    )
    await _drain(bus)

    assert len(followups) == 3
    assert followups[0].payload.reply_kind == "status"
    assert "已开始处理" in followups[0].payload.reply_text
    assert "已完成扫描" in followups[1].payload.reply_text
    assert "整理输出" in followups[1].payload.reply_text
    assert followups[2].payload.reply_kind == "answer"
    assert followups[2].payload.reply_text == "这是右脑给左脑的答案素材。"
    assert reflections == []


def test_executive_brain_formats_right_brain_followups() -> None:
    asyncio.run(_exercise_executive_brain_formats_right_brain_followups())


async def _exercise_executive_brain_formats_rejected_and_failed_followups() -> None:
    bus = PriorityPubSubBus()
    store = _store()
    brain = ExecutiveBrain(bus=bus, task_store=store)
    brain.register()

    followups: list[BusEnvelope[LeftFollowupReadyPayload]] = []

    async def _capture_followup(event: BusEnvelope[LeftFollowupReadyPayload]) -> None:
        followups.append(event)

    bus.subscribe(consumer="test:followup-2", event_type=EventType.LEFT_EVENT_FOLLOWUP_READY, handler=_capture_followup)

    await bus.publish(
        _left_reply_request(
            session_id="sess_exec_1",
            turn_id="turn_exec_4",
            task_id="task_exec_1",
            followup_context=FollowupContextPayload(
                source_event=EventType.RIGHT_EVENT_JOB_REJECTED,
                job_id="job_exec_1",
                decision="reject",
                reason="缺少可处理的源文件。",
                preferred_delivery_mode="push",
            ),
        )
    )
    await bus.publish(
        _left_reply_request(
            session_id="sess_exec_1",
            turn_id="turn_exec_5",
            task_id="task_exec_1",
            followup_context=FollowupContextPayload(
                source_event=EventType.RIGHT_EVENT_RESULT_READY,
                job_id="job_exec_1",
                decision="accept",
                summary="右脑执行失败。",
                result_text="执行命令失败。",
                preferred_delivery_mode="push",
                metadata={"result": "failed"},
            ),
        )
    )
    await _drain(bus)

    assert len(followups) == 2
    assert "先不继续执行" in followups[0].payload.reply_text
    assert "失败了" in followups[1].payload.reply_text


def test_executive_brain_formats_rejected_and_failed_followups() -> None:
    asyncio.run(_exercise_executive_brain_formats_rejected_and_failed_followups())
