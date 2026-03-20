"""Prompt and media context builder for the main-brain layer."""

from __future__ import annotations

import base64
import json
import mimetypes
from datetime import datetime
from pathlib import Path

from emoticorebot.config.schema import MemoryConfig, ProvidersConfig
from emoticorebot.memory.retrieval import MemoryRetrieval
from emoticorebot.reflection.cognitive import CognitiveEvent


class MainBrainContextBuilder:
    def __init__(
        self,
        workspace: Path,
        *,
        memory_config: MemoryConfig | None = None,
        providers_config: ProvidersConfig | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.memory = MemoryRetrieval(
            self.workspace,
            memory_config=memory_config,
            providers_config=providers_config,
        )

    def close(self) -> None:
        self.memory.close()

    def build_task_memory_bundle(self, *, query: str, limit: int = 6) -> dict[str, list[dict[str, object]]]:
        return self.memory.build_task_memory_bundle(query=query, limit=limit)

    def build_main_brain_system_prompt(self, *, query: str = "", world_state: object | None = None) -> str:
        parts = [
            "# Main Brain",
            f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "你是统一主脑。你负责理解、决策、最终表达；execution 只负责执行，不对用户说话。",
            "不要输出工具轨迹，不要假装执行已经完成；需要执行时，先明确交给 execution。",
        ]

        agents = self._read_file("AGENTS.md")
        if agents:
            parts.append("## 协作规则\n" + agents)
        soul = self._read_file("SOUL.md")
        if soul:
            parts.append("## SOUL\n" + soul)
        user = self._read_file("USER.md")
        if user:
            parts.append("## USER\n" + user)
        state = self._read_file("current_state.md")
        if state:
            parts.append("## 当前状态\n" + state)
        session_world_state = self._build_session_world_state(world_state)
        if session_world_state:
            parts.append(session_world_state)

        memory_context = self.memory.build_main_brain_context(query=query, limit=8)
        if memory_context:
            parts.append(memory_context)

        cognitive_sections = CognitiveEvent.build_cognitive_sections(self.workspace, query=query)
        parts.extend(cognitive_sections)
        return "\n\n---\n\n".join(part for part in parts if part)

    def build_media_context(self, media: list[str] | None) -> list[dict[str, object]]:
        if not media:
            return []
        items: list[dict[str, object]] = []
        for path_str in media:
            if path_str.startswith(("http://", "https://", "data:")):
                items.append({"type": "image_url", "image_url": {"url": path_str}})
                continue
            path = Path(path_str)
            if not path.exists():
                continue
            mime, _ = mimetypes.guess_type(str(path))
            if not mime:
                continue
            data = base64.b64encode(path.read_bytes()).decode()
            if mime.startswith("image/"):
                items.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
            elif mime == "application/pdf":
                items.append({"type": "text", "text": f"[PDF attachment: {path.name}]"})
        return items

    def _read_file(self, filename: str) -> str:
        path = self.workspace / filename
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()

    @staticmethod
    def _build_session_world_state(world_state: object | None) -> str:
        if world_state is None:
            return ""
        if hasattr(world_state, "model_dump"):
            payload = world_state.model_dump(exclude_none=True)
        elif isinstance(world_state, dict):
            payload = dict(world_state)
        else:
            return ""

        lines: list[str] = ["## SessionWorldState"]
        conversation_phase = str(payload.get("conversation_phase", "") or "").strip()
        if conversation_phase:
            lines.append(f"conversation_phase={conversation_phase}")
        foreground_task_id = str(payload.get("foreground_task_id", "") or "").strip()
        if foreground_task_id:
            lines.append(f"foreground_task_id={foreground_task_id}")

        background_task_ids = [str(item).strip() for item in list(payload.get("background_task_ids", []) or []) if str(item).strip()]
        if background_task_ids:
            lines.append("background_task_ids=" + json.dumps(background_task_ids, ensure_ascii=False))

        user_state = payload.get("user_state")
        if isinstance(user_state, dict) and user_state:
            lines.append("user_state=" + json.dumps(user_state, ensure_ascii=False, sort_keys=True))

        active_topics = [str(item).strip() for item in list(payload.get("active_topics", []) or []) if str(item).strip()]
        if active_topics:
            lines.append("active_topics=" + json.dumps(active_topics, ensure_ascii=False))

        reply_strategy = payload.get("reply_strategy")
        if isinstance(reply_strategy, dict) and reply_strategy:
            lines.append("reply_strategy=" + json.dumps(reply_strategy, ensure_ascii=False, sort_keys=True))

        tasks = payload.get("tasks")
        if isinstance(tasks, dict) and tasks:
            task_lines: list[str] = []
            for task_id, task in list(tasks.items())[:5]:
                if not isinstance(task, dict):
                    continue
                title = str(task.get("title", "") or "").strip()
                status = str(task.get("status", "") or "").strip()
                update = str(
                    task.get("last_user_visible_update", "")
                    or (list(task.get("recent_observations", []) or [])[-1] if list(task.get("recent_observations", []) or []) else "")
                    or ""
                ).strip()
                summary = f"- {task_id}: status={status or 'pending'}"
                if title:
                    summary += f", title={title}"
                if update:
                    summary += f", update={update}"
                task_lines.append(summary)
            if task_lines:
                lines.append("tasks:")
                lines.extend(task_lines)

        risk_flags = [str(item).strip() for item in list(payload.get("risk_flags", []) or []) if str(item).strip()]
        if risk_flags:
            lines.append("risk_flags=" + json.dumps(risk_flags, ensure_ascii=False))

        return "\n".join(lines) if len(lines) > 1 else ""


__all__ = ["MainBrainContextBuilder"]
