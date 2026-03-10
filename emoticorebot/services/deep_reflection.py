"""Periodic deep reflection for unified long-term memory."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any

from loguru import logger

from emoticorebot.config.schema import MemoryConfig, ProvidersConfig
from emoticorebot.memory import MemoryStore
from emoticorebot.services.skill_materializer import SkillMaterializer
from emoticorebot.utils.llm_utils import extract_message_text


@dataclass(frozen=True)
class DeepReflectionResult:
    summary: str = ""
    memory_ids: list[str] = field(default_factory=list)
    memory_count: int = 0
    skill_hint_count: int = 0
    materialized_skills: list[str] = field(default_factory=list)
    materialized_skill_count: int = 0
    updated_soul: bool = False
    updated_user: bool = False


class DeepReflectionService:
    """Consolidate recent cognitive events into unified long-term memory."""

    _PROMPT = """
你是 `main_brain` 的深反思过程。

只返回 JSON，不要输出任何额外说明。

任务：
1. 阅读最近的 `cognitive_event`。
2. 只提炼真正稳定的长期价值。
3. 为统一长期记忆存储产出 `memory_candidates`。
4. 只有在重复执行模式明显可复用时，才产出 `skill_hint` 候选。

规则：
- 不要复制原始日志或大段文本。
- 优先给出稳定结论，而不是一次性噪声。
- 如果证据不足，直接返回空列表。
- 没有内容时，字符串字段返回 `""`，数组字段返回 `[]`，对象字段返回 `{}`。
- `user_updates` / `soul_updates` 的每一项都必须是一条可直接写入 Markdown 列表的稳定结论。
- `user_updates` 聚焦用户的稳定事实、偏好、目标、边界与长期沟通习惯。
- `soul_updates` 聚焦主脑的稳定风格、表达原则与长期策略修正。
- 不要输出标题、编号、解释前缀或多段内容，每一项都用单句表达。

最近的认知事件：
{event_block}

返回结构必须符合：
{{
  "summary": "",
  "memory_candidates": [
    {{
      "audience": "main_brain|executor|shared",
      "kind": "episodic|durable|procedural",
      "type": "user_fact|preference|goal|constraint|relationship|soul_trait|turn_insight|tool_experience|error_pattern|workflow_pattern|skill_hint",
      "summary": "",
      "content": "",
      "importance": 1,
      "confidence": 0.0,
      "stability": 0.0,
      "tags": [""],
      "payload": {{}}
    }}
  ],
  "user_updates": [""],
  "soul_updates": [""],
  "skill_hints": [
    {{
      "summary": "",
      "content": "",
      "trigger": "",
      "hint": "",
      "skill_name": ""
    }}
  ]
}}

字段说明：
- `summary`：这一阶段的高层总结。
- `memory_candidates`：真正值得进入统一长期记忆的候选列表，没有就返回空数组。
- `user_updates`：对用户整体画像的更新候选，没有就返回空数组；每一项都应像 `用户更喜欢先讨论架构，再进入实现细节。` 这样可直接落盘。
- `soul_updates`：对主脑稳定风格的更新候选，没有就返回空数组；每一项都应像 `复杂任务中先收敛判断，再交给 executor 执行。` 这样可直接落盘。
- `skill_hints`：只有在重复模式明显可复用时才填写，没有就返回空数组。

`skill_hints` 字段说明：
- `summary`：一句话概括这个技能提示。
- `content`：更完整的说明。
- `trigger`：什么情况下应触发。
- `hint`：给 executor 的紧凑提示。
- `skill_name`：未来沉淀为技能时的名称。

示例：
{{
  "summary": "近期多轮任务显示，复杂问题更适合由 executor 内部收敛后再交回主脑。",
  "memory_candidates": [
    {{
      "audience": "executor",
      "kind": "procedural",
      "type": "workflow_pattern",
      "summary": "复杂任务适合走最终结果式执行链路",
      "content": "当任务需要多步分析和工具配合时，executor 应优先在内部收敛，再把最终结果返回给 main_brain。",
      "importance": 8,
      "confidence": 0.88,
      "stability": 0.81,
      "tags": ["workflow", "executor"],
      "payload": {{
        "goal_cluster": "complex_execution",
        "tool_sequence": ["analysis", "tool", "summary"],
        "preconditions": ["需要多步执行"],
        "steps_summary": "主脑决策，executor 内部收敛后返回最终结果",
        "sample_size": 4,
        "success_rate": 0.8
      }}
    }}
  ],
  "user_updates": [],
  "soul_updates": [],
  "skill_hints": [
    {{
      "summary": "复杂任务优先走最终结果式执行",
      "content": "对于复杂任务，优先让 executor 在单次执行中收敛到最终结果。",
      "trigger": "需要多步执行或工具组合时",
      "hint": "减少中间汇报，优先给最终结果。",
      "skill_name": "final-result-execution"
    }}
  ]
}}
""".strip()

    _AUTO_SECTION_TITLE = "## 深反思沉淀（自动维护）"
    _SOUL_MARKER_START = "<!-- DEEP_REFLECTION_SOUL_START -->"
    _SOUL_MARKER_END = "<!-- DEEP_REFLECTION_SOUL_END -->"
    _USER_MARKER_START = "<!-- DEEP_REFLECTION_USER_START -->"
    _USER_MARKER_END = "<!-- DEEP_REFLECTION_USER_END -->"

    def __init__(
        self,
        workspace: Path,
        llm: Any,
        *,
        memory_config: MemoryConfig | None = None,
        providers_config: ProvidersConfig | None = None,
    ):
        self.workspace = workspace
        self.llm = llm
        self.memory_store = MemoryStore(
            workspace,
            memory_config=memory_config,
            providers_config=providers_config,
        )
        self.skill_materializer = SkillMaterializer(workspace, self.memory_store)

    async def run_cycle(self, events: list[dict[str, Any]]) -> DeepReflectionResult:
        if not events:
            return DeepReflectionResult()

        fallback = self._fallback_payload(events)
        if not self.llm:
            return self._persist_payload(fallback)

        prompt = self._PROMPT.format(event_block=self._build_event_block(events))
        try:
            response = await self.llm.ainvoke([{"role": "user", "content": prompt}])
            parsed = self._extract_json(extract_message_text(response))
        except Exception:
            parsed = None

        payload = self._normalize_payload(parsed if isinstance(parsed, dict) else fallback)
        if not payload["memory_candidates"] and fallback["memory_candidates"]:
            payload = fallback
        return self._persist_payload(payload)

    def _persist_payload(self, payload: dict[str, Any]) -> DeepReflectionResult:
        memory_candidates = list(payload.get("memory_candidates", []) or [])
        memory_ids = self.memory_store.append_many(memory_candidates)
        skill_hint_count = sum(1 for record in memory_candidates if str(record.get("type", "")) == "skill_hint")
        materialization = self.skill_materializer.materialize_from_memory()
        updated_user = self.write_managed_reflection_section(
            filename="USER.md",
            updates=payload.get("user_updates"),
            marker_start=self._USER_MARKER_START,
            marker_end=self._USER_MARKER_END,
            intro="以下条目沉淀用户的稳定画像，由 `deep_reflection` 自动维护。",
        )
        updated_soul = self.write_managed_reflection_section(
            filename="SOUL.md",
            updates=payload.get("soul_updates"),
            marker_start=self._SOUL_MARKER_START,
            marker_end=self._SOUL_MARKER_END,
            intro="以下条目沉淀主脑的稳定风格与长期策略，由 `deep_reflection` 自动维护。",
        )
        return DeepReflectionResult(
            summary=str(payload.get("summary", "") or "").strip(),
            memory_ids=memory_ids,
            memory_count=len(memory_ids),
            skill_hint_count=skill_hint_count,
            materialized_skills=list(materialization.skill_names),
            materialized_skill_count=int(materialization.created_count + materialization.updated_count),
            updated_soul=updated_soul,
            updated_user=updated_user,
        )

    def write_managed_reflection_section(
        self,
        *,
        filename: str,
        updates: Any,
        marker_start: str,
        marker_end: str,
        intro: str,
        section_title: str | None = None,
        max_entries: int | None = None,
    ) -> bool:
        normalized_updates = self._normalize_anchor_updates(updates)
        if not normalized_updates:
            return False

        current = self._ensure_md_file(filename)
        existing_updates = self._extract_managed_updates(
            current,
            marker_start=marker_start,
            marker_end=marker_end,
        )
        merged_updates = self._merge_updates(existing_updates, normalized_updates)
        if max_entries is not None and max_entries > 0:
            merged_updates = merged_updates[-max_entries:]
        block = self._render_managed_block(
            section_title=section_title or self._AUTO_SECTION_TITLE,
            marker_start=marker_start,
            marker_end=marker_end,
            intro=intro,
            updates=merged_updates,
        )
        updated = self._replace_or_append_managed_block(
            current,
            marker_start=marker_start,
            marker_end=marker_end,
            block=block,
        )
        if updated == current:
            return False
        return self._safe_write_text(self.workspace / filename, updated)

    def _ensure_md_file(self, filename: str) -> str:
        target = self.workspace / filename
        if target.exists():
            return target.read_text(encoding="utf-8")
        template = self._load_template(filename)
        if template:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(template, encoding="utf-8")
            return template
        return ""

    @staticmethod
    def _load_template(filename: str) -> str:
        try:
            return (files("emoticorebot") / "templates" / filename).read_text(encoding="utf-8")
        except Exception:
            return ""

    @staticmethod
    def _normalize_anchor_updates(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        items: list[str] = []
        for item in value:
            text = str(item or "").strip()
            text = re.sub(r"^[-*•]\s*", "", text)
            text = re.sub(r"^\d+[.)、]\s*", "", text)
            text = " ".join(text.split())
            if text and text not in items:
                items.append(text)
        return items[:8]

    @staticmethod
    def _extract_managed_updates(
        text: str,
        *,
        marker_start: str,
        marker_end: str,
    ) -> list[str]:
        pattern = re.compile(rf"{re.escape(marker_start)}([\s\S]*?){re.escape(marker_end)}")
        match = pattern.search(text or "")
        if not match:
            return []
        items: list[str] = []
        for line in match.group(1).splitlines():
            stripped = line.strip()
            if not stripped.startswith("- "):
                continue
            value = stripped[2:].strip()
            if value and value not in items:
                items.append(value)
        return items

    @staticmethod
    def _merge_updates(existing: list[str], incoming: list[str]) -> list[str]:
        merged: list[str] = []
        for item in [*existing, *incoming]:
            value = str(item or "").strip()
            if value and value not in merged:
                merged.append(value)
        return merged

    def _render_managed_block(
        self,
        *,
        section_title: str,
        marker_start: str,
        marker_end: str,
        intro: str,
        updates: list[str],
    ) -> str:
        lines = [
            marker_start,
            section_title,
            f"> {intro}",
            "",
            *(f"- {item}" for item in updates),
            marker_end,
        ]
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _replace_or_append_managed_block(
        current: str,
        *,
        marker_start: str,
        marker_end: str,
        block: str,
    ) -> str:
        pattern = re.compile(rf"{re.escape(marker_start)}[\s\S]*?{re.escape(marker_end)}")
        stripped_block = block.strip()
        if pattern.search(current or ""):
            updated = pattern.sub(stripped_block, current, count=1)
        else:
            base = (current or "").rstrip()
            updated = f"{base}\n\n{stripped_block}" if base else stripped_block
        return updated.rstrip() + "\n"

    def _normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        candidates = self._normalize_candidates(payload.get("memory_candidates"))
        skill_hints = self._normalize_skill_hints(payload.get("skill_hints"))
        return {
            "summary": str(payload.get("summary", "") or "").strip(),
            "memory_candidates": [*candidates, *skill_hints],
            "user_updates": self._normalize_str_list(payload.get("user_updates")),
            "soul_updates": self._normalize_str_list(payload.get("soul_updates")),
        }

    def _fallback_payload(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []
        skill_hints: list[dict[str, Any]] = []

        tool_events = [event for event in events if (event.get("executor") or {}).get("used")]
        if len(tool_events) >= 2:
            candidates.append(
                {
                    "audience": "executor",
                    "kind": "procedural",
                    "type": "workflow_pattern",
                    "summary": "近期多轮任务中持续使用执行链路解决问题。",
                    "content": "最近多轮任务都依赖 executor 执行并由 main_brain 统一收口，适合继续保持最终结果式返回。",
                    "importance": 7,
                    "confidence": 0.76,
                    "stability": 0.68,
                    "tags": ["workflow", "executor"],
                    "payload": {
                        "goal_cluster": "general_execution",
                        "tool_sequence": [],
                        "preconditions": ["需要外部工具或多步执行"],
                        "steps_summary": "主脑决策，executor 完成执行并返回最终结果。",
                        "sample_size": len(tool_events),
                        "success_rate": 0.7,
                    },
                }
            )
            skill_hints.append(
                {
                    "summary": "复杂任务默认走最终结果式执行链路",
                    "content": "遇到复杂任务时，executor 优先在单次执行内完成收敛，再把最终结果交回 main_brain。",
                    "trigger": "需要多步执行或工具组合时",
                    "hint": "减少中间态汇报，优先收敛到最终结果。",
                    "skill_name": "final-result-execution",
                }
            )

        return {
            "summary": "已对近期多轮认知事件完成一次深反思。",
            "memory_candidates": candidates,
            "user_updates": [],
            "soul_updates": [],
            "skill_hints": skill_hints,
        }

    @staticmethod
    def _build_event_block(events: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for event in events[-12:]:
            event_id = str(event.get("id", "") or "")
            timestamp = str(event.get("timestamp", "") or "")[:19]
            user_input = str(event.get("user_input", "") or "").strip()
            assistant_output = str(event.get("assistant_output", "") or "").strip()
            turn_reflection = event.get("turn_reflection") if isinstance(event.get("turn_reflection"), dict) else {}
            executor = event.get("executor") if isinstance(event.get("executor"), dict) else {}
            lines.append(
                "- "
                f"{event_id} [{timestamp}] 用户={DeepReflectionService._compact(user_input, 80)} "
                f"主脑回复={DeepReflectionService._compact(assistant_output, 80)} "
                f"反思摘要={DeepReflectionService._compact(str(turn_reflection.get('summary', '') or ''), 80)} "
                f"执行状态={str(executor.get('status', 'none') or 'none')}"
            )
        return "\n".join(lines)

    @staticmethod
    def _normalize_candidates(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        records: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            summary = str(item.get("summary", "") or "").strip()
            content = str(item.get("content", "") or "").strip()
            if not summary and not content:
                continue
            records.append(
                {
                    "audience": str(item.get("audience", "shared") or "shared").strip(),
                    "kind": str(item.get("kind", "durable") or "durable").strip(),
                    "type": str(item.get("type", "turn_insight") or "turn_insight").strip(),
                    "summary": summary or DeepReflectionService._compact(content, 120),
                    "content": content or summary,
                    "importance": int(item.get("importance", 6) or 6),
                    "confidence": float(item.get("confidence", 0.78) or 0.78),
                    "stability": float(item.get("stability", 0.72) or 0.72),
                    "tags": DeepReflectionService._normalize_str_list(item.get("tags")),
                    "payload": dict(item.get("payload", {}) or {}),
                }
            )
        return records[:8]

    @staticmethod
    def _normalize_skill_hints(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        records: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            summary = str(item.get("summary", "") or "").strip()
            content = str(item.get("content", "") or "").strip()
            hint = str(item.get("hint", "") or "").strip()
            trigger = str(item.get("trigger", "") or "").strip()
            if not any((summary, content, hint, trigger)):
                continue
            skill_name = str(item.get("skill_name", "") or "").strip() or "unnamed-skill"
            records.append(
                {
                    "audience": "executor",
                    "kind": "procedural",
                    "type": "skill_hint",
                    "summary": summary or DeepReflectionService._compact(content or hint, 120),
                    "content": content or hint or summary,
                    "importance": 7,
                    "confidence": 0.8,
                    "stability": 0.85,
                    "tags": ["skill", "hint"],
                    "payload": {
                        "skill_id": f"skill_{re.sub(r'[^a-z0-9\u4e00-\u9fff]+', '_', skill_name.lower()).strip('_') or 'hint'}",
                        "skill_name": skill_name,
                        "trigger": trigger,
                        "hint": hint or content or summary,
                        "applies_to_tools": [],
                    },
                }
            )
        return records[:4]

    @staticmethod
    def _normalize_str_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        items: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in items:
                items.append(text)
        return items[:6]

    @staticmethod
    def _compact(text: str, limit: int) -> str:
        compact = " ".join(str(text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1] + "…"

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        raw = str(text or "").strip()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            return None
        try:
            parsed = json.loads(match.group())
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    @staticmethod
    def _safe_write_text(target: Path, content: str) -> bool:
        backup = target.with_suffix(target.suffix + ".bak")
        temp = target.with_suffix(target.suffix + ".tmp")
        previous = target.read_text(encoding="utf-8") if target.exists() else None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if previous is not None:
                backup.write_text(previous, encoding="utf-8")
            temp.write_text(content, encoding="utf-8")
            temp.replace(target)
            return True
        except Exception as exc:
            logger.warning("DeepReflectionService safe write failed for {}: {}", target.name, exc)
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


__all__ = ["DeepReflectionResult", "DeepReflectionService"]
