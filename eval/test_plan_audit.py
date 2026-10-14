"""plan_audit: the fixtures are real lines from the game that exposed prose plan execution
(docs/harness/ARCHITECTURE-REVIEW.md, problem 5)."""
import json
import os
import tempfile
import unittest

import plan_audit as pa

LOG = """\
[Grixis Reanimator] [ROUTER] play(main): 2 castable -> asking m...
[Grixis Reanimator] [ROUTER] plan: Hold Shallow Grave (graveyard has no creatures); next play Swamp, cast Unmask exiling a black card targeting myself to bin Atraxa, then Shallow Grave to reanimate her with haste.
[Grixis Reanimator] [ROUTER] play: PASS
[Grixis Reanimator] [ROUTER] play: plan step 1 -> Shallow Grave @6 1{B} [Return the top creature card of your graveyard to the battlefield.]
[Mardu Energy] [ROUTER] play(main): 2 castable -> asking m...
[Mardu Energy] [ROUTER] plan: Cast Voice of Victory now to add a fast clock (mobilize 2 each attack) while keeping Karakas mana; Wasteland is dead against basics so hold it.
[Mardu Energy] [ROUTER] play: casts Voice of Victory 1/3 @35 1{W} [Voice of Victory - Creature 1 / 3]
[Mardu Energy] [ROUTER] play: plan step 1 -> Wasteland @36 [{T}, Sacrifice CARDNAME: Destroy target nonbasic land.]
[Grixis Reanimator] [ROUTER] play(main): 2 castable -> asking m...
[Grixis Reanimator] [ROUTER] plan: Pass this window; next, Unmask (exile Archon) targeting myself to bin Atraxa, then Animate Dead her for a 7/7 lifelink clock.
[Grixis Reanimator] [ROUTER] play: plan step 1 -> Animate Dead @45 1{B} [Animate Dead]
[Mardu Energy] [ROUTER] play(main): 1 castable -> asking m...
[Mardu Energy] [ROUTER] play: casts Goblin Bombardment @11 1{R} [Goblin Bombardment]
[Grixis Reanimator] [ROUTER] play(main): 2 castable -> asking m...
[Grixis Reanimator] [ROUTER] plan: Cast Animate Dead targeting Atraxa in my graveyard (7/7 flying lifelink deathtouch), block next turn to gain 7.
[Grixis Reanimator] [ROUTER] play: casts Animate Dead @115 1{B} [Animate Dead]
"""


class PlanAuditTest(unittest.TestCase):
    def audit_log(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
            f.write(text)
        try:
            return pa.audit_events(pa.events_from_log(f.name))
        finally:
            os.unlink(f.name)

    def test_card_name_from_menu_and_log_labels(self):
        self.assertEqual(pa.card_name("CAST Shallow Grave @6 1{B} [Return the top...]"), "Shallow Grave")
        self.assertEqual(pa.card_name("Voice of Victory 1/3 @35 1{W} [Voice of Victory - Creature 1 / 3]"), "Voice of Victory")
        self.assertEqual(pa.card_name("CAST Lava Spike {R} [CARDNAME deals 3 damage]"), "Lava Spike")
        self.assertEqual(pa.card_name("ACTIVATE Goblin Bombardment @29 — cost: Sacrifice a creature [..]"), "Goblin Bombardment")
        self.assertEqual(pa.card_name("Wasteland @36 [{T}, Sacrifice CARDNAME: Destroy target nonbasic land.]"), "Wasteland")

    def test_real_contradictions_are_flagged(self):
        res = self.audit_log(LOG)
        got = [(v["card"], v["how"]) for v in res["hold_violations"]]
        self.assertEqual(got, [("Shallow Grave", "plan"), ("Wasteland", "plan"), ("Animate Dead", "plan")])

    def test_a_consistent_play_is_not_flagged(self):
        res = self.audit_log(LOG)
        cards = [v["card"] for v in res["hold_violations"]]
        self.assertNotIn("Voice of Victory", cards)          # "Cast Voice of Victory now"
        # "Animate Dead" contains the word "dead"; the card's own name is never a hold reason.
        self.assertEqual(cards.count("Animate Dead"), 1)

    def test_a_play_the_plan_endorses_is_not_a_hold_violation(self):
        # Recorded false positives. g02 T6: the HOLD belongs to the clause after "and", not to the
        # land the seat then played. g03 T9: "use it later" describes the land's use, not holding it.
        self.assertIsNone(pa.held_in(
            "Activate Stoneforge Mystic to put Batterskull into play, then play Wasteland and hold Swords to Plowshares.",
            "Wasteland"))
        self.assertIsNone(pa.held_in(
            "Actually playing Wasteland as a land drop means I can use it later to destroy their land", "Wasteland"))
        # ... but an `and` joining two objects of one "hold" is NOT two clauses: both stay held.
        for plan in ("hold Swords to Plowshares and Goblin Bombardment",
                     "Actually holding Arid Mesa and Wasteland is fine"):
            self.assertIsNotNone(pa.held_in(plan, "Goblin Bombardment" if "Goblin" in plan else "Wasteland"), plan)

    def test_a_plan_does_not_govern_a_later_turn(self):
        res = self.audit_log(LOG)
        # Goblin Bombardment was cast on a Mardu turn with no plan of its own: not scored at all.
        self.assertEqual(res["plays"], 6)
        self.assertEqual(res["planned_plays"], 5)

    def test_jsonl_rows(self):
        rows = [
            {"type": "play", "seat": "A", "turn": 3, "tier": "smart", "choice": 0,
             "options": ["CAST Lava Spike @7 {R} [..]", "CAST Fireblast @9 [..]", "PASS (do nothing)"],
             "raw_reply": "PLAN: Lava Spike now, hold Fireblast for lethal.\nCHOICE: 1"},
            {"type": "play", "seat": "A", "turn": 3, "tier": "smart", "choice": 0,
             "options": ["CAST Fireblast @9 [..]", "PASS (do nothing)"], "raw_reply": "CHOICE: 1"},
            {"type": "play", "seat": "A", "turn": 3, "tier": "fast", "choice": -1,
             "options": ["CAST Fireblast @9 [..]", "PASS (do nothing)"], "raw_reply": "CHOICE: 2"},
            {"type": "play", "seat": "A", "turn": 4, "tier": "smart", "choice": 0,
             "options": ["PLAY LAND Mountain", "PASS (do nothing)"], "raw_reply": "CHOICE: 1"},
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        try:
            res = pa.audit_events(pa.events_from_jsonl(f.name))
        finally:
            os.unlink(f.name)
        self.assertEqual(res["plans"], 1)
        self.assertEqual(res["plays"], 2)                    # the pass and the land are not plays
        self.assertEqual([v["card"] for v in res["hold_violations"]], ["Fireblast"])

    def test_plan_segment_stops_at_the_next_tag(self):
        self.assertEqual(pa.last_segment("PLAN: bolt face\nSTEPS: Lightning Bolt\nCHOICE: 1", "PLAN:"), "bolt face")


if __name__ == "__main__":
    unittest.main()
