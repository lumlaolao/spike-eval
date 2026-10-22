package forge.harnessenv.grader;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

/**
 * The hidden grader's model: an OpenAI-compatible chat-completions endpoint on loopback, one URL per replay
 * ({@code /r/<id>/chat/completions}), so a seat the grader did not write is driven through its own HTTP client.
 * A decision is a conversation: a request with no assistant message opens decision k, which is answered with the
 * k-th scripted `answer` call; a correction round in the same conversation gets the same answer again. In the prose,
 * empty and garbage modes no function is called. A replay that keeps asking is parked: past the request ceiling its
 * requests get no reply until the server closes, so a seat that loops waits instead of spinning.
 */
public final class ScriptedModel implements AutoCloseable {
    public enum Mode { CALL, PROSE, EMPTY, GARBAGE }

    static final String PROSE = "I choose option 2. CHOICE: 2\nPICKS: 2\nBLOCKS: NONE\nNUMBER: 2\nKEEP";
    static final int MAX_REQUESTS = 60;

    static final class Replay {
        final Mode mode;
        final List<String> answers;
        int decisions, requests;
        final StringBuilder text = new StringBuilder();
        Replay(Mode mode, List<String> answers) { this.mode = mode; this.answers = answers; }
    }

    private final HttpServer server;
    private final Map<String, Replay> replays = new ConcurrentHashMap<>();
    private final AtomicInteger seq = new AtomicInteger();
    private final CountDownLatch closed = new CountDownLatch(1);
    final int port;

    public ScriptedModel() throws IOException {
        server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        server.createContext("/r/", this::handle);
        server.setExecutor(Executors.newCachedThreadPool(r -> { Thread t = new Thread(r, "scripted-model"); t.setDaemon(true); return t; }));
        server.start();
        port = server.getAddress().getPort();
    }

    /** A fresh replay; returns its id. Its base URL is {@link #baseUrl}. */
    String open(Mode mode, List<String> answers) {
        String id = "x" + seq.incrementAndGet();
        replays.put(id, new Replay(mode, answers));
        return id;
    }

    String baseUrl(String id) { return "http://127.0.0.1:" + port + "/r/" + id; }

    Replay get(String id) { return replays.get(id); }

    private void handle(HttpExchange ex) throws IOException {
        String[] parts = ex.getRequestURI().getPath().split("/");
        Replay r = parts.length > 2 ? replays.get(parts[2]) : null;
        byte[] body;
        try (InputStream in = ex.getRequestBody()) { body = in.readAllBytes(); }
        if (r == null || !"POST".equals(ex.getRequestMethod())) { send(ex, 404, "{\"error\":{\"message\":\"no such replay\"}}"); return; }
        JsonObject req;
        try { req = JsonParser.parseString(new String(body, StandardCharsets.UTF_8)).getAsJsonObject(); }
        catch (RuntimeException e) { send(ex, 400, "{\"error\":{\"message\":\"bad json\"}}"); return; }
        JsonArray msgs = req.has("messages") && req.get("messages").isJsonArray() ? req.getAsJsonArray("messages") : new JsonArray();
        boolean opens = true;
        StringBuilder read = new StringBuilder();
        for (JsonElement e : msgs) {
            if (!e.isJsonObject()) continue;
            JsonObject m = e.getAsJsonObject();
            if (m.has("role") && "assistant".equals(m.get("role").getAsString())) opens = false;
            appendContent(read, m.get("content"));
        }
        boolean offersAnswer = false;
        if (req.has("tools") && req.get("tools").isJsonArray())
            for (JsonElement t : req.getAsJsonArray("tools"))
                if (t.isJsonObject() && t.getAsJsonObject().has("function")
                        && "answer".equals(t.getAsJsonObject().getAsJsonObject("function").get("name").getAsString())) offersAnswer = true;
        int decision;
        boolean park;
        synchronized (r) {
            r.requests++;
            if (opens) r.decisions++;
            r.text.append(read).append('\n');
            decision = Math.max(1, r.decisions);
            park = r.requests > MAX_REQUESTS;
        }
        if (park) {
            try { closed.await(); } catch (InterruptedException ignore) { Thread.currentThread().interrupt(); }
            return;
        }
        JsonObject msg = new JsonObject();
        msg.addProperty("role", "assistant");
        String finish = "stop";
        if (r.mode == Mode.CALL && offersAnswer && !r.answers.isEmpty()) {
            JsonObject fn = new JsonObject();
            fn.addProperty("name", "answer");
            fn.addProperty("arguments", r.answers.get(Math.min(decision, r.answers.size()) - 1));
            JsonObject call = new JsonObject();
            call.addProperty("id", "g" + r.requests);
            call.addProperty("type", "function");
            call.add("function", fn);
            JsonArray calls = new JsonArray();
            calls.add(call);
            msg.add("content", com.google.gson.JsonNull.INSTANCE);
            msg.add("tool_calls", calls);
            finish = "tool_calls";
        } else {
            msg.addProperty("content", r.mode == Mode.PROSE ? PROSE : r.mode == Mode.GARBAGE ? "zx qv wrrk" : "");
        }
        JsonObject choice = new JsonObject();
        choice.addProperty("index", 0);
        choice.addProperty("finish_reason", finish);
        choice.add("message", msg);
        JsonArray choices = new JsonArray();
        choices.add(choice);
        JsonObject usage = new JsonObject();
        usage.addProperty("prompt_tokens", Math.max(1, read.length() / 4));
        usage.addProperty("completion_tokens", 8);
        usage.addProperty("total_tokens", Math.max(1, read.length() / 4) + 8);
        JsonObject out = new JsonObject();
        out.addProperty("id", "grader-" + r.requests);
        out.addProperty("object", "chat.completion");
        out.addProperty("model", "fake/harness-model");
        out.add("choices", choices);
        out.add("usage", usage);
        send(ex, 200, out.toString());
    }

    /** The text a model reads: a string content, or the text parts of a content array (prompt-cache blocks). */
    private static void appendContent(StringBuilder b, JsonElement c) {
        if (c == null || c.isJsonNull()) return;
        if (c.isJsonPrimitive()) b.append(c.getAsString()).append('\n');
        else if (c.isJsonArray())
            for (JsonElement part : c.getAsJsonArray())
                if (part.isJsonObject() && part.getAsJsonObject().has("text")) b.append(part.getAsJsonObject().get("text").getAsString()).append('\n');
    }

    private static void send(HttpExchange ex, int status, String json) throws IOException {
        byte[] data = json.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().set("Content-Type", "application/json");
        ex.getResponseHeaders().set("Connection", "close");
        ex.sendResponseHeaders(status, data.length);
        try (OutputStream os = ex.getResponseBody()) { os.write(data); }
    }

    @Override public void close() {
        closed.countDown();
        server.stop(0);
    }
}
