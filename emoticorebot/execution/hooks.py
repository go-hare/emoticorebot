"""Run-scoped hooks shared between the executor and runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

DetailedProgressReporter = Callable[[str, dict[str, Any]], Awaitable[None]]
AuditDecision = Literal["accept", "answer_only", "reject"]
AuditHandler = Callable[["AuditSignal"], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class AuditSignal:
    decision: AuditDecision
    reason: str = ""
    summary: str = ""
    result_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class AuditInterrupt(RuntimeError):
    def __init__(self, signal: AuditSignal) -> None:
        self.signal = signal
        super().__init__(signal.reason or signal.summary or signal.decision)


@dataclass
class RunHooks:
    reporter: DetailedProgressReporter | None = None
    audit_handler: AuditHandler | None = None

    def bind_reporter(self, reporter: DetailedProgressReporter | None) -> None:
        self.reporter = reporter

    def bind_audit_handler(self, handler: AuditHandler | None) -> None:
        self.audit_handler = handler

    def clear(self) -> None:
        self.reporter = None
        self.audit_handler = None

    async def report_progress(self, message: str, **payload: Any) -> str:
        if self.reporter is None:
            raise RuntimeError("当前无法汇报进展（未绑定 runtime 回调）")
        text = str(message or "").strip()
        if not text:
            raise RuntimeError("汇报内容为空")
        await self.reporter(text, dict(payload))
        return f"已汇报: {text}"

    async def audit(
        self,
        *,
        decision: AuditDecision,
        reason: str = "",
        summary: str = "",
        result_text: str = "",
        **metadata: Any,
    ) -> str:
        if self.audit_handler is None:
            raise RuntimeError("当前无法执行 audit_tool（未绑定 runtime 回调）")
        signal = AuditSignal(
            decision=decision,
            reason=str(reason or "").strip(),
            summary=str(summary or "").strip(),
            result_text=str(result_text or "").strip(),
            metadata=dict(metadata),
        )
        await self.audit_handler(signal)
        if signal.decision == "accept":
            return "任务可以开始"
        raise AuditInterrupt(signal)


__all__ = [
    "AuditDecision",
    "AuditHandler",
    "AuditInterrupt",
    "AuditSignal",
    "DetailedProgressReporter",
    "RunHooks",
]
