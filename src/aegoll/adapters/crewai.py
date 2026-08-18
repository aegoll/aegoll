"""CrewAI adapter. `pip install aegoll[crewai]`.

**Read this before relying on it.** This is the one adapter in the package whose framework
surface is *unverified*. The other three were built against SDKs that ran end to end in the
proof-of-concept; CrewAI has no such precedent here and was not installed when this was
written. So:

* the **governance** behaviour is tested exactly as thoroughly as the others -- the three calls,
  the tightening rules, channel separation, consumption on finish, refusal before start;
* the **CrewAI hook names** (`step_callback`, `task_callback`, `max_iter`, `max_rpm`) come from
  CrewAI's documentation rather than from a run, and a version that renames one will break this
  adapter without breaking a single test.

That asymmetry is stated here rather than left for a user to discover, and `docs/adapters.md`
repeats it. If the names are wrong, `crew_kwargs()` produces a dict CrewAI rejects immediately,
which is a loud failure -- the shape of wrongness worth preferring.

The governance situation is the same as ADK and LangGraph: CrewAI's `max_iter` and `max_rpm`
bound **steps and rate**, not money. `max_rpm` is worth noting precisely because it looks like a
cost control and is not -- pacing at a rate limit is unbounded in total, which is the velocity
evasion AEGS-0.1-SEC-6 records as open. Sixty requests a minute, held all day, is eighty-six
thousand requests.

**No import of `crewai` anywhere in this module** -- invariant 8.
"""

from __future__ import annotations

from typing import Any

from .base import RunGuard

__all__ = ["CrewAIAdapter"]


class CrewAIAdapter:
    """Govern a CrewAI crew.

        adapter = CrewAIAdapter(Governor.load(), budget_usd="0.40")

        allowed, why = adapter.before_run(model="gpt-4o-mini")
        if not allowed:
            raise SystemExit(why)

        crew = Crew(agents=[...], tasks=[...], **adapter.crew_kwargs(spent_getter))
        result = crew.kickoff()
        adapter.after_run(actual_cost_usd=total)

    A governor of `None` means ungoverned, and every call allows.
    """

    channel = "internal"

    #: CrewAI's own per-agent iteration ceiling. A step bound, not a spend bound.
    DEFAULT_MAX_ITER = 15

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
        return self.guard.start(model=model, provider=self.provider)

    def should_stop(self, spent_usd: str | int) -> bool:
        """The spend ceiling CrewAI lacks. `True` means stop now."""
        return self.guard.should_stop(spent_usd)

    def after_run(
        self, *, actual_cost_usd: str | int | None = None, success: bool = True
    ) -> None:
        self.guard.finish(actual_cost_usd, success=success)

    # --- CrewAI's own configuration ---------------------------------------

    def crew_kwargs(
        self, spent_usd_getter: Any, crew_kwargs: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Kwargs for `Crew(...)`, with a governed step callback and an iteration ceiling.

        Never loosens what the caller set: `max_iter` is filled in only if absent, and an
        existing `step_callback` is **chained** rather than replaced -- silently dropping a
        caller's callback would remove their telemetry to install ours.
        """
        out = dict(crew_kwargs or {})
        out.setdefault("max_iter", self.DEFAULT_MAX_ITER)

        existing = out.get("step_callback")
        governed = self.step_callback(spent_usd_getter)

        if existing is None:
            out["step_callback"] = governed
        else:
            def chained(*args: Any, **kwargs: Any) -> Any:
                # Governance first: if it stops the run, the caller's callback should not be
                # asked to observe a step that is not going to happen.
                governed(*args, **kwargs)
                return existing(*args, **kwargs)

            out["step_callback"] = chained
        return out

    def step_callback(self, spent_usd_getter: Any) -> Any:
        """A per-step callback that raises when the governed budget is reached.

        CrewAI's step callback has no documented "stop here" return value, so this raises. The
        exception is a governance decision surfacing as control flow, and it carries the
        attributed control -- "the run stopped" is not actionable, "the daily envelope stopped
        it" is.

        A callable getter rather than a number, because the callback is built once and invoked
        many times: a number captured here would be the spend before the crew started, i.e.
        always zero, and the ceiling would never trip.
        """
        def callback(*_args: Any, **_kwargs: Any) -> None:
            if self.should_stop(spent_usd_getter()):
                stopped = self.guard.stopped_by
                raise GovernedBudgetExceeded(
                    f"governance stopped this crew: "
                    f"{stopped.reason if stopped else 'budget reached'} "
                    f"[{self.guard.stop_reason}]",
                    attributed_control=self.guard.stop_reason,
                )

        return callback

    def as_dict(self) -> dict[str, Any]:
        return {"framework": "crewai", **self.guard.as_dict()}


class GovernedBudgetExceeded(RuntimeError):
    """Raised when a governed crew reaches its ceiling mid-run.

    A `RuntimeError` so a caller catching broadly still catches it, carrying the attributed
    control so the message names which limit was reached.
    """

    def __init__(self, message: str, *, attributed_control: str = "") -> None:
        super().__init__(message)
        self.attributed_control = attributed_control
