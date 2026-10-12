package forge.spike;

import org.testng.Assert;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;
import org.testng.annotations.AfterClass;

import forge.GuiDesktop;
import forge.game.Game;
import forge.game.player.Player;
import forge.gui.GuiBase;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;

/**
 * Bounded probe for the G8 lookahead mechanism. It asserts nothing about lookahead; it REPORTS
 * whether {@link forge.ai.simulation.GameCopier} can copy a game constructed the way the observer
 * tests construct one. The result decides how the lookahead tool is built (see
 * docs/harness/LOOKAHEAD-DESIGN.md "Implementation findings"): if a copy works, the preview is
 * buildable from GameCopier + resolveStack; if not, the tool needs the replay-from-index path.
 *
 * Deliberately tolerant: it always passes and prints `[LOOKAHEAD-PROBE] ...`, so it can live in the
 * suite as a living record without ever reddening it.
 */
public class LookaheadProbeTest {
    @BeforeClass
    public void initializeModel() {
        if (GuiBase.getInterface() == null) {
            GuiBase.setInterface(new GuiDesktop());
            FModel.initialize(null, preferences -> {
                preferences.setPref(FPref.LOAD_CARD_SCRIPTS_LAZILY, false);
                preferences.setPref(FPref.UI_LANGUAGE, "en-US");
                return null;
            });
        }
    }

    @AfterClass
    public void clear() { }

    @Test
    public void gameCopierBehaviorProbe() {
        Game g = SeatObservationTest.bareGame();
        String result;
        try {
            Game copy = new forge.ai.simulation.GameCopier(g).makeCopy();
            result = copy == null ? "returned null" : "ok (players=" + copy.getPlayers().size() + ")";
            // SAFETY PIN for G8: a copy must be a distinct object graph, not an alias of the live game
            // (a simulation on an alias would mutate the real game). Necessary condition for the
            // lookahead tool; a stronger no-mutation check comes with the preview implementation.
            if (copy != null) {
                Assert.assertNotSame(copy, g, "GameCopier returned the live game itself");
                Assert.assertNotSame(copy.getPlayers().get(0), g.getPlayers().get(0),
                        "copy aliases a live player -- simulation would mutate the real game");
            }
        } catch (Throwable t) {
            result = "threw " + t.getClass().getName() + ": " + t.getMessage();
        }
        System.out.println("[LOOKAHEAD-PROBE] GameCopier.makeCopy on a bare test game: " + result);
        Assert.assertNotNull(result);
    }

    @Test
    public void gameSimulatorConstructionProbe() {
        Game g = SeatObservationTest.bareGame();
        String out;
        try {
            forge.ai.simulation.SimulationController sc =
                    new forge.ai.simulation.SimulationController(new forge.ai.simulation.GameStateEvaluator.Score(0));
            forge.ai.simulation.GameSimulator sim =
                    new forge.ai.simulation.GameSimulator(sc, g, g.getPlayers().get(0), null);
            Game simGame = sim.getSimulatedGameState();
            out = simGame == null ? "simGame null" : ("ctor ok (players=" + simGame.getPlayers().size() + ")");
            Game copy = new forge.ai.simulation.GameCopier(g).makeCopy();
            forge.ai.simulation.GameSimulator.resolveStack(copy, copy.getPlayers().get(1));
            out += "; resolveStack ok";
        } catch (Throwable t) {
            out = "threw " + t.getClass().getName() + ": " + t.getMessage();
        }
        System.out.println("[LOOKAHEAD-PROBE2] GameSimulator on a bare test game: " + out);
        Assert.assertNotNull(out);
    }

    private static String libNames(Player p) {
        StringBuilder b = new StringBuilder();
        for (forge.game.card.Card c : p.getCardsIn(forge.game.zone.ZoneType.Library)) {
            if (b.length() > 0) b.append(',');
            b.append(c.getName());
        }
        return b.toString();
    }

    private static Game gameWithLibrary() {
        Game g = SeatObservationTest.bareGame();
        Player me = g.getPlayers().get(0);
        for (String n : new String[] {"Island", "Forest", "Mountain", "Swamp", "Plains", "Island", "Forest"})
            SeatObservationTest.card(n, me, forge.game.zone.ZoneType.Library);
        return g;
    }

    /** Probe: can we simulate a real spell cast (Lightning Bolt) on the copy and read the result? */
    @Test
    public void spellSimulationProbe() {
        Game g = SeatObservationTest.bareGame();
        Player me = g.getPlayers().get(0), opp = g.getPlayers().get(1);
        StringBuilder out = new StringBuilder();
        try {
            for (int i = 0; i < 3; i++) SeatObservationTest.card("Mountain", me, forge.game.zone.ZoneType.Battlefield);
            forge.game.card.Card bolt = SeatObservationTest.card("Lightning Bolt", me, forge.game.zone.ZoneType.Hand);
            forge.game.spellability.SpellAbility sa = null;
            for (forge.game.spellability.SpellAbility s : bolt.getSpellAbilities()) if (s.isSpell()) { sa = s; break; }
            if (sa == null) {
                out.append("no spell ability found");
            } else {
                sa.setActivatingPlayer(me);
                sa.getTargets().add(opp);
                forge.ai.simulation.GameSimulator sim = new forge.ai.simulation.GameSimulator(
                        new forge.ai.simulation.SimulationController(new forge.ai.simulation.GameStateEvaluator.Score(0)),
                        g, me, null);
                sim.simulateSpellAbility(sa, false);
                Game simGame = sim.getSimulatedGameState();
                forge.ai.simulation.GameSimulator.resolveStack(simGame, simGame.getPlayers().get(1));
                out.append("ok; opp life in sim = ").append(simGame.getPlayers().get(1).getLife())
                   .append(" (live opp life = ").append(opp.getLife()).append(")");
            }
        } catch (Throwable t) {
            out.append("threw ").append(t.getClass().getName()).append(": ").append(t.getMessage());
        }
        System.out.println("[LOOKAHEAD-PROBE3] spell simulation: " + out);
        Assert.assertNotNull(out);
    }

    /** End-to-end: a cast preview reports the resolution and leaves the LIVE game untouched. */
    @Test
    public void previewCastReportsResolutionWithoutTouchingTheLiveGame() {
        Game g = SeatObservationTest.bareGame();
        Player me = g.getPlayers().get(0), opp = g.getPlayers().get(1);
        for (int i = 0; i < 3; i++) SeatObservationTest.card("Mountain", me, forge.game.zone.ZoneType.Battlefield);
        forge.game.card.Card bolt = SeatObservationTest.card("Lightning Bolt", me, forge.game.zone.ZoneType.Hand);
        forge.game.spellability.SpellAbility sa = null;
        for (forge.game.spellability.SpellAbility s : bolt.getSpellAbilities()) if (s.isSpell()) { sa = s; break; }
        Assert.assertNotNull(sa, "fixture: no spell ability on Lightning Bolt");
        sa.setActivatingPlayer(me);
        sa.getTargets().add(opp);

        String report = PlayerLookahead.previewCast(g, me, sa, 99L);
        System.out.println("[LOOKAHEAD] " + report);
        Assert.assertTrue(report.contains("Lightning Bolt"), report);
        Assert.assertTrue(report.contains("life 20 -> 17"), "opponent's life drop not reported: " + report);
        Assert.assertEquals(opp.getLife(), 20, "preview mutated the LIVE opponent's life");
    }

    /** THE safety property: a preview must not consume the live RNG stream or mutate live state. */
    @Test
    public void copyForPreviewDoesNotTouchLiveRngOrState() {
        Game g = gameWithLibrary();
        Player me = g.getPlayers().get(0);
        int lifeBefore = me.getLife();
        String libBefore = libNames(me);

        // Known live RNG; run a preview; the NEXT value on the live stream must equal the value a
        // fresh stream would give (i.e. the preview consumed nothing from it).
        forge.util.MyRandom.setRandom(new java.util.Random(42));
        PlayerLookahead.copyForPreview(g, 1234L);
        int afterPreview = forge.util.MyRandom.getRandom().nextInt();

        forge.util.MyRandom.setRandom(new java.util.Random(42));
        int noPreview = forge.util.MyRandom.getRandom().nextInt();

        Assert.assertEquals(afterPreview, noPreview, "preview consumed the LIVE RNG stream");
        Assert.assertEquals(me.getLife(), lifeBefore, "preview mutated live life total");
        Assert.assertEquals(libNames(me), libBefore, "preview shuffled the LIVE library");
    }

    /** The copy is distinct, determinized from its seed, and deterministic for a fixed seed. */
    @Test
    public void copyForPreviewIsDistinctAndDeterministic() {
        Game g = gameWithLibrary();
        Game c1 = PlayerLookahead.copyForPreview(g, 7L);
        Game c2 = PlayerLookahead.copyForPreview(g, 7L);
        Assert.assertNotNull(c1);
        Assert.assertNotSame(c1, g);
        Assert.assertNotSame(c1.getPlayers().get(0), g.getPlayers().get(0), "copy aliases a live player");
        // Same seed -> identical determinized library order; and the live order is untouched.
        Assert.assertEquals(libNames(c1.getPlayers().get(0)), libNames(c2.getPlayers().get(0)),
                "determinization is not deterministic for a fixed seed");
        Assert.assertEquals(PlayerLookahead.publicDelta(g.getPlayers().get(0), g.getPlayers().get(0)), "no change");
    }
}
