"""Context builder - EQ system prompt 构建器。

当前主链只保留 EQ prompt 组装：

  EQ 模板 → build_eq_system_prompt()
    - SOUL.md（人格锚点）
    - USER.md（用户认知）
    - 结构化记忆检索（relational / affective / reflective / episodic）

依赖注入：MemoryFacade 由 FusionRuntime 注入（避免重复初始化）。
"""

from __future__ import annotations

import base64
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Any

from emoticorebot.core.skills import SkillsLoader
from emoticorebot.memory.memory_facade import MemoryFacade
from emoticorebot.memory.retriever import MemoryRetriever


class ContextBuilder:
    """EQ 能力模板的上下文组装器。"""

    def __init__(self, workspace: Path, memory_facade: MemoryFacade | None = None):
        self.workspace = workspace
        self.memory_facade = memory_facade or MemoryFacade(workspace)
        self.memory = MemoryRetriever(self.memory_facade)
        self.skills = SkillsLoader(workspace)

    # ─────────────────────────────────────────────────────────────────────────
    # EQ 模板
    # ─────────────────────────────────────────────────────────────────────────

    def build_eq_system_prompt(
        self,
        query: str = "",
        current_emotion: str = "平静",
        pad_state: tuple[float, float, float] | None = None,
        internal_iq_summaries: list[str] | None = None,
    ) -> str:
        parts = [self._get_eq_identity()]

        # 加载 EQ 执行规则（从 AGENTS.md）
        eq_rules = self._load_eq_rules()
        if eq_rules:
            parts.append(f"## EQ 执行规则\n\n{eq_rules}")

        soul = self._load_file("SOUL.md")
        if soul:
            parts.append(f"## 人格设定（SOUL）\n\n{soul}")

        user = self._load_file("USER.md")
        if user:
            parts.append(f"## 用户认知（USER）\n\n{user}")

        state = self._load_file("current_state.md")
        if state:
            parts.append(f"## 当前状态\n\n{state}")

        iq_summaries = [str(item).strip() for item in (internal_iq_summaries or []) if str(item).strip()]
        if iq_summaries:
            parts.append("## 历史内部摘要\n\n" + "\n".join(f"- {item}" for item in iq_summaries[:5]))

        parts.extend(
            self.memory.build_eq_sections(
                query=query,
                current_emotion=current_emotion,
                pad_state=pad_state,
            )
        )

        return "\n\n---\n\n".join(parts)

    # ─────────────────────────────────────────────────────────────────────────
    # Identity headers
    # ─────────────────────────────────────────────────────────────────────────

    def _get_eq_identity(self) -> str:
        return f"""# 💛 EQ 情感层（System 1 — 快系统）

你是 EQ 主导层，负责理解用户、判断意图、决定是否委托 IQ，并最终对用户表达。

## 当前时间
{self._get_datetime_str()}

## 职责
1. 你处理的是 user ↔ EQ 这条对外会话
2. 你结合 SOUL.md、USER.md、当前状态与 EQ 记忆来理解用户
3. 你判断当前输入是闲聊、信息请求，还是需要委托 IQ 的复杂问题
4. 只有在需要事实核查、工具执行、复杂规划时，才委托 IQ
5. IQ 只是内部执行顾问，你拥有最终对外表达权

## 边界
1. 不直接输出原始日志、JSON、工具结果或内部分析痕迹
2. 不把 EQ ↔ IQ 的内部讨论当成对用户说过的话
3. 不把纯执行层资料当成人格、关系或陪伴记忆
4. 所有对外表达都必须由你生成，并保持与 SOUL.md 一致

## EQ 记忆规则
1. 与用户关系、偏好、情绪连续性有关的信息属于 EQ 记忆（`memory/eq/`）
2. 与事实执行、资料沉淀、复用知识有关的信息不属于 EQ 记忆范围
3. 根据 PAD 情绪状态、关系记忆和用户语境调整语气"""

    @staticmethod
    def _get_datetime_str() -> str:
        import time as _time
        now = datetime.now()
        tz = _time.strftime("%Z") or "UTC"
        weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
        return now.strftime(f"%Y-%m-%d %H:%M {weekday}（{tz}）")

    def _load_file(self, filename: str) -> str:
        path = self.workspace / filename
        if path.exists():
            try:
                return path.read_text(encoding="utf-8").strip()
            except Exception:
                return ""
        return ""

    def _load_eq_rules(self) -> str:
        """从 AGENTS.md 加载 EQ 执行规则"""
        # 尝试从 workspace 加载
        content = self._load_file("AGENTS.md")
        if content and "# EQ 执行层规则" in content:
            eq_section = content.split("# EQ 执行层规则")[-1]
            if "---" in eq_section:
                eq_section = eq_section.split("---")[0]
            return eq_section.strip()

        # 回退：从包模板加载
        try:
            from importlib.resources import files
            pkg_content = (files("emoticorebot") / "templates" / "AGENTS.md").read_text(encoding="utf-8")
            if "# EQ 执行层规则" in pkg_content:
                eq_section = pkg_content.split("# EQ 执行层规则")[-1]
                if "---" in eq_section:
                    eq_section = eq_section.split("---")[0]
                return eq_section.strip()
        except Exception:
            pass

        return ""

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        current_emotion: str = "平静",
        pad_state: tuple[float, float, float] | None = None,
        media: list[str] | None = None,
        internal_iq_summaries: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """构建 LLM 调用所需的完整消息列表。

        Args:
            history: 历史消息列表，每条为 {"role": ..., "content": ...}
            current_message: 当前用户消息
            current_emotion: 当前情绪
            pad_state: PAD 情绪向量
        """
        system = self.build_eq_system_prompt(
            query=current_message,
            current_emotion=current_emotion,
            pad_state=pad_state,
            internal_iq_summaries=internal_iq_summaries,
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]

        for turn in history:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

        media_items = self.build_media_context(media)
        if media_items:
            user_content: Any = [{"type": "text", "text": current_message}] + media_items
        else:
            user_content = current_message
        messages.append({"role": "user", "content": user_content})
        return messages

    def build_media_context(self, media: list[str] | None) -> list[dict[str, Any]]:
        """将本地文件路径转换为 LangChain 多模态消息内容。"""
        if not media:
            return []
        items: list[dict[str, Any]] = []
        for path_str in media:
            path = Path(path_str)
            if not path.exists():
                continue
            mime, _ = mimetypes.guess_type(str(path))
            if not mime:
                continue
            try:
                data = base64.b64encode(path.read_bytes()).decode()
                if mime.startswith("image/"):
                    items.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{data}"},
                    })
                elif mime == "application/pdf":
                    items.append({
                        "type": "text",
                        "text": f"[PDF attachment: {path.name}]",
                    })
            except Exception:
                pass
        return items
