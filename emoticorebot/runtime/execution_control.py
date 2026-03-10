"""Execution control helpers for the runtime."""

from __future__ import annotations

import asyncio
from datetime import datetime

from emoticorebot.bus.events import InboundMessage, OutboundMessage


class RuntimeExecutionControlMixin:
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

        self._mark_last_execution_stopped(msg.session_key)
        execution = self.get_execution_state(msg.session_key)
        control = self.main_brain_control_stop_execution(
            cancelled_tasks=cancelled,
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
                event="executor.execution.stopped.failed",
                content=str(execution.get("summary", "") or "执行已被手动停止。"),
                timestamp=timestamp,
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
