package forge.spike;

import java.io.File;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.util.LinkedHashMap;
import java.util.Map;

import com.google.common.eventbus.Subscribe;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import forge.game.Game;
import forge.game.card.Card;
import forge.game.card.CounterType;
import forge.game.combat.Combat;
import forge.game.event.GameEvent;
import forge.game.event.GameEventGameOutcome;
import forge.game.player.Player;
import forge.game.spellability.SpellAbilityStackInstance;
import forge.game.zone.ZoneType;

/** The demo's audience feed: engine truth for the screen, never for the seat (docs/harness/DEMO.md).
 *
 *  With {@code DEMO_FEED=1} and EVAL_OUT set, every game event that changes what a spectator would see writes one
 *  JSON line to {@code feed-<seed>-<ms>.jsonl} beside the game's {@code game-<seed>-<ms>.jsonl}: both players' hands
 *  (the opponent's real cards, which the seat never sees), battlefield, graveyards, exile, library counts, life,
 *  the stack and combat, and each llm seat's handle map ({@code @N -> engine id}, read from its SeatObservation)
 *  so the viewer can put the seat's highlights on the true cards. {@code jsonl_offset} is the game JSONL's length
 *  when the line was written: the replay places each snapshot between the decision rows it fell between.
 *
 *  Display-only by construction: it is reached only when DEMO_FEED is set, reads the game on the event thread,
 *  renders no prompt, calls no seat, and catches everything, so a failure here costs the screen a frame and never
 *  the game. Feed on or off, the seat's prompts are byte-identical (DemoFeedTest). */
final class DemoFeed {
    static final String VERSION = "demo-feed-v1";

    private static volatile DemoFeed active;  // read unlocked by beforeDecisionRow (the EvalLog writer)

    private final Game game;
    private final File gameFile;
    private final PrintWriter out;
    private final Map<String, SeatObservation> observers = new LinkedHashMap<>();
    private final Map<String, Integer> handlesSent = new LinkedHashMap<>();
    private String last = "";
    private int seq = 0;
    private boolean over = false;

    private DemoFeed(Game game, File gameFile, PrintWriter out) {
        this.game = game;
        this.gameFile = gameFile;
        this.out = out;
    }

    static boolean enabled() { return "1".equals(System.getenv("DEMO_FEED")); }

    /** Called from each llm seat's controller. The first seat of a game opens the feed; a preview copy of the game
     *  (PlayerLookahead) made while that game runs is ignored. */
    static synchronized void attach(Game game, String seat, SeatObservation observer) {
        if (!enabled()) return;
        attachTo(game, seat, observer, LlmSpikeGui.EvalLog.currentFile());
    }

    static synchronized DemoFeed attachTo(Game game, String seat, SeatObservation observer, File gameFile) {
        try {
            if (active != null && active.game != game && !active.over) return null;
            if (active == null || active.game != game) {
                if (gameFile == null) {
                    System.out.println("[DEMO-FEED] off: DEMO_FEED=1 needs EVAL_OUT (the feed is written beside the game JSONL)");
                    return null;
                }
                File f = new File(gameFile.getParentFile(), gameFile.getName().replaceFirst("^game-", "feed-"));
                active = new DemoFeed(game, gameFile, new PrintWriter(new FileWriter(f, true), true));
                game.subscribeToEvents(active);
                System.out.println("[DEMO-FEED] " + VERSION + " " + f.getName() + " (audience view; the seat never reads it)");
            }
            if (observer != null) active.observers.put(seat, observer);
            return active;
        } catch (Exception e) {
            System.out.println("[DEMO-FEED] off: " + e);
            return null;
        }
    }

    /** Detach the current feed so the next game opens its own (tests run many games in one JVM). */
    static synchronized void closeForTest() {
        if (active != null) { active.over = true; active.out.close(); active = null; }
    }

    /** Called by EvalLog just before it writes a decision row: the seat hands out handles while it renders a prompt,
     *  with no game event between that render and the row, so the map is written here, at the row's offset. */
    static void beforeDecisionRow() {
        DemoFeed f = active;
        if (f == null) return;
        try {
            f.handlesRow();
        } catch (Throwable t) {
            // The screen loses this frame; the game goes on.
        }
    }

    private synchronized void handlesRow() {
        if (over) return;
        JsonObject hm = grownHandles();
        if (hm.size() == 0) return;
        JsonObject row = new JsonObject();
        row.addProperty("t", "feed_handles");
        row.add("handles", hm);
        row.addProperty("seq", seq++);
        row.addProperty("jsonl_offset", gameFile.length());
        out.println(row.toString());
    }

    /** Each seat's handle -> engine id map that grew since it was last written. */
    private JsonObject grownHandles() {
        JsonObject hm = new JsonObject();
        for (Map.Entry<String, SeatObservation> e : observers.entrySet()) {
            Map<Integer, Integer> m = e.getValue().handleMap();
            Integer sent = handlesSent.get(e.getKey());
            if (sent != null && sent == m.size()) continue;
            handlesSent.put(e.getKey(), m.size());
            JsonObject one = new JsonObject();
            for (Map.Entry<Integer, Integer> h : m.entrySet()) one.addProperty(String.valueOf(h.getKey()), h.getValue());
            hm.add(e.getKey(), one);
        }
        return hm;
    }

    @Subscribe
    public synchronized void on(GameEvent e) {
        try {
            if (over) return;
            snapshot(e instanceof GameEventGameOutcome);
            if (e instanceof GameEventGameOutcome) { over = true; out.close(); }
        } catch (Throwable t) {
            // The screen loses this frame; the game goes on.
        }
    }

    void snapshot(boolean force) {
        JsonObject row = new JsonObject();
        row.addProperty("t", "feed");
        try {
            row.addProperty("turn", game.getPhaseHandler().getTurn());
            row.addProperty("phase", String.valueOf(game.getPhaseHandler().getPhase()));
            Player turn = game.getPhaseHandler().getPlayerTurn();
            row.addProperty("active", turn == null ? null : turn.getName());
        } catch (Exception ignore) { }
        JsonArray players = new JsonArray();
        for (Player p : game.getPlayers()) {
            JsonObject o = new JsonObject();
            o.addProperty("name", p.getName());
            o.addProperty("life", p.getLife());
            o.addProperty("poison", p.getPoisonCounters());
            o.addProperty("library", p.getCardsIn(ZoneType.Library).size());
            o.add("hand", cards(p.getCardsIn(ZoneType.Hand)));
            o.add("battlefield", cards(p.getCardsIn(ZoneType.Battlefield)));
            o.add("graveyard", cards(p.getCardsIn(ZoneType.Graveyard)));
            o.add("exile", cards(p.getCardsIn(ZoneType.Exile)));
            players.add(o);
        }
        row.add("players", players);
        JsonArray stack = new JsonArray();
        for (SpellAbilityStackInstance si : game.getStack()) {
            JsonObject s = new JsonObject();
            Card src = si.getSourceCard();
            s.addProperty("id", src == null ? -1 : src.getId());
            s.addProperty("name", src == null ? "?" : src.getName());
            s.addProperty("kind", si.isSpell() ? "spell" : si.isTrigger() ? "trigger" : "ability");
            s.addProperty("by", si.getActivatingPlayer() == null ? null : si.getActivatingPlayer().getName());
            stack.add(s);
        }
        row.add("stack", stack);
        Combat combat = game.getCombat();
        if (combat != null) {
            JsonArray atk = new JsonArray();
            for (Card a : combat.getAttackers()) {
                JsonObject x = new JsonObject();
                x.addProperty("id", a.getId());
                JsonArray blockers = new JsonArray();
                for (Card b : combat.getBlockers(a)) blockers.add(b.getId());
                x.add("blockers", blockers);
                atk.add(x);
            }
            row.add("combat", atk);
        }
        String body = row.toString();
        if (!force && body.equals(last)) return;
        last = body;
        JsonObject hm = grownHandles();
        if (hm.size() > 0) row.add("handles", hm);
        row.addProperty("seq", seq++);
        row.addProperty("jsonl_offset", gameFile.length());
        out.println(row.toString());
    }

    private static JsonArray cards(Iterable<Card> cs) {
        JsonArray a = new JsonArray();
        for (Card c : cs) {
            JsonObject o = new JsonObject();
            o.addProperty("id", c.getId());
            o.addProperty("name", c.getName());
            if (c.isTapped()) o.addProperty("tapped", true);
            if (c.isFaceDown()) o.addProperty("face_down", true);
            if (c.isToken()) o.addProperty("token", true);
            if (c.isLand()) o.addProperty("land", true);
            if (c.isCreature()) {
                // What Forge's own card panel draws: the current P/T against the printed one, damage, sickness.
                o.addProperty("pt", c.getNetPower() + "/" + c.getNetToughness());
                o.addProperty("base_pt", c.getBasePower() + "/" + c.getBaseToughness());
                if (c.getDamage() > 0) o.addProperty("damage", c.getDamage());
                if (c.isSick()) o.addProperty("sick", true);
            }
            if (c.getAttachedTo() != null) o.addProperty("attached_to", c.getAttachedTo().getId());
            StringBuilder ct = new StringBuilder();
            for (CounterType t : c.getCounters().elementSet()) {
                if (ct.length() > 0) ct.append(", ");
                ct.append(t.getName()).append(" x").append(c.getCounters(t));
            }
            if (ct.length() > 0) o.addProperty("counters", ct.toString());
            a.add(o);
        }
        return a;
    }
}
