package forge.spike;

import java.io.File;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import forge.LobbyPlayer;
import forge.ai.LobbyPlayerAi;
import forge.deck.Deck;
import forge.game.Game;
import forge.game.GameEndReason;
import forge.game.GameLogEntry;
import forge.game.GameOutcome;
import forge.game.GameRules;
import forge.game.GameType;
import forge.game.Match;
import forge.game.card.Card;
import forge.game.player.PlayerOutcome;
import forge.game.player.RegisteredPlayer;
import forge.game.zone.ZoneType;
import forge.gamemodes.puzzle.Puzzle;
import forge.localinstance.properties.ForgeConstants;
import forge.util.FileSection;
import forge.util.FileUtil;
import forge.util.MyRandom;

/**
 * PZL axis: run ONE Forge puzzle headless and report solved / not solved.
 *
 * WHY THIS EXISTS AS ITS OWN ENTRY POINT. A puzzle is not a shortened match. It has no decks
 * (both libraries are empty), no mulligan, no opening draw, a hand-authored board applied after
 * the game object is built, and — the part that makes it scoreable at all — a hidden enforcement
 * card that ends the game in a loss at the turn limit. Threading that through the ordinary
 * match path would have meant branching {@link LlmSpikeHeadless} in five places; it is a
 * separate constructor instead.
 *
 * WHAT ADJUDICATES. Nothing here. {@code Puzzle.addGoalEnforcement} (forge-gui, unmodified at the
 * pinned commit) installs a "Puzzle Goal" card in the solver's command zone whose trigger makes
 * the solver LOSE at their cleanup step on the limit turn unless the goal is met. There is no
 * mined answer key, no rollout, and no scoring code of ours in the loop: the rules decide, and
 * this class only reads {@code GameOutcome} afterwards. That is the whole value of the axis, and
 * it is why the seat-selection fix lives in {@link LlmSpikeGui#puzzleSolverSeat} rather than in a
 * forked copy of Puzzle.java — a copy would be free to drift from the engine it claims to be.
 *
 * SEATING. Player 0 is the solver, player 1 the opponent, matching Forge's own puzzle screen
 * (CSubmenuPuzzleSolve adds the human first). Puzzles flagged {@code HumanControl:True} give
 * player 0 control of player 1, so the ordering is load-bearing, not cosmetic.
 *
 * Env:
 *   PUZZLE=<id|path>     required. Bare id resolves against PUZZLE_DIRS.
 *   PUZZLE_DIRS=a:b      search path (default: Forge's res/puzzle, then $PWD/eval/puzzles/upstream)
 *   CONTROLLER_A=...     solver arm: llm|random|floor|ai-log|pass|first|script (default llm);
 *                        pass/first/script are the zero-model puzzle arms in {@link PuzzleArms}
 *   CONTROLLER_B=...     opponent arm: ai|ai-log|llm|random|floor (default ai)
 *   PZL_SEEDS=1..200     many rollouts in one JVM, one result row each (default: one at GAME_SEED)
 *   PZL_TAPE=<file>      the draw tape the script arm replays (a stored reference line)
 *   PZL_TAPE_ALL=1       keep every rollout's tape (default: only solved rollouts and single runs)
 *   CONTROLLER_A=search  prove the puzzle with {@link PuzzleSearch} instead of one rollout per seed. With
 *                        CONTROLLER_B=search every opponent choice must be beaten; with ai, the Forge AI's.
 *   PZL_SEARCH_BUDGET=N  rollouts the search may play (default 2000); PZL_SEARCH_LINES=N winning lines
 *                        tried per choice point before giving up on it (default 4)
 *   SMART_MODEL_A / FAST_MODEL_A / GAME_SEED / EVAL_OUT / GAME_CAP_MS as usual.
 *
 * Run: PUZZLE=PS_MSH1 CONTROLLER_A=random bash run-llm-headless.sh
 */
public final class LlmSpikePuzzle {
    private LlmSpikePuzzle() {}

    /** Resolve PUZZLE to a readable .pzl. Accepts a path, a path without the suffix, or a bare id
     *  looked up across PUZZLE_DIRS. Fails loudly with every directory it tried — a puzzle silently
     *  resolving to the wrong file would mis-attribute a score to the wrong position. */
    static File resolve(String spec, List<String> dirs) {
        List<String> tried = new ArrayList<>();
        if (spec.indexOf(File.separatorChar) >= 0 || spec.indexOf('/') >= 0) {
            for (String cand : new String[] {spec, spec + ".pzl"}) {
                File f = new File(cand);
                tried.add(f.getPath());
                if (f.isFile()) return f;
            }
        } else {
            for (String dir : dirs) {
                for (String cand : new String[] {spec, spec + ".pzl"}) {
                    File f = new File(dir, cand);
                    tried.add(f.getPath());
                    if (f.isFile()) return f;
                }
            }
        }
        throw new RuntimeException("PUZZLE=" + spec + " not found; tried: " + tried);
    }

    static List<String> searchDirs() {
        String env = System.getenv("PUZZLE_DIRS");
        List<String> dirs = new ArrayList<>();
        if (env != null && !env.trim().isEmpty()) {
            for (String d : env.split(File.pathSeparator)) if (!d.trim().isEmpty()) dirs.add(d.trim());
            return dirs;
        }
        dirs.add(ForgeConstants.PUZZLE_DIR);
        // Puzzles that exist in Forge master but not at the pinned commit are vendored in the eval
        // repo; the launcher cd's into forge-gui-desktop, so reach back out to the user's checkout.
        String repo = System.getenv("PZL_REPO");
        if (repo != null && !repo.trim().isEmpty()) dirs.add(new File(repo, "eval/puzzles/upstream").getPath());
        return dirs;
    }

    /** Goal strings Puzzle.addGoalEnforcement has an explicit `case` for (Puzzle.java:144-209),
     *  lowercased exactly as it compares them. Anything else reaches its `default:` and is silently
     *  enforced as plain "win the game by turn N" -- the puzzle still RUNS and still produces a
     *  confident solved/failed, it is just answering a different question than the author asked.
     *  All 300 rows of pzl-v1 hit a real case today; this list exists so that stops being true
     *  loudly rather than silently the first time the suite grows. */
    static final java.util.Set<String> ENFORCED_GOALS = new java.util.HashSet<>(java.util.Arrays.asList(
            "win", "survive", "win before opponent's next turn",
            "destroy specified permanents", "destroy specified creatures",
            "remove specified permanents from the battlefield", "kill specified creatures",
            "put the specified permanent on the battlefield", "play the specified permanent",
            "gain control of specified permanents"));

    /** Refuse to score a goal Forge would silently downgrade. PZL_ALLOW_UNENFORCED_GOAL=1 runs it
     *  anyway, for deliberately probing what the fallthrough does. */
    static void requireEnforcedGoal(String goal) {
        String g = goal == null ? "" : goal.trim().toLowerCase();
        if (ENFORCED_GOALS.contains(g)) return;
        if ("1".equals(System.getenv("PZL_ALLOW_UNENFORCED_GOAL"))) {
            System.out.println("[PZL] WARNING: goal '" + goal + "' has no case in Puzzle.addGoalEnforcement; "
                    + "it is being enforced as plain 'win by turn N'. Result is NOT a score.");
            return;
        }
        throw new RuntimeException("goal '" + goal + "' has no case in Puzzle.addGoalEnforcement "
                + "(Puzzle.java:144-209) and would silently fall through to 'win the game by turn N'. "
                + "Add the goal to Forge's switch, or set PZL_ALLOW_UNENFORCED_GOAL=1 to run it "
                + "knowing the verdict answers a different question than the puzzle asks.");
    }

    /** Read `key=value` out of a FileSection body, case-insensitively, or `def`. */
    static String sectionValue(List<String> lines, String key, String def) {
        if (lines == null) return def;
        for (String line : lines) {
            String[] kv = line.split("[=:]", 2);
            if (kv.length == 2 && kv[0].trim().equalsIgnoreCase(key)) return kv[1].trim();
        }
        return def;
    }

    static int intOr(String s, int def) {
        try { return Integer.parseInt(s.trim()); } catch (Exception e) { return def; }
    }

    /** Is the turn limit actually reachable?
     *
     *  The enforcement trigger is `TurnCount$ <Turns + turnCorr>` and Forge compares it with
     *  STRICT EQUALITY -- `phaseHandler.getTurn() != turn` (Trigger.java:281-286). But a .pzl
     *  [state] may also set a STARTING turn number (`turn=2`), which GameState feeds to
     *  devModeSet, and addGoalEnforcement never offsets its limit by it (Puzzle.java:132-137).
     *  So a puzzle with `Turns:1` and `turn=2` waits forever for a turn 1 that already went by:
     *  the goal card is installed, looks correct, and can never fire. The solver then has
     *  UNLIMITED turns, and "solved" silently degrades from "won within the limit" to "won
     *  eventually". This is not hypothetical -- PS_STX2 (Turns:1, turn=2) ran to turn 8 under the
     *  Forge-AI arm and was scored a solve. 6 of the 300 suite rows are in this shape. */
    static boolean goalIsBounded(Map<String, List<String>> sections) {
        int turns = intOr(sectionValue(sections.get("metadata"), "Turns", "1"), 1);
        int startTurn = intOr(sectionValue(sections.get("state"), "turn", "1"), 1);
        boolean startsInCleanup =
                "CLEANUP".equalsIgnoreCase(sectionValue(sections.get("state"), "activephase", ""));
        return turns + (startsInCleanup ? 1 : 0) >= startTurn;
    }

    /** Every seat except plain "ai" is one of our controllers. The Forge-AI arm must be "ai-log",
     *  not "ai": a stock LobbyPlayerAi builds a PlayerControllerAi we cannot override, so it can
     *  never answer isGuiPlayer() and the goal card would have nowhere to go. Rejecting it here
     *  turns a mid-game NPE into a startup message that names the fix. */
    static void requireOverridableSolver(String ctl) {
        if ("ai".equals(ctl))
            throw new RuntimeException("CONTROLLER_A=ai cannot solve puzzles (stock PlayerControllerAi "
                    + "cannot be designated as the goal-bearing seat). Use CONTROLLER_A=ai-log for the "
                    + "Forge-AI floor arm; it is the same AI with decision logging.");
    }

    /** Arms that may sit in the solver seat. Plain "ai" cannot (see requireOverridableSolver); any
     *  other value would be built as a model seat, so a typo must not silently call a model. */
    static final java.util.Set<String> SOLVER_ARMS = new java.util.HashSet<>(java.util.Arrays.asList(
            "llm", "random", "floor", "ai-log", "pass", "first", "script", "search"));

    static void requireSolverArm(String ctl) {
        requireOverridableSolver(ctl);
        if (!SOLVER_ARMS.contains(ctl))
            throw new RuntimeException("CONTROLLER_A=" + ctl + " is not a solver arm " + new java.util.TreeSet<>(SOLVER_ARMS)
                    + "; anything else would be built as a model seat");
    }

    /** PZL_SEEDS: "a..b" (inclusive) and/or "a,b,c". Empty or null -> null (one run at GAME_SEED). */
    static List<Long> parseSeeds(String spec) {
        if (spec == null || spec.trim().isEmpty()) return null;
        List<Long> out = new ArrayList<>();
        for (String part : spec.split(",")) {
            String t = part.trim();
            if (t.isEmpty()) continue;
            int dots = t.indexOf("..");
            if (dots > 0) {
                long a = Long.parseLong(t.substring(0, dots).trim());
                long b = Long.parseLong(t.substring(dots + 2).trim());
                if (b < a) throw new IllegalArgumentException("PZL_SEEDS range " + t + " runs backwards");
                if (b - a >= 100_000) throw new IllegalArgumentException("PZL_SEEDS range " + t + " is too large");
                for (long x = a; x <= b; x++) out.add(x);
            } else {
                out.add(Long.parseLong(t));
            }
        }
        return out;
    }

    /** The solver's own actions in the game log (cast, activate, land, attack). Their count is the
     *  length of a line; the lines themselves are the readable form of a reference solution. */
    static List<String> solverActions(List<String> logLines, String solverName) {
        List<String> out = new ArrayList<>();
        for (String l : logLines) {
            int colon = l.indexOf(": ");
            if (colon < 0) continue;
            String type = l.substring(0, colon);
            String msg = l.substring(colon + 2);
            if (("STACK_ADD".equals(type) && (msg.startsWith(solverName + " cast ") || msg.startsWith(solverName + " activated ")))
                    || ("LAND".equals(type) && msg.startsWith(solverName + " played "))
                    || ("COMBAT".equals(type) && msg.startsWith(solverName + " assigned ")))
                out.add(l);
        }
        return out;
    }

    /** sha256 of the game log without its wall-clock line ("Match Duration 0m : 01s"), which is the
     *  only part of a deterministic replay that differs from run to run. Two rollouts with the same
     *  fingerprint played the same game; the verifier relies on that. */
    static String logFingerprint(List<String> logLines) {
        List<String> kept = new ArrayList<>();
        for (String l : logLines) if (!l.startsWith("GAME_OUTCOME: Match Duration")) kept.add(l);
        return sha256(String.join("\n", kept));
    }

    /** The sha256 of the .pzl file's bytes, as eval/puzzle_format.py freezes it (sha256_file). It hashed the text
     *  Forge's reader returns, which differs (line ends), so puzzle_score.py matched no result to its puzzle. */
    static String fileSha256(String path) {
        try {
            byte[] d = java.security.MessageDigest.getInstance("SHA-256")
                    .digest(java.nio.file.Files.readAllBytes(java.nio.file.Paths.get(path)));
            StringBuilder sb = new StringBuilder();
            for (byte b : d) sb.append(String.format("%02x", b));
            return sb.toString();
        } catch (Exception e) {
            return "";
        }
    }

    static String sha256(String s) {
        try {
            byte[] d = java.security.MessageDigest.getInstance("SHA-256")
                    .digest(s.getBytes(java.nio.charset.StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder();
            for (byte b : d) sb.append(String.format("%02x", b));
            return sb.toString();
        } catch (Exception e) {
            return "";
        }
    }

    public static void run(String spec) throws Exception {
        long capMs = 10 * 60 * 1000L; // a puzzle is <=3 turns; 3h would hide a hang, not survive one
        try { String v = System.getenv("GAME_CAP_MS"); if (v != null) capMs = Long.parseLong(v.trim()); } catch (Exception ignore) {}

        File file = resolve(spec, searchDirs());
        String id = file.getName().replaceAll("\\.pzl$", "");
        Map<String, List<String>> sections = FileSection.parseSections(FileUtil.readFile(file));
        if (sections.get("metadata") == null || sections.get("state") == null)
            throw new RuntimeException(file + ": missing [metadata] or [state] section");
        // Read Goal straight from [metadata]: Puzzle keeps its parsed copy package-private, and
        // getGoalDescription() interleaves it with free text we would have to unpick.
        String goal = "";
        for (String line : sections.get("metadata")) {
            String[] kv = line.split(":", 2);
            if (kv.length == 2 && "Goal".equalsIgnoreCase(kv[0].trim())) goal = kv[1].trim();
        }
        requireEnforcedGoal(goal);
        final boolean bounded = goalIsBounded(sections);
        final String pzlSha = fileSha256(String.valueOf(file));

        String ctlA = System.getenv().getOrDefault("CONTROLLER_A", "llm");
        String ctlB = System.getenv().getOrDefault("CONTROLLER_B", "ai");
        requireSolverArm(ctlA);
        String smartA = System.getenv().getOrDefault("SMART_MODEL_A",
                System.getenv().getOrDefault("SMART_MODEL", "opus"));
        String fastA = System.getenv().getOrDefault("FAST_MODEL_A",
                System.getenv().getOrDefault("FAST_MODEL", "haiku"));

        // The reference line the script arm replays: a draw tape written by an earlier random rollout.
        if ("script".equals(ctlA)) {
            String tapeFile = System.getenv("PZL_TAPE");
            if (tapeFile == null || tapeFile.trim().isEmpty())
                throw new RuntimeException("CONTROLLER_A=script needs PZL_TAPE=<file holding the draw tape>");
            PuzzleArms.scriptTape = FileUtil.readFileToString(new File(tapeFile.trim())).trim();
        }

        if ("search".equals(ctlA)) {
            search(file, id, sections, goal, bounded, pzlSha, ctlB, smartA, fastA, capMs);
            LlmSpikeGui.EvalLog.closeForTest();
            System.exit(0);
        }

        // Many rollouts share one JVM (startup is ~10 s, a puzzle ~1 s). Each rollout re-seeds the
        // engine RNG and builds a fresh Match, Game and seats, as a fresh launch would.
        List<Long> seeds = parseSeeds(System.getenv("PZL_SEEDS"));
        boolean multi = seeds != null;
        if (!multi) {
            seeds = new ArrayList<>();
            seeds.add(LlmSpikeGui.applySeed());
        }
        boolean keepAllTapes = !multi || "1".equals(System.getenv("PZL_TAPE_ALL"));
        int rollout = 0;
        for (long seed : seeds) {
            if (multi) {
                MyRandom.setRandom(new java.util.Random(seed));
                LlmSpikeGui.SEED_USED = seed;
                LlmSpikeGui.EvalLog.closeForTest();
            }
            LlmSpikeGui.EvalLog.init(seed);
            boolean healthy = runOnce(file, id, sections, goal, bounded, pzlSha, ctlA, ctlB, smartA, fastA,
                    seed, capMs, rollout++, keepAllTapes);
            if (!healthy) {
                System.out.println("[PZL] the game thread outlived the cap at seed " + seed
                        + "; later rollouts would share the JVM with it, so the run stops here");
                break;
            }
        }
        LlmSpikeGui.EvalLog.closeForTest();
        System.exit(0);
    }

    /** CONTROLLER_A=search: prove the puzzle on one engine seed and write one result row. Every rollout
     *  replays from the start position on the same seed; only the line of choices differs. The proof's main
     *  line is then replayed twice more and must give the same game log both times, or the result is drift. */
    static void search(File file, String id, Map<String, List<String>> sections, String goal, boolean bounded,
                       String pzlSha, String ctlB, String smartA, String fastA, long capMs) throws Exception {
        List<Long> seeds = parseSeeds(System.getenv("PZL_SEEDS"));
        final long seed = seeds != null && !seeds.isEmpty() ? seeds.get(0) : LlmSpikeGui.applySeed();
        int budget = intOr(System.getenv().getOrDefault("PZL_SEARCH_BUDGET", "2000"), 2000);
        int lines = intOr(System.getenv().getOrDefault("PZL_SEARCH_LINES", "4"), 4);
        System.out.println("[PZL] search " + id + " opponent=" + ctlB + " seed=" + seed + " budget=" + budget);
        final int[] n = {0};
        final boolean[] broken = {false};
        PuzzleSearch.Game g = (prefix, completionSeed) -> {
            if (broken[0]) return new PuzzleSearch.Rollout(new ArrayList<>(prefix), false, "game thread outlived the cap");
            PuzzleArms.Line line = new PuzzleArms.Line(prefix, completionSeed);
            if (n[0] > 0 && n[0] % 100 == 0) System.out.println("[PZL] search " + id + ": " + n[0] + " rollouts");
            Played p;
            try {
                p = replay(file, id, sections, ctlB, smartA, fastA, seed, capMs, n[0]++, bounded, line);
            } catch (Exception e) {
                return new PuzzleSearch.Rollout(new ArrayList<>(line.taken), false, "rollout threw: " + e);
            }
            if (!p.healthy) broken[0] = true;
            return new PuzzleSearch.Rollout(new ArrayList<>(line.taken), p.cleanSolve(), line.drift);
        };
        long t0 = System.currentTimeMillis();
        PuzzleSearch.Result r = bounded ? new PuzzleSearch(g, "Solver", budget, lines, seed).prove() : null;
        String status = r == null ? "unscoreable" : r.verdict == PuzzleSearch.Verdict.PROVEN ? "proven"
                : r.verdict == PuzzleSearch.Verdict.DRIFT ? "drift" : "not_found";
        Map<String, Object> extra = new LinkedHashMap<>();
        Played main = null;
        if ("proven".equals(status)) {
            main = replay(file, id, sections, ctlB, smartA, fastA, seed, capMs, n[0]++, bounded,
                    new PuzzleArms.Line(r.line, 0));
            Played again = replay(file, id, sections, ctlB, smartA, fastA, seed, capMs, n[0]++, bounded,
                    new PuzzleArms.Line(r.line, 0));
            if (!main.cleanSolve() || !logFingerprint(main.logLines).equals(logFingerprint(again.logLines))) {
                status = "drift";
                extra.put("search_drift", "the main line did not replay to the same solve");
            }
        }
        long durMs = System.currentTimeMillis() - t0;
        extra.put("pzl_sha256", pzlSha);
        extra.put("goal_bounded", bounded);
        if (r != null) {
            extra.put("search_replays", r.replays);
            extra.put("search_budget", budget);
            extra.put("search_lines_per_point", lines);
            extra.put("opponent_choice_points", r.opponentChoicePoints);
            extra.put("opponent_branches_beaten", r.opponentBranches);
            if (r.drift != null) extra.put("search_drift", r.drift);
            if (r.line != null) extra.put("line", PuzzleSearch.lineText(r.line));
        }
        if (main != null) {
            List<String> actions = solverActions(main.logLines, "Solver");
            extra.put("log_sha256", logFingerprint(main.logLines));
            extra.put("solver_actions", actions.size());
            extra.put("actions", actions);
        }
        System.out.println("[PZL] search " + id + ": " + status + (r == null ? "" : " after " + r.replays
                + " rollouts, " + r.opponentBranches + " opponent replies beaten") + " (" + (durMs / 1000) + "s)");
        writeRow(id, file, status, "proven".equals(status), main == null ? -1 : main.turns,
                main == null ? "?" : main.winCond, durMs, seed, "search", ctlB, "",
                main != null && main.goalInstalled, main == null ? "" : main.goalOracle, goal, extra);
    }

    /** One search rollout: the engine re-seeded as for a fresh launch, both search seats drawing from {@code line}. */
    static Played replay(File file, String id, Map<String, List<String>> sections, String ctlB, String smartA,
                         String fastA, long seed, long capMs, int rollout, boolean bounded,
                         PuzzleArms.Line line) throws Exception {
        MyRandom.setRandom(new java.util.Random(seed));
        LlmSpikeGui.SEED_USED = seed;
        PuzzleArms.searchLine = line;
        try {
            return playOnce(file, id, sections, "search", ctlB, smartA, fastA, seed, capMs, rollout, bounded, true);
        } finally {
            PuzzleArms.searchLine = null;
        }
    }

    /** Play one rollout and read the outcome; {@code quiet} prints nothing (a search plays thousands). */
    static Played playOnce(File file, String id, Map<String, List<String>> sections, String ctlA, String ctlB,
                           String smartA, String fastA, long seed, long capMs, int rollout, boolean bounded,
                           boolean quiet) throws Exception {
        final Puzzle puzzle = new Puzzle(sections, id, false);

        // Empty decks and a zero-card opening hand: the [state] section IS the board. Anything drawn
        // here would be a card the puzzle author never put in the position.
        // The solver is told the goal as the author wrote it, before its seat (and prompt) is built.
        LlmSpikeGui.puzzleBrief = puzzle.getGoalDescription();
        LobbyPlayer solverSeat = LlmSpikeGui.seatFor(ctlA, "Solver", "", smartA, fastA);
        LobbyPlayer oppSeat = "ai".equals(ctlB) ? new LobbyPlayerAi("Opponent", null)
                : LlmSpikeGui.seatFor(ctlB, "Opponent", "", smartA, fastA);
        RegisteredPlayer solver = new RegisteredPlayer(new Deck()).setPlayer(solverSeat);
        solver.setStartingHand(0);
        RegisteredPlayer opp = new RegisteredPlayer(new Deck()).setPlayer(oppSeat);
        opp.setStartingHand(0);

        // Designate the goal-bearing seat BEFORE the game is built. See LlmSpikeGui.puzzleSolverSeat.
        LlmSpikeGui.puzzleSolverSeat = solverSeat;

        GameRules rules = new GameRules(GameType.Puzzle);
        rules.setGamesPerMatch(1);
        List<RegisteredPlayer> players = new ArrayList<>();
        players.add(solver);
        players.add(opp);
        Match match = new Match(rules, players, "LlmPuzzle");
        final Game game = match.createGame();

        if (rollout == 0 && !quiet) System.out.println("[PZL] " + id + "  goal=" + puzzle.getGoalDescription().replace("\n", " | "));
        if (!quiet) System.out.println("[PZL] solver=" + ctlA + " opponent=" + ctlB + " seed=" + seed
                + " cap=" + (capMs / 1000) + "s rollout=" + rollout);

        // Set inside the start-game hook, immediately after the board is applied. This observes the
        // real post-apply state rather than racing it ONLY because the worker below is named
        // "Game-puzzle" -- see the thread-name note there.
        final boolean[] goalInstalled = {false};
        final String[] goalOracle = {""};

        long t0 = System.currentTimeMillis();
        Thread worker = new Thread(() -> {
            try {
                match.startGame(game, () -> {
                    puzzle.applyToGame(game);
                    for (Card c : game.getPlayers().get(0).getZone(ZoneType.Command)) {
                        if ("Puzzle Goal".equals(c.getName())) {
                            goalInstalled[0] = true;
                            goalOracle[0] = String.valueOf(c.getOracleText());
                        }
                    }
                    if (!quiet) System.out.println("[PZL] goal enforcement installed: " + goalInstalled[0]);
                });
            } catch (Throwable t) {
                System.out.println("[PZL] game thread threw: " + t);
                t.printStackTrace(System.out);
            }
        // THREAD NAME IS LOAD-BEARING. ThreadUtil.isGameThread() is literally
        // `Thread.currentThread().getName().startsWith("Game")`, and GameState.applyToGame routes
        // through GameAction.invoke, which runs its runnable INLINE on a game thread but posts it to
        // a background pool otherwise. Named "forge-puzzle" the board application was asynchronous:
        // the hook returned before any of the puzzle state existed, the goal card check read an
        // empty command zone, and the first turn began on a blank board -- the floor solver "won"
        // PS_THB1b on turn 2 because the opponent decked out of an empty library. Do not rename this
        // thread without re-reading ThreadUtil.
        }, "Game-puzzle");
        worker.setDaemon(true);
        worker.start();
        worker.join(capMs);
        boolean capped = worker.isAlive();
        if (capped) {
            System.out.println("[PZL] wall-clock cap reached; this puzzle is INVALID, not a failure");
            game.setGameOver(GameEndReason.Draw);
            // maxDecisionMs(), not maxConfiguredTimeoutMs(): a null reply sends the router into
            // its retry loop, where each further attempt gets a FULL fresh timeout. Budgeting
            // for one timeout meant a game capped on attempt 2 or 3 was killed with the ask
            // still in flight -- the silent drop this grace exists to prevent.
            worker.join(LlmSpikeGui.ModelCfg.maxDecisionMs() + 10_000L);
        }
        long durMs = System.currentTimeMillis() - t0;

        // A run where the goal card never got installed is UNSCOREABLE, not a loss: without it the
        // solver simply never loses on time, so "not solved" would be indistinguishable from
        // "harness broken". This is the check that makes the axis honest, so it outranks the result.
        boolean solved = false;
        int turns = -1;
        String winCond = "?";
        try {
            GameOutcome outcome = game.getOutcome();
            if (outcome != null) {
                solved = outcome.isWinner(solverSeat);
                turns = outcome.getLastTurnNumber();
                winCond = String.valueOf(outcome.getWinCondition());
            }
        } catch (Exception ignore) {}

        // DECK-OUT TAINT. Puzzle libraries are hand-authored and usually near-empty: 275 of the 300
        // suite rows give a player fewer library cards than the turn limit needs, and 8 multi-turn
        // rows give the opponent ZERO. Whoever draws first from an empty library loses on a state-based
        // action, which decides the game for reasons that have nothing to do with the puzzle's line --
        // in BOTH directions. A do-nothing solver can be handed a free "solve" when the opponent decks
        // out, and a correct solver can be denied one when it decks out first. So the reason each side
        // lost is recorded, not just who won: a solve whose opponent was Milled is not evidence.
        String solverLoss = lossOf(game, 0), oppLoss = lossOf(game, 1);
        String solverLossSpell = lossSpellOf(game, 0), oppLossSpell = lossSpellOf(game, 1);
        boolean tainted = "Milled".equals(solverLoss) || "Milled".equals(oppLoss);

        // An unbounded goal is UNSCOREABLE, not a result: the card is installed and the game runs,
        // but the limit never arrives, so a "solve" means "won eventually" rather than "won in N".
        String status = capped ? "invalid"
                : (!goalInstalled[0] || !bounded) ? "unscoreable"
                : (solved ? "solved" : "failed");

        List<String> logLines = new ArrayList<>();
        try {
            for (GameLogEntry e : game.getGameLog().getAllEntries()) logLines.add(e.type() + ": " + e.message());
        } catch (Exception e) {
            logLines.add("INFORMATION: (log unavailable: " + e + ")");
        }
        Played pl = new Played();
        pl.game = game; pl.solverSeat = solverSeat; pl.status = status; pl.solved = solved; pl.turns = turns;
        pl.winCond = winCond; pl.durMs = durMs; pl.goalInstalled = goalInstalled[0]; pl.goalOracle = goalOracle[0];
        pl.solverLoss = solverLoss; pl.oppLoss = oppLoss; pl.solverLossSpell = solverLossSpell;
        pl.oppLossSpell = oppLossSpell; pl.tainted = tainted; pl.logLines = logLines; pl.healthy = !worker.isAlive();
        return pl;
    }


    /** What one rollout did, read off the finished game. */
    static final class Played {
        Game game; LobbyPlayer solverSeat;
        String status, winCond = "?", solverLoss, oppLoss, solverLossSpell, oppLossSpell;
        boolean solved, tainted, goalInstalled, healthy;
        String goalOracle = "";
        int turns = -1;
        long durMs;
        List<String> logLines = new ArrayList<>();
        /** A solve that counts: Forge scored it solved, and no empty library decided it. */
        boolean cleanSolve() { return "solved".equals(status) && !tainted; }
    }

    /** One rollout. Returns false when the game thread is still alive after the cap and its grace
     *  (the JVM is then not safe for another rollout). */
    static boolean runOnce(File file, String id, Map<String, List<String>> sections, String goal, boolean bounded,
                           String pzlSha, String ctlA, String ctlB, String smartA, String fastA, long seed,
                           long capMs, int rollout, boolean keepTape) throws Exception {
        Played pl = playOnce(file, id, sections, ctlA, ctlB, smartA, fastA, seed, capMs, rollout, bounded, false);
        final Game game = pl.game;
        final LobbyPlayer solverSeat = pl.solverSeat;
        final boolean[] goalInstalled = {pl.goalInstalled};
        final String[] goalOracle = {pl.goalOracle};
        boolean capped = "invalid".equals(pl.status);
        boolean solved = pl.solved;
        int turns = pl.turns;
        String winCond = pl.winCond;
        long durMs = pl.durMs;
        String solverLoss = pl.solverLoss, oppLoss = pl.oppLoss;
        String solverLossSpell = pl.solverLossSpell, oppLossSpell = pl.oppLossSpell;
        boolean tainted = pl.tainted;
        String status = pl.status;
        List<String> logLines = pl.logLines;
        List<String> actions = solverActions(logLines, "Solver");

        System.out.println("========== PUZZLE OVER ==========");
        System.out.println("puzzle : " + id);
        System.out.println("status : " + status
                + "  (turns=" + turns + ", " + winCond + ", " + (durMs / 1000) + "s)");
        System.out.println("loss   : solver=" + solverLoss + (solverLossSpell.isEmpty() ? "" : "/" + solverLossSpell)
                + "  opponent=" + oppLoss + (oppLossSpell.isEmpty() ? "" : "/" + oppLossSpell));
        if (!goalInstalled[0] && !capped)
            System.out.println("WARNING: no 'Puzzle Goal' card in the solver's command zone -- the turn "
                    + "limit was NOT enforced, so this result is not a score.");
        if (!bounded)
            System.out.println("WARNING: the goal trigger's TurnCount can never equal the game's turn "
                    + "counter (state starts past the limit), so the solver had UNLIMITED turns -- "
                    + "UNSCOREABLE, see LlmSpikePuzzle.goalIsBounded.");
        if (tainted)
            System.out.println("WARNING: decided by an empty-library deck-out (Milled), not by the puzzle "
                    + "line -- this row is TAINTED and must not be pooled into a solve rate.");
        System.out.println("--- game log ---");
        for (String l : logLines) System.out.println("  " + l);
        System.out.println("=================================");

        Map<String, Object> extra = new LinkedHashMap<>();
        extra.put("solver_loss_reason", solverLoss);
        extra.put("solver_loss_spell", solverLossSpell);
        extra.put("opponent_loss_reason", oppLoss);
        extra.put("opponent_loss_spell", oppLossSpell);
        extra.put("goal_bounded", bounded);
        extra.put("tainted_by_deckout", tainted);
        extra.put("taint_side", tainted ? ("Milled".equals(solverLoss) ? "solver" : "opponent") : "");
        // Puzzle-suite fields (eval/PUZZLES.md), additive: which rollout, which exact position, a
        // fingerprint of what happened (the verifier compares it), and the line when it is wanted.
        extra.put("rollout", rollout);
        extra.put("pzl_sha256", pzlSha);
        // Which frozen suite version this attempt was run for (eval/puzzle_score.py refuses to pool versions).
        String suiteSha = System.getenv("PZL_SUITE_SHA256");
        if (suiteSha != null && !suiteSha.trim().isEmpty()) extra.put("suite_sha256", suiteSha.trim());
        extra.put("log_sha256", logFingerprint(logLines));
        extra.put("solver_actions", actions.size());
        java.util.Random arm = null;
        try {
            Object ctl = game.getRegisteredPlayers().get(0).getController();
            if (ctl instanceof LlmSpikeGui.PlayerControllerLLM) arm = ((LlmSpikeGui.PlayerControllerLLM) ctl).armRandom();
        } catch (Exception ignore) {}
        if (arm instanceof PuzzleArms.ArmRandom) {
            PuzzleArms.ArmRandom ar = (PuzzleArms.ArmRandom) arm;
            extra.put("draws", ar.draws());
            if (keepTape || solved) extra.put("tape", ar.tapeString());
            if (ar instanceof PuzzleArms.Script) {
                extra.put("script_drift", ar.drift == null ? "" : ar.drift);
                extra.put("script_exhausted", ar.exhausted);
                extra.put("script_unconsumed", ((PuzzleArms.Script) ar).unconsumed());
            }
        }
        if (keepTape || solved) extra.put("actions", actions);
        writeRow(id, file, status, solved, turns, winCond, durMs, seed, ctlA, ctlB,
                smartA, goalInstalled[0], goalOracle[0], goal, extra);
        return pl.healthy;
    }

    /** Seat i's PlayerOutcome, read off the STABLE player list.
     *
     *  {@code Game.getPlayers()} returns `ingamePlayers` -- "the players who are still fighting to
     *  win" (Game.java:400) -- and Forge REMOVES a player from it the moment they lose
     *  (Game.onPlayerLost -> ingamePlayers.remove, Game.java:998), which runs from
     *  checkGameOverCondition BEFORE setGameOver. So in a finished 2-player puzzle that list holds
     *  one element: index 0 is the WINNER and index 1 is out of bounds. Reading loss reasons off it
     *  silently reported "no deck-out anywhere" on runs whose log plainly said otherwise, which
     *  would have understated the taint rate as exactly zero. `allPlayers` behind
     *  getRegisteredPlayers() is only ever appended to (Game.java:347), in the order the seats were
     *  registered, so index 0 stays the solver. */
    static PlayerOutcome outcomeOf(Game game, int i) {
        try { return game.getRegisteredPlayers().get(i).getOutcome(); }
        catch (Exception e) { return null; }
    }

    /** GameLossReason for seat index i, or "" if that seat did not lose / the game never ended. */
    static String lossOf(Game game, int i) {
        PlayerOutcome po = outcomeOf(game, i);
        return po == null || po.lossState == null ? "" : po.lossState.name();
    }

    /** The spell that caused a SpellEffect loss ("Puzzle Goal" when the turn limit fired), else "". */
    static String lossSpellOf(Game game, int i) {
        PlayerOutcome po = outcomeOf(game, i);
        return po == null || po.loseConditionSpell == null ? "" : po.loseConditionSpell;
    }

    /** One JSONL row per puzzle into $EVAL_OUT/puzzle-results.jsonl. Additive-only schema, same
     *  contract as the match summaries: consumers read via .get(). */
    static void writeRow(String id, File file, String status, boolean solved, int turns,
                         String winCond, long durMs, long seed, String ctlA, String ctlB,
                         String smartA, boolean goalInstalled, String goalOracle, String goal,
                         Map<String, Object> extra) {
        String out = System.getenv("EVAL_OUT");
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("schema", "pzl-result-v1");
        row.put("id", id);
        row.put("source_file", file.getPath());
        row.put("status", status);
        row.put("solved", solved);
        row.put("goal_enforcement_installed", goalInstalled);
        row.put("goal_oracle", goalOracle);
        row.put("turns", turns);
        row.put("win_condition", winCond);
        row.put("duration_ms", durMs);
        row.put("seed", seed);
        row.put("solver_arm", ctlA);
        row.put("opponent_arm", ctlB);
        row.put("solver_model", "llm".equals(ctlA) ? smartA : "");
        row.put("goal", goal);
        row.putAll(extra);
        String line = toJson(row);
        System.out.println("PZL_RESULT " + line);
        if (out == null || out.isEmpty()) return;
        try {
            File dir = new File(out);
            if (!dir.exists()) dir.mkdirs();
            try (PrintWriter pw = new PrintWriter(new FileWriter(new File(dir, "puzzle-results.jsonl"), true))) {
                pw.println(line);
            }
        } catch (Exception e) {
            System.out.println("[PZL] could not write EVAL_OUT row: " + e);
        }
    }

    static String toJson(Map<String, Object> m) {
        StringBuilder sb = new StringBuilder("{");
        boolean first = true;
        for (Map.Entry<String, Object> e : m.entrySet()) {
            if (!first) sb.append(',');
            first = false;
            sb.append('"').append(esc(e.getKey())).append("\":");
            Object v = e.getValue();
            if (v instanceof Boolean || v instanceof Number) sb.append(v);
            else if (v instanceof List) {
                sb.append('[');
                boolean firstItem = true;
                for (Object o : (List<?>) v) {
                    if (!firstItem) sb.append(',');
                    firstItem = false;
                    sb.append('"').append(esc(String.valueOf(o))).append('"');
                }
                sb.append(']');
            }
            else sb.append('"').append(esc(String.valueOf(v))).append('"');
        }
        return sb.append('}').toString();
    }

    static String esc(String s) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
                    else sb.append(c);
            }
        }
        return sb.toString();
    }
}
