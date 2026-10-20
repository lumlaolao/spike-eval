#!/usr/bin/env python3
"""Lookups and model time per condition of a puzzle run: what puzzle_score.py does not report.

    python3 eval/puzzle_tool_use.py runs/puzzles/bench-v1-e67        # a run dir holding off/ and on/

For each condition: attempts solved, the lookups the seat made by tool name (`answer` and `set_plan` are
decisions, not lookups), how many attempts called `rules_lookup`, and the median and total model time per
call; then the puzzles whose solve counts differ between the conditions. It reads the game JSONL
(`context_tool` and `context_call` rows, which runs/ keeps and git does not) and the result rows.

What it cannot say: whether a lookup helped, and it counts a solve as the result row's `solved` (a capped or
deck-out solve is not filtered; `puzzle_score.py` scores those). Stdlib only.
"""
import argparse
import collections
import glob
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tool_audit import ANSWER_TOOLS, tool_name  # noqa: E402

CONDITIONS = ("off", "on")


def condition(dirpath):
    """{solved, attempts, per_puzzle {id: (c, k)}, lookups Counter, rules_attempts, call_secs []} for one condition."""
    out = {"solved": 0, "attempts": 0, "per_puzzle": {}, "lookups": collections.Counter(), "rules_attempts": 0,
           "call_secs": []}
    for d in sorted(glob.glob(os.path.join(dirpath, "*"))):
        rows = [json.loads(l) for f in glob.glob(d + "/puzzle-results.jsonl") for l in open(f) if l.strip()]
        c = sum(bool(r.get("solved")) for r in rows)
        out["per_puzzle"][os.path.basename(d)] = (c, len(rows))
        out["solved"] += c
        out["attempts"] += len(rows)
        for g in sorted(glob.glob(d + "/game-*.jsonl")):
            used = False
            for l in open(g):
                r = json.loads(l)
                if r.get("t") == "context_tool":
                    name = tool_name(r.get("request"))
                    if name not in ANSWER_TOOLS:
                        out["lookups"][name] += 1
                        used |= name == "rules_lookup"
                elif r.get("t") == "context_call":
                    out["call_secs"].append(r.get("elapsed_ms", 0) / 1000.0)
            out["rules_attempts"] += used
    return out


def report(root):
    by = {c: condition(os.path.join(root, c)) for c in CONDITIONS}
    lines = []
    for c in CONDITIONS:
        x = by[c]
        lines.append("== %s: %d/%d attempts solved (%.0f%%), %d puzzles" % (
            c, x["solved"], x["attempts"], 100.0 * x["solved"] / max(x["attempts"], 1), len(x["per_puzzle"])))
        lines.append("   lookups: %s | attempts that called rules_lookup: %d" % (
            dict(x["lookups"].most_common()), x["rules_attempts"]))
        if x["call_secs"]:
            lines.append("   model time per call: median %.1fs, total %.0f min" % (
                statistics.median(x["call_secs"]), sum(x["call_secs"]) / 60))
    off, on = by["off"]["per_puzzle"], by["on"]["per_puzzle"]
    lines.append("== puzzles where the conditions differ (off c/k -> on c/k):")
    for p in sorted(off):
        if on.get(p) and off[p][0] != on[p][0]:
            lines.append("   %-14s %d/%d -> %d/%d" % (p, off[p][0], off[p][1], on[p][0], on[p][1]))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="a run dir holding off/ and on/")
    print(report(ap.parse_args(argv).root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
