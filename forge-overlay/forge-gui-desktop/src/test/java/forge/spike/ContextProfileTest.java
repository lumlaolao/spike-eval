package forge.spike;

import org.testng.Assert;
import org.testng.annotations.Test;

import forge.spike.LlmSpikeGui.ContextProfile;

/**
 * Guards the context profile as a MEASUREMENT IDENTITY, not just a config struct.
 *
 * The property that matters is not "the code runs" — it is that a run's context shape can never
 * change without its recorded identity changing too, and that the default shape is bit-for-bit the
 * pre-profile harness. A profile system that silently drifts is strictly worse than the hardcoded
 * literals it replaced, because it looks like provenance while providing none.
 */
public class ContextProfileTest {

    /** v1-compat's knobs ARE the definition of every measurement taken before profiles existed.
     *  Each literal here was read off the call site it replaced. If this test fails, either someone
     *  "improved" the default — which silently invalidates every artifact on disk — or a knob was
     *  renamed without thinking about what that does to comparability. Neither is a test to update
     *  casually; add a NEW profile instead. */
    @Test
    public void v1CompatPinsThePreProfileHarnessExactly() {
        ContextProfile p = ContextProfile.V1_COMPAT;
        Assert.assertEquals(p.name, "v1-compat");
        Assert.assertEquals(p.recentEvents, 10, "the historical RECENT EVENTS tail");
        Assert.assertEquals(p.intelCap, 16, "the historical INTEL deque cap");
        Assert.assertEquals(p.yardCap, 30, "the historical graveyard render cap");
        Assert.assertEquals(p.exileCap, 30, "the historical exile render cap");
        Assert.assertEquals(p.castHistoryCap, 25, "the historical OPP CAST distinct-name cap");
    }

    /** The sha is the field that lands in run provenance, so it must be STABLE across builds — a
     *  sha that drifts makes every historical context_sha unmatchable. Pinned literally, which is
     *  the only way this assertion can fail on an accidental change to canonical(). */
    @Test
    public void v1CompatShaIsPinned() {
        Assert.assertEquals(ContextProfile.V1_COMPAT.sha(), "73b94c6d03c7");
    }

    /** Two profiles that differ in ANY knob must differ in sha; otherwise provenance cannot
     *  distinguish the measurements and pooling them looks legal. */
    @Test
    public void differentProfilesGetDifferentShas() {
        Assert.assertNotEquals(ContextProfile.V2_MEMORY.sha(), ContextProfile.V1_COMPAT.sha());
        Assert.assertNotEquals(ContextProfile.V2_MEMORY.name, ContextProfile.V1_COMPAT.name);
    }

    /** Every registered profile must have a distinct sha. Two profiles colliding would make
     *  provenance actively misleading rather than merely absent. */
    @Test
    public void everyRegisteredProfileHasADistinctSha() {
        java.util.Set<String> seen = new java.util.HashSet<>();
        for (ContextProfile p : new ContextProfile[] {
                ContextProfile.V1_COMPAT, ContextProfile.V2_MEMORY, ContextProfile.V3_COMPACT,
                ContextProfile.V4_FAITHFUL }) {
            Assert.assertTrue(seen.add(p.sha()), "sha collision on " + p.name);
        }
        Assert.assertEquals(seen.size(), ContextProfile.names().size(),
                "every registered name must be covered by this test");
    }

    /** v4-faithful is a different MEASUREMENT (a different renderer, not just different caps), so its
     *  identity carries the observation schema; every legacy profile keeps its context.v2 preimage
     *  byte-for-byte so the context_sha already stamped on artifacts on disk still matches. */
    @Test
    public void faithfulProfileCarriesTheObservationVersionAndLegacyPreimagesAreUntouched() {
        Assert.assertTrue(ContextProfile.V4_FAITHFUL.faithfulObservation);
        String c = ContextProfile.V4_FAITHFUL.canonical();
        Assert.assertTrue(c.startsWith("context.v3|"), c);
        Assert.assertTrue(c.endsWith("|observation=" + SeatObservation.VERSION), c);
        for (ContextProfile legacy : new ContextProfile[] {
                ContextProfile.V1_COMPAT, ContextProfile.V2_MEMORY, ContextProfile.V3_COMPACT }) {
            Assert.assertFalse(legacy.faithfulObservation, legacy.name);
            Assert.assertTrue(legacy.canonical().startsWith("context.v2|"), legacy.canonical());
            Assert.assertFalse(legacy.canonical().contains("observation="), legacy.canonical());
        }
        Assert.assertEquals(ContextProfile.V1_COMPAT.sha(), "73b94c6d03c7", "legacy sha moved: artifacts on disk no longer match");
        Assert.assertEquals(ContextProfile.resolve("v4-faithful").name, "v4-faithful");
    }

    /** v3-compact is the ladder: it must select TIERED and configure all three tier knobs, or the
     *  renderer silently degrades to a plain window. */
    @Test
    public void v3CompactIsAConfiguredLadder() {
        ContextProfile p = ContextProfile.V3_COMPACT;
        Assert.assertEquals(p.historyMode, GameHistory.Mode.TIERED);
        Assert.assertTrue(p.digestTurns > 0, "some turns must get a full digest");
        Assert.assertTrue(p.digestPerTurn > 0, "a digest line with no entries is not a digest");
        Assert.assertTrue(p.compactTurnsPerLine > 1,
                "the hard tier must collapse MULTIPLE turns per line, else it is just the log again");
    }

    /** v2-memory must change the history knob and ONLY the history knobs. A profile that moves five
     *  dials at once cannot attribute a win-rate change to any one of them, which defeats the point
     *  of having profiles at all. */
    @Test
    public void v2MemoryIsAOneDimensionChange() {
        ContextProfile v1 = ContextProfile.V1_COMPAT, v2 = ContextProfile.V2_MEMORY;
        Assert.assertEquals(v2.recentEvents, ContextProfile.EVENTS_SINCE_LAST_DECISION);
        Assert.assertTrue(v2.recentEventsMax > v1.recentEvents, "the cap must actually be raised");
        Assert.assertTrue(v2.intelCap > v1.intelCap, "durable-fact memory grows with event memory");
        // Everything NOT about memory is held fixed.
        Assert.assertEquals(v2.yardCap, v1.yardCap);
        Assert.assertEquals(v2.exileCap, v1.exileCap);
        Assert.assertEquals(v2.castHistoryCap, v1.castHistoryCap);
    }

    /** The canonical preimage must mention every knob. Adding a knob without adding it here would
     *  produce two materially different profiles sharing one sha — the exact silent-collision this
     *  design exists to prevent. */
    @Test
    public void canonicalCoversEveryKnob() {
        String c = ContextProfile.V2_MEMORY.canonical();
        for (String key : new String[] {"name=", "recent_events=", "recent_events_max=",
                                        "intel_cap=", "yard_cap=", "exile_cap=", "cast_history_cap="}) {
            Assert.assertTrue(c.contains(key), "canonical() is missing " + key + ": " + c);
        }
        Assert.assertTrue(c.startsWith("context.v2|"), "the preimage must carry its own schema version");
    }

    /** An unknown CONTEXT_PROFILE must be fatal. Falling back to the default would produce a run
     *  whose provenance says v1-compat while its operator believes it is running something else —
     *  undetectable after the fact, and the single worst outcome this type can have. */
    @Test
    public void unknownProfileNameThrowsAndNamesTheKnownProfiles() {
        // Driven through resolve(), which exists precisely so this claim is testable: a test cannot
        // mutate the process environment, so asserting it via fromEnv() would have been a test that
        // named the behavior without ever reaching it.
        try {
            ContextProfile.resolve("v9-nonexistent");
            Assert.fail("an unknown profile name must throw, not fall back to the default");
        } catch (IllegalArgumentException e) {
            String m = String.valueOf(e.getMessage());
            Assert.assertTrue(m.contains("v9-nonexistent"), "the message must quote the bad name: " + m);
            for (String known : ContextProfile.names()) {
                Assert.assertTrue(m.contains(known),
                        "the message must offer '" + known + "' as a valid choice: " + m);
            }
        }
    }

    /** The absent/blank cases are the ones every normal run takes; they must resolve, not throw. */
    @Test
    public void absentOrBlankProfileNameResolvesToTheDefault() {
        Assert.assertEquals(ContextProfile.resolve(null).name, "v1-compat");
        Assert.assertEquals(ContextProfile.resolve("   ").name, "v1-compat");
        Assert.assertEquals(ContextProfile.resolve("v2-memory").name, "v2-memory");
        Assert.assertEquals(ContextProfile.resolve(" v3-compact ").name, "v3-compact",
                "surrounding whitespace in an env var must not be fatal");
    }

    /** The egress gate must fire on real egress and ONLY on real egress. A gate that also blocks
     *  loopback trains the operator to set the consent flag reflexively, which is how a consent flag
     *  stops being consent — and it blocks the free local lane that makes iteration affordable. */
    @Test
    public void loopbackEndpointsAreExemptFromTheEgressGate() {
        for (String local : new String[] {"http://localhost:11434/v1", "http://127.0.0.1:8080/v1",
                                          "http://[::1]:11434/v1", "HTTP://LocalHost:1234/v1"}) {
            Assert.assertTrue(LlmSpikeGui.LLMBridgeFactory.isLoopbackEndpoint(local),
                    "must be recognised as loopback: " + local);
        }
    }

    /** Fail CLOSED on anything that is not provably loopback. An unset base URL is the real
     *  openrouter.ai default, and an unparseable one is not evidence of safety. */
    @Test
    public void nonLoopbackAndUnparseableEndpointsStillRequireConsent() {
        for (String remote : new String[] {null, "", "   ", "https://openrouter.ai/api/v1",
                                           "http://localhost.evil.com/v1", "not a url",
                                           "http://169.254.169.254/v1"}) {
            Assert.assertFalse(LlmSpikeGui.LLMBridgeFactory.isLoopbackEndpoint(remote),
                    "must NOT be treated as loopback: " + remote);
        }
    }

    /** The key requirement follows the same loopback rule as the egress gate. These two must never
     *  disagree: an endpoint exempt from the gate but still demanding a key is unusable, which is
     *  exactly the state the free local game lane was in. */
    @Test
    public void theKeyRuleAndTheEgressRuleAgreeOnWhatIsLocal() {
        for (String local : new String[] {"http://localhost:11434/v1", "http://127.0.0.1:8080/v1"}) {
            Assert.assertTrue(LlmSpikeGui.LLMBridgeFactory.isLoopbackEndpoint(local),
                    "one predicate governs both the egress gate and the key requirement: " + local);
        }
        Assert.assertFalse(LlmSpikeGui.LLMBridgeFactory.isLoopbackEndpoint("https://openrouter.ai/api/v1"),
                "the real API must require both consent and a key");
    }

    /** Effort is measurement identity, so an unset one must be RECORDED as such, not blank.
     *
     *  This test earned its keep on its first run: the knob was originally named CLAUDE_EFFORT,
     *  which is ALREADY CLAUDE CODE'S OWN env var and was set to "high" in the developing shell, so
     *  the harness would have silently inherited the effort of whatever terminal launched it. The
     *  assertion below fails loudly if a future rename walks back into another tool's namespace. */
    @Test
    public void unsetEffortIsStampedAsCliDefault() {
        // No CLAUDE_BRIDGE_EFFORT* in the test environment, so both tiers report the sentinel rather
        // than null/"" — a blank provenance field is indistinguishable from a field nobody wrote.
        Assert.assertEquals(LlmSpikeGui.ClaudeBridge.effortLabel(false), "cli-default");
        Assert.assertEquals(LlmSpikeGui.ClaudeBridge.effortLabel(true), "cli-default");
        Assert.assertNull(LlmSpikeGui.ClaudeBridge.effortFor(false),
                "unset must mean 'omit the flag', keeping argv byte-identical to pre-effort runs");
    }

    /** The post-cap grace window is maxConfiguredTimeoutMs()+10s, and run_batch.py budgets for it
     *  with a HARDCODED GRACE_S = 1000 because it cannot call the Java side. That cross-language
     *  coupling is invisible at both ends, so it is asserted here: raise a model timeout so the
     *  grace passes 1000s and capped games start getting killed with an ask in flight, orphaning its jsonl row — the
     *  silent drop defect_analyzer.py exists to catch. If you need a longer timeout, raise GRACE_S
     *  in eval/run_batch.py in the same commit. */
    @Test
    public void graceFitsRunBatchBudget() {
        // maxDecisionMs(), not maxConfiguredTimeoutMs(): a decision that gets a null reply retries,
        // and every further attempt gets a FULL fresh timeout. Budgeting one timeout understated
        // the real worst case by 3x and let a capped game be killed with an ask in flight.
        long graceMs = LlmSpikeGui.ModelCfg.maxDecisionMs() + 10_000L;
        Assert.assertTrue(graceMs <= 1_000_000L,
                "post-cap grace " + graceMs + "ms exceeds the GRACE_S=1000s budgeted by "
                + "run_batch.py; raise GRACE_S there in the same commit, or lower the model timeout");
    }

    /** The grace must cover the retry loop, not just one attempt -- the arithmetic that was wrong. */
    @Test
    public void theGraceCoversEveryRetryAttemptNotJustTheFirst() {
        long one = LlmSpikeGui.ModelCfg.maxConfiguredTimeoutMs();
        long decision = LlmSpikeGui.ModelCfg.maxDecisionMs();
        int attempts = LlmSpikeGui.PlayerControllerLLM.BRIDGE_RETRY_BACKOFF_MS.length + 1;
        Assert.assertTrue(decision >= one * attempts,
                "a decision can spend " + attempts + " full timeouts (" + (one * attempts)
                        + "ms) but maxDecisionMs() reports only " + decision + "ms");
        Assert.assertTrue(decision > one,
                "maxDecisionMs() must exceed a single timeout or the retry loop is unbudgeted");
    }

    /** Every model spec resolves to ONE config. The Claude specs used to get their own 180s entry,
     *  because the first opus harness run timed out against the 90s catch-all; with the table uniform
     *  there is no separate entry left to fall through to, and the invariant worth pinning is that a
     *  spec with a matching key and one without get the same budget (the values themselves are pinned
     *  by LlmSpikeTest.modelCfg_everyRowGetsOneBudgetSoAnEvalComparesModelsUnderOneInfra). */
    @Test
    public void everyModelSpecResolvesToTheSameConfig() {
        LlmSpikeGui.ModelCfg dflt = LlmSpikeGui.ModelCfg.forModel("some-unknown-model");
        for (String spec : new String[] {"claude-opus-4-8", "opus", "claude-sonnet-5", "haiku",
                                         "anthropic/claude-opus-4.8", "deepseek/deepseek-v4.1-flash", "kimi"}) {
            LlmSpikeGui.ModelCfg c = LlmSpikeGui.ModelCfg.forModel(spec);
            Assert.assertEquals(c.timeoutMs, dflt.timeoutMs, spec + " has its own smart timeout");
            Assert.assertEquals(c.maxTokens, dflt.maxTokens, spec + " has its own smart cap");
        }
    }

    /**
     * model_cfg_sha must actually identify the config a run used.
     *
     * It used to hash the OPENROUTER_MODEL_CFG env string and return the constant "builtin" when
     * that was unset -- so for almost every run on disk it recorded nothing at all, and changing a
     * built-in timeout or token cap moved no sha and invalidated no artifact. That is the exact
     * failure this whole test class exists to prevent: something shaped like identity that
     * identifies nothing. It is not theoretical here -- an unbounded OpenRouter timeout emptied the
     * entire PZL LLM arm, and no artifact recorded which timeout was in force.
     */
    @Test
    public void modelCfgShaIdentifiesTheBuiltInTableNotJustTheEnvOverride() {
        String sha = LlmSpikeGui.modelCfgSha();
        Assert.assertNotEquals(sha, "builtin",
                "a sha that is a constant for every unoverridden run is not provenance");
        Assert.assertNotEquals(sha, "unknown", "hashing should not have failed");
        Assert.assertTrue(sha.matches("[0-9a-f]{12}"), "expected 12 hex chars, got: " + sha);
        Assert.assertEquals(sha, LlmSpikeGui.modelCfgSha(), "the sha must be stable within a run");
    }

    /**
     * Every ModelCfg field must reach the canonical string, or a change to that field would silently
     * leave the sha untouched -- which is how "builtin" managed to mean nothing for so long.
     * The literals here are read off the built-in table; a deliberate config change should move them
     * AND the sha, in the same commit.
     */
    @Test
    public void modelCfgCanonicalCoversEveryRowAndField() {
        String c = LlmSpikeGui.ModelCfg.canonical();
        Assert.assertTrue(c.startsWith("modelcfg.v1|"), "canonical form must carry its version: " + c);
        for (String key : new String[] {"opus", "sonnet", "haiku", "deepseek", "gpt", "kimi", "grok", "default"})
            Assert.assertTrue(c.contains(key + ":"), "row missing from canonical form: " + key);
        // Every row renders field-for-field. Since 2026-10-13 there is ONE uniform row, so the check
        // is that all seven fields reach the hash on a row that has a value in every slot (deepseek),
        // and that the row the run_batch.py grace budget is pinned against renders identically (opus).
        Assert.assertTrue(c.contains("deepseek:32000,300000,null,null,8192,120000,none|"),
                "deepseek row not rendered field-for-field: " + c);
        Assert.assertTrue(c.contains("opus:32000,300000,null,null,8192,120000,none|"),
                "claude row not rendered field-for-field: " + c);
    }

    /**
     * The egress refusal path must be reachable from a test, and it must actually fire.
     *
     * Until 2026-09-01 the gate read both values straight from the process environment, so no test
     * could drive the refusal branch -- it was a consent gate nobody had ever watched work. The
     * repo's rule is that any OpenRouter call ships the full game snapshot (board, hand, deck plan)
     * to a third party, that holding an API key is a configuration fact and NOT consent, and that
     * the flag is never set on the user's behalf. That rule is worth exactly as much as this test.
     */
    @Test
    public void egressWithoutConsentIsRefusedAndConsentIsTheOnlyThingThatLiftsIt() {
        String api = "https://openrouter.ai/api/v1";
        for (String noConsent : new String[] {null, "", "0", "true", "yes", "1 ", "01"}) {
            try {
                LlmSpikeGui.LLMBridgeFactory.requireEgressConsent(api, noConsent, "A/smart");
                Assert.fail("egress to " + api + " was permitted with consent=" + noConsent);
            } catch (IllegalStateException expected) {
                Assert.assertTrue(expected.getMessage().contains("OPENROUTER_ALLOW_EGRESS"),
                        "the refusal must name the flag that lifts it: " + expected.getMessage());
            }
        }
        // Exactly the literal "1" consents. Nothing else may.
        LlmSpikeGui.LLMBridgeFactory.requireEgressConsent(api, "1", "A/smart");
    }

    /** A loopback endpoint leaves the machine's network stack, so it needs no consent -- but an
     *  absent or unparseable endpoint means the REAL API and must fail closed. */
    @Test
    public void loopbackNeedsNoConsentButAnUnknownEndpointFailsClosed() {
        for (String local : new String[] {"http://localhost:8080/v1", "http://127.0.0.1:1234/v1",
                                          "http://[::1]:9/v1"})
            LlmSpikeGui.LLMBridgeFactory.requireEgressConsent(local, null, "A/smart"); // must not throw

        for (String remote : new String[] {null, "", "   ", "not a url", "https://evil.example/v1",
                                           "http://127.0.0.2:1/v1", "http://0.0.0.0:1/v1"}) {
            try {
                LlmSpikeGui.LLMBridgeFactory.requireEgressConsent(remote, null, "A/smart");
                Assert.fail("treated as loopback and let through without consent: " + remote);
            } catch (IllegalStateException expected) { /* fail closed, as required */ }
        }
    }

    /** The gate belongs to CONSTRUCTION, not to whoever remembered to call the factory. The
     *  package-private test constructor added for the deadline work must not be a way around it. */
    @Test
    public void theTestConstructorCannotReachARemoteEndpointWithoutConsent() {
        if ("1".equals(System.getenv("OPENROUTER_ALLOW_EGRESS"))) return; // operator has consented
        try {
            new LlmSpikeGui.OpenRouterBridge("A/smart", "some/model", null,
                                             "https://openrouter.ai/api/v1", 1000L);
            Assert.fail("built a bridge pointed at the real API with no egress consent");
        } catch (IllegalStateException expected) {
            Assert.assertTrue(expected.getMessage().contains("OPENROUTER_ALLOW_EGRESS"),
                    "expected the egress refusal, got: " + expected.getMessage());
        }
    }

    /** docs/harness/MODEL-LIMITS.md: under the faithful profile nothing logged is dropped from the
     *  history. 300 events since the last decision (past the old 40) are all verbatim, and every
     *  earlier turn, however old, keeps its low-signal events and more than 12 entries (the old digest
     *  kept 12 a turn, and only high-signal events for turns older than three). */
    @Test
    public void theFaithfulHistoryIsWhole() {
        ContextProfile p = ContextProfile.V4_FAITHFUL;
        java.util.List<GameHistory.Ev> evs = new java.util.ArrayList<>();
        int id = 0;
        for (int turn = 1; turn <= 30; turn++)
            for (int k = 0; k < 20; k++)
                evs.add(new GameHistory.Ev(++id, turn, k % 2 == 0 ? forge.game.GameLogEntryType.STACK_ADD
                        : forge.game.GameLogEntryType.ZONE_CHANGE, "T" + turn + " event " + k));
        for (int k = 0; k < 300; k++)
            evs.add(new GameHistory.Ev(++id, 31, forge.game.GameLogEntryType.ZONE_CHANGE, "recent event " + k));
        String text = GameHistory.renderEvents(evs, p, 600).text;
        for (GameHistory.Ev e : evs) Assert.assertTrue(text.contains(e.msg), "dropped: " + e.msg);
        Assert.assertFalse(text.contains("more)"), "a digest line was cut:\n" + text);
        Assert.assertFalse(text.contains("no high-signal events"), text);
    }

    /** The two-tier arm's fast tier keeps reasoning off but its answer is not cut at 1024 tokens
     *  (2 of 170 fast calls truncated in runs/compare/two-tier). */
    @Test
    public void theFastTierAnswerIsNotCutShort() {
        LlmSpikeGui.ModelCfg c = LlmSpikeGui.ModelCfg.forModel("deepseek/deepseek-v4.1-flash");
        Assert.assertEquals(c.fastMaxTokens, 8192);
        Assert.assertEquals(c.fastTimeoutMs, 120000L);
        Assert.assertEquals(c.fastReasoningEffort, "none", "the two-tier arm's fast tier is the no-reasoning tier");
    }

    /** The per-game call budgets, the per-turn play cap and the one-chat window are loop guards far
     *  above any recorded game (at most 137 calls a game, 15 play decisions a turn). */
    @Test
    public void theCallBudgetsAndTheTurnCapAreLoopGuards() {
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.smartBudget(), 10000);
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.fastBudget(), 10000);
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.MAX_PLAY_DECISIONS_PER_TURN_FAITHFUL, 500);
        Assert.assertEquals(ChatSessionBridge.MAX_PAIRS, 10000);
    }
}
