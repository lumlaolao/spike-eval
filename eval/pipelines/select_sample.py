#!/usr/bin/env python3
"""Category-weighted RANDOM rollout sample from a mined candidates file.

Extracted from the overnight-2 inline heredoc (the round whose random
sampling beat 3-4-band targeting ~2x on usable keys). The heredoc hardcoded
its shuffle seed, which made the selection step unreproducible from the
artifacts alone — here the seed is a required CLI arg and is WRITTEN INTO the
sample manifest, so a round can be rebuilt byte-for-byte.

Weights (the overnight-2 measured mix): save-vs-lethal 220 / mulligan 40 /
graded-midgame 40. Override per category with --cap CATEGORY=N.

Stdlib-only, Python 3.9 (repo rule).

Usage:
    python3 eval/pipelines/select_sample.py CANDIDATES.jsonl \
        --out SAMPLE.jsonl --seed 20260731 [--cap save-vs-lethal=220] ...
"""

import argparse
import json
import random
import sys
from pathlib import Path

DEFAULT_CAPS = {
    "save-vs-lethal": 220,
    "mulligan": 40,
    "graded-midgame": 40,
}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Category-weighted random rollout sample (reproducible).")
    ap.add_argument("candidates", help="mined candidates jsonl")
    ap.add_argument("--out", required=True, help="sample jsonl path")
    ap.add_argument("--seed", type=int, required=True,
                    help="shuffle seed — recorded in the manifest; a sample "
                         "without its seed cannot be rebuilt")
    ap.add_argument("--cap", action="append", default=[],
                    metavar="CATEGORY=N",
                    help="override a category cap (repeatable); default %s"
                         % json.dumps(DEFAULT_CAPS, sort_keys=True))
    args = ap.parse_args(argv)

    caps = dict(DEFAULT_CAPS)
    for spec in args.cap:
        try:
            k, v = spec.split("=", 1)
            caps[k] = int(v)
        except ValueError:
            ap.error("--cap wants CATEGORY=N, got %r" % spec)

    rows = []
    with open(args.candidates, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if not rows:
        print("no candidate rows in %s" % args.candidates, file=sys.stderr)
        return 2

    by = {}
    for r in rows:
        by.setdefault(r.get("category"), []).append(r)
    for k in by:
        # Per-category derived seed (scheme v2): a shared Random(args.seed)
        # per category made equal-sized categories draw the IDENTICAL
        # permutation stream (correlated, not independent). Str seeding is
        # deterministic across processes (unlike hash()). NOTE: this scheme
        # does NOT reproduce the overnight-2 heredoc's selection; the
        # manifest records the scheme so artifacts are attributable.
        random.Random("%d|%s" % (args.seed, k)).shuffle(by[k])

    sel = []
    taken = {}
    for cat, cap in sorted(caps.items()):
        got = by.get(cat, [])[:cap]
        taken[cat] = {"cap": cap, "available": len(by.get(cat, [])),
                      "taken": len(got)}
        sel.extend(got)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(str(out), "w", encoding="utf-8") as f:
        for r in sel:
            f.write(json.dumps(r, sort_keys=True) + "\n")

    manifest = {
        "schema": "rollout-sample-manifest.v1",
        "source": str(args.candidates),
        "seed": args.seed,
        "shuffle_scheme": "per-category-v2",
        "caps": caps,
        "taken": taken,
        "n_selected": len(sel),
        "categories_in_source": sorted(str(k) for k in by),
    }
    mpath = out.with_suffix(out.suffix + ".meta.json")
    with open(str(mpath), "w", encoding="utf-8") as f:
        f.write(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    print("rollout sample: %d rows -> %s (manifest %s, seed %d)"
          % (len(sel), out, mpath, args.seed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
