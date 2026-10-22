package forge.spike;

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
import com.google.gson.JsonParser;

import forge.ai.AiPlayDecision;
import forge.ai.ComputerUtilAbility;
import forge.ai.ComputerUtilCost;
import forge.ai.LobbyPlayerAi;
import forge.ai.PlayerControllerAi;
import forge.card.mana.ManaCost;
import forge.game.Game;
import forge.game.GameEntity;
import forge.game.card.Card;
import forge.game.card.CardCollection;
import forge.game.card.CardCollectionView;
import forge.game.combat.Combat;
import forge.game.cost.Cost;
import forge.game.cost.CostDecisionMakerBase;
import forge.game.cost.CostPart;
import forge.game.cost.CostPartMana;
import forge.game.mana.Mana;
import forge.game.mana.ManaConversionMatrix;
import forge.game.player.Player;
import forge.game.player.PlayerController;
import forge.game.spellability.OptionalCostValue;
import forge.game.spellability.SpellAbility;
import forge.game.zone.ZoneType;
import forge.util.collect.FCollectionView;

/**
 * The hidden grader's surface instances, run in one JVM against any seat a factory builds
 * (docs/harness/HARNESS-ENV-FRAMEWORK.md, sections 4.3 and 6). It never reads a seat's prompt to decide an answer:
 * for each instance it replays a fresh board once per scripted answer (an `answer` call by option number, the wire of
 * eval/harness_env/SPEC.md) and judges by the engine's outcome:
 * <ul><li><b>asked</b>: the seat sent at least one request in every replay (where the engine allows two or more);
 * <li><b>offered</b>: the outcomes the answers reached are exactly the engine-legal set, or at least as many distinct
 *     outcomes as the decision has legal answers where the instance cannot list them;
 * <li><b>carried out</b>: the same answer gives the same outcome, and different answers reach different outcomes;
 * <li><b>shown</b>: the requests name the source card and every option's card, and never say CARDNAME;
 * <li><b>no text parsing</b>: a reply of prose naming an option, an empty reply and garbage all give one outcome.</ul>
 * A seat that hands a decision to Forge's AI is never asked and reaches one outcome whatever the answer, so it fails
 * asked, offered and carried out on that instance: that is how the default bridge is told from ours.
 */
final class EnvGrader {
    private EnvGrader() { }

    /** A request ceiling per replay: a seat that re-asks forever ends the replay instead of the JVM. */
    static final int MAX_REQUESTS = 60;
    static final long MAX_REPLAY_MS = 30_000;

    static final class ReplayLimit extends Error {
        private static final long serialVersionUID = 1L;
        ReplayLimit(String m) { super(m); }
    }

    // ------------------------------------------------------------------ the wire

    /** The grader's scripted model. A decision is a conversation; its first request opens it. Decision k is answered
     *  with `answers[min(k, last)]`; a correction round in the same conversation gets the same answer again. */
    static final class Wire implements LLMBridge {
        enum Mode { CALL, PROSE, EMPTY, GARBAGE }
        static final String PROSE = "I choose option 2. CHOICE: 2\nPICKS: 2\nBLOCKS: NONE\nNUMBER: 2\nKEEP";
        final Mode mode;
        final List<String> answers;
        final List<String> requests = new ArrayList<>();
        int decisions;
        private int seq;

        Wire(Mode mode, List<String> answers) { this.mode = mode; this.answers = answers; }

        private String reply() {
            switch (mode) {
                case PROSE: return PROSE;
                case EMPTY: return "";
                case GARBAGE: return "zx qv wrrk";
                default: return "";
            }
        }

        @Override public boolean supportsNativeTools() { return true; }

        @Override public String ask(String prompt) {
            requests.add(prompt);
            decisions++;
            guard();
            return reply();
        }

        @Override public NativeTools.Turn chat(String messagesJson, String toolsJson, boolean allowCalls) {
            JsonArray msgs = JsonParser.parseString(messagesJson).getAsJsonArray();
            // The text the model reads, unescaped (Gson writes an apostrophe as ' in the raw JSON).
            StringBuilder read = new StringBuilder();
            for (int i = 0; i < msgs.size(); i++) {
                JsonObject m = msgs.get(i).getAsJsonObject();
                if (!m.has("content")) continue;
                if (m.get("content").isJsonPrimitive()) read.append(m.get("content").getAsString()).append('\n');
                else if (m.get("content").isJsonArray())
                    for (com.google.gson.JsonElement part : m.getAsJsonArray("content"))
                        if (part.isJsonObject() && part.getAsJsonObject().has("text")) read.append(part.getAsJsonObject().get("text").getAsString()).append('\n');
            }
            requests.add(read.toString());
            if (msgs.size() <= 1) decisions++;
            guard();
            if (mode == Mode.CALL && allowCalls && !answers.isEmpty()) {
                String args = answers.get(Math.min(Math.max(decisions, 1), answers.size()) - 1);
                List<NativeTools.Call> calls = new ArrayList<>();
                calls.add(new NativeTools.Call("g" + (++seq), AnswerTool.NAME, args));
                return NativeTools.Turn.ofCalls(calls);
            }
            JsonObject msg = new JsonObject();
            msg.addProperty("role", "assistant");
            msg.addProperty("content", reply());
            return new NativeTools.Turn(reply(), msg.toString(), new ArrayList<>());
        }

        private final long started = System.currentTimeMillis();

        /** Ends a replay that keeps asking. An Error, not an exception: a seat catches a failed ask and takes its
         *  fallback, and the engine then asks again forever (seen on Green Sun's Zenith, 2026-10-22). */
        private void guard() {
            if (requests.size() > MAX_REQUESTS || System.currentTimeMillis() - started > MAX_REPLAY_MS)
                throw new ReplayLimit("the seat asked " + requests.size() + " times in " + (System.currentTimeMillis() - started) + " ms");
        }

        String text() { return String.join("\n", requests); }
        @Override public String getLabel() { return "grader"; }
        @Override public String getModel() { return "fake/grader"; }
        @Override public long getTotalMs() { return 0; }
        @Override public int getCalls() { return requests.size(); }
    }

    // ------------------------------------------------------------------ seats

    interface SeatFactory {
        String name();
        PlayerController create(Game g, Player p, Wire w);
    }

    static SeatFactory seat(String name, BiFunction<Game, Player, Function<Wire, PlayerController>> make) {
        return new SeatFactory() {
            @Override public String name() { return name; }
            @Override public PlayerController create(Game g, Player p, Wire w) { return make.apply(g, p).apply(w); }
        };
    }

    /** Ours, on the default decision protocol (the `answer` function), faithful profile. */
    static final SeatFactory OURS = seat("ours", (g, p) -> w ->
            new LlmSpikeGui.PlayerControllerLLM(g, p, new LobbyPlayerAi("Seat", null), "Seat", "", w, w, "llm"));

    /** What a naive bridge gets for free: priority (play or pass) goes to the model; every other decision is Forge's
     *  AI, inherited. The menu is every ability the engine lists for the seat's cards that it can pay for. */
    static final SeatFactory DEFAULT = seat("default", (g, p) -> w -> new DefaultBridgeSeat(g, p, w));

    static final SeatFactory OURS_MINUS_COMBAT = seat("ours-minus-combat", (g, p) -> w -> new OursMinusCombat(g, p, w));
    static final SeatFactory OURS_MINUS_COSTS = seat("ours-minus-costs", (g, p) -> w -> new OursMinusCosts(g, p, w));

    static final class DefaultBridgeSeat extends PlayerControllerAi {
        private final Wire wire;
        DefaultBridgeSeat(Game g, Player p, Wire w) { super(g, p, new LobbyPlayerAi("Seat", null)); this.wire = w; }

        @Override public List<SpellAbility> chooseSpellAbilityToPlay() {
            Player me = getPlayer();
            List<SpellAbility> menu = new ArrayList<>();
            for (SpellAbility sa : ComputerUtilAbility.getSpellAbilities(ComputerUtilAbility.getAvailableCards(getGame(), me), me)) {
                sa.setActivatingPlayer(me);
                if (sa.isManaAbility()) continue;
                if (sa.isLandAbility() ? me.canPlayLand(sa.getHostCard(), false, sa) : sa.canPlay() && ComputerUtilCost.canPayCost(sa, me, false))
                    menu.add(sa);
            }
            StringBuilder b = new StringBuilder("You have priority. Choose one:\n");
            for (int i = 0; i < menu.size(); i++) b.append("  ").append(i + 1).append(") ").append(menu.get(i).getHostCard().getName())
                    .append(": ").append(menu.get(i).getDescription()).append('\n');
            b.append("  ").append(menu.size() + 1).append(") PASS\n");
            JsonArray msgs = new JsonArray();
            JsonObject user = new JsonObject();
            user.addProperty("role", "user");
            user.addProperty("content", b.toString());
            msgs.add(user);
            NativeTools.Turn t = wire.chat(msgs.toString(), "[{\"type\":\"function\",\"function\":{\"name\":\"answer\"}}]", true);
            if (t == null || t.calls.isEmpty()) return null;
            JsonObject a = NativeTools.arguments(t.calls.get(0).arguments);
            if (a == null || !a.has("option") || !a.get("option").isJsonPrimitive()) return null;
            int o = a.get("option").getAsInt();
            if (o < 1 || o > menu.size()) return null;
            SpellAbility sa = menu.get(o - 1);
            // The naive bridge lets Forge's AI fill in targets, as its cast path needs them set (forge-overlay rules).
            if (!sa.isLandAbility() && getAi().canPlaySa(sa) != AiPlayDecision.WillPlay && sa.usesTargeting() && !sa.isTargetNumberValid())
                return null;
            List<SpellAbility> out = new ArrayList<>();
            out.add(sa);
            return out;
        }
    }

    /** Ours with every combat hook handed to Forge's AI: the ablation that must cost the combat family. */
    static final class OursMinusCombat extends LlmSpikeGui.PlayerControllerLLM {
        private PlayerControllerAi ai;
        OursMinusCombat(Game g, Player p, Wire w) { super(g, p, new LobbyPlayerAi("Seat", null), "Seat", "", w, w, "llm"); }
        private PlayerControllerAi ai() { if (ai == null) ai = new PlayerControllerAi(getGame(), getPlayer(), getLobbyPlayer()); return ai; }
        @Override public void declareAttackers(Player attacker, Combat combat) { ai().declareAttackers(attacker, combat); }
        @Override public void declareBlockers(Player defender, Combat combat) { ai().declareBlockers(defender, combat); }
        @Override public java.util.Map<Card, Integer> assignCombatDamage(Card attacker, CardCollectionView blockers, CardCollectionView remaining,
                int damageDealt, GameEntity defender, boolean overrideOrder) {
            return ai().assignCombatDamage(attacker, blockers, remaining, damageDealt, defender, overrideOrder);
        }
        @Override public CardCollection orderBlockers(Card attacker, CardCollection blockers) { return ai().orderBlockers(attacker, blockers); }
        @Override public CardCollection orderAttackers(Card blocker, CardCollection attackers) { return ai().orderAttackers(blocker, attackers); }
        @Override public boolean payCombatCost(Card card, Cost cost, SpellAbility sa, String prompt) { return ai().payCombatCost(card, cost, sa, prompt); }
    }

    /** Ours with every cost hook handed to Forge's AI: the ablation that must cost the costs family. */
    static final class OursMinusCosts extends LlmSpikeGui.PlayerControllerLLM {
        private PlayerControllerAi ai;
        OursMinusCosts(Game g, Player p, Wire w) { super(g, p, new LobbyPlayerAi("Seat", null), "Seat", "", w, w, "llm"); }
        private PlayerControllerAi ai() { if (ai == null) ai = new PlayerControllerAi(getGame(), getPlayer(), getLobbyPlayer()); return ai; }
        @Override public CostDecisionMakerBase getCostDecisionMaker(Player player, SpellAbility ability, boolean effect, String prompt) {
            return ai().getCostDecisionMaker(player, ability, effect, prompt);
        }
        @Override public List<OptionalCostValue> chooseOptionalCosts(SpellAbility chosen, List<OptionalCostValue> values) { return ai().chooseOptionalCosts(chosen, values); }
        @Override public boolean payCostToPreventEffect(Cost cost, SpellAbility sa, boolean alreadyPaid, FCollectionView<Player> allPayers) {
            return ai().payCostToPreventEffect(cost, sa, alreadyPaid, allPayers);
        }
        @Override public boolean confirmPayment(CostPart costPart, String question, SpellAbility sa) { return ai().confirmPayment(costPart, question, sa); }
        @Override public CardCollectionView chooseCardsToDelve(int genericAmount, CardCollection grave) { return ai().chooseCardsToDelve(genericAmount, grave); }
        @Override public boolean payManaCost(ManaCost toPay, CostPartMana costPartMana, SpellAbility sa, String prompt, ManaConversionMatrix matrix, boolean effect) {
            return ai().payManaCost(toPay, costPartMana, sa, prompt, matrix, effect);
        }
        @Override public Mana chooseManaFromPool(List<Mana> manaChoices) { return ai().chooseManaFromPool(manaChoices); }
    }

    // ------------------------------------------------------------------ instances

    /** One surface: a fresh board, the engine action that reaches the hook, the outcome by engine identity. */
    static final class Instance {
        final String id, family, hook;
        final Supplier<Game> board;
        final BiFunction<Game, PlayerController, String> drive;
        final Function<Game, Set<String>> legal;   // engine-legal outcomes on a fresh board, or null
        final int minDistinct;                     // when legal is null: the decision's number of legal answers
        final int minAsks;                         // decisions the seat must put to the model in each replay
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
        int ai = -1;
        String text = "";
        String error;
    }

    static Run run(SeatFactory seat, Instance in, Wire.Mode mode, List<String> profile) {
        Run r = new Run();
        Wire w = new Wire(mode, profile);
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL;
        try {
            Game g = in.board.get();
            Player me = g.getPlayers().get(0);
            PlayerController c = seat.create(g, me, w);
            me.dangerouslySetController(c);
            r.outcome = in.drive.apply(g, c);
            if (c instanceof LlmSpikeGui.PlayerControllerLLM) r.ai = ((LlmSpikeGui.PlayerControllerLLM) c).aiDelegationsCount();
        } catch (Throwable t) {
            String last = w.requests.isEmpty() ? "" : w.requests.get(w.requests.size() - 1);
            last = last.substring(Math.max(0, last.lastIndexOf("DECISION")));
            r.error = t.getClass().getSimpleName() + ": " + String.valueOf(t.getMessage()).replaceAll("\\s+", " ")
                    + (last.isEmpty() ? "" : " | last ask: " + last.substring(0, Math.min(240, last.length())).replaceAll("\\s+", " "));
            r.outcome = "ERROR " + r.error;
        } finally {
            LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null;
        }
        r.decisions = w.decisions;
        r.text = w.text();
        return r;
    }

    static final String[] COLUMNS = {"seat", "instance", "family", "hook", "legal", "reached", "min_requests", "asked", "offered",
            "carried", "shown", "noparse", "pass", "ai", "note"};

    /** One report row: the five checks of one instance on one seat. */
    static String grade(SeatFactory seat, Instance in) {
        Set<String> legal = null;
        String note = "";
        try { if (in.legal != null) legal = in.legal.apply(in.board.get()); }
        catch (Throwable t) { note = "legal set: " + t.getClass().getSimpleName() + " " + t.getMessage(); }
        Set<String> reached = new TreeSet<>();
        int minAsked = Integer.MAX_VALUE, maxAsked = 0;
        int ai = 0;
        Run first = null;
        List<String> errors = new ArrayList<>();
        for (List<String> p : in.profiles) {
            Run r = run(seat, in, Wire.Mode.CALL, p);
            if (first == null) first = r;
            if (r.error != null) { errors.add(r.error + " | answers " + p); continue; }
            reached.add(r.outcome);
            minAsked = Math.min(minAsked, r.decisions);
            maxAsked = Math.max(maxAsked, r.decisions);
            ai = Math.max(ai, r.ai);
        }
        // A legal outcome written "?x" may be reached and need not be (cancelling a cast that is not committed yet).
        Set<String> required = new TreeSet<>(), allowed = new TreeSet<>();
        if (legal != null) for (String s : legal) {
            allowed.add(s.startsWith("?") ? s.substring(1) : s);
            if (!s.startsWith("?")) required.add(s);
        }
        int legalN = legal != null ? required.size() : in.minDistinct;
        Run again = run(seat, in, Wire.Mode.CALL, in.profiles.get(0));
        Run prose = run(seat, in, Wire.Mode.PROSE, in.profiles.get(0));
        Run empty = run(seat, in, Wire.Mode.EMPTY, in.profiles.get(0));
        Run garbage = run(seat, in, Wire.Mode.GARBAGE, in.profiles.get(0));
        // An instance whose legal set could not be computed proves nothing: it fails rather than passing on count.
        boolean ok = errors.isEmpty() && first != null && (in.legal == null || legal != null);
        // Every replay asks at least once; a play that is passed at its first question stops there, so the deeper
        // decision (the cost, the mode, the target) need only be reached by some replay.
        boolean asked = ok && (legalN < 2 || minAsked >= 1 && maxAsked >= Math.max(1, in.minAsks));
        boolean offered = ok && (legal != null ? reached.containsAll(required) && allowed.containsAll(reached) : reached.size() >= in.minDistinct);
        boolean carried = ok && first.outcome.equals(again.outcome) && (legalN < 2 || reached.size() >= 2);
        boolean shown = ok && !first.text.isEmpty() && !first.text.contains("CARDNAME");
        for (String s : in.show) shown &= first != null && first.text.contains(s);
        boolean noparse = prose.error == null && prose.outcome.equals(empty.outcome) && empty.outcome.equals(garbage.outcome);
        boolean pass = asked && offered && carried && shown && noparse;
        if (!errors.isEmpty()) note += (note.isEmpty() ? "" : "; ") + "error: " + errors.get(0);
        if (!offered && ok) {
            Set<String> missing = new TreeSet<>(required);
            missing.removeAll(reached);
            Set<String> extra = new TreeSet<>(reached);
            if (legal != null) extra.removeAll(allowed);
            note += (note.isEmpty() ? "" : "; ") + "missing " + missing + " extra " + extra;
        }
        if (!noparse) note += (note.isEmpty() ? "" : "; ") + "prose=" + prose.outcome + " empty=" + empty.outcome;
        return String.join("\t", seat.name(), in.id, in.family, in.hook, String.valueOf(legalN), String.valueOf(reached.size()),
                String.valueOf(minAsked == Integer.MAX_VALUE ? 0 : minAsked), bit(asked), bit(offered), bit(carried), bit(shown),
                bit(noparse), bit(pass), String.valueOf(ai), note.replace('\t', ' ').replace('\n', ' '));
    }

    private static String bit(boolean b) { return b ? "1" : "0"; }

    // ------------------------------------------------------------------ answer profiles (the scripted model's grid)

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

    /** Every answer in one JSON, repeated for every decision: each decision reads the field it needs. */
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

    /** option = 1..n+1 (the last is out of range), with `options` and `number` saying the same index. */
    static List<String> option(int n) {
        List<String> out = new ArrayList<>();
        for (int i = 1; i <= n + 1; i++) out.add(json("option", i, "options", ints(List.of(i)), "number", i));
        return out;
    }

    /** Every subset of 1..n (ascending) as `options`, plus one out of range; `option` is the subset's first. */
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

    /** Every order of 1..n as `options`. */
    static List<String> orders(int n) {
        List<String> out = new ArrayList<>();
        permute(new ArrayList<>(), n, out);
        return out;
    }

    private static void permute(List<Integer> prefix, int n, List<String> out) {
        if (prefix.size() == n) { out.add(json("options", ints(prefix), "option", prefix.get(0))); return; }
        for (int i = 1; i <= n; i++) if (!prefix.contains(i)) { List<Integer> p = new ArrayList<>(prefix); p.add(i); permute(p, n, out); }
    }

    /** number = lo-1..hi+1, with `option` the value's place in a menu of lo..hi. */
    static List<String> numbers(int lo, int hi) {
        List<String> out = new ArrayList<>();
        for (int v = lo - 1; v <= hi + 1; v++) out.add(json("number", v, "option", v - lo + 1));
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

    /** The visible state that a decision can change: every zone of both players with tapped state, damage, power
     *  and toughness on the battlefield, life, the stack with its targets, and the mana pool. */
    static String digest(Game g) {
        StringBuilder b = new StringBuilder();
        for (Player p : g.getPlayers()) {
            b.append(p.getName()).append(" life=").append(p.getLife()).append(" pool=").append(p.getManaPool().totalMana()).append(';');
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
        for (forge.game.spellability.SpellAbilityStackInstance si : g.getStack()) stack.add(si.getSpellAbility().getHostCard().getName() + "->" + si.getSpellAbility().getTargets());
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
