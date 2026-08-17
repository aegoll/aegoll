# Extending aegoll

Two extension points, and picking the right one matters more than it looks.

| You want to… | Use | It is |
|---|---|---|
| Combine facts the engines already produce | a **derived fact** | data, in your policy pack |
| Add a measurement nothing currently makes | a **custom engine** | code, registered and gated |

**Reach for a derived fact first.** A pack is data — no expressions, no `eval`, nothing
that can execute — so a derived fact carries none of the risk an engine does. An engine is
Python running inside the decision path; the gate on it is real, but the safest extension
is the one that isn't code.

The rule of thumb: if the answer is already somewhere in the facts and you just need to
combine them, that's a derived fact. If you need to *measure something new* — query a list,
score a pattern, consult a history nothing tracks yet — that's an engine.

---

## Derived facts

A named predicate over existing facts, declared in the pack and usable in any rule below it.

```yaml
version: 1
name: house-rules

derived:
  # "over a dollar, to somebody we have never paid"
  - name: big_and_new
    all:
      - amount_usd: { gte: 1.0 }
      - vendor.is_new: true

  # Derived facts compose, in declaration order.
  - name: needs_a_human
    any:
      - derived.big_and_new: true
      - risk.score: { gte: 0.8 }

rules:
  - id: escalate-unfamiliar-spend
    priority: 10
    when:
      derived.needs_a_human: true
    then: ESCALATE
    reason: first payment to this counterparty is above the trivial threshold

  - id: default
    priority: 9999
    when: {}
    then: REVIEW
```

**Three combinators, and only three:** `all`, `any`, `not`. A clause is a mapping of fact
to condition, and every condition in one clause must hold — the same shape and the same
meaning as a rule's `when`. `not: [a, b]` is `not (a and b)`.

**Declaration order is evaluation order.** A derived fact may reference one declared above
it, never one below. That is why a cycle cannot be *written* rather than being written and
then detected: there is no ordering in which both halves are in scope.

Everything else about a pack still applies — the comparator vocabulary is the same fixed
set inside a derived clause as anywhere else, and `aegoll check` rejects the whole pack at
load if any of it is wrong. It will tell you what *is* in scope at the point you referenced
something that isn't:

```
[error] d:derived.second: no derived fact 'third' is in scope here. A derived fact must be
declared *before* it is used — declaration order is evaluation order. In scope at this
point: derived.first
```

### What facts are available

Everything the engines produce. `aegoll check --controls` shows the profile side; for the
fact vocabulary:

```python
from aegoll.validate import known_facts
print(sorted(known_facts()))
```

That list is **derived from the engine code**, not maintained by hand — so it cannot drift
from the facts that actually exist.

---

## Custom engines

When you need a measurement nothing makes yet.

```python
from aegoll import extend


class CountryScreen:
    """Refuse counterparties in jurisdictions we are not set up for."""

    name = "country-screen"          # this becomes the attributed control

    def __init__(self, blocked: frozenset[str]) -> None:
        self.blocked = blocked

    def assess(self, context: extend.Context) -> extend.Assessment:
        country = (context.request.vendor.tags or ())
        matches = self.blocked & set(country)
        if matches:
            return extend.Assessment(
                control=self.name,
                verdict="REJECT",
                score=1.0,
                reason=f"counterparty is tagged {sorted(matches)}",
            )
        return extend.Assessment(control=self.name, verdict=None, score=0.0)


extend.register_engine(CountryScreen(frozenset({"xx", "yy"})))
```

Registered engines are consulted on every decision, after the built-in controls.

### The contract

**1. An engine may only narrow.** Not a rule you have to follow — a fact about the
composition. Your engine returns an *opinion*; the composition root applies `narrower()`.
An engine answering `APPROVE` against a standing `REJECT` has no effect at all. There is
nothing to get wrong here, which is the point.

If your engine returns something looser than the standing verdict, that is recorded as
`opinion_did_not_narrow` rather than silently dropped — an engine whose view is routinely
discarded is worth noticing.

**2. An engine must be pure.** Checked at **registration**, by reading your `assess`
source. Refused for any of:

- importing `os`, `sys`, `pathlib`, `socket`, `urllib`, `requests`, `httpx`, `subprocess`, `sqlite3` — no I/O in the decision path
- importing `time` or `datetime` — the clock is injected; take `now` from the `Context`
- importing `random` or `secrets` — a non-deterministic engine cannot be replayed, and replay is what makes a decision auditable
- importing `anthropic`, `openai`, `groq`, `google` — no model in the decision path
- calling `open`, `eval`, `exec`, `compile`, `__import__`, `input`
- declaring `global` — mutable module state means the same inputs stop producing the same decision

Registration fails at import time, where a developer is looking at it, rather than at
runtime while an agent is mid-run holding a wallet.

If you need something that isn't on the `Context`, that's a gap in the `Context` worth
reporting — not a reason to reach around it.

**3. `verdict=None` means no opinion, which is not approval.** An engine with nothing to
say says nothing. Voting `APPROVE` to mean "no objection" would make a silent engine look
like an endorsing one.

**4. A control that could not run says `measured=False`.** Never `score=0.0`. The
`Assessment` constructor rejects that combination outright, because it is the exact bug the
four-state rule exists for: an unmeasured vendor history rendered as `0` once made every
advisor treat established counterparties as strangers.

An unmeasured control is recorded as `not_measured` on the decision, so a record can always
show the difference between *screened, clean* and *never screened*.

**5. Your name may not be one the standard defines.** `treasury`, `risk`, `TrustAssessment`
and the rest are reserved. An attributed control that means two different things depending
on whose engine is loaded makes every conformance report ambiguous.

### What the Context carries

```python
context.request     # the PaymentRequest: amount_atomic, resource, vendor, channel, purpose
context.snapshot    # the HistorySnapshot the engines read
context.now         # the injected clock's time. Do not read a wall clock
context.trust       # the built-in assessments, already computed
context.risk
context.roi
context.budget
context.facts       # the flat fact base, including derived facts
```

Values, not handles. No store, no journal, no config loader — an engine that wanted one of
those would be reaching for I/O, which is what the gate refuses.

### Registration order

Engines are consulted in registration order. Order does not change the final verdict —
narrowing is commutative — but it does change which control gets **attributed** when two
would narrow to the same thing, and conformance scores attribution.

### Testing your engine

```python
from aegoll import extend

def test_it_is_registerable():
    extend.clear_engines()
    extend.register_engine(CountryScreen(frozenset({"xx"})))
    assert [e.name for e in extend.registered_engines()] == ["country-screen"]
```

The registry is process-global, so clear it between tests. A leaked engine breaks its
neighbours.

---

## What neither extension point will let you do

- **Widen a verdict.** No mechanism exists.
- **Put a model in the decision path.** Advisors are a separate, clamped surface — see [`advisors.md`](advisors.md).
- **Execute anything from a policy pack.** Packs are data. Permanently.
- **Claim a standard control name.**

If you want something here and cannot get it, that is worth raising as an issue. Working
around a gap hides it; reporting one is how the gap gets closed.
