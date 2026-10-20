package forge.spike;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

import org.testng.Assert;
import org.testng.annotations.AfterClass;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;

import forge.GuiDesktop;
import forge.game.Game;
import forge.game.card.Card;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.zone.ZoneType;
import forge.gui.GuiBase;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;

/** The demo's audience feed (DemoFeed) shows the screen both hands and the seat's handle map, and changes nothing
 *  the seat is shown: with the feed on and off the seat's prompts are byte-identical. */
public class DemoFeedTest {
    private static boolean initialized;

    @BeforeClass
    public void initializeModel() {
        if (initialized) return;
        GuiBase.setInterface(new GuiDesktop());
        FModel.initialize(null, preferences -> {
            preferences.setPref(FPref.LOAD_CARD_SCRIPTS_LAZILY, false);
            preferences.setPref(FPref.UI_LANGUAGE, "en-US");
            return null;
        });
        initialized = true;
    }

    /** The seat the demo shows plays the faithful profile, so its observation (and handle map) exists. */
    @BeforeClass
    public void faithful() { LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL; }

    @AfterClass
    public void legacy() { LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null; }

    /** One fixed game: the seat holds Lightning Bolt, the rival holds Counterspell (hidden from the seat). The seat
     *  is asked, a card moves (a zone-change event the feed answers), and the seat is asked again. */
    private static List<String> play(boolean feed, Path dir) throws Exception {
        LlmSpikeGui.EvalLog.closeForTest();
        DemoFeed.closeForTest();
        LlmSpikeGui.EvalLog.initToDir(dir.toString(), 7);
        try {
            Game g = LineFindingProbeTest.bareGame(PhaseType.MAIN1);
            Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
            LineFindingProbeTest.card("Mountain", me, ZoneType.Battlefield);
            LineFindingProbeTest.card("Lightning Bolt", me, ZoneType.Hand);
            LineFindingProbeTest.card("Counterspell", rival, ZoneType.Hand);
            Card bears = LineFindingProbeTest.card("Grizzly Bears", rival, ZoneType.Library);
            LineFindingProbeTest.Recorder model = new LineFindingProbeTest.Recorder("CHOICE: 99");
            LlmSpikeGui.PlayerControllerLLM ctrl = LineFindingProbeTest.seat(g, model, model);
            Assert.assertNotNull(ctrl.observer(), "the faithful seat has an observation");
            if (feed) Assert.assertNotNull(DemoFeed.attachTo(g, "Seat", ctrl.observer(), LlmSpikeGui.EvalLog.currentFile()));
            ctrl.chooseSpellAbilityToPlay();
            g.getAction().moveToHand(bears, null);
            ctrl.chooseSpellAbilityToPlay();
            return new ArrayList<>(model.prompts);
        } finally {
            DemoFeed.closeForTest();
            LlmSpikeGui.EvalLog.closeForTest();
        }
    }

    @Test
    public void theFeedShowsBothHandsAndTheSeatsHandles() throws Exception {
        Path dir = Files.createTempDirectory("demo-feed");
        play(true, dir);
        Path feed = null;
        for (Path p : Files.newDirectoryStream(dir)) if (p.getFileName().toString().startsWith("feed-")) feed = p;
        Assert.assertNotNull(feed, "no feed file beside the game JSONL");
        String rows = new String(Files.readAllBytes(feed));
        Assert.assertTrue(rows.contains("\"name\":\"Counterspell\""), "the rival's hidden hand is on the feed: " + rows);
        Assert.assertTrue(rows.contains("\"name\":\"Grizzly Bears\""), "the moved card is on the feed: " + rows);
        Assert.assertTrue(rows.contains("\"handles\":{\"Seat\":{"), "the seat's handle map is on the feed: " + rows);
        Assert.assertTrue(rows.contains("\"jsonl_offset\":"), rows);
    }

    @Test
    public void theSeatsPromptsAreByteIdenticalWithTheFeedOnAndOff() throws Exception {
        List<String> off = play(false, Files.createTempDirectory("demo-feed-off"));
        List<String> on = play(true, Files.createTempDirectory("demo-feed-on"));
        Assert.assertEquals(off.size(), 2, "the seat was asked twice: " + off);
        Assert.assertEquals(on, off, "the feed changed what the seat was shown");
    }

    @Test
    public void withoutTheGameJsonlTheFeedStaysOff() {
        Game g = LineFindingProbeTest.bareGame(PhaseType.MAIN1);
        DemoFeed.closeForTest();
        Assert.assertNull(DemoFeed.attachTo(g, "Seat", null, null));
    }
}
