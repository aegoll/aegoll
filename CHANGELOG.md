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

**AEGS and AEGL are unchanged.** They name the standard and the category, not this package. A
package whose name does not echo its standard is normal — nobody thinks `requests` sounds like HTTP.

> The sentence here used to add *"and the schema `$id`s still resolve through the same host because
> the GitHub organisation keeps its name."* **That stopped being true on 2026-08-28**, when the
> organisation was renamed from `aegoll` to `tesoro-labs`. Corrected rather than deleted, because a
> changelog that silently drops a claim it made is the thing this file exists not to be.

Notable changes to `aegoll`. Format follows [Keep a Changelog](https://keepachangelog.com/);
versions follow [semantic versioning](https://semver.org/), with the caveat that **`0.x` means
the API may change** — see [`docs/api-surface.md`](docs/api-surface.md) for which symbols carry
which stability tier.

Entries name the defect a change fixes, not only the change. A changelog that lists features
and hides fixes tells a reader what was added and not what was wrong.

## [Unreleased]

### Added

- **A counterparty's jurisdiction, declared by an operator and never inferred.**
  `Vendor.jurisdiction`, two new policy facts — `vendor.jurisdiction` and
  `vendor.jurisdiction_state` — and `tesoro decide --vendor-jurisdiction CODE` /
  `--vendor-jurisdiction-unknown`. Specified as
  [`AEGS-0.1-CTRL-6a`](https://github.com/tesoro-labs/aegs/blob/main/spec/02-controls.md), with
  three conformance vectors.

  **Three states, because two cannot carry the fact.** `str | None` cannot separate *nobody was
  asked* from *an operator was asked and does not know*, so the field defaults to the `MISSING`
  sentinel: an absent key means nothing is claimed, an explicit `null` is an answer somebody gave,
  and a string is a declared value. Collapsing the first two would be the same defect as
  `assessed: false` on a control that does not exist.

  **Nothing derives it, and that is enforced rather than intended.** Not from the counterparty's
  postal address, not from a domain suffix in its id, not from the settlement chain. Each of those
  correlates with jurisdiction and none of them *is* jurisdiction, which is a legal fact about a
  legal person — a `.de` domain is registrable from anywhere and a postal address is where post
  goes. The vectors hand the runner a `.de` identifier alongside a Berlin address specifically so
  an implementation that reads either one fails them; planting exactly that inference was confirmed
  to fail one vector and two tests before the plant was reverted.

  The asymmetry is the argument. An **absent** jurisdiction is visibly absent — a policy can refuse
  on it and a reviewer can ask for it. An **inferred** one is a guess wearing the clothes of a
  fact, sitting in the record identically to a declared value, with the error invisible at exactly
  the moment somebody builds a determination on it.

  This adds no jurisdiction *model*, which is a separate thing the reference implementation still
  does not have and does not claim.

### Fixed

- **A documented policy rule that would never have fired.** The example added to the policies page
  omitted `priority`, and rules are evaluated in priority order with the first match terminal — so
  a rule without one sorts last and is reached only when nothing else matched. For a rule meant to
  catch unvetted counterparties that is indistinguishable from it not working. Caught by running
  the example instead of reading it, which is now the rule for anything published as a command or a
  config.

## [0.1.2] — 2026-08-28

The first release that is not a rename or a repackage. Two red-team findings closed, a kill switch,
anchored evidence, a vertical conformance profile the reference implementation deliberately fails,
and **an sdist** — 0.1.0 and 0.1.1 both shipped wheel-only because a hand upload omitted it.

### Added

- **The `stablecoin-1` conformance profile, vendored from the standard — and this package does
  not conform to it.** It extends `aegs-2` and requires an AML position in the record. There is no
  AML control here, so no `assessments/aml` key is emitted, so the profile finds against it. That
  is the intended reading: a profile whose only claimants are the implementations that wrote it
  measures nothing.

  Getting there needed a change to the standard. `AEGS-0.1-PROF-6` forbids a profile from requiring
  a control no implementation has an engine for, and names `AMLAssessment` as one of three — so the
  profile was not merely unbuilt, it was **prohibited by the specification it was written for**.
  PROF-6 is right about *ladder* profiles, where every implementation must claim a rung and an
  unreachable rung just moves the real bar down. It is wrong about *vertical* profiles, which one
  class of deployment claims: obliged entities settling in stablecoins already meet the AML bar by
  licence. `AEGS-0.1-PROF-6a` permits it, guarded by the condition that the named class must
  *already* meet the requirement outside AEGS — otherwise "vertical" is a label anything can wear.

  **The obvious shortcut was tried first and was a false statement.** Emitting
  `assessments.aml = {"assessed": false}` looks honest; it means *this control exists and did not
  run*, and there is no AML control here at all. `IMPLEMENTED_CONTROLS` caught it within a minute.
  Absent is not not-run.

- **`Profile.is_vertical()` and `Profile.deployment_class`**, read from the manifest rather than
  defaulted away, because the field's absence is meaningful: a profile without one is a ladder
  profile and PROF-6 applies to it unchanged.

- **Count envelopes over a long window: `actions_per_day` and `actions_per_month`.** The rate
  counters `velocity_60s` and `velocity_1h` bound how *fast* an agent acts. Nothing bounded how
  *many* times it acts over a window longer than an hour, and an hourly ceiling of 100 does not
  imply a daily ceiling of 2,400 -- no product of a rate limit and a duration was ever compared
  against anything.

  Measured consequence, now closed: an agent pacing at **97 actions an hour -- three per cent
  under the ceiling -- was compliant indefinitely**, at 2,328 actions a day for $2.33 against a
  $50 daily budget. Every value envelope was out of reach because each action was trivial; every
  rate limit was out of reach because the pacing sat under it. The red-team suite's paced-evasion
  attack moved from **undefended to defended** and is refused at action 500, attributed to
  `treasury` citing `actions_per_day`. See
  [EXP-010](https://github.com/tesoro-labs/aegs/tree/main/research/experiments/EXP-010).

  **This needed no new control and no new clause.** `AEGS-0.1-ENV-7` has permitted count
  envelopes since 0.1 and fixes their semantics; tesoro already implemented two of them. The
  entire gap was the window. An AEGS 0.2 clause family was drafted on the mistaken belief that the
  standard could not express this, and thrown away.

  The shipped `default` pack declares `actions_per_day: 500` (five hours of continuous work at the
  hourly ceiling) and `actions_per_month: 10000`, with `60` / `500` on the internal channel --
  declared rather than inherited, because channels must not share an envelope and the external
  figure was chosen for payouts. **An omitted key means no limit; `0` forbids every action.** The
  loader returns `None` for an absent key rather than defaulting to a number, so an existing policy
  pack behaves exactly as before, and a negative limit is refused at load.

  The default pack's content hash changes: `a5a64aeb69dbc5f9206b31022064da26` ->
  `46abca353ed56adc703aa555ca1e12d6`, 12 rules either way.

- **A kill switch: `Governor.freeze(reason)` / `unfreeze()` / `frozen`.** Refuses every action
  until lifted, and records why. `reason` is required and may not be blank -- whoever finds the
  agent stopped at 2am has to be able to read why.

  **It exists because revoking an identity is not a substitute, and the reason is measured.**
  `identities.set_status(agent, "revoked")` refuses an agent that has a registered identity and
  **silently does nothing for one that does not**: it returns `False` and the next payment is
  APPROVED. A kill switch whose effect depends on an unrelated registration having happened is not
  a kill switch, so this one has no precondition.

  **Declared dispositive, and the ranking is declared with it** as `AEGS-0.1-VERD-4a` requires:
  `sanctions` outranks `killswitch`. A sanctioned counterparty attempted *while frozen* is
  attributed to `sanctions`, because "this agent tried to pay a barred party" must not be displaced
  by "an operator had paused it" -- the operator knows they paused it. Both facts stay on the
  record; precedence decides attribution, not retention.

  **Persisted**, because a freeze that evaporates on restart is not a freeze -- a crash loop would
  resume spending. **An unreadable state file reads as FROZEN**, not as clear: a corrupt state is an
  *unknown* state, and continuing to spend on an unknown state is the failure the switch exists to
  prevent.

  **What it is not:** it stops a *misbehaving* agent and does not contain an *adversarial* one. The
  state is a file the agent's own process can usually write, so an agent that controls its host can
  lift its own freeze. Not a containment boundary, and not described as one.

  The engines still run while frozen -- a freeze *narrows* the verdict rather than skipping
  evaluation -- so the record of a refusal during a freeze still shows what would have happened,
  which is what an operator needs when deciding whether it is safe to lift.

- **`Governor.verify_anchored(anchor)` — verification against an external anchor.**
  AEGS-0.1-EVID-6a. A hash chain cannot detect truncation of its own tail, because any prefix of a
  valid chain is itself valid; the missing information is *that there was more*, and an anchor
  supplies it as a `(length, head)` pair published where the journal's writer cannot reach.

  **Four outcomes, and `unknown` is never a pass:** `consistent`, `truncated`, `diverged`,
  `unknown`. A sink that cannot be read leaves the anchored claim unavailable, which is a third
  thing -- reporting it as consistent would mean anyone able to partition the process from the
  sink could also make a truncated journal verify.

  **It bounds truncation rather than eliminating it.** Everything appended since the anchor's last
  publication is unattested and remains removable, so the honest claim is *detectable beyond a
  bound you chose*. A vector and a test both assert that limit rather than describing it.
  Publishing on every refusal is the cheap way to narrow the window where it matters: a refusal is
  rare, and it is the entry an adversary most wants inside it.

  **`verify()` is unchanged.** Two calls, because they answer different questions and one can hold
  while the other fails -- a chain can be internally valid *and* shorter than the history that
  happened.

  **No sink ships, deliberately.** `Anchor` is a two-method duck type needing no tesoro import. An
  append-only file beside the journal is rewritable by the agent's own user in most deployments, so
  bundling one with a config key would make the gap look closed while defending nothing -- the
  `head.json` trap at one remove. `docs/design/evidence-anchoring.md` lists five candidate sinks
  and what each actually guarantees, including the x402-rail option, which is nearly free and
  structurally blind to refusals.

- **`Governor.decide()` was public and undocumented**, and `verify()` was too. Both are on
  `docs/api-surface.md` §3 now, and a test compares that page against the class in both directions
  -- documented-but-absent, and public-but-undocumented. The README once carried an API that did
  not exist and whose opening snippet raised `AttributeError` on line 3; nothing compared the page
  to the code. Now something does.

### Fixed

- **200 trivial actions erased an agent's real spending history from the risk baseline.**
  `agent_amounts` was `ORDER BY at DESC LIMIT 200` and nothing else -- a *count* window, so
  history was evicted by volume rather than by age. An agent with 40 varied purchases, given 200
  of $0.001, kept 200 rows all of $0.001; standard deviation collapsed to zero. Bounded by 30 days
  first now (matching every other aggregate in the snapshot), with the row cap kept as a memory
  guard and raised to 1,000. Hitting it sets `baseline_truncated`, which `risk` reports as a flag
  and in the term detail -- a statistic over the most recent N actions is not the statistic it
  appears to be.

- **`amount_zscore` returned a hardcoded `6.0` when it had no dispersion to report**, so against a
  flat history $0.01 and $100,000 scored identically. The function had stopped measuring, and the
  fabricated sigma reached `risk.score`, the journal and every report as though it had been
  computed. It returns `None` now, with `dispersion_state()` naming which of three reasons applies
  -- `no_baseline`, `no_spread`, `measured`. "Nothing is known about this agent" and "every amount
  ever seen was identical" are opposite statements and used to be the same return value.

  **No verdict changes.** The signal the magnitude carried moved to
  `differs_from_flat_baseline()`, and `risk` scores that case at exactly 1.0 -- the same
  contribution `min(1.0, 6.0 / 4.0)` produced. Collapsing the zero-dispersion case into the
  no-baseline branch would have dropped the term to 0.0 and *relaxed* the verdict; that is now a
  test of its own.

  Both defects were invisible because neither moved a verdict: 6.0 saturates against
  `zscore_saturation: 4.0`, so the term pinned at maximum -- the conservative direction. An input
  weighted `weight_zscore: 0.20` had silently become a constant, and nothing said so.

### Changed

- **`structuring` is now bounded but still not refused, and every document says so.** A count
  envelope bounds the mechanism, not the instance: 40 payments in an afternoon is nowhere near 500,
  and refusing it would refuse legitimate work. 2,328 actions a day is impossible; 40 is still
  permitted. A test asserts this so the result cannot be rounded up to "structuring is handled".
- **The specification's own description of count envelopes was wrong and is corrected.** `ENV-7`'s
  note and `SEC-6` both said a count envelope "constrains rate, not total". A count envelope
  constrains the total over its window; the claim generalised from the short windows anyone had
  used, and it pointed away from the answer ENV-7 already contained. `SEC-6` now records paced
  evasion as *defensible within 0.1 and not required by it* -- ENV-7 is a `MAY`, so a conforming
  implementation may still be wide open, and a conformance result does not tell an adopter
  otherwise.

### Changed — the organisation, 2026-08-28

- **`github.com/aegoll` is now `github.com/tesoro-labs`, and the schema `$id`s moved with it.**
  Every link a reviewer followed said `aegoll`, a coined word this project had already abandoned
  for being unspellable, fronting a package called `tesoro`. `tesoro` is taken as a GitHub user, so
  the org could not simply take it.

  A `$id` is an identifier and moving one is not free — two documents claiming the same schema
  under different names cannot be told apart by a validator that caches by `$id`. Done now for one
  reason: **at 0.1 there are no external consumers, and this is the cheapest it will ever be.**

  Two consequences worth stating rather than discovering. GitHub redirects renamed *repository*
  URLs but does not keep the old Pages hostname alive, so `aegoll.github.io/...` stops resolving
  and the name becomes claimable by anyone. And **the PyPI trusted publisher was configured with
  `Owner: aegoll`**, so it stops matching until it is changed.

  The sweep was two narrow patterns, not a blanket replace. `aegoll` was also the *package* name
  for its first two releases, and this file says so on purpose — `pip install aegoll`,
  `import aegoll`, `aegoll[langgraph]`, `.aegoll/`, `pypi.org/project/aegoll/`. A blanket rename
  would have turned all of those into false statements, and two of them did survive the first
  attempt long enough to be caught by a dry run. Sealed records under `research/experiments/` are
  untouched: EXP-007 measured the published `aegoll` package, which is true and stays true.

### Fixed

- **The red-team CI job had been green against nothing since it was added.** It tested for a
  directory the suite was never in and printed a note when absent -- and the false branch of that
  condition is a passing job. The 18-attack suite now runs unconditionally against a recorded
  baseline that fails in both directions.

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
  `Owner: tesoro`; the owner is the *organisation*, `tesoro-labs` (`github.com/tesoro-labs/tesoro`). Anyone
  following the comment configured a publisher that could never match, and the failure surfaces
  minutes later as an opaque permissions error at the final step. A setup instruction that is
  wrong is worse than one that is missing: it produces confident, incorrect configuration.
- **Every workflow file is now checked for duplicate keys**, by a test rather than by a run
  failing. Preparing this release, `workflow_dispatch` was given a second `inputs:` block on the
  belief that it declared none -- the belief came from reading the first 30 lines of the file,
  which end exactly at `workflow_dispatch:`. `dry_run` was already there, correctly typed and
  defaulting to `true`.

  What a duplicate key costs is worth recording: GitHub does not report it as a configuration
  error. It stops resolving the workflow, so the `on: push: tags:` filter is not applied -- the
  workflow fires on **branch** pushes it should ignore, fails instantly, and appears in the run
  list named `.github/workflows/release.yml` instead of `release`. That filename is the only
  signal, and it looks like the release simply failed again. Two such runs happened here.
- **The `purity` CI job was red.** `test_not_being_able_to_validate_is_not_the_same_as_invalid`
  asserted `can_validate()` -- requiring the validator whose *absence* is the state under test,
  in the one job that deliberately installs the core alone. It skips there now. The test made the
  mistake it was written to catch.

### Added

- **Documentation site** at <https://tesoro-labs.github.io/tesoro/> -- nine pages behind a left
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
