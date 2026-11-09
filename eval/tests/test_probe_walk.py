"""probe_walk.py: the menu at the end of a probe prefix, read from the drift message and the router log. No JVM:
a fake run_arm answers each probe line with a result row and the jvm.log text the real one would leave."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import probe_walk as pw  # noqa: E402

HEAD = "[Solver] [ROUTER] play(main): 3 castable -> random baseline (no model call)"


def drift_row(line, at, seat, bound):
    return {"kind": "probe", "status": "probe", "acceptance": "accepted", "goal_bounded": True,
            "search_drift": "draw %d: line has %s, game asked %s:%d" % (at, line.split()[-1], seat, bound)}


class Fake:
    """Answers probes by their PZL_PROBE line: `menus` maps a prefix to (seat, bound); `plays` maps a full line to
    its router lines and `verdicts` to its acceptance."""

    def __init__(self, menus, plays=None, accept=None):
        self.menus, self.plays, self.accept = menus, plays or {}, accept or {}
        self.lines = []

    def __call__(self, build, pzl, arm, seeds, out_dir, env):
        line = env["PZL_PROBE"]
        self.lines.append(line)
        steps = line.split()
        prefix = " ".join(steps[:-1])
        seat, bound = self.menus[prefix]
        if steps[-1].startswith("Solver:9") and steps[-1] not in ("Solver:99:0", "Solver:98:0"):
            raise AssertionError(line)
        sentinel = steps[-1] in ("Solver:99:0", "Solver:98:0")
        if sentinel and int(steps[-1].split(":")[1]) == bound:
            row = {"kind": "probe", "status": "probe", "acceptance": "accepted", "goal_bounded": True}
        elif sentinel:
            row = drift_row(line, len(steps) - 1, seat, bound)
        else:
            row = {"kind": "probe", "status": "probe", "goal_bounded": True,
                   "acceptance": self.accept.get(line, "rejected")}
        text = "\n".join(self.plays.get(line, [HEAD, "[Solver] [ROUTER] play: PASS"]))
        Path(out_dir, "jvm.log").write_text("noise\n" + text + "\n")
        return [row]


class NextDecisionTest(unittest.TestCase):

    def test_seat_and_bound_come_from_the_drift_message(self):
        # mutant: read group 3 as the seat's bound off by one / swap seat and bound -> not ("Opponent", 7)
        row = drift_row("Solver:4:0 Solver:99:0", 1, "Opponent", 7)
        self.assertEqual(pw.next_decision(row, 1, 99), ("Opponent", 7))

    def test_a_prefix_that_drifted_before_the_sentinel_is_refused(self):
        # mutant: drop the `at != n_prefix` check -> the prefix's own mismatch is read as the next menu
        row = drift_row("Solver:4:0 Solver:2:1 Solver:99:0", 0, "Solver", 5)
        with self.assertRaisesRegex(pw.WalkError, "prefix itself drifted at step 0 of 2"):
            pw.next_decision(row, 2, 99)

    def test_a_game_that_ends_after_the_prefix_has_no_menu(self):
        # mutant: return None for line-not-consumed -> the walk tries the 98 sentinel and then lies
        row = {"kind": "probe", "search_drift": "line not consumed: 2 of 3 steps"}
        with self.assertRaisesRegex(pw.WalkError, "game ends after the prefix"):
            pw.next_decision(row, 2, 99)
        short = {"kind": "probe", "search_drift": "line not consumed: 1 of 3 steps"}
        with self.assertRaisesRegex(pw.WalkError, "ended after 1 of the prefix's 2"):
            pw.next_decision(short, 2, 99)

    def test_a_capped_or_missing_or_unreadable_row_is_refused_not_guessed(self):
        # mutant: skip the probe_stopped check -> a draw-capped game's drift would be read as a menu
        with self.assertRaises(pw.WalkError):
            pw.next_decision(None, 0, 99)
        with self.assertRaises(pw.WalkError):
            pw.next_decision({"probe_stopped": True, "search_drift": "draw 0: line has a, game asked Solver:4"}, 0, 99)
        with self.assertRaisesRegex(pw.WalkError, "unreadable"):
            pw.next_decision({"search_drift": "something else"}, 0, 99)

    def test_a_matched_sentinel_is_not_a_menu(self):
        self.assertIsNone(pw.next_decision({"kind": "probe"}, 0, 99))


class FirstNewTest(unittest.TestCase):

    def test_the_shared_prefix_and_header_are_skipped(self):
        # mutant: shared stays 0 -> every value prints the prefix's first line
        runs = [["p1", HEAD, "play: a"], ["p1", HEAD, "play: b"]]
        self.assertEqual(pw.first_new(runs), ["play: a", "play: b"])

    def test_a_cast_shows_its_play_line_not_its_target_line(self):
        # mutant: return r[shared] only -> value 0 shows the target line, not the card cast
        runs = [["h", "target: Moon <- Ray", "play: casts Ray"], ["h", "play: land Depths"]]
        self.assertEqual(pw.first_new(runs), ["play: casts Ray", "play: land Depths"])

    def test_a_non_play_decision_falls_back_to_its_first_new_line(self):
        runs = [["h", "mana: keep 1"], ["h", "mana: keep 0"]]
        self.assertEqual(pw.first_new(runs), ["mana: keep 1", "mana: keep 0"])

    def test_a_run_with_no_new_line_is_none_and_identical_values_all_none(self):
        self.assertEqual(pw.first_new([["h"], ["h"]]), [None, None])
        self.assertEqual(pw.first_new([["h", "play: x"]]), [None])

    def test_router_lines_read_only_router_lines(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "jvm.log").write_text("x\n[Solver] [ROUTER] play: land Forest \n[TALLY] y\n")
            self.assertEqual(pw.router_lines(d), ["play: land Forest"])
            self.assertEqual(pw.router_lines(Path(d) / "none"), [])


class WalkTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        self.said = []

    def walk(self, fake, prefix, **kw):
        return pw.walk(None, "RP_X.pzl", prefix, out_dir=self.out, run=fake, say=self.said.append, **kw)

    def test_the_menu_size_is_read_without_options_in_one_probe(self):
        # mutant: --options behaviour always on -> more than one probe line
        fake = Fake({"Solver:4:0": ("Solver", 2)})
        self.assertEqual(self.walk(fake, "Solver:4:0"), ("Solver", 2, []))
        self.assertEqual(fake.lines, ["Solver:4:0 Solver:99:0"])

    def test_an_empty_prefix_gives_a_bare_sentinel(self):
        # mutant: join with a leading space -> check_probe_line refuses or the JVM gets " Solver:99:0"
        fake = Fake({"": ("Solver", 4)})
        self.assertEqual(self.walk(fake, "  ")[:2], ("Solver", 4))
        self.assertEqual(fake.lines, ["Solver:99:0"])

    def test_options_run_one_probe_per_value_and_print_each_play_and_verdict(self):
        # mutant: range(bound - 1) -> the last value (pass) is missing
        plays = {
            "Solver:4:0": [HEAD, "[Solver] [ROUTER] target: Moon <- Ray", "[Solver] [ROUTER] play: casts Ray"],
            "Solver:4:1": [HEAD, "[Solver] [ROUTER] play: casts Plains"],
            "Solver:4:2": [HEAD, "[Solver] [ROUTER] play: land Depths"],
            "Solver:4:3": [HEAD, "[Solver] [ROUTER] play: PASS"]}
        fake = Fake({"": ("Solver", 4)}, plays, {"Solver:4:1": "accepted"})
        seat, bound, rows = self.walk(fake, "", options=True)
        self.assertEqual((seat, bound), ("Solver", 4))
        self.assertEqual(fake.lines, ["Solver:99:0", "Solver:4:0", "Solver:4:1", "Solver:4:2", "Solver:4:3"])
        self.assertEqual([r[1] for r in rows], ["play: casts Ray", "play: casts Plains", "play: land Depths", "play: PASS"])
        self.assertEqual([r[2] for r in rows], ["rejected", "the misreading reaches the acceptance state",
                                               "rejected", "rejected"])
        self.assertTrue(any("play: casts Ray" in s and "rejected" in s for s in self.said))

    def test_the_value_probes_extend_the_prefix_with_the_menu_seat(self):
        # mutant: always "Solver:" -> an Opponent menu is probed as the wrong seat and drifts
        fake = Fake({"Solver:4:0": ("Opponent", 2)})
        self.walk(fake, "Solver:4:0", options=True, opponent="search")
        self.assertEqual(fake.lines[1:], ["Solver:4:0 Opponent:2:0", "Solver:4:0 Opponent:2:1"])

    def test_the_second_sentinel_is_tried_when_the_game_really_asks_99(self):
        # mutant: SENTINEL_BOUNDS = (99,) -> a 99-option menu is reported as an error
        fake = Fake({"": ("Solver", 99)})
        self.assertEqual(self.walk(fake, "")[:2], ("Solver", 99))
        self.assertEqual(fake.lines, ["Solver:99:0", "Solver:98:0"])

    def test_a_menu_over_the_cap_is_refused_before_its_probes_run(self):
        # mutant: no cap check -> 70 JVMs
        fake = Fake({"": ("Solver", 70)})
        with self.assertRaisesRegex(pw.WalkError, "more than --max-options 64"):
            self.walk(fake, "", options=True)
        self.assertEqual(fake.lines, ["Solver:99:0"])
        self.assertEqual(self.walk(Fake({"": ("Solver", 70)}), "", options=True, max_options=70)[1], 70)

    def test_an_invalid_prefix_is_refused_before_a_probe(self):
        with self.assertRaises(SystemExit):
            self.walk(Fake({}), "Solver:2:5")


class MainTest(unittest.TestCase):

    def test_a_walk_error_is_exit_1_with_the_reason_on_stderr(self):
        with tempfile.TemporaryDirectory() as d:
            pzl = Path(d) / "P.pzl"
            pzl.write_text("[metadata]\n[acceptance]\nx\n")
            with mock.patch.object(pw.pb, "Build"), \
                    mock.patch.object(pw, "walk", side_effect=pw.WalkError("no menu")), \
                    mock.patch("sys.stderr") as err:
                self.assertEqual(pw.main(["--pzl", str(pzl)]), 1)
                self.assertIn("no menu", "".join(c.args[0] for c in err.write.call_args_list))

    def test_a_puzzle_without_acceptance_is_refused_before_the_build(self):
        with tempfile.TemporaryDirectory() as d:
            pzl = Path(d) / "P.pzl"
            pzl.write_text("[metadata]\nname=x\n")
            with mock.patch.object(pw.pb, "Build") as build:
                with self.assertRaises(SystemExit):
                    pw.main(["--pzl", str(pzl)])
                build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
