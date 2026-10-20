#!/usr/bin/env python3
"""Lookups and model time per condition of a puzzle run: what puzzle_score.py does not report.

    python3 eval/puzzle_tool_use.py runs/puzzles/bench-v1-e67        # a run dir holding off/ and on/

For each condition: attempts solved, the lookups the seat made by tool name (`answer` and `set_plan` are
decisions, not lookups), how many attempts called `rules_lookup`, and the median and total model time per
call; then the puzzles whose solve counts differ between the conditions. It reads the game JSONL
(`context_tool` and `context_call` rows, which runs/ keeps and git does not) and the result rows.

Exit 2 when a condition is missing or any attempt has no game log (its lookups cannot be counted).
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
    """{solved, attempts, games, per_puzzle {id: (c, k)}, lookups Counter, rules_attempts, call_secs []} for one
    condition; `games` is the game logs found, which the completeness check compares with the attempts."""
    out = {"solved": 0, "attempts": 0, "per_puzzle": {}, "lookups": collections.Counter(), "rules_attempts": 0,
           "call_secs": [], "games": 0}
    for d in sorted(glob.glob(os.path.join(dirpath, "*"))):
        f = os.path.join(d, "puzzle-results.jsonl")
        rows = [json.loads(l) for l in open(f) if l.strip()] if os.path.exists(f) else []
        c = sum(bool(r.get("solved")) for r in rows)
        out["per_puzzle"][os.path.basename(d)] = (c, len(rows))
        out["solved"] += c
        out["attempts"] += len(rows)
        for g in sorted(glob.glob(d + "/game-*.jsonl")):
            out["games"] += 1
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
    """The text, and whether it can be trusted: every attempt of both conditions has its game log."""
    if not all(os.path.isdir(os.path.join(root, c)) for c in CONDITIONS):
        return "no %s/ beside each other in %s" % ("/ and ".join(CONDITIONS), root), False
    by = {c: condition(os.path.join(root, c)) for c in CONDITIONS}
    complete = all(by[c]["games"] >= by[c]["attempts"] > 0 for c in CONDITIONS)
    lines = []
    for c in CONDITIONS:
        x = by[c]
        lines.append("== %s: %d/%d attempts solved (%.0f%%), %d puzzles" % (
            c, x["solved"], x["attempts"], 100.0 * x["solved"] / max(x["attempts"], 1), len(x["per_puzzle"])))
        if not x["attempts"]:
            lines.append("   no attempts in %s/" % c)
            continue
        if x["games"] < x["attempts"]:
            # Without the logs a lookup count of zero would read as "none made" (pr-review of PR #272).
            lines.append("   no game JSONL for %d of %d attempts: lookups not counted" % (
                x["attempts"] - x["games"], x["attempts"]))
            continue
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
    return "\n".join(lines), complete


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="a run dir holding off/ and on/")
    text, complete = report(ap.parse_args(argv).root)
    print(text)
    return 0 if complete else 2


if __name__ == "__main__":
    sys.exit(main())
