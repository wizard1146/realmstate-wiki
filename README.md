# Realmstate Wiki

The wiki for [Realmstate](https://github.com/wizard1146/realmstate), built alongside the game.
Live at https://wizard1146.github.io/realmstate-wiki/

- **Rules pages** (races, personalities, effects, ages) are generated from the game's own rule files by
  `tools/sync.py`, so every number is the one the engine uses.
- **Guide pages** are hand-written HTML in `content/`. Their numbers come from the current age through
  `{{name}}` values, so they update when the rules do.

Built with the same tooling as Scriptorium: HTML fragments, a standard-library Python build, GitHub Pages.

```sh
python3 tools/sync.py            # export rules from ../realmstate (or $REALMSTATE) and regenerate pages
python3 tools/sync.py --check    # CI: generated pages must match data/rules.json
python3 build.py --check         # CI: build, fail on broken links or unknown {{values}}
python3 serve.py                 # preview at http://localhost:8000
```

Page anatomy and CSS classes: `docs/ANATOMY.md`. How to edit: `content/contribute.html`.
