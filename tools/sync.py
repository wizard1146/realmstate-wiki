#!/usr/bin/env python3
"""Keep the wiki in step with the game's rule files.  Standard library only.

    python3 tools/sync.py            export rules from ../realmstate (or $REALMSTATE), then regenerate pages
    python3 tools/sync.py --offline  regenerate pages from the committed data/rules.json only
    python3 tools/sync.py --check    what CI runs: fail if any generated page differs from data/rules.json

The export comes from the game itself (`cargo run -p realm-rules --bin export`), so every number
on a generated page is the number the engine uses. Generated pages carry `generated: tools/sync.py`
in their header comment; edit tools/sync.py (or the game's rules), never those pages.

Writes:
  data/rules.json             the raw export
  content/_values.json        the current (latest) age's numbers, for {{name}} in hand-written pages
  content/races.html, race-<id>.html, personalities.html, personality-<id>.html,
  content/ages.html, age-<n>.html, content/effects.html, content/materials.html
"""
import html, json, os, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTENT, DATA = ROOT / "content", ROOT / "data"
GEN_MARK = "generated: tools/sync.py"

# Plain-language meaning of each effect type. A new effect type in the game must be described here;
# --check fails until it is.
STAT_TEXT = {
    "income": "Gold earned from peasants each tick",
    "population": "How many people the land can hold",
    "food_production": "Food grown on land each tick",
    "food_consumption": "Food eaten each tick",
    "offense": "Offense of troops sent on attacks",
    "defense": "Defense of troops at home",
    "explore_cost": "Gold cost of exploring",
    "training_cost": "Gold cost of training troops",
    "return_time": "How long armies take to come home",
    "elite_offense": "Offense of elite troops (elite and elite+)",
    "elite_defense": "Defense of elite troops (elite and elite+)",
    "casualties": "Troops killed in battle",
    "land_loss": "Land lost when attacked",
    "construction_cost": "Gold cost of construction",
    "thief_strength": "Strength of your thieves when spying",
    "thief_defense": "Strength of your thieves against enemy spies",
    "trade_bonus": "Extra gold on your market sales",
    "building_efficiency": "Building efficiency (can pass 100%)",
    "construction_time": "Time to build",
    "land_gain": "Land taken on a successful attack",
    "training_time": "Time to train troops, medics and upgrades",
    "thief_losses": "Thieves lost when caught spying",
    "science_efficiency": "Strength of every science bonus",
    "scientist_spawn": "How fast new scientists arrive",
    "book_production": "Books scientists write each tick",
    "general_effect": "Strength of your generals' traits",
    "renown_gain": "Renown earned",
    "practice_books": "Books from learning by doing and lost texts",
    "material_output": "Your state's output of its realm's material",
    "upgrade_cost": "Material spent on unit upgrades",
    "building_materials": "Materials spent on construction",
    "general_cost": "Material spent on generals",
    "rescue": "Troops your medics save",
    "refine_yield": "What refining makes",
    "paper_books": "Books each paper adds",
}
PRODUCT_TEXT = {"gold": "gold", "food": "food", "horses": "horses", "renown": "renown"}
FLAG_TEXT = {
    "no_food": "The house's people and troops eat nothing.",
    "no_explore": "The house cannot explore for land.",
}
UNLOCK_TEXT = {
    "spell": "Grants a spell (spells arrive in a later milestone).",
    "operation": "Grants a thievery or spy operation (later milestone).",
    "building": "Grants a building (later milestone).",
}
UNIT_ROLE = {"offense": "Offense specialist", "defense": "Defense specialist", "elite": "Elite", "thief": "Thief",
             "offense+": "Offense specialist, upgraded", "defense+": "Defense specialist, upgraded", "elite+": "Elite, upgraded"}
PARAM_TEXT = [  # (key, label, how to show it)
    ("realms", "Realms in the world", "n"),
    ("states_per_realm", "States in each realm", "n"),
    ("houses_per_state", "Houses each state can hold", "n"),
    ("tick_ms", "Length of a tick", "hours"),
    ("starting_land", "Starting land (acres)", "n"),
    ("starting_peasants", "Starting peasants", "n"),
    ("starting_gold", "Starting gold", "n"),
    ("starting_food", "Starting food", "n"),
    ("peasant_growth_bp", "Peasant growth per tick", "pct"),
    ("gold_per_peasant", "Gold per peasant per tick", "n"),
    ("food_per_person_milli", "Food eaten per person per tick", "milli"),
    ("starvation_bp", "Peasants lost per tick without food", "pct"),
    ("explore_gold_per_acre", "Gold to explore one acre", "n"),
    ("explore_ticks", "Explored land arrives after (ticks)", "n"),
    ("train_ticks", "Troops finish training after (ticks)", "n"),
    ("attack_return_ticks", "Armies return after (ticks)", "n"),
    ("land_gain_bp", "Land taken on a successful attack", "pct"),
    ("attacker_loss_bp", "Attacker's troops lost", "pct"),
    ("defender_loss_bp", "Defender's troops lost", "pct"),
    ("luck_bp", "Battle luck (offense varies by up to)", "pm"),
    ("spy_base_success_bp", "Spying: chance with equal thieves per acre", "pct"),
    ("spy_loss_bp", "Spying: thieves caught when it fails", "pct"),
    ("share_floor_bp", "Economy: lowest share a state can fall to", "pct"),
    ("house_split_bp", "Economy: part of a state's output shared among its houses", "pct"),
    ("tax_min_bp", "Economy: lowest state tax on house income", "pct"),
    ("tax_max_bp", "Economy: highest state tax on house income", "pct"),
    ("tax_default_bp", "Economy: tax a new state starts with", "pct"),
    ("upgrade_bonus_bp", "Upgrades: extra main stat of an upgraded unit (unless the race sets it)", "pct"),
    ("upgrade_material", "Upgrades: material spent", "text"),
    ("upgrade_cost", "Upgrades: material per unit", "n"),
    ("upgrade_cost_elite", "Upgrades: material per elite", "n"),
    ("medic_material", "Medics: material spent", "text"),
    ("medic_material_cost", "Medics: material per medic", "n"),
    ("medic_gold", "Medics: gold per medic", "n"),
    ("rescue_bp_per_medic", "Medics: deaths saved per medic per 100 troops", "pct"),
    ("rescue_cap_bp", "Medics: most deaths saved", "pct"),
    ("renown_per_win", "Renown for a successful attack (scaled by how close it was)", "n"),
    ("general_material", "Generals: material paid", "text"),
    ("general_elites", "Generals: elites retired to raise one", "n"),
    ("general_cost", "Generals: material per trait", "n"),
    ("general_trait_renown", "Generals: renown for 1st, 2nd, 3rd... trait", "list"),
    ("general_pick_renown", "Generals: renown to choose traits", "n"),
    ("general_max", "Generals: most a house can keep", "n"),
    ("general_death_bp", "Generals: chance of dying leading a failed attack", "pct"),
    ("barren_living", "Land: people each barren acre houses", "n"),
    ("barren_food", "Land: food each barren acre grows per tick", "n"),
    ("constructing_living", "Land: people each acre under construction houses", "n"),
    ("build_cost_per_land_milli", "Construction: gold per acre of land, thousandths", "n"),
    ("build_cost_offset", "Construction: land added before costing", "n"),
    ("construction_ticks", "Construction: ticks to build", "n"),
    ("raze_cost_base", "Razing: base gold per building", "n"),
    ("raze_cost_per_land_milli", "Razing: gold per acre of land, thousandths", "n"),
    ("optimal_workers_bp", "Efficiency: peasants needed, as a share of jobs", "pct"),
    ("horse_offense", "Mounts: offense a horse adds", "n"),
    ("chariot_offense", "Mounts: offense a chariot adds", "n"),
    ("chariot_horses", "Chariots: horses per chariot", "n"),
    ("chariot_material", "Chariots: material used", "text"),
    ("chariot_material_cost", "Chariots: material per chariot", "n"),
    ("chariot_gold", "Chariots: gold per chariot", "n"),
    ("starting_scientists", "Science: scientists a new house starts with", "n"),
    ("scientist_spawn_milli", "Science: new scientists per tick, thousandths", "n"),
    ("science_ranks", "Science: ranks as [books written per scientist, books a tick]", "pairs"),
    ("paper_material", "Science: material that speeds research", "text"),
    ("books_per_paper", "Science: books each paper adds", "n"),
    ("paper_cap_bp", "Science: most extra books from paper, share of the base", "pct"),
    ("books_per_close_win", "Learning by doing: books for a close win", "n"),
    ("books_per_spy", "Learning by doing: books for a successful spy", "n"),
    ("books_per_building", "Learning by doing: books per building built", "n"),
    ("books_per_thousand_traded", "Learning by doing: books per 1,000 gold traded", "n"),
    ("lost_text_chance_bp", "Lost texts: chance an exploration finds books", "pct"),
    ("books_per_explored_acre", "Lost texts: books per acre explored", "n"),
    ("decay_start_bp", "Colloquium: knowledge lost per tick at first", "pct"),
    ("decay_growth_bp", "Colloquium: extra loss per tick without a contribution", "pct"),
    ("decay_max_bp", "Colloquium: most knowledge lost per tick", "pct"),
    ("renown_per_thousand_books", "Colloquium: renown per 1,000 books contributed", "n"),
    ("academic_building", "Academics: building that supports them", "text"),
    ("academic_universities", "Academics: buildings needed per academic", "n"),
    ("academic_books", "Academics: books to recruit one", "n"),
    ("academic_material", "Academics: material paid per attribute", "text"),
    ("academic_material_cost", "Academics: material per attribute", "n"),
    ("academic_attribute_books", "Academics: books invested for 1st, 2nd, 3rd attribute", "list"),
    ("academic_pick_books", "Academics: books invested to choose attributes", "n"),
    ("trade_fee_bp", "Trading: share of a sale paid to the seller's state", "pct"),
    ("out_of_realm_bp", "Trading: extra an out-of-realm buyer pays", "pct"),
    ("auction_min_ticks", "Trading: shortest auction, ticks", "n"),
    ("auction_max_ticks", "Trading: longest auction, ticks", "n"),
    ("bid_step_bp", "Trading: each bid must beat the last by", "pct"),
    ("offer_ticks", "Trading: an offer lapses after, ticks", "n"),
    ("trade_cooldown_ticks", "Trading: no trading again for, ticks", "n"),
    ("settling_ticks", "Trading: settling in lasts, ticks", "n"),
    ("settling_penalty_bp", "Trading: penalty while settling in", "pct"),
]

# Groups for an age's Numbers table, in order. A rule joins a group by its key, or by its label's "Topic:" prefix.
# The prefix is dropped from the label when it just repeats the group's name. Anything unmatched lands in "Other".
PARAM_GROUPS = [
    ("World", {"realms", "states_per_realm", "houses_per_state", "tick_ms"}, ()),
    ("Starting a house", {"starting_land", "starting_peasants", "starting_gold", "starting_food"}, ()),
    ("Population and food", {"peasant_growth_bp", "gold_per_peasant", "food_per_person_milli", "starvation_bp"}, ()),
    ("Land and construction", {"explore_gold_per_acre", "explore_ticks"}, ("Land", "Construction", "Razing", "Efficiency")),
    ("Economy", set(), ("Economy",)),
    ("Trading", set(), ("Trading",)),
    ("War", {"train_ticks", "attack_return_ticks", "land_gain_bp", "attacker_loss_bp", "defender_loss_bp", "luck_bp", "renown_per_win"},
     ("Upgrades", "Medics", "Mounts", "Chariots")),
    ("Generals", set(), ("Generals",)),
    ("Spying", set(), ("Spying",)),
    ("Science", set(), ("Science", "Learning by doing", "Lost texts", "Colloquium", "Academics")),
]


def grouped_params(params):
    """PARAM_TEXT entries present in `params`, as [(group title, [(key, label, how), ...])] in PARAM_GROUPS order."""
    out = {title: [] for title, _, _ in PARAM_GROUPS} | {"Other": []}
    for k, label, how in PARAM_TEXT:
        if k not in params: continue
        prefix, _, rest = label.partition(": ")
        title = next((t for t, keys, prefixes in PARAM_GROUPS if k in keys or (rest and prefix in prefixes)), "Other")
        if rest and prefix == title: label = rest[0].upper() + rest[1:]
        out[title].append((k, label, how))
    return [(t, rows) for t, rows in out.items() if rows]


e = html.escape


def pct(bp):
    v = bp / 100
    return f"{v:g}"


def show_param(value, how):
    if how == "hours":
        return f"{value / 3_600_000:g} hour(s)"
    if how == "pct":
        return f"{pct(value)}%"
    if how == "pm":
        return f"&plusmn;{pct(value)}%"
    if how == "milli":
        return f"{value / 1000:g}"
    if how == "text":
        return e(str(value))
    if how == "list":
        return ", ".join(f"{v:,}" for v in value)
    if how == "pairs":
        return ", ".join(f"{a:,}: {b}" for a, b in value)
    return f"{value:,}"


def export_rules():
    repo = pathlib.Path(os.environ.get("REALMSTATE", ROOT.parent / "realmstate")).resolve()
    if not (repo / "rules").is_dir():
        sys.exit(f"can't find the game repo at {repo} (set REALMSTATE=/path/to/realmstate)")
    env = dict(os.environ)
    env["PATH"] = str(pathlib.Path.home() / ".cargo" / "bin") + os.pathsep + env.get("PATH", "")
    out = subprocess.run(["cargo", "run", "-q", "-p", "realm-rules", "--bin", "export", "--", "rules"],
                         cwd=repo, env=env, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit("export failed:\n" + out.stderr)
    DATA.mkdir(exist_ok=True)
    (DATA / "rules.json").write_text(out.stdout, encoding="utf-8")


def header(title, category):
    return f"<!--\ntitle: {title}\ncategory: {category}\n{GEN_MARK}\n-->\n"


def source_note(age):
    return (f'<p class="data-note">Generated from the game\'s rule files: {e(age["name"])}, '
            f'ruleset <code>{e(age["hash"])}</code>. Edit the game\'s rules, then run <code>tools/sync.py</code>.</p>\n')


def effects_list(d, stats):
    items = []
    for i, bp in enumerate(d["mods_bp"]):
        if bp:
            sign = "+" if bp > 0 else ""
            cls = "cell-good" if (bp > 0) != (stats[i] in ("food_consumption", "explore_cost", "training_cost", "return_time")) else "cell-bad"
            items.append(f'<li><span class="{cls}">{sign}{pct(bp)}%</span> {e(STAT_TEXT.get(stats[i], stats[i]).lower())} '
                         f'(<a href="effects.html#{stats[i]}"><code>{stats[i]}</code></a>)</li>')
    return items


def def_effects_html(d, vocab):
    items = effects_list(d, vocab["stats"])
    for n, name in enumerate(vocab["flags"]):
        if d["flags"] & (1 << n):
            items.append(f'<li>{e(FLAG_TEXT.get(name, name))} (<a href="effects.html#{name}"><code>{name}</code></a>)</li>')
    for kind, ident in d.get("unlocks", []):
        items.append(f'<li>Unlocks {e(kind)} <b>{e(ident)}</b> (<a href="effects.html#unlock-{kind}"><code>{kind}</code></a>)</li>')
    if not items:
        return "<p>No special effects.</p>\n"
    return '<ul>\n' + "\n".join(items) + "\n</ul>\n"


def units_table(d, vocab):
    rows = "".join(
        f'<tr><td>{e(UNIT_ROLE.get(role, role))}</td><td>{e(u["name"])}</td>'
        f'<td class="cell-num">{u["off"]}</td><td class="cell-num">{u["def"]}</td><td class="cell-num">{u["gold"]:,}</td></tr>\n'
        for role, u in zip(vocab["unit_slots"], d["units"]))
    return ('<div class="table-scroll" data-updated="none"><table>\n'
            '<tr><th>Unit slot</th><th>Unit</th><th class="cell-num">Offense</th><th class="cell-num">Defense</th><th class="cell-num">Gold</th></tr>\n'
            f"{rows}</table></div>\n")


UNIT_SHORT = {"offense": "Offense spec.", "defense": "Defense spec.", "elite": "Elite", "thief": "Thief",
              "offense+": "Offense spec.+", "defense+": "Defense spec.+", "elite+": "Elite+"}
AGE_DATES = CONTENT / "_age-dates.json"   # hand-edited: {"2": {"from": "2026-10-01", "to": "2026-12-31"}}; the game has no dates


def age_dates(age):
    """'1 Oct – 31 Dec 2026' from content/_age-dates.json, or 'dates to be announced'."""
    import datetime
    try: d = json.loads(AGE_DATES.read_text(encoding="utf-8")).get(str(age["age"]), {})
    except (OSError, ValueError): d = {}
    try: a, b = (datetime.date.fromisoformat(d[k]) for k in ("from", "to"))
    except (KeyError, TypeError, ValueError): return "dates to be announced"
    left = f"{a.day} {a:%b}" + ("" if a.year == b.year else f" {a.year}")
    return f"{left} &ndash; {b.day} {b:%b %Y}"


def split_effects(d, vocab):
    """A definition's effects as (bonuses, penalties, other) lists of <li>."""
    good, bad = [], []
    for li in effects_list(d, vocab["stats"]):
        (good if 'class="cell-good"' in li else bad).append(li)
    other = [f'<li>{e(FLAG_TEXT.get(name, name))}</li>' for n, name in enumerate(vocab["flags"]) if d["flags"] & (1 << n)]
    other += [f'<li>Unlocks {e(kind)} <b>{e(ident)}</b></li>' for kind, ident in d.get("unlocks", [])]
    return good, bad, other


def change_tag(changes, kind, ident):
    """' new' / ' rebalanced' tag for a race or personality that changed since the previous age."""
    for c in changes or []:
        if c["kind"] == kind and c["identity"] == ident and c["to"]:
            return f' <span class="status-badge status-badge--retired">{"rebalanced" if c["from"] else "new"}</span>'
    return ""


def other_changes(prev, cur):
    """Everything but races and personalities that differs between two ages: [(area, what, before, after)]."""
    rows = []
    groups = {k: t for t, rows_ in grouped_params({**prev["params"], **cur["params"]}) for k, _, _ in rows_}
    labels = {k: label for t, rows_ in grouped_params({**prev["params"], **cur["params"]}) for k, label, _ in rows_}
    for k, _, how in PARAM_TEXT:
        a, b = prev["params"].get(k), cur["params"].get(k)
        if a != b:
            show = lambda v: "&mdash;" if v is None else show_param(v, how)
            rows.append((groups.get(k, "Other"), e(labels.get(k, k)), show(a), show(b)))
    def ident(x): return x["key"]["identity"] if isinstance(x.get("key"), dict) else x.get("id") or x.get("name")
    def version(x): return f'<code>{e(ident(x))}@{e(x["key"]["version"])}</code>' if isinstance(x.get("key"), dict) and x["key"].get("version") else "changed"
    for field, area in (("buildings", "Buildings"), ("materials", "Materials"), ("recipes", "Refining"), ("traits", "Generals' traits"),
                        ("sciences", "Sciences"), ("projects", "Colloquium projects"), ("attributes", "Academics' attributes")):
        before = {ident(x): x for x in prev.get(field, []) if x}
        after = {ident(x): x for x in cur.get(field, []) if x}
        for k in sorted(set(before) | set(after)):
            a, b = before.get(k), after.get(k)
            name = e((b or a).get("name", k))
            if a is None: rows.append((area, name, "&mdash;", "added"))
            elif b is None: rows.append((area, name, "removed", "&mdash;"))
            elif json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True): rows.append((area, name, version(a), version(b)))
    names = lambda age: [m["name"] for m in age.get("materials", [])]
    for r in range(max(len(prev.get("realm_material", [])), len(cur.get("realm_material", [])))):
        a = names(prev)[prev["realm_material"][r]] if r < len(prev.get("realm_material", [])) else None
        b = names(cur)[cur["realm_material"][r]] if r < len(cur.get("realm_material", [])) else None
        if a != b: rows.append(("Materials", f"Realm {r + 1} produces", e(a or "&mdash;"), e(b or "&mdash;")))
    sb_prev, sb_cur = prev.get("starting_buildings") or {}, cur.get("starting_buildings") or {}
    for k in sorted(set(sb_prev) | set(sb_cur)) if isinstance(sb_cur, dict) else []:
        if sb_prev.get(k) != sb_cur.get(k):
            rows.append(("Starting a house", f"Starting {e(k.replace('_', ' '))}", f'{sb_prev.get(k, 0):,}', f'{sb_cur.get(k, 0):,}'))
    return rows


def current_age_body(ages, vocab):
    cur = ages[-1]
    prev = ages[-2] if len(ages) > 1 else None
    changes = cur.get("changes_from_previous")
    races = [d for d in cur["races"] if d]
    pers = [d for d in cur["personalities"] if d]
    link = f'<a href="age-{cur["age"]}.html">{e(cur["name"])}</a>'
    out = [f'<p>The current age is {link} ({age_dates(cur)}). This page sums up its races, personalities and what changed'
           + (f' since <a href="age-{prev["age"]}.html">{e(prev["name"])}</a>' if prev else "") + '. Full numbers are on the ' + link + ' page.</p>\n']

    # Units: one row per race. Each upgraded unit (+) shares its base unit's columns as "5 → 6"; a stat no race has is left out.
    slots = vocab["unit_slots"]
    cols = []
    for k, role in enumerate(slots):
        if role.endswith("+"): continue
        up = slots.index(role + "+") if role + "+" in slots else None
        units = lambda r: [r["units"][k]] + ([r["units"][up]] if up is not None else [])
        stats = [s_ for s_ in ("off", "def", "gold") if any(u[s_] for r in races for u in units(r))]
        if stats: cols.append((k, up, role, stats))
    def cell(r, k, up, s_):
        v = r["units"][k][s_]
        w = r["units"][up][s_] if up is not None else v
        return f'{v:,}' + (f' <span class="upgrade">&rarr;&nbsp;{w:,}</span>' if w != v else "")
    head1 = "".join(f'<th colspan="{len(st)}">{e(UNIT_ROLE.get(role, role))}</th>' for k, up, role, st in cols)
    head2 = "".join(f'<th class="cell-num">{ {"off": "Off", "def": "Def", "gold": "Gold"}[s_] }</th>' for k, up, role, st in cols for s_ in st)
    body = "".join(
        f'<tr><td><a href="race-{r["key"]["identity"]}.html"><b>{e(r["name"])}</b></a>{change_tag(changes, "race", r["key"]["identity"])}</td>'
        + "".join(f'<td class="cell-num{" cell-muted" if s_ == "gold" else ""}">{cell(r, k, up, s_)}</td>' for k, up, role, st in cols for s_ in st)
        + "</tr>\n" for r in races)
    out.append('<h2 id="units">Race units</h2>\n<p>Offense, defense and gold cost of each unit. After the arrow: the upgraded unit (+), '
               'where it differs. How upgrading works: <a href="military.html">Military</a>.</p>\n'
               '<div class="table-scroll" data-updated="none"><table class="table--sticky-first table--hover table--unit-summary">\n'
               f'<tr><th rowspan="2">Race</th>{head1}</tr>\n<tr>{head2}</tr>\n{body}</table></div>\n')

    def effects_table(defs, kind, singular):
        """Bonuses / Penalties / Other per definition. A column empty for every row is left out; a cell is shaded only when it has something."""
        split = [(d, *split_effects(d, vocab)) for d in defs]
        columns = [(n, label, cls, width) for n, label, cls, width in ((1, "Bonuses", "cell-good", ""), (2, "Penalties", "cell-bad", ""), (3, "Other", "", ' class="col-md"'))
                   if any(row[n] for row in split)]
        ul = lambda items: f'<ul class="list-plain list-spaced">{"".join(items)}</ul>' if items else '<span class="cell-muted">None</span>'
        rows = "".join(
            f'<tr><td><a href="{singular}-{d["key"]["identity"]}.html"><b>{e(d["name"])}</b></a>{change_tag(changes, singular, d["key"]["identity"])}</td>'
            + "".join(f'<td{f" class={chr(34)}{cls}{chr(34)}" if cls and row[n] else ""} data-label="{label}">{ul(row[n])}</td>' for n, label, cls, _ in columns)
            + "</tr>\n" for row in split for d in [row[0]])
        heads = "".join(f"<th{w}>{label}</th>" for _, label, _, w in columns)
        return ('<div class="table-scroll" data-updated="none"><table class="table--fixed table--cards table--hover table--wide">\n'
                f'<tr><th class="col-sm">{e(kind)}</th>{heads}</tr>\n{rows}</table></div>\n')
    out.append('<h2 id="race-effects">Race bonuses and penalties</h2>\n' + effects_table(races, "Race", "race"))
    out.append('<h2 id="personalities">Personalities</h2>\n' + effects_table(pers, "Personality", "personality"))

    gone = [c for c in changes or [] if not c["to"]]
    if gone:
        out.append("<p>Retired since " + e(prev["name"]) + ": " + ", ".join(
            f'<a href="{c["kind"]}-{c["identity"]}.html">{e(c["identity"].title())}</a> ({e(c["kind"])})' for c in gone) + ".</p>\n")

    if prev:
        rows = other_changes(prev, cur)
        out.append(f'<h2 id="changes">Other changes since {e(prev["name"])}</h2>\n')
        if rows:
            areas = []
            for area, *_ in rows:
                if area not in areas: areas.append(area)
            body = "".join(
                f'<tr class="row-group"><th colspan="3" scope="colgroup">{e(area)}</th></tr>\n'
                + "".join(f'<tr><td>{w}</td><td class="cell-num cell-muted">{a}</td><td class="cell-num">{b}</td></tr>\n' for ar, w, a, b in rows if ar == area)
                for area in areas)
            out.append('<div class="table-scroll" data-updated="none"><table class="table--hover table--grouped">\n'
                       f'<tr><th>What</th><th class="cell-num">{e(prev["name"])}</th><th class="cell-num">{e(cur["name"])}</th></tr>\n{body}</table></div>\n')
        else:
            out.append("<p>Nothing else changed.</p>\n")
    return "".join(out) + source_note(cur)


def generate(rules):
    vocab = rules["vocabulary"]
    ages = rules["ages"]
    latest = ages[-1]
    problems = []
    for s in vocab["stats"]:
        if s not in STAT_TEXT: problems.append(f"stat '{s}' has no description in tools/sync.py STAT_TEXT")
    for f in vocab["flags"]:
        if f not in FLAG_TEXT: problems.append(f"flag '{f}' has no description in tools/sync.py FLAG_TEXT")
    for u in vocab["unlock_kinds"]:
        if u not in UNLOCK_TEXT: problems.append(f"unlock kind '{u}' has no description in tools/sync.py UNLOCK_TEXT")

    pages = {}

    # Where each identity appears: [(age, compiled definition)]
    def appearances(kind):
        seen = {}
        for a in ages:
            for slot, d in enumerate(a[kind]):
                if d:
                    seen.setdefault(d["key"]["identity"], []).append((a, slot, d))
        return seen

    for kind, singular, title in (("races", "race", "Races"), ("personalities", "personality", "Personalities")):
        seen = appearances(kind)
        known = {i["id"]: i for i in rules[kind]}
        successors = {}
        for i in rules[kind]:
            if i["lineage_from"]:
                successors.setdefault(i["lineage_from"], []).append(i["id"])

        # Index page: the current age's slots, then every identity ever.
        rows = []
        for slot, d in enumerate(latest[kind]):
            if d:
                ident = d["key"]["identity"]
                rows.append(f'<tr><td class="cell-num">{slot}</td><td><a href="{singular}-{ident}.html">{e(d["name"])}</a></td>'
                            f'<td><code>{e(d["key"]["identity"])}@{e(d["key"]["version"])}</code></td></tr>')
            else:
                rows.append(f'<tr class="cell-muted"><td class="cell-num">{slot}</td><td colspan="2">unused this age</td></tr>')
        every = []
        for ident, i in sorted(known.items(), key=lambda kv: kv[1]["name"]):
            where = ", ".join(f'<a href="age-{a["age"]}.html">Age {a["age"]}</a>' for a, _, _ in seen.get(ident, [])) or "no age yet"
            every.append(f'<li><a href="{singular}-{ident}.html">{e(i["name"])}</a> <span class="cell-muted">({where})</span></li>')
        body = (f'<p>Every {singular} has a permanent identity (such as <code>{next(iter(known), "")}</code>) and a definition '
                f'for each age it is played in. An age places definitions into numbered slots; unlisted slots are unused. '
                f'See the <a href="glossary.html">glossary</a>.</p>\n'
                f'<h2 id="current">In {e(latest["name"])}</h2>\n'
                '<div class="table-scroll" data-updated="none"><table>\n<tr><th class="cell-num">Slot</th>'
                f'<th>{singular.title()}</th><th>Definition</th></tr>\n' + "\n".join(rows) + "\n</table></div>\n"
                f'<h2 id="all">Every {singular}</h2>\n<ul>\n' + "\n".join(every) + "\n</ul>\n" + source_note(latest))
        pages[f"{kind}.html"] = header(title, "Rules") + body

        # One page per identity: its lineage and each distinct definition, with the ages it was used in.
        for ident, i in known.items():
            parts = []
            if i["lineage_from"]:
                src = known.get(i["lineage_from"], {"name": i["lineage_from"]})
                parts.append(f'<p>A rework of <a href="{singular}-{i["lineage_from"]}.html">{e(src["name"])}</a>. '
                             'It is a separate identity, linked so history can follow the lineage.</p>')
            for succ in successors.get(ident, []):
                parts.append(f'<p>Reworked as <a href="{singular}-{succ}.html">{e(known[succ]["name"])}</a>.</p>')
            by_hash = {}
            for a, slot, d in seen.get(ident, []):
                by_hash.setdefault(d["hash"], (d, []))[1].append(a)
            if not by_hash:
                parts.append("<p>Not played in any age yet.</p>")
            for h, (d, used_in) in sorted(by_hash.items(), key=lambda kv: -kv[1][1][-1]["age"]):
                label = ", ".join(f'<a href="age-{a["age"]}.html">Age {a["age"]}</a>' for a in used_in)
                parts.append(f'<h2 id="{e(d["key"]["version"])}">{e(i["name"])} {e(d["key"]["version"])}</h2>\n'
                             f'<p>Played in: {label}. Rules fingerprint <code>{e(h)}</code>.</p>')
                if d["units"]:
                    parts.append(units_table(d, vocab))
                parts.append("<h3>Effects</h3>\n" + def_effects_html(d, vocab))
            pages[f"{singular}-{ident}.html"] = header(i["name"], title) + "\n".join(parts) + "\n" + source_note(latest)

    # Ages: index + one page each, with the age's numbers, slots and patch notes.
    lis = "".join(f'<li><a href="age-{a["age"]}.html">{e(a["name"])}</a> <span class="cell-muted">(ruleset <code>{e(a["hash"])}</code>)</span></li>\n'
                  for a in reversed(ages))
    pages["ages.html"] = header("Ages", "Rules") + ("<p>Each age freezes its own rules when it starts. "
                                                    "Reports from an age are always read under that age's rules.</p>\n"
                                                    f"<ul>\n{lis}</ul>\n") + source_note(latest)
    for n, a in enumerate(ages):
        params = "".join(
            f'<tr class="row-group" id="numbers-{re.sub(r"[^a-z]+", "-", title.lower())}"><th colspan="2" scope="colgroup">{e(title)}</th></tr>\n'
            + "".join(f'<tr><td>{e(label)}</td><td class="cell-num">{show_param(a["params"][k], how)}</td></tr>\n' for k, label, how in rows)
            for title, rows in grouped_params(a["params"]))
        def slots(kind, singular):
            rows = []
            for slot, d in enumerate(a[kind]):
                if d:
                    rows.append(f'<tr><td class="cell-num">{slot}</td><td><a href="{singular}-{d["key"]["identity"]}.html#{e(d["key"]["version"])}">{e(d["name"])}</a></td>'
                                f'<td><code>{e(d["key"]["identity"])}@{e(d["key"]["version"])}</code></td></tr>')
                else:
                    rows.append(f'<tr class="cell-muted"><td class="cell-num">{slot}</td><td colspan="2">unused</td></tr>')
            return ('<div class="table-scroll" data-updated="none"><table>\n<tr><th class="cell-num">Slot</th>'
                    f'<th>{singular.title()}</th><th>Definition</th></tr>\n' + "\n".join(rows) + "\n</table></div>\n")
        realm_rows = "".join(
            f'<tr><td class="cell-num">{r + 1}</td><td><a href="materials.html#{a["materials"][m]["key"]["identity"]}">{e(a["materials"][m]["name"])}</a></td></tr>\n'
            for r, m in enumerate(a.get("realm_material", [])))
        materials_html = ('<h2 id="materials">Materials</h2>\n<div class="table-scroll" data-updated="none"><table class="table--fit">\n'
                          f'<tr><th class="cell-num">Realm</th><th>Material</th></tr>\n{realm_rows}</table></div>\n') if realm_rows else ""
        changes = a["changes_from_previous"]
        if changes is None:
            notes = "<p>The first age.</p>\n"
        elif not changes:
            notes = "<p>No race or personality changed from the previous age.</p>\n"
        else:
            items = []
            for c in changes:
                page = f'{c["kind"]}-{c["identity"]}.html'
                if c["from"] and c["to"]:
                    items.append(f'<li><a href="{page}">{e(c["identity"])}</a> rebalanced: <code>{e(c["from"])}</code> &rarr; <code>{e(c["to"])}</code></li>')
                elif c["to"]:
                    items.append(f'<li><a href="{page}">{e(c["identity"])}</a> added (<code>{e(c["to"])}</code>)</li>')
                else:
                    items.append(f'<li><a href="{page}">{e(c["identity"])}</a> removed</li>')
            notes = "<ul>\n" + "\n".join(items) + "\n</ul>\n"
        prev = f' (compared with <a href="age-{ages[n - 1]["age"]}.html">{e(ages[n - 1]["name"])}</a>)' if n else ""
        body = (f'<p>Ruleset fingerprint <code>{e(a["hash"])}</code>.</p>\n'
                f'<h2 id="changes">What changed{prev}</h2>\n{notes}'
                '<h2 id="numbers">Numbers</h2>\n<div class="table-scroll" data-updated="none"><table class="table--hover table--grouped">\n'
                f'<tr><th>Rule</th><th class="cell-num">Value</th></tr>\n{params}</table></div>\n'
                f'<h2 id="races">Races</h2>\n{slots("races", "race")}'
                f'<h2 id="personalities">Personalities</h2>\n{slots("personalities", "personality")}' + materials_html + source_note(a))
        pages[f"age-{a['age']}.html"] = header(a["name"], "Ages") + body

    # Current age: one summary page, linked from "Current Age" in the sidebar.
    pages["current-age.html"] = header("Current Age", "Ages") + current_age_body(ages, vocab)

    # Materials (latest age).
    # A material made only by refining sits directly under the material it is refined from.
    mats = latest.get("materials", [])
    refined_from = {r["output"]: r["inputs"][0][0] for r in latest.get("recipes", []) if r["inputs"]}
    refined_from = {out: src for out, src in refined_from.items()
                     if not any(x == out for x in latest["realm_material"])}
    mat_rows = []
    for m, mat in enumerate(mats):
        if m in refined_from: continue
        for n in [m] + [o for o in range(len(mats)) if refined_from.get(o) == m]:
            mat, ident = mats[n], mats[n]["key"]["identity"]
            if n == m:
                realms = [str(r + 1) for r, x in enumerate(latest["realm_material"]) if x == m]
                mat_rows.append(f'<tr id="{ident}"><td><b>{e(mat["name"])}</b></td><td>{e(mat["description"])}</td>'
                                f'<td>{", ".join(realms)}</td><td class="cell-num">{mat["output_per_tick"]:,}</td>'
                                f'<td class="cell-num">{mat["output_per_tick"] * len(realms):,}</td></tr>')
            else:
                mat_rows.append(f'<tr id="{ident}" class="row-sub"><td><b>{e(mat["name"])}</b></td><td>{e(mat["description"])}</td>'
                                f'<td>Refined from {e(mats[m]["name"])}</td><td class="cell-num cell-muted">–</td>'
                                f'<td class="cell-num cell-muted">–</td></tr>')
    pages["materials.html"] = header("Materials", "Rules, Economy") + (
        "<p>Every realm produces its own signature material each tick. No material comes from just one realm, "
        "so nobody can corner the market. See "
        '<a href="trade.html">Materials and Trade</a> for how production is shared and sold.</p>\n'
        f'<h2 id="current">In {e(latest["name"])}</h2>\n<div class="table-scroll" data-updated="none"><table>\n'
        '<tr><th>Material</th><th>Used for</th><th>Made in realms</th><th class="cell-num">Per realm, per tick</th>'
        '<th class="cell-num">World total, per tick</th></tr>\n' + "\n".join(mat_rows) + "\n</table></div>\n" + source_note(latest))

    # Refining recipes, on the materials page.
    names = {m["key"]["identity"]: m["name"] for m in latest.get("materials", [])}
    slot_name = [m["name"] for m in latest.get("materials", [])]
    recipe_rows = "".join(
        f'<tr><td><b>{e(r["id"])}</b></td><td>{", ".join(f"{q} {e(slot_name[m])}" for m, q in r["inputs"])}</td>'
        f'<td>{r["quantity"]} {e(slot_name[r["output"]])}</td></tr>\n' for r in latest.get("recipes", []))
    if recipe_rows:
        pages["materials.html"] = pages["materials.html"].replace(source_note(latest), "") + (
            '<h2 id="refining">Refining</h2>\n<p>Some materials are made only by refining others. Refining happens at once.</p>\n'
            '<div class="table-scroll" data-updated="none"><table>\n<tr><th>Recipe</th><th>Uses</th><th>Makes</th></tr>\n'
            + recipe_rows + "</table></div>\n" + source_note(latest))

    # Buildings.
    def snake(name):
        return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()

    def building_effects(b):
        items = []
        for product, n in b["produce"]:
            items.append(f"Makes {n:,} {PRODUCT_TEXT[product]} a tick" + ("" if product == "renown" else " (times efficiency)"))
        for product, n in b["capacity"]:
            items.append(f"Holds {n:,} {PRODUCT_TEXT[product]}")
        for m in b["mods"]:
            base, cap = m["base_bp"] / 100, m["max_bp"] / 100
            sign = "+" if base > 0 else ""
            items.append(f'{sign}{base:g}% {e(STAT_TEXT.get(snake(m["stat"]), m["stat"]).lower())} per 1% of land, up to {sign}{cap:g}% '
                         f'(<a href="effects.html#{snake(m["stat"])}"><code>{snake(m["stat"])}</code></a>)')
        if b["trait_slots"]:
            items.append(f'+{b["trait_slots"]} general trait slot')
        return "<br>".join(items) or "None"
    def building_cost(b):
        parts = [f'{q:,} {e(slot_name[m])}' for m, q in b["materials"]]
        if b["extra_gold"]:
            parts.insert(0, f'{b["extra_gold"]:,} extra gold')
        return ", ".join(parts) or "Gold only"
    brow = []
    for k, b in enumerate(latest.get("buildings", [])):
        ticks = b["construction_ticks"] or latest["params"]["construction_ticks"]
        limit = f'{b["max_count"]} per house' if b["max_count"] else ""
        brow.append(f'<tr id="{b["key"]["identity"]}"><td><b>{e(b["name"])}</b><br><span class="cell-muted">{e(b["description"])}</span></td>'
                    f'<td>{building_effects(b)}</td><td class="cell-num">{b["living"]}</td><td class="cell-num">{b["jobs"]}</td>'
                    f'<td>{building_cost(b)}</td><td class="cell-num">{ticks}</td><td class="cell-num">{latest["starting_buildings"][k]}</td><td>{limit}</td></tr>')
    if brow:
        pages["buildings.html"] = header("Buildings", "Rules, Buildings") + (
            '<p>Every building is built on one acre of barren land. See <a href="construction.html">Land and Construction</a> for costs, '
            'building efficiency and how percentage effects grow.</p>\n'
            f'<h2 id="current">In {e(latest["name"])}</h2>\n<div class="table-scroll" data-updated="none"><table>\n'
            '<tr><th>Building</th><th>Effects</th><th class="cell-num">Houses (people)</th><th class="cell-num">Jobs</th>'
            '<th>Extra cost</th><th class="cell-num">Build time (ticks)</th><th class="cell-num">A new house starts with</th><th>Limit</th></tr>\n'
            + "\n".join(brow) + "\n</table></div>\n" + source_note(latest))

    # Sciences, by category.
    cats = ["economy", "military", "arcane"]
    sci_parts = []
    for c, cat in enumerate(cats):
        rows = []
        for x in (x for x in latest.get("sciences", []) if x["category"] == c):
            effs = []
            for stat, per_root in x["effects"]:
                st = snake(stat)
                per = per_root / 100
                at10k, at100k = per * 100 / 100, per * 316 / 100
                sign = "+" if per > 0 else ""
                effs.append(f'{e(STAT_TEXT.get(st, st))} (<a href="effects.html#{st}"><code>{st}</code></a>): '
                            f'{sign}{at10k:.1f}% at 10,000 books, {sign}{at100k:.1f}% at 100,000')
            rows.append(f'<tr id="{x["key"]["identity"]}"><td><b>{e(x["name"])}</b><br><span class="cell-muted">{e(x["description"])}</span></td>'
                        f'<td>{"<br>".join(effs)}</td></tr>')
        if rows:
            sci_parts.append(f'<h2 id="{cat}">{cat.title()}</h2>\n<div class="table-scroll" data-updated="none"><table>\n'
                             '<tr><th>Science</th><th>Effect</th></tr>\n' + "\n".join(rows) + "\n</table></div>\n")
    if sci_parts:
        pages["sciences.html"] = header("Sciences", "Rules, Science") + (
            '<p>Each science grows with the square root of the books invested in it: four times the books gives twice the bonus. '
            'Books are spent in their own category. See <a href="science.html">Science</a> for how books are earned.</p>\n'
            + "".join(sci_parts) + source_note(latest))

    # Colloquium projects.
    prow = []
    for proj in latest.get("projects", []):
        effs = []
        for stat, bp in proj["mods"]:
            st = snake(stat)
            sign = "+" if bp > 0 else ""
            effs.append(f'{sign}{bp / 100:g}% {e(STAT_TEXT.get(st, st).lower())} per tier (<a href="effects.html#{st}"><code>{st}</code></a>)')
        if proj["trait_slots_at"]:
            t, n = proj["trait_slots_at"]
            effs.append(f"+{n} general trait slot at tier {t}")
        who = f'States in a realm making {e(slot_name[proj["requires_material"]])}' if proj["requires_material"] is not None else "Every state"
        prow.append(f'<tr id="{proj["key"]["identity"]}"><td><b>{e(proj["name"])}</b><br><span class="cell-muted">{e(proj["description"])}</span></td>'
                    f'<td>{"<br>".join(effs)}</td><td>{", ".join(f"{t:,}" for t in proj["tiers"])}</td><td>{who}</td></tr>')
    if prow:
        pages["projects.html"] = header("Colloquium Projects", "Rules, Science") + (
            '<p>A state researches one of these at a time in its <a href="colloquium.html">Colloquium</a>. '
            'Each finished tier applies to every house in the state.</p>\n<div class="table-scroll" data-updated="none"><table>\n'
            '<tr><th>Project</th><th>Effect</th><th>Books for tiers 1, 2, 3</th><th>Who can research it</th></tr>\n'
            + "\n".join(prow) + "\n</table></div>\n" + source_note(latest))

    # General traits.
    trait_rows = "".join(
        f'<tr id="{t["key"]["identity"]}"><td><b>{e(t["name"])}</b></td><td><ul class="list-plain">{"".join(effects_list(t, vocab["stats"]))}</ul></td></tr>\n'
        for t in latest.get("traits", []))
    if trait_rows:
        pages["traits.html"] = header("Generals' Traits", "Rules") + (
            '<p>A <a href="generals.html">general</a> has one or more of these traits. Each is a flat modifier while '
            "the general leads an army or commands the defense.</p>\n"
            '<div class="table-scroll" data-updated="none"><table>\n<tr><th>Trait</th><th>Effect</th></tr>\n'
            + trait_rows + "</table></div>\n"
            '<h2 id="stacking">How traits stack</h2>\n'
            '<p class="callout"><b>Traits are additive.</b> A general\'s traits for the same stat are added together, and that total is '
            "added to your house's other bonuses for the stat (race, personality, buildings, sciences). The army is then scaled by the "
            "combined percentage once. Bonuses that strengthen generals scale the traits' total before it is added.</p>\n"
            "<p><b>Elite troops are the exception.</b> Elites get their elite bonus first. The whole army, elites included, then gets "
            "the offense or defense bonus, so for elites the two multiply.</p>\n" + source_note(latest))

    # Attack kinds.
    kinds = latest["params"].get("attacks", [])
    takes_text = {
        "land": lambda k: f'{pct(k["amount_bp"])}% of a march\'s land' if k["amount_bp"] != 10_000 else f'land ({pct(latest["params"]["land_gain_bp"])}% of theirs)',
        "reclaim": lambda k: f'{pct(k["amount_bp"])}% of the land their army is carrying home from you',
        "buildings": lambda k: f'{pct(k["amount_bp"])}% of every building, left as barren land',
        "resources": lambda k: f'{pct(k["amount_bp"])}% of their gold, food and materials, carried home',
        "people": lambda k: f'{pct(k["amount_bp"])}% of their peasants and thieves, killed',
        "books": lambda k: f'{pct(k["amount_bp"])}% of their unspent books, carried home',
    }
    attack_rows = "".join(
        f'<tr id="{e(k["id"])}"><td><b>{e(k["name"])}</b><br><code>{e(k["id"])}</code></td>'
        f'<td>{takes_text[k["takes"]](k)}{"; earns renown" if k.get("renown") else ""}</td>'
        f'<td class="cell-num">{"the army that took your land" if k["takes"] == "reclaim" else (str(pct(k["range_min_bp"])) + "%" if k.get("range_min_bp") else "any")}</td>'
        f'<td class="cell-num">&times;{k["hostility_bp"] / 10_000:g}</td>'
        f'<td class="cell-num">{"+" + pct(k["war_bonus_bp"]) + "% (" + pct(k["amount_bp"] + k["war_bonus_bp"]) + "% in all)" if k.get("war_bonus_bp") else ""}{" (war only)" if k.get("war_only") else ""}</td></tr>\n'
        for k in kinds)
    if attack_rows:
        pages["attacks.html"] = header("Attacks", "Rules") + (
            '<p>Every <a href="military.html#Attacking">attack</a> is one of these kinds. They all fight the same battle; they differ in what a win takes. '
            "The first is the default.</p>\n"
            '<div class="table-scroll" data-updated="none"><table>\n<tr><th>Attack</th><th>A win takes</th><th class="cell-num">Smallest target</th>'
            '<th class="cell-num"><a href="war.html#Meter">Meter</a></th><th class="cell-num">At war</th></tr>\n'
            + attack_rows + "</table></div>\n"
            "<p><b>Smallest target</b> is their land as a share of yours. <b>Meter</b> scales the hostility points the attack adds to the target "
            "state's meter. <b>At war</b> is added to the share taken when the two states are at war; land attacks get the war's land bonus instead.</p>\n"
            + source_note(latest))

    # Academic attributes.
    attr_rows = "".join(
        f'<tr id="{t["key"]["identity"]}"><td><b>{e(t["name"])}</b></td><td><ul class="list-plain">{"".join(effects_list(t, vocab["stats"]))}</ul></td></tr>\n'
        for t in latest.get("attributes", []))
    if attr_rows:
        pages["attributes.html"] = header("Academics' Attributes", "Rules") + (
            '<p>An <a href="academics.html">academic</a> has one or more of these attributes. Each is a flat modifier to your '
            "house's science for as long as the academic stays. Attributes of every academic you keep add together.</p>\n"
            '<div class="table-scroll" data-updated="none"><table>\n<tr><th>Attribute</th><th>Effect</th></tr>\n'
            + attr_rows + "</table></div>\n" + source_note(latest))

    # Effects reference.
    stat_rows = "".join(f'<tr id="{s}"><td><code>{s}</code></td><td>{e(STAT_TEXT.get(s, "(no description yet)"))}</td></tr>\n' for s in vocab["stats"])
    flag_rows = "".join(f'<tr id="{f}"><td><code>{f}</code></td><td>{e(FLAG_TEXT.get(f, "(no description yet)"))}</td></tr>\n' for f in vocab["flags"])
    unlock_rows = "".join(f'<tr id="unlock-{u}"><td><code>{u}</code></td><td>{e(UNLOCK_TEXT.get(u, "(no description yet)"))}</td></tr>\n' for u in vocab["unlock_kinds"])
    pages["effects.html"] = header("Effects", "Rules") + (
        "<p>Races and personalities are built only from these effect types. The game implements each type once; "
        "rule files combine them. A race and a personality's modifiers on the same stat add together.</p>\n"
        '<h2 id="modifiers">Modifiers</h2>\n<p>Change a stat by a percentage.</p>\n'
        '<div class="table-scroll" data-updated="none"><table>\n<tr><th>Stat</th><th>What it changes</th></tr>\n' + stat_rows + "</table></div>\n"
        '<h2 id="flags">Flags</h2>\n<p>Switch a rule on.</p>\n'
        '<div class="table-scroll" data-updated="none"><table>\n<tr><th>Flag</th><th>Effect</th></tr>\n' + flag_rows + "</table></div>\n"
        '<h2 id="unlocks">Unlocks</h2>\n'
        '<div class="table-scroll" data-updated="none"><table>\n<tr><th>Kind</th><th>Effect</th></tr>\n' + unlock_rows + "</table></div>\n"
        '<h2 id="units">Unit slots</h2>\n<p>Every race fills the same four unit slots: '
        + ", ".join(e(UNIT_ROLE.get(u, u)).lower() for u in vocab["unit_slots"]) + ".</p>\n" + source_note(latest))

    # Values for hand-written pages: the latest age's numbers.
    p = latest["params"]
    values = {"_note": "Written by tools/sync.py from the latest age. Do not edit by hand.",
              "age": latest["age"], "age_name": latest["name"], "tick_hours": f'{p["tick_ms"] / 3_600_000:g}',
              "food_per_person": f'{p["food_per_person_milli"] / 1000:g}'}
    values["world_states"] = p["realms"] * p["states_per_realm"]
    values["world_houses"] = f'{p["realms"] * p["states_per_realm"] * p["houses_per_state"]:,}'
    values["general_trait_renown"] = ", ".join(f"{v:,}" for v in p["general_trait_renown"])
    values["general_trait_slots"] = len(p["general_trait_renown"])
    values["rescue_per_medic_pct"] = pct(p["rescue_bp_per_medic"])
    values["example_build_cost"] = f'{p["build_cost_per_land_milli"] * (p["starting_land"] + p["build_cost_offset"]) // 1000:,}'
    values["recruit_books"] = p["science_ranks"][0][1]
    values["science_ranks"] = "; ".join(f"{b} books a tick from {x:,}" for x, b in p["science_ranks"])
    values["professor_books"] = p["science_ranks"][-1][1]
    values["starting_books_per_tick"] = p["starting_scientists"] * p["science_ranks"][0][1]
    values["settling_penalty_size"] = pct(abs(p["settling_penalty_bp"]))
    values["academic_attribute_books"] = ", ".join(f"{v:,}" for v in p["academic_attribute_books"])
    values["scientists_per_tick"] = f'{p["scientist_spawn_milli"] / 1000:g}'
    values["starting_built"] = sum(latest.get("starting_buildings", []))
    values["starting_barren"] = p["starting_land"] - values["starting_built"]
    values["equal_share_pct"] = f'{100 / p["states_per_realm"]:.1f}'
    for mat in latest.get("materials", []):
        if mat["key"]["identity"] == "bauxite":
            # Worked example for trade.html: one bauxite state with an equal share and a full roster.
            out = mat["output_per_tick"]
            state = out * (10_000 // p["states_per_realm"]) // 10_000
            houses = state * p["house_split_bp"] // 10_000
            values.update({
                "bauxite_output": f"{out:,}",
                "example_state_output": state,
                "example_houses_part": houses,
                "example_treasury_part": state - houses,
                "example_per_house": f'{houses / p["houses_per_state"]:.1f}',
            })
    # Hostility meters count hundredths of a point; pages show points.
    meter = ("hostility_attack_points", "hostility_failed_attack_points", "hostility_spy_points", "unfriendly_points",
             "hostile_points", "declare_either_points", "auto_war_points", "hostility_cap", "meter_decay_min_points")
    for k, v in p.items():
        if k in meter:
            values[k] = f"{v / 100:g}"
        elif k.endswith("_bp"):
            values[k[:-3] + "_pct"] = pct(v)
        elif k not in ("tick_ms", "food_per_person_milli", "general_trait_renown", "academic_attribute_books", "rescue_bp_per_medic", "attacks", "build_cost_per_land_milli", "raze_cost_per_land_milli", "science_ranks"):
            values[k] = f"{v:,}" if isinstance(v, int) and abs(v) >= 10_000 else v
    pages["_values.json"] = json.dumps(values, indent=2) + "\n"
    return pages, problems


def main():
    check = "--check" in sys.argv
    if not check and "--offline" not in sys.argv:
        export_rules()
    rules = json.loads((DATA / "rules.json").read_text(encoding="utf-8"))
    pages, problems = generate(rules)

    existing = {p.name for p in CONTENT.glob("*.html") if GEN_MARK in p.read_text(encoding="utf-8")[:400]}
    stale = sorted(existing - set(pages))
    differ = [name for name, text in pages.items()
              if not (CONTENT / name).exists() or (CONTENT / name).read_text(encoding="utf-8") != text]
    for p in problems:
        print(f"error: {p}", file=sys.stderr)
    if check:
        if differ or stale:
            print("generated pages are out of date; run python3 tools/sync.py and commit:", file=sys.stderr)
            for n in differ + stale: print(f"  {n}", file=sys.stderr)
        sys.exit(1 if differ or stale or problems else 0)
    for name, text in pages.items():
        (CONTENT / name).write_text(text, encoding="utf-8")
    for name in stale:
        (CONTENT / name).unlink()
    print(f"{len(differ)} page(s) updated, {len(stale)} removed, {len(pages)} generated")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
