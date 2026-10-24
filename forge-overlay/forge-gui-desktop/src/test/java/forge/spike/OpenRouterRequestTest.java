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
        server.start();
        base = "http://127.0.0.1:" + server.getAddress().getPort() + "/v1";
    }

    @AfterClass(alwaysRun = true)
    public void stopServer() {
        if (server != null) server.stop(0);
    }

    private List<String> twoBodies(String model, boolean tools) {
        bodies.clear();
        calls.set(0);
        LlmSpikeGui.OpenRouterBridge bridge = new LlmSpikeGui.OpenRouterBridge("test/smart", model, null, base, 5000);
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

    /** A model that can turn reasoning off keeps the measured fast retry (65 tokens, 1.3 s). */
    @Test
    public void theRetryStillTurnsReasoningOffWhereTheModelAllowsIt() {
        Assert.assertTrue(twoBodies("openai/gpt-6-luna", true).get(1).contains("\"reasoning\":{\"enabled\":false}"));
        Assert.assertTrue(twoBodies("deepseek/deepseek-v4-pro", true).get(1).contains("\"enabled\":false"));
    }

    private static String read(HttpExchange ex) throws IOException {
        try (InputStream in = ex.getRequestBody()) { return new String(in.readAllBytes(), StandardCharsets.UTF_8); }
    }

    private static void respond(HttpExchange ex, String json) throws IOException {
        byte[] b = json.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().add("Content-Type", "application/json");
        ex.sendResponseHeaders(200, b.length);
        try (OutputStream os = ex.getResponseBody()) { os.write(b); }
    }
}
