from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "emoticorebot"


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def test_legacy_runtime_modules_are_removed() -> None:
    assert not (PACKAGE_ROOT / "agent" / "brain.py").exists()
    assert not (PACKAGE_ROOT / "agent" / "system.py").exists()
    assert not (PACKAGE_ROOT / "agent" / "central").exists()
    assert not (PACKAGE_ROOT / "agent" / "central" / "central.py").exists()
    assert not any((PACKAGE_ROOT / "agent" / "central").glob("*.py"))
    assert not (PACKAGE_ROOT / "session" / "manager.py").exists()
    assert not (PACKAGE_ROOT / "runtime" / "runtime.py").exists()
    assert not (PACKAGE_ROOT / "brain" / "companion_brain.py").exists()
    assert not (PACKAGE_ROOT / "brain" / "event_narrator.py").exists()
    assert not (PACKAGE_ROOT / "runtime" / "session_runtime.py").exists()
    assert not (PACKAGE_ROOT / "runtime" / "event_loop.py").exists()
    assert not (PACKAGE_ROOT / "runtime" / "manager.py").exists()
    assert not (PACKAGE_ROOT / "agent" / "reflection" / "coordinator.py").exists()
    assert not (PACKAGE_ROOT / "agent" / "reflection" / "memory.py").exists()
    assert not (PACKAGE_ROOT / "agent" / "reflection" / "skill.py").exists()


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
    assert "await self._reset_session(key)" in source
    assert "reply_to=message_id" in source
    assert "metadata=msg.metadata or {}" in source


def test_runtime_host_uses_preemptive_turn_guards_instead_of_locks() -> None:
    bootstrap = _read("emoticorebot/bootstrap.py")
    kernel = _read("emoticorebot/runtime/kernel.py")

    assert "_turn_locks" not in bootstrap
    assert "_state_locks" not in bootstrap
    assert "await self.kernel.interrupt_session(" in bootstrap
    assert "if turn_id and not self.kernel.is_current_turn(" in bootstrap
    assert "self.kernel = RuntimeKernel(" in bootstrap
    assert "session_lock_for" not in kernel
    assert "self._pending_turns" in kernel
    assert "self._pending_turn_by_session" in kernel
    assert "self._active_turn_by_session" in kernel
    assert "EventType.INPUT_INTERRUPT" in kernel
    assert "EventType.OUTPUT_REPLY_APPROVED" in kernel


def test_reflection_pipeline_runs_through_kernel_memory_governor() -> None:
    executive = _read("emoticorebot/brain/executive.py")
    governor = _read("emoticorebot/memory/governor.py")
    reflection = _read("emoticorebot/memory/reflection.py")
    persona = _read("emoticorebot/memory/persona.py")
    deep = _read("emoticorebot/agent/reflection/deep.py")
    agent_exports = _read("emoticorebot/agent/reflection/__init__.py")
    agent_root = _read("emoticorebot/agent/__init__.py")
    context = _read("emoticorebot/agent/context.py")
    crystallizer = _read("emoticorebot/memory/crystallizer.py")
    kernel = _read("emoticorebot/runtime/kernel.py")
    host = _read("emoticorebot/bootstrap.py")

    assert '"reflection_input"' in executive
    assert "PersonaManager" in governor
    assert "ReflectionManager" in governor
    assert "build_reflection_input(raw)" in reflection
    assert "ManagedAnchorWriter" in persona
    assert "persist_proposal" not in deep
    assert "write_managed_reflection_section" not in deep
    assert "ReflectionCoordinator" not in agent_exports
    assert "MemoryService" not in agent_exports
    assert "SkillMaterializer" not in agent_exports
    assert "MemoryService" not in agent_root
    assert "MemoryRetrieval" in context
    assert "MemoryStore(" not in context
    assert "memory.skill_hint" in crystallizer
    assert "self._memory = MemoryGovernor(" in kernel
    assert "return await self.kernel.run_deep_reflection" in host
    assert "_schedule_turn_reflection" not in host
    assert "ReflectionCoordinator" not in host


def test_memory_phase6_is_split_into_governor_persona_and_reflection_modules() -> None:
    governor = _read("emoticorebot/memory/governor.py")
    reflection = _read("emoticorebot/memory/reflection.py")
    persona = _read("emoticorebot/memory/persona.py")
    retrieval = _read("emoticorebot/memory/retrieval.py")
    crystallizer = _read("emoticorebot/memory/crystallizer.py")

    assert (PACKAGE_ROOT / "memory" / "governor.py").exists()
    assert (PACKAGE_ROOT / "memory" / "persona.py").exists()
    assert (PACKAGE_ROOT / "memory" / "reflection.py").exists()
    assert (PACKAGE_ROOT / "memory" / "retrieval.py").exists()
    assert (PACKAGE_ROOT / "memory" / "crystallizer.py").exists()
    assert "reflect_turn(" not in governor
    assert "DeepReflectionService" in governor
    assert "CognitiveEvent" not in governor
    assert "propose_turn(" in reflection
    assert "append_deep_memories(" in reflection
    assert '("deep", "persona")' in persona
    assert '("turn", "persona")' in persona
    assert "query_brain_memories" in retrieval
    assert "build_task_memory_bundle" in retrieval
    assert "class SkillMaterializer" in crystallizer


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
        assert "from emoticorebot.brain.companion_brain import" not in source, str(path)
        assert "from emoticorebot.brain.event_narrator import" not in source, str(path)
        assert "from emoticorebot.runtime.session_runtime import" not in source, str(path)
        assert "from emoticorebot.runtime.manager import" not in source, str(path)
        assert "from emoticorebot.runtime.event_loop import" not in source, str(path)


def test_worker_team_no_longer_imports_legacy_executor_wrapper() -> None:
    team = _read("emoticorebot/execution/team.py")
    kernel = _read("emoticorebot/runtime/kernel.py")

    assert not (PACKAGE_ROOT / "execution" / "central_executor.py").exists()
    assert "from emoticorebot.execution.central_executor import CentralExecutor" not in team
    assert "DeepAgentExecutor" in team
    assert "central_executor" not in kernel
