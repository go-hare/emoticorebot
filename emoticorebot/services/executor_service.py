"""Executor service backed by Deep Agents."""

from __future__ import annotations

import json
import hashlib
from uuid import uuid4
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from emoticorebot.checkpointing import PersistentMemorySaver
from emoticorebot.core.context import ContextBuilder
from emoticorebot.core.skills import BUILTIN_SKILLS_DIR
from emoticorebot.core.state import ExecutorPacketStatus, ExecutorRecommendedAction, ExecutorResultPacket
from emoticorebot.tools import ToolRegistry
from emoticorebot.utils.helpers import ensure_dir
from emoticorebot.utils.llm_utils import normalize_content_blocks
from emoticorebot.utils.llm_utils import extract_message_metrics

try:
    from deepagents import create_deep_agent
except Exception:
    create_deep_agent = None

try:
    from langgraph.checkpoint.memory import InMemorySaver
except Exception:
    InMemorySaver = None

try:
    from langgraph.types import Command
except Exception:
    Command = None


class ExecutorService:
    """Executor layer for complex tasks.

    The outer main_brain ↔ executor contract stays minimal.
    The inner execution model is Deep Agents-based:
    - planning
    - skills
    - subagents
    - long-running complex tasks
    """

    _VALID_RAW_STATUS: set[ExecutorPacketStatus] = {"completed", "needs_input", "uncertain", "failed"}
    _VALID_ACTIONS: set[ExecutorRecommendedAction] = {"answer", "ask_user", "continue"}

    def __init__(
        self,
        executor_llm,
        tool_registry: ToolRegistry | None,
        context_builder: ContextBuilder,
    ):
        self.executor_llm = executor_llm
        self.tools = tool_registry
        self.context = context_builder
        self._agent: Any | None = None
        self._checkpointer: Any | None = None

    async def run_request(
        self,
        request: str,
        history: list[dict[str, Any]],
        emotion: str,
        pad: dict[str, float],
        channel: str,
        chat_id: str,
        session_id: str = "",
        execution_context: dict[str, Any] | None = None,
        media: list[str] | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_trace: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> ExecutorResultPacket:
        del emotion, pad

        if create_deep_agent is None:
            return self._failed_packet(
                analysis="Deep Agents 依赖尚未安装，executor 新执行内核当前不可用。",
                missing=self._extract_missing(execution_context),
            )

        request = str(request or "").strip()
        resume_value = self._build_resume_value(execution_context)
        can_resume = resume_value is not None and str((execution_context or {}).get("thread_id", "") or "").strip() != ""
        if not request and not can_resume:
            return self._failed_packet("executor 未收到有效问题。")

        if on_progress is not None:
            await on_progress("executor 正在恢复上次执行" if can_resume else "executor 正在规划内部问题")

        agent = self._ensure_agent()
        run_id = str((execution_context or {}).get("run_id", "") or "").strip() if can_resume else ""
        if not run_id:
            run_id = self._new_run_id()
        thread_id = str((execution_context or {}).get("thread_id", "") or "").strip() if can_resume else ""
        if not thread_id:
            thread_id = self._build_thread_id(
                channel=channel,
                chat_id=chat_id,
                session_id=session_id,
                run_id=run_id,
            )
        prompt = self._build_request_prompt(
            request=request,
            history=history,
            execution_context=execution_context,
            media=media,
        )
        try:
            raw_result = await self._invoke_agent(
                agent,
                prompt,
                channel=channel,
                chat_id=chat_id,
                session_id=session_id,
                thread_id=thread_id,
                run_id=run_id,
                on_trace=on_trace,
                resume_value=resume_value if can_resume else None,
            )
        except Exception as exc:
            if can_resume:
                try:
                    raw_result = await self._invoke_agent(
                        agent,
                        prompt,
                        channel=channel,
                        chat_id=chat_id,
                        session_id=session_id,
                        thread_id=thread_id,
                        run_id=run_id,
                        on_trace=on_trace,
                        resume_value=None,
                    )
                except Exception as resume_exc:
                    packet = self._failed_packet(
                        analysis=f"Deep Agents 恢复失败：{resume_exc}",
                        missing=self._extract_missing(execution_context),
                    )
                    packet["thread_id"] = thread_id
                    packet["run_id"] = run_id
                    return packet
            else:
                packet = self._failed_packet(
                    analysis=f"Deep Agents 执行失败：{exc}",
                    missing=self._extract_missing(execution_context),
                )
                packet["thread_id"] = thread_id
                packet["run_id"] = run_id
                return packet
        packet = self._normalize_result_packet(
            raw_result,
            request=request,
            execution_context=execution_context,
        )
        packet["thread_id"] = thread_id
        packet["run_id"] = run_id
        return packet

    def _ensure_agent(self) -> Any:
        if self._agent is None:
            self._agent = self._build_agent()
        return self._agent

    def _build_agent(self) -> Any:
        if create_deep_agent is None:
            raise RuntimeError("deepagents is not available")

        tools = self._build_tools()
        subagents = self._build_subagents()
        skills = self._build_skill_paths()
        backend = self._build_backend()
        checkpointer = self._ensure_checkpointer()
        interrupt_on = self._build_interrupt_on()

        try:
            kwargs: dict[str, Any] = {
                "model": self.executor_llm,
                "tools": tools,
                "system_prompt": self._build_agent_instructions(),
            }
            if subagents:
                kwargs["subagents"] = subagents
            if skills:
                kwargs["skills"] = skills
            if backend is not None:
                kwargs["backend"] = backend
            if checkpointer is not None:
                kwargs["checkpointer"] = checkpointer
            if interrupt_on:
                kwargs["interrupt_on"] = interrupt_on
            return create_deep_agent(**kwargs)
        except TypeError as exc:
            raise RuntimeError(f"Deep Agents API mismatch: {exc}") from exc

    def _ensure_checkpointer(self) -> Any | None:
        if self._checkpointer is not None:
            return self._checkpointer
        workspace = Path(self.context.workspace).expanduser().resolve()
        checkpoint_dir = ensure_dir(workspace / "sessions" / "_checkpoints")
        checkpoint_file = checkpoint_dir / "executor.pkl"
        if PersistentMemorySaver is not None:
            self._checkpointer = PersistentMemorySaver(checkpoint_file)
            return self._checkpointer
        if InMemorySaver is None:
            return None
        self._checkpointer = InMemorySaver()
        return self._checkpointer

    @staticmethod
    def _build_interrupt_on() -> dict[str, Any]:
        return {
            "message": {
                "allowed_decisions": ["approve", "edit", "reject"],
                "description": "请确认是否允许 executor 发送消息。",
            },
            "cron": {
                "allowed_decisions": ["approve", "reject"],
                "description": "请确认是否允许 executor 创建或修改定时任务。",
            },
        }

    def _build_agent_instructions(self) -> str:
        workspace = Path(self.context.workspace).expanduser().resolve()
        base = (
            "你是 executor，负责复杂问题的规划、执行、核查与结果收口。\n"
            "你处理的是 main_brain -> executor 这条内部执行链路，不负责对用户做最终表达。\n\n"
            f"当前工作区目录是 `{workspace}`。\n"
            "工作区虚拟路径通过 `/state/` 暴露，内置技能通过 `/skills/` 暴露。\n\n"
            "## 职责\n"
            "1. 接收 main_brain 委托的问题并转成可执行步骤。\n"
            "2. 必要时拆分步骤、调用工具、使用 skills、委派子代理。\n"
            "3. 给出清晰结论、风险、缺失信息和下一步建议。\n"
            "4. 只关注把事情做对，不模仿主脑的陪伴语气。\n\n"
            "## 边界\n"
            "1. 不负责最终对用户表达。\n"
            "2. 不把内部分析伪装成用户可见对话。\n"
            "3. 不负责关系判断、人格维护、情绪陪伴和主脑反思。\n"
            "4. 不更新 `SOUL.md`、`USER.md`，也不负责 `light_insight` / `deep_insight`。\n"
            "5. 不自行读写长期 `memory` 沉淀文件；工具调用后的即时经验会由外层记录为 `tool_light_reflection`。\n"
            "6. 不保留临时草稿、一次性中间产物、原始噪声输出。\n\n"
            "## 输出规则\n"
            "1. 最终只输出协议要求的 JSON。\n"
            "2. JSON 只包含 status、analysis、risks、missing、recommended_action、confidence。"
        )
        skills_context = self._build_internal_skill_context()
        extras = [section for section in (skills_context,) if section]
        if not extras:
            return base
        return f"{base}\n\n" + "\n\n".join(extras)

    def _build_internal_skill_context(self) -> str:
        skills_loader = getattr(self.context, "skills", None)
        if skills_loader is None:
            return ""

        skills_summary = skills_loader.build_skills_summary()
        if not skills_summary:
            return ""

        return (
            "## Skills\n\n"
            "以下技能同时来自工作区 `skills/` 与内置 `emoticorebot/skills/`。\n"
            "如果当前问题需要某个 skill，先读取对应 `SKILL.md`，再按其中流程执行。\n"
            "工作区同名 skill 优先覆盖内置 skill。\n\n"
            f"{skills_summary}"
        )

    def _build_backend(self) -> Any | None:
        workspace = Path(self.context.workspace).expanduser().resolve()
        builtin_skills_root = BUILTIN_SKILLS_DIR.resolve()

        try:
            from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
        except Exception:
            return None

        def build_agent_backend(rt: Any) -> Any:
            return CompositeBackend(
                default=StateBackend(rt),
                routes={
                    "/state/": FilesystemBackend(root_dir=workspace, virtual_mode=True),
                    "/skills/": FilesystemBackend(root_dir=builtin_skills_root, virtual_mode=True),
                },
            )

        return build_agent_backend

    def _build_tools(self) -> list[Any]:
        return self._build_registry_tools(["web_search", "web_fetch", "message", "cron"])

    def _build_subagents(self) -> list[Any]:
        research_tools = self._build_registry_tools(["web_search", "web_fetch"])
        workspace_tools = self._build_registry_tools(["message", "cron"])
        skill_paths = self._build_skill_paths()

        subagents: list[dict[str, Any]] = [
            {
                "name": "research",
                "description": "负责信息检索、网页抓取、事实对比、资料整理。",
                "system_prompt": (
                    "你是 research 子 agent。专注检索、阅读、比对、总结外部信息。"
                    "避免直接做大规模工作区修改。"
                ),
                "tools": research_tools,
            },
            {
                "name": "workspace",
                "description": "负责本地工作区分析、文件修改、命令执行、代码与文档处理。",
                "system_prompt": (
                    "你是 workspace 子 agent。专注本地文件、代码、命令与工作区操作。"
                    "优先保持修改最小且可验证。"
                ),
                "tools": workspace_tools,
            },
        ]

        if skill_paths:
            for item in subagents:
                item["skills"] = skill_paths
        return subagents

    def _build_registry_tools(self, names: list[str]) -> list[Any]:
        if self.tools is None:
            return []

        built: list[Any] = []
        for name in names:
            tool = self._build_registry_tool(name)
            if tool is not None:
                built.append(tool)
        return built

    def _build_registry_tool(self, name: str) -> Any | None:
        if self.tools is None:
            return None

        registry_tool = self.tools.get(name) if hasattr(self.tools, "get") else None
        if registry_tool is None:
            return None

        try:
            from langchain_core.tools import StructuredTool
            from pydantic import create_model
        except Exception:
            return None

        properties = dict((registry_tool.parameters or {}).get("properties", {}) or {})
        required = set((registry_tool.parameters or {}).get("required", []) or [])
        field_defs: dict[str, tuple[Any, Any]] = {}

        for key, schema in properties.items():
            field_type = self._json_schema_to_python_type(schema)
            default = ... if key in required else None
            field_defs[key] = (field_type, default)

        args_schema = create_model(f"{name.title().replace('_', '')}Args", **field_defs)  # type: ignore[call-overload]

        async def _runner(**kwargs: Any) -> str:
            return await self.tools.execute(name, kwargs)

        _runner.__name__ = name
        _runner.__doc__ = str(registry_tool.description or name)
        return StructuredTool.from_function(
            coroutine=_runner,
            name=name,
            description=str(registry_tool.description or name),
            args_schema=args_schema,
        )

    @staticmethod
    def _json_schema_to_python_type(schema: dict[str, Any] | None) -> Any:
        schema = schema or {}
        schema_type = str(schema.get("type", "string") or "string")
        if schema_type == "integer":
            return int
        if schema_type == "number":
            return float
        if schema_type == "boolean":
            return bool
        if schema_type == "array":
            return list[Any]
        if schema_type == "object":
            return dict[str, Any]
        return str

    def _build_skill_paths(self) -> list[str]:
        workspace = getattr(self.context, "workspace", None)
        paths: list[str] = []

        if workspace is not None:
            workspace_skills = (Path(workspace) / "skills").resolve()
            if workspace_skills.exists():
                paths.append(str(workspace_skills))

        builtin_skills = BUILTIN_SKILLS_DIR.resolve()
        if builtin_skills.exists() and str(builtin_skills) not in paths:
            paths.append(str(builtin_skills))

        return paths

    def _build_request_prompt(
        self,
        *,
        request: str,
        history: list[dict[str, Any]],
        execution_context: dict[str, Any] | None,
        media: list[str] | None,
    ) -> str:
        parts = [f"内部问题：{request}"] if request else []

        execution = execution_context or {}
        resume_payload = execution.get("resume_payload")
        missing = self._extract_missing(execution)
        thread_id = str(execution.get("thread_id", "") or "").strip()
        run_id = str(execution.get("run_id", "") or "").strip()

        if thread_id:
            parts.append(f"当前执行线程：{thread_id}")
        if run_id:
            parts.append(f"当前执行轮次：{run_id}")
        if resume_payload not in (None, "", [], {}):
            payload_text = resume_payload if isinstance(resume_payload, str) else json.dumps(resume_payload, ensure_ascii=False)
            parts.append(f"恢复载荷：{payload_text}")
        if missing:
            parts.append(f"需优先确认缺参：{json.dumps(missing, ensure_ascii=False)}")
        if media:
            parts.append(f"关联媒体数量：{len(media)}")

        compact_history = self._compact_history(history)
        if compact_history:
            parts.append("最近内部上下文：")
            parts.extend(compact_history)

        parts.append(
            "请完成复杂问题的分析与执行。最终请只输出一个 JSON 对象："
            '{"status":"completed|needs_input|uncertain|failed","analysis":"...",'
            '"risks":["..."],"missing":["..."],'
            '"recommended_action":"answer|ask_user|continue","confidence":0.0}'
        )
        return "\n".join(parts)

    @staticmethod
    def _compact_history(history: list[dict[str, Any]] | None, *, limit: int = 6) -> list[str]:
        compact: list[str] = []
        filtered: list[dict[str, Any]] = []
        for item in reversed(history or []):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "") or "").strip()
            if role == "tool":
                continue
            content = " ".join(str(item.get("content", "") or "").split()).strip()
            if role == "assistant" and not content:
                tool_calls = item.get("tool_calls")
                if isinstance(tool_calls, list) and tool_calls:
                    continue
            filtered.append(item)
            if len(filtered) >= limit:
                break

        for item in reversed(filtered):
            role = str(item.get("role", "") or "").strip()
            content = " ".join(str(item.get("content", "") or "").split()).strip()
            if role and content:
                compact.append(f"- {role}: {content[:200]}")
        return compact

    async def _invoke_agent(
        self,
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
                "assistant_id": "emoticorebot-executor",
                "run_id": run_id,
                "channel": channel,
                "chat_id": chat_id,
                "session_id": session_id,
            },
        }
        if hasattr(agent, "astream"):
            return await self._stream_agent(agent, payload=payload, config=config, on_trace=on_trace)
        if hasattr(agent, "ainvoke"):
            return await agent.ainvoke(payload, config=config)
        if hasattr(agent, "invoke"):
            return agent.invoke(payload, config=config)
        raise RuntimeError("Deep Agent does not expose invoke/ainvoke/astream")

    @staticmethod
    def _build_thread_id(*, channel: str, chat_id: str, session_id: str, run_id: str) -> str:
        base = str(session_id or "").strip()
        if not base:
            channel_text = str(channel or "").strip()
            chat_text = str(chat_id or "").strip()
            base = f"{channel_text}:{chat_text}" if channel_text or chat_text else "default"
        return f"exec:{base}:{run_id}"

    @staticmethod
    def _new_run_id() -> str:
        return f"run_{uuid4().hex[:12]}"

    async def _stream_agent(
        self,
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
            namespace, mode, data = self._unpack_stream_item(item)
            if mode == "values":
                last_values = data
                continue
            if on_trace is None:
                continue
            for record in self._build_trace_records(mode=mode, namespace=namespace, data=data):
                await on_trace(record)
        if last_values is None:
            raise RuntimeError("Deep Agent stream did not produce final state")
        return last_values

    @staticmethod
    def _unpack_stream_item(item: Any) -> tuple[tuple[str, ...], str, Any]:
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

    def _build_trace_records(self, *, mode: str, namespace: tuple[str, ...], data: Any) -> list[dict[str, Any]]:
        normalized = self._build_normalized_trace_messages(mode=mode, namespace=namespace, data=data)
        if normalized:
            return normalized

        base: dict[str, Any] = {
            "role": "assistant",
            "phase": "executor_trace",
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
                record["content"] = self._summarize_trace_payload(node_data) or str(node_name)
                records.append(record)
            return records

        if mode == "messages":
            return self._build_message_trace_records(base, data)

        if mode == "custom":
            record = dict(base)
            record["content"] = self._compact_trace_text(self._json_safe_dump(data), limit=240)
            return [record]

        return []

    def _build_normalized_trace_messages(self, *, mode: str, namespace: tuple[str, ...], data: Any) -> list[dict[str, Any]]:
        if mode == "messages":
            message = self._extract_message_from_messages_stream(data)
            records = self._message_to_conversation_records(message)
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
                records.extend(self._message_to_conversation_records(messages[-1]))
            if records:
                return records

        return []

    def _extract_message_from_messages_stream(self, data: Any) -> Any | None:
        if not isinstance(data, tuple) or len(data) != 2:
            return None
        message_chunk, _metadata = data
        return message_chunk

    def _message_to_conversation_records(self, message: Any) -> list[dict[str, Any]]:
        if message is None:
            return []

        records: list[dict[str, Any]] = []
        timestamp = datetime.now().isoformat()

        tool_calls = self._extract_message_attr(message, "tool_calls")
        normalized_calls = self._normalize_trace_tool_calls(tool_calls)
        if normalized_calls:
            assistant_record: dict[str, Any] = {
                "role": "assistant",
                "content": normalize_content_blocks(self._extract_message_attr(message, "content")),
                "tool_calls": normalized_calls,
                "timestamp": timestamp,
            }
            assistant_record["trace_signature"] = self._trace_signature(assistant_record)
            records.append(assistant_record)

        tool_call_id = str(self._extract_message_attr(message, "tool_call_id") or "").strip()
        message_type = str(self._extract_message_attr(message, "type") or type(message).__name__ or "").lower()
        name = str(self._extract_message_attr(message, "name") or "").strip()
        content = normalize_content_blocks(self._extract_message_attr(message, "content"))
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
            tool_record["trace_signature"] = self._trace_signature(tool_record)
            records.append(tool_record)

        return records

    @staticmethod
    def _normalize_trace_tool_calls(value: Any) -> list[dict[str, Any]]:
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

    @staticmethod
    def _trace_signature(payload: dict[str, Any]) -> str:
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

    def _build_message_trace_records(self, base: dict[str, Any], data: Any) -> list[dict[str, Any]]:
        if not isinstance(data, tuple) or len(data) != 2:
            return []
        message_chunk, metadata = data
        record_base = dict(base)
        if isinstance(metadata, dict):
            node_name = str(metadata.get("langgraph_node", "") or "").strip()
            if node_name:
                record_base["node"] = node_name

        tool_call_chunks = self._extract_message_attr(message_chunk, "tool_call_chunks")
        if not isinstance(tool_call_chunks, list):
            return []

        records: list[dict[str, Any]] = []
        for chunk in tool_call_chunks:
            if not isinstance(chunk, dict):
                continue
            tool_name = str(chunk.get("name", "") or "").strip()
            args_chunk = self._compact_trace_text(str(chunk.get("args", "") or "").strip(), limit=200)
            if not tool_name and not args_chunk:
                continue
            record = dict(record_base)
            record["event"] = "tool_call"
            if tool_name:
                record["tool_name"] = tool_name
            record["content"] = args_chunk or tool_name
            records.append(record)
        return records

    def _summarize_trace_payload(self, payload: Any) -> str:
        if isinstance(payload, dict):
            messages = payload.get("messages")
            if isinstance(messages, list) and messages:
                return self._summarize_trace_message(messages[-1])
            return self._compact_trace_text(self._json_safe_dump(payload), limit=240)
        if isinstance(payload, list) and payload:
            return self._compact_trace_text(self._json_safe_dump(payload[-1]), limit=240)
        return self._compact_trace_text(str(payload or ""), limit=240)

    def _summarize_trace_message(self, message: Any) -> str:
        tool_calls = self._extract_message_attr(message, "tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            names = [
                str(call.get("name", "") or "").strip()
                for call in tool_calls
                if isinstance(call, dict) and str(call.get("name", "") or "").strip()
            ]
            if names:
                return "tool_calls: " + ", ".join(names)

        name = str(self._extract_message_attr(message, "name") or "").strip()
        content = self._extract_message_attr(message, "content")
        content_text = self._compact_trace_text(self._normalize_message_content(content), limit=240)
        if name and content_text:
            return f"{name}: {content_text}"
        return content_text

    @staticmethod
    def _extract_message_attr(message: Any, key: str) -> Any:
        if isinstance(message, dict):
            return message.get(key)
        return getattr(message, key, None)

    @staticmethod
    def _normalize_message_content(content: Any) -> str:
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

    @staticmethod
    def _json_safe_dump(value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            return str(value)

    @staticmethod
    def _compact_trace_text(text: str, *, limit: int = 240) -> str:
        compact = " ".join(str(text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1] + "…"

    def _normalize_result_packet(

        self,
        raw_result: Any,
        *,
        request: str,
        execution_context: dict[str, Any] | None,
    ) -> ExecutorResultPacket:
        del request
        metrics = self._extract_result_metrics(raw_result)
        if self._is_interrupt_result(raw_result):
            packet = self._build_paused_packet(raw_result, execution_context=execution_context)
            packet.update(metrics)
            return packet

        text = self._extract_text(raw_result).strip()
        if not text:
            packet = self._failed_packet(
                analysis="Deep Agents 未返回有效内容。",
                missing=self._extract_missing(execution_context),
            )
            packet.update(metrics)
            return packet

        parsed = self._parse_json(text)
        if isinstance(parsed, dict):
            raw_status = str(parsed.get("status", "completed") or "completed").strip().lower()
            if raw_status not in self._VALID_RAW_STATUS:
                raw_status = "completed"
            recommended_action = str(parsed.get("recommended_action", "answer") or "answer").strip().lower()
            if recommended_action not in self._VALID_ACTIONS:
                recommended_action = "answer"
            missing = [str(item).strip() for item in parsed.get("missing", []) if str(item).strip()]
            risks = [str(item).strip() for item in parsed.get("risks", []) if str(item).strip()][:8]
            confidence = parsed.get("confidence", 0.0)
            try:
                confidence_value = max(0.0, min(1.0, float(confidence)))
            except Exception:
                confidence_value = 0.0

            control_state, status = self._map_result_status(
                raw_status=raw_status,
                missing=missing,
                recommended_action=recommended_action,
            )
            packet = {
                "control_state": control_state,
                "status": status,
                "analysis": str(parsed.get("analysis", "") or "").strip() or text,
                "risks": risks,
                "missing": missing,
                "recommended_action": recommended_action,
                "confidence": confidence_value,
            }
            packet.update(metrics)
            return packet

        packet = {
            "control_state": "completed",
            "status": "done",
            "analysis": text,
            "risks": [],
            "missing": self._extract_missing(execution_context),
            "recommended_action": "answer",
            "confidence": 0.72,
        }
        packet.update(metrics)
        return packet

    @staticmethod
    def _extract_result_metrics(raw_result: Any) -> dict[str, Any]:
        if isinstance(raw_result, dict):
            messages = raw_result.get("messages")
            if isinstance(messages, list) and messages:
                return extract_message_metrics(messages[-1])
        return extract_message_metrics(raw_result)

    @staticmethod
    def _extract_text(raw_result: Any) -> str:
        if raw_result is None:
            return ""
        if isinstance(raw_result, str):
            return raw_result
        if isinstance(raw_result, dict):
            messages = raw_result.get("messages")
            if isinstance(messages, list) and messages:
                last = messages[-1]
                if isinstance(last, dict):
                    return str(last.get("content", "") or "")
                content = getattr(last, "content", "")
                if isinstance(content, list):
                    return " ".join(str(item) for item in content if item)
                return str(content or "")
            for key in ["output", "content", "answer", "result"]:
                value = raw_result.get(key)
                if value:
                    return str(value)
            return json.dumps(raw_result, ensure_ascii=False)
        content = getattr(raw_result, "content", "")
        if isinstance(content, list):
            return " ".join(str(item) for item in content if item)
        if content:
            return str(content)
        return str(raw_result)

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any] | None:
        raw = str(text or "").strip()
        if not raw:
            return None
        if raw.startswith("```"):
            raw = raw.strip("`")
            raw = raw.replace("json\n", "", 1).strip()
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            parsed = json.loads(raw[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    @staticmethod
    def _extract_missing(execution_context: dict[str, Any] | None) -> list[str]:
        params = execution_context or {}
        missing: list[str] = []
        for item in params.get("missing", []) or []:
            text = str(item or "").strip()
            if text and text not in missing:
                missing.append(text)
        return missing

    @staticmethod
    def _is_interrupt_result(raw_result: Any) -> bool:
        return isinstance(raw_result, dict) and bool(raw_result.get("__interrupt__"))

    def _build_paused_packet(
        self,
        raw_result: Any,
        *,
        execution_context: dict[str, Any] | None,
    ) -> ExecutorResultPacket:
        summary = self._summarize_interrupt(raw_result)
        pending_review = self._extract_pending_review(raw_result)
        return {
            "control_state": "paused",
            "status": "need_more",
            "analysis": summary or "executor 已暂停，等待恢复输入。",
            "risks": [],
            "missing": self._extract_missing(execution_context),
            "recommended_action": "ask_user",
            "confidence": 0.0,
            "pending_review": pending_review,
        }

    @staticmethod
    def _map_result_status(*, raw_status: str, missing: list[str], recommended_action: str) -> tuple[str, str]:
        if raw_status == "failed":
            return "stopped", "failed"
        if raw_status == "needs_input" or missing or recommended_action == "ask_user":
            return "paused", "need_more"
        if raw_status == "uncertain" or recommended_action == "continue":
            return "completed", "need_more"
        return "completed", "done"

    @staticmethod
    def _summarize_interrupt(raw_result: Any) -> str:
        interrupts = raw_result.get("__interrupt__") if isinstance(raw_result, dict) else None
        if not interrupts:
            return ""
        parts: list[str] = []
        for item in interrupts:
            value = getattr(item, "value", item)
            if isinstance(value, dict):
                action_requests = value.get("action_requests")
                if isinstance(action_requests, list) and action_requests:
                    names = [
                        str(action.get("name", "") or "").strip()
                        for action in action_requests
                        if isinstance(action, dict) and str(action.get("name", "") or "").strip()
                    ]
                    if names:
                        parts.append(f"等待审批动作：{', '.join(names)}")
                        continue
                text = json.dumps(value, ensure_ascii=False, default=str)
            else:
                text = str(value or "").strip()
            text = " ".join(text.split()).strip()
            if text:
                parts.append(text)
        return "；".join(parts[:2])

    @staticmethod
    def _extract_pending_review(raw_result: Any) -> dict[str, Any]:
        interrupts = raw_result.get("__interrupt__") if isinstance(raw_result, dict) else None
        if not interrupts:
            return {}
        for item in interrupts:
            value = getattr(item, "value", item)
            if not isinstance(value, dict):
                continue
            action_requests = value.get("action_requests")
            if not isinstance(action_requests, list) or not action_requests:
                continue
            review_configs = value.get("review_configs")
            pending_review: dict[str, Any] = {
                "action_requests": [
                    dict(action)
                    for action in action_requests
                    if isinstance(action, dict)
                ]
            }
            if isinstance(review_configs, list) and review_configs:
                pending_review["review_configs"] = [
                    dict(config)
                    for config in review_configs
                    if isinstance(config, dict)
                ]
            return pending_review
        return {}

    @staticmethod
    def _build_resume_value(execution_context: dict[str, Any] | None) -> Any | None:
        execution = execution_context or {}
        if "resume_payload" not in execution:
            return None
        payload = execution.get("resume_payload")
        if isinstance(payload, str):
            raw = payload.strip()
            if not raw:
                return None
            try:
                return json.loads(raw)
            except Exception:
                return raw
        return payload

    @classmethod
    def _failed_packet(
        cls,
        analysis: str,
        missing: list[str] | None = None,
    ) -> ExecutorResultPacket:
        return {
            "control_state": "stopped",
            "status": "failed",
            "analysis": str(analysis or "").strip(),
            "risks": [],
            "missing": list(missing or []),
            "recommended_action": "ask_user" if missing else "answer",
            "confidence": 0.0,
        }


__all__ = ["ExecutorService"]
