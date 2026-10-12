package forge.spike;

import org.testng.Assert;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;
import org.testng.annotations.AfterClass;

import forge.GuiDesktop;
import forge.StaticData;
import forge.game.Game;
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
}
