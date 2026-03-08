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

from emoticorebot.core.context import ContextBuilder
from emoticorebot.core.graph import run_fusion_agent
from emoticorebot.core.model import LLMFactory
from emoticorebot.bus.events import InboundMessage, OutboundMessage
from emoticorebot.bus.queue import MessageBus
from emoticorebot.memory.memory_facade import MemoryFacade
from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.services import EQService, IQService, MemoryService, ToolManager
from emoticorebot.session.iq_context import build_expert_disagreement_summary, build_iq_context, compact_text, extract_memory_overlay_metadata
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
            session.metadata.pop("current_task_id", None)
            session.metadata.pop("tasks", None)
            session.metadata.pop("current_task_label", None)
            session.metadata.pop("current_task_updated_at", None)
            self.sessions.clear_eq_iq_messages(session.key)
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

        # 加载两条历史：
        # - `user_eq_history`：跨轮持久的用户↔EQ 外部会话
        # - `eq_iq_history`：单轮临时的 EQ↔IQ 内部会话（每轮从空开始）
        user_eq_history = session.get_history(max_messages=self.memory_window, include_iq_context=False)
        turn_metadata = self._build_turn_metadata(session)
        turn_task_id = self._prepare_turn_task_id(session, turn_metadata)

        content, final_state = await run_fusion_agent(
            user_input=msg.content,
            workspace=self.workspace,
            runtime=self,
            user_eq_history=user_eq_history,
            eq_iq_history=[],
            metadata=turn_metadata,
            channel=msg.channel,
            chat_id=msg.chat_id,
            session_id=key,
            on_progress=on_progress,
            agent=self._compiled_agent,
        )

        # 保存对话到 session
        assistant_timestamp = datetime.now().isoformat()
        full_iq_fields = self._build_full_iq_session_fields(final_state)
        resolved_task_id = self._resolve_final_task_id(
            session=session,
            user_input=msg.content,
            final_state=final_state,
            provisional_task_id=turn_task_id,
        )
        self.sessions.append_eq_iq_messages(
            key,
            self._build_eq_iq_turn_records(
                final_state,
                assistant_timestamp=assistant_timestamp,
                task_id=resolved_task_id,
            ),
        )

        user_fields = self._build_task_message_fields(resolved_task_id)
        assistant_fields = self._build_assistant_session_fields(
            final_state,
            full_fields=full_iq_fields,
            task_id=resolved_task_id,
        )
        session.add_message("user", msg.content, **user_fields)
        session.add_message(
            "assistant",
            content,
            timestamp=assistant_timestamp,
            **assistant_fields,
        )

        pending_task = self._build_pending_task_metadata(final_state)
        if pending_task:
            pending_task["task_id"] = resolved_task_id or pending_task.get("task_id", "")
            session.metadata["pending_task"] = pending_task
        else:
            session.metadata.pop("pending_task", None)

        self._update_task_session_metadata(
            session=session,
            task_id=resolved_task_id,
            final_state=final_state,
            user_input=msg.content,
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

    def _build_full_iq_session_fields(self, final_state: dict[str, Any]) -> dict[str, Any]:
        """Extract the full persisted IQ payload for audit purposes."""
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

    def _build_assistant_session_fields(
        self,
        final_state: dict[str, Any],
        *,
        full_fields: dict[str, Any] | None = None,
        task_id: str = "",
    ) -> dict[str, Any]:
        """Keep user_eq clean; internal IQ detail lives in `eq_iq.jsonl`."""
        return self._build_task_message_fields(task_id)

    def _build_eq_iq_turn_records(
        self,
        final_state: dict[str, Any],
        *,
        assistant_timestamp: str,
        task_id: str,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for item in final_state.get("eq_iq_history", []) or []:
            if not isinstance(item, dict):
                continue
            payload = dict(item)
            payload.setdefault("timestamp", assistant_timestamp)
            if task_id:
                payload["task_id"] = task_id
            records.append(payload)

        summary_event = self._build_eq_iq_summary_event(
            final_state,
            assistant_timestamp=assistant_timestamp,
            task_id=task_id,
        )
        if summary_event:
            records.append(summary_event)
        return records

    def _build_eq_iq_summary_event(
        self,
        final_state: dict[str, Any],
        *,
        assistant_timestamp: str,
        task_id: str,
    ) -> dict[str, Any] | None:
        fields = self._build_full_iq_session_fields(final_state)
        if not fields:
            return None
        eq = final_state.get("eq")
        return {
            "role": "assistant",
            "phase": "task_summary",
            "content": str(fields.get("iq_summary", "") or "").strip(),
            "timestamp": assistant_timestamp,
            "task_id": task_id,
            "task": str(fields.get("iq_task", "") or "").strip(),
            "task_continuity": str(getattr(eq, "task_continuity", "") or "") if eq is not None else "",
            "task_label": str(getattr(eq, "task_label", "") or "") if eq is not None else "",
            "iq_status": str(fields.get("iq_status", "") or "").strip(),
            "iq_confidence": float(fields.get("iq_confidence", 0.0) or 0.0),
            "iq_selected_experts": list(fields.get("iq_selected_experts", []) or []),
            "iq_tool_calls": list(fields.get("iq_tool_calls", []) or []),
            "eq_accepted_experts": list(fields.get("eq_accepted_experts", []) or []),
            "eq_rejected_experts": list(fields.get("eq_rejected_experts", []) or []),
            "eq_arbitration_summary": str(fields.get("eq_arbitration_summary", "") or "").strip(),
            "final_decision": str(getattr(eq, "final_decision", "") or "") if eq is not None else "",
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
            "task_id": str((final_state.get("metadata") or {}).get("task_id", "") or "").strip(),
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
                "task_id": str(pending_task.get("task_id", "") or "").strip(),
            }
        active_task_id = str(session.metadata.get("current_task_id", "") or "").strip()
        current_task = self._get_task_entry(session, active_task_id) if active_task_id else None
        if current_task:
            metadata["current_task"] = {
                "task_id": active_task_id,
                "task_label": str(current_task.get("task_label", "") or "").strip(),
                "updated_at": str(current_task.get("updated_at", "") or "").strip(),
                "status": str(current_task.get("status", "") or "").strip(),
            }
        elif active_task_id:
            metadata["current_task"] = {
                "task_id": active_task_id,
                "task_label": str(session.metadata.get("current_task_label", "") or "").strip(),
                "updated_at": str(session.metadata.get("current_task_updated_at", "") or "").strip(),
            }
        recent_iq_summaries: list[str] = []
        for message in reversed(self.sessions.get_eq_iq_messages(session.key, max_messages=48)):
            if str(message.get("phase", "") or "") != "task_summary":
                continue
            summary = compact_text(str(message.get("content", "") or "").strip(), limit=220)
            if not summary:
                continue
            if summary in recent_iq_summaries:
                continue
            recent_iq_summaries.append(summary)
            if len(recent_iq_summaries) >= 4:
                break
        if recent_iq_summaries:
            metadata["recent_iq_summaries"] = list(reversed(recent_iq_summaries))
        return metadata

    def _prepare_turn_task_id(self, session, metadata: dict[str, Any]) -> str:
        pending_task = metadata.get("pending_task") if isinstance(metadata.get("pending_task"), dict) else None
        pending_task_id = str((pending_task or {}).get("task_id", "") or "").strip()
        if pending_task_id:
            return pending_task_id
        return ""

    def _resolve_final_task_id(
        self,
        *,
        session,
        user_input: str,
        final_state: dict[str, Any],
        provisional_task_id: str,
    ) -> str:
        pending_task = self._build_pending_task_metadata(final_state)
        pending_task_id = str((pending_task or {}).get("task_id", "") or "").strip()
        if pending_task_id:
            return pending_task_id

        eq = final_state.get("eq")
        continuity = str(getattr(eq, "task_continuity", "") or "").strip().lower() if eq is not None else ""
        current_task_id = str(session.metadata.get("current_task_id", "") or "").strip()
        pending_session = session.metadata.get("pending_task") if isinstance(session.metadata.get("pending_task"), dict) else None
        pending_session_task_id = str((pending_session or {}).get("task_id", "") or "").strip()

        if continuity == "continue":
            return pending_session_task_id or current_task_id or provisional_task_id or self._new_task_id()
        if continuity == "new":
            return self._new_task_id()
        if continuity == "none":
            return ""

        if provisional_task_id:
            return provisional_task_id

        if not self._turn_contains_task(final_state, user_input):
            return ""

        current_task_id = str(session.metadata.get("current_task_id", "") or "").strip()
        if current_task_id and not self._looks_like_explicit_new_task(user_input):
            return current_task_id
        return self._new_task_id()

    def _update_task_session_metadata(
        self,
        *,
        session,
        task_id: str,
        final_state: dict[str, Any],
        user_input: str,
    ) -> None:
        if not task_id:
            return
        session.metadata["current_task_id"] = task_id
        task_entry = self._ensure_task_entry(session, task_id)
        task_label = self._build_task_label(final_state, user_input)
        if task_label:
            task_entry["task_label"] = task_label

        eq = final_state.get("eq")
        pending_task = session.metadata.get("pending_task") if isinstance(session.metadata.get("pending_task"), dict) else None
        final_decision = str(getattr(eq, "final_decision", "") or "").strip().lower() if eq is not None else ""
        task_entry["updated_at"] = datetime.now().isoformat()
        task_entry["status"] = "pending" if pending_task else ("completed" if final_decision == "answer" else (final_decision or "active"))
        task_entry["last_user_input"] = compact_text(str(user_input or "").strip(), limit=240)
        task_entry["last_output"] = compact_text(str(final_state.get("output", "") or "").strip(), limit=240)
        task_entry["task_continuity"] = str(getattr(eq, "task_continuity", "") or "") if eq is not None else ""

        session.metadata.pop("current_task_label", None)
        session.metadata.pop("current_task_updated_at", None)

    @staticmethod
    def _build_task_message_fields(task_id: str) -> dict[str, Any]:
        if not task_id:
            return {}
        return {"task_id": task_id}

    @staticmethod
    def _new_task_id() -> str:
        return f"task_{uuid4().hex[:12]}"

    @staticmethod
    def _looks_like_explicit_new_task(text: str) -> bool:
        normalized = str(text or "").strip().lower()
        if not normalized:
            return False
        markers = ["新任务", "另外", "另一个", "换个", "重新来", "再来一个", "顺便再", "新增一个"]
        return any(marker in normalized for marker in markers)

    @staticmethod
    def _looks_task_like(text: str) -> bool:
        normalized = str(text or "").strip()
        if not normalized:
            return False
        if len(normalized) >= 18:
            return True
        keywords = [
            "帮我", "请", "分析", "整理", "导出", "生成", "创建", "修改", "比较", "查", "搜索", "运行", "执行",
            "写", "做", "统计", "汇总", "修复", "继续", "补充", "恢复", "导入", "导出",
        ]
        return any(token in normalized for token in keywords)

    def _turn_contains_task(self, final_state: dict[str, Any], user_input: str) -> bool:
        iq = final_state.get("iq")
        eq = final_state.get("eq")
        if iq is not None and str(getattr(iq, "task", "") or "").strip():
            return True
        if final_state.get("eq_iq_history"):
            return True
        if eq is not None and str(getattr(eq, "final_decision", "") or "").strip() == "ask_user":
            return True
        return self._looks_task_like(user_input)

    @staticmethod
    def _build_task_label(final_state: dict[str, Any], user_input: str) -> str:
        eq = final_state.get("eq")
        if eq is not None:
            task_label = compact_text(str(getattr(eq, "task_label", "") or "").strip(), limit=120)
            if task_label:
                return task_label
        iq = final_state.get("iq")
        if iq is not None:
            task = compact_text(str(getattr(iq, "task", "") or "").strip(), limit=120)
            if task:
                return task
        return compact_text(str(user_input or "").strip(), limit=120)

    @staticmethod
    def _get_task_registry(session) -> dict[str, dict[str, Any]]:
        tasks = session.metadata.get("tasks")
        if not isinstance(tasks, dict):
            tasks = {}
            session.metadata["tasks"] = tasks
        return tasks

    def _get_task_entry(self, session, task_id: str) -> dict[str, Any] | None:
        if not task_id:
            return None
        tasks = self._get_task_registry(session)
        entry = tasks.get(task_id)
        return entry if isinstance(entry, dict) else None

    def _ensure_task_entry(self, session, task_id: str) -> dict[str, Any]:
        tasks = self._get_task_registry(session)
        entry = tasks.get(task_id)
        if not isinstance(entry, dict):
            entry = {"task_id": task_id}
            tasks[task_id] = entry
        return entry

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
        user_eq_history: list[dict[str, Any]],
        emotion: str,
        pad: dict[str, float],
        pending_task: dict[str, Any] | None,
        current_task: dict[str, Any] | None,
        internal_iq_summaries: list[str] | None = None,
    ) -> dict[str, Any]:
        return await self.eq_service.deliberate(
            user_input=user_input,
            history=user_eq_history,
            emotion=emotion,
            pad=pad,
            pending_task=pending_task,
            current_task=current_task,
            internal_iq_summaries=internal_iq_summaries,
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
