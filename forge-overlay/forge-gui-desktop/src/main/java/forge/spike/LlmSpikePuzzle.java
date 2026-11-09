package forge.spike;

import java.io.File;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.io.Writer;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import forge.LobbyPlayer;
import forge.deck.Deck;
import forge.game.Game;
import forge.game.GameEndReason;
import forge.game.GameLog;
import forge.game.GameLogEntry;
import forge.game.GameOutcome;
import forge.game.GameRules;
import forge.game.GameType;
import forge.game.Match;
import forge.game.card.Card;
import forge.game.player.Player;
import forge.game.player.PlayerOutcome;
import forge.game.player.RegisteredPlayer;
import forge.game.trigger.Trigger;
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
 *   PUZZLE_DIRS=a:b      search path (default: Forge's res/puzzle)
 *   CONTROLLER_A=...     solver arm: llm|random|floor|ai-log|pass|first|script (default llm);
 *                        pass/first/script are the zero-model puzzle arms in {@link PuzzleArms}
 *   CONTROLLER_B=...     opponent arm: ai|ai-log|llm|random|floor (default ai)
 *   PZL_SEEDS=1..200     many rollouts in one JVM, one result row each (default: one at GAME_SEED)
 *   PZL_TAPE=<file>      the draw tape the script arm replays (a stored reference line)
 *   PZL_TAPE_ALL=1       keep every rollout's tape (default: only solved rollouts and single runs)
 *   CONTROLLER_A=search  prove the puzzle with {@link PuzzleSearch} instead of one rollout per seed. With
 *                        CONTROLLER_B=search every opponent choice must be beaten; with ai, the Forge AI's.
 *   PZL_SEARCH_BUDGET=N  rollouts the search may play (default 2000); PZL_SEARCH_LINES=N winning lines
 *                        tried per choice point before giving up on it (default 32)
 *   PZL_PROBE="Solver:2:1 Opponent:3:0"   with CONTROLLER_A=search: the misreading probe ({@link #probeRun}); a seeded
 *                        random play past the line (PZL_PROBE_COMPLETION_SEED, default 0).
 *   SMART_MODEL_A / FAST_MODEL_A / GAME_SEED / EVAL_OUT / GAME_CAP_MS as usual. SMART_MODEL_A=FAST_MODEL_A=
 *                        replay:<game.jsonl|attempt dir> with CONTROLLER_A=llm replays a recorded attempt
 *                        ({@link ReplayBridge}) on the same seeds (PZL_SEEDS / GAME_SEED) and the recorded env.
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

    /** A card name chosen while the board is put down ("As Pithing Needle enters, choose a card name") is added to by
     *  the .pzl's {@code NamedCard:}, not replaced (GameState.handleChosenEntities uses addNamedCard), so the Needle
     *  held both names (rules pilot RP_207). The .pzl's names are set exactly; GameState keeps them in a private
     *  map, read here. */
    @SuppressWarnings("unchecked")
    static void restoreNamedCards(forge.game.GameState state) {
        try {
            java.lang.reflect.Field f = forge.game.GameState.class.getDeclaredField("cardToNamedCard");
            f.setAccessible(true);
            for (Map.Entry<Card, List<String>> e : ((Map<Card, List<String>>) f.get(state)).entrySet())
                e.getKey().setNamedCards(new java.util.ArrayList<>(e.getValue()));
        } catch (ReflectiveOperationException | RuntimeException e) {
            System.out.println("[PZL] NamedCard not restored: " + e);
        }
    }

    static List<String> searchDirs() {
        String env = System.getenv("PUZZLE_DIRS");
        List<String> dirs = new ArrayList<>();
        if (env != null && !env.trim().isEmpty()) {
            for (String d : env.split(File.pathSeparator)) if (!d.trim().isEmpty()) dirs.add(d.trim());
            return dirs;
        }
        dirs.add(ForgeConstants.PUZZLE_DIR);
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

    /** What the solver is told: never Forge's name or difficulty label (the author's guess at the difficulty, and a
     *  name that can state the trick: rules pilot RP_443, "Bolster picks the least toughness"). A rules puzzle (an
     *  [acceptance] section) gets its author's description and turn limit only, since its Forge Goal is just the turn
     *  bound (usually Survive, above a description asking for the opponent at 0 life). A Forge puzzle keeps Forge's
     *  goal line, turn limit and description, from its Goal line on: the goal there is the one Forge enforces.
     *  Forge's HumanControl warning stays in both: the solver then decides for the opponent too. */
    static String seatBrief(Map<String, List<String>> sections, String forgeDescription) {
        String warning = forgeDescription.startsWith(HUMAN_CONTROL)
                ? forgeDescription.substring(0, forgeDescription.indexOf("\n\n") + 2) : "";
        if (sections.get("acceptance") == null) {
            int goal = forgeDescription.indexOf("\n\nGoal: ", warning.length());
            return goal < 0 ? forgeDescription : warning + forgeDescription.substring(goal + 2);
        }
        return warning + "Turns Limit: " + turnLimit(sections) + "\n\n"
                + sectionValue(sections.get("metadata"), "Description", "").replace("\\n", "\n");
    }

    /** How Forge's getGoalDescription opens for a HumanControl puzzle (Puzzle.java). */
    static final String HUMAN_CONTROL = "\nWARNING: This puzzle is human-controlled";

    static int turnLimit(Map<String, List<String>> sections) {
        return intOr(sectionValue(sections.get("metadata"), "Turns", "1"), 1);
    }

    /** The solver's brief ({@link #seatBrief}), stored in LlmSpikeGui.puzzleBrief: call it before the solver's seat
     *  is built, whose system prompt reads it. */
    static void briefSolver(Map<String, List<String>> sections, Puzzle puzzle) {
        LlmSpikeGui.puzzleBrief = seatBrief(sections, puzzle.getGoalDescription());
    }

    /** The zones of the solver's own cards its YOUR CARDS block lists: the ones it can see. Its library is not one (an
     *  author's library is the solver's hidden draw, and Forge keeps its order), nor is the opponent's side. The
     *  per-decision block reads the same zones in play (SeatObservation.OWN_SEEN_ZONES), with the stack added. */
    private static final java.util.Set<String> SEEN_ZONES =
            java.util.Set.of("hand", "battlefield", "play", "graveyard", "exile", "command");

    /** What the solver's seat is told about its own cards (#391): the YOUR CARDS block a game seat gets from its deck
     *  (LlmSpikeGui.cardText, Forge's card database), here from the cards the [state] section puts on the solver's side
     *  (p0, or "human": the zones it controls) in the zones it can see, with each card's type line. A face-down card is
     *  listed only on the battlefield or foretold (CR 406.3: a face-down exiled card is hidden unless an effect lets its
     *  owner look). Cards it sees later are SeatObservation's (YOUR CARDS SINCE THE START). DECKLIST=0, the game seat's
     *  gate, gives none; so does a [state] it cannot read (logged). */
    static String solverPlan(Map<String, List<String>> sections) {
        if ("0".equals(LlmSpikeGui.env("DECKLIST"))) return "";
        try {
            Deck own = new Deck();
            List<String> state = sections.get("state");
            if (state != null) for (String line : state) {
                String[] kv = line.split("=", 2);
                if (kv.length != 2) continue;
                String key = kv[0].trim().toLowerCase();
                String zone = key.startsWith("p0") ? key.substring(2) : key.startsWith("human") ? key.substring(5) : null;
                if (zone == null || !SEEN_ZONES.contains(zone)) continue;
                boolean onBattlefield = zone.equals("battlefield") || zone.equals("play");
                for (String element : kv[1].split(";")) {
                    String[] parts = element.split("\\|");
                    String name = parts[0].trim();
                    if (name.isEmpty() || name.toLowerCase().startsWith("t:")) continue;   // a token: no database card
                    boolean faceDown = false, foretold = false;
                    for (int i = 1; i < parts.length; i++) {
                        faceDown |= parts[i].trim().startsWith("FaceDown");
                        foretold |= parts[i].trim().startsWith("Foretold");
                    }
                    if (faceDown && !onBattlefield && !foretold) continue;
                    forge.item.PaperCard pc = forge.StaticData.instance().getCommonCards().getCard(name);
                    if (pc != null) own.getMain().add(pc);
                }
            }
            String text = LlmSpikeGui.cardText(own, true);
            return text.isEmpty() ? "" : "YOUR CARDS (what each of your cards in this puzzle does; your library is not listed):\n" + text;
        } catch (Exception e) {
            System.out.println("[PUZZLE] the solver's YOUR CARDS could not be built (" + e + "): none");
            return "";
        }
    }

    /** The solver's seat, as playOnce seats it: its plan is {@link #solverPlan}. */
    static LobbyPlayer solverSeat(Map<String, List<String>> sections, String ctlA, String smartA, String fastA) {
        return LlmSpikeGui.seatFor(ctlA, "Solver", solverPlan(sections), smartA, fastA);
    }

    /** The opponent's seat, as playOnce seats it: AI_PROFILE_B names a Forge AI opponent's profile (empty: the compiled
     *  defaults, the opponent PUZ always met; puzzle_solve.py strips the names). */
    static LobbyPlayer opponentSeat(String ctlB, String smartA, String fastA) {
        return LlmSpikeGui.seatFor(ctlB, "Opponent", "", smartA, fastA, LlmSpikeGui.env("AI_PROFILE_B"));
    }

    /** Every rollout's start hook, on the game thread: the board (choices made while it is put down are the
     *  .pzl's), the named cards Forge's setup drops, and on the Puzzle Goal card the brief the solver's prompt
     *  reads (LlmSpikeGui.puzzleBrief, set by {@link #briefSolver}), so the two cannot differ. */
    static void applyBoard(Game game, Puzzle puzzle) {
        PuzzleArms.SETUP = true;
        try { puzzle.applyToGame(game); } finally { PuzzleArms.SETUP = false; }
        restoreNamedCards(puzzle);
        tellGoalCard(game, LlmSpikeGui.puzzleBrief);
        LlmSpikeGui.puzzleLastTurn = lastTurn(game);
    }

    /** The last turn the game can be played in, from the Puzzle Goal card's turn trigger (Puzzle.addGoalEnforcement):
     *  the turn whose cleanup the solver loses at, or the turn before the upkeep it wins at (Survive). -1 when no
     *  trigger counts turns ("win before opponent's next turn", a permanent goal with no limit). */
    static int lastTurn(Game game) {
        for (Player p : game.getPlayers())
            for (Card c : p.getZone(ZoneType.Command))
                if ("Puzzle Goal".equals(c.getName()))
                    for (Trigger t : c.getTriggers())
                        if (t.hasParam("TurnCount")) {
                            int n = Integer.parseInt(t.getParam("TurnCount"));
                            return "Upkeep".equalsIgnoreCase(t.getParam("Phase")) ? n - 1 : n;
                        }
        return -1;
    }

    /** Forge writes its whole goal description on the Puzzle Goal card, which `inspect` reads: the card gets the
     *  solver's brief, so a rules puzzle's name and "Goal: Survive" stay hidden there too. */
    static void tellGoalCard(Game game, String brief) {
        for (Player p : game.getPlayers())
            for (Card c : p.getZone(ZoneType.Command))
                if ("Puzzle Goal".equals(c.getName())) c.setOracleText(brief);
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
        int turns = turnLimit(sections);
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
        // A combat entry holds one line per attacker ("Solver didn't block A" then "Solver assigned B to block C"):
        // each line is read, or a block after a "didn't block" line was lost (rules pilot RP_108).
        List<String> lines = new ArrayList<>();
        for (String entry : logLines) {
            int colon = entry.indexOf(": ");
            if (colon < 0) continue;
            String prefix = entry.substring(0, colon + 2);
            for (String part : entry.substring(colon + 2).split("\n")) lines.add(prefix + part);
        }
        for (String l : lines) {
            int colon = l.indexOf(": ");
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
        return sha256(String.join("\n", withoutWallClock(logLines)));
    }

    /** The game log without its wall-clock line, the part {@link #logFingerprint} leaves out. */
    static List<String> withoutWallClock(List<String> logLines) {
        List<String> kept = new ArrayList<>();
        for (String l : logLines) if (!l.startsWith("GAME_OUTCOME: Match Duration")) kept.add(l);
        return kept;
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
        // A rules puzzle's acceptance state: read now, so a line it cannot read stops the run before any game.
        if (sections.get("acceptance") != null) PuzzleAcceptance.parse(sections.get("acceptance"));
        final boolean bounded = goalIsBounded(sections);
        final String pzlSha = fileSha256(String.valueOf(file));

        String ctlA = System.getenv().getOrDefault("CONTROLLER_A", "llm");
        String ctlB = System.getenv().getOrDefault("CONTROLLER_B", "ai");
        requireSolverArm(ctlA);
        String missingModel = LlmSpikeGui.missingModel(ctlA, "A", System.getenv());
        if (missingModel != null) throw new IllegalStateException(missingModel);
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

        // The prover: both seats on the search arm, or a written line (ScriptBridge) against the search opponent, which
        // then proves that one fixed line beats every reply the engine offers (HARD-PUZZLES.md section 4, item 2).
        boolean writtenSolver = "llm".equals(ctlA) && smartA.startsWith(ScriptBridge.PREFIX);
        // Step regret: a recorded attempt replayed for its first REPLAY_HANDOVER decision rows, then searched.
        if ("llm".equals(ctlA) && smartA.startsWith(ReplayBridge.PREFIX) && System.getenv("REPLAY_HANDOVER") != null) {
            stepSearchMain(file, id, sections, bounded, pzlSha, ctlB, smartA, capMs);
            LlmSpikeGui.EvalLog.closeForTest();
            System.exit(0);
        }
        if ("search".equals(ctlA) || (writtenSolver && "search".equals(ctlB))) {
            search(file, id, sections, goal, bounded, pzlSha, ctlA, ctlB, smartA, fastA, capMs);
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
                       String pzlSha, String ctlA, String ctlB, String smartA, String fastA, long capMs) throws Exception {
        final boolean written = !"search".equals(ctlA);
        List<Long> seeds = parseSeeds(System.getenv("PZL_SEEDS"));
        final long seed = seeds != null && !seeds.isEmpty() ? seeds.get(0) : LlmSpikeGui.applySeed();
        String probe = System.getenv("PZL_PROBE");
        if (probe != null && !probe.trim().isEmpty()) {
            if (written) throw new IllegalArgumentException("PZL_PROBE needs CONTROLLER_A=search");
            probeRun(file, id, sections, probe.trim(), bounded, pzlSha, ctlA, ctlB, smartA, fastA, seed, capMs);
            return;
        }
        int budget = intOr(System.getenv().getOrDefault("PZL_SEARCH_BUDGET", "2000"), 2000);
        // Lines tried per choice point before it is given up: 4 gave up on RP_034 (four Fiery Confluence lines, each
        // beaten by Welding Jar) where 40 proved it; the budget bounds the total either way.
        int lines = intOr(System.getenv().getOrDefault("PZL_SEARCH_LINES", "32"), 32);
        System.out.println("[PZL] search " + id + " opponent=" + ctlB + " seed=" + seed + " budget=" + budget);
        final int[] n = {0};
        final boolean[] broken = {false};
        PuzzleSearch.Game g = (prefix, completionSeed) -> {
            if (broken[0]) return new PuzzleSearch.Rollout(new ArrayList<>(prefix), false, "game thread outlived the cap");
            PuzzleArms.Line line = new PuzzleArms.Line(prefix, completionSeed);
            if (n[0] > 0 && n[0] % 100 == 0) {
                // Heap after a collection: prove-v3 PS_M204 and PS_WAR0a ran out of heap after 500-600 rollouts.
                System.gc();
                Runtime rt = Runtime.getRuntime();
                System.out.println("[PZL] search " + id + ": " + n[0] + " rollouts, heap "
                        + (rt.totalMemory() - rt.freeMemory()) / (1024 * 1024) + " MB");
            }
            Played p;
            try {
                p = replay(file, id, sections, ctlA, ctlB, smartA, fastA, seed, capMs, n[0]++, bounded, line);
            } catch (Exception e) {
                return new PuzzleSearch.Rollout(new ArrayList<>(line.taken), false, "rollout threw: " + e);
            }
            if (!p.healthy) broken[0] = true;
            // A game the engine keeps looping without asking anyone (prove-v2-drift PS_M204, PS_MKM4) is ended by the
            // wall-clock cap: not a solve. Only a game thread that outlived the cap makes the JVM unsafe (broken).
            if ("invalid".equals(p.status) && line.drift == null)
                return new PuzzleSearch.Rollout(new ArrayList<>(line.taken), false, p.healthy ? null : "rollout hit the wall-clock cap");
            // A written line that drifted under this reply did not beat it, whatever the do-nothing answers reached.
            boolean solved = !line.stopped() && wonAsWritten(p.cleanSolve(), writtenLine(p.game), playsNotMade(p.game));
            // What tells two solutions apart: the solver's game-log actions, and what it chose where the log does not
            // say (a mode, a clone pick, a number). Not the final state: the opponent's replies change that too
            // (RP_045: the opponent's Scavenging Ooze exiled a card on one line and not the other).
            // As a set of lines: the same plays in another order (a pump before or after combat, both winning) are
            // one solution with a variant, not two (rules pilot RP_005, RP_058, RP_059).
            // Card ids dropped: playing Forest (2) or the identical Forest (3) is one play (RP_120).
            List<String> print = new ArrayList<>();
            for (String a : solverActions(p.logLines, "Solver")) print.add(a.replaceAll(" \\(\\d+\\)", ""));
            print.addAll(line.choices);
            java.util.Collections.sort(print);
            return new PuzzleSearch.Rollout(new ArrayList<>(line.taken), solved, line.drift, String.join("\n", print));
        };
        long t0 = System.currentTimeMillis();
        PuzzleSearch searcher = new PuzzleSearch(g, "Solver", budget, lines, seed);
        PuzzleSearch.Result r = bounded ? searcher.prove() : null;
        String status = r == null ? "unscoreable" : r.verdict == PuzzleSearch.Verdict.PROVEN ? "proven"
                : r.verdict == PuzzleSearch.Verdict.DRIFT ? "drift" : "not_found";
        Map<String, Object> extra = new LinkedHashMap<>();
        Played main = null;
        if ("proven".equals(status)) {
            main = replay(file, id, sections, ctlA, ctlB, smartA, fastA, seed, capMs, n[0]++, bounded,
                    new PuzzleArms.Line(r.line, 0));
            Played again = replay(file, id, sections, ctlA, ctlB, smartA, fastA, seed, capMs, n[0]++, bounded,
                    new PuzzleArms.Line(r.line, 0));
            putWritten(extra, "", main.game);
            if (!wonAsWritten(main.cleanSolve(), writtenLine(main.game), playsNotMade(main.game))
                    || !logFingerprint(main.logLines).equals(logFingerprint(again.logLines))) {
                status = "drift";
                extra.put("search_drift", "the main line did not replay to the same solve");
            }
        }
        // One solution (eval/BENCHMARK-V1.md): a rules puzzle is searched for a second one by default.
        // A written line has no solver choice on the search line to vary: its uniqueness is not searched here.
        int uniqueBudget = written ? 0 : intOr(System.getenv().getOrDefault("PZL_UNIQUE_BUDGET",
                sections.get("acceptance") != null ? String.valueOf(budget) : "0"), 0);
        if ("proven".equals(status) && uniqueBudget > 0) {
            PuzzleSearch.Uniqueness u = searcher.unique(r.line, uniqueBudget);
            extra.put("uniqueness", u.verdict);
            extra.put("uniqueness_deviations", u.deviations);
            extra.put("uniqueness_exhausted", u.exhausted);
            extra.put("uniqueness_replays", u.replays);
            extra.put("uniqueness_unproven_alternatives", u.unprovenAlternatives);
            if (u.second != null) {
                extra.put("uniqueness_second_line", PuzzleSearch.lineText(u.second));
                Played second = replay(file, id, sections, ctlA, ctlB, smartA, fastA, seed, capMs, n[0]++, bounded,
                        new PuzzleArms.Line(u.second, 0));
                extra.put("uniqueness_second_actions", solverActions(second.logLines, "Solver"));
                if (u.mainPrint != null && u.secondPrint != null) {
                    // The lines of the two fingerprints that differ: what made the second line another solution.
                    List<String> a = java.util.Arrays.asList(u.mainPrint.split("\n")), b = java.util.Arrays.asList(u.secondPrint.split("\n"));
                    List<String> diff = new ArrayList<>();
                    // As multisets: a second line with one more of the same action (RP_056: another sacrifice) differs.
                    List<String> onlyB = new ArrayList<>(b);
                    for (String l : a) if (!onlyB.remove(l)) diff.add("main: " + l);
                    for (String l : onlyB) diff.add("second: " + l);
                    extra.put("uniqueness_difference", diff);
                }
            }
            if (u.drift != null) extra.put("uniqueness_drift", u.drift);
            System.out.println("[PZL] search " + id + ": uniqueness " + u.verdict + " (" + u.deviations
                    + " deviations, " + u.exhausted + " played out, " + u.replays + " rollouts)");
        }
        if (r != null && r.refutation != null && !"proven".equals(status)) {
            // The opponent reply that beat every line found, played out once, for the puzzle's audit.
            Played ref = replay(file, id, sections, ctlA, ctlB, smartA, fastA, seed, capMs, n[0]++, bounded,
                    new PuzzleArms.Line(r.refutation, 0));
            List<String> opp = new ArrayList<>();
            for (String l : ref.logLines) if (l.contains("Opponent")) opp.add(l);
            extra.put("refutation_line", PuzzleSearch.lineText(r.refutation));
            extra.put("refutation_opponent_log", opp);
            // How the line fared under that reply: lost, or drifted (pr-review of #278: both read "beaten").
            extra.put("refutation_status", ref.status);
            putWritten(extra, "refutation_", ref.game);
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
            if (r.exhausted) extra.put("search_exhausted", true);
            if (r.line != null) extra.put("line", PuzzleSearch.lineText(r.line));
        }
        if (main != null) {
            List<String> actions = solverActions(main.logLines, "Solver");
            putAcceptance(extra, main);
            extra.put("log_sha256", logFingerprint(main.logLines));
            extra.put("solver_actions", actions.size());
            extra.put("actions", actions);
        }
        System.out.println("[PZL] search " + id + ": " + status + (r == null ? "" : " after " + r.replays
                + " rollouts, " + r.opponentBranches + " opponent replies beaten") + " (" + (durMs / 1000) + "s)");
        writeRow(id, file, status, "proven".equals(status), main == null ? -1 : main.turns,
                main == null ? "?" : main.winCond, durMs, seed, written ? ctlA : "search", ctlB, written ? smartA : "",
                written ? fastA : "", main != null && main.goalInstalled, main == null ? "" : main.goalOracle, goal, extra);
    }

    /** PZL_PROBE: one scripted line played to game end, reported against the acceptance state. A line that drifted
     *  or was not fully drawn, and a game the draw cap or the wall clock ended, are each named in the row, never
     *  "rejected": only a state that was read and failed the acceptance state counts, and then only for this line plus
     *  ONE seeded random completion, not for every completion of the misreading. */
    static void probeRun(File file, String id, Map<String, List<String>> sections, String probeLine, boolean bounded,
                         String pzlSha, String ctlA, String ctlB, String smartA, String fastA, long seed, long capMs)
            throws Exception {
        if (sections.get("acceptance") == null)
            throw new IllegalArgumentException("PZL_PROBE needs a puzzle with an [acceptance] section: " + id);
        int completionSeed = intOr(System.getenv().getOrDefault("PZL_PROBE_COMPLETION_SEED", "0"), 0);
        List<PuzzleArms.Step> prefix = PuzzleSearch.parseLine(probeLine);
        long t0 = System.currentTimeMillis();
        PuzzleArms.Line line = new PuzzleArms.Line(prefix, completionSeed);
        Played p = replay(file, id, sections, ctlA, ctlB, smartA, fastA, seed, capMs, 0, bounded, line);
        Map<String, Object> extra = new LinkedHashMap<>();
        extra.put("kind", "probe");
        extra.put("probe_line", probeLine);
        extra.put("probe_completion_seed", completionSeed);
        extra.put("pzl_sha256", pzlSha);
        extra.put("goal_bounded", bounded);
        // A game that ended before the whole line was drawn did not play the misreading it names.
        String drift = line.drift;
        if (drift == null && line.taken.size() < prefix.size())
            drift = "line not consumed: " + line.taken.size() + " of " + prefix.size() + " steps";
        if (drift != null) extra.put("search_drift", drift);
        // The draw cap ended the game as a Draw: the final state is the cap's, not the line's.
        if (line.stopped()) extra.put("probe_stopped", true);
        putAcceptance(extra, p);
        extra.put("actions", solverActions(p.logLines, "Solver"));
        System.out.println("[PZL] probe " + id + ": " + probeLine + " -> " + extra.getOrDefault("acceptance", "unread"));
        writeRow(id, file, "invalid".equals(p.status) || line.stopped() ? "invalid" : "probe", false, p.turns, p.winCond,
                System.currentTimeMillis() - t0, seed, "search", ctlB, "", "", p.goalInstalled, p.goalOracle, "", extra);
    }

    /** One search rollout: the engine re-seeded as for a fresh launch, both search seats drawing from {@code line}. */
    static Played replay(File file, String id, Map<String, List<String>> sections, String ctlA, String ctlB, String smartA,
                         String fastA, long seed, long capMs, int rollout, boolean bounded,
                         PuzzleArms.Line line) throws Exception {
        MyRandom.setRandom(new java.util.Random(seed));
        LlmSpikeGui.SEED_USED = seed;
        PuzzleArms.searchLine = line;
        try {
            return playOnce(file, id, sections, ctlA, ctlB, smartA, fastA, seed, capMs, rollout, bounded, true);
        } finally {
            PuzzleArms.searchLine = null;
        }
    }

    /** Play one rollout and read the outcome; {@code quiet} prints nothing (a search plays thousands). */
    static Played playOnce(File file, String id, Map<String, List<String>> sections, String ctlA, String ctlB,
                           String smartA, String fastA, long seed, long capMs, int rollout, boolean bounded,
                           boolean quiet) throws Exception {
        final Puzzle puzzle = new Puzzle(sections, id, false);
        briefSolver(sections, puzzle);

        // Empty decks and a zero-card opening hand: the [state] section IS the board. Anything drawn
        // here would be a card the puzzle author never put in the position.
        LobbyPlayer solverSeat = solverSeat(sections, ctlA, smartA, fastA);
        LobbyPlayer oppSeat = opponentSeat(ctlB, smartA, fastA);
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
        // A replay seat (ReplayBridge) checks each decision row it writes against the recorded one; the hook is the
        // fork hook, null in every other run, and is put back when the game is over. Rows are written only with the
        // game log on, so a replay without it is refused rather than left unchecked.
        final ReplayBridge replay = replayLine(game);
        if (replay != null && !LlmSpikeGui.EvalLog.on())
            throw new IllegalStateException("a replay checks every decision row against the recording, and rows are "
                    + "written only with the game log on: set EVAL_OUT (" + replay.source + ")");
        // The game ends at the drift, on the game thread, before the seat is asked anything more: whatever followed
        // would not be the recorded game (the poll below is the backstop).
        if (replay != null) replay.onDrift = () -> game.setGameOver(GameEndReason.Draw);
        LlmSpikeGui.ForkHook hookBefore = LlmSpikeGui.FORK_HOOK;
        // The draws made so far go with each row: a step search's first row after the handover must follow one draw.
        if (replay != null) LlmSpikeGui.FORK_HOOK = (i, p, seat, type, n, choice, opts) -> {
            PuzzleArms.Line l = PuzzleArms.searchLine;
            replay.onDecisionRow(seat, type, n, choice, opts, l == null ? -1 : l.taken.size());
        };
        try {
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
                        applyBoard(game, puzzle);
                        // Choices made while the board is put down (Roaming Throne's type) are the puzzle's, not a play.
                        PuzzleArms.Line setupLine = PuzzleArms.searchLine;
                        if (setupLine != null) setupLine.clearChoices();
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
            // A search rollout past its choice cap is ended here, unsolved (PuzzleArms.Line.maxDraws).
            // A replay that drifted is ended here too: nothing after the drift is the recorded game.
            PuzzleArms.Line searchLine = PuzzleArms.searchLine;
            while (worker.isAlive() && System.currentTimeMillis() - t0 < capMs) {
                worker.join(Math.max(1, Math.min(200, capMs - (System.currentTimeMillis() - t0))));
                if (((searchLine != null && searchLine.stopped()) || (replay != null && replay.drift != null)) && worker.isAlive()) {
                    game.setGameOver(GameEndReason.Draw);
                    worker.join(10_000L);
                    break;
                }
            }
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
            // The static AiCache holds the game it was filled for: cleared after every rollout so a search keeps one.
            forge.ai.AiCache.clear();

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
            // A rules puzzle is solved when its acceptance state holds at the end, whoever won (PuzzleAcceptance).
            List<String> acceptanceFailed = null;
            if (sections.get("acceptance") != null && !capped) {
                PuzzleAcceptance.Verdict av = PuzzleAcceptance.parse(sections.get("acceptance"))
                        .evaluate(PuzzleAcceptance.of(game));
                solved = av.accepted;
                acceptanceFailed = av.failed;
            }

            String status = capped ? "invalid"
                    : (!goalInstalled[0] || !bounded) ? "unscoreable"
                    : (solved ? "solved" : "failed");

            List<String> logLines = new ArrayList<>();
            List<GameLogEntry> entries = new ArrayList<>();
            try {
                entries = game.getGameLog().getAllEntries();
                for (GameLogEntry e : entries) logLines.add(e.type() + ": " + e.message());
            } catch (Exception e) {
                logLines.add("INFORMATION: (log unavailable: " + e + ")");
            }
            // A replay that is not the recorded game (a drift, or another outcome than the recorded one) is no result,
            // whatever the engine then decided.
            if (replay != null && !capped) {
                replay.atEnd(status, logFingerprint(logLines));
                if (!replay.reproduced()) status = "drift";
            }
            Played pl = new Played();
            pl.game = game; pl.status = status; pl.solved = solved; pl.turns = turns;
            pl.winCond = winCond; pl.durMs = durMs; pl.goalInstalled = goalInstalled[0]; pl.goalOracle = goalOracle[0];
            pl.solverLoss = solverLoss; pl.oppLoss = oppLoss; pl.solverLossSpell = solverLossSpell;
            pl.oppLossSpell = oppLossSpell; pl.tainted = tainted; pl.logLines = logLines; pl.entries = entries; pl.healthy = !worker.isAlive();
            pl.acceptanceFailed = acceptanceFailed;
            if (replay != null && !replay.reproduced())
                pl.replayDrift = replay.drift != null ? replay.drift
                        : replay.handover >= 0 ? "the game ended after " + replay.report().get("replay_rows_matched")
                                + " of the " + replay.handover + " decision rows before the handover"
                        : "the recording was not played to its end";
            return pl;
        } finally {
            if (replay != null) LlmSpikeGui.FORK_HOOK = hookBefore;
        }
    }


    /** The acceptance verdict on a result row, when the puzzle has an acceptance state. */
    static void putAcceptance(Map<String, Object> extra, Played p) {
        if (p.acceptanceFailed == null) return;
        extra.put("acceptance", p.acceptanceFailed.isEmpty() ? "accepted" : "rejected");
        extra.put("acceptance_failed", p.acceptanceFailed);
    }

    /** What one rollout did, read off the finished game. */
    static final class Played {
        Game game;
        String status, winCond = "?", solverLoss, oppLoss, solverLossSpell, oppLossSpell;
        boolean solved, tainted, goalInstalled, healthy;
        /** Null without an [acceptance] section; else the conditions that did not hold (empty: accepted). */
        List<String> acceptanceFailed;
        /** A replay seat's game that was not the recorded one (ReplayBridge), or null. */
        String replayDrift;
        String goalOracle = "";
        int turns = -1;
        long durMs;
        List<String> logLines = new ArrayList<>();
        /** The game log's entries themselves, for the run's full record (Transcript.engineLog). */
        List<GameLogEntry> entries = new ArrayList<>();
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
        if (!pl.goalInstalled && !capped)
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
        putAcceptance(extra, pl);
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
        LlmSpikeGui.PlayerControllerLLM llm = solverLlm(game);
        java.util.Random arm = llm == null ? null : llm.armRandom();
        // What the attempt spent (BENCHMARK-V2 section 3): a model seat's calls, tokens, time and lookups.
        if (llm != null && "llm".equals(ctlA)) extra.putAll(llm.budget());
        // A written line (CONTROLLER_A=llm, SMART_MODEL=script:<file>): whether the game played it as written.
        putWritten(extra, "", game);
        // A replay (CONTROLLER_A=llm, SMART_MODEL=replay:<recording>): whether it played the recorded game.
        ReplayBridge replay = replayLine(game);
        if (replay != null) extra.putAll(replay.report());
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
        putTranscript(extra, Transcript.engineLog(id + "-seed" + seed + "-r" + rollout,
                engineEntries(capped ? game.getGameLog() : null, pl.entries)));
        writeRow(id, file, status, solved, turns, winCond, durMs, seed, ctlA, ctlB,
                smartA, fastA, pl.goalInstalled, pl.goalOracle, goal, extra);
        return pl.healthy;
    }

    /** Where this attempt's full record is (Transcript), when the run keeps one: the version, the http file every
     *  model call of this JVM is in (rows carry game_file and label), this game's engine log, and how many rows could
     *  not be written so far in this JVM (above 0 is a gap in the record, also printed as [TRANSCRIPT] FAILED). */
    static void putTranscript(Map<String, Object> extra, String engineFile) {
        if (!Transcript.on()) return;
        extra.put("transcript_version", Transcript.VERSION);
        List<String> http = new ArrayList<>();
        for (String f : Transcript.httpFiles()) http.add(Transcript.DIR + "/" + f);
        extra.put("transcript_http_files", http);
        if (engineFile != null) extra.put("transcript_engine_file", Transcript.DIR + "/" + engineFile);
        extra.put("transcript_errors", Transcript.errors());
    }

    /** The entries the engine log keeps: for a capped game, read again now (its thread may have logged more after the
     *  result was read, during or after the grace join), else the entries the result was read from. */
    static List<GameLogEntry> engineEntries(GameLog live, List<GameLogEntry> atResult) {
        if (live == null) return atResult;
        List<GameLogEntry> now = live.getAllEntries();
        return now.size() >= atResult.size() ? now : atResult;
    }

    /** The solver seat's controller when it is the model seat, else null. */
    static LlmSpikeGui.PlayerControllerLLM solverLlm(Game game) {
        try {
            Object c = game.getRegisteredPlayers().get(0).getController();
            return c instanceof LlmSpikeGui.PlayerControllerLLM ? (LlmSpikeGui.PlayerControllerLLM) c : null;
        } catch (Exception e) {
            return null;
        }
    }

    /** The written line the solver played (ScriptBridge), or null when the solver is no script seat. */
    static ScriptBridge writtenLine(Game game) {
        LlmSpikeGui.PlayerControllerLLM l = solverLlm(game);
        return l == null ? null : l.scriptBridge();
    }

    /** The recording the solver replays (ReplayBridge), or null when the solver is no replay seat. */
    static ReplayBridge replayLine(Game game) {
        LlmSpikeGui.PlayerControllerLLM l = solverLlm(game);
        return l == null ? null : l.replayBridge();
    }

    /** Plays the solver seat chose that did not happen (PlayerControllerLLM.playsNotMade), 0 for another seat. */
    static int playsNotMade(Game game) {
        LlmSpikeGui.PlayerControllerLLM l = solverLlm(game);
        return l == null ? 0 : l.playsNotMade();
    }

    /** A written line's row fields (ScriptBridge.report and the seat's plays_not_made), each name after {@code prefix}. */
    static void putWritten(Map<String, Object> extra, String prefix, Game game) {
        ScriptBridge w = writtenLine(game);
        if (w == null) return;
        for (Map.Entry<String, Object> e : w.report().entrySet()) extra.put(prefix + e.getKey(), e.getValue());
        extra.put(prefix + "plays_not_made", playsNotMade(game));
    }

    /** A solve the written line made (or any solve, without one): no drift and every play it chose happened. Lines left
     *  unplayed are allowed: under some replies the line wins before its end (pr-review of #278: an opponent that did
     *  not chump-block lost in combat, and the solve was read as that reply beating the line). */
    static boolean wonAsWritten(boolean cleanSolve, ScriptBridge w, int playsNotMade) {
        return cleanSolve && (w == null || (w.drift == null && playsNotMade == 0));
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

    // ==================== STEP REGRET (step 2): a recorded attempt, handed to the search arm at K ====================

    /** One position of a recorded attempt: the seat's first {@code k} decision rows replayed (ReplayBridge), the rest of
     *  the game searched (PuzzleSearch) against the recorded opponent arm, Forge AI, on the recorded seed. */
    static final class StepResult {
        int k;
        /** proven | unwinnable (every line played, none capped) | not_found | drift; null for a probe. */
        String verdict;
        int replays, capped, budget;
        boolean exhausted;
        String line = "", drift, refused;
        /** The forced option of the first decision after the handover (REPLAY_FORCE), or null. */
        Integer forced;
        /** The probe: the first decision after the handover, its draw's bound and the recording's value in it. */
        int bound = -1, modelValue = -1;
        String firstType;
        List<String> firstOptions;
        List<String> actions = new ArrayList<>();
        long ms;

        Map<String, Object> toMap() {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("k", k);
            if (verdict != null) m.put("verdict", verdict);
            if (forced != null) m.put("forced", forced);
            m.put("replays", replays);
            m.put("budget", budget);
            m.put("capped_rollouts", capped);
            m.put("exhausted", exhausted);
            m.put("line", line);
            m.put("actions", actions);
            if (drift != null) m.put("drift", drift);
            if (refused != null) m.put("refused", refused);
            if (bound >= 0) { m.put("bound", bound); m.put("model_value", modelValue); }
            if (firstType != null) { m.put("first_type", firstType); m.put("first_options", firstOptions); }
            m.put("duration_ms", ms);
            return m;
        }

        @Override public String toString() { return toMap().toString(); }
    }

    /** The search arm's value for a recorded index-encoded row's {@code choice} under a draw of {@code bound}: PASS or a
     *  decline (-1) is the last value (PlayerControllerLLM: play draws over passIdx, PASS last; decideOne over the
     *  options plus the decline). */
    static int stepValue(int choice, int bound) { return choice >= 0 ? choice : bound - 1; }

    /** Runs {@code body} with the handover set and the game log on (a discarded sink when it was off): the replay checks
     *  every row through the fork hook, which only a written row reaches. */
    private static <T> T withHandover(int k, java.util.concurrent.Callable<T> body) throws Exception {
        boolean logWasOn = LlmSpikeGui.EvalLog.on();
        if (!logWasOn) LlmSpikeGui.EvalLog.initDiscard();
        ReplayBridge.HANDOVER = k;
        try { return body.call(); }
        finally {
            ReplayBridge.HANDOVER = -1;
            if (!logWasOn) LlmSpikeGui.EvalLog.closeForTest();
        }
    }

    /** Null when the first decision after the handover is the recording's row K+1 asked the same way: its type, menu
     *  size and options, answered by one search draw (a multi-pick may take more). The seat switches inside row K's
     *  write, so the rest of that hook already runs the arm's branches (no yield, no CANCEL, no action intent): a
     *  different first decision means the position searched is not the recorded one. A handover at or past the
     *  recording's last row has no row K+1 to check. */
    static String firstAfterMismatch(ReplayBridge rb) {
        if (rb == null) return "no replay seat in the rollout";
        int k = rb.handover;
        if (k < 0 || k >= rb.rows.size()) return null;
        ReplayBridge.Row rec = rb.rows.get(k), live = rb.firstAfter;
        if (live == null) return "the game ended before the first decision after the handover (the recording's row " + (k + 1) + ")";
        if (!rec.type.equals(live.type) || rec.n != live.n || !rec.options.equals(live.options))
            return "the first decision after the handover is " + live + ", the recording's row " + (k + 1) + " was " + rec;
        // A multi-pick may take several draws (at most its menu size); a draw beyond that was made in the rest of row K's hook.
        if (rec.multi ? rb.drawsAtFirstAfter > rec.n : rb.drawsAtFirstAfter != 1)
            return "the first decision after the handover took " + rb.drawsAtFirstAfter + " search draws, not "
                    + (rec.multi ? "at most " + rec.n : "one");
        return null;
    }

    /** One step-search rollout: the replay seat to row k, then the search arm drawing from {@code line}. With
     *  {@code checkFirst} a rollout whose first decision after the handover is not the recording's is a drift
     *  ({@link #firstAfterMismatch}); the probe reports that itself, as a refusal. */
    private static PuzzleSearch.Rollout stepRollout(File file, String id, Map<String, List<String>> sections, boolean bounded,
                                                    String spec, long seed, long capMs, int rollout, PuzzleArms.Line line,
                                                    int[] capped, ReplayBridge[] bridge, boolean checkFirst) {
        Played p;
        try {
            p = replay(file, id, sections, "llm", "ai", spec, spec, seed, capMs, rollout, bounded, line);
        } catch (Exception e) {
            return new PuzzleSearch.Rollout(new ArrayList<>(line.taken), false, "rollout threw: " + e);
        }
        ReplayBridge rb = replayLine(p.game);
        if (bridge != null) bridge[0] = rb;
        if (p.replayDrift != null) return new PuzzleSearch.Rollout(new ArrayList<>(line.taken), false, "replay: " + p.replayDrift);
        if (!p.healthy) return new PuzzleSearch.Rollout(new ArrayList<>(line.taken), false, "rollout hit the wall-clock cap");
        String first = checkFirst ? firstAfterMismatch(rb) : null;
        if (first != null) return new PuzzleSearch.Rollout(new ArrayList<>(line.taken), false, "replay: " + first);
        // A capped line is counted lost, as the search arm counts it; a position is then never called unwinnable.
        if ("invalid".equals(p.status) || line.stopped()) capped[0]++;
        boolean solved = !line.stopped() && p.cleanSolve();
        return new PuzzleSearch.Rollout(new ArrayList<>(line.taken), solved, line.drift,
                String.join("\n", solverActions(p.logLines, "Solver")));
    }

    /** The first decision after the handover at {@code k}, as one rollout with no prefix finds it: its draw's bound and
     *  the recorded value in it, or why it cannot be forced (v1: a play or single-choice decision, one draw, the
     *  recorded row's type and options). */
    static StepResult stepProbe(File file, String id, Map<String, List<String>> sections, boolean bounded, String spec,
                                long seed, int k, long capMs) throws Exception {
        return withHandover(k, () -> probe(file, id, sections, bounded, spec, seed, k, capMs));
    }

    private static StepResult probe(File file, String id, Map<String, List<String>> sections, boolean bounded, String spec,
                                    long seed, int k, long capMs) {
        StepResult r = new StepResult();
        r.k = k;
        long t0 = System.currentTimeMillis();
        ReplayBridge[] b = {null};
        PuzzleSearch.Rollout ro = stepRollout(file, id, sections, bounded, spec, seed, capMs, 0,
                new PuzzleArms.Line(new ArrayList<>(), 0L), new int[1], b, false);
        r.replays = 1;
        r.ms = System.currentTimeMillis() - t0;
        if (ro.drift != null) { r.drift = ro.drift; return r; }
        ReplayBridge rb = b[0];
        ReplayBridge.Row live = rb == null ? null : rb.firstAfter;
        ReplayBridge.Row rec = rb == null || k >= rb.rows.size() ? null : rb.rows.get(k);
        if (live == null || rec == null || ro.steps.isEmpty()) {
            r.refused = "no decision after the handover to force (" + (rec == null ? "the recording has none" : "the arm drew none") + ")";
            return r;
        }
        r.firstType = live.type;
        r.firstOptions = live.options;
        PuzzleArms.Step first = ro.steps.get(0);
        r.bound = first.bound;
        if (rec.multi) r.refused = "the recorded decision " + (k + 1) + " is a multi-pick (" + rec.type + "): v1 forces one choice";
        else if (!rec.type.equals(live.type) || rec.n != live.n || !rec.options.equals(live.options))
            r.refused = "the first decision after the handover is " + live + ", the recording's was " + rec;
        else if (rb.drawsAtFirstAfter != 1 || !"Solver".equals(first.seat))
            r.refused = "the first decision after the handover took " + rb.drawsAtFirstAfter + " draws (one is forced)";
        else if (first.bound != rec.n && first.bound != rec.n + 1)
            r.refused = "the draw's bound " + first.bound + " is not the recorded menu's " + rec.n + " (or with its decline)";
        else if (stepValue(live.choice, first.bound) != first.value)
            r.refused = "the draw " + first + " is not the row's choice " + live.choice;
        else r.modelValue = stepValue(rec.choice, first.bound);
        return r;
    }

    /** Test seam: run once between the search and the replay of its witness line (null in play). */
    static Runnable BEFORE_WITNESS;

    /** The position after the seat's first {@code k} decision rows of the recording {@code spec}, searched with
     *  {@code budget} rollouts; with {@code force}, the first decision after it is forced to that value (its bound from
     *  {@link #stepProbe}). Unwinnable only when every line was played and none was capped. */
    static StepResult stepSearch(File file, String id, Map<String, List<String>> sections, boolean bounded, String spec,
                                 long seed, int k, Integer force, int budget, int lines, long capMs) throws Exception {
        return stepSearch(file, id, sections, bounded, spec, seed, k, force, null, budget, lines, capMs);
    }

    /** As above with the forced decision's {@code given} probe already made (null: made here), so forcing every option
     *  (REPLAY_FORCE=all) probes once. */
    static StepResult stepSearch(File file, String id, Map<String, List<String>> sections, boolean bounded, String spec,
                                 long seed, int k, Integer force, StepResult given, int budget, int lines, long capMs)
            throws Exception {
        return withHandover(k, () -> {
            long t0 = System.currentTimeMillis();
            StepResult r = new StepResult();
            r.k = k;
            r.forced = force;
            r.budget = budget;
            PuzzleArms.Step forced = null;
            int extra = 0;
            if (force != null) {
                StepResult pr = given != null ? given : probe(file, id, sections, bounded, spec, seed, k, capMs);
                extra = given != null ? 0 : pr.replays;
                r.bound = pr.bound; r.modelValue = pr.modelValue; r.firstType = pr.firstType; r.firstOptions = pr.firstOptions;
                if (pr.drift != null || pr.refused != null || force < 0 || force >= pr.bound) {
                    r.verdict = pr.drift != null ? "drift" : "refused";
                    r.drift = pr.drift;
                    r.refused = pr.refused != null ? pr.refused : pr.drift != null ? null : "force " + force + " is outside 0.." + (pr.bound - 1);
                    r.replays = extra;
                    r.ms = System.currentTimeMillis() - t0;
                    return r;
                }
                forced = new PuzzleArms.Step("Solver", pr.bound, force);
            }
            final PuzzleArms.Step f = forced;
            final int[] n = {0};
            final int[] capped = {0};
            PuzzleSearch.Game g = (prefix, completionSeed) -> {
                List<PuzzleArms.Step> full = new ArrayList<>(prefix);
                if (f != null) full.add(0, f);
                PuzzleSearch.Rollout ro = stepRollout(file, id, sections, bounded, spec, seed, capMs, n[0]++,
                        new PuzzleArms.Line(full, completionSeed), capped, null, true);
                if (f == null) return ro;
                // The forced draw is the search's root, not one of its choices.
                if (ro.drift == null && (ro.steps.isEmpty() || !ro.steps.get(0).equals(f)))
                    return new PuzzleSearch.Rollout(ro.steps, false, "the forced draw " + f + " was not the first one");
                return new PuzzleSearch.Rollout(new ArrayList<>(ro.steps.subList(Math.min(1, ro.steps.size()), ro.steps.size())),
                        ro.solved, ro.drift, ro.actions);
            };
            PuzzleSearch.Result res = new PuzzleSearch(g, "Solver", budget, lines, seed).prove();
            r.replays = res.replays + extra;
            r.capped = capped[0];
            r.exhausted = res.exhausted;
            r.drift = res.drift;
            r.verdict = res.verdict == PuzzleSearch.Verdict.DRIFT ? "drift" : res.verdict == PuzzleSearch.Verdict.PROVEN ? "proven"
                    : res.exhausted && capped[0] == 0 ? "unwinnable" : "not_found";
            if (res.line != null) {
                List<PuzzleArms.Step> main = new ArrayList<>(res.line);
                if (f != null) main.add(0, f);
                r.line = PuzzleSearch.lineText(main);
                // The witness, replayed: it must solve again (the search arm's own check on its main line).
                Runnable hook = BEFORE_WITNESS;
                if (hook != null) hook.run();
                PuzzleSearch.Rollout again = stepRollout(file, id, sections, bounded, spec, seed, capMs, n[0]++,
                        new PuzzleArms.Line(main, 0L), new int[1], null, true);
                r.replays++;
                if (!again.solved || again.drift != null) {
                    r.verdict = "drift";
                    r.drift = "the witness line did not solve again" + (again.drift == null ? "" : ": " + again.drift);
                }
                if (again.actions != null && !again.actions.isEmpty())
                    r.actions = new ArrayList<>(java.util.Arrays.asList(again.actions.split("\n")));
            }
            r.ms = System.currentTimeMillis() - t0;
            return r;
        });
    }

    /** CONTROLLER_A=llm with a replay: spec and REPLAY_HANDOVER=K: one step-search row per search, in
     *  {@code step-search.jsonl} under EVAL_OUT (never puzzle-results.jsonl: this is no attempt). REPLAY_FORCE=o forces
     *  the first decision after K to o, REPLAY_FORCE=all to each of its options in turn, REPLAY_FORCE=probe only finds
     *  that decision. PZL_SEARCH_BUDGET / PZL_SEARCH_LINES as for the search arm; the seed is PZL_SEEDS' first. */
    static void stepSearchMain(File file, String id, Map<String, List<String>> sections, boolean bounded, String pzlSha,
                               String ctlB, String spec, long capMs) throws Exception {
        if (!"ai".equals(ctlB))
            throw new IllegalArgumentException("a step search plays the recorded opponent arm, Forge AI (CONTROLLER_B=ai), not " + ctlB);
        List<Long> seeds = parseSeeds(System.getenv("PZL_SEEDS"));
        final long seed = seeds != null && !seeds.isEmpty() ? seeds.get(0) : LlmSpikeGui.applySeed();
        int k = Integer.parseInt(System.getenv("REPLAY_HANDOVER").trim());
        int budget = intOr(System.getenv().getOrDefault("PZL_SEARCH_BUDGET", "2000"), 2000);
        int lines = intOr(System.getenv().getOrDefault("PZL_SEARCH_LINES", "32"), 32);
        String force = System.getenv().getOrDefault("REPLAY_FORCE", "").trim();
        List<StepResult> out = new ArrayList<>();
        if (force.isEmpty()) out.add(stepSearch(file, id, sections, bounded, spec, seed, k, null, budget, lines, capMs));
        else {
            StepResult pr = stepProbe(file, id, sections, bounded, spec, seed, k, capMs);
            if ("probe".equals(force) || pr.drift != null || pr.refused != null) out.add(pr);
            else {
                List<Integer> forces = new ArrayList<>();
                if ("all".equals(force)) for (int o = 0; o < pr.bound; o++) forces.add(o);
                else forces.add(Integer.parseInt(force));
                for (Integer o : forces) out.add(stepSearch(file, id, sections, bounded, spec, seed, k, o, pr, budget, lines, capMs));
                // The probe's replay is in no row of its own: the first forced row carries it.
                out.get(0).replays += pr.replays;
            }
        }
        String dir = System.getenv("EVAL_OUT");
        for (StepResult r : out) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("schema", "step-search-v1");
            row.put("id", id);
            row.put("pzl_sha256", pzlSha);
            row.put("seed", seed);
            row.put("source", spec.substring(ReplayBridge.PREFIX.length()));
            row.put("replay_version", ReplayBridge.VERSION);
            row.put("harness_epoch", LlmSpikeGui.HARNESS_EPOCH);
            row.put("search_lines_per_point", lines);
            row.putAll(r.toMap());
            String json = toJson(row);
            System.out.println("STEP_RESULT " + json);
            if (dir == null || dir.isEmpty()) continue;
            appendTo(new File(dir), "step-search.jsonl", json);
        }
    }

    /** One JSONL row per puzzle into $EVAL_OUT/puzzle-results.jsonl. Additive-only schema, same
     *  contract as the match summaries: consumers read via .get(). */
    static void writeRow(String id, File file, String status, boolean solved, int turns,
                         String winCond, long durMs, long seed, String ctlA, String ctlB,
                         String smartA, String fastA, boolean goalInstalled, String goalOracle, String goal,
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
        // Which interface the attempt saw: puzzle_score.py refuses to pool epochs (pr-review of PR #273).
        row.put("harness_epoch", LlmSpikeGui.HARNESS_EPOCH);
        // The hidden-menu arm, only when on: a row without it saw the menu, as every row before the arm did.
        if (AnswerTool.proposeActions()) row.put("propose_actions", true);
        if (AnswerTool.proposeTerse()) row.put("propose_terse", true);
        if (AnswerTool.proposeNotebook()) row.put("propose_notebook", true);
        if (LlmSpikeGui.PlayerControllerLLM.PROPOSE_AUDIT) row.put("propose_audit", true);
        // Either tier, as the game line's jevA (LlmSpikeGui): a fast-tier jev: solver is a jev: solver too.
        if ("llm".equals(ctlA) && (DecisionsBridge.isSpec(smartA) || DecisionsBridge.isSpec(fastA)))
            row.put("decisions_bridge_version", DecisionsBridge.VERSION);
        // A claude-cli: solver (ClaudeCliBridge): the bridge and the effort and thinking budget it ran at, which
        // puzzle_score.py never pools; a solver on either CLI lane: the development-lane label it prints instead of a score.
        if ("llm".equals(ctlA) && (ClaudeCliBridge.isSpec(smartA) || ClaudeCliBridge.isSpec(fastA))) {
            row.put("claude_cli_bridge_version", ClaudeCliBridge.VERSION);
            row.put("claude_cli_effort", ClaudeCliBridge.effortStamp());
            row.put("claude_cli_thinking_tokens", ClaudeCliBridge.thinkingStamp());
        }
        if (LlmSpikeGui.devLane(ctlA, smartA, fastA)) row.put("dev_lane", ClaudeCliBridge.DEV_LANE);
        row.putAll(extra);
        String line = toJson(row);
        System.out.println("PZL_RESULT " + line);
        if (out == null || out.isEmpty()) return;
        appendTo(new File(out), "puzzle-results.jsonl", line);
    }

    /** One row appended to dir/file, the directory made if need be. A failure to open, write or close it is one
     *  `[PUZZLE] write failed` line on stdout (issue #430), not a `[PZL] could not write` line nothing reads. */
    static boolean appendTo(File dir, String file, String line) {
        boolean[] said = new boolean[1];
        try {
            dir.mkdirs();
            try (Writer w = new FileWriter(new File(dir, file), true)) {
                said[0] = !appendRow(w, line, file);
                return !said[0];
            }
        } catch (Exception e) {      // open or close: appendRow has already said a write that failed
            if (!said[0]) System.out.println("[PUZZLE] write failed: a row of " + file + " did not reach the disk and is missing from it: " + e);
            return false;
        }
    }

    /** One row to `w`, and false (after one `[PUZZLE] write failed` line on stdout) when it did not reach it. A bare
     *  PrintWriter swallows the IOException of a full disk, so the row would be missing with nothing said (issue #430);
     *  checkError flushes, so a failure in the buffered tail is seen too. puzzle_baselines.write_failures reads the line. */
    static boolean appendRow(Writer w, String line, String file) {
        PrintWriter pw = new PrintWriter(w);
        pw.println(line);
        if (!pw.checkError()) return true;
        System.out.println("[PUZZLE] write failed: a row of " + file + " did not reach the disk and is missing from it");
        return false;
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
