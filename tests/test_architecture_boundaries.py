from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "emoticorebot"


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def test_legacy_runtime_modules_are_removed() -> None:
    assert not (PACKAGE_ROOT / "agent").exists()
    assert not (PACKAGE_ROOT / "session" / "manager.py").exists()
    assert (PACKAGE_ROOT / "session" / "runtime.py").exists()
    assert (PACKAGE_ROOT / "left_brain" / "runtime.py").exists()
    assert (PACKAGE_ROOT / "left_brain" / "packet.py").exists()
    assert (PACKAGE_ROOT / "left_brain" / "reply_policy.py").exists()
    assert (PACKAGE_ROOT / "left_brain" / "context.py").exists()
    assert not (PACKAGE_ROOT / "brain").exists()
    assert not (PACKAGE_ROOT / "task" / "runtime.py").exists()
    assert not (PACKAGE_ROOT / "task" / "coordinator.py").exists()
    assert (PACKAGE_ROOT / "right_brain" / "runtime.py").exists()
    assert not (PACKAGE_ROOT / "right_brain" / "coordinator.py").exists()
    assert not (PACKAGE_ROOT / "right_brain" / "team.py").exists()
    assert not (PACKAGE_ROOT / "right_brain" / "assignment.py").exists()
    assert not (PACKAGE_ROOT / "right_brain" / "recovery.py").exists()
    assert (PACKAGE_ROOT / "reflection" / "runtime.py").exists()
    assert (PACKAGE_ROOT / "input" / "normalizer.py").exists()
    assert not (PACKAGE_ROOT / "input" / "adapters.py").exists()
    assert not (PACKAGE_ROOT / "input" / "models.py").exists()
    assert (PACKAGE_ROOT / "output" / "__init__.py").exists()
    assert (PACKAGE_ROOT / "output" / "builder.py").exists()
    assert (PACKAGE_ROOT / "output" / "runtime.py").exists()
    assert (PACKAGE_ROOT / "delivery" / "runtime.py").exists()
    assert not (PACKAGE_ROOT / "runtime" / "service.py").exists()
    assert not (PACKAGE_ROOT / "runtime" / "scheduler.py").exists()
    assert not (PACKAGE_ROOT / "runtime" / "running_task.py").exists()
    assert not (PACKAGE_ROOT / "runtime" / "task_state.py").exists()
    assert not (PACKAGE_ROOT / "runtime" / "runtime.py").exists()
    assert not (PACKAGE_ROOT / "brain" / "companion_brain.py").exists()
    assert not (PACKAGE_ROOT / "brain" / "event_narrator.py").exists()
    assert not (PACKAGE_ROOT / "brain" / "reply_builder.py").exists()
    assert not (PACKAGE_ROOT / "runtime" / "session_runtime.py").exists()
    assert not (PACKAGE_ROOT / "runtime" / "event_loop.py").exists()
    assert not (PACKAGE_ROOT / "runtime" / "manager.py").exists()
    assert not (PACKAGE_ROOT / "memory" / "cognitive_events.py").exists()
    assert not (PACKAGE_ROOT / "memory" / "reflection_input.py").exists()
    assert not (PACKAGE_ROOT / "memory" / "turn_reflection.py").exists()
    assert not (PACKAGE_ROOT / "memory" / "deep_reflection.py").exists()
    assert not (PACKAGE_ROOT / "memory" / "memory_candidates.py").exists()
    assert not (PACKAGE_ROOT / "memory" / "governor.py").exists()
    assert not (PACKAGE_ROOT / "memory" / "persona.py").exists()
    assert not (PACKAGE_ROOT / "memory" / "reflection.py").exists()
    assert (PACKAGE_ROOT / "reflection" / "cognitive.py").exists()
    assert (PACKAGE_ROOT / "reflection" / "input.py").exists()
    assert (PACKAGE_ROOT / "reflection" / "turn.py").exists()
    assert (PACKAGE_ROOT / "reflection" / "deep.py").exists()
    assert (PACKAGE_ROOT / "reflection" / "candidates.py").exists()
    assert (PACKAGE_ROOT / "reflection" / "governor.py").exists()
    assert (PACKAGE_ROOT / "reflection" / "persona.py").exists()
    assert (PACKAGE_ROOT / "reflection" / "manager.py").exists()
    assert not (PACKAGE_ROOT / "background" / "reflection.py").exists()
    assert (PACKAGE_ROOT / "providers" / "factory.py").exists()
    assert (PACKAGE_ROOT / "tools" / "manager.py").exists()
    assert (PACKAGE_ROOT / "tools" / "mcp.py").exists()
    assert not (PACKAGE_ROOT / "protocol" / "safety_models.py").exists()


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
    left_runtime = _read("emoticorebot/left_brain/runtime.py")
    output = _read("emoticorebot/output/runtime.py")
    task = _read("emoticorebot/right_brain/runtime.py")

    assert "_turn_locks" not in bootstrap
    assert "_state_locks" not in bootstrap
    assert "if turn_id and not self.kernel.is_current_turn(" in bootstrap
    assert "self.kernel = RuntimeKernel(" in bootstrap
    assert "session_lock_for" not in kernel
    assert "self._pending_turns" in kernel
    assert "self._pending_turn_by_session" in kernel
    assert "self._active_turn_by_session" in kernel
    assert "EventType.OUTPUT_INLINE_READY" in kernel
    assert "InputNormalizer()" in kernel
    assert "RightBrainRuntime(" in kernel
    assert "LeftBrainRuntime(" in kernel
    assert "ReflectionRuntime(" in kernel
    assert "OutputRuntime(" in kernel
    assert "self._output_runtime = OutputRuntime(" in kernel
    assert "DeliveryRuntime(" in kernel
    assert "self._guard =" not in kernel
    assert "reply_guard=self._guard" not in kernel
    assert "self._runtime =" not in kernel
    assert "self._team =" not in kernel
    assert "self._brain =" not in kernel
    assert "self._memory =" not in kernel
    assert "self._delivery =" not in kernel
    assert "reply_guard" not in left_runtime
    assert "SafetyGuard" not in left_runtime
    assert "SafetyGuard" not in left_runtime
    assert "guard_reply_event" not in left_runtime
    assert "OUTPUT_INLINE_READY" not in left_runtime
    assert "OUTPUT_PUSH_READY" not in left_runtime
    assert "OUTPUT_STREAM_" not in left_runtime
    assert "EventType.LEFT_COMMAND_REPLY_REQUESTED" in left_runtime
    assert "EventType.LEFT_EVENT_REPLY_READY" in left_runtime
    assert "EventType.LEFT_EVENT_STREAM_DELTA_READY" in left_runtime
    assert "EventType.LEFT_EVENT_FOLLOWUP_READY" in left_runtime
    assert "guard_reply_event" in output
    assert "self._reply_guard = reply_guard or SafetyGuard()" in output
    assert "RuntimeService" not in task
    assert "RIGHT_COMMAND_JOB_REQUESTED" in task
    assert "RIGHT_EVENT_JOB_ACCEPTED" in task
    assert "RIGHT_EVENT_PROGRESS" in task
    assert "RIGHT_EVENT_RESULT_READY" in task
    assert "RightBrainExecutor" in task
    assert "self._active_runs" in task
    assert "audit_tool" in _read("emoticorebot/right_brain/backend.py")
    assert "EventType.INPUT_INTERRUPT" not in left_runtime
    assert "OUTPUT_REPLY_BLOCKED" not in _read("emoticorebot/protocol/topics.py")
    assert "SAFETY_BLOCKED" not in _read("emoticorebot/protocol/topics.py")
    assert "INPUT_VOICE_CHUNK" not in _read("emoticorebot/protocol/topics.py")


def test_runtime_wrappers_no_longer_describe_themselves_as_compatibility_layers() -> None:
    left_runtime = _read("emoticorebot/left_brain/runtime.py")
    task = _read("emoticorebot/right_brain/runtime.py")
    delivery = _read("emoticorebot/delivery/runtime.py")
    reflection = _read("emoticorebot/reflection/runtime.py")
    session = _read("emoticorebot/session/runtime.py")

    assert "Compatibility" not in left_runtime
    assert "Compatibility" not in task
    assert "Compatibility" not in delivery
    assert "Compatibility" not in reflection
    assert "Compatibility" not in session


def test_reflection_pipeline_runs_through_kernel_reflection_governor() -> None:
    left_runtime = _read("emoticorebot/left_brain/runtime.py")
    governor = _read("emoticorebot/reflection/governor.py")
    reflection = _read("emoticorebot/reflection/manager.py")
    persona = _read("emoticorebot/reflection/persona.py")
    deep = _read("emoticorebot/reflection/deep.py")
    context = _read("emoticorebot/left_brain/context.py")
    crystallizer = _read("emoticorebot/memory/crystallizer.py")
    kernel = _read("emoticorebot/runtime/kernel.py")
    host = _read("emoticorebot/bootstrap.py")

    assert '"reflection_input"' in left_runtime
    assert '"await_delivery"' not in left_runtime
    assert "PersonaManager" in governor
    assert "ReflectionManager" in governor
    assert "OUTPUT_REPLIED" not in governor
    assert "_pending" not in governor
    assert "build_reflection_input(raw)" in reflection
    assert "ManagedAnchorWriter" in persona
    assert "persist_proposal" not in deep
    assert "write_managed_reflection_section" not in deep
    assert "MemoryRetrieval" in context
    assert "MemoryStore(" not in context
    assert "memory.skill_hint" in crystallizer
    assert "self._reflection = ReflectionRuntime(" in kernel
    assert "return await self.kernel.run_deep_reflection" in host
    assert "_schedule_turn_reflection" not in host
    assert "ReflectionCoordinator" not in host
    assert "event_type=EventType.REFLECTION_DEEP" not in left_runtime


def test_memory_phase6_is_split_into_governor_persona_and_reflection_modules() -> None:
    governor = _read("emoticorebot/reflection/governor.py")
    reflection = _read("emoticorebot/reflection/manager.py")
    persona = _read("emoticorebot/reflection/persona.py")
    retrieval = _read("emoticorebot/memory/retrieval.py")
    crystallizer = _read("emoticorebot/memory/crystallizer.py")

    assert not (PACKAGE_ROOT / "memory" / "governor.py").exists()
    assert not (PACKAGE_ROOT / "memory" / "persona.py").exists()
    assert not (PACKAGE_ROOT / "memory" / "reflection.py").exists()
    assert (PACKAGE_ROOT / "memory" / "retrieval.py").exists()
    assert (PACKAGE_ROOT / "memory" / "crystallizer.py").exists()
    assert (PACKAGE_ROOT / "reflection" / "governor.py").exists()
    assert (PACKAGE_ROOT / "reflection" / "persona.py").exists()
    assert (PACKAGE_ROOT / "reflection" / "manager.py").exists()
    assert not (PACKAGE_ROOT / "memory" / "facade.py").exists()
    assert not (PACKAGE_ROOT / "memory" / "service.py").exists()
    assert "run_turn_reflection(" not in governor
    assert "DeepReflectionService" in governor
    assert "CognitiveEvent" not in governor
    assert "propose_turn(" in reflection
    assert "append_deep_memories(" in reflection
    assert '("deep", "persona")' in persona
    assert '("turn", "persona")' in persona
    assert "query_left_brain_memories" in retrieval
    assert "build_task_memory_bundle" in retrieval
    assert "class SkillMaterializer" in crystallizer


def test_source_tree_has_no_legacy_brain_or_task_system_imports() -> None:
    legacy_import_prefix = "from emoticorebot." + "brain"
    for path in PACKAGE_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        assert "from emoticorebot.agent" not in source, str(path)
        assert "from emoticorebot.agent.brain import" not in source, str(path)
        assert "from emoticorebot.agent.central" not in source, str(path)
        assert "SessionTaskSystem" not in source, str(path)
        assert "BrainService" not in source, str(path)
        assert "CentralAgentService" not in source, str(path)
        assert legacy_import_prefix not in source, str(path)
        assert "from emoticorebot.runtime.session_runtime import" not in source, str(path)
        assert "from emoticorebot.runtime.manager import" not in source, str(path)
        assert "from emoticorebot.runtime.event_loop import" not in source, str(path)


def test_right_runtime_uses_deep_agent_directly_without_legacy_team_wrapper() -> None:
    task = _read("emoticorebot/right_brain/runtime.py")
    kernel = _read("emoticorebot/runtime/kernel.py")

    assert not (PACKAGE_ROOT / "execution" / "central_executor.py").exists()
    assert not (PACKAGE_ROOT / "right_brain" / "team.py").exists()
    assert "RightBrainExecutor" in task
    assert "coordinator" not in task
    assert "team" not in task
    assert "central_executor" not in kernel


