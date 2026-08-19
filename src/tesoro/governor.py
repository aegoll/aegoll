"""The governance surface: the object most users construct, and the only one they need.

This is the Tier 1 API specified in `docs/api-surface.md` §3, which was deliberately written
before any of it existed — "a bad early API is the only permanent mistake available in this
project". The code then grew a different shape underneath, so `from tesoro import Governor`
returned the internal rules evaluator and the README's own opening snippet raised
`AttributeError`. This module is the specified surface, implemented over `Tesoro`.

**A facade, not a second implementation.** Every call here delegates. Nothing in this file
decides anything, converts money, or writes evidence — if it did, there would be two decision
paths and only one of them would be the tested one. What it does own is *ergonomics*: reading
config from a file, accepting a vendor as a string, and refusing a `float`.

Why a facade at all, rather than telling people to use `Tesoro` directly:

* `Tesoro.authorize()` takes a `PaymentRequest` you must build first, with a `Vendor` object
  and a `Purpose` enum. Correct for a library, three imports and six lines for a first run.
* Configuration lives in `tesoro.yaml`, and `Tesoro(...)` does not read it. Every command in
  the CLI had to remember to — and for eleven of them, one helper forgot, which is how a user
  could edit a policy and have it silently ignored.

The narrower door stays open: `Tesoro` remains public and unchanged, and anything this class
cannot express is a reason to use it directly rather than a reason to widen this.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .domain import Channel, Purpose, Vendor
from .errors import ConfigError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .domain import Decision, PaymentRequest
    from .reporting import Report
    from .runtime import Tesoro
    from .settings import Config

__all__ = ["Governor"]


def _amount(value: Any) -> str:
    """A USD amount as a decimal string, or a refusal.

    `str` is a decimal amount (`"2.50"`); `int` is **atomic units**. A `float` raises, which is
    invariant 3 enforced at the boundary rather than trusted — `0.1 + 0.2` is not `0.3`, and a
    governance layer that rounds a payment silently has miscounted somebody's money. `bool` is
    excluded explicitly because `isinstance(True, int)` is true in Python and `authorize(
    amount_usd=True)` should not mean one atomic unit.
    """
    from .domain import atomic_to_usd  # noqa: PLC0415

    if isinstance(value, bool):
        raise TypeError(
            f"amount_usd must be a decimal string like '2.50' or an int of atomic units, "
            f"not {value!r}"
        )
    if isinstance(value, float):
        raise TypeError(
            f"amount_usd must not be a float ({value!r}). Pass a decimal string ('2.50') or "
            "an int of atomic units. Binary floating point cannot represent most decimal "
            "amounts exactly, and money that rounds silently is money that is miscounted."
        )
    if isinstance(value, int):
        return f"{atomic_to_usd(value):.6f}"
    if isinstance(value, str):
        return value
    raise TypeError(f"amount_usd must be a str or an int, not {type(value).__name__}")


class Governor:
    """One instance governs one agent session.

    **stable** — `docs/api-surface.md` §3. Construct with `load()`; `Governor()` takes a live
    `Tesoro` and exists for tests and for callers that already have one.

        gov = Governor.load()
        decision = gov.authorize(amount_usd="2.50", vendor="acme", resource="/data")
        if decision.approved:
            pay(...)
            gov.settle(decision, success=True)

    `authorize()` then `settle()` is always two calls. A decision is made before money moves; a
    settlement records what actually happened. Envelopes consume on settle, not on authorize, so
    an abandoned decision does not eat budget — and a layer that could not tell those apart
    would report spending that never occurred.
    """

    def __init__(self, layer: "Tesoro", *, config: "Config | None" = None) -> None:
        self._layer = layer
        self._config = config
        #: Requests by decision, so `settle()` can take a `Decision` rather than an id. A
        #: settlement for a decision that was never made is then unrepresentable instead of
        #: merely discouraged.
        self._requests: dict[str, "PaymentRequest"] = {}

    # --- construction -----------------------------------------------------

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Governor":
        """Read `tesoro.yaml` and build the layer it describes.

        The default constructor, because configuration belongs in a file rather than in a call
        with fourteen keyword arguments. A missing config is not an error: `pip install tesoro`
        followed by `Governor.load()` works, with the packaged starter pack and defaults, so the
        first thing a new user tries is not a stack trace.
        """
        from .settings import Config  # noqa: PLC0415

        try:
            config = Config.load(path)
        except ConfigError:
            if path is not None:
                raise  # an explicitly named config that cannot be read is a real error
            config = None

        return cls.from_config(config) if config is not None else cls(cls._bare_layer())

    @classmethod
    def from_config(cls, config: "Config") -> "Governor":
        """Build from an already-loaded `Config`. For tests, and for config built in code."""
        from .runtime import Tesoro, Paths  # noqa: PLC0415

        paths = (
            Paths.for_journal(config.evidence_path)
            if config.evidence_path
            else Paths.under()
        )
        return cls(Tesoro(bundle=config.policy(), paths=paths), config=config)

    @staticmethod
    def _bare_layer() -> "Tesoro":
        from .runtime import Tesoro  # noqa: PLC0415

        return Tesoro()

    # --- the decision path ------------------------------------------------

    def authorize(
        self,
        *,
        amount_usd: str | int,
        vendor: str,
        resource: str,
        channel: str = "external",
        purpose: str | None = None,
        sanctioned: bool = False,
        now: datetime | None = None,
    ) -> "Decision":
        """Decide whether this spend may happen, and journal the decision.

        Keyword-only throughout. `authorize(2.50, "acme")` is the kind of call whose arguments
        eventually get swapped, and here that means paying the wrong party.

        `vendor` is a string because that is what a caller has. Pass a `Vendor` to
        `Tesoro.authorize()` directly when you need to carry more about a counterparty than its
        id — `sanctioned` is exposed here only because a screening result must be able to reach
        the layer, and a caller with no screening at all should not have to construct anything
        to say so.
        """
        request = self._request(
            amount_usd=amount_usd, vendor=vendor, resource=resource,
            channel=channel, purpose=purpose, sanctioned=sanctioned,
        )
        decision = self._layer.authorize(request, now=now)
        self._requests[decision.request_id] = request
        return decision

    def decide(
        self,
        *,
        amount_usd: str | int,
        vendor: str,
        resource: str,
        channel: str = "external",
        purpose: str | None = None,
        sanctioned: bool = False,
        now: datetime | None = None,
    ) -> "Decision":
        """What *would* be decided, without journalling it or consuming anything.

        For a dry run or a preflight check. Deliberately a different method rather than an
        `authorize(dry_run=True)` flag: a boolean that determines whether evidence is written is
        too easy to pass through from a variable and never notice.
        """
        return self._layer.decide(
            self._request(
                amount_usd=amount_usd, vendor=vendor, resource=resource,
                channel=channel, purpose=purpose, sanctioned=sanctioned,
            ),
            now=now,
        )

    def settle(
        self,
        decision: "Decision",
        *,
        success: bool = True,
        actual_amount_usd: str | int | None = None,
    ) -> None:
        """Record what actually happened. Envelopes consume here, not at `authorize()`.

        Takes the `Decision` rather than an id, so a settlement for a decision this governor
        never made cannot be expressed. `actual_amount_usd` exists because the amount paid is
        not always the amount authorized, and an envelope updated with the quoted figure would
        be quietly wrong in whichever direction the difference fell.
        """
        from .domain import usd_to_atomic  # noqa: PLC0415

        if decision.request_id not in self._requests:
            raise ValueError(
                f"decision {decision.request_id} was not made by this governor, so there is "
                "nothing to settle. A settlement records what happened to a decision this "
                "layer authorized; anything else would be evidence of an event it never saw."
            )

        amount_atomic = (
            usd_to_atomic(_amount(actual_amount_usd))
            if actual_amount_usd is not None
            else None
        )
        self._layer.record_settlement(
            decision.request_id, success=success, amount_atomic=amount_atomic
        )

    # --- declarations, before acting --------------------------------------

    def declare_intent(
        self,
        *,
        purpose: str,
        budget_usd: str | int,
        expires_in_s: int,
        resources: list[str] | None = None,
        agent_id: str | None = None,
    ) -> str:
        """Declare what this agent was sent to do, and what it may spend doing it.

        Returns the intent id. `expires_in_s` rather than an absolute time because the caller
        knows the duration and the layer owns the clock — and a decision path that reads the
        wall clock independently is not replayable (AEGS-0.1-PATH-4).
        """
        intent = self._layer.intents.declare(
            agent_id=agent_id or self._layer.agent_id,
            purpose=purpose,
            maximum_usd=_amount(budget_usd),
            allowed_resources=tuple(resources or ()),
            expires_at=self._now() + timedelta(seconds=expires_in_s),
            now=self._now(),
        )
        return intent.intent_id

    def revoke_intent(self, intent_id: str) -> bool:
        """Revoke a declared intent. `False` if there was no such live intent."""
        return self._layer.intents.revoke(intent_id)

    def register_identity(
        self,
        *,
        agent_id: str,
        controller: str,
        parent_id: str | None = None,
        purpose: str = "unstated",
        per_action_usd: str | int | None = None,
        daily_usd: str | int | None = None,
    ) -> None:
        """Register who this agent is and who is accountable for it.

        `controller` is a string here and a `Party` underneath. A delegate's limits are
        **clamped** to its parent's rather than compared against them: declaring no limit
        inherits the parent's, which is AEGS-0.1-ID-4 and was a live escalation when it was
        merely compared.
        """
        from .engines.evidence.identity import Party  # noqa: PLC0415

        self._layer.identities.register(
            agent_id=agent_id,
            purpose=purpose,
            parent_agent_id=parent_id,
            controller=Party(id=controller, kind="organisation"),
            per_action_usd=_amount(per_action_usd) if per_action_usd is not None else None,
            daily_usd=_amount(daily_usd) if daily_usd is not None else None,
            now=self._now(),
        )

    # --- framework integration --------------------------------------------

    def wrap(self, agent: Any, *, vendor: str | None = None) -> Any:
        """Govern a payment client, returning something with the same shape.

        The returned object is the only thing holding the signer, so the agent cannot pay
        without a decision — that is the point, and it is why `wrap()` returns a new object
        rather than mutating the one it was given.

        The dependency arrow points from the agent to this layer and never back: nothing here
        imports a framework, and the wrapped client is not required to subclass or register
        anything. Invariant 8, and what makes this installable rather than integrated —
        `tests/test_deps.py` fails if the core ever imports a framework.

        A client that cannot pay is refused **by name**. `isinstance` against a runtime
        protocol only checks attribute presence and returns a bare `False`, which tells an
        integrator nothing about what to add.
        """
        from .adapters.x402_python import GovernedBuyer  # noqa: PLC0415
        from .plugin import conforms  # noqa: PLC0415

        ok, missing = conforms(agent)
        if not ok:
            raise TypeError(
                "this object cannot be governed as a payment client; it is missing: "
                + ", ".join(missing)
                + ". Nothing needs to be subclassed -- the members just have to be there."
            )

        return GovernedBuyer(
            self._layer,
            agent,
            vendor=Vendor(id=vendor, name=vendor) if vendor else None,
        )

    # --- reading state ----------------------------------------------------

    def report(self, *, limit: int = 20) -> "Report":
        """Everything a reader needs, as plain data. Render with `tesoro.html.render()`."""
        from . import reporting  # noqa: PLC0415

        return reporting.build(
            self._layer,
            profile=self._config.profile if self._config else None,
            limit=limit,
        )

    def budget(self, channel: str = "external") -> Any:
        """The envelope state for one channel.

        The two channels never share an envelope, so this takes one and returns one. An
        exhausted internal token budget and an exhausted external payout budget are different
        facts with different remedies.
        """
        views = self.report().envelopes.get(_channel(channel).value, ())
        return views

    def decisions(self, limit: int | None = None) -> list[Any]:
        """Recent decisions, newest first, read from the hash-chained journal.

        The journal rather than the sqlite history, because the journal is the verifiable
        artifact: a number from the record that can be checked is worth more than the same
        number from a convenience table, and if the two ever disagree the journal is right.
        """
        views = list(self.report(limit=limit or 20).decisions)
        return views if limit is None else views[:limit]

    def verify(self) -> Any:
        """Re-verify the evidence chain.

        Detects editing, middle-deletion and reordering. Does **not** detect truncation of the
        tail — any prefix of a valid chain is itself valid, so an agent that was refused could
        delete the refusal and this would still report success. Closing that needs an external
        anchor; `Report.chain.caveat` carries the disclosure that AEGS-0.1-EVID-6 requires.

        Unchanged by the arrival of `verify_anchored`, deliberately. A caller that never
        configured an anchor must not silently begin receiving a different claim than the one it
        asked for.
        """
        return self._layer.audit.verify()

    def verify_anchored(self, anchor: Any) -> Any:
        """Compare the journal against what an external anchor attests. AEGS-0.1-EVID-6a.

        Returns an `AnchorResult` with one of four outcomes — `consistent`, `truncated`,
        `diverged`, `unknown` — and **`unknown` is never a pass**. A sink that cannot be read
        leaves the anchored claim unavailable, which is a third thing; reporting it as consistent
        would mean anyone able to partition this process from the sink could also make a
        truncated journal verify.

        Separate from `verify()` because the two answer different questions and one can hold
        while the other fails: a chain can be internally valid and shorter than what was
        attested, which is precisely the case a hash chain cannot see.

        **What this does not close.** Everything appended since the anchor's last publication is
        unattested and remains truncatable. Anchoring makes truncation detectable *beyond a
        bound you chose*, not detectable. `anchor` is duck-typed — see
        `tesoro.engines.evidence.anchor.Anchor`; nothing ships as a default, because an
        append-only file beside the journal is within the writer's authority in most deployments
        and would make the gap look closed.
        """
        from .engines.evidence.anchor import verify_against_anchor  # noqa: PLC0415

        return verify_against_anchor(self._layer.audit.entries(), anchor)

    # --- lifecycle --------------------------------------------------------

    def close(self) -> None:
        self._layer.close()

    def __enter__(self) -> "Governor":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # --- internals --------------------------------------------------------

    def _now(self) -> datetime:
        """The layer's clock, never `datetime.now()` directly.

        A `FixedClock` in a test must move this too, and a facade that read the wall clock would
        make its own calls unrepeatable while the layer's stayed deterministic.
        """
        now = self._layer.clock.now()
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)

    def _request(
        self,
        *,
        amount_usd: str | int,
        vendor: str,
        resource: str,
        channel: str,
        purpose: str | None,
        sanctioned: bool,
    ) -> "PaymentRequest":
        return self._layer.build_request(
            resource=resource,
            amount_usd=_amount(amount_usd),
            vendor=Vendor(id=vendor, name=vendor, sanctioned=sanctioned),
            purpose=_purpose(purpose),
            channel=_channel(channel),
        )


def _channel(name: str) -> Channel:
    """A channel name as the enum, with a message that lists the two.

    A plain string in the signature buys the caller one less import; validating it here is what
    keeps that from becoming a typo that silently governs the wrong budget.
    """
    try:
        return Channel(name)
    except ValueError:
        raise ValueError(
            f"unknown channel {name!r}. The two are 'internal' (tokens the agent burns "
            "thinking) and 'external' (what it pays out). They never share an envelope."
        ) from None


def _purpose(name: str | None) -> Purpose:
    if name is None:
        return Purpose.DATA_PURCHASE
    try:
        return Purpose(name)
    except ValueError:
        raise ValueError(
            f"unknown purpose {name!r}. Known: {', '.join(p.value for p in Purpose)}"
        ) from None
