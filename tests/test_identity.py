"""Agent identity (KYA) — who is acting, and under whose authority?

Intent asks whether an action is what the agent was sent to do. Identity asks the
prior question: is this actor still authorised to act at all, and does the authority
it claims actually belong to it?

The tests that carry the most weight here are the **privacy** ones. A standard which
makes real-world identity the price of transacting has answered the accountability
question by abolishing privacy, so the design keeps controller and operator private
by default — and a default is only real if something fails when it is violated.
`test_a_controller_never_reaches_a_counterparty` is that something.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from tesoro.engines.evidence import identity as identity_engine
from tesoro.clock import FixedClock
from tesoro.domain import Vendor, Verdict
from tesoro.engines.evidence.identity import Credential, Identity, IdentityStore, Party
from tesoro.plugin import Governor
from tesoro.runtime import Tesoro, Paths
from tesoro.record import can_validate

BASE = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
SELLER = Vendor(id="x402-poc-desk", name="POC Desk")
ACME = Party(id="acme-ltd", kind="organisation", jurisdiction="LK")


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


def request_for(tesoro, amount="0.001", **kw):
    return tesoro.build_request(
        resource="/market/snapshot", amount_usd=amount, vendor=SELLER, **kw
    )


def codes(decision) -> set[str]:
    return {r.code for r in decision.reasons if r.source == "identity"}


# --- privacy: the load-bearing tests --------------------------------------


def test_a_controller_never_reaches_a_counterparty(tesoro):
    """The default is only real if something fails when it is violated."""
    tesoro.identities.register(
        agent_id="agent-1", purpose="buy data", controller=ACME,
        operator=Party(id="ops-team", kind="organisation"), now=BASE,
    )
    disclosed = tesoro.identities.get("agent-1").disclose("vendor")
    blob = json.dumps(disclosed)

    assert "acme-ltd" not in blob
    assert "ops-team" not in blob
    assert "controller" not in disclosed
    assert "operator" not in disclosed


def test_spending_limits_are_never_disclosed_to_a_counterparty(tesoro):
    """Telling a seller the remaining budget invites it to charge exactly that."""
    tesoro.identities.register(
        agent_id="agent-1", purpose="buy data", per_action_usd="0.02",
        daily_usd="1.00", now=BASE,
    )
    disclosed = tesoro.identities.get("agent-1").disclose("vendor")

    assert "spendingLimits" not in disclosed
    assert "0.02" not in json.dumps(disclosed)


def test_wallets_are_not_disclosed_by_default(tesoro):
    """A seller already sees the address paying it; the set links activity across
    counterparties, which is a surveillance surface rather than a control."""
    tesoro.identities.register(
        agent_id="agent-1", purpose="buy data",
        wallets=[{"address": "0xABC", "network": "eip155:84532"}], now=BASE,
    )
    assert "wallets" not in tesoro.identities.get("agent-1").disclose("vendor")


def test_an_auditor_sees_everything(tesoro):
    """An audit that cannot see the controller cannot establish accountability."""
    tesoro.identities.register(
        agent_id="agent-1", purpose="buy data", controller=ACME, now=BASE
    )
    disclosed = tesoro.identities.get("agent-1").disclose("auditor")
    assert disclosed["controller"]["id"] == "acme-ltd"


def test_an_unknown_audience_is_refused_rather_than_defaulted(tesoro):
    """Defaulting an unrecognised audience to full disclosure is how leaks happen."""
    tesoro.identities.register(agent_id="agent-1", purpose="p", now=BASE)
    with pytest.raises(ValueError):
        tesoro.identities.get("agent-1").disclose("partner")


def test_the_journal_carries_only_the_vendor_projection(tesoro):
    """A journal holding controller details makes every reader of it a holder of
    personal data — a privacy cost the evidence does not require."""
    tesoro.identities.register(
        agent_id="agent-1", purpose="buy data", controller=ACME, now=BASE
    )
    tesoro.authorize(request_for(tesoro))

    blob = json.dumps([e.payload for e in tesoro.audit.entries()])
    assert "acme-ltd" not in blob


@pytest.mark.skipif(
    not can_validate(),
    reason=(
        "schema validation needs the `schema` extra (jsonschema). Skipped rather than "
        "failed: without a validator this test cannot distinguish an invalid record from "
        "an unchecked one, which is the very thing it asserts. "
        "test_ci_can_validate_schemas keeps this from becoming permanent."
    ),
)
def test_the_decision_record_carries_no_controller(gov):
    from tesoro import record as record_mod

    gov.register_identity(purpose="buy data", controller=ACME)
    gov.authorize_run(model="m", budget_usd=0.03)

    record = record_mod.records_from_journal(gov.tesoro.audit.entries())[0]
    assert "acme-ltd" not in json.dumps(record)
    assert record["actor"]["known"] is True
    assert record_mod.validate(record)[0] is True


def test_the_governor_report_carries_no_controller(gov):
    """The report is rendered in a browser; it must not leak because it can."""
    gov.register_identity(purpose="buy data", controller=ACME)
    assert "acme-ltd" not in json.dumps(gov.report())


# --- absence is recorded, never assumed -----------------------------------


def test_an_unregistered_agent_is_not_refused(tesoro):
    assert tesoro.decide(request_for(tesoro)).verdict is Verdict.APPROVE


def test_an_unregistered_agent_says_so_explicitly(tesoro):
    decision = tesoro.decide(request_for(tesoro))
    assert "no_identity_registered" in codes(decision)
    assert tesoro.identity_for(request_for(tesoro)).known is False


def test_a_record_distinguishes_unknown_from_cleared(gov):
    from tesoro import record as record_mod

    gov.authorize_run(model="m", budget_usd=0.03)
    record = record_mod.records_from_journal(gov.tesoro.audit.entries())[0]
    assert record["actor"]["known"] is False
    assert record["actor"]["status"] is None


# --- authority to act -----------------------------------------------------


def test_a_revoked_agent_may_not_transact(tesoro):
    tesoro.identities.register(agent_id="agent-1", purpose="p", now=BASE)
    tesoro.identities.set_status("agent-1", "revoked")

    decision = tesoro.decide(request_for(tesoro))
    assert decision.verdict is Verdict.REJECT
    assert "identity_revoked" in codes(decision)


def test_a_suspended_agent_escalates_rather_than_rejecting(tesoro):
    """Suspension is reversible and needs a human; rejection is the wrong signal."""
    tesoro.identities.register(agent_id="agent-1", purpose="p", now=BASE)
    tesoro.identities.set_status("agent-1", "suspended")

    decision = tesoro.decide(request_for(tesoro))
    assert decision.verdict is Verdict.ESCALATE
    assert "identity_suspended" in codes(decision)


def test_revocation_is_terminal(tesoro):
    """Reviving a revoked identity would make revocation advisory."""
    tesoro.identities.register(agent_id="agent-1", purpose="p", now=BASE)
    tesoro.identities.set_status("agent-1", "revoked")

    with pytest.raises(ValueError) as err:
        tesoro.identities.set_status("agent-1", "active")
    assert "terminal" in str(err.value)


def test_suspension_is_reversible(tesoro):
    tesoro.identities.register(agent_id="agent-1", purpose="p", now=BASE)
    tesoro.identities.set_status("agent-1", "suspended")
    assert tesoro.identities.set_status("agent-1", "active") is True
    assert tesoro.decide(request_for(tesoro)).verdict is Verdict.APPROVE


# --- delegation may only narrow -------------------------------------------


def test_a_delegation_that_widens_authority_is_refused(tesoro):
    """A sub-agent claiming more than its parent is an escalation, not a delegation."""
    tesoro.identities.register(
        agent_id="parent", purpose="parent", per_action_usd="0.01", now=BASE
    )
    tesoro.identities.register(
        agent_id="child", purpose="child", parent_agent_id="parent",
        per_action_usd="1.00", now=BASE,
    )

    verdict = identity_engine.evaluate(
        request_for(tesoro), tesoro.identities.get("child"), now=BASE,
        parent=tesoro.identities.get("parent"),
    )
    assert verdict.verdict is Verdict.REJECT
    assert "identity_delegation_widens" in {r.code for r in verdict.reasons}


def test_a_delegation_within_the_parents_authority_passes(tesoro):
    tesoro.identities.register(
        agent_id="parent", purpose="parent", per_action_usd="1.00", now=BASE
    )
    tesoro.identities.register(
        agent_id="child", purpose="child", parent_agent_id="parent",
        per_action_usd="0.01", now=BASE,
    )
    verdict = identity_engine.evaluate(
        request_for(tesoro), tesoro.identities.get("child"), now=BASE,
        parent=tesoro.identities.get("parent"),
    )
    assert verdict.verdict is Verdict.APPROVE


def test_a_delegate_declaring_no_limit_inherits_its_parents(tesoro):
    """The escalation reachable by omission, and the reason AEGS-0.1-ID-4 says *clamp*.

    `_widens()` compares only limits both parties declare, so a child that declares nothing
    widened nothing it could detect — and the child's own per-action check did not fire either,
    because it had no limit to check against. Declaring no limit was therefore strictly more
    permissive than declaring a large one, which is an escalation you reach by leaving a field
    out.

    The docstring said the policy envelopes would cover it. They do not: envelopes are
    treasury-scoped and never inherit a parent identity's ceiling, so nothing downstream knew
    the parent had said $0.002.
    """
    tesoro.identities.register(
        agent_id="parent", purpose="parent", per_action_usd="0.002", now=BASE
    )
    tesoro.identities.register(
        agent_id="child", purpose="child", parent_agent_id="parent", now=BASE
    )

    verdict = identity_engine.evaluate(
        request_for(tesoro, amount="1.00"), tesoro.identities.get("child"), now=BASE,
        parent=tesoro.identities.get("parent"),
    )
    assert verdict.verdict is not Verdict.APPROVE, (
        "a delegate that declares no per-action limit escaped its parent's"
    )
    assert "identity_per_action_exceeded" in {r.code for r in verdict.reasons}


def test_a_delegate_is_still_bound_by_its_own_tighter_limit(tesoro):
    """The clamp takes the narrower of the two, so a tighter child is not loosened to its
    parent's ceiling on the way through."""
    tesoro.identities.register(
        agent_id="parent", purpose="parent", per_action_usd="10.00", now=BASE
    )
    tesoro.identities.register(
        agent_id="child", purpose="child", parent_agent_id="parent",
        per_action_usd="0.002", now=BASE,
    )

    verdict = identity_engine.evaluate(
        request_for(tesoro, amount="1.00"), tesoro.identities.get("child"), now=BASE,
        parent=tesoro.identities.get("parent"),
    )
    assert verdict.verdict is not Verdict.APPROVE
    assert "identity_per_action_exceeded" in {r.code for r in verdict.reasons}


def test_delegated_authority_cannot_outlive_its_source(tesoro):
    tesoro.identities.register(agent_id="parent", purpose="parent", now=BASE)
    tesoro.identities.register(
        agent_id="child", purpose="child", parent_agent_id="parent", now=BASE
    )
    tesoro.identities.set_status("parent", "revoked")

    verdict = identity_engine.evaluate(
        request_for(tesoro), tesoro.identities.get("child"), now=BASE,
        parent=tesoro.identities.get("parent"),
    )
    assert verdict.verdict is Verdict.REJECT
    assert "identity_parent_inactive" in {r.code for r in verdict.reasons}


def test_an_unverifiable_parent_is_reviewed(tesoro):
    """Unverifiable delegated authority is not authority."""
    tesoro.identities.register(
        agent_id="child", purpose="child", parent_agent_id="ghost", now=BASE
    )
    verdict = identity_engine.evaluate(
        request_for(tesoro), tesoro.identities.get("child"), now=BASE, parent=None
    )
    assert verdict.verdict is Verdict.REVIEW
    assert "identity_parent_unknown" in {r.code for r in verdict.reasons}


def test_a_narrower_network_set_is_not_a_widening(tesoro):
    tesoro.identities.register(
        agent_id="parent", purpose="p", authorized_networks=["a", "b"], now=BASE
    )
    tesoro.identities.register(
        agent_id="child", purpose="c", parent_agent_id="parent",
        authorized_networks=["a"], now=BASE,
    )
    verdict = identity_engine.evaluate(
        request_for(tesoro), tesoro.identities.get("child"), now=BASE,
        parent=tesoro.identities.get("parent"),
    )
    assert "identity_delegation_widens" not in {r.code for r in verdict.reasons}


# --- the controller's declared ceilings -----------------------------------


def test_a_declared_per_action_limit_is_enforced(tesoro):
    tesoro.identities.register(
        agent_id="agent-1", purpose="p", per_action_usd="0.005", now=BASE
    )
    decision = tesoro.decide(request_for(tesoro, amount="0.010"))
    assert decision.verdict is Verdict.REJECT
    assert "identity_per_action_exceeded" in codes(decision)


def test_an_identity_limit_cannot_widen_the_policy(tesoro):
    """It is an ADDITIONAL constraint. An identity does not grant itself headroom."""
    tesoro.identities.register(
        agent_id="agent-1", purpose="p", per_action_usd="100000.00", now=BASE
    )
    # $5000 breaches the treasury envelopes whatever the identity claims.
    assert tesoro.decide(request_for(tesoro, amount="5000.00")).verdict is not Verdict.APPROVE


def test_a_network_outside_the_authorised_set_is_refused(tesoro):
    tesoro.identities.register(
        agent_id="agent-1", purpose="p", authorized_networks=["eip155:84532"], now=BASE
    )
    verdict = identity_engine.evaluate(
        request_for(tesoro), tesoro.identities.get("agent-1"), now=BASE,
        network="eip155:1",
    )
    assert verdict.verdict is Verdict.REJECT
    assert "identity_network_unauthorized" in {r.code for r in verdict.reasons}


def test_a_clamp_by_identity_is_attributed(tesoro):
    tesoro.identities.register(agent_id="agent-1", purpose="p", now=BASE)
    tesoro.identities.set_status("agent-1", "revoked")
    decision = tesoro.decide(request_for(tesoro))
    assert any(
        r.source == "authorize" and r.code == "clamped_by_identity"
        for r in decision.reasons
    )


# --- credentials are referenced, not verified -----------------------------


def test_a_listed_credential_is_not_claimed_as_verified(tesoro):
    """AEGS 0.1 has no verification mechanism; reporting one would be assurance
    this implementation has not obtained."""
    tesoro.identities.register(
        agent_id="agent-1", purpose="p",
        credentials=[Credential(type="kyb", issuer="registrar")], now=BASE,
    )
    credential = tesoro.identities.get("agent-1").credentials[0]
    assert credential.verified is False
    assert credential.as_dict()["verified"] is False


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
def test_a_registered_identity_validates(tesoro):
    identity = tesoro.identities.register(
        agent_id="agent-1", purpose="buy data", controller=ACME,
        authorized_networks=["eip155:84532"], per_action_usd="0.02",
        credentials=[Credential(type="kyb", issuer="registrar")], now=BASE,
    )
    ok, problems = identity_engine.validate(identity.as_dict())
    assert ok, problems


def test_the_schema_rejects_an_unknown_field(tesoro):
    identity = tesoro.identities.register(agent_id="agent-1", purpose="p", now=BASE)
    payload = identity.as_dict()
    payload["vendorExtension"] = True
    assert identity_engine.validate(payload)[0] is False


def test_an_identity_round_trips(tesoro):
    original = tesoro.identities.register(
        agent_id="agent-1", purpose="round trip", controller=ACME,
        per_action_usd="0.02", daily_usd="1.00", now=BASE,
    )
    assert Identity.from_dict(original.as_dict()) == original


def test_the_plugin_registers_and_reads_back(gov):
    gov.register_identity(purpose="buy data", controller=ACME, per_action_usd="0.02")
    identity = gov.identity()
    assert identity is not None
    assert identity.purpose == "buy data"
    assert gov.report()["identity"]["purpose"] == "buy data"


def test_identities_are_per_agent(tmp_path):
    store = IdentityStore(tmp_path / "identities.json")
    store.register(agent_id="a", purpose="a", now=BASE)
    assert store.get("b") is None
    assert store.get("a") is not None
