"""harness_env_aicount.py: the grader's engine patch counts every Forge-AI decision method a seat enters.

The patch is generated from the engine's own PlayerControllerAi source, so these tests check the insertion on a
small fixture (a decision method, a non-decision method, a generic method whose signature spans lines) and on the
pinned engine file when this machine has it.
"""
import os
import subprocess
import unittest

import harness_env_aicount as ac

FIXTURE = '''package forge.ai;

public class PlayerControllerAi extends PlayerController {
    public PlayerControllerAi(Game game, Player p, LobbyPlayer lp) {
        super(game, p, lp);
    }

    @Override
    public void declareBlockers(Player defender, Combat combat) {
        brains.declareBlockersFor(defender, combat);
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
LEDGER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "harness",
                      "controller-ownership.tsv")


class PatchTest(unittest.TestCase):
    def test_a_decision_method_counts_on_entry_and_nothing_else_changes(self):
        out, counted = ac.patch(FIXTURE, HOOKS)
        self.assertEqual(counted, ["chooseEntitiesForEffect", "declareBlockers"])
        lines = out.splitlines()
        i = lines.index("    public void declareBlockers(Player defender, Combat combat) {")
        self.assertEqual(lines[i + 1].strip(), ac.CALL % "declareBlockers")
        self.assertNotIn(ac.CALL % "getAi", out)
        self.assertEqual(out.replace("\n" + " " * 8 + ac.CALL % "declareBlockers", "")
                         .replace("\n" + " " * 8 + ac.CALL % "chooseEntitiesForEffect", ""), FIXTURE)

    def test_a_signature_over_several_lines_counts_after_its_opening_brace(self):
        lines = ac.patch(FIXTURE, HOOKS)[0].splitlines()
        i = next(n for n, l in enumerate(lines) if "Player targetedPlayer, Map<String, Object> params) {" in l)
        self.assertEqual(lines[i + 1].strip(), ac.CALL % "chooseEntitiesForEffect")

    def test_an_abstract_method_has_no_body_to_count_in(self):
        self.assertNotIn(ac.CALL % "confirmTrigger", ac.patch(FIXTURE, HOOKS)[0])

    @unittest.skipUnless(os.path.isdir(os.path.join(FORGE, ".git")), "no engine checkout here")
    def test_the_pinned_engine_file_gets_a_counter_in_every_decision_method_it_defines(self):
        src = subprocess.run(["git", "-C", FORGE, "show", "HEAD:" + ac.ENGINE_PATH], capture_output=True, text=True).stdout
        out, counted = ac.patch(src, ac.decision_hooks(LEDGER))
        self.assertGreater(len(counted), 60)
        self.assertIn("declareAttackers", counted)
        self.assertIn("chooseSpellAbilityToPlay", counted)
        self.assertEqual(out.count("{"), src.count("{"))
        for name in counted:  # overloads (chooseBinary, chooseNumber, ...) each get their own counter
            self.assertIn(ac.CALL % name, out)
        self.assertGreaterEqual(out.count(ac.CALL.split("(")[0]), len(counted))


if __name__ == "__main__":
    unittest.main()
