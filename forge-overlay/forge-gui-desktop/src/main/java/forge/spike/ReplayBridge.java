package forge.spike;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * The replay arm (step regret, step 1): a recorded attempt's model turns, served again to the seat that asks for them.
 * Model spec {@code replay:<game.jsonl>} or {@code replay:<attempt dir>} (the dir's {@code game-<seed>-*.jsonl} for the
 * seed played). Each {@code context_call} row of the recording's seat is one model turn: its {@code raw_reply} is the
 * turn's calls exactly as the model made them ({@code TOOL CALL <name> <arguments>}, NativeTools.Call.render), so the
 * seat carries out the recorded `answer` (with `keep_untapped`, `yield`, picks and blocks) and `set_plan` through its
 * own answer path, and every skip, side field and RNG draw of the model seat is the recorded game's.
 *
 * <p>Nothing is guessed. A turn is served only to the ask it was recorded for: the same answer field, the same option
 * labels in order, the same listed items ({@code N = ...}: blockers, attackers, amounts), the same answer range (picks,
 * number, amounts), the same turn and step, the same number of messages, and no refusal the recording did not have.
 * Each decision row the seat writes is checked against the recorded one too (type, n_options, choice, options;
 * {@link #onDecisionRow}, wired by the puzzle runner, which refuses a replay with the game log off since no row is
 * written then). When the attempt's result row is beside its game log ({@code puzzle-results.jsonl}, same seed), the
 * game's end must give its status and engine log ({@link #atEnd}). The first difference is the {@link #drift}:
 * recorded, the game ended at once ({@link #onDrift}, set by the runner), and the result is void
 * ({@code status: drift}). An ask that still comes after it gets the do-nothing answer; for a field with no empty
 * value (card, action, declare_attackers, declare_blockers) that answer is refused and the seat takes its fixed legal
 * option, which is no longer the recorded game either way. Prompt text that differs while the question is the same (a
 * later epoch's wording) is no drift; it is counted in {@link #promptDiffs}.
 */
final class ReplayBridge implements LLMBridge {
    static final String PREFIX = "replay:";
    static final String VERSION = "replay-bridge-v1";

    /** One recorded model turn: the ask it answered and what came back (null: the bridge gave no reply). */
    static final class Turn {
        final int call;
        final String prompt;
        final int messages;
        final List<String> toolResults;
        final String raw;
        Turn(int call, String prompt, int messages, List<String> toolResults, String raw) {
            this.call = call; this.prompt = prompt; this.messages = messages; this.toolResults = toolResults; this.raw = raw;
        }
    }

    /** One recorded decision row, as the fork hook sees it. */
    static final class Row {
        final String type;
        final int n, choice;
        final List<String> options;
        Row(String type, int n, int choice, List<String> options) {
            this.type = type; this.n = n; this.choice = choice; this.options = options;
        }
        @Override public String toString() { return type + " n=" + n + " choice=" + choice + " options=" + options; }
    }

    private final String label;
    final String seat, source;
    final List<Turn> turns;
    final List<Row> rows;
    private int pos = 0, rowPos = 0, calls = 0;
    private boolean retrying = false;
    /** The first ask or decision row that differed from the recording, or null. */
    volatile String drift = null;
    /** Asks served whose prompt text differed from the recorded one while the question was the same. */
    int promptDiffs = 0;
    String firstPromptDiff = null;
    /** Asks served after the drift: zero when the game was ended at it. */
    int asksAfterDrift = 0;
    /** Run once, on the thread that found the drift (the puzzle runner ends the game there). */
    volatile Runnable onDrift = null;
    /** The attempt's own result (its puzzle-results.jsonl row for the seed), or null when the recording has none. */
    String recordedStatus = null, recordedLogSha = null;

    ReplayBridge(String label, String source, List<Turn> turns, List<Row> rows) {
        this.label = label;
        this.seat = seatOf(label);
        this.source = source;
        this.turns = turns;
        this.rows = rows;
    }

    /** The seat a bridge label names ("Solver/smart" -> "Solver"). */
    static String seatOf(String label) { return label.contains("/") ? label.substring(0, label.indexOf('/')) : label; }

    /** The recording at {@code path}: a game log, or the attempt directory holding one per seed. */
    static File recordingFor(String path, long seed) {
        File f = new File(path.trim());
        if (f.isFile()) return f;
        if (!f.isDirectory()) throw new IllegalArgumentException("replay: no recording at " + path);
        List<File> hits = new ArrayList<>();
        File[] all = f.listFiles();
        if (all != null) for (File g : all) if (g.getName().startsWith("game-" + seed + "-") && g.getName().endsWith(".jsonl")) hits.add(g);
        if (hits.size() != 1)
            throw new IllegalArgumentException("replay: " + hits.size() + " game logs for seed " + seed + " in " + path
                    + " (one is needed; name the file)");
        return hits.get(0);
    }

    /** Both tiers replay one recording, or neither does: a replay on one tier would mix its turns with a model's. */
    static void requireBothTiers(String smartSpec, String fastSpec) {
        boolean s = smartSpec != null && smartSpec.startsWith(PREFIX), f = fastSpec != null && fastSpec.startsWith(PREFIX);
        if ((s || f) && !(s && f && smartSpec.equals(fastSpec)))
            throw new IllegalArgumentException("replay: both tiers must name the same recording (smart " + smartSpec
                    + ", fast " + fastSpec + ")");
    }

    /** The spec's recording for the engine seed this game was seeded with (LlmSpikeGui.SEED_USED). */
    static ReplayBridge fromSpec(String label, String rest) {
        return fromFile(label, recordingFor(rest, LlmSpikeGui.SEED_USED).getPath());
    }

    /** The turns and decision rows of {@code label}'s seat in the game log at {@code path}. */
    static ReplayBridge fromFile(String label, String path) {
        List<String> lines;
        try { lines = Files.readAllLines(new File(path).toPath(), StandardCharsets.UTF_8); }
        catch (IOException e) { throw new IllegalArgumentException("replay: cannot read " + path + ": " + e); }
        String seat = seatOf(label);
        List<Turn> turns = new ArrayList<>();
        List<Row> rows = new ArrayList<>();
        for (String l : lines) {
            if (l.trim().isEmpty()) continue;
            JsonObject r;
            try { r = JsonParser.parseString(l).getAsJsonObject(); } catch (RuntimeException e) { continue; }
            if (!seat.equals(str(r, "seat"))) continue;
            String t = str(r, "t");
            if ("context_call".equals(t)) {
                // A text-protocol turn (the MEMORY_TOOLS text loop) has no calls to serve back: refused, not guessed.
                if (!"native".equals(str(r, "protocol")))
                    throw new IllegalArgumentException("replay: " + path + " holds a text-protocol model turn (call "
                            + str(r, "call") + "); only native tool-call turns replay");
                int n = 1;
                List<String> results = new ArrayList<>();
                JsonElement m = r.get("messages");
                if (m != null && m.isJsonArray()) {
                    n = m.getAsJsonArray().size();
                    results = toolResults(m.getAsJsonArray());
                }
                JsonElement raw = r.get("raw_reply");
                turns.add(new Turn(r.has("call") ? r.get("call").getAsInt() : turns.size() + 1, str(r, "prompt"), n,
                        results, raw == null || raw.isJsonNull() ? null : raw.getAsString()));
            } else if ("decision".equals(t)) {
                List<String> opts = new ArrayList<>();
                JsonElement o = r.get("options");
                if (o != null && o.isJsonArray()) for (JsonElement e : o.getAsJsonArray()) opts.add(e.getAsString());
                rows.add(new Row(str(r, "type"), r.get("n_options").getAsInt(), r.get("choice").getAsInt(), opts));
            }
        }
        if (turns.isEmpty()) throw new IllegalArgumentException("replay: no model turn of seat " + seat + " in " + path);
        ReplayBridge b = new ReplayBridge(label, path, turns, rows);
        b.readResult(new File(path));
        return b;
    }

    /** The attempt's result row: puzzle-results.jsonl beside the game log, the row of the seed in its name. */
    private void readResult(File log) {
        java.util.regex.Matcher m = java.util.regex.Pattern.compile("^game-(-?\\d+)-").matcher(log.getName());
        File results = new File(log.getAbsoluteFile().getParentFile(), "puzzle-results.jsonl");
        if (!m.find() || !results.isFile()) return;
        try {
            for (String l : Files.readAllLines(results.toPath(), StandardCharsets.UTF_8)) {
                if (l.trim().isEmpty()) continue;
                JsonObject r = JsonParser.parseString(l).getAsJsonObject();
                if (!m.group(1).equals(str(r, "seed"))) continue;
                recordedStatus = str(r, "status");
                recordedLogSha = str(r, "log_sha256");
            }
        } catch (IOException | RuntimeException e) {
            throw new IllegalArgumentException("replay: cannot read " + results + ": " + e);
        }
    }

    /** The game is over with {@code status} and engine log fingerprint {@code logSha}: the recorded attempt's, when it
     *  has a result row. */
    void atEnd(String status, String logSha) {
        if (recordedStatus == null) return;
        if (!recordedStatus.equals(status)) fail("game end: status " + status + " where the recording's was " + recordedStatus);
        else if (recordedLogSha != null && !recordedLogSha.equals(logSha))
            fail("game end: engine log " + logSha + " where the recording's was " + recordedLogSha);
    }

    private static String str(JsonObject o, String k) {
        JsonElement e = o.get(k);
        return e == null || e.isJsonNull() ? null : e.isJsonPrimitive() ? e.getAsString() : e.toString();
    }

    private static List<String> toolResults(JsonArray msgs) {
        List<String> out = new ArrayList<>();
        for (JsonElement m : msgs) if ("tool".equals(ScriptBridge.role(m))) out.add(ScriptBridge.text(m));
        return out;
    }

    /** The recorded turn's calls, as the model served them; a turn with no call is its text. */
    static NativeTools.Turn turnOf(String raw, int seq) {
        String marker = NativeTools.Call.RENDER_PREFIX;
        if (!raw.startsWith(marker)) {
            JsonObject msg = new JsonObject();
            msg.addProperty("role", "assistant");
            msg.addProperty("content", raw);
            return new NativeTools.Turn(raw, msg.toString(), new ArrayList<>());
        }
        List<NativeTools.Call> cs = new ArrayList<>();
        for (String part : ("\n" + raw).split("\n" + marker)) {
            if (part.isEmpty()) continue;
            int sp = part.indexOf(' ');
            String name = sp < 0 ? part : part.substring(0, sp);
            String args = sp < 0 ? "{}" : part.substring(sp + 1);
            cs.add(new NativeTools.Call("replay" + seq + "-" + cs.size(), name, args));
        }
        return NativeTools.Turn.ofCalls(cs);
    }

    /** Every recorded turn was served and every recorded decision row matched, with nothing drifted. */
    boolean reproduced() { return drift == null && unusedTurns() == 0 && rowPos == rows.size(); }

    int unusedTurns() { return turns.size() - pos; }

    /** The result row's fields (LlmSpikePuzzle). */
    Map<String, Object> report() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("replay_version", VERSION);
        m.put("replay_source", source);
        m.put("replay_drift", drift == null ? "" : drift);
        m.put("replay_reproduced", reproduced());
        m.put("replay_turns", turns.size());
        m.put("replay_turns_used", pos);
        m.put("replay_rows", rows.size());
        m.put("replay_rows_matched", rowPos);
        m.put("replay_recorded_status", recordedStatus == null ? "" : recordedStatus);
        m.put("replay_prompt_diffs", promptDiffs);
        if (firstPromptDiff != null) m.put("replay_first_prompt_diff", firstPromptDiff);
        return m;
    }

    private void fail(String why) {
        if (drift != null) return;
        drift = why;
        System.out.println("[REPLAY] DRIFT " + why);
        Runnable stop = onDrift;
        if (stop != null) stop.run();
    }

    @Override public boolean supportsNativeTools() { return true; }
    @Override public String getLabel() { return label; }
    @Override public String getModel() { return "replay"; }
    @Override public long getTotalMs() { return 0; }
    @Override public int getCalls() { return calls; }
    /** The seat's bridge retry (askModel) restarts before it asks again: that ask is the same recorded turn. */
    @Override public void restart() { retrying = true; }

    /** A text ask has no recorded turn to serve: the recording answered by call. */
    @Override public String ask(String prompt) {
        calls++;
        if (drift != null) asksAfterDrift++;
        fail("a text ask (no `answer` offered); the recording answered every ask by call");
        return null;
    }

    @Override
    public NativeTools.Turn chat(String messagesJson, String toolsJson, boolean allowCalls) {
        calls++;
        JsonArray msgs = JsonParser.parseString(messagesJson).getAsJsonArray();
        String prompt = ScriptBridge.text(msgs.get(0));
        ScriptBridge.Question live = new ScriptBridge.Question(prompt);
        boolean retry = retrying;
        retrying = false;
        if (drift != null) { asksAfterDrift++; return doNothing(live); }
        int idx = retry && pos > 0 ? pos - 1 : pos;
        if (idx == pos && idx < turns.size()) pos++;
        if (idx >= turns.size()) {
            fail("turn " + live.turn + " " + live.phase + ": ask " + (idx + 1) + " comes after the recording's last ("
                    + turns.size() + "): " + live.head());
            return doNothing(live);
        }
        Turn rec = turns.get(idx);
        String diff = differs(new ScriptBridge.Question(rec.prompt == null ? "" : rec.prompt), live);
        if (diff == null && msgs.size() != rec.messages)
            diff = msgs.size() + " messages where the recording had " + rec.messages;
        if (diff == null) {
            List<String> results = toolResults(msgs);
            for (int i = 0; i < results.size() && diff == null; i++) {
                boolean liveErr = results.get(i).startsWith("ERROR:");
                boolean recErr = i < rec.toolResults.size() && rec.toolResults.get(i).startsWith("ERROR:");
                if (liveErr != recErr) diff = "tool result " + (i + 1) + " is " + (liveErr ? "a refusal" : "accepted")
                        + " where the recording's was " + (recErr ? "a refusal" : "accepted") + ": " + results.get(i);
            }
        }
        if (diff != null) {
            fail("turn " + live.turn + " " + live.phase + ", ask " + (idx + 1) + " (recorded call " + rec.call + "): " + diff);
            return doNothing(live);
        }
        if (!Objects.equals(prompt, rec.prompt)) {
            promptDiffs++;
            if (firstPromptDiff == null) firstPromptDiff = "ask " + (idx + 1) + ": " + firstDifference(rec.prompt, prompt);
        }
        return rec.raw == null ? null : turnOf(rec.raw, idx + 1);
    }

    /** What makes the live question another than the recorded one, or null when it is the same question. */
    static String differs(ScriptBridge.Question rec, ScriptBridge.Question live) {
        if (!rec.field.equals(live.field)) return "answered with `" + live.field + "` where the recording's was `" + rec.field + "`";
        if (rec.turn != live.turn || !rec.phase.equals(live.phase))
            return "asked at T" + live.turn + " " + live.phase + " where the recording's was at T" + rec.turn + " " + rec.phase;
        List<String> a = new ArrayList<>(rec.options.values()), b = new ArrayList<>(live.options.values());
        if (!a.equals(b)) return "options " + b + " where the recording's were " + a;
        a = listed(rec); b = listed(live);
        if (!a.equals(b)) return "lists " + b + " where the recording's were " + a;
        a = ranges(rec); b = ranges(live);
        if (!a.equals(b)) return "answer range " + b + " where the recording's was " + a;
        if (!rec.head().equals(live.head())) return "asked \"" + live.head() + "\" where the recording asked \"" + rec.head() + "\"";
        return null;
    }

    private static final java.util.regex.Pattern LISTED = java.util.regex.Pattern.compile("(?m)^([A-Z][A-Z ]*):\\s*(\\d+ = .*)$");

    /** The numbered lists of the DECISION block ("YOUR BLOCKERS: 1 = Typhoid Rats 1/1 @3; 2 = ..."), one per line. */
    static List<String> listed(ScriptBridge.Question q) {
        List<String> out = new ArrayList<>();
        java.util.regex.Matcher m = LISTED.matcher(q.block);
        while (m.find()) out.add(m.group(1) + ": " + m.group(2).trim());
        return out;
    }

    /** The answer's range as the ask states it: how many picks, the number's lower end, the amounts and their sum. */
    static List<String> ranges(ScriptBridge.Question q) {
        List<String> out = new ArrayList<>();
        for (java.util.regex.Pattern p : new java.util.regex.Pattern[] {ScriptBridge.PICK_RANGE, ScriptBridge.NUMBER_FROM,
                ScriptBridge.AMOUNTS_SPEC}) {
            java.util.regex.Matcher m = p.matcher(q.prompt);
            while (m.find()) out.add(m.group());
        }
        return out;
    }

    private static String firstDifference(String a, String b) {
        String x = a == null ? "" : a, y = b == null ? "" : b;
        int i = 0;
        while (i < x.length() && i < y.length() && x.charAt(i) == y.charAt(i)) i++;
        int from = Math.max(0, i - 40);
        return "at char " + i + ": recorded \"" + LlmSpikeGui.oneLine(x.substring(from, Math.min(x.length(), i + 60)))
                + "\" vs live \"" + LlmSpikeGui.oneLine(y.substring(from, Math.min(y.length(), i + 60))) + "\"";
    }

    private static NativeTools.Turn doNothing(ScriptBridge.Question q) {
        List<NativeTools.Call> one = new ArrayList<>();
        one.add(new NativeTools.Call("replay-drift", AnswerTool.NAME, q.fallback().toString()));
        return NativeTools.Turn.ofCalls(one);
    }

    /** A decision row the seat just wrote (LlmSpikeGui.FORK_HOOK): it must be the next recorded row of this seat. */
    void onDecisionRow(String rowSeat, String type, int n, int choice, List<String> options) {
        if (!seat.equals(rowSeat) || drift != null) return;
        Row live = new Row(type, n, choice, options == null ? Collections.emptyList() : new ArrayList<>(options));
        if (rowPos >= rows.size()) { fail("decision row " + (rowPos + 1) + " comes after the recording's last: " + live); return; }
        Row rec = rows.get(rowPos);
        if (!rec.toString().equals(live.toString())) {
            fail("decision row " + (rowPos + 1) + ": " + live + " where the recording's was " + rec);
            return;
        }
        rowPos++;
    }
}
