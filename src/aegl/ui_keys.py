"""BYOK key entry, as a plugin.

```python
from aegl import ui_keys

ui_keys.apply_session_keys()   # first, before anything reads a key
ui_keys.render()               # the sidebar panel
```

Ships with AEGL for the same reason the decision panel does: a governance layer
whose advisor needs a key, but which cannot ask for one without per-host UI work,
is not really portable. This used to live in the Claude cockpit, where the other
three hosts could not reach it.

## Security properties, and why they are here rather than in a docstring

Keys typed here are held **in memory only** by default, for the life of the process.
Saving to `.env` is a separate, explicit action.

* **Never render a key.** Only `keys.masked()` output reaches the page, so a key
  cannot be recovered from the screen or a screenshot.
* **Never log one.** No raw value is written anywhere, and provider error text is
  truncated before display.
* **Never persist silently.** A key entered here disappears on restart unless the
  user ticks the save box.
* **Never widen trust.** `keys.persist_to_env()` refuses a key containing a
  newline, which would otherwise let one input write arbitrary extra variables.

> **These pages have no authentication.** Anyone who can reach the port can use
> whatever keys are loaded, and spend against them. Serve on `127.0.0.1` — every
> launch command in the runbook does. The panel repeats this **on screen**, because
> a security property that lives only in a docstring is not a control.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from .advisors import keys as keymod
from .advisors import providers as advisor_providers
from .advisors import test_key
from .config import REPO_ROOT

DEFAULT_ENV_PATH = REPO_ROOT / ".env"

SIGNUP_URLS = {
    "groq": "https://console.groq.com/keys",
    "openai": "https://platform.openai.com/api-keys",
    "gemini": "https://aistudio.google.com/apikey",
    "anthropic": "https://console.anthropic.com/settings/keys",
}

SOURCE_LABEL = {
    "runtime": "🔑 entered here (memory only)",
    "env": "📄 from .env",
    "none": "⬜ not set",
}

#: Streamlit session key. Named for the host-agnostic panel, not any one cockpit.
SESSION_SLOT = "aegl_byok_keys"


def apply_session_keys() -> None:
    """Push keys held in this browser session into the process key store.

    Must be called on every rerun, before anything reads a key. Streamlit reruns
    the whole script per interaction, so without this the store would only reflect
    whichever rerun last touched the form.
    """
    for provider, key in (st.session_state.get(SESSION_SLOT) or {}).items():
        keymod.set_runtime_key(provider, key)


def render(
    catalogue: list[dict[str, Any]] | None = None,
    *,
    env_path: Path | None = None,
    container: Any | None = None,
) -> None:
    """The BYOK panel. Renders into the sidebar unless `container` is given.

    `catalogue` defaults to AEGL's own provider list, so a host needs no arguments.
    """
    if catalogue is None:
        catalogue = [p.as_dict() for p in advisor_providers()]
    if not catalogue:
        return

    env_path = env_path or DEFAULT_ENV_PATH
    host = container if container is not None else st.sidebar
    st.session_state.setdefault(SESSION_SLOT, {})
    ready = [p for p in catalogue if p["keyPresent"]]

    with host.expander(
        f"BYOK keys — {len(ready)}/{len(catalogue)} configured", expanded=not ready
    ):
        st.caption(
            "Paste a key to use it immediately. Held **in memory only** unless you "
            "tick *Save to .env* — so by default it disappears when this app stops."
        )
        st.warning(
            "This page has no login. Only enter keys if it is served on "
            "`127.0.0.1`; on `0.0.0.0` anyone who can reach the port can spend "
            "against them.",
            icon="⚠️",
        )

        for provider in catalogue:
            _provider_row(provider, env_path)


def _provider_row(provider: dict[str, Any], env_path: Path) -> None:
    name = provider["provider"]
    status = keymod.key_status(name)

    st.markdown(f"**{name}** — {SOURCE_LABEL.get(status.source, status.source)}")
    if status.present:
        # `masked` is the only path a key value takes to the screen.
        st.caption(f"`{status.masked}` · variable `{status.env_var}`")
    else:
        url = SIGNUP_URLS.get(name)
        st.caption(
            f"variable `{status.env_var}`" + (f" · [get a key]({url})" if url else "")
        )

    entered = st.text_input(
        f"{name} API key",
        value="",
        type="password",
        key=f"aegl_byok_input_{name}",
        placeholder=status.editable_hint or "paste key",
        label_visibility="collapsed",
    )

    cols = st.columns([1, 1, 1])
    save_to_env = cols[2].checkbox(
        "Save to .env", key=f"aegl_byok_persist_{name}", help=str(env_path)
    )

    if cols[0].button("Use", key=f"aegl_byok_use_{name}", disabled=not entered):
        _accept(name, entered, save_to_env, env_path)

    if cols[1].button("Test", key=f"aegl_byok_test_{name}", disabled=not status.present):
        _run_test(name, provider["models"])

    if status.source == "runtime" and st.button(
        f"Forget {name} key", key=f"aegl_byok_forget_{name}"
    ):
        st.session_state[SESSION_SLOT].pop(name, None)
        keymod.clear_runtime_key(name)
        st.rerun()

    st.divider()


def _accept(name: str, entered: str, save_to_env: bool, env_path: Path) -> None:
    """Validate, then load. A rejected key is removed rather than left half-set."""
    plausible, why = keymod.looks_plausible(name, entered)
    if not plausible:
        # Never echo the key back, not even a fragment of it.
        st.error(f"Rejected: {why}")
        st.session_state[SESSION_SLOT].pop(name, None)
        keymod.clear_runtime_key(name)
        return

    st.session_state[SESSION_SLOT][name] = entered
    keymod.set_runtime_key(name, entered)
    if why != "looks plausible":
        st.warning(why)

    if save_to_env:
        ok, detail = keymod.persist_to_env(name, entered, env_path)
        (st.success if ok else st.error)(detail)
    else:
        st.success("Key loaded for this session.")
    st.rerun()


def _run_test(provider: str, models: list[str]) -> None:
    """Prove a key works, with the cheapest call the provider allows."""
    model = models[0] if models else ""
    with st.spinner(f"testing {provider}/{model}…"):
        result = test_key(provider, model)
    if result.ok:
        st.success(f"{result.detail} ({result.latency_ms:.0f} ms)")
    else:
        st.error(result.detail)
