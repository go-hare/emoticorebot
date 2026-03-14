from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from emoticorebot.execution.backend import build_backend


def _build_service(workspace: Path) -> SimpleNamespace:
    return SimpleNamespace(context=SimpleNamespace(workspace=str(workspace)))


def test_backend_routes_workspace_root_to_real_filesystem() -> None:
    with TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir)
        backend_factory = build_backend(_build_service(workspace))
        assert backend_factory is not None

        runtime = SimpleNamespace(state={})
        backend = backend_factory(runtime)

        result = backend.write("/sub.py", "def sub(a, b):\n    return a + b")

        assert result.error is None
        assert (workspace / "sub.py").exists()
        assert (workspace / "sub.py").read_text(encoding="utf-8") == "def sub(a, b):\n    return a + b"
        assert runtime.state.get("files", {}) == {}


def test_backend_keeps_state_namespace_ephemeral() -> None:
    with TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir)
        backend_factory = build_backend(_build_service(workspace))
        assert backend_factory is not None

        runtime = SimpleNamespace(state={})
        backend = backend_factory(runtime)

        result = backend.write("/state/scratch.txt", "temporary note")

        assert result.error is None
        assert not (workspace / "state" / "scratch.txt").exists()
        assert "/scratch.txt" in runtime.state.get("files", {})
        assert "temporary note" in backend.read("/state/scratch.txt")
