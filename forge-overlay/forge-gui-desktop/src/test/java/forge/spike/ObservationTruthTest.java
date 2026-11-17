package forge.spike;

import static forge.spike.LineFindingProbeTest.bareGame;
import static forge.spike.LineFindingProbeTest.card;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import forge.ai.LobbyPlayerAi;
import forge.game.Game;
import forge.game.card.Card;
import forge.game.card.CardCollection;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.zone.ZoneType;

/** The faithful state is smaller and says what each part is (critique batch 4). */
public class ObservationTruthTest {

    @BeforeClass
    public void init() { new LineFindingProbeTest().initializeModel(); }

    @BeforeMethod
    public void faithful() { LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL; }

    @AfterMethod(alwaysRun = true)
    public void legacy() { LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null; }

    private static LlmSpikeGui.PlayerControllerLLM seat(Game g) {
        Player me = g.getPlayers().get(0);
        LineFindingProbeTest.Recorder model = new LineFindingProbeTest.Recorder("CHOICE: 1");
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(g, me,
                new LobbyPlayerAi("Seat", null), "Seat", "", model, model, "llm");
        me.dangerouslySetController(ctrl);
        return ctrl;
    }

    /** O1: a search of the seat's own library printed every card of it into the history and REVEALS TO YOU, re-sent
     *  with every later decision (two thirds of a turn-25 prompt). It is one line, and the library a current line. */
    @Test
    public void aLibrarySearchIsOneLineAndTheLibraryOneCurrentLine() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        CardCollection lib = new CardCollection();
        for (int i = 0; i < 6; i++) lib.add(card("Plains", me, ZoneType.Library));
        for (int i = 0; i < 4; i++) lib.add(card("Lightning Bolt", me, ZoneType.Library));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g);
        String before = ctrl.snapshot();
        Assert.assertFalse(before.contains("  library ("), "no library line before a search: " + before);
        ctrl.reveal(lib, ZoneType.Library, me, "Looking at cards", false);
        String after = ctrl.snapshot();
        Assert.assertTrue(after.contains("you looked through your library (10 cards"), after);
        Assert.assertTrue(after.contains("Lightning Bolt x4, Plains x6"), after);
        Assert.assertFalse(after.contains("revealed from your Library"), after);
        Assert.assertTrue(after.length() - before.length() < 600, "a search adds a line, not the library: " + (after.length() - before.length()));
    }

    /** O6, O7: keywords were a fixed list (no landwalk, toxic, changeling, nothing granted) and showed flash on a
     *  permanent; O15: a pumped creature read as its pumped size alone. */
    @Test
    public void aPermanentShowsItsEngineKeywordsAndItsBasePowerWhenChanged() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card bow = card("Orcish Bowmasters", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        bears.addCounter(forge.game.card.CounterEnumType.P1P1, 1, me, null);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g);
        String s = ctrl.snapshot();
        String bowLine = s.substring(s.indexOf("Orcish Bowmasters"));
        bowLine = bowLine.substring(0, bowLine.indexOf('\n'));
        Assert.assertFalse(bowLine.contains("flash"), "flash is a casting keyword: " + bowLine);
        Assert.assertTrue(s.contains("Grizzly Bears 3/3 (base 2/2)"), s);
        Assert.assertNotNull(bow);
    }

    /** O13: SPENT THIS TURN printed on the opponent's turn, where "attackers declared none" read as a choice made. */
    @Test
    public void spentThisTurnIsOnTheSeatsOwnTurnOnly() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g);
        String mine = ctrl.snapshot();
        Assert.assertTrue(mine.contains("SPENT THIS TURN") && mine.contains("no attack declared yet"), mine);
        g.getPhaseHandler().devModeSet(PhaseType.MAIN1, rival, 2);
        Assert.assertFalse(ctrl.snapshot().contains("SPENT THIS TURN"));
        Assert.assertNotNull(me);
    }

    /** O8: the block of Forge AI's estimators was headed "FACTS". */
    @Test
    public void forgeAisArithmeticIsCalledAnEstimate() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        String s = seat(g).snapshot();
        Assert.assertTrue(s.contains("ESTIMATES (Forge AI's arithmetic, not certain):"), s);
        Assert.assertFalse(s.contains("FACTS ("), s);
    }
}
