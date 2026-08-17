# Adapters

Two boundaries, deliberately separate.

| | Governs | Direction | Contract |
|---|---|---|---|
| **Framework** adapter | the **internal** channel — tokens the agent burns thinking | the *framework* calls the adapter | [`RunGuard`](../src/aegoll/adapters/base.py) |
| **Rail** adapter | the **external** channel — what the agent pays out | the *agent* calls the adapter | [`PaymentClient`](../src/aegoll/adapters/base.py) |

Merging them would be tempting and wrong. They differ in currency, in counterparty, in failure
mode, and in direction of control. When AP2 or a card rail arrives it will need the second
contract and none of the first, and a merged interface would make that a rewrite rather than an
addition.

**The core imports no framework**, ever — that is invariant 8, checked by
[`test_deps.py`](../tests/test_deps.py) — and `import aegoll` pulls in no adapter at all, checked
by a subprocess in [`test_adapters.py`](../tests/test_adapters.py) because an in-process check
would pass regardless once the test file's own imports had run.

---

## The framework contract: three calls

```python
from aegoll import Governor
from aegoll.adapters.base import RunGuard

guard = RunGuard(Governor.load(), budget_usd="0.40")

allowed, why = guard.start(model="claude-sonnet-4", provider="anthropic")
if not allowed:
    raise SystemExit(why)          # refused before a single token is spent

for step in agent_loop():
    if guard.should_stop(spent_so_far):
        break                      # once per step

guard.finish(actual_cost_usd)      # envelopes consume here
```

That is the whole surface. **A framework adapter is nothing more than the code that arranges for
a particular framework to make these three calls** — which is why an agent on a framework with no
adapter in this package can still be fully governed by using `RunGuard` directly. It imports
nothing, so it needs no extra.

Four properties worth knowing:

**A governor of `None` is valid and means ungoverned.** Every call allows, so an agent written
against the guard behaves exactly as it did before governance existed. That is what keeps this a
*layer*; the alternative is `if governor:` scattered through an agent loop, and the branch that
gets forgotten is always the one that mattered.

**A governor with no `budget_usd` invents no ceiling.** Silence is not zero. A run with no
declared budget is ungoverned on that axis, and refusing work nobody said was too expensive would
be the layer overreaching.

**`should_stop()` asks the layer rather than comparing against `budget_usd`.** So the ceiling is
whatever the policy pack says, *including limits the guard knows nothing about* — a daily envelope
crossed partway through a long run, a per-vendor ceiling, a counterparty whose trust changed. A
guard that only checked its own number would enforce the one limit it was told and silently ignore
every other.

**A refused run neither stops nor settles.** `authorization` is recorded even for a refusal —
the refusal is evidence — so both later calls check that it *approved*. Without that, a refused
run reported itself as stopped, and a refusal and a stop are different facts with different
remedies. (That was a real bug in the first version of this, caught by a test.)

## `aegoll[claude]` — Claude Agent SDK

```python
from aegoll.adapters.claude import ClaudeAgentAdapter

adapter = ClaudeAgentAdapter(Governor.load(), budget_usd="0.40")

allowed, why = adapter.before_run(model="claude-sonnet-4")
if not allowed:
    raise SystemExit(why)

options = adapter.options_for({"model": "claude-sonnet-4"})   # carries max_budget_usd
...
adapter.after_run(actual_cost_usd=result.total_cost_usd)
```

The Agent SDK is the one framework of the three verified in the prototype that **ships a cost
ceiling of its own**: `max_budget_usd` stops a run when estimated LLM cost reaches it. So this
adapter's job is not to add a ceiling the SDK lacks — it is to make two ceilings agree, and to
supply what a single per-run number cannot express:

- a **cumulative** budget, so ten runs of `$0.40` cannot quietly cost `$4.00` against a `$1.00` day;
- a **decision recorded as evidence**, attributable to the control that made it;
- a refusal **before the run starts**, rather than a stop partway through with tokens already spent.

`max_budget_usd` stays on, deliberately: it is enforced inside the SDK, closer to the token
accounting than this layer can reach, and two independent ceilings beat one. `options_for()`
returns **the tighter of the two** and never raises the caller's own limit — a governance layer
that widened a limit somebody set would be doing the one thing no control in this system may do.

`stopped_by_sdk(result)` tells the SDK's ceiling apart from the layer's. Two different facts,
adjustable in two different places; reporting one as the other sends whoever is debugging to the
wrong ceiling.

## `aegoll[adk]` — Google ADK

```python
from aegoll.adapters.adk import GoogleADKAdapter

adapter = GoogleADKAdapter(Governor.load(), budget_usd="0.40")
config = adapter.run_config_for({})                  # keeps max_llm_calls
callback = adapter.before_model_callback(lambda: spent_so_far)
```

ADK caps `max_llm_calls`. **A step ceiling is not a spend ceiling**: one long-context call can
cost more than fifty short ones, so `max_llm_calls=50` bounds nothing about money. Here
`should_stop()` is not a second opinion — nothing else in an ADK run is watching money.

`run_config_for()` leaves `max_llm_calls` alone rather than deriving it from the budget, and only
fills it in when the caller named none. The two bound different things: a step ceiling catches a
runaway loop whose steps are nearly free, which a spend ceiling notices only slowly; a spend
ceiling catches one enormous call, which a step ceiling never notices at all. Deriving one from
the other would silently drop half the coverage.

`before_model_callback(getter)` takes a **callable**, not a number. A number captured at
construction is the spend before the run began — always zero — so the ceiling would never trip
while the callback appeared to work.

## What is verified, and what is not

Stated plainly, because "supports the Claude Agent SDK" is the kind of claim that gets read as
more than it is.

**Verified here:** both adapters, against fakes, with **no SDK installed** — the three calls, the
tightening rules, channel separation, consumption on finish, refusal before start, attribution on
every refusal and stop, and that neither module imports its framework.

**Not verified here:** that the hook *names and signatures* still match the current SDKs. The
shapes come from the [proof-of-concept](https://github.com/Jayzilva/x402), where all three
frameworks ran end to end, and an SDK that moves a hook will break the integration without
breaking these tests. That is what the version pins in `[project.optional-dependencies]` are for,
and it is why [`aegoll-integrations`](https://github.com/aegoll/aegoll-integrations) — which does
install the real SDKs — is where end-to-end runs belong.

A contract exercised only with a real SDK present is a contract nobody checks, so the tests are
where they are on purpose. But a test suite that passes with nothing installed cannot tell you
the SDK still looks the way it did.

## Writing your own

Nothing subclasses and nothing registers. If your framework can call three functions, it can be
governed:

```python
class MyFrameworkAdapter:
    channel = "internal"

    def __init__(self, governor=None, *, budget_usd=None):
        self.guard = RunGuard(governor, budget_usd=budget_usd)

    def before_run(self, *, model=""):
        return self.guard.start(model=model, provider="my-provider")

    def should_stop(self, spent_usd):
        return self.guard.should_stop(spent_usd)

    def after_run(self, *, actual_cost_usd=None, success=True):
        self.guard.finish(actual_cost_usd, success=success)
```

The remaining work in an adapter is always the framework-shaped part: where its hooks live, what
they are called, and what a stop must return to end a run. That is the only thing the two shipped
adapters do differently from each other.

**The dependency arrow points one way.** An agent does not import the governor; the governor wraps
the agent, and a framework calls the guard. If that ever reverses, "plugin" and "dependency"
become indistinguishable — which is why a test fails when it does.
