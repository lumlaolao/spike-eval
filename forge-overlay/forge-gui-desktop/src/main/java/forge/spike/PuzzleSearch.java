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

    /** What a rollout did: every draw, whether Forge scored it a solve, and any drift from the prefix. */
    static final class Rollout {
        final List<Step> steps; final boolean solved; final String drift;
        Rollout(List<Step> steps, boolean solved, String drift) {
            this.steps = steps; this.solved = solved; this.drift = drift;
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
    }

    private final Game game;
    private final String solverSeat;
    private final int budget;
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
        Boolean ok = prove(new ArrayList<>(), main);
        r.replays = replays;
        r.drift = drift;
        r.opponentBranches = opponentBranches;
        if (drift != null) { r.verdict = Verdict.DRIFT; return r; }
        if (Boolean.TRUE.equals(ok)) {
            r.verdict = Verdict.PROVEN;
            r.line = main;
            for (Step s : main) if (!s.seat.equals(solverSeat) && s.bound > 1) r.opponentChoicePoints++;
        }
        return r;
    }

    int replays() { return replays; }

    private boolean outOfBudget() { return replays >= budget || drift != null; }

    /** True when every opponent reply after {@code prefix} is beaten; null when the budget ran out first.
     *  {@code mainOut} receives the winning line the proof rests on. */
    private Boolean prove(List<Step> prefix, List<Step> mainOut) {
        String key = key(prefix, prefix.size());
        Boolean memo = proven.get(key);
        if (memo != null) return memo;
        Set<String> tried = new HashSet<>();
        for (int attempt = 0; attempt < linesPerNode; attempt++) {
            // The root may spend the whole budget finding its line; an opponent branch gets a quarter of
            // what is left, so one unbeatable reply cannot starve the others.
            int share = prefix.isEmpty() ? budget - replays : Math.max(8, (budget - replays) / 4);
            List<Step> win = findWin(prefix, tried, share);
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
                    Boolean sub = prove(alt, null);
                    if (sub == null) return null;
                    if (!sub) holds = false;
                }
            }
            if (holds) {
                proven.put(key, Boolean.TRUE);
                if (mainOut != null) { mainOut.clear(); mainOut.addAll(win); }
                return Boolean.TRUE;
            }
            if (outOfBudget()) return null;
        }
        return outOfBudget() ? null : Boolean.FALSE;
    }

    /** A winning line through {@code prefix} not in {@code tried}, or null when {@code share} rollouts
     *  found none. */
    private List<Step> findWin(List<Step> prefix, Set<String> tried, int share) {
        for (int i = 0; i < share && !outOfBudget(); i++) {
            List<Step> start = select(prefix);
            Rollout ro = game.play(start, nextSeed++);
            replays++;
            if (ro.drift != null) { drift = ro.drift; return null; }
            if (!startsWith(ro.steps, prefix)) { drift = "rollout left its prefix"; return null; }
            update(ro);
            if (ro.solved && !tried.contains(key(ro.steps, ro.steps.size()))) return ro.steps;
        }
        return null;
    }

    /** Descend the UCT tree from {@code prefix}: an untried choice first, else the best by UCT (the
     *  opponent's best is the solver's worst). Returns the prefix to replay. */
    private List<Step> select(List<Step> prefix) {
        List<Step> path = new ArrayList<>(prefix);
        while (true) {
            Node n = tree.get(key(path, path.size()));
            if (n == null) return path;
            int pick = -1;
            List<Integer> untried = new ArrayList<>();
            for (int v = 0; v < n.bound; v++) if (n.visits[v] == 0) untried.add(v);
            if (!untried.isEmpty()) {
                pick = untried.get(rng.nextInt(untried.size()));
                path.add(new Step(n.seat, n.bound, pick));
                return path;
            }
            boolean mine = n.seat.equals(solverSeat);
            double best = -1;
            for (int v = 0; v < n.bound; v++) {
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
