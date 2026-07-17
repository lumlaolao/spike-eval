package forge.spike;

import java.util.List;

import forge.GuiDesktop;
import forge.game.Game;
import forge.game.GameEndReason;
import forge.game.GameLogEntry;
import forge.game.GameRules;
import forge.game.GameType;
import forge.game.Match;
import forge.game.player.RegisteredPlayer;
import forge.gui.GuiBase;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;

/**
 * HEADLESS runner: the same LLM-vs-LLM match as {@link LlmSpikeGui}, but no Forge window —
 * pure console. Uses the exact same PlayerControllerLLM / ClaudeBridge / deck plumbing
 * (via {@link LlmSpikeGui#buildPlayers()}), so a headless game exercises identical decision
 * paths to the GUI spectator match.
 *
 * Eval features: GAME_SEED (reproducible shuffles), SWAP_SEATS=1 (paired seat flip),
 * SMART_MODEL_A/B + FAST_MODEL_A/B, CONTROLLER_A/B = llm|random|floor|ai (baseline arms),
 * EVAL_OUT=<dir> (per-decision + game-summary JSONL), GAME_CAP_MS (default 3h; a capped
 * game is recorded as INVALID, not a draw — timeouts must not count as results).
 *
 * Run: bash run-llm-headless.sh  (from the forge repo root; installed by install.sh)
 */
public final class LlmSpikeHeadless {
    private LlmSpikeHeadless() {}

    public static void main(String[] args) throws Exception {
        long capMs = 3 * 60 * 60 * 1000L;
        try { String v = System.getenv("GAME_CAP_MS"); if (v != null) capMs = Long.parseLong(v.trim()); } catch (Exception ignore) {}

        System.out.println("[HEADLESS] initializing Forge model (no GUI)...");
        GuiBase.setInterface(new GuiDesktop());
        FModel.initialize(null, preferences -> {
            preferences.setPref(FPref.LOAD_CARD_SCRIPTS_LAZILY, false);
            preferences.setPref(FPref.UI_LANGUAGE, "en-US");
            return null;
        });

        long seed = LlmSpikeGui.applySeed(); // AFTER model init so nothing reseeds behind us
        LlmSpikeGui.EvalLog.init(seed);
        List<RegisteredPlayer> players = LlmSpikeGui.buildPlayers();

        GameRules rules = new GameRules(GameType.Constructed);
        Match match = new Match(rules, players, "LlmHeadless");
        final Game game = match.createGame();

        long t0 = System.currentTimeMillis();
        System.out.println("[HEADLESS] game starting (cap=" + (capMs / 60000) + "min)");
        Thread worker = new Thread(() -> {
            try { match.startGame(game); }
            catch (Throwable t) { System.out.println("[HEADLESS] game thread threw: " + t); }
        }, "forge-game");
        worker.setDaemon(true);
        worker.start();
        worker.join(capMs);
        boolean capped = worker.isAlive();
        if (capped) {
            System.out.println("[HEADLESS] wall-clock cap reached; game is INVALID for eval purposes");
            game.setGameOver(GameEndReason.Draw);
            worker.join(15000);
        }
        long durMs = System.currentTimeMillis() - t0;

        // Winner in DECK-IDENTITY terms (A/B), independent of seat swap.
        String nameA = System.getenv().getOrDefault("DECK_A_NAME", "Grixis Reanimator");
        String nameB = System.getenv().getOrDefault("DECK_B_NAME", "Mardu Energy");
        String winner = "draw"; int turns = -1; String winCond = "?";
        try {
            if (game.getOutcome() != null) {
                turns = game.getOutcome().getLastTurnNumber();
                winCond = String.valueOf(game.getOutcome().getWinCondition());
                var w = game.getOutcome().getWinningLobbyPlayer();
                // unknown-name winner should be unreachable; map to "invalid" so aggregate.py's
                // A|B|draw|invalid contract holds even if it happens
                if (w != null) winner = nameA.equals(w.getName()) ? "A" : (nameB.equals(w.getName()) ? "B" : "invalid");
            }
        } catch (Exception ignore) { }
        if (capped) winner = "invalid";

        System.out.println("========== GAME OVER ==========");
        System.out.println("outcome: " + winner + " after " + turns + " turns (" + winCond + ") in " + (durMs / 1000) + "s");
        System.out.println("--- full game log ---");
        try {
            for (GameLogEntry e : game.getGameLog().getAllEntries())
                System.out.println("  " + e.type() + ": " + e.message());
        } catch (Exception e) { System.out.println("  (log unavailable: " + e + ")"); }
        System.out.println("===============================");

        // Machine-readable game summary — the batch driver keys off this line. Every interpolated
        // string goes through jsonEscape; all per-identity fields are IDENTITY-keyed (never post-swap).
        String smartDef = System.getenv().getOrDefault("SMART_MODEL", "opus");
        String fastDef  = System.getenv().getOrDefault("FAST_MODEL", "haiku");
        LlmSpikeGui.EvalLog.write("{\"t\":\"game\",\"seed\":" + seed
                + ",\"swap\":" + "1".equals(System.getenv("SWAP_SEATS"))
                + ",\"deck_a\":\"" + LlmSpikeGui.ClaudeBridge.jsonEscape(nameA) + "\""
                + ",\"deck_b\":\"" + LlmSpikeGui.ClaudeBridge.jsonEscape(nameB) + "\""
                + ",\"controller_a\":\"" + LlmSpikeGui.ClaudeBridge.jsonEscape(System.getenv().getOrDefault("CONTROLLER_A", "llm")) + "\""
                + ",\"controller_b\":\"" + LlmSpikeGui.ClaudeBridge.jsonEscape(System.getenv().getOrDefault("CONTROLLER_B", "llm")) + "\""
                + ",\"smart_a\":\"" + LlmSpikeGui.ClaudeBridge.jsonEscape(System.getenv().getOrDefault("SMART_MODEL_A", smartDef)) + "\""
                + ",\"smart_b\":\"" + LlmSpikeGui.ClaudeBridge.jsonEscape(System.getenv().getOrDefault("SMART_MODEL_B", smartDef)) + "\""
                + ",\"fast_a\":\"" + LlmSpikeGui.ClaudeBridge.jsonEscape(System.getenv().getOrDefault("FAST_MODEL_A", fastDef)) + "\""
                + ",\"fast_b\":\"" + LlmSpikeGui.ClaudeBridge.jsonEscape(System.getenv().getOrDefault("FAST_MODEL_B", fastDef)) + "\""
                + ",\"winner\":\"" + winner + "\",\"turns\":" + turns
                + ",\"win_condition\":\"" + LlmSpikeGui.ClaudeBridge.jsonEscape(winCond) + "\""
                + ",\"duration_ms\":" + durMs
                // Self-describing provenance: heuristic switches / plans / budgets / caps / SHAs / discipline,
                // all read as the JVM actually saw them so the batch is reconstructable from a single line.
                + ",\"mulligan_heuristic\":" + (LlmSpikeGui.PlayerControllerLLM.mullHeuristic() ? 1 : 0)
                + ",\"skip_heuristics\":" + (LlmSpikeGui.PlayerControllerLLM.skipHeuristics() ? 1 : 0)
                + ",\"plan_a\":\"" + LlmSpikeGui.ClaudeBridge.jsonEscape(System.getenv().getOrDefault("PLAN_A", LlmSpikeGui.DEFAULT_PLAN_A)) + "\""
                + ",\"plan_b\":\"" + LlmSpikeGui.ClaudeBridge.jsonEscape(System.getenv().getOrDefault("PLAN_B", LlmSpikeGui.DEFAULT_PLAN_B)) + "\""
                + ",\"smart_budget\":" + LlmSpikeGui.PlayerControllerLLM.smartBudget()
                + ",\"fast_budget\":" + LlmSpikeGui.PlayerControllerLLM.fastBudget()
                + ",\"claude_deadline_ms\":" + LlmSpikeGui.ClaudeBridge.deadlineMs()
                + ",\"game_cap_ms\":" + capMs
                + ",\"engine_sha\":\"" + LlmSpikeGui.ClaudeBridge.jsonEscape(System.getenv().getOrDefault("ENGINE_SHA", "unknown")) + "\""
                + ",\"overlay_sha\":\"" + LlmSpikeGui.ClaudeBridge.jsonEscape(System.getenv().getOrDefault("OVERLAY_SHA", "unknown")) + "\""
                + ",\"deck_sha\":\"" + LlmSpikeGui.ClaudeBridge.jsonEscape(System.getenv().getOrDefault("DECK_SHA", "unknown")) + "\""
                + ",\"discipline\":\"" + LlmSpikeGui.ClaudeBridge.jsonEscape(System.getenv().getOrDefault("RUN_DISCIPLINE", "unspecified")) + "\""
                + "}");
        System.exit(0); // bridges hold non-daemon subprocess pipes; exit explicitly
    }
}
