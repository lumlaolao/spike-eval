#!/usr/bin/env python3
"""Puzzle mode for the demo viewer: what a puzzle run's directory says about the puzzle it played.

  python3 eval/demo_puzzle.py /tmp/puzline        # print what the viewer's puzzle panel shows (JSON)

A puzzle run (eval/puzzle_baselines.py line, eval/puzzle_solve.py) writes game-*.jsonl, which the viewer replays
like any game, and puzzle-results.jsonl, which says how it ended. This module reads that result row and the
.pzl it names, and returns the panel: the brief the seat was given (the row's goal_oracle, else the .pzl's
Description), who played (the puzzle's written line, another script, or the model), how it ended, and, for the
audience only, the puzzle's intended
line (its Rule: note and [solution] steps). The seat never sees the Rule or the solution; the panel draws them
behind a reveal. Stdlib only.

What it cannot show: a result the run did not write (a puzzle killed mid-game has no result row), or a .pzl that
moved since the run (it is found by its recorded path, else by its id under eval/puzzles).
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import puzzle_format as pf  # noqa: E402

REPO = os.path.dirname(HERE)


def result_row(run_dir, seed=None):
    """The puzzle-results.jsonl row of the run: the one for `seed` when given (None when that attempt wrote no row:
    another seed's result is never borrowed), else the last."""
    path = os.path.join(run_dir, "puzzle-results.jsonl")
    try:
        with open(path) as f:
            rows = [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError):
        return None
    if seed is not None:
        rows = [r for r in rows if str(r.get("seed")) == str(seed)]
    return rows[-1] if rows else None


def find_pzl(row):
    """The .pzl a result row played: its recorded path, else <id>.pzl anywhere under eval/puzzles."""
    p = row.get("source_file") or ""
    if os.path.isfile(p):
        return p
    want = (row.get("id") or "") + ".pzl"
    for d, _, files in os.walk(os.path.join(REPO, "eval", "puzzles")):
        if want in files:
            return os.path.join(d, want)
    return None


def solver_of(row):
    """Who played: the puzzle's written line, another script (a --script file, a relaxed replay), or the model."""
    model = row.get("solver_model") or ""
    if model.startswith("script:"):
        path = model[len("script:"):]
        if path == row.get("source_file"):
            return {"kind": "written line", "detail": "the puzzle's [solution], replayed by the script arm (no model)"}
        return {"kind": "script", "detail": "a scripted line (%s), not the puzzle's [solution]" % path}
    return {"kind": "model", "detail": model or row.get("solver_arm") or "?"}


def steps_mappable(row):
    """True when each asked decision consumed exactly one [solution] line, in order: the written line itself, with no
    wait, untap fallback, drift or exhaustion (ScriptBridge answers those without consuming a line)."""
    return (solver_of(row)["kind"] == "written line" and bool(row.get("script_played")) and not row.get("script_drift")
            and not row.get("script_exhausted") and not row.get("script_waits") and not row.get("script_untaps_left"))


def info(run_dir, seed=None):
    """The puzzle panel for one run directory, or None when the directory holds no puzzle result."""
    row = result_row(run_dir, seed)
    if not row:
        return None
    out = {"id": row.get("id"), "status": row.get("status"), "solved": bool(row.get("solved")),
           "solver": solver_of(row), "solver_actions": row.get("solver_actions"),
           "harness_epoch": row.get("harness_epoch"), "brief": row.get("goal_oracle"),
           "script_lines": row.get("script_lines") if steps_mappable(row) else None}
    pzl = find_pzl(row)
    if not pzl:
        out["script_lines"] = None  # no [solution] to name the steps from
    else:
        with open(pzl, "rb") as f:
            raw = f.read()
        text = raw.decode("utf-8")
        # The file found today must be the one played: else its Rule and [solution] are not what the run faced.
        out["pzl_changed"] = bool(row.get("pzl_sha256")) and pf.sha256_bytes(raw) != row["pzl_sha256"]
        if out["pzl_changed"]:
            out["script_lines"] = None
        parsed = pf.parse_pzl(text)  # comment lines dropped, as Forge reads it
        meta = parsed.get("metadata", [])
        rule = pf.section_value(meta, "Rule", "") or ""
        out.update({"pzl": os.path.relpath(pzl, REPO) if pzl.startswith(REPO) else pzl,
                    "brief": out["brief"] or pf.section_value(meta, "Description", ""),
                    "family": rule.split("|", 1)[0].replace("generated-", "").strip() or None,
                    "rule": rule.split("|", 1)[1].strip() if "|" in rule else rule,
                    "solution": parsed.get("solution", [])})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    a = ap.parse_args(argv)
    print(json.dumps(info(a.run_dir), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
