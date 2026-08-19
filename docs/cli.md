# The CLI

The primary surface. Non-interactive, scriptable, meaningful exit codes — `--json` on every
command, because a CLI without machine output is a CLI nobody scripts.

```bash
tesoro init            # scaffold tesoro.yaml and a starter policy
tesoro check           # validate before an agent holds a wallet
tesoro policy explain  # what will this policy actually do?
tesoro decide ...      # one decision, full engine breakdown
tesoro report          # what was spent, what was refused, and why
tesoro report --html   # the same, as one self-contained page
tesoro conformance     # were the profile's required controls exercised?
tesoro audit           # verify the evidence chain
```

---

## Exit codes

| Code | Means | Typical cause |
|---|---|---|
| `0` | ok | it worked |
| `1` | invalid | config or policy is unusable |
| `2` | **refused** | the layer worked and said no |
| `3` | chain | the evidence chain is broken or unverifiable |
| `4` | usage | the command line itself was wrong |

**`2` is not an error.** A refusal is the layer doing its job, and a script that treats it
as a crash has misread the tool. The distinction from `1` is the one that matters most:
`1` means *the layer could not decide*, `2` means *it decided no*.

`4` exists because argparse exits `2` by default, which here would collide with `refused` —
a typo would read as a governance decision, and that is the worst confusion this particular
tool could hand someone.

## `tesoro init`

Writes `tesoro.yaml` and `policies/default.yaml` into the working directory. Refuses to
overwrite without `--force`: a policy file decides whether money moves, and clobbering one
on a mistyped command is not a risk worth the convenience.

The starter policy is **copied out**, not referenced in place. A config pointing at a file
inside `site-packages` teaches people their policy is not theirs to change.

## `tesoro check`

Validates the config **and** the policy pack it points at, reports every problem rather
than the first, and exits `1` if anything is wrong.

The quiet win: a policy change that would refuse everything, or allow everything, fails the
build **before** it reaches an agent holding a wallet.

```bash
tesoro check                # human-readable
tesoro check --json         # for CI
tesoro check --controls     # also list what the active profile requires
```

```
config : /srv/agent/tesoro.yaml
profile: aegs-1  7 required control(s)
policy : default  46abca353ed56adc  12 rules

ok
```

A profile of `none` is called out explicitly — a user who selected it and forgot is
otherwise reading a green check that guarantees nothing.

## `tesoro policy explain`

What a policy would do, rule by rule, in **evaluation order**. Priority order *is*
evaluation order and the first match is terminal, so reading top to bottom is reading the
decision procedure.

```
  [    5] deny-over-balance  ->  REJECT
          because: insufficient treasury balance
          matches when ALL of:
            budget.ok is False
            budget.binding is one of ['balance', 'emergency_reserve']
```

Derived facts are explained first, in declaration order, because rules reference them and
that order is the order they are computed in.

Resolves the pack **your config names**, not the packaged starter. An earlier version fell
back to the default whenever `--policy` was absent, so it cheerfully explained a policy the
agent was not using — worse than explaining nothing.

## `tesoro decide`

One decision, with the full engine breakdown. Exits `2` on any refusal.

```bash
tesoro decide --amount 2.50 --vendor acme --resource /market/snapshot
tesoro decide --amount 2.50 --vendor acme --resource /x --dry-run   # decide, journal nothing
```

`--dry-run` uses an ephemeral store, so nothing is recorded and no envelope moves. Useful
for "what would happen if" without polluting the evidence.

## `tesoro report`

The four questions an agent developer asks at 2am, in that order.

```
policy   : default  46abca353ed56adc  12 rules
profile  : aegs-1
decisions: 3  settled 0  spent $0.000000

by attributed control  -- what actually governed this agent
  policy                 2
  treasury               1

envelopes: external
  per_transaction  per call (base)        ceiling $10.000000
  daily            today                  $0.000000 of $50.000000  headroom $50.000000

decisions (newest first, 3 of 3)
  2026-08-17T13:32:42  REJECT    $  500.000000  /market/snapshot [treasury]
      per_transaction (per call (base)) admits $10.000000 but the request is $500.000000

chain    : 3 entries, VALID
  note   : a hash chain detects edits and middle-deletions, not truncation...
```

**`by attributed control` is the part worth reading.** Counts by verdict tell you what
happened; counts by attributed control tell you **what actually governed this agent** —
frequently not what the policy file's author expected.

Two details that are deliberate rather than cosmetic:

- A **per-call ceiling** is shown as a ceiling, not as "used of limit". `per_transaction`
  never accumulates, so `$0.00 of $10.00` beside the cumulative windows would read as
  "nothing was spent", which is false.
- The **chain caveat is printed every time**, next to the verdict on the chain. A report
  saying `VALID` without it overstates what a hash chain proves: any *prefix* of a valid
  chain is itself valid, so an agent that was refused could delete the refusal and the
  chain would still verify. Edits and middle-deletions **are** caught.

Exits `3` if the chain does not verify, because a report whose evidence is broken should
not exit `0`.

### `--html` — the same report as a page

```
tesoro report --html -o spend.html     # write a file
tesoro report --html > spend.html      # or pipe; stdout is the default
```

One self-contained HTML file: four panels, no server, no port, no listener, and **no
outbound request of any kind** — no CDN, no webfont, no analytics. That last part is a
tested property rather than an intention, because a page describing what an agent spends,
generated on a machine next to a wallet, should not phone anywhere. `tests/test_html.py`
greps the rendered bytes for absolute URLs, `src` attributes, `<link>` elements, `@import`
and every call that can reach the network.

It is an **artifact**, not an app. You can attach it to a ticket or mail it to whoever asks
why the agent stopped, which is most of the value of a dashboard without any of its attack
surface. A live view is `tesoro serve` in 0.2, and it will feed *this same renderer* rather
than a second template — `render()` takes a `Report`, so there is nothing transport-specific
in it to duplicate.

The panels, and the question each answers:

| Panel | Answers |
|---|---|
| **Policy** | *What will this do?* — the pack, its content hash, and every rule **in evaluation order** with its condition in plain terms |
| **Envelopes** | *How much is left?* — both channels, every limit, headroom, and which envelope **binds** |
| **Decisions** | *Why did my agent stop?* — newest first, each with its **attributed control** |
| **Evidence** | *Can I trust this record?* — chain length, state, hash function and length, and the caveat |

Three things the page does deliberately:

- An **unset limit renders as `absent`**, never as `$0.00`. Rendering it as zero would state
  the *tightest possible* ceiling where there is in fact no ceiling — exactly inverted, and
  this is the place a reader misreads it fastest.
- The rules are shown **in evaluation order**, because the first matching rule decides. A
  count tells you the pack is not empty; the order tells you what will stop you.
- The **chain caveat sits next to the chain state**, not in a footnote. A page saying `VALID`
  above an unqualified tick claims more than a hash chain delivers.

`--html` and `--json` together is a usage error (exit `4`) rather than one silently winning:
a user who passes both has a wrong expectation about one of them. `-o` without `--html` is
likewise an error, because accepting it and ignoring it would discard the file they asked
for.

## `tesoro conformance`

Were the controls the active profile requires actually exercised, given the decisions in
the journal?

```bash
tesoro conformance                    # profile from config
tesoro conformance --profile aegs-2   # score against a different one
```

This is **evidence completeness**, not the full standard. The AEGS-CONF suite is a separate
package that scores an implementation against the standard's own cases, and it ships apart
from the thing it tests on purpose — a conformance suite bundled with its subject is not a
conformance suite.

Exits `1` when any record is non-conformant, and names the finding:

```
[MUST_EXERCISE] TrustAssessment: nothing at 'assessments/trust'. The profile requires
this control and the record does not show it ran.
```

## `tesoro audit` · `tesoro replay`

`audit` verifies the hash chain and exits `3` if it is broken. `replay` re-decides every
journalled decision and checks the verdicts come out identical — the determinism guarantee,
checked rather than asserted. A model in the decision path would make `replay` impossible,
which is one of the reasons there isn't one.

## `tesoro bench`

Measures decision latency on **your** hardware. No framework, no key, no money.

```
latency p50    : 238 us
latency p99    : 411 us
inference cost : $0.000000 (no model was invoked)
p99 under 1ms  : True
```

Kept in the shipped CLI deliberately, against an earlier plan to move it out: it
substantiates the layer's central performance claim, and a core claim belongs where the
user already is rather than in another repository.

## `tesoro record` · `tesoro intent` · `tesoro identity` · `tesoro reviews` · `tesoro policies`

- **`record`** — emit or validate AEGS Decision Records. `--validate` is the interoperability surface: another implementation can check its own exported records against the schema without sharing any of this code.
- **`intent`** — declare, list or revoke an economic intent. What the agent was *sent to do*, declared before it acts.
- **`identity`** — register or inspect an agent identity. Pseudonymous by default.
- **`reviews`** — inspect or resolve the review queue.
- **`policies`** — list discoverable policy packs, one per name.

## `--json`

On every command, and accepted **both** before and after the subcommand:

```bash
tesoro report --json
tesoro --json report      # identical
```

Both work because both are what people type. The second needed an explicit fix — argparse
parses the global flag first and then lets the subparser's default overwrite it with
`False`, so the pre-command spelling silently stopped working the moment per-command flags
were added. Caught by a test rather than by a user.

## What the CLI will not do

- **Mutate anything from a report.** `report`, `audit`, `conformance` and `policy explain` are read-only.
- **Print a key.** Ever. Masked display is the only path, and `check` refuses a key in the config file outright.
- **Bind a network port.** That is `tesoro serve`, which is separate, optional, and `127.0.0.1` only.
