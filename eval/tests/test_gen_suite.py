"""scripts/puzzle-gate/gen_suite.py: a generated board is active only when its written line verified, every floor
failed it; any other board is dropped with its reason."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)

EVAL = evalpath.EVAL
sys.path.insert(0, str(EVAL.parent / "scripts" / "puzzle-gate"))
import gen_suite as gs  # noqa: E402
import puzzle_format as pf  # noqa: E402

PZL = """[metadata]
Name:%s
Goal:Survive
Turns:1
Difficulty:Hard
Description:It is your second main phase.
[state]
turn=1
activeplayer=p0
activephase=MAIN2
p1life=12
[solution]
# steps: 12
cast Lightning Bolt
opponent
"""

PLAYED = {"script_played": True, "status": "solved", "plays_not_made": 0, "seed": 1, "log_sha256": "L"}
FLOORS_OK = {"random": (0, 20), "pass": (0, 4), "first": (0, 4), "greedy": (0, 4), "ai": (0, 4)}


def gate(tmp, boards, name="gate"):
    """A gate output dir for `boards`: {stem: (floors, line_row)}."""
    repo, g = Path(tmp), Path(tmp) / name
    bdir = repo / "eval/puzzles/hard/gen"
    bdir.mkdir(parents=True, exist_ok=True)
    rows, recs = [], []
    for stem, (floors, line_row) in boards.items():
        p = bdir / (stem + ".pzl")
        p.write_text(PZL % stem + "Rule:generated-ping | ping before each spell\n", encoding="utf-8")
        rows.append({"board": stem, "solved": True, "played": True, "actions": 12})
        base = {arm: {"solved": s, "n": n, "rate": s / n} for arm, (s, n) in floors.items()}
        base["greedy"]["condition"] = {"harness_epoch": "harness-epoch-70"}
        pid = "hard-" + stem.replace("_", "-")
        recs.append({"schema": pf.PUZZLE_SCHEMA, "id": pid, "split": pf.split_for(pid), "status": "candidate",
                     "goal": {"engine_goal": "Survive", "turns": 1}, "source": {"kind": "authored"},
                     "pzl": str(p), "pzl_sha256": pf.sha256_file(p), "baselines": base})
        (g / "line" / stem).mkdir(parents=True)
        (g / "line" / stem / "puzzle-results.jsonl").write_text(json.dumps(line_row) + "\n")
    (g / "gate.json").write_text(json.dumps(rows))
    (g / "floors.json").write_text(json.dumps({"puzzles": recs}))
    return repo, g, bdir


class GenSuiteTest(unittest.TestCase):
    def test_only_a_verified_board_that_beats_every_floor_is_active(self):
        boards = {"ping_good": (FLOORS_OK, PLAYED),
                  "ping_ai": (dict(FLOORS_OK, ai=(2, 4)), PLAYED),
                  "ping_random": (dict(FLOORS_OK, random=(1, 20)), PLAYED),
                  "ping_drift": (FLOORS_OK, dict(PLAYED, script_drift="i3 vs i2"))}
        with tempfile.TemporaryDirectory() as tmp:
            repo, g, bdir = gate(tmp, boards)
            suite = gs.build_suite(g, bdir, repo=repo, engine="abc", date="2026-10-21")
            by = {r["id"]: r for r in suite["puzzles"]}
            self.assertEqual([i for i, r in by.items() if r["status"] == "active"], ["hard-ping-good"])
            good = by["hard-ping-good"]
            self.assertEqual(good["reference_line"], {"arm": "written", "seed": 1, "solver_actions": 12})
            self.assertEqual(good["verification"]["status"], "verified")
            self.assertEqual(good["pzl"], "eval/puzzles/hard/gen/ping_good.pzl")
            self.assertEqual(good["source"]["family"], "ping")
            self.assertEqual(good["tier"], 3)
            self.assertIn("floor ai 2", by["hard-ping-ai"]["flags"])
            self.assertIn("floor random 1", by["hard-ping-random"]["flags"])
            self.assertTrue(any(f.startswith("line: drift") for f in by["hard-ping-drift"]["flags"]))
            self.assertEqual(pf.validate_suite(suite, repo=repo), [])
            self.assertTrue(suite["frozen"])

    def test_a_board_edited_after_the_gate_is_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, g, bdir = gate(tmp, {"ping_good": (FLOORS_OK, PLAYED)})
            p = bdir / "ping_good.pzl"
            p.write_text(p.read_text() + "# edited\n")
            rec = gs.build_suite(g, bdir, repo=repo, engine="abc")["puzzles"][0]
            self.assertEqual((rec["status"], rec["flags"]), ("dropped", ["pzl_changed_since_gate"]))
    def test_a_suite_gathers_every_gate_its_boards_ran_in(self):
        # gen2 was gated family by family in separate runs; one suite takes them all, under its own name.
        with tempfile.TemporaryDirectory() as tmp:
            repo, g1, bdir = gate(tmp, {"ping_a": (FLOORS_OK, PLAYED)}, "gate1")
            _, g2, _ = gate(tmp, {"ping_b": (dict(FLOORS_OK, ai=(3, 4)), PLAYED)}, "gate2")
            suite = gs.build_suite([g1, g2], bdir, repo=repo, engine="abc", name="hard-gen2")
            self.assertEqual(suite["suite"], "hard-gen2")
            self.assertEqual({r["id"]: r["status"] for r in suite["puzzles"]},
                             {"hard-ping-a": "active", "hard-ping-b": "dropped"})

    def test_a_board_gated_in_two_runs_is_one_record_from_the_later_run(self):
        # pr-review of #299: a board re-gated into a new directory came out twice, and the suite was written anyway.
        with tempfile.TemporaryDirectory() as tmp:
            repo, g1, bdir = gate(tmp, {"ping_a": (dict(FLOORS_OK, ai=(3, 4)), PLAYED)}, "gate1")
            _, g2, _ = gate(tmp, {"ping_a": (FLOORS_OK, PLAYED)}, "gate2")
            recs = gs.build_suite([g1, g2], bdir, repo=repo, engine="abc")["puzzles"]
            self.assertEqual([(r["id"], r["status"]) for r in recs], [("hard-ping-a", "active")])


if __name__ == "__main__":
    unittest.main()
