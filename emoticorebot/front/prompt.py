"""Prompt assembly for the front service."""

from __future__ import annotations

from pathlib import Path

from emoticorebot.state.schemas import MemoryView


class FrontPromptBuilder:
    """Build prompts for fast user-facing replies."""

    def __init__(self, workspace: Path):
        self.workspace = workspace

    def build_user_prompt(self, *, user_text: str, memory: MemoryView) -> str:
        sections: list[str] = []
        needs_verification = self.requires_verification(user_text)
        if needs_verification:
            sections.extend(
                [
                    "## 回复约束",
                    "这是一个需要核实事实的请求。你只能表达会查看、会处理、会继续跟进，不能提前判断文件存在与否、内容是什么、命令结果是什么、错误原因是什么。",
                    "",
                ]
            )
        sections.extend(
            [
                "## 用户输入",
                user_text.strip(),
            ]
        )
        user_anchor = str(memory.projections.get("user_anchor", "") or "").strip()
        soul_anchor = str(memory.projections.get("soul_anchor", "") or "").strip()
        current_state = str(memory.current_state or "").strip()
        if soul_anchor:
            sections.extend(["", "## 灵魂锚点", soul_anchor])
        if user_anchor:
            sections.extend(["", "## 用户画像", user_anchor])
        if current_state:
            sections.extend(["", "## 当前状态", current_state])
        if not needs_verification:
            recent_dialogue = list(memory.raw_layer.get("recent_dialogue", []) or [])
            if recent_dialogue:
                lines = []
                for row in recent_dialogue[-6:]:
                    role = str(row.get("role", "") or "").strip() or "unknown"
                    content = str(row.get("content", "") or "").strip()
                    if content:
                        lines.append(f"{role}: {content}")
                if lines:
                    sections.extend(["", "## 最近对话", "\n".join(lines)])
            recent_tools = list(memory.raw_layer.get("recent_tools", []) or [])
            if recent_tools:
                lines = []
                for row in recent_tools[-4:]:
                    role = str(row.get("tool_name", "") or "").strip() or "tool"
                    content = str(row.get("content", "") or "").strip()
                    if content:
                        lines.append(f"{role}: {content}")
                if lines:
                    sections.extend(["", "## 最近工具摘要", "\n".join(lines)])
            cognitive_layer = list(memory.cognitive_layer or [])
            if cognitive_layer:
                lines = []
                for row in cognitive_layer[-4:]:
                    summary = str(row.get("summary", "") or "").strip()
                    outcome = str(row.get("outcome", "") or "").strip()
                    if summary and outcome:
                        lines.append(f"- [{outcome}] {summary}")
                    elif summary:
                        lines.append(f"- {summary}")
                if lines:
                    sections.extend(["", "## 认知摘要", "\n".join(lines)])
            long_term_summary = str(memory.long_term_layer.get("summary", "") or "").strip()
            if long_term_summary:
                sections.extend(["", "## 长期记忆摘要", long_term_summary])
        return "\n".join(part for part in sections if part is not None)

    def requires_verification(self, user_text: str) -> bool:
        text = str(user_text or "").strip().lower()
        if not text:
            return False
        keywords = [
            "读取",
            "读一下",
            "看看",
            "检查",
            "分析",
            "搜索",
            "查看",
            "文件",
            "代码",
            "日志",
            "命令",
            "网页",
            "内容",
            "错误",
            "是否存在",
            "有没有",
            "read ",
            "check ",
            "search ",
            "file",
            "code",
            "log",
            "command",
            "error",
            "content",
        ]
        return any(keyword in text for keyword in keywords)
