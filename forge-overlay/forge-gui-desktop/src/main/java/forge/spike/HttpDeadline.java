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

    /**
     * Sends {@code req} and returns the response, or {@code null} if {@code remainingMs} elapses
     * first (the in-flight exchange is cancelled). A transport failure is rethrown unwrapped, so
     * callers log the real fault rather than an {@link ExecutionException} wrapper.
     */
    static <T> HttpResponse<T> send(HttpClient client, HttpRequest req,
                                    HttpResponse.BodyHandler<T> handler, long remainingMs)
            throws Exception {
        CompletableFuture<HttpResponse<T>> pending = client.sendAsync(req, handler);
        try {
            return pending.get(remainingMs, TimeUnit.MILLISECONDS);
        } catch (TimeoutException te) {
            pending.cancel(true);
            return null;
        } catch (ExecutionException ee) {
            Throwable cause = ee.getCause();
            throw (cause instanceof Exception) ? (Exception) cause : new RuntimeException(cause);
        }
    }
}
