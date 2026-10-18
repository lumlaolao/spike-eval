package forge.spike;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;

/** The acceptance state of a rules puzzle (eval/BENCHMARK-V1.md, "The shape every puzzle takes"): conditions on
 *  the final engine state, all of which must hold. Evaluated here against a fake state; the runner reads Forge's. */
public class PuzzleAcceptanceTest {

    static PuzzleAcceptance.CardFacts card(String name, boolean token, String counter, int n) {
        Map<String, Integer> c = new HashMap<>();
        if (counter != null) c.put(counter.toUpperCase(), n);
        return new PuzzleAcceptance.CardFacts() {
            public String name() { return name; }
            public boolean token() { return token; }
            public int counters(String type) { return c.getOrDefault(type.toUpperCase(), 0); }
            public String counterSummary() { return new java.util.TreeMap<>(c).toString(); }
        };
    }

    /** Solver at 7, opponent at 0 with Tarmogoyf in the graveyard; solver has two Goblin tokens and a creature
     *  with three +1/+1 counters. */
    static PuzzleAcceptance.View board() {
        Map<String, List<PuzzleAcceptance.CardFacts>> zones = new HashMap<>();
        zones.put("0:Battlefield", Arrays.asList(card("Goblin", true, null, 0), card("Goblin", true, null, 0),
                card("Hardened Scales", false, null, 0), card("Walking Ballista", false, "P1P1", 3)));
        zones.put("1:Graveyard", Collections.singletonList(card("Tarmogoyf", false, null, 0)));
        return new PuzzleAcceptance.View() {
            public int life(int p) { return p == 0 ? 7 : 0; }
            public List<PuzzleAcceptance.CardFacts> cards(int p, String zone) {
                return zones.getOrDefault(p + ":" + zone, new ArrayList<>());
            }
            public String winner() { return "p0"; }
        };
    }

    @Test
    public void everyConditionHoldingAccepts() {
        PuzzleAcceptance a = PuzzleAcceptance.parse(Arrays.asList(
                "life p0 = 7", "life p1 <= 0", "count p1 Graveyard \"Tarmogoyf\" = 1",
                "tokens p0 Battlefield \"Goblin\" = 2", "count p0 Battlefield * = 4",
                "counters p0 Battlefield \"Walking Ballista\" P1P1 >= 3", "winner p0", "# a comment", ""));
        PuzzleAcceptance.Verdict v = a.evaluate(board());
        Assert.assertTrue(v.accepted, String.valueOf(v.failed));
        Assert.assertEquals(a.size(), 7);
    }

    @Test
    public void oneFailingConditionRejectsAndIsNamedWithWhatWasThere() {
        PuzzleAcceptance a = PuzzleAcceptance.parse(Arrays.asList(
                "life p0 = 7", "count p1 Graveyard \"Tarmogoyf\" = 0", "tokens p0 Battlefield * = 3"));
        PuzzleAcceptance.Verdict v = a.evaluate(board());
        Assert.assertFalse(v.accepted);
        Assert.assertEquals(v.failed, Arrays.asList(
                "count p1 Graveyard \"Tarmogoyf\" = 0 (was 1)", "tokens p0 Battlefield * = 3 (was 2)"));
    }

    @Test
    public void aNonTokenCountIgnoresNothingAndANameMatchesExactly() {
        PuzzleAcceptance a = PuzzleAcceptance.parse(Arrays.asList(
                "count p0 Battlefield \"Goblin\" = 2", "count p0 Battlefield \"goblin\" = 2", "winner p1"));
        PuzzleAcceptance.Verdict v = a.evaluate(board());
        Assert.assertEquals(v.failed, Arrays.asList("count p0 Battlefield \"goblin\" = 2 (was 0)",
                "winner p1 (was p0)"));
    }

    /** Rules pilot batch 2: two solutions differing only in a mode, a clone pick or a face-up choice have the same
     *  game-log actions, so uniqueness compares the final state too: the same board in another order is one state,
     *  a counter more is another. */
    @Test
    public void theStateDigestSeesCountersButNotOrder() {
        PuzzleAcceptance.View a = board();
        Map<String, List<PuzzleAcceptance.CardFacts>> zones = new HashMap<>();
        List<PuzzleAcceptance.CardFacts> bf = new ArrayList<>(a.cards(0, "Battlefield"));
        Collections.reverse(bf);
        zones.put("0:Battlefield", bf);
        zones.put("1:Graveyard", a.cards(1, "Graveyard"));
        PuzzleAcceptance.View reordered = view(zones, 7, 0);
        Assert.assertEquals(PuzzleAcceptance.digest(reordered), PuzzleAcceptance.digest(a));
        List<PuzzleAcceptance.CardFacts> more = new ArrayList<>(bf);
        more.set(0, card("Walking Ballista", false, "P1P1", 4));
        zones.put("0:Battlefield", more);
        Assert.assertNotEquals(PuzzleAcceptance.digest(view(zones, 7, 0)), PuzzleAcceptance.digest(a));
        Assert.assertNotEquals(PuzzleAcceptance.digest(view(new HashMap<>(), 7, 0)),
                PuzzleAcceptance.digest(view(new HashMap<>(), 6, 0)));
    }

    static PuzzleAcceptance.View view(Map<String, List<PuzzleAcceptance.CardFacts>> zones, int life0, int life1) {
        return new PuzzleAcceptance.View() {
            public int life(int p) { return p == 0 ? life0 : life1; }
            public List<PuzzleAcceptance.CardFacts> cards(int p, String zone) {
                return zones.getOrDefault(p + ":" + zone, new ArrayList<>());
            }
            public String winner() { return "p0"; }
        };
    }

    @Test
    public void aLineItCannotReadIsRefusedNotIgnored() {
        for (String bad : new String[] {"life p2 = 1", "life p0 == 1", "count p0 Graveyard Tarmogoyf = 1",
                "counters p0 Battlefield \"X\" = 1", "hand p0 = 1", "count p0 Nowhere * = 1", "life p0 = x",
                "winner someone"}) {
            try {
                PuzzleAcceptance.parse(Collections.singletonList(bad));
                Assert.fail("accepted: " + bad);
            } catch (IllegalArgumentException expected) {
                Assert.assertTrue(expected.getMessage().contains(bad), expected.getMessage());
            }
        }
    }

    @Test
    public void anEmptySectionIsRefused() {
        try {
            PuzzleAcceptance.parse(Arrays.asList("# nothing", ""));
            Assert.fail("an empty acceptance state accepts every game");
        } catch (IllegalArgumentException expected) { }
    }
}
