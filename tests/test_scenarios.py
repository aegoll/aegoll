"""Scenario A-D2 regression, plus the engine-isolation claim."""

from __future__ import annotations

import pytest

from aegl.scenarios import SCENARIOS, run_all, run_scenario
from aegl.domain import Verdict


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.key)
def test_scenario_matches_expectation(scenario):
    result = run_scenario(scenario)
    assert result.decision.verdict in scenario.expected, (
        f"scenario {scenario.key} returned {result.decision.verdict.value}, "
        f"expected one of {[v.value for v in scenario.expected]}. "
        f"Reasons: {result.decision.explain()}"
    )


def test_microtransaction_needs_no_inference():
    result = run_scenario(SCENARIOS[0])
    assert result.decision.verdict is Verdict.APPROVE
    assert result.decision.intelligence.eiap.would_invoke is False


def test_high_value_never_auto_approves():
    for scenario in SCENARIOS:
        if float(scenario.amount_usd) >= 100:
            result = run_scenario(scenario)
            assert result.decision.verdict is not Verdict.APPROVE, (
                f"scenario {scenario.key} auto-approved ${scenario.amount_usd}"
            )


def test_suspicious_affordable_is_decided_by_risk_not_budget():
    """D2 exists to prove the risk engine can bind on its own.

    D itself is refused on authority grounds ($500 breaches the balance envelope)
    before the risk rule is reached, so it cannot demonstrate this.
    """
    d2 = next(s for s in SCENARIOS if s.key == "D2")
    result = run_scenario(d2)
    assert result.decision.budget.ok, "D2 should be affordable, so budget must not object"
    assert result.decision.verdict is Verdict.ESCALATE
    assert result.decision.matched_rule == "escalate-high-risk"
    assert "high_risk" in result.decision.risk.flags


def test_all_scenarios_are_labelled_live_or_simulated():
    """Honesty check: exactly one scenario may claim to be live-capable."""
    live = [s for s in SCENARIOS if s.live]
    assert len(live) == 1 and live[0].key == "A", (
        "only scenario A is within the real seller's $0.001-$0.01 price range"
    )


def test_scenarios_are_reproducible():
    first = {r.scenario.key: r.decision.decision_hash for r in run_all()}
    second = {r.scenario.key: r.decision.decision_hash for r in run_all()}
    assert first == second
