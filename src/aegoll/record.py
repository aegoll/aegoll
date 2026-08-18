"""AEGS Decision Record — the evidence one governed action produces.

The hash-chained journal already *was* this object informally. This makes it
formal: a versioned, schema-validated projection that any implementation could
emit, whatever engines it uses internally.

That distinction is the point of the whole standards track. `Decision` is AEGL's
internal type and will change as AEGL changes. `DecisionRecord` is the interface a
second implementation would have to satisfy, and it must be able to describe
controls AEGL does not have — AML, sanctions screening, fraud, behavioural
monitoring — without pretending to have run them.

## Absent, null and zero are three different things

The schema distinguishes them deliberately, because this project has already been
burned by conflating two of them. `consult()` once rendered an *unmeasured* vendor
history as `0`, and every advisor read it as the fact that the counterparty was a
stranger. An unmeasured value rendered as a zero is not a missing fact, it is a
wrong one, and neither a model nor an auditor can tell the difference.

So in a record:

* **key absent** — this implementation does not have that control at all
* **`assessed: false`** — it has the control and did not run it here
* **`score: null`** — it ran and the value is genuinely unknown
* **`score: 0`** — it ran and the answer is zero

## Why it is a projection, not a second write path

Building the record *from* the journal rather than alongside it means the record
cannot drift from what was actually journalled — and reading it back proves the
journal holds enough to reconstruct the decision, which is the property that makes
it evidence rather than a log.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from .domain import atomic_to_usd

AEGS_VERSION = "0.1"

#: Vendored package data — see `_schemas/PROVENANCE.txt`. The prototype resolved this
#: from `parents[2]`, which only worked inside the monorepo that had `aegs/` as a
#: sibling. An installed wheel has no siblings. See PLAN.md F-A1.
SCHEMA_PATH = Path(
    str(resources.files("aegoll") / "_schemas" / "decision-record-0.1.json")
).resolve()

#: Controls AEGL implements. Anything outside this set is *absent* from a record
#: rather than reported as un-run -- claiming to have a sanctions control that never
#: runs would overstate what this implementation is.
IMPLEMENTED_CONTROLS = ("trust", "risk", "roi")


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _amount(atomic: Any) -> str:
    """Money as a decimal string. Never a float, in the record or anywhere else."""
    try:
        return f"{atomic_to_usd(int(atomic)):.6f}"
    except (TypeError, ValueError):
        return "0.000000"


def _usd_str(value: Any) -> str:
    try:
        return f"{float(value):.6f}"
    except (TypeError, ValueError):
        return "0.000000"


#: A clamp records itself under `authorize`, because that is the engine that applied
#: it -- but the *control* that caused it is what a reader needs. Reporting every
#: clamp as "authorize" makes the field useless for exactly the refusals that matter:
#: an auditor cannot tell an intent mismatch from a sanctions bar, and a conformance
#: suite cannot score either without string-matching an implementation's private
#: reason codes.
#: Controls declared **dispositive** under AEGS-0.1-VERD-4a: their finding decides
#: attribution whenever it is present, whether or not it narrowed the verdict. The clause
#: requires this set and its precedence to be documented, and this is that documentation.
#:
#: `sanctions` is the only one, and the reason is the defect that produced it. A sanctions
#: bar could be recorded as a *policy* refusal: the clamp was written only when it changed
#: the verdict, so when a policy rule refused first the record showed no screening at all —
#: delete the screening and every record stayed identical. A counterparty being barred does
#: not become less true because a spending limit happened to bite first.
#:
#: Precedence: `sanctions` outranks every narrowing control. With one entry there is nothing
#: further to order, and a second entry would need its rank stated here.
DISPOSITIVE_CONTROLS = ("sanctions",)

CLAMP_ORIGIN = {
    "clamped_by_identity": "identity",
    "clamped_by_intent": "intent",
    "clamped_by_treasury": "treasury",
    "clamped_by_sanction": "sanctions",
    "clamped_by_risk": "risk",
}


def _deciding_engine(decision: dict[str, Any]) -> str:
    """Which control determined the verdict. AEGS-0.1-VERD-4 and VERD-4a.

    Read from the back: the last clamp applied is the one that set the final verdict, and it
    is more informative than the rule that preceded it. A REJECT with no attributable cause
    is not auditable evidence, which is why the schema requires this field -- and why a clamp
    resolves to the control that caused it rather than to the engine that applied it.

    Reading from the back also implements VERD-4a for free, and it is worth naming why.
    `sanctions` records its clamp *unconditionally*, so a sanctions finding is always the
    last `authorize` reason present and therefore always wins attribution -- even when the
    verdict was already REJECT and the clamp changed nothing. That is exactly what
    DISPOSITIVE_CONTROLS declares. The mechanism is incidental; the behaviour is required.
    """
    for reason in reversed(decision.get("reasons") or []):
        if reason.get("source") == "authorize" and reason.get("verdict"):
            return CLAMP_ORIGIN.get(reason.get("code", ""), "authorize")
    if not (decision.get("budget") or {}).get("ok", True):
        return "treasury"
    if "high_risk" in ((decision.get("risk") or {}).get("flags") or []):
        return "risk"
    return "policy"


def _assessment(payload: dict[str, Any] | None, *, score_key: str = "value") -> dict[str, Any]:
    """One control's finding, keeping 'did not run' distinct from 'scored zero'."""
    if payload is None:
        return {"assessed": False, "score": None}
    score = payload.get(score_key)
    return {
        "assessed": True,
        "score": float(score) if isinstance(score, (int, float)) else None,
        "indicators": list(payload.get("flags") or []),
    }


def _roi_assessment(roi: dict[str, Any] | None) -> dict[str, Any]:
    """ROI reports `unknown` honestly rather than inventing a ratio.

    `ratio: null` in the engine means no declared expected value existed. That must
    stay null in the record -- coercing it to 0 would assert the purchase was
    worthless, which is a different and much stronger claim.
    """
    if roi is None:
        return {"assessed": False, "score": None}
    ratio = roi.get("ratio")
    return {
        "assessed": True,
        "score": None,
        "level": None if ratio is None else f"{float(ratio):.2f}x",
        "detail": "no declared expected value for this resource" if ratio is None else None,
    }


def _actor(payload: dict[str, Any]) -> dict[str, Any]:
    """Who acted, as the record should carry it.

    `known: false` is the honest answer for an unregistered agent, and it is a
    different statement from a registered agent that happened to pass -- which is
    why it is recorded rather than left to be inferred from a missing key.
    """
    identity = payload.get("identity") or {}
    disclosed = identity.get("disclosed") or {}
    return {
        "known": bool(identity.get("known", False)),
        "status": identity.get("status"),
        "purpose": disclosed.get("purpose"),
        # Deliberately no controller or operator. The record is evidence about an
        # economic action, not a dossier on whoever owns the agent.
        "delegatedFrom": None,
    }


def from_audit_entry(
    entry: Any,
    *,
    implementation: dict[str, Any] | None = None,
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Project one journal entry into an AEGS Decision Record.

    Raises `ValueError` on an entry that carries no decision -- settlement updates
    are separate append-only entries and are folded in by `records_from_journal`.
    """
    payload = getattr(entry, "payload", {}) or {}
    decision = payload.get("decision")
    if not decision:
        raise ValueError("audit entry carries no decision; it is a settlement update")

    tx = payload.get("transaction") or {}
    vendor = payload.get("vendor") or {}
    settlement = payload.get("settlement") or {}
    entry_labels = {**(payload.get("labels") or {}), **(labels or {})}
    budget = decision.get("budget") or {}
    advice = settlement.get("advice") or {}

    final_verdict = settlement.get("finalVerdict") or decision.get("verdict")
    deterministic = decision.get("verdict")

    record: dict[str, Any] = {
        "aegsVersion": AEGS_VERSION,
        "decisionId": tx.get("id") or decision.get("requestId") or "",
        "agentId": payload.get("agent") or "",
        # Null means the agent declared none, not that the concept is unmodelled.
        # The engine emits an explicit `no_intent_declared` reason in that case, so
        # a reader can tell the two apart from the reasons rather than guessing.
        "intentId": (payload.get("intent") or {}).get("intentId"),
        "action": {
            "channel": "internal"
            if str(tx.get("resource", "")).startswith("llm:")
            else "external",
            "resource": str(tx.get("resource", "")),
            "amount": _usd_str(tx.get("amountUsd")),
            "asset": "USD" if str(tx.get("resource", "")).startswith("llm:") else "USDC",
            "purpose": tx.get("purpose"),
            "counterparty": {
                "id": vendor.get("id", ""),
                "name": vendor.get("name"),
                "sanctioned": vendor.get("sanctioned"),
            },
        },
        "decision": final_verdict,
        "authorization": {
            "decidingEngine": "advisor"
            if settlement.get("changed")
            else _deciding_engine(decision),
            "matchedRule": decision.get("matchedRule"),
            "deterministicVerdict": deterministic,
            "reasons": [
                {
                    "source": r.get("source", ""),
                    "code": r.get("code", ""),
                    "detail": r.get("detail", ""),
                    "verdict": r.get("verdict"),
                }
                for r in (decision.get("reasons") or [])
            ],
        },
        "budgetState": {
            "ok": bool(budget.get("ok", True)),
            "binding": budget.get("binding"),
            "envelopes": [
                {
                    "name": e.get("name", ""),
                    "window": e.get("window"),
                    "limit": _usd_str(e.get("limitUsd")),
                    "used": _usd_str(e.get("usedUsd")),
                    # A per-call cap's `used` is meaningless. Carrying the flag into
                    # the record stops a consumer re-making the mistake the cockpit
                    # made, where a cap rendered as a spent budget said the opposite
                    # of what it meant.
                    "cumulative": bool(e.get("cumulative", True)),
                }
                for e in (budget.get("envelopes") or [])
            ],
        }
        if budget
        else None,
        "actor": _actor(payload),
        "assessments": {
            "trust": _assessment(decision.get("trust")),
            "risk": _assessment(decision.get("risk")),
            "roi": _roi_assessment(decision.get("roi")),
        },
        "intelligence": {
            "consulted": bool(settlement.get("consulted")),
            "skipReason": settlement.get("skipReason") or None,
            "provider": advice.get("provider"),
            "model": advice.get("model"),
            "recommendation": advice.get("recommendation"),
            "confidence": advice.get("confidence"),
            "changedVerdict": bool(settlement.get("changed")) if settlement else None,
            "costUsd": settlement.get("advisorCostUsd"),
            "injectionSuspected": advice.get("injectionSuspected"),
        }
        if settlement
        else None,
        "policy": {
            "id": entry_labels.get("policy") or "default",
            "version": decision.get("policyHash") or "unknown",
        },
        "humanReview": None,
        "timestamp": getattr(entry, "at", "") or decision.get("decidedAt", ""),
        "latencyUs": decision.get("latencyUs"),
        "evidence": {
            "evidenceHash": getattr(entry, "entry_hash", ""),
            "chainSequence": getattr(entry, "seq", None),
            "previousHash": getattr(entry, "prev_hash", None),
            "decisionHash": decision.get("decisionHash"),
        },
        "settlement": None,
        "implementation": {
            "name": "aegoll",
            "version": AEGS_VERSION,
            "framework": entry_labels.get("framework"),
            "rail": "x402",
            **(implementation or {}),
        },
    }
    return record


def records_from_journal(
    entries: list[Any], *, labels: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Every decision in a journal as a record, with settlements folded in.

    Settlements and human overrides arrive as their own append-only entries keyed by
    request id -- the log is never edited in place -- so they are matched back onto
    the decision they refer to here.
    """
    updates: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        update = (getattr(entry, "payload", {}) or {}).get("settlement_update")
        if update and update.get("requestId"):
            updates.setdefault(update["requestId"], []).append(update)

    records = []
    for entry in entries:
        if not (getattr(entry, "payload", {}) or {}).get("decision"):
            continue
        record = from_audit_entry(entry, labels=labels)
        for update in updates.get(record["decisionId"], []):
            if update.get("type") == "human_override":
                record["humanReview"] = {
                    "type": "override",
                    "at": None,
                    "note": update.get("note"),
                    "overrodeVerdict": update.get("overrodeVerdict"),
                }
            elif "success" in update or "txHash" in update:
                record["settlement"] = {
                    "settled": True,
                    "success": bool(update.get("success")),
                    "reference": update.get("txHash"),
                    "amount": _amount(update.get("amountAtomic"))
                    if update.get("amountAtomic") is not None
                    else None,
                }
        records.append(record)
    return records


# --- validation -----------------------------------------------------------


#: The problem string `validate()` returns when it has no validator. Exported so a caller can
#: tell *this record is invalid* from *I could not check*, which are different answers and were
#: previously the same `False`.
NOT_VALIDATED = "jsonschema is not installed; cannot validate"


def can_validate() -> bool:
    """Whether schema validation is available at all.

    `jsonschema` is the `schema` extra, because the layer governs correctly without it. But that
    makes *not checked* a real state, and invariant 5 applies to a validator as much as to a
    control: `absent` is not `invalid`. A caller that cannot tell them apart will eventually
    report a conforming record as broken, or -- worse -- treat "could not check" as "checked".
    """
    try:
        import jsonschema  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


def validate(record: dict[str, Any]) -> tuple[bool, list[str]]:
    """Check a record against the AEGS schema. Returns (ok, problems).

    Any implementation can run this against its own output; that is what makes the
    schema an interoperability surface rather than this layer's internal format written
    down.

    **Fails closed when it cannot check.** With no validator installed the answer is
    `(False, [NOT_VALIDATED])` rather than `(True, [])`: reporting a record valid without having
    validated it would be the fail-open mistake that AEGS-0.1-PATH-3 forbids for assessors, and
    the reasoning is identical. Use `can_validate()` to tell the two `False`s apart -- or check
    for `NOT_VALIDATED` in the problems, which is why it is a named constant rather than a
    literal buried here.
    """
    if not can_validate():
        return False, [NOT_VALIDATED]

    import jsonschema  # noqa: PLC0415

    validator = jsonschema.Draft202012Validator(load_schema())
    problems = [
        f"{'/'.join(str(p) for p in err.path) or '<root>'}: {err.message}"
        for err in sorted(validator.iter_errors(record), key=lambda e: list(e.path))
    ]
    return (not problems), problems


def validate_all(records: list[dict[str, Any]]) -> tuple[int, list[str]]:
    """Validate a batch. Returns (valid_count, problems) with each problem located."""
    problems: list[str] = []
    valid = 0
    for i, record in enumerate(records):
        ok, issues = validate(record)
        if ok:
            valid += 1
        else:
            problems += [f"record {i} ({record.get('decisionId', '?')}): {p}" for p in issues]
    return valid, problems
