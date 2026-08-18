# Quickstart

Governing an agent's spending, from nothing, in about five minutes. No prior knowledge of
AEGS, profiles or Decision Records assumed — you will have produced all three by the end
without needing to have read about any of them.

Requires Python 3.11 or newer. Nothing else: the core installs `tesoro` and `PyYAML` and
stops.

---

## 1 · Install

```bash
pip install tesoro
```

Two packages, on purpose. No web framework, no model client, no network library — a layer
that decides whether a payment may happen has no business pulling in a HTTP stack. If you
want schema validation, a payment rail or an advisory model, each is a separate extra
(`tesoro[schema]`, `tesoro[x402]`, `tesoro[advisors]`).

## 2 · Create a project

```bash
mkdir my-agent && cd my-agent
tesoro init
```

You now have two files:

| File | What it is |
|---|---|
| `tesoro.yaml` | which profile to enforce, which policy pack to use, where state lives |
| `policies/default.yaml` | **your rules** — the numbers and the decisions |

The split matters and is the one concept worth understanding up front:

- A **profile** says *which controls must exist and what must be recorded*. That is written
  by [the standard](https://github.com/aegoll/aegs), not by you.
- A **policy pack** says *what the rules actually are*. That is yours. Edit it freely.

## 3 · Check it before an agent holds a wallet

```bash
tesoro check
```

```
config : /my-agent/tesoro.yaml
profile: aegs-1  7 required control(s)
policy : default  a5a64aeb69dbc5f9206b31022064da26  12 rules

ok
```

Exit `0` means the config *and* the pack it points at are both valid. Every fault is
reported at once rather than one per run, so this is usable in CI:

```yaml
- run: tesoro check      # fails the build on an invalid policy
```

That hash is the pack's content hash, and it goes into every decision this pack produces. A
label can be reused across edited rules; a hash cannot.

## 4 · Ask it a question

```bash
tesoro decide --amount 0.01 --vendor acme --resource /market/snapshot
```

```
APPROVE  /market/snapshot  $0.010000
  rule     : auto-approve-micro
  trust    : 0.2500  ['new_vendor']
  budget   : ok=True binding=None
  ...
```

Now one it should refuse:

```bash
tesoro decide --amount 500 --vendor acme --resource /market/snapshot
```

```
REJECT  /market/snapshot  $500.000000
  rule     : deny-over-balance
```

**Check the exit code.** This is what makes it scriptable:

| Exit | Meaning |
|---|---|
| `0` | approved |
| `2` | **refused.** The layer worked and said no — not an error |
| `1` | invalid config or policy |
| `3` | the evidence chain does not verify |
| `4` | usage error |

```bash
if tesoro decide --amount 500 --vendor acme --resource /r; then
  echo "cleared"
else
  echo "refused: $?"     # 2
fi
```

`2` is a refusal rather than a failure because a governance layer that returns an error when
it governs cannot be told apart from one that is broken.

> **Windows, Git Bash only.** If you use Git Bash (MSYS), a resource that starts with `/`
> gets rewritten before Python ever sees it — `/market/snapshot` arrives as
> `C:/Program Files/Git/market/snapshot`, and it is recorded in your evidence that way with
> no error. Use PowerShell, `cmd`, or `MSYS_NO_PATHCONV=1`. Not an `tesoro` behaviour, but it
> silently corrupts the resource identifier, which is worse than an error.

## 5 · Govern the actual agent

Two lines. The governor wraps the agent, not the other way round:

```python
from tesoro import Governor

gov = Governor.load()                  # reads ./tesoro.yaml
agent = gov.wrap(my_agent)             # duck-typed; any framework, or none
```

Or call it directly if you would rather hold the payment yourself:

```python
decision = gov.authorize(
    amount_usd="2.50",                 # a string. Never a float -- passing one raises
    vendor="acme",
    resource="/market/snapshot",
)

if decision.approved:
    pay(...)                           # your payment call, on any rail
    gov.settle(decision, success=True)  # tell the layer what happened
else:
    print(decision.verdict, decision.attributed_control, decision.reason)
```

**`settle()` is where envelopes consume**, not `authorize()`. A decision is made before money
moves; a settlement records what actually happened. So an abandoned decision does not eat
budget — and if the amount paid differs from the amount quoted, pass
`actual_amount_usd=` and *that* is what counts against your limits. Skipping `settle()`
entirely means the layer knows what it authorized and never learns what happened: counterparty
trust never accumulates, and nothing is consumed.

Two more things in that snippet are load-bearing:

- **`amount_usd` is a string.** Money is integer atomic units internally, converted once at
  the boundary. A `float` raises rather than rounding quietly — `0.1 + 0.2` is not `0.3`, and
  there is no rounding this layer could pick that you would know about. An `int` is accepted
  and means **atomic units**, so `2500000` is `"2.50"`.
- **`decision.attributed_control`** is the field worth logging. Not *what* was decided —
  *which control decided it*. That is the question you will actually have at 2am, and the one a
  verdict alone cannot answer.

## 6 · See what happened

```bash
tesoro report
```

Or as one self-contained page you can open, keep, or attach to a ticket:

```bash
tesoro report --html -o spend.html
```

Four panels, one question each: **Policy** (what will this do?), **Envelopes** (how much is
left?), **Decisions** (why did my agent stop?), **Evidence** (can I trust this record?).

No server, no port, no listener, and no outbound request of any kind — no CDN, no webfont,
no analytics. It is a file. That property is asserted by test, because a page describing what
your agent spends should not phone anywhere.

The part worth reading is **by attributed control**:

```
by attributed control  -- what actually governed this agent
  policy                 2
  treasury               1
```

Counts by verdict tell you what happened. Counts by attributed control tell you what is
*actually* governing this agent, which is frequently not what whoever wrote the policy
expected.

## 7 · Verify the record

```bash
tesoro audit
```

Every decision is journalled in an append-only, hash-chained record. `audit` re-verifies the
chain and exits `3` if it does not hold.

**What that does and does not prove**, stated because a tool claiming "tamper-proof" would be
lying to you:

- **Detected:** editing an entry, deleting one from the middle, reordering.
- **Not detected:** deleting entries off the *end*. Any prefix of a valid chain is itself a
  valid chain, so an agent that was refused could delete the refusal and verification would
  still report success.

Closing that needs an external anchor. `tesoro` prints the caveat next to every `VALID`
rather than shipping a `head.json` beside the journal, which would *look* like a fix while
defending against nothing — whoever can truncate the journal can rewrite the file next to it.

## Where to go next

| | |
|---|---|
| [`docs/cli.md`](cli.md) | every command, every flag |
| [`docs/custom-policies.md`](custom-policies.md) | writing your own rules, and the two extension points |
| [`docs/api-surface.md`](api-surface.md) | the public API, and what is deliberately not public |
| [`docs/adapters.md`](adapters.md) | framework and rail adapters, and writing your own |
| [`docs/advisors.md`](advisors.md) | optional models, and why they can only ever tighten |
| [the standard](https://github.com/aegoll/aegs) | AEGS: the spec, its test vectors, and the conformance suite |

## Before you rely on this

`0.x`. The API will change before `1.0`, and this section is here rather than at the bottom
of a `CHANGELOG` because you are about to point it at a wallet.

More specifically: AML screening is a schema with no engine behind it, no regulatory
compliance is claimed or sought, and two attacks are open by design — **structuring** (many
payments below every limit) and **velocity evasion** (pacing exactly at a rate limit). Both
need a control that examines the *shape of a sequence*, and no amount of tightening an
envelope produces one.

The fairest available summary of this layer, from its own red-team suite: *it resists prose;
it did not resist a minus sign, and it does not yet resist patience.* The minus sign is
fixed and permanently vectored. Patience is still open.
