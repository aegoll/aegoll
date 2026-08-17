# Public API surface — `aegoll`

**Status: design, not yet implemented.** Written before the code on purpose. A bad early
API is the only permanent mistake available in this project; everything else can be revised.

**Version:** draft 1 · 2026-08-17 · Task [A0.6](../PLAN.md) · Applies to `aegoll 0.1.x`

Rule for this document: **if a symbol is not listed here, it is not public**, regardless of
whether Python lets you import it. The prototype exported 21 names from `__init__.py` and
had an 838-line plugin module; most of that was internal machinery that happened to be
reachable. This surface is deliberately about a third of that size.

---

## 1 · The whole thing in ten lines

```python
from aegoll import Governor

gov = Governor.load()                     # reads ./aegoll.yaml
agent = gov.wrap(my_agent)                # any framework, duck-typed

decision = gov.authorize(amount_usd="2.50", vendor="acme", resource="/market/snapshot")
if decision.approved:
    receipt = pay(...)                    # your payment call, on any rail
    gov.settle(decision, success=True)

print(gov.report())                       # what was spent, what was refused, and why
```

That is the surface 99% of users touch. Everything below is either a detail of those calls
or a deliberately smaller door for a narrower audience.

---

## 2 · Stability tiers

Every public symbol carries one of these. They appear in the docstring and in this table
only — never as a decorator, because a stability marker that changes behaviour is a lie.

| Tier | Means |
|---|---|
| **stable** | Will not break within `0.x`. A breaking change needs a major bump and a migration note |
| **provisional** | Expected to change before `1.0`. Documented, tested, and explicitly not frozen |
| **internal** | Not public. May move or vanish in a patch release. Importable is not the same as supported |

`0.x` overall means the API may change before `1.0`, and [A9.8](../PLAN.md) requires saying
so plainly on the README. **stable** within `0.x` is a real promise about the minor series,
not a claim of finality.

---

## 3 · Tier 1 — the governance surface

### `Governor`

**stable.** One instance governs one agent session. The only object most users construct.

```python
class Governor:
    @classmethod
    def load(cls, path: str | Path | None = None) -> "Governor": ...
    @classmethod
    def from_config(cls, config: "Config") -> "Governor": ...

    # --- the decision path ---
    def authorize(
        self,
        *,
        amount_usd: str | int,
        vendor: str,
        resource: str,
        channel: str = "external",
        purpose: str | None = None,
    ) -> "Decision": ...

    def settle(self, decision: "Decision", *, success: bool = True,
               actual_amount_usd: str | int | None = None) -> None: ...

    # --- declarations, before acting ---
    def declare_intent(self, *, purpose: str, budget_usd: str | int,
                       expires_in_s: int, resources: list[str] | None = None) -> str: ...
    def revoke_intent(self, intent_id: str) -> bool: ...
    def register_identity(self, *, agent_id: str, controller: str,
                          parent_id: str | None = None) -> None: ...

    # --- framework integration ---
    def wrap(self, agent: Any) -> Any: ...

    # --- reading state ---
    def budget(self, channel: str = "external") -> "Envelope": ...
    def report(self) -> "Report": ...
    def decisions(self, limit: int | None = None) -> list["Decision"]: ...

    # --- lifecycle ---
    def close(self) -> None: ...
    def __enter__(self) -> "Governor": ...
    def __exit__(self, *exc: Any) -> None: ...
```

**Decisions written into these signatures, each on purpose:**

- **`load()` is the default constructor.** Configuration lives in a file, not in a
  constructor call with fourteen keyword arguments. `from_config()` exists for tests and
  for callers building config programmatically.
- **`authorize()` is keyword-only.** `authorize(2.50, "acme")` is the kind of call that
  eventually gets its arguments swapped, and here that means paying the wrong party.
- **`amount_usd` accepts `str` or `int`, never `float`.** A `str` is a decimal amount
  (`"2.50"`); an `int` is **atomic units**. Passing a `float` raises `TypeError` — invariant
  3, enforced at the boundary rather than trusted. This is the single most opinionated
  choice on this page and it stays.
- **`authorize()` then `settle()`, always two calls.** The prototype's `authorize_run` /
  `check_spend` / `settle_run` triple conflated deciding with observing. A decision is made
  before money moves; a settlement records what actually happened. Envelopes update on
  settle, not on authorize, so an abandoned decision does not consume budget.
- **`settle()` takes the `Decision`, not an id.** It cannot be called for a decision that
  was never made, and the type system says so.
- **`channel` is a plain string, `"internal"` or `"external"`.** An enum here buys nothing
  a validated string does not, and costs an import in every caller.
- **No `precheck` and no `record_override`.** Both were prototype conveniences that leaked
  the decision path. Overrides are a review-queue operation, not a governor method.
- **`wrap()` returns the same duck type it was given.** It never requires a framework
  import, and the core never imports a framework — invariant 8. An agent does not import
  the governor; the governor wraps the agent.

### `Decision`

**stable.** Immutable. What the layer decided and, critically, *why*.

```python
@dataclass(frozen=True)
class Decision:
    id: str
    verdict: str                    # "APPROVE" | "REVIEW" | "ESCALATE" | "REJECT"
    attributed_control: str         # which control decided — never None
    reason: str                     # human-readable, safe to log
    amount_atomic: int
    channel: str
    vendor: str
    resource: str
    assessments: Mapping[str, "Assessment"]
    policy_hash: str
    aegoll_version: str
    aegs_version: str

    @property
    def approved(self) -> bool: ...          # verdict == "APPROVE"
    def as_dict(self) -> dict: ...           # JSON-safe, canonical key order
    def as_aegs_record(self) -> dict: ...    # AEGS Decision Record projection
```

- **`attributed_control` is required and never `None`.** A verdict without an attributed
  control is the exact defect the prototype's conformance suite found twice: right answer,
  no evidence the control existed. Conformance scores attribution, so the type enforces it.
- **`verdict` is a string, not an enum.** It crosses a JSON boundary in every direction; a
  string is what it already is on both sides.
- **`as_dict()` guarantees canonical key order.** Key order, number formatting and
  separators change the hash — invariant 11 — so this is a serialisation contract, not a
  convenience.
- **Both version lines are on every decision** ([A0.7](../PLAN.md)). A record that does not
  say which spec and which implementation produced it cannot be audited later.

### `Envelope`

**stable.** A budget's state. Read-only.

```python
@dataclass(frozen=True)
class Envelope:
    channel: str
    limits: Mapping[str, int | None]     # atomic units; None means "no such limit"
    spent: Mapping[str, int]
    binding: str | None                  # which limit is closest to biting
    def headroom_atomic(self, limit: str) -> int | None: ...
    def as_dict(self) -> dict: ...
```

`None` in `limits` means the limit is **absent**, which is not the same as zero — invariant
5. A limit set to `0` refuses everything; a limit that is `None` does not exist. Rendering
one as the other was a real bug in the prototype.

### `Report`

**provisional.** The shape of `aegoll report` and of `aegoll serve`'s read API. Provisional
because [A10](../PLAN.md) will want fields this draft has not anticipated.

```python
@dataclass(frozen=True)
class Report:
    decisions_total: int
    by_verdict: Mapping[str, int]
    by_attributed_control: Mapping[str, int]
    envelopes: Mapping[str, Envelope]
    chain_valid: bool
    chain_length: int
    def as_dict(self) -> dict: ...
```

`by_attributed_control` is the field that makes a report worth reading: *what actually
governed this agent*, as opposed to what the policy file hoped would.

---

## 4 · Tier 2 — configuration

### `Config` · `PolicyPack`

**stable** for `load`/`validate`, **provisional** for field-level access.

```python
@dataclass(frozen=True)
class Config:
    profile: str                      # "aegs-1" | "aegs-2" | "none"
    policy: "PolicyPack"
    channels: Mapping[str, Mapping[str, int | None]]
    evidence_path: Path | None
    advisor: Mapping[str, Any] | None
    content_hash: str

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config": ...
    def validate(self) -> list["ConfigProblem"]: ...   # empty list == valid

@dataclass(frozen=True)
class PolicyPack:
    id: str
    version: str                      # content hash strongly preferred over a label
    rules: tuple[Mapping[str, Any], ...]
    content_hash: str
    @classmethod
    def load(cls, path: str | Path) -> "PolicyPack": ...
    def explain(self) -> str: ...     # what this policy would do, in plain terms
```

- **`aegoll.yaml` and `aegoll.json` are the same schema in two syntaxes** ([A0.5](../PLAN.md)).
  One loader; the extension picks the parser.
- **`validate()` returns problems, never raises.** `aegoll check` needs every problem at
  once for a CI log, not the first one.
- **`content_hash` covers config and rules together.** A decision must be traceable to the
  exact numbers that produced it, and a config change with an unchanged rule file is still
  a different policy.
- **`rules` are data — `Mapping`, not objects with methods.** Invariant 2 is a security
  boundary: a rule that can execute is remote code execution wearing a governance hat.
  Keeping the public type a plain mapping makes that hard to accidentally undo.

---

## 5 · Tier 3 — extension authors

Small on purpose. Most people never import from this tier.

### `Engine` · `Assessment` · `register_engine`

**provisional.** The contract for a user-supplied engine ([A6.2](../PLAN.md)).

```python
class Engine(Protocol):
    name: str
    def assess(self, request: "Request", state: "State") -> "Assessment": ...

@dataclass(frozen=True)
class Assessment:
    control: str
    verdict: str | None              # None means "no opinion" — NOT "approve"
    score: float | None              # None means not-measured, not zero
    measured: bool                   # False means the control did not run
    reason: str | None

def register_engine(engine: Engine) -> None: ...
```

Three hard rules, enforced at registration and by test, not by documentation:

1. **`assess()` must be pure.** No filesystem, no network, no wall clock. The clock is in
   `state`. A registered engine that performs I/O fails the same purity test a first-party
   one does.
2. **An engine may only narrow.** Returning a verdict wider than the one already reached is
   rejected at registration time where statically detectable, and refused at runtime
   otherwise. Invariant 6.
3. **`verdict=None`, `score=None` and `measured=False` are three different things.**
   Invariant 5, in the type. An engine that could not run says `measured=False`; it never
   says `score=0.0`.

### `Clock` · `FixedClock`

**stable.** Time is injected, never read.

```python
class Clock(Protocol):
    def now_s(self) -> int: ...

class FixedClock(Clock):
    def __init__(self, at_s: int) -> None: ...
    def advance(self, by_s: int) -> None: ...
```

`FixedClock` is public because every test of a windowed limit needs it, and a library that
makes its own time untestable is a library people work around.

### Money conversion

**stable.**

```python
def usd_to_atomic(amount: str | int, decimals: int = 6) -> int: ...
def atomic_to_usd(atomic: int, decimals: int = 6) -> str: ...
```

Returns and accepts `str`, never `float`. Rounding mode is specified in the AEGS spec
([B2.8](../../aegs/PLAN.md)) rather than left to a language default, because two
implementations that round differently disagree on a hash.

---

## 6 · Errors

**stable.** One base class, so `except AegollError` catches everything this library raises.

```python
class AegollError(Exception): ...
class ConfigError(AegollError): ...        # bad or missing aegoll.yaml
class PolicyError(AegollError): ...        # invalid pack; names the offending rule id
class RefusedError(AegollError): ...       # carries .decision
class EvidenceError(AegollError): ...      # chain broken or unwritable
class RegistrationError(AegollError): ...  # a custom engine violated the contract
```

**A refusal is not an exception by default.** `authorize()` returns a `Decision` with
`approved == False`. `RefusedError` exists only for callers who opt into
`raise_on_refusal=True`, because a governance layer whose normal operation is an exception
teaches people to wrap it in `try: ... except: pass`.

---

## 7 · Explicitly not public

The most useful half of an API document. Each of these was reachable in the prototype and is
now **internal** — importable, unsupported, and free to move in a patch release.

| Not public | Why |
|---|---|
| `authorize.py` composition root, all ten engine modules | The verdict is the contract; how it was composed is not |
| `Store`, `AuditLog`, `HistorySnapshot` | Persistence is an implementation detail. `Report` and `decisions()` are the read surface |
| `ReviewQueue`, `ReviewItem` | CLI and localhost-page surface only, until a user asks otherwise with a reason |
| `PolicyBundle`, `load_bundle`, `Aegl`, `Paths` | Prototype names, replaced by `Config` / `PolicyPack` / `Governor` |
| `Tier`, `Purpose`, `Vendor`, `PaymentRequest` | Collapsed into `authorize()`'s keyword arguments. Four types for four strings |
| Advisor classes and the four backends | Configured in `aegoll.yaml`, never constructed by a caller. Keeps invariant 1 hard to break |
| `SpendCheck`, `GovernanceEvent`, `precheck_run`, `record_override` | Superseded by `Decision`, `authorize()` and `settle()` |
| Everything under `adapters/` | Loaded by name from config. Importing an adapter directly couples you to a boundary that is expected to move |
| Streamlit UI, `scenarios`, `evaluation`, `crossview` | Leaving the package entirely ([A2](../PLAN.md)) |

---

## 8 · Compatibility rules

- Adding a keyword argument with a default is a **minor** change. Reordering or removing one is **major**.
- Adding a field to a frozen dataclass is **minor**; every consumer reads by name.
- Adding a `Decision.verdict` value is **major** — callers branch on it exhaustively.
- Renaming an `attributed_control` value is **major**: conformance scoring depends on it, and a renamed control silently changes a conformance result.
- `as_dict()` output is a **wire format**. Its key order and number formatting are part of the API, because the evidence chain hashes them.
- Anything in [§7](#7--explicitly-not-public) may change in a **patch**.

## 9 · Open questions

Recorded rather than guessed. None blocks implementation.

- [ ] Does `wrap()` need an async variant, or does duck-typing cover an async agent? Decide when the first async adapter is written, not before
- [ ] Should `authorize()` accept a caller-supplied idempotency key? The prototype had a replayable request id that overwrote the evidence of a first payment — a key would help, and it needs a spec clause first
- [ ] Is `Report` the same shape as the localhost read API, or a projection of it? [A10.1](../PLAN.md) answers this
- [ ] Does a custom engine get a stable id it can be referenced by in a policy pack, and who owns that namespace?
