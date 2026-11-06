package forge.spike;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import com.google.common.collect.Lists;

import org.testng.Assert;
import org.testng.annotations.AfterClass;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
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
import forge.game.event.GameEventCardChangeZone;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.player.RegisteredPlayer;
import forge.game.zone.Zone;
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

    /** The solver's plan and own-card tracking read DECKLIST (aSolverWithNoSetupCardsStillReadsACardItDraws and
     *  the solver tests beside it): an exported DECKLIST=0 must not fail them (round 4 of PR #394), so each test runs
     *  against an empty environment and puts the old one back. */
    private Map<String, String> savedEnv;
    @BeforeMethod
    public void emptyEnvironment() {
        savedEnv = LlmSpikeGui.envOverride; LlmSpikeGui.envOverride = Map.of();
        LlmSpikeGui.puzzleLastTurn = -1;  // a puzzle board applied by an earlier class sets it (pr-review of #398)
    }
    @AfterMethod
    public void restoreEnvironment() { LlmSpikeGui.envOverride = savedEnv; }

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

    /** Before the first turn (the mulligan) there is no phase and no active player: the header read
     *  "== Turn 0 null (active: ? | priority: ?) ==" on every opening-hand ask (runs/loop/g07-g09). */
    @Test
    public void theHeaderBeforeTheFirstTurnSaysSo() {
        List<RegisteredPlayer> players = Lists.newArrayList();
        players.add(new RegisteredPlayer(new Deck()).setPlayer(new LobbyPlayerAi("Viewer", null)));
        players.add(new RegisteredPlayer(new Deck()).setPlayer(new LobbyPlayerAi("Rival", null)));
        GameRules rules = new GameRules(GameType.Constructed);
        Game g = new Game(players, rules, new Match(rules, players, "SeatObservationTest"));
        g.setAge(GameStage.Mulligan);
        String out = render(observerFor(g, 0), "");
        String header = out.substring(out.indexOf("== Turn"), out.indexOf("==\n", out.indexOf("== Turn") + 3) + 2);
        Assert.assertFalse(header.contains("null") || header.contains("?"), header);
        Assert.assertTrue(header.contains("before the first turn (opening hands)"), header);
    }

    /** The opponent's Shallow Grave on the stack read "returns a Creature.TopGraveyardCreature+YouCtrl card from their
     *  graveyard" (runs/loop/g07 T5): engine filters in the state are said in words. */
    @Test
    public void aStackLineSaysItsFilterInWords() {
        Game g = bareGame();
        card("Griselbrand", g.getPlayers().get(1), ZoneType.Graveyard);
        LineFindingProbeTest.rivalCasts(g, "Shallow Grave", null);
        String out = render(observerFor(g, 0), "");
        Assert.assertTrue(out.contains("Shallow Grave"), out);
        Assert.assertFalse(out.contains(". gains "), "a subjectless sentence: " + out);
        // A player with no untapped mana source (the opponent here) reads "none", not "{} up to 0 mana".
        Assert.assertFalse(out.contains("{} up to 0 mana"), out);
        Assert.assertTrue(out.contains("mana available: none (no untapped mana sources)"), out);
        Assert.assertFalse(java.util.regex.Pattern.compile(LlmSpikeGui.PlayerControllerLLM.ENGINE_FILTER_REGEX).matcher(out).find(),
                "the engine's filter is shown: " + out);
    }

    // ---- known hidden locations: the Unsummon -> Thoughtseize chain ---------------------------

    /** The seat can SEE a public card move into an opponent's hand and act on that knowledge — and the
     *  knowledge is dropped the moment the card is seen leaving. This is exactly the chain the review
     *  said the model could not follow ("unsummon a creature, then Thoughtseize it away"). */
    @Test
    public void seenMovesIntoHiddenZonesAreKnownThenInvalidated() {
        Game g = world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        Player rival = g.getPlayers().get(1);
        SeatObservation o = observerFor(g, 0);
        Card creature = card("Griselbrand", rival, ZoneType.Battlefield);
        Zone battlefield = creature.getZone();
        Zone hand = card("Entomb", rival, ZoneType.Hand).getZone();
        Zone yard = card("Dark Ritual", rival, ZoneType.Graveyard).getZone();

        o.describe(creature, false); // the viewer has seen it, so its identity is knowable
        Assert.assertFalse(render(o, "").contains("\nKNOWN IN HIDDEN ZONES"), "claimed knowledge too early");

        // "Return target creature to its owner's hand": a PUBLIC move INTO a hidden zone.
        o.onZoneChange(new GameEventCardChangeZone(creature, battlefield, hand));
        String r = render(o, "");
        Assert.assertTrue(r.contains("\nKNOWN IN HIDDEN ZONES"), r);
        Assert.assertTrue(r.contains("Rival's hand: Griselbrand"), r);

        // "...then Thoughtseize": the discard is hand -> graveyard, which we CAN see. The claim must
        // vanish, or the seat would keep "knowing" a card that is no longer in the hand.
        o.onZoneChange(new GameEventCardChangeZone(creature, hand, yard));
        Assert.assertFalse(render(o, "").contains("\nKNOWN IN HIDDEN ZONES"),
                "stale knowledge survived a public move out of the hidden zone");
    }

    /** A card the engine presents out of a hidden zone (a revealed hand / searched library) is
     *  knowledge even though the viewer never saw it on the battlefield. */
    @Test
    public void presentedCardsFromHiddenZonesAreRecordedAsKnown() {
        Game g = world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        Player rival = g.getPlayers().get(1);
        SeatObservation o = observerFor(g, 0);
        Card hidden = card("Entomb", rival, ZoneType.Hand);
        o.describe(hidden, true); // presented = the engine handed the viewer this card for a choice
        Assert.assertTrue(render(o, "").contains("Rival's hand: Entomb"), render(o, ""));
    }

    // ---- explicit state: the "spent this turn" ledger -----------------------------------------

    /** Legality is stated as an explicit fact rather than left to be inferred from a missing menu
     *  entry (a model cannot attend to gaps). Playing a land must flip the line to USED. */
    @Test
    public void spentThisTurnIsExplicitAndFlipsWhenALandIsPlayed() {
        Game g = world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        Player me = g.getPlayers().get(0);
        SeatObservation o = observerFor(g, 0);
        String before = render(o, "");
        Assert.assertTrue(before.contains("SPENT THIS TURN:"), before);
        Assert.assertTrue(before.contains("land drop AVAILABLE"), before);
        Assert.assertTrue(before.contains("attackers declared none"), before);
        me.setLandsPlayedThisTurn(me.getMaxLandPlays());
        String after = render(o, "");
        Assert.assertTrue(after.contains("land drop USED"), after);
        Assert.assertFalse(after.contains("land drop AVAILABLE"), after);
    }

    // ---- the plan survives the turn boundary ------------------------------------------------

    /** The plan is deliberately NOT dropped when the turn passes: "hold up UU and counter their
     *  payoff" only means anything on THEIR turn, which the old same-turn gate erased. It carries the
     *  turn it was stated and flags itself as possibly stale once the turn has moved on. */
    @Test
    public void thePlanSurvivesTheTurnBoundaryAndIsMarkedStale() {
        Game g = world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        SeatObservation o = observerFor(g, 0);
        int now = g.getPhaseHandler().getTurn();
        Assert.assertFalse(o.render("seat", ContextProfile.V4_FAITHFUL, "", null, -1).contains("\nYOUR PLAN"));
        String sameTurn = o.render("seat", ContextProfile.V4_FAITHFUL, "", "hold up UU, counter their payoff", now);
        Assert.assertTrue(sameTurn.contains("\nYOUR PLAN (yours, stated at T" + now), sameTurn);
        Assert.assertTrue(sameTurn.contains(", this turn;"), sameTurn);
        Assert.assertTrue(sameTurn.contains("It is a note, not a commitment: check it against the state above at this decision"), "it says the plan can change: " + sameTurn);
        String previous = o.render("seat", ContextProfile.V4_FAITHFUL, "", "hold up UU, counter their payoff", now - 1);
        Assert.assertTrue(previous.contains(", an earlier turn;"), previous);
        Assert.assertTrue(previous.contains("hold up UU"), previous);
    }

    // ---- engine facts ------------------------------------------------------------------------

    /** The engine's own deterministic arithmetic is stated as facts — both sides' next-combat clocks
     *  and the opponent's instant-speed mana — labelled as estimates, never as truth. */
    @Test
    public void engineFactsStateBothClocksAndTheirInstantSpeedMana() {
        Game g = world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        Player me = g.getPlayers().get(0);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        SeatObservation o = observerFor(g, 0);
        String r = render(o, "");
        Assert.assertTrue(r.contains("FACTS (engine estimates, not truth):"), r);
        Assert.assertTrue(r.contains("your next combat:"), r);
        Assert.assertTrue(r.contains("unblocked; their life"), r);
        Assert.assertTrue(r.contains("instant speed"), r);
    }

    /** A puzzle's last turn ends the game, so once the solver's last combat before it has ended there is no next
     *  combat to be lethal in (#398), from the end-of-combat step on. Before that, and outside puzzles, the clock is
     *  unchanged. */
    @Test
    public void aPuzzleSolverIsNotPromisedACombatAfterItsTurnLimit() {
        Game g = world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Grizzly Bears", rival, ZoneType.Battlefield);
        rival.setLife(2, null);
        me.setLife(2, null);
        SeatObservation o = observerFor(g, 0);
        g.getPhaseHandler().devModeSet(PhaseType.MAIN2, me, 1);
        String game = render(o, "");
        Assert.assertTrue(game.contains("your next combat: 2 attacker(s) dealing 3 unblocked; their life 2 -> 0 = LETHAL"), game);
        try {
            LlmSpikeGui.puzzleLastTurn = 1;
            g.getPhaseHandler().devModeSet(PhaseType.COMBAT_END, me, 1);
            Assert.assertTrue(render(o, "").contains("your next combat: none before the puzzle's turn limit"));
            g.getPhaseHandler().devModeSet(PhaseType.MAIN2, me, 1);
            String after = render(o, "");
            Assert.assertTrue(after.contains("your next combat: none before the puzzle's turn limit"), after);
            Assert.assertTrue(after.contains("their next combat: none before the puzzle's turn limit"), after);
            Assert.assertFalse(after.contains("= LETHAL"), after);
            g.getPhaseHandler().devModeSet(PhaseType.MAIN1, me, 1);
            String before = render(o, "");
            Assert.assertTrue(before.contains("your next combat: 2 attacker(s) dealing 3 unblocked; their life 2 -> 0 = LETHAL"), before);
            Assert.assertTrue(before.contains("their next combat: none before the puzzle's turn limit"), before);
            LlmSpikeGui.puzzleLastTurn = 2;
            g.getPhaseHandler().devModeSet(PhaseType.MAIN2, me, 1);
            String next = render(o, "");
            Assert.assertTrue(next.contains("your next combat: none before the puzzle's turn limit"), next);
            Assert.assertTrue(next.contains("their next combat: 1 attacker(s) dealing 2 unblocked; YOUR life 2 -> 0 = LETHAL"), next);
        } finally {
            LlmSpikeGui.puzzleLastTurn = -1;
        }
        g.getPhaseHandler().devModeSet(PhaseType.MAIN2, me, 1);
        Assert.assertEquals(render(o, ""), game);
    }

    /** A card the opponent has played 2+ times is named as RECURRING — a threat the model would
     *  otherwise have to re-derive from a long log. Played once: not recurring. */
    @Test
    public void repeatedOpponentPlayIsSurfacedAsRecurring() {
        Game g = world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        Player rival = g.getPlayers().get(1);
        SeatObservation o = observerFor(g, 0);
        cast(g, rival, "Chain Lightning");
        cast(g, rival, "Chain Lightning");
        cast(g, rival, "Lava Spike");
        o.capture();
        String r = render(o, "");
        Assert.assertTrue(r.contains("RECURRING (cast 2+ times): Chain Lightning x2"), r);
        Assert.assertFalse(r.contains("Lava Spike x"), r);
    }

    /** The opponent's casts and land drops come from the engine's events, not from the log's wording: a log
     *  line that says "cast" is not a cast, a copy is not cast (CR 707.10), and a face-down spell stays hidden. */
    @Test
    public void castsAndLandDropsComeFromEngineEventsNotLogWording() {
        Game g = world(new String[0], new String[0]);
        Player rival = g.getPlayers().get(1);
        SeatObservation o = observerFor(g, 0);
        g.getGameLog().add(GameLogEntryType.STACK_ADD, "Rival cast Lightning Bolt.");
        cast(g, rival, "Brainstorm");
        Card copy = cast(g, rival, "Brainstorm", true);
        Card morph = onStack(g, rival, "Exalted Angel");
        forge.game.spellability.SpellAbility msa = morph.getFirstSpellAbility();
        msa.setActivatingPlayer(rival);
        morph.turnFaceDown(true);
        g.fireEvent(new forge.game.event.GameEventSpellAbilityCast(msa,
                new forge.game.spellability.SpellAbilityStackInstance(msa), 0));
        Card land = card("Mountain", rival, ZoneType.Battlefield);
        g.fireEvent(new forge.game.event.GameEventLandPlayed(rival.getView(), land.getView()));
        o.capture();
        String r = render(o, "");
        Assert.assertTrue(copy.isCopiedSpell());
        Assert.assertTrue(r.contains("CAST THIS GAME: Brainstorm, a face-down spell"), r);
        Assert.assertFalse(r.contains("Lightning Bolt") || r.contains("Exalted Angel"), r);
        Assert.assertTrue(r.contains("LANDS PLAYED: Mountain"), r);
    }

    /** A card of {@code who}'s in the game's stack zone (the stack is not a player's zone). */
    static Card onStack(Game g, Player who, String name) {
        Card c = card(name, who, ZoneType.Hand);
        who.getZone(ZoneType.Hand).remove(c);
        g.getStackZone().add(c);
        return c;
    }

    /** Puts {@code name} on the stack as {@code who}'s spell and fires the engine's cast event. */
    static Card cast(Game g, Player who, String name) { return cast(g, who, name, false); }
    static Card cast(Game g, Player who, String name, boolean copy) {
        Card c = onStack(g, who, name);
        if (copy) c.setGamePieceType(forge.card.GamePieceType.COPIED_SPELL);
        forge.game.spellability.SpellAbility sa = c.getFirstSpellAbility();
        sa.setActivatingPlayer(who);
        g.fireEvent(new forge.game.event.GameEventSpellAbilityCast(sa, new forge.game.spellability.SpellAbilityStackInstance(sa), 0));
        return c;
    }

    /** INSPECT answers "what happened to this?" — the object's zone history, which is the object-level
     *  half of continuity: Forge makes a new Card on every move, so without this the model can only see
     *  that a NEW thing appeared. */
    @Test
    public void inspectShowsAnObjectsZoneTransitionHistory() {
        Game g = world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        Player rival = g.getPlayers().get(1);
        SeatObservation o = observerFor(g, 0);
        Card creature = card("Griselbrand", rival, ZoneType.Battlefield);
        Zone battlefield = creature.getZone();
        Zone yard = card("Dark Ritual", rival, ZoneType.Graveyard).getZone();
        o.describe(creature, false);
        int h = o.handleOf(creature);
        Assert.assertTrue(h > 0, "the creature should have a handle");
        Assert.assertTrue(o.inspect("@" + h).contains("history:"), o.inspect("@" + h));
        Assert.assertTrue(o.inspect("@" + h).contains("first seen in battlefield"), o.inspect("@" + h));
        o.onZoneChange(new GameEventCardChangeZone(creature, battlefield, yard));
        String hist = o.inspect("@" + h);
        Assert.assertTrue(hist.contains("battlefield -> graveyard"), hist);
    }

    /** The model's own deck-interaction note: absent until it writes one, then shown and labelled as
     *  its own unverified note. */
    @Test
    public void deckInteractionsAreShownOnlyAfterTheModelWritesThem() {
        Game g = world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        SeatObservation o = observerFor(g, 0);
        Assert.assertFalse(render(o, "").contains("YOUR CARD INTERACTIONS"));
        o.setDeckLines("Cabal Therapy flashback sacrifices a creature | sacrificing a Cat token flips Ajani");
        String r = render(o, "");
        Assert.assertTrue(r.contains("YOUR CARD INTERACTIONS (you wrote this from your own decklist; unverified)"), r);
        Assert.assertTrue(r.contains("flips Ajani"), r);
    }

    /** A reveal from an earlier turn says how old it is, as a fact; one from this turn is unmarked.
     *  The harness does not call it stale: it cannot know whether the opponent's hand changed. */
    @Test
    public void oldRevealsAreStampedWithTheirAge() {
        Game g = world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        SeatObservation o = observerFor(g, 0);
        int now = g.getPhaseHandler().getTurn();
        o.recordReveal("saw their hand [Force of Will]");
        String fresh = render(o, "");
        Assert.assertTrue(fresh.contains("saw their hand [Force of Will]"), fresh);
        Assert.assertFalse(fresh.contains("ago)"), "a reveal from this turn must not carry an age:\n" + fresh);
        g.getPhaseHandler().devModeSet(forge.game.phase.PhaseType.MAIN1, g.getPlayers().get(0), now + 3);
        String later = render(o, "");
        Assert.assertTrue(later.contains("T" + now + ": (3 turns ago)"), later);
        Assert.assertFalse(later.contains("stale"), "the harness must not judge staleness:\n" + later);
    }

    /** The seat reads the OPPONENT's cards too. An opponent's card it can identify is printed with its
     *  rules text from the card database; a card in a hidden zone is not; a basic land (no text) is
     *  not; and the seat's own cards are not repeated here. */
    @Test
    public void opponentCardsTheSeatHasSeenAreShownWithTheirRulesText() {
        Game g = world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        Player rival = g.getPlayers().get(1);
        card("Orcish Bowmasters", rival, ZoneType.Battlefield);
        card("Karakas", rival, ZoneType.Battlefield);
        String r = render(observerFor(g, 0), "");
        int at = r.indexOf("OPPONENT CARDS YOU HAVE SEEN");
        Assert.assertTrue(at >= 0, r);
        String block = r.substring(at, r.indexOf("STACK", at));
        Assert.assertTrue(block.contains("  Orcish Bowmasters: ") && block.contains("deals 1 damage to any target"), block);
        Assert.assertTrue(block.contains("  Karakas: ") && block.contains("Return target legendary creature"), block);
        Assert.assertTrue(block.contains("  Griselbrand: "), "a card in their graveyard is public:\n" + block);
        assertNoneOf(block, "opponent card text", "Thoughtseize", "Entomb", "Swamp:", "Delver of Secrets", "Force of Will");
    }

    /** A puzzle's solver is told its setup cards in its system prompt (#391); an own card it sees later (here, drawn
     *  from its library) gets its text in the state as it is first identified, once, and a card the setup listed is
     *  not repeated. A game seat (no tracking) gets no such block: its deck text lists every card it can hold. */
    @Test
    public void aPuzzleSolversCardDrawnLaterIsShownWithItsText() {
        Game g = world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        Player me = g.getPlayers().get(0);
        SeatObservation o = observerFor(g, 0);
        o.setOwnDeckText(LlmSpikePuzzle.solverPlan(java.util.Map.of("state", List.of(
                "p0battlefield=Volcanic Island;Volcanic Island;Delver of Secrets", "p0hand=Brainstorm;Force of Will",
                "p0graveyard=Ponder"))));
        o.setTrackOwnCards(true);
        String before = render(o, "");
        Assert.assertFalse(before.contains("YOUR CARDS SINCE THE START"), "the setup cards were repeated:\n" + before);
        Card snap = me.getCardsIn(ZoneType.Library).get(0);
        g.getAction().moveToHand(snap, null);
        String after = render(o, "");
        int at = after.indexOf("YOUR CARDS SINCE THE START");
        Assert.assertTrue(at >= 0, after);
        String block = after.substring(at, after.indexOf("OPPONENT CARDS YOU HAVE SEEN", at));
        Assert.assertTrue(block.contains("  Snapcaster Mage: ") && block.contains("Flash"), block);
        assertNoneOf(block, "own new cards", "Brainstorm", "Delver of Secrets", "Ponder", "Griselbrand");
        // A name the other block already prints is not printed twice: the opponent's Griselbrand (in their graveyard,
        // seen at the first render) is the solver's own card too once it holds one.
        card("Griselbrand", me, ZoneType.Hand);
        String both = render(o, "");
        Assert.assertEquals(both.split("  Griselbrand: ", -1).length - 1, 1, "Griselbrand printed twice:\n" + both);
        SeatObservation game = observerFor(g, 0);
        Assert.assertFalse(render(game, "").contains("YOUR CARDS SINCE THE START"), "a game seat got the puzzle block");
    }

    /** A solver whose setup gives it no card to list (RP_460: a Clue token on the battlefield, Lightning Bolt in the
     *  library) has no YOUR CARDS block, and still reads a card it draws: tracking is the solver's under DECKLIST, not
     *  a stand-in for "has a setup block" (#391 review round 3; at 446e5bb the drawn Bolt had no text). */
    @Test
    public void aSolverWithNoSetupCardsStillReadsACardItDraws() {
        Game g = bareGame();
        Player me = g.getPlayers().get(0);
        Card bolt = card("Lightning Bolt", me, ZoneType.Library);
        String plan = LlmSpikePuzzle.solverPlan(java.util.Map.of("state",
                List.of("p0battlefield=t:c_a_clue_draw", "p0library=Lightning Bolt")));
        Assert.assertEquals(plan, "", "fixture: the setup lists no card");
        SeatObservation o = observerFor(g, 0);
        o.setOwnDeckText(plan);
        o.setTrackOwnCards(LlmSpikeGui.tracksOwnCards(true));
        Assert.assertFalse(render(o, "").contains("Lightning Bolt"), "the library card was shown");
        g.getAction().moveToHand(bolt, null);
        String after = render(o, "");
        Assert.assertTrue(after.contains("YOUR CARDS SINCE THE START") && after.contains("  Lightning Bolt: "), after);
    }

    /** The puzzle form of the seat's own text (LlmSpikePuzzle.solverPlan, with a type line) also keeps a card it lists
     *  out of OPPONENT CARDS YOU HAVE SEEN: the opponent's Griselbrand is the solver's own card too. */
    @Test
    public void aSolversOwnCardIsNotRepeatedAsTheOpponents() {
        Game g = world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        Player rival = g.getPlayers().get(1);
        card("Orcish Bowmasters", rival, ZoneType.Battlefield);
        SeatObservation o = observerFor(g, 0);
        o.setOwnDeckText(LlmSpikePuzzle.solverPlan(java.util.Map.of("state", List.of("p0hand=Griselbrand"))));
        String r = render(o, "");
        int at = r.indexOf("OPPONENT CARDS YOU HAVE SEEN");
        String block = r.substring(at, r.indexOf("STACK", at));
        Assert.assertTrue(block.contains("  Orcish Bowmasters: "), block);
        Assert.assertFalse(block.contains("  Griselbrand: "), "a card in the solver's YOUR CARDS was repeated:\n" + block);
    }

    /** Text once seen stays known after the card leaves; a card already printed in the seat's own
     *  deck text is not printed twice; and the block never depends on the opponent's hidden cards. */
    @Test
    public void opponentCardTextPersistsIsNotDuplicatedAndIgnoresHiddenZones() {
        Game g = world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        Player rival = g.getPlayers().get(1);
        Card bow = card("Orcish Bowmasters", rival, ZoneType.Battlefield);
        SeatObservation o = observerFor(g, 0);
        o.setOwnDeckText("YOUR DECK:\n  Griselbrand {4}{B}{B}{B}{B} — Flying, lifelink\n");
        String first = render(o, "");
        Assert.assertTrue(first.contains("  Orcish Bowmasters: "), first);
        Assert.assertFalse(first.contains("  Griselbrand: "), "a card in the seat's own deck text was repeated:\n" + first);
        g.getAction().moveToLibrary(bow, null);
        String later = render(o, "");
        Assert.assertTrue(later.contains("  Orcish Bowmasters: "), "the text of a card already seen was forgotten:\n" + later);

        Game a = world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        Game b = world(new String[] {"Orcish Bowmasters"}, new String[] {"Karakas"});
        Assert.assertEquals(render(observerFor(a, 0), ""), render(observerFor(b, 0), ""),
                "the observation depends on the opponent's hidden hand/library");
    }

    /** The model's own opponent read is re-injected every decision and always labelled unverified:
     *  it is a hypothesis it can revise, never an engine fact. */
    @Test
    public void opponentProfileIsShownOnlyWhenTheModelWroteOneAndIsLabelledUnverified() {
        Game g = world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        SeatObservation o = observerFor(g, 0);
        Assert.assertFalse(render(o, "").contains("\nYOUR OPPONENT PROFILE"));
        o.setProfile("Grixis Reanimator: Entomb+Reanimate; respect Force of Will");
        String r = render(o, "");
        Assert.assertTrue(r.contains("\nYOUR OPPONENT PROFILE (you wrote this; unverified"), r);
        Assert.assertTrue(r.contains("respect Force of Will"), r);
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
        // v24: how to read the state is said once, in the system prompt, not in every message.
        Assert.assertFalse(ra.contains("LEGEND:"), "the legend is back in the message");
        String system = LlmSpikeGui.PlayerControllerLLM.seatFraming("Seat", "GAME PLAN: x", true, true, true);
        Assert.assertTrue(system.contains(SeatObservation.GUIDE), "the system prompt lost the guide to the state");
        Assert.assertFalse(system.contains("PRINCIPLES") || system.contains("WORKED EXAMPLES") || system.contains("at most two"),
                "the faithful system prompt states facts, not coaching (PROMPT-AUDIT.md C1, C2, C5)");
        // The format is the one the decks are built for; the reference is the same terms in every format, and
        // names no eval-pool archetype (2026-10-16 review).
        Assert.assertTrue(system.contains("Legacy format") && system.contains("<reference>"), "the default is Legacy");
        String modernSystem = LlmSpikeGui.PlayerControllerLLM.seatFraming("modern", "Seat", "GAME PLAN: x", true, true, true);
        Assert.assertTrue(modernSystem.contains("Magic: The Gathering, Modern format"), modernSystem.substring(0, 120));
        Assert.assertEquals(modernSystem.substring(modernSystem.indexOf("<reference>")), system.substring(system.indexOf("<reference>")),
                "a Legacy and a Modern seat get one reference");
        Assert.assertFalse(system.contains("Archetypes in this pool") || system.contains("Sneak & Show"), "the eval pool is named");
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.gameFormat(" Pauper "), "pauper");
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.gameFormat(null), "legacy");

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

    /** A puzzle solver is told the author's goal in place of "win the game"; outside puzzle mode the framing
     *  is unchanged. Both get the same terms reference. */
    @Test
    public void aPuzzleSolverIsToldTheGoalAsTheAuthorWroteIt() {
        String game = LlmSpikeGui.PlayerControllerLLM.seatFraming("Seat", "", true, true, true);
        try {
            LlmSpikeGui.puzzleBrief = "Goal: Play the Specified Permanent\nTurns Limit: 1\nYou know the top card is Channel.";
            String pz = LlmSpikeGui.PlayerControllerLLM.seatFraming("Solver", "", true, true, true);
            Assert.assertTrue(pz.contains("<puzzle>\nGoal: Play the Specified Permanent") && pz.contains("top card is Channel"), pz);
            Assert.assertFalse(pz.contains("Your goal is to win the game"), pz);
            Assert.assertTrue(pz.contains("<reference>"), pz);
        } finally {
            LlmSpikeGui.puzzleBrief = null;
        }
        Assert.assertTrue(game.contains("Your goal is to win the game") && game.contains("<reference>"));
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.seatFraming("Seat", "", true, true, true), game);
    }

    static String eventsText(SeatObservation o) {
        StringBuilder b = new StringBuilder();
        for (GameHistory.Ev e : o.events()) b.append('[').append(e.id).append("] T").append(e.turn).append(' ').append(e.type).append(": ").append(e.msg).append('\n');
        return b.toString();
    }

    // ---- narration redaction (object-anchored, not a name dictionary) -------------------------

    /**
     * REVIEW DEFECT 1, negative case. The old capture() built a game-wide dictionary of every
     * face-down card's real name and string-replaced it out of EVERY message. A public card that
     * merely shared a hidden card's name was erased from true history, and (worse) renaming the
     * hidden card changed PUBLIC text. The fix redacts by the object an event actually names.
     */
    @Test
    public void publicEvidenceSurvivesAHiddenCardWithTheSameName() {
        Game g = world(new String[0], new String[0]);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card publicWillbender = card("Willbender", me, ZoneType.Battlefield); // face-up, public
        Card morph = card("Willbender", rival, ZoneType.Battlefield);         // same name, hidden face
        Assert.assertTrue(morph.turnFaceDown(true), "could not turn the test creature face down");
        SeatObservation o = observerFor(g, 0);
        Assert.assertFalse(o.faceKnown(morph), "fixture: the rival's face-down card must be hidden from us");

        // A public event naming the PUBLIC card must survive intact; only the hidden one is redacted.
        g.getGameLog().add(GameLogEntryType.STACK_ADD, "Viewer resolves Willbender (" + publicWillbender.getId() + ")");
        g.getGameLog().add(GameLogEntryType.STACK_ADD, "Rival casts Willbender (" + morph.getId() + ") face down");
        o.capture();
        String h = eventsText(o);
        Assert.assertTrue(h.contains("Viewer resolves Willbender @"), "public evidence was erased:\n" + h);
        Assert.assertFalse(h.contains("Rival casts Willbender"), "hidden identity leaked:\n" + h);
        Assert.assertTrue(h.contains("Rival casts a face-down card"), h);
    }

    /** A hidden card in a private zone, named by the event, must lose its name with its engine id. */
    @Test
    public void hiddenHandCardNameIsRedactedByItsEngineId() {
        Game g = world(new String[] {"Thoughtseize"}, new String[0]);
        Player rival = g.getPlayers().get(1);
        Card hidden = rival.getCardsIn(ZoneType.Hand).get(0);
        SeatObservation o = observerFor(g, 0);
        g.getGameLog().add(GameLogEntryType.STACK_ADD, "Rival casts Thoughtseize (" + hidden.getId() + ")");
        o.capture();
        String h = eventsText(o);
        Assert.assertFalse(h.contains("Thoughtseize"), "hidden hand card leaked by name:\n" + h);
        Assert.assertTrue(h.contains("Rival casts a hidden card"), h);
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

    /** G2: a face-down card in EXILE (zone-visible, face hidden) must not reveal its identity, to
     *  anyone — the engine's mayFaceDownBeShownTo permission covers only Battlefield/Stack/Sideboard
     *  for the controller. */
    @Test
    public void faceDownCardInExileHidesItsIdentity() {
        Game g = world(new String[0], new String[0]);
        Player rival = g.getPlayers().get(1);
        Card hidden = card("Willbender", rival, ZoneType.Exile);
        Assert.assertTrue(hidden.turnFaceDown(true), "could not turn the exiled card face down");
        SeatObservation o = observerFor(g, 0);
        Assert.assertFalse(o.identityKnown(hidden), "fixture: a face-down exile card must be hidden");
        String r = render(o, "");
        Assert.assertTrue(r.contains("exile(1)"), r);
        assertNoneOf(r, "exile", "Willbender");
        Assert.assertTrue(r.contains("face-down"), r);
    }

    /** G2: a TOKEN is a distinct permanent with its own handle, rendered with its real P/T. */
    @Test
    public void tokensGetTheirOwnIdentityAndRender() {
        Game g = world(new String[0], new String[0]);
        Player me = g.getPlayers().get(0);
        Card host = me.getCardsIn(ZoneType.Battlefield).get(0);
        forge.game.spellability.SpellAbility tokenSa = forge.game.ability.AbilityFactory.getAbility(
                "AB$ Token | Cost$ 0 | TokenScript$ r_1_1_goblin | SpellDescription$ make a token.", host);
        Card proto = forge.game.card.token.TokenInfo.getProtoType("r_1_1_goblin", tokenSa, me);
        Assert.assertNotNull(proto, "fixture: no token prototype for r_1_1_goblin");
        proto.setGamePieceType(forge.card.GamePieceType.TOKEN);
        me.getZone(ZoneType.Battlefield).add(proto);

        SeatObservation o = observerFor(g, 0);
        String d = o.shortRef(proto);
        Assert.assertTrue(d.contains("Goblin"), d);
        Assert.assertTrue(d.contains("1/1"), d);
        Assert.assertTrue(d.contains("@"), d);
        Assert.assertNotEquals(o.handleOf(proto), o.handleOf(host), "a token must not share the host's handle");
        String r = render(o, "");
        Assert.assertTrue(r.contains("Goblin"), r);
        // A token has no database entry: inspecting it by name answers with the object (it was "no card named").
        for (String asked : new String[] {"Goblin", "Goblin Token", "goblin token"}) {
            String seen = o.inspect(asked);
            Assert.assertTrue(seen.contains("@" + o.handleOf(proto) + " (current)"), asked + " -> " + seen);
        }
        Assert.assertTrue(o.inspect("Nonexistent Thing").startsWith("ERROR"), "a name nothing has is still an error");
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
        Assert.assertTrue(r.contains("REVEALS TO YOU (Forge game log"), r);
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

    /** runs/loop/g08 (seed 61, T3 and T5): the seat cast Cabal Therapy targeting ITSELF to bin
     *  Griselbrand, then a separate name ask -- which never said who the target was and told it to name
     *  "the card most likely there" -- named Ajani and then Amped Raptor, cards in the opponent's hand.
     *  The name decision must state whose hand the named card leaves. */
    @Test
    public void aNameAskStatesWhoTheSpellTargets() {
        Game g = bareGame();
        Player me = g.getPlayers().get(0), opp = g.getPlayers().get(1);
        Card therapy = card("Cabal Therapy", me, ZoneType.Hand);
        forge.game.spellability.SpellAbility sa = null;
        for (forge.game.spellability.SpellAbility s : therapy.getSpellAbilities()) if (s.isSpell()) { sa = s; break; }
        Assert.assertNotNull(sa);
        sa.setActivatingPlayer(me);
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.targetNote(sa, me), "", "no target chosen yet: nothing to state");

        sa.getSubAbility().getTargets().add(me);
        String self = LlmSpikeGui.PlayerControllerLLM.targetNote(sa, me);
        Assert.assertTrue(self.contains("YOU (Viewer)"), self);

        sa.getSubAbility().resetTargets();
        sa.getSubAbility().getTargets().add(opp);
        String them = LlmSpikeGui.PlayerControllerLLM.targetNote(sa, me);
        Assert.assertTrue(them.contains("Rival"), them);
        Assert.assertFalse(them.contains("YOU"), them);
    }

    /** runs/loop/g08 (seed 61, T1 and T3): Unmask sat in hand beside four other black cards and was
     *  never on the play menu ("Unmask isn't offered as an option, so I can't set up Reanimate yet").
     *  A spell castable by its alternative cost (exile a black card) must be offered. */
    @Test
    public void aSpellCastableOnlyByItsAlternativeCostIsOffered() {
        Game g = bareGame();
        Player me = g.getPlayers().get(0);
        card("Unmask", me, ZoneType.Hand);
        card("Griselbrand", me, ZoneType.Hand);
        LlmSpikeGui.PlayerControllerLLM.PlayOptions po = LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(me, true);
        List<String> labels = LlmSpikeGui.PlayerControllerLLM.buildPlayLabels(po.spells, po.landCards);
        Assert.assertTrue(labels.stream().anyMatch(l -> l.contains("Unmask")), labels.toString());
    }

    /** Issue #106: the menu stopped adding spells after the twelfth, and said nothing. Thirteen
     *  zero-cost spells in hand must give thirteen CAST lines; on the capped menu the thirteenth
     *  (Welding Jar, last in hand order) is missing. */
    @Test
    public void everyCastableSpellIsOfferedPastTwelve() {
        Game g = bareGame();
        Player me = g.getPlayers().get(0);
        String[] hand = {"Ornithopter", "Memnite", "Phyrexian Walker", "Shield Sphere", "Mox Pearl",
                "Mox Sapphire", "Mox Jet", "Mox Ruby", "Mox Emerald", "Lotus Petal", "Mishra's Bauble",
                "Urza's Bauble", "Welding Jar"};
        for (String n : hand) card(n, me, ZoneType.Hand);
        LlmSpikeGui.PlayerControllerLLM.PlayOptions po = LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(me, true);
        List<String> labels = LlmSpikeGui.PlayerControllerLLM.buildPlayLabels(po.spells, po.landCards);
        for (String n : hand)
            Assert.assertTrue(labels.stream().anyMatch(l -> l.startsWith("CAST " + n + " ")), n + " missing from " + labels);
    }

    /** docs/harness/MODEL-LIMITS.md: nothing the observation shows is cut. An event's text was cut at
     *  90 characters (19-31% of asks in runs/compare lost the end of an event, block assignments among
     *  them), an opponent card's text at 320 (Ragavan lost its Dash clause), reveals to the latest 6
     *  (48% of asks), and an object's zone trail to 8 moves. */
    @Test
    public void nothingTheObservationShowsIsCut() {
        Game g = world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        Player rival = g.getPlayers().get(1);
        SeatObservation o = observerFor(g, 0);
        String longEvent = "Rival assigned Murktide Regent to block Thalia, Guardian of Thraben. Rival assigned "
                + "Dragon's Rage Channeler to block Mother of Runes. Rival didn't block Stoneforge Mystic.";
        g.getGameLog().add(GameLogEntryType.COMBAT, longEvent);
        o.capture();
        GameHistory.Ev last = o.events().get(o.events().size() - 1);
        Assert.assertEquals(last.msg, longEvent);

        card("Ragavan, Nimble Pilferer", rival, ZoneType.Battlefield);
        for (int i = 0; i < 10; i++) o.recordReveal("reveal number " + i);
        String r = render(o, "");
        Assert.assertTrue(r.contains("beginning of the next end step"), "Ragavan's text was cut:\n" + r);
        for (int i = 0; i < 10; i++) Assert.assertTrue(r.contains("reveal number " + i), r);

        Card creature = card("Griselbrand", rival, ZoneType.Battlefield);
        Zone battlefield = creature.getZone();
        Zone yard = card("Dark Ritual", rival, ZoneType.Graveyard).getZone();
        o.describe(creature, false);
        int h = o.handleOf(creature);
        for (int i = 0; i < 12; i++) o.onZoneChange(new GameEventCardChangeZone(creature, battlefield, yard));
        String hist = o.inspect("@" + h);
        Assert.assertEquals(hist.split("battlefield -> graveyard", -1).length - 1, 12, hist);
    }

    /** Mana available is the mana the engine says each source makes, not a count of sources: the three Tron lands
     *  make seven, Ancient Tomb two (g7: Kozilek's Command looked forced at X=0 while the line said 2 sources). */
    @Test
    public void manaAvailableIsTheEnginesManaNotACountOfSources() {
        Game g = LineFindingProbeTest.bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        LineFindingProbeTest.card("Urza's Tower", me, ZoneType.Battlefield);
        LineFindingProbeTest.card("Urza's Mine", me, ZoneType.Battlefield);
        LineFindingProbeTest.card("Urza's Power Plant", me, ZoneType.Battlefield);
        String tron = PlayerControllerLLM.manaAvailable(me);
        Assert.assertTrue(tron.startsWith("{C:7} up to 7 mana (3 untapped sources;"), tron);
        Assert.assertTrue(tron.contains("Urza's Tower makes 3") && tron.contains("Urza's Mine makes 2"), tron);
        LineFindingProbeTest.card("Ancient Tomb", me, ZoneType.Battlefield);
        LineFindingProbeTest.card("Volcanic Island", me, ZoneType.Battlefield);
        String more = PlayerControllerLLM.manaAvailable(me);
        Assert.assertTrue(more.startsWith("{U:1 R:1 C:9} up to 10 mana (5 untapped sources;"), more);
        Assert.assertTrue(more.contains("Ancient Tomb makes 2"), more);
        Assert.assertTrue(SeatObservation.manaLine(me).contains("5 untapped sources; each counts its mana under every colour"),
                SeatObservation.manaLine(me));
    }

    /** runs/c3/ant-storm-default T11: Chrome Mox with Brainstorm imprinted makes blue, the menu offered Ponder {U} as
     *  castable, and the line said {B:1}: a reflected or chosen colour was read as no colour. */
    @Test
    public void aReflectedColourIsCounted() {
        Game g = LineFindingProbeTest.bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        LineFindingProbeTest.card("Swamp", me, ZoneType.Battlefield);
        forge.game.card.Card mox = LineFindingProbeTest.card("Chrome Mox", me, ZoneType.Battlefield);
        mox.addImprintedCard(LineFindingProbeTest.card("Brainstorm", me, ZoneType.Exile));
        String line = PlayerControllerLLM.manaAvailable(me);
        Assert.assertTrue(line.startsWith("{U:1 B:1} up to 2 mana (2 untapped sources"), line);
    }

    /** A spell on the stack is said to be waiting to resolve, not "pending": with its own Duress on the stack,
     *  Luna read "1 pending" as a Duress choice it was owed and reported the menu as wrong (C3 ANT, T3). */
    @Test
    public void aSpellOnTheStackIsSaidToBeUnresolved() {
        Game g = LineFindingProbeTest.bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        g.getPhaseHandler().devModeSet(PhaseType.MAIN1, rival);
        forge.game.card.Card duress = card("Duress", rival, ZoneType.Hand);
        forge.game.spellability.SpellAbility sa = duress.getFirstSpellAbility();
        sa.setActivatingPlayer(rival);
        sa.getTargets().add(me);
        sa.setHostCard(g.getAction().moveToStack(duress, sa));
        g.getStack().add(sa);
        String r = render(observerFor(g, 0), "");
        int at = r.indexOf("STACK (engine, top first): ");
        Assert.assertTrue(at >= 0, r);
        String line = r.substring(at, r.indexOf('\n', at));
        Assert.assertTrue(line.startsWith("STACK (engine, top first): 1 not yet resolved (it resolves when both players"
                + " pass in a row): #1 spell Duress"), line);
        Assert.assertFalse(line.contains("pending"), line);
    }

    /** Every kind of public game state the engine keeps is in the seat's state: counters, damage, attachments,
     *  loyalty, tapped, face-down, phased out, poison, energy, monarch, initiative, city's blessing, day/night. */
    @Test
    public void everyPublicStateFacetIsShown() {
        Game g = LineFindingProbeTest.bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        forge.game.card.Card bears = LineFindingProbeTest.card("Grizzly Bears", me, ZoneType.Battlefield);
        bears.addCounterInternal(forge.game.card.CounterEnumType.P1P1, 2, me, false, null, null);
        bears.setDamage(1);
        forge.game.card.Card aura = LineFindingProbeTest.card("Rancor", me, ZoneType.Battlefield);
        aura.attachToEntity(bears, null, true);
        forge.game.card.Card sword = LineFindingProbeTest.card("Bonesplitter", me, ZoneType.Battlefield);
        sword.attachToEntity(bears, null, true);
        forge.game.card.Card walker = LineFindingProbeTest.card("Jace, the Mind Sculptor", rival, ZoneType.Battlefield);
        walker.addCounterInternal(forge.game.card.CounterEnumType.LOYALTY, 3, rival, false, null, null);
        forge.game.card.Card phased = LineFindingProbeTest.card("Llanowar Elves", rival, ZoneType.Battlefield);
        phased.setPhasedOut(rival);
        forge.game.card.Card morph = LineFindingProbeTest.card("Willbender", rival, ZoneType.Battlefield);
        morph.turnFaceDown(true);
        forge.game.card.Card tapped = LineFindingProbeTest.card("Craw Wurm", rival, ZoneType.Battlefield);
        tapped.setTapped(true);
        forge.game.GameEntityCounterTable table = new forge.game.GameEntityCounterTable();
        me.addPoisonCounters(3, rival, table);
        table.replaceCounterEffect(g, null);
        g.getAction().checkStaticAbilities();
        me.addCounterInternal(forge.game.card.CounterEnumType.ENERGY, 4, me, false, null, null);
        g.setMonarch(me);
        g.setHasInitiative(rival);
        g.setDayTime(Boolean.FALSE);   // Forge: false is day, true is night
        rival.setBlessing(true, null);
        String r = SeatObservationTest.render(SeatObservationTest.observerFor(g, 0), "");
        String head = r.substring(0, r.indexOf('\n'));
        String you = r.substring(r.indexOf("YOU ["), r.indexOf('\n', r.indexOf("YOU [")));
        String opp = r.substring(r.indexOf("OPP ["), r.indexOf("OPPONENT CARDS") < 0 ? r.indexOf("STACK") : r.indexOf("OPPONENT CARDS"));
        Assert.assertTrue(head.contains("it is day"), head);
        Assert.assertTrue(you.contains("poison 3/10") && you.contains("energy 4") && you.contains("monarch"), you);
        Assert.assertTrue(opp.contains("has the initiative") && opp.contains("has the city's blessing"), opp);
        Assert.assertTrue(opp.contains("Llanowar Elves") && opp.contains("[phased out]"), "a phased-out permanent is shown: " + opp);
        Assert.assertTrue(r.contains("[+1/+1 x2]") && r.contains("[damage 1]") && r.contains("[loyalty 3]") && r.contains("[tapped]")
                && r.contains("attached to Grizzly Bears") && r.contains("face-down Creature 2/2"), r);
    }

    /** What a permanent remembers is shown: Pithing Needle's named card, Cavern's type, Chrome Mox's imprint, a token. */
    @Test
    public void whatAPermanentRemembersIsShown() {
        Game g = LineFindingProbeTest.bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        forge.game.card.Card needle = LineFindingProbeTest.card("Pithing Needle", rival, ZoneType.Battlefield);
        needle.addNamedCard("Jace, the Mind Sculptor");
        forge.game.card.Card cavern = LineFindingProbeTest.card("Cavern of Souls", me, ZoneType.Battlefield);
        cavern.setChosenType("Human");
        forge.game.card.Card mox = LineFindingProbeTest.card("Chrome Mox", me, ZoneType.Battlefield);
        mox.addImprintedCard(LineFindingProbeTest.card("Brainstorm", me, ZoneType.Exile));
        forge.game.card.Card sick = LineFindingProbeTest.card("Grizzly Bears", me, ZoneType.Battlefield);
        sick.setSickness(true);
        forge.game.card.Card tok = LineFindingProbeTest.card("Grizzly Bears", rival, ZoneType.Battlefield);
        tok.setGamePieceType(forge.card.GamePieceType.TOKEN);
        g.getAction().checkStaticAbilities();
        String r = SeatObservationTest.render(SeatObservationTest.observerFor(g, 0), "");
        Assert.assertTrue(r.contains("Pithing Needle [untapped] [named: Jace, the Mind Sculptor]"), r);
        Assert.assertTrue(r.contains("[chosen type: Human]"), r);
        Assert.assertTrue(r.contains("Chrome Mox [untapped] [imprinted: Brainstorm @"), r);
        Assert.assertTrue(r.contains("Grizzly Bears 2/2 [untapped] [token]"), r);
    }

    /** A card waiting outside the battlefield shows how: a suspended card its time counters. */
    @Test
    public void aSuspendedCardShowsItsTimeCounters() {
        Game g = LineFindingProbeTest.bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        forge.game.card.Card av = LineFindingProbeTest.card("Ancestral Vision", me, ZoneType.Exile);
        av.addCounterInternal(forge.game.card.CounterEnumType.TIME, 3, me, false, null, null);
        String r = SeatObservationTest.render(SeatObservationTest.observerFor(g, 0), "");
        int at = r.indexOf("Ancestral Vision");
        String line = r.substring(at, r.indexOf('\n', at));
        Assert.assertTrue(line.contains("[suspended]") && line.toLowerCase().matches(".*\\[time[^\\]]*3\\].*"), line);
    }

    /** Mana only for some spells is kept apart, in the card's words: two Great Halls make {C:2} for anything;
     *  their any colour is for instants and sorceries only (std-g10 showed it as five colours for anything). */
    @Test
    public void restrictedManaIsKeptApart() {
        Game g = LineFindingProbeTest.bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        LineFindingProbeTest.card("Great Hall of the Biblioplex", me, ZoneType.Battlefield);
        LineFindingProbeTest.card("Great Hall of the Biblioplex", me, ZoneType.Battlefield);
        String line = PlayerControllerLLM.manaAvailable(me);
        Assert.assertTrue(line.startsWith("{C:2} up to 2 mana (2 untapped sources; also mana only for some spells, not counted"
                + " above: Great Hall of the Biblioplex x2: "), line);
        Assert.assertTrue(line.contains("instant or sorcery"), line);
    }

    /** A filter adds no mana, but its colours count: Sunken Ruins makes {C}, or turns one mana into U/B mana. */
    @Test
    public void aFilterCountsItsColoursButNotExtraMana() {
        Game g = LineFindingProbeTest.bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        LineFindingProbeTest.card("Sunken Ruins", me, ZoneType.Battlefield);
        Assert.assertEquals(PlayerControllerLLM.manaAvailable(me), "{U:1 B:1 C:1} up to 1 mana (1 untapped sources)");
    }

    /** A source the engine cannot activate now makes no mana: a tapped land, a summoning-sick creature. */
    @Test
    public void aSourceThatCannotBeActivatedMakesNoMana() {
        Game g = LineFindingProbeTest.bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        LineFindingProbeTest.card("Forest", me, ZoneType.Battlefield);
        LineFindingProbeTest.card("Mountain", me, ZoneType.Battlefield).setTapped(true);
        LineFindingProbeTest.card("Llanowar Elves", me, ZoneType.Battlefield).setSickness(true);
        Assert.assertEquals(PlayerControllerLLM.manaAvailable(me), "{G:1} up to 1 mana (1 untapped sources)");
        int[] t = PlayerControllerLLM.untappedManaTally(me);
        Assert.assertEquals(t[6], 1);
        Assert.assertEquals(t[7], 1);
    }

    /** The hand tag names a total shortfall the colours do not explain: two Forests for Craw Wurm ({4}{G}{G}). */
    @Test
    public void theHandTagNamesATotalShortfall() {
        Game g = LineFindingProbeTest.bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        LineFindingProbeTest.card("Forest", me, ZoneType.Battlefield);
        LineFindingProbeTest.card("Forest", me, ZoneType.Battlefield);
        Card wurm = LineFindingProbeTest.card("Craw Wurm", me, ZoneType.Hand);
        Assert.assertEquals(PlayerControllerLLM.affordability(me, wurm, PlayerControllerLLM.untappedManaTally(me)),
                "(need 4 more mana)");
    }

    /** The "may shuffle" after a Ponder order states the order it would undo, known from the ability (MayShuffle),
     *  not from the engine message's wording: a message that never says "shuffle" still gets the note. */
    @Test
    public void theShuffleAfterAnOrderIsKnownFromTheAbility() {
        PlayerControllerLLM.TEST_PROFILE_OVERRIDE = ContextProfile.V4_FAITHFUL;
        try {
            Game g = LineFindingProbeTest.bareGame(PhaseType.MAIN1);
            Player me = g.getPlayers().get(0);
            Card ponder = LineFindingProbeTest.card("Ponder", me, ZoneType.Hand);
            CardCollection top = new CardCollection();
            for (String n : new String[]{"Island", "Forest", "Mountain"}) top.add(LineFindingProbeTest.card(n, me, ZoneType.Library));
            LineFindingProbeTest.Recorder r = new LineFindingProbeTest.Recorder("PICKS: 1, 2, 3");
            PlayerControllerLLM ctrl = new PlayerControllerLLM(g, me, new LobbyPlayerAi("Seat", null), "Seat", "", r, r, "llm");
            me.dangerouslySetController(ctrl);
            forge.game.spellability.SpellAbility sa = ponder.getFirstSpellAbility();
            sa.setActivatingPlayer(me);
            ctrl.orderMoveToZoneList(top, ZoneType.Library, sa);
            ctrl.confirmAction(sa, null, "Bibliothek mischen?", null, null, null);
            String last = r.prompts.get(r.prompts.size() - 1);
            Assert.assertTrue(last.contains("You just put these on top of that library, top first"), last);
        } finally {
            PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null;
        }
    }
}
