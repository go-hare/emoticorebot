"""Runtime - 使用服务类架构

精简后的 Runtime，职责：
1. 消息调度（接收消息、分发处理）
2. 服务编排（协调各个服务完成任务）
3. 会话管理（加载/保存 session）
4. 策略生成（信号提取 → 策略参数）

原 878 行代码 → 精简到 ~300 行
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable
from uuid import uuid4

from loguru import logger

from emoticorebot.bus.events import InboundMessage, OutboundMessage
from emoticorebot.bus.queue import MessageBus
from emoticorebot.config.schema import ModelModeConfig, ProvidersConfig
from emoticorebot.core.context import ContextBuilder
from emoticorebot.core.graph import run_turn_graph
from emoticorebot.core.model import LLMFactory
from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.services import ExecutorService, MainBrainService, MemoryService, ToolManager
from emoticorebot.session.executor_context import build_executor_context
from emoticorebot.session.manager import SessionManager

if TYPE_CHECKING:
    from emoticorebot.config.schema import ChannelsConfig, ExecToolConfig
    from emoticorebot.core.state import (
        ExecutorResultPacket,
        MainBrainDeliberationPacket,
        MainBrainFinalizePacket,
    )
    from emoticorebot.cron.service import CronService


class EmoticoreRuntime:
    """精简的 Runtime - 使用服务类架构

    职责：消息调度 + 服务编排
    """

    def __init__(
        self,
        bus: MessageBus,
        workspace: Path,
        executor_mode: "ModelModeConfig",
        main_brain_mode: "ModelModeConfig",
        brave_api_key: str | None = None,
        exec_config: "ExecToolConfig | None" = None,
        cron_service: "CronService | None" = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: "ChannelsConfig | None" = None,
        providers_config: "ProvidersConfig | None" = None,
    ):
        from emoticorebot.config.schema import ExecToolConfig

        self.bus = bus
        self.workspace = workspace
        self.executor_mode = executor_mode
        self.main_brain_mode = main_brain_mode
        self.memory_window = executor_mode.memory_window
        self.channels_config = channels_config

        # 核心组件
        self.sessions = session_manager or SessionManager(workspace)
        self.emotion_mgr = EmotionStateManager(workspace)
        self.context = ContextBuilder(workspace)

        # LLM 实例 - 通过 LLMFactory 按配置构建
        _factory = LLMFactory(
            providers_config=providers_config,
            executor_mode=executor_mode,
            main_brain_mode=main_brain_mode,
        )
        self.executor_llm = _factory.get_executor()
        self.main_brain_llm = _factory.get_main_brain()

        # 服务类（单一职责）
        self.main_brain_service = MainBrainService(self.main_brain_llm, self.context)
        self.executor_service = ExecutorService(self.executor_llm, None, self.context)  # registry injected later
        self.memory_service = MemoryService(
            workspace, self.emotion_mgr, self.sessions, executor_mode.memory_window,
            reflection_llm=self.main_brain_llm,
        )
        self.tool_manager = ToolManager(
            workspace,
            exec_config or ExecToolConfig(),
            bus,
            cron_service,
            brave_api_key,
            restrict_to_workspace,
        )

        # Register default tools and inject the registry into the executor service.
        self.tool_manager.register_default_tools()
        self.executor_service.tools = self.tool_manager.get_registry()

        # SubagentManager（初始化后注册 spawn 工具）
        self.subagents = None
        self._initialize_subagent_manager()

        # MCP 服务器配置
        self._mcp_servers = mcp_servers or {}

        # 预编译 LangGraph agent（每个 Runtime 实例只编译一次）
        from emoticorebot.core.graph import create_turn_graph
        self._compiled_graph = create_turn_graph(self.workspace, runtime=self)

        # 运行状态
        self._running = False
        self._active_tasks: dict[str, list[asyncio.Task]] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}

        # 潜意识守护进程和心跳服务（延迟初始化）
        self.subconscious = None
        self.heartbeat = None

    def _initialize_subagent_manager(self) -> None:
        """初始化 SubagentManager 并注册 spawn 工具"""
        from emoticorebot.background.subagent import SubagentManager

        self.subagents = SubagentManager(
            workspace=self.workspace,
            bus=self.bus,
            executor_llm=self.executor_llm,
            brave_api_key=self.tool_manager.brave_api_key,
            exec_config=self.tool_manager.exec_config,
            restrict_to_workspace=self.tool_manager.restrict_to_workspace,
        )

        self.tool_manager.register_spawn_tool(self.subagents)
        logger.debug("SubagentManager initialized")

    @staticmethod
    def _parse_execution_control_command(content: str) -> tuple[str, str] | None:
        raw = str(content or "").strip()
        if not raw.startswith("/"):
            return None
        command, _, argument = raw.partition(" ")
        action = {
            "/stop": "stop",
            "/pause": "pause",
            "/resume": "resume",
            "/continue": "continue",
            "/approve": "approve",
            "/reject": "reject",
            "/edit": "edit",
        }.get(command.lower())
        if not action:
            return None
        return action, argument.strip()

    @staticmethod
    def _help_text() -> str:
        return (
            "🐾 emoticorebot commands:\n"
            "/new  — Start a new conversation\n"
            "/stop — Stop the current request\n"
            "/pause — Inspect whether current execution can pause\n"
            "/resume — Resume a paused execution\n"
            "/continue — Continue a paused execution\n"
            "/help — Show available commands"
        )

    async def run(self) -> None:
        """主循环：接收消息并调度"""
        self._running = True
        await self.tool_manager.connect_mcp_servers(self._mcp_servers)
        logger.info("Emoticore runtime started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            control = self._parse_execution_control_command(msg.content)
            if control and control[0] in {"stop", "pause"}:
                response = await self._handle_execution_control(msg, action=control[0], argument=control[1])
                if response is not None:
                    await self.bus.publish_outbound(response)
                continue

            task = asyncio.create_task(self._dispatch(msg))
            self._active_tasks.setdefault(msg.session_key, []).append(task)
            task.add_done_callback(
                lambda t, k=msg.session_key: (
                    self._active_tasks.get(k, []) and t in self._active_tasks[k] and self._active_tasks[k].remove(t)
                )
            )

    async def _handle_stop(self, msg: InboundMessage) -> None:
        """处理停止命令"""
        response = await self._handle_execution_control(msg, action="stop", argument="")
        if response is not None:
            await self.bus.publish_outbound(response)

    async def _handle_execution_control(
        self,
        msg: InboundMessage,
        *,
        action: str,
        argument: str,
    ) -> OutboundMessage | None:
        if action == "stop":
            return await self._stop_execution(msg)
        if action == "pause":
            return await self._pause_execution(msg)
        if action in {"resume", "continue", "approve", "reject", "edit"}:
            return await self._resume_execution(msg, action=action, argument=argument)
        return None

    async def _dispatch(self, msg: InboundMessage) -> None:
        """调度单条消息"""
        lock = self._session_locks.setdefault(msg.session_key, asyncio.Lock())
        async with lock:
            response = await self._process_message(msg)
            if response is not None:
                await self.bus.publish_outbound(response)

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """处理消息（核心逻辑）"""
        # 拦截潜意识系统内部指令
        if msg.content == "__subconscious_recovery__":
            if self.subconscious:
                await self.subconscious.handle_energy_recovery()
            return None

        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)
        cmd = msg.content.strip().lower()

        control = self._parse_execution_control_command(msg.content)
        if control is not None:
            return await self._handle_execution_control(msg, action=control[0], argument=control[1])

        # 内置命令处理
        if cmd == "/new":
            session.clear()
            self.sessions.clear_internal_messages(session.key)
            self.sessions.save(session)
            self.sessions.invalidate(session.key)
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="New session started.")
        if cmd == "/help":
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=self._help_text(),
            )

        # 设置工具上下文
        self.tool_manager.set_context(
            msg.channel, msg.chat_id, msg.metadata.get("message_id"), key
        )

        # Load persisted dialogue history plus internal deliberation history.
        dialogue_history = session.get_history(max_messages=self.memory_window, include_executor_context=False)
        internal_history = self.sessions.get_internal_messages(key, max_messages=self.memory_window)
        message_id = str(msg.metadata.get("message_id", "") or "").strip() or self._new_message_id()
        msg.metadata["message_id"] = message_id
        turn_metadata = self._build_turn_metadata(session=session, user_input=msg.content, message_id=message_id)

        content, final_state = await run_turn_graph(
            user_input=msg.content,
            workspace=self.workspace,
            runtime=self,
            dialogue_history=dialogue_history,
            internal_history=internal_history,
            metadata=turn_metadata,
            channel=msg.channel,
            chat_id=msg.chat_id,
            session_id=key,
            on_progress=on_progress,
            agent=self._compiled_graph,
        )

        # 保存对话到 session
        assistant_timestamp = datetime.now().isoformat()
        self.sessions.append_internal_messages(
            key,
            self._build_internal_turn_records(
                final_state,
                assistant_timestamp=assistant_timestamp,
                message_id=message_id,
                existing_internal_count=len(internal_history),
            ),
        )

        user_content = self._build_user_message_content(msg.content, msg.media)
        assistant_fields = self._build_assistant_session_fields(final_state)
        session.add_message("user", user_content, message_id=message_id, timestamp=msg.timestamp.isoformat())
        session.add_message(
            "assistant",
            [{"type": "text", "text": content}],
            message_id=message_id,
            timestamp=assistant_timestamp,
            **assistant_fields,
        )

        self.sessions.save(session)
        self._save_proactive_target(msg.channel, msg.chat_id)
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=msg.metadata or {})

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """直接处理消息（不通过消息总线，供 CLI 使用）"""
        await self.tool_manager.connect_mcp_servers(self._mcp_servers)
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)
        response = await self._process_message(msg, session_key=session_key, on_progress=on_progress)
        return response.content if response else ""

    async def close_mcp(self) -> None:
        """关闭 MCP 连接"""
        await self.tool_manager.close_mcp()

    def stop(self) -> None:
        """停止 Runtime"""
        self._running = False

    def _build_executor_summary(self, final_state: dict[str, Any]) -> str:
        """Build a compact persisted summary from the current turn's executor execution."""
        executor = final_state.get("executor")
        if executor is None:
            return ""
        return build_executor_context(
            {
                "execution": {
                    "thread_id": str(getattr(executor, "thread_id", "") or ""),
                    "run_id": str(getattr(executor, "run_id", "") or ""),
                    "control_state": getattr(executor, "control_state", ""),
                    "status": getattr(executor, "status", ""),
                    "summary": getattr(executor, "analysis", ""),
                    "recommended_action": getattr(executor, "recommended_action", ""),
                    "confidence": float(getattr(executor, "confidence", 0.0) or 0.0),
                    "missing": list(getattr(executor, "missing", []) or []),
                }
            }
        )

    def get_execution_state(self, session_key: str) -> dict[str, Any]:
        session = self.sessions.get(session_key)
        if session is None:
            return {}
        return self._extract_last_execution(session)

    def has_active_execution(self, session_key: str) -> bool:
        return any(not task.done() for task in self._active_tasks.get(session_key, []))

    def _snapshot_execution(
        self,
        *,
        executor: Any | None = None,
        execution: dict[str, Any] | None = None,
        summary: str = "",
    ) -> dict[str, Any]:
        base = dict(execution or {})
        if executor is not None:
            executor_snapshot = {
                "invoked": True,
                "thread_id": str(getattr(executor, "thread_id", "") or "").strip(),
                "run_id": str(getattr(executor, "run_id", "") or "").strip(),
                "control_state": str(getattr(executor, "control_state", "") or "idle").strip(),
                "status": str(getattr(executor, "status", "") or "none").strip(),
                "summary": str(summary or getattr(executor, "analysis", "") or "").strip(),
                "recommended_action": str(getattr(executor, "recommended_action", "") or "").strip(),
                "confidence": float(getattr(executor, "confidence", 0.0) or 0.0),
                "missing": list(getattr(executor, "missing", []) or []),
                "pending_review": dict(getattr(executor, "pending_review", {}) or {}),
            }
            executor_has_state = any(
                [
                    executor_snapshot["thread_id"],
                    executor_snapshot["run_id"],
                    executor_snapshot["summary"],
                    executor_snapshot["missing"],
                    executor_snapshot["pending_review"],
                    executor_snapshot["control_state"] not in {"", "idle"},
                    executor_snapshot["status"] not in {"", "none"},
                ]
            )
            if executor_has_state or not base:
                base.update(executor_snapshot)
            elif summary and not str(base.get("summary", "") or "").strip():
                base["summary"] = summary
        elif summary and not str(base.get("summary", "") or "").strip():
            base["summary"] = summary

        invoked = bool(base.get("invoked")) or any(
            [
                str(base.get("thread_id", "") or "").strip(),
                str(base.get("run_id", "") or "").strip(),
                str(base.get("summary", "") or "").strip(),
                list(base.get("missing", []) or []),
                dict(base.get("pending_review", {}) or {}),
                str(base.get("control_state", "") or "").strip() not in {"", "idle"},
            ]
        )
        snapshot = {
            "invoked": invoked,
            "thread_id": str(base.get("thread_id", "") or "").strip(),
            "run_id": str(base.get("run_id", "") or "").strip(),
            "control_state": str(base.get("control_state", "") or ("idle" if not invoked else "completed")).strip(),
            "status": str(base.get("status", "") or ("none" if not invoked else "done")).strip(),
            "summary": str(base.get("summary", "") or "").strip(),
            "recommended_action": str(base.get("recommended_action", "") or "").strip(),
            "confidence": float(base.get("confidence", 0.0) or 0.0),
            "missing": [str(item).strip() for item in list(base.get("missing", []) or []) if str(item).strip()],
            "pending_review": dict(base.get("pending_review", {}) or {}),
        }
        return snapshot

    @staticmethod
    def _execution_event_name(execution: dict[str, Any]) -> str:
        control_state = str(execution.get("control_state", "") or "completed").strip() or "completed"
        status = str(execution.get("status", "") or "none").strip() or "none"
        return f"execution.{control_state}.{status}"

    @staticmethod
    def _summarize_resume_payload(resume_payload: Any) -> str:
        if resume_payload in (None, "", [], {}):
            return ""
        if isinstance(resume_payload, dict):
            decisions = resume_payload.get("decisions") if isinstance(resume_payload.get("decisions"), list) else []
            if decisions:
                labels = [
                    str(item.get("type", "") or "").strip()
                    for item in decisions
                    if isinstance(item, dict) and str(item.get("type", "") or "").strip()
                ]
                if labels:
                    return f"恢复决策：{', '.join(labels)}"
            return json.dumps(resume_payload, ensure_ascii=False)
        return str(resume_payload).strip()

    def _build_internal_lifecycle_records(
        self,
        final_state: dict[str, Any],
        *,
        assistant_timestamp: str,
        message_id: str,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        metadata = final_state.get("metadata") if isinstance(final_state.get("metadata"), dict) else {}
        metadata_execution = metadata.get("execution") if isinstance(metadata.get("execution"), dict) else {}
        main_brain = final_state.get("main_brain")
        executor = final_state.get("executor")
        executor_summary = self._build_executor_summary(final_state)
        execution = self._snapshot_execution(executor=executor, execution=metadata_execution, summary=executor_summary)

        if main_brain is not None:
            main_brain_payload = {
                "intent": str(getattr(main_brain, "intent", "") or "").strip(),
                "working_hypothesis": str(getattr(main_brain, "working_hypothesis", "") or "").strip(),
                "question_to_executor": str(getattr(main_brain, "question_to_executor", "") or "").strip(),
                "final_decision": str(getattr(main_brain, "final_decision", "") or "").strip(),
                "final_message": str(getattr(main_brain, "final_message", "") or "").strip(),
                "execution_action": str(getattr(main_brain, "execution_action", "") or "").strip(),
                "execution_reason": str(getattr(main_brain, "execution_reason", "") or "").strip(),
            }
            main_brain_payload = {key: value for key, value in main_brain_payload.items() if value}
            if main_brain_payload:
                records.append(
                    {
                        "message_id": message_id,
                        "role": "assistant",
                        "phase": "main_brain",
                        "event": "main_brain.turn.summary",
                        "source": "runtime",
                        "content": json.dumps(main_brain_payload, ensure_ascii=False),
                        "main_brain": main_brain_payload,
                        "timestamp": assistant_timestamp,
                    }
                )

        resume_payload = metadata_execution.get("resume_payload") if isinstance(metadata_execution, dict) else None
        resume_summary = self._summarize_resume_payload(resume_payload)
        if execution.get("invoked") and resume_summary:
            records.append(
                {
                    "message_id": message_id,
                    "role": "assistant",
                        "phase": "main_brain",
                        "event": "main_brain.execution.resume_requested",
                        "source": "runtime",
                        "content": resume_summary,
                        "main_brain": {
                            "execution_action": "resume",
                            "execution_reason": "resume_payload_available",
                        },
                        "execution": execution,
                        "meta": {"resume_payload": resume_payload},
                        "timestamp": assistant_timestamp,
                    }
                )

        if execution.get("invoked"):
            execution_summary_payload = {
                "control_state": execution.get("control_state", "idle"),
                "status": execution.get("status", "none"),
                "thread_id": execution.get("thread_id", ""),
                "run_id": execution.get("run_id", ""),
                "summary": execution.get("summary", ""),
                "missing": execution.get("missing", []),
            }
            if execution.get("pending_review"):
                execution_summary_payload["pending_review"] = execution.get("pending_review", {})
            records.append(
                {
                    "message_id": message_id,
                    "role": "assistant",
                    "phase": "executor",
                    "event": self._execution_event_name(execution),
                    "source": "runtime",
                    "content": json.dumps(execution_summary_payload, ensure_ascii=False),
                    "execution": execution,
                    "timestamp": assistant_timestamp,
                }
            )

        return records

    def _append_internal_execution_event(
        self,
        *,
        session_key: str,
        message_id: str,
        execution: dict[str, Any],
        event: str,
        content: str,
        timestamp: str | None = None,
        source: str = "runtime_control",
    ) -> None:
        if not execution:
            return
        self.sessions.append_internal_messages(
            session_key,
            [
                {
                    "message_id": message_id,
                    "role": "assistant",
                    "phase": "executor",
                    "event": event,
                    "source": source,
                    "content": content,
                    "execution": execution,
                    "timestamp": timestamp or datetime.now().isoformat(),
                }
            ],
        )

    def _append_internal_main_brain_event(
        self,
        *,
        session_key: str,
        message_id: str,
        main_brain: dict[str, Any],
        timestamp: str | None = None,
        event: str = "main_brain.execution.control",
        source: str = "runtime_control",
    ) -> None:
        if not main_brain:
            return
        self.sessions.append_internal_messages(
            session_key,
            [
                {
                    "message_id": message_id,
                    "role": "assistant",
                    "phase": "main_brain",
                    "event": event,
                    "source": source,
                    "content": json.dumps(main_brain, ensure_ascii=False),
                    "main_brain": main_brain,
                    "timestamp": timestamp or datetime.now().isoformat(),
                }
            ],
        )

    def _build_assistant_session_fields(
        self,
        final_state: dict[str, Any],
    ) -> dict[str, Any]:
        main_brain = final_state.get("main_brain")
        executor = final_state.get("executor")
        if main_brain is None:
            return {}
        execution = self._snapshot_execution(executor=executor, summary=self._build_executor_summary(final_state))
        return {
            **{
                key: value
                for key, value in {
                    "model_name": str(getattr(main_brain, "model_name", "") or ""),
                    "prompt_tokens": int(getattr(main_brain, "prompt_tokens", 0) or 0),
                    "completion_tokens": int(getattr(main_brain, "completion_tokens", 0) or 0),
                    "total_tokens": int(getattr(main_brain, "total_tokens", 0) or 0),
                }.items()
                if value not in ("", 0)
            },
            "execution": {
                key: value
                for key, value in execution.items()
                if value not in ("", [], {}, None)
            },
        }

    def _build_internal_turn_records(
        self,
        final_state: dict[str, Any],
        *,
        assistant_timestamp: str,
        message_id: str,
        existing_internal_count: int = 0,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = self._build_internal_lifecycle_records(
            final_state,
            assistant_timestamp=assistant_timestamp,
            message_id=message_id,
        )
        seen_signatures: set[str] = set()
        internal_history = (final_state.get("internal_history", []) or [])[max(0, existing_internal_count):]
        executor_trace = final_state.get("executor_trace", []) or []
        for source_name, source in (("internal_history", internal_history), ("executor_trace", executor_trace)):
            for item in source:
                if not isinstance(item, dict):
                    continue
                if source_name == "executor_trace" and item.get("phase"):
                    continue
                payload = dict(item)
                payload.setdefault("message_id", message_id)
                payload.setdefault("timestamp", assistant_timestamp)
                signature = str(payload.pop("trace_signature", "") or "").strip()
                if signature:
                    if signature in seen_signatures:
                        continue
                    seen_signatures.add(signature)
                records.append(payload)
        records.sort(key=lambda item: (str(item.get("timestamp", "") or assistant_timestamp), item.get("role", ""), item.get("event", "")))
        return records

    def _build_user_message_content(self, content: str, media: list[str] | None) -> list[dict[str, Any]]:
        media_items = self.context.build_media_context(media)
        return [{"type": "text", "text": str(content or "")}, *media_items]

    @staticmethod
    def _new_message_id() -> str:
        return f"msg_{uuid4().hex[:16]}"

    def _build_turn_metadata(self, *, session, user_input: str, message_id: str) -> dict[str, Any]:
        metadata: dict[str, Any] = {"message_id": message_id}
        execution = self._build_resume_execution_context(session=session, user_input=user_input)
        if execution:
            metadata["execution"] = execution
        return metadata

    @staticmethod
    def _extract_last_execution(session) -> dict[str, Any]:
        for message in reversed(getattr(session, "messages", []) or []):
            if message.get("role") != "assistant":
                continue
            execution = message.get("execution")
            if isinstance(execution, dict):
                return dict(execution)
        return {}

    def _build_resume_execution_context(self, *, session, user_input: str) -> dict[str, Any]:
        execution = self._extract_last_execution(session)
        if str(execution.get("control_state", "") or "").strip() != "paused":
            return {}
        resumed = dict(execution)
        resume_input = self._extract_resume_input(
            user_input,
            pending_review=execution.get("pending_review") if isinstance(execution.get("pending_review"), dict) else {},
        )
        if resume_input not in (None, "", [], {}):
            resumed["resume_payload"] = resume_input
        return resumed

    @staticmethod
    def _extract_resume_input(user_input: str, *, pending_review: dict[str, Any] | None = None) -> Any:
        text = str(user_input or "").strip()
        if not text:
            return ""
        parsed = EmoticoreRuntime._parse_resume_json(text)
        if parsed is not None:
            return EmoticoreRuntime._normalize_resume_payload(parsed, pending_review=pending_review)

        lowered = text.lower()
        approve_prefixes = ("approve", "ok", "yes", "继续", "继续吧", "同意", "可以")
        reject_prefixes = ("reject", "no", "停止执行", "拒绝", "不要执行")
        edit_prefixes = ("edit", "编辑", "修改")

        if any(
            lowered == prefix
            or lowered.startswith(f"{prefix} ")
            or lowered.startswith(f"{prefix}:")
            or lowered.startswith(f"{prefix}：")
            for prefix in approve_prefixes
        ):
            return EmoticoreRuntime._build_review_decisions("approve", pending_review=pending_review)

        if any(
            lowered == prefix
            or lowered.startswith(f"{prefix} ")
            or lowered.startswith(f"{prefix}:")
            or lowered.startswith(f"{prefix}：")
            for prefix in reject_prefixes
        ):
            reason = EmoticoreRuntime._strip_resume_prefix(text, reject_prefixes)
            return EmoticoreRuntime._build_review_decisions(
                "reject",
                pending_review=pending_review,
                message=reason or text,
            )

        if any(
            lowered == prefix
            or lowered.startswith(f"{prefix} ")
            or lowered.startswith(f"{prefix}:")
            or lowered.startswith(f"{prefix}：")
            for prefix in edit_prefixes
        ):
            edit_text = EmoticoreRuntime._strip_resume_prefix(text, edit_prefixes)
            return EmoticoreRuntime._build_edit_resume_payload(edit_text, pending_review=pending_review) or text

        return text

    @staticmethod
    def _parse_resume_json(text: str) -> dict[str, Any] | None:
        raw = str(text or "").strip()
        if not raw.startswith("{"):
            return None
        try:
            payload = json.loads(raw)
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _normalize_resume_payload(payload: dict[str, Any], *, pending_review: dict[str, Any] | None = None) -> Any:
        if "decisions" in payload:
            return payload
        decision_type = str(payload.get("type", "") or "").strip().lower()
        if decision_type in {"approve", "reject"}:
            return EmoticoreRuntime._build_review_decisions(
                decision_type,
                pending_review=pending_review,
                message=str(payload.get("message", "") or "").strip(),
            )
        if decision_type == "edit" or "edited_action" in payload:
            edit_payload = dict(payload)
            if decision_type == "edit":
                edit_payload.pop("type", None)
            return EmoticoreRuntime._build_edit_resume_payload(edit_payload, pending_review=pending_review) or payload
        return payload

    @staticmethod
    def _build_review_decisions(
        decision_type: str,
        *,
        pending_review: dict[str, Any] | None = None,
        message: str = "",
    ) -> dict[str, Any]:
        action_requests = (pending_review or {}).get("action_requests")
        count = len(action_requests) if isinstance(action_requests, list) and action_requests else 1
        decisions: list[dict[str, Any]] = []
        for _ in range(count):
            decision: dict[str, Any] = {"type": decision_type}
            if decision_type == "reject" and message:
                decision["message"] = message
            decisions.append(decision)
        return {"decisions": decisions}

    @staticmethod
    def _build_edit_resume_payload(edit_input: Any, *, pending_review: dict[str, Any] | None = None) -> dict[str, Any] | None:
        pending_review = pending_review or {}
        action_requests = pending_review.get("action_requests")
        if not isinstance(action_requests, list) or len(action_requests) != 1:
            if isinstance(edit_input, dict) and "decisions" in edit_input:
                return edit_input
            return None

        action = action_requests[0] if isinstance(action_requests[0], dict) else {}
        action_name = str(action.get("name", "") or "").strip()
        if not action_name:
            return None

        if isinstance(edit_input, dict) and "edited_action" in edit_input:
            edited_action = edit_input.get("edited_action")
            if isinstance(edited_action, dict):
                return {"decisions": [{"type": "edit", "edited_action": edited_action}]}
            return None

        edited_action: dict[str, Any] = {"name": action_name, "args": dict(action.get("args", {}) or {})}
        if isinstance(edit_input, dict):
            if str(edit_input.get("name", "") or "").strip():
                edited_action["name"] = str(edit_input.get("name", "") or "").strip()
            if isinstance(edit_input.get("args"), dict):
                edited_action["args"] = dict(edit_input.get("args") or {})
            else:
                edited_action["args"] = dict(edit_input)
                edited_action["args"].pop("name", None)
        else:
            value = str(edit_input or "").strip()
            if not value:
                return None
            arg_keys = list(edited_action["args"].keys())
            if "content" in edited_action["args"]:
                edited_action["args"]["content"] = value
            elif len(arg_keys) == 1:
                edited_action["args"][arg_keys[0]] = value
            else:
                edited_action["args"] = {"content": value}

        return {"decisions": [{"type": "edit", "edited_action": edited_action}]}

    @staticmethod
    def _strip_resume_prefix(text: str, prefixes: tuple[str, ...]) -> str:
        raw = str(text or "").strip()
        lowered = raw.lower()
        for prefix in prefixes:
            if lowered == prefix:
                return ""
            if lowered.startswith(f"{prefix} "):
                return raw[len(prefix):].strip()
            if lowered.startswith(f"{prefix}:") or lowered.startswith(f"{prefix}："):
                return raw[len(prefix) + 1 :].strip()
        return raw

    def _mark_last_execution_stopped(self, session_key: str) -> None:
        session = self.sessions.get(session_key)
        if session is None:
            return
        for message in reversed(session.messages):
            execution = message.get("execution") if isinstance(message, dict) else None
            if not isinstance(execution, dict):
                continue
            control_state = str(execution.get("control_state", "") or "").strip()
            if control_state not in {"running", "paused"}:
                return
            updated = dict(execution)
            updated["control_state"] = "stopped"
            updated["status"] = "failed" if str(updated.get("status", "") or "").strip() == "none" else updated.get("status", "failed")
            summary = str(updated.get("summary", "") or "").strip()
            if not summary:
                updated["summary"] = "执行已被停止。"
            message["execution"] = updated
            self.sessions.save(session)
            return

    async def _stop_execution(self, msg: InboundMessage) -> OutboundMessage:
        tasks = self._active_tasks.pop(msg.session_key, [])
        cancelled = sum(1 for task in tasks if not task.done() and task.cancel())
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        subagent_cancelled = 0
        if self.subagents:
            subagent_cancelled = await self.subagents.cancel_by_session(msg.session_key)

        self._mark_last_execution_stopped(msg.session_key)
        execution = self.get_execution_state(msg.session_key)
        control = self.main_brain_control_stop_execution(
            cancelled_tasks=cancelled,
            cancelled_subagents=subagent_cancelled,
            execution=execution,
        )
        main_brain_payload = {
            "execution_action": str(control.get("action", "") or "").strip(),
            "execution_reason": str(control.get("reason", "") or "").strip(),
            "final_decision": str(control.get("final_decision", "") or "").strip(),
            "final_message": str(control.get("message", "") or "").strip(),
        }
        main_brain_payload = {key: value for key, value in main_brain_payload.items() if value}
        if execution:
            message_id = str((msg.metadata or {}).get("message_id", "") or self._new_message_id()).strip()
            timestamp = datetime.now().isoformat()
            self._append_internal_main_brain_event(
                session_key=msg.session_key,
                message_id=message_id,
                main_brain=main_brain_payload,
                timestamp=timestamp,
                event="main_brain.execution.stop",
            )
            self._append_internal_execution_event(
                session_key=msg.session_key,
                message_id=message_id,
                execution=execution,
                event="execution.stopped.failed",
                content=str(execution.get("summary", "") or "执行已被手动停止。"),
                timestamp=timestamp,
            )
            self.memory_service.append_execution_memory(
                session_id=msg.session_key,
                turn_id=message_id,
                execution=execution,
                channel=msg.channel,
                source="runtime_control",
                event="execution.stopped.failed",
                main_brain=main_brain_payload,
            )

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=str(control.get("message", "") or "⏹ 当前执行已停止。"),
        )

    async def _pause_execution(self, msg: InboundMessage) -> OutboundMessage:
        execution = self.get_execution_state(msg.session_key)
        control_state = str(execution.get("control_state", "") or "").strip()
        if control_state == "paused":
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="⏸ executor 当前已经处于暂停状态。")
        if self.has_active_execution(msg.session_key):
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="⏸ 当前执行还没有到可恢复的中断点，暂不支持安全 pause；你可以先用 /stop，或等待它进入 paused。",
            )
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="当前没有可暂停的 executor 执行。")

    async def _resume_execution(self, msg: InboundMessage, *, action: str, argument: str) -> OutboundMessage | None:
        execution = self.get_execution_state(msg.session_key)
        if str(execution.get("control_state", "") or "").strip() != "paused":
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="当前没有处于 paused 的 executor 执行。")

        resume_text = self._build_control_resume_text(action=action, argument=argument)
        if resume_text is None:
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="`/edit` 需要提供新的内容或 JSON 参数。")

        synthetic = InboundMessage(
            channel=msg.channel,
            sender_id=msg.sender_id,
            chat_id=msg.chat_id,
            content=resume_text,
            timestamp=msg.timestamp,
            metadata=dict(msg.metadata or {}),
            session_key_override=msg.session_key,
        )
        return await self._process_message(synthetic, session_key=msg.session_key)

    @staticmethod
    def _build_control_resume_text(*, action: str, argument: str) -> str | None:
        payload = str(argument or "").strip()
        if action in {"resume", "continue"}:
            return payload or "继续"
        if action == "approve":
            return f"approve {payload}".strip()
        if action == "reject":
            return f"reject {payload}".strip()
        if action == "edit":
            return f"edit {payload}".strip() if payload else None
        return payload or "继续"

    def _save_proactive_target(self, channel: str, chat_id: str) -> None:
        """保存主动对话目标（供潜意识服务使用）"""
        import json
        target_file = self.workspace / "subconscious_target.json"
        try:
            target_file.write_text(
                json.dumps({"channel": channel, "chat_id": chat_id}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    # ── 主脑主导接口 ───────────────────────────────────────────

    async def main_brain_deliberate(
        self,
        *,
        user_input: str,
        dialogue_history: list[dict[str, Any]],
        emotion: str,
        pad: dict[str, float],
        channel: str = "",
        chat_id: str = "",
        session_id: str = "",
    ) -> "MainBrainDeliberationPacket":
        return await self.main_brain_service.deliberate(
            user_input=user_input,
            history=dialogue_history,
            emotion=emotion,
            pad=pad,
            channel=channel,
            chat_id=chat_id,
            session_id=session_id,
        )

    async def main_brain_finalize(self, **kwargs) -> "MainBrainFinalizePacket":
        return await self.main_brain_service.finalize(**kwargs)

    def main_brain_decide_paused_execution(self, **kwargs):
        return self.main_brain_service.decide_paused_execution(**kwargs)

    def main_brain_control_after_deliberation(self, **kwargs):
        return self.main_brain_service.control_after_deliberation(**kwargs)

    def main_brain_control_after_finalize(self, **kwargs):
        return self.main_brain_service.control_after_finalize(**kwargs)

    def main_brain_control_stop_execution(self, **kwargs):
        return self.main_brain_service.control_stop_execution(**kwargs)

    async def run_executor_request(self, **kwargs) -> "ExecutorResultPacket":
        return await self.executor_service.run_request(**kwargs)

    async def write_memory(self, state: dict) -> None:
        await self.memory_service.write_turn_memory(state)

    # ── 潜意识 & 心跳服务 ────────────────────────────────────────────────────

    def initialize_subconscious(self, enable_reflection: bool = True, enable_heartbeat: bool = False) -> None:
        """初始化潜意识守护进程和心跳服务"""
        if enable_reflection:
            from emoticorebot.background.subconscious import SubconsciousDaemon
            self.subconscious = SubconsciousDaemon(self, self.workspace)
            if hasattr(self.tool_manager, "cron_service") and self.tool_manager.cron_service:
                self.subconscious.register_energy_recovery(self.tool_manager.cron_service)
            logger.info("SubconsciousDaemon initialized")

        if enable_heartbeat:
            from emoticorebot.background.heartbeat import HeartbeatService

            async def execute_heartbeat_task(tasks: str) -> str:
                msg = InboundMessage(
                    channel="__heartbeat__",
                    sender_id="__system__",
                    chat_id="__system__",
                    content=f"请处理以下事项：{tasks}",
                    session_key_override="__heartbeat__",
                    metadata={"_heartbeat": True},
                )
                response = await self._process_message(msg, session_key="__heartbeat__")
                return response.content if response else ""

            async def notify_heartbeat(content: str) -> None:
                target = self.subconscious._load_proactive_target() if self.subconscious else None
                if target:
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=target["channel"],
                            chat_id=target["chat_id"],
                            content=content,
                            metadata={"_heartbeat": True},
                        )
                    )

            self.heartbeat = HeartbeatService(
                workspace=self.workspace,
                runtime=self,
                on_execute=execute_heartbeat_task,
                on_notify=notify_heartbeat,
                interval_s=30 * 60,
                enabled=True,
            )
            logger.info("HeartbeatService initialized")

    def start_background_services(self) -> None:
        """启动后台服务"""
        if self.subconscious:
            self.subconscious.start_background_tasks()
        if self.heartbeat:
            asyncio.create_task(self.heartbeat.start())

    def stop_background_services(self) -> None:
        """停止后台服务"""
        if self.subconscious:
            self.subconscious.stop()
        if self.heartbeat:
            self.heartbeat.stop()
