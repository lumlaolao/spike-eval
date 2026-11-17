package forge.spike;

import static forge.spike.LineFindingProbeTest.bareGame;
import static forge.spike.LineFindingProbeTest.card;

import java.util.Arrays;
import java.util.List;

import org.testng.Assert;
import org.testng.annotations.AfterClass;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import forge.ai.LobbyPlayerAi;
import forge.game.Game;
import forge.game.card.Card;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.zone.ZoneType;

/** The menus the seat picks from (critique batch 5). */
public class MenuTruthTest {

    @BeforeClass
    public void quickRetries() { QuickRetries.on(); }

    @AfterClass(alwaysRun = true)
    public void realRetries() { QuickRetries.off(); }

    @BeforeClass
    public void init() { new LineFindingProbeTest().initializeModel(); }

    @BeforeMethod
    public void faithful() { LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL; }

    @AfterMethod(alwaysRun = true)
    public void legacy() { LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null; }

    private static LlmSpikeGui.PlayerControllerLLM seat(Game g, LineFindingProbeTest.Recorder model) {
        Player me = g.getPlayers().get(0);
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(g, me,
                new LobbyPlayerAi("Seat", null), "Seat", "", model, model, "llm");
        me.dangerouslySetController(ctrl);
        return ctrl;
    }

    private static String decision(String prompt) { return prompt.substring(prompt.lastIndexOf("DECISION:")); }

    /** O18: Swords to Plowshares listed the seat's own creatures first. The opponents' come first. */
    @Test
    public void aTargetListPutsTheOpponentsObjectsFirst() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Plains", me, ZoneType.Battlefield);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Serra Angel", rival, ZoneType.Battlefield);
        card("Swords to Plowshares", me, ZoneType.Hand);
        GoldenChecksTest.Script model = new GoldenChecksTest.Script("CHOICE: 1")
                .on("You have priority", GoldenChecksTest.choice("CAST Swords to Plowshares"));
        seat(g, model).chooseSpellAbilityToPlay();
        String ask = model.prompts.stream().filter(p -> p.contains("Choose the TARGET")).findFirst().orElse("");
        String d = decision(ask);
        Assert.assertTrue(d.indexOf("Serra Angel") >= 0 && d.indexOf("Serra Angel") < d.indexOf("Grizzly Bears"), d);
    }

    /** O3: attackers were "Name (P/T)", unlike every other option; they are named by handle and state. */
    @Test
    public void anAttackerIsNamedByItsHandle() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        Player me = g.getPlayers().get(0);
        card("Grizzly Bears", me, ZoneType.Battlefield).setSickness(false);
        GoldenChecksTest.Script model = new GoldenChecksTest.Script("PICKS: none");
        seat(g, model).declareAttackers(me, new forge.game.combat.Combat(me));
        String d = decision(model.prompts.get(0));
        Assert.assertTrue(d.matches("(?s).*1\\) Grizzly Bears 2/2[^\\n]*@\\d+.*"), d);
    }

    /** C16: a multi-target play could not be cancelled; a single-target one could. */
    @Test
    public void aMultiTargetPlayCanBeCancelled() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Mountain", me, ZoneType.Battlefield);
        card("Serra Angel", rival, ZoneType.Battlefield);
        card("Forked Bolt", me, ZoneType.Hand);
        GoldenChecksTest.Script model = new GoldenChecksTest.Script("CHOICE: 99")
                .on("You have priority", p -> p.contains("cancelled") ? "CHOICE: 99" : GoldenChecksTest.choice("CAST Forked Bolt").apply(p))
                .on("Choose TARGETS", p -> "PICKS: " + GoldenChecksTest.opt(p, "CANCEL"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model);
        ctrl.chooseSpellAbilityToPlay();
        Assert.assertTrue(model.prompts.stream().anyMatch(p -> p.contains("Choose TARGETS") && p.contains("CANCEL")), "offered");
        Assert.assertEquals(g.getStack().size(), 0, "nothing was cast");
        Assert.assertEquals(rival.getLife(), 20);
    }

    /** C22: first fit gave a green-white creature {G} and left a mono-green pick paying nothing. */
    @Test
    public void convokeSymbolsAreMatchedToTheMostPicks() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card selesnya = card("Qasali Pridemage", me, ZoneType.Battlefield);   // green-white
        Card elf = card("Llanowar Elves", me, ZoneType.Battlefield);          // green
        List<Card> opts = Arrays.asList(selesnya, elf);
        List<forge.card.mana.ManaCostShard> shards = Arrays.asList(forge.card.mana.ManaCostShard.GREEN, forge.card.mana.ManaCostShard.WHITE);
        java.util.Map<Card, forge.card.mana.ManaCostShard> got = LlmSpikeGui.PlayerControllerLLM.assignConvoke(opts, Arrays.asList(0, 1), shards, true);
        Assert.assertEquals(got.size(), 2, "both pay: " + got);
        Assert.assertEquals(got.get(elf), forge.card.mana.ManaCostShard.GREEN);
        Assert.assertEquals(got.get(selesnya), forge.card.mana.ManaCostShard.WHITE);
    }
}
