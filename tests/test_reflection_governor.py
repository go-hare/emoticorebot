from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.reflection.governor import ReflectionGovernor
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.reflection_models import ReflectionWriteRequestPayload, ReflectionSignalPayload
from emoticorebot.protocol.task_models import ProtocolModel
from emoticorebot.protocol.topics import EventType


def _collect(bus: PriorityPubSubBus, event_type: str) -> list[BusEnvelope[ProtocolModel]]:
    captured: list[BusEnvelope[ProtocolModel]] = []

    async def _capture(event: BusEnvelope[ProtocolModel]) -> None:
        captured.append(event)

    bus.subscribe(consumer=f"test:{event_type}", event_type=event_type, handler=_capture)
    return captured


class _ReflectionLLM:
    def __init__(
        self,
        *,
        turn_needs_deep_reflection: bool = False,
        turn_user_updates: list[str] | None = None,
        turn_soul_updates: list[str] | None = None,
        deep_user_updates: list[str] | None = None,
        deep_soul_updates: list[str] | None = None,
    ):
        self.turn_needs_deep_reflection = turn_needs_deep_reflection
        self.turn_user_updates = list(turn_user_updates or [])
        self.turn_soul_updates = list(turn_soul_updates or [])
        self.deep_user_updates = list(deep_user_updates or ["用户希望复杂任务先收敛判断，再查看最终结果。"])
        self.deep_soul_updates = list(deep_soul_updates or ["复杂任务中先收敛判断，再交给 task 执行。"])

    def with_structured_output(self, _schema):
        return self

    async def ainvoke(self, prompt):
        if "最近的认知事件：" in prompt:
            return {
                "summary": "近期多轮执行表明，复杂任务更适合在内部先收敛再输出最终结果。",
                "memory_candidates": [
                    {
                        "memory_type": "execution",
                        "summary": "复杂任务优先走最终结果式执行",
                        "detail": "当任务需要多步分析和工具配合时，优先让 task 在内部先收敛，再把最终结果交回 left_brain。",
                        "confidence": 0.88,
                        "stability": 0.82,
                        "tags": ["skill", "hint"],
                        "metadata": {
                            "subtype": "skill_hint",
                            "importance": 7,
                            "skill_id": "skill_final_result_execution",
                            "skill_name": "final-result-execution",
                            "trigger": "需要多步执行或工具组合时",
                            "hint": "减少中间态汇报，优先收敛到最终结果。",
                            "applies_to_tools": [],
                        },
                    },
                    {
                        "memory_type": "execution",
                        "summary": "多步工具任务先在内部收敛再输出最终结果",
                        "detail": "当任务要连续读文件、修参数、验证结果时，优先在 task 内部完成收敛，再统一返回最终结果。",
                        "confidence": 0.85,
                        "stability": 0.8,
                        "tags": ["skill", "hint"],
                        "metadata": {
                            "subtype": "skill_hint",
                            "importance": 7,
                            "skill_id": "skill_final_result_execution_variant",
                            "skill_name": "final-result-execution",
                            "trigger": "需要多步执行或工具组合时",
                            "hint": "先在 task 内部解决中间错误，再统一输出最终结果。",
                            "applies_to_tools": [],
                        },
                    }
                ],
                "user_updates": list(self.deep_user_updates),
                "soul_updates": list(self.deep_soul_updates),
            }

        has_trace = "- task_trace: []" not in prompt
        if has_trace:
            return {
                "summary": "本轮围绕执行链路完成了一次真实反思。",
                "problems": ["执行中存在需要记录的阻塞。"],
                "resolution": "梳理问题后完成执行。",
                "outcome": "success",
                "next_hint": "下次先检查阻塞点。",
                "needs_deep_reflection": self.turn_needs_deep_reflection,
                "user_updates": list(self.turn_user_updates),
                "soul_updates": list(self.turn_soul_updates),
                "state_update": {
                    "should_apply": False,
                    "confidence": 0.5,
                    "reason": "状态稳定。",
                    "pad_delta": {"pleasure": 0.0, "arousal": 0.0, "dominance": 0.0},
                    "drives_delta": {"social": 50.0, "energy": 50.0},
                },
                "memory_candidates": [
                    {
                        "memory_type": "reflection",
                        "summary": "本轮完成真实反思",
                        "detail": "反思里保留了关键执行问题与解决方式。",
                        "confidence": 0.8,
                        "stability": 0.5,
                        "tags": ["reflection"],
                        "metadata": {"subtype": "turn_insight", "importance": 6},
                    }
                ],
                "execution_review": {
                    "effectiveness": "medium",
                    "main_failure_reason": "执行中存在需要记录的阻塞。",
                    "next_execution_hint": "下次先检查阻塞点。",
                },
            }
        return {
            "summary": "本轮完成真实反思。",
            "problems": [],
            "resolution": "执行结束。",
            "outcome": "success",
            "next_hint": "继续推进。",
            "needs_deep_reflection": self.turn_needs_deep_reflection,
            "user_updates": list(self.turn_user_updates),
            "soul_updates": list(self.turn_soul_updates),
            "state_update": {
                "should_apply": False,
                "confidence": 0.5,
                "reason": "状态稳定。",
                "pad_delta": {"pleasure": 0.0, "arousal": 0.0, "dominance": 0.0},
                "drives_delta": {"social": 50.0, "energy": 50.0},
            },
            "memory_candidates": [
                {
                    "memory_type": "reflection",
                    "summary": "本轮完成真实反思",
                    "detail": "执行链路完成并写入认知事件。",
                    "confidence": 0.8,
                    "stability": 0.5,
                    "tags": ["reflection"],
                    "metadata": {"subtype": "turn_insight", "importance": 6},
                }
            ],
            "execution_review": {
                "effectiveness": "high",
                "main_failure_reason": "",
                "next_execution_hint": "",
            },
        }


async def _exercise_turn_reflection_persistence(workspace: Path) -> None:
    bus = PriorityPubSubBus()
    governor = ReflectionGovernor(bus=bus, workspace=workspace, reflection_llm=_ReflectionLLM())
    governor.register()

    committed = _collect(bus, EventType.REFLECTION_WRITE_COMMITTED)

    await bus.publish(
        build_envelope(
            event_type=EventType.REFLECTION_LIGHT,
            source="left_runtime",
            target="reflection_governor",
            session_id="sess_1",
            turn_id="turn_1",
            correlation_id="turn_1",
            payload=ReflectionSignalPayload(
                trigger_id="reflection_1",
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
                            "failure_reason": "",
                        },
                    }
                },
            ),
        )
    )
    await bus.drain()

    assert committed == []
    cognitive = (workspace / "memory" / "cognitive_events.jsonl").read_text(encoding="utf-8")
    assert "帮我分析一下这个问题" in cognitive
    assert "我先帮你拆一下结构" in cognitive
    assert not (workspace / "memory" / "memory.jsonl").exists()


def test_reflection_governor_persists_turn_reflection_on_signal() -> None:
    with TemporaryDirectory() as tmp_dir:
        asyncio.run(_exercise_turn_reflection_persistence(Path(tmp_dir)))


async def _exercise_turn_reflection_updates_user_and_soul(workspace: Path) -> None:
    bus = PriorityPubSubBus()
    governor = ReflectionGovernor(
        bus=bus,
        workspace=workspace,
        reflection_llm=_ReflectionLLM(
            turn_user_updates=["用户更喜欢先讨论结构，再进入实现。"],
            turn_soul_updates=["遇到复杂问题时先讲结构，再展开代码细节。"],
        ),
    )
    governor.register()

    user_updates = _collect(bus, EventType.REFLECTION_UPDATE_USER_MODEL)
    persona_updates = _collect(bus, EventType.REFLECTION_UPDATE_PERSONA)

    await bus.publish(
        build_envelope(
            event_type=EventType.REFLECTION_LIGHT,
            source="left_runtime",
            target="reflection_governor",
            session_id="sess_turn_updates",
            turn_id="turn_turn_updates",
            correlation_id="turn_turn_updates",
            payload=ReflectionSignalPayload(
                trigger_id="reflection_turn_updates",
                reason="user_turn",
                source_event_id="evt_turn_updates",
                metadata={
                    "reflection_input": {
                        "session_id": "sess_turn_updates",
                        "turn_id": "turn_turn_updates",
                        "message_id": "msg_turn_updates",
                        "source_type": "user_turn",
                        "user_input": "先帮我讲结构，再动代码。",
                        "assistant_output": "我先把结构和改动边界梳理清楚。",
                        "output": "我先把结构和改动边界梳理清楚。",
                        "channel": "cli",
                        "chat_id": "direct",
                        "task": {"state": "done", "summary": "已完成结构梳理"},
                        "execution": {
                            "invoked": True,
                            "status": "done",
                            "summary": "完成一次结构化说明",
                            "failure_reason": "",
                        },
                    }
                },
            ),
        )
    )
    await bus.drain()

    user = (workspace / "USER.md").read_text(encoding="utf-8")
    soul = (workspace / "SOUL.md").read_text(encoding="utf-8")

    assert "<!-- TURN_REFLECTION_USER_START -->" in user
    assert "用户更喜欢先讨论结构，再进入实现。" in user
    assert "<!-- TURN_REFLECTION_SOUL_START -->" in soul
    assert "遇到复杂问题时先讲结构，再展开代码细节。" in soul
    assert len(user_updates) == 1
    assert len(persona_updates) == 1
    assert user_updates[0].payload.metadata.get("governance", {}).get("scope") == "turn"
    assert persona_updates[0].payload.metadata.get("governance", {}).get("scope") == "turn"
    assert not (workspace / "memory" / "memory.jsonl").exists()


def test_reflection_governor_turn_reflection_updates_user_and_soul() -> None:
    with TemporaryDirectory() as tmp_dir:
        asyncio.run(_exercise_turn_reflection_updates_user_and_soul(Path(tmp_dir)))


async def _exercise_right_brain_reflection_without_delivery_gate(workspace: Path) -> None:
    bus = PriorityPubSubBus()
    governor = ReflectionGovernor(bus=bus, workspace=workspace, reflection_llm=_ReflectionLLM())
    governor.register()

    committed = _collect(bus, EventType.REFLECTION_WRITE_COMMITTED)
    warnings = _collect(bus, EventType.SYSTEM_WARNING)

    await bus.publish(
        build_envelope(
            event_type=EventType.REFLECTION_LIGHT,
            source="right_runtime",
            target="reflection_governor",
            session_id="sess_right_1",
            turn_id="turn_right_1",
            task_id="task_right_1",
            correlation_id="task_right_1",
            payload=ReflectionSignalPayload(
                trigger_id="reflection_right_1",
                reason="right_brain_result",
                source_event_id="evt_right_1",
                task_id="task_right_1",
                metadata={
                    "right_brain_summary": {
                        "session_id": "sess_right_1",
                        "turn_id": "turn_right_1",
                        "origin_message": {
                            "channel": "cli",
                            "chat_id": "direct",
                            "message_id": "msg_right_1",
                        },
                        "request_text": "整理一下反思链路",
                        "summary": "反思链路已整理完成",
                        "result_text": "已经按模块梳理了反思入口和职责。",
                        "result": "success",
                        "decision": "accept",
                        "task": {
                            "task_id": "task_right_1",
                            "state": "done",
                            "result": "success",
                            "summary": "反思链路已整理完成",
                        },
                        "task_trace": [
                            {
                                "kind": "tool",
                                "message": "读取 governor.py",
                                "data": {"tool_name": "read_file", "event": "task.tool", "phase": "tool"},
                            }
                        ],
                        "tool_usage_summary": [
                            {"tool_name": "read_file", "message": "读取 governor.py", "phase": "tool"}
                        ],
                        "recent_turns": [
                            {"role": "user", "content": "看一下反思"},
                            {"role": "assistant", "content": "我先核对右脑和 ReflectionGovernor 的链路。"},
                        ],
                        "short_term_memory": ["用户要求严格按模块实现"],
                        "long_term_memory": ["用户不需要兼容旧架构"],
                        "memory_refs": ["反思模块按异步触发处理"],
                        "tool_context": {"available_tools": ["read_file"], "tool_constraints": []},
                    }
                },
            ),
        )
    )
    await bus.drain()

    assert committed == []
    assert warnings == []
    cognitive = (workspace / "memory" / "cognitive_events.jsonl").read_text(encoding="utf-8")
    assert "整理一下反思链路" in cognitive
    assert "已经按模块梳理了反思入口和职责" in cognitive
    assert not (workspace / "memory" / "memory.jsonl").exists()


def test_reflection_governor_persists_right_brain_reflection_without_delivery_gate() -> None:
    with TemporaryDirectory() as tmp_dir:
        asyncio.run(_exercise_right_brain_reflection_without_delivery_gate(Path(tmp_dir)))


async def _exercise_turn_reflection_without_llm_emits_warning(workspace: Path) -> None:
    bus = PriorityPubSubBus()
    governor = ReflectionGovernor(bus=bus, workspace=workspace)
    governor.register()

    committed = _collect(bus, EventType.REFLECTION_WRITE_COMMITTED)
    warnings = _collect(bus, EventType.SYSTEM_WARNING)

    await bus.publish(
        build_envelope(
            event_type=EventType.REFLECTION_LIGHT,
            source="left_runtime",
            target="reflection_governor",
            session_id="sess_warn",
            turn_id="turn_warn",
            correlation_id="turn_warn",
            payload=ReflectionSignalPayload(
                trigger_id="reflection_warn",
                reason="task_result",
                source_event_id="evt_warn",
                metadata={
                    "reflection_input": {
                        "session_id": "sess_warn",
                        "turn_id": "turn_warn",
                        "message_id": "msg_warn",
                        "source_type": "task_event",
                        "user_input": "看一下反思",
                        "assistant_output": "我来总结。",
                        "output": "我来总结。",
                        "channel": "cli",
                        "chat_id": "direct",
                        "task": {"state": "done", "summary": "执行结束"},
                        "execution": {"invoked": True, "status": "done", "summary": "执行结束", "failure_reason": ""},
                    }
                },
            ),
        )
    )
    await bus.drain()

    assert committed == []
    assert len(warnings) == 1
    assert warnings[0].payload.reason == "turn_reflection_llm_unavailable"


def test_reflection_governor_warns_when_turn_reflection_llm_is_missing() -> None:
    with TemporaryDirectory() as tmp_dir:
        asyncio.run(_exercise_turn_reflection_without_llm_emits_warning(Path(tmp_dir)))


async def _exercise_periodic_deep_reflection(workspace: Path) -> None:
    bus = PriorityPubSubBus()
    governor = ReflectionGovernor(bus=bus, workspace=workspace, reflection_llm=_ReflectionLLM())
    governor.register()

    committed = _collect(bus, EventType.REFLECTION_WRITE_COMMITTED)
    governor._memory_store.append_many(
        [
            {
                "memory_type": "execution",
                "summary": "复杂任务优先走最终结果式执行",
                "detail": "复杂任务应尽量在单次执行里先收敛，再把最终结果交回 brain。",
                "confidence": 0.8,
                "stability": 0.85,
                "tags": ["skill", "hint"],
                "metadata": {
                    "subtype": "skill_hint",
                    "importance": 7,
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
                event_type=EventType.REFLECTION_LIGHT,
                source="left_runtime",
                target="reflection_governor",
                session_id="sess_1",
                turn_id=turn_id,
                correlation_id=turn_id,
                payload=ReflectionSignalPayload(
                    trigger_id=f"reflection_{index}",
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
                                "failure_reason": "",
                            },
                        }
                    },
                ),
            )
        )
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


def test_reflection_governor_runs_periodic_deep_reflection() -> None:
    with TemporaryDirectory() as tmp_dir:
        asyncio.run(_exercise_periodic_deep_reflection(Path(tmp_dir)))


async def _exercise_turn_reflection_triggers_deep_followup(workspace: Path) -> None:
    bus = PriorityPubSubBus()
    governor = ReflectionGovernor(
        bus=bus,
        workspace=workspace,
        reflection_llm=_ReflectionLLM(turn_needs_deep_reflection=True),
    )
    governor.register()

    committed = _collect(bus, EventType.REFLECTION_WRITE_COMMITTED)
    warnings = _collect(bus, EventType.SYSTEM_WARNING)

    await bus.publish(
        build_envelope(
            event_type=EventType.REFLECTION_LIGHT,
            source="left_runtime",
            target="reflection_governor",
            session_id="sess_deep_followup",
            turn_id="turn_deep_followup",
            correlation_id="turn_deep_followup",
            payload=ReflectionSignalPayload(
                trigger_id="reflection_deep_followup",
                reason="task_result",
                source_event_id="evt_deep_followup",
                task_id="task_deep_followup",
                metadata={
                    "reflection_input": {
                        "session_id": "sess_deep_followup",
                        "turn_id": "turn_deep_followup",
                        "message_id": "msg_deep_followup",
                        "source_type": "task_event",
                        "user_input": "这轮虽然结束了，但你判断一下还需不需要继续深反思",
                        "assistant_output": "这轮我先完成浅反思，再看是否要继续抽象经验。",
                        "output": "这轮我先完成浅反思，再看是否要继续抽象经验。",
                        "channel": "cli",
                        "chat_id": "direct",
                        "task": {
                            "task_id": "task_deep_followup",
                            "state": "done",
                            "result": "success",
                            "summary": "已完成本轮处理",
                        },
                        "task_trace": [
                            {
                                "kind": "tool",
                                "message": "exec 返回：No such file or directory",
                                "data": {"tool_name": "exec", "event": "task.trace"},
                            }
                        ],
                        "execution": {
                            "invoked": True,
                            "status": "done",
                            "summary": "收敛后完成执行",
                            "failure_reason": "",
                        },
                    }
                },
            ),
        )
    )
    await bus.drain()

    cognitive_rows = [
        json.loads(line)
        for line in (workspace / "memory" / "cognitive_events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(cognitive_rows) == 1
    assert cognitive_rows[0]["turn_reflection"]["needs_deep_reflection"] is True

    memory_rows = [
        json.loads(line)
        for line in (workspace / "memory" / "memory.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(str((row.get("metadata") or {}).get("subtype", "")) == "skill_hint" for row in memory_rows)
    assert any(str((row.get("metadata") or {}).get("subtype", "")) == "user_model" for row in memory_rows)
    assert any(str((row.get("metadata") or {}).get("subtype", "")) == "persona" for row in memory_rows)
    user = (workspace / "USER.md").read_text(encoding="utf-8")
    soul = (workspace / "SOUL.md").read_text(encoding="utf-8")
    assert "<!-- DEEP_REFLECTION_USER_START -->" in user
    assert "用户希望复杂任务先收敛判断，再查看最终结果。" in user
    assert "<!-- DEEP_REFLECTION_SOUL_START -->" in soul
    assert "复杂任务中先收敛判断，再交给 task 执行。" in soul
    assert any(event.payload.metadata.get("reflection_type") == "deep" for event in committed)
    assert warnings == []


def test_reflection_governor_triggers_deep_followup_when_turn_requests_it() -> None:
    with TemporaryDirectory() as tmp_dir:
        asyncio.run(_exercise_turn_reflection_triggers_deep_followup(Path(tmp_dir)))


async def _exercise_reflection_pipeline_from_turn_to_deep_to_crystallization(workspace: Path) -> None:
    bus = PriorityPubSubBus()
    governor = ReflectionGovernor(bus=bus, workspace=workspace, reflection_llm=_ReflectionLLM())
    governor.register()

    committed = _collect(bus, EventType.REFLECTION_WRITE_COMMITTED)
    warnings = _collect(bus, EventType.SYSTEM_WARNING)

    async def _turn(index: int) -> None:
        turn_id = f"turn_pipeline_{index}"
        await bus.publish(
            build_envelope(
                event_type=EventType.REFLECTION_LIGHT,
                source="left_runtime",
                target="reflection_governor",
                session_id="sess_pipeline",
                turn_id=turn_id,
                correlation_id=turn_id,
                payload=ReflectionSignalPayload(
                    trigger_id=f"reflection_pipeline_{index}",
                    reason="task_result",
                    source_event_id=f"evt_pipeline_{index}",
                    task_id=f"task_pipeline_{index}",
                    metadata={
                        "reflection_input": {
                            "session_id": "sess_pipeline",
                            "turn_id": turn_id,
                            "message_id": f"msg_pipeline_{index}",
                            "source_type": "task_event",
                            "user_input": "把这次执行链路整理成稳定做法",
                            "assistant_output": "我先收敛过程，再给你最后结果。",
                            "output": "我先收敛过程，再给你最后结果。",
                            "channel": "cli",
                            "chat_id": "direct",
                            "task": {
                                "task_id": f"task_pipeline_{index}",
                                "state": "done",
                                "result": "success",
                                "summary": "已经完成执行链路整理",
                            },
                            "task_trace": [
                                {
                                    "kind": "tool",
                                    "message": "read_file 返回：Error: Invalid parameters",
                                    "data": {"tool_name": "read_file", "event": "task.trace"},
                                },
                                {
                                    "kind": "tool",
                                    "message": "exec 返回：No such file or directory",
                                    "data": {"tool_name": "exec", "event": "task.trace"},
                                },
                            ],
                            "execution": {
                                "invoked": True,
                                "status": "done",
                                "summary": "多步执行后收敛为最终结果",
                                "failure_reason": "",
                            },
                        }
                    },
                ),
            )
        )
        await bus.drain()

    await _turn(1)
    await _turn(2)

    cognitive_path = workspace / "memory" / "cognitive_events.jsonl"
    cognitive_rows = [
        json.loads(line)
        for line in cognitive_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(cognitive_rows) == 2
    assert all(row["turn_reflection"]["summary"] for row in cognitive_rows)
    assert any(row["turn_reflection"]["problems"] for row in cognitive_rows)

    result = await governor.run_deep_reflection(reason="pipeline_test", warm_limit=15)
    await bus.drain()

    memory_path = workspace / "memory" / "memory.jsonl"
    memory_rows = [
        json.loads(line)
        for line in memory_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(str((row.get("metadata") or {}).get("subtype", "")) == "skill_hint" for row in memory_rows)
    assert any(str((row.get("metadata") or {}).get("subtype", "")) == "user_model" for row in memory_rows)
    assert any(str((row.get("metadata") or {}).get("subtype", "")) == "persona" for row in memory_rows)

    skill_file = workspace / "skills" / "final-result-execution" / "SKILL.md"
    assert skill_file.exists()
    skill_text = skill_file.read_text(encoding="utf-8")
    assert "该技能由重复出现的 `skill_hint` 记忆自动沉淀生成。" in skill_text
    assert "减少中间态汇报，优先收敛到最终结果。" in skill_text
    assert result.materialized_skill_count >= 1
    assert result.updated_user is True
    assert result.updated_soul is True
    assert any(event.payload.metadata.get("reflection_type") == "deep" for event in committed)
    assert warnings == []


def test_reflection_pipeline_from_turn_to_deep_to_crystallization() -> None:
    with TemporaryDirectory() as tmp_dir:
        asyncio.run(_exercise_reflection_pipeline_from_turn_to_deep_to_crystallization(Path(tmp_dir)))


async def _exercise_deep_signal_runs_without_delivery_gate(workspace: Path) -> None:
    bus = PriorityPubSubBus()
    governor = ReflectionGovernor(bus=bus, workspace=workspace, reflection_llm=_ReflectionLLM())
    governor.register()

    committed = _collect(bus, EventType.REFLECTION_WRITE_COMMITTED)

    async def _turn(index: int) -> None:
        turn_id = f"turn_seed_{index}"
        await bus.publish(
            build_envelope(
                event_type=EventType.REFLECTION_LIGHT,
                source="left_runtime",
                target="reflection_governor",
                session_id="sess_seed",
                turn_id=turn_id,
                correlation_id=turn_id,
                payload=ReflectionSignalPayload(
                    trigger_id=f"reflection_seed_{index}",
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
                                "failure_reason": "",
                            },
                        }
                    },
                ),
            )
        )
        await bus.drain()

    await _turn(1)
    await _turn(2)

    await bus.publish(
        build_envelope(
            event_type=EventType.REFLECTION_DEEP,
            source="reflection",
            target="reflection_governor",
            session_id="system:memory",
            turn_id="turn_background_reflection",
            correlation_id="background_reflection",
            payload=ReflectionSignalPayload(
                trigger_id="reflection_deep_timer",
                reason="periodic_signal",
                metadata={"trigger": "timer", "warm_limit": 12},
            ),
        )
    )
    await bus.drain()

    assert any(event.payload.metadata.get("reflection_type") == "deep" for event in committed)


def test_reflection_governor_runs_deep_signal_without_delivery_gate() -> None:
    with TemporaryDirectory() as tmp_dir:
        asyncio.run(_exercise_deep_signal_runs_without_delivery_gate(Path(tmp_dir)))


async def _exercise_write_request_updates_persona(workspace: Path) -> None:
    bus = PriorityPubSubBus()
    governor = ReflectionGovernor(bus=bus, workspace=workspace)
    governor.register()

    committed = _collect(bus, EventType.REFLECTION_WRITE_COMMITTED)
    persona_updates = _collect(bus, EventType.REFLECTION_UPDATE_PERSONA)

    await bus.publish(
        build_envelope(
            event_type=EventType.REFLECTION_WRITE_REQUEST,
            source="reflection",
            target="reflection_governor",
            session_id="sess_1",
            turn_id="turn_1",
            correlation_id="turn_1",
            payload=ReflectionWriteRequestPayload(
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


def test_reflection_governor_write_request_updates_persona() -> None:
    with TemporaryDirectory() as tmp_dir:
        asyncio.run(_exercise_write_request_updates_persona(Path(tmp_dir)))


def test_reflection_governor_context_prefers_task_scope() -> None:
    with TemporaryDirectory() as tmp_dir:
        governor = ReflectionGovernor(bus=PriorityPubSubBus(), workspace=Path(tmp_dir))
        governor._remember("evt_task_1", session_id="sess_1", task_id="task_1")
        governor._remember("evt_task_2", session_id="sess_1", task_id="task_2")

        event = build_envelope(
            event_type=EventType.REFLECTION_DEEP,
            source="left_runtime",
            target="reflection_governor",
            session_id="sess_1",
            task_id="task_2",
            payload=ReflectionSignalPayload(trigger_id="reflection_1", task_id="task_2"),
        )

        assert governor._context_ids_for(event) == ["evt_task_2"]


def test_reflection_governor_processed_reflection_triggers_are_bounded() -> None:
    with TemporaryDirectory() as tmp_dir:
        governor = ReflectionGovernor(bus=PriorityPubSubBus(), workspace=Path(tmp_dir))

        for index in range(governor._MAX_PROCESSED_REFLECTION_TRIGGERS + 20):
            governor._remember_reflection_trigger(f"reflection_{index}")

        assert len(governor._processed_reflection_triggers) == governor._MAX_PROCESSED_REFLECTION_TRIGGERS
        assert "reflection_0" not in governor._processed_reflection_triggers
        assert f"reflection_{governor._MAX_PROCESSED_REFLECTION_TRIGGERS + 19}" in governor._processed_reflection_triggers


async def _exercise_governor_rollback_emits_update(workspace: Path) -> None:
    bus = PriorityPubSubBus()
    governor = ReflectionGovernor(bus=bus, workspace=workspace)
    governor.register()

    persona_updates = _collect(bus, EventType.REFLECTION_UPDATE_PERSONA)
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


def test_reflection_governor_rollback_emits_observable_update() -> None:
    with TemporaryDirectory() as tmp_dir:
        asyncio.run(_exercise_governor_rollback_emits_update(Path(tmp_dir)))
