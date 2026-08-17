"""Shared fixtures, and one helper that exists because of a real bug.

`package_dir()` resolves the package from the *imported module*, never from a path
relative to this file. Four tests used to do

    Path(__file__).resolve().parents[1] / "aegoll"

which silently assumed the package sat next to `tests/`. Moving to a `src/` layout
broke all four at once — and the failure mode that matters is the opposite one: had
those tests been written to look *up* a directory and guess, a future layout change
could leave them pointing at nothing and passing vacuously, exactly as the purity
test once did when a refactor turned its targets into three-line shims.

Resolving through the import means the tests check the package that is actually
installed and importable. If it cannot be found, they fail loudly. See PLAN.md F-A1.
"""

from __future__ import annotations

import ast
from pathlib import Path

import aegoll


def package_dir() -> Path:
    """Where the importable `aegoll` package actually lives."""
    assert aegoll.__file__ is not None, "aegoll is a namespace package, not a real one"
    return Path(aegoll.__file__).resolve().parent


def module_source(*parts: str) -> Path:
    """Path to a module inside the package, e.g. `module_source("plugin.py")`."""
    p = package_dir().joinpath(*parts)
    assert p.is_file(), f"expected {p} to exist; the layout changed under the tests"
    return p


def imported_names(source: Path) -> list[tuple[int, str]]:
    """Every name this module imports, as (lineno, dotted name).

    Walks the AST rather than importing, so a banned dependency is detected even
    when it is not installed.
    """
    out: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append((node.lineno, node.module))
    return out
