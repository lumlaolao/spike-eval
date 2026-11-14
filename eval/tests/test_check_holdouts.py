"""check_holdouts.py: which eval decks appear in which training corpus, and a held-out deck outside its grandfathered
datasets is a violation (exit 1). build_report.py reads its --json and its exit status (0 clean, 1 a violation,
anything else an audit that did not run)."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import module_tmp  # noqa: E402
import check_holdouts as ch  # noqa: E402


def setUpModule():
    module_tmp.isolate(__name__)


class ParseMatchupTest(unittest.TestCase):
    def test_dashed_slugs_split_on_the_longest_known_deck(self):
        slugs = {"ur-delver", "ur", "mono-red-burn", "burn"}
        self.assertEqual(ch.parse_matchup("ur-delver__vs__mono-red-burn", slugs), ("ur-delver", "mono-red-burn"))
        self.assertEqual(ch.parse_matchup("ur-delver-vs-burn", slugs), ("ur-delver", "burn"))
        self.assertIsNone(ch.parse_matchup("ur-delver-vs-unknown", slugs))
        self.assertIsNone(ch.parse_matchup("stage0", slugs))
        self.assertIsNone(ch.parse_matchup(None, slugs))
        self.assertEqual(ch.slugify("UR Delver!"), "ur-delver")


class AuditTest(unittest.TestCase):
    """A scratch repository: a registry of two decks (one held out), deck files, and datasets' meta.jsonl rows."""

    def run_audit(self, rows_by_dataset, grandfathered=(), registry=None, deck_files=True, *argv):
        root = Path(tempfile.mkdtemp())
        decks = {"elves": {"file": "eval/decks/elves.txt", "status": "in-corpus"},
                 "ur-delver": {"file": "eval/decks/ur-delver.txt", "status": "held-out",
                               "grandfathered": list(grandfathered)}}
        (root / "eval/decks").mkdir(parents=True)
        if deck_files:
            for d in decks.values():
                (root / d["file"]).write_text("4 Forest\n")
        reg = root / "eval/holdouts.json"
        reg.write_text(registry if registry is not None else json.dumps({"version": 1, "decks": decks}))
        for name, rows in rows_by_dataset.items():
            ds = root / "datasets" / name
            ds.mkdir(parents=True)
            if rows is not None:
                (ds / "meta.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows) + "not json\n")
        (root / "datasets").mkdir(exist_ok=True)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ch, "REPO", root), mock.patch.object(ch, "REGISTRY", reg), \
                mock.patch.object(ch, "DATASETS", root / "datasets"), mock.patch.object(sys, "argv", ["x"] + list(argv)), \
                redirect_stdout(out), redirect_stderr(err):
            rc = ch.main()
        return rc, out.getvalue(), err.getvalue()

    def test_a_held_out_deck_in_a_training_set_is_a_violation(self):
        rows = {"sft-v1": [{"deck": "UR Delver", "split": "train", "source_subdir": "ur-delver__vs__elves"},
                           {"deck": "Elves", "split": "train"}, {"seat": "Elves", "split": "val"}]}
        rc, out, _ = self.run_audit(rows, (), None, True, "--json")
        self.assertEqual(rc, 1)
        rep = json.loads(out)
        self.assertEqual(rep["violations"], [["ur-delver", "sft-v1"]])
        self.assertEqual(rep["decks"]["elves"]["datasets"], {"sft-v1": {"train": 1, "val": 1}})
        self.assertEqual(rep["matchups"], {"elves vs ur-delver": {"sft-v1": 1}})
        rc, out, _ = self.run_audit(rows)
        self.assertEqual(rc, 1)
        self.assertIn("VIOLATION: held-out deck 'ur-delver' appears in non-grandfathered dataset 'sft-v1'", out)
        self.assertIn("RESULT: FAIL", out)

    def test_a_grandfathered_dataset_is_noted_not_failed(self):
        rc, out, _ = self.run_audit({"stage1-v0": [{"deck": "ur-delver", "split": "train"}]}, ("stage1-v0",))
        self.assertEqual(rc, 0)
        self.assertIn("NOTE: held-out deck 'ur-delver' found in grandfathered dataset 'stage1-v0'", out)
        self.assertIn("RESULT: CLEAN", out)

    def test_unknown_decks_and_datasets_without_meta_are_warned_about(self):
        rc, out, _ = self.run_audit({"a": [{"deck": "Mystery Brew"}], "b": None}, (), None, True, "--json")
        rep = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(rep["unknown_deck_values"], {"Mystery Brew": ["a"]})
        self.assertEqual(rep["datasets_without_meta"], ["b"])

    def test_a_broken_registry_or_a_missing_deck_file_is_a_usage_error_not_a_verdict(self):
        rc, _, err = self.run_audit({}, (), "{not json")
        self.assertEqual(rc, 2)
        self.assertIn("cannot load", err)
        rc, _, err = self.run_audit({}, (), None, False)
        self.assertEqual(rc, 2)
        self.assertIn("file missing: eval/decks/", err)

    def test_the_committed_registry_and_datasets_are_clean(self):
        # The audit build_report.py runs, on this repository as it is: every deck file exists, no new leak.
        with redirect_stdout(io.StringIO()) as out, mock.patch.object(sys, "argv", ["x", "--json"]):
            rc = ch.main()
        self.assertEqual(rc, 0, json.loads(out.getvalue())["violations"])


if __name__ == "__main__":
    unittest.main()
