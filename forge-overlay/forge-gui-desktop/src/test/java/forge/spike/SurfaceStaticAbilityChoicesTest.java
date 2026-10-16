package forge.spike;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;

import org.testng.Assert;
import org.testng.ITestResult;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import forge.game.Game;
import forge.game.card.Card;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.spellability.SpellAbility;
import forge.game.zone.ZoneType;

import static forge.spike.GoldenChecksTest.Script;
import static forge.spike.GoldenChecksTest.choice;
import static forge.spike.LineFindingProbeTest.bareGame;
import static forge.spike.LineFindingProbeTest.card;
import static forge.spike.LineFindingProbeTest.seat;

/**
 * The last S3 surface (docs/harness/GOLDEN-CHECKS.md queue, S3-13): the engine asks the seat which
 * cost reduction applies when MORE THAN ONE hits one cast (CostAdjustment.java:230, the
 * reduceAbilities loop, engine file forge-game/src/main/java/forge/game/cost/CostAdjustment.java).
 * The fixture is the real pair from the queue brief: Foundry Inspector
 * (forge-gui/res/cardsfolder/f/foundry_inspector.txt) and Cloud Key
 * (forge-gui/res/cardsfolder/c/cloud_key.txt) on one battlefield and an artifact creature cast
 * through the play loop — both its S-mode ReduceCost statics land in reduceAbilities for the one
 * cast, and the seat picks which applies first. Every prompt of every test here keeps the ask
 * contract (AskContract). Two controller fixes this wave rides on (chooseSingleStaticAbility in
 * LlmSpikeGui.java): the ask's own snapshot priced hand cards through the same CostAdjustment
 * adjust and recursed into a StackOverflowError (PRICING_FOR_SNAPSHOT guard), and the loop's last
 * single legal candidate recorded an auto row instead of the silent pick the engine's human
 * controller takes (see the gate: only `asked` rows traverse the hook).
 */
public class SurfaceStaticAbilityChoicesTest {

    /** The ask types this wave puts on the seat's surface; the gate fails until the model has
     *  answered each through the live controller. */
    static final Set<String> HOOKS = new TreeSet<>(Arrays.asList("static-order"));

    /** Types a model answered in this class (tier smart or fast), across every test. */
    static final Set<String> ANSWERED = Collections.synchronizedSet(new TreeSet<>());

    private final List<String> asked = Collections.synchronizedList(new ArrayList<>());

    @BeforeClass
    public void init() { new LineFindingProbeTest().initializeModel(); }

    @BeforeMethod
    public void faithful() {
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL;
        asked.clear();
        LlmSpikeGui.PlayerControllerLLM.TEST_ASK_TAP = asked::add;
        // This class owns its types the way the golden class owns its own (the gate below): taps land
        // in this class's set only. GoldenChecksTest.ANSWERED stays untouched — the single acceptance
        // JVM runs the classes alphabetically, so golden's gate always runs BEFORE these tests and its
        // OWNED list cannot name types answered here (measured 2026-10-16: priorities do not reorder
        // across classes); golden's everyOwnedDecisionTypeIsExercisedHere guards its own class only.
        LlmSpikeGui.PlayerControllerLLM.TEST_DECISION_TAP = (type, tier) -> {
            if ("smart".equals(tier) || "fast".equals(tier)) ANSWERED.add(type);
        };
    }

    @AfterMethod(alwaysRun = true)
    public void everyAskKeepsTheContract(ITestResult result) {
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null;
        LlmSpikeGui.PlayerControllerLLM.TEST_ASK_TAP = null;
        LlmSpikeGui.PlayerControllerLLM.TEST_DECISION_TAP = null;
        if (!result.isSuccess()) return;
        AskContract.check(result.getName(), asked);
    }

    /** One cast benefits from two reductions at once: Foundry Inspector ("Artifact spells you cast
     *  cost {1} less") and Cloud Key holding the named type "Artifact" — the engine's reduction loop
     *  asks the seat which applies, both candidates are in the menu, and the applied cost shows both
     *  {1}s at work ({4} Juggernaut leaves {2} to pay). */
    @Test
    public void twoCostReductionsOnOneCastArePutToTheSeat() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        for (int i = 0; i < 6; i++) card("Island", me, ZoneType.Battlefield);
        card("Foundry Inspector", me, ZoneType.Battlefield);
        card("Cloud Key", me, ZoneType.Battlefield).setChosenType("Artifact");   // the named type, as the ETB would prompt it
        Card jugg = card("Juggernaut", me, ZoneType.Hand);                      // {4} artifact creature
        Script model = new Script("CHOICE: 1")
                .on("You have priority", choice("CAST Juggernaut"))
                .on("Several static abilities apply", choice("Cloud Key"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, "the seat cast Juggernaut");
        ctrl.playChosenSpellAbility(r.get(0));

        List<String> picksAsks = staticAsks(model);
        Assert.assertEquals(picksAsks.size(), 2,
                "the payment prices the reductions once building the payment and once paying: " + decisions(model));
        for (String ask : picksAsks) {
            Assert.assertTrue(ask.contains("1) Foundry Inspector: Artifact spells you cast cost {1} less to cast."),
                    "the Inspector reduction was one of the menu candidates: " + ask);
            Assert.assertTrue(ask.contains("2) Cloud Key: Spells you cast of the chosen type cost {1} less to cast."),
                    "the Cloud Key reduction was the other candidate: " + ask);
        }        long tapped = me.getCardsIn(ZoneType.Battlefield).stream()
                .filter(c -> c.getName().equals("Island") && c.isTapped()).count();
        Assert.assertEquals(tapped, 2L, "{4} minus both {1} reductions is {2}: " + decisions(model));
        Assert.assertTrue(jugg.isInZone(ZoneType.Stack), "the reduced cast went on the stack: " + jugg.getZone());
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** One reduction alone is no decision: the engine's human controller takes the single candidate
     *  without prompting, so the seat does too — silently, never Forge's AI, and the {1} still came
     *  off the cast ({4} leaves {3}). */
    @Test
    public void aSingleCostReductionIsAppliedWithoutAskingTheModel() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        for (int i = 0; i < 6; i++) card("Island", me, ZoneType.Battlefield);
        card("Foundry Inspector", me, ZoneType.Battlefield);
        Card jugg = card("Juggernaut", me, ZoneType.Hand);                      // {4} artifact creature
        Script model = new Script("CHOICE: 1").on("You have priority", choice("CAST Juggernaut"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, "the seat cast Juggernaut");
        ctrl.playChosenSpellAbility(r.get(0));

        Assert.assertTrue(model.prompts.stream().noneMatch(q -> q.contains("Several static abilities apply")),
                "one candidate is not a question: " + decisions(model));
        long tapped = me.getCardsIn(ZoneType.Battlefield).stream()
                .filter(c -> c.getName().equals("Island") && c.isTapped()).count();
        Assert.assertEquals(tapped, 3L, "{4} minus the Inspector's {1} is {3}: " + decisions(model));
        Assert.assertTrue(jugg.isInZone(ZoneType.Stack), "the reduced cast went on the stack: " + jugg.getZone());
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    // ------------------------------------------------------------------ the gate

    /** Runs last: a model has answered every surface hook this class owns through the live controller. */
    @Test(priority = 1000)
    public void theSurfaceHooksAreAskedAndAnsweredByTheModel() {
        Set<String> missing = new TreeSet<>(HOOKS);
        missing.removeAll(ANSWERED);
        Assert.assertTrue(missing.isEmpty(),
                "surface hooks no test here drives through the live controller: " + missing);
    }

    // ------------------------------------------------------------------ fixtures

    private static List<String> staticAsks(Script model) {
        List<String> out = new ArrayList<>();
        for (String q : model.prompts) if (q.contains("Several static abilities apply")) out.add(q);
        return out;
    }

    private static String decisions(Script model) {
        return PromptFidelityTest.decisions(model);
    }
}
