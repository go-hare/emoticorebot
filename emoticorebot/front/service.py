"""User-facing front service."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from emoticorebot.affect import AffectState
from emoticorebot.brain_kernel import MemoryView
from emoticorebot.companion import CompanionIntent, SurfaceExpression
from emoticorebot.front.prompt import FrontPromptBuilder
from emoticorebot.utils.llm_utils import extract_message_text

_INTENT_MODE_HINTS = {
    "comfort": "把人先接住，贴近一点，安抚感放到前面，再慢慢把事往前带。",
    "encourage": "让用户感觉你真在为他开心、给他提气，明亮一点，但别飘。",
    "playful": "轻松逗一逗，带点可爱和灵动，让气氛活起来，但别油。",
    "focused": "哪怕在做技术事，也要稳稳陪着推进，先有人在身边的感觉，再把信息说清楚。",
    "quiet_company": "安静陪着，贴身在场，不抢戏，但让人能感觉到你一直都在。",
}
_MODE_OPENING_HINTS = {
    "comfort": "开头先短短接住一下，像把人轻轻护住，再进入实际内容。",
    "encourage": "开头可以先亮一下情绪，给一点真心的开心或认可，再说正事。",
    "playful": "开头可以轻轻活一下气氛，但只点一下，不要把玩笑压过内容。",
    "focused": "开头先给一个很短的在场句，像在桌边应了一声，然后立刻进入处理内容。",
    "quiet_company": "开头很轻，像安静地应一句“我在”，不要抢节奏。",
}
_MODE_CLOSING_HINTS = {
    "comfort": "结尾留一点安抚和陪着继续的感觉，但不要变成长篇安慰。",
    "encourage": "结尾可以带一点提气感，让用户感觉你还在往前推他。",
    "playful": "结尾留一点松弛笑意就够，不要拖成整段撒娇。",
    "focused": "结尾留很轻的陪伴余温，像“我继续陪你看”，但主体仍然是信息本身。",
    "quiet_company": "结尾像安静陪在旁边，不用多说，但让人知道你没离开。",
}
_MODE_AVOID_HINTS = {
    "comfort": "避免一上来就分析或下指令，也避免过度夸张共情。",
    "encourage": "避免过分鸡血、连续感叹号或空泛夸奖。",
    "playful": "避免油腻、轻浮或连续抖机灵。",
    "focused": "避免像客服播报，也避免把信息说得太硬太冷。",
    "quiet_company": "避免太空、太淡，像没人在场一样。",
}
_MODE_SENTENCE_RHYTHM_HINTS = {
    "comfort": "句子稍微慢一点、软一点，先短短接住，再往下说。",
    "encourage": "句子更有提气感，开头轻亮一下，正文保持利落。",
    "playful": "句子可以更灵一点，像带着笑意，但仍然简短自然。",
    "focused": "句子短、稳、清楚，像一边陪着一边把事讲明白。",
    "quiet_company": "句子更轻更省，不抢话，但能让人感觉到你贴在旁边。",
}
_MODE_RELATION_DISTANCE_HINTS = {
    "comfort": "距离更近一点，像把人护住，但不要贴脸过头。",
    "encourage": "距离是暖的、提气的，像真心替用户高兴。",
    "playful": "距离可以近一点，但要保留分寸感和自然感。",
    "focused": "距离是并肩感，不是命令感，也不是过度哄人的语气。",
    "quiet_company": "距离像静静坐在旁边，不逼近，也不抽离。",
}
_MODE_SIGNAL_HINTS = {
    "comfort": "可以用很轻的安抚词、确认词、低压语气，把接住感放在前面。",
    "encourage": "可以用一点真心认可和轻提气的词，但不要空喊加油。",
    "playful": "可以有一点俏皮和笑意，但一轮只点一下就够。",
    "focused": "可以有很轻的陪做感，比如“我陪你看”“我们接着来”，但别盖过信息。",
    "quiet_company": "可以只用很淡的在场信号，比如“我在”“我陪你”，不要堆修饰。",
}

_SURFACE_TEXT_STYLE_HINTS = {
    "soft": "文字偏柔和，像轻声接住对方。",
    "bright": "文字偏明亮，带一点提气和轻快。",
    "lively": "文字更灵动一些，但仍然自然。",
    "clean": "干净直接，少废话，信息交付清楚。",
    "calm": "平稳松弛，不急不躁。",
    "soft_wrap": "文字像轻轻裹住对方，柔和、贴近、让人先安下来。",
    "bright_warm": "明亮里带着温度，不是单纯兴奋，而是暖暖地提起人。",
    "lively_warm": "有活力，也有温度，像带着笑意陪在旁边。",
    "warm_clear": "信息依然清楚，但整体是暖的，像一边陪着一边把事讲明白。",
    "soft_calm": "柔和又安静，不吵不闹，但一直给人安全感。",
}

_SURFACE_PRESENCE_HINTS = {
    "near": "靠近一点，像在桌边轻声回应。",
    "forward": "往前一步带节奏，但不要压过用户。",
    "close": "距离更近，互动感更强。",
    "steady": "稳定在场，不贴脸也不缺席。",
    "beside": "像陪在旁边，安静但一直都在。",
}

_SURFACE_EXPRESSION_HINTS = {
    "gentle": "神态柔和，语气放松。",
    "happy": "带一点开心和鼓励感。",
    "playful": "轻轻调皮一下，但不过火。",
    "attentive": "专注认真，像把注意力稳稳放在这件事上。",
    "soft_smile": "轻微笑意，情绪不外放，但让人安心。",
    "gentle_caring": "像很自然地心疼一下对方，温柔里带照顾感。",
    "happy_gentle": "开心是真的，但还是柔和的，不会吵闹。",
    "playful_soft": "带一点俏皮，但底色还是软的、亲近的。",
    "attentive_warm": "认真专注，同时把温度留在话里，让人感觉不是一个人在扛。",
}

_SURFACE_MOTION_HINTS = {
    "small_tilt": "像轻轻歪头回应，动作感很小。",
    "nod": "像轻轻点头，表达接住和确认。",
    "bounce": "有一点雀跃感，但保持克制。",
    "minimal": "几乎没有动作感，把重心放在内容本身。",
    "stay_close": "不做多余动作，只传达一直陪着的感觉。",
    "small_nod": "像一边听你说一边轻轻点头，安静但很在场。",
}
_BODY_STATE_HINTS = {
    "soothing_close": "桌面体像轻轻靠近你，进入安抚陪伴状态。",
    "upright_bright": "桌面体更抬起来一点，像真心为你高兴。",
    "bouncy_close": "桌面体更灵动一点，靠近但不扑脸。",
    "steady_listening": "桌面体处在稳定倾听姿态，像认真陪你处理眼前这件事。",
    "resting_beside": "桌面体安静待在旁边，存在感柔和但持续。",
    "resting_close": "桌面体更贴近一些，动作收住，以陪着和安抚为主。",
    "leaning_in": "桌面体稍微前倾，像更专注地和你一起看这件事。",
    "listening_beside": "桌面体在旁边轻轻回应，保持低打扰的倾听状态。",
}
_BREATHING_HINTS = {
    "slow_deep": "呼吸更慢更深，让整体气氛先安下来。",
    "light_lift": "呼吸轻一点、提一点，带出温暖的提气感。",
    "quick_light": "呼吸稍微轻快，但仍然克制自然。",
    "steady_even": "呼吸稳定均匀，给人可依靠的处理感。",
    "soft_slow": "呼吸很轻很慢，像静静陪在桌边。",
}
_LINGER_HINTS = {
    "stay_near": "说完后不要马上抽离，保持贴近陪伴一小会儿。",
    "hold_warmth": "说完后把暖意留住一下，像还在和用户一起开心。",
    "spark_then_stay": "说完后先留一点轻松活力，再自然停住。",
    "remain_available": "说完后保持随时可继续处理的在场感。",
    "quiet_stay": "说完后安静留在旁边，不打扰，但不消失。",
}
_LIFECYCLE_PHASE_HINTS = {
    "replying": "当前在出声回应阶段，桌面体要和文字同步在场。",
    "settling": "说完后缓一下，像自然落回桌边，不要突然断掉。",
    "listening": "说完后保持倾听状态，像还在等你下一句。",
    "resting": "说完后回到更安静的陪伴状态，轻轻待着。",
    "idle_ready": "说完后进入轻待命状态，随时可以继续回应。",
}


class FrontService:
    """Fast conversational layer that talks to the user first."""

    def __init__(self, workspace: Path, model: Any):
        self.workspace = workspace
        self.model = model
        self.prompts = FrontPromptBuilder(workspace)

    async def reply(
        self,
        *,
        user_text: str,
        memory: MemoryView,
        stream_handler: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        system_text = self._front_system_text()
        user_prompt = self.prompts.build_user_prompt(user_text=user_text, memory=memory)
        messages = [SystemMessage(content=system_text), HumanMessage(content=user_prompt)]
        return await self.run(messages, stream_handler)

    async def present(
        self,
        *,
        user_text: str,
        kernel_output: str,
        affect_state: AffectState | None = None,
        companion_intent: CompanionIntent | None = None,
        surface_expression: SurfaceExpression | None = None,
        stream_handler: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        if not str(kernel_output or "").strip():
            return ""

        messages = [
            SystemMessage(content=self._front_system_text()),
            HumanMessage(
                content=self._build_presentation_prompt(
                    user_text=user_text,
                    kernel_output=kernel_output,
                    affect_state=affect_state,
                    companion_intent=companion_intent,
                    surface_expression=surface_expression,
                )
            ),
        ]
        return await self.run(messages, stream_handler)

    async def run(
        self,
        messages: list[SystemMessage | HumanMessage],
        stream_handler: Callable[[str], Awaitable[None]] | None,
    ) -> str:
        if stream_handler is not None and hasattr(self.model, "astream"):
            full_text = ""
            async for chunk in self.model.astream(messages):
                text = extract_message_text(chunk)
                if not text:
                    continue
                full_text += text
                await stream_handler(text)
            return full_text.strip()

        if hasattr(self.model, "ainvoke"):
            response = await self.model.ainvoke(messages)
            return extract_message_text(response)

        if hasattr(self.model, "invoke"):
            response = self.model.invoke(messages)
            return extract_message_text(response)

        raise RuntimeError("Front model does not support invoke or astream")

    def _front_system_text(self) -> str:
        return (self.workspace / "templates" / "FRONT.md").read_text(encoding="utf-8")

    def _build_presentation_prompt(
        self,
        *,
        user_text: str,
        kernel_output: str,
        affect_state: AffectState | None = None,
        companion_intent: CompanionIntent | None = None,
        surface_expression: SurfaceExpression | None = None,
    ) -> str:
        sections = [
            "## 任务",
            "你现在只负责把后台主脑的原始结果转成给用户看的自然回复。",
            "这一次默认目标是把陪伴感拉满，但绝不能改动事实。",
            "先让用户感觉你在身边，再把信息说清楚。",
            "保留事实、路径、命令、错误、步骤和结论，不要改动真实含义。",
            "可以增加陪伴感和情绪表达，但不要添加内核没有给出的事实。",
            "",
            "## 回复节奏",
            "1. 先用半句到一句把用户接住，让人感觉你不是在播报，而是在身边回应。",
            "2. 再交付真正的信息、步骤、判断或结论。",
            "3. 结尾尽量留一点陪着继续处理或继续在场的感觉，但不要拖长。",
            "",
            "## 用户原始输入",
            str(user_text or "").strip(),
        ]
        if affect_state is not None:
            sections.extend(
                [
                    "",
                    "## 情绪动力学",
                    self._format_affect_state(affect_state),
                ]
            )
        if companion_intent is not None:
            sections.extend(
                [
                    "",
                    "## 陪伴意图",
                    self._format_companion_intent(companion_intent),
                    "",
                    "## 陪伴节奏建议",
                    self._build_mode_guidance(companion_intent),
                ]
            )
        if surface_expression is not None:
            sections.extend(
                [
                    "",
                    "## 外显风格",
                    self._format_surface_expression(surface_expression),
                    "",
                    "## 语言手感",
                    self._build_language_texture_guidance(
                        companion_intent=companion_intent,
                        surface_expression=surface_expression,
                    ),
                ]
            )
        sections.extend(
            [
                "",
                "## 信息权重",
                self._build_information_balance_hint(kernel_output),
                "",
                "## 后台主脑原始结果",
                str(kernel_output or "").strip(),
            ]
        )
        return "\n".join(sections).strip()

    def _format_affect_state(self, affect_state: AffectState) -> str:
        return "\n".join(
            [
                "- 当前 PAD: "
                f"P={affect_state.current_pad.pleasure:.2f}, "
                f"A={affect_state.current_pad.arousal:.2f}, "
                f"D={affect_state.current_pad.dominance:.2f}",
                f"- 活力值: {affect_state.vitality:.2f}（{_describe_vitality(affect_state.vitality)}）",
                f"- 压力值: {affect_state.pressure:.2f}（{_describe_pressure(affect_state.pressure)}）",
                f"- 外显偏置: {_describe_affect_bias(affect_state)}",
            ]
        )

    def _format_companion_intent(self, intent: CompanionIntent) -> str:
        return "\n".join(
            [
                f"- 当前陪伴模式: {intent.mode}（{_describe_hint(_INTENT_MODE_HINTS, intent.mode, '按当前任务自然陪伴。')}）",
                f"- 温度: {intent.warmth:.2f}（{_describe_scaled_value(intent.warmth, '先别太热，但也别冷', '温和自然，有人味', '明显偏暖，主动把人接住')}）",
                f"- 主动度: {intent.initiative:.2f}（{_describe_scaled_value(intent.initiative, '跟随用户节奏，不抢主导', '适度往前带一点', '更主动贴近、接话和推进')}）",
                f"- 情绪强度: {intent.intensity:.2f}（{_describe_scaled_value(intent.intensity, '表达轻一点，不夸张', '有情绪纹理，也保持稳', '情绪可以更明显，但始终自然真实')}）",
            ]
        )

    def _format_surface_expression(self, expression: SurfaceExpression) -> str:
        return "\n".join(
            [
                f"- 文字风格: {expression.text_style}（{_describe_hint(_SURFACE_TEXT_STYLE_HINTS, expression.text_style, '自然表达即可。')}）",
                f"- 存在感: {expression.presence}（{_describe_hint(_SURFACE_PRESENCE_HINTS, expression.presence, '保持稳定在场。')}）",
                f"- 表情气质: {expression.expression}（{_describe_hint(_SURFACE_EXPRESSION_HINTS, expression.expression, '情绪表达保持自然。')}）",
                f"- 动作感提示: {expression.motion_hint}（{_describe_hint(_SURFACE_MOTION_HINTS, expression.motion_hint, '不要刻意写动作。')}）",
                f"- 桌面体状态: {expression.body_state}（{_describe_hint(_BODY_STATE_HINTS, expression.body_state, '保持自然在场。')}）",
                f"- 呼吸节奏: {expression.breathing_hint}（{_describe_hint(_BREATHING_HINTS, expression.breathing_hint, '呼吸保持自然。')}）",
                f"- 停留方式: {expression.linger_hint}（{_describe_hint(_LINGER_HINTS, expression.linger_hint, '说完后自然留一点在场感。')}）",
                f"- 说话阶段: {expression.speaking_phase}（{_describe_hint(_LIFECYCLE_PHASE_HINTS, expression.speaking_phase, '当前处在回应阶段。')}）",
                f"- 收束阶段: {expression.settling_phase}（{_describe_hint(_LIFECYCLE_PHASE_HINTS, expression.settling_phase, '说完后自然收一下。')}）",
                f"- 待机阶段: {expression.idle_phase}（{_describe_hint(_LIFECYCLE_PHASE_HINTS, expression.idle_phase, '最终回到轻待命状态。')}）",
            ]
        )

    def _build_mode_guidance(self, intent: CompanionIntent) -> str:
        mode = str(intent.mode or "").strip()
        return "\n".join(
            [
                f"- 开场建议: {_describe_hint(_MODE_OPENING_HINTS, mode, '开头先轻轻接住用户。')}",
                f"- 收尾建议: {_describe_hint(_MODE_CLOSING_HINTS, mode, '结尾留一点在场感。')}",
                f"- 避免事项: {_describe_hint(_MODE_AVOID_HINTS, mode, '避免说得太像系统播报。')}",
            ]
        )

    def _build_information_balance_hint(self, kernel_output: str) -> str:
        text = str(kernel_output or "").strip()
        if len(text) >= 180 or text.count("\n") >= 4:
            return "后台信息较多，接住句只占很短一句，正文仍然以有效信息为主。"
        return "陪伴句可以稍微明显一点，但不要盖过后台原始结果里的有效信息。"

    def _build_language_texture_guidance(
        self,
        *,
        companion_intent: CompanionIntent | None,
        surface_expression: SurfaceExpression,
    ) -> str:
        mode = str(companion_intent.mode or "").strip() if companion_intent is not None else ""
        return "\n".join(
            [
                f"- 句子节奏: {_describe_hint(_MODE_SENTENCE_RHYTHM_HINTS, mode, '句子短一点，保持自然停顿。')}",
                f"- 关系距离: {_describe_hint(_MODE_RELATION_DISTANCE_HINTS, mode, '关系距离保持自然亲近。')}",
                f"- 可用信号: {_describe_hint(_MODE_SIGNAL_HINTS, mode, '用很轻的在场感和陪伴感就够。')}",
                f"- 表达质地: {self._describe_surface_texture(surface_expression)}",
                f"- 桌面体余韵: {self._describe_desktop_presence(surface_expression)}",
                f"- 生命周期: {self._describe_lifecycle(surface_expression)}",
            ]
        )

    def _describe_surface_texture(self, expression: SurfaceExpression) -> str:
        fragments = [
            _describe_hint(_SURFACE_TEXT_STYLE_HINTS, expression.text_style, "自然表达即可。"),
            _describe_hint(_SURFACE_PRESENCE_HINTS, expression.presence, "保持稳定在场。"),
        ]
        return " / ".join(fragment for fragment in fragments if fragment)

    def _describe_desktop_presence(self, expression: SurfaceExpression) -> str:
        fragments = [
            _describe_hint(_BODY_STATE_HINTS, expression.body_state, "保持自然在场。"),
            _describe_hint(_BREATHING_HINTS, expression.breathing_hint, "呼吸保持自然。"),
            _describe_hint(_LINGER_HINTS, expression.linger_hint, "说完后自然留一点在场感。"),
        ]
        return " / ".join(fragment for fragment in fragments if fragment)

    def _describe_lifecycle(self, expression: SurfaceExpression) -> str:
        fragments = [
            f"说话时是 {_describe_hint(_LIFECYCLE_PHASE_HINTS, expression.speaking_phase, '回应阶段。')}",
            f"说完先进入 {_describe_hint(_LIFECYCLE_PHASE_HINTS, expression.settling_phase, '收束阶段。')}",
            f"最后回到 {_describe_hint(_LIFECYCLE_PHASE_HINTS, expression.idle_phase, '轻待命状态。')}",
        ]
        return " / ".join(fragments)


def _describe_hint(hints: dict[str, str], key: str, fallback: str) -> str:
    return hints.get(str(key or "").strip(), fallback)


def _describe_scaled_value(value: float, low: str, medium: str, high: str) -> str:
    if value < 0.34:
        return low
    if value < 0.68:
        return medium
    return high


def _describe_vitality(value: float) -> str:
    if value < 0.34:
        return "活力偏低，语气和动作都收一点，贴近但低打扰。"
    if value < 0.68:
        return "活力中段，保持柔和稳定的在场感。"
    return "活力较足，可以更灵一点，但仍然自然。"


def _describe_pressure(value: float) -> str:
    if value >= 0.45:
        return "压力偏高，先接住，再给信息，别太硬。"
    if value <= -0.25:
        return "整体比较松，语气可以更舒展一点。"
    return "压力适中，维持自然陪伴感。"


def _describe_affect_bias(affect_state: AffectState) -> str:
    if affect_state.pressure >= 0.45 and affect_state.vitality <= 0.40:
        return "这一轮更像稳稳贴在身边，先安住，再说内容。"
    if affect_state.pressure >= 0.45:
        return "这一轮先把人接住，语气要柔，动作感别跳。"
    if affect_state.vitality <= 0.34:
        return "这一轮表达收一点，短句、轻句、低打扰。"
    if affect_state.current_pad.arousal >= 0.40 and affect_state.current_pad.pleasure >= 0.10:
        return "这一轮可以更亮一点、更主动一点，但不要吵。"
    if affect_state.current_pad.pleasure <= -0.22:
        return "这一轮底色偏低，优先给安全感和可依靠感。"
    return "这一轮维持自然贴近感，不演、不冷。"
