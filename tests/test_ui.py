"""The panel and the payload must agree.

This exists because of a specific, easy, silent failure: the Claude cockpit's
original panel read `amount_usd` while `Governor.report()` emits `amountUsd`. A
panel reading the wrong dialect does not crash -- `dict.get` returns `None`, the
formatter turns that into `$0.000000`, and every figure renders as zero. Nothing
in a normal test suite notices, because nothing renders.

So these tests *do* render: a recording stub stands in for Streamlit, the real
`render()` runs against a real `Governor.report()`, and the recorded output is
asserted to contain the actual numbers. If the two dialects ever diverge again,
the figures go to zero and these fail.

The stub is not a Streamlit mock in the general sense -- it only needs to absorb
the calls this panel makes and remember what it was shown.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

import pytest


# --- the recording stub ---------------------------------------------------


@dataclass
class Recorder:
    """Absorbs Streamlit calls and remembers the text and metrics it was given."""

    text: list[str] = field(default_factory=list)
    metrics: list[tuple[str, Any, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)
    tables: list[Any] = field(default_factory=list)
    progress: list[float] = field(default_factory=list)

    # every text-ish sink records into `text` as well, so assertions can just
    # search one place for a string
    def markdown(self, body: str = "", **kw: Any) -> None:
        self.text.append(str(body))

    def caption(self, body: str = "", **kw: Any) -> None:
        self.text.append(str(body))

    def write(self, body: Any = "", **kw: Any) -> None:
        self.text.append(str(body))

    def error(self, body: str = "", **kw: Any) -> None:
        self.errors.append(str(body))
        self.text.append(str(body))

    def warning(self, body: str = "", **kw: Any) -> None:
        self.warnings.append(str(body))
        self.text.append(str(body))

    def info(self, body: str = "", **kw: Any) -> None:
        self.infos.append(str(body))
        self.text.append(str(body))

    def metric(self, label: str, value: Any = None, delta: Any = None, **kw: Any) -> None:
        self.metrics.append((label, value, delta))
        self.text.append(f"{label}={value} ({delta})")

    def dataframe(self, data: Any = None, **kw: Any) -> None:
        self.tables.append(data)

    def progress(self, value: float = 0.0, **kw: Any) -> None:  # type: ignore[override]
        pass

    def columns(self, spec: Any, **kw: Any) -> list["Recorder"]:
        n = spec if isinstance(spec, int) else len(spec)
        return [self] * n

    def container(self, **kw: Any) -> "Recorder":
        return self

    def expander(self, label: str = "", **kw: Any) -> "Recorder":
        self.text.append(str(label))
        return self

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def metric_value(self, label: str) -> Any:
        for name, value, _ in self.metrics:
            if name == label:
                return value
        raise AssertionError(f"the panel never rendered a {label!r} metric")

    @property
    def blob(self) -> str:
        return "\n".join(self.text)


@pytest.fixture
def st(monkeypatch):
    """Install the recorder as `streamlit` before `aegoll.ui` imports it."""
    rec = Recorder()
    # `progress` collides with the dataclass field name, so bind it here.
    monkeypatch.setattr(Recorder, "progress",
                        lambda self, value=0.0, **kw: self.__dict__.setdefault("_p", []).append(value),
                        raising=False)
    monkeypatch.setitem(sys.modules, "streamlit", rec)
    for name in [m for m in sys.modules if m.startswith("aegoll.ui")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    return rec


@pytest.fixture
def ui(st):
    import importlib

    module = importlib.import_module("aegoll.ui")
    return importlib.reload(module)


# --- fixtures that produce real payloads ----------------------------------


@pytest.fixture
def governor(tmp_path):
    from aegoll.plugin import Governor

    g = Governor(advisor=None, data_dir=tmp_path)
    yield g
    g.close()


class FakeQuote:
    price_usd = "0.001"


class FakeCall:
    payment_status = "settled"
    transaction = "0xabc"


class FakeBuyer:
    address = "0xFAKE"
    spend_cap_usd = 1.0
    total_spent_usd = 0.0

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def quote(self, path: str):
        return FakeQuote()

    async def get_free(self, path: str):
        return {}

    async def get_paid(self, path: str):
        return FakeCall()

    def budget_snapshot(self) -> dict:
        return {}

    async def aclose(self) -> None:
        return None


# --- the panel agrees with the payload ------------------------------------


def test_the_real_figures_reach_the_panel(ui, st, governor):
    """The dialect test. If `report()` and the panel disagree, these go to zero."""
    governor.authorize_run(model="gpt-4o-mini", provider="openai", budget_usd=0.04)

    ui.render(governor.report())

    assert st.metric_value("Authorized budget") == "$0.040000"
    assert st.metric_value("Model") == "gpt-4o-mini"
    assert st.metric_value("Decisions") == 1
    assert st.metric_value("Internal (tokens)") == 1
    # The decision's own amount, which is the field that was previously misread.
    assert "$0.040000" in st.blob
    assert "llm:gpt-4o-mini" in st.blob


def test_no_figure_silently_renders_as_zero(ui, st, governor):
    """A stricter form of the same check, aimed at the actual failure mode.

    Reading the wrong key does not raise -- it yields `None`, which formats as
    `$0.000000`. So assert the amount that reached the panel is the amount that
    was authorized, not merely that something was rendered.
    """
    governor.authorize_run(model="m", budget_usd=0.03)

    ui.render(governor.report())

    amounts = [v for label, v, _ in st.metrics if label == "Amount"]
    assert amounts, "no decision card was rendered"
    assert amounts[0] == "$0.030000", f"amount rendered as {amounts[0]} instead of $0.030000"


def test_the_ceiling_stop_is_the_first_thing_reported(ui, st, governor):
    """On LangGraph and the ADK this is the guard the framework lacks."""
    governor.authorize_run(model="m", budget_usd=0.02)
    governor.check_spend(0.05)

    ui.render(governor.report())

    assert any("AEGL stopped this run" in e for e in st.errors)
    assert st.metric_value("Spent") == "$0.050000"
    assert "250%" in st.blob  # $0.05 spent against a $0.02 ceiling


def test_a_completed_run_is_not_reported_as_stopped(ui, st, governor):
    governor.authorize_run(model="m", budget_usd=0.04)
    governor.check_spend(0.01)

    ui.render(governor.report())

    assert not any("AEGL stopped" in e for e in st.errors)
    assert st.metric_value("Stopped by AEGL") == "no"


def test_a_refused_decision_names_the_engine_and_the_rule(ui, st, governor):
    # $0.10 breaches the default bundle's $0.04 per-transaction envelope.
    governor.authorize_run(model="m", budget_usd=0.10)

    ui.render(governor.report())

    blob = st.blob
    assert "treasury" in blob
    assert "internal-reject-over-budget" in blob
    assert any("stopped this" in e for e in st.errors)


def test_an_external_purchase_is_shown_on_its_own_channel(ui, st, governor):
    import asyncio

    wrapped = governor.wrap(FakeBuyer())
    asyncio.run(wrapped.get_paid("/market/snapshot"))

    ui.render(governor.report())

    assert st.metric_value("External (x402)") == 1
    assert "/market/snapshot" in st.blob


def test_an_ungoverned_run_says_so_instead_of_rendering_empty(ui, st):
    ui.render(None)
    assert any("was not governed" in i for i in st.infos)
    assert not st.metrics


def test_both_channels_get_an_envelope_panel(ui, st, governor):
    ui.render(governor.report())
    blob = st.blob
    assert "Internal — LLM tokens" in blob
    assert "External — data via x402" in blob


def test_a_broken_audit_chain_is_reported_loudly(ui, st, governor):
    governor.authorize_run(model="m", budget_usd=0.02)
    report = governor.report()
    report["summary"]["auditOk"] = False
    report["summary"]["auditProblems"] = ["entry 3 hash mismatch"]

    ui.render(report)

    assert any("audit chain does not verify" in e for e in st.errors)
    assert "entry 3 hash mismatch" in st.blob


def test_a_not_recommended_advisor_is_flagged(ui, st, governor):
    """D2 measured `llama-3.1-8b-instant` as unusable; the UI must say so."""
    report = governor.report()
    report["advisor"] = {
        "provider": "groq", "model": "llama-3.1-8b-instant",
        "error": None, "warning": "blocked all 14 evaluation cases",
    }

    ui.render(report)

    assert any("not recommended" in w for w in st.warnings)


def test_an_advisor_that_could_not_run_is_explained(ui, st, governor):
    report = governor.report()
    report["advisor"] = {
        "provider": "groq", "model": "x", "error": "no API key configured",
        "warning": None,
    }

    ui.render(report)

    assert any("could not be used" in i for i in st.infos)
    assert any("not its ability to transact" in i for i in st.infos)


def test_advice_is_rendered_when_the_gate_opened(ui, st, governor):
    """Synthesised advice, so no model is called and nothing is spent."""
    governor.authorize_run(model="m", budget_usd=0.02)
    report = governor.report()
    report["events"][0]["advice"] = {
        "provider": "gemini", "model": "gemini-flash-lite-latest",
        "recommendation": "REVIEW", "confidence": 0.81,
        "rationale": "vendor repriced this endpoint 25x",
        "concerns": ["silent reprice"], "costUsd": 0.000112,
        "latencyMs": 1512, "inputTokens": 582, "outputTokens": 149,
        "injectionSuspected": False, "error": None,
    }
    report["events"][0]["advisorChanged"] = True

    ui.render(report)

    blob = st.blob
    assert "Advisor tightened this verdict" in blob
    assert "gemini/gemini-flash-lite-latest" in blob
    assert "vendor repriced this endpoint 25x" in blob
    assert st.metric_value("Advice cost") == "$0.000112"
    assert st.metric_value("Tokens") == "582/149"


def test_injection_detection_is_impossible_to_miss(ui, st, governor):
    governor.authorize_run(model="m", budget_usd=0.02)
    report = governor.report()
    report["events"][0]["advice"] = {
        "provider": "groq", "model": "m", "recommendation": "REJECT",
        "confidence": 0.99, "rationale": "the description addressed me directly",
        "concerns": [], "costUsd": 0.0, "latencyMs": 0,
        "injectionSuspected": True, "error": None,
    }

    ui.render(report)

    assert any("Prompt injection detected" in e for e in st.errors)


def test_a_skipped_advisor_explains_the_economics(ui, st, governor):
    governor.authorize_run(model="m", budget_usd=0.02)
    report = governor.report()
    report["events"][0]["advisorSkipReason"] = (
        "exposure $0.001000 is below the $0.008600 break-even"
    )

    ui.render(report)

    assert "no advisor consulted" in st.blob
    assert "break-even" in st.blob


# --- the structural claim -------------------------------------------------


def test_the_panel_imports_no_agent_and_no_framework():
    """One panel, four cockpits, and it must not know which one it is in."""
    import ast

    from conftest import module_source

    banned = {
        "langgraph", "langchain_core", "langchain_openai", "google.adk",
        "x402_agent", "langgraph_x402", "adk_x402", "x402_core",
        "openai", "anthropic", "groq",
    }
    source = module_source("ui.py")
    offenders = []
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            if name.split(".")[0] in {b.split(".")[0] for b in banned}:
                offenders.append(f"ui.py:{node.lineno} imports {name}")

    assert not offenders, "the panel is host-specific:\n  " + "\n  ".join(offenders)


def test_the_panel_never_touches_a_governor():
    """It renders a dict. Taking a `Governor` would couple every host to AEGL."""
    import ast

    from conftest import module_source

    source = module_source("ui.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "plugin" not in node.module, "the panel imported the plugin"


# --- a cap is not an empty budget -----------------------------------------


def test_a_per_call_cap_is_not_rendered_as_an_unspent_budget(ui, st, governor):
    """`per_transaction` never accumulates, so "0.000000 used" is misleading.

    Its `used` is hardcoded to zero in `treasury.evaluate` -- it is a ceiling
    checked fresh against each request, not a running total. Rendered in the same
    "used of limit" shape as `daily` or `monthly`, it reads as "nothing has been
    spent", which is the opposite of what the row means and has confused a reader
    in practice.
    """
    governor.authorize_run(model="m", budget_usd=0.02)

    ui.render(governor.report())

    blob = st.blob
    assert "per call" in blob, "a per-call cap was not distinguished from a window"
    # The cumulative windows keep their normal treatment.
    assert "daily" in blob


def test_the_envelope_table_marks_caps_and_leaves_windows_alone(ui, st):
    rows = ui._envelope_rows(
        [
            {"name": "per_transaction", "window": "per call", "limitUsd": 15.0,
             "usedUsd": 0.0, "headroomUsd": 15.0, "cumulative": False},
            {"name": "daily", "window": "today", "limitUsd": 50.0,
             "usedUsd": 0.035, "headroomUsd": 49.965, "cumulative": True},
        ],
        binding=None,
    )
    assert rows[0]["used"] == "— per call, not a total"
    assert rows[1]["used"] == "$0.035000"


def test_an_envelope_without_the_flag_is_treated_as_cumulative(ui, st):
    """Older payloads predate the flag; they must not all become caps."""
    rows = ui._envelope_rows(
        [{"name": "daily", "limitUsd": 50.0, "usedUsd": 0.035, "headroomUsd": 49.9}],
        binding=None,
    )
    assert rows[0]["used"] == "$0.035000"
