"""The universal plugin surface.

The claim these defend: `Governor` governs *any* agent, and nothing about it is
specific to a framework. So they use a fake payment client and no framework at
all -- if these pass without LangGraph, ADK or the Claude SDK installed, the
surface really is portable. `agents/tests/test_decoupling.py` covers the other
direction, that `tesoro` never imports an agent.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal

import pytest

from tesoro.plugin import (
    NOT_RECOMMENDED,
    RECOMMENDED_ADVISOR,
    Governor,
    PaymentClient,
    conforms,
    provider_vendor,
)


@dataclass
class FakeQuote:
    """The minimum a quote must carry: a price AEGL can govern."""

    price_usd: Decimal


@dataclass
class FakeCall:
    payment_status: str = "settled"
    transaction: str = "0xdeadbeef"


class FakeBuyer:
    """A payment client that satisfies `PaymentClient` and signs nothing.

    Not a mock of the real buyer -- the point is that AEGL works against anything
    with this shape, which is exactly what the protocol promises.
    """

    def __init__(self, price: str = "0.001") -> None:
        self.price = Decimal(price)
        self.address = "0xFAKE"
        self.spend_cap_usd = Decimal("1.00")
        self.total_spent_usd = Decimal("0")
        self.calls: list[str] = []
        self.paid: list[str] = []

    async def quote(self, path: str):
        return FakeQuote(price_usd=self.price)

    async def get_free(self, path: str):
        return {"path": path}

    async def get_paid(self, path: str):
        self.paid.append(path)
        return FakeCall()

    def budget_snapshot(self) -> dict:
        return {"capUsd": "1.00", "spentUsd": "0"}

    async def aclose(self) -> None:
        return None


class Incomplete:
    address = "0x0"


class PricelessQuoteBuyer(FakeBuyer):
    """Has every required member, but its quote carries no price."""

    async def quote(self, path: str):
        return {"path": path, "amountUsd": "0.001"}  # a dict, not a Quote


@pytest.fixture
def gov(tmp_path):
    # advisor=None keeps this suite free and offline. Advisor behaviour has its
    # own tests; here it would only add network flakiness.
    g = Governor(advisor=None, data_dir=tmp_path)
    yield g
    g.close()


# --- the protocol ---------------------------------------------------------


def test_a_conforming_buyer_is_accepted(gov):
    assert conforms(FakeBuyer()) == (True, [])
    assert isinstance(FakeBuyer(), PaymentClient)
    assert gov.wrap(FakeBuyer()) is not None


def test_a_nonconforming_buyer_is_refused_by_name(gov):
    ok, missing = conforms(Incomplete())
    assert not ok
    assert "get_paid" in missing

    with pytest.raises(TypeError) as exc:
        gov.wrap(Incomplete())
    # The message must name what is missing; a bare False is useless when
    # integrating a new buyer.
    assert "get_paid" in str(exc.value)


def test_wrapping_preserves_the_buyers_own_surface(gov):
    wrapped = gov.wrap(FakeBuyer())
    assert wrapped.address == "0xFAKE"
    assert wrapped.spend_cap_usd == Decimal("1.00")
    assert wrapped.budget_snapshot()["capUsd"] == "1.00"


def test_an_approved_purchase_reaches_the_buyer_and_is_recorded(gov):
    # asyncio.run rather than a pytest-asyncio marker: the plugin must be
    # testable without extra plugins installed, same as it must be usable
    # without a framework installed.
    buyer = FakeBuyer()
    wrapped = gov.wrap(buyer)

    asyncio.run(wrapped.get_paid("/market/snapshot"))

    assert buyer.paid == ["/market/snapshot"], "the payment never reached the client"
    external = [e for e in gov.events if e.channel == "external"]
    assert external and external[-1].verdict == "APPROVE"
    assert external[-1].engine


def test_a_quote_without_a_price_fails_with_a_usable_message(gov):
    """`conforms()` checks members exist; it cannot check what they return.

    A buyer can pass conformance and still be ungovernable, so the failure has to
    name the problem rather than surfacing an AttributeError from inside AEGL.
    """
    assert conforms(PricelessQuoteBuyer()) == (True, [])

    wrapped = gov.wrap(PricelessQuoteBuyer())
    with pytest.raises(TypeError) as exc:
        asyncio.run(wrapped.get_paid("/market/snapshot"))
    assert "price_usd" in str(exc.value)


# --- the mid-run ceiling: the capability AEGL adds ------------------------


def test_check_spend_stops_the_run_at_the_authorized_budget(gov):
    auth = gov.authorize_run(model="gpt-4o-mini", provider="openai", budget_usd=0.04)
    assert auth.allowed

    assert gov.check_spend(0.01).should_stop is False
    assert gov.check_spend(0.039).should_stop is False
    assert gov.check_spend(0.04).should_stop is True


def test_check_spend_enforces_what_was_authorized_not_what_was_asked(gov):
    """If policy clamps the budget, the ceiling is the clamped figure.

    Enforcing the requested amount would let a caller talk its way past a
    treasury envelope simply by asking for more.
    """
    auth = gov.authorize_run(model="m", budget_usd=0.04)
    assert gov.check_spend(auth.budget_usd).should_stop is True


def test_a_tripped_ceiling_stays_tripped(gov):
    gov.authorize_run(model="m", budget_usd=0.02)
    assert gov.check_spend(0.05).should_stop is True
    # A later, smaller figure must not reopen the run -- some frameworks report
    # per-step costs rather than cumulative ones, and that must not unstop it.
    assert gov.check_spend(0.001).should_stop is True


def test_check_spend_warns_before_it_stops(gov):
    gov.authorize_run(model="m", budget_usd=0.04)
    assert gov.check_spend(0.02).warn is False
    early = gov.check_spend(0.034)
    assert early.warn is True and early.should_stop is False


def test_check_spend_is_inert_without_an_authorized_run(gov):
    check = gov.check_spend(9999.0)
    assert check.should_stop is False
    assert "no run" in check.reason


def test_a_ceiling_stop_is_journalled(gov):
    """A run stopped silently is indistinguishable from one that finished."""
    gov.authorize_run(model="m", budget_usd=0.01)
    gov.check_spend(0.02)

    # The log is append-only: a settlement arrives as its own entry keyed
    # `settlement_update`, never as an edit to the decision it refers to.
    stops = [
        e for e in gov.tesoro.audit.entries()
        if (e.payload.get("settlement_update") or {}).get("type") == "spend_ceiling_stop"
    ]
    assert len(stops) == 1
    assert stops[0].payload["settlement_update"]["shouldStop"] is True


def test_headroom_and_fraction_are_reported(gov):
    auth = gov.authorize_run(model="m", budget_usd=0.04)
    assert auth.allowed, "the fixture policy must allow this, or the test is vacuous"
    check = gov.check_spend(0.01)
    assert check.headroom_usd == pytest.approx(0.03)
    assert check.fraction_used == pytest.approx(0.25)


# --- channels and counterparties -----------------------------------------


def test_providers_are_distinct_counterparties():
    """Merging two providers would corrupt the trust history of both."""
    assert provider_vendor("openai").id != provider_vendor("anthropic").id
    assert provider_vendor("gemini").id == provider_vendor("google").id
    assert provider_vendor("something-new").id == "something-new-api"


def test_the_two_channels_are_reported_separately(gov):
    gov.authorize_run(model="m", budget_usd=0.02)
    report = gov.report()
    assert report["internal"] and report["external"]
    assert report["internal"] is not report["external"]
    assert all(e["channel"] == "internal" for e in report["events"])


def test_report_is_plain_data(gov):
    """`tesoro.ui.render()` takes this; a UI must not need AEGL's types."""
    import json

    gov.authorize_run(model="m", budget_usd=0.02)
    gov.check_spend(0.03)
    json.dumps(gov.report())  # raises if anything in it is not serialisable


# --- defaults, as measured ------------------------------------------------


def test_the_default_advisor_is_the_measured_one():
    """See EVAL.md: 0/8 false-blocks, cheapest per call of the four tested."""
    assert RECOMMENDED_ADVISOR == ("gemini", "gemini-flash-lite-latest")


def test_the_model_measured_unusable_is_not_a_default():
    assert "llama-3.1-8b-instant" in NOT_RECOMMENDED
    assert RECOMMENDED_ADVISOR[1] not in NOT_RECOMMENDED


def test_advisor_none_is_deterministic_only(tmp_path):
    g = Governor(advisor=None, data_dir=tmp_path)
    try:
        assert g.tesoro.advisor is None
        assert g.advisor_spec is None
    finally:
        g.close()


class Unavailable:
    """An advisor that cannot run, for whatever reason a provider gives."""

    provider, model = "stub", "stub-model"

    def available(self):
        return False, "no API key configured"


def _no_advisor_available(monkeypatch):
    """Make advisor construction fail at the point `Governor` uses it.

    Patching `tesoro.advisors.keys.resolve_key` looks equivalent and is not: each
    provider module does `from .keys import resolve_key`, binding the name at
    import time. Whether the patch lands then depends on whether some earlier
    test already imported that module -- which made this suite pass alone and
    fail in full. Patch what the code under test actually calls.
    """
    monkeypatch.setattr("tesoro.plugin.build_advisor", lambda *a, **k: Unavailable())


def test_a_missing_key_costs_the_advisor_not_the_run(tmp_path, monkeypatch):
    """Degrade, never fail. Losing a second opinion must not stop transacting."""
    _no_advisor_available(monkeypatch)

    g = Governor(advisor="auto", data_dir=tmp_path)
    try:
        assert g.tesoro.advisor is None
        # "auto" with no key is the ordinary path, not an error to report.
        assert g.advisor_error is None
        assert g.authorize_run(model="m", budget_usd=0.02).allowed
    finally:
        g.close()


def test_an_explicit_advisor_reports_why_it_could_not_be_used(tmp_path, monkeypatch):
    """Asking for one by name and silently getting none would be a lie."""
    _no_advisor_available(monkeypatch)

    g = Governor(advisor=("groq", "llama-3.3-70b-versatile"), data_dir=tmp_path)
    try:
        assert g.tesoro.advisor is None
        assert g.advisor_error == "no API key configured", (
            "an explicit advisor failed without saying why"
        )
    finally:
        g.close()


def test_bad_advisor_argument_is_rejected(tmp_path):
    with pytest.raises(TypeError):
        Governor(advisor="groq", data_dir=tmp_path)


# --- policy selection -----------------------------------------------------


def test_policy_selects_by_name(tmp_path):
    g = Governor(policy="strict", advisor=None, data_dir=tmp_path)
    try:
        assert g.bundle.name == "strict"
    finally:
        g.close()


def test_an_unknown_policy_name_falls_back_to_the_default(tmp_path):
    g = Governor(policy="does-not-exist", advisor=None, data_dir=tmp_path)
    try:
        assert g.bundle.name
    finally:
        g.close()


def test_governor_closes_itself_as_a_context_manager(tmp_path):
    with Governor(advisor=None, data_dir=tmp_path) as g:
        assert g.authorize_run(model="m", budget_usd=0.01).allowed


# --- the structural claim -------------------------------------------------


def test_the_plugin_imports_no_framework_and_no_llm_sdk():
    """`Governor` must be installable next to any framework, or none.

    The whole universality claim rests on this file being importable in a process
    that has never heard of LangGraph, ADK or the Claude SDK. Model clients are
    reached only through `tesoro.advisors`, lazily.
    """
    import ast
    from pathlib import Path

    banned = {
        "langgraph", "langchain_core", "langchain_openai", "google.adk",
        "openai", "anthropic", "groq", "streamlit",
        "x402_agent", "langgraph_x402", "adk_x402",
    }
    from conftest import module_source

    source = module_source("plugin.py")
    offenders = []
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            if name in banned or name.split(".")[0] in {b.split(".")[0] for b in banned}:
                offenders.append(f"plugin.py:{node.lineno} imports {name}")

    assert not offenders, "the plugin is no longer framework-neutral:\n  " + "\n  ".join(
        offenders
    )


def test_the_four_calls_exist_and_are_the_documented_names():
    """The surface agents are told to depend on. Renaming one breaks every host."""
    for name in ("authorize_run", "wrap", "check_spend", "settle_run"):
        assert callable(getattr(Governor, name)), f"Governor.{name} is missing"


# --- the human-override flow, moved out of the Claude cockpit in B5 --------


def test_precheck_previews_a_refusal_without_journalling_it(gov):
    """A UI must be able to warn *before* a run starts.

    Journalling the preview would make the audit log a record of hypotheticals:
    someone who reads the warning and walks away would leave a phantom refusal
    behind. Only the real attempt is recorded.
    """
    before = len(gov.tesoro.audit.entries())

    pre = gov.precheck_run(model="m", budget_usd=0.10)  # over the $0.04 envelope

    assert pre["allowed"] is False
    assert pre["verdict"] == "REJECT"
    assert pre["engine"] == "treasury"
    assert pre["matched_rule"] == "internal-reject-over-budget"
    assert len(gov.tesoro.audit.entries()) == before, "the preview was journalled"


def test_precheck_agrees_with_the_real_authorization(gov):
    """A preview that disagreed with the decision would be worse than none."""
    pre = gov.precheck_run(model="m", budget_usd=0.04)
    auth = gov.authorize_run(model="m", budget_usd=0.04)
    assert pre["allowed"] == auth.allowed
    assert pre["verdict"] == auth.decision.verdict.value


def test_an_override_is_journalled_with_its_reason(gov):
    """A bypass that left no trace would make the log a record of policy, not events."""
    pre = gov.precheck_run(model="m", budget_usd=0.10)
    gov.record_override(pre, seconds_left=7.5)

    overrides = [
        e.payload["settlement_update"] for e in gov.tesoro.audit.entries()
        if (e.payload.get("settlement_update") or {}).get("type") == "human_override"
    ]
    assert len(overrides) == 1
    assert overrides[0]["overrodeVerdict"] == "REJECT"
    assert overrides[0]["engine"] == "treasury"
    assert overrides[0]["secondsLeftInWindow"] == 7.5


def test_precheck_attributes_spend_to_the_right_provider(gov):
    """Same reason `authorize_run` takes a provider: separate counterparties."""
    assert gov.precheck_run(model="m", budget_usd=0.01, provider="openai")["provider"] == "openai"
