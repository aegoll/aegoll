"""The universal surface: govern any agent, on any framework, in four calls.

```python
from tesoro.plugin import Governor

gov = Governor(policy="default")

auth = gov.authorize_run(model="gpt-4o-mini", provider="openai", budget_usd=0.04)
if not auth.allowed:
    ...                                    # AEGL refused the run's token budget

buyer = gov.wrap(buyer)                    # external channel: USDC over x402
...                                        # run the agent
if gov.check_spend(cost_so_far).should_stop:
    break                                  # internal channel: mid-run ceiling
gov.settle_run(actual_cost_usd)
```

Nothing here imports a framework, an LLM SDK, or an agent package. `wrap()` takes
anything satisfying `PaymentClient` -- no subclassing, no registration. That is
what makes AEGL installable rather than integrated: the dependency arrow points
from the agent to the governance layer, never back, and
`agents/tests/test_decoupling.py` fails if it ever reverses.

## Why `check_spend()` exists

Of the three frameworks this was built against, **only the Claude Agent SDK ships
a cost ceiling** (`max_budget_usd`). LangGraph has `recursion_limit`; Google ADK
has `max_llm_calls`. Both are *step* ceilings, and a step ceiling is not a spend
ceiling -- one long-context call can cost more than fifty short ones.

So `check_spend()` gives those frameworks something they do not have. The agent's
own loop calls it; AEGL answers against the budget it authorized and journals the
stop. It is the clearest demonstration that the layer adds capability rather than
duplicating a built-in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .adapters.x402_python import GovernanceRefused, GovernedBuyer
from .advisors import build_advisor
from .config import PolicyBundle, available_bundles, load_bundle
from .domain import Channel, Purpose, Vendor
from .inference import InferenceGate, RunAuthorization
from .runtime import Tesoro, Paths

# --- defaults ------------------------------------------------------------
#
# These are measured, not assumed. See `EVAL.md`: across 14 labelled cases,
# `gemini-flash-lite-latest` blocked 0/8 legitimate purchases at $0.00012 a call,
# while models costing 5x and 14x more blocked 50% and 62%. Price does not
# predict advice quality, so the default is the model that scored best -- which
# happened also to be the cheapest per call.

RECOMMENDED_ADVISOR: tuple[str, str] = ("gemini", "gemini-flash-lite-latest")

#: Models measured as unusable for advice. Selectable -- an operator may know
#: something this measurement did not -- but never chosen automatically, and the
#: UI is expected to surface the warning.
NOT_RECOMMENDED: dict[str, str] = {
    "llama-3.1-8b-instant": (
        "Recommended REVIEW or stricter on all 14 evaluation cases, including a "
        "$0.001 repeat purchase from a vendor with 12 clean settlements. An "
        "advisor that never returns 'fine' carries no information."
    ),
}

#: LLM providers are counterparties like any other. Keeping them distinct means
#: per-vendor envelopes become per-provider spending limits at no extra cost.
PROVIDER_VENDORS: dict[str, Vendor] = {
    "anthropic": Vendor(id="anthropic-api", name="Anthropic API", tags=("llm", "internal")),
    "openai": Vendor(id="openai-api", name="OpenAI API", tags=("llm", "internal")),
    "google": Vendor(id="google-api", name="Google AI API", tags=("llm", "internal")),
    "gemini": Vendor(id="google-api", name="Google AI API", tags=("llm", "internal")),
    "groq": Vendor(id="groq-api", name="Groq API", tags=("llm", "internal")),
}

DEFAULT_SELLER = Vendor(id="x402-poc-desk", name="x402 POC Data Desk")


def provider_vendor(provider: str) -> Vendor:
    """The counterparty for an LLM provider, inventing one if unknown.

    An unrecognised provider gets its own vendor rather than being folded into a
    default -- merging two counterparties into one would corrupt the trust and
    velocity history of both.
    """
    key = (provider or "").strip().lower()
    if key in PROVIDER_VENDORS:
        return PROVIDER_VENDORS[key]
    return Vendor(id=f"{key or 'unknown'}-api", name=f"{provider} API",
                  tags=("llm", "internal"))


# --- what AEGL needs of a payment client ---------------------------------


@runtime_checkable
class Quote(Protocol):
    """What `quote()` must return: at minimum, a price.

    Called out separately because it is the one part of the contract that
    `hasattr` cannot check -- a buyer can have a `quote()` method and still be
    unusable if what it returns has no price on it. AEGL reads `price_usd` to
    build the payment request, so a quote without one cannot be governed.
    """

    @property
    def price_usd(self) -> Any: ...


@runtime_checkable
class PaymentClient(Protocol):
    """The nine members `wrap()` needs. Any x402 buyer satisfying these works.

    Deliberately structural: requiring a base class would make AEGL a dependency
    of every buyer, which is the coupling this whole design avoids. A conformance
    test lives in `tests/test_plugin.py`.

    Two members carry requirements beyond their signature, because AEGL reads
    fields off what they return:

    * `quote()` returns something with **`price_usd`** — the amount governed.
    * `get_paid()` returns something whose `payment_status` and `transaction`
      are read when recording settlement. Both are optional: absent, the call is
      journalled as unsettled rather than failing.
    """

    @property
    def address(self) -> str: ...
    @property
    def spend_cap_usd(self) -> Any: ...
    @property
    def total_spent_usd(self) -> Any: ...
    @property
    def calls(self) -> list[Any]: ...

    async def quote(self, path: str) -> Quote: ...
    async def get_free(self, path: str) -> Any: ...
    async def get_paid(self, path: str) -> Any: ...
    def budget_snapshot(self) -> dict[str, Any]: ...
    async def aclose(self) -> None: ...


def conforms(client: Any) -> tuple[bool, list[str]]:
    """Check a buyer against `PaymentClient`, naming what is missing.

    `isinstance` against a runtime protocol only checks attribute presence and
    returns a bare False, which is useless when integrating a new buyer. This
    says which members are absent.
    """
    required = [
        "address", "spend_cap_usd", "total_spent_usd", "calls",
        "quote", "get_free", "get_paid", "budget_snapshot", "aclose",
    ]
    missing = [m for m in required if not hasattr(client, m)]
    return (not missing), missing


# --- events ---------------------------------------------------------------


@dataclass
class GovernanceEvent:
    """One AEGL decision, flattened for any UI to render."""

    channel: str
    verdict: str
    resource: str
    amount_usd: float
    matched_rule: str | None
    engine: str
    trust: float
    risk: float
    roi_ratio: float | None
    budget_ok: bool
    binding: str | None
    would_use_ai: bool
    latency_us: float
    reasons: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    envelopes: list[dict[str, Any]] = field(default_factory=list)
    trust_terms: list[dict[str, Any]] = field(default_factory=list)
    risk_terms: list[dict[str, Any]] = field(default_factory=list)
    eiap: dict[str, Any] = field(default_factory=dict)
    # Phase 2: present only when the EIAP gate opened and an advisor ran.
    advice: dict[str, Any] | None = None
    advisor_changed: bool = False
    advisor_skip_reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.verdict == "APPROVE"

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel, "verdict": self.verdict, "resource": self.resource,
            "amountUsd": self.amount_usd, "matchedRule": self.matched_rule,
            "engine": self.engine, "trust": self.trust, "risk": self.risk,
            "roiRatio": self.roi_ratio, "budgetOk": self.budget_ok,
            "binding": self.binding, "wouldUseAi": self.would_use_ai,
            "latencyUs": self.latency_us, "reasons": self.reasons,
            "riskFlags": self.risk_flags, "envelopes": self.envelopes,
            "trustTerms": self.trust_terms, "riskTerms": self.risk_terms,
            "eiap": self.eiap, "advice": self.advice,
            "advisorChanged": self.advisor_changed,
            "advisorSkipReason": self.advisor_skip_reason,
        }


def deciding_engine(decision: Any) -> str:
    """Which engine actually determined the verdict.

    Read from the back: an explicit clamp in `authorize` is more informative than
    the policy rule that preceded it, and a treasury refusal is more specific than
    the generic rule that reported it.
    """
    for reason in reversed(decision.reasons):
        if reason.source == "authorize" and reason.verdict:
            return "authorize"
    if not decision.budget.ok:
        return "treasury"
    if "high_risk" in decision.risk.flags:
        return "risk"
    return "policy"


def to_event(decision: Any, channel: str, resource: str) -> GovernanceEvent:
    b = decision.budget
    return GovernanceEvent(
        channel=channel,
        verdict=decision.verdict.value,
        resource=resource,
        # The EIAP's exposure *is* the request amount, and it is the only place the
        # decision carries it -- `Decision` is keyed by request id, not by request.
        amount_usd=float(decision.intelligence.eiap.exposure_atomic) / 1e6,
        matched_rule=decision.matched_rule,
        engine=deciding_engine(decision),
        trust=decision.trust.value,
        risk=decision.risk.value,
        roi_ratio=decision.roi.ratio,
        budget_ok=b.ok,
        binding=b.binding,
        would_use_ai=decision.intelligence.eiap.would_invoke,
        latency_us=decision.latency_us,
        reasons=decision.explain(),
        risk_flags=list(decision.risk.flags),
        envelopes=[e.as_dict() for e in b.envelopes],
        trust_terms=[t.as_dict() for t in decision.trust.terms],
        risk_terms=[t.as_dict() for t in decision.risk.terms],
        eiap=decision.intelligence.eiap.as_dict(),
    )


def event_from_payload(
    decision: dict[str, Any], channel: str, resource: str, payload: dict[str, Any]
) -> GovernanceEvent:
    """Rebuild an event from an audit entry.

    Approving `get_paid` returns the response, not the decision -- so the decision
    is recovered from the journal it was just written to. Reading it back rather
    than threading it out also proves the journal holds enough to reconstruct what
    happened, which is the whole point of keeping one.
    """
    budget = decision.get("budget") or {}
    risk = decision.get("risk") or {}
    trust = decision.get("trust") or {}
    eiap = (decision.get("intelligence") or {}).get("eiap") or {}
    tx = payload.get("transaction") or {}
    reasons = [
        f"[{r.get('source')}/{r.get('code')}] {r.get('detail')}"
        for r in (decision.get("reasons") or [])
    ]
    engine = "policy"
    if not budget.get("ok", True):
        engine = "treasury"
    elif "high_risk" in (risk.get("flags") or []):
        engine = "risk"
    for r in reversed(decision.get("reasons") or []):
        if r.get("source") == "authorize" and r.get("verdict"):
            engine = "authorize"
            break
    settlement = payload.get("settlement") or {}
    if settlement.get("changed"):
        engine = "advisor"
    return GovernanceEvent(
        channel=channel,
        verdict=settlement.get("finalVerdict") or decision.get("verdict", "?"),
        resource=resource or tx.get("resource", ""),
        amount_usd=float(tx.get("amountUsd", eiap.get("exposureUsd", 0.0))),
        matched_rule=decision.get("matchedRule"),
        engine=engine,
        trust=float(trust.get("value", 0.0)),
        risk=float(risk.get("value", 0.0)),
        roi_ratio=(decision.get("roi") or {}).get("ratio"),
        budget_ok=bool(budget.get("ok", True)),
        binding=budget.get("binding"),
        would_use_ai=bool(eiap.get("wouldInvoke", False)),
        latency_us=float(decision.get("latencyUs", 0.0)),
        reasons=reasons,
        risk_flags=list(risk.get("flags") or []),
        envelopes=list(budget.get("envelopes") or []),
        trust_terms=list(trust.get("terms") or []),
        risk_terms=list(risk.get("terms") or []),
        eiap=eiap,
        advice=settlement.get("advice"),
        advisor_changed=bool(settlement.get("changed")),
        advisor_skip_reason=str(settlement.get("skipReason") or ""),
    )


# --- the mid-run ceiling --------------------------------------------------


@dataclass(frozen=True)
class SpendCheck:
    """The answer to "may the agent keep going?" -- the capability AEGL adds."""

    should_stop: bool
    reason: str
    spent_usd: float
    budget_usd: float
    warn: bool = False

    @property
    def headroom_usd(self) -> float:
        return max(0.0, self.budget_usd - self.spent_usd)

    @property
    def fraction_used(self) -> float:
        return (self.spent_usd / self.budget_usd) if self.budget_usd > 0 else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "shouldStop": self.should_stop, "reason": self.reason,
            "spentUsd": round(self.spent_usd, 6), "budgetUsd": round(self.budget_usd, 6),
            "headroomUsd": round(self.headroom_usd, 6),
            "fractionUsed": round(self.fraction_used, 4), "warn": self.warn,
        }


# --- the governor ---------------------------------------------------------


class Governor:
    """One governance layer for one agent session, on any framework."""

    def __init__(
        self,
        policy: str | Path | None = None,
        *,
        agent_id: str = "agent-1",
        advisor: tuple[str, str] | str | None = "auto",
        seller: Vendor = DEFAULT_SELLER,
        data_dir: Path | str | None = None,
        warn_at: float = 0.8,
        framework: str | None = None,
        labels: dict[str, str] | None = None,
    ) -> None:
        """
        `advisor="auto"` uses `RECOMMENDED_ADVISOR` when a key for it is present
        and stays deterministic otherwise -- so installing the plugin never
        depends on a key being set, and never silently *needs* one. Pass `None`
        for deterministic-only, or an explicit `(provider, model)` pair.

        A missing or invalid key degrades to deterministic rather than raising:
        losing the second opinion should cost the agent its advisor, not its
        ability to transact. The reason is on `advisor_error`.

        `framework` stamps every journalled decision with which host produced it,
        which is what makes a cross-framework view possible. It is recorded as an
        audit *label*, not as `agent_id`: every treasury envelope is agent-scoped,
        so labelling frameworks through `agent_id` would quietly split one shared
        budget into one per framework. Use `agent_id` when that *is* what you
        want.
        """
        self.bundle: PolicyBundle = _resolve_bundle(policy)
        self.seller = seller
        self.warn_at = warn_at

        self.advisor_spec: tuple[str, str] | None = None
        self.advisor_error: str | None = None
        self.advisor_warning: str | None = None
        advisor_obj = self._build_advisor(advisor)

        self.labels = {k: str(v) for k, v in (labels or {}).items() if v}
        if framework:
            self.labels["framework"] = framework
        self.tesoro = Tesoro(
            bundle=self.bundle,
            paths=Paths.under(data_dir) if data_dir else Paths.under(),
            agent_id=agent_id,
            advisor=advisor_obj,
            labels=self.labels,
        )
        self.inference = InferenceGate(self.tesoro)
        self.events: list[GovernanceEvent] = []

        self._run_request_id: str | None = None
        self._run_budget_usd: float = 0.0
        self._run_model: str = ""
        self._stopped: SpendCheck | None = None
        # model -> the request id `precheck_run` allocated for it. `authorize_run`
        # reuses it so the previewed decision and the real one are the same
        # decision, which is what lets a human override be attributed to it. Keyed
        # by model rather than order, because the cockpit records an override
        # before the run starts and a test may do it after.
        self._precheck_ids: dict[str, str] = {}

    # --- advisor selection ------------------------------------------------
    def _build_advisor(self, advisor: tuple[str, str] | str | None) -> Any | None:
        if advisor is None:
            return None
        if advisor == "auto":
            spec, required = RECOMMENDED_ADVISOR, False
        elif isinstance(advisor, tuple):
            spec, required = advisor, True
        else:
            raise TypeError("advisor must be None, 'auto', or a (provider, model) pair")

        try:
            candidate = build_advisor(*spec)
            ok, detail = candidate.available()
        except Exception as exc:  # noqa: BLE001 - any provider error degrades the same way
            ok, detail, candidate = False, f"{type(exc).__name__}: {exc}", None

        if not ok:
            # In "auto" this is the ordinary no-key path, not an error worth
            # reporting as one.
            self.advisor_error = detail if required else None
            return None

        self.advisor_spec = spec
        if spec[1] in NOT_RECOMMENDED:
            self.advisor_warning = NOT_RECOMMENDED[spec[1]]
        return candidate

    # --- internal channel: tokens -----------------------------------------
    def authorize_run(
        self,
        *,
        model: str,
        budget_usd: float,
        provider: str = "anthropic",
        expected_value_usd: float | None = None,
    ) -> RunAuthorization:
        """Ask AEGL whether the agent may commit `budget_usd` of tokens.

        A non-APPROVE verdict means do not start the run. `provider` decides the
        counterparty, so per-vendor envelopes act as per-provider limits.
        """
        self.inference.vendor = provider_vendor(provider)
        # Reuse the id the pre-check allocated for this model, so the journalled
        # refusal and any override that bypassed it refer to one decision.
        pending = self._precheck_ids.pop(model, None)
        # Stamp the provider too, so the cross-framework view can separate
        # "LangGraph on Gemini" from "ADK on Gemini".
        self.tesoro.audit.labels.setdefault("provider", provider)
        self.labels.setdefault("provider", provider)
        auth = self.inference.authorize_run(
            model=model, budget_usd=budget_usd,
            expected_value_usd=expected_value_usd, request_id=pending,
        )
        event = to_event(auth.decision, Channel.INTERNAL.value, f"llm:{model}")
        event.amount_usd = auth.budget_usd
        self.events.append(event)

        self._run_model = model
        self._stopped = None
        if auth.allowed:
            self._run_request_id = auth.request_id
            self._run_budget_usd = auth.budget_usd
        else:
            self._run_request_id = None
            self._run_budget_usd = 0.0
        return auth

    # --- agent identity (KYA) ----------------------------------------------
    def register_identity(
        self,
        *,
        purpose: str,
        agent_id: str | None = None,
        parent_agent_id: str | None = None,
        controller: Any | None = None,
        operator: Any | None = None,
        wallets: Any = (),
        authorized_networks: Any = (),
        per_action_usd: str | float | None = None,
        daily_usd: str | float | None = None,
        credentials: Any = (),
        risk_profile: str | None = None,
    ) -> Any:
        """Register who this agent is and under whose authority it acts.

        Optional, like intent. Pseudonymous by default: `controller` and
        `operator` are never disclosed to a counterparty, and the declared spending
        limits are applied as an *additional* clamp on top of policy -- an identity
        cannot grant itself a higher ceiling than the policy allows.
        """
        return self.tesoro.identities.register(
            agent_id=agent_id or self.tesoro.agent_id,
            purpose=purpose,
            parent_agent_id=parent_agent_id,
            controller=controller,
            operator=operator,
            wallets=wallets,
            authorized_networks=authorized_networks,
            per_action_usd=per_action_usd,
            daily_usd=daily_usd,
            credentials=credentials,
            risk_profile=risk_profile,
            policy_version=self.bundle.hash,
            now=self.tesoro.clock.now(),
        )

    def identity(self, agent_id: str | None = None) -> Any | None:
        return self.tesoro.identities.get(agent_id or self.tesoro.agent_id)

    def set_identity_status(self, status: str, agent_id: str | None = None) -> bool:
        """Suspend, revoke or reactivate. Revocation is terminal."""
        return self.tesoro.identities.set_status(agent_id or self.tesoro.agent_id, status)

    # --- economic intent ---------------------------------------------------
    def declare_intent(
        self,
        *,
        purpose: str,
        maximum_usd: str | float,
        asset: str = "USDC",
        maximum_per_action_usd: str | float | None = None,
        allowed_resources: tuple[str, ...] | list[str] = (),
        allowed_categories: tuple[str, ...] | list[str] = (),
        allowed_channels: tuple[str, ...] | list[str] = (),
        expected_outcome: str | None = None,
        expires_at: Any | None = None,
        declared_by: str | None = None,
    ) -> Any:
        """Declare what this agent is being sent out to do, before it acts.

        Optional. An agent with no declared intent is governed exactly as before
        and its records say `intentId: null` -- absence is recorded, never implied
        to be a passed check.

        `maximum_usd` is the total across the intent's life, not per action. A
        ceiling that permits unlimited repetitions is not a budget, and splitting
        one large spend into many small ones is a named threat.
        """
        return self.tesoro.intents.declare(
            agent_id=self.tesoro.agent_id,
            purpose=purpose,
            maximum_usd=maximum_usd,
            asset=asset,
            maximum_per_action_usd=maximum_per_action_usd,
            allowed_resources=allowed_resources,
            allowed_categories=allowed_categories,
            allowed_channels=allowed_channels,
            expected_outcome=expected_outcome,
            expires_at=expires_at,
            declared_by=declared_by,
            now=self.tesoro.clock.now(),
        )

    def active_intent(self) -> Any | None:
        return self.tesoro.intents.active_for(self.tesoro.agent_id, self.tesoro.clock.now())

    def revoke_intent(self, intent_id: str) -> bool:
        """Withdraw authority without deleting the record of it having been granted."""
        return self.tesoro.intents.revoke(intent_id)

    def precheck_run(
        self, *, model: str, budget_usd: float, provider: str = "anthropic"
    ) -> dict[str, Any]:
        """Preview the internal-channel verdict **without journalling it**.

        A UI needs to know a run would be refused *before* it starts, so it can put
        the decision in front of a human. Using `decide()` rather than `authorize()`
        keeps that preview out of the audit trail: someone who reads the warning and
        walks away leaves no phantom refusal behind, and the log records attempts
        rather than hypotheticals.
        """
        request = self.tesoro.build_request(
            resource=f"llm:{model}",
            amount_usd=budget_usd,
            vendor=provider_vendor(provider),
            purpose=Purpose.INFERENCE,
            channel=Channel.INTERNAL,
        )
        decision = self.tesoro.decide(request)
        self._precheck_ids[model] = request.id
        return {
            # The id the real attempt will reuse, so an override can be attributed
            # to the exact decision it bypassed rather than to a synthetic key.
            "request_id": request.id,
            "allowed": decision.approved,
            "verdict": decision.verdict.value,
            "engine": deciding_engine(decision),
            "matched_rule": decision.matched_rule,
            "binding": decision.budget.binding,
            "reasons": decision.explain(),
            "budget_usd": float(budget_usd),
            "model": model,
            "provider": provider,
            "envelopes": [e.as_dict() for e in decision.budget.envelopes],
        }

    def record_override(self, precheck: dict[str, Any], seconds_left: float = 0.0) -> None:
        """Journal that a human deliberately overrode a refusal.

        An override that left no trace would make the audit log a record of what
        the policy *would* have done rather than what happened. This writes the
        override as its own entry, so the sequence reads: refused, then bypassed by
        a human, with the reason.

        It is keyed to the **request id the pre-check allocated**, which
        `authorize_run` then reuses. Keying it to anything else -- as an earlier
        version did, using a synthetic `override-<model>` -- makes the override
        unattributable: no decision carries that id, so no reader can tell which
        refusal was bypassed, and the `humanReview` field of a Decision Record can
        never be populated.
        """
        request_id = precheck.get("request_id")
        if not request_id:
            raise ValueError(
                "precheck payload carries no request_id; an override that cannot be "
                "attributed to a decision is not evidence"
            )
        self.tesoro.audit.attach_settlement(
            request_id,
            {
                "type": "human_override",
                "channel": Channel.INTERNAL.value,
                "requestId": request_id,
                "overrodeVerdict": precheck.get("verdict"),
                "engine": precheck.get("engine"),
                "matchedRule": precheck.get("matched_rule"),
                "budgetUsd": precheck.get("budget_usd"),
                "secondsLeftInWindow": round(seconds_left, 1),
                "note": (
                    "A human used the dangerous-allow override inside the warning "
                    "window. The refusal above stands as the policy's decision; "
                    "this entry records that it was deliberately bypassed."
                ),
            },
        )

    def check_spend(self, spent_usd: float) -> SpendCheck:
        """Mid-run cost ceiling. Call from the agent's own loop.

        ```python
        if gov.check_spend(running_cost).should_stop:
            break
        ```

        This is the capability AEGL supplies rather than duplicates: LangGraph and
        Google ADK cap *steps*, not spend, and one long-context call can cost more
        than fifty short ones. The ceiling enforced here is the budget
        `authorize_run` actually authorized -- not what the caller asked for, which
        the policy may have clamped.

        Deterministic and journal-free by design; it is called every step, and a
        guard that costs a database write per step would not be used.
        """
        if self._run_request_id is None:
            return SpendCheck(False, "no run under governance", float(spent_usd), 0.0)
        if self._stopped is not None:
            return self._stopped  # already tripped; stay tripped

        spent, budget = float(spent_usd), self._run_budget_usd
        if spent >= budget:
            check = SpendCheck(
                True,
                f"run has spent ${spent:.6f} of its authorized ${budget:.6f} "
                f"for {self._run_model}",
                spent, budget,
            )
            self._stopped = check
            self._journal_stop(check)
            return check
        return SpendCheck(
            False, "within authorized budget", spent, budget,
            warn=budget > 0 and spent >= budget * self.warn_at,
        )

    def _journal_stop(self, check: SpendCheck) -> None:
        """Record the stop once, when it happens.

        A ceiling that stops a run silently is indistinguishable from an agent
        that simply finished, which would make the audit trail misleading in
        exactly the case it exists for.
        """
        if self._run_request_id:
            self.tesoro.audit.attach_settlement(
                self._run_request_id,
                {"type": "spend_ceiling_stop", "channel": Channel.INTERNAL.value,
                 **check.as_dict()},
            )

    def settle_run(self, actual_cost_usd: float, *, success: bool = True) -> None:
        """Record what the run actually cost. Safe to call when no run is open."""
        if self._run_request_id:
            self.inference.settle_run(
                self._run_request_id, actual_cost_usd=actual_cost_usd, success=success
            )
            self._run_request_id = None

    # --- external channel: USDC over x402 ---------------------------------
    def wrap(self, client: Any, *, vendor: Vendor | None = None) -> Any:
        """Put a payment client behind AEGL.

        ADR-002: the returned object is the only thing holding the signer, so the
        agent cannot pay without a decision. Refusals raise `GovernanceRefused`.
        """
        ok, missing = conforms(client)
        if not ok:
            raise TypeError(
                "client does not satisfy PaymentClient; missing: " + ", ".join(missing)
            )
        governed = GovernedBuyer(self.tesoro, client, vendor=vendor or self.seller)
        return _RecordingBuyer(governed, self)

    # --- reporting --------------------------------------------------------
    def internal_budget(self) -> dict[str, Any]:
        return self.inference.budget_state()

    def external_budget(self) -> dict[str, Any]:
        from .engines.economic import treasury as treasury_engine  # noqa: PLC0415

        probe = self.tesoro.build_request(
            resource="/market/snapshot", amount_usd="0",
            vendor=self.seller, channel=Channel.EXTERNAL,
        )
        snapshot = self.tesoro.snapshot_for(probe)
        return treasury_engine.evaluate(probe, snapshot, self.bundle.treasury).as_dict()

    def report(self) -> dict[str, Any]:
        """Everything a UI needs, as plain data.

        `tesoro.ui.render()` takes exactly this, which is what lets one panel serve
        every cockpit without any of them importing the others.
        """
        return {
            "policy": {"name": self.bundle.name, "hash": self.bundle.hash,
                       "rules": len(self.bundle.rules)},
            "labels": dict(self.labels),
            "intent": (
                self.active_intent().as_dict() if self.active_intent() else None
            ),
            # Vendor-safe projection only. A report rendered in a browser must not
            # carry the controller's details just because the layer happens to know
            # them.
            "identity": (
                self.identity().disclose("vendor") if self.identity() else None
            ),
            "advisor": {
                "provider": self.advisor_spec[0] if self.advisor_spec else None,
                "model": self.advisor_spec[1] if self.advisor_spec else None,
                "error": self.advisor_error,
                "warning": self.advisor_warning,
            },
            "internal": self.internal_budget(),
            "external": self.external_budget(),
            "events": [e.as_dict() for e in self.events],
            "run": {
                "model": self._run_model,
                "budgetUsd": self._run_budget_usd,
                "stopped": self._stopped.as_dict() if self._stopped else None,
            },
            "summary": self.tesoro.summary(),
        }

    def close(self) -> None:
        self.tesoro.close()

    def __enter__(self) -> "Governor":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class _RecordingBuyer:
    """Wraps `GovernedBuyer` so every decision lands in `Governor.events`.

    `GovernedBuyer` raises on refusal, which is right, but the decision goes out
    with the exception and is lost to the UI. This captures it on the way past
    and re-raises unchanged.
    """

    def __init__(self, governed: Any, governor: Governor) -> None:
        self._g = governed
        self._gov = governor

    def __getattr__(self, name: str) -> Any:
        return getattr(self._g, name)

    async def get_paid(self, path: str) -> Any:
        before = len(self._gov.tesoro.audit.entries())
        try:
            result = await self._g.get_paid(path)
        except GovernanceRefused as exc:
            event = to_event(exc.decision, Channel.EXTERNAL.value, path)
            advised = getattr(exc, "advised", None)
            if advised is not None:
                event.verdict = exc.final_verdict.value
                event.advice = advised.advice.as_dict() if advised.advice else None
                event.advisor_changed = advised.changed
                event.advisor_skip_reason = advised.skip_reason
                event.reasons = advised.explain()
                if advised.changed:
                    event.engine = "advisor"
            self._gov.events.append(event)
            raise
        for entry in self._gov.tesoro.audit.entries()[before:]:
            payload = entry.payload.get("decision")
            if payload:
                self._gov.events.append(
                    event_from_payload(payload, Channel.EXTERNAL.value, path, entry.payload)
                )
                break
        return result


def _resolve_bundle(policy: str | Path | None) -> PolicyBundle:
    """Accept a bundle name (`"strict"`) or a path; fall back to the default."""
    if policy is None:
        return load_bundle()
    path = Path(policy)
    if path.suffix and path.exists():
        return load_bundle(path)
    for candidate in available_bundles():
        if candidate.stem == str(policy):
            return load_bundle(candidate)
    return load_bundle()
