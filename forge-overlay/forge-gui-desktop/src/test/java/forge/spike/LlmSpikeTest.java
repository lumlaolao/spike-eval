package forge.spike;

import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;

import com.google.common.collect.Lists;

import forge.GuiDesktop;
import forge.LobbyPlayer;
import forge.ai.LobbyPlayerAi;
import forge.ai.PlayerControllerAi;
import forge.deck.Deck;
import forge.deck.DeckgenUtil;
import forge.game.Game;
import forge.game.GameEndReason;
import forge.game.GameRules;
import forge.game.GameType;
import forge.game.Match;
import forge.game.card.Card;
import forge.game.combat.Combat;
import forge.game.player.Player;
import forge.game.player.RegisteredPlayer;
import forge.game.spellability.SpellAbility;
import forge.gui.GuiBase;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;
import forge.util.ITriggerEvent;

import org.testng.Assert;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;

/**
 * CODE SPIKE (throwaway): prove that a custom PlayerController can be injected into a
 * full Forge game via the LobbyPlayer -> Player.setFirstController seam, WITHOUT forking
 * the engine, and that the engine calls it back synchronously at real decision points.
 *
 * NO LLM here on purpose. We only prove the seam. Each overridden callback bumps a
 * counter and delegates to the built-in AI so the game still plays legally.
 */
public class LlmSpikeTest {
    private static boolean initialized = false;

    @BeforeClass
    public void initializeModel() {
        if (initialized) return;
        GuiBase.setInterface(new GuiDesktop());
        FModel.initialize(null, preferences -> {
            preferences.setPref(FPref.LOAD_CARD_SCRIPTS_LAZILY, false);
            preferences.setPref(FPref.UI_LANGUAGE, "en-US");
            return null;
        });
        initialized = true;
    }

    /** Counters proving our controller was actually driven by the engine. */
    static final AtomicInteger cbMulligan   = new AtomicInteger();
    static final AtomicInteger cbAbility     = new AtomicInteger();
    static final AtomicInteger cbAttackers   = new AtomicInteger();
    static final AtomicInteger cbBlockers    = new AtomicInteger();
    static int maxLegalOptionsSeen = 0;

    /** Our agent's controller: extend the AI, override a few callbacks, delegate to super. */
    static class PlayerControllerLLM extends PlayerControllerAi {
        PlayerControllerLLM(Game game, Player p, LobbyPlayer lp) { super(game, p, lp); }

        @Override
        public boolean mulliganKeepHand(Player firstPlayer, int cardsToReturn) {
            cbMulligan.incrementAndGet();
            System.out.println("[LLM-SEAM] mulliganKeepHand called (hand decision at game start)");
            return super.mulliganKeepHand(firstPlayer, cardsToReturn);
        }

        @Override
        public SpellAbility getAbilityToPlay(Card hostCard, List<SpellAbility> abilities, ITriggerEvent triggerEvent) {
            int n = cbAbility.incrementAndGet();
            int legal = abilities == null ? 0 : abilities.size();
            if (legal > maxLegalOptionsSeen) maxLegalOptionsSeen = legal;
            if (n <= 5) {
                System.out.println("[LLM-SEAM] getAbilityToPlay #" + n
                        + " -> engine handed me " + legal + " pre-validated legal option(s)");
            }
            return super.getAbilityToPlay(hostCard, abilities, triggerEvent);
        }

        @Override
        public void declareAttackers(Player attacker, Combat combat) {
            cbAttackers.incrementAndGet();
            System.out.println("[LLM-SEAM] declareAttackers called (combat decision)");
            super.declareAttackers(attacker, combat);
        }

        @Override
        public void declareBlockers(Player defender, Combat combat) {
            cbBlockers.incrementAndGet();
            System.out.println("[LLM-SEAM] declareBlockers called (combat decision)");
            super.declareBlockers(defender, combat);
        }
    }

    /** LobbyPlayer whose in-game Player uses OUR controller instead of PlayerControllerAi. */
    static class LobbyPlayerLLM extends LobbyPlayerAi {
        LobbyPlayerLLM(String name) { super(name, null); }

        @Override
        public Player createIngamePlayer(Game game, int id) {
            Player p = new Player(getName(), game, id);
            p.setFirstController(new PlayerControllerLLM(game, p, this));
            return p;
        }
    }

    @Test(timeOut = 180000)
    public void injectsCustomControllerIntoFullGame() throws Exception {
        // Two random constructed decks (one call each), same generator the AI uses.
        Deck deckLLM = DeckgenUtil.getRandomColorDeck(true);
        Deck deckAI  = DeckgenUtil.getRandomColorDeck(true);

        List<RegisteredPlayer> players = Lists.newArrayList();
        players.add(new RegisteredPlayer(deckLLM).setPlayer(new LobbyPlayerLLM("LLM-Agent")));   // seat 0: our controller
        players.add(new RegisteredPlayer(deckAI).setPlayer(new LobbyPlayerAi("Forge-AI", null))); // seat 1: built-in AI

        GameRules rules = new GameRules(GameType.Constructed);
        Match match = new Match(rules, players, "LlmSpike");
        final Game game = match.createGame();

        // Run the full game on a worker thread with a hard cap so a durdle can't hang the test.
        Thread worker = new Thread(() -> {
            try {
                match.startGame(game);
            } catch (Throwable t) {
                System.out.println("[LLM-SEAM] game thread threw: " + t);
            }
        }, "forge-game");
        worker.start();
        worker.join(150000);
        if (worker.isAlive()) {
            System.out.println("[LLM-SEAM] game exceeded cap; ending as draw");
            game.setGameOver(GameEndReason.Draw);
            worker.join(10000);
        }

        System.out.println("========== SPIKE RESULT ==========");
        System.out.println("mulliganKeepHand calls : " + cbMulligan.get());
        System.out.println("getAbilityToPlay calls : " + cbAbility.get());
        System.out.println("declareAttackers calls : " + cbAttackers.get());
        System.out.println("declareBlockers calls  : " + cbBlockers.get());
        System.out.println("max legal options in one getAbilityToPlay : " + maxLegalOptionsSeen);
        System.out.println("game over? " + game.isGameOver()
                + "  outcome: " + (game.getOutcome() == null ? "n/a" : game.getOutcome().toString()));
        System.out.println("==================================");

        // The proof: our injected controller was actually driven by the engine.
        int total = cbMulligan.get() + cbAbility.get() + cbAttackers.get() + cbBlockers.get();
        Assert.assertTrue(total > 0,
                "Custom PlayerController was never called -> injection seam does NOT work");
    }
}
