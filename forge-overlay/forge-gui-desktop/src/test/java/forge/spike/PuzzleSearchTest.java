package forge.spike;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Random;

import org.testng.Assert;
import org.testng.annotations.Test;

import forge.spike.PuzzleArms.Step;

/**
 * PuzzleSearch on toy games, and the search arm's draws (eval/BENCHMARK-V1.md, phase 2). What these guard:
 * a "proven" puzzle that an opponent reply actually beats (the Forge AI blocked badly, the proof never looked
 * at the other blocks), a search arm whose subset draws can not reach every subset, and a replay that left
 * its line without saying so.
 */
public class PuzzleSearchTest {

    /** A toy puzzle: the solver picks a (3 options), the opponent b (2), the solver c (2). */
    interface Rule { boolean solved(int a, int b, int c); }

    private static PuzzleSearch.Game toy(Rule rule) {
        return (prefix, seed) -> {
            PuzzleArms.Line line = new PuzzleArms.Line(prefix, seed);
            int a = line.draw("Solver", 3), b = line.draw("Opponent", 2), c = line.draw("Solver", 2);
            return new PuzzleSearch.Rollout(new ArrayList<>(line.taken), rule.solved(a, b, c), line.drift);
        };
    }

    /** The proven main line, then the uniqueness search over it with {@code extra} more rollouts. */
    private static PuzzleSearch.Uniqueness unique(PuzzleSearch.Game g, int extra) {
        PuzzleSearch s = new PuzzleSearch(g, "Solver", 500, 4, 7L);
        PuzzleSearch.Result r = s.prove();
        Assert.assertEquals(r.verdict, PuzzleSearch.Verdict.PROVEN);
        return s.unique(r.line, extra);
    }

    @Test
    public void aPuzzleWithOneSolvingStrategyIsUnique() {
        PuzzleSearch.Uniqueness u = unique(toy((a, b, c) -> a == 2 && c == b), 300);
        Assert.assertEquals(u.verdict, "unique");
        Assert.assertNull(u.second);
        Assert.assertEquals(u.deviations, 3, "a=0, a=1 and the other c");
        Assert.assertEquals(u.exhausted, 3, "every line of every deviation was played");
        Assert.assertTrue(u.replays < 60, "a played-out deviation is not replayed: " + u.replays);
    }

    @Test
    public void aSecondSolvingStrategyIsFoundAndShown() {
        PuzzleSearch.Uniqueness u = unique(toy((a, b, c) -> (a == 2 || a == 0) && c == b), 300);
        Assert.assertEquals(u.verdict, "multiple");
        Assert.assertNotEquals(u.second.get(0).value, 1);
    }

    @Test
    public void aSecondLineThatNeedsTheOpponentToErrIsNotASecondSolution() {
        PuzzleSearch.Uniqueness u = unique(toy((a, b, c) -> (a == 2 && c == b) || (a == 1 && b == 0)), 300);
        Assert.assertEquals(u.verdict, "unique");
        Assert.assertTrue(u.unprovenAlternatives >= 1, "the a=1 line was found and refuted");
    }

    @Test
    public void choicesThatChangeNoSolverActionAreOneSolution() {
        // A fourth solver draw d that the game ignores: lines differing only in d do the same thing.
        PuzzleSearch.Game g = (prefix, seed) -> {
            PuzzleArms.Line line = new PuzzleArms.Line(prefix, seed);
            int a = line.draw("Solver", 3), b = line.draw("Opponent", 2), c = line.draw("Solver", 2);
            line.draw("Solver", 2);
            return new PuzzleSearch.Rollout(new ArrayList<>(line.taken), a == 2 && c == b, line.drift,
                    "a=" + a + " c=" + c);
        };
        Assert.assertEquals(unique(g, 300).verdict, "unique");
    }

    @Test
    public void aLineThatAnswersEveryOpponentReplyIsProven() {
        // a = 2, then c must equal b: a strategy exists, but only one that answers each reply differently.
        PuzzleSearch.Result r = new PuzzleSearch(toy((a, b, c) -> a == 2 && c == b), "Solver", 500, 4, 7L).prove();
        Assert.assertEquals(r.verdict, PuzzleSearch.Verdict.PROVEN);
        Assert.assertEquals(r.line.get(0).value, 2);
        Assert.assertEquals(r.opponentChoicePoints, 1);
        Assert.assertTrue(r.opponentBranches >= 1, "the other opponent reply was examined");
    }

    @Test
    public void aWinThatNeedsTheOpponentToErrIsNotProven() {
        // a = 1 wins only if the opponent picks b = 0. A Forge AI that picked 0 would have "solved" it.
        PuzzleSearch.Result r = new PuzzleSearch(toy((a, b, c) -> a == 1 && b == 0), "Solver", 500, 4, 7L).prove();
        Assert.assertEquals(r.verdict, PuzzleSearch.Verdict.NOT_FOUND);
        Assert.assertNull(r.line);
        // The audit gets the reply that beat it: a = 1, then the opponent's b = 1.
        Assert.assertEquals(PuzzleSearch.lineText(r.refutation), "Solver:3:1 Opponent:2:1");
    }

    @Test
    public void anUnsolvablePuzzleSpendsTheBudgetAndIsNotFound() {
        PuzzleSearch s = new PuzzleSearch(toy((a, b, c) -> false), "Solver", 60, 4, 7L);
        PuzzleSearch.Result r = s.prove();
        Assert.assertEquals(r.verdict, PuzzleSearch.Verdict.NOT_FOUND);
        Assert.assertTrue(s.replays() <= 60);
        // prove-v2-drift PS_DOM0 stopped after 73 of 1500 rollouts: every line was played, and the row should say
        // so ("no line wins", not "none found in the budget").
        Assert.assertTrue(r.exhausted, "all 12 lines were played");
        Assert.assertTrue(s.replays() <= 12 + 1, "and no line twice: " + s.replays());
    }

    @Test
    public void theRootMaySpendTheWholeBudgetOnARareLine() {
        // One solving line in 3 * 2 * 2 * 8 * 8 leaves: a quarter of the budget is not enough to find it.
        PuzzleSearch.Game rare = (prefix, seed) -> {
            PuzzleArms.Line line = new PuzzleArms.Line(prefix, seed);
            int a = line.draw("Solver", 3), b = line.draw("Solver", 8), c = line.draw("Solver", 8);
            return new PuzzleSearch.Rollout(new ArrayList<>(line.taken), a == 2 && b == 5 && c == 1, line.drift);
        };
        PuzzleSearch s = new PuzzleSearch(rare, "Solver", 300, 4, 3L);
        Assert.assertEquals(s.prove().verdict, PuzzleSearch.Verdict.PROVEN);
        Assert.assertTrue(s.replays() > 75, "found after " + s.replays() + " rollouts, past a quarter of 300");
    }

    @Test
    public void aRolloutThatLeavesItsLineIsDrift() {
        PuzzleSearch.Game drifting = (prefix, seed) -> {
            PuzzleArms.Line line = new PuzzleArms.Line(prefix, seed);
            line.draw("Solver", prefix.isEmpty() ? 3 : 4); // the menu changed under the replay
            line.draw("Opponent", 2);
            return new PuzzleSearch.Rollout(new ArrayList<>(line.taken), true, line.drift);
        };
        PuzzleSearch.Result r = new PuzzleSearch(drifting, "Solver", 100, 4, 7L).prove();
        Assert.assertEquals(r.verdict, PuzzleSearch.Verdict.DRIFT);
        Assert.assertNotNull(r.drift);
    }

    @Test
    public void theSearchArmReplaysItsLineAndRecordsWhoChoseFromHowMany() {
        List<Step> prefix = Arrays.asList(new Step("Solver", 7, 3), new Step("Opponent", 2, 1));
        PuzzleArms.searchLine = new PuzzleArms.Line(prefix, 5L);
        try {
            Random solver = PuzzleArms.forMode("search", 1L, "Solver");
            Random opp = PuzzleArms.forMode("search", 2L, "Opponent");
            Assert.assertEquals(PuzzleArms.pick(solver, 7, true), 3);
            Assert.assertTrue(opp.nextBoolean());
            Assert.assertEquals(solver.nextInt(1), 0, "a single option draws nothing");
            long x = solver.nextLong(2L, 5L);                 // past the line: a completion draw
            Assert.assertTrue(x >= 2 && x < 5);
            PuzzleArms.Line l = PuzzleArms.searchLine;
            Assert.assertNull(l.drift);
            Assert.assertEquals(l.taken.size(), 3);
            Assert.assertEquals(l.taken.subList(0, 2), prefix);
            Assert.assertEquals(l.taken.get(2).seat, "Solver");
            Assert.assertEquals(l.taken.get(2).bound, 3);
        } finally {
            PuzzleArms.searchLine = null;
        }
    }

    /** runs/puzzles/prove-v1: PS_ELD1 and PS_DOM0 hit the 10-minute cap and PS_M204 ran out of heap, each a
     *  random completion looping, and each was then reported as drift. A rollout stops at a choice cap. */
    @Test
    public void aRolloutStopsAtTheChoiceCapButNeverInsideItsPrefix() {
        List<Step> prefix = new ArrayList<>();
        for (int i = 0; i < 5; i++) prefix.add(new Step("Solver", 2, 1));
        PuzzleArms.Line line = new PuzzleArms.Line(prefix, 5L, 3);
        for (int i = 0; i < 5; i++) {
            line.draw("Solver", 2);
            Assert.assertFalse(line.stopped(), "stopped inside the prefix at draw " + i);
        }
        line.draw("Opponent", 2);
        Assert.assertTrue(line.stopped());
        Assert.assertNull(line.drift);
        Assert.assertFalse(new PuzzleArms.Line(prefix, 5L).stopped());
    }

    /** Rules pilot RP_017: tapping City of Brass for mana before Angel's Grace was never on the search arm's menu,
     *  so the prover could not find the line. The prover's menu is the model's; the canary arms keep theirs. */
    @Test
    public void theProverChoosesFromTheModelsMenuAndTheCanaryArmsKeepTheirs() {
        Assert.assertTrue(LlmSpikeGui.PlayerControllerLLM.faithfulMenu(false, PuzzleArms.forMode("search", 1L, "Solver")));
        Assert.assertTrue(LlmSpikeGui.PlayerControllerLLM.faithfulMenu(true, null));
        for (String arm : new String[] {"random", "pass", "first"})
            Assert.assertFalse(LlmSpikeGui.PlayerControllerLLM.faithfulMenu(false, PuzzleArms.forMode(arm, 1L, "Solver")), arm);
    }

    /** Rules pilot RP_002 / RP_020: the search arm answered every order ask (trigger order, cards to a zone) in the
     *  given order, so a puzzle decided by trigger order was "proven" only because Forge's default was right. */
    @Test
    public void everyOrderIsReachableByTheSearchArm() {
        java.util.Set<List<Integer>> seen = new java.util.HashSet<>();
        for (int a = 0; a < 3; a++) for (int b = 0; b < 2; b++) {
            PuzzleArms.searchLine = new PuzzleArms.Line(Arrays.asList(new Step("Solver", 3, a), new Step("Solver", 2, b)), 5L);
            try {
                List<Integer> p = PuzzleArms.permutation(PuzzleArms.forMode("search", 1L, "Solver"), 3);
                Assert.assertEquals(new java.util.TreeSet<>(p).size(), 3, "a permutation: " + p);
                Assert.assertNull(PuzzleArms.searchLine.drift);
                Assert.assertEquals(PuzzleArms.searchLine.taken.size(), 2, "n-1 draws");
                seen.add(p);
            } finally {
                PuzzleArms.searchLine = null;
            }
        }
        Assert.assertEquals(seen.size(), 6);
    }

    @Test
    public void theSearchArmFlagsDriftWhenTheOtherSeatOrMenuSizeAsks() {
        PuzzleArms.searchLine = new PuzzleArms.Line(Collections.singletonList(new Step("Solver", 4, 2)), 5L);
        try {
            PuzzleArms.forMode("search", 1L, "Opponent").nextInt(4);
            Assert.assertNotNull(PuzzleArms.searchLine.drift);
        } finally {
            PuzzleArms.searchLine = null;
        }
    }

    @Test
    public void theSearchArmReachesEverySubsetOnceAndHonoursTheLimits() {
        // 4 options, 1..2 picks: C(4,1) + C(4,2) = 10 subsets, each by exactly one line of include draws.
        java.util.Set<List<Integer>> seen = new java.util.HashSet<>();
        int lines = 0;
        for (int mask = 0; mask < 16; mask++) {
            List<Step> prefix = new ArrayList<>();
            for (int i = 0; i < 4; i++) prefix.add(new Step("Solver", 2, (mask >> i) & 1));
            PuzzleArms.searchLine = new PuzzleArms.Line(prefix, 5L);
            try {
                List<Integer> sub = PuzzleArms.subset(PuzzleArms.forMode("search", 1L, "Solver"), 4, 1, 2);
                Assert.assertTrue(sub.size() >= 1 && sub.size() <= 2, String.valueOf(sub));
                if (PuzzleArms.searchLine.drift == null
                        && PuzzleArms.searchLine.taken.size() == prefix.size()) { seen.add(sub); lines++; }
                else seen.add(sub);
            } finally {
                PuzzleArms.searchLine = null;
            }
        }
        Assert.assertEquals(seen.size(), 10);
        Assert.assertNull(PuzzleArms.subset(PuzzleArms.forMode("search", 1L, "Solver"), 4, 3, 2));
    }

    @Test
    public void subsetMakesTheDrawsDecideManyAlwaysMadeForTheOtherArms() {
        for (long seed : new long[] {1L, 42L, 99L}) {
            Random plain = new Random(seed);
            List<Integer> all = new ArrayList<>(Arrays.asList(0, 1, 2, 3, 4));
            Collections.shuffle(all, plain);
            List<Integer> old = new ArrayList<>(all.subList(0, 1 + plain.nextInt(3)));
            Assert.assertEquals(PuzzleArms.subset(PuzzleArms.forMode("random", seed), 5, 1, 3), old, "seed " + seed);
        }
    }
}
