package forge.spike;

import java.util.List;

import forge.GuiDesktop;
import forge.game.Game;
import forge.game.GameEndReason;
import forge.game.GameRules;
import forge.game.GameType;
import forge.game.Match;
import forge.game.player.RegisteredPlayer;
import forge.gui.GuiBase;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;

/**
 * HEADLESS runner: the same LLM-vs-LLM match as {@link LlmSpikeGui}, but no Forge window —
 * pure console. Uses the exact same PlayerControllerLLM / LLMBridge / deck plumbing
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

        // Shared with the GUI runner: identity-keyed GAME OVER block + machine-readable game-summary
        // JSONL line the eval/tournament drivers parse. See LlmSpikeGui.writeGameSummary.
        LlmSpikeGui.writeGameSummary(game, seed, durMs, capMs, capped);
        System.exit(0); // bridges hold non-daemon subprocess pipes; exit explicitly
    }
}
