# `tesoro` — sub-plan

**The Python package.** An Autonomous Economic Governance Layer that installs in one
line, configures in one file, and runs from a CLI.

Master plan: [`../PLAN.md`](../PLAN.md) · Context and rules: [`../CONTEXT.md`](../CONTEXT.md)
Port source (**read-only**): `../x402/aegl/` (10,533 LOC, 249 tests, all green) — [`../x402-REFERENCE.md`](../x402-REFERENCE.md)

**Positioning:** `tesoro` is a **policy-engine host**. AEGS is one *profile* it can
enforce. The Decision Records and the conformance level are what it produces, mentioned
second.

> **Positioning revised 2026-08-17 after upstream research.** The old pitch was *"know and
> cap what your agent spends"*, resting on the claim that agent frameworks have no cost
> ceiling. That claim is expiring: the x402 SDKs themselves are adding spend controls
> (`maxAmountPerPayment` over `allowedAssets`, client-side, per-payment) in TypeScript,
> Python and Go — see [F3](../UPSTREAM-x402.md).
>
> **A cap on one payment is not a budget.** What `tesoro` adds on top of that baseline:
> **cumulative envelopes over time windows**, per-vendor and per-resource ceilings,
> velocity, trust, intent, **attribution of which control decided**, **hash-chained
> evidence**, policy as a versioned file rather than a constructor argument, and a
> conformance profile. The difference between refusing a payment and being able to prove
> why — and between one limit and a governed spend.
>
> That is a narrower, harder, and more honest claim than the old one, and it is now
> checkable against a real upstream baseline instead of against an absence.

---

## A0 — Blocking decisions ✅

Irreversible once published. Nothing else starts until these are answers, not opinions.

- [x] A0.1 **`tesoro` is free on PyPI** — `pypi.org/pypi/tesoro/json` → 404, checked 2026-08-17
- [x] A0.2 **`tesoro` free on npm** (404) and **`@tesoro/core` free** (404), checked 2026-08-17
- [x] A0.3 Import name `import tesoro`, matching the distribution name. No `aegl` alias published
- [x] A0.4 Licence: **Apache-2.0** for code — already the `LICENSE` in all three repos. The patent grant matters for anything with standards ambition

> **Found — one name is taken, and it is not the one that matters.** `aegs` **exists on
> PyPI** at 0.0.6 ("model for aegs."), unrelated. Harmless: `aegs` is a *specification*
> repo, not a Python distribution. The only thing that repo ever publishes is the
> conformance suite, and **`aegs-conformance` is free** (checked, 404). No rename needed —
> but never write `pip install aegs` in any document, because it installs a stranger's
> package.
- [x] A0.5 **Config format: `tesoro.yaml`, with `tesoro.json` accepted everywhere YAML is.** One loader, one schema, two syntaxes. No TOML — the vision says "YAML or JSON", and a third parser buys nothing but a third set of docs
- [x] A0.6 **Public API written before it exists** — [`docs/api-surface.md`](docs/api-surface.md)
- [x] A0.7 Two version lines: `tesoro.__version__` (semver) and `tesoro.AEGS_VERSION` (spec). Both stamped in every Decision Record and every conformance declaration
- [x] A0.8 Minimum Python **3.11**, matching the prototype
- [x] A0.9 **Clean break** on the `aegl` module name — no shim, no alias. Nothing is published, so there are no external users to break, and a compatibility shim for zero users is pure carrying cost

**Exit:** ✅ `docs/api-surface.md` exists and A0.1–A0.9 all have recorded answers.

---

## A1 — Port and rename ✅

**`../x402` is read-only** — it is the frozen POC and nothing is moved out of it
([`../x402-REFERENCE.md`](../x402-REFERENCE.md) R1–R2). So this is a **port**: copy in, re-commit
here, record provenance. `git log --follow` will not reach the prototype's commits, and
that cost is accepted deliberately, with a provenance index as the mitigation.

- [x] A1.1 [`PROVENANCE.md`](PROVENANCE.md) created before any file arrived
- [x] A1.2 `../x402/aegl/aegl/` copied in **verbatim** and committed unchanged — `41dbe48`
- [x] A1.3 Every fix a separate commit after the faithful copy — `75b9ba5`, `926f50a`, `5a7c37d`, `bf63141`, `0049ce9`, `19a93c0`, `53c124a`
- [x] A1.4 `src/` layout adopted — and it earned its keep in the first commit, see [F-A1](#f-a1--the-prototype-was-never-a-package--2026-08-17)
- [x] A1.5 Module renamed `aegl` → `tesoro` across 46 files; class `Aegl` → `Tesoro`; CLI entry point, widget keys and `pyproject.toml` with it — `0049ce9`
- [x] A1.6 `AEGL` left intact in prose everywhere
- [x] A1.7 **267 tests green** from the source tree — the 249 baseline plus 18 new guards
- [x] A1.8 The re-export shims deleted — **ten, not eleven** as this plan said. 13 internal import sites repointed across three import forms — `19a93c0`
- [x] A1.9 Purity tests re-run and green. `test_no_llm.py` walks the tree, so it stayed honest through the deletion

### Discovered during the port — see [F-A1](#f-a1--the-prototype-was-never-a-package--2026-08-17)

The package reaches outside itself in eleven places. Each gets its own commit so the diff
reads as a fix rather than as churn.

- [x] A1.10 **Starter policies are package data** — `src/tesoro/policies/*.yaml` via `importlib.resources` — `75b9ba5`
- [x] A1.11 **AEGS schemas vendored** — `src/tesoro/_schemas/`, three of thirteen (only those an engine validates against; copying all would imply controls this package does not implement), with `_schemas/PROVENANCE.txt` naming the source commit — `926f50a`
- [ ] A1.11a CI check that a vendored schema has not drifted from the standard at its pinned commit. **A validator running against a stale schema is worse than one that fails loudly** — the rule is written in `PROVENANCE.txt`, the enforcement is not built yet
- [x] A1.12 ~~Move the schema read out of import time~~ — **already correct in the prototype.** Reads are lazy inside `load_schema()`, `import jsonschema` is local to the validating function, and absent `jsonschema` degrades to a clear message rather than an error. Nothing to do; recorded so the claim is not repeated
- [x] A1.13 **The `.env` walk is gone** — `75b9ba5`. It ran at *import time* in two places, so `import aegl` read a file the caller never offered it and exported the contents into `os.environ`. Replaced by `load_env_file(path)`, which the caller names and which returns a dict rather than mutating the environment
- [x] A1.14 **Runtime state is caller-controlled** — `./.tesoro/` relative to the working directory, not beside the package. Demonstrated against the installed wheel: `site-packages` stays clean — `bf63141`
- [x] A1.15 **`x402_core` path-hack removed.** The adapter checks importability and names the extra to install — `bf63141`
- [x] A1.16 **The four layout-dependent tests fixed** via `tests/conftest.py` — `package_dir()` resolves from `tesoro.__file__`, so the tests check the package that is actually importable — `5a7c37d`
- [x] A1.17 **`tests/test_paths.py`** — nine guards: no upward walk from `__file__`, no `sys.path` mutation, no dotenv-style import, policies and schemas resolve inside the package, and importing exports nothing into `os.environ` — `bf63141`
- [x] A1.18 **Wheel verified in a clean venv** — built, installed, suite run against the installed package, CLI exercised from an empty directory. Caught two real test bugs. `53c124a`

**Exit:** ✅ **267 green from the source tree, 266 + 1 skip from the installed wheel.** `PROVENANCE.md` covers every ported path, no module named `aegl`, no path resolved outside the package, clean install pulls `PyYAML` alone.

> **Found — the wheel test paid for itself immediately.** Neither failure it caught was in
> the package; both were in my own tests, and neither was reproducible from the source tree.
> Windows returned an 8.3 short path (`JAYATH~1`) from `importlib.resources` and the long
> form from `__file__` — same file, unequal comparison — which only happens for a wheel
> installed under a temp directory. And `test_advisor_picks_up_a_runtime_key` assumed the
> `openai` SDK was importable, which it is not in a clean core install; `available()` was
> correctly returning `False` for a reason the test did not mean. **A core that could not be
> installed without a model SDK would have been the actual bug**, so the test was wrong to
> assume one.

---

## A2 — Purity: get the package out of the UI business ✅

Today `aegl` depends on `streamlit>=1.40` unconditionally. A library that drags a web
framework into every install will be declined by exactly the teams worth having.

- [x] A2.1 `app.py`, `ui.py`, `ui_demo.py`, `ui_keys.py`, `crossview.py` moved to `../tesoro-integrations/cockpit/` — `8b825c6` / `237b7a2`
- [x] A2.2 `scenarios.py`, `evaluation.py` moved to `../tesoro-integrations/harness/`
- [x] A2.3 Runtime dependencies are **`pyyaml` alone**. `jsonschema` is the `schema` extra; a clean install pulls nothing else
- [x] A2.4 [`tests/test_deps.py`](tests/test_deps.py) — 16 assertions on the declared *and* imported surface. Verified by planting `import streamlit`: the guard fails
- [x] A2.5 Engines import no third-party code **at module scope**. Not "at all" — two import `jsonschema` inside a validation function, after the verdict, degrading cleanly when the extra is absent
- [x] A2.6 Model clients importable only under `advisors/`, enforced by test
- [x] A2.7 `engines/` imports nothing from `adapters/` or `advisors/`
- [x] A2.8 The three engine families still import no sibling family

**Exit:** ✅ a clean venv pulls `tesoro` + `PyYAML` and nothing else; 16 dependency
assertions green; the wheel carries 35 modules and no UI. 225 passed from source,
224 + 1 skip from the wheel, AEGS-CONF still 7/7.

> **Found — I made the mistake this file exists to prevent, inside this file.**
> `test_deps.py` located `pyproject.toml` as `package_dir().parents[1]` — resolving a
> path relative to the package to reach something outside it, exactly the F-A1 pattern.
> Fine in the source tree; from an installed wheel `parents[1]` is `Lib/`, and three
> assertions failed. Anchored to `__file__` now, and it skips when there is no
> `pyproject.toml` to assert about. Recorded rather than quietly corrected, because the
> pattern is evidently easy to reproduce even while actively guarding against it.

---

## A3 — Config and policy packs ✅

Two separate things, and conflating them causes trouble later. **Policy pack** = what
the rules are (user-authored). **Profile** = which controls must exist (the standard,
see A4).

- [x] A3.1 [`settings.py`](src/tesoro/settings.py) — one loader; `tesoro.yaml`, `tesoro.yml` and `tesoro.json` all accepted, same schema. JSON is a YAML 1.2 subset so there is one parser, not two that can disagree
- [x] A3.2 Config schema: `profile`, `policy`, `channels.{internal,external}`, `evidence.journal`, `advisor`. Unknown keys at every level are rejected, not ignored
- [x] A3.3 Pack discovery by path or by stem, **de-duplicated with YAML winning** — both syntaxes ship, and two entries with one name make `--policy strict` ambiguous
- [x] A3.4 Validation names the offending rule id, and reports **every** problem rather than the first
- [x] A3.5 Content hash over config **and** rules, carried from the prototype. Config and pack hash *separately* — they version independently and a record carries both
- [x] A3.6 `LOOKS_LIKE_HASH` exposed for the label-vs-hash warning
- [x] A3.7 Packs stay data — fixed comparator vocabulary, no `eval`, `safe_load` only. The *enforcement* moved from evaluation time to load time, which is the substance of this task
- [x] A3.8 Tested: unknown comparator, unknown verdict, unknown fact, duplicate id, missing id, bad priority, malformed `between`/`in`, null comparison, unknown keys — each **rejected at load**, and `!!python/object/apply` fails to parse rather than executing
- [x] A3.9 Two starter packs shipped — **`default` and `strict`, not renamed**. Declined the `dev-sandbox`/`prod-strict` naming for two reasons: `default` is genuinely the default and is *not* a permissive sandbox (it rejects sanctioned counterparties, rejects over-balance, reviews at $100), so the name would misdescribe it; and `strict` is referenced **by stem** in CONF-005 in the `aegs` repo, so renaming breaks a conformance case — data that is supposed to be stable — for no user benefit
- [x] A3.10 Both shipped as `.json` too, **generated from the YAML and proven equivalent**: the content hash is over the parsed structure, so an identical hash means the two files really are one policy rather than merely looking similar
- [x] A3.11 `tesoro init` writes `tesoro.yaml` + `policies/default.yaml` and refuses to overwrite without `--force`. It **copies** the starter out rather than pointing at site-packages, so a user's first act with a policy is reading and editing their own copy

**Exit:** ✅ a policy change is validated **at load**, hashed, and traceable from any
decision back to the exact numbers that produced it. `tesoro init` then `tesoro check`
works from an empty directory; a broken pack exits 1 with every fault named.

> **Found — the check existed and did not run.** `COMPARATORS` was already a fixed tuple
> with no `eval` anywhere, so the *vocabulary* half of "policy packs are data, never code"
> was solid. But the check lived inside `policy.evaluate()`, so a malformed rule only
> raised if a request reached it — and **a rule that never matches never validates.** A
> pack could carry a verdict of `MAYBE` and look fine until the one request that touched
> it arrived. Same shape as F-A1 and F-C1: a guard in the wrong place, passing quietly.
> The fact vocabulary is now *derived from `build_facts()` by AST* rather than duplicated
> as a list, because a hand-maintained list is how that class of bug returns.

---

## A4 — Profiles ✅

The adoption mechanism. Today the engines *are* the standard; the profile has to
become a configuration input.

- [x] A4.1 Manifests, not code branches — vendored from the standard into `src/tesoro/_profiles/`, pinned and drift-checked. `aegs-1`, `aegs-2`, `none`
- [x] A4.2 Each declares which controls must be **exercised**, which must be **recorded**, and what makes a decision non-conformant. Every `MUST_EXERCISE` names a `recordPath`, because a requirement with nowhere to look for its evidence is not checkable
- [x] A4.3 `Assessment.as_dict()` is that control's content — controls exercised against a **named** profile, which is a checkable statement about one action rather than the capability claim *"we run a trust engine"*
- [x] A4.3a **Correction: it was not "half-supported" as this plan said — it was not supported at all.** The Decision Record carried no profile or compliance field, and the package had no profile concept. Emitting the assessment *into* the record needs a schema change in `aegs` (the record schema is `additionalProperties: false`), so that is [B1](../aegs/PLAN.md) work, not this task's
- [x] A4.4 `aegs-1` is the default in `tesoro init` and in `Config.defaults()`
- [x] A4.5 `none` genuinely disables enforcement, and `tesoro check` **says so out loud** — a user who selected it and forgot is otherwise reading a green check that guarantees nothing
- [x] A4.6 Tested four ways: no engine imports `profiles.py`; the composition root does not either; `profiles.py` mutates nothing (checked by AST); and the same request yields the same verdict and matched rule under every profile. There is no parameter to pass a profile into the decision path, which is itself the evidence
- [x] A4.7 **AEGS-CONF still 7/7**, both levels claimable, after the refactor

**Exit:** ✅ profile switchable in config alone; AEGS-CONF unchanged at 7/7.

> **Found — I scored honesty as a failure.** The first `assess()` treated `intentId: null`
> as a missing control, so a perfectly honest record came back non-conformant. But a null
> `intentId` *is* the record saying "no intent was declared" — a recorded position, and
> exactly what *absent ≠ not-run ≠ unknown ≠ zero* asks for. Scoring it as a failure
> punishes an implementation for reporting accurately and pushes the next one toward
> omitting the key instead.
>
> So the levels now ask different questions: **MUST_EXERCISE** needs a value that is
> evidence; **MUST_RECORD** needs the *key present*, and an explicit null satisfies it.
> That required a `_MISSING` sentinel, because `None` and *absent* are different answers
> and telling them apart is the whole of the four-state rule at this layer. `0` and `False`
> count as evidence for the same reason — a headroom of zero is a measurement.

---

## A5 — The CLI ✅

The primary surface in v0.1, and the one that has to be good. The vision is explicit:
CLI output first, visual output optional and later. Non-interactive, CI-friendly,
meaningful exit codes.

- [x] A5.1 Ported in A1; `scenarios` and `eval` dropped since, `report`, `policy explain` and `conformance` added
- [x] A5.2 `tesoro init` — landed with [A3.11](#a3--config-and-policy-packs-)
- [x] A5.3 `tesoro check` — landed with A3. Validates config **and** the pack it points at, reports every fault, exits 1. `--json` accepted before *and* after the subcommand, because users type it after
- [x] A5.4 `tesoro policy explain` — rules in **evaluation order**, derived facts first, conditions in words. Reads the pack the *config* names, not the packaged starter
- [x] A5.5 `tesoro decide --amount 2.50 --vendor acme --dry-run` — `--dry-run` uses an ephemeral store, so nothing is journalled and no envelope moves
- [x] A5.6 `tesoro report` — four panels, attributed to the control that decided
- [x] A5.6a **`Report` is now stable** — [`reporting.py`](src/tesoro/reporting.py). Field names decided once; attribution and money formatting both reuse `record.py` rather than being reimplemented, so a report and a conformance run cannot disagree about which control decided
- [x] A5.7 `tesoro audit` — verifies the chain, exits `3` when it is broken. A test tampers with a journal to prove the BROKEN path
- [x] A5.8 `tesoro conformance --profile aegs-1` — scores journalled records for **evidence completeness**, and says plainly that the full AEGS-CONF suite is a separate package
- [x] A5.9 `tesoro record [--export|--file]` — carried from the prototype
- [x] A5.10 `tesoro intent` / `tesoro identity` — carried from the prototype
- [x] A5.11 `--json` on **every** subcommand, added by a loop over the subparsers rather than by hand — a hand-maintained list is how "every" quietly becomes "most". Accepted both before and after the subcommand
- [x] A5.12 Five distinct exit codes documented in [`docs/cli.md`](docs/cli.md): `0` ok, `1` invalid, `2` refused, `3` chain, `4` usage. `4` exists because argparse's default of `2` would collide with **refused**, and a typo reading as a governance decision is the worst confusion this tool could hand someone
- [x] A5.13 `scenarios` and `eval` dropped from the shipped CLI — a demo and a money-spending measurement, both now in integrations. `replay` kept: determinism is a user-facing guarantee
- [x] A5.13a **`bench` kept, against this plan's first draft.** It measures decision latency on the caller's own hardware, needs no framework, no key and no money, and it substantiates the layer's central performance claim. Moving it to another repository would put a core claim somewhere the user has to go looking for it
- [x] A5.14 62 CLI tests: `--json` parses for every command, and every exit code asserted end-to-end as a subprocess
- [x] A5.15 `--help` renders for every subcommand, with the command list derived from the parser so a new command is covered the moment it is added

**Exit:** ✅ the whole product usable from a terminal, scriptable, no UI anywhere near it.
412 tests, AEGS-CONF 7/7, and the installed binary runs `init` then `check` from an empty
directory.

> **Found — three bugs the tests caught rather than a user.** `--json` *before* the
> subcommand silently stopped working the moment per-command flags were added, because
> argparse parses the global flag and then lets the subparser's default overwrite it with
> `False`. `policy explain` fell back to the **packaged** starter whenever `--policy` was
> absent, so it cheerfully explained a policy the agent was not using — worse than
> explaining nothing. And a per-call ceiling was rendered as "used of limit", so
> `per_transaction` showed `$0.00 of $10.00` beside the cumulative windows and read as
> *nothing was spent*, which is false.
>
> **And one in my own first draft of `reporting.py`:** it rolled its own attribution and
> reported **rule ids** as controls — `deny-over-balance` where the answer is `treasury`.
> A different claim, and the wrong one. It now reuses `record._deciding_engine`, the same
> projection AEGS-CONF scores, so if that logic is ever wrong it is wrong in one place.

---

## A6 — Custom policies, custom engines, and the LLM option ✅

The vision's extensibility ask: users add their own policy types, and optionally their
own API keys for LLM-based policies.

- [x] A6.1 **Derived facts** — `all`/`any`/`not` over existing facts, declared in the pack. Still data: fixed combinators, the same fixed comparator vocabulary, nothing to `eval`. Declaration order is evaluation order, so **a cycle cannot be written** rather than being written and then detected
- [x] A6.2 **Engine registry** — [`extend.py`](src/tesoro/extend.py): `Engine` protocol, `Context`, `Assessment`, `register_engine()`
- [x] A6.3 Contract documented in [`docs/custom-policies.md`](docs/custom-policies.md) and enforced in code: pure function, values in, opinion + attribution out, may only narrow
- [x] A6.4 **Corrected, not followed.** Widening is not *refused* — it is **unreachable**. An engine returns an opinion and the composition root applies `narrower()`, so one answering `APPROVE` against a standing `REJECT` has no effect. There is nothing to check because there is nothing to get wrong. Asserted by effect instead: a `Widener` voting APPROVE on everything leaves REVIEW as REVIEW and REJECT as REJECT, and the discarded opinion is recorded as `opinion_did_not_narrow`
- [x] A6.5 **Refused at registration**, by reading the callable's own source: I/O, network, subprocess, sqlite3, clock reads, randomness, model clients, `open`/`eval`/`exec`/`compile`/`__import__`/`input`, and `global`. Twelve cases parametrized over arbitrary source. Unreadable source is itself refused unless explicitly opted into
- [x] A6.6 BYOK handling carried over; keys never stored, logged or journalled, and `tesoro check` **refuses a key in the config file** — that file gets committed
- [x] A6.7 Four advisor backends carried across in the A1 port
- [x] A6.8 `tesoro[advisors]` extra; the core installs no model client
- [x] A6.9 The advisor clamp and its tests carried intact
- [x] A6.10 [`docs/advisors.md`](docs/advisors.md) — why the exclusion is *structural*, with the numbers, and what a model in the path costs in cost, latency, determinism and injection resistance
- [x] A6.11 [`docs/custom-policies.md`](docs/custom-policies.md) — both extension points, when to use which, and why the data one comes first

**Exit:** ✅ a user can add a policy type and an engine without forking, and cannot use
either to weaken the layer. 350 tests, AEGS-CONF 7/7.

> **Found — the `exec` trap, and why the fix mattered.** The impure-engine tests originally
> built their subjects with `exec`, and `inspect.getsource()` cannot read exec'd code — so
> every case failed on *"no readable source"* rather than on the purity problem it was
> written to check. Twelve tests were green-adjacent for the wrong reason. The helper now
> writes a real module and imports it, which is the only way to test a gate that reads
> source. A second self-inflicted one: a test replaced `Tightener.assess` and restored it
> *from* `Tightener.assess` — which by then was the replacement — leaving the class broken
> and taking thirteen other tests with it. A test that reaches into shared state breaks its
> neighbours; the fix is not to reach.

---

## A7 — Framework adapters, step by step 🔨

Optional extras. Nobody pays for a dependency they do not use, and **the core never
imports a framework**.

Order is by verified evidence, not by popularity: Claude SDK and Google ADK are the
two verified end-to-end in the prototype.

- [x] A7.1 [`adapters/base.py`](src/tesoro/adapters/base.py) — **two** contracts, not one. `RunGuard` is the framework hook (three calls, internal channel); `PaymentClient` is the rail contract (external channel). Both duck-typed, both exercised with fakes and **no framework installed** — a contract that needs a real SDK present is a contract nobody checks
- [x] A7.2 `tesoro[claude]` — [`adapters/claude.py`](src/tesoro/adapters/claude.py). The SDK **already has** a cost ceiling, so the adapter's job is making two ceilings agree: `options_for()` returns the **tighter** and never raises the caller's own `max_budget_usd`. What it adds is what one per-run number cannot express — cumulative budget, attributed evidence, and refusal *before* the run rather than a stop with tokens already spent
- [x] A7.3 `tesoro[adk]` — [`adapters/adk.py`](src/tesoro/adapters/adk.py). The opposite case: ADK caps `max_llm_calls`, which is a **step** ceiling, and one long-context call can cost more than fifty short ones. Here `should_stop()` is not a second opinion, it is the only thing watching money. `run_config_for()` leaves the step ceiling alone rather than deriving it — the two bound different things and neither substitutes
- [x] A7.4 `tesoro[langgraph]` — [`adapters/langgraph.py`](src/tesoro/adapters/langgraph.py). `recursion_limit` bounds the graph's *shape*, so this supplies the spend ceiling. LangGraph has no polite stop, so the ceiling **raises** `GovernedBudgetExceeded` carrying the attributed control; only `on_llm_start` is implemented, because a callback firing after a call has cost what it cost is a report rather than a control. Config keys checked against the installed `RunnableConfig`
- [~] A7.5 `tesoro[crewai]` — [`adapters/crewai.py`](src/tesoro/adapters/crewai.py) written, **and the one adapter whose framework surface is unverified**: no prototype precedent and the SDK was not installed, so `step_callback` / `max_iter` / `max_rpm` come from documentation rather than from a run. Governance behaviour is tested identically to the other three; the hook names are not. Stated as a table in [`docs/adapters.md`](docs/adapters.md) rather than implied. Close this by installing `crewai` and running one crew
- [ ] A7.6 `tesoro[x402]` — the payment **rail** adapter. Migrate `adapters/x402_python.py` (245 LOC)
- [ ] A7.7 Rail adapter contract separated from framework adapter contract — they are different boundaries and merging them will hurt when AP2 arrives
- [x] A7.8 Both checked. No adapter module imports its framework (AST walk, so a docstring naming an SDK does not fail while an import would), and `import tesoro` pulls in **no** adapter — asserted in a **subprocess**, because by the time a test file has run its own imports an in-process check passes regardless
- [x] A7.9 A fake framework that knows nothing about `tesoro` beyond the three calls runs governed end to end. The adapter is not the coupling: an agent on a framework with no adapter here is governed by using `RunGuard` directly, which imports nothing
- [x] A7.10 Carried, and now stated as a *direction of control* rather than only an import rule: a framework **calls** the guard, whereas a rail adapter is **called by** the agent. That asymmetry is why the two contracts stay separate — a test fails if their members start to overlap

**Exit:** 🔨 **four** framework adapters and one rail adapter, each optional, core clean — **45 tests**, all passing with **no SDK installed**. Three of the four have their hook shapes from real runs; CrewAI's are from documentation and labelled as such. A7.6 (migrating the rail adapter behind the `x402` extra) and A7.7 remain.

---

## A8 — Test vectors ✅

Consumes [`../aegs/PLAN.md`](../aegs/PLAN.md) B4. The reason tesoro can be checked
against a spec rather than against itself.

- [x] A8.1 **Located by search, not vendored yet.** Two repos side by side is the normal layout; the module skips cleanly when the standard is absent, so a contributor with only this repo does not face red for something they cannot fix. Vendoring with a pin (like `_schemas/` and `_profiles/`) is the next step once the vectors stabilise
- [x] A8.2 [`tests/test_vectors.py`](tests/test_vectors.py) — one test per vector, and a failure names the **clause in dispute** so the argument is *either the spec is wrong or this implementation is*, not a mystery
- [x] A8.3 **Arithmetic family passes: 33 vectors, 0 divergences.** Refusal categories are *mapped* rather than string-compared — a vector checks the right *kind* of reason, and matching wording would make the suite a test of our vocabulary
- [x] A8.4 **Envelope family passes: 27 vectors.** The runner builds `Envelope` directly rather than driving a whole decision, so a failure names the envelope rule rather than implicating policy, trust and risk as well
- [x] A8.5 **Verdict family passes: 32 vectors**, plus a test that drives five real decisions end to end and recomputes attribution independently — without it the vectors could pass while the product disagreed with the spec, and the suite would be testing the runner
- [x] A8.6 **Evidence family passes: 21 vectors** — after raising every hash from 64 to 128 bits, which is what the clause demanded
- [x] A8.6a **States, controls, identity, profiles and path pass: 38 vectors** across five families — and they did not, at first. See [F-A11](#f-a11--twenty-eight-vectors-that-ran-nothing--2026-08-17)
- [x] A8.7 Both covered, and asserted **by name** — "we have arithmetic vectors" is not the same claim as "the minus-sign bug is covered"
- [x] A8.8 **Six classified.** **Three code bugs here**, both found by writing the clause: the per-call ceiling rendered as "used of limit" ([ENV-4](../aegs/spec/03-envelopes.md)), and `binding` conflated with `tightest` ([ENV-6](../aegs/spec/03-envelopes.md)). one a **vector bug** (an amount that genuinely breached the envelope it claimed to fit), one a **specification bug** ([VERD-4](../aegs/spec/04-verdicts.md) misattributed a sanctioned counterparty to whatever spending limit bit first — the spec was amended, this code was already right), one a **cryptographic weakness** (64-bit truncated hashes where [EVID-5](../aegs/spec/07-evidence.md) requires 128), one a **live escalation** ([ID-4](../aegs/spec/08-identity.md) — a delegate declaring *no* limit escaped its parent's entirely), and one a **missing distinction** ([STATE-1](../aegs/spec/06-four-states.md) — the four states were a boolean)

**Exit:** ✅ **151 of 151 green and every one of them executing**, plus a written list of what the spec failed to say and what this implementation failed to do.

---

## A9 — Publish 0.1.0 ✅ · 0.1.1 ✅

- [x] A9.1 `pyproject.toml` complete — classifiers, urls, readme, licence, keywords, and six extras. Each extra must be **documented**: the guard that used to compare against a hardcoded set now fails an extra no document mentions, because the edit that keeps a hardcoded list passing is not the edit that explains a dependency to whoever is installing it
- [ ] A9.2 GitHub Actions: test matrix on 3.11/3.12/3.13, Windows and Linux (the prototype was developed on Windows; do not discover a path bug at install time)
- [x] A9.3 `python -m build` clean, `twine check` **PASSED** on both wheel and sdist. Package data all present (13 files: policies, `_schemas`, `_profiles`), and the 393 KB of vendored vectors correctly **excluded** — they are read by the test suite, and nothing in a user's site-packages needs them
- [x] A9.4 **Clean-venv install and quickstart verified — from the built wheel, then from PyPI itself.** TestPyPI was skipped rather than failed: by the time a token arrived, every property the dry run existed to check had been checked against the identical artifact. Original note follows. **Clean-venv install and quickstart verified from the built wheel** — install pulls `tesoro` + `PyYAML` and nothing else, all five exit codes correct, the README snippet runs verbatim, `report --html` writes a self-contained page. The **TestPyPI upload itself is blocked**: no token in this environment. Everything it would prove has been proven against the same artifact locally
- [x] A9.5 `README.md` leads with the governed-budget claim and a comparison table against a per-payment cap. Its opening snippet **now runs** — it did not, and that is [F-A12](#f-a12--the-documented-api-did-not-exist--2026-08-17)
- [ ] A9.5a `docs/vs-sdk-spend-controls.md` — an honest side-by-side against x402's own `maxAmountPerPayment`. State what theirs does well and where it stops. A comparison that pretends the alternative is nothing is not read twice
- [ ] A9.5b Test the composition, not just the claim: `tesoro` running **with** SDK spend controls enabled, where their per-payment cap is one envelope among ours ([B3.6](../aegs/PLAN.md))
- [x] A9.6 [`docs/quickstart.md`](docs/quickstart.md) — install to verified evidence in seven steps, every command **run from the installed wheel** rather than from the source tree. That is how both config bugs were found, and it is also where the Git Bash `MSYS_NO_PATHCONV` trap is documented: a resource starting with `/` is silently rewritten into the evidence record, which is worse than an error
- [x] A9.7 [`CHANGELOG.md`](CHANGELOG.md), with a **Fixed** section that names each defect and a **Known limitations** section stated up front rather than buried. A changelog that lists features and hides fixes tells a reader what was added and not what was wrong
- [x] A9.8 Stated in the README, the CHANGELOG header and the quickstart's closing section — the last one deliberately placed where a reader is about to point this at a wallet
- [x] A9.9 **Trusted publishing (OIDC)** — [`release.yml`](.github/workflows/release.yml). No `password:` anywhere, and it is a gate rather than a button: the full suite on six OS/Python combinations, a tag-versus-`pyproject` version check, a refusal to republish an existing version, and a **clean-venv install that must refuse a payment** before anything uploads. One-time browser setup documented at the top of the file; until the publisher exists the publish step fails with a permissions error, which is correct because there is no token to fall back to
- [x] A9.10 **Published.** Two releases went out as `aegoll` (`0.1.0` on a long-lived token, `0.1.1` by trusted publishing), then the package was renamed and republished as **[`tesoro 0.1.0`](https://pypi.org/project/tesoro/)** with the `aegoll` project removed from PyPI. `pip install tesoro` pulls three packages into a clean venv and every quickstart step runs against the published artifact
- [ ] A9.11 Sealed experiment record for the packaged overhead, since packaging changed the import graph and the prototype's numbers were measured pre-split

**Exit:** ✅ clean-environment install works and caps a real agent's spend — `pip install tesoro`, `tesoro init`, `tesoro check`, `tesoro decide`, verified end to end from the public index.

---

## A10 — Local visual output 🔨

The vision: *"in the next version this can apply an optional localhost app giving
visual output"*. Deliberately after the CLI, deliberately not Streamlit — and split in
two, because half of it is worth having in 0.1.

**The design decision, made 2026-08-17.** `Governor.report()` already returns everything
a view needs as plain data, with a vendor-safe identity projection already applied — its
docstring says *"a report rendered in a browser must not carry the controller's details
just because the layer happens to know them."* So the package is already **one shape, N
renderers**; it just had exactly one renderer and that renderer needed Streamlit.

**Stdlib only. One self-contained HTML file. No npm, no build step.** Two reasons: a
package that sits next to a wallet should not ship a minified bundle nobody audited, and
the argument that justified a build step in the old plan — one SPA shared across a Python
*and* a TypeScript core — died when the TS core was dropped. Hand-written vanilla HTML,
CSS and JS, shipped as package data.

### A10a — `tesoro report --html`, in 0.1

A generated file, not a server. ~80% of the value at ~10% of the risk: no port, no
listener, no auth surface, and it is an artifact you can send to someone. A generated
file is not a localhost app, so pulling this forward does not contradict the vision.

- [x] A10a.1 [`html.py`](src/tesoro/html.py) renders from `Report`. Self-containment is **tested**, not intended: the suite greps the rendered bytes for absolute URLs, `src` attributes, `<link>`, `@import` and every call that can reach the network. Written as a module rather than a template file — the CSS and JS are inline because they must be, so a separate template would be a file that only ever gets embedded
- [x] A10a.2 `tesoro report --html [-o PATH]`, stdout by default so `> spend.html` works. `--html` with `--json` is a usage error rather than one silently winning, and `-o` without `--html` is too — accepting it and ignoring it would discard the file the user asked for
- [x] A10a.3 **Four panels**, each answering one question an agent developer has at 2am:
      **Policy** — which pack, its hash, rules in priority order, in plain terms → *what will this do?* ·
      **Envelopes** — both channels, every limit, headroom, **which one binds** → *how much is left?* ·
      **Decisions** — newest first, with the **attributed control** → *why did my agent stop?* ·
      **Evidence** — chain length and state → *can I trust this record?*
- [x] A10a.4 Absent limits render as **absent**, never `0`. Rendering an unset limit as `$0.00` would state the *tightest possible* ceiling where there is none — exactly inverted
- [x] A10a.5 The truncation caveat is on the page **next to the chain state**, and a test asserts that ordering: in a footnote below everything it would be technically present and practically absent
- [x] A10a.6 The renderer prints what it is given and filters nothing — filtering belongs in `reporting.build`, where the projection is documented, not scattered through a template. Tested from the other side: `Report`'s wire format carries no `controller`, `wallets` or `spendingLimits`, so the page cannot leak what it is never handed
- [x] A10a.7 Done, and wider than asked: also no `src` attribute, no `<link>`, no `@import`, no `fetch`/`XHR`/`WebSocket`/`sendBeacon`/`EventSource`. A relative `href="style.css"` has no protocol and would have passed the narrow check while making the file useless once mailed
- [x] A10a.8 Reframed and tested as the property that actually holds: no key-shaped *field* exists in `Report`, and the test fails if one is ever added. A renderer guessing which strings are secrets would be the wrong mechanism in the wrong place
- [x] A10a.9 Asserted by property rather than by a golden file — the attributed-control column, the deciding reason, all four panel headings, the rule order, escaping per field. A golden file fails on every whitespace change and teaches people to regenerate it without reading

**Exit:** ✅ a single file a developer opens after a run and immediately sees which control refused what. 18 tests, and the escaping ones verified by removing `_e()` from one field and watching them go red.

### A10b — `tesoro serve`, v0.2

The same template, fed live. Same renderer, second transport.

- [ ] A10b.1 Read-only HTTP API specified in `docs/read-api.md` — one small versioned JSON surface
- [ ] A10b.2 Server is **stdlib `http.server`**. If that proves untenable, a dependency goes behind an `tesoro[serve]` extra — never into the core
- [ ] A10b.3 **`127.0.0.1` only.** Never defaults to `0.0.0.0`
- [ ] A10b.4 Off unless `tesoro serve` is run explicitly
- [ ] A10b.5 **No mutation endpoints. Ever.** Not even resolving a review — that is the CLI's job. Read-only is the whole security model, and it is what makes an unauthenticated page acceptable
- [ ] A10b.6 Test: binding to a non-loopback address requires an explicit flag **and** prints a warning
- [ ] A10b.7 Test: every route is read-only — no request of any method mutates policy, envelope or journal
- [ ] A10b.8 Test: the page renders from the API alone, so the two transports cannot drift

**Exit:** `tesoro serve` shows what the CLI says, on localhost, with no new attack surface.

---

## A11 — The three open red-team findings ⬜

Carried from the prototype. **Each needs a control that does not exist** — none is
fixable by tightening a limit.

- [x] A11.1 **18-attack red-team suite ported and running in CI** — `redteam/` plus `tests/test_redteam.py`, scored against `redteam/baseline.json`. **14 defended, 1 defended by accident, 3 undefended, 0 error**, against the package installed from PyPI rather than `src/`. Sealed as [EXP-008](../aegs/research/experiments/EXP-008).

  The `redteam` CI job already existed and had never run anything: it tested `-d tests/redteam`, a path the suite was never at, and printed a note when absent. **The false branch of that condition was a green job**, so every commit since it was added reported a passing red-team check against nothing. Now unconditional, with a step that asserts the suite files are where the tests expect them.

  Porting found three defects in the *harness*, each of which had made the score wrong in a plausible direction:
  - **the runner reimplemented attribution and disagreed with the layer.** It walked `reversed(decision.reasons)` and took the last refusing one; on budget fragmentation the daily envelope binds as `treasury/envelope_exceeded:daily` and a policy rule then *observes* the same fact, so the observation won and the runner credited `policy` where `attributed_control` says `treasury`. **Two of three apparent surprises were that artefact.** `attributed_control`'s own docstring names the hazard — three components disagreeing about which control refused would be three answers to a question with one — and the runner had become the fourth. It delegates now, and a test parses the function to prove it
  - **RT-ECON-002 could not reach the control it names, for the second time.** $0.50 x 12 vendors was $6 against a $50 envelope; the "fix" of $5.00 x 12 is refused on the *first* call, because $1.00+ to an unknown counterparty is REVIEW by `review-untrusted-vendor-nontrivial` — so the loop broke at `i=0` having moved $0.00 and reported a defence. Now 101 x $0.50, with the resource varying per counterparty so the per-resource envelope cannot bind at the same instant and steal the attribution
  - **RT-ECON-003 farmed trust outside the window it was farming.** 200 settlements placed 60–52 days back, against a 30-day aggregation window. Fixed; the outcome did not change, and knowing the fix changed nothing is worth more than a passing run that never measured the thing
- [x] A11.1a Two stale `expected` values corrected — truncation was believed defended (it is not; open finding 1) and trust farming believed open (it is refused, by the wrong control). Surprise count is now zero **by intent**: a suite that reports known gaps as surprises every run buries the surprises that matter
- [ ] A11.2 Add advisor prompt-injection attacks — vendor-supplied text reaching the advisor. The one open build item from the prototype, and the single place `promptmap`/`garak` genuinely fit
- [~] A11.3 **Design doc written** — [`docs/design/behavioural-monitoring.md`](docs/design/behavioural-monitoring.md). The engine is **not** built and the doc says so at the top. **It needs one decision before any code: AEGS 0.2 clauses, or a tesoro-only control.** Recommendation is 0.2 for the count envelope, because a control the standard cannot express is one no independent implementation can ever be scored on, and W6.4 is already the largest open question.

  What the doc establishes, all of it measured rather than reasoned from descriptions:
  - **Both open economic findings have one cause.** tesoro bounds value and it bounds rate; it never bounds **count over any window longer than an hour**. Neither attack is a loose threshold — lowering `daily_usd` does not help because $0.04 is not near $50, and lowering `velocity_1h` does not help because the attacker paces under whatever it is
  - **The fix is a count envelope, not a heuristic.** `actions_per_day` and friends, the existing envelope arithmetic over a quantity it ignores. Deterministic, attributable, no intent inference — and **it closes RT-ECON-004 while only *bounding* RT-ECON-001**, which the doc states plainly rather than discovering in the after-comparison. 40 actions is not near 500 at any setting a real agent could work under
  - **Sequence-shape detection is recommended NOT to be built.** It cannot distinguish structuring from a polling loop, because the sequences are identical and the difference is intent — and inferring intent needs a model, which invariant 1 forbids in the decision path. It could only ever produce REVIEW. A control every operator disables is worse than an absent one: it appears in the conformance surface and defends nothing
  - **The fact surface cannot support it anyway.** `agent_amounts` is a bag of values with *no timestamps*, so nothing in the snapshot can answer "what were the intervals". Layer 1 needs four `count()` calls beside four existing `spent()` calls; Layer 2 changes the shape of the fact base
- [ ] A11.3a **`agent_amounts` is a count window, not a time window** — `ORDER BY at DESC LIMIT 200`. Measured: an agent with 40 varied purchases, given 200 trivial ones, keeps **200 rows all of $0.001** and the 40 real ones are gone. An agent's entire spending history is erasable from the risk baseline by volume alone. Needs a time bound as well as a count bound. Found while writing A11.3; ships separately, it does not belong to the new engine
- [ ] A11.3b **`amount_zscore` returns a hardcoded `6.0` when it has no dispersion to report** — so after the erosion above, $0.01 and $100,000 are both "6.0". The function stops measuring anything. It **fails safe** (6.0 saturates against `zscore_saturation: 4.0`, the conservative direction) which is why nothing noticed: verdicts do not loosen, but an input weighted `weight_zscore: 0.20` has silently become a constant and `risk.score` gives an operator no way to tell
- [~] A11.4 **Structuring: bounded, not closed.** 40 × $0.001 paced five minutes apart still moves money with nothing refused, and that is correct rather than outstanding work. A count envelope bounds the *mechanism* — 2,328 actions a day is now impossible — and does not refuse 40 actions in an afternoon, because 40 trivial purchases is also what a legitimate agent does. `tests/test_count_envelopes.py::test_structuring_is_bounded_but_still_not_refused` asserts it stays open so the result cannot be rounded up. Refusing the instance needs a control reading the shape of a sequence, and that control is recommended **against** — see A11.3
- [x] A11.5 **Paced evasion closed** — `actions_per_day` and `actions_per_month`. The red-team attack moved UNDEFENDED → DEFENDED, refused at action 500, attributed to `treasury` citing `actions_per_day`. Sealed as [EXP-010](../aegs/research/experiments/EXP-010).

  **It needed no new control, no new engine and no new clause.** `AEGS-0.1-ENV-7` has permitted count envelopes since 0.1 and fixes their semantics; this repository already implemented two of them, `velocity_60s` and `velocity_1h`. **The entire gap was that no count window was longer than an hour.** A rate limit bounds the rate, and no product of a rate limit and a duration is ever compared against anything.

  The change is two policy keys, two snapshot fields (`count_today`, `count_month`) and two entries in the treasury's counter tuple. An absent key yields `None` and appends no envelope (ENV-8), so an existing pack is unaffected on upgrade; `0` forbids everything and a negative limit is refused at load.

  **An AEGS 0.2 clause family was drafted for this and thrown away.** The belief that the standard could not express a count envelope came from grepping clause *families*, seeing `ENV`, and never reading ENV-7 — the fourth instance of a conclusion drawn from a partial read. ENV-7's own note then said *"a count limit constrains rate, not total"*, which is false of the mechanism and true only of the short windows anyone had used; it pointed away from the answer it contained. That sentence and `SEC-6` are both corrected in the standard.
- [ ] A11.5a **Make the requirement a requirement, or say it is not one.** ENV-7 is a `MAY`, so an implementation that declares no long-window count envelope is fully conformant and fully exposed — and `AEGS-CONF 7/7` tells an adopter nothing about it. Promoting it is a **profile** change, not a clause edit, and it invalidates every conformance claim recorded against the current profiles. Deliberately not taken with the fix; `SEC-6` now states the gap explicitly instead
- [ ] A11.6 **Evidence anchoring**, design doc first. Closes finding 1: **journal truncation is undetectable**, because any prefix of a hash chain is a valid chain, so an agent that gets refused can delete the refusal. Editing and middle-deletion *are* caught
- [ ] A11.7 Do **not** ship a `head.json` beside the journal. It would look like a fix and defend against nothing. The fix is an external anchor
- [ ] A11.8 Each new engine gets vectors in `aegs/vectors/` before it gets an implementation
- [x] A11.9 **Both halves sealed.** Before: [EXP-009](../aegs/research/experiments/EXP-009) at 14/1/3/0, which supersedes EXP-008 because its `RT-ECON-004` measurement was wrong. After: [EXP-010](../aegs/research/experiments/EXP-010) at **15/1/2/0**. Same suite, same clock, and the policy hash changed between them (`a5a64aeb…` → `46abca35…`) because the change *was* a policy change — recorded in EXP-010 rather than left for a reader to notice.

  EXP-009 predicted that the two economic findings, sharing one cause, "must close with one control — if they close with two, one was misdiagnosed." **One cause, one control, and only one closed.** The prediction was too strong: sharing a cause does not imply sharing a remedy, because bounding a total is free and refusing forty ordinary-looking actions is not. EXP-010 records the correction.

**Exit:** 18+ attacks in CI, three findings closed by controls rather than by thresholds.
**Half done:** the attacks are in CI and the baseline is sealed. The three findings are open,
and `redteam/baseline.json` now fails the build if any of them closes without the four
documents that describe them as open being revisited in the same commit.

---

## A12 — Enterprise surface (undecided) ⬜

The vision says *"some enterprise features in the future (have to decode)"*. The
decode is the deliverable here, not a feature.

- [ ] A12.1 Write the test every candidate must pass: **does this turn a library into an operation with uptime obligations?** If yes, it is not in `tesoro`
- [ ] A12.2 Candidate list with that test applied — multi-tenant policy management, SSO/RBAC on the review queue, hosted evidence anchor, shared policy registry, signed policy packs, SIEM export, air-gapped conformance reporting
- [ ] A12.3 Note which candidates are *specification* work in `aegs` rather than product work here — signed packs and evidence anchoring both are
- [ ] A12.4 Licence position: does an enterprise tier mean a second licence, and does that conflict with Apache-2.0 + standards ambition? Answer before building, not after
- [ ] A12.5 Decision recorded in `../CONTEXT.md`, not left implicit

**Exit:** a written position on what enterprise means here, and what it explicitly does not.

---

## Invariants — carried forward, not up for renegotiation

Full list and reasoning in [`../CONTEXT.md`](../CONTEXT.md).

- No model in the decision path. Advisor optional, gated, clamped.
- Policy packs are data. Never code.
- Money never touches a float. Integer atomic units throughout; conversion once at the boundary with specified rounding.
- Absent ≠ not-run ≠ unknown ≠ zero. Four distinct states.
- Every engine may only narrow a verdict.
- Two channels never share an envelope — internal (tokens, real USD) and external (USDC, on chain).
- BYOK keys are never stored, logged, or journalled.
- Identity is pseudonymous by default; `spendingLimits` is never disclosed to a counterparty.
- Evidence is append-only and hash-chained, with the truncation gap documented rather than papered over.
- Sealed experiments are superseded, never edited.

## Findings

Recorded as work lands. A plan with the wrong turns removed is not a plan.

### F-A12 · The documented API did not exist — 2026-08-17

Found by following [`docs/quickstart.md`](docs/quickstart.md) against a wheel installed into a
clean virtual environment. Not visible from the source tree, and not visible to 632 passing tests.

`docs/api-surface.md` was written before the code on purpose — W0.4, on the reasoning that a bad
early API is the only permanent mistake available here. The code then grew a different shape
underneath and **nothing checked the two against each other.** `from tesoro import Governor`
returned `authorize.Governor`, the internal *rules evaluator*, which has no `load()`, no `wrap()`
and no keyword `authorize()`. The README's own opening snippet raised `AttributeError` on its
third line.

Three surfaces called `Governor` existed at once: the evaluator (exported), the prototype's
838-line `plugin.Governor` (working, with live consumers in `tesoro-integrations`), and the
documented facade (absent). The evaluator is now `RuleEngine`, the facade is `governor.py`, and
`plugin.Governor` keeps working undocumented so integrations do not break.

The general lesson is not "write the docs later". Writing the API first was right, and it produced
a better surface than the code had arrived at. What was missing is that **a designed API is only a
contract if something asserts it exists** — so `tests/test_governor.py` now runs the ten-line
snippet from that page verbatim, and an AST test fails if a facade method starts deciding anything
rather than delegating.

### F-A13 · Overspending at settlement bypassed every cumulative envelope — 2026-08-17

Found by exercising `settle(actual_amount_usd=...)`, which exists because the amount paid is not
always the amount quoted.

`record_settlement` journalled the settled amount as evidence and then called `mark_settled()`
**without it**, so `transactions.amount_atomic` kept the *authorised* figure — and every window
sum reads that column. A `$0.05` authorisation settled as `$5.00` consumed **`$0.05`** of a daily
ceiling.

Under-counting is the direction that matters. Over-counting would merely be conservative; this
made a cumulative envelope walkable by overspending at settlement, with the daily limit seeing a
hundredth of the money that moved. It is worth noting *where* the layer was already correct: the
real figure was in the evidence the whole time. The journal knew and the envelope did not, which
is the shape of bug that a report and a decision drifting apart always takes.

Fixed with a separate `settled_amount_atomic` column rather than by overwriting the authorised
one, because **both numbers are the interesting fact** — `authorised 0.05, settled 5.00` is a
discrepancy worth surfacing, and a single column that quietly became `5.00` would erase the
evidence that the layer approved something smaller. Negative settlements are refused: a negative
consumption would *create* headroom, which is ARITH-4 arriving at a boundary nobody had applied it
to.

### F-A14 · Evidence location was frozen at import — 2026-08-17

`DATA_DIR = Path.cwd() / ".tesoro"` was evaluated when the module was imported and captured in
`Paths.under()`'s default argument. So the journal's location was decided by whatever directory the
process happened to start in, permanently: a process that changed directory kept writing to the
original, and **two governors loaded from different directories shared one journal** — meaning one
agent's spending consumed the other's envelopes.

Found by this project's own test suite leaking state between tests: a revoked intent declared in
one test refused a payment in another, because `monkeypatch.chdir(tmp_path)` could not affect a
path resolved before the test began. Worth recording because the symptom looked like a test-isolation
problem and was a product defect — the tests were isolated, and the package was not.

A default argument evaluated once is a normal Python fact, and it is a trap specifically for
values that describe *where things are*. `default_data_dir()` is a function now.

### F-A9 · Delegation escalation, reachable by leaving a field out — 2026-08-17

Found by writing [AEGS-0.1-ID-4](../aegs/spec/08-identity.md) and then checking whether the code
did what the clause said. It did not, and the gap was **live**: reverting the fix, a $1.00
payment was **APPROVED** under a parent capped at $0.002.

`_widens()` compared a delegate's limits against its delegator's and refused any that were
larger. That detects nothing when the delegate declares *no* limit: there is nothing comparable,
so nothing is refused — and the child's own per-action check did not fire either, because it had
no limit to check against. **Declaring nothing was strictly more permissive than declaring a
large number**, which is an escalation you reach by omission rather than by attack.

The docstring said the policy envelopes would cover it. They do not, and this is the part worth
carrying forward: **envelopes are treasury-scoped and never inherit an identity's ceiling**, so
nothing downstream of the identity check knew a tighter limit had been declared upstream. Two
mechanisms that each look sufficient can leave a gap precisely between them.

ID-4 says *clamp to the narrower* rather than *refuse the wider* for this reason — a clamp has to
produce a number, so there is no case it can quietly fail to cover. `_widens()` keeps its
narrower job: a delegate claiming a *larger* number is worth refusing outright rather than
silently clamping, because it is a statement of intent.

Both new tests were checked against the old code before being trusted. One failed, which is the
only evidence that it tests anything.

### F-A10 · The four states were a boolean — 2026-08-17

Found by writing [AEGS-0.1-STATE-1](../aegs/spec/06-four-states.md).

`profiles._is_evidence` answered *did this control run* and discarded **which of the four ways it
did not**. That is enough for MUST_EXERCISE scoring, which only needs the boolean, and not enough
for a record a human reads: *we never asked* and *we asked and the answer was nothing* are a gap
and a measurement, and the record showed them identically.

Now `tesoro.states` classifies `absent` / `not-run` / `unknown` / `zero`, plus `no-opinion` for
[STATE-4](../aegs/spec/06-four-states.md) and `measured` for the ordinary case. `is_evidence` is
**defined through** the classifier rather than beside it, so there is one rule; its behaviour is
unchanged, which is why the existing 532 tests still passed unmodified.

Worth noting what the first draft got wrong: a helper that classified a *reported* value returned
`zero` on every branch, which would have called `score: "0.25"` a zero. Caught by reading it back
rather than by a test — none of the 11 vectors happened to carry a non-zero measurement, because
the spec's four states are the four ways a value can fail to be ordinary and nobody thought to
vector the ordinary case.

### F-A11 · Twenty-eight vectors that ran nothing — 2026-08-17

The 38 vectors added alongside spec sections 02, 06, 08, 09 and 10 used five operations the
`tesoro` runner had no arm for. `tools/lint_normative.py` counted their clauses as tested, the
coverage report said 56 clauses, and **28 of the vectors executed no implementation code at all**
— they errored as unrunnable, and had the runner been more forgiving they would have skipped
silently.

Caught by running the suite before committing rather than by trusting the coverage number.

The general form is worth keeping: **a coverage count is not a coverage claim unless something
executes.** A linter that reads a `clause` field is measuring intent. The same shape had already
appeared once as [F-C1](../tesoro-integrations/PLAN.md) — a test scanning a path that resolved to
nothing — and it will appear again, which is why both are recorded rather than just fixed.

Closed with five arms, each driving real code — the state classifier, the packaged profile
manifests, the identity disclosure filter, the delegation clamp — rather than restating the rule
in the test, which would have made each vector a test of the test. One further defect surfaced
while writing them: the generic assertion took `next(iter(expected))` and checked **only the
first key**, so a vector asserting `requiredCount` *and* `enforces` would have had half of itself
ignored. Replaced by `_assert_every_key`.

### F-A8 · Sixty-four-bit hashes, in five places — 2026-08-17

Found by writing [AEGS-0.1-EVID-5](../aegs/spec/07-evidence.md), not by a security review.

Every hash in the package retained 64 bits — `hexdigest()[:16]` written out in `audit.py`,
`authorize.py`, `config.py` and `settings.py`, agreeing with each other only by luck. A number
repeated five times is a number that drifts.

Sixty-four bits matters because altering a hashed artefact undetectably needs a **second
preimage**: 2⁶⁴ work, which commodity GPUs reach in months. Three distinct things were
affected, all by the same concern — the evidence chain (an entry commits to its predecessor),
the decision hash (`replay` compares against it, so a second preimage makes the determinism
check confirm something false), and the content hashes for config and policy (the AEGS Policy
schema prefers a hash to a label *because* a label can be reused across edited rules, and a
second preimage restores exactly the weakness the hash was chosen to remove).

Now 128 bits, routed through one `hashing.py` so the length exists in one place with the
reasoning beside it. Cheap now; a migration once anything published depended on the old
values.

### F-A5 · `binding` is not `tightest` — 2026-08-17

Found while writing [AEGS-0.1-ENV-6](../aegs/spec/03-envelopes.md), not by any test here.

`Report` marked the **binding** envelope under a heading meaning *the limit closest to
biting*. Those are two questions: `binding` answers *why was this refused* and is `None` for
an approved decision; `tightest` answers *what will bite next* and always exists. So the
envelope panel's most useful column was blank on every approved decision — precisely when a
developer is checking headroom.

None of the 449 tests here could have caught it. The code did exactly what it said; the
defect was in what the two concepts *meant*. Only writing them down as separate normative
requirements made the conflation visible, which is the case for writing the spec and the
implementation in the same session rather than one after the other.

### F-A1 · The prototype was never a package — 2026-08-17

Moving to the `src/` layout in the first commit of the port ([A1.4](#a1--port-and-rename-))
turned 249 passing tests into **35 failed, 80 passed, 134 errors**. Not a porting mistake:
the flat layout inside the monorepo had been hiding that the package **reaches outside
itself in eleven places**, and every one of them breaks in an installed wheel.

| Where | Reaches for | Breaks because |
|---|---|---|
| `config.py:22` | `policies/` via `parents[1]` | in a wheel, `parents[1]` is `site-packages` |
| `config.py:23,34` | `.env` via `parents[2]` | a library walking up the tree for dotenv files reads files the caller never offered it |
| `runtime.py:31` | `.data/` beside the package | writes into `site-packages` |
| `record.py:46` | `../aegs/schemas/decision-record-0.1.json` | no sibling `aegs/` exists outside the monorepo |
| `engines/economic/intent.py:58` | `../../aegs/schemas/economic-intent-0.1.json` | same |
| `engines/evidence/identity.py:48` | `../../aegs/schemas/agent-identity-0.1.json` | same |
| `adapters/x402_python.py:32` | path-hack into `../agents/x402_core` | same |
| `app.py:23`, `ui_demo.py:25` | `sys.path` manipulation | leaving the package anyway ([A2.1](#a2--purity-get-the-package-out-of-the-ui-business-)) |
| `tests/test_{engines,plugin,ui,ui_keys}.py` | `parents[1]/"aegl"` | asserts on the old layout |

**One of these is more than packaging.** The `.env` walk resolved a path from `parents[2]`
**and ran at module import time in two places**, so importing the package read a file the
caller had never offered it and wrote the contents into `os.environ`. In a library that also
handles BYOK keys that is a security problem rather than a convenience.

> **Correction, same day.** A first draft of this finding also claimed the three engines
> *read* AEGS schemas at import time, which would have made invariant 7 quietly untrue. They
> do not. Only `SCHEMA_PATH` is computed at import — a `Path` construction with no I/O — and
> the read happens lazily inside `load_schema()`, with `import jsonschema` local to the
> validating function and a clean fallback when it is absent. That part of the prototype was
> already right. The defect is narrower than stated: the path points outside the package.

This is the failure the `src/` layout exists to surface, found in the first commit here
rather than in the first bug report after publishing. Fixed as [A1.10–A1.16](#a1--port-and-rename-),
one concern per commit.
