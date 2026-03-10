from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from math import fabs
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4


@dataclass
class CognitiveEvent:
    id: str
    version: str
    timestamp: str
    session_id: str
    turn_id: str
    actor: str
    event_type: str
    content: str
    state: dict[str, Any] = field(default_factory=dict)
    execution: dict[str, Any] = field(default_factory=dict)
    light_insight: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def storage_path(cls, workspace: Path) -> Path:
        primary_path = workspace / "memory" / "cognitive_events.jsonl"
        primary_path.parent.mkdir(parents=True, exist_ok=True)
        return primary_path

    @classmethod
    def append(cls, workspace: Path, event: "CognitiveEvent") -> None:
        path = cls.storage_path(workspace)
        with path.open("a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    @classmethod
    def read_all(cls, workspace: Path) -> list[dict[str, Any]]:
        path = cls.storage_path(workspace)
        if not path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as file_obj:
            for line in file_obj:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    continue
                if isinstance(payload, dict):
                    entries.append(payload)
        return entries

    @classmethod
    def retrieve(
        cls,
        workspace: Path,
        query: str = "",
        *,
        actors: list[str] | None = None,
        k: int = 8,
    ) -> list[dict[str, Any]]:
        entries = cls.read_all(workspace)
        if actors:
            allowed = set(actors)
            entries = [entry for entry in entries if str(entry.get("actor", "")) in allowed]
        return cls._rank_entries(entries, query=query, limit=k)

    @classmethod
    def get_cognitive_context(cls, workspace: Path, query: str = "", *, k: int = 6) -> str:
        rows = cls.retrieve(workspace, query=query, actors=["user", "assistant"], k=max(k * 2, 8))
        if not rows:
            return ""

        lines: list[str] = []
        for row in rows:
            actor = str(row.get("actor", "event") or "event")
            summary = cls._normalize_text(str(row.get("content", "")), limit=120)
            if not summary:
                continue

            state = row.get("state") or {}
            self_state = state.get("self_state") if isinstance(state, dict) else {}
            relation_state = state.get("relation_state") if isinstance(state, dict) else {}
            mood = str((self_state or {}).get("mood", "") or "stable")
            relation = cls._extract_relation_label(relation_state)
            importance = float((row.get("meta") or {}).get("importance", 0.5) or 0.5)
            pad_text = cls._format_pad((self_state or {}).get("pad"))
            if pad_text:
                lines.append(f"- [{actor}|{mood}|{relation}|{importance:.2f}] {summary} {pad_text}")
            else:
                lines.append(f"- [{actor}|{mood}|{relation}|{importance:.2f}] {summary}")

            if len(lines) >= k:
                break

        if not lines:
            return ""
        return "## Cognitive Events\n" + "\n".join(lines)

    @classmethod
    def build_cognitive_sections(
        cls,
        workspace: Path,
        *,
        query: str = "",
        current_emotion: str = "stable",
        pad_state: tuple[float, float, float] | None = None,
    ) -> list[str]:
        del current_emotion, pad_state
        cognitive_flow = cls.get_cognitive_context(workspace, query=query, k=4)
        if not cognitive_flow:
            return []
        return [cls._compact_section(cognitive_flow, 480)]

    @classmethod
    def build_turn_events(
        cls,
        *,
        state: dict[str, Any],
        emotion_label: str,
        emotion_event: Any,
        pad: dict[str, float],
        drives: dict[str, float],
        importance: float,
        light_insight: dict[str, Any] | None = None,
    ) -> list["CognitiveEvent"]:
        timestamp = datetime.now().astimezone().isoformat()
        session_id = str(state.get("session_id", "") or "")
        turn_id = cls._extract_turn_id(state)
        user_input = str(state.get("user_input", "") or "").strip()
        output = str(state.get("output", "") or "").strip()
        executor = state.get("executor")
        execution = cls._build_execution(state=state, executor=executor)
        insight_payload = cls._normalize_light_insight(light_insight)

        relation_signal = cls._infer_relation(user_input=user_input, emotion_event=emotion_event)
        relation_state = cls._build_relation_state(relation_signal)
        context_state = cls._build_context_state(
            user_input=user_input,
            output=output,
            light_insight=insight_payload,
            execution=execution,
        )
        growth_state = cls._build_growth_state(light_insight=insight_payload)
        channel = str(state.get("channel", "") or "")

        events: list[CognitiveEvent] = []
        if user_input:
            self_state = cls._build_self_state(
                pad=pad,
                drives=drives,
                mood=emotion_label,
                tone="attentive",
                companionship_tension=cls._infer_companionship_tension(relation_signal),
            )
            events.append(
                cls(
                    id=f"evt_{uuid4().hex}",
                    version="2",
                    timestamp=timestamp,
                    session_id=session_id,
                    turn_id=turn_id,
                    actor="user",
                    event_type="user_input",
                    content=user_input,
                    state={
                        "self_state": self_state,
                        "relation_state": relation_state,
                        "context_state": context_state,
                        "growth_state": growth_state,
                    },
                    execution=cls._empty_execution(),
                    light_insight=insight_payload,
                    meta={
                        "importance": round(float(importance), 2),
                        "confidence": 1.0,
                        "channel": channel,
                        "source": "turn_memory",
                        "tags": ["session", "user_input"],
                    },
                )
            )

        if output:
            assistant_tone = "gentle" if relation_signal != "antagonistic" else "contained"
            self_state = cls._build_self_state(
                pad=pad,
                drives=drives,
                mood=emotion_label,
                tone=assistant_tone,
                companionship_tension=cls._infer_companionship_tension(relation_signal),
            )
            events.append(
                cls(
                    id=f"evt_{uuid4().hex}",
                    version="2",
                    timestamp=timestamp,
                    session_id=session_id,
                    turn_id=turn_id,
                    actor="assistant",
                    event_type="assistant_output",
                    content=output,
                    state={
                        "self_state": self_state,
                        "relation_state": relation_state,
                        "context_state": context_state,
                        "growth_state": growth_state,
                    },
                    execution=execution,
                    light_insight=insight_payload,
                    meta={
                        "importance": round(float(importance), 2),
                        "confidence": cls._extract_executor_confidence(executor),
                        "channel": channel,
                        "source": "turn_memory",
                        "tags": cls._build_meta_tags(relation_signal, execution),
                    },
                )
            )

        return events

    @staticmethod
    def estimate_importance(user_input: str, output: str) -> float:
        text = f"{user_input} {output}"
        score = 0.45
        if any(
            token in text
            for token in [
                "\u5931\u604b",
                "\u96be\u8fc7",
                "\u7126\u8651",
                "\u5d29\u6e83",
                "\u559c\u6b22",
                "\u8c22\u8c22",
                "\u7ea6\u4f1a",
            ]
        ):
            score += 0.2
        if any(
            token in text
            for token in [
                "\u660e\u5929",
                "\u4e0b\u5468",
                "\u63d0\u9192",
                "\u8ba1\u5212",
                "\u5b89\u6392",
                "\u5f85\u529e",
            ]
        ):
            score += 0.15
        if "?" in text or "\uff1f" in text:
            score += 0.05
        return max(0.1, min(0.95, score))

    @staticmethod
    def _infer_user_emotion(*, user_input: str, emotion_event: Any) -> dict[str, float | str]:
        text = (user_input or "").strip().lower()
        if not text:
            return {"label": "calm", "intensity": 0.3}
        behavior = str(getattr(emotion_event, "behavior", "") or "")
        if "\u5f00\u5fc3" in behavior:
            return {"label": "happy", "intensity": 0.75}
        if "\u751f\u6c14" in behavior:
            return {"label": "angry", "intensity": 0.82}
        if any(
            token in text
            for token in [
                "\u7126\u8651",
                "\u7740\u6025",
                "\u5d29\u6e83",
                "\u70e6",
                "\u70e6\u6b7b\u4e86",
            ]
        ):
            return {"label": "anxious", "intensity": 0.72}
        if any(
            token in text
            for token in [
                "\u6211\u89c9\u5f97",
                "\u8bbe\u8ba1",
                "\u67b6\u6784",
                "\u65b9\u6848",
                "\u8bb0\u5fc6",
                "\u662f\u4e0d\u662f",
                "\u8981\u4e0d\u8981",
            ]
        ):
            return {"label": "focused", "intensity": 0.62}
        if any(
            token in text
            for token in [
                "\uff1f",
                "?",
                "\u4e0d\u61c2",
                "\u4ec0\u4e48\u610f\u601d",
                "\u5e72\u5565",
            ]
        ):
            return {"label": "confused", "intensity": 0.58}
        return {"label": "calm", "intensity": 0.4}

    @staticmethod
    def _infer_relation(*, user_input: str, emotion_event: Any) -> str:
        text = (user_input or "").strip().lower()
        behavior = str(getattr(emotion_event, "behavior", "") or "")
        if "\u751f\u6c14" in behavior or any(
            token in text
            for token in [
                "\u6eda",
                "\u95ed\u5634",
                "\u5783\u573e",
                "\u8ba8\u538c\u4f60",
                "\u70e6\u6b7b\u4e86",
            ]
        ):
            return "antagonistic"
        if "\u5f00\u5fc3" in behavior or any(
            token in text
            for token in [
                "\u8c22\u8c22",
                "\u559c\u6b22\u4f60",
                "\u7231\u4f60",
                "\u68d2",
                "\u9760\u8c31",
            ]
        ):
            return "close"
        if any(
            token in text
            for token in [
                "\u6211\u89c9\u5f97",
                "\u8bbe\u8ba1",
                "\u67b6\u6784",
                "\u65b9\u6848",
                "\u4e00\u8d77",
                "\u4f60\u770b",
                "\u600e\u4e48\u505a",
            ]
        ):
            return "collaborative"
        return "stable"

    @staticmethod
    def _assistant_intensity_from_pad(pad: dict[str, float]) -> float:
        return round(
            max(
                fabs(float(pad.get("pleasure", 0.0) or 0.0)),
                fabs(float(pad.get("arousal", 0.0) or 0.0)),
                fabs(float(pad.get("dominance", 0.0) or 0.0)),
            ),
            2,
        )

    @staticmethod
    def _normalize_text(value: str, *, limit: int = 240) -> str:
        text = " ".join((value or "").split())
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    @staticmethod
    def _compact_section(text: str, limit: int) -> str:
        compact = " ".join((text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 3] + "..."

    @staticmethod
    def _token_overlap(query: str, values: Iterable[str]) -> float:
        query_tokens = set(query.lower().split())
        if not query_tokens:
            return 0.5
        haystack = " ".join(values).lower()
        hay_tokens = set(haystack.split())
        if not hay_tokens:
            return 0.0
        return min(len(query_tokens & hay_tokens) / max(len(query_tokens), 1), 1.0)

    @staticmethod
    def _recency_score(timestamp: str) -> float:
        if not timestamp:
            return 0.3
        try:
            parsed = datetime.fromisoformat(timestamp)
            now = datetime.now(parsed.tzinfo) if parsed.tzinfo is not None else datetime.now()
            age_hours = (now - parsed).total_seconds() / 3600
        except Exception:
            return 0.3
        return max(0.05, min(1.0, 0.995 ** max(age_hours, 0)))

    @classmethod
    def _rank_entries(
        cls,
        entries: list[dict[str, Any]],
        *,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        ranked: list[tuple[float, dict[str, Any]]] = []
        for entry in entries:
            topic = cls._extract_topic(entry)
            relevance = cls._token_overlap(query, [str(entry.get("content", "")), topic])
            recency = cls._recency_score(str(entry.get("timestamp", "") or ""))
            importance = cls._extract_importance(entry)
            confidence = cls._extract_confidence(entry)
            score = 0.4 * relevance + 0.25 * recency + 0.2 * importance + 0.15 * confidence
            ranked.append((score, entry))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [entry for _, entry in ranked[:limit]]

    @staticmethod
    def _extract_turn_id(state: dict[str, Any]) -> str:
        metadata = state.get("metadata")
        if isinstance(metadata, dict):
            message_id = str(metadata.get("message_id", "") or "").strip()
            if message_id:
                return message_id
        for key in ("turn_id", "message_id"):
            value = str(state.get(key, "") or "").strip()
            if value:
                return value
        return f"turn_{uuid4().hex[:12]}"

    @classmethod
    def _build_self_state(
        cls,
        *,
        pad: dict[str, float],
        drives: dict[str, float],
        mood: str,
        tone: str,
        companionship_tension: float,
    ) -> dict[str, Any]:
        return {
            "pad": {
                "pleasure": round(float(pad.get("pleasure", 0.0) or 0.0), 3),
                "arousal": round(float(pad.get("arousal", 0.0) or 0.0), 3),
                "dominance": round(float(pad.get("dominance", 0.0) or 0.0), 3),
            },
            "drives": {
                "social": round(float(drives.get("social", 0.0) or 0.0), 2),
                "energy": round(float(drives.get("energy", 0.0) or 0.0), 2),
            },
            "mood": mood or "stable",
            "tone": tone or "gentle",
            "companionship_tension": round(float(companionship_tension), 2),
        }

    @staticmethod
    def _build_relation_state(signal: str) -> dict[str, Any]:
        if signal == "close":
            return {
                "stage": "building_trust",
                "trust": 0.78,
                "familiarity": 0.72,
                "closeness": 0.76,
            }
        if signal == "collaborative":
            return {
                "stage": "working_together",
                "trust": 0.7,
                "familiarity": 0.62,
                "closeness": 0.64,
            }
        if signal == "antagonistic":
            return {
                "stage": "strained",
                "trust": 0.28,
                "familiarity": 0.35,
                "closeness": 0.22,
            }
        return {
            "stage": "stable",
            "trust": 0.55,
            "familiarity": 0.5,
            "closeness": 0.5,
        }

    @classmethod
    def _build_context_state(
        cls,
        *,
        user_input: str,
        output: str,
        light_insight: dict[str, Any] | None = None,
        execution: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        primary = user_input or output
        topic = cls._normalize_text(primary, limit=48)
        recent_focus: list[str] = []
        if user_input:
            recent_focus.append(cls._normalize_text(user_input, limit=48))
        if output:
            recent_focus.append(cls._normalize_text(output, limit=48))
        unfinished_threads = cls._build_unfinished_threads(
            light_insight=light_insight,
            execution=execution,
        )
        return {
            "topic": topic,
            "intent": cls._infer_intent(user_input=user_input, output=output, execution=execution),
            "recent_focus": recent_focus[:2],
            "unfinished_threads": unfinished_threads,
        }

    @classmethod
    def _build_growth_state(cls, *, light_insight: dict[str, Any] | None = None) -> dict[str, Any]:
        insight = cls._normalize_light_insight(light_insight)
        direct_updates = insight.get("direct_updates") if isinstance(insight.get("direct_updates"), dict) else {}
        stable_preferences = cls._normalize_string_items(direct_updates.get("user_profile"))
        stable_preferences.extend(cls._normalize_string_items(direct_updates.get("soul_preferences")))
        recent_insights = []
        summary = str(insight.get("summary", "") or "").strip()
        context_update = str(insight.get("context_update", "") or "").strip()
        if summary:
            recent_insights.append(summary)
        if context_update and context_update != summary:
            recent_insights.append(context_update)
        return {
            "recent_insights": recent_insights[:2],
            "stable_preferences": stable_preferences[:4],
            "pending_corrections": [],
        }

    @staticmethod
    def _build_light_insight() -> dict[str, Any]:
        return {
            "summary": "",
            "relation_shift": "stable",
            "context_update": "",
            "next_hint": "",
            "execution_review": {
                "summary": "",
                "effectiveness": "none",
                "failure_reason": "",
                "missing_inputs": [],
                "next_execution_hint": "",
            },
            "direct_updates": {
                "user_profile": [],
                "soul_preferences": [],
                "current_state_updates": {
                    "pad": None,
                    "drives": None,
                },
                "applied": {
                    "user": False,
                    "soul": False,
                    "state": False,
                },
                "applied_state_snapshot": {},
            },
        }

    @classmethod
    def _normalize_light_insight(cls, light_insight: dict[str, Any] | None) -> dict[str, Any]:
        default = cls._build_light_insight()
        if not isinstance(light_insight, dict):
            return default

        direct_updates = light_insight.get("direct_updates") if isinstance(light_insight.get("direct_updates"), dict) else {}
        current_state_updates = direct_updates.get("current_state_updates") if isinstance(direct_updates.get("current_state_updates"), dict) else {}
        applied = direct_updates.get("applied") if isinstance(direct_updates.get("applied"), dict) else {}
        execution_review = light_insight.get("execution_review") if isinstance(light_insight.get("execution_review"), dict) else {}

        return {
            "summary": str(light_insight.get("summary", "") or "").strip(),
            "relation_shift": str(light_insight.get("relation_shift", "stable") or "stable").strip() or "stable",
            "context_update": str(light_insight.get("context_update", "") or "").strip(),
            "next_hint": str(light_insight.get("next_hint", "") or "").strip(),
            "execution_review": {
                "summary": str(execution_review.get("summary", "") or "").strip(),
                "effectiveness": str(execution_review.get("effectiveness", "none") or "none").strip() or "none",
                "failure_reason": str(execution_review.get("failure_reason", "") or "").strip(),
                "missing_inputs": cls._normalize_string_items(execution_review.get("missing_inputs")),
                "next_execution_hint": str(execution_review.get("next_execution_hint", "") or "").strip(),
            },
            "direct_updates": {
                "user_profile": cls._normalize_string_items(direct_updates.get("user_profile")),
                "soul_preferences": cls._normalize_string_items(direct_updates.get("soul_preferences")),
                "current_state_updates": {
                    "pad": dict(current_state_updates.get("pad")) if isinstance(current_state_updates.get("pad"), dict) else None,
                    "drives": dict(current_state_updates.get("drives")) if isinstance(current_state_updates.get("drives"), dict) else None,
                },
                "applied": {
                    "user": bool(applied.get("user", False)),
                    "soul": bool(applied.get("soul", False)),
                    "state": bool(applied.get("state", False)),
                },
                "applied_state_snapshot": dict(direct_updates.get("applied_state_snapshot")) if isinstance(direct_updates.get("applied_state_snapshot"), dict) else {},
            },
        }

    @staticmethod
    def _build_execution(*, state: dict[str, Any], executor: Any) -> dict[str, Any]:
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        execution = metadata.get("execution") if isinstance(metadata.get("execution"), dict) else {}
        invoked = executor is not None or bool(execution)
        if not invoked:
            return CognitiveEvent._empty_execution()

        summary = str(getattr(executor, "analysis", "") or "").strip()
        if not summary:
            summary = str(execution.get("summary", "") or "").strip()
        if not summary:
            summary = str(getattr(executor, "request", "") or "").strip()
        control_state = CognitiveEvent._normalize_execution_control_state(
            getattr(executor, "control_state", "") if executor is not None else execution.get("control_state", "")
        )
        status = CognitiveEvent._normalize_execution_status(
            getattr(executor, "status", "") if executor is not None else execution.get("status", ""),
            control_state=control_state,
        )
        recommended_action = CognitiveEvent._normalize_recommended_action(
            getattr(executor, "recommended_action", "") if executor is not None else execution.get("recommended_action", "")
        )
        pending_review = (
            dict(getattr(executor, "pending_review", {}) or {})
            if executor is not None
            else dict(execution.get("pending_review", {}) or {})
        )
        try:
            confidence = float(
                getattr(executor, "confidence", 0.0)
                if executor is not None
                else execution.get("confidence", 0.0)
            )
        except Exception:
            confidence = 0.0
        return {
            "invoked": True,
            "control_state": control_state,
            "status": status,
            "thread_id": str(
                getattr(executor, "thread_id", "")
                or state.get("executor_thread_id", "")
                or execution.get("thread_id", "")
                or metadata.get("executor_thread_id", "")
                or metadata.get("thread_id", "")
                or ""
            ).strip(),
            "run_id": str(
                getattr(executor, "run_id", "")
                or state.get("executor_run_id", "")
                or execution.get("run_id", "")
                or metadata.get("executor_run_id", "")
                or metadata.get("run_id", "")
                or ""
            ).strip(),
            "summary": CognitiveEvent._normalize_text(summary, limit=180),
            "recommended_action": recommended_action,
            "confidence": round(max(0.0, min(1.0, confidence)), 2),
            "missing": [
                str(item).strip()
                for item in list(
                    getattr(executor, "missing", [])
                    or execution.get("missing", [])
                    or []
                )
                if str(item).strip()
            ],
            "pending_review": pending_review,
        }

    @staticmethod
    def _extract_executor_confidence(executor: Any) -> float:
        if executor is None:
            return 0.8
        return round(float(getattr(executor, "confidence", 0.0) or 0.0), 2)

    @staticmethod
    def _build_meta_tags(relation_signal: str, execution: dict[str, Any]) -> list[str]:
        tags = ["session", "assistant_output", relation_signal or "stable"]
        if execution.get("invoked"):
            tags.append("executor_used")
        return tags

    @staticmethod
    def _extract_relation_label(relation_state: Any) -> str:
        if not isinstance(relation_state, dict):
            return "stable"
        return str(
            relation_state.get("signal", "")
            or relation_state.get("stage", "")
            or "stable"
        ).strip()

    @staticmethod
    def _infer_intent(*, user_input: str, output: str, execution: dict[str, Any] | None = None) -> str:
        text = (user_input or output or "").strip().lower()
        if not text:
            return "dialogue"
        if execution and execution.get("invoked"):
            return "execution"
        if any(
            token in text
            for token in [
                "\u8bbe\u8ba1",
                "\u67b6\u6784",
                "\u65b9\u6848",
                "\u600e\u4e48\u505a",
                "\u5b9e\u73b0",
            ]
        ):
            return "discussion"
        if any(
            token in text
            for token in [
                "\u559c\u6b22",
                "\u5e0c\u671b",
                "\u60f3\u8981",
                "\u6211\u662f",
            ]
        ):
            return "self_disclosure"
        if "?" in text or "\uff1f" in text:
            return "question"
        return "dialogue"

    @classmethod
    def _build_unfinished_threads(
        cls,
        *,
        light_insight: dict[str, Any] | None = None,
        execution: dict[str, Any] | None = None,
    ) -> list[str]:
        unfinished: list[str] = []
        insight = cls._normalize_light_insight(light_insight)
        next_hint = str(insight.get("next_hint", "") or "").strip()
        if next_hint:
            unfinished.append(cls._normalize_text(next_hint, limit=80))
        execution_review = insight.get("execution_review") if isinstance(insight.get("execution_review"), dict) else {}
        next_execution_hint = str(execution_review.get("next_execution_hint", "") or "").strip()
        if next_execution_hint:
            unfinished.append(cls._normalize_text(next_execution_hint, limit=80))
        if isinstance(execution, dict) and str(execution.get("status", "") or "") == "need_more":
            missing = execution.get("missing") if isinstance(execution.get("missing"), list) else []
            if missing:
                unfinished.append(
                    "Need user input: " + ", ".join(str(item).strip() for item in missing if str(item).strip())
                )
        deduped: list[str] = []
        seen: set[str] = set()
        for item in unfinished:
            if item and item not in seen:
                deduped.append(item)
                seen.add(item)
        return deduped[:3]

    @staticmethod
    def _normalize_string_items(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def _empty_execution() -> dict[str, Any]:
        return {
            "invoked": False,
            "control_state": "idle",
            "status": "none",
            "thread_id": "",
            "run_id": "",
            "summary": "",
            "recommended_action": "",
            "confidence": 0.0,
            "missing": [],
            "pending_review": {},
        }

    @staticmethod
    def _normalize_recommended_action(value: Any) -> str:
        action = str(value or "").strip().lower()
        if action in {"answer", "ask_user", "continue"}:
            return action
        return ""

    @staticmethod
    def _normalize_execution_control_state(value: Any) -> str:
        control_state = str(value or "idle").strip().lower()
        if control_state in {"idle", "running", "paused", "stopped", "completed"}:
            return control_state
        if control_state == "failed":
            return "stopped"
        return "idle"

    @staticmethod
    def _normalize_execution_status(value: Any, *, control_state: str) -> str:
        status = str(value or "none").strip().lower()
        if status in {"none", "done", "need_more", "failed"}:
            return status
        if status == "needs_input":
            return "need_more"
        if control_state == "paused":
            return "need_more"
        if control_state == "stopped":
            return "failed"
        if control_state == "completed":
            return "done"
        return "none"

    @staticmethod
    def _infer_companionship_tension(relation_signal: str) -> float:
        mapping = {
            "close": 0.72,
            "collaborative": 0.6,
            "stable": 0.5,
            "antagonistic": 0.32,
        }
        return mapping.get(relation_signal, 0.5)

    @classmethod
    def _extract_topic(cls, entry: dict[str, Any]) -> str:
        state = entry.get("state") or {}
        if isinstance(state, dict):
            context_state = state.get("context_state") or {}
            if isinstance(context_state, dict):
                return str(context_state.get("topic", "") or "")
        return ""

    @staticmethod
    def _extract_importance(entry: dict[str, Any]) -> float:
        meta = entry.get("meta") or {}
        if isinstance(meta, dict) and meta.get("importance") is not None:
            return float(meta.get("importance", 0.5) or 0.5)
        return 0.5

    @staticmethod
    def _extract_confidence(entry: dict[str, Any]) -> float:
        meta = entry.get("meta") or {}
        if isinstance(meta, dict) and meta.get("confidence") is not None:
            return float(meta.get("confidence", 0.8) or 0.8)
        execution = entry.get("execution") or {}
        if isinstance(execution, dict) and execution.get("confidence") is not None:
            return float(execution.get("confidence", 0.8) or 0.8)
        return 0.8

    @staticmethod
    def _format_pad(pad: Any) -> str:
        if not isinstance(pad, dict):
            return ""
        try:
            pleasure = float(pad.get("pleasure", 0.0) or 0.0)
            arousal = float(pad.get("arousal", 0.0) or 0.0)
            dominance = float(pad.get("dominance", 0.0) or 0.0)
        except Exception:
            return ""
        return f"(PAD {pleasure:.2f}/{arousal:.2f}/{dominance:.2f})"


__all__ = ["CognitiveEvent"]
