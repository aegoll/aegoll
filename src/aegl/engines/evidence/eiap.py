"""Economic Intelligence Activation Policy -- computed, never acted on.

Phase 1's cheapest research artifact. On every transaction we evaluate whether
invoking a model *would* be economically rational, log the answer, and then do
nothing with it. That calibrates Phase 2's thresholds against real traffic before
a single token is spent.

    invoke  <=>  E[improvement in decision quality] x exposure  >  cost_ai

Rearranged, the useful form is a break-even exposure:

    break_even = cost_ai / p_flip

With the measured Haiku 4.5 cost of ~$0.004 per analysis call and a deliberately
generous p_flip of 5%, break-even lands near **$0.08**. Every endpoint the x402
seller in this repo offers ($0.001-$0.01) sits an order of magnitude below that
line, which is the quantitative core of the deterministic-first argument.

This module must never import a model client. A test asserts that.
"""

from __future__ import annotations

from ...config import EiapConfig
from ...domain import (
    EiapEvaluation,
    PaymentRequest,
    RoiEstimate,
    Score,
    Term,
    Tier,
    fmt_usd,
)


def evaluate(
    request: PaymentRequest,
    trust: Score,
    risk: Score,
    roi: RoiEstimate,
    cfg: EiapConfig,
    ai_cost_atomic: int | None = None,
) -> EiapEvaluation:
    """`ai_cost_atomic` overrides the configured cost with the *chosen* advisor's.

    This matters more than it looks: break-even is `cost / p_flip`, so swapping a
    $0.004 Haiku call for a $0.00013 Groq call moves the threshold from ~$0.08 to
    ~$0.003 -- across two of the three prices the x402 seller charges. The policy
    is not "is AI worth it" but "is *this* AI worth it here".
    """
    exposure = request.amount_atomic
    cost_atomic = cfg.ai_cost_atomic if ai_cost_atomic is None else ai_cost_atomic

    # Uncertainty: how much a reasoning model could plausibly add. Highest when
    # value is unknown, the vendor is unfamiliar, and risk sits mid-band -- the
    # region where a deterministic rule is least confident.
    terms: list[Term] = []

    roi_unknown = 0.0 if roi.known else 1.0
    terms.append(
        Term(
            "roi_unknown",
            roi_unknown,
            0.40,
            "no declared expected value" if not roi.known else "expected value is known",
        )
    )

    novelty = 1.0 - trust.value
    terms.append(
        Term("vendor_unfamiliarity", novelty, 0.30, f"trust score {trust.value:.2f}")
    )

    # A risk score near 0.5 is maximally ambiguous; near 0 or 1 it is decided.
    ambiguity = 1.0 - abs(risk.value - 0.5) * 2
    terms.append(
        Term("risk_ambiguity", ambiguity, 0.30, f"risk score {risk.value:.2f}")
    )

    uncertainty = max(0.0, min(1.0, sum(t.contribution for t in terms)))

    # p_flip scales linearly between the configured base and max.
    p_flip = cfg.base_p_flip + (cfg.max_p_flip - cfg.base_p_flip) * uncertainty
    expected_gain = int(exposure * p_flip)
    break_even = int(cost_atomic / p_flip) if p_flip > 0 else 0

    would_invoke = expected_gain > cost_atomic

    if not would_invoke:
        tier = Tier.NONE
    elif exposure >= cfg.large_model_exposure_atomic:
        tier = Tier.LARGE
    elif exposure >= cfg.small_model_exposure_atomic:
        tier = Tier.SMALL
    else:
        tier = Tier.SMALL

    terms.append(
        Term(
            "break_even",
            1.0 if would_invoke else 0.0,
            0.0,
            (
                f"exposure {fmt_usd(exposure)} vs break-even {fmt_usd(break_even)} "
                f"(ai cost {fmt_usd(cost_atomic)} / p_flip {p_flip:.3f})"
            ),
        )
    )

    return EiapEvaluation(
        exposure_atomic=exposure,
        uncertainty=round(uncertainty, 4),
        p_flip=round(p_flip, 4),
        expected_gain_atomic=expected_gain,
        ai_cost_atomic=cost_atomic,
        break_even_exposure_atomic=break_even,
        would_invoke=would_invoke,
        would_tier=tier,
        terms=tuple(terms),
    )
