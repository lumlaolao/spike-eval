package forge.spike;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import forge.game.GameLogEntry;
import forge.game.GameLogEntryType;
import org.testng.Assert;
import org.testng.annotations.AfterClass;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpRequest;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.Executors;

/**
 * The run keeps every byte of every model call and the whole engine log (owner, 2026-10-25). A loopback fake records
 * the exact bytes it received and sent; the transcript row must decode to the same bytes, for both the chat lane
 * (OpenRouterBridge) and the Decisions lane (DecisionsBridge), and must never hold a key. Nothing here reaches
 * openrouter.ai: every server is 127.0.0.1.
 */
@Test(singleThreaded = true) // the captured bytes, the scripted reply and Transcript's file are shared
public class TranscriptTest {

    private static final List<byte[]> received = new CopyOnWriteArrayList<>();
    private static final List<byte[]> sent = new CopyOnWriteArrayList<>();
    private static final List<String> auths = new CopyOnWriteArrayList<>();
    private static volatile String reply = "{}";
    private static volatile int status = 200;
    private HttpServer server;
    private String base;
    private File dir;

    /** A reply as OpenRouter writes it: leading keep-alive whitespace, a generation id, the provider, a reasoning
     *  detail with an encrypted part, a tool call, and non-ASCII text. */
    private static final String CHAT_REPLY = "\n         \n{\"id\":\"gen-1761400000-abcDEF\",\"provider\":\"Azure\","
            + "\"model\":\"openai/gpt-6-luna-20261001\",\"object\":\"chat.completion\",\"choices\":[{\"index\":0,"
            + "\"finish_reason\":\"tool_calls\",\"native_finish_reason\":\"completed\",\"message\":{\"role\":\"assistant\","
            + "\"content\":null,\"reasoning\":\"Lethal needs 54 \u2014 count the equipment.\",\"reasoning_details\":["
            + "{\"type\":\"reasoning.encrypted\",\"data\":\"gAAAAB-opaque==\",\"id\":\"rs_1\",\"format\":\"openai-responses-v1\"}],"
            + "\"tool_calls\":[{\"id\":\"call_1\",\"type\":\"function\",\"function\":{\"name\":\"answer\","
            + "\"arguments\":\"{\\\"option\\\":2,\\\"why\\\":\\\"\u00e9quipe\\\"}\"}}]}}],"
            + "\"usage\":{\"prompt_tokens\":3848,\"completion_tokens\":2231,\"cost\":0.0123,"
            + "\"prompt_tokens_details\":{\"cached_tokens\":0}}}";

    private static final String TOOLS = "[{\"type\":\"function\",\"function\":{\"name\":\"" + AnswerTool.NAME
            + "\",\"description\":\"d\",\"parameters\":{\"type\":\"object\",\"properties\":{}}}}]";

    @BeforeClass
    public void startServer() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.setExecutor(Executors.newCachedThreadPool());
        server.createContext("/", ex -> {
            received.add(read(ex));
            String a = ex.getRequestHeaders().getFirst("Authorization");
            auths.add(a == null ? "" : a);
            byte[] b = reply.getBytes(StandardCharsets.UTF_8);
            sent.add(b);
            ex.getResponseHeaders().add("Content-Type", "application/json");
            ex.getResponseHeaders().add("Set-Cookie", "session=s3cr3t-cookie");
            ex.sendResponseHeaders(status, b.length);
            try (OutputStream os = ex.getResponseBody()) { os.write(b); }
        });
        server.start();
        base = "http://127.0.0.1:" + server.getAddress().getPort() + "/api/v1";
    }

    @AfterClass(alwaysRun = true)
    public void stopServer() {
        if (server != null) server.stop(0);
    }

    @BeforeMethod
    public void reset() throws IOException {
        received.clear(); sent.clear(); auths.clear();
        status = 200;
        reply = CHAT_REPLY;
        dir = Files.createTempDirectory("transcript-test").toFile();
        Transcript.openForTest(dir, "sk-or-v1-test-not-a-real-key");
    }

    @AfterMethod(alwaysRun = true)
    public void close() { Transcript.closeForTest(); }

    private List<JsonObject> rows(String prefix) throws IOException {
        List<JsonObject> out = new ArrayList<>();
        File[] fs = dir.listFiles((d, n) -> n.startsWith(prefix) && n.endsWith(".jsonl"));
        Assert.assertNotNull(fs);
        Arrays.sort(fs);
        for (File f : fs)
            for (String l : Files.readAllLines(f.toPath(), StandardCharsets.UTF_8))
                if (!l.isEmpty()) out.add(JsonParser.parseString(l).getAsJsonObject());
        return out;
    }

    private String allText() throws IOException {
        StringBuilder b = new StringBuilder();
        File[] fs = dir.listFiles();
        if (fs != null) for (File f : fs) b.append(new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8));
        return b.toString();
    }

    private static byte[] utf8(JsonObject r, String field) {
        return r.get(field).getAsString().getBytes(StandardCharsets.UTF_8);
    }

    /** The chat lane: the row's request is the bytes the server read (system preamble, every message, the tools,
     *  tool_choice, reasoning, max_tokens, the provider object) and its response the bytes the server wrote (id,
     *  provider, reasoning_details, tool call, usage with cost), with the status and the version. */
    @Test
    public void anOpenRouterCallIsRecordedByteForByte() throws IOException {
        LlmSpikeGui.OpenRouterBridge b = new LlmSpikeGui.OpenRouterBridge("Solver/smart", "openai/gpt-6-luna",
                "SYSTEM: you pilot the seat \u2014 \u00e9t\u00e9.", base, 5000);
        b.chat("[{\"role\":\"user\",\"content\":\"== Turn 1 ==\\nplay\"}]", TOOLS, true);
        List<JsonObject> rs = rows("http-");
        Assert.assertEquals(rs.size(), 1, "one round-trip, one row");
        JsonObject r = rs.get(0);
        Assert.assertEquals(r.get("t").getAsString(), "http");
        Assert.assertEquals(r.get("version").getAsString(), Transcript.VERSION);
        Assert.assertEquals(r.get("bridge").getAsString(), "openrouter");
        Assert.assertEquals(r.get("label").getAsString(), "Solver/smart");
        Assert.assertEquals(r.get("status").getAsInt(), 200);
        Assert.assertEquals(utf8(r, "request_body"), received.get(0), "the request as the server read it");
        Assert.assertEquals(utf8(r, "response_body"), sent.get(0), "the response as the server wrote it");
        Assert.assertEquals(r.get("request_sha256").getAsString(), LlmSpikePuzzle.sha256(r.get("request_body").getAsString()));
        Assert.assertEquals(r.get("response_sha256").getAsString(), LlmSpikePuzzle.sha256(r.get("response_body").getAsString()));
        String req = r.get("request_body").getAsString();
        for (String part : new String[] {"\"role\":\"system\"", "\"tools\":", "\"tool_choice\":", "\"provider\":",
                "\"max_tokens\":", "\"model\":\"openai/gpt-6-luna\""})
            Assert.assertTrue(req.contains(part), part + " in " + req);
        System.out.println("[TRANSCRIPT] " + Transcript.VERSION + " byte-equal openrouter round-trip recorded");
    }

    /** A truncation retry is a second round-trip and its own row; the discarded first completion is kept too. */
    @Test
    public void aRetryAndAnErrorAreEachTheirOwnWholeRow() throws IOException {
        reply = "{\"choices\":[{\"message\":{\"content\":\"\"},\"finish_reason\":\"length\"}]}";
        LlmSpikeGui.OpenRouterBridge b = new LlmSpikeGui.OpenRouterBridge("Solver/smart", "openai/gpt-6-luna", null, base, 5000);
        b.ask("play");
        Assert.assertEquals(rows("http-").size(), 2, "the cut-off completion and its retry");
        // A refusal's body is kept whole: the log line cuts it at 90 characters.
        status = 400;
        StringBuilder longErr = new StringBuilder("{\"error\":{\"message\":\"");
        for (int i = 0; i < 50; i++) longErr.append("provider said no ").append(i).append(' ');
        reply = longErr.append("\"}}").toString();
        b.ask("play");
        List<JsonObject> rs = rows("http-");
        JsonObject last = rs.get(rs.size() - 1);
        Assert.assertEquals(last.get("status").getAsInt(), 400);
        Assert.assertEquals(utf8(last, "response_body"), sent.get(sent.size() - 1));
        Assert.assertEquals(last.get("seq").getAsLong(), rs.size());
    }

    /** The Decisions lane: the request (state, questions with every criterion) and Jev's answers with their
     *  probabilities, exactly. */
    @Test
    public void aDecisionsCallIsRecordedByteForByte() throws IOException {
        reply = "{\"id\":\"gen-dec-1\",\"model\":\"typesafe/jev-1.14-20261020\",\"provider\":\"TypeSafe\","
                + "\"answers\":{\"decision\":{\"type\":\"choice\",\"choice\":\"2\",\"confidence\":0.6,"
                + "\"probabilities\":{\"2\":0.8,\"1\":0.2}}},\"usage\":{\"input_tokens\":476,\"output_tokens\":70,\"cost\":0.000019992}}";
        DecisionsBridge b = new DecisionsBridge("Solver/smart", "typesafe/jev-1.14", "PREAMBLE \u2014 seat.", base, 5000,
                "sk-or-v1-test-not-a-real-key", null);
        b.chat("[" + NativeTools.message("user", "DECISION: choose\n  1) CAST\n  2) PASS") + "]", "[]", true,
                AnswerTool.Spec.one(2).labels(List.of("CAST Lava Spike @4", "PASS (do nothing)")));
        List<JsonObject> rs = rows("http-");
        Assert.assertEquals(rs.size(), 1);
        JsonObject r = rs.get(0);
        Assert.assertEquals(r.get("bridge").getAsString(), "decisions");
        Assert.assertTrue(r.get("url").getAsString().endsWith("/api/alpha/decisions"), r.get("url").getAsString());
        Assert.assertEquals(utf8(r, "request_body"), received.get(0));
        Assert.assertEquals(utf8(r, "response_body"), sent.get(0));
    }

    /** No credential reaches the file: the request's Authorization header and the response's Set-Cookie are not
     *  written, and a row that would carry the key's value is refused whole, counted and printed, never cut. */
    @Test
    public void noSecretIsWritten() throws IOException {
        HttpRequest withKey = HttpRequest.newBuilder(URI.create("https://openrouter.ai/api/v1/chat/completions"))
                .header("Authorization", "Bearer sk-or-v1-test-not-a-real-key").header("Content-Type", "application/json")
                .header("X-Title", "forge").POST(HttpRequest.BodyPublishers.ofString("{}")).build();
        JsonObject h = Transcript.safeHeaders(withKey.headers());
        Assert.assertFalse(h.toString().toLowerCase(java.util.Locale.ROOT).contains("authorization"), h.toString());
        Assert.assertTrue(h.has("Content-Type") || h.has("content-type"), h.toString());
        LlmSpikeGui.OpenRouterBridge b = new LlmSpikeGui.OpenRouterBridge("Solver/smart", "openai/gpt-6-luna", null, base, 5000);
        b.chat("[{\"role\":\"user\",\"content\":\"play\"}]", TOOLS, true);
        String text = allText();
        Assert.assertFalse(text.contains("s3cr3t-cookie"), "a Set-Cookie was written");
        Assert.assertFalse(text.toLowerCase(java.util.Locale.ROOT).contains("authorization"), text);
        // The key in a body (it never is): the row is refused, not shortened.
        int before = Transcript.errors();
        b.chat("[{\"role\":\"user\",\"content\":\"sk-or-v1-test-not-a-real-key\"}]", TOOLS, true);
        Assert.assertEquals(Transcript.errors(), before + 1);
        Assert.assertFalse(allText().contains("sk-or-v1-test-not-a-real-key"));
        Assert.assertEquals(rows("http-").size(), 1, "the refused row is absent, the earlier one intact");
    }

    /** The engine side: every Forge game-log entry, in the engine's order, type and message whole (a multi-line
     *  COMBAT entry stays one row). */
    @Test
    public void theWholeEngineLogIsOneRowPerEntry() throws IOException {
        List<GameLogEntry> log = List.of(
                new GameLogEntry(GameLogEntryType.TURN, "Turn 1 (Solver)", null),
                new GameLogEntry(GameLogEntryType.COMBAT, "Opponent didn't block Fencing Ace (14).\nOpponent didn't block Glory Seeker (15).", null),
                new GameLogEntry(GameLogEntryType.LIFE, "Life: Opponent 54 > 30", null));
        String name = Transcript.engineLog("equip_1-seed1-r0", log);
        Assert.assertNotNull(name);
        List<JsonObject> rs = rows("engine-");
        Assert.assertEquals(rs.size(), 3);
        for (int i = 0; i < 3; i++) {
            Assert.assertEquals(rs.get(i).get("seq").getAsInt(), i + 1);
            Assert.assertEquals(rs.get(i).get("type").getAsString(), log.get(i).type().toString());
            Assert.assertEquals(rs.get(i).get("message").getAsString(), log.get(i).message());
        }
    }

    /** A puzzle row names its record: the version, the http file and the engine file (paths under the puzzle's
     *  directory, as eval/archive_run.py checks them), and the count of rows that could not be written. */
    @Test
    public void aPuzzleRowNamesItsRecord() {
        new LlmSpikeGui.OpenRouterBridge("Solver/smart", "openai/gpt-6-luna", null, base, 5000)
                .chat("[{\"role\":\"user\",\"content\":\"play\"}]", TOOLS, true);
        java.util.Map<String, Object> extra = new java.util.LinkedHashMap<>();
        LlmSpikePuzzle.putTranscript(extra, Transcript.engineLog("p-seed1-r0", List.of()));
        Assert.assertEquals(extra.get("transcript_version"), Transcript.VERSION);
        Assert.assertEquals(extra.get("transcript_http_file"), Transcript.DIR + "/" + Transcript.httpFileName());
        Assert.assertTrue(String.valueOf(extra.get("transcript_engine_file")).startsWith(Transcript.DIR + "/engine-p-seed1-r0-"));
        Assert.assertEquals(extra.get("transcript_errors"), 0);
        Transcript.closeForTest();
        java.util.Map<String, Object> off = new java.util.LinkedHashMap<>();
        LlmSpikePuzzle.putTranscript(off, null);
        Assert.assertTrue(off.isEmpty(), "a run without the record stamps nothing: " + off);
    }

    /** Off unless asked:with no TRANSCRIPTS=1 the bridges write nothing (the test JVM has none set). */
    @Test
    public void nothingIsRecordedWhenOff() throws IOException {
        Transcript.closeForTest();
        Assert.assertFalse(Transcript.on(), "this test JVM must not have TRANSCRIPTS=1 with EVAL_OUT");
        new LlmSpikeGui.OpenRouterBridge("Solver/smart", "openai/gpt-6-luna", null, base, 5000)
                .chat("[{\"role\":\"user\",\"content\":\"play\"}]", TOOLS, true);
        Assert.assertEquals(received.size(), 1);
        Assert.assertEquals(allText(), "");
        Assert.assertNull(Transcript.engineLog("x", List.of()));
    }

    private static byte[] read(HttpExchange ex) throws IOException {
        try (InputStream in = ex.getRequestBody()) { return in.readAllBytes(); }
    }
}
