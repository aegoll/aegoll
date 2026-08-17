"""One report shape, four renderers.

`aegoll report`, `aegoll report --json`, the HTML page and the localhost read API all
answer the same four questions. Deciding the field names once, here, is the whole point of
this module — the alternative is deciding them four times by accident and then discovering
the CLI and the page disagree about what "spent" means.

The four questions, in the order an agent developer asks them at 2am:

1. **What will this policy do?** → `policy`
2. **How much is left, and which limit binds?** → `envelopes`
3. **Why did my agent stop?** → `decisions`, and above all `by_attributed_control`
4. **Can I trust this record?** → `chain`

`by_attributed_control` is the field that makes a report worth reading. Counts by verdict
tell you *what happened*; counts by attributed control tell you **what actually governed
this agent**, which is frequently not what the policy file's author expected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .domain import atomic_to_usd

#: How many decisions the decision stream carries by default. A report is read, not
#: archived — the journal is the archive, and `aegoll audit` reads it in full.
DEFAULT_LIMIT = 50


def _usd(atomic: int | None) -> str | None:
    """Money as a decimal string, or None when there is no figure.

    `None` and `"0.000000"` are different answers and both appear in a report: a limit that
    does not exist versus a limit with nothing left. Rendering the first as the second is
    the four-state bug in its most visible form.
    """
    return None if atomic is None else f"{atomic_to_usd(int(atomic)):.6f}"


@dataclass(frozen=True)
class EnvelopeView:
    """One limit and how much of it is left."""

    name: str
    window: str
    limit_usd: str | None
    used_usd: str | None
    headroom_usd: str | None
    #: False for a per-call ceiling. `per_transaction` never accumulates, so rendering it
    #: as "used of limit" beside the cumulative windows reads as "nothing was spent" when
    #: the concept simply does not apply.
    cumulative: bool = True
    #: True for the limit closest to biting. The single most useful thing on this panel.
    binding: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "window": self.window,
            "limitUsd": self.limit_usd,
            "usedUsd": self.used_usd,
            "headroomUsd": self.headroom_usd,
            "cumulative": self.cumulative,
            "binding": self.binding,
        }


@dataclass(frozen=True)
class DecisionView:
    """One decision, as a reader needs it: what, how much, and **which control decided**."""

    at: str
    verdict: str
    amount_usd: str | None
    vendor: str | None
    resource: str | None
    attributed_control: str | None
    reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "verdict": self.verdict,
            "amountUsd": self.amount_usd,
            "vendor": self.vendor,
            "resource": self.resource,
            "attributedControl": self.attributed_control,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ChainView:
    """The evidence chain's state, and the one thing it cannot prove."""

    entries: int
    valid: bool
    problems: tuple[str, ...] = ()

    #: Printed wherever `valid` is. A page or table reporting VALID without this overstates
    #: what a hash chain proves: any *prefix* of a valid chain is itself valid, so an agent
    #: that was refused can delete the refusal and the chain still verifies. Editing and
    #: middle-deletion **are** caught. The fix is an external anchor, not a note.
    caveat: str = (
        "a hash chain detects edits and middle-deletions, not truncation: any prefix of a "
        "valid chain is itself valid. Closing that needs an external anchor."
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "entries": self.entries,
            "valid": self.valid,
            "problems": list(self.problems),
            "caveat": self.caveat,
        }


@dataclass(frozen=True)
class Report:
    """Everything a reader needs, as plain data. **stable** — see docs/api-surface.md §3."""

    policy_name: str
    policy_hash: str
    policy_rules: int
    profile: str | None

    decisions_total: int
    settled: int
    spent_usd: str
    by_verdict: dict[str, int] = field(default_factory=dict)
    by_attributed_control: dict[str, int] = field(default_factory=dict)

    envelopes: dict[str, tuple[EnvelopeView, ...]] = field(default_factory=dict)
    decisions: tuple[DecisionView, ...] = ()
    pending_reviews: int = 0
    chain: ChainView | None = None

    aegoll_version: str = ""
    aegs_version: str = ""

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe, and the wire format for `--json` and the read API alike.

        Key order and number formatting are part of the contract: the HTML page and the
        localhost API render from this, and two renderers disagreeing about a field name is
        a bug that only shows up in the less-used one.
        """
        return {
            "policy": {
                "name": self.policy_name,
                "hash": self.policy_hash,
                "rules": self.policy_rules,
            },
            "profile": self.profile,
            "spend": {
                "decisions": self.decisions_total,
                "settled": self.settled,
                "spentUsd": self.spent_usd,
                "byVerdict": dict(self.by_verdict),
                "byAttributedControl": dict(self.by_attributed_control),
            },
            "envelopes": {
                channel: [e.as_dict() for e in views]
                for channel, views in self.envelopes.items()
            },
            "decisions": [d.as_dict() for d in self.decisions],
            "pendingReviews": self.pending_reviews,
            "chain": self.chain.as_dict() if self.chain else None,
            "versions": {
                "aegoll": self.aegoll_version,
                "aegs": self.aegs_version,
            },
        }


def _envelope_views(budget: Any) -> tuple[EnvelopeView, ...]:
    if budget is None:
        return ()
    return tuple(
        EnvelopeView(
            name=envelope.name,
            window=envelope.window,
            limit_usd=_usd(envelope.limit_atomic),
            used_usd=_usd(envelope.used_atomic) if envelope.cumulative else None,
            headroom_usd=_usd(envelope.headroom_atomic),
            cumulative=envelope.cumulative,
            binding=envelope.name == budget.binding,
        )
        for envelope in budget.envelopes
    )


def build(layer: Any, *, profile: str | None = None, limit: int = DEFAULT_LIMIT) -> Report:
    """Assemble a `Report` from a live layer.

    Read-only throughout. Reads the **journal** rather than the sqlite history for the
    decision stream, because the journal is the hash-chained record: a number that came
    from the verifiable artifact is worth more than the same number from a convenience
    table, and if they ever disagree the journal is right.
    """
    from . import __version__
    from .record import AEGS_VERSION
    from .domain import Channel

    summary = layer.summary()

    # Attribution comes from `record._deciding_engine`, not from a second implementation
    # here. It is the same projection AEGS-CONF scores, so a report and a conformance run
    # cannot disagree about which control decided — and if that logic is ever wrong, it is
    # wrong in one place.
    # `_usd_str` comes from the same module for the same reason: the journal stores
    # `amountUsd` as a JSON number, so a report has to render it back to a decimal string
    # exactly the way a Decision Record does, or the two disagree about the same payment.
    from .record import _deciding_engine, _usd_str

    entries = [e for e in layer.audit.entries() if e.payload.get("decision")]
    by_control: dict[str, int] = {}
    views: list[DecisionView] = []
    for entry in entries:
        decision = entry.payload["decision"]
        control = _deciding_engine(decision) or "unattributed"
        by_control[control] = by_control.get(control, 0) + 1
        transaction = entry.payload.get("transaction") or {}
        vendor = entry.payload.get("vendor") or {}
        reasons = decision.get("reasons") or []
        # The reason that carried the verdict, not merely the first one logged. A REJECT
        # whose displayed reason is an unrelated informational line is worse than no
        # reason at all, because it sends the reader to the wrong control.
        deciding = next(
            (r for r in reasons if r.get("source") == control and r.get("verdict")),
            next((r for r in reasons if r.get("verdict")), None),
        )
        views.append(
            DecisionView(
                at=entry.at,
                verdict=entry.verdict,
                amount_usd=(
                    _usd_str(transaction["amountUsd"])
                    if transaction.get("amountUsd") is not None
                    else None
                ),
                vendor=vendor.get("id"),
                resource=transaction.get("resource"),
                attributed_control=control,
                reason=(deciding or {}).get("detail"),
            )
        )

    envelopes: dict[str, tuple[EnvelopeView, ...]] = {}
    for channel in (Channel.EXTERNAL, Channel.INTERNAL):
        probe = layer.build_request(
            resource="/", amount_usd="0", vendor=_probe_vendor(), channel=channel
        )
        snapshot = layer.snapshot_for(probe)
        from .engines.economic import treasury as treasury_engine

        budget = treasury_engine.evaluate(
            probe, snapshot, layer.bundle.treasury_for(channel)
        )
        envelopes[channel.value] = _envelope_views(budget)

    ok, problems = layer.audit.verify()

    return Report(
        policy_name=layer.bundle.name,
        policy_hash=layer.bundle.hash,
        policy_rules=len(layer.bundle.rules),
        profile=profile,
        decisions_total=len(entries),
        settled=summary["settled"],
        spent_usd=_usd(summary["spentAtomic"]) or "0.000000",
        by_verdict=dict(summary["verdicts"]),
        by_attributed_control=by_control,
        envelopes=envelopes,
        decisions=tuple(reversed(views[-limit:])),  # newest first: the question is "why did it just stop"
        pending_reviews=summary["pendingReviews"],
        chain=ChainView(entries=len(layer.audit.entries()), valid=ok, problems=tuple(problems)),
        aegoll_version=__version__,
        aegs_version=AEGS_VERSION,
    )


def _probe_vendor() -> Any:
    """A zero-amount, no-name counterparty used only to read envelope state.

    Nothing is decided or journalled for it. Building a request is how the treasury engine
    is asked "where do the envelopes stand", and a probe keeps that read-only.
    """
    from .domain import Vendor

    return Vendor(id="__probe__", name="")


__all__ = [
    "DEFAULT_LIMIT",
    "ChainView",
    "DecisionView",
    "EnvelopeView",
    "Report",
    "build",
]
