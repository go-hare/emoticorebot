from __future__ import annotations

import asyncio

from emoticorebot.brain.executive import ExecutiveBrain
from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import ReplyReadyPayload, StableInputPayload, TaskSummaryPayload, TaskUpdatePayload
from emoticorebot.protocol.task_models import MessageRef, ProtocolModel, TaskRequestSpec
from emoticorebot.protocol.topics import EventType
from emoticorebot.runtime.state_machine import TaskState
from emoticorebot.runtime.task_store import RuntimeTaskRecord, TaskStore
from emoticorebot.session.runtime import SessionRuntime


def _task_store() -> TaskStore:
    store = TaskStore()
    store.add(
        RuntimeTaskRecord(
            task_id="task_1",
            session_id="sess_1",
            turn_id="turn_1",
            request=TaskRequestSpec(request="完成任务", title="完成任务"),
            origin_message=MessageRef(channel="cli", chat_id="direct", message_id="msg_1"),
            title="完成任务",
            state=TaskState.DONE,
            summary="done",
        )
    )
    return store


async def _exercise_terminal_reflection() -> None:
    bus = PriorityPubSubBus()
    store = _task_store()
    brain = ExecutiveBrain(bus=bus, task_store=store)
    brain.register()

    turn_events: list[BusEnvelope[ProtocolModel]] = []

    async def _capture_turn(event: BusEnvelope[ProtocolModel]) -> None:
        turn_events.append(event)

    bus.subscribe(consumer="memory_governor", event_type=EventType.REFLECT_LIGHT, handler=_capture_turn)

    task = store.require("task_1")
    await bus.publish(
        build_envelope(
            event_type=EventType.INPUT_STABLE,
            source="session",
            target="broadcast",
            session_id="sess_1",
            turn_id="turn_1",
            task_id="task_1",
            correlation_id="task_1",
            payload=StableInputPayload(
                input_id="task_front_1",
                input_kind="text",
                channel_kind="chat",
                message=MessageRef(channel="cli", chat_id="direct", sender_id="user", message_id="msg_1"),
                plain_text="已完成",
                metadata={
                    "front_origin": "task",
                    "task_event_type": EventType.TASK_END,
                    "task_event_id": "evt_task_end_1",
                    "task_id": "task_1",
                    "task_result": "success",
                    "task_summary": "已完成",
                    "task_output": "任务已经完成。",
                    "channel_kind": "chat",
                },
            ),
        )
    )
    await bus.drain()

    assert len(turn_events) == 1
    assert turn_events[0].payload.reason == "task_result"
    assert "reflection_input" in turn_events[0].payload.metadata
    task_projection = turn_events[0].payload.metadata["reflection_input"]["task"]
    assert task_projection["state"] == "done"
    assert task_projection["result"] == "success"
    assert turn_events[0].payload.metadata["reflection_input"]["assistant_output"]


def test_executive_brain_emits_light_reflection_for_terminal_result() -> None:
    asyncio.run(_exercise_terminal_reflection())


async def _exercise_task_ask_reflection() -> None:
    bus = PriorityPubSubBus()
    store = TaskStore()
    store.add(
        RuntimeTaskRecord(
            task_id="task_waiting_1",
            session_id="sess_waiting",
            turn_id="turn_waiting",
            request=TaskRequestSpec(request="帮我处理任务", title="帮我处理任务"),
            origin_message=MessageRef(channel="cli", chat_id="direct", message_id="msg_waiting"),
            title="帮我处理任务",
            state=TaskState.WAITING,
            summary="需要补充仓库地址",
        )
    )
    brain = ExecutiveBrain(bus=bus, task_store=store)
    brain.register()

    turn_events: list[BusEnvelope[ProtocolModel]] = []

    async def _capture_turn(event: BusEnvelope[ProtocolModel]) -> None:
        turn_events.append(event)

    bus.subscribe(consumer="memory_governor", event_type=EventType.REFLECT_LIGHT, handler=_capture_turn)

    await bus.publish(
        build_envelope(
            event_type=EventType.INPUT_STABLE,
            source="session",
            target="broadcast",
            session_id="sess_waiting",
            turn_id="turn_waiting",
            task_id="task_waiting_1",
            correlation_id="task_waiting_1",
            payload=StableInputPayload(
                input_id="task_front_waiting",
                input_kind="text",
                channel_kind="chat",
                message=MessageRef(channel="cli", chat_id="direct", sender_id="user", message_id="msg_waiting"),
                plain_text="需要你补充仓库地址",
                metadata={
                    "front_origin": "task",
                    "task_event_type": EventType.TASK_ASK,
                    "task_event_id": "evt_task_ask_1",
                    "task_id": "task_waiting_1",
                    "task_question": "请补充仓库地址",
                    "task_field": "repo_url",
                    "task_why": "缺少执行目标",
                    "channel_kind": "chat",
                },
            ),
        )
    )
    await bus.drain()

    assert len(turn_events) == 1
    assert turn_events[0].payload.reason == "task_need_input"
    reflection_input = turn_events[0].payload.metadata["reflection_input"]
    assert reflection_input["source_type"] == "task_event"
    assert reflection_input["execution"]["status"] == "waiting_input"
    assert reflection_input["execution"]["missing"] == ["repo_url"]


def test_executive_brain_emits_light_reflection_for_task_ask() -> None:
    asyncio.run(_exercise_task_ask_reflection())


def test_task_create_context_injects_memory_bundle() -> None:
    class _ContextBuilder:
        def build_task_memory_bundle(self, *, query: str, limit: int = 6):
            assert "创建 add.py" in query
            return {
                "relevant_task_memories": [
                    {"type": "workflow_pattern", "summary": "复杂任务先收敛后输出"},
                ],
                "relevant_tool_memories": [],
                "skill_hints": [
                    {
                        "summary": "最终结果式执行",
                        "payload": {"skill_name": "final-result-execution", "trigger": "多步执行", "hint": "优先最终结果"},
                    }
                ],
            }

    brain = ExecutiveBrain(bus=PriorityPubSubBus(), task_store=TaskStore(), context_builder=_ContextBuilder())
    payload = StableInputPayload(
        input_id="turn_1",
        input_kind="text",
        channel_kind="chat",
        message=MessageRef(channel="cli", chat_id="direct", sender_id="user", message_id="msg_1"),
        plain_text="创建 add.py",
        metadata={},
    )

    context = brain._task_create_context(
        task={},
        payload=payload,
        origin=payload.message,
        suppress_delivery=False,
    )

    assert context["memory_refs"] == ["[workflow_pattern] 复杂任务先收敛后输出"]
    assert context["skill_hints"] == ["技能 `final-result-execution` | 触发: 多步执行 | 优先最终结果"]


async def _exercise_task_origin_reply_guard_redacts_before_publish() -> None:
    bus = PriorityPubSubBus()
    store = TaskStore()
    store.add(
        RuntimeTaskRecord(
            task_id="task_2",
            session_id="sess_2",
            turn_id="turn_2",
            request=TaskRequestSpec(request="完成任务", title="完成任务"),
            origin_message=MessageRef(channel="cli", chat_id="direct", message_id="msg_2"),
            title="完成任务",
            state=TaskState.DONE,
        )
    )
    brain = ExecutiveBrain(bus=bus, task_store=store)
    brain.register()

    redacted: list[BusEnvelope[ReplyReadyPayload]] = []
    ready: list[BusEnvelope[ReplyReadyPayload]] = []

    async def _capture_redacted(event: BusEnvelope[ReplyReadyPayload]) -> None:
        redacted.append(event)

    async def _capture_ready(event: BusEnvelope[ReplyReadyPayload]) -> None:
        ready.append(event)

    bus.subscribe(consumer="test:redacted", event_type=EventType.OUTPUT_REPLY_REDACTED, handler=_capture_redacted)
    bus.subscribe(consumer="test:ready", event_type=EventType.OUTPUT_REPLY_READY, handler=_capture_ready)

    await bus.publish(
        build_envelope(
            event_type=EventType.INPUT_STABLE,
            source="session",
            target="broadcast",
            session_id="sess_2",
            turn_id="turn_2",
            task_id="task_2",
            correlation_id="task_2",
            payload=StableInputPayload(
                input_id="task_front_2",
                input_kind="text",
                channel_kind="chat",
                message=MessageRef(channel="cli", chat_id="direct", sender_id="user", message_id="msg_2"),
                plain_text="任务结束",
                metadata={
                    "front_origin": "task",
                    "task_event_type": EventType.TASK_END,
                    "task_event_id": "evt_task_end_2",
                    "task_id": "task_2",
                    "task_result": "success",
                    "task_summary": "已完成",
                    "task_output": "api_key=sk-abcdefghijklmnopqrstuv",
                },
            ),
        )
    )
    await bus.drain()

    assert ready == []
    assert len(redacted) == 1
    assert redacted[0].payload.reply.plain_text == "完成任务 已完成。api_key=[REDACTED]"


def test_executive_brain_task_origin_reply_guard_redacts_before_publish() -> None:
    asyncio.run(_exercise_task_origin_reply_guard_redacts_before_publish())


class _UnsafeReplyBrainLLM:
    async def ainvoke(self, _prompt):
        return "####user####\n-----BEGIN PRIVATE KEY-----\n\n####task####\nmode=answer\naction=none\n"


async def _exercise_user_turn_blocked_reply_falls_back_synchronously() -> None:
    bus = PriorityPubSubBus()
    store = TaskStore()
    brain = ExecutiveBrain(bus=bus, task_store=store, brain_llm=_UnsafeReplyBrainLLM())
    brain.register()

    approved: list[BusEnvelope[ReplyReadyPayload]] = []
    ready: list[BusEnvelope[ReplyReadyPayload]] = []

    async def _capture_approved(event: BusEnvelope[ReplyReadyPayload]) -> None:
        approved.append(event)

    async def _capture_ready(event: BusEnvelope[ReplyReadyPayload]) -> None:
        ready.append(event)

    bus.subscribe(consumer="test:approved", event_type=EventType.OUTPUT_REPLY_APPROVED, handler=_capture_approved)
    bus.subscribe(consumer="test:ready", event_type=EventType.OUTPUT_REPLY_READY, handler=_capture_ready)

    await bus.publish(
        build_envelope(
            event_type=EventType.INPUT_STABLE,
            source="input_normalizer",
            target="broadcast",
            session_id="sess_3",
            turn_id="turn_3",
            correlation_id="turn_3",
            payload=StableInputPayload(
                input_id="turn_3",
                input_kind="text",
                channel_kind="chat",
                message=MessageRef(channel="cli", chat_id="direct", sender_id="user", message_id="msg_3"),
                plain_text="回复我",
                metadata={"channel_kind": "chat"},
            ),
        )
    )
    await bus.drain()
    await asyncio.sleep(0)
    await bus.drain()

    assert ready == []
    assert len(approved) == 1
    assert approved[0].payload.reply.safe_fallback is True
    assert approved[0].payload.reply.kind == "safety_fallback"
    assert "不能直接发出" in str(approved[0].payload.reply.plain_text or "")


def test_executive_brain_user_turn_blocked_reply_falls_back_synchronously() -> None:
    asyncio.run(_exercise_user_turn_blocked_reply_falls_back_synchronously())


class _CapturingBrainLLM:
    def __init__(self) -> None:
        self.prompts: list[object] = []

    async def ainvoke(self, prompt: object):
        self.prompts.append(prompt)
        return "####user####\n收到。\n\n####task####\nmode=answer\naction=none\n"


async def _exercise_user_turn_consumes_unread_task_traces() -> None:
    bus = PriorityPubSubBus()
    store = TaskStore()
    session = SessionRuntime(bus=bus, task_store=store)
    session.register()

    task = store.add(
        RuntimeTaskRecord(
            task_id="task_trace_1",
            session_id="sess_trace",
            turn_id="turn_trace_1",
            request=TaskRequestSpec(request="更新 add.py", title="更新 add.py"),
            origin_message=MessageRef(channel="cli", chat_id="direct", message_id="msg_trace_1"),
            title="更新 add.py",
            state=TaskState.RUNNING,
        )
    )
    brain_llm = _CapturingBrainLLM()
    brain = ExecutiveBrain(
        bus=bus,
        task_store=store,
        brain_llm=brain_llm,
        session_runtime=session,
    )
    brain.register()

    try:
        task.touch()
        await bus.publish(
            build_envelope(
                event_type=EventType.TASK_UPDATE,
                source="runtime",
                target="broadcast",
                session_id="sess_trace",
                turn_id="turn_trace_1",
                task_id="task_trace_1",
                correlation_id="task_trace_1",
                payload=TaskUpdatePayload(
                    task_id="task_trace_1",
                    updated_at=task.updated_at,
                    message="正在写 add.py",
                    trace_append=[
                        {
                            "trace_id": "trace_progress_1",
                            "task_id": "task_trace_1",
                            "session_id": "sess_trace",
                            "ts": task.updated_at,
                            "kind": "progress",
                            "message": "正在写 add.py",
                        }
                    ],
                ),
            )
        )
        await bus.drain()
        await bus.publish(
            build_envelope(
                event_type=EventType.INPUT_STABLE,
                source="input_normalizer",
                target="broadcast",
                session_id="sess_trace",
                turn_id="turn_trace_1",
                correlation_id="turn_trace_1",
                payload=StableInputPayload(
                    input_id="turn_trace_1",
                    input_kind="text",
                    channel_kind="chat",
                    message=MessageRef(channel="cli", chat_id="direct", sender_id="user", message_id="msg_trace_1"),
                    plain_text="现在进度怎样？",
                    metadata={"channel_kind": "chat"},
                ),
            )
        )
        await bus.drain()
        await asyncio.sleep(0)
        await bus.drain()

        first_prompt = brain_llm.prompts[0][-1].content
        assert "正在写 add.py" in first_prompt

        task.touch()
        await bus.publish(
            build_envelope(
                event_type=EventType.TASK_SUMMARY,
                source="runtime",
                target="broadcast",
                session_id="sess_trace",
                turn_id="turn_trace_2",
                task_id="task_trace_1",
                correlation_id="task_trace_1",
                payload=TaskSummaryPayload(
                    task_id="task_trace_1",
                    updated_at=task.updated_at,
                    summary="准备提交结果",
                    trace_append=[
                        {
                            "trace_id": "trace_summary_2",
                            "task_id": "task_trace_1",
                            "session_id": "sess_trace",
                            "ts": task.updated_at,
                            "kind": "summary",
                            "message": "准备提交结果",
                        }
                    ],
                ),
            )
        )
        await bus.drain()
        await bus.publish(
            build_envelope(
                event_type=EventType.INPUT_STABLE,
                source="input_normalizer",
                target="broadcast",
                session_id="sess_trace",
                turn_id="turn_trace_2",
                correlation_id="turn_trace_2",
                payload=StableInputPayload(
                    input_id="turn_trace_2",
                    input_kind="text",
                    channel_kind="chat",
                    message=MessageRef(channel="cli", chat_id="direct", sender_id="user", message_id="msg_trace_2"),
                    plain_text="再说一下最新进度",
                    metadata={"channel_kind": "chat"},
                ),
            )
        )
        await bus.drain()
        await asyncio.sleep(0)
        await bus.drain()

        second_prompt = brain_llm.prompts[1][-1].content
        assert "准备提交结果" in second_prompt
        assert "正在写 add.py" not in second_prompt
    finally:
        await brain.stop()


def test_executive_brain_user_turn_consumes_unread_task_traces() -> None:
    asyncio.run(_exercise_user_turn_consumes_unread_task_traces())
