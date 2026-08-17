"""Test a BYOK key with the smallest call each provider allows.

Separate from `advise()` because validation should cost approximately nothing. A
full analysis is ~2k input tokens; these probes are a handful, so testing a key is
free in practice even on the expensive providers.

Returns a plain result rather than raising: a bad key is an expected state in a
BYOK flow, and the UI needs the provider's own error text to be useful ("invalid
api key" vs "model not found" vs "quota exceeded" are three different fixes).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .keys import resolve_key


@dataclass(frozen=True)
class KeyTest:
    provider: str
    model: str
    ok: bool
    detail: str
    latency_ms: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "ok": self.ok,
            "detail": self.detail,
            "latencyMs": round(self.latency_ms, 1),
        }


_PROBE = [{"role": "user", "content": "Reply with the single word: ok"}]


def _shorten(exc: Exception, limit: int = 240) -> str:
    """Provider errors are verbose and can echo the request back.

    Truncate, and never include anything we sent -- an error string that repeats
    the prompt could carry the key if a provider ever echoed headers.
    """
    text = f"{type(exc).__name__}: {exc}"
    return text[:limit]


def test_key(provider: str, model: str, api_key: str | None = None) -> KeyTest:
    """Issue the cheapest possible request to prove a key works."""
    # `None` means "use whatever is configured"; an explicit "" means "no key".
    # Collapsing the two would make a deliberate empty-key test call out with a
    # key the caller did not pass.
    key = (resolve_key(provider) if api_key is None else api_key).strip()
    if not key:
        return KeyTest(provider, model, False, "no key set for this provider")

    started = time.perf_counter()
    try:
        if provider == "groq":
            from groq import Groq

            Groq(api_key=key).chat.completions.create(
                model=model, max_tokens=4, messages=_PROBE
            )

        elif provider == "openai":
            from openai import OpenAI

            OpenAI(api_key=key).chat.completions.create(
                model=model, max_completion_tokens=4, messages=_PROBE
            )

        elif provider == "anthropic":
            import anthropic

            anthropic.Anthropic(api_key=key).messages.create(
                model=model, max_tokens=4, messages=_PROBE
            )

        elif provider == "gemini":
            from google import genai
            from google.genai import types

            # Bind the client to a name. Chaining off `genai.Client(...)` leaves it
            # a temporary, and the SDK closes its transport when the object is
            # collected -- which raced the request and failed with "client has been
            # closed" rather than anything to do with the key.
            client = genai.Client(api_key=key)
            client.models.generate_content(
                model=model,
                contents="Reply with the single word: ok",
                config=types.GenerateContentConfig(max_output_tokens=4),
            )

        else:
            return KeyTest(provider, model, False, f"unknown provider {provider!r}")

    except ImportError as exc:
        return KeyTest(
            provider,
            model,
            False,
            f"SDK not installed: {exc}",
            (time.perf_counter() - started) * 1000,
        )
    except Exception as exc:
        return KeyTest(
            provider,
            model,
            False,
            _shorten(exc),
            (time.perf_counter() - started) * 1000,
        )

    return KeyTest(
        provider,
        model,
        True,
        f"key accepted by {provider}; {model} is reachable",
        (time.perf_counter() - started) * 1000,
    )
