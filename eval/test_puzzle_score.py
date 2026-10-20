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
        out.mkdir()
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


if __name__ == "__main__":
    unittest.main()
