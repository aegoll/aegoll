"""Profiles: a conformance contract that never touches a verdict.

The central claim, and the one test here that would matter most if it broke: **selecting a
profile changes conformance scoring and nothing else.** No engine imports `profiles.py`,
nothing in the decision path reads it, and the same request under `aegs-1`, `aegs-2` and
`none` produces the same verdict.

If that ever stopped being true, two implementations at the same profile could disagree on
outcomes while both claiming conformance, and the profile would have become a second,
weaker policy language.
"""

from __future__ import annotations

import ast
import tempfile

import pytest
from conftest import imported_names, package_dir

from tesoro import record as record_mod
from tesoro.config import load_bundle
from tesoro.domain import Purpose, Vendor
from tesoro.errors import ConfigError
from tesoro.profiles import RANK, Profile, available_profiles
from tesoro.runtime import Tesoro, Paths


@pytest.fixture(scope="module")
def a_record():
    """One real Decision Record from a real decision, with no intent declared."""
    layer = Tesoro(
        bundle=load_bundle(), paths=Paths.ephemeral(tempfile.mkdtemp()), agent_id="p-agent"
    )
    try:
        request = layer.build_request(
            resource="/market/snapshot",
            amount_usd="2.50",
            vendor=Vendor(id="acme", name="Acme"),
            purpose=Purpose.DATA_PURCHASE,
        )
        layer.authorize(request)
        entry = [e for e in layer.audit.entries() if e.payload.get("decision")][-1]
        return record_mod.from_audit_entry(entry)
    finally:
        layer.close()


# --- the profile never touches the decision path -------------------------


def test_no_engine_imports_the_profile_module():
    """A profile is evidence completeness, not a verdict input.

    The moment an engine could read a profile, choosing one could change what gets
    approved — and then two conformant implementations at the same level could disagree
    on outcomes.
    """
    offenders = [
        f"{path.relative_to(package_dir())}:{lineno} imports {name}"
        for path in (package_dir() / "engines").rglob("*.py")
        for lineno, name in imported_names(path)
        if name.rstrip(".").endswith("profiles")
    ]
    assert not offenders, "\n  ".join(offenders)


def test_the_decision_path_does_not_import_the_profile_module():
    """`authorize.py` is the composition root. It must not know profiles exist."""
    for module in ("authorize.py", "domain.py", "store.py"):
        names = [n for _, n in imported_names(package_dir() / module)]
        assert not [n for n in names if n.rstrip(".").endswith("profiles")], module


def test_the_profile_module_never_mutates_a_record():
    """`assess()` reads. An AST check, because a reviewer will not notice an added write.

    A profile that could edit a record would be able to make a non-conformant decision
    look conformant, which is the one thing this layer must never be able to do.
    """
    source = (package_dir() / "profiles.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    writes = []
    for node in ast.walk(tree):
        # record[...] = ... or record.setdefault(...) / .update(...) / .pop(...)
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            writes.append(f"line {node.lineno}: subscript assignment")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"setdefault", "update", "pop", "clear", "__setitem__"}:
                writes.append(f"line {node.lineno}: .{node.func.attr}()")
    assert not writes, "profiles.py mutates something:\n  " + "\n  ".join(writes)


@pytest.mark.parametrize("name", ["aegs-1", "aegs-2", "none"])
def test_the_verdict_is_identical_under_every_profile(name):
    """The whole point, stated as an assertion.

    The profile is not even passed to the layer — which is itself the evidence. There is
    no parameter to pass it through, because the decision path has no use for one.
    """
    layer = Tesoro(
        bundle=load_bundle(), paths=Paths.ephemeral(tempfile.mkdtemp()), agent_id="v-agent"
    )
    try:
        request = layer.build_request(
            resource="/market/snapshot",
            amount_usd="2.50",
            vendor=Vendor(id="acme", name="Acme"),
            purpose=Purpose.DATA_PURCHASE,
        )
        decision = layer.decide(request)
        assert decision.verdict.value == "REVIEW"
        assert decision.matched_rule == "review-untrusted-vendor-nontrivial"
    finally:
        layer.close()


# --- loading the vendored manifests --------------------------------------


def test_the_profiles_are_vendored_and_load():
    assert available_profiles() == ["aegs-1", "aegs-2", "none", "stablecoin-1"]
    for name in available_profiles():
        assert Profile.load(name).id == name


def test_an_unknown_profile_names_the_available_ones():
    with pytest.raises(ConfigError) as exc:
        Profile.load("aegs-99")
    assert "aegs-1" in str(exc.value)


def test_the_manifests_resolve_inside_the_package():
    """Package data, not a sibling directory. PLAN.md F-A1."""
    for name in available_profiles():
        source = Profile.load(name).source
        assert source is not None
        assert source.resolve().is_relative_to(package_dir())


def test_the_vendored_profiles_declare_their_source():
    provenance = package_dir() / "_profiles" / "PROVENANCE.txt"
    assert provenance.is_file(), "vendored data with no stated source is unmaintainable"
    text = provenance.read_text(encoding="utf-8")
    assert "commit:" in text and "tesoro-labs/aegs" in text


def test_none_is_an_opt_out_not_an_empty_contract():
    """It lists all thirteen controls as OPTIONAL rather than listing nothing.

    An escape hatch that does not work is one people fork around, and a fork puts the
    user out of reach of every later fix.
    """
    none = Profile.load("none")
    assert none.enforces() is False
    assert len(none.requirements) == 13
    assert all(r.requirement == "OPTIONAL" for r in none.requirements)


def test_aegs_2_only_tightens_aegs_1():
    """Enforced in the standard's CI too. Checked here because a stale vendored copy
    would carry a relaxation the standard has already rejected."""
    one = Profile.load("aegs-1")
    two = Profile.load("aegs-2")
    assert two.extends == "aegs-1"
    for req in one.requirements:
        assert RANK[two.requirement_for(req.control)] >= RANK[req.requirement], req.control


def test_every_required_control_names_a_record_path():
    """A requirement with nowhere to look for its evidence is not checkable."""
    for name in available_profiles():
        for req in Profile.load(name).required_controls():
            if req.requirement == "MUST_EXERCISE":
                assert req.record_path, f"{name}: {req.control}"


#: Schemas with no engine anywhere. AEGS-0.1-PROF-6 / PROF-6a.
UNBACKED = {"AMLAssessment", "ComplianceAssessment", "IncidentRecord"}


def test_no_ladder_profile_requires_a_control_with_no_engine():
    """AEGS-0.1-PROF-6, and the word "ladder" is the whole content of the amendment.

    Every implementation is expected to claim a rung, so an unreachable rung is dead weight:
    everyone claims the rung below, the higher level goes unused, and the standard has gained
    a decoration. Requiring the impossible moves the real bar *down*.

    This test used to scan every profile. It now scans the ladder, because PROF-6a lets a
    **vertical** profile require one of these — see the next test for the guard that keeps
    that from being a loophole.
    """
    for name in available_profiles():
        profile = Profile.load(name)
        if profile.is_vertical():
            continue
        for control in UNBACKED:
            assert profile.requirement_for(control) == "OPTIONAL", f"{name}: {control}"


def test_a_profile_requiring_an_engineless_control_names_who_can_satisfy_it():
    """AEGS-0.1-PROF-6a. The guard that stops "vertical" being a label to hide behind.

    A vertical profile may require a control no implementation has an engine for, because
    the question is whether the *deployment class it names* can satisfy it rather than
    whether any AEGS implementation can. For `stablecoin-1` that class is obliged entities,
    for whom sanctions and AML screening is a licence condition they already meet.

    Without this test the amendment is an escape hatch: label a profile vertical and require
    anything. The half a test can check is that a class was named. Whether that class
    genuinely meets the bar is a judgement, and PROF-6a says so rather than implying a
    rigour it does not have.
    """
    for name in available_profiles():
        profile = Profile.load(name)
        for control in UNBACKED:
            if RANK[profile.requirement_for(control)] > 0:
                assert profile.deployment_class, (
                    f"{name} requires {control}, which no implementation has an engine for, "
                    "and names no deploymentClass. Under PROF-6 that profile is "
                    "unsatisfiable by anyone; under PROF-6a a vertical profile may require "
                    "it but must name the class expected to satisfy it."
                )


def test_this_implementation_does_not_conform_to_stablecoin_1(a_record):
    """The consequence of PROF-6a, kept rather than worked around.

    `stablecoin-1` requires a recorded AML position. tesoro has no AML control, so
    `IMPLEMENTED_CONTROLS` keeps `aml` out of the record entirely — absent, because a
    present key would claim a control that does not exist. Absence fails MUST_RECORD.

    **So the reference implementation fails its own vertical profile, and that is the point.**
    A profile whose only claimants are the implementations that wrote it measures nothing.
    This test exists so that a future change which makes tesoro "pass" has to come here and
    say why: if a screening provider was integrated, replace this test. If the record was
    widened to emit an empty `aml` key, that is the false statement caught on 2026-08-22 and
    the change is wrong.
    """
    assessment = Profile.load("stablecoin-1").assess(a_record)
    assert not assessment.conformant
    findings = [f for f in assessment.findings if f.control == "AMLAssessment"]
    assert len(findings) == 1
    assert findings[0].requirement == "MUST_RECORD"
    assert "assessments/aml" in findings[0].where

    # And it fails for that reason alone -- every other control of aegs-2 still passes.
    assert {f.control for f in assessment.findings} == {"AMLAssessment"}


# --- scoring a real record -----------------------------------------------


def test_a_real_decision_is_conformant_under_both_profiles(a_record):
    """If the reference implementation cannot reach its own default profile, the profile
    is wrong — a baseline nobody can meet is not a baseline."""
    for name in ("aegs-1", "aegs-2"):
        assessment = Profile.load(name).assess(a_record)
        assert assessment.conformant, [str(f) for f in assessment.findings]


def test_scoring_differs_between_profiles(a_record):
    """A4.6: switching profile changes what is reported, and touches no engine code."""
    one = Profile.load("aegs-1").assess(a_record)
    two = Profile.load("aegs-2").assess(a_record)
    none = Profile.load("none").assess(a_record)

    assert len(two.exercised) > len(one.exercised), "aegs-2 requires strictly more"
    assert none.exercised == (), "`none` requires nothing, so it exercises nothing"
    assert {"TrustAssessment", "RiskAssessment"} <= set(two.exercised)


def test_an_explicit_null_satisfies_must_record(a_record):
    """`intentId: null` is the record saying *no intent was declared*.

    That is a recorded position and exactly what the four-state rule asks for. Scoring it
    as a failure would punish an implementation for being honest, and push the next one
    toward omitting the key instead. The first version of `assess()` got this wrong.
    """
    assert a_record["intentId"] is None
    assessment = Profile.load("aegs-1").assess(a_record)
    assert not [f for f in assessment.findings if f.control == "EconomicIntent"]


def test_a_missing_key_does_not_satisfy_must_record(a_record):
    """A null one is an answer; an absent one is a gap."""
    without = {k: v for k, v in a_record.items() if k != "intentId"}
    findings = Profile.load("aegs-1").assess(without).findings
    assert [f for f in findings if f.control == "EconomicIntent"]


def test_declaring_an_intent_exercises_the_control():
    layer = Tesoro(
        bundle=load_bundle(), paths=Paths.ephemeral(tempfile.mkdtemp()), agent_id="i-agent"
    )
    try:
        layer.intents.declare(
            agent_id="i-agent",
            purpose="buy market data",
            maximum_usd="20",
            allowed_resources=("/market/snapshot",),
        )
        request = layer.build_request(
            resource="/market/snapshot",
            amount_usd="2.50",
            vendor=Vendor(id="acme", name="Acme"),
            purpose=Purpose.DATA_PURCHASE,
        )
        layer.authorize(request)
        entry = [e for e in layer.audit.entries() if e.payload.get("decision")][-1]
        record = record_mod.from_audit_entry(entry)
    finally:
        layer.close()

    assert record["intentId"] is not None
    assessment = Profile.load("aegs-1").assess(record)
    assert "EconomicIntent" in assessment.exercised


def test_a_missing_required_control_is_a_finding(a_record):
    stripped = {k: v for k, v in a_record.items() if k != "budgetState"}
    assessment = Profile.load("aegs-1").assess(stripped)
    assert not assessment.conformant
    findings = [f for f in assessment.findings if f.control == "BudgetEnvelope"]
    assert findings and "budgetState" in findings[0].message


def test_a_not_run_control_is_a_finding_and_honest(a_record):
    """A control reporting `measured: false` did not run, and the profile says so.

    This is a better failure than a zero-filled field asserting a screening that never
    happened — which is why the schemas require omission rather than zero-filling.
    """
    faked = dict(a_record)
    faked["assessments"] = {
        **(a_record.get("assessments") or {}),
        "trust": {"measured": False},
    }
    findings = Profile.load("aegs-2").assess(faked).findings
    trust = [f for f in findings if f.control == "TrustAssessment"]
    assert trust and "not-run" in trust[0].message


def test_zero_and_false_count_as_evidence(a_record):
    """A headroom of zero is a measurement. `sanctioned: false` is a screening that ran.

    Treating either as absent would punish an implementation for reporting accurately,
    which is the opposite of what this layer is for.
    """
    faked = dict(a_record)
    faked["budgetState"] = {"headroomAtomic": 0, "ok": False}
    assessment = Profile.load("aegs-1").assess(faked)
    assert "BudgetEnvelope" in assessment.exercised


def test_none_finds_nothing_wrong_with_an_empty_record():
    """It makes no claim, so nothing can fail it."""
    assessment = Profile.load("none").assess({})
    assert assessment.conformant
    assert assessment.findings == ()


def test_an_assessment_serialises_for_a_record(a_record):
    """This is the `ComplianceAssessment` control's content: controls exercised against a
    *named* profile, rather than a list of controls the implementation happens to have."""
    data = Profile.load("aegs-2").assess(a_record).as_dict()
    assert data["profile"] == "aegs-2"
    assert data["conformant"] is True
    assert isinstance(data["controlsExercised"], list)
    assert "controlsAbsent" in data and "findings" in data
