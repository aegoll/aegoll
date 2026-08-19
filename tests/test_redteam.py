"""The red-team suite runs in CI, and its score cannot drift.

A11.1. Eighteen attacks, ported from the prototype's `security/redteam/`, scored against a
recorded baseline. Both directions of change fail:

* a defence that regressed is a vulnerability reintroduced;
* a gap that closed is a claim three documents currently make -- the CHANGELOG, the docs site
  and the specification's security section all say structuring is undefended -- and they must
  not go stale in the direction of overclaiming.

Either way the fix is `python -m redteam.baseline`, in the same commit as whatever moved the
score. A red-team number that updates itself is not a number anyone is accountable for.

The suite costs about three seconds and no tokens, because nothing in the decision path is a
model. That is the only reason a full adversarial run belongs in an ordinary test session.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

#: `redteam/` sits beside `src/` and is deliberately not part of the installed package -- it
#: forges request ids, rewrites journals on disk and drives the clock. Nothing a user of
#: `tesoro` should be able to import.
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REDTEAM = ROOT / "redteam"

#: Asserted rather than assumed. A missing directory would make every test below pass by
#: checking nothing, which is F-C1 and has happened five times in this project.
assert REDTEAM.is_dir(), f"{REDTEAM} is not there; the layout changed"

from redteam.attacks import CATALOGUE  # noqa: E402
from redteam.baseline import load as load_baseline  # noqa: E402
from redteam.runner import Outcome, report, run_all  # noqa: E402


@pytest.fixture(scope="module")
def live():
    """One run for the whole module. Each attack still gets its own ephemeral store."""
    return report(run_all())


def test_the_suite_is_not_empty_and_covers_four_threat_classes():
    assert len(CATALOGUE) == 18, f"the catalogue is {len(CATALOGUE)} attacks, not 18"
    classes = {a.threat_class for a in CATALOGUE}
    assert classes == {"numeric", "economic", "evidence", "authority"}, classes


def test_every_attack_has_a_handler(live):
    """An attack with no handler reports ERROR, and a suite of ERRORs looks like a clean run
    to anyone reading only the count of failures.

    This is the defect that made 28 AEGS vectors meaningless: the linter counted them and
    nothing executed them.
    """
    broken = [r for r in live["results"] if r["outcome"] == Outcome.ERROR.value]
    assert not broken, "attacks that did not execute: " + ", ".join(
        f"{r['id']} ({r['detail']})" for r in broken
    )


def test_the_baseline_covers_exactly_the_catalogue():
    """A new attack must be recorded, and a deleted one must not linger as a passing score."""
    recorded = set(load_baseline()["outcomes"])
    declared = {a.id for a in CATALOGUE}
    assert recorded == declared, (
        f"baseline and catalogue disagree; missing from baseline: {declared - recorded}, "
        f"stale in baseline: {recorded - declared}. Run `python -m redteam.baseline`."
    )


@pytest.mark.parametrize("attack", CATALOGUE, ids=lambda a: a.id)
def test_the_score_matches_the_baseline(attack, live):
    baseline = load_baseline()["outcomes"]
    got = next(r["outcome"] for r in live["results"] if r["id"] == attack.id)
    want = baseline[attack.id]

    if got == want:
        return

    direction = (
        "a defence REGRESSED -- this is a vulnerability, not a baseline problem"
        if want == Outcome.DEFENDED.value
        else "the score IMPROVED -- update the documents that still describe this as open"
    )
    pytest.fail(
        f"{attack.id} ({attack.title}) scored {got}, baseline says {want}.\n"
        f"  {direction}.\n"
        f"  Then regenerate with `python -m redteam.baseline` in the same commit."
    )


def test_defended_by_accident_is_never_counted_as_a_pass(live):
    """The scoring rule the suite exists for.

    A structuring attack that happens to trip a velocity counter is not defended: paced
    differently it succeeds, and the control the attack targets does not exist. Treating that as
    a pass is how a system certifies protection it lacks -- so `DEFENDED_BY_ACCIDENT` is its own
    outcome, and it is reported as a finding.
    """
    assert Outcome.DEFENDED_BY_ACCIDENT.value != Outcome.DEFENDED.value
    accidents = live["byAccident"]
    assert accidents == load_baseline()["byAccident"], (
        f"the set of accidental defences changed: {accidents}. "
        "Each one is a control that does not exist behind an attack that appears handled."
    )
    for aid in accidents:
        r = next(x for x in live["results"] if x["id"] == aid)
        assert r["refusedBy"] != r["shouldBeRefusedBy"], (
            f"{aid} is scored as an accident but was refused by the control it targets"
        )


def test_the_three_open_findings_are_still_the_three_we_publish(live):
    """The README, the CHANGELOG, the docs site and the specification's security section all
    name these three. If the suite's list and those documents diverge, one of them is lying,
    and the suite is the one that gets to be right.
    """
    assert set(live["undefended"]) == {
        "RT-ECON-001",  # microtransaction structuring
        "RT-ECON-004",  # velocity evasion
        "RT-EVID-002",  # journal truncation
    }, live["undefended"]


def test_no_attack_result_contradicts_its_stated_expectation(live):
    """`expected` records what we believe before running, so a mismatch means either a
    vulnerability or a misunderstanding of our own system -- both worth stopping for.

    It is kept at zero deliberately. Two entries inherited stale expectations from the
    prototype (journal truncation was believed defended; trust farming was believed open), and
    a suite that reports known gaps as surprises on every run buries the surprises that matter.
    """
    assert live["surprises"] == [], [
        f"{s['id']}: expected {s['expected']}, got {s['outcome']}" for s in live["surprises"]
    ]


def test_the_runner_does_not_reimplement_attribution():
    """The bug this port introduced and then removed.

    The ported `_refusing_source` walked `reversed(decision.reasons)` and took the last
    refusing one. On budget fragmentation the daily envelope binds with
    `treasury/envelope_exceeded:daily` and a policy rule then observes the same fact as
    `policy/rule:review-budget-exhausted`; being later, the observation won. The runner
    credited `policy` where `attributed_control` says `treasury`, and two of three apparent
    surprises were that artefact rather than anything about the layer.

    `Decision.attributed_control` says it in its own docstring: a report, a conformance run and
    that property disagreeing about which control refused would be three answers to a question
    with one. A red-team score derived from a shadow copy of the attribution rule measures the
    shadow.

    Checked by parsing the function rather than by grepping the file. The first version of this
    test searched the source text for the forbidden constructs and failed on **its own
    docstring**, which names them in order to explain them. That is the third time in this
    codebase a check has matched its own explanation -- see the `pinned-versions-exist` CI job,
    which had to start excluding comment lines for the same reason. Prose about a rule is not a
    violation of it, and a text search cannot tell the difference.
    """
    import ast

    tree = ast.parse((REDTEAM / "runner.py").read_text(encoding="utf-8"))
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "_refusing_source"),
        None,
    )
    assert fn is not None, "_refusing_source is gone; attribution moved somewhere unchecked"

    body = [n for n in ast.walk(fn) if not isinstance(n, ast.Constant)]
    attributes = {n.attr for n in body if isinstance(n, ast.Attribute)}
    names = {n.id for n in body if isinstance(n, ast.Name)}

    assert "attributed_control" in attributes, (
        "_refusing_source no longer reads the package's own attribution"
    )
    assert "reversed" not in names, "the runner is re-deriving attribution by reason order again"
    assert "CLAMP_ORIGIN" not in names, (
        "clamp origins are resolved by `_deciding_engine`; resolving them here is the same "
        "second implementation in a smaller place"
    )


def test_the_sdist_ships_the_suite_alongside_its_test():
    """`tests/test_redteam.py` was in the tarball and `redteam/` was not.

    The test asserts its suite directory exists rather than skipping -- deliberately, so a
    missing suite cannot pass as a green run -- which means the tarball's test session died at
    collection. Shipping a test without the code it exercises is the packaging form of the same
    mistake the assert exists to catch.

    Checked against `MANIFEST.in` rather than by building an sdist, because building one costs
    seconds and this only needs to catch the line being removed.
    """
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    directives = [
        line.strip() for line in manifest.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert any("redteam" in d for d in directives), (
        "MANIFEST.in no longer ships redteam/; the sdist will carry tests/test_redteam.py "
        "without the suite it imports, and collection will fail for anyone building from "
        "source. Directives found: " + "; ".join(directives)
    )


def test_the_hourly_rate_limit_works_and_the_paced_attack_probes_past_it():
    """The assertion that would have caught RT-ECON-004's parameters.

    `UNDEFENDED` only means something if the attack ran far enough to be refused. RT-ECON-004
    paced 30 actions at 21-second spacing -- 171 an hour on paper, but 30 actions over ten
    minutes, so the hourly counter never reached its ceiling of 100. It read as a hole in the
    rate limit. Run to 120 actions at that same spacing and action 100 **is** refused by
    `velocity_1h`.

    So this pins both halves: the rate limit works at the rate it claims, and the attack is
    probing the thing one step past it -- that no envelope counts *actions* over a window
    longer than an hour. Pacing 3% under the hourly ceiling is unbounded in total.

    Three attacks in this catalogue have now had parameters that could not reach the control
    they named. A count of undefended attacks is not a finding unless each one ran.
    """
    from datetime import timedelta

    from tesoro.domain import Verdict

    from redteam.runner import BASE, SELLER, _decide, _gov

    def run(count: int, spacing: int) -> tuple[int | None, str | None, list[str]]:
        gov = _gov()
        try:
            for i in range(count):
                at = BASE + timedelta(seconds=i * spacing)
                d = _decide(gov, "0.001", now=at)
                if d.verdict is not Verdict.APPROVE:
                    codes = [
                        f"{r.source}/{r.code}" for r in d.reasons
                        if r.verdict and r.verdict is not Verdict.APPROVE
                    ]
                    return i, d.attributed_control, codes
                gov.store.record(
                    tx_id=f"probe-{i}", at=at, agent_id=gov.agent_id, vendor_id=SELLER,
                    resource="/market/snapshot", amount_atomic=1_000,
                    verdict=Verdict.APPROVE, settled=True, success=True,
                )
            return None, None, []
        finally:
            gov.close()

    # Over the ceiling: refused, by the control that owns the ceiling.
    stopped_at, control, codes = run(count=120, spacing=21)
    assert stopped_at is not None, "velocity_1h did not fire at 171 actions/hour"
    assert stopped_at == 100, f"velocity_1h fired at {stopped_at}, not its declared 100"
    assert control == "treasury", f"the rate limit was attributed to {control!r}"
    assert any("velocity" in c for c in codes), codes

    # Just under it: two hours, 200 actions, nothing refused. This is the open finding.
    stopped_at, control, _ = run(count=200, spacing=37)
    assert stopped_at is None, (
        f"pacing at ~97/hour was refused at action {stopped_at} by {control!r}. If that is a "
        "new count envelope, RT-ECON-004 has been closed -- regenerate the baseline and update "
        "the four documents that describe velocity evasion as open."
    )
