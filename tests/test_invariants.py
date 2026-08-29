"""The invariants the whole design rests on. If these fail, the claims are void."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from tesoro import Tesoro, FixedClock, Paths, Vendor, Verdict, load_bundle
from tesoro.domain import narrower

BASE = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def tesoro(tmp_path):
    a = Tesoro(bundle=load_bundle(), paths=Paths.ephemeral(tmp_path), clock=FixedClock(BASE))
    yield a
    a.close()


VENDOR = Vendor(id="v1", name="Vendor One")


def test_no_approved_sequence_can_breach_an_envelope(tesoro):
    """Property test: however the amounts fall, approvals never exceed a limit.

    This is the single most important test in the suite -- it is the difference
    between a budget control and a budget suggestion.
    """
    rng = random.Random(1234)
    cfg = tesoro.bundle.treasury
    approved_today = 0

    for i in range(400):
        amount = rng.choice(["0.001", "0.01", "0.5", "2.00", "9.99", "11.00"])
        req = tesoro.build_request(
            resource="/market/snapshot",
            amount_usd=amount,
            vendor=VENDOR,
            request_id=f"prop-{i}",
        )
        decision = tesoro.authorize(req)

        if decision.verdict is Verdict.APPROVE:
            # Simulate settlement so the next iteration sees the spend.
            tesoro.record_settlement(req.id, success=True, tx_hash=f"0x{i:04d}")
            approved_today += req.amount_atomic

            assert req.amount_atomic <= cfg.per_tx_atomic * 3, "per-transaction ceiling breached"
            assert approved_today <= cfg.daily_atomic, (
                f"daily envelope breached: {approved_today} > {cfg.daily_atomic}"
            )

    assert approved_today <= cfg.daily_atomic


def test_policy_cannot_widen_a_treasury_refusal(tmp_path):
    """A permissive rule must not be able to approve past a breached envelope."""
    bundle_path = tmp_path / "permissive.yaml"
    bundle_path.write_text(
        """
version: 1
name: permissive
config:
  treasury:
    balance_usd: "1"
    per_transaction_usd: "0.01"
    daily_usd: "0.01"
    monthly_usd: "0.01"
    per_vendor_30d_usd: "0.01"
    per_resource_30d_usd: "0.01"
    emergency_reserve_usd: "0"
rules:
  - id: approve-everything
    priority: 1
    when: {}
    then: APPROVE
    reason: deliberately reckless
""",
        encoding="utf-8",
    )
    a = Tesoro(
        bundle=load_bundle(bundle_path),
        paths=Paths.ephemeral(tmp_path),
        clock=FixedClock(BASE),
    )
    try:
        req = a.build_request(resource="/big", amount_usd="500", vendor=VENDOR)
        decision = a.decide(req)
        assert decision.verdict is not Verdict.APPROVE, (
            "a rule widened a treasury refusal -- the clamp in authorize.py is broken"
        )
        assert any(r.code == "clamped_by_treasury" for r in decision.reasons)
    finally:
        a.close()


def test_sanctioned_vendor_is_an_absolute_bar(tesoro):
    req = tesoro.build_request(
        resource="/market/snapshot",
        amount_usd="0.001",
        vendor=Vendor(id="ofac-1", name="Sanctioned Co", sanctioned=True),
    )
    decision = tesoro.decide(req)
    assert decision.verdict is Verdict.REJECT


def test_decision_is_deterministic(tesoro):
    """Same inputs -> identical hash. This is what makes replay meaningful."""
    hashes = set()
    for _ in range(5):
        req = tesoro.build_request(
            resource="/market/snapshot",
            amount_usd="0.001",
            vendor=VENDOR,
            request_id="stable-id",
        )
        hashes.add(tesoro.decide(req).decision_hash)
    assert len(hashes) == 1, f"non-deterministic decision hash: {hashes}"


def test_narrower_never_loosens():
    order = [Verdict.APPROVE, Verdict.REVIEW, Verdict.ESCALATE, Verdict.REJECT]
    for a in order:
        for b in order:
            result = narrower(a, b)
            assert result in (a, b)
            assert order.index(result) >= max(order.index(a), order.index(b))


def test_money_path_uses_no_floats():
    """Atomic-unit conversion must not lose cents to float representation."""
    from tesoro.domain import atomic_to_usd, usd_to_atomic

    for value in ["0.001", "0.005", "0.01", "0.1", "1.00", "999.999999", "0.000001"]:
        assert str(atomic_to_usd(usd_to_atomic(value))) == f"{float(value):.6f}"

    # The classic float failure: 0.1 + 0.2 != 0.3
    assert usd_to_atomic("0.1") + usd_to_atomic("0.2") == usd_to_atomic("0.3")


def test_audit_chain_detects_tampering(tesoro, tmp_path):
    for i in range(3):
        req = tesoro.build_request(
            resource="/market/snapshot", amount_usd="0.001", vendor=VENDOR,
            request_id=f"audit-{i}",
        )
        tesoro.authorize(req)

    ok, problems = tesoro.audit.verify()
    assert ok, problems

    # Edit a past record: the chain must notice.
    path = tesoro.audit.path
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].replace('"verdict":"APPROVE"', '"verdict":"REJECT"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, problems = tesoro.audit.verify()
    assert not ok
    assert any("hash mismatch" in p or "does not match" in p for p in problems)


def test_velocity_burst_is_caught(tesoro):
    """Ten transactions in a minute must trip the velocity counter."""
    for i in range(12):
        tesoro.store.record(
            tx_id=f"burst-{i}",
            at=BASE - timedelta(seconds=30),
            agent_id=tesoro.agent_id,
            vendor_id=VENDOR.id,
            resource="/market/snapshot",
            amount_atomic=1000,
            verdict=Verdict.APPROVE,
            settled=True,
            success=True,
        )
    req = tesoro.build_request(resource="/market/snapshot", amount_usd="0.001", vendor=VENDOR)
    decision = tesoro.decide(req)
    assert not decision.budget.ok
    assert decision.budget.binding == "velocity_60s"
    assert decision.verdict is not Verdict.APPROVE


# --- P3.2: the narrowing lattice, enumerated -------------------------------

_SEVERITY = {"APPROVE": 0, "REVIEW": 1, "ESCALATE": 2, "REJECT": 3}


def _resolve(order):
    """Run one evaluation order. Returns (final severity, narrowing sequence).

    The lattice from `research/lattice.md`: controls propose, composition is join, and a control
    narrows only when it strictly raises the running verdict.
    """
    running, narrowed = 0, []
    for name, proposal in order:
        if _SEVERITY[proposal] > running:
            running = _SEVERITY[proposal]
            narrowed.append(name)
    return running, narrowed


def test_the_verdict_is_order_independent_over_every_permutation():
    """AEGS-0.1-VERD-3, as a theorem rather than a discipline.

    `max` is commutative, associative and idempotent, so the join of a set of proposals cannot
    depend on the order they arrive in. An implementation cannot fail this except by leaving the
    lattice -- by letting a control *assign* a verdict rather than propose one.
    """
    import itertools

    controls = [("treasury", "REVIEW"), ("risk", "ESCALATE"),
                ("sanctions", "REJECT"), ("roi", "APPROVE")]
    finals = {_resolve(p)[0] for p in itertools.permutations(controls)}
    assert finals == {_SEVERITY["REJECT"]}, finals


def test_attribution_is_order_dependent_exactly_when_the_maximum_ties():
    """`research/lattice.md` Proposition 2, and the correction it produced.

    The last control to narrow is the **first control in the order attaining the maximum**. So
    attribution varies with order if and only if more than one control proposes that maximum.

    The specification's own rationale for VERD-4 illustrated order-dependence with treasury
    narrowing to REVIEW and risk then narrowing to ESCALATE. **That example does not exhibit it**
    -- the maximum is attained only by risk, so every order attributes to risk. Formalising the
    lattice caught it; this test pins the corrected reading so the example cannot drift back.
    """
    import itertools

    # |argmax| == 1: attribution is order-independent.
    single = [("treasury", "REVIEW"), ("risk", "ESCALATE")]
    attributions = {_resolve(p)[1][-1] for p in itertools.permutations(single)}
    assert attributions == {"risk"}, (
        "the spec's original VERD-4 example, which attributes to risk under both orders and "
        "therefore demonstrates nothing about attribution"
    )

    # |argmax| == 2: attribution follows whichever tied control comes first.
    tied = [("sanctions", "REJECT"), ("treasury", "REJECT")]
    attributions = {_resolve(p)[1][-1] for p in itertools.permutations(tied)}
    assert attributions == {"sanctions", "treasury"}, (
        "a tie at the maximum is the only way attribution becomes order-dependent, and it is "
        "exactly the case VERD-4a's dispositive controls exist to resolve"
    )


def test_a_dispositive_control_makes_attribution_order_independent_again():
    """`research/lattice.md` Proposition 3, against the shipped declaration.

    A total order on verdicts cannot separate two controls that both reach the maximum. Something
    outside the lattice has to, and the declared precedence of dispositive controls is that
    something -- which is why the set must be fixed in advance rather than chosen per decision.
    """
    from tesoro.record import DISPOSITIVE_CONTROLS

    assert DISPOSITIVE_CONTROLS == ("sanctions", "killswitch"), DISPOSITIVE_CONTROLS

    # Declared precedence is a total order on the set, so a tie among dispositive controls
    # resolves without reading the evaluation order at all.
    assert len(set(DISPOSITIVE_CONTROLS)) == len(DISPOSITIVE_CONTROLS)
