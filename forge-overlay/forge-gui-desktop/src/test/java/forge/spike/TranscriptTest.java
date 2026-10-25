package forge.spike;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import forge.game.GameLog;
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
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.URI;
import java.net.http.HttpRequest;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.Executors;

/**
 * The run keeps every byte of every model call and the whole engine log (owner, 2026-10-25). A loopback fake records
 * the exact bytes it received and sent; the transcript rows must decode to the same bytes, for both the chat lane
 * (OpenRouterBridge) and the Decisions lane (DecisionsBridge), must be on disk before the call is answered, must never
 * hold a key, and must never change what is sent or lose a reply. Nothing here reaches openrouter.ai: every server is
 * 127.0.0.1.
 */
@Test(singleThreaded = true) // the captured bytes, the scripted reply and Transcript's state are shared
public class TranscriptTest {

    private static final String KEY = "sk-or-v1-test-not-a-real-key";
    private static final List<byte[]> received = new CopyOnWriteArrayList<>();
    private static final List<byte[]> sent = new CopyOnWriteArrayList<>();
    /** What the transcripts directory held when the server read each request (the write-ahead row's proof). */
    private static final List<String> onDiskAtRequest = new CopyOnWriteArrayList<>();
    private static volatile String reply = "{}";
    private static volatile int status = 200;
    private static volatile File dir;
    private HttpServer server;
    private String base;

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
    private static final String MESSAGES = "[{\"role\":\"user\",\"content\":\"== Turn 1 ==\\nplay\"}]";

    @BeforeClass
    public void startServer() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.setExecutor(Executors.newCachedThreadPool());
        server.createContext("/", ex -> {
            received.add(read(ex));
            onDiskAtRequest.add(allText());
            byte[] b = reply.getBytes(StandardCharsets.UTF_8);
            sent.add(b);
            ex.getResponseHeaders().add("Content-Type", "application/json");
            ex.getResponseHeaders().add("Set-Cookie", "session=s3cr3t-cookie");
            ex.getResponseHeaders().add("X-RateLimit-Remaining-Tokens", "99000");
            ex.sendResponseHeaders(status, b.length);
            try (OutputStream os = ex.getResponseBody()) { os.write(b); }
        });
        // Answers nothing for 1.5 s: past a 300 ms bridge deadline.
        server.createContext("/slow/", ex -> {
            received.add(read(ex));
            sleep(1500);
            ex.sendResponseHeaders(200, -1);
            ex.close();
        });
        // Sends its status and headers at once, then no body before the deadline.
        server.createContext("/headers-then-silence/", ex -> {
            received.add(read(ex));
            ex.getResponseHeaders().add("X-Generation-Id", "gen-cut-1");
            ex.sendResponseHeaders(200, 0);
            ex.getResponseBody().flush();
            sleep(1500);
            ex.close();
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
        received.clear(); sent.clear(); onDiskAtRequest.clear();
        status = 200;
        reply = CHAT_REPLY;
        dir = Files.createTempDirectory("transcript-test").toFile();
        Transcript.openForTest(dir, KEY);
    }

    @AfterMethod(alwaysRun = true)
    public void close() { Transcript.closeForTest(); }

    private static void sleep(long ms) {
        try { Thread.sleep(ms); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
    }

    private LlmSpikeGui.OpenRouterBridge chatBridge(String url, long timeoutMs) {
        return new LlmSpikeGui.OpenRouterBridge("Solver/smart", "openai/gpt-6-luna",
                "SYSTEM: you pilot the seat \u2014 \u00e9t\u00e9.", url, timeoutMs);
    }

    /** Every http row, in file then line order; {@code phase} null for all. */
    private static List<JsonObject> rows(String phase) throws IOException {
        List<JsonObject> out = new ArrayList<>();
        File[] fs = dir.listFiles((d, n) -> n.startsWith("http-") && n.endsWith(".jsonl"));
        if (fs == null) return out;
        Arrays.sort(fs);
        for (File f : fs)
            for (String l : Files.readAllLines(f.toPath(), StandardCharsets.UTF_8)) {
                if (l.isEmpty()) continue;
                JsonObject r = JsonParser.parseString(l).getAsJsonObject();
                if (phase == null || phase.equals(r.get("phase").getAsString())) out.add(r);
            }
        return out;
    }

    private static String allText() {
        StringBuilder b = new StringBuilder();
        File[] fs = dir == null ? null : dir.listFiles();
        if (fs != null) for (File f : fs) {
            try { b.append(new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8)); }
            catch (IOException e) { b.append("<unreadable ").append(f).append('>'); }
        }
        return b.toString();
    }

    private static byte[] utf8(JsonObject r, String field) {
        return r.get(field).getAsString().getBytes(StandardCharsets.UTF_8);
    }

    /** The chat lane: one seq, a sent row whose request is the bytes the server read (system preamble, every
     *  message, the tools, tool_choice, reasoning, max_tokens, the provider object) and a response row whose body is
     *  the bytes the server wrote (id, provider, reasoning_details, tool call, usage with cost). */
    @Test
    public void anOpenRouterCallIsRecordedByteForByte() throws IOException {
        chatBridge(base, 5000).chat(MESSAGES, TOOLS, true);
        List<JsonObject> s = rows("sent"), r = rows("response");
        Assert.assertEquals(s.size(), 1, "one round-trip, one sent row");
        Assert.assertEquals(r.size(), 1, "and one response row");
        Assert.assertEquals(s.get(0).get("seq"), r.get(0).get("seq"));
        Assert.assertEquals(s.get(0).get("version").getAsString(), Transcript.VERSION);
        Assert.assertEquals(s.get(0).get("bridge").getAsString(), "openrouter");
        Assert.assertEquals(s.get(0).get("label").getAsString(), "Solver/smart");
        Assert.assertEquals(r.get(0).get("status").getAsInt(), 200);
        Assert.assertEquals(utf8(s.get(0), "request_body"), received.get(0), "the request as the server read it");
        Assert.assertEquals(utf8(r.get(0), "response_body"), sent.get(0), "the response as the server wrote it");
        Assert.assertEquals(s.get(0).get("request_sha256").getAsString(),
                LlmSpikePuzzle.sha256(s.get(0).get("request_body").getAsString()));
        Assert.assertEquals(r.get(0).get("response_sha256").getAsString(),
                LlmSpikePuzzle.sha256(r.get(0).get("response_body").getAsString()));
        String req = s.get(0).get("request_body").getAsString();
        for (String part : new String[] {"\"role\":\"system\"", "\"tools\":", "\"tool_choice\":", "\"provider\":",
                "\"max_tokens\":", "\"model\":\"openai/gpt-6-luna\""})
            Assert.assertTrue(req.contains(part), part + " in " + req);
        System.out.println("[TRANSCRIPT] " + Transcript.VERSION + " byte-equal openrouter round-trip recorded");
    }

    /** Write-ahead: the request is on disk, whole, before the server has answered it, so a JVM that dies in the
     *  middle of a call still has what it sent. */
    @Test
    public void theRequestIsOnDiskBeforeTheReplyArrives() throws IOException {
        chatBridge(base, 5000).chat(MESSAGES, TOOLS, true);
        String atRequest = onDiskAtRequest.get(0);
        Assert.assertTrue(atRequest.contains("\"phase\":\"sent\""), "no sent row when the server read the request: " + atRequest);
        Assert.assertFalse(atRequest.contains("\"phase\":\"response\""), atRequest);
        JsonObject early = JsonParser.parseString(atRequest.trim()).getAsJsonObject();
        Assert.assertEquals(utf8(early, "request_body"), received.get(0));
    }

    /** Recording changes no byte sent: the same call with the record off and on reaches the server identically. */
    @Test
    public void recordingChangesNoByteSent() {
        Transcript.closeForTest();
        Assert.assertFalse(Transcript.on());
        chatBridge(base, 5000).chat(MESSAGES, TOOLS, true);
        Transcript.openForTest(dir, KEY);
        chatBridge(base, 5000).chat(MESSAGES, TOOLS, true);
        Assert.assertEquals(received.size(), 2);
        Assert.assertEquals(received.get(1), received.get(0), "the record changed what the model was sent");
    }

    /** A truncation retry is a second round-trip with its own seq; a refusal's body is kept whole (the log line cuts
     *  it at 90 characters). */
    @Test
    public void aRetryAndAnErrorAreEachTheirOwnWholeRows() throws IOException {
        reply = "{\"choices\":[{\"message\":{\"content\":\"\"},\"finish_reason\":\"length\"}]}";
        LlmSpikeGui.OpenRouterBridge b = chatBridge(base, 5000);
        b.ask("play");
        Assert.assertEquals(rows("response").size(), 2, "the cut-off completion and its retry");
        status = 400;
        StringBuilder longErr = new StringBuilder("{\"error\":{\"message\":\"");
        for (int i = 0; i < 50; i++) longErr.append("provider said no ").append(i).append(' ');
        reply = longErr.append("\"}}").toString();
        b.ask("play");
        List<JsonObject> rs = rows("response");
        JsonObject last = rs.get(rs.size() - 1);
        Assert.assertEquals(last.get("status").getAsInt(), 400);
        Assert.assertEquals(utf8(last, "response_body"), sent.get(sent.size() - 1));
        Assert.assertEquals(last.get("seq").getAsLong(), rs.size());
    }

    /** A call past its deadline: the exact request, then a response row that says it stopped at the deadline. */
    @Test(timeOut = 10000)
    public void aTimedOutCallIsRecordedWithItsStop() throws IOException {
        chatBridge(base.replace("/api/v1", "/slow/api/v1"), 300).chat(MESSAGES, TOOLS, true);
        List<JsonObject> s = rows("sent"), r = rows("response");
        Assert.assertEquals(s.size(), 1);
        Assert.assertEquals(r.size(), 1);
        Assert.assertEquals(utf8(s.get(0), "request_body"), received.get(0));
        Assert.assertEquals(r.get(0).get("stop").getAsString(), "deadline");
        Assert.assertFalse(r.get(0).has("response_body"));
    }

    /** A call cut off after its status and headers came keeps them (the generation id is one). */
    @Test(timeOut = 10000)
    public void aCallCutOffAfterItsHeadersKeepsThem() throws IOException {
        chatBridge(base.replace("/api/v1", "/headers-then-silence/api/v1"), 500).chat(MESSAGES, TOOLS, true);
        JsonObject r = rows("response").get(0);
        Assert.assertEquals(r.get("stop").getAsString(), "deadline");
        Assert.assertEquals(r.get("status").getAsInt(), 200, r.toString());
        Assert.assertTrue(r.get("response_headers").toString().contains("gen-cut-1"), r.toString());
    }

    /** A call that cannot connect: its request, then a response row naming the error. */
    @Test
    public void aRefusedConnectionIsAnErrorRow() throws IOException {
        int port;
        try (ServerSocket s = new ServerSocket(0, 1, InetAddress.getLoopbackAddress())) { port = s.getLocalPort(); }
        chatBridge("http://127.0.0.1:" + port + "/api/v1", 2000).chat(MESSAGES, TOOLS, true);
        List<JsonObject> s = rows("sent"), r = rows("response");
        Assert.assertEquals(s.size(), 1);
        Assert.assertEquals(r.size(), 1);
        Assert.assertTrue(r.get(0).get("error").getAsString().contains("Connect"), r.get(0).toString());
    }

    /** The Decisions lane: the request (state, questions with every criterion) and Jev's answers with their
     *  probabilities, exactly. */
    @Test
    public void aDecisionsCallIsRecordedByteForByte() throws IOException {
        reply = "{\"id\":\"gen-dec-1\",\"model\":\"typesafe/jev-1.14-20261020\",\"provider\":\"TypeSafe\","
                + "\"answers\":{\"decision\":{\"type\":\"choice\",\"choice\":\"2\",\"confidence\":0.6,"
                + "\"probabilities\":{\"2\":0.8,\"1\":0.2}}},\"usage\":{\"input_tokens\":476,\"output_tokens\":70,\"cost\":0.000019992}}";
        DecisionsBridge b = new DecisionsBridge("Solver/smart", "typesafe/jev-1.14", "PREAMBLE \u2014 seat.", base, 5000,
                KEY, null);
        b.chat("[" + NativeTools.message("user", "DECISION: choose\n  1) CAST\n  2) PASS") + "]", "[]", true,
                AnswerTool.Spec.one(2).labels(List.of("CAST Lava Spike @4", "PASS (do nothing)")));
        List<JsonObject> s = rows("sent"), r = rows("response");
        Assert.assertEquals(s.size(), 1);
        Assert.assertEquals(s.get(0).get("bridge").getAsString(), "decisions");
        Assert.assertTrue(s.get(0).get("url").getAsString().endsWith("/api/alpha/decisions"), s.get(0).get("url").getAsString());
        Assert.assertEquals(utf8(s.get(0), "request_body"), received.get(0));
        Assert.assertEquals(utf8(r.get(0), "response_body"), sent.get(0));
        Assert.assertTrue(onDiskAtRequest.get(0).contains("\"phase\":\"sent\""), "the decisions lane writes ahead too");
    }

    /** No credential reaches the file: Authorization, cookies and API-key headers are dropped (rate-limit headers
     *  that merely end in "tokens" are kept), and a row that would carry the key's value is refused whole. */
    @Test
    public void noSecretIsWritten() throws IOException {
        HttpRequest withKey = HttpRequest.newBuilder(URI.create("https://openrouter.ai/api/v1/chat/completions"))
                .header("Authorization", "Bearer " + KEY).header("X-Api-Key", KEY).header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString("{}")).build();
        JsonObject h = Transcript.safeHeaders(withKey.headers());
        Assert.assertFalse(h.toString().contains(KEY), h.toString());
        Assert.assertTrue(h.has("Content-Type") || h.has("content-type"), h.toString());
        LlmSpikeGui.OpenRouterBridge b = chatBridge(base, 5000);
        b.chat(MESSAGES, TOOLS, true);
        String text = allText();
        Assert.assertFalse(text.contains("s3cr3t-cookie"), "a Set-Cookie was written");
        Assert.assertFalse(text.toLowerCase(Locale.ROOT).contains("authorization"), text);
        Assert.assertTrue(text.toLowerCase(Locale.ROOT).contains("x-ratelimit-remaining-tokens"), "a rate-limit header was dropped");
        int before = Transcript.errors();
        b.chat("[{\"role\":\"user\",\"content\":\"" + KEY + "\"}]", TOOLS, true);
        Assert.assertEquals(Transcript.errors(), before + 1, "the sent row refused; its response is not written alone");
        Assert.assertFalse(allText().contains(KEY));
        Assert.assertEquals(rows(null).size(), 2, "the refused call is absent, the earlier one intact");
    }

    /** A row whose append fails part way is not the end of the record: the failure is counted, that file is left
     *  (its cut row is a gap a reader can see), and the next row starts a fresh file, whole. */
    @Test
    public void aRowThatFailsPartWayStartsAFreshFile() throws IOException {
        int[] calls = {0};
        Transcript.sink = (f, b) -> {
            if (calls[0]++ == 0) {
                Files.write(f, Arrays.copyOf(b, b.length / 2), StandardOpenOption.CREATE, StandardOpenOption.APPEND);
                throw new IOException("disk full (test)");
            }
            Transcript.APPEND.append(f, b);
        };
        chatBridge(base, 5000).chat(MESSAGES, TOOLS, true);
        Assert.assertEquals(Transcript.errors(), 1);
        List<String> files = Transcript.httpFiles();
        Assert.assertEquals(files.size(), 2, "a fresh file after the failure: " + files);
        List<String> second = Files.readAllLines(new File(dir, files.get(1)).toPath(), StandardCharsets.UTF_8);
        Assert.assertEquals(second.size(), 1);
        JsonObject r = JsonParser.parseString(second.get(0)).getAsJsonObject();
        Assert.assertEquals(r.get("phase").getAsString(), "response");
        Assert.assertEquals(utf8(r, "response_body"), sent.get(0));
    }

    /** Recording can never cost a reply: a sink that throws anything leaves the bridge's answer as it was. */
    @Test
    public void aRecordingFailureNeverLosesTheReply() {
        Transcript.sink = (f, b) -> { throw new IllegalStateException("broken sink (test)"); };
        NativeTools.Turn t = chatBridge(base, 5000).chat(MESSAGES, TOOLS, true);
        Assert.assertNotNull(t, "the reply was lost to a recording failure");
        Assert.assertEquals(t.calls.size(), 1);
        Assert.assertEquals(Transcript.errors(), 2, "both rows counted");
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
        List<String> lines = Files.readAllLines(new File(dir, name).toPath(), StandardCharsets.UTF_8);
        Assert.assertEquals(lines.size(), 3);
        for (int i = 0; i < 3; i++) {
            JsonObject r = JsonParser.parseString(lines.get(i)).getAsJsonObject();
            Assert.assertEquals(r.get("seq").getAsInt(), i + 1);
            Assert.assertEquals(r.get("type").getAsString(), log.get(i).type().toString());
            Assert.assertEquals(r.get("message").getAsString(), log.get(i).message());
        }
    }

    /** A capped game's thread may log after its result was read: the engine log is read again when it is written. */
    @Test
    public void aCappedGamesEngineLogIsReadAgainWhenWritten() {
        GameLog live = new GameLog();
        live.add(GameLogEntryType.TURN, "Turn 1 (Solver)");
        List<GameLogEntry> atResult = live.getAllEntries();
        live.add(GameLogEntryType.DAMAGE, "Fencing Ace deals 24 combat damage to Opponent.");
        Assert.assertEquals(LlmSpikePuzzle.engineEntries(live, atResult).size(), 2);
        Assert.assertEquals(LlmSpikePuzzle.engineEntries(null, atResult), atResult);
    }

    /** A puzzle row names its record: the version, every http file, the engine file (paths under the puzzle's
     *  directory, as eval/archive_run.py checks them), and the count of rows that could not be written. */
    @Test
    public void aPuzzleRowNamesItsRecord() {
        chatBridge(base, 5000).chat(MESSAGES, TOOLS, true);
        Map<String, Object> extra = new LinkedHashMap<>();
        LlmSpikePuzzle.putTranscript(extra, Transcript.engineLog("p-seed1-r0", List.of()));
        Assert.assertEquals(extra.get("transcript_version"), Transcript.VERSION);
        Assert.assertEquals(extra.get("transcript_http_files"), List.of(Transcript.DIR + "/" + Transcript.httpFiles().get(0)));
        Assert.assertTrue(String.valueOf(extra.get("transcript_engine_file")).startsWith(Transcript.DIR + "/engine-p-seed1-r0-"));
        Assert.assertEquals(extra.get("transcript_errors"), 0);
        Transcript.closeForTest();
        Map<String, Object> off = new LinkedHashMap<>();
        LlmSpikePuzzle.putTranscript(off, null);
        Assert.assertTrue(off.isEmpty(), "a run without the record stamps nothing: " + off);
    }

    /** Off unless asked: with no TRANSCRIPTS=1 the bridges write nothing (the test JVM has none set). */
    @Test
    public void nothingIsRecordedWhenOff() {
        Transcript.closeForTest();
        Assert.assertFalse(Transcript.on(), "this test JVM must not have TRANSCRIPTS=1 with EVAL_OUT");
        chatBridge(base, 5000).chat(MESSAGES, TOOLS, true);
        Assert.assertEquals(received.size(), 1);
        Assert.assertEquals(allText(), "");
        Assert.assertNull(Transcript.engineLog("x", List.of()));
    }

    private static byte[] read(HttpExchange ex) throws IOException {
        try (InputStream in = ex.getRequestBody()) { return in.readAllBytes(); }
    }
}
