"""demo_state: the demo viewer's board is rebuilt from the prompt the seat was shown, and from nothing else.
Fixtures are rows of runs/loop/g33 (epoch 64) and a July 2026 tournament game (eval/testdata/demo/README.md)."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
import demo_state as ds

FIX = os.path.join(HERE, "testdata", "demo")


def decision(name, **match):
    with open(os.path.join(FIX, name)) as f:
        rows = [json.loads(line) for line in f if line.strip()]
    rows = [r for r in rows if r.get("t") == "decision" and all(r.get(k) == v for k, v in match.items())]
    return rows[-1]


def by_handle(state):
    return {o["handle"]: o for o in ds.visible_objects(state) if o.get("handle") is not None}


class T5UpkeepTest(unittest.TestCase):
    """The state sample of the demo work order: g33, T5 UPKEEP."""

    def setUp(self):
        self.st = ds.parse_prompt(decision("g33-t5-upkeep.jsonl")["prompt"])
        self.you, self.opp = self.st["players"]

    def test_the_header_names_turn_phase_active_and_priority(self):
        self.assertEqual(self.st["header"], {"turn": 5, "phase": "UPKEEP", "active": "Seat", "priority": "you"})

    def test_both_players_counters_are_read(self):
        self.assertEqual((self.you["kind"], self.you["name"]), ("YOU", "Seat"))
        self.assertEqual(self.you["counters"]["life"], 18)
        self.assertEqual(self.you["counters"]["library"], 52)
        self.assertEqual(self.you["counters"]["graveyard"], 3)
        self.assertEqual(self.opp["name"], "ForgeAI")
        self.assertEqual(self.opp["counters"]["hand"], 5)

    def test_every_zone_lists_its_objects_with_handles_and_lineage(self):
        objs = by_handle(self.st)
        self.assertEqual(objs[28]["name"], "Mox Ruby")
        self.assertEqual(objs[28]["was"], 27)
        self.assertEqual(objs[31]["name"], "Bilbo, Thief in the Night")
        self.assertEqual(objs[31]["pt"], "2/2")
        self.assertEqual(objs[31]["tags"], ["untapped"])
        self.assertEqual((objs[25]["name"], objs[25]["zone"], objs[25]["was"]), ("Tundra", "lands", 23))
        self.assertEqual([o["name"] for o in self.you["zones"]["graveyard"]], ["Black Lotus", "Time Walk", "Flow State"])
        self.assertEqual(objs[33]["cost"], "1{U}")
        self.assertEqual(objs[34]["notes"], ["payable, not castable now"])

    def test_the_opponents_hand_is_a_count_and_their_board_keeps_its_tags(self):
        self.assertEqual(self.opp["zones"]["hand"], [])
        self.assertEqual(self.opp["hand_hidden"], "5 cards (hidden)")
        e = by_handle(self.st)[42]
        self.assertEqual((e["name"], e["pt"], e["owner"]), ("Emberheart Challenger", "2/2", "OPP"))
        self.assertEqual(e["tags"], ["tapped", "haste", "prowess"])
        self.assertEqual([(o["name"], o["tags"]) for o in self.opp["zones"]["lands"]],
                         [("Mountain", ["tapped"]), ("Mutavault", ["tapped"])])

    def test_mana_stack_combat_plan_and_options(self):
        self.assertTrue(self.you["lines"]["mana available"].startswith("{W:1 U:1 R:1} up to 2 mana"))
        self.assertEqual(self.st["stack"], [])
        self.assertEqual(self.st["combat"], "none")
        self.assertTrue(self.st["plan"]["goal"].startswith("Use Bilbo to recast Time Walk"))
        self.assertTrue(self.st["plan"]["next"].startswith("Pass upkeep without tapping mana"))
        self.assertIn("stated at T3 UPKEEP", self.st["plan"]["stated"])
        self.assertTrue(self.st["facts"].startswith("your next combat"))
        self.assertEqual([o["label"] for o in self.st["options"]][-1], "PASS (do nothing)")
        self.assertEqual(len(self.st["options"]), 4)

    def test_named_blocks_are_kept_and_nothing_is_left_unparsed(self):
        for name in ("GAME HISTORY", "SINCE YOUR LAST DECISION", "REVEALS TO YOU", "OPPONENT CARDS YOU HAVE SEEN",
                     "NOT VISIBLE", "YOUR OPPONENT PROFILE", "YOUR CARD INTERACTIONS"):
            self.assertIn(name, self.st["blocks"])
        self.assertEqual(self.st["unparsed"], [])
        self.assertIn("M68", self.st["archive"])


class RevealsTest(unittest.TestCase):
    def test_a_card_revealed_to_the_seat_is_a_reveal_never_an_opponent_hand_card(self):
        st = ds.parse_prompt(decision("g33-t5-upkeep.jsonl")["prompt"])
        reveals = st["blocks"]["REVEALS TO YOU"]["lines"]
        self.assertTrue(any("Pithing Needle" in line for line in reveals))
        names = [o["name"] for o in ds.visible_objects(st)]
        self.assertNotIn("Pithing Needle", names)
        self.assertEqual(st["players"][1]["zones"]["hand"], [])


class NothingButThePromptTest(unittest.TestCase):
    def test_every_object_the_board_draws_is_named_in_that_decisions_prompt(self):
        for name in ("g33-inspect.jsonl", "g33-set-plan.jsonl", "g33-simulate.jsonl", "g33-t5-upkeep.jsonl",
                     "g33-blockers.jsonl", "mirror-42-start.jsonl", "tournament-2026-07-play.jsonl"):
            with open(os.path.join(FIX, name)) as f:
                rows = [json.loads(line) for line in f if line.strip()]
            for r in rows:
                if r.get("t") != "decision" or not r.get("prompt"):
                    continue
                for o in ds.visible_objects(ds.parse_prompt(r["prompt"])):
                    self.assertIn(o["name"], r["prompt"], name)
                    if o.get("handle") is not None:
                        self.assertIn("@%d" % o["handle"], r["prompt"], name)


class StackAndCombatTest(unittest.TestCase):
    def test_a_spell_on_the_stack_is_an_item_with_its_source_and_controller(self):
        st = ds.parse_prompt(decision("g33-simulate.jsonl")["prompt"])
        self.assertEqual(st["header"]["turn"], 2)
        items = ds.parse_stack("1 not yet resolved (it resolves when both players pass in a row): "
                               "#1 spell Mox Ruby @27 (you): Mox Ruby")
        self.assertEqual(items, [{"index": 1, "kind": "spell", "source": "Mox Ruby @27", "who": "you",
                                  "text": "Mox Ruby", "handles": [27]}])

    def test_the_blockers_ask_keeps_the_decision_text(self):
        st = ds.parse_prompt(decision("g33-blockers.jsonl")["prompt"])
        self.assertIn("declare blockers", st["decision"])
        self.assertIn("Soul-Scar Mage 2/3 @135", st["decision"])
        self.assertIn("Construct Token", [o["name"] for o in st["players"][0]["zones"]["board"]])


class OlderGrammarTest(unittest.TestCase):
    def test_a_july_2026_prompt_parses_into_the_same_shape(self):
        st = ds.parse_prompt(decision("tournament-2026-07-play.jsonl")["prompt"])
        self.assertEqual(st["header"]["turn"], 8)
        you, opp = st["players"]
        self.assertEqual(you["name"], "deepseek-v4-pro")
        self.assertEqual(you["counters"]["life"], 19)
        board = [(o["name"], o["pt"], o["tags"]) for o in you["zones"]["board"]]
        self.assertIn(("Noble Hierarch", "0/1", ["tapped"]), board)
        self.assertEqual(len(you["zones"]["graveyard"]), 5)
        self.assertIn("Emrakul, the Aeons Torn", [o["name"] for o in opp["zones"]["board"]])
        self.assertTrue(st["stack"][0]["text"].startswith("1 pending"))

    def test_an_unknown_block_is_kept_raw_and_never_raises(self):
        st = ds.parse_prompt("== Turn 1 MAIN1 (active: A | priority: you) ==\nSOMETHING NEW: hello\n  more\n"
                             "a stray line\nDECISION: pick\n  1) PASS (do nothing)\nANSWER: call `answer`")
        self.assertEqual(st["blocks"]["SOMETHING NEW"], {"note": None, "text": "hello", "lines": ["more"]})
        self.assertEqual(st["unparsed"], ["a stray line"])
        self.assertEqual(st["options"], [{"n": 1, "label": "PASS (do nothing)"}])
        self.assertEqual(ds.parse_prompt(None)["players"], [])


if __name__ == "__main__":
    unittest.main()
