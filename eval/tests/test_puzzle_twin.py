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
import puzzle_format as pf  # noqa: E402
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
            written = tw.install(manifest, forge, others="replace")
            self.assertFalse(stale.exists(), "--replace removes a twin that is not in the manifest")
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

    @staticmethod
    def snapshot(root):
        """Every file (bytes) and every folder under root: what 'the tree as it was' means."""
        root = Path(root)
        return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None) for p in sorted(root.rglob("*"))}

    def test_uninstall_leaves_the_checkout_byte_identical_to_before_the_install(self):
        # Kills: no uninstall -> AttributeError; one that leaves the sidecar, the edition or the empty zz_twins folder
        # leaves the snapshot different.
        with tempfile.TemporaryDirectory() as tmp:
            forge, out, manifest = self.make(tmp)
            before = self.snapshot(forge)
            tw.install(manifest, forge)
            self.assertNotEqual(self.snapshot(forge), before)
            removed, kept = tw.uninstall(forge)
            self.assertEqual(kept, [])
            self.assertEqual(len(removed), len(manifest["cards"]) + 2, "the scripts, the sidecar and the edition")
            self.assertEqual(self.snapshot(forge), before)

    def test_uninstall_with_nothing_installed_succeeds_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            forge, out, manifest = self.make(tmp)
            before = self.snapshot(forge)
            self.assertEqual(tw.uninstall(forge), ([], []))
            self.assertEqual(tw.uninstall(forge), ([], []))
            self.assertEqual(self.snapshot(forge), before)
            tw.install(manifest, forge)
            tw.uninstall(forge)
            self.assertEqual(tw.uninstall(forge), ([], []), "a second uninstall after an install")
            self.assertEqual(self.snapshot(forge), before)
            self.assertEqual(tw.main(["uninstall", "--forge", str(forge)]), 0)

    def test_uninstall_keeps_and_names_every_file_it_did_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            forge, out, manifest = self.make(tmp)
            tw.install(manifest, forge)
            folder = forge / tw.TWINS_DIR
            mine = folder / "mine.txt"
            mine.write_text("Name:Mine\n")                      # a script install never wrote
            glimmer = folder / tw.script_file(next(c["twin"] for c in manifest["cards"]
                                                   if c["original"] == "Glimmer Rite"))
            glimmer.write_text(glimmer.read_text() + "# hand edit\n")   # a script of ours, since changed
            removed, kept = tw.uninstall(forge)
            self.assertEqual(sorted(Path(p).name for p, _ in kept), sorted(["mine.txt", glimmer.name, tw.INSTALLED]))
            self.assertTrue(mine.is_file() and glimmer.is_file() and (folder / tw.INSTALLED).is_file())
            self.assertFalse((forge / tw.EDITION_FILE).exists(), "the edition is ours and goes")
            self.assertTrue(all(w in why for _, why in kept for w in [""]) and all(any(k in why for k in ("did not write", "changed", "records")) for _, why in kept), kept)
            self.assertEqual(tw.main(["uninstall", "--forge", str(forge)]), 1, "left files are not a clean uninstall")
            # an edition file that is not the generated one is never deleted
            (forge / tw.EDITION_FILE).write_text("[metadata]\nName=Somebody else's set\n")
            _, kept = tw.uninstall(forge)
            self.assertIn(tw.EDITION_FILE, " ".join(str(p) for p, _ in kept).replace("\\", "/"))
            self.assertTrue((forge / tw.EDITION_FILE).is_file())

    def test_install_refuses_an_original_that_changed_since_the_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            forge, out, manifest = self.make(tmp)
            (forge / tw.CARDSFOLDER / "g/glimmer_rite.txt").write_text(GLIMMER.replace("Amount$ 2", "Amount$ 3"))
            with self.assertRaisesRegex(tw.TwinRefused, "Glimmer Rite"):
                tw.install(manifest, forge)
            self.assertFalse((forge / tw.TWINS_DIR).exists(), "nothing is written when one card fails")

    def second_manifest(self, manifest, twin=None, original="Ember Wisp"):
        """A second suite's manifest holding one card of the first (renamed when `twin` is given)."""
        card = [dict(c) for c in manifest["cards"] if c["original"] == original]
        if twin:
            card[0]["twin"] = twin
        return dict(manifest, cards=card)

    def test_install_refuses_to_delete_twins_another_manifest_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            forge, out, manifest = self.make(tmp)
            tw.install(manifest, forge)
            before = sorted(p.name for p in (forge / tw.TWINS_DIR).glob("*.txt"))
            self.assertGreater(len(before), 1)
            with self.assertRaisesRegex(tw.TwinRefused, "another manifest"):
                tw.install(self.second_manifest(manifest), forge)
            self.assertEqual(sorted(p.name for p in (forge / tw.TWINS_DIR).glob("*.txt")), before)

    def test_install_keep_adds_a_second_manifest_and_replace_removes_the_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            forge, out, manifest = self.make(tmp)
            only_glimmer = self.second_manifest(manifest, original="Glimmer Rite")
            only_ember = self.second_manifest(manifest)
            tw.install(only_glimmer, forge)
            tw.install(only_ember, forge, others="keep")
            self.assertEqual(len(list((forge / tw.TWINS_DIR).glob("*.txt"))), 2)
            edition = (forge / tw.EDITION_FILE).read_text(encoding="utf-8")
            for c in only_glimmer["cards"] + only_ember["cards"]:
                self.assertIn(" C %s\n" % c["twin"], edition, "the edition lists every installed twin")
            tw.install(only_ember, forge, others="replace")
            self.assertEqual(len(list((forge / tw.TWINS_DIR).glob("*.txt"))), 1)
            self.assertEqual(tw.install(only_ember, forge) and 1, 1, "installing the same manifest again is fine")

    def test_install_refuses_one_card_under_two_twin_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            forge, out, manifest = self.make(tmp)
            tw.install(self.second_manifest(manifest), forge)
            other = self.second_manifest(manifest, twin="Zorvak Bel")
            for mode in ("refuse", "keep"):
                with self.assertRaisesRegex(tw.TwinRefused, "Ember Wisp"):
                    tw.install(other, forge, others=mode)

    def test_the_cli_refuses_with_a_nonzero_exit_and_replace_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            forge, out, manifest = self.make(tmp)
            tw.install(manifest, forge)
            second = Path(tmp) / "second.json"
            second.write_text(json.dumps(self.second_manifest(manifest)), encoding="utf-8")
            args = ["install", "--manifest", str(second), "--forge", str(forge)]
            self.assertEqual(tw.main(args), 1)
            self.assertEqual(tw.main(args + ["--replace"]), 0)

    def test_the_cli_never_writes_into_the_repositorys_eval_folder(self):
        with self.assertRaises(SystemExit) as cm:
            tw.main(["make", "--suite", "eval/suites/puzzles-rules-sample-v1.json", "--ids", "rules-RP_017",
                     "--out", str(evalpath.EVAL / "puzzles" / "twins")])
        self.assertNotEqual(cm.exception.code, 0)
        self.assertFalse((evalpath.EVAL / "puzzles" / "twins").exists())


class TwinSuiteTest(unittest.TestCase):
    """The twin records, their stamp, the check against the installed scripts, and the install.sh step."""

    def build(self, tmp, orig_id="fix-1"):
        tmp = Path(tmp)
        forge = fake_forge(tmp / "forge", FAKE_SCRIPTS)
        repo = tmp / "repo"
        (repo / "p").mkdir(parents=True)
        (repo / "p/fix.pzl").write_text(PZL, encoding="utf-8")
        rec = {"schema": pf.PUZZLE_SCHEMA, "id": orig_id, "split": pf.split_for(orig_id), "status": "active",
               "goal": {"engine_goal": "Win", "turns": 1}, "source": {"kind": "rules"}, "pzl": "p/fix.pzl",
               "pzl_sha256": pf.sha256_file(repo / "p/fix.pzl"), "tier": 2, "baselines": {"random": {"n": 1}}}
        suite = {"suite": "fixture", "version": "1", "engine_sha": "e", "suite_sha256": "abc", "puzzles": [rec]}
        out = repo / "runs/twins/t"
        manifest = tw.make_twins(suite, [orig_id], forge, out, seed=1, repo=repo)
        return forge, repo, out, suite, manifest

    def test_a_twin_record_names_its_original_inherits_its_split_and_stamps_its_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            for orig in (next(i for i in ("o%d" % k for k in range(99)) if pf.split_for(i) == "heldout"), "fix-1"):
                forge, repo, out, suite, manifest = self.build(tmp + "/" + orig, orig)
                (rec,) = tw.twin_records(manifest, suite, out / tw.MANIFEST, repo)
                self.assertEqual((rec["id"], rec["twin_of"], rec["status"]), (orig + "-twin", orig, "candidate"))
                self.assertEqual(rec["split"], pf.split_for(orig), "the original's split, not the twin id's hash")
                self.assertEqual(rec["pzl"], "runs/twins/t/pzl/%s-twin.pzl" % orig)
                self.assertEqual(rec["pzl_sha256"], pf.sha256_file(repo / rec["pzl"]))
                self.assertNotEqual(rec["pzl_sha256"], suite["puzzles"][0]["pzl_sha256"])
                self.assertEqual(rec["original_pzl_sha256"], suite["puzzles"][0]["pzl_sha256"])
                self.assertEqual(pf.validate_puzzle(rec, repo=repo, check_files=False), [], "a real twin is valid")
                self.assertEqual(sorted(rec["twin_cards"]), sorted(c["twin"] for c in manifest["cards"]))
                self.assertEqual(rec["twin_set_sha256"], pf.twin_set_sha(rec["twin_cards"]))
                self.assertNotIn("baselines", rec, "measured on the twin later, never copied from the original")
                self.assertNotIn("reference_line", rec)
                self.assertEqual(pf.validate_puzzle(dict(rec, status="candidate"), repo=repo), [])

    def test_a_twin_record_is_refused_for_a_changed_original_or_a_changed_twin_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            forge, repo, out, suite, manifest = self.build(tmp)
            changed = json.loads(json.dumps(suite))
            changed["puzzles"][0]["pzl_sha256"] = "0" * 64
            with self.assertRaisesRegex(tw.TwinRefused, "not the one the twin was made from"):
                tw.twin_records(manifest, changed, out / tw.MANIFEST, repo)
            (out / manifest["puzzles"][0]["pzl"]).write_text("edited")
            with self.assertRaisesRegex(tw.TwinRefused, "changed since the manifest"):
                tw.twin_records(manifest, suite, out / tw.MANIFEST, repo)
            with self.assertRaisesRegex(tw.TwinRefused, "not in the suite"):
                tw.twin_records(manifest, dict(suite, puzzles=[]), out / tw.MANIFEST, repo)

    def test_a_real_twin_round_trips_and_validates_with_its_files_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            forge, repo, out, suite, manifest = self.build(tmp)
            (rec,) = tw.twin_records(manifest, suite, out / tw.MANIFEST, repo)
            self.assertEqual(tw.verify_record(rec, manifest, repo), [])
            self.assertEqual(tw.find_manifest(rec, repo), json.loads((out / tw.MANIFEST).read_text()))
            self.assertEqual(pf.validate_puzzle(rec, repo=repo), [])
            self.assertIn("Set:", PZL, "the fixture's original has a Set: the generator drops; it still round-trips")

    def test_an_edited_twin_board_is_refused_everywhere_twin_records_are_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            forge, repo, out, suite, manifest = self.build(tmp)
            (rec,) = tw.twin_records(manifest, suite, out / tw.MANIFEST, repo)
            board = repo / rec["pzl"]
            text = board.read_text(encoding="utf-8")
            twin_names = sorted(rec["twin_cards"])
            board.write_text(text.replace("p1life=3", "p1life=4"), encoding="utf-8")   # one value changed
            edited = dict(rec, pzl_sha256=pf.sha256_file(board))                      # hash kept consistent
            for why in (tw.verify_record(edited, manifest, repo), pf.validate_puzzle(edited, repo=repo)):
                self.assertTrue(any("not the original's board renamed" in w for w in why), why)
            board.write_text(text.replace(twin_names[0], twin_names[-1], 1), encoding="utf-8")   # one card changed
            edited = dict(rec, pzl_sha256=pf.sha256_file(board))
            self.assertTrue(tw.verify_record(edited, manifest, repo))
            self.assertTrue(pf.validate_puzzle(edited, repo=repo))
            # the suite command refuses to write a record that does not round-trip
            (out / "pzl/fix-1-twin.pzl").write_text(text.replace("p1life=3", "p1life=4"), encoding="utf-8")
            forged = json.loads(json.dumps(manifest))
            forged["puzzles"][0]["twin_pzl_sha256"] = pf.sha256_file(out / "pzl/fix-1-twin.pzl")
            with self.assertRaisesRegex(tw.TwinRefused, "not the original's board renamed"):
                tw.twin_records(forged, suite, out / tw.MANIFEST, repo)

    def test_a_twin_record_pointed_at_another_original_is_refused(self):
        """The reviewer's forgery: the twin of a held-out board H, split open, twin_of and original_pzl_sha256 those of
        an open puzzle P."""
        heldout = next(i for i in ("o%d" % k for k in range(99)) if pf.split_for(i) == "heldout")
        open_ = next(i for i in ("p%d" % k for k in range(99)) if pf.split_for(i) == "open")
        with tempfile.TemporaryDirectory() as tmp:
            forge, repo, out, suite, manifest = self.build(tmp, heldout)
            (rec,) = tw.twin_records(manifest, suite, out / tw.MANIFEST, repo)
            (repo / "p/other.pzl").write_text(PZL.replace("p1life=3", "p1life=5"), encoding="utf-8")
            forged = dict(rec, id=open_ + "-twin", split="open", twin_of=open_,
                          original_pzl_sha256=pf.sha256_file(repo / "p/other.pzl"))
            self.assertEqual(pf.validate_puzzle(forged, repo=repo, check_files=False), [],
                             "the declaration alone is consistent: only the manifest can refuse it")
            self.assertTrue(tw.verify_record(forged, manifest, repo))
            self.assertTrue(pf.validate_puzzle(forged, repo=repo), "refused where the twin file and manifest are found")

    def test_the_installed_scripts_must_be_the_ones_the_record_stamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            forge, repo, out, suite, manifest = self.build(tmp)
            (rec,) = tw.twin_records(manifest, suite, out / tw.MANIFEST, repo)
            self.assertTrue(tw.installed_problems(rec, forge), "nothing installed yet")
            with self.assertRaises(SystemExit) as cm:
                tw.refuse_uninstalled([rec], forge)
            self.assertIn(rec["id"], str(cm.exception))
            self.assertEqual(tw.installed_problems(suite["puzzles"][0], forge), [], "an original needs no twin script")
            tw.install(manifest, forge)
            self.assertEqual(tw.installed_problems(rec, forge), [])
            tw.refuse_uninstalled([rec, suite["puzzles"][0]], forge)
            name = sorted(rec["twin_cards"])[0]
            script = forge / tw.TWINS_DIR / tw.script_file(name)
            script.write_text(script.read_text() + "SVar:Extra:Mana\n")
            why = tw.installed_problems(rec, forge)
            self.assertTrue(any(name in w and "differs" in w for w in why), why)
            script.unlink()
            self.assertTrue(any("no script" in w for w in tw.installed_problems(rec, forge)))
            tw.install(manifest, forge, others="replace")
            (forge / tw.EDITION_FILE).write_text("[cards]\n")
            self.assertTrue(any("not in" in w for w in tw.installed_problems(rec, forge)))

    def test_the_suite_command_writes_a_candidate_suite_under_runs_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            forge, repo, out, suite, manifest = self.build(tmp)
            sfile = Path(tmp) / "suite.json"
            sfile.write_text(json.dumps(suite))
            with self.assertRaises(SystemExit):
                tw.main(["suite", "--manifest", str(out / tw.MANIFEST), "--suite", str(sfile),
                         "--out", str(Path(tmp) / "elsewhere.json")])
            target = evalpath.REPO / "runs" / ("twin-suite-test-%d.json" % os.getpid())
            self.addCleanup(lambda: target.unlink() if target.exists() else None)
            real = tw.twin_records
            tw.twin_records = lambda m, s, path: real(m, s, path, repo)
            try:
                self.assertEqual(tw.main(["suite", "--manifest", str(out / tw.MANIFEST), "--suite", str(sfile),
                                          "--out", str(target)]), 0)
            finally:
                tw.twin_records = real
            cand = json.loads(target.read_text())
            self.assertEqual((cand["schema"], cand["suite"], cand["frozen"]), (pf.SUITE_SCHEMA, "fixture-twins", False))
            self.assertEqual(cand["puzzles"][0]["twin_of"], "fix-1")

    def test_a_run_on_a_twin_record_stops_before_any_game_when_its_scripts_are_not_installed(self):
        import argparse
        import puzzle_baselines as pb
        with tempfile.TemporaryDirectory() as tmp:
            forge, repo, out, suite, manifest = self.build(tmp)
            (rec,) = tw.twin_records(manifest, suite, out / tw.MANIFEST, repo)
            rec["reference_line"] = {"arm": "ai", "seed": 1, "log_sha256": "L"}
            sfile = Path(tmp) / "twins.json"
            sfile.write_text(json.dumps(pf.candidate_suite("t", "1", "e", [rec])))
            played = []
            real_build, real_verify = pb.Build, pb.verify_puzzle
            pb.Build = lambda *a, **k: argparse.Namespace(forge=forge)
            pb.verify_puzzle = lambda *a, **k: played.append(a)
            try:
                with self.assertRaises(SystemExit) as cm:
                    pb.cmd_verify(argparse.Namespace(suite=str(sfile), build=None, forge=None, allow_stale_build=False,
                                                     out_dir=str(Path(tmp) / "o"), jobs=1))
            finally:
                pb.Build, pb.verify_puzzle = real_build, real_verify
            self.assertIn("not installed", str(cm.exception))
            self.assertEqual(played, [], "no game was started")

    def absolute_suite(self, tmp, repo, rec, name="twins.json"):
        """A suite file whose twin record names its board by absolute path, so the CLI (which reads the repository's
        default root) finds the board and the manifest beside it."""
        rec = dict(rec, pzl=str(repo / rec["pzl"]))
        path = repo / "runs/twins/t" / tw.MANIFEST
        m = json.loads(path.read_text())
        for t in m["puzzles"]:
            t["original_pzl"] = str(repo / t["original_pzl"])
        path.write_text(json.dumps(m))
        sfile = Path(tmp) / name
        sfile.write_text(json.dumps(pf.candidate_suite("t", "1", "e", [rec])))
        return sfile, rec

    def test_twin_cards_that_are_not_the_manifests_scripts_for_the_board_are_refused(self):
        # Kills: delete the `rec.get("twin_cards") != scripts` check in verify_record -> a record that stamps another
        # script (or one more or fewer) than the manifest made verifies, and so reads open in eval_only.
        with tempfile.TemporaryDirectory() as tmp:
            forge, repo, out, suite, manifest = self.build(tmp)
            (rec,) = tw.twin_records(manifest, suite, out / tw.MANIFEST, repo)
            self.assertEqual(tw.verify_record(rec, manifest, repo), [])
            name = sorted(rec["twin_cards"])[0]
            other = dict(rec["twin_cards"])
            variants = {"another script": dict(other, **{name: "0" * 64}),
                        "a card dropped": {k: v for k, v in other.items() if k != name},
                        "a card added": dict(other, **{"Invented Extra": "1" * 64})}
            for label, cards in variants.items():
                forged = dict(rec, twin_cards=cards, twin_set_sha256=pf.twin_set_sha(cards))   # internally consistent
                self.assertEqual(pf.twin_problems(forged), [], label + ": the declaration alone is consistent")
                why = tw.verify_record(forged, manifest, repo)
                self.assertTrue(any("twin_cards are not the manifest's scripts" in w for w in why), (label, why))
                self.assertTrue(pf.validate_puzzle(forged, repo=repo), label + ": validate_puzzle refuses it too")

    def test_the_verify_command_exits_nonzero_on_a_twin_it_cannot_verify(self):
        # Kills: `return 1 if bad else 0` -> `return 0` (an edited board or a missing manifest exits 0); and
        # dropping the missing-manifest branch (a twin with no manifest prints ok or crashes).
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as tmp:
            forge, repo, out, suite, manifest = self.build(tmp)
            (rec,) = tw.twin_records(manifest, suite, out / tw.MANIFEST, repo)
            sfile, rec = self.absolute_suite(tmp, repo, rec)

            def verify():
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    return tw.main(["verify", "--suite", str(sfile)]), buf.getvalue()
            code, text = verify()
            self.assertEqual((code, text), (0, "ok %s\n" % rec["id"]))
            board = Path(rec["pzl"])
            good = board.read_text(encoding="utf-8")
            board.write_text(good.replace("p1life=3", "p1life=4"), encoding="utf-8")
            sfile.write_text(json.dumps(pf.candidate_suite("t", "1", "e", [dict(rec, pzl_sha256=pf.sha256_file(board))])))
            code, text = verify()
            self.assertEqual(code, 1)
            self.assertIn("REFUSED", text)
            board.write_text(good, encoding="utf-8")
            sfile.write_text(json.dumps(pf.candidate_suite("t", "1", "e", [rec])))
            (out / tw.MANIFEST).unlink()
            code, text = verify()
            self.assertEqual(code, 1)
            self.assertIn("no twin manifest at", text)
            sfile.write_text(json.dumps(pf.candidate_suite("t", "1", "e", [])))
            self.assertEqual(verify(), (0, ""), "a suite with no twin has nothing to refuse")

    def test_validate_puzzle_reports_a_malformed_manifest_apart_from_a_missing_one(self):
        # Kills: find_manifest folding a malformed manifest into None -> validate_puzzle returned [] for both.
        with tempfile.TemporaryDirectory() as tmp:
            forge, repo, out, suite, manifest = self.build(tmp)
            (rec,) = tw.twin_records(manifest, suite, out / tw.MANIFEST, repo)
            good = (out / tw.MANIFEST).read_text()
            self.assertEqual(pf.validate_puzzle(rec, repo=repo), [])
            (out / tw.MANIFEST).unlink()
            self.assertEqual(pf.validate_puzzle(rec, repo=repo), [], "missing: runs/ is not committed, not a defect")
            self.assertIn("no twin manifest", tw.manifest_state(rec, repo)[1])
            for label, text in {"not json": "{not json", "a list": "[]",
                                "a card without its keys": json.dumps({"cards": [{"original": "x"}]})}.items():
                (out / tw.MANIFEST).write_text(text)
                why = pf.validate_puzzle(rec, repo=repo)
                self.assertTrue(any("malformed twin manifest" in w for w in why), (label, why))
                self.assertIsNone(tw.find_manifest(rec, repo), label)
            (out / tw.MANIFEST).write_text(good)
            self.assertEqual(pf.validate_puzzle(rec, repo=repo), [])

    def test_a_malformed_manifest_is_refused_with_a_message_not_a_traceback(self):
        # Kills: drop manifest_problems/load_manifest -> a manifest that is a list, has a card without its keys, or
        # is not JSON raises KeyError/AttributeError/JSONDecodeError out of install, suite and verify_record.
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as tmp:
            forge, repo, out, suite, manifest = self.build(tmp)
            (rec,) = tw.twin_records(manifest, suite, out / tw.MANIFEST, repo)
            sfile, arec = self.absolute_suite(tmp, repo, rec)
            good = json.loads((out / tw.MANIFEST).read_text())
            target = evalpath.REPO / "runs" / ("twin-malformed-test-%d.json" % os.getpid())
            self.addCleanup(lambda: target.unlink() if target.exists() else None)

            def with_(**kw):
                return dict(good, **kw)
            bad = {"a list": [],
                   "cards not a list": with_(cards={"x": 1}),
                   "a card that is a string": with_(cards=["x"]),
                   "a card without a twin": with_(cards=[{k: v for k, v in good["cards"][0].items() if k != "twin"}]),
                   "a puzzle without twin_id": with_(puzzles=[{k: v for k, v in good["puzzles"][0].items()
                                                              if k != "twin_id"}]),
                   "puzzle cards not a list": with_(puzzles=[dict(good["puzzles"][0], cards="Glimmer Rite")]),
                   # pr-review of #454: an object in a puzzle's cards hashed into a dict -> TypeError, not a refusal
                   "a puzzle card that is an object": with_(puzzles=[dict(good["puzzles"][0], cards=[{"a": 1}])]),
                   "a hash that is a number": with_(puzzles=[dict(good["puzzles"][0], original_pzl_sha256=7)])}
            for label, m in bad.items():
                self.assertTrue(tw.verify_record(rec, m, repo), label)   # a refusal, not an exception
                with self.assertRaises(tw.TwinRefused, msg=label):
                    tw.install(m, forge)
                with self.assertRaises(tw.TwinRefused, msg=label):
                    tw.twin_records(m, suite, out / tw.MANIFEST, repo)
                (out / tw.MANIFEST).write_text(json.dumps(m))
                self.assertIsNone(tw.find_manifest(rec, repo), label)
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(tw.main(["install", "--manifest", str(out / tw.MANIFEST), "--forge", str(forge)]),
                                     1, label)
                    self.assertEqual(tw.main(["suite", "--manifest", str(out / tw.MANIFEST), "--suite", str(sfile),
                                              "--out", str(target)]), 1, label)
                self.assertIn("malformed twin manifest", err.getvalue())
            (out / tw.MANIFEST).write_text("{not json")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(tw.main(["install", "--manifest", str(out / tw.MANIFEST), "--forge", str(forge)]), 1)
            self.assertIn("cannot read the twin manifest", err.getvalue())
            self.assertIsNone(tw.find_manifest(rec, repo))

    def test_a_probe_of_a_twin_board_stops_before_any_game_when_its_scripts_are_not_installed(self):
        # Kills: delete the refuse_uninstalled call in cmd_probe -> run_arm (the JVM) is reached for a twin board whose
        # scripts are missing, and the probe reads "rejected" about a board the engine cannot load.
        import argparse
        import puzzle_baselines as pb
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            forge, repo, out, suite, manifest = self.build(tmp)
            board = out / manifest["puzzles"][0]["pzl"]
            board.write_text(board.read_text(encoding="utf-8") + "\n[acceptance]\nzone p0 Exile Ember == 1\n")
            argv = ["probe", "--pzl", str(board), "--line", "Solver:2:1", "--out-dir", str(Path(tmp) / "o")]
            row = {"status": "ok", "acceptance": "rejected"}
            with mock.patch.object(pb, "Build", return_value=argparse.Namespace(forge=forge)), \
                    mock.patch.object(pb, "run_arm", return_value=[row]) as run:
                with self.assertRaises(SystemExit) as cm:
                    pb.main(argv)
                self.assertIn("not installed", str(cm.exception))
                run.assert_not_called()
                tw.install(manifest, forge)
                pb.main(argv)
                run.assert_called_once()
                # a bare board that no manifest names is not a twin: nothing to check, the probe runs
                run.reset_mock()
                bare = Path(tmp) / "bare.pzl"
                bare.write_text(board.read_text(encoding="utf-8"), encoding="utf-8")
                pb.main(["probe", "--pzl", str(bare), "--line", "Solver:2:1", "--out-dir", str(Path(tmp) / "o")])
                run.assert_called_once()
                # a manifest beside the board that cannot be read refuses rather than guess
                run.reset_mock()
                (out / tw.MANIFEST).write_text("[]")
                with self.assertRaises(SystemExit) as cm:
                    pb.main(argv)
                self.assertIn("twin manifest", str(cm.exception))
                run.assert_not_called()

    def install_sh(self, tmp, env):
        """install.sh in a copy of its folder with a stub overlay and engine patcher, the real puzzle_twin.py."""
        import shutil
        import subprocess
        here = Path(tmp) / "here"
        (here / "forge-overlay").mkdir(parents=True)
        (here / "forge-overlay/run-llm-spike.sh").write_text("")
        (here / "forge-overlay/run-llm-headless.sh").write_text("")
        (here / "eval/puzzles").mkdir(parents=True)
        (here / "eval/engine_patches.py").write_text("")
        for f in ("puzzle_twin.py", "puzzle_format.py"):
            shutil.copy(str(evalpath.EVAL / "puzzles" / f), str(here / "eval/puzzles" / f))
        shutil.copy(str(evalpath.EVAL / "evalpath.py"), str(here / "eval/evalpath.py"))
        shutil.copy(str(evalpath.REPO / "install.sh"), str(here / "install.sh"))
        forge = fake_forge(Path(tmp) / "forge", FAKE_SCRIPTS)
        (forge / "forge-gui-desktop").mkdir()
        full = {k: v for k, v in os.environ.items() if k not in ("TWIN_MANIFEST", "TWIN_MODE")}
        full.update(FORGE_HOME=str(forge), **env)
        return forge, subprocess.run(["bash", str(here / "install.sh")], capture_output=True, text=True, env=full)

    def test_install_sh_installs_the_twin_scripts_only_when_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            forge, repo, out, suite, manifest = self.build(tmp + "/b")
            forge2, done = self.install_sh(tmp + "/a", {})
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertFalse((forge2 / tw.TWINS_DIR).exists(), "no TWIN_MANIFEST, no twin")
            forge3, done = self.install_sh(tmp + "/c", {"TWIN_MANIFEST": str(out / tw.MANIFEST)})
            self.assertEqual(done.returncode, 0, done.stderr)
            (rec,) = tw.twin_records(manifest, suite, out / tw.MANIFEST, repo)
            self.assertEqual(tw.installed_problems(rec, forge3), [], "the scripts the record stamps are the ones installed")
            self.assertFalse((forge / tw.TWINS_DIR).exists(), "the checkout the manifest was made from is not written")
            forge4, done = self.install_sh(tmp + "/d", {"TWIN_MANIFEST": str(out / tw.MANIFEST), "TWIN_MODE": "sideways"})
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("TWIN_MODE", done.stderr)

    def test_install_sh_stops_when_the_checkout_cannot_take_the_twins(self):
        with tempfile.TemporaryDirectory() as tmp:
            forge, repo, out, suite, manifest = self.build(tmp + "/b")
            bad = json.loads((out / tw.MANIFEST).read_text())
            bad["cards"][0]["original_sha256"] = "0" * 64   # an original that is not the one the manifest saw
            (out / tw.MANIFEST).write_text(json.dumps(bad))
            _, done = self.install_sh(tmp + "/a", {"TWIN_MANIFEST": str(out / tw.MANIFEST)})
            self.assertNotEqual(done.returncode, 0)
            self.assertNotIn("Done.", done.stdout, "a refused install is not reported as done")
            self.assertIn("REFUSED", done.stderr)


if __name__ == "__main__":
    unittest.main()
