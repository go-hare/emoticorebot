"""Shared helpers for user-facing fallback phrasing."""

from __future__ import annotations


def build_companion_prompt(emotion: str = "平静") -> str:
    mood = str(emotion or "").strip()
    variants = {
        "兴奋": "欸，听你这么一说我都来劲了，说吧，我在听！",
        "开心": "嗯哼，你继续说呀，我在认真接着呢。",
        "平静": "嗯，我在。你接着说，我认真听着。",
        "低落": "我在呢...你慢慢说也没关系，我会好好听。",
        "难过": "我在呢...你慢慢说，我不催你。",
        "委屈": "好啦，我在听。你慢慢说，我不会敷衍你的。",
        "生气": "行，我先听你说完。别急，我们一件件来。",
        "焦虑": "先别慌，我在。你一点点说，我们慢慢理。",
    }
    return variants.get(mood, "嗯，我在。你接着说，我认真听着。")


def build_missing_info_prompt(missing: list[str]) -> str:
    items = [str(item).strip() for item in missing if str(item).strip()]
    if not items:
        return "我这边还差一点点信息，补上我就能继续接着帮你。"
    if len(items) == 1:
        return f"我先接着帮你弄，不过还差一个关键信息：{items[0]}。"
    return f"我先接着帮你弄，不过还需要你补充这几个信息：{'、'.join(items)}。"


__all__ = ["build_companion_prompt", "build_missing_info_prompt"]
