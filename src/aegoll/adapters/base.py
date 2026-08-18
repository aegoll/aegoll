"""The adapter contracts. Two of them, and keeping them apart is the point.

A **framework** adapter governs the *internal* channel: the tokens an agent burns thinking. It
hooks a run — before it starts, once per step, and after it finishes — and the framework calls
it. A **rail** adapter governs the *external* channel: what the agent pays out. It wraps a
payment client and holds the signer, so the agent cannot pay without a decision.

Merging them would be tempting and wrong. They differ in currency, in counterparty, in failure
mode, and in *direction of control*: a framework calls the adapter, whereas a rail adapter is
called by the agent. When AP2 or a card rail arrives it will need the second contract and none
of the first, and a merged interface would make that a rewrite rather than an addition.

**Nothing here imports a framework**, at module level or otherwise. That is invariant 8, checked
by `tests/test_deps.py`, and it is what makes `aegoll` installable rather than integrated: the
dependency arrow points from the agent to this layer and never back. An agent does not import the
governor; the governor wraps the agent.

The contracts are **duck-typed** on purpose. Nothing subclasses, nothing registers, and
`tests/test_adapters.py` satisfies both with fakes and no framework installed. A contract that
can only be exercised with a real SDK present is a contract nobody checks.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

__all__ = [
    "PAYMENT_CLIENT_MEMBERS",
    "PaymentClient",
    "RunGuard",
    "conforms_as_payment_client",
]


class RunGuard:
    """The framework-facing hook: govern one run's token spend.

    Three calls, in order, and a framework adapter is nothing more than the code that arranges
    for a particular framework to make them:

        guard = RunGuard(governor, budget_usd="0.40")
        allowed, why = guard.start(model="claude-sonnet-4", provider="anthropic")
        if not allowed:
            return why                        # the run never begins
        ...
        if guard.should_stop(spent_so_far):   # once per step
            break
        guard.finish(actual_cost)             # what it really cost

    **A governor is optional.** With none, every call allows — so an agent written against this
    runs identically with governance absent, which is what keeps this a *layer*. The alternative
    is `if governor:` scattered through an agent loop, and the branch that gets forgotten is
    always the one that mattered.

    **`should_stop()` exists because step ceilings are not spend ceilings.** Of the frameworks
    this was built against, only the Claude Agent SDK ships a cost ceiling. LangGraph has
    `recursion_limit`; Google ADK has `max_llm_calls`. Both count *steps*, and one long-context
    call can cost more than fifty short ones — so without this there is nothing between a
    runaway agent and its provider bill.
    """

    def __init__(self, governor: Any | None = None, *, budget_usd: str | int | None = None):
        self.governor = governor
        self.budget_usd = budget_usd
        #: The decision that let this run start, or `None` if it never asked.
        self.authorization: Any | None = None
        #: The decision that stopped it, or `None`.
        self.stopped_by: Any | None = None
        #: True when the *declared run budget* stopped it rather than a policy envelope. Two
        #: different facts: one is adjustable by the caller, the other by whoever owns the pack.
        self._stopped_by_run_budget = False
        self._spent_usd: str | None = None

    @property
    def active(self) -> bool:
        """Whether anything is actually governing. `False` is a valid, honest state."""
        return self.governor is not None

    # --- the three calls --------------------------------------------------

    def start(self, *, model: str = "", provider: str = "") -> tuple[bool, str]:
        """Ask permission to begin. Returns `(allowed, reason_if_not)`.

        With no governor, or a governor and no named budget, this allows: a governance layer
        should not invent a ceiling the caller never asked for. `model` and `provider` are
        recorded rather than interpreted — they are what makes the evidence answer *what was
        this spent on*, and a policy pack may match on them.
        """
        if not self.active or self.budget_usd is None:
            return True, ""

        decision = self.governor.authorize(
            amount_usd=self.budget_usd,
            vendor=provider or "internal",
            resource=f"model:{model}" if model else "model:unspecified",
            channel="internal",
        )
        self.authorization = decision
        if decision.approved:
            return True, ""

        return False, (
            f"governance refused this run's token budget of {self.budget_usd} "
            f"({decision.verdict.value}): {decision.reason or 'no reason recorded'} "
            f"[{decision.attributed_control}]"
        )

    def should_stop(self, spent_usd: str | int) -> bool:
        """The mid-run ceiling. `True` means stop now. Call once per step.

        Asks the layer rather than comparing numbers here, so the ceiling is whatever the policy
        pack says — including limits this guard knows nothing about, like a daily envelope that a
        long run crosses partway through. A guard that only compared against `budget_usd` would
        enforce the one limit it was told and silently ignore the rest.

        An unauthorised run never stops, because it never started. `authorization` is recorded
        even for a refusal -- the refusal is evidence -- so this checks that it *approved*, not
        merely that it exists. Without that, a refused run reported itself as stopped, and a
        refusal and a stop are different facts with different remedies.

        **Two ceilings, and the tighter one wins.** The declared `budget_usd` is checked here as
        well as at `start()`, because for a while it was not: a caller passing
        `budget_usd="0.02"` could spend four times that without a stop, since only the policy
        pack was consulted and its micro-approval threshold sat higher. The run budget is a limit
        the caller set, and invariant 6 applies to this guard as much as to an engine -- nothing
        may widen a limit somebody else declared.

        Exceeding is the trigger, not reaching. Spending exactly the budget is within it, which
        matches how every envelope treats its own boundary.
        """
        if not self.active or self.authorization is None or not self.authorization.approved:
            return False

        self._spent_usd = str(spent_usd)

        if self._over_declared_budget(spent_usd):
            # No `decide()` call here, and that is deliberate. The first version asked the layer
            # anyway and stored the answer -- which was an **approval**, because the spend was
            # fine by the policy. `stopped_by.verdict` then read `APPROVE` on a run that had just
            # been stopped, which is a contradiction a reader has to unpick. The caller's own
            # ceiling is not a policy verdict and should not be dressed as one.
            self._stopped_by_run_budget = True
            return True

        decision = self.governor.decide(
            amount_usd=spent_usd,
            vendor="internal",
            resource="model:continue",
            channel="internal",
        )
        if decision.approved:
            return False
        self.stopped_by = decision
        return True

    def _over_declared_budget(self, spent_usd: str | int) -> bool:
        """Whether spending has passed the budget the caller named for this run.

        Compared in integer atomic units, never as floats -- invariant 3 does not stop applying
        because the number came from a token counter rather than a payment.
        """
        if self.budget_usd is None:
            return False

        from ..domain import usd_to_atomic  # noqa: PLC0415

        def atomic(value: str | int) -> int:
            return value if isinstance(value, int) else usd_to_atomic(str(value))

        return atomic(spent_usd) > atomic(self.budget_usd)

    def finish(self, actual_cost_usd: str | int | None = None, *, success: bool = True) -> None:
        """Record what the run actually cost. Envelopes consume here.

        Skipping this leaves the layer knowing what it authorised and never learning what
        happened: nothing is consumed, and the next run sees a budget that was never spent.
        """
        if not self.active or self.authorization is None:
            return
        if not self.authorization.approved:
            # A refused run spent nothing, and consuming budget for it would report spending
            # that never occurred -- the same reasoning as a failed settlement consuming nothing.
            return
        self.governor.settle(
            self.authorization, success=success, actual_amount_usd=actual_cost_usd
        )

    # --- the external channel, handed straight through --------------------

    def wrap(self, client: Any) -> Any:
        """Put a payment client behind the governor, if there is one.

        Present so an agent needs no `if governor:` branch here either. The rail contract itself
        is `PaymentClient` below; this is only the optionality.
        """
        return self.governor.wrap(client) if self.active else client

    # --- what happened ----------------------------------------------------

    @property
    def stopped(self) -> dict[str, Any] | None:
        """What stopped this run, or `None` if nothing did.

        One shape for both ceilings, because a caller should not have to know which one fired to
        read the answer. A run-budget stop has no verdict and no attributed control -- there was
        no policy decision, only the number this caller passed -- and saying `None` for those is
        more honest than borrowing a verdict from an unrelated approval.
        """
        if self._stopped_by_run_budget:
            return {
                "stoppedBy": "run-budget",
                "verdict": None,
                "attributedControl": None,
                "reason": (
                    f"spent {self._spent_usd} against a declared run budget of {self.budget_usd}"
                ),
                "spentUsd": self._spent_usd,
            }
        if self.stopped_by is None:
            return None
        return {
            "stoppedBy": self.stopped_by.attributed_control,
            "verdict": self.stopped_by.verdict.value,
            "attributedControl": self.stopped_by.attributed_control,
            "reason": self.stopped_by.reason,
            "spentUsd": self._spent_usd,
        }

    @property
    def stop_reason(self) -> str:
        """Which ceiling stopped the run, or `""`.

        Not a boolean, because *why* is the useful part and a caller that only knows *whether*
        has to guess. `run-budget` is distinguished from a control name because the two are
        adjusted in different places: one is the number this caller passed, the other is the
        policy pack.
        """
        stopped = self.stopped
        return stopped["stoppedBy"] if stopped else ""

    def as_dict(self) -> dict[str, Any]:
        """For telemetry. `governed: false` is itself evidence — it distinguishes a run nobody
        checked from one that was checked and passed."""
        return {
            "governed": self.active,
            "budgetUsd": str(self.budget_usd) if self.budget_usd is not None else None,
            "authorized": bool(self.authorization and self.authorization.approved),
            "stopped": self.stopped,
        }


#: The members a payment client must have. Named as data so a refusal can *list what is
#: missing*: `isinstance` against a runtime protocol returns a bare `False`, which tells an
#: integrator nothing about what to add.
PAYMENT_CLIENT_MEMBERS = (
    "address",
    "spend_cap_usd",
    "total_spent_usd",
    "calls",
    "budget_snapshot",
    "quote",
    "get_free",
    "get_paid",
    "aclose",
)


@runtime_checkable
class PaymentClient(Protocol):
    """The rail-facing contract: what a client must offer to be governed.

    Structural, so nothing subclasses and nothing registers. The *rail* is what varies here —
    x402 today, AP2 or a card rail later — and this is the boundary that will change when one
    arrives. It is deliberately not `RunGuard`: see the module docstring.
    """

    address: str

    def quote(self, *args: Any, **kwargs: Any) -> Any: ...
    def get_free(self, *args: Any, **kwargs: Any) -> Any: ...
    def get_paid(self, *args: Any, **kwargs: Any) -> Any: ...
    def budget_snapshot(self) -> dict[str, Any]: ...
    async def aclose(self) -> None: ...


def conforms_as_payment_client(client: Any) -> tuple[bool, list[str]]:
    """Whether this object can be governed as a payment client, and what it lacks.

    Returns the missing member names rather than a bare boolean, because "does not conform" is
    unactionable and "missing: quote, aclose" is a to-do list.
    """
    missing = [name for name in PAYMENT_CLIENT_MEMBERS if not hasattr(client, name)]
    return not missing, missing
