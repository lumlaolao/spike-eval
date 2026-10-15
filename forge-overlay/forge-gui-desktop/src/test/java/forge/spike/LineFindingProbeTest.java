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
    static class Recorder implements LLMBridge {
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

    /** Seats answer by function call (ToolAnswerBridge) instead of text: GoldenChecksToolTest runs the golden
     *  checks on the default protocol this way. Each wrapped bridge is kept, so its leaks can be read. */
    static volatile boolean TOOL_MODE = false;
    static final List<ToolAnswerBridge> TOOL_BRIDGES = java.util.Collections.synchronizedList(new ArrayList<>());
    /** The seats built in tool mode, so GoldenChecksToolTest can check none read a decision from text. */
    static final List<LlmSpikeGui.PlayerControllerLLM> TOOL_SEATS = java.util.Collections.synchronizedList(new ArrayList<>());

    private static LLMBridge maybeTool(Recorder r) {
        if (!TOOL_MODE || r == null) return r;
        ToolAnswerBridge t = new ToolAnswerBridge(r);
        TOOL_BRIDGES.add(t);
        return t;
    }

    static LlmSpikeGui.PlayerControllerLLM seat(Game g, Recorder smart, Recorder fast) {
        Player me = g.getPlayers().get(0);
        LLMBridge s = maybeTool(smart);
        LLMBridge f = fast == smart ? s : maybeTool(fast);
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(g, me,
                new LobbyPlayerAi("Seat", null), "Seat", "", s, f, "llm");
        me.dangerouslySetController(ctrl);
        if (TOOL_MODE) TOOL_SEATS.add(ctrl);
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

    static List<String> faithfulMenu(Player me) {
        LlmSpikeGui.PlayerControllerLLM.PlayOptions po = LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(me, true);
        List<String> out = new ArrayList<>();
        for (SpellAbility sa : po.spells)
            out.add(LlmSpikeGui.PlayerControllerLLM.verbOf(sa) + " "
                    + LlmSpikeGui.PlayerControllerLLM.faithfulSaLabel(sa, sa.getHostCard().getName() + " @N"));
        return out;
    }

    static String only(List<String> menu, String name) {
        String hit = null;
        for (String m : menu) if (m.contains(name)) { Assert.assertNull(hit, "two options for " + name + ": " + menu); hit = m; }
        Assert.assertNotNull(hit, "no option for " + name + ": " + menu);
        return hit;
    }

    /** An option says what it costs and where it comes from. The legacy label printed the CARD's mana
     *  cost on everything: "CAST Goblin Bombardment 1{R}" for an ability paid with a creature, and
     *  "CAST Cabal Therapy {B}" for a graveyard flashback paid with a creature. */
    @Test
    public void optionLabelsShowTheCostAndZoneOfTheOptionItself() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        for (int i = 0; i < 4; i++) card("Badlands", me, ZoneType.Battlefield);
        card("Goblin Bombardment", me, ZoneType.Battlefield);
        card("Savannah Lions", me, ZoneType.Battlefield);
        card("Cabal Therapy", me, ZoneType.Graveyard);
        card("Faithless Looting", me, ZoneType.Graveyard);
        card("Lightning Bolt", me, ZoneType.Hand);
        List<String> menu = faithfulMenu(me);

        String bombardment = only(menu, "Goblin Bombardment");
        Assert.assertTrue(bombardment.startsWith("ACTIVATE Goblin Bombardment @N [Sacrifice a creature:"), bombardment);
        Assert.assertFalse(bombardment.contains("{R}"), "an activation must not show the card's mana cost: " + bombardment);
        Assert.assertFalse(bombardment.contains("CARDNAME"), bombardment);

        String therapy = only(menu, "Cabal Therapy");
        Assert.assertTrue(therapy.startsWith("CAST Cabal Therapy @N from your GRAVEYARD (Flashback) — cost: Sacrifice a creature ["), therapy);
        Assert.assertFalse(therapy.contains("{B}"), "a flashback must not show the hand cost: " + therapy);

        String looting = only(menu, "Faithless Looting");
        Assert.assertTrue(looting.contains("from your GRAVEYARD (Flashback) — cost: {2}{R}"), looting);

        // A spell from hand at its printed cost reads exactly as before.
        Assert.assertEquals(only(menu, "Lightning Bolt"), "CAST Lightning Bolt @N {R} [Lightning Bolt deals 3 damage to any target.]");
    }

    /** The upkeep shortcut passes for the seat without a model call. It must leave a `window` row
     *  saying so and counting what the seat could have used (both of Sensei's Divining Top's abilities), because the
     *  decision rows never see these windows: before this, the review sheet called such games clean. */
    @Test
    public void aWindowThePlayLoopPassesWithoutAskingIsRecorded() throws Exception {
        // The skip heuristics are off by default since 2026-10-15 (SKIP_HEURISTICS=1 opts in); this tests them on.
        boolean was = LlmSpikeGui.PlayerControllerLLM.SKIP_HEUR;
        LlmSpikeGui.PlayerControllerLLM.SKIP_HEUR = true;
        try {
            java.nio.file.Path dir = java.nio.file.Files.createTempDirectory("window-rows");
            LlmSpikeGui.EvalLog.closeForTest();
            LlmSpikeGui.EvalLog.initToDir(dir.toString(), 7);
            try {
                Game g = bareGame(PhaseType.UPKEEP);
                Player me = g.getPlayers().get(0);
                card("Island", me, ZoneType.Battlefield);
                card("Sensei's Divining Top", me, ZoneType.Battlefield);
                Recorder smart = new Recorder(PLAY), fast = new Recorder(PLAY);
                LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, smart, fast);
    
                Assert.assertNull(ctrl.chooseSpellAbilityToPlay(), "the shortcut passes priority");
                Assert.assertEquals(smart.prompts.size() + fast.prompts.size(), 0, "no model was asked");
                Assert.assertEquals(ctrl.windowSkips.get("window-gate"), Integer.valueOf(1));
                String rows = new String(java.nio.file.Files.readAllBytes(LlmSpikeGui.EvalLog.currentFile().toPath()));
                Assert.assertTrue(rows.contains("\"t\":\"window\""), rows);
                Assert.assertTrue(rows.contains("\"phase\":\"UPKEEP\""), rows);
                Assert.assertTrue(rows.contains("\"reason\":\"window-gate\",\"castable\":2"), rows);
            } finally {
                LlmSpikeGui.EvalLog.closeForTest();
            }
        } finally {
            LlmSpikeGui.PlayerControllerLLM.SKIP_HEUR = was;
        }
    }

    /** Sacrifices `victim` through the engine's zone-change table, so "dies" triggers fire. */
    static void sacrificeForReal(Game g, Card victim) {
        forge.game.card.CardZoneTable table = new forge.game.card.CardZoneTable(
                g.copyLastStateBattlefield(), g.copyLastStateGraveyard());
        java.util.Map<forge.game.ability.AbilityKey, Object> params = forge.game.ability.AbilityKey.newMap();
        forge.game.ability.AbilityKey.addCardZoneTableParams(params, table);
        g.getAction().sacrifice(Lists.newArrayList(victim), null, false, params);
        table.triggerChangesZoneAll(g, null);
        g.getTriggerHandler().runWaitingTriggers();
        g.getStack().addAllTriggeredAbilitiesToStack();
        while (!g.getStack().isEmpty()) {
            g.getStack().resolveStack();
            g.getAction().checkStateEffects(false);
            g.getStack().addAllTriggeredAbilitiesToStack();
        }
    }

    static boolean controlsPlaneswalker(Player p) {
        for (Card c : p.getCardsIn(ZoneType.Battlefield)) if (c.isPlaneswalker()) return true;
        return false;
    }

    /** Ajani's flip is an optional trigger. Under the faithful profile the question carries the
     *  trigger's own text, goes to the reasoning tier, and the answer is logged. */
    @Test
    public void anOptionalTriggerIsAskedWithItsTextAndTheAnswerIsLogged() {
        for (boolean accept : new boolean[] {true, false}) {
            Game g = bareGame(PhaseType.MAIN1);
            Player me = g.getPlayers().get(0);
            card("Ajani, Nacatl Pariah", me, ZoneType.Battlefield);
            Card lions = card("Savannah Lions", me, ZoneType.Battlefield);
            Recorder smart = new Recorder(accept ? "CHOICE: 1" : "CHOICE: 2"), fast = new Recorder("CHOICE: 2");
            String log;
            LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL;
            try {
                seat(g, smart, fast);
                log = logOf(() -> sacrificeForReal(g, lions));
            } finally {
                LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null;
            }

            Assert.assertEquals(fast.prompts.size(), 0, "the flip was asked on the no-reasoning tier");
            Assert.assertEquals(smart.prompts.size(), 1);
            String q = smart.prompts.get(0);
            Assert.assertTrue(q.contains("OPTIONAL TRIGGER: \""), q);
            Assert.assertTrue(q.contains("return him to the battlefield transformed"), "the trigger text is missing:\n" + q);
            Assert.assertEquals(controlsPlaneswalker(me), accept);
            Assert.assertTrue(log.contains("[ROUTER] confirm-trigger [Ajani, Nacatl Pariah]: " + (accept ? "YES" : "NO")), log);
        }
    }

    /** The LINES ask shows a FORMAT, never a real interaction: an example naming cards from an eval
     *  deck would coach that deck and make "did the seat find the line" unmeasurable. */
    @Test
    public void theLinesAskNamesNoRealCard() throws Exception {
        String ask = LlmSpikeGui.PlayerControllerLLM.DECK_LINES_ASK.toLowerCase();
        String dir = System.getProperty("harness.decks.dir");
        if (dir == null) throw new org.testng.SkipException("pass -Dharness.decks.dir=<repo>/eval/decks (the acceptance suite does)");
        java.io.File decks = new java.io.File(dir);
        Assert.assertTrue(decks.isDirectory(), dir);
        int checked = 0;
        for (java.io.File f : decks.listFiles((d, n) -> n.endsWith(".txt") || n.endsWith(".dck"))) {
            for (String line : java.nio.file.Files.readAllLines(f.toPath())) {
                java.util.regex.Matcher m = java.util.regex.Pattern.compile("^\\s*\\d+\\s+([^|(]+)").matcher(line);
                if (!m.find()) continue;
                String name = m.group(1).trim().toLowerCase();
                if (name.length() < 4) continue;
                checked++;
                Assert.assertFalse(ask.contains(name), "the LINES ask names a real card: " + name);
            }
        }
        Assert.assertTrue(checked > 100, "deck files were not read (" + checked + " card lines)");
    }

    /** The legacy label is part of the teacher/student prompt pairing and must not move. */
    @Test
    public void legacyLabelsAreUnchanged() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Goblin Bombardment", me, ZoneType.Battlefield);
        card("Savannah Lions", me, ZoneType.Battlefield);
        LlmSpikeGui.PlayerControllerLLM.PlayOptions po = LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(me, true);
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.buildPlayLabels(po.spells, po.landCards).get(0),
                "CAST Goblin Bombardment 1{R} [Sacrifice a creature: CARDNAME deals 1 damage to any target.]");
    }

    private static int count(List<String> prompts, String needle) {
        int n = 0;
        for (String q : prompts) if (q.contains(needle)) n++;
        return n;
    }

    /** The LINES ask is sent on a smart play decision, its answer is stored without the other tags,
     *  shown back at later decisions, and never asked again. */
    @Test
    public void theLinesAnswerIsParsedStoredAndShownBack() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        card("Lightning Bolt", me, ZoneType.Hand);
        Recorder smart = new Recorder("LINES: Aaa + Bbb -> x | Ccc + Ddd -> y PROFILE: p\nPLAN: bolt LINES: stray\nCHOICE: 1");
        Recorder fast = new Recorder("CHOICE: 1");
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL;
        try {
            LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, smart, fast);
            String log = logOf(() -> { ctrl.chooseSpellAbilityToPlay(); ctrl.chooseSpellAbilityToPlay(); });
            Assert.assertEquals(count(smart.prompts, LlmSpikeGui.PlayerControllerLLM.DECK_LINES_ASK), 1, "asked again after it was answered");
            Assert.assertTrue(log.contains("[ROUTER] lines: stray"), log);       // the LAST LINES tag wins, like every other tag
            Assert.assertTrue(log.contains("[ROUTER] plan: bolt\n") || log.contains("[ROUTER] plan: bolt\r"), log);
            Assert.assertTrue(smart.prompts.get(1).contains("YOUR CARD INTERACTIONS (you wrote this from your own decklist; unverified): stray"),
                    smart.prompts.get(1));
        } finally {
            LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null;
        }
    }

    /** A model that never answers stops being asked after DECK_LINES_MAX_ASKS, and a legacy-profile
     *  seat is never asked at all. */
    @Test
    public void theLinesAskIsBoundedAndFaithfulOnly() {
        for (boolean faithful : new boolean[] {true, false}) {
            Game g = bareGame(PhaseType.MAIN1);
            Player me = g.getPlayers().get(0);
            card("Mountain", me, ZoneType.Battlefield);
            card("Lightning Bolt", me, ZoneType.Hand);
            Recorder smart = new Recorder("PLAN: bolt\nCHOICE: 1"), fast = new Recorder("CHOICE: 1");
            LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = faithful ? LlmSpikeGui.ContextProfile.V4_FAITHFUL : null;
            try {
                LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, smart, fast);
                for (int i = 0; i < 5; i++) ctrl.chooseSpellAbilityToPlay();
            } finally {
                LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null;
            }
            Assert.assertTrue(smart.prompts.size() >= 5, "fixture did not reach the smart tier 5 times: " + smart.prompts.size());
            Assert.assertEquals(count(smart.prompts, LlmSpikeGui.PlayerControllerLLM.DECK_LINES_ASK),
                    faithful ? LlmSpikeGui.PlayerControllerLLM.DECK_LINES_MAX_ASKS : 0);
        }
    }

    /** Puts Ajani's real "another Cat died" trigger on the stack and leaves it there. */
    static void ownTriggerOnStack(Game g, Card cat) {
        forge.game.card.CardZoneTable table = new forge.game.card.CardZoneTable(
                g.copyLastStateBattlefield(), g.copyLastStateGraveyard());
        java.util.Map<forge.game.ability.AbilityKey, Object> params = forge.game.ability.AbilityKey.newMap();
        forge.game.ability.AbilityKey.addCardZoneTableParams(params, table);
        g.getAction().sacrifice(Lists.newArrayList(cat), null, false, params);
        table.triggerChangesZoneAll(g, null);
        g.getTriggerHandler().runWaitingTriggers();
        g.getStack().addAllTriggeredAbilitiesToStack();
    }

    /** With the seat's OWN trigger on the stack at its end step, the seat is asked whether to respond
     *  (the Voice of Victory + Goblin Bombardment window); declining once is remembered. With its own
     *  ACTIVATED ability on the stack it is still not asked. */
    @Test
    public void theSeatMayRespondToItsOwnTriggerButNotToItsOwnActivation() {
        // The skip heuristics are off by default since 2026-10-15 (SKIP_HEURISTICS=1 opts in); this tests them on.
        boolean was = LlmSpikeGui.PlayerControllerLLM.SKIP_HEUR;
        LlmSpikeGui.PlayerControllerLLM.SKIP_HEUR = true;
        try {
            Game g = bareGame(PhaseType.END_OF_TURN);
            Player me = g.getPlayers().get(0);
            card("Goblin Bombardment", me, ZoneType.Battlefield);
            card("Ajani, Nacatl Pariah", me, ZoneType.Battlefield);
            card("Grizzly Bears", me, ZoneType.Battlefield);
            Card lions = card("Savannah Lions", me, ZoneType.Battlefield);
            Recorder smart = new Recorder("CHOICE: 2"), fast = new Recorder("CHOICE: 2");   // 2 = PASS
            LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, smart, fast);
            ownTriggerOnStack(g, lions);
            Assert.assertEquals(g.getStack().size(), 1, "fixture: the trigger is not on the stack");
            Assert.assertTrue(g.getStack().peekAbility().isTrigger());
    
            Assert.assertNull(ctrl.chooseSpellAbilityToPlay());
            int asked = smart.prompts.size() + fast.prompts.size();
            Assert.assertEquals(asked, 1, "the seat was not asked about its own trigger window");
            Assert.assertTrue((smart.prompts.isEmpty() ? fast : smart).prompts.get(0).contains("Goblin Bombardment"));
            Assert.assertNull(ctrl.chooseSpellAbilityToPlay());
            Assert.assertEquals(smart.prompts.size() + fast.prompts.size(), asked, "the same declined window was asked twice");
    
            // Own ACTIVATION on the stack: unchanged, no ask.
            Game g2 = bareGame(PhaseType.MAIN1);
            Player me2 = g2.getPlayers().get(0);
            card("Goblin Bombardment", me2, ZoneType.Battlefield);
            card("Grizzly Bears", me2, ZoneType.Battlefield);
            card("Memnite", me2, ZoneType.Battlefield);
            Recorder s2 = new Recorder(PLAY, "Memnite"), f2 = new Recorder("CHOICE: 1", "Memnite");
            LlmSpikeGui.PlayerControllerLLM c2 = seat(g2, s2, f2);
            List<SpellAbility> first = c2.chooseSpellAbilityToPlay();
            Assert.assertNotNull(first);
            c2.playChosenSpellAbility(first.get(0));
            int before = s2.prompts.size() + f2.prompts.size();
            Assert.assertNull(c2.chooseSpellAbilityToPlay());
            Assert.assertEquals(s2.prompts.size() + f2.prompts.size(), before, "asked to respond to its own activation");
        } finally {
            LlmSpikeGui.PlayerControllerLLM.SKIP_HEUR = was;
        }
    }

    /** The seat passes on the rival's Ponder, then the rival casts Show and Tell in the same phase.
     *  The menu is identical (Counterspell, PASS), so the "already passed here" check, which keyed on
     *  turn, phase and menu only, passed again without asking: the counter was never offered. */
    @Test
    public void aNewOpponentSpellInTheSamePhaseIsAskedAgain() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Island", me, ZoneType.Battlefield);
        card("Island", me, ZoneType.Battlefield);
        card("Counterspell", me, ZoneType.Hand);
        rivalCasts(g, "Ponder", null);
        Recorder smart = new Recorder("CHOICE: 2"), fast = new Recorder("CHOICE: 2");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, smart, fast);
        Assert.assertNull(ctrl.chooseSpellAbilityToPlay(), "the seat passes on Ponder");
        int asked = smart.prompts.size() + fast.prompts.size();
        Assert.assertEquals(asked, 1);
        rivalCasts(g, "Show and Tell", null);
        ctrl.chooseSpellAbilityToPlay();
        Assert.assertEquals(smart.prompts.size() + fast.prompts.size(), 2, "Show and Tell on the stack was not asked about");
        Assert.assertTrue(smart.asked("Show and Tell") || fast.asked("Show and Tell"));
    }

    /** Puts the rival's {@code spell} on the stack aimed at {@code target}, on the rival's main phase. */
    static void rivalCasts(Game g, String spell, Card target) {
        Player rival = g.getPlayers().get(1);
        g.getPhaseHandler().devModeSet(PhaseType.MAIN1, rival);
        Card c = card(spell, rival, ZoneType.Hand);
        SpellAbility sa = c.getFirstSpellAbility();
        sa.setActivatingPlayer(rival);
        if (target != null) sa.getTargets().add(target);
        sa.setHostCard(g.getAction().moveToStack(c, sa));
        g.getStack().add(sa);
    }

    /** The opponent casts Reanimate on Griselbrand; the seat controls Soul-Guide Lantern ("exile each
     *  opponent's graveyard"). That answer neither targets nor counters, and the reactive filter
     *  passed priority for the seat without a question. Now it is asked, on the reasoning tier. */
    @Test
    public void anUntargetedAnswerToAnOpponentsSpellIsOffered() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card lantern = card("Soul-Guide Lantern", me, ZoneType.Battlefield);
        Card fatty = card("Griselbrand", rival, ZoneType.Graveyard);
        rivalCasts(g, "Reanimate", fatty);
        Recorder smart = new Recorder("CHOICE: 1"), fast = new Recorder("CHOICE: 1");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, smart, fast);
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertEquals(smart.prompts.size(), 1, "the response window was not asked on the reasoning tier");
        Assert.assertEquals(fast.prompts.size(), 0);
        Assert.assertTrue(smart.prompts.get(0).contains("Exile each opponent's graveyard"), smart.prompts.get(0));
        Assert.assertNotNull(r, "the seat chose option 1 and the harness passed anyway");
        Assert.assertEquals(r.get(0).getHostCard(), lantern);
    }

    /** A ritual is not an answer: with only mana production available the seat is still not asked. */
    @Test
    public void manaProductionAloneIsNotAResponse() {
        // The skip heuristics are off by default since 2026-10-15 (SKIP_HEURISTICS=1 opts in); this tests them on.
        boolean was = LlmSpikeGui.PlayerControllerLLM.SKIP_HEUR;
        LlmSpikeGui.PlayerControllerLLM.SKIP_HEUR = true;
        try {
            Game g = bareGame(PhaseType.MAIN1);
            Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
            card("Swamp", me, ZoneType.Battlefield);
            card("Dark Ritual", me, ZoneType.Hand);
            Card fatty = card("Griselbrand", rival, ZoneType.Graveyard);
            rivalCasts(g, "Reanimate", fatty);
            Recorder smart = new Recorder("CHOICE: 1"), fast = new Recorder("CHOICE: 1");
            LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, smart, fast);
            Assert.assertNull(ctrl.chooseSpellAbilityToPlay());
            Assert.assertEquals(smart.prompts.size() + fast.prompts.size(), 0, "asked whether to answer a spell with a ritual");
        } finally {
            LlmSpikeGui.PlayerControllerLLM.SKIP_HEUR = was;
        }
    }

    // ---- one candidate is not a forced choice --------------------------------------------------

    /**
     * Found by reading one match (runs/lines/blocks/native-lookahead, seed 50): the seat's only
     * creature attacked on six turns with `attackers(forced-only-legal)` and the model was never
     * asked. Not attacking is always legal.
     */
    @Test
    public void aSingleCreatureThatCanAttackIsTheModelsDecisionNotAForcedAttack() {
        for (String reply : new String[] {"PICKS: none", "PICKS: 1"}) {
            Game g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
            Player me = g.getPlayers().get(0), opp = g.getPlayers().get(1);
            Card guide = card("Elite Vanguard", me, ZoneType.Battlefield);
            guide.setSickness(false);
            Recorder smart = new Recorder(reply);
            LlmSpikeGui.PlayerControllerLLM seat = seat(g, smart, new Recorder(reply));
            forge.game.combat.Combat combat = new forge.game.combat.Combat(me);
            String log = logOf(() -> seat.declareAttackers(me, combat));
            Assert.assertEquals(smart.prompts.size(), 1, "the model must be asked about its single attacker: " + log);
            Assert.assertTrue(smart.prompts.get(0).contains("Elite Vanguard"), smart.prompts.get(0));
            Assert.assertFalse(log.contains("forced-only-legal"), log);
            Assert.assertEquals(combat.getAttackers().size(), reply.endsWith("none") ? 0 : 1, "reply " + reply + ": " + log);
            Assert.assertEquals(opp.getLife(), 20);
        }
    }

    /** A seat whose model never answers must not do better than one that passes (acceptance
     *  `gaming`): no answer used to be a land drop and an all-in attack. Now it is a pass and no attack. */
    @Test
    public void aSeatThatNeverAnswersNeitherPlaysALandNorAttacks() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Hand);
        Recorder dead = new Recorder(null);
        LlmSpikeGui.PlayerControllerLLM seat = seat(g, dead, new Recorder(null));
        String log = logOf(() -> Assert.assertNull(seat.chooseSpellAbilityToPlay(), "no answer is a pass"));
        Assert.assertTrue(log.contains("-> asking"), "the model was asked: " + log);
        Assert.assertTrue(log.contains("play(bridge-down)->PASS"), log);

        Game g2 = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        Player me2 = g2.getPlayers().get(0);
        card("Elite Vanguard", me2, ZoneType.Battlefield).setSickness(false);
        card("Savannah Lions", me2, ZoneType.Battlefield).setSickness(false);
        LlmSpikeGui.PlayerControllerLLM seat2 = seat(g2, new Recorder(null), new Recorder(null));
        forge.game.combat.Combat combat = new forge.game.combat.Combat(me2);
        String log2 = logOf(() -> seat2.declareAttackers(me2, combat));
        Assert.assertEquals(combat.getAttackers().size(), 0, "no answer is no attack: " + log2);
        Assert.assertTrue(log2.contains("attackers(bridge-down)->none"), log2);
    }

    /** Scry 1 / surveil 1: keeping the card or sending it away is the model's call. It was kept on
     *  top without asking (`surveil(forced-only-legal)->keep-all-top`). */
    @Test
    public void aSingleSurveiledCardIsTheModelsDecision() {
        for (String reply : new String[] {"PICKS: none", "PICKS: 1"}) {
            Game g = bareGame(PhaseType.MAIN1);
            Player me = g.getPlayers().get(0);
            Card top = card("Mountain", me, ZoneType.Library);
            Recorder smart = new Recorder(reply);
            LlmSpikeGui.PlayerControllerLLM seat = seat(g, smart, new Recorder(reply));
            forge.game.card.CardCollection one = new forge.game.card.CardCollection();
            one.add(top);
            org.apache.commons.lang3.tuple.ImmutablePair<forge.game.card.CardCollection, forge.game.card.CardCollection>[] out =
                    new org.apache.commons.lang3.tuple.ImmutablePair[1];
            String log = logOf(() -> out[0] = seat.arrangeForSurveil(one));
            Assert.assertEquals(smart.prompts.size(), 1, "the model must be asked about the one card: " + log);
            Assert.assertFalse(log.contains("forced-only-legal"), log);
            Assert.assertEquals(out[0].getLeft().size(), reply.endsWith("none") ? 0 : 1, "kept on top, reply " + reply);
            Assert.assertEquals(out[0].getRight().size(), reply.endsWith("none") ? 1 : 0, "to the graveyard, reply " + reply);
        }
    }

    /**
     * Recorded (runs/lines/blocks/native-lookahead, seed 50, turn 6): the second ask of a play
     * decision was one sentence, "...CHOICE: <a number 1..7 from the list above>", with no list
     * above. The model searched its archive for the question and the decision was lost.
     */
    @Test
    public void theRetryOfAPlayDecisionCarriesTheStateAndTheMenuAgain() {
        String state = "== Turn 6 MAIN1 ==\nYOU [Seat] life 20\nDECISION: You have priority. Choose ONE action:\n  1) CAST Lightning Bolt\n  2) PASS (do nothing)";
        String retry = LlmSpikeGui.PlayerControllerLLM.playRetryPrompt(state, 2);
        Assert.assertTrue(retry.startsWith(state), retry);
        Assert.assertTrue(retry.contains("1) CAST Lightning Bolt") && retry.contains("2) PASS"));
        Assert.assertTrue(retry.endsWith("CHOICE: <a number 1..2 from the list above>.)"), retry);

        // End to end: a first reply with no choice, then an answer. The second prompt is the full one.
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        card("Lightning Bolt", me, ZoneType.Hand);
        List<String> seen = new ArrayList<>();   // not `prompts`: that is a field of Recorder
        Recorder smart = new Recorder("CHOICE: 2") {
            @Override public String ask(String prompt) {
                seen.add(prompt);
                return seen.size() == 1 ? "PLAN: bolt them later, and I ran out of room before" : "CHOICE: 2";
            }
        };
        LlmSpikeGui.PlayerControllerLLM seat = seat(g, smart, smart);
        logOf(() -> seat.chooseSpellAbilityToPlay());
        Assert.assertTrue(seen.size() >= 2, "expected a retry, got " + seen.size() + " asks");
        String second = seen.get(1);
        Assert.assertTrue(second.contains("Lightning Bolt") && second.contains("PASS"), "the retry lost the menu: " + second);
        Assert.assertTrue(second.contains("Your previous reply had no parseable choice"), second);
        Assert.assertTrue(second.length() > 300, "the retry is the bare correction again: " + second);
    }
}
