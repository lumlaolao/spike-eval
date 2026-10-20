"""puzzle_gen.py: the drain and elf families of generated hard puzzles. Each board's step count is 10 to 15, its
opponent's life is the exhaustive search's maximum, its written line takes exactly those steps, and no two boards
of a draw are one board."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import puzzle_gen as g  # noqa: E402


def solution(text):
    return text.split("[solution]\n", 1)[1].split("[acceptance]", 1)[0].splitlines()


def stated_life(text):
    return int(re.search(r"^p1life=(\d+)$", text, re.M).group(1))


class DrainSearchTest(unittest.TestCase):
    def test_the_authored_altar_board_drains_eleven(self):
        # hard2/drain_0: the Skeleton dies four times with both drainers out (8), then the drainers (2 + 1).
        drain, steps, _ = g.drain_search(3, False, {"Blood Artist", "Zulaport Cutthroat", "Reassembling Skeleton"},
                                         set(), set(), True)
        self.assertEqual((drain, steps), (11, 3))

    def test_a_drainer_cast_first_drains_on_every_later_death(self):
        # Two Swamps pay for Blood Artist; Carrion Feeder then sacrifices the Skeleton and the Artist: 1 + 1.
        drain, steps, moves = g.drain_search(2, False, {"Carrion Feeder", "Reassembling Skeleton"}, set(),
                                             {"Blood Artist"}, False)
        self.assertEqual(drain, 2)
        self.assertEqual(moves[0], ("cast", "Blood Artist"))

    def test_bloodghast_returns_only_from_the_graveyard_on_the_land_drop(self):
        with_land = g.drain_search(0, True, {"Bloodghast", "Zulaport Cutthroat"}, set(), set(), True)[0]
        without = g.drain_search(0, False, {"Bloodghast", "Zulaport Cutthroat"}, set(), set(), True)[0]
        self.assertEqual((with_land, without), (3, 2))   # Bloodghast, back, Bloodghast again, Zulaport; or one of each

    def test_gravecrawler_needs_a_zombie_on_the_battlefield(self):
        # Carrion Feeder is the only Zombie: Gravecrawler is cast again while it lives, never after.
        drain = g.drain_search(3, False, {"Carrion Feeder", "Cruel Celebrant"}, {"Gravecrawler"}, set(), False)[0]
        self.assertEqual(drain, 4)   # three Gravecrawler deaths, then the Celebrant (the Feeder dies last, unseen)


class ElfSearchTest(unittest.TestCase):
    def test_the_authored_elf_board_deals_twenty_seven(self):
        # hard2/elf_0: Forest, Druid, two Sentinels, two elves and Craterhoof: 11 + 8 + 8.
        dmg, steps, moves = g.elf_search(1, 2, 0, [1, 1])
        self.assertEqual((dmg, steps), (27, 4))
        self.assertEqual(moves[-1], ("hoof",))

    def test_no_craterhoof_without_eight_mana(self):
        self.assertEqual(g.elf_search(0, 1, 0, [])[0], -1)


class FamilyBoardsTest(unittest.TestCase):
    COUNT = 25

    def boards(self, family):
        boards = g.generate(family, self.COUNT, 7)
        self.assertGreaterEqual(len(boards), self.COUNT // 2, "the family draws too few boards")
        return boards

    def check_common(self, family):
        boards = self.boards(family)
        keys = [b["key"] for b in boards]
        self.assertEqual(len(keys), len(set(keys)), "two boards share a key")
        for b in boards:
            text = b["text"]("x")
            lines = solution(text)
            self.assertTrue(g.MIN_ACTIONS <= b["actions"] <= g.MAX_ACTIONS, b["actions"])
            self.assertEqual(lines[0], "# steps: %d" % b["actions"])
            self.assertEqual(stated_life(text), b["life"])
        return boards

    def test_drain_boards(self):
        for b in self.check_common("drain"):
            s = b["search"]
            drain, steps, _ = g.drain_search(s["swamps"], s["land"], set(s["bf"]), set(s["gy"]), set(s["hand"]),
                                             s["altar"])
            self.assertEqual((drain, steps), (b["life"], b["actions"]), "the life is the search's maximum")
            lines = solution(b["text"]("x"))[1:]
            counted = [l for l in lines if l.startswith(("cast ", "play land ", "activate "))
                       and l != "activate Ashnod's Altar"]
            self.assertEqual(len(counted), b["actions"], "the line takes the steps it states")

    def test_elf_boards(self):
        for b in self.check_common("elf"):
            s = b["search"]
            costs = [g.ELF_HAND[n] for n in s["elves"]]
            dmg, steps, _ = g.elf_search(s["forests"], s["sentinels"], len(s["mana_elves"]), costs)
            self.assertEqual((dmg, steps), (b["life"], b["actions"]), "the life is the search's maximum")
            lines = solution(b["text"]("x"))[1:]
            casts = [l for l in lines if l.startswith("cast ")]
            self.assertEqual(len(casts) + 1, b["actions"], "casts and the attack")
            self.assertTrue(lines[-1].startswith("Craterhoof Behemoth; Nettle Sentinel"), lines[-1])
            # Every green spell asks each Sentinel's "may untap" once.
            self.assertEqual(lines.count("yes"), len(casts) * s["sentinels"])


if __name__ == "__main__":
    unittest.main()
