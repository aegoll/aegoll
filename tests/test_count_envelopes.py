"""Count envelopes over a long window.

AEGS-0.1-ENV-7 already permits envelopes that limit the number of actions in a window, and
already fixes their semantics: evaluated against the count already recorded, independent of the
amount being decided. tesoro already implemented it -- as `velocity_60s` and `velocity_1h`.

What was missing was any window longer than an hour, and that is the whole of the gap. A rate
limit bounds the rate; no product of a rate limit and a duration is ever compared against
anything, so an hourly ceiling of 100 implies no daily ceiling of 2,400 -- it implies none at
all. Measured before this existed: 97 actions an hour sustained ran 200 actions over two hours
with nothing refused, and would have produced 2,328 a day for $2.33 against a $50 budget.
See EXP-009.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from tesoro.clock import FixedClock
from tesoro.config import PolicyError, load_bundle, available_bundles
from tesoro.domain import Vendor, Verdict
from tesoro.runtime import Paths, Tesoro

#: 06:00 UTC, so a run of several hundred actions at 37-second spacing stays inside one
#: calendar day -- the window `actions_per_day` actually measures.
BASE = datetime(2026, 8, 15, 6, 0, tzinfo=timezone.utc)
SELLER = "count-desk"


def gov(tmp_path, **overrides):
    bundle = load_bundle(next(p for p in available_bundles() if p.stem == "default"))
    if overrides:
        # `replace`, not assignment: both are frozen, which is the point. A policy bundle that
        # could be mutated after loading would make its content hash a claim about the file
        # rather than about the rules in force.
        bundle = replace(bundle, treasury=replace(bundle.treasury, **overrides))
    return Tesoro(
        bundle=bundle,
        paths=Paths.ephemeral(tmp_path),
        clock=FixedClock(BASE),
        agent_id="counter",
    )


def _decide(g, amount="0.001", *, at=None, resource="/market/snapshot"):
    return g.decide(
        g.build_request(resource=resource, amount_usd=amount, vendor=Vendor(id=SELLER)), at
    )


def _pace(g, count, spacing_s, amount="0.001"):
    """Take `count` actions, recording each approval. Returns (stopped_at, control, codes)."""
    for i in range(count):
        at = BASE + timedelta(seconds=i * spacing_s)
        d = _decide(g, amount, at=at)
        if d.verdict is not Verdict.APPROVE:
            codes = [
                f"{r.source}/{r.code}" for r in d.reasons
                if r.verdict and r.verdict is not Verdict.APPROVE
            ]
            return i, d.attributed_control, codes
        g.store.record(
            tx_id=f"c{i}", at=at, agent_id=g.agent_id, vendor_id=SELLER,
            resource="/market/snapshot", amount_atomic=int(float(amount) * 1_000_000),
            verdict=Verdict.APPROVE, settled=True, success=True,
        )
    return None, None, []


# --- the finding this closes -------------------------------------------------


def test_pacing_under_the_hourly_ceiling_is_now_bounded_by_the_day(tmp_path):
    """The measured attack: 97 actions an hour, 3% under `velocity_1h: 100`.

    Before `actions_per_day` existed this ran indefinitely. It is now refused at the daily
    count limit, and the attribution must name *that* envelope -- a refusal citing `daily_usd`
    would mean a value threshold moved rather than a count envelope arriving, which is a
    different claim entirely.
    """
    g = gov(tmp_path)
    try:
        stopped_at, control, codes = _pace(g, count=600, spacing_s=37)
        assert stopped_at == 500, f"refused at action {stopped_at}, expected the 500th"
        assert control == "treasury", control
        assert any("actions_per_day" in c for c in codes), (
            f"refused by {codes} -- a count envelope must be what refused this, not a value one"
        )
    finally:
        g.close()


def test_the_hourly_rate_limit_still_binds_first_when_the_rate_is_high(tmp_path):
    """The new envelope must not shadow the old one.

    At 21-second spacing the hourly counter reaches 100 long before the daily one reaches 500,
    so `velocity_1h` must still be what refuses. A count envelope that took over every refusal
    would make the rate limit unobservable.
    """
    g = gov(tmp_path)
    try:
        stopped_at, control, codes = _pace(g, count=200, spacing_s=21)
        assert stopped_at == 100, stopped_at
        assert control == "treasury"
        assert any("velocity_1h" in c for c in codes), codes
    finally:
        g.close()


def test_structuring_is_bounded_but_still_not_refused(tmp_path):
    """Stated as a test so it cannot be quietly overclaimed.

    Forty payments of $0.001 five minutes apart is 40 actions, nowhere near 500. A count
    envelope bounds the *mechanism* and does not refuse this *instance*, which is why the
    README, the CHANGELOG, the docs site and the specification's security section all still
    say structuring is undefended. If this test ever fails, that is the day those four
    documents change -- not a day to change this assertion.
    """
    g = gov(tmp_path)
    try:
        stopped_at, _, _ = _pace(g, count=40, spacing_s=300)
        assert stopped_at is None, (
            f"structuring was refused at action {stopped_at}. If a control now catches it, "
            "update the four documents that describe it as open."
        )
    finally:
        g.close()


# --- semantics required by ENV-7 and ENV-8 -----------------------------------


def test_the_action_that_reaches_the_limit_is_admitted(tmp_path):
    """ENV-7's boundary: `used < limit`, so the 500th action is the last permitted one.

    Written as the off-by-one it prevents. `used <= limit` yields a real limit of 501, and a
    record saying 500.
    """
    g = gov(tmp_path, actions_per_day=3)
    try:
        stopped_at, control, codes = _pace(g, count=5, spacing_s=60)
        assert stopped_at == 3, f"limit 3 refused action {stopped_at}; expected the fourth"
        assert any("actions_per_day" in c for c in codes), codes
    finally:
        g.close()


def test_a_count_envelope_counts_actions_whatever_they_cost(tmp_path):
    """ENV-7: evaluated against the count, independent of the amount being decided.

    The clause the gap depends on. An implementation that skipped trivial amounts as noise
    would reopen it exactly.
    """
    g = gov(tmp_path, actions_per_day=3)
    try:
        stopped_at, _, codes = _pace(g, count=5, spacing_s=60, amount="0")
        assert stopped_at == 3, "a zero-amount action did not count against the envelope"
        assert any("actions_per_day" in c for c in codes), codes
    finally:
        g.close()


def test_an_absent_count_envelope_constrains_nothing(tmp_path):
    """ENV-8, and the reason the loader refuses to default these to a number.

    `strict.yaml` declares neither key. Absent must mean unconstrained; if it meant zero,
    adding the section to a policy pack would be an outage and omitting it would be a freeze.
    """
    strict = load_bundle(next(p for p in available_bundles() if p.stem == "strict"))
    assert strict.treasury.actions_per_day is None
    assert strict.treasury.actions_per_month is None

    # The shipped `default` pack with *only* the two keys removed. Running `strict` instead
    # would vary more than one thing: it refuses the first action outright via
    # `review-new-vendor`, which says nothing about count envelopes. The first version of this
    # test read that refusal as "absent was treated as zero" and was measuring a different
    # rule entirely.
    g = gov(tmp_path, actions_per_day=None, actions_per_month=None)
    try:
        stopped_at, control, codes = _pace(g, count=600, spacing_s=37)
        assert not any("actions_per_" in c for c in codes), (
            f"an absent count limit refused action {stopped_at} via {codes} -- absent was "
            "treated as zero"
        )
        assert stopped_at is None, (
            f"refused at action {stopped_at} by {codes}; with both count limits absent this run "
            "is inside every value envelope and under every rate limit"
        )
    finally:
        g.close()


def test_zero_is_not_absent(tmp_path):
    """The other half. A declared zero forbids everything, and must not read as no limit."""
    g = gov(tmp_path, actions_per_day=0)
    try:
        stopped_at, control, codes = _pace(g, count=2, spacing_s=60)
        assert stopped_at == 0, "a limit of zero admitted an action"
        assert any("actions_per_day" in c for c in codes), codes
    finally:
        g.close()


def test_a_negative_count_limit_is_refused_at_load(tmp_path):
    """Neither absent nor zero. A negative count limit is not a policy, it is a typo."""
    pack = tmp_path / "bad.yaml"
    pack.write_text(
        "version: 1\nname: bad\nconfig:\n  treasury:\n    actions_per_day: -1\nrules: []\n",
        encoding="utf-8",
    )
    with pytest.raises(PolicyError, match="cannot be negative"):
        load_bundle(pack)


def test_channels_do_not_share_a_count_envelope():
    """ENV-8 for counts, and the reason the internal block declares its own numbers.

    The shipped pack must not let the external channel's 500-a-day figure -- chosen for
    payouts -- silently become the ceiling on inference calls.
    """
    default = load_bundle(next(p for p in available_bundles() if p.stem == "default"))
    assert default.treasury.actions_per_day == 500
    assert default.treasury_internal.actions_per_day == 60
    assert default.treasury.actions_per_day != default.treasury_internal.actions_per_day, (
        "the internal channel inherited the external count limit; declare it explicitly"
    )


def test_the_snapshot_reports_long_window_counts(tmp_path):
    """The facts the envelopes read. `count_last_1h` cannot answer a question about a day."""
    g = gov(tmp_path)
    try:
        for i in range(5):
            g.store.record(
                tx_id=f"s{i}", at=BASE + timedelta(hours=i * 2), agent_id=g.agent_id,
                vendor_id=SELLER, resource="/market/snapshot", amount_atomic=1_000,
                verdict=Verdict.APPROVE, settled=True, success=True,
            )
        snap = g.snapshot_for(
            g.build_request(resource="/market/snapshot", amount_usd="0.001",
                            vendor=Vendor(id=SELLER)),
            BASE + timedelta(hours=9),
        )
        assert snap.count_today == 5, snap.count_today
        assert snap.count_month == 5, snap.count_month
        assert snap.count_last_1h <= 1, (
            "the hourly counter should see at most the most recent action; if it sees all "
            "five, the windows are not what they claim"
        )
    finally:
        g.close()
