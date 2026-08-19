"""Regenerate `redteam/baseline.json`.

    python -m redteam.baseline

The baseline exists so that a *change* in the red-team score has to be deliberate. Both
directions matter and both fail the test until this file is regenerated:

* a defence that regressed is a vulnerability reintroduced,
* a gap that closed is a claim the documentation, the CHANGELOG and the docs site all have to
  stop making -- three places still say structuring is undefended, and they must not go stale
  in the direction of overclaiming.

So the update is a commit, reviewed alongside whatever changed the score, rather than a number
that drifts silently between runs.
"""

from __future__ import annotations

import json
import pathlib

from .runner import report, run_all

PATH = pathlib.Path(__file__).resolve().parent / "baseline.json"

NOTE = (
    "The recorded score. `tests/test_redteam.py` compares a live run against this file and "
    "fails on ANY difference -- a defence that regressed and a gap that closed are both "
    "changes that must be made deliberately, with this file updated in the same commit. "
    "Regenerate with `python -m redteam.baseline`."
)


def build() -> dict:
    data = report(run_all())
    return {
        "suite": data["suite"],
        "note": NOTE,
        "counts": data["counts"],
        "outcomes": {r["id"]: r["outcome"] for r in data["results"]},
        "undefended": data["undefended"],
        "byAccident": data["byAccident"],
    }


def load() -> dict:
    return json.loads(PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    before = load() if PATH.exists() else None
    current = build()
    PATH.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")

    if before is None:
        print(f"wrote {PATH.name}: {current['counts']}")
    elif before["outcomes"] == current["outcomes"]:
        print(f"{PATH.name} unchanged: {current['counts']}")
    else:
        print(f"{PATH.name} UPDATED")
        for aid, was in before["outcomes"].items():
            now = current["outcomes"].get(aid, "<attack removed>")
            if was != now:
                print(f"  {aid}: {was} -> {now}")
        for aid in set(current["outcomes"]) - set(before["outcomes"]):
            print(f"  {aid}: <new attack> -> {current['outcomes'][aid]}")
