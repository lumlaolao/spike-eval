"""puzzle_gen.py: the generated families (ping, storm, splice) draw boards of the right length with the life set to
exactly what their line deals, and the splice search finds a line no unspliced line matches."""
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

    def test_a_written_board_is_a_valid_pzl(self):
        out = Path(tempfile.mkdtemp())
        self.assertEqual(pg.main(["--family", "splice", "--count", "3", "--seed", "3", "--out", str(out)]), 0)
        for p in out.glob("*.pzl"):
            goal, turns, start, phase = pf.pzl_goal(p.read_text())
            self.assertEqual((goal, turns, start, phase), ("Survive", 1, 1, "MAIN2"))
            self.assertTrue(re.search(r"^Description:It is your second main phase", p.read_text(), re.M))


if __name__ == "__main__":
    unittest.main()
