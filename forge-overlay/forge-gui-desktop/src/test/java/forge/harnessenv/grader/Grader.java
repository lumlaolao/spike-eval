package forge.harnessenv.grader;

import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;
import java.util.function.BiFunction;
import java.util.function.Function;
import java.util.function.Supplier;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import forge.game.Game;
import forge.game.GameEntity;
import forge.game.card.Card;
import forge.game.combat.Combat;
import forge.game.player.Player;
import forge.game.player.PlayerController;
import forge.game.spellability.SpellAbilityStackInstance;
import forge.game.zone.ZoneType;
import forge.harnessenv.ModelEndpoint;
import forge.harnessenv.SeatFactory;

/**
 * The hidden grader (docs/harness/HARNESS-ENV-FRAMEWORK.md, sections 4.3 to 4.5 and 4.9). It drives a seat only
 * through harness-env-abi/1: {@link SeatFactory#create} and the model endpoint, which is {@link ScriptedModel} on
 * loopback. It never reads a prompt to decide an answer: each instance's board is replayed once per scripted answer
 * and judged by the engine's outcome on five checks (asked, offered, carried out, shown, no text parsing), and gated
 * on {@link AiCounter}: a replay in which the seat entered a Forge-AI decision method fails, whatever it was asked.
 * Each replay runs on its own daemon thread under a wall-clock limit; one that hangs is abandoned and its model
 * requests parked.
 */
public final class Grader {
    private Grader() { }

    static final long MAX_REPLAY_MS = 30_000;
    static final String MODEL = "fake/harness-model";
    static ScriptedModel server;

    static synchronized ScriptedModel server() {
        if (server == null) {
            try { server = new ScriptedModel(); } catch (java.io.IOException e) { throw new IllegalStateException(e); }
        }
        return server;
    }

    /** A seat factory by class name (harness-env.properties' seat.factory), or a named one for the validation seats. */
    static SeatFactory load(String className) {
        try { return (SeatFactory) Class.forName(className).getDeclaredConstructor().newInstance(); }
        catch (ReflectiveOperationException e) { throw new IllegalArgumentException("cannot load seat factory " + className, e); }
    }

    // ------------------------------------------------------------------ instances

    /** One surface: a fresh board, the engine action that reaches the hook, the outcome by engine identity. */
    public static final class Instance {
        public final String id, family, hook;
        final Supplier<Game> board;
        final BiFunction<Game, PlayerController, String> drive;
        final Function<Game, Set<String>> legal;   // engine-legal outcomes on a fresh board ("?x": allowed, not required)
        final int minDistinct;                     // when legal is null: the decision's number of legal answers
        final int minAsks;                         // decisions some replay must put to the model
        final List<List<String>> profiles;
        final String[] show;

        Instance(String id, String family, String hook, Supplier<Game> board, BiFunction<Game, PlayerController, String> drive,
                 Function<Game, Set<String>> legal, int minDistinct, int minAsks, List<List<String>> profiles, String... show) {
            this.id = id; this.family = family; this.hook = hook; this.board = board; this.drive = drive; this.legal = legal;
            this.minDistinct = minDistinct; this.minAsks = minAsks; this.profiles = profiles; this.show = show;
        }
    }

    static final class Run {
        String outcome;
        int decisions;
        List<String> ai = new ArrayList<>();
        String text = "";
        String error;
    }

    static Run run(SeatFactory seat, Instance in, ScriptedModel.Mode mode, List<String> profile, Path logDir) {
        ScriptedModel m = server();
        String id = m.open(mode, profile);
        Run r = new Run();
        Thread t = new Thread(() -> {
            Player me = null;
            try {
                Game g = in.board.get();
                me = g.getPlayers().get(0);
                AiCounter.watch(me);
                PlayerController c = seat.create(g, me, me.getLobbyPlayer(), new ModelEndpoint(m.baseUrl(id), MODEL, logDir));
                me.dangerouslySetController(c);
                r.outcome = in.drive.apply(g, c);
            } catch (Throwable x) {
                r.error = x.getClass().getSimpleName() + ": " + String.valueOf(x.getMessage()).replaceAll("\\s+", " ");
                r.outcome = "ERROR " + r.error;
            } finally {
                if (me != null) r.ai = AiCounter.stop(me);
            }
        }, "replay-" + id);
        t.setDaemon(true);
        t.start();
        try { t.join(MAX_REPLAY_MS); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
        ScriptedModel.Replay rep = m.get(id);
        synchronized (rep) {
            r.decisions = rep.decisions;
            r.text = rep.text.toString();
            if (t.isAlive()) {
                r.error = "hang: no outcome in " + MAX_REPLAY_MS + " ms after " + rep.requests + " model requests";
                r.outcome = "ERROR " + r.error;
            }
        }
        if (r.error != null) {
            String last = r.text.substring(Math.max(0, r.text.lastIndexOf("DECISION")));
            if (!last.isEmpty()) r.error += " | last ask: " + last.substring(0, Math.min(200, last.length())).replaceAll("\\s+", " ");
        }
        return r;
    }

    public static final String[] COLUMNS = {"seat", "instance", "family", "hook", "legal", "reached", "min_requests", "asked",
            "offered", "carried", "shown", "noparse", "pass", "ai", "note"};

    /** One report row: the five checks and the Forge-AI gate of one instance on one seat. */
    public static String grade(String seatName, SeatFactory seat, Instance in) {
        Path logDir = Paths.get(System.getProperty("java.io.tmpdir"), "harness-env-seat-logs");
        Set<String> legal = null;
        String note = "";
        try { if (in.legal != null) legal = in.legal.apply(in.board.get()); }
        catch (Throwable x) { note = "legal set: " + x.getClass().getSimpleName() + " " + x.getMessage(); }
        Set<String> required = new TreeSet<>(), allowed = new TreeSet<>();
        if (legal != null) for (String s : legal) {
            allowed.add(s.startsWith("?") ? s.substring(1) : s);
            if (!s.startsWith("?")) required.add(s);
        }
        Set<String> reached = new TreeSet<>();
        Set<String> aiHooks = new TreeSet<>();
        int minAsked = Integer.MAX_VALUE, maxAsked = 0, ai = 0;
        Run first = null;
        List<String> errors = new ArrayList<>();
        for (List<String> p : in.profiles) {
            Run r = run(seat, in, ScriptedModel.Mode.CALL, p, logDir);
            if (first == null) first = r;
            ai += r.ai.size();
            aiHooks.addAll(r.ai);
            if (r.error != null) { errors.add(r.error + " | answers " + p); continue; }
            reached.add(r.outcome);
            minAsked = Math.min(minAsked, r.decisions);
            maxAsked = Math.max(maxAsked, r.decisions);
        }
        int legalN = legal != null ? required.size() : in.minDistinct;
        Run again = run(seat, in, ScriptedModel.Mode.CALL, in.profiles.get(0), logDir);
        Run prose = run(seat, in, ScriptedModel.Mode.PROSE, in.profiles.get(0), logDir);
        Run empty = run(seat, in, ScriptedModel.Mode.EMPTY, in.profiles.get(0), logDir);
        Run garbage = run(seat, in, ScriptedModel.Mode.GARBAGE, in.profiles.get(0), logDir);
        for (Run r : new Run[] {again, prose, empty, garbage}) { ai += r.ai.size(); aiHooks.addAll(r.ai); }
        // An instance whose legal set could not be computed proves nothing: it fails rather than passing on count.
        boolean ok = errors.isEmpty() && first != null && (in.legal == null || legal != null);
        // Every replay asks at least once; a play passed at its first question stops there, so the deeper decision
        // (the cost, the mode, the target) need only be reached by some replay.
        boolean asked = ok && (legalN < 2 || minAsked >= 1 && maxAsked >= Math.max(1, in.minAsks));
        boolean offered = ok && (legal != null ? reached.containsAll(required) && allowed.containsAll(reached) : reached.size() >= in.minDistinct);
        boolean carried = ok && first.outcome.equals(again.outcome) && (legalN < 2 || reached.size() >= 2);
        boolean shown = ok && !first.text.isEmpty() && !first.text.contains("CARDNAME");
        for (String s : in.show) shown &= first != null && first.text.contains(s);
        boolean noparse = prose.error == null && prose.outcome.equals(empty.outcome) && empty.outcome.equals(garbage.outcome);
        boolean pass = asked && offered && carried && shown && noparse && ai == 0;
        if (!errors.isEmpty()) note += (note.isEmpty() ? "" : "; ") + "error: " + errors.get(0);
        if (!offered && ok) {
            Set<String> missing = new TreeSet<>(required);
            missing.removeAll(reached);
            Set<String> extra = new TreeSet<>(reached);
            if (legal != null) extra.removeAll(allowed);
            note += (note.isEmpty() ? "" : "; ") + "missing " + missing + " extra " + extra;
        }
        if (!noparse) note += (note.isEmpty() ? "" : "; ") + "prose=" + prose.outcome + " empty=" + empty.outcome;
        if (ai > 0) note += (note.isEmpty() ? "" : "; ") + "Forge AI decided " + ai + "x: " + aiHooks;
        return String.join("\t", seatName, in.id, in.family, in.hook, String.valueOf(legalN), String.valueOf(reached.size()),
                String.valueOf(minAsked == Integer.MAX_VALUE ? 0 : minAsked), bit(asked), bit(offered), bit(carried), bit(shown),
                bit(noparse), bit(pass), String.valueOf(ai), note.replace('\t', ' ').replace('\n', ' '));
    }

    private static String bit(boolean b) { return b ? "1" : "0"; }

    // ------------------------------------------------------------------ answer grids (the scripted model's)

    static String json(Object... kv) {
        JsonObject o = new JsonObject();
        for (int i = 0; i < kv.length; i += 2) {
            Object v = kv[i + 1];
            if (v instanceof Integer) o.addProperty((String) kv[i], (Integer) v);
            else if (v instanceof Boolean) o.addProperty((String) kv[i], (Boolean) v);
            else if (v instanceof JsonArray) o.add((String) kv[i], (JsonArray) v);
            else o.addProperty((String) kv[i], String.valueOf(v));
        }
        return o.toString();
    }

    static JsonArray ints(Collection<Integer> xs) { JsonArray a = new JsonArray(); for (int x : xs) a.add(x); return a; }

    /** Every answer in one call, repeated for every decision: each decision reads the field it needs (SPEC.md 3.2). */
    static List<List<String>> repeat(List<String> answers) {
        List<List<String>> out = new ArrayList<>();
        for (String a : answers) { List<String> one = new ArrayList<>(); one.add(a); out.add(one); }
        return out;
    }

    /** One answer per decision, in order: the cartesian product of the steps. */
    @SafeVarargs
    static List<List<String>> seq(List<String>... steps) {
        List<List<String>> out = new ArrayList<>();
        out.add(new ArrayList<>());
        for (List<String> step : steps) {
            List<List<String>> next = new ArrayList<>();
            for (List<String> prefix : out) for (String a : step) { List<String> l = new ArrayList<>(prefix); l.add(a); next.add(l); }
            out = next;
        }
        return out;
    }

    /** option = 1..n+1 (the last out of range), with `options` and `number` saying the same index. */
    static List<String> option(int n) {
        List<String> out = new ArrayList<>();
        for (int i = 1; i <= n + 1; i++) out.add(json("option", i, "options", ints(List.of(i)), "number", i));
        return out;
    }

    /** Every subset of 1..n (ascending) as `options`, plus one out of range; with extraOption > 0 each subset is paired
     *  with option 1..extraOption (an attack: who attacks, then where). */
    static List<String> subsets(int n, int extraOption) {
        List<String> out = new ArrayList<>();
        for (int mask = 0; mask < (1 << n); mask++) {
            List<Integer> s = new ArrayList<>();
            for (int i = 0; i < n; i++) if ((mask & (1 << i)) != 0) s.add(i + 1);
            for (int o = 1; o <= Math.max(1, extraOption); o++)
                out.add(json("options", ints(s), "option", extraOption > 0 ? o : (s.isEmpty() ? 1 : s.get(0)), "blocks", new JsonArray()));
        }
        out.add(json("options", ints(List.of(n + 1)), "option", n + 1));
        return out;
    }

    /** Every subset of 1..n of exactly k, plus one out of range. */
    static List<String> subsetsOf(int n, int k) {
        List<String> out = new ArrayList<>();
        for (int mask = 0; mask < (1 << n); mask++) {
            if (Integer.bitCount(mask) != k) continue;
            List<Integer> s = new ArrayList<>();
            for (int i = 0; i < n; i++) if ((mask & (1 << i)) != 0) s.add(i + 1);
            out.add(json("options", ints(s), "option", s.get(0)));
        }
        out.add(json("options", ints(List.of(n + 1)), "option", n + 1));
        return out;
    }

    /** Every order of 1..n as `options`, plus one out of range. */
    static List<String> orders(int n) {
        List<String> out = new ArrayList<>();
        permute(new ArrayList<>(), n, out);
        out.add(json("options", ints(List.of(n + 1)), "option", n + 1));
        return out;
    }

    private static void permute(List<Integer> prefix, int n, List<String> out) {
        if (prefix.size() == n) { out.add(json("options", ints(prefix), "option", prefix.get(0))); return; }
        for (int i = 1; i <= n; i++) if (!prefix.contains(i)) { List<Integer> p = new ArrayList<>(prefix); p.add(i); permute(p, n, out); }
    }

    /** number = lo-1..hi+1, with `option` the value's place in a menu of lo..hi. */
    static List<String> numbers(int lo, int hi) {
        List<String> out = new ArrayList<>();
        for (int v = lo - 1; v <= hi + 1; v++) out.add(json("number", v, "option", v - lo + 1, "options", ints(List.of(v - lo + 1))));
        return out;
    }

    static List<String> keeps() {
        return List.of(json("keep", true, "option", 1), json("keep", false, "option", 2), json("option", 3));
    }

    /** Every set of (blocker, attacker) pairs over nb blockers and na attackers, plus one out of range. */
    static List<String> blocks(int nb, int na) {
        List<int[]> pairs = new ArrayList<>();
        for (int b = 1; b <= nb; b++) for (int a = 1; a <= na; a++) pairs.add(new int[] {b, a});
        List<String> out = new ArrayList<>();
        for (int mask = 0; mask < (1 << pairs.size()); mask++) {
            JsonArray arr = new JsonArray();
            for (int i = 0; i < pairs.size(); i++) if ((mask & (1 << i)) != 0) {
                JsonObject o = new JsonObject();
                o.addProperty("blocker", pairs.get(i)[0]);
                o.addProperty("attacker", pairs.get(i)[1]);
                arr.add(o);
            }
            out.add(json("blocks", arr));
        }
        JsonArray bad = new JsonArray();
        JsonObject o = new JsonObject();
        o.addProperty("blocker", nb + 1);
        o.addProperty("attacker", 1);
        bad.add(o);
        out.add(json("blocks", bad));
        return out;
    }

    /** Every split of `total` over two recipients as `amounts`; `number` and `option` say the first share. */
    static List<String> splits(int total) {
        List<String> out = new ArrayList<>();
        for (int k = 0; k <= total; k++) out.add(json("amounts", ints(List.of(k, total - k)), "number", k, "option", k + 1));
        return out;
    }

    // ------------------------------------------------------------------ outcomes by engine identity

    static String names(Collection<Card> cs) {
        List<String> n = new ArrayList<>();
        if (cs != null) for (Card c : cs) n.add(c.getName());
        java.util.Collections.sort(n);
        return String.join(",", n);
    }

    static String ordered(Collection<Card> cs) {
        List<String> n = new ArrayList<>();
        if (cs != null) for (Card c : cs) n.add(c.getName());
        return String.join(">", n);
    }

    /** The visible state a decision can change: every zone of both players with tapped state, damage, power and
     *  toughness on the battlefield, life, the mana pool by colour, and the stack with its targets. */
    static String digest(Game g) {
        StringBuilder b = new StringBuilder();
        for (Player p : g.getPlayers()) {
            b.append(p.getName()).append(" life=").append(p.getLife()).append(" pool=").append(p.getManaPool().totalMana());
            for (byte c : forge.card.MagicColor.WUBRGC) {
                int n = p.getManaPool().getAmountOfColor(c);
                if (n > 0) b.append(forge.card.MagicColor.toShortString(c)).append(n);
            }
            b.append(';');
            for (ZoneType z : new ZoneType[] {ZoneType.Hand, ZoneType.Graveyard, ZoneType.Exile, ZoneType.Library}) {
                b.append(z).append('=').append(names(p.getCardsIn(z))).append(';');
            }
            List<String> bf = new ArrayList<>();
            for (Card c : p.getCardsIn(ZoneType.Battlefield))
                bf.add(c.getName() + (c.isTapped() ? "(T)" : "") + (c.isCreature() ? c.getNetPower() + "/" + c.getNetToughness() + " d" + c.getDamage() : ""));
            java.util.Collections.sort(bf);
            b.append("bf=").append(String.join(",", bf)).append('|');
        }
        List<String> stack = new ArrayList<>();
        for (SpellAbilityStackInstance si : g.getStack()) stack.add(si.getSpellAbility().getHostCard().getName() + "->" + si.getSpellAbility().getTargets());
        b.append("stack=").append(stack);
        return b.toString();
    }

    /** The seat's attack as "attacker->defender" pairs, sorted. */
    static String attacks(Combat c) {
        Set<String> s = new TreeSet<>();
        for (java.util.Map.Entry<Card, GameEntity> e : c.getAttackersAndDefenders().entrySet()) s.add(e.getKey().getName() + "->" + e.getValue().getName());
        return s.isEmpty() ? "no attack" : String.join(";", s);
    }

    /** The seat's blocks as "blocker>attacker" pairs, sorted. */
    static String blocksOf(Combat c) {
        Set<String> s = new TreeSet<>();
        for (Card a : c.getAttackers()) for (Card b : c.getBlockers(a)) s.add(b.getName() + ">" + a.getName());
        return s.isEmpty() ? "no blocks" : String.join(";", s);
    }

    static Set<String> set(String... xs) { return new LinkedHashSet<>(List.of(xs)); }
}
