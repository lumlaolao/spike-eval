package forge.spike;

import java.io.File;
import java.util.ArrayList;
import java.util.EnumSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.google.common.eventbus.Subscribe;

import forge.deck.Deck;
import forge.game.Game;
import forge.game.GameRules;
import forge.game.GameType;
import forge.game.event.GameEvent;
import forge.game.player.Player;
import forge.game.player.RegisteredPlayer;

/**
 * Games of more than two seats (PLAYERS=3 or 4) and Commander games (GAME_FORMAT=commander): the seat count, seats C
 * and D, the rules, and each seat's place on the game line. A two-seat Constructed game takes none of this: its
 * seats, rules and game line are built as before.
 */
final class MultiSeat {
    private MultiSeat() {}

    /** The seat ids, in seat order: the game line's {@code winner} and each {@code players} entry use them. */
    static final String[] IDS = {"A", "B", "C", "D"};

    /** PLAYERS: 2 when unset; 2 to 4, else refused. */
    static int players(Map<String, String> env) {
        String v = env.get("PLAYERS");
        if (v == null || v.trim().isEmpty()) return 2;
        int n;
        try { n = Integer.parseInt(v.trim()); } catch (NumberFormatException e) { n = -1; }
        if (n < 2 || n > IDS.length) throw new IllegalStateException("PLAYERS=" + v + ": a game seats 2 to 4 players");
        return n;
    }

    /** GAME_FORMAT=commander: the engine's Commander game (the command zone, 40 life, commander damage and tax). */
    static boolean commander(Map<String, String> env) {
        return "commander".equals(LlmSpikeGui.PlayerControllerLLM.gameFormat(env.get("GAME_FORMAT")));
    }

    /** Whether the game line carries {@code n_players} and {@code players}: more than two seats, or Commander. */
    static boolean placed(Map<String, String> env) {
        return players(env) > 2 || commander(env);
    }

    /** The rules: Constructed as before, or Commander with its variant applied (as Forge's lobby applies it). */
    static GameRules rules(boolean commander) {
        if (!commander) return new GameRules(GameType.Constructed);
        GameRules rules = new GameRules(GameType.Commander);
        rules.setAppliedVariants(EnumSet.of(GameType.Commander));
        return rules;
    }

    /** A seat's registration: a Commander seat's deck must name its commander. */
    static RegisteredPlayer registered(Deck deck, boolean commander) {
        if (!commander) return new RegisteredPlayer(deck);
        if (deck.getCommanders().isEmpty())
            throw new IllegalStateException("the deck '" + deck.getName() + "' names no commander ([Commander] in a .dck): "
                    + "a Commander game needs one");
        return RegisteredPlayer.forCommander(deck);
    }

    /** Seat C's or D's identity name: DECK_X_NAME, else its deck file's name without the extension. */
    static String name(Map<String, String> env, String id) {
        String n = env.get("DECK_" + id + "_NAME");
        if (n != null && !n.trim().isEmpty()) return n.trim();
        String path = env.get("DECK_" + id);
        return path == null ? "Seat " + id : new File(path).getName().replaceFirst("\\.(dck|txt)$", "");
    }

    /** Seats C and D (PLAYERS=3, 4), each from its own DECK_X, CONTROLLER_X (default ai), SMART_MODEL_X / FAST_MODEL_X,
     *  AI_PROFILE_X and PLAN_X, as seats A and B are built. {@code taken}: the names already seated, which a name must
     *  not repeat (the game line finds a seat by its name). */
    static List<RegisteredPlayer> extraSeats(Map<String, String> env, boolean commander, List<String> taken) {
        List<RegisteredPlayer> out = new ArrayList<>();
        List<String> names = new ArrayList<>(taken);
        boolean optedIn = "1".equals(env.get(ClaudeCliBridge.OPT_IN));
        for (int i = 2; i < players(env); i++) {
            String id = IDS[i];
            String path = env.get("DECK_" + id);
            if (path == null || path.trim().isEmpty())
                throw new IllegalStateException("PLAYERS=" + players(env) + " seats " + id + ": set DECK_" + id);
            String name = name(env, id);
            if (names.contains(name))
                throw new IllegalStateException("seat " + id + " is named '" + name + "' as another seat is: set DECK_"
                        + id + "_NAME");
            names.add(name);
            String ctl = env.getOrDefault("CONTROLLER_" + id, "ai");
            if (LlmSpikeGui.HUMAN.equals(ctl)) throw new IllegalStateException("seat " + id + ": only seat A or B may be a person");
            String missing = LlmSpikeGui.missingModel(ctl, id, env);
            if (missing != null) throw new IllegalStateException(missing);
            String smart = env.getOrDefault("SMART_MODEL_" + id, env.getOrDefault("SMART_MODEL", "opus"));
            String fast = env.getOrDefault("FAST_MODEL_" + id, env.getOrDefault("FAST_MODEL", "haiku"));
            LlmSpikeGui.LLMBridgeFactory.requireCliOptIn(smart, ctl, optedIn, "seat " + id);
            LlmSpikeGui.LLMBridgeFactory.requireCliOptIn(fast, ctl, optedIn, "seat " + id);
            Deck deck;
            try { deck = LlmSpikeGui.loadDeck(name, path); }
            catch (Exception e) { throw new IllegalStateException("[DECK] seat " + id + " deck load failed: " + e, e); }
            String plan = env.get("PLAN_" + id);
            if (plan == null) plan = LlmSpikeGui.loadPlanSidecar(path, "");
            if (!"0".equals(env.getOrDefault("DECKLIST", "1"))) {
                String list = LlmSpikeGui.decklist(deck), text = LlmSpikeGui.cardText(deck);
                if (!list.isEmpty()) plan = (plan.isEmpty() ? "" : plan + "\n")
                        + "YOUR DECKLIST (you know your own deck's contents; only the draw order is hidden): " + list;
                if (!text.isEmpty()) plan += "\nYOUR CARDS (what each card in your deck does):\n" + text;
            }
            out.add(registered(deck, commander).setPlayer(LlmSpikeGui.seatFor(ctl, name, plan, smart, fast,
                    LlmSpikeGui.aiProfile(env.get("AI_PROFILE_" + id)))));
            System.out.println("[SEATS] " + id + "=" + name + "(" + ctl + "," + LlmSpikeGui.seatModel(ctl, smart) + ")");
        }
        return out;
    }

    /** The turn each player left the game, noted on the game's own events (the engine records the order of losses,
     *  {@code Game.getLostPlayers}, not their turns). The turn is the one in progress at the first event after the
     *  loss. */
    static final class Places {
        private final Game game;
        private final Map<Player, Integer> lostAt = new LinkedHashMap<>();

        Places(Game game) {
            this.game = game;
            game.subscribeToEvents(this);
        }

        @Subscribe
        public void onEvent(GameEvent ev) { note(); }

        synchronized void note() {
            int turn = game.getPhaseHandler().getTurn();
            for (Player p : game.getLostPlayers()) lostAt.putIfAbsent(p, turn);
        }

        synchronized Integer lostTurn(Player p) { return lostAt.get(p); }
    }

    /** A game of more than two players: the seat reads several opponents (each named where one used to be "the
     *  opponent"). A two-player game, Commander included, reads as before. */
    static boolean many(Game g) {
        try { return g != null && g.getRegisteredPlayers().size() > 2; } catch (Exception e) { return false; }
    }

    /** The seat count the system prompt states, from the environment the seats are built from. */
    static int playersFromEnv() {
        try {
            String v = LlmSpikeGui.env("PLAYERS");
            return v == null || v.trim().isEmpty() ? 2 : Integer.parseInt(v.trim());
        } catch (Exception e) { return 2; }
    }

    /** The system prompt's opening clause after the seat's name: "against one opponent" in a two-player game, else
     *  how many and how a free-for-all is won; a Commander game adds its own rules (the engine enforces them). */
    static String opponentsClause(int players, String format) {
        String who = players > 2
                ? "against " + (players - 1) + " opponents, every player against every other: a player who loses leaves "
                  + "the game, and the last player left wins. Your goal is to be that player."
                : "against one opponent. Your goal is to win the game.";
        if (!"commander".equals(format)) return who;
        return who + " Commander rules: each player starts at 40 life with a commander in the command zone; a player "
                + "dealt 21 or more combat damage by one commander loses; each time you cast your commander from the "
                + "command zone it costs {2} more for each earlier time.";
    }

    /** The faithful mulligan ask in a game of more than two players, where the rules differ from two-player: the
     *  first mulligan is free (Forge's MulliganService: a game of more than two players), and no player skips the
     *  draw of their first turn (CR 103.8c). A free mulligan is the first, from a full hand of seven. */
    static String mulliganPrompt(int size, int lands, String handNames, boolean onThePlay) {
        boolean free = size >= 7;
        return "MULLIGAN: you " + (onThePlay ? "take the first turn" : "do not take the first turn")
                + " (in a game of more than two players every player draws on their first turn). This hand has " + size
                + " cards, " + lands + " of them lands: " + handNames + ". A mulligan gives you a new hand of "
                + (free ? size + " cards (the first mulligan is free in a game of more than two players; each later one "
                          + "costs one card" : Math.max(0, size - 1) + " cards (each mulligan after the first costs one card")
                + ", and you cannot go below 5); keeping plays this hand. Keep or mulligan? End with exactly one word: KEEP "
                + "or MULLIGAN.";
    }

    /** Set by the headless runner for a placed game. */
    static volatile Places places;

    /** The seat id of a lobby player's name, or null: seats are found by their names. */
    static String idOf(Map<String, String> env, String name) {
        for (int i = 0; i < players(env); i++) if (seatName(env, i).equals(name)) return IDS[i];
        return null;
    }

    /** Seat {@code i}'s name: seats A and B keep their two-seat defaults (LlmSpikeGui.buildPlayers), C and D
     *  {@link #name}. */
    static String seatName(Map<String, String> env, int i) {
        if (i == 0) return env.getOrDefault("DECK_A_NAME", "Grixis Reanimator");
        if (i == 1) return env.getOrDefault("DECK_B_NAME", "Mardu Energy");
        return name(env, IDS[i]);
    }

    /** The game line's {@code n_players} and {@code players}: per seat its id, name, controller, deck file, place (1 for
     *  the winner, then by the reverse order of losses; null for a seat still in a game that ended without a winner),
     *  the turn it left and why ({@code won}, the engine's loss reason, or {@code draw}, {@code capped}, {@code error}
     *  for a seat still playing), and its decisions lost to a dead model bridge. */
    static String gameLineFields(Game game, Map<String, String> env, String winnerName, boolean capped, String error) {
        int n = players(env);
        Map<String, Player> byId = new LinkedHashMap<>();
        List<Player> lost = new ArrayList<>();
        if (game != null) {
            for (Player p : game.getRegisteredPlayers()) {
                String id = idOf(env, p.getName());
                if (id != null) byId.put(id, p);
            }
            lost.addAll(game.getLostPlayers());
        }
        Places pl = places;
        if (pl != null) pl.note();
        StringBuilder b = new StringBuilder(",\"n_players\":" + n + ",\"players\":[");
        for (int i = 0; i < n; i++) {
            String id = IDS[i];
            Player p = byId.get(id);
            String name = p != null ? p.getName() : seatName(env, i);
            Integer place = null, turn = null;
            String reason;
            if (p != null && winnerName != null && winnerName.equals(p.getName())) { place = 1; reason = "won"; }
            else if (p != null && lost.contains(p)) {
                place = n - lost.indexOf(p);
                turn = pl == null ? null : pl.lostTurn(p);
                reason = p.getOutcome() == null || p.getOutcome().lossState == null ? "lost" : String.valueOf(p.getOutcome().lossState);
            } else reason = error != null ? "error" : capped ? "capped" : "draw";
            String deck = env.get("DECK_" + id);
            b.append(i == 0 ? "" : ",").append("{\"seat\":\"").append(id).append("\"")
                    .append(",\"name\":\"").append(LlmSpikeGui.jsonEscape(name)).append("\"")
                    .append(",\"controller\":\"").append(LlmSpikeGui.jsonEscape(env.getOrDefault("CONTROLLER_" + id, i < 2 ? "llm" : "ai"))).append("\"")
                    .append(",\"deck\":\"").append(LlmSpikeGui.jsonEscape(deck == null ? "" : new File(deck).getName())).append("\"")
                    .append(",\"place\":").append(place)
                    .append(",\"eliminated_turn\":").append(turn)
                    .append(",\"reason\":\"").append(LlmSpikeGui.jsonEscape(reason)).append("\"")
                    .append(",\"bridge_down_decisions\":").append(LlmSpikeGui.bridgeDownDecisions(name))
                    .append("}");
        }
        return b.append("]").toString();
    }

    /** The winner a placed game records: the winning seat's id ("invalid" for a name no seat has), "draw" when none won. */
    static String winner(Map<String, String> env, String winnerName) {
        if (winnerName == null) return "draw";
        String id = idOf(env, winnerName);
        return id == null ? "invalid" : id;
    }
}
