package forge.spike;

import java.util.ArrayList;
import java.util.List;

import com.google.common.collect.Lists;

import forge.GuiDesktop;
import forge.StaticData;
import forge.ai.LobbyPlayerAi;
import forge.deck.Deck;
import forge.game.Game;
import forge.game.GameRules;
import forge.game.GameStage;
import forge.game.GameType;
import forge.game.Match;
import forge.game.card.Card;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.player.RegisteredPlayer;
import forge.game.spellability.SpellAbility;
import forge.game.zone.ZoneType;
import forge.gui.GuiBase;
import forge.item.IPaperCard;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;

import org.testng.Assert;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;

/**
 * Engine probes for the "own-card line" failures audited in docs/harness/ARCHITECTURE-REVIEW.md.
 * Each drives the LIVE controller against a real engine position and records every prompt the
 * model is shown, so "who decided this, and what were they told" is an observation, not a guess.
 */
public class LineFindingProbeTest {
    private static boolean initialized;

    @BeforeClass
    public void initializeModel() {
        if (initialized) return;
        GuiBase.setInterface(new GuiDesktop());
        FModel.initialize(null, preferences -> {
            preferences.setPref(FPref.LOAD_CARD_SCRIPTS_LAZILY, false);
            preferences.setPref(FPref.UI_LANGUAGE, "en-US");
            return null;
        });
        initialized = true;
    }

    /** Records every prompt. A sacrifice-as-cost question is answered by NAME (the option whose
     *  label contains {@code sacrifice}); everything else gets {@code reply}. */
    static final class Recorder implements LLMBridge {
        final String reply;
        final String sacrifice;
        final List<String> prompts = new ArrayList<>();
        Recorder(String reply) { this(reply, null); }
        Recorder(String reply, String sacrifice) { this.reply = reply; this.sacrifice = sacrifice; }
        public String ask(String prompt) {
            prompts.add(prompt);
            if (sacrifice != null && prompt.contains("COST: to pay for")) {
                java.util.regex.Matcher m = java.util.regex.Pattern
                        .compile("(?m)^\\s*(\\d+)\\) [^\\n]*" + java.util.regex.Pattern.quote(sacrifice))
                        .matcher(prompt.substring(prompt.indexOf("COST: to pay for")));
                if (m.find()) return "PICKS: " + m.group(1);
            }
            return reply;
        }
        boolean asked(String needle) { for (String q : prompts) if (q.contains(needle)) return true; return false; }
        public String getLabel() { return "probe"; }
        public String getModel() { return "fake"; }
        public long getTotalMs() { return 0; }
        public int getCalls() { return prompts.size(); }
    }

    static Game bareGame(PhaseType phase) {
        List<RegisteredPlayer> players = Lists.newArrayList();
        Deck d = new Deck();
        players.add(new RegisteredPlayer(d).setPlayer(new LobbyPlayerAi("Seat", null)));
        players.add(new RegisteredPlayer(d).setPlayer(new LobbyPlayerAi("Rival", null)));
        GameRules rules = new GameRules(GameType.Constructed);
        Game game = new Game(players, rules, new Match(rules, players, "LineFindingProbe"));
        game.setAge(GameStage.Play);
        game.getPhaseHandler().devModeSet(phase, game.getPlayers().get(0));
        return game;
    }

    static Card card(String name, Player p, ZoneType zone) {
        IPaperCard pc = FModel.getMagicDb().getCommonCards().getCard(name);
        if (pc == null) { StaticData.instance().attemptToLoadCard(name); pc = FModel.getMagicDb().getCommonCards().getCard(name); }
        Assert.assertNotNull(pc, "card not in the engine DB: " + name);
        Card c = Card.fromPaperCard(pc, p);
        c.setGameTimestamp(p.getGame().getNextTimestamp());
        p.getZone(zone).add(c);
        if (zone == ZoneType.Battlefield) c.setSickness(false);
        return c;
    }

    static LlmSpikeGui.PlayerControllerLLM seat(Game g, Recorder smart, Recorder fast) {
        Player me = g.getPlayers().get(0);
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(g, me,
                new LobbyPlayerAi("Seat", null), "Seat", "", smart, fast, "llm");
        me.dangerouslySetController(ctrl);
        return ctrl;
    }

    private static final String PLAY = "PLAN: use it\nCHOICE: 1";

    /** Goblin Bombardment ("Sacrifice a creature: 1 damage to any target"): the MODEL chooses which
     *  creature pays the cost. Before this was routed, Forge's AI chose (it took Memnite) and no
     *  question was asked — LineFindingProbe A in docs/harness/ARCHITECTURE-REVIEW.md. */
    @Test
    public void sacrificeOutletCostIsChosenByTheModel() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Goblin Bombardment", me, ZoneType.Battlefield);
        Card angel = card("Serra Angel", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        Card memnite = card("Memnite", me, ZoneType.Battlefield);
        Recorder smart = new Recorder(PLAY, "Grizzly Bears"), fast = new Recorder("CHOICE: 1", "Grizzly Bears");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, smart, fast);

        List<SpellAbility> chosen = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(chosen, "seat passed instead of activating");
        ctrl.playChosenSpellAbility(chosen.get(0));

        Assert.assertTrue(smart.asked("COST: to pay for") || fast.asked("COST: to pay for"),
                "the model was never asked which creature to sacrifice");
        Assert.assertFalse(bears.isInZone(ZoneType.Battlefield), "the creature the model named was not sacrificed");
        Assert.assertTrue(angel.isInZone(ZoneType.Battlefield) && memnite.isInZone(ZoneType.Battlefield),
                "a creature the model did not name was sacrificed");
        Assert.assertEquals(g.getStack().size(), 1, "the ability is not on the stack");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** Cabal Therapy's flashback cost is "sacrifice a creature"; Ajani, Nacatl Pariah flips when
     *  ANOTHER Cat dies. The line needs the Cat sacrificed and Ajani kept. Forge's AI sacrificed
     *  Ajani (probe D), which made the line impossible whatever the model chose. */
    @Test
    public void flashbackSacrificeKeepsTheCreatureTheModelWantsToKeep() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card ajani = card("Ajani, Nacatl Pariah", me, ZoneType.Battlefield);
        Card lions = card("Savannah Lions", me, ZoneType.Battlefield);
        card("Cabal Therapy", me, ZoneType.Graveyard);
        card("Force of Will", rival, ZoneType.Hand);
        Recorder smart = new Recorder(PLAY, "Savannah Lions"), fast = new Recorder("CHOICE: 1", "Savannah Lions");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, smart, fast);

        List<SpellAbility> chosen = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(chosen, "flashback was not offered");
        Assert.assertEquals(chosen.get(0).getHostCard().getName(), "Cabal Therapy");
        ctrl.playChosenSpellAbility(chosen.get(0));

        Assert.assertTrue(ajani.isInZone(ZoneType.Battlefield), "Ajani was sacrificed to his own enabler");
        Assert.assertFalse(lions.isInZone(ZoneType.Battlefield), "the Cat the model named was not sacrificed");
        Assert.assertEquals(g.getStack().size(), 1, "Cabal Therapy is not on the stack");
    }

    /** Runs {@code body} and returns everything the harness printed while it ran. */
    static String logOf(Runnable body) {
        java.io.PrintStream old = System.out;
        java.io.ByteArrayOutputStream buf = new java.io.ByteArrayOutputStream();
        System.setOut(new java.io.PrintStream(buf, true));
        try { body.run(); } finally { System.setOut(old); }
        String out = buf.toString();
        old.print(out);
        return out;
    }

    /** One eligible creature is no choice: it is taken without a model call, and the log SAYS which
     *  creature paid. Before the cost was routed there was no such line for any sacrifice. */
    @Test
    public void aForcedSacrificeIsNotAskedButIsLogged() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Goblin Bombardment", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        Recorder smart = new Recorder(PLAY), fast = new Recorder("CHOICE: 1");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, smart, fast);
        String log = logOf(() -> {
            List<SpellAbility> chosen = ctrl.chooseSpellAbilityToPlay();
            Assert.assertNotNull(chosen);
            ctrl.playChosenSpellAbility(chosen.get(0));
        });
        Assert.assertFalse(smart.asked("COST: to pay for") || fast.asked("COST: to pay for"), "asked about a forced sacrifice");
        Assert.assertFalse(bears.isInZone(ZoneType.Battlefield));
        Assert.assertEquals(g.getStack().size(), 1);
        Assert.assertTrue(log.contains("sacrifice(forced) [Goblin Bombardment] -> Grizzly Bears"), log);
    }

    /** An unparseable answer takes the FIRST legal creature — a fixed rule, not Forge's AI. The AI's
     *  own rule ("worst creature") would take Grizzly Bears here; the deterministic rule takes the
     *  first candidate, Serra Angel. The difference is what proves who decided. */
    @Test
    public void anUnparseableSacrificeAnswerFallsBackDeterministically() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Goblin Bombardment", me, ZoneType.Battlefield);
        Card angel = card("Serra Angel", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        LLMBridge garbled = new LLMBridge() {
            public String ask(String prompt) { return prompt.contains("COST: to pay for") ? "the small one" : PLAY; }
            public String getLabel() { return "probe"; }
            public String getModel() { return "fake"; }
            public long getTotalMs() { return 0; }
            public int getCalls() { return 0; }
        };
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(g, me,
                new LobbyPlayerAi("Seat", null), "Seat", "", garbled, garbled, "llm");
        me.dangerouslySetController(ctrl);
        String log = logOf(() -> {
            List<SpellAbility> chosen = ctrl.chooseSpellAbilityToPlay();
            Assert.assertNotNull(chosen);
            ctrl.playChosenSpellAbility(chosen.get(0));
        });
        Assert.assertFalse(angel.isInZone(ZoneType.Battlefield), "the first legal candidate was not the one sacrificed");
        Assert.assertTrue(bears.isInZone(ZoneType.Battlefield), "the engine AI's choice (worst creature) was sacrificed");
        Assert.assertTrue(log.contains("sacrifice(unparsed)"), log);
        Assert.assertEquals(g.getStack().size(), 1);
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** A play that cannot be paid for is not a play, and the log must say so. Aiming Bombardment at
     *  the seat's ONLY creature leaves nothing to sacrifice (the target is excluded from the cost):
     *  the audited game logged exactly this as "casts Goblin Bombardment". */
    @Test
    public void anUnpayableActivationIsReportedAsFailedAndChangesNothing() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card bombardment = card("Goblin Bombardment", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        LLMBridge aimsAtOwnCreature = new LLMBridge() {
            public String ask(String prompt) {
                String opts = prompt.substring(Math.max(0, prompt.lastIndexOf("DECISION:")));
                if (!opts.contains("Goblin Bombardment @") && !opts.contains("CAST Goblin Bombardment")) {
                    java.util.regex.Matcher m = java.util.regex.Pattern
                            .compile("(?m)^\\s*(\\d+)\\) [^\\n]*Grizzly Bears").matcher(opts);
                    if (m.find()) return "CHOICE: " + m.group(1);
                }
                return PLAY;
            }
            public String getLabel() { return "probe"; }
            public String getModel() { return "fake"; }
            public long getTotalMs() { return 0; }
            public int getCalls() { return 0; }
        };
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(g, me,
                new LobbyPlayerAi("Seat", null), "Seat", "", aimsAtOwnCreature, aimsAtOwnCreature, "llm");
        me.dangerouslySetController(ctrl);
        final SpellAbility[] played = new SpellAbility[1];
        String log = logOf(() -> {
            List<SpellAbility> chosen = ctrl.chooseSpellAbilityToPlay();
            Assert.assertNotNull(chosen);
            played[0] = chosen.get(0);
            ctrl.playChosenSpellAbility(chosen.get(0));
        });
        Assert.assertEquals(played[0].getTargets().getFirstTargetedCard(), bears, "fixture did not aim at the seat's own creature");
        Assert.assertTrue(log.contains("FAILED to pay for"), log);
        Assert.assertTrue(bears.isInZone(ZoneType.Battlefield), "a creature was sacrificed for a play that failed");
        Assert.assertTrue(bombardment.isInZone(ZoneType.Battlefield));
        Assert.assertEquals(g.getStack().size(), 0, "a failed play reached the stack");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }
}
