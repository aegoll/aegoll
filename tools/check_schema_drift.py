"""Fail if vendored AEGS content has drifted from the standard at its pinned commit.

A validator running against a stale schema is worse than one that fails loudly: it
reports conformance against a document nobody is holding it to. This drift is not
hypothetical — within hours of the first pin, the standard rewrote every `$id` and the
vendored copies here silently kept a dead one. Nothing failed.

    python tools/check_schema_drift.py             # compare against the pinned commit
    python tools/check_schema_drift.py --refresh   # update the copies and the pin

Covers `_schemas/`, `_profiles/` and `tests/_vectors/`, each with its own `PROVENANCE.txt`
and its own pin,
because they are separate artifacts of the standard and change for different reasons. A
stale schema makes validation wrong; a stale profile makes a *conformance claim* wrong.

Reads each pin and fetches only that commit's copy, so raising the pin stays a deliberate act rather than a side effect of the
standard moving. Needs network; skips cleanly without one, because a broken build on a
train is not a useful signal.

While the standard's repository is **private**, reads need a token in `GITHUB_TOKEN` or
`GH_TOKEN`. Without one this skips rather than failing: an unauthenticated 404 cannot be
told apart from a deleted file, and guessing between those two is how a check starts
lying. It goes away on its own once `aegs` is public.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]

#: Every directory of vendored standard content. Each carries its own PROVENANCE.txt with
#: its own pin, so schemas and profiles can be raised independently — they are separate
#: artifacts of the standard and change for different reasons.
VENDORED = (
    HERE / "src" / "tesoro" / "_schemas",
    HERE / "src" / "tesoro" / "_profiles",
    HERE / "tests" / "_vectors",
)
API = "https://api.github.com/repos/{repo}/contents/{path}{name}?ref={commit}"
RAW = "https://raw.githubusercontent.com/{repo}/{commit}/{path}{name}"


def token() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None


def read_pin(provenance: Path) -> tuple[str, str, str]:
    """(repo, commit, path) from a provenance file. The file is the single source."""
    text = provenance.read_text(encoding="utf-8")

    def field(name: str) -> str:
        match = re.search(rf"^\s*{name}:\s*(\S+)\s*$", text, re.M)
        if not match:
            raise SystemExit(f"{provenance}: no `{name}:` field. The pin must be explicit.")
        return match.group(1)

    return field("repo"), field("commit"), field("path")


def vendored(directory: Path) -> list[Path]:
    """Every vendored JSON file, recursively.

    Recursive because the vectors are organised into per-family subdirectories, unlike the flat
    `_schemas/` and `_profiles/`. `schema.json` is skipped: it is the standard's *validator* for
    vector files rather than vendored content this package reads, and `check_vectors.py` in the
    standard is what holds it to account.

    One request per file, which is 151 for the vectors. Tolerable for a job that runs on push,
    and worth replacing with a single recursive tree-API call if it ever becomes the slow part —
    compare git blob SHAs, and fetch content only for the ones that differ.
    """
    return sorted(p for p in directory.rglob("*.json") if p.name != "schema.json")


def fetch(repo: str, commit: str, path: str, name: str) -> str:
    """The pinned commit's copy of one schema.

    Authenticated reads go through the contents API, which works for a private repo;
    unauthenticated ones use raw.githubusercontent, which does not.
    """
    auth = token()
    if auth:
        request = urllib.request.Request(
            API.format(repo=repo, commit=commit, path=path, name=name),
            headers={
                "Authorization": f"Bearer {auth}",
                "Accept": "application/vnd.github.raw",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    else:
        request = urllib.request.Request(
            RAW.format(repo=repo, commit=commit, path=path, name=name)
        )
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
        return response.read().decode("utf-8")


def canonical(text: str) -> str:
    """Compare meaning, not whitespace or line endings.

    Windows checkouts rewrite line endings, and a CRLF difference is not drift. A
    changed field is.
    """
    return json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"))


def _name(local: Path, directory: Path) -> str:
    """A vendored file's name relative to its directory, so `arithmetic/negative.json` reads
    as itself rather than as `negative.json` in a list of nine files called that."""
    return local.relative_to(directory).as_posix()


def check(directory: Path, *, refresh: bool) -> int:
    """One vendored directory against its own pin."""
    provenance = directory / "PROVENANCE.txt"
    if not provenance.is_file():
        print(f"{directory.name}: no PROVENANCE.txt. Vendored data with no stated source "
              "is unmaintainable — a reader cannot tell what it should equal.")
        return 1

    repo, commit, path = read_pin(provenance)
    files = vendored(directory)
    if not files:
        print(f"{directory.name}: nothing to check")
        return 1

    print(f"{directory.name}: pinned to {repo}@{commit[:7]} {path}")

    drifted, missing, refreshed = [], [], []
    args_refresh = refresh
    for local in files:
        try:
            remote = fetch(repo, commit, path, local.relative_to(directory).as_posix())
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 404) and not token():
                print(
                    f"skipped: {repo} is not readable without a token "
                    f"(HTTP {exc.code}).\n"
                    "Set GITHUB_TOKEN or GH_TOKEN. Skipping rather than failing on "
                    "purpose: an unauthenticated 404 cannot be told apart from a "
                    "deleted file, and guessing between those is how a check starts "
                    "lying."
                )
                return 0
            if exc.code == 404:
                missing.append(_name(local, directory))
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"skipped: no network ({exc.reason if hasattr(exc, 'reason') else exc})")
            return 0

        if canonical(remote) == canonical(local.read_text(encoding="utf-8")):
            print(f"  ok      {_name(local, directory)}")
        elif args_refresh:
            local.write_text(remote, encoding="utf-8")
            refreshed.append(_name(local, directory))
            print(f"  updated {_name(local, directory)}")
        else:
            drifted.append(_name(local, directory))
            print(f"  DRIFT   {_name(local, directory)}")

    if missing:
        print(
            "  not present at the pinned commit: " + ", ".join(missing) +
            "\n  Either the pin is wrong or the file was renamed in the standard. "
            "Both need a human."
        )
        return 1

    if refreshed:
        print(
            f"  refreshed {len(refreshed)}. The pin in PROVENANCE.txt is unchanged and "
            "still names the commit these came from -- raise it deliberately, in its own "
            "commit, so the diff shows what the standard changed."
        )
        return 0

    if drifted:
        print(
            f"  {len(drifted)} file(s) differ from {repo}@{commit[:7]}.\n"
            f"  Running against a stale copy reports conformance against a document "
            "nobody is holding it to.\n"
            f"  Fix by refreshing (`--refresh`), never by editing a file in "
            f"{directory.name}/ -- that content belongs to the standard."
        )
        return 1

    print(f"  {len(files)} file(s) match the pin")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh", action="store_true",
        help="overwrite the vendored copies from their pinned commits",
    )
    args = parser.parse_args()

    worst = 0
    for directory in VENDORED:
        if not directory.is_dir():
            print(f"{directory.name}: absent, skipped")
            continue
        worst = max(worst, check(directory, refresh=args.refresh))
    return worst


if __name__ == "__main__":
    sys.exit(main())
