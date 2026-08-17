"""The four required scenarios from `docs/archive/2026-08-aegl-original-research-prompt.md`, plus a deterministic runner.

**Honest labelling matters here.** The x402 seller in this repo sells $0.001-$0.01,
so only Scenario A can run against a real 402 and a real settlement. B, C and D use
simulated vendors and seeded history -- through the *same* `decide()` code path,
only the source of the request differs. Every report says which is which.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .clock import FixedClock
from .config import PolicyBundle, load_bundle
from .runtime import Aegl, Paths
from .store import Store
from .domain import Decision, Purpose, Vendor, Verdict

# A fixed instant so every scenario run is byte-identical.
BASE_TIME = datetime(2026, 8, 12, 14, 30, tzinfo=timezone.utc)

POC_VENDOR = Vendor(id="x402-poc-desk", name="x402 POC Data Desk")
KNOWN_VENDOR = Vendor(id="acme-compute", name="Acme Compute")
NEW_VENDOR = Vendor(id="newco-analytics", name="NewCo Analytics")
SHADY_VENDOR = Vendor(id="unknown-7f2a", name="Unknown Counterparty 7f2a")


@dataclass
class Scenario:
    key: str
    title: str
    live: bool
    amount_usd: str
    vendor: Vendor
    resource: str
    purpose: Purpose
    expected: tuple[Verdict, ...]
    rationale: str
    seed: Callable[[Store, datetime], None] | None = None
    expected_value_usd: str | None = None


@dataclass
class ScenarioResult:
    scenario: Scenario
    decision: Decision
    passed: bool
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.scenario.key,
            "title": self.scenario.title,
            "mode": "live-capable" if self.scenario.live else "simulated",
            "amountUsd": self.scenario.amount_usd,
            "vendor": self.scenario.vendor.display,
            "expected": [v.value for v in self.scenario.expected],
            "actual": self.decision.verdict.value,
            "matchedRule": self.decision.matched_rule,
            "passed": self.passed,
            "trust": self.decision.trust.value,
            "risk": self.decision.risk.value,
            "riskFlags": list(self.decision.risk.flags),
            "wouldInvokeAi": self.decision.intelligence.eiap.would_invoke,
            "wouldTier": self.decision.intelligence.eiap.would_tier.value,
            "latencyUs": round(self.decision.latency_us, 1),
        }


# --- history seeders -------------------------------------------------------


def _settle(
    store: Store,
    *,
    vendor_id: str,
    resource: str,
    amount_atomic: int,
    at: datetime,
    idx: int,
    agent_id: str = "agent-1",
    disputed: bool = False,
    success: bool = True,
) -> None:
    store.record(
        tx_id=f"seed-{vendor_id}-{idx}",
        at=at,
        agent_id=agent_id,
        vendor_id=vendor_id,
        resource=resource,
        amount_atomic=amount_atomic,
        verdict=Verdict.APPROVE,
        settled=True,
        success=success,
        disputed=disputed,
        tx_hash=f"0xseed{idx:04d}",
    )


def seed_trusted_vendor(store: Store, now: datetime) -> None:
    """A long, clean relationship with Acme -- earns trust and authority."""
    store.register_vendor(KNOWN_VENDOR.id, KNOWN_VENDOR.name, first_seen=now - timedelta(days=90))
    for i in range(30):
        _settle(
            store,
            vendor_id=KNOWN_VENDOR.id,
            resource="/compute/batch",
            amount_atomic=8_000_000,  # $8 each
            at=now - timedelta(days=60) + timedelta(days=i),
            idx=i,
        )


def seed_thin_history(store: Store, now: datetime) -> None:
    """A modest baseline of small spend, so a $1,000 request is a clear outlier."""
    store.register_vendor(POC_VENDOR.id, POC_VENDOR.name, first_seen=now - timedelta(days=20))
    for i in range(12):
        _settle(
            store,
            vendor_id=POC_VENDOR.id,
            resource="/market/snapshot",
            amount_atomic=1_000,  # $0.001
            at=now - timedelta(days=10) + timedelta(hours=i),
            idx=i,
        )
    store.register_vendor(NEW_VENDOR.id, NEW_VENDOR.name, first_seen=now - timedelta(hours=1))


def seed_suspicious(store: Store, now: datetime) -> None:
    """A vendor with failures and a dispute, plus a burst of recent activity."""
    seed_thin_history(store, now)
    store.register_vendor(SHADY_VENDOR.id, SHADY_VENDOR.name, first_seen=now - timedelta(days=3))
    # Two settlements, one of them disputed.
    _settle(
        store, vendor_id=SHADY_VENDOR.id, resource="/data/feed",
        amount_atomic=20_000_000, at=now - timedelta(days=2), idx=900,
    )
    _settle(
        store, vendor_id=SHADY_VENDOR.id, resource="/data/feed",
        amount_atomic=20_000_000, at=now - timedelta(days=1), idx=901, disputed=True,
    )
    # Approved-but-never-settled attempts read as vendor-side failures.
    for i in range(3):
        store.record(
            tx_id=f"seed-fail-{i}",
            at=now - timedelta(hours=6 - i),
            agent_id="agent-1",
            vendor_id=SHADY_VENDOR.id,
            resource="/data/feed",
            amount_atomic=20_000_000,
            verdict=Verdict.APPROVE,
            settled=False,
            success=False,
        )
    # A burst in the last minute -- the velocity signal.
    for i in range(9):
        store.record(
            tx_id=f"seed-burst-{i}",
            at=now - timedelta(seconds=50 - i),
            agent_id="agent-1",
            vendor_id=SHADY_VENDOR.id,
            resource="/data/feed",
            amount_atomic=1_000_000,
            verdict=Verdict.APPROVE,
            settled=False,
            success=False,
        )


# --- the scenarios ---------------------------------------------------------

SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        key="A",
        title="Microtransaction",
        live=True,
        amount_usd="0.001",
        vendor=POC_VENDOR,
        resource="/market/snapshot",
        purpose=Purpose.DATA_PURCHASE,
        expected=(Verdict.APPROVE,),
        rationale=(
            "The real seller's cheapest endpoint. Deterministic engines alone should "
            "approve it, and the EIAP should refuse to consult a model because the "
            "exposure is two orders of magnitude below break-even."
        ),
        seed=seed_thin_history,
    ),
    Scenario(
        key="B",
        title="Medium transaction, trusted vendor",
        live=False,
        amount_usd="25.00",
        vendor=KNOWN_VENDOR,
        resource="/compute/batch",
        purpose=Purpose.COMPUTE,
        expected=(Verdict.APPROVE, Verdict.REVIEW),
        rationale=(
            "A long clean record with this vendor should carry a $25 request. Earned "
            "authority raises the per-transaction ceiling; the EIAP starts to favour "
            "consulting a model, which Phase 1 logs without acting on."
        ),
        seed=seed_trusted_vendor,
        expected_value_usd="40.00",
    ),
    Scenario(
        key="C",
        title="High-value transaction, unfamiliar vendor",
        live=False,
        amount_usd="1000.00",
        vendor=NEW_VENDOR,
        resource="/analytics/bespoke",
        purpose=Purpose.SERVICE,
        expected=(Verdict.REVIEW, Verdict.ESCALATE, Verdict.REJECT),
        rationale=(
            "Far beyond every envelope and from a vendor with no history. Must never "
            "auto-approve. The EIAP should strongly favour a large model here -- this "
            "is the transaction where paying for reasoning is obviously rational."
        ),
        seed=seed_thin_history,
    ),
    Scenario(
        key="D",
        title="Suspicious transaction",
        live=False,
        amount_usd="500.00",
        vendor=SHADY_VENDOR,
        resource="/data/feed",
        purpose=Purpose.SERVICE,
        expected=(Verdict.ESCALATE, Verdict.REJECT, Verdict.REVIEW),
        rationale=(
            "Poor trust, a dispute on record, repeated failures and a burst of recent "
            "activity. Note it is refused on *authority* grounds first -- $500 breaches "
            "the balance envelope, which is a cheaper and more fundamental objection than "
            "risk. See D2 for the risk engine deciding in isolation."
        ),
        seed=seed_suspicious,
    ),
    Scenario(
        key="D2",
        title="Suspicious but affordable",
        live=False,
        amount_usd="5.00",
        vendor=SHADY_VENDOR,
        resource="/data/feed",
        purpose=Purpose.SERVICE,
        expected=(Verdict.ESCALATE, Verdict.REVIEW, Verdict.REJECT),
        rationale=(
            "The same bad counterparty at an amount that fits every envelope. Budget has "
            "no objection, so the verdict has to come from trust and risk -- which is the "
            "claim scenario D is meant to demonstrate but cannot, because its amount is "
            "refused on authority grounds first."
        ),
        seed=seed_suspicious,
    ),
)


def run_scenario(
    scenario: Scenario,
    bundle: PolicyBundle | None = None,
    root: str = ".data-scenarios",
) -> ScenarioResult:
    clock = FixedClock(BASE_TIME)
    aegl = Aegl(
        bundle=bundle or load_bundle(),
        paths=Paths.ephemeral(root),
        clock=clock,
    )
    try:
        if scenario.seed:
            scenario.seed(aegl.store, BASE_TIME)

        request = aegl.build_request(
            resource=scenario.resource,
            amount_usd=scenario.amount_usd,
            vendor=scenario.vendor,
            purpose=scenario.purpose,
            request_id=f"scenario-{scenario.key}",
            expected_value_usd=scenario.expected_value_usd,
        )
        decision = aegl.decide(request)

        notes: list[str] = []
        if not scenario.live:
            notes.append("simulated vendor -- no real 402 or settlement involved")
        if decision.intelligence.eiap.would_invoke:
            notes.append(
                f"EIAP: Phase 2 would consult a {decision.intelligence.eiap.would_tier.value} "
                "model here"
            )
        else:
            notes.append("EIAP: no model would be economically justified")

        return ScenarioResult(
            scenario=scenario,
            decision=decision,
            passed=decision.verdict in scenario.expected,
            notes=notes,
        )
    finally:
        aegl.close()


def run_all(bundle: PolicyBundle | None = None) -> list[ScenarioResult]:
    b = bundle or load_bundle()
    return [run_scenario(s, b) for s in SCENARIOS]


def by_key(key: str) -> Scenario:
    for s in SCENARIOS:
        if s.key.upper() == key.upper():
            return s
    raise KeyError(f"no scenario {key!r}; have {[s.key for s in SCENARIOS]}")
