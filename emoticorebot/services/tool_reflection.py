"""Immediate tool reflection and persistence."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger


class ToolLightReflectionService:
    """Persist per-tool execution reflections for later deep consolidation."""

    _ERROR_HINT = "\n\n[Analyze the error above and try a different approach.]"

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory_file = workspace / "memory" / "tool_memory.jsonl"
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)

    async def record_execution(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return

        tool_name = str(event.get("tool_name", "") or "").strip()
        if not tool_name or tool_name == "message":
            return

        raw_result = self._strip_error_hint(str(event.get("raw_result", "") or ""))
        success = bool(event.get("success", False))
        params = event.get("params") if isinstance(event.get("params"), dict) else {}
        context = event.get("context") if isinstance(event.get("context"), dict) else {}
        summary = self._build_summary(tool_name=tool_name, success=success, raw_result=raw_result)
        failure_reason = self._extract_failure_reason(raw_result) if not success else ""
        missing_inputs = self._extract_missing_inputs(raw_result)
        effectiveness = self._classify_effectiveness(success=success, raw_result=raw_result)
        next_hint = self._build_next_hint(tool_name=tool_name, failure_reason=failure_reason, missing_inputs=missing_inputs)

        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "tool_light_reflection",
            "tool_name": tool_name,
            "params": params,
            "summary": summary,
            "effectiveness": effectiveness,
            "success": success,
            "failure_reason": failure_reason,
            "missing_inputs": missing_inputs,
            "next_hint": next_hint,
            "result_preview": self._compact(raw_result, limit=280),
            "context": {
                "session_key": str(context.get("session_key", "") or ""),
                "channel": str(context.get("channel", "") or ""),
                "chat_id": str(context.get("chat_id", "") or ""),
                "message_id": str(context.get("message_id", "") or ""),
                "source": str(context.get("source", "") or "executor"),
            },
        }

        try:
            with self.memory_file.open("a", encoding="utf-8") as file_obj:
                file_obj.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning("ToolLightReflectionService write failed: {}", exc)

    @classmethod
    def _strip_error_hint(cls, text: str) -> str:
        if text.endswith(cls._ERROR_HINT):
            return text[: -len(cls._ERROR_HINT)].rstrip()
        return text.strip()

    @staticmethod
    def _build_summary(*, tool_name: str, success: bool, raw_result: str) -> str:
        if success:
            return f"{tool_name} completed and returned usable output."
        if "not found" in raw_result.lower():
            return f"{tool_name} failed because the target resource or tool was not found."
        if "invalid parameters" in raw_result.lower():
            return f"{tool_name} failed due to invalid or incomplete parameters."
        return f"{tool_name} failed and needs a different execution path."

    @staticmethod
    def _classify_effectiveness(*, success: bool, raw_result: str) -> str:
        text = raw_result.lower()
        if not success:
            return "low"
        if not raw_result.strip():
            return "low"
        if any(token in text for token in ["no results", "empty", "nothing found", "0 results"]):
            return "medium"
        return "high"

    @staticmethod
    def _extract_failure_reason(raw_result: str) -> str:
        text = raw_result.strip()
        lowered = text.lower()
        if "invalid parameters" in lowered:
            return "invalid_parameters"
        if "not found" in lowered:
            return "not_found"
        if "permission" in lowered or "access is denied" in lowered:
            return "permission_denied"
        if "timeout" in lowered:
            return "timeout"
        if "network" in lowered or "dns" in lowered or "connection" in lowered:
            return "network_error"
        if text.startswith("Error"):
            return "tool_error"
        return "unknown_failure"

    @staticmethod
    def _extract_missing_inputs(raw_result: str) -> list[str]:
        text = raw_result.strip()
        missing: list[str] = []

        invalid_match = re.search(r"Invalid parameters for tool '[^']+':\s*(.+)", text, re.IGNORECASE)
        if invalid_match:
            for part in invalid_match.group(1).split(";"):
                cleaned = part.strip()
                if cleaned:
                    missing.append(cleaned)

        required_matches = re.findall(r"missing required ([\w.\[\]-]+)", text, re.IGNORECASE)
        for item in required_matches:
            cleaned = item.strip()
            if cleaned and cleaned not in missing:
                missing.append(cleaned)

        return missing[:8]

    @staticmethod
    def _build_next_hint(*, tool_name: str, failure_reason: str, missing_inputs: list[str]) -> str:
        if missing_inputs:
            return f"Fill the missing inputs before retrying {tool_name}."
        if failure_reason == "not_found":
            return f"Check the target path, identifier, or URL before retrying {tool_name}."
        if failure_reason == "invalid_parameters":
            return f"Reduce {tool_name} to the minimum valid parameter set and retry."
        if failure_reason == "permission_denied":
            return f"Switch to an allowed workspace path or request permission before retrying {tool_name}."
        if failure_reason == "timeout":
            return f"Retry {tool_name} with a simpler scope or a longer timeout."
        if failure_reason == "network_error":
            return f"Retry {tool_name} after checking connectivity or narrowing the request."
        if tool_name in {"web_search", "web_fetch"}:
            return f"Tighten the query or source selection before retrying {tool_name}."
        if tool_name in {"read_file", "write_file", "edit_file", "list_dir"}:
            return f"Verify the workspace path and file target before retrying {tool_name}."
        if tool_name == "exec":
            return "Retry with a narrower command and explicit working directory."
        return f"Retry {tool_name} with a narrower, better-scoped request."

    @staticmethod
    def _compact(text: str, *, limit: int) -> str:
        compact = " ".join((text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 3] + "..."


__all__ = ["ToolLightReflectionService"]
