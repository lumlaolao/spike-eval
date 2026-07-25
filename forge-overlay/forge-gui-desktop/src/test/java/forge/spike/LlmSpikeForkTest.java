package forge.spike;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;

/**
 * Hermetic unit coverage for {@link LlmSpikeFork}'s engine-independent (pure string-parsing/diff)
 * helpers — no Forge model, no Game/Match, no System.exit. Added as part of the Milestone-D
 * verifier pass (pre-commit finding: zero test coverage of this logic). These deliberately do NOT
 * extend {@link LlmSpikeTest} / share its {@code @BeforeClass} FModel.initialize() — every function
 * exercised here (decisionRows, rowsMatch, evaluateProvenance, numField, boolField) is a plain
 * static function over jsonl text and/or a {@code Map<String,String>} env snapshot, so none of them
 * need a live Forge model and this class runs fast.
 */
public class LlmSpikeForkTest {

    // ================================================================================
    // numField / boolField: tiny scalar readers LlmSpikeFork's provenance/diff logic is built on.
    // ================================================================================

    @Test
    public void numField_nullLineReturnsNull() {
        Assert.assertNull(LlmSpikeFork.numField(null, "seed"));
    }

    @Test
    public void numField_missingKeyReturnsNull() {
        Assert.assertNull(LlmSpikeFork.numField("{\"turns\":12}", "seed"));
    }

    @Test
    public void numField_extractsPositiveAndNegativeIntegers() {
        Assert.assertEquals(LlmSpikeFork.numField("{\"seed\":4242,\"turns\":16}", "seed"), "4242");
        // -1 is the real value writeGameSummary emits for "no outcome yet" (turns=-1) -- the regex
        // must accept the leading minus, not just unsigned digits.
        Assert.assertEquals(LlmSpikeFork.numField("{\"turns\":-1}", "turns"), "-1");
    }

    @Test
    public void boolField_nullLineReturnsNull() {
        Assert.assertNull(LlmSpikeFork.boolField(null, "swap"));
    }

    @Test
    public void boolField_missingKeyReturnsNull() {
        Assert.assertNull(LlmSpikeFork.boolField("{\"seed\":1}", "swap"));
    }

    @Test
    public void boolField_extractsTrueAndFalse() {
        Assert.assertEquals(LlmSpikeFork.boolField("{\"swap\":true}", "swap"), Boolean.TRUE);
        Assert.assertEquals(LlmSpikeFork.boolField("{\"swap\":false}", "swap"), Boolean.FALSE);
    }

    // ================================================================================
    // decisionRows: must pick out ONLY t:"decision" lines (not t:"game"/t:"fork_enumerate"/garbage),
    // in file order, as [type, seat, n_options].
    // ================================================================================

    @Test
    public void decisionRows_extractsOnlyDecisionLinesInFileOrder() {
        List<String> lines = new ArrayList<>();
        lines.add("{\"t\":\"game\",\"seed\":4242,\"turns\":16}"); // must be ignored
        lines.add("{\"t\":\"decision\",\"seat\":\"A\",\"type\":\"play\",\"n_options\":3,\"choice\":1}");
        lines.add("not even json, a stray log line"); // must be ignored, not throw
        lines.add("{\"t\":\"decision\",\"seat\":\"B\",\"type\":\"attackers\",\"n_options\":2,\"choice\":0}");
        lines.add("{\"t\":\"fork_enumerate\",\"idx\":5,\"seat\":\"A\",\"type\":\"play\",\"n_options\":3}"); // ignored

        List<String[]> rows = LlmSpikeFork.decisionRows(lines);

        Assert.assertEquals(rows.size(), 2, "only the two t:\"decision\" lines should be picked up");
        Assert.assertEquals(rows.get(0), new String[] {"play", "A", "3"});
        Assert.assertEquals(rows.get(1), new String[] {"attackers", "B", "2"});
    }

    @Test
    public void decisionRows_emptyInputYieldsEmptyList() {
        Assert.assertTrue(LlmSpikeFork.decisionRows(new ArrayList<>()).isEmpty());
    }

    // ================================================================================
    // rowsMatch: exact (type, seat, n_options) equality -- the row-by-row diff LlmSpikeFork's check
    // mode uses to find the first REPLAY-DRIFT.
    // ================================================================================

    @Test
    public void rowsMatch_identicalRowsMatch() {
        Assert.assertTrue(LlmSpikeFork.rowsMatch(
                new String[] {"play", "A", "3"}, new String[] {"play", "A", "3"}));
    }

    @Test
    public void rowsMatch_differingTypeDoesNotMatch() {
        Assert.assertFalse(LlmSpikeFork.rowsMatch(
                new String[] {"play", "A", "3"}, new String[] {"attackers", "A", "3"}));
    }

    @Test
    public void rowsMatch_differingSeatDoesNotMatch() {
        Assert.assertFalse(LlmSpikeFork.rowsMatch(
                new String[] {"play", "A", "3"}, new String[] {"play", "B", "3"}));
    }

    @Test
    public void rowsMatch_differingNOptionsDoesNotMatch() {
        // This is the synthetic corruption HANDOFF.md's own 5b negative check used (n_options 3 -> 999).
        Assert.assertFalse(LlmSpikeFork.rowsMatch(
                new String[] {"play", "A", "3"}, new String[] {"play", "A", "999"}));
    }

    // ================================================================================
    // evaluateProvenance: the BLOCKER-adjacent HIGH fix. seed/swap/controllers/decks stay strict
    // (any disagreement refuses); engine_sha/overlay_sha are best-effort (unset/"unknown" CURRENT
    // value warns instead of refusing OR silently no-op-passing; both sides present and differing
    // still refuses).
    // ================================================================================

    private static String sourceLine(String engineSha, String overlaySha) {
        return "{\"t\":\"game\",\"seed\":4242,\"swap\":false"
                + ",\"deck_a\":\"Grixis Reanimator\",\"deck_b\":\"Mardu Energy\""
                + ",\"controller_a\":\"random\",\"controller_b\":\"random\""
                + ",\"engine_sha\":\"" + engineSha + "\",\"overlay_sha\":\"" + overlaySha + "\"}";
    }

    private static Map<String, String> matchingEnv(String engineSha, String overlaySha) {
        Map<String, String> env = new java.util.HashMap<>();
        env.put("GAME_SEED", "4242");
        env.put("CONTROLLER_A", "random");
        env.put("CONTROLLER_B", "random");
        env.put("DECK_A_NAME", "Grixis Reanimator");
        env.put("DECK_B_NAME", "Mardu Energy");
        if (engineSha != null) env.put("ENGINE_SHA", engineSha);
        if (overlaySha != null) env.put("OVERLAY_SHA", overlaySha);
        return env;
    }

    @Test
    public void evaluateProvenance_fullyMatchingEnvHasNoMismatchesAndNoWarnings() {
        String src = sourceLine("abc123", "def456");
        Map<String, String> env = matchingEnv("abc123", "def456");

        LlmSpikeFork.ProvenanceVerdict v = LlmSpikeFork.evaluateProvenance(src, env);

        Assert.assertTrue(v.mismatches.isEmpty(), "mismatches: " + v.mismatches);
        Assert.assertTrue(v.shaWarnings.isEmpty(), "sha warnings: " + v.shaWarnings);
        Assert.assertFalse(v.shouldRefuse());
    }

    @Test
    public void evaluateProvenance_unsetCurrentShaWarnsButDoesNotRefuse() {
        // The exact bare-launch scenario the fix targets: ENGINE_SHA/OVERLAY_SHA are never set by a
        // plain `bash run-llm-headless.sh` invocation. This must NOT refuse a legitimate replay --
        // the old "unknown" default either matched a source that was also "unknown" (no-op) or,
        // worse, refused a real run_batch.py-stamped source. Neither should happen: it should warn.
        String src = sourceLine("abc123", "def456"); // a source recorded with real shas (run_batch.py)
        Map<String, String> env = matchingEnv(null, null); // bare launch: ENGINE_SHA/OVERLAY_SHA unset

        LlmSpikeFork.ProvenanceVerdict v = LlmSpikeFork.evaluateProvenance(src, env);

        Assert.assertTrue(v.mismatches.isEmpty(),
                "an unset current sha must never itself cause a refusal: " + v.mismatches);
        Assert.assertFalse(v.shouldRefuse(),
                "a bare launch replaying a run_batch.py-stamped source must NOT be refused on sha grounds");
        Assert.assertEquals(v.shaWarnings.size(), 2, "both sha dimensions should warn: " + v.shaWarnings);
        Assert.assertTrue(v.shaWarnings.get(0).contains("ENGINE_SHA") || v.shaWarnings.get(1).contains("ENGINE_SHA"));
        Assert.assertTrue(v.shaWarnings.get(0).contains("OVERLAY_SHA") || v.shaWarnings.get(1).contains("OVERLAY_SHA"));
    }

    @Test
    public void evaluateProvenance_unknownSourceShaVsRealCurrentShaWarnsNotRefuses() {
        // The mirror image of the bare-launch case (D-verifier HIGH #1): the SOURCE was recorded
        // by a bare launch (writeGameSummary stamps the literal "unknown"), and the replay runs
        // under a run_batch.py-stamped environment with real shas. "unknown" vs "abc123" is not
        // drift evidence -- the source side simply never had the sha. Must warn, never refuse.
        String src = sourceLine("unknown", "unknown"); // bare-launch-recorded source
        Map<String, String> env = matchingEnv("abc123", "def456"); // stamped replay environment

        LlmSpikeFork.ProvenanceVerdict v = LlmSpikeFork.evaluateProvenance(src, env);

        Assert.assertTrue(v.mismatches.isEmpty(),
                "an unknown SOURCE sha must never itself cause a refusal: " + v.mismatches);
        Assert.assertFalse(v.shouldRefuse(),
                "a stamped environment replaying a bare-launch source must NOT be refused on sha grounds");
        Assert.assertEquals(v.shaWarnings.size(), 2, "both sha dimensions should warn: " + v.shaWarnings);
    }

    @Test
    public void evaluateProvenance_literalUnknownCurrentShaAlsoWarnsNotRefuses() {
        // Some callers might explicitly export ENGINE_SHA=unknown (matching the OLD default) --
        // that must be treated the same as unset, not as a "set" value to strictly compare.
        String src = sourceLine("abc123", "def456");
        Map<String, String> env = matchingEnv("unknown", "unknown");

        LlmSpikeFork.ProvenanceVerdict v = LlmSpikeFork.evaluateProvenance(src, env);

        Assert.assertFalse(v.shouldRefuse());
        Assert.assertEquals(v.shaWarnings.size(), 2);
    }

    @Test
    public void evaluateProvenance_bothShasSetAndDifferingRefuses() {
        // Both sides genuinely present and disagreeing must still refuse -- the fix narrows the
        // false-positive/false-negative cases, it does not remove sha checking altogether.
        String src = sourceLine("abc123", "def456");
        Map<String, String> env = matchingEnv("ZZZ999", "def456"); // engine_sha differs, overlay matches

        LlmSpikeFork.ProvenanceVerdict v = LlmSpikeFork.evaluateProvenance(src, env);

        Assert.assertTrue(v.shouldRefuse(), "a real, present sha disagreement must still refuse");
        Assert.assertEquals(v.mismatches.size(), 1);
        Assert.assertTrue(v.mismatches.get(0).contains("engine_sha"), v.mismatches.get(0));
        Assert.assertTrue(v.shaWarnings.isEmpty(), "overlay_sha matched and engine_sha is a real mismatch, "
                + "not an unverifiable dimension -- no warning expected here: " + v.shaWarnings);
    }

    @Test
    public void evaluateProvenance_strictFieldMismatchRefusesEvenWithShasUnset() {
        // seed/swap/controllers/decks must stay strict regardless of the sha relaxation.
        String src = sourceLine("abc123", "def456");
        Map<String, String> env = matchingEnv(null, null);
        env.put("GAME_SEED", "9999"); // deliberately wrong seed (mirrors HANDOFF.md's own negative check)

        LlmSpikeFork.ProvenanceVerdict v = LlmSpikeFork.evaluateProvenance(src, env);

        Assert.assertTrue(v.shouldRefuse());
        Assert.assertEquals(v.mismatches.size(), 1);
        Assert.assertTrue(v.mismatches.get(0).startsWith("seed:"), v.mismatches.get(0));
        // The sha warnings still fire independently -- they're orthogonal to the strict-field refusal.
        Assert.assertEquals(v.shaWarnings.size(), 2);
    }

    @Test
    public void evaluateProvenance_controllerAndDeckMismatchesAreBothReported() {
        String src = sourceLine("abc123", "def456");
        Map<String, String> env = matchingEnv("abc123", "def456");
        env.put("CONTROLLER_A", "floor"); // source recorded "random"
        env.put("DECK_B_NAME", "Some Other Deck"); // source recorded "Mardu Energy"

        LlmSpikeFork.ProvenanceVerdict v = LlmSpikeFork.evaluateProvenance(src, env);

        Assert.assertTrue(v.shouldRefuse());
        Assert.assertEquals(v.mismatches.size(), 2, "both mismatches must be reported, not just the first: " + v.mismatches);
    }

    @Test
    public void currentSeedString_unsetGameSeedReturnsNull() {
        Assert.assertNull(LlmSpikeFork.currentSeedString(Map.of()));
    }

    @Test
    public void currentSeedString_numericGameSeedRendersCanonically() {
        Assert.assertEquals(LlmSpikeFork.currentSeedString(Map.of("GAME_SEED", "  4242  ")), "4242");
    }
}
