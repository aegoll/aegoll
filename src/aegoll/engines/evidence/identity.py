"""Engine 10 — agent identity: who is acting, and under whose authority?

Intent asks whether an action is what the agent was sent to do. Identity asks a
prior question: *is this agent still authorised to act at all, and does the
authority it claims actually belong to it?*

## Pseudonymous by default, and that is a design position

`agentId` is a stable handle, not a person. Everything that could identify a human
or an organisation — controller, operator, wallets, spending limits — is optional,
grouped separately, and **excluded from counterparty disclosure by default**.

That is not squeamishness. A standard which makes real-world identity the price of
transacting has answered the accountability question by abolishing privacy, and the
roadmap raises the privacy problem in the same breath as KYA precisely because the
two have to be solved together. So disclosure is a first-class operation here
rather than an afterthought: `disclose(audience)` returns what that audience may
see, and a test asserts the private fields never reach a counterparty.

The sharpest case is `spendingLimits`. Telling a seller how much budget an agent has
left invites it to charge exactly that. A field that looks like transparency is a
price-discovery gift to the counterparty, so it never leaves the layer.

## Two invariants worth stating

**Delegation may only narrow.** A sub-agent's authority cannot exceed its parent's.
A delegation that widens authority is an escalation, and it is refused rather than
recorded — the same clamp discipline every engine here obeys.

**Listing a credential is not verifying it.** AEGS 0.1 defines the *reference* to a
credential and has no verification mechanism, so `verified` is false and the record
says so. Reporting an unchecked credential as assurance would be the same class of
mistake as rendering an unmeasured value as zero.
"""

from __future__ import annotations

import json
from importlib import resources
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...domain import PaymentRequest, Reason, Verdict, atomic_to_usd, fmt_usd, usd_to_atomic

AEGS_VERSION = "0.1"

#: Vendored package data — see `_schemas/PROVENANCE.txt`. The prototype resolved this
#: from `parents[4]`, which only worked inside the monorepo. See PLAN.md F-A1.
SCHEMA_PATH = Path(
    str(resources.files("aegoll") / "_schemas" / "agent-identity-0.1.json")
).resolve()

#: Fields a counterparty may see. Everything else stays inside the layer.
#: `wallets` is absent deliberately -- a seller already sees the address paying it,
#: and publishing the set links an agent's activity across counterparties.
VENDOR_VISIBLE = ("aegsVersion", "agentId", "purpose", "status", "authorizedNetworks")

#: What an auditor sees. Everything, because an audit that cannot see the
#: controller cannot establish accountability -- which is the whole point of one.
AUDITOR_VISIBLE = None  # None means "no redaction"


@dataclass(frozen=True)
class Party:
    """A controller or operator. Private by default, everywhere."""

    id: str
    kind: str = "unknown"
    jurisdiction: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "jurisdiction": self.jurisdiction}


@dataclass(frozen=True)
class Credential:
    """A reference to a credential held elsewhere. Not a verification of it."""

    type: str
    issuer: str
    reference: str | None = None
    verified: bool = False
    expires_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "issuer": self.issuer,
            "reference": self.reference,
            # AEGS 0.1 has no verification mechanism. An implementation that
            # reported True here would be claiming assurance it has not obtained.
            "verified": self.verified,
            "expiresAt": self.expires_at.isoformat() if self.expires_at else None,
        }


@dataclass(frozen=True)
class Identity:
    """What is known about one autonomous economic actor."""

    agent_id: str
    purpose: str
    status: str = "active"
    parent_agent_id: str | None = None
    controller: Party | None = None
    operator: Party | None = None
    wallets: tuple[dict[str, Any], ...] = ()
    authorized_networks: tuple[str, ...] = ()
    per_action_atomic: int | None = None
    daily_atomic: int | None = None
    limits_asset: str | None = None
    credentials: tuple[Credential, ...] = ()
    risk_profile: str | None = None
    policy_version: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.status == "active"

    def permits_network(self, network: str | None) -> bool:
        if not self.authorized_networks or not network:
            return True
        return network in self.authorized_networks

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "aegsVersion": AEGS_VERSION,
            "agentId": self.agent_id,
            "purpose": self.purpose,
            "status": self.status,
            "parentAgentId": self.parent_agent_id,
            "controller": self.controller.as_dict() if self.controller else None,
            "operator": self.operator.as_dict() if self.operator else None,
            "wallets": [dict(w) for w in self.wallets],
            "authorizedNetworks": list(self.authorized_networks),
            "credentials": [c.as_dict() for c in self.credentials],
            "riskProfile": self.risk_profile,
            "policyVersion": self.policy_version,
            "createdAt": (self.created_at or datetime.now(timezone.utc)).isoformat(),
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }
        if self.per_action_atomic is not None or self.daily_atomic is not None:
            out["spendingLimits"] = {
                "perAction": (
                    f"{atomic_to_usd(self.per_action_atomic):.6f}"
                    if self.per_action_atomic is not None
                    else None
                ),
                "daily": (
                    f"{atomic_to_usd(self.daily_atomic):.6f}"
                    if self.daily_atomic is not None
                    else None
                ),
                "asset": self.limits_asset,
            }
        else:
            out["spendingLimits"] = None
        return out

    def disclose(self, audience: str = "vendor") -> dict[str, Any]:
        """What this audience may see.

        Selective disclosure is the privacy mechanism, so it is an operation rather
        than a convention. `vendor` gets the pseudonymous minimum needed to
        transact; `auditor` gets everything, because an audit that cannot see the
        controller cannot establish accountability.
        """
        full = self.as_dict()
        if audience == "auditor":
            return full
        if audience != "vendor":
            raise ValueError(
                f"unknown disclosure audience {audience!r}; add it deliberately "
                "rather than defaulting to full disclosure"
            )
        return {k: v for k, v in full.items() if k in VENDOR_VISIBLE}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Identity":
        def _party(raw: dict[str, Any] | None) -> Party | None:
            return Party(
                id=raw["id"],
                kind=raw.get("kind", "unknown"),
                jurisdiction=raw.get("jurisdiction"),
            ) if raw else None

        def _dt(value: Any) -> datetime | None:
            return datetime.fromisoformat(value) if value else None

        limits = data.get("spendingLimits") or {}
        return cls(
            agent_id=data["agentId"],
            purpose=data["purpose"],
            status=data.get("status", "active"),
            parent_agent_id=data.get("parentAgentId"),
            controller=_party(data.get("controller")),
            operator=_party(data.get("operator")),
            wallets=tuple(data.get("wallets") or ()),
            authorized_networks=tuple(data.get("authorizedNetworks") or ()),
            per_action_atomic=(
                usd_to_atomic(limits["perAction"]) if limits.get("perAction") else None
            ),
            daily_atomic=usd_to_atomic(limits["daily"]) if limits.get("daily") else None,
            limits_asset=limits.get("asset"),
            credentials=tuple(
                Credential(
                    type=c["type"],
                    issuer=c["issuer"],
                    reference=c.get("reference"),
                    verified=bool(c.get("verified", False)),
                    expires_at=_dt(c.get("expiresAt")),
                )
                for c in (data.get("credentials") or ())
            ),
            risk_profile=data.get("riskProfile"),
            policy_version=data.get("policyVersion"),
            created_at=_dt(data.get("createdAt")),
            updated_at=_dt(data.get("updatedAt")),
        )


@dataclass
class IdentityVerdict:
    """What the identity engine concluded about the actor."""

    identity: Identity | None
    verdict: Verdict
    reasons: tuple[Reason, ...] = ()
    flags: tuple[str, ...] = ()

    @property
    def known(self) -> bool:
        """False when no identity is registered — recorded, never implied."""
        return self.identity is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "known": self.known,
            "agentId": self.identity.agent_id if self.identity else None,
            "status": self.identity.status if self.identity else None,
            "verdict": self.verdict.value,
            "flags": list(self.flags),
            # Only ever the vendor-safe projection here: this dict is journalled,
            # and a journal that carries controller details makes every reader of
            # it a holder of personal data.
            "disclosed": self.identity.disclose("vendor") if self.identity else None,
            "reasons": [r.as_dict() for r in self.reasons],
        }


def evaluate(
    request: PaymentRequest,
    identity: Identity | None,
    *,
    now: datetime,
    parent: Identity | None = None,
    spent_today_atomic: int = 0,
    network: str | None = None,
) -> IdentityVerdict:
    """Is this actor authorised to act, and is its claimed authority its own?"""
    if identity is None:
        return IdentityVerdict(
            identity=None,
            verdict=Verdict.APPROVE,
            flags=("no_identity",),
            reasons=(
                Reason(
                    "identity",
                    "no_identity_registered",
                    "no agent identity is registered; the action is ungoverned by "
                    "identity and this record says so rather than implying a check "
                    "that did not run",
                ),
            ),
        )

    from ...domain import narrower  # noqa: PLC0415

    reasons: list[Reason] = []
    flags: list[str] = []
    verdict = Verdict.APPROVE

    def refuse(code: str, detail: str, level: Verdict) -> None:
        nonlocal verdict
        flags.append(code)
        reasons.append(Reason("identity", code, detail, level))
        verdict = narrower(verdict, level)

    # --- is the actor still authorised at all? ---------------------------
    if identity.status == "revoked":
        refuse(
            "identity_revoked",
            f"agent {identity.agent_id} is revoked; it may not transact",
            Verdict.REJECT,
        )
    elif identity.status == "suspended":
        refuse(
            "identity_suspended",
            f"agent {identity.agent_id} is suspended; transacting is paused "
            "pending a human decision",
            Verdict.ESCALATE,
        )

    # --- delegation may only narrow --------------------------------------
    if identity.parent_agent_id:
        if parent is None:
            refuse(
                "identity_parent_unknown",
                f"agent {identity.agent_id} claims delegation from "
                f"{identity.parent_agent_id}, which is not registered; unverifiable "
                "delegated authority is not authority",
                Verdict.REVIEW,
            )
        else:
            if not parent.active:
                refuse(
                    "identity_parent_inactive",
                    f"parent {parent.agent_id} is {parent.status}; delegated "
                    "authority cannot outlive its source",
                    Verdict.REJECT,
                )
            if _widens(identity, parent):
                refuse(
                    "identity_delegation_widens",
                    f"agent {identity.agent_id} claims a higher limit than its "
                    f"parent {parent.agent_id}; a delegation that widens authority "
                    "is an escalation",
                    Verdict.REJECT,
                )

    # --- the network must be one it is authorised on ----------------------
    if not identity.permits_network(network):
        refuse(
            "identity_network_unauthorized",
            f"{network} is not among this agent's authorised networks "
            f"({', '.join(identity.authorized_networks)})",
            Verdict.REJECT,
        )

    # --- the controller's own declared ceilings ---------------------------
    # Applied as an ADDITIONAL constraint. It can only tighten what policy already
    # allows; an identity claiming a higher limit than the policy does not get one.
    # The clamped ceiling, not this identity's own. A delegated agent inherits its
    # parent's limit where it declares none -- AEGS-0.1-ID-4.
    per_action_ceiling = effective_per_action_atomic(identity, parent)
    if per_action_ceiling is not None and request.amount_atomic > per_action_ceiling:
        refuse(
            "identity_per_action_exceeded",
            f"{fmt_usd(request.amount_atomic)} exceeds the per-action limit its "
            f"controller declared ({fmt_usd(per_action_ceiling)})",
            Verdict.REJECT,
        )

    if identity.daily_atomic is not None:
        remaining = identity.daily_atomic - spent_today_atomic
        if request.amount_atomic > remaining:
            refuse(
                "identity_daily_exceeded",
                f"{fmt_usd(request.amount_atomic)} exceeds the "
                f"{fmt_usd(max(0, remaining))} remaining of this agent's declared "
                f"daily limit ({fmt_usd(identity.daily_atomic)})",
                Verdict.REJECT,
            )

    if not reasons:
        reasons.append(
            Reason(
                "identity",
                "identity_ok",
                f"agent {identity.agent_id} is active and acting within the "
                "authority its controller declared",
                Verdict.APPROVE,
            )
        )

    return IdentityVerdict(
        identity=identity, verdict=verdict, reasons=tuple(reasons), flags=tuple(flags)
    )


def narrower_limit(child: int | None, parent: int | None) -> int | None:
    """The effective limit when a child is delegated from a parent. AEGS-0.1-ID-4.

    The narrower of the two, where an undeclared limit contributes no constraint of its own
    but does **not** erase the other's. So:

        child 5, parent 10  -> 5      the child is tighter
        child 10, parent 5  -> 5      the parent binds
        child None, parent 10 -> 10   inherited, NOT unlimited
        child 5, parent None  -> 5
        both None -> None             no identity limit; envelopes still apply

    The third line is the one that was wrong. `I declare no limit` is not `I am unlimited`
    when somebody above you declared one -- and treating it as such made declaring nothing
    strictly more permissive than declaring a large number, which is an escalation reachable
    by omission.
    """
    candidates = [x for x in (child, parent) if x is not None]
    return min(candidates) if candidates else None


def effective_per_action_atomic(identity: Any, parent: Any | None) -> int | None:
    """This agent's per-action ceiling after the delegation clamp."""
    if parent is None:
        return identity.per_action_atomic
    return narrower_limit(identity.per_action_atomic, parent.per_action_atomic)


def _widens(child: Identity, parent: Identity) -> bool:
    """Does a delegated identity claim more authority than its source?

    Only comparable limits are compared, and this function alone is **not** the whole
    clamp. A child that declares no limit widens nothing detectable here, and used to
    escape its parent's ceiling entirely as a result -- envelopes are treasury-scoped and
    do not inherit an identity limit. `effective_per_action_atomic()` closes that, and this
    function keeps its narrower job: catching a child that declares a *larger* number,
    which is worth refusing outright rather than silently clamping.
    """
    for child_limit, parent_limit in (
        (child.per_action_atomic, parent.per_action_atomic),
        (child.daily_atomic, parent.daily_atomic),
    ):
        if child_limit is not None and parent_limit is not None and child_limit > parent_limit:
            return True
    if child.authorized_networks and parent.authorized_networks:
        if not set(child.authorized_networks) <= set(parent.authorized_networks):
            return True
    return False


# --- storage --------------------------------------------------------------


class IdentityStore:
    """Registered agent identities, as plain JSON beside the journal.

    Identities are *declarations* maintained by operators, not measurements
    accumulated by the system, which is why they live outside the sqlite history.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def register(
        self,
        *,
        agent_id: str,
        purpose: str,
        parent_agent_id: str | None = None,
        controller: Party | None = None,
        operator: Party | None = None,
        wallets: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
        authorized_networks: tuple[str, ...] | list[str] = (),
        per_action_usd: str | float | None = None,
        daily_usd: str | float | None = None,
        limits_asset: str | None = None,
        credentials: tuple[Credential, ...] | list[Credential] = (),
        risk_profile: str | None = None,
        policy_version: str | None = None,
        now: datetime | None = None,
    ) -> Identity:
        identity = Identity(
            agent_id=agent_id,
            purpose=purpose,
            parent_agent_id=parent_agent_id,
            controller=controller,
            operator=operator,
            wallets=tuple(wallets),
            authorized_networks=tuple(authorized_networks),
            per_action_atomic=(
                usd_to_atomic(per_action_usd) if per_action_usd is not None else None
            ),
            daily_atomic=usd_to_atomic(daily_usd) if daily_usd is not None else None,
            limits_asset=limits_asset,
            credentials=tuple(credentials),
            risk_profile=risk_profile,
            policy_version=policy_version,
            created_at=now or datetime.now(timezone.utc),
        )
        data = self._load()
        data[agent_id] = identity.as_dict()
        self._save(data)
        return identity

    def get(self, agent_id: str) -> Identity | None:
        raw = self._load().get(agent_id)
        return Identity.from_dict(raw) if raw else None

    def all(self) -> list[Identity]:
        return [Identity.from_dict(raw) for raw in self._load().values()]

    def set_status(self, agent_id: str, status: str) -> bool:
        """Suspend, revoke or reactivate. The record of registration survives."""
        if status not in ("active", "suspended", "revoked"):
            raise ValueError(f"unknown status {status!r}")
        data = self._load()
        if agent_id not in data:
            return False
        if data[agent_id].get("status") == "revoked" and status != "revoked":
            raise ValueError(
                "revocation is terminal; register a new identity rather than "
                "reviving a revoked one"
            )
        data[agent_id]["status"] = status
        data[agent_id]["updatedAt"] = datetime.now(timezone.utc).isoformat()
        self._save(data)
        return True


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate(identity: dict[str, Any]) -> tuple[bool, list[str]]:
    try:
        import jsonschema  # noqa: PLC0415
    except ImportError:
        return False, ["jsonschema is not installed; cannot validate"]

    validator = jsonschema.Draft202012Validator(load_schema())
    problems = [
        f"{'/'.join(str(p) for p in err.path) or '<root>'}: {err.message}"
        for err in sorted(validator.iter_errors(identity), key=lambda e: list(e.path))
    ]
    return (not problems), problems
