"""Google ADK adapter. `pip install aegoll[adk]`.

Google ADK caps `max_llm_calls`. That is a **step** ceiling, and a step ceiling is not a spend
ceiling: one long-context call can cost more than fifty short ones, so `max_llm_calls=50` bounds
nothing about money. This adapter supplies the ceiling ADK does not have.

That difference from the Claude adapter is the whole reason both exist. There, the SDK ships a
cost ceiling and the adapter's job is to make two ceilings agree. Here there is only one, and it
is this one — so `should_stop()` is not belt-and-braces, it is the belt.

`max_llm_calls` is still worth keeping: a runaway loop that costs nothing per step is a real
failure mode, and a step ceiling catches it. The two bound different things and neither
substitutes for the other. `run_config_for()` says so by leaving it alone.

**No import of any Google package anywhere in this module** — invariant 8, and it makes the
adapter testable with nothing installed.
"""

from __future__ import annotations

from typing import Any

from .base import RunGuard

__all__ = ["GoogleADKAdapter"]


class GoogleADKAdapter:
    """Govern a Google ADK run.

        adapter = GoogleADKAdapter(Governor.load(), budget_usd="0.40")

        allowed, why = adapter.before_run(model="gemini-2.0-flash")
        if not allowed:
            raise SystemExit(why)

        # from a callback, once per step
        if adapter.should_stop(spent_so_far):
            ...                                  # end the run
        adapter.after_run(actual_cost_usd=total)

    A governor of `None` means ungoverned, and every call allows.
    """

    channel = "internal"

    #: ADK's own ceiling counts calls, not dollars. Kept as a default because an unbounded loop
    #: is a failure mode a spend ceiling alone does not catch quickly: a step that costs almost
    #: nothing can repeat for a very long time before any budget notices.
    DEFAULT_MAX_LLM_CALLS = 50

    def __init__(
        self,
        governor: Any | None = None,
        *,
        budget_usd: str | int | None = None,
        provider: str = "google",
    ) -> None:
        self.guard = RunGuard(governor, budget_usd=budget_usd)
        self.provider = provider

    # --- the run ----------------------------------------------------------

    def before_run(self, *, model: str = "") -> tuple[bool, str]:
        return self.guard.start(model=model, provider=self.provider)

    def should_stop(self, spent_usd: str | int) -> bool:
        """The spend ceiling ADK lacks. `True` means stop now.

        Call from a per-step callback. Unlike the Claude adapter this is not a second opinion —
        nothing else in an ADK run is watching money.
        """
        return self.guard.should_stop(spent_usd)

    def after_run(
        self, *, actual_cost_usd: str | int | None = None, success: bool = True
    ) -> None:
        self.guard.finish(actual_cost_usd, success=success)

    # --- ADK's own configuration ------------------------------------------

    def run_config_for(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        """ADK run config with a step ceiling present, and never loosened.

        Only fills `max_llm_calls` in if the caller named none. A step ceiling and a spend
        ceiling bound different things, so this does not derive one from the other — and it does
        not raise a caller's value, for the same reason nothing in this system widens a limit
        somebody else set.
        """
        out = dict(config or {})
        out.setdefault("max_llm_calls", self.DEFAULT_MAX_LLM_CALLS)
        return out

    def before_model_callback(self, spent_usd_getter: Any) -> Any:
        """An ADK-shaped callback that stops the run when the governed budget is reached.

        ADK calls its `before_model_callback` with a context; the amount spent so far is not in
        it, so the caller passes a zero-argument getter for that. Returning `None` means proceed,
        which is ADK's convention and is why this returns `None` rather than `True`.

        The getter is a callable rather than a number because the callback is built once and
        invoked many times — a number captured at construction would be the spend before the run
        began, i.e. always zero, and the ceiling would never trip.
        """

        def callback(*_args: Any, **_kwargs: Any) -> Any:
            if self.should_stop(spent_usd_getter()):
                return {
                    "stop": True,
                    "reason": self.guard.stopped_by.reason if self.guard.stopped_by else "",
                    "attributedControl": self.guard.stop_reason,
                }
            return None

        return callback

    def as_dict(self) -> dict[str, Any]:
        return {"framework": "google-adk", **self.guard.as_dict()}
