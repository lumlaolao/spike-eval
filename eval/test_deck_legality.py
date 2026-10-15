#!/usr/bin/env python3
"""eval/deck_legality.py against the pinned engine's format data, and every format deck in eval/decks legal.

Needs the Forge checkout ($FORGE_HOME, ~/code/forge or /root/forge); skipped without one.
"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
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
        for prefix in ("modern", "standard", "pauper"):
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


if __name__ == "__main__":
    unittest.main()
