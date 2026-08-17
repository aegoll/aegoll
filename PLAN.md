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

## A0 — Blocking decisions ⬜

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

## A1 — Port and rename ⬜

**`../x402` is read-only** — it is the frozen POC and nothing is moved out of it
([`../x402-REFERENCE.md`](../x402-REFERENCE.md) R1–R2). So this is a **port**: copy in, re-commit
here, record provenance. `git log --follow` will not reach the prototype's commits, and
that cost is accepted deliberately, with a provenance index as the mitigation.

- [ ] A1.1 `PROVENANCE.md` created first, before any file arrives — every ported path, its source path, and source commit `e3e295b`
- [ ] A1.2 Copy `../x402/aegl/aegl/` → `src/aegoll/` and commit it **faithfully, unchanged**, with trailer `Ported-from: Jayzilva/x402@e3e295b aegl/aegl/`
- [ ] A1.3 Every subsequent change is a **separate commit after** the faithful copy. A port and a rewrite in one commit hides which is which
- [ ] A1.4 `src/` layout is the target: `src/aegoll/`. Prevents the classic "tests pass against the source tree, package ships broken"
- [ ] A1.5 Rename module `aegl` → `aegoll` throughout — imports, `pyproject.toml`, CLI entry point, docstrings
- [ ] A1.6 Keep `AEGL` in *prose* everywhere. The layer is an AEGL; the package is `aegoll`. Do not sed the concept away
- [ ] A1.7 `tests/` ported, **249 tests green** under the new name and layout — the number from [P2.1](../x402-REFERENCE.md) is the pass/fail bar for the port itself
- [ ] A1.8 Delete the eleven 13-line re-export shims (`aegl/aegl/{audit,eiap,escalation,identity,intent,policy,risk,roi,treasury,trust}.py`) — they existed for an in-repo move and a fresh package does not need two import routes
- [ ] A1.9 Re-run the purity test after deletion: `tests/test_no_llm.py` walks the tree, and the shims are exactly the shape that once made it pass while checking nothing

### Discovered during the port — see [F-A1](#f-a1--the-prototype-was-never-a-package--2026-08-17)

The package reaches outside itself in eleven places. Each gets its own commit so the diff
reads as a fix rather than as churn.

- [ ] A1.10 **Starter policies become package data** — `src/aegoll/policies/*.yaml`, resolved with `importlib.resources`, never with `Path(__file__).parents[n]`
- [ ] A1.11 **AEGS schemas become vendored package data** — `src/aegoll/_schemas/`, with a provenance header naming the `aegs` commit they came from and a CI check that they have not drifted. Three engines currently read them from a sibling directory of the monorepo
- [ ] A1.12 **The schema read moves out of import time.** `record.py`, `intent.py` and `identity.py` load a schema at module import, which is filesystem I/O inside the core — invariant 7, quietly untrue. Load lazily on first validation, and make validation optional so the core needs no `jsonschema` at all
- [ ] A1.13 **Delete the `.env` walk.** `config.py` resolves `.env` from `parents[2]`. A library that walks up the filesystem looking for dotenv files reads files the caller never offered it, and this one also handles BYOK keys. Keys come from the environment or from explicit config — never from a file the library went looking for
- [ ] A1.14 **`.data/` becomes caller-controlled.** `runtime.py` writes the journal and sqlite history beside the package, i.e. into `site-packages`. Default to `./.aegoll/` relative to the working directory, overridable by `evidence.journal` in config
- [ ] A1.15 **Remove the `x402_core` path-hack** from the rail adapter. It imports its dependency or fails with a clear message; it does not reach into a sibling repository
- [ ] A1.16 **Update the four tests** asserting on `parents[1]/"aegl"` to the new layout — and make them assert on the *package* location rather than a hardcoded relative path, so the next layout change fails loudly instead of silently
- [ ] A1.17 Add a test that **fails if any module resolves a path outside the package**. This is the check whose absence let all eleven survive
- [ ] A1.18 Add a test that installs the built wheel into a clean environment and imports it. Source-tree tests cannot catch this class of bug at all

**Exit:** 249 tests green **from an installed wheel**, `PROVENANCE.md` covers every ported path, no module named `aegl`, and no path resolved outside the package.

---

## A2 — Purity: get the package out of the UI business ⬜

Today `aegl` depends on `streamlit>=1.40` unconditionally. A library that drags a web
framework into every install will be declined by exactly the teams worth having.

- [ ] A2.1 Move `app.py` (853 LOC), `ui.py` (383), `ui_demo.py` (128), `ui_keys.py` (190), `crossview.py` (227) out — destination `../aegoll-integrations/` ([C4](../aegoll-integrations/PLAN.md))
- [ ] A2.2 Move `scenarios.py` (316) and `evaluation.py` (436) out — demo and measurement harnesses, not library surface ([C6](../aegoll-integrations/PLAN.md))
- [ ] A2.3 Remove `streamlit` from `dependencies`. Target dependency set: `pyyaml` only, with `jsonschema` as an extra for validation
- [ ] A2.4 Test: `tests/test_deps.py` asserts the installed-package import graph pulls in nothing outside the declared runtime set
- [ ] A2.5 Test: core purity — no module under `src/aegoll/engines/` may import `os.path`, `open`, `requests`/`httpx`, or read a wall clock. Clock is injected (`clock.py` already does this — enforce it)
- [ ] A2.6 Test: no module under `src/aegoll/` outside `advisors/` may import an LLM client. Extend the existing tree-walking `test_no_llm.py`
- [ ] A2.7 Test: `src/aegoll/engines/` imports nothing from `src/aegoll/adapters/` or `advisors/` — dependency arrow points one way only
- [ ] A2.8 Confirm the three engine families still import no sibling family (existing 20 tests from prototype S6)

**Exit:** `pip install aegoll` in a clean venv pulls `pyyaml` and nothing else; five purity tests green.

---

## A3 — Config and policy packs ⬜

Two separate things, and conflating them causes trouble later. **Policy pack** = what
the rules are (user-authored). **Profile** = which controls must exist (the standard,
see A4).

- [ ] A3.1 `src/aegoll/config.py` — one loader, `aegoll.yaml` and `aegoll.json` both accepted, same schema
- [ ] A3.2 Config schema: `profile`, `policy`, `channels.internal`, `channels.external`, `evidence.journal`, `advisor` (optional). Documented in `docs/configuration.md`
- [ ] A3.3 Policy pack loader — discovery by path or by name from `policies/`
- [ ] A3.4 Policy pack validation against the AEGS Policy schema (`aegs/schemas/policy-0.1.json`) with readable errors that name the offending rule id
- [ ] A3.5 Content hash over config **and** rules together, recorded in every audit entry. Already true in the prototype — carry it, do not rebuild it
- [ ] A3.6 `policy.version` may be a label; warn when it is not a hash. A label can be reused across edited rules; a hash cannot
- [ ] A3.7 **Policy packs stay data, never code.** Fixed comparator vocabulary (`gte`, `lt`, `in`, …), no `eval`, no import hooks. This is a security boundary: an executable policy pack downloaded from a registry is remote code execution wearing a governance hat
- [ ] A3.8 Test: a pack containing a Python expression, an import, or an unknown comparator is *rejected*, not partially applied
- [ ] A3.9 Ship two starter packs — `policies/dev-sandbox.yaml` (permissive, loud) and `policies/prod-strict.yaml`. Port `../x402/aegl/policies/{default,strict}.yaml` as the base
- [ ] A3.10 Ship the same two as `.json` so the JSON path is tested by use, not by a unit test alone
- [ ] A3.11 `aegoll init` writes `aegoll.yaml` + `policies/default.yaml` and nothing else

**Exit:** a policy change is validated, hashed, and traceable from any decision back to the exact numbers that produced it.

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
- [ ] A5.2 `aegoll init` — scaffold config + starter policy
- [ ] A5.3 `aegoll check` — validate config and policy, exit 1 if invalid. **The quiet win:** a policy change that would refuse everything, or allow everything, fails the build before it reaches an agent holding a wallet
- [ ] A5.4 `aegoll policy explain` — what this policy would do, in plain terms, rule by rule
- [ ] A5.5 `aegoll decide --amount 2.50 --vendor acme --dry-run`
- [ ] A5.6 `aegoll report` — what was spent, what was refused, and why, attributed to the control that decided
- [ ] A5.7 `aegoll audit` — verify the evidence chain
- [ ] A5.8 `aegoll conformance --profile aegs-1` — delegates to `aegs-conformance` if installed, says so clearly if not
- [ ] A5.9 `aegoll record [--export|--validate]` — emit or validate AEGS Decision Records
- [ ] A5.10 `aegoll intent` / `aegoll identity` — carried from the prototype
- [ ] A5.11 `--json` on **every** command. A CLI without machine output is a CLI nobody scripts
- [ ] A5.12 Exit-code table documented in `docs/cli.md`: `0` ok, `1` invalid config/policy, `2` refused, `3` chain broken, `4` usage
- [ ] A5.13 Drop `scenarios`, `bench`, `eval` from the shipped CLI — they move to integrations ([C6](../aegoll-integrations/PLAN.md)). Keep `replay`: determinism is a user-facing guarantee
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

## A10 — Local visual output (v0.2) ⬜

The vision: *"in the next version this can apply an optional localhost app giving
visual output"*. Deliberately after the CLI, and deliberately not Streamlit.

- [ ] A10.1 Read-only HTTP API specified in `docs/read-api.md` — one small JSON surface, versioned
- [ ] A10.2 Server: stdlib only if possible; a small dependency behind the `aegoll[serve]` extra if not
- [ ] A10.3 **`127.0.0.1` only.** Never defaults to `0.0.0.0`. It shows spending state and sits next to a wallet
- [ ] A10.4 Off unless `aegoll serve` is run explicitly
- [ ] A10.5 SPA views: active policy in readable form, live envelope headroom, decision stream with attributed reasons, evidence chain state
- [ ] A10.6 Built bundle shipped inside the package — no build step for the user, no node toolchain at install
- [ ] A10.7 Test: binding to a non-loopback address requires an explicit flag *and* prints a warning
- [ ] A10.8 Test: every read-API endpoint is read-only — no route mutates policy, envelope, or journal

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

**Two of these are more than packaging.** The `.env` walk is a security smell in a library
that also handles BYOK keys. And three engines **read AEGS schema files from disk at import
time**, which is filesystem access inside the supposedly pure core — invariant 7, quietly
untrue since whenever those lines were written, and invisible because the purity test checked
imports rather than I/O.

This is the failure the `src/` layout exists to surface, found in the first commit here
rather than in the first bug report after publishing. Fixed as [A1.10–A1.16](#a1--port-and-rename-),
one concern per commit.
