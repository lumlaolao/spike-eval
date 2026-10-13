# Modern MTG rules puzzles — a sourced, extendable corpus

Real, non-trivial Magic: The Gathering **rules / timing / combat / interaction**
puzzles with **source-attributed ground truth**, aimed at evaluating an AI agent
(and at seeding a rules-engine-verifiable puzzle suite).

"Modern" here means **contemporary cards and current rules** (roughly 2019+
printings; rules text as of the latest updates), not the Modern *format*. Each
puzzle records the format in which all of its referenced cards are legal.

## Files

| Path | What it is |
|---|---|
| `puzzles.jsonl` | The corpus — one JSON object per line. |
| `CATALOG.md` | Human-readable index grouped by rules area, with counts. |
| `harvest.py` | The repeatable harvester (stdlib only): collect → convert → normalise → catalog. |
| `tools/make_curated.py` | Emits the hand-curated items (Cranial Insertion, Ask a Magic Judge, official rulings) in target schema. |
| `raw/api/` | As-collected payloads (RulesGuru API dumps, `sets.json` from RulesGuru's set list). |
| `raw/items/` | Converted/curated items in target schema — the input to `normalize`. |

## Pipeline

```
raw/api/*.jsonl ──convert──▶ raw/items/*.jsonl ──normalize──▶ puzzles.jsonl ──catalog──▶ CATALOG.md
        (as fetched)              (target schema)       (validate + de-dupe)
```

De-duplication key: **`sha256(source_url + normalized(question))[:16]`**. The same
key is the puzzle `id` (prefixed with a source slug), so re-running the pipeline
is idempotent and new sources can be added without collisions.

### Commands

```bash
# 1. (optional) collect raw items from the public RulesGuru API (needs network).
#    The server asks for >= 1 request / 2s; keep --delay >= 2.
python3 harvest.py fetch-rulesguru --n 150 --delay 2.2 --profile depth
python3 harvest.py fetch-rulesguru --n 30  --delay 2.2 --profile breadth

# 2. convert raw payloads to target schema (drops pre-2019 card sets and
#    questions whose answers the source itself marks unsupported)
python3 harvest.py convert

# 3. regenerate the curated hand-written items (no network)
python3 tools/make_curated.py

# 4. validate + de-dupe -> puzzles.jsonl
python3 harvest.py normalize

# 5. histograms / catalog
python3 harvest.py stats
python3 harvest.py catalog
```

## Item schema (additive; unknown keys are preserved)

```json
{
  "id": "rulesguru-<hash16>",
  "title": "short human title",
  "source_url": "https://rulesguru.org/?2078",
  "source_name": "RulesGuru",
  "date": "2024",                 // see "date conventions" below
  "era": "2024+",                 // card era bucket
  "format": "Modern",             // most restrictive format legal for all cards in the puzzle
  "question": "the puzzle prompt",
  "board_state": "free text, or a card list when the question is self-contained",
  "answer": "the ruling",
  "answer_authority": "judge-article | official-ruling | consensus | unverified",
  "rules_areas": ["layers", "replacement"],
  "difficulty": "easy | medium | hard | expert",
  "machine_verifiable": true,
  "notes": "provenance + caveats",
  "verified": true,               // source actually fetched (vs inferred)
  "cards": ["Card A", "Card B"],
  "cited_rules": {"608.2c": "rule text (truncated)"}
}
```

### Field conventions (read these before using the data)

- **`verified`** — `true` only where this harvest actually fetched the source
  (RulesGuru API, the Cranial Insertion article, the Ask a Magic Judge post, or
  the Wizards ruling via Scryfall's rulings API). Every item in the current
  corpus is `true`; the field exists so future, inferred items are marked
  `false` rather than silently mixed in.
- **`answer_authority`** — `judge-article` = answered by a judge in a rules
  column/database that states Comprehensive Rules citations; `official-ruling` =
  Wizards' own Gatherer ruling text. `consensus` / `unverified` are supported by
  the schema but no item currently uses them (nothing was included on community
  say-so alone).
- **`date`** — for RulesGuru items this is the **year of the newest printing of
  any referenced card** (the API has no per-question date); for curated items it
  is the **source publication date**. `era` is the same idea bucketed to `YYYY+`.
- **`difficulty`** — RulesGuru `level` 0→`easy`, 1→`medium`, 2→`hard`,
  3 / Corner Case→`expert`; curated items were graded by the transcriber.
- **`machine_verifiable`** — `true` when the answer is a deterministic outcome of
  the CR (layers, SBAs, priority, replacement, damage assignment, triggers), i.e.
  in principle computable by a correct rules engine (e.g. Forge). It means
  *deterministic*, **not** *independently engine-verified in this harvest*. Items
  resting on hidden information, politics, or deck-construction/legality
  knowledge are `false`.
- **`format`** — the most restrictive format (Standard → Pioneer → Modern →
  Legacy → Vintage → Commander) in which *every* referenced card is legal.

## Sources and attribution

| Source | Items | Authority | How collected |
|---|---|---|---|
| [RulesGuru](https://rulesguru.org) | 327 | judge-curated Q&A with explicit CR citations | public JSON API (`/api/questions/`), one random matching question per request with criteria rotation |
| [Cranial Insertion](https://www.cranial-insertion.com) | 56 | long-running judge rules column (L2+ judges) | article HTML, Q&A pairs transcribed |
| [Ask a Magic Judge](https://magicjudge.tumblr.com) | 108 | judge-run rules blog | Tumblr read API (`/api/read/json?type=answer`) |
| Wizards official card rulings (Gatherer, via [Scryfall](https://scryfall.com/docs/api/rulings)) | 49 | `official-ruling` | Scryfall rulings endpoint (`.../cards/<id>/rulings`), interactions picked from the ruling text |
| [Judge Foundry](https://judgefoundry.org) | 3 | `judge-article` (quiz wrap-ups with explicit CR cites) | judge-authored Q&A wrap-up articles |
| Wizards of the Coast — **Release Notes** (`magic.wizards.com/en/news/feature/*-release-notes`) | 60 | `official-ruling` (the set's own Card-Specific Notes) | article HTML, the "Card-Specific Notes" Q&A transcribed |
| [EDHREC](https://edhrec.com) | 3 | `judge-article` ("Judge Alex, Rules Attorney at Law") | column HTML |

**Total: 606** (2026-10-13). The 2026-10-13 extension added 411 items (RulesGuru 162→327,
Cranial Insertion 23→56, Ask a Magic Judge 7→108, official rulings 3→49, Judge Foundry / Wizards
Release Notes / EDHREC new) — all
`verified: true` against a source actually fetched, all current-rules/2019+.

Sources deliberately **not** used for corpus items (see Gaps): r/mtgpuzzles
(board states are images, not text) and mtgpuzzles.com (pictorial solutions).

### Licensing / provenance note

This directory stores **facts and short rulings**, transcribed or lightly
paraphrased, each with a source URL and publication date — not long passages of
copyrighted prose. Card names, oracle text, and the Comprehensive Rules are
© Wizards of the Coast LLC. The cited sites are unofficial fan content and are
not affiliated with or endorsed by Wizards of the Coast. If you extend this
corpus, keep to the same rule: **transcribe the fact and cite it**, and keep
verbatim quotes short.

## How to extend

1. **More RulesGuru items** — re-run `fetch-rulesguru` with a different
   `--profile`, or add criteria to `RG_QUERIES` / `RG_QUERIES_BREADTH`
   (tags are listed in `RAW/api/rulesguru.jsonl`). De-dupe is automatic.
2. **A new source** — either (a) write a converter that appends target-schema
   objects to `raw/items/<source>.jsonl`, or (b) drop a `raw/api/<source>.jsonl`
   and add a converter function alongside `rg_convert`. Then `normalize`.
3. **New rules areas** — add entries to `RULE_AREA_SYNONYMS` (curated sources) or
   `RG_TAG_MAP` (RulesGuru tags) so areas stay canonical and the histogram stays
   readable.
4. **Re-check answers after a rules update** — items carry `cited_rules` where
   the source cited CR numbers; bump `notes` with the update that changed the
   answer rather than deleting the item (the corpus is additive and versioned by
   git history).

## Known gaps

- No item comes from **r/mtgpuzzles** or **mtgpuzzles.com**: their boards are
  images and their solutions are pictorial, so board states cannot be recorded as
  text without OCR/vision, and most of the subreddit's top content is 2016–2018.
- **Layer/continuous-effect** coverage is thinner than trigger/zone coverage;
  only a handful of items exercise dependency (613.8) explicitly.
- **Multiplayer / Two-Headed Giant** coverage is minimal (3 items).
- RulesGuru carries **no per-question date**, so `date` is a card-era proxy.
- `machine_verifiable` has not been checked against an actual engine run; doing
  so (Forge `GameCopier`-style replay) is the natural next step.
