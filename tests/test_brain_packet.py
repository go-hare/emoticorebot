from __future__ import annotations

import pytest

from emoticorebot.brain.packet import normalize_decision_packet, parse_decision_packet


def test_minimal_direct_reply_packet_is_accepted() -> None:
    packet = normalize_decision_packet(
        {
            "final_message": "1 + 1 = 2",
            "actions": [{"type": "none"}],
        },
        current_context={},
    )

    assert packet == {
        "final_message": "1 + 1 = 2",
        "actions": [{"type": "none"}],
    }


def test_execute_packet_requires_goal_and_current_checks_for_new_task() -> None:
    packet = normalize_decision_packet(
        {
            "final_message": "我先处理一下。",
            "actions": [
                {
                    "type": "execute",
                    "task_id": "new",
                    "goal": "创建一个 add.py 文件",
                    "mainline": ["看需求", "创建文件", "检查内容"],
                    "current_stage": "创建文件",
                    "current_checks": ["创建 add.py 并写入 add(a, b) 返回 a + b"],
                }
            ],
        },
        current_context={},
    )

    assert packet["actions"][0]["type"] == "execute"
    assert packet["actions"][0]["task_id"] == "new"
    assert packet["actions"][0]["goal"] == "创建一个 add.py 文件"
    assert packet["actions"][0]["current_checks"] == ["创建 add.py 并写入 add(a, b) 返回 a + b"]


def test_cancel_action_uses_current_task_id() -> None:
    packet = normalize_decision_packet(
        {
            "final_message": "好，我先停下这个任务。",
            "actions": [{"type": "execute", "operation": "cancel"}],
        },
        current_context={
            "current_task_id": "task_123",
        },
    )

    assert packet["actions"] == [
        {
            "type": "execute",
            "operation": "cancel",
            "task_id": "task_123",
        }
    ]


def test_tagged_direct_reply_is_parsed() -> None:
    payload = parse_decision_packet(
        """#####user######
1 + 1 = 2

#####Action######
{"type":"none"}
"""
    )

    assert payload == {
        "final_message": "1 + 1 = 2",
        "actions": {"type": "none"},
    }


def test_user_only_tagged_reply_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="#####user###### and #####Action######"):
        parse_decision_packet(
            """#####user######
等于 2 呀……你是在故意逗我玩吗？
"""
        )


def test_tagged_execute_and_reflect_actions_are_parsed() -> None:
    payload = parse_decision_packet(
        """#####user######
我先处理一下，然后顺手做个浅反思。

#####Action######
[
  {
    "type": "execute",
    "task_id": "new",
    "goal": "修复测试",
    "current_checks": ["运行 pytest 并定位失败点"]
  },
  {
    "type": "reflect",
    "mode": "turn"
  }
]
"""
    )

    assert isinstance(payload["actions"], list)
    assert payload["actions"][0]["type"] == "execute"
    assert payload["actions"][1]["type"] == "reflect"


def test_multiple_execute_actions_are_rejected_in_single_task_mode() -> None:
    with pytest.raises(RuntimeError, match="at most one execute action"):
        normalize_decision_packet(
            {
                "final_message": "我同时推进两件事。",
                "actions": [
                    {
                        "type": "execute",
                        "task_id": "new",
                        "goal": "检查日志",
                        "current_checks": ["检查错误日志"],
                    },
                    {
                        "type": "execute",
                        "task_id": "new",
                        "goal": "整理测试",
                        "current_checks": ["整理测试用例"],
                    },
                ],
            },
            current_context={},
        )


def test_action_first_order_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="#####user###### before #####Action######"):
        parse_decision_packet(
            """#####Action######
{"type":"none"}

#####user######
1 + 1 = 2
"""
        )


def test_json_text_brain_output_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="#####user###### and #####Action######"):
        parse_decision_packet(
            '{"actions":[{"type":"none"}],"final_message":"1 + 1 = 2"}'
        )
