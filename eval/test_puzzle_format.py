"""puzzle_format.py: the puzzle.v1 record's baseline arms and what each must carry (eval/PUZZLES.md)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import puzzle_format as pf  # noqa: E402


class BaselineArmsTest(unittest.TestCase):
    """A record's baselines name known arms; a greedy rate carries the condition it was measured under."""

    def problems(self, baselines):
        rec = {"schema": "puzzle.v1", "id": "x", "split": "dev", "status": "candidate", "goal": {"engine_goal": "Win", "turns": 1},
               "source": {"kind": "rules"}, "pzl": "eval/puzzles/rules-pilot/RP_001.pzl", "pzl_sha256": "0" * 64,
               "baselines": baselines}
        return [p for p in pf.validate_puzzle(rec, check_files=False) if "baseline" in p]

    def test_an_unknown_arm_is_refused_and_greedy_is_known(self):
        rate = {"solved": 0, "n": 4, "rate": 0.0}
        self.assertTrue(any("unknown baseline arm" in p for p in self.problems({"deep": rate})))
        self.assertIn("greedy", pf.BASELINE_ARMS)

    def test_a_greedy_rate_without_its_epoch_is_refused(self):
        rate = {"solved": 0, "n": 4, "rate": 0.0}
        self.assertTrue(any("without its condition" in p for p in self.problems({"greedy": rate})))
        ok = dict(rate, condition={"harness_epoch": "harness-epoch-68", "env": {}})
        self.assertEqual(self.problems({"greedy": ok}), [])
        self.assertEqual(self.problems({"pass": rate}), [], "the other arms read no prompt: no condition needed")


if __name__ == "__main__":
    unittest.main()
