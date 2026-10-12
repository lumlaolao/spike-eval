#!/usr/bin/env python3
"""Build a machine-readable card-knowledge corpus for the eval decks.

Fetches every distinct card in eval/decks/*.txt from the Scryfall API and
writes, next to this script:

  cards.jsonl      one JSON object per distinct card (the knowledge corpus)
  by_deck.json     deck filename -> ordered list of distinct card names
  unresolved.txt   names Scryfall could not resolve (only written if any)
  README.md        provenance, schema, refresh + licensing notes

Stdlib only (urllib), no third-party deps. Re-run to refresh:

    python3 data/cards/build.py

Notes
-----
* Politeness: a User-Agent is set and we sleep between calls, per Scryfall's
  API guidelines (https://scryfall.com/docs/api).
* Transform / modal double-faced cards (layout "transform", "modal_dfc", ...)
  keep their per-face data in ``card_faces`` while the top-level
  mana_cost/oracle_text/colors are null. We flatten the faces into the
  combined fields with " // " / "\\n//\\n" separators so a playing model
  still gets the complete rules text on one line.
* The ``name`` field is the name *as written in the deck* (a DFC's front
  face), so cards.jsonl joins cleanly against by_deck.json. The canonical
  Scryfall name is preserved in ``scryfall_name`` when it differs.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
DEFAULT_DECKS_DIR = os.path.join(REPO_ROOT, "eval", "decks")

API = "https://api.scryfall.com/cards/named"
USER_AGENT = "mtg-forge-llm-agent/eval-corpus (local evaluation; contact: repo owner)"
SLEEP_SECONDS = 0.1
MAX_RETRIES = 4

DECK_LINE = re.compile(r"^(\d+)\s+(.+?)\s*$")


def parse_deck_file(path):
    """Return deck card names (distinct, first-seen order) from one deck file.

    A line is '<count> <Card Name>'. Blank lines are separators (main /
    sideboard) and are ignored; counts are folded away.
    """
    names = []
    seen = set()
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line:
                continue
            m = DECK_LINE.match(line)
            if not m:
                print(f"  ! {os.path.basename(path)}:{lineno}: unparsed line {line!r}",
                      file=sys.stderr)
                continue
            name = m.group(2)
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def load_decks(deck_dir):
    """Return {deck_filename: [distinct card names]} plus the global distinct set."""
    by_deck = {}
    distinct = []
    seen = set()
    for path in sorted(glob.glob(os.path.join(deck_dir, "*.txt"))):
        fname = os.path.basename(path)
        names = parse_deck_file(path)
        by_deck[fname] = names
        for n in names:
            if n not in seen:
                seen.add(n)
                distinct.append(n)
    return by_deck, distinct


def _http_get_json(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_card(name):
    """Fetch one card by exact name. Returns (card_dict, error_str)."""
    url = API + "?" + urllib.parse.urlencode({"exact": name})
    delay = SLEEP_SECONDS
    for attempt in range(MAX_RETRIES + 1):
        try:
            time.sleep(delay)
            return _http_get_json(url), None
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None, "404 not found"
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else max(1.0, delay * 4)
                print(f"  ~ 429 rate limited on {name!r}; waiting {wait:.1f}s",
                      file=sys.stderr)
                delay = wait
                continue
            if 500 <= exc.code < 600 and attempt < MAX_RETRIES:
                delay = max(1.0, delay * 3)
                continue
            return None, f"HTTP {exc.code}"
        except urllib.error.URLError as exc:
            if attempt < MAX_RETRIES:
                delay = max(1.0, delay * 3)
                continue
            return None, f"network error: {exc.reason}"
    return None, "exhausted retries"


def _join(values, sep=" // "):
    vals = [v for v in values if v]
    return sep.join(vals) if vals else None


def flatten(card, requested_name):
    """Reduce a Scryfall card object to the player-facing schema."""
    faces = card.get("card_faces") or []
    if faces and card.get("oracle_text") is None:
        mana_cost = _join(f.get("mana_cost") for f in faces)
        oracle_text = _join(((f.get("oracle_text") or "").strip() for f in faces
                             if f.get("oracle_text")), sep="\n//\n")
        power = _join(f.get("power") for f in faces)
        toughness = _join(f.get("toughness") for f in faces)
        loyalty = _join(f.get("loyalty") for f in faces)
        colors = []
        for f in faces:
            for c in f.get("colors") or []:
                if c not in colors:
                    colors.append(c)
    else:
        mana_cost = card.get("mana_cost") or None
        oracle_text = (card.get("oracle_text") or "").strip() or None
        power = card.get("power")
        toughness = card.get("toughness")
        loyalty = card.get("loyalty")
        colors = list(card.get("colors") or [])

    rec = {
        "name": requested_name,
        "mana_cost": mana_cost,
        "type_line": card.get("type_line"),
        "oracle_text": oracle_text,
        "power": power,
        "toughness": toughness,
        "loyalty": loyalty,
        "colors": colors,
        "keywords": list(card.get("keywords") or []),
        "scryfall_id": card.get("id"),
        "released_at": card.get("released_at"),
    }
    if card.get("name") and card["name"] != requested_name:
        # e.g. a transform card whose canonical name is "Front // Back".
        rec["scryfall_name"] = card["name"]
    return rec


README_TEMPLATE = """# Eval deck card corpus

Machine-readable rules text for every card in the harness's eval decks, so a
playing model can be handed the exact text instead of recalling it from
training.

## Provenance

- Source: the Scryfall API, `GET https://api.scryfall.com/cards/named?exact=<name>`
  (see <https://scryfall.com/docs/api/cards/named>).
- Fetched: **{fetched}** by `build.py` in this directory.
- Scope: the {n_decks} decklists in `eval/decks/*.txt`
  ({n_distinct} distinct cards; {n_unresolved} unresolved).
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
{{"name", "mana_cost", "type_line", "oracle_text", "power", "toughness",
  "loyalty", "colors", "keywords", "scryfall_id", "released_at"}}
```

- `name` — name as written in the deck (a DFC's front face).
- `mana_cost` — e.g. `"{{2}}{{U}}"`; `null` for lands.
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
mana cost, stats) and `\\n//\\n` (oracle text) separators.

## Refresh

```
python3 data/cards/build.py
```

Stdlib only. Set `--decks-dir` to override the decklist source. The script
sleeps between requests and sends a User-Agent, per Scryfall's API guidelines.

## Licensing / attribution

Card names, rules text, mana costs and type lines are Wizards of the Coast's
intellectual property; this corpus is a local, non-commercial evaluation aid
and is not a redistribution of Magic: The Gathering. Card data is retrieved
from Scryfall (<https://scryfall.com>), whose data is community-shared; many
fields originate with Wizards of the Coast and with Scryfall's contributors.
Follow Scryfall's API guidelines and Wizards' Fan Content Policy if you move
this data anywhere public.
"""


def write_readme(fetched, n_decks, n_distinct, n_unresolved):
    text = README_TEMPLATE.format(
        fetched=fetched, n_decks=n_decks, n_distinct=n_distinct,
        n_unresolved=n_unresolved,
    )
    with open(os.path.join(HERE, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(text)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--decks-dir", default=DEFAULT_DECKS_DIR,
                    help="directory of 'N Cardname' decklist .txt files")
    ap.add_argument("--out-dir", default=HERE, help="output directory")
    args = ap.parse_args(argv)

    by_deck, distinct = load_decks(args.decks_dir)
    print(f"decks: {len(by_deck)}  distinct cards: {len(distinct)}", file=sys.stderr)

    records = []
    unresolved = []
    for i, name in enumerate(distinct, 1):
        card, err = fetch_card(name)
        if card is None:
            unresolved.append(name)
            print(f"[{i}/{len(distinct)}] UNRESOLVED {name!r} ({err})", file=sys.stderr)
            continue
        records.append(flatten(card, name))
        if i % 25 == 0 or i == len(distinct):
            print(f"[{i}/{len(distinct)}] ok", file=sys.stderr)

    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "cards.jsonl"), "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    with open(os.path.join(args.out_dir, "by_deck.json"), "w", encoding="utf-8") as fh:
        json.dump(by_deck, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    unresolved_path = os.path.join(args.out_dir, "unresolved.txt")
    if unresolved:
        with open(unresolved_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(unresolved) + "\n")
    elif os.path.exists(unresolved_path):
        os.remove(unresolved_path)

    write_readme(date.today().isoformat(), len(by_deck), len(distinct), len(unresolved))

    print(f"resolved {len(records)}/{len(distinct)}; wrote cards.jsonl, by_deck.json, "
          f"README.md" + (f", unresolved.txt ({len(unresolved)})" if unresolved else ""),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
