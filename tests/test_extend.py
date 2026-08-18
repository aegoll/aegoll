"""Custom engines: gated at registration, and structurally unable to weaken anything.

Anything a policy pack cannot express is a missing engine, and this is where one lands.
That makes it the one place a third party gets to run code inside the decision path, so
the interesting tests here are the refusals.

Two claims, and the difference between them matters:

* **An engine cannot widen a verdict.** Not refused — *unreachable*. It returns an opinion
  and the composition root applies `narrower()`. Asserted by effect below.
* **An engine cannot be impure.** Refused at registration, by reading its source. A
  runtime failure in a governance layer arrives while an agent is mid-run holding a
  wallet; a registration failure arrives while a developer is looking at it.
"""

from __future__ import annotations

import tempfile

import pytest

from tesoro import extend
from tesoro.config import load_bundle
from tesoro.domain import Purpose, Vendor
from tesoro.errors import RegistrationError
from tesoro.runtime import Tesoro, Paths


@pytest.fixture(autouse=True)
def _clean_registry():
    """The registry is process-global, so a leaked engine would break its neighbours.

    Learned the hard way earlier in this project: a test that mutates shared state and
    does not put it back fails five tests that have nothing to do with it.
    """
    extend.clear_engines()
    yield
    extend.clear_engines()


def a_layer(agent_id: str = "x-agent") -> Tesoro:
    return Tesoro(
        bundle=load_bundle(), paths=Paths.ephemeral(tempfile.mkdtemp()), agent_id=agent_id
    )


def a_request(layer: Tesoro, amount: str = "2.50"):
    return layer.build_request(
        resource="/market/snapshot",
        amount_usd=amount,
        vendor=Vendor(id="acme", name="Acme"),
        purpose=Purpose.DATA_PURCHASE,
    )


# --- well-behaved engines -------------------------------------------------


class Tightener:
    """Refuses anything over a threshold. The shape a real extension takes."""

    name = "house-limit"

    def __init__(self, ceiling_atomic: int = 1_000_000) -> None:
        self.ceiling = ceiling_atomic

    def assess(self, context: extend.Context) -> extend.Assessment:
        amount = context.request.amount_atomic
        if amount > self.ceiling:
            return extend.Assessment(
                control=self.name,
                verdict="REJECT",
                score=1.0,
                reason=f"{amount} exceeds the house ceiling of {self.ceiling}",
            )
        return extend.Assessment(control=self.name, verdict=None, score=0.0)


class Widener:
    """Always votes APPROVE. Registers fine, and can never help anything through."""

    name = "wishful"

    def assess(self, context: extend.Context) -> extend.Assessment:
        return extend.Assessment(control=self.name, verdict="APPROVE", reason="looks fine")


class CouldNotRun:
    """A control that had nothing to work with, and says so."""

    name = "external-screen"

    def assess(self, context: extend.Context) -> extend.Assessment:
        return extend.Assessment(
            control=self.name, measured=False, reason="no screening list configured"
        )


def test_a_pure_engine_registers():
    extend.register_engine(Tightener())
    assert [e.name for e in extend.registered_engines()] == ["house-limit"]


def test_a_registered_engine_can_tighten_a_verdict():
    extend.register_engine(Tightener(ceiling_atomic=1_000_000))  # $1.00
    layer = a_layer()
    try:
        decision = layer.decide(a_request(layer, "2.50"))
        assert decision.verdict.value == "REJECT"
        assert any(r.source == "house-limit" for r in decision.reasons)
    finally:
        layer.close()


def test_an_engine_cannot_widen_a_verdict():
    """The load-bearing assertion of this whole module.

    `Widener` votes APPROVE on every request. The unextended decision for this request is
    REVIEW. If an engine could widen, this would come back APPROVE.
    """
    layer = a_layer()
    try:
        before = layer.decide(a_request(layer)).verdict.value
        assert before == "REVIEW", "the fixture policy must refuse this, or the test is vacuous"
    finally:
        layer.close()

    extend.register_engine(Widener())
    layer = a_layer()
    try:
        after = layer.decide(a_request(layer))
        assert after.verdict.value == "REVIEW", "an engine widened a verdict"
        assert any(
            r.code == "opinion_did_not_narrow" and r.source == "wishful"
            for r in after.reasons
        ), "the discarded opinion was not recorded"
    finally:
        layer.close()


def test_an_engine_cannot_widen_even_a_rejection():
    """Two engines: one refuses, one approves. The refusal must stand."""
    extend.register_engine(Tightener(ceiling_atomic=1))
    extend.register_engine(Widener())
    layer = a_layer()
    try:
        assert layer.decide(a_request(layer)).verdict.value == "REJECT"
    finally:
        layer.close()


def test_an_unmeasured_control_is_recorded_not_ignored():
    """`measured=False` must leave a trace.

    A control that silently did not run is indistinguishable from one that ran and found
    nothing, and that difference is the whole of the four-state rule.
    """
    extend.register_engine(CouldNotRun())
    layer = a_layer()
    try:
        decision = layer.decide(a_request(layer))
        traces = [r for r in decision.reasons if r.source == "external-screen"]
        assert traces and traces[0].code == "not_measured"
        assert "no screening list" in traces[0].detail
    finally:
        layer.close()


def test_an_engine_with_no_opinion_is_silent():
    """`verdict=None` means no opinion, which is not approval."""
    extend.register_engine(Tightener(ceiling_atomic=10 ** 12))
    layer = a_layer()
    try:
        decision = layer.decide(a_request(layer))
        assert decision.verdict.value == "REVIEW"
        assert not [r for r in decision.reasons if r.code == "clamped_by_extension"]
    finally:
        layer.close()


# --- refusals at registration --------------------------------------------


def engine_from_source(tmp_path, body: str, name: str = "impure"):
    """Build a real engine from arbitrary source, in a real importable module.

    Written to a file and imported rather than `exec`'d, because `inspect.getsource()`
    cannot read exec'd code — an `exec` version of this helper made every case here fail
    on "no readable source" instead of on the purity problem it was written to check.
    Testing the gate means giving it something it can actually read.
    """
    import importlib.util
    import sys

    module_name = f"_engine_{abs(hash(body)) % 10**8}"
    path = tmp_path / f"{module_name}.py"
    path.write_text(
        "from tesoro import extend\n\n\n"
        "class Subject:\n"
        f"    name = {name!r}\n\n"
        "    def assess(self, context):\n"
        f"{body}\n",
        encoding="utf-8",
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        return module.Subject()
    finally:
        sys.modules.pop(module_name, None)


@pytest.mark.parametrize(
    "body, expect",
    [
        ("        import os\n        return extend.Assessment(control='x')", "does not touch the environment"),
        ("        import requests\n        return extend.Assessment(control='x')", "no network"),
        ("        import random\n        return extend.Assessment(control='x')", "cannot be replayed"),
        ("        import time\n        return extend.Assessment(control='x')", "clock is injected"),
        ("        import anthropic\n        return extend.Assessment(control='x')", "no model in the decision path"),
        ("        from pathlib import Path\n        return extend.Assessment(control='x')", "no filesystem"),
        ("        import sqlite3\n        return extend.Assessment(control='x')", "not a database"),
        ("        open('/etc/passwd')\n        return extend.Assessment(control='x')", "no filesystem"),
        ("        eval('1+1')\n        return extend.Assessment(control='x')", "does not become an interpreter"),
        ("        exec('x=1')\n        return extend.Assessment(control='x')", "same as eval"),
        ("        __import__('os')\n        return extend.Assessment(control='x')", "dynamic import"),
        ("        input('go on then')\n        return extend.Assessment(control='x')", "block on a human"),
    ],
)
def test_an_impure_engine_is_refused_at_registration(tmp_path, body, expect):
    """Refused before it ever sees a request.

    Parametrized over arbitrary source because a fixed set of hand-written classes would
    only prove the check works on the ones I thought of.
    """
    engine = engine_from_source(tmp_path, body)
    with pytest.raises(RegistrationError) as exc:
        extend.register_engine(engine)
    assert expect in str(exc.value)
    assert extend.registered_engines() == (), "a refused engine was registered anyway"


def test_an_engine_holding_global_state_is_refused(tmp_path):
    """Mutable module state means the same inputs stop producing the same decision."""
    engine = engine_from_source(
        tmp_path,
        "        global _seen\n"
        "        _seen = 1\n"
        "        return extend.Assessment(control='stateful')",
        name="stateful",
    )
    with pytest.raises(RegistrationError, match="replayable"):
        extend.register_engine(engine)


def test_a_pure_engine_built_the_same_way_does_register(tmp_path):
    """The control for the cases above: the helper is not simply refusing everything."""
    engine = engine_from_source(
        tmp_path,
        "        return extend.Assessment(control='fine', score=float(context.request.amount_atomic > 0))",
        name="fine",
    )
    extend.register_engine(engine)
    assert [e.name for e in extend.registered_engines()] == ["fine"]


@pytest.mark.parametrize("name", ["treasury", "policy", "risk", "TrustAssessment", "EvidenceRecord"])
def test_an_engine_cannot_claim_a_standard_control(name):
    """An attributed control meaning two things makes every conformance report ambiguous."""
    engine = Tightener()
    engine.name = name
    with pytest.raises(RegistrationError, match="already defines"):
        extend.register_engine(engine)


def test_two_engines_cannot_share_a_name():
    extend.register_engine(Tightener())
    other = Tightener()
    with pytest.raises(RegistrationError, match="already registered"):
        extend.register_engine(other)


@pytest.mark.parametrize("name", [None, "", "   ", 42])
def test_an_engine_without_a_usable_name_is_refused(name):
    engine = Tightener()
    engine.name = name
    with pytest.raises(RegistrationError, match="name"):
        extend.register_engine(engine)


def test_an_engine_without_assess_is_refused():
    class Empty:
        name = "empty"

    with pytest.raises(RegistrationError, match="callable `assess`"):
        extend.register_engine(Empty())


def test_the_wrong_signature_is_refused():
    class TooMany:
        name = "greedy"

        def assess(self, context, extra):  # noqa: ARG002
            return extend.Assessment(control="greedy")

    with pytest.raises(RegistrationError, match="exactly one"):
        extend.register_engine(TooMany())


def test_unreadable_source_is_refused_unless_opted_into():
    """A C extension or a REPL definition cannot be checked, so it is not waved through.

    Uses a throwaway class rather than patching `Tightener`. The first version replaced
    `Tightener.assess` and restored it from `Tightener.assess` — which by then *was* the
    replacement — so the class stayed broken and took thirteen other tests down with it.
    A test that reaches into shared state breaks its neighbours; the fix is not to reach.
    """

    class Opaque:
        name = "opaque"
        assess = len  # a builtin: callable, one argument, no readable Python source

    with pytest.raises(RegistrationError, match="no readable source"):
        extend.register_engine(Opaque())

    extend.register_engine(Opaque(), allow_unreadable_source=True)
    assert [e.name for e in extend.registered_engines()] == ["opaque"]


# --- the Assessment type refuses to misreport ----------------------------


def test_an_unknown_verdict_is_refused():
    with pytest.raises(ValueError, match="not one of"):
        extend.Assessment(control="x", verdict="MAYBE")


def test_an_unmeasured_control_cannot_carry_a_score():
    """`measured=False` with `score=0.0` is the exact bug the four-state rule exists for.

    An unmeasured vendor history rendered as 0 once made every advisor treat established
    counterparties as strangers.
    """
    with pytest.raises(ValueError, match="four different things"):
        extend.Assessment(control="x", measured=False, score=0.0)


def test_no_opinion_is_not_approval():
    """A silent engine must not look like an endorsing one."""
    assert extend.Assessment(control="x").verdict is None


# --- the purity checker itself -------------------------------------------


def test_a_pure_source_has_no_problems():
    assert extend.purity_problems(
        "def assess(self, context):\n    return context.request.amount_atomic > 0\n"
    ) == []


def test_attribute_calls_on_forbidden_modules_are_caught():
    """`os.getenv(...)` without a visible import — e.g. via a module-level alias."""
    problems = extend.purity_problems(
        "def assess(self, context):\n    return os.getenv('X')\n"
    )
    assert problems and "environment" in problems[0]


def test_the_registry_is_emptied_cleanly():
    extend.register_engine(Tightener())
    assert extend.unregister_engine("house-limit") is True
    assert extend.unregister_engine("house-limit") is False
    assert extend.registered_engines() == ()
