from __future__ import annotations

from emoticorebot.brain.event_narrator import EventNarrator


def test_event_narrator_builds_done_packet_without_llm() -> None:
    packet = EventNarrator._build_direct_event_packet(
        {
            "type": "done",
            "task_id": "task_1",
            "title": "创建 add.py",
            "summary": "文件已经写入工作区。",
            "message_id": "msg_1",
        }
    )

    assert packet is not None
    assert packet["final_decision"] == "answer"
    assert "创建 add.py" in packet["final_message"]
    assert "文件已经写入工作区" in packet["final_message"]


def test_event_narrator_builds_need_input_packet_without_llm() -> None:
    packet = EventNarrator._build_direct_event_packet(
        {
            "type": "need_input",
            "task_id": "task_2",
            "title": "查询天气",
            "summary": "已经识别到你想查天气。",
            "question": "你想查哪个城市？",
            "message_id": "msg_2",
        }
    )

    assert packet is not None
    assert packet["final_decision"] == "ask_user"
    assert "查询天气" in packet["final_message"]
    assert "你想查哪个城市" in packet["final_message"]
