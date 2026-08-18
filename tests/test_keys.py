"""BYOK key handling. Security-relevant, so the invariants are asserted.

The property that matters most: a key must never be renderable. Everything else
follows from keeping the only display path through `masked()`.
"""

from __future__ import annotations

import pytest

from tesoro.advisors import keys as keymod

SAMPLE = "sk-test-abcdefghijklmnopqrstuvwxyz0123456789"


@pytest.fixture(autouse=True)
def _clean():
    for provider in keymod.ENV_KEYS:
        keymod.clear_runtime_key(provider)
    yield
    for provider in keymod.ENV_KEYS:
        keymod.clear_runtime_key(provider)


# --- masking --------------------------------------------------------------


def test_masked_never_leaks_the_key():
    out = keymod.masked(SAMPLE)
    assert SAMPLE not in out
    assert SAMPLE[:-4] not in out
    assert out.endswith(f"{SAMPLE[-4:]} ({len(SAMPLE)} chars)")


def test_masked_handles_empty_and_short():
    assert keymod.masked(None) == "—"
    assert keymod.masked("") == "—"
    assert "abc" not in keymod.masked("abc")


# --- precedence -----------------------------------------------------------


def test_runtime_key_beats_env(monkeypatch):
    """A key typed in the UI must override a stale one in .env."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env-000000000000000000")
    assert keymod.key_status("openai").source == "env"

    keymod.set_runtime_key("openai", SAMPLE)
    assert keymod.resolve_key("openai") == SAMPLE
    assert keymod.key_status("openai").source == "runtime"

    keymod.clear_runtime_key("openai")
    assert keymod.key_status("openai").source == "env"


def test_gemini_accepts_either_env_name(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", SAMPLE)
    status = keymod.key_status("gemini")
    assert status.present and status.env_var == "GOOGLE_API_KEY"


def test_empty_runtime_key_clears_rather_than_setting(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    keymod.set_runtime_key("groq", SAMPLE)
    keymod.set_runtime_key("groq", "   ")
    assert keymod.resolve_key("groq") == ""


# --- shape checks ---------------------------------------------------------


@pytest.mark.parametrize(
    "key,expected",
    [
        ("", False),
        ("short", False),
        ("sk-with space-aaaaaaaaaaaaaaaaaaa", False),
        (SAMPLE, True),
    ],
)
def test_plausibility_rejects_only_the_clearly_broken(key, expected):
    ok, why = keymod.looks_plausible("openai", key)
    assert ok is expected
    assert why


def test_unusual_prefix_warns_but_is_accepted():
    """Prefixes change. A wrong-looking key must still be usable."""
    ok, why = keymod.looks_plausible("groq", "totally-different-but-long-enough-key")
    assert ok is True
    assert "gsk_" in why


# --- persistence ----------------------------------------------------------


def test_persist_writes_replaces_and_backs_up(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "OTHER=keep-me\nOPENAI_API_KEY=old-value\nTRAILING=also-keep\n", encoding="utf-8"
    )
    # setenv rather than delenv so monkeypatch owns the variable and restores it:
    # persist_to_env writes to os.environ, which would otherwise leak into later
    # tests and give them a key they never set.
    monkeypatch.setenv("OPENAI_API_KEY", "pre-existing")

    ok, detail = keymod.persist_to_env("openai", SAMPLE, env)
    assert ok, detail

    body = env.read_text(encoding="utf-8")
    assert f"OPENAI_API_KEY={SAMPLE}" in body
    assert "old-value" not in body
    assert "OTHER=keep-me" in body, "persisting clobbered an unrelated variable"
    assert "TRAILING=also-keep" in body
    assert body.count("OPENAI_API_KEY=") == 1, "left a duplicate definition"

    backup = env.with_suffix(env.suffix + ".bak-byok")
    assert backup.exists() and "old-value" in backup.read_text(encoding="utf-8")

    # Takes effect in this process immediately, so no restart is needed.
    import os

    assert os.environ["OPENAI_API_KEY"] == SAMPLE


def test_persist_appends_when_absent(tmp_path):
    env = tmp_path / ".env"
    env.write_text("EXISTING=1\n", encoding="utf-8")
    ok, _ = keymod.persist_to_env("groq", SAMPLE, env)
    assert ok
    assert f"GROQ_API_KEY={SAMPLE}" in env.read_text(encoding="utf-8")


def test_persist_refuses_a_key_containing_a_newline(tmp_path):
    """A newline would let one value forge additional .env lines."""
    env = tmp_path / ".env"
    env.write_text("A=1\n", encoding="utf-8")
    ok, detail = keymod.persist_to_env("groq", "abc\nMALICIOUS=1", env)
    assert not ok and "newline" in detail
    assert "MALICIOUS" not in env.read_text(encoding="utf-8")


def test_persist_refuses_empty(tmp_path):
    ok, detail = keymod.persist_to_env("groq", "   ", tmp_path / ".env")
    assert not ok and detail


# --- advisors read through the store --------------------------------------


def test_advisor_picks_up_a_runtime_key(monkeypatch):
    """A key entered at runtime is found without an environment variable.

    Skipped when the backend SDK is absent, because then `available()` is correctly
    False for a different reason and the test would be asserting the wrong thing. This
    surfaced the first time the suite ran against an installed wheel: `advisors` is an
    optional extra, so a clean install of the core has no `openai` package at all — and
    a core that could not be installed without one would be the real bug.
    """
    pytest.importorskip("openai", reason="advisors is an optional extra")

    from tesoro.advisors import build_advisor

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    keymod.clear_runtime_key("openai")
    assert build_advisor("openai", "gpt-4o-mini").available()[0] is False

    keymod.set_runtime_key("openai", SAMPLE)
    ok, detail = build_advisor("openai", "gpt-4o-mini").available()
    assert ok, detail


def test_key_test_reports_missing_key_without_calling_out():
    from tesoro.advisors import test_key

    keymod.clear_runtime_key("openai")
    result = test_key("openai", "gpt-4o-mini", api_key="")
    assert result.ok is False
    assert "no key" in result.detail
    assert result.latency_ms == 0.0, "it should not have attempted a network call"
