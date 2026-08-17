"""Injectable clock.

Determinism (ADR-004) depends on time being an input rather than an ambient fact.
Every engine that needs "now" receives it; nothing calls `datetime.now()` inline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """Wall clock, always timezone-aware UTC."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock:
    """A clock that does not move unless told to. Used by tests and replay."""

    def __init__(self, at: datetime) -> None:
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        self._at = at

    def now(self) -> datetime:
        return self._at

    def advance(self, **kwargs: float) -> "FixedClock":
        self._at = self._at + timedelta(**kwargs)
        return self
