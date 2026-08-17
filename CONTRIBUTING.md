# Contributing to `aegoll`

Apache-2.0. By contributing you agree your work is licensed under it.

This is a governance layer that sits next to a wallet. The bar is higher than for a normal
library, and most of that bar is expressed as tests rather than as review comments.

---

## The invariants

A PR that weakens any of these will be declined regardless of how good the rest of it is.
Each has a test; if you find a way past one, that is a bug report worth more than the PR.

1. **No model in the decision path.** Nothing outside `advisors/` may import an LLM client.
   The advisor is optional, gated, and *clamped* — it can tighten a verdict, never widen one.
2. **Policy is data, never code.** Declarative rules, a fixed comparator vocabulary, no
   `eval`, no import hooks. This is a security boundary, not a style preference.
3. **Money never touches a float.** Integer atomic units internally. A `float` at the
   boundary raises `TypeError`.
4. **Absent ≠ not-run ≠ unknown ≠ zero.** Four states. `None` for a limit means the limit
   does not exist; `0` refuses everything. Never render one as the other.
5. **Every control may only narrow.** No engine may widen a verdict another one set.
6. **Two channels never share an envelope.** `internal` and `external` are different money.
7. **Core is pure.** No filesystem, no network, no wall clock inside `engines/`. The clock is
   injected.
8. **The governor wraps the agent; the agent never imports the governor.**
9. **BYOK keys are never stored, logged, or journalled.** A masked display path is the only
   way a key is shown.
10. **Evidence is append-only and hash-chained.** Its one known gap is documented, not
    patched with something that looks like a fix.

## What a good PR looks like

- **One behaviour change, one PR.** A port and a rewrite in the same commit hides which is which.
- **A test that fails before and passes after.** For a bug, the test reproduces it first.
- **A purity test if you added a module.** New code inside `engines/` must be covered by the
  tree-walking purity checks, not by a hardcoded filename list. The prototype learned this
  the hard way: a refactor turned the checked files into three-line shims, and the test kept
  passing while checking nothing.
- **A vector, if the change affects a verdict, an envelope, or a hash.** Vectors live in
  [`aegs/vectors/`](https://github.com/aegoll/aegs) and are language-neutral on purpose.
- **A note in `PLAN.md`** — tick the task, and add to that section's **Findings** if you
  learned something that changes the plan. A plan with the wrong turns removed is not a plan.

## Public API

The public surface is [`docs/api-surface.md`](docs/api-surface.md), and **nothing outside it
is public** regardless of what Python lets you import. Adding a symbol to that document is a
deliberate act with a compatibility cost; adding one without is not an API change, it is an
accident waiting to be depended on.

Read §8 of that document before changing any signature. Renaming an `attributed_control`
value is a **major** change, because conformance scoring depends on it and a rename silently
changes someone's conformance result.

## Errors and refusals

**A refusal is not an exception.** `authorize()` returns a `Decision` with
`approved == False`. If you find yourself raising on a refusal, that is the wrong shape — a
library whose normal operation throws teaches people to wrap it in `except: pass`, and here
that means an ungoverned agent.

## Measurements

Any claim about latency, cost or effectiveness needs a **sealed experiment record** stamped
with the policy hash, the package version and the spec version. Sealed records are
**superseded, never edited** — a correction is a new record with `supersedes` pointing back.

Do not quote a number without the limitations that go with it. Every measurement so far is
one run or a handful, single-agent, on testnet.

## Security

Report anything that gets past an invariant privately first, not as a public issue. Two real
vulnerabilities in the prototype were a **negative amount** (every envelope asked
`amount <= headroom`, which any negative satisfies, so the sign inverted the whole treasury)
and a **30-digit amount that crashed** the layer rather than being refused by it. Both are
permanent test vectors now. Attacks of that shape are exactly what this project wants to hear
about.

## Commits

Present tense, imperative, one subject line that says what changed and why it matters. No
attribution trailers.

Porting commits from the proof-of-concept carry `Ported-from: Jayzilva/x402@<sha> <path>` and
copy the file faithfully; changes come in a later commit.

## Style

Match the surrounding code. It favours explicit names over short ones, comments that explain
*why* rather than *what*, and frozen dataclasses over mutable state. Where a comment exists
in the prototype explaining a decision, keep it — it is usually load-bearing.
