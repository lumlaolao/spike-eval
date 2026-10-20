"""puzzle_score.py's paired comparison: two solvers, or two conditions, on the same puzzles of one frozen suite."""
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
        a, b = self.rep([4, 4, 4]), self.rep([4, 2, 4])     # one condition loses 0.5 on one puzzle
        c, d = self.rep([4, 4, 4]), self.rep([4, 4, 2])     # the other on another
        dd = ps.paired(a, b, base=(c, d), n_boot=200)
        self.assertAlmostEqual(dd["mean"], ((0 - 0) + (-0.5 - 0) + (0 + 0.5)) / 3)

    def test_runs_on_other_puzzles_or_attempts_are_not_paired(self):
        with self.assertRaises(ps.Refused):
            ps.paired(self.rep([4, 2, 0]), self.rep([4, 2], recs=self.recs[:2]))
        a = self.rep([4, 2, 0])
        with self.assertRaises(ps.Refused):
            ps.paired(a, dict(a, aggregate=dict(a["aggregate"], k=5)))

    def test_the_command_prints_the_paired_line(self):
        out = Path(self.tmp.name) / "a"
        out.mkdir()
        recs = [dict(r, pzl=str(Path(self.tmp.name) / r["pzl"])) for r in self.recs]  # the suite check reads the files
        self.m = suite(recs)
        rows = []
        for r in recs:
            rows += [result(r, s, "solved") for s in range(4)]
        (out / "puzzle-results.jsonl").write_text("".join(__import__("json").dumps(x) + "\n" for x in rows))
        sfile = Path(self.tmp.name) / "s.json"
        sfile.write_text(__import__("json").dumps(self.m))
        self.assertEqual(ps.main(["--suite", str(sfile), "--split", "open", "--k", "4", str(out),
                                  "--paired-with", str(out)]), 0)


if __name__ == "__main__":
    unittest.main()
