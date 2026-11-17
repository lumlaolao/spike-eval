package forge.spike;

import static forge.spike.LineFindingProbeTest.bareGame;
import static forge.spike.LineFindingProbeTest.card;

import java.util.Arrays;
import java.util.List;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;

import forge.game.Game;
import forge.game.card.Card;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.zone.ZoneType;

/** A preview's copy keeps what the seat knows (critique O4, U7). */
public class LookaheadTruthTest {

    @BeforeClass
    public void init() { new LineFindingProbeTest().initializeModel(); }

    /** O4: after Brainstorm the seat knows its top two cards, and a preview shuffled them away; a hand Thoughtseize
     *  showed it was re-dealt from hand plus library. */
    @Test
    public void theKnownTopAndAKnownHandSurviveDeterminization() {
        for (int seed = 0; seed < 5; seed++) {
            Game g = bareGame(PhaseType.MAIN1);
            Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
            for (String n : new String[] {"Island", "Mountain", "Forest", "Plains", "Swamp", "Wastes"}) card(n, me, ZoneType.Library);
            List<Card> lib = me.getZone(ZoneType.Library).getCards();
            String top0 = lib.get(0).getName(), top1 = lib.get(1).getName();
            card("Force of Will", rival, ZoneType.Hand);
            card("Brainstorm", rival, ZoneType.Hand);
            for (int i = 0; i < 8; i++) card("Island", rival, ZoneType.Library);
            forge.util.MyRandom.setRandom(new java.util.Random(seed));
            PlayerLookahead.determinize(g, me, 2, Map.of(rival.getName(), Arrays.asList("Force of Will")));
            List<Card> after = me.getZone(ZoneType.Library).getCards();
            Assert.assertEquals(after.get(0).getName(), top0, "seed " + seed);
            Assert.assertEquals(after.get(1).getName(), top1, "seed " + seed);
            Assert.assertTrue(rival.getZone(ZoneType.Hand).getCards().stream().anyMatch(c -> c.getName().equals("Force of Will")),
                    "the known card stays in the hand, seed " + seed);
            Assert.assertEquals(rival.getZone(ZoneType.Hand).size(), 2);
        }
    }
}
