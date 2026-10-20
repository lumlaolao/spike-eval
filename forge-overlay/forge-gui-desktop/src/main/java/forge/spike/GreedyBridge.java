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
 * answered toward the opponent: a target ask takes what the chosen play's preview assumed, else the opponent, never
 * CANCEL; every attacker; no blocks; any other ask the do-nothing answer. It acts only when the faithful prompt shows
 * an empty stack (a preview resolves what is already there too, so every option would seem to deal that damage), and
 * it never plays a land or previews a land's mana ability. It plans nothing: a puzzle it solves is one move deep.
 *
 * <p>It sees only what the model is shown and calls only the model's tools, through the faithful seat, so the floor
 * is the seat's own one-ply lookahead, not a privileged search.
 */
final class GreedyBridge implements LLMBridge {
    static final String PREFIX = "greedy:";

    private final String label;
    private int calls = 0;
    /** The targets the last chosen play's preview assumed ("[assuming target: X, Y]"), for its target ask only. */
    private List<String> assumed = new ArrayList<>();
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
            assumed = new ArrayList<>();
            JsonObject pass = new JsonObject();
            pass.addProperty("option", q.passOption());
            // Fail closed: only the faithful profile writes this line; without it the stack is not known to be empty
            // (pr-review of #279: the legacy snapshot's "stack (top first)" let the floor score the stack again).
            int at = prompt.indexOf(STACK);
            if (at < 0 || !prompt.startsWith("(empty)", at + STACK.length())) return answer(pass);
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
            if (am.find()) for (String t : am.group(1).split(", ")) assumed.add(ScriptBridge.norm(t));
            JsonObject a = new JsonObject();
            a.addProperty("option", best);
            return answer(a);
        }
        return answer(towardOpponent(q));
    }

    /** A decision off the priority menu, answered toward the opponent: never planning, never the model's. A target ask
     *  takes what the chosen play's preview assumed, else the opponent, never CANCEL; any other ask keeps the
     *  do-nothing answer (pr-review of #279: "never CANCEL" on every single pick paid every tax and said yes to
     *  every "you may"). */
    private JsonObject towardOpponent(ScriptBridge.Question q) {
        JsonObject a = q.fallback();
        boolean target = q.block.contains("TARGET");
        List<String> want = assumed;
        if (target) assumed = new ArrayList<>();   // the preview's targets are for this ask only
        if (target && "option".equals(q.field)) {
            List<Integer> picks = targets(q, want, 1);
            if (!picks.isEmpty()) a.addProperty("option", picks.get(0));
        }
        if (target && "options".equals(q.field)) {
            JsonArray picks = new JsonArray();
            for (Integer n : targets(q, want, a.getAsJsonArray("options").size())) picks.add(n);
            a.add("options", picks);
        }
        if ("options".equals(q.field) && q.block.contains("declare attackers")) {
            JsonArray all = new JsonArray();
            for (Integer n : q.options.keySet()) all.add(n);
            a.add("options", all);
        }
        return a;
    }

    /** A target ask's picks: each target the preview assumed, else the opponent, topped up to {@code min} with the first
     *  options that are neither CANCEL nor the seat itself. */
    private static List<Integer> targets(ScriptBridge.Question q, List<String> want, int min) {
        List<Integer> picks = new ArrayList<>();
        for (String w : want)
            for (Map.Entry<Integer, String> o : q.options.entrySet())
                if (!picks.contains(o.getKey()) && ScriptBridge.norm(o.getValue()).startsWith(w)) { picks.add(o.getKey()); break; }
        if (picks.isEmpty())
            for (Map.Entry<Integer, String> o : q.options.entrySet())
                if (ScriptBridge.norm(o.getValue()).startsWith("opponent")) { picks.add(o.getKey()); break; }
        for (Map.Entry<Integer, String> o : q.options.entrySet()) {
            if (picks.size() >= Math.max(min, 1)) break;
            String l = ScriptBridge.norm(o.getValue());
            if (!picks.contains(o.getKey()) && !l.startsWith("cancel") && !l.startsWith("you ")) picks.add(o.getKey());
        }
        return picks;
    }

    private NativeTools.Turn answer(JsonObject args) {
        List<NativeTools.Call> one = new ArrayList<>();
        one.add(new NativeTools.Call("g" + calls, AnswerTool.NAME, args.toString()));
        return NativeTools.Turn.ofCalls(one);
    }
}
