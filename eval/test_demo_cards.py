"""demo_cards: a card image is fetched once and then served from disk; a name Scryfall lacks is asked for once; an
offline cache never touches the network. The fetcher is faked: no test reaches the network."""
import os
import shutil
import tempfile
import threading
import time
import unittest

import demo_cards as dc

HERE = os.path.dirname(os.path.abspath(__file__))


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.asked = []

    def tearDown(self):
        shutil.rmtree(self.dir)

    def fetch(self, name):
        self.asked.append(name)
        return None if "Token" in name else b"\xff\xd8jpeg:" + name.encode()

    def test_a_card_is_fetched_once_then_read_from_disk(self):
        c = dc.Cache(self.dir, fetch=self.fetch)
        p = c.get("Black Lotus")
        self.assertTrue(p and os.path.isfile(p))
        self.assertEqual(c.get("black lotus "), p)  # same card, same file
        self.assertEqual(self.asked, ["Black Lotus"])
        with open(p, "rb") as f:
            self.assertTrue(f.read().startswith(b"\xff\xd8"))

    def test_a_name_scryfall_lacks_is_asked_for_once_even_after_a_restart(self):
        self.assertIsNone(dc.Cache(self.dir, fetch=self.fetch).get("Construct Token"))
        self.assertIsNone(dc.Cache(self.dir, fetch=self.fetch).get("Construct Token"))
        self.assertEqual(self.asked, ["Construct Token"])

    def test_offline_serves_the_cache_and_fetches_nothing(self):
        dc.Cache(self.dir, fetch=self.fetch).get("Tundra")
        off = dc.Cache(self.dir, fetch=self.fetch, offline=True)
        self.assertIsNotNone(off.get("Tundra"))
        self.assertIsNone(off.get("Time Walk"))
        self.assertEqual(self.asked, ["Tundra"])

    def test_a_network_error_is_no_image_and_is_not_remembered_as_a_miss(self):
        def broken(name):
            raise OSError("no route")
        c = dc.Cache(self.dir, fetch=broken)
        self.assertIsNone(c.get("Mox Ruby"))
        self.assertNotIn("mox ruby", c.misses)
        c.fetch = self.fetch
        self.assertIsNone(c.get("Mox Ruby"))  # not retried at once: a dead network costs one wait per name
        self.assertEqual(self.asked, [])
        c.failed["mox ruby"] -= dc.RETRY_AFTER_S
        self.assertIsNotNone(c.get("Mox Ruby"))

    def test_a_slow_fetch_holds_up_only_its_own_name(self):
        release = threading.Event()

        def slow(name):
            if name == "Black Lotus":
                release.wait(10)
            return b"\xff\xd8" + name.encode()
        c = dc.Cache(self.dir, fetch=slow)
        t = threading.Thread(target=c.get, args=("Black Lotus",))
        t.start()
        time.sleep(0.3)
        t0 = time.time()
        self.assertIsNone(c.get("Black Lotus"))      # already being fetched: no second request, no wait
        self.assertIsNotNone(c.get("Tundra"))        # another name is not held up by the slow one
        self.assertLess(time.time() - t0, 2)
        release.set()
        t.join(10)
        self.assertIsNotNone(c.get("Black Lotus"))

    def test_a_reply_that_is_not_a_jpeg_is_never_cached(self):
        c = dc.Cache(self.dir, fetch=lambda name: b"<html>proxy login</html>")
        self.assertIsNone(c.get("Tundra"))
        self.assertFalse(os.path.exists(c.path("Tundra")))

    def test_prefetch_names_are_the_objects_the_prompts_showed(self):
        names = dc.names_in_game(os.path.join(HERE, "testdata", "demo", "g33-t5-upkeep.jsonl"))
        for n in ("Bilbo, Thief in the Night", "Tundra", "Emberheart Challenger", "Black Lotus"):
            self.assertIn(n, names)
        self.assertNotIn("Pithing Needle", names)  # only revealed, never a visible object at that decision


if __name__ == "__main__":
    unittest.main()
