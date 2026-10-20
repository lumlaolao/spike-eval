package forge.spike;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The greedy-preview floor (docs/harness/HARD-PUZZLES.md section 4, item 4), model spec {@code greedy:}: a zero-model
 * seat that, at each priority menu, previews every cast and activation with the seat's own `simulate` tool
 * (LOOKAHEAD=1) and takes the one that lowers the opponent's life most, else passes. Every other decision is
 * answered toward the opponent: a target named "opponent" when there is one, every attacker, no blocks, else the
 * do-nothing answer. It plans nothing: a puzzle it solves is one move deep, so a hard tier must stay unsolved by it.
 *
 * <p>It sees only what the model is shown and calls only the model's tools, through the faithful seat, so the floor
 * is the seat's own one-ply lookahead, not a privileged search.
 */
final class GreedyBridge implements LLMBridge {
    static final String PREFIX = "greedy:";

    private final String label;
    private int calls = 0;

    GreedyBridge(String label) { this.label = label; }

    @Override public boolean supportsNativeTools() { return true; }
    @Override public String getLabel() { return label; }
    @Override public String getModel() { return "greedy"; }
    @Override public long getTotalMs() { return 0; }
    @Override public int getCalls() { return calls; }
    @Override public String ask(String prompt) { calls++; return null; }

    /** "...; Opponent life 11 -> 8": a player's change always starts with its life (PlayerLookahead.publicDelta), so a
     *  "life" right after "; Name " is that opponent's; the seat's own is "you life", after the colon. */
    private static final Pattern OPP_LIFE = Pattern.compile(";\\s+(?!you\\b)[^;:]*?\\blife (-?\\d+) -> (-?\\d+)");

    /** How much the previews say the opponent loses; 0 when nothing, or when the preview did not run. */
    static int opponentLoss(String preview) {
        int loss = 0;
        Matcher m = OPP_LIFE.matcher(preview == null ? "" : preview);
        while (m.find()) loss += Integer.parseInt(m.group(1)) - Integer.parseInt(m.group(2));
        return loss;
    }

    @Override
    public NativeTools.Turn chat(String messagesJson, String toolsJson, boolean allowCalls) {
        calls++;
        JsonArray msgs = JsonParser.parseString(messagesJson).getAsJsonArray();
        ScriptBridge.Question q = new ScriptBridge.Question(text(msgs.get(0)));
        Map<String, Integer> asked = new LinkedHashMap<>();   // call id -> option previewed
        Map<Integer, String> seen = new LinkedHashMap<>();    // option -> preview text
        for (JsonElement e : msgs) {
            JsonObject m = e.getAsJsonObject();
            String role = m.has("role") ? m.get("role").getAsString() : "";
            if ("assistant".equals(role) && m.has("tool_calls"))
                for (JsonElement tc : m.getAsJsonArray("tool_calls")) {
                    JsonObject f = tc.getAsJsonObject().getAsJsonObject("function");
                    if (NativeTools.SIMULATE.equals(f.get("name").getAsString()))
                        asked.put(tc.getAsJsonObject().get("id").getAsString(),
                                JsonParser.parseString(f.get("arguments").getAsString()).getAsJsonObject().get("option").getAsInt());
                }
            if ("tool".equals(role) && m.has("tool_call_id") && asked.containsKey(m.get("tool_call_id").getAsString()))
                seen.put(asked.get(m.get("tool_call_id").getAsString()), text(e));
        }
        if (q.playMenu) {
            if (asked.isEmpty() && allowCalls && toolsJson.contains("\"" + NativeTools.SIMULATE + "\"")) {
                List<NativeTools.Call> previews = new ArrayList<>();
                for (Map.Entry<Integer, String> o : q.options.entrySet())
                    if ((o.getValue().startsWith("CAST ") || o.getValue().startsWith("ACTIVATE ")) && previews.size() < ContextMemory.TOOLS_PER_ASK)
                        previews.add(new NativeTools.Call("g" + calls + "_" + o.getKey(), NativeTools.SIMULATE, "{\"option\":" + o.getKey() + "}"));
                if (!previews.isEmpty()) return turn(previews);
            }
            int best = -1, bestLoss = 0;
            for (Map.Entry<Integer, String> p : seen.entrySet()) {
                int loss = opponentLoss(p.getValue());
                if (loss > bestLoss) { bestLoss = loss; best = p.getKey(); }
            }
            JsonObject a = new JsonObject();
            a.addProperty("option", best > 0 ? best : q.passOption());
            return answer(a);
        }
        return answer(towardOpponent(q));
    }

    /** A decision off the priority menu, answered toward the opponent: never planning, never the model's. */
    private static JsonObject towardOpponent(ScriptBridge.Question q) {
        JsonObject a = q.fallback();
        if ("option".equals(q.field))
            for (Map.Entry<Integer, String> o : q.options.entrySet())
                if (o.getValue().toLowerCase(java.util.Locale.ROOT).startsWith("opponent")) { a.addProperty("option", o.getKey()); break; }
        if ("options".equals(q.field) && q.block.contains("declare attackers")) {
            JsonArray all = new JsonArray();
            for (Integer n : q.options.keySet()) all.add(n);
            a.add("options", all);
        }
        return a;
    }

    private NativeTools.Turn answer(JsonObject args) {
        List<NativeTools.Call> one = new ArrayList<>();
        one.add(new NativeTools.Call("g" + calls, AnswerTool.NAME, args.toString()));
        return turn(one);
    }

    private static NativeTools.Turn turn(List<NativeTools.Call> calls) {
        JsonArray tcs = new JsonArray();
        for (NativeTools.Call c : calls) {
            JsonObject f = new JsonObject();
            f.addProperty("name", c.name);
            f.addProperty("arguments", c.arguments);
            JsonObject tc = new JsonObject();
            tc.addProperty("id", c.id);
            tc.addProperty("type", "function");
            tc.add("function", f);
            tcs.add(tc);
        }
        JsonObject msg = new JsonObject();
        msg.addProperty("role", "assistant");
        msg.add("tool_calls", tcs);
        return new NativeTools.Turn(null, msg.toString(), calls);
    }

    private static String text(JsonElement m) {
        JsonObject o = m.getAsJsonObject();
        return o.has("content") && o.get("content").isJsonPrimitive() ? o.get("content").getAsString() : "";
    }
}
