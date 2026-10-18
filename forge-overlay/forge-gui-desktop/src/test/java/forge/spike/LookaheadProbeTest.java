package forge.spike;

import org.testng.Assert;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.AfterClass;
import org.testng.annotations.Test;

import forge.GuiDesktop;
import forge.game.Game;
import forge.game.player.Player;
import forge.gui.GuiBase;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;

/**
 * Tests for the G8 lookahead primitive ({@link PlayerLookahead}) and the copy mechanism it rests on.
 * The safety properties are pinned here: a preview must not consume the live RNG stream, must not
 * mutate live state, and must be deterministic for a fixed seed.
 */
public class LookaheadProbeTest {
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
    public void clear() { }

    // ---- helpers ------------------------------------------------------------------------------

    private static String libNames(Player p) {
        StringBuilder b = new StringBuilder();
        for (forge.game.card.Card c : p.getCardsIn(forge.game.zone.ZoneType.Library)) {
            if (b.length() > 0) b.append(',');
            b.append(c.getName());
        }
        return b.toString();
    }

    private static Game gameWithLibrary() {
        Game g = SeatObservationTest.bareGame();
        Player me = g.getPlayers().get(0);
        for (String n : new String[] {"Island", "Forest", "Mountain", "Swamp", "Plains", "Island", "Forest"})
            SeatObservationTest.card(n, me, forge.game.zone.ZoneType.Library);
        return g;
    }

    private static forge.game.spellability.SpellAbility spellOn(forge.game.card.Card c) {
        for (forge.game.spellability.SpellAbility s : c.getSpellAbilities()) if (s.isSpell()) return s;
        return null;
    }

    // ---- copy mechanism -----------------------------------------------------------------------

    /** runs/loop/g16 T8 (2026-10-18): two `simulate` calls failed with "Couldn't map Enroot (151)". Carnivorous
     *  Cultivator enters prepared: an effect remembers a copy of the card in its prepared-spell state that is in no
     *  zone the copier copies, so GameCopier.find throws. The preview parks such remembered cards for the copy and
     *  puts every remembered list back exactly. */
    @Test
    public void aPreparedCardsRememberedSpellDoesNotBreakThePreview() {
        Game g = SeatObservationTest.bareGame();
        forge.game.player.Player me = g.getPlayers().get(0);
        // Entering from hand runs the card's "enters prepared" replacement, as casting it did in g16.
        forge.game.card.Card inHand = SeatObservationTest.card("Carnivorous Cultivator", me, forge.game.zone.ZoneType.Hand);
        forge.game.card.Card cult = g.getAction().moveTo(forge.game.zone.ZoneType.Battlefield, inHand, null, null);
        Assert.assertTrue(cult.isPrepared(), "the fixture prepared the card");
        // g16's state: the effect remembers the prepared spell (Enroot), whose zone is set, but no copied zone holds
        // it. Entering prepared here leaves Enroot in exile, so the fixture takes it out of the exile list the way the
        // game had it; the route the game took to that state is not reproduced yet.
        forge.game.card.Card enroot = null;
        for (forge.game.card.Card c : g.getCardsIn(forge.game.zone.ZoneType.Command))
            for (Object o : c.getRemembered()) if (o instanceof forge.game.card.Card) enroot = (forge.game.card.Card) o;
        Assert.assertNotNull(enroot, "the prepared effect remembers its spell");
        enroot.getZone().remove(enroot);
        Assert.assertNotNull(enroot.getZone(), "its zone is still set");
        try {
            new forge.ai.simulation.GameCopier(g).makeCopy();
            Assert.fail("the engine copier now maps a remembered card no copied zone holds: remove the workaround");
        } catch (RuntimeException e) {
            Assert.assertTrue(String.valueOf(e.getMessage()).contains("Couldn't map"), String.valueOf(e));
        }
        java.util.Map<forge.game.card.Card, java.util.List<Object>> before = new java.util.HashMap<>();
        for (forge.game.card.Card c : g.getCardsInGame()) before.put(c, PlayerLookahead.UncopiedRemembered.rememberedOf(c));
        PlayerLookahead.UncopiedEffects effects = PlayerLookahead.UncopiedEffects.park(g);
        PlayerLookahead.UncopiedRemembered parked = PlayerLookahead.UncopiedRemembered.park(g);
        Assert.assertTrue(parked.parked > 0, "the prepared spell was parked");
        try {
            Assert.assertNotNull(new forge.ai.simulation.GameCopier(g).makeCopy());
        } finally {
            parked.restore();
            effects.restore();
        }
        for (forge.game.card.Card c : g.getCardsInGame())
            Assert.assertEquals(PlayerLookahead.UncopiedRemembered.rememberedOf(c), before.get(c), "remembered restored on " + c);
    }

    @Test
    public void gameCopierBehaviorProbe() {
        Game g = SeatObservationTest.bareGame();
        Game copy = new forge.ai.simulation.GameCopier(g).makeCopy();
        Assert.assertNotNull(copy);
        Assert.assertNotSame(copy, g, "GameCopier returned the live game itself");
        Assert.assertNotSame(copy.getPlayers().get(0), g.getPlayers().get(0),
                "copy aliases a live player -- simulation would mutate the real game");
    }

    /** The safety property: a preview must not consume the live RNG stream or mutate live state. */
    @Test
    public void copyForPreviewDoesNotTouchLiveRngOrState() {
        Game g = gameWithLibrary();
        Player me = g.getPlayers().get(0);
        int lifeBefore = me.getLife();
        String libBefore = libNames(me);

        forge.util.MyRandom.setRandom(new java.util.Random(42));
        PlayerLookahead.copyForPreview(g, 1234L);
        int afterPreview = forge.util.MyRandom.getRandom().nextInt();

        forge.util.MyRandom.setRandom(new java.util.Random(42));
        int noPreview = forge.util.MyRandom.getRandom().nextInt();

        Assert.assertEquals(afterPreview, noPreview, "preview consumed the LIVE RNG stream");
        Assert.assertEquals(me.getLife(), lifeBefore, "preview mutated live life total");
        Assert.assertEquals(libNames(me), libBefore, "preview shuffled the LIVE library");
    }

    @Test
    public void copyForPreviewIsDistinctAndDeterministic() {
        Game g = gameWithLibrary();
        Game c1 = PlayerLookahead.copyForPreview(g, 7L);
        Game c2 = PlayerLookahead.copyForPreview(g, 7L);
        Assert.assertNotNull(c1);
        Assert.assertNotSame(c1, g);
        Assert.assertNotSame(c1.getPlayers().get(0), g.getPlayers().get(0), "copy aliases a live player");
        Assert.assertEquals(libNames(c1.getPlayers().get(0)), libNames(c2.getPlayers().get(0)),
                "determinization is not deterministic for a fixed seed");
        Assert.assertEquals(PlayerLookahead.publicDelta(g.getPlayers().get(0), g.getPlayers().get(0)), "no change");
    }

    // ---- the preview --------------------------------------------------------------------------

    /** A non-targeting spell resolves on the copy and its public delta is reported, live untouched. */
    @Test
    public void previewCastResolvesANonTargetingSpellWithoutTouchingTheLiveGame() {
        Game g = SeatObservationTest.bareGame();
        Player me = g.getPlayers().get(0), opp = g.getPlayers().get(1);
        for (int i = 0; i < 2; i++) SeatObservationTest.card("Mountain", me, forge.game.zone.ZoneType.Battlefield);
        forge.game.card.Card pyro = SeatObservationTest.card("Pyroclasm", me, forge.game.zone.ZoneType.Hand);
        forge.game.card.Card theirs = SeatObservationTest.card("Elite Vanguard", opp, forge.game.zone.ZoneType.Battlefield);
        forge.game.spellability.SpellAbility sa = spellOn(pyro);
        Assert.assertNotNull(sa, "fixture: no spell ability on Pyroclasm");
        Assert.assertFalse(sa.usesTargeting(), "fixture: Pyroclasm should not target");

        String report = PlayerLookahead.previewCast(g, me, sa, 5L);
        System.out.println("[LOOKAHEAD] " + report);
        Assert.assertTrue(report.contains("Pyroclasm"), report);
        Assert.assertTrue(report.contains("board 1 -> 0"), "the doomed creature was not reported: " + report);
        // The LIVE game is untouched: the opponent's creature is still there and still alive.
        Assert.assertEquals(opp.getCardsIn(forge.game.zone.ZoneType.Battlefield).size(), 1);
        Assert.assertTrue(theirs.isInZone(forge.game.zone.ZoneType.Battlefield));
    }

    /** runs/compare: after Palace Jailer's monarchy changed hands, every preview failed with
     *  "Couldn't map The Monarch" (the engine leaves the old monarch's effect card a stale zone). */
    @Test
    public void aPreviewWorksAfterTheMonarchyChangedHands() {
        Game g = SeatObservationTest.bareGame();
        Player me = g.getPlayers().get(0), opp = g.getPlayers().get(1);
        for (int i = 0; i < 2; i++) SeatObservationTest.card("Mountain", me, forge.game.zone.ZoneType.Battlefield);
        forge.game.card.Card pyro = SeatObservationTest.card("Pyroclasm", me, forge.game.zone.ZoneType.Hand);
        SeatObservationTest.card("Elite Vanguard", opp, forge.game.zone.ZoneType.Battlefield);
        me.createMonarchEffect("CN2");
        me.removeMonarchEffect();
        opp.createMonarchEffect("CN2");
        String report = PlayerLookahead.previewCast(g, me, spellOn(pyro), 5L);
        Assert.assertFalse(report.contains("preview failed"), report);
        Assert.assertTrue(report.contains("board 1 -> 0"), report);
        PlayerLookahead.StaleEffectCards again = PlayerLookahead.StaleEffectCards.park(g);
        Assert.assertEquals(again.size(), 1, "the live stale pointer was put back after the preview");
        again.restore();
    }

    /**
     * The defect behind every failed preview of the Mardu games (runs/lines/ctl/native-lookahead: 16
     * of 16 spell previews; 104 of 104 on loopback): a characteristic-defining card in a SIDEBOARD
     * has a static effect the engine's copier cannot map, because it does not copy that zone.
     */
    @Test
    public void aSideboardCardWithACharacteristicDefiningAbilityDoesNotBreakThePreview() throws Exception {
        Game g = SeatObservationTest.bareGame();
        Player me = g.getPlayers().get(0), opp = g.getPlayers().get(1);
        for (int i = 0; i < 2; i++) SeatObservationTest.card("Mountain", me, forge.game.zone.ZoneType.Battlefield);
        forge.game.card.Card pyro = SeatObservationTest.card("Pyroclasm", me, forge.game.zone.ZoneType.Hand);
        SeatObservationTest.card("Elite Vanguard", opp, forge.game.zone.ZoneType.Battlefield);
        SeatObservationTest.card("Barrowgoyf", opp, forge.game.zone.ZoneType.Sideboard);
        SeatObservationTest.card("Barrowgoyf", me, forge.game.zone.ZoneType.Sideboard);
        g.getAction().checkStaticAbilities();

        // The fixture reproduces the engine defect. If this stops throwing, the engine was fixed and
        // PlayerLookahead.UncopiedEffects can be deleted.
        try {
            new forge.ai.simulation.GameCopier(g).makeCopy();
            Assert.fail("the engine copier now handles a sideboard CDA card: remove the workaround");
        } catch (RuntimeException e) {
            Assert.assertTrue(String.valueOf(e.getMessage()).contains("Couldn't map Barrowgoyf"), String.valueOf(e));
        }

        java.util.Map<Object, Object> table = PlayerLookahead.UncopiedEffects.tableOf(g);
        java.util.List<Object> keysBefore = new java.util.ArrayList<>(table.keySet());
        java.util.List<Object> valuesBefore = new java.util.ArrayList<>(table.values());
        Assert.assertTrue(keysBefore.size() >= 2, "fixture: no static effects for the sideboard cards");

        String report = PlayerLookahead.previewCast(g, me, spellOn(pyro), 5L);
        Assert.assertFalse(report.contains("preview failed"), report);
        Assert.assertTrue(report.contains("board 1 -> 0"), report);
        Assert.assertNotNull(PlayerLookahead.copyForPreview(g, 5L));

        // The live table is exactly what it was: same objects, same order.
        java.util.List<Object> keysAfter = new java.util.ArrayList<>(table.keySet()), valuesAfter = new java.util.ArrayList<>(table.values());
        Assert.assertEquals(keysAfter.size(), keysBefore.size());
        for (int i = 0; i < keysBefore.size(); i++) {
            Assert.assertSame(keysAfter.get(i), keysBefore.get(i), "effect order changed at " + i);
            Assert.assertSame(valuesAfter.get(i), valuesBefore.get(i));
        }
        PlayerLookahead.UncopiedEffects parked = PlayerLookahead.UncopiedEffects.park(g);
        Assert.assertEquals(parked.parked, 2, "both sideboard effects are parked");
        Assert.assertEquals(table.size(), keysBefore.size() - 2);
        parked.restore();
        Assert.assertEquals(new java.util.ArrayList<>(table.keySet()), keysBefore);
    }

    private static forge.game.spellability.SpellAbility abilityOn(forge.game.card.Card c, Player activator) {
        for (forge.game.spellability.SpellAbility s : c.getSpellAbilities()) {
            if (s.isActivatedAbility() && !s.isManaAbility()) { s.setActivatingPlayer(activator); return s; }
        }
        return null;
    }

    private static forge.game.spellability.SpellAbility landAbility(forge.game.card.Card c, Player activator) {
        for (forge.game.spellability.SpellAbility s : c.getAllPossibleAbilities(activator, false)) {
            if (s.isLandAbility()) { s.setActivatingPlayer(activator); return s; }
        }
        return null;
    }

    /**
     * A previewed line may START with a land drop. The seat asked for exactly this and was refused:
     * runs/loop/g02 turn 6, `simulate {"option":4,"then":[3]}` on a menu whose 4 was "PLAY LAND
     * Wasteland" and 3 was Stoneforge Mystic's ability, answered "`option` must be the number of a
     * CAST or ACTIVATE option (1..3)"; 13 of the 92 preview requests in runs/lines/ctl2/native-lookahead
     * named a PLAY LAND option and all 13 were refused. The land resolves on the copy, so a later step
     * the land pays for is castable there.
     */
    @Test
    public void aLineThatStartsWithALandDropIsPreviewed() {
        Game g = SeatObservationTest.bareGame();
        Player me = g.getPlayers().get(0), opp = g.getPlayers().get(1);
        forge.game.card.Card mountain = SeatObservationTest.card("Mountain", me, forge.game.zone.ZoneType.Hand);
        forge.game.card.Card bolt = SeatObservationTest.card("Lightning Bolt", me, forge.game.zone.ZoneType.Hand);
        forge.game.spellability.SpellAbility drop = landAbility(mountain, me);
        Assert.assertNotNull(drop, "fixture: no land-play ability on Mountain");
        forge.game.spellability.SpellAbility s = spellOn(bolt);
        Assert.assertNotNull(s);
        s.setActivatingPlayer(me);   // as a real menu option carries it
        int life = opp.getLife();

        String report = PlayerLookahead.previewLine(g, me, java.util.Arrays.asList(drop, s), 5L, null);
        System.out.println("[LOOKAHEAD] " + report);
        Assert.assertTrue(report.startsWith("if you play Mountain"), report);
        Assert.assertTrue(report.contains("entered the battlefield: Mountain"), report);
        Assert.assertTrue(report.contains("then cast Lightning Bolt"), report);
        Assert.assertTrue(report.contains("life " + life + " -> " + (life - 3)), report);
        // Live game untouched: the land is still in hand, nobody lost life, the ability has no target left.
        Assert.assertTrue(mountain.isInZone(forge.game.zone.ZoneType.Hand));
        Assert.assertEquals(opp.getLife(), life);
        Assert.assertTrue(s.getTargets() == null || s.getTargets().isEmpty());
    }

    /**
     * v2: an activated ability is previewed. In runs/lines/ctl/native-lookahead 19 of the seat's 38
     * preview requests were for one (Goblin Bombardment, a planeswalker) and every one was refused.
     * The report names what left the battlefield, because "board 3 -> 2" hides the whole point.
     */
    @Test
    public void anActivatedAbilityWithASacrificeCostIsPreviewed() {
        Game g = SeatObservationTest.bareGame();
        Player me = g.getPlayers().get(0), opp = g.getPlayers().get(1);
        forge.game.card.Card bombardment = SeatObservationTest.card("Goblin Bombardment", me, forge.game.zone.ZoneType.Battlefield);
        forge.game.card.Card fodder = SeatObservationTest.card("Elite Vanguard", me, forge.game.zone.ZoneType.Battlefield);
        forge.game.spellability.SpellAbility sa = abilityOn(bombardment, me);
        Assert.assertNotNull(sa, "fixture: no activated ability on Goblin Bombardment");
        int lifeBefore = opp.getLife();

        String report = PlayerLookahead.previewCast(g, me, sa, 5L, opp.getName());
        System.out.println("[LOOKAHEAD] " + report);
        Assert.assertTrue(report.startsWith("if you activate Goblin Bombardment"), report);
        Assert.assertTrue(report.contains("left the battlefield: Elite Vanguard"), report);
        Assert.assertTrue(report.contains("life " + lifeBefore + " -> " + (lifeBefore - 1)), report);
        Assert.assertTrue(report.contains("in the real play YOU choose"), "the engine-paid sacrifice must be flagged: " + report);
        // Live game untouched: the creature is still there, nobody lost life, the option has no targets left on it.
        Assert.assertTrue(fodder.isInZone(forge.game.zone.ZoneType.Battlefield));
        Assert.assertEquals(opp.getLife(), lifeBefore);
        Assert.assertTrue(sa.getTargets() == null || sa.getTargets().isEmpty());
    }

    @Test
    public void aTapAbilityAndAManaAbilityArePreviewed() {
        Game g = SeatObservationTest.bareGame();
        Player me = g.getPlayers().get(0), opp = g.getPlayers().get(1);
        forge.game.card.Card pinger = SeatObservationTest.card("Prodigal Pyromancer", me, forge.game.zone.ZoneType.Battlefield);
        pinger.setSickness(false);
        forge.game.card.Card theirs = SeatObservationTest.card("Elite Vanguard", opp, forge.game.zone.ZoneType.Battlefield);
        String report = PlayerLookahead.previewCast(g, me, abilityOn(pinger, me), 5L, "Elite Vanguard");
        System.out.println("[LOOKAHEAD] " + report);
        Assert.assertTrue(report.startsWith("if you activate Prodigal Pyromancer [assuming target: Elite Vanguard]"), report);
        Assert.assertTrue(report.contains("left the battlefield: Elite Vanguard"), report);
        Assert.assertFalse(report.contains("YOU choose"), "no sacrifice in this cost: " + report);
        Assert.assertTrue(theirs.isInZone(forge.game.zone.ZoneType.Battlefield));
        Assert.assertFalse(pinger.isTapped(), "the live permanent must not be tapped by a preview");

        // A mana ability is an ACTIVATE option of the menu and is previewed too: the seat asked for "cast Heritage
        // Druid, cast Heritage Druid, then tap Gaea's Cradle" and was refused the whole line (runs/loop/g10 turn 6,
        // `simulate {"option":2,"then":[4,6]}`). Its effect is the mana it adds, which the report shows.
        forge.game.card.Card forest = SeatObservationTest.card("Forest", me, forge.game.zone.ZoneType.Battlefield);
        forge.game.spellability.SpellAbility mana = null;
        for (forge.game.spellability.SpellAbility s : forest.getAllPossibleAbilities(me, false)) if (s.isManaAbility()) mana = s;
        Assert.assertNotNull(mana, "fixture: no mana ability on a Forest");
        String tapped = PlayerLookahead.previewLine(g, me, java.util.Arrays.asList(mana), 5L, null);
        Assert.assertTrue(tapped.startsWith("if you activate Forest: you mana pool empty -> {G:1}"), tapped);
        Assert.assertFalse(forest.isTapped(), "the live Forest must not be tapped by a preview");
        Assert.assertTrue(me.getManaPool().isEmpty(), "the live pool must not be filled by a preview");
    }

    /** A line: two plays on one copy. With mana for both, both happen; with mana for one, the preview
     *  says the second could not be paid for, which is the sequencing answer the seat is asking for. */
    @Test
    public void aLineOfTwoPlaysIsPreviewedOnOneCopyAndStopsWhereTheManaRunsOut() {
        for (int lands = 1; lands <= 2; lands++) {
            Game g = SeatObservationTest.bareGame();
            Player me = g.getPlayers().get(0), opp = g.getPlayers().get(1);
            for (int i = 0; i < lands; i++) SeatObservationTest.card("Mountain", me, forge.game.zone.ZoneType.Battlefield);
            forge.game.card.Card b1 = SeatObservationTest.card("Lightning Bolt", me, forge.game.zone.ZoneType.Hand);
            forge.game.card.Card b2 = SeatObservationTest.card("Lightning Bolt", me, forge.game.zone.ZoneType.Hand);
            int life = opp.getLife();
            forge.game.spellability.SpellAbility s1 = spellOn(b1), s2 = spellOn(b2);
            s1.setActivatingPlayer(me);   // as a real menu option carries it
            s2.setActivatingPlayer(me);
            String report = PlayerLookahead.previewLine(g, me, java.util.Arrays.asList(s1, s2), 5L, opp.getName());
            System.out.println("[LOOKAHEAD] lands=" + lands + ": " + report);
            if (lands == 2) {
                Assert.assertTrue(report.startsWith("if you cast Lightning Bolt [assuming target: " + opp.getName() + "], then cast Lightning Bolt"), report);
                Assert.assertTrue(report.contains("life " + life + " -> " + (life - 6)), report);
                Assert.assertFalse(report.contains("could NOT"), report);
            } else {
                Assert.assertTrue(report.contains("life " + life + " -> " + (life - 3)), report);
                Assert.assertTrue(report.contains("could NOT pay for or play Lightning Bolt"), report);
                Assert.assertTrue(report.contains("hand 2 -> 1"), "the unpaid second spell must not be reported as gone from hand: " + report);
            }
            Assert.assertEquals(opp.getLife(), life, "the live game is untouched");
            Assert.assertEquals(me.getCardsIn(forge.game.zone.ZoneType.Hand).size(), 2);
            for (forge.game.card.Card c : me.getCardsIn(forge.game.zone.ZoneType.Battlefield)) Assert.assertFalse(c.isTapped());
        }
        Game g = SeatObservationTest.bareGame();
        Player me = g.getPlayers().get(0);
        forge.game.spellability.SpellAbility s = spellOn(SeatObservationTest.card("Lightning Bolt", me, forge.game.zone.ZoneType.Hand));
        // No length cap any more (it was 3): a long line is refused only for naming an option twice.
        Assert.assertTrue(PlayerLookahead.previewLine(g, me, java.util.Arrays.asList(s, s, s, s), 5L, null).startsWith("(a line names each option once"));
        // Seen on loopback: the same option twice was reported as two casts with one card's worth of change.
        Assert.assertTrue(PlayerLookahead.previewLine(g, me, java.util.Arrays.asList(s, s), 5L, null).startsWith("(a line names each option once"));
    }

    /**
     * Every player the engine creates during a preview is a player of a copy. Watching one game
     * (runs/loop/g01) showed the seat's model being asked to declare attackers and confirm triggers
     * inside the combat the engine simulates to score a copy: real model calls, real decision rows,
     * for a game that never happened. A lobby that records what it is asked stands in for the seat.
     */
    @Test
    public void everyPlayerCreatedDuringAPreviewIsCreatedInsideThePreview() {
        java.util.List<Boolean> seen = new java.util.ArrayList<>();
        forge.ai.LobbyPlayerAi watching = new forge.ai.LobbyPlayerAi("Seat", null) {
            @Override public Player createIngamePlayer(Game game, int id) {
                seen.add(PlayerLookahead.inPreview());
                return super.createIngamePlayer(game, id);
            }
        };
        java.util.List<forge.game.player.RegisteredPlayer> players = new java.util.ArrayList<>();
        forge.deck.Deck d = new forge.deck.Deck();
        players.add(new forge.game.player.RegisteredPlayer(d).setPlayer(watching));
        players.add(new forge.game.player.RegisteredPlayer(d).setPlayer(new forge.ai.LobbyPlayerAi("Rival", null)));
        forge.game.GameRules rules = new forge.game.GameRules(forge.game.GameType.Constructed);
        Game g = new Game(players, rules, new forge.game.Match(rules, players, "preview-players"));
        g.setAge(forge.game.GameStage.Play);
        g.getPhaseHandler().devModeSet(forge.game.phase.PhaseType.MAIN1, g.getPlayers().get(0));
        Player me = g.getPlayers().get(0);
        seen.clear();                                   // the live game's own player was created outside
        Assert.assertFalse(PlayerLookahead.inPreview());
        for (int i = 0; i < 2; i++) SeatObservationTest.card("Mountain", me, forge.game.zone.ZoneType.Battlefield);
        forge.game.card.Card attacker = SeatObservationTest.card("Elite Vanguard", me, forge.game.zone.ZoneType.Battlefield);
        attacker.setSickness(false);                    // a creature that can attack: the scoring simulates combat
        forge.game.spellability.SpellAbility bolt = spellOn(SeatObservationTest.card("Lightning Bolt", me, forge.game.zone.ZoneType.Hand));
        bolt.setActivatingPlayer(me);

        String report = PlayerLookahead.previewCast(g, me, bolt, 5L);
        Assert.assertTrue(report.startsWith("if you cast Lightning Bolt"), report);
        Assert.assertTrue(seen.size() >= 2, "expected the copy and the combat copy to create the seat, saw " + seen.size());
        Assert.assertFalse(seen.contains(Boolean.FALSE), "a copy's player was created outside the preview guard: " + seen);
        Assert.assertFalse(PlayerLookahead.inPreview(), "the guard must be released after the preview");
        PlayerLookahead.copyForPreview(g, 5L);
        Assert.assertFalse(seen.contains(Boolean.FALSE));
        Assert.assertFalse(PlayerLookahead.inPreview());
    }

    /** A targeting spell is previewed against a first-legal target, and the live option is restored. */
    @Test
    public void previewCastResolvesATargetingSpellAndRestoresTheLiveOption() {
        Game g = SeatObservationTest.bareGame();
        Player me = g.getPlayers().get(0), opp = g.getPlayers().get(1);
        for (int i = 0; i < 3; i++) SeatObservationTest.card("Mountain", me, forge.game.zone.ZoneType.Battlefield);
        forge.game.card.Card bolt = SeatObservationTest.card("Lightning Bolt", me, forge.game.zone.ZoneType.Hand);
        forge.game.spellability.SpellAbility sa = spellOn(bolt);
        Assert.assertNotNull(sa);
        sa.setActivatingPlayer(me); // no target, as in a real decision before the model commits
        boolean targetsEmptyBefore = sa.getTargets() == null || sa.getTargets().isEmpty();
        Assert.assertTrue(targetsEmptyBefore, "fixture: the option should start untargeted");

        String report = PlayerLookahead.previewCast(g, me, sa, 5L);
        System.out.println("[LOOKAHEAD] " + report);
        Assert.assertTrue(report.contains("Lightning Bolt"), report);
        Assert.assertTrue(report.contains("assuming target:"), "target assumption not disclosed: " + report);
        Assert.assertTrue(report.contains("Rival life 20 -> 17"), "the 3 damage at the opponent was not previewed: " + report);
        Assert.assertFalse(report.contains("you life 20 -> 17"), "the preview aimed the bolt at us: " + report);
        // LIVE state untouched: opponent still at 20, and the option's own targets restored (empty) so
        // the real cast's ensureTargetsSet is free to choose normally.
        Assert.assertEquals(opp.getLife(), 20, "preview mutated the LIVE opponent's life");
        Assert.assertTrue(sa.getTargets() == null || sa.getTargets().isEmpty(),
                "preview left a target on the LIVE option");
    }

    /** A preview aims every targeting ability of the chain, and restored only the first one's targets: Arc Trail's
     *  "1 damage to any other target" kept the preview's pick, and the real cast's ensureTargetsSet skips an ability
     *  that is already targeted, so the seat was never asked for it (found reading PlayerLookahead, 2026-10-17). */
    @Test
    public void aPreviewLeavesNoTargetOnAnyAbilityOfTheChain() {
        Game g = SeatObservationTest.bareGame();
        Player me = g.getPlayers().get(0);
        for (int i = 0; i < 2; i++) SeatObservationTest.card("Mountain", me, forge.game.zone.ZoneType.Battlefield);
        SeatObservationTest.card("Grizzly Bears", g.getPlayers().get(1), forge.game.zone.ZoneType.Battlefield);
        forge.game.spellability.SpellAbility sa = spellOn(SeatObservationTest.card("Arc Trail", me, forge.game.zone.ZoneType.Hand));
        sa.setActivatingPlayer(me);
        String report = PlayerLookahead.previewCast(g, me, sa, 5L);
        Assert.assertTrue(report.contains("Arc Trail"), report);
        for (forge.game.spellability.SpellAbility s = sa; s != null; s = s.getSubAbility())
            Assert.assertTrue(s.getTargets() == null || s.getTargets().isEmpty(),
                    "the preview left a target on " + s + ": " + s.getTargets());
    }

    /** A target choice of a play can be previewed: runs/loop/g03 T8 asked `simulate` twice while aiming Wasteland, and
     *  was refused. The model previews Lightning Bolt at the Bears from the target question, then aims it there. */
    @Test
    public void aTargetChoiceOfAPlayCanBePreviewed() {
        Game g = LineFindingProbeTest.bareGame(forge.game.phase.PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        SeatObservationTest.card("Mountain", me, forge.game.zone.ZoneType.Battlefield);
        forge.game.card.Card bears = SeatObservationTest.card("Grizzly Bears", g.getPlayers().get(1), forge.game.zone.ZoneType.Battlefield);
        SeatObservationTest.card("Lightning Bolt", me, forge.game.zone.ZoneType.Hand);
        java.util.List<String> results = new java.util.ArrayList<>();
        LineFindingProbeTest.Recorder model = new LineFindingProbeTest.Recorder("CHOICE: 1") {
            int seq;
            @Override public boolean supportsNativeTools() { return true; }
            @Override public NativeTools.Turn chat(String messagesJson, String toolsJson, boolean allowCalls) {
                com.google.gson.JsonArray msgs = com.google.gson.JsonParser.parseString(messagesJson).getAsJsonArray();
                String prompt = msgs.get(0).getAsJsonObject().get("content").getAsString();
                com.google.gson.JsonObject last = msgs.get(msgs.size() - 1).getAsJsonObject();
                if ("tool".equals(last.get("role").getAsString())) results.add(last.get("content").getAsString());
                boolean target = prompt.contains("Choose the TARGET");
                String option = numberOf(prompt, target ? "Grizzly Bears" : "CAST Lightning Bolt");
                return target && msgs.size() == 1 ? call(NativeTools.SIMULATE, "{\"option\":" + option + "}")
                        : call(AnswerTool.NAME, "{\"option\":" + option + "}");
            }
            private NativeTools.Turn call(String name, String args) {
                NativeTools.Call c = new NativeTools.Call("c" + (++seq), name, args);
                com.google.gson.JsonObject f = new com.google.gson.JsonObject();
                f.addProperty("name", name);
                f.addProperty("arguments", args);
                com.google.gson.JsonObject tc = new com.google.gson.JsonObject();
                tc.addProperty("id", c.id);
                tc.addProperty("type", "function");
                tc.add("function", f);
                com.google.gson.JsonArray tcs = new com.google.gson.JsonArray();
                tcs.add(tc);
                com.google.gson.JsonObject msg = new com.google.gson.JsonObject();
                msg.addProperty("role", "assistant");
                msg.add("tool_calls", tcs);
                return new NativeTools.Turn(null, msg.toString(), new java.util.ArrayList<>(java.util.List.of(c)));
            }
            private String numberOf(String prompt, String label) {
                java.util.regex.Matcher m = java.util.regex.Pattern.compile("(?m)^\\s*(\\d+)\\) " + java.util.regex.Pattern.quote(label)).matcher(prompt);
                String n = "1";
                while (m.find()) n = m.group(1);
                return n;
            }
        };
        LlmSpikeGui.PlayerControllerLLM.TEST_LOOKAHEAD_OVERRIDE = Boolean.TRUE;
        try {
            LlmSpikeGui.PlayerControllerLLM ctrl = LineFindingProbeTest.seat(g, model, model);
            java.util.List<forge.game.spellability.SpellAbility> chosen = ctrl.chooseSpellAbilityToPlay();
            Assert.assertNotNull(chosen, "the seat passed instead of casting");
            Assert.assertEquals(results.size(), 1, "one preview, from the target question: " + results);
            Assert.assertTrue(results.get(0).startsWith("LOOKAHEAD (target "), results.get(0));
            Assert.assertTrue(results.get(0).contains("assuming target: Grizzly Bears") && results.get(0).contains("Grizzly Bears"),
                    "the preview aims the bolt at the Bears: " + results.get(0));
            Assert.assertTrue(bears.isInZone(forge.game.zone.ZoneType.Battlefield), "the preview touched the live Bears");
            Assert.assertEquals(chosen.get(0).getTargets().getFirstTargetedCard(), bears, "the bolt is aimed where the seat said");
        } finally {
            LlmSpikeGui.PlayerControllerLLM.TEST_LOOKAHEAD_OVERRIDE = null;
        }
    }

    /** The copy kept the opponent's real hand (2026-10-16 review H2): a Show and Tell preview could name the
     *  creature the opponent would put in. Two games whose opponent holds the same cards split differently
     *  between hand and library, which the seat cannot tell apart, must preview the same. */
    @Test
    public void hiddenCardsDoNotChangeThePreview() {
        String[] reports = new String[2];
        for (int k = 0; k < 2; k++) {
            Game g = SeatObservationTest.bareGame();
            Player me = g.getPlayers().get(0), opp = g.getPlayers().get(1);
            for (int i = 0; i < 3; i++) SeatObservationTest.card("Island", me, forge.game.zone.ZoneType.Battlefield);
            forge.game.card.Card st = SeatObservationTest.card("Show and Tell", me, forge.game.zone.ZoneType.Hand);
            SeatObservationTest.card(k == 0 ? "Craw Wurm" : "Plains", opp, forge.game.zone.ZoneType.Hand);
            SeatObservationTest.card(k == 0 ? "Plains" : "Craw Wurm", opp, forge.game.zone.ZoneType.Library);
            for (int i = 0; i < 4; i++) SeatObservationTest.card("Plains", opp, forge.game.zone.ZoneType.Library);
            forge.game.spellability.SpellAbility sa = spellOn(st);
            Assert.assertNotNull(sa, "fixture: no spell ability on Show and Tell");
            sa.setActivatingPlayer(me);
            reports[k] = PlayerLookahead.previewCast(g, me, sa, 5L);
            System.out.println("[LOOKAHEAD] " + reports[k]);
            Assert.assertTrue(reports[k].contains("Show and Tell"), reports[k]);
            // The live hand is untouched by the re-deal.
            Assert.assertEquals(opp.getCardsIn(forge.game.zone.ZoneType.Hand).get(0).getName(), k == 0 ? "Craw Wurm" : "Plains");
        }
        Assert.assertEquals(reports[1], reports[0], "the preview depends on the opponent's hidden hand");
    }

    /** runs/c3/ant-storm-default T11: Chrome Mox imprinting Brainstorm pays {U} in the real game, and the preview
     *  of Ponder said the copy could not pay: the engine's copy drops imprinted cards. */
    @Test
    public void aPreviewKeepsAnImprintedMoxsColour() {
        Game g = SeatObservationTest.bareGame();
        Player me = g.getPlayers().get(0);
        forge.game.card.Card mox = SeatObservationTest.card("Chrome Mox", me, forge.game.zone.ZoneType.Battlefield);
        mox.addImprintedCard(SeatObservationTest.card("Brainstorm", me, forge.game.zone.ZoneType.Exile));
        forge.game.card.Card ponder = SeatObservationTest.card("Ponder", me, forge.game.zone.ZoneType.Hand);
        for (int i = 0; i < 4; i++) SeatObservationTest.card("Island", me, forge.game.zone.ZoneType.Library);
        forge.game.spellability.SpellAbility sa = spellOn(ponder);
        sa.setActivatingPlayer(me);
        String report = PlayerLookahead.previewCast(g, me, sa, 5L);
        System.out.println("[LOOKAHEAD] " + report);
        Assert.assertFalse(report.startsWith("(preview unavailable"), report);
        Assert.assertTrue(report.contains("Ponder"), report);
    }

    /** runs/c3/doomsday-game T8, T19: a preview of Force of Will cast for its alternative cost (exile a blue card, pay 1
     *  life) came back unavailable: the copy matched the ability by its text, found the hard cast, and could not pay
     *  {3}{U}{U}. The preview plays the same alternative cost. */
    @Test
    public void aForceOfWillPitchIsPreviewed() {
        Game g = LineFindingProbeTest.bareGame(forge.game.phase.PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        LineFindingProbeTest.card("Force of Will", me, forge.game.zone.ZoneType.Hand);
        LineFindingProbeTest.card("Brainstorm", me, forge.game.zone.ZoneType.Hand);
        LineFindingProbeTest.rivalCasts(g, "Show and Tell", null);
        g.getAction().checkStaticAbilities();
        forge.game.spellability.SpellAbility pitch = null;
        for (forge.game.spellability.SpellAbility o : LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(me, false, true).spells)
            if (o.getHostCard() != null && o.getHostCard().getName().equals("Force of Will") && o.getDescription().contains("rather than pay")) pitch = o;
        Assert.assertNotNull(pitch, "fixture: Force of Will has its pitch cost");
        pitch.setActivatingPlayer(me);
        String report = PlayerLookahead.previewCast(g, me, pitch, 5L);
        System.out.println("[LOOKAHEAD] " + report);
        Assert.assertFalse(report.startsWith("(preview unavailable"), report);
        Assert.assertTrue(report.contains("Force of Will"), report);
        // The copy holds the stack: the response is previewed against the spell it answers (the engine's copy has no
        // stack by default, so a counterspell's preview showed the opponent unchanged).
        Assert.assertTrue(report.contains("Rival graveyard 0 -> 1"), "Show and Tell was countered on the copy: " + report);
    }

    /** The model can aim the preview: a hint naming a legal target is honoured. */
    @Test
    public void previewCastHonoursATargetHint() {
        Game g = SeatObservationTest.bareGame();
        Player me = g.getPlayers().get(0), opp = g.getPlayers().get(1);
        for (int i = 0; i < 3; i++) SeatObservationTest.card("Mountain", me, forge.game.zone.ZoneType.Battlefield);
        forge.game.card.Card bolt = SeatObservationTest.card("Lightning Bolt", me, forge.game.zone.ZoneType.Hand);
        SeatObservationTest.card("Goblin Guide", opp, forge.game.zone.ZoneType.Battlefield);
        SeatObservationTest.card("Elite Vanguard", opp, forge.game.zone.ZoneType.Battlefield);
        forge.game.spellability.SpellAbility sa = spellOn(bolt);
        Assert.assertNotNull(sa);
        sa.setActivatingPlayer(me);

        String report = PlayerLookahead.previewCast(g, me, sa, 5L, "Elite Vanguard");
        System.out.println("[LOOKAHEAD] " + report);
        Assert.assertTrue(report.contains("assuming target: Elite Vanguard"), report);
        Assert.assertTrue(report.contains("board 2 -> 1"), "the named creature should die: " + report);
    }

    /** The hint the seat actually sends is the label the board printed for the target -- name, P/T,
     *  tags, handle -- not the bare name. `pickTarget` asked only whether the candidate's NAME
     *  contained the hint, and the label is longer than the name, so it never matched and the pick
     *  fell silently back to the default. runs/loop/g05 T5 (jsonl:57): the seat asked to preview
     *  Nomads en-Kor targeting "Cephalid Illusionist 1/1 [untapped] [sick] @75", and the report came
     *  back "[assuming target: Nomads en-Kor]: you no change" -- a no-op preview of the ability the
     *  whole game turned on; it then spent its one allowed retry on the same request and was refused. */
    @Test
    public void previewCastHonoursATargetHintThatQuotesThePrintedLabel() {
        Game g = SeatObservationTest.bareGame();
        Player me = g.getPlayers().get(0), opp = g.getPlayers().get(1);
        for (int i = 0; i < 3; i++) SeatObservationTest.card("Mountain", me, forge.game.zone.ZoneType.Battlefield);
        forge.game.card.Card bolt = SeatObservationTest.card("Lightning Bolt", me, forge.game.zone.ZoneType.Hand);
        SeatObservationTest.card("Goblin Guide", opp, forge.game.zone.ZoneType.Battlefield);
        forge.game.card.Card vanguard = SeatObservationTest.card("Elite Vanguard", opp, forge.game.zone.ZoneType.Battlefield);
        forge.game.spellability.SpellAbility sa = spellOn(bolt);
        Assert.assertNotNull(sa);
        sa.setActivatingPlayer(me);

        String label = vanguard.getName() + " " + vanguard.getNetPower() + "/" + vanguard.getNetToughness()
                + " [untapped] @" + vanguard.getId();      // the board's own rendering, minus "(was @k)"
        String report = PlayerLookahead.previewCast(g, me, sa, 5L, label);
        System.out.println("[LOOKAHEAD] " + report);
        Assert.assertTrue(report.contains("assuming target: Elite Vanguard"), report);
        Assert.assertTrue(report.contains("board 2 -> 1"), "the named creature should die: " + report);
    }

    /** A PASS preview resolves nothing, so no copy is made and the answer says so. The one thing it
     *  cannot answer is what the opponent will do if the seat does nothing, so it says that too,
     *  rather than implying the preview played their turn. runs/loop/g03 and runs/loop/g05 both had
     *  the seat ask for PASS and get an error instead. */
    @Test
    public void previewPassSaysNothingResolvesAndDoesNotPlayTheOpponentsTurn() {
        String report = PlayerLookahead.previewPass();
        System.out.println("[LOOKAHEAD] " + report);
        Assert.assertTrue(report.startsWith("if you pass, nothing resolves"), report);
        Assert.assertTrue(report.contains("unchanged"), report);
        Assert.assertTrue(report.contains("opponent's turn"),
                "a PASS preview must not imply it plays out their turn: " + report);
    }
}
