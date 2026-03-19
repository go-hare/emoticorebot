from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from emoticorebot.reflection.cognitive import CognitiveEvent
from emoticorebot.reflection.turn import TurnReflectionService
from emoticorebot.models.emotion_state import EmotionStateManager

def test_build_turn_event_persists_full_left_brain_emotion_snapshot() -> None:
    events = CognitiveEvent.build_turn_events(
        reflection_input={
            "message_id": "msg_test",
            "session_id": "cli:direct",
            "user_input": "真棒",
            "assistant_output": "知道就好。",
            "left_brain": {
                "intent": "respond_to_praise",
            },
            "emotion": {
                "emotion_label": "兴奋",
                "emotion_prompt": "[当前情绪: 兴奋] 非常兴奋，话比较多，喜欢感叹号",
                "pad": {"pleasure": 0.6, "arousal": 0.9, "dominance": 0.5},
                "drives": {"social": 100.0, "energy": 98.0},
            },
        },
        importance=0.7,
        turn_reflection={"summary": "接住了夸奖。"},
    )

    assert len(events) == 1
    left_brain_state = events[0].left_brain_state
    assert left_brain_state["emotion"] == "兴奋"
    assert left_brain_state["pad"] == {"pleasure": 0.6, "arousal": 0.9, "dominance": 0.5}
    assert left_brain_state["drives"] == {"social": 100.0, "energy": 98.0}
    assert left_brain_state["emotion_prompt"] == "[当前情绪: 兴奋] 非常兴奋，话比较多，喜欢感叹号"


def test_build_turn_event_projects_task_to_three_state_view() -> None:
    events = CognitiveEvent.build_turn_events(
        reflection_input={
            "message_id": "msg_task",
            "session_id": "cli:task",
            "user_input": "继续做任务",
            "assistant_output": "我继续处理。",
            "task": {
                "task_id": "task_1",
                "title": "创建 add.py",
                "state": "running",
                "result": "none",
                "summary": "继续执行中",
            },
            "execution": {
                "invoked": True,
                "status": "running",
                "summary": "继续执行中",
            },
        },
        importance=0.6,
        turn_reflection={"summary": "任务仍在执行。"},
    )

    assert len(events) == 1
    assert events[0].task == {
        "used": True,
        "state": "running",
        "result": "none",
        "summary": "继续执行中",
    }


def test_state_update_keeps_all_delta_keys_even_when_zero() -> None:
    state_update = TurnReflectionService._normalize_state_update(
        {
            "should_apply": False,
            "confidence": 0.72,
            "reason": "本轮无需额外调整。",
            "pad_delta": {},
            "drives_delta": {},
        },
        fallback={
            "should_apply": False,
            "confidence": 0.4,
            "reason": "回填当前状态上下文。",
            "pad_delta": {
                "pleasure": 0.6,
                "arousal": 0.9,
                "dominance": 0.5,
            },
            "drives_delta": {
                "social": 100.0,
                "energy": 98.0,
            },
        },
    )

    assert state_update == {
        "should_apply": False,
        "confidence": 0.72,
        "reason": "本轮无需额外调整。",
        "pad_delta": {
            "pleasure": 0.6,
            "arousal": 0.9,
            "dominance": 0.5,
        },
        "drives_delta": {
            "social": 100.0,
            "energy": 98.0,
        },
    }


def test_state_update_preserves_explicit_target_values() -> None:
    state_update = TurnReflectionService._normalize_state_update(
        {
            "should_apply": True,
            "confidence": 0.72,
            "reason": "本轮问题得到解决，当前状态应记录为更稳定的状态值。",
            "pad_delta": {
                "pleasure": 0.66,
                "arousal": 0.82,
                "dominance": 0.58,
            },
            "drives_delta": {
                "social": 96.0,
                "energy": 84.0,
            },
        },
        fallback={
            "should_apply": False,
            "confidence": 0.4,
            "reason": "回填当前状态上下文。",
            "pad_delta": {
                "pleasure": 0.6,
                "arousal": 0.9,
                "dominance": 0.5,
            },
            "drives_delta": {
                "social": 100.0,
                "energy": 98.0,
            },
        },
    )

    assert state_update == {
        "should_apply": True,
        "confidence": 0.72,
        "reason": "本轮问题得到解决，当前状态应记录为更稳定的状态值。",
        "pad_delta": {
            "pleasure": 0.66,
            "arousal": 0.82,
            "dominance": 0.58,
        },
        "drives_delta": {
            "social": 96.0,
            "energy": 84.0,
        },
    }


def test_state_update_true_translates_legacy_negative_delta_to_target_values() -> None:
    state_update = TurnReflectionService._normalize_state_update(
        {
            "should_apply": True,
            "confidence": 0.66,
            "reason": "旧输出里仍给了小幅负向调整量。",
            "pad_delta": {
                "pleasure": -0.02,
                "arousal": -0.2,
                "dominance": -0.05,
            },
            "drives_delta": {
                "social": -2.0,
                "energy": -1.0,
            },
        },
        fallback={
            "should_apply": False,
            "confidence": 0.4,
            "reason": "回填当前状态上下文。",
            "pad_delta": {
                "pleasure": 1.0,
                "arousal": 1.0,
                "dominance": 0.5,
            },
            "drives_delta": {
                "social": 100.0,
                "energy": 94.0,
            },
        },
    )

    assert state_update == {
        "should_apply": True,
        "confidence": 0.66,
        "reason": "旧输出里仍给了小幅负向调整量。",
        "pad_delta": {
            "pleasure": 0.98,
            "arousal": 0.8,
            "dominance": 0.45,
        },
        "drives_delta": {
            "social": 98.0,
            "energy": 93.0,
        },
    }


def test_fallback_state_update_uses_current_context_values() -> None:
    state_update = TurnReflectionService._fallback_state_update(
        {
            "emotion_label": "兴奋",
            "pad": {
                "pleasure": 0.6,
                "arousal": 0.9,
                "dominance": 0.5,
            },
            "drives": {
                "social": 100.0,
                "energy": 98.0,
            },
        }
    )

    assert state_update == {
        "should_apply": False,
        "confidence": 0.4,
        "reason": "本轮未判断出需要额外调整，回填当前状态上下文。",
        "pad_delta": {
            "pleasure": 0.6,
            "arousal": 0.9,
            "dominance": 0.5,
        },
        "drives_delta": {
            "social": 100.0,
            "energy": 98.0,
        },
    }


def test_turn_reflection_memory_candidates_use_formal_long_term_schema() -> None:
    with TemporaryDirectory() as tmp_dir:
        service = TurnReflectionService(EmotionStateManager(Path(tmp_dir)), llm=None)

        reflection = service._normalize_turn_reflection(
            {
                "summary": "本轮完成一次执行。",
                "problems": [],
                "resolution": "执行结束。",
                "outcome": "success",
                "next_hint": "继续推进。",
                "user_updates": [],
                "soul_updates": [],
                "state_update": {
                    "should_apply": False,
                    "confidence": 0.5,
                    "reason": "状态稳定。",
                    "pad_delta": {"pleasure": 0.2, "arousal": 0.1, "dominance": 0.0},
                    "drives_delta": {"social": 60.0, "energy": 70.0},
                },
                "memory_candidates": [
                    {
                        "memory_type": "reflection",
                        "summary": "本轮执行完成",
                        "detail": "执行链路完整收敛并返回结果。",
                        "confidence": 0.81,
                        "stability": 0.45,
                        "tags": ["execution"],
                        "metadata": {"subtype": "turn_insight", "importance": 6},
                    }
                ],
                "execution_review": {
                    "effectiveness": "high",
                    "main_failure_reason": "",
                    "next_execution_hint": "",
                },
            },
            user_input="帮我整理一下",
            output="已经整理好了。",
            emotion={
                "emotion_label": "平静",
                "pad": {"pleasure": 0.2, "arousal": 0.1, "dominance": 0.0},
                "drives": {"social": 60.0, "energy": 70.0},
            },
            execution={
                "invoked": True,
                "status": "done",
                "summary": "已完成",
                "failure_reason": "",
            },
        )

    candidate = reflection["memory_candidates"][0]
    assert candidate["memory_type"] == "reflection"
    assert candidate["detail"] == "执行链路完整收敛并返回结果。"
    assert candidate["metadata"]["subtype"] == "turn_insight"
    assert "type" not in candidate
    assert "content" not in candidate
    assert "payload" not in candidate


def test_emotion_state_manager_applies_reflection_state_as_absolute_values() -> None:
    with TemporaryDirectory() as tmp_dir:
        manager = EmotionStateManager(Path(tmp_dir))

        snapshot = manager.apply_reflection_state_update(
            pad_delta={
                "pleasure": 0.25,
                "arousal": -0.15,
                "dominance": 0.1,
            },
            drive_delta={
                "social": 64.0,
                "energy": 88.0,
            },
        )

        assert snapshot["pad"] == {
            "pleasure": 0.25,
            "arousal": -0.15,
            "dominance": 0.1,
        }
        assert snapshot["drives"] == {
            "social": 64.0,
            "energy": 88.0,
        }
