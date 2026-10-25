package forge.spike;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;

import forge.game.player.RegisteredPlayer;
import forge.player.LobbyPlayerHuman;

/** A person can sit at one seat of the GUI match (CONTROLLER_A/B=human, play-vs-llm.sh) and play the model through
 *  Forge's own human controller. Only the GUI can seat one: a headless, fork or puzzle run has no window to ask. */
public class HumanSeatTest {

    private static Map<String, String> env(String a, String b) {
        Map<String, String> env = new HashMap<>();
        env.put("CONTROLLER_A", a);
        env.put("CONTROLLER_B", b);
        return env;
    }

    private static List<RegisteredPlayer> build(Map<String, String> env, boolean gui) {
        new LineFindingProbeTest().initializeModel();
        LlmSpikeGui.envOverride = env;
        try {
            return LlmSpikeGui.buildPlayers(gui);
        } finally {
            LlmSpikeGui.envOverride = null;
        }
    }

    @Test
    public void theGuiSeatsAPersonOnForgesHumanController() {
        List<RegisteredPlayer> players = build(env("human", "floor"), true);
        Assert.assertTrue(players.get(0).getPlayer() instanceof LobbyPlayerHuman, "seat A is the person");
        Assert.assertFalse(players.get(1).getPlayer() instanceof LobbyPlayerHuman, "seat B stays the harness's");
        Assert.assertSame(LlmSpikeGui.humanSeat(players), players.get(0));
    }

    @Test
    public void aPersonCanSitAtSeatBWithNoModelNamed() {
        // No SMART_MODEL / FAST_MODEL at all: the person's seat asks no model, so it is not refused for naming none.
        List<RegisteredPlayer> players = build(env("floor", "human"), true);
        Assert.assertTrue(players.get(1).getPlayer() instanceof LobbyPlayerHuman);
    }

    /** The puzzle runner builds its seats with seatFor, not buildPlayers: a person cannot be its opponent either. */
    @Test
    public void seatForNeverBuildsAPerson() {
        try {
            LlmSpikeGui.seatFor(LlmSpikeGui.HUMAN, "Opponent", "", "greedy:", "greedy:");
            Assert.fail("seatFor seated a person");
        } catch (IllegalStateException e) {
            Assert.assertTrue(e.getMessage().contains("play-vs-llm.sh"), e.getMessage());
        }
    }

    @Test
    public void aRunWithNoWindowRefusesAHumanSeat() {
        try {
            build(env("human", "floor"), false);
            Assert.fail("a headless run seated a person it cannot ask");
        } catch (IllegalStateException e) {
            Assert.assertTrue(e.getMessage().contains("play-vs-llm.sh"), e.getMessage());
        }
    }

    @Test
    public void twoHumanSeatsAreRefused() {
        try {
            build(env("human", "human"), true);
            Assert.fail("two people were seated at one window");
        } catch (IllegalStateException e) {
            Assert.assertTrue(e.getMessage().contains("one"), e.getMessage());
        }
    }

    /** Forge warns a person that its AI plays some of the opponent's cards badly; no Forge AI plays the model's seat,
     *  so with a person seated the warning only misleads (it named Rishadan Port in a Burn vs Goblins smoke match). */
    @Test
    public void aPersonIsNotWarnedAboutForgesAiAndASpectatorMatchIsUnchanged() {
        Assert.assertFalse(LlmSpikeGui.guiRules(1, true).warnAboutAICards());
        Assert.assertTrue(LlmSpikeGui.guiRules(3, false).warnAboutAICards());
        Assert.assertEquals(LlmSpikeGui.guiRules(3, false).getGamesPerMatch(), 3);
    }

    @Test
    public void aMatchWithNoPersonHasNoHumanSeat() {
        Assert.assertNull(LlmSpikeGui.humanSeat(build(env("floor", "random"), true)));
    }
}
