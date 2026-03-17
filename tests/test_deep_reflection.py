from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from emoticorebot.agent.cognitive import CognitiveEvent
from emoticorebot.agent.reflection.deep import DeepReflectionService
from emoticorebot.config.schema import MemoryConfig, MemoryVectorConfig
from emoticorebot.memory.crystallizer import SkillMaterializer
from emoticorebot.memory import MemoryStore


def test_cognitive_event_recent_prefers_latest_timestamp() -> None:
    with TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir)
        older = CognitiveEvent(
            id="evt_old",
            version="3",
            timestamp="2026-03-14T16:00:00+08:00",
            session_id="cli:direct",
            turn_id="turn_old",
            user_input="old",
            assistant_output="old",
            meta={"importance": 0.95},
        )
        newer = CognitiveEvent(
            id="evt_new",
            version="3",
            timestamp="2026-03-14T17:00:00+08:00",
            session_id="cli:direct",
            turn_id="turn_new",
            user_input="new",
            assistant_output="new",
            meta={"importance": 0.10},
        )
        CognitiveEvent.append(workspace, older)
        CognitiveEvent.append(workspace, newer)

        rows = CognitiveEvent.recent(workspace, limit=1)

        assert len(rows) == 1
        assert rows[0]["id"] == "evt_new"


def test_deep_reflection_normalize_skill_hints_uses_skill_hint_type() -> None:
    records = DeepReflectionService._normalize_skill_hints(
        [
            {
                "summary": "复杂任务优先走最终结果式执行",
                "content": "对于复杂任务，优先让 task 在单次执行中收敛到最终结果。",
                "trigger": "需要多步执行或工具组合时",
                "hint": "减少中间汇报，优先给最终结果。",
                "skill_name": "final-result-execution",
            }
        ]
    )

    assert len(records) == 1
    assert records[0]["type"] == "skill_hint"
    assert records[0]["payload"]["skill_name"] == "final-result-execution"


def test_deep_reflection_derive_skill_names_from_content_when_missing() -> None:
    records = DeepReflectionService._normalize_skill_hints(
        [
            {
                "summary": "处理代码重构时优先先写测试",
                "content": "先补测试再改代码",
                "trigger": "代码重构",
                "hint": "先写测试",
            },
            {
                "summary": "陪伴对话里多用选项题收敛",
                "content": "给用户2到3个选项收敛需求",
                "trigger": "陪伴对话",
                "hint": "给选项题",
            },
        ]
    )

    assert len(records) == 2
    assert records[0]["payload"]["skill_name"] != records[1]["payload"]["skill_name"]
    assert all(record["payload"]["skill_name"] != "unnamed-skill" for record in records)


def test_deep_reflection_event_block_includes_updates_and_state() -> None:
    block = DeepReflectionService._build_event_block(
        [
            {
                "id": "evt_1",
                "timestamp": "2026-03-14T17:00:00+08:00",
                "user_input": "你好",
                "assistant_output": "你好。",
                "brain_state": {
                    "emotion": "开心",
                    "pad": {"pleasure": 0.7, "arousal": 0.4, "dominance": 0.5},
                    "drives": {"social": 60.0, "energy": 80.0},
                },
                "turn_reflection": {
                    "summary": "正常寒暄。",
                    "problems": ["语气偏轻佻"],
                    "user_updates": ["用户偏好中性开场。"],
                    "soul_updates": ["寒暄时避免过度拟人化。"],
                    "state_update": {
                        "should_apply": False,
                        "confidence": 0.7,
                        "reason": "当前状态合理。",
                        "pad_delta": {"pleasure": 0.7, "arousal": 0.4, "dominance": 0.5},
                        "drives_delta": {"social": 60.0, "energy": 80.0},
                    },
                },
                "task": {"status": "done"},
            }
        ]
    )

    assert "emotion=开心" in block
    assert "user_updates=[" in block
    assert "soul_updates=[" in block
    assert "state_update=" in block
    assert '"should_apply": false' in block


def test_skill_materializer_accepts_legacy_skill_type() -> None:
    with TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir)
        store = MemoryStore(
            workspace,
            memory_config=MemoryConfig(vector=MemoryVectorConfig(backend="")),
        )
        store.append_many(
            [
                {
                    "audience": "task",
                    "kind": "procedural",
                    "type": "skill",
                    "summary": "暧昧场景用接住情绪加选项题推进对话",
                    "content": "先接住情绪，再给2到3个互斥选项，让用户低成本选择后续互动方式。",
                    "payload": {
                        "skill_id": "skill_affection_choices",
                        "skill_name": "affection-acknowledge-and-choices",
                        "trigger": "用户表达夸赞或好感时",
                        "hint": "先接住情绪，再给2到3个选项。",
                    },
                },
                {
                    "audience": "task",
                    "kind": "procedural",
                    "type": "skill_hint",
                    "summary": "用有限选项收敛暧昧/陪伴需求",
                    "content": "当用户给出夸赞或模糊陪伴诉求时，用2到4个选项快速收敛需求和语气。",
                    "payload": {
                        "skill_id": "skill_choice_based_affective_clarification",
                        "skill_name": "choice-based-affective-clarification",
                        "trigger": "用户表达情绪需求但不够具体时",
                        "hint": "给出2到4个可选项，让用户选一个再继续。",
                    },
                },
            ]
        )

        result = SkillMaterializer(workspace, store).materialize_from_memory()

        skill_files = list((workspace / "skills").rglob("SKILL.md"))
        assert len(skill_files) == 1
        assert result.created_count == 1
