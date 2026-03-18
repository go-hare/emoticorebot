from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.memory.governor import MemoryGovernor
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import RepliedPayload
from emoticorebot.protocol.memory_models import MemoryWriteRequestPayload, ReflectSignalPayload
from emoticorebot.protocol.task_models import MessageRef, ProtocolModel
from emoticorebot.protocol.topics import EventType


def _collect(bus: PriorityPubSubBus, event_type: str) -> list[BusEnvelope[ProtocolModel]]:
    captured: list[BusEnvelope[ProtocolModel]] = []

    async def _capture(event: BusEnvelope[ProtocolModel]) -> None:
        captured.append(event)

    bus.subscribe(consumer=f"test:{event_type}", event_type=event_type, handler=_capture)
    return captured


async def _publish_delivery_gate(bus: PriorityPubSubBus, *, session_id: str, turn_id: str, correlation_id: str) -> None:
    await bus.publish(
        build_envelope(
            event_type=EventType.OUTPUT_REPLIED,
            source="delivery",
            target="broadcast",
            session_id=session_id,
            turn_id=turn_id,
            correlation_id=correlation_id,
            payload=RepliedPayload(
                reply_id="reply_1",
                delivery_message=MessageRef(
                    channel="cli",
                    chat_id="direct",
                    message_id="reply_1",
                    reply_to_message_id="msg_1",
                ),
                delivery_mode="inline",
            ),
        )
    )


async def _exercise_turn_reflection_persistence(workspace: Path) -> None:
    bus = PriorityPubSubBus()
    governor = MemoryGovernor(bus=bus, workspace=workspace)
    governor.register()

    committed = _collect(bus, EventType.MEMORY_WRITE_COMMITTED)

    await bus.publish(
        build_envelope(
            event_type=EventType.REFLECT_LIGHT,
            source="brain",
            target="memory_governor",
            session_id="sess_1",
            turn_id="turn_1",
            correlation_id="turn_1",
            payload=ReflectSignalPayload(
                trigger_id="reflect_1",
                reason="user_turn",
                source_event_id="evt_user_1",
                metadata={
                    "reflection_input": {
                        "session_id": "sess_1",
                        "turn_id": "turn_1",
                        "message_id": "msg_1",
                        "source_type": "user_turn",
                        "user_input": "帮我分析一下这个问题",
                        "assistant_output": "我先帮你拆一下结构。",
                        "output": "我先帮你拆一下结构。",
                        "channel": "cli",
                        "chat_id": "direct",
                        "task": {"state": "done", "summary": "结构已经拆解"},
                        "execution": {
                            "invoked": True,
                            "status": "done",
                            "summary": "完成一次结构化分析",
                            "confidence": 0.9,
                            "attempt_count": 1,
                            "missing": [],
                            "failure_reason": "",
                            "recommended_action": "",
                        },
                    }
                },
            ),
        )
    )
    await _publish_delivery_gate(bus, session_id="sess_1", turn_id="turn_1", correlation_id="turn_1")
    await bus.drain()

    assert len(committed) == 1
    cognitive = (workspace / "memory" / "cognitive_events.jsonl").read_text(encoding="utf-8")
    memories = (workspace / "memory" / "memory.jsonl").read_text(encoding="utf-8")
    assert "帮我分析一下这个问题" in cognitive
    assert "我先帮你拆一下结构" in cognitive
    assert "执行已完成" in memories


def test_memory_governor_persists_turn_reflection_after_delivery() -> None:
    with TemporaryDirectory() as tmp_dir:
        asyncio.run(_exercise_turn_reflection_persistence(Path(tmp_dir)))


async def _exercise_periodic_deep_reflection(workspace: Path) -> None:
    bus = PriorityPubSubBus()
    governor = MemoryGovernor(bus=bus, workspace=workspace)
    governor.register()

    committed = _collect(bus, EventType.MEMORY_WRITE_COMMITTED)
    governor._memory_store.append_many(
        [
            {
                "audience": "task",
                "kind": "procedural",
                "type": "skill_hint",
                "summary": "复杂任务优先走最终结果式执行",
                "content": "复杂任务应尽量在单次执行里先收敛，再把最终结果交回 brain。",
                "importance": 7,
                "confidence": 0.8,
                "stability": 0.85,
                "tags": ["skill", "hint"],
                "payload": {
                    "skill_id": "skill_final_result_execution_seed",
                    "skill_name": "final-result-execution",
                    "trigger": "需要多步执行或工具组合时",
                    "hint": "减少中间态汇报，优先收敛到最终结果。",
                    "applies_to_tools": [],
                },
            }
        ]
    )

    async def _turn(index: int) -> None:
        turn_id = f"turn_{index}"
        await bus.publish(
            build_envelope(
                event_type=EventType.REFLECT_LIGHT,
                source="brain",
                target="memory_governor",
                session_id="sess_1",
                turn_id=turn_id,
                correlation_id=turn_id,
                payload=ReflectSignalPayload(
                    trigger_id=f"reflect_{index}",
                    reason="task_result",
                    source_event_id=f"evt_task_{index}",
                    task_id=f"task_{index}",
                    metadata={
                        "reflection_input": {
                            "session_id": "sess_1",
                            "turn_id": turn_id,
                            "message_id": f"msg_{index}",
                            "source_type": "task_event",
                            "user_input": "完成一个复杂任务",
                            "assistant_output": "任务已完成，我给你最终结果。",
                            "output": "任务已完成，我给你最终结果。",
                            "channel": "cli",
                            "chat_id": "direct",
                            "task": {"task_id": f"task_{index}", "state": "done", "summary": "复杂任务已收敛"},
                            "execution": {
                                "invoked": True,
                                "status": "done",
                                "summary": "多步执行后收敛为最终结果",
                                "confidence": 0.88,
                                "attempt_count": 2,
                                "missing": [],
                                "failure_reason": "",
                                "recommended_action": "",
                            },
                        }
                    },
                ),
            )
        )
        await _publish_delivery_gate(bus, session_id="sess_1", turn_id=turn_id, correlation_id=turn_id)
        await bus.drain()

    await _turn(1)
    await _turn(2)
    result = await governor.run_deep_reflection(reason="periodic_signal", warm_limit=15)
    await bus.drain()

    assert result.memory_count >= 1
    assert result.materialized_skill_count >= 1
    assert any(event.payload.metadata.get("reflection_type") == "deep" for event in committed)
    skill_files = list((workspace / "skills").rglob("SKILL.md"))
    assert len(skill_files) >= 1


def test_memory_governor_runs_periodic_deep_reflection() -> None:
    with TemporaryDirectory() as tmp_dir:
        asyncio.run(_exercise_periodic_deep_reflection(Path(tmp_dir)))


async def _exercise_deep_signal_runs_without_delivery_gate(workspace: Path) -> None:
    bus = PriorityPubSubBus()
    governor = MemoryGovernor(bus=bus, workspace=workspace)
    governor.register()

    committed = _collect(bus, EventType.MEMORY_WRITE_COMMITTED)

    async def _turn(index: int) -> None:
        turn_id = f"turn_seed_{index}"
        await bus.publish(
            build_envelope(
                event_type=EventType.REFLECT_LIGHT,
                source="brain",
                target="memory_governor",
                session_id="sess_seed",
                turn_id=turn_id,
                correlation_id=turn_id,
                payload=ReflectSignalPayload(
                    trigger_id=f"reflect_seed_{index}",
                    reason="task_result",
                    source_event_id=f"evt_seed_{index}",
                    task_id=f"task_seed_{index}",
                    metadata={
                        "reflection_input": {
                            "session_id": "sess_seed",
                            "turn_id": turn_id,
                            "message_id": f"msg_seed_{index}",
                            "source_type": "task_event",
                            "user_input": "完成一个复杂任务",
                            "assistant_output": "任务已完成，我给你最终结果。",
                            "output": "任务已完成，我给你最终结果。",
                            "channel": "cli",
                            "chat_id": "direct",
                            "task": {"task_id": f"task_seed_{index}", "state": "done", "summary": "复杂任务已收敛"},
                            "execution": {
                                "invoked": True,
                                "status": "done",
                                "summary": "多步执行后收敛为最终结果",
                                "confidence": 0.88,
                                "attempt_count": 2,
                                "missing": [],
                                "failure_reason": "",
                                "recommended_action": "",
                            },
                        }
                    },
                ),
            )
        )
        await _publish_delivery_gate(bus, session_id="sess_seed", turn_id=turn_id, correlation_id=turn_id)
        await bus.drain()

    await _turn(1)
    await _turn(2)

    await bus.publish(
        build_envelope(
            event_type=EventType.REFLECT_DEEP,
            source="reflection",
            target="memory_governor",
            session_id="system:memory",
            turn_id="turn_background_reflection",
            correlation_id="background_reflection",
            payload=ReflectSignalPayload(
                trigger_id="reflect_deep_timer",
                reason="periodic_signal",
                metadata={"trigger": "timer", "warm_limit": 12},
            ),
        )
    )
    await bus.drain()

    assert any(event.payload.metadata.get("reflection_type") == "deep" for event in committed)


def test_memory_governor_runs_deep_signal_without_delivery_gate() -> None:
    with TemporaryDirectory() as tmp_dir:
        asyncio.run(_exercise_deep_signal_runs_without_delivery_gate(Path(tmp_dir)))


async def _exercise_write_request_updates_persona(workspace: Path) -> None:
    bus = PriorityPubSubBus()
    governor = MemoryGovernor(bus=bus, workspace=workspace)
    governor.register()

    committed = _collect(bus, EventType.MEMORY_WRITE_COMMITTED)
    persona_updates = _collect(bus, EventType.MEMORY_UPDATE_PERSONA)

    await bus.publish(
        build_envelope(
            event_type=EventType.MEMORY_WRITE_REQUEST,
            source="reflection",
            target="memory_governor",
            session_id="sess_1",
            turn_id="turn_1",
            correlation_id="turn_1",
            payload=MemoryWriteRequestPayload(
                request_id="memreq_1",
                memory_type="persona",
                summary="复杂问题先收敛架构判断",
                content="复杂问题优先收敛判断，再进入实现，不要一开始就铺很多细节。",
                confidence=0.93,
                evidence_event_ids=["evt_1"],
                source_component="reflection",
            ),
        )
    )
    await bus.drain()

    assert len(committed) == 1
    assert len(persona_updates) == 1
    governance = persona_updates[0].payload.metadata.get("governance", {})
    assert governance.get("action") == "apply"
    assert governance.get("scope") == "deep"
    assert governance.get("version") == 1
    soul = (workspace / "SOUL.md").read_text(encoding="utf-8")
    assert "<!-- DEEP_REFLECTION_SOUL_START -->" in soul
    assert "<!-- TURN_REFLECTION_SOUL_START -->" not in soul
    assert "复杂问题先收敛架构判断" in soul


def test_memory_governor_write_request_updates_persona() -> None:
    with TemporaryDirectory() as tmp_dir:
        asyncio.run(_exercise_write_request_updates_persona(Path(tmp_dir)))


def test_memory_governor_context_prefers_task_scope() -> None:
    with TemporaryDirectory() as tmp_dir:
        governor = MemoryGovernor(bus=PriorityPubSubBus(), workspace=Path(tmp_dir))
        governor._remember("evt_task_1", session_id="sess_1", task_id="task_1")
        governor._remember("evt_task_2", session_id="sess_1", task_id="task_2")

        event = build_envelope(
            event_type=EventType.REFLECT_DEEP,
            source="brain",
            target="memory_governor",
            session_id="sess_1",
            task_id="task_2",
            payload=ReflectSignalPayload(trigger_id="reflect_1", task_id="task_2"),
        )

        assert governor._context_ids_for(event) == ["evt_task_2"]


def test_memory_governor_processed_triggers_are_bounded() -> None:
    with TemporaryDirectory() as tmp_dir:
        governor = MemoryGovernor(bus=PriorityPubSubBus(), workspace=Path(tmp_dir))

        for index in range(governor._MAX_PROCESSED_TRIGGERS + 20):
            governor._remember_trigger(f"reflect_{index}")

        assert len(governor._processed_triggers) == governor._MAX_PROCESSED_TRIGGERS
        assert "reflect_0" not in governor._processed_triggers
        assert f"reflect_{governor._MAX_PROCESSED_TRIGGERS + 19}" in governor._processed_triggers


async def _exercise_governor_rollback_emits_update(workspace: Path) -> None:
    bus = PriorityPubSubBus()
    governor = MemoryGovernor(bus=bus, workspace=workspace)
    governor.register()

    persona_updates = _collect(bus, EventType.MEMORY_UPDATE_PERSONA)
    governor._persona.apply_updates_result("persona", ["先判断再执行"], scope="deep")
    governor._persona.apply_updates_result("persona", ["结论优先返回"], scope="deep")

    result = await governor.rollback_anchor(
        target="persona",
        scope="deep",
        version=1,
        session_id="sess_admin",
        turn_id="turn_admin",
        correlation_id="admin_rollback",
        reason="manual_fix",
    )
    await bus.drain()

    assert result.applied is True
    assert result.rollback_to_version == 1
    assert len(persona_updates) == 1
    governance = persona_updates[0].payload.metadata.get("governance", {})
    assert governance.get("action") == "rollback"
    assert governance.get("scope") == "deep"
    assert governance.get("rollback_to_version") == 1
    soul = (workspace / "SOUL.md").read_text(encoding="utf-8")
    assert "先判断再执行" in soul
    assert "结论优先返回" not in soul


def test_memory_governor_rollback_emits_observable_update() -> None:
    with TemporaryDirectory() as tmp_dir:
        asyncio.run(_exercise_governor_rollback_emits_update(Path(tmp_dir)))
