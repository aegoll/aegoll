"""AEGS Decision Record — the evidence a governed action produces.

These test the *interface*, not AEGL's internals. The record has to be emittable by
an implementation that shares none of this code, and readable by an auditor who
knows nothing about our engines. So the assertions are about what a reader can
conclude from a record, and about the distinctions the schema exists to keep.

The load-bearing one: **absent, `assessed: false`, `null` and `0` mean four
different things.** This project has already conflated two of them once — an
unmeasured vendor history was rendered as `0` and every advisor read it as the fact
that the counterparty was a stranger. A record that repeats that mistake is worse
than no record, because it looks like evidence.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from aegoll import record as record_mod
from aegoll.plugin import Governor
from aegoll.record import can_validate

BASE = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def gov(tmp_path):
    g = Governor(advisor=None, data_dir=tmp_path, framework="test-harness")
    yield g
    g.close()


def records_for(gov) -> list[dict]:
    return record_mod.records_from_journal(gov.aegoll.audit.entries())


class FakeQuote:
    price_usd = "0.001"


class FakeCall:
    payment_status = "settled"
    transaction = "0xabc123"


class FakeBuyer:
    address = "0xFAKE"
    spend_cap_usd = 1.0
    total_spent_usd = 0.0

    def __init__(self) -> None:
        self.calls: list = []

    async def quote(self, path: str):
        return FakeQuote()

    async def get_free(self, path: str):
        return {}

    async def get_paid(self, path: str):
        return FakeCall()

    def budget_snapshot(self) -> dict:
        return {}

    async def aclose(self) -> None:
        return None


# --- the schema holds against real journals -------------------------------


@pytest.mark.skipif(
    not can_validate(),
    reason=(
        "schema validation needs the `schema` extra (jsonschema). Skipped rather than "
        "failed: without a validator this test cannot distinguish an invalid record from "
        "an unchecked one, which is the very thing it asserts. "
        "test_ci_can_validate_schemas keeps this from becoming permanent."
    ),
)
def test_every_decision_produces_a_valid_record(gov):
    gov.authorize_run(model="claude-haiku-4-5", provider="anthropic", budget_usd=0.03)
    gov.authorize_run(model="m", provider="openai", budget_usd=0.10)  # refused

    records = records_for(gov)
    valid, problems = record_mod.validate_all(records)

    assert len(records) == 2
    assert valid == 2, "schema-invalid records:\n  " + "\n  ".join(problems)


def test_a_malformed_record_is_rejected():
    """A validator that accepts anything is not a conformance surface."""
    ok, problems = record_mod.validate({"aegsVersion": "0.1"})
    assert ok is False
    assert problems


@pytest.mark.skipif(
    not can_validate(),
    reason=(
        "schema validation needs the `schema` extra (jsonschema). Skipped rather than "
        "failed: without a validator this test cannot distinguish an invalid record from "
        "an unchecked one, which is the very thing it asserts. "
        "test_ci_can_validate_schemas keeps this from becoming permanent."
    ),
)
def test_an_unknown_field_is_rejected(gov):
    """`additionalProperties: false` — a vendor cannot smuggle meaning into a record
    and still claim conformance."""
    gov.authorize_run(model="m", budget_usd=0.03)
    record = records_for(gov)[0]
    record["vendorSpecificScore"] = 0.9

    ok, problems = record_mod.validate(record)
    assert ok is False
    assert any("vendorSpecific" in p for p in problems)


def test_a_wrong_version_is_rejected(gov):
    gov.authorize_run(model="m", budget_usd=0.03)
    record = records_for(gov)[0]
    record["aegsVersion"] = "0.2"
    assert record_mod.validate(record)[0] is False


# --- money is never a float -----------------------------------------------


def test_amounts_are_decimal_strings_not_floats(gov):
    """Float arithmetic is not associative; a record that round-trips through one
    cannot be reconciled against a ledger."""
    gov.authorize_run(model="m", budget_usd=0.03)
    record = records_for(gov)[0]

    assert isinstance(record["action"]["amount"], str)
    assert record["action"]["amount"] == "0.030000"
    for env in record["budgetState"]["envelopes"]:
        assert isinstance(env["limit"], str)
        assert isinstance(env["used"], str)


def test_the_amount_pattern_rejects_a_float(gov):
    gov.authorize_run(model="m", budget_usd=0.03)
    record = records_for(gov)[0]
    record["action"]["amount"] = 0.03  # a float, not a string
    assert record_mod.validate(record)[0] is False


# --- absent / not-run / unknown / zero are four different things -----------


def test_a_control_this_implementation_lacks_is_absent_not_zero(gov):
    """AEGL has no AML engine. Reporting `aml: {score: 0}` would claim a clean
    screening that never happened."""
    gov.authorize_run(model="m", budget_usd=0.03)
    assessments = records_for(gov)[0]["assessments"]

    assert "aml" not in assessments
    assert "sanctions" not in assessments
    assert "fraud" not in assessments
    assert set(assessments) <= set(record_mod.IMPLEMENTED_CONTROLS)


def test_unknown_roi_stays_null_and_never_becomes_zero(gov):
    """`ratio: null` means no declared expected value. Coercing it to 0 would
    assert the purchase was worthless — a different, much stronger claim."""
    gov.authorize_run(model="m", budget_usd=0.03)
    roi = records_for(gov)[0]["assessments"]["roi"]

    assert roi["assessed"] is True
    assert roi["score"] is None
    assert roi["level"] is None or roi["level"].endswith("x")


def test_an_unimplemented_concept_is_null_not_omitted(gov):
    """AEGL has no intent model yet. `null` says 'not modelled'; omitting the key
    would say the concept does not exist in the standard."""
    gov.authorize_run(model="m", budget_usd=0.03)
    record = records_for(gov)[0]

    assert "intentId" in record
    assert record["intentId"] is None


def test_a_measured_zero_is_still_reported(gov):
    """The mirror of the above: a control that ran and found zero says so."""
    gov.authorize_run(model="m", budget_usd=0.03)
    trust = records_for(gov)[0]["assessments"]["trust"]
    assert trust["assessed"] is True
    assert isinstance(trust["score"], float)


# --- attribution ----------------------------------------------------------


def test_a_refusal_names_the_engine_that_caused_it(gov):
    """A REJECT with no attributable cause is not auditable evidence."""
    gov.authorize_run(model="m", budget_usd=0.10)  # over the $0.04 envelope

    record = records_for(gov)[0]
    assert record["decision"] == "REJECT"
    assert record["authorization"]["decidingEngine"] == "treasury"
    assert record["authorization"]["matchedRule"]
    assert record["authorization"]["reasons"]


def test_the_deterministic_verdict_is_recorded_beside_the_final_one(gov):
    """So a reader can see whether a model moved the outcome, and which way."""
    gov.authorize_run(model="m", budget_usd=0.03)
    auth = records_for(gov)[0]["authorization"]
    assert auth["deterministicVerdict"] == records_for(gov)[0]["decision"]


def test_the_two_channels_are_distinguishable(gov):
    import asyncio

    gov.authorize_run(model="m", budget_usd=0.03)
    asyncio.run(gov.wrap(FakeBuyer()).get_paid("/market/snapshot"))

    channels = {r["action"]["channel"] for r in records_for(gov)}
    assets = {r["action"]["asset"] for r in records_for(gov)}
    assert channels == {"internal", "external"}
    assert assets == {"USD", "USDC"}


def test_a_per_call_cap_is_marked_as_such(gov):
    """Carrying `cumulative` stops a consumer re-making the cockpit's mistake,
    where a cap rendered as a spent budget said the opposite of what it meant."""
    gov.authorize_run(model="m", budget_usd=0.03)
    envelopes = {e["name"]: e for e in records_for(gov)[0]["budgetState"]["envelopes"]}

    assert envelopes["per_transaction"]["cumulative"] is False
    assert envelopes["daily"]["cumulative"] is True


# --- evidence -------------------------------------------------------------


def test_a_record_is_bound_to_the_chain(gov):
    """Without a hash binding it to a tamper-evident log, a record is a claim."""
    gov.authorize_run(model="m", budget_usd=0.03)
    evidence = records_for(gov)[0]["evidence"]

    assert len(evidence["evidenceHash"]) >= 16
    assert evidence["chainSequence"] is not None
    assert evidence["previousHash"] is not None
    assert evidence["decisionHash"]


def test_the_evidence_hash_matches_the_journal_entry(gov):
    gov.authorize_run(model="m", budget_usd=0.03)
    entry = [e for e in gov.aegoll.audit.entries() if e.payload.get("decision")][0]
    assert records_for(gov)[0]["evidence"]["evidenceHash"] == entry.entry_hash


def test_the_policy_version_is_a_content_hash(gov):
    """A label can be reused across edited rules; a hash cannot."""
    record = records_for(gov)[0] if records_for(gov) else None
    gov.authorize_run(model="m", budget_usd=0.03)
    record = records_for(gov)[0]
    assert record["policy"]["version"] == gov.bundle.hash


def test_a_human_override_appears_on_the_record(gov):
    """An override that left no trace would make the log a record of what policy
    would have done rather than what happened."""
    pre = gov.precheck_run(model="m", budget_usd=0.10)
    gov.authorize_run(model="m", budget_usd=0.10)
    gov.record_override(pre, seconds_left=6.0)

    record = [r for r in records_for(gov) if r["humanReview"]]
    assert record, "the override is not visible on any record"
    assert record[0]["humanReview"]["type"] == "override"
    assert record[0]["humanReview"]["overrodeVerdict"] == "REJECT"


def test_settlement_is_folded_in_from_its_own_entry(gov):
    """Settlements arrive as separate append-only entries; the log is never edited."""
    import asyncio

    asyncio.run(gov.wrap(FakeBuyer()).get_paid("/market/snapshot"))

    external = [r for r in records_for(gov) if r["action"]["channel"] == "external"]
    assert external[0]["settlement"]["settled"] is True
    assert external[0]["settlement"]["reference"] == "0xabc123"


def test_an_unsettled_action_reports_null_not_false_success(gov):
    """An authorised payment is not a completed one."""
    gov.authorize_run(model="m", budget_usd=0.03)
    assert records_for(gov)[0]["settlement"] is None


# --- portability ----------------------------------------------------------


def test_the_record_names_the_implementation_that_made_it(gov):
    """Comparing two implementations requires knowing which produced which record."""
    gov.authorize_run(model="m", budget_usd=0.03)
    impl = records_for(gov)[0]["implementation"]
    assert impl["name"] == "aegoll"
    assert impl["framework"] == "test-harness"
    assert impl["rail"] == "x402"


def test_records_are_json_serialisable(gov):
    gov.authorize_run(model="m", budget_usd=0.03)
    json.dumps(records_for(gov))


def test_a_settlement_update_alone_yields_no_record(gov):
    """Only decisions produce records; updates fold into the decision they refer to."""
    import asyncio

    asyncio.run(gov.wrap(FakeBuyer()).get_paid("/market/snapshot"))
    entries = gov.aegoll.audit.entries()

    assert len(entries) > len(records_for(gov))
    update = [e for e in entries if e.payload.get("settlement_update")][0]
    with pytest.raises(ValueError):
        record_mod.from_audit_entry(update)


def test_the_schema_ships_with_the_project():
    """A schema a second implementation cannot read is not a standard."""
    schema = record_mod.load_schema()
    assert schema["version"] == record_mod.AEGS_VERSION
    assert schema["$id"].endswith("decision-record-0.1.json")
    assert "additionalProperties" in schema
