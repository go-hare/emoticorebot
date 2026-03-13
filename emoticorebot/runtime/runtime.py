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
from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.runtime.event_bus import InboundMessage, OutboundMessage, RuntimeEventBus
from emoticorebot.session.manager import SessionManager

if TYPE_CHECKING:
    from emoticorebot.config.schema import ChannelsConfig, ExecToolConfig
    from emoticorebot.cron.service import CronService
    from emoticorebot.session.manager import Session


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
        content, final_state = await self._run_user_message(
            user_input=msg.content,
            dialogue_history=dialogue_history,
            internal_history=internal_history,
            message_id=message_id,
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

    async def run_deep_reflection(self, *, reason: str = "", warm_limit: int = 15) -> Any:
        """运行深反思（供周期性触发或外部调用）"""
        return await self.reflection_coordinator.run_deep_reflection(reason=reason, warm_limit=warm_limit)

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
    def _help_text() -> str:
        """返回帮助信息"""
        return """可用命令：
/new - 开始新对话
/help - 显示此帮助信息"""

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
        message_id: str,
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
            "message_id": message_id,
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
        result = await self.brain_service.handle_user_message(
            user_input=user_input,
            history=dialogue_history,
            internal_history=internal_history,
            emotion=emotion,
            pad=pad,
            task_system=task_system,
            message_id=message_id,
            channel=channel,
            chat_id=chat_id,
            session_id=session_id,
            media=media,
        )

        message = result.get("message", "")
        if not message:
            message = "我先处理这件事。"

        final_state["output"] = message
        final_state["execution_summary"] = result.get("execution_summary", "")
        final_state["done"] = True
        
        # 构建完整的 metadata 结构供 reflection 使用
        execution_summary = result.get("execution_summary", "")
        brain_decision = "direct_reply" if not execution_summary else "task_delegated"
        
        final_state["metadata"] = {
            "message_id": message_id,
            "execution": {
                "summary": execution_summary,
                "brain_decision": brain_decision,
            },
            "channel": channel,
            "chat_id": chat_id,
        }
        
        # 如果委托了任务，添加 brain 决策信息
        if execution_summary:
            final_state["brain"] = {
                "decision": "delegate_to_central",
                "reasoning": execution_summary,
            }
        
        # 如果有活跃任务，添加任务快照（供 reflection 使用）
        active_tasks = task_system.active_tasks()
        if active_tasks:
            # 取最近创建的任务
            latest_task = active_tasks[-1]
            task_snapshot = latest_task.snapshot()
            final_state["task"] = task_snapshot
            final_state["metadata"]["task"] = {
                "task_id": task_snapshot.get("task_id", ""),
                "status": task_snapshot.get("status", "running"),
                "summary": task_snapshot.get("summary", ""),
                "missing": task_snapshot.get("missing", []),
            }
        
        # 添加 task_trace（如果有的话，从 result 中获取）
        final_state["task_trace"] = result.get("task_trace", [])
        
        return message, final_state

    def _build_internal_turn_records(
        self,
        final_state: dict[str, Any],
        *,
        assistant_timestamp: str,
        message_id: str,
        existing_internal_count: int = 0,
    ) -> list[dict[str, Any]]:
        """构建内部历史记录，包含完整的执行上下文"""
        records: list[dict[str, Any]] = []
        
        # 基础记录
        base_record = {
            "message_id": message_id,
            "role": "assistant",
            "timestamp": assistant_timestamp,
            "source": "runtime",
        }
        
        # Brain 决策记录
        brain_info = final_state.get("brain", {})
        execution_summary = final_state.get("execution_summary", "")
        if brain_info or execution_summary:
            brain_record = {
                **base_record,
                "phase": "brain",
                "event": "brain.decision",
                "content": {
                    "decision": brain_info.get("decision", "direct_reply"),
                    "reasoning": brain_info.get("reasoning", execution_summary),
                    "execution_summary": execution_summary,
                },
            }
            records.append(brain_record)
        
        # Task 记录（如果有）
        task_info = final_state.get("task")
        metadata_task = (final_state.get("metadata", {}) or {}).get("task")
        if task_info or metadata_task:
            task_record = {
                **base_record,
                "phase": "task",
                "event": "task.executed",
                "content": {
                    "task_id": (task_info or {}).get("task_id", "") or (metadata_task or {}).get("task_id", ""),
                    "status": (task_info or {}).get("status", "") or (metadata_task or {}).get("status", ""),
                    "summary": (task_info or {}).get("summary", "") or (metadata_task or {}).get("summary", ""),
                    "missing": (task_info or {}).get("missing", []) or (metadata_task or {}).get("missing", []),
                },
            }
            records.append(task_record)
        
        # Task trace 记录（如果有）
        task_trace = final_state.get("task_trace", [])
        if task_trace and isinstance(task_trace, list):
            trace_record = {
                **base_record,
                "phase": "execution",
                "event": "execution.trace",
                "content": {
                    "trace_count": len(task_trace),
                    "trace_summary": self._summarize_trace(task_trace),
                },
            }
            records.append(trace_record)
        
        # 如果没有任何特殊记录，至少保留一个占位符
        if not records:
            records.append({
                **base_record,
                "phase": "brain",
                "event": "brain.turn.summary",
                "content": {"output": final_state.get("output", "")},
            })
        
        return records
    
    @staticmethod
    def _summarize_trace(trace: list[dict[str, Any]]) -> str:
        """总结执行追踪"""
        if not trace:
            return ""
        
        tool_calls = [t for t in trace if t.get("type") == "tool_call"]
        if not tool_calls:
            return f"{len(trace)} 个执行步骤"
        
        tool_names = []
        for call in tool_calls:
            name = call.get("tool_name") or call.get("name") or ""
            if name and name not in tool_names:
                tool_names.append(name)
        
        if tool_names:
            return f"调用了 {len(tool_calls)} 次工具: {', '.join(tool_names[:5])}"
        return f"{len(tool_calls)} 次工具调用"

    def _build_user_message_content(self, content: str, media: list[str] | None) -> list[dict[str, Any]]:
        media_items = self.context.build_media_context(media)
        return [{"type": "text", "text": str(content or "")}, *media_items]

    @staticmethod
    def _new_message_id() -> str:
        return f"msg_{uuid4().hex[:16]}"

    def _build_assistant_session_fields(self, final_state: dict[str, Any]) -> dict[str, Any]:
        task_state = final_state.get("task")
        if not isinstance(task_state, dict) or not task_state:
            return {}
        
        task_id = str(task_state.get("task_id", "") or "").strip()
        task_payload = {
            "invoked": bool(task_id),
            "task_id": task_id,
            "status": str(task_state.get("status", "") or "").strip(),
            "summary": str(
                task_state.get("summary", "") or task_state.get("analysis", "") or ""
            ).strip(),
        }
        cleaned = {k: v for k, v in task_payload.items() if v not in ("", None, False)}
        return {"task": cleaned} if cleaned else {}

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
            self._start_task_consumer(session_id=key)
        return system

    def _start_task_consumer(self, session_id: str) -> None:
        """启动任务事件消费者"""
        existing = self._task_consumers.get(session_id)
        if existing is not None and not existing.done():
            return

        task = asyncio.create_task(
            self._consume_task_events(session_id=session_id),
            name=f"task-consumer:{session_id}",
        )
        self._task_consumers[session_id] = task

        def _cleanup(done_task: asyncio.Task, consumer_key: str = session_id) -> None:
            current = self._task_consumers.get(consumer_key)
            if current is done_task:
                self._task_consumers.pop(consumer_key, None)

        task.add_done_callback(_cleanup)

    async def _consume_task_events(self, session_id: str) -> None:
        """消费任务事件并发送回复"""
        system = self._get_task_system(session_id)
        while True:
            event = await system.to_main_queue.get()
            try:
                # 从事件中获取路由信息
                channel = str(event.get("channel", "") or "").strip()
                chat_id = str(event.get("chat_id", "") or "").strip()
                task_id = str(event.get("task_id", "") or "").strip()
                event_type = str(event.get("type", "") or "").strip()
                
                # 如果缺少路由信息，记录内部状态但不发送消息
                if not channel or not chat_id:
                    # 仍然更新内部状态和反思
                    session = self.sessions.get(session_id)
                    if session is not None:
                        await self._handle_task_event_internal(session, event, session_id)
                    continue
                
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
                content = await self.brain_service.handle_task_event(
                    event=event,
                    history=history,
                    emotion=self.emotion_mgr.get_emotion_label(),
                    pad=pad,
                    task_system=system,
                    channel=channel,
                    chat_id=chat_id,
                    session_id=session_id,
                )
                if not content:
                    continue
                assistant_message_id = self._new_message_id()
                task_id = str(event.get("task_id", "") or "").strip()

                origin_message_id = str(event.get("message_id", "") or "").strip()
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=channel,
                        chat_id=chat_id,
                        content=content,
                        reply_to=origin_message_id or None,
                        metadata={
                            "task_id": task_id,
                            "task_event": str(event.get("type", "") or "").strip(),
                            "producer": "task_system",
                            "message_id": origin_message_id,
                        },
                    )
                )

                if session is not None:
                    assistant_timestamp = datetime.now().isoformat()
                    event_type = str(event.get("type", "") or "").strip().lower()
                    task_status = "running"
                    task_control_state = str(event.get("control_state", "running") or "").strip()
                    
                    if event_type == "need_input":
                        task_status = "waiting_input"
                        task_control_state = "waiting_input"
                    elif event_type == "done":
                        task_status = "done"
                        task_control_state = str(event.get("control_state", "completed") or "completed").strip()
                    elif event_type == "failed":
                        task_status = "failed"
                        task_control_state = "failed"
                    
                    task_snapshot = {
                        "invoked": True,
                        "task_id": str(event.get("task_id", "") or "").strip(),
                        "control_state": task_control_state,
                        "status": task_status,
                        "summary": str(event.get("summary", "") or event.get("message", "") or "").strip(),
                        "analysis": str(event.get("analysis", "") or "").strip(),
                        "missing": [
                            str(item).strip()
                            for item in list(
                                event.get("missing", [])
                                or ([event.get("field")] if event.get("field") else [])
                            )
                            if str(item).strip()
                        ],
                        "recommended_action": str(event.get("recommended_action", "") or "").strip(),
                        "confidence": float(event.get("confidence", 0.8 if task_status == "done" else 0.5)),
                    }
                    session.add_message(
                        "assistant",
                        [{"type": "text", "text": content}],
                        message_id=assistant_message_id,
                        timestamp=assistant_timestamp,
                        task=task_snapshot,
                    )
                    self.sessions.save(session)
                    
                    # 任务事件也需要反思
                    task_state = {
                        "user_input": str(event.get("summary", "") or event.get("question", "") or ""),
                        "output": content,
                        "session_id": session_id,
                        "execution_summary": self._build_task_execution_summary(event, task_status),
                        "metadata": {
                            "message_id": assistant_message_id,
                            "execution": {
                                "summary": self._build_task_execution_summary(event, task_status),
                                "brain_decision": "task_event",
                            },
                            "channel": channel,
                            "chat_id": chat_id,
                            "task": {
                                "task_id": task_snapshot.get("task_id", ""),
                                "status": task_status,
                                "summary": task_snapshot.get("summary", ""),
                                "analysis": task_snapshot.get("analysis", ""),
                                "missing": task_snapshot.get("missing", []),
                                "failure_reason": str(event.get("reason", "")).strip() if event_type == "failed" else "",
                                "recommended_action": task_snapshot.get("recommended_action", ""),
                                "confidence": task_snapshot.get("confidence", 0.5),
                                "attempt_count": 1,
                            },
                        },
                        "task": task_snapshot,
                        "task_trace": list(event.get("task_trace", []) or []),
                    }
                    self._schedule_turn_reflection(session_key=session_id, state=task_state)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Task consumer failed for {}: {}", session_id, exc)

    def _schedule_turn_reflection(self, *, session_key: str, state: dict[str, Any]) -> None:
        """异步调度反思，不阻塞主流程"""
        async def _run():
            try:
                result = await self.reflection_coordinator.write_turn_reflection(state)
                if result and getattr(result, "should_run_deep_reflection", False):
                    await self.reflection_coordinator.run_deep_reflection(
                        reason=str(getattr(result, "deep_reflection_reason", "") or ""),
                    )
            except Exception as exc:
                logger.warning("Reflection failed for {}: {}", session_key, exc)
        
        asyncio.create_task(_run(), name=f"reflection:{session_key}")

    async def _handle_task_event_internal(
        self, session: "Session", event: dict[str, Any], session_id: str
    ) -> None:
        """处理缺少路由信息的任务事件（仅内部状态更新）"""
        event_type = str(event.get("type", "") or "").strip().lower()
        task_id = str(event.get("task_id", "") or "").strip()
        
        # 构建任务快照
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
            "task_id": task_id,
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
        
        # 调度反思
        task_state = {
            "user_input": str(event.get("summary", "") or event.get("question", "") or ""),
            "output": f"[内部任务事件] {task_snapshot.get('summary', '')}",
            "session_id": session_id,
            "execution_summary": self._build_task_execution_summary(event, task_status),
            "metadata": {
                "message_id": f"internal_{task_id}_{event_type}",
                "execution": {
                    "summary": self._build_task_execution_summary(event, task_status),
                    "brain_decision": "internal_task_event",
                },
                "task": {
                    "task_id": task_id,
                    "status": task_status,
                    "summary": task_snapshot.get("summary", ""),
                    "missing": task_snapshot.get("missing", []),
                    "failure_reason": str(event.get("reason", "")).strip() if event_type == "failed" else "",
                    "recommended_action": self._get_task_recommended_action(event_type, task_status),
                    "confidence": 0.8 if task_status == "done" else 0.5,
                    "attempt_count": 1,
                }
            },
            "task": task_snapshot,
        }
        self._schedule_turn_reflection(session_key=session_id, state=task_state)

    @staticmethod
    def _build_task_execution_summary(event: dict[str, Any], status: str) -> str:
        """构建任务执行摘要"""
        event_type = str(event.get("type", "")).strip()
        task_id = str(event.get("task_id", "")).strip()
        
        if event_type == "done":
            summary = str(event.get("summary", "")).strip()
            return f"任务 {task_id} 已完成：{summary}" if summary else f"任务 {task_id} 已完成"
        elif event_type == "failed":
            reason = str(event.get("reason", "")).strip()
            return f"任务 {task_id} 执行失败：{reason}" if reason else f"任务 {task_id} 执行失败"
        elif event_type == "need_input":
            question = str(event.get("question", "")).strip()
            field = str(event.get("field", "")).strip()
            if question:
                return f"任务 {task_id} 需要用户提供信息：{question}"
            elif field:
                return f"任务 {task_id} 需要用户提供：{field}"
            return f"任务 {task_id} 需要更多信息"
        elif event_type == "progress":
            message = str(event.get("message", "")).strip()
            return f"任务 {task_id} 进展：{message}" if message else f"任务 {task_id} 执行中"
        else:
            return f"任务 {task_id} 状态更新"

    @staticmethod
    def _get_task_recommended_action(event_type: str, status: str) -> str:
        """获取任务的建议操作"""
        if event_type == "need_input":
            return "等待用户提供所需信息"
        elif event_type == "failed":
            return "分析失败原因，考虑重试或调整策略"
        elif event_type == "done":
            return ""
        elif status == "waiting_input":
            return "等待用户补充信息"
        else:
            return ""

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
