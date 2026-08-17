"""Run the AEGS test vectors. A8.

The point of these is not that they pass. It is that when one fails, the failure is
attributable: **either the specification is wrong or this implementation is wrong**, and the
vector says which clause is in dispute. A test that only says "expected 1, got 0" leaves that
argument unresolved.

The two vectors that matter most are the ones that exist because of real defects: a
`-$1000` payment that was **approved**, and a 30-digit amount that **crashed** the layer.
Both shipped in the prototype. Neither can come back without a red test here.

Vectors are read from `aegs/vectors/`, located by search rather than by a hard-coded path,
and the whole module skips cleanly when the standard is not checked out beside this
repository — a contributor with only this repo should not see a wall of red for something
they cannot fix.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aegoll.domain import atomic_to_usd, usd_to_atomic

#: Where the standard might be. Two repos side by side is the normal layout; a vendored
#: copy is the fallback once the vectors are pinned like the schemas are.
_SEARCH = (
    Path(__file__).resolve().parents[2] / "aegs" / "vectors",
    Path(__file__).resolve().parents[1] / "src" / "aegoll" / "_vectors",
)


def _vectors_dir() -> Path | None:
    return next((p for p in _SEARCH if p.is_dir()), None)


def _load() -> list[tuple[str, dict]]:
    directory = _vectors_dir()
    if directory is None:
        return []
    out = []
    for path in sorted(directory.rglob("*.json")):
        if path.name == "schema.json":
            continue
        out.append((
            path.relative_to(directory).as_posix().removesuffix(".json"),
            json.loads(path.read_text(encoding="utf-8")),
        ))
    return out


VECTORS = _load()

pytestmark = pytest.mark.skipif(
    not VECTORS,
    reason=(
        "the AEGS vectors are not available. Check out aegoll/aegs beside this repository, "
        "or vendor them. A contributor without the standard should not see red for "
        "something they cannot fix."
    ),
)


def _run(vector: dict):
    """Perform the operation under test, returning either a value or a refusal category."""
    operation = vector["operation"]
    data = vector["input"]

    if operation == "usd_to_atomic":
        try:
            return {"atomic": usd_to_atomic(data["amount"])}
        except (ValueError, TypeError, ArithmeticError) as exc:
            return {"refused": True, "reason": _categorise(str(exc), data["amount"])}

    if operation == "atomic_to_usd":
        decimals = data.get("decimals", 6)
        return {"usd": f"{atomic_to_usd(data['atomic']):.{decimals}f}"}

    raise AssertionError(f"unknown operation {operation!r}")


def _categorise(message: str, amount: object) -> str:
    """Map this implementation's refusal to one of the spec's four categories.

    Deliberately mapped rather than compared. A vector checks that an implementation refused
    *for the right kind of reason*; matching exact strings would make the suite a test of
    this implementation's vocabulary, which is precisely what a conformance suite must not
    be. The mapping lives here, in the implementation, because that is whose wording it is.
    """
    lowered = message.lower()
    if "negative" in lowered:
        return "negative"
    if "too large" in lowered or "exceeds the largest" in lowered:
        return "overflow"
    if "not a finite" in lowered:
        return "nonfinite"
    if "not a usable amount" in lowered:
        # This implementation raises one error for both unparseable text and the
        # non-finite literals its decimal parser accepts as words. Split them here rather
        # than in the spec: the spec's categories are right, and this is our ambiguity.
        text = str(amount).strip().lower().lstrip("+-")
        return "nonfinite" if text in {"nan", "inf", "infinity"} else "unparseable"
    return "unparseable"


@pytest.mark.parametrize("name, vector", VECTORS, ids=[n for n, _ in VECTORS])
def test_vector(name, vector):
    """One vector, one clause, one attributable outcome."""
    got = _run(vector)
    expected = vector["expect"]
    clause = vector["clause"]

    if expected.get("refused"):
        assert got.get("refused"), (
            f"{clause}: {vector['description']}\n"
            f"  the spec requires a refusal ({expected['reason']}); this implementation "
            f"returned {got}.\n"
            f"  Either {clause} is wrong or this implementation is."
        )
        assert got["reason"] == expected["reason"], (
            f"{clause}: refused, but for the wrong kind of reason.\n"
            f"  expected category {expected['reason']!r}, got {got['reason']!r}\n"
            + (f"  note: {vector['note']}" if vector.get("note") else "")
        )
        return

    assert not got.get("refused"), (
        f"{clause}: {vector['description']}\n"
        f"  the spec expects {expected}; this implementation refused: {got}.\n"
        f"  Either {clause} is wrong or this implementation is."
    )
    key = next(iter(expected))
    assert got[key] == expected[key], (
        f"{clause}: {vector['description']}\n"
        f"  expected {key}={expected[key]!r}, got {got[key]!r}\n"
        + (f"  note: {vector['note']}" if vector.get("note") else "")
    )


# --- the suite's own integrity --------------------------------------------


def test_the_vectors_were_actually_found():
    """Guards every test above from passing by iterating nothing.

    A parametrized suite over an empty list is green and worthless, and this project has
    already shipped one guard that passed while checking nothing.
    """
    assert VECTORS, "no vectors loaded"
    assert len(VECTORS) >= 20, f"only {len(VECTORS)} vectors -- is the checkout complete?"


def test_every_vector_names_a_clause():
    """A vector with no clause is a test of somebody's implementation."""
    missing = [name for name, v in VECTORS if not v.get("clause")]
    assert not missing, missing


def test_the_two_known_vulnerabilities_are_covered():
    """Both shipped in the prototype. Neither may come back quietly.

    Named explicitly rather than trusted to the family count, because "we have vectors for
    arithmetic" is not the same claim as "the minus-sign bug is covered".
    """
    ids = {name for name, _ in VECTORS}
    assert any("negative" in i for i in ids), "no vector covers the -$1000 approval"
    assert any("overflow" in i for i in ids), "no vector covers the 30-digit crash"


def test_the_rounding_mode_is_actually_pinned_down():
    """Half-up and half-even differ on exactly one of these. Both vectors are needed.

    A suite containing only `0.0000015` would pass under either mode and prove nothing —
    which is the kind of coverage that reads as thorough and is not.
    """
    assert usd_to_atomic("0.0000005") == 1, "half-even would give 0 here"
    assert usd_to_atomic("0.0000015") == 2
