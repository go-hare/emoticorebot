"""
PAD 情感状态机 + 驱动欲望指数。

设计依据：ai.md §1.1 / §1.2
- PAD 三维情感模型 (Pleasure-Arousal-Dominance)
- 驱动欲望 (social / energy)
- EmotionStateManager：统一管理，线程安全，与 current_state.md 文件同步
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from loguru import logger


_DEFAULT_DRIVE_CONFIG = {
    "drives": {
        "social": {
            "initial": 50,
            "decay_rate": 0.5,
            "threshold_low": 20,
            "threshold_high": 80,
            "recover_per_chat": 20,
        },
        "energy": {
            "initial": 100,
            "decay_rate": 1.0,
            "threshold_low": 10,
            "threshold_zero": 0,
            "recover_per_sleep": 80,
        },
    },
    "triggers": {
        "proactive_chat": {
            "condition": "social < 20",
            "probability": 0.3,
        },
    },
    "schedules": {
        "decay_minutes": 30,
        "reflect_hours": 1,
        "proactive_check_minutes": 10,
    },
}


def _deep_update(base: dict, override: dict) -> dict:
    """递归合并配置，override 优先。"""
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
    return base


def load_drive_config(workspace: Path) -> dict:
    """
    从 workspace/config/drive_config.yaml 加载驱动配置。
    优先按 YAML 解析，失败时回退到 JSON 解析；都失败则使用默认值。
    """
    cfg = deepcopy(_DEFAULT_DRIVE_CONFIG)
    cfg_file = workspace / "config" / "drive_config.yaml"
    if not cfg_file.exists():
        return cfg
    raw = cfg_file.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        user_cfg = yaml.safe_load(raw)
        if isinstance(user_cfg, dict):
            _deep_update(cfg, user_cfg)
            return cfg
    except Exception:
        pass

    try:
        import json
        user_cfg = json.loads(raw)
        if isinstance(user_cfg, dict):
            _deep_update(cfg, user_cfg)
            return cfg
    except Exception as e:
        logger.warning("load_drive_config failed ({}), using defaults", e)
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# PAD 情感状态
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PADState:
    """三维情感状态 (Pleasure-Arousal-Dominance)，取值范围均为 [-1.0, 1.0]。"""

    pleasure:  float = 0.0   # 愉悦度：负→悲伤/愤怒，正→开心/兴奋
    arousal:   float = 0.5   # 激活度：低→困倦，高→激动话多
    dominance: float = 0.5   # 支配度：低→撒娇顺从，高→傲娇自信

    def clamp(self) -> "PADState":
        """防止数值溢出，写入后必须调用。"""
        self.pleasure  = max(-1.0, min(1.0, self.pleasure))
        self.arousal   = max(-1.0, min(1.0, self.arousal))
        self.dominance = max(-1.0, min(1.0, self.dominance))
        return self

    # ── 事件驱动更新（ai.md §1.1 更新因子）─────────────────────────────────

    def on_praise(self) -> None:
        """用户夸奖：pleasure +0.3, arousal +0.2"""
        self.pleasure += 0.3
        self.arousal  += 0.2
        self.clamp()

    def on_insult(self) -> None:
        """用户谩骂：pleasure -0.5, arousal +0.4 (愤怒), dominance -0.2"""
        self.pleasure  -= 0.5
        self.arousal   += 0.4
        self.dominance -= 0.2
        self.clamp()

    def on_idle(self) -> None:
        """长时间无互动：arousal -0.1/hour（由守护进程调用）"""
        self.arousal -= 0.1
        self.clamp()

    # ── 状态推导 ─────────────────────────────────────────────────────────────

    def get_emotion_label(self) -> str:
        """PAD 数值 → 中文情绪标签（用于关系记忆元数据和检索）。"""
        if self.pleasure < -0.5:
            return "悲伤" if self.arousal < 0.3 else "愤怒"
        if self.pleasure > 0.5:
            return "兴奋" if self.arousal > 0.7 else "开心"
        if self.arousal < 0.2:
            return "困倦"
        return "平静"

    def get_emotion_prompt(self) -> str:
        """自然语言描述，注入 system prompt（主脑路径使用）。"""
        label = self.get_emotion_label()
        desc = {
            "悲伤": "有些低落，回复简短，惜字如金",
            "愤怒": "有点生气，说话简短有力",
            "开心": "心情不错，愿意多聊",
            "兴奋": "非常兴奋，话比较多，喜欢感叹号",
            "困倦": "感觉困倦，反应有点慢，回复简短",
            "平静": "状态平稳，正常交流",
        }
        return f"[当前情绪: {label}] {desc.get(label, '状态平稳')}"


# ─────────────────────────────────────────────────────────────────────────────
# 驱动欲望指数
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DriveState:
    """驱动欲望指数（ai.md §1.2），取值范围 [0, 100]。"""

    social: float = 50.0   # 社交渴望：低→主动找人聊，高→话痨模式
    energy: float = 100.0  # 精力值：仅影响表达节奏与主动性，不影响实际执行
    social_decay_per_hour: float = 0.5
    energy_decay_per_hour: float = 1.0
    threshold_social_low: float = 20.0
    threshold_energy_low: float = 10.0
    threshold_energy_zero: float = 0.0
    recover_per_chat: float = 20.0
    recover_per_sleep: float = 80.0

    def clamp(self) -> "DriveState":
        self.social = max(0.0, min(100.0, self.social))
        self.energy = max(0.0, min(100.0, self.energy))
        return self

    def decay(self, hours: float = 0.5) -> None:
        """自然衰减（ai.md §1.2：social 0.5/h，energy 1.0/h）。"""
        self.social -= self.social_decay_per_hour * hours
        self.energy -= self.energy_decay_per_hour * hours
        self.clamp()

    def on_praise(self) -> None:
        """用户夸奖：social +5.00, energy +20.00"""
        self.social += 5.00
        self.energy += 20.00
        self.clamp()

    def on_insult(self) -> None:
        """用户谩骂：social -5.00, energy +20.00"""
        self.social -= 5.00
        self.energy += 20.00
        self.clamp()

    def on_chat(self) -> None:
        """每次对话后社交渴望回升 20，精力消耗 2。"""
        self.social += self.recover_per_chat
        self.energy -= 2.0
        self.clamp()

    def needs_proactive_chat(self) -> bool:
        """social < 20：应触发主动对话（ai.md §1.2 阈值触发行为）。"""
        return self.social < self.threshold_social_low

# ─────────────────────────────────────────────────────────────────────────────
# 情绪事件（情绪记忆流的基本单元）
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EmotionEvent:
    """
    单次情绪变化事件，对应 emoticorebot 设计的情绪记忆流格式：
      时间戳 | 触发词 | 情绪变化量 | 后续行为
    """
    timestamp:     str              # 2026-03-03 15:31
    trigger:       str              # 触发内容片段，如 "喜欢你"
    delta_pleasure: float = 0.0     # 愉悦度变化量
    delta_arousal:  float = 0.0     # 激活度变化量
    delta_dominance: float = 0.0    # 支配度变化量
    delta_social: float = 0.0       # 社交驱动变化量
    delta_energy: float = 0.0       # 精力驱动变化量
    behavior:      str = ""         # 后续行为描述，如 "傲娇防御，开心"

    def to_md_row(self) -> str:
        """渲染为 Markdown 表格行。"""
        parts = []
        if self.delta_pleasure != 0:
            parts.append(f"愉悦{self.delta_pleasure:+.2f}")
        if self.delta_arousal != 0:
            parts.append(f"激活{self.delta_arousal:+.2f}")
        if self.delta_dominance != 0:
            parts.append(f"支配{self.delta_dominance:+.2f}")
        if self.delta_social != 0:
            parts.append(f"社交{self.delta_social:+.2f}")
        if self.delta_energy != 0:
            parts.append(f"精力{self.delta_energy:+.2f}")
        delta_str = " ".join(parts) if parts else "无变化"
        return f"| {self.timestamp} | {self.trigger} | {delta_str} | {self.behavior} |"


# ─────────────────────────────────────────────────────────────────────────────
# 统一管理器：读写 current_state.md
# ─────────────────────────────────────────────────────────────────────────────

# 夸奖/谩骂关键词（用于从对话内容自动检测情绪事件）
_PRAISE_WORDS  = ["谢谢", "棒", "厉害", "好聪明", "喜欢你", "爱你", "你真好", "太棒了", "完美", "太厉害了"]
_INSULT_WORDS  = ["笨", "蠢", "傻", "烦死了", "讨厌你", "废物", "垃圾", "滚", "闭嘴"]


class EmotionStateManager:
    """
    统一管理 PADState + DriveState，与 workspace/current_state.md 文件双向同步。

    - 启动时从文件读取上次状态（持久化）
    - 每次状态变更后立即写回文件（供 ContextBuilder 注入 System Prompt）
    - 线程安全（守护进程和主循环并发写入）
    - 情绪事件对象：由调用方接管并写入结构化记忆层
    """

    def __init__(self, workspace: Path):
        self.state_file  = workspace / "current_state.md"
        self._lock       = threading.Lock()
        self.pad         = PADState()
        self.drive_config = load_drive_config(workspace)
        social_cfg = self.drive_config.get("drives", {}).get("social", {})
        energy_cfg = self.drive_config.get("drives", {}).get("energy", {})
        self.drive = DriveState(
            social=float(social_cfg.get("initial", 50)),
            energy=float(energy_cfg.get("initial", 100)),
            social_decay_per_hour=float(social_cfg.get("decay_rate", 0.5)),
            energy_decay_per_hour=float(energy_cfg.get("decay_rate", 1.0)),
            threshold_social_low=float(social_cfg.get("threshold_low", 20)),
            threshold_energy_low=float(energy_cfg.get("threshold_low", 10)),
            threshold_energy_zero=float(energy_cfg.get("threshold_zero", 0)),
            recover_per_chat=float(social_cfg.get("recover_per_chat", 20)),
            recover_per_sleep=float(energy_cfg.get("recover_per_sleep", 80)),
        )
        self._load()

    # ── 文件读写 ──────────────────────────────────────────────────────────────

    def _load(self) -> None:
        """从 current_state.md 解析 PAD + Drive 数值。"""
        if not self.state_file.exists():
            self._save()   # 首次运行时生成初始文件
            return
        try:
            text = self.state_file.read_text(encoding="utf-8")
            # 解析 PAD 数值（匹配表格行：| Pleasure | 0.20 | ... |）
            for attr in ("pleasure", "arousal", "dominance"):
                m = re.search(
                    rf'{attr}[^|]*\|\s*([-\d.]+)',
                    text, re.IGNORECASE
                )
                if m:
                    setattr(self.pad, attr, float(m.group(1)))
            # 解析 Drive 数值（匹配：| Social | 45/100 | ... |）
            m = re.search(r'Social[^|]*\|\s*([\d.]+)/100', text, re.IGNORECASE)
            if m:
                self.drive.social = float(m.group(1))
            m = re.search(r'Energy[^|]*\|\s*([\d.]+)/100', text, re.IGNORECASE)
            if m:
                self.drive.energy = float(m.group(1))
            logger.debug("EmotionState loaded: PAD=({:.2f},{:.2f},{:.2f}) Drive=({:.0f},{:.0f})",
                         self.pad.pleasure, self.pad.arousal, self.pad.dominance,
                         self.drive.social, self.drive.energy)
        except Exception as e:
            logger.warning("EmotionState parse failed ({}), using defaults", e)

    def _save(self) -> None:
        """将当前状态写入 current_state.md（持久化）。"""
        try:
            self.state_file.write_text(self._render_md(), encoding="utf-8")
        except Exception as e:
            logger.warning("EmotionState save failed: {}", e)

    def _render_md(self) -> str:
        """生成 current_state.md 的 Markdown 内容。"""
        label      = self.pad.get_emotion_label()
        intention  = (
            "[守护进程] 社交渴望值低，准备发起主动对话。"
            if self.drive.needs_proactive_chat()
            else "[守护进程] 无异常，待机中。"
        )
        energy_desc = (
            "精力充沛" if self.drive.energy > 60
            else ("状态一般" if self.drive.energy > 20 else "状态偏低（将简洁表达）")
        )
        return f"""# Current State（实时快照）
> 最后更新：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 1. 情感状态（PAD 模型）
| 维度 | 数值 | 范围 |
| :--- | :--- | :--- |
| Pleasure（愉悦） | {self.pad.pleasure:.2f} | [-1.0, 1.0] |
| Arousal（激活）  | {self.pad.arousal:.2f}  | [-1.0, 1.0] |
| Dominance（支配）| {self.pad.dominance:.2f}| [-1.0, 1.0] |

## 2. 驱动欲望
| 维度 | 数值 | 状态描述 |
| :--- | :--- | :--- |
| Social（社交渴望） | {self.drive.social:.0f}/100 | {'需要主动找人聊聊' if self.drive.needs_proactive_chat() else '正常'} |
| Energy（精力值）   | {self.drive.energy:.0f}/100 | {energy_desc} |

## 3. 当前主导情绪
[当前情绪: {label}] {self.pad.get_emotion_prompt()}

## 4. 当前意图
{intention}
"""

    # ── 对外接口 ──────────────────────────────────────────────────────────────

    def get_emotion_prompt(self) -> str:
        """供融合策略与上下文构建读取当前情绪描述。"""
        return self.pad.get_emotion_prompt()

    def get_emotion_label(self) -> str:
        """供关系记忆写入时获取情绪标签。"""
        return self.pad.get_emotion_label()

    def read_md(self) -> str:
        """供 ContextBuilder 直接读取 MD 文本注入 System Prompt。"""
        if self.state_file.exists():
            return self.state_file.read_text(encoding="utf-8")
        return self._render_md()

    def snapshot(self) -> dict[str, Any]:
        """Return a structured snapshot of the current live state."""
        with self._lock:
            return self._snapshot_unlocked()

    def apply_reflection_state_update(
        self,
        *,
        pad_delta: dict[str, float] | None = None,
        drive_delta: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Apply reflection-selected absolute state values through the state manager."""
        with self._lock:
            for attr in ("pleasure", "arousal", "dominance"):
                if attr not in (pad_delta or {}):
                    continue
                try:
                    value = float((pad_delta or {}).get(attr, getattr(self.pad, attr)) or 0.0)
                except Exception:
                    continue
                setattr(self.pad, attr, value)

            for attr in ("social", "energy"):
                if attr not in (drive_delta or {}):
                    continue
                try:
                    value = float((drive_delta or {}).get(attr, getattr(self.drive, attr)) or 0.0)
                except Exception:
                    continue
                setattr(self.drive, attr, value)

            self.pad.clamp()
            self.drive.clamp()
            self._save()
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> dict[str, Any]:
        return {
            "pad": {
                "pleasure": round(float(self.pad.pleasure), 3),
                "arousal": round(float(self.pad.arousal), 3),
                "dominance": round(float(self.pad.dominance), 3),
            },
            "drives": {
                "social": round(float(self.drive.social), 2),
                "energy": round(float(self.drive.energy), 2),
            },
            "emotion_label": self.pad.get_emotion_label(),
            "emotion_prompt": self.pad.get_emotion_prompt(),
        }

    def update_from_conversation(self, user_msg: str, ai_msg: str) -> "EmotionEvent | None":
        """
        根据对话内容自动检测情绪事件并更新 PAD 状态。
        每次对话结束后调用（ai.md §1.1 更新因子）。
        情绪变化事件对象由上层写入结构化记忆层。
        """
        with self._lock:
            lower = user_msg.lower()

            # 记录变化前的值（用于计算 delta）
            p_before = self.pad.pleasure
            a_before = self.pad.arousal
            d_before = self.pad.dominance
            social_before = self.drive.social
            energy_before = self.drive.energy

            event_trigger  = ""
            event_behavior = ""

            if any(w in lower for w in _PRAISE_WORDS):
                # 找到具体触发词
                event_trigger  = next((w for w in _PRAISE_WORDS if w in lower), user_msg[:20])
                self.pad.on_praise()
                self.drive.on_praise()
                event_behavior = "开心，略感傲娇，倾向多聊"
                logger.debug("PAD: praise detected → pleasure↑ arousal↑")

            elif any(w in lower for w in _INSULT_WORDS):
                event_trigger  = next((w for w in _INSULT_WORDS if w in lower), user_msg[:20])
                self.pad.on_insult()
                self.drive.on_insult()
                event_behavior = "生气，回复简短有力"
                logger.debug("PAD: insult detected → pleasure↓ arousal↑")

            else:
                # 普通对话：无明显情绪触发，记录轻微社交回升
                event_trigger  = user_msg[:30].strip()
                event_behavior = "正常对话，状态平稳"

            self.drive.on_chat()
            self._save()

            # ── 写入情绪事件记忆流 ──────────────────────────────────────────
            delta_p = round(self.pad.pleasure  - p_before, 2)
            delta_a = round(self.pad.arousal   - a_before, 2)
            delta_d = round(self.pad.dominance - d_before, 2)
            delta_social = round(self.drive.social - social_before, 2)
            delta_energy = round(self.drive.energy - energy_before, 2)

            event = EmotionEvent(
                timestamp       = datetime.now().strftime("%Y-%m-%d %H:%M"),
                trigger         = f'"{event_trigger}"',
                delta_pleasure  = delta_p,
                delta_arousal   = delta_a,
                delta_dominance = delta_d,
                delta_social    = delta_social,
                delta_energy    = delta_energy,
                behavior        = event_behavior,
            )
            return event  # 返回给调用方，供写入对话事件流

    def decay(self, hours: float = 0.5) -> None:
        """
        守护进程每 30 分钟调用一次，衰减 PAD + Drive。
        对应 ai.md §1.2 自然衰减率。
        """
        with self._lock:
            self.pad.on_idle()
            self.drive.decay(hours)
            self._save()
            logger.debug("EmotionState decayed: social={:.0f} energy={:.0f}",
                         self.drive.social, self.drive.energy)
