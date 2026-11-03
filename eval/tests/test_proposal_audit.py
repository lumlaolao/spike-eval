#!/usr/bin/env python3
"""Tests for proposal_audit.py. Run: python3 eval/tests/test_proposal_audit.py"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401
import proposal_audit as pa  # noqa: E402


def prop(verb, verdict, category=None):
    p = {"action": {"verb": verb, "card": "@3"}, "verdict": verdict}
    if category:
        p["category"] = category
    return p


def row(*ps, seat="Seat"):
    return {"t": "decision", "seat": seat, "type": "play", "proposals": list(ps)}


class TestProposalAudit(unittest.TestCase):
    def test_the_headline_excludes_passes_and_format_errors(self):
        rs = [
            row(prop("cast", "illegal", "timing"), prop("cast", "accepted")),   # repaired on try 2
            row(prop("cast", "format"), prop("play_land", "accepted")),         # format error, then legal
            row(prop("pass", "accepted")),                                      # a pass: legal, not in the headline
            row(*[prop("activate", "illegal", "cost")] * 4),                    # lost
        ]
        s = pa.summarize(rs)
        self.assertEqual(s["decisions"], 4)
        self.assertEqual(s["first_try_legal_nonpass"], round(1 / 3, 4))
        self.assertEqual(s["first_try_legal"], 0.5)
        self.assertEqual(s["pass_first_share"], 0.25)
        self.assertEqual(s["lost"], 1)
        self.assertEqual(s["format_errors"], 1)
        self.assertEqual(s["legal_within"], {"1": 0.25, "2": 0.75, "3": 0.75, "4": 0.75})
        self.assertEqual(s["categories"], {"cost": 4, "timing": 1})

    def test_a_seat_that_only_passes_has_no_headline(self):
        s = pa.summarize([row(prop("pass", "accepted"))] * 5)
        self.assertEqual(s["first_try_legal"], 1.0)
        self.assertIsNone(s["first_try_legal_nonpass"], "passing must not score as rules knowledge")

    def test_reads_a_run_directory_and_skips_menu_rows(self):
        with tempfile.TemporaryDirectory() as d:
            with open(Path(d) / "game-1-0.jsonl", "w") as f:
                f.write(json.dumps({"t": "decision", "seat": "Seat", "type": "play", "choice": 1}) + "\n")
                f.write(json.dumps(row(prop("cast", "accepted"))) + "\n")
                f.write("not json\n")
            sheet = pa.audit([d])
        self.assertEqual(sheet["Seat"]["decisions"], 1)
        self.assertIn("HEADLINE", pa.render(sheet))
        self.assertIn("no proposal rows", pa.render({}))


if __name__ == "__main__":
    unittest.main()
