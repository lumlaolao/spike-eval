"""contrast_pairs.py: splice boards one or two details apart whose winning lines differ, from the board's model."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import contrast_pairs as cp  # noqa: E402

RITUAL, SPIKE, RAY, BOLT, SHOCK = "Desperate Ritual", "Lava Spike", "Glacial Ray", "Lightning Bolt", "Shock"

PZL = """[metadata]
Name:splice_test
[state]
turn=1
activeplayer=p0
activephase=MAIN2
p0life=20
p0hand=Shock;Desperate Ritual;Shock;Shock;Lightning Bolt;Glacial Ray;Desperate Ritual
p0battlefield=Thermo-Alchemist;Mountain;Mountain;Mountain
p0library=Colossal Dreadmaw
p1life=19
p1library=Plains
[solution]
pass
"""


def board(hand, mountains=3, tapped=0, alch=True, alch_untapped=True, opp=19):
    return cp.Board(tuple(sorted(hand)), mountains, tapped, alch, alch and alch_untapped, opp)


# splice_a80fa0f9f24d: three Mountains, so the only first cast that keeps the win is Desperate Ritual alone.
A80 = board([SHOCK, SHOCK, SHOCK, RITUAL, RITUAL, BOLT, RAY])


class ReadBoard(unittest.TestCase):
    def test_reads_hand_lands_alchemist_and_life(self):
        self.assertEqual(cp.read_board(PZL), A80)

    def test_tapped_permanents(self):
        text = PZL.replace("p0battlefield=Thermo-Alchemist;Mountain;Mountain;Mountain",
                           "p0battlefield=Thermo-Alchemist|Tapped;Mountain;Mountain|Tapped;Mountain")
        b = cp.read_board(text)
        self.assertEqual((b.mountains, b.tapped, b.alch, b.untapped), (2, 1, True, False))

    def test_a_card_outside_the_model_is_refused(self):
        with self.assertRaises(cp.NotModeled):
            cp.read_board(PZL.replace("Lightning Bolt", "Rite of Flame"))
        with self.assertRaises(cp.NotModeled):
            cp.read_board(PZL.replace("Thermo-Alchemist;", "Kiln Fiend;Thermo-Alchemist;"))


class Model(unittest.TestCase):
    def test_value_matches_the_generator(self):
        self.assertEqual(cp.best(A80), 19)
        self.assertTrue(cp.winnable(A80))
        self.assertFalse(cp.winnable(A80._replace(opp=20)))

    def test_winning_first_casts(self):
        self.assertEqual(cp.winning_moves(A80, cp.start(A80)), {(RITUAL, ())})


class Pairs(unittest.TestCase):
    def test_a_resource_change_alone_never_contrasts(self):
        # More of anything at the same life keeps every winning line of the smaller board (the model is monotone).
        more = A80._replace(mountains=4)
        self.assertIsNone(cp.divergences(A80, more))
        self.assertIsNone(cp.divergences(A80, A80._replace(opp=18)))
        self.assertIsNone(cp.divergences(A80, A80._replace(hand=tuple(sorted(A80.hand + (SPIKE,))))))

    def test_the_ritual_must_be_cast_alone_on_one_side_and_spliced_on_the_other(self):
        b = A80._replace(hand=tuple(sorted([SHOCK, SHOCK, SHOCK, RITUAL, SPIKE, BOLT, RAY])))
        div = cp.divergences(A80, b)
        self.assertEqual(len(div), 1)
        d = div[0]
        self.assertEqual(d["prefix"], ())
        self.assertEqual(d["win_a"], [(RITUAL, ())])
        self.assertEqual(d["win_b"], [(SPIKE, (RITUAL,))])
        self.assertEqual(d["trap"], "a_on_b")       # A's move is legal on B and loses there; B's is not legal on A
        rec = cp.classify(A80, b)
        self.assertEqual((rec["trap"], rec["depth"]), ("one-way", 0))

    def test_perturbations_name_their_details(self):
        ps = {name: (details, b) for name, details, _, b in cp.perturbations(A80)}
        self.assertEqual(ps["one more Mountain"][1].mountains, 4)
        self.assertEqual(ps["a Mountain tapped"][1][1:3], (2, 1))
        self.assertEqual(ps["Thermo-Alchemist tapped"][1].untapped, False)
        self.assertEqual(ps["opponent at 20"][0], 1)
        self.assertEqual(ps["Desperate Ritual -> Lava Spike"][0], 1)
        self.assertEqual(ps["one more Mountain; opponent at 20"][0], 2)
        self.assertEqual(sum(1 for _ in ps), len(set(ps)))


class Lines(unittest.TestCase):
    def test_each_side_line_wins_its_board_and_the_cross_line_is_short(self):
        b = A80._replace(hand=tuple(sorted([SHOCK, SHOCK, SHOCK, RITUAL, SPIKE, BOLT, RAY])))
        lines = cp.pair_lines(A80, b)
        self.assertGreaterEqual(lines["a_on_a"]["dealt"], 19)
        self.assertGreaterEqual(lines["b_on_b"]["dealt"], 19)
        self.assertEqual(lines["a_on_a"]["script"][:2], ["activate Thermo-Alchemist", "cast Desperate Ritual"])
        self.assertIn("cast Lava Spike", lines["b_on_b"]["script"][:3])
        self.assertLess(lines["a_on_b"]["dealt"], 19)   # A's line, every play legal on B, falls short there
        self.assertIsNone(lines["b_on_a"])               # B's first cast (Lava Spike) is not in A's hand

    def test_pzl_writes_tapped_permanents_and_the_life(self):
        b = A80._replace(mountains=2, tapped=1, untapped=False, opp=17)
        text = cp.pzl(b, "contrast_x", ["pass"])
        self.assertIn("p0battlefield=Thermo-Alchemist|Tapped;Mountain;Mountain;Mountain|Tapped", text)
        self.assertIn("p1life=17", text)
        self.assertIn("a tapped Thermo-Alchemist", text)
        self.assertIn("two untapped Mountains and one tapped Mountain", text)
        self.assertEqual(cp.read_board(text), b)


if __name__ == "__main__":
    unittest.main()
