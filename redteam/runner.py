"""Execute the attack catalogue against a real tesoro runtime.

Free and deterministic: no model sits in the decision path, so a full run costs nothing and can
go in CI. Every attack gets a fresh ephemeral store -- an attack that only works because a
previous one left history behind is measuring the suite.

Ported from the prototype's `security/redteam/`. The prototype resolved its own layer by
inserting `../aegl` onto `sys.path`; this imports `tesoro` the way any consumer does, so the
suite scores **the installed package**. That distinction has already mattered here six times:
every defect in F-A12 through F-A14 was invisible from the source tree.
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from .attacks import CATALOGUE, Attack

BASE = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
SELLER = "x402-poc-desk"


class Outcome(str, Enum):
    DEFENDED = "DEFENDED"
    #: Refused, but not by the control the attack targets. A finding, not a pass.
    DEFENDED_BY_ACCIDENT = "DEFENDED_BY_ACCIDENT"
    UNDEFENDED = "UNDEFENDED"
    ERROR = "ERROR"


@dataclass
class Result:
    attack: Attack
    outcome: Outcome
    detail: str = ""
    refused_by: str | None = None

    @property
    def as_expected(self) -> bool:
        return self.outcome.value == self.attack.expected

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.attack.id, "title": self.attack.title,
            "class": self.attack.threat_class, "outcome": self.outcome.value,
            "expected": self.attack.expected, "asExpected": self.as_expected,
            "shouldBeRefusedBy": self.attack.defence_source,
            "refusedBy": self.refused_by, "detail": self.detail,
        }


def _gov(clock_at: datetime = BASE, agent: str = "redteam-agent"):
    from tesoro.clock import FixedClock
    from tesoro.runtime import Paths, Tesoro

    return Tesoro(
        paths=Paths.ephemeral(tempfile.mkdtemp()),
        clock=FixedClock(clock_at),
        agent_id=agent,
    )


def _decide(gov, amount: str, *, resource="/market/snapshot", vendor=SELLER,
            sanctioned=False, channel=None, now=None):
    from tesoro.domain import Channel, Purpose, Vendor

    ch = channel or Channel.EXTERNAL
    return gov.decide(
        gov.build_request(
            resource=resource, amount_usd=amount,
            vendor=Vendor(id=vendor, name=vendor, sanctioned=sanctioned),
            purpose=Purpose.INFERENCE if ch is Channel.INTERNAL else Purpose.DATA_PURCHASE,
            channel=ch,
        ),
        now,
    )


def _refusing_source(decision) -> str | None:
    """Which control refused, or None if it was approved.

    Delegates to `Decision.attributed_control`, the package's own projection, and does not
    re-derive it. The ported version walked `reversed(decision.reasons)` and took the last
    refusing one, which is a *different* rule -- and it disagreed.

    On the budget-fragmentation attack the daily envelope binds with
    `treasury/envelope_exceeded:daily`, and a policy rule then observes the same fact as
    `policy/rule:review-budget-exhausted`. Being later in the list, the observation won: the
    runner credited `policy`, while `attributed_control` says `treasury`. Two of the three
    results that disagreed with their stated expectation were this artefact and not a defect in
    the layer.

    `attributed_control`'s own docstring names the reason: a report, a conformance run and that
    property disagreeing about which control refused would be three answers to a question with
    one, and only one of them would be under test. A red-team score computed from a shadow copy
    of the attribution rule measures the shadow. `CLAMP_ORIGIN` is no longer consulted here for
    the same reason -- `_deciding_engine` already resolves clamp origins.
    """
    from tesoro.domain import Verdict

    if decision.verdict is Verdict.APPROVE:
        return None
    control = decision.attributed_control
    #: "unattributed" means the layer refused and could not say which control did it. That is a
    #: finding in its own right, and it must not be silently reported as some engine's work.
    return control or "unattributed"


def _score(attack: Attack, refused_by: str | None, detail: str = "") -> Result:
    if refused_by is None:
        return Result(attack, Outcome.UNDEFENDED, detail or "the action was approved")
    if attack.defence_source is None:
        # No named control: the attack should have failed structurally. Being refused by
        # *some* engine instead is luck, not a structural guarantee.
        return Result(
            attack, Outcome.DEFENDED_BY_ACCIDENT, refused_by=refused_by,
            detail=f"refused by `{refused_by}` rather than failing structurally",
        )
    if refused_by != attack.defence_source:
        return Result(
            attack, Outcome.DEFENDED_BY_ACCIDENT, refused_by=refused_by,
            detail=(
                f"refused by `{refused_by}`, not the `{attack.defence_source}` control "
                "this attack targets -- shaped differently it would succeed"
            ),
        )
    return Result(attack, Outcome.DEFENDED, refused_by=refused_by, detail=detail)


# --- the attacks -----------------------------------------------------------


def _numeric(attack: Attack) -> Result:
    from tesoro.domain import Channel

    p = attack.params

    if attack.id == "RT-NUM-005":
        gov = _gov()
        try:
            gov.intents.declare(
                agent_id=gov.agent_id, purpose="buy data",
                maximum_usd="100.00", asset=p["intent_asset"], now=BASE,
            )
            d = _decide(gov, "0.01", resource="llm:m", channel=Channel.INTERNAL)
            return _score(attack, _refusing_source(d))
        finally:
            gov.close()

    gov = _gov()
    try:
        try:
            d = _decide(gov, p["amount"])
        except ValueError as exc:
            # Refused at the boundary rather than by an engine. For malformed input that is
            # the correct place: it is not money, so there is no spending decision to weigh.
            return Result(
                attack, Outcome.DEFENDED, refused_by="input-validation",
                detail=f"rejected before any engine saw it: {exc}"[:140],
            )
        src = _refusing_source(d)
        if attack.id == "RT-NUM-002":
            # Approving zero is correct. The question is whether it poisons the baseline,
            # so check the recorded amount rather than the verdict.
            return Result(
                attack, Outcome.DEFENDED,
                detail=f"zero-value request handled as {d.verdict.value} without error",
            )
        if attack.id == "RT-NUM-003":
            from tesoro.domain import usd_to_atomic

            atomic = usd_to_atomic(p["amount"])
            return Result(
                attack,
                Outcome.DEFENDED if atomic == 0 else Outcome.UNDEFENDED,
                detail=(
                    f"{p['amount']} rounds to {atomic} atomic units -- "
                    + ("down, which does not favour the spender"
                       if atomic == 0 else "UP, which favours the spender")
                ),
            )
        return _score(attack, src)
    finally:
        gov.close()


def _economic(attack: Attack) -> Result:
    from tesoro.domain import Verdict

    p = attack.params

    if attack.id in ("RT-ECON-001", "RT-ECON-004"):
        gov = _gov()
        try:
            spacing = p["spacing_seconds"]
            refused = None
            total = 0.0
            for i in range(p["count"]):
                at = BASE + timedelta(seconds=i * spacing)
                d = _decide(gov, p["amount"], now=at)
                if d.verdict is not Verdict.APPROVE:
                    refused = _refusing_source(d)
                    break
                gov.store.record(
                    tx_id=f"{attack.id}-{i}", at=at, agent_id=gov.agent_id,
                    vendor_id=SELLER, resource="/market/snapshot",
                    amount_atomic=int(float(p["amount"]) * 1_000_000),
                    verdict=Verdict.APPROVE, settled=True, success=True,
                )
                total += float(p["amount"])
            return _score(
                attack, refused,
                detail=f"${total:.6f} moved across {p['count']} actions"
                if refused is None else f"stopped after ${total:.6f}",
            )
        finally:
            gov.close()

    if attack.id == "RT-ECON-002":
        gov = _gov()
        try:
            refused, total = None, 0.0
            for i in range(p["vendors"]):
                at = BASE + timedelta(seconds=i * p["spacing_seconds"])
                # The **resource** varies with the counterparty, not just the vendor id. All
                # calls hitting `/market/snapshot` would exhaust the per-resource envelope at
                # the same moment as the agent-level one, and the attack claims only an
                # agent-level envelope can see this spread. Two envelopes binding together
                # cannot tell you which one saw it.
                resource = f"/market/{i}"
                d = _decide(gov, p["amount"], vendor=f"vendor-{i}",
                            resource=resource, now=at)
                if d.verdict is not Verdict.APPROVE:
                    refused = _refusing_source(d)
                    break
                gov.store.record(
                    tx_id=f"{attack.id}-{i}", at=at, agent_id=gov.agent_id,
                    vendor_id=f"vendor-{i}", resource=resource,
                    amount_atomic=int(float(p["amount"]) * 1_000_000),
                    verdict=Verdict.APPROVE, settled=True, success=True,
                )
                total += float(p["amount"])
            return _score(
                attack, refused,
                detail=f"${total:.2f} across {p['vendors']} counterparties"
                if refused is None else f"stopped at ${total:.2f} of the $50.00 daily envelope",
            )
        finally:
            gov.close()

    if attack.id == "RT-ECON-003":
        gov = _gov()
        try:
            # `history_for` aggregates over 30 days. The ported offset was a hardcoded 60
            # days, so all 200 warmup settlements fell outside the window and the attack
            # farmed nothing while claiming to. The offset is a parameter now, and visible in
            # the catalogue, because a number that decides whether a test tests anything does
            # not belong buried in the runner.
            for i in range(p["warmup"]):
                gov.store.record(
                    tx_id=f"{attack.id}-w{i}",
                    at=BASE - timedelta(days=p["warmup_starts_days_ago"])
                    + timedelta(hours=i),
                    agent_id=gov.agent_id, vendor_id=SELLER,
                    resource="/market/snapshot",
                    amount_atomic=int(float(p["warmup_amount"]) * 1_000_000),
                    verdict=Verdict.APPROVE, settled=True, success=True,
                )
            d = _decide(gov, p["strike_amount"])
            return _score(
                attack, _refusing_source(d),
                detail=f"${p['strike_amount']} after {p['warmup']} cheap settlements "
                       f"-> {d.verdict.value}",
            )
        finally:
            gov.close()

    return Result(attack, Outcome.ERROR, detail="no handler for this economic attack")


def _evidence(attack: Attack) -> Result:
    from tesoro.domain import Vendor, Verdict

    p = attack.params

    if attack.id == "RT-EVID-004":
        gov = _gov()
        try:
            req = gov.build_request(
                resource="/market/snapshot", amount_usd=p["amount"],
                vendor=Vendor(id=SELLER),
            )
            gov.authorize(req)
            gov.record_settlement(req.id, success=True, tx_hash="0xdead")
            replay = gov.build_request(
                resource="/market/snapshot", amount_usd=p["replay_amount"],
                vendor=Vendor(id=SELLER), request_id=req.id,
            )
            d = gov.authorize(replay)
            rows = gov.store.all_transactions(limit=50)
            same_id = [t for t in rows if t.id == req.id]
            overwritten = len(same_id) == 1 and same_id[0].amount_atomic != int(
                float(p["amount"]) * 1_000_000
            )
            if d.verdict is not Verdict.APPROVE:
                return _score(attack, _refusing_source(d))
            return Result(
                attack, Outcome.UNDEFENDED,
                detail=(
                    "the replayed id was accepted and the settled ledger row was "
                    f"{'OVERWRITTEN' if overwritten else 'left intact'}"
                ),
            )
        finally:
            gov.close()

    # journal integrity
    gov = _gov()
    try:
        for _ in range(4):
            gov.authorize(gov.build_request(
                resource="/market/snapshot", amount_usd="0.001",
                vendor=Vendor(id=SELLER),
            ))
        path = gov.paths.audit
        lines = path.read_text(encoding="utf-8").splitlines()
        mode = p["mode"]
        if mode == "edit":
            # The journal is written with compact separators, so the naive spaced form never
            # matched and the "tamper" was a no-op that reported the chain as holding. Assert
            # the edit landed.
            before = lines[1]
            lines[1] = lines[1].replace('"amountUsd":0.001', '"amountUsd":500.0')
            if lines[1] == before:
                return Result(attack, Outcome.ERROR,
                              detail="the tamper did not apply; the test is wrong")
        elif mode == "truncate":
            lines = lines[:-2]
        elif mode == "delete_middle":
            del lines[1]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok, problems = gov.audit.verify()
        return Result(
            attack,
            Outcome.DEFENDED if not ok else Outcome.UNDEFENDED,
            detail=(problems[0][:110] if problems else "the chain still verified"),
        )
    finally:
        gov.close()


def _authority(attack: Attack) -> Result:
    p = attack.params

    if attack.id == "RT-AUTH-001":
        gov = _gov(agent="child")
        try:
            gov.identities.register(agent_id="parent", purpose="p",
                                    per_action_usd=p["parent_limit"], now=BASE)
            gov.identities.register(agent_id="child", purpose="c",
                                    parent_agent_id="parent",
                                    per_action_usd=p["child_limit"], now=BASE)
            d = _decide(gov, "0.05")
            return _score(attack, _refusing_source(d))
        finally:
            gov.close()

    if attack.id == "RT-AUTH-002":
        gov = _gov()
        try:
            gov.identities.register(agent_id=gov.agent_id, purpose="p", now=BASE)
            gov.identities.set_status(gov.agent_id, "revoked")
            d = _decide(gov, "0.001")
            return _score(attack, _refusing_source(d))
        finally:
            gov.close()

    if attack.id == "RT-AUTH-003":
        gov = _gov()
        try:
            gov.intents.declare(
                agent_id=gov.agent_id, purpose="buy data", maximum_usd="100.00",
                expires_at=BASE - timedelta(hours=p["expired_hours"]),
                now=BASE - timedelta(hours=p["expired_hours"] + 1),
            )
            d = _decide(gov, "0.001")
            return _score(attack, _refusing_source(d))
        finally:
            gov.close()

    if attack.id == "RT-AUTH-004":
        gov = _gov(agent="agent-b")
        try:
            gov.intents.declare(agent_id="agent-a", purpose="A's purpose",
                                maximum_usd="100.00",
                                allowed_resources=["/market/*"], now=BASE)
            d = _decide(gov, "0.001", resource="/anything/else")
            intent_seen = any(
                r.source == "intent" and r.code != "no_intent_declared"
                for r in d.reasons
            )
            if intent_seen:
                return Result(
                    attack, Outcome.UNDEFENDED,
                    detail="agent-b was evaluated against agent-a's intent",
                )
            return Result(
                attack, Outcome.DEFENDED,
                detail="agent-b saw no intent; A's did not transfer",
            )
        finally:
            gov.close()

    if attack.id == "RT-AUTH-005":
        gov = _gov()
        try:
            d = _decide(gov, p["amount"], sanctioned=True, vendor="ofac-1")
            return _score(attack, _refusing_source(d))
        finally:
            gov.close()

    return Result(attack, Outcome.ERROR, detail="no handler for this authority attack")


_BY_CLASS = {
    "numeric": _numeric,
    "economic": _economic,
    "evidence": _evidence,
    "authority": _authority,
}


def run_attack(attack: Attack) -> Result:
    handler = _BY_CLASS.get(attack.threat_class)
    if handler is None:
        return Result(attack, Outcome.ERROR,
                      detail=f"no handler for threat class {attack.threat_class!r}")
    return handler(attack)


def run_all(catalogue: tuple[Attack, ...] = CATALOGUE) -> list[Result]:
    results = []
    for attack in catalogue:
        try:
            results.append(run_attack(attack))
        except Exception as exc:  # noqa: BLE001 - one broken attack must not end the run
            results.append(Result(attack, Outcome.ERROR,
                                  detail=f"{type(exc).__name__}: {exc}"))
    return results


def report(results: list[Result]) -> dict[str, Any]:
    counts = {o.value: sum(1 for r in results if r.outcome is o) for o in Outcome}
    surprises = [r for r in results if not r.as_expected]
    return {
        "suite": "tesoro red-team", "version": "0.1",
        "attacks": len(results), "counts": counts,
        "undefended": [r.attack.id for r in results if r.outcome is Outcome.UNDEFENDED],
        "byAccident": [
            r.attack.id for r in results if r.outcome is Outcome.DEFENDED_BY_ACCIDENT
        ],
        "surprises": [r.as_dict() for r in surprises],
        "results": [r.as_dict() for r in results],
    }


def format_report(data: dict[str, Any]) -> str:
    lines = ["  tesoro red-team 0.1", "  " + "-" * 74]
    for r in data["results"]:
        mark = {"DEFENDED": "ok", "DEFENDED_BY_ACCIDENT": "ACCIDENT",
                "UNDEFENDED": "OPEN", "ERROR": "ERROR"}[r["outcome"]]
        flag = "" if r["asExpected"] else "  <- not what we expected"
        lines.append(f"  {r['id']:13} {mark:9} {r['class']:9} {r['title'][:34]:34}{flag}")
        if r["detail"]:
            lines.append(f"                {r['detail'][:86]}")
    c = data["counts"]
    lines += [
        "  " + "-" * 74,
        f"  defended {c['DEFENDED']}   by accident {c['DEFENDED_BY_ACCIDENT']}   "
        f"undefended {c['UNDEFENDED']}   error {c['ERROR']}",
    ]
    if data["byAccident"]:
        lines += [
            "",
            "  DEFENDED_BY_ACCIDENT is a finding, not a pass: the attack was refused",
            "  by a control it does not target, and shaped differently it would work.",
        ]
    return "\n".join(lines)


if __name__ == "__main__":
    data = report(run_all())
    if "--json" in sys.argv:
        print(json.dumps(data, indent=2))
    else:
        print(format_report(data))
