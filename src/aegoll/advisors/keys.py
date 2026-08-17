"""BYOK key resolution: runtime overlay on top of the environment.

Keys can arrive two ways, and precedence matters:

1. **Runtime** -- typed into the cockpit. Held in memory only, for the life of the
   process. Wins, so a key entered in the UI overrides a stale one in `.env`
   without the user having to find and edit the file.
2. **Environment** -- `.env` or an exported variable. The persistent path.

Nothing here writes a key to disk unless the caller explicitly asks
(`persist_to_env`), and no function in this module logs, prints, or returns a full
key. `masked()` is the only way a key is ever rendered.

The overlay is process-global, not per-browser-session. For a local single-user
cockpit that is the right trade; if this were ever served to more than one person
it would be wrong, and `SECURITY` in the README says so.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

# provider -> env var names, first is canonical for writing
ENV_KEYS: dict[str, tuple[str, ...]] = {
    "groq": ("GROQ_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "anthropic": ("ANTHROPIC_API_KEY",),
}

# Rough shape checks. Deliberately loose: providers change prefixes, and a
# false rejection here would block a working key. Used only to warn.
KEY_HINTS: dict[str, str] = {
    "groq": "gsk_...",
    "openai": "sk-...",
    "gemini": "AIza...",
    "anthropic": "sk-ant-...",
}

_lock = threading.Lock()
_runtime: dict[str, str] = {}


@dataclass(frozen=True)
class KeyStatus:
    provider: str
    present: bool
    source: str  # "runtime" | "env" | "none"
    masked: str
    env_var: str

    @property
    def editable_hint(self) -> str:
        return KEY_HINTS.get(self.provider, "")


def masked(key: str | None) -> str:
    """Render a key safely. Never returns more than the last four characters."""
    if not key:
        return "—"
    tail = key[-4:] if len(key) >= 4 else "?"
    return f"{'•' * 8}{tail} ({len(key)} chars)"


def set_runtime_key(provider: str, key: str) -> None:
    """Hold a key in memory for this process. Empty string clears it."""
    with _lock:
        if key and key.strip():
            _runtime[provider] = key.strip()
        else:
            _runtime.pop(provider, None)


def clear_runtime_key(provider: str) -> None:
    with _lock:
        _runtime.pop(provider, None)


def set_runtime_keys(mapping: dict[str, str]) -> None:
    for provider, key in (mapping or {}).items():
        set_runtime_key(provider, key)


def resolve_key(provider: str) -> str:
    """The key an advisor should use: runtime first, then environment."""
    with _lock:
        runtime = _runtime.get(provider)
    if runtime:
        return runtime
    for var in ENV_KEYS.get(provider, ()):
        value = os.environ.get(var)
        if value and value.strip():
            return value.strip()
    return ""


def key_status(provider: str) -> KeyStatus:
    env_var = ENV_KEYS.get(provider, (f"{provider.upper()}_API_KEY",))[0]
    with _lock:
        runtime = _runtime.get(provider)
    if runtime:
        return KeyStatus(provider, True, "runtime", masked(runtime), env_var)
    for var in ENV_KEYS.get(provider, ()):
        value = os.environ.get(var)
        if value and value.strip():
            return KeyStatus(provider, True, "env", masked(value.strip()), var)
    return KeyStatus(provider, False, "none", "—", env_var)


def looks_plausible(provider: str, key: str) -> tuple[bool, str]:
    """A cheap shape check, advisory only.

    Never blocks: prefixes change, and refusing a valid key would be worse than
    accepting an invalid one that then fails a live test with a clear error.
    """
    key = (key or "").strip()
    if not key:
        return False, "empty"
    if len(key) < 16:
        return False, "shorter than any provider key we know of"
    if re.search(r"\s", key):
        return False, "contains whitespace — likely a paste error"
    hint = KEY_HINTS.get(provider)
    if hint:
        prefix = hint.rstrip(".")
        if not key.startswith(prefix):
            return True, f"does not start with {hint} — unusual, but may still work"
    return True, "looks plausible"


def persist_to_env(provider: str, key: str, env_path: Path) -> tuple[bool, str]:
    """Write a key into `.env`, replacing any existing line for that variable.

    Only called on an explicit request. The value is written verbatim and never
    echoed back to the caller.
    """
    var = ENV_KEYS.get(provider, (f"{provider.upper()}_API_KEY",))[0]
    key = (key or "").strip()
    if not key:
        return False, "no key to write"
    if "\n" in key or "\r" in key:
        return False, "key contains a newline; refusing to write"

    try:
        lines = (
            env_path.read_text(encoding="utf-8").splitlines()
            if env_path.exists()
            else []
        )
        backup = env_path.with_suffix(env_path.suffix + ".bak-byok")
        if env_path.exists():
            backup.write_text("\n".join(lines) + "\n", encoding="utf-8")

        pattern = re.compile(rf"^\s*{re.escape(var)}\s*=")
        replaced = False
        out: list[str] = []
        for line in lines:
            if pattern.match(line):
                if not replaced:
                    out.append(f"{var}={key}")
                    replaced = True
                # drop any duplicate definitions of the same variable
            else:
                out.append(line)
        if not replaced:
            out.append("")
            out.append(f"# AEGL advisor key, written from the cockpit")
            out.append(f"{var}={key}")

        env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    except OSError as exc:
        return False, f"could not write {env_path.name}: {exc}"

    # Reflect it in this process too, so the change takes effect immediately.
    os.environ[var] = key
    return True, f"wrote {var} to {env_path.name} (backup at {backup.name})"
