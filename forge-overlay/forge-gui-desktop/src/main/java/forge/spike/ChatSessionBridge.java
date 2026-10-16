package forge.spike;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;

/**
 * ONE_CHAT=1: one conversation per seat instead of a stateless call per question. Each ask goes out
 * after the seat's earlier decisions this game, each shortened to its question and the model's own
 * answer, so the steps of one action (and the turns of one plan) are in the same conversation
 * (docs/harness/PLAYER-HARNESS.md, "One chat").
 *
 * <p>Only the newest message carries a game state. An earlier question keeps its DECISION part (the
 * question and its options) and drops its snapshot, and says it is past: a long chat of full states
 * would put stale boards next to the current one. Every decision of the game is kept (up to
 * {@link #MAX_PAIRS}, a guard no recorded game comes near: the longest asked 137 decisions).
 *
 * <p>A condition, stamped `one_chat` on the game line; off by default. Wraps the OpenRouter lane only
 * (the Claude CLI lane already keeps its own session). The controller records each final reply with
 * {@link #remember}, so a tool round or a retry is not mistaken for an answer.
 *
 * <p>TURN_CHAT=1 (`turn-chat-v1`, stamped `turn_chat`) is the same conversation bounded by the game turn:
 * this turn's earlier decisions, cleared when the turn changes, so the steps of one line (ritual, ritual,
 * tutor, cast) stay in one train of thought without stale boards from earlier turns building up
 * (docs/harness/REVIEW-2026-10-16.md, C1).
 *
 * <p>A decision answered by a call is kept as it happened (v3): the question's DECISION part, then the
 * model's own assistant messages unchanged, with their tool calls and `reasoning_details`, and a tool
 * result for every call, in order. OpenRouter's reasoning guide asks for exactly that (pass
 * `reasoning_details` back unchanged and in order, so the model continues its earlier reasoning), and a
 * past answer rendered as text taught a tool-answering model that text answers were the form.
 */
final class ChatSessionBridge implements LLMBridge {
    /** A backend that can answer over a whole message list (the OpenRouter lane; tests fake it). */
    interface MessagesBridge extends LLMBridge {
        String askMessages(String messages);
    }

    static final boolean ON = "1".equals(System.getenv("ONE_CHAT"));
    /** TURN_CHAT=1: the conversation holds this game turn's decisions only. */
    static final boolean TURN = "1".equals(System.getenv("TURN_CHAT"));
    /** v2 (2026-10-15): the whole game's decisions, not the last 20 (docs/harness/MODEL-LIMITS.md).
     *  v3 (2026-10-16): a decision answered by a call is kept as its calls, results and reasoning. */
    static final String VERSION = "one-chat-v3";
    static final String TURN_VERSION = "turn-chat-v1";
    static final int MAX_PAIRS = intEnv("ONE_CHAT_TURNS", 10000);
    /** Said before the current question whenever there is history. Part of the one-chat prompt bytes. */
    static final String NOTE = "(The earlier messages in this conversation are your own past decisions in this game, "
            + "each shortened to its question and your answer; the states they were asked in are no longer "
            + "current. The next message holds the current state.)";
    /** The same note for a turn-bounded conversation. */
    static final String TURN_NOTE = "(The earlier messages in this conversation are your own decisions earlier this turn, "
            + "each shortened to its question, with your calls and their results; the states they were asked in are no "
            + "longer current. The next message holds the current state.)";

    private final MessagesBridge inner;
    private final boolean turnScoped;
    /** Each kept decision as its message objects, in order. */
    private final Deque<JsonArray> asks = new ArrayDeque<>();
    private int turn = Integer.MIN_VALUE;

    ChatSessionBridge(MessagesBridge inner) { this(inner, TURN); }
    ChatSessionBridge(MessagesBridge inner, boolean turnScoped) { this.inner = inner; this.turnScoped = turnScoped; }

    /** Under TURN_CHAT the conversation starts over when the game turn changes. */
    void atTurn(int gameTurn) {
        if (turnScoped && gameTurn != turn) { asks.clear(); turn = gameTurn; }
    }

    private static int intEnv(String k, int dflt) {
        try { return Math.max(1, Integer.parseInt(System.getenv().getOrDefault(k, String.valueOf(dflt)))); }
        catch (NumberFormatException e) { return dflt; }
    }

    /** An earlier question as it is kept: its turn and phase, then its DECISION part. */
    static String stub(String prompt) {
        if (prompt == null) return "";
        String when = "";
        Matcher m = Pattern.compile("== Turn (\\d+) ([A-Z_0-9]+)").matcher(prompt);
        if (m.find()) when = " (turn " + m.group(1) + ", " + m.group(2) + ")";
        int at = prompt.lastIndexOf("DECISION:");
        String q = at < 0 ? prompt : prompt.substring(at);
        return "[earlier decision" + when + "; that state is no longer current]\n" + q.trim();
    }

    /** The conversation so far as message objects (comma-separated, no brackets), or "". */
    String history() {
        StringBuilder b = new StringBuilder();
        for (JsonArray a : asks) for (JsonElement m : a) {
            if (b.length() > 0) b.append(',');
            b.append(m.toString());
        }
        if (b.length() > 0) b.append(',').append(msg("user", turnScoped ? TURN_NOTE : NOTE));
        return b.toString();
    }

    int size() { return asks.size(); }

    /** Records one decision answered in text. Called by the controller with the prompt it asked and the
     *  final reply it used. */
    void remember(String prompt, String reply) {
        if (prompt == null || reply == null || reply.trim().isEmpty()) return;
        JsonArray a = new JsonArray();
        a.add(NativeTools.message("user", stub(prompt)));
        a.add(NativeTools.message("assistant", reply.trim()));
        keep(a);
    }

    /** Records one decision answered by a call: its whole conversation as it was sent and returned, the
     *  first message cut to its DECISION part. Assistant messages are kept byte for byte (their
     *  `reasoning_details` included); every tool call has its result. */
    void rememberAsk(JsonArray transcript) {
        if (transcript == null || transcript.size() == 0) return;
        JsonArray a = new JsonArray();
        for (int i = 0; i < transcript.size(); i++) {
            JsonElement m = transcript.get(i);
            if (i == 0 && m.isJsonObject() && m.getAsJsonObject().has("content")) {
                a.add(NativeTools.message("user", stub(m.getAsJsonObject().get("content").getAsString())));
            } else a.add(m.deepCopy());
        }
        keep(a);
    }

    private void keep(JsonArray a) {
        asks.addLast(a);
        while (asks.size() > MAX_PAIRS) asks.removeFirst();
    }

    private static String msg(String role, String content) {
        return "{\"role\":\"" + role + "\",\"content\":\"" + LlmSpikeGui.jsonEscape(content) + "\"}";
    }

    @Override public String ask(String prompt) {
        String h = history();
        String cur = msg("user", prompt);
        return inner.askMessages(h.isEmpty() ? cur : h + "," + cur);
    }

    @Override public NativeTools.Turn chat(String messagesJson, String toolsJson, boolean allowCalls) {
        String h = history();
        if (h.isEmpty()) return inner.chat(messagesJson, toolsJson, allowCalls);
        String m = messagesJson.trim();
        String body = m.substring(1, m.length() - 1).trim();
        return inner.chat("[" + h + (body.isEmpty() ? "" : "," + body) + "]", toolsJson, allowCalls);
    }

    @Override public void restart() { asks.clear(); inner.restart(); }
    @Override public String getLabel() { return inner.getLabel(); }
    @Override public String getModel() { return inner.getModel(); }
    @Override public long getTotalMs() { return inner.getTotalMs(); }
    @Override public int getCalls() { return inner.getCalls(); }
    @Override public boolean supportsNativeTools() { return inner.supportsNativeTools(); }
    @Override public String getLastFinishReason() { return inner.getLastFinishReason(); }
    @Override public int getLastPromptTokens() { return inner.getLastPromptTokens(); }
    @Override public int getLastCompletionTokens() { return inner.getLastCompletionTokens(); }
    @Override public int getLastCachedTokens() { return inner.getLastCachedTokens(); }
    @Override public int getLastCacheWriteTokens() { return inner.getLastCacheWriteTokens(); }
    @Override public String getLastProvider() { return inner.getLastProvider(); }
}
