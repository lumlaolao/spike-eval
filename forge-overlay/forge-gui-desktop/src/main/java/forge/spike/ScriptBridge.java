package forge.spike;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The script arm (docs/harness/HARD-PUZZLES.md section 4, item 1): a written solution line, played by the faithful
 * model seat. Model spec {@code script:<file>}: the file's {@code [solution]} section (or the whole file), one line
 * per decision, each naming an option by the words the seat is shown ("cast Dark Ritual", "Thing in the Ice",
 * "Goblin Guide; Monastery Swiftspear" for a pick list, "Grizzly Bears -> Hill Giant" for a block, "3" for a
 * number). The bridge answers each `answer` call from the question's own numbered options, so a scripted line sees
 * the menu a model sees, floating mana and every cost included, and the engine checks every step.
 *
 * <p>Matching: a line names the option whose label starts with it (case and spacing ignored); " ... " skips ahead
 * in the label ("activate Tundra ... Add {U}"); a label equal to the line wins over longer ones. Two matching labels
 * that differ in more than their handle (@N) are ambiguous. "at [T2] PHASE: line" waits for that turn and step.
 *
 * <p>Nothing is guessed. A priority menu the next line does not fit is passed (the line waits for its window), and
 * a mana payment's "which stay untapped" is left to the engine unless the line is written for it ("untap: Mountain");
 * both are counted. Any other decision the next line does not fit, an answer the harness refuses, or a decision
 * after the last line (other than a priority menu or an untap ask) is a DRIFT: recorded, the rest of the game answered by the do-nothing option, and the result void. A game that ends with
 * lines unplayed did not play the line either (the runner refuses {@link #unconsumed} above zero).
 */
final class ScriptBridge implements LLMBridge {
    static final String PREFIX = "script:";

    private final String label;
    final List<String> lines;
    private int pos = 0;
    /** The first decision the line did not fit, or null. */
    String drift = null;
    /** A decision that needed a line came after the last one. */
    boolean exhausted = false;
    /** Priority menus passed while the next line waited for its window. */
    int waits = 0;
    /** Mana payments whose untapped sources the line left to the engine. */
    int untapsLeft = 0;
    /** What each played line took: "line => label". */
    final List<String> taken = new ArrayList<>();
    private int calls = 0;

    ScriptBridge(String label, List<String> lines) {
        this.label = label;
        this.lines = new ArrayList<>(lines);
    }

    /** {@code path}'s [solution] section, or the whole file when it has none; blank and # lines skipped. */
    static ScriptBridge fromFile(String label, String path) {
        List<String> raw;
        try { raw = Files.readAllLines(Paths.get(path.trim()), StandardCharsets.UTF_8); }
        catch (IOException e) { throw new IllegalArgumentException("script: cannot read " + path + ": " + e); }
        boolean sectioned = raw.stream().anyMatch(ScriptBridge::isHeader);
        List<String> out = new ArrayList<>();
        boolean in = !sectioned;
        for (String l : raw) {
            String t = l.trim();
            if (isHeader(t)) { in = t.equalsIgnoreCase("[solution]"); continue; }
            if (in && !t.isEmpty() && !t.startsWith("#")) out.add(t);
        }
        if (out.isEmpty()) throw new IllegalArgumentException("script: no solution lines in " + path);
        return new ScriptBridge(label, out);
    }

    private static boolean isHeader(String l) { String t = l.trim(); return t.startsWith("[") && t.endsWith("]") && t.length() > 2; }

    /** The game played the line as written: every line taken, nothing drifted. A solve counts as the line's only then
     *  (after a drift the do-nothing answers can still stumble into the acceptance state). */
    boolean played() { return drift == null && unconsumed() == 0; }

    int unconsumed() { return lines.size() - pos; }

    /** The result row's fields (LlmSpikePuzzle), the names the draw-tape script arm already writes plus the counts. */
    Map<String, Object> report() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("script_drift", drift == null ? "" : drift);
        m.put("script_exhausted", exhausted);
        m.put("script_unconsumed", unconsumed());
        m.put("script_lines", lines.size());
        // Which line the row played: --script files are not covered by the puzzle's hash.
        m.put("script_sha256", LlmSpikePuzzle.sha256(String.join("\n", lines)));
        m.put("script_waits", waits);
        m.put("script_untaps_left", untapsLeft);
        m.put("script_played", played());
        // What each line took ("line => label"): the verdict checks the casts it claims against the game log.
        m.put("script_taken", new ArrayList<>(taken));
        return m;
    }

    @Override public boolean supportsNativeTools() { return true; }
    @Override public String getLabel() { return label; }
    @Override public String getModel() { return "script"; }
    @Override public long getTotalMs() { return 0; }
    @Override public int getCalls() { return calls; }

    /** A text ask has no option list to match: the seat asked outside the answer tool. */
    @Override public String ask(String prompt) {
        calls++;
        fail("a text ask (no `answer` tool offered)");
        return null;
    }

    @Override
    public NativeTools.Turn chat(String messagesJson, String toolsJson, boolean allowCalls) {
        calls++;
        JsonArray msgs = JsonParser.parseString(messagesJson).getAsJsonArray();
        String prompt = text(msgs.get(0));
        String refused = null;
        JsonElement last = msgs.get(msgs.size() - 1);
        if (msgs.size() > 1 && "tool".equals(role(last)) && text(last).startsWith("ERROR:")) refused = text(last);
        String args = decide(new Question(prompt), refused).toString();
        List<NativeTools.Call> one = new ArrayList<>();
        one.add(new NativeTools.Call("s" + calls, AnswerTool.NAME, args));
        return NativeTools.Turn.ofCalls(one);
    }

    private static String role(JsonElement m) {
        JsonObject o = m.getAsJsonObject();
        return o.has("role") ? o.get("role").getAsString() : "";
    }

    private static String text(JsonElement m) {
        JsonObject o = m.getAsJsonObject();
        return o.has("content") && o.get("content").isJsonPrimitive() ? o.get("content").getAsString() : "";
    }

    private void fail(String why) { if (drift == null) drift = "line " + (pos + 1) + ": " + why; }

    /** One decision: the arguments of its `answer` call. */
    JsonObject decide(Question q, String refused) {
        if (refused != null) fail("the harness refused " + (lastTaken == null ? "the do-nothing answer"
                : "the answer to line " + pos + " (" + lastTaken + ")") + ": " + refused);
        lastTaken = null;
        if (drift != null) return q.fallback();
        String line = pos < lines.size() ? lines.get(pos) : null;
        String body = line;
        boolean here = true;
        if (line != null) {
            Matcher m = QUALIFIED.matcher(line);
            if (m.matches()) {
                body = m.group(3).trim();
                here = (m.group(1) == null || Integer.toString(q.turn).equals(m.group(1)))
                        && m.group(2).equalsIgnoreCase(q.phase);
            }
        }
        if (q.manaUntap) {
            // Only a line written for it ("untap: Mountain"): any other line is for a later decision, and an untap ask
            // that took it would shift every line after it (pr-review of PR #276: "none" for the attack was eaten).
            Matcher u = UNTAP.matcher(body == null ? "" : body);
            if (line != null && here && u.matches()) {
                JsonObject a = q.answer(u.group(1).trim());
                if (a != null) { take(line, q); return a; }
                fail("\"" + line + "\" fits no source: " + q.head() + (q.ambiguity == null ? "" : " (" + q.ambiguity + ")"));
                return q.fallback();
            }
            untapsLeft++;
            return q.fallback();
        }
        if (q.playMenu) {
            if (line != null && here) {
                JsonObject a = q.answer(body);
                if (a != null) { take(line, q); return a; }
                if (q.ambiguity != null) { fail(q.ambiguity); return q.fallback(); }
            }
            waits++;
            return q.fallback();
        }
        if (line == null) {
            exhausted = true;
            fail("the line ended before this decision: " + q.head());
            return q.fallback();
        }
        if (!here) { fail("\"" + line + "\" is for another step; this decision is at T" + q.turn + " " + q.phase + ": " + q.head()); return q.fallback(); }
        JsonObject a = q.answer(body);
        if (a == null) {
            fail("\"" + line + "\" fits no option" + (q.ambiguity == null ? "" : " (" + q.ambiguity + ")") + ": " + q.head()
                    + (q.options.isEmpty() ? "" : " | options: " + String.join(" | ", q.options.values())));
            return q.fallback();
        }
        take(line, q);
        return a;
    }

    /** The line the last answer came from, or null when it was a wait or a do-nothing answer. */
    private String lastTaken = null;

    private void take(String line, Question q) {
        lastTaken = line;
        pos++;
        taken.add(line + " => " + (q.lastTaken == null ? "" : q.lastTaken));
    }

    static final Pattern UNTAP = Pattern.compile("(?i)^untap:\\s*(.*)$");
    static final Pattern QUALIFIED = Pattern.compile("(?i)^at\\s+(?:T(\\d+)\\s+)?([A-Za-z0-9_]+)\\s*:\\s*(.*)$");
    private static final Pattern OPTION = Pattern.compile("(?m)^\\s*(\\d+)\\) (.*)$");
    private static final Pattern FIELD = Pattern.compile("call `answer` with `(\\w+)`");
    private static final Pattern HEADER = Pattern.compile("== Turn (\\d+) ([A-Z0-9_]+) \\(");
    private static final Pattern PRIORITY = Pattern.compile("You have priority \\(([A-Z0-9_]+),");
    /** What tells twins apart and nothing else: a handle ("@5", "@29 (was @26)") or an attack menu's "#2". */
    private static final Pattern HANDLE = Pattern.compile("\\s*@\\d+(\\s*\\(was @\\d+\\))?|\\s+#\\d+$");

    static String norm(String s) { return s.toLowerCase(Locale.ROOT).replaceAll("\\s+", " ").trim(); }

    /** The label read as the line reads it: starts with the first segment, then each later " ... " segment in order. */
    static boolean fits(String label, String body) {
        String l = norm(label);
        String[] segs = norm(body).split("\\s*\\.\\.\\.\\s*");
        if (segs.length == 0 || !l.startsWith(segs[0])) return false;
        int at = segs[0].length();
        for (int i = 1; i < segs.length; i++) {
            int k = l.indexOf(segs[i], at);
            if (k < 0) return false;
            at = k + segs[i].length();
        }
        return true;
    }

    /** One decision as the seat was asked it. */
    static final class Question {
        final String block, field;
        final Map<Integer, String> options = new LinkedHashMap<>();
        final int turn;
        final String phase;
        final boolean playMenu, manaUntap;
        final String prompt;
        String ambiguity, lastTaken;

        Question(String prompt) {
            this.prompt = prompt;
            int d = prompt.lastIndexOf("DECISION:");
            String b = d < 0 ? prompt : prompt.substring(d);
            int a = b.lastIndexOf("ANSWER:");
            block = a < 0 ? b : b.substring(0, a);
            Matcher f = FIELD.matcher(prompt);
            String fld = "option";
            while (f.find()) fld = f.group(1);
            field = fld;
            Matcher o = OPTION.matcher(block);
            while (o.find()) options.put(Integer.parseInt(o.group(1)), o.group(2).trim());
            Matcher h = HEADER.matcher(prompt);
            int t = 0;
            String ph = "";
            if (h.find()) { t = Integer.parseInt(h.group(1)); ph = h.group(2); }
            Matcher p = PRIORITY.matcher(block);
            if (p.find()) ph = p.group(1);
            turn = t;
            phase = ph;
            playMenu = block.contains("You have priority (") && passOption() > 0;
            manaUntap = block.startsWith("DECISION: MANA: you are paying") && block.contains("stay untapped");
        }

        String head() {
            String first = block.split("\n", 2)[0];
            return first.length() > 160 ? first.substring(0, 160) + "..." : first;
        }

        int passOption() {
            for (Map.Entry<Integer, String> e : options.entrySet()) if (e.getValue().startsWith("PASS")) return e.getKey();
            return -1;
        }

        /** The one unused option {@code body} names, or null (with {@link #ambiguity} when two different labels fit). */
        private Integer pick(String body, Set<Integer> used) {
            for (Map.Entry<Integer, String> e : options.entrySet())
                if (!used.contains(e.getKey()) && norm(e.getValue()).equals(norm(body))) return e.getKey();
            Integer first = null;
            Set<String> kinds = new LinkedHashSet<>();
            for (Map.Entry<Integer, String> e : options.entrySet()) {
                if (used.contains(e.getKey()) || !fits(e.getValue(), body)) continue;
                if (first == null) first = e.getKey();
                kinds.add(norm(HANDLE.matcher(e.getValue()).replaceAll("")));
            }
            if (kinds.size() > 1) { ambiguity = "ambiguous: \"" + body + "\" fits " + String.join(" | ", kinds); return null; }
            return first;
        }

        /** The arguments the line gives, or null when it fits nothing here. */
        JsonObject answer(String body) {
            JsonObject a = new JsonObject();
            String b = body.trim();
            switch (field) {
                case "option": {
                    Integer n = pick(b, new LinkedHashSet<>());
                    if (n == null) return null;
                    lastTaken = options.get(n);
                    a.addProperty("option", n);
                    return a;
                }
                case "options": {
                    JsonArray picks = new JsonArray();
                    Set<Integer> used = new LinkedHashSet<>();
                    if (!b.equalsIgnoreCase("none")) {
                        for (String item : b.split(";")) {
                            Integer n = pick(item.trim(), used);
                            if (n == null) return null;
                            used.add(n);
                            picks.add(n);
                        }
                    }
                    List<String> names = new ArrayList<>();
                    for (Integer n : used) names.add(options.get(n));
                    lastTaken = used.isEmpty() ? "none" : String.join("; ", names);
                    a.add("options", picks);
                    return a;
                }
                case "blocks": {
                    JsonArray out = new JsonArray();
                    Set<Integer> usedBlockers = new LinkedHashSet<>();
                    if (!b.equalsIgnoreCase("none")) {
                        for (String pair : b.split(";")) {
                            String[] ab = pair.split("->");
                            if (ab.length != 2) return null;
                            int blocker = listed("YOUR BLOCKERS:", ab[0].trim(), usedBlockers, true);
                            int attacker = blocker < 0 ? -1 : listed("ATTACKERS:", ab[1].trim(), new LinkedHashSet<>(), false);
                            if (blocker < 0 || attacker < 0) return null;
                            usedBlockers.add(blocker);
                            JsonObject o = new JsonObject();
                            o.addProperty("blocker", blocker);
                            o.addProperty("attacker", attacker);
                            out.add(o);
                        }
                    }
                    lastTaken = b;
                    a.add("blocks", out);
                    return a;
                }
                case "number":
                    if (!b.matches("-?\\d+")) return null;
                    a.addProperty("number", Integer.parseInt(b));
                    lastTaken = b;
                    return a;
                case "amounts": {
                    JsonArray out = new JsonArray();
                    for (String x : b.split("[\\s,;]+")) {
                        if (!x.matches("-?\\d+")) return null;
                        out.add(Integer.parseInt(x));
                    }
                    a.add("amounts", out);
                    lastTaken = b;
                    return a;
                }
                case "keep":
                    if (!b.equalsIgnoreCase("keep") && !b.equalsIgnoreCase("mulligan")) return null;
                    a.addProperty("keep", b.equalsIgnoreCase("keep"));
                    lastTaken = b;
                    return a;
                case "card":
                    a.addProperty("card", b);
                    lastTaken = b;
                    return a;
                default:
                    return null;
            }
        }

        /** "1 = Typhoid Rats 1/1 @3; 2 = ..." on the line headed {@code header}: the number of the one {@code name}
         *  names, as {@link #pick} names an option: an equal label first, then one that fits; two different labels
         *  that fit are ambiguous. Twins (labels equal but for the handle) take turns as blockers ({@code used}); as
         *  attackers they are ambiguous, since one each and a double block are both legal: the line names a handle. */
        private int listed(String header, String name, Set<Integer> used, boolean twinsTakeTurns) {
            int i = block.lastIndexOf(header);
            if (i < 0) return -1;
            int end = block.indexOf('\n', i);
            String row = block.substring(i + header.length(), end < 0 ? block.length() : end);
            Map<Integer, String> items = new LinkedHashMap<>();
            for (String item : row.split(";")) {
                Matcher m = Pattern.compile("^\\s*(\\d+) = (.*)$").matcher(item);
                if (m.matches()) items.put(Integer.parseInt(m.group(1)), m.group(2).trim());
            }
            for (Map.Entry<Integer, String> e : items.entrySet())
                if (!used.contains(e.getKey()) && norm(e.getValue()).equals(norm(name))) return e.getKey();
            List<Integer> fit = new ArrayList<>();
            Set<String> kinds = new LinkedHashSet<>();
            for (Map.Entry<Integer, String> e : items.entrySet()) {
                if (used.contains(e.getKey()) || !fits(e.getValue(), name)) continue;
                fit.add(e.getKey());
                kinds.add(norm(HANDLE.matcher(e.getValue()).replaceAll("")));
            }
            if (kinds.size() > 1 || (!twinsTakeTurns && fit.size() > 1)) {
                ambiguity = "ambiguous: \"" + name + "\" fits " + fit.size() + " in " + header.replace(":", "")
                        + (kinds.size() > 1 ? ": " + String.join(" | ", kinds) : " (twins: name one by its handle)");
                return -1;
            }
            return fit.isEmpty() ? -1 : fit.get(0);
        }

        /** The do-nothing answer: PASS, CANCEL or a decline, no picks beyond the least allowed, no blocks, the least number. */
        JsonObject fallback() {
            JsonObject a = new JsonObject();
            switch (field) {
                case "option": {
                    int none = passOption();
                    for (Map.Entry<Integer, String> e : options.entrySet()) {
                        if (none > 0) break;
                        String l = norm(e.getValue());
                        if (l.startsWith("cancel") || l.startsWith("decline") || l.startsWith("do not") || l.equals("no")) none = e.getKey();
                    }
                    a.addProperty("option", none > 0 ? none : options.isEmpty() ? 1 : options.keySet().iterator().next());
                    return a;
                }
                case "options": {
                    Matcher m = Pattern.compile("`options`: the numbers you pick, (?:exactly (\\d+)|between (\\d+) and)").matcher(prompt);
                    int lo = m.find() ? Integer.parseInt(m.group(1) != null ? m.group(1) : m.group(2)) : 0;
                    JsonArray picks = new JsonArray();
                    for (Integer n : options.keySet()) { if (picks.size() >= lo) break; picks.add(n); }
                    a.add("options", picks);
                    return a;
                }
                case "blocks": a.add("blocks", new JsonArray()); return a;
                case "number": {
                    Matcher m = Pattern.compile("`number`: an integer from (-?\\d+)").matcher(prompt);
                    a.addProperty("number", m.find() ? Integer.parseInt(m.group(1)) : 0);
                    return a;
                }
                case "amounts": {
                    Matcher m = Pattern.compile("`amounts`: (\\d+) numbers in the order listed, summing to (-?\\d+)").matcher(prompt);
                    JsonArray out = new JsonArray();
                    if (m.find()) {
                        out.add(Integer.parseInt(m.group(2)));
                        for (int i = 1; i < Integer.parseInt(m.group(1)); i++) out.add(0);
                    }
                    a.add("amounts", out);
                    return a;
                }
                case "keep": a.addProperty("keep", true); return a;
                default: return a;
            }
        }
    }
}
