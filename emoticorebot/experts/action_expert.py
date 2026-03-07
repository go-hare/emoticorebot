"""Primary action expert backed by the existing IQ tool loop."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from emoticorebot.experts.base import ExpertContext, ExpertPacket


class ActionExpert:
    name = "ActionExpert"

    def __init__(
        self,
        runner: Callable[[ExpertContext], Awaitable[dict[str, Any]]],
    ) -> None:
        self._runner = runner

    async def run(self, context: ExpertContext) -> ExpertPacket:
        packet = await self._runner(context)
        return ExpertPacket(
            expert=self.name,
            status=str(packet.get("status", "uncertain") or "uncertain"),
            answer=str(packet.get("analysis", "") or "").strip(),
            confidence=float(packet.get("confidence", 0.0) or 0.0),
            evidence=list(packet.get("evidence", []) or []),
            risks=list(packet.get("risks", []) or []),
            missing=list(packet.get("missing", []) or []),
            proposed_action=str(packet.get("recommended_action", "") or ""),
            metadata={
                "tool_calls": list(packet.get("tool_calls", []) or []),
                "iterations": int(packet.get("iterations", 0) or 0),
                "options": list(packet.get("options", []) or []),
                "rationale_summary": str(packet.get("rationale_summary", "") or ""),
                "raw_packet": packet,
            },
        )


__all__ = ["ActionExpert"]
