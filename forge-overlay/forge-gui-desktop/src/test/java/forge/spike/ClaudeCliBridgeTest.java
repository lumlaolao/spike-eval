package forge.spike;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.testng.Assert;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import forge.game.Game;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.spellability.SpellAbility;
import forge.game.zone.ZoneType;

/**
 * The Claude CLI answer seat (`claude-cli:` specs, ClaudeCliBridge): Claude Code in print mode, isolated, answering
 * through `--json-schema` structured output that becomes the harness's own `answer` call. Every process here is a fake
 * `claude` (a shell script written per test that records its argv, environment, working directory and stdin and prints
 * a scripted result); nothing in this file can reach a model.
 */
@Test(singleThreaded = true) // each test writes its own fake binary, but the seat's static state is shared
public class ClaudeCliBridgeTest {

    private Path dir;

    /** The fake CLI: call n records argv.n (NUL-separated), env.n, cwd.n, ls.n (the cwd's entries), its pid (self.pid)
     *  and stdin.n (unless `noread` exists), copies `stderr` to its stderr, waits on a child sleeping `sleep` seconds
     *  (child.pid) when that file exists, then prints reply.n (else reply) and exits with `exit` (else 0). */
    private static final String FAKE = "#!/bin/bash\n"
            + "d=\"$(cd \"$(dirname \"$0\")\" && pwd)\"\n"
            + "n=$(cat \"$d/count\" 2>/dev/null || echo 0); n=$((n+1)); echo $n > \"$d/count\"\n"
            + "printf '%s\\0' \"$@\" > \"$d/argv.$n\"\n"
            + "env > \"$d/env.$n\"\n"
            + "pwd > \"$d/cwd.$n\"\n"
            + "ls -A > \"$d/ls.$n\"\n"
            + "echo $$ > \"$d/self.pid\"\n"
            + "[ -f \"$d/noread\" ] || cat > \"$d/stdin.$n\"\n"
            + "[ -f \"$d/stderr\" ] && cat \"$d/stderr\" >&2\n"
            + "if [ -f \"$d/sleep\" ]; then sleep \"$(cat \"$d/sleep\")\" & echo $! > \"$d/child.pid\"; wait; fi\n"
            + "r=\"$d/reply.$n\"; [ -f \"$r\" ] || r=\"$d/reply\"\n"
            + "[ -f \"$r\" ] && cat \"$r\"\n"
            + "exit \"$(cat \"$d/exit\" 2>/dev/null || echo 0)\"\n";

    @BeforeMethod
    public void fakeCli() throws IOException {
        dir = Files.createTempDirectory("fake-claude-");
        Path bin = dir.resolve("claude");
        Files.write(bin, FAKE.getBytes(StandardCharsets.UTF_8));
        Assert.assertTrue(bin.toFile().setExecutable(true));
    }

    private void reply(String json) throws IOException { write("reply", json); }
    private void write(String name, String text) throws IOException {
        Files.write(dir.resolve(name), text.getBytes(StandardCharsets.UTF_8));
    }
    private String read(String name) throws IOException {
        return new String(Files.readAllBytes(dir.resolve(name)), StandardCharsets.UTF_8);
    }
    private List<String> argv(int n) throws IOException {
        String s = read("argv." + n);
        return new ArrayList<>(Arrays.asList(s.substring(0, s.length() - 1).split("\0", -1)));
    }
    private int processes() throws IOException {
        return Files.exists(dir.resolve("count")) ? Integer.parseInt(read("count").trim()) : 0;
    }

    private ClaudeCliBridge bridge() { return bridge(null, 20000); }
    private ClaudeCliBridge bridge(String effort, long deadlineMs) { return bridge("claude-opus-5-5", effort, deadlineMs); }
    private ClaudeCliBridge bridge(String model, String effort, long deadlineMs) {
        return new ClaudeCliBridge("Seat/smart", model, "PREAMBLE: you pilot the seat.", dir.resolve("claude").toString(),
                deadlineMs, effort, null, false);
    }

    private static final String USER = "== Turn 3 MAIN1 ==\nDECISION: Choose ONE action:\n  1) CAST Lava Spike\n  2) PASS";

    /** A CLI result as `claude -p --output-format json --json-schema` prints it (the shape of the owner's probe of
     *  2026-10-24: `structured_output`, `total_cost_usd`, `usage`, and `modelUsage` with the CLI's own side model). */
    private static String result(String structured) {
        return "{\"type\":\"result\",\"subtype\":\"success\",\"is_error\":false,\"duration_ms\":4100,\"num_turns\":2,"
                + "\"result\":\"Option 2 keeps the burn for later.\",\"session_id\":\"s-1\",\"total_cost_usd\":0.0123,"
                + "\"usage\":{\"input_tokens\":12,\"cache_creation_input_tokens\":3000,\"cache_read_input_tokens\":500,"
                + "\"output_tokens\":140},\"modelUsage\":{\"claude-haiku-5-5\":{\"inputTokens\":300,\"outputTokens\":20,"
                + "\"costUSD\":0.0002},\"claude-opus-5-5\":{\"inputTokens\":12,\"outputTokens\":140,\"costUSD\":0.0121}}"
                + (structured == null ? "" : ",\"structured_output\":" + structured) + "}\n";
    }

    private static String messages(String user) { return "[" + NativeTools.message("user", user) + "]"; }

    private static String tools(AnswerTool.Spec spec, boolean plan) { return new AnswerTool.Ask(spec, plan).definitions().toString(); }

    private static String converse(ClaudeCliBridge b, AnswerTool.Ask ask) {
        return AnswerTool.converse(USER, ask, (u, m, tools, allow, round) -> b.chat(m, tools, allow, ask.spec), null, (x, y) -> { });
    }

    // ------------------------------------------------------------------ the process

    /** Print mode with one JSON result, the seat's own system prompt from a file, the answer schema, and every isolation
     *  flag: no tools, no MCP, no saved session, no customizations or settings files, no slash commands. The decision is
     *  the process's stdin, it runs in an empty directory, and no key is on its command line. */
    @Test
    public void theArgvIsolatesTheCliAndCarriesTheAnswerSchema() throws IOException {
        reply(result("{\"answer\":{\"option\":2}}"));
        AnswerTool.Spec spec = AnswerTool.Spec.one(2);
        Assert.assertNotNull(bridge().chat(messages(USER), tools(spec, true), true, spec));

        List<String> a = argv(1);
        Assert.assertEquals(a.subList(0, 3), List.of("-p", "--model", "claude-opus-5-5"), a.toString());
        for (String[] pair : new String[][] {{"--output-format", "json"}, {"--tools", ""}, {"--setting-sources", ""}}) {
            int i = a.indexOf(pair[0]);
            Assert.assertTrue(i >= 0 && a.get(i + 1).equals(pair[1]), pair[0] + " " + pair[1] + " missing: " + a);
        }
        for (String flag : new String[] {"--strict-mcp-config", "--no-session-persistence", "--safe-mode", "--disable-slash-commands"})
            Assert.assertTrue(a.contains(flag), flag + " missing: " + a);
        Assert.assertFalse(a.contains("--effort"), "an unset effort is the CLI's own default: " + a);
        Assert.assertFalse(a.contains("--system-prompt"), "the system prompt goes in a file (argv size): " + a);
        String sp = a.get(a.indexOf("--system-prompt-file") + 1);
        Assert.assertEquals(new String(Files.readAllBytes(Path.of(sp)), StandardCharsets.UTF_8), "PREAMBLE: you pilot the seat.");

        // The schema is the answer function's own parameters (and description), and set_plan's, never a copy.
        JsonObject schema = JsonParser.parseString(a.get(a.indexOf("--json-schema") + 1)).getAsJsonObject();
        JsonObject answerFn = AnswerTool.fixedDefinition().getAsJsonObject("function");
        JsonObject answer = schema.getAsJsonObject("properties").getAsJsonObject("answer");
        Assert.assertEquals(answer.getAsJsonObject("properties"), answerFn.getAsJsonObject("parameters").getAsJsonObject("properties"));
        Assert.assertEquals(answer.get("description").getAsString(), answerFn.get("description").getAsString());
        Assert.assertEquals(schema.getAsJsonObject("properties").getAsJsonObject("set_plan").getAsJsonObject("properties"),
                AnswerTool.setPlanDefinition().getAsJsonObject("function").getAsJsonObject("parameters").getAsJsonObject("properties"));
        Assert.assertEquals(schema.getAsJsonArray("required").toString(), "[\"answer\"]");

        Assert.assertEquals(read("stdin.1"), USER, "the decision is the process's whole stdin");
        Assert.assertEquals(read("ls.1").trim(), "", "the CLI's working directory is not empty");
        for (String arg : a) Assert.assertFalse(arg.contains("OPENROUTER") || arg.matches("(?s).*\\bsk-[A-Za-z0-9-]{8,}.*"), arg);
    }

    /** CLAUDE_BRIDGE_EFFORT reaches the CLI as --effort (stamped as claude_effort_smart / _fast on the game line). */
    @Test
    public void theEffortIsPassedWhenSet() throws IOException {
        reply(result("{\"answer\":{\"option\":1}}"));
        AnswerTool.Spec spec = AnswerTool.Spec.one(2);
        bridge("high", 20000).chat(messages(USER), tools(spec, false), true, spec);
        List<String> a = argv(1);
        Assert.assertEquals(a.get(a.indexOf("--effort") + 1), "high", a.toString());
    }

    /** The child's environment is an allow-list: what the CLI needs to run, reach the network and log in. Anything that
     *  could change how the model decides (MAX_THINKING_TOKENS, ANTHROPIC_MODEL and its per-tier defaults), the
     *  launching session's CLAUDE* variables and the harness's OPENROUTER* ones are dropped. The variables are planted
     *  through the test hook, so the drop is proved in any JVM, also one launched from a clean shell. */
    @Test
    public void theChildEnvironmentIsAnAllowList() throws IOException {
        String[] dropped = {"MAX_THINKING_TOKENS", "ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_SMALL_FAST_MODEL",
                "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_EFFORT", "CLAUDE_CLI_SEAT",
                "OPENROUTER_API_KEY", "SOME_TOOL_SETTING"};
        String[] kept = {"PATH", "HOME", "LANG", "LC_ALL", "HTTPS_PROXY", "no_proxy", "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS",
                "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR"};
        // A provider's credentials only with its switch; an API key only when the operator asks for one (the owner's
        // subscription login is the lane's login).
        String[] creds = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                "GOOGLE_APPLICATION_CREDENTIALS", "ANTHROPIC_FOUNDRY_API_KEY"};
        Map<String, String> planted = new HashMap<>();
        for (String k : dropped) planted.put(k, "planted");
        for (String k : kept) planted.put(k, "planted");
        for (String k : creds) planted.put(k, "planted");
        Assert.assertEquals(new java.util.TreeSet<>(ClaudeCliBridge.childEnv(planted, null, false).keySet()),
                new java.util.TreeSet<>(Arrays.asList(kept)));
        Map<String, String> withKey = ClaudeCliBridge.childEnv(planted, null, true);
        Assert.assertTrue(withKey.containsKey("ANTHROPIC_API_KEY") && withKey.containsKey("ANTHROPIC_AUTH_TOKEN"), withKey.toString());
        Assert.assertFalse(withKey.containsKey("AWS_ACCESS_KEY_ID"), "AWS credentials without CLAUDE_CODE_USE_BEDROCK");
        planted.put("CLAUDE_CODE_USE_BEDROCK", "0");
        Assert.assertFalse(ClaudeCliBridge.childEnv(planted, null, false).containsKey("AWS_ACCESS_KEY_ID"),
                "a provider switch set to 0 is off");
        planted.put("CLAUDE_CODE_USE_BEDROCK", "true");
        for (String k : new String[] {"AWS_DEFAULT_REGION", "AWS_REGION", "AWS_PROFILE", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"})
            planted.put(k, "planted");
        Map<String, String> bedrock = ClaudeCliBridge.childEnv(planted, null, false);
        Assert.assertTrue(bedrock.containsKey("AWS_ACCESS_KEY_ID") && bedrock.containsKey("CLAUDE_CODE_USE_BEDROCK"), bedrock.toString());
        for (String k : new String[] {"AWS_DEFAULT_REGION", "AWS_REGION", "AWS_PROFILE", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"})
            Assert.assertTrue(bedrock.containsKey(k), k);
        Map<String, String> vertex = new HashMap<>();
        vertex.put("CLAUDE_CODE_USE_VERTEX", "1");
        vertex.put("VERTEX_REGION_CLAUDE_OPUS", "planted");
        vertex.put("CLOUD_ML_REGION", "planted");
        Assert.assertEquals(ClaudeCliBridge.childEnv(vertex, null, false).keySet(),
                Set.of("CLAUDE_CODE_USE_VERTEX", "VERTEX_REGION_CLAUDE_OPUS", "CLOUD_ML_REGION"));
        Assert.assertFalse(bedrock.containsKey("GOOGLE_APPLICATION_CREDENTIALS") || bedrock.containsKey("ANTHROPIC_FOUNDRY_API_KEY"));
        // The login the child has is said in the log line.
        Assert.assertEquals(ClaudeCliBridge.loginSource(bedrock), "Bedrock (CLAUDE_CODE_USE_BEDROCK)");
        Assert.assertEquals(ClaudeCliBridge.loginSource(withKey), "API key (ANTHROPIC_API_KEY)");
        Map<String, String> oauth = new HashMap<>();
        oauth.put("CLAUDE_CODE_OAUTH_TOKEN", "t");
        Assert.assertEquals(ClaudeCliBridge.loginSource(oauth), "OAuth token (CLAUDE_CODE_OAUTH_TOKEN)");
        Assert.assertEquals(ClaudeCliBridge.loginSource(new HashMap<>()), "the CLI's own stored login (its config directory)");
        planted.remove("CLAUDE_CODE_USE_BEDROCK");

        reply(result("{\"answer\":{\"option\":1}}"));
        AnswerTool.Spec spec = AnswerTool.Spec.one(2);
        Map<String, String> plant = new HashMap<>();
        for (String k : dropped) plant.put(k, "planted");
        ClaudeCliBridge.plantedEnv = plant;
        try {
            Assert.assertNotNull(bridge().chat(messages(USER), tools(spec, false), true, spec));
        } finally {
            ClaudeCliBridge.plantedEnv = null;
        }
        java.util.Set<String> seen = new java.util.TreeSet<>();
        for (String line : read("env.1").split("\n"))
            if (line.matches("[A-Za-z_][A-Za-z0-9_]*=.*")) seen.add(line.substring(0, line.indexOf('=')));
        for (String k : dropped) Assert.assertFalse(seen.contains(k), "the child inherited " + k + ": " + seen);
        Map<String, String> parent = new HashMap<>(System.getenv());
        parent.putAll(plant);
        java.util.Set<String> allowed = ClaudeCliBridge.childEnv(parent, null, false).keySet();
        for (String k : seen)
            Assert.assertTrue(allowed.contains(k) || List.of("PWD", "OLDPWD", "SHLVL", "_").contains(k),
                    "the child got " + k + ", which is not on the allow-list");
    }

    /** The thinking budget is the harness's own setting (CLAUDE_CLI_THINKING_TOKENS, from run_batch / puzzle_solve
     *  --claude-cli-thinking-tokens), set in the child as MAX_THINKING_TOKENS, never the operator's shell value. */
    @Test
    public void theThinkingBudgetIsSetExplicitlyNeverInherited() throws IOException {
        Map<String, String> shell = new HashMap<>();
        shell.put("MAX_THINKING_TOKENS", "999");
        shell.put("PATH", "/bin");
        Assert.assertFalse(ClaudeCliBridge.childEnv(shell, null, false).containsKey("MAX_THINKING_TOKENS"));
        Assert.assertEquals(ClaudeCliBridge.childEnv(shell, "16000", false).get("MAX_THINKING_TOKENS"), "16000");
        Assert.assertEquals(ClaudeCliBridge.thinkingStamp(null), "cli-default");
        Assert.assertEquals(ClaudeCliBridge.thinkingStamp("16000"), "16000");
        for (String bad : new String[] {"x", "-5", "0"}) {
            try {
                ClaudeCliBridge.thinkingTokens(bad);
                Assert.fail("accepted thinking tokens " + bad);
            } catch (IllegalArgumentException e) {
                Assert.assertTrue(e.getMessage().contains("CLAUDE_CLI_THINKING_TOKENS"), e.getMessage());
            }
        }
        Assert.assertNull(ClaudeCliBridge.thinkingTokens(null));
        Assert.assertNull(ClaudeCliBridge.thinkingTokens(" "), "an empty value (the drivers' unset) is the CLI's default");

        reply(result("{\"answer\":{\"option\":1}}"));
        AnswerTool.Spec spec = AnswerTool.Spec.one(2);
        new ClaudeCliBridge("Seat/smart", "claude-opus-5-5", "p", dir.resolve("claude").toString(), 20000, null, "12345", false)
                .chat(messages(USER), tools(spec, false), true, spec);
        Assert.assertTrue(read("env.1").contains("\nMAX_THINKING_TOKENS=12345\n") || read("env.1").startsWith("MAX_THINKING_TOKENS=12345\n"),
                read("env.1"));
    }

    // ------------------------------------------------------------------ the answer

    /** The structured output's `answer` is the `answer` call AnswerTool validates, and its `set_plan` the plan. */
    @Test
    public void aStructuredAnswerBecomesTheAnswerCallThatAnswerToolValidates() throws IOException {
        reply(result("{\"answer\":{\"option\":2,\"why\":\"keep the burn\"},\"set_plan\":{\"goal\":\"burn them out\","
                + "\"next\":\"hold Lava Spike\"}}"));
        AnswerTool.Spec spec = AnswerTool.Spec.one(3).labels(List.of("a", "b", "c"));
        AnswerTool.Ask ask = new AnswerTool.Ask(spec, true);
        List<String> plans = new ArrayList<>();
        ask.planSink = plans::add;
        String rendered = converse(bridge(), ask);
        Assert.assertTrue(ask.answered(), "AnswerTool did not accept the structured answer");
        Assert.assertEquals(ask.answer.option, Integer.valueOf(2));
        Assert.assertEquals(ask.via, "tool");
        Assert.assertTrue(rendered.endsWith("CHOICE: 2"), rendered);
        Assert.assertEquals(plans, List.of("GOAL: burn them out NEXT: hold Lava Spike"));
        Assert.assertEquals(processes(), 1);
    }

    /** An illegal answer is told why in a second process that carries the decision and the earlier answer; a second
     *  illegal one loses the decision (the caller then takes its fixed legal option). */
    @Test
    public void anIllegalAnswerIsCorrectedOnceThenTheDecisionIsLost() throws IOException {
        reply(result("{\"answer\":{\"option\":9}}"));
        AnswerTool.Spec spec = AnswerTool.Spec.one(3).labels(List.of("a", "b", "c"));
        AnswerTool.Ask ask = new AnswerTool.Ask(spec, false);
        Assert.assertNull(converse(bridge(), ask));
        Assert.assertTrue(ask.lost);
        Assert.assertEquals(ask.invalid, 2);
        Assert.assertEquals(processes(), 2);
        String second = read("stdin.2");
        Assert.assertTrue(second.startsWith(USER), second);
        Assert.assertTrue(second.contains("YOUR EARLIER REPLY TO THIS DECISION:") && second.contains("`answer`: {\"option\":9}"), second);
        Assert.assertTrue(second.contains("THE HARNESS REPLIED: ERROR:"), second);
    }

    /** A result whose structured output holds no `answer` calls nothing: it is reminded MAX_NUDGES times, then lost. */
    @Test
    public void aResultWithoutAnAnswerIsRemindedThenLost() throws IOException {
        reply(result(null));
        AnswerTool.Spec spec = AnswerTool.Spec.one(2);
        AnswerTool.Ask ask = new AnswerTool.Ask(spec, false);
        Assert.assertNull(converse(bridge(), ask));
        Assert.assertTrue(ask.lost);
        Assert.assertEquals(ask.nudges, AnswerTool.MAX_NUDGES);
        Assert.assertEquals(processes(), AnswerTool.MAX_NUDGES + 1);
        Assert.assertTrue(read("stdin.2").contains(AnswerTool.ANSWER_BY_CALL), read("stdin.2"));
    }

    /** A CLI that fails, reports an error, prints no result or runs past its deadline gives no answer (null: the seat's
     *  bridge retry, then its fixed legal option); the failure's stderr is in the log line, and a late process and its
     *  child are killed, even one that never reads its stdin. */
    @Test(timeOut = 30000)
    public void aCliErrorOrTimeoutGivesNoAnswer() throws Exception {
        AnswerTool.Spec spec = AnswerTool.Spec.one(2);
        write("exit", "1");
        write("stderr", "boom: not logged in");
        String log = LineFindingProbeTest.logOf(() -> Assert.assertNull(bridge().chat(messages(USER), tools(spec, false), true,
                spec), "a failed process answered"));
        Assert.assertTrue(log.contains("[CLAUDE-CLI-NO-ANSWER] exit 1") && log.contains("boom: not logged in"), log);
        // One process's failure is not the decision's: askModel re-asks before the fixed legal option. In the
        // thinking-vs-sampling run (arm B), every decision whose call was cut was answered on a later attempt.
        Assert.assertTrue(log.contains("re-asks while it has attempts left"), log);
        Files.delete(dir.resolve("stderr"));
        write("exit", "0");
        reply("{\"type\":\"result\",\"subtype\":\"error_max_structured_output_retries\",\"is_error\":true,\"result\":\"\","
                + "\"total_cost_usd\":0.01}");
        Assert.assertNull(bridge().chat(messages(USER), tools(spec, false), true, spec), "an error result answered");
        reply("not json");
        Assert.assertNull(bridge().chat(messages(USER), tools(spec, false), true, spec), "garbage answered");
        reply(result("{\"answer\":{\"option\":1}}"));
        write("sleep", "30");
        write("noread", "");
        // A prompt larger than a pipe's buffer, to a process that never reads it: the deadline must still hold.
        String big = USER + "\n" + "x".repeat(4 << 20);
        long t0 = System.currentTimeMillis();
        Assert.assertNull(bridge(null, 1500).chat(messages(big), tools(spec, false), true, spec), "a late process answered");
        Assert.assertTrue(System.currentTimeMillis() - t0 < 10000, "the deadline was not kept");
        long self = Long.parseLong(read("self.pid").trim()), child = Long.parseLong(read("child.pid").trim());
        for (int i = 0; i < 50 && (alive(self) || alive(child)); i++) Thread.sleep(100);
        Assert.assertFalse(alive(self), "the timed-out CLI is still running");
        Assert.assertFalse(alive(child), "the timed-out CLI's child is still running");
    }

    private static boolean alive(long pid) { return ProcessHandle.of(pid).map(ProcessHandle::isAlive).orElse(false); }

    /** A null `is_error` is not an error: the answer is read. */
    @Test
    public void aNullIsErrorIsNotAnError() throws IOException {
        reply(result("{\"answer\":{\"option\":2}}").replace("\"is_error\":false", "\"is_error\":null"));
        AnswerTool.Spec spec = AnswerTool.Spec.one(2);
        NativeTools.Turn t = bridge().chat(messages(USER), tools(spec, false), true, spec);
        Assert.assertNotNull(t);
        Assert.assertEquals(t.calls.get(0).arguments, "{\"option\":2}");
    }

    /** Through the live controller: a CLI that never answers costs the seat its decision, which goes to the fixed legal
     *  option (pass), never to Forge's AI. */
    @Test(timeOut = 60000)
    public void aSeatWhoseCliFailsTakesTheFixedLegalOptionNeverForgesAi() throws IOException {
        new LineFindingProbeTest().initializeModel();
        Game g = LineFindingProbeTest.bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        LineFindingProbeTest.card("Goblin Bombardment", me, ZoneType.Battlefield);
        LineFindingProbeTest.card("Grizzly Bears", me, ZoneType.Battlefield);
        write("exit", "1");
        ClaudeCliBridge b = bridge();
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(g, me,
                new forge.ai.LobbyPlayerAi("Seat", null), "Seat", "", b, b, "llm");
        me.dangerouslySetController(ctrl);
        List<SpellAbility> chosen = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNull(chosen, "the seat acted without an answer: " + chosen);
        Assert.assertTrue(processes() >= 1, "the seat never asked the CLI");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
        Assert.assertEquals(ctrl.textParsesOnToolAsks, 0);
    }

    /** A text ask (no `answer` offered) has nothing to put in the schema: refused without starting a process. */
    @Test
    public void aTextAskIsRefusedWithoutAProcess() throws IOException {
        Assert.assertNull(bridge().ask(USER));
        Assert.assertNull(bridge().chat(messages(USER), "[]", true, null));
        Assert.assertEquals(processes(), 0);
    }

    // ------------------------------------------------------------------ the bill

    /** The CLI's total_cost_usd (its side call to another model included) is the call's cost_usd; usage is the seat's
     *  model's tokens, cache reads and writes inside the prompt count; the served model is the requested one. */
    @Test
    public void theCostAndUsageAreRecorded() throws IOException {
        reply(result("{\"answer\":{\"option\":1}}"));
        AnswerTool.Spec spec = AnswerTool.Spec.one(2);
        ClaudeCliBridge b = bridge();
        b.chat(messages(USER), tools(spec, false), true, spec);
        b.chat(messages(USER), tools(spec, false), true, spec);
        Assert.assertEquals(b.getLastCostUsd(), 0.0123, 1e-12);
        Assert.assertEquals(b.getCostUsd(), 0.0246, 1e-12);
        Assert.assertEquals(b.getLastPromptTokens(), 3512);
        Assert.assertEquals(b.getLastCachedTokens(), 500);
        Assert.assertEquals(b.getLastCacheWriteTokens(), 3000);
        Assert.assertEquals(b.getLastCompletionTokens(), 140);
        Assert.assertEquals(b.getBilledPromptTokens(), 7024L);
        Assert.assertEquals(b.getBilledCompletionTokens(), 280L);
        Assert.assertEquals(b.getCalls(), 2);
        Assert.assertEquals(LlmSpikeGui.servedModels.get("Seat/smart"), "claude-opus-5-5");
        // An alias spec records the model that answered: the modelUsage key with the most output tokens.
        LlmSpikeGui.servedModels.remove("Seat/smart");
        bridge("opus", null, 20000).chat(messages(USER), tools(spec, false), true, spec);
        Assert.assertEquals(LlmSpikeGui.servedModels.get("Seat/smart"), "claude-opus-5-5");
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.costField(b), ",\"cost_usd\":0.0123");
        // A call that fails reports no cost or usage of its own.
        write("exit", "1");
        reply("");
        Assert.assertNull(b.chat(messages(USER), tools(spec, false), true, spec));
        Assert.assertEquals(b.getLastCostUsd(), -1.0);
        Assert.assertEquals(b.getLastPromptTokens(), -1);
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.costField(b), "");
    }

    /** A failed attempt that was billed (an error result after real turns) counts toward the row of the ask it was
     *  part of: the seat's bridge retry (restart, then the call again) sums both; the next ask starts afresh. */
    @Test
    public void aBilledFailedAttemptIsCountedWithTheRetryThatAnswered() throws IOException {
        write("reply.1", "{\"type\":\"result\",\"subtype\":\"error_max_turns\",\"is_error\":true,\"result\":\"\","
                + "\"total_cost_usd\":0.01}");
        reply(result("{\"answer\":{\"option\":1}}"));
        AnswerTool.Spec spec = AnswerTool.Spec.one(2);
        ClaudeCliBridge b = bridge();
        Assert.assertNull(b.chat(messages(USER), tools(spec, false), true, spec));
        Assert.assertEquals(b.getLastCostUsd(), 0.01, 1e-12);
        b.restart();
        Assert.assertNotNull(b.chat(messages(USER), tools(spec, false), true, spec));
        Assert.assertEquals(b.getLastCostUsd(), 0.0223, 1e-12);
        Assert.assertNotNull(b.chat(messages(USER), tools(spec, false), true, spec));
        Assert.assertEquals(b.getLastCostUsd(), 0.0123, 1e-12);
        Assert.assertEquals(b.getCostUsd(), 0.0346, 1e-12);
    }

    /** A retry that the seat makes through a text ask (which never reaches chat) ends the retry: the next ask's row does
     *  not carry the earlier ask's cost. */
    @Test
    public void aRetryThroughATextAskDoesNotCarryCostIntoTheNextAsk() throws IOException {
        write("reply.1", "{\"type\":\"result\",\"subtype\":\"error_max_turns\",\"is_error\":true,\"result\":\"\","
                + "\"total_cost_usd\":0.01}");
        reply(result("{\"answer\":{\"option\":1}}"));
        AnswerTool.Spec spec = AnswerTool.Spec.one(2);
        ClaudeCliBridge b = bridge();
        Assert.assertNull(b.chat(messages(USER), tools(spec, false), true, spec));
        b.restart();
        Assert.assertNull(b.ask(USER));
        Assert.assertNotNull(b.chat(messages(USER), tools(spec, false), true, spec));
        Assert.assertEquals(b.getLastCostUsd(), 0.0123, 1e-12);
    }

    /** The log line names the bridge version and the answer (acceptance pins the literal from this test's log). */
    @Test
    public void theLogLineStampsTheVersionAndTheAnswer() throws IOException {
        reply(result("{\"answer\":{\"option\":2}}"));
        AnswerTool.Spec spec = AnswerTool.Spec.one(2);
        String log = LineFindingProbeTest.logOf(() -> bridge().chat(messages(USER), tools(spec, false), true, spec));
        Assert.assertTrue(log.contains("[CLAUDE-CLI Seat/smart] claude-cli-bridge-v2 -> TOOL CALL answer {\"option\":2}"), log);
    }

    // ------------------------------------------------------------------ the seat's conditions

    /** The bare-name text lane (`opus`, `claude-opus-5-5`) is retired (owner, 2026-10-25): a model seat naming one is
     *  refused with what to use instead, by the factory too; a seat that never asks a model (floor, random, the puzzle
     *  arms) keeps it as a placeholder. A claude-cli: model seat needs its opt-in. Any other spec naming a "cli:" lane is
     *  a typo and is refused. */
    @Test
    public void theBareCliLaneIsRetiredAndAMisspelledLaneIsRefused() {
        for (String spec : new String[] {"opus", "claude-opus-5-5", "haiku"}) {
            Assert.assertTrue(LlmSpikeGui.LLMBridgeFactory.isBare(spec), spec);
            for (boolean optedIn : new boolean[] {false, true}) {
                try {
                    LlmSpikeGui.LLMBridgeFactory.requireCliOptIn(spec, "llm", optedIn, "Seat/smart");
                    Assert.fail("a model seat on the retired " + spec + " was built");
                } catch (IllegalArgumentException e) {
                    assertRetired(e);
                }
            }
            try {
                LlmSpikeGui.LLMBridgeFactory.create("Seat/smart", spec, "p");
                Assert.fail("the factory built the retired " + spec);
            } catch (IllegalArgumentException e) {
                assertRetired(e);
            }
            for (String mode : new String[] {"floor", "random", "pass"}) LlmSpikeGui.LLMBridgeFactory.requireCliOptIn(spec, mode, false, "S");
            Assert.assertNull(LlmSpikeGui.LLMBridgeFactory.placeholder("S/smart", spec).ask("x"), "a placeholder answers nothing");
        }
        try {
            LlmSpikeGui.LLMBridgeFactory.requireCliOptIn("claude-cli:claude-opus-5-5", "llm", false, "Seat/smart");
            Assert.fail("a claude-cli: model seat was built without " + ClaudeCliBridge.OPT_IN);
        } catch (IllegalStateException e) {
            Assert.assertTrue(e.getMessage().contains(ClaudeCliBridge.OPT_IN + "=1") && e.getMessage().contains("local development"),
                    e.getMessage());
        }
        LlmSpikeGui.LLMBridgeFactory.requireCliOptIn("claude-cli:claude-opus-5-5", "llm", true, "Seat/smart");
        for (String spec : new String[] {"openrouter:openai/gpt-6-luna", "jev:typesafe/jev-1.14", "greedy:", "script:x.txt",
                "replay:game-1-1.jsonl", "claude-cli:opus"}) {
            Assert.assertFalse(LlmSpikeGui.LLMBridgeFactory.isBare(spec), spec);
        }
        Assert.assertEquals(new java.util.TreeSet<>(Arrays.asList(LlmSpikeGui.LLMBridgeFactory.ROUTED_PREFIXES)),
                new java.util.TreeSet<>(List.of("openrouter:", DecisionsBridge.PREFIX, ClaudeCliBridge.PREFIX, ScriptBridge.PREFIX,
                        GreedyBridge.PREFIX, ReplayBridge.PREFIX)));
        for (String typo : new String[] {"Claude-CLI:opus", "cli:opus", "claude_cli:opus", "openrouter:x/claude-cli:y"}) {
            try {
                LlmSpikeGui.LLMBridgeFactory.create("Seat/smart", typo, "p");
                Assert.fail("built a bridge for " + typo);
            } catch (IllegalArgumentException e) {
                Assert.assertTrue(e.getMessage().contains(ClaudeCliBridge.PREFIX), e.getMessage());
            }
        }
    }

    private static void assertRetired(RuntimeException e) {
        Assert.assertTrue(e.getMessage().contains("retired") && e.getMessage().contains(ClaudeCliBridge.PREFIX + "<model>")
                && e.getMessage().contains(ClaudeCliBridge.OPT_IN + "=1"), e.getMessage());
    }

    /** The gates at their call site: a seat built from model strings, as a game builds it. */
    @Test
    public void aSeatBuiltFromModelStringsIsGatedWhereItIsBuilt() {
        new LineFindingProbeTest().initializeModel();
        Game g = LineFindingProbeTest.bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        forge.ai.LobbyPlayerAi lp = new forge.ai.LobbyPlayerAi("Seat", null);
        try {
            new LlmSpikeGui.PlayerControllerLLM(g, me, lp, "Seat", "", "opus", "opus", "llm");
            Assert.fail("a model seat on opus was built");
        } catch (IllegalArgumentException e) {
            assertRetired(e);
        }
        LlmSpikeGui.envOverride = new HashMap<>();
        try {
            try {
                new LlmSpikeGui.PlayerControllerLLM(g, me, lp, "Seat", "", "claude-cli:opus", "claude-cli:opus", "llm");
                Assert.fail("a claude-cli: model seat was built without " + ClaudeCliBridge.OPT_IN);
            } catch (IllegalStateException e) {
                Assert.assertTrue(e.getMessage().contains(ClaudeCliBridge.OPT_IN + "=1"), e.getMessage());
            }
            LlmSpikeGui.envOverride.put(ClaudeCliBridge.OPT_IN, "1");
            try {
                LlmSpikeGui.PlayerControllerLLM ok = new LlmSpikeGui.PlayerControllerLLM(g, me, lp, "Seat", "", "claude-cli:opus",
                        "claude-cli:opus", "llm");
                Assert.assertFalse(LlmSpikeGui.PlayerControllerLLM.MEMORY_TOOLS);
                Assert.assertTrue(ok.smartBridge() instanceof ClaudeCliBridge, String.valueOf(ok.smartBridge()));
            } catch (IllegalStateException e) {
                // Past the opt-in, a JVM run with MEMORY_TOOLS=1 (the acceptance javatests) meets the lane's next refusal.
                Assert.assertTrue(LlmSpikeGui.PlayerControllerLLM.MEMORY_TOOLS && e.getMessage().contains("MEMORY_TOOLS"), e.getMessage());
            }
        } finally {
            LlmSpikeGui.envOverride = null;
        }
        // A floor seat keeps its placeholder names and starts nothing.
        LlmSpikeGui.PlayerControllerLLM floor = new LlmSpikeGui.PlayerControllerLLM(g, me, lp, "Seat", "", "opus", "haiku", "floor");
        Assert.assertEquals(floor.smartBridge().getModel(), "opus");
        Assert.assertFalse(floor.smartBridge() instanceof ClaudeCliBridge);
        // A seat that asks no model gets placeholders on both tiers whatever they name: an openrouter: smart tier with
        // the fast tier left at its "haiku" default builds, and no bridge to any model is made.
        for (String mode : new String[] {"random", "floor"}) {
            LlmSpikeGui.PlayerControllerLLM arm = new LlmSpikeGui.PlayerControllerLLM(g, me, lp, "Seat", "",
                    "openrouter:fake/harness-model", "haiku", mode);
            Assert.assertFalse(arm.smartBridge() instanceof LlmSpikeGui.OpenRouterBridge, mode + ": " + arm.smartBridge());
            Assert.assertEquals(arm.smartBridge().getModel(), "openrouter:fake/harness-model");
            String log = LineFindingProbeTest.logOf(() -> Assert.assertNull(arm.smartBridge().ask("x")));
            Assert.assertTrue(log.contains("[PLACEHOLDER-ASKED]"), "a placeholder that is asked must say so: " + log);
        }
        // Nor does a claude-cli: name on a seat that asks no model need the opt-in: it starts no CLI.
        LlmSpikeGui.PlayerControllerLLM random = new LlmSpikeGui.PlayerControllerLLM(g, me, lp, "Seat", "", "claude-cli:opus",
                "claude-cli:opus", "random");
        Assert.assertFalse(random.smartBridge() instanceof ClaudeCliBridge);
    }

    /** buildPlayers, through its environment: a model seat with no model, on a retired bare name, or on claude-cli:
     *  without the opt-in is refused before any deck is read; floor and random seats need no model. */
    @Test
    public void buildPlayersRefusesAnUnnamedRetiredOrUnchosenModelSeat() {
        new LineFindingProbeTest().initializeModel();
        Map<String, String> env = new HashMap<>();
        env.put("CONTROLLER_A", "llm");
        env.put("CONTROLLER_B", "floor");
        LlmSpikeGui.envOverride = env;
        try {
            buildPlayersRefusals(env);
        } finally {
            LlmSpikeGui.envOverride = null;
        }
    }

    private static void buildPlayersRefusals(Map<String, String> env) {
        try {
            LlmSpikeGui.buildPlayers();
            Assert.fail("a model seat with no model was built");
        } catch (IllegalStateException e) {
            Assert.assertTrue(e.getMessage().contains("SMART_MODEL_A") && e.getMessage().contains("no default model"), e.getMessage());
        }
        env.put("SMART_MODEL", "opus");
        env.put("FAST_MODEL", "haiku");
        try {
            LlmSpikeGui.buildPlayers();
            Assert.fail("a model seat on the retired bare lane was built");
        } catch (IllegalArgumentException e) {
            assertRetired(e);
        }
        env.put("SMART_MODEL", "claude-cli:opus");
        env.put("FAST_MODEL", "claude-cli:opus");
        try {
            LlmSpikeGui.buildPlayers();
            Assert.fail("a claude-cli: model seat was built without " + ClaudeCliBridge.OPT_IN);
        } catch (IllegalStateException e) {
            Assert.assertTrue(e.getMessage().contains(ClaudeCliBridge.OPT_IN + "=1"), e.getMessage());
        }
        env.clear();
        env.put("CONTROLLER_A", "floor");
        env.put("CONTROLLER_B", "random");
        Assert.assertEquals(LlmSpikeGui.buildPlayers().size(), 2, "floor and random seats need no model");
    }

    /** A model seat with no model named is refused rather than played on the CLI's Opus; a seat that asks no model keeps
     *  its placeholder. */
    @Test
    public void aModelSeatWithNoModelNamedIsRefused() {
        Map<String, String> env = new HashMap<>();
        Assert.assertTrue(String.valueOf(LlmSpikeGui.missingModel("llm", "A", env)).contains("SMART_MODEL_A"));
        Assert.assertNull(LlmSpikeGui.missingModel("floor", "A", env));
        Assert.assertNull(LlmSpikeGui.missingModel("random", "B", env));
        env.put("SMART_MODEL", "openrouter:openai/gpt-6-luna");
        Assert.assertTrue(String.valueOf(LlmSpikeGui.missingModel("llm", "A", env)).contains("FAST_MODEL_A"));
        env.put("FAST_MODEL_B", "openrouter:openai/gpt-6-luna");
        Assert.assertNull(LlmSpikeGui.missingModel("llm", "B", env));
        env.put("FAST_MODEL_A", "");
        Assert.assertNotNull(LlmSpikeGui.missingModel("llm", "A", env), "an empty value names no model");
    }

    /** A game or puzzle with a claude-cli: model seat is a development lane, stamped so. */
    @Test
    public void aClaudeCliSeatIsADevelopmentLane() {
        Assert.assertTrue(LlmSpikeGui.devLane("llm", "claude-cli:claude-opus-5-5", "claude-cli:claude-opus-5-5"));
        Assert.assertTrue(LlmSpikeGui.devLane("llm", "openrouter:openai/gpt-6-luna", "claude-cli:claude-opus-5-5"));
        Assert.assertFalse(LlmSpikeGui.devLane("llm", "openrouter:openai/gpt-6-luna", "openrouter:openai/gpt-6-luna"));
        Assert.assertFalse(LlmSpikeGui.devLane("floor", "opus", "haiku"), "a floor seat's placeholder model is never asked");
    }

    /** The lane is chosen explicitly (owner, 2026-10-24: local development on the owner's subscription, never a benchmark
     *  score), so without CLAUDE_CLI_SEAT=1 it is refused first. Phase 1 offers no lookups and no preview, needs the
     *  answer tool, and keeps no conversation. */
    @Test
    public void theFactoryRoutesTheSpecAndRefusesLookupsLookaheadTextAnswersAndChat() {
        Assert.assertEquals(ClaudeCliBridge.VERSION, "claude-cli-bridge-v2", "move this pin WITH ClaudeCliBridge.VERSION");
        try {
            LlmSpikeGui.LLMBridgeFactory.claudeCli("Seat/smart", "claude-cli:claude-opus-5-5", "p", false, false, false, true, false);
            Assert.fail("a claude-cli: seat was built without its opt-in");
        } catch (IllegalStateException e) {
            Assert.assertTrue(e.getMessage().contains(ClaudeCliBridge.OPT_IN + "=1") && e.getMessage().contains("local development")
                    && e.getMessage().contains("benchmark"), e.getMessage());
        }
        LLMBridge b = LlmSpikeGui.LLMBridgeFactory.claudeCli("Seat/smart", "claude-cli:claude-opus-5-5", "p", true, false, false, true,
                false);
        Assert.assertTrue(b instanceof ClaudeCliBridge, String.valueOf(b));
        Assert.assertEquals(b.getModel(), "claude-opus-5-5");
        Assert.assertTrue(b.supportsNativeTools(), "the seat offers `answer` only to a bridge that takes a call");
        Assert.assertTrue(AnswerTool.usedBy("claude-cli:claude-opus-5-5"));
        Assert.assertFalse(NativeTools.usedBy("claude-cli:claude-opus-5-5"));
        String[] named = {"MEMORY_TOOLS", "LOOKAHEAD", "ANSWER_TOOL", "ONE_CHAT"};
        boolean[][] flags = {{true, false, true, false}, {false, true, true, false}, {false, false, false, false},
                {false, false, true, true}};
        for (int i = 0; i < flags.length; i++) {
            boolean[] f = flags[i];
            try {
                LlmSpikeGui.LLMBridgeFactory.claudeCli("Seat/smart", "claude-cli:claude-opus-5-5", "p", true, f[0], f[1], f[2], f[3]);
                Assert.fail("accepted " + Arrays.toString(f));
            } catch (IllegalStateException e) {
                Assert.assertTrue(e.getMessage().contains(named[i]) && e.getMessage().contains("claude-cli:"), e.getMessage());
            }
        }
    }
}
