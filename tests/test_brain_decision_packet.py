from __future__ import annotations

import pytest

from emoticorebot.brain.decision_packet import normalize_brain_packet, parse_raw_brain_json


def test_minimal_direct_reply_packet_is_accepted() -> None:
    packet = normalize_brain_packet(
        {
            "task_action": "none",
            "final_decision": "answer",
            "final_message": "1 + 1 = 2",
        },
        current_context={},
    )

    assert packet["task_action"] == "none"
    assert packet["final_decision"] == "answer"
    assert packet["final_message"] == "1 + 1 = 2"
    assert "intent" not in packet
    assert "execution_summary" not in packet


def test_minimal_create_task_packet_uses_user_input_as_request() -> None:
    packet = normalize_brain_packet(
        {
            "task_action": "create_task",
            "final_decision": "continue",
            "final_message": "好的，我来处理。",
        },
        current_context={
            "user_input": "创建一个 add.py 文件 add(a, b) 返回 a + b",
            "history_context": "用户一直在测试文件创建链路。",
            "review_policy": "skip",
            "preferred_agent": "worker",
        },
    )

    assert packet["task_action"] == "create_task"
    assert packet["task"]["request"] == "创建一个 add.py 文件 add(a, b) 返回 a + b"
    assert packet["task"]["history_context"] == "用户一直在测试文件创建链路。"
    assert packet["task"]["review_policy"] == "skip"
    assert packet["task"]["preferred_agent"] == "worker"


def test_tagged_direct_reply_is_parsed() -> None:
    payload = parse_raw_brain_json(
        """####user####
1 + 1 = 2

####task####
mode=answer
action=none
"""
    )

    assert payload == {
        "task_action": "none",
        "final_decision": "answer",
        "final_message": "1 + 1 = 2",
    }


def test_tagged_resume_task_is_parsed() -> None:
    payload = parse_raw_brain_json(
        """####user####
好，我继续处理。

####task####
mode=continue
action=resume_task
task_id=task_123
"""
    )

    assert payload == {
        "task_action": "resume_task",
        "final_decision": "continue",
        "final_message": "好，我继续处理。",
        "task": {"task_id": "task_123"},
    }


def test_json_text_brain_output_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="####user#### and ####task####"):
        parse_raw_brain_json(
            '{"task_action":"none","final_decision":"answer","final_message":"1 + 1 = 2"}'
        )
