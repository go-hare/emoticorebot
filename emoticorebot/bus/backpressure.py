"""Backpressure primitives for the priority pub/sub bus."""

from __future__ import annotations


class BackpressureError(RuntimeError):
    """Raised when the bus queue exceeds the configured hard limit."""


class BackpressureController:
    """Tracks queue pressure and enforces a hard capacity limit."""

    def __init__(self, *, warning_threshold: int = 128, max_queue_size: int = 1024) -> None:
        if warning_threshold <= 0:
            raise ValueError("warning_threshold must be positive")
        if max_queue_size < warning_threshold:
            raise ValueError("max_queue_size must be >= warning_threshold")
        self.warning_threshold = warning_threshold
        self.max_queue_size = max_queue_size

    def check(self, queue_size: int) -> bool:
        if queue_size > self.max_queue_size:
            raise BackpressureError(f"queue size {queue_size} exceeds limit {self.max_queue_size}")
        return queue_size >= self.warning_threshold


__all__ = ["BackpressureController", "BackpressureError"]
