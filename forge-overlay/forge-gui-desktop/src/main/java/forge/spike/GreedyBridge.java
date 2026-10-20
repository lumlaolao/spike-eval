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
 * answered toward the opponent: the target the chosen play's preview assumed, else one named "opponent", else the
 * first that is not CANCEL; every attacker; no blocks; else the do-nothing answer. It acts only on an empty stack (a
 * preview resolves what is already there too, so every option would seem to deal that damage), and it never plays a
 * land or previews a land's mana ability. It plans nothing: a puzzle it solves is one move deep.
 *
 * <p>It sees only what the model is shown and calls only the model's tools, through the faithful seat, so the floor
 * is the seat's own one-ply lookahead, not a privileged search.
 */
final class GreedyBridge implements LLMBridge {
    static final String PREFIX = "greedy:";

    private final String label;
    private int calls = 0;
    /** The target the last chosen play's preview assumed ("[assuming target: X]"), for its target menu. */
    private String assumed = null;
    private static final Pattern ASSUMED = Pattern.compile("\\[assuming target: ([^\\]]+)\\]");
    private static final String STACK = "STACK (engine, top first): ";

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
        String prompt = ScriptBridge.text(msgs.get(0));
        ScriptBridge.Question q = new ScriptBridge.Question(prompt);
        Map<String, Integer> asked = new LinkedHashMap<>();   // call id -> option previewed
        Map<Integer, String> seen = new LinkedHashMap<>();    // option -> preview text
        for (JsonElement e : msgs) {
            JsonObject m = e.getAsJsonObject();
            String role = ScriptBridge.role(e);
            if ("assistant".equals(role) && m.has("tool_calls"))
                for (JsonElement tc : m.getAsJsonArray("tool_calls")) {
                    JsonObject f = tc.getAsJsonObject().getAsJsonObject("function");
                    if (NativeTools.SIMULATE.equals(f.get("name").getAsString()))
                        asked.put(tc.getAsJsonObject().get("id").getAsString(),
                                JsonParser.parseString(f.get("arguments").getAsString()).getAsJsonObject().get("option").getAsInt());
                }
            if ("tool".equals(role) && m.has("tool_call_id") && asked.containsKey(m.get("tool_call_id").getAsString()))
                seen.put(asked.get(m.get("tool_call_id").getAsString()), ScriptBridge.text(e));
        }
        if (q.playMenu) {
            assumed = null;
            JsonObject pass = new JsonObject();
            pass.addProperty("option", q.passOption());
            int at = prompt.indexOf(STACK);
            if (at >= 0 && !prompt.startsWith("(empty)", at + STACK.length())) return answer(pass);
            if (asked.isEmpty() && allowCalls && toolsJson.contains("\"" + NativeTools.SIMULATE + "\"")) {
                List<NativeTools.Call> previews = new ArrayList<>();
                for (Map.Entry<Integer, String> o : q.options.entrySet()) {
                    String l = o.getValue();
                    boolean play = l.startsWith("CAST ") || (l.startsWith("ACTIVATE ") && !l.contains(": Add {"));
                    if (play && previews.size() < ContextMemory.TOOLS_PER_ASK)
                        previews.add(new NativeTools.Call("g" + calls + "_" + o.getKey(), NativeTools.SIMULATE, "{\"option\":" + o.getKey() + "}"));
                }
                if (!previews.isEmpty()) return NativeTools.Turn.ofCalls(previews);
            }
            int best = -1, bestLoss = 0;
            for (Map.Entry<Integer, String> p : seen.entrySet()) {
                int loss = opponentLoss(p.getValue());
                if (loss > bestLoss) { bestLoss = loss; best = p.getKey(); }
            }
            if (best < 0) return answer(pass);
            Matcher am = ASSUMED.matcher(seen.get(best));
            assumed = am.find() ? ScriptBridge.norm(am.group(1)) : null;
            JsonObject a = new JsonObject();
            a.addProperty("option", best);
            return answer(a);
        }
        return answer(towardOpponent(q));
    }

    /** A decision off the priority menu, answered toward the opponent: never planning, never the model's. */
    private JsonObject towardOpponent(ScriptBridge.Question q) {
        JsonObject a = q.fallback();
        if ("option".equals(q.field)) {
            Integer pick = null, opponent = null, first = null;
            for (Map.Entry<Integer, String> o : q.options.entrySet()) {
                String l = ScriptBridge.norm(o.getValue());
                if (l.startsWith("cancel")) continue;
                if (first == null) first = o.getKey();
                if (opponent == null && l.startsWith("opponent")) opponent = o.getKey();
                if (pick == null && assumed != null && l.startsWith(assumed)) pick = o.getKey();
            }
            Integer n = pick != null ? pick : opponent != null ? opponent : first;
            if (n != null) a.addProperty("option", n);
        }
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
        return NativeTools.Turn.ofCalls(one);
    }
}
