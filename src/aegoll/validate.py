"""Validate a policy pack when it is loaded, not when a rule happens to be reached.

The prototype had the right vocabulary and checked it in the wrong place. `COMPARATORS`
was already a fixed tuple with no `eval` anywhere — but the check lived inside
`policy.evaluate()`, so an unknown comparator only raised if a request reached that rule.
**A rule that never matches never validates.** A pack could carry `{"gte_": 5}` or a
verdict of `MAYBE` and sit there looking fine until the one request that touches it
arrives, holding a wallet.

So the rule is: **a pack is rejected at load, in full, or it is not loaded.** Every
problem at once, each naming the rule it came from, because a policy with four mistakes
should be fixed in one pass and because `aegoll check` in CI needs the whole list rather
than the first line of it.

This is the enforcement half of the invariant that policy packs are **data, never code**.
The other half is that there is no expression evaluator to reach in the first place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .domain import Verdict
from .config import COMBINATORS
from .engines.economic.policy import COMPARATORS, build_facts

#: Comparators that need a two-element sequence rather than a scalar.
_PAIR_COMPARATORS = ("between",)

#: Comparators meaningful against a missing fact. Everything else needs a value to
#: compare, and `absent` is not `zero` — invariant 5.
_NULL_SAFE = ("eq", "ne", "in", "not_in")

#: Top-level keys a pack may carry. Anything else is a typo or a feature nobody built.
_PACK_KEYS = {"version", "name", "description", "supersedes", "config", "rules", "derived"}

#: Config blocks a pack may carry, matching the dataclasses in `config.py`.
_CONFIG_KEYS = {"treasury", "treasury_internal", "trust", "risk", "roi", "eiap"}

#: A content hash rather than a label. Labels get reused across edited rules; hashes
#: cannot be. Used by `config.py` to warn when a pack's declared version is a label.
LOOKS_LIKE_HASH = re.compile(r"^[0-9a-f]{8,64}$")


@dataclass(frozen=True)
class Problem:
    """One thing wrong, or worth warning about, and where."""

    severity: str  # "error" | "warning"
    where: str     # rule id, or a dotted config path
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.where}: {self.message}"

    def as_dict(self) -> dict[str, str]:
        return {"severity": self.severity, "where": self.where, "message": self.message}


def known_facts() -> frozenset[str]:
    """Every fact name a rule may match on.

    Derived from `build_facts()` itself rather than duplicated as a list, so a new fact
    becomes matchable the moment an engine produces it — and a list here cannot drift
    from the facts that actually exist. The prototype's purity test failed exactly that
    way once, by naming files instead of walking the tree.
    """
    return frozenset(_FACT_NAMES)


def _derive_fact_names() -> tuple[str, ...]:
    """Read the keys `build_facts` returns, without needing a real request.

    Calls it with stand-ins whose only job is to have the attributes it touches. Cheaper
    and far more honest than maintaining a parallel list.
    """
    import ast
    import inspect

    source = inspect.getsource(build_facts)
    tree = ast.parse(source.strip())
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict) and node.keys:
            names = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            if len(names) == len(node.keys):
                return tuple(names)
    raise RuntimeError("could not read the fact vocabulary from build_facts()")


_FACT_NAMES: tuple[str, ...] = _derive_fact_names()

_VERDICTS = frozenset(v.value for v in Verdict)


def _validate_clause(
    rule_id: str, fact: str, spec: Any, *, extra_facts: frozenset[str] = frozenset()
) -> list[Problem]:
    problems: list[Problem] = []

    if fact not in _FACT_NAMES and fact not in extra_facts:
        known = set(_FACT_NAMES) | set(extra_facts)
        if fact.startswith("derived."):
            # A different cause and a different fix, so a different message. The usual
            # reason is order: declaration order is evaluation order, so a derived fact
            # can only reference one declared above it. That is also why a cycle cannot
            # be written down rather than merely being detected.
            in_scope = sorted(f for f in extra_facts if f.startswith("derived."))
            problems.append(Problem(
                "error", rule_id,
                f"no derived fact {fact.removeprefix('derived.')!r} is in scope here. "
                "A derived fact must be declared *before* it is used — declaration order "
                "is evaluation order. In scope at this point: "
                + (", ".join(in_scope) if in_scope else "nothing"),
            ))
            return problems
        near = sorted(f for f in known if f.split(".")[0] == fact.split(".")[0])
        hint = f" Did you mean one of {near}?" if near else ""
        problems.append(Problem(
            "error", rule_id,
            f"unknown fact {fact!r}. A rule may only match facts the engines produce."
            + hint,
        ))
        return problems

    # A bare value is shorthand for equality: `vendor.sanctioned: true`.
    if not isinstance(spec, dict):
        return problems

    for op, want in spec.items():
        if op not in COMPARATORS:
            problems.append(Problem(
                "error", rule_id,
                f"unknown comparator {op!r} on {fact!r}. Allowed: {', '.join(COMPARATORS)}. "
                "A policy pack is data — there is no expression evaluator to reach.",
            ))
            continue
        if op in _PAIR_COMPARATORS:
            if not isinstance(want, (list, tuple)) or len(want) != 2:
                problems.append(Problem(
                    "error", rule_id,
                    f"{op!r} on {fact!r} needs exactly two values, got {want!r}",
                ))
        if op in ("in", "not_in") and not isinstance(want, (list, tuple)):
            problems.append(Problem(
                "error", rule_id,
                f"{op!r} on {fact!r} needs a list, got {type(want).__name__}",
            ))
        if op not in _NULL_SAFE and want is None:
            problems.append(Problem(
                "error", rule_id,
                f"{op!r} on {fact!r} compares against null. Absent is not zero and not "
                "unknown; use `eq: null` if you mean 'this was never measured'.",
            ))
    return problems


def validate_pack(raw: Any, *, source: str = "<pack>") -> list[Problem]:
    """Every problem in a parsed policy pack. Never raises.

    Returns `[]` for a valid pack. Warnings are included and do not make a pack invalid —
    `has_errors()` is the question a loader asks.
    """
    problems: list[Problem] = []

    if not isinstance(raw, dict):
        return [Problem("error", source, f"a pack must be a mapping, got {type(raw).__name__}")]

    for key in sorted(set(raw) - _PACK_KEYS):
        problems.append(Problem(
            "error", source,
            f"unknown top-level key {key!r}. Allowed: {', '.join(sorted(_PACK_KEYS))}",
        ))

    config = raw.get("config")
    if config is not None:
        if not isinstance(config, dict):
            problems.append(Problem("error", f"{source}:config", "must be a mapping"))
        else:
            for key in sorted(set(config) - _CONFIG_KEYS):
                problems.append(Problem(
                    "error", f"{source}:config",
                    f"unknown block {key!r}. Allowed: {', '.join(sorted(_CONFIG_KEYS))}",
                ))

    version = raw.get("version")
    if version is None:
        problems.append(Problem("error", source, "no `version`"))

    # --- derived facts ----------------------------------------------------
    # Validated before the rules, because rules may reference them and the set of legal
    # fact names depends on what was declared here.
    derived_names: list[str] = []
    declared: dict[str, int] = {}
    derived = raw.get("derived")
    if derived is not None and not isinstance(derived, list):
        problems.append(Problem("error", f"{source}:derived", f"must be a list, got {type(derived).__name__}"))
        derived = None

    for index, entry in enumerate(derived or []):
        where = f"{source}:derived[{index}]"
        if not isinstance(entry, dict):
            problems.append(Problem("error", where, f"must be a mapping, got {type(entry).__name__}"))
            continue

        name = entry.get("name")
        if not isinstance(name, str) or not name:
            problems.append(Problem("error", where, "no `name`. A derived fact is referenced by name, so it needs one"))
            continue
        where = f"{source}:derived.{name}"
        if name in declared:
            problems.append(Problem(
                "error", where,
                f"duplicate name, also at derived[{declared[name]}]. Two definitions for "
                "one fact make its value depend on evaluation order.",
            ))
        if f"derived.{name}" in _FACT_NAMES or name in _FACT_NAMES:
            problems.append(Problem(
                "error", where,
                f"{name!r} shadows a fact the engines already produce. A rule matching it "
                "would read the derived value while its author expected the measured one.",
            ))
        declared[name] = index

        present = [c for c in COMBINATORS if c in entry]
        if len(present) != 1:
            problems.append(Problem(
                "error", where,
                f"needs exactly one of {', '.join(COMBINATORS)}, found "
                f"{', '.join(present) or 'none'}. A derived fact is a single combinator "
                "over a list of clauses — nesting would make it a language.",
            ))
            derived_names.append(f"derived.{name}")
            continue

        for key in sorted(set(entry) - {"name", "description", *COMBINATORS}):
            problems.append(Problem("error", where, f"unknown key {key!r}"))

        clauses = entry[present[0]]
        if not isinstance(clauses, list) or not clauses:
            problems.append(Problem(
                "error", where,
                f"{present[0]!r} must be a non-empty list of clauses",
            ))
        else:
            # Only facts declared BEFORE this one are in scope. That is what makes a
            # cycle impossible to write rather than merely detectable.
            in_scope = frozenset(derived_names)
            for clause in clauses:
                if not isinstance(clause, dict):
                    problems.append(Problem(
                        "error", where,
                        f"each clause is a mapping of fact to condition, got {type(clause).__name__}",
                    ))
                    continue
                for fact, spec in clause.items():
                    problems.extend(_validate_clause(where, fact, spec, extra_facts=in_scope))

        derived_names.append(f"derived.{name}")

    known_extra = frozenset(derived_names)

    rules = raw.get("rules")
    if rules is None:
        problems.append(Problem("error", source, "no `rules`. A pack that permits nothing and refuses nothing is not a policy"))
        return problems
    if not isinstance(rules, list):
        problems.append(Problem("error", f"{source}:rules", f"must be a list, got {type(rules).__name__}"))
        return problems
    if not rules:
        problems.append(Problem(
            "warning", f"{source}:rules",
            "empty. Every request will fall through to whatever the engines decide, with "
            "no policy attribution on any record.",
        ))

    seen: dict[str, int] = {}
    for index, rule in enumerate(rules):
        where = f"{source}:rules[{index}]"
        if not isinstance(rule, dict):
            problems.append(Problem("error", where, f"must be a mapping, got {type(rule).__name__}"))
            continue

        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not rule_id:
            problems.append(Problem("error", where, "no `id`. A rule with no id cannot be attributed, and conformance scores attribution"))
            rule_id = where
        else:
            where = f"{source}:{rule_id}"
            if rule_id in seen:
                problems.append(Problem(
                    "error", where,
                    f"duplicate id, also at rules[{seen[rule_id]}]. Two rules with one id "
                    "make the attributed control ambiguous.",
                ))
            seen[rule_id] = index

        then = rule.get("then")
        if then is None:
            problems.append(Problem("error", where, "no `then`"))
        elif not isinstance(then, str) or then.upper() not in _VERDICTS:
            problems.append(Problem(
                "error", where,
                f"verdict {then!r} is not one of {', '.join(sorted(_VERDICTS))}",
            ))

        priority = rule.get("priority")
        if priority is not None and not isinstance(priority, int):
            problems.append(Problem(
                "error", where,
                f"`priority` must be an integer, got {type(priority).__name__}. "
                "Evaluation order is normative — the attributed control depends on it.",
            ))
        if isinstance(priority, bool):  # bool is an int in Python, and never intended here
            problems.append(Problem("error", where, "`priority` must be an integer, got a boolean"))

        when = rule.get("when")
        if when is None:
            problems.append(Problem(
                "warning", where,
                "no `when`, so this rule matches everything. Intentional only as the "
                "lowest-priority default.",
            ))
        elif not isinstance(when, dict):
            problems.append(Problem("error", where, f"`when` must be a mapping, got {type(when).__name__}"))
        else:
            for fact, spec in when.items():
                problems.extend(_validate_clause(where, fact, spec, extra_facts=known_extra))

    return problems


def has_errors(problems: list[Problem]) -> bool:
    return any(p.severity == "error" for p in problems)


def format_problems(problems: list[Problem]) -> str:
    if not problems:
        return "no problems"
    errors = sum(1 for p in problems if p.severity == "error")
    warnings = len(problems) - errors
    head = f"{errors} error(s), {warnings} warning(s)"
    return head + "\n  " + "\n  ".join(str(p) for p in problems)


__all__ = [
    "Problem",
    "format_problems",
    "has_errors",
    "known_facts",
    "validate_pack",
]
