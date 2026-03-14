"""Context builder for the brain layer."""

from __future__ import annotations

import base64
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Any

from emoticorebot.agent.cognitive import CognitiveEvent
from emoticorebot.config.schema import MemoryConfig, ProvidersConfig
from emoticorebot.execution.skills import SkillsLoader
from emoticorebot.memory import MemoryStore


class ContextBuilder:
    """Assemble prompts and memory bundles for the brain layer."""

    def __init__(
        self,
        workspace: Path,
        *,
        memory_config: MemoryConfig | None = None,
        providers_config: ProvidersConfig | None = None,
    ):
        self.workspace = workspace
        self.skills = SkillsLoader(workspace)
        self.memory_store = MemoryStore(
            workspace,
            memory_config=memory_config,
            providers_config=providers_config,
        )

    def query_brain_memories(self, *, query: str, limit: int = 8) -> list[dict[str, Any]]:
        return self.memory_store.query(query, audiences=("brain", "shared"), limit=limit)

    def build_task_memory_bundle(self, *, query: str, limit: int = 6) -> dict[str, list[dict[str, Any]]]:
        return self.memory_store.build_task_bundle(query=query, limit=limit)

    def build_brain_system_prompt(
        self,
        query: str = "",
        current_emotion: str = "平静",
        pad_state: tuple[float, float, float] | None = None,
        internal_task_summaries: list[str] | None = None,
    ) -> str:
        parts = [self._get_brain_identity()]

        brain_rules = self._load_brain_rules()
        if brain_rules:
            parts.append(f"## Brain 规则\n\n{brain_rules}")

        soul = self._load_file("SOUL.md")
        if soul:
            parts.append(f"## 灵魂锚点（SOUL）\n\n{soul}")

        user = self._load_file("USER.md")
        if user:
            parts.append(f"## 用户锚点（USER）\n\n{user}")

        long_term_memory = self.memory_store.build_brain_context(query=query, limit=8)
        if long_term_memory:
            parts.append(long_term_memory)

        state = self._load_file("current_state.md")
        if state:
            parts.append(f"## 当前状态\n\n{state}")

        task_summaries = [
            str(item).strip() for item in (internal_task_summaries or []) if str(item).strip()
        ]
        if task_summaries:
            parts.append("## 最近任务摘要\n\n" + "\n".join(f"- {item}" for item in task_summaries[:5]))

        parts.extend(
            CognitiveEvent.build_cognitive_sections(
                self.workspace,
                query=query,
                current_emotion=current_emotion,
                pad_state=pad_state,
            )
        )

        return "\n\n---\n\n".join(parts)

    def _get_brain_identity(self) -> str:
        return f"""# Brain

你是这个 AI 系统唯一的主体。
你统一承担理性判断、情绪理解、决策控制、反思成长，以及最终对外表达。

## 当前时间
{self._get_datetime_str()}

## 核心职责
1. 处理所有用户可见对话。
2. 综合 `SOUL.md`、`USER.md`、统一长期 `memory`、当前状态和最近认知事件。
3. 判断当前轮应该直接回复，还是创建 `task` 并委托给 `central`。
4. 由你自己完成长期记忆检索；`central` 不允许直接检索长期记忆。
5. 只把与任务相关的执行经验、工具经验和技能提示传给 `central`。
6. 保持最终表达权，用户可见回复必须由你亲自完成。
7. 每轮结束后触发 `turn_reflection`，并决定是否需要 `deep_reflection`。

## 边界
1. 不要暴露原始日志、JSON、工具轨迹或内部思维过程。
2. 不要把运行时执行状态误当成稳定的长期记忆。
3. 不要让 `central` 变成第二人格或第二个对外说话者。
4. 在保持理性判断的同时，确保回复始终和 `SOUL.md` 一致。

## 架构取向
1. `brain` 是长期 `memory` 的唯一检索者。
2. `central` 是通用执行 agent，职责是返回任务结果与阶段性结论。
3. 长期记忆只有一个统一事实源：`memory.jsonl`。
4. 高频且稳定的执行模式，未来可以结晶为 `skills`。"""

    @staticmethod
    def _get_datetime_str() -> str:
        now = datetime.now()
        weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
        return now.strftime(f"%Y-%m-%d %H:%M {weekday}")

    def _load_file(self, filename: str) -> str:
        path = self.workspace / filename
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8").strip()
        except Exception:
            return ""

    def _load_brain_rules(self) -> str:
        content = self._load_file("AGENTS.md")
        extracted = self._extract_markdown_section(
            content,
            headings=[
                "# `brain` Brain 规则",
                "# brain Brain 规则",
                "# Brain 规则",
                "# Brain Rules",
            ],
        )
        if extracted:
            return extracted
        return self._default_brain_rules()

    @staticmethod
    def _extract_markdown_section(content: str, *, headings: list[str]) -> str:
        if not content:
            return ""
        for heading in headings:
            if heading not in content:
                continue
            section = content.split(heading, 1)[-1]
            if "\n# " in section:
                section = section.split("\n# ", 1)[0]
            if "\n---" in section:
                section = section.split("\n---", 1)[0]
            return section.strip()
        return ""

    @staticmethod
    def _default_brain_rules() -> str:
        return (
            "1. 默认以陪伴式理解为先，但同时保持高质量决策。\n"
            "2. 在决定是否创建 task 之前，先由 brain 自行检索长期记忆。\n"
            "3. 只有当 central 能明显提升正确性或完成度时，才进行委托。\n"
            "4. central 应接收紧凑的任务上下文包，并返回最终结果，而不是闲聊式中间状态。\n"
            "5. 每轮都触发 turn_reflection，只有在确实值得时才安排 deep_reflection。\n"
            "6. 稳定的用户信息、自我风格和关系结论应进入长期记忆或锚点，而不是停留在原始运行时日志中。"
        )

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        current_emotion: str = "平静",
        pad_state: tuple[float, float, float] | None = None,
        media: list[str] | None = None,
        internal_task_summaries: list[str] | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        system = self.build_brain_system_prompt(
            query=query if query is not None else current_message,
            current_emotion=current_emotion,
            pad_state=pad_state,
            internal_task_summaries=internal_task_summaries,
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]

        for turn in history:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

        media_items = self.build_media_context(media)
        if media_items:
            user_content: Any = [{"type": "text", "text": current_message}, *media_items]
        else:
            user_content = current_message
        messages.append({"role": "user", "content": user_content})
        return messages

    def build_media_context(self, media: list[str] | None) -> list[dict[str, Any]]:
        if not media:
            return []
        items: list[dict[str, Any]] = []
        for path_str in media:
            # Handle remote URLs (http/https) or data URIs directly
            if path_str.startswith(("http://", "https://", "data:")):
                items.append({"type": "image_url", "image_url": {"url": path_str}})
                continue

            # Handle local file paths
            path = Path(path_str)
            if not path.exists():
                continue
            mime, _ = mimetypes.guess_type(str(path))
            if not mime:
                continue
            try:
                data = base64.b64encode(path.read_bytes()).decode()
                if mime.startswith("image/"):
                    items.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{data}"},
                        }
                    )
                elif mime == "application/pdf":
                    items.append({"type": "text", "text": f"[PDF attachment: {path.name}]"})
            except Exception:
                pass
        return items


__all__ = ["ContextBuilder"]
