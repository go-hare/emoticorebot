"""Runtime - 使用 agent 分层架构

精简后的 Runtime，职责：
1. 消息调度（接收消息、分发处理）
2. 直接调用主脑，并把任务交给任务系统
3. 会话管理（加载/保存 `dialogue` 与 `internal`）
4. 反思调度（每轮 `turn_reflection`，按需 / 周期 `deep_reflection`）
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable
from uuid import uuid4

from loguru import logger

from emoticorebot.agent.brain import BrainService
from emoticorebot.agent.reflection import MemoryService, ReflectionCoordinator
from emoticorebot.agent.system import SessionTaskSystem
from emoticorebot.agent.tool import ToolManager
from emoticorebot.config.schema import MemoryConfig, ModelModeConfig, ProvidersConfig
from emoticorebot.agent.context import ContextBuilder
from emoticorebot.agent.model import LLMFactory
from emoticorebot.agent.brain_types import BrainControlPacket
from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.runtime.event_bus import InboundMessage, OutboundMessage, RuntimeEventBus
from emoticorebot.session.manager import SessionManager

if TYPE_CHECKING:
    from emoticorebot.config.schema import ChannelsConfig, ExecToolConfig
    from emoticorebot.cron.service import CronService


class EmoticoreRuntime:
    """精简的 Runtime - 使用 agent 分层架构"""

    def __init__(
        self,
        bus: RuntimeEventBus,
        workspace: Path,
        central_mode: "ModelModeConfig",
        brain_mode: "ModelModeConfig",
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
        self.central_mode = central_mode
        self.brain_mode = brain_mode
        self.memory_window = central_mode.memory_window
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
            central_mode=central_mode,
            brain_mode=brain_mode,
        )
        self.central_llm = factory.get_central()
        self.brain_llm = factory.get_brain()

        self.brain_service = BrainService(self.brain_llm, self.context, bus=self.bus)
        self.memory_service = MemoryService(
            workspace,
            memory_config=memory_config,
            providers_config=providers_config,
        )
        self.reflection_coordinator = ReflectionCoordinator(
            workspace,
            self.emotion_mgr,
            self.memory_service,
            reflection_llm=self.brain_llm,
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

        self._mcp_servers = mcp_servers or {}

        self._running = False
        self._dispatch_tasks: set[asyncio.Task] = set()
        self._task_systems: dict[str, SessionTaskSystem] = {}
        self._task_consumers: dict[str, asyncio.Task] = {}
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

            task = asyncio.create_task(self._dispatch(msg))
            self._dispatch_tasks.add(task)
            task.add_done_callback(self._dispatch_tasks.discard)

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

        dialogue_history = session.get_history(max_messages=self.memory_window, include_task_context=False)
        internal_history = self.sessions.get_internal_messages(key, max_messages=self.memory_window)
        message_id = str(msg.metadata.get("message_id", "") or "").strip() or self._new_message_id()
        msg.metadata["message_id"] = message_id
        turn_metadata = self._build_turn_metadata(session=session, user_input=msg.content, message_id=message_id)

        content, final_state = await self._run_user_message(
            user_input=msg.content,
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
        target_file = self.workspace / "subconscious_target.json"
        try:
            target_file.write_text(
                json.dumps({"channel": channel, "chat_id": chat_id}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    @staticmethod
    def _load_pad_from_workspace(workspace: Path) -> dict[str, float]:
        state_file = workspace / "current_state.md"
        pad = {"pleasure": 0.0, "arousal": 0.5, "dominance": 0.5}
        if not state_file.exists():
            return pad
        try:
            text = state_file.read_text(encoding="utf-8")
            pleasure = re.search(r"Pleasure[^|]*\|\s*([-\d.]+)", text, re.IGNORECASE)
            arousal = re.search(r"Arousal[^|]*\|\s*([-\d.]+)", text, re.IGNORECASE)
            dominance = re.search(r"Dominance[^|]*\|\s*([-\d.]+)", text, re.IGNORECASE)
            if pleasure:
                pad["pleasure"] = max(-1.0, min(1.0, float(pleasure.group(1))))
            if arousal:
                pad["arousal"] = max(-1.0, min(1.0, float(arousal.group(1))))
            if dominance:
                pad["dominance"] = max(-1.0, min(1.0, float(dominance.group(1))))
        except Exception:
            return {"pleasure": 0.0, "arousal": 0.5, "dominance": 0.5}
        return pad

    @staticmethod
    def _get_emotion_label(pad: dict[str, float]) -> str:
        pleasure = float(pad.get("pleasure", 0.0))
        arousal = float(pad.get("arousal", 0.5))
        if pleasure < -0.5:
            return "难过" if arousal < 0.3 else "生气"
        if pleasure > 0.5:
            return "兴奋" if arousal > 0.7 else "开心"
        if arousal < 0.2:
            return "低落"
        return "平静"

    async def _run_user_message(
        self,
        *,
        user_input: str,
        dialogue_history: list[dict[str, Any]],
        internal_history: list[dict[str, Any]],
        metadata: dict[str, Any] | None,
        channel: str,
        chat_id: str,
        session_id: str,
        media: list[str] | None,
        on_progress: Callable[..., Awaitable[None]] | None,
    ) -> tuple[str, dict[str, Any]]:
        pad = self._load_pad_from_workspace(self.workspace)
        emotion = self._get_emotion_label(pad)
        final_state: dict[str, Any] = {
            "user_input": user_input,
            "dialogue_history": dialogue_history,
            "internal_history": internal_history,
            "metadata": dict(metadata or {}),
            "media": list(media or []),
            "workspace": str(self.workspace),
            "session_id": session_id,
            "channel": channel,
            "chat_id": chat_id,
            "done": False,
            "output": "",
        }
        if on_progress is not None:
            final_state["on_progress"] = on_progress

        task_system = self._get_task_system(session_id)
        self._ensure_task_consumer(
            session_id=session_id,
            channel=channel,
            chat_id=chat_id,
            message_id=str((final_state.get("metadata") or {}).get("message_id", "") or ""),
        )
        brain_result = await self.brain_service.handle_user_message(
            user_input=user_input,
            history=dialogue_history,
            emotion=emotion,
            pad=pad,
            task_system=task_system,
            message_id=str((final_state.get("metadata") or {}).get("message_id", "") or ""),
            channel=channel,
            chat_id=chat_id,
            session_id=session_id,
        )

        final_state["brain"] = self.brain_service.build_runtime_brain_snapshot(
            control=brain_result,
            emotion=emotion,
            pad=pad,
            default_query=user_input,
        )

        message = str(brain_result.get("message", "") or "").strip()
        if not message:
            message = str(getattr(final_state.get("brain"), "final_message", "") or "").strip() or "我先处理这件事。"

        final_state["output"] = message
        final_state["done"] = True
        return message, final_state

    def _build_turn_metadata(self, *, session, user_input: str, message_id: str) -> dict[str, Any]:
        del session, user_input
        return {"message_id": message_id}

    def _build_internal_turn_records(
        self,
        final_state: dict[str, Any],
        *,
        assistant_timestamp: str,
        message_id: str,
        existing_internal_count: int = 0,
    ) -> list[dict[str, Any]]:
        del existing_internal_count
        brain = final_state.get("brain")
        if brain is None:
            return []
        brain_payload = {
            "intent": str(getattr(brain, "intent", "") or "").strip(),
            "working_hypothesis": str(getattr(brain, "working_hypothesis", "") or "").strip(),
            "task_brief": str(getattr(brain, "task_brief", "") or "").strip(),
            "final_decision": str(getattr(brain, "final_decision", "") or "").strip(),
            "final_message": str(getattr(brain, "final_message", "") or "").strip(),
            "task_action": str(getattr(brain, "task_action", "") or "").strip(),
            "task_reason": str(getattr(brain, "task_reason", "") or "").strip(),
        }
        brain_payload = {key: value for key, value in brain_payload.items() if value}
        if not brain_payload:
            return []
        return [
            {
                "message_id": message_id,
                "role": "assistant",
                "phase": "brain",
                "event": "brain.turn.summary",
                "source": "runtime",
                "content": json.dumps(brain_payload, ensure_ascii=False),
                "brain": brain_payload,
                "timestamp": assistant_timestamp,
            }
        ]

    def _build_user_message_content(self, content: str, media: list[str] | None) -> list[dict[str, Any]]:
        media_items = self.context.build_media_context(media)
        return [{"type": "text", "text": str(content or "")}, *media_items]

    @staticmethod
    def _new_message_id() -> str:
        return f"msg_{uuid4().hex[:16]}"

    def _build_assistant_session_fields(self, final_state: dict[str, Any]) -> dict[str, Any]:
        brain = final_state.get("brain")
        fields: dict[str, Any] = {}
        if brain is not None:
            for key in ("model_name", "prompt_tokens", "completion_tokens", "total_tokens"):
                value = getattr(brain, key, None)
                if value not in (None, "", 0):
                    fields[key] = value

        task_state = final_state.get("task")
        metadata = final_state.get("metadata") if isinstance(final_state.get("metadata"), dict) else {}
        task_payload = metadata.get("task") if isinstance(metadata.get("task"), dict) else None
        if task_payload is None and task_state is not None:
            task_payload = {
                "invoked": bool(str(getattr(task_state, "task_id", "") or "").strip()),
                "task_id": str(getattr(task_state, "task_id", "") or "").strip(),
                "title": str(getattr(task_state, "title", "") or "").strip(),
                "goal": str(getattr(task_state, "goal", "") or "").strip(),
                "thread_id": str(getattr(task_state, "thread_id", "") or "").strip(),
                "run_id": str(getattr(task_state, "run_id", "") or "").strip(),
                "control_state": str(getattr(task_state, "control_state", "") or "").strip(),
                "status": str(getattr(task_state, "status", "") or "").strip(),
                "summary": str(getattr(task_state, "analysis", "") or "").strip(),
                "recommended_action": str(getattr(task_state, "recommended_action", "") or "").strip(),
                "confidence": float(getattr(task_state, "confidence", 0.0) or 0.0),
                "missing": list(getattr(task_state, "missing", []) or []),
                "pending_review": dict(getattr(task_state, "pending_review", {}) or {}),
            }
        if isinstance(task_payload, dict) and task_payload:
            cleaned = {key: value for key, value in task_payload.items() if value not in ("", [], {}, None, False)}
            if cleaned:
                fields["task"] = cleaned
        return fields

    def _get_task_system(self, session_id: str) -> SessionTaskSystem:
        key = str(session_id or "__default__").strip() or "__default__"
        system = self._task_systems.get(key)
        if system is None:
            system = SessionTaskSystem(
                central_llm=self.central_llm,
                context_builder=self.context,
                tool_registry=self.tool_manager.get_registry(),
            )
            self._task_systems[key] = system
        return system

    def _ensure_task_consumer(
        self,
        *,
        session_id: str,
        channel: str,
        chat_id: str,
        message_id: str,
    ) -> None:
        key = str(session_id or "__default__").strip() or "__default__"
        existing = self._task_consumers.get(key)
        if existing is not None and not existing.done():
            return

        task = asyncio.create_task(
            self._consume_task_events(
                session_id=key,
                channel=channel,
                chat_id=chat_id,
                message_id=message_id,
            ),
            name=f"task-consumer:{key}",
        )
        self._task_consumers[key] = task

        def _cleanup(done_task: asyncio.Task, consumer_key: str = key) -> None:
            current = self._task_consumers.get(consumer_key)
            if current is done_task:
                self._task_consumers.pop(consumer_key, None)

        task.add_done_callback(_cleanup)

    async def _consume_task_events(
        self,
        *,
        session_id: str,
        channel: str,
        chat_id: str,
        message_id: str,
    ) -> None:
        system = self._get_task_system(session_id)
        while True:
            event = await system.to_main_queue.get()
            try:
                session = self.sessions.get(session_id)
                history = (
                    session.get_history(max_messages=self.memory_window, include_task_context=False)
                    if session is not None
                    else []
                )
                pad = {
                    "pleasure": float(self.emotion_mgr.pad.pleasure),
                    "arousal": float(self.emotion_mgr.pad.arousal),
                    "dominance": float(self.emotion_mgr.pad.dominance),
                }
                control = await self.brain_service.handle_task_event(
                    event=event,
                    history=history,
                    emotion=self.emotion_mgr.get_emotion_label(),
                    pad=pad,
                    message_id=message_id,
                    channel=channel,
                    chat_id=chat_id,
                    session_id=session_id,
                )
                content = str(control.get("message", "") or "").strip()
                if not content:
                    continue
                assistant_message_id = self._new_message_id()

                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=channel,
                        chat_id=chat_id,
                        content=content,
                        reply_to=message_id or None,
                        metadata={
                            "task_id": str(event.get("task_id", "") or "").strip(),
                            "task_event": str(event.get("type", "") or "").strip(),
                            "producer": "task_system",
                        },
                    )
                )

                if session is not None:
                    assistant_timestamp = datetime.now().isoformat()
                    event_type = str(event.get("type", "") or "").strip().lower()
                    task_status = "running"
                    task_control_state = "running"
                    if event_type == "need_input":
                        task_status = "waiting_input"
                        task_control_state = "waiting_input"
                    elif event_type == "done":
                        task_status = "done"
                        task_control_state = "completed"
                    elif event_type == "failed":
                        task_status = "failed"
                        task_control_state = "failed"
                    task_snapshot = {
                        "invoked": True,
                        "task_id": str(event.get("task_id", "") or "").strip(),
                        "control_state": task_control_state,
                        "status": task_status,
                        "summary": str(event.get("summary", "") or event.get("message", "") or "").strip(),
                        "missing": [
                            str(item).strip()
                            for item in list(
                                event.get("missing", [])
                                or ([event.get("field")] if event.get("field") else [])
                            )
                            if str(item).strip()
                        ],
                    }
                    session.add_message(
                        "assistant",
                        [{"type": "text", "text": content}],
                        message_id=assistant_message_id,
                        timestamp=assistant_timestamp,
                        task=task_snapshot,
                    )
                    self.sessions.save(session)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Task consumer failed for {}: {}", session_id, exc)

    async def run_deep_reflection(self, *, reason: str = "", warm_limit: int = 15):
        async with self._deep_reflection_lock:
            return await self.reflection_coordinator.run_deep_reflection(reason=reason, warm_limit=warm_limit)

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
                result = await self.reflection_coordinator.write_turn_reflection(state)
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
