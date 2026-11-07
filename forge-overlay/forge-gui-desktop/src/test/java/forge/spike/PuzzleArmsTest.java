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

    /** A repeatable-mode choice on the random arm is the stream it always was: the cap's worth of uniform draws. */
    @Test
    public void repeatModesOnTheRandomArmDrawsTheCapUniformly() {
        Random plain = new Random(42L);
        List<Integer> expected = new ArrayList<>();
        for (int i = 0; i < 5; i++) expected.add(plain.nextInt(4));
        Assert.assertEquals(PuzzleArms.repeatModes(PuzzleArms.forMode("random", 42L), 4, 0, 5), expected);
    }

    /** #269: the search arm drew the cap every time, so "choose up to five" (the Seasons, Hela's Arcanum, Moment of
     *  Reckoning) was
     *  never tried with fewer modes, and it branched over orders of the same modes. It draws a count, then a
     *  non-decreasing multiset. */
    @Test
    public void repeatModesOnTheSearchArmDrawsACountThenAMultiset() {
        // "up to five" (min 0): the count is a choice among 0..5 (bound 6); value 0 is no mode at all, as the engine
        // allows, and value 1 is one mode, here index 2 of 4
        Assert.assertEquals(searchRepeat(Collections.singletonList(new PuzzleArms.Step("Solver", 6, 0)), 4, 0, 5),
                Collections.emptyList());
        Assert.assertEquals(searchRepeat(Arrays.asList(new PuzzleArms.Step("Solver", 6, 1),
                new PuzzleArms.Step("Solver", 4, 2)), 4, 0, 5), Collections.singletonList(2));
        // one to three modes: each next pick is drawn from the modes at or after the last one, so [1, 1, 3] is one
        // branch
        Assert.assertEquals(searchRepeat(Arrays.asList(new PuzzleArms.Step("Solver", 3, 2),
                new PuzzleArms.Step("Solver", 4, 1), new PuzzleArms.Step("Solver", 3, 0),
                new PuzzleArms.Step("Solver", 3, 2)), 4, 1, 3), Arrays.asList(1, 1, 3));
        // exactly N (min == num: 17 of the 24 repeatable cards, 14 of them choose three): no count is drawn, and once
        // the last mode is reached the rest are forced, so [2, 2, 2] is one draw
        Assert.assertEquals(searchRepeat(Collections.singletonList(new PuzzleArms.Step("Solver", 3, 2)), 3, 3, 3),
                Arrays.asList(2, 2, 2));
    }

    /** #415: a pawprint charm's picks total at most its {P} budget on every arm. Season of Loss: modes of 1, 2 and 3
     *  {P}, budget 5. On the search arm the {P}{P}{P} mode leaves 2, and no mode at or after it fits: one draw. */
    @Test
    public void repeatModesKeepsAPawprintCharmWithinItsBudget() {
        int[] cost = {1, 2, 3};
        for (long seed = 1; seed <= 300; seed++) {
            List<Integer> picks = PuzzleArms.repeatModes(PuzzleArms.forMode("random", seed), 3, 0, 5, cost, 5);
            int total = 0;
            for (int i : picks) total += cost[i];
            Assert.assertTrue(total <= 5, "seed " + seed + ": " + picks);
        }
        PuzzleArms.searchLine = new PuzzleArms.Line(Collections.singletonList(new PuzzleArms.Step("Solver", 4, 2)), 5L);
        try {
            Assert.assertEquals(PuzzleArms.repeatModes(PuzzleArms.forMode("search", 1L, "Solver"), 3, 0, 5, cost, 5),
                    Collections.singletonList(2));
            Assert.assertNull(PuzzleArms.searchLine.drift);
            Assert.assertEquals(PuzzleArms.searchLine.taken.size(), 1, "no draw past the line");
        } finally {
            PuzzleArms.searchLine = null;
        }
    }

    /** repeatModes on the search arm over exactly these draws: none drifts, and none is made past them. */
    private static List<Integer> searchRepeat(List<PuzzleArms.Step> steps, int options, int min, int num) {
        PuzzleArms.searchLine = new PuzzleArms.Line(steps, 5L);
        try {
            List<Integer> picks = PuzzleArms.repeatModes(PuzzleArms.forMode("search", 1L, "Solver"), options, min, num);
            Assert.assertNull(PuzzleArms.searchLine.drift, "the draws asked are the ones the line holds");
            Assert.assertEquals(PuzzleArms.searchLine.taken.size(), steps.size(), "no draw past the line");
            return picks;
        } finally {
            PuzzleArms.searchLine = null;
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
