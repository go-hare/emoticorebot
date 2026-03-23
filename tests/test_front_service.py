from __future__ import annotations

from pathlib import Path

from emoticorebot.affect import AffectState, PADVector
from emoticorebot.brain_kernel import MemoryView
from emoticorebot.companion import CompanionIntent, SurfaceExpression
from emoticorebot.front.prompt import FrontPromptBuilder
from emoticorebot.front.service import FrontService


class DummyModel:
    pass


def test_front_service_formats_companion_surface_hints_in_chinese() -> None:
    service = FrontService(workspace=Path("/tmp"), model=DummyModel())

    prompt = service._build_presentation_prompt(
        user_text="帮我看看日志",
        kernel_output="kernel raw for: 帮我看看日志",
        affect_state=AffectState(
            current_pad=PADVector(pleasure=-0.28, arousal=0.32, dominance=-0.12),
            vitality=0.31,
            pressure=0.52,
        ),
        companion_intent=CompanionIntent(
            mode="focused",
            warmth=0.86,
            initiative=0.58,
            intensity=0.46,
        ),
        surface_expression=SurfaceExpression(
            text_style="warm_clear",
            presence="beside",
            expression="attentive_warm",
            motion_hint="small_nod",
            body_state="steady_listening",
            breathing_hint="steady_even",
            linger_hint="remain_available",
            speaking_phase="replying",
            settling_phase="listening",
            idle_phase="idle_ready",
        ),
    )

    assert "这一次默认目标是把陪伴感拉满，但绝不能改动事实。" in prompt
    assert "## 情绪动力学" in prompt
    assert "活力值: 0.31（活力偏低，语气和动作都收一点，贴近但低打扰。）" in prompt
    assert "压力值: 0.52（压力偏高，先接住，再给信息，别太硬。）" in prompt
    assert "外显偏置: 这一轮更像稳稳贴在身边，先安住，再说内容。" in prompt
    assert "当前陪伴模式: focused（哪怕在做技术事，也要稳稳陪着推进，先有人在身边的感觉，再把信息说清楚。）" in prompt
    assert "- 开场建议: 开头先给一个很短的在场句，像在桌边应了一声，然后立刻进入处理内容。" in prompt
    assert "文字风格: warm_clear（信息依然清楚，但整体是暖的，像一边陪着一边把事讲明白。）" in prompt
    assert "存在感: beside（像陪在旁边，安静但一直都在。）" in prompt
    assert "动作感提示: small_nod（像一边听你说一边轻轻点头，安静但很在场。）" in prompt
    assert "桌面体状态: steady_listening（桌面体处在稳定倾听姿态，像认真陪你处理眼前这件事。）" in prompt
    assert "呼吸节奏: steady_even（呼吸稳定均匀，给人可依靠的处理感。）" in prompt
    assert "停留方式: remain_available（说完后保持随时可继续处理的在场感。）" in prompt
    assert "说话阶段: replying（当前在出声回应阶段，桌面体要和文字同步在场。）" in prompt
    assert "收束阶段: listening（说完后保持倾听状态，像还在等你下一句。）" in prompt
    assert "待机阶段: idle_ready（说完后进入轻待命状态，随时可以继续回应。）" in prompt
    assert "- 句子节奏: 句子短、稳、清楚，像一边陪着一边把事讲明白。" in prompt
    assert "- 关系距离: 距离是并肩感，不是命令感，也不是过度哄人的语气。" in prompt
    assert "- 可用信号: 可以有很轻的陪做感，比如“我陪你看”“我们接着来”，但别盖过信息。" in prompt
    assert "- 桌面体余韵: 桌面体处在稳定倾听姿态，像认真陪你处理眼前这件事。 / 呼吸稳定均匀，给人可依靠的处理感。 / 说完后保持随时可继续处理的在场感。" in prompt
    assert "- 生命周期: 说话时是 当前在出声回应阶段，桌面体要和文字同步在场。 / 说完先进入 说完后保持倾听状态，像还在等你下一句。 / 最后回到 说完后进入轻待命状态，随时可以继续回应。" in prompt
    assert "陪伴句可以稍微明显一点，但不要盖过后台原始结果里的有效信息。" in prompt


def test_front_prompt_builder_keeps_high_companion_even_for_verification() -> None:
    builder = FrontPromptBuilder(Path("/tmp"))

    prompt = builder.build_user_prompt(
        user_text="帮我看看日志",
        memory=MemoryView(),
    )

    assert "默认高陪伴、高在场。先接住用户，再表达内容。" in prompt
    assert "允许很轻的称呼、确认、安抚或陪着推进的语气，但不要每句都堆这些东西。" in prompt
    assert "先接住用户，再表达会查看、会处理、会继续跟进。" in prompt
    assert "回复尽量控制在一到两句里，短一点，但不要冷。" in prompt


def test_front_service_keeps_opening_short_when_kernel_output_is_long() -> None:
    service = FrontService(workspace=Path("/tmp"), model=DummyModel())

    prompt = service._build_presentation_prompt(
        user_text="帮我整理一下这段输出",
        kernel_output="\n".join(f"line {index}" for index in range(8)),
        affect_state=None,
        companion_intent=CompanionIntent(
            mode="quiet_company",
            warmth=0.92,
            initiative=0.44,
            intensity=0.22,
        ),
        surface_expression=SurfaceExpression(
            text_style="soft_calm",
            presence="beside",
            expression="soft_smile",
            motion_hint="stay_close",
            body_state="resting_beside",
            breathing_hint="soft_slow",
            linger_hint="quiet_stay",
            speaking_phase="replying",
            settling_phase="settling",
            idle_phase="resting",
        ),
    )

    assert "后台信息较多，接住句只占很短一句，正文仍然以有效信息为主。" in prompt
