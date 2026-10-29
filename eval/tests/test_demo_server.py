"""demo_server: the viewer's API serves every decision of the file, pages by `from`, and follows a file a JVM is still
writing (a live pane survives the file not existing yet and ends on the game row)."""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)

HERE = str(evalpath.EVAL)
import demo_server as srv

FIX = os.path.join(HERE, "testdata", "demo")


def serve(panes, cards=None):
    s = srv.make_server(panes, port=0, cards=cards)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, "http://127.0.0.1:%d" % s.server_address[1]


def get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        body = r.read()
        return r.status, (json.loads(body) if r.headers.get("Content-Type", "").startswith("application/json") else body)


class ReplayTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        with open(os.path.join(self.dir, "game-4133-1.jsonl"), "w") as out:
            for name in ("g33-set-plan.jsonl", "g33-simulate.jsonl", "g33-game.jsonl"):
                with open(os.path.join(FIX, name)) as f:
                    out.write(f.read())
        with open(os.path.join(self.dir, "game-4133-0.log"), "w") as f:
            f.write("[Seat] [TALLY] smart=9 fast=0 auto=0 ai=0 textParses=0 avgSmart=7000ms avgFast=7000ms\n")
        self.server, self.url = serve([srv.Pane(self.dir)])

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.dir)

    def test_the_page_and_its_assets_are_served(self):
        code, body = get(self.url + "/")
        self.assertEqual(code, 200)
        self.assertIn(b"demo.js", body)
        self.assertEqual(get(self.url + "/demo.css")[0], 200)

    def test_events_hold_every_decision_row_and_page_by_from(self):
        _, r = get(self.url + "/api/game/0/events")
        decisions = [e for e in r["events"] if e["kind"] == "decision"]
        self.assertEqual(len(decisions), 2)  # one decision row in each of the two slices
        self.assertTrue(r["done"])
        _, tail = get(self.url + "/api/game/0/events?from=%d" % (r["total"] - 1))
        self.assertEqual([e["kind"] for e in tail["events"]], ["game"])

    def test_the_summary_carries_the_tally_and_the_review_sheet(self):
        _, games = get(self.url + "/api/games")
        s = games[0]["summary"]
        self.assertEqual(s["tally"]["Seat"]["ai"], "0")
        self.assertEqual(s["game"]["winner"], "A")
        self.assertIn("model decisions", s["review"])

    def test_a_path_outside_the_web_directory_is_refused(self):
        # A raw request line: a client library might normalise the dots away. Without the guard this serves
        # eval/demo/demo_server.py (checked by removing it once).
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        conn.request("GET", "/../../demo_server.py")
        r = conn.getresponse()
        r.read()
        conn.close()
        self.assertEqual(r.status, 404)

    def test_a_bad_from_is_a_400_not_a_traceback(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            get(self.url + "/api/game/0/events?from=abc")
        self.assertEqual(cm.exception.code, 400)

    def test_a_card_image_comes_from_the_cache_and_a_miss_is_404(self):
        asked = []

        def fake(name):
            asked.append(name)
            return None if "Token" in name else b"\xff\xd8" + name.encode()
        cache_dir = tempfile.mkdtemp()
        try:
            server, url = serve([srv.Pane(self.dir)], cards=srv.demo_cards.Cache(cache_dir, fetch=fake))
            with urllib.request.urlopen(url + "/card/Fire%20%2F%2F%20Ice", timeout=10) as r:
                self.assertEqual((r.status, r.headers["Content-Type"]), (200, "image/jpeg"))
                self.assertEqual(r.read(), b"\xff\xd8Fire // Ice")
            for _ in range(2):
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    get(url + "/card/Construct%20Token")
                self.assertEqual(cm.exception.code, 404)
            self.assertEqual(asked, ["Fire // Ice", "Construct Token"])  # a miss is asked for once
            self.assertEqual(cm.exception.headers["Cache-Control"], "max-age=86400")  # a miss never changes
            self.assertEqual(cm.exception.headers["X-Image-Miss"], "1")  # and says so: the page stops asking
            offline = srv.demo_cards.Cache(cache_dir, fetch=fake, offline=True)
            server2, url2 = serve([srv.Pane(self.dir)], cards=offline)
            with self.assertRaises(urllib.error.HTTPError) as cm:
                get(url2 + "/card/Time%20Walk")  # not cached and not fetched: it may have an image later
            self.assertEqual(cm.exception.headers["Cache-Control"], "no-store")
            self.assertIsNone(cm.exception.headers["X-Image-Miss"])
        finally:
            for s in [x for x in (locals().get("server"), locals().get("server2")) if x]:
                s.shutdown()
                s.server_close()
            shutil.rmtree(cache_dir)


class DivergenceEndpointTest(unittest.TestCase):
    def test_two_panes_get_their_divergence_and_one_pane_gets_none(self):
        d = tempfile.mkdtemp()
        try:
            for sub, chosen in (("now", 0), ("then", 1)):
                os.makedirs(os.path.join(d, sub))
                with open(os.path.join(d, sub, "game-7-1.jsonl"), "w") as f:
                    f.write(json.dumps({"t": "decision", "seat": "Seat", "turn": 1, "phase": "MAIN1",
                                        "type": "play", "options": ["PLAY Tundra @7", "PASS"],
                                        "choice": chosen, "tier": "smart"}) + "\n")
            server, url = serve([srv.Pane(os.path.join(d, "now")), srv.Pane(os.path.join(d, "then"))])
            try:
                self.assertEqual(get(url + "/api/divergence")[1]["reason"], "same question, different answer")
            finally:
                server.shutdown()
                server.server_close()
            server, url = serve([srv.Pane(os.path.join(d, "now"))])
            try:
                self.assertIsNone(get(url + "/api/divergence")[1])
            finally:
                server.shutdown()
                server.server_close()
        finally:
            shutil.rmtree(d)


class PuzzleTest(unittest.TestCase):
    def test_a_puzzle_run_is_done_on_its_result_row_and_its_summary_carries_the_puzzle(self):
        d = tempfile.mkdtemp()
        try:
            game = os.path.join(FIX, "puzzle", "game-1-1792546270115.jsonl")
            shutil.copy(game, d)
            pane = srv.Pane(d)
            self.assertFalse(pane.done)  # a puzzle writes no game row; until its result row lands, not done
            shutil.copy(os.path.join(FIX, "puzzle", "puzzle-results.jsonl"), d)
            pane = srv.Pane(d)
            self.assertTrue(pane.done)
            self.assertEqual(pane.summary()["puzzle"]["solver"]["kind"], "written line")
            # a newer attempt (seed 2) in the same directory, its result not written yet: not done, nothing borrowed
            shutil.copy(game, os.path.join(d, "game-2-1792546299999.jsonl"))
            live = srv.Pane(d, live=True)
            self.assertTrue(live.reader.path.endswith("game-2-1792546299999.jsonl"))
            self.assertFalse(live.done)
            self.assertIsNone(live.summary()["puzzle"])
        finally:
            shutil.rmtree(d)

    def test_a_second_run_of_a_seed_pairs_each_game_with_its_own_row(self):
        fix = os.path.join(FIX, "puzzle")
        with open(os.path.join(fix, "puzzle-results.jsonl")) as f:
            row = json.loads(f.readline())
        d = tempfile.mkdtemp()
        try:
            old, new = os.path.join(d, "game-1-1792546270115.jsonl"), os.path.join(d, "game-1-1792546999999.jsonl")
            results = os.path.join(d, "puzzle-results.jsonl")
            shutil.copy(os.path.join(fix, "game-1-1792546270115.jsonl"), old)
            shutil.copy(old, new)

            def write(rows, t_old, t_results, t_new):
                with open(results, "w") as f:
                    f.writelines(json.dumps(r) + "\n" for r in rows)
                for path, t in ((old, t_old), (results, t_results), (new, t_new)):
                    os.utime(path, (t, t))
            t = 1792546000  # epoch seconds near the games' start times in their names (270.115 s and 999.999 s later)
            # puzzle_solve appends: the rehearsal's row is there, the real attempt still plays (written after it)
            write([dict(row, solved=False, status="failed")], t + 300, t + 500, t + 1200)
            live = srv.Pane(d, live=True)
            self.assertTrue(live.reader.path.endswith("game-1-1792546999999.jsonl"))
            self.assertEqual((live.done, live.summary()["puzzle"]), (False, None))
            # the real attempt has just started (after the row was written), its first write within the slack
            write([dict(row, solved=False, status="failed")], t + 300, t + 999, t + 1000.5)
            self.assertIsNone(srv.Pane(new).summary()["puzzle"])
            # it ends: two rows, each game its own
            write([dict(row, solved=False, status="failed"), row], t + 300, t + 1300, t + 1290)
            self.assertTrue(srv.Pane(new).summary()["puzzle"]["solved"])
            self.assertFalse(srv.Pane(old).summary()["puzzle"]["solved"])
            # puzzle_baselines line rewrites the file and keeps the old game: the one row is the newest game's
            write([row], t + 300, t + 1300, t + 1290)
            self.assertTrue(srv.Pane(d).done)
            self.assertTrue(srv.Pane(new).summary()["puzzle"]["solved"])
            self.assertIsNone(srv.Pane(old).summary()["puzzle"])
        finally:
            shutil.rmtree(d)

    def test_a_game_named_from_its_own_directory_is_found(self):
        d, here = tempfile.mkdtemp(), os.getcwd()
        try:
            for name in ("game-1-1792546270115.jsonl", "puzzle-results.jsonl"):
                shutil.copy(os.path.join(FIX, "puzzle", name), d)
            os.chdir(d)
            self.assertTrue(srv.Pane("game-1-1792546270115.jsonl").done)  # dirname "" is the working directory
        finally:
            os.chdir(here)
            shutil.rmtree(d)


class LoneGameTest(unittest.TestCase):
    def test_a_lone_game_of_its_seed_takes_its_row_whatever_the_file_times(self):
        d = tempfile.mkdtemp()
        try:
            for name in ("game-1-1792546270115.jsonl", "puzzle-results.jsonl"):
                shutil.copy(os.path.join(FIX, "puzzle", name), d)
            # a clock behind the recording, or a game file rewritten by a later checkout: times say nothing here
            os.utime(os.path.join(d, "puzzle-results.jsonl"), (1000, 1000))
            self.assertTrue(srv.Pane(d).summary()["puzzle"]["solved"])
        finally:
            shutil.rmtree(d)

    def test_a_file_named_like_a_game_after_a_prefix_takes_no_row(self):
        d = tempfile.mkdtemp()
        try:  # the server and result_row agree: only a name that starts game-<seed>- has a seed
            game = os.path.join(FIX, "puzzle", "game-1-1792546270115.jsonl")
            copy = os.path.join(d, "copy-game-1-1792546299999.jsonl")
            shutil.copy(game, d)
            shutil.copy(game, copy)
            shutil.copy(os.path.join(FIX, "puzzle", "puzzle-results.jsonl"), d)
            self.assertIsNone(srv.Pane(copy).summary()["puzzle"])
        finally:
            shutil.rmtree(d)

    def test_a_game_file_without_a_seed_in_its_name_takes_no_row(self):
        d = tempfile.mkdtemp()
        try:
            shutil.copy(os.path.join(FIX, "puzzle", "game-1-1792546270115.jsonl"), os.path.join(d, "game-copy.jsonl"))
            shutil.copy(os.path.join(FIX, "puzzle", "puzzle-results.jsonl"), d)
            pane = srv.Pane(os.path.join(d, "game-copy.jsonl"))
            self.assertIsNone(pane.summary()["puzzle"])
        finally:
            shutil.rmtree(d)


class ArmTest(unittest.TestCase):
    def test_the_summary_names_the_arm_demo_compare_recorded(self):
        d = tempfile.mkdtemp()
        try:
            shutil.copy(os.path.join(FIX, "g33-game.jsonl"), os.path.join(d, "game-4133-1.jsonl"))
            self.assertIsNone(srv.Pane(d).summary()["arm"])  # no arm.json: the viewer falls back to the directory name
            arm = {"arm": "then", "commit": "e1324cfdf5b5e830a9a9684cee6c9fd09a616f33", "date": "2026-10-14"}
            with open(os.path.join(d, "arm.json"), "w") as f:  # what demo_compare.arm_record writes
                json.dump(arm, f)
            self.assertEqual(srv.Pane(d).summary()["arm"], arm)
        finally:
            shutil.rmtree(d)


class NotAPuzzleTest(unittest.TestCase):
    def test_a_replayed_game_with_no_puzzle_results_is_never_looked_up(self):
        def boom(*a):
            raise AssertionError("looked up a puzzle for an ordinary game")
        d = tempfile.mkdtemp()
        try:
            shutil.copy(os.path.join(FIX, "g33-game.jsonl"), os.path.join(d, "game-4133-1.jsonl"))
            with mock.patch.object(srv.demo_puzzle, "info", boom):
                pane = srv.Pane(d)
                self.assertIsNone(pane.puzzle())
                self.assertIsNone(pane.puzzle())
        finally:
            shutil.rmtree(d)


class SummaryTest(unittest.TestCase):
    def test_a_summary_built_while_the_game_ended_is_not_kept(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "game-4133-1.jsonl")
            with open(os.path.join(FIX, "g33-simulate.jsonl")) as f, open(path, "w") as out:
                out.write(f.read())
            pane = srv.Pane(d, live=True)
            real = srv.build_summary

            def slow(p):
                s = real(p)
                with open(os.path.join(FIX, "g33-game.jsonl")) as f, open(path, "a") as out:
                    out.write(f.read())  # the game row lands while this summary is being built
                p.refresh()
                return s
            srv.build_summary = slow
            try:
                self.assertIsNone(pane.summary()["game"])
            finally:
                srv.build_summary = real
            self.assertEqual(pane.summary()["game"]["winner"], "A")  # rebuilt for the finished game
        finally:
            shutil.rmtree(d)


class LiveTest(unittest.TestCase):
    def test_a_live_pane_waits_for_the_file_then_follows_it_to_the_game_row(self):
        d = tempfile.mkdtemp()
        server, url = serve([srv.Pane(d, live=True)])
        try:
            _, r = get(url + "/api/game/0/events")
            self.assertTrue(r["waiting_for_file"])
            self.assertEqual(r["events"], [])
            with open(os.path.join(FIX, "g33-simulate.jsonl")) as f:
                rows = f.read().splitlines()
            path = os.path.join(d, "game-4133-2.jsonl")

            def write_later():
                time.sleep(0.6)
                with open(path, "w") as f:
                    f.write("\n".join(rows) + "\n")
            threading.Thread(target=write_later).start()
            t0 = time.time()
            _, r = get(url + "/api/game/0/events?from=0&wait=1")  # the long poll returns when the decision lands
            self.assertLess(time.time() - t0, 10)
            self.assertEqual([e["kind"] for e in r["events"]], ["decision"])
            self.assertFalse(r["done"])
            with open(os.path.join(FIX, "g33-game.jsonl")) as f, open(path, "a") as out:
                out.write(f.read())
            _, r = get(url + "/api/game/0/events?from=1&wait=1")
            self.assertEqual([e["kind"] for e in r["events"]], ["game"])
            self.assertTrue(r["done"])
        finally:
            server.shutdown()
            server.server_close()
            shutil.rmtree(d)

    def test_a_live_pane_moves_to_a_newer_game_written_into_the_same_directory(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(FIX, "g33-simulate.jsonl")) as f:
                sim = f.read()
            with open(os.path.join(FIX, "g33-game.jsonl")) as f:
                game = f.read()
            with open(os.path.join(d, "game-1-100.jsonl"), "w") as f:
                f.write(sim + game)  # the finished rehearsal
            pane = srv.Pane(d, live=True)
            self.assertTrue(pane.done)
            gen = pane.gen
            later = os.path.join(d, "game-2-200.jsonl")
            with open(later, "w") as f:
                f.write(sim)  # the real game, started after
            os.utime(later, (time.time() + 5, time.time() + 5))
            pane.refresh()
            self.assertEqual(pane.reader.path, later)
            self.assertFalse(pane.done)
            self.assertEqual(pane.gen, gen + 1)  # the client sees the generation move and starts over
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
