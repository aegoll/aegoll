"""The governance surface: the Tier 1 API of `docs/api-surface.md` §3.

Two things these tests are for.

**That the documented surface exists and works.** It did not: `from tesoro import Governor`
returned the internal rules evaluator, so the README's own opening snippet raised
`AttributeError` on its third line. A public API described in a document and absent from the
package is worse than an undocumented one, because the document is what a new user trusts.

**That it stays a facade.** Every method here delegates to `Tesoro`. The moment one of them
starts deciding, converting money or writing evidence, there are two decision paths and only one
is the tested one — so `test_the_facade_does_not_decide_anything` checks the calls line up rather
than merely that the answers look plausible.
"""

from __future__ import annotations

import pytest

from tesoro import Governor
from tesoro.domain import Verdict


@pytest.fixture
def gov(tmp_path, monkeypatch):
    """A governor in an empty directory, so `load()` takes its no-config path."""
    monkeypatch.chdir(tmp_path)
    g = Governor.load()
    yield g
    g.close()


def daily_used(g: Governor) -> str | None:
    for envelope in g.report().envelopes["external"]:
        if envelope.name == "daily":
            return envelope.used_usd
    return None


# --- it exists ------------------------------------------------------------


def test_the_documented_snippet_runs(gov):
    """Verbatim from README.md and docs/api-surface.md §1. It used to raise AttributeError.

    Kept as one test rather than split up, because what broke was not any single call — it was
    that the *sequence* a new user copies did not work end to end.
    """
    decision = gov.authorize(
        amount_usd="2.50", vendor="acme", resource="/market/snapshot"
    )
    assert decision.verdict in tuple(Verdict)
    assert decision.attributed_control, "no control was attributed"

    if decision.approved:
        gov.settle(decision, success=True)

    report = gov.report()
    assert report.decisions_total >= 1
    assert report.policy_name


def test_load_works_with_no_config_at_all(tmp_path, monkeypatch):
    """`pip install tesoro` then `Governor.load()` must not be a stack trace.

    A missing config is not an error — the packaged starter pack and the defaults are a working
    configuration. The first thing a new user tries is the one that most needs to work.
    """
    monkeypatch.chdir(tmp_path)
    with Governor.load() as g:
        assert g.report().policy_name


def test_load_honours_the_config_it_is_given(tmp_path, monkeypatch):
    """And an explicitly named config that cannot be read is a real error, unlike an absent
    one: the caller asked for a specific file."""
    monkeypatch.chdir(tmp_path)
    from tesoro.errors import ConfigError

    with pytest.raises(ConfigError):
        Governor.load(tmp_path / "nope.yaml")


def test_two_projects_in_one_process_do_not_share_a_journal(tmp_path, monkeypatch):
    """Evidence location was frozen at import time, and for a layer that counts money that is
    not a cosmetic bug.

    `DATA_DIR = Path.cwd() / ".tesoro"` was evaluated when the module was imported and captured
    in `Paths.under()`'s default argument. So a process that changed directory kept writing to
    the journal it started with, and two governors loaded from different directories shared one
    — meaning **one agent's spending consumed the other's envelopes**.

    Found by this suite leaking state between tests: a revoked intent from one test refused a
    payment in another, because both had written to the same directory outside `tmp_path`.
    """
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()

    monkeypatch.chdir(first)
    with Governor.load() as g1:
        decision = g1.authorize(amount_usd="0.01", vendor="acme", resource="/r")
        g1.settle(decision, success=True)
        assert daily_used(g1) == "0.010000"

    monkeypatch.chdir(second)
    with Governor.load() as g2:
        assert daily_used(g2) == "0.000000", (
            "the second project sees the first one's spending, so they share a journal"
        )
        assert g2.report().decisions_total == 0

    assert (first / ".tesoro").is_dir()
    assert (second / ".tesoro").is_dir(), "the second project wrote outside its own directory"


def test_it_is_a_context_manager(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with Governor.load() as g:
        assert g.authorize(amount_usd="0.01", vendor="v", resource="/r")


# --- money never touches a float -----------------------------------------


@pytest.mark.parametrize("bad", [2.50, 0.0, -1.5])
def test_a_float_amount_raises(gov, bad):
    """Invariant 3, enforced at the boundary rather than trusted.

    `0.1 + 0.2` is not `0.3`, and a governance layer that rounds a payment silently has
    miscounted somebody's money. Raising is the only honest option: there is no rounding this
    layer could choose that the caller would know about.
    """
    with pytest.raises(TypeError, match="float"):
        gov.authorize(amount_usd=bad, vendor="v", resource="/r")


def test_a_bool_amount_raises(gov):
    """`isinstance(True, int)` is true in Python, and an `int` here means atomic units — so
    without an explicit check, `amount_usd=True` would quietly mean one atomic unit."""
    with pytest.raises(TypeError):
        gov.authorize(amount_usd=True, vendor="v", resource="/r")


def test_an_int_amount_is_atomic_units(gov):
    """`"2.50"` is dollars; `2500000` is atomic units. Two types, two meanings, documented —
    and a caller who confuses them is off by a million, which is loud rather than subtle."""
    decision = gov.authorize(amount_usd=2_500_000, vendor="v", resource="/r")
    assert decision.request_id
    other = gov.decide(amount_usd="2.50", vendor="v", resource="/r")
    assert other.budget.binding == decision.budget.binding


def test_an_unknown_channel_names_the_two(gov):
    """A plain string spares the caller an import; validating it keeps that from becoming a
    typo that silently governs the wrong budget."""
    with pytest.raises(ValueError, match="internal"):
        gov.authorize(amount_usd="1", vendor="v", resource="/r", channel="middle")


# --- authorize then settle ------------------------------------------------


def test_authorizing_consumes_nothing(gov):
    """Envelopes consume on settle. An abandoned decision must not eat budget, or the layer
    reports spending that never happened."""
    before = daily_used(gov)
    gov.authorize(amount_usd="0.01", vendor="acme", resource="/r")
    assert daily_used(gov) == before


def test_settling_consumes(gov):
    decision = gov.authorize(amount_usd="0.01", vendor="acme", resource="/r")
    assert decision.approved, decision.reason
    gov.settle(decision, success=True)
    assert daily_used(gov) == "0.010000"


def test_a_failed_settlement_consumes_nothing(gov):
    """A payment that did not happen did not spend anything, and counting it would make a
    budget exhaust itself on failures."""
    decision = gov.authorize(amount_usd="0.01", vendor="acme", resource="/r")
    gov.settle(decision, success=False)
    assert daily_used(gov) == "0.000000"


def test_settling_for_more_than_was_authorized_consumes_the_larger_amount(gov):
    """The hole this found, and the direction that matters.

    `record_settlement` journalled the settled amount as evidence and called `mark_settled()`
    without it, so `transactions.amount_atomic` kept the *authorised* figure — and every window
    sum reads that column. A `$0.05` authorisation settled as `$5.00` consumed `$0.05`, so a
    cumulative envelope could be walked straight through by overspending at settlement: the
    daily ceiling saw a hundredth of the money that moved.

    Under-consumption is the dangerous direction. Over-consumption would merely be
    conservative.
    """
    decision = gov.authorize(amount_usd="0.05", vendor="acme", resource="/r")
    gov.settle(decision, actual_amount_usd="5.00")
    assert daily_used(gov) == "5.000000", (
        "the envelope consumed the authorised amount rather than what settled, so overspending "
        "at settlement bypasses every cumulative limit"
    )


def test_settling_for_less_frees_the_difference(gov):
    """The other side of the same rule: the amount paid is what was spent."""
    decision = gov.authorize(amount_usd="5.00", vendor="acme", resource="/r")
    gov.settle(decision, actual_amount_usd="0.02")
    assert daily_used(gov) == "0.020000"


def test_no_reported_amount_falls_back_to_the_authorized_one(gov):
    """Four states again: *not reported* is not *zero*.

    A settlement that says nothing about the amount is the ordinary case — the rail moved what
    was quoted. Treating silence as zero would make every normal settlement free.
    """
    decision = gov.authorize(amount_usd="0.01", vendor="acme", resource="/r")
    gov.settle(decision)
    assert daily_used(gov) == "0.010000"


def test_a_negative_settlement_is_refused(gov):
    """AEGS-0.1-ARITH-4 at the settlement boundary. A refund is its own event, not a payment
    with a minus sign — and a negative consumption would *create* headroom."""
    decision = gov.authorize(amount_usd="0.01", vendor="acme", resource="/r")
    with pytest.raises((ValueError, TypeError)):
        gov.settle(decision, actual_amount_usd=-10_000)


def test_settling_a_decision_this_governor_never_made_is_refused(gov):
    """`settle()` takes the `Decision` so this is unrepresentable rather than discouraged.

    A settlement records what happened to a decision *this layer authorized*. Anything else
    would be evidence of an event it never saw.
    """
    import dataclasses

    decision = gov.authorize(amount_usd="0.01", vendor="acme", resource="/r")
    foreign = dataclasses.replace(decision, request_id="never-made-here")
    with pytest.raises(ValueError, match="not made by this governor"):
        gov.settle(foreign)


def test_decide_journals_nothing(gov):
    """A dry run must leave no evidence, or the record contains decisions nobody acted on.

    A separate method rather than `authorize(dry_run=True)`: a boolean that determines whether
    evidence is written is too easy to pass through from a variable and never notice.
    """
    before = gov.report().decisions_total
    gov.decide(amount_usd="0.01", vendor="acme", resource="/r")
    assert gov.report().decisions_total == before


# --- attribution ----------------------------------------------------------


def test_a_refusal_names_the_control_that_refused(gov):
    """The field that makes a decision auditable rather than merely recorded."""
    decision = gov.authorize(amount_usd="5000", vendor="acme", resource="/r")
    assert not decision.approved
    assert decision.attributed_control not in ("", "unattributed"), (
        "a refusal with no attributable cause is not auditable evidence"
    )
    assert decision.reason, "no reason carried the verdict"


def test_attribution_matches_the_report(gov):
    """One projection, not two. A report, a conformance run and `Decision.attributed_control`
    disagreeing about which control refused would be three answers to a question with one."""
    decision = gov.authorize(amount_usd="5000", vendor="acme", resource="/r")
    view = gov.report().decisions[0]
    assert view.attributed_control == decision.attributed_control


def test_a_sanctioned_counterparty_is_attributed_to_sanctions(gov):
    """AEGS-0.1-VERD-4a. Attribution must not land on whatever spending limit bit first."""
    decision = gov.authorize(
        amount_usd="5000", vendor="ofac-listed", resource="/r", sanctioned=True
    )
    assert decision.verdict is Verdict.REJECT
    assert decision.attributed_control == "sanctions", decision.attributed_control


# --- declarations ---------------------------------------------------------


def test_an_intent_can_be_declared_and_revoked(gov):
    intent_id = gov.declare_intent(
        purpose="buy market data", budget_usd="10", expires_in_s=3600
    )
    assert intent_id
    assert gov.revoke_intent(intent_id) is True
    assert gov.revoke_intent(intent_id) is False, "revoking twice should report nothing to do"


def test_an_identity_can_be_registered(gov):
    gov.register_identity(agent_id="agent-1", controller="acme-ltd", per_action_usd="5")
    identity = gov._layer.identities.get("agent-1")
    assert identity is not None
    assert identity.controller is not None and identity.controller.id == "acme-ltd"


def test_a_registered_identity_does_not_leak_its_controller(gov):
    """Invariant 10. The facade must not undo the pseudonymous default on the way through."""
    gov.register_identity(agent_id="agent-1", controller="acme-ltd")
    disclosed = gov._layer.identities.get("agent-1").disclose("vendor")
    assert "controller" not in disclosed
    assert "spendingLimits" not in disclosed


# --- wrap -----------------------------------------------------------------


def test_wrap_refuses_an_object_that_cannot_pay_and_says_what_is_missing(gov):
    """`isinstance` against a runtime protocol returns a bare `False`, which tells an
    integrator nothing about what to add."""
    with pytest.raises(TypeError) as excinfo:
        gov.wrap(object())
    assert "missing" in str(excinfo.value)
    assert "quote" in str(excinfo.value), "the message does not name the absent members"


# --- it stays a facade ----------------------------------------------------


def test_the_facade_does_not_decide_anything():
    """Every decision goes through `Tesoro`, so there is one decision path and it is the tested
    one.

    An AST check rather than a behavioural one: two paths that agree today would diverge on the
    first change, and by then the second one would have its own users.
    """
    import ast
    from pathlib import Path

    import tesoro

    source = Path(tesoro.__file__).parent / "governor.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))

    banned = {"evaluate_rules", "usd_to_atomic_unchecked", "_deciding_engine"}
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not (called & banned), f"the facade reimplements: {called & banned}"


def test_the_facade_imports_no_framework():
    """Invariant 8, at the one place a convenience import would be most tempting."""
    import ast
    from pathlib import Path

    import tesoro

    source = Path(tesoro.__file__).parent / "governor.py"
    banned = {"anthropic", "openai", "langgraph", "crewai", "streamlit", "google"}
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            assert name.split(".")[0] not in banned, f"governor.py imports {name}"


def test_governor_is_what_the_package_exports():
    """The whole reason this module exists. `from tesoro import Governor` returned the internal
    rules evaluator, which has no `load`, no `wrap` and no keyword `authorize`."""
    import tesoro

    assert tesoro.Governor is Governor
    for method in ("load", "from_config", "authorize", "settle", "wrap", "report", "close"):
        assert hasattr(tesoro.Governor, method), f"Governor has no {method}()"


def test_the_rules_evaluator_is_no_longer_called_governor():
    """It evaluates rules; it does not govern. Sharing the name is what made the documented
    surface unreachable."""
    from tesoro.authorize import RuleEngine

    assert RuleEngine.__name__ == "RuleEngine"
    import tesoro

    assert tesoro.Governor is not RuleEngine


def test_every_method_the_api_surface_documents_actually_exists():
    """F-A12, as a test rather than a lesson.

    `docs/api-surface.md` §3 carries a `class Governor:` block listing the Tier 1 surface. The
    README once documented an API that did not exist and its opening snippet raised
    `AttributeError` on line 3; nothing compared the page to the class. This does.

    It also runs the other way: a public method on `Governor` that the page omits is an
    undocumented part of the stable surface, and `verify()` was exactly that until
    `verify_anchored()` was added and the omission became visible.
    """
    import re
    from pathlib import Path

    page = (Path(__file__).resolve().parents[1] / "docs" / "api-surface.md").read_text(
        encoding="utf-8"
    )
    block = re.search(r"```python\nclass Governor:\n(.*?)```", page, re.S)
    assert block, "the `class Governor:` block is gone from docs/api-surface.md §3"

    documented = set(re.findall(r"^\s+def (\w+)\(", block.group(1), re.M))
    assert documented, "the block lists no methods"

    missing = sorted(n for n in documented if not hasattr(Governor, n))
    assert not missing, (
        f"documented but absent from Governor: {missing}. A documented method that does not "
        "exist is worse than an undocumented one -- a reader follows it."
    )

    public = {
        n for n in vars(Governor)
        if not n.startswith("_") and callable(getattr(Governor, n, None))
    }
    undocumented = sorted(public - documented)
    assert not undocumented, (
        f"public on Governor but absent from docs/api-surface.md §3: {undocumented}"
    )
