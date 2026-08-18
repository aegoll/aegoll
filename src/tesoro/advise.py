"""Phase 2 orchestration: decide, then consult -- but only when it pays.

    deterministic engines
            |
            v
          EIAP  --- not worth it ---> done, $0 spent
            |
      worth it
            |
            v
        advisor (BYOK)
            |
            v
     narrower(deterministic, advice)  <-- the advisor can only tighten
            |
            v
         verdict

Three properties, in order of importance:

1. **The advisor cannot widen a verdict.** `narrower()` clamps it. A successful
   prompt injection in vendor text achieves nothing except getting its own
   transaction blocked.
2. **The advisor is not always consulted.** The EIAP gate is what makes this
   economically coherent: spending $0.004 of reasoning on a $0.001 purchase
   destroys value, and the system refuses to do it.
3. **Advisor cost is recorded and fed back.** The cost of thinking is part of the
   economic picture, not a free side-channel.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .advisors import Advice, AdviceRequest, Advisor, estimate_call_cost_usd, fmt_amount
from .domain import Decision, PaymentRequest, Reason, Tier, Verdict, narrower


@dataclass(frozen=True)
class AdvisedDecision:
    """A deterministic decision, optionally revised by an advisor."""

    decision: Decision
    final_verdict: Verdict
    consulted: bool
    advice: Advice | None = None
    skip_reason: str = ""
    changed: bool = False
    reasons: tuple[Reason, ...] = ()

    @property
    def advisor_cost_usd(self) -> float:
        return self.advice.cost_usd if self.advice else 0.0

    @property
    def approved(self) -> bool:
        return self.final_verdict is Verdict.APPROVE

    def explain(self) -> list[str]:
        return [f"[{r.source}/{r.code}] {r.detail}" for r in self.reasons]

    def as_dict(self) -> dict[str, Any]:
        return {
            "deterministicVerdict": self.decision.verdict.value,
            "finalVerdict": self.final_verdict.value,
            "consulted": self.consulted,
            "changed": self.changed,
            "skipReason": self.skip_reason,
            "advisorCostUsd": round(self.advisor_cost_usd, 8),
            "advice": self.advice.as_dict() if self.advice else None,
            "reasons": [r.as_dict() for r in self.reasons],
            "decision": self.decision.as_dict(),
        }


def _tier_for(advisor: Advisor | None) -> Tier:
    if advisor is None:
        return Tier.NONE
    cost = estimate_call_cost_usd(advisor.model)
    # Cheap models are the "small" tier; expensive reasoning models the "large".
    return Tier.SMALL if cost < 0.001 else Tier.LARGE


def consult(
    request: PaymentRequest,
    decision: Decision,
    advisor: Advisor | None,
    *,
    vendor_description: str = "",
    force: bool = False,
    respect_eiap: bool = True,
    snapshot: Any | None = None,
) -> AdvisedDecision:
    """Optionally consult an advisor about an already-made decision.

    `force` bypasses the EIAP gate -- useful for evaluating advice quality on
    traffic the policy would not normally pay to analyse. `respect_eiap=False`
    consults always, which is the "every transaction gets an LLM" baseline the
    architecture argues against; it exists so the comparison can be measured
    rather than asserted.
    """
    reasons: list[Reason] = list(decision.reasons)
    eiap = decision.intelligence.eiap

    if advisor is None:
        return AdvisedDecision(
            decision=decision,
            final_verdict=decision.verdict,
            consulted=False,
            skip_reason="no advisor configured",
            reasons=tuple(reasons),
        )

    ok, detail = advisor.available()
    if not ok:
        reasons.append(
            Reason("advisor", "unavailable", f"advisor not usable: {detail}")
        )
        return AdvisedDecision(
            decision=decision,
            final_verdict=decision.verdict,
            consulted=False,
            skip_reason=detail,
            reasons=tuple(reasons),
        )

    # --- the gate --------------------------------------------------------
    if respect_eiap and not force and not eiap.would_invoke:
        skip = (
            f"exposure ${eiap.exposure_atomic / 1e6:.6f} is below the "
            f"${eiap.break_even_exposure_atomic / 1e6:.6f} break-even for "
            f"{advisor.model}; consulting would destroy value"
        )
        reasons.append(Reason("eiap", "advisor_not_economic", skip))
        return AdvisedDecision(
            decision=decision,
            final_verdict=decision.verdict,
            consulted=False,
            skip_reason=skip,
            reasons=tuple(reasons),
        )

    # --- consult ---------------------------------------------------------
    # The counterparty's record. Without a snapshot these stay None/unknown --
    # deliberately, because telling an advisor a vendor has "0 settled
    # transactions" when the number was never measured makes every established
    # counterparty look like a stranger, and the advisor blocks accordingly.
    settled = failed = disputed = None
    age_days: float | None = None
    median_price = "unknown"
    if snapshot is not None:
        stats = snapshot.vendor
        settled, failed, disputed = (
            stats.settled_count, stats.failed_count, stats.disputed_count,
        )
        age_days = stats.age_days(snapshot.now)
        med = snapshot.vendor_resource_median(request.resource)
        if med is not None:
            median_price = fmt_amount(med / 1e6)

    advice_request = AdviceRequest(
        amount_usd=float(request.amount_usd),
        resource=request.resource,
        channel=request.channel.value,
        vendor_name=request.vendor.display,
        vendor_id=request.vendor.id,
        vendor_is_new=(settled == 0) if settled is not None else decision.trust.value <= 0.3,
        vendor_settled_count=settled,
        vendor_failed_count=failed or 0,
        vendor_disputed_count=disputed or 0,
        vendor_age_days=age_days,
        vendor_median_price_text=median_price,
        trust_score=decision.trust.value,
        risk_score=decision.risk.value,
        risk_flags=decision.risk.flags,
        roi_ratio=decision.roi.ratio,
        budget_ok=decision.budget.ok,
        budget_binding=decision.budget.binding,
        budget_headroom_usd=float(decision.budget.headroom_atomic) / 1e6,
        deterministic_verdict=decision.verdict.value,
        matched_rule=decision.matched_rule,
        vendor_description=vendor_description,
    )

    advice = advisor.advise(advice_request)

    if not advice.ok:
        reasons.append(
            Reason(
                "advisor",
                "call_failed",
                f"{advisor.provider}/{advisor.model} failed ({advice.error}); the "
                "deterministic verdict stands unchanged",
            )
        )
        return AdvisedDecision(
            decision=decision,
            final_verdict=decision.verdict,
            consulted=True,
            advice=advice,
            skip_reason=advice.error or "",
            reasons=tuple(reasons),
        )

    # --- clamp: the advisor may tighten, never loosen --------------------
    recommended = Verdict(advice.recommendation)
    final = narrower(decision.verdict, recommended)
    changed = final is not decision.verdict

    reasons.append(
        Reason(
            "advisor",
            f"{advisor.provider}:{advisor.model}",
            f"recommended {recommended.value} "
            f"(confidence {advice.confidence:.2f}, cost ${advice.cost_usd:.6f}, "
            f"{advice.latency_ms:.0f} ms): {advice.rationale}",
            recommended,
        )
    )

    if advice.injection_suspected:
        reasons.append(
            Reason(
                "advisor",
                "injection_suspected",
                "the advisor flagged the vendor-supplied text as containing "
                "instructions aimed at it rather than a service description",
                Verdict.REJECT,
            )
        )
        final = narrower(final, Verdict.REJECT)
        changed = final is not decision.verdict

    if changed:
        reasons.append(
            Reason(
                "advisor",
                "tightened",
                f"advice narrowed the verdict from {decision.verdict.value} to "
                f"{final.value}",
                final,
            )
        )
    elif recommended is not decision.verdict:
        reasons.append(
            Reason(
                "advisor",
                "clamped",
                f"advisor recommended {recommended.value}, which is more permissive "
                f"than the deterministic {decision.verdict.value}; ignored -- an "
                "advisor can only tighten",
            )
        )

    for concern in advice.concerns:
        reasons.append(Reason("advisor", "concern", concern))

    return AdvisedDecision(
        decision=decision,
        final_verdict=final,
        consulted=True,
        advice=advice,
        changed=changed,
        reasons=tuple(reasons),
    )
