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
  content/ages.html, age-<n>.html, content/effects.html
"""
import html, json, os, pathlib, subprocess, sys

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
}
FLAG_TEXT = {
    "no_food": "The house's people and troops eat nothing.",
    "no_explore": "The house cannot explore for land.",
}
UNLOCK_TEXT = {
    "spell": "Grants a spell (spells arrive in a later milestone).",
    "operation": "Grants a thievery or spy operation (later milestone).",
    "building": "Grants a building (later milestone).",
}
UNIT_ROLE = {"offense": "Offense specialist", "defense": "Defense specialist", "elite": "Elite", "thief": "Thief"}
PARAM_TEXT = [  # (key, label, how to show it)
    ("realms", "Realms in the world", "n"),
    ("states_per_realm", "States in each realm", "n"),
    ("houses_per_state", "Houses each state can hold", "n"),
    ("tick_ms", "Length of a tick", "hours"),
    ("starting_land", "Starting land (acres)", "n"),
    ("starting_peasants", "Starting peasants", "n"),
    ("starting_gold", "Starting gold", "n"),
    ("starting_food", "Starting food", "n"),
    ("people_per_acre", "People each acre can hold", "n"),
    ("peasant_growth_bp", "Peasant growth per tick", "pct"),
    ("gold_per_peasant", "Gold per peasant per tick", "n"),
    ("food_per_acre", "Food grown per acre per tick", "n"),
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
]

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
        params = "".join(f'<tr><td>{e(label)}</td><td class="cell-num">{show_param(a["params"][k], how)}</td></tr>\n'
                         for k, label, how in PARAM_TEXT if k in a["params"])
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
                '<h2 id="numbers">Numbers</h2>\n<div class="table-scroll" data-updated="none"><table>\n'
                f'<tr><th>Rule</th><th class="cell-num">Value</th></tr>\n{params}</table></div>\n'
                f'<h2 id="races">Races</h2>\n{slots("races", "race")}'
                f'<h2 id="personalities">Personalities</h2>\n{slots("personalities", "personality")}' + source_note(a))
        pages[f"age-{a['age']}.html"] = header(a["name"], "Ages") + body

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
    for k, v in p.items():
        if k.endswith("_bp"):
            values[k[:-3] + "_pct"] = pct(v)
        elif k not in ("tick_ms", "food_per_person_milli"):
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
