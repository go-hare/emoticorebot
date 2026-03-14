from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "emoticorebot"


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def test_legacy_runtime_modules_are_removed() -> None:
    assert not (PACKAGE_ROOT / "agent" / "brain.py").exists()
    assert not (PACKAGE_ROOT / "agent" / "system.py").exists()
    assert not (PACKAGE_ROOT / "agent" / "central" / "central.py").exists()
    assert not any((PACKAGE_ROOT / "agent" / "central").glob("*.py"))
    assert not (PACKAGE_ROOT / "session" / "manager.py").exists()
    assert not (PACKAGE_ROOT / "runtime" / "runtime.py").exists()


def test_runtime_host_depends_on_thread_store_not_session_manager() -> None:
    source = _read("emoticorebot/bootstrap.py")
    assert "from emoticorebot.session.thread_store import ThreadStore" in source
    assert "thread_store: ThreadStore | None = None" in source
    assert "SessionManager" not in source
    assert "session_manager" not in source


def test_command_shortcuts_preserve_message_correlation() -> None:
    source = _read("emoticorebot/bootstrap.py")

    assert 'msg.metadata["message_id"] = message_id' in source
    assert 'content="New session started."' in source
    assert "reply_to=message_id" in source
    assert "metadata=msg.metadata or {}" in source


def test_runtime_host_uses_split_turn_and_state_locks() -> None:
    bootstrap = _read("emoticorebot/bootstrap.py")
    event_loop = _read("emoticorebot/runtime/event_loop.py")

    assert "self._turn_locks: dict[str, asyncio.Lock] = {}" in bootstrap
    assert "self._state_locks: dict[str, asyncio.Lock] = {}" in bootstrap
    assert "async with self._turn_lock_for(key):" in bootstrap
    assert "async with self._state_lock_for(key):" in bootstrap
    assert "state_lock_for=self._state_lock_for" in bootstrap
    assert "session_lock_for" not in event_loop
    assert "state_lock_for: Callable[[str], asyncio.Lock]" in event_loop
    assert "self._state_lock_for = state_lock_for" in event_loop


def test_reflection_pipeline_uses_normalized_reflection_input() -> None:
    coordinator = _read("emoticorebot/agent/reflection/coordinator.py")
    host = _read("emoticorebot/bootstrap.py")

    assert "build_reflection_input" in coordinator
    assert "write_turn_reflection(self, reflection_input: ReflectionInput)" in coordinator
    assert "build_reflection_input(state)" in host


def test_source_tree_has_no_legacy_brain_or_task_system_imports() -> None:
    for path in PACKAGE_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        assert "from emoticorebot.agent.brain import" not in source, str(path)
        assert "from emoticorebot.agent.central" not in source, str(path)
        assert "from emoticorebot.session.manager import" not in source, str(path)
        assert "SessionTaskSystem" not in source, str(path)
        assert "BrainService" not in source, str(path)
        assert "CentralAgentService" not in source, str(path)
