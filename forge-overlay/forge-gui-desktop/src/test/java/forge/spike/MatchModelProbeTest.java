package forge.spike;

import java.util.List;

import org.testng.Assert;
import org.testng.annotations.Test;

import forge.game.Game;
import forge.game.GameLogEntryType;
import forge.game.card.Card;
import forge.game.player.Player;
import forge.game.zone.ZoneType;
import forge.spike.LlmSpikeGui.ContextProfile;

/**
 * M2 — the MATCH MODEL's capability probes.
 *
 * <p>One probe per play-quality failure the match model exists to fix, so a future refactor that
 * silently drops a support fails here rather than in a paid game. These are DETERMINISTIC and
 * harness-side: they assert what the seat is TOLD, not what a model does with it. The behavioural
 * half (does a model play better?) is a paired A/B, not a unit test — see docs/harness/MATCH-MODEL.md.
 *
 * <p>Probe → failure class:
 * <ul>
 *   <li>{@link #sameNamedPermanentsStayDistinct} — cannot tell two same-named permanents apart</li>
 *   <li>{@link #aCardThatLeavesForAHiddenZoneIsRemembered} — cannot follow unsummon→Thoughtseize</li>
 *   <li>{@link #anObjectsPastIsRetrievable} — cannot track an object across zone changes</li>
 *   <li>{@link #legalityIsStatedNotInferred} — replays a land drop / re-targets a resolved spell</li>
 *   <li>{@link #thePlanOutlivesTheTurn} — cannot sequence a turn or hold up mana</li>
 *   <li>{@link #engineArithmeticIsStated} — mishandles races, counterspells, combat tricks</li>
 *   <li>{@link #recurringThreatsAreNamedExplicitly} — does not remember repeated threats</li>
 *   <li>{@link #historyIsLabelledAsHistory} — confuses the past with what it can do now</li>
 * </ul>
 */
public class MatchModelProbeTest {

    @Test
    public void sameNamedPermanentsStayDistinct() {
        Game g = SeatObservationTest.world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        Player me = g.getPlayers().get(0);
        SeatObservation o = SeatObservationTest.observerFor(g, 0);
        Card a = SeatObservationTest.card("Mountain", me, ZoneType.Battlefield);
        Card b = SeatObservationTest.card("Mountain", me, ZoneType.Battlefield);
        o.describe(a, false);
        o.describe(b, false);
        int ha = o.handleOf(a);
        int hb = o.handleOf(b);
        Assert.assertTrue(ha > 0 && hb > 0, "both copies must have handles");
        Assert.assertNotEquals(ha, hb, "two same-named permanents must not share a handle");
        String r = SeatObservationTest.render(o, "");
        Assert.assertTrue(r.contains("@" + ha) && r.contains("@" + hb), "both handles must be visible:\n" + r);
    }

    @Test
    public void aCardThatLeavesForAHiddenZoneIsRemembered() {
        Game g = SeatObservationTest.world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        Player rival = g.getPlayers().get(1);
        SeatObservation o = SeatObservationTest.observerFor(g, 0);
        Card creature = SeatObservationTest.card("Griselbrand", rival, ZoneType.Battlefield);
        forge.game.zone.Zone bf = creature.getZone();
        forge.game.zone.Zone hand = SeatObservationTest.card("Entomb", rival, ZoneType.Hand).getZone();
        o.describe(creature, false);
        o.onZoneChange(new forge.game.event.GameEventCardChangeZone(creature, bf, hand));
        String r = SeatObservationTest.render(o, "");
        Assert.assertTrue(r.contains("Rival's hand: Griselbrand"),
                "the seat must know what it saw go into their hand:\n" + r);
    }

    @Test
    public void anObjectsPastIsRetrievable() {
        Game g = SeatObservationTest.world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        Player rival = g.getPlayers().get(1);
        SeatObservation o = SeatObservationTest.observerFor(g, 0);
        Card creature = SeatObservationTest.card("Griselbrand", rival, ZoneType.Battlefield);
        forge.game.zone.Zone bf = creature.getZone();
        forge.game.zone.Zone yard = SeatObservationTest.card("Dark Ritual", rival, ZoneType.Graveyard).getZone();
        o.describe(creature, false);
        int h = o.handleOf(creature);
        o.onZoneChange(new forge.game.event.GameEventCardChangeZone(creature, bf, yard));
        String hist = o.inspect("@" + h);
        Assert.assertTrue(hist.contains("battlefield -> graveyard"),
                "an object's zone history must be retrievable:\n" + hist);
    }

    @Test
    public void legalityIsStatedNotInferred() {
        Game g = SeatObservationTest.world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        Player me = g.getPlayers().get(0);
        SeatObservation o = SeatObservationTest.observerFor(g, 0);
        String before = SeatObservationTest.render(o, "");
        Assert.assertTrue(before.contains("SPENT THIS TURN:"), before);
        Assert.assertTrue(before.contains("land drop AVAILABLE"), before);
        me.setLandsPlayedThisTurn(me.getMaxLandPlays());
        Assert.assertTrue(SeatObservationTest.render(o, "").contains("land drop USED"),
                "a used land drop must be STATED, never left to be inferred from absence");
    }

    @Test
    public void thePlanOutlivesTheTurn() {
        Game g = SeatObservationTest.world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        SeatObservation o = SeatObservationTest.observerFor(g, 0);
        int now = g.getPhaseHandler().getTurn();
        String r = o.render("seat", ContextProfile.V4_FAITHFUL, "", "hold up UU, counter their payoff", now - 1);
        Assert.assertTrue(r.contains("\nYOUR PLAN (you stated this at T" + (now - 1)), r);
        Assert.assertTrue(r.contains("a previous turn"), "a plan from an earlier turn must be flagged stale");
    }

    @Test
    public void engineArithmeticIsStated() {
        Game g = SeatObservationTest.world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        Player me = g.getPlayers().get(0);
        SeatObservationTest.card("Grizzly Bears", me, ZoneType.Battlefield);
        String r = SeatObservationTest.render(SeatObservationTest.observerFor(g, 0), "");
        Assert.assertTrue(r.contains("FACTS (engine estimates, not truth):"), r);
        Assert.assertTrue(r.contains("your next combat:"), "the seat must be given its own clock");
        Assert.assertTrue(r.contains("instant speed"), "the opponent's instant-speed ceiling must be stated");
    }

    @Test
    public void recurringThreatsAreNamedExplicitly() {
        Game g = SeatObservationTest.world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        SeatObservation o = SeatObservationTest.observerFor(g, 0);
        g.getGameLog().add(GameLogEntryType.STACK_ADD, "Rival cast Entomb.");
        g.getGameLog().add(GameLogEntryType.STACK_ADD, "Rival cast Entomb.");
        g.getGameLog().add(GameLogEntryType.STACK_ADD, "Rival cast Careful Study.");
        o.capture();
        String r = SeatObservationTest.render(o, "");
        Assert.assertTrue(r.contains("RECURRING (cast 2+ times): Entomb x2"), r);
        Assert.assertFalse(r.contains("Careful Study x"), "a card cast once is not recurring");
    }

    @Test
    public void historyIsLabelledAsHistory() {
        Game g = SeatObservationTest.world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        SeatObservation o = SeatObservationTest.observerFor(g, 0);
        o.recordReveal("T1: they showed Force of Will");
        String r = SeatObservationTest.render(o, "GAME HISTORY (compacted; E<n> are canonical event ids):\n  T1 [E1]: x\n");
        // The past must be visibly the past, so it is never mistaken for what the seat can do now.
        Assert.assertTrue(r.contains("REVEALS TO YOU (historical evidence, seat-local;"), r);
        Assert.assertTrue(r.contains("GAME HISTORY (compacted"), r);
        Assert.assertTrue(r.contains("CURRENT OBSERVATION") || r.contains("== Turn "), r);
    }

    @Test
    public void everyObservationIsSelfDescribing() {
        Game g = SeatObservationTest.world(new String[] {"Thoughtseize"}, new String[] {"Entomb"});
        String r = SeatObservationTest.render(SeatObservationTest.observerFor(g, 0), "");
        Assert.assertTrue(r.contains("observation " + SeatObservation.VERSION), r);
        Assert.assertTrue(r.contains("LEGEND: @N"), "every prompt must explain its own notation");
        List<String> must = List.of("SPENT THIS TURN", "YOUR PLAN", "FACTS", "KNOWN IN HIDDEN ZONES");
        for (String s : must) Assert.assertTrue(r.contains(s), "legend must describe " + s + ":\n" + r);
    }
}
