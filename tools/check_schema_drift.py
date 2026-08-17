"""Fail if a vendored AEGS schema has drifted from the standard at its pinned commit.

A validator running against a stale schema is worse than one that fails loudly: it
reports conformance against a document nobody is holding it to. This drift is not
hypothetical — within hours of the first pin, the standard rewrote every `$id` and the
vendored copies here silently kept a dead one. Nothing failed.

    python tools/check_schema_drift.py             # compare against the pinned commit
    python tools/check_schema_drift.py --refresh   # update the copies and the pin

Reads the pin from `src/aegoll/_schemas/PROVENANCE.txt` and fetches only that commit's
copy, so raising the pin stays a deliberate act rather than a side effect of the
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
SCHEMAS = HERE / "src" / "aegoll" / "_schemas"
PROVENANCE = SCHEMAS / "PROVENANCE.txt"
API = "https://api.github.com/repos/{repo}/contents/{path}{name}?ref={commit}"
RAW = "https://raw.githubusercontent.com/{repo}/{commit}/{path}{name}"


def token() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None


def read_pin() -> tuple[str, str, str]:
    """(repo, commit, path) from the provenance file. The file is the single source."""
    text = PROVENANCE.read_text(encoding="utf-8")

    def field(name: str) -> str:
        match = re.search(rf"^\s*{name}:\s*(\S+)\s*$", text, re.M)
        if not match:
            raise SystemExit(f"{PROVENANCE}: no `{name}:` field. The pin must be explicit.")
        return match.group(1)

    return field("repo"), field("commit"), field("path")


def vendored() -> list[Path]:
    return sorted(SCHEMAS.glob("*.json"))


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh", action="store_true",
        help="overwrite the vendored copies from the pinned commit",
    )
    args = parser.parse_args()

    repo, commit, path = read_pin()
    files = vendored()
    if not files:
        raise SystemExit(f"{SCHEMAS}: no vendored schemas to check")

    print(f"pinned to {repo}@{commit[:7]} {path}")

    drifted, missing, refreshed = [], [], []
    for local in files:
        try:
            remote = fetch(repo, commit, path, local.name)
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
                missing.append(local.name)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"skipped: no network ({exc.reason if hasattr(exc, 'reason') else exc})")
            return 0

        if canonical(remote) == canonical(local.read_text(encoding="utf-8")):
            print(f"  ok      {local.name}")
        elif args.refresh:
            local.write_text(remote, encoding="utf-8")
            refreshed.append(local.name)
            print(f"  updated {local.name}")
        else:
            drifted.append(local.name)
            print(f"  DRIFT   {local.name}")

    if missing:
        print(
            "\nnot present at the pinned commit: " + ", ".join(missing) +
            "\nEither the pin is wrong or the schema was renamed in the standard. "
            "Both need a human."
        )
        return 1

    if refreshed:
        print(
            f"\nrefreshed {len(refreshed)}. The pin in PROVENANCE.txt is unchanged and "
            "still names the commit these came from -- raise it deliberately, in its own "
            "commit, so the diff shows what the standard changed."
        )
        return 0

    if drifted:
        print(
            f"\n{len(drifted)} vendored schema(s) differ from {repo}@{commit[:7]}.\n"
            "A validator running against a stale schema reports conformance against a "
            "document nobody is holding it to.\n"
            "Fix by refreshing the copies (`--refresh`), never by editing a file in "
            "_schemas/ -- a schema change belongs in the standard."
        )
        return 1

    print(f"\n{len(files)} vendored schema(s) match the pin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
