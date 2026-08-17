"""No module may resolve a path outside the package, or mutate `sys.path`.

This is the test whose absence let eleven separate out-of-package reaches survive in
the prototype — see PLAN.md F-A1. Every one of them resolved fine inside the monorepo
and would have broken in an installed wheel, and 249 tests passing said nothing about
it, because the tests ran against the source tree where the siblings existed.

The rule: a package locates its own data through `importlib.resources`, and locates
nothing else. Anything the caller wants read comes in as an argument.
"""

from __future__ import annotations

import ast
import os

import pytest
from conftest import imported_names, package_dir

#: Modules whose out-of-package reaches are known and already scheduled for removal.
#: The Streamlit cockpit leaves the package entirely at PLAN.md A2.1 — it is not a
#: library surface at all. Each entry must name a file that still exists, so the
#: exemption cannot outlive the module: delete the file and this list must shrink or
#: `test_the_exemptions_have_not_become_permanent` fails.
_LEAVING_AT_A2 = {"app.py", "ui_demo.py"}


def _modules():
    return sorted(p for p in package_dir().rglob("*.py"))


def _rel(path) -> str:
    return str(path.relative_to(package_dir())).replace("\\", "/")


def _upward_walks(path):
    """Every expression in this file that walks up from `__file__`, as (lineno, source).

    An AST walk rather than a text scan, so a docstring *describing* the banned pattern
    is not mistaken for the pattern itself. That false positive showed up immediately.
    """
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Attribute) and node.attr in {"parents", "parent"}):
            continue
        try:
            src = ast.unparse(node)
        except Exception:  # pragma: no cover - unparse is total in practice
            continue
        if "__file__" in src:
            out.append((node.lineno, src))
    return out


def test_no_module_walks_up_out_of_the_package():
    """`Path(__file__).parents[n]` is how the package escaped itself eleven times."""
    offenders = [
        f"{_rel(path)}:{lineno}: {src}"
        for path in _modules()
        if path.name not in _LEAVING_AT_A2
        for lineno, src in _upward_walks(path)
    ]
    assert not offenders, (
        "these resolve a path outside the package, which breaks in an installed "
        "wheel. Use importlib.resources for package data, and take anything else "
        "as an argument:\n  " + "\n  ".join(offenders)
    )


def test_the_exemptions_have_not_become_permanent():
    """An exemption for a module that no longer exists is rot. Delete the entry too."""
    names = {p.name for p in _modules()}
    stale = sorted(_LEAVING_AT_A2 - names)
    assert not stale, (
        "these modules are gone, so their exemptions must go with them — remove them "
        f"from _LEAVING_AT_A2: {stale}"
    )


def test_no_module_mutates_sys_path():
    """A library that edits `sys.path` is guessing where its dependencies live."""
    offenders = []
    for path in _modules():
        if path.name in _LEAVING_AT_A2:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # sys.path.insert(...) / sys.path.append(...)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "path"
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id == "sys"
            ):
                offenders.append(f"{_rel(path)}:{node.lineno}: sys.path.{node.func.attr}()")

    assert not offenders, (
        "a package must not rewrite the import path to find its own dependencies; "
        "declare them and fail with a clear message when absent:\n  "
        + "\n  ".join(offenders)
    )


def test_no_module_imports_dotenv_style_loaders():
    """Keys arrive from the environment or an explicit call. Never from a file we hunted for.

    The prototype walked up two directories looking for `.env` and exported whatever it
    found into `os.environ`, at import time. In a library that also handles BYOK keys
    that is a security problem, so the shape is banned rather than merely removed.
    """
    banned = {"dotenv", "python_dotenv"}
    offenders = [
        f"{_rel(path)}:{lineno} imports {name}"
        for path in _modules()
        for lineno, name in imported_names(path)
        if name.split(".")[0] in banned
    ]
    assert not offenders, "\n  ".join(offenders)


def test_packaged_policies_are_reachable_through_resources():
    """The starter policies must be package data, not a sibling directory."""
    from aegl.config import DEFAULT_BUNDLE, available_bundles

    assert DEFAULT_BUNDLE.is_file(), f"{DEFAULT_BUNDLE} is not there"
    assert DEFAULT_BUNDLE.is_relative_to(package_dir()), (
        f"{DEFAULT_BUNDLE} sits outside the package"
    )
    names = {p.name for p in available_bundles()}
    assert {"default.yaml", "strict.yaml"} <= names, names


def test_vendored_schemas_are_reachable_and_declared():
    """Each vendored schema resolves inside the package and has stated provenance."""
    from aegl import record
    from aegl.engines.economic import intent
    from aegl.engines.evidence import identity

    for module in (record, intent, identity):
        path = module.SCHEMA_PATH
        assert path.is_file(), f"{module.__name__}: {path} is not there"
        assert path.is_relative_to(package_dir()), (
            f"{module.__name__}: {path} sits outside the package"
        )

    provenance = package_dir() / "_schemas" / "PROVENANCE.txt"
    assert provenance.is_file(), "vendored data with no stated source is unmaintainable"
    text = provenance.read_text(encoding="utf-8")
    assert "commit:" in text and "aegoll/aegs" in text


@pytest.mark.parametrize("module_name", ["aegl", "aegl.config", "aegl.advisors"])
def test_importing_does_not_touch_the_environment(module_name, tmp_path):
    """Importing the package must not export anything into `os.environ`.

    `_load_repo_env()` used to run at import time in two places, so `import aegl` had
    the side effect of reading a `.env` two directories up and exporting its contents.

    Run in a **subprocess**, with a `.env` planted in its working directory as bait. The
    first version of this test purged `sys.modules` and re-imported in-process, which
    handed five unrelated tests fresh copies of classes they already held references to
    — a reminder that a test which mutates the interpreter is a test that breaks its
    neighbours.
    """
    import json
    import subprocess
    import sys

    (tmp_path / ".env").write_text(
        "AEGOLL_CANARY_KEY=should-never-be-exported\n", encoding="utf-8"
    )

    program = (
        "import json, os, sys;"
        "before = dict(os.environ);"
        f"__import__({module_name!r});"
        "print(json.dumps({"
        "'added': sorted(k for k in os.environ if k not in before),"
        "'changed': sorted(k for k in before if os.environ.get(k) != before[k])"
        "}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(package_dir().parent)},
    )
    assert result.returncode == 0, result.stderr
    delta = json.loads(result.stdout.strip().splitlines()[-1])

    assert not delta["added"], (
        f"importing {module_name} exported {delta['added']} — a library must not read "
        "files the caller never offered it, least of all one holding API keys"
    )
    assert not delta["changed"], f"importing {module_name} changed {delta['changed']}"
