package forge.spike;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.Set;

import forge.spike.PuzzleArms.Step;

/**
 * Proves a puzzle is solvable against every opponent response (eval/BENCHMARK-V1.md, phase 2).
 *
 * <p>A puzzle rollout with both seats on the {@code search} arm is a deterministic function of the engine
 * seed and the line of choices both seats make ({@link PuzzleArms.Line}). This class sees only that
 * function ({@link Game}): it replays prefixes and reads back who chose what from how many options and
 * whether the solver won. Forge decides every outcome; nothing here judges a position.
 *
 * <p>{@link #prove} finds a winning line, then, at every point on it where the opponent had a choice,
 * proves each other choice is also beaten (recursively, letting the solver answer differently after it).
 * A returned proof is a real strategy tree: every leaf is a rollout Forge scored as a solve. The search is
 * bounded by a replay budget, so "not proven" means "not found", never "unsolvable".
 *
 * <p>Finding a line is a UCT search over the same choice tree: solver choices maximise the solve rate,
 * opponent choices minimise it, so the lines it finds tend to survive the opponent's best replies.
 */
final class PuzzleSearch {

    /** One rollout: replay {@code prefix}, complete uniformly at random from {@code completionSeed}. */
    interface Game {
        Rollout play(List<Step> prefix, long completionSeed);
    }

    /** What a rollout did: every draw, whether Forge scored it a solve, any drift from the prefix, and what the
     *  solver did (its actions in the game log), which tells two solving lines apart. */
    static final class Rollout {
        final List<Step> steps; final boolean solved; final String drift; final String actions;
        Rollout(List<Step> steps, boolean solved, String drift) { this(steps, solved, drift, null); }
        Rollout(List<Step> steps, boolean solved, String drift, String actions) {
            this.steps = steps; this.solved = solved; this.drift = drift; this.actions = actions;
        }
    }

    enum Verdict { PROVEN, NOT_FOUND, DRIFT }

    /** The answer: the verdict, the winning line the proof starts from, and the size of the proof. */
    static final class Result {
        Verdict verdict = Verdict.NOT_FOUND;
        List<Step> line = null;          // the first winning line of the proof (the solver's main line)
        int replays = 0;
        int opponentBranches = 0;        // opponent alternatives the proof had to beat
        int opponentChoicePoints = 0;    // points on the main line where the opponent had 2+ options
        String drift = null;
        /** Every line from the start was played: NOT_FOUND then means no line wins against these replies. */
        boolean exhausted = false;
        /** When not proven: an opponent reply no line the search found could beat, as the line reaching it. */
        List<Step> refutation = null;
    }

    /** The uniqueness search's answer: "unique" (no second solution found within the budget, never a proof
     *  that none exists), "multiple" (a second strategy proven, in {@link #second}) or "drift". */
    static final class Uniqueness {
        String verdict = "unique";
        List<Step> second = null;
        String mainPrint = null, secondPrint = null;   // what told them apart (fingerprints: actions, then state)
        int deviations = 0;             // solver choices on the main line, each other option searched
        int exhausted = 0;              // deviations whose every line was played: none of them is a solution
        int replays = 0;
        int unprovenAlternatives = 0;   // other solving lines found that an opponent reply beat
        String drift = null;
    }

    private final Game game;
    private final String solverSeat;
    private final int budget;
    private int cap;                    // the replay count this search stops at (budget, or a deviation's share)
    private final int linesPerNode;
    private final double explore;
    private final Random rng;
    private long nextSeed;
    private int replays = 0;
    private String drift = null;

    /** UCT statistics per choice point, keyed by the prefix that reaches it. */
    private final Map<String, Node> tree = new HashMap<>();
    private final Map<String, Boolean> proven = new HashMap<>();
    private int opponentBranches = 0;
    private List<Step> refutation = null;
    /** A prove() call stopped at linesPerNode with lines under it unplayed: its FALSE is "none found", and nothing
     *  above it may claim every line was played (pr-review of PR #268, 2026-10-19). */
    private boolean gaveUp = false;

    private static final class Node {
        final String seat; final int bound;
        final int[] visits; final int[] wins;
        int total = 0;
        Node(String seat, int bound) {
            this.seat = seat; this.bound = bound; this.visits = new int[bound]; this.wins = new int[bound];
        }
    }

    PuzzleSearch(Game game, String solverSeat, int budget, int linesPerNode, long seed) {
        this.game = game;
        this.solverSeat = solverSeat;
        this.budget = budget;
        this.linesPerNode = linesPerNode;
        this.explore = Math.sqrt(2);
        this.rng = new Random(seed);
        this.nextSeed = seed * 1_000_003L + 17;
    }

    Result prove() {
        Result r = new Result();
        List<Step> main = new ArrayList<>();
        cap = budget;
        gaveUp = false;
        Boolean ok = prove(new ArrayList<>(), main, null, true);
        r.replays = replays;
        r.drift = drift;
        r.opponentBranches = opponentBranches;
        if (drift != null) { r.verdict = Verdict.DRIFT; return r; }
        if (Boolean.TRUE.equals(ok)) {
            r.verdict = Verdict.PROVEN;
            r.line = main;
            for (Step s : main) if (!s.seat.equals(solverSeat) && s.bound > 1) r.opponentChoicePoints++;
        } else {
            r.refutation = refutation;
            r.exhausted = !gaveUp && exhausted(new ArrayList<>());
        }
        return r;
    }

    int replays() { return replays; }

    /** Search for a second solution beside the proven {@code main}, with {@code extra} more replays. Any other
     *  solving line leaves the main line at a choice of the solver's or of the opponent's; the opponent's are
     *  the proof's branches, so each other option at each solver choice is searched in turn for a line that
     *  does something different (other solver actions) and is itself proven against every opponent reply. */
    Uniqueness unique(List<Step> main, int extra) {
        Uniqueness u = new Uniqueness();
        int start = replays;
        Rollout m = game.play(main, nextSeed++);
        replays++;
        if (m.drift != null || !m.solved) {
            u.verdict = "drift";
            u.drift = m.drift != null ? m.drift : "the main line did not solve on replay";
            u.replays = replays - start;
            return u;
        }
        String mainPrint = fingerprint(m);
        List<List<Step>> devs = new ArrayList<>();
        for (int j = 0; j < main.size(); j++) {
            Step s = main.get(j);
            if (!s.seat.equals(solverSeat) || s.bound < 2) continue;
            for (int v = 0; v < s.bound; v++) {
                if (v == s.value) continue;
                List<Step> dev = new ArrayList<>(main.subList(0, j));
                dev.add(new Step(s.seat, s.bound, v));
                devs.add(dev);
            }
        }
        u.deviations = devs.size();
        int end = replays + extra;
        int altsBefore = unprovenAlternatives;
        int searchedOut = 0;   // deviations searched to FALSE without giving up anywhere
        for (int i = 0; i < devs.size() && replays < end && drift == null; i++) {
            cap = replays + Math.max(4, (end - replays) / (devs.size() - i));
            List<Step> found = new ArrayList<>();
            gaveUp = false;
            Boolean ok = prove(devs.get(i), found, mainPrint, true);
            if (Boolean.FALSE.equals(ok) && !gaveUp && exhausted(devs.get(i))) searchedOut++;
            if (Boolean.TRUE.equals(ok)) {
                u.verdict = "multiple";
                u.second = found;
                u.mainPrint = mainPrint;
                u.secondPrint = fingerprint(solves.get(key(found, found.size())));
                break;
            }
        }
        if (drift != null) { u.verdict = "drift"; u.drift = drift; }
        if ("unique".equals(u.verdict)) u.exhausted = searchedOut;
        u.unprovenAlternatives = unprovenAlternatives - altsBefore;
        u.replays = replays - start;
        return u;
    }

    private int unprovenAlternatives = 0;

    /** What the solver did in a rollout: its game-log actions, or (a toy game) its draws. */
    private String fingerprint(Rollout ro) {
        if (ro.actions != null) return ro.actions;
        StringBuilder sb = new StringBuilder();
        for (Step s : ro.steps) if (s.seat.equals(solverSeat)) sb.append(s).append(' ');
        return sb.toString();
    }

    private boolean outOfBudget() { return replays >= cap || drift != null; }

    /** True when every opponent reply after {@code prefix} is beaten; null when the budget ran out first.
     *  {@code mainOut} receives the winning line the proof rests on. {@code avoid}: a solver fingerprint the
     *  winning line must not have (the uniqueness search; it applies at the top only, since under an opponent
     *  branch the solver may well answer as the main line does). {@code top}: this call may spend all of the
     *  remaining budget finding its line. */
    private Boolean prove(List<Step> prefix, List<Step> mainOut, String avoid, boolean top) {
        String key = key(prefix, prefix.size()) + (avoid == null ? "" : "|avoid");
        Boolean memo = proven.get(key);
        if (memo != null) return memo;
        Set<String> tried = new HashSet<>();
        for (int attempt = 0; attempt < linesPerNode; attempt++) {
            // The root may spend the whole budget finding its line; an opponent branch gets a quarter of
            // what is left, so one unbeatable reply cannot starve the others.
            int share = top ? cap - replays : Math.max(8, (cap - replays) / 4);
            List<Step> win = findWin(prefix, tried, share, avoid);
            if (win == null) return outOfBudget() ? null : Boolean.FALSE;
            tried.add(key(win, win.size()));
            boolean holds = true;
            for (int j = prefix.size(); j < win.size() && holds; j++) {
                Step s = win.get(j);
                if (s.seat.equals(solverSeat) || s.bound < 2) continue;
                for (int v = 0; v < s.bound && holds; v++) {
                    if (v == s.value) continue;
                    List<Step> alt = new ArrayList<>(win.subList(0, j));
                    alt.add(new Step(s.seat, s.bound, v));
                    opponentBranches++;
                    Boolean sub = prove(alt, null, null, false);
                    if (sub == null) return null;
                    if (!sub) {
                        holds = false;
                        if (avoid != null) unprovenAlternatives++;
                        else if (refutation == null || alt.size() < refutation.size()) refutation = alt;
                    }
                }
            }
            if (holds) {
                proven.put(key, Boolean.TRUE);
                if (mainOut != null) { mainOut.clear(); mainOut.addAll(win); }
                return Boolean.TRUE;
            }
            if (outOfBudget()) return null;
        }
        if (outOfBudget()) return null;
        gaveUp = true;
        return Boolean.FALSE;
    }

    /** A winning line through {@code prefix} not in {@code tried}, or null when {@code share} rollouts
     *  found none. */
    private List<Step> findWin(List<Step> prefix, Set<String> tried, int share, String avoid) {
        // A solve already played through this prefix (a played-out subtree is never replayed, so its solves
        // are only here).
        String pk = key(prefix, prefix.size());
        for (Map.Entry<String, Rollout> e : solves.entrySet()) {
            Rollout ro = e.getValue();
            if (e.getKey().startsWith(pk) && !tried.contains(e.getKey())
                    && (avoid == null || !avoid.equals(fingerprint(ro)))) return ro.steps;
        }
        for (int i = 0; i < share && !outOfBudget() && !exhausted(prefix); i++) {
            List<Step> start = select(prefix);
            Rollout ro = game.play(start, nextSeed++);
            replays++;
            if (ro.drift != null) { drift = ro.drift; return null; }
            if (!startsWith(ro.steps, prefix)) { drift = "rollout left its prefix"; return null; }
            update(ro);
            String k = key(ro.steps, ro.steps.size());
            terminal.add(k);
            if (ro.solved) solves.put(k, ro);
            if (ro.solved && !tried.contains(k)
                    && (avoid == null || !avoid.equals(fingerprint(ro)))) return ro.steps;
            if (exhausted(prefix)) return null;
        }
        return null;
    }

    /** Lines a rollout ended on: replaying one draws nothing more, so it is the only line below its key. */
    private final Set<String> terminal = new HashSet<>();
    private final Set<String> exhaustedKeys = new HashSet<>();
    /** Every solving line played, by its key, in the order found. */
    private final Map<String, Rollout> solves = new java.util.LinkedHashMap<>();

    /** Every line through {@code path} has been played (the rollouts are deterministic given their line). */
    boolean exhausted(List<Step> path) { return exhausted(key(path, path.size())); }

    private boolean exhausted(String k) {
        if (terminal.contains(k) || exhaustedKeys.contains(k)) return true;
        Node n = tree.get(k);
        if (n == null) return false;
        for (int v = 0; v < n.bound; v++) if (!exhausted(k + new Step(n.seat, n.bound, v) + " ")) return false;
        exhaustedKeys.add(k);
        return true;
    }

    /** Descend the UCT tree from {@code prefix}, never into a played-out subtree: an untried choice first, else
     *  the best by UCT (the opponent's best is the solver's worst). Returns the prefix to replay. */
    private List<Step> select(List<Step> prefix) {
        List<Step> path = new ArrayList<>(prefix);
        while (true) {
            String here = key(path, path.size());
            Node n = tree.get(here);
            if (n == null) return path;
            int pick = -1;
            List<Integer> untried = new ArrayList<>();
            boolean[] done = new boolean[n.bound];
            for (int v = 0; v < n.bound; v++) done[v] = exhausted(here + new Step(n.seat, n.bound, v) + " ");
            for (int v = 0; v < n.bound; v++) if (n.visits[v] == 0 && !done[v]) untried.add(v);
            if (!untried.isEmpty()) {
                pick = untried.get(rng.nextInt(untried.size()));
                path.add(new Step(n.seat, n.bound, pick));
                return path;
            }
            boolean mine = n.seat.equals(solverSeat);
            double best = -1;
            for (int v = 0; v < n.bound; v++) {
                if (done[v]) continue;
                double rate = n.wins[v] / (double) n.visits[v];
                double score = (mine ? rate : 1 - rate) + explore * Math.sqrt(Math.log(n.total) / n.visits[v]);
                if (score > best) { best = score; pick = v; }
            }
            path.add(new Step(n.seat, n.bound, pick));
        }
    }

    /** Count the rollout at every tree node it passed through, and add the first node it reached beyond. */
    private void update(Rollout ro) {
        int win = ro.solved ? 1 : 0;
        for (int i = 0; i < ro.steps.size(); i++) {
            Step s = ro.steps.get(i);
            if (s.bound < 2) continue;
            String k = key(ro.steps, i);
            Node n = tree.get(k);
            boolean added = false;
            if (n == null) { n = new Node(s.seat, s.bound); tree.put(k, n); added = true; }
            if (s.value < n.bound) { n.visits[s.value]++; n.wins[s.value] += win; n.total++; }
            if (added) return;
        }
    }

    static boolean startsWith(List<Step> steps, List<Step> prefix) {
        if (steps.size() < prefix.size()) return false;
        for (int i = 0; i < prefix.size(); i++) if (!steps.get(i).equals(prefix.get(i))) return false;
        return true;
    }

    /** The prefix of {@code steps} of length {@code n} as a map key. Single-option draws are kept: they
     *  are part of where the game is. */
    static String key(List<Step> steps, int n) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < n; i++) sb.append(steps.get(i)).append(' ');
        return sb.toString();
    }

    /** A line as text: "Solver:3:1 Opponent:2:0 ...". */
    static String lineText(List<Step> steps) {
        return steps == null ? "" : key(steps, steps.size()).trim();
    }
}
