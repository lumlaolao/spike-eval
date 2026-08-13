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
 *   CONTROLLER_A=...     solver arm: llm|random|floor|ai-log  (default llm)
 *   CONTROLLER_B=...     opponent arm: ai|ai-log|llm|random|floor (default ai)
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
        final Puzzle puzzle = new Puzzle(sections, id, false);

        String ctlA = System.getenv().getOrDefault("CONTROLLER_A", "llm");
        String ctlB = System.getenv().getOrDefault("CONTROLLER_B", "ai");
        requireOverridableSolver(ctlA);
        String smartA = System.getenv().getOrDefault("SMART_MODEL_A",
                System.getenv().getOrDefault("SMART_MODEL", "opus"));
        String fastA = System.getenv().getOrDefault("FAST_MODEL_A",
                System.getenv().getOrDefault("FAST_MODEL", "haiku"));

        long seed = LlmSpikeGui.applySeed();
        LlmSpikeGui.EvalLog.init(seed);

        // Empty decks and a zero-card opening hand: the [state] section IS the board. Anything drawn
        // here would be a card the puzzle author never put in the position.
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

        System.out.println("[PZL] " + id + "  goal=" + puzzle.getGoalDescription().replace("\n", " | "));
        System.out.println("[PZL] solver=" + ctlA + " opponent=" + ctlB + " seed=" + seed
                + " cap=" + (capMs / 1000) + "s");

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
                    System.out.println("[PZL] goal enforcement installed: " + goalInstalled[0]);
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
            worker.join(LlmSpikeGui.ModelCfg.maxConfiguredTimeoutMs() + 10_000L);
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
        try {
            for (GameLogEntry e : game.getGameLog().getAllEntries())
                System.out.println("  " + e.type() + ": " + e.message());
        } catch (Exception e) { System.out.println("  (log unavailable: " + e + ")"); }
        System.out.println("=================================");

        Map<String, Object> extra = new LinkedHashMap<>();
        extra.put("solver_loss_reason", solverLoss);
        extra.put("solver_loss_spell", solverLossSpell);
        extra.put("opponent_loss_reason", oppLoss);
        extra.put("opponent_loss_spell", oppLossSpell);
        extra.put("goal_bounded", bounded);
        extra.put("tainted_by_deckout", tainted);
        extra.put("taint_side", tainted ? ("Milled".equals(solverLoss) ? "solver" : "opponent") : "");
        writeRow(id, file, status, solved, turns, winCond, durMs, seed, ctlA, ctlB,
                smartA, goalInstalled[0], goalOracle[0], goal, extra);
        System.exit(0);
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
