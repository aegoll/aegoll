"""AEGL -- Autonomous Economic Governance Layer, Phase 1 (deterministic).

Sits above x402: x402 answers *how* an agent pays; AEGL answers *whether* it
should, how much, and under what authority. Phase 1 does that with rules,
budgets, scores and arithmetic only -- no model inference, so a decision costs
nothing and takes microseconds.

Nothing in this package may import an LLM client. `tests/test_no_llm.py` enforces
that, because the moment a governance layer needs a model to authorize a payment,
its cost and latency guarantees are gone.
"""

from .engines.evidence.audit import AuditLog
from .governor import Governor
from .record import AEGS_VERSION
from .clock import FixedClock, SystemClock
from .config import PolicyBundle, load_bundle
from .engines.evidence.escalation import ReviewItem, ReviewQueue
from .runtime import Aegoll, Paths
from .store import HistorySnapshot, Store
from .domain import (
    Decision,
    PaymentRequest,
    Purpose,
    Tier,
    Vendor,
    Verdict,
    atomic_to_usd,
    usd_to_atomic,
)

__all__ = [
    "Aegoll",
    "AuditLog",
    "Decision",
    "FixedClock",
    "AEGS_VERSION",
    "Governor",
    "HistorySnapshot",
    "PaymentRequest",
    "Paths",
    "PolicyBundle",
    "Purpose",
    "ReviewItem",
    "ReviewQueue",
    "Store",
    "SystemClock",
    "Tier",
    "Vendor",
    "Verdict",
    "atomic_to_usd",
    "load_bundle",
    "usd_to_atomic",
]

__version__ = "0.1.1"
