"""What the package is allowed to depend on, and what it must never reach for.

The core decides whether a payment may happen. It needs a YAML parser and nothing
else. Every heavier thing is an optional extra, so nobody pays for a dependency they
do not use — and a governance layer that drags a web framework into every install gets
declined by exactly the teams worth having.

These are assertions about the *declared* and *imported* surface, checked without
installing anything. `test_paths.py` covers the filesystem side of the same discipline.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

import pytest
from conftest import imported_names, package_dir

#: Everything the core may import at runtime, beyond the standard library and itself.
RUNTIME_ALLOWED = {"yaml"}

#: Importable only from `advisors/`, and only behind the `advisors` extra. Listing them
#: here rather than as a blanket ban means a new backend has to be added deliberately.
ADVISOR_ONLY = {"anthropic", "openai", "google", "groq"}

#: Optional extras. Importable, but only lazily and only where declared.
EXTRA_ONLY = {"jsonschema", "x402", "x402_core", "eth_account"}

#: Never, anywhere in the package. Each has a reason.
BANNED = {
    # A library must not ship a web framework. This is why 2,534 lines left the
    # package at PLAN.md A2 rather than being kept "just for the demo".
    "streamlit": "a library must not drag a web framework into every install",
    "gradio": "same reason as streamlit",
    "flask": "the localhost page uses the standard library; see PLAN.md A10.2",
    "fastapi": "same",
    "django": "same",
    # The decision path is deterministic. Invariant 1.
    "langchain": "no framework in the governance layer",
    "langgraph": "no framework in the governance layer",
    "crewai": "no framework in the governance layer",
    "claude_agent_sdk": "no framework in the governance layer",
    # Keys come from the environment or an explicit call, never from a hunted file.
    "dotenv": "the caller names the file it is willing to have read",
    # Money never touches a float, so a float-first numeric stack has no place here.
    "numpy": "money is integer atomic units; a float-first stack invites regressions",
    "pandas": "same",
    # Network clients in the core would put I/O in the decision path.
    "requests": "no network in the decision path",
    "httpx": "no network in the core; the rail adapter declares its own",
    "aiohttp": "no network in the decision path",
}


def _project() -> dict:
    """The repository's declared metadata.

    Located relative to **this test file**, not to the package. The first version used
    `package_dir().parents[1] / "pyproject.toml"`, which is fine in the source tree and
    is nothing at all from an installed wheel — `parents[1]` there is `Lib/`. Three tests
    failed against the wheel, which is precisely the mistake this file exists to prevent,
    committed inside the file that prevents it. Recorded rather than quietly corrected.

    Tests always live in the repository, so `__file__` is the right anchor. When the
    suite is run against a wheel from outside its checkout, there is no `pyproject.toml`
    to assert about and these tests skip.
    """
    path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    if not path.is_file():
        pytest.skip(f"no pyproject.toml at {path}; nothing to assert about declarations")
    return tomllib.loads(path.read_text("utf-8"))


def _first_party(name: str) -> bool:
    return name == "aegoll" or name.startswith("aegoll.")


def _stdlib(name: str) -> bool:
    return name.split(".")[0] in sys.stdlib_module_names


def _third_party_imports():
    """(module_path, lineno, imported_root) for every non-stdlib, non-first-party import."""
    for path in sorted(package_dir().rglob("*.py")):
        for lineno, name in imported_names(path):
            if _first_party(name) or _stdlib(name) or name.startswith("."):
                continue
            yield path, lineno, name.split(".")[0]


# --- the declared surface --------------------------------------------------


def test_the_core_declares_only_a_yaml_parser():
    """One runtime dependency. Anything else is an extra."""
    declared = {
        d.split(">=")[0].split("[")[0].split("==")[0].strip()
        for d in _project()["project"]["dependencies"]
    }
    assert declared == {"pyyaml"}, (
        f"the core declares {sorted(declared)}; it should declare pyyaml alone. "
        "Everything heavier belongs in [project.optional-dependencies]."
    )


def test_streamlit_is_not_declared_anywhere():
    """It was an unconditional dependency in the prototype. It is now not even an extra.

    The cockpit lives in aegoll-integrations, where a heavy dependency is fine because
    nothing installs it as a side effect of wanting a spend cap.
    """
    project = _project()["project"]
    everything = list(project["dependencies"]) + [
        d for group in project.get("optional-dependencies", {}).values() for d in group
    ]
    assert not [d for d in everything if "streamlit" in d.lower()], everything


def test_every_extra_has_a_stated_purpose():
    """An extra nobody can explain is an extra nobody should install."""
    extras = _project()["project"].get("optional-dependencies", {})
    assert set(extras) == {"schema", "x402", "advisors", "dev"}, sorted(extras)
    for name, deps in extras.items():
        assert deps, f"extra {name!r} declares nothing"


# --- the imported surface --------------------------------------------------


def test_no_module_imports_a_banned_dependency():
    offenders = [
        f"{path.relative_to(package_dir())}:{lineno} imports {root} — {BANNED[root]}"
        for path, lineno, root in _third_party_imports()
        if root in BANNED
    ]
    assert not offenders, "\n  ".join(offenders)


def test_every_third_party_import_is_accounted_for():
    """A new dependency must be a deliberate act, not an incidental one.

    Fails on anything imported that is neither the one runtime dependency, nor a
    declared extra, nor an advisor backend. Adding a genuinely new dependency means
    adding it here *and* to `pyproject.toml`, which is the point.
    """
    known = RUNTIME_ALLOWED | ADVISOR_ONLY | EXTRA_ONLY
    surprises = {
        f"{path.relative_to(package_dir())}:{lineno} imports {root}"
        for path, lineno, root in _third_party_imports()
        if root not in known
    }
    assert not surprises, (
        "undeclared third-party imports:\n  " + "\n  ".join(sorted(surprises))
    )


def test_advisor_backends_are_imported_only_under_advisors():
    """A model client outside `advisors/` would put a model near the decision path."""
    offenders = [
        f"{path.relative_to(package_dir())}:{lineno} imports {root}"
        for path, lineno, root in _third_party_imports()
        if root in ADVISOR_ONLY and "advisors" not in path.parts
    ]
    assert not offenders, "\n  ".join(offenders)


@pytest.mark.parametrize("root", sorted(EXTRA_ONLY | ADVISOR_ONLY))
def test_optional_dependencies_are_imported_lazily(root):
    """An extra imported at module scope is not optional — it is required, quietly.

    The core must import cleanly with no extras installed at all. So every optional
    dependency has to be imported inside a function, where its absence can be caught
    and explained.
    """
    offenders = []
    for path in sorted(package_dir().rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        top_level = {id(node) for node in tree.body}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            if any(n.split(".")[0] == root for n in names) and id(node) in top_level:
                offenders.append(
                    f"{path.relative_to(package_dir())}:{node.lineno} imports {root} "
                    "at module scope"
                )
    assert not offenders, "\n  ".join(offenders)


# --- the engines are stricter still ----------------------------------------


def test_engines_import_no_third_party_code_at_module_scope():
    """Ten engines, deterministic integer arithmetic, standard library only.

    Not even the YAML parser: config is *loaded* elsewhere and handed in as values. An
    engine that parses a format at import time has stopped being a pure function of its
    inputs, and replay stops meaning anything.

    Module scope is the line, not "at all". Two engines import `jsonschema` **inside a
    validation function** — validating a record against the AEGS schema is a separate
    operation from deciding, it happens after the verdict, and the import degrades to a
    clear message when the extra is absent. Banning that outright would be a rule
    written for its own neatness.
    """
    engines = package_dir() / "engines"
    offenders = []
    for path in sorted(engines.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        top_level = {id(node) for node in tree.body}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if id(node) not in top_level:
                continue  # lazy, inside a function — judged by the test above
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [("." * node.level + (node.module or ""))]
            )
            for name in names:
                if name.startswith(".") or _stdlib(name) or _first_party(name):
                    continue
                offenders.append(
                    f"{path.relative_to(package_dir())}:{node.lineno} imports {name} "
                    "at module scope"
                )
    assert not offenders, (
        "an engine reached outside the standard library at import time:\n  "
        + "\n  ".join(offenders)
    )


def test_engines_do_not_import_adapters_or_advisors():
    """The dependency arrow points one way. Enforced, not intended."""
    engines = package_dir() / "engines"
    offenders = []
    for path in sorted(engines.rglob("*.py")):
        for lineno, name in imported_names(path):
            leaf = name.replace(".", " ").split()
            if "adapters" in leaf or "advisors" in leaf:
                offenders.append(f"{path.relative_to(package_dir())}:{lineno} imports {name}")
    assert not offenders, "\n  ".join(offenders)
