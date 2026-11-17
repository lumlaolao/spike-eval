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
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.zone.ZoneType;

/** Each standing fact is said once, in the system prompt or the guide, not in every message (critique batch 3). */
public class AskNoiseTest {

    @BeforeClass
    public void init() { new LineFindingProbeTest().initializeModel(); }

    @BeforeMethod
    public void faithful() { LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL; }

    @AfterMethod(alwaysRun = true)
    public void legacy() { LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null; }

    /** P7: every ask ended "Call `set_plan` too if your plan is new or has changed. Lookups do not count against the
     *  answer.", a starting-player ask and a mulligan included. */
    @Test
    public void theAnswerLineIsOneSentence() {
        Assert.assertEquals(AnswerTool.instruction(AnswerTool.Spec.one(3), true),
                "ANSWER: call `answer` with `option`: one number from the list.\n");
    }

    /** M3, P8: the plan line carried a paragraph on what a plan is in every message, with the text grammar's PLAN: line
     *  on the tool lane, and "an earlier turn" for a plan 24 turns old. */
    @Test
    public void thePlanLineSaysItsAgeAndNothingElse() {
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.revisablePlanText("GOAL: win NEXT: attack", "T1 MAIN1", 1, 25),
                "YOUR PLAN (stated at T1 MAIN1, 24 turns ago): GOAL: win NEXT: attack\n");
        Assert.assertTrue(LlmSpikeGui.PlayerControllerLLM.revisablePlanText("x", "T3 MAIN1", 3, 3).contains("this turn"));
        Assert.assertTrue(SeatObservation.guide().contains("check a note against the state, and replace it when it no longer fits"));
    }

    /** P8: the mana caveat and NOT VISIBLE's two fixed sentences were in every state; they are the guide's. */
    @Test
    public void theManaLineAndNotVisibleCarryNoFixedCaveat() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Plateau", me, ZoneType.Battlefield);
        Assert.assertFalse(SeatObservation.manaLine(me).contains("each counts its mana"), SeatObservation.manaLine(me));
        Assert.assertTrue(SeatObservation.guide().contains("Continuous effects are applied to the shown P/T"));
    }

    /** P6: the first smart play ask of every turn asked for a plan, even with one held. */
    @Test
    public void aSeatWithAPlanIsNotAskedForAnother() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        card("Lightning Bolt", me, ZoneType.Hand);
        GoldenChecksTest.Script model = new GoldenChecksTest.Script("CHOICE: 99");
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(g, me,
                new LobbyPlayerAi("Seat", null), "Seat", "", model, model, "llm");
        me.dangerouslySetController(ctrl);
        ctrl.chooseSpellAbilityToPlay();
        Assert.assertTrue(model.prompts.get(0).contains("state your plan"), "no plan yet: asked");
        ctrl.adoptPlanText("GOAL: burn them out NEXT: bolt their face");
        g.getPhaseHandler().devModeSet(PhaseType.MAIN1, me, 2);
        model.prompts.clear();
        ctrl.chooseSpellAbilityToPlay();
        Assert.assertFalse(model.prompts.get(0).contains("state your plan"), "a plan held: not asked again");
    }

    /** E9: a model seat started with no CONTEXT_PROFILE fell back to v1-compat, the legacy profile. */
    @Test
    public void aModelSeatNamesItsProfile() {
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null;
        if (System.getenv("CONTEXT_PROFILE") != null) return;   // the refusal reads the environment
        Assert.assertThrows(IllegalStateException.class,
                () -> LlmSpikeGui.PlayerControllerLLM.requireProfileForModel("openrouter:x/y", "Seat/smart"));
        LlmSpikeGui.PlayerControllerLLM.requireProfileForModel("script:line.txt", "Seat/smart");   // an arm: exempt
    }
}
