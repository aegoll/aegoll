"""Generate the documentation site: one shell, one nav, nine pages.

    python docs/_src/build.py

The **served** site is still plain static HTML with no build step and nothing external — this
script exists so the sidebar is defined once rather than hand-maintained in nine copies, which is
the kind of duplication that drifts silently. Its output is committed, so GitHub Pages serves
files nobody has to trust a pipeline for.

Page bodies live beside this file as fragments. Section links in the sidebar are extracted from
each fragment's own `<h2 id=...>` headings, so a heading added to a page appears in its nav
automatically and one that is renamed cannot leave a dead anchor behind.
"""

from __future__ import annotations

import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parent

#: The sidebar, in reading order. Grouped, because the order is an argument: a reader arrives not
#: knowing what an AEGL is, so the concept comes before the product and the product before the
#: standard. Jumping straight to "here is our package" assumes a reader who already agrees there
#: is a problem.
NAV: list[tuple[str, list[tuple[str, str]]]] = [
    ("Concepts", [
        ("index.html", "Autonomous economic governance"),
        ("types.html", "Four kinds of AEGL"),
        ("vocabulary.html", "The shared vocabulary"),
        ("stablecoins.html", "Stablecoins &amp; irreversibility"),
    ]),
    ("Tesoro", [
        ("tesoro.html", "What Tesoro is"),
        ("architecture.html", "Architecture"),
        ("policies.html", "Policies &amp; rules"),
        ("frameworks.html", "Frameworks &amp; rails"),
    ]),
    ("The standard", [
        ("aegs.html", "AEGS"),
    ]),
    ("Practice", [
        ("start.html", "Get started"),
    ]),
]

#: The canonical origin. Discovery metadata needs absolute URLs -- a relative canonical is
#: ignored by every crawler, and an Open Graph image or URL must resolve from someone else's host.
SITE = "https://aegoll.github.io/tesoro"

#: One version string. The topbar used to carry a hand-typed `0.1.0` and stayed there through the
#: 0.1.1 release, which is the argument for reading it from the package instead of retyping it.
def _version() -> str:
    text = (OUT.parent / "src" / "tesoro" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


VERSION = _version()

ORDER = [href for _, pages in NAV for href, _ in pages]
TITLES = {href: title for _, pages in NAV for href, title in pages}

SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{canonical}">
<meta name="robots" content="index,follow,max-snippet:-1,max-image-preview:large">
<meta name="author" content="Jayzilva">

<!-- Open Graph and Twitter. Not vanity: when this page is pasted into Slack, a PR description or
     a model's browsing tool, these tags are what it shows instead of a bare URL. -->
<meta property="og:type" content="article">
<meta property="og:site_name" content="tesoro">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:url" content="{canonical}">
<meta property="og:locale" content="en">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{description}">

<!-- Structured data. An answer engine quoting this page needs to know what kind of thing it is,
     who wrote it and what software it documents; a crawler infers none of that from prose. Kept
     inline, like everything else here, because the site makes no outbound request. -->
<script type="application/ld+json">
{jsonld}
</script>
<link rel="stylesheet" href="site.css">
</head>
<body>

<input type="checkbox" id="menu">
<div class="topbar">
  <label class="menu-btn" for="menu">&#9776; menu</label>
  <a class="brand" href="index.html">
    <!-- The mark, inlined in the topbar so it appears on every page and still costs no request.
         `currentColor` and the palette variables make it follow the theme, which a flat SVG file
         linked as an <img> could not do. -->
    <svg class="mark-sm" viewBox="0 0 96 96" width="22" height="22" aria-hidden="true" focusable="false">
      <rect x="6" y="6" width="84" height="84" rx="20" fill="none" stroke="currentColor" stroke-width="7"/>
      <rect x="21" y="58" width="11" height="18" rx="2" fill="var(--accent)"/>
      <rect x="37" y="48" width="11" height="28" rx="2" fill="var(--accent)"/>
      <rect x="53" y="38" width="11" height="38" rx="2" fill="var(--accent)"/>
      <rect x="69" y="34" width="11" height="42" rx="2" fill="var(--bad)"/>
      <line x1="16" y1="34" x2="82" y2="34" stroke="currentColor" stroke-width="8" stroke-linecap="round"/>
    </svg>
    <span>tesoro <span class="v">{version}</span></span>
  </a>
  <div class="links">
    <a href="https://github.com/aegoll/tesoro">GitHub</a>
    <a href="https://pypi.org/project/tesoro/">PyPI</a>
    <a href="https://github.com/aegoll/aegs">AEGS</a>
  </div>
</div>

<div class="shell">
<aside class="nav">
{nav}
</aside>

<main>
{body}
{next}
<footer class="page">
  Apache-2.0 · specification text CC-BY-4.0 · no trackers and no external requests, by design
</footer>
</main>
</div>

</body>
</html>
"""


def sections(fragment: str) -> list[tuple[str, str]]:
    """The `<h2 id="...">` headings of one page, in document order.

    Extracted rather than declared, so the sidebar cannot disagree with the page. Tags inside a
    heading are stripped -- a heading containing `<code>` should read as plain text in the nav.
    """
    found = []
    for match in re.finditer(r'<h2 id="([^"]+)">(.*?)</h2>', fragment, re.S):
        anchor, raw = match.group(1), match.group(2)
        text = re.sub(r"<[^>]+>", "", raw)
        text = re.sub(r"\s+", " ", text).strip()
        found.append((anchor, text))
    return found


def build_nav(current: str, fragment: str) -> str:
    out = []
    for group, pages in NAV:
        out.append(f'  <div class="group">{group}</div>')
        for href, title in pages:
            here = href == current
            attr = ' aria-current="page"' if here else ""
            out.append(f'  <a class="page" href="{href}"{attr}>{title}</a>')
            if here:
                subs = sections(fragment)
                if subs:
                    out.append('  <ul class="sub">')
                    for anchor, text in subs:
                        out.append(f'    <li><a href="#{anchor}">{text}</a></li>')
                    out.append("  </ul>")
    return "\n".join(out)


def build_next(current: str) -> str:
    """Previous/next links, because a docs site read front to back should not dead-end."""
    i = ORDER.index(current)
    prev_href = ORDER[i - 1] if i > 0 else None
    next_href = ORDER[i + 1] if i + 1 < len(ORDER) else None
    if not prev_href and not next_href:
        return ""
    left = (
        f'<a href="{prev_href}"><span class="lbl">Previous</span>&larr; {TITLES[prev_href]}</a>'
        if prev_href else "<span></span>"
    )
    right = (
        f'<a href="{next_href}"><span class="lbl">Next</span>{TITLES[next_href]} &rarr;</a>'
        if next_href else "<span></span>"
    )
    return f'<div class="next">\n  {left}\n  {right}\n</div>'


def json_ld(href: str, title: str, description: str, fragment: str) -> str:
    """Structured data for this page, as a `@graph` of three things.

    Answer and generative engines quote prose but they resolve *entities*, and none of the three
    below is inferable from the text: that this documents a specific PyPI package, that a named
    person wrote it, and which page of a series it is. A `TechArticle` alone would leave the
    software anonymous.

    `FAQPage` is emitted only for a page that genuinely has question-shaped headings. Marking up
    a page's ordinary sections as questions to farm a rich result is the sort of thing that gets
    a site demoted, and it would also be a lie about the content.
    """
    import json as _json

    url = f"{SITE}/{'' if href == 'index.html' else href}"
    graph: list[dict] = [
        {
            "@type": "TechArticle",
            "@id": f"{url}#article",
            "headline": title,
            "description": description,
            "inLanguage": "en",
            "isPartOf": {"@id": f"{SITE}/#website"},
            "about": {"@id": f"{SITE}/#software"},
            "author": {"@type": "Person", "name": "Jayzilva"},
            "license": "https://www.apache.org/licenses/LICENSE-2.0",
        },
        {
            "@type": "WebSite",
            "@id": f"{SITE}/#website",
            "name": "tesoro",
            "url": f"{SITE}/",
            "description": (
                "Documentation for tesoro, an Autonomous Economic Governance Layer: spend "
                "controls, budget envelopes and auditable evidence for AI agents that pay."
            ),
        },
        {
            "@type": "SoftwareApplication",
            "@id": f"{SITE}/#software",
            "name": "tesoro",
            "applicationCategory": "DeveloperApplication",
            "operatingSystem": "OS Independent",
            "programmingLanguage": "Python",
            "softwareVersion": VERSION,
            "license": "https://www.apache.org/licenses/LICENSE-2.0",
            "downloadUrl": "https://pypi.org/project/tesoro/",
            "codeRepository": "https://github.com/aegoll/tesoro",
            "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
            "description": (
                "A policy-engine host that decides whether an autonomous agent may spend, before "
                "it spends, and records which control decided. Deterministic, no model in the "
                "decision path."
            ),
        },
    ]

    faqs = [
        (q, a) for q, a in re.findall(
            r'<h2 id="[^"]*"[^>]*>\s*(.*?\?)\s*</h2>\s*<p[^>]*>(.*?)</p>', fragment, re.S
        )
    ]
    if faqs:
        graph.append({
            "@type": "FAQPage",
            "@id": f"{url}#faq",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": _strip_tags(q),
                    "acceptedAnswer": {"@type": "Answer", "text": _strip_tags(a)},
                }
                for q, a in faqs
            ],
        })

    return _json.dumps({"@context": "https://schema.org", "@graph": graph}, indent=2)


def _strip_tags(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


def write_discovery() -> None:
    """`robots.txt`, `sitemap.xml` and `llms.txt`.

    The first two are ordinary crawler hygiene. `llms.txt` is the emerging convention for telling
    a model-driven reader what a site holds without making it infer the shape from nine pages of
    HTML -- the same argument as a sitemap, for a different kind of crawler. All three are
    generated, so a page added to NAV cannot be left out of them.
    """
    (OUT / "robots.txt").write_text(
        "User-agent: *\nAllow: /\n\n" f"Sitemap: {SITE}/sitemap.xml\n",
        encoding="utf-8",
    )

    urls = "\n".join(
        f"  <url><loc>{SITE}/{'' if h == 'index.html' else h}</loc>"
        f"<changefreq>weekly</changefreq>"
        f"<priority>{'1.0' if h == 'index.html' else '0.8'}</priority></url>"
        for h in ORDER
    )
    (OUT / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n</urlset>\n",
        encoding="utf-8",
    )

    lines = [
        "# tesoro",
        "",
        "> An Autonomous Economic Governance Layer (AEGL) for AI agents: it decides whether an "
        "agent may spend, before it spends, and records which control decided. Deterministic "
        "engines, no model in the decision path, integer money, append-only hash-chained "
        "evidence.",
        "",
        f"- Package: https://pypi.org/project/tesoro/ (version {VERSION}, Apache-2.0)",
        "- Source: https://github.com/aegoll/tesoro",
        "- Standard: https://github.com/aegoll/aegs (AEGS 0.1: 12 sections, 90 clauses, "
        "151 executing test vectors)",
        "",
        "## Pages",
        "",
    ]
    for group, pages in NAV:
        lines.append(f"### {group}")
        for h, title in pages:
            src = HERE / h
            desc = ""
            if src.is_file():
                m = re.search(
                    r"<!--\s*description:\s*(.+?)\s*-->", src.read_text(encoding="utf-8")
                )
                desc = f": {m.group(1)}" if m else ""
            lines.append(
                f"- [{title.replace('&amp;', '&')}]"
                f"({SITE}/{'' if h == 'index.html' else h}){desc}"
            )
        lines.append("")
    (OUT / "llms.txt").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    written = 0
    for href in ORDER:
        src = HERE / href
        if not src.is_file():
            print(f"  MISSING fragment: {src.name}")
            continue
        fragment = src.read_text(encoding="utf-8")

        # Each fragment declares its own head metadata on the first two comment lines.
        title = re.search(r"<!--\s*title:\s*(.+?)\s*-->", fragment)
        desc = re.search(r"<!--\s*description:\s*(.+?)\s*-->", fragment)
        body = re.sub(r"<!--\s*(title|description):.*?-->\s*", "", fragment, count=2)

        page_title = title.group(1) if title else TITLES[href]
        page_desc = desc.group(1) if desc else ""
        page = SHELL.format(
            title=page_title,
            description=page_desc,
            canonical=f"{SITE}/{'' if href == 'index.html' else href}",
            jsonld=json_ld(href, page_title, page_desc, fragment),
            version=VERSION,
            nav=build_nav(href, fragment),
            body=body.strip(),
            next=build_next(href),
        )
        (OUT / href).write_text(page, encoding="utf-8")
        written += 1

    write_discovery()
    (OUT / ".nojekyll").touch()
    print(f"  wrote {written} page(s) to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
