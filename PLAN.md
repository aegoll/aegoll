# `aegoll` — sub-plan

**The Python package.** An Autonomous Economic Governance Layer that installs in one
line, configures in one file, and runs from a CLI.

Master plan: [`../PLAN.md`](../PLAN.md) · Context and rules: [`../CONTEXT.md`](../CONTEXT.md)
Port source (**read-only**): `../x402/aegl/` (10,533 LOC, 249 tests, all green) — [`../x402-REFERENCE.md`](../x402-REFERENCE.md)

**Positioning:** `aegoll` is a **policy-engine host**. AEGS is one *profile* it can
enforce. The Decision Records and the conformance level are what it produces, mentioned
second.

> **Positioning revised 2026-08-17 after upstream research.** The old pitch was *"know and
> cap what your agent spends"*, resting on the claim that agent frameworks have no cost
> ceiling. That claim is expiring: the x402 SDKs themselves are adding spend controls
> (`maxAmountPerPayment` over `allowedAssets`, client-side, per-payment) in TypeScript,
> Python and Go — see [F3](../UPSTREAM-x402.md).
>
> **A cap on one payment is not a budget.** What `aegoll` adds on top of that baseline:
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

- [x] A0.1 **`aegoll` is free on PyPI** — `pypi.org/pypi/aegoll/json` → 404, checked 2026-08-17
- [x] A0.2 **`aegoll` free on npm** (404) and **`@aegoll/core` free** (404), checked 2026-08-17
- [x] A0.3 Import name `import aegoll`, matching the distribution name. No `aegl` alias published
- [x] A0.4 Licence: **Apache-2.0** for code — already the `LICENSE` in all three repos. The patent grant matters for anything with standards ambition

> **Found — one name is taken, and it is not the one that matters.** `aegs` **exists on
> PyPI** at 0.0.6 ("model for aegs."), unrelated. Harmless: `aegs` is a *specification*
> repo, not a Python distribution. The only thing that repo ever publishes is the
> conformance suite, and **`aegs-conformance` is free** (checked, 404). No rename needed —
> but never write `pip install aegs` in any document, because it installs a stranger's
> package.
- [x] A0.5 **Config format: `aegoll.yaml`, with `aegoll.json` accepted everywhere YAML is.** One loader, one schema, two syntaxes. No TOML — the vision says "YAML or JSON", and a third parser buys nothing but a third set of docs
- [x] A0.6 **Public API written before it exists** — [`docs/api-surface.md`](docs/api-surface.md)
- [x] A0.7 Two version lines: `aegoll.__version__` (semver) and `aegoll.AEGS_VERSION` (spec). Both stamped in every Decision Record and every conformance declaration
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
- [x] A1.5 Module renamed `aegl` → `aegoll` across 46 files; class `Aegl` → `Aegoll`; CLI entry point, widget keys and `pyproject.toml` with it — `0049ce9`
- [x] A1.6 `AEGL` left intact in prose everywhere
- [x] A1.7 **267 tests green** from the source tree — the 249 baseline plus 18 new guards
- [x] A1.8 The re-export shims deleted — **ten, not eleven** as this plan said. 13 internal import sites repointed across three import forms — `19a93c0`
- [x] A1.9 Purity tests re-run and green. `test_no_llm.py` walks the tree, so it stayed honest through the deletion

### Discovered during the port — see [F-A1](#f-a1--the-prototype-was-never-a-package--2026-08-17)

The package reaches outside itself in eleven places. Each gets its own commit so the diff
reads as a fix rather than as churn.

- [x] A1.10 **Starter policies are package data** — `src/aegoll/policies/*.yaml` via `importlib.resources` — `75b9ba5`
- [x] A1.11 **AEGS schemas vendored** — `src/aegoll/_schemas/`, three of thirteen (only those an engine validates against; copying all would imply controls this package does not implement), with `_schemas/PROVENANCE.txt` naming the source commit — `926f50a`
- [ ] A1.11a CI check that a vendored schema has not drifted from the standard at its pinned commit. **A validator running against a stale schema is worse than one that fails loudly** — the rule is written in `PROVENANCE.txt`, the enforcement is not built yet
- [x] A1.12 ~~Move the schema read out of import time~~ — **already correct in the prototype.** Reads are lazy inside `load_schema()`, `import jsonschema` is local to the validating function, and absent `jsonschema` degrades to a clear message rather than an error. Nothing to do; recorded so the claim is not repeated
- [x] A1.13 **The `.env` walk is gone** — `75b9ba5`. It ran at *import time* in two places, so `import aegl` read a file the caller never offered it and exported the contents into `os.environ`. Replaced by `load_env_file(path)`, which the caller names and which returns a dict rather than mutating the environment
- [x] A1.14 **Runtime state is caller-controlled** — `./.aegoll/` relative to the working directory, not beside the package. Demonstrated against the installed wheel: `site-packages` stays clean — `bf63141`
- [x] A1.15 **`x402_core` path-hack removed.** The adapter checks importability and names the extra to install — `bf63141`
- [x] A1.16 **The four layout-dependent tests fixed** via `tests/conftest.py` — `package_dir()` resolves from `aegoll.__file__`, so the tests check the package that is actually importable — `5a7c37d`
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

- [x] A2.1 `app.py`, `ui.py`, `ui_demo.py`, `ui_keys.py`, `crossview.py` moved to `../aegoll-integrations/cockpit/` — `8b825c6` / `237b7a2`
- [x] A2.2 `scenarios.py`, `evaluation.py` moved to `../aegoll-integrations/harness/`
- [x] A2.3 Runtime dependencies are **`pyyaml` alone**. `jsonschema` is the `schema` extra; a clean install pulls nothing else
- [x] A2.4 [`tests/test_deps.py`](tests/test_deps.py) — 16 assertions on the declared *and* imported surface. Verified by planting `import streamlit`: the guard fails
- [x] A2.5 Engines import no third-party code **at module scope**. Not "at all" — two import `jsonschema` inside a validation function, after the verdict, degrading cleanly when the extra is absent
- [x] A2.6 Model clients importable only under `advisors/`, enforced by test
- [x] A2.7 `engines/` imports nothing from `adapters/` or `advisors/`
- [x] A2.8 The three engine families still import no sibling family

**Exit:** ✅ a clean venv pulls `aegoll` + `PyYAML` and nothing else; 16 dependency
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

- [x] A3.1 [`settings.py`](src/aegoll/settings.py) — one loader; `aegoll.yaml`, `aegoll.yml` and `aegoll.json` all accepted, same schema. JSON is a YAML 1.2 subset so there is one parser, not two that can disagree
- [x] A3.2 Config schema: `profile`, `policy`, `channels.{internal,external}`, `evidence.journal`, `advisor`. Unknown keys at every level are rejected, not ignored
- [x] A3.3 Pack discovery by path or by stem, **de-duplicated with YAML winning** — both syntaxes ship, and two entries with one name make `--policy strict` ambiguous
- [x] A3.4 Validation names the offending rule id, and reports **every** problem rather than the first
- [x] A3.5 Content hash over config **and** rules, carried from the prototype. Config and pack hash *separately* — they version independently and a record carries both
- [x] A3.6 `LOOKS_LIKE_HASH` exposed for the label-vs-hash warning
- [x] A3.7 Packs stay data — fixed comparator vocabulary, no `eval`, `safe_load` only. The *enforcement* moved from evaluation time to load time, which is the substance of this task
- [x] A3.8 Tested: unknown comparator, unknown verdict, unknown fact, duplicate id, missing id, bad priority, malformed `between`/`in`, null comparison, unknown keys — each **rejected at load**, and `!!python/object/apply` fails to parse rather than executing
- [x] A3.9 Two starter packs shipped — **`default` and `strict`, not renamed**. Declined the `dev-sandbox`/`prod-strict` naming for two reasons: `default` is genuinely the default and is *not* a permissive sandbox (it rejects sanctioned counterparties, rejects over-balance, reviews at $100), so the name would misdescribe it; and `strict` is referenced **by stem** in CONF-005 in the `aegs` repo, so renaming breaks a conformance case — data that is supposed to be stable — for no user benefit
- [x] A3.10 Both shipped as `.json` too, **generated from the YAML and proven equivalent**: the content hash is over the parsed structure, so an identical hash means the two files really are one policy rather than merely looking similar
- [x] A3.11 `aegoll init` writes `aegoll.yaml` + `policies/default.yaml` and refuses to overwrite without `--force`. It **copies** the starter out rather than pointing at site-packages, so a user's first act with a policy is reading and editing their own copy

**Exit:** ✅ a policy change is validated **at load**, hashed, and traceable from any
decision back to the exact numbers that produced it. `aegoll init` then `aegoll check`
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

## A4 — Profiles ⬜

The adoption mechanism. Today the engines *are* the standard; the profile has to
become a configuration input.

- [ ] A4.1 `src/aegoll/profiles/` — `aegs-1`, `aegs-2`, `none` as declarative manifests, not code branches
- [ ] A4.2 A profile declares: which controls must be exercised, which must be *recorded*, and what makes a decision non-conformant
- [ ] A4.3 `ComplianceAssessment` records controls exercised *against the active profile* (half-supported in the prototype — finish it)
- [ ] A4.4 `profile = "aegs-1"` is the default in `aegoll init`, so a user emits conformant evidence without having read the spec
- [ ] A4.5 `profile = "none"` genuinely disables profile enforcement — the escape hatch has to work or people fork
- [ ] A4.6 Test: switching profile changes conformance scoring and touches **no** engine code
- [ ] A4.7 Test: AEGS-CONF still 7/7 under `aegs-1` after the refactor. This is the independent instrument; if it moves, the refactor changed behaviour

**Exit:** profile switchable in config alone, AEGS-CONF unchanged at 7/7.

---

## A5 — The CLI ⬜

The primary surface in v0.1, and the one that has to be good. The vision is explicit:
CLI output first, visual output optional and later. Non-interactive, CI-friendly,
meaningful exit codes.

- [ ] A5.1 Migrate `../x402/aegl/aegl/cli.py` (536 LOC, 12 subcommands: decide, scenarios, audit, replay, reviews, bench, eval, policies, record, intent, identity)
- [x] A5.2 `aegoll init` — landed with [A3.11](#a3--config-and-policy-packs-)
- [x] A5.3 `aegoll check` — landed with A3. Validates config **and** the pack it points at, reports every fault, exits 1. `--json` accepted before *and* after the subcommand, because users type it after
- [ ] A5.4 `aegoll policy explain` — what this policy would do, in plain terms, rule by rule
- [ ] A5.5 `aegoll decide --amount 2.50 --vendor acme --dry-run`
- [ ] A5.6 `aegoll report` — what was spent, what was refused, and why, attributed to the control that decided
- [ ] A5.6a **Firm up `Report`** from provisional to stable ([api-surface §3](docs/api-surface.md)). Four renderers now depend on one shape: the CLI table, `--json`, the HTML page ([A10a](#a10a--aegoll-report---html-in-01)) and the live API ([A10b](#a10b--aegoll-serve-v02)). `Governor.report()` already returns it; the work is deciding the field names once, deliberately, rather than four times by accident
- [ ] A5.7 `aegoll audit` — verify the evidence chain
- [ ] A5.8 `aegoll conformance --profile aegs-1` — delegates to `aegs-conformance` if installed, says so clearly if not
- [ ] A5.9 `aegoll record [--export|--validate]` — emit or validate AEGS Decision Records
- [ ] A5.10 `aegoll intent` / `aegoll identity` — carried from the prototype
- [ ] A5.11 `--json` on **every** command. A CLI without machine output is a CLI nobody scripts
- [ ] A5.12 Exit-code table documented in `docs/cli.md`: `0` ok, `1` invalid config/policy, `2` refused, `3` chain broken, `4` usage
- [x] A5.13 `scenarios` and `eval` dropped from the shipped CLI — a demo and a money-spending measurement, both now in integrations. `replay` kept: determinism is a user-facing guarantee
- [x] A5.13a **`bench` kept, against this plan's first draft.** It measures decision latency on the caller's own hardware, needs no framework, no key and no money, and it substantiates the layer's central performance claim. Moving it to another repository would put a core claim somewhere the user has to go looking for it
- [ ] A5.14 Test: every subcommand has a `--json` path and an exit-code assertion
- [ ] A5.15 Test: `--help` for every subcommand renders without importing an optional extra

**Exit:** the whole product usable from a terminal, scriptable, no UI anywhere near it.

---

## A6 — Custom policies, custom engines, and the LLM option ⬜

The vision's extensibility ask: users add their own policy types, and optionally their
own API keys for LLM-based policies.

- [ ] A6.1 Registry for user-defined **rule kinds** — declarative predicates over existing engine outputs, still data
- [ ] A6.2 Registry for user-defined **engines** — a Python entry point, because anything a pack cannot express is a missing engine, and the answer is a new engine rather than an escape hatch
- [ ] A6.3 Engine contract documented: pure function, integer atomic units in, verdict + attribution out, **may only narrow**
- [ ] A6.4 Test: a third-party engine that tries to *widen* a verdict is refused at registration, not at runtime
- [ ] A6.5 Test: a third-party engine that performs I/O fails the purity test like a first-party one
- [ ] A6.6 BYOK key handling — migrate `advisors/keys.py` (180 LOC). Keys are **never stored, logged, or journalled**; `masked()` remains the only display path
- [ ] A6.7 Four advisor backends carried over: Anthropic, OpenAI, Gemini, Groq (`advisors/`, ~1,200 LOC total)
- [ ] A6.8 `aegoll[advisors]` extra — core installs none of them
- [ ] A6.9 **The advisor stays clamped:** it may tighten a verdict, never widen one. Behind an economic gate (`eiap.py`). Carry the existing clamp tests verbatim
- [ ] A6.10 Document loudly in `docs/advisors.md`: **there is no model in the decision path.** A layer that needs a model to authorize a payment has lost its cost and latency guarantees
- [ ] A6.11 `docs/custom-policies.md` — a worked example of a user adding a rule kind and an engine end to end

**Exit:** a user can add a policy type and an engine without forking, and cannot use either to weaken the layer.

---

## A7 — Framework adapters, step by step ⬜

Optional extras. Nobody pays for a dependency they do not use, and **the core never
imports a framework**.

Order is by verified evidence, not by popularity: Claude SDK and Google ADK are the
two verified end-to-end in the prototype.

- [ ] A7.1 Adapter contract in `src/aegoll/adapters/base.py` — duck-typed, documented, testable without any framework installed
- [ ] A7.2 `aegoll[claude]` — Claude Agent SDK adapter
- [ ] A7.3 `aegoll[adk]` — Google ADK adapter
- [ ] A7.4 `aegoll[langgraph]` — LangGraph adapter
- [ ] A7.5 `aegoll[crewai]` — CrewAI adapter (new; no prototype precedent, so budget discovery time)
- [ ] A7.6 `aegoll[x402]` — the payment **rail** adapter. Migrate `adapters/x402_python.py` (245 LOC)
- [ ] A7.7 Rail adapter contract separated from framework adapter contract — they are different boundaries and merging them will hurt when AP2 arrives
- [ ] A7.8 Test: **no adapter is importable from the core**, and the core test suite passes with zero extras installed
- [ ] A7.9 Test: the duck-typed governor contract — a fake framework object satisfies it, proving the adapter is not the coupling
- [ ] A7.10 Carry the prototype's inverted-dependency rule: an agent does not import the governor; the governor wraps the agent. A test fails if that reverses — otherwise "plugin" and "dependency" are indistinguishable

**Exit:** four framework adapters and one rail adapter, each optional, core clean.

---

## A8 — Test vectors ⬜

Consumes [`../aegs/PLAN.md`](../aegs/PLAN.md) B4. The reason aegoll can be checked
against a spec rather than against itself.

- [ ] A8.1 Vendor `aegs/vectors/` into the test suite (git submodule or pinned download — decide, then record)
- [ ] A8.2 Vector runner in `tests/test_vectors.py` — one test per vector, failure names the vector id
- [ ] A8.3 Arithmetic family passes — atomic conversion, rounding, boundaries, negatives, overflow
- [ ] A8.4 Envelope family passes — headroom, cumulative vs per-transaction, windows
- [ ] A8.5 Verdict family passes — narrowing, attribution, evaluation order
- [ ] A8.6 Evidence family passes — record projection, canonical serialisation, chain hashes
- [ ] A8.7 The two known prototype vulnerabilities are vectors on day one: **the negative amount** (a `-$1000` request was approved, because every envelope asks `amount <= headroom` and any negative satisfies it) and **the 30-digit overflow** (crashed the layer instead of being refused by it)
- [ ] A8.8 Every failure classified in writing as *spec bug* or *code bug*, never quietly fixed

**Exit:** 100% of vectors green, and a written list of what the spec failed to say.

---

## A9 — Publish 0.1.0 ⬜

- [ ] A9.1 `pyproject.toml` complete — classifiers, urls, readme, licence, keywords
- [ ] A9.2 GitHub Actions: test matrix on 3.11/3.12/3.13, Windows and Linux (the prototype was developed on Windows; do not discover a path bug at install time)
- [ ] A9.3 Build with `python -m build`; check with `twine check`
- [ ] A9.4 TestPyPI upload, then install into a clean venv and run the quickstart from the published artifact
- [ ] A9.5 `README.md` — the governed-budget claim in the first sentence, the standard in the second paragraph. Do **not** lead with "spend cap": the x402 SDKs now have one, and a reader who knows that will bounce. Lead with envelopes, attribution and evidence ([F3](../UPSTREAM-x402.md))
- [ ] A9.5a `docs/vs-sdk-spend-controls.md` — an honest side-by-side against x402's own `maxAmountPerPayment`. State what theirs does well and where it stops. A comparison that pretends the alternative is nothing is not read twice
- [ ] A9.5b Test the composition, not just the claim: `aegoll` running **with** SDK spend controls enabled, where their per-payment cap is one envelope among ours ([B3.6](../aegs/PLAN.md))
- [ ] A9.6 `docs/quickstart.md` — governs an agent in under five minutes, no prior knowledge assumed
- [ ] A9.7 `CHANGELOG.md` started at 0.1.0
- [ ] A9.8 State plainly that the API may change before 1.0, and mean it
- [ ] A9.9 Trusted publishing (OIDC) configured — no long-lived PyPI token in a secret
- [ ] A9.10 Publish `0.1.0`
- [ ] A9.11 Sealed experiment record for the packaged overhead, since packaging changed the import graph and the prototype's numbers were measured pre-split

**Exit:** clean-environment install works and caps a real agent's spend.

---

## A10 — Local visual output ⬜

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

### A10a — `aegoll report --html`, in 0.1

A generated file, not a server. ~80% of the value at ~10% of the risk: no port, no
listener, no auth surface, and it is an artifact you can send to someone. A generated
file is not a localhost app, so pulling this forward does not contradict the vision.

- [ ] A10a.1 One HTML template as package data, rendered from `Report` — no network, no CDN font, no analytics, no outbound request of any kind. Self-contained or it is not auditable
- [ ] A10a.2 `aegoll report --html [-o PATH]`, defaulting to stdout so it pipes
- [ ] A10a.3 **Four panels**, each answering one question an agent developer has at 2am:
      **Policy** — which pack, its hash, rules in priority order, in plain terms → *what will this do?* ·
      **Envelopes** — both channels, every limit, headroom, **which one binds** → *how much is left?* ·
      **Decisions** — newest first, with the **attributed control** → *why did my agent stop?* ·
      **Evidence** — chain length and state → *can I trust this record?*
- [ ] A10a.4 Absent limits render as **absent**, never as `0`. Invariant 5, in the one place a reader will misread it fastest
- [ ] A10a.5 The **truncation caveat is printed on the page**, next to the chain state. A page that says "VALID" without it overstates what a hash chain proves
- [ ] A10a.6 Keys never rendered; keep the vendor-safe projection `report()` already applies
- [ ] A10a.7 Test: the rendered HTML contains no `http://`, `https://` or `//cdn` reference
- [ ] A10a.8 Test: a report containing a key-shaped string does not render it
- [ ] A10a.9 Test: golden-file render, so a template change that drops the attributed-control column fails

**Exit:** a single file a developer opens after a run and immediately sees which control refused what.

### A10b — `aegoll serve`, v0.2

The same template, fed live. Same renderer, second transport.

- [ ] A10b.1 Read-only HTTP API specified in `docs/read-api.md` — one small versioned JSON surface
- [ ] A10b.2 Server is **stdlib `http.server`**. If that proves untenable, a dependency goes behind an `aegoll[serve]` extra — never into the core
- [ ] A10b.3 **`127.0.0.1` only.** Never defaults to `0.0.0.0`
- [ ] A10b.4 Off unless `aegoll serve` is run explicitly
- [ ] A10b.5 **No mutation endpoints. Ever.** Not even resolving a review — that is the CLI's job. Read-only is the whole security model, and it is what makes an unauthenticated page acceptable
- [ ] A10b.6 Test: binding to a non-loopback address requires an explicit flag **and** prints a warning
- [ ] A10b.7 Test: every route is read-only — no request of any method mutates policy, envelope or journal
- [ ] A10b.8 Test: the page renders from the API alone, so the two transports cannot drift

**Exit:** `aegoll serve` shows what the CLI says, on localhost, with no new attack surface.

---

## A11 — The three open red-team findings ⬜

Carried from the prototype. **Each needs a control that does not exist** — none is
fixable by tightening a limit.

- [ ] A11.1 Port the 18-attack red-team suite from `../x402/security/redteam/` into this repo's CI
- [ ] A11.2 Add advisor prompt-injection attacks — vendor-supplied text reaching the advisor. The one open build item from the prototype, and the single place `promptmap`/`garak` genuinely fit
- [ ] A11.3 **Behavioural-monitoring engine**, design doc first — it must examine the *shape* of a sequence, not the size of a transaction
- [ ] A11.4 Closes finding 2: **microtransaction structuring** — 40 × $0.001 paced five minutes apart moved money with nothing refused
- [ ] A11.5 Closes finding 3: **velocity evasion** — pacing exactly at the rate limit is unbounded in total
- [ ] A11.6 **Evidence anchoring**, design doc first. Closes finding 1: **journal truncation is undetectable**, because any prefix of a hash chain is a valid chain, so an agent that gets refused can delete the refusal. Editing and middle-deletion *are* caught
- [ ] A11.7 Do **not** ship a `head.json` beside the journal. It would look like a fix and defend against nothing. The fix is an external anchor
- [ ] A11.8 Each new engine gets vectors in `aegs/vectors/` before it gets an implementation
- [ ] A11.9 Sealed experiment recording the red-team score before and after

**Exit:** 18+ attacks in CI, three findings closed by controls rather than by thresholds.

---

## A12 — Enterprise surface (undecided) ⬜

The vision says *"some enterprise features in the future (have to decode)"*. The
decode is the deliverable here, not a feature.

- [ ] A12.1 Write the test every candidate must pass: **does this turn a library into an operation with uptime obligations?** If yes, it is not in `aegoll`
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
