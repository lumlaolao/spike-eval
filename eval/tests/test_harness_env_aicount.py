"""harness_env_aicount.py: the grader's engine patch counts every Forge-AI decision method a seat enters.

The patch is generated from the engine's own PlayerControllerAi source, so these tests check the insertion on a
small fixture (a decision method and its overload, a non-decision method, a generic method whose signature spans
lines) and on the pinned engine file when this machine has it. counts() is what the grader compares a submission's
copy against the engine with, and the only count (patch() returns the text alone), so it is pinned equal to the
counters patch() writes: if they drifted apart, an uncounted overload would pass the "counter incomplete" check.
"""
import collections
import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)
import harness_env_aicount as ac
import surface_coverage

FIXTURE = '''package forge.ai;

public class PlayerControllerAi extends PlayerController {
    public PlayerControllerAi(Game game, Player p, LobbyPlayer lp) {
        super(game, p, lp);
    }

    @Override
    public void declareBlockers(Player defender, Combat combat) {
        brains.declareBlockersFor(defender, combat);
    }

    @Override
    public void declareBlockers(Player defender) {
        brains.declareBlockersFor(defender, null);
    }

    public AiController getAi() {
        return brains;
    }

    @Override
    public <T extends GameEntity> List<T> chooseEntitiesForEffect(
            FCollectionView<T> optionList, int min, int max, DelayedReveal delayedReveal, SpellAbility sa, String title,
            Player targetedPlayer, Map<String, Object> params) {
        return null;
    }

    public abstract boolean confirmTrigger(WrappedAbility wrapper);
}
'''
HOOKS = {"declareBlockers", "chooseEntitiesForEffect", "confirmTrigger"}
FORGE = os.path.expanduser(os.path.expandvars(os.environ.get("FORGE_HOME", "/root/forge")))
LEDGER = os.path.join(str(evalpath.REPO), "docs", "harness",
                      "controller-ownership.tsv")


def ledger_file(root):
    path = os.path.join(root, "ledger.tsv")
    with open(path, "w", encoding="utf-8") as f:
        f.write("signature\timpl\tkind\townership\tlegacy_reach\tnotes\n"
                "declareBlockers[x]\tLLM\tstrategic\tmodel\tcore\tn\n"
                "chooseEntitiesForEffect[x]\tLLM\tstrategic\tmodel\tcore\tn\n")
    return path


class PatchTest(unittest.TestCase):
    def test_a_decision_method_counts_on_entry_and_nothing_else_changes(self):
        out = ac.patch(FIXTURE, HOOKS)
        self.assertIsInstance(out, str, "the text only: counts() is the count")
        self.assertEqual(sorted(ac.counts(FIXTURE, HOOKS)), ["chooseEntitiesForEffect", "declareBlockers"])
        lines = out.splitlines()
        i = lines.index("    public void declareBlockers(Player defender, Combat combat) {")
        self.assertEqual(lines[i + 1].strip(), ac.CALL % "declareBlockers")
        self.assertNotIn(ac.CALL % "getAi", out)
        self.assertEqual(out.replace("\n" + " " * 8 + ac.CALL % "declareBlockers", "")
                         .replace("\n" + " " * 8 + ac.CALL % "chooseEntitiesForEffect", ""), FIXTURE)

    def test_a_signature_over_several_lines_counts_after_its_opening_brace(self):
        lines = ac.patch(FIXTURE, HOOKS).splitlines()
        i = next(n for n, l in enumerate(lines) if "Player targetedPlayer, Map<String, Object> params) {" in l)
        self.assertEqual(lines[i + 1].strip(), ac.CALL % "chooseEntitiesForEffect")

    def test_tabs_and_a_signature_split_before_its_name_still_count(self):
        src = FIXTURE.replace("    ", "\t").replace("public void declareBlockers(", "public void\n\t\tdeclareBlockers(")
        out = ac.patch(src, HOOKS)
        self.assertEqual(sorted(ac.counts(src, HOOKS)), ["chooseEntitiesForEffect", "declareBlockers"])
        self.assertNotIn(ac.CALL % "confirmTrigger", out)
        self.assertEqual(out.count("AiCounter.enter("), 3)

    def test_an_abstract_method_has_no_body_to_count_in(self):
        self.assertNotIn(ac.CALL % "confirmTrigger", ac.patch(FIXTURE, HOOKS))

    def test_a_given_source_text_is_patched_instead_of_the_engines(self):
        tmp = tempfile.mkdtemp()
        try:
            dest, counted = ac.write("/no/engine/here", ledger_file(tmp), os.path.join(tmp, "out"), source=FIXTURE)
            self.assertEqual(counted, collections.Counter(declareBlockers=2, chooseEntitiesForEffect=1))
            with open(dest, encoding="utf-8") as f:
                self.assertEqual(f.read().count(ac.CALL % "declareBlockers"), 2)
        finally:
            shutil.rmtree(tmp)

    def test_counts_is_a_counter_of_exactly_the_counters_patch_writes(self):
        found = ac.counts(FIXTURE, HOOKS)
        self.assertIsInstance(found, collections.Counter)
        self.assertEqual(found, collections.Counter(declareBlockers=2, chooseEntitiesForEffect=1))
        out = ac.patch(FIXTURE, HOOKS)
        self.assertEqual(sum(found.values()), out.count("AiCounter.enter("))
        for name, n in found.items():
            self.assertEqual(out.count(ac.CALL % name), n, name)

    def test_the_command_line_says_how_many_hooks_and_how_many_methods(self):
        tmp = tempfile.mkdtemp()
        try:
            forge = os.path.join(tmp, "forge")
            os.makedirs(os.path.join(forge, os.path.dirname(ac.ENGINE_PATH)))
            with open(os.path.join(forge, ac.ENGINE_PATH), "w", encoding="utf-8") as f:
                f.write(FIXTURE)
            for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "pin"]):
                subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=forge, check=True)
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                rc = ac.main(["aicount", forge, ledger_file(tmp), os.path.join(tmp, "out")])
            self.assertEqual(rc, 0)
            self.assertIn(": 2 hooks, 3 methods counted", printed.getvalue())
        finally:
            shutil.rmtree(tmp)

    @unittest.skipUnless(os.path.isdir(os.path.join(FORGE, ".git")), "no engine checkout here")
    def test_the_pinned_engine_file_gets_a_counter_in_every_decision_method_it_defines(self):
        src = subprocess.run(["git", "-C", FORGE, "show", "HEAD:" + ac.ENGINE_PATH], capture_output=True, text=True).stdout
        hooks = surface_coverage.decision_hooks(LEDGER)
        found = ac.counts(src, hooks)
        out, counted = ac.patch(src, hooks), sorted(found)
        self.assertGreater(len(counted), 60)
        self.assertIn("declareAttackers", counted)
        self.assertIn("chooseSpellAbilityToPlay", counted)
        self.assertEqual(out.count("{"), src.count("{"))
        for name in counted:  # overloads (chooseBinary, chooseNumber, ...) each get their own counter
            self.assertIn(ac.CALL % name, out)
        self.assertGreaterEqual(out.count(ac.CALL.split("(")[0]), len(counted))
        self.assertEqual(sum(found.values()), out.count("AiCounter.enter("))


if __name__ == "__main__":
    unittest.main()
