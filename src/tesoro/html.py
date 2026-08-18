"""Render a `Report` as one self-contained HTML file. A10a.

A generated file, not a server: no port, no listener, no authentication surface, and an
artifact you can attach to a ticket. `tesoro serve` (A10b) is the same renderer behind a second
transport, which is the whole reason this module takes a `Report` and not a live layer.

**Self-contained or it is not auditable.** No CDN, no webfont, no analytics, no outbound request
of any kind — a page that phones out is a page whose contents left the machine, and this one
describes an agent's spending next to a wallet. `tests/test_html.py` asserts the rendered output
contains no absolute URL, because "we did not add one" is a claim and a test is a check.

Stdlib only. Hand-written HTML, CSS and a little JS, no build step, nothing minified. A package
that governs payments should not ship a bundle nobody read.

Four panels, each answering one question an agent developer actually has:

* **Policy** — which pack, its hash, its rules in evaluation order → *what will this do?*
* **Envelopes** — both channels, every limit, and which one binds → *how much is left?*
* **Decisions** — newest first, with the attributed control → *why did my agent stop?*
* **Evidence** — chain length, state, hash strength, caveat → *can I trust this record?*
"""

from __future__ import annotations

from html import escape
from typing import Any

from .reporting import Report

__all__ = ["render"]

#: Verdict colours. `REVIEW` and `ESCALATE` are deliberately different hues rather than two
#: shades of amber: AEGS-0.1-VERD-2 makes them different severities, and a page that renders
#: them alike teaches the reader they are interchangeable.
_VERDICT_CLASS = {
    "APPROVE": "ok",
    "REVIEW": "warn",
    "ESCALATE": "esc",
    "REJECT": "bad",
}

_CSS = """
:root {
  --bg: #fbfbfa; --panel: #fff; --ink: #1a1a19; --dim: #6b6b68;
  --line: #e4e4e1; --ok: #1f7a4d; --warn: #9a6a00; --esc: #a8480c; --bad: #a81c1c;
  --accent: #2b4c7e;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16161a; --panel: #1e1e23; --ink: #e8e8e6; --dim: #9a9a96;
    --line: #2f2f36; --ok: #4bbf85; --warn: #d9a534; --esc: #e0834a; --bad: #e8635f;
    --accent: #7fa8dd;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem 1.25rem 4rem; background: var(--bg); color: var(--ink);
  font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
}
.wrap { max-width: 1080px; margin: 0 auto; }
header { border-bottom: 2px solid var(--line); padding-bottom: 1rem; margin-bottom: 1.5rem; }
h1 { font-size: 1.4rem; margin: 0 0 .3rem; letter-spacing: -.01em; }
.sub { color: var(--dim); font-size: .85rem; }
.sub code { font-size: .82rem; }
section {
  background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
  padding: 1.1rem 1.25rem; margin-bottom: 1.25rem;
}
h2 {
  font-size: .78rem; text-transform: uppercase; letter-spacing: .09em; color: var(--dim);
  margin: 0 0 .15rem; font-weight: 600;
}
.asks { color: var(--dim); font-size: .82rem; font-style: italic; margin: 0 0 .9rem; }
table { width: 100%; border-collapse: collapse; font-size: .87rem; }
th {
  text-align: left; font-weight: 600; color: var(--dim); font-size: .74rem;
  text-transform: uppercase; letter-spacing: .05em; padding: 0 .6rem .4rem 0;
  border-bottom: 1px solid var(--line); white-space: nowrap;
}
td { padding: .42rem .6rem .42rem 0; border-bottom: 1px solid var(--line); vertical-align: top; }
tr:last-child td { border-bottom: none; }
.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
code, .mono {
  font-family: ui-monospace, SFMono-Regular, "Cascadia Mono", Consolas, monospace;
  font-size: .85em;
}
.tag {
  display: inline-block; padding: .08rem .42rem; border-radius: 4px; font-size: .74rem;
  font-weight: 600; letter-spacing: .02em;
}
.ok   { color: var(--ok); }   .tag.ok   { background: color-mix(in srgb, var(--ok) 14%, transparent); }
.warn { color: var(--warn); } .tag.warn { background: color-mix(in srgb, var(--warn) 16%, transparent); }
.esc  { color: var(--esc); }  .tag.esc  { background: color-mix(in srgb, var(--esc) 16%, transparent); }
.bad  { color: var(--bad); }  .tag.bad  { background: color-mix(in srgb, var(--bad) 14%, transparent); }
.absent { color: var(--dim); font-style: italic; }
.binds { color: var(--bad); font-weight: 600; font-size: .78rem; }
.tightest { color: var(--dim); font-size: .78rem; }
.stats { display: flex; flex-wrap: wrap; gap: 1.75rem; margin-bottom: .25rem; }
.stat .k {
  font-size: .72rem; text-transform: uppercase; letter-spacing: .06em; color: var(--dim);
}
.stat .v { font-size: 1.3rem; font-variant-numeric: tabular-nums; }
.caveat {
  margin-top: .9rem; padding: .7rem .85rem; border-left: 3px solid var(--warn);
  background: color-mix(in srgb, var(--warn) 8%, transparent); font-size: .84rem;
  border-radius: 0 5px 5px 0;
}
.caveat b { color: var(--warn); }
.empty { color: var(--dim); font-style: italic; font-size: .87rem; }
footer { color: var(--dim); font-size: .78rem; margin-top: 2rem; text-align: center; }
.scroll { overflow-x: auto; }
"""

# Sorting only. Deliberately the sole script on the page: anything that fetched, posted or
# mutated would need a threat model, and a static artifact does not have one.
_JS = """
document.querySelectorAll('table[data-sortable] th').forEach(function (th, col) {
  th.style.cursor = 'pointer';
  th.title = 'sort';
  th.addEventListener('click', function () {
    var body = th.closest('table').tBodies[0];
    var rows = Array.prototype.slice.call(body.rows);
    var desc = th.dataset.desc === 'true';
    rows.sort(function (a, b) {
      var x = a.cells[col].dataset.sort ?? a.cells[col].textContent.trim();
      var y = b.cells[col].dataset.sort ?? b.cells[col].textContent.trim();
      var nx = parseFloat(x), ny = parseFloat(y);
      var cmp = (!isNaN(nx) && !isNaN(ny)) ? nx - ny : x.localeCompare(y);
      return desc ? -cmp : cmp;
    });
    th.dataset.desc = (!desc).toString();
    rows.forEach(function (r) { body.appendChild(r); });
  });
});
"""


def _e(value: Any) -> str:
    """Escape for HTML text. Everything user- or counterparty-supplied goes through this.

    A resource path and a counterparty id both arrive from outside and both land in this page.
    AEGS-0.1-SEC-2 says the decision path never reads prose; a *renderer* does, so escaping is
    this module's job and not an optional nicety.
    """
    return escape("" if value is None else str(value), quote=True)


def _money(value: str | None) -> str:
    """A USD string, or a visible **absent** — never `0`.

    Invariant 5 in the one place a reader misreads it fastest. `$0.00` beside a limit means
    "nothing spent"; an unset limit means "no limit here", and rendering the second as the
    first invents a ceiling that does not exist.
    """
    return f"${_e(value)}" if value is not None else '<span class="absent">absent</span>'


def _verdict(name: str) -> str:
    return f'<span class="tag {_VERDICT_CLASS.get(name, "")}">{_e(name)}</span>'


def _policy_panel(data: dict[str, Any]) -> str:
    policy = data["policy"]
    rows = "".join(
        f"<tr><td class='num mono'>{_e(r['priority'])}</td>"
        f"<td class='mono'>{_e(r['id'])}</td>"
        f"<td>{_e(r['condition'])}</td>"
        f"<td>{_verdict(r['verdict'])}</td>"
        f"<td>{_e(r['reason'])}</td></tr>"
        for r in policy.get("ruleList") or ()
    )
    table = (
        "<div class='scroll'><table><thead><tr><th>Pri</th><th>Rule</th><th>When</th>"
        f"<th>Then</th><th>Because</th></tr></thead><tbody>{rows}</tbody></table></div>"
        if rows
        else "<p class='empty'>No rules in this pack.</p>"
    )
    return f"""<section>
<h2>Policy</h2>
<p class="asks">What will this do?</p>
<div class="stats">
  <div class="stat"><div class="k">Pack</div><div class="v mono">{_e(policy['name'])}</div></div>
  <div class="stat"><div class="k">Content hash</div><div class="v mono">{_e(policy['hash'])}</div></div>
  <div class="stat"><div class="k">Rules</div><div class="v">{_e(policy['rules'])}</div></div>
  <div class="stat"><div class="k">Profile</div><div class="v mono">{_e(data['profile'] or 'none')}</div></div>
</div>
<p class="asks">Evaluated in this order. The first rule that matches decides.</p>
{table}
</section>"""


def _envelopes_panel(data: dict[str, Any]) -> str:
    blocks = []
    for channel, envelopes in (data.get("envelopes") or {}).items():
        if not envelopes:
            continue
        rows = []
        for e in envelopes:
            # `binding` only exists on a refusal; `tightest` always does. Both, because the
            # useful question when nothing was refused is "what bites next" — ENV-6.
            mark = (
                "<span class='binds'>&larr; binding</span>" if e["binding"]
                else ("<span class='tightest'>tightest</span>" if e["tightest"] else "")
            )
            if e["cumulative"]:
                used, headroom = _money(e["usedUsd"]), _money(e["headroomUsd"])
            else:
                # A per-call ceiling never accumulates. "$0.00 of $10.00" beside the
                # cumulative windows reads as "nothing was spent", which is not what it means.
                used = "<span class='absent'>n/a &mdash; per-call ceiling</span>"
                headroom = "<span class='absent'>n/a</span>"
            rows.append(
                f"<tr><td class='mono'>{_e(e['name'])}</td><td>{_e(e['window'])}</td>"
                f"<td class='num'>{_money(e['limitUsd'])}</td><td class='num'>{used}</td>"
                f"<td class='num'>{headroom}</td><td>{mark}</td></tr>"
            )
        blocks.append(
            f"<h2 style='margin-top:1.1rem'>{_e(channel)}</h2><div class='scroll'><table>"
            "<thead><tr><th>Envelope</th><th>Window</th><th class='num'>Limit</th>"
            "<th class='num'>Used</th><th class='num'>Headroom</th><th></th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div>"
        )
    body = "".join(blocks) or "<p class='empty'>No envelopes configured.</p>"
    return f"""<section>
<h2>Envelopes</h2>
<p class="asks">How much is left?</p>
{body}
<p class="asks" style="margin-top:.9rem">The two channels never share an envelope. An
internal token budget and an external payout are different money with different failure
modes, so their consumption is tracked separately.</p>
</section>"""


def _decisions_panel(data: dict[str, Any]) -> str:
    decisions = data.get("decisions") or ()
    if not decisions:
        return """<section>
<h2>Decisions</h2>
<p class="asks">Why did my agent stop?</p>
<p class="empty">No decisions recorded yet.</p>
</section>"""

    rows = "".join(
        f"<tr><td class='mono' data-sort='{_e(d['at'])}'>{_e(d['at'][:19])}</td>"
        f"<td>{_verdict(d['verdict'])}</td>"
        f"<td class='num'>{_money(d['amountUsd'])}</td>"
        f"<td class='mono'>{_e(d['resource'])}</td>"
        f"<td class='mono'>{_e(d['vendor'])}</td>"
        f"<td class='mono'>{_e(d['attributedControl'])}</td>"
        f"<td>{_e(d['reason'])}</td></tr>"
        for d in decisions
    )
    spend = data["spend"]
    controls = "".join(
        f"<tr><td class='mono'>{_e(name)}</td><td class='num'>{_e(count)}</td></tr>"
        for name, count in sorted(
            (spend.get("byAttributedControl") or {}).items(), key=lambda kv: -kv[1]
        )
    )
    attribution = (
        "<div class='scroll' style='max-width:32rem'><table><thead><tr>"
        "<th>Attributed control</th><th class='num'>Decisions</th></tr></thead>"
        f"<tbody>{controls}</tbody></table></div>"
        if controls else ""
    )
    pending = (
        f"<div class='stat'><div class='k'>Awaiting review</div>"
        f"<div class='v warn'>{_e(data['pendingReviews'])}</div></div>"
        if data.get("pendingReviews") else ""
    )
    return f"""<section>
<h2>Decisions</h2>
<p class="asks">Why did my agent stop?</p>
<div class="stats">
  <div class="stat"><div class="k">Total</div><div class="v">{_e(spend['decisions'])}</div></div>
  <div class="stat"><div class="k">Settled</div><div class="v">{_e(spend['settled'])}</div></div>
  <div class="stat"><div class="k">Spent</div><div class="v">${_e(spend['spentUsd'])}</div></div>
  {pending}
</div>
<p class="asks" style="margin-top:1rem">Counts by verdict say what happened. Counts by
<b>attributed control</b> say what actually governed this agent, which is often not what the
policy author expected.</p>
{attribution}
<h2 style="margin-top:1.2rem">Newest first</h2>
<div class="scroll"><table data-sortable><thead><tr><th>When</th><th>Verdict</th>
<th class="num">Amount</th><th>Resource</th><th>Counterparty</th>
<th>Attributed control</th><th>Reason</th></tr></thead><tbody>{rows}</tbody></table></div>
</section>"""


def _evidence_panel(data: dict[str, Any]) -> str:
    chain = data.get("chain")
    if not chain:
        return """<section>
<h2>Evidence</h2>
<p class="asks">Can I trust this record?</p>
<p class="empty">No evidence chain available.</p>
</section>"""

    state = (
        "<span class='tag ok'>VALID</span>" if chain["valid"]
        else "<span class='tag bad'>BROKEN</span>"
    )
    problems = (
        "<ul>" + "".join(f"<li class='bad'>{_e(p)}</li>" for p in chain["problems"]) + "</ul>"
        if chain.get("problems") else ""
    )
    hashing = chain.get("hash") or {}
    return f"""<section>
<h2>Evidence</h2>
<p class="asks">Can I trust this record?</p>
<div class="stats">
  <div class="stat"><div class="k">Entries</div><div class="v">{_e(chain['entries'])}</div></div>
  <div class="stat"><div class="k">Chain</div><div class="v">{state}</div></div>
  <div class="stat"><div class="k">Hash</div>
    <div class="v mono">{_e(hashing.get('function'))} / {_e(hashing.get('bits'))} bits</div></div>
</div>
{problems}
<div class="caveat"><b>What this does not prove.</b> {_e(chain['caveat'])}</div>
</section>"""


def render(report: Report) -> str:
    """One self-contained HTML document.

    Takes a `Report` rather than a layer, so `tesoro serve` can feed the same renderer live
    without a second template drifting from this one.
    """
    data = report.as_dict()
    versions = data["versions"]
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>tesoro &mdash; {_e(data['policy']['name'])}</title>
<style>{_CSS}</style>
</head><body><div class="wrap">
<header>
  <h1>Governed spend &mdash; {_e(data['policy']['name'])}</h1>
  <div class="sub">
    policy <code>{_e(data['policy']['hash'])}</code> &middot;
    profile <code>{_e(data['profile'] or 'none')}</code> &middot;
    tesoro <code>{_e(versions['tesoro'])}</code> &middot;
    AEGS <code>{_e(versions['aegs'])}</code>
  </div>
</header>
{_policy_panel(data)}
{_envelopes_panel(data)}
{_decisions_panel(data)}
{_evidence_panel(data)}
<footer>
  Generated locally by <code>tesoro report --html</code>. No network access, no external
  resources. Identity is disclosed to a counterparty pseudonymously, so this page carries no
  controller, wallet or key material.
</footer>
</div><script>{_JS}</script></body></html>
"""
