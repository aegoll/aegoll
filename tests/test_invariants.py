"""The invariants the whole design rests on. If these fail, the claims are void."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from aegoll import Aegoll, FixedClock, Paths, Vendor, Verdict, load_bundle
from aegoll.domain import narrower

BASE = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def aegoll(tmp_path):
    a = Aegoll(bundle=load_bundle(), paths=Paths.ephemeral(tmp_path), clock=FixedClock(BASE))
    yield a
    a.close()


VENDOR = Vendor(id="v1", name="Vendor One")


def test_no_approved_sequence_can_breach_an_envelope(aegoll):
    """Property test: however the amounts fall, approvals never exceed a limit.

    This is the single most important test in the suite -- it is the difference
    between a budget control and a budget suggestion.
    """
    rng = random.Random(1234)
    cfg = aegoll.bundle.treasury
    approved_today = 0

    for i in range(400):
        amount = rng.choice(["0.001", "0.01", "0.5", "2.00", "9.99", "11.00"])
        req = aegoll.build_request(
            resource="/market/snapshot",
            amount_usd=amount,
            vendor=VENDOR,
            request_id=f"prop-{i}",
        )
        decision = aegoll.authorize(req)

        if decision.verdict is Verdict.APPROVE:
            # Simulate settlement so the next iteration sees the spend.
            aegoll.record_settlement(req.id, success=True, tx_hash=f"0x{i:04d}")
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
    a = Aegoll(
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


def test_sanctioned_vendor_is_an_absolute_bar(aegoll):
    req = aegoll.build_request(
        resource="/market/snapshot",
        amount_usd="0.001",
        vendor=Vendor(id="ofac-1", name="Sanctioned Co", sanctioned=True),
    )
    decision = aegoll.decide(req)
    assert decision.verdict is Verdict.REJECT


def test_decision_is_deterministic(aegoll):
    """Same inputs -> identical hash. This is what makes replay meaningful."""
    hashes = set()
    for _ in range(5):
        req = aegoll.build_request(
            resource="/market/snapshot",
            amount_usd="0.001",
            vendor=VENDOR,
            request_id="stable-id",
        )
        hashes.add(aegoll.decide(req).decision_hash)
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
    from aegoll.domain import atomic_to_usd, usd_to_atomic

    for value in ["0.001", "0.005", "0.01", "0.1", "1.00", "999.999999", "0.000001"]:
        assert str(atomic_to_usd(usd_to_atomic(value))) == f"{float(value):.6f}"

    # The classic float failure: 0.1 + 0.2 != 0.3
    assert usd_to_atomic("0.1") + usd_to_atomic("0.2") == usd_to_atomic("0.3")


def test_audit_chain_detects_tampering(aegoll, tmp_path):
    for i in range(3):
        req = aegoll.build_request(
            resource="/market/snapshot", amount_usd="0.001", vendor=VENDOR,
            request_id=f"audit-{i}",
        )
        aegoll.authorize(req)

    ok, problems = aegoll.audit.verify()
    assert ok, problems

    # Edit a past record: the chain must notice.
    path = aegoll.audit.path
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].replace('"verdict":"APPROVE"', '"verdict":"REJECT"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, problems = aegoll.audit.verify()
    assert not ok
    assert any("hash mismatch" in p or "does not match" in p for p in problems)


def test_velocity_burst_is_caught(aegoll):
    """Ten transactions in a minute must trip the velocity counter."""
    for i in range(12):
        aegoll.store.record(
            tx_id=f"burst-{i}",
            at=BASE - timedelta(seconds=30),
            agent_id=aegoll.agent_id,
            vendor_id=VENDOR.id,
            resource="/market/snapshot",
            amount_atomic=1000,
            verdict=Verdict.APPROVE,
            settled=True,
            success=True,
        )
    req = aegoll.build_request(resource="/market/snapshot", amount_usd="0.001", vendor=VENDOR)
    decision = aegoll.decide(req)
    assert not decision.budget.ok
    assert decision.budget.binding == "velocity_60s"
    assert decision.verdict is not Verdict.APPROVE
