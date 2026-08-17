"""Anthropic advisor (BYOK via `ANTHROPIC_API_KEY`).

Stronger reasoning at roughly thirty times the Groq cost. Uses structured outputs
(`output_config.format`) so the response is schema-validated by the API rather
than parsed hopefully -- which matters here, because the advisor sits in a money
path and reads untrusted vendor text.

Note this key is the *same* one the agent spends on its own thinking, so advisor
calls draw on the same real budget. That is not an accident: the EIAP's whole
claim is that analysis cost belongs in the economic decision, and using a free
side-channel for advice would quietly falsify it.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from .keys import resolve_key
from . import (
    ADVICE_SCHEMA,
    SYSTEM_PROMPT,
    Advice,
    AdviceRequest,
    actual_cost_usd,
    build_user_prompt,
    estimate_call_cost_usd,
    parse_advice,
)


class AnthropicAdvisor:
    provider = "anthropic"

    def __init__(self, model: str = "claude-haiku-4-5", api_key: str | None = None) -> None:
        self.model = model
        self._api_key = resolve_key("anthropic") if api_key is None else api_key

    def available(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "ANTHROPIC_API_KEY is not set in the environment"
        try:
            import anthropic  # noqa: F401, PLC0415
        except ImportError:
            return False, "the `anthropic` package is not installed (pip install anthropic)"
        return True, "ready"

    def estimated_cost_usd(self) -> float:
        return estimate_call_cost_usd(self.model)

    def advise(self, request: AdviceRequest) -> Advice:
        ok, detail = self.available()
        if not ok:
            return Advice(
                recommendation="REVIEW",
                confidence=0.0,
                rationale="Advisor unavailable; deterministic verdict stands.",
                provider=self.provider,
                model=self.model,
                error=detail,
            )

        import anthropic  # noqa: PLC0415

        client = anthropic.Anthropic(api_key=self._api_key)
        started = time.perf_counter()

        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": ADVICE_SCHEMA,
                    }
                },
                messages=[{"role": "user", "content": build_user_prompt(request)}],
            )
        except Exception as exc:
            return Advice(
                recommendation="REVIEW",
                confidence=0.0,
                rationale="Advisor call failed; deterministic verdict stands.",
                provider=self.provider,
                model=self.model,
                latency_ms=(time.perf_counter() - started) * 1000,
                error=f"{type(exc).__name__}: {exc}",
            )

        latency_ms = (time.perf_counter() - started) * 1000
        usage = getattr(response, "usage", None)
        in_tok = int(getattr(usage, "input_tokens", 0) or 0)
        out_tok = int(getattr(usage, "output_tokens", 0) or 0)
        cost = actual_cost_usd(self.model, in_tok, out_tok)

        # Safety classifiers can decline; check before reading content.
        if getattr(response, "stop_reason", None) == "refusal":
            return Advice(
                recommendation="REVIEW",
                confidence=0.0,
                rationale="Advisor declined to analyse this request.",
                provider=self.provider,
                model=self.model,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cost_usd=cost,
                latency_ms=latency_ms,
                error="stop_reason=refusal",
            )

        text = ""
        for block in response.content:
            if getattr(block, "type", "") == "text":
                text += getattr(block, "text", "")

        payload: dict[str, Any]
        try:
            payload = json.loads(text.strip())
        except json.JSONDecodeError:
            return Advice(
                recommendation="REVIEW",
                confidence=0.0,
                rationale="Advisor returned unparseable JSON; verdict unchanged.",
                provider=self.provider,
                model=self.model,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cost_usd=cost,
                latency_ms=latency_ms,
                error=f"non-JSON response: {text[:200]}",
            )

        rec, confidence, rationale, concerns, injection = parse_advice(payload)
        return Advice(
            recommendation=rec,
            confidence=confidence,
            rationale=rationale,
            concerns=concerns,
            injection_suspected=injection,
            provider=self.provider,
            model=self.model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
            latency_ms=latency_ms,
        )
