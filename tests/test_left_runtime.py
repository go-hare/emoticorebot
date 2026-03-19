from __future__ import annotations

import asyncio

from emoticorebot.left_brain.runtime import LeftBrainRuntime
from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.commands import FollowupContextPayload, LeftReplyRequestPayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import (
    DeliveryTargetPayload,
    InputSlots,
    LeftFollowupReadyPayload,
    LeftReplyReadyPayload,
    LeftStreamDeltaPayload,
    TurnInputPayload,
)
from emoticorebot.protocol.task_models import MessageRef, TaskRequestSpec
from emoticorebot.protocol.topics import EventType
from emoticorebot.right_brain.store import RightBrainRecord, RightBrainStore


class _StreamingLeftBrainLLM:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = list(chunks)

    async def astream(self, _prompt):
        for chunk in self._chunks:
            yield chunk


def _left_reply_request(
    *,
    session_id: str,
    turn_id: str,
    task_id: str | None = None,
    turn_input: TurnInputPayload | None = None,
    followup_context: FollowupContextPayload | None = None,
) -> BusEnvelope[LeftReplyRequestPayload]:
    return build_envelope(
        event_type=EventType.LEFT_COMMAND_REPLY_REQUESTED,
        source="session",
        target="left_runtime",
        session_id=session_id,
        turn_id=turn_id,
        task_id=task_id,
        correlation_id=task_id or turn_id,
        payload=LeftReplyRequestPayload(
            request_id=f"left_req_{turn_id}",
            turn_input=turn_input,
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
            delivery_target=DeliveryTargetPayload(delivery_mode="push", channel="telegram", chat_id="123456"),
        )
    )
    return store


async def _drain(bus: PriorityPubSubBus) -> None:
    await bus.drain()
    await asyncio.sleep(0)
    await bus.drain()


async def _exercise_left_brain_runtime_formats_right_brain_followups() -> None:
    bus = PriorityPubSubBus()
    left_runtime = LeftBrainRuntime(bus=bus, task_store=_store())
    left_runtime.register()

    followups: list[BusEnvelope[LeftFollowupReadyPayload]] = []
    reflections: list[str] = []

    async def _capture_followup(event: BusEnvelope[LeftFollowupReadyPayload]) -> None:
        followups.append(event)

    async def _capture_reflection(event: BusEnvelope[object]) -> None:
        reflections.append(str(event.event_type))

    bus.subscribe(consumer="test:followup", event_type=EventType.LEFT_EVENT_FOLLOWUP_READY, handler=_capture_followup)
    bus.subscribe(consumer="reflection_governor", event_type=EventType.REFLECTION_LIGHT, handler=_capture_reflection)

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
                delivery_target=DeliveryTargetPayload(delivery_mode="push", channel="telegram", chat_id="123456"),
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
                delivery_target=DeliveryTargetPayload(delivery_mode="push", channel="telegram", chat_id="123456"),
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
                delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="telegram", chat_id="654321"),
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
    assert followups[0].payload.delivery_target.chat_id == "123456"
    assert followups[2].payload.delivery_target.delivery_mode == "inline"
    assert followups[2].payload.delivery_target.chat_id == "654321"
    assert reflections == []


def test_left_brain_runtime_formats_right_brain_followups() -> None:
    asyncio.run(_exercise_left_brain_runtime_formats_right_brain_followups())


async def _exercise_left_brain_runtime_formats_rejected_and_failed_followups() -> None:
    bus = PriorityPubSubBus()
    store = _store()
    left_runtime = LeftBrainRuntime(bus=bus, task_store=store)
    left_runtime.register()

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
                delivery_target=DeliveryTargetPayload(delivery_mode="push", channel="telegram", chat_id="123456"),
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
                delivery_target=DeliveryTargetPayload(delivery_mode="push", channel="telegram", chat_id="123456"),
                metadata={"result": "failed"},
            ),
        )
    )
    await _drain(bus)

    assert len(followups) == 2
    assert "先不继续执行" in followups[0].payload.reply_text
    assert "失败了" in followups[1].payload.reply_text


def test_left_brain_runtime_formats_rejected_and_failed_followups() -> None:
    asyncio.run(_exercise_left_brain_runtime_formats_rejected_and_failed_followups())


async def _exercise_left_brain_runtime_streams_user_block_only() -> None:
    bus = PriorityPubSubBus()
    left_runtime = LeftBrainRuntime(
        bus=bus,
        task_store=RightBrainStore(),
        left_brain_llm=_StreamingLeftBrainLLM(
            [
                "####user####\n你好，我在这里。",
                "\n\n####task####\naction=none\ntask_mode=skip\n",
            ]
        ),
    )
    left_runtime.register()

    stream_deltas: list[BusEnvelope[LeftStreamDeltaPayload]] = []
    replies: list[BusEnvelope[LeftReplyReadyPayload]] = []

    async def _capture_delta(event: BusEnvelope[LeftStreamDeltaPayload]) -> None:
        stream_deltas.append(event)

    async def _capture_reply(event: BusEnvelope[LeftReplyReadyPayload]) -> None:
        replies.append(event)

    bus.subscribe(consumer="test:stream-delta", event_type=EventType.LEFT_EVENT_STREAM_DELTA_READY, handler=_capture_delta)
    bus.subscribe(consumer="test:reply-ready", event_type=EventType.LEFT_EVENT_REPLY_READY, handler=_capture_reply)

    await bus.publish(
        _left_reply_request(
            session_id="sess_stream_1",
            turn_id="turn_stream_1",
            turn_input=TurnInputPayload(
                input_id="turn_stream_1",
                input_mode="turn",
                message=MessageRef(channel="cli", chat_id="direct", message_id="msg_stream_1"),
                user_text="你好",
                input_slots=InputSlots(),
                metadata={"current_delivery_mode": "stream", "available_delivery_modes": ["stream", "inline", "push"]},
            ),
        )
    )
    await _drain(bus)

    assert len(stream_deltas) >= 1
    streamed_text = "".join(item.payload.delta_text for item in stream_deltas)
    assert "你好，我在这里。" in streamed_text
    assert "####task####" not in streamed_text
    assert "action=none" not in streamed_text
    assert all(item.payload.stream_state in {"open", "delta"} for item in stream_deltas)

    assert len(replies) == 1
    assert replies[0].payload.reply_text == "你好，我在这里。"
    assert replies[0].payload.delivery_target.delivery_mode == "stream"
    assert replies[0].payload.stream_state == "close"
    assert replies[0].payload.stream_id == stream_deltas[0].payload.stream_id


def test_left_brain_runtime_streams_user_block_only() -> None:
    asyncio.run(_exercise_left_brain_runtime_streams_user_block_only())


async def _exercise_left_brain_runtime_suppresses_sync_intermediate_followups() -> None:
    bus = PriorityPubSubBus()
    left_runtime = LeftBrainRuntime(bus=bus, task_store=_store())
    left_runtime.register()

    followups: list[BusEnvelope[LeftFollowupReadyPayload]] = []

    async def _capture_followup(event: BusEnvelope[LeftFollowupReadyPayload]) -> None:
        followups.append(event)

    bus.subscribe(consumer="test:followup-sync", event_type=EventType.LEFT_EVENT_FOLLOWUP_READY, handler=_capture_followup)

    await bus.publish(
        _left_reply_request(
            session_id="sess_exec_1",
            turn_id="turn_exec_sync_1",
            task_id="task_exec_1",
            followup_context=FollowupContextPayload(
                source_event=EventType.RIGHT_EVENT_JOB_ACCEPTED,
                job_id="job_exec_1",
                decision="accept",
                reason="audit_tool 返回任务可以开始。",
                delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct"),
            ),
        )
    )
    await bus.publish(
        _left_reply_request(
            session_id="sess_exec_1",
            turn_id="turn_exec_sync_2",
            task_id="task_exec_1",
            followup_context=FollowupContextPayload(
                source_event=EventType.RIGHT_EVENT_PROGRESS,
                job_id="job_exec_1",
                decision="accept",
                summary="已完成扫描。",
                next_step="整理输出",
                delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct"),
            ),
        )
    )
    await bus.publish(
        _left_reply_request(
            session_id="sess_exec_1",
            turn_id="turn_exec_sync_3",
            task_id="task_exec_1",
            followup_context=FollowupContextPayload(
                source_event=EventType.RIGHT_EVENT_RESULT_READY,
                job_id="job_exec_1",
                decision="accept",
                summary="整理完成。",
                result_text="产物已生成。",
                delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct"),
                metadata={"result": "success"},
            ),
        )
    )
    await _drain(bus)

    assert len(followups) == 3
    assert followups[0].payload.metadata["suppress_output"] is True
    assert followups[1].payload.metadata["suppress_output"] is True
    assert "suppress_output" not in followups[2].payload.metadata


def test_left_brain_runtime_suppresses_sync_intermediate_followups() -> None:
    asyncio.run(_exercise_left_brain_runtime_suppresses_sync_intermediate_followups())



