#!/usr/bin/env python3
"""The rules-pilot driver's pure decisions: the five gate criteria and record assembly.

The gates drive engine runs (the Build class), so the tests here pin what the run does NOT
decide: gate_failures' named failures (each test names the tweak that flips it red) and
cmd_build's manifest assembly over a fixture pilot directory.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rules_pilot as rp  # noqa: E402

MINIMAL_PZL = """[metadata]
Name=Fixture
Goal=Win
Turns=10
[state]
turn=1
activeplayer=p0
activephase=Main1
"""

MINIMAL_SOURCE = {
    "question_id": "ask-a-magic-judge-0000",
    "question": "Q?",
    "answer": "A.",
    "rule": "The rule tested.",
    "areas": ["examples"],
}


def a_proven_probe(bound=0.05):
    return {
        "proof": {"status": "proven", "acceptance": "accepted", "unique": "yes"},
        "baselines": {"random": {"rate": 0.0, "n": 200, "solved": 0}},
        "probes": [{"probe_line": "0:1:2", "acceptance": "rejected"}],
    }


class GateFailures(unittest.TestCase):
    def test_a_through_puzzle_has_no_failures(self):
        self.assertEqual(rp.gate_failures(a_proven_probe()), [])

    def test_each_criterion_names_its_failure(self):
        rec = a_proven_probe()
        rec["proof"]["status"] = "not_found"
        self.assertIn("proof is not_found, not proven", rp.gate_failures(rec))

        rec = a_proven_probe()
        rec["proof"]["acceptance"] = "rejected"
        self.assertIn("acceptance is rejected, not accepted", rp.gate_failures(rec))

        rec = a_proven_probe()
        rec["proof"]["unique"] = "no"
        self.assertIn("uniqueness is no", rp.gate_failures(rec)[0])

        rec = a_proven_probe()
        rec["baselines"]["random"]["rate"] = 0.06
        self.assertIn("random arm solves 0.060 (>= 0.05)", rp.gate_failures(rec))

        rec = a_proven_probe()
        rec["probes"][0]["acceptance"] = "accepted"
        self.assertIn("probe 0 was accepted, not rejected", rp.gate_failures(rec))

        rec = a_proven_probe()
        rec["probes"] = []
        self.assertIn("no misreading probe recorded yet", rp.gate_failures(rec))

        rec = a_proven_probe()
        del rec["proof"]
        self.assertEqual(len(rp.gate_failures(rec)), 3)

    def test_the_bound_is_a_parameter(self):
        rec = a_proven_probe()
        rec["baselines"]["random"]["rate"] = 0.06
        self.assertEqual(rp.gate_failures(rec, bound=0.20), [])

    def test_error_is_not_accepted_and_not_a_free_solve(self):
        rec = a_proven_probe()
        rec["proof"]["acceptance"] = "error"
        self.assertIn("acceptance is error, not accepted", rp.gate_failures(rec))


class BuildAssembles(unittest.TestCase):
    def setUp(self):
        # The fixture lives under REPO (git-ignored runs/) because the record stores the .pzl
        # relative to the repository, exactly as a real pilot puzzle is stored.
        self.dir = Path(tempfile.mkdtemp(prefix="rules-pilot-test-", dir=rp.REPO / "runs"))
        self.out = self.dir / "manifest.json"
        self.pilot = self.dir / "pilot"
        self.pilot.mkdir()
        self.keep = rp.PILOT_DIR
        rp.PILOT_DIR = self.pilot
        (self.pilot / "fixture.pzl").write_text(MINIMAL_PZL, encoding="utf-8")
        (self.pilot / "fixture.source.json").write_text(json.dumps(MINIMAL_SOURCE), encoding="utf-8")

    def tearDown(self):
        rp.PILOT_DIR = self.keep

    def test_a_pzl_and_its_source_become_one_record(self):
        self.assertEqual(rp.main(["build", "--out", str(self.out)]), 0)
        m = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(len(m["puzzles"]), 1)
        rec = m["puzzles"][0]
        self.assertEqual(rec["id"], "rules-fixture")
        self.assertEqual(rec["source"]["kind"], "authored")
        self.assertEqual(rec["source"]["question_id"], "ask-a-magic-judge-0000")
        self.assertEqual(rec["status"], "candidate")
        self.assertEqual(rec["goal"]["engine_goal"], "Win")
        self.assertEqual(rec["pzl_sha256"], __import__("puzzle_format").sha256_file(
            self.pilot / "fixture.pzl"))

    def test_a_pzl_without_a_source_note_fails_loudly(self):
        (self.pilot / "fixture.source.json").unlink()
        with self.assertRaises((SystemExit, OSError)):
            rp.main(["build", "--out", str(self.out)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
