"""系统工具 - 消息发送、定时任务。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Coroutine

from emoticorebot.tools.base import Tool

if TYPE_CHECKING:
    from emoticorebot.cron.service import CronService


class MessageTool(Tool):
    """向用户发送消息（主动推送进度通知）"""

    def __init__(self, send_callback: Callable[..., Coroutine[Any, Any, None]]):
        self._send = send_callback
        self._channel: str = "cli"
        self._chat_id: str = "direct"
        self._message_id: str | None = None

    def set_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        self._channel = channel
        self._chat_id = chat_id
        self._message_id = message_id

    @property
    def name(self) -> str:
        return "message"

    @property
    def description(self) -> str:
        return "Send a message to the user (use for progress updates or intermediate results)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Message content to send"},
            },
            "required": ["content"],
        }

    async def execute(self, content: str, **kwargs: Any) -> str:
        try:
            from emoticorebot.runtime.event_bus import OutboundMessage

            msg = OutboundMessage(
                channel=self._channel,
                chat_id=self._chat_id,
                content=content,
            )
            await self._send(msg)
            return "Message sent"
        except Exception as e:
            return f"Error sending message: {e}"


class CronTool(Tool):
    """管理定时任务（增删查改 cron jobs）"""

    def __init__(self, cron_service: "CronService"):
        self._cron = cron_service
        self._channel: str = "cli"
        self._chat_id: str = "direct"

    def set_context(self, channel: str, chat_id: str) -> None:
        self._channel = channel
        self._chat_id = chat_id

    @property
    def name(self) -> str:
        return "cron"

    @property
    def description(self) -> str:
        return (
            "Manage scheduled tasks. Actions: list, add, remove, enable, disable.\n"
            "Schedule kinds: 'every' (everyMs), 'at' (atMs), 'cron' (expr + optional tz)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "add", "remove", "enable", "disable"],
                },
                "name": {"type": "string"},
                "message": {"type": "string"},
                "schedule_kind": {"type": "string", "enum": ["every", "at", "cron"]},
                "every_ms": {"type": "integer"},
                "at_ms": {"type": "integer"},
                "cron_expr": {"type": "string"},
                "tz": {"type": "string"},
                "job_id": {"type": "string"},
                "delete_after_run": {"type": "boolean"},
            },
            "required": ["action"],
        }

    async def execute(self, action: str, **kwargs: Any) -> str:
        try:
            from emoticorebot.cron.types import CronSchedule

            if action == "list":
                jobs = self._cron.list_jobs(include_disabled=True)
                if not jobs:
                    return "No scheduled jobs"
                lines = []
                for j in jobs:
                    status = "✓" if j.enabled else "✗"
                    next_run = ""
                    if j.state.next_run_at_ms:
                        from datetime import datetime
                        dt = datetime.fromtimestamp(j.state.next_run_at_ms / 1000)
                        next_run = f" | next: {dt.strftime('%Y-%m-%d %H:%M:%S')}"
                    lines.append(f"[{status}] {j.id} - {j.name}{next_run}")
                return "\n".join(lines)

            elif action == "add":
                name = kwargs.get("name", "")
                message = kwargs.get("message", "")
                kind = kwargs.get("schedule_kind", "")
                if not name or not message or not kind:
                    return "Error: name, message, and schedule_kind are required for add"
                schedule = CronSchedule(
                    kind=kind,
                    every_ms=kwargs.get("every_ms"),
                    at_ms=kwargs.get("at_ms"),
                    expr=kwargs.get("cron_expr"),
                    tz=kwargs.get("tz"),
                )
                job = self._cron.add_job(
                    name=name,
                    schedule=schedule,
                    message=message,
                    channel=self._channel,
                    to=self._chat_id,
                    delete_after_run=kwargs.get("delete_after_run", False),
                )
                return f"Job '{name}' created (id: {job.id})"

            elif action == "remove":
                job_id = kwargs.get("job_id", "")
                if not job_id:
                    return "Error: job_id is required for remove"
                removed = self._cron.remove_job(job_id)
                return f"Job {job_id} removed" if removed else f"Error: Job {job_id} not found"

            elif action in ("enable", "disable"):
                job_id = kwargs.get("job_id", "")
                if not job_id:
                    return f"Error: job_id is required for {action}"
                job = self._cron.enable_job(job_id, enabled=(action == "enable"))
                if job:
                    return f"Job {job_id} {action}d"
                return f"Error: Job {job_id} not found"

            return f"Error: Unknown action '{action}'"
        except Exception as e:
            return f"Error managing cron: {e}"


__all__ = ["MessageTool", "CronTool"]
