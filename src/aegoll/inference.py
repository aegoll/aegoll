"""The internal channel: AEGL governing the agent's own token spend.

The external channel asks *"may I buy this data?"*. The internal channel asks
*"may I afford to think about it?"* -- and it is real money on an API key, not
testnet USDC.

Both go through the same engines. That is the point: an agent's inference spend is
an economic decision like any other, and the moment it is governed by the same
policy engine as its purchases, you can express things you could not before --
"this agent may reason for at most $0.04 to decide a $0.01 purchase" is a rule,
not a hard-coded constant.

Two gates:

* `authorize_run()` -- before the agent starts, asks whether it may commit the
  run's token budget. Returns a `Decision`; a non-APPROVE means don't start.
* `settle_run()` -- afterwards, records what was actually spent, which is what
  builds the history the next `authorize_run()` reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .domain import Channel, Decision, Purpose, Vendor, atomic_to_usd, usd_to_atomic
from .runtime import Aegoll

# The LLM provider is a counterparty like any other. It happens to be a highly
# reliable one, which the trust engine will work out from settlement history.
ANTHROPIC = Vendor(id="anthropic-api", name="Anthropic API", tags=("llm", "internal"))


@dataclass
class RunAuthorization:
    """The outcome of asking permission to spend tokens on one agent run."""

    decision: Decision
    request_id: str
    budget_usd: float
    model: str

    @property
    def allowed(self) -> bool:
        return self.decision.approved

    @property
    def blocking_engine(self) -> str:
        """Which engine actually stopped this, for the UI.

        The most specific objection wins: an explicit clamp is more informative
        than the policy rule that preceded it.
        """
        for reason in reversed(self.decision.reasons):
            if reason.source in ("authorize", "treasury", "risk") and reason.verdict:
                return reason.source
        return "policy"

    def as_dict(self) -> dict[str, Any]:
        return {
            "requestId": self.request_id,
            "verdict": self.decision.verdict.value,
            "budgetUsd": self.budget_usd,
            "model": self.model,
            "matchedRule": self.decision.matched_rule,
            "blockingEngine": None if self.allowed else self.blocking_engine,
            "reasons": self.decision.explain(),
        }


class InferenceGate:
    """Governs the internal (token) channel."""

    def __init__(self, aegoll: Aegoll, vendor: Vendor = ANTHROPIC) -> None:
        self.aegoll = aegoll
        self.vendor = vendor

    def authorize_run(
        self,
        *,
        model: str,
        budget_usd: float | str,
        expected_value_usd: float | str | None = None,
        request_id: str | None = None,
    ) -> RunAuthorization:
        """Ask whether the agent may commit `budget_usd` of tokens to a run.

        `resource` is the model id, so per-resource envelopes become per-model
        spending limits for free -- "no more than $1/month on Opus" is expressible
        without new code.
        """
        request = self.aegoll.build_request(
            resource=f"llm:{model}",
            amount_usd=budget_usd,
            vendor=self.vendor,
            purpose=Purpose.INFERENCE,
            channel=Channel.INTERNAL,
            request_id=request_id,
            expected_value_usd=expected_value_usd,
        )
        decision = self.aegoll.authorize(request)
        return RunAuthorization(
            decision=decision,
            request_id=request.id,
            budget_usd=float(usd_to_atomic(budget_usd)) / 1e6,
            model=model,
        )

    def settle_run(
        self, request_id: str, *, actual_cost_usd: float, success: bool = True
    ) -> None:
        """Record what the run actually cost.

        The authorization reserved the *budget*; this records the *spend*, which is
        what the next run's envelopes are computed from. Reserving the ceiling and
        settling the actual is the same shape as the external channel, where the
        quote is authorized and the settled amount recorded.
        """
        actual_atomic = usd_to_atomic(actual_cost_usd)
        self.aegoll.store.mark_settled(request_id, None, success=success)
        # Correct the recorded amount from budget to actual.
        self.aegoll.store._conn.execute(  # noqa: SLF001 - deliberate, same package
            "UPDATE transactions SET amount_atomic=? WHERE id=?",
            (actual_atomic, request_id),
        )
        self.aegoll.store._conn.commit()  # noqa: SLF001
        self.aegoll.audit.attach_settlement(
            request_id,
            {
                "channel": Channel.INTERNAL.value,
                "success": success,
                "actualCostUsd": float(atomic_to_usd(actual_atomic)),
            },
        )

    def budget_state(self) -> dict[str, Any]:
        """Internal-channel envelope state, for the UI."""
        from . import treasury as treasury_engine

        probe = self.aegoll.build_request(
            resource="llm:probe",
            amount_usd="0",
            vendor=self.vendor,
            purpose=Purpose.INFERENCE,
            channel=Channel.INTERNAL,
        )
        snapshot = self.aegoll.snapshot_for(probe)
        verdict = treasury_engine.evaluate(
            probe, snapshot, self.aegoll.bundle.treasury_internal
        )
        return verdict.as_dict()
