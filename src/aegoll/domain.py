"""Core value types.

Two rules hold everywhere in this package:

1. **Money is integer atomic units.** USDC has 6 decimals, so $0.001 is 1000.
   No float ever touches the money path -- floats make budget arithmetic
   non-associative, and a governance layer that cannot add up is worthless.
2. **Engines take structured values, never prose.** This is ADR-003 and it is the
   reason Phase 1 is immune to prompt injection: a hostile service description
   cannot argue its way past an integer comparison.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from typing import Any

USDC_DECIMALS = 6
_ATOMIC = Decimal(10) ** USDC_DECIMALS


#: Refuse amounts beyond this. Chosen far above any plausible action and far below
#: anything that strains Decimal -- the point is to fail loudly on nonsense rather
#: than to express a policy, which is the treasury engine's job.
MAX_ATOMIC = 10**24


def usd_to_atomic(amount: str | int | float | Decimal) -> int:
    """`"0.001"` -> `1000`. Rounds half-up at the 6th decimal.

    Rejects negatives and absurd magnitudes, because both defeat every check
    downstream rather than being caught by one:

    * A **negative** amount inverts every envelope. `admits()` asks
      `amount <= headroom`, and any negative satisfies that, so a -$1000 request was
      approved by a layer whose entire purpose is refusing overspend. Found by
      RT-NUM-001.
    * An **absurd** magnitude raised `decimal.InvalidOperation` from inside the
      arithmetic, so a malformed request crashed the governance layer instead of
      being refused by it. Found by RT-NUM-004.

    Raising rather than returning a verdict is deliberate. A negative price is not a
    spending decision to be weighed; it is not money, and a layer that quietly
    normalised it would be inventing an intent the caller never expressed.
    """
    try:
        d = Decimal(str(amount))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{amount!r} is not a usable amount") from exc

    if not d.is_finite():
        raise ValueError(f"{amount!r} is not a finite amount")
    if d < 0:
        raise ValueError(
            f"negative amount {amount!r}: an economic action moves value out, and a "
            "negative would satisfy every budget check by inverting it"
        )
    try:
        atomic = int((d * _ATOMIC).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except InvalidOperation as exc:
        raise ValueError(f"amount {amount!r} is too large to represent") from exc
    if atomic > MAX_ATOMIC:
        raise ValueError(
            f"amount {amount!r} exceeds the largest representable action "
            f"({MAX_ATOMIC} atomic units)"
        )
    return atomic


def atomic_to_usd(atomic: int) -> Decimal:
    """`1000` -> `Decimal("0.001")`."""
    return (Decimal(int(atomic)) / _ATOMIC).quantize(Decimal("0.000001"))


def fmt_usd(atomic: int) -> str:
    return f"${atomic_to_usd(atomic):.6f}"


class Verdict(str, Enum):
    """The authorization engine's output (the original AEGL design, docs/archive/2026-08-aegoll-original-research-prompt.md section 6)."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REVIEW = "REVIEW"      # pausable: queue it, a human answers later
    ESCALATE = "ESCALATE"  # blocking: the agent cannot proceed at all

    @property
    def is_terminal_denial(self) -> bool:
        return self in (Verdict.REJECT, Verdict.ESCALATE)


# Ordered most permissive -> most restrictive. Used to clamp: a policy rule may
# never widen a verdict that treasury or risk already narrowed.
_SEVERITY = {Verdict.APPROVE: 0, Verdict.REVIEW: 1, Verdict.ESCALATE: 2, Verdict.REJECT: 3}


def narrower(a: Verdict, b: Verdict) -> Verdict:
    """Return whichever verdict is more restrictive."""
    return a if _SEVERITY[a] >= _SEVERITY[b] else b


class Tier(str, Enum):
    """How much intelligence a decision requires.

    Phase 1 always resolves to NONE. SMALL and LARGE exist so the EIAP can record
    what Phase 2 *would* have done without Phase 1 being able to act on it.
    """

    NONE = "none"
    SMALL = "small"
    LARGE = "large"


class Channel(str, Enum):
    """Which kind of spend this is. Both flow through AEGL; neither shares a budget.

    * `INTERNAL` -- the agent buying its own *thinking*: LLM tokens, billed to an
      Anthropic API key in real dollars.
    * `EXTERNAL` -- the agent buying *data or services* from a third party over
      x402, settled in USDC on chain.

    They are deliberately separate envelopes. They are different currencies, paid
    to different counterparties, with different failure modes -- an agent that has
    burned its token budget should still be able to settle a payment it already
    committed to, and one that has exhausted its USDC should still be able to
    explain why. Sharing a budget would couple those.
    """

    INTERNAL = "internal"
    EXTERNAL = "external"

    @property
    def label(self) -> str:
        return (
            "internal (LLM tokens, API key, real USD)"
            if self is Channel.INTERNAL
            else "external (data via x402, USDC)"
        )


class Purpose(str, Enum):
    """Why the agent wants to spend. A closed vocabulary, by design."""

    DATA_PURCHASE = "data_purchase"
    COMPUTE = "compute"
    SERVICE = "service"
    SUBSCRIPTION = "subscription"
    INFERENCE = "inference"  # internal channel: the agent paying to think
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Vendor:
    id: str
    name: str = ""
    address: str | None = None
    sanctioned: bool = False
    tags: tuple[str, ...] = ()

    @property
    def display(self) -> str:
        return self.name or self.id


@dataclass(frozen=True)
class PaymentRequest:
    """A request to spend, before any decision has been made."""

    id: str
    agent_id: str
    vendor: Vendor
    resource: str
    amount_atomic: int
    purpose: Purpose = Purpose.UNKNOWN
    channel: Channel = Channel.EXTERNAL
    # Operator-declared expected value. `None` means genuinely unknown, which the
    # ROI engine reports honestly rather than inventing a number for.
    expected_value_atomic: int | None = None
    value_confidence: float | None = None
    quoted_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def amount_usd(self) -> Decimal:
        return atomic_to_usd(self.amount_atomic)


@dataclass(frozen=True)
class Reason:
    """Why a decision came out the way it did. Every verdict carries these."""

    source: str  # treasury | policy | risk | trust | roi | authorize
    code: str
    detail: str
    verdict: Verdict | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "code": self.code,
            "detail": self.detail,
            "verdict": self.verdict.value if self.verdict else None,
        }


@dataclass(frozen=True)
class Term:
    """One named, weighted contribution to a score.

    Scores are built from these rather than computed inline so the cockpit can
    show *why* a number is what it is. An unexplainable risk score is useless in
    an audit.
    """

    name: str
    value: float       # normalised 0..1
    weight: float
    detail: str = ""

    @property
    def contribution(self) -> float:
        return self.value * self.weight

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": round(self.value, 4),
            "weight": self.weight,
            "contribution": round(self.contribution, 4),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Score:
    """A 0..1 score plus the terms that produced it."""

    value: float
    terms: tuple[Term, ...] = ()
    flags: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": round(self.value, 4),
            "terms": [t.as_dict() for t in self.terms],
            "flags": list(self.flags),
        }


@dataclass(frozen=True)
class Envelope:
    """One budget constraint and how much of it is left."""

    name: str
    limit_atomic: int
    used_atomic: int
    window: str = ""
    #: False for a *per-call cap* -- a limit that never accumulates, checked
    #: fresh against each request. `per_transaction` is one: its `used` is
    #: permanently zero, so rendering it as "used of limit" alongside the
    #: cumulative windows reads as "nothing was spent" when in fact the concept
    #: does not apply. UIs use this to display it as a ceiling instead.
    cumulative: bool = True

    @property
    def headroom_atomic(self) -> int:
        return max(0, self.limit_atomic - self.used_atomic)

    def admits(self, amount_atomic: int) -> bool:
        return amount_atomic <= self.headroom_atomic

    @property
    def utilisation(self) -> float:
        if self.limit_atomic <= 0:
            return 1.0
        return min(1.0, self.used_atomic / self.limit_atomic)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "window": self.window,
            "limitUsd": float(atomic_to_usd(self.limit_atomic)),
            "usedUsd": float(atomic_to_usd(self.used_atomic)),
            "cumulative": self.cumulative,
            "headroomUsd": float(atomic_to_usd(self.headroom_atomic)),
            "utilisation": round(self.utilisation, 4),
        }


@dataclass(frozen=True)
class CountEnvelope:
    """A velocity constraint: N transactions per window."""

    name: str
    limit: int
    used: int
    window: str = ""

    @property
    def headroom(self) -> int:
        return max(0, self.limit - self.used)

    def admits(self) -> bool:
        return self.used < self.limit

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "window": self.window,
            "limit": self.limit,
            "used": self.used,
            "headroom": self.headroom,
        }


@dataclass(frozen=True)
class BudgetVerdict:
    ok: bool
    envelopes: tuple[Envelope, ...] = ()
    counters: tuple[CountEnvelope, ...] = ()
    binding: str | None = None
    reasons: tuple[Reason, ...] = ()

    @property
    def headroom_atomic(self) -> int:
        """Headroom of the tightest money envelope."""
        if not self.envelopes:
            return 0
        return min(e.headroom_atomic for e in self.envelopes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "binding": self.binding,
            "headroomUsd": float(atomic_to_usd(self.headroom_atomic)),
            "envelopes": [e.as_dict() for e in self.envelopes],
            "counters": [c.as_dict() for c in self.counters],
        }


@dataclass(frozen=True)
class RoiEstimate:
    known: bool
    expected_value_atomic: int | None
    cost_atomic: int
    confidence: float | None = None

    @property
    def ratio(self) -> float | None:
        if not self.known or self.expected_value_atomic is None or self.cost_atomic <= 0:
            return None
        return self.expected_value_atomic / self.cost_atomic

    @property
    def justified(self) -> bool | None:
        r = self.ratio
        return None if r is None else r >= 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "known": self.known,
            "expectedValueUsd": (
                float(atomic_to_usd(self.expected_value_atomic))
                if self.expected_value_atomic is not None
                else None
            ),
            "costUsd": float(atomic_to_usd(self.cost_atomic)),
            "ratio": round(self.ratio, 4) if self.ratio is not None else None,
            "justified": self.justified,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class EiapEvaluation:
    """Economic Intelligence Activation Policy.

    Phase 1 computes this on every transaction and acts on none of it. The point
    is to calibrate Phase 2's thresholds against real traffic before spending a
    single token.
    """

    exposure_atomic: int
    uncertainty: float          # 0..1
    p_flip: float               # P(AI analysis flips a wrong decision to right)
    expected_gain_atomic: int
    ai_cost_atomic: int
    break_even_exposure_atomic: int
    would_invoke: bool
    would_tier: Tier
    terms: tuple[Term, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "exposureUsd": float(atomic_to_usd(self.exposure_atomic)),
            "uncertainty": round(self.uncertainty, 4),
            "pFlip": round(self.p_flip, 4),
            "expectedGainUsd": float(atomic_to_usd(self.expected_gain_atomic)),
            "aiCostUsd": float(atomic_to_usd(self.ai_cost_atomic)),
            "breakEvenExposureUsd": float(atomic_to_usd(self.break_even_exposure_atomic)),
            "wouldInvoke": self.would_invoke,
            "wouldTier": self.would_tier.value,
            "terms": [t.as_dict() for t in self.terms],
        }


@dataclass(frozen=True)
class Intelligence:
    """What intelligence the decision used, and what it would have used.

    `required` is always NONE in Phase 1 -- enforced by a test asserting no model
    client is importable from this package.
    """

    required: Tier
    would_escalate: bool
    eiap: EiapEvaluation

    def as_dict(self) -> dict[str, Any]:
        return {
            "required": self.required.value,
            "wouldEscalate": self.would_escalate,
            "eiap": self.eiap.as_dict(),
        }


@dataclass(frozen=True)
class Decision:
    request_id: str
    verdict: Verdict
    reasons: tuple[Reason, ...]
    trust: Score
    risk: Score
    roi: RoiEstimate
    budget: BudgetVerdict
    intelligence: Intelligence
    matched_rule: str | None
    policy_hash: str
    decision_hash: str
    decided_at: datetime
    latency_us: float = 0.0

    @property
    def approved(self) -> bool:
        return self.verdict is Verdict.APPROVE

    def explain(self) -> list[str]:
        return [f"[{r.source}/{r.code}] {r.detail}" for r in self.reasons]

    def as_dict(self) -> dict[str, Any]:
        return {
            "requestId": self.request_id,
            "verdict": self.verdict.value,
            "matchedRule": self.matched_rule,
            "reasons": [r.as_dict() for r in self.reasons],
            "trust": self.trust.as_dict(),
            "risk": self.risk.as_dict(),
            "roi": self.roi.as_dict(),
            "budget": self.budget.as_dict(),
            "intelligence": self.intelligence.as_dict(),
            "policyHash": self.policy_hash,
            "decisionHash": self.decision_hash,
            "decidedAt": self.decided_at.isoformat(),
            "latencyUs": round(self.latency_us, 1),
        }
