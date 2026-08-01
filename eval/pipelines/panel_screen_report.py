#!/usr/bin/env python3
"""Validate the cheap-panel pre-screen hypothesis.

Hypothesis: positions that EVERY model in a cheap 3-model panel solves are
"free" for frontier models too (they rank nobody), so a mining round can skip
confirming them and spend its rollout budget on positions the panel splits on.

Validation design (two populations, one panel):
  suite side  the panel runs on the frozen suite, where frontier ground truth
              exists (tournament-8 per-position solve rates). Measures the
              PRECISION of "panel-free -> frontier-free" and what fraction of
              informative positions a panel screen would wrongly discard.
  pool side   the panel runs on the verified non-suite keys (the population a
              mining screen would actually see). Reports the distribution —
              how much rollout budget a screen at each threshold would save.

Usage:
    python3 eval/pipelines/panel_screen_report.py runs/puz/panel-screen \
        [--tournament runs/puz/tournament-8]

Stdlib-only, Python 3.9.
"""

import argparse
import json
import sys
from pathlib import Path


def per_position_solved(run_dir):
    """position_id -> solved (k=1 binary) for one run dir; None if unusable."""
    rp = Path(run_dir) / "results.jsonl"
    if not rp.is_file():
        return None
    out = {}
    for line in rp.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("t") == "summary" or not r.get("position_id"):
            continue
        if r.get("k") == 1:
            out[r["position_id"]] = 1 if r.get("solved") else 0
    return out or None


def collect(parent, prefix):
    """-> {model_tag: {pid: solved}} over run dirs named <prefix>-*."""
    panel = {}
    for d in sorted(Path(parent).glob(prefix + "-*")):
        got = per_position_solved(d)
        if got:
            panel[d.name[len(prefix) + 1:]] = got
    return panel


def panel_counts(panel):
    """pid -> (n_solved, n_models) over positions every panel model answered."""
    models = list(panel)
    if not models:
        return {}
    shared = set.intersection(*(set(panel[m]) for m in models))
    return dict((pid, (sum(panel[m][pid] for m in models), len(models)))
                for pid in shared)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("panel_dir", help="dir holding pool-*/ and suite-*/ runs")
    ap.add_argument("--tournament", default="runs/puz/tournament-8",
                    help="frontier ground-truth run dirs (suite side)")
    args = ap.parse_args(argv)

    suite_panel = collect(args.panel_dir, "suite")
    pool_panel = collect(args.panel_dir, "pool")
    print("panel models (suite side): %s" % ", ".join(sorted(suite_panel)))

    # frontier ground truth on the suite
    frontier = {}
    for d in sorted(Path(args.tournament).iterdir()):
        if not d.is_dir() or "SUPERSEDED" in d.name:
            continue
        got = per_position_solved(d)
        if got:
            frontier[d.name] = got
    fshared = (set.intersection(*(set(v) for v in frontier.values()))
               if frontier else set())

    sc = panel_counts(suite_panel)
    rows = []
    for pid in sorted(sc):
        if pid not in fshared:
            continue
        n_solved, n_models = sc[pid]
        frates = [frontier[m][pid] for m in frontier]
        f_free = all(v == 1 for v in frates)
        f_informative = 0 < sum(frates) < len(frates)
        rows.append((pid, n_solved, n_models, f_free, f_informative,
                     sum(frates) / float(len(frates))))

    print()
    print("== SUITE SIDE: panel vs frontier ground truth (%d positions) =="
          % len(rows))
    print("   pid              panel  frontier_solve  frontier_free")
    for pid, ns, nm, ffree, finf, frate in rows:
        print("   %-16s %d/%d    %.2f            %s%s"
              % (pid[:16], ns, nm, frate, "yes" if ffree else "no",
                 "   <- informative" if finf else ""))

    print()
    print("== screen-rule table (suite side) ==")
    for thresh_desc, pred in (
            ("panel all-solve (3/3)", lambda ns, nm: ns == nm),
            ("panel majority (>=2/3)", lambda ns, nm: ns >= nm - 1)):
        predicted_free = [r for r in rows if pred(r[1], r[2])]
        if not predicted_free:
            print("  %-24s predicts 0 free positions" % thresh_desc)
            continue
        tp = sum(1 for r in predicted_free if r[3])
        wrongly_discarded = sum(1 for r in predicted_free if r[4])
        print("  %-24s predicts %2d free: precision %d/%d = %.2f; "
              "would wrongly discard %d informative"
              % (thresh_desc, len(predicted_free), tp, len(predicted_free),
                 tp / float(len(predicted_free)), wrongly_discarded))

    pc = panel_counts(pool_panel)
    print()
    print("== POOL SIDE: %d non-suite verified keys ==" % len(pc))
    from collections import Counter
    dist = Counter(ns for ns, _ in pc.values())
    for ns in sorted(dist):
        print("   panel %d/3 solve: %3d positions" % (ns, dist[ns]))
    n_all = sum(1 for ns, nm in pc.values() if ns == nm)
    print("   -> a 3/3-solve screen would skip %d/%d (%.0f%%) of confirm "
          "budget on this population" % (n_all, len(pc),
                                         100.0 * n_all / len(pc) if pc else 0))
    print()
    print("CAVEAT: suite-side n is small; treat precision as an estimate "
          "with a wide interval, not a law. The rule ships only if precision "
          "is 1.00 AND wrongly-discarded informative = 0 at the chosen "
          "threshold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
