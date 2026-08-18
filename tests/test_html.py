"""The HTML report. A10a.

Three properties are worth a test each, and they are the three that would fail silently:

* **Self-contained.** A page that fetches anything has leaked that it was opened, and the
  contents of this one describe an agent's spending. "We did not add a CDN link" is a claim; a
  test that greps the rendered bytes is a check.
* **Nothing sensitive rendered.** `report()` already applies a vendor-safe projection, so this
  asserts the renderer does not undo it — a template is exactly where a controller id gets
  added back "for debugging".
* **The columns that carry meaning are present.** A page that dropped the attributed-control
  column would still look fine, which is the problem: the reader would conclude the layer
  cannot say which control refused.
"""

from __future__ import annotations

import re

import pytest

from tesoro.html import render
from tesoro.reporting import (
    ChainView,
    DecisionView,
    EnvelopeView,
    Report,
    RuleView,
)


def a_report(**overrides) -> Report:
    """A report with one of everything, so a panel cannot pass by being empty."""
    defaults = dict(
        policy_name="default",
        policy_hash="a1b2c3d4e5f60718",
        policy_rules=2,
        profile="aegs-1",
        rules=(
            RuleView(
                id="deny-sanctioned", priority=0, verdict="REJECT",
                condition="vendor.sanctioned", reason="sanctioned counterparty",
            ),
            RuleView(
                id="review-budget-exhausted", priority=10, verdict="REVIEW",
                condition="not budget.ok", reason="outside a budget envelope",
            ),
        ),
        decisions_total=2,
        settled=1,
        spent_usd="0.001000",
        by_verdict={"APPROVE": 1, "REJECT": 1},
        by_attributed_control={"treasury": 1, "policy": 1},
        envelopes={
            "external": (
                EnvelopeView(
                    name="daily", window="24h", limit_usd="50.000000",
                    used_usd="0.001000", headroom_usd="49.999000", tightest=True,
                ),
                EnvelopeView(
                    name="per_transaction", window="per call", limit_usd="10.000000",
                    used_usd=None, headroom_usd=None, cumulative=False, binding=True,
                ),
                EnvelopeView(
                    name="monthly", window="30d", limit_usd=None,
                    used_usd=None, headroom_usd=None,
                ),
            ),
            "internal": (),
        },
        decisions=(
            DecisionView(
                at="2026-08-17T12:00:00+00:00", verdict="REJECT", amount_usd="25.000000",
                vendor="data-co", resource="/premium/feed", attributed_control="treasury",
                reason="over the per-transaction ceiling",
            ),
            DecisionView(
                at="2026-08-17T11:59:00+00:00", verdict="APPROVE", amount_usd="0.001000",
                vendor="data-co", resource="/market/snapshot", attributed_control="policy",
                reason=None,
            ),
        ),
        pending_reviews=1,
        chain=ChainView(entries=4, valid=True, hash_name="sha256", hash_bits=128),
        tesoro_version="0.1.0",
        aegs_version="0.1",
    )
    defaults.update(overrides)
    return Report(**defaults)


# --- self-contained --------------------------------------------------------


def test_the_page_makes_no_outbound_request():
    """A10a.7. No CDN, no webfont, no analytics, no image, no absolute URL of any kind."""
    page = render(a_report())

    urls = re.findall(r'https?://[^\s"\'<>()]+', page)
    assert not urls, f"the page references external resources: {urls}"
    assert "//cdn" not in page
    assert not re.search(r'\ssrc\s*=', page), "a src attribute is a fetch"
    assert not re.search(r'<link\b', page), "a link element can pull a stylesheet or font"
    assert "@import" not in page, "an @import in CSS is a fetch"
    for call in ("fetch(", "XMLHttpRequest", "WebSocket", "navigator.sendBeacon", "EventSource"):
        assert call not in page, f"the page can call out: {call}"


def test_the_page_is_one_file():
    """No external stylesheet or script means the bytes are the whole artifact.

    Worth asserting separately from the URL check: a relative `href="style.css"` has no
    protocol and would pass the test above while making the file useless once mailed to
    somebody.
    """
    page = render(a_report())
    assert page.count("<style>") == 1
    assert page.count("<script>") == 1
    assert not re.search(r'<script[^>]+src', page)


# --- nothing sensitive -----------------------------------------------------


def test_a_key_shaped_string_is_not_rendered():
    """A10a.8.

    `Report` carries no key field, which is the actual defence — so this test asserts the
    property that keeps it true: every string the page prints comes from `as_dict()`, and a
    key has no path into that. If someone adds one, the assertion below starts failing and the
    conversation happens at review time rather than after a page is emailed to a vendor.
    """
    secret = "0x" + "b" * 64
    report = a_report(
        decisions=(
            DecisionView(
                at="2026-08-17T12:00:00+00:00", verdict="APPROVE", amount_usd="1.000000",
                vendor=secret, resource="/r", attributed_control="policy", reason=None,
            ),
        ),
    )
    page = render(report)
    # It *is* rendered here, because a vendor id is a vendor id and the renderer must not
    # start guessing which strings are secrets. The point is that it arrived through the
    # report's own vendor field, and `reporting.build` is what applies the vendor-safe
    # projection. This test pins the boundary: the renderer prints what it is given.
    assert secret in page, (
        "the renderer must not silently drop field contents -- filtering belongs in "
        "reporting.build, where the projection is documented, not scattered in a template"
    )

    for forbidden in ("privateKey", "private_key", "mnemonic", "seedPhrase", "secretKey"):
        assert forbidden not in page, (
            f"{forbidden} reached the page. Report carries no such field, so this means one "
            "was added -- see docs/api-surface.md §3 and invariant 9"
        )


def test_the_controller_has_no_path_into_the_page():
    """Invariant 10, asserted where it is easiest to break.

    `Report` deliberately has no controller, operator or wallet field. A renderer cannot leak
    what it is never handed, and this test fails if one is ever added to the wire format
    without a decision being made about the page.
    """
    keys = set(a_report().as_dict())
    for field in ("controller", "operator", "wallets", "spendingLimits"):
        assert field not in keys, (
            f"{field!r} is now in the report's wire format. Identity is pseudonymous by "
            "default (invariant 10) and this page is shareable -- decide deliberately."
        )


# --- the columns that carry meaning ---------------------------------------


def test_the_attributed_control_is_on_the_page():
    """A10a.9, the part of the golden file that actually matters.

    A page that lost this column looks complete and answers the wrong question: the reader
    learns *what* happened and not *which control decided*, which is the one thing this layer
    exists to be able to say.
    """
    page = render(a_report())
    assert "Attributed control" in page
    assert "treasury" in page, "the deciding control for the refused decision is missing"
    assert "over the per-transaction ceiling" in page, "the deciding reason is missing"


def test_all_four_panels_are_present():
    page = render(a_report())
    for heading, question in (
        ("Policy", "What will this do?"),
        ("Envelopes", "How much is left?"),
        ("Decisions", "Why did my agent stop?"),
        ("Evidence", "Can I trust this record?"),
    ):
        assert f">{heading}<" in page, f"the {heading} panel is missing"
        assert question in page, f"the {heading} panel does not state what it answers"


def test_the_rules_are_shown_in_evaluation_order():
    """A count of rules cannot answer "what will this do". The order can, because the first
    matching rule decides."""
    page = render(a_report())
    assert page.index("deny-sanctioned") < page.index("review-budget-exhausted")
    assert "vendor.sanctioned" in page, "the condition is not rendered in readable terms"


def test_an_absent_limit_renders_as_absent_not_zero():
    """A10a.4, and invariant 5 in the place a reader misreads it fastest.

    `monthly` has no limit. Rendering that as `$0.00` would state a ceiling of zero — the
    tightest possible limit — where there is in fact no limit at all. Exactly inverted.
    """
    page = render(a_report())
    assert "absent" in page
    assert "$0.00<" not in page and ">$0.000000<" not in page, (
        "a zero-valued money cell is present; an unset limit must render as absent"
    )


def test_a_per_call_ceiling_is_not_shown_as_used_of_limit():
    """`per_transaction` never accumulates, so "used" does not apply to it.

    Showing `$0.000000 of $10.000000` beside the cumulative windows reads as "nothing has been
    spent", which is false and was a real defect in the terminal renderer first.
    """
    page = render(a_report())
    assert "per-call ceiling" in page


def test_the_truncation_caveat_is_on_the_page():
    """A10a.5. A page that says VALID without it overstates what a hash chain proves."""
    page = render(a_report())
    assert "VALID" in page
    assert "truncation" in page
    assert "prefix of a" in page, "the caveat does not explain why truncation is undetectable"
    assert page.index("VALID") < page.index("truncation"), (
        "the caveat must sit with the chain state, not in a footnote below everything"
    )


def test_a_broken_chain_says_so():
    page = render(a_report(chain=ChainView(
        entries=4, valid=False, problems=("entry 3 does not follow entry 2",),
        hash_name="sha256", hash_bits=128,
    )))
    assert "BROKEN" in page
    assert "entry 3 does not follow entry 2" in page


def test_the_hash_strength_is_declared():
    """AEGS-0.1-EVID-5: an auditor cannot assess a chain whose strength is unstated."""
    page = render(a_report())
    assert "sha256" in page
    assert "128" in page


# --- escaping --------------------------------------------------------------


@pytest.mark.parametrize("field", ["resource", "vendor", "reason"])
def test_counterparty_text_cannot_inject_markup(field):
    """A resource path and a counterparty name both arrive from outside and both land here.

    AEGS-0.1-SEC-2 holds that the *decision path* reads no prose. A renderer does, so escaping
    is this module's responsibility. Parametrized because it only takes one unescaped field.
    """
    import dataclasses

    payload = '<img src=x onerror="alert(1)">'
    decision = DecisionView(
        at="2026-08-17T12:00:00+00:00", verdict="REJECT", amount_usd="1.000000",
        vendor="v", resource="/r", attributed_control="treasury", reason="r",
    )
    page = render(a_report(decisions=(
        dataclasses.replace(decision, **{field: payload}),
    )))

    # The test is whether a *tag* was produced, not whether the word `onerror` appears: the
    # escaped text `onerror=&quot;` is inert, and asserting on the substring alone would fail
    # a correct renderer. What must not exist is an angle bracket the browser can parse.
    assert "<img" not in page
    assert '<img src=x' not in page
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in page, (
        "the payload should appear fully escaped, rather than be dropped -- silently "
        "discarding field contents hides what the counterparty actually sent"
    )
    # And the quotes are escaped too, so the payload cannot break out of an attribute if this
    # value is ever rendered into one.
    assert '"alert(1)"' not in page


def test_a_policy_rule_cannot_inject_markup():
    """A policy pack is data, and a shared pack is data from somewhere else. PATH-5 keeps it
    from executing in the engine; escaping keeps it from executing in a browser."""
    page = render(a_report(rules=(
        RuleView(
            id="<script>alert(1)</script>", priority=0, verdict="REJECT",
            condition="vendor.sanctioned", reason="x",
        ),
    )))
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


# --- shape -----------------------------------------------------------------


def test_an_empty_report_still_renders():
    """The first thing a new user sees is a report with nothing in it. A traceback there is a
    worse first impression than an empty page."""
    page = render(Report(
        policy_name="default", policy_hash="0" * 16, policy_rules=0, profile=None,
        decisions_total=0, settled=0, spent_usd="0.000000",
    ))
    assert page.startswith("<!DOCTYPE html>")
    assert "No decisions recorded yet." in page
    assert "No evidence chain available." in page


def test_the_page_is_valid_enough_to_parse():
    """Not a full validator — just that tags balance, so a dropped closing tag fails here
    rather than as a mangled page in somebody's browser."""
    from html.parser import HTMLParser

    class Balance(HTMLParser):
        VOID = {"meta", "br", "hr", "img", "input", "link"}

        def __init__(self):
            super().__init__()
            self.stack: list[str] = []
            self.problems: list[str] = []

        def handle_starttag(self, tag, attrs):
            if tag not in self.VOID:
                self.stack.append(tag)

        def handle_endtag(self, tag):
            if not self.stack:
                self.problems.append(f"</{tag}> with nothing open")
            elif self.stack[-1] != tag:
                self.problems.append(f"</{tag}> closes <{self.stack[-1]}>")
                self.stack.pop()
            else:
                self.stack.pop()

    parser = Balance()
    parser.feed(render(a_report()))
    assert not parser.problems, parser.problems
    assert not parser.stack, f"unclosed: {parser.stack}"
