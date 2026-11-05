"""puzzle_twin.py: a twin puzzle names no original card anywhere, a twin script is the original with only its
self-references renamed, the refusals fire, the names are deterministic, and install writes only where it is told.

The real-puzzle tests read three committed boards (a rules-pilot puzzle and two generated ones) read-only. The script
fixtures are invented cards (docs/STANDARDS.md section 5), shaped like Forge scripts; the one test that reads Forge's
own Rite of Flame skips when no checkout is readable."""
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)
import puzzle_twin as tw  # noqa: E402

REAL = {
    "rules-RP_017": "eval/puzzles/rules-pilot/RP_017.pzl",
    "hard-ping-0134ececa133": "eval/puzzles/hard/gen/ping_0134ececa133.pzl",
    "hard-drain-0026d851c85f": "eval/puzzles/hard/gen/drain_0026d851c85f.pzl",
}

# An invented card shaped like Forge's Rite of Flame: its count names itself in an SVar, its DeckHints and its text.
GLIMMER = "\n".join([
    "Name:Glimmer Rite",
    "ManaCost:R",
    "Types:Sorcery",
    "A:SP$ Mana | Produced$ R | Amount$ 2 | AILogic$ ManaRitual | SubAbility$ SubMana | SpellDescription$ Add {R}{R},"
    " then add {R} for each card named Glimmer Rite in each graveyard.",
    "SVar:SubMana:DB$ Mana | Produced$ R | Amount$ X",
    "SVar:X:Count$ValidGraveyard Card.namedGlimmer Rite",
    "DeckHints:Name$Glimmer Rite",
    "Oracle:Add {R}{R}, then add {R} for each card named Glimmer Rite in each graveyard.",
    ""])
EMBER = "\n".join([
    "Name:Ember Wisp",
    "ManaCost:1 R",
    "Types:Creature Elemental",
    "PT:1/1",
    "A:AB$ DealDamage | Cost$ T | ValidTgts$ Player | NumDmg$ 1 | SpellDescription$ CARDNAME deals 1 damage to"
    " target player.",
    "Oracle:{T}: Ember Wisp deals 1 damage to target player.",
    ""])
PZL = "\n".join([
    "[metadata]",
    "Name:Fixture - Glimmer Rite twice",
    "Goal:Win",
    "Turns:1",
    "Difficulty:Hard",
    "Description:Cast Glimmer Rite twice, then let Ember Wisp's ping finish it. Ember Wisps are not enough alone.",
    "Rule:fixture | Glimmer Rite counts itself",
    "[state]",
    "turn=1",
    "activeplayer=p0",
    "activephase=MAIN1",
    "p0life=20",
    "p0hand=Glimmer Rite;Glimmer Rite|Set:XYZ",
    "p0battlefield=Ember Wisp|Tapped;Mountain",
    "p0library=Mountain;Mountain",
    "p1life=3",
    "p1library=Plains;Plains",
    "[solution]",
    "# search: {\"cards\": [\"Glimmer Rite\", \"Ember Wisp\"]}",
    "cast Glimmer Rite",
    "activate Ember Wisp ... damage",
    "[acceptance]",
    "count p0 Graveyard \"Glimmer Rite\" = 2",
    "life p1 <= 0",
    ""])


def whole(name):
    return re.compile(r"(?<![A-Za-z0-9])%s(?:s|es)?(?![A-Za-z0-9])" % re.escape(name), re.IGNORECASE)


def fake_forge(root, scripts, java=""):
    """A Forge-shaped folder: cardsfolder scripts at their Forge paths, and one engine source file."""
    root = Path(root)
    for rel, text in scripts.items():
        p = root / tw.CARDSFOLDER / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    src = root / "forge-game/src/main/java/forge/Fake.java"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(java, encoding="utf-8")
    (root / tw.EDITIONS).mkdir(parents=True, exist_ok=True)
    return root


FAKE_SCRIPTS = {"g/glimmer_rite.txt": GLIMMER, "e/ember_wisp.txt": EMBER,
                "m/mountain.txt": "Name:Mountain\nTypes:Basic Land Mountain\nOracle:({T}: Add {R}.)\n",
                "p/plains.txt": "Name:Plains\nTypes:Basic Land Plains\nOracle:({T}: Add {W}.)\n",
                "d/dark_vault.txt": "Name:Dark Vault\nTypes:Land\nOracle:Dark Vault enters tapped.\n"}


class RealPuzzleTest(unittest.TestCase):
    """Every name-bearing field of three committed boards is renamed; nothing else moves."""

    def twins(self, rel):
        text = (evalpath.REPO / rel).read_text(encoding="utf-8")
        names = tw.board_names(text) - tw.KEEP_NAMES
        mapping = tw.assign_twins(names, seed=1, real_names=set())
        return text, names, mapping, tw.twin_pzl(text, mapping)

    def test_no_original_name_survives_in_a_twin_puzzle(self):
        for pid, rel in REAL.items():
            text, names, mapping, twin = self.twins(rel)
            self.assertGreaterEqual(len(names), 2, pid)
            for name in names:
                self.assertTrue(whole(name).search(text), "%s: %s was on the board" % (pid, name))
                self.assertIsNone(whole(name).search(twin), "%s: %s survived its twin" % (pid, name))

    def test_the_twin_board_holds_the_twins_and_the_kept_basics_only(self):
        for pid, rel in REAL.items():
            text, names, mapping, twin = self.twins(rel)
            kept = tw.board_names(text) & tw.KEEP_NAMES
            self.assertEqual(tw.board_names(twin), set(mapping.values()) | kept, pid)

    def test_mapping_the_twin_back_gives_the_original_byte_for_byte(self):
        for pid, rel in REAL.items():
            text, names, mapping, twin = self.twins(rel)
            back = tw.twin_pzl(twin, {v: k for k, v in mapping.items()})
            self.assertEqual(back, text, pid)

    def test_the_rules_puzzles_description_and_rule_are_renamed(self):
        text, names, mapping, twin = self.twins(REAL["rules-RP_017"])
        self.assertIn("opponent's %s is attacking" % mapping["Grizzly Bears"], twin)
        self.assertIn("%s's damage is a triggered ability" % mapping["City of Brass"], twin)
        self.assertIn("p0library=Plains;Plains;Plains", twin, "basic lands keep their names")


class ScriptTest(unittest.TestCase):

    def test_a_self_reference_by_name_is_rewritten_everywhere_the_card_names_itself(self):
        out, edits = tw.twin_script(GLIMMER, "Vorel Tasken")
        self.assertNotIn("Glimmer Rite", out)
        self.assertIn("Name:Vorel Tasken\n", out)
        self.assertIn("SVar:X:Count$ValidGraveyard Card.namedVorel Tasken\n", out)
        self.assertIn("DeckHints:Name$Vorel Tasken\n", out)
        self.assertIn("Oracle:Add {R}{R}, then add {R} for each card named Vorel Tasken in each graveyard.", out)
        self.assertIn("AILogic$ ManaRitual", out, "the AI's hints are copied unchanged")
        self.assertEqual([e["line"] for e in edits], [1, 4, 6, 7, 8])
        self.assertEqual(out.replace("Vorel Tasken", "Glimmer Rite"), GLIMMER, "nothing else moved")

    def test_forges_rite_of_flame_names_itself_and_its_twin_does_not(self):
        forge = tw.forge_home()
        if forge is None:
            self.skipTest("no readable Forge checkout (FORGE_HOME, ~/code/forge, /root/forge)")
        index = tw.real_card_index(forge)
        self.assertIn("Rite of Flame", index)
        text = (forge / tw.CARDSFOLDER / index["Rite of Flame"]).read_text(encoding="utf-8")
        self.assertIn("Card.namedRite of Flame", text)
        out, edits = tw.twin_script(text, "Vorel Tasken")
        self.assertIn("Card.namedVorel Tasken", out)
        self.assertIsNone(whole("Rite of Flame").search(out))

    def test_a_script_that_names_another_card_is_refused(self):
        lage = EMBER.replace("Oracle:", "Oracle:When it dies, create a token named Marit Lage. ")
        with self.assertRaisesRegex(tw.TwinRefused, "Marit Lage"):
            tw.twin_script(lage, "Vorel Tasken")
        other = EMBER.replace("SVar", "").replace("PT:1/1", "PT:1/1\nSVar:X:Count$Valid Card.namedGlimmer Rite")
        with self.assertRaisesRegex(tw.TwinRefused, "Glimmer Rite"):
            tw.twin_script(other, "Vorel Tasken")

    def test_a_script_that_mentions_another_board_card_by_name_is_refused(self):
        mention = EMBER.replace("Oracle:", "Oracle:Glimmer Rite costs {1} less. ")
        tw.twin_script(mention, "Vorel Tasken")  # unknown to it: fine
        with self.assertRaisesRegex(tw.TwinRefused, "Glimmer Rite"):
            tw.twin_script(mention, "Vorel Tasken", others={"Glimmer Rite"})

    def test_a_name_left_outside_the_text_fields_is_refused(self):
        flash = "Name:Flash\nTypes:Instant\nK:Flash\nOracle:You may put a creature card from your hand.\n"
        with self.assertRaisesRegex(tw.TwinRefused, "K:Flash"):
            tw.twin_script(flash, "Vorel Tasken")

    def test_a_card_with_two_faces_is_refused(self):
        with self.assertRaisesRegex(tw.TwinRefused, "face"):
            tw.twin_script(EMBER + "ALTERNATE\n\nName:Ember Wisp Risen\nOracle:x\n", "Vorel Tasken")


class RefusalTest(unittest.TestCase):

    def test_a_board_with_a_card_the_engine_names_is_refused(self):
        text = (evalpath.REPO / "eval/puzzles/rules-pilot/RP_001.pzl").read_text(encoding="utf-8")
        with self.assertRaisesRegex(tw.TwinRefused, "Dark Depths"):
            tw.check_board(text, engine_names={"Dark Depths"},
                           known_names={"Dark Depths", "Ray of Revelation", "Blood Moon"})

    def test_a_board_with_a_card_forge_does_not_have_is_refused(self):
        with self.assertRaisesRegex(tw.TwinRefused, "Ember Wisp"):
            tw.check_board(PZL, engine_names=set(), known_names={"Glimmer Rite"})

    def test_the_basic_lands_are_kept_though_the_engine_names_them(self):
        self.assertEqual(tw.check_board(PZL, engine_names={"Mountain", "Plains"},
                                        known_names={"Glimmer Rite", "Ember Wisp"}),
                         {"Glimmer Rite", "Ember Wisp"})

    def test_a_twin_name_that_is_a_real_card_is_refused(self):
        with self.assertRaisesRegex(tw.TwinRefused, "real card"):
            tw.twin_pzl(PZL, {"Glimmer Rite": "Lightning Bolt", "Ember Wisp": "Vorel Tasken"},
                        real_names={"lightning bolt"})

    def test_a_twin_name_that_is_a_prefix_of_another_label_is_refused(self):
        with self.assertRaisesRegex(tw.TwinRefused, "prefix"):
            tw.twin_pzl(PZL, {"Glimmer Rite": "Mount", "Ember Wisp": "Vorel Tasken"})
        with self.assertRaisesRegex(tw.TwinRefused, "prefix"):
            tw.twin_pzl(PZL, {"Glimmer Rite": "Vorel", "Ember Wisp": "Vorel Tasken"})

    def test_two_cards_on_one_twin_name_are_refused(self):
        with self.assertRaisesRegex(tw.TwinRefused, "two cards"):
            tw.twin_pzl(PZL, {"Glimmer Rite": "Vorel Tasken", "Ember Wisp": "Vorel Tasken"})

    def test_an_original_name_the_rewrite_cannot_reach_is_refused(self):
        lower = PZL.replace("Description:", "Description:glimmer rite is lowercase here. ")
        with self.assertRaisesRegex(tw.TwinRefused, "Glimmer Rite"):
            tw.twin_pzl(lower, {"Glimmer Rite": "Vorel Tasken", "Ember Wisp": "Kesh Ombra"})

    def test_a_lower_case_card_in_the_labels_the_seat_never_sees_is_renamed(self):
        labels = (PZL.replace("Name:Fixture - ", "Name:glimmer rite_3f2a ")
                  .replace("Rule:fixture", "Rule:ember wisp-gen"))
        twin = tw.twin_pzl(labels, {"Glimmer Rite": "Vorel Tasken", "Ember Wisp": "Kesh Ombra"})
        self.assertIn("Name:Vorel Tasken_3f2a ", twin)
        self.assertIn("Rule:Kesh Ombra-gen", twin)

    def test_every_name_bearing_field_is_rewritten_and_the_set_is_dropped(self):
        twin = tw.twin_pzl(PZL, {"Glimmer Rite": "Vorel Tasken", "Ember Wisp": "Kesh Ombra"})
        self.assertIn("p0hand=Vorel Tasken;Vorel Tasken\n", twin, "a twin has no printing in the original's set")
        self.assertIn("Kesh Ombra's ping", twin)
        self.assertIn("Kesh Ombras are not enough", twin)
        self.assertIn("count p0 Graveyard \"Vorel Tasken\" = 2", twin)
        self.assertIn("activate Kesh Ombra ... damage", twin)
        self.assertIn("# search: {\"cards\": [\"Vorel Tasken\", \"Kesh Ombra\"]}", twin)
        self.assertIn("Name:Fixture - Vorel Tasken twice", twin)
        self.assertIn("Goal:Win\n", twin)


class NameTest(unittest.TestCase):

    def test_names_are_deterministic_per_seed_and_card(self):
        self.assertEqual(tw.twin_name("Lightning Bolt", 7), tw.twin_name("Lightning Bolt", 7))
        self.assertNotEqual(tw.twin_name("Lightning Bolt", 7), tw.twin_name("Lightning Bolt", 8))
        a = tw.assign_twins({"Shock", "Lava Spike", "Goblin Guide"}, seed=3, real_names=set())
        b = tw.assign_twins(["Goblin Guide", "Shock", "Lava Spike"], seed=3, real_names=set())
        self.assertEqual(a, b, "the order the names arrive in does not matter")

    def test_a_name_keeps_the_word_count_and_is_pronounceable(self):
        for original in ("Shock", "Lava Spike", "Angel's Grace", "Baylen, the Haymaker", "City of Brass"):
            twin = tw.twin_name(original, 1)
            self.assertEqual(len(twin.split()), min(3, len(original.split())), twin)
            for word in twin.split():
                self.assertRegex(word, r"^[A-Z][a-z]{2,}$")
                self.assertRegex(word.lower(), r"[aeiou]")

    def test_a_real_card_name_or_a_prefix_clash_is_redrawn(self):
        first = tw.twin_name("Shock", 5)
        got = tw.assign_twins({"Shock"}, seed=5, real_names={first.lower()})
        self.assertNotEqual(got["Shock"], first)
        clash = tw.assign_twins({"Shock"}, seed=5, real_names=set(), avoid={first + " Extra"})
        self.assertNotEqual(clash["Shock"], first)


class EngineNamesTest(unittest.TestCase):

    def test_a_card_name_in_an_engine_string_literal_is_found_and_a_comment_is_not(self):
        java = ('class Fake {\n  boolean f(Card c) { return c.getName().equals("Dark Vault"); }\n'
                '  // "Ember Wisp" in a comment decides nothing\n  String s = "not a card";\n}\n')
        with tempfile.TemporaryDirectory() as tmp:
            forge = fake_forge(tmp, FAKE_SCRIPTS, java)
            self.assertEqual(tw.engine_names(forge), {"Dark Vault"})

    def test_the_index_leaves_out_the_twins_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            forge = fake_forge(tmp, dict(FAKE_SCRIPTS, **{"zz_twins/vorel.txt": "Name:Vorel Tasken\n"}))
            index = tw.real_card_index(forge)
            self.assertEqual(index["Glimmer Rite"], "g/glimmer_rite.txt")
            self.assertNotIn("Vorel Tasken", index)


class MakeAndInstallTest(unittest.TestCase):
    """make writes twin boards and a manifest; install regenerates the scripts from the checkout's originals."""

    def make(self, tmp, java=""):
        forge = fake_forge(Path(tmp) / "forge", FAKE_SCRIPTS, java)
        repo = Path(tmp) / "repo"
        (repo / "p").mkdir(parents=True)
        (repo / "p/fix.pzl").write_text(PZL, encoding="utf-8")
        (repo / "p/vault.pzl").write_text(PZL.replace("Mountain;Mountain\n", "Mountain;Dark Vault\n"),
                                          encoding="utf-8")
        suite = {"suite": "fixture", "suite_sha256": "abc", "puzzles": [
            {"id": "fix-1", "pzl": "p/fix.pzl"}, {"id": "vault-1", "pzl": "p/vault.pzl"}]}
        out = Path(tmp) / "out"
        manifest = tw.make_twins(suite, ["fix-1", "vault-1"], forge, out, seed=1, repo=repo)
        return forge, out, manifest

    def test_make_writes_the_twin_boards_and_a_manifest_and_refuses_what_it_cannot_twin(self):
        with tempfile.TemporaryDirectory() as tmp:
            forge, out, manifest = self.make(tmp, java='String v = "Dark Vault";\n')
            self.assertEqual([p["id"] for p in manifest["puzzles"]], ["fix-1"])
            self.assertEqual([r["id"] for r in manifest["refused"]], ["vault-1"])
            self.assertIn("Dark Vault", manifest["refused"][0]["reason"])
            self.assertEqual(sorted(c["original"] for c in manifest["cards"]), ["Ember Wisp", "Glimmer Rite"])
            twin = (out / manifest["puzzles"][0]["pzl"]).read_text(encoding="utf-8")
            for c in manifest["cards"]:
                self.assertIsNone(whole(c["original"]).search(twin))
                self.assertIn(c["twin"], twin)
            on_disk = json.loads((out / tw.MANIFEST).read_text(encoding="utf-8"))
            self.assertEqual(on_disk, manifest)
            glimmer = [c for c in manifest["cards"] if c["original"] == "Glimmer Rite"][0]
            self.assertEqual(glimmer["script"], "g/glimmer_rite.txt")
            self.assertEqual(len(glimmer["edits"]), 5, "the manifest holds the edited lines, not the script")

    def test_install_writes_the_scripts_and_the_edition_into_the_given_checkout_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            forge, out, manifest = self.make(tmp)
            before = {p for p in Path(tmp).rglob("*")}
            stale = forge / tw.TWINS_DIR / "old_twin.txt"
            stale.parent.mkdir(parents=True)
            stale.write_text("Name:Old Twin\n")
            written = tw.install(manifest, forge)
            self.assertFalse(stale.exists(), "a twin no longer in the manifest is removed")
            for path in written:
                self.assertTrue(Path(path).resolve().is_relative_to(forge.resolve())
                                if hasattr(Path, "is_relative_to") else str(path).startswith(str(forge)))
            new = {p for p in Path(tmp).rglob("*")} - before - {stale.parent}
            self.assertTrue(all(str(p).startswith(str(forge / "forge-gui/res")) for p in new), new)
            for c in manifest["cards"]:
                script = (forge / tw.TWINS_DIR / tw.script_file(c["twin"])).read_text(encoding="utf-8")
                self.assertIn("Name:%s\n" % c["twin"], script)
                self.assertIsNone(whole(c["original"]).search(script))
            edition = (forge / tw.EDITION_FILE).read_text(encoding="utf-8")
            self.assertIn("Type=Custom_Set", edition)
            self.assertIn("Code=%s" % tw.EDITION_CODE, edition)
            for c in manifest["cards"]:
                self.assertRegex(edition, r"\n\d+ C %s\n" % re.escape(c["twin"]))

    def test_install_refuses_an_original_that_changed_since_the_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            forge, out, manifest = self.make(tmp)
            (forge / tw.CARDSFOLDER / "g/glimmer_rite.txt").write_text(GLIMMER.replace("Amount$ 2", "Amount$ 3"))
            with self.assertRaisesRegex(tw.TwinRefused, "Glimmer Rite"):
                tw.install(manifest, forge)
            self.assertFalse((forge / tw.TWINS_DIR).exists(), "nothing is written when one card fails")

    def test_the_cli_never_writes_into_the_repositorys_eval_folder(self):
        with self.assertRaises(SystemExit) as cm:
            tw.main(["make", "--suite", "eval/suites/puzzles-rules-sample-v1.json", "--ids", "rules-RP_017",
                     "--out", str(evalpath.EVAL / "puzzles" / "twins")])
        self.assertNotEqual(cm.exception.code, 0)
        self.assertFalse((evalpath.EVAL / "puzzles" / "twins").exists())


if __name__ == "__main__":
    unittest.main()
