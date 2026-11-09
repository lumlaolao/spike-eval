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
        self.lines, self.envs, self.seeds = [], [], []

    def __call__(self, build, pzl, arm, seeds, out_dir, env):
        line = env["PZL_PROBE"]
        self.lines.append(line)
        self.envs.append(env)
        self.seeds.append(seeds)
        steps = line.split()
        prefix = " ".join(steps[:-1])
        seat, bound = self.menus[prefix]
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
        self.assertEqual(pw.next_decision(row, 1), ("Opponent", 7))

    def test_a_prefix_that_drifted_before_the_sentinel_is_refused(self):
        # mutant: drop the `at != n_prefix` check -> the prefix's own mismatch is read as the next menu
        row = drift_row("Solver:4:0 Solver:2:1 Solver:99:0", 0, "Solver", 5)
        with self.assertRaisesRegex(pw.WalkError, "prefix itself drifted at step 0 of 2"):
            pw.next_decision(row, 2)

    def test_a_game_that_ends_after_the_prefix_has_no_menu(self):
        # mutant: return None for line-not-consumed -> the walk tries the 98 sentinel and then lies
        row = {"kind": "probe", "search_drift": "line not consumed: 2 of 3 steps"}
        with self.assertRaisesRegex(pw.WalkError, "game ends after the prefix"):
            pw.next_decision(row, 2)
        short = {"kind": "probe", "search_drift": "line not consumed: 1 of 3 steps"}
        with self.assertRaisesRegex(pw.WalkError, "ended after 1 of the prefix's 2"):
            pw.next_decision(short, 2)

    def test_a_capped_row_with_a_drift_is_read(self):
        # mutant: check probe_stopped / status invalid before the drift -> the menu the sentinel found is thrown away
        drift = "draw 1: line has Solver:99:0, game asked Solver:4"
        for extra in ({"probe_stopped": True}, {"status": "invalid"}):
            row = dict({"kind": "probe", "search_drift": drift}, **extra)
            self.assertEqual(pw.next_decision(row, 1), ("Solver", 4))

    def test_a_capped_row_without_a_drift_is_refused(self):
        # mutant: drop the probe_stopped / invalid check -> a capped game with no drift reads as a matched sentinel
        for extra in ({"probe_stopped": True}, {"status": "invalid"}):
            for drift in ("", "line not consumed: 1 of 3 steps"):
                row = dict({"kind": "probe", "search_drift": drift}, **extra)
                with self.assertRaisesRegex(pw.WalkError, "draw cap or the wall clock"):
                    pw.next_decision(row, 1)

    def test_a_capped_row_whose_drift_is_at_the_wrong_step_still_names_the_prefix(self):
        row = {"kind": "probe", "probe_stopped": True, "search_drift": "draw 0: line has a, game asked Solver:4"}
        with self.assertRaisesRegex(pw.WalkError, "prefix itself drifted"):
            pw.next_decision(row, 2)

    def test_a_missing_unreadable_or_non_probe_row_is_refused(self):
        # mutant: no kind check -> a search/proof row is read as a probe; mutant: unreadable drift -> None
        with self.assertRaisesRegex(pw.WalkError, "no result row"):
            pw.next_decision(None, 0)
        with self.assertRaisesRegex(pw.WalkError, "not a probe row"):
            pw.next_decision({"kind": "search", "search_drift": "draw 0: line has a, game asked Solver:4"}, 0)
        with self.assertRaisesRegex(pw.WalkError, "unreadable"):
            pw.next_decision({"kind": "probe", "search_drift": "something else"}, 0)

    def test_a_matched_sentinel_is_not_a_menu(self):
        self.assertIsNone(pw.next_decision({"kind": "probe"}, 0))


class FirstNewTest(unittest.TestCase):

    def test_the_shared_prefix_and_header_are_skipped(self):
        # mutant: shared stays 0 -> every value prints the prefix's first line
        runs = [["p1", HEAD, "play: a"], ["p1", HEAD, "play: b"]]
        self.assertEqual(pw.first_new(runs), ["play: a", "play: b"])

    def test_a_cast_shows_its_target_and_its_play(self):
        # mutant: return r[shared] only -> the target line without the card cast
        runs = [["h", "target: Moon <- Ray", "play: casts Ray"], ["h", "play: land Depths"]]
        self.assertEqual(pw.first_new(runs), ["target: Moon <- Ray | play: casts Ray", "play: land Depths"])

    def test_two_targets_with_the_same_play_stay_distinct(self):
        # mutant: show the next play: line only -> both values read "play: casts Ray"
        runs = [["h", "target: Moon <- Ray", "play: casts Ray"], ["h", "target: Sun <- Ray", "play: casts Ray"]]
        self.assertEqual(pw.first_new(runs), ["target: Moon <- Ray | play: casts Ray",
                                              "target: Sun <- Ray | play: casts Ray"])

    def test_a_target_takes_the_play_after_it_not_a_prefix_play_before_it(self):
        # mutant: scan r[0:] for the play -> the prefix's own "play: land Forest" is shown with the target
        runs = [["play: land Forest", HEAD, "target: Moon <- Ray", "play: casts Ray"],
                ["play: land Forest", HEAD, "play: PASS"]]
        self.assertEqual(pw.first_new(runs), ["target: Moon <- Ray | play: casts Ray", "play: PASS"])

    def test_a_target_with_no_play_after_it_is_shown_alone(self):
        self.assertEqual(pw.first_new([["h", "target: A"], ["h", "play: x"]]), ["target: A", "play: x"])

    def test_a_non_play_draw_does_not_borrow_a_later_completion_play(self):
        # mutant: scan for the next play: line -> value 0 reads "play: casts Ray", value 1 "play: PASS"
        runs = [["h", "mana: keep 1", "play: casts Ray"], ["h", "mana: keep 0", "play: PASS"]]
        self.assertEqual(pw.first_new(runs), ["mana: keep 1", "mana: keep 0"])

    def test_a_run_with_no_new_line_is_none_and_identical_logs_have_no_difference(self):
        # mutant: None for identical logs -> the table prints "(no router line)" for a menu that does not differ
        self.assertEqual(pw.first_new([["h"], ["h"]]), ["(no difference)"] * 2)
        self.assertEqual(pw.first_new([["h", "play: x"]]), ["(no difference)"])
        self.assertEqual(pw.first_new([["h"], ["h", "play: x"]]), [None, "play: x"])

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
        self.assertEqual([r[1] for r in rows], ["target: Moon <- Ray | play: casts Ray", "play: casts Plains", "play: land Depths", "play: PASS"])
        self.assertEqual([r[2] for r in rows], ["rejected", "the misreading reaches the acceptance state",
                                               "rejected", "rejected"])
        self.assertTrue(any("play: casts Ray" in s and "rejected" in s for s in self.said))
        self.assertIn("seed 1, completion seed 0, opponent ai", self.said[0])

    def test_the_value_probes_extend_the_prefix_with_the_menu_seat(self):
        # mutant: always "Solver:" -> an Opponent menu is probed as the wrong seat and drifts
        fake = Fake({"Solver:4:0": ("Opponent", 2)})
        self.walk(fake, "Solver:4:0", options=True, opponent="search")
        self.assertEqual(fake.lines[1:], ["Solver:4:0 Opponent:2:0", "Solver:4:0 Opponent:2:1"])

    def test_seed_completion_seed_and_opponent_reach_every_probe(self):
        # mutants: drop opponent / completion_seed / str(seed) in probe() -> the env keeps its default
        fake = Fake({"": ("Solver", 2)})
        self.walk(fake, "", options=True, opponent="search", completion_seed=7, seed=5)
        self.assertEqual(len(fake.envs), 3)
        for env in fake.envs:
            self.assertEqual((env["CONTROLLER_B"], env["PZL_PROBE_COMPLETION_SEED"]), ("search", "7"))
        self.assertEqual(fake.seeds, ["5"] * 3)  # run_arm's `seeds` is what becomes PZL_SEEDS
        fake = Fake({"": ("Solver", 2)})
        self.walk(fake, "")
        self.assertEqual((fake.envs[0]["CONTROLLER_B"], fake.envs[0]["PZL_PROBE_COMPLETION_SEED"], fake.seeds),
                         ("ai", "0", ["1"]))

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

    def run_main(self, argv, **patches):
        with tempfile.TemporaryDirectory() as d:
            pzl = Path(d) / "P.pzl"
            pzl.write_text("[metadata]\n[acceptance]\nx\n")
            with mock.patch.object(pw.pb, "Build"), mock.patch.object(pw, "walk", **patches) as walk, \
                    mock.patch("sys.stderr") as err:
                code = pw.main(["--pzl", str(pzl)] + argv)
                return code, "".join(c.args[0] for c in err.write.call_args_list), walk

    def test_a_walk_error_is_exit_1_with_the_reason_on_stderr(self):
        code, err, _ = self.run_main([], side_effect=pw.WalkError("no menu"))
        self.assertEqual(code, 1)
        self.assertIn("probe_walk: no menu", err)

    def test_a_run_arm_failure_is_exit_1_not_a_traceback(self):
        # mutant: catch WalkError only -> RuntimeError escapes main
        code, err, _ = self.run_main([], side_effect=RuntimeError("JVM exit 1 and no rows"))
        self.assertEqual(code, 1)
        self.assertIn("probe_walk: JVM exit 1 and no rows", err)

    def test_the_options_reach_walk(self):
        # mutant: a swapped positional -> the opponent or seed lands in the wrong walk argument
        _, _, walk = self.run_main(["--prefix", "Solver:4:0", "--opponent", "search", "--seed", "3",
                                    "--completion-seed", "9", "--options"], return_value=None)
        args = walk.call_args.args
        self.assertEqual(args[2:], ("Solver:4:0", "search", 3, 9, None, True, pw.MAX_OPTIONS))
        # mutant: --out-dir or --max-options not passed -> walk writes to the default dir or keeps the default cap
        _, _, walk = self.run_main(["--out-dir", "/tmp/w", "--max-options", "5"], return_value=None)
        self.assertEqual(walk.call_args.args[6:], ("/tmp/w", False, 5))

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
