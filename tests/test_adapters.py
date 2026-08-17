"""The adapter contracts. A7.

Every test here runs with **no framework installed**, which is the point rather than a
convenience: a contract that can only be exercised with a real SDK present is a contract nobody
checks, and the two adapters would then be verified by their absence.

What these defend:

* the framework contract works with a governor, and works identically without one;
* an adapter can only ever *tighten* a ceiling the caller set;
* the rail contract and the framework contract stay separate;
* the dependency arrow points from the agent to this layer, never back.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from aegoll import Governor
from aegoll.adapters.adk import GoogleADKAdapter
from aegoll.adapters.base import RunGuard, conforms_as_payment_client
from aegoll.adapters.claude import SDK_BUDGET_STOP, ClaudeAgentAdapter


@pytest.fixture
def gov(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    g = Governor.load()
    yield g
    g.close()


# --- ungoverned is a valid state -----------------------------------------


@pytest.mark.parametrize("adapter_cls", [ClaudeAgentAdapter, GoogleADKAdapter])
def test_no_governor_means_everything_proceeds(adapter_cls):
    """An agent written against an adapter must run identically with governance absent.

    That is what keeps this a *layer* rather than a dependency. The alternative is `if governor:`
    scattered through an agent loop, and the branch that gets forgotten is always the one that
    mattered.
    """
    adapter = adapter_cls(None, budget_usd="0.40")
    allowed, why = adapter.before_run(model="m")
    assert allowed and why == ""
    assert adapter.should_stop("999999") is False
    adapter.after_run(actual_cost_usd="1.00")  # must not raise
    assert adapter.as_dict()["governed"] is False


@pytest.mark.parametrize("adapter_cls", [ClaudeAgentAdapter, GoogleADKAdapter])
def test_a_governor_with_no_budget_does_not_invent_a_ceiling(adapter_cls, gov):
    """A governance layer must not impose a limit the caller never asked for.

    Silence is not zero. A run with no declared budget is ungoverned *on this axis*, and
    inventing a ceiling would refuse work nobody said was too expensive.
    """
    adapter = adapter_cls(gov, budget_usd=None)
    allowed, _ = adapter.before_run(model="m")
    assert allowed
    assert adapter.should_stop("100") is False


# --- the three calls ------------------------------------------------------


def test_a_run_within_budget_starts(gov):
    adapter = ClaudeAgentAdapter(gov, budget_usd="0.01")
    allowed, why = adapter.before_run(model="claude-sonnet-4")
    assert allowed, why
    assert adapter.guard.authorization is not None


def test_a_run_over_budget_is_refused_before_any_tokens_are_spent(gov):
    """The value a per-run ceiling inside an SDK cannot deliver: a refusal *before* the run,
    rather than a stop partway through with tokens already paid for."""
    adapter = ClaudeAgentAdapter(gov, budget_usd="5000")
    allowed, why = adapter.before_run(model="claude-sonnet-4")
    assert not allowed
    assert "refused" in why
    assert adapter.guard.authorization is not None
    assert not adapter.guard.authorization.approved


def test_the_refusal_names_the_control_that_refused(gov):
    """A stop message reading "budget exceeded" sends a reader to the wrong place when what
    actually happened was a treasury balance check."""
    adapter = ClaudeAgentAdapter(gov, budget_usd="5000")
    _, why = adapter.before_run(model="m")
    assert adapter.guard.authorization.attributed_control in why


def test_an_unauthorized_run_never_stops(gov):
    """`should_stop()` on a run that never started is `False`, because there is nothing to stop.

    Not a technicality: returning `True` would make a refused run look like a stopped one, and
    those are different facts with different remedies.
    """
    adapter = ClaudeAgentAdapter(gov, budget_usd="5000")
    adapter.before_run(model="m")  # refused
    assert adapter.should_stop("999999") is False


def test_a_run_is_stopped_when_spend_reaches_a_limit_the_adapter_never_knew(gov):
    """The reason `should_stop()` asks the layer instead of comparing against `budget_usd`.

    A guard that only checked its own number would enforce the one limit it was told and ignore
    every other — a daily envelope crossed partway through a long run, a per-vendor ceiling, a
    counterparty whose trust changed. Here the run's budget is tiny and the spend is enormous:
    what refuses it is treasury policy, not this adapter's arithmetic.
    """
    adapter = ClaudeAgentAdapter(gov, budget_usd="0.01")
    assert adapter.before_run(model="m")[0]
    assert adapter.should_stop("5000") is True
    assert adapter.guard.stop_reason, "nothing was attributed for the stop"


def test_finishing_consumes_and_starting_does_not(gov):
    """Envelopes consume on settle. An abandoned run must not eat budget."""
    def used():
        for e in gov.report().envelopes["internal"]:
            if e.name == "daily":
                return e.used_usd
        return None

    before = used()
    adapter = ClaudeAgentAdapter(gov, budget_usd="0.01")
    assert adapter.before_run(model="m")[0]
    assert used() == before, "starting a run consumed budget"

    adapter.after_run(actual_cost_usd="0.004")
    assert used() != before, "finishing a run consumed nothing"


def test_finishing_records_what_it_actually_cost(gov):
    """A run authorised for $0.01 that cost $0.008 must consume $0.008.

    The authorised figure is an estimate; the settled one is what happened. Consuming the
    estimate makes every report wrong by whatever the difference was.
    """
    adapter = ClaudeAgentAdapter(gov, budget_usd="0.01")
    adapter.before_run(model="m")
    adapter.after_run(actual_cost_usd="0.008")

    internal = {e.name: e.used_usd for e in gov.report().envelopes["internal"]}
    assert internal["daily"] == "0.008000", internal


def test_a_failed_run_consumes_nothing(gov):
    adapter = ClaudeAgentAdapter(gov, budget_usd="0.01")
    adapter.before_run(model="m")
    adapter.after_run(actual_cost_usd="0.008", success=False)

    internal = {e.name: e.used_usd for e in gov.report().envelopes["internal"]}
    assert internal["daily"] == "0.000000"


def test_the_internal_channel_is_the_one_used(gov):
    """Two channels never share an envelope. A token budget must not consume payout headroom."""
    adapter = ClaudeAgentAdapter(gov, budget_usd="0.01")
    adapter.before_run(model="m")
    adapter.after_run(actual_cost_usd="0.008")

    external = {e.name: e.used_usd for e in gov.report().envelopes["external"]}
    assert external["daily"] == "0.000000", "an internal run consumed the external budget"


# --- an adapter may only tighten -----------------------------------------


def test_the_sdk_ceiling_is_the_tighter_of_the_two(gov):
    """`max_budget_usd` is the SDK's own ceiling, and the layer must not raise it.

    A governance layer that widened a limit the caller set would be doing the one thing no
    control in this system may do. `0.10` asked for, `0.40` governed, `0.10` wins.
    """
    adapter = ClaudeAgentAdapter(gov, budget_usd="0.40")
    options = adapter.options_for({"model": "claude-sonnet-4", "max_budget_usd": 0.10})
    assert options["max_budget_usd"] == pytest.approx(0.10)
    assert options["model"] == "claude-sonnet-4", "the caller's other options were dropped"


def test_the_governed_ceiling_applies_when_the_caller_named_none(gov):
    adapter = ClaudeAgentAdapter(gov, budget_usd="0.40")
    assert adapter.options_for({})["max_budget_usd"] == pytest.approx(0.40)


def test_the_governed_ceiling_tightens_a_looser_caller_limit(gov):
    adapter = ClaudeAgentAdapter(gov, budget_usd="0.40")
    assert adapter.options_for({"max_budget_usd": 9.0})["max_budget_usd"] == pytest.approx(0.40)


def test_no_governor_leaves_the_sdk_options_untouched():
    """Ungoverned means unchanged, not defaulted."""
    adapter = ClaudeAgentAdapter(None, budget_usd="0.40")
    assert adapter.options_for({"max_budget_usd": 0.10}) == {"max_budget_usd": 0.10}
    assert "max_budget_usd" not in adapter.options_for({})


def test_adk_step_ceiling_is_not_derived_from_the_spend_ceiling(gov):
    """`max_llm_calls` counts steps; a budget counts dollars. Neither substitutes for the other.

    A step ceiling catches a runaway loop whose steps are almost free — which a spend ceiling
    notices only slowly. A spend ceiling catches one enormous call, which a step ceiling never
    notices. Deriving one from the other would silently drop half the coverage.
    """
    adapter = GoogleADKAdapter(gov, budget_usd="0.40")
    assert adapter.run_config_for({})["max_llm_calls"] == adapter.DEFAULT_MAX_LLM_CALLS
    assert adapter.run_config_for({"max_llm_calls": 3})["max_llm_calls"] == 3, (
        "the caller's step ceiling was overwritten"
    )


def test_the_adk_callback_returns_none_to_proceed(gov):
    """ADK's convention: `None` means carry on. Returning `True` would read as "handled"."""
    adapter = GoogleADKAdapter(gov, budget_usd="0.01")
    adapter.before_run(model="gemini-2.0-flash")

    spent = ["0.001"]
    callback = adapter.before_model_callback(lambda: spent[0])
    assert callback() is None

    spent[0] = "5000"
    stop = callback()
    assert stop is not None and stop["stop"] is True
    assert stop["attributedControl"], "a stop with no attribution is not auditable"


def test_the_callback_reads_the_spend_each_time_it_is_called(gov):
    """The getter is a callable rather than a number for a reason.

    A number captured at construction is the spend *before the run began* — always zero — so the
    ceiling would never trip and the callback would look like it was working.
    """
    adapter = GoogleADKAdapter(gov, budget_usd="0.01")
    adapter.before_run(model="m")

    calls = []

    def spent():
        calls.append(1)
        return "0.001"

    callback = adapter.before_model_callback(spent)
    callback()
    callback()
    assert len(calls) == 2, "the callback cached the spend instead of re-reading it"


# --- the SDK's ceiling versus ours ---------------------------------------


def test_an_sdk_budget_stop_is_distinguishable_from_a_governed_stop():
    """Two different facts, adjustable in two different places. Reporting one as the other sends
    whoever is debugging to the wrong ceiling."""
    adapter = ClaudeAgentAdapter(None)

    class Result:
        subtype = SDK_BUDGET_STOP

    assert adapter.stopped_by_sdk(Result()) is True
    assert adapter.stopped_by_sdk({"subtype": SDK_BUDGET_STOP}) is True
    assert adapter.stopped_by_sdk({"subtype": "success"}) is False
    assert adapter.stopped_by_sdk(object()) is False


# --- the rail contract ---------------------------------------------------


def test_a_conforming_client_is_accepted():
    class Client:
        address = "0xabc"
        spend_cap_usd = 10
        total_spent_usd = 0
        calls: list = []

        def budget_snapshot(self): return {}
        def quote(self): ...
        def get_free(self): ...
        def get_paid(self): ...
        async def aclose(self): ...

    ok, missing = conforms_as_payment_client(Client())
    assert ok and missing == []


def test_a_non_conforming_client_is_told_what_is_missing():
    """"Does not conform" is unactionable; "missing: quote, aclose" is a to-do list."""
    class Half:
        address = "0xabc"
        def quote(self): ...

    ok, missing = conforms_as_payment_client(Half())
    assert not ok
    assert "get_paid" in missing and "aclose" in missing
    assert "quote" not in missing, "a member that is present was reported missing"


def test_the_two_contracts_are_separate():
    """A framework adapter and a rail adapter are different boundaries.

    Merged, they would differ in currency, counterparty, failure mode and direction of control —
    a framework calls the adapter, whereas a rail adapter is called by the agent. When AP2
    arrives it needs the rail contract and none of the framework one.
    """
    guard_members = set(dir(RunGuard))
    from aegoll.adapters.base import PAYMENT_CLIENT_MEMBERS

    overlap = guard_members & set(PAYMENT_CLIENT_MEMBERS)
    assert not overlap, f"the contracts have started to merge: {overlap}"


# --- the dependency arrow -------------------------------------------------


@pytest.mark.parametrize("module", ["base.py", "claude.py", "adk.py"])
def test_no_adapter_imports_its_framework(module):
    """Invariant 8, and the reason these are testable with nothing installed.

    An AST walk rather than a text scan, so a docstring naming the SDK does not fail the test
    while an actual import would.
    """
    import aegoll

    source = Path(aegoll.__file__).parent / "adapters" / module
    banned = {
        "claude_agent_sdk", "anthropic", "google", "google_adk", "langgraph", "crewai",
    }
    offenders = []
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        offenders += [f"{module}:{node.lineno} imports {n}" for n in names
                      if n.split(".")[0] in banned]
    assert not offenders, "\n  ".join(offenders)


def test_importing_aegoll_does_not_import_an_adapter():
    """`import aegoll` must not pull in a framework or a payment SDK.

    Checked as a subprocess, because by the time this test file has run its own imports the
    modules are already in `sys.modules` and an in-process check would pass regardless.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable, "-c",
            "import aegoll, sys; "
            "leaked = [m for m in sys.modules if m.startswith('aegoll.adapters')]; "
            "print(leaked)",
        ],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "[]", f"importing aegoll pulled in {result.stdout.strip()}"


def test_a_fake_framework_satisfies_the_contract(gov):
    """A7.9. The adapter is not the coupling: anything making the three calls is governed.

    An agent on a framework with no adapter in this package can still be governed by using
    `RunGuard` directly, which imports nothing at all.
    """
    guard = RunGuard(gov, budget_usd="0.01")

    class SomeOtherFramework:
        """Knows nothing about aegoll beyond the three calls."""

        def __init__(self, hook):
            self.hook = hook

        def run(self):
            allowed, why = self.hook.start(model="whatever", provider="someone")
            if not allowed:
                return f"refused: {why}"
            for spent in ("0.001", "0.002"):
                if self.hook.should_stop(spent):
                    return "stopped"
            self.hook.finish("0.002")
            return "completed"

    assert SomeOtherFramework(guard).run() == "completed"
    assert guard.as_dict()["authorized"] is True
