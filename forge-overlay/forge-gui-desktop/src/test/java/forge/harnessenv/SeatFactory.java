package forge.harnessenv;

import forge.LobbyPlayer;
import forge.game.Game;
import forge.game.player.Player;
import forge.game.player.PlayerController;

/**
 * harness-env-abi/1 (eval/harness_env/SPEC.md, section 3.1): what a candidate harness implements so the hidden grader
 * can drive it. The grader builds the game, calls {@link #create} for the seat it tests and installs the controller;
 * every model request the controller makes goes to {@code model}. Named in the submission's
 * harness-env.properties as {@code seat.factory=<class>}, with a public no-argument constructor.
 */
public interface SeatFactory {
    PlayerController create(Game game, Player player, LobbyPlayer lobbyPlayer, ModelEndpoint model);
}
