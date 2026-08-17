"""Engine 3 -- trust: how much confidence this counterparty has earned.

A weighted sum of named terms, never a black box. Cold start is deliberately low:
an unknown vendor is not trusted, which is what makes the suspicious-transaction
scenario behave sensibly without any special-casing.
"""

from __future__ import annotations

from datetime import datetime

from ...config import TrustConfig
from ...store import VendorStats
from ...domain import Score, Term


def evaluate(vendor: VendorStats, now: datetime, cfg: TrustConfig) -> Score:
    flags: list[str] = []

    if vendor.is_new:
        # No settled history: report the cold-start prior explicitly rather than
        # computing a misleading score from one or two data points.
        flags.append("new_vendor")
        return Score(
            value=round(cfg.cold_start, 4),
            terms=(
                Term(
                    "cold_start_prior",
                    1.0,
                    cfg.cold_start,
                    "no settled history; an unknown vendor is not trusted",
                ),
            ),
            flags=tuple(flags),
        )

    success = vendor.success_rate or 0.0
    volume = min(1.0, vendor.settled_count / max(1, cfg.volume_saturation))
    age = min(1.0, vendor.age_days(now) / max(1e-9, cfg.age_saturation_days))

    terms = [
        Term(
            "success_rate",
            success,
            cfg.weight_success,
            f"{vendor.settled_count} settled of {vendor.attempts} attempts",
        ),
        Term(
            "volume",
            volume,
            cfg.weight_volume,
            f"{vendor.settled_count} settled, saturating at {cfg.volume_saturation}",
        ),
        Term(
            "relationship_age",
            age,
            cfg.weight_age,
            f"{vendor.age_days(now):.1f} days, saturating at {cfg.age_saturation_days:.0f}",
        ),
    ]

    value = sum(t.contribution for t in terms)

    # Disputes are a penalty rather than a term: they must be able to pull an
    # otherwise-excellent record down hard.
    if vendor.dispute_rate > 0:
        penalty = min(1.0, vendor.dispute_rate) * cfg.penalty_dispute
        terms.append(
            Term(
                "dispute_penalty",
                -min(1.0, vendor.dispute_rate),
                cfg.penalty_dispute,
                f"{vendor.disputed_count} disputed of {vendor.settled_count} settled",
            )
        )
        value -= penalty
        flags.append("disputes_on_record")

    if (vendor.success_rate or 1.0) < 0.8 and vendor.attempts >= 3:
        flags.append("elevated_failure_rate")

    return Score(value=round(max(0.0, min(1.0, value)), 4), terms=tuple(terms), flags=tuple(flags))
