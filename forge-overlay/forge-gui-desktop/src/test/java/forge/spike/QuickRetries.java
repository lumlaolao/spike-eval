package forge.spike;

/**
 * The seat's retry backoff ({@code BRIDGE_RETRY_BACKOFF_MS}: 0.5 s, then 2 s) cut to 1 ms for a test class, and put
 * back after it. A dead-bridge test asks a bridge that never answers, so each lost decision slept 2.5 s that no
 * assertion reads (what those tests check is how many asks, lost decisions, breaker trips and tokens were counted);
 * LlmSpikeTest, GoldenChecksTest (and GoldenChecksToolTest) and LineFindingProbeTest spent about a minute of the
 * javatests stage in those sleeps. A class switches it on in a @BeforeClass and off in an @AfterClass(alwaysRun),
 * so a class that runs after it waits the real backoff.
 */
final class QuickRetries {
    private static long[] saved;

    private QuickRetries() { }

    static synchronized void on() {
        long[] backoff = LlmSpikeGui.PlayerControllerLLM.BRIDGE_RETRY_BACKOFF_MS;
        if (saved == null) saved = backoff.clone();
        java.util.Arrays.fill(backoff, 1L);
    }

    static synchronized void off() {
        if (saved == null) return;
        System.arraycopy(saved, 0, LlmSpikeGui.PlayerControllerLLM.BRIDGE_RETRY_BACKOFF_MS, 0, saved.length);
        saved = null;
    }
}
