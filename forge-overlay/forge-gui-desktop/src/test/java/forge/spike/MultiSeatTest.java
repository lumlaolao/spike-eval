package forge.spike;

import java.io.File;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.testng.Assert;
import org.testng.SkipException;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;

import forge.deck.Deck;
import forge.game.Game;
import forge.game.GameType;
import forge.game.phase.PhaseType;
import forge.game.player.GameLossReason;
import forge.game.player.Player;
import forge.game.player.RegisteredPlayer;
import forge.localinstance.properties.ForgeConstants;

/** Games of more than two seats and Commander games (MultiSeat): the seat count, seats C and D, the Commander
 *  registration, and each seat's place on the game line. A two-seat Constructed game takes none of it. */
public class MultiSeatTest {

    @BeforeClass
    public void model() { new LineFindingProbeTest().initializeModel(); }

    private static String deck(String name) {
        String dir = System.getProperty("harness.decks.dir");
        if (dir == null) throw new SkipException("pass -Dharness.decks.dir=<repo>/eval/decks (the acceptance suite does)");
        return new File(dir, name + ".txt").getPath();
    }

    private static String precon(String file) {
        return new File(ForgeConstants.RES_DIR, "quest/commanderprecons/" + file).getPath();
    }

    private static List<RegisteredPlayer> build(Map<String, String> env) {
        new LineFindingProbeTest().initializeModel();
        LlmSpikeGui.envOverride = env;
        try {
            return LlmSpikeGui.buildPlayers();
        } finally {
            LlmSpikeGui.envOverride = null;
        }
    }

    private static Map<String, String> fourSeats() {
        Map<String, String> env = new HashMap<>();
        env.put("PLAYERS", "4");
        String[] decks = {"burn", "elves", "goblins", "infect"};
        for (int i = 0; i < 4; i++) {
            String id = MultiSeat.IDS[i];
            env.put("DECK_" + id, deck(decks[i]));
            env.put("DECK_" + id + "_NAME", decks[i]);
            env.put("CONTROLLER_" + id, "ai");
        }
        return env;
    }

    @Test
    public void twoSeatsIsTheDefaultAndTheCountIsChecked() {
        Map<String, String> env = new HashMap<>();
        Assert.assertEquals(MultiSeat.players(env), 2);
        Assert.assertFalse(MultiSeat.placed(env), "a two-seat Constructed game writes no places");
        env.put("GAME_FORMAT", "legacy");
        Assert.assertFalse(MultiSeat.placed(env));
        for (String bad : new String[] {"1", "5", "four"}) {
            env.put("PLAYERS", bad);
            try { MultiSeat.players(env); Assert.fail("PLAYERS=" + bad + " was accepted"); }
            catch (IllegalStateException e) { Assert.assertTrue(e.getMessage().contains("2 to 4"), e.getMessage()); }
        }
        env.put("PLAYERS", "2");
        env.put("GAME_FORMAT", "commander");
        Assert.assertTrue(MultiSeat.placed(env), "a two-seat Commander game writes its places");
        Assert.assertEquals(MultiSeat.rules(true).getGameType(), GameType.Commander);
        Assert.assertTrue(MultiSeat.rules(true).hasAppliedVariant(GameType.Commander));
        Assert.assertEquals(MultiSeat.rules(false).getGameType(), GameType.Constructed);
    }

    @Test
    public void fourSeatsAreBuiltInSeatOrderEachWithItsOwnDeckAndController() {
        Map<String, String> env = fourSeats();
        env.put("CONTROLLER_C", "random");
        List<RegisteredPlayer> players = build(env);
        Assert.assertEquals(players.size(), 4);
        String[] names = {"burn", "elves", "goblins", "infect"};
        for (int i = 0; i < 4; i++) Assert.assertEquals(players.get(i).getPlayer().getName(), names[i]);
        Assert.assertEquals(players.get(2).getPlayer().getClass(), LlmSpikeGui.LobbyPlayerLLM.class, "seat C random");
        Assert.assertEquals(players.get(3).getPlayer().getClass(), forge.ai.LobbyPlayerAi.class, "seat D ai");
    }

    @Test
    public void aSeatWithoutADeckOrWithAnotherSeatsNameIsRefused() {
        Map<String, String> env = fourSeats();
        env.remove("DECK_D");
        try { build(env); Assert.fail("seat D had no deck"); }
        catch (IllegalStateException e) { Assert.assertTrue(e.getMessage().contains("DECK_D"), e.getMessage()); }
        env = fourSeats();
        env.put("DECK_C_NAME", "burn");
        try { build(env); Assert.fail("seat C took seat A's name"); }
        catch (IllegalStateException e) { Assert.assertTrue(e.getMessage().contains("DECK_C_NAME"), e.getMessage()); }
        env = fourSeats();
        env.put("SWAP_SEATS", "1");
        try { build(env); Assert.fail("a four-seat swap"); }
        catch (IllegalStateException e) { Assert.assertTrue(e.getMessage().contains("SWAP_SEATS"), e.getMessage()); }
    }

    @Test
    public void aCommanderSeatReadsAPreconAndIsRegisteredWithItsCommander() throws Exception {
        Map<String, String> env = new HashMap<>();
        env.put("GAME_FORMAT", "commander");
        env.put("DECK_A", precon("Abzan Armor [TDC] [2025].dck"));
        env.put("DECK_B", precon("Breed Lethality [C16] [2016].dck"));
        env.put("DECK_A_NAME", "Abzan Armor");
        env.put("DECK_B_NAME", "Breed Lethality");
        env.put("CONTROLLER_A", "ai");
        env.put("CONTROLLER_B", "ai");
        env.put("EVAL_OUT", TestTmp.dir("multiseat").toString());   // a deck that fails to load is refused, not replaced
        List<RegisteredPlayer> players = build(env);
        Assert.assertEquals(players.size(), 2);
        for (RegisteredPlayer rp : players) {
            Assert.assertFalse(rp.getCommanders().isEmpty(), "registered with its commander");
            Assert.assertEquals(rp.getStartingLife(), 40);
        }
        Deck noCommander = LlmSpikeGui.loadDeck("burn", deck("burn"));
        try { MultiSeat.registered(noCommander, true); Assert.fail("a deck with no commander was seated"); }
        catch (IllegalStateException e) { Assert.assertTrue(e.getMessage().contains("no commander"), e.getMessage()); }
    }

    /** Places: the winner 1, then by the reverse order of losses, each loss with its turn and reason. */
    @Test
    public void theGameLineGivesEachSeatItsPlace() {
        Game g = LineFindingProbeTest.bareGame(PhaseType.MAIN1, 4);
        Map<String, String> env = new HashMap<>();
        env.put("PLAYERS", "4");
        env.put("DECK_A_NAME", "Seat");
        env.put("DECK_B_NAME", "Rival");
        env.put("DECK_C_NAME", "Third");
        env.put("DECK_D_NAME", "Fourth");
        env.put("DECK_C", "/decks/third.dck");
        env.put("DECK_D", "/decks/fourth.dck");
        MultiSeat.Places places = new MultiSeat.Places(g);
        MultiSeat.places = places;
        try {
            List<Player> ps = g.getPlayers();
            Player rival = ps.get(1), third = ps.get(2), fourth = ps.get(3);
            fourth.loseConditionMet(GameLossReason.Milled, null);
            g.onPlayerLost(fourth);
            places.note();
            rival.loseConditionMet(GameLossReason.LifeReachedZero, null);
            g.onPlayerLost(rival);
            third.loseConditionMet(GameLossReason.Poisoned, null);
            g.onPlayerLost(third);
            String f = MultiSeat.gameLineFields(g, env, "Seat", false, null);
            Assert.assertTrue(f.startsWith(",\"n_players\":4,\"multi_opponent_version\":\"" + MultiSeat.VERSION + "\",\"players\":[{\"seat\":\"A\",\"name\":\"Seat\""), f);
            Assert.assertTrue(f.contains("\"seat\":\"A\",\"name\":\"Seat\",\"controller\":\"llm\",\"deck\":\"\",\"place\":1,"
                    + "\"eliminated_turn\":null,\"reason\":\"won\""), f);
            Assert.assertTrue(f.contains("\"seat\":\"D\",\"name\":\"Fourth\",\"controller\":\"ai\",\"deck\":\"fourth.dck\","
                    + "\"place\":4,\"eliminated_turn\":" + g.getPhaseHandler().getTurn() + ",\"reason\":\"Milled\""), f);
            Assert.assertTrue(f.contains("\"seat\":\"B\",\"name\":\"Rival\",\"controller\":\"llm\",\"deck\":\"\",\"place\":3,"), f);
            Assert.assertTrue(f.contains("\"reason\":\"LifeReachedZero\""), f);
            Assert.assertTrue(f.contains("\"seat\":\"C\",\"name\":\"Third\",\"controller\":\"ai\",\"deck\":\"third.dck\",\"place\":2,"), f);
            Assert.assertEquals(MultiSeat.winner(env, "Third"), "C");
            Assert.assertEquals(MultiSeat.winner(env, "Nobody"), "invalid");
            Assert.assertEquals(MultiSeat.winner(env, null), "draw");
            String draw = MultiSeat.gameLineFields(LineFindingProbeTest.bareGame(PhaseType.MAIN1, 4), env, null, true, null);
            Assert.assertTrue(draw.contains("\"place\":null,\"eliminated_turn\":null,\"reason\":\"capped\""), draw);
        } finally {
            MultiSeat.places = null;
        }
    }
}
