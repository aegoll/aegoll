# The CLI

The primary surface. Non-interactive, scriptable, meaningful exit codes — `--json` on every
command, because a CLI without machine output is a CLI nobody scripts.

```bash
aegoll init            # scaffold aegoll.yaml and a starter policy
aegoll check           # validate before an agent holds a wallet
aegoll policy explain  # what will this policy actually do?
aegoll decide ...      # one decision, full engine breakdown
aegoll report          # what was spent, what was refused, and why
aegoll conformance     # were the profile's required controls exercised?
aegoll audit           # verify the evidence chain
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

## `aegoll init`

Writes `aegoll.yaml` and `policies/default.yaml` into the working directory. Refuses to
overwrite without `--force`: a policy file decides whether money moves, and clobbering one
on a mistyped command is not a risk worth the convenience.

The starter policy is **copied out**, not referenced in place. A config pointing at a file
inside `site-packages` teaches people their policy is not theirs to change.

## `aegoll check`

Validates the config **and** the policy pack it points at, reports every problem rather
than the first, and exits `1` if anything is wrong.

The quiet win: a policy change that would refuse everything, or allow everything, fails the
build **before** it reaches an agent holding a wallet.

```bash
aegoll check                # human-readable
aegoll check --json         # for CI
aegoll check --controls     # also list what the active profile requires
```

```
config : /srv/agent/aegoll.yaml
profile: aegs-1  7 required control(s)
policy : default  a5a64aeb69dbc5f9  12 rules

ok
```

A profile of `none` is called out explicitly — a user who selected it and forgot is
otherwise reading a green check that guarantees nothing.

## `aegoll policy explain`

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

## `aegoll decide`

One decision, with the full engine breakdown. Exits `2` on any refusal.

```bash
aegoll decide --amount 2.50 --vendor acme --resource /market/snapshot
aegoll decide --amount 2.50 --vendor acme --resource /x --dry-run   # decide, journal nothing
```

`--dry-run` uses an ephemeral store, so nothing is recorded and no envelope moves. Useful
for "what would happen if" without polluting the evidence.

## `aegoll report`

The four questions an agent developer asks at 2am, in that order.

```
policy   : default  a5a64aeb69dbc5f9  12 rules
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

## `aegoll conformance`

Were the controls the active profile requires actually exercised, given the decisions in
the journal?

```bash
aegoll conformance                    # profile from config
aegoll conformance --profile aegs-2   # score against a different one
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

## `aegoll audit` · `aegoll replay`

`audit` verifies the hash chain and exits `3` if it is broken. `replay` re-decides every
journalled decision and checks the verdicts come out identical — the determinism guarantee,
checked rather than asserted. A model in the decision path would make `replay` impossible,
which is one of the reasons there isn't one.

## `aegoll bench`

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

## `aegoll record` · `aegoll intent` · `aegoll identity` · `aegoll reviews` · `aegoll policies`

- **`record`** — emit or validate AEGS Decision Records. `--validate` is the interoperability surface: another implementation can check its own exported records against the schema without sharing any of this code.
- **`intent`** — declare, list or revoke an economic intent. What the agent was *sent to do*, declared before it acts.
- **`identity`** — register or inspect an agent identity. Pseudonymous by default.
- **`reviews`** — inspect or resolve the review queue.
- **`policies`** — list discoverable policy packs, one per name.

## `--json`

On every command, and accepted **both** before and after the subcommand:

```bash
aegoll report --json
aegoll --json report      # identical
```

Both work because both are what people type. The second needed an explicit fix — argparse
parses the global flag first and then lets the subparser's default overwrite it with
`False`, so the pre-command spelling silently stopped working the moment per-command flags
were added. Caught by a test rather than by a user.

## What the CLI will not do

- **Mutate anything from a report.** `report`, `audit`, `conformance` and `policy explain` are read-only.
- **Print a key.** Ever. Masked display is the only path, and `check` refuses a key in the config file outright.
- **Bind a network port.** That is `aegoll serve`, which is separate, optional, and `127.0.0.1` only.
