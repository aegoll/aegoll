"""AEGL cockpit -- one panel per engine.

    aegl/.venv/Scripts/streamlit run aegl/aegl/app.py --server.port 8502

Runs on its own port, separate from the agent cockpits, so the governance layer
and the agent stay isolated.

The point of this UI is that a governance layer you cannot inspect is not
auditable. Every engine shows its inputs, its output, and the weighted terms that
produced it -- so when a verdict changes you can see *which* engine changed it.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import streamlit as st

# `streamlit run` executes this as a top-level script, not a package module.
_AEGL_ROOT = str(Path(__file__).resolve().parents[1])
if _AEGL_ROOT not in sys.path:
    sys.path.insert(0, _AEGL_ROOT)

from aegl.config import available_bundles, load_bundle  # noqa: E402
from aegl.runtime import Aegl, Paths  # noqa: E402
from aegl.scenarios import (  # noqa: E402
    KNOWN_VENDOR,
    NEW_VENDOR,
    POC_VENDOR,
    SHADY_VENDOR,
    SCENARIOS,
    run_scenario,
)
from aegl.domain import (  # noqa: E402
    Purpose,
    Vendor,
    Verdict,
    atomic_to_usd,
    fmt_usd,
)

st.set_page_config(page_title="AEGL cockpit", page_icon="🛡", layout="wide")

VERDICT_STYLE = {
    Verdict.APPROVE: ("success", "APPROVE", "payment authorized; x402 may settle"),
    Verdict.REVIEW: ("warning", "REVIEW", "pausable — queued for a human decision"),
    Verdict.ESCALATE: ("error", "ESCALATE", "blocking — the agent cannot proceed"),
    Verdict.REJECT: ("error", "REJECT", "refused outright"),
}

PRESET_VENDORS = {
    "x402 POC Data Desk (the real seller)": POC_VENDOR,
    "Acme Compute (long clean record)": KNOWN_VENDOR,
    "NewCo Analytics (no history)": NEW_VENDOR,
    "Unknown Counterparty 7f2a (disputes + failures)": SHADY_VENDOR,
}

PRESET_RESOURCES = [
    "/market/snapshot",
    "/market/signal",
    "/market/ohlcv/ETH-USD",
    "/compute/batch",
    "/analytics/bespoke",
    "/data/feed",
]


# --------------------------------------------------------------------------
# sidebar
# --------------------------------------------------------------------------
st.sidebar.header("Governance configuration")

bundles = available_bundles()
bundle_names = [p.stem for p in bundles]
choice = st.sidebar.selectbox(
    "Policy bundle",
    bundle_names,
    index=bundle_names.index("default") if "default" in bundle_names else 0,
    help="Config and rules share one content hash, recorded in every audit entry.",
)
bundle = load_bundle(bundles[bundle_names.index(choice)])

st.sidebar.caption(f"`{bundle.name}` · hash `{bundle.hash}` · {len(bundle.rules)} rules")

persist = st.sidebar.toggle(
    "Journal decisions",
    value=False,
    help="Off (default): decide only, nothing written — the playground stays a dry run. "
    "On: decisions are recorded to history, the audit chain and the review queue.",
)

st.sidebar.divider()
st.sidebar.subheader("Treasury envelopes")
t = bundle.treasury
for label, value in [
    ("balance", t.balance_atomic),
    ("per transaction", t.per_tx_atomic),
    ("daily", t.daily_atomic),
    ("monthly", t.monthly_atomic),
    ("per vendor / 30d", t.per_vendor_30d_atomic),
    ("emergency reserve", t.emergency_reserve_atomic),
]:
    st.sidebar.caption(f"{label}: **{fmt_usd(value)}**")
st.sidebar.caption(f"velocity: **{t.velocity_60s}/60s**, **{t.velocity_1h}/h**")

st.sidebar.divider()
if st.sidebar.button("Reset journal, history and queue"):
    p = Paths.under()
    for f in (p.audit, p.review, p.history):
        if f.exists() and f.name != ":memory:":
            f.unlink()
    st.rerun()



# --------------------------------------------------------------------------
# BYOK keys (Phase 2 advisors)
# --------------------------------------------------------------------------
from aegl.advisors import advisor_catalogue_safe  # noqa: E402

st.sidebar.divider()
st.sidebar.subheader("Advisor keys (BYOK)")
_cat = advisor_catalogue_safe()
_ready = [c for c in _cat if c["keyPresent"]]
st.sidebar.caption(
    f"{len(_ready)}/{len(_cat)} providers configured. Phase 1 needs none -- the "
    "deterministic engines decide for free. Keys only matter when you want a "
    "second opinion on high-exposure transactions."
)
for _c in _cat:
    _mark = "✅" if _c["keyPresent"] else "⬜"
    _src = {"runtime": "in memory", "env": ".env", "none": "not set"}.get(_c["source"], _c["source"])
    st.sidebar.caption(f"{_mark} **{_c['provider']}** · `{_c['envKey']}` · {_src}")
st.sidebar.caption(
    "Enter keys in the agent cockpit on port 8501 -- it owns the key form so there "
    "is one place to manage them."
)


@st.cache_resource(show_spinner=False)
def _runtime(policy_hash: str, persistent: bool) -> Aegl:
    """One Aegl per (policy, persistence) combination.

    Cached because the SQLite connection should not be rebuilt on every rerun.
    """
    b = load_bundle(bundles[bundle_names.index(choice)])
    paths = Paths.under() if persistent else Paths.ephemeral(".data-playground")
    return Aegl(bundle=b, paths=paths)


aegl = _runtime(bundle.hash, persist)


# --------------------------------------------------------------------------
# header
# --------------------------------------------------------------------------
st.title("AEGL cockpit")
st.caption(
    "Autonomous Economic Governance Layer, Phase 1 — deterministic. "
    "x402 answers *how* the agent pays; AEGL answers *whether it should*, "
    "using rules, budgets, scores and arithmetic only. No model is ever invoked, "
    "so a decision costs nothing and takes microseconds."
)

summary = aegl.summary()
h = st.columns(5)
h[0].metric("Inference cost", "$0.000000", "no model invoked")
h[1].metric("Decisions journalled", summary["auditEntries"])
h[2].metric("Pending reviews", summary["pendingReviews"])
h[3].metric("Audit chain", "valid" if summary["auditOk"] else "BROKEN")
h[4].metric("Policy", summary["policy"], summary["policyHash"])

if not persist:
    st.info(
        "**Dry-run mode.** Decisions are computed but nothing is written. Turn on "
        "*Journal decisions* in the sidebar to record to history, the audit chain "
        "and the review queue."
    )

tabs = st.tabs(
    [
        "Decision playground",
        "Treasury",
        "Policy",
        "Trust & Risk",
        "ROI & EIAP",
        "Scenarios",
        "Review queue",
        "Audit",
        "Cross-framework",
    ]
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def verdict_banner(decision: Any) -> None:
    kind, label, blurb = VERDICT_STYLE[decision.verdict]
    getattr(st, kind)(
        f"### {label}\n{blurb}  \n"
        f"matched rule `{decision.matched_rule}` · decided in "
        f"**{decision.latency_us:.0f} µs** · hash `{decision.decision_hash}`"
    )


def term_table(score: Any) -> list[dict[str, Any]]:
    rows = [
        {
            "term": t.name,
            "value": round(t.value, 4),
            "weight": t.weight,
            "contribution": round(t.contribution, 4),
            "why": t.detail,
        }
        for t in score.terms
    ]
    rows.append(
        {
            "term": "TOTAL",
            "value": "",
            "weight": "",
            "contribution": round(score.value, 4),
            "why": f"flags: {', '.join(score.flags) or 'none'}",
        }
    )
    return rows


def envelope_rows(budget: Any) -> list[dict[str, Any]]:
    """One row per envelope. Per-call caps are marked, not shown as 0% used.

    `per_transaction` never accumulates -- it is a ceiling checked fresh against
    each request -- so its `used` is permanently zero. Printing "0.0%" beside the
    rolling windows reads as "nothing has been spent", which is the opposite of
    what the row means.
    """
    return [
        {
            "envelope": e.name,
            "window": e.window,
            "limit": fmt_usd(e.limit_atomic),
            "used": fmt_usd(e.used_atomic) if e.cumulative else "— per call",
            "headroom": fmt_usd(e.headroom_atomic),
            "used %": f"{e.utilisation * 100:.1f}%" if e.cumulative else "n/a",
            "binding": "◀" if budget.binding == e.name else "",
        }
        for e in budget.envelopes
    ]


# --------------------------------------------------------------------------
# 1. Decision playground
# --------------------------------------------------------------------------
with tabs[0]:
    st.subheader("Decide a payment request")
    st.caption(
        "Change an amount by a cent and watch which engine flips the verdict. "
        "That is the whole argument for deterministic governance: the reason is "
        "always attributable to a named rule or envelope."
    )

    c1, c2, c3 = st.columns([2, 2, 1])
    vendor_label = c1.selectbox("Vendor", list(PRESET_VENDORS))
    vendor: Vendor = PRESET_VENDORS[vendor_label]
    resource = c2.selectbox("Resource", PRESET_RESOURCES)
    purpose = c3.selectbox("Purpose", [p.value for p in Purpose], index=0)

    c4, c5, c6 = st.columns([2, 2, 1])
    amount = c4.text_input("Amount (USD)", value="0.001")
    seed_choice = c5.selectbox(
        "Seeded history",
        ["none", "thin (12 micro purchases)", "trusted vendor (30 settled)", "suspicious"],
        help="History changes trust, risk and budget headroom — the same request can "
        "decide differently against a different past.",
    )
    sanctioned = c6.checkbox("Sanctioned", value=vendor.sanctioned)

    if st.button("Decide", type="primary"):
        from aegl.scenarios import (
            BASE_TIME,
            seed_suspicious,
            seed_thin_history,
            seed_trusted_vendor,
        )
        from aegl.clock import FixedClock

        seeder = {
            "none": None,
            "thin (12 micro purchases)": seed_thin_history,
            "trusted vendor (30 settled)": seed_trusted_vendor,
            "suspicious": seed_suspicious,
        }[seed_choice]

        run = Aegl(
            bundle=bundle,
            paths=Paths.ephemeral(".data-playground-run"),
            clock=FixedClock(BASE_TIME),
        )
        try:
            if seeder:
                seeder(run.store, BASE_TIME)
            req = run.build_request(
                resource=resource,
                amount_usd=amount,
                vendor=Vendor(
                    id=vendor.id, name=vendor.name, sanctioned=sanctioned, tags=vendor.tags
                ),
                purpose=Purpose(purpose),
            )
            snapshot = run.snapshot_for(req)
            decision = run.governor.decide(req, snapshot)
            facts, rule_result = run.governor.evaluate_rules(req, snapshot)

            st.session_state["pg"] = {
                "decision": decision,
                "facts": facts,
                "rules": rule_result,
                "request": req,
            }
            if persist:
                aegl.authorize(
                    aegl.build_request(
                        resource=resource,
                        amount_usd=amount,
                        vendor=Vendor(id=vendor.id, name=vendor.name, sanctioned=sanctioned),
                        purpose=Purpose(purpose),
                    )
                )
        finally:
            run.close()

    pg = st.session_state.get("pg")
    if not pg:
        st.info("Pick a request and press **Decide**.")
    else:
        decision = pg["decision"]
        verdict_banner(decision)

        m = st.columns(5)
        m[0].metric("Trust", f"{decision.trust.value:.3f}")
        m[1].metric("Risk", f"{decision.risk.value:.3f}")
        m[2].metric(
            "ROI",
            f"{decision.roi.ratio:.2f}x" if decision.roi.ratio is not None else "unknown",
        )
        m[3].metric("Budget", "ok" if decision.budget.ok else f"blocked: {decision.budget.binding}")
        m[4].metric(
            "Would use AI?",
            "yes" if decision.intelligence.eiap.would_invoke else "no",
            decision.intelligence.eiap.would_tier.value,
        )

        st.markdown("#### Why — every engine that spoke")
        for r in decision.reasons:
            icon = {"treasury": "💰", "policy": "📜", "risk": "⚠️", "trust": "🤝",
                    "eiap": "🧠", "authorize": "⚖️"}.get(r.source, "•")
            st.markdown(f"{icon} **`{r.source}/{r.code}`** — {r.detail}")

        st.markdown("#### Per-engine detail")
        with st.expander("💰 Treasury — envelopes and headroom", expanded=False):
            st.dataframe(envelope_rows(decision.budget), width="stretch", hide_index=True)
            st.dataframe(
                [c.as_dict() for c in decision.budget.counters],
                width="stretch",
                hide_index=True,
            )
        with st.expander("🤝 Trust — weighted terms", expanded=False):
            st.dataframe(term_table(decision.trust), width="stretch", hide_index=True)
        with st.expander("⚠️ Risk — weighted terms", expanded=False):
            st.dataframe(term_table(decision.risk), width="stretch", hide_index=True)
            if decision.risk.flags:
                st.warning("flags: " + ", ".join(decision.risk.flags))
        with st.expander("📈 ROI", expanded=False):
            st.json(decision.roi.as_dict())
            if not decision.roi.known:
                st.info(
                    "No declared expected value for this resource, so ROI is honestly "
                    "`unknown` rather than guessed. Inferring value from a service "
                    "description is Phase 2's job."
                )
        with st.expander("📜 Policy — the fact base rules matched against", expanded=False):
            st.json(pg["facts"])
        with st.expander("🧠 EIAP — would a model have been worth it?", expanded=False):
            st.json(decision.intelligence.eiap.as_dict())


# --------------------------------------------------------------------------
# 2. Treasury
# --------------------------------------------------------------------------
with tabs[1]:
    st.subheader("Treasury — engine 1")
    st.caption(
        "Every envelope is evaluated, not just the first that fails, so the binding "
        "constraint is always identifiable. All arithmetic is in integer atomic USDC "
        "units — no float touches the money path."
    )
    pg = st.session_state.get("pg")
    if not pg:
        st.info("Run a decision in the playground to populate this.")
    else:
        budget = pg["decision"].budget
        st.metric("Verdict", "within all envelopes" if budget.ok else f"blocked by {budget.binding}")
        for e in budget.envelopes:
            binding = budget.binding == e.name
            st.markdown(
                f"**{e.name}** · {e.window} — {fmt_usd(e.used_atomic)} of "
                f"{fmt_usd(e.limit_atomic)} used, {fmt_usd(e.headroom_atomic)} headroom"
                + ("  ← **binding**" if binding else "")
            )
            st.progress(min(1.0, e.utilisation))
        st.markdown("##### Velocity counters")
        st.dataframe(
            [c.as_dict() for c in budget.counters], width="stretch", hide_index=True
        )
        st.markdown("##### Earned authority")
        st.caption(
            "A clean settlement record raises the per-transaction ceiling; a single "
            "dispute revokes it immediately. Authority is earned continuously and lost "
            "at once, which is the conservative direction."
        )
        st.dataframe(
            [
                {"settled at least": n, "per-tx multiplier": f"x{m}",
                 "per-tx limit": fmt_usd(int(t.per_tx_atomic * m))}
                for n, m in sorted(t.tiers)
            ]
            or [{"settled at least": "-", "per-tx multiplier": "none configured"}],
            width="stretch",
            hide_index=True,
        )


# --------------------------------------------------------------------------
# 3. Policy
# --------------------------------------------------------------------------
with tabs[2]:
    st.subheader("Policy — engine 2")
    st.caption(
        "Rules evaluate in priority order; the first match is terminal. There is no "
        "`eval` — only a fixed comparator vocabulary — so a policy file cannot become "
        "code. A rule can narrow a verdict that treasury or risk already set, never "
        "widen it."
    )
    pg = st.session_state.get("pg")
    rules_result = pg["rules"] if pg else None

    rows = []
    for ev in (rules_result.evaluations if rules_result else []):
        rows.append(
            {
                "": "✅" if ev.matched else ("·" if ev.failed_detail == "not reached" else "✗"),
                "priority": ev.rule.priority,
                "rule": ev.rule.id,
                "then": ev.rule.then,
                "why not": (
                    ""
                    if ev.matched
                    else (
                        "not reached (an earlier rule matched)"
                        if ev.failed_detail == "not reached"
                        else f"{ev.failed_clause}: {ev.failed_detail}"
                    )
                ),
            }
        )
    if rows:
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.dataframe(
            [
                {"priority": r.priority, "rule": r.id, "then": r.then, "reason": r.reason}
                for r in bundle.sorted_rules()
            ],
            width="stretch",
            hide_index=True,
        )
        st.info("Run a decision in the playground to see which rule matched and why.")

    with st.expander("Raw bundle"):
        st.code(Path(bundle.source).read_text(encoding="utf-8"), language="yaml")


# --------------------------------------------------------------------------
# 4. Trust & Risk
# --------------------------------------------------------------------------
with tabs[3]:
    st.subheader("Trust — engine 3 · Risk — engine 4")
    st.caption(
        "Both are weighted sums of named terms, never black boxes. Deliberately not "
        "machine learning: a score nobody can explain is useless in an audit and "
        "impossible to appeal."
    )
    pg = st.session_state.get("pg")
    if not pg:
        st.info("Run a decision in the playground to populate this.")
    else:
        d = pg["decision"]
        left, right = st.columns(2)
        with left:
            st.metric("Trust", f"{d.trust.value:.4f}")
            st.dataframe(term_table(d.trust), width="stretch", hide_index=True)
            st.caption(
                f"Cold start is {bundle.trust.cold_start} — an unknown vendor is "
                "deliberately not trusted, which is what makes the suspicious scenario "
                "behave without special-casing."
            )
        with right:
            st.metric("Risk", f"{d.risk.value:.4f}")
            st.dataframe(term_table(d.risk), width="stretch", hide_index=True)
            st.caption(
                "The `reprice` term compares against the *vendor's own* historical price "
                "for this resource — a seller that silently charges 10x is caught here, "
                "where no absolute threshold would notice."
            )


# --------------------------------------------------------------------------
# 5. ROI & EIAP
# --------------------------------------------------------------------------
with tabs[4]:
    st.subheader("ROI — engine 5 · Economic Intelligence Activation Policy")
    pg = st.session_state.get("pg")
    if not pg:
        st.info("Run a decision in the playground to populate this.")
    else:
        d = pg["decision"]
        e = d.intelligence.eiap

        st.markdown("#### ROI")
        st.json(d.roi.as_dict())

        st.markdown("#### EIAP — computed on every transaction, acted on never")
        st.caption(
            "Phase 1's cheapest research artifact. We evaluate whether invoking a model "
            "*would* be rational, log the answer, and do nothing with it — which "
            "calibrates Phase 2's thresholds against real traffic before a token is spent."
        )
        k = st.columns(4)
        k[0].metric("Exposure", fmt_usd(e.exposure_atomic))
        k[1].metric("Expected gain", fmt_usd(e.expected_gain_atomic))
        k[2].metric("AI cost", fmt_usd(e.ai_cost_atomic))
        k[3].metric(
            "Break-even exposure",
            fmt_usd(e.break_even_exposure_atomic),
            "invoke above this",
        )
        if e.would_invoke:
            st.warning(
                f"Phase 2 would consult a **{e.would_tier.value}** model here — expected "
                f"gain {fmt_usd(e.expected_gain_atomic)} exceeds the "
                f"{fmt_usd(e.ai_cost_atomic)} analysis cost. Phase 1 decided "
                "deterministically anyway."
            )
        else:
            st.success(
                f"No model is justified: exposure {fmt_usd(e.exposure_atomic)} is below "
                f"the {fmt_usd(e.break_even_exposure_atomic)} break-even. Invoking one "
                "would destroy value."
            )
        st.dataframe(
            [t.as_dict() for t in e.terms], width="stretch", hide_index=True
        )
        st.markdown(
            f"""
**The formula.** `invoke ⟺ E[Δ quality] × exposure > cost_ai`, so
`break_even = cost_ai / p_flip`. With the measured Haiku 4.5 cost of
{fmt_usd(bundle.eiap.ai_cost_atomic)} per analysis and a generous
`p_flip` ceiling of {bundle.eiap.max_p_flip}, break-even lands near
**{fmt_usd(int(bundle.eiap.ai_cost_atomic / bundle.eiap.max_p_flip))}** —
an order of magnitude above every price the real x402 seller charges
($0.001–$0.01). That is the quantitative core of the deterministic-first argument.
"""
        )


# --------------------------------------------------------------------------
# 6. Scenarios
# --------------------------------------------------------------------------
with tabs[5]:
    st.subheader("Scenarios")
    st.caption(
        "The seller in this repo sells $0.001–$0.01, so **only scenario A can run "
        "against a real 402 and a real settlement**. B–D2 use simulated vendors and "
        "seeded history through the same `decide()` path. Every row says which."
    )
    if st.button("Run all scenarios"):
        started = time.perf_counter()
        st.session_state["scenarios"] = [run_scenario(s, bundle) for s in SCENARIOS]
        st.session_state["scenario_wall"] = time.perf_counter() - started

    results = st.session_state.get("scenarios")
    if results:
        st.metric(
            "All matched expectations",
            "yes" if all(r.passed for r in results) else "NO",
            f"{len(results)} scenarios in {st.session_state['scenario_wall'] * 1000:.0f} ms",
        )
        st.dataframe(
            [
                {
                    "": "✅" if r.passed else "❌",
                    "key": r.scenario.key,
                    "scenario": r.scenario.title,
                    "mode": "LIVE-CAPABLE" if r.scenario.live else "simulated",
                    "amount": f"${r.scenario.amount_usd}",
                    "verdict": r.decision.verdict.value,
                    "rule": r.decision.matched_rule,
                    "trust": round(r.decision.trust.value, 2),
                    "risk": round(r.decision.risk.value, 2),
                    "would use AI": r.decision.intelligence.eiap.would_invoke,
                    "µs": round(r.decision.latency_us),
                }
                for r in results
            ],
            width="stretch",
            hide_index=True,
        )
        for r in results:
            with st.expander(f"{r.scenario.key} — {r.scenario.title}"):
                st.caption(r.scenario.rationale)
                for line in r.decision.explain():
                    st.markdown(f"- {line}")
                for note in r.notes:
                    st.caption(f"note: {note}")
    else:
        st.info("Press **Run all scenarios**.")


# --------------------------------------------------------------------------
# 7. Review queue
# --------------------------------------------------------------------------
with tabs[6]:
    st.subheader("Review queue — where a human closes the loop")
    st.caption(
        "`REVIEW` is pausable: the item waits and a human answers later. `ESCALATE` is "
        "blocking: the agent cannot proceed. Both land here so nothing is silently "
        "dropped, but they are reported distinctly."
    )
    pending = aegl.queue.pending()
    if not pending:
        st.success("Nothing pending.")
        if not persist:
            st.caption(
                "Note: dry-run mode does not enqueue. Turn on *Journal decisions* to "
                "route REVIEW/ESCALATE verdicts here."
            )
    for item in pending:
        with st.container(border=True):
            head = st.columns([3, 1, 1, 1])
            head[0].markdown(f"**`{item.request_id}`** · {item.resource}")
            head[1].metric("Amount", f"${item.amount_usd:.6f}")
            head[2].metric("Verdict", item.verdict)
            head[3].metric("Type", "BLOCKING" if item.blocking else "pausable")
            for r in item.reasons:
                st.caption(r)
            note = st.text_input("Note", key=f"note-{item.request_id}")
            b1, b2 = st.columns(2)
            if b1.button("Approve", key=f"ok-{item.request_id}"):
                aegl.queue.resolve(item.request_id, "approved", by="cockpit", note=note)
                st.rerun()
            if b2.button("Deny", key=f"no-{item.request_id}"):
                aegl.queue.resolve(item.request_id, "denied", by="cockpit", note=note)
                st.rerun()

    resolved = [i for i in aegl.queue.all() if i.resolution != "pending"]
    if resolved:
        with st.expander(f"Resolved ({len(resolved)})"):
            st.dataframe(
                [
                    {
                        "request": i.request_id,
                        "verdict": i.verdict,
                        "amount": f"${i.amount_usd:.6f}",
                        "resolution": i.resolution,
                        "by": i.resolved_by,
                        "note": i.note,
                    }
                    for i in resolved
                ],
                width="stretch",
                hide_index=True,
            )


# --------------------------------------------------------------------------
# 8. Audit
# --------------------------------------------------------------------------
with tabs[7]:
    st.subheader("Audit — engine 8")
    st.caption(
        "Append-only JSONL where every record carries its predecessor's hash, so "
        "editing or deleting any past record breaks the chain from that point on. "
        "Deliberately not a database: a flat file is verifiable by a third party "
        "without our code."
    )
    ok, problems = aegl.audit.verify()
    entries = aegl.audit.entries()

    a1, a2, a3 = st.columns(3)
    a1.metric("Entries", len(entries))
    a2.metric("Chain", "VALID" if ok else "BROKEN")
    a3.metric("File", aegl.paths.audit.name)

    if problems:
        for p in problems:
            st.error(p)

    b1, b2 = st.columns(2)
    if b1.button("Verify chain"):
        ok2, probs2 = aegl.audit.verify()
        (st.success if ok2 else st.error)(
            "chain valid" if ok2 else f"{len(probs2)} problem(s): " + "; ".join(probs2)
        )
    if b2.button("Replay determinism check"):
        st.json(aegl.replay())

    if entries:
        st.dataframe(
            [
                {
                    "seq": e.seq,
                    "at": e.at[:19],
                    "verdict": e.verdict,
                    "resource": (e.payload.get("transaction") or {}).get("resource", "-"),
                    "amount": f"${(e.payload.get('transaction') or {}).get('amountUsd', 0):.6f}",
                    "prev": e.prev_hash,
                    "hash": e.entry_hash,
                }
                for e in reversed(entries[-50:])
            ],
            width="stretch",
            hide_index=True,
        )
        with st.expander("Newest entry, raw"):
            st.json(entries[-1].as_dict())
    else:
        st.info(
            "Journal is empty. Turn on *Journal decisions* in the sidebar and make a "
            "decision."
        )


# --------------------------------------------------------------------------
# Cross-framework — the same layer, every host that used it
# --------------------------------------------------------------------------
with tabs[8]:
    from aegl import crossview  # noqa: PLC0415

    st.subheader("One layer, every framework")
    st.caption(
        "The three agents exist to show AEGL is framework-neutral, and that claim "
        "is only checkable if all of them appear in one place. Everything below is "
        "read from the **hash-chained audit journal**, so these are figures from "
        "the record that can be verified rather than a convenience table."
    )

    _entries = aegl.audit.entries()
    _summary = crossview.summarise(_entries)
    _totals = _summary["totals"]

    if not _entries:
        st.info(
            "The journal is empty. Run an agent with governance on — "
            "`run_agent.py adk --govern --budget 0.03`, or the **AEGL governance** "
            "toggle in any cockpit — and its decisions appear here."
        )
    else:
        k = st.columns(5)
        k[0].metric("Frameworks", _totals["frameworks"])
        k[1].metric("Decisions", _totals["decisions"])
        k[2].metric("Refused", _totals["refused"])
        k[3].metric(
            "Internal / external",
            f"${_totals['internalUsd']:.4f} / ${_totals['externalUsd']:.4f}",
        )
        k[4].metric(
            "Ceiling stops", _totals["ceilingStops"],
            f"{_totals['overrides']} human overrides" if _totals["overrides"] else None,
        )

        if _summary["unlabelled"]:
            st.caption(
                f"{_summary['unlabelled']} decision(s) carry no framework label — "
                "written before labelling existed, or by a host that set none. They "
                "are shown as `unlabelled` rather than dropped, because a view that "
                "silently omits history is worse than one that admits what it does "
                "not know."
            )

        st.markdown("#### Per framework")
        st.dataframe(
            [
                {
                    "framework": f["framework"],
                    "providers": ", ".join(f["providers"]) or "—",
                    "decisions": f["decisions"],
                    "approved": f"{f['approvalRate']:.0%}",
                    "refused": f["refused"],
                    "internal": f"${f['internalUsd']:.6f}",
                    "external": f"${f['externalUsd']:.6f}",
                    "advisor calls": f["advisorCalls"],
                    "advisor $": f"${f['advisorCostUsd']:.6f}",
                    "tightened": f["advisorChanged"],
                    "ceiling stops": f["ceilingStops"],
                }
                for f in _summary["frameworks"]
            ],
            width="stretch",
            hide_index=True,
        )

        _named = [f for f in _summary["frameworks"] if f["framework"] != "unlabelled"]
        if len(_named) > 1:
            st.success(
                "**The same governance layer decided for "
                + ", ".join(f["framework"] for f in _named)
                + ".** No agent imports AEGL except the Claude cockpit, which hosts "
                "it; the other two are handed a governor they never reach for."
            )
        elif _named:
            st.info(
                f"Only **{_named[0]['framework']}** has run under governance so far. "
                "Run another framework with `--govern` to compare them here."
            )

        st.markdown("#### Which engine refused, by framework")
        _refusals = [
            {"framework": f["framework"], "engine": engine, "refusals": count}
            for f in _summary["frameworks"]
            for engine, count in sorted(f["engines"].items())
        ]
        if _refusals:
            st.dataframe(_refusals, width="stretch", hide_index=True)
        else:
            st.caption("Nothing has been refused yet.")

        st.markdown("#### Recent decisions")
        st.dataframe(
            [
                {
                    **row,
                    "amountUsd": f"${row['amountUsd']:.6f}",
                    "advisor": "yes" if row["advisor"] else "",
                }
                for row in crossview.recent(_entries, limit=40)
            ],
            width="stretch",
            hide_index=True,
        )
