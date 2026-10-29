#!/usr/bin/env python3
"""harvest.py -- normalise raw collected MTG rules-puzzle items into puzzles.jsonl.

Stdlib only. No network needed for `normalize`/`catalog`/`stats`; the optional
`fetch-rulesguru` subcommand collects raw items from the public RulesGuru API
(https://rulesguru.org), which is a judge-curated rules-question database.

Pipeline
--------
    raw/*.jsonl  (per-source raw items, see RAW SCHEMA below)
        |  normalize  (validate, canonicalise url, derive id, de-dupe)
        v
    puzzles.jsonl  (one JSON object per line, the corpus)
        |  catalog
        v
    CATALOG.md  (human-readable index + counts)

Raw schema (one JSON object per line; all keys optional except q/answer):
    {
      "source_name":   "Cranial Insertion" | "RulesGuru" | ...,
      "source_url":    "https://...",           # canonical citation
      "title":         "short puzzle title",
      "date":          "YYYY-MM-DD" or "YYYY",  # source publication date
      "era":           "2019+" | "2021+" | ...,  # newest card-set era touched
      "format":        "Modern" | "Commander" | "No format" | ...,
      "question":      "...",                    # the puzzle prompt (FACTS, paraphrased)
      "board_state":   "...",                    # free text or JSON string
      "answer":        "...",                    # the ruling
      "answer_authority": "judge-article" | "official-ruling" | "consensus" | "unverified",
      "rules_areas":   ["layers", ...],
      "difficulty":    "easy" | "medium" | "hard" | "expert",
      "machine_verifiable": true | false,
      "notes":         "...",
      "verified":      true | false,             # did we actually fetch the source?
      "cards":         ["Card Name", ...],
      "cited_rules":   {"608.2c": "..."}         # CR rule numbers -> text (optional)
    }

Usage
-----
    python3 harvest.py fetch-rulesguru --n 180
    python3 harvest.py normalize
    python3 harvest.py catalog
    python3 harvest.py stats
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "raw")
RAW_API_DIR = os.path.join(RAW_DIR, "api")     # as-collected payloads
ITEMS_DIR = os.path.join(RAW_DIR, "items")     # converted -> target schema
OUT_JSONL = os.path.join(HERE, "puzzles.jsonl")
CATALOG = os.path.join(HERE, "CATALOG.md")

ALLOWED_AUTHORITY = {"judge-article", "official-ruling", "consensus", "unverified"}
ALLOWED_DIFFICULTY = {"easy", "medium", "hard", "expert"}
REQUIRED = ["id", "title", "source_url", "source_name", "date", "era", "format",
            "question", "board_state", "answer", "answer_authority",
            "rules_areas", "difficulty", "machine_verifiable", "notes"]

# Canalisation of free-text rules tags onto a small, stable vocabulary.
RULE_AREA_SYNONYMS = {
    "layers": "layers",
    "continuous effects": "layers",
    "state-based actions": "state-based-actions",
    "triggered abilities": "triggered-abilities",
    "triggers": "triggered-abilities",
    "replacement effects": "replacement",
    "replacement": "replacement",
    "prevention": "replacement",
    "priority": "priority",
    "casting spells": "casting",
    "casting": "casting",
    "costs": "costs",
    "additional costs": "costs",
    "targets": "targets",
    "combat": "combat",
    "blocking": "combat",
    "attacking": "combat",
    "damage": "damage",
    "damage assignment": "damage-assignment",
    "stack": "stack",
    "copy effects": "copying",
    "copying": "copying",
    "tokens": "tokens",
    "counters": "counters",
    "zones": "zone-changes",
    "zone-changes": "zone-changes",
    "resolving objects": "stack",
    "turn structure": "turn-structure",
    "mana": "mana",
    "legend rule": "legend-rule",
    "control": "control",
    "lifegain": "life-totals",
    "life totals": "life-totals",
    "commander": "commander",
    "deck construction": "deck-construction",
    "characteristic-defining abilities": "layers",
    "last known information": "last-known-information",
    "keywords": "keywords",
    "morph": "face-down",
    "face down": "face-down",
    "politics": "multiplayer",
    "multiplayer": "multiplayer",
    "hidden information": "hidden-information",
    "policy": "policy",
}


def canon_area(tag: str) -> str:
    t = tag.strip().lower()
    if t in RULE_AREA_SYNONYMS:
        return RULE_AREA_SYNONYMS[t]
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def norm_question(q: str) -> str:
    """Collapse whitespace + lowercase for the de-dup hash."""
    return re.sub(r"\s+", " ", (q or "").lower()).strip()


def dedup_key(rec: dict) -> str:
    return sha16(rec.get("source_url", "") + "\n" + norm_question(rec.get("question", "")))


def load_raw(raw_dir: str) -> list:
    items = []
    if not os.path.isdir(raw_dir):
        return items
    for name in sorted(os.listdir(raw_dir)):
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(raw_dir, name)
        with open(path, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    print(f"[warn] {name}:{lineno}: bad JSON ({exc}); skipped", file=sys.stderr)
                    continue
                obj.setdefault("_raw_file", name)
                items.append(obj)
    return items


def normalise(rec: dict) -> dict:
    out = dict(rec)
    out.pop("_raw_file", None)

    # --- canonicalise url, derive stable id -------------------------------
    url = (out.get("source_url") or "").strip()
    out["source_url"] = url
    out.setdefault("source_name", "unknown")
    out.setdefault("format", "No format")
    out.setdefault("era", str(out.get("date", ""))[:4] or "unknown")
    out.setdefault("board_state", "not recorded (see question)")
    out.setdefault("answer_authority", "unverified")
    out.setdefault("difficulty", "medium")
    out.setdefault("machine_verifiable", False)
    out.setdefault("notes", "")
    out.setdefault("verified", False)

    areas = out.get("rules_areas") or []
    if isinstance(areas, str):
        areas = [p for p in re.split(r"[,;]", areas) if p.strip()]
    seen, clean = set(), []
    for a in areas:
        c = canon_area(str(a))
        if c and c not in seen:
            seen.add(c)
            clean.append(c)
    out["rules_areas"] = clean

    if not out["title"]:
        out["title"] = norm_question(out.get("question", ""))[:70] or "(untitled)"
    out["date"] = str(out.get("date", "") or "unknown")

    # stable per-source id: <source-slug>-<hash(source_url + question)>
    slug = re.sub(r"[^a-z0-9]+", "-", str(out["source_name"]).lower()).strip("-") or "src"
    out["id"] = f"{slug}-{dedup_key(out)}"

    # keep only known-ish keys but preserve extras (additive schema)
    out["_dedup_key"] = dedup_key(out)
    return out


def validate(rec: dict) -> list:
    errs = []
    for k in REQUIRED:
        if k not in rec or rec[k] in (None, "", []):
            errs.append(f"missing {k}")
    if rec.get("answer_authority") not in ALLOWED_AUTHORITY:
        errs.append(f"bad answer_authority {rec.get('answer_authority')!r}")
    if rec.get("difficulty") not in ALLOWED_DIFFICULTY:
        errs.append(f"bad difficulty {rec.get('difficulty')!r}")
    if not isinstance(rec.get("machine_verifiable"), bool):
        errs.append("machine_verifiable not bool")
    if not isinstance(rec.get("rules_areas"), list) or not rec["rules_areas"]:
        errs.append("rules_areas empty")
    return errs


def cmd_normalize(args) -> int:
    raw = load_raw(args.raw_dir)
    by_key, dropped, invalid = {}, 0, 0
    for rec in raw:
        rec = normalise(rec)
        errs = validate(rec)
        if errs:
            invalid += 1
            print(f"[warn] invalid record ({'; '.join(errs)}): {rec.get('title','?')[:60]}", file=sys.stderr)
            continue
        k = rec.pop("_dedup_key")
        if k in by_key:
            dropped += 1
            continue
        rec["_sort"] = (rec["source_name"], rec["date"], rec["title"])
        by_key[k] = rec

    records = list(by_key.values())
    records.sort(key=lambda r: (r["source_name"], r["date"], r["title"]))
    for r in records:
        r.pop("_sort", None)

    with open(args.out, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")

    print(f"normalize: raw={len(raw)} written={len(records)} dupes_dropped={dropped} invalid={invalid}")
    print(f"wrote {os.path.relpath(args.out, HERE)}")
    return 0


def _hist(records, keyfn):
    hist = {}
    for r in records:
        for k in keyfn(r):
            hist[k] = hist.get(k, 0) + 1
    return hist


def cmd_catalog(args) -> int:
    records = [json.loads(l) for l in open(args.jsonl, encoding="utf-8") if l.strip()]
    verified = sum(1 for r in records if r.get("verified"))
    mv = sum(1 for r in records if r.get("machine_verifiable"))
    hard = sum(1 for r in records if r["difficulty"] in ("medium", "hard", "expert"))

    lines = []
    lines.append("# Modern MTG puzzle corpus -- catalog\n")
    lines.append(f"Generated by `harvest.py catalog` from `{os.path.basename(args.jsonl)}`.\n")
    lines.append(f"- **Total puzzles:** {len(records)}")
    lines.append(f"- **Verified against a fetched source:** {verified} / {len(records)}")
    lines.append(f"- **Machine-verifiable answers:** {mv}")
    lines.append(f"- **Medium/hard/expert:** {hard}\n")

    lines.append("## Counts by rules area\n")
    lines.append("| rules area | n |")
    lines.append("|---|---|")
    for k, v in sorted(_hist(records, lambda r: r["rules_areas"]).items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| `{k}` | {v} |")
    lines.append("")

    lines.append("## Counts by difficulty\n")
    lines.append("| difficulty | n |")
    lines.append("|---|---|")
    for d in ("easy", "medium", "hard", "expert"):
        lines.append(f"| {d} | {sum(1 for r in records if r['difficulty'] == d)} |")
    lines.append("")

    lines.append("## Counts by era\n")
    lines.append("| era | n |")
    lines.append("|---|---|")
    eras = _hist(records, lambda r: [r.get("era") or "unknown"])
    for k, v in sorted(eras.items()):
        lines.append(f"| {k} | {v} |")
    lines.append("")

    lines.append("## Difficulty x primary rules area\n")
    all_areas = sorted({r["rules_areas"][0] for r in records})
    lines.append("| area | easy | medium | hard | expert | total |")
    lines.append("|---|---|---|---|---|---|")
    for a in sorted(all_areas):
        row = [sum(1 for r in records if r["rules_areas"][0] == a and r["difficulty"] == d)
               for d in ("easy", "medium", "hard", "expert")]
        lines.append(f"| `{a}` | {row[0]} | {row[1]} | {row[2]} | {row[3]} | {sum(row)} |")
    lines.append("")

    lines.append("## Counts by source\n")
    lines.append("| source | n | verified |")
    lines.append("|---|---|---|")
    for name, n in sorted(_hist(records, lambda r: [r["source_name"]]).items(), key=lambda kv: (-kv[1], kv[0])):
        v = sum(1 for r in records if r["source_name"] == name and r.get("verified"))
        lines.append(f"| {name} | {n} | {v} |")
    lines.append("")

    # index grouped by rules area (primary = first listed)
    lines.append("## Index by primary rules area\n")
    by_area = {}
    for r in records:
        by_area.setdefault(r["rules_areas"][0], []).append(r)
    for area in sorted(by_area, key=lambda a: (-len(by_area[a]), a)):
        lines.append(f"### `{area}` ({len(by_area[area])})\n")
        for r in sorted(by_area[area], key=lambda r: (r["date"] or "", r["title"])):
            flag = "MV" if r["machine_verifiable"] else "--"
            vflag = "verified" if r.get("verified") else "INFERRED"
            lines.append(f"- **{r['title']}** -- {r['difficulty']}, {flag}, {r['era']}, {r['date']}, {vflag} -- "
                         f"[{r['source_name']}]({r['source_url']})")
        lines.append("")

    with open(CATALOG, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"catalog: {len(records)} puzzles -> {os.path.relpath(CATALOG, HERE)}")
    return 0


def cmd_stats(args) -> int:
    records = [json.loads(l) for l in open(args.jsonl, encoding="utf-8") if l.strip()]
    print(f"total={len(records)}")
    print("rules_areas:", json.dumps(_hist(records, lambda r: r["rules_areas"]), sort_keys=True))
    print("difficulty :", json.dumps(_hist(records, lambda r: [r["difficulty"]]), sort_keys=True))
    print("source     :", json.dumps(_hist(records, lambda r: [r["source_name"]]), sort_keys=True))
    print("verified   :", sum(1 for r in records if r.get("verified")))
    print("machine_verifiable:", sum(1 for r in records if r.get("machine_verifiable")))
    return 0


# --------------------------------------------------------------------------
# Converters: source payload -> target schema (raw/items/*.jsonl)
# --------------------------------------------------------------------------

# RulesGuru rules tag -> our canonical rules area. RulesGuru tags are already
# close to CR chapter names, so this is mostly a slug + a few merges.
RG_TAG_MAP = {
    "Triggered abilities": "triggered-abilities",
    "Static abilities": "static-abilities",
    "Activated abilities": "activated-abilities",
    "Abilities": "abilities",
    "State-based actions": "state-based-actions",
    "Continuous effects": "continuous-effects",
    "Replacement effects": "replacement",
    "Prevention effects": "replacement",
    "Layers": "layers",
    "Copy effects": "copying",
    "Type-changing effects": "type-changing",
    "Types": "type-changing",
    "Subtypes": "type-changing",
    "Control-changing effects": "control",
    "Zone-changes": "zone-changes",
    "Graveyard": "zone-changes",
    "Turn structure": "turn-structure",
    "Casting spells": "casting",
    "Resolving objects": "resolving",
    "Targets": "targets",
    "Combat": "combat",
    "Damage": "damage",
    "Tokens": "tokens",
    "Counters": "counters",
    "Costs": "costs",
    "Alternative Costs": "alternative-costs",
    "Mana": "mana",
    "Mana abilities": "mana-abilities",
    "Evergreen keywords": "keywords",
    "Non-evergreen keywords": "keywords",
    "Last known information": "last-known-information",
    "Face-down objects": "face-down",
    "Morph cards": "face-down",
    "Commander": "commander",
    "Multiplayer": "multiplayer",
    "Two-Headed Giant": "multiplayer",
    "Politics": "multiplayer",
    "Hidden information": "hidden-information",
    "Numbers and symbols": "misc",
    "Miscellaneous": "misc",
    "Life totals": "life-totals",
    "Emblems and effects": "continuous-effects",
    "Deck construction": "deck-construction",
    "Unsupported answers": "unsupported",
    "\"Can't\" effects": "cant-effects",
    "Card names": "card-names",
    "Color-changing effects": "color-changing",
    "Double-faced cards": "double-faced",
    "Drawing a card": "drawing",
    "Equipment": "equipment",
    "Lands": "lands",
    "Leaving the game": "leaving-the-game",
    "Libraries": "libraries",
    "Sagas": "sagas",
    "Special actions": "special-actions",
    "Starting the game": "starting-the-game",
    "Text-changing effects": "text-changing",
    "The stack": "stack",
    "Timing and priority": "priority",
    "Transform": "transform",
    "Turn-based actions": "turn-based-actions",
    "Vehicles": "vehicles",
    "Auras": "auras",
    "Adventurer cards": "adventurer-cards",
    "Phasing": "phasing",
    "Life": "life-totals",
    "Dependency": "dependency",
    "Discard": "discard",
    "Planeswalkers": "planeswalkers",
    "Split cards": "split-cards",
    "Reading cards": "reading-cards",
}

# Keep at most this many primary rules areas per puzzle (full tag list is kept
# verbatim under rec["rulesguru"]["tags"]).
MAX_PRIMARY_AREAS = 3

# Formats ordered most -> least restrictive; a question's format is the most
# restrictive format in which *every* referenced card is legal.
FORMAT_ORDER = ["standard", "pioneer", "modern", "legacy", "vintage", "commander"]
FORMAT_LABEL = {"standard": "Standard", "pioneer": "Pioneer", "modern": "Modern",
                "legacy": "Legacy", "vintage": "Vintage", "commander": "Commander"}


def load_sets(path):
    """set code -> release year, from RulesGuru's allSets payload (or Scryfall)."""
    years = {}
    if not os.path.exists(path):
        return years
    raw = open(path, encoding="utf-8").read()
    m = re.search(r"(\[.*\])", raw, re.S)
    if not m:
        return years
    try:
        for s in json.loads(m.group(1)):
            code, rd = s.get("code"), str(s.get("releaseDate", ""))
            mm = re.match(r"(\d{4})", rd)
            if code and mm and int(mm.group(1)) < 9000:
                years[code] = int(mm.group(1))
    except json.JSONDecodeError:
        pass
    return years


def rg_format(cards):
    legal = None
    for f in FORMAT_ORDER:
        if all((c.get("legalities") or {}).get(f) == "Legal" for c in cards):
            legal = f
            break
    return FORMAT_LABEL.get(legal, "Commander/casual") if legal else "Commander/casual"


def rg_convert(q, set_years):
    tags = q.get("tags") or []
    if "Unsupported answers" in tags:
        return None, "unsupported-answer-tag"
    cards = q.get("includedCards") or []
    if not cards:
        return None, "no-cards"

    years = []
    for c in cards:
        ys = [set_years[p] for p in (c.get("printingsCode") or []) if p in set_years]
        years.append(max(ys) if ys else None)
    newest = max([y for y in years if y])
    if newest < 2019:
        return None, f"pre-2019 (newest card {newest})"

    areas = []
    for t in tags:
        a = RG_TAG_MAP.get(t, canon_area(t))
        if a not in areas:
            areas.append(a)
    areas = [a for a in areas if a != "unsupported"][:MAX_PRIMARY_AREAS]
    if not areas:
        areas = ["misc"]

    names = [c["name"] for c in cards]
    head = " + ".join(names[:2]) + (" + %d more" % (len(names) - 2) if len(names) > 2 else "")
    title = f"{head} -- {areas[0]}"

    cited = q.get("citedRules") or {}
    # A deterministic CR outcome is (in principle) computable by a correct rules
    # engine. Questions whose answer rests on hidden information, politics, or
    # deck-construction/legality knowledge are not engine-computable.
    NON_MECHANICAL = {"Politics", "Hidden information", "Deck construction", "Reading cards"}
    mv = not (set(tags) & NON_MECHANICAL)
    note_bits = [
        "RulesGuru judge-curated database (rulesguru.org); answer states the CR citations.",
        "date = year of the newest card printing referenced by the question.",
        "verified: fetched from the RulesGuru API.",
        "machine_verifiable = the answer is a deterministic CR outcome; it was NOT independently engine-verified in this harvest.",
    ]
    if sample := q.get("_criteria"):
        note_bits.append("collected via criteria " + json.dumps(sample, sort_keys=True))

    rec = {
        "source_name": "RulesGuru",
        "source_url": f"https://rulesguru.org/?{q['id']}",
        "title": title,
        "date": str(newest),
        "era": f"{newest}+",
        "format": rg_format(cards),
        "question": q.get("questionSimple") or q.get("questionHTML", ""),
        "board_state": "Referenced cards: " + "; ".join(names),
        "answer": q.get("answerSimpleCited") or q.get("answerSimple", ""),
        "answer_authority": "judge-article",
        "rules_areas": areas,
        "difficulty": RG_LEVEL_TO_DIFF.get(str(q.get("level")), "medium"),
        "machine_verifiable": mv,
        "notes": " ".join(note_bits),
        "verified": True,
        "cards": names,
        "cited_rules": {k: (v.get("ruleText") or "")[:400] for k, v in cited.items()},
        "rulesguru": {"id": q["id"], "level": q.get("level"), "complexity": q.get("complexity"),
                      "tags": tags},
    }
    return rec, None


def cmd_convert(args) -> int:
    os.makedirs(ITEMS_DIR, exist_ok=True)
    set_years = load_sets(os.path.join(RAW_API_DIR, "sets.json"))
    written, reasons = 0, {}

    def tally(reason):
        reasons[reason] = reasons.get(reason, 0) + 1

    # -- RulesGuru ------------------------------------------------------
    rg_path = os.path.join(RAW_API_DIR, "rulesguru.jsonl")
    if args.source in ("all", "rulesguru") and os.path.exists(rg_path):
        out = []
        for line in open(rg_path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            q = json.loads(line)
            rec, why = rg_convert(q, set_years)
            if rec is None:
                tally(why)
                continue
            out.append(rec)
        with open(os.path.join(ITEMS_DIR, "rulesguru.jsonl"), "w", encoding="utf-8") as fh:
            for r in out:
                fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
        written += len(out)
        print(f"convert: rulesguru -> {len(out)} items (dropped: {json.dumps(reasons, sort_keys=True)})")

    # Hand-curated JSONL dropped into raw/api/curated_*.jsonl is copied as-is
    # (already in target schema); `normalize` validates it.
    return 0


# --------------------------------------------------------------------------
# Optional collection: RulesGuru public API
# --------------------------------------------------------------------------

RG_API = "https://rulesguru.org/api/questions/"
RG_UA = "mtg-puzzle-harvester/1.0 (research corpus; contact: repo owner)"
RG_LEVEL_TO_DIFF = {"0": "easy", "1": "medium", "2": "hard", "3": "expert", "Corner Case": "expert"}

# Criteria rotation -> biases the random draw across rules areas and depths.
RG_QUERIES = [
    {"level": ["2", "3", "Corner Case"], "legality": "modern"},
    {"level": ["2", "3", "Corner Case"], "legality": "all"},
    {"level": ["3", "Corner Case"], "legality": "modern"},
    {"level": ["2", "3"], "legality": "modern", "tags": ["Layers"], "tagsConjunc": "OR"},
    {"level": ["2", "3", "Corner Case"], "legality": "all", "tags": ["Combat"], "tagsConjunc": "OR"},
    {"level": ["2", "3"], "legality": "all", "tags": ["State-based actions"], "tagsConjunc": "OR"},
    {"level": ["2", "3"], "legality": "all", "tags": ["Replacement effects"], "tagsConjunc": "OR"},
    {"level": ["2", "3", "Corner Case"], "legality": "all", "tags": ["Triggered abilities"], "tagsConjunc": "OR"},
    {"level": ["2", "3"], "legality": "all", "tags": ["Copying"], "tagsConjunc": "OR"},
    {"level": ["2", "3"], "legality": "all", "tags": ["Tokens"], "tagsConjunc": "OR"},
    {"level": ["2", "3"], "legality": "all", "tags": ["Counters"], "tagsConjunc": "OR"},
    {"level": ["2", "3", "Corner Case"], "legality": "all", "tags": ["Priority"], "tagsConjunc": "OR"},
    {"level": ["2", "3"], "legality": "all", "tags": ["Casting spells"], "tagsConjunc": "OR"},
    {"level": ["2", "3"], "legality": "all", "tags": ["Continuous effects"], "tagsConjunc": "OR"},
    {"level": ["2", "3", "Corner Case"], "legality": "all", "tags": ["Zone-changes"], "tagsConjunc": "OR"},
]

# A breadth profile: intro/medium-level questions, for easy/medium anchors.
RG_QUERIES_BREADTH = [
    {"level": ["0", "1"], "legality": "modern"},
    {"level": ["0", "1"], "legality": "all"},
    {"level": ["1"], "legality": "modern", "tags": ["Combat"], "tagsConjunc": "OR"},
    {"level": ["1"], "legality": "all", "tags": ["Triggered abilities"], "tagsConjunc": "OR"},
    {"level": ["1"], "legality": "all", "tags": ["State-based actions"], "tagsConjunc": "OR"},
    {"level": ["1"], "legality": "all", "tags": ["Casting spells"], "tagsConjunc": "OR"},
    {"level": ["0", "1"], "legality": "all", "tags": ["Replacement effects"], "tagsConjunc": "OR"},
    {"level": ["1"], "legality": "all", "tags": ["Continuous effects"], "tagsConjunc": "OR"},
]


def rg_get(params: dict, timeout: int = 20):
    p = dict(params)
    p.setdefault("from", "homePage")
    p.setdefault("avoidRateLimiting", True)
    qs = urllib.parse.urlencode({"json": json.dumps(p)})
    req = urllib.request.Request(RG_API + "?" + qs, headers={"User-Agent": RG_UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cmd_fetch_rulesguru(args) -> int:
    os.makedirs(RAW_API_DIR, exist_ok=True)
    path = args.out or os.path.join(RAW_API_DIR, "rulesguru.jsonl")
    seen = set()
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    seen.add(json.loads(line)["id"])
                except Exception:
                    pass
    got = 0
    fh = open(path, "a", encoding="utf-8")
    i = 0
    queries = RG_QUERIES_BREADTH if getattr(args, "profile", "depth") == "breadth" else RG_QUERIES
    while got < args.n:
        crit = queries[i % len(queries)]
        i += 1
        try:
            data = rg_get(crit)
        except urllib.error.HTTPError as exc:
            print(f"[warn] HTTP {exc.code}; backing off 5s", file=sys.stderr)
            time.sleep(5)
            continue
        except Exception as exc:  # noqa: BLE001 - network flake is expected/benign
            print(f"[warn] {exc}; backing off 5s", file=sys.stderr)
            time.sleep(5)
            continue
        if not isinstance(data, list) or not data:
            time.sleep(args.delay)
            continue
        q = data[0]
        if q.get("id") not in seen:
            seen.add(q.get("id"))
            q["_criteria"] = crit
            fh.write(json.dumps(q, ensure_ascii=False) + "\n")
            fh.flush()
            got += 1
            print(f"[{got}/{args.n}] id={q.get('id')} level={q.get('level')} tags={q.get('tags')}")
        time.sleep(args.delay)
    fh.close()
    print(f"wrote {got} new raw items to {os.path.relpath(path, HERE)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("normalize", help="raw/items/*.jsonl -> puzzles.jsonl (validate + de-dupe)")
    p.add_argument("--raw-dir", default=ITEMS_DIR)
    p.add_argument("--out", default=OUT_JSONL)
    p.set_defaults(func=cmd_normalize)

    p = sub.add_parser("convert", help="raw/api/*.jsonl -> raw/items/*.jsonl (source -> target schema)")
    p.add_argument("--source", default="all", choices=["all", "rulesguru"])
    p.add_argument("--min-year", type=int, default=2019, help="drop puzzles whose newest card predates this")
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("catalog", help="puzzles.jsonl -> CATALOG.md")
    p.add_argument("--jsonl", default=OUT_JSONL)
    p.set_defaults(func=cmd_catalog)

    p = sub.add_parser("stats", help="print histograms")
    p.add_argument("--jsonl", default=OUT_JSONL)
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("fetch-rulesguru", help="collect raw items from the RulesGuru API")
    p.add_argument("--n", type=int, default=40)
    p.add_argument("--delay", type=float, default=2.2, help="seconds between requests (server asks >=2s)")
    p.add_argument("--profile", default="depth", choices=["depth", "breadth"])
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_fetch_rulesguru)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
