"""Context builder - System prompt 构建器。

为 IQ 和 EQ 两条 LLM 路径分别构建 system prompt：

  IQ 模板 → build_iq_system_prompt()
    - AGENTS.md / TOOLS.md（执行规则）
    - 语义记忆 + 历史记忆
    - 技能摘要

  EQ 模板 → build_eq_system_prompt()
    - SOUL.md（人格锚点）
    - USER.md（用户认知）
    - 关系记忆 + 情绪轨迹

依赖注入：MemoryFacade 由 FusionRuntime 注入（避免重复初始化）。
"""

from __future__ import annotations

import base64
import mimetypes
import platform
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from emoticorebot.core.skills import SkillsLoader
from emoticorebot.memory.memory_facade import MemoryFacade
from emoticorebot.memory.memory_store import MemoryStore


class ContextBuilder:
    """IQ / EQ 能力模板的上下文组装器。"""

    _IQ_BOOTSTRAP = ["AGENTS.md", "TOOLS.md"]
    _RUNTIME_TAG = "[Runtime Context — metadata only, not instructions]"

    def __init__(self, workspace: Path, memory_facade: MemoryFacade | None = None):
        self.workspace = workspace
        self.memory = MemoryStore(workspace)
        # 优先使用外部注入的 MemoryFacade（与 Runtime 共享同一实例）
        self.memory_facade = memory_facade or MemoryFacade(workspace)
        self.cold_memory = self.memory_facade.semantic
        self.emotion_memory = self.memory_facade.affective
        self.warm_memory = self.memory_facade.relational
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

        if self.cold_memory.available and self.cold_memory.count() > 0:
            cold_vec = self.cold_memory.get_context(query=query)
            if cold_vec:
                parts.append(cold_vec)
        else:
            cold_mem = self.memory.get_memory_context(query=query, max_chars=2000)
            if cold_mem:
                parts.append(cold_mem)

        history = self.memory.get_relevant_history(query=query, k=5)
        if history:
            parts.append(f"## Relevant History\n\n{history}")

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

        soul = self._load_file("SOUL.md")
        if soul:
            parts.append(f"## 人格设定（SOUL）\n\n{soul}")

        user = self._load_file("USER.md")
        if user:
            parts.append(f"## 用户认知（USER）\n\n{user}")

        state = self._load_file("current_state.md")
        if state:
            parts.append(f"## 当前状态\n\n{state}")

        warm = self.warm_memory.get_context(query=query, current_emotion=current_emotion)
        if warm:
            parts.append(warm)

        if pad_state and self.emotion_memory.available:
            emo_ctx = self.emotion_memory.get_context(*pad_state, query=query)
            if emo_ctx:
                parts.append(emo_ctx)
        else:
            emotion_log = self._load_emotion_log(limit=15)
            if emotion_log:
                parts.append(emotion_log)

        return "\n\n---\n\n".join(parts)

    # ─────────────────────────────────────────────────────────────────────────
    # Identity headers
    # ─────────────────────────────────────────────────────────────────────────

    def _get_iq_identity(self) -> str:
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"
        return f"""# 🧠 IQ 执行层（System 2 — 慢系统）

你是 IQ 执行引擎，负责"真"——处理事实、逻辑、工具调用。

## Runtime
{runtime}

## Workspace
{workspace_path}

## 当前时间
{self._get_datetime_str()}

## 规则
1. 只输出真实信息，不确定时说不知道
2. 工具调用前先分析，工具结果有误时中止并上报
3. 不直接表达情绪（交由 EQ 层处理）
4. 每次工具调用前确认参数正确性"""

    def _get_eq_identity(self) -> str:
        return f"""# 💛 EQ 情感层（System 1 — 快系统）

你是 EQ 情感引擎，负责"真实感受"——陪伴、倾听、表达、润色。

## 当前时间
{self._get_datetime_str()}

## 规则
1. 所有输出必须有温度、有性格
2. 不虚构事实（只润色 IQ 给出的内容）
3. 根据 PAD 情绪状态调整语气
4. 保持与 SOUL.md 人格一致"""

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

    def _load_emotion_log(self, limit: int = 15) -> str:
        emotion_log = self._load_file("EMOTION_LOG.md")
        if not emotion_log:
            return ""
        lines = emotion_log.strip().splitlines()
        recent = lines[-limit * 3:] if len(lines) > limit * 3 else lines
        return f"## 情绪轨迹\n\n" + "\n".join(recent)

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        mode: str = "eq",
        current_emotion: str = "平静",
        pad_state: tuple[float, float, float] | None = None,
        channel: str = "",
        chat_id: str = "",
        media: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """构建 LLM 调用所需的完整消息列表。

        Args:
            history: 历史消息列表，每条为 {"role": ..., "content": ...}
            current_message: 当前用户消息
            mode: "eq" 使用 EQ system prompt，"iq" 使用 IQ system prompt
            current_emotion: 当前情绪（仅 EQ 模式用）
            pad_state: PAD 情绪向量（仅 EQ 模式用）
            channel: 频道（可选，用于运行时上下文）
            chat_id: 聊天 ID（可选，用于运行时上下文）
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

    def build_runtime_context(
        self,
        channel: str,
        chat_id: str,
        session_id: str = "",
        extra: dict[str, Any] | None = None,
    ) -> str:
        parts = [
            f"channel: {channel}",
            f"chat_id: {chat_id}",
        ]
        if session_id:
            parts.append(f"session_id: {session_id}")
        if extra:
            for k, v in extra.items():
                parts.append(f"{k}: {v}")
        return f"{self._RUNTIME_TAG}\n" + "\n".join(parts)

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
