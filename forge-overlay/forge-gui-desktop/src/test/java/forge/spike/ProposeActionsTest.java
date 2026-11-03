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
        Assert.assertTrue(AnswerTool.validate(s, "{\"option\":1}").error.contains("`action`"));
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
            Card c = sa.getHostCard();
            AnswerTool.Proposal p;
            if (sa.isSpell()) {
                p = prop("cast", handle(ctrl, c), null, null, LlmSpikeGui.PlayerControllerLLM.castWay(sa));
            } else {
                int ab = LlmSpikeGui.PlayerControllerLLM.proposalAbilities(c).indexOf(sa) + 1;
                if (ab == 0) ab = 1 + indexByText(LlmSpikeGui.PlayerControllerLLM.proposalAbilities(c), sa);
                p = prop("activate", handle(ctrl, c), ab, null, null);
            }
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
        model.actions.add("{\"verb\":\"cast\",\"card\":\"@" + handle(ctrl, bolt) + "\"}");
        List<SpellAbility> played = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(played, "the repaired proposal was not played");
        Assert.assertSame(played.get(0).getHostCard(), bolt);
        String ask = model.prompts.stream().filter(p -> p.contains("Propose ONE")).findFirst().orElse("");
        Assert.assertFalse(ask.matches("(?s).*\\n\\s*1\\) CAST.*"), "the menu was shown: " + ask);
        Assert.assertTrue(model.results.stream().anyMatch(r -> r.contains("ILLEGAL (cost)")), String.valueOf(model.results));
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
