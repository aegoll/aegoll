"""The four states, as a classifier rather than a convention.

`absent`, `not-run`, `unknown` and `zero` are four different answers, and AEGS-0.1-STATE-1
requires an implementation to tell them apart. This module is where that distinction is made
once, so every layer above it -- profile scoring, reporting, conformance -- reads the same
answer instead of re-deriving it from whatever fields happen to be present.

It was previously derived, and only ever as a boolean: `profiles._is_evidence` answered *did
this control run* and threw away *which of the four ways it did not*. That was enough for
MUST_EXERCISE scoring and not enough for a record a human has to read, where "we never asked"
and "we asked and the answer was nothing" are the difference between a gap and a measurement.

Two labels beyond the four:

* `measured` -- the ordinary case, a control that ran and reported a value. The spec names four
  states because four is how many ways a value can fail to be an ordinary measurement.
* `no-opinion` -- AEGS-0.1-STATE-4: a control that ran and has nothing to say. Deliberately not
  `zero`, and emphatically not `APPROVE`.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "MISSING",
    "Missing",
    "STATES",
    "classify_state",
    "dig",
    "is_evidence",
    "state_of",
]


class Missing:
    """`None` and *absent* are different answers, so they need different values.

    Without this sentinel a key holding an explicit `null` is indistinguishable from a key
    that is not there — and telling those apart is the whole of the four-state rule.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<missing>"


MISSING = Missing()

#: Every label this module can return.
STATES = ("absent", "not-run", "unknown", "zero", "no-opinion", "measured")

#: States in which the control did not run. Everything else is evidence that it did.
DID_NOT_RUN = ("absent", "not-run")

#: Keys a control uses to say *I ran, and I decline to constrain this action*.
_VERDICT_KEYS = ("verdict", "decision")

#: Keys carrying the measurement itself. Null in one of these is `unknown`: the control ran and
#: cannot report a value, which must never be read as zero.
_VALUE_KEYS = ("score", "value", "listed", "level", "result", "amount", "count")


def dig(record: dict[str, Any], path: str) -> Any:
    """Follow a slash-separated record path. Returns `MISSING` when the key is absent.

    An explicit `null` comes back as `None`, which is a *stated* position rather than a
    missing one — and a control field stating nothing is a control that did not run, which is
    why `state_of(None)` is `not-run` rather than `absent`.
    """
    node: Any = record
    for part in path.split("/"):
        if not isinstance(node, dict) or part not in node:
            return MISSING
        node = node[part]
    return node


def classify_state(record: dict[str, Any], field: str) -> str:
    """Which state this record's `field` is in. AEGS-0.1-STATE-1."""
    return state_of(dig(record, field))


def state_of(value: Any) -> str:
    """Which state a value is in.

    The order of these checks is the substance of the clause, so it reads as a sequence
    rather than a set:

    1. **absent** — the key is not there. Nothing was claimed, so nothing may be inferred; in
       particular it is not zero-filled, per AEGS-0.1-STATE-2. A missing AML assessment is not
       a clean one.
    2. **not-run** — a key that carries nothing: `null`, `{}`, `""`, `[]`, or a value whose own
       `measured` flag is false. The control exists and did not answer. Distinct from *absent*
       because something was said, even if what was said was nothing.
    3. **no-opinion** — it ran, and its verdict is explicitly null. AEGS-0.1-STATE-4.
    4. **unknown** — it ran, and its measurement is explicitly null. Distinct from *zero*,
       which is the confusion this whole rule exists to prevent.
    5. **zero** — it ran and reported zero or false. AEGS-0.1-STATE-3: that is a measurement.
    6. **measured** — it ran and reported something else.
    """
    if value is MISSING:
        return "absent"

    if value is None:
        return "not-run"

    if isinstance(value, dict):
        if value.get("measured") is False or value.get("notRun") is True:
            return "not-run"
        if not value:
            return "not-run"
        for key in _VERDICT_KEYS:
            if key in value and value[key] is None:
                return "no-opinion"
        for key in _VALUE_KEYS:
            if key in value:
                return state_of(value[key]) if value[key] is not None else "unknown"
        return "measured"

    if isinstance(value, bool):
        return "zero" if value is False else "measured"

    if isinstance(value, (int, float)):
        return "zero" if value == 0 else "measured"

    if isinstance(value, str):
        if not value.strip():
            return "not-run"
        return "zero" if _parses_as_zero(value) else "measured"

    if isinstance(value, (list, tuple)):
        return "not-run" if not value else "measured"

    return "measured"


def _parses_as_zero(text: str) -> bool:
    """Whether a string is a numeric zero.

    The money path serialises decimals as text, so `"0"` and `"0.000000"` are zeros. A
    classifier that read the encoding rather than the value would call them measurements and
    lose exactly the distinction AEGS-0.1-STATE-3 is about.
    """
    from decimal import Decimal, InvalidOperation

    try:
        return Decimal(text) == 0
    except InvalidOperation:
        return False


def is_evidence(value: Any) -> bool:
    """Whether a record field is evidence that a control actually ran.

    One rule, not two: everything except *absent* and *not-run* is a control that ran. `zero`
    counts, and so does `unknown` — a control that ran and could not produce a value has still
    been exercised, and the missing value is a finding about the value rather than about the
    control.
    """
    return state_of(value) not in DID_NOT_RUN
