#!/usr/bin/env python3
"""The PUZ puzzle axis: record and suite validation, scoring and its refusals, and the baseline
runner's pure decisions (docs/eval/PUZZLES.md). No JVM: the Java side is PuzzleArmsTest."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)
import puzzle_baselines as pb  # noqa: E402
import puzzle_format as pf  # noqa: E402
import puzzle_score as ps  # noqa: E402

STATE = "turn=3\nactiveplayer=p0\nactivephase=MAIN1\np0life=20\np1life=3\np0battlefield=Grizzly Bears\n"


def pzl(goal="Win", turns=3, name="Test"):
    return pf.build_pzl(name, goal, turns, STATE, "a test puzzle")


def rec(pid, repo, random_solved=0, n=100, status="active", tier=2, goal="Win", turns=3):
    path = Path(repo) / "puzzles" / (pid + ".pzl")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(pzl(goal, turns, pid), encoding="utf-8")
    return {
        "schema": pf.PUZZLE_SCHEMA, "id": pid, "split": pf.split_for(pid), "status": status, "tier": tier,
        "goal": {"engine_goal": goal, "turns": turns}, "source": {"kind": "forge-builtin", "file": pid + ".pzl"},
        "pzl": "puzzles/%s.pzl" % pid, "pzl_sha256": pf.sha256_file(path),
        "baselines": {arm: {"solved": random_solved if arm == "random" else 0, "n": n,
                            "rate": (random_solved if arm == "random" else 0) / n}
                      for arm in pf.REQUIRED_BASELINES},
        "reference_line": {"arm": "random", "seed": 7, "tape": "i4:2", "solver_actions": 2},
        "verification": {"status": "verified"},
    }


def suite(recs, frozen=True):
    m = {"schema": pf.SUITE_SCHEMA, "suite": "t", "version": "1", "engine_sha": "abc", "puzzles": recs}
    return pf.freeze(m) if frozen else m


def result(r, seed, status="solved", **kw):
    row = {"schema": "pzl-result-v1", "id": r["id"], "pzl_sha256": r["pzl_sha256"], "seed": seed, "status": status, "solver_arm": "llm"}
    row.update(kw)
    return row


class FormatTest(unittest.TestCase):

    def test_the_split_comes_from_the_id_alone_and_is_about_thirty_percent(self):
        ids = ["p%04d" % i for i in range(2000)]
        held = sum(1 for i in ids if pf.split_for(i) == "heldout")
        self.assertTrue(0.26 < held / len(ids) < 0.34, held)
        self.assertEqual(pf.split_for("abc"), pf.split_for("abc"))

    def test_a_pzl_round_trips_its_goal_and_refuses_a_goal_forge_does_not_enforce(self):
        self.assertEqual(pf.pzl_goal(pzl("Survive", 4)), ("Survive", 4, 3, "MAIN1"))
        with self.assertRaises(ValueError):
            pf.build_pzl("x", "Win with style", 3, STATE, "d")

    def test_a_turn_limit_below_the_start_turn_never_fires(self):
        self.assertTrue(pf.goal_is_bounded(3, 3, "MAIN1"))
        self.assertFalse(pf.goal_is_bounded(2, 3, "MAIN1"))
        self.assertTrue(pf.goal_is_bounded(2, 3, "CLEANUP"))

    def test_swapping_seats_swaps_every_player_reference(self):
        out = pf.swap_seats("activeplayer=p1\np0life=5\np1life=9\np1battlefield=Bear|Owner:P0")
        self.assertEqual(out, "activeplayer=p0\np1life=5\np0life=9\np0battlefield=Bear|Owner:P1")

    def test_a_valid_record_passes_and_each_defect_is_named(self):
        with tempfile.TemporaryDirectory() as d:
            r = rec("good", d)
            self.assertEqual(pf.validate_puzzle(r, repo=Path(d)), [])
            bad = dict(r, split="heldout" if r["split"] == "open" else "open")
            self.assertTrue(any("sha256(id)" in p for p in pf.validate_puzzle(bad, repo=Path(d))))
            easy = rec("easy", d, random_solved=30)
            self.assertTrue(any("random solves it" in p for p in pf.validate_puzzle(easy, repo=Path(d))))
            unverified = dict(r, verification={"status": "failed"})
            self.assertTrue(any("not verified" in p for p in pf.validate_puzzle(unverified, repo=Path(d))))
            edited = dict(r, pzl_sha256="0" * 64)
            self.assertTrue(any("pzl_sha256" in p for p in pf.validate_puzzle(edited, repo=Path(d))))
            unbounded = rec("unbounded", d, turns=1)
            self.assertTrue(any("never fires" in p for p in pf.validate_puzzle(unbounded, repo=Path(d))))

    def test_a_mined_puzzle_must_come_from_the_mining_band(self):
        with tempfile.TemporaryDirectory() as d:
            r = dict(rec("m", d), source={"kind": "mined", "seed": 42})
            self.assertTrue(any("20000-29999" in p for p in pf.validate_puzzle(r, repo=Path(d))))
            self.assertEqual(pf.validate_puzzle(dict(r, source={"kind": "mined", "seed": 20001}), repo=Path(d)), [])

    def test_a_frozen_suite_detects_an_edit_and_its_hash_ignores_puzzle_order(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = rec("a", d), rec("b", d)
            m = suite([a, b])
            self.assertEqual(pf.validate_suite(m, repo=Path(d)), [])
            self.assertEqual(pf.suite_hash(m), pf.suite_hash(dict(m, puzzles=[b, a])))
            m["puzzles"][0] = dict(a, tier=3)
            self.assertTrue(any("edited after freezing" in p for p in pf.validate_suite(m, repo=Path(d))))


class ScoreTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        # Pick ids that land in the open split, so one suite holds three scoreable puzzles.
        ids = [i for i in ("p%d" % n for n in range(40)) if pf.split_for(i) == "open"][:3]
        self.recs = [rec(ids[0], d, random_solved=10), rec(ids[1], d), rec(ids[2], d, tier=3)]
        self.m = suite(self.recs)

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self, solves):
        out = []
        for r, c in zip(self.recs, solves):
            out += [result(r, s, "solved" if s < c else "failed") for s in range(4)]
        return out

    def test_the_headline_is_pass_at_one_over_the_random_rate(self):
        rep = ps.score(self.m, self.rows([4, 2, 0]), "open", k=4, n_boot=200)
        a = rep["aggregate"]
        self.assertAlmostEqual(a["pass@1"]["value"], 0.5)
        self.assertAlmostEqual(a["headline"]["value"], (0.9 + 0.5 + 0.0) / 3)
        self.assertEqual(a["headline"]["floors"]["random"], 0.0)
        self.assertEqual(a["solved"]["all"]["solved"], 2)
        self.assertEqual(set(a["solved"]["by_tier"]), {"2", "3"})
        self.assertIn("PUZ", ps.render(rep))

    def test_none_solved_has_a_count_interval_wider_than_zero(self):
        rep = ps.score(self.m, self.rows([0, 0, 0]), "open", k=4, n_boot=200)
        s = rep["aggregate"]["solved"]["all"]
        self.assertEqual(s["ci95"], [0, 0], "the bootstrap's false certainty")
        self.assertEqual(s["ci95_wilson"][0], 0.0)
        self.assertGreater(s["ci95_wilson"][1], 1.0, "0 of 3 solved is compatible with more than one")
        self.assertIsNone(rep["aggregate"]["pass@1"]["ci95_wilson"], "k=4: attempts cluster by puzzle")
        self.assertIn("Wilson", ps.render(rep))

    def test_pass_hat_is_every_one_of_j_attempts_solving(self):
        self.assertEqual(ps.pass_hat(4, 4, 4), 1.0)
        self.assertAlmostEqual(ps.pass_hat(2, 4, 2), 1 / 6)
        self.assertEqual(ps.pass_hat(1, 4, 2), 0.0)

    def test_a_capped_game_or_a_deck_out_solve_counts_as_a_failure_and_unscoreable_is_refused(self):
        self.assertEqual(ps.attempt_success({"status": "invalid"}), (False, "capped"))
        self.assertEqual(ps.attempt_success({"status": "solved", "tainted_by_deckout": True}), (False, "tainted"))
        with self.assertRaises(ps.Refused):
            ps.attempt_success({"status": "unscoreable"})

    def test_it_refuses_what_would_not_mean_what_it_says(self):
        rows = self.rows([4, 4, 4])
        with self.assertRaises(ps.Refused):  # an unfrozen suite has no version
            ps.score(suite(self.recs, frozen=False), rows, "open")
        with self.assertRaises(ps.Refused):  # too few attempts
            ps.score(self.m, rows[:-1], "open")
        with self.assertRaises(ps.Refused):  # a repeated seed is not an independent attempt
            ps.score(self.m, rows[:-1] + [dict(rows[-2])], "open")
        with self.assertRaises(ps.Refused):  # two solvers in one score
            ps.score(self.m, rows[:-1] + [dict(rows[-1], solver_arm="random")], "open")
        with self.assertRaises(ps.Refused):  # a different version of the puzzle
            ps.score(self.m, rows[:-1] + [dict(rows[-1], pzl_sha256="f" * 64)], "open")
        with self.assertRaises(ps.Refused):  # results stamped with another suite version
            ps.score(self.m, [dict(r, suite_sha256="x") for r in rows], "open")
        with self.assertRaises(ps.Refused):
            ps.score(self.m, rows, "both")

    def test_the_main_entry_refuses_with_exit_two_and_scores_from_files(self):
        with tempfile.TemporaryDirectory() as d:
            # validate_suite reads the .pzl files relative to the repo, so point the records at
            # absolute paths under the temporary directory.
            m = pf.freeze(dict(self.m, puzzles=[dict(r, pzl=str(Path(self.tmp.name) / r["pzl"]))
                                                 for r in self.recs]))
            sp = Path(d) / "suite.json"
            sp.write_text(json.dumps(m))
            res = Path(d) / "puzzle-results.jsonl"
            res.write_text("\n".join(json.dumps(r) for r in self.rows([1, 1, 1])))
            self.assertEqual(ps.main(["--suite", str(sp), "--split", "open", "--bootstrap", "50", str(d)]), 0)
            res.write_text("\n".join(json.dumps(r) for r in self.rows([1, 1, 1])[:-1]))
            self.assertEqual(ps.main(["--suite", str(sp), "--split", "open", str(d)]), 2)


class BaselineDecisionTest(unittest.TestCase):

    def test_the_shortest_clean_solving_rollout_is_the_reference_line(self):
        rows = [{"status": "solved", "seed": 3, "tape": "a", "solver_actions": 4, "draws": 9},
                {"status": "solved", "seed": 5, "tape": "b", "solver_actions": 2, "draws": 9},
                {"status": "solved", "seed": 1, "tape": "c", "solver_actions": 1, "tainted_by_deckout": True},
                {"status": "failed", "seed": 2, "tape": "d", "solver_actions": 0}]
        self.assertEqual(pb.choose_line(rows)["seed"], 5)
        self.assertIsNone(pb.choose_line(rows[2:]))

    def test_a_forge_ai_solve_is_a_line_when_random_never_solves(self):
        rows = [{"status": "failed", "seed": 1}, {"status": "solved", "seed": 3, "solver_actions": 5, "log_sha256": "L"},
                {"status": "solved", "seed": 2, "solver_actions": 5, "log_sha256": "M", "tainted_by_deckout": True}]
        line = pb.choose_ai_line(rows)
        self.assertEqual((line["arm"], line["seed"], line["log_sha256"]), ("ai", 3, "L"))
        self.assertTrue(pf.has_line(line))
        self.assertFalse(pf.has_line(dict(line, log_sha256=None)))  # an AI line is replayed by its log
        self.assertFalse(pf.has_line({"arm": "random", "seed": 3}))  # a random line needs its tape
        self.assertIsNone(pb.choose_ai_line(rows[:1]))

    def test_a_written_line_is_the_pzls_own_solution_replayed(self):
        # A generated board (eval/puzzles/puzzle_gen.py) carries its line in its [solution] section, covered by pzl_sha256.
        line = {"arm": "written", "seed": 1, "solver_actions": 12}
        self.assertTrue(pf.has_line(line))
        self.assertFalse(pf.has_line(dict(line, solver_actions=None)))   # the length it was gated at
        self.assertFalse(pf.has_line(dict(line, seed=None)))
        played = {"script_played": True, "status": "solved", "plays_not_made": 0}
        self.assertEqual(pb.check_written_replay(line, played), [])
        self.assertTrue(pb.check_written_replay(line, None))
        for bad in ({"script_drift": "i4 vs i3"}, {"status": "failed"}, {"script_unconsumed": 2},
                    {"plays_not_made": 1}):
            self.assertTrue(pb.check_written_replay(line, dict(played, **bad)), bad)

    def test_a_replay_verifies_only_when_it_reaches_the_goal_on_the_same_line(self):
        line = {"log_sha256": "L"}
        self.assertEqual(pb.check_replay(line, {"status": "solved", "log_sha256": "L"}), [])
        self.assertTrue(pb.check_replay(line, None))
        for bad in ({"status": "failed"}, {"script_drift": "i4 vs i3"}, {"script_exhausted": True},
                    {"script_unconsumed": 2}, {"log_sha256": "other"}):
            self.assertTrue(pb.check_replay(line, dict({"status": "solved", "log_sha256": "L"}, **bad)), bad)

    def test_a_search_row_becomes_the_proof_and_a_malformed_proof_is_named(self):
        row = {"status": "proven", "search_replays": 62, "opponent_choice_points": 1, "line": "Solver:2:1",
               "solver_actions": 6, "actions": ["LAND: Solver played Swamp"], "search_drift": "", "seed": 1}
        proof = pb.proof_from_row(row, "search", "abc", "2026-10-18")
        self.assertEqual((proof["status"], proof["opponent"], proof["line"]), ("proven", "search", "Solver:2:1"))
        self.assertNotIn("search_drift", proof)          # empty fields are left out
        # pr-review of PR #268: the proof kept no verdict of the rules puzzles' acceptance or uniqueness search.
        verdicts = {"search_exhausted": True, "acceptance": "accepted", "acceptance_failed": ["life p1 = 0"],
                    "uniqueness": "unique", "uniqueness_deviations": 4, "uniqueness_exhausted": 4}
        kept = pb.proof_from_row(dict(row, **verdicts), "search", "abc", "2026-10-18")
        self.assertEqual({k: kept.get(k) for k in verdicts}, verdicts)
        self.assertIsNone(pb.proof_from_row(None, "search", "abc", "2026-10-18"))
        rec_ = rec("p1", tempfile.mkdtemp())
        self.assertEqual(pf.validate_puzzle(dict(rec_, proof=proof), check_files=False), [])
        for bad in ({"status": "solved", "opponent": "search"}, {"status": "proven", "opponent": "human"},
                    {"status": "proven", "opponent": "search", "solver_actions": 3}):
            self.assertTrue(pf.validate_puzzle(dict(rec_, proof=bad), check_files=False), bad)

    def test_a_proven_search_is_a_rules_puzzles_line_when_random_and_the_ai_never_solve(self):
        # Most rules puzzles take a line random play and Forge's AI never find; the search proof is the line.
        row = {"status": "proven", "acceptance": "accepted", "line": "Solver:3:1 Opponent:2:0", "seed": 1,
               "solver_actions": 2, "search_budget": 600}
        line = pb.choose_search_line(row)
        self.assertEqual((line["arm"], line["seed"], line["line"], line["budget"]), ("search", 1, row["line"], 600))
        self.assertTrue(pf.has_line(line))
        self.assertFalse(pf.has_line(dict(line, budget=None)))      # a search line is replayed by re-proving
        for bad in ({"status": "not_found"}, {"acceptance": "rejected"}, {"line": ""}):
            self.assertIsNone(pb.choose_search_line(dict(row, **bad)), bad)
        self.assertEqual(pb.check_search_replay(line, row), [])
        self.assertTrue(pb.check_search_replay(line, None))
        for bad in ({"status": "not_found"}, {"acceptance": "rejected"}):
            self.assertTrue(pb.check_search_replay(line, dict(row, **bad)), bad)
        # A rollout capped under load steers the search elsewhere: still proven and accepted, so still verified.
        self.assertEqual(pb.check_search_replay(line, dict(row, line="Solver:3:2")), [])

    def test_a_rules_puzzle_no_rollout_solves_gets_its_search_proof_as_a_verified_line(self):
        calls = []

        def fake_run_arm(build, pzl, arm, seeds, out_dir, extra_env=None):
            calls.append((arm, (extra_env or {}).get("CONTROLLER_B")))
            if arm == "search":
                return [{"status": "proven", "acceptance": "accepted", "line": "Solver:2:1", "seed": 1,
                         "solver_actions": 2, "search_budget": pb.SEARCH_LINE_BUDGET}]
            return [{"status": "failed", "seed": s} for s in pb.expand_seeds(seeds)]

        repo = tempfile.mkdtemp()
        r = dict(rec("rules-RP_1", repo), source={"kind": "rules"}, baselines={}, reference_line=None,
                 verification=None)
        real, pb.run_arm = pb.run_arm, fake_run_arm
        try:
            out = pb.baseline_puzzle(None, dict(r, pzl=str(Path(repo) / r["pzl"])), Path(repo) / "out", 4, 1, 0,
                                     ["random", "pass", "first", "ai"])
        finally:
            pb.run_arm = real
        self.assertEqual(out["reference_line"]["arm"], "search")
        self.assertEqual(out["verification"]["status"], "verified", out["verification"])
        self.assertEqual((out["tier"], out["status"]), (2, "active"))
        self.assertEqual(calls.count(("search", "search")), 2)   # found, then proven again to verify
        # A Forge-builtin puzzle never gets one: the search proves against every reply, its line is a rules thing.
        pb.run_arm = fake_run_arm
        try:
            out = pb.baseline_puzzle(None, dict(r, pzl=str(Path(repo) / r["pzl"]), source={"kind": "forge-builtin"}),
                                     Path(repo) / "out", 4, 1, 0, ["random", "pass", "first", "ai"])
        finally:
            pb.run_arm = real
        self.assertIsNone(out.get("reference_line"))

    def test_a_rerun_arm_reads_only_its_own_rows(self):
        # LlmSpikePuzzle appends to puzzle-results.jsonl: a second run read the first run's row (pr-review, PR #268).
        out = Path(tempfile.mkdtemp())
        (out / "puzzle-results.jsonl").write_text(json.dumps({"status": "solved", "seed": 1, "old": True}) + "\n")

        class FakeBuild:
            def run(self, env, out_dir, timeout_s=None):
                with open(Path(out_dir) / "puzzle-results.jsonl", "a") as f:
                    f.write(json.dumps({"status": "failed", "seed": 1}) + "\n")
                return 0
        rows = pb.run_arm(FakeBuild(), out / "x.pzl", "random", "1", out)
        self.assertEqual(rows, [{"status": "failed", "seed": 1}])

    def test_rules_candidates_are_the_converted_rows_with_their_files_and_questions(self):
        repo = Path(tempfile.mkdtemp())
        d = repo / "eval/puzzles/rules-pilot"
        d.mkdir(parents=True)
        for pid in ("RP_001", "RP_002"):
            (d / (pid + ".pzl")).write_text(pzl("Survive", 3, pid), encoding="utf-8")
        rows = [{"puzzle": "RP_001", "question_id": "q1", "rule": "CR 1", "result": "converted",
                 "proof": {"status": "proven"}, "uniqueness": {"verdict": "unique"}},
                {"puzzle": "RP_002", "question_id": "q2", "rule": "CR 2", "result": "multiple_review"},
                {"puzzle": None, "question_id": "q3", "result": "dropped"}]
        (d / "manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        m, skipped = pf.rules_candidates(d / "manifest.jsonl", "rules", "1", "abc", repo=repo)
        self.assertEqual([r["id"] for r in m["puzzles"]], ["rules-RP_001"])
        r = m["puzzles"][0]
        self.assertEqual((r["pzl"], r["status"], r["goal"]), ("eval/puzzles/rules-pilot/RP_001.pzl", "candidate",
                                                              {"engine_goal": "Survive", "turns": 3}))
        self.assertEqual((r["source"]["kind"], r["source"]["question_id"]), ("rules", "q1"))
        self.assertEqual(r["pzl_sha256"], pf.sha256_file(d / "RP_001.pzl"))
        self.assertEqual(pf.pzl_path(r, repo), d / "RP_001.pzl")
        self.assertEqual(pf.validate_puzzle(r, check_files=False), [])
        self.assertEqual(skipped, [])

    def test_tier_and_status(self):
        self.assertEqual(pb.tier_for(0.75, 9), 1)
        self.assertEqual(pb.tier_for(0.0, 3), 2)
        self.assertEqual(pb.tier_for(0.0, 4), 3)
        self.assertIsNone(pb.tier_for(None, 3))
        ok = {"baselines": {a: {"rate": 0.0} for a in pf.REQUIRED_BASELINES}, "verification": {"status": "verified"}}
        self.assertEqual(pb.decide_status(ok)[0], "active")
        easy = dict(ok, baselines=dict(ok["baselines"], random={"rate": 0.5}))
        self.assertEqual(pb.decide_status(easy), ("dropped", ["random_above_threshold"]))
        self.assertEqual(pb.decide_status(dict(ok, verification={}))[0], "candidate")

    def test_seed_specs_count_ranges_and_singles(self):
        self.assertEqual(pb.seed_count("1..4"), 4)
        self.assertEqual(pb.seed_count("1..3,9, 12..13"), 6)
        s = pb.summarize([{"status": "solved"}, {"status": "invalid"}, {"status": "solved", "tainted_by_deckout": True}], "1..3")
        self.assertEqual((s["solved"], s["n"], s["invalid"], s["tainted"]), (1, 3, 1, 1))


class ResumeTest(unittest.TestCase):
    """run --resume reuses an arm's rows only when the same build ran exactly the same seeds."""

    def test_reuse_needs_the_same_build_and_the_same_seeds(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            build = type("B", (), {"classes": Path("/b/one/classes"), "overlay_sha": "aaaaaaaaaaaa"})()
            (out / "java.args").write_text("-Xmx2048m\n-cp\n/b/one/classes:/forge/x.jar\n")
            (out / "build.json").write_text(json.dumps({"build_overlay_sha": "aaaaaaaaaaaa"}))
            rows = [{"seed": s, "status": "failed"} for s in (1, 2, 3)]
            (out / "puzzle-results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
            self.assertEqual(pb.reusable(build, out, "1..3"), rows)
            self.assertIsNone(pb.reusable(build, out, "1..4"), "a partial run is run again")
            other = type("B", (), {"classes": Path("/b/one/classes-old"), "overlay_sha": "aaaaaaaaaaaa"})()
            self.assertIsNone(pb.reusable(other, out, "1..3"), "another build's rows are not reused")

    def test_reuse_needs_the_same_compiled_overlay_too(self):
        # pr-review of #319: the classes path is the same when a build dir is recompiled from other sources.
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            build = type("B", (), {"classes": Path("/b/one/classes"), "overlay_sha": "aaaaaaaaaaaa"})()
            (out / "java.args").write_text("-Xmx2048m\n-cp\n/b/one/classes:/forge/x.jar\n")
            (out / "puzzle-results.jsonl").write_text(json.dumps({"seed": 1}) + "\n")
            self.assertIsNone(pb.reusable(build, out, "1"), "no build.json: rows from before builds were recorded")
            (out / "build.json").write_text(json.dumps({"build_overlay_sha": "bbbbbbbbbbbb"}))
            self.assertIsNone(pb.reusable(build, out, "1"), "recompiled from other sources")
            (out / "build.json").write_text(json.dumps({"build_overlay_sha": "aaaaaaaaaaaa"}))
            self.assertIsNotNone(pb.reusable(build, out, "1"))
            unknown = type("B", (), {"classes": Path("/b/one/classes"), "overlay_sha": "unknown"})()
            (out / "build.json").write_text(json.dumps({"build_overlay_sha": "unknown"}))
            self.assertIsNone(pb.reusable(unknown, out, "1"), "two unrecorded builds are not known to be one")

    def test_seed_specs_expand(self):
        self.assertEqual(pb.expand_seeds("1..3,7"), [1, 2, 3, 7])
        self.assertEqual(pb.seed_count("1..3,7"), 4)


if __name__ == "__main__":
    unittest.main()
