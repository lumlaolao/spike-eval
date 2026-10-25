package forge.spike;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.PrintWriter;
import java.net.http.HttpHeaders;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Locale;
import java.util.Map;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import forge.game.GameLogEntry;

/**
 * The run's full record of what crossed the wire and what the engine logged (owner, 2026-10-25: "we must keep logs of
 * them fully, from the models' chats to the engine logs"). With TRANSCRIPTS=1 and EVAL_OUT set, every HTTP round-trip
 * a model bridge makes (OpenRouterBridge, DecisionsBridge) is one {@code t:"http"} row in
 * {@code <EVAL_OUT>/transcripts/http-<ms>-<pid>.jsonl}: the request body exactly as sent and the response body exactly
 * as received, as JSON strings (decode the string, encode it UTF-8, and you have the bytes), with their sha256, the
 * status, the non-secret headers, and why a call that got no response stopped. A puzzle game's whole Forge game log is
 * one {@code engine-*.jsonl} file beside it, one row per entry in the engine's order.
 *
 * <p>Recording only: nothing here changes a request, a response or what the seat sees. The context_call rows stay as
 * they are; these files are what they were rendered from. Nothing is cut: a row is written whole or, when it cannot be
 * (an I/O error, a secret found in it), not at all, with a loud {@code [TRANSCRIPT] FAILED} line and the count on the
 * puzzle row ({@code transcript_errors}), never a shortened row. No Authorization header, cookie or key is written;
 * a row that would contain the API key's value is refused whole.
 *
 * <p>What it cannot record: the Claude CLI lane (ClaudeBridge has no HTTP of its own), and a response body that was
 * not valid UTF-8 (the bridges read it as text, so such bytes arrive here already replaced).
 */
final class Transcript {
    /** v1 (2026-10-25): http rows (exact request/response bodies) and engine rows (the whole Forge game log). Stamped
     *  on a puzzle row as `transcript_version` when on (eval/versions.py), pinned in eval/harness_acceptance.sh. */
    static final String VERSION = "transcript-v1";
    static final String DIR = "transcripts";

    private static boolean resolved;
    private static File dir;
    private static PrintWriter http;
    private static File httpFile;
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
        if (http != null) http.close();
        http = null; httpFile = null; dir = null; resolved = false; seq = 0; errors = 0;
        secret = System.getenv("OPENROUTER_API_KEY");
    }

    /** The http file's name (relative to the transcripts directory), or null before the first call. */
    static synchronized String httpFileName() { return httpFile == null ? null : httpFile.getName(); }

    /** Rows refused or not written; a puzzle row carries it so a gap is never silent. */
    static synchronized int errors() { return errors; }

    /**
     * One round-trip. {@code body} is the exact string handed to the request's body publisher; {@code resp} is null when
     * none came back, with {@code stop} (HttpDeadline.LAST_STOP) or {@code error} saying why.
     */
    static void http(String bridge, String label, HttpRequest req, String body, HttpResponse<String> resp,
                     String stop, Throwable error, long startMs) {
        if (!on()) return;
        JsonObject r = new JsonObject();
        r.addProperty("t", "http");
        r.addProperty("version", VERSION);
        r.addProperty("bridge", bridge);
        r.addProperty("label", label);
        java.io.File game = LlmSpikeGui.EvalLog.currentFile();
        if (game != null) r.addProperty("game_file", game.getName());
        r.addProperty("started_ms", startMs);
        r.addProperty("elapsed_ms", System.currentTimeMillis() - startMs);
        r.addProperty("method", req.method());
        r.addProperty("url", req.uri().toString());
        r.add("request_headers", safeHeaders(req.headers()));
        r.addProperty("request_body", body);
        r.addProperty("request_sha256", LlmSpikePuzzle.sha256(body));
        if (resp != null) {
            r.addProperty("status", resp.statusCode());
            r.add("response_headers", safeHeaders(resp.headers()));
            String rb = resp.body();
            r.addProperty("response_body", rb);
            r.addProperty("response_sha256", rb == null ? "" : LlmSpikePuzzle.sha256(rb));
        }
        if (stop != null) r.addProperty("stop", stop);
        if (error != null) r.addProperty("error", String.valueOf(error));
        writeHttp(r);
    }

    private static synchronized void writeHttp(JsonObject r) {
        r.addProperty("seq", ++seq);
        String line = r.toString();
        if (secret != null && !secret.isEmpty() && line.contains(secret)) {
            fail("http row " + seq + " (" + r.get("label") + ") contains the API key; refused whole");
            return;
        }
        try {
            if (http == null) {
                dir.mkdirs();
                httpFile = new File(dir, "http-" + System.currentTimeMillis() + "-" + ProcessHandle.current().pid() + ".jsonl");
                http = new PrintWriter(new OutputStreamWriter(new FileOutputStream(httpFile, true), StandardCharsets.UTF_8));
                System.out.println("[TRANSCRIPT] " + VERSION + " http " + httpFile.getName());
            }
            http.println(line);
            http.flush();
            if (http.checkError()) fail("http row " + seq + " not written (I/O error) to " + httpFile);
        } catch (Exception e) {
            fail("http row " + seq + " not written: " + e);
        }
    }

    /**
     * The whole Forge game log of one game, in the engine's order, as {@code engine-<name>.jsonl}; returns that file's
     * name, or null when off or not written (counted and printed).
     */
    static String engineLog(String name, List<GameLogEntry> entries) {
        if (!on()) return null;
        File f;
        synchronized (Transcript.class) { f = new File(dir, "engine-" + name + "-" + System.currentTimeMillis() + ".jsonl"); }
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
        try {
            f.getParentFile().mkdirs();
            java.nio.file.Files.write(f.toPath(), all.toString().getBytes(StandardCharsets.UTF_8));
            return f.getName();
        } catch (Exception e) {
            fail("engine log " + f + " not written: " + e);
            return null;
        }
    }

    private static synchronized void fail(String why) {
        errors++;
        System.out.println("[TRANSCRIPT] FAILED " + why);
    }

    /** Header names that carry a credential or a session: never written. */
    static boolean secretHeader(String name) {
        String n = name.toLowerCase(Locale.ROOT);
        return n.equals("authorization") || n.equals("proxy-authorization") || n.equals("cookie") || n.equals("set-cookie")
                || n.contains("api-key") || n.contains("apikey") || n.contains("token");
    }

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
