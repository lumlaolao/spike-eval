package forge.harnessenv.toys;

import java.util.Set;

import forge.LobbyPlayer;
import forge.game.Game;
import forge.game.player.Player;
import forge.game.player.PlayerController;
import forge.harnessenv.ModelEndpoint;
import forge.harnessenv.SeatFactory;

/**
 * Toy candidates for validating the grader, each a {@link SeatFactory} a submission could name in
 * harness-env.properties. None knows the grader's instances; each is a known kind of wrong harness.
 */
public final class Toys {
    private Toys() { }

    /** The default bridge: priority (play or pass) to the model, every other decision Forge's AI, inherited. */
    public static final class PriorityOnly implements SeatFactory {
        @Override public PlayerController create(Game game, Player player, LobbyPlayer lobbyPlayer, ModelEndpoint model) {
            return new ToySeat(game, player, model, false) { };
        }
    }

    /** Routes eleven decisions to the model and reads a number out of a reply that calls nothing. */
    public static final class TextParser implements SeatFactory {
        @Override public PlayerController create(Game game, Player player, LobbyPlayer lobbyPlayer, ModelEndpoint model) {
            return new RoutedSeat(game, player, model, true, Set.of(), Set.of());
        }
    }

    /** Routes the same decisions, but lets Forge's AI decide the sacrifice, tap-or-untap and the mulligan after asking,
     *  and consults Forge's AI before every colour (then uses the model's answer). */
    public static final class AiDelegate implements SeatFactory {
        @Override public PlayerController create(Game game, Player player, LobbyPlayer lobbyPlayer, ModelEndpoint model) {
            return new RoutedSeat(game, player, model, false, Set.of("choosePermanentsToSacrifice", "chooseBinary", "mulliganKeepHand"),
                    Set.of("chooseColor"));
        }
    }
}
