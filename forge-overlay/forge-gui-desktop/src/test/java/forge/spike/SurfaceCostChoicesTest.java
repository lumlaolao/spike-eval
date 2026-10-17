package forge.spike;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;

import forge.game.Game;
import forge.game.card.Card;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.spellability.SpellAbility;
import forge.game.zone.ZoneType;

import org.testng.Assert;
import org.testng.ITestResult;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import static forge.spike.GoldenChecksTest.Script;
import static forge.spike.GoldenChecksTest.choice;
import static forge.spike.GoldenChecksTest.picks;
import static forge.spike.LineFindingProbeTest.bareGame;
import static forge.spike.LineFindingProbeTest.card;
import static forge.spike.LineFindingProbeTest.seat;

/**
 * The cost-side keyword surfaces (REVIEW-2026-10-16.md row S3; the 13-hook queue lived in
 * eval/surface_gaps.txt, now pruned to zero): an optional keyword cost of a real cast reaches
 * the seat through the engine's addExtraKeywordCost, the seat's answer is the one used, and
 * every prompt of every test here keeps the ask contract (AskContract). addKeywordCost /
 * chooseNumberForKeywordCost ask the "keyword-cost" type; the sacrifice shape the Casualty
 * payment adds asks "sacrifice" through LlmCostDecision as Goblin Bombardment does.
 * The wide NUMBER ask (LlmSpikeGui chooseWideKeywordCost) is the repair this class repros:
 * a reverted repair re-enumerates and the JVM dies filling the menu (pr-review 2026-10-16).
 */
public class SurfaceCostChoicesTest {

    /** The ask types this wave puts on the seat's surface; the gate below fails until the model
     *  has answered each through the live controller. */
    static final Set<String> HOOKS = new TreeSet<>(Arrays.asList("keyword-cost", "sacrifice"));

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
        // This class owns its types the way the effects family does (the gate below): taps land
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

    /** Rooftop Nuisance through the play loop: the seat answers "Pay" at the Casualty ask, picks the
     *  casualty creature from the sacrifice the payment then asks for, and the card's own wording —
     *  "copy this spell and you may choose new targets for the copy" — resolves the spell twice. */
    @Test
    public void payingCasualtyIsTheSeatsAndTheSpellResolvesTwice() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        for (int i = 0; i < 4; i++) card("Plains", me, ZoneType.Library);   // the draws must draw cards
        for (int i = 0; i < 3; i++) card("Island", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Savannah Lions", me, ZoneType.Battlefield);          // a second casualty-eligible creature: a real pick
        Card kami = card("Lantern Kami", rival, ZoneType.Battlefield);
        card("Rooftop Nuisance", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1")
                .on("You have priority", choice("CAST Rooftop Nuisance"))
                .on("Choose the TARGET", choice("Lantern Kami"))
                .on("Pay for Casualty", choice("1"))
                .on("must SACRIFICE", picks("Grizzly Bears"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, "Rooftop Nuisance was offered");
        ctrl.playChosenSpellAbility(r.get(0));
        List<String> pays = casualtyAsks(model);
        Assert.assertEquals(pays.size(), 1, "the Casualty ask came once: " + decisions(model));
        Assert.assertTrue(pays.get(0).contains("[Rooftop Nuisance: Casualty 1"),
                "the ask was Nuisance's Casualty 1, with the card's rules text: " + pays.get(0));
        g.getTriggerHandler().runWaitingTriggers();
        g.getStack().addAllTriggeredAbilitiesToStack();
        while (!g.getStack().isEmpty()) {
            g.getStack().resolveStack();
            g.getStack().addAllTriggeredAbilitiesToStack();
        }
        Assert.assertTrue(model.prompts.stream().anyMatch(q -> q.contains("Choose the TARGET for a copy of Rooftop Nuisance")),
                "the copy's new targets were the seat's: " + decisions(model));
        Assert.assertTrue(bears.isInZone(ZoneType.Graveyard), "the creature the seat named was sacrificed: " + bears.getZone());
        Assert.assertTrue(kami.isTapped(), "both resolutions tapped the target");
        Assert.assertTrue(me.getCardsIn(ZoneType.Graveyard).stream().anyMatch(c -> c.getName().equals("Rooftop Nuisance")),
                "the spell resolved");
        Assert.assertEquals(me.getCardsIn(ZoneType.Hand).size(), 2, "two draws: the original and the casualty copy");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** The decline direction: "0" is no payment, nothing is sacrificed, and the spell resolves once,
     *  alone — no copy, no second target ask. */
    @Test
    public void decliningCasualtyResolvesTheSpellOnceWithoutSacrificing() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        for (int i = 0; i < 4; i++) card("Plains", me, ZoneType.Library);   // the draw must draw a card
        for (int i = 0; i < 3; i++) card("Island", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Savannah Lions", me, ZoneType.Battlefield);
        Card kami = card("Lantern Kami", rival, ZoneType.Battlefield);
        card("Rooftop Nuisance", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1")
                .on("You have priority", choice("CAST Rooftop Nuisance"))
                .on("Choose the TARGET", choice("Lantern Kami"))
                .on("Pay for Casualty", choice("0"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r);
        ctrl.playChosenSpellAbility(r.get(0));
        Assert.assertEquals(casualtyAsks(model).size(), 1, "the Casualty ask came once: " + decisions(model));
        g.getTriggerHandler().runWaitingTriggers();
        g.getStack().addAllTriggeredAbilitiesToStack();
        while (!g.getStack().isEmpty()) {
            g.getStack().resolveStack();
            g.getStack().addAllTriggeredAbilitiesToStack();
        }
        Assert.assertTrue(model.prompts.stream().noneMatch(q -> q.contains("must SACRIFICE")),
                "declining, there was no cost to pay and no cost ask: " + decisions(model));
        Assert.assertTrue(model.prompts.stream().noneMatch(q -> q.contains("Choose the TARGET for a copy of")),
                "no copy was made: " + decisions(model));
        Assert.assertTrue(bears.isInZone(ZoneType.Battlefield) && bears.isInPlay(), "nothing was sacrificed");
        Assert.assertTrue(kami.isTapped(), "the spell still resolved once");
        Assert.assertEquals(me.getCardsIn(ZoneType.Hand).size(), 1, "one draw, not two");
        Assert.assertEquals(me.getCardsIn(ZoneType.Graveyard).stream().filter(c -> c.getName().equals("Rooftop Nuisance")).count(),
                1L, "the spell went where a resolved spell goes");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** Spell Contortion (Multikicker {1}{U}, res/cardsfolder/s/spell_contortion.txt): the seat says "1" at
     *  the keyword-cost ask, the {1}{U} is added, so five Islands pay {2}{U} plus one kick and the spell
     *  that says "Draw a card for each time Spell Contortion was kicked" draws its card. */
    @Test
    public void oneMultikickerIsPaidAndDrawsItsCard() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        for (int i = 0; i < 4; i++) card("Plains", me, ZoneType.Library);   // the kick's draw must draw a card
        for (int i = 0; i < 8; i++) card("Island", me, ZoneType.Battlefield);
        LineFindingProbeTest.rivalCasts(g, "Ponder", null);
        card("Spell Contortion", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1")
                .on("You have priority", choice("CAST Spell Contortion"))
                .on("Choose the TARGET", choice("Ponder"))
                .on("Multikicker", p -> {
                    // The engine's range is any number (Integer.MAX_VALUE); eight Islands pay {2}{U} and two {1}{U} kicks.
                    Assert.assertTrue(p.contains("3) 2") && !p.contains("4) 3"), "only the payable kicks are offered: " + p);
                    return choice("1").apply(p);
                });
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, "Spell Contortion was offered");
        ctrl.playChosenSpellAbility(r.get(0));
        while (!g.getStack().isEmpty()) g.getStack().resolveStack();

        long tappedIslands = me.getCardsIn(ZoneType.Battlefield).stream()
                .filter(c -> c.getName().equals("Island") && c.isTapped()).count();
        Assert.assertEquals(tappedIslands, 5L, "{2}{U} is 3 and the {1}{U} kick is 2: five Islands: " + decisions(model));
        Assert.assertEquals(me.getCardsIn(ZoneType.Hand).size(), 1, "the kick draws its card: " + decisions(model));
        Assert.assertTrue(rival.getCardsIn(ZoneType.Graveyard).stream().anyMatch(c -> c.getName().equals("Ponder")),
                "the spell was countered");
        Assert.assertTrue(g.getStack().isEmpty());
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

    private static List<String> casualtyAsks(Script model) {
        List<String> out = new ArrayList<>();
        for (String q : model.prompts) if (q.contains("Pay for Casualty")) out.add(q);
        return out;
    }

    private static String decisions(Script model) {
        return PromptFidelityTest.decisions(model);
    }
}
