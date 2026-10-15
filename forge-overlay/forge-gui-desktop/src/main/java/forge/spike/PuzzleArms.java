package forge.spike;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Random;

/**
 * The zero-model policy arms of the puzzle axis (eval/PUZZLES.md), and the draw tape that makes a
 * random rollout a replayable reference line.
 *
 * <p>Every arm here plugs into the one seam the random arm already uses: {@code PlayerControllerLLM.rnd}.
 * The random arm's decision sites draw from it ({@code nextInt}, {@code nextBoolean}, {@code nextLong},
 * {@code Collections.shuffle}), so an arm is just a different source of those draws:
 * <ul>
 *   <li>{@code random}: {@link Tape} seeded exactly as before. Every draw goes through {@code super}, so
 *       the stream is identical to the plain {@code java.util.Random} it replaces (the seed-42 random canary
 *       cannot move), and each draw is also written down.</li>
 *   <li>{@code script}: {@link Script} answers from a tape written by an earlier {@code random} rollout.
 *       Same seed, same draws, same game: that is how a stored reference line is replayed by the engine.
 *       A draw whose kind or bound differs from the tape is DRIFT (the menu changed under the line), and
 *       running off the end of the tape is EXHAUSTED; both are recorded, and the verifier refuses either.</li>
 *   <li>{@code pass}: always the do-nothing option where one exists (PASS, decline, the fewest picks,
 *       no blocks, the smallest X), else the first.</li>
 *   <li>{@code first}: always the first option listed (one pick from a subset menu when one is allowed).</li>
 * </ul>
 *
 * <p>The context-free draws ({@code nextInt(n)}) cannot tell "the last option is a decline" from "the
 * last option is a card", so the two sites where it matters ask {@link #pick} instead, and the subset
 * sites ask {@link #subsetSize} and {@link #shuffle}. For a plain or recording Random those helpers make
 * exactly the draws the old code made, in the same order.
 */
final class PuzzleArms {
    private PuzzleArms() {}

    /** The arms that run on the random-arm decision paths (the {@code rnd != null} branches). */
    static boolean isArm(String mode) {
        return "random".equals(mode) || "script".equals(mode) || "pass".equals(mode) || "first".equals(mode);
    }

    /** The tape the next {@code script} seat replays. Set by LlmSpikePuzzle before each rollout. */
    static volatile String scriptTape = "";
    /** The last arm Random built, so the runner can read its tape/drift after the game. */
    static volatile ArmRandom lastBuilt = null;

    /** The draw source for {@code mode}, or null for a seat that is not one of these arms. {@code seed}
     *  is the long the random arm has always drawn from MyRandom at construction; every arm draws it,
     *  so the engine's own stream (and with it the opponent's AI) is the same across arms. */
    static Random forMode(String mode, long seed) {
        ArmRandom r;
        switch (mode == null ? "" : mode) {
            case "random": r = new Tape(seed); break;
            case "script": r = new Script(seed, scriptTape); break;
            case "pass":   r = new Fixed(seed, true); break;
            case "first":  r = new Fixed(seed, false); break;
            default: return null;
        }
        lastBuilt = r;
        return r;
    }

    /** One choice from {@code n} options; {@code lastIsDecline} says option n-1 is the do-nothing one. */
    static int pick(Random r, int n, boolean lastIsDecline) {
        if (r instanceof ArmRandom) return ((ArmRandom) r).choose(n, lastIsDecline);
        return r.nextInt(n);
    }

    /** How many items a random subset of lo..hi takes (draws only when hi > lo, as the old code did). */
    static int subsetSize(Random r, int lo, int hi) {
        if (r instanceof ArmRandom) return ((ArmRandom) r).size(lo, hi);
        return lo + (hi > lo ? r.nextInt(hi - lo + 1) : 0);
    }

    /** Shuffle the candidate order for a subset pick; the fixed arms keep menu order. */
    static void shuffle(List<Integer> items, Random r) {
        if (r instanceof Fixed) return;
        Collections.shuffle(items, r);
    }

    /** Base for every arm Random: a tape of what was drawn, and drift/exhaustion flags. */
    abstract static class ArmRandom extends Random {
        private static final long serialVersionUID = 1L;
        static final int TAPE_CAP = 200_000;
        final List<String> tape = new ArrayList<>();
        String drift = null;      // first mismatch, script arm only
        boolean exhausted = false;

        ArmRandom(long seed) { super(seed); }

        abstract int choose(int n, boolean lastIsDecline);
        abstract int size(int lo, int hi);

        void note(String draw) { if (tape.size() < TAPE_CAP) tape.add(draw); }
        String tapeString() { return String.join(" ", tape); }
        int draws() { return tape.size(); }
    }

    /** The random arm: identical stream to {@code new Random(seed)}, every draw written down. */
    static final class Tape extends ArmRandom {
        private static final long serialVersionUID = 1L;
        Tape(long seed) { super(seed); }
        @Override public int nextInt(int bound) { int v = super.nextInt(bound); note("i" + bound + ":" + v); return v; }
        @Override public boolean nextBoolean() { boolean v = super.nextBoolean(); note("b:" + (v ? 1 : 0)); return v; }
        @Override public long nextLong(long origin, long bound) {
            long v = super.nextLong(origin, bound); note("l" + origin + "," + bound + ":" + v); return v;
        }
        @Override int choose(int n, boolean lastIsDecline) { return nextInt(n); }
        @Override int size(int lo, int hi) { return lo + (hi > lo ? nextInt(hi - lo + 1) : 0); }
    }

    /** Replays a {@link Tape}. A mismatch or the end of the tape falls back to the PASS policy, flagged. */
    static final class Script extends ArmRandom {
        private static final long serialVersionUID = 1L;
        private final String[] script;
        private int pos = 0;
        private final Fixed after;

        Script(long seed, String tapeText) {
            super(seed);
            String t = tapeText == null ? "" : tapeText.trim();
            this.script = t.isEmpty() ? new String[0] : t.split("\\s+");
            this.after = new Fixed(seed, true);
        }

        /** The scripted value for a draw of this kind ("i5", "b", "l0,3"), or null to fall back. */
        private String next(String kind) {
            if (drift != null) return null;
            if (pos >= script.length) { exhausted = true; return null; }
            String d = script[pos];
            int colon = d.lastIndexOf(':');
            if (colon < 0 || !d.substring(0, colon).equals(kind)) {
                drift = "draw " + pos + ": tape has " + d + ", game asked " + kind;
                return null;
            }
            pos++;
            note(d);
            return d.substring(colon + 1);
        }

        @Override public int nextInt(int bound) {
            String v = next("i" + bound);
            if (v == null) return after.nextInt(bound);
            int x = Integer.parseInt(v);
            if (x < 0 || x >= bound) { drift = "draw " + (pos - 1) + ": value " + x + " outside bound " + bound; return 0; }
            return x;
        }
        @Override public boolean nextBoolean() {
            String v = next("b");
            return v == null ? after.nextBoolean() : "1".equals(v);
        }
        @Override public long nextLong(long origin, long bound) {
            String v = next("l" + origin + "," + bound);
            return v == null ? after.nextLong(origin, bound) : Long.parseLong(v);
        }
        @Override int choose(int n, boolean lastIsDecline) {
            if (drift != null || pos >= script.length) {
                if (pos >= script.length) exhausted = true;
                return after.choose(n, lastIsDecline);
            }
            return nextInt(n);
        }
        @Override int size(int lo, int hi) {
            if (hi <= lo) return lo;
            if (drift != null || pos >= script.length) {
                if (pos >= script.length) exhausted = true;
                return after.size(lo, hi);
            }
            return lo + nextInt(hi - lo + 1);
        }
        /** Draws of the tape not consumed when the game ended (a solving line normally ends the game
         *  with none left; a positive number means the game was decided before the line finished). */
        int unconsumed() { return script.length - pos; }
    }

    /** The two deterministic policies. {@code pass}: the do-nothing option wherever one exists.
     *  {@code first}: the first option listed. Neither draws from the seeded stream. */
    static final class Fixed extends ArmRandom {
        private static final long serialVersionUID = 1L;
        final boolean pass;
        Fixed(long seed, boolean pass) { super(seed); this.pass = pass; }
        // Context-free draws: option 0. nextBoolean() is only drawn by the block assignment, where
        // true means "this blocker does not block" -- the do-nothing answer for both policies.
        @Override public int nextInt(int bound) { note("i" + bound + ":0"); return 0; }
        @Override public boolean nextBoolean() { note("b:1"); return true; }
        @Override public long nextLong(long origin, long bound) { note("l" + origin + "," + bound + ":" + origin); return origin; }
        @Override int choose(int n, boolean lastIsDecline) {
            int v = pass && lastIsDecline ? n - 1 : 0;
            note("i" + n + ":" + v);
            return v;
        }
        @Override int size(int lo, int hi) {
            int v = pass ? lo : Math.max(lo, Math.min(1, hi));
            note("s" + lo + "," + hi + ":" + v);
            return v;
        }
    }
}
