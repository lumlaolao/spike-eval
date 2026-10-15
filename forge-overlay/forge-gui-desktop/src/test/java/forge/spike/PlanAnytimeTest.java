package forge.spike;

import java.util.ArrayList;
import java.util.List;

import forge.ai.LobbyPlayerAi;
import forge.game.Game;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.zone.ZoneType;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import static forge.spike.LineFindingProbeTest.bareGame;
import static forge.spike.LineFindingProbeTest.card;

/** The seat's plan is its own and can be replaced at any decision (observation v22). */
public class PlanAnytimeTest {
    @BeforeClass
    public void init() { new LineFindingProbeTest().initializeModel(); }

    @BeforeMethod
    public void faithful() { LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL; }

    @AfterMethod(alwaysRun = true)
    public void legacy() { LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null; }

    @Test
    public void aPlanIsALineThatStartsWithPlan() {
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.planStatement("think\nPLAN: GOAL: race NEXT: bolt face\nCHOICE: 2"),
                "GOAL: race NEXT: bolt face");
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.planStatement("**PLAN:** GOAL: hold up Force CHOICE: 3"),
                "GOAL: hold up Force");
        Assert.assertNull(LlmSpikeGui.PlayerControllerLLM.planStatement("My game plan: race them.\nCHOICE: 1"),
                "a line mentioning a plan is not a plan statement");
        Assert.assertNull(LlmSpikeGui.PlayerControllerLLM.planStatement("PLAN:\nCHOICE: 1"));
    }

    /** Answers by question: the play with one plan, the target with a changed one. */
    static final class Scripted implements LLMBridge {
        final List<String> prompts = new ArrayList<>();
        @Override public String ask(String prompt) {
            prompts.add(prompt);
            if (prompt.contains("DECISION: Choose the TARGET"))
                return "They have a Lions; the race is off.\nPLAN: GOAL: kill the Lions first NEXT: then attack\nCHOICE: 1";
            return "PLAN: GOAL: burn them out NEXT: Bolt to the face\nCHOICE: 1";
        }
        @Override public String getLabel() { return "scripted"; }
        @Override public String getModel() { return "fake"; }
        @Override public long getTotalMs() { return 0; }
        @Override public int getCalls() { return prompts.size(); }
    }

    /** A PLAN line written while answering a target question replaces the plan stated at the play,
     *  and the next question shows the new plan, when it was stated, and that it can change again. */
    @Test
    public void aPlanChangedAtATargetQuestionIsTheNextPromptsPlan() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Mountain", me, ZoneType.Battlefield);
        card("Mountain", me, ZoneType.Battlefield);
        card("Lightning Bolt", me, ZoneType.Hand);
        card("Lightning Bolt", me, ZoneType.Hand);
        card("Savannah Lions", rival, ZoneType.Battlefield);
        Scripted b = new Scripted();
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(g, me,
                new LobbyPlayerAi("Seat", null), "Seat", "", b, b, "llm");
        me.dangerouslySetController(ctrl);
        ctrl.chooseSpellAbilityToPlay();
        Assert.assertTrue(b.prompts.get(0).contains("PLAN: GOAL:"), "the first play asks for a goal and steps");
        int asked = b.prompts.size();
        ctrl.chooseSpellAbilityToPlay();
        Assert.assertTrue(b.prompts.size() > asked, "asked again");
        String next = b.prompts.get(asked);
        Assert.assertTrue(next.contains("YOUR PLAN (yours, stated at T" + g.getPhaseHandler().getTurn() + " MAIN1, this turn"), next);
        Assert.assertTrue(next.contains("): GOAL: kill the Lions first NEXT: then attack\n"), next);
        Assert.assertFalse(next.contains("burn them out"), "the old plan is gone");
        Assert.assertFalse(next.contains("First state your plan"), "a plan stated this turn is not asked for again");
    }
}
