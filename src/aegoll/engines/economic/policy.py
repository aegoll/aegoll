"""Engine 2 -- the policy rule engine.

Rules are declarative YAML evaluated in priority order; the first match is
terminal. Three constraints keep this auditable:

* **No arbitrary expressions.** A fixed comparator vocabulary only. There is no
  `eval`, so a policy file cannot become code.
* **Facts are structured.** The engine matches over a flat dict of typed values
  built from the request and the engine outputs -- never over free text.
* **Rules cannot widen.** Enforced in `authorize.py`: a rule may narrow a verdict
  that treasury or risk already set, never loosen it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...config import PolicyBundle, Rule
from ...store import HistorySnapshot
from ...domain import (
    BudgetVerdict,
    PaymentRequest,
    Reason,
    RoiEstimate,
    Score,
    Verdict,
)

COMPARATORS = ("eq", "ne", "lt", "lte", "gt", "gte", "in", "not_in", "between", "contains")


def build_facts(
    request: PaymentRequest,
    trust: Score,
    risk: Score,
    roi: RoiEstimate,
    budget: BudgetVerdict,
    snapshot: HistorySnapshot,
) -> dict[str, Any]:
    """The flat, typed fact base rules are matched against."""
    return {
        "amount_usd": float(request.amount_usd),
        "resource": request.resource,
        "purpose": request.purpose.value,
        "channel": request.channel.value,
        "agent_id": request.agent_id,
        "vendor.id": request.vendor.id,
        "vendor.sanctioned": request.vendor.sanctioned,
        "vendor.tags": list(request.vendor.tags),
        "vendor.is_new": snapshot.vendor.is_new,
        "vendor.settled_count": snapshot.vendor.settled_count,
        "trust.score": trust.value,
        "trust.flags": list(trust.flags),
        "risk.score": risk.value,
        "risk.flags": list(risk.flags),
        "roi.known": roi.known,
        "roi.ratio": roi.ratio,
        "roi.justified": roi.justified,
        "budget.ok": budget.ok,
        "budget.binding": budget.binding,
        "budget.headroom_usd": float(budget.headroom_atomic) / 1_000_000,
    }


def _compare(fact: Any, spec: Any) -> tuple[bool, str]:
    """Match one fact against one clause. Returns (matched, why-it-failed)."""
    if not isinstance(spec, dict):
        return (fact == spec, f"expected {spec!r}, got {fact!r}")

    for op, want in spec.items():
        if op not in COMPARATORS:
            raise ValueError(f"unknown comparator {op!r}; allowed: {COMPARATORS}")

        # A missing/None fact can never satisfy an ordering comparison. Treat it
        # as a non-match rather than raising -- `roi.ratio` is legitimately None.
        if fact is None and op not in ("eq", "ne", "in", "not_in"):
            return False, f"{op} against an unknown value"

        if op == "eq" and fact != want:
            return False, f"expected == {want!r}, got {fact!r}"
        if op == "ne" and fact == want:
            return False, f"expected != {want!r}"
        if op == "lt" and not fact < want:
            return False, f"expected < {want}, got {fact}"
        if op == "lte" and not fact <= want:
            return False, f"expected <= {want}, got {fact}"
        if op == "gt" and not fact > want:
            return False, f"expected > {want}, got {fact}"
        if op == "gte" and not fact >= want:
            return False, f"expected >= {want}, got {fact}"
        if op == "in" and fact not in want:
            return False, f"expected one of {want}, got {fact!r}"
        if op == "not_in" and fact in want:
            return False, f"expected not one of {want}"
        if op == "between":
            lo, hi = want
            if not (lo <= fact <= hi):
                return False, f"expected within [{lo}, {hi}], got {fact}"
        if op == "contains":
            if not isinstance(fact, (list, tuple, str)) or want not in fact:
                return False, f"expected to contain {want!r}"

    return True, ""


@dataclass(frozen=True)
class RuleEvaluation:
    rule: Rule
    matched: bool
    failed_clause: str | None = None
    failed_detail: str = ""


@dataclass(frozen=True)
class PolicyResult:
    verdict: Verdict
    matched: Rule | None
    reason: Reason
    evaluations: tuple[RuleEvaluation, ...]


def apply_derived(bundle: PolicyBundle, facts: dict[str, Any]) -> dict[str, Any]:
    """Add every `derived.<name>` fact to a **copy** of the fact base.

    Declaration order is evaluation order, so a derived fact may reference one declared
    before it and never one declared after. Cycles are therefore impossible rather than
    detected — there is no order in which a cycle could be written down.

    Returns a copy. `build_facts()` output stays exactly what the engines produced, which
    keeps "the facts a rule matched" and "the measurements the engines took" separable in
    a record.
    """
    if not bundle.derived:
        return facts

    out = dict(facts)
    for derived in bundle.derived:
        results = []
        for clause in derived.clauses:
            # A clause is a mapping, and every condition in it must hold -- the same
            # shape and the same semantics as a rule's `when`.
            results.append(all(_compare(out.get(k), spec)[0] for k, spec in clause.items()))
        if derived.combinator == "all":
            value = all(results)
        elif derived.combinator == "any":
            value = any(results)
        else:  # "not" -- the negation of the conjunction, so `not: [a, b]` is not(a and b)
            value = not all(results)
        out[f"derived.{derived.name}"] = value
    return out


def evaluate(bundle: PolicyBundle, facts: dict[str, Any]) -> PolicyResult:
    facts = apply_derived(bundle, facts)
    evaluations: list[RuleEvaluation] = []
    winner: Rule | None = None

    for rule in bundle.sorted_rules():
        if winner is not None:
            # Record remaining rules as unevaluated-but-listed so the cockpit can
            # show the whole ruleset in order.
            evaluations.append(RuleEvaluation(rule, False, None, "not reached"))
            continue

        matched, failed_clause, failed_detail = True, None, ""
        for key, spec in rule.when.items():
            ok, why = _compare(facts.get(key), spec)
            if not ok:
                matched, failed_clause, failed_detail = False, key, why
                break

        evaluations.append(RuleEvaluation(rule, matched, failed_clause, failed_detail))
        if matched:
            winner = rule

    if winner is None:
        # Only reachable if the bundle has no catch-all. Fail closed.
        return PolicyResult(
            verdict=Verdict.REVIEW,
            matched=None,
            reason=Reason(
                "policy",
                "no_rule_matched",
                "no rule matched and the bundle defines no catch-all; failing closed to REVIEW",
                Verdict.REVIEW,
            ),
            evaluations=tuple(evaluations),
        )

    verdict = Verdict(winner.then)
    return PolicyResult(
        verdict=verdict,
        matched=winner,
        reason=Reason(
            "policy",
            f"rule:{winner.id}",
            winner.reason or f"matched rule {winner.id}",
            verdict,
        ),
        evaluations=tuple(evaluations),
    )
