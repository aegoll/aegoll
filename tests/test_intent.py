"""Economic intent — was this what the agent was sent to do?

Every other engine answers a variant of *may this agent spend?* Intent asks a
different question, and the case that motivates it is an agent which passes every
other check: right amount, familiar vendor, budget intact, and nothing to do with
the job it was given. Treasury, trust, risk and policy all approve that purchase,
because none of them knows what the agent was for.

Two properties carry most of the weight here:

* **Intent is optional, and its absence is recorded rather than assumed.** Adding
  it must not refuse agents that never declared one, and a record must not look
  like a passed check when no check ran.
* **The total is the budget.** A per-action ceiling that permits unlimited
  repetitions is not a budget; structuring one large spend into many small ones is
  precisely the threat it fails to stop.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tesoro.engines.economic import intent as intent_engine
from tesoro.clock import FixedClock
from tesoro.domain import Channel, Purpose, Vendor, Verdict
from tesoro.engines.economic.intent import Intent, IntentStore
from tesoro.plugin import Governor
from tesoro.runtime import Tesoro, Paths
from tesoro.record import can_validate

BASE = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
SELLER = Vendor(id="x402-poc-desk", name="POC Desk")


@pytest.fixture
def tesoro(tmp_path):
    a = Tesoro(paths=Paths.ephemeral(tmp_path), clock=FixedClock(BASE))
    yield a
    a.close()


@pytest.fixture
def gov(tmp_path):
    g = Governor(advisor=None, data_dir=tmp_path)
    yield g
    g.close()


def request_for(tesoro, resource="/market/snapshot", amount="0.001", **kw):
    return tesoro.build_request(resource=resource, amount_usd=amount, vendor=SELLER, **kw)


def codes(decision) -> set[str]:
    return {r.code for r in decision.reasons if r.source == "intent"}


# --- absence is recorded, never assumed -----------------------------------


def test_an_agent_with_no_intent_is_not_refused(tesoro):
    """Adding intent must not punish the ordinary case."""
    decision = tesoro.decide(request_for(tesoro))
    assert decision.verdict is Verdict.APPROVE


def test_an_agent_with_no_intent_says_so_explicitly(tesoro):
    """`no check ran` and `check passed` must never look alike."""
    decision = tesoro.decide(request_for(tesoro))
    assert "no_intent_declared" in codes(decision)

    verdict = tesoro.intent_for(request_for(tesoro))
    assert verdict.governed is False
    assert verdict.as_dict()["intentId"] is None


def test_a_governed_action_is_marked_as_governed(tesoro):
    tesoro.intents.declare(agent_id="agent-1", purpose="buy data", maximum_usd="1.00", now=BASE)
    verdict = tesoro.intent_for(request_for(tesoro))
    assert verdict.governed is True
    assert "within_intent" in {r.code for r in verdict.reasons}


# --- the intent must still be live ----------------------------------------


def test_an_expired_intent_authorises_nothing(tesoro):
    tesoro.intents.declare(
        agent_id="agent-1", purpose="buy data", maximum_usd="1.00",
        expires_at=BASE - timedelta(hours=1), now=BASE - timedelta(days=1),
    )
    # `active_for` will not return an expired intent, so ask the engine directly
    # with the intent in hand -- an expired authorisation is not a weaker one.
    expired = tesoro.intents.all()[0]
    verdict = intent_engine.evaluate(request_for(tesoro), expired, now=BASE)

    assert verdict.verdict is Verdict.REJECT
    assert "intent_expired" in {r.code for r in verdict.reasons}


def test_a_revoked_intent_authorises_nothing(tesoro):
    declared = tesoro.intents.declare(
        agent_id="agent-1", purpose="buy data", maximum_usd="1.00", now=BASE
    )
    assert tesoro.intents.revoke(declared.intent_id) is True

    verdict = intent_engine.evaluate(
        request_for(tesoro), tesoro.intents.get(declared.intent_id), now=BASE
    )
    assert verdict.verdict is Verdict.REJECT
    assert "intent_revoked" in {r.code for r in verdict.reasons}


def test_revoking_keeps_the_record_of_it_having_been_granted(tesoro):
    """Withdrawing authority is not the same as it never having existed."""
    declared = tesoro.intents.declare(
        agent_id="agent-1", purpose="buy data", maximum_usd="1.00", now=BASE
    )
    tesoro.intents.revoke(declared.intent_id)

    stored = tesoro.intents.get(declared.intent_id)
    assert stored is not None
    assert stored.status == "revoked"
    assert stored.purpose == "buy data"


def test_an_expired_intent_is_not_returned_as_active(tesoro):
    tesoro.intents.declare(
        agent_id="agent-1", purpose="stale", maximum_usd="1.00",
        expires_at=BASE - timedelta(minutes=1), now=BASE - timedelta(days=1),
    )
    assert tesoro.intents.active_for("agent-1", BASE) is None


# --- the action must be the kind of thing the intent is about -------------


def test_a_resource_outside_the_intent_is_reviewed_not_approved(tesoro):
    tesoro.intents.declare(
        agent_id="agent-1", purpose="buy market data", maximum_usd="1.00",
        allowed_resources=["/market/*"], now=BASE,
    )
    decision = tesoro.decide(request_for(tesoro, resource="/compute/batch"))

    assert decision.verdict is Verdict.REVIEW
    assert "intent_resource_mismatch" in codes(decision)


def test_a_resource_inside_the_intent_passes(tesoro):
    tesoro.intents.declare(
        agent_id="agent-1", purpose="buy market data", maximum_usd="1.00",
        allowed_resources=["/market/*"], now=BASE,
    )
    assert tesoro.decide(request_for(tesoro, resource="/market/signal")).verdict is Verdict.APPROVE


def test_an_empty_resource_list_means_unrestricted(tesoro):
    """A real choice, and visible as one rather than an accident."""
    tesoro.intents.declare(agent_id="agent-1", purpose="anything", maximum_usd="1.00", now=BASE)
    assert tesoro.decide(request_for(tesoro, resource="/anything/at/all")).verdict is Verdict.APPROVE


def test_a_category_outside_the_intent_is_reviewed(tesoro):
    tesoro.intents.declare(
        agent_id="agent-1", purpose="market data only", maximum_usd="1.00",
        allowed_categories=["market-data"], now=BASE,
    )
    request = tesoro.build_request(
        resource="/x", amount_usd="0.001", vendor=SELLER,
    )
    verdict = intent_engine.evaluate(
        request, tesoro.intents.active_for("agent-1", BASE), now=BASE, category="gambling"
    )
    assert verdict.verdict is Verdict.REVIEW
    assert "intent_category_mismatch" in {r.code for r in verdict.reasons}


def test_an_intent_can_be_scoped_to_one_channel(tesoro):
    """An intent to buy market data does not obviously authorise inference spend."""
    tesoro.intents.declare(
        agent_id="agent-1", purpose="buy data", maximum_usd="1.00",
        allowed_channels=["external"], now=BASE,
    )
    request = tesoro.build_request(
        resource="llm:some-model", amount_usd="0.01", vendor=SELLER,
        purpose=Purpose.INFERENCE, channel=Channel.INTERNAL,
    )
    verdict = intent_engine.evaluate(
        request, tesoro.intents.active_for("agent-1", BASE), now=BASE
    )
    assert "intent_channel_mismatch" in {r.code for r in verdict.reasons}


# --- and it must be affordable within the intent --------------------------


def test_the_total_is_the_budget_not_the_per_action_figure(tesoro):
    """A ceiling that permits unlimited repetitions is not a budget."""
    declared = tesoro.intents.declare(
        agent_id="agent-1", purpose="buy data", maximum_usd="0.010", now=BASE
    )
    # Spend most of it, settled -- only settled spend consumes an envelope.
    tesoro.store.record(
        tx_id="t1", at=BASE, agent_id="agent-1", vendor_id=SELLER.id,
        resource="/market/snapshot", amount_atomic=9_000, verdict=Verdict.APPROVE,
        settled=True, success=True, intent_id=declared.intent_id,
    )
    assert tesoro.store.spent_under_intent(declared.intent_id) == 9_000

    decision = tesoro.decide(request_for(tesoro, amount="0.005"))
    assert decision.verdict is Verdict.REJECT
    assert "intent_budget_exceeded" in codes(decision)


def test_unsettled_spend_does_not_consume_the_intent(tesoro):
    """An authorised payment that never settled did not consume budget."""
    declared = tesoro.intents.declare(
        agent_id="agent-1", purpose="buy data", maximum_usd="0.010", now=BASE
    )
    tesoro.store.record(
        tx_id="t1", at=BASE, agent_id="agent-1", vendor_id=SELLER.id,
        resource="/market/snapshot", amount_atomic=9_000, verdict=Verdict.APPROVE,
        settled=False, success=False, intent_id=declared.intent_id,
    )
    assert tesoro.store.spent_under_intent(declared.intent_id) == 0


def test_a_per_action_ceiling_is_enforced_when_declared(tesoro):
    tesoro.intents.declare(
        agent_id="agent-1", purpose="buy data", maximum_usd="1.00",
        maximum_per_action_usd="0.005", now=BASE,
    )
    decision = tesoro.decide(request_for(tesoro, amount="0.010"))
    assert decision.verdict is Verdict.REJECT
    assert "intent_per_action_exceeded" in codes(decision)


def test_a_different_asset_is_refused_not_converted(tesoro):
    """Converting silently would need a rate, a source and a timestamp."""
    tesoro.intents.declare(
        agent_id="agent-1", purpose="buy data", maximum_usd="1.00",
        asset="USDC", now=BASE,
    )
    internal = tesoro.build_request(
        resource="llm:m", amount_usd="0.01", vendor=SELLER,
        purpose=Purpose.INFERENCE, channel=Channel.INTERNAL,
    )
    verdict = intent_engine.evaluate(
        internal, tesoro.intents.active_for("agent-1", BASE), now=BASE
    )
    assert verdict.verdict is Verdict.REJECT
    assert "intent_asset_mismatch" in {r.code for r in verdict.reasons}


# --- the clamp invariant --------------------------------------------------


def test_intent_can_only_narrow_never_widen(tesoro):
    """The invariant every engine obeys. An intent cannot authorise what treasury refused."""
    tesoro.intents.declare(
        agent_id="agent-1", purpose="spend freely", maximum_usd="10000.00", now=BASE
    )
    # $5000 breaches the treasury envelopes regardless of what the intent permits.
    decision = tesoro.decide(request_for(tesoro, amount="5000.00"))
    assert decision.verdict is not Verdict.APPROVE


def test_a_clamp_by_intent_is_attributed(tesoro):
    """A refusal with no attributable cause is not auditable evidence."""
    tesoro.intents.declare(
        agent_id="agent-1", purpose="market only", maximum_usd="1.00",
        allowed_resources=["/market/*"], now=BASE,
    )
    decision = tesoro.decide(request_for(tesoro, resource="/compute/batch"))
    assert any(
        r.source == "authorize" and r.code == "clamped_by_intent" for r in decision.reasons
    )


# --- the plugin surface ---------------------------------------------------


def test_the_governor_can_declare_and_read_back_an_intent(gov):
    declared = gov.declare_intent(
        purpose="buy market data", maximum_usd="0.05", allowed_resources=["/market/*"]
    )
    active = gov.active_intent()
    assert active is not None
    assert active.intent_id == declared.intent_id
    assert gov.report()["intent"]["purpose"] == "buy market data"


def test_a_run_without_an_intent_reports_none(gov):
    assert gov.active_intent() is None
    assert gov.report()["intent"] is None


@pytest.mark.skipif(
    not can_validate(),
    reason=(
        "schema validation needs the `schema` extra (jsonschema). Skipped rather than "
        "failed: without a validator this test cannot distinguish an invalid record from "
        "an unchecked one, which is the very thing it asserts. "
        "test_ci_can_validate_schemas keeps this from becoming permanent."
    ),
)
def test_the_decision_record_carries_the_intent_id(gov):
    from tesoro import record as record_mod

    declared = gov.declare_intent(purpose="think", maximum_usd="1.00", asset="USD")
    gov.authorize_run(model="m", budget_usd=0.03)

    record = record_mod.records_from_journal(gov.tesoro.audit.entries())[0]
    assert record["intentId"] == declared.intent_id
    assert record_mod.validate(record)[0] is True


@pytest.mark.skipif(
    not can_validate(),
    reason=(
        "schema validation needs the `schema` extra (jsonschema). Skipped rather than "
        "failed: without a validator this test cannot distinguish an invalid record from "
        "an unchecked one, which is the very thing it asserts. "
        "test_ci_can_validate_schemas keeps this from becoming permanent."
    ),
)
def test_a_record_without_an_intent_has_a_null_id_and_stays_valid(gov):
    from tesoro import record as record_mod

    gov.authorize_run(model="m", budget_usd=0.03)
    record = record_mod.records_from_journal(gov.tesoro.audit.entries())[0]

    assert record["intentId"] is None
    assert record_mod.validate(record)[0] is True


# --- the schema -----------------------------------------------------------


@pytest.mark.skipif(
    not can_validate(),
    reason=(
        "schema validation needs the `schema` extra (jsonschema). Skipped rather than "
        "failed: without a validator this test cannot distinguish an invalid record from "
        "an unchecked one, which is the very thing it asserts. "
        "test_ci_can_validate_schemas keeps this from becoming permanent."
    ),
)
def test_a_declared_intent_validates_against_the_schema(tesoro):
    declared = tesoro.intents.declare(
        agent_id="agent-1", purpose="buy data", maximum_usd="0.05",
        allowed_resources=["/market/*"], expires_at=BASE + timedelta(days=1), now=BASE,
    )
    ok, problems = intent_engine.validate(declared.as_dict())
    assert ok, problems


def test_the_schema_rejects_a_float_amount(tesoro):
    declared = tesoro.intents.declare(
        agent_id="agent-1", purpose="p", maximum_usd="1.00", now=BASE
    )
    payload = declared.as_dict()
    payload["maximumAmount"] = 1.0
    assert intent_engine.validate(payload)[0] is False


def test_the_schema_rejects_an_unknown_field(tesoro):
    declared = tesoro.intents.declare(
        agent_id="agent-1", purpose="p", maximum_usd="1.00", now=BASE
    )
    payload = declared.as_dict()
    payload["vendorExtension"] = True
    assert intent_engine.validate(payload)[0] is False


def test_an_intent_round_trips_through_its_own_schema(tesoro):
    declared = tesoro.intents.declare(
        agent_id="agent-1", purpose="round trip", maximum_usd="0.25",
        maximum_per_action_usd="0.05", allowed_resources=["/a/*", "/b/**"],
        expires_at=BASE + timedelta(hours=6), now=BASE,
    )
    restored = Intent.from_dict(declared.as_dict())
    assert restored == declared


# --- storage --------------------------------------------------------------


def test_intents_are_not_transferable_between_agents(tmp_path):
    """An intent is a statement of purpose, not a shared budget."""
    store = IntentStore(tmp_path / "intents.json")
    store.declare(agent_id="agent-a", purpose="a", maximum_usd="1.00", now=BASE)

    assert store.active_for("agent-b", BASE) is None
    assert store.active_for("agent-a", BASE) is not None


def test_the_newest_active_intent_wins(tmp_path):
    store = IntentStore(tmp_path / "intents.json")
    store.declare(agent_id="a", purpose="old", maximum_usd="1.00", now=BASE - timedelta(days=1))
    newer = store.declare(agent_id="a", purpose="new", maximum_usd="1.00", now=BASE)

    assert store.active_for("a", BASE).intent_id == newer.intent_id
