from __future__ import annotations

import pytest

from emoticorebot.left_brain.packet import normalize_decision_packet, parse_decision_packet


def test_minimal_direct_reply_packet_is_accepted() -> None:
    packet = normalize_decision_packet(
        {
            "task_action": "none",
            "task_mode": "skip",
            "final_message": "1 + 1 = 2",
        },
        current_context={},
    )

    assert packet == {
        "task_action": "none",
        "task_mode": "skip",
        "task_reason": "",
        "final_message": "1 + 1 = 2",
    }


def test_minimal_create_task_packet_uses_runtime_owned_task_spec() -> None:
    packet = normalize_decision_packet(
        {
            "task_action": "create_task",
            "task_mode": "async",
            "final_message": "我先处理一下。",
        },
        current_context={
            "user_input": "创建一个 add.py 文件 add(a, b) 返回 a + b",
            "history_context": "用户一直在测试文件创建链路。",
        },
    )

    assert packet["task_action"] == "create_task"
    assert packet["task_mode"] == "async"
    assert packet["final_message"] == "我先处理一下。"
    assert "task" not in packet


def test_cancel_task_packet_uses_current_active_task() -> None:
    packet = normalize_decision_packet(
        {
            "task_action": "cancel_task",
            "task_mode": "sync",
            "final_message": "好，我先停下这个任务。",
        },
        current_context={
            "active_task_id": "task_123",
        },
    )

    assert packet["task_action"] == "cancel_task"
    assert packet["task_mode"] == "sync"
    assert packet["task"] == {"task_id": "task_123"}


def test_tagged_direct_reply_is_parsed() -> None:
    payload = parse_decision_packet(
        """####user####
1 + 1 = 2

####task####
action=none
task_mode=skip
"""
    )

    assert payload == {
        "task_action": "none",
        "task_mode": "skip",
        "final_message": "1 + 1 = 2",
    }


def test_user_only_tagged_reply_defaults_to_direct_answer() -> None:
    payload = parse_decision_packet(
        """####user####
等于 2 呀……你是在故意逗我玩吗？
"""
    )

    assert payload == {
        "task_action": "none",
        "task_mode": "skip",
        "final_message": "等于 2 呀……你是在故意逗我玩吗？",
    }


def test_tagged_cancel_task_is_parsed() -> None:
    payload = parse_decision_packet(
        """####user####
好，先停下这个任务。

####task####
action=cancel_task
task_mode=sync
task_id=task_123
"""
    )

    assert payload == {
        "task_action": "cancel_task",
        "task_mode": "sync",
        "final_message": "好，先停下这个任务。",
        "task": {"task_id": "task_123"},
    }


def test_old_task_first_order_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="####user#### before ####task####"):
        parse_decision_packet(
            """####task####
action=none
task_mode=skip

####user####
1 + 1 = 2
"""
        )


def test_json_text_brain_output_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="####user#### and ####task####"):
        parse_decision_packet(
            '{"task_action":"none","final_message":"1 + 1 = 2"}'
        )
