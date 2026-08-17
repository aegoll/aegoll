"""Google Gemini advisor (BYOK via `GEMINI_API_KEY`, or `GOOGLE_API_KEY`).

Uses the `google-genai` SDK with `response_mime_type="application/json"` plus a
response schema, so the model is constrained to the advice shape at the API level.

Gemini's schema dialect rejects `additionalProperties`, which the shared
`ADVICE_SCHEMA` carries for the other providers -- so the schema is translated
rather than passed through. Same contract, different dialect.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from .keys import resolve_key
from . import (
    SYSTEM_PROMPT,
    VERDICTS,
    Advice,
    AdviceRequest,
    actual_cost_usd,
    build_user_prompt,
    estimate_call_cost_usd,
    parse_advice,
)

# Gemini's OpenAPI-subset schema. Deliberately built here rather than reusing
# ADVICE_SCHEMA: `additionalProperties` is rejected by the API.
_GEMINI_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "recommendation": {"type": "STRING", "enum": list(VERDICTS)},
        "confidence": {"type": "NUMBER"},
        "rationale": {"type": "STRING"},
        "concerns": {"type": "ARRAY", "items": {"type": "STRING"}},
        "injection_suspected": {"type": "BOOLEAN"},
    },
    "required": ["recommendation", "confidence", "rationale"],
}


class GeminiAdvisor:
    provider = "gemini"

    def __init__(self, model: str = "gemini-2.0-flash", api_key: str | None = None) -> None:
        self.model = model
        self._api_key = resolve_key("gemini") if api_key is None else api_key

    def available(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "GEMINI_API_KEY (or GOOGLE_API_KEY) is not set in the environment"
        try:
            from google import genai  # noqa: F401, PLC0415
        except ImportError:
            return False, "the `google-genai` package is not installed (pip install google-genai)"
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

        from google import genai  # noqa: PLC0415
        from google.genai import types  # noqa: PLC0415

        client = genai.Client(api_key=self._api_key)
        started = time.perf_counter()

        try:
            response = client.models.generate_content(
                model=self.model,
                contents=build_user_prompt(request),
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=_GEMINI_SCHEMA,
                    max_output_tokens=700,
                    temperature=0,
                ),
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
        usage = getattr(response, "usage_metadata", None)
        in_tok = int(getattr(usage, "prompt_token_count", 0) or 0)
        out_tok = int(getattr(usage, "candidates_token_count", 0) or 0)
        cost = actual_cost_usd(self.model, in_tok, out_tok)

        text = (getattr(response, "text", "") or "").strip()
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
