"""puzzle_baselines.py: the `line` command (a written solution played through the model seat), the greedy arm's
condition, and the commands that write a record's difficulty and v2 tier (prove, retier, run --baselines-only)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import puzzle_baselines as pb  # noqa: E402
import run_batch  # noqa: E402


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
        cond = pb.greedy_condition()
        self.assertEqual(cond["env"]["SMART_MODEL_A"], "greedy:")
        self.assertTrue(str(cond["harness_epoch"]).startswith("harness-epoch-"), cond)


PZL = ("[metadata]\nName:x\nGoal:Win\nTurns:1\n[state]\nturn=1\np0hand=Lava Spike;Fling\n"
       "p0battlefield=Mountain;Thermo-Alchemist\np0library=Island;Island\np1battlefield=Wall of Omens\n"
       "p1graveyard=Shock\n[acceptance]\nlife p1 <= 0\n")


class DifficultyCommandsTest(unittest.TestCase):
    """The commands that write a record's difficulty, v2 tier and skills (prove, retier, run --baselines-only)."""

    def test_a_proven_record_gets_its_difficulty_tier_and_tags(self):
        repo = Path(tempfile.mkdtemp())
        (repo / "h.pzl").write_text(PZL, encoding="utf-8")
        rec = {"id": "h", "pzl": str(repo / "h.pzl"), "source": {"kind": "rules"},
               "baselines": {"ai": {"solved": 0, "n": 4, "rate": 0.0}},
               "proof": {"status": "proven", "opponent": "search", "line": "Solver:3:1 Solver:2:0", "solver_actions": 2, "turns": 1,
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
        (repo / "h.pzl").write_text(PZL, encoding="utf-8")
        rec = {"id": "h", "pzl": str(repo / "h.pzl"), "source": {"kind": "rules"}, "baselines": {},
               "proof": {"status": "proven", "opponent": "search", "turns": 1, "line": "Solver:2:0", "solver_actions": 6, "opponent_choice_points": 0}}
        suite = repo / "s.json"
        suite.write_text(json.dumps({"schema": "puzzle-suite.v1", "suite": "s", "puzzles": [rec]}), encoding="utf-8")
        self.assertEqual(pb.main(["retier", "--suite", str(suite), "--write", str(repo / "t.json")]), 0)
        self.assertEqual(json.loads((repo / "t.json").read_text())["puzzles"][0]["tier_v2"], 3)

    def test_an_unreadable_pzl_gives_no_tier_and_fails_retier(self):
        # With FORGE_HOME at another checkout every forge: record read as T=None and board_objects=0, and retier
        # rewrote them all with exit 0 (pr-review of #280).
        repo = Path(tempfile.mkdtemp())
        old = {"L": 1, "D": 1, "O": 0, "T": 1, "B": 2, "board_objects": 5}
        rec = {"id": "h", "pzl": str(repo / "missing.pzl"), "source": {"kind": "rules"}, "baselines": {},
               "difficulty": old, "tier_v2": 1, "proof": {"status": "proven", "opponent": "search", "turns": 1,
                                                         "line": "Solver:2:0", "solver_actions": 1}}
        out = pb.apply_difficulty(rec)
        self.assertEqual((out["difficulty"], out["tier_v2"], out["skills"]), (None, None, None),
                         "no measure from a pzl that was not read, and no old one beside a new proof")
        suite = repo / "s.json"
        suite.write_text(json.dumps({"schema": "puzzle-suite.v1", "suite": "s", "puzzles": [rec]}), encoding="utf-8")
        with self.assertRaises(SystemExit):
            pb.main(["retier", "--suite", str(suite), "--write", str(repo / "t.json")])
        self.assertFalse((repo / "t.json").exists(), "no manifest is written")

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

    def test_baselines_only_keeps_the_status_true_to_the_new_rates(self):
        # An active record whose new random rate is above the drop threshold is dropped, and its v1 tier follows the
        # AI's new rate (pr-review of #280: both were left as they were).
        real = pb.run_arm
        pb.run_arm = lambda build, pzl, arm, seeds, out, extra_env=None: [{"status": "solved", "seed": i} for i in range(4)]
        try:
            rec = {"id": "h", "pzl": "/x/h.pzl", "source": {"kind": "rules"}, "status": "active", "tier": 3,
                   "verification": {"status": "verified"}, "reference_line": {"solver_actions": 2}}
            out = pb.baseline_puzzle(None, rec, Path(tempfile.mkdtemp()), 4, 1, 0, ["random", "ai"], baselines_only=True)
        finally:
            pb.run_arm = real
        self.assertEqual(out["status"], "dropped")
        self.assertIn("random_above_threshold", out["flags"])
        self.assertEqual(out["tier"], pb.tier_for(1.0, 2))

    def test_a_proof_not_found_keeps_no_old_tier(self):
        # pr-review of #280 at e23f74d: re-proving with FORGE_HOME elsewhere wrote a not_found proof beside the old
        # difficulty and tier_v2 1.
        class NoGame:
            def run(self, env, out, timeout_s=None):
                Path(out).mkdir(parents=True, exist_ok=True)
        repo = Path(tempfile.mkdtemp())
        rec = {"id": "h", "pzl": str(repo / "missing.pzl"), "source": {"kind": "rules"}, "baselines": {},
               "difficulty": {"L": 1, "D": 1, "O": 0, "T": 1, "B": 2, "board_objects": 5}, "tier_v2": 1,
               "skills": ["rules application"], "proof": {"status": "proven"}}
        out = pb.prove_puzzle(NoGame(), rec, repo / "out", 10, "search")
        self.assertEqual(out["proof"]["status"], "not_found")
        self.assertEqual((out["difficulty"], out["tier_v2"], out["skills"]), (None, None, None))

    def test_apply_difficulty_gives_no_tier_from_a_pzl_that_is_not_the_one_recorded(self):
        # pr-review of #280 at 0374732: only retier checked pzl_sha256, so prove and run tiered another checkout's file.
        repo = Path(tempfile.mkdtemp())
        (repo / "h.pzl").write_text(PZL, encoding="utf-8")
        rec = {"id": "h", "pzl": str(repo / "h.pzl"), "pzl_sha256": "0" * 64, "source": {"kind": "rules"},
               "baselines": {}, "proof": {"status": "proven", "opponent": "search", "turns": 1, "line": "Solver:2:0",
                                          "solver_actions": 1}}
        out = pb.apply_difficulty(rec)
        self.assertEqual((out["difficulty"], out["tier_v2"], out["skills"]), (None, None, None))
        self.assertIsNotNone(pb.apply_difficulty(dict(rec, pzl_sha256=pb.pf.sha256_file(repo / "h.pzl")))["difficulty"])

    def test_run_and_prove_refuse_before_any_game_on_a_pzl_that_is_not_the_one_recorded(self):
        # pr-review of #281: the check ran after the games, so run still wrote the v1 tier, the rates and the status,
        # and prove a proof, measured on another checkout's file.
        repo = Path(tempfile.mkdtemp())
        (repo / "h.pzl").write_text(PZL, encoding="utf-8")
        rec = {"id": "h", "pzl": str(repo / "h.pzl"), "pzl_sha256": "0" * 64, "source": {"kind": "rules"}}
        suite = repo / "s.json"
        suite.write_text(json.dumps({"schema": "puzzle-suite.v1", "suite": "s", "puzzles": [rec]}), encoding="utf-8")
        calls = []
        real_arm, real_build = pb.run_arm, pb.Build
        pb.run_arm = lambda *a, **k: calls.append("arm") or []
        pb.Build = lambda *a, **k: calls.append("build")
        try:
            for cmd in (["run", "--arms", "ai", "--baselines-only"], ["prove"]):
                with self.assertRaises(SystemExit, msg=cmd[0]):
                    pb.main([cmd[0], "--suite", str(suite), "--write", str(repo / "t.json")] + cmd[1:])
        finally:
            pb.run_arm, pb.Build = real_arm, real_build
        self.assertEqual(calls, [], "no build and no game")
        self.assertFalse((repo / "t.json").exists())

    def test_retier_refuses_a_pzl_that_is_not_the_one_proven(self):
        # Another Forge checkout can hold a file of the same name with other contents (pr-review of #280).
        repo = Path(tempfile.mkdtemp())
        (repo / "h.pzl").write_text(PZL, encoding="utf-8")
        rec = {"id": "h", "pzl": str(repo / "h.pzl"), "pzl_sha256": "0" * 64, "source": {"kind": "rules"},
               "baselines": {}, "proof": {"status": "proven", "opponent": "search", "turns": 1, "line": "Solver:2:0",
                                          "solver_actions": 1}}
        suite = repo / "s.json"
        suite.write_text(json.dumps({"schema": "puzzle-suite.v1", "suite": "s", "puzzles": [rec]}), encoding="utf-8")
        with self.assertRaises(SystemExit):
            pb.main(["retier", "--suite", str(suite), "--write", str(repo / "t.json")])
        self.assertFalse((repo / "t.json").exists())

    def test_baselines_only_clears_the_v1_tier_of_an_unverified_record(self):
        # The normal path sets tier None when the line is not verified; --baselines-only kept the old tier.
        real = pb.run_arm
        pb.run_arm = lambda build, pzl, arm, seeds, out, extra_env=None: [{"status": "failed", "seed": i} for i in range(4)]
        try:
            rec = {"id": "h", "pzl": "/x/h.pzl", "source": {"kind": "rules"}, "status": "candidate", "tier": 2,
                   "verification": {"status": "failed"}}
            out = pb.baseline_puzzle(None, rec, Path(tempfile.mkdtemp()), 4, 1, 0, ["ai"], baselines_only=True)
        finally:
            pb.run_arm = real
        self.assertIsNone(out["tier"])


class BuildFreshnessTest(unittest.TestCase):
    """A build runs only when its compiled overlay is this checkout's (the build's overlay-sha.txt, written by
    harness_acceptance.sh's compile stage, equals benchmark_version.overlay_sha_of_checkout). Before, the default
    was the last NAME under runs/harness-acceptance (an old ps-* build) and --build was never checked, so a paid
    puzzle run could play a stale or unmerged harness under this checkout's provenance."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.forge = self.tmp / "forge"
        (self.forge / "forge-gui-desktop").mkdir(parents=True)
        self.root = self.tmp / "acceptance"
        self.patches = [mock.patch.object(pb, "checkout_overlay_sha", return_value="aaaaaaaaaaaa"),
                        mock.patch.object(pb, "ACCEPTANCE_ROOT", self.root)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def make(self, name, sha, mtime=None):
        d = self.root / name
        (d / "classes/forge/spike").mkdir(parents=True)
        (d / "classes/forge/spike/LlmSpikePuzzle.class").write_bytes(b"")
        (d / "classpath.txt").write_text("/x.jar\n")
        if sha is not None:
            (d / pb.BUILD_SHA_FILE).write_text(sha + "\n")
            if mtime is not None:
                os.utime(d / pb.BUILD_SHA_FILE, (mtime, mtime))
        return d

    def test_a_build_of_this_checkout_is_accepted(self):
        d = self.make("b1", "aaaaaaaaaaaa")
        b = pb.Build(d, self.forge)
        self.assertEqual(b.dir, d.resolve())
        self.assertEqual(b.provenance(), {"build_dir": str(d.resolve()), "build_overlay_sha": "aaaaaaaaaaaa",
                                          "checkout_overlay_sha": "aaaaaaaaaaaa", "stale_build_allowed": False})

    def test_a_puzzle_game_never_inherits_a_condition_from_the_shell(self):
        # Every condition run_batch.py pins (CONDITION_ENV_KEYS) and the shadow labeller it pins apart: a value in the
        # operator's shell never reaches a puzzle game unrecorded; puzzle_solve.py sets the ones a puzzle uses through
        # env_extra (#342 PROPOSE_AUDIT, #345 SHADOW_AI each needed a review round to close this by hand).
        b = pb.Build(self.make("b2", "aaaaaaaaaaaa"), self.forge)
        seen = {}
        def call(cmd, **kw):
            seen.update(kw["env"])
            return 0
        names = {n: "1" for n in run_batch.CONDITION_ENV_KEYS + ("SHADOW_AI",)}
        # A name added to PINNED_ENV_KEYS alone (the list the runner strips) must still be tested here.
        self.assertEqual(tuple(names), run_batch.PINNED_ENV_KEYS)
        with mock.patch.dict(os.environ, names), mock.patch.object(pb.subprocess, "call", call):
            self.assertEqual(b.run({}, tempfile.mkdtemp()), 0)
        self.assertEqual([n for n in names if n in seen], [])
        seen.clear()
        with mock.patch.dict(os.environ, names), mock.patch.object(pb.subprocess, "call", call):
            b.run({"ONE_TIER": "1"}, tempfile.mkdtemp())
        self.assertEqual(seen.get("ONE_TIER"), "1", "a caller's own setting still reaches the game")

    def test_a_build_of_other_sources_or_unrecorded_is_refused(self):
        for name, sha in (("other", "bbbbbbbbbbbb"), ("unrecorded", None), ("empty", "")):
            with self.assertRaises(SystemExit, msg=name) as cm:
                pb.Build(self.make(name, sha), self.forge)
            self.assertIn("--allow-stale-build", str(cm.exception.code), name)

    def test_a_checkout_whose_overlay_cannot_be_hashed_matches_nothing(self):
        with mock.patch.object(pb, "checkout_overlay_sha", return_value="unknown"):
            with self.assertRaises(SystemExit):
                pb.Build(self.make("u", "unknown"), self.forge)

    def test_the_default_is_the_newest_matching_build_by_mtime_not_by_name(self):
        self.make("a-new", "aaaaaaaaaaaa", mtime=2_000_000)
        self.make("b-old", "aaaaaaaaaaaa", mtime=1_000_000)
        self.make("z-stale", "bbbbbbbbbbbb", mtime=3_000_000)
        self.make("zz-unrecorded", None)
        self.assertEqual(pb.Build(None, self.forge).dir.name, "a-new")

    def test_the_default_refuses_when_no_build_matches_and_says_how_to_build_one(self):
        self.make("z-stale", "bbbbbbbbbbbb")
        with self.assertRaises(SystemExit) as cm:
            pb.Build(None, self.forge)
        self.assertIn('STAGES="classpath compile"', str(cm.exception.code))
        with self.assertRaises(SystemExit):  # the override names one build; it never picks a stale one itself
            pb.Build(None, self.forge, allow_stale=True)

    def test_the_override_runs_a_stale_build_and_says_so_in_every_run_dir(self):
        d = self.make("old", "bbbbbbbbbbbb")
        b = pb.Build(d, self.forge, java="true", allow_stale=True)
        self.assertEqual(b.provenance()["stale_build_allowed"], True)
        self.assertEqual(b.provenance()["build_overlay_sha"], "bbbbbbbbbbbb")
        out = self.tmp / "out"
        b.run({}, out)
        self.assertEqual(json.loads((out / "build.json").read_text()), b.provenance())
        # A build that matches is not called stale even with the flag.
        self.assertFalse(pb.Build(self.make("fresh", "aaaaaaaaaaaa"), self.forge,
                                  allow_stale=True).provenance()["stale_build_allowed"])

    def test_the_cli_flag_reaches_the_build(self):
        with mock.patch.object(pb, "Build", side_effect=SystemExit("stop")) as build:
            with self.assertRaises(SystemExit):
                pb.main(["--build", "/x", "--allow-stale-build", "line", "--pzl", "/x.pzl"])
        self.assertEqual(build.call_args, mock.call("/x", None, allow_stale=True))


if __name__ == "__main__":
    unittest.main()
