"""Derived facts: composition that stays data.

Anything a pack cannot express is a missing engine — but "over $100 **and** the vendor is
new" needs no new measurement, only composition. Making that a derived fact keeps the
common case in data and reserves engines, which are code, for genuinely new measurements.

The design constraint that makes this safe: **declaration order is evaluation order.** A
derived fact may reference one declared above it and never one below. So a cycle cannot be
written down, rather than being written and then detected — there is no ordering in which
one is expressible.
"""

from __future__ import annotations

import tempfile

import pytest
import yaml

from aegoll.config import COMBINATORS, load_bundle
from aegoll.domain import Purpose, Vendor
from aegoll.engines.economic.policy import apply_derived
from aegoll.errors import PolicyError
from aegoll.runtime import Aegoll, Paths
from aegoll.validate import validate_pack


def pack(**over):
    base = {
        "version": 1,
        "name": "d",
        "rules": [{"id": "default", "priority": 9999, "when": {}, "then": "APPROVE"}],
    }
    base.update(over)
    return base


def errors(raw):
    return [p for p in validate_pack(raw, source="d") if p.severity == "error"]


def write(tmp_path, raw):
    path = tmp_path / "d.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


# --- it works -------------------------------------------------------------


def test_a_derived_fact_is_parsed_and_evaluated(tmp_path):
    raw = pack(
        derived=[{"name": "big", "all": [{"amount_usd": {"gte": 1.0}}]}],
        rules=[
            {"id": "no-big", "priority": 5, "when": {"derived.big": True}, "then": "REJECT"},
            {"id": "default", "priority": 9999, "when": {}, "then": "APPROVE"},
        ],
    )
    bundle = load_bundle(write(tmp_path, raw))
    assert [(d.name, d.combinator) for d in bundle.derived] == [("big", "all")]

    layer = Aegoll(bundle=bundle, paths=Paths.ephemeral(tempfile.mkdtemp()), agent_id="d")
    try:
        for amount, expected in (("0.10", "APPROVE"), ("2.50", "REJECT")):
            request = layer.build_request(
                resource="/market/snapshot",
                amount_usd=amount,
                vendor=Vendor(id="v", name="V"),
                purpose=Purpose.DATA_PURCHASE,
            )
            assert layer.decide(request).verdict.value == expected, amount
    finally:
        layer.close()


def test_a_derived_fact_may_reference_an_earlier_one(tmp_path):
    raw = pack(
        derived=[
            {"name": "big", "all": [{"amount_usd": {"gte": 1.0}}]},
            {"name": "big_and_new", "all": [{"derived.big": True}, {"vendor.is_new": True}]},
        ],
        rules=[
            {"id": "r", "priority": 5, "when": {"derived.big_and_new": True}, "then": "REJECT"},
            {"id": "default", "priority": 9999, "when": {}, "then": "APPROVE"},
        ],
    )
    assert errors(raw) == []
    bundle = load_bundle(write(tmp_path, raw))
    layer = Aegoll(bundle=bundle, paths=Paths.ephemeral(tempfile.mkdtemp()), agent_id="d")
    try:
        request = layer.build_request(
            resource="/market/snapshot",
            amount_usd="2.50",
            vendor=Vendor(id="brand-new", name="New"),
            purpose=Purpose.DATA_PURCHASE,
        )
        decision = layer.decide(request)
        assert decision.verdict.value == "REJECT"
        assert decision.matched_rule == "r"
    finally:
        layer.close()


@pytest.mark.parametrize(
    "combinator, clauses, facts, expected",
    [
        ("all", [{"a": {"gte": 1}}, {"b": True}], {"a": 5, "b": True}, True),
        ("all", [{"a": {"gte": 1}}, {"b": True}], {"a": 5, "b": False}, False),
        ("any", [{"a": {"gte": 9}}, {"b": True}], {"a": 5, "b": True}, True),
        ("any", [{"a": {"gte": 9}}, {"b": False}], {"a": 5, "b": True}, False),
        ("not", [{"a": {"gte": 1}}], {"a": 5}, False),
        ("not", [{"a": {"gte": 9}}], {"a": 5}, True),
        # `not: [x, y]` is not(x and y), stated in the source so it cannot be guessed at
        ("not", [{"a": {"gte": 1}}, {"b": True}], {"a": 5, "b": False}, True),
    ],
)
def test_each_combinator(combinator, clauses, facts, expected):
    from aegoll.config import Derived, PolicyBundle

    bundle = PolicyBundle(
        version=1, name="t", treasury=None, treasury_internal=None, trust=None,
        risk=None, roi=None, eiap=None, rules=(), hash="h",
        derived=(Derived(name="x", combinator=combinator, clauses=tuple(clauses)),),
    )
    assert apply_derived(bundle, facts)["derived.x"] is expected


def test_apply_derived_does_not_mutate_the_engine_facts():
    """The engines' output stays exactly what they measured.

    Keeping "the facts a rule matched" separable from "the measurements the engines took"
    is what lets a record report the second honestly.
    """
    from aegoll.config import Derived, PolicyBundle

    bundle = PolicyBundle(
        version=1, name="t", treasury=None, treasury_internal=None, trust=None,
        risk=None, roi=None, eiap=None, rules=(), hash="h",
        derived=(Derived(name="x", combinator="all", clauses=({"a": {"gte": 1}},)),),
    )
    facts = {"a": 5}
    out = apply_derived(bundle, facts)
    assert "derived.x" in out
    assert facts == {"a": 5}, "the engines' facts were mutated"


def test_a_pack_with_no_derived_facts_is_untouched():
    from aegoll.config import PolicyBundle

    bundle = PolicyBundle(
        version=1, name="t", treasury=None, treasury_internal=None, trust=None,
        risk=None, roi=None, eiap=None, rules=(), hash="h",
    )
    facts = {"a": 1}
    assert apply_derived(bundle, facts) is facts


# --- the refusals ---------------------------------------------------------


def test_a_cycle_cannot_be_written():
    """Not detected — unwritable. Neither ordering puts both facts in scope."""
    raw = pack(derived=[
        {"name": "a", "all": [{"derived.b": True}]},
        {"name": "b", "all": [{"derived.a": True}]},
    ])
    problems = errors(raw)
    assert problems and "declared *before*" in problems[0].message


def test_a_self_reference_is_refused():
    raw = pack(derived=[{"name": "a", "all": [{"derived.a": True}]}])
    assert errors(raw)


def test_a_forward_reference_says_what_is_in_scope():
    raw = pack(derived=[
        {"name": "first", "all": [{"amount_usd": {"gte": 1}}]},
        {"name": "second", "all": [{"derived.third": True}]},
        {"name": "third", "all": [{"amount_usd": {"gte": 2}}]},
    ])
    problems = errors(raw)
    assert problems
    assert "derived.first" in problems[0].message, "the message should list what IS in scope"


def test_a_duplicate_name_is_refused():
    raw = pack(derived=[
        {"name": "a", "all": [{"amount_usd": {"gte": 1}}]},
        {"name": "a", "any": [{"amount_usd": {"gte": 2}}]},
    ])
    problems = errors(raw)
    assert problems and "duplicate name" in problems[0].message


def test_shadowing_an_engine_fact_is_refused():
    """A rule matching it would read the derived value while its author expected the
    measured one — a silent wrong answer rather than an error."""
    raw = pack(derived=[{"name": "amount_usd", "all": [{"amount_usd": {"gte": 1}}]}])
    problems = errors(raw)
    assert problems and "shadows" in problems[0].message


@pytest.mark.parametrize("entry", [
    {"name": "a"},                                                   # no combinator
    {"name": "a", "all": [{"amount_usd": {"gte": 1}}], "any": []},   # two
])
def test_exactly_one_combinator_is_required(entry):
    problems = errors(pack(derived=[entry]))
    assert problems and "exactly one" in problems[0].message


def test_an_empty_clause_list_is_refused():
    problems = errors(pack(derived=[{"name": "a", "all": []}]))
    assert problems and "non-empty" in problems[0].message


def test_a_derived_fact_without_a_name_is_refused():
    problems = errors(pack(derived=[{"all": [{"amount_usd": {"gte": 1}}]}]))
    assert problems and "`name`" in problems[0].message


def test_an_unknown_key_on_a_derived_fact_is_refused():
    problems = errors(pack(derived=[
        {"name": "a", "all": [{"amount_usd": {"gte": 1}}], "then": "REJECT"},
    ]))
    assert problems and "unknown key" in problems[0].message


def test_a_clause_must_be_a_mapping():
    problems = errors(pack(derived=[{"name": "a", "all": ["amount_usd > 1"]}]))
    assert problems and "mapping" in problems[0].message


def test_the_comparator_vocabulary_applies_inside_a_derived_clause():
    """The gate does not weaken just because the clause is one level in."""
    problems = errors(pack(derived=[{"name": "a", "all": [{"amount_usd": {"gte_": 1}}]}]))
    assert problems and "unknown comparator" in problems[0].message


def test_an_unknown_fact_inside_a_derived_clause_is_refused():
    problems = errors(pack(derived=[{"name": "a", "all": [{"vendor.reputaton": True}]}]))
    assert problems and "unknown fact" in problems[0].message


def test_a_rule_referencing_an_undeclared_derived_fact_is_refused():
    problems = errors(pack(rules=[
        {"id": "r", "when": {"derived.nope": True}, "then": "REVIEW"},
    ]))
    assert problems and "no derived fact" in problems[0].message


def test_derived_must_be_a_list():
    problems = errors(pack(derived={"name": "a"}))
    assert problems and "must be a list" in problems[0].message


def test_a_broken_derived_fact_is_rejected_at_load(tmp_path):
    """Same rule as everything else: rejected at load, not at first match."""
    raw = pack(derived=[{"name": "a", "all": [{"amount_usd": {"gte_": 1}}]}])
    with pytest.raises(PolicyError, match="gte_"):
        load_bundle(write(tmp_path, raw))


def test_the_combinator_set_is_fixed():
    """A pack that could introduce a fourth combinator could introduce logic."""
    assert COMBINATORS == ("all", "any", "not")
