from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from emoticorebot.agent.cognitive import CognitiveEvent
from emoticorebot.agent.reflection.turn import TurnReflectionService
from emoticorebot.models.emotion_state import EmotionStateManager

def test_build_turn_event_persists_full_brain_emotion_snapshot() -> None:
    events = CognitiveEvent.build_turn_events(
        reflection_input={
            "message_id": "msg_test",
            "session_id": "cli:direct",
            "user_input": "真棒",
            "assistant_output": "知道就好。",
            "brain": {
                "intent": "respond_to_praise",
                "final_decision": "answer",
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
    brain_state = events[0].brain_state
    assert brain_state["emotion"] == "兴奋"
    assert brain_state["pad"] == {"pleasure": 0.6, "arousal": 0.9, "dominance": 0.5}
    assert brain_state["drives"] == {"social": 100.0, "energy": 98.0}
    assert brain_state["emotion_prompt"] == "[当前情绪: 兴奋] 非常兴奋，话比较多，喜欢感叹号"


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
                "state": "waiting",
                "result": "none",
                "summary": "等待用户补充路径",
            },
            "execution": {
                "invoked": True,
                "status": "waiting_input",
                "summary": "需要补充路径",
                "missing": ["path"],
            },
        },
        importance=0.6,
        turn_reflection={"summary": "任务仍在等待输入。"},
    )

    assert len(events) == 1
    assert events[0].task == {
        "used": True,
        "state": "waiting",
        "result": "none",
        "summary": "等待用户补充路径",
        "missing": ["path"],
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
