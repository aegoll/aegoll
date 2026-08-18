# aegoll

**Know what your autonomous agent spends, cap it across time, and be able to prove why
anything was refused.**

`aegoll` is an *Autonomous Economic Governance Layer* (AEGL): it sits between an agent and
every dollar it spends — the tokens it burns thinking and the money it pays out — and
decides, before the payment, whether it should happen. Ten deterministic engines, no model
in the decision path, an append-only hash-chained record of every decision and the control
that made it.

```bash
pip install aegoll
aegoll init
```

```python
from aegoll import Governor

gov = Governor.load()                     # reads ./aegoll.yaml
decision = gov.authorize(amount_usd="2.50", vendor="acme", resource="/market/snapshot")

if decision.approved:
    pay(...)                              # your payment call, on any rail
    gov.settle(decision, success=True)    # envelopes consume here, not above
else:
    print(decision.verdict, decision.attributed_control, decision.reason)
```

Or from the terminal, which is the same layer and the same evidence:

```bash
aegoll check                                    # validate before an agent holds a wallet
aegoll decide --amount 2.50 --vendor acme --resource /market/snapshot
echo $?                                         # 0 approved · 2 refused · 1 invalid · 3 chain · 4 usage
aegoll report --html -o spend.html              # one self-contained page, no server
```

Start with [`docs/quickstart.md`](https://github.com/aegoll/aegoll/blob/main/docs/quickstart.md).

> **Status: pre-release.** Nothing is published yet. Ported from a working prototype and now
> at **597 tests**, a 7/7 [AEGS](https://github.com/aegoll/aegs) conformance score with both
> levels claimable, and **151 specification test vectors executing** against 56 normative
> clauses. See [`PLAN.md`](https://github.com/aegoll/aegoll/blob/main/PLAN.md) and [`CHANGELOG.md`](https://github.com/aegoll/aegoll/blob/main/CHANGELOG.md).

---

## A cap on one payment is not a budget

Per-payment caps are becoming table stakes; the x402 SDKs now ship one. `aegoll` is about
what a single cap cannot express:

| | A per-payment cap | `aegoll` |
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

`aegoll` is a **policy-engine host**. [AEGS](https://github.com/aegoll/aegs) — the
Autonomous Economic Governance Standard — is one *profile* it can enforce, and the default.
Pick it and your agent emits conformant, scoreable Decision Records without your ever having
read the specification.

A **profile** says which controls must exist and what evidence must be emitted; that is
written by the standard. A **policy pack** says what the rules actually are; that is written
by you.

## Repositories

| | |
|---|---|
| [`aegoll`](https://github.com/aegoll/aegoll) | this package |
| [`aegs`](https://github.com/aegoll/aegs) | the standard: spec, schemas, vectors, conformance |
| [`aegoll-integrations`](https://github.com/aegoll/aegoll-integrations) | example agents, frameworks, use cases |
| [`Jayzilva/x402`](https://github.com/Jayzilva/x402) | the proof-of-concept this grew from. Read-only |

## Documentation

- [`docs/quickstart.md`](https://github.com/aegoll/aegoll/blob/main/docs/quickstart.md) — governing an agent from nothing, in about five minutes
- [`docs/api-surface.md`](https://github.com/aegoll/aegoll/blob/main/docs/api-surface.md) — the public API, and what is deliberately not public
- [`docs/adapters.md`](https://github.com/aegoll/aegoll/blob/main/docs/adapters.md) — framework and rail adapters, and what is verified about each
- [`PLAN.md`](https://github.com/aegoll/aegoll/blob/main/PLAN.md) — the build plan, as tracked checkboxes
- [`PROVENANCE.md`](https://github.com/aegoll/aegoll/blob/main/PROVENANCE.md) — what was ported from the prototype, and from which commit

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
