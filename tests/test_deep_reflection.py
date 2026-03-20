from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from emoticorebot.reflection.cognitive import CognitiveEvent
from emoticorebot.reflection.deep import DeepReflectionService, DeepReflectionUnavailable
from emoticorebot.reflection.candidates import build_skill_hint_candidate
from emoticorebot.config.schema import MemoryConfig, MemoryVectorConfig
from emoticorebot.memory.crystallizer import SkillMaterializer
from emoticorebot.memory import MemoryStore


def test_cognitive_event_recent_prefers_latest_timestamp() -> None:
    with TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir)
        older = CognitiveEvent(
            id="evt_old",
            schema_version="cognitive_event.v1",
            timestamp="2026-03-14T16:00:00+08:00",
            session_id="cli:direct",
            turn_id="turn_old",
            user_input="old",
            assistant_output="old",
            meta={"importance": 0.95},
        )
        newer = CognitiveEvent(
            id="evt_new",
            schema_version="cognitive_event.v1",
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


def test_memory_store_migrates_legacy_long_term_path() -> None:
    with TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir)
        legacy_dir = workspace / "memory" / "long_term"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        legacy_path = legacy_dir / "memory.jsonl"
        legacy_path.write_text(
            '{"memory_id":"mem_1","memory_type":"fact","summary":"legacy","detail":"legacy"}\n',
            encoding="utf-8",
        )

        store = MemoryStore(workspace)
        rows = store.read_all()

        assert len(rows) == 1
        assert rows[0]["memory_id"] == "mem_1"
        assert (workspace / "memory" / "memory.jsonl").exists()
        assert not legacy_path.exists()


def test_build_skill_hint_candidate_uses_formal_execution_schema() -> None:
    record = build_skill_hint_candidate(
        summary="复杂任务优先走最终结果式执行",
        detail="对于复杂任务，优先让 task 在单次执行中收敛到最终结果。",
        trigger="需要多步执行或工具组合时",
        hint="减少中间汇报，优先给最终结果。",
        skill_name="final-result-execution",
    )

    assert record["memory_type"] == "execution"
    assert record["metadata"]["subtype"] == "skill_hint"
    assert record["metadata"]["skill_name"] == "final-result-execution"


def test_build_skill_hint_candidate_derives_skill_name_when_missing() -> None:
    first = build_skill_hint_candidate(
        summary="处理代码重构时优先先写测试",
        detail="先补测试再改代码",
        trigger="代码重构",
        hint="先写测试",
        skill_name="",
    )
    second = build_skill_hint_candidate(
        summary="陪伴对话里多用选项题收敛",
        detail="给用户2到3个选项收敛需求",
        trigger="陪伴对话",
        hint="给选项题",
        skill_name="",
    )

    assert first["metadata"]["skill_name"] != second["metadata"]["skill_name"]
    assert first["metadata"]["skill_name"] != ""
    assert second["metadata"]["skill_name"] != ""


def test_deep_reflection_event_block_includes_updates_and_state() -> None:
    block = DeepReflectionService._build_event_block(
        [
            {
                "id": "evt_1",
                "timestamp": "2026-03-14T17:00:00+08:00",
                "user_input": "你好",
                "assistant_output": "你好。",
                "main_brain_state": {
                    "emotion": "开心",
                    "pad": {"pleasure": 0.7, "arousal": 0.4, "dominance": 0.5},
                    "drives": {"social": 60.0, "energy": 80.0},
                },
                "turn_reflection": {
                    "summary": "正常寒暄。",
                    "problems": ["语气偏轻佻"],
                    "needs_deep_reflection": True,
                    "user_updates": ["用户偏好中性开场。"],
                    "soul_updates": ["寒暄时避免过度拟人化。"],
                    "state_update": {
                        "should_apply": False,
                        "confidence": 0.7,
                        "reason": "当前状态合理。",
                        "pad_state": {"pleasure": 0.7, "arousal": 0.4, "dominance": 0.5},
                        "drives_state": {"social": 60.0, "energy": 80.0},
                    },
                },
                "task": {"status": "done"},
            }
        ]
    )

    assert "emotion=开心" in block
    assert "user_updates=[" in block
    assert "soul_updates=[" in block
    assert "needs_deep_reflection=true" in block
    assert "state_update=" in block
    assert '"should_apply": false' in block


def test_skill_materializer_accepts_formal_skill_hint_records() -> None:
    with TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir)
        store = MemoryStore(
            workspace,
            memory_config=MemoryConfig(vector=MemoryVectorConfig(backend="")),
        )
        store.append_many(
            [
                {
                    "memory_type": "execution",
                    "summary": "暧昧场景用接住情绪加选项题推进对话",
                    "detail": "先接住情绪，再给2到3个互斥选项，让用户低成本选择后续互动方式。",
                    "metadata": {
                        "subtype": "skill_hint",
                        "skill_id": "skill_affection_choices",
                        "skill_name": "affection-acknowledge-and-choices",
                        "trigger": "用户表达夸赞或好感时",
                        "hint": "先接住情绪，再给2到3个选项。",
                    },
                },
                {
                    "memory_type": "execution",
                    "summary": "用有限选项收敛暧昧/陪伴需求",
                    "detail": "当用户给出夸赞或模糊陪伴诉求时，用2到4个选项快速收敛需求和语气。",
                    "metadata": {
                        "subtype": "skill_hint",
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


class _StructuredDeepResponse:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self):
        return dict(self._payload)


class _StructuredDeepLLM:
    def __init__(self, payload):
        self.payload = payload

    def with_structured_output(self, _schema):
        return self

    async def ainvoke(self, prompt):
        assert "最近的认知事件：" in prompt
        return _StructuredDeepResponse(self.payload)


def test_deep_reflection_service_requires_real_model_result() -> None:
    service = DeepReflectionService(llm=None)

    try:
        asyncio.run(
            service.propose(
                [
                    {
                        "id": "evt_1",
                        "timestamp": "2026-03-14T17:00:00+08:00",
                        "user_input": "完成一个复杂任务",
                        "assistant_output": "任务已完成，我给你最终结果。",
                        "turn_reflection": {"summary": "这轮有稳定模式。"},
                        "task": {"state": "done", "result": "success"},
                    }
                ]
            )
        )
    except DeepReflectionUnavailable as exc:
        assert exc.reason == "deep_reflection_llm_unavailable"
    else:
        raise AssertionError("missing deep reflection llm should not fall back to templated memories")


def test_deep_reflection_service_accepts_model_dump_response() -> None:
    service = DeepReflectionService(
        llm=_StructuredDeepLLM(
            {
                "summary": "近期复杂任务更适合先在内部收敛，再输出最终结果。",
                "memory_candidates": [
                    {
                        "memory_type": "execution",
                        "summary": "复杂任务优先走最终结果式执行",
                        "detail": "当任务涉及多步分析和工具配合时，优先在 task 内部完成收敛，再把最终结果交回 left_brain。",
                        "confidence": 0.88,
                        "stability": 0.83,
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
                    }
                ],
                "user_updates": [],
                "soul_updates": ["复杂任务中先收敛判断，再交给 task 执行。"],
            }
        )
    )

    proposal = asyncio.run(
        service.propose(
            [
                {
                    "id": "evt_1",
                    "timestamp": "2026-03-14T17:00:00+08:00",
                    "user_input": "完成一个复杂任务",
                    "assistant_output": "任务已完成，我给你最终结果。",
                    "turn_reflection": {"summary": "这轮有稳定模式。"},
                    "task": {"state": "done", "result": "success"},
                }
            ]
        )
    )

    assert proposal.summary == "近期复杂任务更适合先在内部收敛，再输出最终结果。"
    assert len(proposal.memory_candidates) == 1
    assert proposal.memory_candidates[0]["metadata"]["subtype"] == "skill_hint"
    assert proposal.soul_updates == ["复杂任务中先收敛判断，再交给 task 执行。"]
