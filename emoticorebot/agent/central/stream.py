"""Central streaming and trace helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable
from uuid import uuid4

from emoticorebot.utils.llm_utils import normalize_content_blocks

try:
    from langgraph.types import Command
except Exception:
    Command = None

if TYPE_CHECKING:
    from emoticorebot.agent.central.central import CentralAgentService


async def invoke_agent(
    service: "CentralAgentService",
    agent: Any,
    prompt: str,
    *,
    channel: str,
    chat_id: str,
    session_id: str,
    thread_id: str,
    run_id: str,
    on_trace: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    resume_value: Any | None = None,
) -> Any:
    del service
    payload: Any
    if resume_value is not None and Command is not None:
        payload = Command(resume=resume_value)
    else:
        payload = {"messages": [{"role": "user", "content": prompt}]}

    config = {
        "configurable": {
            "thread_id": thread_id,
        },
        "metadata": {
            "assistant_id": "emoticorebot-central",
            "run_id": run_id,
            "channel": channel,
            "chat_id": chat_id,
            "session_id": session_id,
        },
    }
    if hasattr(agent, "astream"):
        return await stream_agent(agent, payload=payload, config=config, on_trace=on_trace)
    if hasattr(agent, "ainvoke"):
        return await agent.ainvoke(payload, config=config)
    if hasattr(agent, "invoke"):
        return agent.invoke(payload, config=config)
    raise RuntimeError("Deep Agent does not expose invoke/ainvoke/astream")


def build_thread_id(*, channel: str, chat_id: str, session_id: str, run_id: str) -> str:
    base = str(session_id or "").strip()
    if not base:
        channel_text = str(channel or "").strip()
        chat_text = str(chat_id or "").strip()
        base = f"{channel_text}:{chat_text}" if channel_text or chat_text else "default"
    return f"central:{base}:{run_id}"


def new_run_id() -> str:
    return f"run_{uuid4().hex[:12]}"


async def stream_agent(
    agent: Any,
    *,
    payload: dict[str, Any],
    config: dict[str, Any],
    on_trace: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> Any:
    last_values: Any | None = None
    async for item in agent.astream(
        payload,
        config=config,
        stream_mode=["values", "updates", "messages", "custom"],
        subgraphs=True,
    ):
        namespace, mode, data = unpack_stream_item(item)
        if mode == "values":
            last_values = data
            continue
        if on_trace is None:
            continue
        for record in build_trace_records(mode=mode, namespace=namespace, data=data):
            await on_trace(record)
    if last_values is None:
        raise RuntimeError("Deep Agent stream did not produce final state")
    return last_values


def unpack_stream_item(item: Any) -> tuple[tuple[str, ...], str, Any]:
    namespace: tuple[str, ...] = ()
    if isinstance(item, tuple) and len(item) == 3:
        raw_namespace, mode, data = item
        if isinstance(raw_namespace, (list, tuple)):
            namespace = tuple(str(part) for part in raw_namespace if str(part))
        elif raw_namespace:
            namespace = (str(raw_namespace),)
        return namespace, str(mode), data
    if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], tuple) and len(item[1]) == 2:
        raw_namespace, chunk = item
        if isinstance(raw_namespace, (list, tuple)):
            namespace = tuple(str(part) for part in raw_namespace if str(part))
        elif raw_namespace:
            namespace = (str(raw_namespace),)
        return namespace, str(chunk[0]), chunk[1]
    if isinstance(item, tuple) and len(item) == 2:
        head, tail = item
        if isinstance(head, (list, tuple)):
            namespace = tuple(str(part) for part in head if str(part))
            return namespace, "values", tail
        return namespace, str(head), tail
    raise RuntimeError(f"Unexpected Deep Agent stream item: {type(item)!r}")


def build_trace_records(*, mode: str, namespace: tuple[str, ...], data: Any) -> list[dict[str, Any]]:
    normalized = build_normalized_trace_messages(mode=mode, namespace=namespace, data=data)
    if normalized:
        return normalized

    base: dict[str, Any] = {
        "role": "assistant",
        "phase": "task_trace",
        "stream_mode": mode,
        "timestamp": datetime.now().isoformat(),
    }
    if namespace:
        base["namespace"] = list(namespace)

    if mode == "updates" and isinstance(data, dict):
        records: list[dict[str, Any]] = []
        for node_name, node_data in data.items():
            record = dict(base)
            record["node"] = str(node_name)
            record["content"] = summarize_trace_payload(node_data) or str(node_name)
            records.append(record)
        return records

    if mode == "messages":
        return build_message_trace_records(base, data)

    if mode == "custom":
        record = dict(base)
        record["content"] = compact_trace_text(json_safe_dump(data), limit=240)
        return [record]

    return []


def build_normalized_trace_messages(
    *,
    mode: str,
    namespace: tuple[str, ...],
    data: Any,
) -> list[dict[str, Any]]:
    del namespace
    if mode == "messages":
        message = extract_message_from_messages_stream(data)
        records = message_to_conversation_records(message)
        if records:
            return records

    if mode == "updates" and isinstance(data, dict):
        records: list[dict[str, Any]] = []
        for node_data in data.values():
            if not isinstance(node_data, dict):
                continue
            messages = node_data.get("messages")
            if not isinstance(messages, list) or not messages:
                continue
            records.extend(message_to_conversation_records(messages[-1]))
        if records:
            return records

    return []


def extract_message_from_messages_stream(data: Any) -> Any | None:
    if not isinstance(data, tuple) or len(data) != 2:
        return None
    message_chunk, _metadata = data
    return message_chunk


def message_to_conversation_records(message: Any) -> list[dict[str, Any]]:
    if message is None:
        return []

    records: list[dict[str, Any]] = []
    timestamp = datetime.now().isoformat()

    tool_calls = extract_message_attr(message, "tool_calls")
    normalized_calls = normalize_trace_tool_calls(tool_calls)
    if normalized_calls:
        assistant_record: dict[str, Any] = {
            "role": "assistant",
            "content": normalize_content_blocks(extract_message_attr(message, "content")),
            "tool_calls": normalized_calls,
            "timestamp": timestamp,
        }
        assistant_record["trace_signature"] = trace_signature(assistant_record)
        records.append(assistant_record)

    tool_call_id = str(extract_message_attr(message, "tool_call_id") or "").strip()
    message_type = str(extract_message_attr(message, "type") or type(message).__name__ or "").lower()
    name = str(extract_message_attr(message, "name") or "").strip()
    content = normalize_content_blocks(extract_message_attr(message, "content"))
    if tool_call_id or message_type == "tool" or message_type.endswith("toolmessage"):
        tool_record: dict[str, Any] = {
            "role": "tool",
            "content": content,
            "timestamp": timestamp,
        }
        if tool_call_id:
            tool_record["tool_call_id"] = tool_call_id
        if name:
            tool_record["name"] = name
        tool_record["trace_signature"] = trace_signature(tool_record)
        records.append(tool_record)

    return records


def normalize_trace_tool_calls(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or "").strip()
        call_id = str(item.get("id", "") or "").strip()
        args = item.get("args", {})
        if not name and not call_id:
            continue
        payload: dict[str, Any] = {}
        if call_id:
            payload["id"] = call_id
        if name:
            payload["name"] = name
        if isinstance(args, dict):
            payload["args"] = args
        else:
            payload["args"] = {"raw": str(args)} if args not in (None, "") else {}
        out.append(payload)
    return out


def trace_signature(payload: dict[str, Any]) -> str:
    normalized = json.dumps(
        {
            "role": payload.get("role"),
            "content": payload.get("content"),
            "tool_calls": payload.get("tool_calls"),
            "tool_call_id": payload.get("tool_call_id"),
            "name": payload.get("name"),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def build_message_trace_records(base: dict[str, Any], data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, tuple) or len(data) != 2:
        return []
    message_chunk, metadata = data
    record_base = dict(base)
    if isinstance(metadata, dict):
        node_name = str(metadata.get("langgraph_node", "") or "").strip()
        if node_name:
            record_base["node"] = node_name

    tool_call_chunks = extract_message_attr(message_chunk, "tool_call_chunks")
    if not isinstance(tool_call_chunks, list):
        return []

    records: list[dict[str, Any]] = []
    for chunk in tool_call_chunks:
        if not isinstance(chunk, dict):
            continue
        tool_name = str(chunk.get("name", "") or "").strip()
        args_chunk = compact_trace_text(str(chunk.get("args", "") or "").strip(), limit=200)
        if not tool_name and not args_chunk:
            continue
        record = dict(record_base)
        record["event"] = "task.tool.call"
        if tool_name:
            record["tool_name"] = tool_name
        record["content"] = args_chunk or tool_name
        records.append(record)
    return records


def summarize_trace_payload(payload: Any) -> str:
    if isinstance(payload, dict):
        messages = payload.get("messages")
        if isinstance(messages, list) and messages:
            return summarize_trace_message(messages[-1])
        return compact_trace_text(json_safe_dump(payload), limit=240)
    if isinstance(payload, list) and payload:
        return compact_trace_text(json_safe_dump(payload[-1]), limit=240)
    return compact_trace_text(str(payload or ""), limit=240)


def summarize_trace_message(message: Any) -> str:
    tool_calls = extract_message_attr(message, "tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        names = [
            str(call.get("name", "") or "").strip()
            for call in tool_calls
            if isinstance(call, dict) and str(call.get("name", "") or "").strip()
        ]
        if names:
            return "tool_calls: " + ", ".join(names)

    name = str(extract_message_attr(message, "name") or "").strip()
    content = extract_message_attr(message, "content")
    content_text = compact_trace_text(normalize_message_content(content), limit=240)
    if name and content_text:
        return f"{name}: {content_text}"
    return content_text


def extract_message_attr(message: Any, key: str) -> Any:
    if isinstance(message, dict):
        return message.get(key)
    return getattr(message, key, None)


def normalize_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = str(item.get("text", "") or item.get("content", "") or "").strip()
                if text:
                    parts.append(text)
            elif item:
                parts.append(str(item))
        return " ".join(parts)
    if content is None:
        return ""
    return str(content)


def json_safe_dump(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def compact_trace_text(text: str, *, limit: int = 240) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


__all__ = [
    "build_thread_id",
    "invoke_agent",
    "new_run_id",
    "stream_agent",
]
