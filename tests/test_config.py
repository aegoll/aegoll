"""Config and policy packs: rejected at load, or not loaded.

The invariant these defend is that **policy packs are data, never code** — a security
boundary rather than a style preference, because a shared or downloaded pack that can
execute is remote code execution wearing a governance hat.

The prototype had the right vocabulary in the wrong place. `COMPARATORS` was already a
fixed tuple with no `eval`, but it was checked inside `policy.evaluate()` — so a malformed
rule only raised if a request reached it, and **a rule that never matches never
validates**. These tests exist because "it would have failed eventually" is not a
guarantee when the eventual failure happens next to a wallet.
"""

from __future__ import annotations

import json

import pytest

from tesoro.config import (
    PACK_SUFFIXES,
    available_bundles,
    load_bundle,
    parse_pack_text,
)
from tesoro.errors import ConfigError, PolicyError
from tesoro.settings import Config, find_config, validate_config
from tesoro.validate import format_problems, has_errors, known_facts, validate_pack

MINIMAL = {
    "version": 1,
    "name": "t",
    "rules": [{"id": "only", "priority": 1, "when": {"channel": "external"}, "then": "REVIEW"}],
}


def write(path, data, *, as_json: bool = False):
    import yaml

    text = json.dumps(data, indent=2) if as_json else yaml.safe_dump(data, sort_keys=False)
    path.write_text(text, encoding="utf-8")
    return path


# --- the packaged starters ------------------------------------------------


def test_the_packaged_starters_are_valid():
    """If a shipped pack does not validate, every other test here is theatre."""
    for path in available_bundles():
        problems = validate_pack(parse_pack_text(path.read_text("utf-8"), source=path.name))
        assert not has_errors(problems), f"{path.name}:\n{format_problems(problems)}"


def test_discovery_reports_one_pack_per_name():
    """Both syntaxes ship. Discovery must not report `strict` twice.

    Two entries with one name make `--policy strict` ambiguous, and something downstream
    would quietly pick whichever sorted first — the AEGS conformance suite selects a pack
    by stem exactly that way.
    """
    found = available_bundles()
    stems = [p.stem for p in found]
    assert len(stems) == len(set(stems)), [p.name for p in found]
    assert {"default", "strict"} <= set(stems)


@pytest.mark.parametrize("stem", ["default", "strict"])
def test_yaml_and_json_produce_an_identical_bundle(stem):
    """One schema, two syntaxes — checked, not asserted.

    The content hash is over the parsed structure, so an identical hash means the two
    files really are the same policy rather than merely looking similar.
    """
    directory = available_bundles()[0].parent
    yaml_bundle = load_bundle(directory / f"{stem}.yaml")
    json_bundle = load_bundle(directory / f"{stem}.json")
    assert yaml_bundle.hash == json_bundle.hash
    assert len(yaml_bundle.rules) == len(json_bundle.rules)
    assert yaml_bundle.treasury == json_bundle.treasury


def test_json_is_accepted_wherever_yaml_is(tmp_path):
    assert ".json" in PACK_SUFFIXES
    pack = write(tmp_path / "p.json", MINIMAL, as_json=True)
    assert load_bundle(pack).name == "t"


# --- a bad pack is rejected at load --------------------------------------


def test_an_unknown_comparator_is_rejected_at_load(tmp_path):
    """Not at first match. This is the whole point of the module."""
    bad = dict(MINIMAL)
    bad["rules"] = [{"id": "r", "when": {"amount_usd": {"gte_": 1}}, "then": "REJECT"}]
    with pytest.raises(PolicyError) as exc:
        load_bundle(write(tmp_path / "p.yaml", bad))
    assert "gte_" in str(exc.value)
    assert "no expression evaluator" in str(exc.value)


def test_an_unknown_verdict_is_rejected(tmp_path):
    bad = dict(MINIMAL)
    bad["rules"] = [{"id": "r", "when": {}, "then": "MAYBE"}]
    with pytest.raises(PolicyError, match="MAYBE"):
        load_bundle(write(tmp_path / "p.yaml", bad))


def test_an_unknown_fact_is_rejected_with_a_suggestion(tmp_path):
    """A typo in a fact name silently disables a rule, so it must not load."""
    bad = dict(MINIMAL)
    bad["rules"] = [{"id": "r", "when": {"vendor.sancitoned": True}, "then": "REJECT"}]
    with pytest.raises(PolicyError) as exc:
        load_bundle(write(tmp_path / "p.yaml", bad))
    assert "vendor.sanctioned" in str(exc.value), "no suggestion offered"


def test_a_duplicate_rule_id_is_rejected(tmp_path):
    """Two rules with one id make the attributed control ambiguous, and conformance
    scores attribution."""
    bad = dict(MINIMAL)
    bad["rules"] = [
        {"id": "same", "when": {}, "then": "REVIEW"},
        {"id": "same", "when": {}, "then": "REJECT"},
    ]
    with pytest.raises(PolicyError, match="duplicate id"):
        load_bundle(write(tmp_path / "p.yaml", bad))


def test_a_rule_without_an_id_is_rejected(tmp_path):
    bad = dict(MINIMAL)
    bad["rules"] = [{"when": {}, "then": "REVIEW"}]
    with pytest.raises(PolicyError, match="no `id`"):
        load_bundle(write(tmp_path / "p.yaml", bad))


def test_a_non_integer_priority_is_rejected(tmp_path):
    """Evaluation order is normative — the attributed control depends on it."""
    bad = dict(MINIMAL)
    bad["rules"] = [{"id": "r", "priority": "high", "when": {}, "then": "REVIEW"}]
    with pytest.raises(PolicyError, match="priority"):
        load_bundle(write(tmp_path / "p.yaml", bad))


def test_a_comparison_against_null_is_rejected(tmp_path):
    """Absent is not zero and not unknown. Invariant 5, at the point of entry."""
    bad = dict(MINIMAL)
    bad["rules"] = [{"id": "r", "when": {"trust.score": {"gte": None}}, "then": "REVIEW"}]
    with pytest.raises(PolicyError, match="null"):
        load_bundle(write(tmp_path / "p.yaml", bad))


def test_an_unknown_top_level_key_is_rejected(tmp_path):
    """A typo in a block name would otherwise be silently ignored."""
    bad = dict(MINIMAL)
    bad["rulez"] = []
    with pytest.raises(PolicyError, match="rulez"):
        load_bundle(write(tmp_path / "p.yaml", bad))


def test_every_problem_is_reported_not_just_the_first(tmp_path):
    """A pack with four mistakes should be fixed in one pass."""
    bad = dict(MINIMAL)
    bad["rules"] = [
        {"id": "a", "when": {"amount_usd": {"gte_": 1}}, "then": "MAYBE"},
        {"id": "a", "when": {"nope.fact": True}, "then": "REVIEW"},
    ]
    with pytest.raises(PolicyError) as exc:
        load_bundle(write(tmp_path / "p.yaml", bad))
    assert len(exc.value.problems) >= 4, exc.value.problems


def test_a_missing_pack_says_so(tmp_path):
    with pytest.raises(PolicyError, match="no policy pack"):
        load_bundle(tmp_path / "absent.yaml")


def test_unparseable_yaml_is_a_policy_error(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text("rules: [\n  - id: unclosed\n", encoding="utf-8")
    with pytest.raises(PolicyError, match="could not be parsed"):
        load_bundle(path)


def test_a_pack_cannot_construct_python_objects(tmp_path):
    """`safe_load`, not `load`. A loader that can instantiate types is a code loader.

    `!!python/object/apply` is the classic YAML deserialisation vector. It must fail to
    parse rather than execute — this is the data-not-code boundary at its literal edge.
    """
    path = tmp_path / "p.yaml"
    path.write_text(
        "version: 1\nname: evil\nrules: !!python/object/apply:os.system ['echo pwned']\n",
        encoding="utf-8",
    )
    with pytest.raises(PolicyError):
        load_bundle(path)


def test_validate_false_is_available_but_not_the_default(tmp_path):
    """An escape hatch for inspecting something known-broken. Nothing in the library uses it."""
    bad = dict(MINIMAL)
    bad["rules"] = [{"id": "r", "when": {}, "then": "MAYBE"}]
    path = write(tmp_path / "p.yaml", bad)
    with pytest.raises(PolicyError):
        load_bundle(path)
    assert load_bundle(path, validate=False).name == "t"


# --- the fact vocabulary is derived, not duplicated ----------------------


def test_the_fact_vocabulary_comes_from_the_engine():
    """A hand-maintained list would drift from the facts that actually exist.

    That is exactly how a purity test in this codebase once passed while checking
    nothing: it named files instead of walking the tree.
    """
    facts = known_facts()
    assert {"amount_usd", "channel", "trust.score", "risk.score", "budget.ok"} <= facts
    assert len(facts) >= 20


def test_every_fact_the_starters_use_is_in_the_vocabulary():
    """Closes the loop: the shipped packs and the derived vocabulary agree."""
    facts = known_facts()
    for path in available_bundles():
        raw = parse_pack_text(path.read_text("utf-8"), source=path.name)
        for rule in raw.get("rules") or []:
            for fact in (rule.get("when") or {}):
                assert fact in facts, f"{path.name}:{rule.get('id')} uses {fact!r}"


# --- config --------------------------------------------------------------


def test_no_config_file_is_not_an_error(tmp_path, monkeypatch):
    """`pip install tesoro` then govern something, immediately."""
    monkeypatch.chdir(tmp_path)
    config = Config.load()
    assert config.source is None
    assert config.profile == "aegs-1"
    assert config.policy().name == "default"


def test_config_is_found_in_the_working_directory_only(tmp_path, monkeypatch):
    """Deliberately not a walk up the filesystem.

    `_load_repo_env()` in the prototype walked up looking for a `.env` and read whatever
    it found, which is a security problem in a library that handles keys.
    """
    (tmp_path / "tesoro.yaml").write_text("profile: none\n", encoding="utf-8")
    nested = tmp_path / "deep" / "deeper"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert find_config() is None, "config was found by walking up the tree"


@pytest.mark.parametrize("name", ["tesoro.yaml", "tesoro.yml", "tesoro.json"])
def test_all_three_config_names_load(tmp_path, monkeypatch, name):
    body = {"profile": "none"}
    write(tmp_path / name, body, as_json=name.endswith(".json"))
    monkeypatch.chdir(tmp_path)
    assert Config.load().profile == "none"


def test_an_unknown_profile_is_rejected(tmp_path):
    write(tmp_path / "tesoro.yaml", {"profile": "aegs-9"})
    with pytest.raises(ConfigError, match="aegs-9"):
        Config.load(tmp_path / "tesoro.yaml")


def test_profile_none_is_accepted(tmp_path):
    """An escape hatch that does not work is an escape hatch people fork around."""
    write(tmp_path / "tesoro.yaml", {"profile": "none"})
    assert Config.load(tmp_path / "tesoro.yaml").profile == "none"


def test_an_unknown_channel_is_rejected():
    problems = validate_config({"channels": {"extrenal": {"daily_usd": "1"}}})
    assert has_errors(problems)
    assert any("extrenal" in str(p) for p in problems)


def test_a_float_amount_is_rejected():
    """YAML turns an unquoted decimal into a float, and money never touches one."""
    problems = validate_config({"channels": {"external": {"daily_usd": 50.0}}})
    assert has_errors(problems)
    assert any("float" in str(p) for p in problems)


def test_a_key_in_the_config_file_is_rejected():
    """This file gets committed. A key in it is a leak, not a convenience."""
    problems = validate_config({"advisor": {"provider": "openai", "api_key": "sk-x"}})
    assert has_errors(problems)
    assert any("must not be in a config file" in str(p) for p in problems)


def test_a_relative_policy_path_resolves_against_the_config_not_the_cwd(tmp_path, monkeypatch):
    """Otherwise `tesoro check` and a running agent could read two different policies
    from one config, depending on where each was started."""
    (tmp_path / "policies").mkdir()
    write(tmp_path / "policies" / "mine.yaml", MINIMAL)
    write(tmp_path / "tesoro.yaml", {"profile": "none", "policy": "policies/mine.yaml"})

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    config = Config.load(tmp_path / "tesoro.yaml")
    assert config.policy().name == "t"


def test_validate_reports_a_broken_pack_the_config_points_at(tmp_path):
    """A config that is fine while pointing at a broken pack is not fine."""
    (tmp_path / "policies").mkdir()
    bad = dict(MINIMAL)
    bad["rules"] = [{"id": "r", "when": {}, "then": "MAYBE"}]
    write(tmp_path / "policies" / "bad.yaml", bad)
    write(tmp_path / "tesoro.yaml", {"profile": "none", "policy": "policies/bad.yaml"})

    problems = Config.load(tmp_path / "tesoro.yaml").validate()
    assert has_errors(problems)
    assert any("MAYBE" in str(p) for p in problems)


def test_a_broken_pack_is_reported_once_not_twice(tmp_path):
    """The wrapper exception carries the same problems as the list it is built from."""
    (tmp_path / "policies").mkdir()
    bad = dict(MINIMAL)
    bad["rules"] = [{"id": "r", "when": {}, "then": "MAYBE"}]
    write(tmp_path / "policies" / "bad.yaml", bad)
    write(tmp_path / "tesoro.yaml", {"profile": "none", "policy": "policies/bad.yaml"})

    problems = Config.load(tmp_path / "tesoro.yaml").validate()
    assert sum("MAYBE" in str(p) for p in problems) == 1, [str(p) for p in problems]


def test_config_and_policy_hash_separately(tmp_path):
    """They version independently, and a record carries both. A config change with an
    untouched rule file is still a different deployment."""
    write(tmp_path / "tesoro.yaml", {"profile": "aegs-1"})
    a = Config.load(tmp_path / "tesoro.yaml")
    write(tmp_path / "tesoro.yaml", {"profile": "aegs-2"})
    b = Config.load(tmp_path / "tesoro.yaml")
    assert a.content_hash != b.content_hash
    assert a.policy().hash == b.policy().hash
