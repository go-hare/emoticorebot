from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from emoticorebot.reflection.persona import ManagedAnchorWriter


def _history_rows(workspace: Path, *, target: str, scope: str) -> list[dict[str, object]]:
    path = workspace / ".governance" / "persona" / target / scope / "history.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_managed_anchor_writer_records_versions() -> None:
    with TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir)
        writer = ManagedAnchorWriter(workspace)

        first = writer.write(target="persona", updates=["复杂问题先收敛判断"], scope="deep")
        second = writer.write(target="persona", updates=["对外表达保持克制"], scope="deep")

        assert first.applied is True
        assert first.version == 1
        assert second.applied is True
        assert second.version == 2

        manifest = json.loads(
            (workspace / ".governance" / "persona" / "persona" / "deep" / "manifest.json").read_text(encoding="utf-8")
        )
        history = _history_rows(workspace, target="persona", scope="deep")
        soul = (workspace / "SOUL.md").read_text(encoding="utf-8")

        assert manifest["latest_version"] == 2
        assert len(history) == 2
        assert history[-1]["action"] == "apply"
        assert "复杂问题先收敛判断" in soul
        assert "对外表达保持克制" in soul


def test_managed_anchor_writer_detects_conflict_and_preserves_disk_edits() -> None:
    with TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir)
        writer = ManagedAnchorWriter(workspace)

        initial = writer.write(target="persona", updates=["优先给用户最终结论"], scope="deep")
        assert initial.version == 1

        soul_path = workspace / "SOUL.md"
        soul = soul_path.read_text(encoding="utf-8")
        soul = soul.replace("- 优先给用户最终结论", "- 人工改写的稳定风格")
        soul_path.write_text(soul, encoding="utf-8")

        result = writer.write(target="persona", updates=["避免过早承诺"], scope="deep")
        history = _history_rows(workspace, target="persona", scope="deep")
        updated = soul_path.read_text(encoding="utf-8")

        assert result.applied is True
        assert result.version == 2
        assert result.conflict_detected is True
        assert history[-1]["conflict_detected"] is True
        assert "人工改写的稳定风格" in updated
        assert "避免过早承诺" in updated


def test_managed_anchor_writer_can_rollback_to_prior_version() -> None:
    with TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir)
        writer = ManagedAnchorWriter(workspace)

        writer.write(target="persona", updates=["先判断再执行"], scope="deep")
        writer.write(target="persona", updates=["结论优先返回"], scope="deep")

        rollback = writer.rollback(target="persona", scope="deep", version=1)
        history = _history_rows(workspace, target="persona", scope="deep")
        soul = (workspace / "SOUL.md").read_text(encoding="utf-8")

        assert rollback.applied is True
        assert rollback.version == 3
        assert rollback.rollback_to_version == 1
        assert history[-1]["action"] == "rollback"
        assert history[-1]["rollback_to_version"] == 1
        assert "先判断再执行" in soul
        assert "结论优先返回" not in soul
