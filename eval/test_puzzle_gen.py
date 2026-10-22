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


GEN2 = Path(__file__).resolve().parent / "puzzles" / "hard" / "gen2"


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

    GEN2 = Path(__file__).resolve().parent / "puzzles" / "hard" / "gen2"

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
    def test_a_resumed_gate_reuses_a_boards_line_result(self):
        # A gate stopped by a time limit is run again with --resume: a board whose line already has a result is
        # not played again.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "puzzle-gate"))
        import argparse
        import json
        import gen_gate
        tmp = Path(tempfile.mkdtemp())
        pzl = tmp / "drain2_x.pzl"
        pzl.write_text("[solution]\n# steps: 13\npass\n", encoding="utf-8")
        done = tmp / "out" / "line" / "drain2_x" / "a"
        done.mkdir(parents=True)
        (done / "puzzle-results.jsonl").write_text(json.dumps({"solved": True, "script_played": True,
                                                               "solver_actions": 20}) + "\n")
        a = argparse.Namespace(out=str(tmp / "out"), build="b", forge="f", resume=True)
        real = gen_gate.subprocess.run
        gen_gate.subprocess.run = lambda *x, **k: self.fail("the line was played again")
        try:
            row = gen_gate.play_line(a, pzl)
        finally:
            gen_gate.subprocess.run = real
        self.assertEqual((row["solved"], row["actions"]), (True, 13))
        self.assertNotIn("dropped", row)


if __name__ == "__main__":
    unittest.main()
