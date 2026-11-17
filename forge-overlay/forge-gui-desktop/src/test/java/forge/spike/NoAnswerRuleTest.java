package forge.spike;

import static forge.spike.LineFindingProbeTest.bareGame;
import static forge.spike.LineFindingProbeTest.card;
import static forge.spike.LineFindingProbeTest.logOf;

import java.util.List;

import org.testng.Assert;
import org.testng.annotations.AfterClass;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import forge.ai.LobbyPlayerAi;
import forge.game.Game;
import forge.game.card.Card;
import forge.game.combat.Combat;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.spellability.SpellAbility;
import forge.game.zone.ZoneType;
import forge.util.collect.FCollection;

/**
 * The rule for a decision the seat did not answer (critique A3, A4, A13, A15, A18, C17, C18): the do-nothing option
 * where one exists (pass, no attack, decline, cancel a play not yet made), the first legal option only where a choice
 * is mandatory, and a row that names the cause and what was done. The floor and random arms keep their fixed
 * policies (land drop, all-in attack): the canaries pin them.
 */
public class NoAnswerRuleTest {

    @BeforeClass
    public void quickRetries() { QuickRetries.on(); }

    @AfterClass(alwaysRun = true)
    public void realRetries() { QuickRetries.off(); }

    @BeforeClass
    public void init() { new LineFindingProbeTest().initializeModel(); }

    @BeforeMethod
    public void faithful() { LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL; }

    @AfterMethod(alwaysRun = true)
    public void legacy() { LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null; }

    private static LlmSpikeGui.PlayerControllerLLM seat(Game g, LineFindingProbeTest.Recorder model, String mode) {
        Player me = g.getPlayers().get(0);
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(g, me,
                new LobbyPlayerAi("Seat", null), "Seat", "", model, model, mode);
        me.dangerouslySetController(ctrl);
        return ctrl;
    }

    /** The seat's call budget spent, as at the end of a long game. */
    private static void spend(LlmSpikeGui.PlayerControllerLLM ctrl) throws Exception {
        for (String f : new String[] {"smartCalls", "fastCalls"}) {
            java.lang.reflect.Field calls = LlmSpikeGui.PlayerControllerLLM.class.getDeclaredField(f);
            calls.setAccessible(true);
            calls.setInt(ctrl, LlmSpikeGui.PlayerControllerLLM.SMART_BUDGET);
        }
    }

    /** A3: out of calls, a model seat played the first land in its hand ("play(no-budget): land Wasteland" with Plateau
     *  in hand). It passes, as when its bridge is down. The floor arm's land drop is its baseline and stays. */
    @Test
    public void aModelSeatOutOfCallsPassesAndTheFloorArmStillPlaysItsLand() throws Exception {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Hand);
        LineFindingProbeTest.Recorder model = new LineFindingProbeTest.Recorder("CHOICE: 1");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, "llm");
        spend(ctrl);
        String log = logOf(() -> Assert.assertNull(ctrl.chooseSpellAbilityToPlay(), "no answer is a pass, never a land"));
        Assert.assertTrue(log.contains("play(no-budget)->PASS"), log);
        Assert.assertTrue(model.prompts.isEmpty(), "no call once the budget is spent");

        Game g2 = bareGame(PhaseType.MAIN1);
        card("Mountain", g2.getPlayers().get(0), ZoneType.Hand);
        LlmSpikeGui.PlayerControllerLLM floor = seat(g2, new LineFindingProbeTest.Recorder(null), "floor");
        List<SpellAbility> land = floor.chooseSpellAbilityToPlay();
        Assert.assertNotNull(land, "the floor arm plays its land");
        Assert.assertTrue(land.get(0).isLandAbility());
    }

    /** A4: out of calls, a model seat attacked with everything; with a dead bridge, with nothing. One rule: nothing.
     *  The floor arm still attacks all-in (its goldfish baseline). */
    @Test
    public void aModelSeatOutOfCallsDoesNotAttackAndTheFloorArmStillAttacksAllIn() throws Exception {
        for (String mode : new String[] {"llm", "floor"}) {
            Game g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
            Player me = g.getPlayers().get(0);
            card("Elite Vanguard", me, ZoneType.Battlefield);
            card("Savannah Lions", me, ZoneType.Battlefield);
            LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new LineFindingProbeTest.Recorder("PICKS: 1,2"), mode);
            spend(ctrl);
            Combat combat = new Combat(me);
            String log = logOf(() -> ctrl.declareAttackers(me, combat));
            if ("llm".equals(mode)) {
                Assert.assertEquals(combat.getAttackers().size(), 0, log);
                Assert.assertTrue(log.contains("attackers(no-budget)->none"), log);
            } else {
                Assert.assertEquals(combat.getAttackers().size(), 2, log);
                Assert.assertTrue(log.contains("attackers(no-model)->all-in x2"), log);
            }
        }
    }

    /** A13: a target ask that got no usable answer aimed at candidate 1, which for Swords to Plowshares is the seat's own
     *  creature, although CANCEL was on the list. The play is not made: the window passes, on the record. */
    @Test
    public void anUnansweredTargetCancelsThePlayInsteadOfHittingTheFirstCandidate() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Plains", me, ZoneType.Battlefield);
        Card own = card("Grizzly Bears", me, ZoneType.Battlefield);
        Card theirs = card("Serra Angel", rival, ZoneType.Battlefield);
        card("Swords to Plowshares", me, ZoneType.Hand);
        GoldenChecksTest.Script model = new GoldenChecksTest.Script("no idea")
                .on("You have priority", GoldenChecksTest.choice("CAST Swords to Plowshares"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, "llm");
        String log = logOf(() -> Assert.assertNull(ctrl.chooseSpellAbilityToPlay(), "no target answer: the window passes"));
        Assert.assertTrue(model.prompts.stream().anyMatch(p -> p.contains("Choose the TARGET")), "the target was asked");
        Assert.assertTrue(log.contains("target(unparsed)->cancel play"), log);
        Assert.assertFalse(log.contains("->first"), log);
        Assert.assertTrue(own.isInZone(ZoneType.Battlefield) && theirs.isInZone(ZoneType.Battlefield));
        Assert.assertEquals(g.getStack().size(), 0, "nothing was cast");
        Assert.assertEquals(ctrl.playsNotMade(), 1);
    }

    /** C17 and C18: a divided spell (Forked Bolt, 2 damage among one or two targets) whose target set got no answer took
     *  the first candidates, and a split that got none was an even split with no row. The play is not made. */
    @Test
    public void anUnansweredTargetSetCancelsThePlay() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Mountain", me, ZoneType.Battlefield);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Serra Angel", rival, ZoneType.Battlefield);
        card("Forked Bolt", me, ZoneType.Hand);
        GoldenChecksTest.Script model = new GoldenChecksTest.Script("no idea")
                .on("You have priority", GoldenChecksTest.choice("CAST Forked Bolt"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, "llm");
        String log = logOf(() -> Assert.assertNull(ctrl.chooseSpellAbilityToPlay(), "no target answer: the window passes"));
        Assert.assertTrue(log.contains("targets(multi,unparsed)->cancel play"), log);
        Assert.assertEquals(g.getStack().size(), 0, "nothing was cast");
        Assert.assertEquals(me.getLife() + rival.getLife(), 40);
    }

    /** A2: an exception inside the priority hook became a PASS with no decision row, read as the model's pass. It writes
     *  an auto row naming the exception, which defect_analyzer fails on. */
    @Test
    public void aPriorityHookThatThrowsWritesItsRow() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        card("Lightning Bolt", me, ZoneType.Hand);
        LineFindingProbeTest.Recorder broken = new LineFindingProbeTest.Recorder("CHOICE: 1") {
            @Override public String ask(String prompt) { throw new IllegalStateException("broken bridge code"); }
        };
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, broken, "llm");
        String log = logOf(() -> Assert.assertNull(ctrl.chooseSpellAbilityToPlay()));
        Assert.assertTrue(log.contains("[AUTO-LEGAL] play(err:IllegalStateException)->PASS"), log);
    }

    /** O12: a state that failed to render was sent anyway ("== (state unavailable: ...) =="), so the model answered
     *  blind. It is not sent; the fallback row says render-failed. */
    @Test
    public void aDecisionWhoseStateDidNotRenderIsNotAsked() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        card("Lightning Bolt", me, ZoneType.Hand);
        LineFindingProbeTest.Recorder model = new LineFindingProbeTest.Recorder("CHOICE: 1");
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(g, me,
                new LobbyPlayerAi("Seat", null), "Seat", "", model, model, "llm") {
            @Override String snapshot() { return LlmSpikeGui.PlayerControllerLLM.STATE_UNAVAILABLE + ": boom) =="; }
        };
        me.dangerouslySetController(ctrl);
        String log = logOf(() -> Assert.assertNull(ctrl.chooseSpellAbilityToPlay()));
        Assert.assertTrue(model.prompts.isEmpty(), "nothing was sent: " + model.prompts);
        Assert.assertTrue(log.contains("play(render-failed)->PASS"), log);
    }

    /** A20, A21: four optional asks answered "do it" when the answer was lost (Propaganda paid, an optional static
     *  applied, the largest creature tapped for harmonize, the whole cost reduction), and the row said "->first". */
    @Test
    public void optionalAsksThatGotNoAnswerDoNothingAndSayWhatTheyTook() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card src = card("Pithing Needle", me, ZoneType.Battlefield);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new LineFindingProbeTest.Recorder("no idea"), "llm");
        String log = logOf(() -> Assert.assertFalse(ctrl.confirmStaticApplication(src,
                forge.game.player.PlayerActionConfirmMode.Random, "Apply this optional effect?", null), "not applied"));
        Assert.assertTrue(log.contains("confirm-static(unparsed)->No"), log);
    }

    /** A30: an empty numeric range from the engine threw out of the hook and ended the game thread. */
    @Test
    public void anEmptyNumericRangeIsAnsweredNotThrown() {
        Game g = bareGame(PhaseType.MAIN1);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new LineFindingProbeTest.Recorder("CHOICE: 1"), "llm");
        String log = logOf(() -> {
            Assert.assertEquals(ctrl.chooseNumber(null, "How many?", 3, 2), 3);
            Assert.assertEquals(ctrl.chooseNumber(null, "How many?", new java.util.ArrayList<>(), null), 0);
        });
        Assert.assertTrue(log.contains("number(empty range 3..2)->3") && log.contains("number(no options)->0"), log);
    }

    /** P29 (driven by the card's own script through the engine's TwoPilesEffect, Q13): Fact or Fiction has no FaceDown
     *  parameter, so both piles are face up, as the human controller shows them. The seat was shown "N face-down
     *  cards" for each pile. */
    @Test
    public void factOrFictionShowsTheSeatBothPiles() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        String[] top = {"Lightning Bolt", "Grizzly Bears", "Counterspell", "Brainstorm", "Ponder"};
        for (String n : top) card(n, me, ZoneType.Library);
        Card fof = card("Fact or Fiction", me, ZoneType.Hand);
        GoldenChecksTest.Script model = new GoldenChecksTest.Script("CHOICE: 1");
        seat(g, model, "llm");
        SpellAbility sa = fof.getFirstSpellAbility();
        sa.setActivatingPlayer(me);
        forge.game.ability.AbilityUtils.resolve(sa);
        String pile = model.prompts.stream().filter(p -> p.contains("Choose a pile")).findFirst().orElse(null);
        Assert.assertNotNull(pile, "the pile was asked: " + model.prompts);
        String decision = pile.substring(pile.lastIndexOf("Choose a pile"));
        Assert.assertFalse(decision.contains("face-down"), decision);
        for (String n : top) Assert.assertTrue(decision.contains(n), n + " is shown: " + decision);
    }

    /** P29: with FaceDown$ One pile 1 is hidden and pile 2 shown; with True both are hidden. */
    @Test
    public void aFaceDownPileIsShownByCountOnly() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card bolt = card("Lightning Bolt", me, ZoneType.Library), bears = card("Grizzly Bears", me, ZoneType.Library);
        GoldenChecksTest.Script model = new GoldenChecksTest.Script("CHOICE: 1");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, "llm");
        SpellAbility sa = card("Fact or Fiction", me, ZoneType.Hand).getFirstSpellAbility();
        ctrl.chooseCardsPile(sa, new forge.game.card.CardCollection(bolt), new forge.game.card.CardCollection(bears), "One");
        ctrl.chooseCardsPile(sa, new forge.game.card.CardCollection(bolt), new forge.game.card.CardCollection(bears), "True");
        String one = model.prompts.get(0), both = model.prompts.get(1);
        one = one.substring(one.lastIndexOf("Choose a pile"));
        both = both.substring(both.lastIndexOf("Choose a pile"));
        Assert.assertTrue(one.contains("Pile 1: 1 face-down card") && one.contains("Grizzly Bears") && !one.contains("Lightning Bolt"), one);
        Assert.assertTrue(both.contains("Pile 1: 1 face-down card") && both.contains("Pile 2: 1 face-down card"), both);
    }

    /** C21: an either-or question (Discover: cast it or put it into your hand) was labelled YES / NO; the engine's two
     *  captions are the options, and a lost answer names the one it took. */
    @Test
    public void anEitherOrConfirmOffersTheEnginesTwoChoices() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card found = card("Lightning Bolt", me, ZoneType.Exile);
        SpellAbility sa = card("Geological Appraiser", me, ZoneType.Battlefield).getFirstSpellAbility();
        GoldenChecksTest.Script model = new GoldenChecksTest.Script("no idea").on("Choose one", GoldenChecksTest.choice("Cast"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, "llm");
        String q = "Do you want to cast Lightning Bolt without paying its mana cost or put it into your hand?";
        Assert.assertTrue(ctrl.confirmAction(sa, null, q, java.util.Arrays.asList("Cast", "Hand"), found, null));
        String asked = model.prompts.get(0);
        asked = asked.substring(asked.lastIndexOf("DECISION"));
        Assert.assertTrue(asked.contains("Cast") && asked.contains("Hand") && !asked.contains("YES"), asked);
        GoldenChecksTest.Script lost = new GoldenChecksTest.Script("no idea");
        LlmSpikeGui.PlayerControllerLLM ctrl2 = seat(g, lost, "llm");
        String log = logOf(() -> Assert.assertFalse(ctrl2.confirmAction(sa, null, q, java.util.Arrays.asList("Cast", "Hand"), found, null)));
        Assert.assertTrue(log.contains("without paying its mana cost or put it into your hand?): Hand"), log);
    }

    /** A15: an optional pick that got no answer was declined, and its row said "->first". The row says what was done. */
    @Test
    public void anOptionalPickThatGotNoAnswerSaysItWasDeclined() throws Exception {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card a = card("Grizzly Bears", me, ZoneType.Battlefield), b = card("Savannah Lions", me, ZoneType.Battlefield);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new LineFindingProbeTest.Recorder("CHOICE: 1"), "llm");
        spend(ctrl);
        FCollection<Card> opts = new FCollection<>();
        opts.add(a);
        opts.add(b);
        Card[] got = new Card[1];
        String log = logOf(() -> got[0] = ctrl.chooseSingleEntityForEffect(opts, null, null, "Choose a creature", true, null, null));
        Assert.assertNull(got[0], "declined");
        Assert.assertTrue(log.contains("entity(no-budget)->decline"), log);
        log = logOf(() -> got[0] = ctrl.chooseSingleEntityForEffect(opts, null, null, "Choose a creature", false, null, null));
        Assert.assertEquals(got[0], a, "a mandatory pick takes the first option");
        Assert.assertTrue(log.contains("entity(no-budget)->first"), log);
    }
}
