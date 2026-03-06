"""EQ Service - 情感执行服务

EQ 核心方法：respond（拟人化响应）
"""

from __future__ import annotations

import re
from typing import Any

from emoticorebot.core.context import ContextBuilder
from emoticorebot.utils.llm_utils import extract_message_text


class EQService:
    """EQ 情感执行服务

    核心方法：respond - 拟人化响应
    """

    def __init__(self, eq_llm, context_builder: ContextBuilder):
        self.eq_llm = eq_llm
        self.context = context_builder

    async def generate_proactive(self, prompt: str) -> str:
        """生成主动对话消息（供 SubconsciousDaemon 使用）"""
        system = self.context.build_eq_system_prompt()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        resp = await self.eq_llm.ainvoke(messages)
        return extract_message_text(resp).strip()

    async def respond(
        self,
        user_input: str,
        iq_result: str,
        iq_error: str,
        history: list[dict[str, Any]],
        emotion: str,
        pad: dict[str, float],
        channel: str,
        chat_id: str,
    ) -> dict:
        system = self.context.build_eq_system_prompt(
            query=user_input,
            current_emotion=emotion,
            pad_state=(pad.get("pleasure", 0.0), pad.get("arousal", 0.5), pad.get("dominance", 0.5)),
        )
        polish_prompt = (
            "请将以下事实数据，用你的性格转述给用户。\n"
            "**禁止直接输出JSON或技术报错。禁止篡改数据内容。**\n\n"
            f"用户的原始问题：{user_input}\n\n"
            f"事实数据（IQ返回）：\n{iq_result}\n\n"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": polish_prompt},
        ]
        resp = await self.eq_llm.ainvoke(messages)
        text = self._msg_text(resp)

        # 解析行动指令
        action = self._parse_action(text)
        clean_response = self._remove_action(text)

        return {
            "response": clean_response.strip(),
            "action": action,
        }

    @staticmethod
    def _msg_text(msg: Any) -> str:
        """从 LangChain AIMessage 提取文本内容"""
        return extract_message_text(msg)

    @staticmethod
    def _strip_think(text: str) -> str:
        """移除 <think>...</think> 标签内容"""
        return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    @staticmethod
    def _format_history(history: list[dict[str, Any]]) -> str:
        """格式化历史对话"""
        if not history:
            return "无"
        lines = []
        for msg in history:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:100]
            lines.append(f"- {role}: {content}")
        return "\n".join(lines)

    @staticmethod
    def _parse_action(text: str) -> dict | None:
        """从文本中解析行动指令"""
        match = re.search(r"\[行动:\s*(\w+)\s*-\s*(.+?)\]", text)
        if not match:
            return None
        action_type = match.group(1).strip()
        action_content = match.group(2).strip()

        if action_type == "委托":
            return {"type": "delegate", "task": action_content}
        elif action_type == "尝试":
            return {"type": "try", "task": action_content}
        elif action_type == "追问":
            return {"type": "ask", "question": action_content}
        return None

    @staticmethod
    def _remove_action(text: str) -> str:
        """移除行动指令标记"""
        return re.sub(r"\[行动:.*?\]", "", text).strip()


__all__ = ["EQService"]