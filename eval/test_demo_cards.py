"""demo_cards: a card image is fetched once and then served from disk; a name Scryfall lacks is asked for once; an
offline cache never touches the network. The fetcher is faked: no test reaches the network."""
import os
import shutil
import tempfile
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

    def test_prefetch_names_are_the_objects_the_prompts_showed(self):
        names = dc.names_in_game(os.path.join(HERE, "testdata", "demo", "g33-t5-upkeep.jsonl"))
        for n in ("Bilbo, Thief in the Night", "Tundra", "Emberheart Challenger", "Black Lotus"):
            self.assertIn(n, names)
        self.assertNotIn("Pithing Needle", names)  # only revealed, never a visible object at that decision


if __name__ == "__main__":
    unittest.main()
