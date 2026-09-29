# Realmstate Wiki

Wiki for the game at `../realmstate`, built as the game progresses. Read `README.md` first.

Identity: **wizard1146** (`yellowhat1146@gmail.com`), SSH host alias `github-wizard1146`,
remote `git@github-wizard1146:wizard1146/realmstate-wiki.git`.

## Rules
- Never hand-edit a page whose header says `generated: tools/sync.py`. Change the game's `rules/` (or
  `tools/sync.py` for wording), run `python3 tools/sync.py`, commit both the data and the pages.
- Guide pages never type game numbers: use `{{name}}` from `content/_values.json` (written by sync.py).
- A new effect type in the game needs a description in `tools/sync.py` (STAT_TEXT / FLAG_TEXT / UNLOCK_TEXT);
  `--check` fails until it has one.
- When a game mechanic changes (a formula in realm-engine), update the matching guide page in the same session.
- Groups of kingdoms are **realms**, never "islands".
- Tooling is copied from Scriptorium (same identity, MIT); keep it standard-library Python, no JavaScript in content.
