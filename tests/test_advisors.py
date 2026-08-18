"""Phase 2: the advisor advises, the engines decide.

The security claim of the whole phase is the clamp. Phase 1 refused to read
untrusted vendor text at all (ADR-003); Phase 2 reads it, which is only
defensible because a compromised advisor cannot widen a verdict. These tests use
stub advisors so the invariant is checked mechanically, with no network and no
spend -- a live model that happens to behave well is not evidence.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tesoro import Tesoro, FixedClock, Paths, Vendor, Verdict, load_bundle
from tesoro.advise import consult
from tesoro.advisors import Advice, AdviceRequest, estimate_call_cost_usd

BASE = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
SELLER = Vendor(id="x402-poc-desk", name="POC Desk")
UNKNOWN = Vendor(id="mystery-co", name="Mystery Co")


class StubAdvisor:
    """An advisor that says whatever the test tells it to, for free."""

    provider = "stub"

    def __init__(self, recommendation: str, *, model: str = "stub-model",
                 injection: bool = False, fail: str | None = None) -> None:
        self.model = model
        self._recommendation = recommendation
        self._injection = injection
        self._fail = fail
        self.calls = 0

    def available(self):
        return True, "ready"

    def estimated_cost_usd(self) -> float:
        return 0.0001

    def advise(self, request: AdviceRequest) -> Advice:
        self.calls += 1
        if self._fail:
            return Advice(
                recommendation="REVIEW", confidence=0.0, rationale="failed",
                provider=self.provider, model=self.model, error=self._fail,
            )
        return Advice(
            recommendation=self._recommendation,
            confidence=0.9,
            rationale="stub",
            injection_suspected=self._injection,
            provider=self.provider,
            model=self.model,
            input_tokens=2000,
            output_tokens=400,
            cost_usd=0.0001,
        )


@pytest.fixture
def tesoro(tmp_path):
    a = Tesoro(bundle=load_bundle(), paths=Paths.ephemeral(tmp_path), clock=FixedClock(BASE))
    yield a
    a.close()


def _decide(tesoro, amount="0.01", vendor=SELLER, resource="/market/signal"):
    request = tesoro.build_request(resource=resource, amount_usd=amount, vendor=vendor)
    return request, tesoro.decide(request)


# --- the clamp ------------------------------------------------------------


def test_advisor_cannot_widen_a_verdict(tesoro):
    """The load-bearing invariant. A compromised advisor must achieve nothing."""
    # $500 from an unknown vendor: the engines refuse.
    request, decision = _decide(tesoro, amount="500", vendor=UNKNOWN, resource="/x")
    assert decision.verdict is not Verdict.APPROVE

    advisor = StubAdvisor("APPROVE")  # maximally compromised
    result = consult(request, decision, advisor, force=True)

    assert result.final_verdict is decision.verdict, (
        "an advisor widened a deterministic refusal -- the clamp is broken"
    )
    assert advisor.calls == 1
    assert any(r.code == "clamped" for r in result.reasons)


@pytest.mark.parametrize(
    "recommendation,expected",
    [("APPROVE", Verdict.APPROVE), ("REVIEW", Verdict.REVIEW),
     ("ESCALATE", Verdict.ESCALATE), ("REJECT", Verdict.REJECT)],
)
def test_advisor_can_always_tighten(tesoro, recommendation, expected):
    """From APPROVE, every recommendation is honoured -- all of them narrow."""
    request, decision = _decide(tesoro, amount="0.001")
    assert decision.verdict is Verdict.APPROVE

    result = consult(request, decision, StubAdvisor(recommendation), force=True)
    assert result.final_verdict is expected


def test_injection_flag_forces_rejection(tesoro):
    request, decision = _decide(tesoro, amount="0.001")
    assert decision.verdict is Verdict.APPROVE

    result = consult(
        request, decision, StubAdvisor("APPROVE", injection=True), force=True
    )
    assert result.final_verdict is Verdict.REJECT, (
        "an advisor that detected an injection did not stop the payment"
    )
    assert any(r.code == "injection_suspected" for r in result.reasons)


def test_advisor_failure_leaves_the_verdict_untouched(tesoro):
    """A dead advisor must not change outcomes in either direction."""
    request, decision = _decide(tesoro, amount="0.001")
    result = consult(
        request, decision, StubAdvisor("REJECT", fail="connection refused"), force=True
    )
    assert result.final_verdict is decision.verdict
    assert result.consulted is True
    assert any(r.code == "call_failed" for r in result.reasons)


# --- the economic gate ----------------------------------------------------


def test_advisor_is_not_consulted_below_break_even(tesoro):
    """The point of the EIAP: cheap purchases must not buy expensive opinions."""
    request, decision = _decide(tesoro, amount="0.001")
    advisor = StubAdvisor("REJECT")
    result = consult(request, decision, advisor)

    assert result.consulted is False
    assert advisor.calls == 0, "advisor was called despite failing the EIAP gate"
    assert result.final_verdict is decision.verdict
    assert "break-even" in result.skip_reason


def test_force_overrides_the_gate(tesoro):
    request, decision = _decide(tesoro, amount="0.001")
    advisor = StubAdvisor("REJECT")
    assert consult(request, decision, advisor, force=True).consulted is True
    assert advisor.calls == 1


def test_no_advisor_means_phase_1_behaviour(tesoro):
    request, decision = _decide(tesoro, amount="0.001")
    result = consult(request, decision, None)
    assert result.consulted is False
    assert result.advisor_cost_usd == 0.0
    assert result.final_verdict is decision.verdict


# --- advisor cost drives the threshold ------------------------------------


def test_cheaper_advisor_lowers_the_break_even(tmp_path):
    """The finding Phase 2 exists to surface.

    Break-even is `cost / p_flip`, so a 30x cheaper advisor moves the threshold
    across two of the three prices the x402 seller actually charges. Whether AI
    analysis is rational is not a property of the transaction alone.
    """
    thresholds = {}
    for model, cost in [("claude-haiku-4-5", None), ("llama-3.1-8b-instant", None)]:
        advisor = StubAdvisor("REVIEW", model=model)
        a = Tesoro(
            paths=Paths.ephemeral(tmp_path / model.replace("/", "_")),
            clock=FixedClock(BASE),
            advisor=advisor,
        )
        try:
            _, decision = _decide(a, amount="0.01")
            eiap = decision.intelligence.eiap
            thresholds[model] = (
                eiap.break_even_exposure_atomic,
                eiap.would_invoke,
            )
        finally:
            a.close()

    haiku_be, haiku_invoke = thresholds["claude-haiku-4-5"]
    groq_be, groq_invoke = thresholds["llama-3.1-8b-instant"]

    assert groq_be < haiku_be, "the cheaper advisor did not lower the threshold"
    assert groq_invoke is True, "$0.01 should justify a $0.00013 advisor"
    assert haiku_invoke is False, "$0.01 should not justify a $0.004 advisor"


def test_pricing_table_covers_every_offered_model():
    from tesoro.advisors import PRICING, providers

    for provider in providers():
        for model in provider.models:
            assert model in PRICING, (
                f"{provider.name}/{model} is offered but has no price, so the EIAP "
                "would silently fall back to a default and mis-price the gate"
            )
            assert estimate_call_cost_usd(model) > 0


# --- BYOK across four providers -------------------------------------------


def test_every_provider_is_constructible():
    """A provider in the registry must be buildable, key present or not."""
    from tesoro.advisors import build_advisor, providers

    for provider in providers():
        assert provider.models, f"{provider.name} offers no models"
        advisor = build_advisor(provider.name, provider.models[0])
        assert advisor.provider == provider.name
        ok, detail = advisor.available()
        # Without a key it must report *why*, not raise -- BYOK means a missing
        # key is a normal state the UI explains, not a crash.
        assert isinstance(ok, bool) and detail


def test_unknown_provider_names_the_known_ones():
    from tesoro.advisors import build_advisor

    with pytest.raises(ValueError, match="unknown advisor provider"):
        build_advisor("not-a-provider", "x")


def test_missing_key_degrades_instead_of_raising():
    """An advisor with no key must return REVIEW-with-error, never explode.

    The verdict then stands unchanged via the failure path, so a missing key costs
    the agent its second opinion and nothing else.
    """
    from tesoro.advisors import AdviceRequest, build_advisor

    advisor = build_advisor("openai", "gpt-4o-mini", api_key="")
    advice = advisor.advise(
        AdviceRequest(
            amount_usd=1.0, resource="/x", channel="external", vendor_name="V",
            vendor_id="v", vendor_is_new=True, vendor_settled_count=0,
            trust_score=0.2, risk_score=0.3, risk_flags=(), roi_ratio=None,
            budget_ok=True, budget_binding=None, budget_headroom_usd=10.0,
            deterministic_verdict="APPROVE", matched_rule="r",
        )
    )
    assert advice.ok is False
    assert advice.recommendation == "REVIEW"
    assert "OPENAI_API_KEY" in (advice.error or "")


def test_unpriced_model_fails_expensive_not_cheap():
    """An unknown model must make the gate reluctant, not eager.

    Defaulting an unpriced model to a cheap figure would silently open the gate on
    models we cannot cost -- the opposite of the safe direction.
    """
    from tesoro.advisors import FALLBACK_PRICE, estimate_call_cost_usd

    unknown = estimate_call_cost_usd("model-that-does-not-exist")
    assert unknown == estimate_call_cost_usd("claude-opus-5") or unknown > 0.01
    assert FALLBACK_PRICE[0] >= 1.0


def test_pricing_override_moves_the_gate():
    """Correcting a price must change what the EIAP will pay to analyse."""
    from tesoro.advisors import apply_pricing_overrides, estimate_call_cost_usd

    model = "test-override-model"
    apply_pricing_overrides({model: {"input_per_mtok": 10.0, "output_per_mtok": 50.0}})
    expensive = estimate_call_cost_usd(model)
    apply_pricing_overrides({model: {"input_per_mtok": 0.01, "output_per_mtok": 0.02}})
    cheap = estimate_call_cost_usd(model)
    assert cheap < expensive / 100


# --- phase 1 stays pure ---------------------------------------------------


def test_phase_1_engines_still_import_no_model_client():
    """Phase 2 adds SDKs, but only inside `advisors/`.

    If a decision engine could import a model client, the deterministic path
    would no longer be guaranteed free or fast.
    """
    import ast
    from pathlib import Path

    import tesoro

    root = Path(tesoro.__file__).resolve().parent
    # Walked, not listed. The S6 move into `engines/` left the old hardcoded list
    # naming files that are now three-line re-export shims -- so the test kept
    # passing while checking nothing. A list of filenames is a claim about the tree
    # that rots the moment the tree changes; walking it cannot.
    engines = sorted(
        [p.relative_to(root) for p in (root / "engines").rglob("*.py")]
        + [Path("authorize.py"), Path("domain.py"), Path("store.py")]
    )
    forbidden = {"anthropic", "groq", "openai", "claude_agent_sdk"}
    offenders = []

    for name in engines:
        tree = ast.parse((root / name).read_text(encoding="utf-8"), filename=str(name))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                if n.split(".")[0] in forbidden:
                    offenders.append(f"{name}:{node.lineno} imports {n}")

    assert not offenders, "a Phase 1 engine imported a model client:\n" + "\n".join(offenders)


# --- the facts the advisor is shown ---------------------------------------
#
# These exist because of a real defect: `consult()` hardcoded
# `vendor_settled_count=0`, so every advisor -- in every run, including live ones
# that blocked legitimate purchases -- was told each counterparty was a stranger
# with no history. The advice was sound; the input was not. Measured on a labelled
# set, the models cited "0 settled transactions" as their leading concern.
#
# The lesson generalises past this one field: an unmeasured value rendered as a
# zero is not a missing fact, it is a wrong one, and a model cannot tell the
# difference.


class CapturingAdvisor(StubAdvisor):
    """Records the AdviceRequest it was handed, so the facts can be asserted."""

    def __init__(self, recommendation: str = "APPROVE") -> None:
        super().__init__(recommendation)
        self.seen: AdviceRequest | None = None

    def advise(self, request: AdviceRequest) -> Advice:
        self.seen = request
        return super().advise(request)


def _settle_history(tesoro, count: int, vendor=SELLER, amount_atomic: int = 10_000) -> None:
    for i in range(count):
        tesoro.store.record(
            tx_id=f"hist-{i}", at=BASE, agent_id=tesoro.agent_id, vendor_id=vendor.id,
            resource="/market/signal", amount_atomic=amount_atomic,
            verdict=Verdict.APPROVE, settled=True, success=True,
        )


def test_advisor_is_told_the_real_settled_count(tesoro):
    advisor = CapturingAdvisor()
    _settle_history(tesoro, 9)
    request, decision = _decide(tesoro)

    consult(request, decision, advisor, force=True,
            snapshot=tesoro.snapshot_for(request, decision.decided_at))

    assert advisor.seen.vendor_settled_count == 9, "advisor was shown a fabricated history"
    assert advisor.seen.vendor_is_new is False
    assert "vendor_settled_transactions: 9" in advisor.seen.facts_block()


def test_an_unmeasured_count_reads_as_unknown_not_zero(tesoro):
    """No snapshot means no claim -- not a claim of zero."""
    advisor = CapturingAdvisor()
    request, decision = _decide(tesoro)

    consult(request, decision, advisor, force=True)  # no snapshot passed

    assert advisor.seen.vendor_settled_count is None
    assert "vendor_settled_transactions: unknown" in advisor.seen.facts_block()
    assert "vendor_settled_transactions: 0" not in advisor.seen.facts_block()


def test_the_advisor_sees_the_vendors_historical_price(tesoro):
    """Silent repricing is the case the deterministic engines let through.

    A vendor that has always charged $0.01 asking $0.25 stays inside every
    envelope, so no amount threshold fires. The advisor can only catch it if it
    is shown what the vendor used to charge.
    """
    advisor = CapturingAdvisor()
    _settle_history(tesoro, 8, amount_atomic=10_000)  # $0.01 each
    request, decision = _decide(tesoro, amount="0.25")

    consult(request, decision, advisor, force=True,
            snapshot=tesoro.snapshot_for(request, decision.decided_at))

    # `fmt_amount` spells small figures out -- six decimal places read as
    # thousands separators to a model, which it once demonstrably did.
    assert advisor.seen.vendor_median_price_text == "0.0100 USD"
    assert (
        "vendor_historical_price_for_this_resource: 0.0100 USD"
        in advisor.seen.facts_block()
    )


def test_runtime_advise_passes_the_snapshot_through(tesoro):
    """The plumbing, end to end -- the bug lived in exactly this gap."""
    tesoro.advisor = CapturingAdvisor()
    _settle_history(tesoro, 5)
    request = tesoro.build_request(resource="/market/signal", amount_usd="0.01", vendor=SELLER)

    tesoro.advise(request, force=True)

    assert tesoro.advisor.seen.vendor_settled_count == 5
