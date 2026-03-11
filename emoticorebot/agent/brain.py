"""Thin main-brain service with a small public API."""

from __future__ import annotations

from typing import Any

from emoticorebot.agent.brain_reflection import should_deep_reflect
from emoticorebot.agent.brain_task_signal import handle_task_signal as plan_task_signal
from emoticorebot.agent.brain_user_turn import handle_user_turn as plan_user_turn
from emoticorebot.agent.context import ContextBuilder
from emoticorebot.agent.state import BrainControlPacket
from emoticorebot.runtime.event_bus import RuntimeEventBus, TaskSignal
from emoticorebot.utils.llm_utils import extract_message_metrics, extract_message_text


class BrainService:
    """Brain entrypoints: user turn, task signal, human interaction, reflection."""

    def __init__(
        self,
        brain_llm,
        context_builder: ContextBuilder,
        *,
        bus: RuntimeEventBus | None = None,
    ):
        self.brain_llm = brain_llm
        self.context = context_builder
        self.bus = bus
        self.memory_service = None

    async def handle_user_turn(
        self,
        *,
        user_input: str,
        history: list[dict[str, Any]],
        emotion: str,
        pad: dict[str, float],
        paused_task: dict[str, Any] | None = None,
        message_id: str = "",
        channel: str = "",
        chat_id: str = "",
        session_id: str = "",
    ) -> BrainControlPacket:
        control = await plan_user_turn(
            self,
            user_input=user_input,
            history=history,
            emotion=emotion,
            pad=pad,
            paused_task=paused_task,
            channel=channel,
            chat_id=chat_id,
            session_id=session_id,
        )
        message = str(control.get("message", "") or "").strip()
        if message and str(control.get("final_decision", "") or "").strip() in {"answer", "ask_user"}:
            control["message"] = await self.handle_human_interaction(
                content=message,
                session_id=session_id,
                message_id=message_id,
                task_id=str((control.get("task", {}) or {}).get("task_id", "") or ""),
                channel=channel,
                chat_id=chat_id,
                event="brain.user_turn",
                payload={
                    "producer": "brain",
                    "final_decision": str(control.get("final_decision", "") or ""),
                    "reason": str(control.get("reason", "") or ""),
                },
            )
        return control

    async def handle_task_signal(
        self,
        *,
        signal: TaskSignal,
        user_input: str,
        history: list[dict[str, Any]],
        emotion: str,
        pad: dict[str, float],
        brain_intent: str,
        brain_working_hypothesis: str,
        loop_count: int,
        max_loop_rounds: int,
        task: dict[str, Any] | None = None,
        channel: str = "",
        chat_id: str = "",
        session_id: str = "",
    ) -> BrainControlPacket:
        control = await plan_task_signal(
            self,
            signal=signal,
            user_input=user_input,
            history=history,
            emotion=emotion,
            pad=pad,
            brain_intent=brain_intent,
            brain_working_hypothesis=brain_working_hypothesis,
            loop_count=loop_count,
            max_loop_rounds=max_loop_rounds,
            task=task,
            channel=channel,
            chat_id=chat_id,
            session_id=session_id,
        )
        message = str(control.get("message", "") or "").strip()
        if message and (
            bool(control.get("notify_user", False))
            or str(control.get("final_decision", "") or "").strip() in {"answer", "ask_user"}
        ):
            control["message"] = await self.handle_human_interaction(
                content=message,
                session_id=session_id,
                message_id=signal.message_id,
                task_id=signal.task_id,
                channel=channel,
                chat_id=chat_id,
                event="brain.task_signal",
                payload={
                    "producer": "brain",
                    "signal_event": signal.event,
                    "reason": str(control.get("reason", "") or ""),
                    "final_decision": str(control.get("final_decision", "") or ""),
                },
            )
        return control

    async def handle_human_interaction(
        self,
        *,
        content: str,
        session_id: str,
        message_id: str = "",
        task_id: str = "",
        channel: str = "",
        chat_id: str = "",
        event: str = "brain.human_interaction",
        payload: dict[str, Any] | None = None,
    ) -> str:
        message = str(content or "").strip()
        if not message or self.bus is None:
            return message
        signal_payload = {
            "producer": "brain",
            "channel": channel,
            "chat_id": chat_id,
        }
        if payload:
            signal_payload.update(dict(payload))
        await self.bus.publish_task_signal(
            TaskSignal(
                session_id=session_id,
                message_id=message_id,
                task_id=task_id,
                event=event,
                content=message,
                payload=signal_payload,
            )
        )
        return message

    async def turn_reflect(self, state: dict[str, Any]):
        if self.memory_service is None:
            return None
        return await self.memory_service.write_turn_reflection(state)

    async def deep_reflect(self, *, reason: str = "", warm_limit: int = 15):
        if self.memory_service is None:
            return None
        return await self.memory_service.run_deep_reflection(reason=reason, warm_limit=warm_limit)

    async def _generate_proactive(
        self,
        prompt: str,
        *,
        channel: str = "",
        chat_id: str = "",
        session_id: str = "",
    ) -> str:
        del channel, chat_id, session_id
        system_prompt = self.context.build_brain_system_prompt(query=prompt)
        response = await self.brain_llm.ainvoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]
        )
        return extract_message_text(response).strip()

    def _should_deep_reflect(
        self,
        *,
        state: dict[str, Any],
        importance: float,
        task: dict[str, Any],
        turn_reflection: dict[str, Any],
    ) -> tuple[bool, str]:
        return should_deep_reflect(
            state=state,
            importance=importance,
            task=task,
            turn_reflection=turn_reflection,
        )

    async def _run_brain_task(
        self,
        *,
        history: list[dict[str, Any]],
        current_message: str,
        current_emotion: str,
        pad_state: tuple[float, float, float] | None,
        internal_task_summaries: list[str] | None,
        channel: str,
        chat_id: str,
        session_id: str,
        query: str,
        retrieval_focus: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        del channel, chat_id, session_id
        records = self.context.query_brain_memories(query=query, limit=8)
        messages = self.context.build_messages(
            history=history,
            current_message=current_message,
            current_emotion=current_emotion,
            pad_state=pad_state,
            internal_task_summaries=internal_task_summaries,
            query=query,
        )
        response = await self.brain_llm.ainvoke(messages)
        metrics = extract_message_metrics(response)
        metrics.update(
            {
                "retrieval_query": query,
                "retrieval_focus": list(retrieval_focus or []),
                "retrieved_memory_ids": [
                    str(record.get("id", "") or "")
                    for record in records
                    if str(record.get("id", "") or "")
                ],
            }
        )
        return extract_message_text(response), metrics


__all__ = ["BrainService"]
