<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
  <img src="assets/logo.svg" alt="tesoro" width="96" height="96">
</picture>

# tesoro

**Know what your autonomous agent spends, cap it across time, and be able to prove why
anything was refused.**

[![PyPI](https://img.shields.io/pypi/v/tesoro?color=2b4c7e&label=pypi)](https://pypi.org/project/tesoro/)
[![Python](https://img.shields.io/pypi/pyversions/tesoro?color=2b4c7e)](https://pypi.org/project/tesoro/)
[![CI](https://github.com/aegoll/tesoro/actions/workflows/ci.yml/badge.svg)](https://github.com/aegoll/tesoro/actions/workflows/ci.yml)
[![Licence](https://img.shields.io/badge/licence-Apache--2.0-2b4c7e)](LICENSE)
[![Red team](https://img.shields.io/badge/red%20team-15%2F18%20defended%2C%202%20open-9a6a00)](https://github.com/aegoll/aegs/tree/main/research/experiments/EXP-010)

*Three payments approved. The fourth stopped at the ceiling — and the record says which
control stopped it.*

</div>

`tesoro` decides whether an autonomous agent may spend — **before** it spends — and records
**which control decided**. Deterministic arithmetic, no model in the decision path, and an
append-only hash-chained journal of every decision.

## It is measured against 18 attacks, and 2 of them still work

```
$ python -m redteam.runner
  defended 15   by accident 1   undefended 2   error 0
```

**The interesting column is the middle one.** `DEFENDED_BY_ACCIDENT` means an attack was refused
by a control it does not target — paced slightly differently it succeeds, so it is counted as a
**finding, not a pass**. A headline of *16 defended* would count a coincidence as a control.

The two that work are named rather than footnoted:

| Attack | What happens |
|---|---|
| **Microtransaction structuring** | 40 payments of $0.001, five minutes apart. $0.04 moves, nothing refused. A count envelope **bounds** this and does not refuse it, because 40 trivial purchases is also what a legitimate agent does |
| **Journal truncation** | Any prefix of a valid hash chain is a valid hash chain, so a refused agent can delete the refusal. `verify_anchored()` closes it against an external anchor; **no anchor ships by default**, so the benchmark reports the unanchored posture |

Every score is a **sealed record** with a checksum — and one of them exists because an earlier
measurement was *wrong*: [EXP-009](https://github.com/aegoll/aegs/tree/main/research/experiments/EXP-009)
supersedes EXP-008 rather than editing it, because a record that can be revised after the fact
stops being evidence.

**📊 [The benchmark](https://aegoll.github.io/tesoro/benchmark.html)** — methodology, all 18
attacks, the provenance, and the limitations that matter more than the score. The first of those:
every attack was written by the author of the system under test.

## What is actually new here, and what is not

Most of this is not new, and saying so first is what makes the rest credible. Policy evaluation
over structured facts is **XACML** (2003) and **OPA**. Cumulative limits per period and per
counterparty are **card controls**, decades old. Delegation that narrows at each hop is **macaroon
caveat attenuation**. A hash chain with an external anchor is **Certificate Transparency** and
**Sigstore**. Even *fail-closed on a requirement you cannot discharge* is already in XACML.

Two things appear to be absent from that prior art:

1. **A benchmark whose scoring separates a defence from a coincidence** — the middle column above.
2. **Attribution as a required, independently conformance-tested property.** XACML responses *may*
   carry which policy applied; `AEGS` requires which control decided, and scores it separately from
   whether the verdict was right.

Both are checkable claims. [The prior-art survey](https://github.com/aegoll/research) records what
would falsify them.

**📖 [Documentation](https://aegoll.github.io/tesoro/)** — what an AEGL is, the architecture,
policies and rules, framework adapters, and the AEGS standard. Two pages worth reading first:
[**stablecoins and irreversibility**](https://aegoll.github.io/tesoro/stablecoins.html), which is
why the controls are shaped this way, and
[**what governs what**](https://aegoll.github.io/tesoro/ecosystem.html), which is how this relates
to action-governance tools like Microsoft's Agent Governance Toolkit.

```bash
pip install tesoro
tesoro init
```

```python
from tesoro import Governor

gov = Governor.load()                     # reads ./tesoro.yaml
decision = gov.authorize(amount_usd="2.50", vendor="acme", resource="/market/snapshot")

if decision.approved:
    pay(...)                              # your payment call, on any rail
    gov.settle(decision, success=True)    # envelopes consume here, not above
else:
    print(decision.verdict, decision.attributed_control, decision.reason)
```

Or from the terminal, which is the same layer and the same evidence:

```bash
tesoro check                                    # validate before an agent holds a wallet
tesoro decide --amount 2.50 --vendor acme --resource /market/snapshot
echo $?                                         # 0 approved · 2 refused · 1 invalid · 3 chain · 4 usage
tesoro report --html -o spend.html              # one self-contained page, no server
```

Start with [`docs/quickstart.md`](https://github.com/aegoll/tesoro/blob/main/docs/quickstart.md).

> **Status: pre-release.** Nothing is published yet. Ported from a working prototype and now
> at **597 tests**, a 7/7 [AEGS](https://github.com/aegoll/aegs) conformance score with both
> levels claimable, and **151 specification test vectors executing** against 56 normative
> clauses. See [`PLAN.md`](https://github.com/aegoll/tesoro/blob/main/PLAN.md) and [`CHANGELOG.md`](https://github.com/aegoll/tesoro/blob/main/CHANGELOG.md).

---

## A cap on one payment is not a budget

Per-payment caps are becoming table stakes; the x402 SDKs now ship one. `tesoro` is about
what a single cap cannot express:

| | A per-payment cap | `tesoro` |
|---|---|---|
| One transaction too large | ✅ | ✅ |
| Cumulative spend over a day, a month, a window | — | ✅ |
| Per-vendor and per-resource ceilings | — | ✅ |
| Velocity, and the *shape* of a sequence | — | ✅ (in progress) |
| Counterparty trust earned over settlements | — | ✅ |
| Whether this spend matches what the agent was **sent to do** | — | ✅ |
| Which control refused, recorded as evidence | — | ✅ |
| Tamper-evident decision history | — | ✅ |
| Policy as a reviewable, versioned, hashed file | — | ✅ |

Forty payments of one cent each pass every per-payment cap ever written. That is the
problem this layer exists for.

## Two channels, never one budget

An agent spends in two directions, and they are not the same money:

- **internal** — the tokens it burns thinking. Real currency, on your provider key.
- **external** — what it pays out. Settled to a counterparty.

Different currencies, different counterparties, different failure modes. They never share an
envelope. An exhausted token budget must *reject*, not queue for review: there is no human
to ask mid-run, and starting a run that cannot finish wastes the budget that is already short.

## Design commitments

These are not preferences. Each is enforced by a test.

- **No model in the decision path.** Every engine is deterministic integer arithmetic over
  structured values. An optional advisor may be consulted behind an economic gate and is
  *clamped* — it can tighten a verdict, never widen one. A governance layer that needs a
  model to authorize a payment has lost its cost and latency guarantees.
- **Policy is data, never code.** Declarative rules, a fixed comparator vocabulary, no
  `eval`. This is a security boundary: an executable policy file fetched from a registry is
  remote code execution wearing a governance hat.
- **Money never touches a float.** Integer atomic units internally; conversion happens once
  at the boundary with an explicitly specified rounding mode. Passing a `float` raises.
- **Absent ≠ not-run ≠ unknown ≠ zero.** Four distinct states. An unmeasured vendor history
  rendered as `0` once made every advisor treat established counterparties as strangers.
- **Every control may only narrow.** No engine can widen a verdict another one set.
- **Evidence is append-only and hash-chained**, with its one known gap documented rather
  than papered over.
- **CLI first.** The terminal is the product surface; the optional localhost page comes
  later, binds `127.0.0.1` only, and is off unless you start it.

## AEGS

`tesoro` is a **policy-engine host**. [AEGS](https://github.com/aegoll/aegs) — the
Autonomous Economic Governance Standard — is one *profile* it can enforce, and the default.
Pick it and your agent emits conformant, scoreable Decision Records without your ever having
read the specification.

A **profile** says which controls must exist and what evidence must be emitted; that is
written by the standard. A **policy pack** says what the rules actually are; that is written
by you.

## Repositories

| | |
|---|---|
| [`tesoro`](https://github.com/aegoll/tesoro) | this package |
| [`aegs`](https://github.com/aegoll/aegs) | the standard: spec, schemas, vectors, conformance |
| [`tesoro-integrations`](https://github.com/aegoll/tesoro-integrations) | example agents, frameworks, use cases |
| [`Jayzilva/x402`](https://github.com/Jayzilva/x402) | the proof-of-concept this grew from. Read-only |

## Documentation

- [`docs/quickstart.md`](https://github.com/aegoll/tesoro/blob/main/docs/quickstart.md) — governing an agent from nothing, in about five minutes
- [`docs/api-surface.md`](https://github.com/aegoll/tesoro/blob/main/docs/api-surface.md) — the public API, and what is deliberately not public
- [`docs/adapters.md`](https://github.com/aegoll/tesoro/blob/main/docs/adapters.md) — framework and rail adapters, and what is verified about each
- [`PLAN.md`](https://github.com/aegoll/tesoro/blob/main/PLAN.md) — the build plan, as tracked checkboxes
- [`PROVENANCE.md`](https://github.com/aegoll/tesoro/blob/main/PROVENANCE.md) — what was ported from the prototype, and from which commit

## What is not established

Read this before quoting anything. AML/CFT effectiveness is a schema with no engine behind
it. No regulatory compliance is claimed or sought. Every measurement so far is one run or a
handful, single-agent, on testnet. The conformance suite has never scored an implementation
nobody here wrote — that is the open question it exists to answer. Three red-team findings
are open by design, each needing a control that does not yet exist.

The red team's verdict on this layer's own central claim, kept because it is the fairest
summary available: *the layer resists prose; it did not resist a minus sign, and it does not
yet resist patience.*

## Licence

Apache-2.0. The patent grant matters for anything with standards ambition.
