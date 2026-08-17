"""Headless entry point: `python -m aegl.cli <command>`.

    decide       one-off decision, full engine breakdown
    scenarios    run A-D2 and report
    audit        verify the hash chain
    replay       determinism check against the journal
    reviews      list / resolve the review queue
    bench        latency and cost-per-decision measurement
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
from .config import available_bundles, load_bundle
from .runtime import Aegl, Paths
from .scenarios import BASE_TIME, run_all
from .domain import Purpose, Vendor, fmt_usd


def _aegl(args: argparse.Namespace, ephemeral: bool = False) -> Aegl:
    bundle = load_bundle(args.policy) if args.policy else load_bundle()
    paths = Paths.ephemeral(".data-cli") if ephemeral else Paths.under()
    clock = FixedClock(BASE_TIME) if getattr(args, "fixed_time", False) else None
    return Aegl(bundle=bundle, paths=paths, clock=clock)


def cmd_decide(args: argparse.Namespace) -> int:
    aegl = _aegl(args, ephemeral=args.dry_run)
    try:
        vendor = Vendor(id=args.vendor, name=args.vendor, sanctioned=args.sanctioned)
        request = aegl.build_request(
            resource=args.resource,
            amount_usd=args.amount,
            vendor=vendor,
            purpose=Purpose(args.purpose),
        )
        decision = aegl.decide(request) if args.dry_run else aegl.authorize(request)

        if args.json:
            print(json.dumps(decision.as_dict(), indent=2))
            return 0 if decision.approved else 2

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
        return 0 if decision.approved else 2
    finally:
        aegl.close()


def cmd_scenarios(args: argparse.Namespace) -> int:
    bundle = load_bundle(args.policy) if args.policy else load_bundle()
    results = run_all(bundle)

    if args.json:
        print(json.dumps([r.as_dict() for r in results], indent=2))
        return 0 if all(r.passed for r in results) else 1

    print(f"policy: {bundle.name} ({bundle.hash})\n")
    for r in results:
        d = r.as_dict()
        mark = "PASS" if d["passed"] else "FAIL"
        mode = "LIVE-CAPABLE" if r.scenario.live else "simulated"
        print(
            f"{mark}  {d['key']:3} {d['title'][:34]:34} {mode:13} "
            f"${d['amountUsd']:>9} -> {d['actual']:9} ({d['matchedRule']})"
        )
        print(
            f"      trust={d['trust']:.2f} risk={d['risk']:.2f} "
            f"flags={d['riskFlags']} latency={d['latencyUs']:.0f}us"
        )
        for note in r.notes:
            print(f"      note: {note}")
        print()

    ok = all(r.passed for r in results)
    print(f"{sum(r.passed for r in results)}/{len(results)} scenarios matched expectations")

    if bundle.name != "default":
        divergent = [r.scenario.key for r in results if not r.passed]
        print(
            f"\nnote: scenario expectations are calibrated against the `default` bundle. "
            f"Under `{bundle.name}` the divergences ({', '.join(divergent) or 'none'}) are "
            "not failures -- they are the measured cost of a stricter policy, i.e. its "
            "false-reject rate on traffic default would have allowed."
        )
    if any(not s.scenario.live for s in results):
        print(
            "\nreminder: only scenario A can run against the real x402 seller "
            "($0.001-$0.01). B-D2 use simulated vendors through the same decide() path."
        )
    # A non-default bundle diverging is expected, so do not fail the process on it.
    return 0 if (ok or bundle.name != "default") else 1


def cmd_audit(args: argparse.Namespace) -> int:
    aegl = _aegl(args)
    try:
        entries = aegl.audit.entries()
        ok, problems = aegl.audit.verify()
        print(f"audit: {len(entries)} entries at {aegl.paths.audit}")
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
        return 0 if ok else 1
    finally:
        aegl.close()


def cmd_replay(args: argparse.Namespace) -> int:
    aegl = _aegl(args)
    try:
        result = aegl.replay()
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    finally:
        aegl.close()


def cmd_reviews(args: argparse.Namespace) -> int:
    aegl = _aegl(args)
    try:
        if args.resolve:
            item = aegl.queue.resolve(args.resolve, args.as_, by="cli", note=args.note)
            print("resolved:" if item else "not found:", args.resolve)
            return 0 if item else 1
        items = aegl.queue.all() if args.all else aegl.queue.pending()
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
        aegl.close()


def cmd_bench(args: argparse.Namespace) -> int:
    """The cost-and-latency claim, measured rather than asserted."""
    aegl = _aegl(args, ephemeral=True)
    try:
        vendor = Vendor(id="bench-vendor", name="Bench Vendor")
        latencies = []
        started = time.perf_counter()
        for _ in range(args.n):
            req = aegl.build_request(
                resource="/market/snapshot", amount_usd="0.001", vendor=vendor
            )
            latencies.append(aegl.decide(req).latency_us)
        wall = time.perf_counter() - started
        latencies.sort()

        def pct(p: float) -> float:
            return latencies[min(len(latencies) - 1, int(p * len(latencies)))]

        print(f"decisions      : {args.n}")
        print(f"wall clock     : {wall:.3f}s  ({args.n / wall:,.0f} decisions/sec)")
        print(f"latency p50    : {statistics.median(latencies):.0f} us")
        print(f"latency p99    : {pct(0.99):.0f} us")
        print(f"latency max    : {latencies[-1]:.0f} us")
        print("inference cost : $0.000000 (no model was invoked)")
        print(f"p99 under 1ms  : {pct(0.99) < 1000}")
        return 0
    finally:
        aegl.close()


def cmd_policies(args: argparse.Namespace) -> int:
    for p in available_bundles():
        b = load_bundle(p)
        print(f"  {b.name:12} {b.hash}  {len(b.rules)} rules  {p}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    """Measure how often an advisor blocks traffic that should have passed.

    Costs money: one advisor call per case. The deterministic baseline
    (`--advisor none`) is free and shows what the engines alone decide.
    """
    from .evaluation import CASES, evaluate_advisor

    bundle = load_bundle(args.policy) if args.policy else load_bundle()
    reports = []
    for spec in args.advisor or ["none"]:
        if spec == "none":
            provider = model = None
        elif "/" in spec:
            provider, model = spec.split("/", 1)
        else:
            print(f"  bad --advisor {spec!r}; expected provider/model or 'none'")
            return 2
        try:
            report = evaluate_advisor(provider, model, bundle)
        except Exception as exc:  # noqa: BLE001 - one bad key must not kill the run
            print(f"  {spec:44} SKIPPED  {exc}")
            continue
        reports.append(report)

        if not args.json:
            print(f"\n  {report.provider}/{report.model}")
            print(f"  {'-' * 74}")
            for r in report.results:
                mark = "FALSE-BLOCK" if r.false_block else ("ok" if r.passed_through else "blocked")
                said = (r.as_dict()["advisorSaid"] or "-")[:8]
                print(
                    f"    {r.case.key:24} {r.case.category:9} "
                    f"{r.deterministic:8} advisor={said:8} final={r.final:8} {mark}"
                )
            print(
                f"  false-block {report.false_blocks}/{len(report.good)} "
                f"({report.false_block_rate:.0%})   "
                f"caught {report.catch_rate:.0%} of bad   "
                f"ambiguous blocked {report.ambiguous_blocked}/{len(report.ambiguous)}   "
                f"${report.total_cost_usd:.5f}  {report.mean_latency_ms:.0f}ms avg"
                + "  mean tokens {:.0f} in / {:.0f} out".format(*report.mean_tokens)
                + (f"  errors {report.errors}" if report.errors else "")
            )

    if args.json:
        print(json.dumps({"cases": len(CASES),
                          "advisors": [r.as_dict() for r in reports]}, indent=2))
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
        aegl = _aegl(args)
        try:
            records = record_mod.records_from_journal(aegl.audit.entries())
        finally:
            aegl.close()
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

    aegl = _aegl(args)
    try:
        if args.revoke:
            ok = aegl.intents.revoke(args.revoke)
            print(f"  {'revoked' if ok else 'not found'}: {args.revoke}")
            return 0 if ok else 1

        if args.declare:
            expires = (
                aegl.clock.now() + timedelta(hours=args.expires_in)
                if args.expires_in
                else None
            )
            intent = aegl.intents.declare(
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
                now=aegl.clock.now(),
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

        intents = aegl.intents.all()
        if args.json:
            print(json.dumps([i.as_dict() for i in intents], indent=2))
            return 0
        if not intents:
            print("  no intents declared -- actions are ungoverned by intent, and "
                  "their records say so")
            return 0
        for i in intents:
            spent = aegl.store.spent_under_intent(i.intent_id)
            print(
                f"  {i.intent_id:20} {i.status:9} {fmt_usd(spent)} of "
                f"{fmt_usd(i.maximum_atomic)}  {i.purpose[:38]}"
            )
        return 0
    finally:
        aegl.close()


def cmd_identity(args: argparse.Namespace) -> int:
    """Register, list or change the status of an agent identity.

    Pseudonymous by default. `--controller` is stored but never disclosed to a
    counterparty, and `--show vendor` prints exactly what one would see.
    """
    from .identity import Party  # noqa: PLC0415

    aegl = _aegl(args)
    try:
        if args.status:
            ok = aegl.identities.set_status(args.agent, args.status)
            print(f"  {'set ' + args.status if ok else 'not found'}: {args.agent}")
            return 0 if ok else 1

        if args.register:
            identity = aegl.identities.register(
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
                policy_version=aegl.bundle.hash,
                now=aegl.clock.now(),
            )
            print(f"  registered {identity.agent_id}")
            print(f"    purpose    : {identity.purpose}")
            print(f"    controller : "
                  f"{'set (never disclosed to counterparties)' if identity.controller else 'none'}")
            print(f"    per action : "
                  f"{fmt_usd(identity.per_action_atomic) if identity.per_action_atomic else 'policy only'}")
            return 0

        identities = aegl.identities.all()
        if args.show:
            target = aegl.identities.get(args.agent)
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
        aegl.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aegl", description="AEGL Phase 1 (deterministic)")
    p.add_argument("--policy", help="path to a policy bundle YAML")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="command", required=True)

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

    s = sub.add_parser("scenarios", help="run scenarios A-D2")
    s.set_defaults(func=cmd_scenarios)

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

    e = sub.add_parser("eval", help="measure advisor false-block rate (costs money)")
    e.add_argument("--advisor", action="append", metavar="PROVIDER/MODEL",
                   help="repeatable; 'none' for the deterministic baseline")
    e.set_defaults(func=cmd_eval)

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

    return p


def main() -> None:
    args = build_parser().parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
