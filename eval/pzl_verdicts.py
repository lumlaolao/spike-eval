#!/usr/bin/env python3
"""Fold reviewer verdicts back into the PZL suite, and re-derive the mechanic
counts against LOAD-BEARING tags instead of raw card presence.

WHY. `pzl_corpus.mechanic_index` tags a mechanic whenever a card carrying it
sits in ANY zone of the puzzle. That is the only thing a static reader can do,
and it over-counts in two distinct ways that this module separates:

  1. NOT A MECHANIC AT ALL. Forge's effect vocabulary includes engine
     bookkeeping -- `eff:Cleanup` (clear remembered objects), `eff:Effect`
     (create a hidden effect holder), `eff:Mana` (a basic land's mana
     ability), `eff:ChangeZone`, `eff:Draw`. Every deck does these every
     turn. Counting them as "mechanics the benchmark covers" inflates the
     denominator with things no model could fail to encounter. They are
     dropped, not merely down-weighted.

  2. PRESENT BUT INCIDENTAL. A real mechanic (Flying, Scry) on a card that
     is stuck in the library, or on a body irrelevant to the goal. Reviewers
     judge this per puzzle; only load-bearing tags survive.

The result is a smaller and more defensible coverage number than raw
presence, plus a per-mechanic quota count that means "N puzzles where
understanding this mechanic actually matters".

    python3 eval/pzl_verdicts.py --verdicts DIR --suite eval/suites/pzl-v1
"""
import argparse
import collections
import glob
import json
import os
import re
import sys

# Forge effect primitives that are engine plumbing rather than a mechanic a
# player must understand. Deliberately conservative: only tokens that are
# either pure bookkeeping or so universal that "covering" them is vacuous.
# Reviewers independently flagged the first several as their most-common
# incidental tags, which is the evidence for this list, not taste.
NON_MECHANIC_EFFECTS = frozenset({
    "eff:Cleanup", "eff:Effect", "eff:Mana", "eff:ManaReflected",
    "eff:ChangeZone", "eff:ChangeZoneAll", "eff:Draw", "eff:Tap",
    "eff:Untap", "eff:Pump", "eff:PumpAll", "eff:GainLife", "eff:LoseLife",
    "eff:DealDamage", "eff:Destroy", "eff:DestroyAll", "eff:Token",
    "eff:PutCounter", "eff:RemoveCounter", "eff:Sacrifice", "eff:Discard",
    "eff:Play", "eff:Counter", "eff:Charm", "eff:Branch", "eff:Repeat",
    "eff:StoreSVar", "eff:Debuff", "eff:Animate", "eff:SetState",
})


def load_verdicts(dirpath):
    """-> ({puzzle id: verdict}, files, {file: [verdicts]}).

    The per-file grouping exists because the reviewers are NOT interchangeable
    and pooling them blind would be wrong. Each batch is one reviewer, and
    they demonstrably applied different thresholds: batch 1 marked its
    most-common tag incidental on 60 of 75 puzzles, batch 3 on 13 of 75, and
    batch 3 said outright that it defaulted active-zone mechanics to
    load-bearing rather than solving each line. `strictness` below turns that
    into a number instead of a footnote.
    """
    # STRICT name match, not result*.json. Reviewers write intermediate
    # scratch files next to their final output (result4_part1.json,
    # result_0.json ...); a loose glob swept those in and silently double
    # counted, reporting 199 judged out of 300 from partial work. Only
    # `result<N>.json` is a finished batch.
    out, by_file = {}, collections.OrderedDict()
    files = sorted(p for p in glob.glob(os.path.join(dirpath, "result*.json"))
                   if re.match(r"^result\d+\.json$", os.path.basename(p)))
    if not files:
        raise SystemExit("no result<N>.json in %s" % dirpath)
    for path in files:
        with open(path, "r", encoding="utf-8") as fh:
            try:
                rows = json.load(fh)
            except ValueError as exc:
                raise SystemExit("%s: %s" % (path, exc))
        if not isinstance(rows, list):
            raise SystemExit("%s: expected a JSON array" % path)
        keep = [r for r in rows if isinstance(r, dict) and r.get("id")]
        by_file[os.path.basename(path)] = keep
        for row in keep:
            out[row["id"]] = row
    return out, files, by_file


def strictness(verdict_rows, tagged_by_id):
    """-> (mean fraction of a puzzle's real mechanics judged INCIDENTAL, n).

    A reviewer that keeps everything scores ~0.0; one that prunes hard scores
    high. Comparing this across batches says whether their numbers may be
    pooled at all.
    """
    fracs = []
    for r in verdict_rows:
        tagged = [t for t in tagged_by_id.get(r["id"], []) if is_mechanic(t)]
        if not tagged:
            continue
        lb = {t for t in (r.get("load_bearing") or []) if is_mechanic(t)}
        fracs.append(1.0 - (len(lb & set(tagged)) / float(len(tagged))))
    return (sum(fracs) / len(fracs) if fracs else 0.0), len(fracs)


def is_mechanic(tag):
    """kw:* always counts; eff:* only if it is not engine plumbing."""
    return tag.startswith("kw:") or (
        tag.startswith("eff:") and tag not in NON_MECHANIC_EFFECTS)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--verdicts", required=True, help="dir of result*.json")
    ap.add_argument("--suite", default="eval/suites/pzl-v1",
                    help="suite basename (reads .jsonl, rewrites both)")
    ap.add_argument("--quota", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    verdicts, files, by_file = load_verdicts(args.verdicts)
    jsonl = args.suite + ".jsonl"
    rows = [json.loads(l) for l in open(jsonl, encoding="utf-8")]
    print("verdict files : %d  (%d puzzles judged)" % (len(files), len(verdicts)))
    print("suite rows    : %d" % len(rows))

    missing = [r["id"] for r in rows if r["id"] not in verdicts]
    if missing:
        print("UNJUDGED      : %d rows -- %s"
              % (len(missing), ", ".join(missing[:5])))

    qual = collections.Counter()
    raw_cnt, lb_cnt = collections.Counter(), collections.Counter()
    for r in rows:
        v = verdicts.get(r["id"]) or {}
        qual[v.get("quality", "unjudged")] += 1
        raw = [t for t in r.get("tagged_mechanics", []) if is_mechanic(t)]
        lb = [t for t in (v.get("load_bearing") or []) if is_mechanic(t)]
        r["load_bearing_mechanics"] = sorted(lb)
        r["review_quality"] = v.get("quality", "unjudged")
        if v.get("note"):
            r["review_note"] = v["note"]
        for t in raw:
            raw_cnt[t] += 1
        for t in lb:
            lb_cnt[t] += 1

    print("\nreview quality: %s"
          % ", ".join("%s=%d" % kv for kv in sorted(qual.items())))

    # Inter-reviewer strictness. If these diverge widely the pooled
    # load-bearing count is a blend of incompatible thresholds, and the
    # honest read is the per-reviewer spread, not the mean.
    tagged_by_id = {r["id"]: r.get("tagged_mechanics", []) for r in rows}
    print("\n%-16s %-8s %s" % ("reviewer", "judged", "frac tags called incidental"))
    strict = {}
    for fname, vrows in by_file.items():
        frac, n = strictness(vrows, tagged_by_id)
        strict[fname] = frac
        print("  %-14s %-8d %.2f" % (fname, n, frac))
    if strict:
        lo, hi = min(strict.values()), max(strict.values())
        print("  spread: %.2f  %s" % (hi - lo,
              "OK to pool" if hi - lo < 0.25 else
              "TOO WIDE to pool blind -- reviewers used different thresholds"))
    print("\n%-34s %8s %8s" % ("", "raw", "load-bearing"))
    print("%-34s %8d %8d" % ("distinct mechanics present",
                             len(raw_cnt), len(lb_cnt)))
    print("%-34s %8d %8d"
          % ("at quota %d" % args.quota,
             sum(1 for m in raw_cnt if raw_cnt[m] >= args.quota),
             sum(1 for m in lb_cnt if lb_cnt[m] >= args.quota)))

    dropped = sorted({t for r in rows
                      for t in r.get("tagged_mechanics", [])
                      if not is_mechanic(t)})
    print("\ndropped as engine plumbing (%d): %s"
          % (len(dropped), ", ".join(d[4:] for d in dropped[:12])))

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    with open(jsonl, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
    import hashlib
    with open(jsonl, "rb") as fh:
        sha = hashlib.sha256(fh.read()).hexdigest()

    man_path = args.suite + ".json"
    with open(man_path, encoding="utf-8") as fh:
        man = json.load(fh)
    man["sha256"] = sha
    mc = man.setdefault("mechanic_coverage", {})
    mc["review"] = collections.OrderedDict([
        ("verdict_files", len(files)),
        ("n_judged", sum(1 for r in rows if r["review_quality"] != "unjudged")),
        ("quality", dict(qual)),
        ("n_load_bearing_mechanics", len(lb_cnt)),
        ("n_load_bearing_at_quota",
         sum(1 for m in lb_cnt if lb_cnt[m] >= args.quota)),
        ("n_dropped_as_engine_plumbing", len(dropped)),
        ("reviewer_strictness", {k: round(v, 3) for k, v in strict.items()}),
        ("strictness_spread", round(max(strict.values()) - min(strict.values()), 3)
         if strict else 0),
        ("note", "load-bearing counts come from per-puzzle reviewer judgement "
                 "and are the honest figure; the raw counts above measure only "
                 "that a card carrying the mechanic was somewhere on the board. "
                 "Engine-plumbing effect tokens (Cleanup/Effect/Mana/...) are "
                 "excluded from BOTH -- they are not mechanics."),
    ])
    with open(man_path, "w", encoding="utf-8") as fh:
        json.dump(man, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print("\nrewrote %s (sha256=%s)" % (jsonl, sha[:16]))
    print("updated %s" % man_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
