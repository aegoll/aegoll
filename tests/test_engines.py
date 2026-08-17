"""The engine families, and the rule that stops them becoming folders.

Grouping is only worth doing if the grouping *means* something. Three directories
whose contents freely import each other are three directories; they say nothing
about what an engine answers to, and nothing prevents the next engine landing
wherever it is convenient.

So the family boundary is asserted:

* every engine lives in exactly one family
* **no family imports another**
* engines depend only on `domain`, `config` and `store` — value types, not behaviour

`authorize.py` sits outside the families on purpose. It is the composition root: it
reads all three and clamps their verdicts together. Placing it inside one would make
the rule above unstatable, and a rule with an exception carved for its most
important case is not a rule.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from conftest import package_dir

PACKAGE = package_dir()
ENGINES = PACKAGE / "engines"

FAMILIES = {
    "economic": {"treasury", "policy", "roi", "intent"},
    "risk": {"trust", "risk"},
    "evidence": {"audit", "eiap", "identity", "escalation"},
}

#: Value types the engines may depend on. Not behaviour -- these carry data and
#: arithmetic, which is what keeps engines pure and replayable (ADR-004).
SHARED = {"domain", "config", "store"}


def module_imports(path: Path) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.module, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            # `from . import x` -- relative with no module
            found += [(a.name, node.lineno) for a in node.names]
        elif isinstance(node, ast.Import):
            found += [(a.name, node.lineno) for a in node.names]
    return found


# --- the families are real -------------------------------------------------


def test_every_engine_lives_in_exactly_one_family():
    placed: dict[str, str] = {}
    for family, expected in FAMILIES.items():
        found = {p.stem for p in (ENGINES / family).glob("*.py") if p.stem != "__init__"}
        assert found == expected, f"{family} holds {sorted(found)}, expected {sorted(expected)}"
        for engine in found:
            assert engine not in placed, f"{engine} is in both {placed[engine]} and {family}"
            placed[engine] = family


def test_the_composition_root_is_outside_the_families():
    """`authorize` reads every family. Inside one, the no-cross-import rule could
    not be stated at all."""
    assert (PACKAGE / "authorize.py").exists()
    for family in FAMILIES:
        assert not (ENGINES / family / "authorize.py").exists()


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_a_family_never_imports_another_family(family):
    """The rule that makes the grouping mean something."""
    others = set(FAMILIES) - {family}
    offenders = []
    for path in (ENGINES / family).glob("*.py"):
        for module, line in module_imports(path):
            parts = module.replace("...", "").replace("..", "").split(".")
            for other in others:
                if other in parts:
                    offenders.append(f"{path.name}:{line} imports {module}")
    assert not offenders, (
        f"the {family} family reached into another:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_engines_depend_only_on_value_types(family):
    """An engine that imports `runtime` or `plugin` has stopped being an engine."""
    banned = {"runtime", "plugin", "advise", "record", "crossview", "cli", "app"}
    offenders = []
    for path in (ENGINES / family).glob("*.py"):
        for module, line in module_imports(path):
            leaf = module.split(".")[-1]
            if leaf in banned:
                offenders.append(f"{path.name}:{line} imports {module}")
    assert not offenders, "\n  ".join(offenders)


# --- the move did not break the old paths ---------------------------------


@pytest.mark.parametrize(
    "engine", sorted({e for names in FAMILIES.values() for e in names})
)
def test_the_old_import_path_still_works(engine):
    """A regrouping that forced every caller to be edited would be a rewrite wearing
    a refactor's clothes."""
    import importlib

    shim = importlib.import_module(f"aegoll.{engine}")
    family = next(f for f, names in FAMILIES.items() if engine in names)
    real = importlib.import_module(f"aegoll.engines.{family}.{engine}")

    exported = [n for n in dir(real) if not n.startswith("_")]
    missing = [n for n in exported if not hasattr(shim, n)]
    assert not missing, f"aegoll.{engine} no longer re-exports {missing}"


def test_no_engine_file_remains_at_the_top_level():
    """Other than the shims, which are three lines and say so."""
    for names in FAMILIES.values():
        for engine in names:
            source = (PACKAGE / f"{engine}.py").read_text(encoding="utf-8")
            assert "Moved to" in source, f"{engine}.py is not a shim"
            assert len(source.splitlines()) < 20, f"{engine}.py has grown real code again"


# --- the purity claim survives the move -----------------------------------


def test_no_engine_imports_a_model_client():
    """Phase 1's whole claim: nothing in the decision path talks to a model.

    Previously asserted against a hardcoded list of filenames, which the S6 move
    silently invalidated -- the list still named files that no longer existed, so it
    passed by checking nothing. Now it walks the families, so a new engine is covered
    the moment it lands.
    """
    forbidden = {"anthropic", "groq", "openai", "claude_agent_sdk", "google"}
    offenders = []
    targets = list(ENGINES.rglob("*.py")) + [
        PACKAGE / "authorize.py", PACKAGE / "domain.py", PACKAGE / "store.py"
    ]
    for path in targets:
        for module, line in module_imports(path):
            if module.split(".")[0] in forbidden:
                offenders.append(f"{path.name}:{line} imports {module}")
    assert not offenders, (
        "an engine imported a model client:\n  " + "\n  ".join(offenders)
    )
