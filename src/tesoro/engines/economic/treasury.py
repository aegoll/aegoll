"""Engine 1 -- treasury: budgets, limits and earned authority.

Evaluates every envelope, not just the first one that fails, so the cockpit can
show which constraint is tight and how much headroom is left. Integer arithmetic
throughout.
"""

from __future__ import annotations

from ...config import TreasuryConfig
from ...store import HistorySnapshot
from ...domain import (
    BudgetVerdict,
    CountEnvelope,
    Envelope,
    PaymentRequest,
    Reason,
    Verdict,
    fmt_usd,
)


def evaluate(
    request: PaymentRequest, snapshot: HistorySnapshot, cfg: TreasuryConfig
) -> BudgetVerdict:
    amount = request.amount_atomic

    # Earned authority: a clean record raises the per-transaction ceiling.
    per_tx_limit, multiplier, tier_label = cfg.per_tx_limit_for(
        snapshot.vendor.settled_count, snapshot.vendor.disputed_count
    )

    # `balance` is spendable balance minus the untouchable reserve.
    spendable = max(0, cfg.balance_atomic - cfg.emergency_reserve_atomic)

    envelopes = (
        # A cap on one payment, not a running total: `used` stays zero and every
        # request is checked fresh against the whole limit.
        Envelope(
            "per_transaction", per_tx_limit, 0, f"per call ({tier_label})",
            cumulative=False,
        ),
        Envelope("balance", spendable, snapshot.spent_month_atomic, "spendable balance"),
        Envelope("daily", cfg.daily_atomic, snapshot.spent_today_atomic, "today"),
        Envelope("monthly", cfg.monthly_atomic, snapshot.spent_month_atomic, "this month"),
        Envelope(
            "per_vendor",
            cfg.per_vendor_30d_atomic,
            snapshot.spent_vendor_30d_atomic,
            "vendor, rolling 30d",
        ),
        Envelope(
            "per_resource",
            cfg.per_resource_30d_atomic,
            snapshot.spent_resource_30d_atomic,
            "resource, rolling 30d",
        ),
    )

    # Rate counters, then count envelopes over a long window. An absent long-window limit
    # contributes no envelope at all rather than one with a limit of zero -- ENV-8, and the
    # difference between "unconstrained" and "frozen".
    counters = [
        CountEnvelope("velocity_60s", cfg.velocity_60s, snapshot.count_last_60s, "last 60s"),
        CountEnvelope("velocity_1h", cfg.velocity_1h, snapshot.count_last_1h, "last hour"),
    ]
    if cfg.actions_per_day is not None:
        counters.append(
            CountEnvelope("actions_per_day", cfg.actions_per_day, snapshot.count_today, "today")
        )
    if cfg.actions_per_month is not None:
        counters.append(
            CountEnvelope(
                "actions_per_month", cfg.actions_per_month, snapshot.count_month, "this month"
            )
        )
    counters = tuple(counters)

    reasons: list[Reason] = []
    failing: list[Envelope] = []

    for env in envelopes:
        if not env.admits(amount):
            failing.append(env)
            reasons.append(
                Reason(
                    source="treasury",
                    code=f"envelope_exceeded:{env.name}",
                    detail=(
                        f"{env.name} ({env.window}) admits {fmt_usd(env.headroom_atomic)} "
                        f"but the request is {fmt_usd(amount)}"
                    ),
                    verdict=Verdict.REJECT,
                )
            )

    breached_counters = [c for c in counters if not c.admits()]
    for c in breached_counters:
        reasons.append(
            Reason(
                source="treasury",
                code=f"velocity_exceeded:{c.name}",
                detail=f"{c.used} transactions in {c.window}, limit {c.limit}",
                verdict=Verdict.REJECT,
            )
        )

    # Reserve check is separate so it can be reported distinctly: this is the
    # floor the agent may never spend below, regardless of other envelopes.
    if cfg.balance_atomic - snapshot.spent_month_atomic - amount < cfg.emergency_reserve_atomic:
        env = Envelope(
            "emergency_reserve",
            cfg.emergency_reserve_atomic,
            cfg.emergency_reserve_atomic,
            "untouchable floor",
        )
        failing.append(env)
        reasons.append(
            Reason(
                source="treasury",
                code="emergency_reserve",
                detail=(
                    f"would leave the treasury below its "
                    f"{fmt_usd(cfg.emergency_reserve_atomic)} reserve"
                ),
                verdict=Verdict.REJECT,
            )
        )

    ok = not failing and not breached_counters
    binding: str | None = None
    if failing:
        # Report the envelope with the least headroom as the binding one.
        binding = min(failing, key=lambda e: e.headroom_atomic).name
    elif breached_counters:
        binding = breached_counters[0].name

    if ok:
        tightest = min(envelopes, key=lambda e: e.headroom_atomic)
        reasons.append(
            Reason(
                source="treasury",
                code="within_all_envelopes",
                detail=(
                    f"inside all {len(envelopes)} envelopes; tightest is {tightest.name} "
                    f"with {fmt_usd(tightest.headroom_atomic)} headroom"
                ),
                verdict=Verdict.APPROVE,
            )
        )
        if multiplier > 1.0:
            reasons.append(
                Reason(
                    source="treasury",
                    code="earned_authority",
                    detail=(
                        f"{snapshot.vendor.settled_count} settled transactions raised the "
                        f"per-call limit to {fmt_usd(per_tx_limit)} ({tier_label})"
                    ),
                )
            )

    return BudgetVerdict(
        ok=ok,
        envelopes=envelopes,
        counters=counters,
        binding=binding,
        reasons=tuple(reasons),
    )
