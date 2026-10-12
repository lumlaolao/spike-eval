package forge.spike;

import java.io.File;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import forge.GuiDesktop;
import forge.game.Game;
import forge.game.GameEndReason;
import forge.game.GameRules;
import forge.game.GameType;
import forge.game.Match;
import forge.game.phase.PhaseHandler;
import forge.game.player.Player;
import forge.game.player.RegisteredPlayer;
import forge.game.zone.ZoneType;
import forge.gui.GuiBase;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;
import forge.util.MyRandom;

/**
 * Milestone D — replay-and-rollout harness (eval/EVAL-DESIGN.md §3.1). Delegated to from
 * {@link LlmSpikeHeadless#main} when {@code FORK_MODE} is set; the launcher script
 * (run-llm-headless.sh) and its classpath are untouched.
 *
 * <p>A game piloted only by {@code floor|random|ai} controllers is bit-reproducible given
 * (seed, controllers, decks, engine build) — the seed-42 floor canary (CLAUDE.md) is the
 * existing proof of this. {@code FORK_MODE} refuses {@code llm} controllers for exactly this
 * reason: an LLM seat's choices depend on a live model call and cannot be replayed.
 *
 * <p>Three modes ({@code FORK_MODE=check|enumerate|rollout}):
 * <ul>
 *   <li><b>check</b> — play the game normally (decisions logged via the existing EvalLog path,
 *       same as {@link LlmSpikeHeadless}) and, if {@code FORK_SOURCE} names a prior game's jsonl,
 *       diff the replayed decision stream against it. {@code REPLAY-OK n=<count>} / exit 0 on
 *       match; {@code REPLAY-DRIFT at index i: <expected> vs <got>} / exit 4 on the first
 *       mismatch; refuses (exit 2) up front if the source's provenance (seed, swap, controllers,
 *       decks — strict fields) disagrees with the current environment, or if BOTH sides have a
 *       recorded engine/overlay sha and they differ. A bare launch never sets ENGINE_SHA/OVERLAY_SHA,
 *       so an unset current sha only WARNS ("drift on this dimension can't be verified") rather than
 *       refusing or silently passing — see {@link #evaluateProvenance}. A drifted strict field is a
 *       wrong answer key, so that check runs BEFORE the (expensive) game replay, not after.</li>
 *   <li><b>enumerate</b> — replay with the normal deterministic controllers until
 *       {@code FORK_DECISION_INDEX} (0-based, over the same merged/emission-order decision stream
 *       {@code check} compares), emit one additive {@code t:"fork_enumerate"} jsonl line
 *       describing that decision (type/seat/turn/phase/n_options/options/state snapshot), answer
 *       it exactly as the deterministic controller already had (unchanged), then terminate the
 *       game so the JVM exits promptly.</li>
 *   <li><b>rollout</b> — the EVAL-DESIGN §3.1(b) answer-key generator. Replays the deterministic
 *       prefix from turn 1 (a fresh Match per rollout — replay-from-index, never a mid-stack
 *       GameCopier; that is the v1-mandated mechanism), installs {@link LlmSpikeGui#FORK_FORCER} so
 *       the decision at {@code FORK_DECISION_INDEX} takes option {@code o} instead of the
 *       controller's own pick, reseeds the shared {@code MyRandom} stream at that exact moment with
 *       {@code rollout_seed = mix64(seed_base ^ (o*C1) ^ (j*C2))}, re-deals the hidden library
 *       order from that stream ({@code FORK_ROLLOUT_DETERMINIZE=library}, the default — WITHOUT it
 *       the continuation provably does not diverge and every arm scores 0/n or n/n; see
 *       {@link #determinizeHiddenZones}), and lets the game run to
 *       completion. One {@code t:"fork_rollout"} jsonl row per (option, j) plus one
 *       {@code t:"fork_rollout_summary"} row land in {@code FORK_ROLLOUT_OUT}. v1 forces ONLY
 *       {@code play} and {@code mulligan} (index-encoded types); count-encoded types (attackers,
 *       blockers, discard, ...) log a subset SIZE, not an index — there is no per-option object to
 *       force, so they are refused (exit 2; the old exit 3 "not implemented" is RETIRED).
 *       Continuation policy note: plain {@code ai} seats use untouched {@code LobbyPlayerAi} and
 *       never reach the forcer or the decision log — the loggable+forceable ai arm is
 *       {@code ai-log} (hence the driver-side ROLLOUT_CONTINUATION constant "ai-log/ai-log").</li>
 * </ul>
 */
public final class LlmSpikeFork {
    private LlmSpikeFork() {}

    /** Entry point. Every path ends in {@code System.exit(code)}; nothing here returns to a caller
     *  that expects further work (mirrors {@link LlmSpikeHeadless#main}'s own explicit-exit
     *  discipline — bridges/subprocess pipes aside, an explicit exit keeps behavior identical
     *  across modes instead of depending on daemon-thread cleanup).
     *  <p>Exit-code contract: 0 = OK, 2 = refused (bad args / provenance mismatch / decision type
     *  not forceable in v1), 3 = RETIRED (was "rollout not implemented"; never returned now that
     *  rollout is real — non-forceable targets refuse with 2 like every other refusal),
     *  4 = REPLAY-DRIFT, 5 = REPLAY-FAILED (worker error mid-game / leaked game thread). Any
     *  exception escaping this method (e.g. {@code FModel} init failure, unreadable
     *  {@code FORK_SOURCE}) propagates to {@code main} and exits with the JVM default
     *  {@code 1} — callers switching on exit codes must treat 1 as "infrastructure failure,
     *  result unknown", never as a replay verdict. */
    static void run(String mode) throws Exception {
        switch (mode) {
            case "check":     runCheck();     break;
            case "enumerate": runEnumerate(); break;
            case "rollout":   runRollout();   break;
            default:
                System.err.println("[FORK] unknown FORK_MODE '" + mode + "' (expected check|enumerate|rollout)");
                System.exit(2);
        }
    }

    // ==================== shared arg validation ====================

    /** Bit-reproducible arms. "ai-log" (PlayerControllerAiLogging) was added for rollout: it is the
     *  only ai arm that both LOGS decision rows (through emitDecisionRow, so fork indices exist) and
     *  can be FORCED (LlmSpikeGui.forkForce is wired into its play/mulligan overrides); plain "ai"
     *  is untouched LobbyPlayerAi and does neither. ai-log routes every decision through the same
     *  Forge AI as "ai", so it is equally seed-deterministic. */
    private static final Set<String> DETERMINISTIC_CONTROLLERS = Set.of("floor", "random", "ai", "ai-log");

    /** Refuses (exit 2) unless CONTROLLER_A/B are both set to a bit-reproducible arm and EVAL_OUT
     *  is set (every fork mode needs the decision jsonl — even the enumerate/rollout stub path
     *  validates it up front rather than discovering the gap mid-game). CONTROLLER_A/B default to
     *  "llm" when unset (see LlmSpikeGui.buildPlayers), so an unset env is also a refusal, not a
     *  silent pass-through. */
    private static void requireDeterministicControllers() {
        String ctlA = System.getenv("CONTROLLER_A");
        String ctlB = System.getenv("CONTROLLER_B");
        String evalOut = System.getenv("EVAL_OUT");
        List<String> problems = new ArrayList<>();
        if (ctlA == null || !DETERMINISTIC_CONTROLLERS.contains(ctlA))
            problems.add("CONTROLLER_A must be one of floor|random|ai|ai-log (got '" + ctlA + "')");
        if (ctlB == null || !DETERMINISTIC_CONTROLLERS.contains(ctlB))
            problems.add("CONTROLLER_B must be one of floor|random|ai|ai-log (got '" + ctlB + "')");
        if (evalOut == null || evalOut.isEmpty())
            problems.add("EVAL_OUT must be set (fork modes read/write the decision jsonl)");
        if (!problems.isEmpty()) {
            System.err.println("[FORK] refusing: an llm controller cannot be replayed deterministically "
                    + "(FORK_MODE requires floor|random|ai|ai-log — see CLAUDE.md validation ladder).");
            for (String p : problems) System.err.println("  - " + p);
            System.exit(2);
        }
    }

    /** Parses FORK_DECISION_INDEX (required for enumerate/rollout). Refuses (exit 2) if missing or
     *  not a non-negative integer. */
    private static int requireDecisionIndex() {
        String v = System.getenv("FORK_DECISION_INDEX");
        try {
            int idx = Integer.parseInt(v.trim());
            if (idx < 0) throw new NumberFormatException("negative");
            return idx;
        } catch (Exception e) {
            System.err.println("[FORK] FORK_DECISION_INDEX must be set to a non-negative integer (got '" + v + "')");
            System.exit(2);
            return -1; // unreachable: System.exit terminates the JVM
        }
    }

    // ==================== check mode ====================

    private static void runCheck() throws Exception {
        requireDeterministicControllers();

        String sourcePath = System.getenv("FORK_SOURCE");
        File sourceFile = null;
        String sourceGameLine = null;
        if (sourcePath != null && !sourcePath.isEmpty()) {
            sourceFile = new File(sourcePath);
            if (!sourceFile.isFile()) {
                System.err.println("[FORK] FORK_SOURCE not found: " + sourcePath);
                System.exit(2);
            }
            List<String> lines = Files.readAllLines(sourceFile.toPath());
            for (String l : lines) if (l.contains("\"t\":\"game\"")) { sourceGameLine = l; break; }
            if (sourceGameLine == null) {
                System.err.println("[FORK] FORK_SOURCE has no game-summary line (t:\"game\"): " + sourcePath);
                System.exit(2);
            }
            // Provenance gate BEFORE spending a full replay: a drifted replay is a wrong answer key.
            refuseOnProvenanceMismatch(sourceGameLine);
        }

        GameRunResult r = playDeterministicGame(null);
        if (r.workerError != null) {
            // The worker thread never completed a real game -- taking the success path here would
            // print a false REPLAY-OK for a game that crashed. Fail loudly instead.
            System.out.println("[FORK] REPLAY-FAILED: game thread threw " + r.workerError);
            System.exit(5);
        }
        LlmSpikeGui.writeGameSummary(r.game, r.seed, r.durMs, r.capMs, r.capped);

        if (sourceFile == null) {
            // Nothing to diff against -- the game ran to completion under deterministic controllers,
            // which is itself the only thing "check" without FORK_SOURCE can assert. r.decisionCount
            // is the REAL emission-order count (FORK_DECISION_SEQ now increments unconditionally in
            // emitDecisionRow -- see LlmSpikeGui) so a zero-decision floor/random game is visibly
            // anomalous instead of silently reading as success.
            System.out.println("REPLAY-OK n=" + r.decisionCount);
            System.exit(0);
        }

        List<String> ourLines = Files.readAllLines(r.jsonlFile.toPath());
        List<String> sourceLines = Files.readAllLines(sourceFile.toPath());
        List<String[]> ours = decisionRows(ourLines);
        List<String[]> theirs = decisionRows(sourceLines);

        int n = Math.min(ours.size(), theirs.size());
        for (int i = 0; i < n; i++) {
            if (!rowsMatch(ours.get(i), theirs.get(i))) drift(i, describeRow(theirs.get(i)), describeRow(ours.get(i)));
        }
        if (ours.size() != theirs.size()) {
            String expected = n < theirs.size() ? describeRow(theirs.get(n)) : "(stream ended)";
            String got = n < ours.size() ? describeRow(ours.get(n)) : "(stream ended)";
            drift(n, expected, got);
        }

        String ourGameLine = null;
        for (String l : ourLines) if (l.contains("\"t\":\"game\"")) ourGameLine = l;
        String wExp = LlmSpikeGui.extractJsonString(sourceGameLine, "\"winner\":\"");
        String wGot = LlmSpikeGui.extractJsonString(ourGameLine, "\"winner\":\"");
        String tExp = numField(sourceGameLine, "turns");
        String tGot = numField(ourGameLine, "turns");
        if (!Objects.equals(wExp, wGot) || !Objects.equals(tExp, tGot)) {
            drift(n, "winner=" + wExp + " turns=" + tExp, "winner=" + wGot + " turns=" + tGot);
        }

        System.out.println("REPLAY-OK n=" + n);
        System.exit(0);
    }

    private static void drift(int i, String expected, String got) {
        System.out.println("REPLAY-DRIFT at index " + i + ": " + expected + " vs " + got);
        System.exit(4);
    }

    /** Pure result of {@link #evaluateProvenance}: the strict-field mismatches that must refuse the
     *  replay, and the sha-dimension warnings that must be printed but must NOT refuse it. Kept
     *  exit-free so the decision logic is directly unit-testable (LlmSpikeTest) -- the caller
     *  ({@link #refuseOnProvenanceMismatch}) is the thin System.exit-performing wrapper. */
    static final class ProvenanceVerdict {
        final List<String> mismatches;
        final List<String> shaWarnings;
        ProvenanceVerdict(List<String> mismatches, List<String> shaWarnings) {
            this.mismatches = mismatches; this.shaWarnings = shaWarnings;
        }
        boolean shouldRefuse() { return !mismatches.isEmpty(); }
    }

    /** Compares the source game line's provenance against an ENV snapshot, using the exact same
     *  env-var + default resolution LlmSpikeGui.writeGameSummary/buildPlayers use, so "no
     *  mismatches" really does mean "would produce the identical replay."
     *
     * <p>seed/swap/controllers/decks are strict: any disagreement is a mismatch (refuse). The two
     * sha fields (engine_sha/overlay_sha) are handled differently: a bare launch never sets
     * ENGINE_SHA/OVERLAY_SHA, so comparing an unset CURRENT value against a real recorded sha (e.g.
     * one stamped by run_batch.py) using an "unknown" default used to either silently pass (both
     * sides defaulted to "unknown", a no-op check) or, worse, refuse a legitimate replay. Instead:
     * an unset/blank/"unknown" CURRENT sha is a warning only (that dimension's drift can't be
     * verified, but it does not block the replay); both sides present and different is still a
     * mismatch, exactly as before. */
    static ProvenanceVerdict evaluateProvenance(String sourceGameLine, Map<String, String> env) {
        List<String> mismatches = new ArrayList<>();
        List<String> shaWarnings = new ArrayList<>();
        checkShaField(shaWarnings, mismatches, "engine_sha", "ENGINE_SHA",
                LlmSpikeGui.extractJsonString(sourceGameLine, "\"engine_sha\":\""), env.get("ENGINE_SHA"));
        checkShaField(shaWarnings, mismatches, "overlay_sha", "OVERLAY_SHA",
                LlmSpikeGui.extractJsonString(sourceGameLine, "\"overlay_sha\":\""), env.get("OVERLAY_SHA"));
        addIfDiffer(mismatches, "seed", numField(sourceGameLine, "seed"), currentSeedString(env));
        addIfDiffer(mismatches, "swap",
                String.valueOf(boolField(sourceGameLine, "swap")),
                String.valueOf("1".equals(env.get("SWAP_SEATS"))));
        addIfDiffer(mismatches, "controller_a",
                LlmSpikeGui.extractJsonString(sourceGameLine, "\"controller_a\":\""),
                env.getOrDefault("CONTROLLER_A", "llm"));
        addIfDiffer(mismatches, "controller_b",
                LlmSpikeGui.extractJsonString(sourceGameLine, "\"controller_b\":\""),
                env.getOrDefault("CONTROLLER_B", "llm"));
        addIfDiffer(mismatches, "deck_a",
                LlmSpikeGui.extractJsonString(sourceGameLine, "\"deck_a\":\""),
                env.getOrDefault("DECK_A_NAME", "Grixis Reanimator"));
        addIfDiffer(mismatches, "deck_b",
                LlmSpikeGui.extractJsonString(sourceGameLine, "\"deck_b\":\""),
                env.getOrDefault("DECK_B_NAME", "Mardu Energy"));
        return new ProvenanceVerdict(mismatches, shaWarnings);
    }

    /** Refuses (exit 2) unless the source's provenance agrees with the CURRENT environment
     *  (System.getenv()) per {@link #evaluateProvenance}'s rules; prints any sha-dimension
     *  warnings unconditionally (they never refuse). */
    private static void refuseOnProvenanceMismatch(String sourceGameLine) {
        ProvenanceVerdict v = evaluateProvenance(sourceGameLine, System.getenv());
        for (String w : v.shaWarnings) System.err.println("[FORK] WARNING: " + w);
        if (v.shouldRefuse()) {
            System.err.println("[FORK] REFUSE: FORK_SOURCE provenance disagrees with the current "
                    + "environment -- a drifted replay is a wrong answer key:");
            for (String m : v.mismatches) System.err.println("  " + m);
            System.exit(2);
        }
    }

    private static void checkShaField(List<String> shaWarnings, List<String> mismatches,
            String jsonField, String envVarName, String sourceValue, String currentValue) {
        // "unknown" on EITHER side means that side never had the sha stamped (bare launches
        // don't set ENGINE_SHA/OVERLAY_SHA; writeGameSummary records the literal "unknown").
        // A one-sided unknown is unverifiable, not a mismatch -- refusing here would spuriously
        // reject every bare-launch source replayed under a run_batch-stamped environment.
        boolean sourceUnknown = sourceValue == null || sourceValue.isEmpty() || "unknown".equals(sourceValue);
        boolean currentUnknown = currentValue == null || currentValue.isEmpty() || "unknown".equals(currentValue);
        if (sourceUnknown || currentUnknown) {
            shaWarnings.add(jsonField + " is unverifiable for this replay -- "
                    + (currentUnknown ? envVarName + " is not set in the current environment"
                                      : "the source recorded no " + jsonField)
                    + " (source=" + sourceValue + " env=" + currentValue + ")");
            return;
        }
        addIfDiffer(mismatches, jsonField, sourceValue, currentValue);
    }

    private static void addIfDiffer(List<String> out, String field, String expected, String actual) {
        if (!Objects.equals(expected, actual)) out.add(field + ": source=" + expected + " env=" + actual);
    }

    /** GAME_SEED as writeGameSummary's own {@code seed} field would render it, or null if unset
     *  (an unset GAME_SEED draws a fresh random seed every run and can never legitimately match a
     *  fixed source seed -- that mismatch is a correct refusal, not a bug). */
    static String currentSeedString(Map<String, String> env) {
        String v = env.get("GAME_SEED");
        if (v == null || v.isEmpty()) return null;
        try { return String.valueOf(Long.parseLong(v.trim())); } catch (Exception e) { return v.trim(); }
    }

    /** [type, seat, n_options] for every {@code t:"decision"} line, in file order. File order IS
     *  emission order: EvalLog.write is synchronized and both seats share one PrintWriter.
     *  Package-visible (not private) so LlmSpikeTest can exercise it directly against synthetic
     *  jsonl text. */
    static List<String[]> decisionRows(List<String> lines) {
        List<String[]> rows = new ArrayList<>();
        for (String l : lines) {
            if (!l.contains("\"t\":\"decision\"")) continue;
            rows.add(new String[] {
                    LlmSpikeGui.extractJsonString(l, "\"type\":\""),
                    LlmSpikeGui.extractJsonString(l, "\"seat\":\""),
                    numField(l, "n_options"),
            });
        }
        return rows;
    }

    /** Package-visible (not private) so LlmSpikeTest can exercise match/mismatch directly. */
    static boolean rowsMatch(String[] a, String[] b) {
        return Objects.equals(a[0], b[0]) && Objects.equals(a[1], b[1]) && Objects.equals(a[2], b[2]);
    }

    private static String describeRow(String[] r) {
        return "type=" + r[0] + " seat=" + r[1] + " n_options=" + r[2];
    }

    // ==================== enumerate mode ====================

    private static void runEnumerate() throws Exception {
        requireDeterministicControllers();
        int targetIndex = requireDecisionIndex();

        AtomicBoolean hit = new AtomicBoolean(false);
        String[] hitDesc = new String[1];

        LlmSpikeGui.ForkHook hook = (idx, pl, seat, type, nOptions, choice, options) -> {
            if (idx != targetIndex || !hit.compareAndSet(false, true)) return;
            try {
                String snapshot;
                try {
                    // Empty intel / no turn plan: exactly the convention LlmSpikeGui's own
                    // ai-teacher seat (PlayerControllerAiLogging.snap()) uses to reconstruct "the
                    // prompt the LLM seat would have sent" for a seat that never accumulated
                    // either -- floor/random are in the same position (they never call ask()).
                    snapshot = LlmSpikeGui.PlayerControllerLLM.buildSnapshot(
                            pl, seat, Collections.<String>emptyList(), "", -1);
                } catch (Exception e) {
                    snapshot = "== (state unavailable: " + e + ") ==";
                }
                PhaseHandler ph = pl.getGame().getPhaseHandler();
                StringBuilder sb = new StringBuilder(256);
                sb.append("{\"t\":\"fork_enumerate\",\"idx\":").append(idx)
                        .append(",\"seat\":\"").append(LlmSpikeGui.jsonEscape(seat)).append('"')
                        .append(",\"turn\":").append(ph.getTurn())
                        .append(",\"phase\":\"").append(ph.getPhase()).append('"')
                        .append(",\"type\":\"").append(LlmSpikeGui.jsonEscape(type)).append('"')
                        .append(",\"n_options\":").append(nOptions)
                        .append(",\"choice\":").append(choice)
                        .append(",\"options\":[");
                if (options != null) {
                    for (int i = 0; i < options.size(); i++) {
                        if (i > 0) sb.append(',');
                        sb.append('"').append(LlmSpikeGui.jsonEscape(options.get(i))).append('"');
                    }
                }
                sb.append("],\"snapshot\":\"").append(LlmSpikeGui.jsonEscape(snapshot)).append("\"}");
                LlmSpikeGui.EvalLog.write(sb.toString());
                hitDesc[0] = "type=" + type + " seat=" + seat + " n_options=" + nOptions;
            } catch (Throwable t) {
                System.out.println("[FORK] enumerate hook failed: " + t);
            } finally {
                // The decision already resolved deterministically before this hook ran (evalDec is
                // called with `choice` already computed -- see LlmSpikeGui's play/attackers/etc.
                // decision methods); answering "as usual" needs no action from us. Terminating here
                // mirrors LlmSpikeHeadless's wall-clock-cap shutdown: flip game-over and let
                // PhaseHandler's `while (!game.isGameOver())` loop unwind on its own, no
                // GameCopier/mid-stack surgery needed.
                try { pl.getGame().setGameOver(GameEndReason.Draw); } catch (Throwable ignore) { }
            }
        };

        GameRunResult r = playDeterministicGame(hook);
        if (r.workerError != null) {
            // The worker thread never completed a real game -- don't write a game-summary row for
            // it (that row would misleadingly look like a truncated-but-otherwise-normal game).
            System.out.println("[FORK] FORK-FAILED: game thread threw " + r.workerError);
            System.exit(5);
        }
        if (!hit.get()) {
            // The index was never reached (game ended/capped first). Exit WITHOUT writing a game
            // summary row: this is an error path, and a "winner=invalid" row here would be a
            // spurious/misleading jsonl entry for a run that never produced a real fork_enumerate
            // result (jsonl stays additive-only for genuine outcomes, not error paths).
            System.err.println("[FORK] FORK_DECISION_INDEX=" + targetIndex + " was never reached "
                    + "(the game logged only " + r.decisionCount + " decision(s) before ending/capping)");
            System.exit(2);
        }

        // Always "invalid": this game was deliberately truncated, never a real outcome. Written only
        // now that we know the run actually hit the target index -- see the reordering note above.
        LlmSpikeGui.writeGameSummary(r.game, r.seed, r.durMs, r.capMs, true);
        System.out.println("[FORK] enumerate OK: wrote fork_enumerate at index " + targetIndex + " (" + hitDesc[0] + ")");
        System.exit(0);
    }

    // ==================== rollout mode ====================

    // Two large odd constants for the SplitMix64-style rollout seed mix (see rolloutSeed).
    static final long ROLLOUT_C1 = 0x9E3779B97F4A7C15L;
    static final long ROLLOUT_C2 = 0xC2B2AE3D27D4EB4FL;

    /** SplitMix64 finalizer. Pure; unit-tested in LlmSpikeForkTest. */
    static long mix64(long z) {
        z = (z ^ (z >>> 30)) * 0xBF58476D1CE4E5B9L;
        z = (z ^ (z >>> 27)) * 0x94D049BB133111EBL;
        return z ^ (z >>> 31);
    }

    /** {@code rollout_seed = mix64(base ^ (option*C1) ^ (j*C2))} — the exact function the Python
     *  driver records as {@code seed_fn: "mix64(base_seed ^ (i*C1) ^ (j*C2))"}. */
    static long rolloutSeed(long base, int option, int j) {
        return mix64(base ^ (option * ROLLOUT_C1) ^ (j * ROLLOUT_C2));
    }

    /**
     * Re-randomize the HIDDEN information at the fork point: every player's library order.
     *
     * <p>WHY THIS EXISTS — the measured defect it repairs. Reseeding {@link MyRandom} alone was
     * assumed to make the continuation diverge across j ("j-variance enters through direct
     * MyRandom consumers — Forge-AI evaluators via percentTrue"). Measured on the real
     * 16-game corpus, that assumption is FALSE: across 6 positions and 18 option-arms, at every
     * n tried, every arm returned exactly 0/n or n/n and never an intermediate value. All n
     * replays of an option played the identical line, so n=16 bought exactly n=1 of information
     * and every WP was 0.0 or 1.0. The reason is structural, not statistical: a mid-game reseed
     * cannot re-deal a library whose order was fixed at game start, and the Forge AI's line is
     * effectively a deterministic function of the board it sees. With the draws already fixed,
     * there was nothing left for the new stream to vary.
     *
     * <p>WHAT IT CHANGES, PRECISELY. Only the ORDER of cards in each library — a zone whose
     * order is hidden from both players. Zone membership, hands, battlefield, graveyards, exile,
     * life, counters and the stack are untouched, so the OBSERVABLE position every option is
     * evaluated from is bit-identical to the probe's. {@link forge.game.zone.Zone#shuffle} is the
     * engine's own routine and fires no shuffle triggers or state-change events, so no ability
     * observes this (a {@code Player.shuffle(sa)} call WOULD have — hence the zone-level call).
     *
     * <p>WHAT IT MEANS FOR THE NUMBER. The resulting WP is the win probability conditional on the
     * observable state, marginalized over library orders — the imperfect-information quantity the
     * eval actually wants, not the single-deal quantity {@code single_deal_hidden_info} in
     * EVAL-DESIGN §9.3 warned about. Two honest caveats, both recorded on every row via
     * {@code determinize}: (1) a player who legitimately KNEW its top card (post-Brainstorm,
     * post-scry, Sensei's-style effects) loses that knowledge, so positions whose value rests on
     * a known top card are measured slightly pessimistically; (2) an opponent's unknown HAND is
     * not re-dealt, only libraries — this is partial determinization, not full ISMCTS.
     *
     * <p>Draws come from the freshly-seeded {@code MyRandom} stream installed immediately before,
     * so the shuffle is a deterministic function of {@code rollout_seed} and replays exactly.
     * Set {@code FORK_ROLLOUT_DETERMINIZE=none} to restore the old behavior — kept only so the
     * degenerate 0/n-or-n/n result above stays reproducible as evidence.
     *
     * @return number of libraries shuffled (0 when disabled or the game is unavailable)
     */
    static int determinizeHiddenZones(Player decidingPlayer, boolean enabled) {
        if (!enabled || decidingPlayer == null) return 0;
        Game g = decidingPlayer.getGame();
        if (g == null) return 0;
        int n = 0;
        for (Player p : g.getPlayers()) {
            forge.game.zone.Zone lib = p.getZone(ZoneType.Library);
            if (lib == null || lib.size() < 2) continue;
            lib.shuffle();
            n++;
        }
        return n;
    }

    /** {@code FORK_ROLLOUT_DETERMINIZE=library|none} (default {@code library}). Any other value is
     *  refused by the caller rather than silently treated as one of them — a typo that silently
     *  disabled determinization would reproduce the zero-variance defect while LOOKING fixed. */
    static Boolean parseDeterminize(String raw) {
        String v = (raw == null || raw.trim().isEmpty()) ? "library" : raw.trim().toLowerCase();
        if ("library".equals(v)) return Boolean.TRUE;
        if ("none".equals(v)) return Boolean.FALSE;
        return null;
    }

    /** v1 forceable gate: only index-encoded types whose forced index maps unambiguously to an
     *  engine object. Count-encoded types (attackers/blockers/discard/...) log a subset SIZE, not
     *  an index — there is nothing to force per option. */
    static boolean isForceableType(String type) {
        return "play".equals(type) || "mulligan".equals(type);
    }

    /** The {@code choice} value the decision row at the forced index must log if the forcer was
     *  honoured: play logs the 0-based option index, except the trailing PASS slot (and any
     *  negative force) which logs -1; mulligan logs 0 (KEEP, also for a -1 decline force) or 1. */
    static int expectedLoggedChoice(String type, int option, int nOptions) {
        if ("mulligan".equals(type)) return option == 1 ? 1 : 0;
        return (option < 0 || option >= nOptions - 1) ? -1 : option;
    }

    /** Tally slot for one finished rollout: 0=win (the decision seat won), 1=loss, 2=draw,
     *  3=invalid. A rollout whose force was NOT honoured is always invalid, never a loss. */
    static int outcomeSlot(String winner, boolean seatIsA, boolean forcedOk) {
        if (!forcedOk || winner == null || "invalid".equals(winner)) return 3;
        if ("draw".equals(winner)) return 2;
        if (!"A".equals(winner) && !"B".equals(winner)) return 3;
        return "A".equals(winner) == seatIsA ? 0 : 1;
    }

    /** The summary row's per_option array. {@code targets} lists the option indices actually rolled
     *  (FORK_ROLLOUT_OPTION may restrict to one). Pure; unit-tested in LlmSpikeForkTest. */
    static String perOptionJson(int[][] tally, long[][] seeds, List<Integer> targets) {
        StringBuilder sb = new StringBuilder("[");
        boolean first = true;
        for (int o : targets) {
            if (!first) sb.append(',');
            first = false;
            sb.append("{\"option\":").append(o)
                    .append(",\"wins\":").append(tally[o][0])
                    .append(",\"losses\":").append(tally[o][1])
                    .append(",\"draws\":").append(tally[o][2])
                    .append(",\"invalid\":").append(tally[o][3])
                    .append(",\"seeds\":[");
            for (int j = 0; j < seeds[o].length; j++) {
                if (j > 0) sb.append(',');
                sb.append(seeds[o][j]);
            }
            sb.append("]}");
        }
        return sb.append(']').toString();
    }

    /** What the probe game captured at FORK_DECISION_INDEX, plus the pre-index decision-row prefix
     *  every rollout's own prefix is diffed against (a perturbed prefix means every WP is wrong —
     *  the rollout correctness gate, enforced per rollout, not just in the validation ladder). */
    static final class ProbeResult {
        String type; String seat; String phase;
        int turn; int nOptions; int choice;
        List<String> options;
        final List<String[]> prefix = new ArrayList<>();
    }

    /**
     * FORK_MODE=rollout. Env contract (ALL validated before any engine work):
     * FORK_DECISION_INDEX=&lt;i&gt;, FORK_ROLLOUT_OPTION=&lt;o or 'all' (default)&gt;,
     * FORK_ROLLOUT_N=&lt;j count, default 16&gt;, FORK_ROLLOUT_SEED_BASE=&lt;long, default GAME_SEED&gt;,
     * FORK_ROLLOUT_OUT=&lt;jsonl path or dir; defaults to EVAL_OUT&gt;, plus the usual
     * EVAL_OUT/CONTROLLER_A/B/GAME_SEED/DECK_A/B/DECK_A_NAME/B_NAME.
     *
     * <p>MyRandom's contract (READ from forge-core MyRandom.java, not assumed): a plain static
     * mutable {@code Random} field behind getRandom()/setRandom(); every consumer fetches the
     * current instance at call time and none preserves un-consumed stream state, so swapping in a
     * fresh {@code new Random(rollout_seed)} at the MOMENT of the forced decision is sound — the
     * pre-decision prefix's draws already happened and are untouched, and the whole continuation
     * draws from the fresh stream. Documented caveat: components that derived their OWN Random
     * from MyRandom at construction time (PlayerControllerLLM's random-arm {@code rnd}) keep their
     * construction-time stream.
     *
     * <p>CORRECTION (measured 2026-07-28, supersedes this method's original claim). It used to say
     * j-variance "enters through direct MyRandom consumers (Forge-AI evaluators via percentTrue),
     * which is why the recommended continuation is ai-log/ai-log". That is FALSE as written: with
     * the reseed alone, all n replays of an option played the identical line and every arm scored
     * exactly 0/n or n/n. A mid-game reseed cannot re-deal a library fixed at game start. Real
     * variance now comes from {@link #determinizeHiddenZones}, which re-deals the hidden library
     * order at the fork point ({@code FORK_ROLLOUT_DETERMINIZE=library}, the default); the reseed
     * is retained because the shuffle draws from it. See that method's javadoc for what the
     * resulting WP does and does not mean.
     *
     * <p>Exit codes: 0 OK (rows emitted; individual failed rollouts are counted invalid, never
     * losses), 2 refused (bad args, non-forceable type — the old exit 3 "not implemented" is
     * RETIRED and never returned), 4 every attempted rollout prefix-drifted (replay unsound),
     * 5 probe/game infrastructure failure or a leaked game thread (results untrustworthy).
     */
    private static void runRollout() throws Exception {
        requireDeterministicControllers();
        final int targetIdx = requireDecisionIndex();

        String seedEnv = System.getenv("GAME_SEED");
        long gameSeed = 0;
        boolean seedOk = false;
        try {
            gameSeed = Long.parseLong(seedEnv.trim());
            seedOk = true;
        } catch (Exception ignore) { }
        if (!seedOk) {
            System.err.println("[FORK] rollout requires GAME_SEED (an unset seed draws a fresh random "
                    + "prefix and can never re-reach the source position); got '" + seedEnv + "'");
            System.exit(2);
        }

        int nPerOption = 16;
        String nEnv = System.getenv("FORK_ROLLOUT_N");
        if (nEnv != null && !nEnv.isEmpty()) {
            try {
                nPerOption = Integer.parseInt(nEnv.trim());
                if (nPerOption <= 0) throw new NumberFormatException("non-positive");
            } catch (Exception e) {
                System.err.println("[FORK] FORK_ROLLOUT_N must be a positive integer (got '" + nEnv + "')");
                System.exit(2);
            }
        }

        long seedBase = gameSeed;
        String sbEnv = System.getenv("FORK_ROLLOUT_SEED_BASE");
        if (sbEnv != null && !sbEnv.isEmpty()) {
            try { seedBase = Long.parseLong(sbEnv.trim()); } catch (Exception e) {
                System.err.println("[FORK] FORK_ROLLOUT_SEED_BASE must be a long (got '" + sbEnv + "')");
                System.exit(2);
            }
        }

        String optSpec = System.getenv().getOrDefault("FORK_ROLLOUT_OPTION", "all");
        int onlyOption = -1; // -1 == all
        if (!"all".equals(optSpec)) {
            try {
                onlyOption = Integer.parseInt(optSpec.trim());
                if (onlyOption < 0) throw new NumberFormatException("negative");
            } catch (Exception e) {
                System.err.println("[FORK] FORK_ROLLOUT_OPTION must be 'all' or a non-negative option index (got '" + optSpec + "')");
                System.exit(2);
            }
        }

        // FORK_ROLLOUT_J_OFFSET shifts the j band this run draws from, so two runs over the
        // same position can use PROVABLY DISJOINT rollout streams. That is what makes a cheap
        // screening pass safe to build on: screen with j in [0, s), confirm with j in [s, s+n),
        // and the confirming rollouts are not the ones that selected the position. Reusing them
        // would be a winner's-curse bias -- the screen preferentially picks positions whose
        // sampling noise ran high, and re-reading that same noise as evidence inflates every
        // spread it minted a key from.
        int jOffset = 0;
        String jOffEnv = System.getenv("FORK_ROLLOUT_J_OFFSET");
        if (jOffEnv != null && !jOffEnv.isEmpty()) {
            try {
                jOffset = Integer.parseInt(jOffEnv.trim());
                if (jOffset < 0) throw new NumberFormatException("negative");
            } catch (Exception e) {
                System.err.println("[FORK] FORK_ROLLOUT_J_OFFSET must be a non-negative integer (got '"
                        + jOffEnv + "')");
                System.exit(2);
            }
        }

        String detEnv = System.getenv("FORK_ROLLOUT_DETERMINIZE");
        Boolean detParsed = parseDeterminize(detEnv);
        if (detParsed == null) {
            System.err.println("[FORK] FORK_ROLLOUT_DETERMINIZE must be 'library' or 'none' (got '"
                    + detEnv + "'). Refusing rather than defaulting: silently falling back to 'none' "
                    + "would reproduce the zero-variance defect (every arm 0/n or n/n) while the run "
                    + "still reported OK.");
            System.exit(2);
        }
        final boolean determinize = detParsed.booleanValue();
        final String determinizeLabel = determinize ? "library" : "none";

        File outFile = resolveRolloutOut(gameSeed, targetIdx);

        // ---- probe: re-reach the position once, capture its shape + prefix (exits 2/5 itself) ----
        ProbeResult probe = runProbe(targetIdx);
        if (!isForceableType(probe.type)) {
            System.err.println("[FORK] REFUSE: decision at index " + targetIdx + " has type '" + probe.type
                    + "' — v1 rollouts force only index-encoded types (play, mulligan). Count-encoded "
                    + "types log a subset SIZE, not an index; there is no per-option object to force. "
                    + "(exit 3 is retired; this refusal exits 2 like every other.)");
            System.exit(2);
        }
        if (onlyOption >= probe.nOptions) {
            System.err.println("[FORK] FORK_ROLLOUT_OPTION=" + onlyOption + " out of range (n_options="
                    + probe.nOptions + " at index " + targetIdx + ")");
            System.exit(2);
        }

        // Identity of the deciding seat. On a mirror-matchup name tie both idents match; equals()
        // against DECK_A_NAME makes 'a' win the tie — same convention replay_match.seat_to_ident uses.
        final boolean seatIsA = probe.seat != null
                && probe.seat.equals(System.getenv().getOrDefault("DECK_A_NAME", "Grixis Reanimator"));

        List<Integer> targets = new ArrayList<>();
        if (onlyOption >= 0) targets.add(onlyOption);
        else for (int o = 0; o < probe.nOptions; o++) targets.add(o);

        PrintWriter out = new PrintWriter(new FileWriter(outFile, true), true);
        StringBuilder pj = new StringBuilder(256);
        pj.append("{\"t\":\"fork_rollout_probe\",\"idx\":").append(targetIdx)
                .append(",\"seat\":\"").append(LlmSpikeGui.jsonEscape(probe.seat)).append('"')
                .append(",\"turn\":").append(probe.turn)
                .append(",\"phase\":\"").append(LlmSpikeGui.jsonEscape(probe.phase)).append('"')
                .append(",\"decision_type\":\"").append(LlmSpikeGui.jsonEscape(probe.type)).append('"')
                .append(",\"n_options\":").append(probe.nOptions)
                .append(",\"source_choice\":").append(probe.choice)
                .append(",\"options\":").append(jsonStringArray(probe.options))
                .append(",\"n_per_option\":").append(nPerOption)
                .append(",\"j_offset\":").append(jOffset)
                .append(",\"seed_base\":").append(seedBase)
                .append(",\"seed_fn\":\"mix64(base_seed ^ (i*C1) ^ (j*C2))\"}");
        out.println(pj.toString());

        int[][] tally = new int[probe.nOptions][4];
        long[][] seeds = new long[probe.nOptions][nPerOption];
        int emitted = 0;
        int prefixDrifts = 0;
        int determinizeNoops = 0;
        for (int o : targets) {
            for (int j = jOffset; j < jOffset + nPerOption; j++) {
                final long rseed = rolloutSeed(seedBase, o, j);
                seeds[o][j - jOffset] = rseed;
                final int optionToForce = o;
                final AtomicBoolean fired = new AtomicBoolean(false);
                final int[] loggedChoice = {Integer.MIN_VALUE};
                final int[] shuffled = {0};
                final List<String[]> prefixRows = new ArrayList<>();

                LlmSpikeGui.ForkForcer forcer = (idx, pl, seat, type, nOptions, options) -> {
                    if (idx != targetIdx || !fired.compareAndSet(false, true)) return null;
                    // Shape guard: if the live decision no longer matches the probe (drift), do NOT
                    // force a wrong menu — leave the decision alone; forced_ok stays false below.
                    if (!Objects.equals(type, probe.type) || nOptions != probe.nOptions) return null;
                    // Reseed the continuation stream at the exact moment of the forced decision
                    // (see the MyRandom contract note in this method's javadoc).
                    MyRandom.setRandom(new java.util.Random(rseed));
                    // ...then re-deal the hidden information. The reseed alone provably does not
                    // make the continuation diverge — see determinizeHiddenZones' javadoc for the
                    // measurement. Order matters: the shuffle must draw from the fresh stream so
                    // it is a pure function of rollout_seed.
                    shuffled[0] = determinizeHiddenZones(pl, determinize);
                    return optionToForce;
                };
                LlmSpikeGui.ForkHook hook = (idx, pl, seat, type, nOptions, choice, options) -> {
                    if (idx < targetIdx) prefixRows.add(new String[] {type, seat, String.valueOf(nOptions)});
                    else if (idx == targetIdx) loggedChoice[0] = choice;
                };

                GameRunResult r = playDeterministicGame(hook, forcer);
                if (r.threadLeaked) {
                    System.err.println("[FORK] game thread leaked past cap+grace on option " + o + " j=" + j
                            + " — later rollouts in this JVM would share a live game's statics; aborting.");
                    System.exit(5);
                }

                boolean prefixOk = prefixMatches(probe.prefix, prefixRows);
                if (!prefixOk) prefixDrifts++;
                boolean honoured = fired.get()
                        && loggedChoice[0] == expectedLoggedChoice(probe.type, o, probe.nOptions);
                boolean forcedOk = prefixOk && honoured && r.workerError == null;

                String winner = "invalid";
                int turns = -1;
                String winCond = "?";
                if (r.workerError == null) {
                    String[] oc = outcomeOf(r.game, r.capped);
                    winner = oc[0];
                    turns = Integer.parseInt(oc[1]);
                    winCond = oc[2];
                }
                String note = r.workerError != null ? ("worker: " + r.workerError)
                        : !prefixOk ? "prefix_drift"
                        : !honoured ? "force_not_honoured" : null;

                StringBuilder rj = new StringBuilder(256);
                rj.append("{\"t\":\"fork_rollout\",\"idx\":").append(targetIdx)
                        .append(",\"option\":").append(o)
                        .append(",\"j\":").append(j)
                        .append(",\"rollout_seed\":").append(rseed)
                        .append(",\"winner\":\"").append(winner).append('"')
                        .append(",\"turns\":").append(turns)
                        .append(",\"win_condition\":\"").append(LlmSpikeGui.jsonEscape(winCond)).append('"')
                        .append(",\"seat\":\"").append(LlmSpikeGui.jsonEscape(probe.seat)).append('"')
                        .append(",\"decision_type\":\"").append(LlmSpikeGui.jsonEscape(probe.type)).append('"')
                        .append(",\"n_options\":").append(probe.nOptions)
                        .append(",\"options\":").append(jsonStringArray(probe.options))
                        .append(",\"determinize\":\"").append(determinizeLabel).append('"')
                        .append(",\"libraries_shuffled\":").append(shuffled[0])
                        .append(",\"forced_ok\":").append(forcedOk);
                if (determinize && fired.get() && shuffled[0] == 0) determinizeNoops++;
                if (note != null) rj.append(",\"note\":\"").append(LlmSpikeGui.jsonEscape(note)).append('"');
                rj.append('}');
                out.println(rj.toString());
                emitted++;
                tally[o][outcomeSlot(winner, seatIsA, forcedOk)]++;
            }
        }

        String ctlA = System.getenv().getOrDefault("CONTROLLER_A", "llm");
        String ctlB = System.getenv().getOrDefault("CONTROLLER_B", "llm");
        StringBuilder sj = new StringBuilder(256);
        sj.append("{\"t\":\"fork_rollout_summary\",\"idx\":").append(targetIdx)
                .append(",\"seat\":\"").append(LlmSpikeGui.jsonEscape(probe.seat)).append('"')
                .append(",\"decision_type\":\"").append(LlmSpikeGui.jsonEscape(probe.type)).append('"')
                .append(",\"n_options\":").append(probe.nOptions)
                .append(",\"options\":").append(jsonStringArray(probe.options))
                .append(",\"n_per_option\":").append(nPerOption)
                .append(",\"j_offset\":").append(jOffset)
                .append(",\"seed_base\":").append(seedBase)
                .append(",\"seed_fn\":\"mix64(base_seed ^ (i*C1) ^ (j*C2))\"")
                .append(",\"continuation\":\"").append(LlmSpikeGui.jsonEscape(ctlA + "/" + ctlB)).append('"')
                .append(",\"determinize\":\"").append(determinizeLabel).append('"')
                .append(",\"per_option\":").append(perOptionJson(tally, seeds, targets))
                .append('}');
        out.println(sj.toString());
        out.flush();

        if (emitted > 0 && prefixDrifts == emitted) {
            // Every rollout's pre-index decision stream disagreed with the probe's: the replay
            // mechanism itself is unsound here, and no WP from this run can be trusted.
            System.out.println("REPLAY-DRIFT at index " + targetIdx + ": every rollout prefix drifted ("
                    + prefixDrifts + "/" + emitted + ")");
            System.exit(4);
        }
        if (emitted == 0) {
            System.out.println("[FORK] FORK-FAILED: no rollout was attempted");
            System.exit(5);
        }
        if (determinize && determinizeNoops == emitted) {
            // Determinization was requested, the forcer fired, and not one library was re-dealt.
            // Every WP below is then a SINGLE-DEAL number wearing a determinized label — exactly
            // the failure that produced 0/n-or-n/n arms and looked like a clean run. Refuse.
            System.out.println("[FORK] FORK-FAILED: determinize=library requested but 0 libraries "
                    + "were shuffled in any of " + emitted + " rollouts; WPs would be single-deal");
            System.exit(5);
        }
        System.out.println("[FORK] rollout OK: " + emitted + " rollouts over " + targets.size()
                + " option(s) x " + nPerOption + " -> " + outFile);
        System.exit(0);
    }

    /** One enumerate-style probe game: replay to {@code targetIdx}, capture the decision's shape and
     *  the pre-index decision-row prefix, terminate. Exits 2 if the index is never reached, 5 on a
     *  worker error — mirrors {@link #runEnumerate}'s error discipline. */
    private static ProbeResult runProbe(int targetIdx) throws Exception {
        final ProbeResult probe = new ProbeResult();
        final AtomicBoolean hit = new AtomicBoolean(false);
        LlmSpikeGui.ForkHook hook = (idx, pl, seat, type, nOptions, choice, options) -> {
            if (idx < targetIdx) {
                probe.prefix.add(new String[] {type, seat, String.valueOf(nOptions)});
                return;
            }
            if (idx != targetIdx || !hit.compareAndSet(false, true)) return;
            try {
                probe.type = type;
                probe.seat = seat;
                probe.nOptions = nOptions;
                probe.choice = choice;
                probe.options = options == null ? Collections.<String>emptyList() : new ArrayList<>(options);
                PhaseHandler ph = pl.getGame().getPhaseHandler();
                probe.turn = ph.getTurn();
                probe.phase = String.valueOf(ph.getPhase());
            } catch (Throwable t) {
                System.out.println("[FORK] rollout probe hook failed: " + t);
            } finally {
                try { pl.getGame().setGameOver(GameEndReason.Draw); } catch (Throwable ignore) { }
            }
        };
        GameRunResult r = playDeterministicGame(hook, null);
        if (r.workerError != null) {
            System.out.println("[FORK] REPLAY-FAILED: probe game thread threw " + r.workerError);
            System.exit(5);
        }
        if (!hit.get()) {
            System.err.println("[FORK] FORK_DECISION_INDEX=" + targetIdx + " was never reached by the "
                    + "rollout probe (the game logged only " + r.decisionCount + " decision(s))");
            System.exit(2);
        }
        return probe;
    }

    /** Row-by-row prefix diff (same [type, seat, n_options] triple check mode uses). */
    static boolean prefixMatches(List<String[]> expected, List<String[]> got) {
        if (expected.size() != got.size()) return false;
        for (int i = 0; i < expected.size(); i++) if (!rowsMatch(expected.get(i), got.get(i))) return false;
        return true;
    }

    /** Winner/turns/win_condition for a finished rollout game — the same identity-keyed extraction
     *  {@link LlmSpikeGui#writeGameSummary} performs, without writing a summary row or dumping the
     *  full game log (a rollout run plays dozens of games; the fork_rollout rows are the record). */
    static String[] outcomeOf(Game game, boolean capped) {
        String nameA = System.getenv().getOrDefault("DECK_A_NAME", "Grixis Reanimator");
        String nameB = System.getenv().getOrDefault("DECK_B_NAME", "Mardu Energy");
        String winner = "draw";
        int turns = -1;
        String winCond = "?";
        try {
            if (game != null && game.getOutcome() != null) {
                turns = game.getOutcome().getLastTurnNumber();
                winCond = String.valueOf(game.getOutcome().getWinCondition());
                var w = game.getOutcome().getWinningLobbyPlayer();
                if (w != null) winner = nameA.equals(w.getName()) ? "A" : (nameB.equals(w.getName()) ? "B" : "invalid");
            }
        } catch (Exception ignore) { }
        if (capped) winner = "invalid";
        return new String[] {winner, String.valueOf(turns), winCond};
    }

    private static String jsonStringArray(List<String> items) {
        StringBuilder sb = new StringBuilder("[");
        if (items != null) {
            for (int i = 0; i < items.size(); i++) {
                if (i > 0) sb.append(',');
                sb.append('"').append(LlmSpikeGui.jsonEscape(items.get(i))).append('"');
            }
        }
        return sb.append(']').toString();
    }

    /** FORK_ROLLOUT_OUT resolution: a *.jsonl path is used as-is; a directory (or anything not
     *  ending in .jsonl) gets a fork-rollout-&lt;seed&gt;-&lt;idx&gt;.jsonl inside it; unset falls back to
     *  EVAL_OUT (already validated non-empty by requireDeterministicControllers). */
    private static File resolveRolloutOut(long seed, int idx) {
        String p = System.getenv("FORK_ROLLOUT_OUT");
        if (p == null || p.isEmpty()) p = System.getenv("EVAL_OUT");
        File f = new File(p);
        if (f.isDirectory() || p.endsWith(File.separator) || !p.endsWith(".jsonl")) {
            f.mkdirs();
            return new File(f, "fork-rollout-" + seed + "-" + idx + ".jsonl");
        }
        if (f.getParentFile() != null) f.getParentFile().mkdirs();
        return f;
    }

    // ==================== shared game-running boilerplate ====================

    private static final class GameRunResult {
        final Game game;
        final long seed;
        final File jsonlFile;
        final long durMs;
        final long capMs;
        final boolean capped;
        final int decisionCount;
        /** Non-null iff the worker thread's {@code match.startGame(game)} threw. A check/enumerate
         *  run that ended this way did NOT complete a real game -- callers must report failure and
         *  exit non-zero instead of taking the success path (see runCheck/runEnumerate). */
        final Throwable workerError;
        /** True iff the worker thread survived cap + grace. Rollout mode MUST abort on this: a live
         *  game thread from a previous rollout shares FORK_HOOK/FORK_FORCER/EvalLog statics with the
         *  next game and would silently corrupt its rows. check/enumerate (one game per JVM, exit
         *  right after) ignore it — behavior unchanged from Milestone D. */
        final boolean threadLeaked;
        GameRunResult(Game game, long seed, File jsonlFile, long durMs, long capMs, boolean capped,
                int decisionCount, Throwable workerError, boolean threadLeaked) {
            this.game = game; this.seed = seed; this.jsonlFile = jsonlFile;
            this.durMs = durMs; this.capMs = capMs; this.capped = capped; this.decisionCount = decisionCount;
            this.workerError = workerError; this.threadLeaked = threadLeaked;
        }
    }

    private static boolean forgeInitialized = false;

    /** One-time headless Forge init (identical to {@link LlmSpikeHeadless#main}'s). Factored out of
     *  {@link #playDeterministicGame} so rollout mode can replay MANY games in one JVM — FModel is a
     *  process singleton and must not be re-initialized per game. */
    private static void initForgeOnce() {
        if (forgeInitialized) return;
        forgeInitialized = true;
        System.out.println("[FORK] initializing Forge model (no GUI)...");
        GuiBase.setInterface(new GuiDesktop());
        FModel.initialize(null, preferences -> {
            preferences.setPref(FPref.LOAD_CARD_SCRIPTS_LAZILY, false);
            preferences.setPref(FPref.UI_LANGUAGE, "en-US");
            return null;
        });
    }

    private static GameRunResult playDeterministicGame(LlmSpikeGui.ForkHook hook) throws Exception {
        return playDeterministicGame(hook, null);
    }

    /** Boilerplate shared by check/enumerate/rollout: init Forge headless (once per JVM), seed,
     *  EvalLog, build the deterministic-controller match, run it on a worker thread with the SAME
     *  wall-clock-cap discipline LlmSpikeHeadless uses (including the post-cap grace join long
     *  enough to outlast the slowest configured model timeout -- irrelevant here since fork
     *  controllers never call a model, but kept identical so a future llm-in-the-loop mode doesn't
     *  silently drop a row), and hand back everything the caller needs to write its own game
     *  summary and read the jsonl back. If {@code hook} is non-null it is installed as
     *  {@code LlmSpikeGui.FORK_HOOK} BEFORE the game starts, so it can observe (and, for
     *  enumerate/probe, terminate) the live decision stream; if {@code forcer} is non-null it is
     *  installed as {@code LlmSpikeGui.FORK_FORCER} the same way (rollout only). Both are cleared
     *  after the game so repeated in-JVM games never inherit a stale hook. Every game gets a FRESH
     *  per-game jsonl (EvalLog.closeForTest + init — a no-op detach on the first game) and a reset
     *  FORK_DECISION_SEQ, so decision indices are per-game in emission order for every mode. */
    private static GameRunResult playDeterministicGame(LlmSpikeGui.ForkHook hook,
            LlmSpikeGui.ForkForcer forcer) throws Exception {
        long capMs = 3 * 60 * 60 * 1000L;
        try { String v = System.getenv("GAME_CAP_MS"); if (v != null) capMs = Long.parseLong(v.trim()); } catch (Exception ignore) { }

        initForgeOnce();
        LlmSpikeGui.FORK_HOOK = hook;
        LlmSpikeGui.FORK_FORCER = forcer;
        try {
            long seed = LlmSpikeGui.applySeed();
            LlmSpikeGui.EvalLog.closeForTest(); // no-op before the first game; fresh file for rollout N>1
            LlmSpikeGui.EvalLog.init(seed);
            LlmSpikeGui.FORK_DECISION_SEQ.set(-1); // per-game emission index (rollout replays many games/JVM)
            List<RegisteredPlayer> players = LlmSpikeGui.buildPlayers();

            GameRules rules = new GameRules(GameType.Constructed);
            Match match = new Match(rules, players, "LlmFork");
            final Game game = match.createGame();

            long t0 = System.currentTimeMillis();
            System.out.println("[FORK] game starting (cap=" + (capMs / 60000) + "min)");
            java.util.concurrent.atomic.AtomicReference<Throwable> workerError = new java.util.concurrent.atomic.AtomicReference<>();
            Thread worker = new Thread(() -> {
                try { match.startGame(game); }
                catch (Throwable t) {
                    System.out.println("[FORK] game thread threw: " + t);
                    workerError.set(t);
                }
            }, "forge-game-fork");
            worker.setDaemon(true);
            worker.start();
            worker.join(capMs);
            boolean capped = worker.isAlive();
            if (capped) {
                System.out.println("[FORK] wall-clock cap reached; forcing game over");
                game.setGameOver(GameEndReason.Draw);
                // maxDecisionMs(), not maxConfiguredTimeoutMs(): a null reply sends the router into
            // its retry loop, where each further attempt gets a FULL fresh timeout. Budgeting
            // for one timeout meant a game capped on attempt 2 or 3 was killed with the ask
            // still in flight -- the silent drop this grace exists to prevent.
            worker.join(LlmSpikeGui.ModelCfg.maxDecisionMs() + 10_000L);
            }
            boolean threadLeaked = worker.isAlive();
            long durMs = System.currentTimeMillis() - t0;

            File jsonlFile = LlmSpikeGui.EvalLog.currentFile();
            int decisionCount = LlmSpikeGui.FORK_DECISION_SEQ.get() + 1;
            return new GameRunResult(game, seed, jsonlFile, durMs, capMs, capped, decisionCount,
                    workerError.get(), threadLeaked);
        } finally {
            LlmSpikeGui.FORK_HOOK = null;
            LlmSpikeGui.FORK_FORCER = null;
        }
    }

    // ==================== tiny jsonl scalar readers (numeric/bool; strings reuse LlmSpikeGui's) ====================

    /** Package-visible (not private) so LlmSpikeTest can exercise edge cases (null line, missing
     *  key, negative numbers) directly. */
    static String numField(String line, String key) {
        if (line == null) return null;
        Matcher m = Pattern.compile("\"" + Pattern.quote(key) + "\":(-?\\d+)").matcher(line);
        return m.find() ? m.group(1) : null;
    }

    /** Package-visible (not private) so LlmSpikeTest can exercise edge cases directly. */
    static Boolean boolField(String line, String key) {
        if (line == null) return null;
        Matcher m = Pattern.compile("\"" + Pattern.quote(key) + "\":(true|false)").matcher(line);
        return m.find() ? Boolean.valueOf(m.group(1)) : null;
    }
}
