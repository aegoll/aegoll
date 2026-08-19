"""Verification of the journal against an external anchor. AEGS-0.1-EVID-6a.

A hash chain cannot detect truncation of its own tail, because any prefix of a valid chain is
itself a valid chain. The missing information is *that there was more*, and no amount of internal
linking supplies it. An external anchor supplies it: a `(length, head)` pair published somewhere
the writer of the journal cannot reach.

**What this does not do.** An anchor is published at some cadence, so everything appended since
the last publication is unattested and remains silently truncatable. Anchoring does not make
truncation detectable; it makes truncation detectable *beyond a bound the operator chose*. That is
a real improvement over nothing and it is a different claim, and
`evidence/anchor-truncation-inside-the-window-is-undetected` asserts the difference rather than
describing it.

**No sink ships with this.** Defining `Anchor` and refusing to bundle an implementation is
deliberate: an append-only file beside the journal is `head.json` at one remove in any deployment
where the agent's own user can rewrite it, and a config key pointing at one would make the gap look
closed. See `docs/design/evidence-anchoring.md` for five candidate sinks and what each actually
guarantees.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Protocol, runtime_checkable

from .audit import GENESIS, _hash_entry


class AnchorOutcome(str, Enum):
    """Four, not two. `UNKNOWN` is never a pass -- EVID-6a."""

    CONSISTENT = "consistent"
    TRUNCATED = "truncated"
    DIVERGED = "diverged"
    UNKNOWN = "unknown"


@runtime_checkable
class Anchor(Protocol):
    """A sink that accepts commitments to the journal and can be read back.

    Duck-typed, like `RunGuard` and `PaymentClient`: implementing this requires importing
    nothing from tesoro.

    The two ways of having no answer are distinguished by *how* `latest` declines, because they
    are different facts and only one of them is alarming:

    * **return `None`** -- the sink was read and holds nothing. Nothing has been attested.
    * **raise** -- the sink could not be read. The anchored claim is unavailable.

    Both produce `UNKNOWN`, and neither is ever reported as consistent.
    """

    def publish(self, length: int, head: str) -> str | None:
        """Commit to `(length, head)`. Returns a receipt, or None if it could not."""
        ...

    def latest(self) -> tuple[int | None, str] | None:
        """The highest commitment held, `(length, head)`, or None if the sink is empty.

        A `length` of `None` means the sink holds a head with no length. EVID-6a requires both,
        so that is not a usable anchor and yields `UNKNOWN` rather than a comparison.
        """
        ...


@dataclass(frozen=True)
class AnchorResult:
    outcome: AnchorOutcome
    detail: str
    local_length: int
    attested_length: int | None = None

    @property
    def consistent(self) -> bool:
        return self.outcome is AnchorOutcome.CONSISTENT

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "detail": self.detail,
            "localLength": self.local_length,
            "attestedLength": self.attested_length,
        }


def _fields(entry: Any) -> tuple[int, str, dict[str, Any]]:
    """`(seq, at, payload)` from either an `AuditEntry` or a plain mapping.

    Both shapes are real callers. `AuditLog.entries()` yields `AuditEntry` objects; the AEGS
    vector runner builds plain dicts, because a vector describes entries without hashes and lets
    the implementation compute them.

    Accepting only dicts is what the first version did, and every one of the seven anchor
    vectors passed while `Governor.verify_anchored()` raised `TypeError: 'AuditEntry' object is
    not subscriptable`. A conformance suite exercising a different shape from the public API is
    a suite that can be green over a broken feature -- the same lesson as installing the wheel
    before believing the tests.
    """
    if isinstance(entry, dict):
        return entry["seq"], entry["at"], entry["payload"]
    return entry.seq, entry.at, entry.payload


def _recomputed_head_at(entries: list[Any], length: int) -> str:
    """The head this chain *should* have at `length`, computed from the payloads.

    Recomputed rather than read from the stored `entry_hash`, and that is the whole correctness
    argument. An attacker who edits a payload does not update the stored hashes -- recomputing is
    what makes the edit visible. Comparing an anchor against stored hashes would report an edited
    chain as consistent with the anchor, which is the failure this function exists to avoid.
    """
    prev = GENESIS
    for entry in entries[:length]:
        seq, at, payload = _fields(entry)
        prev = _hash_entry(seq, at, prev, payload)
    return prev


def verify_against_anchor(entries: Iterable[Any], anchor: Anchor) -> AnchorResult:
    """Compare the local journal against what an anchor attests. EVID-6a.

    Never raises on account of the sink. An anchor that cannot be read is a fact about the
    anchor, not an error in the caller's program, and turning it into an exception invites a
    bare `except` that swallows it into a pass.
    """
    local = list(entries)
    n = len(local)

    try:
        latest = anchor.latest()
    except Exception as exc:  # noqa: BLE001 - any failure to read is the same fact
        return AnchorResult(
            AnchorOutcome.UNKNOWN,
            f"the anchor could not be read ({type(exc).__name__}: {exc}); the chain's own "
            "verification still stands, but nothing is attested about its length",
            local_length=n,
        )

    if latest is None:
        return AnchorResult(
            AnchorOutcome.UNKNOWN,
            "the anchor holds nothing, so no length has been attested. Absent is not the same "
            "as agreeing",
            local_length=n,
        )

    attested_length, attested_head = latest

    if attested_length is None:
        return AnchorResult(
            AnchorOutcome.UNKNOWN,
            "the anchor holds a head with no length. EVID-6a requires both: without a length a "
            "mismatch cannot separate removal from alteration, and a match proves only that "
            "this head was published at some point",
            local_length=n,
        )

    if attested_length > n:
        return AnchorResult(
            AnchorOutcome.TRUNCATED,
            f"the anchor attests {attested_length} entries and the journal holds {n}: "
            f"{attested_length - n} were removed",
            local_length=n,
            attested_length=attested_length,
        )

    head_at = _recomputed_head_at(local, attested_length)
    if head_at == attested_head:
        unattested = n - attested_length
        tail = (
            "; the whole journal is attested"
            if unattested == 0
            else (
                f"; the {unattested} entr{'y' if unattested == 1 else 'ies'} appended since are "
                "unattested and remain truncatable"
            )
        )
        return AnchorResult(
            AnchorOutcome.CONSISTENT,
            f"the first {attested_length} entries match what was attested{tail}",
            local_length=n,
            attested_length=attested_length,
        )

    return AnchorResult(
        AnchorOutcome.DIVERGED,
        f"the journal holds {n} entries and the anchor attests {attested_length}, but the head "
        "at that point does not match: an entry was altered, reordered or replaced rather than "
        "removed",
        local_length=n,
        attested_length=attested_length,
    )
