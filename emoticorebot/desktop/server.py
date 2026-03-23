"""Thin websocket bridge for desktop shells."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Protocol

from websockets.asyncio.server import ServerConnection, serve

from emoticorebot.desktop.adapter import DesktopStateAdapter
from emoticorebot.runtime.scheduler import RuntimeScheduler


class MessageSink(Protocol):
    async def send(self, message: str) -> None:
        """Send one JSON message to a connected shell."""


def load_affect_state_snapshot(workspace: Path) -> dict[str, Any] | None:
    """Read the latest affect snapshot for idle desktop rendering."""

    path = workspace / "memony" / "affect_state.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(raw, dict):
        return raw
    return None


class DesktopBridgeServer:
    """Bridge desktop shell input/output without touching kernel internals."""

    def __init__(
        self,
        *,
        runtime: RuntimeScheduler,
        workspace: Path,
        default_thread_id: str = "desktop:main",
    ) -> None:
        self.runtime = runtime
        self.workspace = workspace
        self.default_thread_id = default_thread_id
        self.adapter = DesktopStateAdapter()
        self._connections: set[MessageSink] = set()
        self._packet_task: asyncio.Task[None] | None = None
        self._turn_tasks: set[asyncio.Task[None]] = set()
        self._thread_locks: dict[str, asyncio.Lock] = {}
        self._stop_event = asyncio.Event()

    @property
    def affect_state_path(self) -> Path:
        return self.workspace / "memony" / "affect_state.json"

    async def start(self) -> None:
        if self._packet_task is not None and not self._packet_task.done():
            return
        self._stop_event.clear()
        self._packet_task = asyncio.create_task(self._pump_packets())

    async def stop(self) -> None:
        self._stop_event.set()

        packet_task = self._packet_task
        if packet_task is not None:
            packet_task.cancel()
            try:
                await packet_task
            except asyncio.CancelledError:
                pass
            self._packet_task = None

        turn_tasks = list(self._turn_tasks)
        for task in turn_tasks:
            task.cancel()
        for task in turn_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._turn_tasks.clear()
        self._connections.clear()

    async def serve(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        await self.start()
        async with serve(self._handle_connection, host, port):
            await self._stop_event.wait()

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        self._connections.add(websocket)
        try:
            await self._safe_send(
                websocket,
                {
                    "type": "ready",
                    "payload": {
                        "default_thread_id": self.default_thread_id,
                        "affect_state_path": str(self.affect_state_path),
                    },
                },
            )
            latest = self.adapter.get_thread_packet(self.default_thread_id)
            if latest is not None:
                await self._safe_send(websocket, {"type": "surface_state", "payload": latest})
            snapshot = load_affect_state_snapshot(self.workspace)
            if snapshot is not None:
                await self._safe_send(websocket, {"type": "affect_state", "payload": snapshot})

            async for raw in websocket:
                await self._dispatch_client_message(websocket, raw)
        finally:
            self._connections.discard(websocket)

    async def _dispatch_client_message(self, websocket: MessageSink, raw: str) -> None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            await self._safe_send(
                websocket,
                {"type": "error", "payload": {"message": "invalid json payload"}},
            )
            return

        if not isinstance(message, dict):
            await self._safe_send(
                websocket,
                {"type": "error", "payload": {"message": "desktop event must be an object"}},
            )
            return

        event_type = str(message.get("type", "") or "").strip()
        payload = message.get("payload", message)
        if not isinstance(payload, dict):
            payload = {}

        if event_type != "user_input":
            await self._safe_send(
                websocket,
                {"type": "error", "payload": {"message": f"unsupported event: {event_type or 'unknown'}"}},
            )
            return

        task = asyncio.create_task(self._run_turn(websocket, payload))
        self._turn_tasks.add(task)
        task.add_done_callback(self._turn_tasks.discard)

    async def _run_turn(self, websocket: MessageSink, payload: dict[str, Any]) -> None:
        user_text = str(payload.get("text", "") or "").strip()
        if not user_text:
            await self._safe_send(
                websocket,
                {"type": "error", "payload": {"message": "text is required"}},
            )
            return

        thread_id = str(payload.get("thread_id", "") or "").strip() or self.default_thread_id
        user_id = str(payload.get("user_id", "") or "").strip() or "desktop-user"
        lock = self._thread_locks.setdefault(thread_id, asyncio.Lock())

        async with lock:
            chunks: list[str] = []

            async def stream_handler(chunk: str) -> None:
                chunks.append(chunk)
                await self._safe_send(
                    websocket,
                    {
                        "type": "reply_chunk",
                        "payload": {"thread_id": thread_id, "chunk": chunk},
                    },
                )

            try:
                reply = await self.runtime.handle_user_text(
                    thread_id=thread_id,
                    session_id=thread_id,
                    user_id=user_id,
                    user_text=user_text,
                    stream_handler=stream_handler,
                    surface_state_handler=self.adapter.handle_surface_state,
                )
            except Exception as exc:
                await self._safe_send(
                    websocket,
                    {
                        "type": "turn_error",
                        "payload": {"thread_id": thread_id, "error": str(exc)},
                    },
                )
                return

            final_text = reply if reply.strip() else "".join(chunks).strip()
            await self._safe_send(
                websocket,
                {
                    "type": "reply_done",
                    "payload": {"thread_id": thread_id, "text": final_text},
                },
            )
            snapshot = load_affect_state_snapshot(self.workspace)
            if snapshot is not None:
                await self._safe_send(websocket, {"type": "affect_state", "payload": snapshot})

    async def _pump_packets(self) -> None:
        try:
            while True:
                packet = await self.adapter.next_packet()
                await self._broadcast({"type": "surface_state", "payload": packet})
        except asyncio.CancelledError:
            raise

    async def _broadcast(self, event: dict[str, Any]) -> None:
        for websocket in list(self._connections):
            await self._safe_send(websocket, event)

    async def _safe_send(self, websocket: MessageSink, event: dict[str, Any]) -> None:
        try:
            await websocket.send(json.dumps(event, ensure_ascii=False))
        except Exception:
            self._connections.discard(websocket)
