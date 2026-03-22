"""Structured outputs for backend agents."""

from __future__ import annotations

from pydantic import BaseModel


class CoreResult(BaseModel):
    summary: str = ""
