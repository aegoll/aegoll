# Test vectors

Language-neutral, executable, and the reason a second implementation is possible at all.

One JSON file per vector: an input, an expected output, and **the clause it checks**. Both
halves of that last part matter — a vector with no clause is a test of somebody's
implementation, and a clause with no vector is a wish.

```
vectors/
  arithmetic/   atomic conversion, rounding, boundaries, negatives, overflow
  envelopes/    headroom, cumulative vs per-call, windows          (not yet written)
  verdicts/     narrowing, attribution, evaluation order            (not yet written)
  evidence/     record projection, canonical serialisation, chains  (not yet written)
```

---

## Why vectors rather than a conformance suite alone

[`../conformance/`](../conformance/) scores an implementation end to end: set up a scenario,
make a decision, check the Decision Record. That is the right shape for *"does this system
govern correctly"*, and the wrong shape for *"do these two systems agree on what
`0.0000005` rounds to"*.

Vectors are for the second question. They are deliberately small enough that an implementer
can run them against a half-built implementation on day one, before there is anything to
score. Concretely: the two vulnerabilities the reference implementation shipped with — a
`-$1000` payment that was **approved**, and a 30-digit amount that **crashed** the layer —
are vectors here. A new implementation cannot repeat either without a red test.

## File format

```json
{
  "aegsVersion": "0.1",
  "id": "arithmetic/negative-simple",
  "clause": "AEGS-0.1-ARITH-4",
  "operation": "usd_to_atomic",
  "description": "A negative amount is refused rather than normalised.",
  "input": { "amount": "-0.01", "decimals": 6 },
  "expect": { "refused": true, "reason": "negative" }
}
```

| Field | Required | Meaning |
|---|---|---|
| `aegsVersion` | yes | the spec version this vector belongs to |
| `id` | yes | `<family>/<name>`, matching the file path |
| `clause` | yes | the normative clause this checks. **A vector without one does not belong here** |
| `operation` | yes | which operation is under test |
| `description` | yes | one sentence, in the imperative |
| `input` | yes | operation-specific |
| `expect` | yes | either a value or a refusal |
| `note` | no | why this vector exists, when that is not obvious |

`expect` takes one of two shapes:

```json
"expect": { "atomic": 2500000 }              // a value
"expect": { "refused": true, "reason": "negative" }   // a refusal
```

**`reason` is a category, not a message.** An implementation's wording is its own business;
what a vector checks is that it refused *for the right kind of reason*. The categories are
`negative`, `overflow`, `nonfinite`, `unparseable`. Checking exact strings would make this
suite a test of one implementation's vocabulary — which is precisely what a conformance suite
must not be.

## Operations

Currently one, because arithmetic is the first family written.

### `usd_to_atomic`

Convert a decimal amount to an integer count of atomic units.

```json
"input": { "amount": "0.0000005", "decimals": 6 }
"expect": { "atomic": 1 }
```

`amount` is **always a string**, including for integers. A vector that wrote `2.5` as a JSON
number would have already lost precision before any implementation read it, which would make
the suite unable to test the very thing ARITH-9 requires.

### `atomic_to_usd`

The inverse: an integer count of atomic units to a decimal string at full precision.

```json
"input": { "atomic": 2500000, "decimals": 6 }
"expect": { "usd": "2.500000" }
```

## Running them

Each implementation runs the vectors in its own test suite, in its own language. There is no
shared runner, on purpose: a runner would be code the implementations share, and two
implementations sharing code prove less about the specification than two that only share
data.

The reference implementation's runner is about forty lines
(`tesoro/tests/test_vectors.py`), which is roughly the intended size.

```
python tools/check_vectors.py     # validate the vectors themselves
```

## Alignment with upstream

The x402 project is building [a conformance-vector set of its own](https://github.com/x402-foundation/x402/pull/2776)
— 52 vectors with a `schema.json` and a dependency-free runner, already covering *spending
limits* and *authorization bypass*.

Field names here follow theirs where the concepts line up, and this suite deliberately
answers one of the open review concerns on that PR: **every vector names its specification
version and clause.** If their format merges, this one aligns to it rather than the reverse
— an ecosystem with two vector formats is worse for everybody than one with a format nobody
here chose.

## Adding a vector

1. Pick or write the clause it checks. If no clause covers the behaviour, the specification is what needs changing first.
2. Name it `<family>/<what-it-checks>`.
3. State the expected value or the refusal category. Never a message.
4. Add a `note` if the reason it exists is not self-evident — especially if it exists because something once went wrong.
5. `python tools/check_vectors.py` must pass, and CI runs `tools/lint_normative.py` to confirm every MUST still has a vector pointing at it.
