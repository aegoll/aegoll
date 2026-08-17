"""Engine 4 -- risk: how dangerous this specific transaction looks.

Deliberately not machine learning. Every signal is a named, inspectable term with
a configured weight, because a risk score nobody can explain is useless in an
audit and impossible to appeal.

The reprice signal is the one worth noting: it compares the amount against the
*vendor's own* historical price for the same resource. A seller that silently
charges 10x for an endpoint it used to sell cheaply is caught here, where no
absolute amount threshold would notice.
"""

from __future__ import annotations

from ...config import RiskConfig
from ...store import HistorySnapshot
from ...domain import PaymentRequest, Score, Term, fmt_usd


def evaluate(
    request: PaymentRequest, snapshot: HistorySnapshot, cfg: RiskConfig
) -> Score:
    amount = request.amount_atomic
    vendor = snapshot.vendor
    flags: list[str] = []
    terms: list[Term] = []

    # 1. Absolute magnitude, saturating.
    magnitude = min(1.0, amount / max(1, cfg.amount_saturation_atomic))
    terms.append(
        Term(
            "amount_magnitude",
            magnitude,
            cfg.weight_amount,
            f"{fmt_usd(amount)} against a {fmt_usd(cfg.amount_saturation_atomic)} ceiling",
        )
    )

    # 2. Vendor novelty -- the inverse of settled volume.
    novelty = 1.0 - min(1.0, vendor.settled_count / 20.0)
    terms.append(
        Term(
            "vendor_novelty",
            novelty,
            cfg.weight_novelty,
            f"{vendor.settled_count} prior settled transactions with this vendor",
        )
    )
    if vendor.is_new:
        flags.append("unknown_vendor")

    # 3. Deviation from this agent's own spending baseline.
    z = snapshot.amount_zscore(amount)
    if z is None:
        z_norm, z_detail = 0.0, "no baseline yet (fewer than 3 settled transactions)"
    else:
        z_norm = min(1.0, max(0.0, z) / max(1e-9, cfg.zscore_saturation))
        z_detail = f"z={z:+.2f} against this agent's mean {fmt_usd(int(snapshot.agent_mean_atomic))}"
        if z >= cfg.zscore_saturation:
            flags.append("amount_anomaly")
    terms.append(Term("amount_zscore", z_norm, cfg.weight_zscore, z_detail))

    # 4. Velocity -- bursts are a classic compromise signal.
    velocity = min(1.0, snapshot.count_last_60s / 10.0)
    terms.append(
        Term(
            "velocity",
            velocity,
            cfg.weight_velocity,
            f"{snapshot.count_last_60s} transactions in the last 60s",
        )
    )
    if snapshot.count_last_60s >= 10:
        flags.append("velocity_burst")

    # 5. Reprice -- the vendor charging more than it historically did.
    median = snapshot.vendor_resource_median(request.resource)
    if median and median > 0:
        ratio = amount / median
        reprice = min(1.0, max(0.0, (ratio - 1.0) / max(1e-9, cfg.reprice_ratio_flag - 1.0))) \
            if ratio > 1.0 else 0.0
        detail = f"{ratio:.2f}x the vendor's historical {fmt_usd(median)} for this resource"
        if ratio >= cfg.reprice_ratio_flag:
            flags.append("vendor_repriced")
    else:
        reprice, detail = 0.0, "no historical price for this resource"
    terms.append(Term("reprice", reprice, cfg.weight_reprice, detail))

    # 6. Failure history.
    fail_rate = 0.0 if vendor.attempts == 0 else vendor.failed_count / vendor.attempts
    terms.append(
        Term(
            "failure_history",
            min(1.0, fail_rate),
            cfg.weight_failures,
            f"{vendor.failed_count} failed of {vendor.attempts} attempts",
        )
    )
    if fail_rate > 0.2 and vendor.attempts >= 3:
        flags.append("unreliable_vendor")

    value = sum(t.contribution for t in terms)

    if vendor.disputed_count > 0:
        flags.append("disputed_history")
        value = max(value, cfg.high_risk_threshold)  # a dispute floors risk at high

    value = round(max(0.0, min(1.0, value)), 4)
    if value >= cfg.high_risk_threshold:
        flags.append("high_risk")

    return Score(value=value, terms=tuple(terms), flags=tuple(flags))
