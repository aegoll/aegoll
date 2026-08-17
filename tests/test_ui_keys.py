"""The BYOK panel must never leak a key.

These are security tests, so they render for real against a recording stub and then
search **everything** that reached the page for the secret. A panel that is careful
in review and careless in one branch is not careful.

The threat model is narrow and real: this panel runs on pages with no login, holding
keys that can spend money. The properties asserted here are the controls.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

import pytest

SECRET = "gsk_ThisIsAVerySecretKeyValue0123456789abcdef"


@dataclass
class Recorder:
    """Absorbs Streamlit calls and remembers every string shown to the user."""

    shown: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    successes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    buttons: dict[str, bool] = field(default_factory=dict)
    text_inputs: dict[str, str] = field(default_factory=dict)
    checkboxes: dict[str, bool] = field(default_factory=dict)
    reran: bool = False
    session_state: dict[str, Any] = field(default_factory=dict)

    # --- sinks ---
    def markdown(self, body: str = "", **kw: Any) -> None:
        self.shown.append(str(body))

    def caption(self, body: str = "", **kw: Any) -> None:
        self.shown.append(str(body))

    def write(self, body: Any = "", **kw: Any) -> None:
        self.shown.append(str(body))

    def error(self, body: str = "", **kw: Any) -> None:
        self.errors.append(str(body))
        self.shown.append(str(body))

    def success(self, body: str = "", **kw: Any) -> None:
        self.successes.append(str(body))
        self.shown.append(str(body))

    def warning(self, body: str = "", **kw: Any) -> None:
        self.warnings.append(str(body))
        self.shown.append(str(body))

    def divider(self, **kw: Any) -> None:
        pass

    def rerun(self, **kw: Any) -> None:
        self.reran = True

    # --- widgets: values come from the dicts above ---
    def text_input(self, label: str = "", value: str = "", key: str = "", **kw: Any) -> str:
        # `placeholder` is a real leak vector: it is rendered on the page.
        if kw.get("placeholder"):
            self.shown.append(str(kw["placeholder"]))
        self.shown.append(str(label))
        return self.text_inputs.get(key, value)

    def checkbox(self, label: str = "", key: str = "", **kw: Any) -> bool:
        self.shown.append(str(label))
        return self.checkboxes.get(key, False)

    def button(self, label: str = "", key: str = "", **kw: Any) -> bool:
        self.shown.append(str(label))
        return self.buttons.get(key, False)

    def columns(self, spec: Any, **kw: Any) -> list["Recorder"]:
        n = spec if isinstance(spec, int) else len(spec)
        return [self] * n

    def expander(self, label: str = "", **kw: Any) -> "Recorder":
        self.shown.append(str(label))
        return self

    def spinner(self, text: str = "", **kw: Any) -> "Recorder":
        self.shown.append(str(text))
        return self

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    @property
    def sidebar(self) -> "Recorder":
        return self

    @property
    def blob(self) -> str:
        return "\n".join(self.shown + self.errors + self.successes + self.warnings)


@pytest.fixture
def st(monkeypatch):
    rec = Recorder()
    rec.session_state = {}
    monkeypatch.setitem(sys.modules, "streamlit", rec)
    return rec


@pytest.fixture
def ui_keys(st, monkeypatch):
    import importlib

    module = importlib.import_module("aegoll.ui_keys")
    module = importlib.reload(module)
    from aegoll.advisors import keys as keymod

    # Runtime keys are process-global; keep each test's writes out of the others.
    monkeypatch.setattr(keymod, "_runtime", {}, raising=False)

    # And the environment, which is the subtler half. `persist_to_env` sets
    # `os.environ[var]` on purpose, so the key takes effect without a restart --
    # which means the save-to-.env test would otherwise leave the test secret
    # resolvable for every test after it. Handing the variables to monkeypatch
    # makes it restore them on teardown.
    for var in ("GROQ_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
                "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    return module


CATALOGUE = [
    {"provider": "groq", "keyPresent": False, "models": ["llama-3.3-70b-versatile"]},
]


# --- the key never reaches the page ---------------------------------------


def test_a_loaded_key_is_never_rendered(ui_keys, st, tmp_path):
    """The core property. Only `masked()` output may reach the screen."""
    st.text_inputs["aegoll_byok_input_groq"] = SECRET
    st.buttons["aegoll_byok_use_groq"] = True

    ui_keys.render(CATALOGUE, env_path=tmp_path / ".env")

    assert SECRET not in st.blob, "the raw key was rendered to the page"
    # Not even a long fragment of it.
    assert SECRET[:20] not in st.blob


def test_a_rejected_key_is_not_echoed_back(ui_keys, st, tmp_path):
    """Validation messages are where a secret most easily slips out."""
    # A distinctive value, so a hit means an echo rather than a coincidence --
    # the rejection message legitimately contains the word "shorter".
    bad = "zq7"
    st.text_inputs["aegoll_byok_input_groq"] = bad
    st.buttons["aegoll_byok_use_groq"] = True

    ui_keys.render(CATALOGUE, env_path=tmp_path / ".env")

    assert st.errors, "an implausible key was accepted without complaint"
    assert bad not in st.blob


def test_a_rejected_key_is_not_left_half_loaded(ui_keys, st, tmp_path):
    """Rejecting must clear, not merely decline to confirm."""
    from aegoll.advisors import keys as keymod

    st.text_inputs["aegoll_byok_input_groq"] = "zq7"
    st.buttons["aegoll_byok_use_groq"] = True

    ui_keys.render(CATALOGUE, env_path=tmp_path / ".env")

    assert keymod.resolve_key("groq") != "zq7"
    assert "groq" not in (st.session_state.get(ui_keys.SESSION_SLOT) or {})


def test_an_accepted_key_is_held_in_memory_only_by_default(ui_keys, st, tmp_path):
    env = tmp_path / ".env"
    st.text_inputs["aegoll_byok_input_groq"] = SECRET
    st.buttons["aegoll_byok_use_groq"] = True
    # Save box deliberately left unticked.

    ui_keys.render(CATALOGUE, env_path=env)

    assert not env.exists(), "a key was persisted without the user asking"
    assert any("this session" in s for s in st.successes)


def test_saving_to_env_is_explicit(ui_keys, st, tmp_path):
    env = tmp_path / ".env"
    env.write_text("EXISTING=1\n", encoding="utf-8")
    st.text_inputs["aegoll_byok_input_groq"] = SECRET
    st.buttons["aegoll_byok_use_groq"] = True
    st.checkboxes["aegoll_byok_persist_groq"] = True

    ui_keys.render(CATALOGUE, env_path=env)

    body = env.read_text(encoding="utf-8")
    assert SECRET in body, "the user asked to save and it was not saved"
    assert "EXISTING=1" in body, "persisting clobbered the rest of the file"


def test_a_key_containing_a_newline_cannot_write_extra_variables(ui_keys, st, tmp_path):
    """A newline in the input would otherwise inject arbitrary .env lines."""
    env = tmp_path / ".env"
    injected = "gsk_" + "a" * 40 + "\nSELLER_ADDRESS=0xattacker"
    st.text_inputs["aegoll_byok_input_groq"] = injected
    st.buttons["aegoll_byok_use_groq"] = True
    st.checkboxes["aegoll_byok_persist_groq"] = True

    ui_keys.render(CATALOGUE, env_path=env)

    body = env.read_text(encoding="utf-8") if env.exists() else ""
    assert "SELLER_ADDRESS=0xattacker" not in body


def test_the_no_login_warning_is_on_screen(ui_keys, st, tmp_path):
    """A security property documented only in a docstring is not a control."""
    ui_keys.render(CATALOGUE, env_path=tmp_path / ".env")

    joined = "\n".join(st.warnings)
    assert "no login" in joined
    assert "127.0.0.1" in joined


def test_an_existing_key_is_shown_masked(ui_keys, st, tmp_path, monkeypatch):
    from aegoll.advisors import keys as keymod

    keymod.set_runtime_key("groq", SECRET)
    catalogue = [{"provider": "groq", "keyPresent": True, "models": ["m"]}]

    ui_keys.render(catalogue, env_path=tmp_path / ".env")

    assert SECRET not in st.blob
    assert "•" in st.blob, "no masked form was shown for a key that is present"


def test_forgetting_a_key_clears_it(ui_keys, st, tmp_path):
    from aegoll.advisors import keys as keymod

    keymod.set_runtime_key("groq", SECRET)
    st.session_state[ui_keys.SESSION_SLOT] = {"groq": SECRET}
    st.buttons["aegoll_byok_forget_groq"] = True
    catalogue = [{"provider": "groq", "keyPresent": True, "models": ["m"]}]

    ui_keys.render(catalogue, env_path=tmp_path / ".env")

    # The session key is gone. `resolve_key` may still find one in the
    # environment, and must: forgetting a key typed into the browser is not a
    # request to delete the operator's `.env`.
    assert keymod.resolve_key("groq") != SECRET
    assert "groq" not in st.session_state[ui_keys.SESSION_SLOT]


def test_session_keys_are_reapplied_on_rerun(ui_keys, st):
    """Streamlit reruns the script per interaction; without this, keys evaporate."""
    from aegoll.advisors import keys as keymod

    st.session_state[ui_keys.SESSION_SLOT] = {"groq": SECRET}
    ui_keys.apply_session_keys()
    assert keymod.resolve_key("groq") == SECRET


def test_no_provider_catalogue_renders_nothing(ui_keys, st, tmp_path):
    ui_keys.render([], env_path=tmp_path / ".env")
    assert not st.shown


# --- the structural claim -------------------------------------------------


def test_the_panel_imports_no_agent_and_no_framework():
    """One key panel, four hosts, and it must not know which one it is in."""
    import ast

    from conftest import module_source

    banned = {
        "langgraph", "langchain_core", "google.adk", "x402_agent",
        "langgraph_x402", "adk_x402", "x402_core", "cockpit_kit",
    }
    source = module_source("ui_keys.py")
    offenders = []
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            if name.split(".")[0] in {b.split(".")[0] for b in banned}:
                offenders.append(f"ui_keys.py:{node.lineno} imports {name}")

    assert not offenders, "the key panel is host-specific:\n  " + "\n  ".join(offenders)
