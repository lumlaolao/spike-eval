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
}
