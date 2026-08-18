"""The CI configuration is code, and it gets the same treatment.

Preparing 0.1.1, `release.yml` was given a second `inputs:` block under `workflow_dispatch`
because the first thirty lines of the file end at `workflow_dispatch:` and it looked as though
nothing followed. `dry_run` was already declared there.

The interesting part is what GitHub did with the duplicate. It did not report a configuration
error. It stopped resolving the workflow, which meant the `on: push: tags: ["v*"]` filter was
never applied: the release workflow fired on a push to `main`, failed at once, and appeared in
the run list as `.github/workflows/release.yml` rather than as `release`. The workflow *name*
being replaced by its *filename* was the only visible difference between "your YAML is invalid"
and "your release failed for the reason you were already expecting" -- and one of those was
being actively investigated at the time.

So: parse every workflow here with duplicate keys made fatal, and assert the properties a
release depends on. A YAML file that only fails on GitHub is a file with no test.
"""

from __future__ import annotations

import pathlib

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML is a runtime dependency; absence is a packaging bug")

WORKFLOWS = sorted((pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows").glob("*.yml"))

#: Asserted, not assumed. A glob that resolves to nothing makes every test below pass by
#: checking zero files -- the failure mode this repository calls F-C1.
assert WORKFLOWS, "no workflow files found; the layout changed"


class _NoDuplicates(yaml.SafeLoader):
    """`yaml.safe_load` silently keeps the last of two identical keys. GitHub does not."""


def _mapping_without_duplicates(loader, node, deep=False):
    seen: set = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise ValueError(f"duplicate key {key!r} at line {key_node.start_mark.line + 1}")
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_NoDuplicates.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping_without_duplicates
)


def _load(path: pathlib.Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), _NoDuplicates)


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_a_workflow_parses_and_declares_no_key_twice(path):
    doc = _load(path)
    assert isinstance(doc, dict), f"{path.name} is not a mapping"
    assert doc.get("jobs"), f"{path.name} declares no jobs"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_job_can_actually_run(path):
    """A job with no steps and no `uses` is a name in a list, not a check."""
    for name, job in _load(path)["jobs"].items():
        assert job.get("steps") or job.get("uses"), f"{path.name}: job {name} does nothing"


def test_the_release_workflow_only_publishes_on_a_tag():
    """The property the duplicate key silently removed.

    With `on:` unresolvable, the tag filter went away and the workflow ran on branch pushes.
    Nothing here can detect *that* -- GitHub's parser is the thing that broke -- but the filter
    being present and correct is checkable, and its absence is what made a branch push able to
    reach a job whose whole purpose is uploading to PyPI.
    """
    release = next((p for p in WORKFLOWS if p.name == "release.yml"), None)
    assert release is not None, "release.yml is gone"
    doc = _load(release)

    # `on` is the YAML 1.1 boolean `true`, which is why this reads oddly. PyYAML resolves the
    # bare word before the key ever reaches us.
    triggers = doc.get("on", doc.get(True))
    assert triggers, "release.yml declares no triggers"
    assert triggers["push"]["tags"] == ["v*"], f"tag filter changed: {triggers['push']}"
    assert "branches" not in triggers["push"], "a branch push must never reach the publish job"


def test_the_publish_job_holds_no_token_and_can_mint_one():
    """A9.9, as a test rather than a comment.

    `password:` reappearing here means trusted publishing has been quietly replaced by a
    long-lived token. `id-token: write` disappearing means the OIDC exchange cannot happen at
    all, which is the failure this release exists to fix.
    """
    release = next(p for p in WORKFLOWS if p.name == "release.yml")
    text = release.read_text(encoding="utf-8")
    publish = _load(release)["jobs"]["publish"]

    assert publish["permissions"]["id-token"] == "write"
    assert publish.get("environment") == "pypi"
    for step in publish["steps"]:
        assert "password" not in (step.get("with") or {}), "the publish step carries a token"
    assert "PYPI_API_TOKEN" not in text
    assert "secrets." not in text, "a release must need no secret"


def test_the_setup_instructions_name_the_organisation_as_the_owner():
    """The defect that made every attempt fail with an opaque permissions error.

    The comment said `Owner: tesoro`. The owner of `github.com/aegoll/tesoro` is `aegoll`; the
    repository is `tesoro`. Anyone following the instruction configured a publisher that could
    never match, and PyPI's refusal names none of the four fields. Checked here because the
    instruction is the only place those four values are written down, and being confidently
    wrong is worse than being absent.
    """
    release = next(p for p in WORKFLOWS if p.name == "release.yml")
    setup = [
        line for line in release.read_text(encoding="utf-8").splitlines()
        if line.lstrip("# ").startswith(("Owner:", "Repository:", "Workflow name:", "Environment:"))
    ]
    assert len(setup) == 4, f"the four publisher fields are no longer all documented: {setup}"

    values = {
        line.lstrip("# ").split(":", 1)[0]: line.split(":", 1)[1].split("<--")[0].strip()
        for line in setup
    }
    assert values["Owner"] == "aegoll", f"Owner must be the organisation, got {values['Owner']!r}"
    assert values["Repository"] == "tesoro"
    assert values["Workflow name"] == "release.yml"
    assert values["Environment"] == "pypi"


def test_a_release_must_carry_both_a_wheel_and_an_sdist():
    """Twice now, a release has gone out as a wheel with no tarball.

    0.1.0: uploaded by hand, and the hand named one file. 0.1.1: the publish step uploaded the
    wheel and failed before the tarball. `pip install` works in both cases, which is exactly why
    neither looked wrong -- the people who need the sdist are the ones doing a source build,
    packaging for a distribution, or auditing what they actually run.

    A version's files cannot be replaced once published, so a half-published release is a worse
    position than a failed one. The guard cannot make the upload atomic; it fails the run before
    a credential is minted if the build did not produce both.
    """
    release = next(p for p in WORKFLOWS if p.name == "release.yml")
    steps = _load(release)["jobs"]["build"]["steps"]

    names = [s.get("name", "") for s in steps]
    guard = next((i for i, n in enumerate(names) if "Both artifacts exist" in n), None)
    assert guard is not None, f"the wheel+sdist guard is gone from build: {names}"

    body = steps[guard]["run"]
    assert "*.whl" in body and "*.tar.gz" in body, "the guard counts only one kind of artifact"

    upload = next(i for i, s in enumerate(steps) if "upload-artifact" in str(s.get("uses", "")))
    assert guard < upload, "the guard must run before dist/ is handed to the publish job"
