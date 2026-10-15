package forge.spike;

import forge.game.Game;
import forge.game.card.Card;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.zone.ZoneType;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import static forge.spike.LineFindingProbeTest.Recorder;
import static forge.spike.LineFindingProbeTest.bareGame;
import static forge.spike.LineFindingProbeTest.card;
import static forge.spike.LineFindingProbeTest.seat;

/**
 * What a decision is asked WITH, under the faithful profile: each test is a case where the prompt
 * left out what the decision turned on (runs/loop g01-g07), checked against the live controller.
 */
public class PromptFidelityTest {
    @BeforeClass
    public void init() { new LineFindingProbeTest().initializeModel(); }

    @BeforeMethod
    public void faithful() { LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL; }

    @AfterMethod(alwaysRun = true)
    public void legacy() { LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null; }

    static String promptWith(Recorder r, String needle) {
        for (String p : r.prompts) if (p.contains(needle)) return p;
        Assert.fail("no prompt contains " + needle + ": " + r.prompts);
        return null;
    }

    /** runs/loop/g07: eight creatures, six the seat's, none marked, and it untapped the opponent's. */
    @Test
    public void targetOptionsSayWhoseObjectEachIs() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Mountain", me, ZoneType.Battlefield);
        card("Lightning Bolt", me, ZoneType.Hand);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Savannah Lions", rival, ZoneType.Battlefield);
        Recorder smart = new Recorder("CHOICE: 1"), fast = new Recorder("CHOICE: 1");
        seat(g, smart, fast).chooseSpellAbilityToPlay();
        Recorder asked = smart.asked("Choose the TARGET") ? smart : fast;
        String p = promptWith(asked, "Choose the TARGET");
        Assert.assertTrue(p.matches("(?s).*Grizzly Bears[^\\n]*\\[Battlefield, yours\\].*"), p);
        Assert.assertTrue(p.matches("(?s).*Savannah Lions[^\\n]*\\[Battlefield, opponent's\\].*"), p);
        Assert.assertTrue(p.contains("you (Seat)") && p.contains("opponent (Rival)"), p);
    }

    /** The fast tier was told "NO reasoning" by its system prompt and "Reason briefly" by every decision. */
    @Test
    public void theFastTierIsNotToldNotToReason() {
        Assert.assertFalse(LlmSpikeGui.PlayerControllerLLM.framing("Seat", "", true, false).contains("NO reasoning"));
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null;
        Assert.assertTrue(LlmSpikeGui.PlayerControllerLLM.framing("Seat", "", true, false).contains("NO reasoning"),
                "legacy profiles keep their bytes");
    }

    /** Sneak Attack's INSPECT text was cut at 90 characters, before "sacrifice the creature". */
    @Test
    public void inspectReturnsTheWholeRulesText() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Sneak Attack", me, ZoneType.Hand);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        String r = ctrl.observer().inspect("Sneak Attack");
        Assert.assertTrue(r.contains("Sacrifice the creature at the beginning of the next end step"), r);
    }

    /** "Choose a number [Galvanic Discharge]" did not say what the number does. */
    @Test
    public void aNumberIsAskedWithTheSourcesRulesText() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card gd = card("Galvanic Discharge", me, ZoneType.Hand);
        Recorder smart = new Recorder("CHOICE: 2"), fast = new Recorder("CHOICE: 2");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, smart, fast);
        int v = ctrl.chooseNumber(gd.getFirstSpellAbility(), "amount of energy to pay", 0, 3);
        Assert.assertEquals(v, 1);
        String p = promptWith(fast, "amount of energy to pay");
        Assert.assertTrue(p.contains("[Galvanic Discharge: Choose target creature or planeswalker."), p);
    }
}
