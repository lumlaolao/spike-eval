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
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Guards the claim that a configured model timeout is a REAL bound on an OpenRouter call.
 *
 * It was not. {@link HttpRequest.Builder#timeout} bounds only the wait for the response headers, and
 * OpenRouter answers with headers immediately and then dribbles keepalive bytes for the length of
 * the generation -- so a 90 s configured timeout permitted a measured 339 s reply, and the PZL LLM
 * arm burned its whole 600 s per-puzzle cap on two or three decisions and scored INVALID every time.
 * Zero rows, on the one arm that costs money.
 *
 * Every server here is loopback and every test asserts against it; nothing in this file can reach
 * openrouter.ai, and the bridge's own loopback rule means no key is sent even when one is present in
 * the environment.
 *
 * Note {@link #thePlainSendRunsPastTheDeadlineThatBoundsHttpDeadline}. It asserts the scenario
 * DISCRIMINATES -- swap HttpDeadline.send back to client.send and these tests fail. Without it this
 * file could pass against the broken implementation and be pure decoration, the same way
 * `make jtest` passed for a month while matching no tests at all. A guard has to be able to fail for
 * the reason it names.
 */
@Test(singleThreaded = true) // the shared handler counters below are only race-free serially
public class OpenRouterTimeoutTest {

    /** Body trickles for this long; every deadline under test is far shorter. */
    private static final int DRIBBLE_MS = 6000;
    private static final long DEADLINE_MS = 1500;
    /**
     * The request-level timeout built into every HttpRequest here: deliberately LONGER than the
     * dribble, so no JDK's own machinery can end the call and the only thing that can is the
     * deadline we pass in. Pinning it this way is not fussiness. The first version of this file
     * asserted "the bare JDK does not bound a body read" -- true on JDK 17 (what run-llm-headless.sh
     * pins, and where the 339 s replies were measured) and FALSE on JDK 26 (what mvn happened to
     * run), so the guard's verdict depended on which java you invoked it with.
     */
    private static final long NO_REQUEST_BOUND_MS = 60_000;

    /** How long the truncated first completion takes; a real bite out of BUDGET_MS. */
    private static final long FIRST_REPLY_MS = 1200;
    /** The bridge's whole-ask deadline in the integration tests. */
    private static final long BUDGET_MS = 2000;

    private static final AtomicInteger completionCalls = new AtomicInteger();
    private static final AtomicInteger billedCalls = new AtomicInteger();
    private static final AtomicInteger handlersOpened = new AtomicInteger();
    private static final AtomicInteger handlersReleased = new AtomicInteger();

    private HttpServer server;
    private String dribbleUrl, promptUrl, loopbackBase, slowBase, billedBase, creditsBase, emptyBase;

    @BeforeClass
    public void startServer() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.setExecutor(Executors.newCachedThreadPool());
        server.createContext("/dribble", OpenRouterTimeoutTest::dribble);
        // A whole OpenRouter-shaped base whose completions endpoint always dribbles.
        server.createContext("/slow/chat/completions", ex -> { drain(ex); dribble(ex); });
        // Stalled connections (C3 games, 2026-10-16): no headers at all; keep-alives that stop; and the long-thinking
        // reply that sends headers and then nothing until it is done, which must never be cut early.
        server.createContext("/noheaders", ex -> { drain(ex); sleep(5000); respond(ex, "{\"late\":true}"); });
        server.createContext("/pause", ex -> {
            drain(ex);
            try {
                ex.sendResponseHeaders(200, 0);
                try (OutputStream os = ex.getResponseBody()) {
                    for (int i = 0; i < 4; i++) { os.write(' '); os.flush(); sleep(100); }
                    sleep(5000);
                    os.write("{\"late\":true}".getBytes(StandardCharsets.UTF_8));
                }
            } catch (Exception ignore) { }
        });
        server.createContext("/quiet", ex -> {
            drain(ex);
            try {
                ex.sendResponseHeaders(200, 0);
                sleep(2500);
                try (OutputStream os = ex.getResponseBody()) { os.write("{\"thought\":true}".getBytes(StandardCharsets.UTF_8)); }
            } catch (Exception ignore) { }
        });
        server.createContext("/prompt", ex -> {
            byte[] b = "{\"ok\":true}".getBytes(StandardCharsets.UTF_8);
            ex.sendResponseHeaders(200, b.length);
            try (OutputStream os = ex.getResponseBody()) { os.write(b); }
        });
        // An OpenRouter chat/completions endpoint whose FIRST reply is slow and length-truncated --
        // so the bridge's [TRUNCATED->RETRY] fires -- and whose second reply dribbles forever. The
        // first reply deliberately burns part of the budget: under ONE shared deadline the whole
        // ask() lands near BUDGET_MS, while a per-send budget would spend FIRST_REPLY_MS + BUDGET_MS.
        server.createContext("/v1/chat/completions", ex -> {
            drain(ex);
            if (completionCalls.incrementAndGet() == 1) {
                try { Thread.sleep(FIRST_REPLY_MS); } catch (InterruptedException ie) { return; }
                // No decision tag + finish_reason=length is exactly the retry trigger.
                byte[] b = ("{\"choices\":[{\"message\":{\"content\":\"still reasoning about the board\"},"
                        + "\"finish_reason\":\"length\"}]}").getBytes(StandardCharsets.UTF_8);
                ex.sendResponseHeaders(200, b.length);
                try (OutputStream os = ex.getResponseBody()) { os.write(b); }
            } else {
                dribble(ex);
            }
        });
        // Billing (pr-review of #282): a length-truncated first reply and its retry each report usage, and a third
        // ask fails with HTTP 500. Every send that came back is billed; a failed one adds nothing.
        server.createContext("/billed/chat/completions", ex -> {
            drain(ex);
            int n = billedCalls.incrementAndGet();
            if (n == 1) respond(ex, "{\"choices\":[{\"message\":{\"content\":\"still reasoning\"},"
                    + "\"finish_reason\":\"length\"}],\"usage\":{\"prompt_tokens\":50,\"completion_tokens\":20}}");
            else if (n == 2) respond(ex, "{\"choices\":[{\"message\":{\"content\":\"CHOICE: 1\"},"
                    + "\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":30,\"completion_tokens\":5}}");
            else {
                try { ex.sendResponseHeaders(500, -1); ex.close(); } catch (Exception ignore) { }
            }
        });
        // Issue #369: a provider out of credits answers 402 to every call; an empty answer is the model's.
        server.createContext("/credits/chat/completions", ex -> {
            drain(ex);
            byte[] b = "{\"error\":{\"message\":\"This request would exceed your available credits\",\"code\":402}}"
                    .getBytes(StandardCharsets.UTF_8);
            try { ex.sendResponseHeaders(402, b.length); try (OutputStream os = ex.getResponseBody()) { os.write(b); } }
            catch (Exception ignore) { }
        });
        server.createContext("/empty/chat/completions", ex -> {
            drain(ex);
            respond(ex, "{\"choices\":[{\"message\":{\"content\":\"\"},\"finish_reason\":\"stop\"}]}");
        });
        server.start();
        String base = "http://127.0.0.1:" + server.getAddress().getPort();
        dribbleUrl = base + "/dribble";
        promptUrl = base + "/prompt";
        loopbackBase = base + "/v1";
        creditsBase = base + "/credits";
        emptyBase = base + "/empty";
        slowBase = base + "/slow";
        billedBase = base + "/billed";
    }

    @AfterClass(alwaysRun = true)
    public void stopServer() {
        if (server != null) server.stop(0);
    }

    private static void sleep(long ms) { try { Thread.sleep(ms); } catch (InterruptedException ignore) { } }

    private static void respond(HttpExchange ex, String body) {
        try {
            byte[] b = body.getBytes(StandardCharsets.UTF_8);
            ex.sendResponseHeaders(200, b.length);
            try (OutputStream os = ex.getResponseBody()) { os.write(b); }
        } catch (Exception ignore) { }
    }

    /** The request body must be consumed or the exchange never completes cleanly. */
    private static void drain(HttpExchange ex) {
        try (InputStream in = ex.getRequestBody()) {
            byte[] buf = new byte[4096];
            while (in.read(buf) > 0) { /* discard */ }
        } catch (Exception ignore) { }
    }

    /** Headers now, body much later -- OpenRouter's long-generation shape. */
    private static void dribble(HttpExchange ex) {
        handlersOpened.incrementAndGet();
        try {
            ex.sendResponseHeaders(200, 0); // 0 => chunked, headers flushed immediately
            try (OutputStream os = ex.getResponseBody()) {
                long until = System.currentTimeMillis() + DRIBBLE_MS;
                while (System.currentTimeMillis() < until) {
                    os.write(' ');
                    os.flush();
                    Thread.sleep(50);
                }
                os.write("{\"done\":true}".getBytes(StandardCharsets.UTF_8));
                os.flush();
            }
        } catch (Exception ignore) {
            // client hung up -- that is the point of the test
        } finally {
            handlersReleased.incrementAndGet();
        }
    }

    private static HttpClient client() {
        return HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build();
    }

    private static HttpRequest get(String url, long timeoutMs) {
        return HttpRequest.newBuilder().uri(URI.create(url))
                .timeout(Duration.ofMillis(timeoutMs)).GET().build();
    }

    // ---- the primitive -------------------------------------------------------------------

    /** THE property: a dribbling body is cut off at the deadline, and reported as no answer. */
    @Test
    public void aDribblingBodyIsCutOffAtTheDeadline() throws Exception {
        long t0 = System.currentTimeMillis();
        HttpResponse<String> resp = HttpDeadline.send(client(), get(dribbleUrl, NO_REQUEST_BOUND_MS),
                HttpResponse.BodyHandlers.ofString(), DEADLINE_MS);
        long elapsed = System.currentTimeMillis() - t0;

        Assert.assertNull(resp, "a call that blew its deadline must read as no answer, not as a reply");
        Assert.assertTrue(elapsed < DRIBBLE_MS - 1000,
                "expected the call to be cut off near " + DEADLINE_MS + " ms, but it ran " + elapsed
                        + " ms -- the deadline is not bounding the body read");
    }

    /**
     * The contrast case, and the reason the test above is evidence rather than decoration: the SAME
     * request, sent the plain way, runs to completion. Holds on any JDK, because the request carries
     * a 60 s timeout and the server finishes in 6 -- nothing here depends on whether a given JDK
     * bounds body reads, only on the deadline being a separate argument that is actually enforced.
     */
    @Test
    public void thePlainSendRunsPastTheDeadlineThatBoundsHttpDeadline() throws Exception {
        long t0 = System.currentTimeMillis();
        HttpResponse<String> resp =
                client().send(get(dribbleUrl, NO_REQUEST_BOUND_MS), HttpResponse.BodyHandlers.ofString());
        long elapsed = System.currentTimeMillis() - t0;

        Assert.assertEquals(resp.statusCode(), 200);
        Assert.assertTrue(elapsed > DEADLINE_MS,
                "the plain send was expected to run past the " + DEADLINE_MS + " ms deadline that"
                        + " bounds HttpDeadline, but returned in " + elapsed + " ms -- if this ever"
                        + " passes trivially, the test above has stopped proving anything");
    }

    private static final long IDLE = 800, LONG_DEADLINE = 10_000;

    private long timed(String path, String[] stop, boolean[] answered) throws Exception {
        String base = "http://127.0.0.1:" + server.getAddress().getPort();
        long t0 = System.currentTimeMillis();
        HttpResponse<String> r = HttpDeadline.send(client(), get(base + path, NO_REQUEST_BOUND_MS),
                HttpResponse.BodyHandlers.ofString(), LONG_DEADLINE, IDLE);
        stop[0] = HttpDeadline.LAST_STOP.get();
        answered[0] = r != null;
        return System.currentTimeMillis() - t0;
    }

    /** No headers within the idle window: a dead connection, given up on long before the deadline. */
    @Test
    public void aConnectionThatNeverAnswersIsGivenUpEarly() throws Exception {
        String[] stop = new String[1]; boolean[] ok = new boolean[1];
        long ms = timed("/noheaders", stop, ok);
        Assert.assertFalse(ok[0]);
        Assert.assertEquals(stop[0], "no-headers");
        Assert.assertTrue(ms < 3000, "given up in " + ms + " ms");
    }

    /** Keep-alive bytes, then silence: stalled mid-generation, given up early. */
    @Test
    public void keepAlivesThatStopAreGivenUpEarly() throws Exception {
        String[] stop = new String[1]; boolean[] ok = new boolean[1];
        long ms = timed("/pause", stop, ok);
        Assert.assertFalse(ok[0]);
        Assert.assertEquals(stop[0], "silent");
        Assert.assertTrue(ms < 3000, "given up in " + ms + " ms");
    }

    /** A reply that keeps sending keep-alives is never cut by the idle rule, however long it runs. */
    @Test
    public void aReplyThatKeepsSendingIsNotCut() throws Exception {
        String[] stop = new String[1]; boolean[] ok = new boolean[1];
        timed("/dribble", stop, ok);
        Assert.assertTrue(ok[0], "the dribbling reply completed: " + stop[0]);
    }

    /** Headers and then nothing until it is done (a model reasoning for minutes with no keep-alives): never cut early. */
    @Test
    public void aReplyThatSendsNothingUntilDoneIsNotCut() throws Exception {
        String[] stop = new String[1]; boolean[] ok = new boolean[1];
        timed("/quiet", stop, ok);
        Assert.assertTrue(ok[0], "the quiet reply completed: " + stop[0]);
    }

    /** A prompt reply is returned untouched -- the deadline must not cost a normal call anything. */
    @Test
    public void aPromptReplyIsReturnedUnharmed() throws Exception {
        long t0 = System.currentTimeMillis();
        HttpResponse<String> resp = HttpDeadline.send(client(), get(promptUrl, NO_REQUEST_BOUND_MS),
                HttpResponse.BodyHandlers.ofString(), DEADLINE_MS);
        long elapsed = System.currentTimeMillis() - t0;

        Assert.assertNotNull(resp, "a fast reply must survive the deadline wrapper");
        Assert.assertEquals(resp.statusCode(), 200);
        Assert.assertTrue(resp.body().contains("\"ok\""), "body should pass through verbatim");
        Assert.assertTrue(elapsed < DEADLINE_MS, "a fast call must not wait out the deadline");
    }

    /**
     * A transport fault surfaces as itself. The bridge logs the exception it catches, so an
     * ExecutionException wrapper would put "java.util.concurrent.ExecutionException" in the run log
     * where the actual connect failure belongs.
     */
    @Test
    public void aTransportFailureIsRethrownUnwrapped() throws Exception {
        // Port 1 on loopback: nothing listens, and binding it needs root, so this reliably refuses.
        try {
            HttpDeadline.send(client(), get("http://127.0.0.1:1/nothing", 2000),
                    HttpResponse.BodyHandlers.ofString(), 2000);
            Assert.fail("expected a transport failure against a closed port");
        } catch (java.util.concurrent.ExecutionException wrapped) {
            Assert.fail("transport failure leaked its ExecutionException wrapper: " + wrapped);
        } catch (IOException expected) {
            // right shape: the caller sees the real fault
        }
    }

    /**
     * Cancelling on expiry must actually release the connection, not merely stop waiting for it.
     * A game is one long-lived HttpClient per seat/tier across 100+ decisions (measured: 30 smart +
     * 53 fast in one game), so a per-timeout socket or thread leak would compound over a multi-hour
     * batch. Puzzles hid this risk -- run_pzl.py forks a JVM per puzzle, so process exit reclaimed
     * everything -- but ordinary games have no such reset.
     *
     * The server side is the real evidence: every dribble handler must EXIT, which only happens when
     * its write fails because the client hung up.
     */
    @Test
    public void repeatedTimeoutsReleaseTheirConnections() throws Exception {
        final int n = 12;
        HttpClient shared = client(); // one client for all of them, as a game has
        int openedBefore = handlersOpened.get(), releasedBefore = handlersReleased.get();
        int threadsBefore = Thread.activeCount();

        for (int i = 0; i < n; i++) {
            CompletableFuture<HttpResponse<String>> f =
                    shared.sendAsync(get(dribbleUrl, NO_REQUEST_BOUND_MS), HttpResponse.BodyHandlers.ofString());
            // Cancel only once the server holds this exchange: under load (16 on 4 cores, PR #373's
            // acceptance) a fixed 200 ms cancelled one request before it arrived (count 11 of 12).
            long arrival = System.currentTimeMillis() + 10_000;
            while (handlersOpened.get() - openedBefore <= i && System.currentTimeMillis() < arrival)
                Thread.sleep(10);
            Assert.assertTrue(handlersOpened.get() - openedBefore > i,
                    "request " + i + " never reached the server within 10 s");
            try { f.get(200, TimeUnit.MILLISECONDS); Assert.fail("dribble should not complete in 200ms"); }
            catch (TimeoutException expected) { f.cancel(true); }
        }

        // Give the server threads a moment to notice the hang-ups and unwind.
        long deadline = System.currentTimeMillis() + 15_000;
        while (handlersReleased.get() - releasedBefore < n && System.currentTimeMillis() < deadline)
            Thread.sleep(100);

        Assert.assertEquals(handlersOpened.get() - openedBefore, n, "server should have seen n requests");
        Assert.assertEquals(handlersReleased.get() - releasedBefore, n,
                "every cancelled exchange must release its server handler; a handler still parked on"
                        + " write means the connection was never actually dropped");
        int leaked = Thread.activeCount() - threadsBefore;
        Assert.assertTrue(leaked < n,
                "thread growth should be bounded by the client's pool, not proportional to the number"
                        + " of cancellations -- saw " + leaked + " new threads for " + n + " timeouts");

        // And the client is still usable afterwards, which a leaked/poisoned pool would prevent.
        HttpResponse<String> after = HttpDeadline.send(shared, get(promptUrl, NO_REQUEST_BOUND_MS),
                HttpResponse.BodyHandlers.ofString(), 5000);
        Assert.assertNotNull(after, "client must still work after repeated cancellations");
        Assert.assertEquals(after.statusCode(), 200);
    }

    // ---- the bridge itself ---------------------------------------------------------------

    /**
     * The integration property, and the one this commit is actually about: the truncation retry
     * SHARES the ask's deadline instead of starting a fresh one.
     *
     * The first completion is slow (FIRST_REPLY_MS) and length-truncated, so [TRUNCATED->RETRY]
     * fires; the retry then dribbles forever. Under one shared deadline the whole ask lands near
     * BUDGET_MS. Under the old per-send budget it would land near FIRST_REPLY_MS + BUDGET_MS, which
     * is what made cfg.timeoutMs mean something different from what run_batch.py's post-cap grace
     * assumes it means.
     */
    @Test
    public void theTruncationRetrySharesTheAsksDeadlineRatherThanDoublingIt() {
        completionCalls.set(0);
        LlmSpikeGui.OpenRouterBridge bridge = new LlmSpikeGui.OpenRouterBridge(
                "test/smart", "deepseek/deepseek-v4-flash", null, loopbackBase, BUDGET_MS);

        long t0 = System.currentTimeMillis();
        String reply = bridge.ask("what is the play?");
        long elapsed = System.currentTimeMillis() - t0;

        Assert.assertEquals(bridge.lastSendCount(), 2,
                "the truncated first reply should have triggered exactly one retry");
        // The truncated original is kept when the retry yields nothing -- ask()'s documented policy
        // ("a truncated-but-substantive original reply is a better final result than a garbled/empty
        // retry"). Pinned here because it means a retry that TIMED OUT and a retry that came back
        // useless are indistinguishable in the returned value; the timing assertion below, and the
        // "[OPENROUTER ...] wall-clock timeout" log line, are what separate them.
        Assert.assertEquals(reply, "still reasoning about the board",
                "expected the truncated original to survive a retry that produced nothing");
        Assert.assertTrue(elapsed < FIRST_REPLY_MS + BUDGET_MS - 300,
                "the retry restarted the budget: ask() took " + elapsed + " ms against a "
                        + BUDGET_MS + " ms deadline (a per-send budget would allow ~"
                        + (FIRST_REPLY_MS + BUDGET_MS) + " ms)");
        Assert.assertTrue(elapsed >= FIRST_REPLY_MS,
                "sanity: the slow first reply should still have been waited for");
    }

    /** Every send that came back is billed, the truncated first one included; a failed ask adds nothing and leaves no
     *  stale count to be read again (pr-review of #282: the seat's budget read the last call's tokens once an ask). */
    @Test
    public void everySendThatCameBackIsBilledAndAFailedAskAddsNothing() {
        billedCalls.set(0);
        LlmSpikeGui.OpenRouterBridge bridge = new LlmSpikeGui.OpenRouterBridge(
                "test/smart", "deepseek/deepseek-v4-flash", null, billedBase, BUDGET_MS);
        Assert.assertEquals(bridge.ask("what is the play?"), "CHOICE: 1");
        Assert.assertEquals(bridge.getBilledPromptTokens(), 80L, "50 for the truncated reply, 30 for its retry");
        Assert.assertEquals(bridge.getBilledCompletionTokens(), 25L);
        Assert.assertNull(bridge.ask("again?"));
        Assert.assertEquals(bridge.getBilledPromptTokens(), 80L, "an HTTP 500 is not billed");
        Assert.assertEquals(bridge.getBilledCompletionTokens(), 25L);
    }

    /** Issue #369: only a transport failure is a dead bridge. A 402 (credits gone) and a 500 are; an empty answer and a
     *  deadline that ran out while the model reasoned are the model's, and the game stays scored. */
    @Test
    public void onlyATransportFailureIsADeadBridge() {
        LlmSpikeGui.OpenRouterBridge credits = new LlmSpikeGui.OpenRouterBridge(
                "test/smart", "deepseek/deepseek-v4-flash", null, creditsBase, BUDGET_MS);
        Assert.assertNull(credits.ask("what is the play?"));
        Assert.assertTrue(credits.lastFailureWasTransport(), "a 402 is the transport failing");
        LlmSpikeGui.OpenRouterBridge empty = new LlmSpikeGui.OpenRouterBridge(
                "test/smart", "deepseek/deepseek-v4-flash", null, emptyBase, BUDGET_MS);
        Assert.assertNull(empty.ask("what is the play?"));
        Assert.assertFalse(empty.lastFailureWasTransport(), "an empty answer is the model's");
        LlmSpikeGui.OpenRouterBridge slow = new LlmSpikeGui.OpenRouterBridge(
                "test/smart", "deepseek/deepseek-v4-flash", null, slowBase, BUDGET_MS);
        Assert.assertNull(slow.ask("what is the play?"));
        Assert.assertFalse(slow.lastFailureWasTransport(), "the deadline running out is the model's time");
        billedCalls.set(2);
        LlmSpikeGui.OpenRouterBridge failing = new LlmSpikeGui.OpenRouterBridge(
                "test/smart", "deepseek/deepseek-v4-flash", null, billedBase, BUDGET_MS);
        Assert.assertNull(failing.ask("again?"));
        Assert.assertTrue(failing.lastFailureWasTransport(), "a 500 is the transport failing");
        Assert.assertNull(credits.ask("and now?"));
        Assert.assertTrue(credits.lastFailureWasTransport());
    }

    /** A connection that sends no headers is no reply, as is a port nobody listens on (an IO error): transport
     *  failures. The deadline running out is the model's time. The bridges read the stop through gaveNoReply. */
    @Test
    public void aSilentConnectionAndARefusedConnectionAreDeadBridges() throws Exception {
        String[] stop = new String[1]; boolean[] ok = new boolean[1];
        timed("/noheaders", stop, ok);
        Assert.assertEquals(stop[0], "no-headers");
        Assert.assertTrue(HttpDeadline.gaveNoReply(stop[0]), "no headers is no reply");
        Assert.assertTrue(HttpDeadline.gaveNoReply("silent"));
        Assert.assertFalse(HttpDeadline.gaveNoReply("deadline"), "the deadline is the model's time");
        Assert.assertFalse(HttpDeadline.transportError(new java.net.http.HttpTimeoutException("headers")),
                "the request deadline is the model's time");
        Assert.assertFalse(HttpDeadline.transportError(new IllegalStateException("not JSON")), "an unreadable reply");
        int closed;
        try (java.net.ServerSocket ss = new java.net.ServerSocket(0, 1, java.net.InetAddress.getLoopbackAddress())) {
            closed = ss.getLocalPort();
        }
        LlmSpikeGui.OpenRouterBridge refused = new LlmSpikeGui.OpenRouterBridge(
                "test/smart", "deepseek/deepseek-v4-flash", null, "http://127.0.0.1:" + closed + "/v1", BUDGET_MS);
        Assert.assertNull(refused.ask("what is the play?"));
        Assert.assertTrue(refused.lastFailureWasTransport(), "connection refused is an IO error");
    }

    /** A bridge pointed at a loopback endpoint needs no API key, and the deadline still applies. */
    @Test
    public void aLoopbackBridgeNeedsNoKeyAndStillHonoursItsDeadline() {
        LlmSpikeGui.OpenRouterBridge bridge = new LlmSpikeGui.OpenRouterBridge(
                "test/smart", "deepseek/deepseek-v4-flash", null, slowBase, BUDGET_MS);
        long t0 = System.currentTimeMillis();
        String reply = bridge.ask("what is the play?");
        long elapsed = System.currentTimeMillis() - t0;

        Assert.assertNull(reply);
        Assert.assertTrue(elapsed < DRIBBLE_MS - 1000,
                "ask() ran " + elapsed + " ms against a " + BUDGET_MS + " ms deadline");
    }
}
