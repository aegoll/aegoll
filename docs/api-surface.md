# Public API surface — `tesoro`

**Status: implemented.** Written before the code on purpose — a bad early API is the only
permanent mistake available in this project, and everything else can be revised.

Writing it first worked, and then nearly failed for a reason worth recording: the code grew a
different shape underneath, and `from tesoro import Governor` returned the internal rules
evaluator instead of the surface below. The README's own opening snippet raised `AttributeError`
on its third line. A designed API is only a contract if something checks that it exists —
`tests/test_governor.py` now does, including a test that runs this page's ten-line snippet
verbatim.

**Version:** draft 1 · 2026-08-17 · Task [A0.6](../PLAN.md) · Applies to `tesoro 0.1.x`

Rule for this document: **if a symbol is not listed here, it is not public**, regardless of
whether Python lets you import it. The prototype exported 21 names from `__init__.py` and
had an 838-line plugin module; most of that was internal machinery that happened to be
reachable. This surface is deliberately about a third of that size.

---

## 1 · The whole thing in ten lines

```python
from tesoro import Governor

gov = Governor.load()                     # reads ./tesoro.yaml
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

    def decide(
        self,
        *,
        amount_usd: str | int,
        vendor: str,
        resource: str,
        channel: str = "external",
        purpose: str | None = None,
        sanctioned: bool = False,
        now: datetime | None = None,
    ) -> "Decision": ...

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

    # --- the kill switch ---
    def freeze(self, reason: str, *, by: str | None = None) -> None: ...
    def unfreeze(self) -> None: ...
    def freeze_state(self) -> "FreezeState": ...
    @property
    def frozen(self) -> bool: ...

    # --- verifying the evidence ---
    def verify(self) -> tuple[bool, list[str]]: ...
    def verify_anchored(self, anchor: "Anchor") -> "AnchorResult": ...

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
- **`decide()` is `authorize()` without the consequences.** Same verdict, same attribution,
  nothing journalled and nothing consumed — for a dry run, a playground, or asking what a
  policy change would have done. It was public and undocumented on this page until a test
  started comparing the two; the omission mattered because a reader could reasonably assume
  `authorize()` was the only way to get a verdict and journal one they did not want.
- **`channel` is a plain string, `"internal"` or `"external"`.** An enum here buys nothing
  a validated string does not, and costs an import in every caller.
- **No `precheck` and no `record_override`.** Both were prototype conveniences that leaked
  the decision path. Overrides are a review-queue operation, not a governor method.
- **`wrap()` returns the same duck type it was given.** It never requires a framework
  import, and the core never imports a framework — invariant 8. An agent does not import
  the governor; the governor wraps the agent.
- **`verify()` and `verify_anchored()` are two calls because they answer two questions.**
  `verify()` walks the chain and catches edits, middle-deletions and reordering. It cannot
  catch truncation of the tail — any prefix of a valid chain is itself valid — so a chain can
  be internally valid *and* shorter than the history that happened.
  `verify_anchored()` compares the journal against an external anchor and is the only one of
  the two that can see that. Merging them would silently change the claim a caller receives
  based on whether an anchor happens to be configured, and `verify()` was here first.
- **`verify_anchored()` returns four outcomes, not a boolean.** `consistent`, `truncated`,
  `diverged`, `unknown` — and **`unknown` is never a pass.** A sink that cannot be read leaves
  the anchored claim unavailable, which is a third thing; reporting it as consistent would mean
  anyone able to partition the process from the sink could also make a truncated journal
  verify. AEGS-0.1-EVID-6a.
- **`anchor` is duck-typed and no implementation ships.** Two methods, `publish` and `latest`,
  and implementing them requires importing nothing from tesoro. Nothing is bundled on purpose:
  an append-only file beside the journal is rewritable by the agent's own user in most
  deployments, so shipping one as a default would make the gap look closed while defending
  nothing. `docs/design/evidence-anchoring.md` lists five candidate sinks and what each
  actually guarantees.
- **`freeze()` requires a reason, and the reason is not decoration.** Whoever finds the agent
  stopped at 2am has to read why. A blank reason raises.
- **A freeze is dispositive.** A refusal during a freeze is attributed to `killswitch` and wins
  attribution over every other control, including a tighter envelope that would also have refused —
  an operator needs to see the freeze, not whichever envelope happened to be tightest. Declared
  alongside `sanctions`, by the same mechanism: recorded unconditionally and last.
- **It persists.** A freeze that evaporates on restart is not a freeze; a crash loop would resume
  spending. State lives beside the journal.
- **An unreadable freeze file reads as frozen.** A corrupt state is an *unknown* state, and
  continuing to spend on an unknown state is the failure the switch exists to prevent.
- **It stops a misbehaving agent; it does not contain an adversarial one.** The state is a file the
  agent's own process can usually write. Not a containment boundary, and it is not described as one.
  This is the same limit that makes an append-only file a poor anchor.
- **It exists because revoking an identity is not a substitute.** Revocation does refuse — and it
  silently does nothing for an agent that never registered an identity: `set_status()` returns
  `False` and the next payment is approved. Measured, and the reason `freeze()` holds no
  precondition.
- **An anchor bounds truncation rather than eliminating it.** Everything appended since the
  last publication is unattested and remains truncatable, so the honest claim is *detectable
  beyond a bound you chose*. The report's chain caveat says so whether or not an anchor is
  configured.

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
    tesoro_version: str
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

**provisional.** The shape of `tesoro report` and of `tesoro serve`'s read API. Provisional
because [A10](../PLAN.md) will want fields this draft has not anticipated.

```python
@dataclass(frozen=True)
class Report:
    policy_name: str
    policy_hash: str
    policy_rules: int                          # the count
    profile: str | None
    rules: tuple[RuleView, ...]                # and the rules themselves, in eval order
    decisions_total: int
    settled: int
    spent_usd: str
    by_verdict: Mapping[str, int]
    by_attributed_control: Mapping[str, int]
    envelopes: Mapping[str, tuple[EnvelopeView, ...]]
    decisions: tuple[DecisionView, ...]
    pending_reviews: int
    chain: ChainView | None
    tesoro_version: str
    aegs_version: str
    def as_dict(self) -> dict: ...
```

`by_attributed_control` is the field that makes a report worth reading: *what actually
governed this agent*, as opposed to what the policy file hoped would.

**One shape, N renderers.** `Report.as_dict()` is the wire format for `--json`, for
`tesoro.html.render()` and for the `tesoro serve` read API in 0.2. Key names and number
formatting are part of the contract for that reason: two renderers disagreeing about a field
name is a bug that only ever shows up in the less-used one.

`EnvelopeView` carries both `binding` and `tightest`, which are two questions and not one —
AEGS-0.1-ENV-6. `binding` answers *why was this refused* and exists only on a refusal;
`tightest` answers *what bites next* and always exists.

`ChainView` carries `hash_name`, `hash_bits` and `caveat`. The first two because
AEGS-0.1-EVID-5 requires the strength to be *declared* — "hash-chained" without a function
and a length describes a shape rather than a guarantee. The third because any renderer that
prints `valid` must print what `valid` does not cover.

### `tesoro.html.render`

**provisional.** `render(report: Report) -> str` — one self-contained HTML document.

Takes a `Report` rather than a layer, so the 0.2 localhost view is a second *transport* over
this renderer rather than a second template that drifts from it. Stdlib only; no network
access of any kind, asserted by test.

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

- **`tesoro.yaml` and `tesoro.json` are the same schema in two syntaxes** ([A0.5](../PLAN.md)).
  One loader; the extension picks the parser.
- **`validate()` returns problems, never raises.** `tesoro check` needs every problem at
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

**stable.** One base class, so `except TesoroError` catches everything this library raises.

```python
class TesoroError(Exception): ...
class ConfigError(TesoroError): ...        # bad or missing tesoro.yaml
class PolicyError(TesoroError): ...        # invalid pack; names the offending rule id
class RefusedError(TesoroError): ...       # carries .decision
class EvidenceError(TesoroError): ...      # chain broken or unwritable
class RegistrationError(TesoroError): ...  # a custom engine violated the contract
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
| Advisor classes and the four backends | Configured in `tesoro.yaml`, never constructed by a caller. Keeps invariant 1 hard to break |
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
- [x] Is `Report` the same shape as the localhost read API, or a projection of it? **The same shape.** `as_dict()` is the wire format for `--json`, the HTML page and the 0.2 read API alike; `render()` takes a `Report` so there is nothing transport-specific to duplicate
- [ ] Does a custom engine get a stable id it can be referenced by in a policy pack, and who owns that namespace?
- [ ] **Should a host be able to enumerate what is installed, and ask a cost before committing?**
  Raised by the cockpit, which needs nine symbols this document does not make public
  ([C4.3](https://github.com/aegoll/tesoro-integrations/blob/main/cockpit/README.md) — *if the
  cockpit needs a private symbol, that is a gap in the public API*). Seven are advisor-internal
  and arguably belong behind the `advisors` extra's own surface rather than the core's. **Two are
  real gaps:**

  * `available_bundles()` and `available_models()` — **enumeration**. Any host that lets a user
    *choose* a policy pack or a model has to list them, and every such host will write the same
    private import until this is public.
  * `estimate_call_cost_usd()` — **cost before commitment**. The entire economic-gate argument
    depends on knowing what a call costs before making it, so a surface that cannot ask is
    missing the input the design turns on.

  Not fixed by exporting them as they stand: `available_bundles()` returns `Path` objects, which
  leaks where packs live on disk into the API. An enumeration surface should return names and
  metadata, not filesystem paths.

## 10 · Where this document has been wrong

Kept because a design document with no error history reads as one nobody checked against reality.

**The whole Tier 1 surface did not exist.** It was written first on purpose — the reasoning at the
top of this page still holds — and then the code grew a different shape underneath, so
`from tesoro import Governor` returned the internal rules evaluator and the ten-line snippet in
§1 raised `AttributeError` on its third line. Nothing compared the document to the package. That
is now `tests/test_governor.py`, which runs §1's snippet verbatim.

**`AEGS_VERSION` was claimed and absent.** [W0.7](../PLAN.md) promised two version lines on the
package; one of them lived in `tesoro.record` and was never exported, so the documented name
raised in the published `aegoll` `0.1.0`. Fixed before `tesoro` `0.1.0`, with a test.
(The version numbers reset at the rename, so the release that had the defect is named by
its old package name -- otherwise this paragraph would libel a release that never had it.)

Both are the same failure: **a designed API is only a contract if something asserts it exists.**
