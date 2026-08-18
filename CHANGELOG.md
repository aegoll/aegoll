# Changelog

## Renamed from `aegoll`, 2026-08-18

This package was published as **`aegoll`** for its first two releases and is now **`tesoro`**.
`aegoll` was a coined word nobody could spell from hearing it, which is a real cost for something
you have to say out loud; *tesoro* is Italian for treasure, and short enough to tell someone once.

The entries below **keep the old name where they describe the past**, deliberately. `0.1.0` and
`0.1.1` really did ship as `aegoll`, and rewriting those lines to say otherwise would be the same
mistake as editing a sealed research record: the history stops being checkable. Anything describing
the *present* says `tesoro`.

What changed for a user, all at once and never again:

| was | is |
|---|---|
| `pip install aegoll` | `pip install tesoro` |
| `import aegoll` | `import tesoro` |
| `aegoll` (CLI) | `tesoro` |
| `aegoll.yaml` | `tesoro.yaml` |
| `.aegoll/` | `.tesoro/` |
| `versions.aegoll` in a Decision Record | `versions.tesoro` |

The `aegoll` project was removed from PyPI rather than left as a yanked shell. It had no recorded
downloads, so nothing depended on it — and that was the whole argument for doing this now rather
than after somebody had a config file.

**AEGS and AEGL are unchanged.** They name the standard and the category, not this package, and the
schema `$id`s still resolve through `aegoll.github.io` because the GitHub *organisation* keeps its
name. A package whose name does not echo its standard is normal — nobody thinks `requests` sounds
like HTTP.

Notable changes to `aegoll`. Format follows [Keep a Changelog](https://keepachangelog.com/);
versions follow [semantic versioning](https://semver.org/), with the caveat that **`0.x` means
the API may change** — see [`docs/api-surface.md`](docs/api-surface.md) for which symbols carry
which stability tier.

Entries name the defect a change fixes, not only the change. A changelog that lists features
and hides fixes tells a reader what was added and not what was wrong.

## [0.1.1] — 2026-08-18

The release *pipeline* release. `0.1.0` reached PyPI by hand, and a manual upload is a step that
can omit a file without telling anyone -- which is exactly what happened.

### Fixed

- **PyPI carries no sdist for `0.1.0`.** The hand-upload sent the wheel and not the tarball, so
  `pip install tesoro` worked while `pip download --no-binary :all: tesoro` did not. Anyone
  building from source, packaging for a distribution, or auditing what they run needs the sdist.
  `0.1.1` publishes both, because the workflow builds `dist/*` and uploads the directory rather
  than a file somebody remembered to name.
- **The trusted-publishing setup instructions in `release.yml` gave the wrong owner.** They said
  `Owner: tesoro`; the owner is the *organisation*, `aegoll` (`github.com/aegoll/tesoro`). Anyone
  following the comment configured a publisher that could never match, and the failure surfaces
  minutes later as an opaque permissions error at the final step. A setup instruction that is
  wrong is worse than one that is missing: it produces confident, incorrect configuration.
- **The `publish` job gated on an input that did not exist.** The condition read
  `inputs.dry_run == false` while `workflow_dispatch` declared no `dry_run`. An undeclared input
  compares equal to `false` in a GitHub expression, so the guard that reads as "skip on a dry
  run" in fact published on **every** manual run. `dry_run` is now declared, typed `boolean`, and
  defaults to `true` -- a manual run cannot publish unless someone unticks it. A condition that
  references something undeclared is not a check; it is a comment that evaluates.
- **The `purity` CI job was red.** `test_not_being_able_to_validate_is_not_the_same_as_invalid`
  asserted `can_validate()` -- requiring the validator whose *absence* is the state under test,
  in the one job that deliberately installs the core alone. It skips there now. The test made the
  mistake it was written to catch.

### Added

- **Documentation site** at <https://aegoll.github.io/tesoro/> -- nine pages behind a left
  sidebar with per-page section navigation: the AEGL concept and its four types, the shared
  vocabulary, what Tesoro is, architecture, policies and rules, frameworks and rails, AEGS, and a
  quickstart. Section links are extracted from the headings by the generator, so a renamed
  heading cannot leave a dead anchor. No JavaScript, no external requests.

No library code changed. The API, the engines, the schemas and `AEGS_VERSION` are identical to
`0.1.0`.

## [0.1.0] — 2026-08-18 · first release as `tesoro`

Identical code to what would have been `aegoll` 0.1.2. A new name gets a clean `0.1.0` rather
than inheriting a version line from a package that no longer exists.

## [0.1.1] — 2026-08-18 · as `aegoll`

Small, and two of the three entries are things `0.1.0` claimed and did not have.

### Added

- **`aegoll.AEGS_VERSION`** is exported from the package. It existed in `aegoll.record` and
  PLAN.md W0.7 claimed it at the top level; nothing compared the claim to the package, so
  `aegoll.AEGS_VERSION` raised `AttributeError` in `0.1.0`. Two version lines answer different
  questions — what the implementation is, and which specification it implements — and a record
  carrying only the first cannot be audited later. Now with a test, because a version line
  nobody imports is one that quietly stops existing.
- **`aegoll[langgraph]`** — LangGraph adapter. `recursion_limit` bounds a graph's *shape*, so
  this supplies the spend ceiling. LangGraph has no polite stop, so the ceiling raises
  `GovernedBudgetExceeded` carrying the attributed control.
- **`aegoll[crewai]`** — CrewAI adapter. **The one adapter whose framework surface is
  unverified**: no prototype precedent and the SDK was not installed, so the hook names come
  from documentation rather than a run. Its governance behaviour is tested exactly as
  thoroughly as the others; the hook names are not. See
  [`docs/adapters.md`](docs/adapters.md), which states the asymmetry as a table.

### Changed

- **Releases now use trusted publishing (OIDC).** `0.1.0` went out on a long-lived API token,
  which is what [`A9.9`](PLAN.md) exists to remove: a token that can publish can publish
  anything, outlives whoever made it, and the only way to learn it leaked is to see something
  you did not upload. `.github/workflows/release.yml` mints a short-lived credential scoped to
  this repository and this workflow, and there is no `password:` anywhere in it to fall back to.
  The workflow also refuses to publish a version already on PyPI, refuses a tag that disagrees
  with `pyproject.toml`, and installs the built wheel into a fresh venv and makes it refuse a
  payment before uploading anything.

## [0.1.0] — 2026-08-18 · as `aegoll`

First release, [was live on PyPI](https://pypi.org/project/aegoll/0.1.0/). Ported from the
[proof-of-concept](https://github.com/Jayzilva/x402) — see [`PROVENANCE.md`](PROVENANCE.md) for
what came from where, and from which commit.

Six of the fixes below were found by installing the wheel into a clean environment and
following the quickstart as written — none were visible from the source tree, or to 632 passing
tests. Two were on the money path: eleven commands ignored the configured policy pack, and
overspending at settlement bypassed every cumulative envelope.

### Added

- **The Python API of [`docs/api-surface.md`](docs/api-surface.md) §3 exists.** `Governor.load()`,
  `authorize()`, `settle()`, `decide()`, `declare_intent()`, `register_identity()`, `wrap()`,
  `report()`, `budget()`, `decisions()`, `verify()`, context manager. It was specified before the
  code and then never built, so `from aegoll import Governor` returned the internal rules
  evaluator and the README's own opening snippet raised `AttributeError` on its third line. The
  evaluator is now `RuleEngine`, which is what it is.
- **`Decision.attributed_control` and `Decision.reason`** — which control decided, and the reason
  that carried the verdict rather than the first one logged. Both read the same projection
  AEGS-CONF scores, so a report, a conformance run and a decision cannot disagree.
- **Framework adapters as extras: `aegoll[claude]`, `aegoll[adk]`.** Plus `RunGuard`, the
  three-call contract they are both built from, which imports nothing and needs no extra — an
  agent on a framework with no adapter here can still be governed. The rail contract
  (`PaymentClient`) is kept deliberately separate; the reasoning is in
  [`docs/adapters.md`](docs/adapters.md), and it comes down to AP2 needing one and not the other.
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
- **Settling for more than was authorised consumed the authorised amount.** `record_settlement`
  journalled the settled figure as evidence and then called `mark_settled()` without it, so
  `transactions.amount_atomic` kept the quote — and every window sum reads that column. A `$0.05`
  authorisation settled as `$5.00` consumed **`$0.05`**. Under-counting is the direction that
  matters: a cumulative envelope can be walked straight through by overspending at settlement,
  with the daily ceiling seeing a hundredth of the money that moved. Both figures are now kept, so
  `authorised 0.05, settled 5.00` stays visible as the discrepancy it is.
- **Evidence location was frozen at import time.** `DATA_DIR = Path.cwd() / ".aegoll"` was
  evaluated when the module loaded and captured in `Paths.under()`'s default, so a process that
  changed directory kept writing to the journal it started with — and two governors loaded from
  different directories shared one, meaning **one agent's spending consumed the other's
  envelopes**.
- **Revoking an already-revoked intent returned `True`**, telling a caller it had just withdrawn
  authority that was withdrawn long ago.
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
- **The framework adapters are not verified against the real SDKs.** Both are tested against
  fakes with nothing installed, which is deliberate — a contract exercised only with an SDK
  present is a contract nobody checks. But it means an SDK that moves a hook will break the
  integration without breaking these tests. The hook shapes come from the proof-of-concept, where
  all three frameworks ran end to end, and end-to-end runs belong in `aegoll-integrations`, which
  installs them. See [`docs/adapters.md`](docs/adapters.md).
