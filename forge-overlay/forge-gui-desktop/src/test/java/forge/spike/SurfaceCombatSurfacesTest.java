package forge.spike;

import static forge.spike.GoldenChecksTest.Script;
import static forge.spike.GoldenChecksTest.choice;
import static forge.spike.GoldenChecksTest.picks;
import static forge.spike.LineFindingProbeTest.bareGame;
import static forge.spike.LineFindingProbeTest.card;
import static forge.spike.LineFindingProbeTest.seat;

import java.util.Arrays;
import java.util.Collections;
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
import forge.game.card.CardCollection;
import forge.game.combat.Combat;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.spellability.SpellAbility;
import forge.game.zone.ZoneType;

/**
 * The three combat surfaces the seat owns beyond the ones GoldenChecksTest covers: the Ghostly
 * Prison attack tax is the model's pay-or-decline; the damage-assignment orders (one blocker
 * blocking several attackers, a late blocker joining an already-ordered attacker) are the seat's
 * choice, made through the real engine paths (PhaseHandler's declare step, Combat ordering, and a
 * real Flash Foliage resolving on the stack). Every hook ends "asked" in the surface report.
 * The payCombatCost prompt renders through seatText(...): a reverted repair would leak the
 * engine-side card id into the model's menu, and AskContract.check's ENGINE_ID rule fails
 * exactly such a prompt on every test here (pr-review 2026-10-16).
 */
public class SurfaceCombatSurfacesTest {

    /** The ask types this wave puts on the seat's surface; the gate below fails until the model
     *  has answered each through the live controller. */
    static final Set<String> HOOKS = new TreeSet<>(Arrays.asList("combat-cost", "damage-order", "damage-assign"));

    /** Types a model answered in this class (tier smart or fast), across every test. */
    static final Set<String> ANSWERED = Collections.synchronizedSet(new TreeSet<>());

    private final java.util.List<String> asked = java.util.Collections.synchronizedList(new java.util.ArrayList<>());

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

    /** Damage-assignment ordering only asks when the rules want an order (GameRules.orderCombatants
     *  must be true BEFORE the Combat is built: the constructor snapshots the flag). */
    private static void wantOrderedDamage(Game g) { g.getRules().setOrderCombatants(true); }

    // ------------------------------------------------------------------ payCombatCost

    /** Ghostly Prison on the rival: the seat declares its Bears and the engine charges the {2} attack
     *  tax through checkPropagandaEffects (PhaseHandler's real declare loop). The model pays. */
    @Test
    public void attackTaxIsPaidByTheModel() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Ghostly Prison", rival, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        Card forestA = card("Forest", me, ZoneType.Battlefield);
        Card forestB = card("Forest", me, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1")
                .on("declare attackers", picks("Grizzly Bears"))
                .on("Pay additional cost", choice("Pay"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);

        g.getPhaseHandler().devAdvanceToPhase(PhaseType.COMBAT_DECLARE_ATTACKERS);

        Combat combat = g.getCombat();
        Assert.assertEquals(combat.getAttackers().size(), 1, "the model paid, so the attack stands");
        Assert.assertTrue(combat.getAttackers().contains(bears));
        Assert.assertTrue(bears.isTapped(), "the attacker ended tapped");
        Assert.assertTrue(forestA.isTapped() && forestB.isTapped(), "the {2} came from the seat's mana sources");
        Assert.assertTrue(model.asked("Pay additional cost"), "the model was never asked about the attack tax");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** The model declines the tax: the attacker does not attack and is untapped again — never an
     *  AI wink, never a silent keep. */
    @Test
    public void aDeclinedAttackTaxWithdrawsTheAttacker() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Ghostly Prison", g.getPlayers().get(1), ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Forest", me, ZoneType.Battlefield);
        card("Forest", me, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1")
                .on("declare attackers", picks("Grizzly Bears"))
                .on("Pay additional cost", choice("Do not pay"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);

        g.getPhaseHandler().devAdvanceToPhase(PhaseType.COMBAT_DECLARE_ATTACKERS);

        Assert.assertEquals(g.getCombat().getAttackers().size(), 0, "decline means no attack");
        Assert.assertFalse(bears.isTapped(), "a withdrawn attacker is untapped again");
        Assert.assertTrue(model.asked("Pay additional cost"), "the decline went through the model, not a rule");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** Empty pool and no mana sources: the price cannot be paid, so the seat's controller takes its
     *  fixed no (an "auto" row in the report) and the engine keeps the attacker home. No model ask
     *  is possible here; nothing may reach Forge's AI. */
    @Test
    public void anUnpayableAttackTaxNeverReachesTheAi() {
        Game g = bareGame(PhaseType.MAIN1);
        card("Ghostly Prison", g.getPlayers().get(1), ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", g.getPlayers().get(0), ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1").on("declare attackers", picks("Grizzly Bears"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);

        g.getPhaseHandler().devAdvanceToPhase(PhaseType.COMBAT_DECLARE_ATTACKERS);

        Assert.assertEquals(g.getCombat().getAttackers().size(), 0, "cannot pay -> no attack");
        Assert.assertFalse(bears.isTapped(), "a withdrawn attacker is untapped again");
        Assert.assertFalse(model.asked("Pay additional cost"), "an unpayable cost is no question");
        Assert.assertTrue(ctrl.autoPicksCount() >= 1, "the fixed no was the harness's own, recorded");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    // ------------------------------------------------------------------ orderAttackers

    /** Night Market Guard blocks TWO attackers; at the damage step the engine asks the seat which
     *  attacker its damage hits first (GameRules.orderCombatants on, the way PhaseHandler drives
     *  orderBlockers/orderAttackers back to back). The seat declares one pair (the text grammar
     *  assigns each candidate once) and the Guard's extra block joins mechanically; the split
     *  follows the model's order: lethal to the Lions (1) before touching the Wurm. The assignment
     *  itself is NOT asked again with an order on file (the #224 fix on main, epoch 36) — pinned
     *  here, so a reverted repair cannot quietly re-ask a choice the seat already made. */
    @Test
    public void aDoubleBlockByTheGuardIsOrderedByTheModel() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        wantOrderedDamage(g);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card guard = card("Night Market Guard", me, ZoneType.Battlefield);
        Card lions = card("Savannah Lions", rival, ZoneType.Battlefield);
        Card wurm = card("Craw Wurm", rival, ZoneType.Battlefield);
        g.getAction().checkStateEffects(true); // the Guard's CanBlockAmount is a continuous effect
        Script model = new Script("BLOCKS: Night Market Guard->Savannah Lions")
                .on("COMBAT DAMAGE ORDER", picks("Savannah Lions", "Craw Wurm"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Combat combat = new Combat(rival);
        combat.addAttacker(lions, me);
        combat.addAttacker(wurm, me);

        ctrl.declareBlockers(me, combat); // the text grammar assigns each candidate once: the Lions block
        combat.addBlocker(wurm, guard); // the Guard's extra block joins, exactly what its static allows
        g.getPhaseHandler().setCombat(combat); // the damage step reads the combat from the handler
        Assert.assertEquals(combat.getBlockers(lions).size(), 1, "the Guard blocks the Lions");
        Assert.assertEquals(combat.getBlockers(wurm).size(), 1, "and the extra creature: the Wurm");
        // The real PhaseHandler ordering pair (748-750): blockers of one attacker each, then the
        // Guard's attacker order (two attackers -> the ask).
        combat.orderBlockersForDamageAssignment();
        combat.orderAttackersForDamageAssignment();
        Assert.assertTrue(model.asked("COMBAT DAMAGE ORDER"), "the guard's attacker order was not asked");

        combat.assignCombatDamage(false);
        Integer onLions = lions.getAssignedDamageMap().get(guard), onWurm = wurm.getAssignedDamageMap().get(guard);
        Assert.assertNotNull(onLions, "the Guard never assigned against the Lions: " + lions.getAssignedDamageMap());
        Assert.assertEquals(onLions.intValue(), 1, "lethal to the Lions is dealt first when the model's order is used");
        Assert.assertEquals(onWurm.intValue(), 2, "the rest of the Guard's 3 damage goes to the Wurm");
        Assert.assertFalse(model.asked("COMBAT DAMAGE ASSIGNMENT"),
                "the assignment is not asked again once the order was declared (the #224 fix on main: a re-ask here is a reverted repair)");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    // ------------------------------------------------------------------ orderBlocker

    /** A second blocker joins an attacker whose blocker order already exists: Flash Foliage resolved
     *  on the stack (real card, real target) puts a Saproling token on the Craw Wurm's blocking list
     *  mid-combat, and Combat.addBlocker asks the ATTACKING seat where the newcomer slots. The
     *  Wurm's damage assignment is then not asked again (the #224 fix on main, epoch 36): it
     *  follows the stored order mechanically — pinned like the Guard fixture's. */
    @Test
    public void aBlockerThatJoinsMidCombatIsOrderedByTheModel() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        wantOrderedDamage(g);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Island", rival, ZoneType.Library); // the spell's draw side effect
        card("Island", rival, ZoneType.Library);
        Card wurm = card("Craw Wurm", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1")
                .on("now also blocks", picks("Saproling Token", "Grizzly Bears"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Combat combat = new Combat(me);
        combat.addAttacker(wurm, rival);
        combat.addBlocker(wurm, bears); // the rival's standing block, mechanics as the engine stores them
        combat.orderBlockersForDamageAssignment(); // one blocker: order recorded without an ask
        g.getPhaseHandler().setCombat(combat);

        rivalBlocksWith(g, "Flash Foliage", wurm);
        Card token = null;
        for (Card c : rival.getCreaturesInPlay())
            if (c.getName().contains("Saproling")) token = c;
        Assert.assertNotNull(token, "Flash Foliage made no Saproling token");
        Assert.assertTrue(combat.getBlockers(wurm).contains(token), "the token did not join the Wurm's blockers");
        Assert.assertTrue(model.asked("now also blocks"), "the joining blocker was never asked to be slotted");

        combat.assignCombatDamage(false);
        Integer onToken = token.getAssignedDamageMap().get(wurm), onBears = bears.getAssignedDamageMap().get(wurm);
        Assert.assertNotNull(onToken, "the token was not assigned any damage");
        Assert.assertEquals(onToken.intValue(), 1,
                "the model put the token first, so the Wurm's lethal goes to it first");
        Assert.assertNotNull(onBears, "the Bears were not assigned any damage");
        Assert.assertEquals(onBears.intValue(), 5,
                "Bears take lethal 2 plus the Wurm's no-trample excess of 3, after the token's 1");
        Assert.assertFalse(model.asked("COMBAT DAMAGE ASSIGNMENT"),
                "the assignment is not asked again once the order was stored (the #224 fix on main: a re-ask here is a reverted repair)");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** The engine's ask contract for a joining blocker, driven on the controller directly (like
     *  GoldenChecksTest does for the plural hook): the answer returned IS the model's slotting. */
    @Test
    public void aLateJoiningBlockerIsSlottedWhereTheModelWants() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0);
        Card wurm = card("Craw Wurm", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        Card goyf = card("Tarmogoyf", me, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1").on("now also blocks", picks("Tarmogoyf", "Grizzly Bears"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        CardCollection order = ctrl.orderBlocker(wurm, goyf, new CardCollection(bears));
        Assert.assertTrue(model.asked("now also blocks"), "the join was not asked");
        Assert.assertEquals(order.size(), 2);
        Assert.assertEquals(order.get(0), goyf, "the model's newcomer goes first: " + order);
        Assert.assertEquals(order.get(1), bears);
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    // ------------------------------------------------------------------ assignCombatDamage

    /** A trample attacker blocked ONCE is the split no declared order can answer on main (epoch
     *  36): one blocker, enough damage to kill it and trample over = a genuine choice, so
     *  LlmSpikeGui.assignCombatDamage asks it (decideOne) whatever the ordering rules hold — the
     *  multi-blocker path answers from the seat's declared order instead (the fixtures above pin
     *  that). The model picks ALL TO BLOCKER, the outcome the silent max-trample default would
     *  not produce, so if the ask ever stops going out (a reverted repair routing this to auto)
     *  the damage assert turns red without a live endpoint. */
    @Test
    public void aTrampleAttackerBlockedOnceIsAskedHowToSplitItsDamage() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card wurm = card("Pelakka Wurm", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1")
                .on("COMBAT DAMAGE ASSIGNMENT", choice("ALL TO BLOCKER"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Combat combat = new Combat(me);
        combat.addAttacker(wurm, rival);
        combat.addBlocker(wurm, bears);
        combat.orderBlockersForDamageAssignment(); // one blocker: recorded, no ask
        g.getPhaseHandler().setCombat(combat);

        combat.assignCombatDamage(false);
        Integer onBears = bears.getAssignedDamageMap().get(wurm);
        Assert.assertTrue(model.asked("COMBAT DAMAGE ASSIGNMENT"), "the split was not asked of the model");
        Assert.assertEquals(onBears, Integer.valueOf(7),
                "the model put all 7 into the Bears: the split the silent max-trample default would not pick");
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

    /** The rival casts an instant that makes a creature of theirs enter combat blocking "target",
     *  exactly the LineFindingProbeTest.rivalCasts mechanics with the phase left as-is. */
    private static void rivalBlocksWith(Game g, String spell, Card target) {
        Player rival = g.getPlayers().get(1);
        Card c = card(spell, rival, ZoneType.Hand);
        SpellAbility sa = c.getFirstSpellAbility();
        sa.setActivatingPlayer(rival);
        sa.getTargets().add(target);
        sa.setHostCard(g.getAction().moveToStack(c, sa));
        g.getStack().add(sa);
        while (!g.getStack().isEmpty()) g.getStack().resolveStack();
    }
}
