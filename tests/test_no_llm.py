"""The deterministic core must not be able to invoke a model.

Phase 2 adds real model clients, so this is no longer "nothing in the package may
import an SDK". The boundary moved rather than disappeared: inference is confined
to `advisors/`, and everything that *decides* stays free and fast. If a decision
engine could call a model, the layer would lose the cost and latency guarantees
that are its whole argument.

`eiap.py` is allowed to compute whether a model would be worth calling; it is
still not allowed to call one.
"""

from __future__ import annotations

import ast
import pkgutil
from pathlib import Path

import pytest

import tesoro

PACKAGE_ROOT = Path(tesoro.__file__).resolve().parent

FORBIDDEN_MODULES = {
    "anthropic",
    "claude_agent_sdk",
    "openai",
    "google.generativeai",
    "cohere",
    "mistralai",
    "ollama",
    "transformers",
    "torch",
}

# Two legitimate exceptions, both at the layer's edge rather than in its core:
#   * `adapters/` reaches into agent-py, which depends on the Agent SDK.
#   * `advisors/` is Phase 2 -- the on-demand reasoning component. It is the only
#     place a model client belongs, and it is only ever reached through the EIAP
#     gate in `advise.py`.
EXEMPT_FILES = {"adapters/x402_python.py"}
EXEMPT_DIRS = {"advisors"}


def _module_files() -> list[Path]:
    out = []
    for p in PACKAGE_ROOT.rglob("*.py"):
        rel = p.relative_to(PACKAGE_ROOT)
        if rel.as_posix() in EXEMPT_FILES:
            continue
        if rel.parts and rel.parts[0] in EXEMPT_DIRS:
            continue
        out.append(p)
    return out


def test_inference_is_confined_to_the_advisors_package():
    """The exemption must stay narrow: only `advisors/` may hold a client."""
    import ast

    offenders: list[str] = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        rel = path.relative_to(PACKAGE_ROOT).as_posix()
        if rel.startswith("advisors/") or rel in EXEMPT_FILES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".")[0] in FORBIDDEN_MODULES:
                    offenders.append(f"{rel}:{node.lineno} imports {name}")

    assert not offenders, (
        "a model client escaped `advisors/`:\n  " + "\n  ".join(offenders)
    )


def test_no_engine_imports_a_model_client():
    offenders: list[str] = []

    for path in _module_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(PACKAGE_ROOT).as_posix()
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                if root in FORBIDDEN_MODULES or name in FORBIDDEN_MODULES:
                    offenders.append(f"{rel}:{node.lineno} imports {name}")

    assert not offenders, (
        "Phase 1 must decide without inference. Offending imports:\n  "
        + "\n  ".join(offenders)
    )


def test_eiap_computes_but_does_not_invoke():
    """The EIAP may recommend a model; the decision must still report Tier.NONE."""
    from datetime import datetime, timezone

    from tesoro import Tesoro, FixedClock, Paths, Tier, Vendor

    a = Tesoro(
        paths=Paths.ephemeral(".data-test-eiap"),
        clock=FixedClock(datetime(2026, 8, 12, tzinfo=timezone.utc)),
    )
    try:
        # A large, unfamiliar-vendor request: the EIAP should want a model here.
        req = a.build_request(
            resource="/analytics/bespoke",
            amount_usd="5000",
            vendor=Vendor(id="brand-new", name="Brand New"),
        )
        decision = a.decide(req)

        assert decision.intelligence.eiap.would_invoke is True, (
            "EIAP should favour inference at $5,000 exposure"
        )
        assert decision.intelligence.required is Tier.NONE, (
            "Phase 1 reported a non-NONE intelligence tier -- it invoked something"
        )
        assert decision.verdict is not None
    finally:
        a.close()


def test_break_even_matches_the_documented_figure():
    """The $0.08-ish break-even claim in PLAN.md, asserted rather than asserted-about."""
    from tesoro.config import load_bundle

    cfg = load_bundle().eiap
    break_even_at_max_uncertainty = cfg.ai_cost_atomic / cfg.max_p_flip
    # $0.004 / 0.05 = $0.08
    assert 70_000 <= break_even_at_max_uncertainty <= 90_000, (
        f"break-even moved to ${break_even_at_max_uncertainty / 1e6:.4f}; "
        "PLAN.md documents ~$0.08 and needs updating if this is intentional"
    )


@pytest.mark.parametrize("amount", ["0.001", "0.005", "0.01"])
def test_live_seller_prices_never_justify_inference(amount):
    """Every price the real x402 seller charges is below break-even.

    This is the quantitative core of the deterministic-first argument.
    """
    from datetime import datetime, timezone

    from tesoro import Tesoro, FixedClock, Paths, Vendor

    a = Tesoro(
        paths=Paths.ephemeral(".data-test-prices"),
        clock=FixedClock(datetime(2026, 8, 12, tzinfo=timezone.utc)),
    )
    try:
        req = a.build_request(
            resource="/market/snapshot",
            amount_usd=amount,
            vendor=Vendor(id="x402-poc-desk", name="POC Desk"),
        )
        eiap = a.decide(req).intelligence.eiap
        assert eiap.would_invoke is False, (
            f"${amount} exposure was judged worth ${eiap.ai_cost_atomic / 1e6:.4f} of inference"
        )
    finally:
        a.close()
