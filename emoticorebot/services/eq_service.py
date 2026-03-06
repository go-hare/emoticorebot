"""EQ Service - 情感执行服务

将 Runtime 中的 5 个 EQ 方法提取为独立服务类。
"""

from __future__ import annotations

import re
from typing import Any

from emoticorebot.core.context import ContextBuilder
from emoticorebot.utils.llm_utils import extract_message_text


class EQService:
    """EQ 情感执行服务
    
    职责：所有 EQ 相关的 LLM 调用
    - 判断是否需要委托给 IQ
    - 直接回复（无需 IQ）
    - 生成共情回应
    - 润色 IQ 结果
    - 生成追问
    """
    
    def __init__(self, eq_llm, context_builder: ContextBuilder):
        self.eq_llm = eq_llm
        self.context = context_builder
    
    async def should_delegate(
        self,
        user_input: str,
        history: list[dict[str, Any]],
        emotion: str,
        pad: dict[str, float],
        channel: str,
        chat_id: str,
    ) -> bool:
        """判断是否需要委托给 IQ 执行
        
        使用启发式规则 + LLM 判断
        """
        # 启发式规则优先（快速路径）
        if re.search(r"(查|搜索|读取|写入|编辑|执行|命令|文件|网址|网页|天气|日程|cron|代码)", user_input, re.I):
            return True
        
        # LLM 判断
        messages = self.context.build_messages(
            history=history[-8:],
            current_message=f"只回答 yes 或 no：这条消息是否需要调用工具或进行事实检索？\n{user_input}",
            mode="eq",
            current_emotion=emotion,
            pad_state=(pad.get("pleasure", 0.0), pad.get("arousal", 0.5), pad.get("dominance", 0.5)),
            channel=channel,
            chat_id=chat_id,
        )
        resp = await self.eq_llm.ainvoke(messages)
        text = self._msg_text(resp).lower()
        return "yes" in text or "需要" in text
    
    async def direct_reply(
        self,
        user_input: str,
        history: list[dict[str, Any]],
        emotion: str,
        pad: dict[str, float],
        channel: str,
        chat_id: str,
    ) -> str:
        """直接回复（无需 IQ 介入）"""
        messages = self.context.build_messages(
            history=history[-12:],
            current_message=user_input,
            mode="eq",
            current_emotion=emotion,
            pad_state=(pad.get("pleasure", 0.0), pad.get("arousal", 0.5), pad.get("dominance", 0.5)),
            channel=channel,
            chat_id=chat_id,
        )
        resp = await self.eq_llm.ainvoke(messages)
        return self._msg_text(resp)
    
    async def empathy(
        self,
        user_input: str,
        emotion: str,
        pad: dict[str, float],
    ) -> str:
        """生成简短共情回应（1-2句），不提及任务或数据"""
        system = self.context.build_eq_system_prompt(
            query=user_input,
            current_emotion=emotion,
            pad_state=(pad.get("pleasure", 0.0), pad.get("arousal", 0.5), pad.get("dominance", 0.5)),
        )
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": f"请先对用户情绪做出共情回应（简短1-2句，不要提及任务，不要提数据）：\n{user_input}",
            },
        ]
        resp = await self.eq_llm.ainvoke(messages)
        return self._strip_think(self._msg_text(resp))
    
    async def polish(
        self,
        user_input: str,
        iq_result: str,
        history: list[dict[str, Any]],
        emotion: str,
        pad: dict[str, float],
        channel: str,
        chat_id: str,
        style: str = "professional",
    ) -> str:
        """将 IQ 事实数据润色成自然、有温度的回复
        
        Args:
            style: "professional" | "caring" | "concise"
        """
        style_guide = {
            "professional": "专业简洁，保持你的性格特征，不要太情绪化",
            "caring": "充满关怀，语气温柔体贴，符合你傲娇心软的性格",
            "concise": "优先给结论，句子更短，减少寒暄，但保留基本礼貌与温度",
        }.get(style, "保持你的性格特征")
        
        polish_prompt = (
            "请将以下事实数据，用你的性格转述给用户。\n"
            "**禁止直接输出JSON或技术报错。禁止篡改数据内容。**\n\n"
            f"用户的原始问题：{user_input}\n\n"
            f"事实数据（IQ返回）：\n{iq_result}\n\n"
            f"转述风格：{style_guide}"
        )
        
        system = self.context.build_eq_system_prompt(
            query=user_input,
            current_emotion=emotion,
            pad_state=(pad.get("pleasure", 0.0), pad.get("arousal", 0.5), pad.get("dominance", 0.5)),
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": polish_prompt},
        ]
        resp = await self.eq_llm.ainvoke(messages)
        polished = self._strip_think(self._msg_text(resp))
        if polished:
            return polished
        return f"我帮你整理好了关键信息：{iq_result}".strip()
    
    async def followup(
        self,
        missing: list[str],
        emotion: str,
    ) -> str:
        """生成追问（当 IQ 需要更多信息时）"""
        prompt = f"请基于缺失信息生成一句自然追问：{missing}。当前情绪：{emotion}。"
        messages = self.context.build_messages(
            history=[],
            current_message=prompt,
            mode="eq",
            current_emotion=emotion,
        )
        resp = await self.eq_llm.ainvoke(messages)
        text = extract_message_text(resp)
        return text or "还差一点信息，能再补充下吗？"

    async def generate_proactive(self, prompt: str) -> str:
        """生成主动对话消息（供 SubconsciousDaemon 使用）

        使用完整的 EQ System Prompt，以保持性格一致性。
        """
        system = self.context.build_eq_system_prompt()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        resp = await self.eq_llm.ainvoke(messages)
        return extract_message_text(resp).strip()

    @staticmethod
    def _msg_text(msg: Any) -> str:
        """从 LangChain AIMessage 提取文本内容（保留向后兼容，内部委托 llm_utils）"""
        return extract_message_text(msg)

    @staticmethod
    def _strip_think(text: str) -> str:
        """移除 <think>...</think> 标签内容"""
        return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


__all__ = ["EQService"]
