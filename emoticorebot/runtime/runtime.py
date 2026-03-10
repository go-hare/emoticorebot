"""Runtime - 使用服务类架构

精简后的 Runtime，职责：
1. 消息调度（接收消息、分发处理）
2. 显式 turn loop 编排（`main_brain -> executor`）
3. 会话管理（加载/保存 `dialogue` 与 `internal`）
4. 反思调度（每轮 `turn_reflection`，按需 / 周期 `deep_reflection`）
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from emoticorebot.bus.events import InboundMessage, OutboundMessage
from emoticorebot.bus.queue import MessageBus
from emoticorebot.config.schema import MemoryConfig, ModelModeConfig, ProvidersConfig
from emoticorebot.core.context import ContextBuilder
from emoticorebot.core.model import LLMFactory
from emoticorebot.core.turn_loop import run_turn_loop
from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.runtime.execution_control import RuntimeExecutionControlMixin
from emoticorebot.runtime.turn_persistence import RuntimeTurnPersistenceMixin
from emoticorebot.services import ExecutorService, MainBrainService, MemoryService, ToolManager
from emoticorebot.session.manager import SessionManager

if TYPE_CHECKING:
    from emoticorebot.config.schema import ChannelsConfig, ExecToolConfig
    from emoticorebot.core.state import (
        ExecutorResultPacket,
        MainBrainDeliberationPacket,
        MainBrainFinalizePacket,
    )
    from emoticorebot.cron.service import CronService


class EmoticoreRuntime(RuntimeExecutionControlMixin, RuntimeTurnPersistenceMixin):
    """精简的 Runtime - 使用服务类架构"""

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
        memory_config: "MemoryConfig | None" = None,
    ):
        from emoticorebot.config.schema import ExecToolConfig

        self.bus = bus
        self.workspace = workspace
        self.executor_mode = executor_mode
        self.main_brain_mode = main_brain_mode
        self.memory_window = executor_mode.memory_window
        self.channels_config = channels_config

        self.sessions = session_manager or SessionManager(workspace)
        self.emotion_mgr = EmotionStateManager(workspace)
        self.context = ContextBuilder(
            workspace,
            memory_config=memory_config,
            providers_config=providers_config,
        )

        factory = LLMFactory(
            providers_config=providers_config,
            executor_mode=executor_mode,
            main_brain_mode=main_brain_mode,
        )
        self.executor_llm = factory.get_executor()
        self.main_brain_llm = factory.get_main_brain()

        self.main_brain_service = MainBrainService(self.main_brain_llm, self.context)
        self.executor_service = ExecutorService(self.executor_llm, None, self.context)
        self.memory_service = MemoryService(
            workspace,
            self.emotion_mgr,
            self.sessions,
            executor_mode.memory_window,
            reflection_llm=self.main_brain_llm,
            deep_reflection_decider=self.main_brain_service.decide_deep_reflection,
            memory_config=memory_config,
            providers_config=providers_config,
        )
        self.tool_manager = ToolManager(
            workspace,
            exec_config or ExecToolConfig(),
            bus,
            cron_service,
            brave_api_key,
            restrict_to_workspace,
        )

        self.tool_manager.register_default_tools()
        self.executor_service.tools = self.tool_manager.get_registry()

        self._mcp_servers = mcp_servers or {}

        self._running = False
        self._active_tasks: dict[str, list[asyncio.Task]] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._reflection_locks: dict[str, asyncio.Lock] = {}
        self._reflection_tasks: dict[str, list[asyncio.Task]] = {}
        self._deep_reflection_lock = asyncio.Lock()

        self.subconscious = None
        self.heartbeat = None

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
                lambda current_task, key=msg.session_key: (
                    self._active_tasks.get(key, [])
                    and current_task in self._active_tasks[key]
                    and self._active_tasks[key].remove(current_task)
                )
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

        self.tool_manager.set_context(
            msg.channel,
            msg.chat_id,
            msg.metadata.get("message_id"),
            key,
        )

        dialogue_history = session.get_history(max_messages=self.memory_window, include_executor_context=False)
        internal_history = self.sessions.get_internal_messages(key, max_messages=self.memory_window)
        message_id = str(msg.metadata.get("message_id", "") or "").strip() or self._new_message_id()
        msg.metadata["message_id"] = message_id
        turn_metadata = self._build_turn_metadata(session=session, user_input=msg.content, message_id=message_id)

        content, final_state = await run_turn_loop(
            user_input=msg.content,
            workspace=self.workspace,
            runtime=self,
            dialogue_history=dialogue_history,
            internal_history=internal_history,
            metadata=turn_metadata,
            channel=msg.channel,
            chat_id=msg.chat_id,
            session_id=key,
            media=msg.media,
            on_progress=on_progress,
        )

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
        self._schedule_turn_reflection(session_key=key, state=final_state)
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=content,
            metadata=msg.metadata or {},
        )

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

    def main_brain_build_executor_delegation(self, **kwargs):
        return self.main_brain_service.build_executor_delegation(**kwargs)

    async def run_executor_request(self, **kwargs) -> "ExecutorResultPacket":
        return await self.executor_service.run_request(**kwargs)

    async def write_turn_reflection(self, state: dict):
        return await self.memory_service.write_turn_reflection(state)

    async def run_deep_reflection(self, *, reason: str = "", warm_limit: int = 15):
        async with self._deep_reflection_lock:
            return await self.memory_service.run_deep_reflection(reason=reason, warm_limit=warm_limit)

    def _schedule_turn_reflection(self, *, session_key: str, state: dict[str, Any]) -> None:
        lock = self._reflection_locks.setdefault(session_key, asyncio.Lock())
        task = asyncio.create_task(
            self._run_turn_reflection(session_key=session_key, state=dict(state), lock=lock),
            name=f"reflection:{session_key}",
        )
        self._reflection_tasks.setdefault(session_key, []).append(task)

        def _cleanup(done_task: asyncio.Task, key: str = session_key) -> None:
            tasks = self._reflection_tasks.get(key, [])
            if done_task in tasks:
                tasks.remove(done_task)
            if not tasks:
                self._reflection_tasks.pop(key, None)

        task.add_done_callback(_cleanup)

    async def _run_turn_reflection(
        self,
        *,
        session_key: str,
        state: dict[str, Any],
        lock: asyncio.Lock,
    ) -> None:
        try:
            async with lock:
                result = await self.write_turn_reflection(state)
                if result and getattr(result, "should_run_deep_reflection", False):
                    await self.run_deep_reflection(
                        reason=str(getattr(result, "deep_reflection_reason", "") or ""),
                    )
        except Exception as exc:
            logger.warning("Turn reflection failed for {}: {}", session_key, exc)

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
