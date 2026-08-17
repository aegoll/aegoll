"""The drop-in panel, on its own, with no agent and no framework.

```powershell
cd D:\\learning-poc\\x402\\aegoll
.venv\\Scripts\\streamlit run aegoll\\ui_demo.py --server.port 8505 --server.address 127.0.0.1
```

This is what "the UI is a plugin too" means in practice: a governance panel that
needs a `report()` dict and nothing else. The whole integration is the last two
lines. Free to run -- the deterministic engines never call a model, and the
purchases below are simulated against a fake payment client.

`agents/cockpit_kit` does the same two lines in C1; this exists so the claim can
be checked without starting an agent.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import streamlit as st

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aegoll import ui as aegoll_ui  # noqa: E402
from aegoll.plugin import Governor  # noqa: E402


class DemoQuote:
    def __init__(self, price: str) -> None:
        self.price_usd = price


class DemoCall:
    payment_status = "settled"
    transaction = "0xdemo"


class DemoBuyer:
    """A payment client that satisfies `PaymentClient` and signs nothing."""

    address = "0xDEMO"
    spend_cap_usd = 1.0
    total_spent_usd = 0.0

    def __init__(self, price: str = "0.001") -> None:
        self.price = price
        self.calls: list[str] = []

    async def quote(self, path: str) -> DemoQuote:
        return DemoQuote(self.price)

    async def get_free(self, path: str) -> dict:
        return {}

    async def get_paid(self, path: str) -> DemoCall:
        self.calls.append(path)
        return DemoCall()

    def budget_snapshot(self) -> dict:
        return {}

    async def aclose(self) -> None:
        return None


st.set_page_config(page_title="AEGL panel", layout="wide")
st.title("AEGL — the drop-in governance panel")
st.caption(
    "No agent, no framework, no model. A `Governor`, a fake payment client, and "
    "one call to `aegoll.ui.render()`."
)

with st.sidebar:
    st.header("Simulate a run")
    policy = st.selectbox("Policy bundle", ["default", "strict"])
    budget = st.number_input(
        "Token budget (USD)", value=0.03, min_value=0.0, max_value=1.0,
        step=0.01, format="%.4f",
        help="Above the bundle's per-transaction envelope ($0.04 in default) the "
             "run is refused before it starts.",
    )
    spent = st.number_input(
        "Pretend the run has spent (USD)", value=0.01, min_value=0.0,
        max_value=1.0, step=0.01, format="%.4f",
        help="Above the authorized budget, the mid-run ceiling stops it.",
    )
    price = st.text_input("Price per purchase (USD)", value="0.001")
    purchases = st.slider("Simulated purchases", 0, 4, 2)
    go = st.button("Run", type="primary", width="stretch")

if not go:
    st.info("Set up a run in the sidebar and press **Run**.")
    st.stop()


async def simulate() -> dict:
    # advisor=None keeps the demo free and offline.
    gov = Governor(policy=policy, advisor=None)
    try:
        gov.authorize_run(model="demo-model", provider="openai", budget_usd=budget)
        buyer = gov.wrap(DemoBuyer(price))
        for i in range(purchases):
            try:
                await buyer.get_paid(f"/market/snapshot?n={i}")
            except Exception:  # noqa: BLE001 - a refusal is a result, not a crash
                pass
        gov.check_spend(spent)
        gov.settle_run(spent)
        return gov.report()
    finally:
        gov.close()


report = asyncio.run(simulate())

# --- the entire integration -----------------------------------------------
aegoll_ui.render(report)

with st.expander("The payload behind this panel"):
    st.caption(
        "`Governor.report()` is plain JSON-safe data. The panel reads it and "
        "nothing else, which is why the same panel serves every cockpit."
    )
    st.json(report)
