"""puzzle_gen.py: every generated family draws boards of 10 to 15 steps with the opponent's life set to exactly
what its line deals (the exhaustive search's maximum for splice, drain, elf and fling), no two boards of a draw are
one board, the splice search finds a line no unspliced line matches, and the fling search plays the way a written
line plays."""
import random
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import puzzle_gen as pg  # noqa: E402
import puzzle_format as pf  # noqa: E402


def solution(text):
    return text.split("[solution]\n", 1)[1].split("[acceptance]", 1)[0].splitlines()


class GeneratedFamiliesTest(unittest.TestCase):
    def test_every_board_is_ten_to_fifteen_steps_and_unique(self):
        for family in ("ping", "storm", "splice"):
            boards = pg.generate(family, 25, seed=11)
            self.assertGreaterEqual(len(boards), 10, family)
            self.assertEqual(len({b["key"] for b in boards}), len(boards), family)
            for b in boards:
                self.assertTrue(pg.MIN_ACTIONS <= b["actions"] <= pg.MAX_ACTIONS, (family, b["actions"]))
                text = b["text"]("x")
                self.assertEqual(solution(text)[0], "# steps: %d" % b["actions"])
                self.assertIn("p1life=%d" % b["life"], text)

    def test_a_ping_board_deals_exactly_its_life(self):
        # The damage is recounted from the written line: one per ping, each burn spell's damage, Fireblast's 4,
        # Kiln Fiend's 1 + 3 a spell and Goblin Electromancer's 2 when they attack, Mutagenic Growth's 2 on one.
        burn = {**{k: v[0] for k, v in pg.BURN.items()}, **{k: v[0] for k, v in pg.FREE.items()}, "Fireblast": 4}
        for b in pg.generate("ping", 30, seed=5):
            lines = solution(b["text"]("x"))
            casts = [l[5:] for l in lines if l.startswith("cast ")]
            total = sum(1 for l in lines if l == "activate Thermo-Alchemist") + sum(burn.get(c, 0) for c in casts)
            attack = lines[-1] if not lines[-1].startswith(("activate", "opponent", "cast", "#")) else ""
            if "Kiln Fiend" in attack:
                total += 1 + 3 * len(casts)
            if "Goblin Electromancer" in attack:
                total += 2
            if attack and "Mutagenic Growth" in casts:
                total += 2
            self.assertEqual(total, b["life"], lines)

    def test_a_storm_board_counts_every_spell_before_grapeshot(self):
        for b in pg.generate("storm", 20, seed=7):
            lines = solution(b["text"]("x"))
            casts = [l[5:] for l in lines if l.startswith("cast ")]
            self.assertEqual(casts[-1], "Grapeshot", "Grapeshot is cast last")
            storm = len(casts) - 1
            burn = sum(pg.BURN.get(c, (0,))[0] for c in casts)
            self.assertEqual(storm + 1 + burn, b["life"])
            self.assertEqual(lines.count("opponent"), storm + 1 + sum(c in pg.BURN or c == "Gitaxian Probe"
                                                                        for c in casts))

    def test_the_splice_search_beats_every_unspliced_line(self):
        hand = ["Lava Spike", "Lava Spike", "Desperate Ritual", "Glacial Ray", "Lightning Bolt", "Shock"]
        (best, steps, line), (plain, _, _) = pg.splice_best(hand, 3, True)
        self.assertEqual((best, plain), (20, 17), "splicing the Ritual onto both Spikes pays for the rest")
        self.assertTrue(any(x and x[1] for x in line))
        self.assertEqual(steps, 13)

    def test_fling_after_combat_beats_both_plain_plans(self):
        # fling_7925155e0782: Swiftspear and two Mountains. Rite of Flame, Pyretic Ritual and the burn before
        # combat, a Mountain held; after combat that Mountain casts the second Rite (three mana, a prowess trigger)
        # and Fling deals Swiftspear's 9 again: 8 burn + 8 combat + 9.
        hand = ["Shock", "Pyretic Ritual", "Gitaxian Probe", "Fling", "Gitaxian Probe", "Rite of Flame", "Lava Spike",
                "Rite of Flame", "Lava Spike"]
        dmg, steps, line = pg.fling_search(hand, 2, ["Monastery Swiftspear"])
        self.assertEqual((dmg, steps), (25, 10))
        after = [x[1] for x in line[line.index(("main2",)) + 1:]]
        self.assertEqual(after, ["Rite of Flame", "Fling"])
        for mode in ("precombat", "fling-only"):
            self.assertEqual(pg.fling_search(hand, 2, ["Monastery Swiftspear"], mode, respond=False)[0], 17, mode)

    def test_a_written_line_casts_in_response(self):
        # After combat Pyretic Ritual pays Brute Force and Fling. A player who lets each spell resolve Flings a
        # 10-power Kiln Fiend; a written line casts Fling the moment it is castable, over Brute Force and the
        # trigger, so it Flings a 4-power one.
        hand = ["Pyretic Ritual", "Brute Force", "Fling"]
        self.assertEqual(pg.fling_search(hand, 2, ["Kiln Fiend"], respond=False)[0], 11)
        self.assertEqual(pg.fling_search(hand, 2, ["Kiln Fiend"])[0], 10)

    def test_a_fling_board_deals_its_life_and_waits_for_main2(self):
        for b in pg.generate("fling", 3, seed=2):
            self.assertTrue(pg.MIN_ACTIONS <= b["actions"] <= pg.MAX_ACTIONS)
            self.assertLess(max(b["early"], b["held"]), b["life"])
            lines = solution(b["text"]("x"))
            self.assertEqual(lines[0], "# steps: %d" % b["actions"])
            waits = [l for l in lines if l.startswith("at MAIN2: ")]
            self.assertEqual(len(waits), 1, "only the first play after combat waits")
            self.assertEqual(sum(l.startswith(("cast ", "at MAIN2: cast ")) for l in lines) + 1, b["actions"])
            self.assertIn("cast Fling", "\n".join(lines))

    def test_a_written_board_is_a_valid_pzl(self):
        out = Path(tempfile.mkdtemp())
        self.assertEqual(pg.main(["--family", "splice", "--count", "3", "--seed", "3", "--out", str(out)]), 0)
        for p in out.glob("*.pzl"):
            goal, turns, start, phase = pf.pzl_goal(p.read_text())
            self.assertEqual((goal, turns, start, phase), ("Survive", 1, 1, "MAIN2"))
            self.assertTrue(re.search(r"^Description:It is your second main phase", p.read_text(), re.M))


def stated_life(text):
    return int(re.search(r"^p1life=(\d+)$", text, re.M).group(1))


class DrainSearchTest(unittest.TestCase):
    def test_the_authored_altar_board_drains_eleven(self):
        # hard2/drain_0: the Skeleton dies four times with both drainers out (8), then the drainers (2 + 1).
        drain, steps, _ = pg.drain_search(3, False, {"Blood Artist", "Zulaport Cutthroat", "Reassembling Skeleton"},
                                         set(), set(), True)
        self.assertEqual((drain, steps), (11, 3))

    def test_a_drainer_cast_first_drains_on_every_later_death(self):
        # Two Swamps pay for Blood Artist; Carrion Feeder then sacrifices the Skeleton and the Artist: 1 + 1.
        drain, steps, moves = pg.drain_search(2, False, {"Carrion Feeder", "Reassembling Skeleton"}, set(),
                                             {"Blood Artist"}, False)
        self.assertEqual(drain, 2)
        self.assertEqual(moves[0], ("cast", "Blood Artist"))

    def test_bloodghast_returns_only_from_the_graveyard_on_the_land_drop(self):
        with_land = pg.drain_search(0, True, {"Bloodghast", "Zulaport Cutthroat"}, set(), set(), True)[0]
        without = pg.drain_search(0, False, {"Bloodghast", "Zulaport Cutthroat"}, set(), set(), True)[0]
        self.assertEqual((with_land, without), (3, 2))   # Bloodghast, back, Bloodghast again, Zulaport; or one of each

    def test_gravecrawler_needs_a_zombie_on_the_battlefield(self):
        # Carrion Feeder is the only Zombie: Gravecrawler is cast again while it lives, never after.
        drain = pg.drain_search(3, False, {"Carrion Feeder", "Cruel Celebrant"}, {"Gravecrawler"}, set(), False)[0]
        self.assertEqual(drain, 4)   # three Gravecrawler deaths, then the Celebrant (the Feeder dies last, unseen)


class ElfSearchTest(unittest.TestCase):
    def test_the_authored_elf_board_deals_twenty_seven(self):
        # hard2/elf_0: Forest, Druid, two Sentinels, two elves and Craterhoof: 11 + 8 + 8.
        dmg, steps, moves = pg.elf_search(1, 2, 0, [1, 1])
        self.assertEqual((dmg, steps), (27, 4))
        self.assertEqual(moves[-1], ("hoof",))

    def test_no_craterhoof_without_eight_mana(self):
        self.assertEqual(pg.elf_search(0, 1, 0, [])[0], -1)


class FamilyBoardsTest(unittest.TestCase):
    COUNT = 25

    def boards(self, family):
        boards = pg.generate(family, self.COUNT, 7)
        self.assertGreaterEqual(len(boards), self.COUNT // 2, "the family draws too few boards")
        return boards

    def check_common(self, family):
        boards = self.boards(family)
        keys = [b["key"] for b in boards]
        self.assertEqual(len(keys), len(set(keys)), "two boards share a key")
        for b in boards:
            text = b["text"]("x")
            lines = solution(text)
            self.assertTrue(pg.MIN_ACTIONS <= b["actions"] <= pg.MAX_ACTIONS, b["actions"])
            self.assertEqual(lines[0], "# steps: %d" % b["actions"])
            self.assertEqual(stated_life(text), b["life"])
        return boards

    def test_a_drain_boards_life_is_the_searched_maximum_in_the_steps_it_states(self):
        for b in self.check_common("drain"):
            s = b["search"]
            drain, steps, _ = pg.drain_search(s["swamps"], s["land"], set(s["bf"]), set(s["gy"]), set(s["hand"]),
                                             s["altar"])
            self.assertEqual((drain, steps), (b["life"], b["actions"]), "the life is the search's maximum")
            lines = solution(b["text"]("x"))[1:]
            counted = [l for l in lines if l.startswith(("cast ", "play land ", "activate "))
                       and l != "activate Ashnod's Altar"]
            self.assertEqual(len(counted), b["actions"], "the line takes the steps it states")

    def test_an_elf_boards_life_is_the_searched_maximum_and_each_sentinel_is_asked_per_spell(self):
        for b in self.check_common("elf"):
            s = b["search"]
            costs = [pg.ELF_HAND[n] for n in s["elves"]]
            dmg, steps, _ = pg.elf_search(s["forests"], s["sentinels"], len(s["mana_elves"]), costs)
            self.assertEqual((dmg, steps), (b["life"], b["actions"]), "the life is the search's maximum")
            lines = solution(b["text"]("x"))[1:]
            casts = [l for l in lines if l.startswith("cast ")]
            self.assertEqual(len(casts) + 1, b["actions"], "casts and the attack")
            self.assertTrue(lines[-1].startswith("Craterhoof Behemoth; Nettle Sentinel"), lines[-1])
            # Every green spell asks each Sentinel's "may untap" once.
            self.assertEqual(lines.count("yes"), len(casts) * s["sentinels"])


class CopySearchTest(unittest.TestCase):
    def test_copying_the_ritual_beats_copying_the_bolt(self):
        # Guttersnipe and four Mountains. Pyretic Ritual takes two, Reverberate the other two while the Ritual is on
        # the stack: six mana for five Bolts, seven casts: 14 + 15. Without a ritual copy the best is the Ritual and
        # five Bolts: 12 + 15.
        hand = ["Pyretic Ritual", "Reverberate"] + ["Lightning Bolt"] * 5
        dmg, steps, line = pg.copy_search(hand, 4, ["Guttersnipe"])
        self.assertEqual((dmg, steps), (29, 7))
        self.assertEqual(line[:2], (("cast", "Pyretic Ritual", None), ("cast", "Reverberate", 0)))
        self.assertEqual(pg.copy_search(hand, 4, ["Guttersnipe"], ritual_copies=False)[:2], (27, 6))

    def test_the_written_line_passes_only_where_the_seat_would_cast(self):
        board = {"hand": ["Pyretic Ritual", "Reverberate"] + ["Lightning Bolt"] * 5, "mountains": 4,
                 "payoffs": ["Guttersnipe"]}
        dmg, steps, line = pg.copy_search(board["hand"], 4, board["payoffs"])
        lines, dealt, casts = pg.copy_lines(board["hand"], 4, board["payoffs"], line, dmg)
        # The copy resolves first and its mana makes a Bolt castable over the Ritual: two passes resolve Guttersnipe's
        # trigger and the Ritual before the first Bolt.
        self.assertEqual(lines[:6], ["cast Pyretic Ritual", "cast Reverberate", "pass", "pass", "cast Lightning Bolt",
                                     "opponent"])
        self.assertEqual((dealt, casts), (29, 7))

    def test_increasing_vengeance_copies_twice_from_the_graveyard(self):
        # Five Mountains: Seething Song, and Increasing Vengeance on it while it is on the stack, make ten. Then a
        # Bolt, Vengeance's flashback (five) on the other Bolt for two copies: 3 + 3 + 6. Without the Song's copy no
        # flashback is payable: 9 at most.
        hand = ["Seething Song", "Increasing Vengeance", "Lightning Bolt", "Lightning Bolt"]
        dmg, _, line = pg.copy_search(hand, 5, [])
        self.assertEqual(dmg, 12)
        self.assertEqual(line[1], ("cast", "Increasing Vengeance", 0))
        self.assertEqual(pg.copy_search(hand, 5, [], ritual_copies=False)[0], 9)

    def test_a_copy_board_is_the_searched_maximum_with_a_near_miss_trap(self):
        boards = pg.generate("copy", 3, 7)
        self.assertGreaterEqual(len(boards), 3)
        for b in boards:
            s = b["search"]
            dmg, steps, _ = pg.copy_search(s["hand"], s["mountains"], s["payoffs"])
            self.assertEqual((dmg, steps), (b["life"], b["actions"]))
            self.assertTrue(13 <= steps <= 15, steps)
            trap = pg.copy_search(s["hand"], s["mountains"], s["payoffs"], ritual_copies=False)[0]
            self.assertEqual(trap, b["trap"])
            self.assertTrue(1 <= dmg - trap <= 3, (dmg, trap))
            lines = solution(b["text"]("x"))
            self.assertEqual(lines[0], "# steps: %d" % steps)
            self.assertEqual(sum(l.startswith("cast ") for l in lines), steps)
            self.assertEqual(stated_life(b["text"]("x")), dmg)


class GraveSearchTest(unittest.TestCase):
    def test_threshold_waits_for_the_probe_and_the_ritual(self):
        # Two Swamps, five cards in the graveyard, Witherbloom Apprentice. Probe and Dark Ritual first put seven in
        # the graveyard, so Cabal Ritual makes five: Consume Spirit for 5, and a drain for each of the four casts.
        # Mana first, Cabal Ritual sees six and makes three: Consume Spirit for 3.
        hand = ["Dark Ritual", "Gitaxian Probe", "Cabal Ritual", "Consume Spirit"]
        dmg, steps, moves = pg.grave_search(hand, 2, 0, True, 5, 0)
        self.assertEqual((dmg, steps), (9, 4))
        self.assertEqual([m[1] for m in moves][-2:], ["Cabal Ritual", "Consume Spirit"])
        self.assertEqual(pg.grave_search(hand, 2, 0, True, 5, 0, mana_first=True)[0], 7)

    def test_the_apprentice_sacrificed_drains_no_more(self):
        # One Swamp and only the Apprentice to sacrifice: it is Culling the Weak's cost, so that cast drains nothing
        # and Consume Spirit is cast without it: X = 2.
        self.assertEqual(pg.grave_search(["Culling the Weak", "Consume Spirit"], 1, 0, True, 0, 0)[0], 2)

    def test_songs_counts_the_creature_sacrificed_before_it(self):
        # One Swamp, a Walking Corpse on the battlefield and one in the graveyard. Songs first makes one; Culling the
        # Weak first puts the second Corpse in the graveyard, then Songs makes two: X = 3, not 2.
        hand = ["Songs of the Damned", "Culling the Weak", "Consume Spirit"]
        dmg, steps, moves = pg.grave_search(hand, 1, 1, False, 1, 1)
        self.assertEqual((dmg, steps), (3, 3))
        self.assertEqual([m[1] for m in moves], ["Culling the Weak", "Songs of the Damned", "Consume Spirit"])

    def test_a_grave_board_is_the_searched_maximum_with_a_near_miss_trap(self):
        boards = pg.generate("grave", 6, 7)
        self.assertGreaterEqual(len(boards), 3)
        for b in boards:
            s = b["search"]
            dmg, steps, _ = pg.grave_search(s["hand"], s["swamps"], s["corpses"], s["apprentice"], s["gy"],
                                            s["gy_corpses"])
            self.assertEqual((dmg, steps), (b["life"], b["actions"]))
            self.assertTrue(13 <= steps <= 15, steps)
            trap = pg.grave_search(s["hand"], s["swamps"], s["corpses"], s["apprentice"], s["gy"], s["gy_corpses"],
                                   mana_first=True)[0]
            self.assertEqual(trap, b["trap"])
            self.assertTrue(1 <= dmg - trap <= 3, (dmg, trap))
            lines = solution(b["text"]("x"))
            self.assertEqual(sum(l.startswith(("cast ", "activate ")) for l in lines), steps)
            self.assertEqual(stated_life(b["text"]("x")), dmg)


if __name__ == "__main__":
    unittest.main()
