"""The kill switch: refuse everything, and say why.

An operator control, and the threat model is worth stating before the code. **A freeze stops a
misbehaving agent; it does not contain an adversarial one.** The state lives in a file the agent's
own process can usually write, so an agent that controls its host can lift its own freeze — the
same limit that makes an append-only file beside the journal a poor anchor
(`engines/evidence/anchor.py`). It is not a containment boundary and must not be described as one.

**Why this exists when identity revocation already refuses.** It does, and it silently does nothing
for an agent that never registered an identity: `identities.set_status()` returns `False` and the
next payment is APPROVED. Measured. A kill switch whose effect depends on an unrelated registration
having happened is not a kill switch, so this one holds no such precondition.

Persisted, because a freeze that evaporates when the process restarts is not a freeze — a crash
loop would resume spending.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FreezeState:
    """Frozen, and the reason. `None` reason means not frozen."""

    reason: str | None = None
    at: str | None = None
    by: str | None = None

    @property
    def frozen(self) -> bool:
        return self.reason is not None

    def as_dict(self) -> dict[str, Any]:
        return {"frozen": self.frozen, "reason": self.reason, "at": self.at, "by": self.by}


NOT_FROZEN = FreezeState()


class FreezeStore:
    """One small JSON file. Read on every decision, so it must stay cheap."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def read(self) -> FreezeState:
        """The current state. **A file that cannot be parsed reads as FROZEN, not as clear.**

        The safe direction is the whole point. A corrupt or truncated freeze file is an unknown
        state, and continuing to spend on an unknown state is the failure this module exists to
        prevent -- the same rule as `absent degrades, broken raises` for the governance layer's own
        availability, pointed at its own state file.
        """
        if not self.path.exists():
            return NOT_FROZEN
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - any unreadable state is the same fact
            return FreezeState(
                reason=(
                    f"the freeze state at {self.path.name} could not be read "
                    f"({type(exc).__name__}); refusing rather than assuming it is clear"
                ),
                at=None,
                by="tesoro",
            )
        if not raw.get("frozen"):
            return NOT_FROZEN
        return FreezeState(
            reason=raw.get("reason") or "frozen, with no reason recorded",
            at=raw.get("at"),
            by=raw.get("by"),
        )

    def freeze(self, reason: str, *, by: str | None = None) -> FreezeState:
        if not (reason or "").strip():
            raise ValueError(
                "a freeze needs a reason. Whoever finds the agent stopped at 2am has to be able "
                "to read why, and an empty string is how that becomes a mystery"
            )
        state = FreezeState(
            reason=reason.strip(),
            at=datetime.now(timezone.utc).isoformat(),
            by=by,
        )
        self._write(state)
        return state

    def clear(self) -> FreezeState:
        self._write(NOT_FROZEN)
        return NOT_FROZEN

    def _write(self, state: FreezeState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(state.as_dict(), indent=2) + "\n", encoding="utf-8"
        )
