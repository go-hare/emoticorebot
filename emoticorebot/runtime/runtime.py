"""Fusion Runtime - 使用服务类架构

精简后的 Runtime，职责：
1. 消息调度（接收消息、分发处理）
2. 服务编排（协调各个服务完成任务）
3. 会话管理（加载/保存 session）
4. 策略生成（信号提取 → 策略参数）

原 878 行代码 → 精简到 ~300 行
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable
from uuid import uuid4

from loguru import logger

from emoticorebot.bus.events import InboundMessage, OutboundMessage
from emoticorebot.bus.queue import MessageBus
from emoticorebot.config.schema import ModelModeConfig, ProvidersConfig
from emoticorebot.core.context import ContextBuilder
from emoticorebot.core.graph import run_orchestration_agent
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


class FusionRuntime:
    """精简的 Fusion Runtime - 使用服务类架构

    职责：消息调度 + 服务编排
    """

    def __init__(
        self,
        bus: MessageBus,
        workspace: Path,
        iq_mode: "ModelModeConfig",
        eq_mode: "ModelModeConfig",
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
        self.iq_mode = iq_mode
        self.eq_mode = eq_mode
        self.memory_window = iq_mode.memory_window
        self.channels_config = channels_config

        # 核心组件
        self.sessions = session_manager or SessionManager(workspace)
        self.emotion_mgr = EmotionStateManager(workspace)
        self.context = ContextBuilder(workspace)

        # LLM 实例 - 通过 LLMFactory 按配置构建
        _factory = LLMFactory(
            providers_config=providers_config,
            iq_mode=iq_mode,
            eq_mode=eq_mode,
        )
        self.iq_llm = _factory.get_iq()
        self.eq_llm = _factory.get_eq()

        # 服务类（单一职责）
        self.main_brain_service = MainBrainService(self.eq_llm, self.context)
        self.executor_service = ExecutorService(self.iq_llm, None, self.context)  # registry injected later
        self.memory_service = MemoryService(
            workspace, self.emotion_mgr, self.sessions, iq_mode.memory_window,
            iq_llm=self.iq_llm,
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
        from emoticorebot.core.graph import create_orchestration_agent
        self._compiled_agent = create_orchestration_agent(self.workspace, runtime=self)

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
            iq_llm=self.iq_llm,
            eq_llm=self.eq_llm,
            brave_api_key=self.tool_manager.brave_api_key,
            exec_config=self.tool_manager.exec_config,
            restrict_to_workspace=self.tool_manager.restrict_to_workspace,
        )

        self.tool_manager.register_spawn_tool(self.subagents)
        logger.debug("SubagentManager initialized")

    async def run(self) -> None:
        """主循环：接收消息并调度"""
        self._running = True
        await self.tool_manager.connect_mcp_servers(self._mcp_servers)
        logger.info("Fusion runtime started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if msg.content.strip().lower() == "/stop":
                await self._handle_stop(msg)
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
        tasks = self._active_tasks.pop(msg.session_key, [])
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

        subagent_cancelled = 0
        if self.subagents:
            subagent_cancelled = await self.subagents.cancel_by_session(msg.session_key)

        message = f"⏹ Stopped {cancelled} main task(s)"
        if subagent_cancelled > 0:
            message += f" and {subagent_cancelled} subagent(s)"
        message += "."

        await self.bus.publish_outbound(
            OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=message)
        )

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
                content="🐾 emoticorebot commands:\n/new  — Start a new conversation\n/stop — Stop the current request\n/help — Show available commands",
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
        turn_metadata = {"message_id": message_id}

        content, final_state = await run_orchestration_agent(
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
            agent=self._compiled_agent,
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
                "executor_status": getattr(executor, "status", ""),
                "executor_analysis": getattr(executor, "analysis", ""),
                "executor_recommended_action": getattr(executor, "recommended_action", ""),
                "executor_confidence": float(getattr(executor, "confidence", 0.0) or 0.0),
                "executor_missing_params": list(getattr(executor, "missing_params", []) or []),
            }
        )

    def _build_assistant_session_fields(
        self,
        final_state: dict[str, Any],
    ) -> dict[str, Any]:
        main_brain = final_state.get("main_brain")
        executor = final_state.get("executor")
        if main_brain is None:
            return {}
        executor_summary = self._build_executor_summary(final_state)
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
            **{
                key: value
                for key, value in {
                    "executor_summary": executor_summary,
                    "executor_status": str(getattr(executor, "status", "") or "") if executor is not None else "",
                    "executor_analysis": str(getattr(executor, "analysis", "") or "") if executor is not None else "",
                    "executor_recommended_action": (
                        str(getattr(executor, "recommended_action", "") or "") if executor is not None else ""
                    ),
                    "executor_confidence": float(getattr(executor, "confidence", 0.0) or 0.0)
                    if executor is not None
                    else 0.0,
                    "executor_missing_params": list(getattr(executor, "missing_params", []) or [])
                    if executor is not None
                    else [],
                }.items()
                if value not in ("", 0, [], None)
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
        records: list[dict[str, Any]] = []
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
        records.sort(key=lambda item: (str(item.get("timestamp", "") or assistant_timestamp), item.get("role", "")))
        return records

    def _build_user_message_content(self, content: str, media: list[str] | None) -> list[dict[str, Any]]:
        media_items = self.context.build_media_context(media)
        return [{"type": "text", "text": str(content or "")}, *media_items]

    @staticmethod
    def _new_message_id() -> str:
        return f"msg_{uuid4().hex[:16]}"

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

    # ── EQ 主导接口 ────────────────────────────────────────────

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
