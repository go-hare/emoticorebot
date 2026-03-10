"""Turn persistence and resume-state helpers for the runtime."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from emoticorebot.session.executor_context import build_executor_context


class RuntimeTurnPersistenceMixin:
    def _build_executor_summary(self, final_state: dict[str, Any]) -> str:
        """Build a compact persisted summary from the current turn's executor execution."""
        executor = final_state.get("executor")
        if not self._executor_has_meaningful_state(executor):
            return ""
        return build_executor_context(
            {
                "execution": {
                    "thread_id": str(getattr(executor, "thread_id", "") or ""),
                    "run_id": str(getattr(executor, "run_id", "") or ""),
                    "control_state": getattr(executor, "control_state", ""),
                    "status": getattr(executor, "status", ""),
                    "summary": getattr(executor, "analysis", ""),
                    "recommended_action": getattr(executor, "recommended_action", ""),
                    "confidence": float(getattr(executor, "confidence", 0.0) or 0.0),
                    "missing": list(getattr(executor, "missing", []) or []),
                }
            }
        )

    @staticmethod
    def _executor_has_meaningful_state(executor: Any | None) -> bool:
        if executor is None:
            return False
        return any(
            [
                str(getattr(executor, "thread_id", "") or "").strip(),
                str(getattr(executor, "run_id", "") or "").strip(),
                str(getattr(executor, "analysis", "") or "").strip(),
                list(getattr(executor, "missing", []) or []),
                dict(getattr(executor, "pending_review", {}) or {}),
                str(getattr(executor, "control_state", "") or "").strip() not in {"", "idle"},
                str(getattr(executor, "status", "") or "").strip() not in {"", "none"},
            ]
        )

    @staticmethod
    def _has_meaningful_execution(execution: dict[str, Any] | None) -> bool:
        if not isinstance(execution, dict) or not execution:
            return False
        control_state = str(execution.get("control_state", "") or "").strip()
        status = str(execution.get("status", "") or "").strip()
        return any(
            [
                bool(execution.get("invoked")),
                str(execution.get("thread_id", "") or "").strip(),
                str(execution.get("run_id", "") or "").strip(),
                str(execution.get("summary", "") or "").strip(),
                list(execution.get("missing", []) or []),
                dict(execution.get("pending_review", {}) or {}),
                control_state not in {"", "idle"},
                status not in {"", "none"},
            ]
        )

    def _resolve_turn_execution(self, final_state: dict[str, Any]) -> dict[str, Any]:
        metadata = final_state.get("metadata") if isinstance(final_state.get("metadata"), dict) else {}
        metadata_execution = metadata.get("execution") if isinstance(metadata.get("execution"), dict) else {}
        paused_execution = metadata.get("paused_execution") if isinstance(metadata.get("paused_execution"), dict) else {}
        persisted_execution = metadata_execution if self._has_meaningful_execution(metadata_execution) else paused_execution
        snapshot = self._snapshot_execution(
            executor=final_state.get("executor"),
            execution=persisted_execution,
            summary=self._build_executor_summary(final_state),
        )
        return snapshot if self._has_meaningful_execution(snapshot) else {}

    def get_execution_state(self, session_key: str) -> dict[str, Any]:
        session = self.sessions.get(session_key)
        if session is None:
            return {}
        return self._extract_last_execution(session)

    def has_active_execution(self, session_key: str) -> bool:
        return any(not task.done() for task in self._active_tasks.get(session_key, []))

    def _snapshot_execution(
        self,
        *,
        executor: Any | None = None,
        execution: dict[str, Any] | None = None,
        summary: str = "",
    ) -> dict[str, Any]:
        base = dict(execution or {})
        if executor is not None:
            executor_snapshot = {
                "invoked": True,
                "thread_id": str(getattr(executor, "thread_id", "") or "").strip(),
                "run_id": str(getattr(executor, "run_id", "") or "").strip(),
                "control_state": str(getattr(executor, "control_state", "") or "idle").strip(),
                "status": str(getattr(executor, "status", "") or "none").strip(),
                "summary": str(summary or getattr(executor, "analysis", "") or "").strip(),
                "recommended_action": str(getattr(executor, "recommended_action", "") or "").strip(),
                "confidence": float(getattr(executor, "confidence", 0.0) or 0.0),
                "missing": list(getattr(executor, "missing", []) or []),
                "pending_review": dict(getattr(executor, "pending_review", {}) or {}),
            }
            executor_has_state = any(
                [
                    executor_snapshot["thread_id"],
                    executor_snapshot["run_id"],
                    executor_snapshot["summary"],
                    executor_snapshot["missing"],
                    executor_snapshot["pending_review"],
                    executor_snapshot["control_state"] not in {"", "idle"},
                    executor_snapshot["status"] not in {"", "none"},
                ]
            )
            if executor_has_state or not base:
                base.update(executor_snapshot)
            elif summary and not str(base.get("summary", "") or "").strip():
                base["summary"] = summary
        elif summary and not str(base.get("summary", "") or "").strip():
            base["summary"] = summary

        invoked = bool(base.get("invoked")) or any(
            [
                str(base.get("thread_id", "") or "").strip(),
                str(base.get("run_id", "") or "").strip(),
                str(base.get("summary", "") or "").strip(),
                list(base.get("missing", []) or []),
                dict(base.get("pending_review", {}) or {}),
                str(base.get("control_state", "") or "").strip() not in {"", "idle"},
            ]
        )
        return {
            "invoked": invoked,
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
    def _execution_event_name(execution: dict[str, Any]) -> str:
        control_state = str(execution.get("control_state", "") or "completed").strip() or "completed"
        status = str(execution.get("status", "") or "none").strip() or "none"
        return f"executor.execution.{control_state}.{status}"

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
        metadata_execution = metadata.get("execution") if isinstance(metadata.get("execution"), dict) else {}
        paused_execution = metadata.get("paused_execution") if isinstance(metadata.get("paused_execution"), dict) else {}
        main_brain = final_state.get("main_brain")
        executor = final_state.get("executor")
        execution = self._resolve_turn_execution(final_state)
        carried_paused_execution = (
            not self._executor_has_meaningful_state(executor)
            and not self._has_meaningful_execution(metadata_execution)
            and self._has_meaningful_execution(paused_execution)
        )

        if main_brain is not None:
            main_brain_payload = {
                "intent": str(getattr(main_brain, "intent", "") or "").strip(),
                "working_hypothesis": str(getattr(main_brain, "working_hypothesis", "") or "").strip(),
                "question_to_executor": str(getattr(main_brain, "question_to_executor", "") or "").strip(),
                "final_decision": str(getattr(main_brain, "final_decision", "") or "").strip(),
                "final_message": str(getattr(main_brain, "final_message", "") or "").strip(),
                "execution_action": str(getattr(main_brain, "execution_action", "") or "").strip(),
                "execution_reason": str(getattr(main_brain, "execution_reason", "") or "").strip(),
            }
            main_brain_payload = {key: value for key, value in main_brain_payload.items() if value}
            if main_brain_payload:
                records.append(
                    {
                        "message_id": message_id,
                        "role": "assistant",
                        "phase": "main_brain",
                        "event": "main_brain.turn.summary",
                        "source": "runtime",
                        "content": json.dumps(main_brain_payload, ensure_ascii=False),
                        "main_brain": main_brain_payload,
                        "timestamp": assistant_timestamp,
                    }
                )

        resume_payload = metadata_execution.get("resume_payload") if isinstance(metadata_execution, dict) else None
        resume_summary = self._summarize_resume_payload(resume_payload)
        if execution.get("invoked") and resume_summary:
            records.append(
                {
                    "message_id": message_id,
                    "role": "assistant",
                    "phase": "main_brain",
                    "event": "main_brain.execution.resume_requested",
                    "source": "runtime",
                    "content": resume_summary,
                    "main_brain": {
                        "execution_action": "resume",
                        "execution_reason": "resume_payload_available",
                    },
                    "execution": execution,
                    "meta": {"resume_payload": resume_payload},
                    "timestamp": assistant_timestamp,
                }
            )

        if execution.get("invoked") and not carried_paused_execution:
            execution_summary_payload = {
                "control_state": execution.get("control_state", "idle"),
                "status": execution.get("status", "none"),
                "thread_id": execution.get("thread_id", ""),
                "run_id": execution.get("run_id", ""),
                "summary": execution.get("summary", ""),
                "missing": execution.get("missing", []),
            }
            if execution.get("pending_review"):
                execution_summary_payload["pending_review"] = execution.get("pending_review", {})
            records.append(
                {
                    "message_id": message_id,
                    "role": "assistant",
                    "phase": "executor",
                    "event": self._execution_event_name(execution),
                    "source": "runtime",
                    "content": json.dumps(execution_summary_payload, ensure_ascii=False),
                    "execution": execution,
                    "timestamp": assistant_timestamp,
                }
            )

        return records

    def _append_internal_execution_event(
        self,
        *,
        session_key: str,
        message_id: str,
        execution: dict[str, Any],
        event: str,
        content: str,
        timestamp: str | None = None,
        source: str = "runtime_control",
    ) -> None:
        if not execution:
            return
        self.sessions.append_internal_messages(
            session_key,
            [
                {
                    "message_id": message_id,
                    "role": "assistant",
                    "phase": "executor",
                    "event": event,
                    "source": source,
                    "content": content,
                    "execution": execution,
                    "timestamp": timestamp or datetime.now().isoformat(),
                }
            ],
        )

    def _append_internal_main_brain_event(
        self,
        *,
        session_key: str,
        message_id: str,
        main_brain: dict[str, Any],
        timestamp: str | None = None,
        event: str = "main_brain.execution.control",
        source: str = "runtime_control",
    ) -> None:
        if not main_brain:
            return
        self.sessions.append_internal_messages(
            session_key,
            [
                {
                    "message_id": message_id,
                    "role": "assistant",
                    "phase": "main_brain",
                    "event": event,
                    "source": source,
                    "content": json.dumps(main_brain, ensure_ascii=False),
                    "main_brain": main_brain,
                    "timestamp": timestamp or datetime.now().isoformat(),
                }
            ],
        )

    def _build_assistant_session_fields(
        self,
        final_state: dict[str, Any],
    ) -> dict[str, Any]:
        main_brain = final_state.get("main_brain")
        if main_brain is None:
            return {}
        execution = self._resolve_turn_execution(final_state)
        fields = {
            key: value
            for key, value in {
                "model_name": str(getattr(main_brain, "model_name", "") or ""),
                "prompt_tokens": int(getattr(main_brain, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(main_brain, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(main_brain, "total_tokens", 0) or 0),
            }.items()
            if value not in ("", 0)
        }
        if execution:
            fields["execution"] = {
                key: value
                for key, value in execution.items()
                if value not in ("", [], {}, None)
            }
        return fields

    def _build_internal_turn_records(
        self,
        final_state: dict[str, Any],
        *,
        assistant_timestamp: str,
        message_id: str,
        existing_internal_count: int = 0,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = self._build_internal_lifecycle_records(
            final_state,
            assistant_timestamp=assistant_timestamp,
            message_id=message_id,
        )
        seen_signatures: set[str] = set()
        internal_history = (final_state.get("internal_history", []) or [])[max(0, existing_internal_count):]
        executor_trace = final_state.get("executor_trace", []) or []
        for source_name, source in (("internal_history", internal_history), ("executor_trace", executor_trace)):
            for item in source:
                if not isinstance(item, dict):
                    continue
                if source_name == "executor_trace" and item.get("phase"):
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
        records.sort(key=lambda item: (str(item.get("timestamp", "") or assistant_timestamp), item.get("role", ""), item.get("event", "")))
        return records

    def _build_user_message_content(self, content: str, media: list[str] | None) -> list[dict[str, Any]]:
        media_items = self.context.build_media_context(media)
        return [{"type": "text", "text": str(content or "")}, *media_items]

    @staticmethod
    def _new_message_id() -> str:
        return f"msg_{uuid4().hex[:16]}"

    def _build_turn_metadata(self, *, session, user_input: str, message_id: str) -> dict[str, Any]:
        metadata: dict[str, Any] = {"message_id": message_id}
        paused_execution = self._build_resume_execution_context(session=session, user_input=user_input)
        if paused_execution:
            metadata["paused_execution"] = paused_execution
        return metadata

    @staticmethod
    def _extract_last_execution(session) -> dict[str, Any]:
        fallback: dict[str, Any] = {}
        for message in reversed(getattr(session, "messages", []) or []):
            if message.get("role") != "assistant":
                continue
            execution = message.get("execution")
            if isinstance(execution, dict):
                if RuntimeTurnPersistenceMixin._has_meaningful_execution(execution):
                    return dict(execution)
                if not fallback:
                    fallback = dict(execution)
        return fallback

    def _build_resume_execution_context(self, *, session, user_input: str) -> dict[str, Any]:
        execution = self._extract_last_execution(session)
        if str(execution.get("control_state", "") or "").strip() != "paused":
            return {}
        resumed = dict(execution)
        resume_input = self._extract_resume_input(
            user_input,
            pending_review=execution.get("pending_review") if isinstance(execution.get("pending_review"), dict) else {},
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
