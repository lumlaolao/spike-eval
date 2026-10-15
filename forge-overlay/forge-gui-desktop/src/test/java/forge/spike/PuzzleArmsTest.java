package forge.spike;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Random;

import org.testng.Assert;
import org.testng.annotations.Test;

/**
 * The zero-model puzzle arms (PuzzleArms) and the puzzle runner's pure helpers. What these guard:
 * a random-arm stream that silently changed (every pinned random canary and every stored reference
 * line would move), a replayed line that "verifies" after the menu changed under it, and baselines
 * whose PASS policy acts or whose first-option policy declines.
 */
public class PuzzleArmsTest {

    /** The draws the random arm's decision sites make, in one representative order. */
    private static List<Object> exercise(Random r) {
        List<Object> out = new ArrayList<>();
        out.add(PuzzleArms.pick(r, 7, true));          // play menu, PASS last
        out.add(PuzzleArms.pick(r, 3, false));         // decideOne without a decline
        List<Integer> items = new ArrayList<>(Arrays.asList(0, 1, 2, 3, 4));
        PuzzleArms.shuffle(items, r);                  // decideMany
        out.add(new ArrayList<>(items));
        out.add(PuzzleArms.subsetSize(r, 0, 5));
        out.add(r.nextBoolean());                      // blocker assignment
        out.add(r.nextInt(4));
        out.add(r.nextLong(2L, 9L));                   // X value
        return out;
    }

    @Test
    public void randomArmStreamIsIdenticalToPlainRandom() {
        // The random arm used `new java.util.Random(seed)` and drew nextInt/shuffle/nextBoolean/nextLong
        // from it directly. The recording arm must produce the same values in the same order.
        for (long seed : new long[] {1L, 42L, -7L, 123456789L}) {
            Assert.assertEquals(exercise(PuzzleArms.forMode("random", seed)), exercise(new Random(seed)),
                    "seed " + seed);
        }
    }

    @Test
    public void scriptReplaysTheTapeExactly() {
        PuzzleArms.ArmRandom rec = (PuzzleArms.ArmRandom) PuzzleArms.forMode("random", 99L);
        List<Object> original = exercise(rec);
        PuzzleArms.scriptTape = rec.tapeString();
        try {
            PuzzleArms.Script s = (PuzzleArms.Script) PuzzleArms.forMode("script", 12345L); // seed irrelevant
            Assert.assertEquals(exercise(s), original);
            Assert.assertNull(s.drift);
            Assert.assertFalse(s.exhausted);
            Assert.assertEquals(s.unconsumed(), 0);
            Assert.assertEquals(s.tapeString(), rec.tapeString());
        } finally {
            PuzzleArms.scriptTape = "";
        }
    }

    @Test
    public void scriptFlagsDriftWhenTheMenuChanged() {
        // The tape says the play menu had 7 options; the replayed game offers 6. That is a different
        // position or a changed harness, and the line must not verify.
        PuzzleArms.scriptTape = "i7:2 i3:1";
        try {
            PuzzleArms.Script s = (PuzzleArms.Script) PuzzleArms.forMode("script", 1L);
            PuzzleArms.pick(s, 6, true);
            Assert.assertNotNull(s.drift);
            Assert.assertTrue(s.drift.contains("i7:2"), s.drift);
        } finally {
            PuzzleArms.scriptTape = "";
        }
    }

    @Test
    public void scriptFlagsExhaustionAndThenPasses() {
        PuzzleArms.scriptTape = "i4:2";
        try {
            PuzzleArms.Script s = (PuzzleArms.Script) PuzzleArms.forMode("script", 1L);
            Assert.assertEquals(PuzzleArms.pick(s, 4, true), 2);
            Assert.assertFalse(s.exhausted);
            // Off the end of the tape: the PASS policy answers, and the run is flagged.
            Assert.assertEquals(PuzzleArms.pick(s, 5, true), 4);
            Assert.assertTrue(s.exhausted);
        } finally {
            PuzzleArms.scriptTape = "";
        }
    }

    @Test
    public void scriptRejectsAValueOutsideTheBound() {
        PuzzleArms.scriptTape = "i3:5";
        try {
            PuzzleArms.Script s = (PuzzleArms.Script) PuzzleArms.forMode("script", 1L);
            int v = s.nextInt(3);
            Assert.assertTrue(v >= 0 && v < 3);
            Assert.assertNotNull(s.drift);
        } finally {
            PuzzleArms.scriptTape = "";
        }
    }

    @Test
    public void passArmTakesTheDoNothingOption() {
        Random r = PuzzleArms.forMode("pass", 5L);
        Assert.assertEquals(PuzzleArms.pick(r, 7, true), 6, "PASS is the last play option");
        Assert.assertEquals(PuzzleArms.pick(r, 3, false), 0, "no decline offered: first option");
        Assert.assertEquals(PuzzleArms.subsetSize(r, 0, 4), 0, "fewest picks");
        Assert.assertEquals(PuzzleArms.subsetSize(r, 2, 4), 2);
        Assert.assertTrue(r.nextBoolean(), "true = this blocker does not block");
        Assert.assertEquals(r.nextLong(3L, 9L), 3L, "smallest X");
        List<Integer> items = new ArrayList<>(Arrays.asList(0, 1, 2));
        PuzzleArms.shuffle(items, r);
        Assert.assertEquals(items, Arrays.asList(0, 1, 2), "menu order kept");
    }

    @Test
    public void firstArmTakesTheFirstOption() {
        Random r = PuzzleArms.forMode("first", 5L);
        Assert.assertEquals(PuzzleArms.pick(r, 7, true), 0, "first play option, not PASS");
        Assert.assertEquals(PuzzleArms.pick(r, 1, true), 0, "only PASS offered");
        Assert.assertEquals(PuzzleArms.subsetSize(r, 0, 4), 1, "one pick when one is allowed");
        Assert.assertEquals(PuzzleArms.subsetSize(r, 0, 0), 0);
        Assert.assertEquals(PuzzleArms.subsetSize(r, 2, 4), 2);
        List<Integer> items = new ArrayList<>(Arrays.asList(0, 1, 2));
        PuzzleArms.shuffle(items, r);
        Assert.assertEquals(items.get(0), Integer.valueOf(0));
    }

    @Test
    public void onlyTheArmsGetADrawSource() {
        Assert.assertNull(PuzzleArms.forMode("llm", 1L));
        Assert.assertNull(PuzzleArms.forMode("floor", 1L));
        Assert.assertNull(PuzzleArms.forMode(null, 1L));
        for (String m : new String[] {"random", "script", "pass", "first"}) {
            Assert.assertTrue(PuzzleArms.isArm(m), m);
            Assert.assertNotNull(PuzzleArms.forMode(m, 1L), m);
        }
        Assert.assertFalse(PuzzleArms.isArm("llm"));
        Assert.assertFalse(PuzzleArms.isArm("Random"));
    }

    @Test
    public void plainRandomHelpersMakeTheOldDraws() {
        // pick/subsetSize/shuffle on a plain Random are the exact expressions they replaced.
        Random a = new Random(3L), b = new Random(3L);
        Assert.assertEquals(PuzzleArms.pick(a, 9, true), b.nextInt(9));
        Assert.assertEquals(PuzzleArms.subsetSize(a, 1, 4), 1 + b.nextInt(4));
        Assert.assertEquals(PuzzleArms.subsetSize(a, 2, 2), 2); // no draw when hi == lo
        Assert.assertEquals(a.nextInt(), b.nextInt());
        List<Integer> x = new ArrayList<>(Arrays.asList(0, 1, 2, 3)), y = new ArrayList<>(x);
        PuzzleArms.shuffle(x, a);
        Collections.shuffle(y, b);
        Assert.assertEquals(x, y);
    }

    // ---------------- LlmSpikePuzzle helpers ----------------

    @Test
    public void seedsParseRangesAndLists() {
        Assert.assertNull(LlmSpikePuzzle.parseSeeds(null));
        Assert.assertNull(LlmSpikePuzzle.parseSeeds("  "));
        Assert.assertEquals(LlmSpikePuzzle.parseSeeds("1..3"), Arrays.asList(1L, 2L, 3L));
        Assert.assertEquals(LlmSpikePuzzle.parseSeeds("5, 9,1..2"), Arrays.asList(5L, 9L, 1L, 2L));
    }

    @Test(expectedExceptions = IllegalArgumentException.class)
    public void seedsRejectABackwardsRange() {
        LlmSpikePuzzle.parseSeeds("9..3");
    }

    @Test
    public void solverActionsAreTheSolversOwnPlays() {
        List<String> log = Arrays.asList(
                "TURN: Turn 1 (Solver)",
                "MANA: Mountain (9) - {T}: Add {R}.",
                "STACK_ADD: Solver cast Lightning Strike targeting [Opponent]",
                "STACK_ADD: Opponent cast Counterspell targeting [Lightning Strike]",
                "LAND: Solver played Mountain (4)",
                "COMBAT: Solver assigned Thor (6) to attack Opponent.",
                "COMBAT: Opponent didn't block Thor (6).",
                "STACK_ADD: Solver activated Mjolnir",
                "STACK_ADD: Solverine cast Shock");
        Assert.assertEquals(LlmSpikePuzzle.solverActions(log, "Solver"), Arrays.asList(
                "STACK_ADD: Solver cast Lightning Strike targeting [Opponent]",
                "LAND: Solver played Mountain (4)",
                "COMBAT: Solver assigned Thor (6) to attack Opponent.",
                "STACK_ADD: Solver activated Mjolnir"));
    }

    @Test
    public void logFingerprintIgnoresOnlyTheWallClockLine() {
        List<String> a = Arrays.asList("TURN: Turn 1 (Solver)", "GAME_OUTCOME: Match Duration 0m : 01s", "GAME_OUTCOME: Turn 1");
        List<String> b = Arrays.asList("TURN: Turn 1 (Solver)", "GAME_OUTCOME: Match Duration 0m : 07s", "GAME_OUTCOME: Turn 1");
        List<String> c = Arrays.asList("TURN: Turn 1 (Solver)", "GAME_OUTCOME: Match Duration 0m : 01s", "GAME_OUTCOME: Turn 2");
        Assert.assertEquals(LlmSpikePuzzle.logFingerprint(a), LlmSpikePuzzle.logFingerprint(b));
        Assert.assertNotEquals(LlmSpikePuzzle.logFingerprint(a), LlmSpikePuzzle.logFingerprint(c));
    }

    @Test
    public void solverArmRejectsAnUnknownArm() {
        for (String arm : new String[] {"llm", "random", "floor", "ai-log", "pass", "first", "script"})
            LlmSpikePuzzle.requireSolverArm(arm);
        for (String bad : new String[] {"ai", "rnadom", "PASS", ""}) {
            try {
                LlmSpikePuzzle.requireSolverArm(bad);
                Assert.fail("accepted " + bad);
            } catch (RuntimeException expected) { /* refused */ }
        }
    }

    @Test
    public void listsSerializeAsJsonArrays() {
        java.util.Map<String, Object> m = new java.util.LinkedHashMap<>();
        m.put("actions", Arrays.asList("a \"b\"", "c"));
        m.put("n", 2);
        Assert.assertEquals(LlmSpikePuzzle.toJson(m), "{\"actions\":[\"a \\\"b\\\"\",\"c\"],\"n\":2}");
    }
}
