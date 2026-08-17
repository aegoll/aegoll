"""Engine 5 -- ROI: is the spend economically justified?

The honest engine. Expected value comes from an operator-declared table; when a
resource is absent, the answer is `unknown` and the decision treats that as an
input to escalation rather than pretending to a number.

Inferring value from a service description is precisely the Phase 2 task, and
faking it here would undermine the comparison Phase 2 is meant to prove.
"""

from __future__ import annotations

from ...config import RoiConfig
from ...domain import PaymentRequest, RoiEstimate


def evaluate(request: PaymentRequest, cfg: RoiConfig) -> RoiEstimate:
    # An explicit value on the request wins over the table: the caller may know
    # something the operator's static config does not.
    if request.expected_value_atomic is not None:
        return RoiEstimate(
            known=True,
            expected_value_atomic=request.expected_value_atomic,
            cost_atomic=request.amount_atomic,
            confidence=request.value_confidence if request.value_confidence is not None
            else cfg.default_confidence,
        )

    declared = cfg.expected_value.get(request.resource)
    if declared is None:
        return RoiEstimate(
            known=False,
            expected_value_atomic=None,
            cost_atomic=request.amount_atomic,
            confidence=None,
        )

    return RoiEstimate(
        known=True,
        expected_value_atomic=declared,
        cost_atomic=request.amount_atomic,
        confidence=cfg.default_confidence,
    )
