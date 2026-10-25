# Eval deck card corpus

Machine-readable rules text for every card in the harness's eval decks, so a
playing model can be handed the exact text instead of recalling it from
training.

## Provenance

- Source: the Scryfall API, `GET https://api.scryfall.com/cards/named?exact=<name>`
  (see <https://scryfall.com/docs/api/cards/named>).
- Fetched: **2026-10-12** by `build.py` in this directory.
- Scope: the 16 decklists that were in `eval/decks/` on that date, the Legacy
  eval pool (234 distinct cards; 0 unresolved; `by_deck.json` names them). The
  format decks and `hook-*` probes added since are not in it.
- Nothing in the harness reads this corpus: the seat is shown each card's rules
  text from the engine.
- The printing returned is Scryfall's default for an exact-name lookup; each
  record carries its own `scryfall_id` and `released_at` for that printing.

## Files

| File | Contents |
| --- | --- |
| `cards.jsonl` | One JSON object per line, one line per distinct card. |
| `by_deck.json` | Deck filename -> ordered list of distinct card names in it (counts folded away). |
| `unresolved.txt` | Names Scryfall could not resolve. Only written when non-empty. |
| `build.py` | Stdlib-only fetcher that reproduces everything here. |

`name` in `cards.jsonl` matches the names listed in `by_deck.json`, so the two
join directly. The canonical Scryfall name is kept in `scryfall_name` when it
differs (e.g. transform cards, whose canonical name is `Front // Back`).

## Schema (`cards.jsonl`)

```
{"name", "mana_cost", "type_line", "oracle_text", "power", "toughness",
  "loyalty", "colors", "keywords", "scryfall_id", "released_at"}
```

- `name` — name as written in the deck (a DFC's front face).
- `mana_cost` — e.g. `"{2}{U}"`; `null` for lands.
- `type_line` — e.g. `"Legendary Creature — Human Wizard"`.
- `oracle_text` — full rules text; `null` for vanilla cards.
- `power` / `toughness` — creature stats, else `null`.
- `loyalty` — planeswalker loyalty, else `null`.
- `colors` — WUBRG list, e.g. `["U"]`; `[]` for colorless/lands.
- `keywords` — Scryfall keyword list, e.g. `["Flying", "Ward"]`.
- `scryfall_id` — Scryfall card UUID for the printing used.
- `released_at` — `YYYY-MM-DD` release date of that printing.
- `scryfall_name` — *only when different* from `name` (multi-face cards).

Multi-face cards (transform / modal DFC) have no top-level text in the API;
their faces are flattened into the combined fields with ` // ` (type line,
mana cost, stats) and `\n//\n` (oracle text) separators.

## Refresh

```
python3 data/cards/build.py
```

Stdlib only. It reads every `*.txt` in `--decks-dir` (default `eval/decks/`), so a
refresh today would also fetch the format decks and the `hook-*` probes. The script
sleeps between requests and sends a User-Agent, per Scryfall's API guidelines.

## Licensing / attribution

Card names, rules text, mana costs and type lines are Wizards of the Coast's
intellectual property; this corpus is a local, non-commercial evaluation aid
and is not a redistribution of Magic: The Gathering. Card data is retrieved
from Scryfall (<https://scryfall.com>), whose data is community-shared; many
fields originate with Wizards of the Coast and with Scryfall's contributors.
Follow Scryfall's API guidelines and Wizards' Fan Content Policy if you move
this data anywhere public.
