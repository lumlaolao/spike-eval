"""board_drain.py: the drain board's model read off a recorded prompt (sacrifice outlets, drainers, recursion, the
stack and the end step), its verdict per position, the regret step and floor, and the witness line it writes."""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import board_drain as bd  # noqa: E402
import puzzle_gen as pg  # noqa: E402
import step_regret_board as sb  # noqa: E402

STATS = {"Blood Artist": "0/1", "Zulaport Cutthroat": "1/1", "Cruel Celebrant": "1/2", "Carrion Feeder": "1/1",
         "Viscera Seer": "1/1", "Reassembling Skeleton": "1/1", "Bloodghast": "2/1", "Gravecrawler": "2/1"}
COST = {"Blood Artist": "1{B}", "Zulaport Cutthroat": "1{B}", "Cruel Celebrant": "{W}{B}", "Carrion Feeder": "{B}",
        "Viscera Seer": "{B}", "Reassembling Skeleton": "1{B}", "Bloodghast": "{B}{B}", "Gravecrawler": "{B}"}
BA_TRIG = ("#{n} trigger of Blood Artist 0/1 @6 (you): Whenever Blood Artist or another creature dies, target player "
           "loses 1 life and you gain 1 life. (Targeting: [[Opponent]]) -> targets: Opponent")
BA_SELF = BA_TRIG.replace("[[Opponent]]) -> targets: Opponent", "[[Solver]]) -> targets: Solver")
ZULA_TRIG = ("#{n} trigger of Zulaport Cutthroat 1/1 @8 (you): Whenever Zulaport Cutthroat or another creature you "
             "control dies, each opponent loses 1 life and you gain 1 life.")
SEER_AB = "#{n} ability of Viscera Seer 1/1 @3 (you): Viscera Seer @3 - Solver scries 1."
FEEDER_AB = ("#{n} ability of Carrion Feeder 1/1 @2 (you): Carrion Feeder @2 - Solver puts a +1/+1 counter on Carrion "
             "Feeder @2.")
RETURN_AB = ("#{n} ability of Reassembling Skeleton 1/1 @17 (you): Reassembling Skeleton @17 - Return Reassembling "
             "Skeleton @17 from the graveyard to the battlefield tapped.")
GHAST_TRIG = ("#{n} trigger of Bloodghast 2/1 @14 (you): Landfall — Whenever a land you control enters, you may return "
              "Bloodghast from your graveyard to the battlefield.")
ARTIST_SPELL = "#{n} spell Blood Artist 0/1 @12 (you): Blood Artist - Creature 0 / 1"


def prompt(bf, hand=(), gy=(), swamps=0, pool="", land_played=0, stack=(), opp=18, phase="MAIN2", tapped=0,
           decision="You have priority (MAIN2, your main phase)."):
    """A seat prompt in the recorded format (the lines the model reads)."""
    items = [s.format(n=len(stack) - i) for i, s in enumerate(stack)]
    st = "(empty)" if not items else "%d not yet resolved (the top one resolves when both players pass in a row): %s" % (
        len(items), " | ".join(items))
    hand_s = ", ".join(("Swamp @%d" % (40 + i)) if c == "Swamp" else "%s %s %s @%d (castable)" % (
        c, COST[c], STATS[c], 40 + i) for i, c in enumerate(hand)) or "(empty)"
    bf_s = ", ".join("%s %s [untapped] @%d" % (c, STATS.get(c, ""), 20 + i) if c in STATS else "%s [untapped] @%d" % (
        c, 20 + i) for i, c in enumerate(bf)) or "(no nonland permanents)"
    gy_s = ", ".join("%s %s %s @%d" % (c, COST[c], STATS[c], 60 + i) for i, c in enumerate(gy)) or "(none)"
    lands = swamps + tapped
    return "\n".join([
        "== Turn 1 %s (active: Solver | priority: you) ==" % phase,
        "YOU [Solver] life 20 | poison 0/10 | hand %d | library 4 (hidden, order unknown) | graveyard %d | exile 0 | "
        "lands played this turn %d/1 | mana pool %s | spells cast this turn 0" % (len(hand), len(gy), land_played,
                                                                                pool or "empty"),
        "  hand(%d): %s" % (len(hand), hand_s),
        "  board: %s" % bf_s,
        "  lands: %d untapped / %d total (Swamp x%d [untapped])" % (swamps, lands, lands),
        "  graveyard(%d): %s" % (len(gy), gy_s),
        "  exile(0): (none)",
        "OPP [Opponent] life %d | poison 0/10 | hand 0 | library 3 (hidden, order unknown) | graveyard 0 | exile 0 | "
        "lands played this turn 0/1 | mana pool empty | spells cast this turn 0" % opp,
        "  hand: 0 cards (hidden)",
        "  board: (no nonland permanents)",
        "  lands: 0 untapped / 0 total",
        "  graveyard(0): (none)",
        "STACK (engine, top first): %s" % st,
        "DECISION: %s" % decision])


ENGINE = ["Blood Artist", "Carrion Feeder", "Cruel Celebrant", "Viscera Seer", "Zulaport Cutthroat"]


class ParseTest(unittest.TestCase):
    def test_the_state_is_read_off_the_prompt(self):
        s = bd.parse_state(prompt(ENGINE, hand=["Reassembling Skeleton", "Swamp"], gy=["Gravecrawler"], swamps=3,
                                  pool="{B:1}", stack=[BA_TRIG, ZULA_TRIG, SEER_AB, RETURN_AB], opp=17))
        self.assertEqual(s["bf"], tuple(sorted(ENGINE)))
        self.assertEqual((s["hand"], s["gy"], s["land"]), (("Reassembling Skeleton",), ("Gravecrawler",), True))
        self.assertEqual((s["pc"], s["pb"], s["sw"], s["opp"], s["eot"]), (0, 1, 3, 17, False))
        self.assertEqual(s["stack"], [("drain", 1), ("drain", 1), ("noop", "scry"), ("return",)], "top first")

    def test_a_trigger_at_yourself_drains_nothing(self):
        s = bd.parse_state(prompt(ENGINE, stack=[BA_SELF, FEEDER_AB, GHAST_TRIG, ARTIST_SPELL]))
        self.assertEqual(s["stack"], [("drain", 0), ("noop", "counter"), ("ghast",), ("creature", "Blood Artist")])

    def test_the_land_drop_needs_a_swamp_and_the_drop_unused(self):
        self.assertFalse(bd.parse_state(prompt(ENGINE, hand=["Swamp"], land_played=1))["land"])
        self.assertFalse(bd.parse_state(prompt(ENGINE))["land"])

    def test_the_end_step_is_read_and_other_steps_refused(self):
        self.assertTrue(bd.parse_state(prompt(ENGINE, phase="END_OF_TURN"))["eot"])
        with self.assertRaises(sb.NotModeled):
            bd.parse_state(prompt(ENGINE, phase="COMBAT_DECLARE_ATTACKERS"))

    def test_anything_outside_the_model_is_refused(self):
        with self.assertRaises(sb.NotModeled):
            bd.parse_state(prompt(ENGINE + ["Grizzly Bears"]))
        with self.assertRaises(sb.NotModeled):
            bd.parse_state(prompt(ENGINE, stack=["#1 spell Fling @9 (you): Fling deals 2 damage to Opponent."]))
        with self.assertRaises(sb.NotModeled):
            bd.parse_state(prompt(ENGINE, pool="{G:1}"))
        with self.assertRaises(sb.NotModeled):
            bd.parse_state(prompt(ENGINE).replace("OPP [Opponent] life 18 | poison 0/10 | hand 0",
                                                  "OPP [Opponent] life 18 | poison 0/10 | hand 2"))


def start_state(search, life):
    """The state of a generated board from its search inputs (puzzle_gen.drain_search's)."""
    return {"pc": 0, "pb": 0, "sw": search["swamps"], "land": search["land"], "bf": tuple(sorted(search["bf"])),
            "gy": tuple(sorted(search["gy"])), "hand": tuple(sorted(search["hand"])), "altar": search["altar"],
            "stack": [], "opp": life, "eot": False, "ended": False, "pending": None}


class ValueTest(unittest.TestCase):
    def test_the_value_is_the_generators_exhaustive_maximum(self):
        # Every generated board from an empty stack in the main phase: the model's value must be drain_search's
        # (it adds the stack, responses and the end step, none of which beats the plain sequence).
        n = 0
        for seed in range(400):
            b = pg.drain_board(random.Random(seed))
            if not b:
                continue
            s = b["search"]
            want = pg.drain_search(s["swamps"], s["land"], set(s["bf"]), set(s["gy"]), set(s["hand"]), s["altar"])[0]
            self.assertEqual(bd.value(start_state(s, b["life"])), want, (seed, s))
            n += 1
            if n == 12:
                break
        self.assertEqual(n, 12)

    def test_a_drainer_on_the_stack_counts_once_it_resolves(self):
        # Blood Artist being cast: wait for it, then Zulaport dies with two drainers out (2), the Artist (1).
        s = bd.parse_state(prompt(["Viscera Seer", "Zulaport Cutthroat"], stack=[ARTIST_SPELL], opp=3))
        self.assertEqual(bd.value(s), 2 + 1, "the Seer, the outlet, dies last and drains nothing")
        self.assertTrue(bd.winnable(s))
        t = bd.after_option(s, "ACTIVATE Viscera Seer 1/1 @3 [Sacrifice a creature: Scry 1.]")
        self.assertEqual(t["pending"], "Viscera Seer")
        u = bd.after_option(t, "Zulaport Cutthroat 1/1 [untapped] @21")
        self.assertEqual(bd.value(u), 1 + 1, "Zulaport in response drains 1, then the Artist 1")
        self.assertFalse(bd.winnable(u))

    def test_the_end_step_keeps_instant_speed_only(self):
        # In the end step Gravecrawler cannot be cast from the graveyard; the sacrifices still drain.
        main = bd.parse_state(prompt(["Carrion Feeder", "Zulaport Cutthroat"], gy=["Gravecrawler"], swamps=1, opp=2))
        self.assertEqual(bd.value(main), 1 + 1, "cast Gravecrawler and sacrifice it, then Zulaport")
        eot = bd.parse_state(prompt(["Carrion Feeder", "Zulaport Cutthroat"], gy=["Gravecrawler"], swamps=1, opp=2,
                                    phase="END_OF_TURN"))
        self.assertEqual(bd.value(eot), 1)

    def test_a_pass_on_an_empty_stack_goes_to_the_end_step_unless_it_yields(self):
        s = bd.parse_state(prompt(["Carrion Feeder", "Zulaport Cutthroat"], gy=["Gravecrawler"], swamps=1, opp=2))
        self.assertTrue(bd.winnable(s))
        t = bd.after_option(s, "PASS (do nothing)")
        self.assertTrue(t["eot"])
        self.assertEqual(bd.value(t), 1)
        self.assertFalse(bd.winnable(t))
        self.assertTrue(bd.after_option(s, "PASS (do nothing)", yielded=True)["ended"])

    def test_a_yield_skips_every_window_but_the_empty_main_phase(self):
        # LlmSpikeGui: once yielded, the seat is asked again this turn only in its own main phase on an empty stack
        # (a recorded deepseek attempt yielded in the end step with lethal still to sacrifice, and was never asked).
        eot = bd.parse_state(prompt(["Carrion Feeder", "Zulaport Cutthroat"], phase="END_OF_TURN",
                                    stack=[ZULA_TRIG, FEEDER_AB], opp=2))
        self.assertEqual(bd.value(eot), 1 + 1, "the trigger, then one more death with Zulaport out")
        t = bd.after_option(eot, "PASS (do nothing)", yielded=True)
        self.assertTrue(t["yld"])
        self.assertEqual((bd.value(t), t["opp"]), (0, 1), "the trigger resolved; never asked again")
        main = bd.parse_state(prompt(["Carrion Feeder", "Zulaport Cutthroat"], gy=["Gravecrawler"], swamps=1,
                                     stack=[ZULA_TRIG], opp=3))
        y = bd.after_option(main, "PASS (do nothing)", yielded=True)
        self.assertEqual(bd.value(y), 2, "asked again on the empty stack: Gravecrawler, then Zulaport")
        self.assertTrue(bd.after_option(dict(y, stack=[]), "PASS (do nothing)")["ended"], "no end step after a yield")

    def test_the_skeleton_returns_in_response(self):
        s = bd.parse_state(prompt(["Viscera Seer", "Zulaport Cutthroat"], gy=["Reassembling Skeleton"], swamps=4,
                                  stack=[ZULA_TRIG, RETURN_AB], opp=9))
        self.assertEqual(bd.value(s), 1 + 3 + 1, "the trigger; the Skeleton 3 times (1 paid, 2 more); Zulaport")

    def test_the_target_and_the_optional_trigger(self):
        bf = ["Viscera Seer", "Blood Artist", "Zulaport Cutthroat"]
        s = bd.parse_state(prompt(bf, gy=["Bloodghast"], stack=[GHAST_TRIG], opp=4))
        self.assertEqual(bd.value(s), 2 + 2 + 1, "Bloodghast back: it, a drainer, the other")
        self.assertFalse(bd.winnable(bd.after_option(s, "NO (decline)")))
        self.assertTrue(bd.winnable(bd.after_option(s, "YES (accept)")))


def r(tier, typ, choice, options, p=None, **kw):
    d = {"t": "decision", "seat": "Solver", "tier": tier, "type": typ, "choice": choice, "options": options}
    if p:
        d["prompt"] = p
    d.update(kw)
    return d


MENU = ["ACTIVATE Viscera Seer 1/1 @3 [Sacrifice a creature: Scry 1.]", "PASS (do nothing)"]


VICTIMS = ["Blood Artist 0/1 [untapped] @20", "Bloodghast 2/1 [untapped] @21", "Viscera Seer 1/1 [untapped] @22",
           "Zulaport Cutthroat 1/1 [untapped] @23", "CANCEL this play"]


class AttemptTest(unittest.TestCase):
    def rows(self):
        bf = ["Blood Artist", "Bloodghast", "Viscera Seer", "Zulaport Cutthroat"]
        after = ["Blood Artist", "Bloodghast", "Viscera Seer"]
        z = ["Zulaport Cutthroat"]
        return [
            r("smart", "play", 0, MENU, prompt(bf, opp=5)),
            r("smart", "sacrifice", 1, VICTIMS, prompt(bf, opp=5), picks=[3]),
            r("smart", "trigger-order", 0, ["Blood Artist 0/1 @20 [...]", "Zulaport Cutthroat 1/1 @23 [...]"],
              prompt(after, gy=z, stack=[SEER_AB], opp=5), picks=[0, 1]),
            r("smart", "target", 1, ["you (Solver)", "opponent (Opponent)"],
              prompt(after, gy=z, stack=[ZULA_TRIG, SEER_AB], opp=5)),
            r("smart", "play", -1, MENU, prompt(after, gy=z, stack=[BA_TRIG, ZULA_TRIG, SEER_AB], opp=5)),
        ]

    def test_the_trajectory_regret_step_and_floor(self):
        # The non-drainer (Bloodghast) must die first: 2 + 2 + 1. Zulaport first is 2 + 1 + 1.
        rows = self.rows()
        traj = bd.trajectory(rows)
        self.assertEqual([p["k"] for p in traj], [0, 1, 2, 3, 4, 5])
        self.assertEqual([p["value"] for p in traj], [5, 5, 4, 4, 4, 3], "the end: one drain resolved, opp 4")
        self.assertEqual([p["winnable"] for p in traj], [True, True, False, False, False, False])
        reg = sb.regret(traj)
        self.assertEqual((reg["status"], reg["regret_k"]), ("exact", 1))
        fl = bd.floor_at(rows, 1)
        self.assertEqual(fl["kept"], ["Bloodghast 2/1 [untapped] @21", "CANCEL this play"])
        self.assertEqual((fl["n_options"], fl["model_label"]), (5, "Zulaport Cutthroat 1/1 [untapped] @23"))

    def test_a_question_row_is_read_from_the_activation_and_checked_against_its_prompt(self):
        rows = self.rows()
        s = bd.position_state(rows, 3)
        self.assertEqual([x[:2] for x in s["stack"][:3]], [("drain", 1), ("drain", 1), ("noop", "scry")])
        rows[3]["prompt"] = prompt(["Viscera Seer"], gy=["Zulaport Cutthroat"], opp=5)
        with self.assertRaises(sb.NotModeled):
            bd.position_state(rows, 3)

    def test_the_target_at_yourself_is_a_lost_drain(self):
        rows = self.rows()
        s = bd.position_state(rows, 3)
        self.assertEqual(bd.value(bd.after_option(s, "you (Solver)")), bd.value(s) - 1)

    def test_the_witness_script(self):
        rows = self.rows()
        w = bd.script_for(rows, 1)
        self.assertEqual(w["option"], "Bloodghast 2/1 [untapped] @21")
        self.assertEqual(w["lines"][:2], ["activate Viscera Seer", "Bloodghast"], "the prefix, then the kept victim")
        self.assertEqual(w["lines"][2:4], ["Blood Artist; Zulaport Cutthroat", "opponent"])
        self.assertGreaterEqual(w["dealt"], 5)
        self.assertNotEqual(w["lines"][-1], "pass", "the line stops where the last drain resolves")

    def test_the_witness_cancels_when_no_sacrifice_keeps_the_win(self):
        # Blood Artist still being cast: every sacrifice now loses its drain; CANCEL, let it resolve, then sacrifice.
        bf = ["Viscera Seer", "Zulaport Cutthroat"]
        rows = [r("smart", "play", 0, MENU, prompt(bf, stack=[ARTIST_SPELL], opp=3)),
                r("smart", "sacrifice", 0, ["Viscera Seer 1/1 [untapped] @20", "Zulaport Cutthroat 1/1 [untapped] @21",
                                            "CANCEL this play"], prompt(bf, stack=[ARTIST_SPELL], opp=3), picks=[1])]
        self.assertEqual(bd.floor_at(rows, 1)["kept"], ["CANCEL this play"])
        w = bd.script_for(rows, 1)
        self.assertEqual(w["option"], "CANCEL this play")
        self.assertEqual(w["lines"][:4], ["activate Viscera Seer", "cancel", "pass", "activate Viscera Seer"])

    def test_the_kind_of_a_regret_step(self):
        rows = self.rows()
        self.assertEqual(bd.regret_kind(rows, 1), "drainer", "Zulaport sacrificed with Bloodghast still to die")
        land = [r("smart", "play", 0, ["PLAY LAND Swamp @40", "PASS (do nothing)"],
                  prompt(["Bloodghast", "Viscera Seer", "Zulaport Cutthroat"], hand=["Swamp"], opp=4))]
        self.assertEqual(bd.regret_kind(land, 0), "land", "the land drop with Bloodghast still on the battlefield")
        skel = [r("smart", "play", 0, ["ACTIVATE Reassembling Skeleton 1/1 @60 in your GRAVEYARD [...]"],
                  prompt(["Carrion Feeder"], gy=["Reassembling Skeleton"], swamps=2))]
        self.assertEqual(bd.regret_kind(skel, 0), "skeleton")
        resp = [r("smart", "play", 0, MENU, prompt(["Viscera Seer", "Zulaport Cutthroat"], stack=[ARTIST_SPELL])),
                r("smart", "sacrifice", 0, ["Viscera Seer 1/1 [untapped] @20", "CANCEL this play"],
                  prompt(["Viscera Seer", "Zulaport Cutthroat"], stack=[ARTIST_SPELL]), picks=[0])]
        self.assertEqual(bd.regret_kind(resp, 1), "response", "a sacrifice while a body is still on the stack")
        y = [r("smart", "play", -1, MENU, prompt(["Viscera Seer"], phase="END_OF_TURN", stack=[ZULA_TRIG]),
               raw_reply="CHOICE: 2\nYIELD: end-of-turn")]
        self.assertEqual(bd.regret_kind(y, 0), "yield")

    def test_the_family_is_chosen_by_the_board(self):
        self.assertIs(sb.family_of({"id": "drain2_516c13f84175"}), bd)
        self.assertIs(sb.family_of({"id": "hard-drain-0154afc1249d"}), bd)
        self.assertIsNone(sb.family_of({"id": "hoofdrain_1"}))
        self.assertIs(sb.family_of({"id": "splice_06dad20af5b3"}), sb)


class WitnessTest(unittest.TestCase):
    def test_proven_only_at_the_same_position_and_option(self):
        rows = AttemptTest().rows()
        res = {"status": "solved", "script_played": True, "script_drift": ""}
        self.assertEqual(bd.witness_verdict(rows, rows, 1, res, "Zulaport Cutthroat 1/1 [untapped] @23"), "proven")
        self.assertTrue(bd.witness_verdict(rows, rows, 1, res, "Bloodghast 2/1 [untapped] @21").startswith("drift"))
        other = [dict(x) for x in rows]
        other[0]["options"] = ["PASS (do nothing)"]
        self.assertTrue(bd.witness_verdict(rows, other, 1, res).startswith("drift"))
        self.assertEqual(bd.witness_verdict(rows, rows, 1, dict(res, status="failed")), "failed")


if __name__ == "__main__":
    unittest.main()
