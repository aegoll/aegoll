"""One base class, so `except TesoroError` catches everything this library raises.

See `docs/api-surface.md` §6. The shape that matters most is what is *not* here:

**A refusal is not an exception.** `authorize()` returns a `Decision` whose `approved` is
False. `RefusedError` exists only for callers who explicitly opt into
`raise_on_refusal=True`, because a governance layer whose normal operation raises teaches
people to wrap it in `try: ... except: pass` — and here that means an ungoverned agent.
"""

from __future__ import annotations

from typing import Any


class TesoroError(Exception):
    """Base for everything this library raises."""


class ConfigError(TesoroError):
    """`tesoro.yaml` is missing, unreadable, or says something impossible."""


class PolicyError(TesoroError):
    """A policy pack is invalid.

    Carries every problem rather than the first, because a policy with four mistakes
    should be fixed in one pass, and because `tesoro check` in CI needs the whole list.
    """

    def __init__(self, message: str, problems: list[Any] | None = None) -> None:
        self.problems = list(problems or [])
        if self.problems:
            message = message + "\n  " + "\n  ".join(str(p) for p in self.problems)
        super().__init__(message)


class RefusedError(TesoroError):
    """Opt-in only. Carries the `Decision` that refused."""

    def __init__(self, decision: Any) -> None:
        self.decision = decision
        super().__init__(
            f"{getattr(decision, 'verdict', 'REFUSED')} by "
            f"{getattr(decision, 'attributed_control', 'unknown control')}: "
            f"{getattr(decision, 'reason', '')}"
        )


class EvidenceError(TesoroError):
    """The evidence chain is broken, or cannot be written."""


class RegistrationError(TesoroError):
    """A custom engine or rule kind violated the contract it must satisfy."""


__all__ = [
    "TesoroError",
    "ConfigError",
    "EvidenceError",
    "PolicyError",
    "RefusedError",
    "RegistrationError",
]
