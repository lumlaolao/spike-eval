#!/usr/bin/env python3
"""Gate generated boards (eval/puzzle_gen.py): a board is kept only when the engine agrees it is a hard puzzle.

  python3 scripts/puzzle-gate/gen_gate.py --build B --forge F --boards DIR --out DIR [-j 2] [--resume]

1. Its written line plays as written and solves (`puzzle_baselines.py line`), with 10 to 15 solver actions.
2. Every floor fails it: random 0 of 20, PASS 0 of 4, first option 0 of 4, greedy 0 of 4, and Forge's AI solves
   it at most 1 of 4 (`puzzle_baselines.py run --baselines-only`).

Writes OUT/gate.json (one row per board, with the reason a board was dropped) and copies the survivors to
OUT/kept/. Free: no model is called.
"""
import argparse
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "eval"))
import puzzle_format as pf  # noqa: E402

MIN_ACTIONS, MAX_ACTIONS = 10, 15
FLOORS = {"random": 0, "pass": 0, "first": 0, "greedy": 0, "ai": 1}   # most solves each may have


def last_row(out_dir):
    rows = [json.loads(l) for p in Path(out_dir).rglob("puzzle-results.jsonl") for l in p.open() if l.strip()]
    return rows[-1] if rows else None


def play_line(a, pzl):
    out = Path(a.out) / "line" / pzl.stem
    if not (getattr(a, "resume", False) and last_row(out)):
        shutil.rmtree(out, ignore_errors=True)
        subprocess.run([sys.executable, str(REPO / "eval/puzzle_baselines.py"), "--build", a.build, "--forge", a.forge,
                        "line", "--pzl", str(pzl), "--out-dir", str(out)], cwd=REPO, capture_output=True,
                       text=True, timeout=1800)
    r = last_row(out) or {}
    # The generator writes the board's real step count ("# steps: N"): the engine also counts a storm copy as a
    # cast, which is not a decision.
    stated = [int(l.split(":")[1]) for l in pzl.read_text(encoding="utf-8").splitlines()
              if l.startswith("# steps:")]
    row = {"board": pzl.stem, "solved": bool(r.get("solved")), "played": bool(r.get("script_played")),
           "actions": stated[0] if stated else r.get("solver_actions"), "engine_actions": r.get("solver_actions"),
           "drift": r.get("script_drift") or "",
           "unconsumed": r.get("script_unconsumed")}
    if not (row["solved"] and row["played"]):
        row["dropped"] = "line: %s" % (row["drift"] or "solved=%s unconsumed=%s" % (row["solved"], row["unconsumed"]))
    elif not (isinstance(row["actions"], int) and MIN_ACTIONS <= row["actions"] <= MAX_ACTIONS):
        row["dropped"] = "length: %s solver actions" % row["actions"]
    return row


def suite_of(paths, out):
    recs = []
    for p in paths:
        pid = "hard-" + p.stem.replace("_", "-")
        goal, turns, _, _ = pf.pzl_goal(p.read_text(encoding="utf-8"))
        recs.append({"schema": pf.PUZZLE_SCHEMA, "id": pid, "split": pf.split_for(pid), "status": "candidate",
                     "goal": {"engine_goal": goal, "turns": turns}, "source": {"kind": "authored"},
                     "pzl": str(p.resolve()), "pzl_sha256": pf.sha256_file(p)})
    out.write_text(json.dumps({"schema": "puzzle-suite.v1", "suite": "hard-gen-gate", "version": "0",
                               "frozen": False, "puzzles": recs}, indent=2), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", required=True)
    ap.add_argument("--forge", required=True)
    ap.add_argument("--boards", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("-j", "--jobs", type=int, default=2)
    ap.add_argument("--resume", action="store_true",
                    help="reuse a board's line result and the floors' rows already in --out (a gate that was stopped)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    boards = sorted(Path(a.boards).glob("*.pzl"))
    with ThreadPoolExecutor(max(1, a.jobs)) as ex:
        rows = list(ex.map(lambda p: play_line(a, p), boards))
    passed = [p for p, r in zip(boards, rows) if "dropped" not in r]
    by_id = {"hard-" + r["board"].replace("_", "-"): r for r in rows}
    if passed:
        suite = out / "suite.json"
        suite_of(passed, suite)
        subprocess.run([sys.executable, str(REPO / "eval/puzzle_baselines.py"), "--build", a.build, "--forge",
                        a.forge, "run", "--suite", str(suite), "--arms", ",".join(FLOORS), "--baselines-only",
                        "--rollouts", "20", "-j", str(a.jobs), "--out-dir", str(out / "floors"), "--write",
                        str(out / "floors.json")] + (["--resume"] if a.resume else []), cwd=REPO,
                       capture_output=True, text=True, timeout=6 * 3600)
        for rec in json.loads((out / "floors.json").read_text())["puzzles"]:
            r = by_id[rec["id"]]
            base = rec.get("baselines") or {}
            r["floors"] = {arm: (base.get(arm) or {}).get("solved") for arm in FLOORS}
            over = [arm for arm, most in FLOORS.items() if not isinstance(r["floors"][arm], int)
                    or r["floors"][arm] > most]
            if over:
                r["dropped"] = "floors: " + ", ".join("%s %s" % (arm, r["floors"][arm]) for arm in over)
        for r in rows:   # passed the line but has no floors record: never measured, never kept
            if "dropped" not in r and "floors" not in r:
                r["dropped"] = "floors: no record"
    kept = out / "kept"
    kept.mkdir(exist_ok=True)
    for p, r in zip(boards, rows):
        if "dropped" not in r:
            shutil.copy(p, kept / p.name)
    (out / "gate.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    n = sum(1 for r in rows if "dropped" not in r)
    print("%d of %d boards kept" % (n, len(rows)))
    for r in rows:
        print("%-24s %-6s %s" % (r["board"], "KEPT" if "dropped" not in r else "drop", r.get("dropped", r.get("floors"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
