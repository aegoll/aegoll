"""The REVIEW / ESCALATE queue -- where a human closes the loop.

`REVIEW` is pausable: the item waits and a human answers later. `ESCALATE` is
blocking: the agent cannot proceed at all. Both land here so nothing is silently
dropped, but they are reported distinctly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

Resolution = Literal["pending", "approved", "denied", "expired"]


@dataclass
class ReviewItem:
    request_id: str
    at: str
    agent_id: str
    vendor_id: str
    resource: str
    amount_usd: float
    verdict: str
    reasons: list[str]
    resolution: Resolution = "pending"
    resolved_at: str | None = None
    resolved_by: str | None = None
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.verdict == "ESCALATE"

    def as_dict(self) -> dict[str, Any]:
        return {
            "requestId": self.request_id,
            "at": self.at,
            "agent": self.agent_id,
            "vendor": self.vendor_id,
            "resource": self.resource,
            "amountUsd": self.amount_usd,
            "verdict": self.verdict,
            "reasons": self.reasons,
            "resolution": self.resolution,
            "resolvedAt": self.resolved_at,
            "resolvedBy": self.resolved_by,
            "note": self.note,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ReviewItem":
        return cls(
            request_id=raw["requestId"],
            at=raw["at"],
            agent_id=raw.get("agent", ""),
            vendor_id=raw.get("vendor", ""),
            resource=raw.get("resource", ""),
            amount_usd=float(raw.get("amountUsd", 0)),
            verdict=raw.get("verdict", "REVIEW"),
            reasons=list(raw.get("reasons") or []),
            resolution=raw.get("resolution", "pending"),
            resolved_at=raw.get("resolvedAt"),
            resolved_by=raw.get("resolvedBy"),
            note=raw.get("note", ""),
            extra=dict(raw.get("extra") or {}),
        )


class ReviewQueue:
    """A JSON file. Small enough that atomic rewrite is the right trade."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[ReviewItem]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return [ReviewItem.from_dict(r) for r in raw]

    def _save(self, items: list[ReviewItem]) -> None:
        self.path.write_text(
            json.dumps([i.as_dict() for i in items], indent=2), encoding="utf-8"
        )

    def all(self) -> list[ReviewItem]:
        return self._load()

    def pending(self) -> list[ReviewItem]:
        return [i for i in self._load() if i.resolution == "pending"]

    def enqueue(self, item: ReviewItem) -> ReviewItem:
        items = self._load()
        # Re-deciding the same request replaces its pending entry rather than
        # stacking duplicates.
        items = [i for i in items if i.request_id != item.request_id]
        items.append(item)
        self._save(items)
        return item

    def resolve(
        self, request_id: str, resolution: Resolution, by: str = "human", note: str = ""
    ) -> ReviewItem | None:
        items = self._load()
        found: ReviewItem | None = None
        for item in items:
            if item.request_id == request_id:
                item.resolution = resolution
                item.resolved_at = datetime.now(timezone.utc).isoformat()
                item.resolved_by = by
                item.note = note
                found = item
        if found:
            self._save(items)
        return found

    def clear_resolved(self) -> int:
        items = self._load()
        keep = [i for i in items if i.resolution == "pending"]
        removed = len(items) - len(keep)
        self._save(keep)
        return removed
