# Advisors

**There is no model in the decision path.** Not "we try to avoid one" — the decision path
is deterministic integer arithmetic over structured values, and a test walks the package
tree to prove nothing outside `advisors/` imports a model client.

An advisor is a separate, optional, gated surface that may **tighten** a verdict the engines
already reached. It can never loosen one, and it is never consulted at all unless you turn
it on.

---

## Why the exclusion is structural

A governance layer's value is that it answers *before* the money moves, cheaply enough that
nobody is tempted to skip it. Concretely, from the reference implementation: **~299 µs and
$0.000000 per decision.**

Put a model in that path and both numbers go, along with the properties that depend on them:

- **Cost.** A per-decision inference charge means governance now has a budget of its own, competing with the spend it governs.
- **Latency.** Hundreds of microseconds becomes hundreds of milliseconds. At that point the layer is something to bypass under load.
- **Determinism.** `tesoro replay` re-derives past decisions from the journal and checks they come out identical. A model in the path makes that impossible, and an unreplayable decision is not an auditable one.
- **Injection resistance.** Vendor-supplied text reaches the layer on every request. A deterministic path does not read prose, so there is nothing to talk into anything. The red-team suite's summary of the design is worth quoting exactly: *the layer resists prose. It did not resist a minus sign, and it does not yet resist patience.*

That last line is the honest version. The deterministic path is not magic — it had a
negative-amount bug and a 30-digit overflow, both now permanent test vectors. What it *does*
give is a class of attack that cannot work at all, and that class is the one an LLM in the
decision path would reopen.

## What an advisor may do

Exactly one thing: **narrow**.

```
engines decide  →  verdict  →  advisor may tighten it  →  final verdict
                                        │
                                        └── can never widen; clamped by narrower()
```

The clamp is not a convention. The advisor returns an opinion and the host applies
`narrower()`, so an advisor answering `APPROVE` against a standing `REJECT` has no effect.
Same construction as [custom engines](custom-policies.md), same reason.

## When it is consulted: the economic gate

An advisor is not called on every decision. The **EIAP** — economic intelligence
authorization protocol — asks whether the call is worth making, and Phase 1 records the
answer without acting on it:

> `eiap: would_invoke=True tier=small break_even=$0.100466`

The reasoning: an analysis costing $0.004 cannot pay for itself on a $0.001 payment. Below
the break-even exposure, invoking a model **destroys value** — you have spent more governing
than the worst case you were governing against. So the gate is economic, not a heuristic,
and it is computed per decision from the amount at risk.

## Configuration

Off by default. Enable in `tesoro.yaml`:

```yaml
advisor:
  provider: anthropic
  model: claude-haiku-4-5
  enabled: true
```

Install the extra:

```bash
pip install "tesoro[advisors]"
```

The core installs no model client. A clean `pip install tesoro` pulls `tesoro` and `PyYAML`
and nothing else.

## Keys

**Keys never go in the config file.** `tesoro check` refuses one outright:

```
[error] tesoro.yaml:advisor: 'api_key' must not be in a config file. Keys come from the
environment; this file gets committed and a key in it is a leak.
```

Keys come from the environment, or from an explicit `load_env_file(path)` call where **you**
name the file. The library never goes looking. An earlier version walked up two directories
hunting for a `.env` and exported whatever it found into `os.environ` at import time — in a
library that handles BYOK keys that is a security problem, not a convenience, and a test now
plants a `.env` as bait to prove importing the package exports nothing.

Beyond that:

- Keys are **never** written to the evidence journal, and never logged.
- `masked()` is the only display path. Nothing else renders a key, anywhere.
- Four backends are supported — Anthropic, OpenAI, Gemini, Groq — each separately importable, each optional.

## What is not established

Stated because the temptation to overclaim is strongest here.

- **Advisor injection resistance is not demonstrated.** Vendor-supplied text reaching an advisor is the one genuine LLM surface in the system, and it is **not yet in the red-team catalogue**. What defends it today is the clamp — which is well tested — and a detection mechanism observed working exactly once. That is not the same as a tested defence.
- **The false-block rate is one measurement.** Sealed as EXP-001, from a handful of runs. It is not a statistically meaningful figure and should not be quoted as one.
- **`NOT_RECOMMENDED` is empirical, not theoretical.** Some models were measured refusing traffic that should have passed. The list is what was observed, not what was predicted, and it is not exhaustive.

## The short version

Turn an advisor on if you want a second opinion that can only ever be stricter. Leave it off
and the layer works exactly as it does now — the engines have never needed one, and the
guarantee that they do not is worth more than any advice a model could add.
