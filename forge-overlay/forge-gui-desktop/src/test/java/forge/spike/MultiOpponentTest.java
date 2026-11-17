package forge.spike;

import java.util.HashMap;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;

import forge.game.Game;
import forge.game.card.Card;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.zone.ZoneType;

import static forge.spike.LineFindingProbeTest.bareGame;
import static forge.spike.LineFindingProbeTest.card;

/** What the seat reads with more than one opponent (Commander plan C2, multi-opponent-v1, harness-epoch-103):
 *  every opponent named where one used to be "the opponent", and a Commander player's tax and damage. A two-player
 *  game reads as before (GoldenChecksTest.twoPlayerStartAndAttackAsksAreUnchanged and the prompt-fidelity tests). */
public class MultiOpponentTest {

    @BeforeClass
    public void model() { new LineFindingProbeTest().initializeModel(); }

    private static String state(Game g, Player viewer) {
        return new SeatObservation(g, viewer).render("Seat", LlmSpikeGui.ContextProfile.V4_FAITHFUL, "", null, 0);
    }

    @Test
    public void factsGiveTheClocksAgainstEachOpponentByName() {
        Game g = bareGame(PhaseType.MAIN1, 3);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1), third = g.getPlayers().get(2);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Hill Giant", third, ZoneType.Battlefield);
        third.setLife(2, null);
        String s = state(g, me);
        Assert.assertTrue(s.contains("your next combat against Rival: 1 attacker(s) dealing 2 if none is blocked; Rival's life 20"), s);
        Assert.assertTrue(s.contains("your next combat against Third: 1 attacker(s) dealing 2 if none is blocked; Third's life 2"), s);
        Assert.assertTrue(s.contains("Third's next combat against you: 1 attacker(s) dealing 3 if none is blocked; YOUR life 20"), s);
        Assert.assertTrue(s.contains("Rival can make up to 0 mana") && s.contains("Third can make up to 0 mana"), s);
        Assert.assertTrue(s.contains("OPP [Rival]") && s.contains("OPP [Third]"), s);
        Assert.assertFalse(s.contains("commander"), "no Commander facts outside Commander");
        // Two players: the one-opponent FACTS, as before.
        Game two = bareGame(PhaseType.MAIN1);
        card("Grizzly Bears", two.getPlayers().get(0), ZoneType.Battlefield);
        String t = state(two, two.getPlayers().get(0));
        Assert.assertTrue(t.contains("ESTIMATES (Forge AI's arithmetic, not certain): your next combat: 1 attacker(s) dealing 2 if none is blocked; their life 20"), t);
    }

    @Test
    public void aCommanderPlayersLineShowsTheTaxAndTheDamageTaken() {
        Game g = bareGame(PhaseType.MAIN1, 3);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card atraxa = card("Atraxa, Praetors' Voice", rival, ZoneType.Command);
        rival.addCommander(atraxa);
        rival.incCommanderCast(atraxa);
        rival.incCommanderCast(atraxa);
        me.addCommanderDamage(atraxa, 7);
        String rivalLine = SeatObservation.commanderFacts(rival), myLine = SeatObservation.commanderFacts(me);
        Assert.assertEquals(rivalLine, " | commander Atraxa, Praetors' Voice cast 2 times (next cast from the command zone costs {4} more)");
        Assert.assertEquals(myLine, " | commander damage taken: Atraxa, Praetors' Voice (Rival's) 7/21");
        Assert.assertEquals(SeatObservation.commanderFacts(g.getPlayers().get(2)), "", "no commander, no damage: nothing");
        Assert.assertTrue(state(g, me).contains(myLine), "on the seat's own line");
    }

    @Test
    public void theSystemPromptSaysHowManyOpponentsAndTheCommanderRules() {
        Assert.assertEquals(MultiSeat.opponentsClause(2, "legacy"), "against one opponent. Your goal is to win the game.");
        String four = MultiSeat.opponentsClause(4, "commander");
        Assert.assertTrue(four.startsWith("against 3 opponents, every player against every other"), four);
        Assert.assertTrue(four.contains("21 or more combat damage by one commander") && four.contains("40 life"), four);
        Map<String, String> env = new HashMap<>();
        env.put("PLAYERS", "4");
        LlmSpikeGui.envOverride = env;
        try {
            String sys = LlmSpikeGui.PlayerControllerLLM.seatFraming("commander", "Seat", "", false, false, true);
            Assert.assertTrue(sys.startsWith("You are playing Magic: The Gathering, Commander format, as the player \"Seat\" "
                    + "against 3 opponents"), sys.substring(0, 200));
        } finally {
            LlmSpikeGui.envOverride = null;
        }
        String two = LlmSpikeGui.PlayerControllerLLM.seatFraming("legacy", "Seat", "", false, false, true);
        Assert.assertTrue(two.startsWith("You are playing Magic: The Gathering, Legacy format, as the player \"Seat\" against "
                + "one opponent. Your goal is to win the game.\n\n"), two.substring(0, 200));
    }

    @Test
    public void theMulliganAskStatesTheMultiplayerRules() {
        String first = MultiSeat.mulliganPrompt(7, 3, "a, b", false);
        Assert.assertTrue(first.contains("do not take the first turn") && first.contains("every player draws on their first turn"), first);
        Assert.assertTrue(first.contains("a new hand of 7 cards (the first mulligan is free"), first);
        Assert.assertTrue(MultiSeat.mulliganPrompt(6, 2, "a", true).contains("a new hand of 5 cards"));
    }

    @Test
    public void anOpponentsCardIsLabelledWithItsOwner() {
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL;
        try { labels(); } finally { LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null; }
    }

    private void labels() {
        Game g = bareGame(PhaseType.MAIN1, 3);
        Script model = new Script();
        LlmSpikeGui.PlayerControllerLLM ctrl = LineFindingProbeTest.seat(g, model, model);
        Card bears = card("Grizzly Bears", g.getPlayers().get(2), ZoneType.Battlefield);
        Assert.assertTrue(ctrl.labelOf(bears).endsWith("[Battlefield, Third's]"), ctrl.labelOf(bears));
        Game two = bareGame(PhaseType.MAIN1);
        LlmSpikeGui.PlayerControllerLLM c2 = LineFindingProbeTest.seat(two, model, model);
        Card b2 = card("Grizzly Bears", two.getPlayers().get(1), ZoneType.Battlefield);
        Assert.assertTrue(c2.labelOf(b2).endsWith("[Battlefield, opponent's]"), c2.labelOf(b2));
    }

    /** A Commander deck's commander is in its decklist and card texts, marked (critique U10: both read the main deck
     *  only, so the seat was never told its own commander); a deck with no commander reads as before. */
    @Test
    public void theSeatsOwnCommanderIsInItsDecklistAndCardTexts() throws Exception {
        forge.deck.Deck precon = LlmSpikeGui.loadDeck("Abzan Armor", new java.io.File(
                forge.localinstance.properties.ForgeConstants.RES_DIR, "quest/commanderprecons/Abzan Armor [TDC] [2025].dck").getPath());
        String list = LlmSpikeGui.decklist(precon), text = LlmSpikeGui.cardText(precon);
        Assert.assertTrue(list.startsWith("commander: Felothar the Steadfast; main deck: 1x "), list.substring(0, 80));
        Assert.assertTrue(text.startsWith("  Felothar the Steadfast (your commander) {1}{W}{B}{G} — "), text.substring(0, 120));
        forge.deck.Deck plain = new forge.deck.Deck("plain");
        plain.getMain().add("Grizzly Bears", 2);
        Assert.assertEquals(LlmSpikeGui.decklist(plain), "2x Grizzly Bears");
        Assert.assertEquals(LlmSpikeGui.cardText(plain), "  Grizzly Bears {1}{G}\n");
    }

    /** A model that is never asked here. */
    static final class Script extends LineFindingProbeTest.Recorder {
        Script() { super("CHOICE: 1"); }
    }
}
