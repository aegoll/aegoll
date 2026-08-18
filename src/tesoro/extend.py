"""User-supplied engines: how to add a control without forking, and without weakening.

Anything a policy pack cannot express is a **missing engine**, and the answer is a new
engine rather than an escape hatch in the rule language. That is why this module exists,
and it is also why `validate.py` can keep refusing anything that looks like an expression:
if packs stay data, the extension point has to be somewhere, and here is somewhere with a
gate on it.

Three properties are non-negotiable, and each is enforced rather than documented:

1. **An engine may only narrow.** Not checked — made impossible. An engine returns an
   opinion; the host applies `narrower()`. A registered engine returning `APPROVE` against
   a standing `REJECT` has no effect at all, because the composition root never widens.
   See `test_extend.py` for the effect asserted directly.
2. **An engine must be pure.** No filesystem, no network, no clock, no model client, no
   randomness. Refused **at registration**, by reading the callable's own source — because
   a runtime failure in a governance layer arrives at the worst possible moment, and an
   impure engine breaks replay, which is what makes a decision auditable at all.
3. **Absent ≠ not-run ≠ unknown ≠ zero.** An engine that could not run says
   `measured=False`. It never says `score=0.0`, and it never says `verdict="APPROVE"` to
   mean "no objection" — that is what `verdict=None` is for.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .domain import Verdict
from .errors import RegistrationError

#: Control names the standard already defines. A custom engine may not claim one: an
#: attributed control that means two different things depending on who is running makes
#: every conformance report ambiguous.
RESERVED_CONTROLS = frozenset({
    "treasury", "policy", "roi", "intent", "trust", "risk",
    "audit", "eiap", "identity", "escalation", "authorize", "sanctions",
    "AgentIdentity", "EconomicIntent", "Policy", "BudgetEnvelope",
    "RiskAssessment", "TrustAssessment", "GovernanceDecision", "EvidenceRecord",
    "Authorization", "ConformanceDeclaration", "AMLAssessment",
    "ComplianceAssessment", "IncidentRecord",
})

#: Modules an engine may not import, with the reason. The decision path is deterministic
#: integer arithmetic over values it was handed; anything here breaks either that or the
#: replayability that makes a journal worth keeping.
FORBIDDEN_IMPORTS = {
    "os": "an engine does not touch the environment or the filesystem",
    "sys": "an engine does not inspect or modify the interpreter",
    "io": "no I/O in the decision path",
    "pathlib": "no filesystem in the decision path",
    "socket": "no network in the decision path",
    "http": "no network in the decision path",
    "urllib": "no network in the decision path",
    "requests": "no network in the decision path",
    "httpx": "no network in the decision path",
    "aiohttp": "no network in the decision path",
    "subprocess": "an engine does not run programs",
    "random": "a non-deterministic engine cannot be replayed, so its decisions cannot be audited",
    "secrets": "same as random",
    "time": "the clock is injected; an engine that reads a wall clock is not replayable",
    "datetime": "the clock is injected — take `now` from the context",
    "anthropic": "no model in the decision path",
    "openai": "no model in the decision path",
    "groq": "no model in the decision path",
    "google": "no model in the decision path",
    "sqlite3": "an engine reads the state it is handed, not a database",
}

#: Calls an engine may not make, even without an import.
FORBIDDEN_CALLS = {
    "open": "no filesystem in the decision path",
    "eval": "an engine is code, but it does not become an interpreter",
    "exec": "same as eval",
    "compile": "same as eval",
    "__import__": "a dynamic import defeats the whole of this check",
    "input": "an engine does not block on a human",
}


@dataclass(frozen=True)
class Assessment:
    """One engine's opinion about one request.

    `verdict=None` means **no opinion**, which is not the same as approval. An engine with
    nothing to say says nothing; it does not vote APPROVE. Confusing the two would let a
    silent engine look like an endorsing one.
    """

    control: str
    verdict: str | None = None
    score: float | None = None
    measured: bool = True
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.verdict is not None and self.verdict not in {v.value for v in Verdict}:
            raise ValueError(
                f"{self.control}: verdict {self.verdict!r} is not one of "
                f"{sorted(v.value for v in Verdict)}. Use None for 'no opinion'."
            )
        if not self.measured and self.score is not None:
            raise ValueError(
                f"{self.control}: measured=False with a score of {self.score!r}. A control "
                "that did not run has no score — absent, not-run, unknown and zero are "
                "four different things."
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "control": self.control,
            "verdict": self.verdict,
            "score": self.score,
            "measured": self.measured,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Context:
    """Everything an engine may read. Read-only, and everything it needs is here.

    Deliberately values rather than handles: no store, no journal, no config loader. An
    engine that wanted one of those would be reaching for I/O, which is the thing the
    purity gate refuses.
    """

    request: Any
    snapshot: Any
    now: Any
    trust: Any = None
    risk: Any = None
    roi: Any = None
    budget: Any = None
    facts: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Engine(Protocol):
    """What a custom engine must be. One attribute, one method."""

    name: str

    def assess(self, context: Context) -> Assessment: ...


_REGISTRY: dict[str, Engine] = {}


# --- the purity gate -----------------------------------------------------


def _source_of(engine: Engine) -> str | None:
    """The `assess` implementation's source, or None when it cannot be read.

    A C extension, a REPL definition or a stripped install has no readable source. That is
    reported rather than waved through: see `register_engine`.
    """
    try:
        return textwrap.dedent(inspect.getsource(type(engine).assess))
    except (OSError, TypeError):
        return None


def purity_problems(source: str) -> list[str]:
    """Every reason this source is not a pure function of its inputs."""
    problems: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:  # pragma: no cover - inspect gave us valid source
        return [f"could not be parsed: {exc}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in FORBIDDEN_IMPORTS:
                    problems.append(f"imports {alias.name} — {FORBIDDEN_IMPORTS[root]}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in FORBIDDEN_IMPORTS:
                problems.append(f"imports {node.module} — {FORBIDDEN_IMPORTS[root]}")
        elif isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name) and target.id in FORBIDDEN_CALLS:
                problems.append(f"calls {target.id}() — {FORBIDDEN_CALLS[target.id]}")
            elif isinstance(target, ast.Attribute):
                # `os.getenv(...)`, `time.time(...)`, `random.random(...)`
                base = target.value
                if isinstance(base, ast.Name) and base.id in FORBIDDEN_IMPORTS:
                    problems.append(
                        f"calls {base.id}.{target.attr}() — {FORBIDDEN_IMPORTS[base.id]}"
                    )
        elif isinstance(node, ast.Global):
            problems.append(
                f"declares `global {', '.join(node.names)}` — an engine holding mutable "
                "module state is not replayable, because the same inputs stop producing "
                "the same decision"
            )

    return problems


# --- registration --------------------------------------------------------


def register_engine(engine: Engine, *, allow_unreadable_source: bool = False) -> None:
    """Register a custom engine, or refuse it with a reason.

    Refusal happens **here**, not on the first decision. A governance layer that fails at
    runtime fails while an agent is mid-run and holding a wallet; a registration that fails
    at import fails while a developer is looking at it.
    """
    name = getattr(engine, "name", None)
    if not isinstance(name, str) or not name.strip():
        raise RegistrationError(
            f"{type(engine).__name__} has no usable `name`. The name becomes the "
            "attributed control on every decision this engine touches, and conformance "
            "scores attribution — an unnamed control cannot be attributed."
        )
    name = name.strip()

    if name in RESERVED_CONTROLS:
        raise RegistrationError(
            f"{name!r} is a control the standard already defines. An attributed control "
            "that means two different things depending on whose engine is loaded makes "
            "every conformance report ambiguous. Pick a name of your own."
        )
    if name in _REGISTRY:
        raise RegistrationError(
            f"{name!r} is already registered by {type(_REGISTRY[name]).__name__}. Two "
            "engines under one control name make the attribution ambiguous."
        )

    assess = getattr(engine, "assess", None)
    if not callable(assess):
        raise RegistrationError(f"{name!r} has no callable `assess`")

    signature = inspect.signature(assess)
    if len(signature.parameters) != 1:
        raise RegistrationError(
            f"{name!r}: assess() takes {len(signature.parameters)} argument(s); it takes "
            "exactly one, a Context. Everything an engine may read is on it."
        )

    source = _source_of(engine)
    if source is None:
        if not allow_unreadable_source:
            raise RegistrationError(
                f"{name!r}: assess() has no readable source, so it cannot be checked for "
                "purity. Pass allow_unreadable_source=True to accept it anyway — and "
                "understand that an impure engine breaks replay, which is what makes a "
                "decision auditable."
            )
    else:
        problems = purity_problems(source)
        if problems:
            raise RegistrationError(
                f"{name!r} is not a pure function of its inputs:\n  "
                + "\n  ".join(problems)
                + "\n\nThe decision path is deterministic arithmetic over values it was "
                "handed. Take what you need from the Context; if it is not there, that is "
                "a gap in the Context worth reporting."
            )

    _REGISTRY[name] = engine


def registered_engines() -> tuple[Engine, ...]:
    """In registration order, which is the order they are consulted.

    Order does not change the final verdict — narrowing is commutative — but it does
    change which control gets *attributed* when two would narrow to the same thing, and
    conformance scores attribution.
    """
    return tuple(_REGISTRY.values())


def unregister_engine(name: str) -> bool:
    return _REGISTRY.pop(name, None) is not None


def clear_engines() -> None:
    """Empty the registry. For tests, and for a host rebuilding its configuration."""
    _REGISTRY.clear()


__all__ = [
    "FORBIDDEN_CALLS",
    "FORBIDDEN_IMPORTS",
    "RESERVED_CONTROLS",
    "Assessment",
    "Context",
    "Engine",
    "clear_engines",
    "purity_problems",
    "register_engine",
    "registered_engines",
    "unregister_engine",
]
