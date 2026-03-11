"""Turn persistence and resume-state helpers for the runtime."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from emoticorebot.tasks.task_context import build_task_context


class RuntimeTurnPersistenceMixin:
    def _build_task_summary(self, final_state: dict[str, Any]) -> str:
        """Build a compact persisted summary from the current turn's task."""
        task_state = final_state.get("task")
        if not self._task_has_meaningful_state(task_state):
            return ""
        return build_task_context(
            {
                "task": {
                    "thread_id": str(getattr(task_state, "thread_id", "") or ""),
                    "run_id": str(getattr(task_state, "run_id", "") or ""),
                    "control_state": str(getattr(task_state, "control_state", "") or ""),
                    "status": str(getattr(task_state, "status", "") or ""),
                    "summary": str(getattr(task_state, "analysis", "") or ""),
                    "recommended_action": str(getattr(task_state, "recommended_action", "") or ""),
                    "confidence": float(getattr(task_state, "confidence", 0.0) or 0.0),
                    "missing": list(getattr(task_state, "missing", []) or []),
                    "pending_review": dict(getattr(task_state, "pending_review", {}) or {}),
                }
            }
        )

    @staticmethod
    def _task_has_meaningful_state(task_state: Any | None) -> bool:
        if task_state is None:
            return False
        return any(
            [
                str(getattr(task_state, "thread_id", "") or "").strip(),
                str(getattr(task_state, "run_id", "") or "").strip(),
                str(getattr(task_state, "analysis", "") or "").strip(),
                list(getattr(task_state, "missing", []) or []),
                dict(getattr(task_state, "pending_review", {}) or {}),
                str(getattr(task_state, "control_state", "") or "").strip() not in {"", "idle"},
                str(getattr(task_state, "status", "") or "").strip() not in {"", "none"},
            ]
        )

    @staticmethod
    def _has_meaningful_task(task: dict[str, Any] | None) -> bool:
        if not isinstance(task, dict) or not task:
            return False
        control_state = str(task.get("control_state", "") or "").strip()
        status = str(task.get("status", "") or "").strip()
        return any(
            [
                bool(task.get("invoked")),
                str(task.get("task_id", "") or "").strip(),
                str(task.get("title", "") or "").strip(),
                str(task.get("goal", "") or "").strip(),
                str(task.get("thread_id", "") or "").strip(),
                str(task.get("run_id", "") or "").strip(),
                list(task.get("plan", []) or []),
                list(task.get("artifacts", []) or []),
                str(task.get("summary", "") or "").strip(),
                list(task.get("missing", []) or []),
                dict(task.get("pending_review", {}) or {}),
                control_state not in {"", "idle"},
                status not in {"", "none"},
            ]
        )

    def _resolve_turn_task(self, final_state: dict[str, Any]) -> dict[str, Any]:
        metadata = final_state.get("metadata") if isinstance(final_state.get("metadata"), dict) else {}
        metadata_task = metadata.get("task") if isinstance(metadata.get("task"), dict) else {}
        paused_task = metadata.get("paused_task") if isinstance(metadata.get("paused_task"), dict) else {}
        persisted_task = metadata_task if self._has_meaningful_task(metadata_task) else paused_task
        snapshot = self._snapshot_task(
            task_state=final_state.get("task"),
            task=persisted_task,
            summary=self._build_task_summary(final_state),
        )
        return snapshot if self._has_meaningful_task(snapshot) else {}

    def get_task_state(self, session_key: str) -> dict[str, Any]:
        session = self.sessions.get(session_key)
        if session is None:
            return {}
        return self._extract_last_task(session)

    def has_active_task(self, session_key: str) -> bool:
        return any(not task.done() for task in self._active_tasks.get(session_key, []))

    def _snapshot_task(
        self,
        *,
        task_state: Any | None = None,
        task: dict[str, Any] | None = None,
        summary: str = "",
    ) -> dict[str, Any]:
        base = dict(task or {})
        if task_state is not None:
            task_snapshot = {
                "invoked": True,
                "task_id": str(getattr(task_state, "task_id", "") or "").strip(),
                "title": str(getattr(task_state, "title", "") or "").strip(),
                "goal": str(getattr(task_state, "goal", "") or "").strip(),
                "plan": list(getattr(task_state, "plan", []) or []),
                "artifacts": list(getattr(task_state, "artifacts", []) or []),
                "created_at": str(getattr(task_state, "created_at", "") or "").strip(),
                "updated_at": str(getattr(task_state, "updated_at", "") or "").strip(),
                "thread_id": str(getattr(task_state, "thread_id", "") or "").strip(),
                "run_id": str(getattr(task_state, "run_id", "") or "").strip(),
                "control_state": str(getattr(task_state, "control_state", "") or "idle").strip(),
                "status": str(getattr(task_state, "status", "") or "none").strip(),
                "summary": str(summary or getattr(task_state, "analysis", "") or "").strip(),
                "recommended_action": str(getattr(task_state, "recommended_action", "") or "").strip(),
                "confidence": float(getattr(task_state, "confidence", 0.0) or 0.0),
                "missing": list(getattr(task_state, "missing", []) or []),
                "pending_review": dict(getattr(task_state, "pending_review", {}) or {}),
            }
            if any(
                [
                    task_snapshot["task_id"],
                    task_snapshot["title"],
                    task_snapshot["goal"],
                    task_snapshot["thread_id"],
                    task_snapshot["run_id"],
                    task_snapshot["plan"],
                    task_snapshot["artifacts"],
                    task_snapshot["summary"],
                    task_snapshot["missing"],
                    task_snapshot["pending_review"],
                    task_snapshot["control_state"] not in {"", "idle"},
                    task_snapshot["status"] not in {"", "none"},
                ]
            ):
                base.update(task_snapshot)
            elif summary and not str(base.get("summary", "") or "").strip():
                base["summary"] = summary
        elif summary and not str(base.get("summary", "") or "").strip():
            base["summary"] = summary

        invoked = bool(base.get("invoked")) or any(
            [
                str(base.get("task_id", "") or "").strip(),
                str(base.get("title", "") or "").strip(),
                str(base.get("goal", "") or "").strip(),
                str(base.get("thread_id", "") or "").strip(),
                str(base.get("run_id", "") or "").strip(),
                list(base.get("plan", []) or []),
                list(base.get("artifacts", []) or []),
                str(base.get("summary", "") or "").strip(),
                list(base.get("missing", []) or []),
                dict(base.get("pending_review", {}) or {}),
                str(base.get("control_state", "") or "").strip() not in {"", "idle"},
            ]
        )
        return {
            "invoked": invoked,
            "task_id": str(base.get("task_id", "") or "").strip(),
            "title": str(base.get("title", "") or "").strip(),
            "goal": str(base.get("goal", "") or "").strip(),
            "plan": list(base.get("plan", []) or []),
            "artifacts": list(base.get("artifacts", []) or []),
            "created_at": str(base.get("created_at", "") or "").strip(),
            "updated_at": str(base.get("updated_at", "") or "").strip(),
            "thread_id": str(base.get("thread_id", "") or "").strip(),
            "run_id": str(base.get("run_id", "") or "").strip(),
            "control_state": str(base.get("control_state", "") or ("idle" if not invoked else "completed")).strip(),
            "status": str(base.get("status", "") or ("none" if not invoked else "done")).strip(),
            "summary": str(base.get("summary", "") or "").strip(),
            "recommended_action": str(base.get("recommended_action", "") or "").strip(),
            "confidence": float(base.get("confidence", 0.0) or 0.0),
            "missing": [str(item).strip() for item in list(base.get("missing", []) or []) if str(item).strip()],
            "pending_review": dict(base.get("pending_review", {}) or {}),
        }

    @staticmethod
    def _task_event_name(task: dict[str, Any]) -> str:
        control_state = str(task.get("control_state", "") or "completed").strip() or "completed"
        status = str(task.get("status", "") or "none").strip() or "none"
        return f"task.lifecycle.{control_state}.{status}"

    @staticmethod
    def _summarize_resume_payload(resume_payload: Any) -> str:
        if resume_payload in (None, "", [], {}):
            return ""
        if isinstance(resume_payload, dict):
            decisions = resume_payload.get("decisions") if isinstance(resume_payload.get("decisions"), list) else []
            if decisions:
                labels = [
                    str(item.get("type", "") or "").strip()
                    for item in decisions
                    if isinstance(item, dict) and str(item.get("type", "") or "").strip()
                ]
                if labels:
                    return f"恢复决策：{', '.join(labels)}"
            return json.dumps(resume_payload, ensure_ascii=False)
        return str(resume_payload).strip()

    def _build_internal_lifecycle_records(
        self,
        final_state: dict[str, Any],
        *,
        assistant_timestamp: str,
        message_id: str,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        metadata = final_state.get("metadata") if isinstance(final_state.get("metadata"), dict) else {}
        metadata_task = metadata.get("task") if isinstance(metadata.get("task"), dict) else {}
        paused_task = metadata.get("paused_task") if isinstance(metadata.get("paused_task"), dict) else {}

        brain = final_state.get("brain")
        task_state = final_state.get("task")
        task = self._resolve_turn_task(final_state)
        carried_paused_task = (
            not self._task_has_meaningful_state(task_state)
            and not self._has_meaningful_task(metadata_task)
            and self._has_meaningful_task(paused_task)
        )

        if brain is not None:
            brain_payload = {
                "intent": str(getattr(brain, "intent", "") or "").strip(),
                "working_hypothesis": str(getattr(brain, "working_hypothesis", "") or "").strip(),
                "task_brief": str(getattr(brain, "task_brief", "") or "").strip(),
                "final_decision": str(getattr(brain, "final_decision", "") or "").strip(),
                "final_message": str(getattr(brain, "final_message", "") or "").strip(),
                "task_action": str(getattr(brain, "task_action", "") or "").strip(),
                "task_reason": str(getattr(brain, "task_reason", "") or "").strip(),
            }
            brain_payload = {key: value for key, value in brain_payload.items() if value}
            if brain_payload:
                records.append(
                    {
                        "message_id": message_id,
                        "role": "assistant",
                        "phase": "brain",
                        "event": "brain.turn.summary",
                        "source": "runtime",
                        "content": json.dumps(brain_payload, ensure_ascii=False),
                        "brain": brain_payload,
                        "timestamp": assistant_timestamp,
                    }
                )

        resume_payload = metadata_task.get("resume_payload") if isinstance(metadata_task, dict) else None
        resume_summary = self._summarize_resume_payload(resume_payload)
        if task.get("invoked") and resume_summary:
            records.append(
                {
                    "message_id": message_id,
                    "role": "assistant",
                    "phase": "brain",
                    "event": "brain.task.resume_requested",
                    "source": "runtime",
                    "content": resume_summary,
                    "brain": {
                        "task_action": "resume_task",
                        "task_reason": "resume_payload_available",
                    },
                    "task": task,
                    "meta": {"resume_payload": resume_payload},
                    "timestamp": assistant_timestamp,
                }
            )

        if task.get("invoked") and not carried_paused_task:
            task_payload = {
                "task_id": task.get("task_id", ""),
                "title": task.get("title", ""),
                "goal": task.get("goal", ""),
                "control_state": task.get("control_state", "idle"),
                "status": task.get("status", "none"),
                "thread_id": task.get("thread_id", ""),
                "run_id": task.get("run_id", ""),
                "summary": task.get("summary", ""),
                "missing": task.get("missing", []),
            }
            if task.get("plan"):
                task_payload["plan"] = task.get("plan", [])
            if task.get("artifacts"):
                task_payload["artifacts"] = task.get("artifacts", [])
            if task.get("created_at"):
                task_payload["created_at"] = task.get("created_at", "")
            if task.get("updated_at"):
                task_payload["updated_at"] = task.get("updated_at", "")
            if task.get("pending_review"):
                task_payload["pending_review"] = task.get("pending_review", {})
            records.append(
                {
                    "message_id": message_id,
                    "role": "assistant",
                    "phase": "task",
                    "event": self._task_event_name(task),
                    "source": "runtime",
                    "content": json.dumps(task_payload, ensure_ascii=False),
                    "task": task,
                    "timestamp": assistant_timestamp,
                }
            )

        return records

    def _append_internal_task_event(
        self,
        *,
        session_key: str,
        message_id: str,
        task: dict[str, Any],
        event: str,
        content: str,
        timestamp: str | None = None,
        source: str = "runtime_control",
    ) -> None:
        if not task:
            return
        self.sessions.append_internal_messages(
            session_key,
            [
                {
                    "message_id": message_id,
                    "role": "assistant",
                    "phase": "task",
                    "event": event,
                    "source": source,
                    "content": content,
                    "task": task,
                    "timestamp": timestamp or datetime.now().isoformat(),
                }
            ],
        )

    def _append_internal_brain_event(
        self,
        *,
        session_key: str,
        message_id: str,
        brain: dict[str, Any],
        timestamp: str | None = None,
        event: str = "brain.task.control",
        source: str = "runtime_control",
    ) -> None:
        if not brain:
            return
        self.sessions.append_internal_messages(
            session_key,
            [
                {
                    "message_id": message_id,
                    "role": "assistant",
                    "phase": "brain",
                    "event": event,
                    "source": source,
                    "content": json.dumps(brain, ensure_ascii=False),
                    "brain": brain,
                    "timestamp": timestamp or datetime.now().isoformat(),
                }
            ],
        )

    def _build_assistant_session_fields(self, final_state: dict[str, Any]) -> dict[str, Any]:
        brain = final_state.get("brain")
        if brain is None:
            return {}
        task = self._resolve_turn_task(final_state)
        fields = {
            key: value
            for key, value in {
                "model_name": str(getattr(brain, "model_name", "") or ""),
                "prompt_tokens": int(getattr(brain, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(brain, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(brain, "total_tokens", 0) or 0),
            }.items()
            if value not in ("", 0)
        }
        if task:
            fields["task"] = {key: value for key, value in task.items() if value not in ("", [], {}, None)}
        return fields

    def _build_internal_turn_records(
        self,
        final_state: dict[str, Any],
        *,
        assistant_timestamp: str,
        message_id: str,
        existing_internal_count: int = 0,
    ) -> list[dict[str, Any]]:
        records = self._build_internal_lifecycle_records(
            final_state,
            assistant_timestamp=assistant_timestamp,
            message_id=message_id,
        )
        seen_signatures: set[str] = set()
        internal_history = (final_state.get("internal_history", []) or [])[max(0, existing_internal_count):]
        task_trace = final_state.get("task_trace", []) or []
        for source_name, source in (("internal_history", internal_history), ("task_trace", task_trace)):
            for item in source:
                if not isinstance(item, dict):
                    continue
                if source_name == "task_trace" and item.get("phase"):
                    continue
                payload = dict(item)
                payload.setdefault("message_id", message_id)
                payload.setdefault("timestamp", assistant_timestamp)
                signature = str(payload.pop("trace_signature", "") or "").strip()
                if signature:
                    if signature in seen_signatures:
                        continue
                    seen_signatures.add(signature)
                records.append(payload)
        records.sort(
            key=lambda item: (
                str(item.get("timestamp", "") or assistant_timestamp),
                item.get("role", ""),
                item.get("event", ""),
            )
        )
        return records

    def _build_user_message_content(self, content: str, media: list[str] | None) -> list[dict[str, Any]]:
        media_items = self.context.build_media_context(media)
        return [{"type": "text", "text": str(content or "")}, *media_items]

    @staticmethod
    def _new_message_id() -> str:
        return f"msg_{uuid4().hex[:16]}"

    def _build_turn_metadata(self, *, session, user_input: str, message_id: str) -> dict[str, Any]:
        metadata: dict[str, Any] = {"message_id": message_id}
        paused_task = self._build_resume_task_context(session=session, user_input=user_input)
        if paused_task:
            metadata["paused_task"] = paused_task
        return metadata

    @staticmethod
    def _extract_last_task(session) -> dict[str, Any]:
        fallback: dict[str, Any] = {}
        for message in reversed(getattr(session, "messages", []) or []):
            if message.get("role") != "assistant":
                continue
            task = message.get("task")
            if isinstance(task, dict):
                if RuntimeTurnPersistenceMixin._has_meaningful_task(task):
                    return dict(task)
                if not fallback:
                    fallback = dict(task)
        return fallback

    def _build_resume_task_context(self, *, session, user_input: str) -> dict[str, Any]:
        task = self._extract_last_task(session)
        if str(task.get("control_state", "") or "").strip() != "paused":
            return {}
        resumed = dict(task)
        resume_input = self._extract_resume_input(
            user_input,
            pending_review=task.get("pending_review") if isinstance(task.get("pending_review"), dict) else {},
        )
        if resume_input not in (None, "", [], {}):
            resumed["resume_payload"] = resume_input
        return resumed

    @staticmethod
    def _extract_resume_input(user_input: str, *, pending_review: dict[str, Any] | None = None) -> Any:
        text = str(user_input or "").strip()
        if not text:
            return ""
        parsed = RuntimeTurnPersistenceMixin._parse_resume_json(text)
        if parsed is not None:
            if pending_review or any(key in parsed for key in ("decisions", "type", "edited_action")):
                return RuntimeTurnPersistenceMixin._normalize_resume_payload(parsed, pending_review=pending_review)
            return ""

        if not pending_review:
            return ""

        lowered = text.lower()
        approve_prefixes = ("approve", "ok", "yes", "resume", "continue", "go ahead", "继续", "继续吧", "同意", "可以")
        reject_prefixes = ("reject", "no", "停止执行", "拒绝", "不要执行")
        edit_prefixes = ("edit", "编辑", "修改")

        if any(
            lowered == prefix
            or lowered.startswith(f"{prefix} ")
            or lowered.startswith(f"{prefix}:")
            or lowered.startswith(f"{prefix}：")
            for prefix in approve_prefixes
        ):
            return RuntimeTurnPersistenceMixin._build_review_decisions("approve", pending_review=pending_review)

        if any(
            lowered == prefix
            or lowered.startswith(f"{prefix} ")
            or lowered.startswith(f"{prefix}:")
            or lowered.startswith(f"{prefix}：")
            for prefix in reject_prefixes
        ):
            reason = RuntimeTurnPersistenceMixin._strip_resume_prefix(text, reject_prefixes)
            return RuntimeTurnPersistenceMixin._build_review_decisions(
                "reject",
                pending_review=pending_review,
                message=reason or text,
            )

        if any(
            lowered == prefix
            or lowered.startswith(f"{prefix} ")
            or lowered.startswith(f"{prefix}:")
            or lowered.startswith(f"{prefix}：")
            for prefix in edit_prefixes
        ):
            edit_text = RuntimeTurnPersistenceMixin._strip_resume_prefix(text, edit_prefixes)
            return RuntimeTurnPersistenceMixin._build_edit_resume_payload(edit_text, pending_review=pending_review) or text

        return ""

    @staticmethod
    def _parse_resume_json(text: str) -> dict[str, Any] | None:
        raw = str(text or "").strip()
        if not raw.startswith("{"):
            return None
        try:
            payload = json.loads(raw)
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _normalize_resume_payload(payload: dict[str, Any], *, pending_review: dict[str, Any] | None = None) -> Any:
        if "decisions" in payload:
            return payload
        decision_type = str(payload.get("type", "") or "").strip().lower()
        if decision_type in {"approve", "reject"}:
            return RuntimeTurnPersistenceMixin._build_review_decisions(
                decision_type,
                pending_review=pending_review,
                message=str(payload.get("message", "") or "").strip(),
            )
        if decision_type == "edit" or "edited_action" in payload:
            edit_payload = dict(payload)
            if decision_type == "edit":
                edit_payload.pop("type", None)
            return RuntimeTurnPersistenceMixin._build_edit_resume_payload(edit_payload, pending_review=pending_review) or payload
        return payload

    @staticmethod
    def _build_review_decisions(
        decision_type: str,
        *,
        pending_review: dict[str, Any] | None = None,
        message: str = "",
    ) -> dict[str, Any]:
        action_requests = (pending_review or {}).get("action_requests")
        count = len(action_requests) if isinstance(action_requests, list) and action_requests else 1
        decisions: list[dict[str, Any]] = []
        for _ in range(count):
            decision: dict[str, Any] = {"type": decision_type}
            if decision_type == "reject" and message:
                decision["message"] = message
            decisions.append(decision)
        return {"decisions": decisions}

    @staticmethod
    def _build_edit_resume_payload(edit_input: Any, *, pending_review: dict[str, Any] | None = None) -> dict[str, Any] | None:
        pending_review = pending_review or {}
        action_requests = pending_review.get("action_requests")
        if not isinstance(action_requests, list) or len(action_requests) != 1:
            if isinstance(edit_input, dict) and "decisions" in edit_input:
                return edit_input
            return None

        action = action_requests[0] if isinstance(action_requests[0], dict) else {}
        action_name = str(action.get("name", "") or "").strip()
        if not action_name:
            return None

        if isinstance(edit_input, dict) and "edited_action" in edit_input:
            edited_action = edit_input.get("edited_action")
            if isinstance(edited_action, dict):
                return {"decisions": [{"type": "edit", "edited_action": edited_action}]}
            return None

        edited_action: dict[str, Any] = {"name": action_name, "args": dict(action.get("args", {}) or {})}
        if isinstance(edit_input, dict):
            if str(edit_input.get("name", "") or "").strip():
                edited_action["name"] = str(edit_input.get("name", "") or "").strip()
            if isinstance(edit_input.get("args"), dict):
                edited_action["args"] = dict(edit_input.get("args") or {})
            else:
                edited_action["args"] = dict(edit_input)
                edited_action["args"].pop("name", None)
        else:
            value = str(edit_input or "").strip()
            if not value:
                return None
            arg_keys = list(edited_action["args"].keys())
            if "content" in edited_action["args"]:
                edited_action["args"]["content"] = value
            elif len(arg_keys) == 1:
                edited_action["args"][arg_keys[0]] = value
            else:
                edited_action["args"] = {"content": value}

        return {"decisions": [{"type": "edit", "edited_action": edited_action}]}

    @staticmethod
    def _strip_resume_prefix(text: str, prefixes: tuple[str, ...]) -> str:
        raw = str(text or "").strip()
        lowered = raw.lower()
        for prefix in prefixes:
            if lowered == prefix:
                return ""
            if lowered.startswith(f"{prefix} "):
                return raw[len(prefix):].strip()
            if lowered.startswith(f"{prefix}:") or lowered.startswith(f"{prefix}："):
                return raw[len(prefix) + 1 :].strip()
        return raw
