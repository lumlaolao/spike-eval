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
    r = {"t": "decision", "seat": seat, "type": "play", "proposals": list(ps)}
    if not any(p["verdict"] == "accepted" for p in ps):
        r.update(answer_via="lost", answer_invalid=len(ps))   # as AnswerTool records a loss: one invalid reply a try
    return r


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
        # Strict: the format-error-first decision counts as not legal on its first try (cast format, then legal).
        self.assertEqual(s["first_try_legal_nonpass_strict"], 0.0)
        lo, hi = s["first_try_legal_nonpass_ci"]
        self.assertTrue(0 < lo < 1 / 3 < hi < 1, s["first_try_legal_nonpass_ci"])
        self.assertEqual(s["lost_other"], 0)

    def test_a_decision_lost_after_one_refusal_is_not_lost_to_refusals(self):
        s = pa.summarize([row(prop("cast", "illegal", "timing"))])
        self.assertEqual((s["lost"], s["lost_other"]), (0, 1))

    def test_tries_equal_the_java_constant(self):
        java = Path(__file__).resolve().parents[2] / "forge-overlay/forge-gui-desktop/src/main/java/forge/spike/AnswerTool.java"
        import re
        m = re.search(r"MAX_INVALID_PROPOSALS\s*=\s*(\d+)", java.read_text(encoding="utf-8"))
        self.assertIsNotNone(m)
        self.assertEqual(int(m.group(1)), pa.TRIES)

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


    def test_combat_proposals_are_their_own_sheet(self):
        # The attack and the blocks are proposed under the same arm (answer-tool-v16): counted per kind, never pooled
        # with the play headline, and an empty declaration is a combat proposal's pass.
        def combat(kind, field, *ps):
            r = {"t": "decision", "seat": "Seat", "type": kind, "proposals": [{field: d, "verdict": v, **({"category": c} if c else {})}
                                                                             for d, v, c in ps]}
            if not any(v == "accepted" for _, v, _ in ps):
                r.update(answer_via="lost", answer_invalid=len(ps))
            return r
        atk = [{"attacker": "@3"}]
        blk = [{"blocker": "@4", "attacker": "@9"}]
        rs = [
            row(prop("cast", "accepted")),
            combat("attackers", "declare_attackers", (atk, "illegal", "summoning sickness"), (atk[:0], "accepted", None)),
            combat("attackers", "declare_attackers", (atk, "accepted", None)),
            combat("attackers", "declare_attackers", ([], "accepted", None)),
            combat("blockers", "declare_blockers", *[(blk, "illegal", "evasion")] * 4),
        ]
        with tempfile.TemporaryDirectory() as d:
            with open(Path(d) / "game-1-0.jsonl", "w") as f:
                for r in rs:
                    f.write(json.dumps(r) + "\n")
            sheet = pa.audit([d])
        s = sheet["Seat"]
        self.assertEqual(s["decisions"], 1, "the play sheet holds only play rows")
        self.assertEqual(s["first_try_legal_nonpass"], 1.0)
        a, b = s["attack"], s["block"]
        self.assertEqual(a["decisions"], 3)
        self.assertEqual(a["first_try_legal_nonpass"], 0.5, "of the two non-empty first attacks, one was legal")
        self.assertEqual(a["pass_first_share"], round(1 / 3, 4), "the empty attack is the combat pass")
        self.assertEqual(a["categories"], {"summoning sickness": 1})
        self.assertEqual((b["decisions"], b["lost"]), (1, 1))
        self.assertEqual(b["categories"], {"evasion": 4})
        text = pa.render(sheet)
        self.assertIn("attack: 3 decisions", text)
        self.assertIn("block: 1 decisions", text)
        self.assertTrue(pa.lost_to_refusals(rs[-1]), "a block set lost to four refusals is the model's error")


if __name__ == "__main__":
    unittest.main()
