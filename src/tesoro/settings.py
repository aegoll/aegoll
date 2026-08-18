"""`tesoro.yaml` — the one file a user edits, and the only one they have to.

Two things live in two files on purpose, and conflating them causes trouble later:

* **This file** says which profile to enforce, where the policy is, what the channel
  ceilings are, and where evidence goes. It is *deployment*.
* **The policy pack** says what the rules actually are. It is *policy*, it is data, and
  it is what `validate.py` guards.

`tesoro.yaml` and `tesoro.json` are the same schema in two syntaxes (PLAN.md A0.5). One
loader, one parser, so the two cannot disagree — the extension only decides which name is
looked for first.

**`validate()` returns problems and never raises.** `tesoro check` needs every problem at
once for a CI log, not the first one; a config with four mistakes should be fixed in one
pass. `Config.load()` raises on errors, because a caller asking for a usable config should
not receive a broken one.
"""

from __future__ import annotations

from .hashing import digest as hash_digest
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import PolicyBundle, load_bundle, parse_pack_text
from .errors import ConfigError, PolicyError
from .validate import Problem, has_errors

#: Searched in order, in the working directory. YAML first because that is what
#: `tesoro init` writes and what the documentation shows.
CONFIG_NAMES = ("tesoro.yaml", "tesoro.yml", "tesoro.json")

#: Profiles a config may name. `none` genuinely disables profile enforcement — an escape
#: hatch that does not work is an escape hatch people fork around.
PROFILES = ("aegs-1", "aegs-2", "none")

_TOP_KEYS = {"profile", "policy", "channels", "evidence", "advisor"}
_CHANNELS = {"internal", "external"}
_CHANNEL_KEYS = {
    "daily_usd", "monthly_usd", "per_transaction_usd", "per_vendor_usd",
    "per_resource_usd", "balance_usd", "emergency_reserve_usd",
    "velocity_60s", "velocity_1h",
}
_ADVISOR_KEYS = {"provider", "model", "enabled"}


def find_config(start: str | Path | None = None) -> Path | None:
    """The config file in `start` (default: the working directory), or None.

    Deliberately **not** a walk up the filesystem. `_load_repo_env()` in the prototype
    walked up looking for a `.env` and read whatever it found, which is a security problem
    in a library that handles keys (PLAN.md F-A1). Config is looked for exactly where the
    caller is standing; if it is elsewhere, the caller says so.
    """
    base = Path(start) if start else Path.cwd()
    for name in CONFIG_NAMES:
        candidate = base / name
        if candidate.is_file():
            return candidate
    return None


@dataclass(frozen=True)
class Config:
    """Deployment settings. The policy itself hangs off `policy`."""

    profile: str
    policy_path: Path | None
    channels: dict[str, dict[str, Any]]
    evidence_path: Path | None
    advisor: dict[str, Any] | None
    content_hash: str
    source: Path | None = None
    _raw: dict[str, Any] = field(default_factory=dict, repr=False)

    # --- loading ----------------------------------------------------------

    @classmethod
    def defaults(cls) -> "Config":
        """What you get with no config file at all: the packaged starter, aegs-1.

        A missing config is not an error. `pip install tesoro` then `Governor.load()`
        should govern something sensible immediately, and refuse things — a spend cap that
        needs a config file before it caps anything is a worse first experience than one
        that starts strict.
        """
        return cls(
            profile="aegs-1",
            policy_path=None,
            channels={},
            evidence_path=None,
            advisor=None,
            content_hash=_hash({"profile": "aegs-1"}),
            source=None,
            _raw={},
        )

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        """Read a config, or fall back to `defaults()` when there is none.

        Raises `ConfigError` listing **every** problem when the file exists and is wrong.
        """
        found = Path(path) if path else find_config()
        if found is None:
            if path is not None:
                raise ConfigError(f"no config at {path}")
            return cls.defaults()
        if not found.is_file():
            raise ConfigError(f"no config at {found}")

        raw = parse_pack_text(found.read_text(encoding="utf-8"), source=str(found))
        if not isinstance(raw, dict):
            raise ConfigError(f"{found}: must be a mapping, got {type(raw).__name__}")

        problems = validate_config(raw, source=found.name)
        if has_errors(problems):
            errors = [p for p in problems if p.severity == "error"]
            raise ConfigError(
                f"{found} is not a usable config\n  "
                + "\n  ".join(str(p) for p in errors)
            )

        policy = raw.get("policy")
        evidence = (raw.get("evidence") or {}).get("journal")
        return cls(
            profile=str(raw.get("profile", "aegs-1")),
            policy_path=(found.parent / policy).resolve() if policy else None,
            channels=dict(raw.get("channels") or {}),
            evidence_path=(found.parent / evidence).resolve() if evidence else None,
            advisor=dict(raw["advisor"]) if raw.get("advisor") else None,
            content_hash=_hash(raw),
            source=found,
            _raw=raw,
        )

    # --- what the caller actually wants ----------------------------------

    def policy(self) -> PolicyBundle:
        """The pack this config names, or the packaged starter.

        Relative paths resolve against the config file's own directory, never the working
        directory. Otherwise `tesoro check` and a running agent could read two different
        policies from the same config, depending on where each was started.
        """
        return load_bundle(self.policy_path) if self.policy_path else load_bundle()

    def validate(self) -> list[Problem]:
        """Every problem in this config *and* its policy pack. Never raises.

        Both together, because that is the question a user is actually asking: *will this
        work?* A config that is fine while pointing at a broken pack is not fine.
        """
        problems = validate_config(self._raw, source=str(self.source or "<defaults>"))
        try:
            self.policy()
        except PolicyError as exc:
            # `PolicyError` already carries one `Problem` per fault, formatted. Re-wrapping
            # its whole message as a single Problem printed every fault twice — once in the
            # wrapper, once in the list. Take the parts, not the summary.
            problems.extend(exc.problems or [
                Problem("error", str(self.policy_path or "<packaged>"), str(exc))
            ])
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            problems.append(Problem("error", str(self.policy_path or "<packaged>"), str(exc)))
        return problems

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "policy": str(self.policy_path) if self.policy_path else None,
            "channels": self.channels,
            "evidence": str(self.evidence_path) if self.evidence_path else None,
            "advisor": self.advisor,
            "contentHash": self.content_hash,
            "source": str(self.source) if self.source else None,
        }


def _hash(raw: Any) -> str:
    """Content hash over the config as written.

    Separate from the policy pack's hash on purpose: they version independently, and a
    decision record carries both. A config change with an untouched rule file is still a
    different deployment.
    """
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str)
    return hash_digest(canonical)


def validate_config(raw: Any, *, source: str = "tesoro.yaml") -> list[Problem]:
    """Every problem in a parsed config. Never raises."""
    problems: list[Problem] = []
    if not isinstance(raw, dict):
        return [Problem("error", source, f"must be a mapping, got {type(raw).__name__}")]

    for key in sorted(set(raw) - _TOP_KEYS):
        problems.append(Problem(
            "error", source,
            f"unknown key {key!r}. Allowed: {', '.join(sorted(_TOP_KEYS))}",
        ))

    profile = raw.get("profile", "aegs-1")
    if profile not in PROFILES:
        problems.append(Problem(
            "error", f"{source}:profile",
            f"unknown profile {profile!r}. Allowed: {', '.join(PROFILES)}",
        ))

    policy = raw.get("policy")
    if policy is not None and not isinstance(policy, str):
        problems.append(Problem(
            "error", f"{source}:policy",
            f"must be a path, got {type(policy).__name__}",
        ))

    channels = raw.get("channels")
    if channels is not None:
        if not isinstance(channels, dict):
            problems.append(Problem("error", f"{source}:channels", "must be a mapping"))
        else:
            for name, block in channels.items():
                where = f"{source}:channels.{name}"
                if name not in _CHANNELS:
                    problems.append(Problem(
                        "error", where,
                        f"unknown channel {name!r}. The two channels are "
                        f"{', '.join(sorted(_CHANNELS))}, and they never share an envelope.",
                    ))
                    continue
                if not isinstance(block, dict):
                    problems.append(Problem("error", where, "must be a mapping"))
                    continue
                for key, value in block.items():
                    if key not in _CHANNEL_KEYS:
                        problems.append(Problem(
                            "error", where,
                            f"unknown limit {key!r}. Allowed: {', '.join(sorted(_CHANNEL_KEYS))}",
                        ))
                    elif isinstance(value, float):
                        problems.append(Problem(
                            "error", f"{where}.{key}",
                            f"money must be a string, not a float ({value!r}). Write "
                            f'"{value}" — money never touches a float, and YAML turns an '
                            "unquoted decimal into one.",
                        ))
                    elif value is None:
                        problems.append(Problem(
                            "warning", f"{where}.{key}",
                            "null means this limit does not exist, which is different from "
                            "zero. Remove the key if that is what you meant.",
                        ))

    advisor = raw.get("advisor")
    if advisor is not None:
        if not isinstance(advisor, dict):
            problems.append(Problem("error", f"{source}:advisor", "must be a mapping"))
        else:
            for key in sorted(set(advisor) - _ADVISOR_KEYS):
                problems.append(Problem(
                    "error", f"{source}:advisor",
                    f"unknown key {key!r}. Allowed: {', '.join(sorted(_ADVISOR_KEYS))}",
                ))
            for key in ("api_key", "key", "token", "secret"):
                if key in advisor:
                    problems.append(Problem(
                        "error", f"{source}:advisor",
                        f"{key!r} must not be in a config file. Keys come from the "
                        "environment; this file gets committed and a key in it is a leak.",
                    ))

    evidence = raw.get("evidence")
    if evidence is not None:
        if not isinstance(evidence, dict):
            problems.append(Problem("error", f"{source}:evidence", "must be a mapping"))
        elif set(evidence) - {"journal"}:
            problems.append(Problem(
                "error", f"{source}:evidence",
                f"unknown key(s) {sorted(set(evidence) - {'journal'})}. Allowed: journal",
            ))

    return problems


__all__ = ["CONFIG_NAMES", "PROFILES", "Config", "find_config", "validate_config"]
