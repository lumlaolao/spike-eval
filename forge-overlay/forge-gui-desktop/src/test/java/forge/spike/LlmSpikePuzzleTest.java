package forge.spike;

import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;

/**
 * Pure-logic tests for the PZL axis's two SCOREABILITY gates. Neither needs a game, a JVM-wide
 * Forge model, or a card database — they are string/int predicates over a parsed .pzl.
 *
 * Both gates exist because the failure they prevent is invisible: the puzzle loads, the goal card
 * is installed, the game runs to completion and reports a confident solved/failed that is
 * answering the wrong question. A wrong answer that looks exactly like a right one is the only
 * kind worth a test here.
 */
public class LlmSpikePuzzleTest {

    private static Map<String, List<String>> sections(String[] metadata, String[] state) {
        Map<String, List<String>> m = new HashMap<>();
        m.put("metadata", Arrays.asList(metadata));
        m.put("state", Arrays.asList(state));
        return m;
    }

    /** A rules puzzle (an [acceptance] section) is told its author's description and turn limit only, never
     *  Forge's Goal/Name/Difficulty header (benchmark-v1 RP_443: "Goal: Survive", "Bolster picks the least
     *  toughness"). */
    @Test
    public void aRulesPuzzleIsToldItsDescriptionNotForgesGoalLine() {
        Map<String, List<String>> s = sections(
                new String[] {"Name:Rules pilot 443 - Bolster picks the least toughness", "Goal:Survive", "Turns:1",
                        "Difficulty:Hard", "Description:You control Hill Giant.\\nReduce the opponent to 0 life this turn."},
                new String[] {"turn=1"});
        s.put("acceptance", Arrays.asList("life p1 <= 0"));
        String brief = LlmSpikePuzzle.seatBrief(s, "Rules pilot 443 - Bolster picks the least toughness\nDifficulty: Hard"
                + "\n\nGoal: Survive\nTurns Limit: 1\n\nYou control Hill Giant.\nReduce the opponent to 0 life this turn.");
        Assert.assertTrue(brief.contains("Reduce the opponent to 0 life this turn."), brief);
        Assert.assertTrue(brief.contains("You control Hill Giant.\nReduce"), "the author's line breaks: " + brief);
        Assert.assertTrue(brief.contains("Turns Limit: 1"), brief);
        for (String hidden : new String[] {"Goal:", "Survive", "Difficulty", "Hard", "Bolster", "least toughness"})
            Assert.assertFalse(brief.contains(hidden), hidden + " reached the seat: " + brief);
    }

    /** Forge's HumanControl warning is the one line of its header a rules puzzle keeps: the solver then decides for
     *  the opponent too, and must be told (pr-review of PR #273). */
    @Test
    public void aHumanControlledRulesPuzzleKeepsForgesWarning() {
        Map<String, List<String>> s = sections(
                new String[] {"Name:Rules pilot 9 - secret", "Goal:Survive", "Turns:1", "HumanControl:True",
                        "Description:Win this turn."},
                new String[] {"turn=1"});
        s.put("acceptance", Arrays.asList("life p1 <= 0"));
        String warning = "\nWARNING: This puzzle is human-controlled (it does not have an active AI player). This means"
                + " you will be making all decisions for your opponent. Make sure your solution works for all possible"
                + " opponent's decisions.\n\n";
        String brief = LlmSpikePuzzle.seatBrief(s, warning + "Rules pilot 9 - secret\nDifficulty: (Unspecified)\n\n"
                + "Goal: Survive\nTurns Limit: 1\n\nWin this turn.");
        Assert.assertTrue(brief.startsWith(warning), brief);
        Assert.assertTrue(brief.endsWith("Turns Limit: 1\n\nWin this turn."), brief);
        Assert.assertFalse(brief.contains("secret"), brief);
    }

    /** A Forge puzzle has no acceptance state: its goal line is the goal Forge enforces, so the header stays. */
    @Test
    public void aForgePuzzleKeepsForgesGoalDescription() {
        String forge = "Possibility Storm 1\nDifficulty: Hard\n\nGoal: Win\nTurns Limit: 1\n\nWin this turn.";
        Assert.assertEquals(LlmSpikePuzzle.seatBrief(sections(new String[] {"Goal:Win", "Turns:1"},
                new String[] {"turn=1"}), forge), forge);
    }

    // ================================================================================
    // goalIsBounded: can the enforcement trigger's TurnCount ever equal the turn counter?
    // Forge compares with STRICT equality (Trigger.java:281-286) and addGoalEnforcement never
    // offsets its limit by the .pzl's own starting `turn=`, so `Turns:1` + `turn=2` waits
    // forever for a turn that already passed and the solver gets unlimited turns.
    // ================================================================================

    /** A result row's pzl_sha256 is the sha256 of the file's bytes, as the frozen suite records it
     *  (puzzle_format.py sha256_file): it hashed the text Forge's reader returns, so puzzle_score.py matched no
     *  Luna result to its puzzle (/tmp pilot, 2026-10-16). */
    @Test
    public void aResultHashesThePuzzleFileItsBytes() throws Exception {
        java.nio.file.Path f = java.nio.file.Files.createTempFile("pzl", ".pzl");
        byte[] bytes = "[metadata]\r\nName:x\r\nGoal:Win\r\n".getBytes(java.nio.charset.StandardCharsets.UTF_8);
        java.nio.file.Files.write(f, bytes);
        byte[] d = java.security.MessageDigest.getInstance("SHA-256").digest(bytes);
        StringBuilder want = new StringBuilder();
        for (byte b : d) want.append(String.format("%02x", b));
        Assert.assertEquals(LlmSpikePuzzle.fileSha256(f.toString()), want.toString());
        Assert.assertNotEquals(LlmSpikePuzzle.sha256(forge.util.FileUtil.readFileToString(f.toString())), want.toString(),
                "the text Forge's reader returns is not the file's bytes");
    }

    @Test
    public void goalBounded_ordinaryPuzzleStartsAtTurnOne() {
        Assert.assertTrue(LlmSpikePuzzle.goalIsBounded(sections(
                new String[] {"Goal:Win", "Turns:1"},
                new String[] {"turn=1", "activephase=MAIN1", "humanlife=20"})));
    }

    @Test
    public void goalBounded_missingTurnKeyDefaultsToTurnOne() {
        // Most .pzl files omit `turn=` entirely; GameState defaults it to 1.
        Assert.assertTrue(LlmSpikePuzzle.goalIsBounded(sections(
                new String[] {"Goal:Win", "Turns:1"},
                new String[] {"activephase=MAIN1", "humanlife=20"})));
    }

    @Test
    public void goalBounded_startingPastTheLimitIsUnbounded() {
        // The real shape of PS_STX2: Turns:1 with turn=2. It ran to turn 8 under the Forge-AI
        // arm and was scored a "solve" before this gate existed.
        Assert.assertFalse(LlmSpikePuzzle.goalIsBounded(sections(
                new String[] {"Goal:Win", "Turns:1"},
                new String[] {"turn=2", "activephase=MAIN1"})));
    }

    @Test
    public void goalBounded_startingExactlyAtTheLimitStillFires() {
        // turn == limit: the trigger's equality check is satisfied on this very turn's cleanup.
        Assert.assertTrue(LlmSpikePuzzle.goalIsBounded(sections(
                new String[] {"Goal:Win", "Turns:2"},
                new String[] {"turn=2", "activephase=MAIN1"})));
    }

    @Test
    public void goalBounded_cleanupStartGetsTheTurnCorrection() {
        // addGoalEnforcement adds +1 to the limit when the applied phase is already CLEANUP
        // (Puzzle.java:132-135), which is exactly enough to reach a turn-2 start.
        Assert.assertTrue(LlmSpikePuzzle.goalIsBounded(sections(
                new String[] {"Goal:Win", "Turns:1"},
                new String[] {"turn=2", "activephase=CLEANUP"})));
        // ...but not a turn-3 start.
        Assert.assertFalse(LlmSpikePuzzle.goalIsBounded(sections(
                new String[] {"Goal:Win", "Turns:1"},
                new String[] {"turn=3", "activephase=CLEANUP"})));
    }

    @Test
    public void goalBounded_multiTurnPuzzleFromTurnOne() {
        Assert.assertTrue(LlmSpikePuzzle.goalIsBounded(sections(
                new String[] {"Goal:Win", "Turns:3"},
                new String[] {"turn=1"})));
    }

    // ================================================================================
    // requireEnforcedGoal: refuse goals Forge's switch would silently downgrade to
    // "win the game by turn N" via its default: branch (Puzzle.java:208-209).
    // ================================================================================

    @Test
    public void enforcedGoal_acceptsEverySuiteGoal() {
        // The four goal strings present in pzl-v1, exactly as written in the .pzl metadata.
        for (String g : new String[] {"Win", "Survive", "Play the Specified Permanent",
                                      "Destroy Specified Creatures"}) {
            LlmSpikePuzzle.requireEnforcedGoal(g); // must not throw
        }
    }

    @Test
    public void enforcedGoal_isCaseAndWhitespaceInsensitive() {
        // Puzzle.addGoalEnforcement lowercases before switching, so we must match that or we
        // would reject a goal the engine handles perfectly well.
        LlmSpikePuzzle.requireEnforcedGoal("  WIN  ");
        LlmSpikePuzzle.requireEnforcedGoal("survive");
    }

    @Test(expectedExceptions = RuntimeException.class)
    public void enforcedGoal_rejectsUnknownGoalRatherThanMisScoringIt() {
        // A plausible future goal with no case in the switch. Forge would run it as
        // "win by turn N" and report a confident verdict about the wrong objective.
        LlmSpikePuzzle.requireEnforcedGoal("Mill the Opponent");
    }

    @Test(expectedExceptions = RuntimeException.class)
    public void enforcedGoal_rejectsEmptyGoal() {
        LlmSpikePuzzle.requireEnforcedGoal("");
    }

    // ================================================================================
    // requireOverridableSolver: a stock LobbyPlayerAi builds a PlayerControllerAi we cannot
    // override, so it can never report itself as the goal-bearing seat and the goal card would
    // have nowhere to go. Fail at startup with the fix named, not with a mid-game NPE.
    // ================================================================================

    @Test(expectedExceptions = RuntimeException.class)
    public void solverArm_plainAiIsRejected() {
        LlmSpikePuzzle.requireOverridableSolver("ai");
    }

    @Test
    public void solverArm_ourControllersAreAccepted() {
        for (String arm : new String[] {"llm", "random", "floor", "ai-log"}) {
            LlmSpikePuzzle.requireOverridableSolver(arm); // must not throw
        }
    }

    // ================================================================================
    // sectionValue: .pzl bodies mix `key=value` (state) and `Key:Value` (metadata).
    // ================================================================================

    @Test
    public void sectionValue_readsBothSeparatorsAndIgnoresCase() {
        List<String> meta = Arrays.asList("Goal:Win", "Turns:2");
        List<String> state = Arrays.asList("turn=3", "activephase=CLEANUP");
        Assert.assertEquals(LlmSpikePuzzle.sectionValue(meta, "turns", "?"), "2");
        Assert.assertEquals(LlmSpikePuzzle.sectionValue(state, "TURN", "?"), "3");
        Assert.assertEquals(LlmSpikePuzzle.sectionValue(state, "absent", "fallback"), "fallback");
        Assert.assertEquals(LlmSpikePuzzle.sectionValue(null, "turn", "fallback"), "fallback");
    }

    @Test
    public void sectionValue_valueContainingSeparatorSurvivesIntact() {
        // Descriptions routinely contain colons; only the FIRST separator splits.
        List<String> meta = Arrays.asList("Description:Win this turn: attack with everything");
        Assert.assertEquals(LlmSpikePuzzle.sectionValue(meta, "Description", ""),
                "Win this turn: attack with everything");
    }

    /** Rules pilot RP_108: a combat entry holds one line per attacker; a block after a "didn't block" line counts. */
    @Test
    public void aBlockAfterADidntBlockLineIsASolverAction() {
        java.util.List<String> acts = LlmSpikePuzzle.solverActions(java.util.Arrays.asList(
                "COMBAT: Solver didn't block Goblin (4).\nSolver assigned Wall (2) to block Ogre (5).",
                "COMBAT: Opponent assigned Ogre (5) and Goblin (4) to attack Solver."), "Solver");
        org.testng.Assert.assertEquals(acts, java.util.Collections.singletonList("COMBAT: Solver assigned Wall (2) to block Ogre (5)."));
    }
}
