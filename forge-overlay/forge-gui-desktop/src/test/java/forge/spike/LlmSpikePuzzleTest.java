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
    // The [acceptance] predicates: the puzzle's own condition on the FINAL engine state
    // (eval/BENCHMARK-V1.md, "a start state, an acceptance state, one solution"). Conditions
    // live in the .pzl's [acceptance] section, all AND'd. These tests are pure: they exercise
    // the parser and the predicate over a small snapshot of the finished game, never the Game
    // object itself — the snapshot builder is the only engine glue and it is exercised by every
    // engine run below it.
    // ================================================================================

    private static LlmSpikePuzzle.Snapshot.SnapshotBuilder snap() {
        return new LlmSpikePuzzle.Snapshot.SnapshotBuilder().player(0).player(1);
    }

    @Test
    public void acceptance_zoneContainsCountsEveryCopy() {
        LlmSpikePuzzle.Snapshot s = snap()
                .card(0, "Graveyard", "Kozilek, Butcher of Truth", false)
                .card(0, "Graveyard", "Kozilek, Butcher of Truth", false)
                .build();
        Assert.assertTrue(LlmSpikePuzzle.accepted(LlmSpikePuzzle.parseAcceptance(
                java.util.Arrays.asList("zone:0:graveyard:Kozilek, Butcher of Truth:min:2")), s));
        Assert.assertFalse(LlmSpikePuzzle.accepted(LlmSpikePuzzle.parseAcceptance(
                java.util.Arrays.asList("zone:0:graveyard:Kozilek, Butcher of Truth:exact:1")), s));
        Assert.assertTrue(LlmSpikePuzzle.accepted(LlmSpikePuzzle.parseAcceptance(
                java.util.Arrays.asList("zone:0:graveyard:Kozilek, Butcher of Truth:exact:2")), s));
        Assert.assertTrue(LlmSpikePuzzle.accepted(LlmSpikePuzzle.parseAcceptance(
                java.util.Arrays.asList("zone:0:graveyard:Kozilek, Butcher of Truth:max:2")), s));
    }

    @Test
    public void acceptance_zoneAbsentFailsWhenTheCardIsAnywhere() {
        LlmSpikePuzzle.Snapshot s = snap().card(1, "Battlefield", "Solemn Simulacrum", false).build();
        Assert.assertFalse(LlmSpikePuzzle.accepted(LlmSpikePuzzle.parseAcceptance(
                java.util.Arrays.asList("zone:1:battlefield:Solemn Simulacrum:max:0")), s));
        // A different card in the zone does not break an absent condition on another name.
        Assert.assertTrue(LlmSpikePuzzle.accepted(LlmSpikePuzzle.parseAcceptance(
                java.util.Arrays.asList("zone:1:battlefield:Karn Liberated:max:0")), s));
    }

    @Test
    public void acceptance_zoneStarCountsEveryCardInAZone() {
        LlmSpikePuzzle.Snapshot s = snap()
                .card(1, "Battlefield", "Ball Lightning", true)
                .card(1, "Battlefield", "Ball Lightning", true)
                .card(1, "Battlefield", "Plains", false)
                .build();
        Assert.assertFalse(LlmSpikePuzzle.accepted(LlmSpikePuzzle.parseAcceptance(
                java.util.Arrays.asList("zone:1:battlefield:*:exact:0")), s));
        Assert.assertTrue(LlmSpikePuzzle.accepted(LlmSpikePuzzle.parseAcceptance(
                java.util.Arrays.asList("zone:1:battlefield:*:exact:3")), s));
        Assert.assertTrue(LlmSpikePuzzle.accepted(LlmSpikePuzzle.parseAcceptance(
                java.util.Arrays.asList("zone:1:battlefield:*:max:3")), s));
        Assert.assertFalse(LlmSpikePuzzle.accepted(LlmSpikePuzzle.parseAcceptance(
                java.util.Arrays.asList("zone:1:battlefield:*:max:2")), s));
    }

    @Test
    public void acceptance_lifeOperators() {
        LlmSpikePuzzle.Snapshot s = snap().life(0, 3).life(1, 0).build();
        Assert.assertTrue(LlmSpikePuzzle.accepted(LlmSpikePuzzle.parseAcceptance(
                java.util.Arrays.asList("life:0:exact:3")), s));
        Assert.assertTrue(LlmSpikePuzzle.accepted(LlmSpikePuzzle.parseAcceptance(
                java.util.Arrays.asList("life:1:max:0")), s));
        Assert.assertFalse(LlmSpikePuzzle.accepted(LlmSpikePuzzle.parseAcceptance(
                java.util.Arrays.asList("life:1:exact:1")), s));
        Assert.assertTrue(LlmSpikePuzzle.accepted(LlmSpikePuzzle.parseAcceptance(
                java.util.Arrays.asList("life:0:>=:2")), s));
        Assert.assertFalse(LlmSpikePuzzle.accepted(LlmSpikePuzzle.parseAcceptance(
                java.util.Arrays.asList("life:0:<=:2")), s));
    }

    @Test
    public void acceptance_countersOnNamedCards() {
        LlmSpikePuzzle.Snapshot s = snap()
                .card(0, "Battlefield", "Walking Ballista", false)
                .counter(0, "Battlefield", "Walking Ballista", "+1/+1", 2)
                .build();
        Assert.assertTrue(LlmSpikePuzzle.accepted(LlmSpikePuzzle.parseAcceptance(
                java.util.Arrays.asList("counter:0:battlefield:Walking Ballista:+1/+1:exact:2")), s));
        Assert.assertFalse(LlmSpikePuzzle.accepted(LlmSpikePuzzle.parseAcceptance(
                java.util.Arrays.asList("counter:0:battlefield:Walking Ballista:+1/+1:max:1")), s));
        // A named counter on a card that is not there is a fail, not a pass.
        Assert.assertFalse(LlmSpikePuzzle.accepted(LlmSpikePuzzle.parseAcceptance(
                java.util.Arrays.asList("counter:1:battlefield:Walking Ballista:+1/+1:min:1")), s));
    }

    @Test
    public void acceptance_tokensAreCountedSeparatelyFromRealCards() {
        LlmSpikePuzzle.Snapshot s = snap()
                .card(1, "Battlefield", "Zombie", true)
                .card(1, "Battlefield", "Zombie", true)
                .card(0, "Battlefield", "Zombie", false)   // a real creature with the same name
                .build();
        Assert.assertEquals(LlmSpikePuzzle.tokensInZone(s, 1, "Battlefield", "Zombie"), 2);
        Assert.assertEquals(LlmSpikePuzzle.tokensInZone(s, 0, "Battlefield", "Zombie"), 0);
        Assert.assertTrue(LlmSpikePuzzle.accepted(LlmSpikePuzzle.parseAcceptance(
                java.util.Arrays.asList("token:1:battlefield:Zombie:min:2")), s));
        Assert.assertFalse(LlmSpikePuzzle.accepted(LlmSpikePuzzle.parseAcceptance(
                java.util.Arrays.asList("token:0:battlefield:Zombie:min:1")), s));
    }

    @Test
    public void acceptance_lostPredicate() {
        LlmSpikePuzzle.Snapshot s = snap().lost(1).build();
        Assert.assertTrue(LlmSpikePuzzle.accepted(LlmSpikePuzzle.parseAcceptance(
                java.util.Arrays.asList("lost:1")), s));
        Assert.assertFalse(LlmSpikePuzzle.accepted(LlmSpikePuzzle.parseAcceptance(
                java.util.Arrays.asList("lost:0")), s));
    }

    @Test
    public void acceptance_allConditionsMustPassTogether() {
        LlmSpikePuzzle.Snapshot s = snap().life(1, 0)
                .card(0, "Exile", "Solemn Simulacrum", false).build();
        Assert.assertTrue(LlmSpikePuzzle.accepted(LlmSpikePuzzle.parseAcceptance(java.util.Arrays.asList(
                "life:1:exact:0", "zone:0:exile:Solemn Simulacrum:min:1")), s));
        Assert.assertFalse(LlmSpikePuzzle.accepted(LlmSpikePuzzle.parseAcceptance(java.util.Arrays.asList(
                "life:1:exact:0", "zone:0:exile:Solemn Simulacrum:min:1", "zone:1:graveyard:A:min:1")), s));
    }

    @Test
    public void acceptance_parserReadsTheSectionLayoutForgeHandsOver() {
        // FileSection.parseSections keeps every line as it appears, blanks included; a
        // comment/preamble line must not be read as a condition and every accepted form parses.
        List<String> lines = java.util.Arrays.asList(
                "life:1:max:0", "zone:0:graveyard:Birds of Paradise:min:1",
                "counter:1:battlefield:Chasm Skulker:LOYAL:max:4", "token:1:battlefield:Goat:exact:2",
                "lost:1", "zone:1:battlefield:*:exact:0");
        Assert.assertEquals(LlmSpikePuzzle.parseAcceptance(lines).size(), 6);
    }

    @Test(expectedExceptions = RuntimeException.class)
    public void acceptance_parserRejectsGarbageLoudly() {
        LlmSpikePuzzle.parseAcceptance(java.util.Arrays.asList("zone:0:battlefield:N autoimmune thing:five"));
    }

    @Test(expectedExceptions = RuntimeException.class)
    public void acceptance_parserRejectsAnUnknownOperator() {
        LlmSpikePuzzle.parseAcceptance(java.util.Arrays.asList("life:0:at:3"));
    }

    @Test(expectedExceptions = RuntimeException.class)
    public void acceptance_parserRejectsAnUnknownZone() {
        LlmSpikePuzzle.parseAcceptance(java.util.Arrays.asList("zone:0:limbo:Birds of Paradise:exact:1"));
    }

    @Test(expectedExceptions = RuntimeException.class)
    public void acceptance_parserRejectsAnUnknownPlayer() {
        LlmSpikePuzzle.parseAcceptance(java.util.Arrays.asList("life:2:max:0"));
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
}
