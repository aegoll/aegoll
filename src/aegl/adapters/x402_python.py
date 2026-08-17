"""Engine 7 -- the x402 adapter.

`GovernedBuyer` wraps any client satisfying the payment-rail interface -- the nine
members `X402Buyer` exposes -- so governing an existing agent is a one-line swap at
the point the buyer is constructed, with no change to the tools themselves.

AEGL imports no agent. It depends on `x402_core`, the shared protocol layer, which
is what lets the same governor serve the Claude Agent SDK, LangGraph and Google ADK
agents without knowing which one it is talking to.

**ADR-002 in practice:** the agent is handed the `GovernedBuyer`, and only the
`GovernedBuyer` holds the `X402Buyer` that holds the signer. Authorization is
therefore structurally enforced rather than merely requested -- an agent, or a
prompt injection steering one, has no path to the key.

Import is lazy so the engines stay testable with no x402 dependency installed.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..runtime import Aegl
from ..domain import Purpose, Vendor, Verdict, usd_to_atomic

def _ensure_core_importable() -> None:
    """Confirm the shared protocol layer is importable, and say so clearly if not.

    The prototype inserted `parents[3] / "agents" / "x402_core"` into `sys.path`, which
    only worked inside the monorepo and silently did nothing anywhere else — leaving a
    confusing ImportError several frames later. A library must not mutate `sys.path` to
    reach into a directory it guessed at. See PLAN.md F-A1.

    AEGL depends on the payment rail, never on a particular agent's package. If the rail
    is missing, that is a missing optional dependency and the message should say so.
    """
    if importlib.util.find_spec("x402_core") is None:
        raise ImportError(
            "the x402 rail adapter needs `x402_core`, which is not importable. "
            "Install the rail extra (`pip install aegoll[x402]`), or add the shared "
            "protocol layer to your environment. AEGL itself needs neither."
        )


class GovernanceRefused(RuntimeError):
    """AEGL declined the payment. The signer was never touched."""

    def __init__(
        self, decision: Any, advised: Any = None, final_verdict: Any = None
    ) -> None:
        self.decision = decision
        self.advised = advised
        self.final_verdict = final_verdict or decision.verdict
        verdict = self.final_verdict.value
        source = advised if advised is not None else decision
        reasons = "; ".join(source.explain())
        super().__init__(f"AEGL returned {verdict}: {reasons}")


@dataclass
class GovernedResult:
    """What the agent sees. Mirrors x402_core's PaidCall, plus the decision."""

    path: str
    authorized: bool
    verdict: str
    spent_usd: Decimal
    transaction: str | None = None
    body: Any = None
    decision: Any = None
    reasons: list[str] = field(default_factory=list)

    def as_dict(self, include_body: bool = False) -> dict[str, Any]:
        out = {
            "path": self.path,
            "authorized": self.authorized,
            "verdict": self.verdict,
            "spentUsd": float(self.spent_usd),
            "transaction": self.transaction,
            "reasons": self.reasons,
        }
        if include_body:
            out["data"] = self.body
        return out


class GovernedBuyer:
    """An `X402Buyer` that cannot pay without AEGL's approval."""

    def __init__(
        self,
        aegl: Aegl,
        buyer: Any,
        vendor: Vendor | None = None,
        purpose: Purpose = Purpose.DATA_PURCHASE,
    ) -> None:
        self._aegl = aegl
        self._buyer = buyer  # deliberately private: the agent never gets this
        self._purpose = purpose
        self.vendor = vendor or Vendor(id="x402-poc-desk", name="x402 POC Data Desk")
        self._catalog_endpoints: list[dict[str, Any]] = []

    # --- pass-through, ungoverned because it costs nothing ----------------
    @property
    def address(self) -> str:
        return self._buyer.address

    @property
    def spend_cap_usd(self) -> Decimal:
        return self._buyer.spend_cap_usd

    @property
    def total_spent_usd(self) -> Decimal:
        return self._buyer.total_spent_usd

    @property
    def calls(self) -> list[Any]:
        return self._buyer.calls

    async def get_free(self, path: str) -> Any:
        result = await self._buyer.get_free(path)
        # Cache the catalogue's descriptions so an advisor can read what the vendor
        # claims about each endpoint.
        if path == "/catalog" and isinstance(result, dict):
            endpoints = result.get("endpoints")
            if isinstance(endpoints, list):
                self._catalog_endpoints = [e for e in endpoints if isinstance(e, dict)]
        return result

    async def quote(self, path: str) -> Any:
        """Price discovery is free, so it needs no authorization."""
        return await self._buyer.quote(path)

    def budget_snapshot(self) -> dict[str, Any]:
        """The agent's view of its budget: x402 cap *and* AEGL envelopes."""
        base = self._buyer.budget_snapshot()
        summary = self._aegl.summary()
        base["governedBy"] = "AEGL"
        base["policy"] = summary["policy"]
        base["pendingReviews"] = summary["pendingReviews"]
        return base

    # --- the governed path ------------------------------------------------
    def _describe(self, path: str) -> str:
        """The vendor-supplied description for this resource, if any.

        This is the untrusted text an advisor reads. Sourced from the seller's own
        free `/catalog`, so it is genuinely counterparty-controlled -- which is the
        point: it is exactly the channel a hostile vendor would use, and the clamp
        in `advise.py` is what makes reading it safe.
        """
        for entry in self._catalog_endpoints:
            if entry.get("path") == path or path.startswith(
                str(entry.get("path", "")).split(":")[0]
            ):
                return str(entry.get("description") or "")
        return ""

    async def get_paid(self, path: str) -> Any:
        """Quote, authorize, and only then pay.

        Raises `GovernanceRefused` on anything other than APPROVE. Raising rather
        than returning keeps the failure impossible to ignore, and the caller in
        each agent's tool layer already converts exceptions into a tool error the
        model can read.
        """
        quote = await self._buyer.quote(path)
        if quote is not None and not hasattr(quote, "price_usd"):
            # `conforms()` checks that `quote` exists; it cannot check what the
            # method returns. This is where that gap surfaces, so it says what is
            # wrong rather than raising AttributeError from three frames down.
            raise TypeError(
                f"quote() returned {type(quote).__name__} with no `price_usd`. "
                "AEGL governs the amount on the quote, so it cannot govern a "
                "quote that has no price. See aegl.plugin.Quote."
            )
        amount_atomic = (
            usd_to_atomic(quote.price_usd) if quote is not None else 0
        )

        request = self._aegl.build_request(
            resource=path,
            amount_usd=str(quote.price_usd) if quote is not None else "0",
            vendor=self.vendor,
            purpose=self._purpose,
        )
        # Phase 2: when an advisor is configured, `advise()` runs the same
        # deterministic decision and then consults -- but only if the EIAP agrees
        # the exposure justifies the analysis cost. With no advisor this is
        # exactly Phase 1.
        advised = None
        if getattr(self._aegl, "advisor", None) is not None:
            advised = self._aegl.advise(
                request, vendor_description=self._describe(path)
            )
            decision = advised.decision
            verdict = advised.final_verdict
        else:
            decision = self._aegl.authorize(request)
            verdict = decision.verdict

        if verdict is not Verdict.APPROVE:
            raise GovernanceRefused(decision, advised=advised, final_verdict=verdict)

        # Approved: hand off to x402 for settlement.
        call = await self._buyer.get_paid(path)

        settled = getattr(call, "payment_status", "") == "settled"
        self._aegl.record_settlement(
            request.id,
            success=settled,
            tx_hash=getattr(call, "transaction", None),
            amount_atomic=amount_atomic,
        )
        return call

    async def aclose(self) -> None:
        await self._buyer.aclose()


def build_governed_buyer(
    aegl: Aegl,
    *,
    vendor: Vendor | None = None,
    private_key: str | None = None,
    base_url: str | None = None,
    spend_cap_usd: str | float | None = None,
    rpc_url: str | None = None,
) -> GovernedBuyer:
    """Construct a GovernedBuyer from the shared wallet config and X402Buyer."""
    _ensure_core_importable()
    from x402_core.buyer import X402Buyer  # noqa: PLC0415
    from x402_core.config import load_wallet_config  # noqa: PLC0415

    cfg = load_wallet_config()
    if not (private_key or cfg.buyer_private_key):
        raise RuntimeError(
            "No buyer private key. Set BUYER_PRIVATE_KEY in the repo-root .env "
            "(see agents/README.md for the wallet and faucet steps)."
        )

    buyer = X402Buyer(
        private_key=private_key or cfg.buyer_private_key,
        base_url=base_url or cfg.data_api_url,
        spend_cap_usd=Decimal(str(spend_cap_usd)) if spend_cap_usd is not None
        else cfg.usdc_cap_usd,
        rpc_url=rpc_url or cfg.rpc_url,
    )
    return GovernedBuyer(aegl, buyer, vendor=vendor)
