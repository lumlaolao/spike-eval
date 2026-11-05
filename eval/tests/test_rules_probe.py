#!/usr/bin/env python3
"""Tests for rules_probe.py. Run: python3 eval/tests/test_rules_probe.py

The fixtures are cut from recorded rows (runs/propose/audit2-random/g-kw03, turn 8 MAIN1; runs/propose/e75-haiku-
hard-propose/hard-storm-5bf9f8c3b603, the Rite of Flame refusal; runs/demo/luna-doomsday-painter-s3131, a target ask),
trimmed to the lines the miner reads.
"""

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402
import rules_probe as rp  # noqa: E402

MENU_PROMPT = """== Turn 8 MAIN1 (active: kw03 | priority: you) ==
YOU [kw03] life 20 | poison 0/10 | hand 8 | library 49 (hidden, order unknown) | graveyard 1 | exile 0 | lands played this turn 0/1 | mana pool empty | spells cast this turn 0
  hand(8): Ajani, Sleeper Agent 1{G}{G/W/P}{W} @1 (not castable now), Audacious Swap 3{R} @2 (need 1 more R), Changeling Hero 4{W} 4/4 @5 (need 3 more mana), Hands of Binding 1{U} @7 (payable, not castable now), Opt {U} @8 (castable), Counterspell {U}{U} @9 (need 1 more U), Forest @14, Mountain @18
  board: (no nonland permanents)
  lands: 2 untapped / 2 total (Plains x1 [untapped @13(was @4)], Island x1 [untapped @17(was @10)])
  mana available: {W:1 U:1} up to 2 mana (2 untapped sources; each counts its mana under every colour it can make, so the colours add up to more than can be spent)
OPP [kw04-ai] life 20 | poison 0/10 | hand 5 | library 50 (hidden, order unknown) | graveyard 0 | exile 0 | lands played this turn 0/1 | mana pool empty | spells cast this turn 0
  board: Adventurer's Airship [untapped] [flying] @16
STACK (engine, top first): (empty)
SPENT THIS TURN: land drop AVAILABLE (0/1) | spells cast 0 | attackers declared none

DECISION: You have priority (MAIN1, your main phase). Choose ONE action:
  1) CAST Opt @8 {U} [Scry 1. Draw a card.]
  2) ACTIVATE Plains @13 [{T}: Add {W}.]
  3) PLAY LAND Forest @14
  4) PLAY LAND Mountain @18
  5) PASS (do nothing)
ANSWER: call `answer` with `option`.
"""

MENU_ROW = {
    "t": "decision", "seat": "kw03", "turn": 8, "phase": "MAIN1", "type": "play", "n_options": 5,
    "options": ["CAST Opt @8 {U} [Scry 1. Draw a card.]", "ACTIVATE Plains @13 [{T}: Add {W}.]",
                "PLAY LAND Forest @14", "PLAY LAND Mountain @18", "PASS (do nothing)"],
    "prompt": MENU_PROMPT,
}

PROPOSE_PROMPT = """== Turn 1 END_OF_TURN (active: Solver | priority: you) ==
YOU [Solver] life 20 | poison 0/10 | hand 2 | library 4 (hidden, order unknown) | graveyard 0 | exile 0 | lands played this turn 0/1 | mana pool empty | spells cast this turn 0
  hand(2): Shock {R} @1, Rite of Flame {R} @3
  lands: 2 untapped / 2 total (Mountain x2 [untapped @11 @13])
  mana available: {R:2} up to 2 mana (2 untapped sources)
OPP [Opponent] life 24 | poison 0/10 | hand 0 | library 3 (hidden, order unknown) | graveyard 0 | exile 0 | lands played this turn 0/1 | mana pool empty | spells cast this turn 0
  board: (no nonland permanents)
STACK (engine, top first): (empty)

DECISION: You have priority (END_OF_TURN, instant-speed window). You are not shown the legal plays. Propose ONE.
"""

PROPOSE_ROW = {
    "t": "decision", "seat": "Solver", "turn": 1, "phase": "END_OF_TURN", "type": "play", "n_options": 3,
    "options": ["CAST Shock @1 {R} [Shock deals 2 damage to any target.]",
                "ACTIVATE Mountain @11 [{T}: Add {R}.]", "PASS (do nothing)"],
    "prompt": PROPOSE_PROMPT,
    "proposals": [
        {"action": None, "verdict": "format", "error": "FORMAT: there is no numbered list (you gave option)."},
        {"action": {"verb": "cast", "card": "@3"}, "verdict": "illegal", "category": "timing",
         "error": "ILLEGAL (timing): Rite of Flame @3 can be cast only when you could cast a sorcery: in your own "
                  "main phase, with an empty stack."},
        {"action": {"verb": "pass"}, "verdict": "accepted"},
    ],
}

TARGET_PROMPT = """== Turn 4 MAIN1 (active: Luna | priority: you) ==
YOU [Luna] life 18 | poison 0/10 | hand 3 | library 40 | graveyard 0 | exile 0 | lands played this turn 1/1 | mana pool empty | spells cast this turn 0
  board: Grizzly Bears 2/2 [untapped] @21 (was @7), Bola Slinger 2/2 [untapped] [sick] @36 (was @10)
  lands: 3 untapped / 3 total (Mountain x3 [untapped @11 @12 @13])
OPP [Painter] life 20 | poison 0/10 | hand 4 | library 40 | graveyard 0 | exile 0 | lands played this turn 0/1 | mana pool empty | spells cast this turn 0
  board: Gladecover Scout 1/1 [untapped] [hexproof] @31, Llanowar Elves 1/1 [untapped] @32
  lands: 2 untapped / 2 total (Forest x2 [untapped @33 @34])
STACK (engine, top first): (empty)

DECISION: Choose the TARGET for Shock @5 {R} [Shock deals 2 damage to any target.] (the spell/ability resolves against whatever you pick).
Options:
  1) Grizzly Bears 2/2 [untapped] @21 [Battlefield, yours]
  2) Llanowar Elves 1/1 [untapped] @32 [Battlefield, opponent's]
  3) you (Luna)
  4) opponent (Painter)
  5) CANCEL: do not take this action; go back to the action menu
"""

TARGET_ROW = {
    "t": "decision", "seat": "Luna", "turn": 4, "phase": "MAIN1", "type": "target", "n_options": 4,
    "options": ["Grizzly Bears 2/2 [untapped] @21 [Battlefield, yours]",
                "Llanowar Elves 1/1 [untapped] @32 [Battlefield, opponent's]", "you (Luna)", "opponent (Painter)"],
    "prompt": TARGET_PROMPT,
}

PROV = {"source_run": "runs/propose/audit2-random", "source_file": "g-kw03/game.jsonl",
        "harness_epoch": "harness-epoch-74", "engine_pin": "34a8662b7966", "engine_pin_source": "game-line",
        "observation_version": "observation-faithful-v42"}


def by(items, verb, card):
    hits = [i for i in items if i["proposal"].get("verb") == verb and i["proposal"].get("card") == card]
    assert len(hits) == 1, (verb, card, [i["proposal"] for i in items])
    return hits[0]


class TestParsing(unittest.TestCase):
    def test_a_hand_name_with_a_comma_keeps_its_cost_handle_and_tag(self):
        hand = rp.parse_hand(MENU_PROMPT)
        ajani = hand[0]
        self.assertEqual((ajani["name"], ajani["cost"], ajani["handle"], ajani["tag"]),
                         ("Ajani, Sleeper Agent", "1{G}{G/W/P}{W}", "@1", "not castable now"))
        self.assertEqual(rp.mana_value(ajani["cost"]), 4)
        hero = [h for h in hand if h["handle"] == "@5"][0]
        self.assertEqual((hero["cost"], hero["pt"], hero["tag"]), ("4{W}", "4/4", "need 3 more mana"))
        forest = [h for h in hand if h["handle"] == "@14"][0]
        self.assertEqual((forest["name"], forest["cost"], forest["tag"]), ("Forest", None, None))

    def test_an_earlier_handle_in_the_hand_line_does_not_merge_two_cards(self):
        # runs/propose/audit-gauntlet-random: "Brighthearth Banneret 1{R} 1/1 @82 (was @26) (need 1 more R), Plains @9"
        p = MENU_PROMPT.replace("Opt {U} @8 (castable)", "Brighthearth Banneret 1{R} 1/1 @82 (was @26) (need 1 more R)")
        hand = rp.parse_hand(p)
        bb = [h for h in hand if h["handle"] == "@82"][0]
        self.assertEqual((bb["name"], bb["cost"], bb["tag"]), ("Brighthearth Banneret", "1{R}", "need 1 more R"))
        self.assertNotIn("@26", [h["handle"] for h in hand])

    def test_a_zero_cost_spell_is_a_cast_not_a_land_when_its_script_is_known(self):
        # Mishra's Bauble has no cost in the hand line, like a land; its card script says it is an artifact.
        p = MENU_PROMPT.replace("Forest @14,", "Mishra's Bauble @3, Forest @14,")
        look = {"Mishra's Bauble": {"types": "Artifact", "keywords": [], "oracle": ""}}.get
        items = rp.items_from_row(dict(MENU_ROW, prompt=p), PROV, 7, look)
        self.assertEqual(by(items, "cast", "@3")["label"], "illegal")
        self.assertFalse([i for i in items if i["proposal"] == {"verb": "play_land", "card": "@3",
                                                                "name": "Mishra's Bauble"}])

    def test_an_option_names_its_card_without_its_power_and_toughness(self):
        # runs/puzzles/hard-gen3-gate, tokens_020bf6de5a31: the pilot's question read "Siege-Gang Commander 2/2".
        self.assertEqual(rp._option_name("ACTIVATE Siege-Gang Commander 2/2 @11 [{1}{R}, Sacrifice a Goblin: ...]"),
                         "Siege-Gang Commander")
        self.assertEqual(rp._option_name("CAST Opt @8 {U} [Scry 1.]"), "Opt")

    def test_the_window_reads_turn_owner_phase_stack_land_drop_and_mana(self):
        w = rp.parse_window(MENU_PROMPT)
        self.assertEqual((w["own_turn"], w["phase"], w["stack_empty"], w["lands_played"], w["land_max"], w["mana"]),
                         (True, "MAIN1", True, 0, 1, 2))
        self.assertTrue(w["sorcery_speed"])
        w2 = rp.parse_window(PROPOSE_PROMPT)
        self.assertFalse(w2["sorcery_speed"])
        # "up to N mana" already counts the floating pool (runs/puzzles/hard-gen2-gate, grave_8711cb3bf800 row 122).
        p = MENU_PROMPT.replace("mana pool empty | spells cast this turn 0\n  hand", "mana pool {B:9} | spells cast "
                                "this turn 0\n  hand").replace("up to 2 mana", "up to 11 mana")
        self.assertEqual(rp.parse_window(p)["mana"], 11)

    def test_the_state_given_to_a_model_carries_no_legality_tag_and_no_menu(self):
        s = rp.state_text(MENU_PROMPT)
        for leak in ("castable", "need 1 more", "need 3 more", "DECISION", "CAST Opt", "PLAY LAND", "Options:"):
            self.assertNotIn(leak, s)
        self.assertIn("Ajani, Sleeper Agent 1{G}{G/W/P}{W} @1", s)
        self.assertIn("Opt {U} @8", s)


class TestMining(unittest.TestCase):
    def test_the_seats_own_notes_are_not_in_the_state(self):
        # Seventeen mined states quoted the seat's last play or plan, which can name the answer.
        p = MENU_PROMPT.replace("SPENT THIS TURN", "YOUR PLAN (yours, stated at T2 MAIN1): GOAL: cast Opt NEXT: x\n"
                                "THE PLAY YOU CHOSE MOST RECENTLY (T8): CAST Opt @8 {U}. What you wrote when you chose "
                                "it: \"Opt is castable\"\n(The state above is current; if it has changed since you "
                                "chose that play, decide on the current state.)\n\nSPENT THIS TURN")
        s = rp.state_text(p)
        for leak in ("YOUR PLAN", "THE PLAY YOU CHOSE", "What you wrote", "The state above is current"):
            self.assertNotIn(leak, s)
        self.assertIn("SPENT THIS TURN", s)

    def test_every_non_pass_menu_play_is_a_legal_item_and_a_card_off_the_menu_an_illegal_one(self):
        items = rp.items_from_row(MENU_ROW, PROV, 7)
        self.assertEqual(by(items, "cast", "@8")["label"], "legal")
        self.assertEqual(by(items, "play_land", "@14")["label"], "legal")
        self.assertEqual(by(items, "activate", "@13")["label"], "legal")
        self.assertTrue(by(items, "activate", "@13")["mana_ability"])
        for h in ("@1", "@2", "@5", "@7", "@9"):
            it = by(items, "cast", h)
            self.assertEqual((it["label"], it["label_source"]), ("illegal", "menu-absence"), h)
        self.assertFalse(any(i["proposal"].get("verb") == "pass" for i in items))
        # A land in hand is never proposed as a cast, nor a spell as a land play: those are FORMAT/type questions.
        self.assertFalse(any(i["proposal"] == {"verb": "cast", "card": "@14"} for i in items))

    def test_the_inferred_category_follows_the_engines_order_of_checks(self):
        items = rp.items_from_row(MENU_ROW, PROV, 7)
        self.assertEqual(by(items, "cast", "@2")["category"], "cost")        # need 1 more R, in a sorcery window
        self.assertEqual(by(items, "cast", "@5")["category"], "cost")
        self.assertEqual(by(items, "cast", "@7")["category"], "restriction-or-target")  # payable, own main, empty stack
        self.assertEqual(by(items, "cast", "@1")["category"], "unknown")
        self.assertTrue(all(by(items, "cast", h)["category_source"] == "inferred" for h in ("@1", "@2", "@5", "@7")))

    def test_the_difficulty_separates_a_total_mana_shortfall_from_a_colour_shortfall(self):
        items = rp.items_from_row(MENU_ROW, PROV, 7)
        self.assertEqual(by(items, "cast", "@5")["difficulty"], "trivial")   # 5 mana with 2 available
        self.assertEqual(by(items, "cast", "@2")["difficulty"], "trivial")   # 4 mana with 2 available
        self.assertEqual(by(items, "cast", "@9")["difficulty"], "subtle")    # 2 mana with 2: only a colour short
        self.assertEqual(by(items, "cast", "@7")["difficulty"], "subtle")    # payable in its own main phase

    def test_engine_verdicts_become_items_with_the_engines_category_and_sentence(self):
        items = rp.items_from_row(PROPOSE_ROW, PROV, 3)
        rite = by(items, "cast", "@3")
        self.assertEqual((rite["label"], rite["label_source"], rite["category"], rite["category_source"]),
                         ("illegal", "engine-verdict", "timing", "engine"))
        self.assertIn("only when you could cast a sorcery", rite["reason"])
        self.assertEqual(by(items, "cast", "@1")["label"], "legal")
        self.assertEqual(len([i for i in items if i["proposal"].get("card") == "@3"]), 1)   # not again from absence

    def test_a_second_land_of_a_name_the_menu_offers_is_not_an_illegal_item(self):
        # The menu lists one PLAY LAND a land name (runs/demo/luna-elves-goblins-s9191 row 63: Mountain @18 offered,
        # @20 @21 @25 not), so a copy off the menu is as playable as the one on it.
        p = MENU_PROMPT.replace("Forest @14, Mountain @18", "Forest @14, Mountain @18, Mountain @25")
        items = rp.items_from_row(dict(MENU_ROW, prompt=p), PROV, 7)
        self.assertFalse([i for i in items if i["proposal"]["card"] == "@25"])

    def test_a_card_chosen_earlier_in_the_same_window_is_not_an_illegal_item(self):
        # A play that failed to pay is not offered again in its window (LlmSpikeGui failedInWindow): its absence from a
        # later menu of that window is the harness's, not the engine's. Hands of Binding @7 chosen in row 5, still in
        # hand and off the menu in row 7.
        first = dict(MENU_ROW, options=["CAST Hands of Binding @7 {U} [...]"] + MENU_ROW["options"], choice=0)
        items = rp.mine_rows([(5, first), (7, MENU_ROW)], PROV)
        self.assertFalse([i for i in items if i["provenance"]["row"] == 7 and i["proposal"]["card"] == "@7"])
        self.assertTrue([i for i in items if i["provenance"]["row"] == 7 and i["proposal"]["card"] == "@5"])
        # In another window (the next turn) the same card off the menu is an item again.
        later = dict(MENU_ROW, prompt=MENU_PROMPT.replace("== Turn 8 MAIN1", "== Turn 10 MAIN1"))
        items = rp.mine_rows([(5, first), (9, later)], PROV)
        self.assertTrue([i for i in items if i["provenance"]["row"] == 9 and i["proposal"]["card"] == "@7"])

    def test_a_land_off_the_menu_after_the_land_drop_is_a_land_drop_item(self):
        p = MENU_PROMPT.replace("lands played this turn 0/1", "lands played this turn 1/1")
        row = dict(MENU_ROW, prompt=p, options=[o for o in MENU_ROW["options"] if "PLAY LAND" not in o])
        items = rp.items_from_row(row, PROV, 7)
        f = by(items, "play_land", "@14")
        self.assertEqual((f["label"], f["category"], f["difficulty"]), ("illegal", "land drop", "trivial"))

    def test_a_permanent_the_target_ask_left_out_is_an_illegal_target(self):
        items = rp.items_from_row(TARGET_ROW, PROV, 2)
        scout = by(items, "target", "@31")
        self.assertEqual((scout["label"], scout["category"], scout["difficulty"]), ("illegal", "target", "subtle"))
        self.assertTrue(scout["keyword_visible"])                       # [hexproof] is on the board line
        self.assertEqual(by(items, "target", "@32")["label"], "legal")
        self.assertEqual(scout["proposal"]["source"], "Shock @5")
        self.assertIn("deals 2 damage to any target", rp.render(scout))     # the source's text is in the question
        self.assertNotIn("Options:", scout["state"])
        # Lands were not offered either, but a land is a legal "any target"? No: any target is creature, player,
        # planeswalker or battle, so the lands are illegal targets too, and plain (no keyword) ones.
        self.assertEqual(by(items, "target", "@33")["label"], "illegal")
        self.assertFalse(by(items, "target", "@33")["keyword_visible"])
        self.assertEqual(by(items, "target", "@33")["difficulty"], "trivial")
        # "(was @7)" names the same Bears by an earlier handle: no item, and the Slinger keeps its own name.
        self.assertFalse([i for i in items if i["proposal"]["card"] in ("@7", "@10")])
        self.assertEqual(by(items, "target", "@36")["proposal"]["name"], "Bola Slinger")

    def test_a_permanent_left_out_of_a_player_only_target_ask_is_trivial(self):
        # A Blood Artist trigger targets a player (runs/puzzles/frontier5, hard-drain2-516c13f84175): every creature
        # on the board is an illegal target, and saying so needs only the trigger's text.
        p = TARGET_PROMPT.replace("Choose the TARGET for Shock @5 {R} [Shock deals 2 damage to any target.]",
                                  "Choose the TARGET for Blood Artist @21 [target player loses 1 life]")
        row = dict(TARGET_ROW, prompt=p, options=["you (Luna)", "opponent (Painter)"])
        items = rp.items_from_row(row, PROV, 2)
        self.assertEqual({i["difficulty"] for i in items}, {"trivial"})
        self.assertEqual({i["label"] for i in items}, {"illegal"})

    def test_an_equipment_left_out_of_an_equip_target_is_trivial_and_attachments_are_not_objects(self):
        # An equip ask (runs/puzzles/hard-gen2-gate, the equipment family): creatures are offered, an Equipment is not.
        p = TARGET_PROMPT.replace(
            "  board: Grizzly Bears 2/2 [untapped] @21 (was @7), Bola Slinger 2/2 [untapped] [sick] @36 (was @10)",
            "  board: Fencing Ace 14/3 [untapped] [has attached: Bonesplitter @22, Cranial Plating @23] @21, "
            "Bonesplitter [untapped] [attached to Fencing Ace 14/3 @21] @22, Loxodon Warhammer [untapped] @9, "
            "Alora, Merry Thief 4/3 [untapped] [+1/+1 x1] @40")
        row = dict(TARGET_ROW, prompt=p, options=["Fencing Ace 14/3 [untapped] [has attached: Bonesplitter @22] @21 "
                                                  "[Battlefield, yours]",
                                                  "Llanowar Elves 1/1 [untapped] @32 [Battlefield, opponent's]"])
        items = rp.items_from_row(row, PROV, 2)
        self.assertEqual(by(items, "target", "@22")["difficulty"], "trivial")
        self.assertEqual(by(items, "target", "@9")["difficulty"], "trivial")
        self.assertEqual(by(items, "target", "@31")["difficulty"], "subtle")      # the hexproof Scout
        self.assertFalse([i for i in items if i["proposal"]["card"] == "@23"])    # named only inside Ace's brackets
        self.assertEqual(by(items, "target", "@40")["proposal"]["name"], "Alora, Merry Thief")
        self.assertEqual(by(items, "target", "@40")["difficulty"], "subtle")

    def test_a_target_ask_whose_candidates_depend_on_an_earlier_choice_is_not_mined(self):
        # X announced, a second target, "another target", a sacrifice in the cost: an earlier choice of the same play
        # narrows the list, so a permanent left out may be a legal target of the card (Searing Blaze, Mutiny, Blaze).
        for source in ("Blaze @5 {X}{R} [Blaze deals X damage to any target.] (you announced X = 1)",
                       "Searing Blaze @5 {R}{R} [Searing Blaze deals 1 damage to target player and 1 damage to target "
                       "creature that player controls.]",
                       "Mutiny @5 {R} [Target creature an opponent controls deals damage equal to its power to another "
                       "target creature that player controls.]",
                       "Goblin Bombardment @5 [Sacrifice a creature: Goblin Bombardment deals 1 damage to any "
                       "target.]"):
            p = TARGET_PROMPT.replace("Shock @5 {R} [Shock deals 2 damage to any target.]", source)
            self.assertEqual(rp.items_from_row(dict(TARGET_ROW, prompt=p), PROV, 2), [], source)

    def test_one_position_reached_with_other_handles_and_notes_is_one_position(self):
        a = by(rp.items_from_row(MENU_ROW, PROV, 7), "cast", "@2")
        p = MENU_PROMPT.replace("@2 (need", "@42 (need").replace("SPENT THIS TURN", "YOUR PLAN: x\nSPENT THIS TURN")
        b = by(rp.items_from_row(dict(MENU_ROW, prompt=p), PROV, 7), "cast", "@42")
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(rp.position_key(a), rp.position_key(b))
        c = by(rp.items_from_row(MENU_ROW, PROV, 7), "cast", "@5")
        self.assertNotEqual(rp.position_key(a), rp.position_key(c))

    def test_items_carry_provenance_and_a_stable_id(self):
        a = rp.items_from_row(MENU_ROW, PROV, 7)
        b = rp.items_from_row(MENU_ROW, PROV, 7)
        self.assertEqual([i["id"] for i in a], [i["id"] for i in b])
        self.assertEqual(len({i["id"] for i in a}), len(a))
        for i in a:
            self.assertEqual(i["schema"], rp.SCHEMA)
            for k in ("harness_epoch", "engine_pin", "source_run", "source_file", "row"):
                self.assertIn(k, i["provenance"])

    def test_the_same_state_and_play_from_two_rows_is_one_item(self):
        items = rp.dedupe(rp.items_from_row(MENU_ROW, PROV, 7) + rp.items_from_row(MENU_ROW, PROV, 9))
        self.assertEqual(len(items), len(rp.items_from_row(MENU_ROW, PROV, 7)))


class TestRunsAndBalance(unittest.TestCase):
    def test_mining_a_run_directory_reads_the_game_line_for_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            g = Path(d) / "g1"
            g.mkdir()
            with open(g / "game-1-1.jsonl", "w") as fh:
                fh.write(json.dumps(MENU_ROW) + "\n")
                fh.write(json.dumps({"t": "game", "harness_epoch": "harness-epoch-74", "engine_sha": "unknown",
                                     "observation_version": "observation-faithful-v42"}) + "\n")
            items = rp.mine([d], repo_pin="abc123")
            self.assertTrue(items)
            p = items[0]["provenance"]
            self.assertEqual((p["harness_epoch"], p["engine_pin"], p["engine_pin_source"]),
                             ("harness-epoch-74", "abc123", "repo-FORGE_COMMIT-at-mining"))

    def test_a_written_set_stores_each_state_once_and_reads_back_whole(self):
        items = rp.items_from_row(MENU_ROW, PROV, 7)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "items.jsonl"
            rp._write(items, out)
            self.assertEqual(out.read_text().count(MENU_PROMPT.split("\n")[1][:40]), 1)
            self.assertEqual(rp._read(out), [dict(i) for i in items])

    def test_the_card_index_expands_a_forge_home_written_with_a_variable(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "forge-gui/res/cardsfolder/r/rite_of_flame.txt"
            f.parent.mkdir(parents=True)
            f.write_text("Name:Rite of Flame\nManaCost:R\nTypes:Sorcery\nOracle:Add {R}{R}.\n")
            os.environ["RP_TEST_HOME"] = d
            try:
                look = rp.card_index("$RP_TEST_HOME")
            finally:
                del os.environ["RP_TEST_HOME"]
            self.assertEqual(look("Rite of Flame")["types"], "Sorcery")
            self.assertIsNone(look("No Such Card"))
            # With the card script known, a sorcery off the menu outside a sorcery window is a timing item.
            row = {k: v for k, v in PROPOSE_ROW.items() if k != "proposals"}
            it = by(rp.items_from_row(row, PROV, 3, look), "cast", "@3")
            self.assertEqual((it["label_source"], it["category"], it["difficulty"]),
                             ("menu-absence", "timing", "trivial"))
            self.assertEqual(by(rp.items_from_row(row, PROV, 3), "cast", "@3")["category"], "unknown")

    def test_balance_caps_each_label_and_category_and_is_deterministic(self):
        def it(i, label, cat=None, diff=None, mana=False, state=None):
            return {"id": i, "kind": "play", "label": label, "category": cat, "difficulty": diff, "mana_ability": mana,
                    "state": state or f"== Turn 1 MAIN1 {i}", "proposal": {"verb": "cast", "card": "@1", "name": i}}
        items = []
        for k in range(30):
            items.append(it(f"l{k}", "legal", mana=k % 2 == 0))
            items.append(it(f"c{k}", "illegal", "cost", "trivial" if k < 20 else "subtle"))
        items.append(it("t0", "illegal", "timing", "trivial"))
        # The same position again, with another handle: one position, so at most one item.
        items.append(dict(it("t0", "illegal", "timing", "trivial"), id="t0-again",
                          proposal={"verb": "cast", "card": "@9", "name": "t0"}))
        a = rp.balance(items, per_cell=5, seed=1)
        self.assertEqual(a, rp.balance(items, per_cell=5, seed=1))
        self.assertEqual(sum(1 for i in a if i["category"] == "cost" and i["difficulty"] == "trivial"), 5)
        self.assertEqual(sum(1 for i in a if i["category"] == "cost" and i["difficulty"] == "subtle"), 5)
        self.assertEqual(sum(1 for i in a if i["category"] == "timing"), 1)
        self.assertFalse(any(i.get("mana_ability") for i in a))     # mana abilities are left out by default
        legal = sum(1 for i in a if i["label"] == "legal")
        self.assertEqual(legal, 11)                                  # as many legal as illegal


class TestScoring(unittest.TestCase):
    def items(self):
        base = {"proposal": {"verb": "cast", "card": "@1"}, "state": ""}
        return [dict(base, id="a", label="legal", category=None),
                dict(base, id="b", label="legal", category=None),
                dict(base, id="c", label="illegal", category="timing"),
                dict(base, id="d", label="illegal", category="cost")]

    def test_balanced_accuracy_has_a_floor_of_one_half_and_a_missing_answer_is_wrong(self):
        s = rp.score(self.items(), {"a": {"legal": True}, "b": {"legal": True}, "c": {"legal": True}})
        self.assertEqual(s["accuracy"], 0.5)
        self.assertEqual(s["balanced_accuracy"], 0.5)
        self.assertEqual(s["floors"]["always_legal_balanced"], 0.5)
        self.assertEqual(s["missing"], 1)
        self.assertEqual(s["n"], 4)
        self.assertEqual(len(s["accuracy_ci"]), 2)

    def test_the_state_reading_floor_catches_the_trivial_negatives_and_no_subtle_one(self):
        items = rp.items_from_row(MENU_ROW, PROV, 7)
        h = {i["id"]: rp.heuristic(i) for i in items}
        self.assertFalse(h[by(items, "cast", "@5")["id"]]["legal"])     # 5 mana with 2
        self.assertTrue(h[by(items, "cast", "@9")["id"]]["legal"])      # Counterspell: only a colour short
        self.assertTrue(h[by(items, "cast", "@8")["id"]]["legal"])
        p = MENU_PROMPT.replace("lands played this turn 0/1", "lands played this turn 1/1")
        land = by(rp.items_from_row(dict(MENU_ROW, prompt=p), PROV, 7), "play_land", "@14")
        self.assertFalse(rp.heuristic(land)["legal"])
        bears = MENU_PROMPT.replace("Opt {U} @8 (castable)", "Grizzly Bears 1{G} 2/2 @8 (payable, not castable now)")
        bears = bears.replace("(active: kw03", "(active: kw04-ai")
        it = by(rp.items_from_row(dict(MENU_ROW, prompt=bears, options=[]), PROV, 7), "cast", "@8")
        self.assertFalse(rp.heuristic(it)["legal"])                    # a creature on the opponent's turn
        s = rp.score(items, {i["id"]: {"legal": i["label"] == "legal"} for i in items})
        self.assertEqual(s["balanced_accuracy"], 1.0)
        self.assertIn("state_reading_balanced", s["floors"])
        self.assertLess(s["floors"]["state_reading_balanced"], 1.0)

    def test_stats_report_the_floor_and_the_cells_over_positions(self):
        items = rp.items_from_row(MENU_ROW, PROV, 7)
        s = rp.stats(items, floor=True)
        cells = s["illegal_positions_by_cell"]
        self.assertEqual(cells["play / cost (inferred) / trivial"], 2)     # Audacious Swap, Changeling Hero
        self.assertEqual(sum(cells.values()), s["illegal_positions"])
        f = s["floor_by_stratum"]
        self.assertEqual(f["illegal play trivial"], {"n": 3, "floor_correct": 3})
        self.assertIn("balanced_accuracy", s["floor"])
        self.assertEqual(s["subtle_positions_by_source"], {"audit2-random": 2})   # Counterspell, Hands of Binding

    def test_the_floor_and_balance_draw_on_the_same_positions(self):
        # Two abilities of one card are one position (the handle is not in the key); when the mana ability comes
        # first, the floor once dropped the position that balance kept.
        legal = rp.items_from_row(MENU_ROW, PROV, 7)
        mana = by(legal, "activate", "@13")
        other = dict(mana, id="0" + mana["id"][1:], mana_ability=False, option="ACTIVATE Plains @13 [{1}: x]")
        items = [mana, other] + [i for i in legal if i is not mana]
        pool = rp.position_pool(items)
        self.assertIn(other["id"], [i["id"] for i in pool])
        self.assertNotIn(mana["id"], [i["id"] for i in pool])
        self.assertEqual(rp.floor_report(items)[0]["positions"], len(pool))
        self.assertTrue({i["id"] for i in rp.balance(items, 100, 1)} <= {i["id"] for i in pool})

    def test_a_pilot_is_scored_on_the_items_the_current_miner_reproduces(self):
        # Matched by file, row, verb and handle: the same label, and a name that is a name (the first miner glued a
        # hand entry's "(was @N)" tail into it).
        old = rp.items_from_row(MENU_ROW, PROV, 7)
        swap, hero, opt = by(old, "cast", "@2"), by(old, "cast", "@5"), by(old, "cast", "@8")
        relabelled = dict(opt, label="illegal")
        garbled = dict(hero, proposal=dict(hero["proposal"], name="Changeling Hero 4{W} @5 (was @3), Forest"))
        gone = dict(swap, proposal=dict(swap["proposal"], card="@99"))
        kept = rp.reproduced([swap, relabelled, garbled, gone], old)
        self.assertEqual([i["id"] for i in kept], [swap["id"]])

    def test_the_category_is_scored_on_engine_labelled_illegal_items_it_called_illegal(self):
        items = self.items()
        for i in items:
            i["category_source"] = "engine" if i["category"] else None
        s = rp.score(items, {"a": {"legal": True}, "b": {"legal": False}, "c": {"legal": False, "category": "timing"},
                             "d": {"legal": False, "category": "timing"}})
        self.assertEqual(s["category_accuracy"], 0.5)
        self.assertEqual(s["category_n"], 2)


class TestAsk(unittest.TestCase):
    def test_the_cli_lane_takes_a_claude_cli_spec_of_an_allowed_model_with_its_opt_in(self):
        on = {"CLAUDE_CLI_SEAT": "1"}
        self.assertEqual(rp.check_cli_lane("claude-cli:claude-haiku-5-5", env=on), "claude-haiku-5-5")
        for spec, env in (("claude-cli:claude-haiku-5-5", {}),          # no opt-in
                          ("haiku", on),                                # the retired bare-name lane
                          ("cli:haiku", on),                            # a misspelled lane
                          ("claude-cli:claude-opus-5-5", on),           # not on the list
                          ("claude-cli:opus", on),
                          ("openrouter:openai/gpt-6-luna", on)):        # not the CLI lane at all
            with self.assertRaises(SystemExit, msg=spec):
                rp.check_cli_lane(spec, env=env)

    def test_the_child_inherits_no_api_key_provider_switch_model_or_session_variable(self):
        parent = {"PATH": "/bin", "HOME": "/root", "HTTPS_PROXY": "http://p", "SSL_CERT_FILE": "/ca",
                  "CLAUDE_CODE_OAUTH_TOKEN": "oauth", "CLAUDE_CONFIG_DIR": "/c", "LC_ALL": "C",
                  "ANTHROPIC_API_KEY": "sk", "ANTHROPIC_AUTH_TOKEN": "t", "CLAUDE_CODE_USE_BEDROCK": "1",
                  "AWS_ACCESS_KEY_ID": "a", "CLAUDE_CODE_USE_VERTEX": "1", "MAX_THINKING_TOKENS": "9",
                  "ANTHROPIC_MODEL": "m", "CLAUDE_CODE_SESSION": "s", "CLAUDECODE": "1", "OPENROUTER_API_KEY": "o"}
        env = rp.child_env(parent)
        self.assertEqual(env, {k: parent[k] for k in ("PATH", "HOME", "HTTPS_PROXY", "SSL_CERT_FILE",
                                                       "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR", "LC_ALL")})
        allowed = rp.child_env(dict(parent, CLAUDE_CLI_ALLOW_API_KEY="1"))
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "AWS_ACCESS_KEY_ID"):
            self.assertIn(k, allowed)
        for k in ("MAX_THINKING_TOKENS", "ANTHROPIC_MODEL", "CLAUDE_CODE_SESSION", "OPENROUTER_API_KEY"):
            self.assertNotIn(k, allowed)

    def test_ask_runs_every_claude_child_with_the_same_filtered_environment(self):
        # A fake `claude` on PATH appends the environment it was given and answers like the CLI. Two items: the second
        # child once got the first reply's JSON envelope as its environment (pr-review of #350, round 2).
        items = rp.items_from_row(PROPOSE_ROW, PROV, 3)[:2]
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "claude"
            reply = json.dumps({"result": json.dumps({"legal": False, "category": "timing"}), "total_cost_usd": 0.001})
            fake.write_text("#!%s\nimport json, os, sys\nsys.stdin.read()\n"
                            "open(%r, 'a').write(json.dumps(dict(os.environ)) + '\\n')\nprint(%r)\n"
                            % (sys.executable, str(Path(d) / "env.jsonl"), reply))
            fake.chmod(0o755)
            out = Path(d) / "answers.jsonl"
            environ = {"PATH": d + os.pathsep + os.environ.get("PATH", ""), "HOME": d, "CLAUDE_CLI_SEAT": "1",
                       "ANTHROPIC_API_KEY": "sk-should-not-reach", "MAX_THINKING_TOKENS": "1"}
            rp.ask(items, "claude-cli:claude-haiku-5-5", str(out), environ=environ)
            seen = [json.loads(ln) for ln in (Path(d) / "env.jsonl").read_text().splitlines()]
            self.assertEqual(len(seen), 2)
            self.assertEqual(seen[0], seen[1])
            for k in ("ANTHROPIC_API_KEY", "MAX_THINKING_TOKENS", "CLAUDE_CLI_SEAT"):
                self.assertNotIn(k, seen[0])
            self.assertEqual(seen[0]["PATH"], environ["PATH"])
            rows = [json.loads(ln) for ln in out.read_text().splitlines()]
            self.assertEqual([r["id"] for r in rows], [i["id"] for i in items])
            for r in rows:
                self.assertEqual((r["answer"], r["model"], r["cost_usd"]),
                                 ({"legal": False, "category": "timing"}, "claude-cli:claude-haiku-5-5", 0.001))

    def test_the_kept_environment_lists_equal_the_cli_bridges(self):
        java = (evalpath.REPO / "forge-overlay/forge-gui-desktop/src/main/java/forge/spike"
                / "ClaudeCliBridge.java").read_text(encoding="utf-8")

        def strings(name, end):
            body = java[java.index(name):]
            return re.findall(r'"([A-Z_a-z*]+)"', body[:body.index(end)])
        self.assertEqual(sorted(strings("KEEP_ENV =", ")));")), sorted(rp.KEEP_ENV))
        self.assertEqual(strings("API_KEY_ENV =", ");"), list(rp.API_KEY_ENV))
        provider = strings("PROVIDER_ENV =", "));")
        java_map, key = {}, None
        for w in provider:
            if w.startswith("CLAUDE_CODE_USE_"):
                key = w
                java_map[key] = []
            else:
                java_map[key].append(w.replace("*", ""))   # VERTEX_REGION_* is a prefix
        self.assertEqual(java_map, {k: list(v) for k, v in rp.PROVIDER_ENV.items()})

    def test_a_reply_is_read_from_its_json_object_and_anything_else_is_no_answer(self):
        self.assertEqual(rp.parse_reply('noise {"legal": false, "category": "timing"} tail'),
                         {"legal": False, "category": "timing"})
        self.assertIsNone(rp.parse_reply("I think it is legal"))
        self.assertIsNone(rp.parse_reply('{"legal": "yes"}'))

    def test_the_question_names_the_play_and_the_categories_and_never_the_label(self):
        it = by(rp.items_from_row(PROPOSE_ROW, PROV, 3), "cast", "@3")
        q = rp.render(it)
        self.assertIn("cast Rite of Flame @3", q)
        for c in rp.CATEGORIES:
            self.assertIn(c, q)
        self.assertNotIn("only when you could cast a sorcery", q)
        self.assertNotIn("ILLEGAL", q)


if __name__ == "__main__":
    unittest.main()
