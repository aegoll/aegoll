# Changelog

Notable changes to `aegoll`. Format follows [Keep a Changelog](https://keepachangelog.com/);
versions follow [semantic versioning](https://semver.org/), with the caveat that **`0.x` means
the API may change** — see [`docs/api-surface.md`](docs/api-surface.md) for which symbols carry
which stability tier.

Entries name the defect a change fixes, not only the change. A changelog that lists features
and hides fixes tells a reader what was added and not what was wrong.

## [Unreleased]

### Added

- **`aegoll report --html [-o PATH]`** — the report as one self-contained HTML file. Four
  panels: Policy (*what will this do?*), Envelopes (*how much is left?*), Decisions (*why did
  my agent stop?*), Evidence (*can I trust this record?*). No server, no port, no listener, and
  no outbound request of any kind — no CDN, no webfont, no analytics. Asserted by test, because
  a page describing what an agent spends should not phone anywhere. Stdlib only; no npm, no
  build step, nothing minified.
- **`Report.rules`** — the policy rules themselves, in evaluation order, each with its
  condition rendered in plain terms. `policy_rules` was a count, and a count cannot answer the
  question anyone actually has before a run: *which rule is going to stop me?*
- **`aegoll.states`** — the four states as a classifier (`absent` / `not-run` / `unknown` /
  `zero`, plus `no-opinion` for AEGS-0.1-STATE-4). Previously the rule was a boolean that
  answered *did this control run* and discarded which of the four ways it did not.
- **`Paths.for_journal()`** — build paths around a configured journal file rather than a
  directory, so `evidence: journal:` honours the filename a user wrote.
- **AEGS test vectors vendored** at `tests/_vectors/`, pinned to a commit, with drift checked
  two ways.

### Fixed

- **Every command now reads the policy pack `aegoll.yaml` names.** Previously `decide`,
  `report` and nine other commands loaded the *packaged* starter pack regardless: a user could
  edit `policies/default.yaml`, watch `aegoll check` confirm the edit by name and content hash,
  and have the agent governed by entirely different numbers, with nothing reporting a conflict.
  A governance layer quietly enforcing a policy other than the one on disk is worse than one
  that fails to start. `Config.policy()` already applied the right precedence; eleven call
  sites shared a helper that never asked it.
- **`evidence: journal:` is honoured.** It had no reader anywhere — a user could point it at
  `logs/spend.jsonl`, get no error, and find their evidence in `./.aegoll` instead.
- **Delegation is clamped, not merely checked** (AEGS-0.1-ID-4). A delegate that declared *no*
  per-action limit escaped its delegator's entirely: the widening check found nothing
  comparable, and the delegate had no limit of its own to trip. Declaring nothing was strictly
  more permissive than declaring a large number — an escalation reachable by omission. Verified
  by reverting the fix, where a `$1.00` payment was **approved** under a parent capped at
  `$0.002`.
- **The vector CI job was running nothing.** It carried `continue-on-error` and checked out only
  this repository, so the runner searched for a sibling `aegs/`, found none, and skipped all 151
  vectors behind a green tick. Now vendored, and the job is a real gate with an explicit
  did-not-skip assertion.
- **Every hash is 128 bits** (AEGS-0.1-EVID-5). Five places retained 64 by writing
  `hexdigest()[:16]` independently. At 64 bits a second preimage is 2⁶⁴ work — commodity GPUs
  reach that in months — which weakens the evidence chain, the determinism check and the
  config/policy content hashes alike. Routed through one `hashing.py`.
- **Attribution for a sanctioned counterparty** (AEGS-0.1-VERD-4a). "The last control that
  narrowed" attributed a sanctions refusal to whatever spending limit happened to bite first.
- **A per-call ceiling is no longer rendered as "used of limit".** `per_transaction` never
  accumulates, so `$0.00 of $10.00` beside the cumulative windows read as *nothing was spent*.
- **`binding` is distinguished from `tightest`** (AEGS-0.1-ENV-6). Two questions: *why was this
  refused* and *what bites next*. With only the first, an approved decision showed no envelope
  at all — blank precisely when someone was checking headroom.
- **`intentId: null` no longer scores as a conformance failure.** MUST_RECORD needs the key
  *present*; a stated null satisfies it. The old behaviour punished honesty.
- **`policy explain` reads the configured pack**, not the packaged default.
- **`--json` before the subcommand** works again. A per-command flag's argparse default was
  overwriting the global one.

### Known limitations

Stated here rather than only in a security document, because they bear on whether to rely on
this:

- **Journal truncation is undetectable** without an external anchor. Any prefix of a valid
  chain is itself valid, so an agent that was refused could delete the refusal and verification
  would still report success. Editing, middle-deletion and reordering **are** detected. The
  caveat is printed next to every `VALID` rather than papered over with a `head.json` beside the
  journal, which would look like a fix while defending against nothing — whoever can truncate
  the journal can rewrite the file next to it.
- **Structuring and velocity evasion are open.** Forty payments of one cent breach no value
  envelope and no rate limit. Both need a control that examines the *shape of a sequence*, and
  no amount of tightening an envelope produces one.
- **AML screening is a schema with no engine behind it.** No list, no matching, no jurisdiction
  model. No regulatory compliance is claimed or sought.
- **The Python API of `docs/api-surface.md` is not fully implemented.** The CLI is the stable
  surface for now.

## [0.1.0] — unreleased

First release. Ported from the [proof-of-concept](https://github.com/Jayzilva/x402) — see
[`PROVENANCE.md`](PROVENANCE.md) for what came from where, and from which commit.
