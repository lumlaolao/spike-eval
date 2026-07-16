package forge.spike;

import java.util.List;

import com.google.common.collect.Lists;

import forge.GuiDesktop;
import forge.deck.Deck;
import forge.deck.DeckgenUtil;
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
 * pure console. Uses the exact same PlayerControllerLLM / ClaudeBridge / deck plumbing, so a
 * headless game exercises identical decision paths to the GUI spectator match.
 *
 * Env: SMART_MODEL / FAST_MODEL / DECK_A / DECK_B / PLAN_A / PLAN_B as in LlmSpikeGui, plus
 * GAME_CAP_MS (default 3h) — a hard wall-clock cap after which the game is ended as a draw.
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

        String home = System.getProperty("user.home");
        String pathA = System.getenv().getOrDefault("DECK_A", home + "/Downloads/Deck - Grixis Reanimator.txt");
        String pathB = System.getenv().getOrDefault("DECK_B", home + "/Downloads/Deck - Mardu Energy.txt");
        String nameA = System.getenv().getOrDefault("DECK_A_NAME", "Grixis Reanimator");
        String nameB = System.getenv().getOrDefault("DECK_B_NAME", "Mardu Energy");
        String smartModel = System.getenv().getOrDefault("SMART_MODEL", "opus");
        String fastModel  = System.getenv().getOrDefault("FAST_MODEL", "haiku");
        String planA = System.getenv().getOrDefault("PLAN_A", LlmSpikeGui.DEFAULT_PLAN_A);
        String planB = System.getenv().getOrDefault("PLAN_B", LlmSpikeGui.DEFAULT_PLAN_B);

        Deck deckA, deckB;
        try { deckA = LlmSpikeGui.loadDeck(nameA, pathA); deckB = LlmSpikeGui.loadDeck(nameB, pathB); }
        catch (Exception e) {
            System.out.println("[DECK] failed to load decklists (" + e + ") -> random color decks");
            deckA = DeckgenUtil.getRandomColorDeck(true); deckB = DeckgenUtil.getRandomColorDeck(true);
        }

        List<RegisteredPlayer> players = Lists.newArrayList();
        players.add(new RegisteredPlayer(deckA).setPlayer(new LlmSpikeGui.LobbyPlayerLLM(nameA, planA, smartModel, fastModel)));
        players.add(new RegisteredPlayer(deckB).setPlayer(new LlmSpikeGui.LobbyPlayerLLM(nameB, planB, smartModel, fastModel)));

        GameRules rules = new GameRules(GameType.Constructed);
        Match match = new Match(rules, players, "LlmHeadless");
        final Game game = match.createGame();

        System.out.println("[HEADLESS] " + nameA + " vs " + nameB + "  (smart=" + smartModel + ", fast=" + fastModel
                + ", cap=" + (capMs / 60000) + "min)");
        Thread worker = new Thread(() -> {
            try { match.startGame(game); }
            catch (Throwable t) { System.out.println("[HEADLESS] game thread threw: " + t); }
        }, "forge-game");
        worker.setDaemon(true);
        worker.start();
        worker.join(capMs);
        if (worker.isAlive()) {
            System.out.println("[HEADLESS] wall-clock cap reached; ending as draw");
            game.setGameOver(GameEndReason.Draw);
            worker.join(15000);
        }

        System.out.println("========== GAME OVER ==========");
        try {
            if (game.getOutcome() == null) System.out.println("outcome: n/a");
            else {
                var w = game.getOutcome().getWinningLobbyPlayer();
                System.out.println("outcome: " + (w == null ? "draw" : w.getName() + " WINS")
                        + " after " + game.getOutcome().getLastTurnNumber() + " turns ("
                        + game.getOutcome().getWinCondition() + ")");
            }
        } catch (Exception e) { System.out.println("outcome: (unprintable: " + e + ")"); }
        System.out.println("--- full game log ---");
        try {
            List<GameLogEntry> all = game.getGameLog().getAllEntries();
            for (GameLogEntry e : all) System.out.println("  " + e.type() + ": " + e.message());
        } catch (Exception e) { System.out.println("  (log unavailable: " + e + ")"); }
        System.out.println("===============================");
        System.exit(0); // bridges hold non-daemon subprocess pipes; exit explicitly
    }
}
