"""Claude Agent SDK adapter. `pip install aegoll[claude]`.

The Agent SDK is the one framework of the three verified in the prototype that ships a cost
ceiling of its own: `max_budget_usd` stops a run when estimated LLM cost reaches it. So this
adapter's job is **not** to add a ceiling the SDK lacks — it is to make the SDK's ceiling and
the governance layer's agree, and to supply what a single per-run number cannot express.

What a governed run gets that `max_budget_usd` alone does not:

* a **cumulative** budget, so ten runs of $0.40 cannot quietly cost $4.00 against a $1.00 day;
* a **decision recorded as evidence**, attributable to the control that made it;
* a refusal *before the run starts*, rather than a stop partway through with tokens already
  spent.

`max_budget_usd` stays on as well, and deliberately: it is enforced inside the SDK, closer to
the token accounting than this layer can get, and two independent ceilings are better than one.
`options_for()` returns the tighter of the two so neither is bypassed.

**No import of `claude_agent_sdk` anywhere in this module.** The adapter takes what the SDK
gives and hands back plain data — so it is testable with no SDK installed, and the core stays
free of a framework dependency (invariant 8).
"""

from __future__ import annotations

from typing import Any

from .base import RunGuard

__all__ = ["ClaudeAgentAdapter"]

#: The SDK's result subtype when its own ceiling trips. Named here so a caller can tell "the
#: SDK stopped this" apart from "the governance layer stopped this" -- two different facts, and
#: reporting one as the other sends whoever is debugging to the wrong place.
SDK_BUDGET_STOP = "error_max_budget_usd"


class ClaudeAgentAdapter:
    """Govern a Claude Agent SDK run.

        adapter = ClaudeAgentAdapter(Governor.load(), budget_usd="0.40")

        allowed, why = adapter.before_run(model="claude-sonnet-4")
        if not allowed:
            raise SystemExit(why)

        options = adapter.options_for({"model": "claude-sonnet-4"})   # carries max_budget_usd
        ...                                                            # run the SDK
        adapter.after_run(actual_cost_usd=result.total_cost_usd)

    A governor of `None` is valid and means ungoverned: every call allows, and an agent written
    against this adapter behaves exactly as it did before governance existed.
    """

    #: What this adapter governs. The SDK's own ceiling is internal-channel too, which is why
    #: the two can be compared at all.
    channel = "internal"

    def __init__(
        self,
        governor: Any | None = None,
        *,
        budget_usd: str | int | None = None,
        provider: str = "anthropic",
    ) -> None:
        self.guard = RunGuard(governor, budget_usd=budget_usd)
        self.provider = provider

    # --- the run ----------------------------------------------------------

    def before_run(self, *, model: str = "") -> tuple[bool, str]:
        """Ask permission before any tokens are spent. `(allowed, reason_if_not)`."""
        return self.guard.start(model=model, provider=self.provider)

    def should_stop(self, spent_usd: str | int) -> bool:
        """Call from the SDK's per-step hook. `True` means stop now.

        Belt and braces: the SDK enforces `max_budget_usd` itself, and this catches the limits it
        cannot see — a daily envelope crossed partway through a run, a per-vendor ceiling, a
        counterparty whose trust changed. `max_budget_usd` knows about this run; the layer knows
        about every run.
        """
        return self.guard.should_stop(spent_usd)

    def after_run(
        self, *, actual_cost_usd: str | int | None = None, success: bool = True
    ) -> None:
        """Record what the run cost. Envelopes consume here, not at `before_run`."""
        self.guard.finish(actual_cost_usd, success=success)

    # --- meeting the SDK's own ceiling ------------------------------------

    def options_for(self, options: dict[str, Any] | None = None) -> dict[str, Any]:
        """The SDK options dict with `max_budget_usd` set to the tighter of the two ceilings.

        Never *raises* the caller's own limit. If they asked for `max_budget_usd=0.10` and the
        governed budget is `$0.40`, the answer is `0.10`: a governance layer that widened a limit
        the caller set would be doing the one thing no control in this system may do.

        A float, because that is what the SDK's parameter takes. It is the only float in the
        package's public behaviour and it is at the boundary of somebody else's API — the money
        path itself never sees it, and the governed decision was made in integer atomic units
        before this is called.
        """
        out = dict(options or {})
        ceiling = self._governed_ceiling_usd()
        if ceiling is None:
            return out

        existing = out.get("max_budget_usd")
        out["max_budget_usd"] = (
            min(float(existing), ceiling) if existing is not None else ceiling
        )
        return out

    def _governed_ceiling_usd(self) -> float | None:
        """This run's governed ceiling in dollars, or `None` if nothing governs it."""
        if self.guard.budget_usd is None or not self.guard.active:
            return None
        from ..domain import atomic_to_usd, usd_to_atomic  # noqa: PLC0415

        return float(atomic_to_usd(usd_to_atomic(str(self.guard.budget_usd))))

    # --- what happened ----------------------------------------------------

    def stopped_by_sdk(self, result: Any) -> bool:
        """Whether the **SDK's** own ceiling ended this run, rather than the layer's.

        Two different facts. Reporting one as the other sends whoever is debugging to the wrong
        ceiling, and both are adjustable in different places.
        """
        subtype = getattr(result, "subtype", None) or (
            result.get("subtype") if isinstance(result, dict) else None
        )
        return subtype == SDK_BUDGET_STOP

    def as_dict(self) -> dict[str, Any]:
        return {"framework": "claude-agent-sdk", **self.guard.as_dict()}
