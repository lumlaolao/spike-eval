package forge.spike;

import java.util.ArrayList;
import java.util.EnumSet;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import com.google.common.eventbus.Subscribe;

import forge.StaticData;
import forge.card.CardStateName;
import forge.card.MagicColor;
import forge.game.Game;
import forge.game.GameEntity;
import forge.game.GameLogEntry;
import forge.game.GameLogEntryType;
import forge.game.GameObject;
import forge.game.card.Card;
import forge.game.card.CardView;
import forge.game.card.CounterEnumType;
import forge.game.card.CounterType;
import forge.game.combat.Combat;
import forge.game.event.GameEventCardChangeZone;
import forge.game.keyword.Keyword;
import forge.game.keyword.KeywordInterface;
import forge.game.phase.PhaseHandler;
import forge.game.player.Player;
import forge.game.player.PlayerView;
import forge.game.spellability.SpellAbility;
import forge.game.spellability.SpellAbilityStackInstance;
import forge.game.spellability.TargetChoices;
import forge.game.zone.Zone;
import forge.game.zone.ZoneType;
import forge.game.zone.ZoneView;
import forge.item.PaperCard;

import forge.spike.LlmSpikeGui.ContextProfile;

/**
 * One seat's window onto the game, built from what that seat may legally see.
 *
 * Everything the faithful observation profile shows a model passes through this class: the current
 * state, candidate labels, the stack, combat, the seat-captured narration stream, private reveals, and
 * the INSPECT tool. It is constructed per controller and never shared across seats.
 *
 * VISIBILITY RULE. A card's identity is shown when (a) its zone is visible to the viewer (public zones;
 * the viewer's own hand) and (b) its face is visible (face-up, or face-down with permission via the
 * engine's own mayLook/controller rules, the same predicate the Forge GUI uses). Candidates the engine
 * explicitly presents (a revealed hand for Thoughtseize, a searched library) skip rule (a) but never
 * rule (b): an opponent's morph offered as a target stays "face-down Creature 2/2".
 *
 * HANDLES. Objects are named {@code @N} with a viewer-local counter assigned on first sight. Handles
 * never derive from engine ids or timestamps, which are allocated in deck order and would let a model
 * correlate objects across hidden shuffles. A zone change makes a new rules object and therefore a
 * new handle; the old handle is quoted as {@code (was @k)} only when the object's whole path since the
 * viewer last saw it ran through zones the viewer can see. A card that passes through an opponent's
 * hand or any library is a fresh, unlinked object when it reappears.
 *
 * NARRATION. The engine log is captured into a per-seat stream at observation time. Hidden names are
 * redacted by OBJECT, not by a name dictionary: the engine formats a card reference as {@code Name (id)},
 * so every id that resolves to an object this viewer may not know loses its name and its id together
 * ("a face-down card"), while an id that resolves to a visible object maps to that object's handle. A
 * public card that merely shares a hidden card's name is never erased, and a hidden name is never
 * substituted out of a message that does not name the hidden object. Private reveals delivered to this
 * seat are appended to the same stream, so every seat-visible fact has one E-id and one place to search.
 */
final class SeatObservation {
    static final String VERSION = "observation-faithful-v2";
    /** Printed once per observation so every prompt is self-describing. Part of the prompt bytes and
     *  therefore of this VERSION: change it and bump the version. */
    static final String LEGEND = "LEGEND: @N = your seat's handle for one object, assigned when you first see it; a zone change "
            + "makes a NEW object with a new handle, and '(was @k)' is shown only when you could see its whole path. "
            + "'face-down <type>' = identity hidden from you. Menu options name the same handles. "
            + "Zones list every card unless an explicit '(+N more not shown)' omission is printed.\n";

    /** Log types narrated. The legacy set plus INFORMATION, which carries public reveal notices
     *  (CostReveal) the legacy filter silently dropped. PHASE and MANA remain noise. */
    static final Set<GameLogEntryType> EVENT_TYPES;
    static {
        EnumSet<GameLogEntryType> s = EnumSet.copyOf(GameHistory.EVENT_TYPES);
        s.add(GameLogEntryType.INFORMATION);
        EVENT_TYPES = s;
    }

    private static final Pattern HIDDEN_ID = Pattern.compile("\\s*\\(H\\d+\\)");
    private static final Pattern ENGINE_ID = Pattern.compile("\\s*\\((\\d+)\\)");

    private static final Keyword[] SHOWN_KEYWORDS = {
        Keyword.FLYING, Keyword.FIRST_STRIKE, Keyword.DOUBLE_STRIKE, Keyword.DEATHTOUCH, Keyword.LIFELINK,
        Keyword.TRAMPLE, Keyword.HASTE, Keyword.VIGILANCE, Keyword.REACH, Keyword.MENACE, Keyword.HEXPROOF,
        Keyword.SHROUD, Keyword.INDESTRUCTIBLE, Keyword.INFECT, Keyword.FLASH, Keyword.DEFENDER, Keyword.PROWESS,
        Keyword.EXALTED, Keyword.FEAR, Keyword.INTIMIDATE, Keyword.SHADOW, Keyword.WITHER, Keyword.PERSIST,
        Keyword.UNDYING, Keyword.FLANKING, Keyword.HORSEMANSHIP, Keyword.SKULK, Keyword.PHASING, Keyword.STORM,
        Keyword.DELVE, Keyword.CASCADE,
    };

    /** One viewer-visible rules object. */
    private static final class Tracked {
        final int engineId;
        final int handle;
        final long timestamp;
        final int predecessor;      // handle this object publicly continues, or -1
        boolean pendingNew;         // a zone change happened since the last sighting
        boolean linkAllowed = true; // no hidden zone on the path since the last sighting
        String lastKnownName;       // identity as last seen, null if never known to the viewer
        ZoneType lastZone;
        Tracked(int engineId, int handle, long timestamp, int predecessor) {
            this.engineId = engineId; this.handle = handle; this.timestamp = timestamp; this.predecessor = predecessor;
        }
    }

    private final Game game;
    private final Player viewer;
    private final Map<Integer, Tracked> byEngineId = new HashMap<>();
    private final Map<Integer, Tracked> byHandle = new HashMap<>();
    private int nextHandle = 1;

    private final List<GameHistory.Ev> stream = new ArrayList<>();
    private final List<String> reveals = new ArrayList<>();
    private int consumedLogEntries = 0;
    private int currentTurn = 0;
    private final Set<String> presentedNoted = new HashSet<>();
    private final List<String> omissions = new ArrayList<>();

    SeatObservation(Game game, Player viewer) {
        this(game, viewer, true);
    }

    SeatObservation(Game game, Player viewer, boolean subscribe) {
        this.game = game;
        this.viewer = viewer;
        if (subscribe) {
            try { game.subscribeToEvents(this); } catch (Exception e) { omissions.add("zone-change events unavailable: " + e); }
        }
    }

    Player viewer() { return viewer; }

    // ---- visibility ---------------------------------------------------------------------------

    /** Zones whose contents the viewer may see at all. */
    boolean zoneVisible(ZoneType zt, Player zoneOwner) {
        if (zt == null) return true;
        switch (zt) {
            case Hand:
            case Sideboard:
            case ExtraHand:
                return zoneOwner == viewer;
            case Library:
            case PlanarDeck:
            case AttractionDeck:
            case ContraptionDeck:
            case SchemeDeck:
                return false;
            default:
                return true;
        }
    }

    private static ZoneType zoneTypeOf(Card c) {
        try { Zone z = c.getZone(); return z == null ? null : z.getZoneType(); } catch (Exception e) { return null; }
    }

    private static Player zoneOwnerOf(Card c) {
        try { Zone z = c.getZone(); return z == null ? null : z.getPlayer(); } catch (Exception e) { return null; }
    }

    /** True when the viewer may know which card this is regardless of zone: face-up, or face-down
     *  with the engine's own look permission (own morphs, foretold cards, "look at" grants). */
    boolean faceKnown(Card c) {
        try {
            if (!c.isFaceDown()) return true;
            CardView cv = c.getView();
            return cv != null && cv.canFaceDownBeShownTo(viewer.getView());
        } catch (Exception e) { return false; }
    }

    /** Zone visible and face known: the full test for state rendering. */
    boolean identityKnown(Card c) {
        return zoneVisible(zoneTypeOf(c), zoneOwnerOf(c)) && faceKnown(c);
    }

    // ---- registry -----------------------------------------------------------------------------

    @Subscribe
    public void onZoneChange(GameEventCardChangeZone ev) {
        try {
            if (ev == null || ev.card() == null) return;
            Tracked t = byEngineId.get(ev.card().getId());
            if (t == null) return; // never sighted: nothing to sever, a future sighting is fresh
            t.pendingNew = true;
            if (!zoneViewVisible(ev.from()) || !zoneViewVisible(ev.to())) t.linkAllowed = false;
        } catch (Exception ignore) { }
    }

    private boolean zoneViewVisible(ZoneView zv) {
        if (zv == null) return true;
        PlayerView owner = zv.player();
        Player ownerPlayer = owner == null ? null : (owner == viewer.getView() ? viewer : null);
        if (owner != null && ownerPlayer == null) {
            for (Player p : game.getPlayers()) if (p.getView() == owner) { ownerPlayer = p; break; }
        }
        return zoneVisible(zv.zoneType(), ownerPlayer);
    }

    /** Handle of a card the viewer has already observed, or -1. Never assigns. */
    int handleOf(Card c) {
        Tracked t = byEngineId.get(c.getId());
        return t == null || t.pendingNew || t.timestamp != c.getGameTimestamp() ? -1 : t.handle;
    }

    private Tracked sight(Card c, boolean known) {
        int id = c.getId();
        long ts = c.getGameTimestamp();
        Tracked t = byEngineId.get(id);
        if (t == null) {
            t = register(id, ts, -1);
        } else if (t.pendingNew || t.timestamp != ts) {
            t = register(id, ts, t.linkAllowed ? t.handle : -1);
        }
        if (known) t.lastKnownName = c.getName();
        t.lastZone = zoneTypeOf(c);
        return t;
    }

    private Tracked register(int id, long ts, int predecessor) {
        Tracked t = new Tracked(id, nextHandle++, ts, predecessor);
        byEngineId.put(id, t);
        byHandle.put(t.handle, t);
        return t;
    }

    // ---- object rendering ---------------------------------------------------------------------

    /** Full label for a menu candidate or a state line. {@code presented} means the engine handed the
     *  viewer this card explicitly (a revealed hand, a searched library): the zone rule is waived, the
     *  face-down rule is not. Presenting a card from a hidden zone is itself a reveal and is recorded. */
    String describe(Card c, boolean presented) {
        boolean zoneOk = zoneVisible(zoneTypeOf(c), zoneOwnerOf(c));
        boolean known = presented ? faceKnown(c) : identityKnown(c);
        if (presented && !zoneOk && known) notePresented(c);
        Tracked t = sight(c, known);
        StringBuilder s = new StringBuilder();
        ZoneType zt = zoneTypeOf(c);
        boolean inPlay = zt == ZoneType.Battlefield;
        boolean onStack = zt == ZoneType.Stack;
        if (known) {
            String n = safeName(c);
            s.append(n.isEmpty() ? "(unnamed " + typeLine(c) + ")" : n);
            if (c.isFaceDown()) s.append(" [face-down, you may look]");
        } else {
            s.append("face-down ").append(typeLine(c));
        }
        if (known && !c.isLand() && !inPlay) {
            String mc = LlmSpikeGui.PlayerControllerLLM.costStr(c);
            if (mc != null) s.append(' ').append(mc);
        }
        if (c.isCreature() && (inPlay || onStack || known)) {
            s.append(' ').append(c.getNetPower()).append('/').append(c.getNetToughness());
        }
        if (inPlay) {
            if (c.isPlaneswalker()) { try { s.append(" [loyalty ").append(c.getCurrentLoyalty()).append(']'); } catch (Exception ignore) { } }
            s.append(c.isTapped() ? " [tapped]" : " [untapped]");
            if (c.isSick()) s.append(" [sick]");
            try { if (c.isPhasedOut()) s.append(" [phased out]"); } catch (Exception ignore) { }
            String cts = counters(c);
            if (!cts.isEmpty()) s.append(" [").append(cts).append(']');
            try { int dmg = c.getDamage(); if (dmg > 0) s.append(" [damage ").append(dmg).append(']'); } catch (Exception ignore) { }
            if (known) {
                String kw = keywords(c);
                if (!kw.isEmpty()) s.append(" [").append(kw).append(']');
            }
            try {
                GameEntity to = c.getEntityAttachedTo();
                if (to instanceof Card) s.append(" [attached to ").append(shortRef((Card) to)).append(']');
                else if (to instanceof Player) s.append(" [attached to ").append(((Player) to).getName()).append(']');
            } catch (Exception ignore) { }
            try {
                if (c.hasCardAttachments()) {
                    StringBuilder a = new StringBuilder();
                    for (Card att : c.getAttachedCards()) { if (a.length() > 0) a.append(", "); a.append(shortRef(att)); }
                    if (a.length() > 0) s.append(" [has attached: ").append(a).append(']');
                }
            } catch (Exception ignore) { }
        } else if (c.isCreature() && !known && onStack) {
            // nothing more: a face-down spell shows only what a face-down spell shows
        }
        s.append(" @").append(t.handle);
        if (t.predecessor > 0) s.append(" (was @").append(t.predecessor).append(')');
        return s.toString();
    }

    /** Compact reference for cross-references (attachments, targets, combat): identity + P/T + handle. */
    String shortRef(Card c) {
        boolean known = identityKnown(c);
        Tracked t = sight(c, known);
        StringBuilder s = new StringBuilder();
        if (known) {
            String n = safeName(c);
            s.append(n.isEmpty() ? "(unnamed " + typeLine(c) + ")" : n);
        } else {
            s.append("face-down ").append(typeLine(c));
        }
        if (c.isCreature()) s.append(' ').append(c.getNetPower()).append('/').append(c.getNetToughness());
        s.append(" @").append(t.handle);
        return s.toString();
    }

    private void notePresented(Card c) {
        String key = c.getId() + "|" + c.getGameTimestamp() + "|" + zoneTypeOf(c);
        if (!presentedNoted.add(key)) return;
        Player owner = zoneOwnerOf(c);
        recordReveal("engine presented " + safeName(c) + " from " + (owner == null ? "?" : owner.getName())
                + "'s " + zoneTypeOf(c) + " for a choice");
    }

    /** The identity a permitted viewer knows: for a face-down card that is its real (original) face,
     *  which the face-down state itself does not carry. Callers gate on faceKnown() first. */
    private static String safeName(Card c) {
        try {
            String n = c.getName();
            if ((n == null || n.isEmpty()) && c.isFaceDown()) {
                try { n = c.getState(CardStateName.Original).getName(); } catch (Exception ignore) { }
            }
            return n == null ? "" : n;
        } catch (Exception e) { return "?"; }
    }

    private static String typeLine(Card c) {
        try {
            String t = String.valueOf(c.getType());
            return t.isEmpty() ? "card" : t;
        } catch (Exception e) { return "card"; }
    }

    private static String counters(Card c) {
        StringBuilder cb = new StringBuilder();
        try {
            com.google.common.collect.Multiset<CounterType> cts = c.getCounters();
            if (cts != null) {
                for (com.google.common.collect.Multiset.Entry<CounterType> e : cts.entrySet()) {
                    if (e.getElement().is(CounterEnumType.LOYALTY) || e.getCount() <= 0) continue;
                    if (cb.length() > 0) cb.append(", ");
                    cb.append(e.getElement().getName()).append(" x").append(e.getCount());
                }
            }
        } catch (Exception ignore) { }
        return cb.toString();
    }

    private static String keywords(Card c) {
        StringBuilder b = new StringBuilder();
        try {
            for (Keyword k : SHOWN_KEYWORDS) {
                if (!c.hasKeyword(k)) continue;
                if (b.length() > 0) b.append(", ");
                b.append(k.toString().toLowerCase(Locale.ROOT));
            }
            for (Keyword k : new Keyword[] { Keyword.PROTECTION, Keyword.WARD }) {
                for (KeywordInterface ki : c.getKeywords(k)) {
                    if (b.length() > 0) b.append(", ");
                    b.append(ki.getOriginal().toLowerCase(Locale.ROOT));
                }
            }
        } catch (Exception ignore) { }
        return b.toString();
    }

    // ---- narration capture --------------------------------------------------------------------

    /** Pull every engine log entry this seat has not yet consumed into the seat stream. Called at each
     *  observation, before the history is rendered and before the archive is extended. */
    void capture() {
        List<GameLogEntry> all;
        try { all = game.getGameLog().getAllEntries(); } catch (Exception e) { return; }
        if (consumedLogEntries > all.size()) consumedLogEntries = 0; // log was reset (new game on same object)
        Map<Integer, Card> byId = null;
        for (int i = consumedLogEntries; i < all.size(); i++) {
            GameLogEntry e = all.get(i);
            if (e.type() == GameLogEntryType.TURN) {
                int parsed = GameHistory.parseTurn(LlmSpikeGui.oneLine(e.message()));
                currentTurn = parsed > 0 ? parsed : currentTurn + 1;
            }
            if (!EVENT_TYPES.contains(e.type())) continue;
            if (byId == null) byId = cardsById();
            String msg = redact(LlmSpikeGui.oneLine(e.message()), e.sourceCard(), byId);
            stream.add(new GameHistory.Ev(stream.size() + 1, currentTurn, e.type(), msg));
        }
        consumedLogEntries = all.size();
    }

    private Map<Integer, Card> cardsById() {
        Map<Integer, Card> m = new HashMap<>();
        try { for (Card c : game.getCardsInGame()) m.put(c.getId(), c); } catch (Exception ignore) { }
        return m;
    }

    /** Remove what this seat may not know from an engine-formatted message: a face-down source card's
     *  true name, the engine's global hidden-card counter, and raw engine ids (mapped to this seat's
     *  handle when the object is visible, dropped otherwise). Package-private for tests. */
    String redact(String msg, CardView source, Map<Integer, Card> byId) {
        if (msg == null) return "";
        String out = msg;
        try {
            if (source != null && source.isFaceDown() && !source.canFaceDownBeShownTo(viewer.getView())) {
                String hidden = null;
                try { if (source.getAlternateState() != null) hidden = source.getAlternateState().getName(); } catch (Exception ignore) { }
                if (hidden == null || hidden.isEmpty()) {
                    Card real = byId == null ? null : byId.get(source.getId());
                    if (real != null) { try { hidden = real.getState(CardStateName.Original).getName(); } catch (Exception ignore) { } }
                }
                if (hidden != null && !hidden.isEmpty()) out = out.replace(hidden, "a face-down card");
            }
        } catch (Exception ignore) { }
        out = HIDDEN_ID.matcher(out).replaceAll("");
        // Object-anchored redaction. The engine formats a card reference as "Name (id)", so an id is
        // the reliable pointer to the object an event names. An id that resolves to a visible object
        // maps to that object's handle; an id that resolves to an object this viewer may not know
        // loses the name AND the id ("a hidden/face-down card"); an unresolved id is dropped. Because this
        // is anchored to the referenced object, a public card that shares a hidden card's name is
        // never erased -- the old game-wide name dictionary did erase it (and leaked: changing a
        // hidden identity changed PUBLIC history text).
        Matcher m = ENGINE_ID.matcher(out);
        StringBuilder sb = new StringBuilder();
        int last = 0;
        while (m.find()) {
            sb.append(out, last, m.start());
            last = m.end();
            int id;
            try { id = Integer.parseInt(m.group(1)); } catch (Exception e) { continue; }
            Card c = byId == null ? null : byId.get(id);
            if (c == null) continue; // unknown reference: drop it
            if (identityKnown(c)) {
                sb.append(" @").append(sight(c, true).handle);
            } else {
                String nm = originalName(c);
                if (nm != null && !nm.isEmpty() && endsWith(sb, nm)) {
                    sb.setLength(sb.length() - nm.length());
                    sb.append(c.isFaceDown() ? "a face-down card" : "a hidden card");
                }
            }
        }
        sb.append(out, last, out.length());
        return sb.toString();
    }

    private static boolean endsWith(StringBuilder sb, String s) {
        int n = s.length();
        if (n == 0 || sb.length() < n) return false;
        for (int i = 0; i < n; i++) if (sb.charAt(sb.length() - n + i) != s.charAt(i)) return false;
        return true;
    }

    /** The real (original-face) name of a card, independent of its current face-down state. */
    private static String originalName(Card c) {
        try {
            String n = c.getState(CardStateName.Original).getName();
            if (n != null && !n.isEmpty()) return n;
        } catch (Exception ignore) { }
        try { return c.getName(); } catch (Exception e) { return null; }
    }

    /** Append a private reveal (or any seat-local evidence) to the stream with turn attribution. */
    int recordReveal(String text) {
        int turn = safeTurn();
        String msg = "[private reveal to you] " + LlmSpikeGui.oneLine(text);
        stream.add(new GameHistory.Ev(stream.size() + 1, turn, GameLogEntryType.INFORMATION, msg));
        reveals.add("[E" + stream.size() + "] T" + turn + ": " + LlmSpikeGui.oneLine(text));
        return stream.size();
    }

    private int safeTurn() {
        try { return game.getPhaseHandler().getTurn(); } catch (Exception e) { return currentTurn; }
    }

    /** The seat stream, oldest first, ids 1-based and stable. */
    List<GameHistory.Ev> events() { return stream; }

    List<String> reveals() { return reveals; }

    // ---- state rendering ----------------------------------------------------------------------

    String render(String seatLabel, ContextProfile ctx, String history, String turnPlan, int planTurn) {
        try {
            StringBuilder s = new StringBuilder();
            PhaseHandler ph = game.getPhaseHandler();
            Player active = ph.getPlayerTurn();
            Player prio = ph.getPriorityPlayer();
            s.append("== Turn ").append(ph.getTurn()).append(' ').append(ph.getPhase())
             .append(" (active: ").append(active == null ? "?" : active.getName())
             .append(" | priority: ").append(prio == null ? "?" : (prio == viewer ? "you" : prio.getName()))
             .append(" | observation ").append(VERSION).append(") ==\n");
            s.append(LEGEND);

            s.append("YOU [").append(seatLabel).append("] ").append(playerLine(viewer)).append('\n');
            s.append("  hand(").append(viewer.getCardsIn(ZoneType.Hand).size()).append("): ").append(handList()).append('\n');
            s.append("  board: ").append(perms(viewer)).append('\n');
            s.append("  lands: ").append(lands(viewer)).append('\n');
            s.append("  mana available: ").append(LlmSpikeGui.PlayerControllerLLM.manaAvailable(viewer)).append('\n');
            s.append("  graveyard(").append(viewer.getCardsIn(ZoneType.Graveyard).size()).append("): ")
             .append(zoneList(viewer.getCardsIn(ZoneType.Graveyard), ctx.yardCap)).append('\n');
            s.append("  exile(").append(viewer.getCardsIn(ZoneType.Exile).size()).append("): ")
             .append(zoneList(viewer.getCardsIn(ZoneType.Exile), ctx.exileCap)).append('\n');
            String myCmd = zoneList(viewer.getCardsIn(ZoneType.Command), ctx.exileCap);
            if (!"(none)".equals(myCmd)) s.append("  command: ").append(myCmd).append('\n');

            for (Player opp : game.getPlayers()) {
                if (opp == viewer) continue;
                boolean isOpp = viewer.isOpponentOf(opp);
                s.append(isOpp ? "OPP [" : "OTHER [").append(opp.getName()).append("] ").append(playerLine(opp)).append('\n');
                s.append("  hand: ").append(opp.getCardsIn(ZoneType.Hand).size()).append(" cards (hidden)\n");
                s.append("  board: ").append(perms(opp)).append('\n');
                s.append("  lands: ").append(lands(opp)).append('\n');
                s.append("  mana available: ").append(LlmSpikeGui.PlayerControllerLLM.manaAvailable(opp)).append('\n');
                s.append("  graveyard(").append(opp.getCardsIn(ZoneType.Graveyard).size()).append("): ")
                 .append(zoneList(opp.getCardsIn(ZoneType.Graveyard), ctx.yardCap)).append('\n');
                s.append("  exile(").append(opp.getCardsIn(ZoneType.Exile).size()).append("): ")
                 .append(zoneList(opp.getCardsIn(ZoneType.Exile), ctx.exileCap)).append('\n');
                String cmd = zoneList(opp.getCardsIn(ZoneType.Command), ctx.exileCap);
                if (!"(none)".equals(cmd)) s.append("  command: ").append(cmd).append('\n');
                String casts = LlmSpikeGui.PlayerControllerLLM.castHistory(game, opp, GameLogEntryType.STACK_ADD, " cast ", ctx.castHistoryCap);
                if (!casts.isEmpty()) s.append("  CAST THIS GAME: ").append(casts).append('\n');
                String landsPlayed = LlmSpikeGui.PlayerControllerLLM.castHistory(game, opp, GameLogEntryType.LAND, " played ", ctx.castHistoryCap);
                if (!landsPlayed.isEmpty()) s.append("  LANDS PLAYED: ").append(landsPlayed).append('\n');
            }

            s.append("STACK (top first): ").append(stack()).append('\n');
            s.append("COMBAT: ").append(combat()).append('\n');
            s.append("NOT VISIBLE: ").append(notVisible()).append('\n');
            s.append(history);
            if (!reveals.isEmpty()) {
                int shown = Math.min(6, reveals.size());
                s.append("REVEALS TO YOU (historical evidence, seat-local; ").append(reveals.size()).append(" total, latest ")
                 .append(shown).append(" shown; all remain searchable):\n");
                for (int i = reveals.size() - shown; i < reveals.size(); i++) s.append("  - ").append(reveals.get(i)).append('\n');
            }
            if (planTurn == ph.getTurn() && ph.isPlayerTurn(viewer) && turnPlan != null && !turnPlan.isEmpty())
                s.append("YOUR TURN PLAN (stated earlier this turn — execute it or consciously deviate): ").append(turnPlan);
            return s.toString();
        } catch (Exception e) {
            return "== (state unavailable: " + e + ") ==";
        }
    }

    private String playerLine(Player p) {
        StringBuilder b = new StringBuilder("life ").append(p.getLife());
        int poison = 0;
        try { poison = p.getCounters(CounterEnumType.POISON); } catch (Exception ignore) { }
        b.append(" | poison ").append(poison).append("/10");
        try {
            com.google.common.collect.Multiset<CounterType> cts = p.getCounters();
            if (cts != null) {
                for (com.google.common.collect.Multiset.Entry<CounterType> e : cts.entrySet()) {
                    if (e.getElement().is(CounterEnumType.POISON) || e.getCount() <= 0) continue;
                    b.append(" | ").append(e.getElement().getName().toLowerCase(Locale.ROOT)).append(' ').append(e.getCount());
                }
            }
        } catch (Exception ignore) { }
        b.append(" | hand ").append(p.getCardsIn(ZoneType.Hand).size());
        b.append(" | library ").append(p.getCardsIn(ZoneType.Library).size()).append(" (hidden, order unknown)");
        b.append(" | graveyard ").append(p.getCardsIn(ZoneType.Graveyard).size());
        b.append(" | exile ").append(p.getCardsIn(ZoneType.Exile).size());
        try {
            b.append(" | lands played this turn ").append(p.getLandsPlayedThisTurn()).append('/')
             .append(p.getMaxLandPlaysInfinite() ? "unlimited" : String.valueOf(p.getMaxLandPlays()));
        } catch (Exception ignore) { }
        b.append(" | mana pool ").append(manaPool(p));
        try { b.append(" | spells cast this turn ").append(p.getSpellsCastThisTurn()); } catch (Exception ignore) { }
        try { if (p.isMonarch()) b.append(" | monarch"); } catch (Exception ignore) { }
        try { if (p.hasLost()) b.append(" | HAS LOST"); } catch (Exception ignore) { }
        return b.toString();
    }

    private static String manaPool(Player p) {
        try {
            forge.game.mana.ManaPool pool = p.getManaPool();
            if (pool == null || pool.isEmpty()) return "empty";
            String[] label = {"W", "U", "B", "R", "G", "C"};
            byte[] colors = MagicColor.WUBRGC;
            StringBuilder b = new StringBuilder("{");
            for (int i = 0; i < colors.length; i++) {
                int n = pool.getAmountOfColor(colors[i]);
                if (n <= 0) continue;
                if (b.length() > 1) b.append(' ');
                b.append(label[i]).append(':').append(n);
            }
            return b.append('}').toString();
        } catch (Exception e) { return "?"; }
    }

    private String handList() {
        int[] tally = LlmSpikeGui.PlayerControllerLLM.untappedManaTally(viewer);
        StringBuilder b = new StringBuilder();
        for (Card c : viewer.getCardsIn(ZoneType.Hand)) {
            if (b.length() > 0) b.append(", ");
            b.append(describe(c, false));
            if (!c.isLand()) {
                try {
                    String afford = LlmSpikeGui.PlayerControllerLLM.affordability(viewer, c, tally);
                    if (afford != null) b.append(' ').append(afford);
                } catch (Exception ignore) { }
            }
        }
        return b.length() == 0 ? "(empty)" : b.toString();
    }

    private String perms(Player p) {
        StringBuilder b = new StringBuilder();
        for (Card c : p.getCardsIn(ZoneType.Battlefield)) {
            if (c.isLand()) continue;
            if (b.length() > 0) b.append(", ");
            b.append(describe(c, false));
        }
        return b.length() == 0 ? "(no nonland permanents)" : b.toString();
    }

    /** Per-copy land listing grouped by name and tap state, each copy carrying its handle. */
    private String lands(Player p) {
        int untapped = 0, total = 0;
        LinkedHashMap<String, int[]> byName = new LinkedHashMap<>();
        LinkedHashMap<String, StringBuilder[]> handles = new LinkedHashMap<>();
        for (Card c : p.getCardsIn(ZoneType.Battlefield)) {
            if (!c.isLand()) continue;
            total++;
            boolean tapped = c.isTapped();
            if (!tapped) untapped++;
            String key = identityKnown(c) ? safeName(c) : "face-down land";
            byName.computeIfAbsent(key, k -> new int[2])[tapped ? 1 : 0]++;
            StringBuilder[] hs = handles.computeIfAbsent(key, k -> new StringBuilder[] { new StringBuilder(), new StringBuilder() });
            StringBuilder tgt = hs[tapped ? 1 : 0];
            if (tgt.length() > 0) tgt.append(' ');
            Tracked t = sight(c, identityKnown(c));
            tgt.append('@').append(t.handle);
            if (t.predecessor > 0) tgt.append("(was @").append(t.predecessor).append(')');
        }
        StringBuilder kinds = new StringBuilder();
        for (Map.Entry<String, int[]> e : byName.entrySet()) {
            int u = e.getValue()[0], t = e.getValue()[1];
            StringBuilder[] hs = handles.get(e.getKey());
            if (u > 0) { if (kinds.length() > 0) kinds.append(", "); kinds.append(e.getKey()).append(" x").append(u).append(" [untapped ").append(hs[0]).append(']'); }
            if (t > 0) { if (kinds.length() > 0) kinds.append(", "); kinds.append(e.getKey()).append(" x").append(t).append(" [tapped ").append(hs[1]).append(']'); }
        }
        return untapped + " untapped / " + total + " total" + (kinds.length() == 0 ? "" : " (" + kinds + ")");
    }

    private String zoneList(Iterable<Card> cards, int cap) {
        StringBuilder b = new StringBuilder();
        int n = 0, shown = 0;
        for (Card c : cards) {
            n++;
            if (shown >= cap) continue;
            if (b.length() > 0) b.append(", ");
            b.append(describe(c, false));
            shown++;
        }
        if (n > shown) b.append(" (+").append(n - shown).append(" more not shown; profile cap ").append(cap).append(')');
        return b.length() == 0 ? "(none)" : b.toString();
    }

    String stack() {
        try {
            int n = game.getStack().size();
            if (n == 0) return "(empty)";
            StringBuilder b = new StringBuilder(n + " pending: ");
            int idx = n;
            boolean first = true;
            for (SpellAbilityStackInstance si : game.getStack()) {
                if (!first) b.append(" | ");
                first = false;
                b.append('#').append(idx--).append(' ');
                b.append(si.isSpell() ? "spell " : si.isTrigger() ? "trigger of " : "ability of ");
                Card src = si.getSourceCard();
                boolean known = src != null && faceKnown(src);
                b.append(src == null ? "?" : shortRef(src));
                Player who = si.getActivatingPlayer();
                b.append(" (").append(who == null ? "?" : (who == viewer ? "you" : who.getName())).append(')');
                if (known) {
                    String d = LlmSpikeGui.oneLine(si.getStackDescription());
                    if (d != null && !d.isEmpty()) b.append(": ").append(d.length() > 160 ? d.substring(0, 157) + "..." : d);
                }
                String t = targets(si);
                if (!t.isEmpty()) b.append(" -> targets: ").append(t);
            }
            return b.toString();
        } catch (Exception e) { return "?"; }
    }

    private String targets(SpellAbilityStackInstance root) {
        StringBuilder t = new StringBuilder();
        for (SpellAbilityStackInstance si = root; si != null; si = si.getSubInstance()) {
            TargetChoices tc;
            try { tc = si.getTargetChoices(); } catch (Exception e) { continue; }
            if (tc == null) continue;
            for (GameObject o : tc) {
                if (t.length() > 0) t.append(", ");
                if (o instanceof Card) t.append(shortRef((Card) o));
                else if (o instanceof Player) t.append(((Player) o).getName());
                else if (o instanceof SpellAbility) {
                    Card h = ((SpellAbility) o).getHostCard();
                    t.append("spell/ability of ").append(h == null ? "?" : shortRef(h));
                } else t.append(String.valueOf(o));
            }
        }
        return t.toString();
    }

    String combat() {
        try {
            Combat cb = game.getCombat();
            if (cb == null) return "none";
            StringBuilder b = new StringBuilder();
            Player atk = cb.getAttackingPlayer();
            b.append(atk == null ? "?" : (atk == viewer ? "you" : atk.getName())).append(" attacking");
            List<Card> attackers = cb.getAttackers();
            if (attackers.isEmpty()) return b.append("; no attackers declared").toString();
            for (Card a : attackers) {
                b.append("; ").append(shortRef(a)).append(" -> ");
                GameEntity d = cb.getDefenderByAttacker(a);
                if (d instanceof Player) b.append(((Player) d) == viewer ? "you" : d.getName());
                else if (d instanceof Card) b.append(shortRef((Card) d));
                else b.append('?');
                List<Card> blockers = cb.getBlockers(a);
                if (blockers != null && !blockers.isEmpty()) {
                    b.append(" blocked by ");
                    boolean f = true;
                    for (Card bl : blockers) { if (!f) b.append(", "); f = false; b.append(shortRef(bl)); }
                } else {
                    b.append(cb.isBlocked(a) ? " (blocked)" : " (unblocked)");
                }
            }
            return b.toString();
        } catch (Exception e) { return "?"; }
    }

    private String notVisible() {
        StringBuilder b = new StringBuilder();
        for (Player p : game.getPlayers()) {
            if (p == viewer) continue;
            b.append(p.getName()).append("'s hand (").append(p.getCardsIn(ZoneType.Hand).size()).append(" cards) and library (")
             .append(p.getCardsIn(ZoneType.Library).size()).append("); ");
        }
        b.append("your library (").append(viewer.getCardsIn(ZoneType.Library).size()).append(", order unknown); ");
        b.append("face-down identities you may not look at. Continuous effects are applied to the shown P/T, types, ")
         .append("counters and keywords but are not enumerated separately. Only the decision below is pending for you.");
        for (String o : omissions) b.append(' ').append(o).append('.');
        return b.toString();
    }

    // ---- INSPECT tool -------------------------------------------------------------------------

    /** {@code @N}: current public characteristics of an observed object, or its historical record when
     *  it is no longer current. Any other text: public card-database reference for that exact name. */
    String inspect(String arg) {
        if (arg == null) return "ERROR: INSPECT needs @<handle> or an exact card name.";
        String a = arg.trim();
        if (a.isEmpty()) return "ERROR: INSPECT needs @<handle> or an exact card name.";
        if (a.startsWith("@")) {
            int h;
            try { h = Integer.parseInt(a.substring(1).trim().split("\\s+")[0]); } catch (Exception e) { return "ERROR: malformed handle."; }
            Tracked t = byHandle.get(h);
            if (t == null) return "ERROR: unknown handle @" + h + " (handles exist only for objects you have observed).";
            Card c = null;
            try { for (Card x : game.getCardsInGame()) if (x.getId() == t.engineId) { c = x; break; } } catch (Exception ignore) { }
            boolean current = c != null && byEngineId.get(t.engineId) == t && !t.pendingNew && c.getGameTimestamp() == t.timestamp;
            if (!current || !zoneVisible(zoneTypeOf(c), zoneOwnerOf(c))) {
                return "@" + h + " is HISTORICAL (no longer the current object, or now in a zone you cannot see). Last seen: "
                        + (t.lastKnownName == null ? "identity unknown" : t.lastKnownName)
                        + (t.lastZone == null ? "" : " in " + t.lastZone) + ". Use current handles for current objects.";
            }
            StringBuilder b = new StringBuilder("@").append(h).append(" (current): ").append(describe(c, false));
            b.append("\nzone: ").append(zoneTypeOf(c)).append(" | controller: ").append(c.getController() == viewer ? "you" : c.getController().getName())
             .append(" | owner: ").append(c.getOwner() == viewer ? "you" : c.getOwner().getName());
            if (identityKnown(c)) {
                b.append("\ntypes: ").append(typeLine(c));
                try { String mc = LlmSpikeGui.PlayerControllerLLM.costStr(c); if (mc != null) b.append(" | cost ").append(mc); } catch (Exception ignore) { }
                try { String o = c.getOracleText(); if (o != null && !o.isEmpty()) b.append("\noracle: ").append(LlmSpikeGui.oneLine(o)); } catch (Exception ignore) { }
            } else {
                b.append("\ntypes: ").append(typeLine(c)).append(" | identity hidden from you (face-down)");
            }
            return b.toString();
        }
        try {
            PaperCard pc = StaticData.instance().getCommonCards().getCard(a);
            if (pc == null) return "ERROR: no card named '" + a + "' in the card database.";
            forge.card.CardRules r = pc.getRules();
            StringBuilder b = new StringBuilder("CARD REFERENCE (public database text; not evidence that this card is in the game): ")
                    .append(r.getName());
            try { if (r.getManaCost() != null) b.append(" ").append(r.getManaCost().getShortString()); } catch (Exception ignore) { }
            b.append("\ntypes: ").append(r.getType());
            try { if (r.getType().isCreature()) b.append(" | ").append(r.getPower()).append('/').append(r.getToughness()); } catch (Exception ignore) { }
            b.append("\noracle: ").append(LlmSpikeGui.oneLine(r.getOracleText()));
            return b.toString();
        } catch (Exception e) {
            return "ERROR: card lookup failed.";
        }
    }

    static String inspectInstructions() {
        return "INSPECT TOOL: you may also reply with exactly ONE line `INSPECT @<handle>` for the current public "
                + "characteristics and rules text of an object you can see, or `INSPECT <exact card name>` for public "
                + "card-database text. INSPECT shares the MEMORY tool budget and never reveals hidden identities.\n";
    }
}
