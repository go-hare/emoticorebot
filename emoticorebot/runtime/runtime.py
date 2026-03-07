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
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from emoticorebot.core.context import ContextBuilder
from emoticorebot.core.graph import run_fusion_agent
from emoticorebot.core.model import LLMFactory
from emoticorebot.bus.events import InboundMessage, OutboundMessage
from emoticorebot.bus.queue import MessageBus
from emoticorebot.memory.memory_facade import MemoryFacade
from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.services import EQService, IQService, MemoryService, ToolManager
from emoticorebot.session.iq_context import build_expert_disagreement_summary, build_iq_context, extract_memory_overlay_metadata
from emoticorebot.session.manager import SessionManager
from emoticorebot.config.schema import ModelModeConfig
from emoticorebot.config.schema import ProvidersConfig
if TYPE_CHECKING:
    from emoticorebot.config.schema import AgentDefaults, ChannelsConfig, ExecToolConfig
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
        from emoticorebot.config.schema import ExecToolConfig as _ETC

        self.bus = bus
        self.workspace = workspace
        self.iq_mode = iq_mode
        self.eq_mode = eq_mode
        self.memory_window = iq_mode.memory_window
        self.channels_config = channels_config

        # 核心组件
        self.sessions = session_manager or SessionManager(workspace)
        self.emotion_mgr = EmotionStateManager(workspace)
        self.memory_facade = MemoryFacade(workspace)
        # 将 memory_facade 注入 ContextBuilder，避免创建两份独立实例
        self.context = ContextBuilder(workspace, memory_facade=self.memory_facade)

        # LLM 实例 - 通过 LLMFactory 按配置构建
        _factory = LLMFactory(
            providers_config=providers_config,
            iq_mode=iq_mode,
            eq_mode=eq_mode,
        )
        self.iq_llm = _factory.get_iq()
        self.eq_llm = _factory.get_eq()

        # 服务类（单一职责）
        self.eq_service = EQService(self.eq_llm, self.context)
        self.iq_service = IQService(self.iq_llm, None, self.context, iq_mode.max_tool_iterations)  # registry 稍后注入
        self.memory_service = MemoryService(
            workspace, self.memory_facade, self.emotion_mgr, self.sessions, iq_mode.memory_window,
            iq_llm=self.iq_llm,
        )
        self.tool_manager = ToolManager(
            workspace,
            exec_config or _ETC(),
            bus,
            cron_service,
            brave_api_key,
            restrict_to_workspace,
        )

        # 注册默认工具，注入 registry 到 IQ Service
        self.tool_manager.register_default_tools()
        self.iq_service.tools = self.tool_manager.get_registry()

        # SubagentManager（初始化后注册 spawn 工具）
        self.subagents = None
        self._initialize_subagent_manager()

        # MCP 服务器配置
        self._mcp_servers = mcp_servers or {}

        # 预编译 LangGraph agent（每个 Runtime 实例只编译一次）
        from emoticorebot.core.graph import create_fusion_agent
        self._compiled_agent = create_fusion_agent(self.workspace, runtime=self)

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
            session.metadata.pop("pending_task", None)
            self.sessions.save(session)
            self.sessions.invalidate(session.key)
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="New session started. ✨")
        if cmd == "/help":
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="🐾 emoticorebot commands:\n/new  — Start a new conversation\n/stop — Stop the current task\n/help — Show available commands",
            )

        # 设置工具上下文
        self.tool_manager.set_context(
            msg.channel, msg.chat_id, msg.metadata.get("message_id"), key
        )

        # 加载历史（包含上一轮 IQ 的紧凑摘要，供 EQ / IQ 共用）
        history = session.get_history(max_messages=self.memory_window, include_iq_context=True)
        turn_metadata = self._build_turn_metadata(session)

        content, final_state = await run_fusion_agent(
            user_input=msg.content,
            workspace=self.workspace,
            runtime=self,
            history=history,
            metadata=turn_metadata,
            channel=msg.channel,
            chat_id=msg.chat_id,
            session_id=key,
            on_progress=on_progress,
            agent=self._compiled_agent,
        )

        # 保存对话到 session
        session.add_message("user", msg.content)
        session.add_message(
            "assistant",
            content,
            **self._build_assistant_session_fields(final_state),
        )

        pending_task = self._build_pending_task_metadata(final_state)
        if pending_task:
            session.metadata["pending_task"] = pending_task
        else:
            session.metadata.pop("pending_task", None)

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

    def _build_iq_summary(self, final_state: dict[str, Any]) -> str:
        """Build a compact persisted summary from the current turn's IQ execution."""
        iq = final_state.get("iq")
        if iq is None:
            return ""
        eq = final_state.get("eq")
        expert_packets = [packet for packet in getattr(iq, "expert_packets", []) if isinstance(packet, dict)]
        memory_overlay = extract_memory_overlay_metadata(expert_packets)
        return build_iq_context(
            {
                "iq_task": getattr(iq, "task", ""),
                "iq_status": getattr(iq, "status", ""),
                "iq_analysis": getattr(iq, "analysis", ""),
                "iq_recommended_action": getattr(iq, "recommended_action", ""),
                "iq_selected_experts": list(getattr(iq, "selected_experts", []) or []),
                "iq_expert_packets": expert_packets,
                "iq_disagreement_summary": build_expert_disagreement_summary(expert_packets),
                "iq_memory_overlay_kind": memory_overlay.get("kind", ""),
                "iq_memory_resume_task": memory_overlay.get("resume_task", ""),
                "iq_memory_overlay_summary": memory_overlay.get("summary", ""),
                "iq_confidence": float(getattr(iq, "confidence", 0.0) or 0.0),
                "iq_rationale_summary": getattr(iq, "rationale_summary", ""),
                "iq_error": getattr(iq, "error", ""),
                "iq_missing_params": list(getattr(iq, "missing_params", []) or []),
                "iq_tool_calls": list(getattr(iq, "tool_calls", []) or []),
                "eq_accepted_experts": list(getattr(eq, "accepted_experts", []) or []) if eq is not None else [],
                "eq_rejected_experts": list(getattr(eq, "rejected_experts", []) or []) if eq is not None else [],
                "eq_arbitration_summary": getattr(eq, "arbitration_summary", "") if eq is not None else "",
            }
        )

    def _build_assistant_session_fields(self, final_state: dict[str, Any]) -> dict[str, Any]:
        """Extract assistant-side internal metadata that should survive across turns."""
        iq = final_state.get("iq")
        if iq is None:
            return {}
        eq = final_state.get("eq")

        expert_packets = [packet for packet in getattr(iq, "expert_packets", []) if isinstance(packet, dict)]

        return {
            "iq_task": getattr(iq, "task", ""),
            "iq_status": getattr(iq, "status", ""),
            "iq_analysis": getattr(iq, "analysis", ""),
            "iq_recommended_action": getattr(iq, "recommended_action", ""),
            "iq_selected_experts": list(getattr(iq, "selected_experts", []) or []),
            "iq_expert_packets": list(getattr(iq, "expert_packets", []) or []),
            "iq_confidence": float(getattr(iq, "confidence", 0.0) or 0.0),
            "iq_rationale_summary": getattr(iq, "rationale_summary", ""),
            "iq_error": getattr(iq, "error", ""),
            "iq_missing_params": list(getattr(iq, "missing_params", []) or []),
            "iq_tool_calls": list(getattr(iq, "tool_calls", []) or []),
            "eq_accepted_experts": list(getattr(eq, "accepted_experts", []) or []) if eq is not None else [],
            "eq_rejected_experts": list(getattr(eq, "rejected_experts", []) or []) if eq is not None else [],
            "eq_arbitration_summary": getattr(eq, "arbitration_summary", "") if eq is not None else "",
            "iq_summary": self._build_iq_summary(final_state),
        }

    def _build_pending_task_metadata(self, final_state: dict[str, Any]) -> dict[str, Any] | None:
        """Build session metadata for an unfinished task awaiting user input."""
        iq = final_state.get("iq")
        if iq is None:
            return None

        eq = final_state.get("eq")
        decision = str(getattr(eq, "final_decision", "") or "").strip().lower()
        status = str(getattr(iq, "status", "") or "").strip().lower()
        missing_params = [
            str(item).strip()
            for item in (getattr(iq, "missing_params", []) or [])
            if str(item).strip()
        ]
        if decision != "ask_user" and status != "needs_input" and not missing_params:
            return None

        task = str(getattr(iq, "task", "") or "").strip()
        if not task:
            return None

        return {
            "task": task,
            "missing_params": missing_params,
            "prompt": str(final_state.get("output", "") or getattr(iq, "analysis", "") or getattr(iq, "error", "")).strip(),
        }

    def _build_turn_metadata(self, session) -> dict[str, Any]:
        """Build per-turn graph metadata, including pending IQ resume context."""
        metadata: dict[str, Any] = {}
        pending_task = session.metadata.get("pending_task")
        if isinstance(pending_task, dict) and str(pending_task.get("task", "")).strip():
            missing_params = [
                str(item).strip()
                for item in (pending_task.get("missing_params") or [])
                if str(item).strip()
            ]
            metadata["pending_task"] = {
                "task": str(pending_task.get("task", "")).strip(),
                "missing_params": missing_params,
                "prompt": str(pending_task.get("prompt", "")).strip(),
            }
        return metadata

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

    async def eq_deliberate(
        self,
        *,
        user_input: str,
        history: list[dict[str, Any]],
        emotion: str,
        pad: dict[str, float],
        pending_task: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return await self.eq_service.deliberate(
            user_input=user_input,
            history=history,
            emotion=emotion,
            pad=pad,
            pending_task=pending_task,
        )

    async def eq_finalize(self, **kwargs) -> dict[str, Any]:
        return await self.eq_service.finalize(**kwargs)

    async def run_iq_task(self, **kwargs) -> dict:
        return await self.iq_service.run_task(**kwargs)

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
                    content=f"请处理以下任务：{tasks}",
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
