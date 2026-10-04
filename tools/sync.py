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
    "ward": "Resistance to hexes and divinations against you, and to storm damage",
    "casualties_attacking": "Your troops killed in battle when you attack",
    "casualties_defending": "Your troops killed in battle when you defend",
    "attack_gains": "Everything your attacks take (land, plunder, kills)",
    "mercenary_cost": "Gold paid for mercenaries",
    "peasant_growth": "The rate peasants are born at",
    "aether_production": "Aether gathered each tick",
    "aether_cost": "Aether rites cost",
}
PRODUCT_TEXT = {"gold": "gold", "food": "food", "horses": "horses", "renown": "renown", "aether": "aether", "adepts": "adepts (drawn from peasants)"}
FLAG_TEXT = {
    "no_food": "The house's people and troops eat nothing.",
    "no_explore": "The house cannot explore for land.",
    "elite_plus_plus": "Elite+ can be upgraded once more, into elite++.",
}
UNLOCK_TEXT = {
    "spell": "Grants a spell. Spells do nothing yet: they are not rites, and every house can already cast every rite.",
    "operation": "Grants a thievery or spy operation. Nothing uses this yet: every house can run every operation.",
    "building": "Grants a building. Nothing uses this yet: every house can build every building.",
}
UNLOCK_LIVE = set()   # unlock kinds the engine acts on; the rest show "no effect yet" on race and personality pages
UNIT_ROLE = {"soldier": "Soldiers (drafted)", "offense": "Offense specialist", "defense": "Defense specialist", "elite": "Elite", "thief": "Thief",
             "offense+": "Offense specialist, upgraded", "defense+": "Defense specialist, upgraded", "elite+": "Elite, upgraded",
             "mercenary": "Mercenaries (hired per attack)", "elite++": "Elite, upgraded twice"}
PARAM_TEXT = [  # (key, label, how to show it)
    ("realms", "Realms in the world", "n"),
    ("states_per_realm", "States in each realm", "n"),
    ("houses_per_state", "Houses each state can hold", "n"),
    ("tick_ms", "Length of a tick", "hours"),
    ("starting_land", "Starting land (acres)", "n"),
    ("starting_peasants", "Starting people (peasants and soldiers)", "n"),
    ("starting_soldiers", "Of those, starting soldiers", "n"),
    ("starting_gold", "Starting gold", "n"),
    ("starting_food", "Starting food", "n"),
    ("peasant_growth_bp", "Peasant growth per tick", "pct"),
    ("gold_per_peasant", "Gold per peasant per tick", "n"),
    ("food_per_person_milli", "Food eaten per person per tick", "milli"),
    ("starvation_bp", "Peasants lost per tick without food", "pct"),
    ("starvation_soldiers_bp", "Soldiers lost per tick without food", "pct"),
    ("starvation_specialists_bp", "Specialists and thieves lost per tick without food", "pct"),
    ("starvation_elites_bp", "Elites lost per tick without food", "pct"),
    ("explore_gold_per_acre", "Gold to explore one acre (base)", "n"),
    ("explore_gold_per_land_milli", "Exploring: extra gold an acre per 1,000 acres you have", "n"),
    ("explore_soldiers_per_land_milli", "Exploring: soldiers an acre per 1,000 acres you have", "n"),
    ("explore_ticks", "Explored land arrives after (ticks)", "n"),
    ("train_ticks", "Troops finish training after (ticks)", "n"),
    ("attack_return_ticks", "Armies return after (ticks)", "n"),
    ("land_gain_bp", "Land taken on a successful attack", "pct"),
    ("attacker_loss_bp", "Attacker's troops lost", "pct"),
    ("defender_loss_bp", "Defender's troops lost", "pct"),
    ("luck_bp", "Battle luck (offense varies by up to)", "pm"),
    ("spy_base_success_bp", "Spying: chance with equal thieves per acre", "pct"),
    ("spy_loss_bp", "Spying: thieves caught when it fails", "pct"),
    ("nerve_regen_bp", "Spying: Nerve recovered a tick", "pct"),
    ("protection_ticks", "Age: new-house protection (ticks)", "n"),
    ("draft_default_bp", "Military: draft rate a new house starts with", "pct"),
    ("draft_max_bp", "Military: highest draft rate", "pct"),
    ("draft_speed_bp", "Military: peasants drafted a tick, at most", "pct"),
    ("upgrade_cross_point", "Military: offense+ defense / defense+ offense bonus (points)", "n"),
    ("train_spread_ticks", "Military: training spread either side of the average (ticks)", "n"),
    ("train_fail_bp", "Military: trainees who fail (back to soldiers)", "pct"),
    ("train_death_bp", "Military: trainees who die", "pct"),
    ("train_plus_bp", "Military: trainees who come out upgraded", "pct"),
    ("direct_cost_bp", "Military: direct recruits cost more by", "pct"),
    ("direct_time_bp", "Military: direct recruits take longer by", "pct"),
    ("direct_spread_ticks", "Military: direct recruits' spread either side (ticks)", "n"),
    ("direct_fail_bp", "Military: direct recruits who fail (back to peasants)", "pct"),
    ("direct_death_bp", "Military: direct recruits who die", "pct"),
    ("recovery_ticks", "War: recovery after losing (ticks)", "n"),
    ("recovery_growth_bp", "War: faster peasant regrowth in recovery", "pct"),
    ("recovery_peace_share_bp", "War: share of recovery each side gets from a peace", "pct"),
    ("peace_dividend_ticks", "War: ticks out of war before the peace dividend", "n"),
    ("age_ticks", "Age: length in ticks", "n"),
    ("score_land_bp", "Age: score for the most land (out of 100)", "pct"),
    ("score_might_bp", "Age: score for the most might (out of 100)", "pct"),
    ("score_per_war_point", "Age: score per war point (out of 100)", "pct"),
    ("might_unit_pct", "Age: might per troop, as a share of its offense plus defense", "n"),
    ("might_medic", "Age: might per medic", "n"),
    ("might_horse", "Age: might per horse", "n"),
    ("might_chariot", "Age: might per chariot", "n"),
    ("might_character", "Age: might per general or academic", "n"),
    ("might_per_100_books", "Age: might per 100 science books invested", "n"),
    ("false_flag_nerve_bp", "Spying: extra Nerve for a False Flag", "pct"),
    ("waver_ticks", "Spying: how long a courted character wavers (ticks)", "n"),
    ("reassure_gold", "Spying: gold to reassure a wavering character", "n"),
    ("reassure_fee_bp", "Spying: plus this share of its highest transfer fee", "pct"),
    ("loyal_ticks", "Spying: loyalty after reassuring (ticks)", "n"),
    ("court_auction_ticks", "Spying: auction length for a character left wavering (ticks)", "n"),
    ("court_reserve_gold", "Spying: that auction's lowest reserve", "n"),
    ("first_refusal_ticks", "Spying: the courting house's first refusal (ticks)", "n"),
    ("rite_base_success_bp", "Rites: chance of a divination or hex with equal adepts per acre", "pct"),
    ("rite_material", "Rites: material hexes burn as incense", "text"),
    ("nerve_fail_extra_bp", "Spying: extra Nerve a failure costs (share of the operation's cost)", "pct"),
    ("vigilance_max_bp", "Spying: most Vigilance a house can have", "pct"),
    ("vigilance_decay_bp", "Spying: Vigilance fading a tick", "pct"),
    ("share_floor_bp", "Economy: lowest share a state can fall to", "pct"),
    ("house_split_bp", "Economy: part of a state's output shared among its houses", "pct"),
    ("tax_min_bp", "Economy: lowest state tax on house income", "pct"),
    ("tax_max_bp", "Economy: highest state tax on house income", "pct"),
    ("state_rename_ticks", "Economy: ticks between a leader's renames of the state", "n"),
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
    ("books_per_spy", "Learning by doing: books for a successful operation", "n"),
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
    ("Starting a house", {"starting_land", "starting_peasants", "starting_soldiers", "starting_gold", "starting_food"}, ()),
    ("Population and food", {"peasant_growth_bp", "gold_per_peasant", "food_per_person_milli", "starvation_bp", "starvation_soldiers_bp", "starvation_specialists_bp", "starvation_elites_bp"}, ()),
    ("Land and construction", {"explore_gold_per_acre", "explore_gold_per_land_milli", "explore_soldiers_per_land_milli", "explore_ticks"}, ("Land", "Construction", "Razing", "Efficiency")),
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
    for c in d.get("conditional", []):
        # The export writes a conditional's stat by its Rust name ("CasualtiesAttacking").
        s, bp = re.sub(r"(?<!^)([A-Z])", r"_\1", c["stat"]).lower(), c["bp"]
        sign = "+" if bp > 0 else ""
        cls = "cell-good" if (bp > 0) != (s in ("food_consumption", "explore_cost", "training_cost", "return_time", "casualties", "casualties_attacking", "casualties_defending")) else "cell-bad"
        when = [{True: "at war", False: "out of war"}[c["at_war"]]] if c.get("at_war") is not None else []
        when += [f'against {c["vs"]}'] if c.get("vs") else []
        when += [f'on {c["attack"]} attacks'] if c.get("attack") else []
        when += [{"overpopulated": "while overpopulated", "well_fed": "while well fed"}.get(c["when"], c["when"])] if c.get("when") else []
        items.append(f'<li><span class="{cls}">{sign}{pct(bp)}%</span> {e(STAT_TEXT.get(s, s).lower())} <b>{e(", ".join(when))}</b> '
                     f'(<a href="effects.html#{s}"><code>{s}</code></a>, <a href="effects.html#conditions">conditional</a>)</li>')
    return items


def unlock_note(kind):
    return "" if kind in UNLOCK_LIVE else ' <span class="cell-muted">(no effect yet)</span>'


def material_uses(a):
    """{material identity: [what spends it, as HTML]}, read from the age's rules so it can't go stale."""
    p, uses = a["params"], {m["key"]["identity"]: [] for m in a.get("materials", [])}
    ident = list(uses)

    def add(m, text):
        if m in uses and text not in uses[m]: uses[m].append(text)
    add(p.get("upgrade_material"), '<a href="military.html#Upgrades">unit upgrades</a>')
    add(p.get("medic_material"), '<a href="military.html#Medics">medics</a>')
    add(p.get("general_material"), '<a href="generals.html">generals</a>')
    add(p.get("chariot_material"), '<a href="military.html#Mounts">chariots</a>')
    add(p.get("paper_material"), '<a href="science.html#Paper">science</a>')
    add(p.get("academic_material"), '<a href="academics.html">academics</a>')
    if any(r.get("incense") for r in p.get("rites", [])):
        add(p.get("rite_material"), '<a href="rite-list.html">hexes</a>')
    if any(v.get("incense") for v in p.get("vigils", [])):
        add(p.get("rite_material"), '<a href="rites.html#Vigils">vigils</a>')
    for b in a.get("buildings", []):
        for m, _ in b["materials"]:
            add(ident[m], f'<a href="buildings.html#{b["key"]["identity"]}">{e(b["name"] if b["max_count"] == 1 else b["name"].lower())}</a>')
    for k in p.get("attacks", []):
        if k.get("cost_material"): add(k["cost_material"], f'the <a href="attacks.html#{k["id"]}">{e(k["name"])}</a> attack')
    for o in p.get("operations", []):
        if o.get("cost_material"): add(o["cost_material"], f'the <a href="operations.html#{o["id"]}">{e(o["name"])}</a> operation')
    made = {}
    for r in a.get("recipes", []):
        for m, _ in r["inputs"]:
            made.setdefault(ident[m], []).append(e(a["materials"][r["output"]]["name"].lower()))
    for m, outs in made.items():
        add(m, f'<a href="#refining">refining</a> into {" or ".join(outs)}')
    return uses


GROUP_TEXT = {"offense": "offensive specialists", "defense": "defensive specialists", "elite": "elites", "military": "every fighting unit"}
WHEN_TEXT = {"war": "at war", "peace": "out of war", "overpopulated": "while overpopulated", "well_fed": "while well fed"}


def race_rules_items(d):
    """A race's own rules (see the game's realm_rules::race::RaceRules), each as plain words in an <li>."""
    r, items = d.get("race") or {}, []
    signed = lambda n: f"+{n}" if n > 0 else str(n)
    o = r.get("overrides", {})
    if "mercenary_ratio" in o:
        items.append(f'One <a href="military.html#Mercenaries">mercenary</a> for every {o["mercenary_ratio"]} of your own troops sent.')
    if "upgrade_cross_point" in o:
        items.append(f'Upgraded units gain {o["upgrade_cross_point"]} in their other stat.')
    for b in r.get("unit_bonuses", []):
        parts = [f'{signed(b[k])} {"offense" if k == "off" else "defense"}' for k in ("off", "def") if b.get(k)]
        when = f' {WHEN_TEXT[b["when"]]}' if b.get("when") else ""
        items.append(f'{GROUP_TEXT[b["units"]].capitalize()} fight with {" and ".join(parts)} each{when}.')
    if r.get("well_fed_per_acre"):
        items.append(f'Well fed means more than {r["well_fed_per_acre"]} food an acre.')
    for op, bp in sorted(r.get("resist_bp", {}).items()):
        link = f'<a href="operations.html#{op}">{e(op)}</a>'
        items.append(f'Immune to {link}: it can\'t be tried on them.' if bp >= 10000
                     else f'{link} is {pct(abs(bp))}% {"less" if bp > 0 else "more"} likely to work on them.')
    if r.get("no_elite_training"):
        items.append("Can't train elites.")
    if (w := r.get("war_elites")):
        items.append(f'A won battle turns {pct(w["bp"])}% of the surviving offensive specialists into elites.')
    if (m := r.get("mercenaries_stay")):
        items.append(f'{pct(m["bp"])}% of the mercenaries who survive a battle stay, as offensive specialists.')
    if r.get("mercenary_upgrades"):
        items.append('Mercenaries can be hired upgraded, for the upgrade material.')
    if (h := r.get("homes")):
        items.append(f'{e(h["building"]).capitalize()} house {h["living"]} people each, and each 1% of the land in them grows peasants {pct(h["growth_bp_per_pct"])}% faster.')
    if (m := r.get("mirror")):
        items.append(f'Can fight with the unit stats of any of the last {m["attackers"]} houses that attacked them, changing at most every {m["every_ticks"]} ticks.')
    if (c := r.get("citizens")):
        items.append(f'Every peasant at home defends as {"a soldier does" if c["defense_bp"] >= 10000 else f"{pct(c["defense_bp"])}% of a soldier"}.')
    if (p := r.get("promote")):
        items.append(f'Every {p["every_ticks"]} ticks, {pct(p["bp"])}% of the soldiers become {GROUP_TEXT[p["into"]]}, free.')
    if "general_elites" in o:
        items.append(f'Raising a <a href="generals.html">general</a> retires {o["general_elites"]} elites.')
    for c in r.get("unit_casualties", []):
        when = f' {WHEN_TEXT[c["when"]]}' if c.get("when") else ""
        items.append(f'{GROUP_TEXT[c["units"]].capitalize()} die {pct(abs(c["bp"]))}% {"less" if c["bp"] < 0 else "more"} in battle{when}.')
    for k in r.get("immune_attacks", []):
        items.append(f'Immune to the <a href="attacks.html#{e(k)}">{e(k)}</a> attack.')
    for k in r.get("immune_rites", []):
        items.append(f'Immune to the <a href="rite-list.html#{e(k)}">{e(k)}</a> hex.')
    if r.get("building_losses_bp"):
        items.append(f'Lose {pct(abs(r["building_losses_bp"]))}% {"fewer" if r["building_losses_bp"] < 0 else "more"} buildings in attacks (barren land goes instead).')
    for k, bp in sorted(r.get("building_losses_by_attack", {}).items()):
        items.append(f'{e(k).capitalize()} attacks take {pct(abs(bp))}% {"fewer" if bp < 0 else "more"} buildings still.')
    if (c := r.get("casualties_return")):
        items.append(f'{pct(c["bp"])}% of their battle dead come back after {c["ticks"]} ticks.')
    if (a := r.get("afflict")):
        mods = ", ".join(f'{"+" if bp > 0 else ""}{pct(bp)}% {e(STAT_TEXT.get(s, s).lower())}' for s, bp in a["mods"])
        items.append(f'<b>{e(a["name"])}</b>: a {pct(a["chance_bp"])}% chance on every attack to afflict the target for {a["ticks"]} ticks: {mods}.')
    if (d := r.get("double_strike")):
        items.append(f'At war, an army led by a general with {d["general_traits"]}+ traits can be sent ready to strike twice: within {d["window_ticks"]} ticks it strikes again at {pct(d["strength_bp"])}% of its offense, killing {pct(d["kill_bp"])}% of the target\'s specialists and taking no land. The general is then spent for {d["spent_ticks"]} ticks.')
    if (x := r.get("roots")):
        items.append(f'At war, they can take back up to {pct(x["share_bp"])}% of the land an attack took from them, before the army carrying it gets home, for {x["elites_per_acre"]} elites an acre.')
    if (m := r.get("momentum")):
        items.append(f'At war, offense rises {pct(m["gain_bp"])}% every {m["every_ticks"]} ticks, up to {pct(m["max_bp"])}%, and falls {pct(m["drop_bp"])}% for every {m["drop_every_ticks"]} ticks in which they were hit.')
    if (a := r.get("activity")):
        items.append(f'Training takes {a["training_ticks"]} ticks less while, within the last {a["window_ticks"]} ticks, the house explored, took land, or started buildings on {pct(a["build_bp"])}% of its land.')
    return [f"<li>{i}</li>" for i in items]


def def_effects_html(d, vocab):
    items = effects_list(d, vocab["stats"]) + race_rules_items(d)
    for n, name in enumerate(vocab["flags"]):
        if d["flags"] & (1 << n):
            items.append(f'<li>{e(FLAG_TEXT.get(name, name))} (<a href="effects.html#{name}"><code>{name}</code></a>)</li>')
    for kind, ident in d.get("unlocks", []):
        items.append(f'<li>Unlocks {e(kind)} <b>{e(ident)}</b>{unlock_note(kind)} (<a href="effects.html#unlock-{kind}"><code>{kind}</code></a>)</li>')
    if not items:
        return "<p>No special effects.</p>\n"
    return '<ul>\n' + "\n".join(items) + "\n</ul>\n"


def units_table(d, vocab):
    rows = "".join(
        f'<tr><td>{e(UNIT_ROLE.get(role, role))}</td><td>{e(u["name"])}</td>'
        f'<td class="cell-num">{u["off"]}</td><td class="cell-num">{u["def"]}</td><td class="cell-num">{"drafted" if role == "soldier" else f"{u['gold']:,}"}</td></tr>\n'
        for role, u in zip(vocab["unit_slots"], d["units"]))
    return ('<div class="table-scroll" data-updated="none"><table>\n'
            '<tr><th>Unit slot</th><th>Unit</th><th class="cell-num">Offense</th><th class="cell-num">Defense</th><th class="cell-num">Gold</th></tr>\n'
            f"{rows}</table></div>\n")


UNIT_SHORT = {"soldier": "Soldiers", "offense": "Offense spec.", "defense": "Defense spec.", "elite": "Elite", "thief": "Thief",
              "offense+": "Offense spec.+", "defense+": "Defense spec.+", "elite+": "Elite+"}
LORE = CONTENT / "_lore.json"   # hand-written: {"troll": ["paragraph", ...]}; WY's lore for each race page
RACE_ART = CONTENT / "_race-art.json"   # hand-written: {"troll": {"file", "alt"}}; files in site/assets/races/
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
    other += [f'<li>Unlocks {e(kind)} <b>{e(ident)}</b>{unlock_note(kind)}</li>' for kind, ident in d.get("unlocks", [])]
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
        if role.endswith("+") or role == "soldier": continue   # soldiers are the same for every race
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

        # One page per identity: its lore (races), lineage and each distinct definition, with the
        # ages it was used in.
        try: lore = {k: v for k, v in json.loads(LORE.read_text(encoding="utf-8")).items() if not k.startswith("_")}
        except (OSError, ValueError): lore = {}
        try: art = {k: v for k, v in json.loads(RACE_ART.read_text(encoding="utf-8")).items() if not k.startswith("_")}
        except (OSError, ValueError): art = {}
        for ident, i in known.items():
            parts = []
            if singular == "race" and art.get(ident):
                a = art[ident]
                if not (ROOT / "site" / "assets" / "races" / a["file"]).exists():
                    problems.append(f'content/_race-art.json: site/assets/races/{a["file"]} is missing')
                parts.append(f'<figure class="race-art"><img src="assets/races/{e(a["file"])}" alt="{e(a["alt"])}" width="1000" height="1000"></figure>\n')
            if singular == "race" and lore.get(ident):
                parts.append('<h2 id="lore">Lore</h2>\n' + "".join(f"<p>{e(p)}</p>\n" for p in lore[ident]))
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
    uses = material_uses(latest)

    def used_for(mat):
        u = uses.get(mat["key"]["identity"])
        if not u: return e(mat["description"])
        text = ", ".join(u) + "."
        i = text.index(">") + 1 if text.startswith("<") else 0   # capitalise the first word, inside its link
        return text[:i] + text[i].upper() + text[i + 1:]
    mat_rows = []
    for m, mat in enumerate(mats):
        if m in refined_from: continue
        for n in [m] + [o for o in range(len(mats)) if refined_from.get(o) == m]:
            mat, ident = mats[n], mats[n]["key"]["identity"]
            if n == m:
                realms = [str(r + 1) for r, x in enumerate(latest["realm_material"]) if x == m]
                mat_rows.append(f'<tr id="{ident}"><td><b>{e(mat["name"])}</b></td><td>{used_for(mat)}</td>'
                                f'<td>{", ".join(realms)}</td><td class="cell-num">{mat["output_per_tick"]:,}</td>'
                                f'<td class="cell-num">{mat["output_per_tick"] * len(realms):,}</td></tr>')
            else:
                mat_rows.append(f'<tr id="{ident}" class="row-sub"><td><b>{e(mat["name"])}</b></td><td>{used_for(mat)}</td>'
                                f'<td>Refined from {e(mats[m]["name"])}</td><td class="cell-num cell-muted">–</td>'
                                f'<td class="cell-num cell-muted">–</td></tr>')
    pages["materials.html"] = header("Materials", "Rules, Economy") + (
        "<p>Every realm produces its own signature material each tick. No material comes from just one realm, "
        "so nobody can corner the market. See "
        '<a href="trade.html">Trade</a> for how production is shared and sold.</p>\n'
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
    lp = latest["params"]
    kinds = lp.get("attacks", [])
    names = {kind: {d["key"]["identity"]: d["name"] for d in latest.get(kind, [])} for kind in ("traits", "buildings", "sciences")}
    takes_text = {
        "land": lambda k: f'{pct(k["amount_bp"])}% of an assault\'s land' if k["amount_bp"] != 10_000 else f'land ({pct(lp["land_gain_bp"])}% of theirs)',
        "reclaim": lambda k: f'{pct(k["amount_bp"])}% of the land their army is carrying home from you',
        "buildings": lambda k: f'{pct(k["amount_bp"])}% of every building, left as barren land',
        "resources": lambda k: f'{pct(k["amount_bp"])}% of their gold, food and materials, carried home',
        "people": lambda k: f'{pct(k["amount_bp"])}% of their peasants and thieves, killed',
        "books": lambda k: f'{pct(k["amount_bp"])}% of their unspent books, carried home',
        "destroy": lambda k: f'destroys {pct(k["amount_bp"])}% of their land; nobody receives it',
        "blockade": lambda k: f'+{pct(k["amount_bp"])}% blockade on their whole state\'s material output (up to {pct(lp["blockade_max_bp"])}%, lifting {lp["blockade_ticks"]} ticks after the last); the lost output is destroyed',
    }

    def attack_effects(k):
        parts = [takes_text[k["takes"]](k)]
        part_names = {"thieves": "thieves", "upgraded": "upgraded units (offense+, defense+, elite+)", "chariots": "chariots", "medics": "medics"}
        share_names = {"all": "the share", "gold": "gold", "food": "food", "materials": "materials"}
        for b in k.get("army_bonuses", []):
            parts.append(f'{part_names[b["part"]]} add up to {pct(b["bp"])}% to {share_names[b["share"]]}, by their share of the troops sent')
        if k.get("overkill_max_bp", 10_000) > 10_000:
            parts.append(f'overwhelming force takes more: the share is multiplied by your offense &divide; their defense, up to {k["overkill_max_bp"] / 10_000:g}&times;')
        if k.get("defense_bonus_bp", 10_000) < 10_000:
            parts.append(f'their defense bonuses count for {pct(k["defense_bonus_bp"])}%')
        if k.get("attacker_losses_bp", 10_000) != 10_000:
            parts.append(f'{k["attacker_losses_bp"] / 10_000:g}&times; your usual losses')
        if k.get("breached_ticks"):
            parts.append(f'afterwards they stay breached for {k["breached_ticks"]} ticks (defense bonuses at {pct(k["breached_bonus_bp"])}% against anyone)')
        if k.get("renown"):
            parts.append("earns renown")
        return "; ".join(parts)

    def attack_needs(k):
        needs = []
        if k.get("own_state"):
            needs.append("a house in your own state")
        if k.get("requires_inactive_ticks"):
            needs.append(f'that has given no command for {k["requires_inactive_ticks"]} ticks')
        if k["takes"] == "reclaim":
            needs.append("only against the army that took your land")
        elif k.get("range_min_bp"):
            needs.append(f'target at least {pct(k["range_min_bp"])}% your size')
        if k.get("requires_trait"):
            needs.append(f'a general with the <a href="traits.html#{k["requires_trait"]}">{e(names["traits"].get(k["requires_trait"], k["requires_trait"]))}</a> trait leading the army')
        if k.get("requires_building"):
            needs.append(f'a finished <a href="buildings.html#{k["requires_building"]}">{e(names["buildings"].get(k["requires_building"], k["requires_building"]))}</a>')
        if k.get("requires_science"):
            needs.append(f'a <a href="sciences.html#{k["requires_science"]}">{e(names["sciences"].get(k["requires_science"], k["requires_science"]))}</a> bonus of {pct(k["requires_science_bp"])}%')
        if k.get("cost_material"):
            needs.append(f'1 <a href="materials.html">{e(k["cost_material"])}</a> per {k["troops_per_material"]} troops sent, spent win or lose')
        if k.get("war_only"):
            needs.append("war with their state")
        return "<br>".join(needs) or "any target"

    attack_rows = "".join(
        f'<tr id="{e(k["id"])}"><td><b>{e(k["name"])}</b><br><code>{e(k["id"])}</code></td>'
        f'<td>{attack_effects(k)}</td><td>{attack_needs(k)}</td>'
        f'<td class="cell-num">{"none" if k.get("own_state") else "&times;" + format(k["hostility_bp"] / 10_000, "g")}</td>'
        f'<td class="cell-num">{"+" + pct(k["war_bonus_bp"]) + "% (" + pct(k["amount_bp"] + k["war_bonus_bp"]) + "% in all)" if k.get("war_bonus_bp") else ""}</td></tr>\n'
        for k in kinds)
    hp = lp.get("hit_protection", {})
    curve = {
        "compound": lambda: f'each recent hit takes {pct(hp["step_bp"])}% off what\'s left, never below {pct(hp["floor_bp"])}% of the full amount',
        "linear": lambda: f'each recent hit takes {pct(hp["step_bp"])} points off the full amount, never below {pct(hp["floor_bp"])}%',
        "table": lambda: "after 0, 1, 2 ... recent hits an attack takes " + ", ".join(f'{pct(v)}%' for v in hp["shares_bp"]) + " of the full amount (the last share from then on)",
    }.get(hp.get("kind"), lambda: "")()
    shares = []
    if hp.get("kind") in ("compound", "linear"):
        left = 10_000
        for n in range(6):
            shares.append(f'<td class="cell-num">{pct(left)}%</td>')
            left = max(hp["floor_bp"], left * (10_000 - hp["step_bp"]) // 10_000 if hp["kind"] == "compound" else left - hp["step_bp"])
    elif hp.get("kind") == "table":
        shares = [f'<td class="cell-num">{pct(hp["shares_bp"][min(n, len(hp["shares_bp"]) - 1)])}%</td>' for n in range(6)]
    sg = lp.get("size_gains") or []
    size_section = ""
    if sg:
        size_section = ('<h2 id="size">Target size</h2>\n<p>You can attack a house of any size, but a smaller one yields much less. '
                        "The share of the usual gains depends on the target's land as a share of yours, in straight lines between these points "
                        "(flat beyond the ends). It scales land, plunder, kills, blockade pressure and renown, on top of protection from repeated hits.</p>\n"
                        '<div class="table-scroll" data-updated="none"><table>\n<tr><th>Their land, of yours</th>'
                        + "".join(f'<th class="cell-num">{pct(x)}%</th>' for x, _ in sg)
                        + '</tr>\n<tr><td>Share of the usual gains</td>' + "".join(f'<td class="cell-num">{pct(y)}%</td>' for _, y in sg) + "</tr>\n</table></div>\n")
    if attack_rows:
        pages["attacks.html"] = header("Attacks", "Rules") + (
            '<p>Every <a href="military.html#Attacking">attack</a> is one of these kinds. They all fight the same battle; they differ in what a win takes and what they need. '
            "The first is the default. Every attack is between states except Raze, which clears idle houses out of your own state.</p>\n"
            '<div class="table-scroll" data-updated="none"><table>\n<tr><th>Attack</th><th>A win</th><th>Needs</th>'
            '<th class="cell-num"><a href="war.html#Meter">Meter</a></th><th class="cell-num">At war</th></tr>\n'
            + attack_rows + "</table></div>\n"
            "<p><b>Meter</b> scales the hostility points the attack adds to the target state's meter. <b>At war</b> is added to the share taken when the two "
            "states are at war; land attacks get the war's land bonus instead. Only defense <em>bonuses</em> are cut by a Breach; penalties count in full.</p>\n"
            '<h2 id="protection">Protection from repeated hits</h2>\n'
            f'<p>A house that has been hit hard loses less to each new hit. Counting successful hits it took in the last {lp.get("hit_window_ticks", 0)} ticks, {curve}. '
            "This is applied last, after every other bonus. " + (", ".join(k["name"] for k in kinds if k.get("ignores_protection")) or "No attack") + " neither count as hits nor are reduced.</p>\n"
            + ('<div class="table-scroll" data-updated="none"><table>\n<tr><th>Recent hits</th>' + "".join(f'<th class="cell-num">{n}</th>' for n in range(6))
               + '</tr>\n<tr><td>Share taken</td>' + "".join(shares) + "</tr>\n</table></div>\n" if shares else "")
            + size_section
            + source_note(latest))

    # Rites.
    rites = lp.get("rites", [])
    stat_text = STAT_TEXT
    def rite_does(r):
        t = r["effect"]["type"]
        if t in ("modifiers", "curse"):
            return "; ".join(f'<span class="{"cell-bad" if t == "curse" else "cell-good"}">{"+" if m["bp"] > 0 else ""}{pct(m["bp"])}%</span> {e(stat_text.get(m["stat"], m["stat"])).lower()} (<a href="effects.html#{m["stat"]}"><code>{m["stat"]}</code></a>)' for m in r["mods"])
        war = f' ({pct(r["effect"].get("bp", 0) + r.get("war_bonus_bp", 0))}% at war)' if r.get("war_bonus_bp") else ""
        return {
            "veil": lambda: 'Your attacks show only "a veiled army" in the target\'s news',
            "mirror_ward": lambda: f'{pct(r["effect"]["bp"])}% of hexes against you turn back on their caster',
            "scry": lambda: "Reveals land, peasants, gold, food and troops at home",
            "omens": lambda: "Reveals the rites and hexes in force on a house",
            "roads": lambda: "Reveals a house's armies on the road, when they return, and whose land they carry",
            "rot": lambda: f'{pct(r["effect"]["bp"])}% of their food spoils every tick{war}',
            "storm": lambda: f'Wrecks {pct(r["effect"]["bp"])}% of every building{war}, less their ward',
            "blight": lambda: f'{pct(r["effect"]["bp"])}% of their share of the realm material is lost{war}',
            "unravel": lambda: "Ends one of their rites (the one lasting longest)",
        }[t]()
    def rite_kind(r):
        t = r["effect"]["type"]
        return "On yourself" if t in ("modifiers", "veil", "mirror_ward") else "Divination" if t in ("scry", "omens", "roads") else "Curse" if t == "curse" else "Hex"
    rite_rows = "".join(
        f'<tr id="{e(r["id"])}"><td><b>{e(r["name"])}</b><br><code>{e(r["id"])}</code></td><td>{rite_kind(r)}</td><td>{rite_does(r)}</td>'
        f'<td class="cell-num">{r["aether"]:,}{" + " + str(r["incense"]) + " " + e(lp["rite_material"]) if r.get("incense") else ""}</td>'
        f'<td class="cell-num">{str(r["ticks"]) + " ticks" if r.get("ticks") else "at once"}</td>'
        f'<td class="cell-num">{"always" if rite_kind(r) == "On yourself" else "&times;" + format(r.get("chance_bp", 10_000) / 10_000, "g")}</td></tr>\n'
        for r in rites)
    if rites:
        pages["rite-list.html"] = header("Rites and Hexes", "Rules") + (
            '<p>Every rite this age. How adepts, aether, chance and ward work is on <a href="rites.html">Rites</a>.</p>\n'
            '<div class="table-scroll" data-updated="none"><table>\n<tr><th>Rite</th><th>Kind</th><th>Does</th><th class="cell-num">Costs</th>'
            '<th class="cell-num">Lasts</th><th class="cell-num">Chance</th></tr>\n' + rite_rows + "</table></div>\n"
            "<p><b>Chance</b> scales the usual rite chance. Casting a rite again renews it rather than stacking. Curses are hexes that weaken the target while they last.</p>\n"
            + ('<h2 id="vigils">State vigils</h2>\n<p>A state\'s leader opens a vigil; members give aether and ' + e(lp["rite_material"]) + '. '
               "Once both are met, every house in the state has it. One at a time; opening another while one is still being funded loses what was given.</p>\n"
               '<div class="table-scroll" data-updated="none"><table>\n<tr><th>Vigil</th><th>Every member gets</th><th class="cell-num">Needs</th><th class="cell-num">Lasts</th></tr>\n'
               + "".join(f'<tr id="vigil-{e(v["id"])}"><td><b>{e(v["name"])}</b><br><code>{e(v["id"])}</code></td><td>'
                         + "; ".join(f'<span class="cell-good">+{pct(m["bp"])}%</span> {e(stat_text.get(m["stat"], m["stat"])).lower()}' for m in v["mods"])
                         + f'</td><td class="cell-num">{v["aether"]:,} aether + {v["incense"]} {e(lp["rite_material"])}</td><td class="cell-num">{v["ticks"]} ticks</td></tr>\n' for v in lp.get("vigils", []))
               + "</table></div>\n" if lp.get("vigils") else "")
            + source_note(latest))

    # Thieves' operations.
    ops = lp.get("operations", [])
    op_kind = {"survey": "Intel", "muster": "Intel", "ledgers": "Intel", "archives": "Intel", "couriers": "Intel", "dossier": "Intel",
               "steal_gold": "Theft", "steal_food": "Theft", "steal_material": "Theft", "steal_horses": "Theft", "steal_books": "Theft",
               "undermine": "Sabotage", "set_fires": "Sabotage", "cut_throats": "Sabotage", "propaganda": "Subversion", "kidnap": "Theft",
               "foul_forges": "Sabotage", "poison_wells": "Sabotage", "silence_adepts": "Sabotage",
               "forge_orders": "Subversion", "unsettle_general": "Subversion", "court_general": "Subversion",
               "court_scholar": "Subversion", "stir_unrest": "Subversion"}
    op_does = {
        "survey": lambda o: "Reveals buildings, construction and barren land",
        "muster": lambda o: "Reveals troops at home and away, training, medics, mounts, generals and armies coming home",
        "ledgers": lambda o: "Reveals gold, food, materials and open market orders",
        "archives": lambda o: "Reveals books invested per science, unspent books, scientists and academics",
        "couriers": lambda o: "Reveals their news from the last day",
        "dossier": lambda o: "Reveals everything (the full spy report)",
        "steal_gold": lambda o: f'Steals {pct(o["amount_bp"])}% of their gold, at most {o["cap_per_100_thieves"]:,} per 100 thieves',
        "steal_food": lambda o: f'Steals {pct(o["amount_bp"])}% of their food, at most {o["cap_per_100_thieves"]:,} per 100 thieves',
        "steal_material": lambda o: f'Steals {pct(o["amount_bp"])}% of one material you name, at most {o["cap_per_100_thieves"]:,} per 100 thieves',
        "steal_horses": lambda o: f'Steals {pct(o["amount_bp"])}% of their horses at home, at most {o["cap_per_100_thieves"]:,} per 100 thieves',
        "steal_books": lambda o: f'Steals {pct(o["amount_bp"])}% of their unspent books of one category you name, at most {o["cap_per_100_thieves"]:,} per 100 thieves',
        "undermine": lambda o: f'Delays everything they are building by {o["delay_ticks"]} ticks',
        "set_fires": lambda o: f'Burns {pct(o["amount_bp"])}% of one building type you name, at most {o["cap_per_100_thieves"]:,} per 100 thieves',
        "cut_throats": lambda o: f'Kills {pct(o["amount_bp"])}% of their troops at home, at most {o["cap_per_100_thieves"]:,} per 100 thieves',
        "propaganda": lambda o: f'{pct(o["amount_bp"])}% of their soldiers at home defect to you, at most {o["cap_per_100_thieves"]:,} per 100 thieves',
        "kidnap": lambda o: f'Carries off {pct(o["amount_bp"])}% of their peasants to your lands, at most {o["cap_per_100_thieves"]:,} per 100 thieves',
        "foul_forges": lambda o: f'Delays their troops and medics in training by {o["delay_ticks"]} ticks',
        "poison_wells": lambda o: f'Stops their peasant growth for {o["delay_ticks"]} ticks',
        "silence_adepts": lambda o: f'Kills {pct(o["amount_bp"])}% of their adepts, at most {o["cap_per_100_thieves"]:,} per 100 thieves',
        "forge_orders": lambda o: "Cancels their open market orders (their goods or gold go back to them)",
        "unsettle_general": lambda o: f'Their main general fights {pct(o["amount_bp"])}% weaker for {o["delay_ticks"]} ticks',
        "court_general": lambda o: 'One of their generals starts <a href="spying.html#Courting">wavering</a>',
        "court_scholar": lambda o: 'One of their academics starts <a href="spying.html#Courting">wavering</a>',
        "stir_unrest": lambda o: f'Their state\'s tax brings in {pct(o["amount_bp"])}% less for {o["delay_ticks"]} ticks',
    }
    def op_row(o):
        extra = []
        if o.get("war_bonus_bp"):
            extra.append(f'+{pct(o["war_bonus_bp"])}% at war')
        if o.get("cost_material"):
            extra.append(f'spends 1 {e(o["cost_material"])} per {o["thieves_per_material"]} thieves, win or lose')
        return (f'<tr id="{e(o["id"])}"><td><b>{e(o["name"])}</b><br><code>{e(o["id"])}</code></td><td>{op_kind[o["effect"]]}</td>'
                f'<td>{op_does[o["effect"]](o)}{"; " + "; ".join(extra) if extra else ""}</td>'
                f'<td class="cell-num">{pct(o["nerve_bp"])}%</td><td class="cell-num">&times;{o.get("chance_bp", 10_000) / 10_000:g}</td>'
                f'<td class="cell-num">+{pct(o["vigilance_bp"])}%</td>'
                f'<td class="cell-num">{lp["hostility_spy_points"] * o["hostility_bp"] / 10_000 / 100:g}</td></tr>\n')
    if ops:
        pages["operations.html"] = header("Thieves' Operations", "Rules") + (
            '<p>Every operation your thieves can run this age. How chance, Nerve and Vigilance work is on <a href="spying.html">Intrigue</a>.</p>\n'
            '<div class="table-scroll" data-updated="none"><table>\n<tr><th>Operation</th><th>Kind</th><th>Does</th>'
            '<th class="cell-num">Nerve</th><th class="cell-num">Chance</th><th class="cell-num">Vigilance</th><th class="cell-num"><a href="war.html#Meter">Meter points</a></th></tr>\n'
            + "".join(op_row(o) for o in ops) + "</table></div>\n"
            "<p><b>Chance</b> scales the usual spy chance. <b>Vigilance</b> is what the attempt adds to the target, success or not. "
            "<b>Meter points</b> are added to the target state's hostility meter toward yours.</p>\n" + source_note(latest))

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
        '<h2 id="conditions">Conditional modifiers</h2>\n<p>A modifier can hold only under conditions, all of which must hold: '
        '<code>when = "war"</code> (only while your state is at war) or <code>when = "peace"</code> (only while it isn\'t); '
        'for races, also <code>when = "overpopulated"</code> (more people than the house has room for) or <code>when = "well_fed"</code> (more food an acre than the race\'s limit); '
        '<code>vs = "troll"</code> (only in battles against a house of that race); <code>attack = "massacre"</code> (only on that kind of attack). '
        'Race pages show the conditions next to each modifier.</p>\n'
        '<h2 id="race-rules">Race rules</h2>\n<p>Beyond modifiers, a race can carry rules of its own: its name for soldiers, '
        'age settings it plays by differently (the mercenary ratio, what upgrades add), flat bonuses for some units, resistance or immunity to thieves\' '
        'operations, and signature mechanics (mirroring attackers, citizens who defend, promotions, activity, elites from war). '
        'Each race page lists its rules in plain words.</p>\n'
        '<h2 id="stacking">How modifiers stack</h2>\n'
        "<p>Every modifier on a stat adds together: race, personality, buildings, sciences, Colloquium projects, academics, "
        "rites, vigils and war standing. The total is applied once.</p>\n<ul>\n"
        "<li><b>Floor.</b> A total below &minus;100% counts as &minus;100%. The value falls to zero, never below. "
        "Construction can cost no gold at all, but a building's extra gold (the Ancestral Hall's, say) is never reduced.</li>\n"
        '<li><b>Building caps.</b> Each building caps its own bonus (see <a href="buildings.html">Buildings</a>). '
        "Building efficiency then multiplies it, so efficiency above 100% can pass the cap.</li>\n"
        "<li><b>No other caps on the total</b>, except these results: at most 80% of a target's "
        '<a href="#ward"><code>ward</code></a> counts; rite and operation chances stay between 1% and 95%; '
        '<a href="#trade_bonus"><code>trade_bonus</code></a> never goes below zero; and times never fall below an instant.</li>\n'
        "</ul>\n"
        '<h2 id="flags">Flags</h2>\n<p>Switch a rule on.</p>\n'
        '<div class="table-scroll" data-updated="none"><table>\n<tr><th>Flag</th><th>Effect</th></tr>\n' + flag_rows + "</table></div>\n"
        '<h2 id="unlocks">Unlocks</h2>\n'
        '<div class="table-scroll" data-updated="none"><table>\n<tr><th>Kind</th><th>Effect</th></tr>\n' + unlock_rows + "</table></div>\n"
        '<h2 id="units">Unit slots</h2>\n<p>Every race fills the same ' + str(len(vocab["unit_slots"])) + ' unit slots: '
        + ", ".join(e(UNIT_ROLE.get(u, u)).lower() for u in vocab["unit_slots"]) + ". A race names four base units; "
        "the upgraded slots are those units after an upgrade. Thieves have no upgrade.</p>\n" + source_note(latest))

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
    xg = lambda land: p["explore_gold_per_acre"] + land * p.get("explore_gold_per_land_milli", 0) // 1000
    xs = lambda land: land * p.get("explore_soldiers_per_land_milli", 0) / 1000
    values["explore_gold_land_each"] = f'{p.get("explore_gold_per_land_milli", 0) / 1000:g}'
    values["explore_soldiers_land_each"] = f'{p.get("explore_soldiers_per_land_milli", 0) / 1000:g}'
    values["explore_examples"] = "; ".join(f'{land:,} acres: {xg(land):,} gold and {xs(land):g} soldiers an acre' for land in (p["starting_land"], 1000, 2000, 4000))
    values["starting_peasants_only"] = f'{p["starting_peasants"] - p.get("starting_soldiers", 0):,}'
    values["train_general_per_1000_pct"] = f'{p.get("train_general_cbp", 0) * 1000 / 10_000:g}'   # chance per 1,000 elites trained
    mods_text = lambda mods: ", ".join(f'{"+" if m["bp"] > 0 else "−"}{pct(abs(m["bp"]))}% {STAT_TEXT.get(m["stat"], m["stat"]).lower()}' for m in mods)
    values["recovery_effects"] = mods_text(p.get("recovery_mods", []))
    values["peace_dividend_effects"] = mods_text(p.get("peace_dividend_mods", []))
    if "age_ticks" in p:
        values["age_weeks"] = f'{p["age_ticks"] * p["tick_ms"] / 3_600_000 / 168:g}'
    if "score_per_war_point" in p:
        values["score_war_point_pts"] = f'{p["score_per_war_point"] / 100:g}'
        values["might_unit_factor"] = f'{p["might_unit_pct"] / 100:g}'
    values["heir_slot_list"] = ", ".join(f'{n} at {r:,} renown' for n, r in enumerate(p.get("heir_slots_renown", []), 1))
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
        elif k not in ("tick_ms", "food_per_person_milli", "general_trait_renown", "academic_attribute_books", "rescue_bp_per_medic", "heir_slots_renown", "attacks", "operations", "hit_protection", "rites", "vigils", "recovery_mods", "peace_dividend_mods", "size_gains", "build_cost_per_land_milli", "raze_cost_per_land_milli", "science_ranks"):
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
