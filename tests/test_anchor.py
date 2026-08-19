"""Anchored verification, end to end through the public API. AEGS-0.1-EVID-6a, A11.6.

`tests/test_vectors.py` exercises the seven `evidence/anchor-*` vectors against
`verify_against_anchor` directly. This file drives the same logic through `Governor` with a real
journal on disk, and covers the two things a vector cannot: that `verify()` keeps its old meaning,
and that a sink whose `latest()` misbehaves in unexpected ways still cannot produce a pass.

The property under test throughout is the one that would make this feature worse than its
absence: **an anchor that cannot be read must never read as consistent.**
"""

from __future__ import annotations

import tempfile

import pytest

from tesoro.clock import FixedClock
from tesoro.domain import Vendor
from tesoro.engines.evidence.anchor import (
    Anchor,
    AnchorOutcome,
    verify_against_anchor,
)
from tesoro.governor import Governor
from tesoro.runtime import Paths, Tesoro

from datetime import datetime, timezone

BASE = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


class MemoryAnchor:
    """A sink that holds the highest commitment it was given. Not shipped; a test double.

    Implementing this required importing nothing from tesoro, which is the point of the
    protocol being duck-typed.
    """

    def __init__(self) -> None:
        self.commitments: list[tuple[int, str]] = []
        self.readable = True

    def publish(self, length: int, head: str) -> str | None:
        self.commitments.append((length, head))
        return f"receipt-{length}"

    def latest(self):
        if not self.readable:
            raise OSError("sink unreachable")
        return self.commitments[-1] if self.commitments else None


def _gov(tmp_path):
    return Governor(
        Tesoro(paths=Paths.ephemeral(tmp_path), clock=FixedClock(BASE), agent_id="anchored")
    )


def _spend(g, n, amount="0.01"):
    for _ in range(n):
        g.authorize(amount_usd=amount, vendor="v", resource="/r")


def _entries(g):
    return g._layer.audit.entries()


def _publish(g, anchor):
    """Publish what a real sink would: the length, and the head at that length.

    An empty chain's head is `GENESIS`, not the empty string. The first version of this helper
    used `""` and made the empty-journal case read as DIVERGED -- the test was wrong, not the
    implementation, which is worth noting because the failure looked like a bug in the
    comparison.
    """
    from tesoro.engines.evidence.audit import GENESIS

    entries = _entries(g)
    head = entries[-1].entry_hash if entries else GENESIS
    anchor.publish(len(entries), head)
    return len(entries)


# --- the four outcomes, through the public API -------------------------------


def test_a_journal_matching_its_anchor_is_consistent(tmp_path):
    g, anchor = _gov(tmp_path), MemoryAnchor()
    try:
        _spend(g, 3)
        _publish(g, anchor)
        result = g.verify_anchored(anchor)
        assert result.outcome is AnchorOutcome.CONSISTENT
        assert result.consistent is True
        assert "whole journal is attested" in result.detail
    finally:
        g.close()


def test_truncation_is_detected_once_an_anchor_has_attested_the_length(tmp_path):
    """The finding this closes, through the real journal file.

    `verify()` reports success on the truncated chain -- correctly, because a prefix of a valid
    chain is valid. The anchored call is what sees the removal, and asserting both in one test is
    the clearest statement of what an anchor buys.
    """
    g, anchor = _gov(tmp_path), MemoryAnchor()
    try:
        _spend(g, 4)
        _publish(g, anchor)
        path = g._layer.paths.audit
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")

        ok, problems = g.verify()
        assert ok is True, f"the truncated chain should still verify internally: {problems}"

        result = g.verify_anchored(anchor)
        assert result.outcome is AnchorOutcome.TRUNCATED
        assert result.attested_length == 4
        assert result.local_length == 2
        assert "were removed" in result.detail
    finally:
        g.close()


def test_an_edited_entry_reads_as_diverged_not_truncated(tmp_path):
    """Length alone would call this consistent, which is why EVID-6a requires the head too."""
    g, anchor = _gov(tmp_path), MemoryAnchor()
    try:
        _spend(g, 3)
        _publish(g, anchor)
        path = g._layer.paths.audit
        lines = path.read_text(encoding="utf-8").splitlines()
        lines[1] = lines[1].replace('"amountUsd":0.01', '"amountUsd":500.0')
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = g.verify_anchored(anchor)
        assert result.outcome is AnchorOutcome.DIVERGED, result.detail
        assert "altered, reordered or replaced rather than removed" in result.detail
    finally:
        g.close()


def test_an_unreachable_anchor_is_unknown_and_never_consistent(tmp_path):
    """The fail-open case. The one defect that would make this worse than nothing."""
    g, anchor = _gov(tmp_path), MemoryAnchor()
    try:
        _spend(g, 3)
        _publish(g, anchor)
        anchor.readable = False

        result = g.verify_anchored(anchor)
        assert result.outcome is AnchorOutcome.UNKNOWN
        assert result.consistent is False
        assert "could not be read" in result.detail
        # The chain's own verification is unaffected and still stands separately.
        assert g.verify()[0] is True
    finally:
        g.close()


def test_an_empty_anchor_attests_nothing(tmp_path):
    """Distinct from unreachable, and distinct from agreeing."""
    g, anchor = _gov(tmp_path), MemoryAnchor()
    try:
        _spend(g, 3)
        result = g.verify_anchored(anchor)
        assert result.outcome is AnchorOutcome.UNKNOWN
        assert "holds nothing" in result.detail
    finally:
        g.close()


# --- the window, asserted rather than described ------------------------------


def test_truncation_within_the_unattested_tail_is_not_detected(tmp_path):
    """The honest limit, as a test.

    Four entries, an anchor attesting the first two, then the fourth removed. Everything
    attested is present, so the outcome is CONSISTENT and that is correct. An anchor makes
    truncation detectable *beyond its last publication*, and this is what the words "beyond its
    last publication" cost.
    """
    g, anchor = _gov(tmp_path), MemoryAnchor()
    try:
        _spend(g, 2)
        _publish(g, anchor)
        _spend(g, 2)
        path = g._layer.paths.audit
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join(lines[:3]) + "\n", encoding="utf-8")

        result = g.verify_anchored(anchor)
        assert result.outcome is AnchorOutcome.CONSISTENT
        assert "unattested and remain truncatable" in result.detail
    finally:
        g.close()


def test_a_later_publication_narrows_the_window(tmp_path):
    """Republishing is what shrinks the exposure, and it must actually take effect."""
    g, anchor = _gov(tmp_path), MemoryAnchor()
    try:
        _spend(g, 2)
        _publish(g, anchor)
        _spend(g, 2)
        _publish(g, anchor)               # now all four are attested
        path = g._layer.paths.audit
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join(lines[:3]) + "\n", encoding="utf-8")

        result = g.verify_anchored(anchor)
        assert result.outcome is AnchorOutcome.TRUNCATED, result.detail
    finally:
        g.close()


# --- sinks that misbehave ----------------------------------------------------


def test_a_head_without_a_length_is_not_a_usable_anchor(tmp_path):
    class HeadOnly:
        def publish(self, length, head):
            return None

        def latest(self):
            return (None, "some-head")

    g = _gov(tmp_path)
    try:
        _spend(g, 3)
        result = g.verify_anchored(HeadOnly())
        assert result.outcome is AnchorOutcome.UNKNOWN
        assert "no length" in result.detail
    finally:
        g.close()


@pytest.mark.parametrize(
    "boom",
    [ValueError("bad"), KeyError("missing"), RuntimeError("nope"), TimeoutError("slow")],
)
def test_any_failure_to_read_the_sink_is_unknown(boom, tmp_path):
    """Not just the exception a sink is expected to raise.

    A sink is somebody else's code. `verify_against_anchor` catches broadly on purpose, because
    the alternative is an unhandled exception in a verification path -- and the caller who wraps
    *that* in a bare `except` is how a failure becomes a pass.
    """
    class Broken:
        def publish(self, length, head):
            return None

        def latest(self):
            raise boom

    g = _gov(tmp_path)
    try:
        _spend(g, 2)
        result = g.verify_anchored(Broken())
        assert result.outcome is AnchorOutcome.UNKNOWN
        assert type(boom).__name__ in result.detail
    finally:
        g.close()


def test_an_empty_journal_with_an_empty_anchor_is_not_a_false_alarm(tmp_path):
    g, anchor = _gov(tmp_path), MemoryAnchor()
    try:
        _publish(g, anchor)
        result = g.verify_anchored(anchor)
        assert result.outcome is AnchorOutcome.CONSISTENT, result.detail
        assert result.local_length == 0
    finally:
        g.close()


# --- the protocol and what does not ship -------------------------------------


def test_the_anchor_protocol_needs_no_tesoro_import_to_satisfy():
    """Duck-typed, like RunGuard and PaymentClient."""
    assert isinstance(MemoryAnchor(), Anchor)


def test_no_sink_ships_as_a_default():
    """A11.7, generalised: an anchor within the writer's authority is not an anchor.

    An append-only file beside the journal is rewritable by the agent's own user in most
    deployments, so shipping one as a default -- with a config key pointing at it -- would make
    the gap look closed while defending nothing. If a sink is ever added here, this test should
    fail and be replaced by one asserting what that sink's guarantee actually is.
    """
    import tesoro.engines.evidence.anchor as mod

    concrete = [
        name for name, obj in vars(mod).items()
        if isinstance(obj, type)
        # Defined here, not imported here. The first version of this counted `Enum`,
        # `Any` and `Protocol` as concrete sinks.
        and obj.__module__ == mod.__name__
        and not name.startswith("_")
        and name not in {"Anchor", "AnchorOutcome", "AnchorResult"}
    ]
    assert concrete == [], f"a concrete sink appeared in the package: {concrete}"


def test_verify_and_verify_anchored_answer_different_questions(tmp_path):
    """`verify()` must not change meaning because an anchor now exists."""
    g, anchor = _gov(tmp_path), MemoryAnchor()
    try:
        _spend(g, 3)
        _publish(g, anchor)
        path = g._layer.paths.audit
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join(lines[:1]) + "\n", encoding="utf-8")

        assert g.verify()[0] is True
        assert g.verify_anchored(anchor).outcome is AnchorOutcome.TRUNCATED
    finally:
        g.close()


def test_the_caveat_still_discloses_the_gap_and_now_names_the_window():
    """EVID-6 does not relax because EVID-6a exists."""
    from tesoro.reporting import ChainView

    caveat = ChainView.caveat
    assert "truncation" in caveat
    assert "external anchor" in caveat
    assert "verify_anchored" in caveat
    assert "not detectable" in caveat, (
        "the caveat must say an anchor bounds truncation rather than eliminating it"
    )
    assert "tamper-proof" not in caveat
