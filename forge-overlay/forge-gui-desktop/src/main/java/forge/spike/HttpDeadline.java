package forge.spike;

import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/**
 * One HTTP exchange bounded by a real wall-clock deadline -- headers AND body.
 *
 * <p>Why this exists as its own class rather than an inline {@code client.send(...)}:
 * {@link HttpRequest.Builder#timeout} bounds only the wait for the response HEADERS. Once a 200 is
 * on the wire the body read is unbounded, so a server that answers instantly and then dribbles is
 * never cut off. OpenRouter does exactly that -- it emits headers up front and keepalive bytes for
 * the duration of a long generation -- which made every per-model {@code timeoutMs} in
 * {@code ModelCfg} advisory on that lane. Measured: 339101 ms and 309920 ms replies under a
 * configured 90000 ms, no {@code HttpTimeoutException}
 * (runs/pzl/llm/fail-0b5e9844fb067ab9.log, fail-75b8f1921281fffb.log). Consequence: the PZL LLM arm
 * ran every puzzle past its 600 s wall-clock cap, scored them all INVALID, and produced zero rows --
 * the axis's first model score, still PENDING in RESULTS.md.
 *
 * <p>The Claude CLI lane never had this hole: it runs a deadline loop and calls
 * {@code destroyForcibly()} on expiry. Extracting this makes the HTTP lane mean the same thing by
 * the same word, and -- unlike the bridge itself, whose base URL is a {@code static final} read of
 * {@code OPENROUTER_BASE_URL} at class-init and therefore cannot be pointed at a test server --
 * leaves something a test can actually drive. See OpenRouterTimeoutTest.
 */
final class HttpDeadline {
    private HttpDeadline() { }

    /** How long a connection may stay silent before it is treated as stalled (OPENROUTER_IDLE_MS, 0 turns it off).
     *  3 to 5 calls in each C3 game (2026-10-16) got nothing back until the 300 s deadline, once "header parser
     *  received no bytes": 15-25 minutes of every game spent waiting on dead connections. */
    static final long IDLE_MS = idleFromEnv();

    private static long idleFromEnv() {
        try { return Math.max(0, Long.parseLong(System.getenv().getOrDefault("OPENROUTER_IDLE_MS", "90000"))); }
        catch (NumberFormatException e) { return 90000; }
    }

    /** Why the last {@link #send} on this thread returned null: "deadline", "no-headers" or "silent", or null. */
    static final ThreadLocal<String> LAST_STOP = new ThreadLocal<>();
    /** The connection gave no reply (no headers, or it went silent): the transport failed (issue #369). A "deadline"
     *  stop is the model's time spent. */
    static boolean gaveNoReply(String stop) { return "no-headers".equals(stop) || "silent".equals(stop); }
    /** An exception from a send that is the transport failing (connection refused or reset, an IO error); not the
     *  request's own deadline (HttpTimeoutException: the model's time), nor a reply that could not be read. */
    static boolean transportError(Exception e) {
        return e instanceof java.io.IOException && !(e instanceof java.net.http.HttpTimeoutException);
    }
    /** The status and headers of the last {@link #send} on this thread, once they arrived (null before): a call cut
     *  off after its headers still has them, for the run's record (Transcript). */
    static final ThreadLocal<HttpResponse.ResponseInfo> LAST_INFO = new ThreadLocal<>();

    /**
     * Sends {@code req} and returns the response, or {@code null} if {@code remainingMs} elapses
     * first (the in-flight exchange is cancelled). A transport failure is rethrown unwrapped, so
     * callers log the real fault rather than an {@link ExecutionException} wrapper.
     */
    static <T> HttpResponse<T> send(HttpClient client, HttpRequest req,
                                    HttpResponse.BodyHandler<T> handler, long remainingMs)
            throws Exception {
        return send(client, req, handler, remainingMs, IDLE_MS);
    }

    /**
     * As above, and also gives up early on a connection that has gone silent, so the caller can retry at once instead
     * of waiting out the whole deadline. Two signs, each safe for a model that reasons for minutes:
     * <ul>
     *   <li>no response headers after {@code idleMs}: OpenRouter sends them at once for a live request;</li>
     *   <li>body bytes arrived (OpenRouter's keep-alive whitespace) and then none for {@code idleMs}. A reply that sends
     *       nothing until it is done is never cut by this: the rule waits for a first keep-alive byte.</li>
     * </ul>
     */
    static <T> HttpResponse<T> send(HttpClient client, HttpRequest req,
                                    HttpResponse.BodyHandler<T> handler, long remainingMs, long idleMs)
            throws Exception {
        LAST_STOP.remove();
        LAST_INFO.remove();
        final long start = System.currentTimeMillis();
        final java.util.concurrent.atomic.AtomicLong last = new java.util.concurrent.atomic.AtomicLong(start);
        final java.util.concurrent.atomic.AtomicBoolean headers = new java.util.concurrent.atomic.AtomicBoolean();
        final java.util.concurrent.atomic.AtomicBoolean bytes = new java.util.concurrent.atomic.AtomicBoolean();
        final java.util.concurrent.atomic.AtomicReference<HttpResponse.ResponseInfo> seen =
                new java.util.concurrent.atomic.AtomicReference<>();
        HttpResponse.BodyHandler<T> tracking = info -> {
            seen.set(info);
            headers.set(true);
            last.set(System.currentTimeMillis());
            HttpResponse.BodySubscriber<T> inner = handler.apply(info);
            return new HttpResponse.BodySubscriber<T>() {
                @Override public java.util.concurrent.CompletionStage<T> getBody() { return inner.getBody(); }
                @Override public void onSubscribe(java.util.concurrent.Flow.Subscription s) { inner.onSubscribe(s); }
                @Override public void onNext(java.util.List<java.nio.ByteBuffer> item) {
                    bytes.set(true);
                    last.set(System.currentTimeMillis());
                    inner.onNext(item);
                }
                @Override public void onError(Throwable t) { inner.onError(t); }
                @Override public void onComplete() { inner.onComplete(); }
            };
        };
        CompletableFuture<HttpResponse<T>> pending = client.sendAsync(req, tracking);
        final long deadline = start + remainingMs;
        while (true) {
            long now = System.currentTimeMillis();
            long left = deadline - now;
            if (left <= 0) { pending.cancel(true); LAST_STOP.set("deadline"); LAST_INFO.set(seen.get()); return null; }
            try {
                return pending.get(Math.min(left, 250), TimeUnit.MILLISECONDS);
            } catch (TimeoutException te) {
                now = System.currentTimeMillis();
                if (idleMs > 0 && !headers.get() && now - start > idleMs) {
                    pending.cancel(true); LAST_STOP.set("no-headers"); LAST_INFO.set(seen.get()); return null;
                }
                if (idleMs > 0 && bytes.get() && now - last.get() > idleMs) {
                    pending.cancel(true); LAST_STOP.set("silent"); LAST_INFO.set(seen.get()); return null;
                }
            } catch (ExecutionException ee) {
                Throwable cause = ee.getCause();
                throw (cause instanceof Exception) ? (Exception) cause : new RuntimeException(cause);
            }
        }
    }
}
