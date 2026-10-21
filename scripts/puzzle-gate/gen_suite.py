#!/usr/bin/env python3
"""The frozen suite of generated hard puzzles, from the gate's output (gen_gate.py).

  python3 scripts/puzzle-gate/gen_suite.py --gate GATE_OUT --boards DIR --out SUITE.json

A board is active only when the gate's own rows show it verified hard:
1. its written [solution] played as written against Forge's AI and solved, in 10 to 15 steps (the line stage);
2. every floor failed it: random 0 of 20, PASS, first option and greedy 0 of 4, Forge's AI at most 1 of 4.
The opponent's life is exactly what the line deals (puzzle_gen.py sets it to the search's maximum for splice, red,
drain, elf and fling; for fling, over lines that cast each spell at the first priority it can). A board the floors
measured that fails is written as dropped with its reason; one dropped at the line stage is printed, not written
(a suite record needs its baselines).
Free: no model is called. `line --minimality` (puzzle_baselines.py) does not judge these boards: a removed step
leaves the rest of an interlocked line unplayable, so nearly every removal is inconclusive (drain_0026d851c85f,
2026-10-21: 1 of 53 groups necessary, 16 inconclusive, 36 of them `pass` lines).
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "eval"))
import puzzle_baselines as pb  # noqa: E402
import puzzle_format as pf  # noqa: E402

FLOORS = {"random": 0, "pass": 0, "first": 0, "greedy": 0, "ai": 1}   # most solves each may have (gen_gate.py)


def family_of(pzl_text):
    """The family puzzle_gen.py named in the board's Rule line ("Rule:generated-drain | ...")."""
    for line in pzl_text.splitlines():
        if line.startswith("Rule:generated-"):
            return line[len("Rule:generated-"):].split()[0]
    return None


def board_record(floor_rec, gate_row, line_row, pzl, repo=REPO, engine="unknown", date=None):
    """One suite record from the gate's floors record and its line-stage row and result.
    `pzl` is the board's file in the repository (the gate may have read a copy elsewhere)."""
    text = Path(pzl).read_text(encoding="utf-8")
    rec = dict(floor_rec, pzl=str(Path(pzl).resolve().relative_to(Path(repo).resolve())), pzl_sha256=pf.sha256_file(pzl),
               source={"kind": "authored", "generator": "eval/puzzle_gen.py", "family": family_of(text)})
    flags = []
    if pf.sha256_file(pzl) != floor_rec.get("pzl_sha256"):
        flags.append("pzl_changed_since_gate")
    if gate_row.get("dropped"):
        flags.append("gate: " + gate_row["dropped"])
    actions = gate_row.get("actions")
    rec["reference_line"] = {"arm": "written", "seed": int((line_row or {}).get("seed") or 1),
                             "solver_actions": actions}
    verdict = pb.line_verdict(line_row)
    rec["verification"] = {"status": "verified" if verdict == "played" else "failed", "engine_sha": engine,
                           "date": date or datetime.date.today().isoformat(),
                           "replay_log_sha256": (line_row or {}).get("log_sha256")}
    if verdict != "played":
        flags.append("line: " + verdict)
    base = rec.get("baselines") or {}
    for arm, most in FLOORS.items():
        solved = (base.get(arm) or {}).get("solved")
        if not isinstance(solved, int) or solved > most:
            flags.append("floor %s %s" % (arm, solved))
    rec["tier"] = pb.tier_for((base.get("ai") or {}).get("rate"), actions)
    rec["status"] = "dropped" if flags else "active"
    rec["flags"] = flags
    return rec


def build_suite(gate_dir, boards_dir, repo=REPO, engine="unknown", date=None):
    gate_dir = Path(gate_dir)
    rows = json.loads((gate_dir / "gate.json").read_text())
    floors = {r["id"]: r for r in json.loads((gate_dir / "floors.json").read_text())["puzzles"]}
    recs = []
    for row in rows:
        pid = "hard-" + row["board"].replace("_", "-")
        pzl = Path(boards_dir) / (row["board"] + ".pzl")
        if pid not in floors:   # dropped at the line stage, never floored: no baselines to record
            print("  not in the suite %-24s %s" % (row["board"], row.get("dropped", "no floors record")))
            continue
        line_rows = pb.read_results(gate_dir / "line" / row["board"])
        recs.append(board_record(floors[pid], row, line_rows[-1] if line_rows else None, pzl, repo, engine, date))
    m = pf.candidate_suite("hard-gen", "1", engine, recs)
    return pf.freeze(m)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gate", required=True)
    ap.add_argument("--boards", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    engine = (REPO / "FORGE_COMMIT").read_text().strip() if (REPO / "FORGE_COMMIT").is_file() else "unknown"
    m = build_suite(a.gate, a.boards, engine=engine)
    problems = pf.validate_suite(m)
    for x in problems:
        print("PROBLEM", x)
    Path(a.out).write_text(json.dumps(m, indent=2) + "\n", encoding="utf-8")
    act = [r for r in m["puzzles"] if r["status"] == "active"]
    print("%s: %d active of %d, suite_sha256 %s" % (a.out, len(act), len(m["puzzles"]), m["suite_sha256"]))
    for r in m["puzzles"]:
        if r["status"] != "active":
            print("  dropped %-28s %s" % (r["id"], "; ".join(r["flags"])))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
