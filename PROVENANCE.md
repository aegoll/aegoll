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
| `src/aegoll/` | `aegl/aegl/` | `41dbe48` | 49 modules, 10 engines. Renamed `aegl` → `aegoll` in `0049ce9`; ten top-level re-export shims deleted in `19a93c0` |
| `src/aegoll/policies/` | `aegl/policies/` | `41dbe48` | Moved inside the package as package data in `75b9ba5` — the prototype resolved them from a sibling directory |
| `src/aegoll/_schemas/` | `aegs/schemas/` | `926f50a` | Three of thirteen, vendored. See `_schemas/PROVENANCE.txt` |
| `tests/` | `aegl/tests/` | `41dbe48` | 249 tests, all green. 18 guards added since |
| `pyproject.toml` | `aegl/pyproject.toml` | `41dbe48` | Rewritten for a real distribution in `0049ce9` |
| `docs/eval.md` | `aegl/EVAL.md` | `41dbe48` | |

## Planned

Manifest owned by [`../x402-REFERENCE.md`](../x402-REFERENCE.md) P3; this table is the
destination-side view.

| Here | From | Task |
|---|---|---|
| `tests/redteam/` | `security/redteam/` — 18 attacks | [A11.1](PLAN.md) |

Everything else in the A1 manifest landed with the package in `41dbe48` — `advisors/`,
`adapters/`, `cli.py` and the engine families all came across as part of `aegl/aegl/`.

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
