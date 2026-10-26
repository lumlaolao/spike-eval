package forge.spike;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.testng.Assert;
import org.testng.annotations.AfterClass;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The request body the bridge sends, as each provider will read it (provider audit, 2026-10-24, before the first
 * run of models other than Luna). Every server here is loopback; nothing in this file can reach openrouter.ai.
 */
@Test(singleThreaded = true) // the shared captured-body list is only race-free serially
public class OpenRouterRequestTest {

    private static final List<String> bodies = new CopyOnWriteArrayList<>();
    private static final AtomicInteger calls = new AtomicInteger();
    private HttpServer server;
    private String base;

    private static final String ANSWER_TOOLS = "[{\"type\":\"function\",\"function\":{\"name\":\"" + AnswerTool.NAME
            + "\",\"description\":\"d\",\"parameters\":{\"type\":\"object\",\"properties\":{}}}}]";

    @BeforeClass
    public void startServer() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.setExecutor(Executors.newCachedThreadPool());
        // The first completion of each ask is cut off with no answer; the second answers. So every ask sends
        // the original request and then the truncation retry, and both bodies are captured.
        server.createContext("/v1/chat/completions", ex -> {
            bodies.add(read(ex));
            if (calls.incrementAndGet() % 2 == 1)
                respond(ex, "{\"choices\":[{\"message\":{\"content\":\"\"},\"finish_reason\":\"length\"}],"
                        + "\"usage\":{\"prompt_tokens\":5,\"completion_tokens\":5}}");
            else
                respond(ex, "{\"choices\":[{\"message\":{\"content\":\"CHOICE: 1\"},\"finish_reason\":\"stop\"}],"
                        + "\"usage\":{\"prompt_tokens\":5,\"completion_tokens\":5}}");
        });
        // Refusals under a ceiling: the tool wording OpenRouter gives "no endpoints" in (404), the 503 its error docs give
        // for no provider meeting the routing requirements, a 400 about the request itself, and a rate limit.
        String[][] errors = {{"ceiling", "404", "No endpoints found that support tool use."},
                {"ceiling-503", "503", "No available model provider that meets your routing requirements."},
                {"ceiling-400", "400", "messages[3]: tool message has no tool_call_id"},
                {"ceiling-429", "429", "rate limited"}};
        for (String[] e : errors)
            server.createContext("/" + e[0] + "/v1/chat/completions", ex -> {
                bodies.add(read(ex));
                respond(ex, Integer.parseInt(e[1]), "{\"error\":{\"message\":\"" + e[2] + "\"}}");
            });
        server.start();
        base = "http://127.0.0.1:" + server.getAddress().getPort() + "/v1";
    }

    @AfterClass(alwaysRun = true)
    public void stopServer() {
        if (server != null) server.stop(0);
    }

    private List<String> twoBodies(String model, boolean tools) { return twoBodies("test/smart", model, tools); }

    private List<String> twoBodies(String label, String model, boolean tools) { return twoBodies(label, model, tools, null); }

    /** maxPriceEnv: what OPENROUTER_MAX_PRICE holds; the constructor reads it, as it reads the environment. */
    private List<String> twoBodies(String label, String model, boolean tools, String maxPriceEnv) {
        bodies.clear();
        calls.set(0);
        LlmSpikeGui.OpenRouterBridge bridge = withCeiling(maxPriceEnv, () -> new LlmSpikeGui.OpenRouterBridge(label, model, null, base, 5000));
        if (tools) bridge.chat("[{\"role\":\"user\",\"content\":\"play\"}]", ANSWER_TOOLS, true);
        else bridge.ask("play");
        Assert.assertEquals(bodies.size(), 2, "the original request and its truncation retry");
        return List.copyOf(bodies);
    }

    private static int count(String s, String needle) {
        int n = 0;
        for (int i = s.indexOf(needle); i >= 0; i = s.indexOf(needle, i + 1)) n++;
        return n;
    }

    /** A JSON object with a key twice is read differently by different parsers; a last-wins one drops the latency
     *  sort that fixed the Luna stalls, since the tool path appended a second "provider" for require_parameters. */
    @Test
    public void aToolRequestCarriesOneProviderObjectWithBothPreferences() {
        String body = twoBodies("openai/gpt-6-luna", true).get(0);
        Assert.assertEquals(count(body, "\"provider\":"), 1, body);
        Matcher m = Pattern.compile("\"provider\":\\{([^}]*)\\}").matcher(body);
        Assert.assertTrue(m.find(), body);
        Assert.assertTrue(m.group(1).contains("\"require_parameters\":true"), m.group(1));
        Assert.assertTrue(m.group(1).contains("\"sort\":\"latency\""), m.group(1));
    }

    /** A request with no tools keeps the plain-text body: the latency sort only, no require_parameters. */
    @Test
    public void aPlainRequestCarriesOnlyTheLatencySort() {
        String body = twoBodies("openai/gpt-6-luna", false).get(0);
        Assert.assertEquals(count(body, "\"provider\":"), 1, body);
        Assert.assertFalse(body.contains("require_parameters"), body);
    }

    /** gemini-4-argon's reasoning cannot be turned off (reasoning.mandatory=true, OpenRouter /api/v1/models,
     *  2026-10-24), so the truncation retry's reasoning:{enabled:false} would be refused and the hardest decision
     *  lost to the fixed legal option. It asks for the lowest effort the model has instead. */
    @Test
    public void theRetryAsksAMandatoryReasonerForItsLowestEffortNotForNoReasoning() {
        String retry = twoBodies("google/gemini-4-argon", true).get(1);
        Assert.assertFalse(retry.contains("\"enabled\":false"), retry);
        Assert.assertTrue(retry.contains("\"reasoning\":{\"effort\":\"low\"}"), retry);
        Assert.assertTrue(twoBodies("meta/muse-wonder", true).get(1).contains("\"effort\":\"minimal\""));
        // grok-build-0.1 is mandatory and lists no efforts: no reasoning field at all.
        Assert.assertFalse(twoBodies("x-ai/grok-build-0.1", true).get(1).contains("\"reasoning\""));
    }

    /** A floored model still reasons at its lowest effort, so its retry keeps the whole budget, not the 4096 an
     *  answer with reasoning off needs; a model that turns reasoning off keeps 4096. */
    @Test
    public void aFlooredRetryKeepsTheWholeBudget() {
        Assert.assertTrue(twoBodies("google/gemini-4-argon", true).get(1).contains("\"max_tokens\":32000"));
        Assert.assertTrue(twoBodies("openai/gpt-6-luna", true).get(1).contains("\"max_tokens\":4096"));
    }

    /** The fast tier sends "none" on every call (two-tier runs): a mandatory reasoner gets its floor there too, and
     *  grok-4.3, which can turn reasoning off, keeps enabled:false. */
    @Test
    public void theFastTierSendsEachModelWhatItAccepts() {
        Assert.assertTrue(twoBodies("test/fast", "google/gemini-4-argon", true).get(0).contains("\"reasoning\":{\"effort\":\"low\"}"));
        Assert.assertFalse(twoBodies("test/fast", "x-ai/grok-build-0.1", true).get(0).contains("\"reasoning\""));
        Assert.assertTrue(twoBodies("test/fast", "x-ai/grok-4.3", true).get(0).contains("\"reasoning\":{\"enabled\":false}"));
    }

    /** A model that can turn reasoning off keeps the measured fast retry (65 tokens, 1.3 s). */
    @Test
    public void theRetryStillTurnsReasoningOffWhereTheModelAllowsIt() {
        Assert.assertTrue(twoBodies("openai/gpt-6-luna", true).get(1).contains("\"reasoning\":{\"enabled\":false}"));
        Assert.assertTrue(twoBodies("deepseek/deepseek-v4-pro", true).get(1).contains("\"enabled\":false"));
    }

    private static final String CEILING = "{\"openai/gpt-6.1-sol\":{\"prompt\":2,\"completion\":10},"
            + "\"moonshotai/kimi-k3\":{\"prompt\":0.239,\"completion\":10.8}}";

    /** Latency routing may land on a priority tier at up to 6x list (GPT-6.1 Sol's, eval/pricing/prices.json `max`).
     *  A model OPENROUTER_MAX_PRICE names is sent provider.max_price (USD per million tokens) inside the one provider
     *  object, on the retry too, so no endpoint dearer than that is chosen (owner, 2026-10-25). */
    @Test
    public void aNamedModelSendsItsCeilingInTheOneProviderObject() {
        for (String body : twoBodies("test/smart", "openai/gpt-6.1-sol", true, CEILING)) {
            Assert.assertEquals(count(body, "\"provider\":"), 1, body);
            Assert.assertTrue(body.contains("\"provider\":{\"sort\":\"latency\",\"require_parameters\":true,"
                    + "\"max_price\":{\"prompt\":2,\"completion\":10}}"), body);
        }
        Assert.assertTrue(twoBodies("test/smart", "moonshotai/kimi-k3", false, CEILING).get(0)
                .contains("\"provider\":{\"sort\":\"latency\",\"max_price\":{\"prompt\":0.239,\"completion\":10.8}}"));
    }

    /** No ceiling unless asked: unset, or a model the variable does not name, sends the body it always did. */
    @Test
    public void anUnnamedModelOrNoVariableSendsNoCeiling() {
        Assert.assertFalse(twoBodies("test/smart", "openai/gpt-6-luna", true, CEILING).get(0).contains("max_price"));
        Assert.assertFalse(twoBodies("test/smart", "openai/gpt-6.1-sol", true, null).get(0).contains("max_price"));
        Assert.assertFalse(twoBodies("test/smart", "openai/gpt-6.1-sol", true, " ").get(0).contains("max_price"));
    }

    /** A ceiling that cannot be read is refused, never dropped: a run must not go out believing it is capped. */
    @Test
    public void anUnreadableCeilingIsRefused() {
        for (String bad : new String[] {"2,10", "{\"openai/gpt-6.1-sol\":{\"prompt\":2}}",
                "{\"openai/gpt-6.1-sol\":{\"prompt\":\"two\",\"completion\":10}}",
                "{\"openai/gpt-6.1-sol\":{\"prompt\":0,\"completion\":10}}", "{\"openai/gpt-6.1-sol\":5}"}) {
            Assert.assertThrows(IllegalArgumentException.class,
                    () -> LlmSpikeGui.OpenRouterBridge.maxPriceField(bad, "openai/gpt-6-luna"));
        }
    }

    private static <T> T withCeiling(String env, java.util.function.Supplier<T> make) {
        String was = LlmSpikeGui.OpenRouterBridge.maxPriceEnv;
        LlmSpikeGui.OpenRouterBridge.maxPriceEnv = env;
        try { return make.get(); } finally { LlmSpikeGui.OpenRouterBridge.maxPriceEnv = was; }
    }

    /** pr-review of #327: with a ceiling, "no endpoints" can mean none at that price (DeepSeek V4.1 Flash, GLM 5.3 and
     *  the Muse models have one endpoint at list), not no function calling. Such a refusal is logged as the ceiling's
     *  and keeps native tools; the same refusal without a ceiling still moves the seat to the text grammar. */
    @Test
    public void aRefusalUnderACeilingKeepsNativeTools() {
        String ceilingBase = base.replace("/v1", "/ceiling/v1");
        String model = "openai/gpt-6.1-sol";
        LlmSpikeGui.OpenRouterBridge capped = withCeiling(CEILING,
                () -> new LlmSpikeGui.OpenRouterBridge("ceiling/smart", model, null, ceilingBase, 5000));
        bodies.clear();
        capped.chat("[{\"role\":\"user\",\"content\":\"play\"}]", ANSWER_TOOLS, true);
        capped.chat("[{\"role\":\"user\",\"content\":\"play\"}]", ANSWER_TOOLS, true);
        Assert.assertFalse(NativeTools.DOWNGRADED.contains("ceiling/smart"));
        Assert.assertEquals(capped.ceilingRefusals(), 2, "each refusal is counted for the attempt's row");
        Assert.assertEquals(LlmSpikeGui.OpenRouterBridge.ceilingRefusals(capped, null), Integer.valueOf(2));
        Assert.assertTrue(bodies.size() >= 2 && bodies.get(bodies.size() - 1).contains("\"tools\""), "asked again with tools");

        LlmSpikeGui.OpenRouterBridge plain = new LlmSpikeGui.OpenRouterBridge("plain/smart", model, null, ceilingBase, 5000);
        plain.chat("[{\"role\":\"user\",\"content\":\"play\"}]", ANSWER_TOOLS, true);
        Assert.assertTrue(NativeTools.DOWNGRADED.remove("plain/smart"), "without a ceiling the refusal is the tools'");
        Assert.assertNull(LlmSpikeGui.OpenRouterBridge.ceilingRefusals(plain, plain), "no ceiling, no key on the row");
    }

    private int refusalsAt(String context) {
        LlmSpikeGui.OpenRouterBridge b = withCeiling(CEILING, () -> new LlmSpikeGui.OpenRouterBridge(
                context + "/smart", "openai/gpt-6.1-sol", null, base.replace("/v1", "/" + context + "/v1"), 5000));
        b.chat("[{\"role\":\"user\",\"content\":\"play\"}]", ANSWER_TOOLS, true);
        return b.ceilingRefusals();
    }

    /** pr-review of #327: under a ceiling a 404 or a 5xx can be the ceiling (no endpoint left at the price, or the one
     *  endpoint at list down, with the dearer ones it would fail over to ruled out), so it is counted and the attempt
     *  replayed. A 400 is about the request itself and a 429 is a rate limit: the model's own result, counted as before. */
    @Test
    public void underACeilingA404OrA5xxIsCountedAndOtherRefusalsAreNot() {
        Assert.assertEquals(refusalsAt("ceiling"), 1);
        Assert.assertEquals(refusalsAt("ceiling-503"), 1);
        Assert.assertEquals(refusalsAt("ceiling-400"), 0);
        Assert.assertEquals(refusalsAt("ceiling-429"), 0);
    }

    /** pr-review of #327: ONE_CHAT / TURN_CHAT wrap the smart tier in a ChatSessionBridge; its refusals still count. */
    @Test
    public void refusalsUnderAWrappedTierAreCounted() {
        LlmSpikeGui.OpenRouterBridge capped = withCeiling(CEILING, () -> new LlmSpikeGui.OpenRouterBridge(
                "wrapped/smart", "openai/gpt-6.1-sol", null, base.replace("/v1", "/ceiling/v1"), 5000));
        capped.chat("[{\"role\":\"user\",\"content\":\"play\"}]", ANSWER_TOOLS, true);
        Assert.assertEquals(LlmSpikeGui.OpenRouterBridge.ceilingRefusals(new ChatSessionBridge(capped, false), null),
                Integer.valueOf(1));
    }

    private static String read(HttpExchange ex) throws IOException {
        try (InputStream in = ex.getRequestBody()) { return new String(in.readAllBytes(), StandardCharsets.UTF_8); }
    }

    private static void respond(HttpExchange ex, String json) throws IOException { respond(ex, 200, json); }

    private static void respond(HttpExchange ex, int status, String json) throws IOException {
        byte[] b = json.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().add("Content-Type", "application/json");
        ex.sendResponseHeaders(status, b.length);
        try (OutputStream os = ex.getResponseBody()) { os.write(b); }
    }
}
