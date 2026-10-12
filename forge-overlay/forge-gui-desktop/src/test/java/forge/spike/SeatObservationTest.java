package forge.spike;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import com.google.common.collect.Lists;

import org.testng.Assert;
import org.testng.annotations.AfterClass;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;

import forge.GuiDesktop;
import forge.StaticData;
import forge.ai.LobbyPlayerAi;
import forge.deck.Deck;
import forge.game.Game;
import forge.game.GameLogEntryType;
import forge.game.GameRules;
import forge.game.GameStage;
import forge.game.GameType;
import forge.game.Match;
import forge.game.card.Card;
import forge.game.card.CardCollection;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.player.RegisteredPlayer;
import forge.game.zone.ZoneType;
import forge.gui.GuiBase;
import forge.item.IPaperCard;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;

import forge.spike.LlmSpikeGui.ContextProfile;
import forge.spike.LlmSpikeGui.PlayerControllerLLM;

/**
 * Engine-backed tests for the viewer-aware observation layer (harness gates G1/G2/G3, first stage).
 *
 * Every test builds a REAL Game/Player/Card through the same construction Forge's own AI unit tests
 * use, then drives the LIVE {@link SeatObservation} (and, for reveals and menu labels, the live
 * {@link PlayerControllerLLM} under the v4-faithful profile). Nothing here re-implements the renderer.
 *
 * The central claim is the paired-world test: two games whose only difference is what the viewer
 * cannot see (the opponent's hand and library) must produce byte-identical observations, histories and
 * tool results. A leak of ANY hidden identity into ANY of those strings fails it.
 */
public class SeatObservationTest {
    private static final Pattern HANDLE = Pattern.compile("@(\\d+)");

    @BeforeClass
    public void initializeModel() {
        if (GuiBase.getInterface() == null) {
            GuiBase.setInterface(new GuiDesktop());
            FModel.initialize(null, preferences -> {
                preferences.setPref(FPref.LOAD_CARD_SCRIPTS_LAZILY, false);
                preferences.setPref(FPref.UI_LANGUAGE, "en-US");
                return null;
            });
        }
    }

    @AfterClass
    public void clearProfileOverride() { PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null; }

    // ---- fixtures -----------------------------------------------------------------------------

    static Game bareGame() {
        List<RegisteredPlayer> players = Lists.newArrayList();
        players.add(new RegisteredPlayer(new Deck()).setPlayer(new LobbyPlayerAi("Viewer", null)));
        players.add(new RegisteredPlayer(new Deck()).setPlayer(new LobbyPlayerAi("Rival", null)));
        GameRules rules = new GameRules(GameType.Constructed);
        Match match = new Match(rules, players, "SeatObservationTest");
        Game game = new Game(players, rules, match);
        game.setAge(GameStage.Play);
        game.getPhaseHandler().devModeSet(PhaseType.MAIN1, game.getPlayers().get(0));
        return game;
    }

    static Card card(String name, Player p, ZoneType zone) {
        IPaperCard pc = FModel.getMagicDb().getCommonCards().getCard(name);
        if (pc == null) {
            StaticData.instance().attemptToLoadCard(name);
            pc = FModel.getMagicDb().getCommonCards().getCard(name);
        }
        Assert.assertNotNull(pc, "card database has no " + name);
        Card c = Card.fromPaperCard(pc, p);
        c.setGameTimestamp(p.getGame().getNextTimestamp());
        p.getZone(zone).add(c);
        return c;
    }

    /** Identical PUBLIC state in every world; the opponent's hidden zones differ per world. */
    static Game world(String[] rivalHand, String[] rivalLibrary) {
        Game g = bareGame();
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Volcanic Island", me, ZoneType.Battlefield);
        card("Volcanic Island", me, ZoneType.Battlefield);
        card("Delver of Secrets", me, ZoneType.Battlefield);
        card("Brainstorm", me, ZoneType.Hand);
        card("Force of Will", me, ZoneType.Hand);
        card("Ponder", me, ZoneType.Graveyard);
        card("Swamp", rival, ZoneType.Battlefield);
        card("Griselbrand", rival, ZoneType.Graveyard);
        for (String n : rivalHand) card(n, rival, ZoneType.Hand);
        for (String n : rivalLibrary) card(n, rival, ZoneType.Library);
        card("Snapcaster Mage", me, ZoneType.Library);
        card("Snapcaster Mage", me, ZoneType.Library);
        return g;
    }

    static SeatObservation observerFor(Game g, int seat) {
        return new SeatObservation(g, g.getPlayers().get(seat));
    }

    static String render(SeatObservation o, String history) {
        return o.render("seat", ContextProfile.V4_FAITHFUL, history, null, 0);
    }

    static void assertNoneOf(String text, String where, String... forbidden) {
        for (String f : forbidden) Assert.assertFalse(text.contains(f), where + " leaks '" + f + "':\n" + text);
    }

    // ---- paired worlds ------------------------------------------------------------------------

    @Test
    public void hiddenStateDoesNotChangeTheObservationHistoryOrToolResults() {
        Game a = world(new String[] {"Daze", "Reanimate"}, new String[] {"Entomb", "Thoughtseize", "Animate Dead"});
        Game b = world(new String[] {"Lightning Bolt", "Chain Lightning"}, new String[] {"Mountain", "Mountain", "Fireblast"});
        SeatObservation oa = observerFor(a, 0), ob = observerFor(b, 0);

        // A public engine event mentioning the viewer's own Delver by engine id, plus one that names a
        // hidden card in the rival's hand by id: the id must map to a handle or vanish identically.
        for (Game g : new Game[] {a, b}) {
            Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
            Card delver = me.getCardsIn(ZoneType.Battlefield).get(2);
            Card hidden = rival.getCardsIn(ZoneType.Hand).get(0);
            g.getGameLog().add(GameLogEntryType.STACK_ADD, "Viewer casts Brainstorm (" + delver.getId() + ")");
            g.getGameLog().add(GameLogEntryType.STACK_ADD, "Rival activates something (" + hidden.getId() + ")");
        }
        oa.capture(); ob.capture();
        String ha = eventsText(oa), hb = eventsText(ob);
        Assert.assertEquals(ha, hb, "seat-captured narration differs between hidden worlds");
        Assert.assertFalse(ha.contains("("), "engine ids survived redaction: " + ha);

        String ra = render(oa, ha), rb = render(ob, hb);
        Assert.assertEquals(ra, rb, "observation differs between worlds that differ only in hidden state");
        assertNoneOf(ra, "observation", "Daze", "Reanimate", "Entomb", "Thoughtseize", "Animate Dead",
                "Lightning Bolt", "Chain Lightning", "Fireblast", "Mountain");
        Assert.assertTrue(ra.contains("hand: 2 cards (hidden)"), ra);
        Assert.assertTrue(ra.contains("library 3 (hidden, order unknown)"), ra);
        Assert.assertTrue(ra.contains(SeatObservation.LEGEND), "legend missing");

        // Every handle the viewer has seen inspects identically in both worlds; no handle reaches a
        // hidden object because hidden objects are never sighted.
        for (int h = 1; h <= 12; h++) {
            String ia = oa.inspect("@" + h), ib = ob.inspect("@" + h);
            Assert.assertEquals(ia, ib, "INSPECT @" + h + " differs between worlds");
            assertNoneOf(ia, "INSPECT @" + h, "Daze", "Reanimate", "Lightning Bolt", "Entomb", "Fireblast");
        }
        // Card-database lookup by name is public text, and says so; it is not evidence of presence.
        String ref = oa.inspect("Daze");
        Assert.assertTrue(ref.startsWith("CARD REFERENCE (public database text; not evidence"), ref);
        Assert.assertEquals(ref, ob.inspect("Daze"));
    }

    static String eventsText(SeatObservation o) {
        StringBuilder b = new StringBuilder();
        for (GameHistory.Ev e : o.events()) b.append('[').append(e.id).append("] T").append(e.turn).append(' ').append(e.type).append(": ").append(e.msg).append('\n');
        return b.toString();
    }

    // ---- own visibility -----------------------------------------------------------------------

    @Test
    public void ownHandIsListedWithHandlesAndOpponentHandIsOnlyACount() {
        Game g = world(new String[] {"Daze"}, new String[] {"Entomb"});
        String r = render(observerFor(g, 0), "");
        Assert.assertTrue(r.contains("hand(2): "), r);
        Assert.assertTrue(r.contains("Brainstorm"), r);
        Assert.assertTrue(r.contains("Force of Will"), r);
        Assert.assertTrue(r.contains("OPP [Rival]"), r);
        Assert.assertTrue(r.contains("hand: 1 cards (hidden)"), r);
        Assert.assertTrue(r.contains("Griselbrand"), "opponent's graveyard is public: " + r);
        assertNoneOf(r, "observation", "Daze", "Entomb");
        // The rival's own observer sees its own hand, and not ours.
        String rr = render(observerFor(g, 1), "");
        Assert.assertTrue(rr.contains("Daze"), rr);
        assertNoneOf(rr, "rival observation", "Brainstorm", "Force of Will", "Entomb", "Snapcaster");
    }

    // ---- object identity ----------------------------------------------------------------------

    @Test
    public void duplicateCopiesGetDistinctStableHandles() {
        Game g = world(new String[0], new String[0]);
        SeatObservation o = observerFor(g, 0);
        Player me = g.getPlayers().get(0);
        Card v1 = me.getCardsIn(ZoneType.Battlefield).get(0), v2 = me.getCardsIn(ZoneType.Battlefield).get(1);
        String d1 = o.describe(v1, false), d2 = o.describe(v2, false);
        Assert.assertTrue(d1.startsWith("Volcanic Island"), d1);
        Assert.assertNotEquals(handle(d1), handle(d2), "two copies share a handle: " + d1 + " / " + d2);
        Assert.assertEquals(handle(o.describe(v1, false)), handle(d1), "handle is not stable across sightings");
        Assert.assertEquals(o.handleOf(v1), handle(d1));
        Assert.assertEquals(o.handleOf(v2), handle(d2));
        // The state line and a menu label name the same object with the same handle.
        String r = render(o, "");
        Assert.assertTrue(r.contains("@" + handle(d1)) && r.contains("@" + handle(d2)), r);
    }

    /** The object's OWN handle: the trailing "@N", ignoring a "(was @k)" predecessor suffix and any
     *  handles quoted earlier in the text (attachments, targets). */
    static int handle(String described) {
        String d = described.replaceAll("\\s*\\(was @\\d+\\)\\s*$", "").trim();
        Matcher m = HANDLE.matcher(d);
        int last = -1;
        while (m.find()) last = Integer.parseInt(m.group(1));
        Assert.assertTrue(last > 0, "no handle in: " + described);
        return last;
    }

    @Test
    public void zoneChangeMakesANewObjectLinkedOnlyAcrossVisibleZones() {
        Game g = world(new String[0], new String[0]);
        SeatObservation o = observerFor(g, 0);
        Player me = g.getPlayers().get(0);
        Card delver = me.getCardsIn(ZoneType.Battlefield).get(2);
        int h0 = handle(o.describe(delver, false));

        // Battlefield -> graveyard: both zones are visible, so the new object cites its predecessor.
        Card inYard = g.getAction().moveToGraveyard(delver, null);
        Assert.assertEquals(o.handleOf(inYard), -1, "a zone change must retire the old handle before re-sighting");
        String d1 = o.describe(inYard, false);
        int h1 = handle(d1);
        Assert.assertNotEquals(h1, h0, "zone change did not make a new handle: " + d1);
        Assert.assertTrue(d1.contains("(was @" + h0 + ")"), "public path not linked: " + d1);
        String hist = o.inspect("@" + h0);
        Assert.assertTrue(hist.contains("HISTORICAL"), hist);
        Assert.assertTrue(o.inspect("@" + h1).contains("(current)"), o.inspect("@" + h1));

        // Graveyard -> library (hidden) -> battlefield: the path ran through a hidden zone, so the
        // reappearing object is fresh and unlinked. Nothing may tie it to @h1.
        Card inLib = g.getAction().moveToLibrary(inYard, null);
        Card back = g.getAction().moveToPlay(inLib, null, null);
        String d2 = o.describe(back, false);
        int h2 = handle(d2);
        Assert.assertNotEquals(h2, h1);
        Assert.assertFalse(d2.contains("(was @"), "linked across a hidden shuffle path: " + d2);
    }

    @Test
    public void controlChangeKeepsTheObjectAndMovesItToTheNewControllersBoard() {
        Game g = world(new String[0], new String[0]);
        SeatObservation o = observerFor(g, 0);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card delver = me.getCardsIn(ZoneType.Battlefield).get(2);
        int h = handle(o.describe(delver, false));
        delver.setController(rival, g.getNextTimestamp());
        g.getAction().controllerChangeZoneCorrection(delver); // what the engine's state check does after a control change
        Assert.assertEquals(o.handleOf(delver), h, "control change is not a zone change; the handle must survive");
        String r = render(o, "");
        int youAt = r.indexOf("YOU ["), oppAt = r.indexOf("OPP [");
        int delverAt = r.indexOf("Delver of Secrets");
        Assert.assertTrue(delverAt > oppAt && oppAt > youAt, "Delver should now be listed under the opponent:\n" + r);
        Assert.assertTrue(o.inspect("@" + h).contains("controller: Rival"), o.inspect("@" + h));
    }

    // ---- face-down ----------------------------------------------------------------------------

    @Test
    public void opponentsFaceDownPermanentHidesItsIdentityButNotFromItsController() {
        Game g = world(new String[0], new String[0]);
        Player rival = g.getPlayers().get(1);
        Card morph = card("Willbender", rival, ZoneType.Battlefield);
        Assert.assertTrue(morph.turnFaceDown(true), "could not turn the test creature face down");
        SeatObservation mine = observerFor(g, 0), theirs = observerFor(g, 1);

        String d = mine.describe(morph, true); // even when the engine PRESENTS it as a candidate
        Assert.assertTrue(d.startsWith("face-down "), d);
        assertNoneOf(d, "face-down label", "Willbender");
        String r = render(mine, "");
        assertNoneOf(r, "observation", "Willbender");
        Assert.assertTrue(r.contains("face-down"), r);
        int h = handle(d);
        assertNoneOf(mine.inspect("@" + h), "INSPECT", "Willbender");
        Assert.assertTrue(mine.inspect("@" + h).contains("identity hidden from you"), mine.inspect("@" + h));

        String own = theirs.describe(morph, false);
        Assert.assertTrue(own.contains("Willbender"), "controller may look at its own morph: " + own);
        Assert.assertTrue(own.contains("[face-down, you may look]"), own);

        // Narration naming the hidden face is redacted for the opponent, not for the controller.
        String line = "Rival casts Willbender (" + morph.getId() + ") face down";
        g.getGameLog().add(GameLogEntryType.STACK_ADD, line);
        mine.capture(); theirs.capture();
        String mineText = eventsText(mine), theirsText = eventsText(theirs);
        assertNoneOf(mineText, "opponent narration", "Willbender");
        Assert.assertTrue(theirsText.contains("Willbender"), theirsText);
    }

    // ---- reveals through the live controller --------------------------------------------------

    static final class SilentBridge implements LLMBridge {
        public String ask(String prompt) { return null; }
        public String getLabel() { return "silent"; }
        public String getModel() { return "silent"; }
        public long getTotalMs() { return 0; }
        public int getCalls() { return 0; }
    }

    static PlayerControllerLLM faithfulController(Game g, int seat) {
        Player p = g.getPlayers().get(seat);
        PlayerControllerLLM.TEST_PROFILE_OVERRIDE = ContextProfile.V4_FAITHFUL;
        try {
            return new PlayerControllerLLM(g, p, p.getLobbyPlayer(), p.getName(), "", new SilentBridge(), new SilentBridge(), "llm");
        } finally {
            PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null;
        }
    }

    @Test
    public void revealsDeliveredToASeatAreRecordedForThatSeatOnly() {
        Game g = world(new String[] {"Daze", "Reanimate"}, new String[] {"Entomb"});
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        PlayerControllerLLM mine = faithfulController(g, 0), theirs = faithfulController(g, 1);
        Assert.assertNotNull(mine.observer(), "v4-faithful must construct an observer");
        Assert.assertNotNull(theirs.observer());

        // Thoughtseize-style: the engine shows US the rival's hand through our controller's reveal hook.
        mine.reveal(new CardCollection(rival.getCardsIn(ZoneType.Hand)), ZoneType.Hand, rival, "Thoughtseize", true);
        String r = render(mine.observer(), "");
        Assert.assertTrue(r.contains("REVEALS TO YOU (historical evidence"), r);
        Assert.assertTrue(r.contains("Daze") && r.contains("Reanimate"), r);
        Assert.assertTrue(r.contains("Rival's Hand"), r);
        Assert.assertTrue(r.contains("hand: 2 cards (hidden)"), "current state stays a count; the reveal is history: " + r);
        String ev = eventsText(mine.observer());
        Assert.assertTrue(ev.contains("[private reveal to you] Thoughtseize from Rival's Hand: Daze"), ev);
        // The rival's seat saw nothing of the sort, and never sees our hand.
        String rr = render(theirs.observer(), "");
        assertNoneOf(rr, "rival observation", "REVEALS TO YOU", "Brainstorm", "Force of Will", "Entomb");
        Assert.assertFalse(eventsText(theirs.observer()).contains("private reveal"), eventsText(theirs.observer()));

        // Presented candidates (a searched or revealed set handed to a menu) are recorded as reveals too.
        Card hiddenLib = rival.getCardsIn(ZoneType.Library).get(0);
        String label = mine.cardLabel(hiddenLib);
        Assert.assertTrue(label.startsWith("Entomb"), label);
        Assert.assertTrue(eventsText(mine.observer()).contains("engine presented Entomb from Rival's Library"), eventsText(mine.observer()));
        // ...but a candidate never presented stays hidden in ordinary state rendering.
        assertNoneOf(render(theirs.observer(), ""), "rival", "Snapcaster");
        Assert.assertEquals(me.getName(), "Viewer");
    }

    @Test
    public void menuLabelsAndStateShareHandlesAndTheLegacyProfileIsUntouched() {
        Game g = world(new String[0], new String[0]);
        Player me = g.getPlayers().get(0);
        PlayerControllerLLM faithful = faithfulController(g, 0);
        Card v1 = me.getCardsIn(ZoneType.Battlefield).get(0);
        String state = render(faithful.observer(), "");
        String label = faithful.cardLabel(v1);
        Assert.assertTrue(state.contains("@" + handle(label)), "menu label handle absent from state:\n" + state);
        Assert.assertEquals(label, faithful.observer().describe(v1, true));

        // Legacy profile: no observer, labels are the static candidateLabel byte-for-byte.
        PlayerControllerLLM legacy = new PlayerControllerLLM(g, me, me.getLobbyPlayer(), me.getName(), "",
                new SilentBridge(), new SilentBridge(), "llm");
        Assert.assertNull(legacy.observer(), "legacy profile must not construct an observer");
        Assert.assertEquals(legacy.cardLabel(v1), PlayerControllerLLM.candidateLabel(v1));
    }

    // ---- INSPECT via the memory tool surface --------------------------------------------------

    @Test
    public void inspectThroughContextMemoryIsBudgetedAndHistoricalAfterZoneChange() {
        Game g = world(new String[0], new String[0]);
        SeatObservation o = observerFor(g, 0);
        Player me = g.getPlayers().get(0);
        Card delver = me.getCardsIn(ZoneType.Battlefield).get(2);
        int h = handle(o.describe(delver, false));
        ContextMemory mem = new ContextMemory();
        mem.setInspector(o::inspect);
        String cur = mem.execute("INSPECT @" + h);
        Assert.assertTrue(cur.contains("(current)") && cur.contains("Delver of Secrets"), cur);
        Assert.assertTrue(cur.contains("oracle:"), cur);
        g.getAction().moveToGraveyard(delver, null);
        String old = mem.execute("INSPECT @" + h);
        Assert.assertTrue(old.contains("HISTORICAL"), old);
        Assert.assertTrue(mem.execute("INSPECT @999").startsWith("ERROR: unknown handle"), mem.execute("INSPECT @999"));
        Assert.assertTrue(mem.execute("INSPECT Not A Real Card Name").startsWith("ERROR: no card named"));
        List<String> prompts = new ArrayList<>();
        String reply = mem.ask("DECISION", p -> { prompts.add(p); return prompts.size() == 1 ? "INSPECT @" + h : "CHOICE: 1"; });
        Assert.assertEquals(reply, "CHOICE: 1");
        Assert.assertTrue(prompts.get(1).contains("MEMORY RESULT (historical data):\n@" + h + " is HISTORICAL"), prompts.get(1));
    }
}
