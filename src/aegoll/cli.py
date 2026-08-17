"""Headless entry point: `python -m aegoll.cli <command>`.

    decide       one-off decision, full engine breakdown
    audit        verify the hash chain
    replay       determinism check against the journal
    reviews      list / resolve the review queue
    bench        latency and cost-per-decision measurement
    record       emit or validate AEGS Decision Records
    intent       declare, list or revoke an economic intent
    identity     register or inspect an agent identity
    policies     list available policy bundles

`scenarios` and `eval` are gone: the first is a demo and the second costs money for
advisor calls, and neither belongs in a library's shipped surface. Both live in
aegoll-integrations now.

`bench` stays, against the plan's first draft. It measures the decision latency of
the package on the caller's own hardware, needs no framework, no key and no money,
and it substantiates the layer's central performance claim. Pushing that into a
separate repository would put a core claim somewhere the user has to go looking.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .clock import FixedClock
from .config import DEFAULT_BUNDLE, available_bundles, load_bundle
from .errors import ConfigError, PolicyError
from .runtime import Aegoll, Paths
from .domain import Purpose, Vendor, fmt_usd

#: The fixed instant `--fixed-time` pins the clock to. Lived in the scenarios module,
#: which has left the package; it is a clock constant, so it belongs beside the clock.
BASE_TIME = datetime(2026, 8, 12, 14, 30, tzinfo=timezone.utc)

#: What `aegoll init` writes. Comments included on purpose: this is the first and often
#: only aegoll document a user reads closely, so the reasoning goes where they are already
#: looking rather than in documentation they have not opened.
_STARTER_CONFIG = '''\
# aegoll -- what to enforce, and where the rules live.
#
# `profile` says which controls MUST exist and what evidence MUST be emitted; that is
# the standard's business. `policy` says what the rules actually are; that is yours.
# Two files, two jobs.
#
# Amounts are strings. Money never touches a float, and YAML turns an unquoted decimal
# into one -- `aegoll check` will tell you so.

profile: aegs-1
policy: policies/default.yaml

channels:
  # What the agent pays out.
  external:
    daily_usd: "50"
    per_transaction_usd: "10"

  # The tokens it burns thinking. Never shares an envelope with the above: different
  # currency, different counterparty, different failure mode. An exhausted token budget
  # rejects rather than queues for review -- there is no human to ask mid-run, and
  # starting a run that cannot finish wastes the budget that is already short.
  internal:
    daily_usd: "0.15"
    per_transaction_usd: "0.04"

evidence:
  journal: .aegoll/audit.jsonl

# Optional, and never in the decision path: an advisor may tighten a verdict, never
# widen one. Keys come from the environment, never from this file -- this file gets
# committed.
#
# advisor:
#   provider: anthropic
#   model: claude-haiku-4-5
'''


def _aegl(args: argparse.Namespace, ephemeral: bool = False) -> Aegoll:
    bundle = load_bundle(args.policy) if args.policy else load_bundle()
    paths = Paths.ephemeral(".data-cli") if ephemeral else Paths.under()
    clock = FixedClock(BASE_TIME) if getattr(args, "fixed_time", False) else None
    return Aegoll(bundle=bundle, paths=paths, clock=clock)


def cmd_decide(args: argparse.Namespace) -> int:
    aegoll = _aegl(args, ephemeral=args.dry_run)
    try:
        vendor = Vendor(id=args.vendor, name=args.vendor, sanctioned=args.sanctioned)
        request = aegoll.build_request(
            resource=args.resource,
            amount_usd=args.amount,
            vendor=vendor,
            purpose=Purpose(args.purpose),
        )
        decision = aegoll.decide(request) if args.dry_run else aegoll.authorize(request)

        if args.json:
            print(json.dumps(decision.as_dict(), indent=2))
            return EXIT_OK if decision.approved else EXIT_REFUSED

        print(f"{decision.verdict.value}  {args.resource}  {fmt_usd(request.amount_atomic)}")
        print(f"  rule     : {decision.matched_rule}")
        print(f"  trust    : {decision.trust.value:.4f}  {list(decision.trust.flags)}")
        print(f"  risk     : {decision.risk.value:.4f}  {list(decision.risk.flags)}")
        r = decision.roi
        print(
            f"  roi      : {'ratio ' + format(r.ratio, '.2f') if r.ratio is not None else 'unknown'}"
        )
        print(f"  budget   : ok={decision.budget.ok} binding={decision.budget.binding}")
        e = decision.intelligence.eiap
        print(
            f"  eiap     : would_invoke={e.would_invoke} tier={e.would_tier.value} "
            f"break_even={fmt_usd(e.break_even_exposure_atomic)}"
        )
        print(f"  latency  : {decision.latency_us:.0f} us")
        print("  reasons  :")
        for line in decision.explain():
            print(f"    - {line}")
        return EXIT_OK if decision.approved else EXIT_REFUSED
    finally:
        aegoll.close()


def cmd_audit(args: argparse.Namespace) -> int:
    aegoll = _aegl(args)
    try:
        entries = aegoll.audit.entries()
        ok, problems = aegoll.audit.verify()
        if args.json:
            print(json.dumps({
                "path": str(aegoll.paths.audit),
                "entries": len(entries),
                "valid": ok,
                "problems": problems,
            }, indent=2))
            return EXIT_OK if ok else EXIT_CHAIN

        print(f"audit: {len(entries)} entries at {aegoll.paths.audit}")
        print(f"chain: {'VALID' if ok else 'BROKEN'}")
        for p in problems:
            print(f"  ! {p}")
        if args.tail:
            for e in entries[-args.tail :]:
                tx = e.payload.get("transaction") or {}
                print(
                    f"  #{e.seq:04d} {e.at[:19]}  {e.verdict:9} "
                    f"{tx.get('resource','-'):28} ${tx.get('amountUsd', 0):.6f}"
                )
        return EXIT_OK if ok else EXIT_CHAIN
    finally:
        aegoll.close()


def cmd_replay(args: argparse.Namespace) -> int:
    aegoll = _aegl(args)
    try:
        result = aegoll.replay()
        print(json.dumps(result, indent=2))
        return EXIT_OK if result["ok"] else EXIT_CHAIN
    finally:
        aegoll.close()


def cmd_reviews(args: argparse.Namespace) -> int:
    aegoll = _aegl(args)
    try:
        if args.resolve:
            item = aegoll.queue.resolve(args.resolve, args.as_, by="cli", note=args.note)
            print("resolved:" if item else "not found:", args.resolve)
            return 0 if item else 1
        items = aegoll.queue.all() if args.all else aegoll.queue.pending()
        if args.json:
            print(json.dumps([i.as_dict() for i in items], indent=2))
            return 0

        if not items:
            print("no items")
            return 0
        for i in items:
            flag = "BLOCKING" if i.blocking else "pausable"
            print(
                f"  {i.request_id}  {i.verdict:9} {flag:9} ${i.amount_usd:.6f} "
                f"{i.resource}  [{i.resolution}]"
            )
            for r in i.reasons:
                print(f"      {r}")
        return 0
    finally:
        aegoll.close()


def cmd_bench(args: argparse.Namespace) -> int:
    """The cost-and-latency claim, measured rather than asserted."""
    aegoll = _aegl(args, ephemeral=True)
    try:
        vendor = Vendor(id="bench-vendor", name="Bench Vendor")
        latencies = []
        started = time.perf_counter()
        for _ in range(args.n):
            req = aegoll.build_request(
                resource="/market/snapshot", amount_usd="0.001", vendor=vendor
            )
            latencies.append(aegoll.decide(req).latency_us)
        wall = time.perf_counter() - started
        latencies.sort()

        def pct(p: float) -> float:
            return latencies[min(len(latencies) - 1, int(p * len(latencies)))]

        if args.json:
            print(json.dumps({
                "decisions": args.n,
                "wallSeconds": round(wall, 6),
                "perSecond": round(args.n / wall),
                "latencyP50Us": round(statistics.median(latencies)),
                "latencyP99Us": round(pct(0.99)),
                "latencyMaxUs": round(latencies[-1]),
                "inferenceCostUsd": "0.000000",
            }, indent=2))
            return 0

        print(f"decisions      : {args.n}")
        print(f"wall clock     : {wall:.3f}s  ({args.n / wall:,.0f} decisions/sec)")
        print(f"latency p50    : {statistics.median(latencies):.0f} us")
        print(f"latency p99    : {pct(0.99):.0f} us")
        print(f"latency max    : {latencies[-1]:.0f} us")
        print("inference cost : $0.000000 (no model was invoked)")
        print(f"p99 under 1ms  : {pct(0.99) < 1000}")
        return 0
    finally:
        aegoll.close()


def cmd_policies(args: argparse.Namespace) -> int:
    bundles = [(p, load_bundle(p)) for p in available_bundles()]
    if args.json:
        print(json.dumps([
            {"name": b.name, "hash": b.hash, "rules": len(b.rules), "path": str(p)}
            for p, b in bundles
        ], indent=2))
        return 0
    for p, b in bundles:
        print(f"  {b.name:12} {b.hash}  {len(b.rules)} rules  {p}")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    """Emit or validate AEGS Decision Records from the journal.

    The validator is the interoperability surface: another implementation can run
    `--validate` against its own exported records without sharing any of AEGL's
    code, which is what makes the schema a standard rather than our file format
    written down.
    """
    from . import record as record_mod

    if args.file:
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
        records = data if isinstance(data, list) else [data]
        source = args.file
    else:
        aegoll = _aegl(args)
        try:
            records = record_mod.records_from_journal(aegoll.audit.entries())
        finally:
            aegoll.close()
        source = "the local journal"

    if args.export:
        Path(args.export).write_text(
            json.dumps(records, indent=2) + chr(10), encoding="utf-8"
        )
        print(f"  wrote {len(records)} record(s) to {args.export}")
        return 0

    valid, problems = record_mod.validate_all(records)
    if args.json:
        print(json.dumps({"records": len(records), "valid": valid,
                          "problems": problems}, indent=2))
        return 0 if not problems else 1

    print(f"  schema  : AEGS {record_mod.AEGS_VERSION}")
    print(f"  source  : {source}")
    print(f"  records : {len(records)}")
    print(f"  valid   : {valid}/{len(records)}")
    for p in problems[: args.limit]:
        print(f"    ! {p}")
    if len(problems) > args.limit:
        print(f"    ... and {len(problems) - args.limit} more")
    return 0 if not problems else 1


def cmd_intent(args: argparse.Namespace) -> int:
    """Declare, list or revoke an economic intent.

    An intent says what an agent was sent out to do, before it acts. Every other
    engine asks whether a spend is permitted; this is the only one that can notice
    a perfectly permissible spend on the wrong thing.
    """
    from datetime import timedelta  # noqa: PLC0415

    aegoll = _aegl(args)
    try:
        if args.revoke:
            ok = aegoll.intents.revoke(args.revoke)
            print(f"  {'revoked' if ok else 'not found'}: {args.revoke}")
            return 0 if ok else 1

        if args.declare:
            expires = (
                aegoll.clock.now() + timedelta(hours=args.expires_in)
                if args.expires_in
                else None
            )
            intent = aegoll.intents.declare(
                agent_id=args.agent,
                purpose=args.declare,
                maximum_usd=args.maximum,
                asset=args.asset,
                maximum_per_action_usd=args.max_per_action,
                allowed_resources=args.resource or (),
                allowed_categories=args.category or (),
                allowed_channels=args.channel or (),
                expected_outcome=args.outcome,
                expires_at=expires,
                now=aegoll.clock.now(),
            )
            if args.json:
                print(json.dumps(intent.as_dict(), indent=2))
            else:
                print(f"  declared {intent.intent_id}")
                print(f"    purpose   : {intent.purpose}")
                print(f"    maximum   : {fmt_usd(intent.maximum_atomic)} {intent.asset} "
                      "(total, not per action)")
                print(f"    resources : {', '.join(intent.allowed_resources) or 'unrestricted'}")
                print(f"    expires   : {intent.expires_at or 'never'}")
            return 0

        intents = aegoll.intents.all()
        if args.json:
            print(json.dumps([i.as_dict() for i in intents], indent=2))
            return 0
        if not intents:
            print("  no intents declared -- actions are ungoverned by intent, and "
                  "their records say so")
            return 0
        for i in intents:
            spent = aegoll.store.spent_under_intent(i.intent_id)
            print(
                f"  {i.intent_id:20} {i.status:9} {fmt_usd(spent)} of "
                f"{fmt_usd(i.maximum_atomic)}  {i.purpose[:38]}"
            )
        return 0
    finally:
        aegoll.close()


def cmd_identity(args: argparse.Namespace) -> int:
    """Register, list or change the status of an agent identity.

    Pseudonymous by default. `--controller` is stored but never disclosed to a
    counterparty, and `--show vendor` prints exactly what one would see.
    """
    from .engines.evidence.identity import Party  # noqa: PLC0415

    aegoll = _aegl(args)
    try:
        if args.status:
            ok = aegoll.identities.set_status(args.agent, args.status)
            print(f"  {'set ' + args.status if ok else 'not found'}: {args.agent}")
            return 0 if ok else 1

        if args.register:
            identity = aegoll.identities.register(
                agent_id=args.agent,
                purpose=args.register,
                parent_agent_id=args.parent,
                controller=Party(id=args.controller, kind=args.controller_kind)
                if args.controller
                else None,
                authorized_networks=args.network or (),
                per_action_usd=args.per_action,
                daily_usd=args.daily,
                risk_profile=args.risk_profile,
                policy_version=aegoll.bundle.hash,
                now=aegoll.clock.now(),
            )
            print(f"  registered {identity.agent_id}")
            print(f"    purpose    : {identity.purpose}")
            print(f"    controller : "
                  f"{'set (never disclosed to counterparties)' if identity.controller else 'none'}")
            print(f"    per action : "
                  f"{fmt_usd(identity.per_action_atomic) if identity.per_action_atomic else 'policy only'}")
            return 0

        identities = aegoll.identities.all()
        if args.show:
            target = aegoll.identities.get(args.agent)
            if target is None:
                print(f"  no identity registered for {args.agent}")
                return 1
            print(json.dumps(target.disclose(args.show), indent=2))
            return 0
        if args.json:
            print(json.dumps([i.disclose("auditor") for i in identities], indent=2))
            return 0
        if not identities:
            print("  no identities registered -- actions are ungoverned by identity, "
                  "and their records say so")
            return 0
        for i in identities:
            limits = (
                fmt_usd(i.per_action_atomic) if i.per_action_atomic else "policy only"
            )
            print(
                f"  {i.agent_id:16} {i.status:9} per-action {limits:12} "
                f"{'delegated from ' + i.parent_agent_id if i.parent_agent_id else ''}"
                f"  {i.purpose[:32]}"
            )
        return 0
    finally:
        aegoll.close()


def cmd_init(args: argparse.Namespace) -> int:
    """Scaffold `aegoll.yaml` and a starter policy pack in the working directory.

    Copies the packaged starter out rather than pointing at it, so the first thing a user
    does with a policy is *read and edit their own copy*. A config that references a file
    inside site-packages teaches people their policy is not theirs to change.

    Refuses to overwrite. A policy file is the thing that decides whether money moves;
    clobbering one on a mistyped command is not a risk worth taking for the convenience.
    """
    target = Path(args.dir or ".").resolve()
    config_path = target / "aegoll.yaml"
    policy_dir = target / "policies"
    policy_path = policy_dir / "default.yaml"

    existing = [p for p in (config_path, policy_path) if p.exists()]
    if existing and not args.force:
        for p in existing:
            print(f"exists: {p}")
        print()
        print("nothing written. Pass --force to overwrite, or edit these instead.")
        return EXIT_INVALID

    starter = load_bundle().source or str(DEFAULT_BUNDLE)
    policy_dir.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(Path(starter).read_text(encoding="utf-8"), encoding="utf-8")

    config_path.write_text(_STARTER_CONFIG, encoding="utf-8")

    if args.json:
        print(json.dumps({
            "config": str(config_path),
            "policy": str(policy_path),
        }, indent=2))
        return 0

    print(f"wrote {config_path}")
    print(f"wrote {policy_path}")
    print()
    print("next:  aegoll check     # validate before an agent holds a wallet")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Validate config and policy. Exit 1 if anything is wrong.

    The quiet win of the whole CLI: a policy change that would refuse everything, or
    allow everything, fails the build **before** it reaches an agent holding a wallet.
    Reports every problem rather than the first, because a config with four mistakes
    should be fixed in one pass.
    """
    from .settings import Config  # noqa: PLC0415
    from .validate import format_problems  # noqa: PLC0415

    try:
        config = Config.load(args.config)
    except (ConfigError, PolicyError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "problems": [str(exc)]}, indent=2))
        else:
            print(str(exc))
        return 1

    from .profiles import Profile  # noqa: PLC0415
    from .validate import Problem  # noqa: PLC0415

    problems = config.validate()

    # The profile is loaded before either output branch, so `--json` and the human form
    # report the same thing. Splitting them once meant `--json` referenced a name the
    # human path had assigned, which is the shape of bug that only shows up in the
    # less-used branch.
    profile = None
    try:
        profile = Profile.load(config.profile)
    except ConfigError as exc:
        problems.append(Problem("error", f"{config.source or 'config'}:profile", str(exc)))

    errors = [p for p in problems if p.severity == "error"]

    if args.json:
        print(json.dumps({
            "ok": not errors,
            "config": config.as_dict(),
            "profile": profile.as_dict() if profile is not None else None,
            "problems": [p.as_dict() for p in problems],
        }, indent=2))
        return 1 if errors else 0

    where = config.source or "<no config file; packaged defaults>"
    print(f"config : {where}")
    if profile is None:
        print("profile: UNUSABLE — see below")
    elif profile.enforces():
        print(f"profile: {profile.id}  {len(profile.required_controls())} required control(s)")
    else:
        # Said out loud. A user who selected `none` and forgot is otherwise looking at a
        # green check that guarantees nothing.
        print(f"profile: {profile.id}  — NO conformance enforcement")
    try:
        bundle = config.policy()
        print(f"policy : {bundle.name}  {bundle.hash}  {len(bundle.rules)} rules")
    except (ConfigError, PolicyError):
        # Detail comes from the problems list below. Printing the exception here too
        # reported every fault twice, which makes a long list read as a longer one.
        print("policy : UNUSABLE — see below")

    if profile is not None and args.controls:
        print()
        print(f"controls required by {profile.id}:")
        for req in profile.required_controls():
            print(f"  {req.requirement:14} {req.control:22} <- {req.record_path}")

    if not problems:
        print()
        print("ok")
        return 0
    print()
    print(format_problems(problems))
    return 1 if errors else 0


def cmd_report(args: argparse.Namespace) -> int:
    """What was spent, what was refused, and which control decided.

    `by attributed control` is the part worth reading. Counts by verdict say what happened;
    counts by attributed control say what actually governed this agent, which is often not
    what the policy file's author expected.
    """
    from . import reporting  # noqa: PLC0415
    from .settings import Config  # noqa: PLC0415

    try:
        profile = Config.load(getattr(args, "config", None)).profile
    except (ConfigError, PolicyError):
        profile = None

    aegoll = _aegl(args)
    try:
        report = reporting.build(aegoll, profile=profile, limit=args.limit)
    finally:
        aegoll.close()

    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
        return EXIT_OK if report.chain and report.chain.valid else EXIT_CHAIN

    print(f"policy   : {report.policy_name}  {report.policy_hash}  {report.policy_rules} rules")
    print(f"profile  : {report.profile or 'none'}")
    print(
        f"decisions: {report.decisions_total}  settled {report.settled}  "
        f"spent ${report.spent_usd}"
    )
    if report.pending_reviews:
        print(f"pending  : {report.pending_reviews} awaiting review")

    if report.by_verdict:
        print()
        print("by verdict")
        for verdict, count in sorted(report.by_verdict.items(), key=lambda kv: -kv[1]):
            print(f"  {verdict:10} {count}")

    if report.by_attributed_control:
        print()
        print("by attributed control  -- what actually governed this agent")
        for control, count in sorted(report.by_attributed_control.items(), key=lambda kv: -kv[1]):
            print(f"  {control:22} {count}")

    for channel, envelopes in report.envelopes.items():
        if not envelopes:
            continue
        print()
        print(f"envelopes: {channel}")
        for e in envelopes:
            mark = " <-- binding" if e.binding else ""
            if e.cumulative:
                print(
                    f"  {e.name:16} {e.window:22} ${e.used_usd} of ${e.limit_usd}"
                    f"  headroom ${e.headroom_usd}{mark}"
                )
            else:
                # A per-call ceiling has no `used`. Printing "0 of 10" beside the
                # cumulative windows reads as "nothing was spent", which is false.
                print(f"  {e.name:16} {e.window:22} ceiling ${e.limit_usd}{mark}")

    if report.decisions:
        print()
        print(f"decisions (newest first, {len(report.decisions)} of {report.decisions_total})")
        for d in report.decisions:
            print(
                f"  {d.at[:19]}  {d.verdict:9} ${d.amount_usd or '-':>12}  "
                f"{(d.resource or '-')[:24]:24} [{d.attributed_control}]"
            )
            if d.reason:
                print(f"      {d.reason[:96]}")

    if report.chain:
        print()
        print(
            f"chain    : {report.chain.entries} entries, "
            f"{'VALID' if report.chain.valid else 'BROKEN'}"
        )
        for problem in report.chain.problems:
            print(f"  ! {problem}")
        # Printed every time, next to the verdict on the chain. VALID without this
        # overstates what a hash chain proves.
        print(f"  note   : {report.chain.caveat}")

    return EXIT_OK if report.chain and report.chain.valid else EXIT_CHAIN


def _explain_condition(spec: Any) -> str:
    """One condition, in words. `{'gte': 1.0}` becomes `is at least 1.0`."""
    words = {
        "eq": "is", "ne": "is not", "lt": "is below", "lte": "is at most",
        "gt": "is above", "gte": "is at least", "in": "is one of",
        "not_in": "is none of", "between": "is between", "contains": "contains",
    }
    if not isinstance(spec, dict):
        return f"is {spec!r}"
    return " and ".join(f"{words.get(op, op)} {want!r}" for op, want in spec.items())


def cmd_policy(args: argparse.Namespace) -> int:
    """Explain what a policy would do, rule by rule, in priority order.

    The point is answering "what will this actually do" without running an agent against
    it. Priority order is evaluation order and the first match is terminal, so reading top
    to bottom is reading the decision procedure.

    Resolves the pack the **config** names, not the packaged starter. The first version
    fell back to the default bundle whenever `--policy` was absent, so it cheerfully
    explained a policy the agent was not using — which is worse than explaining nothing.
    """
    from .settings import Config  # noqa: PLC0415

    if args.policy:
        bundle = load_bundle(args.policy)
    else:
        try:
            bundle = Config.load(getattr(args, "config", None)).policy()
        except (ConfigError, PolicyError) as exc:
            print(str(exc))
            return 1

    if args.json:
        print(json.dumps({
            "name": bundle.name,
            "hash": bundle.hash,
            "source": bundle.source,
            "derived": [d.as_dict() for d in bundle.derived],
            "rules": [r.as_dict() for r in bundle.sorted_rules()],
        }, indent=2))
        return 0

    print(f"{bundle.name}  {bundle.hash}  {len(bundle.rules)} rules")
    print(f"source: {bundle.source}")

    if bundle.derived:
        print()
        print("derived facts  -- composed from what the engines measure, in this order")
        for d in bundle.derived:
            print(f"  derived.{d.name}  = {d.combinator} of:")
            for clause in d.clauses:
                for fact, spec in clause.items():
                    print(f"      {fact} {_explain_condition(spec)}")

    print()
    print("rules, in evaluation order. The first match is terminal.")
    for rule in bundle.sorted_rules():
        print()
        print(f"  [{rule.priority:>5}] {rule.id}  ->  {rule.then}")
        if rule.reason:
            print(f"          because: {rule.reason}")
        if not rule.when:
            print("          matches: everything (this is a catch-all)")
        else:
            print("          matches when ALL of:")
            for fact, spec in rule.when.items():
                print(f"            {fact} {_explain_condition(spec)}")

    if not any(not r.when for r in bundle.rules):
        print()
        print("no catch-all rule. Anything unmatched fails closed to REVIEW.")
    return 0


def cmd_conformance(args: argparse.Namespace) -> int:
    """Score this implementation's journalled records against a profile.

    Two layers, and the difference matters. This checks *evidence completeness*: given the
    decisions in the journal, were the controls the profile requires actually exercised?
    The full AEGS-CONF suite is a separate package scoring an implementation against the
    standard's own cases, and it ships apart from the thing it tests on purpose -- a
    conformance suite bundled with its subject is not a conformance suite.
    """
    from . import record as record_mod  # noqa: PLC0415
    from .profiles import Profile  # noqa: PLC0415
    from .settings import Config  # noqa: PLC0415

    name = args.profile
    if name is None:
        try:
            name = Config.load(getattr(args, "config", None)).profile
        except (ConfigError, PolicyError) as exc:
            print(str(exc))
            return 1

    try:
        profile = Profile.load(name)
    except ConfigError as exc:
        print(str(exc))
        return 1

    aegoll = _aegl(args)
    try:
        records = record_mod.records_from_journal(aegoll.audit)
    finally:
        aegoll.close()

    assessments = [profile.assess(r) for r in records]
    non_conformant = [a for a in assessments if not a.conformant]

    if args.json:
        print(json.dumps({
            "profile": profile.id,
            "records": len(records),
            "conformant": len(records) - len(non_conformant),
            "assessments": [a.as_dict() for a in assessments],
        }, indent=2))
        return 1 if non_conformant else 0

    print(f"profile : {profile.id}  ({len(profile.required_controls())} required control(s))")
    print(f"records : {len(records)} from the journal")
    if not records:
        print()
        print("nothing to score yet. Run some decisions first -- try `aegoll decide`.")
        return 0
    print(f"conformant: {len(records) - len(non_conformant)}/{len(records)}")

    if not profile.enforces():
        print()
        print("this profile enforces nothing, so everything passes. That is what `none` means.")
        return 0

    for assessment in non_conformant[: args.limit]:
        print()
        for finding in assessment.findings:
            print(f"  {finding}")

    if not non_conformant:
        print()
        print("ok")
        return 0
    print()
    print(f"{len(non_conformant)} record(s) not conformant with {profile.id}")
    return 1


#: Exit codes, documented in docs/cli.md and asserted in tests/test_cli.py. A CLI that
#: signals every failure with the same number is a CLI nobody can script against.
EXIT_OK = 0
EXIT_INVALID = 1       # config or policy is unusable
EXIT_REFUSED = 2       # the layer worked and said no. Not an error
EXIT_CHAIN = 3         # the evidence chain is broken or unverifiable
EXIT_USAGE = 4         # the command line itself was wrong


class _Parser(argparse.ArgumentParser):
    """argparse with a usage exit code that does not collide with `refused`.

    argparse exits 2 on a bad command line, and 2 is `EXIT_REFUSED` here. A script
    checking `$? -eq 2` would read a typo as a governance decision, which is the worst
    possible confusion for this particular tool to hand someone.
    """

    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}" + chr(10))


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="aegoll", description="AEGL Phase 1 (deterministic)")
    p.add_argument("--policy", help="path to a policy bundle YAML")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="command", required=True, parser_class=_Parser)

    ini = sub.add_parser("init", help="scaffold aegoll.yaml and a starter policy")
    ini.add_argument("--dir", help="where to write (default: here)")
    ini.add_argument("--force", action="store_true", help="overwrite existing files")
    ini.set_defaults(func=cmd_init)

    chk = sub.add_parser("check", help="validate config and policy; exit 1 if invalid")
    chk.add_argument("--config", help="path to aegoll.yaml or aegoll.json")
    # Also accepted before the subcommand. Users type it after, so both work rather than
    # one being technically correct and the other an error message.
    chk.add_argument("--json", action="store_true", help="machine-readable output")
    chk.add_argument("--controls", action="store_true",
                     help="list the controls the active profile requires")
    chk.set_defaults(func=cmd_check)

    d = sub.add_parser("decide", help="decide one payment request")
    d.add_argument("--resource", default="/market/snapshot")
    d.add_argument("--amount", default="0.001", help="USD")
    d.add_argument("--vendor", default="x402-poc-desk")
    d.add_argument("--purpose", default="data_purchase",
                   choices=[x.value for x in Purpose])
    d.add_argument("--sanctioned", action="store_true")
    d.add_argument("--dry-run", action="store_true",
                   help="decide without journalling (uses in-memory history)")
    d.add_argument("--fixed-time", action="store_true", help="deterministic clock")
    d.set_defaults(func=cmd_decide)

    rep = sub.add_parser("report", help="what was spent, what was refused, and why")
    rep.add_argument("--config", help="path to aegoll.yaml or aegoll.json")
    rep.add_argument("--limit", type=int, default=20, help="decisions to show")
    rep.add_argument("--json", action="store_true", help="machine-readable output")
    rep.set_defaults(func=cmd_report)

    pex = sub.add_parser("policy", help="explain what a policy would do")
    pex.add_argument("action", nargs="?", default="explain", choices=["explain"])
    pex.add_argument("--config", help="path to aegoll.yaml or aegoll.json")
    pex.add_argument("--json", action="store_true", help="machine-readable output")
    pex.set_defaults(func=cmd_policy)

    con = sub.add_parser("conformance", help="score journalled records against a profile")
    con.add_argument("--profile", help="aegs-1 | aegs-2 | none (default: from config)")
    con.add_argument("--config", help="path to aegoll.yaml or aegoll.json")
    con.add_argument("--limit", type=int, default=10, help="non-conformant records to detail")
    con.add_argument("--json", action="store_true", help="machine-readable output")
    con.set_defaults(func=cmd_conformance)

    a = sub.add_parser("audit", help="verify the audit chain")
    a.add_argument("--tail", type=int, default=10)
    a.set_defaults(func=cmd_audit)

    r = sub.add_parser("replay", help="determinism check")
    r.set_defaults(func=cmd_replay)

    q = sub.add_parser("reviews", help="inspect or resolve the review queue")
    q.add_argument("--all", action="store_true")
    q.add_argument("--resolve", metavar="REQUEST_ID")
    q.add_argument("--as", dest="as_", default="approved",
                   choices=["approved", "denied", "expired"])
    q.add_argument("--note", default="")
    q.set_defaults(func=cmd_reviews)

    b = sub.add_parser("bench", help="measure decision latency and cost")
    b.add_argument("-n", type=int, default=2000)
    b.set_defaults(func=cmd_bench)

    rec = sub.add_parser("record", help="emit or validate AEGS Decision Records")
    rec.add_argument("--file", help="validate records from a file instead of the journal")
    rec.add_argument("--export", metavar="PATH", help="write records as JSON")
    rec.add_argument("--limit", type=int, default=10, help="max problems to print")
    rec.set_defaults(func=cmd_record)

    intent = sub.add_parser("intent", help="declare, list or revoke an economic intent")
    intent.add_argument("--declare", metavar="PURPOSE", help="declare a new intent")
    intent.add_argument("--agent", default="agent-1")
    intent.add_argument("--maximum", default="1.00",
                        help="total for the intent's whole life, not per action")
    intent.add_argument("--max-per-action", default=None)
    intent.add_argument("--asset", default="USDC")
    intent.add_argument("--resource", action="append",
                        help="allowed resource pattern, repeatable")
    intent.add_argument("--category", action="append")
    intent.add_argument("--channel", action="append", choices=["internal", "external"])
    intent.add_argument("--outcome", default=None, help="what the spend should produce")
    intent.add_argument("--expires-in", type=float, default=None, metavar="HOURS")
    intent.add_argument("--revoke", metavar="INTENT_ID")
    intent.set_defaults(func=cmd_intent)

    ident = sub.add_parser("identity", help="register or inspect an agent identity")
    ident.add_argument("--register", metavar="PURPOSE", help="register an identity")
    ident.add_argument("--agent", default="agent-1")
    ident.add_argument("--parent", default=None, help="delegating agent id")
    ident.add_argument("--controller", default=None,
                       help="stored, never disclosed to a counterparty")
    ident.add_argument("--controller-kind", default="organisation",
                       choices=["individual", "organisation", "agent", "unknown"])
    ident.add_argument("--network", action="append", help="authorised network, repeatable")
    ident.add_argument("--per-action", default=None)
    ident.add_argument("--daily", default=None)
    ident.add_argument("--risk-profile", default=None)
    ident.add_argument("--status", choices=["active", "suspended", "revoked"])
    ident.add_argument("--show", choices=["vendor", "auditor"],
                       help="print exactly what this audience would see")
    ident.set_defaults(func=cmd_identity)

    pol = sub.add_parser("policies", help="list available policy bundles")
    pol.set_defaults(func=cmd_policies)

    # --json on every subcommand, added here rather than on each parser by hand. A CLI
    # without machine output is a CLI nobody scripts, and a hand-maintained list is how
    # "every command" quietly becomes "most commands". Also accepted *before* the
    # subcommand, because both spellings are what users actually type.
    for name, parser in sub.choices.items():
        if not any("--json" in a.option_strings for a in parser._actions):
            parser.add_argument(
                "--json", action="store_true", help="machine-readable output"
            )

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # `aegoll --json report` and `aegoll report --json` must behave identically. argparse
    # parses the global flag first and then lets the subparser's default overwrite it with
    # False, so without this the pre-command spelling silently stopped working the moment
    # per-command flags were added. Caught by a test rather than by a user.
    if not getattr(args, "json", False):
        raw = sys.argv[1:]
        if "--json" in raw:
            args.json = True

    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
