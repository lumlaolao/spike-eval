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

    // ================================================================================
    // The uniqueness search (eval/BENCHMARK-V1.md: "exactly one solution"): a mode beside
    // prove() that, given the proven main line, hunts for a SECOND accepted line whose
    // solver-action sequence differs beyond reordering. Found = the puzzle accepts more than
    // one intended play and must be redesigned; not found within the budget = unique (bounded,
    // never a claim the hunt looked everywhere).
    // ================================================================================

    @Test
    public void uniqueness_aSecondAcceptedLineIsFound() {
        // Good solutions iff a == c (opponent b free): first line a=0,c=0; a different solver
        // choice a=2,c=2 also reaches acceptance → multiple.
        PuzzleSearch.Game twoLines = toy((a, b, c) -> a == c);
        PuzzleSearch.Result r = new PuzzleSearch(twoLines, "Solver", 500, 4, 7L).prove();
        Assert.assertEquals(r.verdict, PuzzleSearch.Verdict.PROVEN);
        PuzzleSearch.Result second = new PuzzleSearch(twoLines, "Solver", 500, 4, 7L).findAlternative(r.line);
        Assert.assertNotNull(second.secondLine, "a second accepted line exists and must be found");
        Assert.assertNotEquals(PuzzleSearch.solverSignature(second.secondLine, "Solver"),
                PuzzleSearch.solverSignature(r.line, "Solver"), "the second line is a different play");
    }

    @Test
    public void uniqueness_aSingleSolutionFindsNoSecondLine() {
        // One accepted solver-action sequence: a=2, c=0 (the opponent's reply is irrelevant).
        PuzzleSearch.Game oneLine = toy((a, b, c) -> a == 2 && c == 0);
        PuzzleSearch.Result r = new PuzzleSearch(oneLine, "Solver", 500, 4, 7L).prove();
        Assert.assertEquals(r.verdict, PuzzleSearch.Verdict.PROVEN);
        PuzzleSearch.Result second = new PuzzleSearch(oneLine, "Solver", 500, 4, 7L).findAlternative(r.line);
        Assert.assertNull(second.secondLine, "no other accepted line exists");
        Assert.assertTrue(second.secondLineRollouts > 0, "the budget was actually spent hunting");
    }

    @Test
    public void uniqueness_theHuntSpendsAtMostItsOwnBudget() {
        PuzzleSearch.Game oneLine = toy((a, b, c) -> a == 2 && c == 0);
        PuzzleSearch.Result r = new PuzzleSearch(oneLine, "Solver", 500, 4, 7L).prove();
        PuzzleSearch s = new PuzzleSearch(oneLine, "Solver", 70, 4, 7L);
        PuzzleSearch.Result second = s.findAlternative(r.line);
        Assert.assertNull(second.secondLine);
        Assert.assertTrue(second.secondLineRollouts <= 70, "spent " + second.secondLineRollouts);
    }

    @Test
    public void uniqueness_reorderingTheSolverChoicesIsTheSameLine() {
        // Two solver-choice sequences that are permutations of each other are one line: the
        // signature sorts them, so an alternative replay that takes the same entries in the other
        // order must NOT count as a second line. In the toy the solver's a (b=3) and c (b=2) are
        // independent of the rules, so a solved line keeps its entries under any order of drawing.
        List<Step> line = java.util.Arrays.asList(
                new Step("Solver", 3, 1), new Step("Opponent", 2, 1), new Step("Solver", 2, 0));
        List<Step> permuted = java.util.Arrays.asList(
                new Step("Solver", 2, 0), new Step("Solver", 3, 1), new Step("Opponent", 2, 1));
        Assert.assertEquals(PuzzleSearch.solverSignature(line, "Solver"),
                PuzzleSearch.solverSignature(permuted, "Solver"));
        // …and a line with a different entry is a different signature.
        List<Step> changed = java.util.Arrays.asList(
                new Step("Solver", 3, 1), new Step("Opponent", 2, 1), new Step("Solver", 2, 1));
        Assert.assertNotEquals(PuzzleSearch.solverSignature(line, "Solver"),
                PuzzleSearch.solverSignature(changed, "Solver"));
    }

    @Test
    public void uniqueness_forcedDrawsDoNotDistinguishLines() {
        // A single-option menu (bound 1) has nothing to reorder or choose: it never separates lines.
        List<Step> with = java.util.Arrays.asList(
                new Step("Solver", 1, 0), new Step("Solver", 3, 1), new Step("Solver", 1, 0));
        List<Step> without = java.util.Collections.singletonList(new Step("Solver", 3, 1));
        Assert.assertEquals(PuzzleSearch.solverSignature(with, "Solver"),
                PuzzleSearch.solverSignature(without, "Solver"));
    }

    @Test
    public void uniqueness_aHuntFindsTheSecondLineEvenWithManySeeds() {
        PuzzleSearch.Game twoLines = toy((a, b, c) -> a == c);
        PuzzleSearch.Result r = new PuzzleSearch(twoLines, "Solver", 500, 4, 11L).prove();
        Assert.assertEquals(r.verdict, PuzzleSearch.Verdict.PROVEN);
        for (long seed : new long[] {3L, 11L, 21L, 101L}) {
            PuzzleSearch.Result second = new PuzzleSearch(twoLines, "Solver", 400, 4, seed).findAlternative(r.line);
            Assert.assertNotNull(second.secondLine, "seed " + seed + " found nothing");
        }
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
        Assert.assertEquals(s.prove().verdict, PuzzleSearch.Verdict.NOT_FOUND);
        Assert.assertTrue(s.replays() <= 60);
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
