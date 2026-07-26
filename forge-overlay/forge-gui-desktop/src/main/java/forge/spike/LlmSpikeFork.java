package forge.spike;

import java.io.File;
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
import forge.game.player.RegisteredPlayer;
import forge.gui.GuiBase;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;

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
 *   <li><b>rollout</b> — validates its args and stops: {@code FORK-ROLLOUT NOT-IMPLEMENTED (see
 *       HANDOFF)}, exit 3. See {@link #applyOptionAndRolloutSTUB()} for the scoped design.</li>
 * </ul>
 */
public final class LlmSpikeFork {
    private LlmSpikeFork() {}

    /** Entry point. Every path ends in {@code System.exit(code)}; nothing here returns to a caller
     *  that expects further work (mirrors {@link LlmSpikeHeadless#main}'s own explicit-exit
     *  discipline — bridges/subprocess pipes aside, an explicit exit keeps behavior identical
     *  across modes instead of depending on daemon-thread cleanup).
     *  <p>Exit-code contract: 0 = OK, 2 = refused (bad args / provenance mismatch), 3 = rollout
     *  not implemented, 4 = REPLAY-DRIFT, 5 = REPLAY-FAILED (worker error mid-game). Any
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

    private static final Set<String> DETERMINISTIC_CONTROLLERS = Set.of("floor", "random", "ai");

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
            problems.add("CONTROLLER_A must be one of floor|random|ai (got '" + ctlA + "')");
        if (ctlB == null || !DETERMINISTIC_CONTROLLERS.contains(ctlB))
            problems.add("CONTROLLER_B must be one of floor|random|ai (got '" + ctlB + "')");
        if (evalOut == null || evalOut.isEmpty())
            problems.add("EVAL_OUT must be set (fork modes read/write the decision jsonl)");
        if (!problems.isEmpty()) {
            System.err.println("[FORK] refusing: an llm controller cannot be replayed deterministically "
                    + "(FORK_MODE requires floor|random|ai — see CLAUDE.md validation ladder).");
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

    // ==================== rollout mode (stub) ====================

    private static void runRollout() throws Exception {
        requireDeterministicControllers();
        requireDecisionIndex(); // validated for parity with enumerate; unused until implemented
        System.out.println("FORK-ROLLOUT NOT-IMPLEMENTED (see HANDOFF)");
        System.exit(3);
    }

    /**
     * STUB — Milestone D rollout is not implemented. Unreachable in v1: {@link #runRollout()}
     * always exits before this could be called. Left as the concrete entry point + design
     * contract for the real implementation; see HANDOFF.md at the worktree root for the full
     * writeup.
     *
     * <p>Intended behavior:
     * <ol>
     *   <li>Replay deterministically to {@code FORK_DECISION_INDEX} using the same mechanism as
     *       {@link #runEnumerate()} / {@link #playDeterministicGame}, but instead of terminating
     *       at the hook, capture the live option set and let the harness force a SPECIFIC option
     *       (index {@code i} into the same {@code options} list the enumerate row already logs;
     *       for "play" rows this includes the PASS slot, matching the play row's own
     *       {@code choice=-1} PASS encoding). {@link LlmSpikeGui.ForkHook} today only receives the
     *       string labels {@code emitDecisionRow} builds, not the underlying engine objects
     *       (SpellAbility / blocker assignment / etc.) — extending the hook, or re-deriving those
     *       objects by re-running the controller's own option-enumeration for that decision type,
     *       is the first sub-task.</li>
     *   <li>Add a "forced" controller mode (or an index-injection hook) to
     *       {@code PlayerControllerLLM} so a specific decision can be steered to option {@code i}
     *       instead of the deterministic controller's own floor/random pick, then let the game
     *       continue normally from there.</li>
     *   <li>Roll out to completion N times per option, reseeding ONLY the post-decision
     *       randomness for each rollout {@code j}: {@code rollout_seed = f(base_seed, option_i, j)}.
     *       Simplest sound choice (verify against MyRandom's actual contract before trusting it —
     *       do not assume): a SplitMix64-style mix, e.g.
     *       {@code rollout_seed = mix64(base_seed ^ (option_i * C1) ^ (j * C2))} for two large odd
     *       constants, applied via {@code MyRandom.setRandom(...)} at the moment of the forced
     *       decision — NOT at process start, since the pre-decision prefix must stay bit-identical
     *       across every rollout of the same position; only the continuation's RNG stream should
     *       vary by {@code j}. Confirm whether MyRandom's stream needs to preserve already-consumed
     *       state for draws not yet made, or whether a fresh Random is correct — check the contract,
     *       do not guess.</li>
     *   <li>Each of the N rollouts needs a fresh Game/Match sharing an identical pre-decision
     *       prefix. EVAL-DESIGN.md §3.1 explicitly rules out mid-stack GameCopier snapshots for v1
     *       and restricts rollout points to EMPTY-STACK decisions (main-phase play, attackers,
     *       blockers, mulligan) for exactly this reason. Replay-from-index (re-running the
     *       deterministic prefix from turn 1 with the same seed up to decision_index — already
     *       what {@link #playDeterministicGame} does) is the v1 mechanism, not a live state fork;
     *       each rollout re-plays the full prefix (cheap: floor/random games run in seconds) then
     *       diverges at decision_index. Only reach for GameCopier as plan B if replay-to-index
     *       proves fragile, same empty-stack restriction still applies (design doc's own fallback
     *       note).</li>
     *   <li><b>Empty-stack restriction (non-negotiable in v1):</b> validate the target decision's
     *       {@code type} against the empty-stack-safe set (play/attackers/blockers/mulligan; NOT
     *       targets/discard/sacrifice/modes/entity/zone-change, which can fire mid-stack) and
     *       refuse (exit 2) otherwise, BEFORE spending any rollout compute.</li>
     *   <li>Emit one jsonl line per (position, option) with win counts — schema
     *       {@code eval/schemas/puzzle-position.v1.schema.json} (EVAL-DESIGN.md §10.1; confirm
     *       that schema file has actually landed before writing the emitter — it did not exist in
     *       this worktree as of Milestone D).</li>
     * </ol>
     *
     * <p>Estimated effort: 3-5 days. The replay-to-index plumbing this stub depends on
     * ({@link #playDeterministicGame}, {@link LlmSpikeGui.ForkHook}) is done; what remains is (a)
     * exposing engine-level option objects (not just labels) through the hook, (b) the
     * forced-decision override in {@code PlayerControllerLLM}, (c) the seed function verified
     * against MyRandom's real contract, (d) the N-rollout driver + win-count aggregation + schema
     * emission, (e) validation against a handful of known-lethal positions before trusting it.
     */
    private static void applyOptionAndRolloutSTUB() {
        throw new UnsupportedOperationException(
                "Milestone D rollout is not implemented -- see HANDOFF.md and this method's own "
                + "doc comment for the intended design.");
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
        GameRunResult(Game game, long seed, File jsonlFile, long durMs, long capMs, boolean capped,
                int decisionCount, Throwable workerError) {
            this.game = game; this.seed = seed; this.jsonlFile = jsonlFile;
            this.durMs = durMs; this.capMs = capMs; this.capped = capped; this.decisionCount = decisionCount;
            this.workerError = workerError;
        }
    }

    /** Boilerplate shared by check/enumerate: init Forge headless (identical to
     *  {@link LlmSpikeHeadless#main}), seed, EvalLog, build the deterministic-controller match,
     *  run it on a worker thread with the SAME wall-clock-cap discipline LlmSpikeHeadless uses
     *  (including the post-cap grace join long enough to outlast the slowest configured model
     *  timeout -- irrelevant here since fork controllers never call a model, but kept identical so
     *  a future llm-in-the-loop mode doesn't silently drop a row), and hand back everything the
     *  caller needs to write its own game summary and read the jsonl back. If {@code hook} is
     *  non-null it is installed as {@code LlmSpikeGui.FORK_HOOK} BEFORE the game starts, so it can
     *  observe (and, for enumerate, terminate) the live decision stream; check mode passes null and
     *  lets the game run to completion untouched. */
    private static GameRunResult playDeterministicGame(LlmSpikeGui.ForkHook hook) throws Exception {
        long capMs = 3 * 60 * 60 * 1000L;
        try { String v = System.getenv("GAME_CAP_MS"); if (v != null) capMs = Long.parseLong(v.trim()); } catch (Exception ignore) { }

        LlmSpikeGui.FORK_HOOK = hook;

        System.out.println("[FORK] initializing Forge model (no GUI)...");
        GuiBase.setInterface(new GuiDesktop());
        FModel.initialize(null, preferences -> {
            preferences.setPref(FPref.LOAD_CARD_SCRIPTS_LAZILY, false);
            preferences.setPref(FPref.UI_LANGUAGE, "en-US");
            return null;
        });

        long seed = LlmSpikeGui.applySeed();
        LlmSpikeGui.EvalLog.init(seed);
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
            worker.join(LlmSpikeGui.ModelCfg.maxConfiguredTimeoutMs() + 10_000L);
        }
        long durMs = System.currentTimeMillis() - t0;

        File jsonlFile = LlmSpikeGui.EvalLog.currentFile();
        int decisionCount = LlmSpikeGui.FORK_DECISION_SEQ.get() + 1;
        return new GameRunResult(game, seed, jsonlFile, durMs, capMs, capped, decisionCount, workerError.get());
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
