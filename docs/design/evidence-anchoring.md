# Evidence anchoring — design

> **Status: design only. Nothing described here is implemented.** A11.6. No code, no config, no
> protocol. What tesoro does today is *disclose* the gap — `Report.chain.caveat`,
> `Governor.verify()`'s docstring, and the docs site all say truncation is undetectable.
>
> Written before the code because the last two designs in this directory were built on premises
> that turned out to be wrong, both times because something already existed and had not been
> read. So: [AEGS-0.1-EVID-6](https://github.com/aegoll/aegs/blob/main/spec/07-evidence.md) has
> been read in full, it is quoted below, and it settles more of this than expected.

## What the standard already settles

EVID-6 requires the **disclosure**, not the fix:

> An implementation **MUST NOT** claim that its evidence is tamper-proof […] Where an
> implementation's chain has no external anchor, it **MUST** state that truncation of the
> journal's tail is undetectable.

and it already names both the fix and the trap:

> Fixing it needs an external anchor — a value published somewhere the agent cannot reach,
> committing to the chain's length and head. That is a real deployment cost, and an
> implementation may reasonably decide not to pay it. What it may not do is describe the result
> as tamper-proof.
>
> **What does not work, and looks like it does:** writing the current head and length to a second
> file beside the journal.

Three consequences worth stating before any design:

1. **tesoro is already conformant.** The gap is disclosed in the report, the API docstring and
   the published documentation. Nothing here is required to reach conformance.
2. **The commitment is to length *and* head**, not head alone. That is EVID-6's wording and it is
   load-bearing — see below.
3. **No clause specifies what an anchor is**, so an anchor cannot be conformance-scored and no
   test vector has anything to cite. That is a decision to take, not a gap to paper over.

## The gap, stated once

Any prefix of a valid chain is itself a valid chain. Remove the last N entries and every link
still verifies, every hash still matches, and `verify()` returns success. **An agent that was
refused can delete the refusal, and the evidence is internally consistent about a history that
did not happen.**

Editing is caught (content-hash mismatch). Middle-deletion is caught (sequence gap). Reordering is
caught. Truncation is not, and no amount of internal linking fixes it, because the missing
information is *that there was more*.

## The threat model, which is the whole design

Everything below follows from being precise about one question: **what can the adversary write?**

| | |
|---|---|
| **Adversary** | the agent, or anything with the host's write authority over the journal |
| **Can** | append, rewrite, delete, truncate the journal; rewrite any file the host can write; choose *when* to do it, including immediately after a refusal |
| **Cannot** | alter a value already accepted by something outside the host's write authority |
| **Motive** | remove the record of a refusal, so the evidence describes a run in which nothing was refused |

The adversary is inside the host. That single fact disqualifies every anchor that lives inside it,
and it generalises the `head.json` trap into a rule worth stating:

> **An anchor within the writer's authority is not an anchor.** If the same authority that can
> truncate the journal can also update the thing that attests to its length, the attestation is
> part of the same document. `head.json` is the obvious instance; a second table in the same
> SQLite file, a hash in a sibling log, and a length counter in the journal's own header are the
> same mistake wearing different clothes.

A11.7 forbids `head.json` specifically. The rule above is what it is an instance of, and the rule
is the thing to test proposals against.

## What an anchor must commit to

`(sequence_length, head_hash)`, together, at a point in time the adversary cannot move.

**Why length as well as head.** Head alone detects a *changed* history: if the published head does
not match the local head, something is wrong. It does not distinguish the two ways that can
happen, and the distinction is the whole diagnosis:

| local vs published | means |
|---|---|
| local length < published length, local head is a prefix of the published chain | **truncation** — entries were removed |
| local length ≥ published length, heads disagree | **fork or edit** — the chain diverged from what was attested |
| local length = published length, heads agree | consistent as far as the last publication |

Reporting "chain does not match its anchor" for all three would be true and nearly useless. An
operator needs to know whether records were deleted or altered.

## The window — what an anchor does not close

**This is the section that matters, and it is the one a reader will skip.**

An anchor is published at some cadence: every N entries, every T seconds, or on some event.
Everything appended *since the last publication* is unattested, and therefore still silently
truncatable.

So an anchor does not make truncation detectable. It makes truncation **detectable beyond a
bound the operator chose** — at most N entries, or T seconds of activity. That is the same shape
as the count-envelope result in
[behavioural-monitoring.md](behavioural-monitoring.md): *unbounded* becomes *bounded by a number
someone picked*, which is what a governance layer has to offer and is a genuine improvement over
nothing.

Two consequences that must reach the documentation, not just this file:

- **The adversary's best move is to truncate immediately.** A refusal followed instantly by
  truncation, before the next publication, is undetected under any cadence. Publishing *on every
  refusal* narrows this specific case to near zero and is worth doing for that reason alone — a
  refusal is rare, so the cost is low and it is exactly the entry worth attesting.
- **"Anchored" must never be reported as "tamper-proof".** EVID-6's MUST NOT does not relax once
  an anchor exists, and the caveat text must change rather than disappear: from *truncation is
  undetectable* to *truncation is undetectable within the last N entries*.

## Candidate sinks, and what each actually guarantees

Ordered by honesty of the guarantee, not by convenience.

| Sink | Guarantee | Cost |
|---|---|---|
| **Append-only file on a filesystem the process cannot rewrite** — different uid, `chattr +a`, an append-only network mount | **Exactly what the filesystem enforces.** On a default single-uid Linux host or an ordinary Windows box: *nothing*, because the agent's own user can rewrite it | none, and that is the problem — it is `head.json` unless the deployment genuinely restricts the writer |
| **Operator-controlled HTTP endpoint that only appends** | The endpoint's, provided the agent holds no credential that can delete | a service to run, and a credential the agent must hold to write but not to delete |
| **Object store with retention lock** (S3 Object Lock, equivalent) | Strong: the platform refuses deletion until the retention window elapses | an account, a bucket policy, and per-write cost |
| **Transparency log** (Rekor-style) | Strong, and third-party verifiable — someone other than the operator can check | an external dependency, and the head becomes public |
| **The payment rail itself** — head hash in an x402 settlement memo | Strong where it applies: on-chain, and the agent cannot retract a settled transaction | nearly free for an agent already paying on-chain, **but it anchors only when a payment settles, and a refusal produces no payment** — so the entries the adversary most wants to delete are the ones this never attests |

The rail option deserves the flag it gets: it is attractive, nearly free, and structurally blind to
exactly the case that motivates the whole feature. Naming it here so nobody re-derives it as an
obvious win later.

**Recommendation: define the protocol, ship no bundled sink, document these five with the table
above.** The failure mode to avoid is shipping the append-only-file sink as *the* anchor, because
in most deployments it is inside the writer's authority and would recreate `head.json` at one
remove — with a config key making it look solved. A protocol plus an explicit "you must supply a
sink whose guarantee you can state" is more honest than a default that is usually vacuous.

The counter-risk is real and is why this is a recommendation rather than a decision: a protocol
with no implementation is the F-A12 shape — a documented thing that does not exist. Mitigation:
ship the protocol, the verification logic, the reporting changes, and a **test double**, so
everything except the deployment-specific sink is real, tested, and usable by anyone who writes
twenty lines.

## Interface sketch

Duck-typed, like `RunGuard` and `PaymentClient` — nothing here should require importing tesoro to
implement.

```python
class Anchor(Protocol):
    def publish(self, length: int, head: str) -> str | None:
        """Commit to (length, head). Returns a receipt, or None if it could not."""

    def latest(self) -> tuple[int, str] | None:
        """The highest (length, head) this sink holds, or None if it holds nothing."""
```

Two calls, no lifecycle, no async. `latest()` returning `None` means *the sink is empty*, which is
different from *the sink could not be reached* — and that difference is the next section.

## The failure mode that must not fail open

If the sink is unreachable at verification time, the result **must not** be "valid".

This project has shipped that mistake twice: `validate()` returned bare `False` for "could not
check", making seven tests fail rather than skip; and the cockpit's governance shim caught
`ModuleNotFoundError` for a UI module and reported the *governance layer* as unavailable, running
ungoverned. Both were the same error — collapsing *unknown* into a definite answer.

So anchored verification has **four** outcomes, not two, and they map onto the four-states rule:

| Outcome | Meaning |
|---|---|
| `consistent` | the local chain matches the anchor, and the anchor was read |
| `truncated` | the local chain is shorter than what was attested |
| `diverged` | lengths allow it but the heads disagree — edited or forked |
| `unknown` | the sink could not be read. **Not a pass.** The chain's internal verification result still stands on its own, and the anchored claim is simply unavailable |

`verify()` keeps its current meaning and its current caveat. Anchored verification is a *separate*
call with its own result type, because a caller that never configured an anchor must not silently
start receiving a weaker or stronger claim than it asked for.

## The AEGS question

**No clause specifies anchor semantics.** EVID-6 requires disclosure and describes the fix in
prose; nothing says what an anchor commits to, what the failure states are, or how verification
reports them. So an anchor cannot be conformance-scored, and a test vector has no clause to cite —
which collides with A11.8's rule that vectors come before implementation.

Two options:

**(a) An `EVID-6a` clause, `MAY`, modelled exactly on `ENV-7`.** *An implementation MAY publish an
external anchor. Where it does, it MUST commit to length and head; the anchor MUST be outside the
authority that writes the journal; and verification against it MUST distinguish truncated,
diverged and unknown.* That gives vectors something to cite, lets a future profile require it, and
lets an independent implementation be measured on it.

**(b) tesoro-local, no clause.** Ships sooner, scores nothing.

**Recommendation: (a).** The argument is the one the count-envelope round proved the hard way — a
control the standard cannot express is a control no independent implementation can ever be scored
on, and W6.4 is already this project's largest open question. It also has a precedent that
worked: ENV-7 is a `MAY` whose semantics are fixed, which is exactly why closing paced evasion
needed no new clause at all.

Note the asymmetry with that round, because it changes the order of work: count envelopes were
already specified and only needed implementing. This is the reverse — the mechanism is unspecified,
so the clause genuinely has to come first.

## What must not be claimed afterwards

- Not "tamper-proof". EVID-6's MUST NOT is unconditional.
- Not "truncation is detected". Truncation is detected **beyond the last publication**.
- The caveat text changes rather than disappearing, and an unconfigured anchor leaves it exactly
  as it reads today.
- The three existing truncation vectors (`evidence/truncation-*`) stay valid and stay passing.
  They test a chain with no anchor, which remains a legitimate and conformant configuration.

## Before code

1. Decide (a) or (b). If (a), the clause and its vectors come first — A11.8, and this time the
   mechanism really is unspecified.
2. Write the four-outcome verification and its reporting before any sink exists, with a test
   double. Fail-open is the one defect that would make this feature worse than its absence.
3. Publish on every refusal, plus a cadence, and make the cadence visible in the report.
4. Update the caveat in all three places that carry it — `reporting.py`, `Governor.verify()`, the
   docs site — and add the window to each.
5. Do **not** ship the append-only-file sink as a default (A11.7's rule, generalised).
