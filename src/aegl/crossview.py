"""What the layer did, across every framework that used it.

The three agents exist to prove AEGL is framework-neutral. That claim is only
checkable if you can see all three in one place — otherwise "the same layer
governs LangGraph, Google ADK and the Claude Agent SDK" is three separate
screenshots.

Everything here is derived from the **audit journal**, not the sqlite history, for
two reasons. The journal is hash-chained, so the numbers come from the record that
can be verified rather than a convenience table. And it carries `labels`, which is
where the producing framework is stamped — see `Governor(framework=...)`.

Deliberately not derived from `agent_id`: every treasury envelope is agent-scoped,
so labelling frameworks that way would split one shared budget into one per
framework. That is a real deployment choice, but it is not this one, and it should
never happen as a side effect of wanting a chart.

Pure data, no Streamlit — `app.py` renders it. That keeps the aggregation testable
without a browser.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

UNLABELLED = "unlabelled"


@dataclass
class FrameworkRollup:
    """Everything one framework did, as recorded."""

    framework: str
    providers: set[str] = field(default_factory=set)
    decisions: int = 0
    approved: int = 0
    refused: int = 0
    internal_usd: float = 0.0
    external_usd: float = 0.0
    advisor_calls: int = 0
    advisor_cost_usd: float = 0.0
    advisor_changed: int = 0
    ceiling_stops: int = 0
    engines: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    verdicts: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @property
    def approval_rate(self) -> float:
        return (self.approved / self.decisions) if self.decisions else 0.0

    @property
    def total_usd(self) -> float:
        return self.internal_usd + self.external_usd

    def as_dict(self) -> dict[str, Any]:
        return {
            "framework": self.framework,
            "providers": sorted(self.providers),
            "decisions": self.decisions,
            "approved": self.approved,
            "refused": self.refused,
            "approvalRate": round(self.approval_rate, 4),
            "internalUsd": round(self.internal_usd, 6),
            "externalUsd": round(self.external_usd, 6),
            "totalUsd": round(self.total_usd, 6),
            "advisorCalls": self.advisor_calls,
            "advisorCostUsd": round(self.advisor_cost_usd, 6),
            "advisorChanged": self.advisor_changed,
            "ceilingStops": self.ceiling_stops,
            "engines": dict(self.engines),
            "verdicts": dict(self.verdicts),
        }


def _deciding_engine(decision: dict[str, Any]) -> str:
    """Which engine determined the verdict, from a journalled decision.

    Same precedence the live panel uses: the most specific objection wins.
    """
    budget = decision.get("budget") or {}
    risk = decision.get("risk") or {}
    for reason in reversed(decision.get("reasons") or []):
        if reason.get("source") == "authorize" and reason.get("verdict"):
            return "authorize"
    if not budget.get("ok", True):
        return "treasury"
    if "high_risk" in (risk.get("flags") or []):
        return "risk"
    return "policy"


def rollup(entries: list[Any]) -> dict[str, FrameworkRollup]:
    """Group audit entries by the framework that produced them.

    `entries` are `AuditEntry` objects (or anything with `.payload`). Entries
    written before labels existed, or by a host that set none, land under
    `unlabelled` rather than being dropped — a view that silently omits history
    is worse than one that admits it does not know.
    """
    out: dict[str, FrameworkRollup] = {}

    # Settlement updates are separate append-only entries keyed by request id, so
    # they are collected first and attributed to their decision's framework.
    updates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        update = (getattr(entry, "payload", {}) or {}).get("settlement_update")
        if update and update.get("requestId"):
            updates[update["requestId"]].append(update)

    for entry in entries:
        payload = getattr(entry, "payload", {}) or {}
        decision = payload.get("decision")
        if not decision:
            continue  # a settlement update; counted via its decision below

        labels = payload.get("labels") or {}
        name = labels.get("framework") or UNLABELLED
        roll = out.setdefault(name, FrameworkRollup(framework=name))
        if labels.get("provider"):
            roll.providers.add(labels["provider"])

        settlement = payload.get("settlement") or {}
        verdict = settlement.get("finalVerdict") or decision.get("verdict", "?")
        tx = payload.get("transaction") or {}
        amount = float(tx.get("amountUsd") or 0.0)

        roll.decisions += 1
        roll.verdicts[verdict] += 1
        if verdict == "APPROVE":
            roll.approved += 1
        else:
            roll.refused += 1
            roll.engines[_deciding_engine(decision)] += 1

        # The channel is on the request, and `llm:` resources are the internal one.
        if str(tx.get("resource", "")).startswith("llm:"):
            roll.internal_usd += amount
        else:
            roll.external_usd += amount

        advice = settlement.get("advice")
        if advice:
            roll.advisor_calls += 1
            roll.advisor_cost_usd += float(advice.get("costUsd") or 0.0)
            if settlement.get("changed"):
                roll.advisor_changed += 1

        for update in updates.get(tx.get("id"), []):
            if update.get("type") == "spend_ceiling_stop":
                roll.ceiling_stops += 1

    return out


def count_overrides(entries: list[Any]) -> int:
    """Human overrides, counted globally rather than per framework.

    An override is journalled against a synthetic `override-<model>` request id
    that no decision carries, so there is nothing to attribute it to. Guessing a
    framework would be worse than reporting the total: the number that matters is
    "a human bypassed the policy N times", and inventing an owner for it would
    make the view look more certain than the record is.
    """
    return sum(
        1
        for entry in entries
        if ((getattr(entry, "payload", {}) or {}).get("settlement_update") or {}).get(
            "type"
        )
        == "human_override"
    )


def summarise(entries: list[Any]) -> dict[str, Any]:
    """The whole cross-framework picture, as plain data."""
    rolls = rollup(entries)
    frameworks = sorted(rolls.values(), key=lambda r: -r.decisions)
    return {
        "frameworks": [r.as_dict() for r in frameworks],
        "totals": {
            "frameworks": len([r for r in frameworks if r.framework != UNLABELLED]),
            "decisions": sum(r.decisions for r in frameworks),
            "refused": sum(r.refused for r in frameworks),
            "internalUsd": round(sum(r.internal_usd for r in frameworks), 6),
            "externalUsd": round(sum(r.external_usd for r in frameworks), 6),
            "advisorCalls": sum(r.advisor_calls for r in frameworks),
            "advisorCostUsd": round(sum(r.advisor_cost_usd for r in frameworks), 6),
            "ceilingStops": sum(r.ceiling_stops for r in frameworks),
            # Global, not per framework -- see `count_overrides`.
            "overrides": count_overrides(entries),
        },
        "unlabelled": rolls[UNLABELLED].decisions if UNLABELLED in rolls else 0,
    }


def recent(entries: list[Any], limit: int = 40) -> list[dict[str, Any]]:
    """The most recent decisions, newest first, with their framework."""
    rows: list[dict[str, Any]] = []
    for entry in reversed(entries):
        payload = getattr(entry, "payload", {}) or {}
        decision = payload.get("decision")
        if not decision:
            continue
        labels = payload.get("labels") or {}
        settlement = payload.get("settlement") or {}
        tx = payload.get("transaction") or {}
        resource = str(tx.get("resource", ""))
        rows.append(
            {
                "seq": getattr(entry, "seq", None),
                "at": getattr(entry, "at", "")[:19],
                "framework": labels.get("framework") or UNLABELLED,
                "provider": labels.get("provider") or "—",
                "channel": "internal" if resource.startswith("llm:") else "external",
                "resource": resource,
                "amountUsd": float(tx.get("amountUsd") or 0.0),
                "verdict": settlement.get("finalVerdict") or decision.get("verdict", "?"),
                "engine": _deciding_engine(decision),
                "rule": decision.get("matchedRule"),
                "advisor": bool(settlement.get("advice")),
            }
        )
        if len(rows) >= limit:
            break
    return rows
