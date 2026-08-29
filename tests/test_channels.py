"""The internal/external channel split.

The claim being tested: both kinds of spending flow through the same engines, but
their budgets are genuinely separate. If internal spend could consume the external
envelope (or vice versa) the layer would be mis-modelling two different
currencies paid to two different counterparties as one pot.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tesoro import Tesoro, FixedClock, Paths, Vendor, Verdict, load_bundle
from tesoro.domain import Channel
from tesoro.inference import InferenceGate

BASE = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
SELLER = Vendor(id="x402-poc-desk", name="POC Desk")


@pytest.fixture
def tesoro(tmp_path):
    a = Tesoro(bundle=load_bundle(), paths=Paths.ephemeral(tmp_path), clock=FixedClock(BASE))
    yield a
    a.close()


def test_channels_have_separate_envelopes(tesoro):
    """The internal per-transaction limit is $0.04; external is $10."""
    internal = tesoro.build_request(
        resource="llm:claude-haiku-4-5",
        amount_usd="0.04",
        vendor=Vendor(id="anthropic-api"),
        channel=Channel.INTERNAL,
    )
    external = tesoro.build_request(
        resource="/market/snapshot", amount_usd="0.04", vendor=SELLER,
        channel=Channel.EXTERNAL,
    )
    i_limit = tesoro.decide(internal).budget.envelopes[0].limit_atomic
    e_limit = tesoro.decide(external).budget.envelopes[0].limit_atomic
    assert i_limit != e_limit, "both channels resolved to the same treasury config"
    assert i_limit == 40_000      # $0.04
    assert e_limit == 10_000_000  # $10.00


def test_internal_spend_does_not_consume_the_external_budget(tesoro):
    """Burn the whole internal budget; external headroom must be untouched."""
    gate = InferenceGate(tesoro)
    probe = tesoro.build_request(
        resource="/market/snapshot", amount_usd="0.001", vendor=SELLER
    )
    before = tesoro.decide(probe).budget.headroom_atomic

    for _ in range(3):
        auth = gate.authorize_run(model="claude-haiku-4-5", budget_usd=0.04)
        if auth.allowed:
            gate.settle_run(auth.request_id, actual_cost_usd=0.04)

    after = tesoro.decide(probe).budget.headroom_atomic
    assert after == before, (
        f"internal spend leaked into the external envelope: {before} -> {after}"
    )


def test_external_spend_does_not_consume_the_internal_budget(tesoro):
    gate = InferenceGate(tesoro)
    before = gate.budget_state()

    for i in range(5):
        req = tesoro.build_request(
            resource="/market/snapshot",
            amount_usd="0.001",
            vendor=SELLER,
            request_id=f"ext-{i}",
        )
        if tesoro.authorize(req).verdict is Verdict.APPROVE:
            tesoro.record_settlement(req.id, success=True, tx_hash=f"0x{i}")

    after = gate.budget_state()
    assert after["envelopes"] == before["envelopes"], (
        "external spend moved the internal envelopes"
    )


def test_exhausted_token_budget_rejects_rather_than_reviews(tesoro):
    """An over-budget run must REJECT, not REVIEW.

    There is no human to ask mid-run, and starting a run that cannot finish wastes
    the very budget that is already short.
    """
    gate = InferenceGate(tesoro)
    auth = gate.authorize_run(model="claude-opus-5", budget_usd=0.10)  # per-tx is $0.04
    assert auth.decision.verdict is Verdict.REJECT
    assert not auth.allowed
    assert auth.blocking_engine == "treasury"
    assert auth.decision.budget.binding == "per_transaction"


def test_settle_run_records_actual_not_budgeted(tesoro):
    """Authorization reserves the ceiling; settlement records what was spent."""
    gate = InferenceGate(tesoro)
    auth = gate.authorize_run(model="claude-haiku-4-5", budget_usd=0.04)
    assert auth.allowed
    gate.settle_run(auth.request_id, actual_cost_usd=0.0223)

    rows = [t for t in tesoro.store.all_transactions() if t.id == auth.request_id]
    assert rows and rows[0].amount_atomic == 22_300, (
        "settlement should overwrite the reserved budget with the actual cost"
    )
    assert rows[0].channel == "internal"


def test_channel_is_part_of_the_decision_hash(tesoro):
    """Otherwise two different spends could collide in the audit trail."""
    common = dict(resource="same", amount_usd="0.01", vendor=SELLER, request_id="fixed")
    a = tesoro.decide(tesoro.build_request(channel=Channel.INTERNAL, **common))
    b = tesoro.decide(tesoro.build_request(channel=Channel.EXTERNAL, **common))
    assert a.decision_hash != b.decision_hash


# --- P2.2: settlement reconciliation ---------------------------------------


def test_an_authorisation_that_never_settles_is_unreconciled_not_failed(tmp_path):
    """The four-state rule applied to settlement. P2.2.

    A failure is a statement somebody made. Silence is the absence of one, and money may well
    have moved. Collapsing the two would let a broken integration look like a series of clean
    refusals.
    """
    from tesoro.domain import Vendor
    from tesoro.runtime import Paths, Tesoro

    t = Tesoro(paths=Paths.under(tmp_path))
    for i in range(3):
        t.authorize(t.build_request(resource=f"/r{i}", amount_usd="9.00", vendor=Vendor(id="acme")))

    r = t.store.reconcile()
    assert r.unreconciled_count == 3
    assert r.unreconciled_atomic == 27_000_000
    assert r.failed_count == 0, "silence was counted as failure"
    assert r.settled_count == 0
    assert not r.clean


def test_the_value_envelopes_have_not_counted_the_unreconciled_exposure(tmp_path):
    """The measurement that makes this surface worth having, pinned so it cannot drift.

    Value envelopes sum `settled=1 AND success=1`. That is defensible -- an authorisation that
    never executed should not permanently consume a budget -- and it means an integration that
    never calls `record_settlement` has **no value ceiling at all**, only a count one.

    Measured: ten $9 authorisations against a $50 daily ceiling leave `spent_today` at zero.
    This test does not assert that the envelope semantics are wrong. It asserts that the gap is
    real and that `reconcile()` reports the number no envelope has seen.
    """
    from tesoro.domain import Vendor
    from tesoro.runtime import Paths, Tesoro

    t = Tesoro(paths=Paths.under(tmp_path))
    for i in range(10):
        t.authorize(t.build_request(resource=f"/r{i}", amount_usd="9.00", vendor=Vendor(id="acme")))

    probe = t.build_request(resource="/probe", amount_usd="9.00", vendor=Vendor(id="acme"))
    assert t.snapshot_for(probe).spent_today_atomic == 0, "envelopes saw the unsettled spend"
    assert t.store.reconcile().exposure_atomic == 90_000_000


def test_a_settlement_for_a_different_amount_is_diverged(tmp_path):
    """`diverged` is separate from `settled`, because a persistent gap is a broken integration.

    A NULL settled amount means *not reported*, which is a different fact from *reported as
    equal* -- so the query counts only rows where an amount was actually given. Counting NULL as
    agreement would report a silent integration as a reconciled one.
    """
    from tesoro.domain import Vendor
    from tesoro.runtime import Paths, Tesoro

    t = Tesoro(paths=Paths.under(tmp_path))
    req = t.build_request(resource="/r", amount_usd="5.00", vendor=Vendor(id="acme"))
    t.authorize(req)
    t.record_settlement(req.id, success=True, tx_hash="0xabc", amount_atomic=7_000_000)

    r = t.store.reconcile()
    assert r.settled_count == 1
    assert r.diverged_count == 1
    assert r.diverged_atomic == 7_000_000, "diverged reports what settled, not what was authorised"
    assert r.unreconciled_count == 0
    assert not r.clean


def test_a_settlement_matching_its_authorisation_reconciles_clean(tmp_path):
    from tesoro.domain import Vendor
    from tesoro.runtime import Paths, Tesoro

    t = Tesoro(paths=Paths.under(tmp_path))
    req = t.build_request(resource="/r", amount_usd="5.00", vendor=Vendor(id="acme"))
    t.authorize(req)
    t.record_settlement(req.id, success=True, tx_hash="0xabc", amount_atomic=5_000_000)

    r = t.store.reconcile()
    assert r.clean
    assert r.settled_count == 1 and r.diverged_count == 0 and r.unreconciled_count == 0


def test_the_four_reconciliation_states_are_named_and_distinct():
    from tesoro.store import RECONCILIATION_STATES

    assert RECONCILIATION_STATES == ("settled", "failed", "diverged", "unreconciled")
