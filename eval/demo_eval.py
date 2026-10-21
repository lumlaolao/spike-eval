#!/usr/bin/env python3
"""The demo's eval page: what the benchmark measures and what the gate proves, from files already on disk.

  python3 eval/demo_eval.py --acceptance /tmp/acc-<sha>            # print the report the page shows (JSON)
  python3 eval/demo_server.py --game runs/loop/g33 --acceptance /tmp/acc-<sha>   # then open /eval.html

Two blocks, read from a scorer or a summary line; the one typed-in number is the per-game cost range from CLAUDE.md:
  1. The acceptance gate: every stage of one harness_acceptance.sh run (its OUT dir's summary.txt), with the two
     zero-model canaries checked against their pins (the canary stages fail when a pin moves).
  2. The headline scores (CLAUDE.md, "The benchmark"), each next to its floors. PUZ is computed here by
     puzzle_score.score from the recorded benchmark-v1 results; STR-AI and STR-HELDOUT are shown as not yet run,
     with the panel's size and the cost of running it (GAME_COST_USD), until a panel root is given.
Stdlib only.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import panel_score  # noqa: E402
import puzzle_format as pf  # noqa: E402
import puzzle_score as ps  # noqa: E402

REPO = os.path.dirname(HERE)
STAGE = re.compile(r"^(PASS|FAIL|SKIP)\s+([\w-]+):\s*(.*)$")
PUZ_SUITE = os.path.join(HERE, "suites", "puzzles-rules-sample-v1.json")
PUZ_DIR = os.path.join(HERE, "puzzles", "rules-pilot", "benchmark-v1")
# The recorded PUZ runs (eval/puzzles/rules-pilot/benchmark-v1/REPORT.md): Luna, k=5, lookup tools off and on.
PUZ_RUNS = (("lookups off, epoch 66", "results/off"), ("lookups on, epoch 66", "results/on"),
            ("lookups off, epoch 67", "results-e67/off"), ("lookups on, epoch 67", "results-e67/on"))
PANELS = os.path.join(HERE, "suites", "panels-v1.json")
GAME_COST_USD = (0.02, 0.05)   # one one-tier Luna game (CLAUDE.md, "Cost of paid runs")


def acceptance(out_dir):
    """The stages of one acceptance run, in order: [{stage, status, detail}], from OUT/summary.txt."""
    path = os.path.join(out_dir, "summary.txt")
    stages = []
    with open(path) as f:
        for line in f:
            m = STAGE.match(line.strip())
            if m:
                detail = m.group(3)
                if m.group(2) == "preflight":  # the JVM's proxy options are environment, not evidence
                    detail = re.sub(r"Picked up JAVA_TOOL_OPTIONS:.*?, (decks)", r"\1", detail)
                stages.append({"stage": m.group(2), "status": m.group(1), "detail": detail})
    return {"out": out_dir, "stages": stages, "passed": sum(s["status"] == "PASS" for s in stages),
            "total": len(stages)}


def run_entry(rep, label, path, **extra):
    """One plotted PUZ run, as eval.js's forest() reads it."""
    agg = rep["aggregate"]
    return dict({"label": label, "path": path, "solver": rep["solver"], "puzzles": agg["n_puzzles"], "k": agg["k"],
                 "headline": agg["headline"], "pass@1": {k: agg["pass@1"][k] for k in ("value", "ci95", "floors")},
                 "attempts": rep["integrity"]["attempts"]}, **extra)


def puz():
    manifest = pf.load_suite(PUZ_SUITE)
    runs = []
    for label, rel in PUZ_RUNS:
        path = os.path.join(PUZ_DIR, rel)
        if not os.path.isdir(path):
            continue
        rep = ps.score(manifest, ps.load_rows([path]), "open", 5)
        runs.append(run_entry(rep, label, os.path.relpath(path, REPO)))
    return {"suite": os.path.relpath(PUZ_SUITE, REPO), "report": os.path.relpath(os.path.join(PUZ_DIR, "REPORT.md"), REPO),
            "runs": runs}


def puz_check(check_dir):
    """A pilot PUZ score from a check directory (check-suite.json, ids.txt, results/: the layout of the hard-suite
    Luna check on the demo-recordings branch), scored by puzzle_score.score on that suite's open split. None when
    the directory is not given or not there. A pilot (one attempt, a sample of boards) is labelled so, never pooled."""
    if not check_dir or not os.path.isdir(check_dir):
        return None
    manifest = pf.load_suite(os.path.join(check_dir, "check-suite.json"))
    with open(os.path.join(check_dir, "ids.txt")) as f:
        ids = set(f.read().replace(",", " ").split())  # puzzle_score's --ids form: comma-separated
    rep = ps.score(manifest, ps.load_rows([os.path.join(check_dir, "results")]), "open", 1, ids=ids)
    return run_entry(rep, "%s, %d boards, one attempt each" % (rep["solver"].split("/", 1)[-1], rep["aggregate"]["n_puzzles"]),
                     check_dir, suite=rep["suite"], pilot=True)


def panel_plan(panel):
    spec, _, _ = panel_score.load_panel(PANELS, panel)
    games = len(spec["cells"]) * len(panel_score.parse_seed_spec(spec["seeds"])) * (2 if spec.get("pairs") else 1)
    return {"panel": panel, "cells": len(spec["cells"]), "games": games, "seeds": spec["seeds"],
            "cost_usd": [round(games * c, 2) for c in GAME_COST_USD], "game_cost_usd": list(GAME_COST_USD)}


def report(acceptance_dir=None, hard_dir=None):
    try:
        hard = puz_check(hard_dir)
    except Exception as e:  # a wrong check dir costs the pilot row, never the whole page
        hard = {"error": "%s: %s" % (hard_dir, e)}
    return {
        "acceptance": acceptance(acceptance_dir) if acceptance_dir else None,
        "headlines": [
            {"key": "STR-AI", "what": "win rate against Forge's AI on the frozen mirror panel (eval/panel_score.py)",
             "floor": "the floor and random sanity arms on the same cells; Forge's AI mirror is 0.5",
             "status": "not yet run", "plan": panel_plan("mirror")},
            {"key": "STR-HELDOUT", "what": "the same, on seeds blocked from every training corpus (eval/seed_policy.py)",
             "floor": "as STR-AI", "status": "not yet run", "plan": None},
            {"key": "PUZ", "what": "mean over puzzles of pass@1 minus the puzzle's random solve rate (eval/puzzle_score.py)",
             "floor": "0 for random play by construction; the PASS and first-option policies alongside",
             "status": "recorded", "result": dict(puz(), hard=hard)},
        ],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--acceptance", help="an OUT dir of eval/harness_acceptance.sh (holds summary.txt)")
    ap.add_argument("--hard-results", help="a PUZ check dir (check-suite.json, ids.txt, results/), scored as a pilot")
    a = ap.parse_args(argv)
    print(json.dumps(report(a.acceptance, a.hard_results), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
