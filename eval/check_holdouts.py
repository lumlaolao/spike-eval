#!/usr/bin/env python3
"""Deck/matchup holdout audit for training corpora.

Scans datasets/*/meta.jsonl and reports which eval decks (declared in
eval/holdouts.json) appear in which datasets and splits. Decks with
status "held-out" must not appear in any dataset except those explicitly
listed in their "grandfathered" field — existing contamination is frozen
and labeled, new leaks fail the audit.

This is the deck-side half of contamination control; the seed-side half
(eval-only seed bank + seed overlap check) is a separate artifact.

Usage:
    python3 eval/check_holdouts.py            # human-readable report
    python3 eval/check_holdouts.py --json     # machine-readable

Exit 0 = clean, 1 = held-out violation found, 2 = config/usage error.
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REGISTRY = Path(__file__).resolve().parent / "holdouts.json"
DATASETS = REPO / "datasets"


def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return s


def parse_matchup(subdir, slugs):
    """Parse 'a__vs__b' or 'a-vs-b' into (a, b) using known deck slugs.

    Deck slugs contain dashes, so split by trying known slugs longest-first.
    Returns None when the matchup is not derivable (e.g. flat source dirs).
    """
    if not subdir:
        return None
    norm = subdir.replace("__vs__", "-vs-")
    for a in sorted(slugs, key=len, reverse=True):
        prefix = a + "-vs-"
        if norm.startswith(prefix):
            b = norm[len(prefix):]
            if b in slugs:
                return (a, b)
    return None


def main():
    as_json = "--json" in sys.argv[1:]

    try:
        registry = json.loads(REGISTRY.read_text())
    except Exception as e:
        print(f"error: cannot load {REGISTRY}: {e}", file=sys.stderr)
        return 2
    decks = registry["decks"]
    slugs = set(decks)

    # registry sanity: every declared deck file must exist
    for slug, d in decks.items():
        if not (REPO / d["file"]).is_file():
            print(f"error: registry deck {slug!r} file missing: {d['file']}",
                  file=sys.stderr)
            return 2

    # deck -> {dataset -> {split -> rows}}; matchup -> {dataset -> games}
    deck_hits = {s: {} for s in slugs}
    matchup_hits = {}
    unknown_decks = {}
    no_meta = []

    for ds in sorted(DATASETS.iterdir()):
        meta = ds / "meta.jsonl"
        if not ds.is_dir():
            continue
        if not meta.is_file():
            no_meta.append(ds.name)
            continue
        with meta.open() as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                raw = row.get("deck") or row.get("seat")
                if raw:
                    slug = slugify(raw)
                    if slug in slugs:
                        per = deck_hits[slug].setdefault(ds.name, {})
                        split = row.get("split", "?")
                        per[split] = per.get(split, 0) + 1
                    else:
                        unknown_decks.setdefault(str(raw), set()).add(ds.name)
                mu = parse_matchup(row.get("source_subdir"), slugs)
                if mu:
                    key = tuple(sorted(mu))
                    matchup_hits.setdefault(key, {}).setdefault(ds.name, set()).add(
                        row.get("source_file", "?"))

    # held-out gate: fail on any hit outside the grandfathered list
    violations = []
    grandfathered_notes = []
    for slug, d in decks.items():
        if d.get("status") != "held-out":
            continue
        ok = set(d.get("grandfathered", []))
        for ds_name in deck_hits[slug]:
            if ds_name in ok:
                grandfathered_notes.append((slug, ds_name))
            else:
                violations.append((slug, ds_name))

    report = {
        "decks": {
            s: {
                "status": decks[s]["status"],
                "datasets": {k: v for k, v in sorted(deck_hits[s].items())},
            }
            for s in sorted(slugs)
        },
        "matchups": {
            f"{a} vs {b}": {k: len(v) for k, v in sorted(ds.items())}
            for (a, b), ds in sorted(matchup_hits.items())
        },
        "datasets_without_meta": no_meta,
        "unknown_deck_values": {k: sorted(v) for k, v in unknown_decks.items()},
        "grandfathered_hits": grandfathered_notes,
        "violations": violations,
    }

    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print("== deck presence by dataset (rows per split) ==")
        for s in sorted(slugs):
            info = report["decks"][s]
            tag = "HELD-OUT" if info["status"] == "held-out" else "in-corpus"
            if info["datasets"]:
                where = ", ".join(
                    f"{k}[{','.join(f'{sp}:{n}' for sp, n in sorted(v.items()))}]"
                    for k, v in info["datasets"].items())
            else:
                where = "not found in any dataset"
            print(f"  {s:20s} {tag:9s} {where}")
        print()
        print(f"== matchups seen in corpora ({len(report['matchups'])} derivable) ==")
        for mu, ds in report["matchups"].items():
            games = ", ".join(f"{k}:{v} games" for k, v in ds.items())
            print(f"  {mu:40s} {games}")
        print("  (matchups are only derivable from source_subdir naming;")
        print("   flat-dir sources like stage0 contribute deck-level info only)")
        print()
        if no_meta:
            print(f"WARNING: {len(no_meta)} dataset dirs have no meta.jsonl "
                  f"(invisible to this audit): {', '.join(no_meta)}")
        if unknown_decks:
            print(f"WARNING: unrecognized deck/seat values in meta.jsonl: "
                  f"{', '.join(sorted(unknown_decks))}")
        for slug, ds_name in grandfathered_notes:
            print(f"NOTE: held-out deck {slug!r} found in grandfathered "
                  f"dataset {ds_name!r} (frozen pre-declaration contamination)")
        if violations:
            print()
            for slug, ds_name in violations:
                print(f"VIOLATION: held-out deck {slug!r} appears in "
                      f"non-grandfathered dataset {ds_name!r}")
        print()
        print("RESULT:", "FAIL" if violations else "CLEAN")

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
