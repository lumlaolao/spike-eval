package forge.spike;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

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
 */
final class ChatSessionBridge implements LLMBridge {
    /** A backend that can answer over a whole message list (the OpenRouter lane; tests fake it). */
    interface MessagesBridge extends LLMBridge {
        String askMessages(String messages);
    }

    static final boolean ON = "1".equals(System.getenv("ONE_CHAT"));
    /** v2 (2026-10-15): the whole game's decisions, not the last 20 (docs/harness/MODEL-LIMITS.md). */
    static final String VERSION = "one-chat-v2";
    static final int MAX_PAIRS = intEnv("ONE_CHAT_TURNS", 10000);
    /** Said before the current question whenever there is history. Part of the one-chat prompt bytes. */
    static final String NOTE = "(The earlier messages in this conversation are your own past decisions in this game, "
            + "each shortened to its question and your answer; the states they were asked in are no longer "
            + "current. The next message holds the current state.)";

    private final MessagesBridge inner;
    private final Deque<String[]> pairs = new ArrayDeque<>();

    ChatSessionBridge(MessagesBridge inner) { this.inner = inner; }

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
        for (String[] p : pairs) {
            if (b.length() > 0) b.append(',');
            b.append(msg("user", p[0])).append(',').append(msg("assistant", p[1]));
        }
        if (b.length() > 0) b.append(',').append(msg("user", NOTE));
        return b.toString();
    }

    int size() { return pairs.size(); }

    /** Records one answered decision. Called by the controller with the prompt it asked and the
     *  final reply it used. */
    void remember(String prompt, String reply) {
        if (prompt == null || reply == null || reply.trim().isEmpty()) return;
        pairs.addLast(new String[] {stub(prompt), reply.trim()});
        while (pairs.size() > MAX_PAIRS) pairs.removeFirst();
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

    @Override public void restart() { pairs.clear(); inner.restart(); }
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
