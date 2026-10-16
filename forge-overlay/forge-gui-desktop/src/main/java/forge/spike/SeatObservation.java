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
import forge.game.event.GameEventLandPlayed;
import forge.game.event.GameEventSpellAbilityCast;
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
    /** v24: the legend moved out of every message into the system prompt ({@link #GUIDE}), where it is said
     *  once and cached; the header no longer names this version (it is for the logs, not the model).
     *  v25: the golden checks (stack text redacted of engine ids; the hand's "(castable)" tag agrees with
     *  the menu), merged with v24.
     *  v26: nothing the observation shows is cut (docs/harness/MODEL-LIMITS.md): whole event, stack and card
     *  text, every seen opponent card and every reveal, and uncapped known-in-hidden-zones and zone trails.
     *  v32 (2026-10-16): mana available counts a colour the card decides (Chrome Mox's imprinted colours, a
     *  chosen colour) as the engine reads it; ability text names the card, not CARDNAME (runs/c3 T7, T11). */
    static final String VERSION = "observation-faithful-v33";
    /** How to read the state each message carries, said once in the system prompt (it is fixed text, so it
     *  caches). Part of the prompt bytes and therefore of this VERSION: change it and bump the version.
     *  Every section named here is one {@link #render} prints. */
    static final String GUIDE =
              "Each message starts with the state of the game, then the decision.\n"
            + "- The header gives the turn number, the phase or step, whose turn it is (active) and who has priority. If the "
            + "active player is not you, you are acting on their turn, at instant speed.\n"
            + "- YOU and OPP each start with a line of counts: life, poison (10 loses), any other counters, hand, library, "
            + "graveyard, exile, lands played this turn out of the lands allowed, mana in pool, and spells cast this turn.\n"
            + "- Your hand lists each card with its mana cost and, for a spell, whether you can pay for it now: \"(castable)\", "
            + "what is missing, such as \"(need 1 more B)\", or \"(not castable now)\". The opponent's hand is only a count.\n"
            + "- board lists nonland permanents; lands lists lands, with [tapped] or [untapped] for each. [sick] marks a "
            + "creature that came under its controller's control this turn and cannot attack or use tap abilities yet.\n"
            + "- mana available is the mana your untapped sources can make right now, as the engine counts it (Ancient Tomb "
            + "makes 2; a summoning-sick creature makes none). Per colour it is the mana that can be made in that colour; a "
            + "dual land counts under both of its colours, so the colours add up to more than can be spent. \"up to N mana\" "
            + "is the total, and a source that makes more than one mana is named with its amount. Mana that can be spent only on "
            + "some spells is listed apart, in the card's own words, and not counted in the total.\n"
            + "- @N is your handle for one object, given when you first see it, and used the same way in the options. When an "
            + "object changes zone it becomes a new object with a new handle; \"(was @k)\" links the two when you saw the whole "
            + "move. \"face-down <type>\" is an object whose identity is hidden from you. Zones list every card unless a "
            + "\"(+N more not shown)\" note says otherwise.\n"
            + "- CAST THIS GAME and LANDS PLAYED record what the opponent has cast and played, even cards that have since left "
            + "play. OPPONENT CARDS YOU HAVE SEEN gives the printed rules text of their cards you have seen.\n"
            + "- STACK lists what is waiting to resolve, top first (the top resolves first); COMBAT describes attackers and "
            + "blockers; NOT VISIBLE names what is hidden from you.\n"
            + "- SINCE YOUR LAST DECISION is the engine's log of what happened since you last chose, oldest first; [E<n>] ids "
            + "can be searched in your archive. REVEALS TO YOU are hidden cards you were shown.\n"
            + "- \"library top (known to you)\" under your hand lists the cards you put or saw on top of your library, top "
            + "first; it is dropped as they are drawn and when the library is shuffled.\n"
            + "- KNOWN IN HIDDEN ZONES is what you saw go into an opponent's hidden zone (it is dropped once you see it leave). "
            + "SPENT THIS TURN states what you have already used (land drop, spells, attackers), so nothing has to be inferred "
            + "from a missing option.\n"
            + "- FACTS is the engine's arithmetic (the next combat's damage, the mana they can make at instant speed): "
            + "estimates, not guarantees.\n"
            + "- YOUR PLAN, YOUR OPPONENT PROFILE and YOUR CARD INTERACTIONS are notes you wrote yourself earlier, with the turn "
            + "you wrote them. They are not verified; replace them when they are out of date.\n"
            + "Where each part comes from, so nothing is mistaken for someone's message: the state (YOU, OPP, STACK, COMBAT, "
            + "FACTS, the hand tags, mana available) is read from the Forge engine. GAME HISTORY, SINCE YOUR LAST DECISION and "
            + "REVEALS TO YOU are lines of Forge's game log, in the engine's own words, about what each player did, you included: "
            + "a line naming your player is the engine recording your own action. Lines marked [private reveal to you] or "
            + "[notice to you] are what the engine showed only you. OPPONENT CARDS YOU HAVE SEEN is printed card text from the "
            + "card database. KNOWN IN HIDDEN ZONES, CAST THIS GAME, LANDS PLAYED and SPENT THIS TURN are the harness's record "
            + "of what you saw and did. Only YOUR PLAN, YOUR OPPONENT PROFILE and YOUR CARD INTERACTIONS are your own words. No "
            + "other model's words appear anywhere: your opponent is known only through the engine.\n";

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
        /** Where this object has been, for as long as we could watch it: "T3: battlefield -> graveyard".
         *  Inherited from the predecessor when a zone change is publicly linked, so an object keeps ONE
         *  history even though Forge makes a new Card (and we a new handle) on every move. */
        final List<String> trail = new ArrayList<>();
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
    /** The seat's OWN one-line read of the opponent (deck/plan, cards to respect). Authored by the
     *  model, refreshed rarely, re-injected every decision, and always labelled unverified — it is a
     *  hypothesis the model can revise, never an engine fact. Empty until the model writes one. */
    private String profile = "";

    /** The colour tally plus what it means (observation v23): a source is counted under every colour
     *  it can make, so `{W:3 U:1 B:1 R:1 G:1 C:1} (3 untapped sources)` from two Plains and a Cavern
     *  of Souls read to a judge as nine mana from four sources (runs/frontier/opus, T8). Since v30 the amounts
     *  are mana, not sources, and the total is stated ("up to 3 mana"), so Tron's seven mana reads as seven. */
    static String manaLine(Player p) {
        return LlmSpikeGui.PlayerControllerLLM.manaAvailable(p).replace(" untapped sources",
                " untapped sources; each counts its mana under every colour it can make, so the colours add up to more than can"
                + " be spent");
    }

    void setProfile(String p) { this.profile = p == null ? "" : p; }
    /** When the seat's plan was stated ("T5 MAIN1"), for the plan line. */
    private String planWhen = "";
    void setPlanWhen(String w) { this.planWhen = w == null ? "" : w; }

    /** The model's OWN statement of how its cards combine, asked once from its own decklist and shown
     *  back at every decision. It is the model's note, labelled as such, never an engine fact. */
    private String deckLines = "";

    /** Rules text of every OPPONENT card this seat has identified, keyed by name in first-seen order.
     *  The seat is given its own deck's card text; an opponent's card used to be a name, a P/T and a
     *  keyword list ("Orcish Bowmasters 1/1 [flash]"), with the text available only through INSPECT —
     *  which a seat that does not know what it is missing never calls. A card is entered the first
     *  time the viewer can identify it (a public zone, the stack, or a card it KNOWS is in a hidden
     *  zone) and stays: the text of a card you have seen does not stop being known when it leaves.
     *  It is the printed card, read from the card database — never an interaction, never advice. */
    private final java.util.LinkedHashMap<String, String> oppCardText = new java.util.LinkedHashMap<>();
    /** The seat's own deck text (already in its framing); a card printed there is not repeated. */
    private String ownDeckText = "";

    void setOwnDeckText(String t) { this.ownDeckText = t == null ? "" : t; }

    private static final ZoneType[] PUBLIC_ZONES = { ZoneType.Battlefield, ZoneType.Stack, ZoneType.Graveyard, ZoneType.Exile, ZoneType.Command };

    /** Record the text of every opponent card the viewer can identify right now. */
    private void noteOpponentCards() {
        try {
            for (Player opp : game.getPlayers()) {
                if (opp == viewer) continue;
                for (ZoneType z : PUBLIC_ZONES) {
                    for (Card c : opp.getCardsIn(z)) {
                        if (c.isToken() || !identityKnown(c)) continue;
                        String name = safeName(c);
                        if (name == null || name.isEmpty() || oppCardText.containsKey(name)) continue;
                        String line = cardTextLine(name);
                        if (line != null) oppCardText.put(name, line);
                    }
                }
            }
            for (KnownLoc k : knownHidden.values()) {
                if (k.owner == viewer || oppCardText.containsKey(k.name)) continue;
                String line = cardTextLine(k.name);
                if (line != null) oppCardText.put(k.name, line);
            }
        } catch (Exception ignore) { }
    }

    /** "{cost} | type P/T — oracle text" from the card database, or null when the card has no rules
     *  text worth printing (a basic land) or is already printed in the seat's own deck text. */
    private String cardTextLine(String name) {
        try {
            if (ownDeckText.contains("  " + name + " ") || ownDeckText.contains("  " + name + "\n")) return null;
            PaperCard pc = StaticData.instance().getCommonCards().getCard(name);
            if (pc == null) return null;
            forge.card.CardRules r = pc.getRules();
            String text = r.getOracleText();
            if (text == null || text.trim().isEmpty()) return null;
            StringBuilder b = new StringBuilder();
            try { if (r.getManaCost() != null && !r.getType().isLand()) b.append(r.getManaCost().getShortString()).append(" | "); } catch (Exception ignore) { }
            b.append(r.getType());
            try { if (r.getType().isCreature()) b.append(' ').append(r.getPower()).append('/').append(r.getToughness()); } catch (Exception ignore) { }
            if (r.getType().isBasicLand()) return null;
            text = text.replace("\\n", " ").replaceAll("\\s+", " ").trim();
            return b.append(" — ").append(text).toString();
        } catch (Exception e) { return null; }
    }

    private String opponentCardText() {
        noteOpponentCards();
        if (oppCardText.isEmpty()) return "";
        int n = oppCardText.size();
        StringBuilder b = new StringBuilder("OPPONENT CARDS YOU HAVE SEEN (printed rules text from the card database; ")
                .append(n).append(n == 1 ? " card" : " cards").append("):\n");
        for (java.util.Map.Entry<String, String> e : oppCardText.entrySet())
            b.append("  ").append(e.getKey()).append(": ").append(e.getValue()).append('\n');
        return b.toString();
    }

    void setDeckLines(String t) { this.deckLines = t == null ? "" : t; }

    /** A card the seat KNOWS is sitting in an opponent's hidden zone. Recorded ONLY from what the
     *  viewer could see: (a) a card visible on the battlefield/graveyard/exile that then MOVED into a
     *  hidden zone ("return target creature to its owner's hand", "put it on top of their library"),
     *  and (b) a card the engine presented from a hidden zone (a revealed hand / searched library).
     *  Invalidated the instant the card is seen leaving that zone (a cast, a discard). This is what
     *  lets the seat reason "I unsummoned their Atraxa, so a Thoughtseize now takes it" instead of
     *  re-deriving the chain from raw history — the concrete failure the review called out. It is a
     *  KNOWLEDGE claim, never engine truth: stale knowledge is still knowledge, and the wording says so. */
    private static final class KnownLoc {
        final String name;
        final Player owner;
        final ZoneType zone;
        int count;
        int turn;
        KnownLoc(String name, Player owner, ZoneType zone) { this.name = name; this.owner = owner; this.zone = zone; }
    }
    /** Keyed "ownerName|zone|name" (an object gets a new engine id across a zone change, so identity
     *  across zones is by name — the same thing the seat itself can track). Not capped: it is bounded
     *  by the opponents' cards, and the old 12-entry cap dropped the oldest fact without saying so. */
    private final Map<String, KnownLoc> knownHidden = new LinkedHashMap<>();

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
            updateKnownHidden(t, ev.from(), ev.to());
            // Where it went, for as long as we could watch it. This is the object-level half of
            // continuity: the model can ask what happened to a thing it saw earlier.
            String from = ev.from() == null ? "?" : zoneWord(ev.from().zoneType());
            String to = ev.to() == null ? "?" : zoneWord(ev.to().zoneType());
            t.trail.add("T" + safeTurn() + ": " + from + " -> " + to + (t.linkAllowed ? "" : " (via a hidden zone)"));
        } catch (Exception ignore) { }
    }

    /** Maintain {@link #knownHidden} from PUBLIC moves only. A move we can see OUT of a hidden zone
     *  invalidates the fact; a move we can see INTO a hidden zone (from a zone we could see, with an
     *  identity we knew) records one. Both directions are needed: casting a known card must stop us
     *  claiming we know it is in hand. */
    private void updateKnownHidden(Tracked t, ZoneView from, ZoneView to) {
        if (t.lastKnownName == null || t.lastKnownName.isEmpty()) return; // never knew this card
        boolean fromVisible = zoneViewVisible(from), toVisible = zoneViewVisible(to);
        if (from != null && !fromVisible) {
            Player owner = playerFromView(from.player());
            if (owner != viewer) removeKnown(t.lastKnownName, owner, from.zoneType());
        }
        if (to != null && !toVisible && fromVisible) {
            Player owner = playerFromView(to.player());
            if (owner != null && owner != viewer) addKnown(t.lastKnownName, owner, to.zoneType());
        }
    }

    private static String knownKey(String name, Player owner, ZoneType zone) {
        return owner.getName() + "|" + zone + "|" + name;
    }

    private void addKnown(String name, Player owner, ZoneType zone) {
        if (name == null || name.isEmpty() || owner == null || zone == null) return;
        KnownLoc k = knownHidden.get(knownKey(name, owner, zone));
        if (k == null) { k = new KnownLoc(name, owner, zone); knownHidden.put(knownKey(name, owner, zone), k); }
        k.count++;
        k.turn = safeTurn();
    }

    private void removeKnown(String name, Player owner, ZoneType zone) {
        if (name == null || owner == null || zone == null) return;
        String key = knownKey(name, owner, zone);
        KnownLoc k = knownHidden.get(key);
        if (k == null) return;
        if (--k.count <= 0) knownHidden.remove(key);
    }

    private static String zoneWord(ZoneType zt) {
        if (zt == null) return "?";
        return zt == ZoneType.Hand ? "hand" : zt == ZoneType.Library ? "library" : zt.name().toLowerCase(Locale.ROOT);
    }

    /** Names of the cards on top of the seat's own library that the seat placed or saw there (scry, surveil,
     *  Ponder, Brainstorm), top first, as a player keeps track of them (MTGA shows the same). Checked against
     *  the library on every render: cards drawn or milled from the top drop off, anything that no longer
     *  matches is forgotten, and a shuffle forgets it all ({@link #forgetOwnTop}), so it never claims more
     *  than the seat knows. */
    private final List<String> ownTop = new ArrayList<>();

    /** The seat now knows these cards are on top of its library, top first, above the {@code keepBelow} known
     *  cards it already had there (0 when the new cards replace the old top, as a Ponder does). */
    void learnOwnTop(List<String> topFirst, boolean keepOld, int replaced) {
        List<String> below = new ArrayList<>();
        if (keepOld) below.addAll(ownTop.subList(Math.min(replaced, ownTop.size()), ownTop.size()));
        ownTop.clear();
        ownTop.addAll(topFirst);
        ownTop.addAll(below);
    }

    void forgetOwnTop() { ownTop.clear(); }

    /** The line under the seat's hand, or "" when it knows nothing about its library's order. */
    String ownTopText() {
        if (ownTop.isEmpty()) return "";
        List<Card> lib = new ArrayList<>(viewer.getCardsIn(ZoneType.Library));
        while (!ownTop.isEmpty() && (lib.isEmpty() || !ownTop.get(0).equals(lib.get(0).getName()))) ownTop.remove(0);
        int k = 0;
        while (k < ownTop.size() && k < lib.size() && ownTop.get(k).equals(lib.get(k).getName())) k++;
        ownTop.subList(k, ownTop.size()).clear();
        if (ownTop.isEmpty()) return "";
        StringBuilder b = new StringBuilder("  library top (known to you, top first): ");
        for (int i = 0; i < ownTop.size(); i++) b.append(i == 0 ? "" : ", ").append(i + 1).append(") ").append(ownTop.get(i));
        return b.append('\n').toString();
    }

    /** The KNOWN IN HIDDEN ZONES block. Empty when nothing is known, so a seat that has learned
     *  nothing sees no extra line (and the section cannot be mistaken for engine truth). */
    private String knownHiddenText() {
        if (knownHidden.isEmpty()) return "";
        LinkedHashMap<String, List<KnownLoc>> groups = new LinkedHashMap<>();
        for (KnownLoc k : knownHidden.values()) {
            groups.computeIfAbsent(k.owner.getName() + "'s " + zoneWord(k.zone), x -> new ArrayList<>()).add(k);
        }
        StringBuilder b = new StringBuilder("KNOWN IN HIDDEN ZONES (harness record: you SAW these move there, or they were "
                + "revealed to you — this is your knowledge, not engine truth; you may be able to act on it, "
                + "and it is dropped once you see the card leave):\n");
        for (Map.Entry<String, List<KnownLoc>> e : groups.entrySet()) {
            b.append("  ").append(e.getKey()).append(": ");
            boolean first = true;
            for (KnownLoc k : e.getValue()) {
                if (!first) b.append(", ");
                first = false;
                b.append(k.name);
                if (k.count > 1) b.append(" x").append(k.count);
                b.append(" (T").append(k.turn).append(')');
            }
            b.append('\n');
        }
        return b.toString();
    }

    /** What this seat has already spent or used THIS TURN, stated as explicit facts. A model cannot
     *  infer legality from absence (AbsenceBench): it must be TOLD the land drop is gone, rather than
     *  left to notice the missing menu entry — the "played a second land" class of error. Rendered
     *  last, beside the decision, because that is where the attention is. */
    private String spentThisTurn() {
        StringBuilder b = new StringBuilder("SPENT THIS TURN: ");
        boolean any = false;
        try {
            int played = viewer.getLandsPlayedThisTurn();
            boolean infinite = viewer.getMaxLandPlaysInfinite();
            int max = infinite ? Integer.MAX_VALUE : viewer.getMaxLandPlays();
            b.append("land drop ").append(infinite || played < max
                    ? "AVAILABLE (" + played + "/" + (infinite ? "inf" : max) + ")"
                    : "USED (" + played + "/" + max + ")");
            any = true;
        } catch (Exception ignore) { }
        try {
            if (any) b.append(" | ");
            b.append("spells cast ").append(viewer.getSpellsCastThisTurn());
            any = true;
        } catch (Exception ignore) { }
        try {
            if (any) b.append(" | ");
            java.util.List<Card> atk = viewer.getCreaturesAttackedThisTurn();
            b.append("attackers declared ").append(atk == null || atk.isEmpty() ? "none" : atk.size());
            any = true;
        } catch (Exception ignore) { }
        return any ? b.append('\n').toString() : "";
    }

    /** Deterministic ENGINE ARITHMETIC the model would otherwise get wrong, stated as FACTS and
     *  labelled as the engine's estimates. It uses only honest primitives — each side's own creatures
     *  and untapped mana, all public — and deliberately never consults {@code GameStateEvaluator},
     *  which is perfect-information and non-adversarial (the opponent never casts a new spell in its
     *  search), nor mutates the live game. canAttackNextTurn is used so the race is meaningful at
     *  every priority window, not only inside the declare-attackers step. */
    private String facts() {
        Player opp = null;
        for (Player o : viewer.getOpponents()) { opp = o; break; }
        if (opp == null) return "";
        StringBuilder b = new StringBuilder();
        clock(viewer, opp, "your next combat", b);
        clock(opp, viewer, "their next combat", b);
        try {
            int mana = forge.ai.ComputerUtilMana.getAvailableManaEstimate(opp);
            if (b.length() > 0) b.append(" | ");
            b.append("they can make up to ").append(mana).append(" mana at instant speed right now");
        } catch (Exception ignore) { }
        return b.length() == 0 ? "" : "FACTS (engine estimates, not truth): " + b + '\n';
    }

    /** Unblocked damage a side could deal in its NEXT combat, and the life left after it. */
    private void clock(Player from, Player to, String label, StringBuilder b) {
        try {
            List<Card> legal = new ArrayList<>();
            for (Card c : from.getCreaturesInPlay())
                if (forge.game.combat.CombatUtil.canAttackNextTurn(c, to)) legal.add(c);
            if (legal.isEmpty()) return;
            int dmg = forge.ai.ComputerUtilCombat.sumDamageIfUnblocked(legal, to);
            if (b.length() > 0) b.append(" | ");
            b.append(label).append(": ").append(legal.size()).append(" attacker(s) dealing ").append(dmg)
             .append(" unblocked; ").append(to == viewer ? "YOUR" : "their").append(" life ")
             .append(to.getLife()).append(" -> ").append(Math.max(0, to.getLife() - dmg));
            if (dmg > 0 && dmg >= to.getLife()) b.append(" = LETHAL");
        } catch (Exception ignore) { }
    }

    private Player playerFromView(PlayerView v) {
        if (v == null) return null;
        if (v == viewer.getView()) return viewer;
        for (Player p : game.getPlayers()) if (p.getView() == v) return p;
        return null;
    }

    private boolean zoneViewVisible(ZoneView zv) {
        if (zv == null) return true;
        return zoneVisible(zv.zoneType(), playerFromView(zv.player()));
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
        if (t.trail.isEmpty()) t.trail.add("T" + safeTurn() + ": first seen in " + zoneWord(t.lastZone));
        return t;
    }

    private Tracked register(int id, long ts, int predecessor) {
        Tracked t = new Tracked(id, nextHandle++, ts, predecessor);
        // A publicly linked move continues the SAME object, so it continues the same history.
        if (predecessor >= 0) {
            Tracked prev = byHandle.get(predecessor);
            if (prev != null) t.trail.addAll(prev.trail);
        }
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
        ZoneType zone = zoneTypeOf(c);
        recordReveal("engine presented " + safeName(c) + " from " + (owner == null ? "?" : owner.getName())
                + "'s " + zone + " for a choice");
        // A card presented out of an opponent's HIDDEN zone is a revealed hand/library: the seat now
        // knows that card is there (this is the Thoughtseize/duress half of the knowledge).
        if (owner != null && owner != viewer && !zoneVisible(zone, owner)) addKnown(safeName(c), owner, zone);
    }

    /** The identity a permitted viewer knows: for a face-down card that is its real (original) face,
     *  which the face-down state itself does not carry. Callers gate on faceKnown() first. */
    private static String safeName(Card c) {
        try {
            String n = c.getName();
            if ((n == null || n.isEmpty()) && c.isFaceDown()) {
                try { n = c.getState(CardStateName.Original).getName(); } catch (Exception ignore) { }
            }
            return n == null ? "" : withoutEngineId(c, n);
        } catch (Exception e) { return "?"; }
    }

    /** {@code n}, the name of {@code c}, without an engine id. Forge names an effect `hostCard + "'s Effect"`, and a
     *  card prints as "Name (id)" ("The Wondrous Wasp (53)'s Effect", runs/luna-std/g6). The name is rebuilt from
     *  the source card itself; the id filter is only the fallback for a name whose source is gone (no card name
     *  holds "(digits)": test_deck_legality). Reveals nothing: it only removes. */
    static String withoutEngineId(Card c, String n) {
        try {
            Card src = c.getEffectSource();
            if (src != null && n.startsWith(src.toString())) return src.getName() + n.substring(src.toString().length());
        } catch (Exception ignore) { }
        return ENGINE_ID.matcher(n).replaceAll("");
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
            // flat, not oneLine: the 90-character cut lost block assignments and search results.
            String msg = redact(LlmSpikeGui.flat(e.message()), e.sourceCard(), byId);
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

    /** {@link #redact} for text the seat is asked with (a stack description in a question). */
    String redactIds(String msg) { return redact(msg, null, cardsById()); }

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
        String msg = "[private reveal to you] " + LlmSpikeGui.flat(text);
        stream.add(new GameHistory.Ev(stream.size() + 1, turn, GameLogEntryType.INFORMATION, msg));
        reveals.add("[E" + stream.size() + "] T" + turn + ": " + LlmSpikeGui.flat(text));
        return stream.size();
    }

    /** Append an engine notice the seat's player is shown (the card an opponent named, a colour or
     *  number chosen): {@code PlayerController.notifyOfValue}. A human player sees these; the seat did
     *  not, so a named card or chosen colour reached the model only if the game log happened to say it. */
    int recordNotice(String text) {
        int turn = safeTurn();
        // Redacted: the engine's own text carries ids ("Lotus Petal (111) picked {U}", runs/luna-v9/g4).
        stream.add(new GameHistory.Ev(stream.size() + 1, turn, GameLogEntryType.INFORMATION, "[notice to you] " + redactIds(LlmSpikeGui.flat(text))));
        return stream.size();
    }

    private int safeTurn() {
        try { return game.getPhaseHandler().getTurn(); } catch (Exception e) { return currentTurn; }
    }

    /** The seat stream, oldest first, ids 1-based and stable. */
    List<GameHistory.Ev> events() { return stream; }

    List<String> reveals() { return reveals; }

    /** One spell cast or land played, from the engine's own event. */
    private record Play(Player who, boolean land, String name) {}
    private final List<Play> plays = new ArrayList<>();

    /** A spell cast, from the engine's event rather than from the wording of the log line it also writes
     *  ("Rival cast X targeting Y" was split on " cast " and " targeting"). A copy is not cast (CR 707.10);
     *  a face-down spell is named only as one, unless its face may be shown to the seat. */
    @Subscribe
    public void onCast(GameEventSpellAbilityCast ev) {
        try {
            if (ev == null || ev.sa() == null || ev.si() == null || !ev.sa().isSpell()) return;
            Player who = playerFromView(ev.si().getActivatingPlayer());
            CardView host = ev.sa().getHostCard();
            if (who == null || host == null) return;
            Card c = game.findById(host.getId());
            if (c != null && c.isCopiedSpell()) return;
            boolean hidden = c != null ? !faceKnown(c) : host.isFaceDown();
            plays.add(new Play(who, false, hidden ? "a face-down spell" : host.getName()));
        } catch (Exception ignore) { }
    }

    /** A land played, from the engine's event. */
    @Subscribe
    public void onLandPlayed(GameEventLandPlayed ev) {
        try {
            if (ev == null || ev.land() == null) return;
            Player who = playerFromView(ev.player());
            if (who == null) return;
            Card c = game.findById(ev.land().getId());
            boolean hidden = c != null ? !faceKnown(c) : ev.land().isFaceDown();
            plays.add(new Play(who, true, hidden ? "a face-down land" : ev.land().getName()));
        } catch (Exception ignore) { }
    }

    /** "<Name> -> count" for one player's casts or land drops this game. */
    private LinkedHashMap<String, Integer> castCounts(Player p, boolean land) {
        LinkedHashMap<String, Integer> counts = new LinkedHashMap<>();
        for (Play pl : plays) if (pl.who() == p && pl.land() == land) counts.merge(pl.name(), 1, Integer::sum);
        return counts;
    }

    /** Names this opponent has played TWO OR MORE times — their RECURRING plays, which a human notices
     *  ("they keep casting Entomb") and which the model otherwise has to re-derive from a long log.
     *  This is the deterministic half of opponent modelling: facts, not an inference. */
    private String streamCastRepeats(Player p, boolean land, int cap) {
        List<Map.Entry<String, Integer>> es = new ArrayList<>();
        for (Map.Entry<String, Integer> e : castCounts(p, land).entrySet())
            if (e.getValue() >= 2) es.add(e);
        if (es.isEmpty()) return "";
        es.sort((a, b) -> b.getValue() - a.getValue());
        StringBuilder b = new StringBuilder();
        int shown = 0;
        for (Map.Entry<String, Integer> en : es) {
            if (shown++ == cap) { b.append(", ...(").append(es.size() - cap).append(" more)"); break; }
            if (b.length() > 0) b.append(", ");
            b.append(en.getKey()).append(" x").append(en.getValue());
        }
        return b.toString();
    }

    private String streamCastHistory(Player p, boolean land, int cap) {
        LinkedHashMap<String, Integer> counts = castCounts(p, land);
        if (counts.isEmpty()) return "";
        List<Map.Entry<String, Integer>> es = new ArrayList<>(counts.entrySet());
        es.sort((a, b) -> b.getValue() - a.getValue());
        StringBuilder b = new StringBuilder();
        int shown = 0;
        for (Map.Entry<String, Integer> en : es) {
            if (shown++ == cap) { b.append(", ...(").append(es.size() - cap).append(" more)"); break; }
            if (b.length() > 0) b.append(", ");
            b.append(en.getKey());
            if (en.getValue() > 1) b.append(" x").append(en.getValue());
        }
        return b.toString();
    }

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
             .append(") ==\n");

            s.append("YOU [").append(seatLabel).append("] ").append(playerLine(viewer)).append('\n');
            s.append("  hand(").append(viewer.getCardsIn(ZoneType.Hand).size()).append("): ").append(handList()).append('\n');
            s.append(ownTopText());
            s.append("  board: ").append(perms(viewer)).append('\n');
            s.append("  lands: ").append(lands(viewer)).append('\n');
            s.append("  mana available: ").append(manaLine(viewer)).append('\n');
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
                s.append("  mana available: ").append(manaLine(opp)).append('\n');
                s.append("  graveyard(").append(opp.getCardsIn(ZoneType.Graveyard).size()).append("): ")
                 .append(zoneList(opp.getCardsIn(ZoneType.Graveyard), ctx.yardCap)).append('\n');
                s.append("  exile(").append(opp.getCardsIn(ZoneType.Exile).size()).append("): ")
                 .append(zoneList(opp.getCardsIn(ZoneType.Exile), ctx.exileCap)).append('\n');
                String cmd = zoneList(opp.getCardsIn(ZoneType.Command), ctx.exileCap);
                if (!"(none)".equals(cmd)) s.append("  command: ").append(cmd).append('\n');
                String casts = streamCastHistory(opp, false, ctx.castHistoryCap);
                if (!casts.isEmpty()) s.append("  CAST THIS GAME: ").append(casts).append('\n');
                String repeats = streamCastRepeats(opp, false, ctx.castHistoryCap);
                if (!repeats.isEmpty()) s.append("  RECURRING (cast 2+ times): ").append(repeats).append('\n');
                String landsPlayed = streamCastHistory(opp, true, ctx.castHistoryCap);
                if (!landsPlayed.isEmpty()) s.append("  LANDS PLAYED: ").append(landsPlayed).append('\n');
            }
            s.append(opponentCardText());

            s.append("STACK (engine, top first): ").append(stack()).append('\n');
            s.append("COMBAT: ").append(combat()).append('\n');
            s.append("NOT VISIBLE: ").append(notVisible()).append('\n');
            s.append(history);
            if (!reveals.isEmpty()) {
                s.append("REVEALS TO YOU (Forge game log and the engine's notices to you, seat-local; ").append(reveals.size())
                        .append(" total, oldest first):\n");
                for (String r : reveals) s.append("  - ").append(staleLabel(r)).append('\n');
            }
            s.append(knownHiddenText());
            s.append(spentThisTurn());
            s.append(facts());
            s.append(LlmSpikeGui.PlayerControllerLLM.revisablePlanText(turnPlan, planWhen, planTurn, ph.getTurn()));
            if (!profile.isEmpty())
                s.append("YOUR OPPONENT PROFILE (you wrote this; unverified — revise it as you learn): ").append(profile).append('\n');
            if (!deckLines.isEmpty())
                s.append("YOUR CARD INTERACTIONS (you wrote this from your own decklist; unverified): ").append(deckLines).append('\n');
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
                    String afford = LlmSpikeGui.PlayerControllerLLM.castTag(viewer, c, tally);
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
            // Not "pending": with its own Duress here, Luna read "1 pending" as a choice it was owed (C3 ANT, T3).
            StringBuilder b = new StringBuilder(n + " not yet resolved (" + (n == 1 ? "it resolves" : "the top one resolves")
                    + " when both players pass in a row): ");
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
                    // Redacted like every engine message: "Aether Vial (4) - ..." carried the engine id 4, and
                    // the seat read it as four charge counters (runs/luna-v9/g5, T7).
                    String d = redactIds(engineNotesOut(LlmSpikeGui.flat(si.getStackDescription())));
                    if (d != null && !d.isEmpty()) b.append(": ").append(d);
                    String copies = copiesMade(si);
                    if (!copies.isEmpty()) b.append(" -> ").append(copies);
                }
                String t = targets(si);
                if (!t.isEmpty()) b.append(" -> targets: ").append(t);
            }
            return b.toString();
        } catch (Exception e) { return "?"; }
    }

    /** Forge's notes on a trigger, for its own debugging: "[Card: Tendrils of Agony (7), Activator: Seat,
     *  SpellAbility: ...]", "[Zone Changer: Chrome Mox (18)]". The card and its text are already in the line. */
    static String engineNotesOut(String d) {
        return d == null ? null : d.replaceAll("\\s*\\[[A-Z][A-Za-z ]*: [^\\[\\]]*\\]", "").trim();
    }

    /** "makes N copies" for a copy effect on the stack (storm, Twincast), N as the engine counts it now. */
    private static String copiesMade(SpellAbilityStackInstance si) {
        try {
            forge.game.spellability.SpellAbility sa = si.getSpellAbility();
            for (forge.game.spellability.SpellAbility part = sa; part != null; part = part.getSubAbility()) {
                if (part.getApi() != forge.game.ability.ApiType.CopySpellAbility) continue;
                int n = forge.game.ability.AbilityUtils.calculateAmount(part.getHostCard(), part.getParamOrDefault("Amount", "1"), part);
                return "makes " + n + (n == 1 ? " copy" : " copies");
            }
        } catch (Exception ignore) { }
        return "";
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
    /** The current, visible object a handle names, or null (unknown, historical or hidden). */
    Card currentCard(int h) {
        Tracked t = byHandle.get(h);
        if (t == null) return null;
        Card c = null;
        try { for (Card x : game.getCardsInGame()) if (x.getId() == t.engineId) { c = x; break; } } catch (Exception ignore) { }
        boolean current = c != null && byEngineId.get(t.engineId) == t && !t.pendingNew && c.getGameTimestamp() == t.timestamp;
        return current && zoneVisible(zoneTypeOf(c), zoneOwnerOf(c)) ? c : null;
    }

    String inspect(String arg) {
        if (arg == null) return "ERROR: INSPECT needs @<handle> or an exact card name.";
        String a = arg.trim();
        if (a.isEmpty()) return "ERROR: INSPECT needs @<handle> or an exact card name.";
        // The model copies an option as it reads, "Cavern of Souls @11": the handle names the object
        // (runs/compare two-tier seed 82 asked that and was told no such card exists).
        java.util.regex.Matcher hm = java.util.regex.Pattern.compile("@(\\d+)").matcher(a);
        if (!a.startsWith("@") && hm.find()) a = "@" + hm.group(1);
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
                        + (t.lastZone == null ? "" : " in " + t.lastZone) + ". Use current handles for current objects."
                        + trailNote(t);
            }
            StringBuilder b = new StringBuilder("@").append(h).append(" (current): ").append(describe(c, false));
            b.append("\nzone: ").append(zoneTypeOf(c)).append(" | controller: ").append(c.getController() == viewer ? "you" : c.getController().getName())
             .append(" | owner: ").append(c.getOwner() == viewer ? "you" : c.getOwner().getName());
            if (identityKnown(c)) {
                b.append("\ntypes: ").append(typeLine(c));
                try { String mc = LlmSpikeGui.PlayerControllerLLM.costStr(c); if (mc != null) b.append(" | cost ").append(mc); } catch (Exception ignore) { }
                try { String o = c.getOracleText(); if (o != null && !o.isEmpty()) b.append("\noracle: ").append(LlmSpikeGui.flat(o)); } catch (Exception ignore) { }
            } else {
                b.append("\ntypes: ").append(typeLine(c)).append(" | identity hidden from you (face-down)");
            }
            b.append(trailNote(t));
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
            b.append("\noracle: ").append(LlmSpikeGui.flat(r.getOracleText()));
            return b.toString();
        } catch (Exception e) {
            return "ERROR: card lookup failed.";
        }
    }

    /** Stamp how old a reveal is: "T3: (8 turns ago) saw their hand ...". The turn stamp was already
     *  there; the AGE is what makes an old read look old. It is a fact and nothing more — whether an
     *  old read is still true depends on what the opponent did since, which the seat can see and the
     *  harness cannot judge (a hand read is still right eight turns later against an opponent who
     *  has cast nothing). A reveal from the current turn is left unstamped. */
    private static final java.util.regex.Pattern REVEAL_TURN = java.util.regex.Pattern.compile("^(?:\\[E\\d+\\] )?T(\\d+):");

    private String staleLabel(String reveal) {
        try {
            // A recorded reveal reads "[E<n>] T<turn>: <text>" (see recordReveal).
            java.util.regex.Matcher m = REVEAL_TURN.matcher(reveal);
            if (!m.find()) return reveal;
            int age = safeTurn() - Integer.parseInt(m.group(1));
            if (age <= 0) return reveal;
            return reveal.substring(0, m.end()) + " (" + age + " turn" + (age == 1 ? "" : "s") + " ago)" + reveal.substring(m.end());
        } catch (Exception e) { return reveal; }
    }

    /** This object's zone history, for the INSPECT answer. Empty when we never saw it move. */
    private static String trailNote(Tracked t) {
        if (t == null || t.trail.isEmpty()) return "";
        return "\nhistory: " + String.join(" | ", t.trail);
    }

    static String inspectInstructions() {
        return "INSPECT TOOL: you may also reply with exactly ONE line `INSPECT @<handle>` for the current public "
                + "characteristics and rules text of an object you can see, or `INSPECT <exact card name>` for public "
                + "card-database text. Inspecting an object you have seen also shows its zone history (`history: ...`). "
                + "INSPECT shares the MEMORY tool budget and never reveals hidden identities.\n";
    }
}
