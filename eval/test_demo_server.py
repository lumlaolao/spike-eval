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
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
import demo_server as srv

FIX = os.path.join(HERE, "testdata", "demo")


def serve(panes):
    s = srv.make_server(panes, port=0)
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
        # eval/demo_server.py (checked by removing it once).
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
        _, games = get(self.url + "/api/games")
        self.assertEqual(games[0]["path"], self.dir)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            get(self.url + "/card/Black%20Lotus")
        self.assertEqual(cm.exception.code, 404)  # this server has no image cache


class LiveTest(unittest.TestCase):
    def test_a_live_pane_waits_for_the_file_then_follows_it_to_the_game_row(self):
        d = tempfile.mkdtemp()
        try:
            server, url = serve([srv.Pane(d, live=True)])
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
            server.shutdown()
            server.server_close()
        finally:
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
