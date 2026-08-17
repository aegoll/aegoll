"""The CLI as a contract: every command scriptable, every exit code distinct.

A5.14 and A5.15. The interesting assertions here are the boring ones — `--help` renders,
`--json` parses, and an exit code means one thing. A CLI that signals every failure with
the same number is a CLI nobody can script against, and this tool's failures include
*"the layer said no"*, which is not an error at all.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from aegoll.cli import (
    EXIT_CHAIN,
    EXIT_INVALID,
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_USAGE,
    build_parser,
)

#: Every subcommand. Derived from the parser rather than listed, so a new command is
#: covered the moment it is added — a hand-kept list is how "every command" becomes "most".
COMMANDS = sorted(build_parser()._subparsers._group_actions[0].choices)

#: Commands that need no config, no journal and no arguments to do something sensible.
STANDALONE = ["check", "policies", "policy", "report", "conformance", "audit", "replay"]


def run(*args: str, cwd, expect: int | None = None):
    """Invoke the CLI as a subprocess, the way a user or a CI job does.

    A subprocess rather than calling `main()`: exit codes, argv parsing and the module
    entry point are exactly what these tests are about, and none of them is exercised by
    an in-process call.
    """
    result = subprocess.run(
        [sys.executable, "-m", "aegoll.cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=_env(),
    )
    if expect is not None:
        assert result.returncode == expect, (
            f"aegoll {' '.join(args)} exited {result.returncode}, expected {expect}\n"
            f"{result.stdout}\n{result.stderr}"
        )
    return result


def _env():
    import os
    from pathlib import Path

    import aegoll

    src = str(Path(aegoll.__file__).resolve().parents[1])
    env = dict(os.environ)
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


@pytest.fixture
def project(tmp_path):
    """An initialised project: `aegoll.yaml` plus a starter policy."""
    run("init", cwd=tmp_path, expect=EXIT_OK)
    return tmp_path


# --- A5.15: --help renders for everything --------------------------------


def test_the_command_list_is_not_empty():
    """Guards the parametrized tests below from passing by iterating nothing."""
    assert len(COMMANDS) >= 10, COMMANDS


@pytest.mark.parametrize("command", COMMANDS)
def test_help_renders_for_every_subcommand(command, tmp_path):
    """And without importing an optional extra.

    `--help` is the first thing anyone runs. If it needs `jsonschema` or a model client to
    render, the core is not really installable without them.
    """
    result = run(command, "--help", cwd=tmp_path, expect=EXIT_OK)
    assert "usage:" in result.stdout


def test_top_level_help_renders(tmp_path):
    result = run("--help", cwd=tmp_path, expect=EXIT_OK)
    for command in COMMANDS:
        assert command in result.stdout, f"{command} is not in the help output"


# --- A5.11: --json on every command --------------------------------------


@pytest.mark.parametrize("command", COMMANDS)
def test_every_subcommand_accepts_json(command):
    """A CLI without machine output is a CLI nobody scripts."""
    parser = build_parser()._subparsers._group_actions[0].choices[command]
    assert any("--json" in a.option_strings for a in parser._actions), command


@pytest.mark.parametrize("command", STANDALONE)
def test_json_output_parses(command, project):
    result = run(command, "--json", cwd=project)
    assert result.returncode in (EXIT_OK, EXIT_INVALID, EXIT_CHAIN), result.stderr
    json.loads(result.stdout)  # raises if it is not valid JSON


def test_json_works_before_and_after_the_subcommand(project):
    """Both spellings are what people type, so both must work.

    argparse parses the global flag first and then lets the subparser's default overwrite
    it with `False`, so the pre-command spelling silently stopped working the moment
    per-command flags were added. This is the test that caught it.
    """
    after = run("report", "--json", cwd=project, expect=EXIT_OK)
    before = run("--json", "report", cwd=project, expect=EXIT_OK)
    assert json.loads(after.stdout)["policy"] == json.loads(before.stdout)["policy"]


# --- A5.12: exit codes ---------------------------------------------------


def test_the_exit_codes_are_distinct():
    """Five meanings, five numbers. Overlap makes the CLI unscriptable."""
    codes = [EXIT_OK, EXIT_INVALID, EXIT_REFUSED, EXIT_CHAIN, EXIT_USAGE]
    assert len(set(codes)) == len(codes)
    assert codes == [0, 1, 2, 3, 4]


def test_a_bad_command_exits_usage_not_refused(tmp_path):
    """The distinction that matters most.

    argparse exits 2 by default, and 2 is `refused` here — so without the override a typo
    would read to a script as a governance decision.
    """
    run("nonsense", cwd=tmp_path, expect=EXIT_USAGE)
    run("report", "--bogus-flag", cwd=tmp_path, expect=EXIT_USAGE)


def test_a_refusal_exits_two(project):
    """The layer worked and said no. Not an error."""
    run(
        "decide", "--amount", "5000", "--vendor", "acme", "--resource", "/x",
        cwd=project, expect=EXIT_REFUSED,
    )


def test_an_approval_exits_zero(project):
    run(
        "decide", "--amount", "0.01", "--vendor", "acme", "--resource", "/market/snapshot",
        cwd=project, expect=EXIT_OK,
    )


def test_an_invalid_policy_exits_one(project):
    """`aegoll check` in CI is the point of this code path."""
    policy = project / "policies" / "default.yaml"
    policy.write_text(
        policy.read_text(encoding="utf-8").replace("then: REJECT", "then: NONSENSE", 1),
        encoding="utf-8",
    )
    result = run("check", cwd=project, expect=EXIT_INVALID)
    assert "NONSENSE" in result.stdout


def test_an_invalid_config_exits_one(project):
    (project / "aegoll.yaml").write_text("profile: aegs-99\n", encoding="utf-8")
    result = run("check", cwd=project, expect=EXIT_INVALID)
    assert "aegs-99" in result.stdout


def test_a_broken_chain_exits_three(project):
    """A report whose evidence is broken must not exit 0."""
    run(
        "decide", "--amount", "0.01", "--vendor", "acme", "--resource", "/market/snapshot",
        cwd=project, expect=EXIT_OK,
    )
    journal = project / ".aegoll" / "audit.jsonl"
    assert journal.is_file(), "no journal was written"
    lines = journal.read_text(encoding="utf-8").splitlines()
    tampered = lines[0].replace('"APPROVE"', '"REJECT"')
    assert tampered != lines[0], "the tamper did not change anything"
    journal.write_text("\n".join([tampered, *lines[1:]]) + "\n", encoding="utf-8")

    run("audit", cwd=project, expect=EXIT_CHAIN)
    run("report", cwd=project, expect=EXIT_CHAIN)


# --- the commands do what they say ---------------------------------------


def test_init_then_check_works_from_an_empty_directory(tmp_path):
    """The whole first-run experience, in two commands."""
    result = run("init", cwd=tmp_path, expect=EXIT_OK)
    assert (tmp_path / "aegoll.yaml").is_file()
    assert (tmp_path / "policies" / "default.yaml").is_file()
    assert "aegoll check" in result.stdout, "init should say what to do next"
    run("check", cwd=tmp_path, expect=EXIT_OK)


def test_init_refuses_to_overwrite(project):
    """A policy file decides whether money moves."""
    result = run("init", cwd=project, expect=EXIT_INVALID)
    assert "nothing written" in result.stdout
    run("init", "--force", cwd=project, expect=EXIT_OK)


def test_init_copies_the_policy_rather_than_referencing_the_package(project):
    """A config pointing into site-packages teaches people their policy is not theirs."""
    config = (project / "aegoll.yaml").read_text(encoding="utf-8")
    assert "policies/default.yaml" in config
    assert "site-packages" not in config


def test_check_names_the_active_profile(project):
    result = run("check", cwd=project, expect=EXIT_OK)
    assert "aegs-1" in result.stdout


def test_check_says_so_loudly_when_the_profile_enforces_nothing(project):
    """A user who selected `none` and forgot is otherwise reading a meaningless green tick."""
    path = project / "aegoll.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("profile: aegs-1", "profile: none"),
        encoding="utf-8",
    )
    result = run("check", cwd=project, expect=EXIT_OK)
    assert "NO conformance enforcement" in result.stdout


def test_policy_explain_reads_the_config_not_the_packaged_default(project):
    """It used to fall back to the starter, explaining a policy the agent was not using."""
    policy = project / "policies" / "default.yaml"
    policy.write_text(
        policy.read_text(encoding="utf-8").replace(
            "name: default", "name: mine-not-the-starter", 1
        ),
        encoding="utf-8",
    )
    result = run("policy", "explain", cwd=project, expect=EXIT_OK)
    assert "mine-not-the-starter" in result.stdout


def test_policy_explain_puts_rules_in_evaluation_order(project):
    """Priority order is evaluation order, and the first match is terminal."""
    result = run("policy", "explain", cwd=project, expect=EXIT_OK)
    priorities = [
        int(line.split("[")[1].split("]")[0])
        for line in result.stdout.splitlines()
        if line.strip().startswith("[") and "->" in line
    ]
    assert priorities == sorted(priorities), priorities


def test_report_attributes_each_decision_to_a_control(project):
    """The field that makes a report worth reading."""
    for amount in ("0.01", "2.50", "5000"):
        run(
            "decide", "--amount", amount, "--vendor", "acme",
            "--resource", "/market/snapshot", cwd=project,
        )
    data = json.loads(run("report", "--json", cwd=project, expect=EXIT_OK).stdout)
    by_control = data["spend"]["byAttributedControl"]
    assert by_control, "no attribution at all"
    assert "unattributed" not in by_control, by_control
    for decision in data["decisions"]:
        assert decision["attributedControl"], decision


def test_report_shows_a_per_call_ceiling_as_a_ceiling(project):
    """`per_transaction` never accumulates. Rendering `$0 of $10` beside the cumulative
    windows would read as "nothing was spent", which is false."""
    data = json.loads(run("report", "--json", cwd=project, expect=EXIT_OK).stdout)
    per_tx = [
        e for e in data["envelopes"]["external"] if e["name"] == "per_transaction"
    ]
    assert per_tx and per_tx[0]["cumulative"] is False
    assert per_tx[0]["usedUsd"] is None


def test_report_distinguishes_binding_from_tightest(project):
    """Two questions, two fields. AEGS-0.1-ENV-6.

    `binding` answers "why was this refused" and exists only for a refusal. `tightest`
    answers "what will bite next" and always exists. The first version of the report had
    only `binding` under a heading meaning the second, so an approved decision showed no
    envelope at all — blank precisely when someone was checking headroom.
    """
    data = json.loads(run("report", "--json", cwd=project, expect=EXIT_OK).stdout)
    external = data["envelopes"]["external"]

    assert not [e for e in external if e["binding"]], (
        "nothing has been refused, so no envelope may be reported as binding"
    )
    tightest = [e for e in external if e["tightest"]]
    assert len(tightest) == 1, "exactly one envelope is the tightest, always"

    human = run("report", cwd=project, expect=EXIT_OK).stdout
    assert "(tightest)" in human, "the human report does not surface it either"


def test_report_always_carries_the_chain_caveat(project):
    """VALID without it overstates what a hash chain proves."""
    data = json.loads(run("report", "--json", cwd=project, expect=EXIT_OK).stdout)
    assert "truncation" in data["chain"]["caveat"]
    assert "prefix" in data["chain"]["caveat"]

    human = run("report", cwd=project, expect=EXIT_OK).stdout
    assert "truncation" in human


def test_decide_uses_the_pack_the_config_names(project):
    """The worst bug this tool can have, and it was live.

    `_aegl()` called `load_bundle()` with no path, which resolves to the *packaged* starter
    pack. So a user could edit `policies/default.yaml`, watch `aegoll check` confirm the edit by
    name and content hash, and then have `aegoll decide` govern the agent by entirely different
    numbers — with nothing reporting a conflict, because nothing knew there was one.

    A governance layer quietly enforcing a policy other than the one on disk is worse than one
    that fails to start.

    The edit here drops the per-transaction ceiling to half a cent, so `$1.00` must breach it.
    The assertion is on `binding`, not on the verdict: both packs produce a non-approval for
    $1.00 by different routes, and only the binding envelope distinguishes which pack was read.
    """
    pack = project / "policies" / "default.yaml"
    pack.write_text(
        pack.read_text(encoding="utf-8")
        .replace('per_transaction_usd: "10"', 'per_transaction_usd: "0.005"')
        .replace("name: default", "name: edited-by-the-user"),
        encoding="utf-8",
    )

    check = run("check", cwd=project, expect=EXIT_OK).stdout
    assert "edited-by-the-user" in check, "check does not even see the edit"

    data = json.loads(
        run("decide", "--amount", "1.00", "--vendor", "acme", "--resource", "/r",
            "--json", cwd=project).stdout
    )
    assert data["budget"]["ok"] is False, (
        "the $0.005 ceiling was not applied, so `decide` read the packaged pack rather than "
        "the project's -- the config's `policy:` setting is being ignored"
    )
    assert data["budget"]["binding"] == "per_transaction", data["budget"]


def test_every_command_agrees_which_pack_is_in_force(project):
    """`check` and `report` must name the same pack. Two commands reading two policies from one
    config is how the defect above stayed invisible: each was self-consistent."""
    pack = project / "policies" / "default.yaml"
    pack.write_text(
        pack.read_text(encoding="utf-8").replace("name: default", "name: one-true-pack"),
        encoding="utf-8",
    )

    checked = run("check", cwd=project, expect=EXIT_OK).stdout
    reported = json.loads(run("report", "--json", cwd=project, expect=EXIT_OK).stdout)

    assert "one-true-pack" in checked
    assert reported["policy"]["name"] == "one-true-pack", (
        f"check and report disagree: report says {reported['policy']['name']!r}"
    )


def test_an_explicit_policy_flag_still_wins(project):
    """Precedence: `--policy` over the config, so a pack can be inspected without editing
    config."""
    other = project / "other.yaml"
    other.write_text(
        (project / "policies" / "default.yaml")
        .read_text(encoding="utf-8")
        .replace("name: default", "name: explicitly-chosen"),
        encoding="utf-8",
    )
    data = json.loads(
        run("--policy", str(other), "report", "--json", cwd=project, expect=EXIT_OK).stdout
    )
    assert data["policy"]["name"] == "explicitly-chosen"


def test_the_configured_journal_path_is_honoured(project):
    """`evidence: journal:` was a setting that did nothing — no reader anywhere.

    A user could point it at `logs/spend.jsonl`, get no error, and find their evidence in
    `./.aegoll` instead. Honouring the *filename* matters as much as the directory: writing
    `audit.jsonl` into the named folder would satisfy half the setting, and the file they asked
    for would still never appear.
    """
    config = project / "aegoll.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "journal: .aegoll/audit.jsonl", "journal: logs/spend.jsonl"
        ),
        encoding="utf-8",
    )

    run("decide", "--amount", "0.01", "--vendor", "acme", "--resource", "/r", cwd=project)

    assert (project / "logs" / "spend.jsonl").is_file(), (
        "the configured journal path was ignored; evidence went somewhere else"
    )
    assert not (project / ".aegoll" / "audit.jsonl").exists(), (
        "evidence was also written to the default path -- two journals means neither is the "
        "record"
    )


def test_report_html_writes_a_self_contained_file(project):
    """A10a.2. The end-to-end path, as a user runs it.

    `tests/test_html.py` checks the renderer against a constructed `Report`; this checks that
    the flag reaches it, that a real layer produces a page, and that the file on disk is the
    whole artifact — a renderer that was correct but wired to nothing would pass the other
    suite entirely.
    """
    run("decide", "--amount", "0.01", "--vendor", "acme", "--resource", "/r", cwd=project)
    out = project / "spend.html"
    run("report", "--html", "-o", str(out), cwd=project, expect=EXIT_OK)

    page = out.read_text(encoding="utf-8")
    assert page.startswith("<!DOCTYPE html>")
    assert "https://" not in page and "http://" not in page
    assert "Attributed control" in page
    assert "truncation" in page, "the chain caveat must survive into the page"


def test_report_html_defaults_to_stdout(project):
    """So it pipes. `aegoll report --html > spend.html` should work without a flag, because
    that is what everything else on a shell does."""
    page = run("report", "--html", cwd=project, expect=EXIT_OK).stdout
    assert page.lstrip().startswith("<!DOCTYPE html>")
    assert "</html>" in page


def test_report_refuses_html_and_json_together(project):
    """Two renderings of the same report. Silently honouring one teaches the user that the
    flag they asked for does nothing."""
    result = run("report", "--html", "--json", cwd=project, expect=EXIT_USAGE)
    assert "pick one" in result.stderr


def test_output_without_html_is_a_usage_error(project):
    """`-o` writes a rendered page. Accepting it for the terminal renderer and ignoring it
    would silently discard the user's file."""
    result = run("report", "-o", "x.html", cwd=project, expect=EXIT_USAGE)
    assert "--html" in result.stderr


def test_report_html_shows_the_rules_not_just_a_count(project):
    """A count says the pack is not empty. The question before a run is which rule will stop
    me, and that needs the rules in evaluation order."""
    page = run("report", "--html", cwd=project, expect=EXIT_OK).stdout
    data = json.loads(run("report", "--json", cwd=project, expect=EXIT_OK).stdout)

    rules = data["policy"]["ruleList"]
    assert len(rules) == data["policy"]["rules"], "the list and the count disagree"
    assert [r["priority"] for r in rules] == sorted(r["priority"] for r in rules), (
        "rules are not in evaluation order"
    )
    for rule in rules:
        assert rule["id"] in page, f"{rule['id']} is missing from the page"
        assert rule["condition"], f"{rule['id']} has no readable condition"


def test_report_separates_the_two_channels(project):
    """They never share an envelope: different currency, counterparty, failure mode."""
    data = json.loads(run("report", "--json", cwd=project, expect=EXIT_OK).stdout)
    assert set(data["envelopes"]) == {"external", "internal"}
    external = {e["name"]: e["limitUsd"] for e in data["envelopes"]["external"]}
    internal = {e["name"]: e["limitUsd"] for e in data["envelopes"]["internal"]}
    assert external["daily"] != internal["daily"], "the channels share a limit"


def test_report_carries_both_version_lines(project):
    """A record that does not say which spec and which implementation produced it cannot
    be audited later."""
    data = json.loads(run("report", "--json", cwd=project, expect=EXIT_OK).stdout)
    assert data["versions"]["aegoll"]
    assert data["versions"]["aegs"]


def test_conformance_scores_journalled_records(project):
    run(
        "decide", "--amount", "0.01", "--vendor", "acme", "--resource", "/market/snapshot",
        cwd=project, expect=EXIT_OK,
    )
    data = json.loads(run("conformance", "--json", cwd=project, expect=EXIT_OK).stdout)
    assert data["profile"] == "aegs-1"
    assert data["records"] >= 1
    assert data["conformant"] == data["records"]


def test_conformance_with_no_records_is_not_a_failure(project):
    """Nothing to score is not the same as failing to score."""
    result = run("conformance", cwd=project, expect=EXIT_OK)
    assert "nothing to score" in result.stdout


def test_bench_reports_zero_inference_cost(project):
    """The central performance claim, measured on the caller's hardware."""
    data = json.loads(run("bench", "-n", "100", "--json", cwd=project, expect=EXIT_OK).stdout)
    assert data["decisions"] == 100
    assert data["inferenceCostUsd"] == "0.000000"
    assert data["latencyP50Us"] > 0


def test_dry_run_journals_nothing(project):
    """`--dry-run` answers "what would happen" without polluting the evidence."""
    run(
        "decide", "--amount", "0.01", "--vendor", "acme", "--resource", "/market/snapshot",
        "--dry-run", cwd=project, expect=EXIT_OK,
    )
    data = json.loads(run("report", "--json", cwd=project, expect=EXIT_OK).stdout)
    assert data["spend"]["decisions"] == 0, "a dry run was journalled"


def test_the_read_only_commands_do_not_write(project):
    """`report`, `audit`, `conformance` and `policy explain` read. Nothing else."""
    run(
        "decide", "--amount", "0.01", "--vendor", "acme", "--resource", "/market/snapshot",
        cwd=project, expect=EXIT_OK,
    )
    journal = project / ".aegoll" / "audit.jsonl"
    before = journal.read_bytes()
    for command in ("report", "audit", "conformance", "policy"):
        run(command, cwd=project)
    assert journal.read_bytes() == before, "a read-only command wrote to the journal"
