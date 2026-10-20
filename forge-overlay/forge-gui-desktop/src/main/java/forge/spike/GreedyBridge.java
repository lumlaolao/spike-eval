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
    private static final Pattern ASSUMED = Pattern.compile(Pattern.quote(PlayerLookahead.ASSUMING) + "([^\\]]+)\\]");

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
            if (!prompt.contains(SeatObservation.STACK_LINE + "(empty)")) return answer(pass);
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

    /** A decision off the priority menu, answered toward the opponent (see the class doc); only a target ask is steered
     *  (pr-review of #279: "never CANCEL" on every single pick paid every tax and said yes to every "you may"). */
    private JsonObject towardOpponent(ScriptBridge.Question q) {
        JsonObject a = q.fallback();
        boolean target = q.block.contains("TARGET");
        if (target && "option".equals(q.field)) {
            // "No target" on the list means the ask is "up to one": declining is allowed.
            boolean upTo = q.options.values().stream().anyMatch(l -> ScriptBridge.norm(l).startsWith("no target"));
            List<Integer> picks = targets(q, assumed, upTo ? 0 : 1, 1);
            if (!picks.isEmpty()) a.addProperty("option", picks.get(0));
        }
        if (target && "options".equals(q.field)) {
            JsonArray picks = new JsonArray();
            for (Integer n : targets(q, assumed, a.getAsJsonArray("options").size(), Integer.MAX_VALUE)) picks.add(n);
            a.add("options", picks);
        }
        if ("options".equals(q.field) && q.block.contains("declare attackers")) {
            JsonArray all = new JsonArray();
            for (Integer n : q.options.keySet()) all.add(n);
            a.add("options", all);
        }
        return a;
    }

    /** How much an option suits a play aimed at the opponent: the opponent or the opponent's card first, then a card
     *  with no side shown, then declining ("No target"), the seat's own last; CANCEL never (pr-review of #279: the
     *  engine lists the seat's own cards first, and the first one took a removal spell). */
    static int side(String label) {
        String l = ScriptBridge.norm(label);
        if (l.startsWith("cancel")) return Integer.MAX_VALUE;
        if (l.startsWith("opponent") || l.contains(", opponent's]")) return 0;
        if (l.startsWith("no target")) return 2;
        if (l.startsWith("you ") || l.contains(", yours]")) return 3;
        return 1;
    }

    /** A label that names {@code name} whole: the name, then its end, a handle or a bracket ("Grizzly Bears 2/2 @7"). */
    private static boolean names(String label, String name) {
        String l = ScriptBridge.norm(label);
        if (!l.startsWith(name)) return false;
        return l.length() == name.length() || " @[(".indexOf(l.charAt(name.length())) >= 0;
    }

    /** A target ask's picks, at most {@code max}: each target the preview assumed (its copy on the opponent's side
     *  first), used up as it is taken so a chain's next ask (Arc Trail's second target) gets the rest; then, up to
     *  {@code min} (at least one), the best-suited options by {@link #side}. */
    private static List<Integer> targets(ScriptBridge.Question q, List<String> want, int min, int max) {
        List<Map.Entry<Integer, String>> ranked = new ArrayList<>(q.options.entrySet());
        ranked.sort(java.util.Comparator.comparingInt(o -> side(o.getValue())));
        List<Integer> picks = new ArrayList<>();
        for (java.util.Iterator<String> it = want.iterator(); it.hasNext() && picks.size() < max; ) {
            String w = it.next();
            for (Map.Entry<Integer, String> o : ranked)
                if (!picks.contains(o.getKey()) && side(o.getValue()) < Integer.MAX_VALUE && names(o.getValue(), w)) {
                    picks.add(o.getKey());
                    it.remove();
                    break;
                }
        }
        for (Map.Entry<Integer, String> o : ranked) {
            if (picks.size() >= Math.min(Math.max(min, 1), max)) break;
            if (side(o.getValue()) == Integer.MAX_VALUE || picks.contains(o.getKey())) continue;
            if (side(o.getValue()) == 2 && min > 0) continue;   // declining is not a pick where one is required
            picks.add(o.getKey());
        }
        return picks;
    }

    private NativeTools.Turn answer(JsonObject args) {
        List<NativeTools.Call> one = new ArrayList<>();
        one.add(new NativeTools.Call("g" + calls, AnswerTool.NAME, args.toString()));
        return NativeTools.Turn.ofCalls(one);
    }
}
