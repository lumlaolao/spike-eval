package forge.spike;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;

import forge.game.Game;
import forge.game.GameEntity;
import forge.game.card.Card;
import forge.game.combat.Combat;
import forge.game.combat.CombatUtil;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.spellability.SpellAbility;
import forge.game.zone.ZoneType;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import static forge.spike.LineFindingProbeTest.Recorder;
import static forge.spike.LineFindingProbeTest.bareGame;
import static forge.spike.LineFindingProbeTest.card;
import static forge.spike.LineFindingProbeTest.seat;

/**
 * The PROPOSE_ACTIONS arm (docs/harness/ARCHITECTURE.md, "The propose condition"): the play menu is hidden, the seat
 * proposes a play, and the engine checks it like a type checker. The menu is the oracle, so the first test is
 * soundness: every play the menu offers is reachable by a proposal. The rest pin each rejection category on a position
 * built to break exactly that rule, the form errors, the record a row carries, and the ask loop's four tries.
 */
public class ProposeActionsTest {
    @BeforeClass
    public void init() { new LineFindingProbeTest().initializeModel(); }

    @BeforeMethod
    public void on() {
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL;
        AnswerTool.PROPOSE_FOR_TEST = true;
    }

    @AfterMethod(alwaysRun = true)
    public void off() {
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null;
        AnswerTool.PROPOSE_FOR_TEST = null;
        AnswerTool.TERSE_FOR_TEST = null;
        AnswerTool.NOTEBOOK_FOR_TEST = null;
    }

    // ------------------------------------------------------------------ helpers

    private static int handle(LlmSpikeGui.PlayerControllerLLM ctrl, Card c) {
        ctrl.observer().describe(c, false);
        return ctrl.observer().handleOf(c);
    }

    private static AnswerTool.Proposal prop(String verb, Integer card, Integer ability, String face, String cost) {
        AnswerTool.Proposal p = new AnswerTool.Proposal();
        p.verb = verb; p.card = card; p.ability = ability; p.face = face; p.cost = cost;
        return p;
    }

    /** The verdict on {@code p} at this priority, against the menu the seat would be shown. */
    private static String verdict(LlmSpikeGui.PlayerControllerLLM ctrl, Player me, boolean ownMain, AnswerTool.Proposal p, int[] hit) {
        LlmSpikeGui.PlayerControllerLLM.PlayOptions po = LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(me, ownMain, true);
        int passIdx = 1 + po.spells.size() + po.landPlays.size();
        return ctrl.matchProposal(p, po.spells, po.landPlays, po.landCards, passIdx, me, ownMain, hit);
    }

    private static String reject(LlmSpikeGui.PlayerControllerLLM ctrl, Player me, boolean ownMain, AnswerTool.Proposal p) {
        String err = verdict(ctrl, me, ownMain, p, new int[] {-1});
        Assert.assertNotNull(err, "accepted: " + p.render());
        return err;
    }

    // ------------------------------------------------------------------ the answer tool

    @Test
    public void theSchemaCarriesActionOnlyUnderTheCondition() {
        AnswerTool.PROPOSE_FOR_TEST = false;
        JsonObject off = AnswerTool.fixedDefinition().getAsJsonObject("function").getAsJsonObject("parameters").getAsJsonObject("properties");
        Assert.assertFalse(off.has("action"), "the default schema's bytes move");
        Assert.assertFalse(AnswerTool.instructions().contains("PLAYS:"), "the default preamble moves");
        AnswerTool.PROPOSE_FOR_TEST = true;
        JsonObject on = AnswerTool.fixedDefinition().getAsJsonObject("function").getAsJsonObject("parameters").getAsJsonObject("properties");
        Assert.assertTrue(on.has("action"));
        Assert.assertTrue(AnswerTool.instructions().contains("PLAYS:"));
        Assert.assertTrue(AnswerTool.protocolVersion().contains("+propose"), AnswerTool.protocolVersion());
        Assert.assertTrue(on.has("declare_attackers") && on.has("declare_blockers"), "the combat proposals are in the schema");
        Assert.assertTrue(AnswerTool.instructions().contains("COMBAT:"));
        Assert.assertFalse(off.has("declare_attackers") || off.has("declare_blockers"), "the default schema's bytes move");
    }

    @Test
    public void aMalformedCombatProposalIsAFormatError() {
        AnswerTool.Spec a = AnswerTool.Spec.attackProposal(List.of("Grizzly Bears"));
        String menu = AnswerTool.validate(a, "{\"options\":[1]}").error;
        Assert.assertTrue(menu.startsWith("FORMAT:") && menu.contains("no numbered list") && menu.contains("declare_attackers"), menu);
        Assert.assertTrue(AnswerTool.validate(a, "{\"declare_attackers\":[{\"attacker\":\"Bears\"}]}").error.startsWith("FORMAT:"));
        Assert.assertTrue(AnswerTool.validate(a, "{\"declare_attackers\":[{\"attacker\":\"@3\"},{\"attacker\":\"@3\"}]}").error
                .contains("twice"));
        AnswerTool.Result ok = AnswerTool.validate(a, "{\"declare_attackers\":[{\"attacker\":\"@3\",\"defender\":\"@9\"},{\"attacker\":\"4\"}]}");
        Assert.assertNull(ok.error);
        Assert.assertEquals(AnswerTool.renderDeclared(ok.answer.declared, true), "@3 -> @9, @4 -> opponent");
        AnswerTool.Spec b = AnswerTool.Spec.blockProposal(List.of("Grizzly Bears"), List.of("Hill Giant"));
        Assert.assertTrue(AnswerTool.validate(b, "{\"blocks\":[]}").error.contains("declare_blockers"));
        Assert.assertTrue(AnswerTool.validate(b, "{\"declare_blockers\":[{\"blocker\":\"@3\"}]}").error.startsWith("FORMAT: `attacker`"));
        Assert.assertNull(AnswerTool.validate(b, "{\"declare_blockers\":[]}").error, "no blocks is well formed");
    }

    @Test
    public void aMalformedProposalIsAFormatError() {
        AnswerTool.Spec s = AnswerTool.Spec.action(3);
        String menuField = AnswerTool.validate(s, "{\"option\":1}").error;
        Assert.assertTrue(menuField.startsWith("FORMAT:") && menuField.contains("no numbered list") && menuField.contains("you gave option"), menuField);
        Assert.assertTrue(AnswerTool.validate(s, "{\"action\":{\"verb\":\"attack\"}}").error.startsWith("FORMAT:"));
        Assert.assertTrue(AnswerTool.validate(s, "{\"action\":{\"verb\":\"cast\"}}").error.startsWith("FORMAT: `card`"));
        Assert.assertTrue(AnswerTool.validate(s, "{\"action\":{\"verb\":\"cast\",\"card\":\"@3\",\"x\":1}}").error.startsWith("FORMAT:"));
        AnswerTool.Result ok = AnswerTool.validate(s, "{\"action\":{\"verb\":\"Cast\",\"card\":\"Lightning Bolt @12\",\"cost\":\"Flashback\"}}");
        Assert.assertNull(ok.error);
        Assert.assertEquals(ok.answer.action.render(), "cast @12 (Flashback)");
    }

    @Test
    public void theRowRecordsEveryProposalAndFirstTryLegalSkipsFormatErrors() {
        List<String[]> calls = new ArrayList<>();
        calls.add(new String[] {"{\"action\":{\"verb\":\"cast\",\"card\":\"@99\"}}", "FORMAT: no object @99 is in view now."});
        calls.add(new String[] {"{\"action\":{\"verb\":\"cast\",\"card\":\"@4\"}}", "ILLEGAL (timing): a sorcery."});
        calls.add(new String[] {"{\"action\":{\"verb\":\"pass\"}}", null});
        JsonObject row = JsonParser.parseString("{\"x\":1" + LlmSpikeGui.PlayerControllerLLM.proposalRecord(calls, "action") + "}").getAsJsonObject();
        JsonArray ps = row.getAsJsonArray("proposals");
        Assert.assertEquals(ps.size(), 3);
        Assert.assertEquals(ps.get(0).getAsJsonObject().get("verdict").getAsString(), "format");
        Assert.assertEquals(ps.get(1).getAsJsonObject().get("category").getAsString(), "timing");
        Assert.assertEquals(ps.get(2).getAsJsonObject().get("verdict").getAsString(), "accepted");
        Assert.assertFalse(row.get("first_try_legal").getAsBoolean(), "the first well-formed proposal was illegal");
        // A combat proposal is recorded under its own field, by the same rule.
        List<String[]> combat = new ArrayList<>();
        combat.add(new String[] {"{\"declare_blockers\":[{\"blocker\":\"@3\",\"attacker\":\"@7\"}]}", "ILLEGAL (evasion): flying."});
        combat.add(new String[] {"{\"declare_blockers\":[]}", null});
        JsonObject crow = JsonParser.parseString("{\"x\":1" + LlmSpikeGui.PlayerControllerLLM.proposalRecord(combat, "declare_blockers") + "}").getAsJsonObject();
        JsonArray cps = crow.getAsJsonArray("proposals");
        Assert.assertEquals(cps.get(0).getAsJsonObject().getAsJsonArray("declare_blockers").size(), 1);
        Assert.assertEquals(cps.get(0).getAsJsonObject().get("category").getAsString(), "evasion");
        Assert.assertEquals(cps.get(1).getAsJsonObject().getAsJsonArray("declare_blockers").size(), 0);
        Assert.assertFalse(crow.get("first_try_legal").getAsBoolean());
    }

    // ------------------------------------------------------------------ soundness: the menu is reachable

    /** Every play the menu offers on a busy main phase (a spell, a flashback from the graveyard, an activated ability
     *  with a sacrifice cost, a land) is named by a proposal built the way the guide tells the seat to. */
    @Test
    public void everyMenuPlayIsReachableByAProposal() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        for (int i = 0; i < 4; i++) card("Mountain", me, ZoneType.Battlefield);
        card("Goblin Bombardment", me, ZoneType.Battlefield);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Lightning Bolt", me, ZoneType.Hand);
        card("Faithless Looting", me, ZoneType.Graveyard);
        card("Forest", me, ZoneType.Hand);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        LlmSpikeGui.PlayerControllerLLM.PlayOptions po = LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(me, true, true);
        int passIdx = 1 + po.spells.size() + po.landPlays.size();
        Assert.assertTrue(po.spells.size() >= 3, "the position offers a spell, a flashback and an ability: " + po.spells);
        for (int i = 0; i < po.spells.size(); i++) {
            SpellAbility sa = po.spells.get(i);
            AnswerTool.Proposal p = ctrl.proposalFor(sa);
            Assert.assertNotNull(p, "the abilities list does not show " + sa);
            int[] hit = {-1};
            Assert.assertNull(ctrl.matchProposal(p, po.spells, po.landPlays, po.landCards, passIdx, me, true, hit), p.render());
            Assert.assertEquals(hit[0], i + 1, p.render() + " names menu option " + (i + 1));
        }
        for (int i = 0; i < po.landCards.size(); i++) {
            int[] hit = {-1};
            AnswerTool.Proposal p = prop("play_land", handle(ctrl, po.landCards.get(i)), null, null, null);
            Assert.assertNull(ctrl.matchProposal(p, po.spells, po.landPlays, po.landCards, passIdx, me, true, hit), p.render());
            Assert.assertEquals(hit[0], 1 + po.spells.size() + i);
        }
        int[] hit = {-1};
        Assert.assertNull(ctrl.matchProposal(prop("pass", null, null, null, null), po.spells, po.landPlays, po.landCards, passIdx, me, true, hit));
        Assert.assertEquals(hit[0], passIdx);
    }

    /** PROPOSE_AUDIT is a diagnostic: auditing a menu of targeted plays must leave no target pending, or the seat's
     *  next targeted cast in a menu game is given the audit's last target without being asked (acceptance 196de57). */
    @Test
    public void theAuditLeavesNoProposedTargetBehind() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        // The last play audited is a targeted one (a mana ability audited after it would clear the target by chance).
        card("Prodigal Pyromancer", me, ZoneType.Battlefield);
        card("Grizzly Bears", rival, ZoneType.Battlefield);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        LlmSpikeGui.PlayerControllerLLM.PlayOptions po = LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(me, true, true);
        Assert.assertEquals(po.spells.size(), 1, "the position offers only the pinger's ability: " + po.spells);
        ctrl.auditProposals(po.spells, po.landPlays, po.landCards, me, true);
        Assert.assertEquals(ctrl.auditMissed, 0, "the ability is proposable");
        Assert.assertNull(ctrl.proposedTarget, "the audit must not change the game it audits");
    }

    private static int indexByText(List<SpellAbility> abs, SpellAbility sa) {
        for (int i = 0; i < abs.size(); i++) if (java.util.Objects.equals(abs.get(i).getDescription(), sa.getDescription())) return i;
        Assert.fail("the ability is not in the proposal list: " + sa);
        return -1;
    }

    // ------------------------------------------------------------------ rejections, one per rule

    @Test
    public void aSorceryAtInstantSpeedBreaksTiming() {
        Game g = bareGame(PhaseType.COMBAT_BEGIN);
        Player me = g.getPlayers().get(0);
        card("Forest", me, ZoneType.Battlefield);
        card("Forest", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Hand);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        String err = reject(ctrl, me, false, prop("cast", handle(ctrl, bears), null, null, null));
        Assert.assertTrue(err.startsWith("ILLEGAL (timing)"), err);
    }

    /** A cast that names an optional cost the card has (Kicker), or "normal", is the normal cast: the kicker is asked
     *  when it is cast, as under the menu. Refusing it would count a legal play as a rules error (pr-review #342). */
    @Test
    public void anOptionalCostOrNormalNamesTheNormalCast() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        Card burst = card("Burst Lightning", me, ZoneType.Hand);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        for (String cost : List.of("Kicker", "normal")) {
            AnswerTool.Proposal p = prop("cast", handle(ctrl, burst), null, null, cost);
            p.targets = List.of("opponent");
            int[] hit = {-1};
            Assert.assertNull(verdict(ctrl, me, true, p, hit), cost);
            Assert.assertEquals(hit[0], 1, cost + " is the normal cast");
        }
        AnswerTool.Proposal other = prop("cast", handle(ctrl, burst), null, null, "Buyback");
        other.targets = List.of("opponent");
        Assert.assertTrue(reject(ctrl, me, true, other).startsWith("ILLEGAL (cost)"), "a cost the card does not have");
    }

    /** A cast with no `cost` is the normal cast only: when the menu holds just another way to cast the card (Evoke
     *  without the mana for the full cost), the seat is asked to name it, never cast that way for it (pr-review #342). */
    @Test
    public void aCastWithNoCostIsNeverAnotherWayToCastIt() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        for (int i = 0; i < 3; i++) card("Island", me, ZoneType.Battlefield);
        Card drifter = card("Mulldrifter", me, ZoneType.Hand);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        for (String cost : java.util.Arrays.asList(null, "normal")) {
            String err = reject(ctrl, me, true, prop("cast", handle(ctrl, drifter), null, null, cost));
            // A form error, not a rules error: the cast is legal once named, so it must not count against legality.
            Assert.assertTrue(err.startsWith("FORMAT: say which way"), cost + ": " + err);
        }
        Assert.assertNull(verdict(ctrl, me, true, prop("cast", handle(ctrl, drifter), null, null, "Evoke"), new int[] {-1}));
    }

    /** A cost name matches only itself: "flash" is not the card's Flashback (pr-review #342). */
    @Test
    public void aCostNameDoesNotMatchALongerOne() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        for (int i = 0; i < 3; i++) card("Mountain", me, ZoneType.Battlefield);
        Card looting = card("Faithless Looting", me, ZoneType.Graveyard);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        Assert.assertNull(verdict(ctrl, me, true, prop("cast", handle(ctrl, looting), null, null, "Flashback"), new int[] {-1}));
        Assert.assertTrue(reject(ctrl, me, true, prop("cast", handle(ctrl, looting), null, null, "flash")).startsWith("ILLEGAL (cost)"));
    }

    @Test
    public void aSpellWithoutTheManaBreaksCost() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Forest", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Hand);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        String err = reject(ctrl, me, true, prop("cast", handle(ctrl, bears), null, null, null));
        Assert.assertTrue(err.startsWith("ILLEGAL (cost)"), err);
        Assert.assertFalse(err.contains("Forest"), "a rejection names the rule, not what to do instead: " + err);
    }

    @Test
    public void aSecondLandBreaksTheLandDrop() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card forest = card("Forest", me, ZoneType.Hand);
        me.setLandsPlayedThisTurn(1);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        Assert.assertTrue(reject(ctrl, me, true, prop("play_land", handle(ctrl, forest), null, null, null)).startsWith("ILLEGAL (land drop)"));
    }

    @Test
    public void aLandOutsideTheMainPhaseBreaksTimingAndALandIsNotCast() {
        Game g = bareGame(PhaseType.COMBAT_BEGIN);
        Player me = g.getPlayers().get(0);
        Card forest = card("Forest", me, ZoneType.Hand);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        Assert.assertTrue(reject(ctrl, me, false, prop("play_land", handle(ctrl, forest), null, null, null)).startsWith("ILLEGAL (timing)"));
        Assert.assertTrue(reject(ctrl, me, false, prop("cast", handle(ctrl, forest), null, null, null)).startsWith("ILLEGAL (type)"));
    }

    @Test
    public void aTappedOrSickCreatureCannotPayATapCost() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        Card tapped = card("Prodigal Pyromancer", me, ZoneType.Battlefield);
        tapped.setTapped(true);
        Card sick = card("Prodigal Pyromancer", me, ZoneType.Battlefield);
        sick.setSickness(true);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        Assert.assertTrue(reject(ctrl, me, true, prop("activate", handle(ctrl, tapped), 1, null, null)).contains("is tapped"));
        Assert.assertTrue(reject(ctrl, me, true, prop("activate", handle(ctrl, sick), 1, null, null)).contains("summoning sickness"));
    }

    /** The menu lists a land's mana ability once per name. A tapped copy's ability, or one in the hand, is not a legal
     *  play: before, it matched the other Mountain's entry, that Mountain was tapped, and the arm counted the illegal
     *  proposal as legal (engine labeller of PR #359). An untapped copy is accepted and is the one tapped. */
    @Test
    public void aLandsManaAbilityIsRefusedWhenThatLandCannotPayIt() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card untapped = card("Mountain", me, ZoneType.Battlefield);
        Card tapped = card("Mountain", me, ZoneType.Battlefield);
        tapped.setTapped(true);
        Card inHand = card("Mountain", me, ZoneType.Hand);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        assertCategory(verdict(ctrl, me, true, prop("activate", handle(ctrl, tapped), 1, null, null), new int[] {-1}), "cost");
        assertCategory(verdict(ctrl, me, true, prop("activate", handle(ctrl, inHand), 1, null, null), new int[] {-1}), "zone");
        int[] hit = {-1};
        Assert.assertNull(verdict(ctrl, me, true, prop("activate", handle(ctrl, untapped), 1, null, null), hit), "the untapped Mountain's mana");
        LlmSpikeGui.PlayerControllerLLM.PlayOptions po = LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(me, true, true);
        SpellAbility got = po.spells.get(hit[0] - 1);
        Assert.assertTrue(got.isManaAbility() && got.getHostCard() == untapped, "the untapped Mountain is the one tapped: " + got);
    }

    /** Two untapped Forests, one enchanted by Utopia Sprawl: the menu lists one Forest's mana, the first. The proposed
     *  copy is the one tapped, so the Sprawl's extra mana comes too; tapping the other copy for it cost the seat a mana
     *  (pr-review of #360). */
    @Test
    public void theProposedCopyOfALandIsTheOneTapped() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card plain = card("Forest", me, ZoneType.Battlefield);
        Card sprawled = card("Forest", me, ZoneType.Battlefield);
        Card sprawl = card("Utopia Sprawl", me, ZoneType.Battlefield);
        sprawl.setChosenColors(List.of("green"));
        sprawl.attachToEntity(sprawled, null, true);
        g.getTriggerHandler().resetActiveTriggers();
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        LlmSpikeGui.PlayerControllerLLM.PlayOptions po = LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(me, true, true);
        int passIdx = 1 + po.spells.size() + po.landPlays.size();
        int[] hit = {-1};
        Assert.assertNull(ctrl.matchProposal(prop("activate", handle(ctrl, sprawled), 1, null, null), po.spells, po.landPlays,
                po.landCards, passIdx, me, true, hit));
        SpellAbility got = po.spells.get(hit[0] - 1);
        Assert.assertSame(got.getHostCard(), sprawled, "the other Forest would be tapped: " + got);
        got.setActivatingPlayer(me);
        ctrl.playSpellAbilityNoStack(got, true);
        Assert.assertTrue(sprawled.isTapped() && !plain.isTapped(), "the proposed Forest is the one tapped");
        Assert.assertEquals(me.getManaPool().totalMana(), 2, "the Forest's mana and the Sprawl's");
    }

    /** Forge's canPlay already asks whether the non-mana part of the cost can be paid (CostPayment.canPayAdditionalCosts),
     *  so a sacrifice, exile or discard cost the seat cannot pay is the cost rule, not a restriction or the zone. */
    @Test
    public void aSacrificeOrExileCostThatCannotBePaidBreaksCost() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        for (int i = 0; i < 2; i++) card("Mountain", me, ZoneType.Battlefield);
        for (int i = 0; i < 3; i++) card("Forest", me, ZoneType.Battlefield);
        Card fling = card("Fling", me, ZoneType.Hand);
        Card evolution = card("Eldritch Evolution", me, ZoneType.Hand);
        Card dread = card("Dread Return", me, ZoneType.Graveyard);
        card("Grizzly Bears", me, ZoneType.Graveyard);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        AnswerTool.Proposal p = prop("cast", handle(ctrl, fling), null, null, null);
        p.targets = List.of("opponent");
        assertCategory(verdict(ctrl, me, true, p, new int[] {-1}), "cost");
        assertCategory(verdict(ctrl, me, true, prop("cast", handle(ctrl, evolution), null, null, null), new int[] {-1}), "cost");
        // Dread Return's flashback sacrifices three creatures; the seat controls one.
        Card took = card("Peregrin Took", me, ZoneType.Battlefield);
        assertCategory(verdict(ctrl, me, true, prop("cast", handle(ctrl, dread), null, null, "Flashback"), new int[] {-1}), "cost");
        // Peregrin Took's ability sacrifices three Foods; the seat has none.
        assertCategory(verdict(ctrl, me, true, prop("activate", handle(ctrl, took), 1, null, null), new int[] {-1}), "cost");
    }

    /** Underworld Breach's escape exiles three other cards from the graveyard: with two there, the cost rule. */
    @Test
    public void anEscapeShortOfCardsToExileBreaksCost() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        for (int i = 0; i < 2; i++) card("Mountain", me, ZoneType.Battlefield);
        card("Underworld Breach", me, ZoneType.Battlefield);
        Card bolt = card("Lightning Bolt", me, ZoneType.Graveyard);
        card("Grizzly Bears", me, ZoneType.Graveyard);
        card("Forest", me, ZoneType.Graveyard);
        g.getAction().checkStaticAbilities();
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        AnswerTool.Proposal p = prop("cast", handle(ctrl, bolt), null, null, "Escape");
        p.targets = List.of("opponent");
        assertCategory(verdict(ctrl, me, true, p, new int[] {-1}), "cost");
        card("Island", me, ZoneType.Graveyard);
        g.getAction().checkStaticAbilities();
        Assert.assertNull(verdict(ctrl, me, true, p, new int[] {-1}), "with three other cards the escape is legal");
    }

    /** A loyalty ability is activated only as a sorcery (CR 606.3): in combat it is the timing rule, not a restriction. */
    @Test
    public void aLoyaltyAbilityInCombatBreaksTiming() {
        Game g = bareGame(PhaseType.COMBAT_BEGIN);
        Player me = g.getPlayers().get(0);
        Card jace = card("Jace Beleren", me, ZoneType.Battlefield);
        jace.addCounterInternal(forge.game.card.CounterEnumType.LOYALTY, 3, me, false, null, null);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        assertCategory(verdict(ctrl, me, false, prop("activate", handle(ctrl, jace), 1, null, null), new int[] {-1}), "timing");
    }

    /** A card in the opponent's graveyard is not the seat's: the control rule, before the zone, a cost name or the
     *  ability's own checks (before, the skeleton's was "zone" and a flashback named wrongly "cost"). */
    @Test
    public void anOpponentsCardBreaksControlFirst() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        for (int i = 0; i < 3; i++) card("Swamp", me, ZoneType.Battlefield);
        Card skeleton = card("Reassembling Skeleton", rival, ZoneType.Graveyard);
        Card looting = card("Faithless Looting", rival, ZoneType.Graveyard);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        assertCategory(verdict(ctrl, me, true, prop("activate", handle(ctrl, skeleton), 1, null, null), new int[] {-1}), "control");
        assertCategory(verdict(ctrl, me, true, prop("cast", handle(ctrl, looting), null, null, "flash"), new int[] {-1}), "control");
        // Checking an opponent's ability leaves its activator as it was (pr-review of #360). The menu's enumeration
        // (Forge's getSpellAbilities over every available card) sets it on its own, so it runs first here.
        Card pyro = card("Prodigal Pyromancer", rival, ZoneType.Battlefield);
        int h = handle(ctrl, pyro);
        LlmSpikeGui.PlayerControllerLLM.PlayOptions po = LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(me, true, true);
        SpellAbility ping = LlmSpikeGui.PlayerControllerLLM.proposalAbilities(pyro).get(0);
        ping.setActivatingPlayer(rival);
        assertCategory(ctrl.matchProposal(prop("activate", h, 1, null, null), po.spells, po.landPlays, po.landCards,
                1 + po.spells.size() + po.landPlays.size(), me, true, new int[] {-1}), "control");
        Assert.assertSame(ping.getActivatingPlayer(), rival, "the seat was left as the opponent's activator");
    }

    /** Off the propose arm the checker is never reached: the seat picks from the menu, the menu's first play is made,
     *  and nothing is refused, in the positions the checker's fixes touch. */
    @Test
    public void offTheArmThePlayIsTheMenusAndNothingIsRefused() {
        AnswerTool.PROPOSE_FOR_TEST = false;
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        card("Mountain", me, ZoneType.Battlefield).setTapped(true);
        card("Mountain", me, ZoneType.Hand);
        card("Fling", me, ZoneType.Hand);
        ProposalModel model = new ProposalModel();
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        LlmSpikeGui.PlayerControllerLLM.PlayOptions po = LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(me, true, true);
        List<SpellAbility> played = ctrl.chooseSpellAbilityToPlay();
        Assert.assertEquals(model.plays, 0, "a proposal was asked off the arm");
        Assert.assertFalse(model.results.stream().anyMatch(r -> r.contains("ILLEGAL")), String.valueOf(model.results));
        Assert.assertNotNull(played, "option 1 is a play");
        SpellAbility first = po.spells.isEmpty() ? po.landPlays.get(0) : po.spells.get(0);
        Assert.assertEquals(played.get(0).getHostCard(), first.getHostCard(), "the menu's first play was made");
    }

    @Test
    public void aSpellWithNoLegalTargetBreaksTarget() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Swamp", me, ZoneType.Battlefield);
        card("Swamp", me, ZoneType.Battlefield);
        Card blade = card("Doom Blade", me, ZoneType.Hand);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        Assert.assertTrue(reject(ctrl, me, true, prop("cast", handle(ctrl, blade), null, null, null)).startsWith("ILLEGAL (target)"));
    }

    @Test
    public void anUnknownHandleOrAbilityIsAFormatError() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card pyro = card("Prodigal Pyromancer", me, ZoneType.Battlefield);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        Assert.assertTrue(reject(ctrl, me, true, prop("cast", 987, null, null, null)).startsWith("FORMAT:"));
        Assert.assertTrue(reject(ctrl, me, true, prop("activate", handle(ctrl, pyro), 5, null, null)).startsWith("FORMAT:"));
    }

    // ------------------------------------------------------------------ what the seat reads

    @Test
    public void theStateShowsNoCastableTagsAndListsTheAbilities() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        card("Goblin Bombardment", me, ZoneType.Battlefield);
        card("Lightning Bolt", me, ZoneType.Hand);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        String state = ctrl.snapshot();
        Assert.assertFalse(state.contains("(castable"), "a hand tag is the hand half of the hidden list: " + state);
        Assert.assertTrue(state.contains("YOUR ACTIVATED ABILITIES"), state);
        Assert.assertTrue(state.matches("(?s).*Goblin Bombardment[^\\n]*: 1\\) Sacrifice a creature.*"), state);
        Assert.assertFalse(state.matches("(?s).*Mountain[^\\n]*: 1\\).*"), "a land that only taps for mana is not listed");
        Assert.assertTrue(SeatObservation.guide().contains("YOUR ACTIVATED ABILITIES"));
        Assert.assertFalse(SeatObservation.guide().contains("\"(castable)\""));
        AnswerTool.PROPOSE_FOR_TEST = false;
        Assert.assertEquals(SeatObservation.guide(), SeatObservation.GUIDE, "the default guide moves");
        Assert.assertTrue(ctrl.snapshot().contains("(castable)"), "the default hand lost its tags");
    }

    /** Forecast (rule 702.57): an ability activated from the hand, only in its owner's upkeep. The menu offers it then
     *  (Proclamation of Rebirth, with a one-drop in the graveyard), YOUR ACTIVATED ABILITIES lists it from the hand, a
     *  proposal reaches it with its target, and the same proposal in the main phase is refused. */
    @Test
    public void aForecastAbilityIsOfferedAndProposableOnlyInTheUpkeep() {
        Game g = bareGame(PhaseType.UPKEEP);
        Player me = g.getPlayers().get(0);
        for (int i = 0; i < 6; i++) card("Plains", me, ZoneType.Battlefield);
        Card proc = card("Proclamation of Rebirth", me, ZoneType.Hand);
        Card lions = card("Savannah Lions", me, ZoneType.Graveyard);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        LlmSpikeGui.PlayerControllerLLM.PlayOptions po = LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(me, false, true);
        SpellAbility forecast = po.spells.stream().filter(sa -> sa.getHostCard() == proc && !sa.isSpell()).findFirst().orElse(null);
        Assert.assertNotNull(forecast, "the forecast ability is on the upkeep menu: " + po.spells);
        Assert.assertTrue(po.spells.stream().noneMatch(sa -> sa.getHostCard() == proc && sa.isSpell()), "the sorcery is not castable in the upkeep");
        String state = ctrl.snapshot();
        Assert.assertTrue(state.matches("(?s).*YOUR ACTIVATED ABILITIES.*Proclamation of Rebirth[^\\n]*: 1\\) .*"), state);
        AnswerTool.Proposal p = ctrl.proposalFor(forecast);
        Assert.assertNotNull(p, "the abilities list shows it");
        Assert.assertEquals(p.verb, "activate");
        Assert.assertEquals(p.targets, List.of("@" + handle(ctrl, lions)), "its target is the one-drop in the graveyard");
        int[] hit = {-1};
        int passIdx = 1 + po.spells.size() + po.landPlays.size();
        Assert.assertNull(ctrl.matchProposal(p, po.spells, po.landPlays, po.landCards, passIdx, me, false, hit), p.render());
        Assert.assertEquals(po.spells.get(hit[0] - 1), forecast);
        g.getPhaseHandler().devModeSet(PhaseType.MAIN1, me);
        String err = reject(ctrl, me, true, p);
        Assert.assertTrue(err.startsWith("ILLEGAL ("), "forecast outside the upkeep breaks a rule: " + err);
    }

    /** A play whose target is in a sub-ability (Govern the Guildless's forecast: choose colours, then "target creature
     *  becomes that colour") is offered only when that target exists. The gauntlet's kw28 game offered it with no
     *  creature in play every upkeep, and each time it failed ("Couldn't add to stack, failed to target"). */
    @Test
    public void aPlayWhoseSubAbilityTargetsIsOfferedOnlyWithATarget() {
        Game g = bareGame(PhaseType.UPKEEP);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        for (int i = 0; i < 2; i++) card("Island", me, ZoneType.Battlefield);
        Card govern = card("Govern the Guildless", me, ZoneType.Hand);
        seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        java.util.function.BooleanSupplier offered = () -> LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(me, false, true)
                .spells.stream().anyMatch(sa -> sa.getHostCard() == govern && !sa.isSpell());
        Assert.assertFalse(offered.getAsBoolean(), "no creature in play: the forecast has no target");
        card("Grizzly Bears", rival, ZoneType.Battlefield);
        Assert.assertTrue(offered.getAsBoolean(), "a creature to target: the forecast is offered");
    }

    /** A sub-ability whose legal targets depend on an earlier target cannot be counted before that target is chosen:
     *  Searing Blaze's second target is a creature "that player controls", Goblin Welder's an artifact card in the first
     *  artifact's controller's graveyard. Counted with no first target they have none, so the sub-ability check must
     *  leave them to the cast, or a legal play is hidden (pr-review of #343: Searing Blaze is in the Burn panel deck). */
    @Test
    public void aSubAbilityTargetThatDependsOnTheFirstTargetDoesNotHideThePlay() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        for (int i = 0; i < 2; i++) card("Mountain", me, ZoneType.Battlefield);
        Card blaze = card("Searing Blaze", me, ZoneType.Hand);
        Card welder = card("Goblin Welder", me, ZoneType.Battlefield);
        card("Grizzly Bears", rival, ZoneType.Battlefield);
        card("Ornithopter", rival, ZoneType.Battlefield);
        card("Millstone", rival, ZoneType.Graveyard);
        seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        List<SpellAbility> menu = LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(me, true, true).spells;
        Assert.assertTrue(menu.stream().anyMatch(sa -> sa.getHostCard() == blaze && sa.isSpell()), "Searing Blaze is legal: " + menu);
        Assert.assertTrue(menu.stream().anyMatch(sa -> sa.getHostCard() == welder && !sa.isManaAbility()), "Goblin Welder's ability is legal: " + menu);
    }

    // ------------------------------------------------------------------ the ask

    /** A model that answers a play decision with the next scripted `action` and anything else with option 1, and keeps
     *  what the harness answered each call. */
    static final class ProposalModel extends Recorder {
        static final String PLAN = "PLAN";
        final Deque<String> actions = new ArrayDeque<>();
        final List<String> results = new ArrayList<>();
        int plays;
        ProposalModel(String... actions) { super("CHOICE: 1"); for (String a : actions) this.actions.add(a); }
        @Override public boolean supportsNativeTools() { return true; }
        @Override public NativeTools.Turn chat(String messagesJson, String toolsJson, boolean allowCalls) {
            JsonArray msgs = JsonParser.parseString(messagesJson).getAsJsonArray();
            String first = msgs.get(0).getAsJsonObject().get("content").getAsString();
            prompts.add(first);
            JsonObject last = msgs.get(msgs.size() - 1).getAsJsonObject();
            if ("tool".equals(last.has("role") ? last.get("role").getAsString() : "")) results.add(last.get("content").getAsString());
            String id = "c" + (results.size() + prompts.size());
            List<NativeTools.Call> calls = new ArrayList<>();
            if (!first.contains("Propose ONE")) {
                calls.add(new NativeTools.Call(id, AnswerTool.NAME, "{\"option\":1}"));
                return NativeTools.Turn.ofCalls(calls);
            }
            plays++;
            String next = actions.isEmpty() ? "{\"verb\":\"pass\"}" : actions.poll();
            // PLAN: a reply that only sets the plan (it takes a round and answers nothing); "a||b": one reply, two calls.
            if (PLAN.equals(next)) calls.add(new NativeTools.Call(id, AnswerTool.SET_PLAN, "{\"goal\":\"g\",\"next\":\"n\"}"));
            else {
                String[] each = next.split("\\|\\|");
                for (int k = 0; k < each.length; k++)
                    calls.add(new NativeTools.Call(id + "-" + k, AnswerTool.NAME, "{\"action\":" + each[k] + "}"));
            }
            return NativeTools.Turn.ofCalls(calls);
        }
    }

    /** The menu is not in the prompt; an illegal proposal is told its rule and repaired in the same ask; the repaired
     *  play is the one cast. */
    @Test
    public void aRejectedProposalIsRepairedInTheSameAsk() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Hand);
        Card bolt = card("Lightning Bolt", me, ZoneType.Hand);
        ProposalModel model = new ProposalModel();
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        model.actions.add("{\"verb\":\"cast\",\"card\":\"@" + handle(ctrl, bears) + "\"}");
        model.actions.add("{\"verb\":\"cast\",\"card\":\"@" + handle(ctrl, bolt) + "\",\"targets\":[\"opponent\"]}");
        List<SpellAbility> played = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(played, "the repaired proposal was not played");
        Assert.assertSame(played.get(0).getHostCard(), bolt);
        String ask = model.prompts.stream().filter(p -> p.contains("Propose ONE")).findFirst().orElse("");
        Assert.assertFalse(ask.matches("(?s).*\\n\\s*1\\) CAST.*"), "the menu was shown: " + ask);
        Assert.assertTrue(model.results.stream().anyMatch(r -> r.contains("ILLEGAL (cost)")), String.valueOf(model.results));
        Assert.assertEquals(played.get(0).getTargets().getFirstTargetedPlayer(), g.getPlayers().get(1), "the proposed target");
        Assert.assertFalse(model.prompts.stream().anyMatch(q -> q.contains("Choose the TARGET")), "a target list was shown");
    }

    // ------------------------------------------------------------------ targets

    @Test
    public void aTargetedPlayNamesItsTargetAndAWrongOneBreaksTarget() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Swamp", me, ZoneType.Battlefield);
        card("Swamp", me, ZoneType.Battlefield);
        Card blade = card("Doom Blade", me, ZoneType.Hand);
        Card vampire = card("Vampire Nighthawk", rival, ZoneType.Battlefield);
        Card scout = card("Gladecover Scout", rival, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", rival, ZoneType.Battlefield);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        int b = handle(ctrl, blade);
        AnswerTool.Proposal none = prop("cast", b, null, null, null);
        Assert.assertTrue(reject(ctrl, me, true, none).startsWith("FORMAT: this play targets"), "a target is required");
        AnswerTool.Proposal black = prop("cast", b, null, null, null);
        black.targets = List.of("@" + handle(ctrl, vampire));
        Assert.assertTrue(reject(ctrl, me, true, black).startsWith("ILLEGAL (target)"), "Doom Blade cannot target a black creature");
        AnswerTool.Proposal hex = prop("cast", b, null, null, null);
        hex.targets = List.of("@" + handle(ctrl, scout));
        String err = reject(ctrl, me, true, hex);
        Assert.assertTrue(err.startsWith("ILLEGAL (target)") && err.toLowerCase().contains("hexproof"), err);
        AnswerTool.Proposal ok = prop("cast", b, null, null, null);
        ok.targets = List.of("@" + handle(ctrl, bears));
        int[] hit = {-1};
        Assert.assertNull(verdict(ctrl, me, true, ok, hit));
        Assert.assertSame(ctrl.proposedTarget, bears, "the accepted target waits for the target step");
    }

    /** Four illegal proposals lose the decision to PASS, never to Forge's AI. */
    @Test
    public void fourIllegalProposalsPass() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Hand);
        card("Lightning Bolt", me, ZoneType.Hand);
        ProposalModel model = new ProposalModel();
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        String bad = "{\"verb\":\"cast\",\"card\":\"@" + handle(ctrl, bears) + "\"}";
        for (int i = 0; i < 6; i++) model.actions.add(bad);
        Assert.assertNull(ctrl.chooseSpellAbilityToPlay(), "a lost proposal passes");
        Assert.assertEquals(model.results.stream().filter(r -> r.contains("ILLEGAL (cost)")).count(), 3L,
                "three corrections are sent; the fourth illegal try ends the decision");
        Assert.assertEquals(model.actions.size(), 2, "exactly four tries were taken");
    }

    // ------------------------------------------------------------------ combat: the attack and the blocks

    private static String[] pair(LlmSpikeGui.PlayerControllerLLM ctrl, Card first, Object second) {
        String s = second == null ? null : second instanceof Card ? String.valueOf(handle(ctrl, (Card) second)) : (String) second;
        return new String[] {String.valueOf(handle(ctrl, first)), s};
    }

    private static String attackError(LlmSpikeGui.PlayerControllerLLM ctrl, Player me, Combat combat, String[]... decl) {
        return ctrl.attackProposalError(java.util.Arrays.asList(decl), me, combat, new java.util.LinkedHashMap<>());
    }

    private static String blockError(LlmSpikeGui.PlayerControllerLLM ctrl, Player me, Combat combat, String[]... decl) {
        return ctrl.blockProposalError(java.util.Arrays.asList(decl), me, combat, new ArrayList<>());
    }

    private static void assertCategory(String err, String category) {
        Assert.assertNotNull(err, "accepted, expected ILLEGAL (" + category + ")");
        Assert.assertTrue(err.startsWith("ILLEGAL (" + category + "): "), err);
    }

    /** Each attack rule is refused with its own category, and the attack proposal leaves the combat as it found it. */
    @Test
    public void eachAttackRuleIsNamed() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card ready = card("Hill Giant", me, ZoneType.Battlefield);
        Card sick = card("Grizzly Bears", me, ZoneType.Battlefield);
        sick.setSickness(true);
        Card tapped = card("Savannah Lions", me, ZoneType.Battlefield);
        tapped.setTapped(true);
        Card wall = card("Wall of Stone", me, ZoneType.Battlefield);
        Card mine = card("Llanowar Elves", me, ZoneType.Battlefield);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        Combat combat = new Combat(me);
        assertCategory(attackError(ctrl, me, combat, pair(ctrl, sick, null)), "summoning sickness");
        assertCategory(attackError(ctrl, me, combat, pair(ctrl, tapped, null)), "tapped");
        String def = attackError(ctrl, me, combat, pair(ctrl, wall, null));
        assertCategory(def, "defender");
        Assert.assertTrue(def.contains("defender"), def);
        assertCategory(attackError(ctrl, me, combat, pair(ctrl, ready, "@" + handle(ctrl, mine))), "combat");
        Assert.assertTrue(attackError(ctrl, me, combat, new String[] {"999", null}).startsWith("FORMAT:"), "an unknown handle");
        Assert.assertNull(attackError(ctrl, me, combat, pair(ctrl, ready, "opponent"), pair(ctrl, mine, null)));
        Assert.assertNull(attackError(ctrl, me, combat), "no attack is legal here");
        Assert.assertTrue(combat.getAttackers().isEmpty(), "checking an attack declares nothing");
        // A static of another permanent (Ensnaring Bridge, an empty hand): named with its source.
        card("Ensnaring Bridge", me, ZoneType.Battlefield);
        String bridge = attackError(ctrl, me, combat, pair(ctrl, ready, null));
        assertCategory(bridge, "restriction");
        Assert.assertTrue(bridge.contains("Ensnaring Bridge"), bridge);

        // The attack as a whole: a global limit, a creature that can't attack alone, a creature that must attack.
        g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        me = g.getPlayers().get(0); rival = g.getPlayers().get(1);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        Card giant = card("Hill Giant", me, ZoneType.Battlefield);
        card("Silent Arbiter", rival, ZoneType.Battlefield);
        ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        String arbiter = attackError(ctrl, me, new Combat(me), pair(ctrl, bears, null), pair(ctrl, giant, null));
        assertCategory(arbiter, "number");
        Assert.assertTrue(arbiter.contains("1"), arbiter);

        g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        me = g.getPlayers().get(0);
        Card flunkies = card("Mogg Flunkies", me, ZoneType.Battlefield);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        assertCategory(attackError(ctrl, me, new Combat(me), pair(ctrl, flunkies, null)), "restriction");

        g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        me = g.getPlayers().get(0);
        Card juggernaut = card("Juggernaut", me, ZoneType.Battlefield);
        Card elf = card("Llanowar Elves", me, ZoneType.Battlefield);
        ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        Combat c = new Combat(me);
        g.getPhaseHandler().setCombat(c);   // the engine counts a requirement on the game's combat, as in a game
        String must = attackError(ctrl, me, c, pair(ctrl, elf, null));
        assertCategory(must, "requirement");
        Assert.assertTrue(must.contains("Juggernaut"), must);
        assertCategory(attackError(ctrl, me, c), "requirement");
        Assert.assertNull(attackError(ctrl, me, c, pair(ctrl, juggernaut, null)));
    }

    /** Soundness: every attacker the engine allows, at every defender it allows, is accepted alone. */
    @Test
    public void everyLegalAttackIsAccepted() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Serra Angel", me, ZoneType.Battlefield);
        Card hasty = card("Raging Goblin", me, ZoneType.Battlefield);
        hasty.setSickness(true);
        card("Jace, the Mind Sculptor", rival, ZoneType.Battlefield);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        Combat combat = new Combat(me);
        int checked = 0;
        for (Card c : me.getCreaturesInPlay())
            for (GameEntity d : combat.getDefenders()) {
                if (!CombatUtil.canAttack(c, d)) continue;
                java.util.Map<Card, GameEntity> out = new java.util.LinkedHashMap<>();
                String err = ctrl.attackProposalError(java.util.Collections.singletonList(
                        pair(ctrl, c, d instanceof Card ? "@" + handle(ctrl, (Card) d) : "opponent")), me, combat, out);
                Assert.assertNull(err, c + " -> " + d);
                Assert.assertSame(out.get(c), d);
                checked++;
            }
        Assert.assertEquals(checked, 6, "three creatures, two defenders each");
    }

    /** Each block rule is refused with its own category. */
    @Test
    public void eachBlockRuleIsNamed() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card angel = card("Serra Angel", rival, ZoneType.Battlefield);
        Card brute = card("Boggart Brute", rival, ZoneType.Battlefield);
        Card idle = card("Hill Giant", rival, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        Card spider = card("Giant Spider", me, ZoneType.Battlefield);
        Card tapped = card("Savannah Lions", me, ZoneType.Battlefield);
        tapped.setTapped(true);
        Card ghast = card("Bloodghast", me, ZoneType.Battlefield);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        Combat combat = new Combat(rival);
        combat.addAttacker(angel, me);
        combat.addAttacker(brute, me);
        String fly = blockError(ctrl, me, combat, pair(ctrl, bears, angel));
        assertCategory(fly, "evasion");
        Assert.assertTrue(fly.toLowerCase().contains("flying"), fly);
        Assert.assertNull(blockError(ctrl, me, combat, pair(ctrl, spider, angel)), "reach blocks a flyer");
        assertCategory(blockError(ctrl, me, combat, pair(ctrl, tapped, brute)), "tapped");
        assertCategory(blockError(ctrl, me, combat, pair(ctrl, ghast, brute)), "restriction");
        String menace = blockError(ctrl, me, combat, pair(ctrl, bears, brute));
        assertCategory(menace, "number");
        Assert.assertTrue(menace.toLowerCase().contains("menace") || menace.contains("two or more"), menace);
        Assert.assertNull(blockError(ctrl, me, combat, pair(ctrl, bears, brute), pair(ctrl, spider, brute)), "two block menace");
        assertCategory(blockError(ctrl, me, combat, pair(ctrl, bears, idle)), "combat");
        assertCategory(blockError(ctrl, me, combat, pair(ctrl, idle, brute)), "control");
        Assert.assertTrue(blockError(ctrl, me, combat, new String[] {"999", String.valueOf(handle(ctrl, brute))}).startsWith("FORMAT:"));
        Assert.assertNull(blockError(ctrl, me, combat), "no blocks is legal here");
        Assert.assertTrue(combat.getAllBlockers().isEmpty(), "checking blocks declares nothing");
        String twice = blockError(ctrl, me, combat, pair(ctrl, spider, angel), pair(ctrl, spider, brute));
        assertCategory(twice, "number");

        // A lure: every creature able to block it must.
        g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        me = g.getPlayers().get(0); rival = g.getPlayers().get(1);
        Card unicorn = card("Prized Unicorn", rival, ZoneType.Battlefield);
        Card giant = card("Hill Giant", rival, ZoneType.Battlefield);
        Card mine = card("Grizzly Bears", me, ZoneType.Battlefield);
        ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        combat = new Combat(rival);
        combat.addAttacker(unicorn, me);
        combat.addAttacker(giant, me);
        assertCategory(blockError(ctrl, me, combat, pair(ctrl, mine, giant)), "requirement");
        assertCategory(blockError(ctrl, me, combat), "requirement");
        Assert.assertNull(blockError(ctrl, me, combat, pair(ctrl, mine, unicorn)));
    }

    /** Soundness: every block the engine allows alone is accepted alone. */
    @Test
    public void everyLegalBlockIsAccepted() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card angel = card("Serra Angel", rival, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", rival, ZoneType.Battlefield);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Giant Spider", me, ZoneType.Battlefield);
        card("Wall of Stone", me, ZoneType.Battlefield);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        Combat combat = new Combat(rival);
        combat.addAttacker(angel, me);
        combat.addAttacker(bears, me);
        int checked = 0;
        for (Card b : me.getCreaturesInPlay())
            for (Card a : combat.getAttackers()) {
                if (!CombatUtil.canBlock(a, b, combat)) continue;
                List<Card[]> out = new ArrayList<>();
                Assert.assertNull(blockError(ctrl, me, combat, pair(ctrl, b, a)), b + " blocks " + a);
                checked++;
            }
        Assert.assertEquals(checked, 4, "Bears and Wall block the Bears; the Spider blocks both");
    }

    /** A model that answers the combat decisions with the next scripted declaration and anything else with option 1. */
    static final class CombatModel extends Recorder {
        final Deque<String> declarations = new ArrayDeque<>();
        final List<String> results = new ArrayList<>();
        CombatModel(String... d) { super("CHOICE: 1"); for (String x : d) declarations.add(x); }
        @Override public boolean supportsNativeTools() { return true; }
        @Override public NativeTools.Turn chat(String messagesJson, String toolsJson, boolean allowCalls) {
            JsonArray msgs = JsonParser.parseString(messagesJson).getAsJsonArray();
            String first = msgs.get(0).getAsJsonObject().get("content").getAsString();
            prompts.add(first);
            JsonObject last = msgs.get(msgs.size() - 1).getAsJsonObject();
            if ("tool".equals(last.has("role") ? last.get("role").getAsString() : "")) results.add(last.get("content").getAsString());
            String field = first.contains("`declare_attackers`") ? "declare_attackers" : first.contains("`declare_blockers`") ? "declare_blockers"
                    : first.contains("call `answer` with `blocks`") ? "blocks" : null;
            String args = field == null ? "{\"option\":1}" : "{\"" + field + "\":" + (declarations.isEmpty() ? "[]" : declarations.poll()) + "}";
            List<NativeTools.Call> calls = new ArrayList<>();
            calls.add(new NativeTools.Call("c" + (results.size() + prompts.size()), AnswerTool.NAME, args));
            return NativeTools.Turn.ofCalls(calls);
        }
    }

    /** The attack is proposed, not picked: no list of who can attack, an illegal attack is told its rule and repaired in
     *  the same ask, and the repaired attack is the one declared. */
    @Test
    public void aRejectedAttackIsRepairedInTheSameAsk() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card sick = card("Grizzly Bears", me, ZoneType.Battlefield);
        sick.setSickness(true);
        Card giant = card("Hill Giant", me, ZoneType.Battlefield);
        CombatModel model = new CombatModel();
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        model.declarations.add("[{\"attacker\":\"@" + handle(ctrl, sick) + "\"},{\"attacker\":\"@" + handle(ctrl, giant) + "\"}]");
        model.declarations.add("[{\"attacker\":\"@" + handle(ctrl, giant) + "\",\"defender\":\"opponent\"}]");
        Combat combat = new Combat(me);
        ctrl.declareAttackers(me, combat);
        Assert.assertEquals(combat.getAttackers(), java.util.Collections.singletonList(giant));
        Assert.assertSame(combat.getDefenderByAttacker(giant), rival);
        Assert.assertTrue(model.results.stream().anyMatch(r -> r.contains("ILLEGAL (summoning sickness)")), String.valueOf(model.results));
        String ask = model.prompts.stream().filter(p -> p.contains("`declare_attackers`")).findFirst().orElse("");
        Assert.assertTrue(ask.contains("not shown which creatures can attack"), ask);
        Assert.assertFalse(ask.contains("Hill Giant (3/3)"), "the candidate list was shown: " + ask);
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** Four illegal attacks lose the decision to no attack, never to Forge's AI. */
    @Test
    public void fourIllegalAttacksDeclareNone() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        Player me = g.getPlayers().get(0);
        Card wall = card("Wall of Stone", me, ZoneType.Battlefield);
        card("Hill Giant", me, ZoneType.Battlefield);
        CombatModel model = new CombatModel();
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        for (int i = 0; i < 6; i++) model.declarations.add("[{\"attacker\":\"@" + handle(ctrl, wall) + "\"}]");
        Combat combat = new Combat(me);
        ctrl.declareAttackers(me, combat);
        Assert.assertTrue(combat.getAttackers().isEmpty(), "a lost attack declares none: " + combat.getAttackers());
        Assert.assertEquals(model.results.stream().filter(r -> r.contains("ILLEGAL (defender)")).count(), 3L);
        Assert.assertEquals(model.declarations.size(), 2, "exactly four tries were taken");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** The blocks are proposed, not picked: no list of who can block, an illegal block is told its rule and repaired. */
    @Test
    public void aRejectedBlockIsRepairedInTheSameAsk() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card angel = card("Serra Angel", rival, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", rival, ZoneType.Battlefield);
        Card mine = card("Hill Giant", me, ZoneType.Battlefield);
        CombatModel model = new CombatModel();
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Combat combat = new Combat(rival);
        combat.addAttacker(angel, me);
        combat.addAttacker(bears, me);
        model.declarations.add("[{\"blocker\":\"@" + handle(ctrl, mine) + "\",\"attacker\":\"@" + handle(ctrl, angel) + "\"}]");
        model.declarations.add("[{\"blocker\":\"@" + handle(ctrl, mine) + "\",\"attacker\":\"@" + handle(ctrl, bears) + "\"}]");
        ctrl.declareBlockers(me, combat);
        Assert.assertEquals(combat.getBlockers(bears), java.util.Collections.singletonList(mine));
        Assert.assertTrue(combat.getBlockers(angel).isEmpty());
        Assert.assertTrue(model.results.stream().anyMatch(r -> r.contains("ILLEGAL (evasion)")), String.valueOf(model.results));
        String ask = model.prompts.stream().filter(p -> p.contains("`declare_blockers`")).findFirst().orElse("");
        Assert.assertTrue(ask.contains("not shown which creatures can block"), ask);
        Assert.assertFalse(ask.contains("YOUR BLOCKERS") || ask.contains("Your blockers:"), "the blocker list was shown: " + ask);
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** Four illegal block sets lose the decision to no blocks (the fixed fallback), never to Forge's AI. */
    @Test
    public void fourIllegalBlocksDeclareNone() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card angel = card("Serra Angel", rival, ZoneType.Battlefield);
        Card mine = card("Grizzly Bears", me, ZoneType.Battlefield);
        CombatModel model = new CombatModel();
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Combat combat = new Combat(rival);
        combat.addAttacker(angel, me);
        // The Bears cannot block the flyer, yet ableBlockers is empty, so give the seat a legal blocker to be asked at all.
        Card spider = card("Giant Spider", me, ZoneType.Battlefield);
        for (int i = 0; i < 6; i++)
            model.declarations.add("[{\"blocker\":\"@" + handle(ctrl, mine) + "\",\"attacker\":\"@" + handle(ctrl, angel) + "\"}]");
        ctrl.declareBlockers(me, combat);
        Assert.assertTrue(combat.getAllBlockers().isEmpty(), "a lost block set declares none: " + combat.getAllBlockers());
        Assert.assertFalse(combat.getAllBlockers().contains(spider));
        Assert.assertEquals(model.declarations.size(), 2, "exactly four tries were taken");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** A "must be blocked if able" attacker blocked by one creature, and another creature blocking a second attacker, is
     *  legal (CR 509.1c: the requirement is obeyed). Each pair used to be judged on a combat with no blocks yet, where the
     *  requirement looked unmet, so the second block was refused (pr-review of #349). */
    @Test
    public void aBlockBesideAMustBeBlockedAttackerIsAccepted() {
        MustBeBlocked t = new MustBeBlocked(new Recorder("CHOICE: 1"));
        Assert.assertNull(blockError(t.ctrl, t.me, t.combat, pair(t.ctrl, t.wall, t.protector), pair(t.ctrl, t.giant, t.bears)));
        Assert.assertNull(blockError(t.ctrl, t.me, t.combat, pair(t.ctrl, t.giant, t.bears), pair(t.ctrl, t.wall, t.protector)),
                "in either order");
        assertCategory(blockError(t.ctrl, t.me, t.combat, pair(t.ctrl, t.giant, t.bears)), "requirement");
        Assert.assertTrue(t.combat.getAllBlockers().isEmpty(), "checking blocks declares nothing");
    }

    /** Gaea's Protector ("must be blocked if able") and Grizzly Bears attack; the seat has Wall of Stone and Hill Giant. */
    static final class MustBeBlocked {
        final Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        final Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        final Card protector = card("Gaea's Protector", rival, ZoneType.Battlefield);
        final Card bears = card("Grizzly Bears", rival, ZoneType.Battlefield);
        final Card wall = card("Wall of Stone", me, ZoneType.Battlefield);
        final Card giant = card("Hill Giant", me, ZoneType.Battlefield);
        final LlmSpikeGui.PlayerControllerLLM ctrl;
        final Combat combat = new Combat(rival);
        MustBeBlocked(Recorder model) {
            ctrl = seat(g, model, model);
            combat.addAttacker(protector, me);
            combat.addAttacker(bears, me);
        }
    }

    /** The text grammar's BLOCKS: line (a backend without function calling) is judged as a set too, so the order the pairs
     *  are written in does not decide: Hill Giant's block, written first, was refused before the Wall's (pr-review #349). */
    @Test
    public void theTextBlocksAreJudgedAsASetInEitherOrder() {
        AnswerTool.PROPOSE_FOR_TEST = false;
        for (String line : new String[] {"BLOCKS: Wall of Stone->Gaea's Protector, Hill Giant->Grizzly Bears",
                                         "BLOCKS: Hill Giant->Grizzly Bears, Wall of Stone->Gaea's Protector"}) {
            MustBeBlocked t = new MustBeBlocked(new Recorder(line));
            t.ctrl.declareBlockers(t.me, t.combat);
            Assert.assertEquals(t.combat.getBlockers(t.protector), java.util.Collections.singletonList(t.wall), line);
            Assert.assertEquals(t.combat.getBlockers(t.bears), java.util.Collections.singletonList(t.giant), line);
        }
    }

    /** The menu's own block check had the same flaw: off the arm, the same legal pair of blocks is accepted as answered. */
    @Test
    public void theMenuAcceptsABlockBesideAMustBeBlockedAttacker() {
        AnswerTool.PROPOSE_FOR_TEST = false;
        CombatModel model = new CombatModel();
        MustBeBlocked t = new MustBeBlocked(model);
        // The menu numbers blockers in ableBlockers order (the seat's creatures as listed) and attackers as declared.
        List<Card> able = LlmSpikeGui.PlayerControllerLLM.ableBlockers(t.me, t.combat);
        model.declarations.add("[{\"blocker\":" + (able.indexOf(t.wall) + 1) + ",\"attacker\":1},{\"blocker\":"
                + (able.indexOf(t.giant) + 1) + ",\"attacker\":2}]");
        t.ctrl.declareBlockers(t.me, t.combat);
        Assert.assertEquals(t.combat.getBlockers(t.protector), java.util.Collections.singletonList(t.wall));
        Assert.assertEquals(t.combat.getBlockers(t.bears), java.util.Collections.singletonList(t.giant), "the answer as given: " + model.results);
    }

    /** The text grammar under a global block cap (Silent Arbiter, one blocker): two blocks written add nothing, the
     *  re-ask carries the cap's reason, and a second breach ends in the fixed fallback, recorded as the harness's. */
    @Test
    public void theTextBlocksOverACapAreAskedAgainThenFallBack() {
        AnswerTool.PROPOSE_FOR_TEST = false;
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card giant = card("Hill Giant", rival, ZoneType.Battlefield);
        card("Silent Arbiter", rival, ZoneType.Battlefield);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Savannah Lions", me, ZoneType.Battlefield);
        Recorder model = new Recorder("BLOCKS: Grizzly Bears->Hill Giant, Savannah Lions->Hill Giant");
        Assert.assertFalse(model.supportsNativeTools(), "the text path: a bridge without function calling");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Combat combat = new Combat(rival);
        combat.addAttacker(giant, me);
        ctrl.declareBlockers(me, combat);
        Assert.assertTrue(combat.getAllBlockers().isEmpty(), "over the cap twice: the fallback blocks none");
        List<String> asks = model.prompts.stream().filter(p -> p.contains("declare blockers")).collect(java.util.stream.Collectors.toList());
        Assert.assertEquals(asks.size(), 2, "asked once more");
        Assert.assertTrue(asks.get(0).contains("End with EXACTLY: BLOCKS:"), "the BLOCKS: grammar was asked");
        Assert.assertTrue(asks.get(1).contains("no more than 1 creature can block each combat"), asks.get(1));
        Assert.assertTrue(ctrl.autoPicksCount() >= 1, "the fallback is recorded as the harness's own");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** A pair the blocker can never make (a flyer) does not use up its name: with one Grizzly Bears, "Bears->Serra Angel,
     *  Bears->Goblin Raider" blocks the Raider, as before the set was judged as a whole (pr-review of #351). */
    @Test
    public void aTextBlockTheBlockerCannotMakeLeavesItsNameForTheNext() {
        AnswerTool.PROPOSE_FOR_TEST = false;
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card angel = card("Serra Angel", rival, ZoneType.Battlefield);
        Card raider = card("Goblin Raider", rival, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        Recorder model = new Recorder("BLOCKS: Grizzly Bears->Serra Angel, Grizzly Bears->Goblin Raider");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Combat combat = new Combat(rival);
        combat.addAttacker(angel, me);
        combat.addAttacker(raider, me);
        ctrl.declareBlockers(me, combat);
        Assert.assertEquals(combat.getBlockers(raider), java.util.Collections.singletonList(bears));
        Assert.assertTrue(combat.getBlockers(angel).isEmpty());
    }

    /** A creature that can block an additional creature blocks two; a third is over its limit. */
    @Test
    public void aCreatureThatBlocksAnAdditionalCreatureBlocksTwo() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card guard = card("Night Market Guard", me, ZoneType.Battlefield);
        Card lions = card("Savannah Lions", rival, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", rival, ZoneType.Battlefield);
        Card giant = card("Hill Giant", rival, ZoneType.Battlefield);
        g.getAction().checkStateEffects(true);   // the Guard's CanBlockAmount is a continuous effect
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        Combat combat = new Combat(rival);
        combat.addAttacker(lions, me);
        combat.addAttacker(bears, me);
        combat.addAttacker(giant, me);
        Assert.assertNull(blockError(ctrl, me, combat, pair(ctrl, guard, lions), pair(ctrl, guard, bears)));
        assertCategory(blockError(ctrl, me, combat, pair(ctrl, guard, lions), pair(ctrl, guard, bears), pair(ctrl, guard, giant)), "number");
    }

    /** A must-attack creature whose attack has a cost (Propaganda) is not required to attack (CR 508.1d): no attack is
     *  legal. */
    @Test
    public void aRequirementWithAnAttackCostDoesNotForceTheAttack() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Juggernaut", me, ZoneType.Battlefield);
        card("Propaganda", rival, ZoneType.Battlefield);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        Combat c = new Combat(me);
        g.getPhaseHandler().setCombat(c);
        Assert.assertNull(attackError(ctrl, me, c), "an empty attack is legal when the requirement's attack costs");
    }

    /** Checking an attack leaves no trace on the combat: no attacker, no empty band (removeFromCombat keeps a band). And a
     *  defender may be named by a bare number, or left blank for the opponent. */
    @Test
    public void checkingAnAttackLeavesNoBandAndTakesABareHandle() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card giant = card("Hill Giant", me, ZoneType.Battlefield);
        Card jace = card("Jace, the Mind Sculptor", rival, ZoneType.Battlefield);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        Combat combat = new Combat(me);
        java.util.Map<Card, GameEntity> out = new java.util.LinkedHashMap<>();
        Assert.assertNull(ctrl.attackProposalError(java.util.Collections.singletonList(
                new String[] {String.valueOf(handle(ctrl, giant)), String.valueOf(handle(ctrl, jace))}), me, combat, out));
        Assert.assertSame(out.get(giant), jace, "a bare number is a handle");
        Assert.assertNull(ctrl.attackProposalError(java.util.Collections.singletonList(
                new String[] {String.valueOf(handle(ctrl, giant)), " "}), me, combat, out));
        Assert.assertSame(out.get(giant), rival, "a blank defender is the opponent");
        Assert.assertTrue(combat.getAttackingBandsOf(rival).isEmpty() && combat.getAttackingBandsOf(jace).isEmpty(),
                "the check left a band behind");
    }

    /** A planeswalker named in the proposal is what the creature attacks, through the whole ask. */
    @Test
    public void anAttackOnAPlaneswalkerIsProposedThroughTheAsk() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card giant = card("Hill Giant", me, ZoneType.Battlefield);
        Card jace = card("Jace, the Mind Sculptor", rival, ZoneType.Battlefield);
        CombatModel model = new CombatModel();
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        model.declarations.add("[{\"attacker\":\"@" + handle(ctrl, giant) + "\",\"defender\":\"@" + handle(ctrl, jace) + "\"}]");
        Combat combat = new Combat(me);
        ctrl.declareAttackers(me, combat);
        Assert.assertSame(combat.getDefenderByAttacker(giant), jace);
        Assert.assertFalse(model.prompts.stream().anyMatch(p -> p.contains("Where does")), "no attack-target list was asked");
    }

    /** Off the arm the combat asks are the menu's: the numbered candidates, answered with `options` and `blocks`. */
    @Test
    public void offTheArmCombatIsTheMenu() {
        AnswerTool.PROPOSE_FOR_TEST = false;
        Game g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        Player me = g.getPlayers().get(0);
        card("Hill Giant", me, ZoneType.Battlefield);
        CombatModel model = new CombatModel();
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        ctrl.declareAttackers(me, new Combat(me));
        Assert.assertFalse(model.prompts.stream().anyMatch(p -> p.contains("declare_attackers")), "a combat proposal off the arm");
        Assert.assertTrue(model.prompts.stream().anyMatch(p -> p.contains("Hill Giant (3/3)")), "the menu lists the attackers");
    }

    // ------------------------------------------------------------------ the terse refusal (PROPOSE_TERSE)

    /** PROPOSE_TERSE: a rules refusal is told only that the play is illegal, Kaggle Game Arena's form, so the message's
     *  detail can be measured. The engine's diagnosis is still recorded (its category counts the rule broken in both
     *  arms); a form error keeps its text, since it says what is malformed, not which rule applies. */
    @Test
    public void aTerseRefusalNamesNoRuleAndTheRowKeepsTheDiagnosis() {
        AnswerTool.TERSE_FOR_TEST = true;
        Assert.assertTrue(AnswerTool.protocolVersion().contains("+propose+terse"), AnswerTool.protocolVersion());
        Assert.assertFalse(AnswerTool.instructions().contains("which rule it breaks"), "the preamble promises the rule");
        Assert.assertTrue(AnswerTool.instructions().contains("not why"), AnswerTool.instructions());
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Hand);
        Card bolt = card("Lightning Bolt", me, ZoneType.Hand);
        ProposalModel model = new ProposalModel();
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        model.actions.add("{\"verb\":\"cast\",\"card\":\"@99\"}");
        model.actions.add("{\"verb\":\"cast\",\"card\":\"@" + handle(ctrl, bears) + "\"}");
        model.actions.add("{\"verb\":\"cast\",\"card\":\"@" + handle(ctrl, bolt) + "\",\"targets\":[\"opponent\"]}");
        List<SpellAbility> played = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(played, "the repaired proposal was not played");
        Assert.assertSame(played.get(0).getHostCard(), bolt);
        Assert.assertTrue(model.results.stream().anyMatch(r -> r.contains(AnswerTool.TERSE_REJECTION)), String.valueOf(model.results));
        Assert.assertFalse(model.results.stream().anyMatch(r -> r.contains("ILLEGAL (")), "a rule was named: " + model.results);
        Assert.assertTrue(model.results.stream().anyMatch(r -> r.contains("FORMAT: no object @99")), "a form error keeps its text");

        List<String[]> calls = new ArrayList<>();
        calls.add(new String[] {"{\"action\":{\"verb\":\"cast\",\"card\":\"@4\"}}", "ILLEGAL (timing): a sorcery."});
        calls.add(new String[] {"{\"action\":{\"verb\":\"pass\"}}", null});
        JsonObject row = JsonParser.parseString("{\"x\":1" + LlmSpikeGui.PlayerControllerLLM.proposalRecord(calls, "action") + "}").getAsJsonObject();
        JsonObject refused = row.getAsJsonArray("proposals").get(0).getAsJsonObject();
        Assert.assertEquals(refused.get("verdict").getAsString(), "illegal");
        Assert.assertEquals(refused.get("category").getAsString(), "timing", "the diagnosis is kept for the count");
        Assert.assertEquals(refused.get("error_shown").getAsString(), AnswerTool.TERSE_REJECTION);
        String schema = AnswerTool.actionProperty().get("description").getAsString();
        Assert.assertFalse(schema.contains("which rule"), "the schema promises the rule: " + schema);

        AnswerTool.TERSE_FOR_TEST = false;
        Assert.assertFalse(LlmSpikeGui.PlayerControllerLLM.proposalRecord(calls, "action").contains("error_shown"), "the detailed arm's row moved");
        Assert.assertTrue(AnswerTool.instructions().contains("which rule it breaks"));
        // The detailed arm's promises, byte for byte as before the terse arm (pr-review of #353): the shared constants
        // must not move them.
        Assert.assertTrue(AnswerTool.instructions().contains("If the rules do not allow it now you are told which rule it "
                + "breaks (timing, cost, zone, target, land drop, control, restriction) and may propose again, up to "),
                "the PLAYS promise moved");
        Assert.assertTrue(AnswerTool.instructions().contains("if the rules forbid it you are told which rule it breaks "
                + "(such as summoning sickness, tapped, defender, flying, menace, a requirement) and may declare again, "),
                "the COMBAT promise moved");
        Assert.assertTrue(AnswerTool.actionProperty().get("description").getAsString().contains("which rule it breaks"));
        Assert.assertFalse(AnswerTool.protocolVersion().contains("+terse"));
    }

    /** The attack and the blocks are refused alike under PROPOSE_TERSE: "that declaration", no rule; the row keeps it. */
    @Test
    public void aTerseCombatRefusalNamesNoRule() {
        AnswerTool.TERSE_FOR_TEST = true;
        Game g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        Player me = g.getPlayers().get(0);
        Card sick = card("Grizzly Bears", me, ZoneType.Battlefield);
        sick.setSickness(true);
        Card giant = card("Hill Giant", me, ZoneType.Battlefield);
        CombatModel model = new CombatModel();
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        model.declarations.add("[{\"attacker\":\"@" + handle(ctrl, sick) + "\"}]");
        model.declarations.add("[{\"attacker\":\"@" + handle(ctrl, giant) + "\",\"defender\":\"opponent\"}]");
        Combat combat = new Combat(me);
        ctrl.declareAttackers(me, combat);
        Assert.assertEquals(combat.getAttackers(), java.util.Collections.singletonList(giant), "repaired after the terse refusal");
        Assert.assertTrue(model.results.stream().anyMatch(r -> r.contains(AnswerTool.TERSE_COMBAT_REJECTION)), String.valueOf(model.results));
        Assert.assertFalse(model.results.stream().anyMatch(r -> r.contains("ILLEGAL (")), "a rule was named: " + model.results);
        Assert.assertFalse(AnswerTool.instructions().contains("which rule"), "the COMBAT paragraph promises the rule");
        List<String[]> calls = new ArrayList<>();
        calls.add(new String[] {"{\"declare_attackers\":[{\"attacker\":\"@3\"}]}", "ILLEGAL (tapped): it is tapped."});
        String rec = LlmSpikeGui.PlayerControllerLLM.proposalRecord(calls, AnswerTool.DECLARE_ATTACKERS);
        Assert.assertTrue(rec.contains("\"category\":\"tapped\"") && rec.contains(AnswerTool.TERSE_COMBAT_REJECTION), rec);
    }

    // ------------------------------------------------------------------ the refusal notebook (PROPOSE_NOTEBOOK)

    /** Two play decisions in one game, each a refused Grizzly Bears (no green mana) and then Lightning Bolt: the play
     *  decision prompts, one per decision. */
    private static List<String> twoPlayDecisions(boolean notebook) {
        AnswerTool.NOTEBOOK_FOR_TEST = notebook;
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Hand);
        Card bolt = card("Lightning Bolt", me, ZoneType.Hand);
        ProposalModel model = new ProposalModel();
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        String refused = "{\"verb\":\"cast\",\"card\":\"@" + handle(ctrl, bears) + "\"}";
        String ok = "{\"verb\":\"cast\",\"card\":\"@" + handle(ctrl, bolt) + "\",\"targets\":[\"opponent\"]}";
        for (int i = 0; i < 2; i++) {
            model.actions.add(refused);
            model.actions.add(ok);
            Assert.assertNotNull(ctrl.chooseSpellAbilityToPlay(), "decision " + (i + 1) + " was not played");
        }
        List<String> asks = playAsks(model);
        Assert.assertEquals(asks.size(), 2, "two play decisions were asked");
        return asks;
    }

    /** The play decision prompts of {@code model}, one per distinct ask, in order. */
    private static List<String> playAsks(ProposalModel model) {
        List<String> asks = new ArrayList<>();
        for (String q : model.prompts)
            if (q.contains("Propose ONE") && (asks.isEmpty() || !asks.get(asks.size() - 1).equals(q))) asks.add(q);
        return asks;
    }

    private static int entries(String prompt) { return prompt.split("\n- turn ", -1).length - 1; }

    /** PROPOSE_NOTEBOOK: a later decision's prompt lists the game's earlier rules refusals as the seat was told them (the
     *  repeat across decisions: eval/research/2026-11-03-propose-condition-prior-work.md, "Repeated refusals in every
     *  propose log on disk"). A decision's own refusals are noted after it, so its prompt lists only earlier ones. */
    @Test
    public void aLaterDecisionCarriesAnEarlierRefusal() {
        List<String> asks = twoPlayDecisions(true);
        Assert.assertFalse(asks.get(0).contains("REFUSED EARLIER"), "the first decision has nothing to list: " + asks.get(0));
        String second = asks.get(1);
        Assert.assertTrue(second.contains("REFUSED EARLIER (harness record"), second);
        Assert.assertTrue(second.contains("every one, oldest first"), "the section says it is complete: " + second);
        Assert.assertTrue(java.util.regex.Pattern.compile("\n- turn \\d+, [^\n]*: you proposed cast @\\d+; you were told: ILLEGAL \\(cost\\)")
                .matcher(second).find(), "the refusal with its rule: " + second);
        Assert.assertEquals(second.split("\n- turn ", -1).length - 1, 1, "one entry, the first decision's one refusal");
        Assert.assertTrue(second.indexOf("REFUSED EARLIER") < second.indexOf("DECISION:"), "the section precedes the decision");
        Assert.assertTrue(AnswerTool.instructions().contains("REFUSED EARLIER"), "the preamble describes the section");
        Assert.assertTrue(AnswerTool.protocolVersion().contains("+propose+notebook"), AnswerTool.protocolVersion());
    }

    /** The terse notebook names no rule: it never says more than the arm's refusal did. */
    @Test
    public void aTerseNotebookNamesNoRule() {
        AnswerTool.TERSE_FOR_TEST = true;
        String second = twoPlayDecisions(true).get(1);
        Assert.assertTrue(second.contains("you were told: " + AnswerTool.TERSE_REJECTION), second);
        Assert.assertFalse(second.contains("ILLEGAL ("), "the notebook named a rule: " + second);
        Assert.assertTrue(AnswerTool.protocolVersion().contains("+terse+notebook"), AnswerTool.protocolVersion());
    }

    /** Off the arm every prompt byte is the same: the on-arm prompts with the section cut out are the off-arm prompts; the
     *  switch does nothing without PROPOSE_ACTIONS. */
    @Test
    public void offTheNotebookEveryPromptIsUnchanged() {
        List<String> on = twoPlayDecisions(true);
        List<String> off = twoPlayDecisions(false);
        Assert.assertFalse(off.get(1).contains("REFUSED EARLIER"), off.get(1));
        Assert.assertEquals(on.get(0), off.get(0));
        Assert.assertNotEquals(on.get(1), off.get(1));
        Assert.assertEquals(on.get(1).replaceAll("\nREFUSED EARLIER \\([^\n]*\\):\n(- [^\n]*\n)+", ""), off.get(1));
        Assert.assertFalse(AnswerTool.instructions().contains("REFUSED EARLIER"), "the preamble moved");
        Assert.assertFalse(AnswerTool.protocolVersion().contains("+notebook"));
        AnswerTool.NOTEBOOK_FOR_TEST = true;
        AnswerTool.PROPOSE_FOR_TEST = false;
        Assert.assertFalse(AnswerTool.proposeNotebook(), "the notebook without the propose arm");
        Assert.assertEquals(AnswerTool.notebookSection(List.of("turn 1, x: you proposed y; you were told: z")), "");
    }

    /** The attack and the blocks are noted like a play; a form error is not a rules refusal and is not noted; the section
     *  shows every entry, oldest first: nothing the seat was told is cut (STANDARDS section 8). */
    @Test
    public void theNotebookNotesCombatAndShowsEveryEntry() {
        AnswerTool.NOTEBOOK_FOR_TEST = true;
        List<String[]> calls = new ArrayList<>();
        calls.add(new String[] {"{\"declare_attackers\":[{\"attacker\":\"@3\"}]}", "ILLEGAL (tapped): it is tapped."});
        calls.add(new String[] {"{\"declare_attackers\":[{\"attacker\":\"@99\"}]}", "FORMAT: no object @99 is in view now."});
        calls.add(new String[] {"{\"declare_attackers\":[]}", null});
        Assert.assertEquals(AnswerTool.notebookEntries(calls, AnswerTool.Kind.ATTACK_PROPOSAL, "turn 4, declare attackers"),
                List.of("turn 4, declare attackers: you proposed the attack @3 -> opponent; you were told: ILLEGAL (tapped): it is tapped."));
        List<String[]> blocks = new ArrayList<>();
        blocks.add(new String[] {"{\"declare_blockers\":[{\"blocker\":\"@5\",\"attacker\":\"@8\"}]}", "ILLEGAL (flying): it has flying."});
        AnswerTool.TERSE_FOR_TEST = true;
        Assert.assertEquals(AnswerTool.notebookEntries(blocks, AnswerTool.Kind.BLOCK_PROPOSAL, "turn 5, declare blockers"),
                List.of("turn 5, declare blockers: you proposed the blocks @5 blocks @8; you were told: "
                        + AnswerTool.TERSE_COMBAT_REJECTION));
        List<String> many = new ArrayList<>();
        for (int i = 1; i <= 30; i++) many.add("entry " + i);
        String section = AnswerTool.notebookSection(many);
        int at = -1;
        for (int i = 1; i <= 30; i++) {
            int next = section.indexOf("\n- entry " + i + "\n");
            Assert.assertTrue(next > at, "entry " + i + " is missing or out of order: " + section);
            at = next;
        }
        Assert.assertEquals(AnswerTool.notebookSection(new ArrayList<>()), "", "an empty notebook shows no section");
    }

    /** The attack decision carries the notebook too: a second attack lists the first's refusal. */
    @Test
    public void aLaterAttackCarriesAnEarlierRefusal() {
        AnswerTool.NOTEBOOK_FOR_TEST = true;
        Game g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        Player me = g.getPlayers().get(0);
        Card sick = card("Grizzly Bears", me, ZoneType.Battlefield);
        sick.setSickness(true);
        Card giant = card("Hill Giant", me, ZoneType.Battlefield);
        CombatModel model = new CombatModel();
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        for (int i = 0; i < 2; i++) {
            model.declarations.add("[{\"attacker\":\"@" + handle(ctrl, sick) + "\"}]");
            model.declarations.add("[{\"attacker\":\"@" + handle(ctrl, giant) + "\",\"defender\":\"opponent\"}]");
            ctrl.declareAttackers(me, new Combat(me));
        }
        String last = model.prompts.get(model.prompts.size() - 1);
        Assert.assertTrue(last.contains("REFUSED EARLIER") && last.contains("you proposed the attack @" + handle(ctrl, sick)
                + " -> opponent; you were told: ILLEGAL (summoning sickness)"), last);
        Assert.assertFalse(model.prompts.get(0).contains("REFUSED EARLIER"), model.prompts.get(0));
    }

    /** The blocks decision carries the notebook too: a second block lists the first's refusal. */
    @Test
    public void aLaterBlockCarriesAnEarlierRefusal() {
        AnswerTool.NOTEBOOK_FOR_TEST = true;
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card angel = card("Serra Angel", rival, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", rival, ZoneType.Battlefield);
        Card mine = card("Hill Giant", me, ZoneType.Battlefield);
        CombatModel model = new CombatModel();
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        String blocker = "@" + handle(ctrl, mine), flyer = "@" + handle(ctrl, angel);
        for (int i = 0; i < 2; i++) {
            Combat combat = new Combat(rival);
            combat.addAttacker(angel, me);
            combat.addAttacker(bears, me);
            model.declarations.add("[{\"blocker\":\"" + blocker + "\",\"attacker\":\"" + flyer + "\"}]");
            model.declarations.add("[{\"blocker\":\"" + blocker + "\",\"attacker\":\"@" + handle(ctrl, bears) + "\"}]");
            ctrl.declareBlockers(me, combat);
        }
        String last = model.prompts.get(model.prompts.size() - 1);
        Assert.assertTrue(last.contains("REFUSED EARLIER") && last.contains(", declare blockers: you proposed the blocks "
                + blocker + " blocks " + flyer + "; you were told: ILLEGAL (evasion)"), last);
        Assert.assertEquals(entries(last), 1, last);
        Assert.assertFalse(model.prompts.get(0).contains("REFUSED EARLIER"), model.prompts.get(0));
    }

    /** Only a refusal the seat was told is noted (pr-review of #357). The fourth refusal ends the decision before its
     *  result is sent, so the later decision lists the first three only. */
    @Test
    public void theRefusalThatEndsADecisionIsNotNoted() {
        AnswerTool.NOTEBOOK_FOR_TEST = true;
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Hand);
        Card bolt = card("Lightning Bolt", me, ZoneType.Hand);
        ProposalModel model = new ProposalModel();
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        for (int i = 0; i < AnswerTool.MAX_INVALID_PROPOSALS; i++) model.actions.add("{\"verb\":\"cast\",\"card\":\"@" + handle(ctrl, bears) + "\"}");
        Assert.assertNull(ctrl.chooseSpellAbilityToPlay(), "four refusals pass");
        Assert.assertEquals(model.results.stream().filter(r -> r.contains("ILLEGAL (cost)")).count(), 3L, "three were sent");
        card("Mountain", me, ZoneType.Hand);   // another spot, so the next window is asked
        model.actions.add("{\"verb\":\"cast\",\"card\":\"@" + handle(ctrl, bolt) + "\",\"targets\":[\"opponent\"]}");
        Assert.assertNotNull(ctrl.chooseSpellAbilityToPlay());
        List<String> asks = playAsks(model);
        String later = asks.get(asks.size() - 1);
        Assert.assertEquals(entries(later), AnswerTool.MAX_INVALID_PROPOSALS - 1, later);
    }

    /** An illegal `answer` call followed by a legal one in the same reply is told "Not used", not its refusal: not noted. */
    @Test
    public void anIllegalCallBesideTheAnswerIsNotNoted() {
        AnswerTool.NOTEBOOK_FOR_TEST = true;
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Hand);
        Card bolt = card("Lightning Bolt", me, ZoneType.Hand);
        ProposalModel model = new ProposalModel();
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        String refused = "{\"verb\":\"cast\",\"card\":\"@" + handle(ctrl, bears) + "\"}";
        String ok = "{\"verb\":\"cast\",\"card\":\"@" + handle(ctrl, bolt) + "\",\"targets\":[\"opponent\"]}";
        model.actions.add(refused + "||" + ok);
        Assert.assertNotNull(ctrl.chooseSpellAbilityToPlay());
        model.actions.add(ok);
        Assert.assertNotNull(ctrl.chooseSpellAbilityToPlay());
        List<String> asks = playAsks(model);
        Assert.assertEquals(asks.size(), 2, "two play decisions were asked");
        Assert.assertFalse(asks.get(1).contains("REFUSED EARLIER"), asks.get(1));
    }

    /** A refusal sent in the last round, with no model turn after it, was never read: not noted. A tool ask whose rounds
     *  run out returns no reply, so the play passes (play(bridge-down)) and the text retry is never asked; the refusal
     *  read in an earlier round of that ask is noted, and only after the decision. */
    @Test
    public void aRefusalNeverReadIsNotNoted() {
        AnswerTool.NOTEBOOK_FOR_TEST = true;
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Hand);
        Card giant = card("Hill Giant", me, ZoneType.Hand);
        Card bolt = card("Lightning Bolt", me, ZoneType.Hand);
        ProposalModel model = new ProposalModel();
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        String read = "{\"verb\":\"cast\",\"card\":\"@" + handle(ctrl, bears) + "\"}";
        String unread = "{\"verb\":\"cast\",\"card\":\"@" + handle(ctrl, giant) + "\"}";
        String ok = "{\"verb\":\"cast\",\"card\":\"@" + handle(ctrl, bolt) + "\",\"targets\":[\"opponent\"]}";
        int rounds = AnswerTool.MAX_ROUNDS + AnswerTool.MAX_INVALID_PROPOSALS - 2;
        model.actions.add(read);
        for (int i = 0; i < rounds - 2; i++) model.actions.add(ProposalModel.PLAN);
        model.actions.add(unread);   // the last round: its refusal is appended and no turn follows
        Assert.assertNull(ctrl.chooseSpellAbilityToPlay(), "an ask out of rounds passes");
        Assert.assertTrue(model.actions.isEmpty(), "every round was taken: " + model.actions.size() + " left");
        Assert.assertEquals(playAsks(model).size(), 1, "the text retry was not asked: one proposal decision only");
        model.actions.add(ok);
        card("Mountain", me, ZoneType.Hand);   // another spot, so the next window is asked
        Assert.assertNotNull(ctrl.chooseSpellAbilityToPlay());
        List<String> asks = playAsks(model);
        Assert.assertEquals(asks.size(), 2, "two play decisions were asked");
        String later = asks.get(1);
        Assert.assertEquals(entries(later), 1, later);
        Assert.assertTrue(later.contains("you proposed cast @" + handle(ctrl, bears) + ";"), later);
        Assert.assertFalse(later.contains("@" + handle(ctrl, giant) + ";"), "the unread refusal was noted: " + later);
    }
}
