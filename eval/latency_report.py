#!/usr/bin/env python3
"""Per-decision latency report over one or more run directories. Stdlib only.

Reads the `game-*.jsonl` written by an `EVAL_OUT` run and prints the numbers that
actually answer "why is a game slow":

  - model calls by tier (smart / fast / auto / plan) and per turn
  - completion tokens, median and MAX (the max is the tail that eats the clock)
  - decisions whose bridge retried (attempts > 1) and the retry time they cost
  - completion tokens by decision TYPE, so the expensive type is obvious

Usage:
  python3 eval/latency_report.py runs/ab/match-on runs/ab/match-final ...
  python3 eval/latency_report.py --compact runs/ab/*

Read it with the caveat it prints: at one seed per arm the THROUGHPUT column is
noise. The mechanism columns (tiers, completion distribution, retries) are the
signal -- they are per-decision, and a game has dozens of them.
"""
import argparse
import glob
import json
import os
import sys
from collections import Counter, defaultdict

from aggregate import unscorable


def load(run_dir):
    """([game_rows], [decision_rows]) across EVERY game in run_dir.

    An arm is a directory of games, not one game: a per-arm report that read only the
    first file would report one game's noise as the arm's behaviour."""
    files = sorted(glob.glob(os.path.join(run_dir, "game-*.jsonl")))
    games, dec = [], []
    for f in files:
        for line in open(f, encoding="utf-8", errors="replace"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            t = r.get("t")
            if t == "game":
                games.append(dict(r, _file=os.path.basename(f)))
            elif t == "decision":
                dec.append(r)
    return games, dec


def pct(vals, p):
    if not vals:
        return None
    vals = sorted(vals)
    i = min(len(vals) - 1, int(len(vals) * p))
    return vals[i]


def report(tag, run_dir, compact=False):
    games, dec = load(run_dir)
    if not dec:
        print("%-28s (no decision rows)" % tag)
        return
    tiers = Counter(d.get("tier") for d in dec)
    ct = [d.get("completion_tokens") for d in dec if isinstance(d.get("completion_tokens"), int)]
    retried = [d for d in dec if (d.get("attempts") or 1) > 1]
    retry_ms = sum((d.get("retry_ms") or 0) for d in dec)
    n_games = len(games)
    turns = sum((g.get("turns") or 0) for g in games) or None
    capped = sum(1 for g in games if g.get("capped"))
    # Latency is per decision and a dead bridge's retries are the thing worth seeing, so its decisions stay in; the
    # game is named unscorable and its winner is not shown (aggregate.unscorable, #430).
    lost = sum(1 for g in games if unscorable(g, run_dir))
    if n_games > 1:
        winner = "%d games" % n_games
    elif games:
        winner = "invalid" if lost else games[0].get("winner")
    else:
        winner = None
    calls = tiers.get("smart", 0) + tiers.get("fast", 0) + tiers.get("plan", 0)

    line = ("%-10s games=%-2d capped=%-2d winner=%-8s turns=%-5s decisions=%-4d smart=%-3d fast=%-3d plan=%-3d auto=%-3d" % (
        tag, n_games, capped, winner, turns, len(dec), tiers.get("smart", 0), tiers.get("fast", 0),
        tiers.get("plan", 0), tiers.get("auto", 0)))
    if lost:
        line += "  unscorable=%d" % lost
    if ct:
        line += "  comp med=%s max=%s" % (pct(ct, 0.5), max(ct))
    if retried:
        line += "  retried=%d (+%.0fs)" % (len(retried), retry_ms / 1000.0)
    print(line)
    if calls and turns:
        print("%-10s   %d model calls over %d turns, %.1f per turn" % ("", calls, turns, calls / float(turns)))
    if not compact:
        by_type = defaultdict(list)
        for d in dec:
            if isinstance(d.get("completion_tokens"), int):
                by_type[d.get("type")].append(d["completion_tokens"])
        for k in sorted(by_type, key=lambda k: -len(by_type[k]))[:5]:
            v = by_type[k]
            print("      %-18s n=%-4d median=%-6s max=%d" % (k, len(v), pct(v, 0.5), max(v)))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="+", help="run directories (each with one game-*.jsonl)")
    ap.add_argument("--compact", action="store_true", help="one line per run; skip the by-type table")
    args = ap.parse_args()
    for d in args.dirs:
        report(os.path.basename(d.rstrip("/")), d, args.compact)
    print("\nNOTE: at one seed per arm the turns/winner columns are noise. The tier split, the "
          "completion distribution (especially MAX) and the retry counts are per-decision and are "
          "the signal. Use --pairs and several seeds for a throughput claim.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
