"""Process-local holder for SessionWorldState and execution followup wiring."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping
from uuid import uuid4

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.execution.store import ExecutionRecord, ExecutionStore
from emoticorebot.protocol.commands import ExecutionTaskRequestPayload, FollowupContextPayload, MainBrainReplyRequestPayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import (
    ExecutionAcceptedPayload,
    ExecutionProgressPayload,
    ExecutionRejectedPayload,
    ExecutionResultPayload,
    MainBrainReplyReadyPayload,
    OutputReadyPayloadBase,
    StreamChunkPayload,
    StreamCommitPayload,
    StreamInterruptedPayload,
    StreamStartPayload,
    TurnInputPayload,
)
from emoticorebot.protocol.topics import EventType

from .models import (
    PerceptionItemSummary,
    PerceptionSummary,
    ReplyStrategyState,
    SessionTaskState,
    SessionTraceRecord,
    SessionWorldState,
    StructuredProgressUpdate,
    TaskChunkState,
    TaskStatus,
    UserStateSnapshot,
)

_TRACE_LIMIT = 64
_OBSERVATION_LIMIT = 6
_ARTIFACT_LIMIT = 12


@dataclass(slots=True)
class _InputStreamState:
    message: object
    metadata: dict[str, Any]
    text: str = ""
    commit_count: int = 0


class SessionRuntime:
    """Maintains SessionWorldState and bridges input/main_brain/execution events."""

    def __init__(self, *, bus: PriorityPubSubBus, task_store: ExecutionStore) -> None:
        self._bus = bus
        self._task_store = task_store
        self._sessions: dict[str, SessionWorldState] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._input_streams: dict[tuple[str, str], _InputStreamState] = {}
        self._interrupted_streams: set[tuple[str, str]] = set()
        self._active_reply_streams: dict[str, str] = {}
        self._task_traces: dict[str, list[SessionTraceRecord]] = {}
        self._trace_cursors: dict[tuple[str, str], str] = {}

    def register(self) -> None:
        self._bus.subscribe(consumer="session", event_type=EventType.INPUT_TURN_RECEIVED, handler=self._on_user_input)
        self._bus.subscribe(consumer="session", event_type=EventType.INPUT_STREAM_STARTED, handler=self._on_input_stream_started)
        self._bus.subscribe(consumer="session", event_type=EventType.INPUT_STREAM_CHUNK, handler=self._on_input_stream_chunk)
        self._bus.subscribe(consumer="session", event_type=EventType.INPUT_STREAM_COMMITTED, handler=self._on_input_stream_committed)
        self._bus.subscribe(consumer="session", event_type=EventType.INPUT_STREAM_INTERRUPTED, handler=self._on_input_stream_interrupted)
        self._bus.subscribe(consumer="session", event_type=EventType.MAIN_BRAIN_EVENT_REPLY_READY, handler=self._on_main_brain_reply_ready)
        self._bus.subscribe(consumer="session", event_type=EventType.EXECUTION_EVENT_TASK_ACCEPTED, handler=self._on_execution_followup_event)
        self._bus.subscribe(consumer="session", event_type=EventType.EXECUTION_EVENT_PROGRESS, handler=self._on_execution_followup_event)
        self._bus.subscribe(consumer="session", event_type=EventType.EXECUTION_EVENT_TASK_REJECTED, handler=self._on_execution_followup_event)
        self._bus.subscribe(consumer="session", event_type=EventType.EXECUTION_EVENT_RESULT_READY, handler=self._on_execution_followup_event)
        self._bus.subscribe(consumer="session", event_type=EventType.OUTPUT_INLINE_READY, handler=self._on_reply_output)
        self._bus.subscribe(consumer="session", event_type=EventType.OUTPUT_PUSH_READY, handler=self._on_reply_output)
        self._bus.subscribe(consumer="session", event_type=EventType.OUTPUT_STREAM_OPEN, handler=self._on_reply_output)
        self._bus.subscribe(consumer="session", event_type=EventType.OUTPUT_STREAM_DELTA, handler=self._on_reply_output)
        self._bus.subscribe(consumer="session", event_type=EventType.OUTPUT_STREAM_CLOSE, handler=self._on_reply_output)

    def snapshot(self, session_id: str) -> SessionWorldState:
        return self._get_or_create(session_id).model_copy(deep=True)

    def task_views(self, session_id: str) -> list[SessionTaskState]:
        world = self._get_or_create(session_id)
        return [task.model_copy(deep=True) for task in self._ordered_tasks(world)]

    def task_view(self, session_id: str, task_id: str) -> SessionTaskState | None:
        world = self._get_or_create(session_id)
        task = world.tasks.get(str(task_id or "").strip())
        if task is not None:
            return task.model_copy(deep=True)
        record = self._task_store.get(str(task_id or "").strip())
        if record is None or record.session_id != str(session_id or "").strip():
            return None
        return self._state_from_record(record).model_copy(deep=True)

    def task_trace_summary(self, task_id: str, *, limit: int = 3) -> list[str]:
        messages = [item.message for item in self._task_traces.get(str(task_id or "").strip(), []) if item.message]
        return messages[-limit:] if limit > 0 else messages

    async def consume_task_traces(self, session_id: str, task_id: str) -> list[SessionTraceRecord]:
        session_key = str(session_id or "").strip()
        task_key = str(task_id or "").strip()
        if not session_key or not task_key:
            return []
        async with self._session_lock(session_key):
            trace = self._task_traces.get(task_key, [])
            if not trace:
                return []
            cursor_key = (session_key, task_key)
            cursor = str(self._trace_cursors.get(cursor_key, "") or "").strip()
            if not cursor:
                unread = trace
            else:
                unread = []
                found = False
                for item in trace:
                    if found:
                        unread.append(item)
                    elif item.trace_id == cursor:
                        found = True
                if not found:
                    unread = trace
            if unread:
                self._trace_cursors[cursor_key] = unread[-1].trace_id
            return [item.model_copy(deep=True) for item in unread]

    async def consume_task_trace_summary(self, session_id: str, task_id: str, *, limit: int = 3) -> list[str]:
        unread = await self.consume_task_traces(session_id, task_id)
        messages = [item.message for item in unread if item.message]
        return messages[-limit:] if limit > 0 else messages

    def latest_active_task_id(self, session_id: str) -> str:
        world = self._get_or_create(session_id)
        if world.foreground_task_id:
            return world.foreground_task_id
        if world.background_task_ids:
            return world.background_task_ids[0]
        latest = self._task_store.latest_for_session(session_id, include_terminal=False)
        return latest.task_id if latest is not None else ""

    def latest_task_id(self, session_id: str) -> str:
        latest = self._task_store.latest_for_session(session_id, include_terminal=True)
        if latest is not None:
            return latest.task_id
        world = self._get_or_create(session_id)
        ordered = self._ordered_tasks(world)
        return ordered[0].task_id if ordered else ""

    def clear_session(self, session_id: str) -> None:
        session_key = str(session_id or "").strip()
        world = self._sessions.pop(session_key, None)
        self._session_locks.pop(session_key, None)
        self._active_reply_streams.pop(session_key, None)
        for key in [key for key in self._input_streams if key[0] == session_key]:
            self._input_streams.pop(key, None)
            self._interrupted_streams.discard(key)
        for key in [key for key in self._trace_cursors if key[0] == session_key]:
            self._trace_cursors.pop(key, None)
        if world is not None:
            for task_id in world.tasks:
                self._task_traces.pop(task_id, None)

    def should_archive(self, session_id: str) -> bool:
        session_key = str(session_id or "").strip()
        if not session_key:
            return True
        if any(key[0] == session_key for key in self._input_streams):
            return False
        if session_key in self._active_reply_streams:
            return False
        world = self._get_or_create(session_key)
        return not any(self._is_active(task.status) for task in world.tasks.values())

    def has_active_reply_stream(self, session_id: str) -> bool:
        return str(session_id or "").strip() in self._active_reply_streams

    async def _on_user_input(self, event: BusEnvelope[TurnInputPayload]) -> None:
        session_id = str(event.session_id or "").strip()
        if not session_id:
            return
        async with self._session_lock(session_id):
            world = self._get_or_create(session_id)
            if bool(event.payload.barge_in):
                self._active_reply_streams.pop(session_id, None)
            user_text = self._user_text(event.payload)
            world.user_state = self._infer_user_state(user_text)
            world.active_topics = [user_text] if user_text else []
            world.perception_summary = self._perception_summary(event.payload)
            world.reply_strategy = ReplyStrategyState(
                goal="先理解当前输入，再决定是否触发 execution。",
                style="direct",
                delivery_mode=self._delivery_mode(event.payload.metadata),
                needs_tool=False,
            )
            self._refresh_world_state(world)
        await self._bus.publish(
            build_envelope(
                event_type=EventType.MAIN_BRAIN_COMMAND_REPLY_REQUESTED,
                source="session",
                target="main_brain",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=event.task_id,
                correlation_id=event.correlation_id or event.turn_id,
                causation_id=event.event_id,
                payload=MainBrainReplyRequestPayload(
                    request_id=f"main_brain_req_{uuid4().hex[:12]}",
                    turn_input=event.payload,
                    metadata={},
                ),
            )
        )

    async def _on_input_stream_started(self, event: BusEnvelope[StreamStartPayload]) -> None:
        session_id = str(event.session_id or "").strip()
        stream_id = str(event.payload.stream_id or "").strip()
        if not session_id or not stream_id:
            return
        async with self._session_lock(session_id):
            self._input_streams[(session_id, stream_id)] = _InputStreamState(
                message=event.payload.message.model_copy(deep=True),
                metadata=dict(event.payload.metadata or {}),
            )
            if bool((event.payload.metadata or {}).get("barge_in", False)):
                self._active_reply_streams.pop(session_id, None)

    async def _on_input_stream_chunk(self, event: BusEnvelope[StreamChunkPayload]) -> None:
        key = (str(event.session_id or "").strip(), str(event.payload.stream_id or "").strip())
        if not key[0] or not key[1] or key in self._interrupted_streams:
            return
        async with self._session_lock(key[0]):
            stream = self._input_streams.get(key)
            if stream is not None:
                stream.text += str(event.payload.chunk_text or "")

    async def _on_input_stream_committed(self, event: BusEnvelope[StreamCommitPayload]) -> None:
        session_id = str(event.session_id or "").strip()
        stream_id = str(event.payload.stream_id or "").strip()
        key = (session_id, stream_id)
        if not session_id or not stream_id or key in self._interrupted_streams:
            return
        async with self._session_lock(session_id):
            stream = self._input_streams.get(key)
            if stream is None:
                return
            committed_text = str(event.payload.committed_text or "").strip() or str(stream.text or "").strip()
            if not committed_text:
                return
            stream.commit_count += 1
            stream.text = ""
            metadata = dict(stream.metadata)
            metadata.update(dict(event.payload.metadata or {}))
            channel_kind = self._stream_channel_kind(metadata)
            session_mode = self._stream_session_mode(metadata, channel_kind=channel_kind)
            input_kind = self._stream_input_kind(metadata)
            turn_id = str(event.turn_id or "").strip() or f"turn_stream_{stream_id}_{stream.commit_count}"
            await self._bus.publish(
                build_envelope(
                    event_type=EventType.INPUT_TURN_RECEIVED,
                    source="session",
                    target="broadcast",
                    session_id=event.session_id,
                    turn_id=turn_id,
                    correlation_id=turn_id,
                    causation_id=event.event_id,
                    payload=TurnInputPayload(
                        input_id=turn_id,
                        input_mode="turn",
                        session_mode=session_mode,
                        channel_kind=channel_kind,
                        input_kind=input_kind,
                        barge_in=bool(metadata.get("barge_in", False)),
                        message=stream.message.model_copy(deep=True),
                        user_text=committed_text,
                        metadata={
                            **metadata,
                            "source_input_mode": "stream",
                            "source_stream_id": stream_id,
                            "stream_commit": True,
                            "stream_commit_index": stream.commit_count,
                        },
                    ),
                )
            )

    async def _on_input_stream_interrupted(self, event: BusEnvelope[StreamInterruptedPayload]) -> None:
        session_id = str(event.session_id or "").strip()
        stream_id = str(event.payload.stream_id or "").strip()
        key = (session_id, stream_id)
        if not session_id or not stream_id:
            return
        async with self._session_lock(session_id):
            self._interrupted_streams.add(key)
            self._input_streams.pop(key, None)

    async def _on_main_brain_reply_ready(self, event: BusEnvelope[MainBrainReplyReadyPayload]) -> None:
        session_id = str(event.session_id or "").strip()
        if not session_id:
            return
        async with self._session_lock(session_id):
            world = self._get_or_create(session_id)
            world.reply_strategy = ReplyStrategyState(
                goal=str(event.payload.reply_text or "").strip(),
                style=str(event.payload.reply_kind or "answer").strip() or "answer",
                delivery_mode=event.payload.delivery_target.delivery_mode,
                needs_tool=bool(event.payload.invoke_execution),
            )
            self._refresh_world_state(world)

        if not event.payload.invoke_execution:
            return
        request = dict(event.payload.execution_request or {})
        if not request:
            raise RuntimeError("main brain reply requested execution without execution_request")
        if not str(request.get("task_id", "") or "").strip() and event.payload.related_task_id:
            request["task_id"] = event.payload.related_task_id
        await self._bus.publish(
            build_envelope(
                event_type=EventType.EXECUTION_COMMAND_TASK_REQUESTED,
                source="session",
                target="execution_runtime",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=str(request.get("task_id", "") or "").strip() or None,
                correlation_id=str(request.get("task_id", "") or "").strip() or event.correlation_id or event.turn_id,
                causation_id=event.event_id,
                payload=ExecutionTaskRequestPayload.model_validate(request),
            )
        )

    async def _on_execution_followup_event(
        self,
        event: BusEnvelope[
            ExecutionAcceptedPayload | ExecutionProgressPayload | ExecutionRejectedPayload | ExecutionResultPayload
        ],
    ) -> None:
        session_id = str(event.session_id or "").strip()
        task_id = str(event.task_id or "").strip()
        if not session_id or not task_id:
            return
        async with self._session_lock(session_id):
            world = self._get_or_create(session_id)
            task = self._apply_execution_event(world, event)
            self._refresh_world_state(world)
            followup = self._followup_context(event, task=task)
        if followup is None:
            return
        await self._bus.publish(
            build_envelope(
                event_type=EventType.MAIN_BRAIN_COMMAND_REPLY_REQUESTED,
                source="session",
                target="main_brain",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=event.task_id,
                correlation_id=event.task_id or event.correlation_id or event.turn_id,
                causation_id=event.event_id,
                payload=MainBrainReplyRequestPayload(
                    request_id=f"main_brain_req_{uuid4().hex[:12]}",
                    followup_context=followup,
                    metadata={"followup": True},
                ),
            )
        )

    async def _on_reply_output(self, event: BusEnvelope[OutputReadyPayloadBase]) -> None:
        session_id = str(event.session_id or "").strip()
        if not session_id:
            return
        stream_state = str(getattr(event.payload, "stream_state", "") or "").strip()
        stream_id = str(getattr(event.payload, "stream_id", "") or event.payload.content.reply_id or "").strip()
        async with self._session_lock(session_id):
            if stream_state in {"open", "delta"} and stream_id:
                self._active_reply_streams[session_id] = stream_id
            elif stream_state in {"close", "superseded", "final"}:
                active = self._active_reply_streams.get(session_id)
                if active in {"", None, stream_id}:
                    self._active_reply_streams.pop(session_id, None)

    def _apply_execution_event(
        self,
        world: SessionWorldState,
        event: BusEnvelope[
            ExecutionAcceptedPayload | ExecutionProgressPayload | ExecutionRejectedPayload | ExecutionResultPayload
        ],
    ) -> SessionTaskState:
        task_id = str(event.task_id or "").strip()
        record = self._task_store.get(task_id)
        task = world.tasks.get(task_id)
        if task is None:
            task = self._state_from_record(record, task_id=task_id)
            world.tasks[task_id] = task
        elif record is not None:
            task = self._merge_record(task, record)
            world.tasks[task_id] = task

        progress = self._progress_update(event, task=task)
        task.status = progress.status
        task.current_chunk = (
            TaskChunkState(
                chunk_id=f"{task_id}:{progress.stage or 'state'}",
                title=progress.stage or progress.summary,
                status=self._chunk_status(progress.status),
            )
            if progress.stage or progress.summary
            else None
        )
        if progress.summary and progress.summary not in task.recent_observations:
            task.recent_observations = [*task.recent_observations[-(_OBSERVATION_LIMIT - 1) :], progress.summary]
        if progress.artifacts:
            task.artifacts = [*task.artifacts, *progress.artifacts][-_ARTIFACT_LIMIT:]
        if progress.summary and task.visibility != "silent":
            task.last_user_visible_update = progress.summary
        task.waiting_for_user = progress.needs_user_input
        task.risk_flags = self._risk_flags_for(task=task, progress=progress)
        self._append_trace(task_id=task_id, event=event, progress=progress)
        return task

    def _progress_update(
        self,
        event: BusEnvelope[
            ExecutionAcceptedPayload | ExecutionProgressPayload | ExecutionRejectedPayload | ExecutionResultPayload
        ],
        *,
        task: SessionTaskState,
    ) -> StructuredProgressUpdate:
        payload = event.payload
        nested = payload.metadata.get("payload") if isinstance(payload.metadata, Mapping) else None
        blockers = self._string_list(nested.get("blockers") if isinstance(nested, Mapping) else [])
        needs_user_input = bool((nested.get("needs_user_input") if isinstance(nested, Mapping) else False) or blockers)
        summary = ""
        stage = str(getattr(payload, "stage", "") or "").strip()
        artifacts: list[dict[str, Any]] = []
        status: TaskStatus = "running"

        if event.event_type == EventType.EXECUTION_EVENT_TASK_ACCEPTED:
            summary = str(payload.reason or "execution accepted").strip()
        elif event.event_type == EventType.EXECUTION_EVENT_PROGRESS:
            summary = str(payload.summary or "").strip()
            artifacts = self._artifact_dicts([])
            if needs_user_input:
                status = "waiting_user"
        elif event.event_type == EventType.EXECUTION_EVENT_TASK_REJECTED:
            summary = str(payload.reason or "execution rejected").strip()
            status = "failed"
        else:
            summary = str(payload.summary or payload.result_text or "").strip()
            artifacts = self._artifact_dicts(payload.artifacts)
            result = str(payload.metadata.get("result", "") or "").strip()
            if result == "failed":
                status = "failed"
            elif result == "cancelled":
                status = "cancelled"
            else:
                status = "done"

        return StructuredProgressUpdate(
            task_id=task.task_id,
            stage=stage,
            status=status,
            summary=summary,
            observations=[summary] if summary else [],
            artifacts=artifacts,
            blockers=blockers,
            needs_user_input=needs_user_input,
            metadata=dict(payload.metadata or {}),
        )

    def _followup_context(
        self,
        event: BusEnvelope[
            ExecutionAcceptedPayload | ExecutionProgressPayload | ExecutionRejectedPayload | ExecutionResultPayload
        ],
        *,
        task: SessionTaskState,
    ) -> FollowupContextPayload | None:
        payload = event.payload
        metadata = dict(payload.metadata or {})
        if self._suppress_followup(event, task=task):
            metadata["suppress_output"] = True
        if event.event_type == EventType.EXECUTION_EVENT_TASK_ACCEPTED:
            return FollowupContextPayload(
                source_event=str(event.event_type),
                job_id=payload.job_id,
                decision=payload.decision,
                stage=str(payload.stage or "").strip() or None,
                reason=str(payload.reason or "").strip() or None,
                delivery_target=payload.delivery_target,
                metadata=metadata,
            )
        if event.event_type == EventType.EXECUTION_EVENT_PROGRESS:
            return FollowupContextPayload(
                source_event=str(event.event_type),
                job_id=payload.job_id,
                decision=payload.decision,
                stage=str(payload.stage or "").strip() or None,
                summary=str(payload.summary or "").strip() or None,
                progress=payload.progress,
                next_step=str(payload.next_step or "").strip() or None,
                delivery_target=payload.delivery_target,
                metadata=metadata,
            )
        if event.event_type == EventType.EXECUTION_EVENT_TASK_REJECTED:
            return FollowupContextPayload(
                source_event=str(event.event_type),
                job_id=payload.job_id,
                decision=payload.decision,
                reason=str(payload.reason or "").strip() or None,
                delivery_target=payload.delivery_target,
                metadata=metadata,
            )
        return FollowupContextPayload(
            source_event=str(event.event_type),
            job_id=payload.job_id,
            decision=payload.decision,
            summary=str(payload.summary or "").strip() or None,
            result_text=str(payload.result_text or "").strip() or None,
            delivery_target=payload.delivery_target,
            metadata=metadata,
        )

    def _suppress_followup(
        self,
        event: BusEnvelope[
            ExecutionAcceptedPayload | ExecutionProgressPayload | ExecutionRejectedPayload | ExecutionResultPayload
        ],
        *,
        task: SessionTaskState,
    ) -> bool:
        if event.event_type in {EventType.EXECUTION_EVENT_TASK_REJECTED, EventType.EXECUTION_EVENT_RESULT_READY}:
            return False
        if task.visibility == "silent":
            return True
        if task.interruptibility == "never":
            return True
        if event.event_type == EventType.EXECUTION_EVENT_PROGRESS and task.visibility == "concise":
            return True
        return False

    def _append_trace(
        self,
        *,
        task_id: str,
        event: BusEnvelope[
            ExecutionAcceptedPayload | ExecutionProgressPayload | ExecutionRejectedPayload | ExecutionResultPayload
        ],
        progress: StructuredProgressUpdate,
    ) -> None:
        if not progress.summary:
            return
        trace = self._task_traces.setdefault(task_id, [])
        trace.append(
            SessionTraceRecord(
                trace_id=str(event.event_id or f"{task_id}:{len(trace) + 1}"),
                task_id=task_id,
                kind=self._trace_kind(event, progress=progress),
                message=progress.summary,
                ts=self._record_updated_at(task_id),
                data={"source_event": str(event.event_type), **dict(progress.metadata)},
            )
        )
        if len(trace) > _TRACE_LIMIT:
            self._task_traces[task_id] = trace[-_TRACE_LIMIT:]

    def _refresh_world_state(self, world: SessionWorldState) -> None:
        active = [task for task in self._ordered_tasks(world) if self._is_active(task.status)]
        waiting = [task for task in active if task.waiting_for_user or task.status == "waiting_user"]
        world.foreground_task_id = active[0].task_id if active else None
        world.background_task_ids = [task.task_id for task in active[1:]]
        world.risk_flags = []
        for task in world.tasks.values():
            for flag in task.risk_flags:
                if flag not in world.risk_flags:
                    world.risk_flags.append(flag)
        if waiting:
            world.conversation_phase = "waiting_user"
        elif len(active) > 1:
            world.conversation_phase = "multitask_chat"
        elif active:
            world.conversation_phase = "task_focus"
        elif world.active_topics:
            world.conversation_phase = "chat"
        else:
            world.conversation_phase = "idle"

    def _state_from_record(self, record: ExecutionRecord | None, *, task_id: str | None = None) -> SessionTaskState:
        if record is None:
            if not task_id:
                raise RuntimeError("task_id is required when execution record is missing")
            return SessionTaskState(task_id=task_id, title=task_id, kind="execution")
        return SessionTaskState(
            task_id=record.task_id,
            title=str(record.title or record.request.title or record.request.request[:48]).strip() or record.task_id,
            kind=self._task_kind(record),
            parent_task_id=str(record.raw_context.get("parent_task_id", "") or "").strip() or None,
            status=self._status_from_record(record),
            priority=self._priority(record.raw_context),
            visibility=self._visibility(record.raw_context),
            interruptibility=self._interruptibility(record.raw_context),
            user_visible=not bool(record.suppress_delivery),
            goal=str(record.request.goal or record.request.request or "").strip(),
            current_chunk=(
                TaskChunkState(
                    chunk_id=f"{record.task_id}:{record.last_progress}",
                    title=record.last_progress,
                    status="running",
                )
                if record.last_progress and record.state.value != "done"
                else None
            ),
            recent_observations=[str(record.summary or record.last_progress or "").strip()] if str(record.summary or record.last_progress or "").strip() else [],
            artifacts=[],
            last_user_visible_update=str(record.summary or "").strip(),
            waiting_for_user=False,
            risk_flags=self._risk_flags_from_record(record),
        )

    def _merge_record(self, task: SessionTaskState, record: ExecutionRecord) -> SessionTaskState:
        task.title = str(record.title or task.title or record.task_id).strip() or record.task_id
        task.kind = self._task_kind(record)
        task.parent_task_id = str(record.raw_context.get("parent_task_id", "") or "").strip() or None
        task.status = self._status_from_record(record)
        task.priority = self._priority(record.raw_context)
        task.visibility = self._visibility(record.raw_context)
        task.interruptibility = self._interruptibility(record.raw_context)
        task.user_visible = not bool(record.suppress_delivery)
        task.goal = str(record.request.goal or record.request.request or task.goal).strip()
        return task

    @staticmethod
    def _ordered_tasks(world: SessionWorldState) -> list[SessionTaskState]:
        return sorted(
            world.tasks.values(),
            key=lambda item: (
                0 if item.status not in {"done", "failed", "cancelled"} else 1,
                -int(item.priority),
                item.task_id,
            ),
        )

    def _get_or_create(self, session_id: str) -> SessionWorldState:
        session_key = str(session_id or "").strip()
        world = self._sessions.get(session_key)
        if world is None:
            world = SessionWorldState(session_id=session_key)
            self._sessions[session_key] = world
        return world

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        session_key = str(session_id or "").strip()
        lock = self._session_locks.get(session_key)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_key] = lock
        return lock

    @staticmethod
    def _user_text(payload: TurnInputPayload) -> str:
        if payload.user_text:
            return str(payload.user_text).strip()
        if payload.input_slots.user:
            return str(payload.input_slots.user).strip()
        parts = [str(block.text or "").strip() for block in payload.content_blocks if block.type == "text" and block.text]
        return "\n".join(part for part in parts if part).strip()

    @staticmethod
    def _infer_user_state(user_text: str) -> UserStateSnapshot:
        text = str(user_text or "").strip()
        if not text:
            return UserStateSnapshot()
        if any(token in text for token in ("累", "困", "疲惫", "疲倦")):
            return UserStateSnapshot(emotion="tired", energy="low", confidence=0.82)
        if any(token in text for token in ("烦", "生气", "annoyed")):
            return UserStateSnapshot(emotion="annoyed", energy="medium", confidence=0.78)
        if any(token in text for token in ("焦虑", "担心", "害怕", "anxious")):
            return UserStateSnapshot(emotion="anxious", energy="low", confidence=0.8)
        if any(token in text for token in ("开心", "高兴", "happy")):
            return UserStateSnapshot(emotion="happy", energy="high", confidence=0.76)
        if any(token in text for token in ("兴奋", "激动", "excited")):
            return UserStateSnapshot(emotion="excited", energy="high", confidence=0.76)
        return UserStateSnapshot(emotion="neutral", energy="medium", confidence=0.55)

    @classmethod
    def _perception_summary(cls, payload: TurnInputPayload) -> PerceptionSummary:
        summary = PerceptionSummary()
        if payload.input_kind == "voice" and payload.user_text:
            summary.audio.append(
                PerceptionItemSummary(
                    name="voice_input",
                    kind="transcript",
                    status="ready",
                    summary=str(payload.user_text or "").strip()[:160],
                )
            )
        if payload.channel_kind == "video":
            summary.video.append(
                PerceptionItemSummary(
                    name="video_turn",
                    kind="video",
                    status="received",
                    summary=str(payload.user_text or "").strip()[:160],
                )
            )
        for block in [*list(payload.content_blocks), *list(payload.attachments)]:
            item = cls._perception_item(block)
            if item is None:
                continue
            if item.kind == "image":
                summary.images.append(item)
            elif item.kind == "audio":
                summary.audio.append(item)
            elif item.kind == "video":
                summary.video.append(item)
            else:
                summary.files.append(item)
        return summary

    @staticmethod
    def _perception_item(block: object) -> PerceptionItemSummary | None:
        mime_type = str(getattr(block, "mime_type", "") or "").strip().lower()
        path = str(getattr(block, "path", "") or "").strip()
        name = str(getattr(block, "name", "") or "").strip() or (path.rsplit("/", 1)[-1] if path else "")
        if getattr(block, "type", "") == "text":
            return None
        kind = "file"
        if mime_type.startswith("image/") or any(path.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp")):
            kind = "image"
        elif mime_type.startswith("audio/") or any(path.lower().endswith(ext) for ext in (".mp3", ".wav", ".m4a", ".ogg")):
            kind = "audio"
        elif mime_type.startswith("video/") or any(path.lower().endswith(ext) for ext in (".mp4", ".mov", ".avi", ".mkv")):
            kind = "video"
        return PerceptionItemSummary(
            name=name or "attachment",
            kind=kind,
            status="received",
            summary=name or path or str(getattr(block, "type", "file") or "file"),
        )

    @staticmethod
    def _delivery_mode(metadata: Mapping[str, Any] | None) -> str:
        value = str((metadata or {}).get("current_delivery_mode", "") or "").strip()
        return value if value in {"inline", "push", "stream"} else "inline"

    @staticmethod
    def _stream_channel_kind(metadata: Mapping[str, Any]) -> str:
        value = str(metadata.get("channel_kind", "") or "").strip()
        return value if value in {"chat", "voice", "video"} else "voice"

    @staticmethod
    def _stream_input_kind(metadata: Mapping[str, Any]) -> str:
        value = str(metadata.get("input_kind", "") or "").strip()
        return value if value in {"text", "voice", "multimodal"} else "voice"

    @staticmethod
    def _stream_session_mode(metadata: Mapping[str, Any], *, channel_kind: str) -> str:
        value = str(metadata.get("session_mode", "") or "").strip()
        if value in {"turn_chat", "realtime_chat"}:
            return value
        return "turn_chat" if channel_kind == "chat" else "realtime_chat"

    @staticmethod
    def _task_kind(record: ExecutionRecord) -> str:
        raw = str(record.raw_context.get("task_kind", "") or record.job_kind or "").strip().lower()
        if raw in {"chat", "diagnosis", "reminder", "search", "analysis", "execution", "followup", "other"}:
            return raw
        if "reminder" in raw:
            return "reminder"
        if any(token in raw for token in ("search", "query")):
            return "search"
        if any(token in raw for token in ("analysis", "review")):
            return "analysis"
        return "execution"

    @staticmethod
    def _priority(context: Mapping[str, Any]) -> int:
        try:
            value = int(context.get("priority", 50))
        except (TypeError, ValueError):
            value = 50
        return max(0, min(100, value))

    @staticmethod
    def _visibility(context: Mapping[str, Any]) -> str:
        value = str(context.get("visibility", "") or "").strip()
        return value if value in {"silent", "concise", "verbose"} else "concise"

    @staticmethod
    def _interruptibility(context: Mapping[str, Any]) -> str:
        value = str(context.get("interruptibility", "") or "").strip()
        return value if value in {"never", "important_only", "always"} else "important_only"

    @staticmethod
    def _status_from_record(record: ExecutionRecord) -> TaskStatus:
        if record.state.value != "done":
            return "running"
        if record.result == "failed":
            return "failed"
        if record.result == "cancelled":
            return "cancelled"
        return "done"

    @staticmethod
    def _chunk_status(status: TaskStatus) -> str:
        if status == "done":
            return "done"
        if status == "failed":
            return "failed"
        if status == "waiting_user":
            return "blocked"
        if status == "cancelled":
            return "failed"
        return "running"

    @staticmethod
    def _is_active(status: str) -> bool:
        return str(status or "").strip() not in {"done", "failed", "cancelled"}

    @staticmethod
    def _string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        items: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in items:
                items.append(text)
        return items

    @staticmethod
    def _artifact_dicts(value: object) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        artifacts: list[dict[str, Any]] = []
        for item in value:
            if hasattr(item, "model_dump"):
                artifacts.append(item.model_dump(exclude_none=True))
            elif isinstance(item, Mapping):
                artifacts.append(dict(item))
        return artifacts

    @staticmethod
    def _risk_flags_from_record(record: ExecutionRecord) -> list[str]:
        flags: list[str] = []
        if record.result == "failed":
            flags.append("execution_failed")
        elif record.result == "cancelled":
            flags.append("execution_cancelled")
        if bool(record.raw_context.get("requires_confirmation")):
            flags.append("requires_confirmation")
        return flags

    @staticmethod
    def _risk_flags_for(task: SessionTaskState, progress: StructuredProgressUpdate) -> list[str]:
        flags = [flag for flag in task.risk_flags if flag not in {"execution_failed", "execution_cancelled", "waiting_user"}]
        if progress.status == "failed":
            flags.append("execution_failed")
        if progress.status == "cancelled":
            flags.append("execution_cancelled")
        if progress.needs_user_input or progress.status == "waiting_user":
            flags.append("waiting_user")
        return flags

    @staticmethod
    def _trace_kind(
        event: BusEnvelope[
            ExecutionAcceptedPayload | ExecutionProgressPayload | ExecutionRejectedPayload | ExecutionResultPayload
        ],
        *,
        progress: StructuredProgressUpdate,
    ) -> str:
        if event.event_type == EventType.EXECUTION_EVENT_TASK_ACCEPTED:
            return "status"
        if event.event_type == EventType.EXECUTION_EVENT_TASK_REJECTED:
            return "warning"
        if event.event_type == EventType.EXECUTION_EVENT_RESULT_READY:
            return "summary" if progress.status == "done" else ("warning" if progress.status == "cancelled" else "error")
        if str(progress.metadata.get("event", "") or "").strip() == "task.tool":
            return "tool"
        return "progress"

    def _record_updated_at(self, task_id: str) -> str:
        record = self._task_store.get(task_id)
        return str(record.updated_at or "").strip() if record is not None else ""


__all__ = ["SessionRuntime"]
