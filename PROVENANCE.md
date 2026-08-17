# Provenance

Code in this repository was **ported** from the proof-of-concept at
[`Jayzilva/x402`](https://github.com/Jayzilva/x402), which is read-only and stays intact.

Porting was done by copying rather than by `git subtree split`, so `git log --follow` here
does **not** reach the prototype's commits. That cost was accepted deliberately; this file and
the commit trailers are the mitigation. Reasoning: [`../x402-REFERENCE.md`](../x402-REFERENCE.md).

**Source commit:** `e3e295b` (branch `agents`) — the state that recorded 7-of-7 build stages,
344 tests, AEGS-CONF 7/7, and an 18-attack red-team suite.

Every porting commit carries a trailer:

```
Ported-from: Jayzilva/x402@e3e295b <source path>
```

and copies the file **faithfully first**. Any change to a ported file is a separate, later
commit — a port and a rewrite in one commit hides which is which.

---

## Ported

| Here | From `Jayzilva/x402` | Commit | Notes |
|---|---|---|---|
| _(nothing yet — [A1](PLAN.md))_ | | | |

## Planned

Manifest owned by [`../x402-REFERENCE.md`](../x402-REFERENCE.md) P3; this table is the
destination-side view.

| Here | From | Task |
|---|---|---|
| `src/aegoll/` | `aegl/aegl/` (engines, domain, store, authorize, record, config, clock, runtime) | [A1.2](PLAN.md) |
| `src/aegoll/advisors/` | `aegl/aegl/advisors/` — 4 backends + BYOK key handling | [A6.6](PLAN.md) |
| `src/aegoll/adapters/x402.py` | `aegl/aegl/adapters/x402_python.py` | [A7.6](PLAN.md) |
| `src/aegoll/cli.py` | `aegl/aegl/cli.py` | [A5.1](PLAN.md) |
| `policies/` | `aegl/policies/{default,strict}.yaml` | [A3.9](PLAN.md) |
| `tests/` | `aegl/tests/` — 249 tests | [A1.7](PLAN.md) |
| `tests/redteam/` | `security/redteam/` — 18 attacks | [A11.1](PLAN.md) |

## Deliberately not ported

Not-ported is a decision, not an omission.

| Not ported | Why |
|---|---|
| `aegl/aegl/{app,ui,ui_demo,ui_keys,crossview}.py` | Streamlit cockpit — a library must not drag a web framework into every install. Goes to `aegoll-integrations` |
| `aegl/aegl/{scenarios,evaluation}.py` | Demo and measurement harnesses, not library surface. Goes to `aegoll-integrations` |
| The eleven 13-line re-export shims | Existed for an in-repo move. A fresh package does not need two import routes — and they are the shape that once made a purity test pass while checking nothing |
| `aegs/`, `conformance/` | Belong to the standard, not the implementation. Go to `aegoll/aegs` |
| `src/` (TypeScript), `docs/protocol/`, `data/` | The x402 rail itself. Stays in the POC, which keeps running as the seller examples buy from |
| `EXECUTION-PLAN.md`, `docs/DISTRIBUTION-PLAN.md`, `aegl/PLAN.md` | Superseded plans. Kept in the POC as the decision record — a log with the wrong turns removed is not a log |
| `docs/how-aegl-came-about.md`, `aegl_aegs.md` | The origin record. **Linked, never copied.** Copies drift |
| `research/` | Sealed experiment records stay where their commit stamps remain valid. New records go to `aegoll/aegs` |
