"""The agent's own spending baseline: how it is bounded, and what it says when it knows nothing.

Two defects, found while writing the behavioural-monitoring design doc rather than by any check,
and both invisible because neither changed a verdict:

**A11.3a.** `agent_amounts` was `ORDER BY at DESC LIMIT 200` and nothing else — a *count* window.
History was evicted by volume, not by age, so 200 trivial actions erased an agent's real spending
record from the risk baseline entirely.

**A11.3b.** With the record erased, every amount was identical, standard deviation was zero, and
`amount_zscore` returned a hardcoded **6.0** — for $0.01 and for $100,000 alike. The function
stopped measuring anything, and the fabricated sigma flowed into `risk.score`, the journal and
every report as though it had been computed.

Neither loosened a verdict, which is why they survived: 6.0 saturates against
`zscore_saturation: 4.0`, so the risk term pinned at its maximum — the conservative direction.
An input weighted `weight_zscore: 0.20` had silently become a constant, and nothing said so.
"""

from __future__ import annotations

import random
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from tesoro.clock import FixedClock
from tesoro.config import available_bundles, load_bundle
from tesoro.domain import Vendor, Verdict
from tesoro.engines.risk.risk import evaluate as risk_evaluate
from tesoro.runtime import Paths, Tesoro
from tesoro.store import BASELINE_ROW_CAP

BASE = datetime(2026, 8, 15, 6, 0, tzinfo=timezone.utc)
BUNDLE = load_bundle(next(p for p in available_bundles() if p.stem == "default"))


def _gov(amounts, *, days_ago=3, spacing_minutes=1):
    """A governor whose agent has already settled `amounts`, most recent last."""
    g = Tesoro(
        bundle=BUNDLE,
        paths=Paths.ephemeral(tempfile.mkdtemp()),
        clock=FixedClock(BASE),
        agent_id="baseline-agent",
    )
    for i, a in enumerate(amounts):
        g.store.record(
            tx_id=f"b{i}",
            at=BASE - timedelta(days=days_ago) + timedelta(minutes=i * spacing_minutes),
            agent_id=g.agent_id, vendor_id="v", resource="/r",
            amount_atomic=int(round(a * 1_000_000)),
            verdict=Verdict.APPROVE, settled=True, success=True,
        )
    return g


def _snapshot(g, amount_usd="9"):
    return g.snapshot_for(
        g.build_request(resource="/r", amount_usd=amount_usd, vendor=Vendor(id="v"))
    )


def _varied(n=40, seed=7):
    random.seed(seed)
    return [round(random.uniform(0.02, 2.00), 6) for _ in range(n)]


# --- A11.3a: volume must not evict history -----------------------------------


def test_trivial_actions_no_longer_erase_a_real_spending_history():
    """The measured defect. 40 varied purchases, then 200 of $0.001.

    Under a 200-row count window the 40 were gone and every retained row was $0.001. The
    baseline is bounded by *time* first now, so all 240 are in the window and the varied
    purchases still shape it.
    """
    g = _gov(_varied() + [0.001] * 200)
    try:
        snap = _snapshot(g)
        assert len(snap.agent_amounts) == 240, len(snap.agent_amounts)
        assert snap.agent_stdev_atomic > 0, (
            "standard deviation collapsed to zero: the varied history was evicted again"
        )
        assert snap.dispersion_state() == "measured"
        z = snap.amount_zscore(9_000_000)
        assert z is not None and z > 3, z
    finally:
        g.close()


def test_the_baseline_is_bounded_by_age_as_well_as_volume():
    """Settlements older than the 30-day window do not define what is normal today.

    Every other aggregate in the snapshot uses 30 days. This one used no time bound at all, so
    a purchase from eight months ago carried the same weight as one from this morning.
    """
    g = _gov([5.0] * 10, days_ago=200)          # ancient, outside the window
    try:
        for i in range(5):                       # recent, inside it
            g.store.record(
                tx_id=f"r{i}", at=BASE - timedelta(days=1, minutes=i),
                agent_id=g.agent_id, vendor_id="v", resource="/r",
                amount_atomic=10_000, verdict=Verdict.APPROVE, settled=True, success=True,
            )
        snap = _snapshot(g)
        assert len(snap.agent_amounts) == 5, (
            f"{len(snap.agent_amounts)} amounts in the baseline; the 200-day-old ones are back"
        )
        assert set(snap.agent_amounts) == {10_000}
    finally:
        g.close()


def test_truncation_is_reported_rather_than_silent():
    """A statistic over the most recent N actions is not the statistic it appears to be.

    The row cap still exists as a memory guard. What changed is that hitting it is observable:
    silently returning a volume-limited sample is how the original defect stayed hidden.
    """
    g = _gov([0.001] * (BASELINE_ROW_CAP + 5), spacing_minutes=1)
    try:
        snap = _snapshot(g)
        assert snap.baseline_truncated is True
        assert len(snap.agent_amounts) == BASELINE_ROW_CAP

        score = risk_evaluate(
            g.build_request(resource="/r", amount_usd="9", vendor=Vendor(id="v")), snap,
            BUNDLE.risk,
        )
        assert "baseline_truncated" in score.flags
        term = next(t for t in score.terms if t.name == "amount_zscore")
        assert "truncated" in term.detail, term.detail
    finally:
        g.close()


def test_an_untruncated_baseline_does_not_claim_to_be_truncated():
    g = _gov(_varied())
    try:
        snap = _snapshot(g)
        assert snap.baseline_truncated is False
        score = risk_evaluate(
            g.build_request(resource="/r", amount_usd="9", vendor=Vendor(id="v")), snap,
            BUNDLE.risk,
        )
        assert "baseline_truncated" not in score.flags
    finally:
        g.close()


# --- A11.3b: no fabricated sigma ---------------------------------------------


def test_a_flat_baseline_yields_no_zscore_at_all():
    """The fabricated 6.0 is gone, and it is not replaced by another number.

    With zero dispersion, "how many standard deviations out" has no answer. Every amount below
    used to score exactly 6.0, which is why the function had stopped measuring.
    """
    g = _gov([0.001] * 200)
    try:
        snap = _snapshot(g)
        assert snap.dispersion_state() == "no_spread"
        for amount in (10_000, 1_000_000, 100_000_000, 100_000_000_000):
            assert snap.amount_zscore(amount) is None, amount
    finally:
        g.close()


def test_the_three_reasons_for_having_no_zscore_are_distinguishable():
    """Absent is not unknown is not zero, applied to a statistic.

    `no_baseline` says nothing is known about this agent. `no_spread` says a great deal: every
    amount ever seen was identical. Returning `None` for both without a way to tell them apart
    would repeat F-A10 in a smaller place.
    """
    empty = _gov([])
    two = _gov([0.5, 0.5])
    flat = _gov([0.001] * 5)
    spread = _gov(_varied())
    try:
        assert _snapshot(empty).dispersion_state() == "no_baseline"
        assert _snapshot(two).dispersion_state() == "no_baseline"
        assert _snapshot(flat).dispersion_state() == "no_spread"
        assert _snapshot(spread).dispersion_state() == "measured"
    finally:
        for g in (empty, two, flat, spread):
            g.close()


def test_an_amount_outside_a_flat_baseline_still_scores_maximally():
    """The signal the fabricated magnitude was carrying, kept without the magnitude.

    This is the assertion that stops the fix from loosening anything. Old behaviour:
    `z = 6.0`, so `z_norm = min(1.0, 6.0 / 4.0) = 1.0`. New behaviour: the same 1.0, reached by
    naming the condition instead of inventing a sigma.

    Dropping the zero-dispersion case into the `no baseline` branch would have made this 0.0 and
    **relaxed** the verdict, which is the wrong direction for a governance layer.
    """
    g = _gov([0.001] * 200)
    try:
        snap = _snapshot(g)
        assert snap.differs_from_flat_baseline(100_000_000) is True
        score = risk_evaluate(
            g.build_request(resource="/r", amount_usd="100", vendor=Vendor(id="v")), snap,
            BUNDLE.risk,
        )
        term = next(t for t in score.terms if t.name == "amount_zscore")
        assert term.value == 1.0, term.value
        assert "amount_outside_flat_baseline" in score.flags
        assert "unquantifiable" in term.detail, term.detail
        assert "z=" not in term.detail, (
            f"the detail still quotes a sigma: {term.detail}"
        )
    finally:
        g.close()


def test_an_amount_matching_a_flat_baseline_is_not_anomalous():
    """The other half. Identical to every prior amount is the *least* surprising case."""
    g = _gov([0.001] * 200)
    try:
        snap = _snapshot(g, amount_usd="0.001")
        assert snap.differs_from_flat_baseline(1_000) is False
        score = risk_evaluate(
            g.build_request(resource="/r", amount_usd="0.001", vendor=Vendor(id="v")), snap,
            BUNDLE.risk,
        )
        term = next(t for t in score.terms if t.name == "amount_zscore")
        assert term.value == 0.0, term.value
        assert "amount_outside_flat_baseline" not in score.flags
        assert "no deviation to measure" in term.detail, term.detail
    finally:
        g.close()


def test_no_baseline_says_so_and_does_not_claim_a_flat_one():
    """An agent with no history must not be described as having a flat one."""
    g = _gov([])
    try:
        snap = _snapshot(g)
        assert snap.differs_from_flat_baseline(9_000_000) is False
        score = risk_evaluate(
            g.build_request(resource="/r", amount_usd="9", vendor=Vendor(id="v")), snap,
            BUNDLE.risk,
        )
        term = next(t for t in score.terms if t.name == "amount_zscore")
        assert term.value == 0.0
        assert "no baseline yet" in term.detail, term.detail
    finally:
        g.close()


@pytest.mark.parametrize("amount", [10_000, 1_000_000, 100_000_000])
def test_the_zscore_no_longer_returns_the_same_number_for_every_amount(amount):
    """A regression guard aimed at the exact shape of the bug.

    The old function answered 6.0 for every one of these against a flat baseline. Any future
    implementation that resumes returning a constant fails here.
    """
    g = _gov([0.001] * 200)
    try:
        snap = _snapshot(g)
        assert snap.amount_zscore(amount) != 6.0
    finally:
        g.close()
