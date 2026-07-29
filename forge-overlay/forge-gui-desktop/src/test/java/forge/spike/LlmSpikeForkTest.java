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

    // ================================================================================
    // Rollout pure logic (Milestone D rollout implementation). These are the engine-independent
    // pieces every WP number rests on: the seed mix, the forceable-type gate, the forced-index ->
    // logged-choice mapping, the outcome tally, and the summary aggregation.
    // ================================================================================

    @Test
    public void rolloutSeed_isDeterministicAndDistinguishesOptionAndJ() {
        long base = 20017L;
        // Same inputs -> same seed, every time (a rollout re-run with the same j must be byte-identical).
        Assert.assertEquals(LlmSpikeFork.rolloutSeed(base, 0, 0), LlmSpikeFork.rolloutSeed(base, 0, 0));
        // Different option or different j -> different continuation stream. Not a cryptographic
        // guarantee, but SplitMix64's finalizer disperses single-bit input changes; equal outputs
        // here would mean option/j collapse into one stream and all WPs collapse with them.
        Assert.assertNotEquals(LlmSpikeFork.rolloutSeed(base, 0, 0), LlmSpikeFork.rolloutSeed(base, 1, 0));
        Assert.assertNotEquals(LlmSpikeFork.rolloutSeed(base, 0, 0), LlmSpikeFork.rolloutSeed(base, 0, 1));
        Assert.assertNotEquals(LlmSpikeFork.rolloutSeed(base, 1, 2), LlmSpikeFork.rolloutSeed(base, 2, 1));
        // And a different base seed moves everything.
        Assert.assertNotEquals(LlmSpikeFork.rolloutSeed(base, 0, 0), LlmSpikeFork.rolloutSeed(base + 1, 0, 0));
    }

    @Test
    public void parseDeterminize_defaultsToLibraryAndRefusesTypos() {
        // Unset/blank must mean the FIX, not the defect: with determinization off, every rollout
        // arm measured 0/n or n/n (see determinizeHiddenZones' javadoc), so a silent default of
        // "none" would produce single-deal WPs from a run that reported OK.
        Assert.assertEquals(Boolean.TRUE, LlmSpikeFork.parseDeterminize(null));
        Assert.assertEquals(Boolean.TRUE, LlmSpikeFork.parseDeterminize(""));
        Assert.assertEquals(Boolean.TRUE, LlmSpikeFork.parseDeterminize("  library  "));
        Assert.assertEquals(Boolean.TRUE, LlmSpikeFork.parseDeterminize("LIBRARY"));
        Assert.assertEquals(Boolean.FALSE, LlmSpikeFork.parseDeterminize("none"));
        // A typo returns null so the caller can REFUSE. Mapping it to either mode would be the
        // worst outcome available: "libary" would quietly disable the fix while the run stayed green.
        Assert.assertNull(LlmSpikeFork.parseDeterminize("libary"));
        Assert.assertNull(LlmSpikeFork.parseDeterminize("hand"));
        Assert.assertNull(LlmSpikeFork.parseDeterminize("0"));
    }

    @Test
    public void determinizeHiddenZones_isANoOpWhenDisabledOrPlayerless() {
        // The engine-backed path is covered by the A/B run recorded in the commit message
        // (turns 16/18 fixed -> 10..14 spread, and WPs 0.20/0.10 where every arm had been 0 or 1).
        // What is unit-testable here is the guard: disabled or null must touch nothing and report 0,
        // because runRollout treats "determinize requested but 0 libraries shuffled" as a hard
        // failure and a guard that lied about its count would defeat that check.
        Assert.assertEquals(0, LlmSpikeFork.determinizeHiddenZones(null, true));
        Assert.assertEquals(0, LlmSpikeFork.determinizeHiddenZones(null, false));
    }

    @Test
    public void mix64_reallyMixes() {
        // CORRECTED 2026-07-28. This test used to assert mix64(0) != 0 and had therefore NEVER
        // passed -- zero IS a fixed point of the SplitMix64 finalizer, unavoidably: every term is
        // 0 XOR (0 >>> k) == 0 multiplied by a constant. Asserting otherwise is a claim about
        // arithmetic, not about this code, and no implementation could satisfy it. (It went
        // unnoticed because the Java suite was not part of the validation ladder; it is now.)
        //
        // The intent behind it was sound, so it is kept and made checkable. What actually has to
        // hold is that the finalizer disperses -- if it were the identity, rollout_seed would
        // collapse to base ^ (i*C1) ^ (j*C2) and neighbouring (i, j) pairs would sit in adjacent,
        // strongly-correlated Random streams.
        Assert.assertEquals(LlmSpikeFork.mix64(0L), 0L);   // documented, not denied

        // Pinned known-answer vector, byte-identical to the Python mirror in
        // eval/test_verify_keys.py (py_rollout_seed(0, 1, 0) == 0xE220A8397B1DCDAF). Changing the
        // finalizer constants breaks BOTH sides at once, which is the point of pinning it twice.
        Assert.assertEquals(LlmSpikeFork.mix64(LlmSpikeFork.ROLLOUT_C1), 0xE220A8397B1DCDAFL);

        // Not the identity, and a +1 input change does not produce a +1 output change.
        Assert.assertNotEquals(LlmSpikeFork.mix64(1L), 1L);
        Assert.assertNotEquals(LlmSpikeFork.mix64(20017L), 20017L);
        Assert.assertNotEquals(LlmSpikeFork.mix64(1L) - LlmSpikeFork.mix64(0L), 1L);

        // The original worry, stated as something testable: with a real seed base, (option 0, j 0)
        // must not hand the continuation the raw base seed back.
        Assert.assertNotEquals(LlmSpikeFork.rolloutSeed(20017L, 0, 0), 20017L);
    }

    @Test
    public void isForceableType_onlyPlayAndMulligan() {
        Assert.assertTrue(LlmSpikeFork.isForceableType("play"));
        Assert.assertTrue(LlmSpikeFork.isForceableType("mulligan"));
        // Count-encoded types log a subset SIZE, not an index — forcing them is undefined in v1.
        Assert.assertFalse(LlmSpikeFork.isForceableType("attackers"));
        Assert.assertFalse(LlmSpikeFork.isForceableType("blockers"));
        Assert.assertFalse(LlmSpikeFork.isForceableType("discard"));
        Assert.assertFalse(LlmSpikeFork.isForceableType("target"));
        Assert.assertFalse(LlmSpikeFork.isForceableType(null));
    }

    @Test
    public void expectedLoggedChoice_playMapsPassSlotAndNegativesToMinusOne() {
        // A 4-option play menu: 0..2 are real spells/lands, 3 is the trailing "PASS (do nothing)".
        Assert.assertEquals(LlmSpikeFork.expectedLoggedChoice("play", 0, 4), 0);
        Assert.assertEquals(LlmSpikeFork.expectedLoggedChoice("play", 2, 4), 2);
        Assert.assertEquals(LlmSpikeFork.expectedLoggedChoice("play", 3, 4), -1); // PASS slot logs -1
        Assert.assertEquals(LlmSpikeFork.expectedLoggedChoice("play", -1, 4), -1); // decline force
    }

    @Test
    public void expectedLoggedChoice_mulliganMapsDeclineToKeep() {
        Assert.assertEquals(LlmSpikeFork.expectedLoggedChoice("mulligan", 0, 2), 0);
        Assert.assertEquals(LlmSpikeFork.expectedLoggedChoice("mulligan", 1, 2), 1);
        Assert.assertEquals(LlmSpikeFork.expectedLoggedChoice("mulligan", -1, 2), 0); // decline == KEEP
    }

    @Test
    public void outcomeSlot_forcedFailureIsInvalidNeverALoss() {
        // The seat won but the force was not honoured -> invalid (3), NOT a win and NOT a loss.
        Assert.assertEquals(LlmSpikeFork.outcomeSlot("A", true, false), 3);
        Assert.assertEquals(LlmSpikeFork.outcomeSlot("B", true, false), 3);
        Assert.assertEquals(LlmSpikeFork.outcomeSlot("invalid", true, true), 3);
        Assert.assertEquals(LlmSpikeFork.outcomeSlot(null, true, true), 3);
    }

    @Test
    public void outcomeSlot_identityKeyedWinLossDraw() {
        Assert.assertEquals(LlmSpikeFork.outcomeSlot("A", true, true), 0);  // seat is A, A won -> win
        Assert.assertEquals(LlmSpikeFork.outcomeSlot("B", true, true), 1);  // seat is A, B won -> loss
        Assert.assertEquals(LlmSpikeFork.outcomeSlot("A", false, true), 1); // seat is B, A won -> loss
        Assert.assertEquals(LlmSpikeFork.outcomeSlot("B", false, true), 0); // seat is B, B won -> win
        Assert.assertEquals(LlmSpikeFork.outcomeSlot("draw", true, true), 2);
    }

    @Test
    public void perOptionJson_aggregatesOnlyRolledOptionsInOrder() {
        int[][] tally = new int[3][4];
        tally[0] = new int[] {5, 2, 1, 0};
        tally[2] = new int[] {0, 0, 0, 8};
        long[][] seeds = new long[3][2];
        seeds[0] = new long[] {11L, 22L};
        seeds[2] = new long[] {33L, 44L};
        String json = LlmSpikeFork.perOptionJson(tally, seeds, List.of(0, 2));
        Assert.assertEquals(json,
                "[{\"option\":0,\"wins\":5,\"losses\":2,\"draws\":1,\"invalid\":0,\"seeds\":[11,22]},"
                + "{\"option\":2,\"wins\":0,\"losses\":0,\"draws\":0,\"invalid\":8,\"seeds\":[33,44]}]");
    }

    @Test
    public void prefixMatches_detectsLengthAndRowDrift() {
        List<String[]> a = new ArrayList<>();
        a.add(new String[] {"play", "A", "3"});
        a.add(new String[] {"mulligan", "B", "2"});
        List<String[]> same = new ArrayList<>();
        same.add(new String[] {"play", "A", "3"});
        same.add(new String[] {"mulligan", "B", "2"});
        Assert.assertTrue(LlmSpikeFork.prefixMatches(a, same));
        List<String[]> shorter = new ArrayList<>(same.subList(0, 1));
        Assert.assertFalse(LlmSpikeFork.prefixMatches(a, shorter));
        List<String[]> drifted = new ArrayList<>();
        drifted.add(new String[] {"play", "A", "3"});
        drifted.add(new String[] {"mulligan", "B", "999"});
        Assert.assertFalse(LlmSpikeFork.prefixMatches(a, drifted));
    }
}
