package forge.spike;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.sun.net.httpserver.HttpServer;
import org.testng.Assert;
import org.testng.SkipException;
import org.testng.annotations.AfterClass;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;

/**
 * Native function calling for the seat's tools (NATIVE_TOOLS=1), and the two text-grammar repairs
 * made with it. The fixtures marked "recorded" are replies from runs/lines/ctl that the v1 grammar
 * refused.
 */
@Test(singleThreaded = true)
public class NativeToolsTest {

    private HttpServer server;
    private String base;
    private final List<JsonObject> requests = new ArrayList<>();
    private final List<String> responses = new ArrayList<>();

    @BeforeClass
    public void startServer() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.setExecutor(Executors.newCachedThreadPool());
        server.createContext("/v1/chat/completions", ex -> {
            String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            byte[] out;
            synchronized (requests) {
                requests.add(JsonParser.parseString(body).getAsJsonObject());
                out = responses.remove(0).getBytes(StandardCharsets.UTF_8);
            }
            ex.getResponseHeaders().add("Content-Type", "application/json");
            ex.sendResponseHeaders(200, out.length);
            try (OutputStream os = ex.getResponseBody()) { os.write(out); }
        });
        server.start();
        base = "http://127.0.0.1:" + server.getAddress().getPort() + "/v1";
    }

    @AfterClass(alwaysRun = true)
    public void stopServer() { if (server != null) server.stop(0); }

    private static String toolCallResponse(String id, String name, String argumentsJson) {
        JsonObject fn = new JsonObject();
        fn.addProperty("name", name);
        fn.addProperty("arguments", argumentsJson);
        JsonObject tc = new JsonObject();
        tc.addProperty("id", id);
        tc.addProperty("type", "function");
        tc.add("function", fn);
        JsonArray tcs = new JsonArray();
        tcs.add(tc);
        JsonObject msg = new JsonObject();
        msg.addProperty("role", "assistant");
        msg.add("content", com.google.gson.JsonNull.INSTANCE);
        msg.addProperty("reasoning", "kept so the provider sees its own thinking again");
        msg.add("tool_calls", tcs);
        return response(msg, "tool_calls");
    }

    private static String textResponse(String content) {
        JsonObject msg = new JsonObject();
        msg.addProperty("role", "assistant");
        msg.addProperty("content", content);
        return response(msg, "stop");
    }

    private static String response(JsonObject msg, String finish) {
        JsonObject choice = new JsonObject();
        choice.add("message", msg);
        choice.addProperty("finish_reason", finish);
        JsonArray choices = new JsonArray();
        choices.add(choice);
        JsonObject usage = new JsonObject();
        usage.addProperty("prompt_tokens", 11);
        usage.addProperty("completion_tokens", 7);
        JsonObject root = new JsonObject();
        root.addProperty("model", "fake/model");
        root.add("choices", choices);
        root.add("usage", usage);
        return root.toString();
    }

    private static NativeTools.Turn turn(String content, NativeTools.Call... calls) {
        JsonObject msg = new JsonObject();
        msg.addProperty("role", "assistant");
        if (content == null) msg.add("content", com.google.gson.JsonNull.INSTANCE); else msg.addProperty("content", content);
        JsonArray tcs = new JsonArray();
        for (NativeTools.Call c : calls) {
            JsonObject fn = new JsonObject();
            fn.addProperty("name", c.name);
            fn.addProperty("arguments", c.arguments);
            JsonObject tc = new JsonObject();
            tc.addProperty("id", c.id);
            tc.add("function", fn);
            tcs.add(tc);
        }
        if (calls.length > 0) msg.add("tool_calls", tcs);
        return new NativeTools.Turn(content, msg.toString(), List.of(calls));
    }

    /** name -> the whole function definition, as JSON text. */
    private static Map<String, String> functionsByName(JsonArray defs) {
        Map<String, String> out = new java.util.LinkedHashMap<>();
        for (JsonElement e : defs) {
            JsonObject f = e.getAsJsonObject().getAsJsonObject("function");
            out.put(f.get("name").getAsString(), f.toString());
        }
        return out;
    }

    private static NativeTools.Call call(String id, String name, String args) { return new NativeTools.Call(id, name, args); }

    // ---- definitions --------------------------------------------------------------------------

    @Test
    public void definitionsMatchTheToolUseSuite() throws IOException {
        String path = System.getProperty("harness.tooluse.json");
        if (path == null) throw new SkipException("-Dharness.tooluse.json not set");
        JsonObject suite = JsonParser.parseString(new String(Files.readAllBytes(Paths.get(path)), StandardCharsets.UTF_8)).getAsJsonObject();
        Assert.assertEquals(suite.get("version").getAsString(), NativeTools.VERSION);
        Map<String, String> java = functionsByName(NativeTools.definitions(true, true, true));
        int n = 0;
        for (JsonElement e : suite.getAsJsonArray("tools")) {
            JsonObject f = e.getAsJsonObject().getAsJsonObject("function");
            String name = f.get("name").getAsString();
            Assert.assertEquals(JsonParser.parseString(java.get(name)), f, name + " differs between NativeTools and tools.json");
            n++;
        }
        Assert.assertEquals(n, java.size(), "a tool exists on one side only");
    }

    @Test
    public void aToolIsOfferedOnlyWhenSomethingIsWiredBehindIt() {
        Assert.assertEquals(new ArrayList<>(functionsByName(NativeTools.definitions(false, false, false)).keySet()),
                List.of("memory_search", "memory_read", "memory_note"));
        Assert.assertTrue(functionsByName(NativeTools.definitions(true, true, true)).keySet()
                .containsAll(List.of("inspect", "rules_lookup", "simulate")));
    }

    @Test
    public void aCallBecomesTheCanonicalCommandLine() {
        Assert.assertEquals(NativeTools.command("inspect", "{\"target\":\"@41\"}"), "INSPECT @41");
        Assert.assertEquals(NativeTools.command("memory_read", "{\"evidence_id\":3}"), "MEMORY READ 3 0");
        Assert.assertEquals(NativeTools.command("memory_search", "{\"query\":\"Rival's\\nhand\"}"), "MEMORY SEARCH 0 Rival's hand");
        Assert.assertEquals(NativeTools.command("memory_note", "{\"evidence_id\":2,\"note\":\"holds a sweeper\"}"),
                "MEMORY NOTE 2 holds a sweeper");
        Assert.assertEquals(NativeTools.command("rules_lookup", "{\"action\":\"search\",\"query\":\"declare blockers\"}"),
                "RULES SEARCH 0 declare blockers");
        Assert.assertEquals(NativeTools.command("rules_lookup", "{\"action\":\"read\",\"query\":\"509.1a\"}"), "RULES READ 509.1a 0");
        Assert.assertEquals(NativeTools.command("rules_lookup", "{\"action\":\"section\",\"query\":\"509\",\"offset\":1500}"),
                "RULES SECTION 509 1500");
        Assert.assertEquals(NativeTools.command("rules_lookup", "{\"action\":\"toc\"}"), "RULES TOC 0");
        Assert.assertTrue(NativeTools.command("rules_lookup", "{\"query\":\"702.34\"}").startsWith("ERROR: rules_lookup needs `action`"));
        Assert.assertTrue(NativeTools.command("rules_lookup", "{\"action\":\"read\",\"query\":\"two words\"}").startsWith("ERROR:"));
        Assert.assertTrue(NativeTools.command("rules_lookup", "{\"action\":\"term\"}").startsWith("ERROR:"));
        // Malformed calls are refused before the executor sees them.
        Assert.assertTrue(NativeTools.command("inspect", "{}").startsWith("ERROR:"));
        Assert.assertTrue(NativeTools.command("inspect", "{not json").startsWith("ERROR:"));
        Assert.assertTrue(NativeTools.command("memory_read", "{\"evidence_id\":\"three\"}").startsWith("ERROR:"));
        Assert.assertTrue(NativeTools.command("memory_read", "{\"evidence_id\":1.5}").startsWith("ERROR:"));
        Assert.assertTrue(NativeTools.command("open_library", "{}").startsWith("ERROR: unknown tool"));
        Assert.assertTrue(NativeTools.command(null, "{}").startsWith("ERROR:"));
    }

    // ---- the ask loop -------------------------------------------------------------------------

    @Test
    public void aNativeAskServesTheCallAndReturnsTheAnswer() {
        ContextMemory memory = new ContextMemory();
        memory.setInspector(arg -> "@41 (current): Glasswright Sentinel 2/2\noracle: First strike, deathtouch.");
        List<String> seenMessages = new ArrayList<>();
        List<String> audit = new ArrayList<>();
        List<Boolean> allowed = new ArrayList<>();
        String reply = memory.askNative("DECISION: attack?", (user, messages, tools, allow, round) -> {
            seenMessages.add(messages);
            allowed.add(allow);
            Assert.assertTrue(tools.contains("\"inspect\"") && !tools.contains("\"simulate\"") && !tools.contains("rules_lookup"));
            return round == 0 ? turn(null, call("c1", "inspect", "{\"target\":\"@41\"}")) : turn("CHOICE: 2");
        }, null, (request, result) -> audit.add(request + " => " + result));
        Assert.assertEquals(reply, "CHOICE: 2");
        Assert.assertEquals(allowed, List.of(true, true));
        JsonArray first = JsonParser.parseString(seenMessages.get(0)).getAsJsonArray();
        Assert.assertEquals(first.size(), 1);
        String user = first.get(0).getAsJsonObject().get("content").getAsString();
        Assert.assertTrue(user.startsWith("DECISION: attack?\nCURRENT OBSERVATION: M1\nEVIDENCE ARCHIVE: 1 entries"), user);
        Assert.assertTrue(user.contains("Tool calls available this ask: 2."));
        Assert.assertFalse(user.contains("MEMORY TOOLS"), "the text grammar is not sent when tools are native");
        // Round 2 sees: the user turn, the assistant's own call echoed back, and the tool result.
        JsonArray second = JsonParser.parseString(seenMessages.get(1)).getAsJsonArray();
        Assert.assertEquals(second.size(), 3);
        Assert.assertEquals(second.get(1).getAsJsonObject().getAsJsonArray("tool_calls").size(), 1);
        JsonObject result = second.get(2).getAsJsonObject();
        Assert.assertEquals(result.get("role").getAsString(), "tool");
        Assert.assertEquals(result.get("tool_call_id").getAsString(), "c1");
        Assert.assertTrue(result.get("content").getAsString().contains("First strike, deathtouch"));
        Assert.assertEquals(audit.size(), 1);
        Assert.assertTrue(audit.get(0).startsWith("TOOL CALL inspect {\"target\":\"@41\"} => @41 (current)"), audit.get(0));
    }

    @Test
    public void theBudgetIsTwoExecutedCallsAndAMalformedCallDoesNotSpendIt() {
        ContextMemory memory = new ContextMemory();
        List<String> inspected = new ArrayList<>();
        memory.setInspector(arg -> { inspected.add(arg); return "text of " + arg; });
        List<Boolean> allowed = new ArrayList<>();
        List<String> results = new ArrayList<>();
        String reply = memory.askNative("DECISION", (user, messages, tools, allow, round) -> {
            allowed.add(allow);
            switch (round) {
                case 0: return turn(null, call("a", "inspect", "{oops"),                        // refused, free
                                          call("b", "inspect", "{\"target\":\"@1\"}"),          // two in one turn
                                          call("c", "inspect", "{\"target\":\"@2\"}"),
                                          call("d", "inspect", "{\"target\":\"@3\"}"));         // over budget
                default: return turn("CHOICE: 1");
            }
        }, null, (request, result) -> results.add(result));
        Assert.assertEquals(reply, "CHOICE: 1");
        Assert.assertEquals(inspected, List.of("@1", "@2"));
        Assert.assertEquals(allowed, List.of(true, false), "after two executed calls the next turn may not call");
        // A null or non-positive id is refused before the executor, not turned into `MEMORY READ -1 0`.
        Assert.assertTrue(NativeTools.command("memory_read", "{\"evidence_id\":null}").startsWith("ERROR:"));
        Assert.assertTrue(NativeTools.command("memory_read", "{\"evidence_id\":0}").startsWith("ERROR:"));
        Assert.assertTrue(results.get(0).startsWith("ERROR: tool arguments"));
        Assert.assertTrue(results.get(3).startsWith("ERROR: tool budget exhausted"));
    }

    @Test
    public void aSeatThatOnlyEverCallsToolsGetsNoAnswerAndTheLoopEnds() {
        ContextMemory memory = new ContextMemory();
        memory.setInspector(arg -> "x");
        int[] rounds = {0};
        String reply = memory.askNative("DECISION", (user, messages, tools, allow, round) -> {
            rounds[0]++;
            return turn(null, call("c" + round, "inspect", "{\"target\":\"@1\"}"));
        }, null, (a, b) -> { });
        Assert.assertNull(reply);
        // The text loop's bound plus the one round in which it is told its tools are spent.
        Assert.assertEquals(rounds[0], ContextMemory.TOOLS_PER_ASK + 2);
    }

    @Test
    public void simulateIsOfferedOnlyWithAHandlerAndRunsOncePerAsk() {
        ContextMemory memory = new ContextMemory();
        List<String> previews = new ArrayList<>();
        List<String> results = new ArrayList<>();
        String reply = memory.askNative("DECISION", (user, messages, tools, allow, round) -> {
            Assert.assertTrue(tools.contains("\"simulate\""));
            return round == 0
                    ? turn(null, call("s1", "simulate", "{\"option\":1}"), call("s2", "simulate", "{\"option\":2}"))
                    : turn("CHOICE: 1");
        }, args -> { previews.add(args); return "LOOKAHEAD (option 1): resolves"; }, (request, result) -> results.add(result));
        Assert.assertEquals(reply, "CHOICE: 1");
        Assert.assertEquals(previews, List.of("{\"option\":1}"));
        Assert.assertEquals(results, List.of("LOOKAHEAD (option 1): resolves", "ERROR: one preview per decision."));
        // Without a handler the tool does not exist, and a call to it is an error, not a preview.
        String r2 = new ContextMemory().askNative("DECISION", (user, messages, tools, allow, round) -> {
            Assert.assertFalse(tools.contains("\"simulate\""));
            return round == 0 ? turn(null, call("s", "simulate", "{\"option\":1}")) : turn("CHOICE: 1");
        }, null, (request, result) -> results.add(result));
        Assert.assertEquals(r2, "CHOICE: 1");
        Assert.assertEquals(results.get(2), "ERROR: unknown tool `simulate`.");
    }

    /** A preview is one of the decision's two calls but is not an archive lookup: it leaves the
     *  per-game allowance alone, as the text path's `SIMULATE:` always did. */
    @Test
    public void aPreviewDoesNotSpendThePerGameAllowance() {
        ContextMemory memory = new ContextMemory();
        memory.setInspector(arg -> "text");
        memory.askNative("DECISION", (user, messages, tools, allow, round) -> round == 0
                ? turn(null, call("s", "simulate", "{\"option\":1}"), call("i", "inspect", "{\"target\":\"@1\"}"))
                : turn("CHOICE: 1"), args -> "LOOKAHEAD (option 1): resolves", (a, b) -> { });
        Assert.assertTrue(memory.brief().contains("MEMORY tools remaining this game: " + (ContextMemory.TOOLS_PER_GAME - 1) + "."),
                memory.brief());
    }

    /** The invariant on both protocols: a tool request never reaches an answer parser. A native
     *  seat that writes the text grammar instead of calling gets no answer, not a mis-parse. */
    @Test
    public void aTextGrammarLineInANativeReplyIsNotAnAnswer() {
        List<String> results = new ArrayList<>();
        String reply = new ContextMemory().askNative("Name a card.", (user, messages, tools, allow, round) ->
                turn("I should look first.\nINSPECT Delver of Secrets"), null, (request, result) -> results.add(result));
        Assert.assertNull(reply);
        // Told once that the tools are spent, it wrote the same thing again: two refusals, then no answer.
        Assert.assertEquals(results, List.of("ERROR: a tool request must be a function call; no tool executed.",
                "ERROR: a tool request must be a function call; no tool executed."));
    }

    /** The last round may not call: a tool run then could never be read and would only be spent.
     *  Round 0 here is a refused call, so `used` alone would still allow one on round 2. */
    @Test
    public void theLastRoundMayNotCall() {
        ContextMemory memory = new ContextMemory();
        List<String> inspected = new ArrayList<>();
        memory.setInspector(arg -> { inspected.add(arg); return "text"; });
        List<Boolean> allowed = new ArrayList<>();
        String reply = memory.askNative("DECISION", (user, messages, tools, allow, round) -> {
            allowed.add(allow);
            if (round == 0) return turn(null, call("a", "inspect", "{oops"));
            if (round == 1) return turn(null, call("b", "inspect", "{\"target\":\"@1\"}"));
            return turn("CHOICE: 1");
        }, null, (a, b) -> { });
        Assert.assertEquals(reply, "CHOICE: 1");
        Assert.assertEquals(allowed, List.of(true, true, false));
        Assert.assertEquals(inspected, List.of("@1"));
    }

    /** tool_choice "none" is a request, and a provider may ignore it. A call on a forbidden round is
     *  answered with an error and never runs (it would spend the budget on a result the last round can
     *  never read); the seat is told once that its answer is due, and that answer is taken. Before,
     *  the call ran and the ask returned nothing, so the decision fell to the deterministic fallback. */
    @Test
    public void aCallMadeOnAForbiddenRoundDoesNotRunAndTheAnswerIsStillTaken() {
        ContextMemory memory = new ContextMemory();
        List<String> inspected = new ArrayList<>();
        memory.setInspector(arg -> { inspected.add(arg); return "text"; });
        List<Boolean> allowed = new ArrayList<>();
        List<String> results = new ArrayList<>();
        List<String> last = new ArrayList<>();
        String reply = memory.askNative("DECISION", (user, messages, tools, allow, round) -> {
            allowed.add(allow);
            last.add(messages);
            if (round == 0) return turn(null, call("a", "inspect", "{oops"));
            if (round == 1) return turn(null, call("b", "inspect", "{\"target\":\"@1\"}"));
            if (round == 2) return turn(null, call("c", "inspect", "{\"target\":\"@2\"}"));   // ignored "none"
            return turn("CHOICE: 1");
        }, null, (request, result) -> results.add(result));
        Assert.assertEquals(reply, "CHOICE: 1");
        Assert.assertEquals(allowed, List.of(true, true, false, false));
        Assert.assertEquals(inspected, List.of("@1"), "the forbidden call did not run");
        Assert.assertEquals(results.get(2), "ERROR: no tool calls are available for this decision; no tool executed.");
        Assert.assertTrue(memory.brief().contains("MEMORY tools remaining this game: " + (ContextMemory.TOOLS_PER_GAME - 1) + "."),
                memory.brief());
        // Every call id is answered before the reminder, as chat-completions requires.
        JsonArray sent = JsonParser.parseString(last.get(3)).getAsJsonArray();
        JsonObject tail = sent.get(sent.size() - 1).getAsJsonObject();
        Assert.assertEquals(tail.get("role").getAsString(), "user");
        Assert.assertTrue(tail.get("content").getAsString().startsWith("No tool calls are available"));
        Assert.assertEquals(sent.get(sent.size() - 2).getAsJsonObject().get("tool_call_id").getAsString(), "c");
    }

    /** Recorded (runs/tooluse/deepseek-v3, native/smart/search-old-hand): with calls forbidden the
     *  model wrote its call markup into the text. That is a tool request, not an answer. */
    @Test
    public void callMarkupInTheTextOfANativeReplyIsNotAnAnswer() {
        List<String> results = new ArrayList<>();
        String reply = new ContextMemory().askNative("Name a card.", (user, messages, tools, allow, round) ->
                turn("<｜DSML｜function_calls>\n<｜DSML｜invoke name=\"memory_read\">\n</｜DSML｜invoke>"),
                null, (request, result) -> results.add(result));
        Assert.assertNull(reply);
        Assert.assertEquals(results.size(), 2);
        Assert.assertFalse(NativeTools.looksLikeCallMarkup("I invoke Lightning Bolt. CHOICE: 2"));
    }

    /**
     * Recorded (runs/lines/blocks/native, seed 51, turn 7): after two served calls the model wrote
     * call markup as text, the ask returned nothing, and the seat PASSED its own main phase. Now it
     * is told once, in words, that the tools are spent, and its answer is taken.
     */
    @Test
    public void aSeatThatWritesCallMarkupAfterItsToolsAreSpentIsAskedOnceMoreAndAnswers() {
        ContextMemory memory = new ContextMemory();
        memory.setInspector(arg -> "text");
        List<Boolean> allowed = new ArrayList<>();
        List<String> last = new ArrayList<>();
        String reply = memory.askNative("DECISION", (user, messages, tools, allow, round) -> {
            allowed.add(allow);
            last.add(messages);
            if (round == 0) return turn(null, call("a", "inspect", "{\"target\":\"@1\"}"), call("b", "inspect", "{\"target\":\"@2\"}"));
            if (round == 1) return turn("\n\n<｜DSML｜ calls>\n<｜DSML｜ invoke name=\"memory_read\">");
            return turn("CHOICE: 3");
        }, null, (a, b) -> { });
        Assert.assertEquals(reply, "CHOICE: 3");
        Assert.assertEquals(allowed, List.of(true, false, false));
        Assert.assertTrue(last.get(2).contains("No tool calls are available for this decision any more"), last.get(2));
    }

    /** A search must not return the decision that is asking: the query usually quotes it. */
    @Test
    public void aSearchSkipsTheDecisionBeingAsked() {
        ContextMemory memory = new ContextMemory();
        memory.reveal("T2: saw Rival hand [Pale Lantern, Marsh], stripped nothing");
        List<String> results = new ArrayList<>();
        memory.askNative("You saw Rival's hand at T2. Name a card.", (user, messages, tools, allow, round) ->
                round == 0 ? turn(null, call("s", "memory_search", "{\"query\":\"Rival's hand\"}")) : turn("CHOICE: 1"),
                null, (request, result) -> results.add(result));
        Assert.assertTrue(results.get(0).startsWith("Matches=1 next=END (no literal match; entries holding every word)\nM1 (historical reveal)"),
                results.get(0));
        Assert.assertTrue(results.get(0).contains("Pale Lantern"));
    }

    /** rules_lookup through the REAL RulesTool: the first version of this mapping produced
     *  `RULES 702.34`, which the tool rejects, and its test asserted that string against itself. */
    @Test
    public void rulesLookupReachesTheRealRulesTool() {
        String path = System.getProperty("harness.rules.file");
        if (path == null) throw new SkipException("-Dharness.rules.file not set");
        RulesTool rules = RulesTool.load(path);
        Assert.assertTrue(rules.available(), rules.loadError());
        ContextMemory memory = new ContextMemory();
        memory.setRulesLookup(RulesTool.instructions(rules.effectiveDate()), rules::lookup);
        List<String> results = new ArrayList<>();
        String[] calls = {
            "{\"action\":\"search\",\"query\":\"declare blockers\"}", "{\"action\":\"read\",\"query\":\"509.1a\"}",
            "{\"action\":\"section\",\"query\":\"509\"}", "{\"action\":\"term\",\"query\":\"Flashback\"}", "{\"action\":\"toc\"}" };
        for (String args : calls) {
            memory.askNative("DECISION", (user, messages, tools, allow, round) -> {
                Assert.assertTrue(tools.contains("\"rules_lookup\""));
                return round == 0 ? turn(null, call("r", "rules_lookup", args)) : turn("CHOICE: 1");
            }, null, (request, result) -> results.add(result));
        }
        Assert.assertEquals(results.size(), calls.length);
        for (int i = 0; i < calls.length; i++)
            Assert.assertFalse(results.get(i).startsWith("ERROR"), calls[i] + " -> " + results.get(i));
        Assert.assertTrue(results.get(1).contains("509.1a"), results.get(1));
    }

    @Test
    public void nativeSeatsAreRecordedPerTier() {
        String or = "openrouter:x/y";
        Assert.assertEquals(NativeTools.seats(true, "llm", or, or, "ai", "opus", "haiku"), "A/smart,A/fast");
        Assert.assertEquals(NativeTools.seats(true, "llm", "haiku", or, "llm", or, "haiku"), "A/fast,B/smart");
        Assert.assertEquals(NativeTools.seats(true, "floor", or, or, "random", or, or), "");
        Assert.assertEquals(NativeTools.seats(false, "llm", or, or, "llm", or, or), "");
    }

    // ---- a provider that has no function calling ----------------------------------------------

    @Test
    public void aRefusalOfToolsIsRecognisedAndOtherErrorsAreNot() {
        Assert.assertTrue(LlmSpikeGui.OpenRouterBridge.refusesTools(404, "{\"error\":{\"message\":\"No endpoints found that support tool use.\"}}"));
        Assert.assertTrue(LlmSpikeGui.OpenRouterBridge.refusesTools(400, "tool_choice is not supported by this model"));
        Assert.assertTrue(LlmSpikeGui.OpenRouterBridge.refusesTools(400, "This model does not support function calling."));
        // A 400 that only mentions tools is not a refusal of them.
        Assert.assertFalse(LlmSpikeGui.OpenRouterBridge.refusesTools(400,
                "maximum context length is 64000 tokens (61000 of text input, 4000 of tool input)"));
        Assert.assertFalse(LlmSpikeGui.OpenRouterBridge.refusesTools(400, "messages[3]: tool message has no tool_call_id"));
        Assert.assertFalse(LlmSpikeGui.OpenRouterBridge.refusesTools(429, "rate limited; tools"));
        Assert.assertFalse(LlmSpikeGui.OpenRouterBridge.refusesTools(500, "tool backend down"));
        Assert.assertFalse(LlmSpikeGui.OpenRouterBridge.refusesTools(400, "max_tokens too large"));
        Assert.assertFalse(LlmSpikeGui.OpenRouterBridge.refusesTools(401, "invalid key for tool use"));
    }

    // ---- the bridge, over real HTTP on loopback -----------------------------------------------

    @Test
    public void theBridgeSendsToolsAndParsesAToolCallTurn() {
        synchronized (requests) {
            requests.clear();
            responses.clear();
            responses.add(toolCallResponse("call_9", "inspect", "{\"target\":\"@41\"}"));
            responses.add(textResponse("CHOICE: 2"));
        }
        LlmSpikeGui.OpenRouterBridge bridge = new LlmSpikeGui.OpenRouterBridge("Seat/smart", "fake/model", "PREAMBLE", base, 5000);
        Assert.assertTrue(bridge.supportsNativeTools());
        String tools = NativeTools.definitions(true, false, false).toString();
        JsonArray messages = new JsonArray();
        messages.add(NativeTools.message("user", "DECISION"));

        NativeTools.Turn first = bridge.chat(messages.toString(), tools, true);
        Assert.assertNotNull(first, "a turn that is only tool calls is not a dead bridge");
        Assert.assertEquals(first.calls.size(), 1);
        Assert.assertEquals(first.calls.get(0).name, "inspect");
        Assert.assertEquals(first.calls.get(0).id, "call_9");
        Assert.assertEquals(first.text(), "TOOL CALL inspect {\"target\":\"@41\"}");
        Assert.assertEquals(bridge.getLastFinishReason(), "tool_calls");

        messages.add(JsonParser.parseString(first.messageJson));
        messages.add(NativeTools.toolResult("call_9", "oracle: First strike"));
        NativeTools.Turn second = bridge.chat(messages.toString(), tools, false);
        Assert.assertEquals(second.content, "CHOICE: 2");
        Assert.assertTrue(second.calls.isEmpty());

        JsonObject r1 = requests.get(0), r2 = requests.get(1);
        Assert.assertEquals(r1.getAsJsonArray("tools").size(), 4);
        Assert.assertEquals(r1.get("tool_choice").getAsString(), "auto");
        Assert.assertEquals(r1.getAsJsonArray("messages").get(0).getAsJsonObject().get("role").getAsString(), "system");
        Assert.assertEquals(r1.getAsJsonArray("messages").size(), 2);
        Assert.assertEquals(r2.get("tool_choice").getAsString(), "none");
        JsonArray m2 = r2.getAsJsonArray("messages");
        Assert.assertEquals(m2.size(), 4);
        // The assistant turn goes back exactly as served, provider-private fields included.
        Assert.assertEquals(m2.get(2).getAsJsonObject().get("reasoning").getAsString(),
                "kept so the provider sees its own thinking again");
        Assert.assertEquals(m2.get(3).getAsJsonObject().get("tool_call_id").getAsString(), "call_9");
    }

    @Test
    public void aPlainAskSendsNoToolsAndTheSameBodyShapeAsBefore() {
        synchronized (requests) {
            requests.clear();
            responses.clear();
            responses.add(textResponse("CHOICE: 1"));
        }
        LlmSpikeGui.OpenRouterBridge bridge = new LlmSpikeGui.OpenRouterBridge("Seat/smart", "fake/model", "PREAMBLE", base, 5000);
        Assert.assertEquals(bridge.ask("DECISION \"quoted\"\nline two"), "CHOICE: 1");
        JsonObject r = requests.get(0);
        Assert.assertFalse(r.has("tools"));
        Assert.assertFalse(r.has("tool_choice"));
        JsonArray m = r.getAsJsonArray("messages");
        Assert.assertEquals(m.size(), 2);
        Assert.assertEquals(m.get(1).getAsJsonObject().get("content").getAsString(), "DECISION \"quoted\"\nline two");
        Assert.assertEquals(new ArrayList<>(r.keySet()).subList(0, 3), List.of("model", "messages", "max_tokens"));
    }

    @Test
    public void aBridgeWhoseProviderRefusesToolsStopsOfferingThem() throws IOException {
        HttpServer refusing = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        int[] hits = {0};
        refusing.createContext("/v1/chat/completions", ex -> {
            String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            hits[0]++;
            boolean tools = JsonParser.parseString(body).getAsJsonObject().has("tools");
            byte[] out = (tools ? "{\"error\":{\"message\":\"No endpoints found that support tool use.\"}}"
                                : textResponse("CHOICE: 1")).getBytes(StandardCharsets.UTF_8);
            ex.sendResponseHeaders(tools ? 404 : 200, out.length);
            try (OutputStream os = ex.getResponseBody()) { os.write(out); }
        });
        refusing.start();
        try {
            String url = "http://127.0.0.1:" + refusing.getAddress().getPort() + "/v1";
            LlmSpikeGui.OpenRouterBridge bridge = new LlmSpikeGui.OpenRouterBridge("Refused/smart", "fake/no-tools", "PREAMBLE", url, 5000);
            Assert.assertTrue(bridge.supportsNativeTools());
            Assert.assertNull(bridge.chat("[{\"role\":\"user\",\"content\":\"DECISION\"}]", NativeTools.definitions(true, false, false).toString(), true));
            Assert.assertFalse(bridge.supportsNativeTools(), "one refusal is enough: the same request cannot succeed");
            Assert.assertTrue(NativeTools.DOWNGRADED.contains("Refused/smart"));
            Assert.assertNull(bridge.chat("[{\"role\":\"user\",\"content\":\"DECISION\"}]", NativeTools.definitions(true, false, false).toString(), true));
            Assert.assertEquals(hits[0], 1, "a refused bridge does not send the same request again");
            Assert.assertEquals(bridge.ask("DECISION"), "CHOICE: 1", "the plain-text path still works on this bridge");
        } finally {
            refusing.stop(0);
            NativeTools.DOWNGRADED.remove("Refused/smart");
        }
    }

    // ---- the two text-grammar repairs ---------------------------------------------------------

    /** Recorded (runs/lines/ctl/lookahead): a sentence, a blank line, a valid command. v1 answered
     *  "ERROR: use exactly one MEMORY/INSPECT/RULES command line" and charged the seat a tool. */
    @Test
    public void aCommandLineThatFollowsASentenceIsExecuted() {
        ContextMemory memory = new ContextMemory();
        List<String> seen = new ArrayList<>();
        memory.setInspector(arg -> { seen.add(arg); return "@87 (current): Ajani, Nacatl Avenger"; });
        List<String> prompts = new ArrayList<>();
        String reply = memory.ask("Decision", prompt -> {
            prompts.add(prompt);
            return prompts.size() == 1 ? "Let me confirm Ajani's 0-ability damage before committing.\n\nINSPECT @87" : "CHOICE: 1";
        });
        Assert.assertEquals(reply, "CHOICE: 1");
        Assert.assertEquals(seen, List.of("@87"));
        Assert.assertTrue(prompts.get(1).contains("MEMORY REQUEST: INSPECT @87\nMEMORY RESULT (historical data):\n@87 (current)"),
                prompts.get(1));
        Assert.assertEquals(ContextMemory.toolLine("  `INSPECT @3`  "), "INSPECT @3");
        Assert.assertEquals(ContextMemory.toolLine("`INSPECT @3` to see what it does"), "INSPECT @3");
        Assert.assertEquals(ContextMemory.toolLine("```INSPECT @3"), "INSPECT @3");
        Assert.assertEquals(ContextMemory.toolLine("I will INSPECT @3 next.\nCHOICE: 2"), null);
        Assert.assertEquals(ContextMemory.toolLine("MEMORY SEARCH 0 a\nINSPECT @3"), "MEMORY SEARCH 0 a");
    }

    /** The reveal reads "saw Rival hand [...]"; the model asks for "Rival's hand". */
    @Test
    public void aSearchWithNoLiteralMatchFallsBackToEveryWord() {
        ContextMemory memory = new ContextMemory();
        memory.reveal("T2: saw Rival hand [Pale Lantern, Ostrin's Bargain, Marsh, Marsh], stripped nothing");
        memory.observe("[E9] T3 Land: Rival played Marsh");
        String literal = memory.execute("MEMORY SEARCH 0 saw Rival hand");
        Assert.assertTrue(literal.startsWith("Matches=1 next=END\nM1 (historical reveal)"), literal);
        String words = memory.execute("MEMORY SEARCH 0 Rival's hand");
        Assert.assertTrue(words.startsWith("Matches=1 next=END (no literal match; entries holding every word)\nM1 "), words);
        Assert.assertEquals(memory.execute("MEMORY SEARCH 0 Rival's library"), "Matches=0 next=END\n");
    }

    @Test
    public void aResponseWithNoMessageIsNotATurn() {
        Assert.assertNull(NativeTools.parseTurn("{\"error\":{\"message\":\"overloaded\"}}"));
        Assert.assertNull(NativeTools.parseTurn("not json"));
        NativeTools.Turn t = NativeTools.parseTurn(textResponse("hello"));
        Assert.assertEquals(t.content, "hello");
        Assert.assertEquals(t.text(), "hello");
    }
}
