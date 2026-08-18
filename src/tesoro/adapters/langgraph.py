"""LangGraph adapter. `pip install tesoro[langgraph]`.

LangGraph caps `recursion_limit`. That is a **step** ceiling and says nothing about money: one
long-context call can cost more than fifty short ones, so `recursion_limit=25` bounds the graph's
shape and not its bill. This adapter supplies the spend ceiling LangGraph does not have.

Same situation as Google ADK, and deliberately not the same as the Claude Agent SDK — there,
`max_budget_usd` already exists and the adapter's job is making two ceilings agree. Here
`should_stop()` is the only thing watching money.

`recursion_limit` is still worth keeping. A cycle whose steps are nearly free is a real failure
mode, and a spend ceiling notices it only slowly. The two bound different things, so
`config_for()` leaves the caller's value alone rather than deriving one from the other.

Verified against the installed `RunnableConfig`, whose keys are `callbacks`, `configurable`,
`max_concurrency`, `metadata`, `recursion_limit`, `run_id`, `run_name` and `tags` — so
`recursion_limit` and `callbacks` are the two this adapter touches, and both are real.

**No import of `langgraph` or `langchain` anywhere in this module** — invariant 8, and it keeps
the adapter testable with nothing installed.
"""

from __future__ import annotations

from typing import Any

from .base import RunGuard

__all__ = ["LangGraphAdapter"]


class LangGraphAdapter:
    """Govern a LangGraph run.

        adapter = LangGraphAdapter(Governor.load(), budget_usd="0.40")

        allowed, why = adapter.before_run(model="gpt-4o-mini")
        if not allowed:
            raise SystemExit(why)

        config = adapter.config_for({"recursion_limit": 25})
        result = graph.invoke(state, config=config)
        adapter.after_run(actual_cost_usd=total)

    A governor of `None` means ungoverned, and every call allows.
    """

    channel = "internal"

    #: LangGraph's own default. Kept as a floor so a graph with a cycle cannot run unbounded
    #: while its spend ceiling is still counting up.
    DEFAULT_RECURSION_LIMIT = 25

    def __init__(
        self,
        governor: Any | None = None,
        *,
        budget_usd: str | int | None = None,
        provider: str = "openai",
    ) -> None:
        self.guard = RunGuard(governor, budget_usd=budget_usd)
        self.provider = provider

    # --- the run ----------------------------------------------------------

    def before_run(self, *, model: str = "") -> tuple[bool, str]:
        """Ask permission before the graph is invoked. `(allowed, reason_if_not)`."""
        return self.guard.start(model=model, provider=self.provider)

    def should_stop(self, spent_usd: str | int) -> bool:
        """The spend ceiling LangGraph lacks. `True` means stop now.

        Call from a node, or from the callback `callback_for()` builds. Nothing else in a
        LangGraph run is watching money, so this is the belt rather than a second opinion.
        """
        return self.guard.should_stop(spent_usd)

    def after_run(
        self, *, actual_cost_usd: str | int | None = None, success: bool = True
    ) -> None:
        """Record what the run cost. Envelopes consume here, not at `before_run`."""
        self.guard.finish(actual_cost_usd, success=success)

    # --- LangGraph's own configuration ------------------------------------

    def config_for(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        """A `RunnableConfig` with a step ceiling present, and never loosened.

        Only fills `recursion_limit` in when the caller named none, and never raises a value
        they set — the same rule every adapter here follows, because a governance layer that
        widened somebody's limit would be doing the one thing no control in this system may do.
        """
        out = dict(config or {})
        out.setdefault("recursion_limit", self.DEFAULT_RECURSION_LIMIT)
        return out

    def callback_for(self, spent_usd_getter: Any) -> Any:
        """A callback object that raises when the governed budget is reached.

        LangGraph has no "stop politely" return value the way ADK's `before_model_callback`
        does: a graph ends when a node says so or when something raises. So this raises
        `GovernedBudgetExceeded`, which is a governance decision surfacing as control flow
        rather than an error — the exception carries the attributed control and the reason, so
        whoever catches it can say *which* limit stopped the run.

        The getter is a callable rather than a number because the callback is built once and
        invoked many times; a number captured here would be the spend before the run began,
        i.e. always zero, and the ceiling would never trip.
        """
        adapter = self

        class _GovernanceCallback:
            """Duck-typed to LangChain's callback protocol without importing it.

            Only `on_llm_start` is implemented, because that is the moment before money is
            spent. A callback firing *after* a call has already cost what it cost is a report,
            not a control.
            """

            def on_llm_start(self, *args: Any, **kwargs: Any) -> None:
                if adapter.should_stop(spent_usd_getter()):
                    stopped = adapter.guard.stopped_by
                    raise GovernedBudgetExceeded(
                        f"governance stopped this run: "
                        f"{stopped.reason if stopped else 'budget reached'} "
                        f"[{adapter.guard.stop_reason}]",
                        attributed_control=adapter.guard.stop_reason,
                    )

            def __getattr__(self, name: str) -> Any:
                """Every other callback hook is a no-op.

                LangChain's callback surface is wide and still moving. Answering every unknown
                hook with a no-op means a new one cannot break a governed run, and this adapter
                never has to track a protocol it does not participate in.
                """
                def _noop(*_args: Any, **_kwargs: Any) -> None:
                    return None

                return _noop

        return _GovernanceCallback()

    def as_dict(self) -> dict[str, Any]:
        return {"framework": "langgraph", **self.guard.as_dict()}


class GovernedBudgetExceeded(RuntimeError):
    """Raised when a governed LangGraph run reaches its ceiling mid-graph.

    A `RuntimeError` rather than a custom base, so a caller who catches broadly still catches
    this. It carries the attributed control because "the run was stopped" is not actionable and
    "the treasury per-transaction ceiling stopped it" is.
    """

    def __init__(self, message: str, *, attributed_control: str = "") -> None:
        super().__init__(message)
        self.attributed_control = attributed_control
