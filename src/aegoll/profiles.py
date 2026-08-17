"""Profile enforcement: which controls must be exercised, and whether they were.

A **profile** says which controls must exist and what evidence must be emitted. A
**policy pack** says what the rules are. This module handles the first; `validate.py`
handles the second.

**A profile never changes a verdict.** Nothing here is reachable from the decision path,
and no engine imports this module — a test enforces that. Verdicts come from policy and
the engines; a profile is about *evidence completeness*. If selecting a profile could
tighten or loosen what gets approved, two implementations at the same profile could
disagree on outcomes while both claiming conformance, and the profile would have become a
second, weaker policy language.

So this module answers one question, after the fact: **given a Decision Record, were the
controls this profile requires actually exercised, and does the record say so?**

The manifests are vendored from the standard (`_profiles/PROVENANCE.txt`). They are data
here, not code — the requirement levels, the controls and the record paths all come from
the file. Adding a control upstream needs no change in this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .states import MISSING as _MISSING
from .states import classify_state, dig as _dig, is_evidence as _is_evidence  # noqa: F401

#: Requirement levels, strictly ordered. `extends` may move a control up, never down —
#: enforced in the standard's own CI, not here, because it is a property of the manifests
#: rather than of any implementation.
RANK = {"OPTIONAL": 0, "MUST_RECORD": 1, "MUST_EXERCISE": 2}


def _profile_dir() -> Path:
    """Vendored manifests, as package data.

    `importlib.resources`, never `Path(__file__).parents[n]`. See PLAN.md F-A1 for the
    eleven times that mattered.
    """
    return Path(str(resources.files(__package__) / "_profiles")).resolve()


def available_profiles() -> list[str]:
    return sorted(p.stem for p in _profile_dir().glob("*.json"))


@dataclass(frozen=True)
class Requirement:
    control: str
    requirement: str
    record_path: str | None = None
    note: str | None = None

    @property
    def required(self) -> bool:
        return RANK[self.requirement] > 0


@dataclass(frozen=True)
class Finding:
    """One control that a profile required and a record did not deliver."""

    control: str
    requirement: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"[{self.requirement}] {self.control}: {self.message}"

    def as_dict(self) -> dict[str, str]:
        return {
            "control": self.control,
            "requirement": self.requirement,
            "where": self.where,
            "message": self.message,
        }


@dataclass(frozen=True)
class Assessment:
    """What a profile makes of one Decision Record.

    This is the `ComplianceAssessment` control's content: controls exercised *against a
    named profile*, rather than a list of controls the implementation happens to have.
    The distinction matters — "we run a trust engine" is a capability claim, while "this
    decision exercised TrustAssessment under aegs-2" is a checkable statement about a
    specific action.
    """

    profile: str
    exercised: tuple[str, ...]
    absent: tuple[str, ...]
    findings: tuple[Finding, ...]

    @property
    def conformant(self) -> bool:
        return not self.findings

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "conformant": self.conformant,
            "controlsExercised": list(self.exercised),
            "controlsAbsent": list(self.absent),
            "findings": [f.as_dict() for f in self.findings],
        }


@dataclass(frozen=True)
class Profile:
    """A conformance contract, loaded from a vendored manifest."""

    id: str
    title: str | None
    description: str | None
    extends: str | None
    requirements: tuple[Requirement, ...]
    non_conformant: tuple[str, ...]
    source: Path | None = None

    @classmethod
    def load(cls, name: str) -> "Profile":
        path = _profile_dir() / f"{name}.json"
        if not path.is_file():
            raise ConfigError(
                f"unknown profile {name!r}. Available: {', '.join(available_profiles())}"
            )
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            id=raw["profile"],
            title=raw.get("title"),
            description=raw.get("description"),
            extends=raw.get("extends"),
            requirements=tuple(
                Requirement(
                    control=c["control"],
                    requirement=c["requirement"],
                    record_path=c.get("recordPath"),
                    note=c.get("note"),
                )
                for c in raw["controls"]
            ),
            non_conformant=tuple(raw.get("nonConformant") or []),
            source=path,
        )

    # --- reading the contract --------------------------------------------

    def requirement_for(self, control: str) -> str:
        for req in self.requirements:
            if req.control == control:
                return req.requirement
        return "OPTIONAL"  # absent from the list means optional by omission

    def required_controls(self) -> tuple[Requirement, ...]:
        return tuple(r for r in self.requirements if r.required)

    def enforces(self) -> bool:
        """False for `none`, which is an opt-out rather than an empty contract."""
        return bool(self.required_controls())

    # --- scoring a record ------------------------------------------------

    def assess(self, record: dict[str, Any]) -> Assessment:
        """Score one Decision Record against this profile.

        Reports findings; never raises, and never changes anything.

        The two requirement levels ask different questions, and conflating them was a real
        bug in the first version of this method:

        * **MUST_EXERCISE** — did the control *run*? Needs a value that is evidence.
        * **MUST_RECORD** — does the record *state the position*? Needs the key to be
          **present**, and an explicit `null` satisfies it.

        `intentId: null` is the record saying *no intent was declared* — a recorded state,
        and exactly what the four-state rule asks for. Scoring that as a failure punishes
        an implementation for being honest, and would push the next one toward omitting
        the key instead. A **missing key** is the failure; a null one is an answer.
        """
        exercised: list[str] = []
        absent: list[str] = []
        findings: list[Finding] = []

        for req in self.requirements:
            found = _dig(record, req.record_path) if req.record_path else _MISSING
            stated = found is not _MISSING
            ran = stated and _is_evidence(found)

            if ran:
                exercised.append(req.control)
            else:
                absent.append(req.control)

            if not req.required:
                continue

            if req.record_path is None:
                findings.append(Finding(
                    req.control, req.requirement, "<profile>",
                    "required by the profile but the profile names no recordPath, so it "
                    "cannot be checked. This is a defect in the profile, not the record.",
                ))
                continue

            if req.requirement == "MUST_RECORD":
                # Presence, not a value. An explicit null is the record stating a position.
                if not stated:
                    findings.append(Finding(
                        req.control, req.requirement, req.record_path,
                        f"no {req.record_path!r} key at all. The profile does not require "
                        "this control to run, but it does require the record to say so — "
                        "an explicit null would satisfy it, an absent key does not.",
                    ))
                continue

            if ran:
                continue
            if not stated:
                findings.append(Finding(
                    req.control, req.requirement, req.record_path,
                    f"nothing at {req.record_path!r}. The profile requires this control "
                    "and the record does not show it ran.",
                ))
            else:
                findings.append(Finding(
                    req.control, req.requirement, req.record_path,
                    f"{req.record_path!r} is present but empty, null, or explicitly "
                    "not-run. Absent, not-run, unknown and zero are four different "
                    "things, and none of them is evidence that a control ran.",
                ))

        return Assessment(
            profile=self.id,
            exercised=tuple(exercised),
            absent=tuple(absent),
            findings=tuple(findings),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.id,
            "title": self.title,
            "extends": self.extends,
            "enforces": self.enforces(),
            "controls": [
                {
                    "control": r.control,
                    "requirement": r.requirement,
                    "recordPath": r.record_path,
                }
                for r in self.requirements
            ],
            "nonConformant": list(self.non_conformant),
        }








__all__ = [
    "RANK",
    "Assessment",
    "Finding",
    "Profile",
    "Requirement",
    "available_profiles",
]
