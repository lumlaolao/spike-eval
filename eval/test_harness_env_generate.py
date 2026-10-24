"""harness_env_generate.py: hidden instances drawn from the card scripts, reproducible from a secret seed only.

What would make these fail: a template that admits a card its board cannot use (a split card, a multi-target
spell), a dev slice that shares a card with the graded split, a set that changes for the same seed or does not for
another, or the seed itself written into the set.
"""
import json
import os
import re
import shutil
import tempfile
import unittest

import harness_env_generate as hg

SCRIPTS = {
    "w/wind_drake.txt": "Name:Wind Drake\nManaCost:2 U\nTypes:Creature Drake\nPT:2/2\nK:Flying\nOracle:Flying\n",
    "g/giant_spider.txt": "Name:Giant Spider\nManaCost:3 G\nTypes:Creature Spider\nPT:2/4\nK:Reach\nOracle:Reach\n",
    "g/grizzly_bears.txt": "Name:Grizzly Bears\nManaCost:1 G\nTypes:Creature Bear\nPT:2/2\nOracle:\n",
    "l/lightning_bolt.txt": ("Name:Lightning Bolt\nManaCost:R\nTypes:Instant\nA:SP$ DealDamage | ValidTgts$ Any | NumDmg$ 3 | "
                             "SpellDescription$ CARDNAME deals 3 damage to any target.\nOracle:x\n"),
    "f/forked_bolt.txt": ("Name:Forked Bolt\nManaCost:R\nTypes:Sorcery\nA:SP$ DealDamage | ValidTgts$ Any | TargetMin$ 1 | "
                          "TargetMax$ 2 | NumDmg$ 2 | DividedAsYouChoose$ 2\nOracle:x\n"),
    "g/goblin_bombardment.txt": ("Name:Goblin Bombardment\nManaCost:1 R\nTypes:Enchantment\nA:AB$ DealDamage | Cost$ Sac<1/Creature> | "
                                 "ValidTgts$ Any | NumDmg$ 1\nOracle:x\n"),
    "i/izzet_charm.txt": ("Name:Izzet Charm\nManaCost:U R\nTypes:Instant\nA:SP$ Charm | Choices$ DBCounter,DBDamage,DBLoot\n"
                          "SVar:DBDamage:DB$ DealDamage | ValidTgts$ Creature | NumDmg$ 2\nOracle:x\n"),
    "f/fire_ice.txt": ("Name:Fire\nManaCost:1 R\nTypes:Instant\nA:SP$ DealDamage | ValidTgts$ Any | NumDmg$ 2\nALTERNATE\n\n"
                       "Name:Ice\nManaCost:1 U\nTypes:Instant\nA:SP$ Tap | ValidTgts$ Permanent\nOracle:x\n"),
    "a/a-wind_drake.txt": "Name:A-Wind Drake\nManaCost:2 U\nTypes:Creature Drake\nPT:2/3\nK:Flying\nOracle:Flying\n",
}


class GeneratorTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        for rel, text in SCRIPTS.items():
            path = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        self.index = hg.index(self.root)

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_each_template_takes_the_cards_its_board_can_use_and_no_other(self):
        self.assertEqual(self.index["attacker-keyword"], ["Wind Drake"])
        self.assertEqual(self.index["blocker-keyword"], ["Giant Spider", "Wind Drake"])
        self.assertIn("Grizzly Bears", self.index["attacker"])
        self.assertEqual(self.index["single-target-spell"], ["Lightning Bolt"])
        self.assertEqual(self.index["sac-outlet"], ["Goblin Bombardment"])
        self.assertEqual(self.index["charm"], ["Izzet Charm"])
        everything = {c for cards in self.index.values() for c in cards}
        self.assertNotIn("Fire", everything, "a split card's faces are not one card a board can hold")
        self.assertNotIn("A-Wind Drake", everything, "an Alchemy rebalance is not in a constructed pool")
        self.assertNotIn("Forked Bolt", everything, "several targets are a different surface")

    def test_the_same_seed_gives_the_same_set_byte_for_byte_and_another_seed_another(self):
        a = hg.generate(self.index, "s3cret", per_template=1, split="all")
        b = hg.generate(self.index, "s3cret", per_template=1, split="all")
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        big = {t: ["Card %d" % i for i in range(40)] for t in hg.TEMPLATES}
        self.assertNotEqual(hg.generate(big, "one", per_template=3, split="all")["instances"],
                            hg.generate(big, "two", per_template=3, split="all")["instances"])

    def test_the_seed_is_never_written_only_its_hash(self):
        s = hg.generate(self.index, "s3cret-seed-value", per_template=1, split="all")
        self.assertNotIn("s3cret-seed-value", json.dumps(s))
        self.assertEqual(s["seed_sha"], hg.sha("s3cret-seed-value")[:16])

    def test_the_dev_slice_and_the_graded_split_share_no_card(self):
        big = {t: ["Card %d" % i for i in range(200)] for t in hg.TEMPLATES}
        dev = {i["card"] for i in hg.generate(big, "public", per_template=50, split="dev")["instances"]}
        held = {i["card"] for i in hg.generate(big, "secret", per_template=50, split="heldout")["instances"]}
        self.assertTrue(dev and held)
        self.assertEqual(dev & held, set())
        self.assertTrue(all(hg.is_dev(c) for c in dev))

    def test_an_instance_id_is_its_content_and_the_set_hash_covers_every_instance(self):
        s = hg.generate(self.index, "x", per_template=1, split="all")
        i = s["instances"][0]
        self.assertEqual(i["id"], hg.instance_id(i["template"], i["card"]))
        t = json.loads(json.dumps(s))
        t["instances"][0]["card"] = "Something Else"
        self.assertNotEqual(hg.set_sha(t["instances"]), s["set_sha"])

    def test_every_instance_names_its_family_and_hook(self):
        for i in hg.generate(self.index, "x", per_template=1, split="all")["instances"]:
            self.assertEqual((i["family"], i["hook"]), hg.TEMPLATES[i["template"]])

    @unittest.skipUnless(os.path.isdir(hg.CARDSFOLDER), "no engine checkout here")
    def test_the_pinned_card_pool_feeds_every_template(self):
        idx = hg.index(hg.CARDSFOLDER)
        for t in hg.TEMPLATES:
            self.assertGreater(len(idx[t]), 20, t)


class JavaSideTest(unittest.TestCase):
    def test_every_template_names_the_family_and_hook_the_java_builder_uses(self):
        java = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "forge-overlay",
                            "forge-gui-desktop", "src", "test", "java", "forge", "harnessenv", "grader",
                            "GeneratedInstances.java")
        with open(java, encoding="utf-8") as f:
            text = f.read()
        built = {t: (fam, hook) for t, fam, hook in
                 re.findall(r'case "([a-z-]+)":.*?inst\(id, "([\w-]+)", "(\w+)"', text, re.S)}
        self.assertEqual(built, hg.TEMPLATES)


if __name__ == "__main__":
    unittest.main()
