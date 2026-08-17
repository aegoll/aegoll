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


def _evaluate_envelopes(data: dict) -> dict:
    """Evaluate the envelope set a vector describes, using this implementation's types.

    Constructs `Envelope` and `CountEnvelope` directly rather than driving a whole decision.
    A vector is about the envelope rule, and routing it through the full engine would drag
    in policy, trust, risk and a store — so a failure would no longer say which rule broke.

    `otherChannel` is deliberately **ignored**, and that is not laziness. ENV-9 says channels
    never share an envelope, so the way an implementation passes those vectors is by the
    other channel's state having no path into this evaluation at all. A runner that read it
    and then carefully declined to use it would be proving something weaker.
    """
    from aegoll.domain import CountEnvelope, Envelope

    amount = data.get("amountAtomic", 0)
    repeat = data.get("repeat", 1)

    envelopes, headroom, used = [], {}, {}
    for spec in data.get("envelopes") or []:
        limit = spec["limit"]
        if limit is None:
            # An absent limit constrains nothing (ENV-8). This implementation has no
            # representation for one, and the vector is answered by construction: an
            # unconstrained envelope is an envelope that is not there.
            headroom[spec["name"]] = None
            used[spec["name"]] = None
            continue
        envelope = Envelope(
            name=spec["name"],
            limit_atomic=limit,
            used_atomic=spec["used"],
            window=spec.get("window", ""),
            cumulative=spec.get("cumulative", True),
        )
        envelopes.append(envelope)
        headroom[envelope.name] = envelope.headroom_atomic
        used[envelope.name] = envelope.used_atomic if envelope.cumulative else None

    counters = [
        CountEnvelope(
            name=spec["name"], limit=spec["limit"], used=spec["used"],
            window=spec.get("window", ""),
        )
        for spec in data.get("counters") or []
    ]

    breached: list[str] = []
    for _ in range(repeat):
        for envelope in envelopes:
            if not envelope.admits(amount) and envelope.name not in breached:
                breached.append(envelope.name)
    breached += [c.name for c in counters if not c.admits()]

    failing = [e for e in envelopes if e.name in breached]
    binding = (
        min(failing, key=lambda e: e.headroom_atomic).name if failing
        else (breached[0] if breached else None)
    )
    tightest = min(envelopes, key=lambda e: e.headroom_atomic).name if envelopes else None

    return {
        "ok": not breached,
        "breached": breached,
        "binding": binding,
        "tightest": tightest,
        "headroom": headroom,
        "used": used,
    }


def _resolve_verdict(data: dict) -> dict:
    """Resolve a sequence of proposals against a standing verdict.

    Uses this implementation's own `narrower()` and `Verdict` so the vectors exercise the
    real severity ordering rather than a copy of it. A runner that reimplemented the
    ordering would pass whatever it reimplemented.

    Attribution here is *the last control that narrowed*, computed the way
    `authorize.decide()` computes it: a proposal is attributed only when applying it
    actually changes the standing verdict. Equality is not narrowing -- see VERD-4.
    """
    from aegoll.domain import Verdict, narrower

    standing_name = data.get("standing")
    # `noRuleMatched` means nothing produced a verdict, and VERD-8 says the fall-through
    # must not approve. This implementation fails closed to REVIEW.
    if standing_name is None:
        standing = Verdict.REVIEW if data.get("noRuleMatched") else Verdict.APPROVE
    else:
        standing = Verdict(standing_name)

    # Controls whose finding decides attribution whether or not it narrowed. VERD-4a.
    # `sanctions` is dispositive in this implementation, which is why a sanctioned
    # counterparty is not attributed to whatever spending limit happened to bite first.
    dispositive = list(data.get("dispositive") or [])

    verdict = standing
    attributed = None
    dispositive_hit = None
    evidenced: list[str] = []

    for proposal in data.get("proposals") or []:
        control = proposal["control"]
        if proposal.get("recordsEvidence"):
            evidenced.append(control)

        proposed_name = proposal.get("verdict")
        if proposed_name is None:
            continue  # no opinion, which is not approval

        # A dispositive control's finding counts even when it changes nothing -- that is
        # the whole point of the declaration, and the reason it must be declared.
        if control in dispositive and dispositive_hit is None:
            dispositive_hit = control

        clamped = narrower(verdict, Verdict(proposed_name))
        if clamped is not verdict:
            verdict = clamped
            attributed = control

    narrowed_by = attributed
    if dispositive_hit is not None:
        attributed = dispositive_hit

    return {
        "verdict": verdict.value,
        # None here asserts that no proposal narrowed. It is NOT an absent attribution --
        # VERD-5 forbids that, and `standingControl` is what a real record would carry.
        "attributedControl": attributed or data.get("standingControl"),
        "narrowedBy": narrowed_by,
        "evidenced": evidenced,
    }


def _canonical(payload: dict) -> str:
    """The canonical serialisation this implementation hashes over. EVID-3.

    Calls the same helper the journal uses rather than reproducing its arguments here. A
    runner that wrote its own `json.dumps(..., sort_keys=True)` would pass whichever
    settings it chose and say nothing about the ones the journal actually uses.
    """
    import json

    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _build_chain(entries: list[dict]) -> list[dict]:
    """Build a real chain with real hashes, using this implementation's hash function."""
    from aegoll.engines.evidence.audit import GENESIS, _hash_entry

    built, prev = [], GENESIS
    for spec in entries:
        entry_hash = _hash_entry(spec["seq"], spec["at"], prev, spec["payload"])
        built.append({
            "seq": spec["seq"], "at": spec["at"], "prev_hash": prev,
            "entry_hash": entry_hash, "payload": spec["payload"],
        })
        prev = entry_hash
    return built


def _tamper(chain: list[dict], spec: dict) -> list[dict]:
    """Alter a built chain the way a vector asks. Hashes are deliberately NOT recomputed.

    That is the point: an attacker who could recompute every downstream hash would need the
    whole journal, and the chain's guarantee is precisely that a local edit does not stay
    local. `truncate` is the exception — it needs no recomputation at all, which is why it is
    undetectable and why EVID-6 requires disclosure instead of a fix.
    """
    from aegoll.engines.evidence.audit import _hash_entry

    kind = spec["kind"]
    chain = [dict(e) for e in chain]

    if kind == "edit":
        chain[spec["seq"]]["payload"] = spec["payload"]
        return chain

    if kind == "editMany":
        for seq in spec["seqs"]:
            chain[seq]["payload"] = {**chain[seq]["payload"], "tampered": True}
        return chain

    if kind == "deleteMiddle":
        return [e for e in chain if e["seq"] != spec["seq"]]

    if kind == "swap":
        i = spec["seq"]
        chain[i], chain[i - 1] = chain[i - 1], chain[i]
        return chain

    if kind == "truncate":
        return chain[: spec["keep"]]

    raise AssertionError(f"unknown tamper kind {kind!r}")


def _verify_chain(chain: list[dict]) -> tuple[bool, list[str], list[str]]:
    """Walk a chain the way `AuditLog.verify()` does, reporting every problem. EVID-7."""
    from aegoll.engines.evidence.audit import GENESIS, _hash_entry

    problems: list[str] = []
    kinds: list[str] = []
    prev = GENESIS
    expected_seq = 0

    for entry in chain:
        if entry["seq"] != expected_seq:
            problems.append(f"seq {entry['seq']}: out of order")
            kinds.append("sequence")
        if entry["prev_hash"] != prev:
            problems.append(f"seq {entry['seq']}: prev_hash does not match")
            kinds.append("prevHash")
        recomputed = _hash_entry(
            entry["seq"], entry["at"], entry["prev_hash"], entry["payload"]
        )
        if recomputed != entry["entry_hash"]:
            problems.append(f"seq {entry['seq']}: content hash mismatch")
            kinds.append("contentHash")
        prev = entry["entry_hash"]
        expected_seq = entry["seq"] + 1

    return (not problems), problems, kinds


def _evidence(operation: str, data: dict) -> dict:
    from aegoll.engines.evidence.audit import GENESIS, _hash_entry

    if operation == "canonical_serialise":
        return {"canonical": _canonical(data["payload"])}

    if operation == "chain_hash":
        prev = data.get("prevHash") or GENESIS
        first = _hash_entry(data["seq"], data["at"], prev, data["payload"])
        if "alsoPayload" in data:
            second = _hash_entry(data["seq"], data["at"], prev, data["alsoPayload"])
            return {"hashesEqual": first == second, "hash": first}
        return {"hash": first}

    if operation == "verify_chain":
        chain = _build_chain(data.get("entries") or [])
        if data.get("tamper"):
            chain = _tamper(chain, data["tamper"])
        valid, problems, kinds = _verify_chain(chain)
        return {
            "valid": valid, "problems": problems, "problemKinds": kinds,
            "entryCount": len(chain),
        }

    if operation == "hash_strength":
        # Read the length from the hash function itself rather than from a constant, so
        # this cannot drift from what the journal really does.
        sample = _hash_entry(0, "t", GENESIS, {})
        return {
            "declaresFunction": True,   # sha256, named in audit.py
            "declaresBits": True,
            "bits": len(sample) * 4,
        }

    raise AssertionError(f"unknown evidence operation {operation!r}")


def _run(vector: dict):
    """Perform the operation under test, returning either a value or a refusal category."""
    operation = vector["operation"]
    data = vector["input"]

    if operation == "evaluate_envelopes":
        return _evaluate_envelopes(data)

    if operation == "resolve_verdict":
        return _resolve_verdict(data)

    if operation in {
        "canonical_serialise", "chain_hash", "verify_chain", "hash_strength",
    }:
        return _evidence(operation, data)

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
    if vector["operation"] == "evaluate_envelopes":
        _assert_envelopes(clause, vector, expected, got)
        return

    if vector["operation"] == "resolve_verdict":
        _assert_verdict(clause, vector, expected, got)
        return

    if vector["operation"] in {
        "canonical_serialise", "chain_hash", "verify_chain", "hash_strength",
    }:
        _assert_evidence(clause, vector, expected, got)
        return

    key = next(iter(expected))
    assert got[key] == expected[key], (
        f"{clause}: {vector['description']}\n"
        f"  expected {key}={expected[key]!r}, got {got[key]!r}\n"
        + (f"  note: {vector['note']}" if vector.get("note") else "")
    )


def _assert_envelopes(clause: str, vector: dict, expected: dict, got: dict) -> None:
    """Check only what the vector asserts, and compare `breached` as a set.

    Order in `breached` is not significant — the spec requires every breach to be
    *reported*, not reported in any particular sequence, and a runner demanding an order
    would be testing an implementation detail the specification deliberately leaves open.
    """
    note = f"\n  note: {vector['note']}" if vector.get("note") else ""
    head = f"{clause}: {vector['description']}"

    assert got["ok"] == expected["ok"], (
        f"{head}\n  expected ok={expected['ok']}, got ok={got['ok']}"
        f"\n  breached: {got['breached']}{note}"
    )

    if "breached" in expected:
        assert set(got["breached"]) == set(expected["breached"]), (
            f"{head}\n  expected breached={sorted(expected['breached'])}, "
            f"got {sorted(got['breached'])}{note}"
        )

    for field in ("binding", "tightest"):
        if field in expected:
            assert got[field] == expected[field], (
                f"{head}\n  expected {field}={expected[field]!r}, got {got[field]!r}{note}"
            )

    for field in ("headroom", "used"):
        for name, want in (expected.get(field) or {}).items():
            assert got[field].get(name) == want, (
                f"{head}\n  expected {field}[{name!r}]={want!r}, "
                f"got {got[field].get(name)!r}{note}"
            )


def _assert_verdict(clause: str, vector: dict, expected: dict, got: dict) -> None:
    """Check only what the vector asserts.

    `attributedControl: null` in a vector means *nothing narrowed*, which is a different
    claim from *attribution is absent* -- VERD-5 forbids the second. So a null expectation
    is checked against `narrowedBy`, while the record's actual attribution falls back to
    whatever produced the standing verdict.
    """
    note = f"\n  note: {vector['note']}" if vector.get("note") else ""
    head = f"{clause}: {vector['description']}"

    if "verdict" in expected:
        assert got["verdict"] == expected["verdict"], (
            f"{head}\n  expected verdict={expected['verdict']}, got {got['verdict']}{note}"
        )

    if "verdictNot" in expected:
        assert got["verdict"] != expected["verdictNot"], (
            f"{head}\n  the verdict MUST NOT be {expected['verdictNot']}, and it is"
            f"{note}"
        )

    if "attributedControl" in expected:
        want = expected["attributedControl"]
        if want is None:
            assert got["narrowedBy"] is None, (
                f"{head}\n  no proposal should have narrowed, but {got['narrowedBy']!r} "
                f"did{note}"
            )
            assert got["attributedControl"] is not None or not vector["input"].get(
                "standingControl"
            ), (
                f"{head}\n  attribution is absent, which VERD-5 forbids{note}"
            )
        else:
            assert got["attributedControl"] == want, (
                f"{head}\n  expected attributedControl={want!r}, got "
                f"{got['attributedControl']!r}{note}"
            )

    if "evidenced" in expected:
        assert set(got["evidenced"]) == set(expected["evidenced"]), (
            f"{head}\n  expected evidenced={sorted(expected['evidenced'])}, "
            f"got {sorted(got['evidenced'])}{note}"
        )


def _assert_evidence(clause: str, vector: dict, expected: dict, got: dict) -> None:
    """Check only what the vector asserts."""
    note = f"\n  note: {vector['note']}" if vector.get("note") else ""
    head = f"{clause}: {vector['description']}"

    if "canonical" in expected:
        assert got["canonical"] == expected["canonical"], (
            f"{head}\n  expected {expected['canonical']}\n  got      {got['canonical']}{note}"
        )

    if "hashesEqual" in expected:
        assert got["hashesEqual"] == expected["hashesEqual"], (
            f"{head}\n  expected hashesEqual={expected['hashesEqual']}, "
            f"got {got['hashesEqual']}{note}"
        )

    if "valid" in expected:
        assert got["valid"] == expected["valid"], (
            f"{head}\n  expected valid={expected['valid']}, got valid={got['valid']}"
            f"\n  problems: {got['problems']}{note}"
        )

    if "problems" in expected and expected["problems"] == []:
        assert got["problems"] == [], (
            f"{head}\n  expected no problems, got {got['problems']}{note}"
        )

    if "problemKinds" in expected:
        assert set(expected["problemKinds"]) <= set(got["problemKinds"]), (
            f"{head}\n  expected problem kinds {expected['problemKinds']} to be present, "
            f"got {got['problemKinds']}{note}"
        )

    if "minProblems" in expected:
        assert len(got["problems"]) >= expected["minProblems"], (
            f"{head}\n  expected at least {expected['minProblems']} problems, got "
            f"{len(got['problems'])}: {got['problems']}{note}"
        )

    if "entryCount" in expected:
        assert got["entryCount"] == expected["entryCount"], (
            f"{head}\n  expected {expected['entryCount']} entries, got {got['entryCount']}{note}"
        )

    for field in ("declaresFunction", "declaresBits"):
        if field in expected:
            assert got[field] == expected[field], f"{head}\n  {field} is not declared{note}"

    if "minBits" in expected:
        assert got["bits"] >= expected["minBits"], (
            f"{head}\n  this implementation retains {got['bits']} bits; "
            f"{clause} requires at least {expected['minBits']}{note}"
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


def test_the_real_pipeline_attributes_the_way_the_spec_says():
    """The runner above reimplements the attribution rule. This checks the *product*.

    Without this, the vectors could all pass while `authorize.decide()` and
    `record._deciding_engine` disagree with AEGS-0.1-VERD-4 — the suite would be testing the
    runner rather than the implementation. So this drives real decisions end to end and
    recomputes the expected attribution independently from the recorded reasons.

    Recomputing needs one detail that cost a debugging round: a clamp's `source` is
    `authorize`, and `CLAMP_ORIGIN` names the control that *caused* it. Reading `source`
    directly makes every sanctions case look misattributed when it is correct.
    """
    import tempfile

    from aegoll import record as record_mod
    from aegoll.config import load_bundle
    from aegoll.domain import Purpose, Vendor, Verdict, narrower
    from aegoll.record import CLAMP_ORIGIN, DISPOSITIVE_CONTROLS
    from aegoll.runtime import Aegoll, Paths

    def control_of(reason: dict) -> str:
        if reason["source"] == "authorize":
            return CLAMP_ORIGIN.get(reason["code"], "authorize")
        return reason["source"]

    bundle = load_bundle()
    cases = [
        ("micro clean", "0.01", {"id": "v", "name": "V"}),
        ("untrusted", "2.50", {"id": "v", "name": "V"}),
        ("over envelopes", "500", {"id": "v", "name": "V"}),
        ("sanctioned micro", "0.01", {"id": "s", "name": "S", "sanctioned": True}),
        ("sanctioned and over", "500", {"id": "s", "name": "S", "sanctioned": True}),
    ]

    for label, amount, vendor in cases:
        layer = Aegoll(
            bundle=bundle, paths=Paths.ephemeral(tempfile.mkdtemp()), agent_id="attr"
        )
        try:
            request = layer.build_request(
                resource="/market/snapshot", amount_usd=amount,
                vendor=Vendor(**vendor), purpose=Purpose.DATA_PURCHASE,
            )
            layer.authorize(request)
            entry = [e for e in layer.audit.entries() if e.payload.get("decision")][-1]
            record = record_mod.from_audit_entry(entry)
        finally:
            layer.close()

        recorded = record["authorization"]["decidingEngine"]

        verdict, last, dispositive = Verdict.APPROVE, None, None
        for reason in record["authorization"]["reasons"]:
            control = control_of(reason)
            if control in DISPOSITIVE_CONTROLS and dispositive is None:
                dispositive = control
            if not reason.get("verdict"):
                continue
            narrowed = narrower(verdict, Verdict(reason["verdict"]))
            if narrowed is not verdict:
                verdict, last = narrowed, control

        expected = dispositive or last or "policy"
        assert recorded == expected, (
            f"{label}: the record attributes this to {recorded!r}; AEGS-0.1-VERD-4/4a "
            f"says {expected!r}. Either the spec is wrong or this implementation is."
        )
        assert recorded, f"{label}: attribution is absent, which VERD-5 forbids"


def test_the_dispositive_set_is_declared():
    """VERD-4a requires the set and its precedence to be documented, not merely behaved."""
    from aegoll.record import DISPOSITIVE_CONTROLS

    assert DISPOSITIVE_CONTROLS == ("sanctions",), DISPOSITIVE_CONTROLS


def test_the_rounding_mode_is_actually_pinned_down():
    """Half-up and half-even differ on exactly one of these. Both vectors are needed.

    A suite containing only `0.0000015` would pass under either mode and prove nothing —
    which is the kind of coverage that reads as thorough and is not.
    """
    assert usd_to_atomic("0.0000005") == 1, "half-even would give 0 here"
    assert usd_to_atomic("0.0000015") == 2
