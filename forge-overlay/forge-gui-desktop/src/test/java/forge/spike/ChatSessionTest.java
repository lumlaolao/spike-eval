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

/** ONE_CHAT: one conversation per seat ({@link ChatSessionBridge}). */
public class ChatSessionTest {
    @BeforeClass
    public void init() { new LineFindingProbeTest().initializeModel(); }

    @BeforeMethod
    public void faithful() { LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL; }

    @AfterMethod(alwaysRun = true)
    public void legacy() { LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null; }

    /** Records the message list of every ask and answers with {@code reply}. */
    static class Messages implements ChatSessionBridge.MessagesBridge {
        final List<String> sent = new ArrayList<>();
        final String reply;
        Messages(String reply) { this.reply = reply; }
        @Override public String askMessages(String messages) { sent.add(messages); return reply; }
        @Override public String ask(String prompt) { return askMessages(prompt); }
        @Override public String getLabel() { return "chat"; }
        @Override public String getModel() { return "fake"; }
        @Override public long getTotalMs() { return 0; }
        @Override public int getCalls() { return sent.size(); }
    }

    /** A seat on a one-chat session reads the wrapped bridge's bill; without the pass-through it would fall back to the
     *  last call's usage and lose a truncation retry's first send (pr-review of #282). */
    @Test
    public void theSessionPassesTheWrappedBridgesBillThrough() {
        Messages billed = new Messages("CHOICE: 1") {
            @Override public long getBilledPromptTokens() { return 123; }
            @Override public long getBilledCompletionTokens() { return 45; }
        };
        ChatSessionBridge c = new ChatSessionBridge(billed);
        Assert.assertEquals(c.getBilledPromptTokens(), 123L);
        Assert.assertEquals(c.getBilledCompletionTokens(), 45L);
    }

    @Test
    public void anEarlierQuestionKeepsItsDecisionAndDropsItsState() {
        String p = "== Turn 3 MAIN1 (active: Seat) ==\nYOU [Seat] life 20\nBATTLEFIELD: Mountain\nDECISION: Choose X for CAST Fireball\n  1) 0\n  2) 1";
        String s = ChatSessionBridge.stub(p);
        Assert.assertTrue(s.startsWith("[earlier decision (turn 3, MAIN1); that state is no longer current]"), s);
        Assert.assertTrue(s.contains("DECISION: Choose X for CAST Fireball") && s.contains("2) 1"), s);
        Assert.assertFalse(s.contains("BATTLEFIELD"), "the old state is not kept: " + s);
    }

    @Test
    public void theConversationKeepsTheLastDecisionsOnly() {
        ChatSessionBridge c = new ChatSessionBridge(new Messages("CHOICE: 1"));
        for (int i = 0; i < ChatSessionBridge.MAX_PAIRS + 5; i++) c.remember("DECISION: q" + i, "CHOICE: 1");
        Assert.assertEquals(c.size(), ChatSessionBridge.MAX_PAIRS);
        Assert.assertFalse(c.history().contains("DECISION: q0\""), "the oldest decision was dropped");
        Assert.assertTrue(c.history().endsWith("\"content\":\"" + LlmSpikeGui.jsonEscape(ChatSessionBridge.NOTE) + "\"}"));
    }

    /** TURN_CHAT: the conversation holds this game turn's decisions and starts over when the turn changes. */
    @Test
    public void aTurnScopedConversationStartsOverEachTurn() {
        ChatSessionBridge c = new ChatSessionBridge(new Messages("CHOICE: 1"), true);
        c.atTurn(5);
        c.remember("DECISION: cast Dark Ritual", "CHOICE: 1");
        c.atTurn(5);
        c.remember("DECISION: cast Tendrils", "CHOICE: 1");
        Assert.assertEquals(c.size(), 2, "the same turn keeps both steps of the line");
        Assert.assertTrue(c.history().endsWith("\"content\":\"" + LlmSpikeGui.jsonEscape(ChatSessionBridge.TURN_NOTE) + "\"}"));
        c.atTurn(6);
        Assert.assertEquals(c.size(), 0, "a new turn starts a new conversation");
        Assert.assertEquals(c.history(), "");
        ChatSessionBridge game = new ChatSessionBridge(new Messages("CHOICE: 1"), false);
        game.remember("DECISION: q", "CHOICE: 1");
        game.atTurn(9);
        Assert.assertEquals(game.size(), 1, "ONE_CHAT keeps the whole game");
    }

    /** A decision answered by a call is replayed as it happened: the question cut to its DECISION part, the
     *  model's assistant message byte for byte (its reasoning_details included), and the tool results. */
    @Test
    public void aCallAnsweredDecisionIsReplayedWithItsReasoning() {
        ChatSessionBridge c = new ChatSessionBridge(new Messages("x"), true);
        com.google.gson.JsonArray tr = new com.google.gson.JsonArray();
        tr.add(NativeTools.message("user", "== Turn 4 MAIN1 ==\nBATTLEFIELD: Swamp\nDECISION: You have priority\n  1) CAST Dark Ritual"));
        String assistant = "{\"role\":\"assistant\",\"content\":null,\"tool_calls\":[{\"id\":\"c9\",\"type\":\"function\","
                + "\"function\":{\"name\":\"answer\",\"arguments\":\"{\\\"option\\\":1}\"}}],"
                + "\"reasoning_details\":[{\"type\":\"reasoning.encrypted\",\"data\":\"opaque\"}]}";
        tr.add(com.google.gson.JsonParser.parseString(assistant));
        tr.add(NativeTools.toolResult("c9", "Answer recorded: CHOICE: 1"));
        c.atTurn(4);
        c.rememberAsk(tr);
        String h = c.history();
        Assert.assertTrue(h.contains(com.google.gson.JsonParser.parseString(assistant).toString()), "the assistant message is kept unchanged: " + h);
        Assert.assertTrue(h.contains("\"tool_call_id\":\"c9\""), h);
        Assert.assertFalse(h.contains("BATTLEFIELD"), "the old state is dropped: " + h);
        Assert.assertTrue(h.contains("DECISION: You have priority"), h);
    }

    /** Through the live controller: the target ask after a play is in the same conversation as the
     *  play, with the model's own reply to it, and the first ask has no history. */
    @Test
    public void theTargetAskFollowsThePlayInOneConversation() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Mountain", me, ZoneType.Battlefield);
        card("Lightning Bolt", me, ZoneType.Hand);
        card("Savannah Lions", rival, ZoneType.Battlefield);
        Messages backend = new Messages("Bolt the Lions before they attack.\nCHOICE: 1");
        ChatSessionBridge chat = new ChatSessionBridge(backend);
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(g, me,
                new LobbyPlayerAi("Seat", null), "Seat", "", chat, chat, "llm");
        me.dangerouslySetController(ctrl);
        ctrl.chooseSpellAbilityToPlay();
        Assert.assertTrue(backend.sent.size() >= 2, "play and target were asked: " + backend.sent.size());
        Assert.assertFalse(backend.sent.get(0).contains("earlier decision"), "the first ask has no history");
        String second = backend.sent.get(1);
        Assert.assertTrue(second.contains("\"role\":\"assistant\""), second);
        Assert.assertTrue(second.contains("Bolt the Lions before they attack."), "the model's own reply is in the chat");
        Assert.assertTrue(second.contains("DECISION: You have priority"), "the play question is in the chat");
        Assert.assertTrue(second.lastIndexOf("DECISION: Choose the TARGET") > second.lastIndexOf("\"role\":\"assistant\""),
                "the target ask comes after the play in the conversation");
    }

    /** ONE_CHAT_TURNS below the default carries fewer pairs, so it is part of the game line's stamp; unset, the stamp is
     *  the version as before (the stamp-coverage scan of every spike file found it unstamped). */
    @Test
    public void aCapOnTheCarriedPairsIsStamped() {
        Assert.assertEquals(ChatSessionBridge.stamp(ChatSessionBridge.DEFAULT_PAIRS, true), ChatSessionBridge.VERSION);
        Assert.assertEquals(ChatSessionBridge.stamp(12, true), ChatSessionBridge.VERSION + "+turns=12");
        Assert.assertEquals(ChatSessionBridge.stamp(12, false), ChatSessionBridge.VERSION, "no chat, nothing capped");
    }
}
