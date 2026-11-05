package forge.spike;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.TimeUnit;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

/**
 * A seat played by a Claude model through the Claude Code CLI in print mode (`claude-cli:<model>` specs). Owner
 * decisions 2026-10-24: Opus 5.5 may be tested "with harnesses" this way, as a development lane on the owner's own
 * Claude subscription, used only when the operator opts in ({@link #OPT_IN}=1) and never part of a benchmark score.
 *
 * <p>The CLI has no function calls of its own to offer, but `--json-schema` makes Claude Code end the model's turn with
 * a forced structured-output tool call whose input is validated against the schema. That schema is built from the
 * ask's own `answer` and `set_plan` definitions (the ones every `openrouter:` seat gets, read from the tool list the
 * seat sends, never copied), and the returned `structured_output` becomes the harness's `answer` (and `set_plan`)
 * call, so AnswerTool validates it, re-asks and falls back exactly as for any model (docs/harness/ARCHITECTURE.md,
 * "The Claude CLI answer bridge").
 *
 * <p>One CLI process per model turn: the harness renders every decision fresh (ARCHITECTURE section 8), so there is no
 * conversation to keep across decisions; a correction or reminder round within one decision is a new process whose
 * prompt carries that decision's earlier turns as text. The process is isolated from the machine it runs on: no
 * built-in tools, no MCP servers, no skills, slash commands, hooks, CLAUDE.md or settings files, no saved session, an
 * empty working directory, the seat's own system prompt in place of Claude Code's, and an allow-listed environment
 * ({@link #childEnv}).
 *
 * <p>What it cannot do in phase 1: look anything up or preview (refused when built, like a `jev:` seat), and answer a
 * plain text ask.
 */
final class ClaudeCliBridge implements LLMBridge {
    static final String PREFIX = "claude-cli:";
    /** v1 (2026-10-24): one `claude -p --output-format json --json-schema` process per model turn; the schema is
     *  {answer, set_plan} from the ask's own definitions. Stamped on the game line of a game with a claude-cli: seat
     *  and on a claude-cli: solver's puzzle row (eval/versions.py); pinned in ClaudeCliBridgeTest and in
     *  eval/harness_acceptance.sh.
     *  v2 (2026-10-24, pr-review of #320): the child's environment is an allow-list (v1 passed on MAX_THINKING_TOKENS
     *  and ANTHROPIC_MODEL from the launching shell, unstamped), its thinking budget is CLAUDE_CLI_THINKING_TOKENS
     *  (stamped), and a seat is built only with CLAUDE_CLI_SEAT=1. */
    static final String VERSION = "claude-cli-bridge-v2";
    /** The operator's explicit choice of this lane: the factory refuses a claude-cli: spec unless it is "1", and so do
     *  run_batch.py and puzzle_solve.py (eval/cost_report.py CLI_OPT_IN). */
    static final String OPT_IN = "CLAUDE_CLI_SEAT";
    /** The seat's thinking budget, set by run_batch.py / puzzle_solve.py --claude-cli-thinking-tokens and passed to the
     *  child as MAX_THINKING_TOKENS; unset or empty is the CLI's own default. Stamped. */
    static final String THINKING_ENV = "CLAUDE_CLI_THINKING_TOKENS";
    /** The development-lane label on the game line and the puzzle row (either CLI lane: claude-cli: or a bare name). */
    static final String DEV_LANE = "claude-cli";
    /** The operator's request that the child may log in with an API key (ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN). Off:
     *  the lane is the owner's subscription login, so those keys are dropped. */
    static final String API_KEY_OPT_IN = "CLAUDE_CLI_ALLOW_API_KEY";
    /** What the child may inherit, by name: how it runs, reaches the network and logs in to the subscription. None of
     *  them chooses the model, its thinking or its effort (those are argv or {@link #THINKING_ENV}, both stamped);
     *  everything else, among it MAX_THINKING_TOKENS, ANTHROPIC_MODEL and its per-tier defaults, the launching session's
     *  CLAUDE* variables and the harness's OPENROUTER* ones, is dropped (pr-review of #320). */
    static final Set<String> KEEP_ENV = Collections.unmodifiableSet(new java.util.TreeSet<>(Arrays.asList(
            "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "LANG", "TZ", "TMPDIR",
            "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "no_proxy", "all_proxy",
            "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS", "NODE_OPTIONS",
            "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR")));
    /** An API key login: kept only when the operator asks ({@link #API_KEY_OPT_IN}). */
    static final List<String> API_KEY_ENV = List.of("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN");
    /** A cloud provider's switch and the credentials kept only while that switch is set. */
    static final Map<String, List<String>> PROVIDER_ENV = Map.of(
            "CLAUDE_CODE_USE_BEDROCK", List.of("AWS_REGION", "AWS_DEFAULT_REGION", "AWS_PROFILE", "AWS_CONFIG_FILE",
                    "AWS_SHARED_CREDENTIALS_FILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
                    "AWS_BEARER_TOKEN_BEDROCK"),
            "CLAUDE_CODE_USE_VERTEX", List.of("GOOGLE_APPLICATION_CREDENTIALS", "CLOUD_ML_REGION", "ANTHROPIC_VERTEX_PROJECT_ID",
                    "VERTEX_REGION_*"),
            "CLAUDE_CODE_USE_FOUNDRY", List.of("ANTHROPIC_FOUNDRY_API_KEY", "ANTHROPIC_FOUNDRY_RESOURCE", "ANTHROPIC_FOUNDRY_BASE_URL"));
    /** Test hook: variables added to the launching environment before it is filtered, so a test proves the filter in
     *  a JVM whatever its own environment holds. Null outside tests. */
    static volatile Map<String, String> plantedEnv;
    private static final String DEADLINE_ENV = System.getenv("CLAUDE_DEADLINE_MS");
    // 300s, the API lane's timeout: the CLI lane may think as long as the API lane.
    private static final long DEADLINE_MS = parseLong(DEADLINE_ENV, 300000);
    /** The levels the CLI's {@code --effort} takes. */
    private static final Set<String> EFFORT_LEVELS = Collections.unmodifiableSet(new java.util.LinkedHashSet<>(
            Arrays.asList("low", "medium", "high", "xhigh", "max")));
    /** Characters of the child's stderr kept for the no-answer log line. */
    private static final int STDERR_TAIL = 400;

    private final String label, model, preamble, bin;
    private final long deadlineMs;
    private final String effort, thinking;
    private final boolean allowApiKey;
    private volatile boolean loginLogged = false;
    private Path home;           // the bridge's own temp directory: the system prompt file and the empty cwd
    private long totalMs = 0;
    private int calls = 0;
    private volatile int lastPromptTokens = -1, lastCompletionTokens = -1, lastCachedTokens = -1, lastCacheWriteTokens = -1;
    /** The cost of this ask's attempts so far: every billed process since the last one that was not a retry. */
    private volatile double askCostUsd = -1;
    /** The next call is the seat's bridge retry of the last ({@link #restart}), so its cost adds to the same ask. */
    private volatile boolean retrying = false;
    private volatile long billedPromptTokens = 0, billedCompletionTokens = 0;
    private volatile double costUsd = 0;

    ClaudeCliBridge(String label, String model, String preamble) {
        this(label, model, preamble, System.getenv().getOrDefault("CLAUDE_BIN", "claude"),
                deadlineFor(model),
                effortFor(label != null && label.endsWith("/fast")),
                thinkingTokens(System.getenv(THINKING_ENV)), "1".equals(System.getenv(API_KEY_OPT_IN)));
    }

    /** Every input passed in, so a test can point it at a fake binary with a short deadline. */
    ClaudeCliBridge(String label, String model, String preamble, String bin, long deadlineMs, String effort, String thinking,
                    boolean allowApiKey) {
        this.label = label;
        this.model = model;
        this.preamble = preamble;
        this.bin = bin;
        this.deadlineMs = deadlineMs;
        this.effort = effort;
        this.thinking = thinking;
        this.allowApiKey = allowApiKey;
    }

    static boolean isSpec(String spec) { return spec != null && spec.startsWith(PREFIX); }

    private static long parseLong(String v, long def) {
        try { return v == null ? def : Long.parseLong(v.trim()); } catch (Exception e) { return def; }
    }

    /** CLAUDE_DEADLINE_MS as set, else 300 s: the game line's `claude_deadline_ms`. */
    static long deadlineMs() { return DEADLINE_MS; }

    /** A call's deadline for {@code model}: CLAUDE_DEADLINE_MS when an operator pins one, else ModelCfg's. */
    static long deadlineFor(String model) {
        return (DEADLINE_ENV != null && !DEADLINE_ENV.isEmpty()) ? DEADLINE_MS : LlmSpikeGui.ModelCfg.forModel(model).timeoutMs;
    }

    /**
     * The CLI's {@code --effort} for a tier, from CLAUDE_BRIDGE_EFFORT[_SMART|_FAST] (run_batch.py and puzzle_solve.py pin
     * it from --claude-cli-effort); null, the CLI's own default, when unset. Effort is part of the measurement, so it is
     * validated here (an unknown level is fatal, not a per-call failure mid-game) and stamped (claude_effort_smart /
     * _fast). Never read from CLAUDE_EFFORT: that is Claude Code's own variable, often set in the launching shell.
     */
    static String effortFor(boolean fastTier) {
        String v = System.getenv(fastTier ? "CLAUDE_BRIDGE_EFFORT_FAST" : "CLAUDE_BRIDGE_EFFORT_SMART");
        if (v == null || v.trim().isEmpty()) v = System.getenv("CLAUDE_BRIDGE_EFFORT");
        if (v == null || v.trim().isEmpty()) return null;
        String e = v.trim().toLowerCase(java.util.Locale.ROOT);
        if (!EFFORT_LEVELS.contains(e))
            throw new IllegalArgumentException("invalid CLAUDE_BRIDGE_EFFORT '" + v.trim() + "' — valid levels: "
                    + String.join(", ", EFFORT_LEVELS));
        return e;
    }

    /** What provenance records: the resolved level, or "cli-default" when the flag is omitted. */
    static String effortLabel(boolean fastTier) {
        String e = effortFor(fastTier);
        return e == null ? "cli-default" : e;
    }

    /** The effort a claude-cli: solver ran at, for its puzzle row: the smart tier's level (CLAUDE_BRIDGE_EFFORT[_SMART],
     *  "cli-default" when unset), and the fast tier's beside it when the two differ. */
    static String effortStamp() {
        String smart = effortLabel(false), fast = effortLabel(true);
        return smart.equals(fast) ? smart : "smart=" + smart + ",fast=" + fast;
    }

    /** {@link #THINKING_ENV}'s value: a positive number of tokens, or null (unset or empty: the CLI's default). A value
     *  that is neither is fatal here, not a per-call failure mid-game. */
    static String thinkingTokens(String v) {
        if (v == null || v.trim().isEmpty()) return null;
        try {
            int n = Integer.parseInt(v.trim());
            if (n > 0) return Integer.toString(n);
        } catch (NumberFormatException ignore) { }
        throw new IllegalArgumentException("invalid " + THINKING_ENV + " '" + v.trim() + "': a positive number of tokens");
    }

    /** What the game line and the puzzle row record of the thinking budget. */
    static String thinkingStamp(String tokens) { return tokens == null ? "cli-default" : tokens; }

    /** The budget this JVM's claude-cli: seats run with, as stamped. */
    static String thinkingStamp() { return thinkingStamp(thinkingTokens(System.getenv(THINKING_ENV))); }

    /** The child's environment: the allow-listed part of {@code parent} ({@link #KEEP_ENV} and locale variables; a
     *  provider's credentials only with its switch; an API key only when {@code allowApiKey}), and MAX_THINKING_TOKENS only
     *  when the harness sets a budget ({@code thinking}), never the parent's own. */
    static Map<String, String> childEnv(Map<String, String> parent, String thinking, boolean allowApiKey) {
        Set<String> keep = new java.util.HashSet<>(KEEP_ENV);
        if (allowApiKey) keep.addAll(API_KEY_ENV);
        List<String> prefixes = new ArrayList<>(List.of("LC_"));
        for (Map.Entry<String, List<String>> p : PROVIDER_ENV.entrySet()) {
            if (!truthy(parent.get(p.getKey()))) continue;
            keep.add(p.getKey());
            for (String k : p.getValue()) if (k.endsWith("*")) prefixes.add(k.substring(0, k.length() - 1)); else keep.add(k);
        }
        Map<String, String> env = new HashMap<>();
        for (Map.Entry<String, String> e : parent.entrySet())
            if (keep.contains(e.getKey()) || prefixes.stream().anyMatch(e.getKey()::startsWith)) env.put(e.getKey(), e.getValue());
        if (thinking != null) env.put("MAX_THINKING_TOKENS", thinking);
        return env;
    }

    /** A provider switch is on only when set to "1" or "true", as the CLI reads it. */
    static boolean truthy(String v) { return v != null && ("1".equals(v.trim()) || "true".equalsIgnoreCase(v.trim())); }

    /** How the child logs in, for the log: a cloud provider, an API key, an OAuth token, or the CLI's own stored login. */
    static String loginSource(Map<String, String> child) {
        if (child.containsKey("CLAUDE_CODE_USE_BEDROCK")) return "Bedrock (CLAUDE_CODE_USE_BEDROCK)";
        if (child.containsKey("CLAUDE_CODE_USE_VERTEX")) return "Vertex (CLAUDE_CODE_USE_VERTEX)";
        if (child.containsKey("CLAUDE_CODE_USE_FOUNDRY")) return "Foundry (CLAUDE_CODE_USE_FOUNDRY)";
        for (String k : API_KEY_ENV) if (child.containsKey(k)) return "API key (" + k + ")";
        if (child.containsKey("CLAUDE_CODE_OAUTH_TOKEN")) return "OAuth token (CLAUDE_CODE_OAUTH_TOKEN)";
        return "the CLI's own stored login (its config directory)";
    }

    /** The seat offers `answer` only to a bridge that answers by call; this one's answer is always that call. */
    @Override public boolean supportsNativeTools() { return true; }

    /** A text ask has no `answer` to put in the schema: refused, never parsed. */
    @Override public String ask(String prompt) {
        retrying = false;   // a retry that ends here is over: the next chat is a new ask
        noAnswer("a text ask (no `answer` offered)");
        return null;
    }

    /** The seat's bridge retry (LlmSpikeGui askModel) restarts the bridge before asking again: that attempt's cost is
     *  the same ask's. There is no session to reset. */
    @Override public void restart() { retrying = true; }

    @Override public NativeTools.Turn chat(String messagesJson, String toolsJson, boolean allowCalls) {
        if (!retrying) askCostUsd = -1;
        retrying = false;
        JsonObject schema = schema(toolsJson);
        if (schema == null) { noAnswer("an ask that offers no `answer`"); return null; }
        long t0 = System.currentTimeMillis();
        String prompt = prompt(messagesJson);
        JsonObject result = run(argv(schema.toString()), prompt);
        totalMs += System.currentTimeMillis() - t0;
        if (result == null) return null;
        JsonElement err = result.get("is_error");
        if (err != null && err.isJsonPrimitive() && err.getAsBoolean()) {
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
        System.out.println("[CLAUDE-CLI " + label + "] [CLAUDE-CLI-NO-ANSWER] " + why + " -> " + LLMBridge.NO_ANSWER);
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
     *  and `answer` (when given) as calls, with the CLI's `result` text as its content; no call at all when it holds no
     *  `answer` object, so AnswerTool reminds. */
    static NativeTools.Turn turn(JsonElement structured, String text, int seq) {
        List<NativeTools.Call> cs = new ArrayList<>();
        JsonObject so = structured != null && structured.isJsonObject() ? structured.getAsJsonObject() : null;
        for (String name : new String[] {AnswerTool.SET_PLAN, AnswerTool.NAME}) {
            JsonElement args = so == null ? null : so.get(name);
            if (args != null && args.isJsonObject()) cs.add(new NativeTools.Call("cli" + seq + "-" + name, name, args.toString()));
        }
        boolean said = text != null && !text.trim().isEmpty();
        String content = said ? text : cs.isEmpty() ? (so != null ? "structured output without `answer`: " + so : "(no reply)") : null;
        JsonObject msg = new JsonObject();
        msg.addProperty("role", "assistant");
        if (content != null) msg.addProperty("content", content);
        if (!cs.isEmpty()) msg.add("tool_calls", JsonParser.parseString(NativeTools.Turn.ofCalls(cs).messageJson)
                .getAsJsonObject().get("tool_calls"));
        return new NativeTools.Turn(content, msg.toString(), cs);
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

    /** A daemon thread copying {@code in} to {@code out}. */
    private Thread drain(InputStream in, ByteArrayOutputStream out, String what) {
        Thread t = new Thread(() -> {
            try { in.transferTo(out); } catch (IOException ignore) { }
        }, "claude-cli-" + what + "-" + label);
        t.setDaemon(true);
        t.start();
        return t;
    }

    /** One process: the prompt on stdin (written on its own thread, so a child that never reads it cannot hold the call
     *  past its deadline), the JSON result from stdout; null (logged, with the tail of stderr) on a timeout, a non-zero
     *  exit, or output that is not a result object. A result is billed whether or not it answers. */
    private JsonObject run(List<String> argv, String prompt) {
        lastPromptTokens = -1; lastCompletionTokens = -1; lastCachedTokens = -1; lastCacheWriteTokens = -1;
        Process proc = null;
        try {
            ProcessBuilder pb = new ProcessBuilder(argv);
            pb.directory(home().resolve("cwd").toFile());
            Map<String, String> parent = new HashMap<>(System.getenv());
            Map<String, String> planted = plantedEnv;
            if (planted != null) parent.putAll(planted);
            Map<String, String> child = childEnv(parent, thinking, allowApiKey);
            if (!loginLogged) {
                loginLogged = true;
                System.out.println("[CLAUDE-CLI " + label + "] login: " + loginSource(child) + "; thinking budget "
                        + thinkingStamp(thinking));
            }
            pb.environment().clear();
            pb.environment().putAll(child);
            proc = pb.start();
            ByteArrayOutputStream out = new ByteArrayOutputStream(), err = new ByteArrayOutputStream();
            Thread reader = drain(proc.getInputStream(), out, "stdout");
            Thread errReader = drain(proc.getErrorStream(), err, "stderr");
            final OutputStream stdin = proc.getOutputStream();
            Thread writer = new Thread(() -> {
                try (OutputStream in = stdin) {
                    in.write(prompt.getBytes(StandardCharsets.UTF_8));
                } catch (IOException ignore) {
                    // The process may exit, or be killed, before reading its stdin; its exit and output say why.
                }
            }, "claude-cli-stdin-" + label);
            writer.setDaemon(true);
            writer.start();
            if (!proc.waitFor(deadlineMs, TimeUnit.MILLISECONDS)) {
                noAnswer("no result within " + deadlineMs + " ms" + stderrTail(err));
                return null;
            }
            reader.join(5000);
            String text = out.toString(StandardCharsets.UTF_8);
            JsonObject result = resultOf(text);
            if (result != null) bill(result);
            if (proc.exitValue() != 0 && (result == null || !result.has("structured_output"))) {
                noAnswer("exit " + proc.exitValue() + ": " + LlmSpikeGui.oneLine(text) + drainedTail(errReader, err));
                return null;
            }
            if (result == null) {
                noAnswer("no JSON result: " + LlmSpikeGui.oneLine(text) + drainedTail(errReader, err));
                return null;
            }
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

    /** The stderr tail of a process that has exited, once its drain thread has copied it (bounded: a child that keeps
     *  the pipe open costs at most 5 s). Only the lines that print it wait, so an answered call never does. */
    private static String drainedTail(Thread errReader, ByteArrayOutputStream err) throws InterruptedException {
        errReader.join(5000);
        return stderrTail(err);
    }

    private static String stderrTail(ByteArrayOutputStream err) {
        String s = err.toString(StandardCharsets.UTF_8).replaceAll("\\s+", " ").trim();
        if (s.isEmpty()) return "";
        return " (stderr: " + (s.length() > STDERR_TAIL ? "..." + s.substring(s.length() - STDERR_TAIL) : s) + ")";
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
     *  adds to the ask's cost. The served model is the `modelUsage` key with the most output tokens: the one that
     *  answered, whether the spec names it in full or by an alias. */
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
            double c = r.get("total_cost_usd").getAsDouble();
            askCostUsd = Math.max(0, askCostUsd) + c;
            costUsd += c;
        }
        if (r.has("modelUsage") && r.get("modelUsage").isJsonObject()) {
            JsonObject mu = r.getAsJsonObject("modelUsage");
            String best = null;
            int most = -1;
            for (String k : mu.keySet()) {
                int o = mu.get(k).isJsonObject() ? intOf(mu.getAsJsonObject(k), "outputTokens") : -1;
                if (o > most) { most = o; best = k; }
            }
            if (best != null) LlmSpikeGui.servedModels.put(label, best);
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

    /** The cost of the current ask's attempts (its first and the seat's retries of it), each process that reported one;
     *  -1 when none did. Written on the ask's context_call row as `cost_usd`. */
    @Override public double getLastCostUsd() { return askCostUsd; }
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
