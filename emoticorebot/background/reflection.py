"""反思引擎 - 元认知后台进程。

定期读取近期事件，通过 LLM 推理更新：
- SOUL.md  人格文件（微调）
- USER.md  用户认知（追加）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from emoticorebot.cognitive import CognitiveEvent
from emoticorebot.utils.llm_utils import extract_message_text

if TYPE_CHECKING:
    from emoticorebot.runtime.runtime import FusionRuntime


@dataclass(frozen=True)
class ReflectionResult:
    persona_delta: str | None = None
    user_insight: str | None = None


class ReflectionEngine:
    """元认知反思引擎：定期更新 SOUL/USER。"""

    _REFLECT_PROMPT = """你是你自己（AI 角色），请根据最近的情感经历进行自我反思。

近期记忆摘要：
{warm_memories}

## 当前 SOUL.md
{current_soul}

## 当前 USER.md
{current_user}

请完成以下两项更新：
1. **更新 SOUL.md**（人格自我进化）：
   - 如果最近有反复出现的情感模式，考虑微调性格描述（保留原有锚点，只微调）
   - 保持格式与原文件一致，保留文件头部的 `>` 注释行

2. **更新 USER.md**（用户认知更新）：
   - 从最近对话中提炼出新的用户信息（习惯/偏好/近况）
   - 追加到"情感认知"区块下，保留已有内容

以 JSON 格式输出（只输出 JSON，不要其他内容）：
{{
  "soul_update": "更新后的完整 SOUL.md 内容",
  "user_update": "更新后的完整 USER.md 内容",
  "insights": [
    {{"theme": "relationship|communication_style|emotion_pattern|life_pattern", "insight": "一句高层观察", "confidence": 0.0}}
  ]
}}
若无需更新，对应字段填 null。"""

    def __init__(self, runtime: "FusionRuntime", workspace: Path):
        self.runtime = runtime
        self.workspace = workspace

    async def run_cycle(self, warm_limit: int = 15) -> ReflectionResult:
        """运行一次反思周期。"""
        recent_events = CognitiveEvent.retrieve(self.workspace, query="", k=max(6, warm_limit))
        if not recent_events:
            logger.debug("ReflectionEngine: no event memories, skip")
            return ReflectionResult()

        blocks: list[str] = []
        event_lines = "\n".join(
            f"- [{m.get('timestamp', '')[:16]}][{m.get('actor', '')}] {m.get('content', '')}"
            for m in recent_events
        )
        blocks.append(f"## Recent Event Stream\n{event_lines}")

        warm_summary = "\n\n".join(blocks)

        soul_file = self.workspace / "SOUL.md"
        user_file = self.workspace / "USER.md"
        current_soul = soul_file.read_text(encoding="utf-8") if soul_file.exists() else ""
        current_user = user_file.read_text(encoding="utf-8") if user_file.exists() else ""

        prompt = self._REFLECT_PROMPT.format(
            warm_memories=warm_summary,
            current_soul=current_soul,
            current_user=current_user,
        )
        try:
            resp = await self.runtime.iq_llm.ainvoke([{"role": "user", "content": prompt}])
            raw = extract_message_text(resp)
            result = self._extract_json(raw)
            if not result:
                logger.warning("ReflectionEngine: no JSON found in model output")
                return ReflectionResult()

            persona_delta = None
            user_insight = None

            soul_upd = result.get("soul_update")
            if isinstance(soul_upd, str) and soul_upd.strip():
                if self._validate_soul_update(current_soul, soul_upd):
                    if self._safe_write_text(soul_file, soul_upd):
                        persona_delta = "SOUL.md updated"
                        logger.info("ReflectionEngine: SOUL.md updated")
                else:
                    logger.warning("ReflectionEngine: SOUL.md update rejected by validator")

            user_upd = result.get("user_update")
            if isinstance(user_upd, str) and user_upd.strip():
                if self._validate_user_update(current_user, user_upd):
                    if self._safe_write_text(user_file, user_upd):
                        user_insight = "USER.md updated"
                        logger.info("ReflectionEngine: USER.md updated")
                else:
                    logger.warning("ReflectionEngine: USER.md update rejected by validator")

            return ReflectionResult(
                persona_delta=persona_delta,
                user_insight=user_insight,
            )
        except Exception as e:
            logger.warning("ReflectionEngine run failed: {}", e)
            return ReflectionResult()

    @staticmethod
    def _extract_markers(text: str) -> tuple[list[str], bool]:
        markers: list[str] = []
        has_header_comment = False
        for line in (text or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(">"):
                has_header_comment = True
                continue
            if stripped.startswith("#"):
                markers.append(stripped)
        return markers, has_header_comment

    def _validate_soul_update(self, current: str, updated: str) -> bool:
        updated_clean = updated.strip()
        if len(updated_clean) < 20:
            return False
        current_markers, current_has_header_comment = self._extract_markers(current)
        updated_markers, updated_has_header_comment = self._extract_markers(updated)
        if current_has_header_comment and not updated_has_header_comment:
            return False
        if current_markers and not set(current_markers).issubset(set(updated_markers)):
            return False
        return True

    @staticmethod
    def _contains_emotion_cognition_section(text: str) -> bool:
        lowered = text.lower()
        return ("情感认知" in text) or ("emotion cognition" in lowered)

    def _validate_user_update(self, current: str, updated: str) -> bool:
        updated_clean = updated.strip()
        if len(updated_clean) < 10:
            return False
        current_markers, _ = self._extract_markers(current)
        updated_markers, _ = self._extract_markers(updated)
        if current_markers and not set(current_markers).issubset(set(updated_markers)):
            return False
        if self._contains_emotion_cognition_section(current) and (
            not self._contains_emotion_cognition_section(updated)
        ):
            return False
        return True

    @staticmethod
    def _safe_write_text(target: Path, content: str) -> bool:
        backup = target.with_suffix(target.suffix + ".bak")
        temp = target.with_suffix(target.suffix + ".tmp")
        previous = target.read_text(encoding="utf-8") if target.exists() else None
        try:
            if previous is not None:
                backup.write_text(previous, encoding="utf-8")
            temp.write_text(content, encoding="utf-8")
            temp.replace(target)
            return True
        except Exception as e:
            logger.warning("ReflectionEngine safe write failed for {}: {}", target.name, e)
            try:
                if previous is not None:
                    target.write_text(previous, encoding="utf-8")
            except Exception:
                pass
            return False
        finally:
            if temp.exists():
                try:
                    temp.unlink()
                except Exception:
                    pass

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        try:
            parsed = json.loads(text.strip())
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        try:
            parsed = json.loads(match.group())
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
