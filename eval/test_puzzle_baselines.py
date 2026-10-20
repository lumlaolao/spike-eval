"""puzzle_baselines.py: the `line` command, which plays a puzzle's written solution through the model seat."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import puzzle_baselines as pb  # noqa: E402


class WrittenLineTest(unittest.TestCase):
    """`line`: a puzzle's written solution played by the faithful seat (ScriptBridge, HARD-PUZZLES.md section 4)."""

    PLAYED = {"status": "solved", "solved": True, "acceptance": "accepted", "script_drift": "", "script_exhausted": False,
              "script_unconsumed": 0, "script_lines": 16, "script_played": True}

    def test_only_a_whole_line_that_solves_is_played(self):
        self.assertEqual(pb.line_verdict(self.PLAYED), "played")
        # h_0 under a typo: the drift's do-nothing answers still stumbled into the acceptance state.
        drifted = dict(self.PLAYED, script_drift='line 3: "Thing in Ice" fits no option', script_played=False)
        self.assertTrue(pb.line_verdict(drifted).startswith("drift: line 3"))
        short = dict(self.PLAYED, script_unconsumed=5, script_played=False)
        self.assertEqual(pb.line_verdict(short), "5 lines unplayed")
        # A cast the harness accepted but the engine rolled back (its payment failed) never happened (pr-review, #276).
        rolled = dict(self.PLAYED, script_taken=["cast Lava Spike => CAST Lava Spike @1 {R} [x]",
                                                 "cast Fireblast => CAST Fireblast @3 [y]"],
                      actions=["STACK_ADD: Solver cast Lava Spike targeting [Opponent]"])
        self.assertEqual(pb.line_verdict(rolled), "no cast of Fireblast reached the stack (the line took 1)")
        self.assertEqual(pb.line_verdict(dict(rolled, actions=rolled["actions"] + ["STACK_ADD: Solver cast Fireblast"])),
                         "played")
        # Copies log as casts too (a storm copy is "Solver cast Grapeshot"): they must not stand in for a cast of
        # another card that was rolled back, so casts are matched by card name (pr-review v2, #276).
        storm = dict(self.PLAYED, script_taken=["cast Bolt => CAST Lightning Bolt @4 {R} [x]",
                                                "cast Grapeshot => CAST Grapeshot @7 1{R} [y]"],
                     actions=["STACK_ADD: Solver cast Grapeshot targeting [Opponent]"] * 3)
        self.assertEqual(pb.line_verdict(storm), "no cast of Lightning Bolt reached the stack (the line took 1)")
        # A face-down cast logs as "Morph", not by name.
        morph = dict(self.PLAYED, script_taken=["cast Thing => CAST Den Protector @2 face down {3} [z]"],
                     actions=["STACK_ADD: Solver cast Morph"])
        self.assertEqual(pb.line_verdict(morph), "played")
        lost = dict(self.PLAYED, status="failed", solved=False, acceptance_failed=["life p1 <= 0 (was 7)"])
        self.assertIn("was 7", pb.line_verdict(lost))
        self.assertEqual(pb.line_verdict(dict(self.PLAYED, tainted_by_deckout=True)), "solved by a deck-out")
        self.assertEqual(pb.line_verdict({"status": "solved"}), "not a written-line row")
        self.assertEqual(pb.line_verdict(None), "no result row")

    def test_the_line_runs_as_the_model_seat_under_the_paid_condition(self):
        env = pb.line_env(Path("/x/h_1.pzl"), "ai", "1..3")
        self.assertEqual((env["CONTROLLER_A"], env["CONTROLLER_B"], env["PZL_SEEDS"]), ("llm", "ai", "1..3"))
        self.assertEqual(env["SMART_MODEL_A"], "script:/x/h_1.pzl")
        self.assertEqual(env["FAST_MODEL_A"], env["SMART_MODEL_A"])
        self.assertEqual(env["ONE_TIER"], "1", "one bridge, one position: never inherited from the shell")
        import puzzle_solve
        import argparse
        luna = puzzle_solve.condition(argparse.Namespace(context_profile="v4-faithful", memory_tools=False, rules_tool=False))
        for k, v in luna.items():
            self.assertEqual(env[k], v, "the seat a written line plays is the seat Luna plays tools off: " + k)
        self.assertFalse(any(k.startswith("OPENROUTER") for k in env), "a script needs no endpoint and sends nothing")

    def test_the_command_passes_only_when_every_seed_played(self):
        out = Path(tempfile.mkdtemp())
        pzl_file = out / "h.pzl"
        pzl_file.write_text("[metadata]\nName:h\n[state]\nturn=1\n[solution]\ncast Lava Spike\n", encoding="utf-8")
        seen = {}

        class FakeBuild:
            def __init__(self, rows):
                self.rows = rows

            def run(self, env, out_dir, timeout_s=None):
                seen.update(env)
                Path(out_dir).mkdir(parents=True, exist_ok=True)
                with open(Path(out_dir) / "puzzle-results.jsonl", "a") as f:
                    for r in self.rows:
                        f.write(json.dumps(r) + "\n")
                return 0
        real = pb.Build
        try:
            pb.Build = lambda *a, **k: FakeBuild([dict(self.PLAYED, seed=1), dict(self.PLAYED, seed=2)])
            args = ["line", "--pzl", str(pzl_file), "--seeds", "1..2", "--out-dir", str(out / "o")]
            self.assertEqual(pb.main(args), 0)
            self.assertEqual(seen["PUZZLE"], str(pzl_file.resolve()))
            pb.Build = lambda *a, **k: FakeBuild([dict(self.PLAYED, seed=1), dict(self.PLAYED, seed=2, script_unconsumed=1)])
            self.assertEqual(pb.main(args), 1)
            script = out / "other.line"
            script.write_text("cast Lava Spike\n", encoding="utf-8")
            pb.main(args + ["--script", str(script)])
            self.assertEqual(seen["SMART_MODEL_A"], "script:" + str(script.resolve()))
        finally:
            pb.Build = real



if __name__ == "__main__":
    unittest.main()
