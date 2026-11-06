"""step_regret_board.py: the splice board model read off a recorded prompt, its verdict per position, the regret step
and the floor it implies, and the agreement with the exact search."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import step_regret_board as sb  # noqa: E402

PING = ("#{n} ability of Thermo-Alchemist 0/3 @7 (you): Thermo-Alchemist @7 - Thermo-Alchemist @7 deals 1 damage to "
        "Opponent.")
UNTAP = ("#{n} trigger of Thermo-Alchemist 0/3 @7 (you): Whenever you cast an instant or sorcery spell, untap "
         "Thermo-Alchemist.")
RITUAL = "#{n} spell Desperate Ritual @11 (you): Desperate Ritual @11 - Solver adds {{R}}{{R}}{{R}}."
SPIKE_RAY = ("#{n} spell Lava Spike @17 (you): Lava Spike @17 - Lava Spike @17 deals 3 damage to Opponent. Lava Spike "
             "@17 deals 2 damage to Opponent. -> targets: Opponent, Opponent")
BOLT_SELF = ("#{n} spell Lightning Bolt @15 (you): Lightning Bolt @15 - Lightning Bolt @15 deals 3 damage to Solver. "
             "-> targets: Solver")


def prompt(hand, pool=0, lands=0, alch="untapped", stack=(), opp=18, decision="You have priority (MAIN2)."):
    """A seat prompt in the recorded format (the lines the model reads; the rest is cut)."""
    items = [s.format(n=len(stack) - i) for i, s in enumerate(stack)]
    st = "(empty)" if not items else "%d not yet resolved (it resolves when both players pass in a row): %s" % (
        len(items), " | ".join(items))
    cards = ", ".join("%s 1{R} @%d (castable)" % (c, i + 1) for i, c in enumerate(hand))
    board = "Thermo-Alchemist 0/3 [%s] [defender] @7" % alch if alch else "(no nonland permanents)"
    return "\n".join([
        "== Turn 1 MAIN2 (active: Solver | priority: you) ==",
        "YOU [Solver] life 20 | poison 0/10 | hand %d | library 4 | mana pool %s | spells cast this turn 0"
        % (len(hand), "{R:%d}" % pool if pool else "empty"),
        "  hand(%d): %s" % (len(hand), cards or "(none)"),
        "  board: %s" % board,
        "  lands: %d untapped / 2 total (Mountain x2)" % lands,
        "OPP [Opponent] life %d | poison 0/10 | hand 0 | library 3 | mana pool empty" % opp,
        "  board: (no nonland permanents)",
        "  lands: 0 untapped / 0 total",
        "STACK (engine, top first): %s" % st,
        "DECISION: %s" % decision])


START = ["Desperate Ritual", "Lava Spike", "Glacial Ray", "Lava Spike", "Lightning Bolt", "Desperate Ritual"]


class ParseTest(unittest.TestCase):
    def test_the_state_is_read_off_the_prompt(self):
        s = sb.parse_state(prompt(["Lava Spike", "Glacial Ray"], pool=3, lands=1, alch="tapped",
                                  stack=[UNTAP, RITUAL, PING], opp=17))
        self.assertEqual(s["hand"], ("Glacial Ray", "Lava Spike"))
        self.assertEqual((s["mana"], s["alch"], s["u"], s["opp"]), (4, True, False, 17))
        self.assertEqual(s["stack"], [{"dmg": 0, "mana": 0, "untap": True}, {"dmg": 0, "mana": 3, "untap": False},
                                      {"dmg": 1, "mana": 0, "untap": False}], "top first")

    def test_a_spliced_spell_and_a_spell_at_yourself(self):
        s = sb.parse_state(prompt([], stack=[SPIKE_RAY, BOLT_SELF]))
        self.assertEqual([x["dmg"] for x in s["stack"]], [5, 0], "only damage to the opponent counts")

    def test_an_unknown_stack_item_is_refused_not_guessed(self):
        bad = "#1 spell Fireball @9 (you): Fireball @9 - Fireball @9 deals X damage to Opponent."
        with self.assertRaises(sb.NotModeled):
            sb.parse_state(prompt([], stack=[bad]))
        with self.assertRaises(sb.NotModeled):
            sb.parse_state(prompt(["Grapeshot"]))
        # splice2 boards can add Kiln Fiend or Goblin Electromancer (a pump, a cost reducer): not in the model.
        with self.assertRaises(sb.NotModeled):
            sb.parse_state(prompt(["Lava Spike"]).replace("[defender] @7", "[defender] @7, Kiln Fiend 1/2 @6"))


    def test_an_empty_hand_reads_as_empty(self):
        # The prompt writes "(empty)" for an empty hand (every solved attempt ends there); was a crash.
        s = sb.parse_state(prompt([], pool=1, alch="tapped").replace("hand(0): (none)", "hand(0): (empty)"))
        self.assertEqual((s["hand"], s["mana"]), ((), 1))


class ValueTest(unittest.TestCase):
    def test_the_board_is_winnable_at_the_start(self):
        # splice_06dad20af5b3: "the best line deals 18 by splicing".
        s = sb.parse_state(prompt(START, lands=2, opp=18))
        self.assertEqual(sb.value(s), 18)
        self.assertTrue(sb.winnable(s))

    def test_a_tapped_alchemist_is_one_ping_less(self):
        s = sb.parse_state(prompt(["Lava Spike"], pool=1, alch="tapped"))
        self.assertEqual(sb.value(s), 3 + 1, "the Spike, then the ping its cast untaps")
        s = sb.parse_state(prompt(["Lava Spike"], pool=1, alch="untapped"))
        self.assertEqual(sb.value(s), 1 + 3 + 1)

    def test_an_untap_trigger_over_an_untapped_alchemist_is_a_ping_in_response(self):
        s = sb.parse_state(prompt([], alch="untapped", stack=[UNTAP]))
        self.assertEqual(sb.value(s), 2, "ping in response, the trigger untaps it, ping again")
        s = sb.parse_state(prompt([], alch="tapped", stack=[UNTAP]))
        self.assertEqual(sb.value(s), 1)

    def test_pending_mana_and_damage_count(self):
        s = sb.parse_state(prompt(["Lava Spike"], alch=None, stack=[PING, RITUAL]))
        self.assertEqual(sb.value(s), 1 + 3, "the ping on the stack, then the Spike paid by the Ritual")

    def test_the_opponent_already_dead_is_won(self):
        self.assertTrue(sb.winnable(sb.parse_state(prompt([], alch=None, opp=0))))


class OptionTest(unittest.TestCase):
    def test_pass_on_an_empty_stack_ends_the_turn(self):
        s = sb.parse_state(prompt(["Lava Spike"], pool=1, opp=3))
        self.assertTrue(sb.winnable(s))
        self.assertFalse(sb.winnable(sb.after_option(s, "PASS (do nothing)")))

    def test_pass_resolves_the_top_item_only(self):
        # An untap trigger on top over an untapped Alchemist: passing lets it resolve and the ping in response
        # is gone.
        s = sb.parse_state(prompt([], alch="untapped", stack=[UNTAP], opp=2))
        self.assertTrue(sb.winnable(s))
        t = sb.after_option(s, "PASS (do nothing)")
        self.assertEqual((t["stack"], t["u"]), ([], True))
        self.assertFalse(sb.winnable(t))

    def test_activate_and_cast(self):
        s = sb.parse_state(prompt(["Lava Spike", "Desperate Ritual"], pool=3, alch="untapped", opp=10))
        t = sb.after_option(s, "ACTIVATE Thermo-Alchemist 0/3 @7 [{T}: Thermo-Alchemist deals 1 damage]")
        self.assertEqual((t["u"], t["stack"][0]["dmg"]), (False, 1))
        self.assertEqual(sb.after_option(s, "ACTIVATE Mountain @8 [{T}: Add {R}.]"), s)
        # Casting the Spike may carry the Ritual (3 mana in the pool: 1 + 2), so the best of the casts counts.
        c = sb.after_option(s, "CAST Lava Spike @2 {R} [Lava Spike deals 3 damage to target player.]")
        self.assertEqual(sb.value(c), sb.value(s), "the Spike is the first spell of the best line")

    def test_casting_the_ritual_bare_loses_the_splice(self):
        # The note's finding, small: a Ritual that each Lava Spike could carry (net +1 mana per carry, and it stays
        # in hand) is cast on its own instead (net +1 once).
        s = sb.parse_state(prompt(HAND, pool=3, alch=None, opp=11))
        self.assertEqual(sb.value(s), 11, "Spike+Ritual twice (free), the Ritual (4), Ray and Bolt: 3+3+2+3")
        t = sb.after_option(s, "CAST Desperate Ritual @3 1{R} [Add {R}{R}{R}.]")
        self.assertEqual(sb.value(t), 9, "4 mana for Spike, Spike, Bolt")
        self.assertFalse(sb.winnable(t))


HAND = ["Lava Spike", "Lava Spike", "Desperate Ritual", "Lightning Bolt", "Glacial Ray"]
SPIKE = "#{n} spell Lava Spike @1 (you): Lava Spike @1 - Lava Spike @1 deals 3 damage to Opponent. -> targets: Opponent"


class AttemptTest(unittest.TestCase):
    def rows(self):
        def r(tier, typ, choice, options, p=None, **kw):
            d = {"t": "decision", "seat": "Solver", "tier": tier, "type": typ, "choice": choice, "options": options}
            if p:
                d["prompt"] = p
            d.update(kw)
            return d
        rest = ["Lava Spike", "Lava Spike", "Lightning Bolt", "Glacial Ray"]
        menu = ["CAST Lava Spike @1 {R}", "CAST Lava Spike @2 {R}", "CAST Desperate Ritual @3 1{R}",
                "CAST Lightning Bolt @4 {R}", "CAST Glacial Ray @5 1{R}", "PASS (do nothing)"]
        return [
            r("smart", "play", 2, menu, prompt(HAND, pool=3, alch=None, opp=11)),
            r("auto", "splice", -1, []),
            r("smart", "play", -1, ["PASS (do nothing)"], prompt(rest, pool=1, alch=None, opp=11, stack=[RITUAL])),
            r("smart", "play", 0, ["CAST Lava Spike @1 {R}", "PASS (do nothing)"], prompt(rest, pool=4, alch=None,
                                                                                         opp=11)),
            r("smart", "target", 1, ["you (Solver)", "opponent (Opponent)"], prompt(rest, pool=4, alch=None, opp=11)),
            r("smart", "play", -1, ["PASS (do nothing)"], prompt(rest[1:], pool=3, alch=None, opp=11, stack=[SPIKE])),
        ]

    def test_the_trajectory_regret_step_and_floor(self):
        rows = self.rows()
        traj = sb.trajectory(rows)
        self.assertEqual([p["k"] for p in traj], [0, 2, 3, 4, 5, 6], "before each model decision, then the end")
        self.assertEqual([p["winnable"] for p in traj], [True, False, False, False, False, False])
        self.assertEqual(traj[3]["pending_cast"], {"card": "Lava Spike", "splices": None})
        reg = sb.regret(traj)
        self.assertEqual((reg["status"], reg["regret_k"]), ("exact", 0))
        fl = sb.floor_at(rows, reg["regret_k"])
        self.assertEqual(fl["kept"], ["CAST Lava Spike @1 {R}", "CAST Lava Spike @2 {R}"], "a Spike carries it")
        self.assertEqual((fl["n_options"], fl["floor"]), (6, 2 / 6))
        self.assertEqual(fl["model_label"], "CAST Desperate Ritual @3 1{R}")

    def test_an_attempt_with_no_model_decision_is_its_own_status(self):
        # Every decision lost to the fixed legal option (the model never answered): no position to label.
        rows = [dict(r, tier="auto") for r in self.rows()]
        traj = sb.trajectory(rows)
        self.assertEqual(traj, [])
        self.assertEqual(sb.regret(traj)["status"], "no_model_decision")

    def test_the_floor_at_a_splice_question_counts_payable_sets(self):
        # A regret step can be a splice question (two haiku attempts): its answers are the sets of offered cards,
        # each copy its own option; a set the pool cannot pay is no answer, so it is out of the denominator.
        hand = ["Desperate Ritual", "Desperate Ritual", "Glacial Ray", "Lava Spike", "Lava Spike"]
        p = prompt(hand, pool=3, alch=None, opp=10)
        rows = [{"t": "decision", "seat": "Solver", "tier": "smart", "type": "play", "choice": 0, "prompt": p,
                 "options": ["CAST Lava Spike @4 {R}", "PASS (do nothing)"]},
                {"t": "decision", "seat": "Solver", "tier": "smart", "type": "splice", "choice": 2, "prompt": p,
                 "options": ["Desperate Ritual 1{R} @1", "Desperate Ritual 1{R} @2", "Glacial Ray 1{R} @3"],
                 "raw_reply": "PICKS: 3"}]
        fl = sb.floor_at(rows, 1)
        self.assertEqual(fl["n_options"], 4, "none, either Ritual, the Ray: any two cost 5 of 3")
        self.assertEqual(fl["kept"], ["splice: Desperate Ritual", "splice: Desperate Ritual"])
        self.assertEqual((fl["floor"], fl["model_label"]), (0.5, "splice: Glacial Ray"))
        self.assertFalse(fl["options"]["splice: none"])

    def test_a_splice_set_the_pool_cannot_pay_is_undone_not_lost(self):
        # Recorded (jev, a Luna attempt): Lava Spike carrying two Glacial Rays (5 mana) with 4; the engine cancels
        # the cast and the next prompt has the same hand and pool. Was valued 0, so a lost dip mid-attempt.
        hand = ["Glacial Ray", "Glacial Ray", "Lava Spike"]
        s = sb.parse_state(prompt(hand, pool=4, alch=None, opp=5))
        t = dict(s, pending={"card": "Lava Spike", "splices": ("Glacial Ray", "Glacial Ray")})
        self.assertEqual(sb.value(t), sb.value(s))
        self.assertTrue(sb.winnable(t))

    def test_the_recorded_prefix_as_script_lines(self):
        self.assertEqual(sb.prefix_lines(self.rows(), 6),
                         ["cast Desperate Ritual", "pass", "cast Lava Spike", "opponent", "pass"])
        self.assertEqual(sb.prefix_lines(self.rows(), 3), ["cast Desperate Ritual", "pass"])

    def test_the_witness_script_at_a_winnable_position(self):
        w = sb.script_for(self.rows(), 0)
        self.assertEqual(w["prefix"], 0)
        self.assertEqual(w["lines"][:3], ["cast Lava Spike", "opponent", "Desperate Ritual"])
        self.assertGreaterEqual(w["dealt"], w["opp"])
        self.assertEqual(w["option"], "CAST Lava Spike @1 {R}", "the menu option the first line takes")
        with self.assertRaises(ValueError):
            sb.script_for(self.rows(), 3)   # lost: the model has no line to write

    def test_a_witness_from_a_stack_with_a_ping_waits_for_it(self):
        s = sb.parse_state(prompt(["Lava Spike"], pool=1, alch="tapped", stack=[PING], opp=5))
        lines, dealt = sb.winning_line(s)
        self.assertEqual((lines, dealt), (["cast Lava Spike", "opponent", "activate Thermo-Alchemist"], 5))

    def test_a_witness_game_is_checked_against_the_recording(self):
        rec = self.rows()
        solved = {"status": "solved", "script_played": True, "script_drift": ""}
        self.assertEqual(sb.witness_verdict(rec, rec, 3, solved), "proven")
        self.assertEqual(sb.witness_verdict(rec, rec, 3, dict(solved, status="failed")), "failed")
        self.assertEqual(sb.witness_verdict(rec, rec, 3, dict(solved, script_played=False, script_drift="x")),
                         "drift: the line did not play: x")
        other = [dict(r) for r in rec]
        other[3] = dict(other[3], prompt=prompt(["Lava Spike", "Lava Spike"], pool=3, alch=None, opp=11))
        self.assertTrue(sb.witness_verdict(rec, other, 3, solved).startswith("drift: K=3 state"))
        other = [dict(r) for r in rec]
        other[2] = dict(other[2], options=["CAST Lava Spike @1 {R}", "PASS (do nothing)"])
        self.assertTrue(sb.witness_verdict(rec, other, 3, solved).startswith("drift: row 2"))
        self.assertTrue(sb.witness_verdict(rec, rec[:2], 3, solved).startswith("drift: the witness game"))
        # The witness must take, at row K, the option the model says keeps the win.
        self.assertEqual(sb.witness_verdict(rec, rec, 3, solved, option="CAST Lava Spike @1 {R}"), "proven")
        self.assertTrue(sb.witness_verdict(rec, rec, 3, solved, option="PASS (do nothing)")
                        .startswith("drift: K=3 the witness took"))

    def test_agreement_with_the_search_and_its_baseline(self):
        traj = sb.trajectory(self.rows())
        verdicts = {0: "proven", 2: "unwinnable", 3: "not_found", 6: "unwinnable"}
        a = sb.agreement(traj, verdicts)
        self.assertEqual((a["decided"], a["agree"], a["baseline_agree"]), (3, 3, 2))
        self.assertEqual(a["disagreements"], [])

    def test_a_survey_row_per_attempt_refusals_included(self):
        # Surveying every recorded attempt: a board outside the model is a refused row (not a crash), and each row
        # carries the attempt's model, epoch and status from its result row, so conditions are never pooled.
        with tempfile.TemporaryDirectory() as d:
            res = {"seed": 1, "status": "failed", "solver_model": "m:x", "harness_epoch": "harness-epoch-9"}
            for name, rows in (("a", self.rows()),
                               ("b", [dict(self.rows()[0], prompt=prompt(["Grapeshot"], opp=3))])):
                (Path(d) / name).mkdir()
                (Path(d) / name / "game-1-5.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
                (Path(d) / name / "puzzle-results.jsonl").write_text(json.dumps(res) + "\n")
            out = Path(d) / "board.jsonl"
            sb.main([str(Path(d) / "a"), str(Path(d) / "b"), "--out", str(out)])
            got = [json.loads(x) for x in out.read_text().splitlines()]
        self.assertEqual([g["status"] for g in got], ["exact", "refused"])
        self.assertIn("Grapeshot", got[1]["refused"])
        self.assertEqual({(g["solver_model"], g["harness_epoch"], g["attempt_status"]) for g in got},
                         {("m:x", "harness-epoch-9", "failed")})
        self.assertEqual((got[0]["n_decisions"], got[0]["floor"]["floor"]), (5, 2 / 6))

    def test_search_verdicts_take_the_decided_one(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "step-regret.jsonl"
            rows = [{"t": "step_regret", "kind": "position", "source_game": "g", "k": 3, "verdict": "not_found"},
                    {"t": "step_regret", "kind": "position", "source_game": "g", "k": 3, "verdict": "unwinnable",
                     "refined": True},
                    {"t": "step_regret", "kind": "position", "source_game": "g", "k": 4, "verdict": "not_found"},
                    {"t": "step_regret_summary", "source_game": "g"}]
            f.write_text("".join(json.dumps(r) + "\n" for r in rows))
            self.assertEqual(sb.search_verdicts([f])["g"], {3: "unwinnable", 4: "not_found"})
            rows.append({"t": "step_regret", "kind": "position", "source_game": "g", "k": 3, "verdict": "proven"})
            f.write_text("".join(json.dumps(r) + "\n" for r in rows))
            with self.assertRaises(SystemExit):
                sb.search_verdicts([f])


if __name__ == "__main__":
    unittest.main()
