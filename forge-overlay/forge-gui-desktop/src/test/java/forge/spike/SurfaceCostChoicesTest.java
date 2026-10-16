package forge.spike;

import java.util.ArrayList;
import java.util.List;

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
 * The cost-side keyword surfaces (docs/harness/GOLDEN-CHECKS.md, S3): an optional keyword cost of a
 * real cast reaches the seat through the engine's addExtraKeywordCost, the seat's answer is the one
 * used, and every prompt of every test here keeps the ask contract (AskContract). addKeywordCost /
 * chooseNumberForKeywordCost ask the "keyword-cost" type; the sacrifice shape the Casualty payment
 * adds asks "sacrifice" through LlmCostDecision as Goblin Bombardment does.
 */
public class SurfaceCostChoicesTest {
    private final List<String> asked = java.util.Collections.synchronizedList(new ArrayList<>());

    @BeforeClass
    public void init() { new LineFindingProbeTest().initializeModel(); }

    @BeforeMethod
    public void faithful() {
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL;
        asked.clear();
        LlmSpikeGui.PlayerControllerLLM.TEST_ASK_TAP = asked::add;
    }

    @AfterMethod(alwaysRun = true)
    public void everyAskKeepsTheContract(ITestResult result) {
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null;
        LlmSpikeGui.PlayerControllerLLM.TEST_ASK_TAP = null;
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
                    Assert.assertTrue(p.contains("Every integer from 0 through 2147483647 inclusive"),
                            "the ask states the engine's own legal range: " + p);
                    return "NUMBER: 1";
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

    private static List<String> casualtyAsks(Script model) {
        List<String> out = new ArrayList<>();
        for (String q : model.prompts) if (q.contains("Pay for Casualty")) out.add(q);
        return out;
    }

    private static String decisions(Script model) {
        return PromptFidelityTest.decisions(model);
    }
}
