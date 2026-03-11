"""心跳服务 - 定时检查 HEARTBEAT.md 中的待办任务。

两阶段设计：
Phase 1（决策）：读取 HEARTBEAT.md，让 LLM 通过工具调用判断 skip/run。
Phase 2（执行）：仅当 Phase 1 返回 run 时，触发 on_execute 回调执行任务。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Coroutine

from loguru import logger

if TYPE_CHECKING:
    from emoticorebot.runtime.runtime import EmoticoreRuntime

_HEARTBEAT_TOOL = {
    "type": "function",
    "function": {
        "name": "heartbeat",
        "description": "检查任务后，报告本次心跳的决策结果。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["skip", "run"],
                    "description": "skip 表示当前没有要做的事，run 表示存在需要执行的任务",
                },
                "tasks": {
                    "type": "string",
                    "description": "当前活跃任务的自然语言总结（当 action=run 时必填）",
                },
            },
            "required": ["action"],
        },
    },
}


class HeartbeatService:
    """心跳服务：定期唤醒 agent 检查是否有待执行任务。"""

    def __init__(
        self,
        workspace: Path,
        runtime: "EmoticoreRuntime",
        on_execute: Callable[[str], Coroutine[Any, Any, str]] | None = None,
        on_notify: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        interval_s: int = 30 * 60,
        enabled: bool = True,
    ):
        self.workspace = workspace
        self.runtime = runtime
        self.on_execute = on_execute
        self.on_notify = on_notify
        self.interval_s = interval_s
        self.enabled = enabled
        self._running = False
        self._task: asyncio.Task | None = None

    @property
    def heartbeat_file(self) -> Path:
        return self.workspace / "HEARTBEAT.md"

    def _read_heartbeat_file(self) -> str | None:
        if self.heartbeat_file.exists():
            try:
                return self.heartbeat_file.read_text(encoding="utf-8")
            except Exception:
                return None
        return None

    async def _decide(self, content: str) -> tuple[str, str]:
        """Phase 1：使用 central 模型工具调用判断 skip/run。"""
        from langchain_core.messages import HumanMessage, SystemMessage

        resp = await self.runtime.central_llm.ainvoke(
            [
                SystemMessage(
                    content="你是一个心跳检查代理。请调用 heartbeat 工具报告你的判断结果。"
                ),
                HumanMessage(
                    content=(
                        "请阅读下面的 HEARTBEAT.md，并判断当前是否存在需要执行的活跃任务。\n\n"
                        f"{content}"
                    )
                ),
            ],
            tools=[_HEARTBEAT_TOOL],
        )

        if not hasattr(resp, "tool_calls") or not resp.tool_calls:
            return "skip", ""

        args = resp.tool_calls[0]["args"]
        return args.get("action", "skip"), args.get("tasks", "")

    async def start(self) -> None:
        """启动心跳服务。"""
        if not self.enabled:
            logger.info("Heartbeat disabled")
            return
        if self._running:
            logger.warning("Heartbeat already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Heartbeat started (every {}s)", self.interval_s)

    def stop(self) -> None:
        """停止心跳服务。"""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.interval_s)
                if self._running:
                    await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Heartbeat error: {}", e)

    async def _tick(self) -> None:
        content = self._read_heartbeat_file()
        if not content:
            logger.debug("Heartbeat: HEARTBEAT.md missing or empty")
            return

        logger.info("Heartbeat: checking for tasks...")
        try:
            action, tasks = await self._decide(content)
            if action != "run":
                logger.info("Heartbeat: OK (nothing to report)")
                return
            logger.info("Heartbeat: tasks found, executing...")
            if self.on_execute:
                response = await self.on_execute(tasks)
                if response and self.on_notify:
                    logger.info("Heartbeat: completed, delivering response")
                    await self.on_notify(response)
        except Exception:
            logger.exception("Heartbeat execution failed")

    async def trigger_now(self) -> str | None:
        """手动触发一次心跳检查。"""
        content = self._read_heartbeat_file()
        if not content:
            return None
        action, tasks = await self._decide(content)
        if action != "run" or not self.on_execute:
            return None
        return await self.on_execute(tasks)
