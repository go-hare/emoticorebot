from __future__ import annotations

from emoticorebot.memory.short_term import ShortTermMemoryStore


def test_short_term_store_persists_multimodal_raw_messages(tmp_path) -> None:
    store = ShortTermMemoryStore(tmp_path)

    store.append_entries(
        "cli:direct",
        [
            {
                "turn_id": "turn_1",
                "memory_type": "turn_summary",
                "summary": "用户发来图片并得到回复",
                "detail": "本轮包含文本和图片，上下文已保留到短期记忆。",
                "raw_messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "看看这个"},
                            {"type": "image", "url": "/tmp/example.png"},
                        ],
                        "message_id": "msg_user",
                        "created_at": "2026-03-19T01:00:00+08:00",
                    },
                    {
                        "role": "assistant",
                        "content": "我看到了这张图。",
                        "message_id": "msg_assistant",
                        "created_at": "2026-03-19T01:00:02+08:00",
                    },
                ],
            }
        ],
    )

    rows = store.load_entries("cli:direct")

    assert len(rows) == 1
    assert rows[0]["memory_type"] == "turn_summary"
    assert rows[0]["raw_messages"][0]["role"] == "user"
    assert rows[0]["raw_messages"][0]["content"] == "看看这个"
    assert rows[0]["raw_messages"][0]["content_blocks"][1]["type"] == "image"
    assert rows[0]["raw_messages"][1]["content"] == "我看到了这张图。"
    assert store.path_for("cli:direct").name == "cli_direct.jsonl"
