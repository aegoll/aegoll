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
<link rel="stylesheet" href="site.css">
</head>
<body>

<input type="checkbox" id="menu">
<div class="topbar">
  <label class="menu-btn" for="menu">&#9776; menu</label>
  <a class="brand" href="index.html">tesoro <span class="v">0.1.0</span></a>
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

        page = SHELL.format(
            title=title.group(1) if title else TITLES[href],
            description=desc.group(1) if desc else "",
            nav=build_nav(href, fragment),
            body=body.strip(),
            next=build_next(href),
        )
        (OUT / href).write_text(page, encoding="utf-8")
        written += 1

    (OUT / ".nojekyll").touch()
    print(f"  wrote {written} page(s) to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
