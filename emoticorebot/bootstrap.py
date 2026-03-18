"""Bootstrap host for the application runtime stack.

这个模块负责装配系统主通路：
1. 消息调度（接收消息、分发处理）
2. 协调 transport、线程历史与新的 bus-driven runtime kernel
3. 线程历史管理（加载/保存 `dialogue` 与 `internal`）
4. 反思与后台服务调度
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable
from uuid import uuid4

from loguru import logger

from emoticorebot.adapters.conversation_gateway import ConversationGateway
from emoticorebot.agent.tool import ToolManager
from emoticorebot.config.schema import MemoryConfig, ModelModeConfig, ProvidersConfig
from emoticorebot.agent.context import ContextBuilder
from emoticorebot.agent.model import LLMFactory
from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.protocol.envelope import BusEnvelope
from emoticorebot.protocol.events import DeliveryFailedPayload, RepliedPayload, ReplyReadyPayload
from emoticorebot.protocol.topics import EventType
from emoticorebot.runtime.transport_bus import InboundMessage, OutboundMessage, TransportBus
from emoticorebot.runtime.kernel import RuntimeKernel, TurnReply
from emoticorebot.session.thread_store import ThreadStore
from emoticorebot.utils.llm_utils import extract_message_text
from emoticorebot.utils.task_projection import project_task_from_runtime_snapshot, project_task_from_session_view

if TYPE_CHECKING:
    from emoticorebot.config.schema import ChannelsConfig, ExecToolConfig
    from emoticorebot.cron.service import CronService


class RuntimeHost:
    """Top-level host that wires the application runtime together."""

    def __init__(
        self,
        bus: TransportBus,
        workspace: Path,
        worker_mode: "ModelModeConfig",
        brain_mode: "ModelModeConfig",
        brave_api_key: str | None = None,
        exec_config: "ExecToolConfig | None" = None,
        cron_service: "CronService | None" = None,
        restrict_to_workspace: bool = False,
        thread_store: ThreadStore | None = None,
        mcp_servers: dict | None = None,
        channels_config: "ChannelsConfig | None" = None,
        providers_config: "ProvidersConfig | None" = None,
        memory_config: "MemoryConfig | None" = None,
    ):
        from emoticorebot.config.schema import ExecToolConfig

        self.bus = bus
        self.workspace = workspace
        self.worker_mode = worker_mode
        self.brain_mode = brain_mode
        self.memory_window = worker_mode.memory_window
        self.channels_config = channels_config

        self.thread_store = thread_store or ThreadStore(workspace)
        self.emotion_mgr = EmotionStateManager(workspace)
        self.context = ContextBuilder(
            workspace,
            memory_config=memory_config,
            providers_config=providers_config,
        )

        factory = LLMFactory(
            providers_config=providers_config,
            worker_mode=worker_mode,
            brain_mode=brain_mode,
        )
        self.worker_llm = factory.get_worker()
        self.brain_llm = factory.get_brain()

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
        self.kernel = RuntimeKernel(
            workspace=workspace,
            transport=self.bus,
            brain_llm=self.brain_llm,
            worker_llm=self.worker_llm,
            reflection_llm=self.brain_llm,
            context_builder=self.context,
            tool_registry=self.tool_manager.get_registry(),
            emotion_manager=self.emotion_mgr,
            memory_config=memory_config,
            providers_config=providers_config,
        )
        self.conversation_gateway = ConversationGateway(
            bus=self.bus,
            message_processor=self._process_message,
        )
        self._pending_task_origin_replies: dict[str, BusEnvelope[ReplyReadyPayload]] = {}
        self.kernel.event_bus.subscribe(
            consumer="runtime_host",
            event_type=EventType.OUTPUT_INLINE_READY,
            handler=self._remember_task_origin_reply,
        )
        self.kernel.event_bus.subscribe(
            consumer="runtime_host",
            event_type=EventType.OUTPUT_PUSH_READY,
            handler=self._remember_task_origin_reply,
        )
        self.kernel.event_bus.subscribe(
            consumer="runtime_host",
            event_type=EventType.OUTPUT_STREAM_OPEN,
            handler=self._remember_task_origin_reply,
        )
        self.kernel.event_bus.subscribe(
            consumer="runtime_host",
            event_type=EventType.OUTPUT_STREAM_DELTA,
            handler=self._remember_task_origin_reply,
        )
        self.kernel.event_bus.subscribe(
            consumer="runtime_host",
            event_type=EventType.OUTPUT_STREAM_CLOSE,
            handler=self._remember_task_origin_reply,
        )
        self.kernel.event_bus.subscribe(
            consumer="runtime_host",
            event_type=EventType.OUTPUT_REPLIED,
            handler=self._persist_task_origin_internal_reply,
        )
        self.kernel.event_bus.subscribe(
            consumer="runtime_host",
            event_type=EventType.OUTPUT_DELIVERY_FAILED,
            handler=self._discard_task_origin_reply,
        )

        self._running = False

        self.subconscious = None
        self.heartbeat = None

    async def run(self) -> None:
        """主循环：接收消息并调度"""
        self._running = True
        await self.tool_manager.connect_mcp_servers(self._mcp_servers)
        await self.kernel.start()
        logger.info("Emoticore runtime started")
        await self.conversation_gateway.run_forever(lambda: self._running)

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """处理消息（核心逻辑）"""
        key = session_key or msg.session_key
        message_id = str(msg.metadata.get("message_id", "") or "").strip() or self._new_message_id()
        msg.metadata["message_id"] = message_id
        if msg.content == "__subconscious_recovery__":
            if self.subconscious:
                await self.subconscious.handle_energy_recovery()
            return None

        cmd = msg.content.strip().lower()

        if cmd == "/new":
            await self._reset_session(key)
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="New session started.",
                reply_to=message_id,
                metadata=msg.metadata or {},
            )
        if cmd == "/help":
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=self._help_text(),
                reply_to=message_id,
                metadata=msg.metadata or {},
            )
        self.tool_manager.set_context(
            msg.channel,
            msg.chat_id,
            message_id,
            key,
        )

        dialogue_history, internal_history = self._snapshot_turn_input(key)
        self._persist_user_message(
            session_key=key,
            content=msg.content,
            media=msg.media,
            message_id=message_id,
            timestamp=msg.timestamp.isoformat(),
        )
        content, final_state = await self._run_user_message(
            user_input=msg.content,
            dialogue_history=dialogue_history,
            internal_history=internal_history,
            message_id=message_id,
            channel=msg.channel,
            chat_id=msg.chat_id,
            session_id=key,
            media=msg.media,
            message_metadata=msg.metadata,
        )
        turn_id = str(final_state.get("turn_id", "") or "").strip()
        if turn_id and not self.kernel.is_current_turn(session_id=key, turn_id=turn_id):
            return None

        assistant_timestamp = datetime.now().isoformat()
        self.thread_store.append_internal_messages(
            key,
            self._build_internal_turn_records(
                final_state,
                assistant_timestamp=assistant_timestamp,
                message_id=message_id,
                existing_internal_count=len(internal_history),
            ),
        )

        thread = self.thread_store.get_or_create(key)
        assistant_fields = self._build_assistant_session_fields(final_state)
        thread.add_message(
            "assistant",
            [{"type": "text", "text": content}],
            message_id=message_id,
            timestamp=assistant_timestamp,
            **assistant_fields,
        )
        self.thread_store.save(thread)
        self._save_proactive_target(msg.channel, msg.chat_id)
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=content,
            reply_to=str(msg.metadata.get("message_id", "") or "").strip() or None,
            metadata=msg.metadata or {},
        )

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        deliver: bool = False,
        message_id: str | None = None,
    ) -> str:
        """直接处理消息（不通过消息总线，供 CLI 使用）"""
        await self.tool_manager.connect_mcp_servers(self._mcp_servers)
        metadata: dict[str, Any] = {}
        if not deliver:
            metadata["suppress_delivery"] = True
        if message_id:
            metadata["message_id"] = message_id
        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content,
            metadata=metadata,
        )
        response = await self.conversation_gateway.process_direct(
            msg,
            session_key=session_key,
            on_progress=on_progress,
        )
        return response.content if response else ""

    async def close_mcp(self) -> None:
        """关闭 MCP 连接"""
        await self.tool_manager.close_mcp()

    async def generate_proactive_message(self, prompt: str) -> str:
        """Generate a proactive user-facing message without entering the task pipeline."""
        fallback = "刚刚想到你了，就来打个招呼。"
        model = self.brain_llm
        if model is None:
            return fallback
        try:
            if hasattr(model, "ainvoke"):
                response = await model.ainvoke(prompt)
            elif hasattr(model, "invoke"):
                response = model.invoke(prompt)
            else:
                return fallback
            text = extract_message_text(response)
            return str(text or "").strip() or fallback
        except Exception as exc:
            logger.warning("Proactive generation failed: {}", exc)
            return fallback

    def stop(self) -> None:
        """停止 Runtime"""
        self._running = False
        self.conversation_gateway.stop()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.kernel.stop(), name="runtime-kernel-stop")

    async def run_deep_reflection(self, *, reason: str = "", warm_limit: int = 15) -> Any:
        """运行深反思（供周期性触发或外部调用）"""
        return await self.kernel.run_deep_reflection(reason=reason, warm_limit=warm_limit)

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
        message_metadata: dict[str, Any] | None,
    ) -> tuple[str, dict[str, Any]]:
        turn = await self.kernel.handle_user_message(
            session_id=session_id,
            channel=channel,
            chat_id=chat_id,
            sender_id="user",
            message_id=message_id,
            content=user_input,
            history_context=self._build_history_context(dialogue_history, internal_history),
            attachments=media,
            metadata=dict(message_metadata or {}),
        )

        message = str(turn.content or "").strip()
        if not message:
            message = "我先处理这件事。"

        task_action = "none"
        latest_task = self.kernel.latest_task_for_session(session_id, include_terminal=True)
        if latest_task is not None and latest_task.turn_id == turn.turn_id:
            task_action = "create_task"
        execution_summary = "brain/runtime kernel completed turn dispatch"
        final_decision = "continue" if task_action != "none" else "answer"

        final_state: dict[str, Any] = {
            "turn_id": turn.turn_id,
            "output": message,
            "execution_summary": execution_summary,
            "brain": {
                "task_action": task_action,
                "final_decision": final_decision,
                "execution_summary": execution_summary,
                "reply_event_type": turn.event_type,
                "turn_id": turn.turn_id,
            },
        }
        task_snapshot = self._resolve_turn_task_state(session_id=session_id, turn=turn)
        if task_snapshot:
            final_state["task"] = task_snapshot

        return message, final_state

    def _build_internal_turn_records(
        self,
        final_state: dict[str, Any],
        *,
        assistant_timestamp: str,
        message_id: str,
        existing_internal_count: int = 0,
        source: str = "runtime",
    ) -> list[dict[str, Any]]:
        """构建内部历史记录，包含完整的执行上下文"""
        records: list[dict[str, Any]] = []

        # 基础记录
        base_record = {
            "message_id": message_id,
            "role": "assistant",
            "timestamp": assistant_timestamp,
            "source": source,
        }

        # Brain 决策记录
        brain_info = final_state.get("brain", {})
        execution_summary = final_state.get("execution_summary", "")
        if brain_info or execution_summary:
            summary = str(execution_summary or final_state.get("output", "") or "").strip()
            brain_record = {
                **base_record,
                "phase": "brain",
                "event": "brain.decision",
                "content": summary,
                "brain": {
                    "task_action": brain_info.get("task_action", "none"),
                    "final_decision": brain_info.get("final_decision", "answer"),
                    "reply_event_type": brain_info.get("reply_event_type", ""),
                    "turn_id": brain_info.get("turn_id", ""),
                    "execution_summary": execution_summary,
                },
            }
            records.append(brain_record)

        # Task 记录（如果有）
        task_info = final_state.get("task")
        if task_info:
            summary = str(task_info.get("summary", "") or final_state.get("output", "") or "").strip()
            task_record = {
                **base_record,
                "phase": "task",
                "event": "task.executed",
                "content": summary,
                "task": {
                    "task_id": task_info.get("task_id", ""),
                    "state": task_info.get("state", ""),
                    "result": task_info.get("result", ""),
                    "summary": task_info.get("summary", ""),
                    "missing": task_info.get("missing", []),
                },
            }
            records.append(task_record)

        # Task trace 记录（如果有）
        task_trace = list((task_info or {}).get("task_trace", []) or [])
        if task_trace:
            trace_summary = self._summarize_trace(task_trace)
            trace_record = {
                **base_record,
                "phase": "execution",
                "event": "execution.trace",
                "content": trace_summary,
                "meta": {
                    "trace_count": len(task_trace),
                    "trace_summary": trace_summary,
                },
            }
            records.append(trace_record)

        # 如果没有任何特殊记录，至少保留一个占位符
        if not records:
            output = str(final_state.get("output", "") or "").strip()
            records.append({
                **base_record,
                "phase": "brain",
                "event": "brain.turn.summary",
                "content": output,
                "meta": {"summary": output, "output": output},
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

    def _resolve_turn_task_state(self, *, session_id: str, turn: TurnReply) -> dict[str, Any]:
        return self._resolve_session_task_state(session_id=session_id, task_id=turn.related_task_id or "")

    def _resolve_session_task_state(self, *, session_id: str, task_id: str = "") -> dict[str, Any]:
        task = self.kernel.get_task(task_id) if task_id else None
        if task is None:
            task = self.kernel.latest_task_for_session(session_id, include_terminal=True)
        if task is None:
            return {}
        request = task.request.model_dump(exclude_none=True)
        params = self._compact_task_spec_for_session(request)
        task_view = self.kernel.session_runtime.task_view(session_id, task.task_id)
        if task_view is not None:
            return self._compact_task_state_for_session(
                project_task_from_session_view(
                    task_view,
                    params=params,
                )
            )
        return self._compact_task_state_for_session(
            project_task_from_runtime_snapshot(
                task.snapshot().model_dump(exclude_none=True),
                params=params,
            )
        )

    @staticmethod
    def _new_message_id() -> str:
        return f"msg_{uuid4().hex[:16]}"

    def _build_assistant_session_fields(self, final_state: dict[str, Any]) -> dict[str, Any]:
        task_state = final_state.get("task")
        if not isinstance(task_state, dict) or not task_state:
            return {}
        compact = self._compact_task_state_for_session(task_state)
        return {"task": compact} if compact else {}

    @staticmethod
    def _compact_task_spec_for_session(task_spec: dict[str, Any] | None) -> dict[str, Any]:
        """Keep task params structured while stripping heavy history from dialogue persistence."""
        if not isinstance(task_spec, dict):
            return {}
        compact: dict[str, Any] = {}
        for key in (
            "task_id",
            "origin_message_id",
            "title",
            "request",
            "goal",
            "expected_output",
            "history_context",
            "channel",
            "chat_id",
            "session_id",
        ):
            value = str(task_spec.get(key, "") or "").strip()
            if value:
                compact[key] = value
        for key in ("constraints", "success_criteria", "memory_bundle_ids", "skill_hints", "media"):
            values = [str(item).strip() for item in list(task_spec.get(key, []) or []) if str(item).strip()]
            if values:
                compact[key] = values
        task_context = task_spec.get("task_context")
        if isinstance(task_context, dict) and task_context:
            compact["task_context"] = dict(task_context)
        return compact

    def _compact_task_state_for_session(self, task_state: dict[str, Any] | None) -> dict[str, Any]:
        """Persist a compact but fully structured task snapshot into dialogue/session records."""
        if not isinstance(task_state, dict):
            return {}
        compact: dict[str, Any] = {}
        for key in (
            "invoked",
            "task_id",
            "title",
            "state",
            "result",
            "summary",
            "error",
            "stage",
            "recommended_action",
            "confidence",
            "attempt_count",
        ):
            value = task_state.get(key)
            if value not in ("", None, [], {}):
                compact[key] = value
        missing = [str(item).strip() for item in list(task_state.get("missing", []) or []) if str(item).strip()]
        if missing:
            compact["missing"] = missing
        input_request = task_state.get("input_request")
        if isinstance(input_request, dict) and input_request:
            compact["input_request"] = {
                "field": str(input_request.get("field", "") or "").strip(),
                "question": str(input_request.get("question", "") or "").strip(),
            }
        task_trace = task_state.get("task_trace")
        if isinstance(task_trace, list) and task_trace:
            compact["task_trace"] = [item for item in task_trace if isinstance(item, dict)]
        params = task_state.get("params")
        compact_params = self._compact_task_spec_for_session(params if isinstance(params, dict) else None)
        if compact_params:
            compact["params"] = compact_params
        return compact

    async def _remember_task_origin_reply(self, event: BusEnvelope[ReplyReadyPayload]) -> None:
        if not self._is_task_origin_reply(event):
            return
        stream_state = self._reply_stream_state(event.payload.reply.metadata)
        if stream_state in {"open", "delta", "superseded"}:
            return
        reply_id = str(event.payload.reply.reply_id or "").strip()
        if not reply_id:
            return
        self._pending_task_origin_replies[reply_id] = event

    async def _discard_task_origin_reply(self, event: BusEnvelope[DeliveryFailedPayload]) -> None:
        reply_id = str(event.payload.reply_id or "").strip()
        if reply_id:
            self._pending_task_origin_replies.pop(reply_id, None)

    async def _persist_task_origin_internal_reply(self, event: BusEnvelope[RepliedPayload]) -> None:
        reply_id = str(event.payload.reply_id or "").strip()
        if not reply_id:
            return
        pending = self._pending_task_origin_replies.pop(reply_id, None)
        if pending is None or not self._is_task_origin_reply(pending):
            return
        session_id = str(event.session_id or "").strip()
        if not session_id:
            return
        delivery_message = event.payload.delivery_message
        assistant_timestamp = str(
            event.payload.delivered_at
            or delivery_message.timestamp
            or datetime.now().isoformat()
        )
        self.thread_store.append_internal_messages(
            session_id,
            self._build_internal_turn_records(
                self._task_origin_final_state(pending),
                assistant_timestamp=assistant_timestamp,
                message_id=str(delivery_message.message_id or reply_id),
                source="task",
            ),
        )

    def _task_origin_final_state(self, event: BusEnvelope[ReplyReadyPayload]) -> dict[str, Any]:
        reply_metadata = dict(event.payload.reply.metadata or {})
        task_event_type = str(reply_metadata.get("task_event_type", "") or "").strip()
        task_id = str(event.payload.related_task_id or event.task_id or "").strip()
        reply_text = self._reply_text(event.payload)
        execution_summary = {
            str(EventType.TASK_ASK): "任务副路需要用户补充信息。",
            str(EventType.TASK_END): "任务副路已经结束，并回传结果。",
        }.get(task_event_type, "任务副路产出了新的内部结果。")
        return {
            "turn_id": event.turn_id or "",
            "output": reply_text,
            "execution_summary": execution_summary,
            "brain": {
                "task_action": "none",
                "final_decision": "ask_user" if event.payload.reply.kind == "ask_user" else "continue",
                "execution_summary": execution_summary,
                "reply_event_type": task_event_type or "task_origin",
                "turn_id": event.turn_id or "",
            },
            "task": self._resolve_session_task_state(
                session_id=str(event.session_id or "").strip(),
                task_id=task_id,
            ),
        }

    @staticmethod
    def _reply_text(payload: ReplyReadyPayload) -> str:
        if payload.reply.plain_text:
            return str(payload.reply.plain_text).strip()
        parts = [block.text for block in payload.reply.content_blocks if block.type == "text" and block.text]
        return "\n".join(str(part).strip() for part in parts if str(part).strip()).strip()

    @staticmethod
    def _is_task_origin_reply(event: BusEnvelope[ReplyReadyPayload]) -> bool:
        metadata = dict(event.payload.reply.metadata or {})
        return str(metadata.get("front_origin", "") or "").strip() == "task"

    @staticmethod
    def _reply_stream_state(metadata: dict[str, Any] | None) -> str:
        stream_state = str((metadata or {}).get("stream_state", "") or "").strip()
        if stream_state == "final":
            return "close"
        return stream_state

    @staticmethod
    def _internal_summary_text(record: dict[str, Any]) -> str:
        content = record.get("content", "")
        if isinstance(content, list):
            text_parts = [
                str(block.get("text", "") or "").strip()
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            summary = " ".join(part for part in text_parts if part).strip()
            if summary:
                return summary
        elif isinstance(content, dict):
            summary = str(content.get("summary", "") or "").strip()
            if summary:
                return summary
        else:
            summary = str(content or "").strip()
            if summary:
                return summary

        meta = record.get("meta", {})
        if isinstance(meta, dict):
            summary = str(meta.get("summary", "") or meta.get("trace_summary", "") or "").strip()
            if summary:
                return summary

        task = record.get("task", {})
        if isinstance(task, dict):
            summary = str(task.get("summary", "") or "").strip()
            if summary:
                return summary

        brain = record.get("brain", {})
        if isinstance(brain, dict):
            return str(brain.get("execution_summary", "") or "").strip()

        return ""

    def _snapshot_turn_input(self, session_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        thread = self.thread_store.get_or_create(session_id)
        dialogue_history = thread.get_history(max_messages=self.memory_window, include_task_context=False)
        internal_history = self.thread_store.get_internal_messages(session_id, max_messages=self.memory_window)
        return dialogue_history, internal_history

    def _build_history_context(
        self,
        dialogue_history: list[dict[str, Any]],
        internal_history: list[dict[str, Any]],
    ) -> str:
        lines: list[str] = []
        for turn in dialogue_history[-6:]:
            role = str(turn.get("role", "") or "").strip()
            content = turn.get("content", "")
            if isinstance(content, list):
                text_parts = [
                    str(block.get("text", "") or "").strip()
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                text = " ".join(part for part in text_parts if part).strip()
            else:
                text = str(content or "").strip()
            if role and text:
                lines.append(f"{role}: {text}")
        for record in internal_history[-3:]:
            summary = self._internal_summary_text(record)
            if summary:
                lines.append(f"internal: {summary}")
        return "\n".join(lines[-8:])

    def _persist_user_message(
        self,
        *,
        session_key: str,
        content: str,
        media: list[str] | None,
        message_id: str,
        timestamp: str,
    ) -> None:
        thread = self.thread_store.get_or_create(session_key)
        user_content = self._build_user_message_content(content, media)
        thread.add_message("user", user_content, message_id=message_id, timestamp=timestamp)
        self.thread_store.save(thread)

    def _reset_session_thread(self, session_id: str) -> None:
        thread = self.thread_store.get_or_create(session_id)
        thread.clear()
        self.thread_store.clear_internal_messages(thread.thread_id)
        self.thread_store.save(thread)
        self.thread_store.invalidate(thread.thread_id)

    async def _reset_session(self, session_id: str) -> None:
        self.kernel.clear_session(session_id)
        self._reset_session_thread(session_id)

    def initialize_subconscious(
        self,
        enable_reflection: bool = True,
        enable_heartbeat: bool = False,
        heartbeat_interval_s: int | None = None,
    ) -> None:
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
                interval_s=heartbeat_interval_s or 30 * 60,
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


__all__ = ["RuntimeHost"]
