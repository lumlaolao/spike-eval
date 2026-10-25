package forge.spike;

import java.io.File;
import java.io.IOException;
import java.net.http.HttpClient;
import java.net.http.HttpHeaders;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import forge.game.GameLogEntry;

/**
 * The run's full record of what crossed the wire and what the engine logged (owner, 2026-10-25: "we must keep logs of
 * them fully, from the models' chats to the engine logs"). With TRANSCRIPTS=1 and EVAL_OUT set, every HTTP round-trip
 * a model bridge makes (OpenRouterBridge, DecisionsBridge, both through {@link #send}) is two {@code t:"http"} rows of
 * one {@code seq} in {@code <EVAL_OUT>/transcripts/http-<jvm>-<n>.jsonl}: a {@code phase:"sent"} row with the request
 * body exactly as handed to the body publisher, written BEFORE the request goes out (so a JVM that dies mid-call
 * still has it), then a {@code phase:"response"} row with the response body exactly as read, or why none came (stop,
 * error), with the status and headers when they arrived. Bodies are JSON strings: decode, encode UTF-8, and you have
 * the bytes; each carries its sha256. A puzzle game's whole Forge game log is one {@code engine-*.jsonl} beside it.
 *
 * <p>Recording only: nothing here changes a request, a response or what the seat sees, and a recording failure of any
 * kind is caught here, counted and printed ({@code [TRANSCRIPT] FAILED}, the puzzle row's {@code transcript_errors}),
 * never thrown into the bridge. Nothing is cut: each row is one append of its whole bytes; an append that fails ends
 * that file (a part-written row there is a visible gap, eval/archive_run.py) and the next row starts a fresh one. No
 * Authorization header, cookie or API-key header is written, and a row that would contain the key's value is refused.
 *
 * <p>What it cannot record: the Claude CLI lane (ClaudeBridge has no HTTP of its own), the body of a response cut off
 * by its deadline, and a response body that was not valid UTF-8 (the bridges read it as text).
 */
final class Transcript {
    /** v1 (2026-10-25): http rows (a sent row before the call, a response row after it, exact bodies) and engine rows
     *  (the whole Forge game log). Stamped on a puzzle row as `transcript_version` when on (eval/versions.py), pinned
     *  in eval/harness_acceptance.sh. */
    static final String VERSION = "transcript-v1";
    static final String DIR = "transcripts";
    /** Header names that carry a credential or a session; every other header is kept (x-ratelimit-*-tokens too). */
    static final Set<String> SECRET_HEADERS = Set.of("authorization", "proxy-authorization", "cookie", "set-cookie",
            "x-api-key", "api-key");

    /** Where a row's bytes go: one call per row. A seam so a test can fail a write part way. */
    interface Sink { void append(Path file, byte[] row) throws IOException; }
    static final Sink APPEND = (f, b) -> Files.write(f, b, StandardOpenOption.CREATE, StandardOpenOption.APPEND);
    static volatile Sink sink = APPEND;

    private static boolean resolved;
    private static File dir;
    private static final String JVM = System.currentTimeMillis() + "-" + ProcessHandle.current().pid();
    private static File httpFile;
    private static final List<String> httpFiles = new ArrayList<>();
    private static long seq;
    private static int errors;
    private static String secret = System.getenv("OPENROUTER_API_KEY");

    private Transcript() { }

    /** True when this JVM records: TRANSCRIPTS=1 with EVAL_OUT set, or a test's {@link #openForTest}. */
    static synchronized boolean on() {
        if (!resolved) {
            resolved = true;
            String out = System.getenv("EVAL_OUT");
            if ("1".equals(System.getenv("TRANSCRIPTS")) && out != null && !out.isEmpty()) dir = new File(out, DIR);
        }
        return dir != null;
    }

    /** Test seam: record under {@code d} (the transcripts directory itself), with {@code key} as the secret to refuse. */
    static synchronized void openForTest(File d, String key) {
        closeForTest();
        resolved = true;
        dir = d;
        secret = key;
    }

    static synchronized void closeForTest() {
        httpFile = null; dir = null; resolved = false; seq = 0; errors = 0; sink = APPEND;
        httpFiles.clear();
        secret = System.getenv("OPENROUTER_API_KEY");
    }

    /** Every http file this JVM has written, oldest first (a failed append starts a new one). */
    static synchronized List<String> httpFiles() { return new ArrayList<>(httpFiles); }

    /** Rows refused or not written; a puzzle row carries it so a gap is never silent. */
    static synchronized int errors() { return errors; }

    /**
     * One round-trip through {@link HttpDeadline#send}, recorded: the sent row, then the call, then the response row.
     * {@code body} is the exact string {@code req} publishes. Returns and throws exactly what HttpDeadline does.
     */
    static HttpResponse<String> send(String bridge, String label, HttpClient client, HttpRequest req, String body,
                                     long remainingMs) throws Exception {
        long n = sent(bridge, label, req, body);
        long t0 = System.currentTimeMillis();
        HttpResponse<String> resp;
        try {
            resp = HttpDeadline.send(client, req, HttpResponse.BodyHandlers.ofString(), remainingMs);
        } catch (Exception e) {
            received(n, bridge, label, null, null, e, t0);
            throw e;
        }
        received(n, bridge, label, resp, resp == null ? HttpDeadline.LAST_STOP.get() : null, null, t0);
        return resp;
    }

    /** The sent row; its seq, or 0 when off or refused for the key (its response is then not written alone). A sent
     *  row lost to an I/O error keeps its seq: the response row is still worth having. */
    private static long sent(String bridge, String label, HttpRequest req, String body) {
        try {
            if (!on()) return 0;
            JsonObject r = head("sent", next(), bridge, label);
            r.addProperty("started_ms", System.currentTimeMillis());
            r.addProperty("method", req.method());
            r.addProperty("url", req.uri().toString());
            r.add("request_headers", safeHeaders(req.headers()));
            r.addProperty("request_body", body);
            r.addProperty("request_sha256", LlmSpikePuzzle.sha256(body));
            long n = r.get("seq").getAsLong();
            return append(r) ? n : 0;
        } catch (Throwable t) {
            fail("sent row (" + label + "): " + t);
            return 0;
        }
    }

    private static void received(long n, String bridge, String label, HttpResponse<String> resp, String stop,
                                 Throwable error, long t0) {
        try {
            if (n == 0) return;
            JsonObject r = head("response", n, bridge, label);
            r.addProperty("elapsed_ms", System.currentTimeMillis() - t0);
            if (resp != null) {
                r.addProperty("status", resp.statusCode());
                r.add("response_headers", safeHeaders(resp.headers()));
                String rb = resp.body();
                r.addProperty("response_body", rb);
                r.addProperty("response_sha256", rb == null ? "" : LlmSpikePuzzle.sha256(rb));
            } else {
                // Cut off after the headers came: they are kept, the body that was cut off is not.
                HttpResponse.ResponseInfo info = HttpDeadline.LAST_INFO.get();
                if (info != null) {
                    r.addProperty("status", info.statusCode());
                    r.add("response_headers", safeHeaders(info.headers()));
                }
            }
            if (stop != null) r.addProperty("stop", stop);
            if (error != null) r.addProperty("error", String.valueOf(error));
            append(r);
        } catch (Throwable t) {
            fail("response row " + n + " (" + label + "): " + t);
        }
    }

    private static synchronized long next() { return ++seq; }

    private static JsonObject head(String phase, long n, String bridge, String label) {
        JsonObject r = new JsonObject();
        r.addProperty("t", "http");
        r.addProperty("version", VERSION);
        r.addProperty("phase", phase);
        r.addProperty("seq", n);
        r.addProperty("bridge", bridge);
        r.addProperty("label", label);
        File game = LlmSpikeGui.EvalLog.currentFile();
        if (game != null) r.addProperty("game_file", game.getName());
        return r;
    }

    /** One append of the whole row. A failed append ends that file; the next row starts a fresh one. False only when
     *  the row was refused for holding the key. */
    private static synchronized boolean append(JsonObject r) {
        String line = r.toString();
        if (secret != null && !secret.isEmpty() && line.contains(secret)) {
            fail(r.get("phase").getAsString() + " row " + r.get("seq") + " (" + r.get("label") + ") contains the API key; refused whole");
            return false;
        }
        try {
            if (httpFile == null) {
                dir.mkdirs();
                httpFile = new File(dir, "http-" + JVM + "-" + (httpFiles.size() + 1) + ".jsonl");
                httpFiles.add(httpFile.getName());
                System.out.println("[TRANSCRIPT] " + VERSION + " http " + httpFile.getName());
            }
            sink.append(httpFile.toPath(), (line + "\n").getBytes(StandardCharsets.UTF_8));
            return true;
        } catch (Throwable t) {
            fail(r.get("phase").getAsString() + " row " + r.get("seq") + " not written to " + httpFile + " (" + t
                    + "); the next row starts a new file");
            httpFile = null;
            return true;
        }
    }

    /**
     * The whole Forge game log of one game, in the engine's order, as {@code engine-<name>-<ms>.jsonl}; returns that
     * file's name, or null when off or not written (counted and printed).
     */
    static String engineLog(String name, List<GameLogEntry> entries) {
        if (!on()) return null;
        File f = new File(dir, "engine-" + name + "-" + System.currentTimeMillis() + ".jsonl");
        try {
            StringBuilder all = new StringBuilder();
            int i = 0;
            for (GameLogEntry e : entries) {
                JsonObject r = new JsonObject();
                r.addProperty("t", "engine");
                r.addProperty("version", VERSION);
                r.addProperty("seq", ++i);
                r.addProperty("type", String.valueOf(e.type()));
                r.addProperty("message", e.message());
                all.append(r).append('\n');
            }
            f.getParentFile().mkdirs();
            Files.write(f.toPath(), all.toString().getBytes(StandardCharsets.UTF_8));
            return f.getName();
        } catch (Throwable t) {
            fail("engine log " + f + " not written: " + t);
            return null;
        }
    }

    private static synchronized void fail(String why) {
        errors++;
        System.out.println("[TRANSCRIPT] FAILED " + why);
    }

    static boolean secretHeader(String name) { return SECRET_HEADERS.contains(name.toLowerCase(Locale.ROOT)); }

    /** Every header but the secret ones, each with all its values in order. */
    static JsonObject safeHeaders(HttpHeaders h) {
        JsonObject o = new JsonObject();
        for (Map.Entry<String, List<String>> e : h.map().entrySet()) {
            if (secretHeader(e.getKey())) continue;
            JsonArray a = new JsonArray();
            for (String v : e.getValue()) a.add(v);
            o.add(e.getKey(), a);
        }
        return o;
    }

}
