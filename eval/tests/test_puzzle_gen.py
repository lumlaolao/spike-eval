"""puzzle_gen.py: every generated family draws boards of 10 to 15 steps with the opponent's life set to exactly
what its line deals (the exhaustive search's maximum for splice, drain, elf and fling), no two boards of a draw are
one board, the splice search finds a line no unspliced line matches, and the fling search plays the way a written
line plays."""
import argparse
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)
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

    def test_the_splice_search_splices_two_copies_of_one_card(self):
        # Forge offers every copy in hand: two Desperate Rituals spliced onto one Arcane spell. Picking splice
        # cards by name found 19 here.
        hand = ["Desperate Ritual"] * 4 + ["Glacial Ray"] * 2 + ["Lava Spike"]
        (best, _, line), _ = pg.splice_best(hand, 3, True)
        self.assertEqual(best, 21)
        self.assertEqual(pg.red_search(hand, 3, True, False)[0], 21, "the red search agrees")
        self.assertTrue(any(x and len(x[1]) > len(set(x[1])) for x in line), "one spell carries two copies")

    def test_a_written_line_lets_the_ritual_resolve_before_a_splice(self):
        # splice_2b37741c0e25 (2026-10-20 gate: solved=False unconsumed=12): the line cast the third Desperate
        # Ritual the moment it was castable, over the second one still on the stack, so the Glacial Ray splice
        # was unpayable, asked nothing, and every later line fell out of step. The line now passes first.
        hand = ["Desperate Ritual"] * 3 + ["Glacial Ray", "Shock", "Shock"]
        (dmg, _, line), _ = pg.splice_best(hand, 3, True)
        items = ["ping" if x is None else ("cast", x[0], x[1]) for x in line]
        lines, dealt, _ = pg.written_line(items, hand, 3, True, False, False)
        self.assertEqual(dealt, dmg, "the line, played on the model of the stack, deals what the search says")
        spliced = lines.index("Glacial Ray")
        self.assertEqual(lines[spliced - 1], "cast Desperate Ritual")
        self.assertEqual(lines[spliced - 2], "pass", "the Ritual below resolves before the splice is asked")

    def test_kiln_fiend_counts_inside_the_red_search(self):
        # A free Gitaxian Probe deals nothing but pumps Kiln Fiend by 3: the search must cast it.
        dmg, _, line, casts, _ = pg.red_search(["Gitaxian Probe", "Lightning Bolt"], 1, False, False, kiln=True)
        self.assertEqual((dmg, casts), (3 + 6, 2))

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


class Splice2Test(unittest.TestCase):
    """The super-hard splice family: 13 to 15 steps, the opponent's life the exact searched maximum, no mana left at
    the end, and a trap: the better of two plain plans (never splice; splice every payable card onto each Arcane
    spell) falls 1 to 3 short."""

    @classmethod
    def setUpClass(cls):
        cls.boards = pg.generate("splice2", 6, seed=3)

    def test_a_splice2_board_is_thirteen_to_fifteen_steps(self):
        self.assertGreaterEqual(len(self.boards), 4)
        for b in self.boards:
            lines = solution(b["text"]("x"))
            self.assertTrue(13 <= b["actions"] <= 15, b["actions"])
            self.assertEqual(lines[0], "# steps: %d" % b["actions"])
            counted = [l for l in lines[1:] if l.startswith(("cast ", "activate "))]
            self.assertEqual(len(counted) + (1 if b["attackers"] else 0), b["actions"])

    def test_a_splice2_boards_life_is_the_searched_maximum_with_nothing_left(self):
        for b in self.boards:
            s = b["search"]
            dmg, _, _, _, left = pg.red_search(s["hand"], s["mountains"], s["alchemist"], s["reducer"],
                                               kiln=s["kiln"])
            self.assertEqual(dmg + s["attack_base"], b["life"])
            self.assertEqual(stated_life(b["text"]("x")), b["life"])
            self.assertEqual(left, 0, "the best line spends every mana")

    def test_a_splice2_board_has_a_trap_one_to_three_short(self):
        for b in self.boards:
            s = b["search"]
            never = pg.red_search(s["hand"], s["mountains"], s["alchemist"], s["reducer"], False, s["kiln"])[0]
            greedy = pg.red_search(s["hand"], s["mountains"], s["alchemist"], s["reducer"], "max", s["kiln"])[0]
            self.assertEqual((never + s["attack_base"], greedy + s["attack_base"]), (b["never"], b["greedy"]))
            self.assertEqual(b["trap"], max(b["never"], b["greedy"]))
            self.assertTrue(1 <= b["life"] - b["trap"] <= 3, (b["life"], b["trap"]))
            self.assertIn("trap %d" % b["trap"], b["text"]("x"))


GEN2 = evalpath.EVAL / "puzzles" / "hard" / "gen2"


class Gen2BoardsTest(unittest.TestCase):
    """Every committed splice2 board, re-derived from its own .pzl: the opponent's life is the searched maximum of
    its hand and battlefield, the best line spends every mana, the stated steps are 13 to 15, and the trap its Rule
    line states is what the two plain plans deal, 1 to 3 short."""

    def test_every_committed_splice2_board_holds_its_bar(self):
        boards = sorted(GEN2.glob("splice2_*.pzl"))
        self.assertTrue(boards, "no splice2 board in %s" % GEN2)
        for p in boards:
            text = p.read_text(encoding="utf-8")
            field = dict(l.split("=", 1) for l in text.split("[state]\n", 1)[1].split("[solution]")[0].splitlines())
            hand, bf = field["p0hand"].split(";"), field["p0battlefield"].split(";")
            args = (hand, bf.count("Mountain"), "Thermo-Alchemist" in bf, "Goblin Electromancer" in bf)
            kiln = "Kiln Fiend" in bf
            base = pg.attack_base(kiln, args[3])
            dmg, steps, _, _, left = pg.red_search(*args, kiln=kiln)
            self.assertEqual(dmg + base, stated_life(text), p.name)
            self.assertEqual(left, 0, p.name)
            self.assertEqual(solution(text)[0], "# steps: %d" % (steps + (1 if base else 0)), p.name)
            self.assertTrue(13 <= steps + (1 if base else 0) <= 15, p.name)
            never = pg.red_search(*args, splice=False, kiln=kiln)[0] + base
            greedy = pg.red_search(*args, splice="max", kiln=kiln)[0] + base
            trap = int(re.search(r"trap (\d+) \(never splicing deals (\d+), splicing every payable card onto each "
                                 r"Arcane spell (\d+)\)", text).group(1))
            self.assertEqual(trap, max(never, greedy), p.name)
            self.assertTrue(1 <= stated_life(text) - trap <= 3, p.name)
            self.assertIn("life p1 <= 0", text.split("[acceptance]", 1)[1], p.name)


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


class HardEnginesTest(unittest.TestCase):
    """drain2 and elf2: 13 to 15 steps, the opponent's life the exact search maximum with no Swamp, land drop or
    green mana left unused, and a trap: the best plain plan (puzzle_gen.DRAIN_PLANS, ELF_PLANS) falls 1 to 3
    short."""
    COUNT = 12

    def test_playing_the_land_first_strands_bloodghast(self):
        # Hand-computed: Viscera Seer sacrifices Bloodghast (1), the land brings it back, again (1), then Zulaport
        # Cutthroat (1, its own death): 3. Land first: Bloodghast once and the Cutthroat: 2.
        board = (0, True, {"Zulaport Cutthroat", "Viscera Seer", "Bloodghast"}, set(), set(), False)
        self.assertEqual(pg.drain_search(*board)[0], 3)
        self.assertEqual(pg.drain_search(*board, plan="land_first")[0], 2)
        self.assertEqual(pg.drain_plain(*board)["land_first"], 2)

    def test_drainers_cast_last_lose_the_early_deaths(self):
        # Hand-computed: two Swamps pay for Blood Artist; the Seer then sacrifices the Skeleton and the Artist: 2.
        # A plain player who keeps the drainer for last casts Gravecrawler first ({B}) and cannot pay for the
        # Artist ({1}{B}) with the one Swamp left: nothing drains.
        board = (2, False, {"Viscera Seer", "Reassembling Skeleton"}, set(), {"Blood Artist", "Gravecrawler"}, False)
        self.assertEqual(pg.drain_search(*board)[0], 2)
        self.assertEqual(pg.drain_search(*board, plan="drainers_last")[0], 0)

    def test_a_starting_priest_of_titania_counts_every_elf(self):
        # Hand-computed: the Priest taps first for the four elves out (4); an elf (3); the Druid taps both Sentinels
        # and that elf (6); the second elf untaps the Sentinels (5); the Druid taps them and the second elf (8):
        # Craterhoof exactly, X = 7, and the Druid and both Sentinels attack beside it: 12 + 8 + 9 + 9.
        self.assertEqual(pg.elf_search(0, 2, 0, [1, 1], priest=True)[0], 38)
        # Never tapping a Sentinel leaves the Priest's 4 and the Druid's one activation: no Craterhoof.
        self.assertEqual(pg.elf_search(0, 2, 0, [1, 1], priest=True, plan="never_tap_sentinels")[0], -1)

    def test_hoofdrain_sacrifices_after_craterhoof_resolves(self):
        # Hand-computed: five Forests, Heritage Druid, one Sentinel, Blood Artist (0/1) and Viscera Seer (1/1), three
        # 1-cost elves. Elf A (4); the Druid taps the Sentinel, itself and A (7); B untaps the Sentinel (6); C (5);
        # the Druid taps the Sentinel, B and C (8): Craterhoof exactly. Without tapping the Druid itself the third
        # activation has no third elf. X = 8: Craterhoof 13, the Sentinel 10, the Artist 8, the Seer 9 attack (40);
        # after Craterhoof's trigger the Seer sacrifices A, B, C and the tapped Druid with the Artist out (4).
        # Steps: 3 casts, Craterhoof, 4 sacrifices, the attack.
        args, kw = (5, 1, 0, [1, 1, 1]), dict(others=(0, 1), drain=1)
        self.assertEqual(pg.elf_search(*args, **kw)[:2], (44, 9))
        self.assertEqual(pg.elf_search(*args, plan="attack_only", **kw)[0], 40)
        # Sacrificing each elf as soon as the Druid has tapped it: all three go before Craterhoof, X = 5:
        # 10 + 7 + 5 + 6 attack, 4 drained.
        self.assertEqual(pg.elf_search(*args, plan="sac_early", **kw)[0], 32)
        self.assertEqual(pg.elf_search(*args, plan="never_tap_sentinels", **kw)[0], -1)

    GEN2 = evalpath.EVAL / "puzzles" / "hard" / "gen2"

    def check_family(self, family):
        """Every committed board of the family, recomputed from the search inputs it states."""
        paths = sorted(self.GEN2.glob(family + "_*.pzl"))
        self.assertTrue(paths, "no %s boards in %s" % (family, self.GEN2))
        for p in paths:
            text = p.read_text(encoding="utf-8")
            rec = pg.board_record(text)
            lines = solution(text)
            self.assertEqual(rec["family"], family, p.name)
            self.assertTrue(13 <= rec["steps"] <= 15, (p.name, rec["steps"]))
            best, steps, unused, plain = pg.research(rec)
            self.assertEqual((best, steps), (rec["life"], rec["steps"]), "%s: the life is the search's maximum" % p.name)
            self.assertEqual(unused, 0, "%s: no mana is left unused at the end of the line" % p.name)
            self.assertEqual(plain, rec["trap"]["plans"], "%s: the plain plans are recomputed" % p.name)
            self.assertEqual(max(plain.values()), rec["trap"]["best"], p.name)
            self.assertTrue(1 <= rec["life"] - rec["trap"]["best"] <= 3, (p.name, rec["trap"]))
            rule = [l for l in text.splitlines() if l.startswith("Rule:")][0]
            self.assertIn("plain plan", rule)
            self.assertIn("%d short" % (rec["life"] - rec["trap"]["best"]), rule)
            counted = [l for l in lines if l.startswith(("cast ", "play land ", "activate "))
                       and l not in ("activate Ashnod's Altar", "activate Forest", "activate Heritage Druid",
                                     "activate " + pg.PRIEST)
                       and l[len("activate "):] not in pg.ELF_MANA]
            attack = 1 if family != "drain2" else 0
            self.assertEqual(len(counted) + attack, rec["steps"], "%s: the line takes the steps it states" % p.name)
        return paths

    def test_drain2_boards(self):
        for p in self.check_family("drain2"):
            rec = pg.board_record(p.read_text(encoding="utf-8"))
            s = rec["search"]
            outlets = [o for o in pg.COUNTED_OUTLETS if o in s["bf"] + s["hand"]] + ([pg.ALTAR] if s["altar"] else [])
            self.assertGreaterEqual(len(outlets), 2, "%s: two sacrifice outlets that differ" % p.name)

    def test_elf2_boards(self):
        for p in self.check_family("elf2"):
            self.assertTrue(solution(p.read_text())[-1].startswith("Craterhoof Behemoth"), p.name)

    def test_hoofdrain_boards(self):
        for p in self.check_family("hoofdrain"):
            lines = solution(p.read_text())
            self.assertIn("activate Viscera Seer", lines)
            self.assertTrue(lines[-1].startswith("Craterhoof Behemoth"), p.name)

    def test_a_generated_drain2_board_states_what_the_tests_recompute(self):
        # A board drawn now (not one committed): its stated record is what the search gives.
        b = pg.drain2_board(random.Random(3)) or next(filter(None, (pg.drain2_board(random.Random(i))
                                                                    for i in range(4, 4000))))
        rec = pg.board_record(b["text"]("x"))
        self.assertEqual((rec["life"], rec["steps"], rec["trap"]), (b["life"], b["actions"], b["trap"]))
        best, steps, unused, plain = pg.research(rec)
        self.assertEqual((best, steps, unused, plain), (b["life"], b["actions"], 0, b["trap"]["plans"]))


class GenGateResumeTest(unittest.TestCase):
    BUILD = type("B", (), {"overlay_sha": "aaaaaaaaaaaa"})()

    def setUp(self):
        sys.path.insert(0, str(evalpath.REPO / "scripts" / "puzzle-gate"))
        import gen_gate
        self.gg = gen_gate
        tmp = Path(tempfile.mkdtemp())
        self.pzl = tmp / "drain2_x.pzl"
        self.pzl.write_text("[solution]\n# steps: 13\npass\n", encoding="utf-8")
        self.done = tmp / "out" / "line" / "drain2_x" / "a"
        self.done.mkdir(parents=True)
        self.a = argparse.Namespace(out=str(tmp / "out"), build="b", forge="f", resume=True, allow_stale_build=False)

    def line_ran(self, pzl_sha, overlay_sha="aaaaaaaaaaaa"):
        """A line result left by an earlier gate; overlay_sha None: by a run from before builds were recorded."""
        (self.done / "puzzle-results.jsonl").write_text(json.dumps({"solved": True, "script_played": True,
                                                                    "solver_actions": 20,
                                                                    "pzl_sha256": pzl_sha}) + "\n")
        if overlay_sha:
            (self.done / "build.json").write_text(json.dumps({"build_overlay_sha": overlay_sha}))

    def plays(self):
        """How many times play_line ran the line, and its row."""
        with mock.patch.object(self.gg.subprocess, "run") as run:
            row = self.gg.play_line(self.a, self.pzl, self.BUILD)
        return run.call_count, row

    def test_a_resumed_gate_reuses_a_boards_line_result(self):
        # A gate stopped by a time limit is run again with --resume: a board whose line already has a result is
        # not played again.
        self.line_ran(pf.sha256_file(self.pzl))
        n, row = self.plays()
        self.assertEqual(n, 0, "the line was played again")
        self.assertEqual((row["solved"], row["actions"]), (True, 13))
        self.assertNotIn("dropped", row)

    def test_a_resumed_gate_plays_again_a_board_edited_since_its_line_ran(self):
        # pr-review of #299: a board regenerated under the same name (its key is the cards, not the line) kept the
        # old line's verdict. A row is reused only when it ran on this very file.
        self.line_ran("an older file")
        self.assertEqual(self.plays()[0], 1, "the stale row was reused")

    def test_a_resumed_gate_plays_again_a_line_another_build_ran(self):
        # pr-review of #319: a gate resumed on a rebuilt harness kept the old harness's verdicts.
        self.line_ran(pf.sha256_file(self.pzl), "bbbbbbbbbbbb")
        self.assertEqual(self.plays()[0], 1, "another overlay's row was reused")

    def test_a_resumed_gate_plays_again_a_line_with_no_build_recorded(self):
        self.line_ran(pf.sha256_file(self.pzl), None)
        self.assertEqual(self.plays()[0], 1, "an unrecorded build's row was reused")


class GenGateBuildTest(unittest.TestCase):
    """gen_gate runs puzzle_baselines.py with its output captured and its exit code unread, so a build that
    puzzle_baselines.Build refuses (not this checkout's overlay) dropped every board as "line: solved=False"."""

    def setUp(self):
        sys.path.insert(0, str(evalpath.REPO / "scripts" / "puzzle-gate"))
        import gen_gate
        self.gg = gen_gate
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "boards").mkdir()
        (self.tmp / "boards" / "b.pzl").write_text("[solution]\n# steps: 12\npass\n", encoding="utf-8")

    def test_a_refused_build_stops_the_gate_before_any_board_is_played(self):
        with mock.patch.object(self.gg.pb, "Build", side_effect=SystemExit("stale build")), \
                mock.patch.object(self.gg.subprocess, "run", side_effect=AssertionError("a board was played")):
            with self.assertRaises(SystemExit) as cm:
                self.gg.main(["--build", "B", "--forge", "F", "--boards", str(self.tmp / "boards"),
                              "--out", str(self.tmp / "out")])
        self.assertEqual(str(cm.exception.code), "stale build")

    def test_allow_stale_build_reaches_the_check_and_every_puzzle_baselines_call(self):
        with mock.patch.object(self.gg.pb, "Build") as build, \
                mock.patch.object(self.gg.subprocess, "run") as run:
            self.gg.main(["--build", "B", "--forge", "F", "--boards", str(self.tmp / "boards"),
                          "--out", str(self.tmp / "out"), "--allow-stale-build"])
        self.assertEqual(build.call_args, mock.call("B", "F", allow_stale=True))
        self.assertTrue(run.call_args_list, "the line was played")
        for c in run.call_args_list:
            self.assertIn("--allow-stale-build", c.args[0])


class RunOneBuildTest(unittest.TestCase):
    """scripts/puzzle-gate/run_one.py makes its own puzzle_baselines.Build: ALLOW_STALE_BUILD=1 is its
    --allow-stale-build (pr-review of #319). The JVM is `true`, so nothing is played."""

    def run_one(self, extra_env):
        tmp = Path(tempfile.mkdtemp())
        build = tmp / "build"
        (build / "classes/forge/spike").mkdir(parents=True)
        (build / "classes/forge/spike/LlmSpikePuzzle.class").write_bytes(b"")
        (build / "classpath.txt").write_text("/x.jar\n")
        (build / "overlay-sha.txt").write_text("0ld0ld0ld0ld\n")
        (tmp / "forge" / "forge-gui-desktop").mkdir(parents=True)
        env = {k: v for k, v in os.environ.items() if k != "ALLOW_STALE_BUILD"}
        env.update(FORGE_HOME=str(tmp / "forge"), JAVA="true", **extra_env)
        script = evalpath.REPO / "scripts" / "puzzle-gate" / "run_one.py"
        res = subprocess.run([sys.executable, str(script), str(build), str(tmp / "x.pzl"), str(tmp / "out"), "ai",
                              "ai"], env=env, capture_output=True, text=True, timeout=60)
        prov = tmp / "out" / "build.json"
        return res, (json.loads(prov.read_text()) if prov.is_file() else None)

    def test_a_stale_build_is_refused_unless_allowed(self):
        res, prov = self.run_one({})
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("--allow-stale-build", res.stderr)
        self.assertIsNone(prov)
        res, prov = self.run_one({"ALLOW_STALE_BUILD": "1"})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(prov["stale_build_allowed"])


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

    def test_unearth_brings_the_apprentice_back_for_the_casts_after_it(self):
        # Two Swamps, the Apprentice in the graveyard: Unearth (no drain, it is not back yet), Dark Ritual and
        # Consume Spirit drain once each: 1 + 1 + X = 1. Without Unearth: X = 2. Dark Ritual could be cast over
        # Unearth, so one pass lets Unearth resolve first; the Apprentice's trigger then sits over each spell.
        hand = ["Unearth", "Dark Ritual", "Consume Spirit"]
        dmg, steps, moves = pg.grave_search(hand, 2, 0, "gy", 1, 0)
        self.assertEqual((dmg, steps), (3, 3))
        lines, dealt, n = pg.grave_lines(hand, 2, 0, "gy", 1, 0, moves)
        self.assertEqual(lines, ["cast Unearth", "pass", "cast Dark Ritual", "cast Consume Spirit", "1", "opponent"])
        self.assertEqual((dealt, n), (3, 3))

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


class AtogSearchTest(unittest.TestCase):
    # The battlefield as counts of Ornithopter, Memnite, Frogmite, Myr Enforcer and Krark-Clan Ironworks.
    def test_ironworks_mana_pays_for_fling(self):
        # One Mountain, Atog, Ironworks, Ornithopter and Memnite. Atog eating everything leaves Fling unpayable
        # (it needs two mana): Galvanic Blast with metalcraft is the best of that, 4. Ironworks eats Ornithopter for
        # {C}{C}, Atog eats Memnite and the Ironworks, and Fling pays {1} from the pool: a 5/6 Atog.
        dmg, steps, moves = pg.atog_search(["Fling", "Galvanic Blast"], 1, 0, (1, 1, 0, 0, 1))
        self.assertEqual((dmg, steps), (5, 3))
        self.assertEqual(moves[0], ("kci", "Ornithopter"))
        self.assertEqual(moves[-1], ("fling", "Atog"))
        self.assertEqual(pg.atog_search(["Fling", "Galvanic Blast"], 1, 0, (1, 1, 0, 0, 1), plain=True)[0], 4)

    def test_the_written_line_lets_each_pump_resolve_before_fling(self):
        moves = pg.atog_search(["Fling", "Galvanic Blast"], 1, 0, (1, 1, 0, 0, 1))[2]
        lines, dealt, steps = pg.atog_lines(1, 0, (1, 1, 0, 0, 1), moves, 5)
        # Fling is castable over the last pump: a pass lets it resolve first, or Fling takes a 3/4 Atog.
        self.assertEqual(lines, ["activate Krark-Clan Ironworks", "Ornithopter", "activate Atog", "Memnite", "pass",
                                 "activate Atog", "pass", "cast Fling", "opponent"])
        self.assertEqual((dealt, steps), (5, 3))

    def test_disciple_drains_for_every_artifact_that_dies(self):
        # Two Disciples, Atog and a lone Ironworks, no mana: Atog eats the Ironworks, 2 drained.
        self.assertEqual(pg.atog_search([], 0, 2, (0, 0, 0, 0, 1))[0], 2)

    def test_an_atog_board_is_the_searched_maximum_with_a_near_miss_trap(self):
        boards = pg.generate("atog", 2, 7)
        self.assertEqual(len(boards), 2)
        for b in boards:
            s = b["search"]
            start = tuple(s["start"])
            dmg, steps, _ = pg.atog_search(s["hand"], s["mountains"], s["disciples"], start)
            self.assertEqual((dmg, steps), (b["life"], b["actions"]))
            self.assertTrue(13 <= steps <= 15, steps)
            trap = pg.atog_search(s["hand"], s["mountains"], s["disciples"], start, plain=True)[0]
            self.assertEqual(trap, b["trap"])
            self.assertTrue(1 <= dmg - trap <= 3, (dmg, trap))
            self.assertEqual(stated_life(b["text"]("x")), dmg)


def infect_start(**kw):
    """An infect search input: one Glistener Elf, no Inkmoth Nexus, at 20 life, the opponent poisoned already."""
    s = {"attackers": ["Glistener Elf"], "inkmoth": False, "forests": 1, "hand": [], "hand_forests": 0,
         "graveyard": 0, "life": 20, "poisoned": True}
    s.update(kw)
    return s


def landfall_start(**kw):
    s = {"mountains": 0, "forests": 0, "valakut": None, "hellkite": False, "geopede": False, "coralhelm": False,
         "cobra": None, "hand": ["Blaze"], "lib": [5, 5]}
    s.update(kw)
    return s


class InfectSearchTest(unittest.TestCase):
    def test_might_of_old_krosa_gives_four_only_in_a_main_phase(self):
        # The land, then Might of Old Krosa in the first main phase (+4) and Thirsting Roots proliferating: 1 + 4 + 1.
        # Cast after the attack (the plain plan that pumps in combat) Might gives +2: 4.
        s = infect_start(hand=["Might of Old Krosa", "Thirsting Roots"], hand_forests=1)
        poison, steps, unused, moves = pg.infect_search(s)
        self.assertEqual((poison, steps, unused), (6, 4, 0))
        self.assertEqual(pg.infect_search(s, plan="combat_pumps")[0], 4)

    def test_proliferate_waits_for_the_first_poison_counter(self):
        # The opponent has no poison: Thirsting Roots before combat gives nothing, so a Forest is held for it after.
        s = infect_start(hand=["Might of Old Krosa", "Thirsting Roots"], hand_forests=1, poisoned=False)
        poison, steps, unused, moves = pg.infect_search(s)
        self.assertEqual(poison, 6)
        kinds = [m[0] if m[0] != "cast" else m[1] for m in moves]
        self.assertLess(kinds.index("attack"), kinds.index("Thirsting Roots"))

    def test_counters_on_two_creatures_proliferate_twice_before_combat(self):
        # A +1/+1 counter on each attacker, then Roots before combat: each creature and the opponent get one more,
        # 3 + 3 + 1. Both counters on one creature: 4 + 1 + 1; Roots after combat: 2 + 2 + 1.
        s = infect_start(attackers=["Blighted Agent", "Glistener Elf"], forests=3,
                         hand=["Battlegrowth", "Battlegrowth", "Thirsting Roots"])
        self.assertEqual(pg.infect_search(s)[0], 7)
        self.assertEqual(pg.infect_search(s, plan="prolif_after_combat")[0], 5)

    def test_become_immense_delves_five(self):
        self.assertEqual(pg.infect_search(infect_start(hand=["Become Immense"], graveyard=5))[0], 7)
        self.assertEqual(pg.infect_search(infect_start(hand=["Become Immense"], graveyard=4))[0], 1)

    def test_land_grant_is_free_with_no_land_in_hand(self):
        # No mana at all: Land Grant for free, the Forest it finds played, Groundswell with landfall: 1 + 4.
        s = infect_start(forests=0, hand=["Land Grant", "Groundswell"])
        self.assertEqual(pg.infect_search(s)[:2], (5, 4))

    def test_inkmoth_nexus_attacks_for_one_mana_and_mutagenic_growth_costs_life(self):
        s = infect_start(inkmoth=True, hand=["Mutagenic Growth"])
        self.assertEqual(pg.infect_search(s)[:2], (4, 3))
        # At 2 life the Phyrexian symbol takes the Forest, and Inkmoth Nexus stays a land.
        self.assertEqual(pg.infect_search(dict(s, life=2))[0], 3)

    def test_mishras_bauble_comes_back_with_unnatural_restoration(self):
        # Bauble, cracked; Restoration returns it and proliferates (+1); Bauble again: three cards in the graveyard
        # (the Bolt, Restoration, Bauble) and Become Immense for {2}{G}: 1 + 1 + 6. Without Restoration the
        # graveyard holds two and Become Immense costs {3}{G}: 7.
        s = infect_start(forests=5, graveyard=1, hand=["Mishra's Bauble", "Unnatural Restoration",
                                                         "Become Immense"])
        poison, steps, unused, moves = pg.infect_search(s)
        self.assertEqual(poison, 8)
        self.assertEqual(sum(m == ("bauble",) for m in moves), 2)

    def test_the_written_line_proliferates_onto_the_opponent_and_each_countered_creature(self):
        s = infect_start(attackers=["Blighted Agent", "Glistener Elf"], forests=3,
                         hand=["Battlegrowth", "Battlegrowth", "Thirsting Roots"])
        poison, steps, unused, moves = pg.infect_search(s)
        lines = pg.infect_lines(s, moves, 10 - poison)[0]
        i = lines.index("Proliferate")
        self.assertEqual(sorted(lines[i + 1:i + 4]), ["Blighted Agent", "Glistener Elf", "opponent"])
        self.assertEqual(lines[-1], "Blighted Agent; Glistener Elf")

    def test_an_infect_board_is_the_searched_maximum_with_a_near_miss_trap(self):
        check_drawn_board(self, "infect", 2, 150)


def equip_start(**kw):
    s = {"creatures": ["Fencing Ace", "Hill Giant"], "start": [], "loose": [], "hand": [], "mana": 0}
    s.update(kw)
    return s


class EquipSearchTest(unittest.TestCase):
    def test_equipment_goes_on_the_double_striker(self):
        # Bonesplitter and Short Sword on Fencing Ace: (1 + 2 + 1) x 2 + Hill Giant's 3. On the biggest creature,
        # the Giant: 3 + 3 + Ace's 2.
        s = equip_start(loose=["Bonesplitter", "Short Sword"], mana=2)
        dmg, steps, unused, moves = pg.equip_search(s)
        self.assertEqual((dmg, steps, unused), (11, 3, 0))
        self.assertEqual(pg.equip_search(s, plan="biggest")[0], 8)

    def test_embercleave_costs_one_less_for_each_attacker(self):
        # Two attackers: Embercleave costs {2}{R}{R} after attacks are declared, and goes on the Giant: 4 x 2 + 2.
        s = equip_start(hand=["Embercleave"], mana=4)
        dmg, steps, unused, moves = pg.equip_search(s)
        self.assertEqual((dmg, steps, unused), (10, 2, 0))
        self.assertEqual(moves[0], ("attack",))
        self.assertEqual(pg.equip_search(s, plan="cleave_main")[0], 5)

    def test_puresteel_paladin_equips_for_free_with_three_artifacts(self):
        s = equip_start(creatures=["Fencing Ace", "Puresteel Paladin"], start=["Puresteel Paladin"],
                        hand=["Bonesplitter", "Leonin Scimitar", "Short Sword"], mana=3)
        dmg, steps, unused, moves = pg.equip_search(s)
        self.assertEqual((dmg, steps, unused), (12, 7, 0))
        self.assertEqual(pg.equip_search(s, plan="equip_asap")[0], 8)

    def test_sigardas_aid_attaches_as_the_equipment_enters(self):
        s = equip_start(creatures=["Fencing Ace", "Savannah Lions"], hand=["Bonesplitter", "Sigarda's Aid"], mana=2)
        self.assertEqual(pg.equip_search(s)[:3], (8, 3, 0))

    def test_the_written_line_equips_by_menu_words(self):
        s = equip_start(loose=["Bonesplitter", "Short Sword"], mana=2)
        moves = pg.equip_search(s)[3]
        lines = pg.equip_lines(s, moves)
        self.assertEqual(lines, ["activate Bonesplitter ... Equip {1}", "Fencing Ace",
                                 "activate Short Sword ... Equip {1}", "Fencing Ace", "Fencing Ace; Hill Giant"])

    def test_the_written_line_orders_the_triggers_of_an_equipment_entering(self):
        s = equip_start(creatures=["Fencing Ace", "Puresteel Paladin"], start=["Puresteel Paladin", "Sigarda's Aid"],
                        hand=["Bonesplitter"], mana=1)
        lines = pg.equip_lines(s, pg.equip_search(s)[3])
        self.assertEqual(lines, ["cast Bonesplitter", "Sigarda's Aid; Puresteel Paladin", "Fencing Ace", "yes", "no",
                                 "Fencing Ace; Puresteel Paladin"])

    def test_an_equip_board_is_the_searched_maximum_with_a_near_miss_trap(self):
        check_drawn_board(self, "equip", 3, 600)


GEN3 = evalpath.EVAL / "puzzles" / "hard" / "gen3"


class Gen3BoardsTest(unittest.TestCase):
    """Every committed infect, equip, landfall and tokens board, re-derived from the search inputs it states
    (check_gen3_family)."""

    def test_infect_boards(self):
        for p in check_gen3_family(self, "infect"):
            self.assertIn("Goal:Win", p.read_text(encoding="utf-8"))

    def test_equip_boards(self):
        check_gen3_family(self, "equip")

    def test_landfall_boards(self):
        check_gen3_family(self, "landfall")

    def test_tokens_boards(self):
        check_gen3_family(self, "tokens")

    def test_a_family_with_no_gen3_writer_is_refused(self):
        with self.assertRaises(ValueError):
            pg.written_from_record({"family": "drain2", "search": {}})


class LandfallSearchTest(unittest.TestCase):
    def test_lotus_cobra_before_the_land_drop(self):
        # Two Mountains, a Forest and Akoum Hellkite out. Lotus Cobra first (a Mountain and the Forest); the Mountain:
        # Hellkite 2, Cobra's green casts Exploration; Wooded Foothills: Hellkite 1, red; cracked for a Mountain:
        # Hellkite 2, red. Two red floating and three Mountains: Blaze for 4. 9 in 6 steps. The land first leaves Cobra
        # one land fewer to see: 8.
        s = landfall_start(mountains=2, forests=1, hellkite=True, cobra="hand",
                           hand=["Blaze", "Exploration", "Lotus Cobra", "Mountain", "Wooded Foothills"])
        dmg, steps, moves = pg.landfall_search(s)
        self.assertEqual((dmg, steps), (9, 6))
        self.assertEqual(moves[0][:2], ("cast", "Lotus Cobra"))
        self.assertEqual(pg.landfall_plain(s), {"land_first": 8, "payoff_first": 9})

    def test_valakut_needs_five_other_mountains(self):
        self.assertEqual(pg.landfall_search(landfall_start(mountains=5, valakut="bf", hand=["Blaze", "Mountain"]))[0],
                         3 + 6)
        self.assertEqual(pg.landfall_search(landfall_start(mountains=4, valakut="bf", hand=["Blaze", "Mountain"]))[0],
                         5)

    def test_harrow_sacrifices_the_tapped_forest_and_both_mountains_count(self):
        # Four Mountains, Valakut and a Forest: Harrow paid with the Forest and two Mountains sacrifices the Forest and
        # fetches two Mountains, each with five others (6); Blaze for the 5 left, X = 4. Sacrificing a Mountain
        # leaves each new one four others.
        s = landfall_start(mountains=4, forests=1, valakut="bf", hand=["Blaze", "Harrow"])
        dmg, steps, moves = pg.landfall_search(s)
        self.assertEqual((dmg, steps), (10, 2))
        self.assertEqual(moves[0][3:5], ("Forest", ("Mountain", "Mountain")))

    def test_floating_mana_of_two_colours_never_pays_a_generic_cost(self):
        # A red and a green floating, a {1}{G} spell: green pays the {G}, red the {1}: allowed. Two of each floating
        # and a {2}: the engine would ask which pays, so the model refuses it.
        self.assertEqual(list(pg.landfall_pay(1, 1, 0, 0, 0, 1, 1, 0)), [((0, 0, 0), 0, 0, 0, 0, 0)])
        self.assertEqual(list(pg.landfall_pay(2, 2, 0, 0, 0, 2, 0, 0)), [])

    def test_the_written_line_names_each_trigger_and_cobras_colour(self):
        # The fetch's triggers resolve before its crack: the seat could crack at once, so the line passes twice.
        s = landfall_start(mountains=2, forests=1, hellkite=True, cobra="hand",
                           hand=["Blaze", "Exploration", "Lotus Cobra", "Mountain", "Wooded Foothills"])
        dmg, steps, moves = pg.landfall_search(s)
        lines, dealt, n = pg.landfall_lines(s, moves)
        self.assertEqual(lines, ["activate Mountain", "activate Forest", "cast Lotus Cobra", "play land Mountain",
                                 "Akoum Hellkite; Lotus Cobra", "opponent", "green", "cast Exploration",
                                 "play land Wooded Foothills", "Akoum Hellkite; Lotus Cobra", "opponent", "pass",
                                 "pass", "red", "activate Wooded Foothills", "Mountain", "Akoum Hellkite; Lotus Cobra",
                                 "opponent", "red", "cast Blaze", "4", "opponent"])
        self.assertEqual((dealt, n), (9, 6))

    def test_a_pass_resolves_the_stack_before_a_fetch_is_cracked(self):
        # Prodigal Pyromancer pings at every chance (Retreat to Coralhelm untaps it on each land); the second fetch's
        # crack waits for the first fetch's triggers, so the line passes while the seat could crack.
        s = landfall_start(mountains=3, coralhelm=True, geopede=True,
                           hand=["Blaze", "Wooded Foothills", "Exploration", "Forest"])
        dmg, steps, moves = pg.landfall_search(s)
        lines, dealt, n = pg.landfall_lines(s, moves)
        self.assertEqual((dealt, n), (dmg, steps))
        self.assertEqual(lines[:2], ["activate Prodigal Pyromancer", "opponent"])
        self.assertIn("Untap", lines)
        self.assertEqual(lines.count("activate Prodigal Pyromancer"), 1 + sum(
            1 for m in moves if m[0] in ("land", "crack")))

    def test_a_landfall_board_holds_the_bar(self):
        check_drawn_board(self, "landfall", 5, 3000)


class TokenSearchTest(unittest.TestCase):
    def test_the_payoff_waits_for_the_bodies_that_pay_for_it(self):
        # Krenko and two Mountains; Goblin Instigator, Impact Tremors and Skirk Prospector in hand. Skirk first (one
        # Mountain), Krenko makes two tokens, Tremors is paid with the last Mountain and a token, Instigator with the
        # other token and Skirk itself: Instigator and its token enter, 2. Tremors first leaves no mana for Skirk and
        # Krenko makes one token: 1.
        s = {"mountains": 2, "bf": ["Krenko, Mob Boss"],
             "hand": ["Goblin Instigator", "Impact Tremors", "Skirk Prospector"]}
        dmg, steps, moves = pg.token_search(s)
        self.assertEqual((dmg, steps), (2, 4))
        self.assertEqual([m[0] for m in moves], ["cast", "krenko", "cast", "cast"])
        self.assertEqual(pg.token_plain(s), {"tokens_first": 2, "payoff_first": 1})

    def test_mogg_war_marshal_leaves_a_token_for_the_devil(self):
        # The end: Marshal to Skirk Prospector (Devil 1, its token enters: Tremors 1), the token (1), Skirk itself (1).
        s = {"mountains": 0, "bf": ["Impact Tremors", "Mayhem Devil", "Mogg War Marshal", "Skirk Prospector"],
             "hand": []}
        self.assertEqual(pg.token_search(s), (4, 0, (("sweep", ("Mogg War Marshal", "Goblin Token",
                                                                "Skirk Prospector")),)))

    def test_mayhem_devil_in_hand_is_never_cast(self):
        # {1}{B}{R}: Mountains cannot pay it (gate B: five lines that cast it were never offered it).
        s = {"mountains": 5, "bf": ["Skirk Prospector"], "hand": ["Mayhem Devil"]}
        self.assertEqual(pg.token_search(s), (0, 0, ()))
        rng = random.Random(1)
        self.assertFalse(any("Mayhem Devil" in pg.token_draw(rng)["hand"] for _ in range(300)))

    def test_siege_gang_is_asked_its_target_before_the_goblin(self):
        # Siege-Gang alone sacrifices itself (the only Goblin: the engine picks it); the target comes first.
        s = {"mountains": 2, "bf": ["Siege-Gang Commander"], "hand": []}
        dmg, steps, moves = pg.token_search(s)
        self.assertEqual((dmg, steps), (2, 1))
        lines, dealt, n, left = pg.token_lines(s, moves, dmg)
        self.assertEqual(lines, ["activate Mountain", "activate Mountain", "activate Siege-Gang Commander",
                                 "opponent"])
        self.assertEqual((dealt, n, left), (2, 1, 0))

    def test_the_written_line_orders_the_triggers_and_names_the_sacrifice(self):
        s = {"mountains": 2, "bf": ["Krenko, Mob Boss"],
             "hand": ["Goblin Instigator", "Impact Tremors", "Skirk Prospector"]}
        dmg, steps, moves = pg.token_search(s)
        lines, dealt, n, left = pg.token_lines(s, moves, dmg)
        self.assertEqual(lines, ["activate Mountain", "cast Skirk Prospector", "pass", "activate Krenko, Mob Boss",
                                 "pass", "activate Skirk Prospector", "Goblin Token", "activate Mountain",
                                 "cast Impact Tremors", "pass", "activate Skirk Prospector", "Goblin Token",
                                 "activate Skirk Prospector", "Skirk Prospector", "cast Goblin Instigator",
                                 "Impact Tremors; Goblin Instigator"])
        self.assertEqual((dealt, n, left), (2, 4, 0))

    def test_a_tokens_board_holds_the_bar(self):
        check_drawn_board(self, "tokens", 3, 3000)


def check_drawn_board(test, family, seed, tries):
    """The first board the family draws from the seed holds the bar: its goal (poison for infect, life otherwise) is
    the exhaustive maximum in the steps it states with nothing left unused, 13 to 15 steps, and the best plain plan
    falls 1 to 3 short. An infect board's goal is a win."""
    b = next(iter(pg.iter_boards(family, 1, seed, tries=tries)))
    text = b["text"]("x")
    rec = pg.board_record(text)
    test.assertEqual(rec["family"], family)
    best, steps, unused, plain = pg.research(rec)
    goal = 10 - rec["poison"] if family == "infect" else rec["life"]
    test.assertEqual((best, steps, unused), (goal, rec["steps"], 0))
    test.assertEqual((steps, plain), (b["actions"], b["trap"]["plans"]))
    if family != "infect":
        test.assertEqual(best, b["life"])
    test.assertTrue(13 <= steps <= 15, steps)
    test.assertEqual(plain, rec["trap"]["plans"])
    test.assertTrue(1 <= best - max(plain.values()) <= 3, (best, plain))
    if family == "infect":
        test.assertIn("Goal:Win", text)


def check_gen3_family(test, family):
    """Every committed gen3 board of the family, recomputed from the search inputs it states: the goal (poison for
    infect, life otherwise) is the exhaustive maximum, the steps are the line's, nothing is left unused, the best plain
    plan falls 1 to 3 short, and the written line is the one the record writes."""
    paths = sorted(GEN3.glob(family + "_*.pzl"))
    test.assertTrue(paths, "no %s boards in %s" % (family, GEN3))
    for p in paths:
        text = p.read_text(encoding="utf-8")
        rec = pg.board_record(text)
        test.assertEqual(rec["family"], family, p.name)
        test.assertTrue(13 <= rec["steps"] <= 15, (p.name, rec["steps"]))
        best, steps, unused, plain = pg.research(rec)
        goal = 10 - rec["poison"] if family == "infect" else rec["life"]
        test.assertEqual((best, steps), (goal, rec["steps"]), "%s: the goal is the search's maximum" % p.name)
        test.assertEqual(unused, 0, "%s: nothing is left unused" % p.name)
        test.assertEqual(plain, rec["trap"]["plans"], "%s: the plain plans are recomputed" % p.name)
        test.assertEqual(max(plain.values()), rec["trap"]["best"], "%s: the trap's best is the plans' best" % p.name)
        test.assertTrue(1 <= goal - rec["trap"]["best"] <= 3, (p.name, rec["trap"]))
        rule = [l for l in text.splitlines() if l.startswith("Rule:")][0]
        test.assertIn("%d short" % (goal - rec["trap"]["best"]), rule)
        written = [l for l in solution(text) if not l.startswith("#")]
        test.assertEqual(written, pg.written_from_record(rec), "%s: the line is the writer's" % p.name)
    return paths


if __name__ == "__main__":
    unittest.main()
