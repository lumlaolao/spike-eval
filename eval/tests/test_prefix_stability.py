"""prefix_stability.py: the cache order's record is a prefix of the next message, and the checker says when not."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import prefix_stability as ps  # noqa: E402

HEAD = ps.RECORD_HEAD + "...):\n"


def game(tmp, prompts, rounds=None):
    """A game file of first-round context_call rows for one seat (and `rounds` extra later-round rows)."""
    path = os.path.join(tmp, "game-1-1.jsonl")
    with open(path, "w") as f:
        call = 0
        for p in prompts:
            call += 1
            f.write(json.dumps({"t": "context_call", "seat": "Seat", "call": call, "round": 0, "prompt": p}) + "\n")
        for _ in range(rounds or 0):
            call += 1
            f.write(json.dumps({"t": "context_call", "seat": "Seat", "call": call, "round": 1, "prompt": "x"}) + "\n")
    return tmp


class PrefixStabilityTest(unittest.TestCase):
    def test_a_record_that_only_grows_passes_and_its_share_is_reported(self):
        rec1 = HEAD + "  T1 [E1-E4]: a\n"
        rec2 = rec1 + "  T2 [E5-E9]: b\n"
        msgs = [rec1 + "<archive>\nM1\n</archive>\n\n== Turn 2 ==\nstate one\n",
                rec2 + "<archive>\nM2\n</archive>\n\n== Turn 3 ==\nstate two\n"]
        with tempfile.TemporaryDirectory() as tmp:
            seats = ps.measure(game(tmp, msgs, rounds=1))
        self.assertEqual(ps.problems(seats), [])
        s = seats[0]
        self.assertEqual((s["decisions"], s["rounds_per_decision"], s["record_breaks"]), (2, 1.5, []))
        self.assertAlmostEqual(s["median_record_share"], len(rec2) / len(msgs[1]), places=3)
        self.assertTrue(s["cache_ordered"])

    def test_a_record_rewritten_between_decisions_is_a_problem(self):
        # A turn line whose count grew ("x2" to "x3") rewrites the record: the cache misses from there.
        msgs = [HEAD + "  T1 [E1-E4]: a x2\n== Turn 2 ==\n", HEAD + "  T1 [E1-E5]: a x3\n== Turn 2 ==\n"]
        with tempfile.TemporaryDirectory() as tmp:
            seats = ps.measure(game(tmp, msgs))
        self.assertEqual(len(seats[0]["record_breaks"]), 1)
        self.assertIn("not a prefix", ps.problems(seats)[0])

    def test_a_cache_ordered_message_with_a_reveals_block_is_a_problem(self):
        msgs = [HEAD + "  T1 [E1-E4]: a\n== Turn 2 ==\nREVEALS TO YOU (Forge game log ...):\n  - x\n"]
        with tempfile.TemporaryDirectory() as tmp:
            seats = ps.measure(game(tmp, msgs))
        self.assertIn("REVEALS TO YOU", ps.problems(seats)[0])

    def test_the_legacy_order_has_no_record_and_a_floor_fails_it(self):
        msgs = ["<archive>\nM1\n</archive>\n\n== Turn 1 ==\nGAME HISTORY ...\n",
                "<archive>\nM2\n</archive>\n\n== Turn 1 ==\nGAME HISTORY ...\n"]
        with tempfile.TemporaryDirectory() as tmp:
            seats = ps.measure(game(tmp, msgs))
            self.assertEqual(ps.problems(seats), [], "no record, nothing to break")
            self.assertFalse(seats[0]["cache_ordered"])
            self.assertEqual(seats[0]["median_record_share"], 0.0)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(ps.main([tmp, "--min-record-share", "0.3"]), 1)

    def test_a_directory_with_no_model_calls_fails(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            from contextlib import redirect_stderr
            with redirect_stderr(io.StringIO()):
                self.assertEqual(ps.main([tmp]), 1)


class WholeRequestTest(unittest.TestCase):
    def test_the_whole_request_is_measured_per_bridge_and_a_cache_reads_its_shared_prefix(self):
        system = {"role": "system", "content": "S" * 100}
        bodies = [{"tools": [{"n": 1}], "messages": [system, {"role": "user", "content": u}]}
                  for u in ("rec1|state a", "rec1|state b", "rec1rec2|state c")]
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "transcripts"))
            with open(os.path.join(tmp, "transcripts", "http-1-1.jsonl"), "w") as f:
                for i, b in enumerate(bodies):
                    f.write(json.dumps({"t": "http", "phase": "sent", "seq": i + 1, "label": "Seat/smart",
                                        "request_body": json.dumps(b)}) + "\n")
                    f.write(json.dumps({"t": "http", "phase": "response", "seq": i + 1, "label": "Seat/smart"}) + "\n")
                f.write(json.dumps({"t": "http", "phase": "sent", "seq": 9, "label": "Other/smart",
                                    "request_body": json.dumps(bodies[0])}) + "\n")
            (w, other) = sorted(ps.measure_requests(tmp), key=lambda x: x["label"], reverse=True)
        texts = [ps.request_text(b) for b in bodies]
        shared = [ps.common_prefix(texts[0], texts[1]), ps.common_prefix(texts[1], texts[2])]
        self.assertEqual((w["label"], w["requests"], other["requests"]), ("Seat/smart", 3, 1))
        self.assertEqual(w["read_chars"], sum(shared))
        self.assertEqual(w["written_chars"], len(texts[0]) + sum(len(t) for t in texts[1:]) - sum(shared))
        self.assertTrue(texts[0].startswith('[{"n": 1}]'), "the tools come first, as the provider reads them")

    def test_thirds_are_the_medians_of_the_start_middle_and_end(self):
        self.assertEqual(ps.thirds([0.1, 0.2, 0.3, 0.5, 0.6, 0.7]), [0.15, 0.4, 0.65])
        self.assertEqual(ps.thirds([0.5]), [0.5, 0.5, 0.5])
        self.assertEqual(ps.thirds([]), [None, None, None])


if __name__ == "__main__":
    unittest.main(verbosity=2)
