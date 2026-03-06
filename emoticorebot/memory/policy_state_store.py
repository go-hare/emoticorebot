from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


class PolicyStateStore:
    """Persistent store for runtime policy adjustments."""

    def __init__(self, workspace: Path):
        self._file = workspace / "data" / "fusion_policy_state.json"
        self._file.parent.mkdir(parents=True, exist_ok=True)

    def save_adjustment(self, adjustment: dict[str, Any]) -> None:
        payload = dict(adjustment)
        duration_hours = payload.pop("duration_hours", None)
        if duration_hours is not None:
            try:
                expires_at = datetime.now() + timedelta(hours=float(duration_hours))
                payload["expires_at"] = expires_at.isoformat()
            except Exception:
                payload["expires_at"] = None
        payload.setdefault("updated_at", datetime.now().isoformat())
        self._file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_active_adjustment(self) -> dict[str, Any] | None:
        if not self._file.exists():
            return None
        try:
            payload = json.loads(self._file.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None
            expires_at = payload.get("expires_at")
            if expires_at:
                try:
                    if datetime.now() > datetime.fromisoformat(str(expires_at)):
                        return None
                except Exception:
                    return None
            return payload
        except Exception:
            return None

