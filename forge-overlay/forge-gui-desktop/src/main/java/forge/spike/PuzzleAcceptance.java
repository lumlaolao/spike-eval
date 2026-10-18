package forge.spike;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

import forge.game.Game;
import forge.game.GameOutcome;
import forge.game.card.Card;
import forge.game.card.CounterType;
import forge.game.player.Player;
import forge.game.zone.ZoneType;

/**
 * The acceptance state of a rules puzzle (eval/BENCHMARK-V1.md, "The shape every puzzle takes"): the
 * {@code [acceptance]} section of a .pzl, one condition per line, all of which must hold on the final engine state.
 * Forge ignores the section; {@link LlmSpikePuzzle} reads it, and a solve is then "the conditions held when the game
 * ended", not "the solver won". Only engine state is read: zones, names, tokens, counters, life, the winner.
 *
 * <pre>
 * life p0 &gt;= 1                                      p0 is the solver, p1 the opponent
 * count p1 Graveyard "Tarmogoyf" = 1                 cards by exact name, or * for any
 * tokens p0 Battlefield "Goblin" = 2                 tokens only
 * counters p0 Battlefield "Walking Ballista" P1P1 &gt;= 3   summed over the matching cards
 * winner p0                                          p0, p1 or none
 * </pre>
 * Operators: = != &lt; &lt;= &gt; &gt;=. Lines starting with # are comments. A line it cannot read is refused, never skipped.
 */
final class PuzzleAcceptance {

    /** What a condition can see of one card. */
    interface CardFacts {
        String name();
        boolean token();
        int counters(String type);
    }

    /** What a condition can see of the final state. */
    interface View {
        int life(int player);
        List<CardFacts> cards(int player, String zone);
        String winner();
    }

    static final class Verdict {
        final boolean accepted; final List<String> failed;
        Verdict(List<String> failed) { this.failed = failed; this.accepted = failed.isEmpty(); }
    }

    static final List<String> ZONES = Arrays.asList("Battlefield", "Graveyard", "Exile", "Hand", "Library",
            "Command", "Stack");
    private static final List<String> OPS = Arrays.asList("=", "!=", "<", "<=", ">", ">=");

    private static final class Cond {
        final String text, kind, zone, name, counter, op, who;
        final int player, n;
        Cond(String text, String kind, int player, String zone, String name, String counter, String op, int n,
             String who) {
            this.text = text; this.kind = kind; this.player = player; this.zone = zone; this.name = name;
            this.counter = counter; this.op = op; this.n = n; this.who = who;
        }
    }

    private final List<Cond> conds;

    private PuzzleAcceptance(List<Cond> conds) { this.conds = conds; }

    int size() { return conds.size(); }

    static PuzzleAcceptance parse(List<String> lines) {
        List<Cond> out = new ArrayList<>();
        for (String raw : lines) {
            String line = raw == null ? "" : raw.trim();
            if (line.isEmpty() || line.startsWith("#")) continue;
            out.add(parseLine(line));
        }
        if (out.isEmpty()) throw new IllegalArgumentException("[acceptance] has no condition: it would accept any game");
        return new PuzzleAcceptance(out);
    }

    private static Cond parseLine(String line) {
        List<String> w = words(line);
        String kind = w.isEmpty() ? "" : w.get(0);
        try {
            switch (kind) {
                case "winner":
                    need(w.size() == 2 && Arrays.asList("p0", "p1", "none").contains(w.get(1)));
                    return new Cond(line, kind, -1, null, null, null, "=", 0, w.get(1));
                case "life":
                    need(w.size() == 4);
                    return new Cond(line, kind, player(w.get(1)), null, null, null, op(w.get(2)), num(w.get(3)), null);
                case "count":
                case "tokens":
                    need(w.size() == 6);
                    return new Cond(line, kind, player(w.get(1)), zone(w.get(2)), name(w.get(3)), null, op(w.get(4)),
                            num(w.get(5)), null);
                case "counters":
                    need(w.size() == 7);
                    return new Cond(line, kind, player(w.get(1)), zone(w.get(2)), name(w.get(3)), w.get(4),
                            op(w.get(5)), num(w.get(6)), null);
                default:
                    throw new IllegalArgumentException("unknown condition");
            }
        } catch (IllegalArgumentException e) {
            throw new IllegalArgumentException("[acceptance] cannot read '" + line + "': " + e.getMessage());
        }
    }

    /** Split on spaces, keeping a "quoted name" as one word (quotes kept, so a bare name is told apart). */
    private static List<String> words(String line) {
        List<String> w = new ArrayList<>();
        StringBuilder cur = new StringBuilder();
        boolean quoted = false;
        for (char c : line.toCharArray()) {
            if (c == '"') { quoted = !quoted; cur.append(c); }
            else if (c == ' ' && !quoted) { if (cur.length() > 0) { w.add(cur.toString()); cur.setLength(0); } }
            else cur.append(c);
        }
        if (quoted) throw new IllegalArgumentException("[acceptance] cannot read '" + line + "': unclosed quote");
        if (cur.length() > 0) w.add(cur.toString());
        return w;
    }

    private static void need(boolean ok) { if (!ok) throw new IllegalArgumentException("wrong number of words"); }

    private static int player(String s) {
        if ("p0".equals(s)) return 0;
        if ("p1".equals(s)) return 1;
        throw new IllegalArgumentException("player must be p0 or p1");
    }

    private static String op(String s) {
        if (!OPS.contains(s)) throw new IllegalArgumentException("operator must be one of " + OPS);
        return s;
    }

    private static int num(String s) {
        try { return Integer.parseInt(s); } catch (NumberFormatException e) {
            throw new IllegalArgumentException("not a number: " + s);
        }
    }

    private static String zone(String s) {
        if (!ZONES.contains(s)) throw new IllegalArgumentException("zone must be one of " + ZONES);
        return s;
    }

    private static String name(String s) {
        if ("*".equals(s)) return null;
        if (s.length() >= 3 && s.startsWith("\"") && s.endsWith("\"")) return s.substring(1, s.length() - 1);
        throw new IllegalArgumentException("a card name is quoted, or * for any card");
    }

    Verdict evaluate(View v) {
        List<String> failed = new ArrayList<>();
        for (Cond c : conds) {
            if ("winner".equals(c.kind)) {
                String was = v.winner();
                if (!c.who.equals(was)) failed.add(c.text + " (was " + was + ")");
                continue;
            }
            int was = 0;
            if ("life".equals(c.kind)) {
                was = v.life(c.player);
            } else {
                for (CardFacts f : v.cards(c.player, c.zone)) {
                    if (c.name != null && !c.name.equals(f.name())) continue;
                    if ("tokens".equals(c.kind) && !f.token()) continue;
                    was += "counters".equals(c.kind) ? f.counters(c.counter) : 1;
                }
            }
            if (!holds(was, c.op, c.n)) failed.add(c.text + " (was " + was + ")");
        }
        return new Verdict(failed);
    }

    private static boolean holds(int a, String op, int b) {
        switch (op) {
            case "=": return a == b;
            case "!=": return a != b;
            case "<": return a < b;
            case "<=": return a <= b;
            case ">": return a > b;
            default: return a >= b;
        }
    }

    /** The final state of a Forge game; player 0 is the solver (the first registered player). */
    static View of(Game game) {
        return new View() {
            Player p(int i) { return game.getRegisteredPlayers().get(i); }
            public int life(int i) { return p(i).getLife(); }
            public List<CardFacts> cards(int i, String zone) {
                List<CardFacts> out = new ArrayList<>();
                for (Card c : p(i).getCardsIn(ZoneType.smartValueOf(zone))) {
                    out.add(new CardFacts() {
                        public String name() { return c.getName(); }
                        public boolean token() { return c.isToken(); }
                        public int counters(String type) {
                            CounterType t = CounterType.getType(type);
                            return t == null ? 0 : c.getCounters(t);
                        }
                    });
                }
                return out;
            }
            public String winner() {
                GameOutcome o = game.getOutcome();
                if (o == null) return "none";
                if (o.isWinner(p(0).getLobbyPlayer())) return "p0";
                if (o.isWinner(p(1).getLobbyPlayer())) return "p1";
                return "none";
            }
        };
    }
}
