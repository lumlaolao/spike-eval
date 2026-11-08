"""puzzle_format.py: the puzzle.v1 record's measured difficulty and v2 tier (BENCHMARK-V2.md section 4), and its
baseline arms."""
import sys
import unittest

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import puzzle_format as pf  # noqa: E402


class DifficultyV2Test(unittest.TestCase):
    """BENCHMARK-V2 section 4: the tier is computed from the proof, never typed in."""

    PZL = ("[metadata]\nName:x\nGoal:Win\nTurns:1\n[state]\nturn=1\np0hand=Lava Spike;Fling\n"
           "p0battlefield=Mountain;Thermo-Alchemist\np0library=Island;Island\np1battlefield=Wall of Omens\n"
           "p1graveyard=Shock\n[acceptance]\nlife p1 <= 0\n")

    def test_the_structure_comes_off_the_proof_line(self):
        proof = {"status": "proven", "opponent": "search", "line": "Solver:6:2 Solver:1:0 Opponent:3:1 Solver:4:0 Opponent:1:0",
                 "solver_actions": 3, "opponent_choice_points": 1, "turns": 1}
        d = pf.difficulty(proof, self.PZL)
        self.assertEqual(d, {"L": 3, "D": 2, "O": 1, "T": 1, "B": 24, "board_objects": 6})

    def test_missing_counts_are_read_from_the_line_and_actions(self):
        proof = {"status": "proven", "opponent": "search", "line": "Solver:2:1 Opponent:2:0 Opponent:3:2",
                 "actions": ["STACK_ADD: Solver cast Shock", "COMBAT: Solver assigned X to attack"]}
        d = pf.difficulty(proof, self.PZL)
        self.assertEqual((d["L"], d["O"], d["T"]), (2, 2, None))
        self.assertIsNone(pf.difficulty({"status": "not_found"}, self.PZL), "no proof, no structure")

    def test_each_tier_starts_at_its_threshold(self):
        t = lambda L, O=0, T=1, B=1, ai=1.0: pf.tier_v2({"L": L, "D": L, "O": O, "T": T, "B": B}, ai)  # noqa: E731
        self.assertEqual(t(2), 1)
        self.assertEqual(t(2, ai=0.25), 2, "the AI fails a two-action puzzle: a capability item")
        self.assertIsNone(t(2, ai=None), "T1 needs the AI's rate")
        self.assertEqual(t(4), 2)
        self.assertEqual(t(1, O=1), 2)
        self.assertEqual(t(1, T=2), 2)
        self.assertEqual(t(6), 3)
        self.assertEqual(t(1, O=2), 3)
        self.assertEqual(t(1, B=92160), 1, "B counts search draws, not menus: alone it does not make a T3 (pr-review of "
                         "#280: RP_035, one cast of Animate Dead that Forge's AI always solves, read T3)")
        self.assertIsNone(pf.tier_v2(None, 1.0))
        self.assertIsNone(t(2, T=None), "a proof without its turns is not tiered as one turn (pr-review of #280)")

    def test_turns_are_counted_from_the_start_and_capped_at_the_limit(self):
        # Forge's row holds the absolute turn the game ended on: a one-turn Survive puzzle ends as turn 2 begins, so
        # it read T=2 and could never be T1 (pr-review of #280: 429 of 550 rules puzzles are Survive).
        survive = self.PZL.replace("Goal:Win", "Goal:Survive")
        proof = {"status": "proven", "opponent": "search", "line": "Solver:2:1", "solver_actions": 1, "turns": 2}
        self.assertEqual(pf.difficulty(proof, survive)["T"], 1)
        late = self.PZL.replace("Turns:1", "Turns:3").replace("turn=1", "turn=3")
        self.assertEqual(pf.difficulty(dict(proof, turns=3), late)["T"], 1, "a puzzle that starts on turn 3")
        two = self.PZL.replace("Turns:1", "Turns:2")
        self.assertEqual(pf.difficulty(dict(proof, turns=2), two)["T"], 2)
        # A start in CLEANUP leaves only that step of the first turn: the first real turn is the next one, and Forge's
        # limit moves one turn later with it (Puzzle.addGoalEnforcement). RP_209's header: Survive, Turns:2, turn=1.
        cleanup = two.replace("Goal:Win", "Goal:Survive").replace("turn=1", "turn=1\nactivephase=CLEANUP")
        self.assertEqual(pf.difficulty(dict(proof, turns=2), cleanup)["T"], 1, "won on the first real turn")
        self.assertEqual(pf.difficulty(dict(proof, turns=4), cleanup)["T"], 2, "survived to the limit")

    def test_no_turns_are_counted_without_the_pzl_or_a_real_end_turn(self):
        proof = {"status": "proven", "opponent": "search", "line": "Solver:2:1", "solver_actions": 1, "turns": 3}
        d = pf.difficulty(proof, "")
        self.assertIsNone(d["T"], "an unreadable pzl is not a one-turn puzzle (pr-review of #280)")
        self.assertIsNone(pf.tier_v2(d, 1.0))
        self.assertIsNone(pf.difficulty(dict(proof, turns=-1), self.PZL)["T"], "-1: the row had no main rollout")

    def test_opponent_points_count_only_against_the_search_opponent(self):
        # Against Forge's AI the line has no opponent draws, so O=0 would read as "the opponent cannot respond".
        proof = {"status": "proven", "opponent": "ai", "line": "Solver:2:1", "solver_actions": 1,
                 "opponent_choice_points": 0, "turns": 1}
        d = pf.difficulty(proof, self.PZL)
        self.assertIsNone(d["O"])
        self.assertIsNone(pf.tier_v2(d, 1.0), "not tiered without the adversarial count")

    def test_branching_is_capped(self):
        proof = {"status": "proven", "opponent": "search", "line": " ".join(["Solver:1000:0"] * 6), "solver_actions": 6,
                 "turns": 1}
        self.assertEqual(pf.difficulty(proof, self.PZL)["B"], pf.B_CAP)

    def test_skill_tags_are_mechanical(self):
        d = {"L": 7, "D": 7, "O": 1, "T": 2, "B": 10}
        actions = ["STACK_ADD: Solver cast Dark Ritual", "STACK_ADD: Solver cast Tendrils",
                   "COMBAT: Solver assigned Bears to attack"]
        tags = pf.skill_tags(d, actions, "rules")
        self.assertEqual(tags, ["rules application", "sequencing", "combat arithmetic", "long lines",
                                "adversarial", "multi-turn"])
        self.assertEqual(pf.skill_tags({"L": 0, "D": 0, "O": 0, "T": 1, "B": 1}, [], "forge-builtin"), ["restraint"])
        self.assertTrue(set(tags + ["restraint"]) <= set(pf.SKILL_TAGS), "every emitted tag validates")

    def test_a_record_with_bad_difficulty_is_refused(self):
        good = {"L": 2, "D": 1, "O": 0, "T": 1, "B": 3, "board_objects": 4}
        self.assertEqual(pf.difficulty_problems("x", good, 1), [])
        self.assertTrue(pf.difficulty_problems("x", dict(good, L=-1), 1))
        self.assertTrue(pf.difficulty_problems("x", dict(good, Q=1), 1))
        partial = {k: v for k, v in good.items() if k != "B"}
        self.assertTrue(pf.difficulty_problems("x", partial, 1), "a difficulty missing a key")
        self.assertTrue(pf.difficulty_problems("x", good, 4))
        self.assertTrue(pf.difficulty_problems("x", None, 1), "a tier without its difficulty")
        self.assertTrue(pf.difficulty_problems("x", good, True), "True is not a tier")
        self.assertTrue(pf.difficulty_problems("x", good, 1, skills=["telepathy"]), "an unknown skill tag")
        self.assertEqual(pf.difficulty_problems("x", good, 1, skills=["sequencing"]), [])


class BaselineArmsTest(unittest.TestCase):
    """A record's baselines name known arms; a greedy rate carries the condition it was measured under."""

    RATE = {"solved": 0, "n": 4, "rate": 0.0}

    def problems(self, baselines):
        rec = {"schema": "puzzle.v1", "id": "x", "split": "open", "status": "candidate", "goal": {"engine_goal": "Win", "turns": 1},
               "source": {"kind": "rules"}, "pzl": "eval/puzzles/rules-pilot/RP_001.pzl", "pzl_sha256": "0" * 64,
               "baselines": baselines}
        return [p for p in pf.validate_puzzle(rec, check_files=False) if "baseline" in p]

    def test_an_unknown_arm_is_refused_and_greedy_is_known(self):
        self.assertTrue(any("unknown baseline arm" in p for p in self.problems({"deep": self.RATE})))
        self.assertIn("greedy", pf.BASELINE_ARMS)

    def test_a_greedy_rate_without_its_epoch_is_refused(self):
        self.assertTrue(any("without its condition" in p for p in self.problems({"greedy": self.RATE})))
        ok = dict(self.RATE, condition={"harness_epoch": "harness-epoch-68", "env": {}})
        self.assertEqual(self.problems({"greedy": ok}), [])
        self.assertEqual(self.problems({"pass": self.RATE}), [], "the other arms read no prompt: no condition needed")


def _ids(split, n=2):
    return [i for i in ("q%d" % k for k in range(400)) if pf.split_for(i) == split][:n]


class TwinRecordTest(unittest.TestCase):
    """A twin record names its original and carries the original's split, whatever its own id hashes to."""

    CARDS = {"Zorbin Kel": "a" * 64, "Dravi Ton": "b" * 64}

    def twin(self, orig, tid=None, split=None, **kw):
        tid = tid or orig + "-twin"
        rec = {"schema": "puzzle.v1", "id": tid, "split": split or pf.split_for(orig), "status": "candidate",
               "goal": {"engine_goal": "Win", "turns": 1}, "source": {"kind": "rules"},
               "pzl": "x.pzl", "pzl_sha256": "0" * 64, "twin_of": orig, "twin_cards": dict(self.CARDS),
               "twin_set_sha256": pf.twin_set_sha(self.CARDS)}
        rec.update(kw)
        return rec

    def problems(self, rec):
        return pf.validate_puzzle(rec, check_files=False)

    def test_a_twin_carries_its_originals_split_not_its_own_ids(self):
        # the first (original, twin id) pair whose own hashes put them in different splits
        orig = next(o for o in _ids("heldout", 40) if pf.split_for(o + "-twin") == "open")
        self.assertEqual(pf.record_split(self.twin(orig)), "heldout")
        self.assertEqual(self.problems(self.twin(orig)), [])
        leak = self.problems(self.twin(orig, split="open"))
        self.assertTrue(any("twin_of" in p and "heldout" in p for p in leak), leak)
        # and the same twin id without twin_of is judged by its own hash
        plain = self.twin(orig)
        for k in ("twin_of", "twin_cards", "twin_set_sha256"):
            del plain[k]
        self.assertEqual(pf.record_split(plain), "open")

    def test_the_twin_fields_are_checked(self):
        o = _ids("open", 1)[0]
        self.assertEqual(self.problems(self.twin(o)), [])
        for bad, why in ((self.twin(o, twin_of="bad id!"), "not a puzzle id"), (self.twin(o, twin_of=o + "-twin"), "of itself"),
                         (self.twin(o, twin_cards={}), "twin_cards"), (self.twin(o, twin_cards={"A": "xyz"}), "twin_cards"),
                         (self.twin(o, twin_set_sha256="0" * 64), "does not match twin_cards")):
            self.assertTrue(any(why in p for p in self.problems(bad)), (why, self.problems(bad)))

    def test_the_set_stamp_moves_with_a_script_and_not_with_the_order(self):
        a = pf.twin_set_sha(self.CARDS)
        self.assertEqual(a, pf.twin_set_sha(dict(reversed(list(self.CARDS.items())))))
        self.assertNotEqual(a, pf.twin_set_sha(dict(self.CARDS, **{"Dravi Ton": "c" * 64})))
        self.assertNotEqual(a, pf.twin_set_sha({"Zorbin Kel": "a" * 64}))

    def test_a_suite_pairs_each_original_once_and_a_frozen_twin_cannot_be_edited(self):
        o = _ids("open", 1)[0]
        t = self.twin(o)
        base = {"schema": pf.SUITE_SCHEMA, "suite": "s", "version": "1", "engine_sha": "e", "puzzles": [t]}
        self.assertEqual(pf.suite_twin_problems(base), [])
        two = dict(base, puzzles=[t, self.twin(o, tid=o + "-twin2")])
        self.assertTrue(any("both twins of" in p for p in pf.suite_twin_problems(two)))
        chain = dict(base, puzzles=[t, self.twin(t["id"], tid="t3", split=t["split"])])
        self.assertTrue(any("itself a twin" in p for p in pf.suite_twin_problems(chain)))
        orig = {"id": o, "split": "heldout"}
        self.assertTrue(any("its original" in p for p in pf.suite_twin_problems(dict(base, puzzles=[orig, t]))))
        frozen = pf.freeze(base)
        moved = dict(frozen, puzzles=[dict(t, twin_of=_ids("open", 2)[1])])
        self.assertNotEqual(pf.suite_hash(moved), frozen["suite_sha256"], "twin_of is part of what the suite means")
        cards = {"Zorbin Kel": "d" * 64}
        moved = dict(frozen, puzzles=[dict(t, twin_cards=cards, twin_set_sha256=pf.twin_set_sha(cards))])
        self.assertNotEqual(pf.suite_hash(moved), frozen["suite_sha256"], "a changed script moves the suite hash")

    def test_a_suite_without_twins_keeps_its_hash(self):
        # the committed frozen suites were hashed before twin fields: they must still verify
        files = sorted((evalpath.EVAL / "suites").glob("puzzles-*.json"))[:3]
        frozen = [pf.load_suite(f) for f in files]
        self.assertTrue(any(m.get("frozen") for m in frozen))
        for f, m in zip(files, frozen):
            if m.get("frozen"):
                self.assertEqual(pf.suite_hash(m), m["suite_sha256"], f.name)


if __name__ == "__main__":
    unittest.main()
