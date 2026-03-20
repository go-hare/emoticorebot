"""Short main-brain runtime for user turns and execution followups."""

from __future__ import annotations

import asyncio
from typing import Any, cast
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.execution.store import ExecutionRecord, ExecutionStore
from emoticorebot.protocol.commands import FollowupContextPayload, MainBrainReplyRequestPayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import MainBrainFollowupReadyPayload, MainBrainReplyReadyPayload, TurnInputPayload
from emoticorebot.protocol.reflection_models import ReflectionSignalPayload
from emoticorebot.protocol.task_models import MessageRef
from emoticorebot.protocol.topics import EventType

from .packet import normalize_decision_packet, parse_decision_packet
from .reply_policy import ReplyPolicy


class MainBrainFrontLoop:
    def __init__(
        self,
        *,
        bus: PriorityPubSubBus,
        task_store: ExecutionStore,
        main_brain_llm: Any | None = None,
        context_builder: Any | None = None,
        session_runtime: Any | None = None,
        reply_policy: ReplyPolicy | None = None,
    ) -> None:
        self._bus = bus
        self._tasks = task_store
        self._main_brain_llm = main_brain_llm
        self._context_builder = context_builder
        self._session_runtime = session_runtime
        self._reply_policy = reply_policy or ReplyPolicy()
        self._session_origins: dict[str, MessageRef] = {}
        self._inflight: set[asyncio.Task[None]] = set()

    def register(self) -> None:
        self._bus.subscribe(
            consumer="main_brain",
            event_type=EventType.MAIN_BRAIN_COMMAND_REPLY_REQUESTED,
            handler=self._on_reply_request,
        )

    async def stop(self) -> None:
        for task in list(self._inflight):
            task.cancel()
        if self._inflight:
            await asyncio.gather(*self._inflight, return_exceptions=True)

    async def _on_reply_request(self, event: BusEnvelope[object]) -> None:
        request = cast(BusEnvelope[MainBrainReplyRequestPayload], event)
        if request.payload.followup_context is not None:
            task = asyncio.create_task(self._run_followup(request), name=f"main-brain-followup:{request.session_id or 'default'}")
        else:
            task = asyncio.create_task(self._run_user_turn(request), name=f"main-brain-turn:{request.session_id or 'default'}")
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)
        task.add_done_callback(self._consume_background_result)

    async def _run_user_turn(self, request: BusEnvelope[MainBrainReplyRequestPayload]) -> None:
        if self._main_brain_llm is None:
            raise RuntimeError("MainBrainFrontLoop requires main_brain_llm")
        payload = request.payload.turn_input
        if payload is None:
            raise RuntimeError("main brain reply requests require turn_input")

        user_text = self._user_text(payload)
        origin = payload.message
        session_id = str(request.session_id or "").strip()
        self._session_origins[session_id] = origin
        messages = self._build_messages(
            session_id=session_id,
            user_text=user_text,
            history_context=str((payload.metadata or {}).get("history_context", "") or "").strip(),
        )
        raw = await self._invoke_model(messages)
        packet = normalize_decision_packet(
            parse_decision_packet(raw),
            current_context={
                "active_task_id": self._latest_active_task_id(session_id),
                "latest_task_id": self._latest_task_id(session_id),
            },
        )

        task_action = str(packet.get("task_action", "none") or "none").strip()
        task_mode = str(packet.get("task_mode", "skip") or "skip").strip()
        reply_text = str(packet.get("final_message", "") or "").strip() or "收到。"
        task_payload = packet.get("task") if isinstance(packet.get("task"), dict) else {}
        task_id = str(task_payload.get("task_id", "") or "").strip() or None
        current_delivery_mode = str((payload.metadata or {}).get("current_delivery_mode", "") or "").strip() or ("stream" if payload.input_mode == "stream" else "inline")

        execution_request = self._build_execution_request(
            request=request,
            payload=payload,
            user_text=user_text,
            task_action=task_action,
            task_mode=task_mode,
            task=task_payload,
        )
        await self._bus.publish(
            build_envelope(
                event_type=EventType.MAIN_BRAIN_EVENT_REPLY_READY,
                source="main_brain",
                target="broadcast",
                session_id=request.session_id,
                turn_id=request.turn_id,
                task_id=task_id or request.task_id,
                correlation_id=task_id or request.correlation_id or request.turn_id,
                causation_id=request.event_id,
                payload=MainBrainReplyReadyPayload(
                    request_id=f"main_brain_reply_{uuid4().hex[:12]}",
                    reply_text=reply_text,
                    reply_kind="status" if task_action in {"create_task", "cancel_task"} else "answer",
                    delivery_target={
                        "delivery_mode": current_delivery_mode if current_delivery_mode in {"inline", "push", "stream"} else "inline",
                        "channel": origin.channel,
                        "chat_id": origin.chat_id,
                    },
                    origin_message=origin,
                    invoke_execution=task_action in {"create_task", "cancel_task"},
                    execution_request=execution_request,
                    related_task_id=task_id,
                    stream_id=f"stream_{request.turn_id}" if current_delivery_mode == "stream" else None,
                    stream_state="close" if current_delivery_mode == "stream" else None,
                    metadata={"task_action": task_action, "task_mode": task_mode},
                ),
            )
        )
        await self._publish_reflection(
            request,
            reason="user_turn",
            metadata={
                "reflection_input": {
                    "session_id": request.session_id or "",
                    "turn_id": request.turn_id or "",
                    "message_id": origin.message_id or "",
                    "source_type": "user_turn",
                    "user_input": user_text,
                    "assistant_output": reply_text,
                    "channel": origin.channel or "",
                    "chat_id": origin.chat_id or "",
                    "main_brain": dict(packet),
                    "task": self._task_snapshot(self._tasks.get(task_id or "")),
                    "execution": self._execution_from_packet(task_action=task_action, task_reason=str(packet.get("task_reason", "") or "").strip(), task=self._tasks.get(task_id or "")),
                    "metadata": {"source_event_type": request.event_type},
                }
            },
        )

    async def _run_followup(self, request: BusEnvelope[MainBrainReplyRequestPayload]) -> None:
        followup = request.payload.followup_context
        if followup is None:
            raise RuntimeError("followup request requires followup_context")
        task = self._tasks.get(request.task_id or "")
        reply_text, reply_kind = self._followup_reply(task=task, followup=followup)
        origin = task.origin_message if task is not None and task.origin_message is not None else self._session_origins.get(str(request.session_id or "").strip())
        await self._bus.publish(
            build_envelope(
                event_type=EventType.MAIN_BRAIN_EVENT_FOLLOWUP_READY,
                source="main_brain",
                target="broadcast",
                session_id=request.session_id,
                turn_id=request.turn_id,
                task_id=request.task_id,
                correlation_id=request.task_id or request.correlation_id or followup.job_id,
                causation_id=request.event_id,
                payload=MainBrainFollowupReadyPayload(
                    job_id=followup.job_id,
                    source_event=followup.source_event,
                    source_decision=followup.decision,
                    reply_text=reply_text,
                    reply_kind=reply_kind,
                    delivery_target=followup.delivery_target,
                    origin_message=origin,
                    related_task_id=request.task_id,
                    metadata={"followup_source": followup.source_event, **dict(followup.metadata or {})},
                ),
            )
        )

    def _build_messages(self, *, session_id: str, user_text: str, history_context: str) -> list[SystemMessage | HumanMessage]:
        system_prompt = ""
        world_state = self._session_world_state(session_id)
        if self._context_builder is not None and hasattr(self._context_builder, "build_main_brain_system_prompt"):
            system_prompt = self._context_builder.build_main_brain_system_prompt(query=user_text, world_state=world_state)
        task_lines: list[str] = []
        if world_state is None:
            for task in self._tasks.for_session(session_id)[-5:]:
                task_lines.append(f"- {{'task_id': '{task.task_id}', 'title': '{task.title}', 'status': '{task.state.value}', 'summary': '{task.summary or task.last_progress}'}}")
        body = [
            "你只能输出两个文本区块：`####user####` 和 `####task####`。",
            "不要输出解释，不要输出额外内容。",
            "####task#### 固定使用：action=<none|create_task|cancel_task>、task_mode=<skip|sync|async>、task_id=<可选>、reason=<可选>。",
            "如果用户要求创建文件、修改文件、运行命令、检查环境、调用工具、生成产物，必须输出 `action=create_task`。",
        ]
        if history_context:
            body.extend(["", "最近对话摘要：", history_context])
        if task_lines:
            body.extend(["", "当前任务：", *task_lines])
        body.extend(["", "用户消息：", user_text])
        messages: list[SystemMessage | HumanMessage] = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content="\n".join(body)))
        return messages

    async def _invoke_model(self, messages: list[SystemMessage | HumanMessage]) -> Any:
        if hasattr(self._main_brain_llm, "ainvoke"):
            return await self._main_brain_llm.ainvoke(messages)
        if hasattr(self._main_brain_llm, "invoke"):
            return self._main_brain_llm.invoke(messages)
        raise RuntimeError("main_brain_llm does not support invoke/ainvoke")

    def _build_execution_request(
        self,
        *,
        request: BusEnvelope[MainBrainReplyRequestPayload],
        payload: TurnInputPayload,
        user_text: str,
        task_action: str,
        task_mode: str,
        task: dict[str, Any],
    ) -> dict[str, Any]:
        if task_action not in {"create_task", "cancel_task"}:
            return {}
        delivery_mode = "push" if task_mode == "async" else (str((payload.metadata or {}).get("current_delivery_mode", "") or "").strip() or "inline")
        if task_action == "create_task":
            return {
                "job_id": f"job_{uuid4().hex[:12]}",
                "job_action": "create_task",
                "job_kind": "execution_review",
                "source_text": user_text,
                "request_text": user_text,
                "delivery_target": {"delivery_mode": delivery_mode, "channel": payload.message.channel, "chat_id": payload.message.chat_id},
                "scores": {},
                "context": {
                    "history_context": str((payload.metadata or {}).get("history_context", "") or "").strip(),
                    "recent_turns": list((payload.metadata or {}).get("recent_turns", []) or []),
                    "short_term_memory": list((payload.metadata or {}).get("short_term_memory", []) or []),
                    "long_term_memory": list((payload.metadata or {}).get("long_term_memory", []) or []),
                    "tool_context": dict((payload.metadata or {}).get("tool_context", {}) or {}),
                    "content_blocks": list(payload.content_blocks) + list(payload.attachments),
                    "origin_message": payload.message.model_dump(exclude_none=True),
                    "task_mode": task_mode,
                },
            }
        return {
            "job_id": f"job_{uuid4().hex[:12]}",
            "job_action": "cancel_task",
            "job_kind": "execution_review",
            "task_id": str(task.get("task_id", "") or "").strip() or None,
            "source_text": user_text,
            "request_text": str(task.get("reason", "") or user_text or "user_cancelled").strip(),
            "delivery_target": {"delivery_mode": delivery_mode, "channel": payload.message.channel, "chat_id": payload.message.chat_id},
            "scores": {},
            "context": {"reason": str(task.get("reason", "") or user_text or "user_cancelled").strip(), "source_event_id": request.event_id},
        }

    def _followup_reply(self, *, task: ExecutionRecord | None, followup: FollowupContextPayload) -> tuple[str, str]:
        if followup.source_event == str(EventType.EXECUTION_EVENT_TASK_ACCEPTED):
            return self._reply_policy.execution_accepted(task, reason=str(followup.reason or "").strip() or None), "status"
        if followup.source_event == str(EventType.EXECUTION_EVENT_PROGRESS):
            return self._reply_policy.execution_progress(task, summary=str(followup.summary or "").strip() or "execution 正在继续处理。", next_step=str(followup.next_step or "").strip() or None), "status"
        if followup.source_event == str(EventType.EXECUTION_EVENT_TASK_REJECTED):
            return self._reply_policy.execution_rejected(task, reason=str(followup.reason or "").strip() or "当前无法处理。"), "status"
        outcome = str(followup.metadata.get("result", "") or "").strip() or None
        return self._reply_policy.execution_result(task, decision=followup.decision, summary=str(followup.summary or "").strip() or None, result_text=str(followup.result_text or "").strip() or None, outcome=outcome), ("answer" if followup.decision == "answer_only" else "status")

    async def _publish_reflection(self, event: BusEnvelope[object], *, reason: str, metadata: dict[str, Any]) -> None:
        await self._bus.publish(
            build_envelope(
                event_type=EventType.REFLECTION_LIGHT,
                source="main_brain",
                target="reflection_governor",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=event.task_id,
                correlation_id=event.correlation_id or event.task_id or event.turn_id,
                causation_id=event.event_id,
                payload=ReflectionSignalPayload(
                    trigger_id=f"reflection_{uuid4().hex[:12]}",
                    reason=reason,
                    source_event_id=event.event_id,
                    task_id=event.task_id,
                    metadata=metadata,
                ),
            )
        )

    def _latest_active_task_id(self, session_id: str) -> str:
        if self._session_runtime is not None:
            task_id = str(self._session_runtime.latest_active_task_id(session_id) or "").strip()
            if task_id:
                return task_id
        for task in reversed(self._tasks.for_session(session_id)):
            if task.state.value != "done":
                return task.task_id
        return ""

    def _latest_task_id(self, session_id: str) -> str:
        if self._session_runtime is not None:
            task_id = str(self._session_runtime.latest_task_id(session_id) or "").strip()
            if task_id:
                return task_id
        latest = self._tasks.latest_for_session(session_id, include_terminal=True)
        return latest.task_id if latest is not None else ""

    def _session_world_state(self, session_id: str) -> Any | None:
        if self._session_runtime is None or not hasattr(self._session_runtime, "snapshot"):
            return None
        return self._session_runtime.snapshot(session_id)

    @staticmethod
    def _user_text(payload: TurnInputPayload) -> str:
        if payload.user_text:
            return payload.user_text
        if payload.input_slots.user:
            return payload.input_slots.user
        return "\n".join(block.text for block in payload.content_blocks if block.type == "text" and block.text).strip()

    @staticmethod
    def _task_snapshot(task: ExecutionRecord | None) -> dict[str, Any]:
        if task is None:
            return {}
        return {
            "invoked": True,
            "task_id": task.task_id,
            "title": task.title,
            "state": task.state.value,
            "result": task.result,
            "summary": task.summary,
            "error": task.error,
            "stage": task.last_progress,
            "params": task.request.model_dump(exclude_none=True),
            "task_trace": [dict(item) for item in list(task.trace_log) if isinstance(item, dict)],
        }

    @staticmethod
    def _execution_from_packet(*, task_action: str, task_reason: str, task: ExecutionRecord | None) -> dict[str, Any]:
        if task_action == "create_task":
            return {"invoked": True, "status": "running", "summary": "主脑已触发 execution。", "failure_reason": ""}
        if task_action == "cancel_task":
            return {"invoked": bool(task is not None), "status": "failed", "summary": "主脑已请求取消 execution。", "failure_reason": task_reason or "user_cancelled"}
        return {"invoked": False, "status": "none", "summary": "主脑直接回复。", "failure_reason": ""}

    @staticmethod
    def _consume_background_result(task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            return


__all__ = ["MainBrainFrontLoop"]
