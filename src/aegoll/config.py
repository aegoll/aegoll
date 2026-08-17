"""The policy bundle: limits, weights and rules, loaded together and hashed.

Config and rules live in **one** YAML file and share **one** content hash. That
hash goes into every audit record, so a decision can always be tied to the exact
ruleset *and* the exact weights that produced it. Splitting them would let the
weights drift without the audit trail noticing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from .domain import usd_to_atomic
from .errors import ConfigError, PolicyError

# `validate` is imported inside `load_bundle`, not here. It reads the fact vocabulary
# from the policy engine, which imports this module for its config dataclasses — so a
# module-level import closes a cycle. Deferring it is also honest: validation is a
# load-time concern, and nothing else in this module needs it.


def _packaged_policies() -> Path:
    """The starter policies, as package data.

    Resolved through `importlib.resources` rather than `Path(__file__).parents[n]`.
    The prototype used the latter, which worked only because the package sat inside
    a monorepo with `policies/` as a sibling: in an installed wheel `parents[1]` is
    `site-packages`, and the starters were unreachable. See PLAN.md F-A1.
    """
    return Path(str(resources.files(__package__) / "policies")).resolve()


DEFAULT_BUNDLE = _packaged_policies() / "default.yaml"


def load_env_file(path: str | Path) -> dict[str, str]:
    """Parse a `.env`-style file and return its pairs. Does **not** mutate `os.environ`.

    The prototype had `_load_repo_env()`, which resolved `.env` from
    `Path(__file__).parents[2]` and was called at *module import time* — so importing
    the package walked up the filesystem, found a file the caller had never offered it,
    and wrote its contents into the process environment as a side effect. In a library
    that also handles BYOK keys that is a security problem, not a convenience.

    Now: the caller names the file, and does what it likes with the result. Nothing is
    read unless asked for, and nothing is exported behind the caller's back.
    """
    out: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return out
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and value:
                out[key] = value
    except OSError:
        pass
    return out


@dataclass(frozen=True)
class TreasuryConfig:
    balance_atomic: int
    per_tx_atomic: int
    daily_atomic: int
    monthly_atomic: int
    per_vendor_30d_atomic: int
    per_resource_30d_atomic: int
    emergency_reserve_atomic: int
    velocity_60s: int
    velocity_1h: int
    # Earned authority (research question 12): settled transactions -> multiplier
    # on the per-transaction limit. Deterministic, table-driven, reversible.
    tiers: tuple[tuple[int, float], ...] = ()

    def per_tx_limit_for(self, settled_count: int, disputed: int) -> tuple[int, float, str]:
        """Per-transaction limit after earned authority.

        A single dispute drops the agent back to base -- authority is earned
        continuously and lost immediately, which is the conservative direction.
        """
        if disputed > 0:
            return self.per_tx_atomic, 1.0, "base (authority revoked by dispute)"
        multiplier, label = 1.0, "base"
        for threshold, mult in sorted(self.tiers):
            if settled_count >= threshold:
                multiplier, label = mult, f"tier>={threshold} (x{mult})"
        return int(self.per_tx_atomic * multiplier), multiplier, label


@dataclass(frozen=True)
class TrustConfig:
    cold_start: float
    weight_success: float
    weight_volume: float
    weight_age: float
    penalty_dispute: float
    volume_saturation: int
    age_saturation_days: float


@dataclass(frozen=True)
class RiskConfig:
    weight_amount: float
    weight_novelty: float
    weight_zscore: float
    weight_velocity: float
    weight_reprice: float
    weight_failures: float
    amount_saturation_atomic: int
    zscore_saturation: float
    reprice_ratio_flag: float
    high_risk_threshold: float


@dataclass(frozen=True)
class RoiConfig:
    # resource -> expected value in atomic units. Operator-declared; the engine
    # never invents a value it was not given.
    expected_value: dict[str, int] = field(default_factory=dict)
    default_confidence: float = 0.5


@dataclass(frozen=True)
class EiapConfig:
    ai_cost_atomic: int
    base_p_flip: float
    max_p_flip: float
    small_model_exposure_atomic: int
    large_model_exposure_atomic: int


@dataclass(frozen=True)
class Rule:
    id: str
    priority: int
    when: dict[str, Any]
    then: str
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "priority": self.priority,
            "when": self.when,
            "then": self.then,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PolicyBundle:
    version: int
    name: str
    treasury: TreasuryConfig            # external channel (the default)
    treasury_internal: TreasuryConfig   # internal channel (LLM tokens)
    trust: TrustConfig
    risk: RiskConfig
    roi: RoiConfig
    eiap: EiapConfig
    rules: tuple[Rule, ...]
    hash: str
    source: str = ""

    def sorted_rules(self) -> list[Rule]:
        return sorted(self.rules, key=lambda r: (r.priority, r.id))

    def treasury_for(self, channel: Any) -> TreasuryConfig:
        """Pick the envelope set for a channel.

        The two channels never share a budget -- see `Channel` for why.
        """
        from .domain import Channel

        return self.treasury_internal if channel is Channel.INTERNAL else self.treasury


def _atomic(node: Any, key: str, default: str) -> int:
    return usd_to_atomic(str((node or {}).get(key, default)))


def parse_pack_text(text: str, *, source: str) -> Any:
    """Parse a policy pack from YAML **or** JSON. One schema, two syntaxes.

    JSON is a subset of YAML 1.2, so `yaml.safe_load` handles both and there is one code
    path rather than two that can disagree. `safe_load` also refuses arbitrary object
    construction, which matters here: a pack is data, and a loader that could instantiate
    Python types from a file would hand that guarantee away at the door.
    """
    try:
        return yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise PolicyError(f"{source}: could not be parsed as YAML or JSON: {exc}") from exc


def load_bundle(path: str | Path | None = None, *, validate: bool = True) -> PolicyBundle:
    """Load and validate a policy pack.

    `validate=True` is the default and should stay that way. The prototype validated
    comparators inside `policy.evaluate()`, so a malformed rule only raised if a request
    reached it — and **a rule that never matches never validates**. A pack could carry a
    verdict of `MAYBE` and look fine until the one request that touched it arrived.

    Now a pack is rejected at load, in full, or it is not loaded. Pass `validate=False`
    only to inspect something known to be broken; nothing in the library does.
    """
    p = Path(path) if path else DEFAULT_BUNDLE
    if not p.is_file():
        raise PolicyError(f"no policy pack at {p}")
    raw = parse_pack_text(p.read_text(encoding="utf-8"), source=str(p))

    if validate:
        from .validate import has_errors, validate_pack  # noqa: PLC0415

        problems = validate_pack(raw, source=p.name)
        if has_errors(problems):
            raise PolicyError(
                f"{p} is not a usable policy pack",
                [x for x in problems if x.severity == "error"],
            )

    cfg = raw.get("config") or {}
    t = cfg.get("treasury") or {}
    # `treasury_internal` overrides only the keys it names; anything it omits
    # falls back to the external block, so an existing bundle keeps working.
    t_internal = {**t, **(cfg.get("treasury_internal") or {})}
    tr = cfg.get("trust") or {}
    rk = cfg.get("risk") or {}
    ro = cfg.get("roi") or {}
    ei = cfg.get("eiap") or {}

    treasury = TreasuryConfig(
        balance_atomic=_atomic(t, "balance_usd", "500"),
        per_tx_atomic=_atomic(t, "per_transaction_usd", "10"),
        daily_atomic=_atomic(t, "daily_usd", "50"),
        monthly_atomic=_atomic(t, "monthly_usd", "300"),
        per_vendor_30d_atomic=_atomic(t, "per_vendor_30d_usd", "100"),
        per_resource_30d_atomic=_atomic(t, "per_resource_30d_usd", "50"),
        emergency_reserve_atomic=_atomic(t, "emergency_reserve_usd", "10"),
        velocity_60s=int(t.get("velocity_60s", 10)),
        velocity_1h=int(t.get("velocity_1h", 100)),
        tiers=tuple(
            (int(row["settled_at_least"]), float(row["per_tx_multiplier"]))
            for row in (t.get("earned_authority") or [])
        ),
    )

    def _treasury_from(node: dict[str, Any]) -> TreasuryConfig:
        return TreasuryConfig(
            balance_atomic=_atomic(node, "balance_usd", "500"),
            per_tx_atomic=_atomic(node, "per_transaction_usd", "10"),
            daily_atomic=_atomic(node, "daily_usd", "50"),
            monthly_atomic=_atomic(node, "monthly_usd", "300"),
            per_vendor_30d_atomic=_atomic(node, "per_vendor_30d_usd", "100"),
            per_resource_30d_atomic=_atomic(node, "per_resource_30d_usd", "50"),
            emergency_reserve_atomic=_atomic(node, "emergency_reserve_usd", "10"),
            velocity_60s=int(node.get("velocity_60s", 10)),
            velocity_1h=int(node.get("velocity_1h", 100)),
            tiers=tuple(
                (int(row["settled_at_least"]), float(row["per_tx_multiplier"]))
                for row in (node.get("earned_authority") or [])
            ),
        )

    treasury_internal = _treasury_from(t_internal)

    trust = TrustConfig(
        cold_start=float(tr.get("cold_start", 0.25)),
        weight_success=float(tr.get("weight_success", 0.45)),
        weight_volume=float(tr.get("weight_volume", 0.30)),
        weight_age=float(tr.get("weight_age", 0.25)),
        penalty_dispute=float(tr.get("penalty_dispute", 0.60)),
        volume_saturation=int(tr.get("volume_saturation", 20)),
        age_saturation_days=float(tr.get("age_saturation_days", 30)),
    )

    risk = RiskConfig(
        weight_amount=float(rk.get("weight_amount", 0.25)),
        weight_novelty=float(rk.get("weight_novelty", 0.20)),
        weight_zscore=float(rk.get("weight_zscore", 0.20)),
        weight_velocity=float(rk.get("weight_velocity", 0.15)),
        weight_reprice=float(rk.get("weight_reprice", 0.10)),
        weight_failures=float(rk.get("weight_failures", 0.10)),
        amount_saturation_atomic=_atomic(rk, "amount_saturation_usd", "1000"),
        zscore_saturation=float(rk.get("zscore_saturation", 4.0)),
        reprice_ratio_flag=float(rk.get("reprice_ratio_flag", 3.0)),
        high_risk_threshold=float(rk.get("high_risk_threshold", 0.6)),
    )

    roi = RoiConfig(
        expected_value={
            str(k): usd_to_atomic(str(v))
            for k, v in (ro.get("expected_value_usd") or {}).items()
        },
        default_confidence=float(ro.get("default_confidence", 0.5)),
    )

    eiap = EiapConfig(
        ai_cost_atomic=_atomic(ei, "ai_cost_usd", "0.004"),
        base_p_flip=float(ei.get("base_p_flip", 0.01)),
        max_p_flip=float(ei.get("max_p_flip", 0.05)),
        small_model_exposure_atomic=_atomic(ei, "small_model_exposure_usd", "1"),
        large_model_exposure_atomic=_atomic(ei, "large_model_exposure_usd", "100"),
    )

    # Advisor price corrections, applied before any EIAP evaluation so the gate
    # is computed from the operator's figures rather than our shipped defaults.
    pricing = cfg.get("advisor_pricing") or {}
    if pricing:
        from .advisors import apply_pricing_overrides  # noqa: PLC0415

        apply_pricing_overrides(pricing)

    rules = tuple(
        Rule(
            id=str(r["id"]),
            priority=int(r.get("priority", 500)),
            when=dict(r.get("when") or {}),
            then=str(r["then"]).upper(),
            reason=str(r.get("reason", "")),
        )
        for r in (raw.get("rules") or [])
    )

    # Hash the canonicalised content, not the raw bytes: reformatting the YAML
    # should not invalidate replay, but changing a value must.
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    return PolicyBundle(
        version=int(raw.get("version", 1)),
        name=str(raw.get("name", p.stem)),
        treasury=treasury,
        treasury_internal=treasury_internal,
        trust=trust,
        risk=risk,
        roi=roi,
        eiap=eiap,
        rules=rules,
        hash=digest,
        source=str(p),
    )


#: Extensions a pack may use, in preference order. YAML is canonical; JSON is the same
#: schema in the other syntax, for callers whose tooling emits JSON.
PACK_SUFFIXES = (".yaml", ".yml", ".json")


def available_bundles(directory: Path | None = None) -> list[Path]:
    """Discoverable packs, **one per name**.

    De-duplicated by stem, YAML winning, because the packaged starters ship in both
    syntaxes and discovery must not report `strict` twice. Two entries with one name make
    `--policy strict` ambiguous, and something downstream would quietly pick whichever
    sorted first — the AEGS conformance suite selects a pack by stem exactly that way.

    Pointing at a `.json` pack explicitly by path always works; it is only *discovery*
    that prefers one form.
    """
    base = directory or DEFAULT_BUNDLE.parent
    chosen: dict[str, Path] = {}
    for suffix in PACK_SUFFIXES:
        for path in sorted(base.glob(f"*{suffix}")):
            chosen.setdefault(path.stem, path)
    return [chosen[stem] for stem in sorted(chosen)]
