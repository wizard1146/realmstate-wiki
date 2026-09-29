#!/usr/bin/env python3
"""Realmstate wiki build: content/*.html + site/ -> dist/.  Standard library only.

    python3 build.py           build into dist/
    python3 build.py --check   build, and exit 1 if any internal link is broken

A content file is an HTML fragment that starts with a metadata comment:

    <!--
    title: Game Rules
    status: needs-update                  (optional; needs-update or retired; see STATUSES below)
    status_note: Formula changed in Age 110   (optional; added to the banner)
    tab_title: Game Rules - Realmstate Wiki   (optional; the browser-tab text)
    hide_title: yes                       (optional; hides the visible heading)
    toc: no                               (optional; "no" hides the table of contents, "yes" forces it)
    category: Guides, Rules
    credits: Puppy101, Eucariot
    -->
    <p>...</p>

Files whose name starts with "_" are not pages (see _nav.html).
"""
import datetime, html, json, pathlib, re, shutil, sys
from collections import defaultdict
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parent
CONTENT, SITE, DIST = ROOT / "content", ROOT / "site", ROOT / "dist"
META = re.compile(r"\A\s*<!--(.*?)-->\s*", re.S)

# ---- values: game numbers kept in content/_values.json and dropped into pages as {{name}} ----
VALUES, USED, UNKNOWN = {}, set(), []
TOKEN = re.compile(r"(?<!\{)\{\{\s*([A-Za-z][A-Za-z0-9_]*)\s*\}\}(?!\})")   # {{name}}; leaves MediaWiki-style {{{1f}}} alone


def load_values():
    f = CONTENT / "_values.json"
    data = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    return {k: v for k, v in data.items() if not k.startswith("_")}      # keys starting with "_" are notes


def fill_values(text, where):
    """Replace every {{name}} with its value from _values.json. Unknown names are recorded (and fail --check) and left as they are."""
    def rep(m):
        k = m.group(1)
        if k in VALUES:
            USED.add(k)
            return html.escape(str(VALUES[k]), quote=False)
        UNKNOWN.append((where, k))
        return m.group(0)
    return TOKEN.sub(rep, text)


def parse_page(path):
    raw = fill_values(path.read_text(encoding="utf-8"), path.stem)      # values first, so titles, search and descriptions all see the numbers
    m = META.match(raw)
    meta = {}
    if m:
        for line in m.group(1).strip().splitlines():
            k, _, v = line.partition(":")
            meta[k.strip().lower()] = v.strip()
        raw = raw[m.end():]
    slug = path.stem
    return {
        "slug": slug, "url": slug + ".html", "body": raw,
        "title": meta.get("title") or slug.replace("-", " ").title(),
        "categories": [c.strip() for c in meta.get("category", "").split(",") if c.strip()],
        "credits": [c.strip() for c in meta.get("credits", "").split(",") if c.strip()],
        "updated": meta.get("updated", ""),
        "hide_title": meta.get("hide_title", "").lower() in ("yes", "true", "1"),   # optional: visually hide the page heading (still read by screen readers)
        "tab_title": meta.get("tab_title", ""),
        "toc": meta.get("toc", "").lower(),          # optional: "no" hides the table of contents, "yes" shows it even on short pages
        "status": meta.get("status", "").lower(),
        "status_note": meta.get("status_note", ""),   # optional: overrides the browser-tab text (default "<title> · Realmstate Wiki")
    }


class Text(HTMLParser):
    """Strip tags -> plain text, for the search index and meta description."""
    def __init__(self):
        super().__init__(); self.out = []; self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"): self.skip += 1
    def handle_endtag(self, tag):
        if tag in ("script", "style"): self.skip -= 1
    def handle_data(self, d):
        if not self.skip: self.out.append(d)

def plain(fragment):
    p = Text(); p.feed(fragment)
    return re.sub(r"\s+", " ", " ".join(p.out)).strip()


class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.hrefs = []; self.ids = set()
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("href"): self.hrefs.append(a["href"])
        if a.get("id"): self.ids.add(a["id"])


# Page status, set with `status:` in a page's header comment. Optional `status_note:` adds a sentence to the banner.
# The site has no separate home page: the root (and the sidebar wordmark) go straight to Getting Started.
REDIRECTS = {"index.html": "getting-started.html"}

STATUSES = {
    "needs-update": {"label": "Needs update", "list_slug": "needs-update", "list_title": "Pages that need an update",
                     "banner": "This page may contain out-of-date information.",
                     "blurb": "These pages are still useful but contain information that is out of date or incomplete. If you know the current numbers or rules, please update them."},
    "retired": {"label": "Retired", "list_slug": "retired", "list_title": "Retired pages",
                "banner": "This page is no longer relevant to the current game. It is kept for historical reference.",
                "blurb": "These pages describe things that are no longer part of the current game. They are kept for history and rank below live pages in search."},
}

TABLE_WRAP = re.compile(r'<div class="table-scroll"([^>]*)>')

def nice_date(iso):
    """2026-02-13 -> 13 Feb 2026 (also accepts 2026-02 and 2026). Returns '' if unparseable."""
    for fmt, out in (("%Y-%m-%d", "%-d %b %Y"), ("%Y-%m", "%b %Y"), ("%Y", "%Y")):
        try: return datetime.datetime.strptime(iso, fmt).strftime(out)
        except ValueError: pass
    return ""


def add_data_notes(page):
    """After every <div class="table-scroll"> add <p class="data-note">Numbers last updated …</p>.
    Date = the wrapper's data-updated="…" if present, else the page's `updated:`. data-updated="none" opts out.
    Returns (body, number_of_tables_without_a_date)."""
    body, out, pos, undated = page["body"], [], 0, 0
    for m in TABLE_WRAP.finditer(body):
        if m.start() < pos: continue                      # nested inside a wrapper we already handled
        depth, i = 1, m.end()
        for tok in re.finditer(r"<div\b|</div>", body[m.end():]):
            depth += 1 if tok.group() == "<div" else -1
            if depth == 0: i = m.end() + tok.end(); break
        attr = re.search(r'data-updated="([^"]*)"', m.group(1))
        date = attr.group(1) if attr else page["updated"]
        out.append(body[pos:i]); pos = i
        if date == "none": continue
        shown = nice_date(date)
        if not shown: undated += 1; continue
        out.append(f'\n<p class="data-note">Numbers last updated {shown}. Changed? <a href="contribute.html">Help keep it current</a>.</p>')
    out.append(body[pos:])
    return "".join(out), undated


TOC_MIN_HEADINGS = 4     # a table of contents appears when a page has at least this many h2/h3 headings (page header `toc: no|yes` overrides)
TOC_SCAN = re.compile(r"<(/?)(table|details)\b[^>]*>|<(h[23])\b([^>]*)>(.*?)</\3>", re.S)


def add_toc(page):
    """Give every h2/h3 an id and, if the page has enough of them, build a table of contents.
    Headings inside tables or <details> are skipped (layout boxes / collapsed content). Returns (body, toc_html); the box is placed by the template, beside the article on wide screens and above it otherwise."""
    body = page["body"]
    used = set(re.findall(r'\bid="([^"]+)"', body))
    out, pos, depth, items = [], 0, 0, []
    for m in TOC_SCAN.finditer(body):
        if m.group(2):                                   # entering / leaving a table or <details>
            depth += -1 if m.group(1) else 1
            continue
        if depth > 0: continue
        tag, attrs, inner = m.group(3), m.group(4), m.group(5)
        text = plain(inner)
        if not text: continue
        found = re.search(r'\bid="([^"]+)"', attrs)
        if found:
            hid = found.group(1)
        else:
            base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "section"
            hid, n = base, 2
            while hid in used: hid, n = f"{base}-{n}", n + 1
            used.add(hid)
            out.append(body[pos:m.start()]); out.append(f'<{tag}{attrs} id="{hid}">{inner}</{tag}>'); pos = m.end()
        items.append((tag, hid, text))
    out.append(body[pos:])
    body = "".join(out)

    mode = page.get("toc", "")
    if mode == "no" or len(items) < (2 if mode == "yes" else TOC_MIN_HEADINGS):
        return body, ""
    rows, sub_open = [], False
    for tag, hid, text in items:
        link = f'<a class="toc__link" href="#{hid}">{html.escape(text)}</a>'
        if tag == "h3" and rows:                         # nest under the previous h2
            if not sub_open: rows[-1] = rows[-1][:-5] + '<ol class="toc__sublist">'; sub_open = True
            rows.append(f'<li class="toc__item toc__item--sub">{link}</li>')
        else:
            if sub_open: rows.append("</ol></li>"); sub_open = False
            rows.append(f'<li class="toc__item">{link}</li>')
    if sub_open: rows.append("</ol></li>")
    toc = (f'<details class="toc" id="toc" open><summary class="toc__title">On this page</summary>'
           f'<ol class="toc__list">{"".join(rows)}</ol></details>')
    return body, toc


FIT_MAX_CHARS = 40      # a table whose every cell is this short (or shorter) is only as wide as its content: see fit_tables
TABLE = re.compile(r"<table\b([^>]*)>(.*?)</table>", re.S)
CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.S)
NO_AUTO_FIT = ("table--wide", "table--fit", "table--fixed", "table--units")   # already sized by hand


def fit_tables(body):
    """Wiki rule: a table is only as wide as its content needs, capped at the page width. One that holds sentences
    (a cell longer than FIT_MAX_CHARS) keeps the full width. `table--wide` / `table--fit` on a table override the rule."""
    def one(m):
        attrs, inner = m.group(1), m.group(2)
        cls = re.search(r'class="([^"]*)"', attrs)
        if cls and any(c in cls.group(1).split() for c in NO_AUTO_FIT): return m.group(0)
        if any(len(plain(c)) > FIT_MAX_CHARS for c in CELL.findall(inner)): return m.group(0)
        attrs = attrs.replace(cls.group(0), f'class="{cls.group(1)} table--fit"') if cls else attrs + ' class="table--fit"'
        return f"<table{attrs}>{inner}</table>"
    return TABLE.sub(one, body)


def category_slug(name):
    return "category-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def fill(template, **values):
    for k, v in values.items():
        template = template.replace("{{" + k + "}}", v)
    return template


NAV_GROUP = re.compile(r'<section class="nav-group[^"]*">\s*<h2 class="nav-group__title">(.*?)</h2>(.*?)</section>', re.S)


def site_map(nav, pages):
    """Group pages the way the sidebar does. Returns (groups, home): groups is [(title, [page, ...])] in nav order; home maps a slug to
    (group title, parent page or None). A page that is not in the nav joins the group of the nav page whose title is one of its
    categories (race-dwarf, category Races -> the Races page), as that page's child."""
    by_url = {p["url"]: p for p in pages}
    groups, home = [], {}
    for title, block in NAV_GROUP.findall(nav):
        members = [by_url[u] for u in re.findall(r'href="([^"]+)"', block) if u in by_url]
        groups.append((plain(title), members))
        for m in members: home.setdefault(m["slug"], (plain(title), None))
    by_title = {p["title"]: p for g in groups for p in g[1]}
    for p in pages:
        if p["slug"] in home: continue
        parent = next((by_title[c] for c in p["categories"] if c in by_title), None)
        if parent: home[p["slug"]] = (home[parent["slug"]][0], parent)
    return groups, home


def related_box(page, groups, home, pages):
    """'More in <group>' box under the article: the other pages in the same sidebar group, or, for a child page, its parent and siblings."""
    if page["slug"] not in home: return ""
    group, parent = home[page["slug"]]
    if parent:
        heading = parent["title"]
        links = [parent] + sorted((p for p in pages if home.get(p["slug"], (None, None))[1] is parent), key=lambda p: p["title"].lower())
    else:
        heading = group
        links = next(m for t, m in groups if t == group)
    links = [p for p in links if p is not page]
    if not links: return ""
    items = "".join(f'<li><a href="{p["url"]}">{html.escape(p["title"])}</a></li>' for p in links)
    return (f'<nav class="related" aria-label="Related pages"><h2 class="related__title">More in {html.escape(heading)}</h2>'
            f'<ul class="related__list">{items}</ul></nav>')


def render(template, nav, page, extra_body="", related=""):
    page = dict(page)
    page["body"], toc = add_toc(page)
    cats = ""
    if page["categories"]:
        links = " ".join(f'<a class="category-tag" href="{category_slug(c)}.html">{html.escape(c)}</a>' for c in page["categories"])
        cats = f'<p class="page__categories"><span class="page__categories-label">Categories:</span> {links}</p>'
    credits = ""
    if page["credits"]:
        names = ", ".join(html.escape(c) for c in page["credits"])
        credits = (f'<details class="page__credits"><summary>Contributors ({len(page["credits"])})</summary>'
                   f'<p>Written by: {names}.</p></details>')
    nav = nav.replace(f'href="{page["url"]}"', f'href="{page["url"]}" aria-current="page"')   # highlights the current page in the sidebar
    body, _ = add_data_notes(page)
    body = fit_tables(body)
    st = STATUSES.get(page.get("status", ""))
    if st:
        note = f' {html.escape(page["status_note"])}' if page.get("status_note") else ""
        help_link = ' <a href="contribute.html">Help update it</a>.' if page["status"] == "needs-update" else f' <a href="{st["list_slug"]}.html">All retired pages</a>.'
        body = (f'<aside class="status-banner status-banner--{page["status"]}" role="note">'
                f'<strong class="status-banner__label">{st["label"]}.</strong> {st["banner"]}{note}{help_link}</aside>\n') + body
    body += extra_body
    tab = page.get("tab_title") or f'{page["title"]} · Realmstate Wiki'
    return fill(template, toc=toc, body_class=" page__body--with-toc" if toc else "", title_class=" page__title--hidden" if page.get("hide_title") else "", tab_title=html.escape(tab), title=html.escape(page["title"]), body=body, nav=nav, related=related, categories=cats, credits=credits,
                description=html.escape(plain(page["body"])[:160]))


def main():
    check = "--check" in sys.argv
    if DIST.exists(): shutil.rmtree(DIST)
    DIST.mkdir()
    shutil.copytree(SITE / "assets", DIST / "assets")
    template = (SITE / "template.html").read_text(encoding="utf-8")
    VALUES.clear(); VALUES.update(load_values()); USED.clear(); UNKNOWN.clear()
    nav = fill_values((CONTENT / "_nav.html").read_text(encoding="utf-8"), "_nav") if (CONTENT / "_nav.html").exists() else ""

    pages = [parse_page(p) for p in sorted(CONTENT.glob("*.html")) if not p.name.startswith("_")]
    by_slug = {p["slug"]: p for p in pages}
    by_cat = defaultdict(list)
    for p in pages:
        for c in p["categories"]: by_cat[c].append(p)

    generated = []
    for c, members in sorted(by_cat.items()):
        items = "".join(f'<li><a href="{m["url"]}">{html.escape(m["title"])}</a></li>' for m in sorted(members, key=lambda m: m["title"].lower()))
        generated.append({"slug": category_slug(c), "url": category_slug(c) + ".html", "title": f"Category: {c}",
                          "body": f'<ul class="page-list">{items}</ul>', "categories": [], "credits": [], "status": "", "status_note": ""})
    def badge(p):
        st = STATUSES.get(p["status"])
        return f' <span class="status-badge status-badge--{p["status"]}">{st["label"].lower()}</span>' if st else ""
    for key, st in STATUSES.items():
        members = sorted((p for p in pages if p["status"] == key), key=lambda p: p["title"].lower())
        lis = "".join(f'<li><a href="{m["url"]}">{html.escape(m["title"])}</a>' + (f' <span class="page-list__note">{html.escape(m["status_note"])}</span>' if m["status_note"] else "") + "</li>" for m in members)
        generated.append({"slug": st["list_slug"], "url": st["list_slug"] + ".html", "title": st["list_title"], "categories": [], "credits": [], "status": "", "status_note": "",
                          "body": f'<p>{st["blurb"]}</p>' + (f'<ul class="page-list">{lis}</ul>' if members else '<p><em>None right now.</em></p>')})
    groups, home = site_map(nav, pages)
    def entry(p):
        kids = sorted((c for c in pages if home.get(c["slug"], (None, None))[1] is p), key=lambda c: c["title"].lower())
        sub = f'<ul>{"".join(entry(c) for c in kids)}</ul>' if kids else ""
        return f'<li><a href="{p["url"]}">{html.escape(p["title"])}</a>{badge(p)}{sub}</li>'
    sections = [(t, members) for t, members in groups if members]
    loose = sorted((p for p in pages if p["slug"] not in home), key=lambda p: p["title"].lower())
    if loose: sections.append(("Other pages", loose))
    body = "".join(f'<section class="all-pages__group"><h2>{html.escape(t)}</h2><ul class="page-list page-list--all">{"".join(entry(p) for p in members)}</ul></section>'
                   for t, members in sections)
    generated.append({"slug": "all-pages", "url": "all-pages.html", "title": "All pages", "toc": "no",
                      "body": f'<p>Every page on the wiki, grouped as in the sidebar.</p><div class="all-pages">{body}</div>', "categories": [], "credits": [], "status": "", "status_note": ""})
    generated.append({"slug": "404", "url": "404.html", "title": "Page not found",
                      "body": '<p>That page does not exist (yet). Try the search box, or see <a href="all-pages.html">all pages</a>.</p>',
                      "categories": [], "credits": [], "status": "", "status_note": ""})

    everything = pages + generated
    known = {p["url"] for p in everything} | set(REDIRECTS) | {"search-index.json"}
    broken = []
    for p in everything:
        (DIST / p["url"]).write_text(render(template, nav, p, related=related_box(p, groups, home, pages)), encoding="utf-8")
        lp = Links(); lp.feed(p["body"])
        for href in lp.hrefs:
            if re.match(r"^(https?:|mailto:|#)", href): continue
            target = href.split("#")[0]
            if target and target not in known: broken.append((p["slug"], href))
    for p in pages:
        undated = add_data_notes(p)[1]
        if undated: print(f"WARNING: {p['slug']}: {undated} table(s) have no date (add 'updated: YYYY-MM-DD' to the page header)", file=sys.stderr)
    for p in pages:
        if p["status"] and p["status"] not in STATUSES:
            print(f"WARNING: {p['slug']}: unknown status '{p['status']}' (use: {', '.join(STATUSES)})", file=sys.stderr)
    for src, dest in REDIRECTS.items():
        (DIST / src).write_text(f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><title>Realmstate Wiki</title>\n'
                                f'<meta http-equiv="refresh" content="0; url={dest}"><link rel="canonical" href="{dest}">\n'
                                f'<script>location.replace("{dest}" + location.hash);</script></head>\n'
                                f'<body><p><a href="{dest}">Continue to Getting Started</a></p></body></html>\n', encoding="utf-8")
    unused = sorted(set(VALUES) - USED)
    if unused: print(f"note: unused values in _values.json: {', '.join(unused)}", file=sys.stderr)
    if UNKNOWN:
        print(f"{len(UNKNOWN)} {{{{name}}}} value(s) not defined in content/_values.json:", file=sys.stderr)
        for where, k in UNKNOWN[:20]: print(f"  {where}: {{{{{k}}}}}", file=sys.stderr)

    index = [{"t": p["title"], "u": p["url"], "x": plain(p["body"]), **({"s": STATUSES[p["status"]]["label"]} if p["status"] in STATUSES else {})} for p in pages]
    (DIST / "search-index.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (DIST / ".nojekyll").write_text("")

    print(f"built {len(pages)} pages + {len(generated)} generated -> {DIST.relative_to(ROOT)}/")
    if broken:
        print(f"{len(broken)} broken internal links:", file=sys.stderr)
        for slug, href in broken[:40]: print(f"  {slug}: {href}", file=sys.stderr)
        if check: sys.exit(1)
    if UNKNOWN and check: sys.exit(1)       # a {{name}} with no value is a mistake worth failing the build for


if __name__ == "__main__":
    main()
