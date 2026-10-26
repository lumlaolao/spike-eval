"""puzzle_score.py's paired comparison: two solvers, or two conditions, on the same puzzles of one frozen suite."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import puzzle_format as pf  # noqa: E402
import puzzle_score as ps  # noqa: E402
from test_puzzle_suite import rec, result, suite  # noqa: E402


class PairedTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ids = [i for i in ("p%d" % n for n in range(40)) if pf.split_for(i) == "open"][:3]
        self.recs = [rec(i, self.tmp.name) for i in ids]
        self.m = suite(self.recs)

    def tearDown(self):
        self.tmp.cleanup()

    def rep(self, solves, recs=None, **kw):
        recs = recs or self.recs
        rows = []
        for r, c in zip(recs, solves):
            rows += [result(r, s, "solved" if s < c else "failed", **kw) for s in range(4)]
        return ps.score(suite(recs) if recs is not self.recs else self.m, rows, "open", k=4, n_boot=50)

    def test_the_difference_is_taken_puzzle_by_puzzle(self):
        c = ps.paired(self.rep([4, 2, 0]), self.rep([4, 4, 2]), n_boot=500)
        self.assertAlmostEqual(c["mean"], (0 + 0.5 + 0.5) / 3)
        self.assertEqual(c["n_puzzles"], 3)
        self.assertLessEqual(c["ci95"][0], c["mean"])
        self.assertLessEqual(c["mean"], c["ci95"][1])
        sd = ((2 * (0.5 - 1 / 3) ** 2 + (1 / 3) ** 2) / 2) ** 0.5
        self.assertAlmostEqual(c["ci95_clustered"][1] - c["mean"], 1.96 * sd / 3 ** 0.5)

    def test_a_difference_of_differences_pairs_four_runs(self):
        a, b = self.rep([4, 4, 4]), self.rep([4, 2, 4])     # B loses 0.5 on one puzzle
        c, d = self.rep([4, 4, 4]), self.rep([4, 4, 4])     # D loses nothing
        dd = ps.paired(a, b, base=(c, d), n_boot=200)
        self.assertAlmostEqual(dd["mean"], -1 / 6, msg="(B - A) - (D - C), not the other way round")

    def test_runs_on_other_puzzles_or_attempts_are_not_paired(self):
        with self.assertRaisesRegex(ps.Refused, "differ in suite_sha256"):
            ps.paired(self.rep([4, 2, 0]), self.rep([4, 2], recs=self.recs[:2]))
        a = self.rep([4, 2, 0])
        with self.assertRaisesRegex(ps.Refused, "differ in k"):
            ps.paired(a, dict(a, aggregate=dict(a["aggregate"], k=5)))
        rows = []
        for r in self.recs:
            rows += [result(r, s, "solved") for s in range(4)]
        two = ps.score(self.m, rows, "open", k=4, n_boot=20, ids={self.recs[0]["id"], self.recs[1]["id"]})
        one = ps.score(self.m, rows, "open", k=4, n_boot=20, ids={self.recs[0]["id"]})
        with self.assertRaisesRegex(ps.Refused, "cover different puzzles"):
            ps.paired(two, one)

    def write_run(self, name, recs, solves):
        out = Path(self.tmp.name) / name
        out.mkdir(exist_ok=True)
        rows = []
        for r, c in zip(recs, solves):
            rows += [result(r, s, "solved" if s < c else "failed") for s in range(4)]
        (out / "puzzle-results.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows))
        return str(out)

    def run_cli(self, *args):
        recs = [dict(r, pzl=str(Path(self.tmp.name) / r["pzl"])) for r in self.recs]  # the suite check reads the files
        sfile = Path(self.tmp.name) / "s.json"
        sfile.write_text(json.dumps(suite(recs)))
        dirs = {n: self.write_run(n, recs, sv) for n, sv in
                (("a", [4, 4, 4]), ("b", [4, 2, 4]), ("c", [4, 4, 4]), ("d", [4, 4, 0]))}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = ps.main(["--suite", str(sfile), "--split", "open", "--k", "4"] + [dirs.get(x, x) for x in args])
        return rc, buf.getvalue()

    def test_the_command_prints_the_paired_line(self):
        rc, out = self.run_cli("a", "--paired-with", "b")
        self.assertEqual(rc, 0)
        self.assertIn("paired B - A", out)
        self.assertIn("-0.167", out)

    def test_the_command_prints_the_difference_of_differences(self):
        rc, out = self.run_cli("a", "--paired-with", "b", "--against", "c", "d")
        self.assertEqual(rc, 0)
        self.assertIn("paired (B - A) - (D - C)", out)
        self.assertIn("+0.167", out, "(-1/6) - (-1/3)")

    def test_a_mean_that_rounds_to_zero_prints_without_a_sign(self):
        self.assertEqual(ps.signed(-1.9e-18), "+0.000")
        self.assertEqual(ps.signed(-0.0004), "+0.000")
        self.assertEqual(ps.signed(-0.076), "-0.076")


    def test_the_paired_flags_reach_the_interval(self):
        seen = {}
        real = ps.paired
        ps.paired = lambda a, b, base=None, n_boot=None, seed=None: seen.update(n_boot=n_boot, seed=seed) or real(
            a, b, base, n_boot=n_boot, seed=seed)
        try:
            _, out = self.run_cli("a", "--paired-with", "b", "--paired-seed", "3", "--paired-bootstrap", "200")
        finally:
            ps.paired = real
        self.assertEqual(seen, {"n_boot": 200, "seed": 3}, "the interval is drawn as the line says")
        self.assertIn("200 resamples, seed 3", out)

    def test_against_needs_paired_with_and_a_refusal_exits_2(self):
        with self.assertRaises(SystemExit):
            self.run_cli("a", "--against", "c", "d")
        with self.assertRaises(SystemExit):
            self.run_cli("a", "--paired-with", "b", "--paired-bootstrap", "0")
        rc, _ = self.run_cli("a", "--paired-with", "b", "--ids", self.recs[0]["id"])
        self.assertEqual(rc, 0, "both runs scored on the same --ids")
        short = self.write_run("e", [dict(r, pzl=str(Path(self.tmp.name) / r["pzl"])) for r in self.recs[:2]], [4, 4])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc, _ = self.run_cli("a", "--paired-with", short)
        self.assertEqual(rc, 2, "a paired run missing a puzzle is refused, not a traceback")
        self.assertIn("REFUSED", err.getvalue())


class PriceCeilingRefusalTest(unittest.TestCase):
    """pr-review of #327: under a price ceiling an endpoint outage refuses every request, so the attempt's decisions
    fall to the fixed legal option. That row says nothing about the model: it is refused, never scored a 0."""

    def test_an_attempt_with_ceiling_refusals_is_refused_and_one_without_is_scored(self):
        row = {"id": "p", "seed": 1, "status": "failed", "routing_refusals": 3}
        with self.assertRaisesRegex(ps.Refused, "routing filter"):
            ps.attempt_success(row)
        self.assertEqual(ps.attempt_success(dict(row, routing_refusals=0)), (False, None))
        self.assertEqual(ps.attempt_success({"id": "p", "seed": 1, "status": "solved"}), (True, None))


class ScoreV2Test(unittest.TestCase):
    """BENCHMARK-V2 phase 2: clustered intervals and the power line, the budget and integrity lines, epochs never pooled,
    and the score by v2 tier."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ids = [i for i in ("p%d" % n for n in range(40)) if pf.split_for(i) == "open"][:3]
        self.recs = [rec(i, self.tmp.name) for i in ids]
        for r, t in zip(self.recs, (1, 2, 3)):
            r["tier_v2"] = t
        self.m = suite(self.recs)

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self, solves, **kw):
        out = []
        for r, c in zip(self.recs, solves):
            out += [result(r, s, "solved" if s < c else "failed", **kw) for s in range(4)]
        return out

    def test_the_clustered_interval_and_the_smallest_difference_it_can_see(self):
        a = ps.score(self.m, self.rows([4, 2, 0]), "open", k=4, n_boot=50)["aggregate"]
        sd = ((0.5 ** 2 + 0 + 0.5 ** 2) / 2) ** 0.5
        lo, hi = a["pass@1"]["ci95_clustered"]
        self.assertAlmostEqual(lo, 0.5 - 1.96 * sd / 3 ** 0.5)
        self.assertAlmostEqual(hi, 0.5 + 1.96 * sd / 3 ** 0.5)
        self.assertAlmostEqual(a["pass@1"]["mde80"], 2.8 * 2 ** 0.5 * sd / 3 ** 0.5)
        self.assertIsNotNone(a["headline"]["ci95_clustered"])

    def test_the_budget_line_reads_what_each_attempt_spent(self):
        rows = self.rows([4, 2, 0], harness_epoch="harness-epoch-69")
        for i, r in enumerate(rows):
            r.update(model_calls=3, prompt_tokens=1000 + i, completion_tokens=50, model_ms=200, lookups=i % 2)
        rep = ps.score(self.m, rows, "open", k=4, n_boot=50)
        b = rep["budget"]
        self.assertEqual((b["attempts"], b["recorded"]), (12, 12))
        self.assertEqual(b["model_calls"]["total"], 36)
        self.assertEqual(b["prompt_tokens"]["median"], 1005.5)
        self.assertEqual(b["lookups"]["total"], 6)
        text = ps.render(rep)
        self.assertIn("budget per attempt", text)
        self.assertIn("clustered", text)
        self.assertEqual(ps.score(self.m, self.rows([4, 2, 0]), "open", k=4, n_boot=20)["budget"]["recorded"], 0)
        self.assertIn("budget: not recorded", ps.render(ps.score(self.m, self.rows([4, 2, 0]), "open", k=4, n_boot=20)))

    def test_integrity_counts_what_is_not_a_clean_attempt(self):
        rows = self.rows([4, 2, 0])
        rows[0]["status"] = "invalid"
        rows[1]["tainted_by_deckout"] = True
        rep = ps.score(self.m, rows, "open", k=4, n_boot=20)
        self.assertEqual(rep["integrity"], {"attempts": 12, "capped": 1, "tainted": 1})
        self.assertIn("integrity", ps.render(rep))

    def test_attempts_from_two_harness_epochs_are_never_pooled(self):
        rows = self.rows([4, 2, 0], harness_epoch="harness-epoch-69")
        ps.score(self.m, rows, "open", k=4, n_boot=20)
        rows[0]["harness_epoch"] = "harness-epoch-68"
        with self.assertRaisesRegex(ps.Refused, "harness epochs"):
            ps.score(self.m, rows, "open", k=4, n_boot=20)
        del rows[0]["harness_epoch"]
        with self.assertRaisesRegex(ps.Refused, "harness epochs"):  # an unstamped row cannot be told apart
            ps.score(self.m, rows, "open", k=4, n_boot=20)
        ps.score(self.m, self.rows([4, 2, 0]), "open", k=4, n_boot=20)  # none stamped: older runs still score

    def test_attempts_from_two_decisions_bridge_versions_are_never_pooled(self):
        # A jev: solver's questions are versioned like the interface (DecisionsBridge.VERSION).
        rows = self.rows([4, 2, 0], decisions_bridge_version="decisions-bridge-v1")
        rep = ps.score(self.m, rows, "open", k=4, n_boot=20)
        self.assertEqual(rep["decisions_bridge_version"], "decisions-bridge-v1")
        rows[0]["decisions_bridge_version"] = "decisions-bridge-v2"
        with self.assertRaisesRegex(ps.Refused, "decisions bridge versions"):
            ps.score(self.m, rows, "open", k=4, n_boot=20)
        self.assertIsNone(ps.score(self.m, self.rows([4, 2, 0]), "open", k=4, n_boot=20)["decisions_bridge_version"])

    def test_attempts_from_two_claude_cli_bridges_or_efforts_are_never_pooled(self):
        # A claude-cli: solver's bridge (ClaudeCliBridge.VERSION) and its effort are conditions of the solver.
        for key, a, b in (("claude_cli_bridge_version", "claude-cli-bridge-v1", "claude-cli-bridge-v2"),
                          ("claude_cli_effort", "high", "cli-default"),
                          ("claude_cli_thinking_tokens", "16000", "cli-default")):
            rows = self.rows([4, 2, 0], **{key: a})
            self.assertEqual(ps.score(self.m, rows, "open", k=4, n_boot=20)[key], a)
            rows[0][key] = b
            with self.assertRaisesRegex(ps.Refused, "claude-cli"):
                ps.score(self.m, rows, "open", k=4, n_boot=20)

    def test_attempts_under_two_routing_filters_are_never_pooled(self):
        # pr-review of #327: a precision list or price ceiling is what the model ran at, so two directories played
        # under different filters are not one score. The row carries each (absent: none).
        for key, a in (("openrouter_quantizations", '{"m":["bf16","fp8"]}'),
                       ("openrouter_max_price", '{"m":{"prompt":"2","completion":"10"}}')):
            rows = self.rows([4, 2, 0], **{key: a})
            self.assertEqual(ps.score(self.m, rows, "open", k=4, n_boot=20)[key], a)
            del rows[0][key]
            with self.assertRaisesRegex(ps.Refused, "routing filters"):
                ps.score(self.m, rows, "open", k=4, n_boot=20)

    def test_games_under_two_caps_are_never_pooled(self):
        # Owner, 2026-10-26: a run's cap was raised from 30 to 90 minutes. Even a game that ended under 30 is not
        # kept beside 90-minute ones: only slow first draws would be drawn again (pr-review of #329).
        rows = self.rows([4, 2, 0], game_cap_ms=5400000)
        self.assertEqual(ps.score(self.m, rows, "open", k=4, n_boot=20)["game_cap_ms"], 5400000)
        rows[0]["game_cap_ms"] = 1800000
        with self.assertRaisesRegex(ps.Refused, "game caps"):
            ps.score(self.m, rows, "open", k=4, n_boot=20)

    def test_a_row_takes_its_game_cap_from_its_puzzles_manifest(self):
        d = Path(tempfile.mkdtemp())
        (d / "p").mkdir()
        (d / "p" / "puzzle-results.jsonl").write_text(json.dumps({"schema": "pzl-result-v1", "id": "x"}) + "\n")
        self.assertIsNone(ps.load_rows([d])[0]["game_cap_ms"], "no puzzle_solve manifest (a baseline arm): unknown")
        (d / "p" / "batch-manifest.json").write_text(json.dumps({"record": {"game_cap_ms": 5400000}}))
        self.assertEqual(ps.load_rows([d])[0]["game_cap_ms"], 5400000)
        (d / "p" / "batch-manifest.json").write_text(json.dumps({"condition": {}}))   # from before the record
        self.assertEqual(ps.load_rows([d])[0]["game_cap_ms"], ps.LEGACY_GAME_CAP_MS)

    def test_a_development_lane_run_is_labelled_so_and_never_called_a_score(self):
        rows = self.rows([4, 2, 0], dev_lane="claude-cli")
        rep = ps.score(self.m, rows, "open", k=4, n_boot=20)
        self.assertEqual(rep["dev_lane"], "claude-cli")
        self.assertIn("DEVELOPMENT LANE", ps.render(rep).splitlines()[0])
        self.assertFalse(any(l.startswith("PUZ") or "headline" in l for l in ps.render(rep).splitlines()), ps.render(rep))
        self.assertNotIn("DEVELOPMENT LANE", ps.render(ps.score(self.m, self.rows([4, 2, 0]), "open", k=4, n_boot=20)))

    def test_no_spread_gives_no_smallest_difference(self):
        # pr-review of #282: three puzzles all solved 4/4 printed "detect: 0.000", the opposite of what they show.
        a = ps.score(self.m, self.rows([4, 4, 4]), "open", k=4, n_boot=20)["aggregate"]
        self.assertIsNone(a["pass@1"]["mde80"])
        self.assertIn("not estimable (no spread)", ps.render(ps.score(self.m, self.rows([4, 4, 4]), "open", k=4,
                                                                         n_boot=20)))

    def test_the_report_names_its_epoch_and_a_cross_epoch_pair_says_so(self):
        # pr-review of #282: neither the score nor a paired difference said which interface it measured.
        rep = ps.score(self.m, self.rows([4, 2, 0], harness_epoch="harness-epoch-69"), "open", k=4, n_boot=20)
        self.assertEqual(rep["harness_epoch"], "harness-epoch-69")
        self.assertIn("epoch=harness-epoch-69", ps.render(rep))
        old = ps.score(self.m, self.rows([4, 4, 0]), "open", k=4, n_boot=20)
        self.assertIsNone(old["harness_epoch"])
        c = ps.paired(old, rep, n_boot=50)
        self.assertEqual(c["epochs"], [None, "harness-epoch-69"])
        self.assertTrue(c["cross_epoch"])
        self.assertFalse(ps.paired(rep, rep, n_boot=50)["cross_epoch"])

    def test_the_score_by_v2_tier(self):
        s = ps.score(self.m, self.rows([4, 2, 0]), "open", k=4, n_boot=20)["aggregate"]["solved"]
        self.assertEqual({t: b["solved"] for t, b in s["by_tier_v2"].items()}, {"1": 1, "2": 1, "3": 0})


if __name__ == "__main__":
    unittest.main()
