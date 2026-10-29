#!/usr/bin/env python3
"""eval/deck_legality.py against the pinned engine's format data, and every format deck in eval/decks legal.

Needs the Forge checkout ($FORGE_HOME, ~/code/forge or /root/forge); skipped without one.
"""

import re
import sys
import unittest
from pathlib import Path
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)

HERE = evalpath.EVAL
import deck_legality as dl  # noqa: E402

try:
    FORGE = dl.forge_home()
except SystemExit:
    FORGE = None


@unittest.skipIf(FORGE is None, "no Forge checkout")
class DeckLegalityTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.printings = dl.read_printings(FORGE)

    def check(self, fmt, deck):
        return dl.problems(deck, dl.read_format(FORGE, fmt), self.printings)

    def test_every_format_deck_is_legal_in_its_format(self):
        for prefix in dl.FORMAT_PREFIXES:
            decks = sorted((HERE / "decks").glob(prefix + "-*.txt"))
            self.assertTrue(decks, "no %s decks" % prefix)
            for d in decks:
                self.assertEqual(self.check(prefix, dl.read_deck(d)), [], d.name)

    def test_a_banned_card_a_rotated_card_and_an_uncommon_are_refused(self):
        lands = [(56, "Mountain")]
        self.assertIn("banned", " ".join(self.check("modern", lands + [(4, "Phlage, Titan of Fire's Fury")])))
        self.assertIn("legal set", " ".join(self.check("standard", lands + [(4, "Sheoldred, the Apocalypse")])))
        self.assertIn("C/L", " ".join(self.check("pauper", lands + [(4, "Fireblade Charger")])))

    def test_a_reprint_outside_the_cards_section_counts(self):
        # MH2 printed Counterspell only in its art sections; it is Modern-legal.
        self.assertEqual(self.check("modern", [(56, "Island"), (4, "Counterspell")]), [])


@unittest.skipIf(FORGE is None, "no Forge checkout")
class CardNameTest(unittest.TestCase):
    """SeatObservation.safeName drops " (digits)" from a name (an effect is named "The Wondrous Wasp (53)'s
    Effect", the engine id inside), and the ask contract and match_review.py read "Name (digits)" as a leaked
    engine id. Both rest on no real card name holding "(digits)". Names with numbers, underscores, hyphens
    and accents ("+2 Mace", "_____ Goblin", "Celebr-8000", "Troll of Khazad-dûm") are untouched by both."""

    def test_no_card_name_in_the_engine_holds_a_parenthesised_number(self):
        names = set()
        for f in (FORGE / "forge-gui/res/cardsfolder").rglob("*.txt"):
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("Name:"):
                    names.add(line[5:].strip())
        self.assertGreater(len(names), 30000, "the card database was not read")
        bad = sorted(n for n in names if re.search(r"\s*\(\d+\)", n))
        self.assertEqual(bad, [], "card names the id filter would change")
        for n in ("+2 Mace", "_____ Goblin", "Celebr-8000", "Borrowing 100,000 Arrows", "B.O.B. (Bevy of Beebles)"):
            self.assertIn(n, names)
            self.assertEqual(re.sub(r"\s*\((\d+)\)", "", n), n, n)


if __name__ == "__main__":
    unittest.main()
