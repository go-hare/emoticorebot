"""LLM 工具函数 - 共享的 LLM 响应处理工具."""

from __future__ import annotations

import json
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


def normalize_content_blocks(content: Any) -> list[dict[str, Any]]:
    """Normalize message content into the persisted block-array shape."""
    if content is None:
        return []
    if isinstance(content, str):
        text = content.strip()
        return [{"type": "text", "text": text}] if text else []
    if isinstance(content, list):
        blocks: list[dict[str, Any]] = []
        for item in content:
            if isinstance(item, dict):
                block = dict(item)
                if block.get("type") == "image_url":
                    image_url = block.get("image_url")
                    if isinstance(image_url, dict) and image_url.get("url"):
                        block = {"type": "image", "url": str(image_url.get("url"))}
                if block.get("type") == "text":
                    text = str(block.get("text", "") or "").strip()
                    if not text:
                        continue
                    block = {"type": "text", "text": text}
                elif block.get("type") in {"image", "file", "audio"}:
                    block = {key: value for key, value in block.items() if value not in (None, "")}
                else:
                    block = {key: value for key, value in block.items() if value is not None}
                if block:
                    blocks.append(block)
                continue
            text = str(item).strip()
            if text:
                blocks.append({"type": "text", "text": text})
        return blocks
    text = str(content).strip()
    return [{"type": "text", "text": text}] if text else []


def extract_message_metrics(msg: Any) -> dict[str, Any]:
    """Extract model and token usage metadata from a LangChain/OpenAI-style message."""
    out: dict[str, Any] = {}

    response_metadata = getattr(msg, "response_metadata", None)
    if isinstance(response_metadata, dict):
        model_name = response_metadata.get("model_name") or response_metadata.get("model")
        if model_name:
            out["model_name"] = str(model_name)

    usage_metadata = getattr(msg, "usage_metadata", None)
    if not isinstance(usage_metadata, dict) and isinstance(response_metadata, dict):
        candidate = response_metadata.get("token_usage") or response_metadata.get("usage")
        if isinstance(candidate, dict):
            usage_metadata = candidate

    if isinstance(usage_metadata, dict):
        prompt_tokens = usage_metadata.get("input_tokens")
        completion_tokens = usage_metadata.get("output_tokens")
        total_tokens = usage_metadata.get("total_tokens")

        if prompt_tokens is not None:
            out["prompt_tokens"] = int(prompt_tokens)
        if completion_tokens is not None:
            out["completion_tokens"] = int(completion_tokens)
        if total_tokens is not None:
            out["total_tokens"] = int(total_tokens)

    return out


def json_text_block(payload: Any) -> list[dict[str, Any]]:
    """Serialize an arbitrary payload into a single text block."""
    if isinstance(payload, str):
        text = payload.strip()
    else:
        text = json.dumps(payload, ensure_ascii=False)
    return [{"type": "text", "text": text}] if text else []


def blocks_to_llm_content(blocks: Any) -> str | list[dict[str, Any]]:
    """Convert persisted block-array to LLM API content format.

    - If blocks contain only text, returns a plain string for efficiency.
    - If blocks contain media (image/file/audio), returns OpenAI-style content array.
    - Converts internal {"type": "image", "url": ...} to {"type": "image_url", "image_url": {"url": ...}}.
    """
    if isinstance(blocks, str):
        return blocks.strip()
    if not isinstance(blocks, list) or not blocks:
        return str(blocks) if blocks else ""

    has_media = False
    converted: list[dict[str, Any]] = []

    for block in blocks:
        if not isinstance(block, dict):
            text = str(block).strip()
            if text:
                converted.append({"type": "text", "text": text})
            continue

        block_type = block.get("type", "")

        if block_type == "text":
            text = str(block.get("text", "") or "").strip()
            if text:
                converted.append({"type": "text", "text": text})

        elif block_type == "image":
            url = block.get("url", "")
            if url:
                has_media = True
                converted.append({"type": "image_url", "image_url": {"url": url}})

        elif block_type == "image_url":
            image_url = block.get("image_url")
            if isinstance(image_url, dict) and image_url.get("url"):
                has_media = True
                converted.append(block)
            elif isinstance(image_url, str) and image_url:
                has_media = True
                converted.append({"type": "image_url", "image_url": {"url": image_url}})

        elif block_type in {"file", "audio"}:
            url = block.get("url", "")
            if url:
                has_media = True
                converted.append(block)

        else:
            text = str(block.get("text", "") or block.get("content", "") or "").strip()
            if text:
                converted.append({"type": "text", "text": text})

    if not converted:
        return ""

    if not has_media:
        return "\n".join(b.get("text", "") for b in converted if b.get("type") == "text").strip()

    return converted


__all__ = [
    "extract_message_text",
    "normalize_content_blocks",
    "extract_message_metrics",
    "json_text_block",
    "blocks_to_llm_content",
]
