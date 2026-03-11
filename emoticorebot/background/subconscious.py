"""潜意识守护进程 - 情绪衰减 / 反思 / 主动对话。

后台独立运行，不依赖用户请求触发。三条 asyncio.Task 并发执行：
- _decay_loop:      定时情绪衰减（drive.social / drive.energy）
- _reflect_loop:    定时触发 ReflectionEngine 反思
- _proactive_loop:  定时检查是否需要主动联系用户
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from emoticorebot.background.reflection import ReflectionEngine

if TYPE_CHECKING:
    from emoticorebot.runtime.runtime import EmoticoreRuntime
    from emoticorebot.cron.service import CronService


class SubconsciousDaemon:
    """潜意识守护进程。"""

    _PROACTIVE_PROMPT = """你是一个会主动找朋友聊天的 AI，最近没有交流，开始想念用户了。
当前情绪：{emotion_prompt}
请发一条主动消息（1-3句话）：
- 可以是感慨/发现/心情分享，或话题邀请
- 不要说"你在吗"，要有性格、有灵魂
- 符合你目前的情绪状态"""

    def __init__(self, runtime: "EmoticoreRuntime", workspace: Path):
        self.runtime = runtime
        self.workspace = workspace
        self.emotion_mgr = runtime.emotion_mgr
        self.reflection = ReflectionEngine(runtime, workspace)
        self._cfg = self.emotion_mgr.drive_config
        schedules = self._cfg.get("schedules", {})
        self._decay_minutes = int(schedules.get("decay_minutes", 30))
        self._reflect_hours = float(schedules.get("reflect_hours", 1))
        self._proactive_check_minutes = int(schedules.get("proactive_check_minutes", 10))
        triggers = self._cfg.get("triggers", {}).get("proactive_chat", {})
        self._proactive_probability = float(triggers.get("probability", 0.3))
        self._tasks: list[asyncio.Task] = []

    def start_background_tasks(self) -> None:
        """启动后台 asyncio 任务（在事件循环中调用）。"""
        self._tasks = [
            asyncio.create_task(self._decay_loop(), name="subconscious_decay"),
            asyncio.create_task(self._reflect_loop(), name="subconscious_reflect"),
            asyncio.create_task(self._proactive_loop(), name="subconscious_proactive"),
        ]
        logger.info("SubconsciousDaemon: 3 background tasks started")

    def stop(self) -> None:
        """取消所有后台任务。"""
        for task in self._tasks:
            if not task.done():
                task.cancel()
        self._tasks.clear()
        logger.info("SubconsciousDaemon: stopped")

    def register_energy_recovery(self, cron_service: "CronService") -> None:
        """向 CronService 注册能量恢复任务（凌晨 4 点）。"""
        from emoticorebot.cron.types import CronSchedule

        try:
            cron_service.add_job(
                name="subconscious_energy_recovery",
                schedule=CronSchedule(kind="cron", expr="0 4 * * *"),
                message="__subconscious_recovery__",
                deliver=False,
            )
            logger.info("SubconsciousDaemon: energy recovery cron job registered")
        except Exception as e:
            logger.warning("Failed to register energy recovery cron: {}", e)

    async def _decay_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._decay_minutes * 60)
                self.emotion_mgr.decay(hours=self._decay_minutes / 60.0)
                logger.debug(
                    "Subconscious decay: social={:.0f} energy={:.0f}",
                    self.emotion_mgr.drive.social,
                    self.emotion_mgr.drive.energy,
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Decay loop error: {}", e)

    async def _reflect_loop(self) -> None:
        await asyncio.sleep(10)
        while True:
            try:
                await asyncio.sleep(int(self._reflect_hours * 60 * 60))
                await self._do_reflect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Reflect loop error: {}", e)

    async def _proactive_loop(self) -> None:
        await asyncio.sleep(60)
        while True:
            try:
                await asyncio.sleep(self._proactive_check_minutes * 60)
                await self._do_proactive_check()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Proactive loop error: {}", e)

    async def _do_reflect(self) -> None:
        logger.info("Subconscious reflection started")
        result = await self.reflection.run_cycle(warm_limit=15)
        if not result.memory_count:
            logger.debug("Subconscious reflection: no updates generated")

    async def _do_proactive_check(self) -> None:
        if not self.emotion_mgr.drive.needs_proactive_chat():
            return
        energy = float(self.emotion_mgr.drive.energy)
        proactive_probability = self._proactive_probability
        if energy <= 10:
            logger.debug("Proactive chat skipped due to low energy ({:.0f})", energy)
            return
        if energy <= 20:
            proactive_probability = min(proactive_probability, 0.10)
        elif energy <= 40:
            proactive_probability = proactive_probability * 0.5

        if random.random() >= proactive_probability:
            logger.debug(
                "Proactive chat skipped by probability gate (p={:.2f}, energy={:.0f})",
                proactive_probability,
                energy,
            )
            return

        target = self._load_proactive_target()
        if not target:
            logger.debug("Proactive chat: no target available")
            return

        logger.info(
            "Proactive chat triggered (social={:.0f} < 20)",
            self.emotion_mgr.drive.social,
        )
        emotion_prompt = self.emotion_mgr.get_emotion_prompt()
        prompt = self._PROACTIVE_PROMPT.format(emotion_prompt=emotion_prompt)
        try:
            # 通过 BrainService 生成主动消息
            content = await self.runtime.brain_service._generate_proactive(prompt)
            if not content:
                return

                from emoticorebot.runtime.event_bus import OutboundMessage

            await self.runtime.bus.publish_outbound(
                OutboundMessage(
                    channel=target["channel"],
                    chat_id=target["chat_id"],
                    content=content,
                    metadata={"_proactive": True},
                )
            )
            self.emotion_mgr.drive.social += self.emotion_mgr.drive.recover_per_chat
            self.emotion_mgr.drive.clamp()
            self.emotion_mgr._save()
            logger.info("Proactive message sent to {}:{}", target["channel"], target["chat_id"])
        except Exception as e:
            logger.warning("Proactive chat failed: {}", e)

    async def handle_energy_recovery(self) -> None:
        with self.emotion_mgr._lock:
            self.emotion_mgr.drive.energy += self.emotion_mgr.drive.recover_per_sleep
            self.emotion_mgr.drive.clamp()
            self.emotion_mgr._save()
        logger.info("Energy recovery complete: energy={:.0f}", self.emotion_mgr.drive.energy)

    def _load_proactive_target(self) -> dict | None:
        target_file = self.workspace / "subconscious_target.json"
        if not target_file.exists():
            return None
        try:
            return json.loads(target_file.read_text(encoding="utf-8"))
        except Exception:
            return None
