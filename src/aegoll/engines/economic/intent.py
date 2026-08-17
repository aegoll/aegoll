"""Engine 9 — economic intent: was this what the agent was sent to do?

Every other engine answers a variant of *may this agent spend?* Treasury asks
whether there is budget, trust asks about the counterparty, policy asks what the
rules say. All of them would approve a perfectly ordinary purchase made by an agent
that has been quietly repurposed — right amount, familiar vendor, budget intact,
and nothing whatever to do with the job it was given.

Intent is the engine that can notice. It compares the proposed action against a
declaration made *before* the agent started, so the question becomes: is this
consistent with what it was sent out to do?

## Design rules

**Intent is optional, and its absence is stated rather than assumed.** An agent
with no declared intent is not refused — that would break every existing caller and
punish the ordinary case. It produces an explicit `no_intent_declared` reason, so a
Decision Record shows *ungoverned by intent* rather than silently implying it
passed a check that never ran. Absent, unknown and zero stay distinct here as
everywhere else in AEGS.

**Intent may only narrow.** Like every engine, it can tighten a verdict and never
widen one. An intent cannot authorise something the treasury refused.

**The total is the budget, not the per-action figure.** A ceiling that permits
unlimited repetitions is not a budget. Structuring one large spend into many small
ones is a named threat in the roadmap, and a per-action-only limit is exactly what
makes it work, so `maximumAmount` is spent-to-date across the whole intent and
`maximumPerAction` is the optional extra.

**A different asset is refused, not converted.** Comparing USDC against a USD
ceiling requires a rate, a source and a timestamp. Guessing one silently is how a
governance layer starts being wrong about money.
"""

from __future__ import annotations

import fnmatch
import json
from importlib import resources
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...domain import (
    Channel,
    PaymentRequest,
    Reason,
    Verdict,
    atomic_to_usd,
    fmt_usd,
    usd_to_atomic,
)

AEGS_VERSION = "0.1"

#: Vendored package data — see `_schemas/PROVENANCE.txt`. The prototype resolved this
#: from `parents[4]`, which only worked inside the monorepo. See PLAN.md F-A1.
SCHEMA_PATH = Path(
    str(resources.files("aegoll") / "_schemas" / "economic-intent-0.1.json")
).resolve()


@dataclass(frozen=True)
class Intent:
    """A declaration of what an agent was sent to do, made before it acts."""

    intent_id: str
    agent_id: str
    purpose: str
    maximum_atomic: int
    asset: str = "USDC"
    maximum_per_action_atomic: int | None = None
    allowed_resources: tuple[str, ...] = ()
    allowed_categories: tuple[str, ...] = ()
    allowed_channels: tuple[str, ...] = ()
    expected_outcome: str | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None
    status: str = "active"
    declared_by: str | None = None

    @property
    def unrestricted_resources(self) -> bool:
        """No resource list means unrestricted — a real choice, visible as one."""
        return not self.allowed_resources

    def covers_channel(self, channel: Channel | str) -> bool:
        if not self.allowed_channels:
            return True
        value = channel.value if isinstance(channel, Channel) else str(channel)
        return value in self.allowed_channels

    def permits_resource(self, resource: str) -> bool:
        if self.unrestricted_resources:
            return True
        return any(fnmatch.fnmatch(resource, pattern) for pattern in self.allowed_resources)

    def permits_category(self, category: str | None) -> bool:
        if not self.allowed_categories:
            return True
        return bool(category) and category in self.allowed_categories

    def expired_at(self, now: datetime) -> bool:
        return self.expires_at is not None and now >= self.expires_at

    def as_dict(self) -> dict[str, Any]:
        return {
            "aegsVersion": AEGS_VERSION,
            "intentId": self.intent_id,
            "agentId": self.agent_id,
            "purpose": self.purpose,
            "maximumAmount": f"{atomic_to_usd(self.maximum_atomic):.6f}",
            "maximumPerAction": (
                f"{atomic_to_usd(self.maximum_per_action_atomic):.6f}"
                if self.maximum_per_action_atomic is not None
                else None
            ),
            "asset": self.asset,
            "allowedResources": list(self.allowed_resources),
            "allowedCategories": list(self.allowed_categories),
            **(
                {"allowedChannels": list(self.allowed_channels)}
                if self.allowed_channels
                else {}
            ),
            "expectedOutcome": self.expected_outcome,
            "createdAt": (self.created_at or datetime.now(timezone.utc)).isoformat(),
            "expiresAt": self.expires_at.isoformat() if self.expires_at else None,
            "status": self.status,
            "declaredBy": self.declared_by,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Intent":
        def _dt(value: Any) -> datetime | None:
            return datetime.fromisoformat(value) if value else None

        return cls(
            intent_id=data["intentId"],
            agent_id=data["agentId"],
            purpose=data["purpose"],
            maximum_atomic=usd_to_atomic(data["maximumAmount"]),
            asset=data.get("asset", "USDC"),
            maximum_per_action_atomic=(
                usd_to_atomic(data["maximumPerAction"])
                if data.get("maximumPerAction")
                else None
            ),
            allowed_resources=tuple(data.get("allowedResources") or ()),
            allowed_categories=tuple(data.get("allowedCategories") or ()),
            allowed_channels=tuple(data.get("allowedChannels") or ()),
            expected_outcome=data.get("expectedOutcome"),
            created_at=_dt(data.get("createdAt")),
            expires_at=_dt(data.get("expiresAt")),
            status=data.get("status", "active"),
            declared_by=data.get("declaredBy"),
        )


@dataclass
class IntentVerdict:
    """What the intent engine concluded, and why."""

    intent: Intent | None
    verdict: Verdict
    reasons: tuple[Reason, ...] = ()
    spent_atomic: int = 0
    flags: tuple[str, ...] = ()

    @property
    def governed(self) -> bool:
        """False when no intent was declared — recorded, never implied."""
        return self.intent is not None

    @property
    def headroom_atomic(self) -> int:
        if self.intent is None:
            return 0
        return max(0, self.intent.maximum_atomic - self.spent_atomic)

    def as_dict(self) -> dict[str, Any]:
        return {
            "governed": self.governed,
            "intentId": self.intent.intent_id if self.intent else None,
            "purpose": self.intent.purpose if self.intent else None,
            "verdict": self.verdict.value,
            "spentUsd": float(atomic_to_usd(self.spent_atomic)),
            "headroomUsd": float(atomic_to_usd(self.headroom_atomic)),
            "maximumUsd": (
                float(atomic_to_usd(self.intent.maximum_atomic)) if self.intent else None
            ),
            "flags": list(self.flags),
            "reasons": [r.as_dict() for r in self.reasons],
        }


def evaluate(
    request: PaymentRequest,
    intent: Intent | None,
    *,
    now: datetime,
    spent_atomic: int = 0,
    category: str | None = None,
) -> IntentVerdict:
    """Is this action consistent with what the agent was sent to do?

    `spent_atomic` is what has already been spent under this intent, which is why
    the caller supplies it — the intent engine does not own a ledger, it reads the
    same history every other engine reads.
    """
    if intent is None:
        return IntentVerdict(
            intent=None,
            verdict=Verdict.APPROVE,
            flags=("no_intent",),
            reasons=(
                Reason(
                    "intent",
                    "no_intent_declared",
                    "no economic intent was declared for this agent; the action is "
                    "ungoverned by intent and this record says so rather than "
                    "implying a check that did not run",
                ),
            ),
        )

    reasons: list[Reason] = []
    flags: list[str] = []
    verdict = Verdict.APPROVE

    def refuse(code: str, detail: str, level: Verdict) -> None:
        nonlocal verdict
        flags.append(code)
        reasons.append(Reason("intent", code, detail, level))
        from ...domain import narrower  # noqa: PLC0415

        verdict = narrower(verdict, level)

    # --- the intent must still be live -----------------------------------
    if intent.status == "revoked":
        refuse(
            "intent_revoked",
            f"intent {intent.intent_id} was revoked; it authorises nothing",
            Verdict.REJECT,
        )
    elif intent.status == "fulfilled":
        refuse(
            "intent_fulfilled",
            f"intent {intent.intent_id} is already fulfilled",
            Verdict.REVIEW,
        )

    if intent.expired_at(now):
        refuse(
            "intent_expired",
            f"intent {intent.intent_id} expired at "
            f"{intent.expires_at.isoformat() if intent.expires_at else '?'}; "
            "an expired authorisation is not a weaker one",
            Verdict.REJECT,
        )

    # --- the action must be the kind of thing this intent is about --------
    if not intent.covers_channel(request.channel):
        refuse(
            "intent_channel_mismatch",
            f"intent covers {', '.join(intent.allowed_channels)} but this is a "
            f"{request.channel.value} action",
            Verdict.REVIEW,
        )

    if not intent.permits_resource(request.resource):
        refuse(
            "intent_resource_mismatch",
            f"`{request.resource}` is outside the intent's declared resources "
            f"({', '.join(intent.allowed_resources)})",
            Verdict.REVIEW,
        )

    if intent.allowed_categories and not intent.permits_category(category):
        refuse(
            "intent_category_mismatch",
            f"category `{category or 'none supplied'}` is outside the intent's "
            f"declared categories ({', '.join(intent.allowed_categories)})",
            Verdict.REVIEW,
        )

    # --- and it must be affordable within the intent ----------------------
    expected_asset = _asset_for(request)
    if expected_asset != intent.asset:
        refuse(
            "intent_asset_mismatch",
            f"intent is denominated in {intent.asset} but this action is in "
            f"{expected_asset}; converting silently would require a rate this "
            "layer does not have",
            Verdict.REJECT,
        )

    if (
        intent.maximum_per_action_atomic is not None
        and request.amount_atomic > intent.maximum_per_action_atomic
    ):
        refuse(
            "intent_per_action_exceeded",
            f"{fmt_usd(request.amount_atomic)} exceeds this intent's per-action "
            f"ceiling of {fmt_usd(intent.maximum_per_action_atomic)}",
            Verdict.REJECT,
        )

    remaining = intent.maximum_atomic - spent_atomic
    if request.amount_atomic > remaining:
        refuse(
            "intent_budget_exceeded",
            f"{fmt_usd(request.amount_atomic)} exceeds the "
            f"{fmt_usd(max(0, remaining))} remaining of this intent's "
            f"{fmt_usd(intent.maximum_atomic)} total",
            Verdict.REJECT,
        )

    if not reasons:
        reasons.append(
            Reason(
                "intent",
                "within_intent",
                f"consistent with intent {intent.intent_id} "
                f"({intent.purpose}); {fmt_usd(max(0, remaining))} of "
                f"{fmt_usd(intent.maximum_atomic)} remains",
                Verdict.APPROVE,
            )
        )

    return IntentVerdict(
        intent=intent,
        verdict=verdict,
        reasons=tuple(reasons),
        spent_atomic=spent_atomic,
        flags=tuple(flags),
    )


def _asset_for(request: PaymentRequest) -> str:
    """The unit an action is denominated in.

    Internal spend is real dollars on a provider key; external spend is USDC on
    chain. They are not interchangeable, which is why the two channels never share
    an envelope.
    """
    return "USD" if request.channel is Channel.INTERNAL else "USDC"


# --- storage --------------------------------------------------------------


class IntentStore:
    """Declared intents, kept as plain JSON beside the rest of the journal.

    Deliberately simple and separate from the sqlite history: an intent is a
    *declaration*, not a measurement, and it is edited by operators rather than
    accumulated by the system.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def declare(
        self,
        *,
        agent_id: str,
        purpose: str,
        maximum_usd: str | float,
        asset: str = "USDC",
        maximum_per_action_usd: str | float | None = None,
        allowed_resources: tuple[str, ...] | list[str] = (),
        allowed_categories: tuple[str, ...] | list[str] = (),
        allowed_channels: tuple[str, ...] | list[str] = (),
        expected_outcome: str | None = None,
        expires_at: datetime | None = None,
        declared_by: str | None = None,
        intent_id: str | None = None,
        now: datetime | None = None,
    ) -> Intent:
        intent = Intent(
            intent_id=intent_id or f"int-{uuid.uuid4().hex[:12]}",
            agent_id=agent_id,
            purpose=purpose,
            maximum_atomic=usd_to_atomic(maximum_usd),
            asset=asset,
            maximum_per_action_atomic=(
                usd_to_atomic(maximum_per_action_usd)
                if maximum_per_action_usd is not None
                else None
            ),
            allowed_resources=tuple(allowed_resources),
            allowed_categories=tuple(allowed_categories),
            allowed_channels=tuple(allowed_channels),
            expected_outcome=expected_outcome,
            created_at=now or datetime.now(timezone.utc),
            expires_at=expires_at,
            declared_by=declared_by,
        )
        data = self._load()
        data[intent.intent_id] = intent.as_dict()
        self._save(data)
        return intent

    def get(self, intent_id: str) -> Intent | None:
        raw = self._load().get(intent_id)
        return Intent.from_dict(raw) if raw else None

    def active_for(self, agent_id: str, now: datetime | None = None) -> Intent | None:
        """The agent's live intent, newest first.

        Returns None rather than raising when there is none — an agent without a
        declared intent is ungoverned by intent, not in error.
        """
        moment = now or datetime.now(timezone.utc)
        candidates = [
            Intent.from_dict(raw)
            for raw in self._load().values()
            if raw.get("agentId") == agent_id
        ]
        live = [
            i
            for i in candidates
            if i.status == "active" and not i.expired_at(moment)
        ]
        live.sort(key=lambda i: i.created_at or moment, reverse=True)
        return live[0] if live else None

    def latest_for(self, agent_id: str) -> Intent | None:
        """The agent's most recent intent, whatever its status.

        Distinct from `active_for`, and the distinction matters: an agent that
        declared time-bounded authority and let it lapse must not be treated as one
        that never declared any. Used by the decision path so an expired intent is
        refused rather than ignored.
        """
        candidates = [
            Intent.from_dict(raw)
            for raw in self._load().values()
            if raw.get("agentId") == agent_id
        ]
        if not candidates:
            return None
        candidates.sort(
            key=lambda i: i.created_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return candidates[0]

    def all(self) -> list[Intent]:
        return [Intent.from_dict(raw) for raw in self._load().values()]

    def revoke(self, intent_id: str) -> bool:
        """Withdraw authority without deleting the record of it having been granted.

        `True` means this call revoked something. `False` means there was nothing to revoke --
        either no such intent, or one already revoked. Reporting `True` for a second revocation
        told a caller it had just withdrawn authority that was withdrawn long ago, which reads
        as an event where there was none.
        """
        data = self._load()
        if intent_id not in data:
            return False
        if data[intent_id].get("status") == "revoked":
            return False
        data[intent_id]["status"] = "revoked"
        self._save(data)
        return True


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate(intent: dict[str, Any]) -> tuple[bool, list[str]]:
    try:
        import jsonschema  # noqa: PLC0415
    except ImportError:
        return False, ["jsonschema is not installed; cannot validate"]

    validator = jsonschema.Draft202012Validator(load_schema())
    problems = [
        f"{'/'.join(str(p) for p in err.path) or '<root>'}: {err.message}"
        for err in sorted(validator.iter_errors(intent), key=lambda e: list(e.path))
    ]
    return (not problems), problems
