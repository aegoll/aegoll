"""OpenAI advisor (BYOK via `OPENAI_API_KEY`).

Uses Chat Completions with `response_format={"type": "json_object"}` rather than
the newer structured-outputs surface, because json_object is supported across the
widest range of OpenAI models -- including whatever the operator names in
`OPENAI_MODELS`. Validation happens in `parse_advice()` regardless, so an
unexpected shape fails closed to REVIEW rather than into the money path.
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


class OpenAIAdvisor:
    provider = "openai"

    def __init__(self, model: str = "gpt-4o-mini", api_key: str | None = None) -> None:
        self.model = model
        self._api_key = resolve_key("openai") if api_key is None else api_key

    def available(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "OPENAI_API_KEY is not set in the environment"
        try:
            import openai  # noqa: F401, PLC0415
        except ImportError:
            return False, "the `openai` package is not installed (pip install openai)"
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

        from openai import OpenAI  # noqa: PLC0415

        client = OpenAI(api_key=self._api_key)
        started = time.perf_counter()

        try:
            response = client.chat.completions.create(
                model=self.model,
                max_completion_tokens=700,
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
        cost = actual_cost_usd(self.model, in_tok, out_tok)

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
