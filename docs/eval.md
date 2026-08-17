> **Canonical record: `research/experiments/EXP-001`**, sealed and checksummed.
> This page is the narrative version; the experiment is the evidence. If the two ever
> disagree, the experiment is right.

# D2 — how often does an advisor block something it shouldn't?

Phase 2 shipped with a conclusion it had not earned: that a *cheap* advisor is the
right default, because the EIAP break-even for Groq's 8b (~$0.0026 of exposure)
sits below what the seller charges. That argument shows consulting is **rational**.
It says nothing about whether the advice is **good**.

Two observations said it might not be. A live governed run blocked a legitimate
$0.01 purchase — Groq recommended REVIEW, the clamp turned APPROVE into REVIEW, and
the agent spent $0.0069 of tokens and bought nothing. An earlier test showed the
same advisor recommending REJECT on a routine $0.05 buy.

This measures it. Reproduce with:

```powershell
cd D:\learning-poc\x402\aegl
.venv\Scripts\python.exe -m aegl.cli eval                                    # free baseline
.venv\Scripts\python.exe -m aegl.cli eval --advisor gemini/gemini-flash-lite-latest
```

## Why false-blocks are the only thing worth counting

The clamp in `advise.py` lets an advisor tighten a verdict, never widen one. That
makes its failure mode one-sided:

- **False allow** — advisor says APPROVE on something dangerous. *Harmless.* The
  deterministic verdict stands; the advisor cannot loosen it. Worst case it adds
  nothing.
- **False block** — advisor says REVIEW/REJECT on something fine. *This changes the
  outcome.* A legitimate purchase is refused and the agent's task fails.

An advisor cannot make the system less safe, only less useful. So the metric is the
false-block rate on traffic that should pass. Bad cases are still run, as a
sensitivity check — but a model that blocks everything scores 100% there while
being worthless, which is why the two numbers are always reported together.

## The set

14 labelled cases in `aegl/evaluation.py`: 8 that should pass, 4 that should be
stopped, 2 genuinely ambiguous (reported, excluded from the rates). Every label
carries a written `rationale` so it can be argued with — an eval whose labels
cannot be inspected is not evidence.

Each advisor sees the same facts and the same prompt, with the EIAP gate forced
open so this measures advice quality rather than gate behaviour.

## Results

| Advisor | $/Mtok in·out | False-block | Caught bad | Ambiguous blocked | Cost / 14 | Per call | Latency |
|---|---|---|---|---|---|---|---|
| *(none — deterministic only)* | — | **0/8** | 3/4 | 1/2 | $0 | — | ~0ms |
| groq/llama-3.1-8b-instant | 0.05 · 0.08 | **8/8 (100%)** | 4/4 | 2/2 | $0.00068 | $0.000049 | 5.7s |
| groq/llama-3.3-70b-versatile | 0.59 · 0.79 | **4/8 (50%)** | 4/4 | 2/2 | $0.00791 | $0.000565 | 1.2s |
| anthropic/claude-haiku-4-5 | 1.00 · 5.00 | **5/8 (62%)** | 4/4 | 1/2 | $0.02399 | $0.001714 | 2.8s |
| gemini/gemini-flash-lite-latest | 0.10 · 0.40 | **0/8 (0%)** | 4/4 | 1/2 | $0.00171 | $0.000122 | 1.7s |

Mean tokens per call, measured on `flash-lite`: **582 in / 149 out** — see the gate-pricing bug below.

Total spent measuring: **~$0.037**, including the re-run that captured token counts.

`gemini-flash-lite` was run three times and returned **identical verdicts on all
14 cases** every time, which is the only stability evidence here.

## What it changes

**1. The earlier conclusion was wrong on its stated axis.** Price does not predict
advice quality. The $0.10/Mtok model scored perfectly; the $0.59 and $1.00 models
blocked half to two-thirds of legitimate traffic. Per call, `gemini-flash-lite` is
**4.6x cheaper than `llama-3.3-70b` and 14x cheaper than `claude-haiku-4-5`, and
strictly better than both** on this set. "Cheap advisors make AI analysis rational
at low exposure" survives; "so pick the cheapest" does not.

**2. `llama-3.1-8b-instant` is not an advisor.** It recommended REVIEW or stricter
on all 14 cases, including a $0.001 repeat purchase from a vendor with 12 clean
settlements. A component that never returns "fine" carries no information. Its 100%
catch rate is an artefact of that, not a strength. It must not be a default.

**3. There is a real case for consulting at all.** `repriced-25x` — a vendor that
sold the same endpoint eight times at $0.01 asking $0.25 — **passes every
deterministic engine**. It sits inside every envelope, so no amount threshold
fires. All four advisors flagged it. That single case is the argument for the
advisor layer existing: it catches a *semantic* signal the numbers do not express.

**4. Ambiguity discrimination separates the good models from the loud ones.** Both
Groq models blocked both ambiguous cases; `flash-lite` and Haiku blocked one and
let the other through. Blocking everything ambiguous is not caution, it is absence
of judgement.

## The bug this measurement found

`advise.py` hardcoded `vendor_settled_count=0`. **Every advisor call ever made** —
including the live run that blocked the legitimate $0.01 purchase — was told the
counterparty had zero settled transactions, whatever its actual record. The models
were reasoning correctly on fabricated facts, and said so:

> "The vendor has a low number of settled transactions (0) and a relatively low
> trust score (0.797), which suggests a need for closer review."
> — on a vendor with **12** clean settlements

> "The vendor has no settled transactions, which raises concerns about their
> reliability."
> — on a vendor with **30** clean settlements over 90 days

Fixed by plumbing the `HistorySnapshot` through `consult()`, which also lets the
advisor see failures, disputes, relationship age, and the vendor's own historical
price for the resource — the last being what makes `repriced-25x` catchable.

The general lesson is in `tests/test_advisors.py`: **an unmeasured value rendered
as `0` is not a missing fact, it is a wrong one**, and a model cannot tell the
difference. `vendor_settled_count` is now `int | None` and renders as `unknown`.
Four regression tests hold the plumbing in place.

Fixing this did **not** change the headline: Groq 8b scored 100% false-block both
before and after. The fabricated history was a real defect that made every model's
input wrong; it was not what made the 8b unusable.

## A second bug: the gate was priced 3x too high

The eval also measures tokens per call. `gemini-flash-lite` averaged **582 input /
149 output**. The constants pricing the EIAP gate were `TYPICAL_INPUT_TOKENS = 2000`
/ `TYPICAL_OUTPUT_TOKENS = 400` — guessed before anything had been run, and **3x
too high**.

That is not harmless, because break-even is `cost_ai / p_flip`. A 3x-high cost
estimate makes break-even 3x too high, so the gate refuses to consult on
transactions where consulting would pay. It was caught doing exactly that in a live
governed run:

```
skipReason: exposure $0.010000 is below the $0.019364 break-even for
            gemini-flash-lite-latest; consulting would destroy value
```

The true break-even was nearer $0.006. Corrected to 600/250 — input measured,
output set deliberately above the measured mean because chattier models exist and
over-estimating cost errs toward *not* spending:

| Advisor | Break-even before | Break-even after |
|---|---|---|
| groq/llama-3.1-8b-instant | $0.0080 | **$0.0027** |
| gemini/gemini-flash-lite-latest | $0.0194 | **$0.0086** |
| groq/llama-3.3-70b-versatile | $0.0891 | **$0.0297** |
| anthropic/claude-haiku-4-5 | $0.2984 | **$0.0995** |

Verified live afterwards: the same $0.01 purchase now opens the gate, the advisor
is consulted for **$0.000112**, and it returns APPROVE at 0.95 confidence citing
*"matches the historical price for this resource. The vendor has 15 settled…"* —
which also confirms the fact-plumbing fix above is reaching a real model.

Both bugs pushed the same way: **the advisor layer was doing less than it appeared
to.** One fed it fabricated history; the other stopped it being asked at all.

## Recommended defaults for `aegl.plugin` (B2)

| Setting | Value | Why |
|---|---|---|
| Default advisor | `gemini/gemini-flash-lite-latest` | 0/8 false-blocks, caught the reprice, cheapest per call measured |
| Advisor enabled by default | **yes**, behind the EIAP gate | It catches something the engines cannot, at $0.00012 a call |
| `llama-3.1-8b-instant` | selectable, not default; warn in the UI | Measured unusable as an advisor |
| Clamp | unchanged | It is what makes a bad advisor merely useless instead of dangerous |

## Limits of this result

Stated plainly, because the numbers above look tidier than they are:

- **14 cases, one run each** (two for `flash-lite`). Small enough that one case is
  12.5% of a rate. This is enough to exclude the 8b and to kill "price predicts
  quality" — not enough to certify `flash-lite` at 0%.
- **The labels are mine.** They are argued in the code, and the eight "good" cases
  are deliberately unambiguous, but they are one person's judgement.
- **One prompt.** Whether Haiku scores badly because of its judgement or because
  the prompt suits it poorly is not separated here. The prompt was deliberately
  *not* tuned after seeing these results — that would be fitting to the test set.
- **`-latest` aliases drift.** `gemini-flash-lite-latest` points at whatever Google
  currently serves; this result has a shelf life, and re-running is cheap.
- **Nothing here measures the EIAP gate's *judgement*.** The gate was forced open
  throughout, so these numbers say nothing about whether it consults at the right
  moments — only that it was pricing those moments wrongly, which is now fixed.
- **Output-token length varies by model**, so one global `TYPICAL_OUTPUT_TOKENS`
  cannot be right for all of them. 250 is a compromise set above the measured mean.
  A per-model figure would be better and is not built.
