"""Engine 6 -- the authorization engine.

Composes the other engines into one `Decision`. Two invariants live here, and they
are the load-bearing part of the whole design:

1. **A policy rule can never widen a verdict.** If treasury refuses, no rule can
   approve. `narrower()` clamps the result, so a badly written policy file can
   make the system stricter but never looser.
2. **The decision is pure and hashed.** Same request + bundle + snapshot + clock
   yields a byte-identical `decision_hash`. That is what makes `aegl replay` a
   real check rather than a gesture.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from . import eiap as eiap_engine
from . import policy as policy_engine
from . import risk as risk_engine
from . import roi as roi_engine
from . import treasury as treasury_engine
from . import trust as trust_engine
from .clock import Clock, SystemClock
from .config import PolicyBundle
from .store import HistorySnapshot
from .domain import (
    Decision,
    Intelligence,
    PaymentRequest,
    Reason,
    Tier,
    Verdict,
    narrower,
)


@dataclass(frozen=True)
class Governor:
    """Holds the bundle and the clock; produces decisions."""

    bundle: PolicyBundle
    clock: Clock = SystemClock()
    # Phase 2: when set, the EIAP prices *this* advisor rather than the
    # bundle's nominal figure.
    advisor_cost_atomic: int | None = None

    def decide(
        self,
        request: PaymentRequest,
        snapshot: HistorySnapshot,
        intent_verdict: Any | None = None,
        identity_verdict: Any | None = None,
    ) -> Decision:
        """Decide one request.

        `intent_verdict` is supplied by the caller rather than computed here,
        because the intent engine needs a store lookup and these engines are pure
        by design (ADR-004) -- they read a snapshot and nothing else. `None` means
        the caller does not model intent at all, which is different from an agent
        having declared none.
        """
        started = time.perf_counter()
        now = snapshot.now

        # --- engines, cheapest first -------------------------------------
        treasury_cfg = self.bundle.treasury_for(request.channel)
        budget = treasury_engine.evaluate(request, snapshot, treasury_cfg)
        trust = trust_engine.evaluate(snapshot.vendor, now, self.bundle.trust)
        risk = risk_engine.evaluate(request, snapshot, self.bundle.risk)
        roi = roi_engine.evaluate(request, self.bundle.roi)

        facts = policy_engine.build_facts(request, trust, risk, roi, budget, snapshot)
        policy_result = policy_engine.evaluate(self.bundle, facts)

        # --- assemble the verdict ----------------------------------------
        reasons: list[Reason] = list(budget.reasons)
        verdict = policy_result.verdict
        reasons.append(policy_result.reason)

        # Invariant -1: identity clamps first of all. Everything downstream asks
        # what an actor may do; this asks whether the actor is still authorised to
        # act at all, and whether the authority it claims is actually its own. A
        # revoked agent's budget headroom is irrelevant.
        if identity_verdict is not None:
            reasons.extend(identity_verdict.reasons)
            clamped = narrower(verdict, identity_verdict.verdict)
            if clamped is not verdict:
                reasons.append(
                    Reason(
                        "authorize",
                        "clamped_by_identity",
                        f"policy said {verdict.value} but the acting agent is not "
                        f"authorised as claimed; narrowing to {clamped.value}",
                        clamped,
                    )
                )
                verdict = clamped

        # Invariant 0: intent clamps before anything else considers the action.
        # An agent can be permitted to spend and still be spending on the wrong
        # thing; nothing downstream can detect that, because every other engine is
        # asking whether the action is *allowed* rather than whether it is what the
        # agent was sent to do.
        if intent_verdict is not None:
            reasons.extend(intent_verdict.reasons)
            clamped = narrower(verdict, intent_verdict.verdict)
            if clamped is not verdict:
                reasons.append(
                    Reason(
                        "authorize",
                        "clamped_by_intent",
                        f"policy said {verdict.value} but the action is not "
                        f"consistent with the declared intent; narrowing to "
                        f"{clamped.value}",
                        clamped,
                    )
                )
                verdict = clamped

        # Invariant 1: treasury and hard risk floors clamp the policy verdict.
        if not budget.ok:
            clamped = narrower(verdict, Verdict.REVIEW)
            if clamped is not verdict:
                reasons.append(
                    Reason(
                        "authorize",
                        "clamped_by_treasury",
                        f"policy said {verdict.value} but a budget envelope "
                        f"({budget.binding}) is breached; narrowing to {clamped.value}",
                        clamped,
                    )
                )
                verdict = clamped

        if request.vendor.sanctioned:
            # Recorded whether or not it changes the verdict. If a policy rule
            # happened to refuse first, skipping this would leave a record showing
            # a *policy* refusal with no evidence that sanctions were considered at
            # all -- and an auditor could not tell a screened counterparty from a
            # lucky one. Delete the screening entirely and the policy rule would
            # still catch it, with nothing to notice the control had gone.
            already = verdict is Verdict.REJECT
            reasons.append(
                Reason(
                    "authorize",
                    "clamped_by_sanction",
                    "sanctioned counterparty is an absolute bar"
                    + ("; the verdict was already REJECT" if already else "; narrowing to REJECT"),
                    Verdict.REJECT,
                )
            )
            verdict = Verdict.REJECT

        if "high_risk" in risk.flags and verdict is Verdict.APPROVE:
            reasons.append(
                Reason(
                    "authorize",
                    "clamped_by_risk",
                    f"risk score {risk.value:.2f} is at or above the "
                    f"{self.bundle.risk.high_risk_threshold} threshold; "
                    "narrowing APPROVE to ESCALATE",
                    Verdict.ESCALATE,
                )
            )
            verdict = Verdict.ESCALATE

        # --- EIAP: compute, do not act -----------------------------------
        eiap = eiap_engine.evaluate(
            request, trust, risk, roi, self.bundle.eiap, self.advisor_cost_atomic
        )
        intelligence = Intelligence(
            required=Tier.NONE,  # Phase 1 never invokes a model
            would_escalate=eiap.would_invoke,
            eiap=eiap,
        )
        if eiap.would_invoke:
            reasons.append(
                Reason(
                    "eiap",
                    "would_invoke_ai",
                    f"Phase 2 would consult a {eiap.would_tier.value} model here "
                    f"(expected gain exceeds the ${eiap.ai_cost_atomic / 1e6:.4f} "
                    "analysis cost); Phase 1 decided deterministically",
                )
            )
        else:
            reasons.append(
                Reason(
                    "eiap",
                    "ai_not_economic",
                    f"exposure is below the break-even of "
                    f"${eiap.break_even_exposure_atomic / 1e6:.4f}; "
                    "invoking a model would destroy value",
                )
            )

        decided_at = self.clock.now()
        latency_us = (time.perf_counter() - started) * 1_000_000

        return Decision(
            request_id=request.id,
            verdict=verdict,
            reasons=tuple(reasons),
            trust=trust,
            risk=risk,
            roi=roi,
            budget=budget,
            intelligence=intelligence,
            matched_rule=policy_result.matched.id if policy_result.matched else None,
            policy_hash=self.bundle.hash,
            decision_hash=_decision_hash(request, self.bundle, snapshot, verdict),
            decided_at=decided_at,
            latency_us=latency_us,
        )

    def evaluate_rules(self, request: PaymentRequest, snapshot: HistorySnapshot):
        """Full per-rule trace, for the cockpit's policy panel."""
        budget = treasury_engine.evaluate(
            request, snapshot, self.bundle.treasury_for(request.channel)
        )
        trust = trust_engine.evaluate(snapshot.vendor, snapshot.now, self.bundle.trust)
        risk = risk_engine.evaluate(request, snapshot, self.bundle.risk)
        roi = roi_engine.evaluate(request, self.bundle.roi)
        facts = policy_engine.build_facts(request, trust, risk, roi, budget, snapshot)
        return facts, policy_engine.evaluate(self.bundle, facts)


def _decision_hash(
    request: PaymentRequest,
    bundle: PolicyBundle,
    snapshot: HistorySnapshot,
    verdict: Verdict,
) -> str:
    """Hash over everything that determined the verdict.

    Deliberately excludes wall-clock time of the decision itself -- `snapshot.now`
    is the time that mattered, and including a second timestamp would make replay
    impossible.
    """
    payload = {
        "request": {
            "id": request.id,
            "agent": request.agent_id,
            "vendor": request.vendor.id,
            "sanctioned": request.vendor.sanctioned,
            "resource": request.resource,
            "amount": request.amount_atomic,
            "purpose": request.purpose.value,
            "channel": request.channel.value,
            "expected_value": request.expected_value_atomic,
        },
        "policy": bundle.hash,
        "snapshot": {
            "now": snapshot.now.isoformat(),
            "today": snapshot.spent_today_atomic,
            "month": snapshot.spent_month_atomic,
            "vendor30d": snapshot.spent_vendor_30d_atomic,
            "resource30d": snapshot.spent_resource_30d_atomic,
            "c60": snapshot.count_last_60s,
            "c1h": snapshot.count_last_1h,
            "amounts": list(snapshot.agent_amounts),
            "vendor_settled": snapshot.vendor.settled_count,
            "vendor_failed": snapshot.vendor.failed_count,
            "vendor_disputed": snapshot.vendor.disputed_count,
            "vendor_first_seen": (
                snapshot.vendor.first_seen.isoformat() if snapshot.vendor.first_seen else None
            ),
        },
        "verdict": verdict.value,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
