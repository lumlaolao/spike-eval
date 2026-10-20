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
              "script_unconsumed": 0, "script_lines": 16, "script_played": True, "plays_not_made": 0}

    def test_only_a_whole_line_that_solves_is_played(self):
        self.assertEqual(pb.line_verdict(self.PLAYED), "played")
        # h_0 under a typo: the drift's do-nothing answers still stumbled into the acceptance state.
        drifted = dict(self.PLAYED, script_drift='line 3: "Thing in Ice" fits no option', script_played=False)
        self.assertTrue(pb.line_verdict(drifted).startswith("drift: line 3"))
        short = dict(self.PLAYED, script_unconsumed=5, script_played=False)
        self.assertEqual(pb.line_verdict(short), "5 lines unplayed")
        # A play the line chose that never happened (unpayable, so the engine rolled it back) voids the line, whatever
        # card it was: the seat counts it (pr-review of #276; a log-name match refused every creature cast).
        self.assertEqual(pb.line_verdict(dict(self.PLAYED, plays_not_made=1)), "1 plays the line chose did not happen")
        self.assertEqual(pb.line_verdict(dict(self.PLAYED, plays_not_made=0)), "played")
        unread = dict(self.PLAYED); del unread["plays_not_made"]
        self.assertTrue(pb.line_verdict(unread).startswith("no plays_not_made"), "a missing count is not a zero")
        lost = dict(self.PLAYED, status="failed", solved=False, acceptance_failed=["life p1 <= 0 (was 7)"])
        self.assertIn("was 7", pb.line_verdict(lost))
        self.assertEqual(pb.line_verdict(dict(self.PLAYED, tainted_by_deckout=True)), "solved by a deck-out")
        self.assertEqual(pb.line_verdict({"status": "solved"}), "not a written-line row")
        self.assertEqual(pb.line_verdict(None), "no result row")

    def test_against_the_search_opponent_only_a_proof_with_the_line_played_passes(self):
        # RP_141: two Canopy Gorgers attack, and every block the opponent can make is beaten (search row, written solver).
        proof = {"status": "proven", "solver_arm": "llm", "opponent_arm": "search", "script_played": True,
                 "script_drift": "", "script_unconsumed": 0, "opponent_branches_beaten": 2}
        self.assertEqual(pb.line_verdict(proof), "proven")
        beaten = dict(proof, status="not_found", refutation_line="Opponent:2:1", script_played=None)
        self.assertEqual(pb.line_verdict(beaten), "beaten by Opponent:2:1 (lost)")
        # How the line fared under the refuting reply is said (pr-review of #278: a drift read like a loss).
        self.assertEqual(pb.line_verdict(dict(beaten, refutation_status="failed")), "beaten by Opponent:2:1 (failed)")
        self.assertEqual(pb.line_verdict(dict(beaten, refutation_script_drift="line 4: x")),
                         "beaten by Opponent:2:1 (drift: line 4: x)")
        self.assertEqual(pb.line_verdict(dict(beaten, refutation_status="solved", refutation_plays_not_made=1)),
                         "beaten by Opponent:2:1 (1 plays the line chose did not happen)")
        self.assertEqual(pb.line_verdict(dict(beaten, refutation_line=None)), "not_found")
        # A main line that won before its end is still the proof; a drift or a lost play is not.
        self.assertEqual(pb.line_verdict(dict(proof, script_played=False, script_unconsumed=2)), "proven")
        self.assertNotEqual(pb.line_verdict(dict(proof, script_drift="line 2: y")), "proven")
        self.assertNotEqual(pb.line_verdict(dict(proof, plays_not_made=1)), "proven")
        env = pb.line_env(Path("/x/h.pzl"), "search", budget=300)
        self.assertEqual((env["CONTROLLER_B"], env["PZL_SEARCH_BUDGET"]), ("search", "300"))
        self.assertNotIn("PZL_SEARCH_BUDGET", pb.line_env(Path("/x/h.pzl"), "ai"))

    def test_the_line_runs_as_the_model_seat_under_the_paid_condition(self):
        env = pb.line_env(Path("/x/h_1.pzl"), "ai")
        self.assertEqual((env["CONTROLLER_A"], env["CONTROLLER_B"]), ("llm", "ai"))
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
            self.assertEqual(seen["PZL_SEEDS"], "1..2")
            pb.Build = lambda *a, **k: FakeBuild([dict(self.PLAYED, seed=1), dict(self.PLAYED, seed=2, script_unconsumed=1)])
            self.assertEqual(pb.main(args), 1)
            script = out / "other.line"
            script.write_text("cast Lava Spike\n", encoding="utf-8")
            pb.main(args + ["--script", str(script)])
            self.assertEqual(seen["SMART_MODEL_A"], "script:" + str(script.resolve()))
        finally:
            pb.Build = real


class MinimalityTest(unittest.TestCase):
    """HARD-PUZZLES.md section 4, item 3: every solver action of a written line is needed; remove one, it fails."""

    LINES = ["cast Lava Spike", "opponent", "cast Fling", "Goblin", "cast Shock", "opponent"]

    def test_an_action_is_its_opening_line_and_what_follows(self):
        self.assertEqual(pb.action_groups(6, [0, 2, 4]), [[0, 1], [2, 3], [4, 5]])
        self.assertEqual(pb.action_groups(3, [1]), [[0], [1, 2]], "lines before the first action stand alone")
        self.assertEqual(pb.action_groups(2, []), [[0], [1]])

    def test_each_action_is_removed_in_turn_and_a_spare_one_is_named(self):
        out = Path(tempfile.mkdtemp())
        pzl = out / "h.pzl"
        pzl.write_text("[metadata]\nName:h\n[state]\nturn=1\n[solution]\n" + "\n".join(self.LINES) + "\n")
        runs, relaxed = [], []

        class FakeBuild:
            def run(self, env, out_dir, timeout_s=None):
                spec = env["SMART_MODEL_A"][len("script:"):]
                relaxed.append(spec.startswith("relaxed:"))
                lines = open(spec[len("relaxed:"):] if relaxed[-1] else spec).read().split("\n")
                lines = [l for l in lines if l and not l.startswith("[") and not l.startswith("Name") and l != "turn=1"]
                runs.append(lines)
                solved = "cast Lava Spike" in lines and "cast Fling" in lines   # Shock is spare
                # Without Lava Spike the replay hits the wall-clock cap: it shows nothing either way.
                status = "invalid" if "cast Lava Spike" not in lines else "solved" if solved else "failed"
                row = {"status": status, "seed": 1, "script_played": True, "script_drift": "",
                       "script_unconsumed": 0, "script_actions": ["0", "2", "4"], "plays_not_made": 0,
                       "script_lines": len(lines)}
                Path(out_dir).mkdir(parents=True, exist_ok=True)
                with open(Path(out_dir) / "puzzle-results.jsonl", "a") as f:
                    f.write(json.dumps(row) + "\n")
                return 0
        real = pb.Build
        try:
            pb.Build = lambda *a, **k: FakeBuild()
            rc = pb.main(["line", "--pzl", str(pzl), "--minimality", "--out-dir", str(out / "o")])
        finally:
            pb.Build = real
        self.assertEqual(rc, 1, "an unnecessary action fails the check")
        self.assertEqual(len(runs), 4, "the whole line, then one run per action")
        self.assertEqual(runs[3], ["cast Lava Spike", "opponent", "cast Fling", "Goblin"])
        self.assertEqual(relaxed, [False, True, True, True], "the removals are relaxed replays, the whole line is not")
        res = json.loads((out / "o" / "minimality.json").read_text())
        self.assertEqual((res["actions"], res["necessary"]), (3, 1), "only a game played out and lost shows need")
        self.assertEqual(res["unnecessary"], [["cast Shock", "opponent"]])
        self.assertEqual(res["inconclusive"], [["cast Lava Spike", "opponent"]])

    def test_only_a_game_played_out_and_lost_shows_an_action_is_needed(self):
        ok = {"status": "failed", "script_drift": "", "plays_not_made": 0}
        self.assertEqual(pb.removal_verdict(ok), "fails")
        self.assertEqual(pb.removal_verdict(dict(ok, status="solved")), "played")
        self.assertTrue(pb.removal_verdict(dict(ok, script_drift="line 3: z")).startswith("inconclusive: drift"))
        self.assertTrue(pb.removal_verdict(dict(ok, status="invalid")).startswith("inconclusive"))
        self.assertTrue(pb.removal_verdict(dict(ok, tainted_by_deckout=True)).startswith("inconclusive"))
        self.assertTrue(pb.removal_verdict(dict(ok, plays_not_made=1)).startswith("inconclusive"))
        self.assertTrue(pb.removal_verdict(None).startswith("inconclusive"))
        self.assertEqual(pb.removal_verdict(dict(ok, status="solved", script_unconsumed=3)), "played",
                         "a replay that won before its last line still shows the action is not needed")
        stalled = dict(ok, script_unconsumed=8)
        self.assertEqual(pb.removal_verdict(stalled), "inconclusive: stalled, 8 lines unplayed",
                         "lost without playing the rest: nothing shown about the removed action")

    def test_the_lines_are_split_and_trimmed_as_the_java_reader_does(self):
        f = Path(tempfile.mkdtemp()) / "l.txt"
        # A form feed is not a line break in Java's readAllLines; a non-breaking space is not trimmed by trim().
        f.write_text("a\r\n\u00a0\nb\x0cc\n  # note\n\n d \n", encoding="utf-8")
        self.assertEqual(pb.script_lines(f), ["a", "\u00a0", "b\x0cc", "d"])

class GreedyArmTest(unittest.TestCase):
    """The greedy-preview floor (HARD-PUZZLES.md section 4, item 4) is a baseline arm like the others."""

    def test_the_greedy_arm_is_the_model_seat_with_previews_and_no_model(self):
        seen = {}

        class FakeBuild:
            def run(self, env, out_dir, timeout_s=None):
                seen.update(env)
                Path(out_dir).mkdir(parents=True, exist_ok=True)
                (Path(out_dir) / "puzzle-results.jsonl").write_text(json.dumps({"status": "failed", "seed": 1}) + "\n")
                return 0
        pb.run_arm(FakeBuild(), Path("/x/h.pzl"), "greedy", "1..4", Path(tempfile.mkdtemp()))
        self.assertEqual((seen["CONTROLLER_A"], seen["SMART_MODEL_A"], seen["FAST_MODEL_A"]), ("llm", "greedy:", "greedy:"))
        self.assertEqual(seen["LOOKAHEAD"], "1", "the floor is the seat's own one-ply preview")
        self.assertEqual(seen["MEMORY_TOOLS"], "0")
        self.assertFalse(any(k.startswith("OPENROUTER") for k in seen if k != "OPENROUTER_BASE_URL"))
        self.assertIn("greedy", pb.DEFAULT_SEEDS)
        import puzzle_format as pf
        self.assertIn("greedy", pf.BASELINE_ARMS)



if __name__ == "__main__":
    unittest.main()
