"""puzzle_format.py's measured difficulty and v2 tier (BENCHMARK-V2.md section 4), and the baselines commands that
write them."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import puzzle_baselines as pb  # noqa: E402
import puzzle_format as pf  # noqa: E402


class DifficultyV2Test(unittest.TestCase):
    """BENCHMARK-V2 section 4: the tier is computed from the proof, never typed in."""

    PZL = ("[metadata]\nName:x\nGoal:Win\nTurns:1\n[state]\nturn=1\np0hand=Lava Spike;Fling\n"
           "p0battlefield=Mountain;Thermo-Alchemist\np0library=Island;Island\np1battlefield=Wall of Omens\n"
           "p1graveyard=Shock\n[acceptance]\nlife p1 <= 0\n")

    def test_the_structure_comes_off_the_proof_line(self):
        proof = {"status": "proven", "line": "Solver:6:2 Solver:1:0 Opponent:3:1 Solver:4:0 Opponent:1:0",
                 "solver_actions": 3, "opponent_choice_points": 1, "turns": 1}
        d = pf.difficulty(proof, self.PZL)
        self.assertEqual(d, {"L": 3, "D": 2, "O": 1, "T": 1, "B": 24, "board_objects": 6})

    def test_missing_counts_are_read_from_the_line_and_actions(self):
        proof = {"status": "proven", "line": "Solver:2:1 Opponent:2:0 Opponent:3:2",
                 "actions": ["STACK_ADD: Solver cast Shock", "COMBAT: Solver assigned X to attack"]}
        d = pf.difficulty(proof, self.PZL)
        self.assertEqual((d["L"], d["O"], d["T"]), (2, 2, None))
        self.assertIsNone(pf.difficulty({"status": "not_found"}, self.PZL), "no proof, no structure")

    def test_the_tier_rule(self):
        t = lambda L, O=0, T=1, B=1, ai=1.0: pf.tier_v2({"L": L, "D": L, "O": O, "T": T, "B": B}, ai)  # noqa: E731
        self.assertEqual(t(2), 1)
        self.assertEqual(t(2, ai=0.25), 2, "the AI fails a two-action puzzle: a capability item")
        self.assertIsNone(t(2, ai=None), "T1 needs the AI's rate")
        self.assertEqual(t(4), 2)
        self.assertEqual(t(1, O=1), 2)
        self.assertEqual(t(1, T=2), 2)
        self.assertEqual(t(6), 3)
        self.assertEqual(t(1, O=2), 3)
        self.assertEqual(t(5, B=10 ** 4), 3)
        self.assertIsNone(pf.tier_v2(None, 1.0))

    def test_skill_tags_are_mechanical(self):
        d = {"L": 7, "D": 7, "O": 1, "T": 2, "B": 10}
        actions = ["STACK_ADD: Solver cast Dark Ritual", "STACK_ADD: Solver cast Tendrils",
                   "COMBAT: Solver assigned Bears to attack"]
        tags = pf.skill_tags(d, actions, "rules")
        self.assertEqual(tags, ["rules application", "sequencing", "combat arithmetic", "long lines",
                                "adversarial", "multi-turn"])
        self.assertEqual(pf.skill_tags({"L": 0, "D": 0, "O": 0, "T": 1, "B": 1}, [], "forge-builtin"), ["restraint"])

    def test_a_proven_record_gets_its_difficulty_tier_and_tags(self):
        repo = Path(tempfile.mkdtemp())
        (repo / "h.pzl").write_text(self.PZL, encoding="utf-8")
        rec = {"id": "h", "pzl": str(repo / "h.pzl"), "source": {"kind": "rules"},
               "baselines": {"ai": {"solved": 0, "n": 4, "rate": 0.0}},
               "proof": {"status": "proven", "line": "Solver:3:1 Solver:2:0", "solver_actions": 2, "turns": 1,
                         "opponent_choice_points": 0, "actions": ["STACK_ADD: Solver cast Shock",
                                                                  "STACK_ADD: Solver cast Fling"]}}
        out = pb.apply_difficulty(rec)
        self.assertEqual(out["difficulty"]["B"], 6)
        self.assertEqual(out["tier_v2"], 2, "two actions, but Forge's AI fails it")
        self.assertEqual(out["skills"], ["rules application", "sequencing"])
        self.assertEqual(pb.apply_difficulty(dict(rec, proof={"status": "not_found"}))["tier_v2"], None)
        self.assertEqual(pb.proof_from_row({"status": "proven", "turns": 2}, "search", "x", "d")["turns"], 2)

    def test_retier_rewrites_every_record_from_its_proof(self):
        repo = Path(tempfile.mkdtemp())
        (repo / "h.pzl").write_text(self.PZL, encoding="utf-8")
        rec = {"id": "h", "pzl": str(repo / "h.pzl"), "source": {"kind": "rules"}, "baselines": {},
               "proof": {"status": "proven", "line": "Solver:2:0", "solver_actions": 6, "opponent_choice_points": 0}}
        suite = repo / "s.json"
        suite.write_text(json.dumps({"schema": "puzzle-suite.v1", "suite": "s", "puzzles": [rec]}), encoding="utf-8")
        self.assertEqual(pb.main(["retier", "--suite", str(suite), "--write", str(repo / "t.json")]), 0)
        self.assertEqual(json.loads((repo / "t.json").read_text())["puzzles"][0]["tier_v2"], 3)

    def test_baselines_only_measures_the_arms_and_nothing_else(self):
        calls = []
        real = pb.run_arm
        pb.run_arm = lambda build, pzl, arm, seeds, out, extra_env=None: calls.append(arm) or [{"status": "solved", "seed": 1}]
        try:
            rec = {"id": "h", "pzl": "/x/h.pzl", "source": {"kind": "rules"}, "status": "candidate"}
            out = pb.baseline_puzzle(None, rec, Path(tempfile.mkdtemp()), 4, 1, 0, ["ai"], baselines_only=True)
        finally:
            pb.run_arm = real
        self.assertEqual(calls, ["ai"], "no line search, no verification replay")
        self.assertEqual(out["baselines"]["ai"]["rate"], 1.0)
        self.assertEqual(out["status"], "candidate")

    def test_a_record_with_bad_difficulty_is_refused(self):
        good = {"L": 2, "D": 1, "O": 0, "T": 1, "B": 3, "board_objects": 4}
        self.assertEqual(pf.difficulty_problems("x", good, 1), [])
        self.assertTrue(pf.difficulty_problems("x", dict(good, L=-1), 1))
        self.assertTrue(pf.difficulty_problems("x", dict(good, Q=1), 1))
        self.assertTrue(pf.difficulty_problems("x", good, 4))



if __name__ == "__main__":
    unittest.main()
