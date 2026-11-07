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
 *   <li>{@code search}: {@link Search}, for {@link PuzzleSearch}. Both seats draw from one shared {@link Line}:
 *       a prefix of choices to replay, then a seeded uniform completion. Every draw is written down with the
 *       seat that made it and how many options it had, so a search knows who chose what from how many.</li>
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
        return "random".equals(mode) || "script".equals(mode) || "pass".equals(mode) || "first".equals(mode)
                || "search".equals(mode);
    }

    /** The tape the next {@code script} seat replays. Set by LlmSpikePuzzle before each rollout. */
    static volatile String scriptTape = "";
    /** The last arm Random built, so the runner can read its tape/drift after the game. */
    static volatile ArmRandom lastBuilt = null;

    /** The draw source for {@code mode}, or null for a seat that is not one of these arms. {@code seed}
     *  is the long the random arm has always drawn from MyRandom at construction; every arm draws it,
     *  so the engine's own stream (and with it the opponent's AI) is the same across arms. */
    static Random forMode(String mode, long seed) { return forMode(mode, seed, ""); }

    /** The picks of a repeatable-mode choice ("you may choose the same mode more than once"), as option indices. The
     *  random and other arms draw the most modes allowed (at most 8), each uniform, in order: the stream the canaries
     *  are pinned on. The search arm draws how many, from min (zero when the card allows it, as Forge's own controllers
     *  do) to that cap, then a multiset in non-decreasing order: "up to N" is tried with fewer modes, and orders of the
     *  same modes are one branch, not several (#269). */
    static List<Integer> repeatModes(Random r, int options, int min, int num) {
        return repeatModes(r, options, min, num, null, -1);
    }

    /** As above, for a pawprint charm when {@code cost} is given: each mode costs {@code cost[i]} {P} and the picks
     *  total at most {@code budget} (#415). Each pick is drawn from the modes that still fit, with "stop" as the last
     *  option once {@code min} are picked; on the search arm from the last pick's mode on, so a multiset is one branch.
     *  No canary deck holds a pawprint card, so no pinned stream moves. */
    static List<Integer> repeatModes(Random r, int options, int min, int num, int[] cost, int budget) {
        int hi = Math.max(1, Math.min(num, 8));
        List<Integer> picks = new ArrayList<>();
        if (cost != null && budget >= 0) {
            boolean search = r instanceof Search;
            int left = budget, from = 0;
            while (picks.size() < hi) {
                List<Integer> fit = new ArrayList<>();
                for (int j = search ? from : 0; j < options; j++) if (cost[j] <= left) fit.add(j);
                if (fit.isEmpty()) break;
                boolean canStop = picks.size() >= min;
                int n = fit.size() + (canStop ? 1 : 0);
                int v = r.nextInt(n);
                if (v == fit.size()) break;                  // stop
                int pick = fit.get(v);
                picks.add(pick);
                left -= cost[pick];
                from = pick;
            }
            return picks;
        }
        if (r instanceof Search) {
            Search s = (Search) r;
            int k = s.size(Math.max(0, Math.min(min, hi)), hi);
            int from = 0;
            for (int i = 0; i < k; i++) {
                from += s.nextInt(options - from);
                picks.add(from);
            }
            return picks;
        }
        for (int i = 0; i < hi; i++) picks.add(r.nextInt(options));
        return picks;
    }

    /** As {@link #forMode(String, long)}; {@code seat} names who draws, which only the search arm records. */
    static Random forMode(String mode, long seed, String seat) {
        ArmRandom r;
        switch (mode == null ? "" : mode) {
            case "random": r = new Tape(seed); break;
            case "script": r = new Script(seed, scriptTape); break;
            case "pass":   r = new Fixed(seed, true); break;
            case "first":  r = new Fixed(seed, false); break;
            case "search": r = new Search(seed, seat); break;
            default: return null;
        }
        lastBuilt = r;
        return r;
    }

    /** One choice from {@code n} options; {@code lastIsDecline} says option n-1 is the do-nothing one. */
    static int pick(Random r, int n, boolean lastIsDecline) {
        if (SETUP) return 0;
        if (r instanceof ArmRandom) return ((ArmRandom) r).choose(n, lastIsDecline);
        return r.nextInt(n);
    }

    /** How many items a random subset of lo..hi takes (draws only when hi > lo, as the old code did). */
    static int subsetSize(Random r, int lo, int hi) {
        if (SETUP) return lo;
        if (r instanceof ArmRandom) return ((ArmRandom) r).size(lo, hi);
        return lo + (hi > lo ? r.nextInt(hi - lo + 1) : 0);
    }

    /** Shuffle the candidate order for a subset pick; the fixed arms keep menu order. */
    static void shuffle(List<Integer> items, Random r) {
        if (r instanceof Fixed) return;
        Collections.shuffle(items, r);
    }

    /** The subset a decideMany menu of {@code n} options takes, {@code lo}..{@code hi} items, or null when
     *  {@code hi < lo}. Every arm but search makes exactly the draws decideMany always made (shuffle the
     *  indices, then draw a size, take the first that many), so no stream moves. A shuffle reaches one
     *  subset by up to n! orders, which a search would branch over in vain; the search arm draws one
     *  include-or-not per option instead, skipping the draws {@code lo} and {@code hi} force. */
    static List<Integer> subset(Random r, int n, int lo, int hi) {
        if (r instanceof Search) {
            if (hi < lo) return null;
            List<Integer> out = new ArrayList<>();
            for (int i = 0; i < n; i++) {
                int left = n - i;
                if (out.size() >= hi) break;
                if (lo - out.size() >= left || r.nextBoolean()) out.add(i);
            }
            return out;
        }
        List<Integer> all = new ArrayList<>();
        for (int i = 0; i < n; i++) all.add(i);
        shuffle(all, r);
        if (hi < lo) return null;
        return new ArrayList<>(all.subList(0, subsetSize(r, lo, hi)));
    }

    /** An order of n options, one draw per place (n, n-1, ... 2 options): the search arm's answer to an order ask,
     *  where {@link #subset} would keep the given order. */
    static List<Integer> permutation(Random r, int n) {
        List<Integer> left = new ArrayList<>();
        for (int i = 0; i < n; i++) left.add(i);
        List<Integer> out = new ArrayList<>();
        while (!left.isEmpty()) out.add(left.remove(r.nextInt(left.size())));
        return out;
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

    /** The line both search seats draw from in one rollout: {@code prefix} choices replayed in order, then
     *  uniform draws from {@code completion}. Set by the search driver before each rollout. */
    static volatile Line searchLine = null;

    /** True while the puzzle's board is put down: a choice asked then (Pithing Needle's name, rules pilot RP_207) is
     *  the .pzl's to set, so every arm takes the first option and nothing is drawn or recorded. */
    static volatile boolean SETUP = false;

    /** One draw of a search rollout: the seat that drew, how many options it had, which it took. */
    static final class Step {
        final String seat; final int bound; final int value;
        Step(String seat, int bound, int value) { this.seat = seat; this.bound = bound; this.value = value; }
        @Override public String toString() { return seat + ":" + bound + ":" + value; }
        @Override public boolean equals(Object o) {
            return o instanceof Step && ((Step) o).seat.equals(seat) && ((Step) o).bound == bound && ((Step) o).value == value;
        }
        @Override public int hashCode() { return (seat.hashCode() * 31 + bound) * 31 + value; }
    }

    /** A rollout's shared draw source. A prefix step whose seat or option count differs from what the game
     *  asks is DRIFT (the replay left the line it was meant to follow): it is recorded, and the rest of the
     *  rollout completes at random, so the driver can refuse the rollout. */
    static final class Line {
        final List<Step> prefix;
        final Random completion;
        final List<Step> taken = new ArrayList<>();
        String drift = null;
        /** Draws past which the rollout is stopped (never inside the prefix): a random completion can loop
         *  (an ability activated forever) until the wall-clock cap or the heap ends it. */
        final int maxDraws;
        private volatile boolean stopped = false;

        Line(List<Step> prefix, long completionSeed) { this(prefix, completionSeed, MAX_DRAWS); }

        Line(List<Step> prefix, long completionSeed, int maxDraws) {
            this.prefix = prefix == null ? new ArrayList<>() : prefix;
            this.completion = new Random(completionSeed);
            this.maxDraws = maxDraws;
        }

        /** What the solver chose where the game log does not say (LlmSpikeGui.noteSolverChoice), in order. */
        final List<String> choices = new ArrayList<>();

        synchronized void note(String choice) { if (!stopped) choices.add(choice); }

        synchronized void clearChoices() { choices.clear(); }

        /** Withdraws the choices noted after the first {@code keep} (an answer the engine refused never happened). */
        synchronized void dropChoicesAfter(int keep) { while (choices.size() > keep) choices.remove(choices.size() - 1); }

        /** True once the rollout passed {@link #maxDraws}: the runner ends the game, unsolved. */
        boolean stopped() { return stopped; }

        synchronized int draw(String seat, int bound) {
            if (SETUP) return 0;
            int at = taken.size();
            int v;
            if (drift == null && at < prefix.size()) {
                Step want = prefix.get(at);
                if (!want.seat.equals(seat) || want.bound != bound) {
                    drift = "draw " + at + ": line has " + want + ", game asked " + seat + ":" + bound;
                    v = completion.nextInt(bound);
                } else {
                    v = want.value;
                }
            } else {
                v = completion.nextInt(bound);
            }
            if (stopped) return v;   // draws after the stop depend on when the game thread sees it: not kept
            if (taken.size() < TAPE_CAP_STEPS) taken.add(new Step(seat, bound, v));
            if (taken.size() >= maxDraws && taken.size() > prefix.size()) stopped = true;
            return v;
        }
        static final int TAPE_CAP_STEPS = 200_000;
        /** A proven puzzle's line is tens of draws; PZL_SEARCH_MAX_DRAWS overrides. */
        static final int MAX_DRAWS = maxDrawsFromEnv();

        private static int maxDrawsFromEnv() {
            try {
                String v = System.getenv("PZL_SEARCH_MAX_DRAWS");
                if (v != null) return Math.max(1, Integer.parseInt(v.trim()));
            } catch (NumberFormatException ignore) { }
            return 3000;
        }
    }

    /** The search arm: every draw comes from {@link #searchLine}, as a choice among a counted set of options. */
    static final class Search extends ArmRandom {
        private static final long serialVersionUID = 1L;
        final String seat;
        Search(long seed, String seat) { super(seed); this.seat = seat == null ? "" : seat; }

        private int draw(int bound) {
            Line l = searchLine;
            int v = l == null ? 0 : l.draw(seat, bound);
            note(seat + ":" + bound + ":" + v);
            return v;
        }
        @Override public int nextInt(int bound) { return bound <= 1 ? 0 : draw(bound); }
        @Override public boolean nextBoolean() { return draw(2) == 1; }
        @Override public long nextLong(long origin, long bound) {
            long span = bound - origin;
            if (span <= 1) return origin;
            if (span > Integer.MAX_VALUE) span = Integer.MAX_VALUE;
            return origin + draw((int) span);
        }
        @Override int choose(int n, boolean lastIsDecline) { return nextInt(n); }
        @Override int size(int lo, int hi) { return lo + (hi > lo ? nextInt(hi - lo + 1) : 0); }
    }
}
