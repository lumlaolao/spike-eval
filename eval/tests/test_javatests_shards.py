"""javatests_shards.py: acceptance's Java test classes dealt into shards, every class exactly once."""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import javatests_shards as js  # noqa: E402

CLASSES = ["forge.spike.%s" % n for n in ("ATest", "BTest", "CTest", "DTest", "ETest")]


class JavatestsShardsTest(unittest.TestCase):
    def test_every_class_is_in_exactly_one_shard_heaviest_first_to_the_lightest(self):
        secs = {"ATest": 10, "BTest": 6, "CTest": 5}
        shards = js.deal(2, CLASSES, secs)
        self.assertEqual(sorted(c for s in shards for c in s), sorted(CLASSES))
        # A (10) to the first; B (6) and C (5) to the second (11); D and E (1 s each, unlisted) to the lighter first.
        self.assertEqual(shards, [["forge.spike.ATest", "forge.spike.DTest", "forge.spike.ETest"],
                                  ["forge.spike.BTest", "forge.spike.CTest"]])
        self.assertEqual(js.deal(2, CLASSES, secs), shards, "the same inputs, the same deal")

    def test_more_shards_than_classes_leaves_no_empty_shard(self):
        self.assertEqual(js.deal(4, CLASSES[:2], {}), [["forge.spike.ATest"], ["forge.spike.BTest"]])

    def test_the_command_line_reads_the_seconds_file(self):
        with tempfile.TemporaryDirectory() as d:
            tsv = os.path.join(d, "s.tsv")
            with open(tsv, "w") as f:
                f.write("# a comment\nETest\t30\n")
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(js.main(["2", ",".join(CLASSES), tsv]), 0)
        self.assertEqual(out.getvalue().split(), ["forge.spike.ETest",
                                                  "forge.spike.ATest,forge.spike.BTest,forge.spike.CTest,forge.spike.DTest"])
        with redirect_stderr(io.StringIO()):
            self.assertEqual(js.main(["0", "x"]), 2)

    def test_the_committed_table_names_only_existing_test_classes(self):
        names = {f[:-5] for f in os.listdir(evalpath.REPO / "forge-overlay/forge-gui-desktop/src/test/java/forge/spike")
                 if f.endswith("Test.java")}
        table = js.weights(str(evalpath.REPO / "eval" / "javatests_seconds.tsv"))
        self.assertTrue(table)
        self.assertEqual(sorted(set(table) - names), [])


if __name__ == "__main__":
    unittest.main()
