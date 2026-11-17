package forge.spike;

import static forge.spike.LineFindingProbeTest.bareGame;
import static forge.spike.LineFindingProbeTest.card;

import java.util.ArrayList;
import java.util.List;

import org.testng.Assert;
import org.testng.annotations.AfterClass;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import forge.ai.LobbyPlayerAi;
import forge.game.Game;
import forge.game.card.CardCollection;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.zone.ZoneType;

/**
 * What the faithful asks state is true and is not advice (critique batch 2: P1, P2, P4, P5, P10, P12, P15, C4). Each
 * test reads the prompt the live controller sent.
 */
public class AskTruthTest {

    @BeforeClass
    public void quickRetries() { QuickRetries.on(); }

    @AfterClass(alwaysRun = true)
    public void realRetries() { QuickRetries.off(); }

    @BeforeClass
    public void init() { new LineFindingProbeTest().initializeModel(); }

    private final List<String> asked = new ArrayList<>();

    @BeforeMethod
    public void faithful() {
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL;
        asked.clear();
        LlmSpikeGui.PlayerControllerLLM.TEST_ASK_TAP = asked::add;
    }

    @AfterMethod(alwaysRun = true)
    public void legacy() {
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null;
        LlmSpikeGui.PlayerControllerLLM.TEST_ASK_TAP = null;
        AskContract.check("AskTruthTest", asked);
    }

    private static LlmSpikeGui.PlayerControllerLLM seat(Game g, LineFindingProbeTest.Recorder model) {
        Player me = g.getPlayers().get(0);
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(g, me,
                new LobbyPlayerAi("Seat", null), "Seat", "", model, model, "llm");
        me.dangerouslySetController(ctrl);
        return ctrl;
    }

    private static String decision(String prompt) { return prompt.substring(prompt.lastIndexOf("DECISION:")); }

    /** P1, P2, P15: the mulligan said "you cannot go below 5" (a floor only MULLIGAN_HEURISTIC=1 has) and described a
     *  shrinking hand; the engine runs the London mulligan: 7 new cards, N to the bottom, one more each time. Names are
     *  joined with "; " (card names hold commas). */
    @Test
    public void theMulliganStatesTheLondonRuleAndNoFloor() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Ajani, Nacatl Pariah", me, ZoneType.Hand);
        for (int i = 0; i < 6; i++) card("Plains", me, ZoneType.Hand);
        GoldenChecksTest.Script model = new GoldenChecksTest.Script("MULLIGAN");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model);
        Assert.assertFalse(ctrl.mulliganKeepHand(me, 0), "the answer is taken");
        String first = decision(model.prompts.get(0));
        Assert.assertFalse(first.contains("below 5"), first);
        Assert.assertTrue(first.contains("you draw 7 new cards, and you put 1 of them of your choice on the bottom"), first);
        Assert.assertTrue(first.contains("Ajani, Nacatl Pariah; Plains"), first);
        ctrl.mulliganKeepHand(me, 1);
        Assert.assertTrue(decision(model.prompts.get(model.prompts.size() - 1)).contains("you put 2 of them"),
                "one more card each mulligan");
    }

    /** P4: every attack ask opened with "LETHAL:" whatever the board; it states what wins instead. */
    @Test
    public void theAttackAskStatesWhatWinsWithNoLethalLabel() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        Player me = g.getPlayers().get(0);
        card("Grizzly Bears", me, ZoneType.Battlefield).setSickness(false);
        GoldenChecksTest.Script model = new GoldenChecksTest.Script("PICKS: none");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model);
        ctrl.declareAttackers(me, new forge.game.combat.Combat(me));
        String d = decision(model.prompts.get(0));
        Assert.assertFalse(d.contains("LETHAL"), d);
        Assert.assertTrue(d.contains("To win by damage: 20 (or 10 poison)."), d);
    }

    /** P5: PASS was "(do nothing)" in every window; it says what passing does here, and the system prompt says how the
     *  steps run. */
    @Test
    public void passSaysWhatItDoesAndTheSystemPromptGivesTheSteps() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        card("Lightning Bolt", me, ZoneType.Hand);
        GoldenChecksTest.Script model = new GoldenChecksTest.Script("CHOICE: 99");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model);
        ctrl.chooseSpellAbilityToPlay();
        Assert.assertTrue(decision(model.prompts.get(0)).contains("PASS (do nothing now: the game moves on to the next step)"),
                decision(model.prompts.get(0)));
        String system = LlmSpikeGui.PlayerControllerLLM.seatFraming("Seat", "", true, false, true);
        Assert.assertTrue(system.contains(LlmSpikeGui.PlayerControllerLLM.PRIORITY_RULES), "the step order is in the system prompt");
        Assert.assertTrue(system.contains("to attack, you pass in your first main phase"), system);
    }

    /** P10: the preamble said "may call again once" and nothing about what an unanswered decision becomes. */
    @Test
    public void thePreambleSaysWhatAnUnansweredDecisionBecomes() {
        String s = AnswerTool.instructions();
        Assert.assertTrue(s.contains("a reply that calls nothing is reminded twice"), s);
        Assert.assertTrue(s.contains("takes its do-nothing option"), s);
    }

    /** C4: a yield was described as "anything they cast or activate always asks you", read as "anything they do"; an
     *  attack does not wake it. One text for the schema and the hint (P16). */
    @Test
    public void theYieldSaysAnAttackDoesNotWakeIt() {
        Assert.assertTrue(AnswerTool.YIELD_RULE.contains("an attack does not"), AnswerTool.YIELD_RULE);
        Assert.assertTrue(LlmSpikeGui.PlayerControllerLLM.YIELD_TOOL_HINT.contains(AnswerTool.YIELD_RULE));
        Assert.assertTrue(new AnswerTool.Ask(AnswerTool.Spec.one(2), false).definitions().toString()
                .contains("an attack does not, though you are still asked how to block"));
    }

    /** P12: an ordered move that is not "to the top of a library" (a graveyard that cares, the library under) said "(first = goes in/on top first)", read both ways; it says the last ends on top. */
    @Test
    public void anOrderedMoveSaysWhichCardEndsOnTop() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        CardCollection two = new CardCollection();
        two.add(card("Lightning Bolt", me, ZoneType.Hand));
        two.add(card("Grizzly Bears", me, ZoneType.Hand));
        GoldenChecksTest.Script model = new GoldenChecksTest.Script("PICKS: 1,2");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model);
        ctrl.orderMoveToZoneList(two, ZoneType.Library, null);   // not to the top: the order the cards go in
        Assert.assertTrue(decision(model.prompts.get(0)).contains("the last card you list ends on top"), model.prompts.get(0));
    }
}
