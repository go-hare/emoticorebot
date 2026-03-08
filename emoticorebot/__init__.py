"""
emoticorebot - A lightweight AI agent framework
"""

import sys

__version__ = "0.0.2"


def _pick_logo() -> str:
    logo = "🐾"
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        logo.encode(encoding)
        return logo
    except Exception:
        return "[EC]"


__logo__ = _pick_logo()
