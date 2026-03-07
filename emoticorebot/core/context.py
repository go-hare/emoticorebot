"""Context builder - System prompt 构建器。

为 IQ 和 EQ 两条 LLM 路径分别构建 system prompt：

  IQ 模板 → build_iq_system_prompt()
    - AGENTS.md / TOOLS.md（执行规则）
    - 结构化记忆检索（semantic / episodic / plans / reflective / events）
    - 技能摘要

  EQ 模板 → build_eq_system_prompt()
    - SOUL.md（人格锚点）
    - USER.md（用户认知）
    - 结构化记忆检索（relational / affective / reflective / episodic）

依赖注入：MemoryFacade 由 FusionRuntime 注入（避免重复初始化）。
"""

from __future__ import annotations

import base64
import mimetypes
import platform
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from emoticorebot.core.skills import SkillsLoader
from emoticorebot.memory.memory_facade import MemoryFacade
from emoticorebot.memory.retriever import MemoryRetriever


class ContextBuilder:
    """IQ / EQ 能力模板的上下文组装器。"""

    _IQ_BOOTSTRAP = ["AGENTS.md", "TOOLS.md"]

    def __init__(self, workspace: Path, memory_facade: MemoryFacade | None = None):
        self.workspace = workspace
        self.memory_facade = memory_facade or MemoryFacade(workspace)
        self.memory = MemoryRetriever(self.memory_facade)
        self.skills = SkillsLoader(workspace)

    # ─────────────────────────────────────────────────────────────────────────
    # IQ 模板
    # ─────────────────────────────────────────────────────────────────────────

    def build_iq_system_prompt(self, query: str = "") -> str:
        parts = [self._get_iq_identity()]

        for fname in self._IQ_BOOTSTRAP:
            content = self._load_file(fname)
            if content:
                parts.append(f"## {fname}\n\n{content}")

        state = self._load_file("current_state.md")
        if state:
            parts.append(f"## Current State\n\n{state}")

        parts.extend(self.memory.build_iq_sections(query=query))

        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        skills_summary = self.skills.build_skills_summary()
        if skills_summary:
            parts.append(
                "# Skills\n\n"
                "To use a skill, read its SKILL.md file using the read_file tool.\n\n"
                f"{skills_summary}"
            )

        return "\n\n---\n\n".join(parts)

    # ─────────────────────────────────────────────────────────────────────────
    # EQ 模板
    # ─────────────────────────────────────────────────────────────────────────

    def build_eq_system_prompt(
        self,
        query: str = "",
        current_emotion: str = "平静",
        pad_state: tuple[float, float, float] | None = None,
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

    def _get_iq_identity(self) -> str:
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"
        return f"""# 🧠 IQ 执行层（System 2 — 慢系统）

你是 IQ 理性参谋，负责"真"——处理事实、逻辑、工具调用，并回答 EQ 的内部问题。

## Runtime
{runtime}

## Workspace
{workspace_path}

## 当前时间
{self._get_datetime_str()}

## 规则
1. 你只对 EQ 负责，不直接对用户说话
2. 输出必须优先使用结构化 JSON，包含分析、证据、风险、缺参、建议动作
3. 工具调用前先分析，工具结果有误时中止并上报
4. 不直接表达情绪，不负责最终用户措辞
5. 若信息不足，明确指出缺失参数与风险，不要含糊其辞"""

    def _get_eq_identity(self) -> str:
        return f"""# 💛 EQ 情感层（System 1 — 快系统）

你是 EQ 主导层，负责"理解与主导"——陪伴、判断、向 IQ 提问、整合 IQ 结论，并最终对用户表达。

## 当前时间
{self._get_datetime_str()}

## 规则
1. 你拥有最终决策权，IQ 只是内部顾问
2. 先判断用户真正需要什么，再决定是否征询 IQ
3. 可以不完全采纳 IQ，但不能虚构事实
4. 所有对外表达都必须由你生成，并保持与 SOUL.md 一致
5. 根据 PAD 情绪状态、关系记忆和用户语境调整语气"""

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
        mode: str = "eq",
        current_emotion: str = "平静",
        pad_state: tuple[float, float, float] | None = None,
        media: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """构建 LLM 调用所需的完整消息列表。

        Args:
            history: 历史消息列表，每条为 {"role": ..., "content": ...}
            current_message: 当前用户消息
            mode: "eq" 使用 EQ system prompt，"iq" 使用 IQ system prompt
            current_emotion: 当前情绪（仅 EQ 模式用）
            pad_state: PAD 情绪向量（仅 EQ 模式用）
        """
        if mode == "iq":
            system = self.build_iq_system_prompt(query=current_message)
        else:
            system = self.build_eq_system_prompt(
                query=current_message,
                current_emotion=current_emotion,
                pad_state=pad_state,
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
