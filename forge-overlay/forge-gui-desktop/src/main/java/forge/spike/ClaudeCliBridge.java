package forge.spike;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.TimeUnit;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

/**
 * A seat played by a Claude model through the Claude Code CLI in print mode (`claude-cli:<model>` specs, owner decision
 * 2026-10-24: Opus 5.5 "with harnesses"). The CLI has no function calls of its own to offer, but `--json-schema` makes
 * Claude Code end the model's turn with a forced structured-output tool call whose input is validated against the
 * schema. That schema is built from the ask's own `answer` and `set_plan` definitions (the ones every
 * `openrouter:` seat gets, read from the tool list the seat sends, never copied), and the returned
 * `structured_output` becomes the harness's `answer` (and `set_plan`) call, so AnswerTool validates it, re-asks and
 * falls back exactly as for any model (docs/harness/ARCHITECTURE.md, "The Claude CLI answer bridge").
 *
 * <p>One CLI process per model turn: the harness renders every decision fresh (ARCHITECTURE section 8), so there is no
 * conversation to keep across decisions; a correction or reminder round within one decision is a new process whose
 * prompt carries that decision's earlier turns as text. The process is isolated from the machine it runs on: no
 * built-in tools, no MCP servers, no skills, slash commands, hooks, CLAUDE.md or settings files, no saved session, an
 * empty working directory, the seat's own system prompt in place of Claude Code's, and an environment without the
 * launching session's CLAUDE* variables.
 *
 * <p>What it cannot do in phase 1: look anything up or preview (refused when built, like a `jev:` seat), and answer a
 * plain text ask.
 */
final class ClaudeCliBridge implements LLMBridge {
    static final String PREFIX = "claude-cli:";
    /** v1 (2026-10-24): one `claude -p --output-format json --json-schema` process per model turn; the schema is
     *  {answer, set_plan} from the ask's own definitions. Stamped on the game line of a game with a claude-cli: seat
     *  and on a claude-cli: solver's puzzle row (eval/versions.py); pinned in ClaudeCliBridgeTest and in
     *  eval/harness_acceptance.sh. */
    static final String VERSION = "claude-cli-bridge-v1";
    /** The launching environment's variables a child keeps although they start with CLAUDE: how it authenticates and
     *  which provider it uses. Every other CLAUDE* variable (the parent session's id, entrypoint, effort, sockets,
     *  CLAUDECODE ...) is removed, and so is OPENROUTER_*: the harness's key is not the CLI's business. */
    static final Set<String> KEEP_ENV = Collections.unmodifiableSet(new java.util.TreeSet<>(Arrays.asList(
            "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
            "CLAUDE_CONFIG_DIR")));

    private final String label, model, preamble, bin;
    private final long deadlineMs;
    private final String effort;
    private Path home;           // the bridge's own temp directory: the system prompt file and the empty cwd
    private long totalMs = 0;
    private int calls = 0;
    private volatile int lastPromptTokens = -1, lastCompletionTokens = -1, lastCachedTokens = -1, lastCacheWriteTokens = -1;
    private volatile double lastCostUsd = -1;
    private volatile long billedPromptTokens = 0, billedCompletionTokens = 0;
    private volatile double costUsd = 0;
    /** The argv of the last process started, for the tests and the log. */
    volatile List<String> lastArgv;

    ClaudeCliBridge(String label, String model, String preamble) {
        this(label, model, preamble, System.getenv().getOrDefault("CLAUDE_BIN", "claude"),
                LlmSpikeGui.ClaudeBridge.deadlineFor(model),
                LlmSpikeGui.ClaudeBridge.effortFor(label != null && label.endsWith("/fast")));
    }

    /** Every input passed in, so a test can point it at a fake binary with a short deadline. */
    ClaudeCliBridge(String label, String model, String preamble, String bin, long deadlineMs, String effort) {
        this.label = label;
        this.model = model;
        this.preamble = preamble;
        this.bin = bin;
        this.deadlineMs = deadlineMs;
        this.effort = effort;
    }

    static boolean isSpec(String spec) { return spec != null && spec.startsWith(PREFIX); }

    /** The effort a claude-cli: solver ran at, for its puzzle row: the smart tier's level (CLAUDE_BRIDGE_EFFORT[_SMART],
     *  "cli-default" when unset), and the fast tier's beside it when the two differ. */
    static String effortStamp() {
        String smart = LlmSpikeGui.ClaudeBridge.effortLabel(false), fast = LlmSpikeGui.ClaudeBridge.effortLabel(true);
        return smart.equals(fast) ? smart : "smart=" + smart + ",fast=" + fast;
    }

    /** The seat offers `answer` only to a bridge that answers by call; this one's answer is always that call. */
    @Override public boolean supportsNativeTools() { return true; }

    /** A text ask has no `answer` to put in the schema: refused, never parsed. */
    @Override public String ask(String prompt) {
        noAnswer("a text ask (no `answer` offered)");
        return null;
    }

    @Override public NativeTools.Turn chat(String messagesJson, String toolsJson, boolean allowCalls) {
        JsonObject schema = schema(toolsJson);
        if (schema == null) { noAnswer("an ask that offers no `answer`"); return null; }
        long t0 = System.currentTimeMillis();
        String prompt = prompt(messagesJson);
        JsonObject result = run(argv(schema.toString()), prompt);
        totalMs += System.currentTimeMillis() - t0;
        if (result == null) return null;
        if (result.has("is_error") && result.get("is_error").getAsBoolean()) {
            noAnswer("the CLI reported an error (" + str(result, "subtype") + "): " + LlmSpikeGui.oneLine(str(result, "result")));
            return null;
        }
        calls++;
        NativeTools.Turn turn = turn(result.get("structured_output"), str(result, "result"), calls);
        System.out.println("[CLAUDE-CLI " + label + "] " + VERSION + " -> "
                + (turn.calls.isEmpty() ? "no answer call" : turn.text().replace('\n', ' ')) + " (prompt_bytes="
                + prompt.getBytes(StandardCharsets.UTF_8).length + ", input_tokens=" + lastPromptTokens + ", cost so far $"
                + String.format(java.util.Locale.ROOT, "%.6f", costUsd) + ")");
        return turn;
    }

    private void noAnswer(String why) {
        System.out.println("[CLAUDE-CLI " + label + "] [CLAUDE-CLI-NO-ANSWER] " + why + " -> no answer; the seat takes its "
                + "fixed legal option");
    }

    // ------------------------------------------------------------------ what is sent

    /**
     * The `--json-schema`: an object whose `answer` property is the `answer` function's own parameters and description,
     * and whose optional `set_plan` property is `set_plan`'s, both taken from the ask's tool list. Null when the list
     * offers no `answer`. Other functions in the list (a lookup) are not offered: such a seat is refused when built.
     */
    static JsonObject schema(String toolsJson) {
        JsonObject props = new JsonObject();
        try {
            for (JsonElement t : JsonParser.parseString(toolsJson).getAsJsonArray()) {
                JsonObject f = t.getAsJsonObject().getAsJsonObject("function");
                if (f == null || !f.has("name")) continue;
                String name = f.get("name").getAsString();
                if (!AnswerTool.NAME.equals(name) && !AnswerTool.SET_PLAN.equals(name)) continue;
                JsonObject p = f.getAsJsonObject("parameters").deepCopy();
                if (f.has("description")) p.add("description", f.get("description"));
                props.add(name, p);
            }
        } catch (RuntimeException e) {
            return null;
        }
        if (!props.has(AnswerTool.NAME)) return null;
        JsonObject s = new JsonObject();
        s.addProperty("type", "object");
        s.addProperty("description", "Your reply to the decision: `" + AnswerTool.NAME + "` is the `" + AnswerTool.NAME
                + "` call" + (props.has(AnswerTool.SET_PLAN) ? "; `" + AnswerTool.SET_PLAN + "`, optional, is the `"
                + AnswerTool.SET_PLAN + "` call" : "") + ".");
        s.add("properties", props);
        JsonArray req = new JsonArray();
        req.add(AnswerTool.NAME);
        s.add("required", req);
        s.addProperty("additionalProperties", false);
        return s;
    }

    /** The process's argv: print mode, one JSON result, the schema, and every isolation flag. No key, ever. */
    List<String> argv(String schema) {
        List<String> a = new ArrayList<>(Arrays.asList(bin, "-p", "--model", model,
                "--output-format", "json", "--json-schema", schema,
                "--system-prompt-file", systemPromptFile().toString(),
                "--tools", "", "--strict-mcp-config", "--no-session-persistence", "--safe-mode",
                "--setting-sources", "", "--disable-slash-commands"));
        if (effort != null) { a.add("--effort"); a.add(effort); }
        return a;
    }

    /**
     * The prompt, written to the process's stdin: the decision as the harness renders it, then, on a later round of the
     * same decision, each earlier turn in order (the model's own answer and the harness's reply to it, or a reminder).
     */
    static String prompt(String messagesJson) {
        StringBuilder b = new StringBuilder();
        for (JsonElement e : JsonParser.parseString(messagesJson).getAsJsonArray()) {
            JsonObject m = e.getAsJsonObject();
            String role = str(m, "role"), content = str(m, "content");
            if (b.length() > 0) b.append("\n\n");
            if ("assistant".equals(role)) {
                b.append("YOUR EARLIER REPLY TO THIS DECISION:");
                if (content != null && !content.trim().isEmpty()) b.append('\n').append(content.trim());
                JsonArray tcs = m.has("tool_calls") ? m.getAsJsonArray("tool_calls") : new JsonArray();
                for (JsonElement tc : tcs) {
                    JsonObject f = tc.getAsJsonObject().getAsJsonObject("function");
                    b.append("\nYou gave `").append(str(f, "name")).append("`: ").append(str(f, "arguments"));
                }
            } else if ("tool".equals(role)) {
                b.append("THE HARNESS REPLIED: ").append(content == null ? "" : content);
            } else {
                b.append(content == null ? "" : content);
            }
        }
        return b.toString();
    }

    /** The CLI's structured output as the assistant turn a calling model would have served: `set_plan` (when given)
     *  and `answer` (when given) as calls; no call at all when it holds no `answer` object, so AnswerTool reminds. */
    static NativeTools.Turn turn(JsonElement structured, String text, int seq) {
        List<NativeTools.Call> cs = new ArrayList<>();
        JsonObject so = structured != null && structured.isJsonObject() ? structured.getAsJsonObject() : null;
        for (String name : new String[] {AnswerTool.SET_PLAN, AnswerTool.NAME}) {
            JsonElement args = so == null ? null : so.get(name);
            if (args != null && args.isJsonObject()) cs.add(new NativeTools.Call("cli" + seq + "-" + name, name, args.toString()));
        }
        if (!cs.isEmpty()) {
            NativeTools.Turn calls = NativeTools.Turn.ofCalls(cs);
            if (text == null || text.trim().isEmpty()) return calls;
            JsonObject msg = JsonParser.parseString(calls.messageJson).getAsJsonObject();
            msg.addProperty("content", text);
            return new NativeTools.Turn(text, msg.toString(), cs);
        }
        String content = text != null && !text.trim().isEmpty() ? text
                : so != null ? "structured output without `answer`: " + so : "(no reply)";
        JsonObject msg = new JsonObject();
        msg.addProperty("role", "assistant");
        msg.addProperty("content", content);
        return new NativeTools.Turn(content, msg.toString(), new ArrayList<>());
    }

    // ------------------------------------------------------------------ the process

    /** This bridge's temp directory: `cwd/`, empty, where the CLI runs, and the system prompt file beside it. */
    private synchronized Path home() throws IOException {
        if (home == null) {
            home = Files.createTempDirectory("claude-cli-seat-");
            // Deleted at exit in the reverse of this order, so each directory is empty when its turn comes.
            home.toFile().deleteOnExit();
            Files.createDirectory(home.resolve("cwd"));
            home.resolve("cwd").toFile().deleteOnExit();
            Files.write(home.resolve("system-prompt.txt"), (preamble == null ? "" : preamble).getBytes(StandardCharsets.UTF_8));
            home.resolve("system-prompt.txt").toFile().deleteOnExit();
        }
        return home;
    }

    private Path systemPromptFile() {
        try {
            return home().resolve("system-prompt.txt");
        } catch (IOException e) {
            throw new IllegalStateException("cannot write the system prompt file (" + label + "): " + e, e);
        }
    }

    /** Removes from {@code env} every variable the child must not inherit (see {@link #KEEP_ENV}). */
    static void scrub(Map<String, String> env) {
        env.keySet().removeIf(k -> (k.startsWith("CLAUDE") && !KEEP_ENV.contains(k)) || k.startsWith("OPENROUTER"));
    }

    /** One process: the prompt on stdin, the JSON result from stdout; null (logged) on a timeout, a non-zero exit, or
     *  output that is not a result object. A result is billed whether or not it answers. */
    private JsonObject run(List<String> argv, String prompt) {
        lastPromptTokens = -1; lastCompletionTokens = -1; lastCachedTokens = -1; lastCacheWriteTokens = -1;
        lastCostUsd = -1;
        lastArgv = argv;
        Process proc = null;
        try {
            ProcessBuilder pb = new ProcessBuilder(argv);
            pb.directory(home().resolve("cwd").toFile());
            scrub(pb.environment());
            pb.redirectError(ProcessBuilder.Redirect.DISCARD);
            proc = pb.start();
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            final InputStream stdout = proc.getInputStream();
            Thread reader = new Thread(() -> {
                try { stdout.transferTo(out); } catch (IOException ignore) { }
            }, "claude-cli-stdout-" + label);
            reader.setDaemon(true);
            reader.start();
            try (OutputStream in = proc.getOutputStream()) {
                in.write(prompt.getBytes(StandardCharsets.UTF_8));
            } catch (IOException e) {
                // The process may exit before reading its stdin; its exit code and output say why.
            }
            if (!proc.waitFor(deadlineMs, TimeUnit.MILLISECONDS)) {
                noAnswer("no result within " + deadlineMs + " ms");
                return null;
            }
            reader.join(5000);
            String text = out.toString(StandardCharsets.UTF_8);
            JsonObject result = resultOf(text);
            if (result != null) bill(result);
            if (proc.exitValue() != 0 && (result == null || !result.has("structured_output"))) {
                noAnswer("exit " + proc.exitValue() + ": " + LlmSpikeGui.oneLine(text));
                return null;
            }
            if (result == null) { noAnswer("no JSON result: " + LlmSpikeGui.oneLine(text)); return null; }
            return result;
        } catch (Exception e) {
            noAnswer("error: " + e);
            return null;
        } finally {
            if (proc != null && proc.isAlive()) {
                proc.descendants().forEach(ProcessHandle::destroyForcibly);
                proc.destroyForcibly();
            }
        }
    }

    /** The result object: stdout as one JSON object, else its last line that parses as one, with `"type":"result"`. */
    static JsonObject resultOf(String stdout) {
        try {
            JsonObject whole = JsonParser.parseString(stdout.trim()).getAsJsonObject();
            if ("result".equals(str(whole, "type"))) return whole;
        } catch (RuntimeException ignore) { }
        String[] lines = stdout.split("\\R");
        for (int i = lines.length - 1; i >= 0; i--) {
            String l = lines[i].trim();
            if (!l.startsWith("{")) continue;
            try {
                JsonObject o = JsonParser.parseString(l).getAsJsonObject();
                if ("result".equals(str(o, "type"))) return o;
            } catch (RuntimeException ignore) { }
        }
        return null;
    }

    /** `usage` is the seat's model's tokens (input plus both cache counts is the prompt, cache reads the cached part,
     *  cache writes the written part); `total_cost_usd`, which also counts the CLI's own side calls to another model,
     *  is the call's `cost_usd`. The served model is the `modelUsage` key that names the requested one. */
    private void bill(JsonObject r) {
        JsonObject u = r.has("usage") && r.get("usage").isJsonObject() ? r.getAsJsonObject("usage") : null;
        if (u != null) {
            int in = intOf(u, "input_tokens"), cw = intOf(u, "cache_creation_input_tokens"), cr = intOf(u, "cache_read_input_tokens");
            lastPromptTokens = in < 0 ? -1 : in + Math.max(0, cw) + Math.max(0, cr);
            lastCompletionTokens = intOf(u, "output_tokens");
            lastCachedTokens = cr;
            lastCacheWriteTokens = cw;
            billedPromptTokens += Math.max(0, lastPromptTokens);
            billedCompletionTokens += Math.max(0, lastCompletionTokens);
        }
        if (r.has("total_cost_usd") && r.get("total_cost_usd").isJsonPrimitive()) {
            lastCostUsd = r.get("total_cost_usd").getAsDouble();
            costUsd += lastCostUsd;
        }
        if (r.has("modelUsage") && r.get("modelUsage").isJsonObject()) {
            for (String k : r.getAsJsonObject("modelUsage").keySet())
                if (k.startsWith(model)) { LlmSpikeGui.servedModels.put(label, k); break; }
        }
    }

    private static int intOf(JsonObject o, String k) {
        JsonElement e = o.get(k);
        return e != null && e.isJsonPrimitive() && e.getAsJsonPrimitive().isNumber() ? e.getAsInt() : -1;
    }

    private static String str(JsonObject o, String k) {
        JsonElement e = o == null ? null : o.get(k);
        return e != null && e.isJsonPrimitive() ? e.getAsString() : null;
    }

    /** The CLI's own reported cost of every process that returned a result, in USD. */
    double getCostUsd() { return costUsd; }

    /** The process's working directory (empty), for the tests. */
    File workDir() throws IOException { return home().resolve("cwd").toFile(); }

    @Override public double getLastCostUsd() { return lastCostUsd; }
    @Override public String getLabel() { return label; }
    @Override public String getModel() { return model; }
    @Override public long getTotalMs() { return totalMs; }
    @Override public int getCalls() { return calls; }
    @Override public int getLastPromptTokens() { return lastPromptTokens; }
    @Override public int getLastCompletionTokens() { return lastCompletionTokens; }
    @Override public int getLastCachedTokens() { return lastCachedTokens; }
    @Override public int getLastCacheWriteTokens() { return lastCacheWriteTokens; }
    @Override public long getBilledPromptTokens() { return billedPromptTokens; }
    @Override public long getBilledCompletionTokens() { return billedCompletionTokens; }
}
