"""LLM 工具函数 - 共享的 LLM 响应处理工具."""

from __future__ import annotations

from typing import Any


def extract_message_text(msg: Any) -> str:
    """从 LangChain AIMessage 提取文本内容。

    统一处理以下三种 content 格式：
    - str：直接返回
    - list[dict]：提取 type=="text" 的项拼接
    - 其他：str() 转换
    """
    content = getattr(msg, "content", msg)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "\n".join(parts).strip()
    return str(content).strip()


__all__ = ["extract_message_text"]
