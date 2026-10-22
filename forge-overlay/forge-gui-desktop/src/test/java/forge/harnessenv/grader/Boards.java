package forge.harnessenv.grader;

import java.util.ArrayList;
import java.util.List;

import forge.GuiDesktop;
import forge.StaticData;
import forge.ai.LobbyPlayerAi;
import forge.deck.Deck;
import forge.game.Game;
import forge.game.GameRules;
import forge.game.GameStage;
import forge.game.GameType;
import forge.game.Match;
import forge.game.card.Card;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.player.RegisteredPlayer;
import forge.game.spellability.SpellAbility;
import forge.game.zone.ZoneType;
import forge.gui.GuiBase;
import forge.item.IPaperCard;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;

/**
 * The grader's own board helpers: the engine set up the way the harness's tests set it up, with no class of any
 * candidate on the path, so the grader compiles and runs against a harness it did not write.
 */
public final class Boards {
    private Boards() { }

    private static boolean initialized;

    /** The card database, loaded eagerly in English, once per JVM. */
    public static synchronized void initModel() {
        if (initialized) return;
        GuiBase.setInterface(new GuiDesktop());
        FModel.initialize(null, preferences -> {
            preferences.setPref(FPref.LOAD_CARD_SCRIPTS_LAZILY, false);
            preferences.setPref(FPref.UI_LANGUAGE, "en-US");
            return null;
        });
        initialized = true;
    }

    /** Two players, "Seat" (the graded one, first) and "Rival" (Forge's AI), at the given phase of the Seat's turn. */
    public static Game bareGame(PhaseType phase) {
        List<RegisteredPlayer> players = new ArrayList<>();
        Deck d = new Deck();
        players.add(new RegisteredPlayer(d).setPlayer(new LobbyPlayerAi("Seat", null)));
        players.add(new RegisteredPlayer(d).setPlayer(new LobbyPlayerAi("Rival", null)));
        GameRules rules = new GameRules(GameType.Constructed);
        Game game = new Game(players, rules, new Match(rules, players, "HarnessEnvGrader"));
        game.setAge(GameStage.Play);
        game.getPhaseHandler().devModeSet(phase, game.getPlayers().get(0));
        return game;
    }

    /** A real card from the database in a zone; on the battlefield it can act now. */
    public static Card card(String name, Player p, ZoneType zone) {
        IPaperCard pc = FModel.getMagicDb().getCommonCards().getCard(name);
        if (pc == null) { StaticData.instance().attemptToLoadCard(name); pc = FModel.getMagicDb().getCommonCards().getCard(name); }
        if (pc == null) throw new IllegalArgumentException("card not in the engine DB: " + name);
        Card c = Card.fromPaperCard(pc, p);
        c.setGameTimestamp(p.getGame().getNextTimestamp());
        p.getZone(zone).add(c);
        if (zone == ZoneType.Battlefield) c.setSickness(false);
        return c;
    }

    /** Triggers onto the stack and the stack resolved, at most 30 rounds, then state-based actions. */
    public static void resolveAll(Game g) {
        for (int i = 0; i < 30; i++) {
            g.getTriggerHandler().runWaitingTriggers();
            g.getStack().addAllTriggeredAbilitiesToStack();
            if (g.getStack().isEmpty()) break;
            g.getStack().resolveStack();
        }
        g.getAction().checkStateEffects(true);
    }

    /** The rival casts {@code spell} (targeting {@code target}, if any) and it waits on the stack. */
    public static void rivalCasts(Game g, String spell, Card target) {
        Player rival = g.getPlayers().get(1);
        g.getPhaseHandler().devModeSet(PhaseType.MAIN1, rival);
        Card c = card(spell, rival, ZoneType.Hand);
        SpellAbility sa = c.getFirstSpellAbility();
        sa.setActivatingPlayer(rival);
        if (target != null) sa.getTargets().add(target);
        sa.setHostCard(g.getAction().moveToStack(c, sa));
        g.getStack().add(sa);
    }
}
