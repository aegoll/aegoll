# Behavioural monitoring — design

> **Status: Layer 1 shipped. Layer 2 deliberately not built. The premise below was wrong in one
> important way and is corrected in place.** A11.3.
>
> **What this document got right:** the two open economic findings share one cause — nothing
> bounded *count* over a window longer than an hour; the fix is a count envelope rather than a
> heuristic; and it would close paced evasion while only *bounding* structuring. All three held.
> `actions_per_day: 500` and `actions_per_month: 10000` now ship in the default pack, paced
> evasion moved from undefended to defended, and structuring did not
> ([EXP-010](https://github.com/aegoll/aegs/tree/main/research/experiments/EXP-010)).
>
> **What it got wrong:** it treated the count envelope as something the standard could not
> express, and recommended an AEGS 0.2 clause family on that basis. **AEGS 0.1 already specifies
> count envelopes — [ENV-7](https://github.com/aegoll/aegs/blob/main/spec/03-envelopes.md) — and
> tesoro already implemented two of them, `velocity_60s` and `velocity_1h`.** The gap was a
> *window*, not a control and not a clause. A 0.2 section was drafted and thrown away.
>
> The cause is worth recording because it is the fourth instance in this project: the conclusion
> came from grepping clause *families*, seeing `ENV`, and never reading ENV-7. ENV-7's own note
> then said *"a count limit constrains rate, not total"* — true of the short windows anyone had
> used, false of the mechanism — which pointed away from the answer it contained. That sentence is
> now corrected in the specification.
>
> The design reasoning below is left as written, because it was sound and reaching the wrong
> destination from sound reasoning is the part worth being able to re-read.

## What this has to close

Two of the three open red-team findings, both economic:

| | Attack | Measured |
|---|---|---|
| **RT-ECON-001** | microtransaction structuring | $0.040000 across 40 payments, one every five minutes. Nothing refused |
| **RT-ECON-004** | paced evasion | $0.200000 across 200 payments over 2.05 hours at 97/hour — 3% under the `velocity_1h` ceiling of 100. Nothing refused |

The third open finding, journal truncation, is unrelated and waits on A11.6.

## They have one cause, and it is not a loose threshold

Stated first because it determines everything else.

tesoro bounds **value** — `daily_usd`, `monthly_usd`, `per_vendor_30d_usd`,
`per_resource_30d_usd`. It bounds **rate** — `velocity_60s: 10`, `velocity_1h: 100`. It does not
bound **the number of actions over any window longer than an hour**.

- Value envelopes are unreachable when each action is trivial. $0.001 × 288/day is $0.29 against
  a $50 budget.
- Rate counters are unreachable when the pacing sits under them. 97/hour never trips a ceiling of
  100.
- Nothing multiplies *trivial* by *many* and compares the product to anything.

**Neither is a threshold set too loosely.** Lowering `daily_usd` does not help: $0.04 is not near
$50 at any value an agent could operate under. Lowering `velocity_1h` does not help: the attacker
paces under whatever it is. The quantity these attacks grow is one no control reads, which is what
makes them structural rather than parametric.

A corollary that constrains the fix: **if the new control closes these by tightening an existing
envelope, it has not closed them.** [EXP-009's report](https://github.com/aegoll/aegs/tree/main/research/experiments/EXP-009)
records that prediction — both attacks must become `DEFENDED` attributed to the *same new control*,
and an attribution to `treasury` means a threshold moved and the attacks should be re-parameterised
until they are out of reach again.

## What the structuring attack actually damages

The obvious reading is that structuring moves money invisibly. Measured against this layer, that
reading is wrong, and the real effect is worth building around instead.

**Its monetary harm is already bounded.** Whatever the pacing, the value envelopes cap the total:
at $0.50 × 100 actions the daily envelope binds, attributed to `treasury`. An attacker choosing
amount and pacing to maximise extraction runs into `daily_usd` exactly as intended.

**Its real effect is on the risk baseline, and it is severe.** `HistorySnapshot.agent_amounts` is
built as:

```sql
SELECT amount_atomic FROM transactions
WHERE agent_id=? AND channel=? AND settled=1 AND success=1
ORDER BY at DESC LIMIT 200
```

That is a **count** window, not a time window. Measured:

| Agent history | rows in baseline | stdev | `amount_zscore` |
|---|---|---|---|
| 40 varied purchases, $0.02–$2.00 | 40 | $0.537625 | 15.26 for a $9.00 request |
| the same 40, then 200 × $0.001 | **200** | **$0.000000** | **6.0** |

The forty real purchases are **gone** — pushed out of the window by volume alone. And with
`stdev == 0`, `amount_zscore` returns a hardcoded `6.0`:

| request | z |
|---|---|
| $0.01 | 6.0 |
| $1.00 | 6.0 |
| $100.00 | 6.0 |
| $100,000.00 | 6.0 |

**The function stops measuring anything.** Every amount is equally anomalous.

**It fails safe, and that is why nobody noticed.** `zscore_saturation` is 4.0, so a z of 6.0 pins
the z term at its maximum — the conservative direction. Verdicts do not loosen: $0.05 and $0.30
still approve, $1.00 still reviews, identically before and after. This is not an escape. It is an
input weighted `weight_zscore: 0.20` silently becoming a constant, so the risk score carries less
information than its weights imply, and an operator reading `risk.score` has no way to tell.

Two consequences for this design:

1. A count envelope is needed for its own sake, not only for the attacks.
2. **`agent_amounts` needs a time bound as well as a count bound**, and `amount_zscore` needs to
   stop returning a magic number when it has no dispersion to report. Both are smaller than a new
   engine and neither belongs to it — recorded here so they are not lost, but they should ship
   separately.

> **Both shipped separately, as A11.3a and A11.3b.** The baseline is bounded by time first (30
> days, matching every other aggregate) with the row cap kept as a memory guard and **raised to
> 1,000 — and hitting it now sets `baseline_truncated`**, because a statistic over the most recent
> N actions is not the statistic it appears to be. The 40-varied-plus-200-trivial case now retains
> all 240 amounts and the z-score measures again.
>
> `amount_zscore` returns `None` whenever there is no dispersion to divide by, and
> `dispersion_state()` distinguishes the three reasons it can have nothing to say:
> `no_baseline`, `no_spread`, `measured`. The information the fabricated `6.0` was carrying moved
> to `differs_from_flat_baseline()`, and `risk` still scores that case at **exactly 1.0** — the
> same contribution `min(1.0, 6.0 / 4.0)` produced. **No verdict changed.** Collapsing the
> zero-dispersion case into the `no baseline` branch would have dropped the term to 0.0 and
> *relaxed* the verdict, which is the wrong direction and is now a test.

## Two layers, and only the first is load-bearing

### Layer 1 — count envelopes. Deterministic, and the actual fix

The existing envelope idea applied to a quantity it currently ignores. New treasury keys:

```yaml
config:
  treasury:
    actions_per_day: 500
    actions_per_month: 10000
    actions_per_vendor_30d: 1000
    actions_per_resource_30d: 1000
```

Same arithmetic as the value envelopes, same `binding` / `tightest` reporting, same narrowing-only
rule, same attribution path. Nothing new conceptually — which is the point. It is deterministic
integer comparison, it invents no heuristic, and it converts *unbounded* into *bounded by a number
the operator chose*, which is the identical guarantee the value envelopes already give.

**What it does not do:** stop the specific 40-action structuring run. 40 actions is not near 500 at
any setting a real agent could work under. Layer 1 bounds the *mechanism*, not the instance —
2,328 actions a day becomes impossible, and 40 stays permitted, because 40 trivial purchases is
also what a legitimate agent does.

That limit is worth stating plainly rather than hiding: **a count envelope closes RT-ECON-004 and
bounds RT-ECON-001 without refusing it.** The red-team baseline would move one attack, not two, and
the honest thing is to say so before building rather than to discover it in the after-comparison.

### Layer 2 — sequence shape. A heuristic, `REVIEW` only, and possibly not worth building

For what Layer 1 bounds but does not catch: a *regular* sequence. Structuring is periodic by
construction — fixed amount, fixed interval — and task-driven spend is not.

The deterministic signal is dispersion of the inter-arrival intervals and of the amounts:
coefficient of variation over a window, compared against a configured floor. Low variation in both,
sustained over a minimum count, is the structuring signature.

**The problem with it, which must be settled before any code:** a monitoring agent that buys a
market snapshot every five minutes is periodic *by design*, and it is the most obvious legitimate
use of an agent with a wallet. The signature does not distinguish structuring from a polling loop,
because there is no difference in the sequence — the difference is in the intent, and inferring
intent needs a model, which invariant 1 forbids in the decision path.

So Layer 2 can only ever produce `REVIEW`, never `REJECT`, and its value is entirely in whether an
operator finds the review useful or turns it off within a week. **My recommendation is to build
Layer 1, ship it, and leave Layer 2 unbuilt until someone reports wanting it.** A control everyone
disables is worse than an absent one: it appears in the conformance surface and defends nothing.

## The fact surface does not support Layer 2 today

Worth knowing before committing to it. `HistorySnapshot` carries:

```
spent_today_atomic · spent_month_atomic · spent_vendor_30d_atomic · spent_resource_30d_atomic
count_last_60s · count_last_1h · agent_amounts · vendor
```

`agent_amounts` is a bag of values **with no timestamps**. `count_last_60s` and `count_last_1h` are
scalars. Nothing in the snapshot can answer *what were the intervals between the last N actions* —
so Layer 2 needs a new fact that is a time series, which means a store query, a snapshot field, and
a new entry in `build_facts`. Layer 1 needs none of that: it is four `count()` calls beside the
four `spent()` calls that already exist.

That asymmetry is the strongest argument for the phasing above. Layer 1 is a day's work against
existing machinery; Layer 2 changes the shape of the fact base for a control that may get switched
off.

## Attribution and evaluation order

A new control needs a name in the attribution vocabulary. `behaviour` is the obvious candidate for
Layer 2. **Layer 1 should be attributed to `treasury`** — it is a treasury envelope over a different
quantity, and inventing a second control name for the same arithmetic would make
`by attributed control` less informative, not more.

That collides with EXP-009's prediction that closing these must not attribute to `treasury`. The
collision is real and this is the resolution: the prediction exists to catch a *threshold* being
tightened. A new envelope over a quantity nothing previously counted is a new control that happens
to live in treasury. The after-comparison must therefore check the envelope *name* — a refusal
citing `actions_per_day` is the new control; one citing `daily_usd` is a moved threshold. The
prediction in EXP-009's report should be restated in those terms when the work lands, in a
superseding record rather than an edit.

Invariant 7 applies: evaluation order is normative because attribution is order-dependent even
though the verdict is not. A count envelope evaluated inside treasury inherits treasury's position
and needs no ordering decision. Layer 2, if ever built, does — and it must sit where a `REVIEW` it
raises cannot displace a `REJECT` another control would have produced.

## The AEGS question — and it needs deciding before any code

A11.8 is binding: **each new engine gets vectors in `aegs/vectors/` before it gets an
implementation.** Vectors cite clause ids. AEGS 0.1 has 90 clauses and none of them describe a
count envelope or a sequence-shape control. So this work implies one of:

**(a) AEGS 0.2 — a count-envelope clause family.** Vectors cite real clauses, conformance can score
it, and a second implementation can be measured on it.

**(b) A tesoro-only control with no conformance claim.** Ships faster; the standard stays at 0.1.

**Recommendation: (a), for Layer 1 only.**

A count envelope is exactly the kind of thing a standard should carry — it is arithmetic over a
declared number, it has no heuristic content, and every AEGL that bounds value has the same hole.
More decisively: W6.4 is already this project's largest open question, and a control the standard
cannot express is a control no independent implementation can ever be scored on. Adding
tesoro-only controls widens the gap between what tesoro does and what AEGS can measure, which is
the one direction this project should not drift.

Layer 2 should stay out of the standard whether or not it is built. A heuristic with a
false-positive mode that depends on an operator's workload is not something to require of a
conforming implementation.

## What this cannot do

- **The regress is real.** Bound count per day and an attacker paces under that. Every threshold
  has the same property, and the honest claim is not "evasion is impossible" but "evasion is
  bounded by a number the operator chose" — which is what a governance layer offers and is
  precisely what these two findings currently lack.
- **Layer 1 does not refuse the measured structuring run,** and the docs must keep saying
  structuring is not fully defended after it ships. Bounding a mechanism is not closing an attack.
- **Layer 2 cannot distinguish structuring from polling,** because the sequences are identical and
  the difference is intent.
- **Neither layer addresses the baseline erosion measured above.** That is a separate fix to
  `agent_amounts` and `amount_zscore`.
- **Nothing here is measured against a real workload.** Every number in this document comes from a
  synthetic attack suite written by the author of the layer.

## Before code

1. Decide (a) or (b) above. Everything else waits on it.
2. If (a): draft the AEGS 0.2 clause family, then vectors, then the engine. A11.8, in that order.
3. Write the red-team predictions into the plan *before* implementing, so the after-comparison is
   a test rather than a narrative — RT-ECON-004 → `DEFENDED` citing `actions_per_day`;
   RT-ECON-001 → still `UNDEFENDED`, and the four documents that say so stay accurate.
4. Fix `agent_amounts`' missing time bound and `amount_zscore`'s `6.0` sentinel separately, each
   verified by reintroducing the bug.
5. Leave Layer 2 unbuilt.
