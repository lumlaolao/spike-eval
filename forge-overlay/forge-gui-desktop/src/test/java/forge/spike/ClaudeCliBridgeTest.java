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

    /** The fake CLI: call n records argv.n (NUL-separated), env.n, cwd.n, ls.n (the cwd's entries) and stdin.n, then
     *  prints reply.n (else reply), after `sleep` seconds when that file exists, and exits with `exit` (else 0). */
    private static final String FAKE = "#!/bin/bash\n"
            + "d=\"$(cd \"$(dirname \"$0\")\" && pwd)\"\n"
            + "n=$(cat \"$d/count\" 2>/dev/null || echo 0); n=$((n+1)); echo $n > \"$d/count\"\n"
            + "printf '%s\\0' \"$@\" > \"$d/argv.$n\"\n"
            + "env > \"$d/env.$n\"\n"
            + "pwd > \"$d/cwd.$n\"\n"
            + "ls -A > \"$d/ls.$n\"\n"
            + "cat > \"$d/stdin.$n\"\n"
            + "if [ -f \"$d/sleep\" ]; then sleep \"$(cat \"$d/sleep\")\"; fi\n"
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
    private ClaudeCliBridge bridge(String effort, long deadlineMs) {
        return new ClaudeCliBridge("Seat/smart", "claude-opus-5-5", "PREAMBLE: you pilot the seat.",
                dir.resolve("claude").toString(), deadlineMs, effort);
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

    /** The child gets no CLAUDE* variable of the session that launched it (its id, entrypoint, effort, sockets) and no
     *  OPENROUTER* one; how it authenticates is kept. */
    @Test
    public void theChildEnvironmentIsScrubbed() throws IOException {
        Map<String, String> env = new HashMap<>();
        for (String k : new String[] {"CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_EFFORT",
                "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "PATH", "HOME"})
            env.put(k, "x");
        ClaudeCliBridge.scrub(env);
        Assert.assertEquals(new java.util.TreeSet<>(env.keySet()), new java.util.TreeSet<>(List.of(
                "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY", "PATH", "HOME")));

        reply(result("{\"answer\":{\"option\":1}}"));
        AnswerTool.Spec spec = AnswerTool.Spec.one(2);
        bridge().chat(messages(USER), tools(spec, false), true, spec);
        for (String line : read("env.1").split("\n")) {
            String k = line.contains("=") ? line.substring(0, line.indexOf('=')) : line;
            if (!k.matches("[A-Za-z_][A-Za-z0-9_]*")) continue;
            Assert.assertFalse(k.startsWith("OPENROUTER"), k);
            Assert.assertTrue(!k.startsWith("CLAUDE") || ClaudeCliBridge.KEEP_ENV.contains(k), "inherited " + k);
        }
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
     *  bridge retry, then its fixed legal option), and the late process is killed. */
    @Test(timeOut = 20000)
    public void aCliErrorOrTimeoutGivesNoAnswer() throws IOException {
        AnswerTool.Spec spec = AnswerTool.Spec.one(2);
        write("exit", "1");
        Assert.assertNull(bridge().chat(messages(USER), tools(spec, false), true, spec), "a failed process answered");
        write("exit", "0");
        reply("{\"type\":\"result\",\"subtype\":\"error_max_structured_output_retries\",\"is_error\":true,\"result\":\"\","
                + "\"total_cost_usd\":0.01}");
        Assert.assertNull(bridge().chat(messages(USER), tools(spec, false), true, spec), "an error result answered");
        reply("not json");
        Assert.assertNull(bridge().chat(messages(USER), tools(spec, false), true, spec), "garbage answered");
        reply(result("{\"answer\":{\"option\":1}}"));
        write("sleep", "30");
        long t0 = System.currentTimeMillis();
        Assert.assertNull(bridge(null, 700).chat(messages(USER), tools(spec, false), true, spec), "a late process answered");
        Assert.assertTrue(System.currentTimeMillis() - t0 < 10000, "the deadline was not kept");
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
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.costField(b), ",\"cost_usd\":0.0123");
        // A call that fails reports no cost or usage of its own.
        write("exit", "1");
        reply("");
        Assert.assertNull(b.chat(messages(USER), tools(spec, false), true, spec));
        Assert.assertEquals(b.getLastCostUsd(), -1.0);
        Assert.assertEquals(b.getLastPromptTokens(), -1);
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.costField(b), "");
    }

    /** The log line names the bridge version and the answer (acceptance pins the literal from this test's log). */
    @Test
    public void theLogLineStampsTheVersionAndTheAnswer() throws IOException {
        reply(result("{\"answer\":{\"option\":2}}"));
        AnswerTool.Spec spec = AnswerTool.Spec.one(2);
        String log = LineFindingProbeTest.logOf(() -> bridge().chat(messages(USER), tools(spec, false), true, spec));
        Assert.assertTrue(log.contains("[CLAUDE-CLI Seat/smart] claude-cli-bridge-v1 -> TOOL CALL answer {\"option\":2}"), log);
    }

    // ------------------------------------------------------------------ the seat's conditions

    /** The spec names the lane; a bare spec is still the text lane. Phase 1 offers no lookups and no preview, needs the
     *  answer tool, and keeps no conversation, so a seat built otherwise is refused. */
    @Test
    public void theFactoryRoutesTheSpecAndRefusesLookupsLookaheadTextAnswersAndChat() {
        Assert.assertEquals(ClaudeCliBridge.VERSION, "claude-cli-bridge-v1", "move this pin WITH ClaudeCliBridge.VERSION");
        LLMBridge b = LlmSpikeGui.LLMBridgeFactory.claudeCli("Seat/smart", "claude-cli:claude-opus-5-5", "p", false, false, true, false);
        Assert.assertTrue(b instanceof ClaudeCliBridge, String.valueOf(b));
        Assert.assertEquals(b.getModel(), "claude-opus-5-5");
        Assert.assertTrue(b.supportsNativeTools(), "the seat offers `answer` only to a bridge that takes a call");
        Assert.assertTrue(LlmSpikeGui.LLMBridgeFactory.create("Seat/smart", "claude-opus-5-5", "p") instanceof LlmSpikeGui.ClaudeBridge,
                "a bare spec left the text lane");
        Assert.assertTrue(AnswerTool.usedBy("claude-cli:claude-opus-5-5"));
        Assert.assertFalse(NativeTools.usedBy("claude-cli:claude-opus-5-5"));
        Assert.assertFalse(AnswerTool.usedBy("claude-opus-5-5"), "the text lane answers in text");
        String[] named = {"MEMORY_TOOLS", "LOOKAHEAD", "ANSWER_TOOL", "ONE_CHAT"};
        boolean[][] flags = {{true, false, true, false}, {false, true, true, false}, {false, false, false, false},
                {false, false, true, true}};
        for (int i = 0; i < flags.length; i++) {
            boolean[] f = flags[i];
            try {
                LlmSpikeGui.LLMBridgeFactory.claudeCli("Seat/smart", "claude-cli:claude-opus-5-5", "p", f[0], f[1], f[2], f[3]);
                Assert.fail("accepted " + Arrays.toString(f));
            } catch (IllegalStateException e) {
                Assert.assertTrue(e.getMessage().contains(named[i]) && e.getMessage().contains("claude-cli:"), e.getMessage());
            }
        }
    }
}
