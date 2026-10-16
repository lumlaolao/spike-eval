package forge.spike;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import forge.card.CardStateName;
import forge.game.Game;
import forge.game.card.Card;
import forge.game.combat.Combat;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.spellability.SpellAbility;
import forge.game.spellability.SpellAbilityStackInstance;
import forge.game.zone.ZoneType;

import org.testng.Assert;
import org.testng.ITestResult;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import static forge.spike.GoldenChecksTest.choice;
import static forge.spike.GoldenChecksTest.picks;
import static forge.spike.LineFindingProbeTest.bareGame;
import static forge.spike.LineFindingProbeTest.card;
import static forge.spike.LineFindingProbeTest.seat;

/**
 * The last four surface-effect hooks (docs/harness/controller-ownership.tsv ledger, wave/effects):
 * a reveal-and-pick across several categories (Gift of the Gargantuan), a villainous choice whose
 * chooser is the targeted creature's owner (This Is How It Ends), an unlock-a-door half pick with
 * both halves locked (Ghostly Keybearer), and a Spellskite redirect over a two-target spell. Each
 * is a decision Forge's AI used to make outright; here the live controller asks the model, the
 * named answer is the one the engine applies, and the ask contract runs on every prompt.
 */
public class SurfaceEffectChoicesTest {

    /** The ask types this wave adds to the seat's surface; the gate below fails until the model
     *  has answered each through the live controller. */
    static final Set<String> HOOKS = new TreeSet<>(Arrays.asList(
            "cards-for-effect", "spell-for-effect", "card-state", "redirect"));

    /** Types a model answered in this class (tier smart or fast), across every test. */
    static final Set<String> ANSWERED = Collections.synchronizedSet(new TreeSet<>());

    private final List<String> asked = Collections.synchronizedList(new ArrayList<>());
    /** The source every ask of the current test must name with its rules text, when it has one. */
    private String sourceName, sourceText;

    @BeforeClass
    public void init() { new LineFindingProbeTest().initializeModel(); }

    @BeforeMethod
    public void faithful() {
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL;
        asked.clear();
        sourceName = null; sourceText = null;
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
        if (sourceName != null) AskContract.namesSource(result.getName(), asked, sourceName, sourceText);
    }

    // ------------------------------------------------------------------ helpers

    /** The menu number of the option whose text contains {@code needle} (the ask types below label
     *  their options mid-line: "<host> — its target <card> ...", not by a stable prefix). */
    static int optContaining(String prompt, String needle) {
        String decision = prompt.substring(Math.max(0, prompt.lastIndexOf("DECISION:")));
        Matcher m = Pattern.compile("(?m)^\\s*(\\d+)\\) [^\n]*" + Pattern.quote(needle)).matcher(decision);
        Assert.assertTrue(m.find(), "no option containing " + needle + ": " + decision);
        return Integer.parseInt(m.group(1));
    }

    private static List<String> names(Iterable<Card> cards) {
        List<String> out = new ArrayList<>();
        for (Card c : cards) out.add(c.getName());
        return out;
    }

    // ------------------------------------------------------------------ hook: chooseCardsForEffectMultiple

    /** Gift of the Gargantuan digs four and asks for a creature and a land separately, each with a
     *  "None" decline; the two the model names go to the hand and the rest return to the bottom. */
    @Test
    public void giftOfTheGargantuansTwoCategoryPicksAreTheSeats() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Gift of the Gargantuan", me, ZoneType.Hand);
        card("Grizzly Bears", me, ZoneType.Library);     // the top four are what the seat digs over
        card("Island", me, ZoneType.Library);
        card("Lightning Bolt", me, ZoneType.Library);
        card("Ponder", me, ZoneType.Library);
        for (int i = 0; i < 3; i++) card("Forest", me, ZoneType.Battlefield);
        GoldenChecksTest.Script model = new GoldenChecksTest.Script("CHOICE: 1")
                .on("You have priority", choice("CAST Gift of the Gargantuan"))
                .on("MANA:", p -> "PICKS: NONE")
                .on("— Creature", choice("Grizzly Bears"))
                .on("— Land", choice("Island"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);

        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, "the seat passed instead of casting");
        ctrl.playChosenSpellAbility(r.get(0));
        while (!g.getStack().isEmpty()) g.getStack().resolveStack();

        Assert.assertTrue(model.asked("— Creature") && model.asked("— Land"),
                "the two categories were not asked: " + PromptFidelityTest.decisions(model));
        List<String> hand = names(me.getCardsIn(ZoneType.Hand));
        Assert.assertTrue(hand.contains("Grizzly Bears") && hand.contains("Island"),
                "the creature and land the model named are not in hand: " + hand);
        Assert.assertEquals(names(me.getCardsIn(ZoneType.Library)), Arrays.asList("Lightning Bolt", "Ponder"),
                "the rest did not go back on the bottom");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** Nothing eligible in the top four: the effect never asks, and the order of the library stands. */
    @Test
    public void aDigWithNothingEligibleIsNotAsked() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Gift of the Gargantuan", me, ZoneType.Hand);
        card("Lightning Bolt", me, ZoneType.Library);
        card("Ponder", me, ZoneType.Library);
        card("Duress", me, ZoneType.Library);
        card("Preordain", me, ZoneType.Library);
        for (int i = 0; i < 3; i++) card("Forest", me, ZoneType.Battlefield);
        GoldenChecksTest.Script model = new GoldenChecksTest.Script("CHOICE: 1")
                .on("You have priority", choice("CAST Gift of the Gargantuan"))
                .on("MANA:", p -> "PICKS: NONE");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);

        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r);
        ctrl.playChosenSpellAbility(r.get(0));
        while (!g.getStack().isEmpty()) g.getStack().resolveStack();

        Assert.assertFalse(model.asked("Choose cards"),
                "a category was asked although nothing in the top four matches one");
        Assert.assertEquals(names(me.getCardsIn(ZoneType.Library)),
                Arrays.asList("Lightning Bolt", "Ponder", "Duress", "Preordain"),
                "the top four changed when no category could be filled");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    // ------------------------------------------------------------------ hook: chooseSpellAbilitiesForEffect

    /** This Is How It Ends targets a creature the seat owns, so the owner facing the villainous
     *  choice is the seat itself: lose 5 life, or shuffle another of its creatures; the answer of
     *  "lose 5 life" is the one the engine applies. */
    @Test
    public void theVillainousChoiceOfItsOwnCreatureIsTheSeat() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        card("This Is How It Ends", me, ZoneType.Hand);
        for (int i = 0; i < 4; i++) card("Swamp", me, ZoneType.Battlefield);
        GoldenChecksTest.Script model = new GoldenChecksTest.Script("CHOICE: 1")
                .on("You have priority", choice("CAST This Is How It Ends"))
                .on("MANA:", p -> "PICKS: NONE")
                .on("Villainous Choice", p -> "PICKS: " + optContaining(p, "loses 5 life"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);

        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, "This Is How It Ends was not offered");
        ctrl.playChosenSpellAbility(r.get(0));
        while (!g.getStack().isEmpty()) g.getStack().resolveStack();

        Assert.assertTrue(model.asked("Villainous Choice"), "the villainous choice was not asked");
        Assert.assertEquals(me.getLife(), 15, "the seat did not lose the 5 life of the answer it named");
        Assert.assertTrue(bears.isInZone(ZoneType.Library),
                "the targeted creature was not shuffled into its owner's library");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    // ------------------------------------------------------------------ hook: chooseSingleCardState

    /** Whenever Ghostly Keybearer deals combat damage to a player, the seat unlocks a locked door of
     *  one target Room it controls — and a Room put into play without being cast has BOTH halves
     *  locked, so the ask is a real two-option choice of door half. The unlocked half's own trigger
     *  (surveil 2) then fires over an empty library and asks nothing. */
    @Test
    public void theUnlockChoosesTheDoorHalfTheSeatNames() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card keybearer = card("Ghostly Keybearer", me, ZoneType.Battlefield);
        Card tunnel = card("Underwater Tunnel", me, ZoneType.Battlefield);
        g.getTriggerHandler().resetActiveTriggers();    // card() adds via a raw zone add: the event-trigger
        // registry is rebuilt from the battlefield state so the Keybearer's damage trigger is live.
        GoldenChecksTest.Script model = new GoldenChecksTest.Script("CHOICE: 1")
                .on("declare attackers", picks("Ghostly Keybearer"))
                .on("Choose target Room you control", choice("Underwater Tunnel"))
                .on("Choose Room to unlock", p -> "CHOICE: " + optContaining(p, "Underwater Tunnel"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);

        Combat combat = new Combat(me);
        g.getPhaseHandler().setCombat(combat);
        ctrl.declareAttackers(me, combat);
        combat.setBlocked(keybearer, false);    // an unblocked band: what the declare-blockers step does
        combat.assignCombatDamage(false);
        combat.dealAssignedDamage();
        g.getTriggerHandler().runWaitingTriggers();
        g.getStack().addAllTriggeredAbilitiesToStack();
        while (!g.getStack().isEmpty()) {
            g.getStack().resolveStack();
            g.getStack().addAllTriggeredAbilitiesToStack();
        }

        Assert.assertTrue(model.asked("Choose Room to unlock"), "the door half to unlock was not asked");
        Assert.assertTrue(tunnel.getUnlockedRooms().contains(CardStateName.LeftSplit),
                "the half the model named was not unlocked: " + tunnel.getUnlockedRooms());
        Assert.assertFalse(tunnel.getUnlockedRooms().contains(CardStateName.RightSplit),
                "the other half was unlocked too: " + tunnel.getUnlockedRooms());
        Assert.assertEquals(rival.getLife(), 17, "the 3 combat damage did not reach the rival");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    // ------------------------------------------------------------------ hook: chooseTarget (the redirect)

    /** LineFindingProbeTest's rivalCasts aims a spell at ONE card; the redirect pair list spans
     *  EVERY target of the stack instance (ChangeTargetsEffect walks them), so the spell to redirect
     *  lands here with a divided target set instead. */
    static void rivalCastsDivided(Game g, String spell, List<Card> targets) {
        Player rival = g.getPlayers().get(1);
        g.getPhaseHandler().devModeSet(PhaseType.MAIN1, rival);
        Card c = card(spell, rival, ZoneType.Hand);
        SpellAbility sa = c.getFirstSpellAbility();
        sa.setActivatingPlayer(rival);
        sa.clearTargets();    // computes the divided total from its param, so getDividedValue is set (#124)
        for (Card t : targets) { sa.getTargets().add(t); sa.addDividedAllocation(t, 1); }
        sa.setHostCard(g.getAction().moveToStack(c, sa));
        g.getStack().add(sa);
    }

    /** Spellskite redirects ONE target of target spell: with the rival's Forked Bolt (2 damage
     *  divided over two targets) on the stack the redirect is a real two-option ask, the named pair
     *  is replaced by Spellskite WITH its divided portion, and Spellskite's own phyrexian {U/P}
     *  pays 2 life with no blue mana anywhere. The seat owns the redirect (Spellskite is its
     *  magnet); the spell that loses the target is the rival's, because the seat's OWN divided cast
     *  through the play loop only ever sets its minimum (ensureTargetsSet tops up to min, never to
     *  max), leaving a one-pair redirect Forge never asks. The ChangeSingleTarget branch swaps the
     *  magnet itself: the appendix's claim that chooseNewTargetsFor follows it up is wrong for this
     *  branch (APPENDIX-A drift, recorded 2026-10-16). */
    @Test
    public void theSeatsRedirectMovesOneForkedBoltPortionToSpellskite() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card skite = card("Spellskite", me, ZoneType.Battlefield);
        Card lions = card("Savannah Lions", rival, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", rival, ZoneType.Battlefield);
        card("Mountain", me, ZoneType.Battlefield);    // Spellskite's {U/P} costs 2 life (no blue exists)
        rivalCastsDivided(g, "Forked Bolt", Arrays.asList(lions, bears));
        g.getPhaseHandler().devModeSet(PhaseType.MAIN1, me);
        GoldenChecksTest.Script model = new GoldenChecksTest.Script("CHOICE: 1")
                .on("You have priority", choice("ACTIVATE Spellskite"))
                .on("REDIRECT", p -> "CHOICE: " + optContaining(p, "Grizzly Bears"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);

        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, "Spellskite's redirect was not offered");
        ctrl.playChosenSpellAbility(r.get(0));

        g.getStack().resolveStack();    // Spellskite's ability resolves; the redirect ask ran inside it
        SpellAbilityStackInstance bolt = g.getStack().peek();
        Assert.assertEquals(bolt.getSourceCard().getName(), "Forked Bolt",
                "the redirect did not resolve into the rival's spell first: " + bolt.getSourceCard());
        Assert.assertTrue(bolt.getTargetChoices().contains(lions) && bolt.getTargetChoices().contains(skite)
                        && !bolt.getTargetChoices().contains(bears),
                "the pair the model named was not replaced by Spellskite: " + bolt.getTargetChoices());
        Assert.assertEquals(bolt.getTargetChoices().getDividedValue(skite), Integer.valueOf(1),
                "the divided portion did not travel with the redirected target: " + bolt.getTargetChoices());
        Assert.assertTrue(model.asked("REDIRECT"), "the redirect was not asked");

        g.getStack().resolveStack();    // Forked Bolt resolves over its redirected targets
        g.getAction().checkStateEffects(true, java.util.Collections.emptySet());    // what the next priority would run: SBAs kill the damage-lethal Lion
        Assert.assertFalse(lions.isInZone(ZoneType.Battlefield),
                "the un-redirected portion missed its target");
        Assert.assertTrue(bears.isInZone(ZoneType.Battlefield),
                "the bolt the redirect moved away still found the spared creature");
        Assert.assertEquals(bears.getDamage(), 0, "the spared creature was hurt");
        Assert.assertEquals(skite.getDamage(), 1, "the magnet did not take the moved portion");
        Assert.assertEquals(me.getLife(), 18, "the {U/P} did not cost 2 life");
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
}
