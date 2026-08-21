"""The kill switch. P1.2, concept #25 of the agent-governance brief.

Written after measuring that the obvious substitute does not work: `identities.set_status(agent,
"revoked")` refuses an agent that has a registered identity, and **silently does nothing for one
that does not** — it returns `False` and the next payment is APPROVED. A kill switch whose effect
depends on an unrelated registration having happened is not a kill switch.

Four properties matter more than the feature, and each has its own test: a freeze wins attribution
over any other control, it survives a restart, an unreadable state file reads as *frozen*, and a
freeze without a reason is refused.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone

import pytest

from tesoro.clock import FixedClock
from tesoro.domain import Vendor, Verdict
from tesoro.freeze import FreezeStore
from tesoro.governor import Governor
from tesoro.runtime import Paths, Tesoro

BASE = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def _gov(root):
    """A governor with paths on disk, because persistence is one of the properties."""
    return Governor(Tesoro(paths=Paths.under(root), clock=FixedClock(BASE), agent_id="frozen-one"))


def _spend(g, amount="0.001"):
    return g.decide(amount_usd=amount, vendor="v", resource="/r")


# --- the reason this exists ---------------------------------------------------


def test_revoking_an_unregistered_identity_does_nothing(tmp_path):
    """The measurement that motivated a separate kill switch.

    Revocation is the obvious substitute and it has a precondition nobody would think to check.
    If this test ever fails because revocation started working without a registration, the
    docstrings claiming otherwise need correcting -- which is why it is asserted rather than
    described.
    """
    layer = Tesoro(paths=Paths.under(tmp_path), clock=FixedClock(BASE), agent_id="a")
    try:
        assert layer.identities.set_status("a", "revoked") is False
        d = layer.decide(
            layer.build_request(resource="/r", amount_usd="0.001", vendor=Vendor(id="v"))
        )
        assert d.verdict is Verdict.APPROVE, (
            "revoking an unregistered identity now refuses; the kill switch docs say it does not"
        )
    finally:
        layer.close()


def test_freeze_needs_no_identity_to_be_registered(tmp_path):
    """The whole point: no precondition."""
    g = _gov(tmp_path)
    try:
        assert _spend(g).verdict is Verdict.APPROVE
        g.freeze("no identity registered, and it must still stop")
        assert _spend(g).verdict is Verdict.REJECT
    finally:
        g.close()


# --- the four properties -----------------------------------------------------


def test_a_freeze_wins_attribution_over_every_other_control(tmp_path):
    """Dispositive, like `sanctions`.

    The interesting case is a payment that *would already have been refused* by something else. An
    operator reading that refusal needs to see the freeze, not whichever envelope happened to be
    tightest -- otherwise they go and change a budget that was never the reason.
    """
    g = _gov(tmp_path)
    try:
        huge = g.decide(amount_usd="5000", vendor="v", resource="/r")
        assert huge.verdict is Verdict.REJECT
        assert huge.attributed_control == "treasury", huge.attributed_control

        g.freeze("incident 4471")
        frozen = g.decide(amount_usd="5000", vendor="v", resource="/r")
        assert frozen.verdict is Verdict.REJECT
        assert frozen.attributed_control == "killswitch", (
            f"attributed to {frozen.attributed_control!r}; a freeze must win over the envelope "
            "that would also have refused"
        )
        assert "incident 4471" in next(r.detail for r in frozen.reasons if r.code == "frozen")
    finally:
        g.close()


def test_a_freeze_survives_a_restart(tmp_path):
    """A freeze that evaporates on restart is not a freeze -- a crash loop resumes spending."""
    first = _gov(tmp_path)
    try:
        first.freeze("stop, and stay stopped")
    finally:
        first.close()

    second = _gov(tmp_path)          # a fresh process would look exactly like this
    try:
        assert second.frozen is True
        assert _spend(second).verdict is Verdict.REJECT
    finally:
        second.close()


def test_an_unreadable_freeze_state_reads_as_frozen(tmp_path):
    """The fail-safe direction, and the one that would be easy to get backwards.

    A corrupt state file is an *unknown* state. Continuing to spend on an unknown state is exactly
    the failure the kill switch exists to prevent -- the four-state rule pointed at the switch's own
    state.
    """
    g = _gov(tmp_path)
    try:
        g.freeze("legitimate")
        g._layer.paths.freeze.write_text("{ this is not json", encoding="utf-8")

        assert g.frozen is True
        d = _spend(g)
        assert d.verdict is Verdict.REJECT
        assert d.attributed_control == "killswitch"
        assert "could not be read" in next(r.detail for r in d.reasons if r.code == "frozen")
    finally:
        g.close()


def test_a_freeze_without_a_reason_is_refused(tmp_path):
    """Whoever finds the agent stopped at 2am has to be able to read why."""
    g = _gov(tmp_path)
    try:
        for blank in ("", "   ", "\n"):
            with pytest.raises(ValueError, match="needs a reason"):
                g.freeze(blank)
        assert g.frozen is False, "a rejected freeze must not leave the governor frozen"
    finally:
        g.close()


# --- lifting it, and what it does not break ----------------------------------


def test_unfreezing_resumes_and_the_refusals_stay_in_the_journal(tmp_path):
    """Lifting a freeze is not erasing it. The decisions it refused are evidence."""
    g = _gov(tmp_path)
    try:
        g.authorize(amount_usd="0.001", vendor="v", resource="/r")
        g.freeze("brief hold")
        blocked = g.authorize(amount_usd="0.001", vendor="v", resource="/r")
        assert blocked.verdict is Verdict.REJECT
        g.unfreeze()

        assert g.frozen is False
        assert _spend(g).verdict is Verdict.APPROVE

        # `decisions()` returns the projected view, which carries the attributed control rather
        # than every reason -- and the attribution is the part that matters here.
        controls = [d.attributed_control for d in g.decisions(limit=10)]
        assert "killswitch" in controls, (
            f"the refusal during the freeze left no trace: {controls}"
        )
        assert g.verify()[0] is True, "the journal must still verify after a freeze"
    finally:
        g.close()


def test_the_state_carries_who_and_when(tmp_path):
    g = _gov(tmp_path)
    try:
        g.freeze("audit hold", by="oncall@example")
        state = g.freeze_state()
        assert state.frozen is True
        assert state.reason == "audit hold"
        assert state.by == "oncall@example"
        assert state.at and state.at.startswith("20")
    finally:
        g.close()


def test_a_frozen_decision_still_records_what_would_have_happened(tmp_path):
    """The engines run anyway, deliberately.

    A freeze narrows the verdict; it does not skip evaluation. So the record of a refusal during a
    freeze still shows the trust, risk and budget context -- which is what an operator needs when
    deciding whether it is safe to lift.
    """
    g = _gov(tmp_path)
    try:
        g.freeze("hold")
        d = _spend(g, "0.50")
        assert d.verdict is Verdict.REJECT
        assert d.budget is not None and d.budget.binding is None, (
            "the budget was not evaluated; a frozen decision should still carry its context"
        )
        assert len(d.reasons) > 1, "only the freeze reason was recorded"
    finally:
        g.close()


def test_the_freeze_store_needs_no_governor(tmp_path):
    """Small enough to drive from an operator script or a signal handler."""
    store = FreezeStore(tmp_path / "freeze.json")
    assert store.read().frozen is False
    store.freeze("from a script")
    assert store.read().frozen is True
    store.clear()
    assert store.read().frozen is False


def test_sanctions_outranks_the_freeze(tmp_path):
    """The declared precedence, asserted. VERD-4a requires the ranking to be documented; this
    checks the code agrees with the document.

    A sanctioned counterparty attempted *while frozen* must be attributed to `sanctions`. The
    freeze is the operator's own action and they know about it; "this agent tried to pay a barred
    party" is the fact that must survive. Attribution is won by the last `authorize` reason, so
    honouring the ranking means inserting the freeze *before* a sanctions clamp -- which is easy to
    get backwards and would silently hide the more serious finding.
    """
    g = _gov(tmp_path)
    try:
        g.freeze("operator hold")

        ordinary = g.decide(amount_usd="0.001", vendor="v", resource="/r")
        assert ordinary.attributed_control == "killswitch"

        sanctioned = g.decide(
            amount_usd="0.001", vendor="ofac-1", resource="/r", sanctioned=True
        )
        assert sanctioned.verdict is Verdict.REJECT
        assert sanctioned.attributed_control == "sanctions", (
            f"attributed to {sanctioned.attributed_control!r}; the freeze displaced a sanctions "
            "finding, which inverts the declared precedence"
        )
        # Both facts are still on the record -- precedence decides attribution, not retention.
        codes = {r.code for r in sanctioned.reasons}
        assert "frozen" in codes, "the freeze vanished from the record entirely"
    finally:
        g.close()
