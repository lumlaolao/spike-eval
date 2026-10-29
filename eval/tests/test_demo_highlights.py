"""demo_highlights: each highlight kind comes from the model's own calls and words, matched only against objects its
prompt showed. Fixtures: runs/loop/g33 rows (eval/testdata/demo/README.md)."""
import json
import os
import sys
import unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)

HERE = str(evalpath.EVAL)
import demo_events as de
import demo_highlights as dh

FIX = os.path.join(HERE, "testdata", "demo")


def last_decision(*names):
    b = de.Builder()
    for name in names:
        with open(os.path.join(FIX, name)) as f:
            for line in f:
                if line.strip():
                    b.add(json.loads(line))
    return [e for e in b.events if e["kind"] == "decision"][-1]


class HighlightTest(unittest.TestCase):
    def test_the_chosen_option_and_the_simulated_options_light_up(self):
        ev = last_decision("g33-simulate.jsonl")
        hl = ev["highlights"]
        self.assertEqual(hl["26"]["kind"], "chosen")  # ACTIVATE Black Lotus @26
        self.assertIn("Black Lotus", hl["26"]["evidence"])
        self.assertEqual(hl["17"]["kind"], "looked")  # simulate option 1: CAST Bilbo @17
        self.assertEqual(hl["20"]["kind"], "looked")  # simulate option 2: CAST Time Walk @20
        self.assertIn("simulate previewed option 2", hl["20"]["evidence"])

    def test_set_plan_names_are_planned(self):
        ev = last_decision("g33-set-plan.jsonl")
        names = {o["handle"]: o["name"] for p in ev["state"]["players"] for z in p["zones"].values() for o in z}
        planned = [names[int(h)] for h, v in ev["highlights"].items() if v["kind"] == "planned"]
        text = (ev["plan"]["goal"] + " " + ev["plan"]["next"]).lower()
        self.assertTrue(planned)
        self.assertTrue(all(n.split(",")[0].lower() in text for n in planned), planned)

    def test_an_inspected_name_is_looked_up(self):
        ev = last_decision("g33-inspect.jsonl")
        looked = {h for h, v in ev["highlights"].items() if v["kind"] == "looked"}
        names = {o["handle"]: o["name"] for p in ev["state"]["players"] for z in p["zones"].values() for o in z}
        self.assertTrue(looked)
        self.assertTrue(all(names[int(h)].startswith("Bilbo") for h in looked))

    def test_the_attacker_in_combat_carries_the_engine_frame(self):
        ev = last_decision("g33-blockers.jsonl")
        mage = ev["highlights"]["135"]
        self.assertIn("in combat", mage["engine"])
        self.assertEqual(ev["chosen"], [])  # no blocks: nothing is chosen
        self.assertFalse(any(v["kind"] == "chosen" for v in ev["highlights"].values()))

    def test_a_guess_about_a_hidden_card_lights_up_nothing(self):
        ev = last_decision("g33-blockers.jsonl")
        self.assertIn("Reckless Rage", ev["answer"]["why"])
        for v in ev["highlights"].values():
            self.assertNotIn("Reckless Rage", v["evidence"] or "")

    def test_a_forced_pick_marks_only_objects_of_the_board_it_borrowed(self):
        b = de.Builder()
        for name in ("g33-t5-upkeep.jsonl", "g33-damage-assign.jsonl"):
            with open(os.path.join(FIX, name)) as f:
                for line in f:
                    b.add(json.loads(line))
        ev = [e for e in b.events if e["kind"] == "decision"][-1]
        shown = b.events[ev["state_from"]]["state"]
        visible = {str(o["handle"]) for p in shown["players"] for z in p["zones"].values() for o in z}
        self.assertEqual((ev["tier"], ev["state"]), ("auto", None))
        self.assertTrue(set(ev["highlights"]) <= visible)
        self.assertNotIn("160", ev["highlights"])  # its target, Pompous Battlemage @160, was not on the T5 board

    def test_names_only_match_visible_objects_and_handles_must_exist(self):
        state = {"players": [{"kind": "YOU", "zones": {"board": [{"name": "Bilbo, Thief in the Night", "handle": 31}]}},
                             {"kind": "OPP", "zones": {"board": [{"name": "Mountain", "handle": 24}]}}],
                 "stack": [], "combat": "none"}
        ev = {"options": [], "chosen": [], "answer": {"why": "Bilbo attacks; they surely hold Lightning Bolt @99"},
              "calls": [{"reasoning": "the mountain is tapped", "tools": []}]}
        hl = dh.compute(ev, state)
        self.assertEqual(set(hl), {"31", "24"})
        self.assertEqual(hl["31"]["kind"], "mentioned")
        self.assertTrue(hl["31"]["evidence"].startswith("why:"))

    def test_a_block_pair_marks_both_creatures(self):
        state = {"players": [{"kind": "YOU", "zones": {"board": [{"name": "Wall", "handle": 5}]}},
                             {"kind": "OPP", "zones": {"board": [{"name": "Ogre", "handle": 9}]}}],
                 "stack": [], "combat": "x",
                 "decision": "COMBAT\nATTACKERS: 1 = Ogre 3/3 @9\nYOUR BLOCKERS: 1 = Wall 0/4 @5"}
        ev = {"type": "blockers", "options": ["Wall 0/4 @5"], "chosen": [],
              "answer": {"blocks": [{"blocker": 1, "attacker": 1}]}, "calls": []}
        hl = dh.compute(ev, state)
        self.assertEqual((hl["5"]["kind"], hl["9"]["kind"]), ("chosen", "chosen"))

    def test_a_stronger_kind_wins_and_keeps_the_engine_frame(self):
        m = dh.Marks([{"handle": 1}])
        m.add(1, "engine", "on the stack")
        m.add(1, "mentioned", "reasoning")
        m.add(1, "chosen", "option 1")
        m.add(1, "planned", "plan")
        self.assertEqual(m.result()["1"], {"kind": "chosen", "evidence": "option 1", "engine": "on the stack"})


if __name__ == "__main__":
    unittest.main()
