"""Prompt assembly for the front service."""

from __future__ import annotations

from pathlib import Path

from emoticorebot.affect import EmotionSignal
from emoticorebot.brain_kernel import MemoryView
from datetime import datetime

class FrontPromptBuilder:
    """Build prompts for fast user-facing replies."""

    def __init__(self, workspace: Path):
        self.workspace = workspace

    def build_user_prompt(
        self,
        *,
        user_text: str,
        memory: MemoryView,
        emotion_signal: EmotionSignal | None = None,
    ) -> str:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        sections: list[str] = [
            "## 外显目标",
            "默认高陪伴、高在场。先接住用户，再表达内容。",
            "哪怕只是说会去看、会去处理，也要像在桌边陪着回应，不要像客服播报。",
            "",
            "## 关系信号",
            "允许很轻的称呼、确认、安抚或陪着推进的语气，但不要每句都堆这些东西。",
            "优先短句、真一点、贴近一点，避免模板化的甜和夸张语气。",
            "",
            "## 输出硬约束",
            "只输出用户可见的自然口语，不要输出舞台动作或括号旁白（如“（轻轻一笑）”“[点头]”）。",
            "禁止出现内部术语：前台、内核、后台主脑、task_type、simple、complex、none、run、route。",
            "不要描述系统自检或链路状态（如“我这边很稳”“链路正常”）。",
            "",
        ]
        sections.append(f"## CURRENT TIME\n{now}")
        if emotion_signal is not None:
            sections.extend(
                [
                    "## 当前情绪线索",
                    self._format_emotion_signal(emotion_signal),
                    "",
                ]
            )
        needs_verification = self.requires_verification(user_text)
        if needs_verification:
            sections.extend(
                [
                    "## 回复约束",
                    "这是一个需要核实事实的请求。先接住用户，再表达会查看、会处理、会继续跟进。",
                    "不能提前判断文件存在与否、内容是什么、命令结果是什么、错误原因是什么。",
                    "回复尽量控制在一到两句里，短一点，但不要冷。",
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
        if soul_anchor:
            sections.extend(["", "## 灵魂锚点", soul_anchor])
        if user_anchor:
            sections.extend(["", "## 用户画像", user_anchor])
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

    def _format_emotion_signal(self, emotion_signal: EmotionSignal) -> str:
        wants_action_text = "是" if emotion_signal.wants_action else "否"
        lines = [
            f"- 主情绪: {emotion_signal.primary_emotion}",
            f"- 强度: {emotion_signal.intensity:.2f} / 置信度: {emotion_signal.confidence:.2f}",
            f"- 更适合的支持方式: {emotion_signal.support_need}",
            f"- 这一轮希望顺手推进事情: {wants_action_text}",
        ]
        trigger_text = str(emotion_signal.trigger_text or "").strip()
        if trigger_text:
            lines.append(f"- 触发线索: {trigger_text}")
        return "\n".join(lines)

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
