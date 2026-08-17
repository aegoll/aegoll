"""The AEGL panel, as a plugin.

```python
from aegoll import ui as aegoll_ui

aegoll_ui.render(gov.report())          # one call, any Streamlit app
```

A governance layer that needs bespoke UI work per host is not really portable, so
this renders from the **plain dict** `Governor.report()` returns and nothing else.
It never touches a `Governor`, an agent, or a framework — which is what lets one
panel serve four cockpits without any of them importing each other.

The keys it reads are exactly the ones `report()` emits, in camelCase. That
matters more than it sounds: the Claude cockpit's original panel read snake_case
(`amount_usd`, `matched_rule`), because it was fed `dataclasses.asdict()` output
rather than `report()`. Two dialects of the same payload is how a panel silently
renders zeros. There is one dialect here, and `tests/test_ui.py` renders a real
report through a recording stub to prove the panel and the payload still agree.

What it shows, in order:

1. **The run ceiling** — whether AEGL stopped this run. First, because on
   LangGraph and Google ADK it is the guard the framework does not have.
2. **Headline counts** — decisions, per channel, refusals, advisor usage.
3. **Every decision**, with the engine that decided it and the full breakdown.
4. **Envelope state**, per channel — they are separate money and stay separate.
5. **The journal** — audit chain length and validity.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

VERDICT_ICON = {"APPROVE": "✅", "REVIEW": "⏸", "ESCALATE": "🚫", "REJECT": "⛔"}
CHANNEL_ICON = {"internal": "🔑", "external": "🔗"}

ENGINE_BLURB = {
    "treasury": "a budget envelope was breached",
    "policy": "a policy rule matched",
    "risk": "the risk score exceeded tolerance",
    "authorize": "a hard clamp overrode the policy verdict",
    "advisor": "the advisor tightened the deterministic verdict",
}

CHANNEL_PANELS = (
    ("internal", "🔑 Internal — LLM tokens", "Real USD, billed to your provider key."),
    ("external", "🔗 External — data via x402", "Testnet USDC, settled on Base Sepolia."),
)


def money(value: Any, places: int = 6) -> str:
    try:
        return f"${float(value or 0.0):.{places}f}"
    except (TypeError, ValueError):
        return "$—"


# --- the run ceiling ------------------------------------------------------


def render_ceiling(report: dict[str, Any]) -> None:
    """Whether AEGL stopped this run, and how close it came.

    Shown first and unconditionally when a run was governed: on LangGraph and the
    ADK this is a limit the framework itself cannot express, so burying it under
    the decision list would hide the one thing the layer uniquely did.
    """
    run = report.get("run") or {}
    budget = run.get("budgetUsd") or 0.0
    if not budget:
        return

    stopped = run.get("stopped")
    if stopped:
        st.error(
            f"**AEGL stopped this run.** {stopped.get('reason', '')}\n\n"
            "The framework's own limits count steps, not dollars. This ceiling is "
            "denominated in money, and it tripped before the next call was made."
        )
    cols = st.columns(4)
    cols[0].metric("Model", run.get("model") or "-")
    cols[1].metric("Authorized budget", money(budget))
    if stopped:
        cols[2].metric("Spent", money(stopped.get("spentUsd")), "ceiling reached")
        cols[3].metric("Used", f"{float(stopped.get('fractionUsed') or 0) * 100:.0f}%")
    else:
        cols[2].metric("Spent", "—", "run completed on its own")
        cols[3].metric("Stopped by AEGL", "no")


# --- one decision ---------------------------------------------------------


def _envelope_rows(envelopes: list[dict[str, Any]], binding: str | None) -> list[dict]:
    """One row per envelope, distinguishing caps from cumulative windows.

    A per-call cap never accumulates: its `used` is permanently zero, so showing
    it as "$0.000000 used" beside the rolling windows reads as "nothing has been
    spent" when the concept simply does not apply to it.
    """
    rows = []
    for x in envelopes:
        cumulative = x.get("cumulative", True)
        rows.append(
            {
                "envelope": x.get("name", ""),
                "window": x.get("window", ""),
                "limit": money(x.get("limitUsd")),
                "used": money(x.get("usedUsd")) if cumulative else "— per call, not a total",
                "headroom": money(x.get("headroomUsd")),
                "": "◀ binding" if x.get("name") == binding else "",
            }
        )
    return rows


def _decision_card(event: dict[str, Any]) -> None:
    channel = event.get("channel", "?")
    verdict = event.get("verdict", "?")

    with st.container(border=True):
        head = st.columns([3, 1, 1, 1, 1])
        head[0].markdown(
            f"{CHANNEL_ICON.get(channel, '•')} **{channel}** · `{event.get('resource', '')}`"
        )
        head[1].metric("Amount", money(event.get("amountUsd")))
        head[2].metric("Verdict", f"{VERDICT_ICON.get(verdict, '•')} {verdict}")
        head[3].metric("Decided by", event.get("engine", "-"))
        head[4].metric("Latency", f"{float(event.get('latencyUs') or 0):.0f} µs")

        if verdict != "APPROVE":
            engine = event.get("engine", "policy")
            binding = event.get("binding")
            st.error(
                f"**The {engine} engine stopped this** — "
                f"{ENGINE_BLURB.get(engine, '')}. Rule `{event.get('matchedRule')}`"
                + (f", binding envelope `{binding}`" if binding else "")
            )

        for reason in event.get("reasons") or []:
            st.caption(reason)

        with st.expander("Engine breakdown"):
            left, right = st.columns(2)

            with left:
                ok = event.get("budgetOk", True)
                st.markdown(
                    f"**Treasury** — {'within all envelopes' if ok else 'BLOCKED'}"
                )
                if event.get("envelopes"):
                    st.dataframe(
                        _envelope_rows(event["envelopes"], event.get("binding")),
                        width="stretch",
                        hide_index=True,
                    )

                st.markdown(f"**Trust** — {float(event.get('trust') or 0):.3f}")
                if event.get("trustTerms"):
                    st.dataframe(event["trustTerms"], width="stretch", hide_index=True)

            with right:
                st.markdown(f"**Risk** — {float(event.get('risk') or 0):.3f}")
                if event.get("riskTerms"):
                    st.dataframe(event["riskTerms"], width="stretch", hide_index=True)
                if event.get("riskFlags"):
                    st.warning("flags: " + ", ".join(event["riskFlags"]))

                ratio = event.get("roiRatio")
                st.markdown(
                    "**ROI** — "
                    + (
                        f"{ratio:.2f}x expected value over cost"
                        if ratio is not None
                        else "`unknown` — no declared expected value for this resource"
                    )
                )

                eiap = event.get("eiap") or {}
                st.markdown(
                    "**EIAP** — would a model have been worth it? "
                    f"`{event.get('wouldUseAi')}`"
                )
                if eiap:
                    st.caption(
                        f"exposure {money(eiap.get('exposureUsd'))} vs break-even "
                        f"{money(eiap.get('breakEvenExposureUsd'))} "
                        f"(analysis cost {money(eiap.get('aiCostUsd'))})"
                    )

        _advice_block(event)


def _advice_block(event: dict[str, Any]) -> None:
    """What the advisor said, if the EIAP gate let it be asked."""
    advice = event.get("advice")
    if not advice:
        skip = event.get("advisorSkipReason")
        if skip:
            st.caption(f"🧠 no advisor consulted — {skip}")
        return

    changed = event.get("advisorChanged")
    box = st.warning if changed else st.info
    box(
        (
            "🧠 **Advisor tightened this verdict**"
            if changed
            else "🧠 **Advisor consulted** — agreed with the deterministic verdict"
        )
        + f"\n\n`{advice.get('provider')}/{advice.get('model')}` recommended "
        f"**{advice.get('recommendation')}** "
        f"(confidence {float(advice.get('confidence') or 0):.2f})"
    )
    st.caption(advice.get("rationale", ""))
    for concern in advice.get("concerns") or []:
        st.caption(f"• {concern}")

    if advice.get("injectionSuspected"):
        st.error(
            "**Prompt injection detected in the vendor-supplied text.** The advisor "
            "flagged it as containing instructions aimed at itself rather than a "
            "service description, and the payment was forced to REJECT."
        )

    cols = st.columns(4)
    cols[0].metric("Advice cost", money(advice.get("costUsd")))
    cols[1].metric("Latency", f"{float(advice.get('latencyMs') or 0):.0f} ms")
    cols[2].metric(
        "Tokens", f"{advice.get('inputTokens', 0)}/{advice.get('outputTokens', 0)}"
    )
    cols[3].metric("Changed verdict", "yes" if changed else "no")

    if advice.get("error"):
        st.error(
            f"Advisor call failed: {advice['error']}. The deterministic verdict "
            "stood unchanged — a dead advisor never moves an outcome in either "
            "direction."
        )


# --- the panel -----------------------------------------------------------


def render_header(report: dict[str, Any]) -> None:
    """Which policy and advisor governed this run."""
    policy = report.get("policy") or {}
    advisor = report.get("advisor") or {}
    cols = st.columns(3)
    cols[0].metric(
        "Policy", policy.get("name", "-"), f"{policy.get('rules', 0)} rules"
    )
    cols[1].metric("Bundle hash", (policy.get("hash") or "")[:12] or "-")
    model = advisor.get("model")
    cols[2].metric(
        "Advisor", model or "none", "deterministic only" if not model else advisor.get("provider")
    )

    if advisor.get("warning"):
        st.warning(f"**This advisor is not recommended.** {advisor['warning']}")
    if advisor.get("error"):
        st.info(
            f"An advisor was requested but could not be used: {advisor['error']}. "
            "The run proceeded on the deterministic engines alone — losing a second "
            "opinion costs the agent its advisor, not its ability to transact."
        )


def render(report: dict[str, Any] | None) -> None:
    """Render the whole AEGL panel from `Governor.report()`."""
    if not report:
        st.info(
            "**This run was not governed.** Enable AEGL to put both spend channels "
            "behind the layer — the agent's token budget and its x402 purchases "
            "both start needing a decision."
        )
        return

    render_header(report)
    render_ceiling(report)

    events = report.get("events") or []
    internal = [e for e in events if e.get("channel") == "internal"]
    external = [e for e in events if e.get("channel") == "external"]
    refused = [e for e in events if e.get("verdict") != "APPROVE"]
    consulted = [e for e in events if e.get("advice")]
    tightened = [e for e in events if e.get("advisorChanged")]
    advice_cost = sum(float((e.get("advice") or {}).get("costUsd") or 0) for e in events)

    st.caption(
        "Every decision the layer made this run, in order. The two channels keep "
        "**separate envelopes**: internal spend cannot consume the external budget "
        "or vice versa, because they are different currencies paid to different "
        "counterparties."
    )

    k = st.columns(5)
    k[0].metric("Decisions", len(events))
    k[1].metric("Internal (tokens)", len(internal))
    k[2].metric("External (x402)", len(external))
    k[3].metric(
        "Refused", len(refused),
        f"by {refused[0].get('engine')}" if refused else "none",
    )
    k[4].metric(
        "Advisor consulted", f"{len(consulted)}/{len(events)}",
        f"{money(advice_cost)} spent" if consulted else "EIAP said no",
    )

    if consulted:
        st.caption(
            f"The advisor was asked on {len(consulted)} of {len(events)} decisions "
            f"and tightened {len(tightened)} of them, for {money(advice_cost)} of "
            "analysis. On the rest the exposure was below break-even, and the layer "
            "refused to pay for an opinion."
        )

    if refused:
        engines = sorted({e.get("engine", "?") for e in refused})
        st.warning(
            f"{len(refused)} spend request(s) were refused, by: "
            f"**{', '.join(engines)}**. Each card below names the rule and the "
            "binding envelope."
        )

    st.markdown("#### Decision flow")
    if not events:
        st.info("The layer was active but the run made no spend requests.")
    for event in events:
        _decision_card(event)

    render_envelopes(report)
    render_journal(report)


def render_envelopes(report: dict[str, Any]) -> None:
    st.markdown("#### Envelope state after the run")
    cols = st.columns(len(CHANNEL_PANELS))
    for col, (key, title, note) in zip(cols, CHANNEL_PANELS):
        with col:
            st.markdown(f"**{title}**")
            st.caption(note)
            envelopes = (report.get(key) or {}).get("envelopes") or []
            if not envelopes:
                st.caption("no envelopes configured for this channel")
            for x in envelopes:
                if not x.get("cumulative", True):
                    # A ceiling on one payment. It has no "used" to draw, and a
                    # progress bar at 0% would say the opposite of what is true.
                    st.caption(
                        f"{x.get('name')} · max {money(x.get('limitUsd'))} "
                        "per call (a cap, not a running total)"
                    )
                    continue
                st.caption(
                    f"{x.get('name')} · {money(x.get('usedUsd'))} of "
                    f"{money(x.get('limitUsd'))}"
                )
                st.progress(min(1.0, max(0.0, float(x.get("utilisation") or 0.0))))


def render_journal(report: dict[str, Any]) -> None:
    summary = report.get("summary") or {}
    st.markdown("#### Journal")
    j = st.columns(4)
    j[0].metric("Audit entries", summary.get("auditEntries", 0))
    j[1].metric("Chain", "valid" if summary.get("auditOk") else "BROKEN")
    j[2].metric("Pending reviews", summary.get("pendingReviews", 0))
    j[3].metric("Policy", summary.get("policy", "-"), summary.get("policyHash", "")[:12])
    if not summary.get("auditOk", True):
        st.error(
            "**The audit chain does not verify.** Entries are hash-chained, so this "
            "means the journal was edited or truncated after the fact: "
            + "; ".join(summary.get("auditProblems") or [])
        )
    st.caption(
        "Decisions are journalled to a hash-chained audit log. The engine "
        "playground, review queue and chain verification live in the AEGL cockpit "
        "on port 8502; this panel shows what happened during *this* run."
    )
