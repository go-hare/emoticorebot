from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from emoticorebot.left_brain.context import ContextBuilder


def test_brain_decision_system_prompt_uses_slim_soul_and_user_context() -> None:
    with TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir)
        (workspace / "SOUL.md").write_text(
            "\n".join(
                [
                    "# SOUL",
                    "",
                    "## 核心人格",
                    "- 冷静但温柔",
                    "",
                    "## 价值观",
                    "- 真实优先",
                    "",
                    "## 说话风格",
                    "- 自然、简洁、有温度",
                    "",
                    "## 外貌",
                    "- 金色长发",
                    "- JK 风格",
                    "",
                    "## 底线（不可被覆盖）",
                    "- 不虚构事实",
                ]
            ),
            encoding="utf-8",
        )
        (workspace / "USER.md").write_text(
            "\n".join(
                [
                    "# USER",
                    "",
                    "## 基础信息",
                    "- **名字**：（用户告知后更新）",
                    "- **常用语言**：中文",
                    "",
                    "## 偏好与习惯",
                    "### 沟通风格",
                    "- [ ] 随意闲聊",
                    "- [x] 专业简洁",
                    "",
                    "### 回复长度偏好",
                    "- [ ] 简短直接",
                    "- [x] 看情况",
                    "",
                    "## 工作背景",
                    "- **当前项目**：EmotiCore",
                    "- **常用工具**：（对话中积累）",
                    "",
                    "## 特殊说明",
                    "（任何用户主动告知的定制指令）",
                ]
            ),
            encoding="utf-8",
        )
        (workspace / "current_state.md").write_text(
            "\n".join(
                [
                    "# Current State",
                    "| 维度 | 数值 |",
                    "| :--- | :--- |",
                    "| Pleasure | 0.0 |",
                    "[当前情绪: 平静] 状态平稳，正常交流",
                    "[守护进程] 无异常，待机中。",
                ]
            ),
            encoding="utf-8",
        )

        builder = ContextBuilder(workspace)
        captured: dict[str, object] = {}

        def _build_left_brain_context(*, query: str, limit: int) -> str:
            captured["query"] = query
            captured["limit"] = limit
            return "## 长期记忆\n\n- 记忆A"

        builder.memory = SimpleNamespace(build_left_brain_context=_build_left_brain_context)

        prompt = builder.build_left_brain_decision_system_prompt(query="创建 add.py")

        assert "## 核心人格" in prompt
        assert "## 价值观" in prompt
        assert "## 说话风格" in prompt
        assert "## 底线（不可被覆盖）" in prompt
        assert "## 外貌" not in prompt
        assert "JK 风格" not in prompt
        assert "（用户告知后更新）" not in prompt
        assert "- [ ] 随意闲聊" not in prompt
        assert "## 基础信息" in prompt
        assert "常用语言" in prompt
        assert "沟通风格：专业简洁" in prompt
        assert "回复长度偏好：看情况" in prompt
        assert "当前项目" in prompt
        assert "[当前情绪: 平静]" in prompt
        assert "| 维度 | 数值 |" not in prompt
        assert captured == {"query": "创建 add.py", "limit": 4}

