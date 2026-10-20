"""puzzle_format.py: the puzzle.v1 record's measured difficulty and v2 tier (BENCHMARK-V2.md section 4), and its
baseline arms."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import puzzle_format as pf  # noqa: E402


class DifficultyV2Test(unittest.TestCase):
    """BENCHMARK-V2 section 4: the tier is computed from the proof, never typed in."""

    PZL = ("[metadata]\nName:x\nGoal:Win\nTurns:1\n[state]\nturn=1\np0hand=Lava Spike;Fling\n"
           "p0battlefield=Mountain;Thermo-Alchemist\np0library=Island;Island\np1battlefield=Wall of Omens\n"
           "p1graveyard=Shock\n[acceptance]\nlife p1 <= 0\n")

    def test_the_structure_comes_off_the_proof_line(self):
        proof = {"status": "proven", "opponent": "search", "line": "Solver:6:2 Solver:1:0 Opponent:3:1 Solver:4:0 Opponent:1:0",
                 "solver_actions": 3, "opponent_choice_points": 1, "turns": 1}
        d = pf.difficulty(proof, self.PZL)
        self.assertEqual(d, {"L": 3, "D": 2, "O": 1, "T": 1, "B": 24, "board_objects": 6})

    def test_missing_counts_are_read_from_the_line_and_actions(self):
        proof = {"status": "proven", "opponent": "search", "line": "Solver:2:1 Opponent:2:0 Opponent:3:2",
                 "actions": ["STACK_ADD: Solver cast Shock", "COMBAT: Solver assigned X to attack"]}
        d = pf.difficulty(proof, self.PZL)
        self.assertEqual((d["L"], d["O"], d["T"]), (2, 2, None))
        self.assertIsNone(pf.difficulty({"status": "not_found"}, self.PZL), "no proof, no structure")

    def test_each_tier_starts_at_its_threshold(self):
        t = lambda L, O=0, T=1, B=1, ai=1.0: pf.tier_v2({"L": L, "D": L, "O": O, "T": T, "B": B}, ai)  # noqa: E731
        self.assertEqual(t(2), 1)
        self.assertEqual(t(2, ai=0.25), 2, "the AI fails a two-action puzzle: a capability item")
        self.assertIsNone(t(2, ai=None), "T1 needs the AI's rate")
        self.assertEqual(t(4), 2)
        self.assertEqual(t(1, O=1), 2)
        self.assertEqual(t(1, T=2), 2)
        self.assertEqual(t(6), 3)
        self.assertEqual(t(1, O=2), 3)
        self.assertEqual(t(1, B=92160), 1, "B counts search draws, not menus: alone it does not make a T3 (pr-review of "
                         "#280: RP_035, one cast of Animate Dead that Forge's AI always solves, read T3)")
        self.assertIsNone(pf.tier_v2(None, 1.0))
        self.assertIsNone(t(2, T=None), "a proof without its turns is not tiered as one turn (pr-review of #280)")

    def test_turns_are_counted_from_the_start_and_capped_at_the_limit(self):
        # Forge's row holds the absolute turn the game ended on: a one-turn Survive puzzle ends as turn 2 begins, so
        # it read T=2 and could never be T1 (pr-review of #280: 429 of 550 rules puzzles are Survive).
        survive = self.PZL.replace("Goal:Win", "Goal:Survive")
        proof = {"status": "proven", "opponent": "search", "line": "Solver:2:1", "solver_actions": 1, "turns": 2}
        self.assertEqual(pf.difficulty(proof, survive)["T"], 1)
        late = self.PZL.replace("Turns:1", "Turns:3").replace("turn=1", "turn=3")
        self.assertEqual(pf.difficulty(dict(proof, turns=3), late)["T"], 1, "a puzzle that starts on turn 3")
        two = self.PZL.replace("Turns:1", "Turns:2")
        self.assertEqual(pf.difficulty(dict(proof, turns=2), two)["T"], 2)
        # A start in CLEANUP leaves only that step of the first turn: the first real turn is the next one, and Forge's
        # limit moves one turn later with it (Puzzle.addGoalEnforcement). RP_209's header: Survive, Turns:2, turn=1.
        cleanup = two.replace("Goal:Win", "Goal:Survive").replace("turn=1", "turn=1\nactivephase=CLEANUP")
        self.assertEqual(pf.difficulty(dict(proof, turns=2), cleanup)["T"], 1, "won on the first real turn")
        self.assertEqual(pf.difficulty(dict(proof, turns=4), cleanup)["T"], 2, "survived to the limit")

    def test_no_turns_are_counted_without_the_pzl_or_a_real_end_turn(self):
        proof = {"status": "proven", "opponent": "search", "line": "Solver:2:1", "solver_actions": 1, "turns": 3}
        d = pf.difficulty(proof, "")
        self.assertIsNone(d["T"], "an unreadable pzl is not a one-turn puzzle (pr-review of #280)")
        self.assertIsNone(pf.tier_v2(d, 1.0))
        self.assertIsNone(pf.difficulty(dict(proof, turns=-1), self.PZL)["T"], "-1: the row had no main rollout")

    def test_opponent_points_count_only_against_the_search_opponent(self):
        # Against Forge's AI the line has no opponent draws, so O=0 would read as "the opponent cannot respond".
        proof = {"status": "proven", "opponent": "ai", "line": "Solver:2:1", "solver_actions": 1,
                 "opponent_choice_points": 0, "turns": 1}
        d = pf.difficulty(proof, self.PZL)
        self.assertIsNone(d["O"])
        self.assertIsNone(pf.tier_v2(d, 1.0), "not tiered without the adversarial count")

    def test_branching_is_capped(self):
        proof = {"status": "proven", "opponent": "search", "line": " ".join(["Solver:1000:0"] * 6), "solver_actions": 6,
                 "turns": 1}
        self.assertEqual(pf.difficulty(proof, self.PZL)["B"], pf.B_CAP)

    def test_skill_tags_are_mechanical(self):
        d = {"L": 7, "D": 7, "O": 1, "T": 2, "B": 10}
        actions = ["STACK_ADD: Solver cast Dark Ritual", "STACK_ADD: Solver cast Tendrils",
                   "COMBAT: Solver assigned Bears to attack"]
        tags = pf.skill_tags(d, actions, "rules")
        self.assertEqual(tags, ["rules application", "sequencing", "combat arithmetic", "long lines",
                                "adversarial", "multi-turn"])
        self.assertEqual(pf.skill_tags({"L": 0, "D": 0, "O": 0, "T": 1, "B": 1}, [], "forge-builtin"), ["restraint"])

    def test_a_record_with_bad_difficulty_is_refused(self):
        good = {"L": 2, "D": 1, "O": 0, "T": 1, "B": 3, "board_objects": 4}
        self.assertEqual(pf.difficulty_problems("x", good, 1), [])
        self.assertTrue(pf.difficulty_problems("x", dict(good, L=-1), 1))
        self.assertTrue(pf.difficulty_problems("x", dict(good, Q=1), 1))
        self.assertTrue(pf.difficulty_problems("x", good, 4))
        self.assertTrue(pf.difficulty_problems("x", None, 1), "a tier without its difficulty")
        self.assertTrue(pf.difficulty_problems("x", good, True), "True is not a tier")
        self.assertTrue(pf.difficulty_problems("x", good, 1, skills=["telepathy"]), "an unknown skill tag")
        self.assertEqual(pf.difficulty_problems("x", good, 1, skills=["sequencing"]), [])


class BaselineArmsTest(unittest.TestCase):
    """A record's baselines name known arms; a greedy rate carries the condition it was measured under."""

    RATE = {"solved": 0, "n": 4, "rate": 0.0}

    def problems(self, baselines):
        rec = {"schema": "puzzle.v1", "id": "x", "split": "open", "status": "candidate", "goal": {"engine_goal": "Win", "turns": 1},
               "source": {"kind": "rules"}, "pzl": "eval/puzzles/rules-pilot/RP_001.pzl", "pzl_sha256": "0" * 64,
               "baselines": baselines}
        return [p for p in pf.validate_puzzle(rec, check_files=False) if "baseline" in p]

    def test_an_unknown_arm_is_refused_and_greedy_is_known(self):
        self.assertTrue(any("unknown baseline arm" in p for p in self.problems({"deep": self.RATE})))
        self.assertIn("greedy", pf.BASELINE_ARMS)

    def test_a_greedy_rate_without_its_epoch_is_refused(self):
        self.assertTrue(any("without its condition" in p for p in self.problems({"greedy": self.RATE})))
        ok = dict(self.RATE, condition={"harness_epoch": "harness-epoch-68", "env": {}})
        self.assertEqual(self.problems({"greedy": ok}), [])
        self.assertEqual(self.problems({"pass": self.RATE}), [], "the other arms read no prompt: no condition needed")


if __name__ == "__main__":
    unittest.main()
