package forge.spike;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;

import forge.game.Game;
import forge.game.card.Card;
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
        JsonObject row = JsonParser.parseString("{\"x\":1" + LlmSpikeGui.PlayerControllerLLM.proposalRecord(calls) + "}").getAsJsonObject();
        JsonArray ps = row.getAsJsonArray("proposals");
        Assert.assertEquals(ps.size(), 3);
        Assert.assertEquals(ps.get(0).getAsJsonObject().get("verdict").getAsString(), "format");
        Assert.assertEquals(ps.get(1).getAsJsonObject().get("category").getAsString(), "timing");
        Assert.assertEquals(ps.get(2).getAsJsonObject().get("verdict").getAsString(), "accepted");
        Assert.assertFalse(row.get("first_try_legal").getAsBoolean(), "the first well-formed proposal was illegal");
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
        java.util.function.Supplier<Boolean> offered = () -> LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(me, false, true)
                .spells.stream().anyMatch(sa -> sa.getHostCard() == govern && !sa.isSpell());
        Assert.assertFalse(offered.get(), "no creature in play: the forecast has no target");
        card("Grizzly Bears", rival, ZoneType.Battlefield);
        Assert.assertTrue(offered.get(), "a creature to target: the forecast is offered");
    }

    // ------------------------------------------------------------------ the ask

    /** A model that answers a play decision with the next scripted `action` and anything else with option 1, and keeps
     *  what the harness answered each call. */
    static final class ProposalModel extends Recorder {
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
            String args;
            if (first.contains("Propose ONE")) {
                plays++;
                args = "{\"action\":" + (actions.isEmpty() ? "{\"verb\":\"pass\"}" : actions.poll()) + "}";
            } else {
                args = "{\"option\":1}";
            }
            List<NativeTools.Call> calls = new ArrayList<>();
            calls.add(new NativeTools.Call("c" + (results.size() + prompts.size()), AnswerTool.NAME, args));
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
}
