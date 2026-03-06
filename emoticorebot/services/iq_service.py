"""IQ Service - 任务执行服务

将 Runtime 中的 run_iq_task 巨型方法提取为独立服务类。
"""

from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from loguru import logger

from emoticorebot.core.context import ContextBuilder
from emoticorebot.tools import ToolRegistry
from emoticorebot.utils.llm_utils import extract_message_text


class IQService:
    """IQ 任务执行服务
    
    职责：工具调用 + 任务执行
    - 执行 IQ 任务（工具调用循环）
    - 参数注入（intent_params）
    - 深度提示（fact_depth）
    - 判断是否需要更多信息
    """
    
    def __init__(
        self,
        iq_llm,
        tool_registry: ToolRegistry,
        context_builder: ContextBuilder,
        max_iterations: int = 30,
    ):
        self.iq_llm = iq_llm
        self.tools = tool_registry
        self.context = context_builder
        self.max_iterations = max_iterations
    
    async def run_task(
        self,
        task: str,
        history: list[dict[str, Any]],
        emotion: str,
        pad: dict[str, float],
        channel: str,
        chat_id: str,
        intent_params: dict[str, Any] | None = None,
        tool_budget: int | None = None,
        fact_depth: int | None = None,
        media: list[str] | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """执行 IQ 任务
        
        Returns:
            {
                "requires_more_info": bool,
                "content": str,
                "tool_calls": list[dict],
                "iterations": int,
                "missing": list[str],  # 仅当 requires_more_info=True 时
            }
        """
        current_message = self._build_task_message(task, intent_params, fact_depth)
        
        messages = self.context.build_messages(
            history=history[-12:],
            current_message=current_message,
            mode="iq",
            media=media,
            channel=channel,
            chat_id=chat_id,
        )
        
        lc_messages = self._to_langchain_messages(messages)
        llm = self.iq_llm.bind_tools(self.tools.get_definitions())
        tool_calls: list[dict[str, str]] = []
        
        # 使用 tool_budget（若提供）或默认 max_iterations
        max_calls = tool_budget if tool_budget is not None else self.max_iterations
        
        for iteration in range(max(1, max_calls)):
            resp = await llm.ainvoke(lc_messages)
            
            # 如果没有工具调用，检查是否需要更多信息
            if not getattr(resp, "tool_calls", None):
                content = self._msg_text(resp)
                if self._needs_more_info(content):
                    return {
                        "requires_more_info": True,
                        "missing": self._extract_missing_params(content),
                        "content": content,
                        "tool_calls": tool_calls,
                        "iterations": iteration + 1,
                    }
                return {
                    "requires_more_info": False,
                    "content": content,
                    "tool_calls": tool_calls,
                    "iterations": iteration + 1,
                }
            
            # 执行工具调用
            lc_messages.append(
                AIMessage(
                    content=self._msg_text(resp),
                    tool_calls=[
                        {"id": tc["id"], "name": tc["name"], "args": tc.get("args", {})}
                        for tc in resp.tool_calls
                    ],
                )
            )
            
            for tc in resp.tool_calls:
                name = tc["name"]
                args = tc.get("args", {})
                logger.debug("IQ tool call: {} with args {}", name, args)
                
                result = await self.tools.execute(name, args)
                tool_calls.append({"tool": name, "result": result[:500]})
                lc_messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))
                
                # 通知进度（可选）
                if on_progress:
                    await on_progress(f"执行工具: {name}")
        
        # 达到最大迭代次数
        return {
            "requires_more_info": False,
            "content": "达到最大迭代次数",
            "tool_calls": tool_calls,
            "iterations": max_calls,
        }
    
    def _build_task_message(
        self,
        task: str,
        intent_params: dict[str, Any] | None,
        fact_depth: int | None,
    ) -> str:
        """构建任务消息（注入参数和深度提示）"""
        current_message = task
        
        # 注入路由器提取的参数
        if intent_params:
            current_message = (
                f"{task}\n\n"
                "[Router Extracted Params]\n"
                f"{json.dumps(intent_params, ensure_ascii=False)}\n\n"
                "请优先使用以上参数执行任务；若参数不足再根据用户原文补全。"
            )
        
        # 注入事实深度提示
        depth_hint = self._get_depth_hint(fact_depth)
        if depth_hint:
            current_message = (
                f"{current_message}\n\n"
                "[Fact Depth]\n"
                f"{depth_hint}"
            )
        
        return current_message
    
    @staticmethod
    def _get_depth_hint(fact_depth: int | None) -> str:
        """根据 fact_depth 生成深度提示"""
        if fact_depth is None:
            return ""
        
        if fact_depth <= 1:
            return "输出要点结论即可，避免展开过多背景。"
        elif fact_depth >= 3:
            return "输出尽量完整，含关键依据、步骤和边界说明。"
        else:
            return "输出结论并给出必要依据，保持简洁。"
    
    @staticmethod
    def _msg_text(msg: Any) -> str:
        """从 LangChain AIMessage 提取文本内容（委托 llm_utils）"""
        return extract_message_text(msg)

    @staticmethod
    def _to_langchain_messages(messages: list[dict[str, Any]]) -> list[Any]:
        """转换为 LangChain 消息格式"""
        out: list[Any] = []
        for m in messages:
            role = m.get("role")
            content = m.get("content", "")
            if role == "system":
                out.append(SystemMessage(content=content))
            elif role == "assistant":
                out.append(AIMessage(content=content))
            elif role == "tool":
                out.append(ToolMessage(content=content, tool_call_id=m.get("tool_call_id", "tool")))
            else:
                out.append(HumanMessage(content=content))
        return out
    
    @staticmethod
    def _needs_more_info(content: str) -> bool:
        """判断是否需要更多信息"""
        kws = ["请提供", "需要知道", "缺少", "请告诉我", "哪个城市", "please provide", "need to know"]
        low = (content or "").lower()
        return any(k in low for k in kws)
    
    @staticmethod
    def _extract_missing_params(content: str) -> list[str]:
        """提取缺失的参数"""
        missing = []
        for pattern in [r"哪个城市", r"需要(.+?)参数", r"请提供(.+?)信息"]:
            missing.extend(re.findall(pattern, content))
        return missing or ["unknown"]


__all__ = ["IQService"]
