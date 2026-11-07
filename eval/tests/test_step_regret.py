"""step_regret.py: the positions an attempt is searched at, the backward scan, the regret step and its floor."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import step_regret as sr  # noqa: E402


def row(tier, typ="play", choice=0, options=("A", "B", "PASS (do nothing)"), **kw):
    r = {"t": "decision", "seat": "Solver", "type": typ, "tier": tier, "choice": choice, "options": list(options),
         "n_options": len(options)}
    r.update(kw)
    return r


def attempt(dir_, rows, status="failed", seed=1):
    d = Path(dir_)
    (d / ("game-%d-1.jsonl" % seed)).write_text("".join(json.dumps(r) + "\n" for r in rows))
    (d / "puzzle-results.jsonl").write_text(json.dumps({"seed": seed, "status": status, "id": "x"}) + "\n")
    return d / ("game-%d-1.jsonl" % seed)


class FakeSearch:
    """W(K) from a table: verdicts by K, options at a K by forced value."""

    def __init__(self, by_k, options=None, bound=3, model_value=0, refused=None):
        self.by_k, self.options, self.bound, self.model_value, self.refused = by_k, options or {}, bound, model_value, refused
        self.calls = []
        self.deeper = {}

    def __call__(self, k, force=None, budget=None):
        self.calls.append((k, force) if budget is None else (k, force, budget))
        if force is None:
            return [{"k": k, "verdict": (self.deeper if budget else self.by_k)[k]}]
        if self.refused:
            return [{"k": k, "bound": self.bound, "refused": self.refused}]
        return [{"k": k, "forced": o, "bound": self.bound, "model_value": self.model_value, "verdict": v}
                for o, v in sorted(self.options.items())]


class StepRegretTest(unittest.TestCase):
    ROWS = [row("smart"), row("auto", "splice", -1, ()), row("smart", "target", 1, ("you", "opponent")),
            row("smart"), row("smart", choice=-1)]

    def test_positions_are_before_each_model_decision_and_the_end(self):
        pos = sr.positions(self.ROWS)
        self.assertEqual([(i, k) for i, k, _ in pos], [(0, 0), (1, 2), (2, 3), (3, 4), (4, 5)])
        self.assertIsNone(pos[-1][2])
        self.assertEqual(pos[1][2]["type"], "target")

    def test_the_scan_stops_at_the_first_proven_position_from_the_end(self):
        pos = sr.positions(self.ROWS)
        fake = FakeSearch({5: "unwinnable", 4: "unwinnable", 3: "proven", 2: "proven", 0: "proven"})
        res = sr.backward_scan(pos, lambda k: fake(k)[0])
        self.assertEqual(sorted(res), [2, 3, 4], "searched from the end, stopped at the proven one")
        reg = sr.regret(pos, res)
        self.assertEqual((reg["status"], reg["regret_index"], reg["regret_k"]), ("exact", 2, 3))

    def test_two_jobs_search_two_positions_at_once_and_the_last_proven_wins(self):
        pos = sr.positions(self.ROWS)
        fake = FakeSearch({5: "unwinnable", 4: "not_found", 3: "proven", 2: "proven"})
        res = sr.backward_scan(pos, lambda k: fake(k)[0], jobs=2)
        self.assertEqual(sorted(res), [1, 2, 3, 4])
        reg = sr.regret(pos, res)
        # The position after decision 2 was not found, the one after it unwinnable: the regret step is bracketed.
        self.assertEqual(reg["status"], "bracket")
        self.assertEqual(reg["regret_bracket"], [3, 4])
        self.assertEqual((reg["last_proven_k"], reg["first_unwinnable_k"]), (3, 5))

    def test_never_proven_won_at_end_drift_and_undetermined(self):
        pos = sr.positions(self.ROWS)
        self.assertEqual(sr.regret(pos, {j: {"verdict": "not_found"} for j in range(5)})["status"], "never_proven")
        never = sr.regret(pos, {4: {"verdict": "unwinnable"}, 3: {"verdict": "unwinnable"}, 2: {"verdict": "not_found"}})
        self.assertEqual((never["status"], never["last_proven_k"], never["first_unwinnable_k"]), ("never_proven", None, 4))
        self.assertEqual(sr.regret(pos, {4: {"verdict": "proven"}})["status"], "won_at_end")
        self.assertEqual(sr.regret(pos, {4: {"verdict": "drift"}})["status"], "drift")
        self.assertEqual(sr.regret(pos, {4: {"verdict": "not_found"}, 3: {"verdict": "proven"}})["status"], "undetermined")

    def test_the_floor_is_exact_only_when_every_option_is_decided(self):
        f = sr.floor([{"verdict": "proven"}, {"verdict": "unwinnable"}, {"verdict": "unwinnable"}], 3)
        self.assertAlmostEqual(f["floor"], 1 / 3)
        self.assertEqual(f["undetermined_options"], 0)
        f = sr.floor([{"verdict": "proven"}, {"verdict": "not_found"}, {"verdict": "unwinnable"}], 3)
        self.assertIsNone(f["floor"])
        self.assertEqual(f["floor_bracket"], [1 / 3, 2 / 3])

    def test_an_attempt_reports_its_regret_decision_and_floor(self):
        with tempfile.TemporaryDirectory() as d:
            log = attempt(d, self.ROWS)
            fake = FakeSearch({5: "unwinnable", 4: "unwinnable", 3: "unwinnable", 2: "proven"},
                              options={0: "unwinnable", 1: "proven"}, bound=2, model_value=0)
            out = []
            s = sr.analyse(log, fake, emit=out.append)
            self.assertEqual(s["regret_k"], 2)
            self.assertEqual(s["regret_decision"]["type"], "target")
            self.assertEqual(s["regret_decision"]["model_label"], "you")
            self.assertEqual(s["regret_decision"]["kept_win"], ["opponent"])
            self.assertEqual(s["floor"], 0.5)
            self.assertEqual(s["model_option_verdict"], "unwinnable")
            self.assertIn((2, "all"), fake.calls)
            kinds = [r.get("kind") for r in out if r["t"] == "step_regret"]
            self.assertEqual(kinds.count("position"), 4)
            self.assertEqual(kinds.count("option"), 2)
            self.assertEqual(out[-1]["t"], "step_regret_summary")

    def test_a_refused_floor_and_a_solved_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            log = attempt(d, self.ROWS)
            fake = FakeSearch({5: "unwinnable", 4: "unwinnable", 3: "unwinnable", 2: "proven"}, refused="multi-pick")
            s = sr.analyse(log, fake, emit=lambda r: None)
            self.assertIsNone(s["floor"])
            self.assertEqual(s["floor_refused"], "multi-pick")
        with tempfile.TemporaryDirectory() as d:
            log = attempt(d, self.ROWS, status="solved")
            fake = FakeSearch({})
            s = sr.analyse(log, fake, emit=lambda r: None)
            self.assertEqual((s["status"], s["searches"]), ("solved", 0))
            self.assertEqual(fake.calls, [], "the model's line is its own witness: nothing is searched")

    def test_a_pass_or_decline_is_the_last_value(self):
        r = row("smart", options=("A", "PASS (do nothing)"))
        self.assertEqual(sr.option_label(r, 1, 2), "PASS (do nothing)")
        self.assertEqual(sr.option_label(row("smart", "target", options=("you", "opponent")), 2, 3), "(decline)")


    def test_a_deeper_search_at_the_boundary_turns_a_bracket_into_a_step(self):
        with tempfile.TemporaryDirectory() as d:
            log = attempt(d, self.ROWS)
            fake = FakeSearch({5: "unwinnable", 4: "unwinnable", 3: "not_found", 2: "not_found", 0: "not_found"},
                              options={0: "unwinnable", 1: "proven"}, bound=2)
            fake.deeper = {3: "unwinnable", 2: "proven"}
            s = sr.analyse(log, fake, jobs=1, emit=lambda r: None, refine_budget=3000)
            self.assertEqual(s["status"], "exact")
            self.assertEqual(s["regret_k"], 2)
            self.assertEqual(s["refined_searches"], 2)
            self.assertEqual([c for c in fake.calls if len(c) == 3], [(3, None, 3000), (2, None, 3000)])
            self.assertEqual(s["floor"], 0.5)
            # Without it, the scan names no step.
            fake2 = FakeSearch(dict(fake.by_k))
            self.assertEqual(sr.analyse(log, fake2, emit=lambda r: None)["status"], "never_proven")

    def test_the_summary_counts_each_search_once(self):
        with tempfile.TemporaryDirectory() as d:
            def put(rel, *rows):
                f = Path(d) / rel / "step-search.jsonl"
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text("".join(json.dumps(r) + "\n" for r in rows))
            put("A/game-1-1/k000", {"verdict": "not_found", "budget": 600})
            # A search run twice into one dir (a resumed scan) holds two rows: one search.
            put("A/game-1-1/k008", {"verdict": "not_found", "budget": 600}, {"verdict": "not_found", "budget": 600})
            put("A/game-1-1/k010-b2400", {"verdict": "not_found", "budget": 2400})
            put("A/game-1-1/k011-b2400", {"verdict": "unwinnable", "budget": 2400})
            put("A/game-1-1/k012", {"verdict": "unwinnable", "budget": 600})
            put("B/game-2-1/k004", {"verdict": "proven", "budget": 600})
            # Forced options are one search each (REPLAY_FORCE=all writes them into one dir).
            put("B/game-2-1/k004-fall", {"verdict": "proven", "forced": 0, "budget": 600},
                {"verdict": "unwinnable", "forced": 1, "budget": 600})
            t = sr.summary([Path(d) / "A", Path(d) / "B"])
            self.assertEqual(t["searches"], 8)
            self.assertEqual(t["verdicts"], {"proven": 2, "unwinnable": 3, "not_found": 3, "drift": 0, "error": 0,
                                             "refused": 0})
            self.assertEqual(t["not_found_by_budget"], {600: 2, 2400: 1})
            self.assertEqual(sr.summary_line(t), "searches 8: proven 2, unwinnable 3, not_found 3 (2 at 600, 1 at "
                                                 "2400), drift 0, error 0, refused 0")

    def test_the_summary_reads_a_probe_rows_drift_and_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            def put(rel, *rows):
                f = Path(d) / rel / "step-search.jsonl"
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text("".join(json.dumps(r) + "\n" for r in rows))
            # REPLAY_FORCE writes the probe itself (no verdict) when it drifted or was refused.
            put("g/k001", {"k": 1, "drift": "replay: row 3 differs"})
            put("g/k002", {"k": 2, "refused": "a multi-pick"})
            put("g/k003", {"k": 3})
            t = sr.summary([Path(d)])
            self.assertEqual(t["verdicts"]["drift"], 1)
            self.assertEqual(t["verdicts"]["refused"], 1)
            self.assertEqual(t["verdicts"]["error"], 1)


if __name__ == "__main__":
    unittest.main()
