"""The cross-framework rollup.

Pure aggregation over the audit journal, so it is tested without a browser. The
properties that matter are about *honesty*: nothing invented, nothing silently
dropped, and no number attributed to a framework the record does not name.
"""

from __future__ import annotations

import pytest

from aegl import crossview
from aegl.plugin import Governor


class Entry:
    """The two attributes `crossview` reads off an audit entry."""

    def __init__(self, payload: dict, seq: int = 0, at: str = "2026-08-15T00:00:00") -> None:
        self.payload = payload
        self.seq = seq
        self.at = at


def decision_entry(
    framework: str | None = "langgraph",
    provider: str = "gemini",
    verdict: str = "APPROVE",
    resource: str = "/market/snapshot",
    amount: float = 0.001,
    tx_id: str = "req-1",
    advice: dict | None = None,
    changed: bool = False,
    budget_ok: bool = True,
) -> Entry:
    payload = {
        "transaction": {"id": tx_id, "resource": resource, "amountUsd": amount},
        "decision": {
            "verdict": verdict,
            "matchedRule": "auto-approve-micro",
            "reasons": [],
            "budget": {"ok": budget_ok},
            "risk": {"flags": []},
        },
        "settlement": (
            {"advice": advice, "changed": changed, "finalVerdict": verdict}
            if advice is not None
            else None
        ),
    }
    if framework is not None:
        payload["labels"] = {"framework": framework, "provider": provider}
    return Entry(payload)


def update_entry(tx_id: str, kind: str) -> Entry:
    return Entry({"settlement_update": {"requestId": tx_id, "type": kind}})


# --- grouping -------------------------------------------------------------


def test_decisions_group_by_the_framework_that_made_them():
    rolls = crossview.rollup(
        [
            decision_entry(framework="langgraph"),
            decision_entry(framework="langgraph"),
            decision_entry(framework="google-adk", provider="gemini"),
        ]
    )
    assert rolls["langgraph"].decisions == 2
    assert rolls["google-adk"].decisions == 1


def test_the_same_provider_on_two_frameworks_stays_separate():
    """The exact case `agent_id` or provider-derived grouping would collapse."""
    rolls = crossview.rollup(
        [
            decision_entry(framework="langgraph", provider="gemini"),
            decision_entry(framework="google-adk", provider="gemini"),
        ]
    )
    assert set(rolls) == {"langgraph", "google-adk"}
    assert rolls["langgraph"].providers == {"gemini"}
    assert rolls["google-adk"].providers == {"gemini"}


def test_unlabelled_history_is_shown_not_dropped():
    """A view that silently omits history is worse than one that admits it."""
    rolls = crossview.rollup([decision_entry(framework=None), decision_entry()])
    assert rolls[crossview.UNLABELLED].decisions == 1
    summary = crossview.summarise([decision_entry(framework=None), decision_entry()])
    assert summary["unlabelled"] == 1
    # ...and unlabelled rows do not inflate the framework count.
    assert summary["totals"]["frameworks"] == 1


def test_settlement_updates_alone_are_not_counted_as_decisions():
    rolls = crossview.rollup([decision_entry(), update_entry("req-1", "settled")])
    assert rolls["langgraph"].decisions == 1


# --- the two channels stay separate ---------------------------------------


def test_internal_and_external_spend_are_split_by_resource():
    rolls = crossview.rollup(
        [
            decision_entry(resource="llm:claude-haiku-4-5", amount=0.03, tx_id="a"),
            decision_entry(resource="/market/signal", amount=0.01, tx_id="b"),
        ]
    )
    roll = rolls["langgraph"]
    assert roll.internal_usd == pytest.approx(0.03)
    assert roll.external_usd == pytest.approx(0.01)
    assert roll.total_usd == pytest.approx(0.04)


# --- refusals -------------------------------------------------------------


def test_a_refusal_is_attributed_to_the_engine_that_caused_it():
    rolls = crossview.rollup(
        [decision_entry(verdict="REJECT", budget_ok=False, tx_id="a")]
    )
    roll = rolls["langgraph"]
    assert roll.refused == 1
    assert roll.approved == 0
    assert roll.engines["treasury"] == 1


def test_approval_rate_is_computed_over_that_frameworks_own_decisions():
    rolls = crossview.rollup(
        [
            decision_entry(tx_id="a"),
            decision_entry(tx_id="b"),
            decision_entry(tx_id="c", verdict="REVIEW"),
            decision_entry(tx_id="d", framework="google-adk", verdict="REJECT"),
        ]
    )
    assert rolls["langgraph"].approval_rate == pytest.approx(2 / 3)
    assert rolls["google-adk"].approval_rate == 0.0


# --- the advisor ----------------------------------------------------------


def test_advisor_calls_and_cost_roll_up_per_framework():
    rolls = crossview.rollup(
        [
            decision_entry(tx_id="a", advice={"costUsd": 0.000112}, changed=True),
            decision_entry(tx_id="b", advice={"costUsd": 0.000112}),
            decision_entry(tx_id="c"),  # gate stayed shut: no advice recorded
        ]
    )
    roll = rolls["langgraph"]
    assert roll.advisor_calls == 2
    assert roll.advisor_changed == 1
    assert roll.advisor_cost_usd == pytest.approx(0.000224)


# --- the ceiling ----------------------------------------------------------


def test_a_ceiling_stop_is_attributed_through_its_request_id():
    entries = [
        decision_entry(tx_id="run-1", resource="llm:m", amount=0.02),
        update_entry("run-1", "spend_ceiling_stop"),
    ]
    assert crossview.rollup(entries)["langgraph"].ceiling_stops == 1


def test_overrides_are_counted_globally_never_guessed_at():
    """An override carries a synthetic request id no decision has.

    Attributing it to a framework would make the view look more certain than the
    record is.
    """
    entries = [decision_entry(), Entry({"settlement_update": {
        "requestId": "override-claude-haiku-4-5", "type": "human_override"}})]

    assert crossview.count_overrides(entries) == 1
    assert crossview.summarise(entries)["totals"]["overrides"] == 1
    # And it did not get pinned on the one framework that happens to be present.
    assert not hasattr(crossview.rollup(entries)["langgraph"], "overrides")


# --- recent rows ----------------------------------------------------------


def test_recent_lists_newest_first_and_names_the_framework():
    entries = [
        decision_entry(tx_id="a", framework="langgraph"),
        decision_entry(tx_id="b", framework="google-adk"),
    ]
    rows = crossview.recent(entries)
    assert [r["framework"] for r in rows] == ["google-adk", "langgraph"]
    assert rows[0]["channel"] == "external"


def test_recent_labels_the_internal_channel():
    rows = crossview.recent([decision_entry(resource="llm:gpt-4o-mini")])
    assert rows[0]["channel"] == "internal"


def test_recent_respects_its_limit():
    entries = [decision_entry(tx_id=str(i)) for i in range(50)]
    assert len(crossview.recent(entries, limit=10)) == 10


def test_an_empty_journal_summarises_to_zeroes():
    summary = crossview.summarise([])
    assert summary["frameworks"] == []
    assert summary["totals"]["decisions"] == 0


# --- end to end against a real Governor -----------------------------------


def test_a_real_governor_stamps_a_framework_that_the_rollup_reads(tmp_path):
    """The stamp and the reader must agree, or the view is empty for no reason."""
    gov = Governor(advisor=None, data_dir=tmp_path, framework="langgraph")
    try:
        gov.authorize_run(model="gemini-flash-latest", provider="gemini", budget_usd=0.02)
        summary = crossview.summarise(gov.aegl.audit.entries())
    finally:
        gov.close()

    assert summary["totals"]["frameworks"] == 1
    row = summary["frameworks"][0]
    assert row["framework"] == "langgraph"
    assert row["providers"] == ["gemini"]
    assert row["internalUsd"] == pytest.approx(0.02)


def test_two_governors_appear_side_by_side(tmp_path):
    """The whole point of the view: one journal, several frameworks."""
    for name, model in (("langgraph", "gemini-flash-latest"), ("google-adk", "m")):
        gov = Governor(advisor=None, data_dir=tmp_path, framework=name)
        try:
            gov.authorize_run(model=model, provider="gemini", budget_usd=0.02)
        finally:
            gov.close()

    gov = Governor(advisor=None, data_dir=tmp_path)
    try:
        summary = crossview.summarise(gov.aegl.audit.entries())
    finally:
        gov.close()

    assert {f["framework"] for f in summary["frameworks"]} == {"langgraph", "google-adk"}
    assert summary["totals"]["frameworks"] == 2


def test_labelling_does_not_touch_agent_id(tmp_path):
    """Envelopes are agent-scoped; labelling must not split a shared budget."""
    gov = Governor(advisor=None, data_dir=tmp_path, framework="langgraph")
    try:
        assert gov.aegl.agent_id == "agent-1"
    finally:
        gov.close()
