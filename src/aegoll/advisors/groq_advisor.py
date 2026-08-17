"""Groq advisor (BYOK via `GROQ_API_KEY`).

Groq's value here is price, not peak capability: `llama-3.1-8b-instant` costs
about $0.00013 per analysis against Haiku 4.5's $0.004. That drops the EIAP
break-even from roughly $0.08 to $0.003 -- below two of the three prices the x402
seller charges -- which is the finding Phase 2 exists to test. Whether the cheaper
advice is *good enough* at that price is the open question.
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


class GroqAdvisor:
    provider = "groq"

    def __init__(self, model: str = "llama-3.1-8b-instant", api_key: str | None = None) -> None:
        self.model = model
        self._api_key = resolve_key("groq") if api_key is None else api_key

    def available(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "GROQ_API_KEY is not set in the environment"
        try:
            import groq  # noqa: F401, PLC0415
        except ImportError:
            return False, "the `groq` package is not installed (pip install groq)"
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

        from groq import Groq  # noqa: PLC0415

        client = Groq(api_key=self._api_key)
        started = time.perf_counter()

        try:
            response = client.chat.completions.create(
                model=self.model,
                max_tokens=700,
                # Deterministic-as-possible: this is a control decision, not prose.
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT
                        + "\n\nReturn JSON matching this schema:\n"
                        + json.dumps(ADVICE_SCHEMA),
                    },
                    {"role": "user", "content": build_user_prompt(request)},
                ],
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
        in_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
        out_tok = int(getattr(usage, "completion_tokens", 0) or 0)

        text = (response.choices[0].message.content or "").strip()
        payload: dict[str, Any]
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return Advice(
                recommendation="REVIEW",
                confidence=0.0,
                rationale="Advisor returned unparseable JSON; verdict unchanged.",
                provider=self.provider,
                model=self.model,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cost_usd=actual_cost_usd(self.model, in_tok, out_tok),
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
            cost_usd=actual_cost_usd(self.model, in_tok, out_tok),
            latency_ms=latency_ms,
        )
