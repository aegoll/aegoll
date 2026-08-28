"""The published documentation site: generated output must match its source.

`docs/_src/build.py` writes the served HTML and the discovery files, and the output is committed
so GitHub Pages serves files nobody has to trust a pipeline for. That arrangement has one failure
mode, and it has already happened once: **a fragment or the shell is edited, the generator is not
run, and the live site keeps serving the old page.** That is how the site shipped a new sidebar
stylesheet against nine pages of old markup, with zero matching CSS rules.

So this file regenerates into a temporary directory and compares byte for byte. It also pins the
properties a reader or a crawler depends on and which nothing else would notice breaking: the
site makes no outbound request, every internal link resolves, and the structured data is valid
JSON rather than merely present.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
BUILD = DOCS / "_src" / "build.py"

#: Asserted rather than assumed. A missing generator would make every test below pass by
#: checking nothing -- F-C1, which this project has hit six times.
assert BUILD.is_file(), f"{BUILD} is gone; the site layout changed"

PAGES = sorted(DOCS.glob("*.html"))
assert PAGES, "no generated pages in docs/"


def _generated() -> dict[str, str]:
    """Run the generator against a copy and return what it produced."""
    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp) / "docs"
        shutil.copytree(DOCS, work)
        for stale in list(work.glob("*.html")) + list(work.glob("*.txt")) + list(work.glob("*.xml")):
            stale.unlink()
        # The generator reads `src/tesoro/__init__.py` two levels up for the version string, so
        # the copy has to sit at the same depth relative to a package. Symlinking is unreliable
        # on Windows without privileges; copying the one file it reads is cheap and honest.
        pkg = pathlib.Path(tmp) / "src" / "tesoro"
        pkg.mkdir(parents=True)
        pkg.joinpath("__init__.py").write_text(
            (ROOT / "src" / "tesoro" / "__init__.py").read_text(encoding="utf-8"), encoding="utf-8"
        )
        result = subprocess.run(
            [sys.executable, str(work / "_src" / "build.py")],
            capture_output=True, text=True, timeout=180,
        )
        assert result.returncode == 0, result.stderr
        return {
            p.name: p.read_text(encoding="utf-8")
            for p in sorted(list(work.glob("*.html")) + list(work.glob("*.txt")) + list(work.glob("*.xml")))
        }


@pytest.fixture(scope="module")
def fresh() -> dict[str, str]:
    return _generated()


def test_the_committed_site_matches_its_source(fresh):
    """The defect this file exists for.

    A fragment edited without re-running the generator leaves the live site serving the old page,
    and nothing fails -- the HTML is still valid, the links still resolve, and the change is
    simply absent. Checked by regenerating rather than by trusting a habit.
    """
    stale = []
    for name, content in fresh.items():
        committed = DOCS / name
        if not committed.is_file():
            stale.append(f"{name} was generated but is not committed")
        elif committed.read_text(encoding="utf-8") != content:
            stale.append(f"{name} differs from what build.py produces")
    assert not stale, (
        "the committed site is out of date:\n  "
        + "\n  ".join(stale)
        + "\n\nRun `python docs/_src/build.py` and commit the result."
    )


def test_the_site_makes_no_outbound_request():
    """A privacy property the footer claims in writing, so it is checked in code.

    Only *fetched* references count. `rel="canonical"` and the Open Graph tags carry absolute
    URLs and a browser never requests them -- an earlier version of this check flagged them and
    reported nine violations that were not violations.
    """
    fetched = []
    for p in PAGES:
        t = p.read_text(encoding="utf-8")
        fetched += re.findall(
            r'<(?:img|script|iframe|video|audio|source|embed)[^>]*\ssrc="(https?://[^"]+)"', t
        )
        fetched += re.findall(
            r'<link[^>]*rel="(?:stylesheet|preload|prefetch|icon|manifest)"[^>]*href="(https?://[^"]+)"',
            t,
        )
        fetched += re.findall(r"@import\s+url\(\s*['\"]?(https?://[^)'\"]+)", t)
    assert not fetched, f"the site fetches external resources: {fetched}"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_every_page_carries_the_discovery_metadata(page):
    """Canonical, robots, Open Graph and structured data, on every page rather than the home page.

    A crawler or an answer engine arriving at a deep page has only that page's head to work from,
    and the pages most likely to be linked directly are the specific ones.
    """
    t = page.read_text(encoding="utf-8")
    for label, pattern in [
        ("<title>", r"<title>[^<]+</title>"),
        ("meta description", r'<meta name="description" content="[^"]+"'),
        ("canonical", r'<link rel="canonical" href="https://[^"]+"'),
        ("robots", r'<meta name="robots"'),
        ("og:title", r'<meta property="og:title"'),
        ("og:url", r'<meta property="og:url" content="https://[^"]+"'),
        ("twitter:card", r'<meta name="twitter:card"'),
        ("JSON-LD", r'<script type="application/ld\+json">'),
        ("exactly one h1", r"<h1"),
    ]:
        assert re.search(pattern, t), f"{page.name} is missing {label}"
    assert t.count("<h1") == 1, f"{page.name} has {t.count('<h1')} h1 elements, expected 1"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_the_structured_data_is_valid_json_and_names_the_software(page):
    """Present is not the same as valid.

    Malformed JSON-LD is silently discarded by every consumer, so a syntax error here costs
    exactly as much as having no structured data while looking like having some.
    """
    t = page.read_text(encoding="utf-8")
    raw = re.search(r'<script type="application/ld\+json">\s*(\{.*?\})\s*</script>', t, re.S)
    assert raw, f"{page.name} has no JSON-LD block"

    doc = json.loads(raw.group(1))          # raises on malformed JSON, which is the point
    assert doc.get("@context") == "https://schema.org"
    types = {node["@type"] for node in doc["@graph"]}
    assert {"TechArticle", "WebSite", "SoftwareApplication"} <= types, types

    software = next(n for n in doc["@graph"] if n["@type"] == "SoftwareApplication")
    from tesoro import __version__

    assert software["softwareVersion"] == __version__, (
        f"{page.name} advertises version {software['softwareVersion']}, package is {__version__}"
    )


def test_no_page_claims_a_faq_it_does_not_have():
    """`FAQPage` markup on a page whose headings are not questions is a lie about the content,
    and the kind that gets a site demoted rather than promoted."""
    for p in PAGES:
        t = p.read_text(encoding="utf-8")
        raw = re.search(r'<script type="application/ld\+json">\s*(\{.*?\})\s*</script>', t, re.S)
        doc = json.loads(raw.group(1))
        faq = next((n for n in doc["@graph"] if n["@type"] == "FAQPage"), None)
        if faq is None:
            continue
        for entry in faq["mainEntity"]:
            assert entry["name"].rstrip().endswith("?"), (
                f"{p.name} marks up a non-question as a FAQ entry: {entry['name']!r}"
            )
            assert entry["acceptedAnswer"]["text"].strip(), (
                f"{p.name} has a FAQ entry with an empty answer: {entry['name']!r}"
            )


def test_every_internal_link_and_anchor_resolves():
    """A dead link in published documentation is a reader who stops reading."""
    ids = {p.name: set(re.findall(r'id="([^"]+)"', p.read_text(encoding="utf-8"))) for p in PAGES}
    files = {p.name for p in DOCS.iterdir() if p.is_file()}
    broken = []
    for p in PAGES:
        for href in re.findall(r'href="([^"]+)"', p.read_text(encoding="utf-8")):
            if href.startswith(("http://", "https://", "mailto:")):
                continue
            if href.startswith("#"):
                if href[1:] not in ids[p.name]:
                    broken.append(f"{p.name} -> {href} (no such anchor on this page)")
                continue
            target, _, frag = href.partition("#")
            if target not in files:
                broken.append(f"{p.name} -> {href} (no such file)")
            elif frag and target in ids and frag not in ids[target]:
                broken.append(f"{p.name} -> {href} (no such anchor)")
    assert not broken, "broken internal links:\n  " + "\n  ".join(broken)


def test_the_discovery_files_cover_every_page():
    """`sitemap.xml` and `llms.txt` are generated from the nav, so a new page cannot be omitted --
    unless someone hand-edits them, which this catches."""
    sitemap = (DOCS / "sitemap.xml").read_text(encoding="utf-8")
    llms = (DOCS / "llms.txt").read_text(encoding="utf-8")
    robots = (DOCS / "robots.txt").read_text(encoding="utf-8")

    assert "sitemap.xml" in robots.lower()
    assert len(re.findall(r"<loc>", sitemap)) == len(PAGES)
    for p in PAGES:
        needle = "/" if p.name == "index.html" else p.name
        assert needle in sitemap, f"{p.name} is missing from sitemap.xml"
        assert needle in llms, f"{p.name} is missing from llms.txt"


def test_the_topbar_version_is_read_from_the_package():
    """It was a hand-typed `0.1.0` and stayed there through the 0.1.1 release.

    A version string a human retypes is a version string that goes stale, and on a documentation
    site it goes stale where every reader can see it.
    """
    from tesoro import __version__

    for p in PAGES:
        shown = re.search(r'class="v">([^<]+)<', p.read_text(encoding="utf-8"))
        assert shown, f"{p.name} shows no version in the topbar"
        assert shown.group(1) == __version__, (
            f"{p.name} shows {shown.group(1)}, package is {__version__}"
        )


def test_the_stablecoin_page_exists_and_says_what_the_rail_costs():
    """The rail is USDC on x402, and the site described the controls without describing the rail.

    Irreversibility is the reason the controls are shaped as they are -- decided before the
    payment rather than reviewed after it -- so a reader who never learns that the rail has no
    chargeback has missed the argument.
    """
    page = DOCS / "stablecoins.html"
    assert page.is_file(), "the stablecoins page is gone"
    t = page.read_text(encoding="utf-8").lower()
    for term in ("usdc", "x402", "irreversib", "chargeback", "atomic unit", "six decimals"):
        assert term in t, f"the stablecoin page does not mention {term!r}"
    # It must not overclaim: this layer holds no funds and screens nobody.
    assert "no custody" in t
    assert "no regulatory compliance is claimed" in t


def test_the_standards_page_counts_match_the_standard_beside_it():
    """The counts on the AEGS page must be the counts in `aegs/`, not the counts when written.

    These went stale silently and stayed that way. The page advertised 151 vectors and 21
    evidence vectors while the standard held 161 and 28, because adding a vector upstream
    touches nothing here and nobody re-reads a number that looks plausible.

    That is the same failure this file was written for -- generated output drifting from its
    source -- one repository further out. A count on a public page is a claim about another
    repository, so it needs the same treatment as the vendored copies: checked against the
    thing it describes, and skipped rather than failed when that thing is not present.
    """
    sibling = ROOT.parent / "aegs"
    if not sibling.is_dir():
        pytest.skip("no sibling aegs/ checkout; nothing to check the counts against")

    page = (DOCS / "aegs.html").read_text(encoding="utf-8")

    families = {p.name: len(list(p.glob("*.json"))) for p in (sibling / "vectors").iterdir() if p.is_dir()}
    total = sum(families.values())
    clauses = len(re.findall(r"(?m)^## AEGS-0\.1-[A-Z]+-\d+[a-z]?", "\n".join(
        f.read_text(encoding="utf-8") for f in sorted((sibling / "spec").glob("*.md"))
    )))

    assert f"{total} test vectors" in page, (
        f"the page does not say {total} test vectors; aegs/vectors holds that many"
    )
    assert f"<strong>{clauses}</strong>" in page, (
        f"the page does not show {clauses} clauses; aegs/spec defines that many headings"
    )
    for family in ("evidence", "profiles", "verdicts", "arithmetic"):
        assert f"<code>{family}</code></td><td>{families[family]}</td>" in page, (
            f"the {family} family holds {families[family]} vectors and the page disagrees"
        )
