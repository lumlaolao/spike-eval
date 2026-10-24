package forge.spike;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.function.Function;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import forge.card.ColorSet;
import forge.card.ICardFace;
import forge.card.MagicColor;
import forge.game.Game;
import forge.game.GameEntity;
import forge.game.GameLogEntry;
import forge.game.GameLogEntryType;
import forge.game.card.Card;
import forge.game.card.CardCollection;
import forge.game.combat.Combat;
import forge.game.combat.CombatUtil;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.player.PlayerActionConfirmMode;
import forge.game.player.PlayerController.BinaryChoiceType;
import forge.game.spellability.OptionalCostValue;
import forge.game.spellability.SpellAbility;
import forge.game.zone.ZoneType;
import forge.model.FModel;
import forge.util.collect.FCollection;

import org.testng.Assert;
import org.testng.ITestResult;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import static forge.spike.LineFindingProbeTest.Recorder;
import static forge.spike.LineFindingProbeTest.bareGame;
import static forge.spike.LineFindingProbeTest.card;
import static forge.spike.LineFindingProbeTest.seat;

/**
 * Golden checks beyond one scenario (docs/STANDARDS.md section 8): every decision type the seat owns
 * is driven through the LIVE controller by at least one test here, the model's answer is asserted to
 * be the one used, and the ask contract (AskContract) runs on every prompt asked. The last test fails
 * when a type the seat can ask has no test, or a test asks a type this list does not know.
 */
public class GoldenChecksTest {
    /** Every decision type the seat asks the model (docs/harness/GOLDEN-CHECKS.md). */
    static final Set<String> OWNED = new TreeSet<>(Arrays.asList(
            "play", "target", "targets", "mana-sources", "phyrexian", "modes", "number", "number-value",
            "ability-to-play", "play-from-effect", "mulligan", "mulligan-bottom", "starting-player",
            "attackers", "attack-target", "blockers", "damage-assign", "damage-order", "vote", "entity", "zone-change",
            "discard", "cleanup-discard", "delve", "sacrifice", "cards-for-effect", "prevent-effect",
            "optional-costs", "trigger-order", "reveal", "scry", "surveil", "order", "confirm",
            "confirm-trigger", "confirm-payment", "confirm-replacement", "binary", "color",
            "color-colorless", "some-type", "protection", "card-name",
            // 2026-10-15: choices Forge's AI made outright until then (work plan priority 0, A).
            "pile", "opening-hand", "colors", "counter-type", "destroy", "keyword", "spell-for-effect", "clash",
            "confirm-static", "card-face", "keyword-cost", "cost-reduction", "discard-unless", "convoke", "new-targets",
            "harmonize", "attack-bands", "exert", "enlist", "splice", "flip", "shield", "roll-ignore", "roll-reroll",
            "roll-modify", "roll-swap", "sticker", "crank", "sprocket", "sector", "starting-hand", "assist", "assist-pay",
            // 2026-10-17: asked since floating mana is on the menu; no golden check named it until delve met a pool.
            "mana-from-pool",
            // 2026-10-20 (epoch 66): an attack the engine refused, recorded as answered and overruled.
            "attackers-refused"));

    /** Types a model answered in this class (tier smart or fast), across every test. */
    static final Set<String> ANSWERED = Collections.synchronizedSet(new TreeSet<>());

    private final List<String> asked = Collections.synchronizedList(new ArrayList<>());
    /** The source every ask of the current test must name with its rules text, when it has one. */
    private String sourceName, sourceText;

    @BeforeClass
    public void init() { new LineFindingProbeTest().initializeModel(); }

    @BeforeMethod
    public void faithful() {
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL;
        asked.clear();
        sourceName = null; sourceText = null;
        LlmSpikeGui.PlayerControllerLLM.TEST_ASK_TAP = asked::add;
        LlmSpikeGui.PlayerControllerLLM.TEST_DECISION_TAP = (type, tier) -> {
            if ("smart".equals(tier) || "fast".equals(tier)) ANSWERED.add(type);
        };
    }

    @AfterMethod(alwaysRun = true)
    public void everyAskKeepsTheContract(ITestResult result) {
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null;
        LlmSpikeGui.PlayerControllerLLM.TEST_ASK_TAP = null;
        LlmSpikeGui.PlayerControllerLLM.TEST_DECISION_TAP = null;
        if (!result.isSuccess()) return;
        AskContract.check(result.getName(), asked);
        if (sourceName != null) AskContract.namesSource(result.getName(), asked, sourceName, sourceText);
    }

    // ------------------------------------------------------------------ helpers

    /** A scripted model: the first rule whose needle the prompt contains answers; else {@code dflt}. */
    static class Script extends Recorder {
        private final Map<String, Function<String, String>> rules = new LinkedHashMap<>();
        private final String dflt;
        Script(String dflt) { super(dflt); this.dflt = dflt; }
        Script on(String needle, Function<String, String> reply) { rules.put(needle, reply); return this; }
        @Override public String ask(String prompt) {
            prompts.add(prompt);
            String decision = prompt.substring(Math.max(0, prompt.lastIndexOf("DECISION:")));
            for (Map.Entry<String, Function<String, String>> r : rules.entrySet())
                if (decision.contains(r.getKey())) return r.getValue().apply(prompt);
            return dflt;
        }
    }

    /** The menu number of the option whose label starts with {@code label}. */
    static int opt(String prompt, String label) {
        String decision = prompt.substring(Math.max(0, prompt.lastIndexOf("DECISION:")));
        Matcher m = Pattern.compile("(?m)^\\s*(\\d+)\\) " + Pattern.quote(label)).matcher(decision);
        Assert.assertTrue(m.find(), "no option " + label + ": " + decision);
        return Integer.parseInt(m.group(1));
    }

    static Function<String, String> choice(String label) { return p -> "CHOICE: " + opt(p, label); }

    static Function<String, String> picks(String... labels) {
        return p -> {
            StringBuilder b = new StringBuilder("PICKS: ");
            for (int i = 0; i < labels.length; i++) b.append(i == 0 ? "" : ",").append(opt(p, labels[i]));
            return b.toString();
        };
    }

    private void source(Card c) { sourceName = c.getName(); sourceText = c.getOracleText(); }

    private static SpellAbility spell(Card c, Player me) {
        SpellAbility sa = c.getFirstSpellAbility();
        sa.setActivatingPlayer(me);
        return sa;
    }

    // ------------------------------------------------------------------ the play loop

    /** The engine patch (docs/harness/ENGINE-PATCHES.md): Forge's AI payment fixed the colour Lotus Petal makes for
     *  a generic shard (the first of WUBRG) before the colour question, so the seat was offered one colour. Now
     *  it is offered all five, and the mana it names is the mana spent. */
    @Test
    public void theColourAnAnyColourSourceMakesForGenericIsTheSeats() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Birds of Paradise", me, ZoneType.Battlefield);   // any colour, as Lotus Petal
        card("Sol Ring", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1")
                .on("You have priority", choice("CAST Sol Ring"))
                .on("Choose a color", choice("black"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, "the seat cast Sol Ring");
        ctrl.playChosenSpellAbility(r.get(0));
        String ask = PromptFidelityTest.promptWith(model, "Choose a color");
        Assert.assertTrue(ask.contains("you are paying {1} for Sol Ring"), "the ask says what the colour pays for:\n" + ask);
        for (String c : new String[] {"white", "blue", "black", "red", "green"})
            Assert.assertTrue(ask.contains(") " + c), "the colour ask offers " + c + ":\n" + ask);
        boolean black = false;
        for (forge.game.mana.Mana m : r.get(0).getPayingMana()) black |= (m.getColor() & forge.card.MagicColor.BLACK) != 0;
        Assert.assertTrue(black, "the mana spent is the colour the seat named: " + r.get(0).getPayingMana());
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** Rules pilot RP_017 (2026-10-18): at 1 life with City of Brass, Angel's Grace was never offered. Forge AI's
     *  mana sorter skips a source whose tap damage would be lethal; tapping it is legal (owner: a legal action is
     *  never hidden), and here the Grace resolves before City's trigger. */
    @Test
    public void aSourceWhoseTapDamageIsLethalStillPaysForTheSeat() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        me.setLife(1, null);
        card("City of Brass", me, ZoneType.Battlefield);
        card("Angel's Grace", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        ctrl.chooseSpellAbilityToPlay();
        String ask = PromptFidelityTest.promptWith(model, "You have priority");
        Assert.assertTrue(ask.contains("CAST Angel's Grace"), "Angel's Grace is on the menu:\n" + ask);
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** pr-review of PR #268 (2026-10-19): the patch above lifted Forge's exclusion outright, so at 1 life with City of
     *  Brass and Llanowar Elves the engine's payment plan tapped City (any-colour lands sort before creatures) and
     *  the seat died paying {1}. A lethal source pays last for a harness seat: still there, never first. */
    @Test
    public void aSourceWhoseTapDamageIsLethalPaysLast() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        me.setLife(1, null);
        Card city = card("City of Brass", me, ZoneType.Battlefield);
        Card elves = card("Llanowar Elves", me, ZoneType.Battlefield);
        elves.setSickness(false);
        seat(g, new Script("CHOICE: 1"), new Script("CHOICE: 1"));
        forge.game.card.CardCollection order = forge.ai.ComputerUtilMana.getAvailableManaSources(me, true);
        Assert.assertTrue(order.contains(city), "City of Brass still pays: " + order);
        Assert.assertTrue(order.indexOf(elves) >= 0 && order.indexOf(elves) < order.indexOf(city),
                "the lethal source pays after the safe one: " + order);
    }

    /** Rules pilot RP_006 (2026-10-18): Finale of Promise ("up to one target instant card and/or up to one target
     *  sorcery card") was never offered with nothing in the graveyard: the menu demanded at least one target. A spell
     *  whose targets are all "up to" is cast with none. */
    @Test
    public void aSpellWithOnlyUpToTargetsIsOfferedWithNoTarget() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        card("Mountain", me, ZoneType.Battlefield);
        card("Finale of Promise", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        ctrl.chooseSpellAbilityToPlay();
        String ask = PromptFidelityTest.promptWith(model, "You have priority");
        Assert.assertTrue(ask.contains("CAST Finale of Promise"), "Finale of Promise is on the menu:\n" + ask);
    }

    /** runs/c3/ant-storm-default T7 (2026-10-16): {B:3} floating and two Lotus Petals, Ad Nauseam (3BB) offered as
     *  castable and chosen; the seat named a colour for one Petal, then "Didn't find what to pay for {1}" and nothing
     *  was cast. Two any-colour sources paying generic shards must both pay. */
    @Test
    public void twoAnyColourSourcesBothPayTheGenericPart() {
        for (boolean floating : new boolean[] {true, false}) {
            Game g = bareGame(PhaseType.MAIN1);
            Player me = g.getPlayers().get(0);
            Card petal1 = card("Lotus Petal", me, ZoneType.Battlefield);
            Card petal2 = card("Lotus Petal", me, ZoneType.Battlefield);
            if (floating) {
                Card src = card("Swamp", me, ZoneType.Battlefield);
                for (int i = 0; i < 3; i++) me.getManaPool().addManaNoEvent(new forge.game.mana.Mana(forge.card.MagicColor.BLACK, src, null, me));
                src.setTapped(true);
            } else {
                for (int i = 0; i < 3; i++) card("Swamp", me, ZoneType.Battlefield);
            }
            Card adn = card("Ad Nauseam", me, ZoneType.Hand);
            Script model = new Script("CHOICE: 1")
                    .on("You have priority", choice("CAST Ad Nauseam"))
                    .on("Choose a color", choice("black"))
                    .on("MANA:", p -> "PICKS: NONE");
            LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
            List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
            Assert.assertNotNull(r, "Ad Nauseam is offered and chosen (floating=" + floating + ")");
            ctrl.playChosenSpellAbility(r.get(0));
            Assert.assertTrue(petal1.isInZone(ZoneType.Graveyard) || !petal1.isInZone(ZoneType.Battlefield), "Petal 1 paid (floating=" + floating + ")");
            Assert.assertTrue(petal2.isInZone(ZoneType.Graveyard) || !petal2.isInZone(ZoneType.Battlefield), "Petal 2 paid (floating=" + floating + ")");
            Assert.assertTrue(adn.isInZone(ZoneType.Stack), "Ad Nauseam is on the stack (floating=" + floating + "), petals: "
                    + petal1.getZone() + " " + petal2.getZone());
        }
    }

    /** runs/c3/ant-storm-default T7, as it was played: Dark Ritual, Lotus Petal, then Ad Nauseam for {B:3} and both Petals.
     *  Asked mid-payment which colour the first Petal makes (the engine patch), the seat's prompt test-paid Tendrils
     *  for its "(castable)" tag; that test payment cleared and refilled the AI memory the real payment was using
     *  (PAYS_TAP_COST), the second Petal read as already used, and Ad Nauseam's last {1} went unpaid. Tendrils in hand
     *  is what makes the prompt test-pay something. */
    @Test
    public void ritualThenPetalThenAdNauseamIsPaid() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Swamp", me, ZoneType.Battlefield);
        card("Lotus Petal", me, ZoneType.Battlefield);
        card("Dark Ritual", me, ZoneType.Hand);
        card("Lotus Petal", me, ZoneType.Hand);
        card("Tendrils of Agony", me, ZoneType.Hand);
        Card adn = card("Ad Nauseam", me, ZoneType.Hand);
        String[] next = {"CAST Dark Ritual"};
        Script model = new Script("CHOICE: 1")
                .on("You have priority", p -> choice(next[0]).apply(p))
                .on("Choose a color", choice("black"))
                .on("MANA:", picks("Lotus Petal"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        for (String play : new String[] {"CAST Dark Ritual", "CAST Lotus Petal", "CAST Ad Nauseam"}) {
            next[0] = play;
            List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
            Assert.assertNotNull(r, play + " offered");
            ctrl.playChosenSpellAbility(r.get(0));
            if (!play.endsWith("Ad Nauseam")) { while (!g.getStack().isEmpty()) g.getStack().resolveStack(); }
        }
        Assert.assertTrue(adn.isInZone(ZoneType.Stack), "Ad Nauseam is on the stack");
        Assert.assertEquals(me.getCardsIn(ZoneType.Graveyard).stream().filter(c -> c.getName().equals("Lotus Petal")).count(), 2L,
                "both Petals paid for Ad Nauseam (3 floating + 2 Petals = 5)");
        Assert.assertTrue(model.prompts.stream().anyMatch(q -> q.contains("Choose a color") && q.contains("Ad Nauseam")),
                "the colour was asked in the middle of the payment");
    }

    /** Doomsday's own decisions (owner, 2026-10-16: combo lines must play out). The five cards and their order are the
     *  combo; in the C3 games Doomsday never resolved for the seat, so this was never seen. The seat picks the five from
     *  library and graveyard, then stacks them; the library is exactly that pile, and Forge's AI decides nothing. */
    @Test
    public void doomsdaysPileAndItsOrderAreTheSeats() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        for (int i = 0; i < 3; i++) card("Swamp", me, ZoneType.Battlefield);
        Card dd = card("Doomsday", me, ZoneType.Hand);
        String[] lib = {"Island", "Brainstorm", "Ponder", "Thassa's Oracle", "Gitaxian Probe", "Lion's Eye Diamond",
                "Swamp", "Force of Will", "Daze", "Dark Ritual", "Cabal Ritual", "Preordain"};
        for (String n : lib) card(n, me, ZoneType.Library);
        card("Street Wraith", me, ZoneType.Graveyard);
        String[] pile = {"Thassa's Oracle", "Ponder", "Lion's Eye Diamond", "Street Wraith", "Gitaxian Probe"};
        Script model = new Script("CHOICE: 1")
                .on("You have priority", choice("CAST Doomsday"))
                .on("MANA:", p -> "PICKS: NONE")
                .on("in your preferred order", picks(pile))
                .on("DECISION:", p -> {
                    // A pick from library/graveyard: the next card of the pile still offered.
                    String d = p.substring(Math.max(0, p.lastIndexOf("DECISION:")));
                    for (String n : pile) {
                        Matcher m = Pattern.compile("(?m)^\\s*(\\d+)\\) " + Pattern.quote(n)).matcher(d);
                        if (m.find()) return "CHOICE: " + m.group(1);
                    }
                    return "CHOICE: 1";
                });
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, "Doomsday offered");
        ctrl.playChosenSpellAbility(r.get(0));
        while (!g.getStack().isEmpty()) g.getStack().resolveStack();
        Assert.assertTrue(me.getCardsIn(ZoneType.Graveyard).stream().anyMatch(c -> c.getName().equals("Doomsday")), "Doomsday resolved");
        List<String> library = new java.util.ArrayList<>();
        for (Card c : me.getCardsIn(ZoneType.Library)) library.add(c.getName());
        Assert.assertEquals(library.size(), 5, "the library is the pile: " + library);
        Assert.assertEqualsNoOrder(library.toArray(), pile, "the five the seat picked: " + library);
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0, "Forge's AI decided nothing");
        // Each later pick says what is already in the pile (runs/loop/g08 T8 searched its archive for its own picks).
        String last = model.prompts.stream().filter(q -> q.contains("(5 / 5)")).reduce((a, b) -> b).orElse("");
        String said = last.substring(Math.max(0, last.indexOf("Already picked for this effect:")));
        Assert.assertTrue(said.startsWith("Already picked for this effect:"), "the fifth pick names the first four: " + last);
        for (String n : java.util.Arrays.copyOf(pile, 4)) Assert.assertTrue(said.contains(n), n + " is named: " + said);
        Assert.assertFalse(model.prompts.stream().filter(q -> q.contains("(1 / 5)")).anyMatch(q -> q.contains("Already picked")),
                "the first pick has nothing picked yet");
    }

    /** Ad Nauseam's repeat is the seat's (owner, 2026-10-16: combo lines must play out): each "repeat?" is asked, and
     *  the seat stops when it says so. */
    @Test
    public void adNauseamRepeatsAsOftenAsTheSeatSays() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        for (int i = 0; i < 5; i++) card("Swamp", me, ZoneType.Battlefield);
        card("Ad Nauseam", me, ZoneType.Hand);
        for (String n : new String[] {"Dark Ritual", "Lotus Petal", "Brainstorm", "Ponder", "Duress", "Island"}) card(n, me, ZoneType.Library);
        int[] repeats = {0};
        Script model = new Script("CHOICE: 1")
                .on("You have priority", choice("CAST Ad Nauseam"))
                .on("MANA:", p -> "PICKS: NONE")
                .on("Ad Nauseam", p -> ++repeats[0] <= 2 ? "CHOICE: 1" : "CHOICE: 2");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        int hand0 = me.getCardsIn(ZoneType.Hand).size();
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r);
        ctrl.playChosenSpellAbility(r.get(0));
        while (!g.getStack().isEmpty()) g.getStack().resolveStack();
        List<String> asks = new java.util.ArrayList<>();
        for (String q : model.prompts) if (q.contains("repeat this process again")) asks.add(q);
        String second = asks.size() > 1 ? asks.get(1) : "";
        System.out.println("[ADN-2ND] " + second.replaceAll("(?s).*?(YOU \\[Seat\\][^\n]*\n[^\n]*\n).*", "$1"));        Assert.assertTrue(repeats[0] >= 1, "the repeat was asked of the seat");
        Assert.assertEquals(me.getCardsIn(ZoneType.Hand).size(), hand0 - 1 + 3, "one reveal plus the two repeats the seat chose");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** Tendrils with storm: the copies are made and resolve, and choosing new targets for them is the seat's. */
    @Test
    public void tendrilsStormCopiesResolveAndTheirTargetsAreTheSeats() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        for (int i = 0; i < 4; i++) card("Swamp", me, ZoneType.Battlefield);
        card("Lotus Petal", me, ZoneType.Hand);
        card("Lotus Petal", me, ZoneType.Hand);
        card("Tendrils of Agony", me, ZoneType.Hand);
        card("Dark Ritual", me, ZoneType.Hand);
        String[] next = {"CAST Lotus Petal"};
        Script model = new Script("CHOICE: 1")
                .on("You have priority", p -> choice(next[0]).apply(p))
                .on("Choose the TARGET", choice("opponent"))
                .on("MANA:", p -> "PICKS: NONE")
                .on("go on the stack at the same time", p -> "PICKS: 1,2");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        String[] stackLine = {""};
        for (String play : new String[] {"CAST Lotus Petal", "CAST Lotus Petal", "CAST Tendrils of Agony"}) {
            next[0] = play;
            List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
            Assert.assertNotNull(r, play + " offered");
            ctrl.playChosenSpellAbility(r.get(0));
            g.getTriggerHandler().runWaitingTriggers();
            g.getStack().addAllTriggeredAbilitiesToStack();
            if (play.contains("Tendrils")) {
                stackLine[0] = stackLineFor(g, me);
                // The seat holds priority over its storm trigger (a Dark Ritual in hand): that ask's state is linted too.
                next[0] = "PASS";
                Assert.assertNull(ctrl.chooseSpellAbilityToPlay(), "the seat passed over its storm trigger");
                Assert.assertTrue(model.prompts.get(model.prompts.size() - 1).contains("makes 2 copies"), "the ask shows the trigger");
            }
            while (!g.getStack().isEmpty()) {
                g.getStack().resolveStack();
                g.getStack().addAllTriggeredAbilitiesToStack();
            }
        }
        // The storm trigger says what it does, from the engine's own count (AskContract keeps Forge's notes out of the ask).
        Assert.assertTrue(stackLine[0].contains("trigger of Tendrils of Agony") && stackLine[0].contains("makes 2 copies"), stackLine[0]);
        // The two copies are the same until each gets its target, so there is no order to choose.
        Assert.assertTrue(model.prompts.stream().noneMatch(q -> q.contains("go on the stack at the same time")),
                "the seat was asked to order identical copies: " + model.prompts.stream().filter(q -> q.contains("go on the stack at the same time")).map(q -> q.substring(q.lastIndexOf("DECISION:"))).findFirst().orElse(""));
        long copyTargetAsks = model.prompts.stream().filter(q -> q.substring(q.lastIndexOf("DECISION:")).startsWith("DECISION: Choose the TARGET")).count();
        Assert.assertEquals(copyTargetAsks, 3L, "the original's target and each copy's were the seat's");
        Assert.assertEquals(model.prompts.stream().filter(q -> q.contains("Choose the TARGET for a copy of Tendrils")).count(), 2L,
                "each copy's question says it is a copy");
        Assert.assertEquals(rival.getLife(), 20 - 6, "Tendrils and its two storm copies each drained 2");
        Assert.assertEquals(me.getLife(), 20 + 6);
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** The STACK line the seat's faithful state shows now. */
    static String stackLineFor(Game g, Player viewer) {
        String r = new SeatObservation(g, viewer).render("seat", LlmSpikeGui.ContextProfile.V4_FAITHFUL, "", null, 0);
        int at = r.indexOf("STACK (engine, top first): ");
        return r.substring(at, r.indexOf('\n', at));
    }

    /** A counterspell counters: its target is the spell on the stack, not the spell's card. Luna's Force of Will
     *  "countered" a Dark Ritual that then resolved (C3 Doomsday, whole-game arm, T8): getAllCandidates gives the
     *  card, and Forge's counter effect reads only targeted spell abilities. */
    @Test
    public void aCounterspellCountersTheSpell() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Island", me, ZoneType.Battlefield);
        card("Island", me, ZoneType.Battlefield);
        card("Counterspell", me, ZoneType.Hand);
        LineFindingProbeTest.rivalCasts(g, "Ponder", null);
        Script model = new Script("CHOICE: 1").on("You have priority", choice("CAST Counterspell"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, "Counterspell was offered");
        Assert.assertTrue(ctrl.playChosenSpellAbility(r.get(0)));
        Assert.assertTrue(g.getStack().peekAbility().getTargets().getFirstTargetedSpell() != null, "it targets the spell itself");
        g.getStack().resolveStack();
        Assert.assertTrue(g.getStack().isEmpty(), "Ponder did not stay on the stack");
        Assert.assertTrue(rival.getCardsIn(ZoneType.Graveyard).stream().anyMatch(c -> c.getName().equals("Ponder")), "Ponder was countered");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** Stifle's target is an ability on the stack, named as one: it was never offered (no card is a legal target). */
    @Test
    public void stifleCountersAnAbilityItNames() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Island", me, ZoneType.Battlefield);
        card("Stifle", me, ZoneType.Hand);
        g.getPhaseHandler().devModeSet(PhaseType.MAIN1, rival);
        Card delta = card("Polluted Delta", rival, ZoneType.Battlefield);
        SpellAbility fetch = delta.getSpellAbilities().stream().filter(SpellAbility::isActivatedAbility).findFirst().get();
        fetch.setActivatingPlayer(rival);
        forge.ai.ComputerUtil.playStack(fetch, rival, g);
        Assert.assertEquals(g.getStack().size(), 1, "fixture: the fetch ability is on the stack");
        Script model = new Script("CHOICE: 1").on("You have priority", choice("CAST Stifle"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, "Stifle was offered");
        Assert.assertTrue(ctrl.playChosenSpellAbility(r.get(0)));
        SpellAbility aimed = g.getStack().peekAbility().getTargets().getFirstTargetedSpell();
        Assert.assertTrue(aimed != null && aimed.isActivatedAbility() && aimed.getHostCard().getName().equals("Polluted Delta"),
                "Stifle targets the fetch ability: " + aimed);
        Assert.assertTrue(model.prompts.stream().noneMatch(q -> q.contains("Choose the TARGET")) || model.prompts.stream()
                .anyMatch(q -> q.contains("ability of Polluted Delta")), "the target is named as an ability");
        int lands = rival.getCardsIn(ZoneType.Battlefield).size();
        g.getStack().resolveStack();
        Assert.assertTrue(g.getStack().isEmpty(), "the fetch ability was countered, not left on the stack");
        Assert.assertEquals(rival.getCardsIn(ZoneType.Battlefield).size(), lands, "no land was fetched");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** A number the cast announces (Announce$ X): Disrupting Shoal's pitch cost exiles a blue card with mana value X and
     *  counters a spell with mana value X. The seat says X when it casts it (announceRequirements, surface S3). */
    @Test
    public void aShoalsXIsAnnouncedByTheSeat() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Disrupting Shoal", me, ZoneType.Hand);
        card("Brainstorm", me, ZoneType.Hand);
        LineFindingProbeTest.rivalCasts(g, "Ponder", null);
        g.getAction().checkStaticAbilities();
        Script model = new Script("CHOICE: 1")
                .on("You have priority", choice("CAST Disrupting Shoal"))
                .on("Every integer", p -> "NUMBER: 1")
                .on("Choose the TARGET", choice("Ponder"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, "the pitch was offered");
        Assert.assertTrue(ctrl.playChosenSpellAbility(r.get(0)), "the Shoal was cast");
        Assert.assertTrue(model.prompts.stream().anyMatch(q -> q.substring(q.lastIndexOf("DECISION:")).startsWith("DECISION: Choose X for CAST Disrupting Shoal")),
                "the seat was asked X");
        Assert.assertTrue(me.getCardsIn(ZoneType.Exile).stream().anyMatch(c -> c.getName().equals("Brainstorm")), "Brainstorm was pitched");
        Assert.assertEquals(g.getStack().peekAbility().getXManaCostPaid(), Integer.valueOf(1), "X is on the spell");
        g.getStack().resolveStack();
        Assert.assertTrue(rival.getCardsIn(ZoneType.Graveyard).stream().anyMatch(c -> c.getName().equals("Ponder")), "Ponder was countered");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** X in a cost other than mana (Toxic Deluge: pay X life) is the seat's: the play loop announced only mana X, so it
     *  was cast at X=0 and killed nothing. */
    @Test
    public void toxicDelugesLifeIsTheSeats() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        for (int i = 0; i < 3; i++) card("Swamp", me, ZoneType.Battlefield);
        card("Toxic Deluge", me, ZoneType.Hand);
        card("Grizzly Bears", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1").on("You have priority", choice("CAST Toxic Deluge"))
                .on("Choose X for CAST Toxic Deluge", p -> p.contains("Every integer") ? "NUMBER: 2" : "CHOICE: " + opt(p, "2"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, "Toxic Deluge was offered");
        Assert.assertTrue(ctrl.playChosenSpellAbility(r.get(0)));
        Assert.assertEquals(me.getLife(), 18, "X = 2 life was paid");
        g.getStack().resolveStack();
        g.getAction().checkStateEffects(true);
        Assert.assertTrue(rival.getCardsIn(ZoneType.Battlefield).isEmpty(), "the Bears got -2/-2");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    // ------------------------------------------------------------------ ways to play a card (phase 1c, S3)
    // Each way the engine lets a card be played (Forge's AlternativeCost and OptionalCost), chosen from the seat's real
    // menu, paid through its real payment and resolved; the board is checked. The `surfaces` stage counts them.

    /** The option of the seat's menu whose text contains {@code needle}. */
    static Function<String, String> choiceWith(String needle) {
        return p -> {
            String d = p.substring(p.lastIndexOf("DECISION:"));
            Matcher m = Pattern.compile("(?m)^\\s*(\\d+)\\) [^\\n]*" + Pattern.quote(needle)).matcher(d);
            Assert.assertTrue(m.find(), "no option with " + needle + ": " + d);
            return "CHOICE: " + m.group(1);
        };
    }

    /** Plays what the seat picks from its menu, then resolves the stack and its triggers and checks state. */
    static void playAndResolve(LlmSpikeGui.PlayerControllerLLM ctrl, Game g, String what) {
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, what + " was offered");
        Assert.assertTrue(ctrl.playChosenSpellAbility(r.get(0)), what + " was played");
        resolveAll(g);
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    static void resolveAll(Game g) {
        for (int i = 0; i < 50; i++) {
            g.getTriggerHandler().runWaitingTriggers();
            g.getStack().addAllTriggeredAbilitiesToStack();
            if (g.getStack().isEmpty()) break;
            g.getStack().resolveStack();
        }
        g.getAction().checkStateEffects(true);
    }

    static boolean has(Player p, ZoneType z, String name) {
        return p.getCardsIn(z).stream().anyMatch(c -> c.getName().equals(name));
    }

    static void lands(Player me, String... names) { for (String n : names) card(n, me, ZoneType.Battlefield); }

    @Test
    public void evokeFuryPitchesARedCardAndIsSacrificed() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Fury", me, ZoneType.Hand);
        card("Lightning Bolt", me, ZoneType.Hand);
        card("Grizzly Bears", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("(Evoke)"))
                .on("go on the stack at the same time", p -> "PICKS: 1,2")
                .on("Choose TARGETS", picks("Grizzly Bears"));
        playAndResolve(seat(g, model, model), g, "Fury's evoke");
        Assert.assertTrue(has(me, ZoneType.Exile, "Lightning Bolt"), "Bolt was pitched");
        Assert.assertTrue(has(me, ZoneType.Graveyard, "Fury"), "Fury was sacrificed");
        Assert.assertFalse(has(rival, ZoneType.Battlefield, "Grizzly Bears"), "Fury's damage killed the Bears");
    }

    @Test
    public void overloadBouncesEach() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        lands(me, "Island", "Island", "Island", "Island", "Island", "Island", "Island");
        card("Cyclonic Rift", me, ZoneType.Hand);
        card("Grizzly Bears", rival, ZoneType.Battlefield);
        card("Llanowar Elves", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("(Overload)"));
        playAndResolve(seat(g, model, model), g, "Cyclonic Rift's overload");
        Assert.assertTrue(has(rival, ZoneType.Hand, "Grizzly Bears") && has(rival, ZoneType.Hand, "Llanowar Elves"), "both bounced");
    }

    @Test
    public void dashGivesHaste() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Mountain", "Mountain", "Mountain");
        card("Goblin Heelcutter", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("(Dash)"));
        playAndResolve(seat(g, model, model), g, "Heelcutter's dash");
        Card h = me.getCardsIn(ZoneType.Battlefield).stream().filter(c -> c.getName().equals("Goblin Heelcutter")).findFirst().orElse(null);
        Assert.assertNotNull(h, "Heelcutter is on the battlefield");
        Assert.assertTrue(h.hasKeyword(forge.game.keyword.Keyword.HASTE), "dashed: it has haste");
    }

    @Test
    public void bestowMakesAnAura() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Forest", "Forest", "Forest", "Forest", "Forest");
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Boon Satyr", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("(Bestow)"));
        playAndResolve(seat(g, model, model), g, "Boon Satyr's bestow");
        Assert.assertEquals(bears.getNetPower(), 2 + 4, "the Satyr is attached to the Bears");
        Assert.assertTrue(bears.isEnchanted());
    }

    @Test
    public void blitzPaysLifeAndGivesHaste() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Swamp", "Swamp", "Swamp", "Swamp");
        card("Tenacious Underdog", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("(Blitz)"));
        playAndResolve(seat(g, model, model), g, "Underdog's blitz");
        Assert.assertEquals(me.getLife(), 18, "2 life paid");
        Card u = me.getCardsIn(ZoneType.Battlefield).stream().filter(c -> c.getName().equals("Tenacious Underdog")).findFirst().orElse(null);
        Assert.assertTrue(u != null && u.hasKeyword(forge.game.keyword.Keyword.HASTE), "blitzed: on the battlefield with haste");
    }

    @Test
    public void emergeSacrificesTheCreatureTheSeatNames() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Island", "Island", "Island", "Island", "Island", "Island");
        card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Llanowar Elves", me, ZoneType.Battlefield);
        card("Elder Deep-Fiend", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("(Emerge)"))
                .on("you SACRIFICE one of these", picks("Llanowar Elves"));
        playAndResolve(seat(g, model, model), g, "Elder Deep-Fiend's emerge");
        Assert.assertTrue(has(me, ZoneType.Battlefield, "Elder Deep-Fiend"), "it was cast");
        Assert.assertTrue(has(me, ZoneType.Graveyard, "Llanowar Elves") && has(me, ZoneType.Battlefield, "Grizzly Bears"),
                "the creature the seat named was sacrificed");
    }

    @Test
    public void awakenAnimatesALand() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Island", "Island", "Island", "Island", "Island", "Island");
        for (int i = 0; i < 3; i++) card("Island", me, ZoneType.Library);
        card("Coastal Discovery", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("(Awaken)"));
        playAndResolve(seat(g, model, model), g, "Coastal Discovery's awaken");
        Assert.assertTrue(me.getCardsIn(ZoneType.Battlefield).stream().anyMatch(c -> c.isLand() && c.isCreature()
                && c.getCounters(forge.game.card.CounterEnumType.P1P1) == 4), "a land got four +1/+1 counters");
        Assert.assertEquals(me.getCardsIn(ZoneType.Hand).size(), 2, "two cards drawn");
    }

    @Test
    public void escapeExilesFiveOthersFromTheGraveyard() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Tropical Island", "Tropical Island", "Tropical Island", "Tropical Island");
        card("Uro, Titan of Nature's Wrath", me, ZoneType.Graveyard);
        for (int i = 0; i < 5; i++) card("Ponder", me, ZoneType.Graveyard);
        for (int i = 0; i < 3; i++) card("Island", me, ZoneType.Library);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("(Escape)"));
        playAndResolve(seat(g, model, model), g, "Uro's escape");
        Assert.assertTrue(has(me, ZoneType.Battlefield, "Uro, Titan of Nature's Wrath"), "Uro escaped");
        Assert.assertEquals(me.getCardsIn(ZoneType.Exile).size(), 5, "five other cards exiled");
    }

    /** Harmonize {5}{R}{R} with five Mountains: only tapping a creature makes it payable, so the menu offers it, and
     *  the seat chooses among the powers that leave it payable (the 2-power Bears leave {5}, the 3-power Giant {4}). */
    @Test
    public void harmonizeTapsACreatureToReduceItsCost() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        lands(me, "Mountain", "Mountain", "Mountain", "Mountain", "Mountain");
        Card giant = card("Hill Giant", me, ZoneType.Battlefield);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Channeled Dragonfire", me, ZoneType.Graveyard);
        String[] asked = new String[1];
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Harmonize"))
                .on("HARMONIZE", p -> { asked[0] = p; return choiceWith("power 3").apply(p); })
                .on("Choose the TARGET", choiceWith("Rival"));
        playAndResolve(seat(g, model, model), g, "Channeled Dragonfire's harmonize");
        Assert.assertNotNull(asked[0], "the power to tap was the seat's");
        Assert.assertTrue(asked[0].contains("{X} less if you tap a creature"), "the menu says how harmonize is paid: " + asked[0]);
        Assert.assertTrue(asked[0].contains("power 2") && asked[0].contains("power 3") && !asked[0].contains("0: tap no creature"),
                "only the payable powers are offered: " + asked[0]);
        Assert.assertEquals(rival.getLife(), 18, "it dealt 2");
        Assert.assertTrue(has(me, ZoneType.Exile, "Channeled Dragonfire"), "a harmonized spell is exiled");
        Assert.assertTrue(giant.isTapped(), "the Giant was tapped for harmonize");
        Assert.assertEquals(me.getCardsIn(ZoneType.Battlefield).stream().filter(c -> c.isLand() && !c.isTapped()).count(), 1L,
                "it cost {4}: one Mountain left");
    }

    /** Combat damage dealt this turn, recorded where combat records it (CardDamageHistory.registerDamage): the
     *  state prowl and freerunning read. */
    static void dealtCombatDamage(Card source, Player to, int n) {
        source.getDamageHistory().registerDamage(n, true, source, to, new java.util.HashMap<>());
    }

    /** Prowl {1}{B} after a Rogue dealt combat damage: the only way to cast Morsel Theft with two Swamps. */
    @Test
    public void prowlCastsAfterARogueDealtCombatDamage() {
        Game g = bareGame(PhaseType.MAIN2);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        lands(me, "Swamp", "Swamp");
        dealtCombatDamage(card("Auntie's Snitch", me, ZoneType.Battlefield), rival, 3);
        card("Morsel Theft", me, ZoneType.Hand);
        card("Island", me, ZoneType.Library);
        int life = rival.getLife();
        String[] menu = new String[1];
        Script model = new Script("CHOICE: 1").on("You have priority", p -> { menu[0] = p; return choiceWith("(Prowl)").apply(p); })
                .on("Choose the TARGET", choiceWith("Rival"));
        playAndResolve(seat(g, model, model), g, "Morsel Theft by prowl");
        Assert.assertTrue(menu[0].contains("loses 3 life"), "the prowl option says what the spell does: " + menu[0]);
        Assert.assertEquals(rival.getLife(), life - 3, "the rival lost 3");
        Assert.assertTrue(has(me, ZoneType.Hand, "Island"), "prowled: it drew a card");
    }

    /** Freerunning {1}{B} after an Assassin dealt combat damage: Merciless Harlequin for two Swamps. */
    @Test
    public void freerunningCastsAfterAnAssassinDealtCombatDamage() {
        Game g = bareGame(PhaseType.MAIN2);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        lands(me, "Swamp", "Swamp");
        dealtCombatDamage(card("Royal Assassin", me, ZoneType.Battlefield), rival, 1);
        card("Merciless Harlequin", me, ZoneType.Hand);
        card("Island", me, ZoneType.Library);
        String[] menu = new String[1];
        Script model = new Script("CHOICE: 1").on("You have priority", p -> { menu[0] = p; return choiceWith("(Freerunning)").apply(p); });
        playAndResolve(seat(g, model, model), g, "Merciless Harlequin by freerunning");
        Assert.assertTrue(menu[0].contains("you draw a card and you lose 1 life"), "the option says what the creature does: " + menu[0]);
        Assert.assertTrue(has(me, ZoneType.Battlefield, "Merciless Harlequin"), "cast by freerunning");
    }

    /** Web-slinging {1}{G}{W}: returning a tapped creature is the only way to cast Spider-Man India with three lands. */
    @Test
    public void webSlingingReturnsATappedCreature() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Forest", "Plains", "Forest");
        card("Grizzly Bears", me, ZoneType.Battlefield).setTapped(true);
        card("Spider-Man India", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Spider-Man India"));
        playAndResolve(seat(g, model, model), g, "Spider-Man India by web-slinging");
        Assert.assertTrue(has(me, ZoneType.Battlefield, "Spider-Man India"), "cast by web-slinging");
        Assert.assertTrue(has(me, ZoneType.Hand, "Grizzly Bears"), "the tapped creature returned");
    }

    /** Mayhem {1}{R}: a card discarded this turn is cast from the graveyard. */
    @Test
    public void mayhemCastsACardDiscardedThisTurn() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        lands(me, "Mountain", "Mountain");
        me.discard(card("Electro's Bolt", me, ZoneType.Hand), null, true, forge.game.ability.AbilityKey.newMap());
        card("Hill Giant", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Electro's Bolt"))
                .on("Choose the TARGET", choiceWith("Hill Giant"));
        playAndResolve(seat(g, model, model), g, "Electro's Bolt by mayhem");
        Assert.assertTrue(has(rival, ZoneType.Graveyard, "Hill Giant"), "4 damage killed the Giant");
    }

    /** More Than Meets the Eye {1}{W}: Ratchet is cast converted, its vehicle face. */
    @Test
    public void moreThanMeetsTheEyeCastsConverted() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Plains", "Plains");
        card("Ratchet, Field Medic", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Ratchet"));
        playAndResolve(seat(g, model, model), g, "Ratchet converted");
        Assert.assertTrue(has(me, ZoneType.Battlefield, "Ratchet, Rescue Racer"), "it entered converted: " + me.getCardsIn(ZoneType.Battlefield));
    }

    /** Beam me up {2}{U}: cast from the graveyard, returning a creature, then exiled. */
    @Test
    public void beamMeUpCastsFromTheGraveyardReturningACreature() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Island", "Island", "Island");
        card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Open Communications", me, ZoneType.Graveyard);
        card("Forest", me, ZoneType.Library);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Open Communications"));
        playAndResolve(seat(g, model, model), g, "Open Communications beamed up");
        Assert.assertTrue(has(me, ZoneType.Hand, "Grizzly Bears"), "the creature returned");
        Assert.assertTrue(has(me, ZoneType.Hand, "Forest"), "it drew");
        Assert.assertTrue(has(me, ZoneType.Exile, "Open Communications"), "then exiled");
    }

    /** Teamwork 1: tapping creatures with total power 1 is the seat's optional cost; paid, the target gets a counter. */
    @Test
    public void teamworkTapsCreaturesAsAnOptionalCost() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Forest", "Forest");
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        Card lions = card("Savannah Lions", me, ZoneType.Battlefield);
        card("Beast Mode", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Beast Mode"))
                .on("optional additional costs", p -> "PICKS: 1").on("Choose the TARGET", choiceWith("Grizzly Bears"));
        playAndResolve(seat(g, model, model), g, "Beast Mode with teamwork");
        Assert.assertTrue(bears.isTapped() || lions.isTapped(), "a creature was tapped for teamwork");
        Assert.assertEquals(bears.getCounters(forge.game.card.CounterEnumType.P1P1), 1, "teamwork paid: a +1/+1 counter");
    }

    /** Sneak {1}{B}: during combat, returning an unblocked attacker casts Oroku Saki for two Swamps. */
    @Test
    public void sneakReturnsAnUnblockedAttacker() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        lands(me, "Swamp", "Swamp");
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        Combat combat = new Combat(me);
        combat.addAttacker(bears, rival);
        combat.setBlocked(bears, false);
        g.getPhaseHandler().setCombat(combat);
        card("Oroku Saki, Shredder Rising", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Oroku Saki"));
        playAndResolve(seat(g, model, model), g, "Oroku Saki by sneak");
        Assert.assertTrue(has(me, ZoneType.Battlefield, "Oroku Saki, Shredder Rising"), "cast by sneak");
        Assert.assertTrue(has(me, ZoneType.Hand, "Grizzly Bears"), "the unblocked attacker returned");
    }

    /** Craft exiles "another artifact you control or an artifact card from your graveyard": with the only other
     *  artifact in the graveyard, or the only one on the battlefield, the craft is paid (the keyword gauntlet's
     *  Braided Net was offered and failed to pay 999 times). */
    @Test
    public void craftExilesAnArtifactFromEitherZone() {
        // Altar of the Wretched is the gauntlet's: Forge's AI declines to exile it, and with one candidate the exile
        // was left to the AI, so the payment failed.
        for (String other : new String[] {"Ornithopter", "Altar of the Wretched"})
        for (ZoneType where : new ZoneType[] {ZoneType.Graveyard, ZoneType.Battlefield}) {
            Game g = bareGame(PhaseType.MAIN1);
            Player me = g.getPlayers().get(0);
            lands(me, "Island", "Mountain");
            card("Braided Net", me, ZoneType.Battlefield);
            card(other, me, where);
            Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Craft"));
            playAndResolve(seat(g, model, model), g, "Braided Net's craft with " + other + " in the " + where);
            Assert.assertTrue(has(me, ZoneType.Exile, other), where + ": " + other + " was exiled");
            Assert.assertTrue(has(me, ZoneType.Battlefield, "Braided Quipu"), where + ": it returned transformed");
        }
        // With its lands tapped the craft cannot be paid, so it is not offered (the gauntlet's Braided Net was).
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Island", "Island");
        for (Card l : me.getCardsIn(ZoneType.Battlefield)) l.setTapped(true);
        Card net = card("Braided Net", me, ZoneType.Battlefield);
        card("Altar of the Wretched", me, ZoneType.Battlefield);
        seat(g, new Script("CHOICE: 1"), new Script("CHOICE: 1"));
        Assert.assertTrue(LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(me, true).spells.stream()
                .noneMatch(sa -> sa.getHostCard() == net && String.valueOf(sa).contains("Craft")), "craft offered with no mana");
        // With mana but no other artifact to exile the craft cannot be paid, so it is not offered.
        g = bareGame(PhaseType.MAIN1);
        me = g.getPlayers().get(0);
        lands(me, "Island", "Island");
        Card lone = card("Braided Net", me, ZoneType.Battlefield);
        seat(g, new Script("CHOICE: 1"), new Script("CHOICE: 1"));
        Assert.assertTrue(LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(me, true).spells.stream()
                .noneMatch(sa -> sa.getHostCard() == lone && String.valueOf(sa).contains("Craft")), "craft offered with nothing to exile");
        // The gauntlet's board: the last lands spent on Altar of the Wretched, its enter trigger on the stack.
        g = bareGame(PhaseType.MAIN1);
        me = g.getPlayers().get(0);
        lands(me, "Swamp", "Swamp", "Swamp");
        Card net2 = card("Braided Net", me, ZoneType.Battlefield);
        card("Altar of the Wretched", me, ZoneType.Hand);
        card("Island", me, ZoneType.Library);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Script("CHOICE: 1").on("You have priority", choiceWith("Altar")),
                new Script("CHOICE: 1"));
        Assert.assertTrue(ctrl.playChosenSpellAbility(ctrl.chooseSpellAbilityToPlay().get(0)), "Altar cast");
        g.getStack().resolveStack();
        g.getTriggerHandler().runWaitingTriggers();
        g.getStack().addAllTriggeredAbilitiesToStack();
        Assert.assertFalse(g.getStack().isEmpty(), "Altar's trigger is on the stack");
        Assert.assertTrue(LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(me, false).spells.stream()
                .noneMatch(sa -> sa.getHostCard() == net2 && String.valueOf(sa).contains("Craft")),
                "craft offered with a trigger on the stack and no mana: " + LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(me, false).spells);
    }

    /** Floating mana of two kinds (Lukka's +1: {R}{G}, creature spells only) and a creature in hand: pricing the hand
     *  for the snapshot reached the pool choice, whose ask built a snapshot that priced the hand again
     *  (StackOverflowError in the keyword gauntlet). Pricing takes the first; only a real payment asks. */
    @Test
    public void floatingManaIsNotAskedWhilePricing() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card lukka = card("Lukka, Bound to Ruin", me, ZoneType.Battlefield);
        lukka.addCounterInternal(forge.game.card.CounterEnumType.LOYALTY, 5, me, false, null, null);
        card("Alesha's Vanguard", me, ZoneType.Hand);
        card("Agonasaur Rex", me, ZoneType.Hand);
        card("Grizzly Bears", me, ZoneType.Hand);
        boolean[] once = {false};
        Script model = new Script("CHOICE: 1").on("You have priority", p -> {
            if (once[0]) return "CHOICE: " + opt(p, "PASS");
            once[0] = true;
            return choiceWith("+1: Add").apply(p);
        });
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Assert.assertTrue(ctrl.playChosenSpellAbility(ctrl.chooseSpellAbilityToPlay().get(0)), "Lukka's +1 activated");
        resolveAll(g);
        Assert.assertEquals(me.getManaPool().totalMana(), 2, "{R}{G} floats");
        ctrl.chooseSpellAbilityToPlay();
        Assert.assertTrue(model.prompts.stream().noneMatch(q -> q.contains("MANA POOL")), "the pool was asked while pricing");
    }

    /** A source kept untapped must leave a payment the engine really makes: with {G} floating, Jegantha ({W}{U}{B}{R}{G},
     *  none for generic), a Mountain and a Swamp, keeping the Island looked payable to the planner (Jegantha's {R}) but
     *  the payment spent the Mountain on {R} and came up {1} short (keyword gauntlet with lookups on, kw03). */
    @Test
    public void aKeptSourceLeavesAPaymentTheEngineMakes() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Forest", "Island", "Mountain", "Swamp");
        card("Jegantha, the Wellspring", me, ZoneType.Battlefield);   // also something to champion
        card("Changeling Berserker", me, ZoneType.Hand);
        boolean[] floated = {false};
        Script model = new Script("CHOICE: 1").on("You have priority", p -> {
            if (!floated[0]) { floated[0] = true; return choiceWith("ACTIVATE Forest").apply(p); }
            return choiceWith("CAST Changeling Berserker").apply(p);
        }).on("MANA: you are paying", picks("Island"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Assert.assertTrue(ctrl.playChosenSpellAbility(ctrl.chooseSpellAbilityToPlay().get(0)), "Forest tapped");
        Assert.assertEquals(me.getManaPool().totalMana(), 1, "{G} floats");
        playAndResolve(ctrl, g, "Changeling Berserker with the floating {G}");
        Assert.assertTrue(me.getCardsIn(ZoneType.Battlefield).anyMatch(c -> c.getName().equals("Changeling Berserker")),
                "the Berserker was paid for: " + String.join(" | ", model.prompts.stream().map(q -> q.substring(Math.max(0, q.length() - 200))).toList()));
    }

    /** A modal spell cancelled at its target keeps no modes: Explosive Derailment's two spree modes stayed chained with no
     *  targets, and the next preview of it failed to target (keyword gauntlet with lookups on, kw23). */
    @Test
    public void aCancelledModalSpellKeepsNoModes() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        lands(me, "Mountain", "Mountain", "Mountain", "Mountain", "Mountain");
        card("Hill Giant", rival, ZoneType.Battlefield);
        card("Ornithopter", rival, ZoneType.Battlefield);
        Card derail = card("Explosive Derailment", me, ZoneType.Hand);
        boolean[] cast = {false};
        Script model = new Script("CHOICE: 1").on("You have priority", p -> {
            if (cast[0]) return choiceWith("PASS").apply(p);
            cast[0] = true;
            return choiceWith("Explosive Derailment").apply(p);
        }).on("MODAL SPELL", p -> "PICKS: 1,2").on("TARGET", choiceWith("CANCEL"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        ctrl.chooseSpellAbilityToPlay();
        Assert.assertTrue(model.prompts.stream().anyMatch(p -> p.contains("MODAL SPELL")), "modes were asked");
        Assert.assertEquals(ctrl.playsNotMade(), 0, "a cancel at the target is the seat's own choice");
        Assert.assertNull(derail.getFirstSpellAbility().getSubAbility(), "the cancelled spell kept its modes");
    }

    /** Pricing inside pricing keeps the guard on. The hand's tags priced each card and switched PRICING_FOR_SNAPSHOT
     *  off when done, though an outer pricing was still running; the next pool choice then asked the seat, whose prompt
     *  priced the hand again: a StackOverflowError (keyword gauntlet with lookups on, kw04 and kw05). */
    @Test
    public void pricingInsidePricingKeepsTheGuardOn() throws Exception {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Forest");
        Card bears = card("Grizzly Bears", me, ZoneType.Hand);
        seat(g, new Script("CHOICE: 1"), new Script("CHOICE: 1"));
        java.lang.reflect.Field f = LlmSpikeGui.PlayerControllerLLM.class.getDeclaredField("PRICING_FOR_SNAPSHOT");
        f.setAccessible(true);
        f.setBoolean(null, true);   // an outer pricing is running
        try {
            LlmSpikeGui.PlayerControllerLLM.castTag(me, bears, LlmSpikeGui.PlayerControllerLLM.untappedManaTally(me));
            Assert.assertTrue(f.getBoolean(null), "the hand tag switched the pricing guard off under an outer pricing");
        } finally { f.setBoolean(null, false); }
    }

    /** "{1}, remove X +1/+1 counters (X at least 1)": with no counter Arcee's ability is not offered; with two, X is
     *  offered up to 2 (it was offered with none and announced at X=1, and the payment failed: keyword gauntlet). */
    @Test
    public void anXCostOfCountersIsBoundByTheCounters() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Mountain", "Mountain");
        Card arcee = card("Arcee, Sharpshooter", me, ZoneType.Battlefield);
        card("Hill Giant", g.getPlayers().get(1), ZoneType.Battlefield);
        seat(g, new Script("CHOICE: 1"), new Script("CHOICE: 1"));
        Assert.assertTrue(LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(me, true).spells.stream()
                .noneMatch(sa -> sa.getHostCard() == arcee && !sa.isSpell()), "offered with no counter to remove");
        arcee.addCounterInternal(forge.game.card.CounterEnumType.P1P1, 2, me, false, null, null);
        String[] xAsk = new String[1];
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("ACTIVATE Arcee"))
                .on("Choose X", p -> { xAsk[0] = p; return "CHOICE: " + opt(p, "2"); }).on("Choose the TARGET", choiceWith("Hill Giant"));
        playAndResolve(seat(g, model, model), g, "Arcee's ability, X=2");
        Assert.assertNotNull(xAsk[0], "X was asked");
        Assert.assertFalse(xAsk[0].contains("\n  4) "), "X offered beyond the two counters: " + xAsk[0]);
        Assert.assertEquals(arcee.getCounters(forge.game.card.CounterEnumType.P1P1), 0, "both counters removed");
    }

    /** Spree: each mode adds its cost. Two Forests pay no mode of Dance of the Tumbleweeds ({1}{G} + {1} or + {3}), so it
     *  is not offered; with three only the {1} mode is (it was chosen with two lands and the cast failed: keyword gauntlet). */
    @Test
    public void spreeModesAreOfferedOnlyWhenPayable() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Forest", "Forest");
        Card dance = card("Dance of the Tumbleweeds", me, ZoneType.Hand);
        card("Forest", me, ZoneType.Library);
        seat(g, new Script("CHOICE: 1"), new Script("CHOICE: 1"));
        Assert.assertTrue(LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(me, true).spells.stream()
                .noneMatch(sa -> sa.getHostCard() == dance), "offered with no payable mode");
        card("Forest", me, ZoneType.Battlefield);
        String[] modes = new String[1];
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Dance of the Tumbleweeds"))
                .on("MODAL SPELL", p -> { modes[0] = p; return "PICKS: 1"; }).on("Forest", choiceWith("Forest"));
        playAndResolve(seat(g, model, model), g, "Dance of the Tumbleweeds, the {1} mode");
        Assert.assertTrue(modes[0] == null || !modes[0].contains("Create an X/X"), "the unpayable {3} mode was offered: " + modes[0]);
        Assert.assertEquals(me.getCardsIn(ZoneType.Battlefield).stream().filter(Card::isLand).count(), 4L, "it fetched a land");
    }

    /** Escalate adds its cost for each mode beyond the first: the seat may choose as many modes as it can pay for, and no
     *  more (the random gauntlet's Borrowed Grace chose both modes with mana for one, and the cast failed to pay). */
    @Test
    public void escalateModesAreOfferedAsFarAsTheyCanBePaid() {
        for (int plains : new int[] {3, 5}) {
            Game g = bareGame(PhaseType.MAIN1);
            Player me = g.getPlayers().get(0);
            for (int i = 0; i < plains; i++) lands(me, "Plains");
            Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
            String[] modes = new String[1];
            Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Borrowed Grace"))
                    .on("MODAL SPELL", p -> { modes[0] = p; return "PICKS: 1,2"; });
            card("Borrowed Grace", me, ZoneType.Hand);
            playAndResolve(seat(g, model, model), g, "Borrowed Grace with " + plains + " Plains");
            Assert.assertTrue(me.getCardsIn(ZoneType.Graveyard).anyMatch(c -> c.getName().equals("Borrowed Grace")),
                    plains + " Plains: the cast was paid");
            Assert.assertTrue(modes[0] != null && modes[0].contains("escalate"), "the question names escalate: " + modes[0]);
            Assert.assertEquals(bears.getNetToughness(), plains == 5 ? 4 : 2, plains + " Plains: both modes only when payable");
        }
    }

    /** Cancelling a tap cost cancels the play: Station's "tap another creature" answered CANCEL in the random gauntlet,
     *  and a creature was tapped anyway, recorded as an unparsed answer. Only with the game log on: writing the
     *  decision row cleared the cancel before it was returned. So the log is on here, as in a game. */
    @Test
    public void aCancelledTapCostTapsNothing() throws Exception {
        boolean mine = !LlmSpikeGui.EvalLog.on();   // GoldenChecksToolTest keeps the log on for every check
        if (mine) LlmSpikeGui.EvalLog.initToDir(java.nio.file.Files.createTempDirectory("cancel-log").toString(), 7);
        try { cancelledTapCost(); } finally { if (mine) LlmSpikeGui.EvalLog.closeForTest(); }
    }

    private void cancelledTapCost() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Atmospheric Greenhouse", me, ZoneType.Battlefield);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Hill Giant", me, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Station"))
                .on("you must TAP", picks("CANCEL"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, "Station offered");
        ctrl.playChosenSpellAbility(r.get(0));   // true: the seat is asked again, with the play unpaid
        resolveAll(g);
        Assert.assertEquals(me.getCreaturesInPlay().stream().filter(Card::isTapped).count(), 0L,
                "nothing tapped: " + String.join(" | ", model.prompts.stream().map(p -> p.substring(Math.max(0, p.length() - 300))).toList()));
        Assert.assertEquals(ctrl.playsNotMade(), 0, "a cancel is the seat's own choice, not a play that failed");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** A cast cancelled at its cost goes back to the hand (harness-epoch-70): Village Rites (sacrifice a creature) from
     *  hand, CANCEL at the sacrifice. The copied engine routine's recovery left the card in the Stack zone. The next
     *  play in the window reaches the stack: the rollback's freeze, kept for the cancelled play, held Duress back. */
    @Test
    public void aCastCancelledAtItsCostIsBackInHand() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Swamp", "Swamp");
        card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Hill Giant", me, ZoneType.Battlefield);
        card("Swamp", me, ZoneType.Hand);
        Card rites = card("Village Rites", me, ZoneType.Hand);
        card("Duress", me, ZoneType.Hand);
        int[] menus = {0};
        Script model = new Script("CHOICE: 1")
                .on("You have priority", p -> choiceWith(++menus[0] == 1 ? "Village Rites" : "Duress").apply(p))
                .on("Choose the TARGET", choiceWith("Rival"))
                .on(LlmSpikeGui.PlayerControllerLLM.COST_CANCEL_LABEL, picks(LlmSpikeGui.PlayerControllerLLM.COST_CANCEL_LABEL));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, "Village Rites offered");
        ctrl.playChosenSpellAbility(r.get(0));
        Assert.assertTrue(model.asked(LlmSpikeGui.PlayerControllerLLM.COST_CANCEL_LABEL), "the cost ask offered CANCEL");
        Assert.assertEquals(g.getCardState(rites).getZone().getZoneType(), ZoneType.Hand, "the cancelled cast's card");
        Assert.assertEquals(me.getCardsIn(ZoneType.Hand).indexOf(g.getCardState(rites)), 1, "at its old place in hand");
        Assert.assertTrue(g.getStackZone().isEmpty(), "a card left in the Stack zone");
        Assert.assertEquals(me.getCreaturesInPlay().size(), 2, "nothing sacrificed");
        Assert.assertEquals(me.getManaPool().totalMana(), 0);
        r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, "Duress offered");
        ctrl.playChosenSpellAbility(r.get(0));
        Assert.assertEquals(g.getStack().size(), 1, "Duress is on the stack");
        Assert.assertFalse(g.getStack().isFrozen());
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** A play the stack refused for want of a target is not offered again in the window (pr-review of #284): rolled
     *  back, Murder is in hand with its Swamps untapped, the window unchanged, so a seat could pick it without end. */
    @Test
    public void aPlayTheStackRefusedIsNotOfferedAgainInTheWindow() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        lands(me, "Swamp", "Swamp", "Swamp");
        card("Grizzly Bears", rival, ZoneType.Battlefield);
        card("Swamp", me, ZoneType.Hand);
        Card murder = card("Murder", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choice("PASS"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Assert.assertNull(ctrl.chooseSpellAbilityToPlay(), "the seat passed");
        Assert.assertTrue(lastDecision(model).contains("Murder"), "Murder on the first menu");
        SpellAbility cast = murder.getFirstSpellAbility();   // no target set: the stack refuses it, as from a stale menu
        cast.setActivatingPlayer(me);
        ctrl.playChosenSpellAbility(cast);
        Assert.assertEquals(g.getCardState(murder).getZone().getZoneType(), ZoneType.Hand, "rolled back");
        int asked = model.prompts.size();
        ctrl.chooseSpellAbilityToPlay();
        Assert.assertEquals(model.prompts.size(), asked + 1, "the changed menu is asked");
        Assert.assertFalse(lastDecision(model).contains("Murder"), "the refused play offered again: " + lastDecision(model));
    }

    private static String lastDecision(Recorder model) {
        String p = model.prompts.get(model.prompts.size() - 1);
        return p.substring(Math.max(0, p.lastIndexOf("DECISION:")));
    }

    /** A rollback refunds only this play's mana (pr-review of harness-epoch-70): Siege-Gang Commander's "{1}{R},
     *  Sacrifice a Goblin" activated and resolved, then activated again and cancelled at its sacrifice. The ability's
     *  own object kept the first activation's mana (the stack took a copy), and the rollback put {1}{R} in the pool. */
    @Test
    public void aCancelledActivationRefundsNoEarlierMana() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        lands(me, "Mountain", "Mountain", "Mountain", "Mountain");
        card("Siege-Gang Commander", me, ZoneType.Battlefield);
        card("Goblin Piker", me, ZoneType.Battlefield);
        card("Goblin Piker", me, ZoneType.Battlefield);
        int[] costAsks = {0};
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("ACTIVATE Siege-Gang Commander"))
                .on("Choose the TARGET", choiceWith("Rival"))
                .on(LlmSpikeGui.PlayerControllerLLM.COST_CANCEL_LABEL, p -> ++costAsks[0] == 1
                        ? picks("Goblin Piker").apply(p) : picks(LlmSpikeGui.PlayerControllerLLM.COST_CANCEL_LABEL).apply(p));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        playAndResolve(ctrl, g, "Siege-Gang Commander's first activation");
        Assert.assertEquals(rival.getLife(), 18, "the first activation resolved");
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, "the second activation offered");
        ctrl.playChosenSpellAbility(r.get(0));
        Assert.assertEquals(costAsks[0], 2, "the second activation was cancelled at its sacrifice");
        Assert.assertEquals(me.getManaPool().totalMana(), 0, "mana from the first activation refunded again");
        Assert.assertEquals(me.getCardsIn(ZoneType.Battlefield).stream().filter(c -> c.isLand() && c.isTapped()).count(), 2L,
                "the first activation's lands stay tapped");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** Strive adds its cost for each target beyond the first: Ajani's Presence ({W}, strive {2}{W}) with two Plains may
     *  take one target, with four two. */
    @Test
    public void striveTargetsAreOfferedAsFarAsTheyCanBePaid() {
        for (int plains : new int[] {2, 4}) {
            Game g = bareGame(PhaseType.MAIN1);
            Player me = g.getPlayers().get(0);
            for (int i = 0; i < plains; i++) lands(me, "Plains");
            card("Grizzly Bears", me, ZoneType.Battlefield);
            card("Grizzly Bears", me, ZoneType.Battlefield);
            card("Ajani's Presence", me, ZoneType.Hand);
            String[] ask = new String[1];
            Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Ajani's Presence"))
                    .on("TARGETS", p -> { ask[0] = p; return p.contains("pick 0 to 2") ? "PICKS: 1,2" : "PICKS: 1"; });
            playAndResolve(seat(g, model, model), g, "Ajani's Presence with " + plains + " Plains");
            Assert.assertTrue(me.getCardsIn(ZoneType.Graveyard).anyMatch(c -> c.getName().equals("Ajani's Presence")), plains + " Plains: cast");
            Assert.assertTrue(ask[0] != null && ask[0].contains("strive"), "the question names strive: " + ask[0]);
            Assert.assertTrue(ask[0].contains(plains == 4 ? "pick 0 to 2" : "pick 0 to 1"), plains + " Plains: the bound: " + ask[0]);
            Assert.assertEquals(me.getCreaturesInPlay().stream().filter(c -> c.getNetPower() == 3).count(), plains == 4 ? 2L : 1L,
                    plains + " Plains: two targets only when payable");
        }
    }

    /** Conspire is paid by tapping two untapped creatures that share a colour with the spell: with none to tap it is
     *  not offered, and the cast goes ahead (the random gauntlet's Burn Trail was answered "pay it" with no creature to
     *  tap, and the whole cast failed); with two, the seat is asked. */
    @Test
    public void aKeywordCostIsOfferedOnlyWhenItCanBePaid() {
        for (boolean goblins : new boolean[] {false, true}) {
            Game g = bareGame(PhaseType.MAIN1);
            Player me = g.getPlayers().get(0);
            lands(me, "Mountain", "Mountain", "Mountain", "Mountain");
            if (goblins) { card("Raging Goblin", me, ZoneType.Battlefield); card("Raging Goblin", me, ZoneType.Battlefield); }
            card("Burn Trail", me, ZoneType.Hand);
            Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Burn Trail")).on("Conspire", p -> "CHOICE: 2");
            playAndResolve(seat(g, model, model), g, "Burn Trail, goblins=" + goblins);
            Assert.assertTrue(me.getCardsIn(ZoneType.Graveyard).anyMatch(c -> c.getName().equals("Burn Trail")), "goblins=" + goblins + ": cast");
            boolean asked = model.prompts.stream().anyMatch(p -> p.contains("Conspire"));
            Assert.assertEquals(asked, goblins, "conspire asked only when two creatures can pay it");
            Assert.assertEquals(me.getCreaturesInPlay().stream().filter(Card::isTapped).count(), goblins ? 2L : 0L, "the goblins paid it");
        }
        // Squad may be paid any number of times (the engine's range is Integer.MAX_VALUE): only the times the mana covers
        // are offered. A random answer of 1,212,328,817 squads hung the payment and ran the game out of heap.
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Forest", "Forest", "Forest", "Forest", "Forest");
        card("Galadhrim Brigade", me, ZoneType.Hand);
        String[] squad = new String[1];
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Galadhrim Brigade"))
                .on("Squad", p -> { squad[0] = p; return "CHOICE: 2"; });
        playAndResolve(seat(g, model, model), g, "Galadhrim Brigade with squad");
        Assert.assertNotNull(squad[0], "squad asked");
        Assert.assertTrue(squad[0].contains("1) 0") && squad[0].contains("2) 1") && !squad[0].contains("3) 2"),
                "five Forests pay the {2}{G} and one {1}{G} squad: 0 or 1 offered: " + squad[0]);
        Assert.assertEquals(me.getCreaturesInPlay().stream().filter(c -> c.getName().equals("Galadhrim Brigade")).count(), 2L, "one squad copy");
    }

    /** Convoke with a mana creature: Llanowar Elves taps for convoke, so it is not a mana source left for the rest, and
     *  keeping a land untapped is not offered when nothing is spare (the keyword gauntlet's Angel of Salvation kept a
     *  land, counted the convoking Elves as mana, and failed to pay). */
    @Test
    public void aConvokingCreatureIsNotAlsoAManaSource() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Plains", "Plains", "Plains", "Plains", "Plains", "Forest");
        card("Llanowar Elves", me, ZoneType.Battlefield);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Angel of Salvation", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Angel of Salvation"))
                .on("CONVOKE", picks("Llanowar Elves", "Grizzly Bears"))
                .on("Which should stay untapped", picks("Plains"))
                .on("prevent", p -> "PICKS: NONE");
        playAndResolve(seat(g, model, model), g, "Angel of Salvation, convoked");
        Assert.assertTrue(has(me, ZoneType.Battlefield, "Angel of Salvation"), "cast with six lands and two convoking creatures");
        // The gauntlet's board: eight lands, only two of them Plains for its {W}{W}, and two creatures that are not white.
        // Keeping a Plains untapped leaves {W}{W} unpayable, whatever convokes.
        g = bareGame(PhaseType.MAIN1);
        me = g.getPlayers().get(0);
        lands(me, "Plains", "Plains", "Island", "Island", "Swamp", "Swamp", "Mountain", "Forest");
        card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Hill Giant", me, ZoneType.Battlefield);
        card("Angel of Salvation", me, ZoneType.Hand);
        model = new Script("CHOICE: 1").on("You have priority", choiceWith("Angel of Salvation"))
                .on("CONVOKE", picks("Grizzly Bears", "Hill Giant"))
                .on("Which should stay untapped", picks("Plains"))
                .on("prevent", p -> "PICKS: NONE");
        playAndResolve(seat(g, model, model), g, "Angel of Salvation, a Plains asked to stay up");
        Assert.assertTrue(has(me, ZoneType.Battlefield, "Angel of Salvation"), "cast: the Plains could not stay up");
        // Six lands for {6}{W}{W}: both creatures must convoke. Convoking none is asked again with the reason, then
        // both convoke (the gauntlet's seat convoked fewer than its kept land assumed, and the payment failed).
        g = bareGame(PhaseType.MAIN1);
        me = g.getPlayers().get(0);
        lands(me, "Plains", "Plains", "Island", "Swamp", "Mountain", "Forest");
        card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Hill Giant", me, ZoneType.Battlefield);
        card("Angel of Salvation", me, ZoneType.Hand);
        model = new Script("CHOICE: 1").on("You have priority", choiceWith("Angel of Salvation"))
                .on("CONVOKE", p -> "PICKS: NONE").on("prevent", p -> "PICKS: NONE");
        playAndResolve(seat(g, model, model), g, "Angel of Salvation, convoke declined");
        Assert.assertTrue(has(me, ZoneType.Battlefield, "Angel of Salvation"), "cast: the creatures convoked when nothing else could pay");
        Assert.assertTrue(model.prompts.stream().anyMatch(q -> q.contains("the rest cannot be paid")), "asked again with the reason");
    }

    /** A legal option is the model's to take (owner, 2026-10-17): Goblin Bombardment aimed at the seat's only creature
     *  may sacrifice that creature (the ability then has no target). The candidates left out the targets (a Forge AI
     *  filter), so the payment failed (harness acceptance random canary). */
    @Test
    public void aCostMayPayWithTheAbilitysOwnTarget() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Goblin Bombardment", me, ZoneType.Battlefield);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("ACTIVATE Goblin Bombardment"))
                .on("Choose the TARGET", choiceWith("Grizzly Bears"));
        playAndResolve(seat(g, model, model), g, "Goblin Bombardment at its own sacrifice");
        Assert.assertTrue(has(me, ZoneType.Graveyard, "Grizzly Bears"), "the targeted creature paid the cost");
        Assert.assertEquals(rival.getLife(), 20, "no damage: its target is gone");
    }

    /** Crew: which creatures, the seat's. Hill Giant crews Smuggler's Copter when the seat says so (Forge's AI chose,
     *  and would not tap a creature it valued above the vehicle). */
    @Test
    public void whoCrewsIsTheSeats() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card copter = card("Smuggler's Copter", me, ZoneType.Battlefield);
        Card giant = card("Hill Giant", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Crew"))
                .on("total power 1 or more", picks("Hill Giant"));
        playAndResolve(seat(g, model, model), g, "Smuggler's Copter crewed");
        Assert.assertTrue(giant.isTapped(), "the Giant crewed");
        Assert.assertFalse(bears.isTapped(), "the Bears did not");
        Assert.assertTrue(copter.isCreature(), "the Copter is a creature");
    }

    /** A companion in the command zone (set up as the engine does at game start) can be put into hand for {3} at sorcery
     *  speed: the special action is on the menu and works. */
    @Test
    public void aCompanionIsPutIntoHandFromTheMenu() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Island", "Island", "Swamp");
        Card gyruda = card("Gyruda, Doom of Depths", me, ZoneType.Command);
        me.getZone(ZoneType.Command).add(Player.createCompanionEffect(gyruda));
        g.getAction().checkStaticAbilities();
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Companion"));
        playAndResolve(seat(g, model, model), g, "Gyruda put into hand");
        Assert.assertTrue(has(me, ZoneType.Hand, "Gyruda, Doom of Depths"), "the companion is in hand");
    }

    /** Floating mana is legal: with a spell to cast, the menu also offers each kind of land's mana ability once, and the
     *  seat may tap one for mana (it then floats). A window with only a land's mana to float is passed without asking;
     *  floating that changes the game (a creature tapped, a trigger on the tap) is a legal play and is asked. */
    @Test
    public void floatingManaIsOnTheMenu() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Mountain", "Mountain", "Island");
        card("Lightning Bolt", me, ZoneType.Hand);
        String[] menu = new String[1];
        Script model = new Script("CHOICE: 1").on("You have priority", p -> { menu[0] = p; return choiceWith("ACTIVATE Mountain").apply(p); });
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r);
        Assert.assertTrue(ctrl.playChosenSpellAbility(r.get(0)), "the Mountain tapped for mana");
        Assert.assertEquals(me.getManaPool().totalMana(), 1, "{R} floats");
        Assert.assertEquals(menu[0].split("ACTIVATE Mountain", -1).length - 1, 1, "one option per kind of land: " + menu[0]);
        Assert.assertTrue(menu[0].contains("ACTIVATE Island"), "the Island too");
        // Only mana to float: no ask.
        g = bareGame(PhaseType.MAIN1);
        me = g.getPlayers().get(0);
        lands(me, "Mountain");
        Script quiet = new Script("CHOICE: 1");
        Assert.assertNull(seat(g, quiet, quiet).chooseSpellAbilityToPlay(), "passed");
        Assert.assertTrue(quiet.prompts.isEmpty(), "not asked");
        // Llanowar Elves tapped can no longer block; Manabarbs deals damage on the tap: both are the seat's to weigh.
        for (String[] board : new String[][] {{"Llanowar Elves", null}, {"Mountain", "Manabarbs"}}) {
            g = bareGame(PhaseType.MAIN1);
            me = g.getPlayers().get(0);
            lands(me, board[0]);
            if (board[1] != null) card(board[1], g.getPlayers().get(1), ZoneType.Battlefield);
            Script asked = new Script("CHOICE: 1");
            seat(g, asked, asked).chooseSpellAbilityToPlay();
            Assert.assertFalse(asked.prompts.isEmpty(), "asked: " + String.join(", ", board[0], String.valueOf(board[1])));
        }
    }

    @Test
    public void disturbCastsTheBackFace() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Plains", "Plains");
        card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Lunarch Veteran", me, ZoneType.Graveyard);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("(Disturb)"));
        playAndResolve(seat(g, model, model), g, "Lunarch Veteran's disturb");
        Assert.assertTrue(me.getCardsIn(ZoneType.Battlefield).stream().anyMatch(c -> c.getName().equals("Luminous Phantom")),
                "its back face is on the battlefield: " + me.getCardsIn(ZoneType.Battlefield));
    }

    @Test
    public void kickerIsTheSeatsOptionalCost() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        lands(me, "Mountain", "Mountain", "Mountain", "Mountain", "Mountain");
        card("Burst Lightning", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Burst Lightning"))
                .on("optional additional costs", p -> "PICKS: 1").on("Choose the TARGET", choiceWith("Rival"));
        playAndResolve(seat(g, model, model), g, "Burst Lightning, kicked");
        Assert.assertEquals(rival.getLife(), 20 - 4, "kicked: 4 damage");
    }

    @Test
    public void buybackReturnsTheSpell() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        lands(me, "Island", "Island", "Island", "Island", "Island", "Island");
        card("Capsize", me, ZoneType.Hand);
        card("Grizzly Bears", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Capsize"))
                .on("optional additional costs", p -> "PICKS: 1").on("Choose the TARGET", choiceWith("Grizzly Bears"));
        playAndResolve(seat(g, model, model), g, "Capsize with buyback");
        Assert.assertTrue(has(rival, ZoneType.Hand, "Grizzly Bears"), "the Bears were bounced");
        Assert.assertTrue(has(me, ZoneType.Hand, "Capsize"), "bought back");
    }

    /** Entwine is offered only when the whole cast stays payable: with 7 lands for 8 mana it is not asked at all. */
    @Test
    public void entwineIsNotOfferedWhenTheWholeCastIsUnaffordable() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Forest", "Forest", "Forest", "Forest", "Forest", "Forest", "Forest");
        card("Rude Awakening", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Rude Awakening"))
                .on("optional additional costs", p -> "PICKS: 1");
        playAndResolve(seat(g, model, model), g, "Rude Awakening, unentwined");
        Assert.assertTrue(model.prompts.stream().noneMatch(q -> q.contains("optional additional costs")), "entwine was offered");
        Assert.assertTrue(has(me, ZoneType.Graveyard, "Rude Awakening"), "it was cast");
    }

    @Test
    public void entwineChoosesBothModes() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Forest", "Forest", "Forest", "Forest", "Forest", "Forest", "Forest", "Forest");
        card("Rude Awakening", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Rude Awakening"))
                .on("optional additional costs", p -> "PICKS: 1");
        playAndResolve(seat(g, model, model), g, "Rude Awakening, entwined");
        long untapped = me.getCardsIn(ZoneType.Battlefield).stream().filter(c -> c.isLand() && !c.isTapped()).count();
        long animated = me.getCardsIn(ZoneType.Battlefield).stream().filter(c -> c.isLand() && c.isCreature()).count();
        Assert.assertEquals(untapped, 8, "untap mode");
        Assert.assertEquals(animated, 8, "animate mode");
    }

    @Test
    public void retraceCastsFromTheGraveyardByDiscardingALand() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        lands(me, "Swamp");
        card("Raven's Crime", me, ZoneType.Graveyard);
        card("Swamp", me, ZoneType.Hand);
        card("Ponder", rival, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Raven's Crime")).on("Choose the TARGET", choiceWith("Rival"));
        playAndResolve(seat(g, model, model), g, "Raven's Crime by retrace");
        Assert.assertTrue(has(me, ZoneType.Graveyard, "Swamp"), "a land was discarded to retrace");
        Assert.assertTrue(has(rival, ZoneType.Graveyard, "Ponder"), "the rival discarded");
    }

    @Test
    public void jumpStartCastsFromTheGraveyardByDiscarding() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Island", "Island", "Island", "Island");
        card("Chemister's Insight", me, ZoneType.Graveyard);
        card("Ponder", me, ZoneType.Hand);
        for (int i = 0; i < 3; i++) card("Island", me, ZoneType.Library);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Chemister's Insight"));
        playAndResolve(seat(g, model, model), g, "Chemister's Insight by jump-start");
        Assert.assertTrue(has(me, ZoneType.Exile, "Chemister's Insight"), "jump-started: exiled after");
        Assert.assertEquals(me.getCardsIn(ZoneType.Hand).size(), 2, "discarded one, drew two");
    }

    @Test
    public void spectacleAfterTheRivalLostLife() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        lands(me, "Mountain");
        card("Skewer the Critics", me, ZoneType.Hand);
        rival.loseLife(1, false, false, null);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("(Spectacle)")).on("Choose the TARGET", choiceWith("Rival"));
        playAndResolve(seat(g, model, model), g, "Skewer's spectacle");
        Assert.assertEquals(rival.getLife(), 19 - 3);
    }

    @Test
    public void madnessCastsTheDiscardedCard() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        lands(me, "Mountain");
        card("Wild Mongrel", me, ZoneType.Battlefield);
        card("Fiery Temper", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Wild Mongrel"))
                .on("Choose the TARGET", choiceWith("Rival"));
        playAndResolve(seat(g, model, model), g, "Wild Mongrel's discard");
        Assert.assertEquals(rival.getLife(), 17, "Fiery Temper was cast for its madness cost: " + model.prompts.stream()
                .map(q -> q.substring(q.lastIndexOf("DECISION:"), Math.min(q.length(), q.lastIndexOf("DECISION:") + 200))).collect(java.util.stream.Collectors.toList()));
        Assert.assertTrue(has(me, ZoneType.Graveyard, "Fiery Temper"));
    }

    @Test
    public void mutateMergesOntoTheSeatsCreature() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Forest", "Forest", "Forest");
        card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Gemrazer", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("(Mutate)"))
                .on("Choose the TARGET", choiceWith("Grizzly Bears"));
        playAndResolve(seat(g, model, model), g, "Gemrazer's mutate");
        Assert.assertEquals(me.getCreaturesInPlay().size(), 1, "one merged creature");
        Assert.assertEquals(me.getCreaturesInPlay().get(0).getMergedCards().size(), 2, "Gemrazer merged with the Bears");
    }

    @Test
    public void surgeAfterAnotherSpell() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        lands(me, "Mountain", "Mountain", "Mountain");
        card("Lightning Bolt", me, ZoneType.Hand);
        card("Boulder Salvo", me, ZoneType.Hand);
        card("Grizzly Bears", rival, ZoneType.Battlefield);
        card("Craw Wurm", rival, ZoneType.Battlefield);
        String[] next = {"Lightning Bolt"};
        Script model = new Script("CHOICE: 1").on("You have priority", p -> choiceWith(next[0]).apply(p))
                .on("Choose the TARGET for Lightning Bolt", choiceWith("Grizzly Bears"))
                .on("Choose the TARGET for Boulder Salvo", choiceWith("Craw Wurm"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        playAndResolve(ctrl, g, "Lightning Bolt");
        next[0] = "(Surge)";
        playAndResolve(ctrl, g, "Boulder Salvo's surge");
        Assert.assertFalse(has(rival, ZoneType.Battlefield, "Craw Wurm"), "the surged Salvo dealt 4 to the Wurm");
    }

    @Test
    public void multikickerCountIsTheSeats() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Plains", "Plains", "Plains", "Plains", "Plains", "Plains", "Plains");
        card("Apex Hawks", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Apex Hawks"))
                .on("Multikicker", p -> {   // seven Plains pay {2}{W} and two {1}{W} kicks: 0, 1 or 2 offered
                    Assert.assertTrue(p.contains("3) 2") && !p.contains("4) 3"), "only the payable kicks: " + p);
                    return "CHOICE: " + opt(p, "2");
                })
                .on("Every integer", p -> "NUMBER: 2");
        playAndResolve(seat(g, model, model), g, "Apex Hawks, kicked twice");
        Card h = me.getCardsIn(ZoneType.Battlefield).stream().filter(c -> c.getName().equals("Apex Hawks")).findFirst().orElse(null);
        Assert.assertNotNull(h, "Apex Hawks was cast");
        Assert.assertEquals(h.getCounters(forge.game.card.CounterEnumType.P1P1), 2, "kicked twice: two counters");
    }

    @Test
    public void bargainSacrificesAnArtifact() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        lands(me, "Mountain");
        card("Ornithopter", me, ZoneType.Battlefield);
        card("Torch the Tower", me, ZoneType.Hand);
        card("Hill Giant", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Torch the Tower"))
                .on("optional additional costs", p -> "PICKS: 1").on("Choose the TARGET", choiceWith("Hill Giant"));
        playAndResolve(seat(g, model, model), g, "Torch the Tower, bargained");
        Assert.assertTrue(has(me, ZoneType.Graveyard, "Ornithopter"), "bargained: the Ornithopter was sacrificed");
        Assert.assertFalse(has(rival, ZoneType.Battlefield, "Hill Giant"), "bargained: 3 damage killed the 3/3");
    }

    @Test
    public void offeringSacrificesAnArtifact() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Mountain", "Mountain", "Mountain", "Mountain", "Mountain", "Mountain");
        card("Wurmcoil Engine", me, ZoneType.Battlefield);
        card("Blast-Furnace Hellkite", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("(Offering)"));
        playAndResolve(seat(g, model, model), g, "Hellkite by offering");
        Assert.assertTrue(has(me, ZoneType.Battlefield, "Blast-Furnace Hellkite"), "cast by offering");
        Assert.assertTrue(has(me, ZoneType.Graveyard, "Wurmcoil Engine"), "the artifact was sacrificed");
    }

    /** Foretell from hand (the special action), then on a later turn the foretold cast from exile. The later turn is
     *  stood in for by marking the exile as one turn earlier (the engine's rule is enteredThisTurn). */
    @Test
    public void foretellThenCastTheForetoldCard() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        lands(me, "Island", "Island", "Island", "Island");
        card("Saw It Coming", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Saw It Coming"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        playAndResolve(ctrl, g, "foretelling Saw It Coming");
        Card foretold = me.getCardsIn(ZoneType.Exile).stream().filter(Card::isForetold).findFirst().orElse(null);
        Assert.assertNotNull(foretold, "foretold: exiled face down");
        for (Card l : me.getCardsIn(ZoneType.Battlefield)) l.setTapped(false);
        foretold.setTurnInZone(g.getPhaseHandler().getTurn() - 1);
        LineFindingProbeTest.rivalCasts(g, "Ponder", null);
        Script cast = new Script("CHOICE: 1").on("You have priority", choiceWith("(Foretold)"));
        LlmSpikeGui.PlayerControllerLLM ctrl2 = seat(g, cast, cast);
        playAndResolve(ctrl2, g, "the foretold Saw It Coming");
        Assert.assertTrue(cast.prompts.stream().noneMatch(q -> q.contains("Saw It Coming 2/2")), "a face-down counterspell named as a 2/2");
        Assert.assertTrue(has(rival, ZoneType.Graveyard, "Ponder"), "Ponder was countered");
        Assert.assertTrue(has(me, ZoneType.Graveyard, "Saw It Coming"));
    }

    /** Plot from hand (the special action), then on a later turn the plotted cast, free, at sorcery speed. */
    @Test
    public void plotThenCastThePlottedCardFree() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Forest", "Forest");
        card("Aloe Alchemist", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("ACTIVATE Aloe Alchemist"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        playAndResolve(ctrl, g, "plotting Aloe Alchemist");
        Card plotted = me.getCardsIn(ZoneType.Exile).stream().filter(c -> c.getName().equals("Aloe Alchemist")).findFirst().orElse(null);
        Assert.assertNotNull(plotted, "plotted: exiled");
        for (Card l : me.getCardsIn(ZoneType.Battlefield)) l.setTapped(true);   // free: no mana needed
        plotted.setTurnInZone(g.getPhaseHandler().getTurn() - 1);
        Script cast = new Script("CHOICE: 1").on("You have priority", choiceWith("Aloe Alchemist"));
        playAndResolve(seat(g, cast, cast), g, "the plotted Aloe Alchemist");
        Assert.assertTrue(has(me, ZoneType.Battlefield, "Aloe Alchemist"), "cast from exile without paying");
    }

    /** An "up to" cost reduction (Mana Matrix) is the seat's choice when it pays, and is not asked while the menu and
     *  the hand are priced: an ask inside a snapshot build re-enters the snapshot (issue #225). */
    @Test
    public void anUpToCostReductionIsAskedWhenPayingNotWhilePricing() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Island", "Island");
        card("Mana Matrix", me, ZoneType.Battlefield);
        card("Fact or Fiction", me, ZoneType.Hand);
        for (int i = 0; i < 5; i++) card("Island", me, ZoneType.Library);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Fact or Fiction"))
                .on("reduce the cost by", choice("2"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, "Fact or Fiction was offered at the reduced cost");
        Assert.assertTrue(model.prompts.stream().noneMatch(q -> q.contains("reduce the cost by")), "a reduction was asked while pricing");
        Assert.assertTrue(ctrl.playChosenSpellAbility(r.get(0)), "it was cast");
        Assert.assertTrue(model.prompts.stream().anyMatch(q -> q.contains("reduce the cost by")), "the reduction was the seat's when paying");
        Assert.assertTrue(me.getCardsIn(ZoneType.Battlefield).stream().filter(Card::isLand).allMatch(Card::isTapped), "paid with the two Islands");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** A damage assignment order already declared (overrideOrder false: the legacy order) is followed, not asked again:
     *  an answer in another order would assign damage against the order the engine holds (issue #224). */
    @Test
    public void aDeclaredDamageOrderIsFollowedNotAskedAgain() {
        Game g = bareGame(PhaseType.COMBAT_DAMAGE);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card attacker = card("Craw Wurm", me, ZoneType.Battlefield);
        Card first = card("Grizzly Bears", rival, ZoneType.Battlefield), second = card("Llanowar Elves", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1").on("COMBAT DAMAGE ASSIGNMENT", p -> "PICKS: 2,1");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Map<Card, Integer> r = ctrl.assignCombatDamage(attacker, new CardCollection(java.util.Arrays.asList(first, second)), null, 6, rival, false);
        Assert.assertTrue(model.prompts.stream().noneMatch(q -> q.contains("COMBAT DAMAGE ASSIGNMENT")), "the declared order was asked again");
        Assert.assertTrue(r.get(first) >= 2, "lethal to the first in the declared order: " + r);
        Assert.assertEquals(r.values().stream().mapToInt(Integer::intValue).sum(), 6);
        // With damage divided as the attacker chooses (overrideOrder true), the amounts are the seat's.
        Script free2 = new Script("CHOICE: 1").on("COMBAT DAMAGE ASSIGNMENT", p -> "ALLOC: 0, 1");
        Map<Card, Integer> free = seat(g, free2, free2).assignCombatDamage(attacker, new CardCollection(java.util.Arrays.asList(first, second)), null, 1, rival, true);
        Assert.assertTrue(free2.prompts.stream().anyMatch(q -> q.contains("COMBAT DAMAGE ASSIGNMENT")), "a free split is asked");
        Assert.assertEquals(free.get(second), Integer.valueOf(1), "the seat put its 1 damage on the Elves: " + free);
    }

    @Test
    public void bothKickerCostsAreTheSeats() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        lands(me, "Tropical Island", "Tropical Island", "Bayou", "Bayou", "Bayou", "Bayou", "Bayou", "Bayou");
        card("Ana Battlemage", me, ZoneType.Hand);
        card("Grizzly Bears", rival, ZoneType.Battlefield);
        for (int i = 0; i < 3; i++) card("Ponder", rival, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Ana Battlemage"))
                .on("optional additional costs", p -> "PICKS: 1,2")
                .on("Choose the TARGET", p -> p.contains("Tap target untapped creature") ? choiceWith("Grizzly Bears").apply(p) : choiceWith("Rival").apply(p));
        playAndResolve(seat(g, model, model), g, "Ana Battlemage, kicked twice");
        Assert.assertTrue(has(me, ZoneType.Battlefield, "Ana Battlemage"));
        Assert.assertEquals(rival.getCardsIn(ZoneType.Hand).size(), 0, "the blue kicker: three discarded");
        Assert.assertEquals(rival.getLife(), 18, "the black kicker: the tapped Bears dealt 2 to its controller");
    }

    /** Two kickers each payable alone but not together: the set is refused with the reason and asked again. */
    @Test
    public void optionalCostsTheSeatCannotPayTogetherAreAskedAgain() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        lands(me, "Tropical Island", "Tropical Island", "Bayou", "Bayou", "Bayou", "Bayou");
        card("Ana Battlemage", me, ZoneType.Hand);
        for (int i = 0; i < 3; i++) card("Ponder", rival, ZoneType.Hand);
        int[] asks = {0};
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Ana Battlemage"))
                .on("optional additional costs", p -> ++asks[0] == 1 ? "PICKS: 1,2" : "PICKS: 1").on("Choose the TARGET", choiceWith("Rival"));
        playAndResolve(seat(g, model, model), g, "Ana Battlemage, one kicker");
        Assert.assertEquals(asks[0], 2, "the unpayable pair was asked again");
        Assert.assertTrue(model.prompts.stream().anyMatch(q -> q.contains("did not come back payable")));
        Assert.assertTrue(has(me, ZoneType.Battlefield, "Ana Battlemage"), "cast with the one kicker it could pay");
        Assert.assertEquals(rival.getCardsIn(ZoneType.Hand).size(), 0, "the blue kicker it chose");
    }

    /** A second unpayable pick takes the deterministic decline-all fallback, and the base cast still
     *  goes through: no kicker was paid (the rival's hand and life untouched). */
    @Test
    public void aTwiceUnpayableKickerSetDeclinesAll() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        lands(me, "Tropical Island", "Tropical Island", "Bayou", "Bayou", "Bayou", "Bayou");
        card("Ana Battlemage", me, ZoneType.Hand);
        for (int i = 0; i < 3; i++) card("Ponder", rival, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Ana Battlemage"))
                .on("optional additional costs", p -> "PICKS: 1,2");
        String log = LineFindingProbeTest.logOf(() -> playAndResolve(seat(g, model, model), g, "Ana Battlemage"));
        Assert.assertEquals(model.prompts.stream().filter(q -> q.contains("optional additional costs")).count(), 2L,
                "the unpayable pair was asked twice");
        Assert.assertTrue(log.contains("[AUTO-LEGAL] optional-costs(together unpayable)->decline-all"), log);
        Assert.assertTrue(has(me, ZoneType.Battlefield, "Ana Battlemage"), "the base cast is itself payable");
        Assert.assertEquals(rival.getCardsIn(ZoneType.Hand).size(), 3, "no kicker ran on the decline");
        Assert.assertEquals(rival.getLife(), 20, "no kicker ran on the decline");
    }

    /** Pay extra for flash (Rout): on the opponent's turn the sorcery is offered with that cost, and only with it. */
    @Test
    public void payingForFlashCastsASorceryOnTheOpponentsTurn() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        lands(me, "Plains", "Plains", "Plains", "Plains", "Plains", "Plains", "Plains");
        card("Rout", me, ZoneType.Hand);
        card("Grizzly Bears", rival, ZoneType.Battlefield);
        LineFindingProbeTest.rivalCasts(g, "Ponder", null);   // the rival's turn, with something to respond to
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Rout"));
        playAndResolve(seat(g, model, model), g, "Rout with flash");
        Assert.assertFalse(has(rival, ZoneType.Battlefield, "Grizzly Bears"), "Rout resolved on the opponent's turn");
        Assert.assertTrue(model.prompts.stream().anyMatch(q -> q.matches("(?s).*\\d\\) CAST Rout[^\\n]*\\(Flash\\).*")), "the option names the flash cost");
    }

    @Test
    public void promisingAGiftIsTheSeats() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        lands(me, "Island", "Island", "Island", "Island", "Island");
        card("Archival Whorl", me, ZoneType.Hand);
        for (int i = 0; i < 10; i++) { card("Island", me, ZoneType.Library); card("Island", rival, ZoneType.Library); }
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Archival Whorl"))
                .on("optional additional costs", p -> "PICKS: 1");
        playAndResolve(seat(g, model, model), g, "Archival Whorl, gift promised");
        Assert.assertTrue(model.prompts.stream().anyMatch(q -> q.contains("optional additional costs") && q.contains("Gift")), "the gift was offered");
        // The promise reached the engine: the opponent got the gift. (The card's "if the gift wasn't promised" draw is
        // Forge's script: ConditionPresent$ Card.PromisedGift counts the battlefield, where the spell never is, so that
        // branch runs whatever is promised, for every player. Not the seat's; card scripts are not patched.)
        Assert.assertTrue(has(rival, ZoneType.Battlefield, "Rhystic Study"), "the opponent got the gift: " + rival.getCardsIn(ZoneType.Battlefield));
    }

    /** A Defiler's "pay 2 life, it costs one coloured mana less" for each colour: with no lands, the life payment is the
     *  only way to cast the one-pip permanent, so the menu offers the cast with it and nothing else. Pricing that
     *  variant once left the reduction on the plain spell (a copy shares the list), which was then offered and cast
     *  for nothing. */
    @Test
    public void aDefilersLifePaymentReducesTheColour() {
        String[][] pairs = {{"Defiler of Faith", "Savannah Lions"}, {"Defiler of Dreams", "Delver of Secrets"},
                {"Defiler of Flesh", "Carrion Feeder"}, {"Defiler of Instinct", "Goblin Guide"}, {"Defiler of Vigor", "Llanowar Elves"}};
        for (String[] pr : pairs) {
            Game g = bareGame(PhaseType.MAIN1);
            Player me = g.getPlayers().get(0);
            card(pr[0], me, ZoneType.Battlefield);
            card(pr[1], me, ZoneType.Hand);
            for (int pass = 0; pass < 2; pass++) {
                long offers = LlmSpikeGui.PlayerControllerLLM.enumeratePlayOptions(me, true).spells.stream()
                        .filter(sa -> sa.getHostCard().getName().equals(pr[1])).count();
                Assert.assertEquals(offers, 1L, pr[1] + ": only the cast with the life payment is offered (pass " + pass + ")");
            }
            String[] menu = new String[1];
            Script model = new Script("CHOICE: 1").on("You have priority", p -> { menu[0] = p; return choiceWith(pr[1]).apply(p); });
            playAndResolve(seat(g, model, model), g, pr[1] + " with " + pr[0]);
            Assert.assertTrue(has(me, ZoneType.Battlefield, pr[1]), pr[1] + " was cast for 2 life");
            // Defiler of Instinct's own trigger deals 1 damage to any target; the seat may pick itself.
            Assert.assertTrue(me.getLife() == 18 || pr[0].equals("Defiler of Instinct") && me.getLife() == 17, pr[0] + ": life " + me.getLife());
            Assert.assertTrue(menu[0].contains("cost: Pay 2 life ["), pr[0] + ": the option names the cost paid, no mana: " + menu[0]);
        }
    }

    /** Declining a Defiler's life payment pays the mana: pricing the payment for the offer used to leave its {W}
     *  reduction on the spell, so the declined cast was free. */
    @Test
    public void aDeclinedLifePaymentPaysTheMana() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Plains");
        card("Defiler of Faith", me, ZoneType.Battlefield);
        card("Savannah Lions", me, ZoneType.Hand);
        boolean[] asked = {false};
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Savannah Lions"))
                .on("optional additional costs", p -> { asked[0] = true; return "PICKS: NONE"; });
        playAndResolve(seat(g, model, model), g, "Savannah Lions, life payment declined");
        Assert.assertTrue(asked[0], "the life payment was the seat's to decline");
        Assert.assertTrue(has(me, ZoneType.Battlefield, "Savannah Lions"), "cast for {W}");
        Assert.assertEquals(me.getLife(), 20, "no life paid");
        Assert.assertTrue(me.getCardsIn(ZoneType.Battlefield).stream().anyMatch(c -> c.getName().equals("Plains") && c.isTapped()),
                "the Plains paid the {W} the declined payment would have taken off");
    }

    /** An optional additional cost a static gives (Analyze the Pollen's collect evidence 8) is the seat's. */
    @Test
    public void collectingEvidenceIsTheSeats() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Forest");
        card("Griselbrand", me, ZoneType.Graveyard);
        card("Craw Wurm", me, ZoneType.Library);
        card("Forest", me, ZoneType.Library);
        card("Analyze the Pollen", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Analyze the Pollen"))
                .on("optional additional costs", p -> "PICKS: 1").on("Craw Wurm", choiceWith("Craw Wurm"));
        playAndResolve(seat(g, model, model), g, "Analyze the Pollen, evidence collected");
        Assert.assertTrue(has(me, ZoneType.Exile, "Griselbrand"), "evidence collected: Griselbrand exiled");
        Assert.assertTrue(has(me, ZoneType.Hand, "Craw Wurm"), "with evidence it finds a creature");
    }

    @Test
    public void impendingEntersAsANonCreature() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Island", "Island", "Island", "Island");
        card("Lurker in the Deep", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("(Impending)"));
        playAndResolve(seat(g, model, model), g, "Lurker in the Deep, impending");
        Card l = me.getCardsIn(ZoneType.Battlefield).stream().filter(c -> c.getName().equals("Lurker in the Deep")).findFirst().orElse(null);
        Assert.assertNotNull(l, "it was cast");
        Assert.assertFalse(l.isCreature(), "impending: not a creature yet");
        Assert.assertEquals(l.getCounters(forge.game.card.CounterEnumType.TIME), 3, "three time counters");
    }

    @Test
    public void warpCastsForTheWarpCost() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        lands(me, "Plains", "Plains");
        card("All-Fates Stalker", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("(Warp)"));
        playAndResolve(seat(g, model, model), g, "All-Fates Stalker, warped");
        Assert.assertTrue(has(me, ZoneType.Battlefield, "All-Fates Stalker"), "warped onto the battlefield");
    }

    /** ANT's line: cast Infernal Tutor, then crack Lion's Eye Diamond in response so the hand is empty when the Tutor
     *  resolves (hellbent). The menu never offered a mana ability, so LED was usable only as payment (owner,
     *  2026-10-16: combo lines must play out). A mana ability with a cost beyond tapping (sacrifice, discard, exile
     *  from hand) is a decision: it is offered, its colour is the seat's, and its mana floats. */
    @Test
    public void lionsEyeDiamondCanBeCrackedInResponseToInfernalTutor() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Swamp", me, ZoneType.Battlefield);
        card("Swamp", me, ZoneType.Battlefield);
        card("Lion's Eye Diamond", me, ZoneType.Battlefield);
        card("Infernal Tutor", me, ZoneType.Hand);
        card("Brainstorm", me, ZoneType.Hand);
        for (String n : new String[] {"Tendrils of Agony", "Island", "Ad Nauseam"}) card(n, me, ZoneType.Library);
        String[] next = {"CAST Infernal Tutor"};
        Script model = new Script("CHOICE: 1")
                .on("You have priority", p -> choice(next[0]).apply(p))
                .on("Choose a color", choice("black"))
                .on("MANA:", p -> "PICKS: NONE")
                .on("Tendrils of Agony", p -> {      // the Tutor's search: the payoff
                    Matcher m = Pattern.compile("(?m)^\\s*(\\d+)\\) Tendrils of Agony").matcher(p.substring(p.lastIndexOf("DECISION:")));
                    return m.find() ? "CHOICE: " + m.group(1) : "CHOICE: 1";
                });
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, "Infernal Tutor offered");
        ctrl.playChosenSpellAbility(r.get(0));
        Assert.assertFalse(g.getStack().isEmpty(), "the Tutor is on the stack");
        next[0] = "ACTIVATE Lion's Eye Diamond";
        List<SpellAbility> led = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(led, "Lion's Eye Diamond is offered with the Tutor on the stack");
        ctrl.playChosenSpellAbility(led.get(0));
        Assert.assertTrue(me.getCardsIn(ZoneType.Hand).isEmpty(), "LED discarded the hand: hellbent");
        Assert.assertEquals(me.getManaPool().totalMana(), 3, "three mana floats");
        while (!g.getStack().isEmpty()) g.getStack().resolveStack();
        Assert.assertTrue(me.getCardsIn(ZoneType.Hand).stream().anyMatch(c -> c.getName().equals("Tendrils of Agony")),
                "hellbent, the Tutor fetched the payoff");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** play -> target -> mana-sources: the menu pick, the target pick and the kept source are all used. */
    @Test
    public void aCastIsAskedAsPlayThenTargetThenWhichSourceStaysUp() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card volc = card("Volcanic Island", me, ZoneType.Battlefield);
        Card mountain = card("Mountain", me, ZoneType.Battlefield);
        card("Lightning Bolt", me, ZoneType.Hand);
        Card lions = card("Savannah Lions", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1")
                .on("You have priority", choice("CAST Lightning Bolt"))
                .on("Choose the TARGET", choice("Savannah Lions"))
                .on("MANA:", picks("Volcanic Island"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r);
        Assert.assertTrue(r.get(0).getTargets().contains(lions), "the target the model named");
        ctrl.playChosenSpellAbility(r.get(0));
        Assert.assertFalse(volc.isTapped(), "the source the model kept is untapped");
        Assert.assertTrue(mountain.isTapped());
    }

    /** A keep set the rest cannot pay is explained and asked once more; the engine's own plan never
     *  replaces the seat's choice (it did, 729 times in recorded games, as "->engine plan"). */
    @Test
    public void aKeepSetThatCannotPayIsAskedAgainNotHandedToTheEngine() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card volc = card("Volcanic Island", me, ZoneType.Battlefield);
        Card mountain = card("Mountain", me, ZoneType.Battlefield);
        card("Island", me, ZoneType.Battlefield);
        card("Lightning Bolt", me, ZoneType.Hand);
        card("Savannah Lions", rival, ZoneType.Battlefield);
        int[] manaAsks = {0};
        Script model = new Script("CHOICE: 1")
                .on("You have priority", choice("CAST Lightning Bolt"))
                .on("Choose the TARGET", choice("Savannah Lions"))
                .on("MANA:", p -> ++manaAsks[0] == 1 ? picks("Volcanic Island", "Mountain").apply(p) : picks("Mountain").apply(p));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        ctrl.playChosenSpellAbility(r.get(0));
        Assert.assertEquals(manaAsks[0], 2, "the unpayable keep set was not asked again");
        Assert.assertTrue(model.asked("unable to pay"), "the second ask did not say why");
        Assert.assertFalse(mountain.isTapped(), "the source kept on the second answer is untapped");
        Assert.assertTrue(volc.isTapped(), "the red came from the Volcanic Island");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** On the opponent's turn the hand called a creature "(castable)" because its mana was there,
     *  while the play menu (instant speed) could not cast it. */
    @Test
    public void theHandCallsCastableOnlyWhatTheMenuCanCast() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Forest", me, ZoneType.Battlefield);
        card("Mountain", me, ZoneType.Battlefield);
        card("Grizzly Bears", me, ZoneType.Hand);
        card("Lightning Bolt", me, ZoneType.Hand);
        LineFindingProbeTest.rivalCasts(g, "Ponder", null);
        Script model = new Script("CHOICE: 1").on("You have priority", choice("PASS"));
        Assert.assertNull(seat(g, model, model).chooseSpellAbilityToPlay(), "the model passed");
        String p = PromptFidelityTest.promptWith(model, "DECISION: You have priority");
        Assert.assertTrue(p.matches("(?s).*Grizzly Bears[^,\\n]*\\(payable, not castable now\\).*"), p);
        Assert.assertTrue(p.matches("(?s).*Lightning Bolt[^,\\n]*\\(castable\\).*"), p);
    }

    /** Free spells are kept up with no mana: Force of Will (exile a blue card, 1 life), Surgical Extraction
     *  (its Phyrexian mana paid with life) and Endurance (flash; evoke by exiling a green card). With no land at
     *  all and the opponent's spell on the stack, the seat is asked, and each is on the menu: no skip may pass
     *  a window in which the seat can cast them. */
    @Test
    public void freeSpellsAreOfferedWithNoManaUp() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Force of Will", me, ZoneType.Hand);
        card("Brainstorm", me, ZoneType.Hand);
        card("Surgical Extraction", me, ZoneType.Hand);
        card("Endurance", me, ZoneType.Hand);
        card("Llanowar Elves", me, ZoneType.Hand);
        card("Griselbrand", rival, ZoneType.Graveyard);
        LineFindingProbeTest.rivalCasts(g, "Ponder", null);
        Script model = new Script("CHOICE: 1").on("You have priority", choice("PASS"));
        Assert.assertNull(seat(g, model, model).chooseSpellAbilityToPlay(), "the model passed");
        String p = PromptFidelityTest.promptWith(model, "DECISION: You have priority");
        Assert.assertNotNull(p, "the seat was not asked: a window with free spells was skipped");
        String menu = p.substring(p.lastIndexOf("DECISION:"));
        for (String free : new String[] {"Force of Will", "Surgical Extraction", "Endurance"})
            Assert.assertTrue(menu.contains("CAST " + free), free + " is not on the menu with no mana up: " + menu);
    }

    /** Phyrexian mana: the split the model picks is the one paid. */
    @Test
    public void howAPhyrexianSymbolIsPaidIsTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Island", me, ZoneType.Battlefield);
        card("Gitaxian Probe", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1")
                .on("You have priority", choice("CAST Gitaxian Probe"))
                .on("Each Phyrexian symbol", choice("pay 1 Phyrexian symbol with 2 life"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r);
        ctrl.playChosenSpellAbility(r.get(0));
        Assert.assertEquals(me.getLife(), 18);
    }

    /** With no blue source, Gitaxian Probe is paid with 2 life, though its script tells Forge's AI never to pay it so
     *  (AIPhyrexianPayment$ Never): the menu asked the AI's payment check, and with one way to pay the payment was
     *  the AI's too, so the Probe was never offered and could not be cast (hard draft h_0, 2026-10-20). */
    @Test
    public void aPhyrexianSpellPayableOnlyWithLifeIsCastWithLife() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        card("Gitaxian Probe", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choice("CAST Gitaxian Probe"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, "the Probe was not on the menu: " + PromptFidelityTest.promptWith(model, "DECISION:"));
        Assert.assertEquals(r.get(0).getHostCard().getName(), "Gitaxian Probe");
        int autos = ctrl.autoPicksCount();
        Assert.assertTrue(ctrl.playChosenSpellAbility(r.get(0)), "the Probe was not paid for");
        Assert.assertEquals(me.getLife(), 18);
        // Paying with life was the only way: the harness paid it, so it says so with a tier=auto row (standards, #277).
        Assert.assertEquals(ctrl.autoPicksCount(), autos + 1, "the forced life payment left no auto row");
    }

    /** The game line stamps the rules lookup as wired, not as asked for: RULES_TOOL=1 without MEMORY_TOOLS=1 never
     *  offers it, and the stamp said rules_tool=true for those games (pr-review of PR #272). */
    @Test
    public void theRulesToolIsStampedOnlyWhenTheSeatIsOfferedIt() {
        Assert.assertTrue(LlmSpikeGui.PlayerControllerLLM.rulesToolWired(true, true));
        Assert.assertFalse(LlmSpikeGui.PlayerControllerLLM.rulesToolWired(false, true));
        Assert.assertFalse(LlmSpikeGui.PlayerControllerLLM.rulesToolWired(true, false));
    }

    /** What one attempt spent, for its puzzle result row (BENCHMARK-V2 section 3: the budget recorded per attempt):
     *  the model calls, their prompt and completion tokens and milliseconds, and the lookups. */
    @Test
    public void aSeatCountsWhatItsDecisionsSpent() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        card("Lightning Bolt", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1") {
            // A call that takes time, so model_ms has something to count (pr-review of #282: >= 0 could not fail).
            @Override public String ask(String prompt) {
                try { Thread.sleep(5); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
                return super.ask(prompt);
            }
            @Override public int getLastPromptTokens() { return 100; }
            @Override public int getLastCompletionTokens() { return 7; }
        };
        model.on("You have priority", choice("PASS"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Assert.assertEquals(ctrl.budget().get("model_calls"), 0);
        ctrl.chooseSpellAbilityToPlay();
        java.util.Map<String, Object> b = ctrl.budget();
        Assert.assertEquals(b.get("model_calls"), 1, String.valueOf(b));
        Assert.assertEquals(b.get("prompt_tokens"), 100L);
        Assert.assertEquals(b.get("completion_tokens"), 7L);
        Assert.assertTrue((Long) b.get("model_ms") >= 5, String.valueOf(b));
        Assert.assertEquals(b.get("lookups"), 0);
    }

    /** A bridge that reports no billing and fails its next ask still shows the last call's usage; the budget must not
     *  count it twice (pr-review of #282: a dead bridge reported a full call's tokens it never spent). */
    @Test
    public void aFailedAskAddsNoTokensToTheBudget() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        card("Lightning Bolt", me, ZoneType.Hand);
        java.util.concurrent.atomic.AtomicBoolean down = new java.util.concurrent.atomic.AtomicBoolean();
        Script model = new Script("CHOICE: 1") {
            @Override public String ask(String prompt) { return down.get() ? null : super.ask(prompt); }
            @Override public int getLastPromptTokens() { return 100; }
            @Override public int getLastCompletionTokens() { return 7; }
        };
        model.on("You have priority", choice("PASS"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        ctrl.chooseSpellAbilityToPlay();
        down.set(true);
        card("Shock", me, ZoneType.Hand);   // a new spot, so the seat is asked again rather than passing it
        ctrl.chooseSpellAbilityToPlay();
        java.util.Map<String, Object> b = ctrl.budget();
        Assert.assertTrue((Integer) b.get("model_calls") >= 2, "the second spot was asked: " + b);
        Assert.assertEquals(b.get("prompt_tokens"), 100L, String.valueOf(b));
        Assert.assertEquals(b.get("completion_tokens"), 7L);
    }

    /** A bridge that keeps a bill is read by its bill: 80 billed for an answered ask whose last call reported 30 (a
     *  truncation retry's first send), and a failed ask's billed sends too (pr-review of #282: no test reached this
     *  branch, which every OpenRouter seat takes). */
    @Test
    public void aBilledBridgeIsReadByItsBill() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        card("Lightning Bolt", me, ZoneType.Hand);
        java.util.concurrent.atomic.AtomicBoolean down = new java.util.concurrent.atomic.AtomicBoolean();
        long[] bill = {0, 0};
        int[] failed = {0};
        Script model = new Script("CHOICE: 1") {
            @Override public String ask(String prompt) {
                if (down.get()) { failed[0]++; bill[0] += 50; bill[1] += 2; return null; }   // a billed non-answer
                bill[0] += 80; bill[1] += 9;
                return super.ask(prompt);
            }
            @Override public int getLastPromptTokens() { return 30; }
            @Override public int getLastCompletionTokens() { return 4; }
            @Override public long getBilledPromptTokens() { return bill[0]; }
            @Override public long getBilledCompletionTokens() { return bill[1]; }
        };
        model.on("You have priority", choice("PASS"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        ctrl.chooseSpellAbilityToPlay();
        down.set(true);
        card("Shock", me, ZoneType.Hand);   // a new spot, so the seat is asked again
        ctrl.chooseSpellAbilityToPlay();
        java.util.Map<String, Object> b = ctrl.budget();
        Assert.assertTrue((Integer) b.get("model_calls") >= 2, "the second spot was asked: " + b);
        // A send the provider billed counts even when its reply was a non-answer (pr-review of #282 at 54b895b).
        Assert.assertTrue(failed[0] >= 1, "the second spot's asks failed");
        Assert.assertEquals(b.get("prompt_tokens"), 80L + 50L * failed[0], String.valueOf(b));
        Assert.assertEquals(b.get("completion_tokens"), 9L + 2L * failed[0]);
    }

    /** X, then the modes of a modal spell, each from the model. */
    @Test
    public void xAndModesAreTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        for (int i = 0; i < 3; i++) card("Forest", me, ZoneType.Battlefield);
        card("Green Sun's Zenith", me, ZoneType.Hand);
        card("Llanowar Elves", me, ZoneType.Library);
        Script model = new Script("CHOICE: 1")
                .on("You have priority", choice("CAST Green Sun's Zenith"))
                .on("Choose X for", choice("1"));
        List<SpellAbility> r = seat(g, model, model).chooseSpellAbilityToPlay();
        Assert.assertNotNull(r);
        Assert.assertEquals(r.get(0).getXManaCostPaid(), Integer.valueOf(1));

        Game g2 = bareGame(PhaseType.MAIN1);
        Player me2 = g2.getPlayers().get(0), rival2 = g2.getPlayers().get(1);
        card("Badlands", me2, ZoneType.Battlefield);
        card("Badlands", me2, ZoneType.Battlefield);
        card("Mountain", me2, ZoneType.Battlefield);
        card("Kolaghan's Command", me2, ZoneType.Hand);
        card("Savannah Lions", rival2, ZoneType.Battlefield);
        card("Grizzly Bears", rival2, ZoneType.Hand);
        Script m2 = new Script("CHOICE: 1")
                .on("You have priority", choice("CAST Kolaghan's Command"))
                .on("MODAL SPELL", picks("Target player discards a card.", "Kolaghan's Command deals 2 damage to any target."));
        List<SpellAbility> r2 = seat(g2, m2, m2).chooseSpellAbilityToPlay();
        Assert.assertNotNull(r2, PromptFidelityTest.decisions(m2));
        StringBuilder modes = new StringBuilder();
        for (SpellAbility part = r2.get(0); part != null; part = part.getSubAbility()) modes.append(part.getDescription()).append('|');
        Assert.assertTrue(modes.toString().contains("discards") && modes.toString().contains("2 damage"), modes.toString());
        Assert.assertNotNull(rival); Assert.assertNotNull(rival2);
    }

    /** Which cast mode of a card with two (a split card's halves). */
    @Test
    public void whichWayToPlayACardIsTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card fi = card("Fire // Ice", me, ZoneType.Hand);
        List<SpellAbility> modes = new ArrayList<>();
        for (SpellAbility sa : fi.getAllSpellAbilities()) if (sa.isSpell()) { sa.setActivatingPlayer(me); modes.add(sa); }
        Assert.assertTrue(modes.size() >= 2, modes.toString());
        Script model = new Script("CHOICE: 1").on("Choose HOW to play", p -> "CHOICE: 2");
        Assert.assertSame(seat(g, model, model).getAbilityToPlay(fi, modes, null), modes.get(1));
    }

    /** "You may cast it" from an effect: the model's no is used. */
    @Test
    public void castingFromAnEffectIsTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        Card bolt = card("Lightning Bolt", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("An effect lets you play this now", choice("Do not cast it"));
        Assert.assertFalse(seat(g, model, model).playSaFromPlayEffect(spell(bolt, me)));
        // The label opens with its verb: the question read "An effect lets you cast CAST Lightning Bolt" (runs/loop/g02).
        Assert.assertFalse(model.prompts.stream().anyMatch(q -> q.contains("cast CAST")), "the verb is doubled");
        Assert.assertTrue(bolt.isInZone(ZoneType.Hand));
        source(bolt);
    }

    // ------------------------------------------------------------------ the game's start

    /** A companion is the seat's to reveal or not: a declined companion is no companion, not a crash (engine patch in
     *  Player.assignCompanion; four Vintage top-8 decks crashed at game start, 2026-10-16). */
    @Test
    public void aCompanionTheSeatDeclinesIsNoCompanionNotACrash() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card lurrus = card("Lurrus of the Dream-Den", me, ZoneType.Sideboard);
        for (int i = 0; i < 10; i++) card("Grizzly Bears", me, ZoneType.Library);
        Script model = new Script("CHOICE: 1") {
            @Override public String ask(String prompt) {
                prompts.add(prompt);
                return "CHOICE: " + opt(prompt, "Decline / none");
            }
        };
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        me.assignCompanion(g, ctrl);
        Assert.assertEquals(model.prompts.size(), 1, "the seat was asked");
        Assert.assertTrue(me.getCardsIn(ZoneType.Command).isEmpty(), "no companion");
        Assert.assertTrue(me.getCardsIn(ZoneType.Sideboard).contains(lurrus), "Lurrus stays in the sideboard");
    }

    /** A five-card hand is the seat's to keep or mulligan: the harness kept it on its own (runs/luna-std/g13). */
    @Test
    public void aFiveCardHandIsStillTheSeatsMulligan() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        for (String n : new String[] {"Mountain", "Lightning Bolt", "Ponder", "Brainstorm", "Counterspell"})
            card(n, me, ZoneType.Hand);
        Script model = new Script("MULLIGAN");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Assert.assertFalse(ctrl.mulliganKeepHand(me, 2), "the model's MULLIGAN at five cards");
        Assert.assertEquals(model.prompts.size(), 1, "the five-card hand was asked");
    }

    /** A seven with no land is asked (with the observation), the retry carries the whole ask, and
     *  the model's mulligan is used; then which card goes to the bottom. */
    @Test
    public void theMulliganAndTheBottomAreTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        for (String n : new String[] {"Lightning Bolt", "Ponder", "Brainstorm", "Grizzly Bears", "Savannah Lions", "Llanowar Elves", "Counterspell"})
            card(n, me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1") {
            @Override public String ask(String prompt) {
                prompts.add(prompt);
                if (prompt.contains("MULLIGAN")) return prompts.size() == 1 ? "Hmm, no lands at all." : "MULLIGAN";
                return "PICKS: " + opt(prompt, "Counterspell");
            }
        };
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Assert.assertFalse(ctrl.mulliganKeepHand(me, 0), "the model's MULLIGAN");
        Assert.assertEquals(model.prompts.size(), 2, "one retry");
        CardCollection hand = new CardCollection(me.getCardsIn(ZoneType.Hand));
        Card bottomed = ctrl.tuckCardsViaMulligan(hand, 1).getFirst();
        Assert.assertEquals(bottomed.getName(), "Counterspell");
    }

    @Test
    public void playOrDrawIsTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Script model = new Script("CHOICE: 1").on("Play first, or draw first", choice("Draw first"));
        Assert.assertEquals(seat(g, model, model).chooseStartingPlayer(true), g.getPlayers().get(1));
    }

    // ------------------------------------------------------------------ combat

    @Test
    public void attackersAndWhereTheyAttackAreTheModels() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        Card jace = card("Jace, the Mind Sculptor", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1")
                .on("declare attackers", picks("Grizzly Bears"))
                .on("Where does", choice("Jace"));
        Combat combat = new Combat(me);
        seat(g, model, model).declareAttackers(me, combat);
        Assert.assertEquals(combat.getAttackers().size(), 1);
        Assert.assertEquals(combat.getDefenderByAttacker(combat.getAttackers().get(0)), jace);
    }

    /** Banding: Benalish Hero and Grizzly Bears attack as one band when the seat says so (the seat used to declare
     *  every attacker alone, so banding was never used); two non-banding creatures are no band. */
    @Test
    public void aBandingAttackerFormsABand() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card hero = card("Benalish Hero", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        Card lions = card("Savannah Lions", me, ZoneType.Battlefield);
        String[] asked = new String[1];
        Script model = new Script("CHOICE: 1")
                .on("declare attackers", picks("Benalish Hero", "Grizzly Bears", "Savannah Lions"))
                .on("BANDS:", p -> { asked[0] = p; return picks("Grizzly Bears").apply(p); });
        Combat combat = new Combat(me);
        seat(g, model, model).declareAttackers(me, combat);
        Assert.assertNotNull(asked[0], "the band was the seat's to form");
        Assert.assertSame(combat.getBandOfAttacker(bears), combat.getBandOfAttacker(hero), "Bears joined the Hero's band");
        Assert.assertNotSame(combat.getBandOfAttacker(lions), combat.getBandOfAttacker(hero), "Lions attack alone");
        g.getPhaseHandler().setCombat(combat);
        String seen = new SeatObservation(g, rival).combat();
        Assert.assertTrue(seen.contains("Grizzly Bears") && seen.contains("in a band with"), "the defender sees the band: " + seen);
        // Joining both non-banding creatures is no band: asked again, then each attacks alone.
        g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        me = g.getPlayers().get(0);
        hero = card("Benalish Hero", me, ZoneType.Battlefield);
        bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Savannah Lions", me, ZoneType.Battlefield);
        model = new Script("CHOICE: 1").on("declare attackers", picks("Benalish Hero", "Grizzly Bears", "Savannah Lions"))
                .on("BANDS:", picks("Grizzly Bears", "Savannah Lions"));
        combat = new Combat(me);
        seat(g, model, model).declareAttackers(me, combat);
        Assert.assertNotSame(combat.getBandOfAttacker(bears), combat.getBandOfAttacker(hero), "an invalid band is not formed");
        Assert.assertEquals(model.prompts.stream().filter(q -> q.contains("BANDS:")).count(), 2L, "asked once more with the reason");
    }

    /** An attack the engine refuses (Silent Arbiter: one attacker at most) was carried out as picked, and the engine
     *  asked again, forever for a seat that kept the same answer: authored puzzles x_0, x_15 and x_18 (2026-10-20) spent
     *  hours in that loop. The seat is told why and asked once more; a second refused set takes the engine's legal set. */
    @Test
    public void anAttackTheEngineRefusesIsAskedAgainThenMadeLegal() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        Card giant = card("Hill Giant", me, ZoneType.Battlefield);
        card("Silent Arbiter", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1").on("declare attackers", p -> p.contains("not a legal attack")
                ? picks("Hill Giant").apply(p) : picks("Grizzly Bears", "Hill Giant").apply(p));
        Combat combat = new Combat(me);
        LlmSpikeGui.PlayerControllerLLM first = seat(g, model, model);
        Player seatPlayer = me;
        String log = LineFindingProbeTest.logOf(() -> first.declareAttackers(seatPlayer, combat));
        Assert.assertEquals(combat.getAttackers(), java.util.Collections.singletonList(giant), "the second answer is used");
        Assert.assertTrue(log.contains("refused by the engine: Grizzly Bears, Hill Giant"), "the refused answer is on the record: " + log);
        Assert.assertTrue(CombatUtil.validateAttackers(combat));
        String again = model.prompts.stream().filter(q -> q.contains("not a legal attack")).findFirst().orElse("");
        Assert.assertTrue(again.contains("at most 1"), "the seat is told the limit: " + again);
        // The same refused set twice: a legal declaration, not a third ask.
        g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        me = g.getPlayers().get(0);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Hill Giant", me, ZoneType.Battlefield);
        card("Silent Arbiter", g.getPlayers().get(1), ZoneType.Battlefield);
        model = new Script("CHOICE: 1").on("declare attackers", picks("Grizzly Bears", "Hill Giant"));
        Combat combat2 = new Combat(me);
        seat(g, model, model).declareAttackers(me, combat2);
        Assert.assertTrue(CombatUtil.validateAttackers(combat2), "a refused set twice ends legal: " + combat2.getAttackers());
        Assert.assertEquals(model.prompts.stream().filter(q -> q.contains("declare attackers")).count(), 2L, "asked twice");
    }

    /** Under the puzzle prover the refused first set was still noted as a solver choice, so the same final attack
     *  reached with and without a refused try read as two solutions (x_0, x_15, x_18 re-gated on epoch 66). A refused
     *  set never happened: its note is withdrawn. */
    @Test
    public void aRefusedAttackLeavesNoSolverChoiceBehind() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        Player me = g.getPlayers().get(0);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Hill Giant", me, ZoneType.Battlefield);
        card("Silent Arbiter", g.getPlayers().get(1), ZoneType.Battlefield);
        // include draws, one per candidate: both (refused), then the Bears alone
        PuzzleArms.searchLine = new PuzzleArms.Line(java.util.Arrays.asList(new PuzzleArms.Step("Solver", 2, 1),
                new PuzzleArms.Step("Solver", 2, 1), new PuzzleArms.Step("Solver", 2, 1), new PuzzleArms.Step("Solver", 2, 0)), 5L);
        try {
            Script model = new Script("CHOICE: 1");
            LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(g, me,
                    new forge.ai.LobbyPlayerAi("Solver", null), "Solver", "", model, model, "search");
            me.dangerouslySetController(ctrl);
            Combat combat = new Combat(me);
            ctrl.declareAttackers(me, combat);
            Assert.assertEquals(combat.getAttackers(), java.util.Collections.singletonList(bears));
            Assert.assertFalse(PuzzleArms.searchLine.choices.toString().contains("Hill Giant"),
                    "the refused set is not a choice: " + PuzzleArms.searchLine.choices);
        } finally {
            PuzzleArms.searchLine = null;
        }
    }

    /** Exert and enlist (Forge's AI chose both until 2026-10-17): which attackers, the seat's. */
    @Test
    public void exertAndEnlistAreTheSeats() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        Player me = g.getPlayers().get(0);
        Card glory = card("Glorybringer", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1").on("EXERT:", picks("Glorybringer")).on("ENLIST:", picks("Grizzly Bears"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Assert.assertEquals(ctrl.exertAttackers(List.of(glory, bears)), List.of(glory), "exerted");
        Assert.assertEquals(ctrl.enlistAttackers(List.of(glory, bears)), List.of(bears), "enlisted");
    }

    /** Splice onto Arcane, through the cast: Glacial Ray spliced onto Glacial Ray deals 2 + 2, both targets the seat's
     *  (the copied play routine asked Forge's AI whether to go on after splicing). */
    @Test
    public void spliceOntoArcaneIsTheSeats() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        lands(me, "Mountain", "Mountain", "Mountain", "Mountain");
        Card cast = card("Glacial Ray", me, ZoneType.Hand);
        card("Glacial Ray", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Glacial Ray"))
                .on("SPLICE", p -> "PICKS: 1").on("Choose the TARGET", choiceWith("Rival"));
        playAndResolve(seat(g, model, model), g, "Glacial Ray with a splice");
        Assert.assertTrue(model.prompts.stream().anyMatch(q -> q.contains("SPLICE")), "the splice was the seat's");
        Assert.assertEquals(rival.getLife(), 16, "2 + 2 damage");
        Assert.assertEquals(me.getCardsIn(ZoneType.Hand).size(), 1, "the spliced card stays in hand");
        // With two Mountains the splice cannot be paid: not offered.
        g = bareGame(PhaseType.MAIN1);
        me = g.getPlayers().get(0);
        lands(me, "Mountain", "Mountain");
        card("Glacial Ray", me, ZoneType.Hand);
        card("Glacial Ray", me, ZoneType.Hand);
        model = new Script("CHOICE: 1").on("You have priority", choiceWith("Glacial Ray"))
                .on("SPLICE", p -> "PICKS: 1").on("Choose the TARGET", choiceWith("Rival"));
        playAndResolve(seat(g, model, model), g, "Glacial Ray, splice unaffordable");
        Assert.assertTrue(model.prompts.stream().noneMatch(q -> q.contains("SPLICE")), "an unpayable splice was offered");
    }

    /** A coin flip's call, and the result kept; shield counters divided. */
    @Test
    public void aFlipCallAndAShieldSplitAreTheSeats() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card clash = card("Mana Clash", me, ZoneType.Hand);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        Card lions = card("Savannah Lions", me, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1").on("call it", choice("Tails")).on("result you keep", choice("Lose the flip"))
                .on("SHIELD:", p -> "ALLOC: 0, 2");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Assert.assertFalse(ctrl.chooseFlipResult(clash.getFirstSpellAbility(), me, true), "called tails");
        Assert.assertFalse(ctrl.chooseFlipResult(clash.getFirstSpellAbility(), me, false), "kept the lost flip");
        Map<GameEntity, Integer> affected = new LinkedHashMap<>();
        affected.put(bears, 0);
        affected.put(lions, 0);
        Map<GameEntity, Integer> split = ctrl.divideShield(clash, affected, 2);
        Assert.assertEquals(split.get(lions), Integer.valueOf(2), "both shields on the Lions: " + split);
        Assert.assertNull(split.get(bears), "none on the Bears");
    }

    /** Dice: the roll ignored, the dice rerolled, the roll modified, the swap; a reroll cost the seat cannot pay is
     *  not paid, and the reroll is not asked again in that roll (the engine would ask forever). */
    @Test
    public void diceChoicesAreTheSeats() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card monitor = card("Grizzly Bears", me, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1").on("roll to ignore", choice("3")).on("dice to reroll", picks("3"))
                .on("raise or lower", choice("15")).on("exchange with power", choice("15"))
                .on("exchange the roll", choice("Toughness"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Assert.assertEquals(ctrl.chooseRollToIgnore(List.of(3, 15)), Integer.valueOf(3));
        Assert.assertEquals(ctrl.chooseDiceToReroll(List.of(3, 15)), List.of(3));
        Assert.assertEquals(ctrl.chooseRollToModify(List.of(3, 15)), Integer.valueOf(15));
        forge.game.ability.effects.RollDiceEffect.DieRollResult r3 = new forge.game.ability.effects.RollDiceEffect.DieRollResult(3, 3);
        forge.game.ability.effects.RollDiceEffect.DieRollResult r15 = new forge.game.ability.effects.RollDiceEffect.DieRollResult(15, 15);
        Assert.assertSame(ctrl.chooseRollToSwap(List.of(r3, r15)), r15);
        Assert.assertEquals(ctrl.chooseRollSwapValue(List.of("Power", "Toughness"), 15, 2, 2), "Toughness");
        // No mana: the {1} reroll cost is not paid, and the next reroll ask of this roll answers none.
        Assert.assertFalse(ctrl.payCostDuringRoll(new forge.game.cost.Cost("1", false), new forge.game.spellability.SpellAbility.EmptySa(monitor, me)));
        Assert.assertTrue(ctrl.chooseDiceToReroll(List.of(3, 15)).isEmpty(), "not asked again after an unpaid reroll");
        // With a Mountain the cost is paid.
        card("Mountain", me, ZoneType.Battlefield);
        Assert.assertTrue(ctrl.payCostDuringRoll(new forge.game.cost.Cost("1", false), new forge.game.spellability.SpellAbility.EmptySa(monitor, me)));
    }

    /** The Un-set and variant hooks Forge's AI decided (stickers, contraptions, attractions, starting hands, assist):
     *  each is the seat's. */
    @Test
    public void unSetAndVariantChoicesAreTheSeats() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        Card lions = card("Savannah Lions", me, ZoneType.Battlefield);
        forge.game.card.sticker.Sticker word = forge.game.card.sticker.Sticker.parse(bears, "1", Map.of("Kind", "Name", "Word", "Happy"));
        forge.game.card.sticker.Sticker pt = forge.game.card.sticker.Sticker.parse(bears, "2", Map.of("Kind", "PT", "Power", "4", "Toughness", "1"));
        Script model = new Script("CHOICE: 1")
                .on("choose the sticker", choice("4/1")).on("go in the name", choice("Grizzly Happy Bears"))
                .on("keeps its stickers", choiceWith("Savannah Lions")).on("CRANK:", picks("Savannah Lions"))
                .on("SPROCKET:", choice("3")).on("SECTOR:", choice("Blue")).on("PLANAR DIE", choice("Chaos")).on("opening hand you keep", choice("Island"))
                .on("ask a player", choice("Rival")).on("How much generic mana", choice("1"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Assert.assertSame(ctrl.chooseSticker(List.of(word, pt), bears, null, true), pt, "the P/T sticker");
        Assert.assertEquals(ctrl.chooseStickerNamePosition(word, bears), 1, "between the two words");
        Assert.assertSame(ctrl.chooseCardToKeepStickers(new CardCollection(List.of(bears, lions))), lions);
        Assert.assertEquals(ctrl.chooseContraptionsToCrank(List.of(bears, lions)), List.of(lions), "cranked");
        Assert.assertEquals(ctrl.chooseSprocket(bears, List.of(1, 2, 3)), 3);
        Assert.assertEquals(ctrl.chooseSector(bears, null, List.of("Red", "Blue", "Green")), "Blue");
        Assert.assertEquals(ctrl.choosePDRollToIgnore(List.of(forge.game.PlanarDice.Blank, forge.game.PlanarDice.Chaos)),
                forge.game.PlanarDice.Chaos, "the planar roll ignored");
        forge.game.zone.PlayerZone a = new forge.game.zone.PlayerZone(ZoneType.Hand, me), b = new forge.game.zone.PlayerZone(ZoneType.Hand, me);
        a.add(card("Swamp", me, ZoneType.Library));
        b.add(card("Island", me, ZoneType.Library));
        Assert.assertSame(ctrl.chooseStartingHand(List.of(a, b)), b, "the hand with the Island");
        Assert.assertNull(ctrl.choosePlayerToAssistPayment(new FCollection<>(List.of(rival)), bears.getFirstSpellAbility(), "", 3),
                "pricing asks nobody (an ask there builds a snapshot that prices again)");
        Assert.assertSame(ctrl.inRealPaymentForTest(() -> ctrl.choosePlayerToAssistPayment(new FCollection<>(List.of(rival)),
                bears.getFirstSpellAbility(), "", 3)), rival, "the real payment asks");
        // Helping pay an assist spell: one Mountain, so 0 or 1; the seat pays 1 and the cost drops by it.
        card("Mountain", me, ZoneType.Battlefield);
        forge.game.mana.ManaCostBeingPaid cost = new forge.game.mana.ManaCostBeingPaid(forge.card.mana.ManaCost.get(3));
        SpellAbility theirs = card("Grizzly Bears", rival, ZoneType.Hand).getFirstSpellAbility();
        theirs.setActivatingPlayer(rival);
        Assert.assertTrue(ctrl.helpPayForAssistSpell(cost, theirs, 3, 3));
        Assert.assertEquals(cost.getGenericManaAmount(), 2, "the seat paid {1} of the assist");
    }

    @Test
    public void blocksAreTheModels() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        Card lions = card("Savannah Lions", rival, ZoneType.Battlefield);
        Script model = new Script("BLOCKS: Grizzly Bears->Savannah Lions");
        Combat combat = new Combat(rival);
        combat.addAttacker(lions, me);
        seat(g, model, model).declareBlockers(me, combat);
        Assert.assertTrue(combat.getBlockers(lions).contains(bears));
    }

    /** Rules pilot RP_045 (2026-10-18): General Leo Cristophe's "return up to one target creature card" with one
     *  creature card in the graveyard took it without asking ("(only legal)"); no target is legal too. An "up to one"
     *  target with a single candidate is asked, with "No target" offered. */
    @Test
    public void anUpToOneTargetWithOneCandidateIsAskedWithNoTargetOffered() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card bears = card("Grizzly Bears", me, ZoneType.Graveyard);
        Card leo = card("General Leo Cristophe", me, ZoneType.Battlefield);
        SpellAbility trig = leo.getTriggers().iterator().next().ensureAbility();
        trig.setActivatingPlayer(me);
        List<String> asks = new ArrayList<>();
        Script model = new Script("CHOICE: 1").on("TARGET", p -> { asks.add(p); return "CHOICE: 2"; });
        Assert.assertTrue(seat(g, model, model).chooseTargetsFor(trig));
        Assert.assertEquals(asks.size(), 1, "the seat was asked");
        Assert.assertTrue(asks.get(0).contains("No target"), "no target is offered:\n" + asks.get(0));
        Assert.assertFalse(trig.getTargets().contains(bears), "the seat's 'no target' stands");
    }

    /** Rules pilot RP_026 (2026-10-18): Forge Anew ("you may pay {0} rather than pay the equip cost of the first equip
     *  ability you activate during each of your turns") with no mana: equip was legal and never on the menu, which
     *  expanded alternative costs for spells only. */
    @Test
    public void anAlternativeCostForAnActivatedAbilityIsOffered() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Forge Anew", me, ZoneType.Battlefield);
        card("Bonesplitter", me, ZoneType.Battlefield);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        g.getAction().checkStaticAbilities();
        Script model = new Script("CHOICE: 1");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        ctrl.chooseSpellAbilityToPlay();
        String ask = PromptFidelityTest.promptWith(model, "You have priority");
        Assert.assertTrue(ask.contains("Bonesplitter") && ask.contains("Equip"), "equip for {0} is on the menu:\n" + ask);
    }

    /** prove-v3 PS_WAR0a (2026-10-18): Forge's static AiCache is cleared only by Forge's AI, when it chooses a play
     *  (AiController.chooseSpellAbilityToPlay). A model seat never cleared it: answers cached against this game
     *  (isPreventCombatDamageThisTurn, keyed by the game) stayed for the whole game, and every finished game stayed
     *  reachable (the puzzle prover ran out of heap after ~550 rollouts). The seat clears it where Forge's AI does. */
    @Test
    public void theSeatClearsForgesAiCacheWhereForgesAiDoes() {
        Game g = bareGame(PhaseType.MAIN1);
        Object probe = new Object();
        Assert.assertEquals((int) forge.ai.AiCache.getCached("harness-probe", () -> 1, null, probe), 1);
        Assert.assertEquals((int) forge.ai.AiCache.getCached("harness-probe", () -> 2, null, probe), 1, "cached");
        Script model = new Script("CHOICE: 1");
        seat(g, model, model).chooseSpellAbilityToPlay();
        Assert.assertEquals((int) forge.ai.AiCache.getCached("harness-probe", () -> 3, null, probe), 3, "cleared by the seat");
        forge.ai.AiCache.clear();
    }

    /** Rules pilot RP_108 (2026-10-18): Deflecting Palm's "choose a source" offered Forge's section headers
     *  ("--PERMANENTS:--", "--SPELLS ON THE STACK:--", ChooseSourceEffect's divider cards) as options; the engine asks
     *  again when one is picked, so a model that picked one spent a call and was asked the same thing. Not options. */
    @Test
    public void choiceListDividersAreNotOptions() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card div = new Card(-1, g);
        div.setName("--PERMANENTS:--");
        Card bears = card("Grizzly Bears", rival, ZoneType.Battlefield);
        Card lions = card("Savannah Lions", rival, ZoneType.Battlefield);
        forge.util.collect.FCollection<Card> list = new forge.util.collect.FCollection<>(Arrays.asList(div, bears, lions));
        List<String> asks = new ArrayList<>();
        Script model = new Script("CHOICE: 1").on("source", p -> { asks.add(p); return "CHOICE: 1"; });
        Card pick = seat(g, model, model).chooseSingleEntityForEffect(list, null, null, "Choose a source", false, null, null);
        Assert.assertEquals(pick, bears, "the first real source");
        Assert.assertEquals(asks.size(), 1);
        Assert.assertFalse(asks.get(0).contains("--PERMANENTS"), "no divider on the menu:\n" + asks.get(0));
    }

    /** Rules pilot RP_175 (2026-10-19): Mishra's Factory's "{1}: becomes a 2/2" was never offered when the Factory's own
     *  mana was the only source; Forge AI's mana chooser skips an ability's own card ("this rarely seems like a good
     *  idea"). Tapping it to pay is legal (owner: a legal action is never hidden). */
    @Test
    public void aCardMayPayForItsOwnAbility() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mishra's Factory", me, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1");
        seat(g, model, model).chooseSpellAbilityToPlay();
        String ask = PromptFidelityTest.promptWith(model, "You have priority");
        Assert.assertTrue(ask.contains("ACTIVATE Mishra's Factory") && ask.contains("Assembly-Worker"),
                "the animate ability is on the menu:\n" + ask);
    }

    /** Rules pilot RP_197 (2026-10-19): Omen Machine casts Terminus "without paying its mana cost if able" while the
     *  opponent's Sphere of Resistance adds {1}. No player is made to activate a mana ability (CR 605.3a), so the seat
     *  may leave the {1} unpaid and the spell is not cast; Forge paid it with a Mountain unasked. */
    @Test
    public void aForcedFreeCastWithATaxAsksWhetherToPayIt() {
        Game g = bareGame(PhaseType.UPKEEP);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card mountain = card("Mountain", me, ZoneType.Battlefield);
        card("Sphere of Resistance", rival, ZoneType.Battlefield);
        Card terminus = card("Terminus", me, ZoneType.Exile);
        g.getAction().checkStaticAbilities();
        Script model = new Script("CHOICE: 2");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        SpellAbility free = terminus.getFirstSpellAbility().copyWithNoManaCost(me);
        free.getPayCosts().setMandatory(true);
        Assert.assertFalse(ctrl.playSaFromPlayEffect(free), "the seat declined the {1}: nothing is cast");
        Assert.assertFalse(mountain.isTapped(), "the Mountain stays untapped");
        Assert.assertTrue(terminus.isInZone(ZoneType.Exile), "Terminus stays in exile");
        Assert.assertEquals(model.prompts.size(), 1, "one ask: pay the tax or not");
        Assert.assertTrue(model.prompts.get(0).contains("{1}"), model.prompts.get(0));
    }

    /** Rules pilot RP_209 (2026-10-19): suspend's cast asked "Do you want to play Living End?" and, after YES, the
     *  play-from-effect ask offered "Do not cast it" again: one decision asked twice. An optional cast the engine has
     *  just confirmed for the card is not asked again. */
    @Test
    public void anOptionalCastAlreadyConfirmedIsNotAskedAgain() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card heist = card("Arcane Heist", me, ZoneType.Hand);
        Card spike = card("Divination", me, ZoneType.Exile);   // no target, no mode: nothing else to ask
        Script model = new Script("CHOICE: 1");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        SpellAbility play = heist.getFirstSpellAbility();
        play.setActivatingPlayer(me);
        Assert.assertTrue(ctrl.confirmAction(play, null, "Do you want to play Divination?", null, spike, null));
        SpellAbility free = spike.getFirstSpellAbility().copyWithNoManaCost(me);
        ctrl.playSaFromPlayEffect(free);
        Assert.assertEquals(model.prompts.size(), 1, "asked twice:\n" + String.join("\n----\n", model.prompts));
        Assert.assertEquals(ctrl.playsNotMade(), 0, "Divination was cast");
    }

    /** A cast the seat chose through an effect that then does not happen is counted, so a written line that chose it is
     *  void (pr-review of #276): Concentrate, mana value 4, cast free "if able" under the opponent's Gaddock Teeg. */
    @Test
    public void anEffectCastAStaticForbidsIsCountedAsNotMade() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Gaddock Teeg", rival, ZoneType.Battlefield);
        Card concentrate = card("Concentrate", me, ZoneType.Exile);
        g.getAction().checkStaticAbilities();
        Script model = new Script("CHOICE: 1");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        SpellAbility free = concentrate.getFirstSpellAbility().copyWithNoManaCost(me);
        free.getPayCosts().setMandatory(true);
        Assert.assertFalse(ctrl.playSaFromPlayEffect(free), "Gaddock Teeg forbids it");
        Assert.assertTrue(concentrate.isInZone(ZoneType.Exile), "Concentrate stays in exile");
        Assert.assertEquals(ctrl.playsNotMade(), 1);
    }

    /** An effect cast tests its cost with Forge AI's check, which obeys Gitaxian Probe's AI-only "never pay life" hint;
     *  the seat's cast runs with it hidden, so with no blue source Probe is cast for 2 life (pr-review of #277). */
    @Test
    public void anEffectCastOfAPhyrexianSpellIsPaidWithLife() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        Card probe = card("Gitaxian Probe", me, ZoneType.Exile);
        Script model = new Script("CHOICE: 1").on("An effect lets you", choice("Cast it"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        SpellAbility play = probe.getFirstSpellAbility();
        play.setActivatingPlayer(me);
        Assert.assertTrue(ctrl.playSaFromPlayEffect(play), "the Probe was refused by the AI's cost check");
        Assert.assertEquals(me.getLife(), 18);
        Assert.assertEquals(ctrl.playsNotMade(), 0);
    }

    /** A land play goes through the inherited play routine, which returns true whatever happened, so the count reads the
     *  game: a land played counts nothing, a second land drop the engine refuses counts one (pr-review of #276). */
    @Test
    public void aLandPlayIsCountedOnlyWhenTheEngineRefusesIt() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card forest = card("Forest", me, ZoneType.Hand);
        Card island = card("Island", me, ZoneType.Hand);
        boolean[] first = {true};
        Script model = new Script("CHOICE: 1").on("You have priority", p -> {
            String land = first[0] ? "PLAY LAND Forest" : "PLAY LAND Island";
            first[0] = false;
            return choiceWith(land).apply(p);
        });
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        List<SpellAbility> playForest = ctrl.chooseSpellAbilityToPlay(), playIsland = ctrl.chooseSpellAbilityToPlay();
        Assert.assertEquals(playForest.get(0).getHostCard(), forest, "fixture: the Forest first");
        Assert.assertEquals(playIsland.get(0).getHostCard(), island, "fixture: then the Island");
        ctrl.playChosenSpellAbility(playForest.get(0));
        Assert.assertTrue(forest.isInZone(ZoneType.Battlefield));
        Assert.assertEquals(ctrl.playsNotMade(), 0, "the Forest was played");
        ctrl.playChosenSpellAbility(playIsland.get(0));
        Assert.assertTrue(island.isInZone(ZoneType.Hand), "one land a turn");
        Assert.assertEquals(ctrl.playsNotMade(), 1, "the refused Island is counted");
    }

    /** runs/loop/g27 T18 (2026-10-19): after epoch 59 let a card pay for its own ability, Rishadan Port's "{1}, {T}: Tap
     *  target land" was offered with Port as the only source; Port cannot tap for mana and tap as the cost, so the cast
     *  failed ("Didn't find what to pay"). A card pays for its own ability only when the two do not both tap it. */
    @Test
    public void aCardThatMustTapForItsAbilityCannotAlsoTapForItsMana() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Rishadan Port", me, ZoneType.Battlefield);
        card("Island", rival, ZoneType.Battlefield);
        card("Forest", me, ZoneType.Hand);   // a land drop on the menu, so the seat is asked; it pays for nothing
        Script model = new Script("CHOICE: 1");
        seat(g, model, model).chooseSpellAbilityToPlay();
        String ask = PromptFidelityTest.promptWith(model, "You have priority");
        Assert.assertFalse(ask.contains("Tap target land"), "Port's tap ability cannot be paid:\n" + ask);
    }

    /** Rules pilot RP_185 (2026-10-19): with a block requirement, the random and search arms redrew up to 64 times and
     *  then kept an illegal block set (most draws break "all creatures able to block it do so"). A failed draw now takes
     *  a legal block set from the engine's own check. Ten creatures must all block Prized Unicorn. */
    @Test
    public void anArmUnderABlockRequirementDeclaresALegalBlock() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        List<Card> mine = new ArrayList<>();
        for (int i = 0; i < 10; i++) mine.add(card("Grizzly Bears", me, ZoneType.Battlefield));
        Card unicorn = card("Prized Unicorn", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1");
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(g, me,
                new forge.ai.LobbyPlayerAi("Seat", null), "Seat", "", model, model, "random");
        me.dangerouslySetController(ctrl);
        Combat combat = new Combat(rival);
        combat.addAttacker(unicorn, me);
        g.getAction().checkStaticAbilities();
        ctrl.declareBlockers(me, combat);
        Assert.assertNull(forge.game.combat.CombatUtil.validateBlocks(combat, me), "the block set is legal");
        Assert.assertEquals(combat.getBlockers(unicorn).size(), 10, "every Bear blocks the Unicorn");
    }

    /** Rules pilot RP_007 (2026-10-18): the engine checks a block's requirements and restrictions only for its own
     *  GUI (InputBlock calls CombatUtil.validateBlocks), so a menace attacker blocked by one creature stood. An
     *  illegal block set goes back to the seat with the engine's reason. */
    @Test
    public void aMenaceAttackerBlockedByOneCreatureIsSentBack() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        Card brute = card("Boggart Brute", rival, ZoneType.Battlefield);
        List<String> asks = new ArrayList<>();
        Script model = new Script("CHOICE: 1").on("declare blockers", p -> {
            asks.add(p);
            return asks.size() == 1 ? "BLOCKS: Grizzly Bears->Boggart Brute" : "BLOCKS: NONE"; });
        Combat combat = new Combat(rival);
        combat.addAttacker(brute, me);
        g.getAction().checkStaticAbilities();
        seat(g, model, model).declareBlockers(me, combat);
        Assert.assertTrue(combat.getBlockers(brute).isEmpty(), "one blocker on a menace creature does not stand");
        Assert.assertEquals(asks.size(), 2, "the seat was asked again");
        Assert.assertTrue(asks.get(1).contains("cannot be blocked with 1"), "with the engine's reason:\n" + asks.get(1));
    }

    /** Prized Unicorn: "All creatures able to block Prized Unicorn do so." A seat that blocks nothing is sent back. */
    @Test
    public void aBlockRequirementTheSeatIgnoredIsSentBack() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        Card unicorn = card("Prized Unicorn", rival, ZoneType.Battlefield);
        List<String> asks = new ArrayList<>();
        Script model = new Script("CHOICE: 1").on("declare blockers", p -> {
            asks.add(p);
            return asks.size() == 1 ? "BLOCKS: NONE" : "BLOCKS: Grizzly Bears->Prized Unicorn"; });
        Combat combat = new Combat(rival);
        combat.addAttacker(unicorn, me);
        g.getAction().checkStaticAbilities();
        seat(g, model, model).declareBlockers(me, combat);
        Assert.assertTrue(combat.getBlockers(unicorn).contains(bears), "the required block stands");
        Assert.assertTrue(asks.size() == 2 && asks.get(1).contains("must block"), "sent back with the reason: " + asks);
    }

    /** Issue #311 (harness-env grader, combat.block.must-block): a seat told the requirement that still declares no
     *  blocks kept "no blocks", which the requirement forbids, and the engine checks blocks only for its own GUI. After
     *  the re-ask the declaration stands only if it is legal; otherwise a fixed legal set is taken, never Forge's AI. */
    @Test
    public void aSeatThatKeepsIgnoringABlockRequirementStillDeclaresALegalBlock() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        Card giant = card("Hill Giant", me, ZoneType.Battlefield);
        Card unicorn = card("Prized Unicorn", rival, ZoneType.Battlefield);
        List<String> asks = new ArrayList<>();
        Script model = new Script("CHOICE: 1").on("declare blockers", p -> { asks.add(p); return "BLOCKS: NONE"; });
        Combat combat = new Combat(rival);
        combat.addAttacker(unicorn, me);
        g.getAction().checkStaticAbilities();
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        String log = LineFindingProbeTest.logOf(() -> ctrl.declareBlockers(me, combat));
        Assert.assertTrue(asks.size() >= 2 && asks.get(asks.size() - 1).contains("must"), "told the requirement first: " + asks);
        Assert.assertNull(forge.game.combat.CombatUtil.validateBlocks(combat, me), "the declared blocks are legal");
        Assert.assertTrue(combat.getBlockers(unicorn).containsAll(Arrays.asList(bears, giant)), "both block the Unicorn");
        // The set the harness took is its decision, so it has an auto row (text: illegal twice; tool: the answer lost).
        Assert.assertTrue(log.matches("(?s).*\\[AUTO-LEGAL\\] blockers\\((illegal-twice|unparsed)\\)->legal 2.*"), log);
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0, "Forge's AI is never asked");
    }

    /** pr-review of #311: past MAX_BLOCK_SETS combinations legalBlockSets gives up, and the fallback kept "no blocks"
     *  again. Thirteen Bears under Prized Unicorn are 2^13 = 8192 sets (a Bear that must block the Unicorn cannot block
     *  the Lions, CombatUtil.canBlock); the fallback still finds the legal one, every Bear on the Unicorn. */
    @Test
    public void aBlockRequirementPastTheSetLimitStillGetsALegalFallback() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        for (int i = 0; i < 13; i++) card("Grizzly Bears", me, ZoneType.Battlefield);
        Card lions = card("Savannah Lions", rival, ZoneType.Battlefield);
        Card unicorn = card("Prized Unicorn", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1").on("declare blockers", p -> "BLOCKS: NONE");
        Combat combat = new Combat(rival);
        combat.addAttacker(lions, me);
        combat.addAttacker(unicorn, me);
        g.getAction().checkStaticAbilities();
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        ctrl.declareBlockers(me, combat);
        Assert.assertNull(forge.game.combat.CombatUtil.validateBlocks(combat, me), "the declared blocks are legal");
        Assert.assertEquals(combat.getBlockers(unicorn).size(), 13, "every Bear blocks the Unicorn");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0, "Forge's AI is never asked");
    }

    /** Round-2 review of #314: Silent Arbiter's "No more than one creature can block each combat" is enforced only by
     *  CombatUtil.canBlock against the combat so far (CombatUtil.java:434); validateBlocks never counts blockers. A model
     *  that blocks the Unicorn with both creatures does not get both: the answer tool's check refuses the set, the text
     *  path drops the pair past the cap; one creature blocks, and that one-creature block is accepted although the
     *  engine's requirement check (which ignores the cap) refuses it. The fallback under a cap is reached by the two
     *  tests after this one. */
    @Test
    public void aBlockOverTheGlobalCapIsNotDeclared() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Hill Giant", me, ZoneType.Battlefield);
        card("Silent Arbiter", rival, ZoneType.Battlefield);
        Card unicorn = card("Prized Unicorn", rival, ZoneType.Battlefield);
        List<String> asks = new ArrayList<>();
        Script model = new Script("CHOICE: 1").on("declare blockers", p -> { asks.add(p);
            return asks.size() == 1 ? "BLOCKS: Grizzly Bears->Prized Unicorn, Hill Giant->Prized Unicorn" : "BLOCKS: NONE"; });
        Combat combat = new Combat(rival);
        combat.addAttacker(unicorn, me);
        g.getAction().checkStaticAbilities();
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        ctrl.declareBlockers(me, combat);
        Assert.assertEquals(combat.getBlockers(unicorn).size(), 1, "one creature blocks under Silent Arbiter");
        // Not CombatUtil.validateBlocks: its requirement test ignores the cap, so here it refuses every block set.
        Assert.assertNull(LlmSpikeGui.PlayerControllerLLM.blocksError(combat, me, new ArrayList<>()), "the block is legal");
        // The answer tool's check is blocksError, so the two-creature set is sent back with the cap; the text path
        // drops the pair past the cap (CombatUtil.canBlock against the combat so far).
        List<Card[]> both = new ArrayList<>();
        for (Card c : me.getCreaturesInPlay()) both.add(new Card[] {c, unicorn});
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.blocksError(combat, me, both),
                "no more than 1 creature can block each combat.");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0, "Forge's AI is never asked");
    }

    /** Round-3 review of #314 (CR 509.1c): under Silent Arbiter, Hill Giant must block Grizzly Bears (a creature-side
     *  requirement) and Llanowar Elves has none. Elves on Savannah Lions obeys no requirement where Giant on the Bears
     *  obeys one, so it is refused, though validateBlocks' first objection is only about the Giant not blocking. The
     *  seat answers it, then no blocks; the fallback takes Giant on the Bears. Silent Arbiter also says "No more than
     *  one creature can attack each combat"; the fixture puts two attackers in combat with combat.addAttacker, which
     *  never checks attack restrictions, on purpose: only the block cap is under test, and a block-only cap (Mirri,
     *  Weatherlight Duelist) exists only as the effect her attack trigger creates. */
    @Test
    public void atTheCapABlockThatObeysFewerRequirementsIsRefused() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card giant = card("Hill Giant", me, ZoneType.Battlefield);
        Card elves = card("Llanowar Elves", me, ZoneType.Battlefield);
        card("Silent Arbiter", rival, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", rival, ZoneType.Battlefield);
        Card lions = card("Savannah Lions", rival, ZoneType.Battlefield);
        giant.addMustBlockCard(g.getNextTimestamp(), bears);
        List<String> asks = new ArrayList<>();
        Script model = new Script("CHOICE: 1").on("declare blockers", p -> { asks.add(p);
            return asks.size() == 1 ? "BLOCKS: Llanowar Elves->Savannah Lions" : "BLOCKS: NONE"; });
        Combat combat = new Combat(rival);
        combat.addAttacker(bears, me);
        combat.addAttacker(lions, me);
        g.getAction().checkStaticAbilities();
        List<Card[]> swap = new ArrayList<>();
        swap.add(new Card[] {elves, lions});
        Assert.assertNotNull(LlmSpikeGui.PlayerControllerLLM.blocksError(combat, me, swap), "Elves on the Lions is refused");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        String log = LineFindingProbeTest.logOf(() -> ctrl.declareBlockers(me, combat));
        Assert.assertEquals(combat.getBlockers(bears), Arrays.asList(giant), "the Giant blocks the Bears");
        Assert.assertTrue(combat.getBlockers(lions).isEmpty(), "nothing blocks the Lions");
        Assert.assertTrue(log.contains("->legal 1"), "the fallback took it: " + log);
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0, "Forge's AI is never asked");
    }

    /** Round-3 review of #314: under Silent Arbiter with Prized Unicorn attacking, Mogg Flunkies ("can't attack or block
     *  alone") blocking alone is illegal, but validateBlocks' first objection is the Bears' requirement (its requirement
     *  loop runs before its restriction loop), which hid the Flunkies'. The fallback takes the Bears. */
    @Test
    public void atTheCapAHiddenBlockRestrictionIsStillSeen() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card flunkies = card("Mogg Flunkies", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Silent Arbiter", rival, ZoneType.Battlefield);
        Card unicorn = card("Prized Unicorn", rival, ZoneType.Battlefield);
        List<String> asks = new ArrayList<>();
        Script model = new Script("CHOICE: 1").on("declare blockers", p -> { asks.add(p);
            return asks.size() == 1 ? "BLOCKS: Mogg Flunkies->Prized Unicorn" : "BLOCKS: NONE"; });
        Combat combat = new Combat(rival);
        combat.addAttacker(unicorn, me);
        g.getAction().checkStaticAbilities();
        List<Card[]> alone = new ArrayList<>();
        alone.add(new Card[] {flunkies, unicorn});
        String err = LlmSpikeGui.PlayerControllerLLM.blocksError(combat, me, alone);
        Assert.assertTrue(err != null && err.contains("can't block alone"), "the Flunkies alone is refused: " + err);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        String log = LineFindingProbeTest.logOf(() -> ctrl.declareBlockers(me, combat));
        Assert.assertEquals(combat.getBlockers(unicorn), Arrays.asList(bears), "the Bears block the Unicorn");
        Assert.assertTrue(log.contains("->legal 1"), "the fallback took it: " + log);
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0, "Forge's AI is never asked");
    }

    /** Round-2 review of #314: the increasing-size search. Thirteen Bears under Prized Unicorn are 8192 sets, past
     *  MAX_BLOCK_SETS, and Silent Arbiter makes both whole-board sets (every Bear on the Unicorn) illegal; the search
     *  finds the one-Bear block. */
    @Test
    public void pastTheSetLimitTheSearchFindsTheSmallLegalBlock() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        for (int i = 0; i < 13; i++) card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Silent Arbiter", rival, ZoneType.Battlefield);
        Card unicorn = card("Prized Unicorn", rival, ZoneType.Battlefield);
        Combat combat = new Combat(rival);
        combat.addAttacker(unicorn, me);
        g.getAction().checkStaticAbilities();
        List<Card[]> set = LlmSpikeGui.PlayerControllerLLM.legalBlockSet(combat, me);
        Assert.assertNotNull(set, "a legal set is found");
        Assert.assertEquals(set.size(), 1, "the one-Bear block");
        Assert.assertNull(LlmSpikeGui.PlayerControllerLLM.blocksError(combat, me, set), "and it is legal");
    }

    /** restrictionError is a copy of the restriction half of CombatUtil.validateBlocks (follow-up to #314). With no
     *  block requirement in play validateBlocks' requirement half finds nothing, so the two must agree on every set:
     *  legal or not, and word for word where both object. A drift in either (an engine bump, an edit to the copy) fails
     *  here instead of in a game at the block cap. */
    @Test
    public void theBlockRestrictionCopyAgreesWithTheEngine() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card flunkies = card("Mogg Flunkies", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        Card giant = card("Hill Giant", me, ZoneType.Battlefield);
        Card lions = card("Savannah Lions", rival, ZoneType.Battlefield);
        Card brute = card("Boggart Brute", rival, ZoneType.Battlefield);
        Combat combat = new Combat(rival);
        combat.addAttacker(lions, me);
        combat.addAttacker(brute, me);
        g.getAction().checkStaticAbilities();
        Card[][][] sets = {
            {},
            {{flunkies, lions}},
            {{flunkies, lions}, {bears, lions}},
            {{flunkies, lions}, {bears, brute}},
            {{bears, brute}},
            {{bears, brute}, {giant, brute}},
            {{flunkies, brute}, {bears, brute}},
            {{bears, lions}, {giant, brute}},
        };
        int objected = 0;
        for (Card[][] set : sets) {
            for (Card[] pr : set) combat.addBlocker(pr[1], pr[0]);
            try {
                CardCollection blocking = new CardCollection(combat.getAllBlockers());
                String copy = LlmSpikeGui.PlayerControllerLLM.restrictionError(combat, blocking);
                String engine = CombatUtil.validateBlocks(combat, me);
                Assert.assertEquals(copy, engine, "set " + Arrays.deepToString(set));
                if (engine != null) objected++;
            } finally {
                for (Card[] pr : set) combat.removeBlockAssignment(pr[1], pr[0]);
            }
        }
        Assert.assertEquals(objected, 4, "both answers are covered: Flunkies alone once, menace by one three times");
    }

    /** pr-review of #311: the no-model branch (the floor seat, or a spent budget) declared no blocks under a block
     *  requirement too. It takes the same fixed legal set, as an auto row. */
    @Test
    public void aSeatWithNoModelBlocksLegallyUnderABlockRequirement() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        Card unicorn = card("Prized Unicorn", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1");
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(g, me,
                new forge.ai.LobbyPlayerAi("Seat", null), "Seat", "", model, model, "floor");
        me.dangerouslySetController(ctrl);
        Combat combat = new Combat(rival);
        combat.addAttacker(unicorn, me);
        g.getAction().checkStaticAbilities();
        String log = LineFindingProbeTest.logOf(() -> ctrl.declareBlockers(me, combat));
        Assert.assertNull(forge.game.combat.CombatUtil.validateBlocks(combat, me), "the declared blocks are legal");
        Assert.assertTrue(combat.getBlockers(unicorn).contains(bears), "the Bears block the Unicorn");
        Assert.assertTrue(log.contains("[AUTO-LEGAL] blockers(no-model)->legal 1"), log);
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0, "Forge's AI is never asked");
    }

    /** A creature that can block any number of creatures may block both attackers (each blocker blocked once,
     *  whatever the card said): the label says so, and both blocks stand. */
    @Test
    public void aBlockerThatCanBlockMoreBlocksMore() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card guard = card("Palace Guard", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", rival, ZoneType.Battlefield);
        Card lions = card("Savannah Lions", rival, ZoneType.Battlefield);
        String[] asked = new String[1];
        Script model = new Script("CHOICE: 1").on("declare blockers", p -> { asked[0] = p;
            return "BLOCKS: Palace Guard->Grizzly Bears, Palace Guard->Savannah Lions"; });
        Combat combat = new Combat(rival);
        combat.addAttacker(bears, me);
        combat.addAttacker(lions, me);
        g.getAction().checkStaticAbilities();
        seat(g, model, model).declareBlockers(me, combat);
        Assert.assertTrue(asked[0] != null && asked[0].contains("can block any number of attackers"), "the label says so: " + asked[0]);
        Assert.assertTrue(combat.getBlockers(bears).contains(guard), "the Guard blocks the Bears");
        Assert.assertTrue(combat.getBlockers(lions).contains(guard), "and the Lions");
    }

    /** A trampler's split over one blocker, and the order of damage over two. */
    @Test
    public void combatDamageIsAssignedTheModelsWay() {
        Game g = bareGame(PhaseType.COMBAT_DAMAGE);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card wurm = card("Colossal Dreadmaw", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", rival, ZoneType.Battlefield);
        Card lions = card("Savannah Lions", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1")
                .on("has trample), single blocker", choice("ALL TO BLOCKER"))
                .on("is blocked by 2 creatures", picks("Savannah Lions", "Grizzly Bears"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Map<Card, Integer> one = ctrl.assignCombatDamage(wurm, new CardCollection(bears), new CardCollection(), 6, rival, false);
        Assert.assertEquals(one.get(bears), Integer.valueOf(6), "all to the blocker: " + one);
        Map<Card, Integer> two = ctrl.assignCombatDamage(wurm, new CardCollection(Arrays.asList(bears, lions)), new CardCollection(), 6, rival, false);
        Assert.assertEquals(two.get(lions), Integer.valueOf(1), "lethal to the Lions first: " + two);
    }

    /** Since the 2024 rules (Forge's default here: GameRules.orderCombatants false), an attacker divides its combat damage
     *  among its blockers as its controller chooses. The seat was asked only an order, with damage cascading lethal-first,
     *  so a split like "all 6 on the second blocker" was never on offer (a legal action hidden; found writing rules
     *  puzzles, batch 5). The amounts are the seat's now. */
    @Test
    public void freelyDividedCombatDamageGoesWhereTheSeatSays() {
        Game g = bareGame(PhaseType.COMBAT_DAMAGE);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card wurm = card("Craw Wurm", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", rival, ZoneType.Battlefield), elves = card("Llanowar Elves", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1").on("COMBAT DAMAGE ASSIGNMENT", p -> "ALLOC: 0, 6");
        Map<Card, Integer> r = seat(g, model, model).assignCombatDamage(wurm, new CardCollection(Arrays.asList(bears, elves)), null, 6, rival, true);
        Assert.assertEquals(r.get(elves), Integer.valueOf(6), "all six on the Elves: " + r);
        Assert.assertEquals(r.getOrDefault(bears, 0), Integer.valueOf(0));
    }

    /** A trampler over one blocker may give it more than lethal and trample the rest: any split with lethal on the
     *  blocker is legal, not only "max trample" or "all to the blocker". A split that tramples over before the blocker
     *  has lethal goes back to the seat with the reason. */
    @Test
    public void aTramplersSplitIsTheSeatsWithinTheRule() {
        Game g = bareGame(PhaseType.COMBAT_DAMAGE);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card dreadmaw = card("Colossal Dreadmaw", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", rival, ZoneType.Battlefield);
        List<String> asks = new ArrayList<>();
        Script model = new Script("CHOICE: 1").on("COMBAT DAMAGE ASSIGNMENT", p -> { asks.add(p);
            return asks.size() == 1 ? "ALLOC: 1, 5" : "ALLOC: 3, 3"; });
        Map<Card, Integer> r = seat(g, model, model).assignCombatDamage(dreadmaw, new CardCollection(bears), null, 6, rival, true);
        Assert.assertEquals(asks.size(), 2, "the split that skipped lethal was sent back");
        Assert.assertTrue(asks.get(1).contains("lethal"), "with the reason:\n" + asks.get(1));
        Assert.assertEquals(r.get(bears), Integer.valueOf(3), "three on the Bears: " + r);
        Assert.assertEquals(r.get(null), Integer.valueOf(3), "three trample over: " + r);
    }

    /** Rules puzzle batch 5 (RP_156-RP_160): "lethal damage" for assignment is the rule's (CR 702.19b: toughness minus
     *  damage marked, 1 with deathtouch), not Forge AI's estimate of what kills (ComputerUtilCombat.getEnoughDamageToKill
     *  counts indestructible, protection, shields and doublers). An indestructible blocker takes lethal at 1. */
    @Test
    public void lethalDamageForAssignmentIsTheRulesNotWhatKills() {
        Game g = bareGame(PhaseType.COMBAT_DAMAGE);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card dreadmaw = card("Colossal Dreadmaw", me, ZoneType.Battlefield);
        Card myr = card("Darksteel Myr", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1").on("COMBAT DAMAGE ASSIGNMENT", p -> "ALLOC: 1, 5");
        Map<Card, Integer> r = seat(g, model, model).assignCombatDamage(dreadmaw, new CardCollection(myr), null, 6, rival, true);
        Assert.assertTrue(model.asked("COMBAT DAMAGE ASSIGNMENT"), "the split was asked, not forced");
        Assert.assertEquals(r.get(myr), Integer.valueOf(1), "lethal to an indestructible 0/1 is 1: " + r);
        Assert.assertEquals(r.get(null), Integer.valueOf(5), "the rest tramples over: " + r);
    }

    /** The order an attacker assigns its damage among its blockers is the seat's (it was Forge's
     *  AiBlockController's, only logged). With no answer the order stays as given: never Forge's AI. */
    @Test
    public void damageOrderIsTheModels() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card wurm = card("Colossal Dreadmaw", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", rival, ZoneType.Battlefield);
        Card lions = card("Savannah Lions", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1").on("COMBAT DAMAGE ORDER", picks("Grizzly Bears", "Savannah Lions"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        CardCollection order = ctrl.orderBlockers(wurm, new CardCollection(Arrays.asList(lions, bears)));
        Assert.assertTrue(model.asked("COMBAT DAMAGE ORDER"), "the damage order was not asked");
        Assert.assertEquals(order.get(0), bears, "the model's order: " + order);
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** What no answer used to hand to Forge's AI now takes a fixed legal choice: card naming with nothing
     *  to name, a cost pick that came back short. */
    @Test
    public void noAnswerIsNeverForgesAi() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card src = card("Pithing Needle", me, ZoneType.Battlefield);
        Script model = new Script("PICKS: NONE");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Assert.assertEquals(ctrl.chooseCardName(spell(src, me), new ArrayList<>(), "Choose a card name."), "");
        Assert.assertTrue(ctrl.chooseModeForAbility(spell(src, me), new ArrayList<>(), 1, 1, false).isEmpty());
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0, "a no-answer path reached Forge's AI");
    }

    // ------------------------------------------------------------------ choices an effect hands the seat

    @Test
    public void aVoteIsTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card cj = card("Council's Judgment", me, ZoneType.Hand);
        Card a = card("Savannah Lions", rival, ZoneType.Battlefield);
        Card b = card("Grizzly Bears", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1").on("VOTE", choice("Grizzly Bears"));
        Object v = seat(g, model, model).vote(spell(cj, me), "Vote for a nonland permanent", new ArrayList<>(Arrays.asList(a, b)),
                com.google.common.collect.ArrayListMultimap.create(), me, false);
        Assert.assertEquals(v, b);
        source(cj);
    }

    @Test
    public void anEntityPickIsTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card src = card("Oblivion Ring", me, ZoneType.Battlefield);
        Card a = card("Savannah Lions", rival, ZoneType.Battlefield);
        Card b = card("Grizzly Bears", rival, ZoneType.Battlefield);
        FCollection<GameEntity> opts = new FCollection<>();
        opts.add(a); opts.add(b);
        Script model = new Script("CHOICE: 1").on("Choose a creature", choice("Grizzly Bears"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        GameEntity got = ctrl.chooseSingleEntityForEffect(opts, null, spell(src, me), "Choose a creature", false, null, null);
        Assert.assertEquals(got, b);
        // The engine names the seat by its label (Thassa's Oracle: "put on top of Seat library", runs/loop/g08).
        ctrl.chooseSingleEntityForEffect(opts, null, spell(src, me), "Choose card(s) to put on top of " + me.getName() + " library", false, null, null);
        String last = model.prompts.get(model.prompts.size() - 1);
        Assert.assertTrue(last.contains("put on top of your library"), last.substring(last.lastIndexOf("DECISION")));
        source(src);
    }

    @Test
    public void aTutorsPickIsTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card tutor = card("Demonic Tutor", me, ZoneType.Hand);
        Card a = card("Lightning Bolt", me, ZoneType.Library);
        Card b = card("Counterspell", me, ZoneType.Library);
        Script model = new Script("CHOICE: 1").on("Select a card", choice("Counterspell"));
        Card got = seat(g, model, model).chooseSingleCardForZoneChange(ZoneType.Hand, Arrays.asList(ZoneType.Library), spell(tutor, me),
                new CardCollection(Arrays.asList(a, b)), null, "Select a card", false, me);
        Assert.assertEquals(got, b);
        source(tutor);
    }

    /** "Divided as you choose among one or two targets": which targets, then how the damage splits. */
    @Test
    public void dividedTargetsAndTheirSplitAreTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card fb = card("Forked Bolt", me, ZoneType.Hand);
        Card lions = card("Savannah Lions", rival, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1")
                .on("Choose TARGETS", picks("Savannah Lions", "Grizzly Bears"))
                .on("Split 2", p -> "ALLOC: 1, 1");
        SpellAbility sa = spell(fb, me);
        Assert.assertTrue(seat(g, model, model).chooseTargetsFor(sa));
        Assert.assertTrue(sa.getTargets().contains(lions) && sa.getTargets().contains(bears), sa.getTargets().toString());
        Assert.assertEquals(sa.getDividedValue(lions), Integer.valueOf(1));
        Assert.assertTrue(model.asked("Split 2"), PromptFidelityTest.decisions(model));
    }

    /** Each target of a divided spell gets at least one (CR 601.2d): the split [2 0] was accepted, so "Fire at the
     *  opponent and Iona" dealt all 2 to the opponent (rules pilot batch 12, rp10/V_iona-search). */
    @Test
    public void aDividedSplitGivesEachTargetAtLeastOne() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card fb = card("Forked Bolt", me, ZoneType.Hand);
        Card lions = card("Savannah Lions", rival, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1")
                .on("Choose TARGETS", picks("Savannah Lions", "Grizzly Bears"))
                .on("Split 2", p -> "ALLOC: 2, 0");
        SpellAbility sa = spell(fb, me);
        Assert.assertTrue(seat(g, model, model).chooseTargetsFor(sa));
        Assert.assertEquals(sa.getDividedValue(lions), Integer.valueOf(1), "a target was given 0");
        Assert.assertEquals(sa.getDividedValue(bears), Integer.valueOf(1), "a target was given 0");
        Assert.assertTrue(model.asked("at least 1 each"), "the split question does not state the rule");
    }

    /** More targets than the divided total can never each get one: Rolling Thunder for X=2 among three picks fell back
     *  to [1 1 0]. A divided spell takes at most as many targets as its total (review of epoch 64). */
    @Test
    public void aDividedSpellTakesNoMoreTargetsThanItsTotal() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card rt = card("Rolling Thunder", me, ZoneType.Hand);
        card("Savannah Lions", rival, ZoneType.Battlefield);
        card("Grizzly Bears", rival, ZoneType.Battlefield);
        card("Hill Giant", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1")
                .on("Choose TARGETS", picks("Savannah Lions", "Grizzly Bears", "Hill Giant"))
                .on("Split 2", p -> "ALLOC: 1, 1");
        SpellAbility sa = spell(rt, me);
        sa.setXManaCostPaid(2);
        Assert.assertTrue(seat(g, model, model).chooseTargetsFor(sa));
        Assert.assertTrue(sa.getTargets().size() <= 2, "three targets for 2 damage: " + sa.getTargets());
        for (forge.game.GameObject t : sa.getTargets())
            Assert.assertTrue(sa.getDividedValue(t) != null && sa.getDividedValue(t) >= 1, "a target got 0: " + t);
    }

    /** Thoughtseize-style: which card the opponent discards is the model's. */
    @Test
    public void whatTheOpponentDiscardsIsTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card ts = card("Thoughtseize", me, ZoneType.Hand);
        Card a = card("Lightning Bolt", rival, ZoneType.Hand);
        Card b = card("Counterspell", rival, ZoneType.Hand);
        Script model = new Script("PICKS: 1").on("must DISCARD", picks("Counterspell"));
        CardCollection hand = new CardCollection(Arrays.asList(a, b));
        CardCollection got = seat(g, model, model).chooseCardsToDiscardFrom(rival, spell(ts, me), hand, 1, 1, hand);
        Assert.assertEquals(got.getFirst(), b);
        source(ts);
    }

    /** The seat's own discard (it coached "a huge creature you can later reanimate" to every deck). */
    @Test
    public void whatTheSeatDiscardsIsTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card looting = card("Faithless Looting", me, ZoneType.Hand);
        Card a = card("Lightning Bolt", me, ZoneType.Hand);
        Card b = card("Counterspell", me, ZoneType.Hand);
        Card c = card("Ponder", me, ZoneType.Hand);
        Script model = new Script("PICKS: 1").on("must DISCARD", picks("Ponder", "Lightning Bolt"));
        CardCollection hand = new CardCollection(Arrays.asList(a, b, c));
        List<Card> got = new ArrayList<>(seat(g, model, model).chooseCardsToDiscardFrom(me, spell(looting, me), hand, 2, 2, hand));
        Assert.assertEquals(got, Arrays.asList(c, a));
        source(looting);
    }

    @Test
    public void theCleanupDiscardIsTheModels() {
        Game g = bareGame(PhaseType.CLEANUP);
        Player me = g.getPlayers().get(0);
        card("Lightning Bolt", me, ZoneType.Hand);
        Card cs = card("Counterspell", me, ZoneType.Hand);
        card("Ponder", me, ZoneType.Hand);
        Script model = new Script("PICKS: 1").on("Cleanup step", picks("Counterspell"));
        Assert.assertEquals(seat(g, model, model).chooseCardsToDiscardToMaximumHandSize(1).getFirst(), cs);
    }

    @Test
    public void whatDelveExilesIsTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Lightning Bolt", me, ZoneType.Graveyard);
        Card cs = card("Counterspell", me, ZoneType.Graveyard);
        Script model = new Script("PICKS: 1").on("DELVE", picks("Counterspell"));
        CardCollection grave = new CardCollection(me.getCardsIn(ZoneType.Graveyard));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Assert.assertEquals(ctrl.inRealPaymentForTest(() -> ctrl.chooseCardsToDelve(1, grave)).getFirst(), cs);
    }

    /** runs/luna-v8/g2 crashed on turn 6 with a StackOverflowError: six cards in the graveyard, more than Murktide
     *  Regent's generic 5, so an affordability test asked the seat, and the ask's own snapshot tested Murktide's
     *  affordability again, which asked again. A test now asks nothing, and the real ask cannot re-enter. */
    @Test
    public void delveWithMurktideInHandDoesNotRecurse() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Murktide Regent", me, ZoneType.Hand);
        for (int i = 0; i < 2; i++) card("Island", me, ZoneType.Battlefield);
        for (String n : new String[] {"Flooded Strand", "Lightning Bolt", "Ponder", "Brainstorm", "Lightning Bolt", "Ponder"})
            card(n, me, ZoneType.Graveyard);
        Script model = new Script("PICKS: 1,2,3,4,5");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        CardCollection grave = new CardCollection(me.getCardsIn(ZoneType.Graveyard));
        Assert.assertEquals(ctrl.chooseCardsToDelve(5, grave).size(), 5);
        Assert.assertTrue(model.prompts.isEmpty(), "an affordability test asks nothing");
        Assert.assertEquals(ctrl.inRealPaymentForTest(() -> ctrl.chooseCardsToDelve(5, grave)).size(), 5);
        Assert.assertEquals(model.prompts.size(), 1, "the real payment asks once");
    }

    @Test
    public void aGameWhoseThreadCrashedHasNoWinner() {
        Assert.assertEquals(LlmSpikeGui.finalWinner("A", false, null), "A");
        Assert.assertEquals(LlmSpikeGui.finalWinner("draw", false, "java.lang.StackOverflowError"), "invalid");
        Assert.assertEquals(LlmSpikeGui.finalWinner("B", true, null), "invalid");
    }

    /** runs/luna-v8/g2: every delve exiled the whole graveyard unasked ("delve(all)"), and a larger graveyard
     *  forced exactly the generic amount. Delve exiles any number up to it, so fewer is the seat's choice. */
    @Test
    public void delveMayExileFewerAndAnAffordabilityTestAsksNothing() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        for (int i = 0; i < 3; i++) card("Island", me, ZoneType.Battlefield);
        Card bolt = card("Lightning Bolt", me, ZoneType.Graveyard);
        card("Ponder", me, ZoneType.Graveyard);
        card("Flooded Strand", me, ZoneType.Graveyard);
        Script model = new Script("PICKS: NONE").on("DELVE", picks("Lightning Bolt"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        CardCollection grave = new CardCollection(me.getCardsIn(ZoneType.Graveyard));
        Assert.assertEquals(ctrl.chooseCardsToDelve(3, grave).size(), 3, "a test counts every card that could pay");
        Assert.assertTrue(model.prompts.isEmpty(), "and asks nothing");
        forge.game.card.CardCollectionView out = ctrl.inRealPaymentForTest(() -> ctrl.chooseCardsToDelve(3, grave));
        Assert.assertEquals(out.size(), 1, "three Islands pay the rest: the seat exiled only " + out);
        Assert.assertTrue(out.contains(bolt));
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** Delve's least is what the mana cannot pay, by the engine's test payment: Become Immense ({5}{G}) with two Forests
     *  must exile four, and was offered none, by an estimate of the mana available (random gauntlet, kw05). */
    @Test
    public void delveMustExileWhatTheManaCannotPay() {
        // runs/gauntlet e44-random-s4 kw05: {U}{G} floating, Plains and two Mountains untapped, two cards in the
        // graveyard. Become Immense ({5}{G}) has five mana, so delve must exile one; the estimate said none.
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card island = card("Island", me, ZoneType.Battlefield), forest = card("Forest", me, ZoneType.Battlefield);
        me.getManaPool().addManaNoEvent(new forge.game.mana.Mana(forge.card.MagicColor.BLUE, island, null, me));
        me.getManaPool().addManaNoEvent(new forge.game.mana.Mana(forge.card.MagicColor.GREEN, forest, null, me));
        island.setTapped(true);
        forest.setTapped(true);
        lands(me, "Plains", "Mountain", "Mountain");
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Aetherflame Wall", me, ZoneType.Graveyard);
        card("Death-Greeter's Champion", me, ZoneType.Graveyard);
        card("Become Immense", me, ZoneType.Hand);
        String[] delve = new String[1];
        Script model = new Script("CHOICE: 1").on("You have priority", choiceWith("Become Immense"))
                .on("DELVE", p -> { delve[0] = p; return "PICKS: 1"; });
        playAndResolve(seat(g, model, model), g, "Become Immense with two floating mana and three lands");
        Assert.assertNotNull(delve[0], "delve asked");
        Assert.assertTrue(delve[0].contains("at least 1"), "the least delve is stated: " + delve[0].substring(Math.max(0, delve[0].indexOf("DELVE"))));
        Assert.assertEquals(bears.getNetPower(), 8, "the cast was paid and resolved");
    }

    /** An edict (not a cost): which permanent is sacrificed. */
    @Test
    public void anEdictsSacrificeIsTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card edict = card("Diabolic Edict", rival, ZoneType.Hand);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        Card lions = card("Savannah Lions", me, ZoneType.Battlefield);
        Script model = new Script("PICKS: 1").on("SACRIFICE", picks("Savannah Lions"));
        SpellAbility sa = edict.getFirstSpellAbility();
        sa.setActivatingPlayer(rival);
        Assert.assertEquals(seat(g, model, model).choosePermanentsToSacrifice(sa, 1, 1, new CardCollection(Arrays.asList(bears, lions)), "Sacrifice a creature").getFirst(), lions);
        source(edict);
    }

    @Test
    public void cardsChosenForAnEffectAreTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card src = card("Intuition", me, ZoneType.Hand);
        Card a = card("Lightning Bolt", me, ZoneType.Library);
        Card b = card("Counterspell", me, ZoneType.Library);
        Card c = card("Ponder", me, ZoneType.Library);
        Script model = new Script("PICKS: 1").on("Choose two cards", picks("Ponder", "Counterspell"));
        List<Card> got = new ArrayList<>(seat(g, model, model).chooseCardsForEffect(new CardCollection(Arrays.asList(a, b, c)), spell(src, me),
                "Choose two cards", 2, 2, false, null));
        Assert.assertEquals(got, Arrays.asList(c, b));
        source(src);
    }

    /** "Counter target spell unless its controller pays {1}": the model pays, and it is paid. The
     *  question used to quote the stack description with engine card ids ("Lightning Bolt (12)"). */
    @Test
    public void payingForAnUnlessCostIsTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card mountain = card("Mountain", me, ZoneType.Battlefield);
        Card bolt = card("Lightning Bolt", me, ZoneType.Hand);
        LineFindingProbeTest.rivalCasts(g, "Force Spike", bolt);
        SpellAbility spike = g.getStack().peekAbility();
        Script model = new Script("CHOICE: 1").on("to prevent this", choice("Pay"));
        Assert.assertTrue(seat(g, model, model).payCostToPreventEffect(new forge.game.cost.Cost("1", false), spike, false, null));
        Assert.assertTrue(mountain.isTapped(), "the Mountain paid");
        sourceName = "Force Spike"; sourceText = spike.getHostCard().getOracleText();
    }

    /** With nothing to pay with, "Pay {2}" is not an option the seat can take: the decline is forced and
     *  nothing is asked (runs/luna-v8/g1 asked it about Spell Pierce with no mana up). One row, not two. */
    @Test
    public void anUnlessCostTheSeatCannotPayIsNotAsked() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card bolt = card("Lightning Bolt", me, ZoneType.Hand);
        LineFindingProbeTest.rivalCasts(g, "Spell Pierce", bolt);
        SpellAbility pierce = g.getStack().peekAbility();
        Script model = new Script("CHOICE: 1");
        Assert.assertFalse(seat(g, model, model).payCostToPreventEffect(new forge.game.cost.Cost("2", false), pierce, false, null));
        Assert.assertTrue(model.prompts.isEmpty(), "an unpayable cost was asked: " + model.prompts);
    }

    @Test
    public void aKickerIsTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        for (int i = 0; i < 5; i++) card("Mountain", me, ZoneType.Battlefield);
        Card burst = card("Burst Lightning", me, ZoneType.Hand);
        SpellAbility sa = spell(burst, me);
        List<OptionalCostValue> costs = forge.game.GameActionUtil.getOptionalCostValues(sa);
        Assert.assertFalse(costs.isEmpty());
        Script model = new Script("PICKS: NONE").on("optional additional costs", p -> "PICKS: 1");
        Assert.assertEquals(seat(g, model, model).chooseOptionalCosts(sa, costs), Collections.singletonList(costs.get(0)));
        source(burst);
    }

    /** Palace Jailer's two enter triggers: the model's order is the stack's, and the exile trigger's
     *  target is the model's. The order's options used to show "[Zone Changer: Palace Jailer (8)]". */
    @Test
    public void theOrderOfSimultaneousTriggersIsTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card lions = card("Savannah Lions", rival, ZoneType.Battlefield);
        Card jailer = card("Palace Jailer", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1") {
            @Override public String ask(String prompt) {
                prompts.add(prompt);
                if (prompt.contains("go on the stack at the same time")) {
                    Matcher ex = Pattern.compile("(?m)^\\s*(\\d+)\\) [^\\n]*exile target").matcher(prompt);
                    Matcher mo = Pattern.compile("(?m)^\\s*(\\d+)\\) [^\\n]*become the monarch").matcher(prompt);
                    Assert.assertTrue(ex.find() && mo.find(), prompt);
                    return "PICKS: " + ex.group(1) + "," + mo.group(1);
                }
                if (prompt.contains("Choose the TARGET")) return "CHOICE: " + opt(prompt, "Savannah Lions");
                return "CHOICE: 1";
            }
        };
        seat(g, model, model);
        g.getAction().moveToPlay(jailer, null, null);
        g.getTriggerHandler().runWaitingTriggers();
        g.getStack().addAllTriggeredAbilitiesToStack();
        Assert.assertTrue(model.asked("go on the stack at the same time"), PromptFidelityTest.decisions(model));
        SpellAbility top = g.getStack().peekAbility();
        Assert.assertTrue(top.getTargets().contains(lions), "the exile trigger resolves first, at the model's target: " + top);
    }

    @Test
    public void whatIsRevealedFromHandIsTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Lightning Bolt", me, ZoneType.Hand);
        Card cs = card("Counterspell", me, ZoneType.Hand);
        Script model = new Script("PICKS: NONE").on("REVEAL", picks("Counterspell"));
        CardCollection hand = new CardCollection(me.getCardsIn(ZoneType.Hand));
        Assert.assertEquals(new ArrayList<>(seat(g, model, model).chooseCardsToRevealFromHand(0, 1, hand)), Collections.singletonList(cs));
    }

    @Test
    public void scryAndSurveilAreTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card a = card("Lightning Bolt", me, ZoneType.Library);
        Card b = card("Counterspell", me, ZoneType.Library);
        Script model = new Script("PICKS: NONE").on("(scry)", picks("Counterspell")).on("(surveil)", picks("Lightning Bolt"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        org.apache.commons.lang3.tuple.ImmutablePair<CardCollection, CardCollection> scry = ctrl.arrangeForScry(new CardCollection(Arrays.asList(a, b)));
        Assert.assertEquals(new ArrayList<>(scry.getLeft()), Collections.singletonList(b));
        Assert.assertEquals(new ArrayList<>(scry.getRight()), Collections.singletonList(a));
        org.apache.commons.lang3.tuple.ImmutablePair<CardCollection, CardCollection> surveil = ctrl.arrangeForSurveil(new CardCollection(Arrays.asList(a, b)));
        Assert.assertEquals(new ArrayList<>(surveil.getLeft()), Collections.singletonList(a));
    }

    /** Brainstorm's put-back order, asked top-down. */
    @Test
    public void aPutBackOrderIsTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card bs = card("Brainstorm", me, ZoneType.Hand);
        Card a = card("Lightning Bolt", me, ZoneType.Hand);
        Card b = card("Counterspell", me, ZoneType.Hand);
        Script model = new Script("PICKS: 1").on("in your preferred order", picks("Counterspell", "Lightning Bolt"));
        seat(g, model, model).orderMoveToZoneList(new CardCollection(Arrays.asList(a, b)), ZoneType.Library, spell(bs, me));
        Assert.assertTrue(model.asked("in your preferred order"));
        source(bs);
    }

    // ------------------------------------------------------------------ yes / no

    @Test
    public void anOptionalTriggerIsTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card src = card("Recruiter of the Guard", me, ZoneType.Battlefield);
        SpellAbility sa = src.getTriggers().iterator().next().ensureAbility();
        sa.setActivatingPlayer(me);
        forge.game.trigger.WrappedAbility optional = new forge.game.trigger.WrappedAbility(src.getTriggers().iterator().next(), sa, me) {
            @Override public boolean isMandatory() { return false; }
        };
        Script model = new Script("CHOICE: 1").on("OPTIONAL TRIGGER", choice("NO"));
        Assert.assertFalse(seat(g, model, model).confirmTrigger(optional));
        Assert.assertTrue(model.asked("OPTIONAL TRIGGER"));
    }

    @Test
    public void anOptionalPaymentIsTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card src = card("Snuff Out", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("Pay 4 life", choice("YES"));
        Assert.assertTrue(seat(g, model, model).confirmPayment(null, "Pay 4 life?", spell(src, me)));
        source(src);
    }

    @Test
    public void anOptionalReplacementIsTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card src = card("Mox Diamond", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("discard a land card", choice("NO"));
        Assert.assertFalse(seat(g, model, model).confirmReplacementEffect(null, spell(src, me), null,
                "Do you want to discard a land card instead of putting Mox Diamond onto the battlefield?"));
        Assert.assertTrue(model.asked("discard a land card"));
    }

    @Test
    public void aYesNoIsTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card src = card("Ponder", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("Yes/No decision", choice("YES"));
        Assert.assertTrue(seat(g, model, model).confirmAction(spell(src, me), PlayerActionConfirmMode.Random,
                "Do you want to shuffle your library?", null, null, null));
        source(src);
    }

    // ------------------------------------------------------------------ numbers, colours, names

    /** A small range is a menu; a wide one is a stated range answered NUMBER: (it had no DECISION line). */
    @Test
    public void numbersAreTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card src = card("Galvanic Discharge", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("amount of energy", choice("2")).on("Every integer", p -> "NUMBER: 17");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Assert.assertEquals(ctrl.chooseNumber(spell(src, me), "amount of energy to pay", 0, 3), 2);
        Assert.assertEquals(ctrl.chooseNumber(spell(src, me), "Choose a number", 0, 40), 17);
        source(src);
    }

    @Test
    public void aBinaryChoiceIsTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card src = card("Twiddle", me, ZoneType.Hand);
        Script model = new Script("CHOICE: 1").on("Tap or untap", choice("Untap"));
        Assert.assertFalse(seat(g, model, model).chooseBinary(spell(src, me), "Tap or untap the permanent?", BinaryChoiceType.TapOrUntap, (Boolean) null));
        source(src);
    }

    @Test
    public void coloursAreTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card src = card("Painter's Servant", me, ZoneType.Battlefield);
        ColorSet three = ColorSet.fromEnums(MagicColor.Color.WHITE, MagicColor.Color.BLUE, MagicColor.Color.RED);
        Script model = new Script("CHOICE: 1").on("Choose a color", choice("red"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Assert.assertEquals(ctrl.chooseColor("Choose a color", spell(src, me), three), MagicColor.RED);
        // The colourless-allowed ask named no source (the engine hands it a card, not an ability).
        Assert.assertEquals(ctrl.chooseColorAllowColorless("Choose a color", src, three), MagicColor.RED);
        source(src);
    }

    @Test
    public void aTypeIsTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card src = card("Cavern of Souls", me, ZoneType.Battlefield);
        // It asks for a creature type: the question read "Choose a Creature to name" (runs/loop/g04, g05).
        Script model = new Script("CHOICE: 1").on("Choose a creature type", choice("Elf"));
        Assert.assertEquals(seat(g, model, model).chooseSomeType("Creature", spell(src, me), Arrays.asList("Goblin", "Elf", "Human"), false), "Elf");
        source(src);
    }

    @Test
    public void aProtectionIsTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card src = card("Mother of Runes", me, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1").on("protect from", choice("red"));
        Assert.assertEquals(seat(g, model, model).chooseProtectionType(spell(src, me), Arrays.asList("white", "blue", "red")), "red");
        source(src);
    }

    /** A named card from a short list, and a free name; the free name's retry carries the whole ask. */
    @Test
    public void aNamedCardIsTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card src = card("Pithing Needle", me, ZoneType.Battlefield);
        List<ICardFace> faces = new ArrayList<>();
        for (String n : new String[] {"Griselbrand", "Thoughtseize"})
            faces.add(FModel.getMagicDb().getCommonCards().getCard(n).getRules().getMainPart());
        Script model = new Script("CHOICE: 1") {
            int free;
            @Override public String ask(String prompt) {
                prompts.add(prompt);
                // The free-form ask (either protocol) says in words what the name must be.
                if (prompt.contains("(it must be the name of a nonland card)")) return free++ == 0 ? "Something with an activated ability." : "CARD: Grim Lavamancer";
                return "CHOICE: " + opt(prompt, "Thoughtseize");
            }
        };
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Assert.assertEquals(ctrl.chooseCardName(spell(src, me), faces, "Choose a card name."), "Thoughtseize");
        Assert.assertEquals(ctrl.chooseCardName(spell(src, me), f -> true, "Card.nonLand", "Choose a nonland card name."), "Grim Lavamancer");
        Assert.assertEquals(model.prompts.size(), 3, "the list ask, the free ask and its retry");
        for (String q : model.prompts) Assert.assertFalse(q.contains("Card.nonLand"), "the engine's filter is shown: " + q);
        source(src);
    }

    /** A token is not a card name (rule 201.3): a seat that names one is not taken at its word, even though `inspect`
     *  answers by a token's name. */
    @Test
    public void aTokenNameIsNotACardName() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card src = card("Pithing Needle", me, ZoneType.Battlefield);
        Script model = new Script("CARD: Goblin Token");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        String named = ctrl.chooseCardName(spell(src, me), f -> true, "Card", "Choose a card name.");
        Assert.assertNotEquals(named, "Goblin Token");
        Assert.assertNotEquals(named, "Goblin");
        source(src);
    }

    /** A creature type is said in its own plural: Forge wrote Heritage Druid's cost "Tap three untapped Elfs you
     *  control" on the ACTIVATE option (runs/loop/g10 turn 6). */
    @Test
    public void aTypeIsSaidInItsOwnPlural() {
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.typePlurals("Tap three untapped Elfs you control: Add {G}{G}{G}."),
                "Tap three untapped Elves you control: Add {G}{G}{G}.");
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.typePlurals("Sacrifice two Goblins and three Wolfs."),
                "Sacrifice two Goblins and three Wolves.");
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.typePlurals("Elves and Clues stay as they are."),
                "Elves and Clues stay as they are.");
    }

    /** The engine's name filter in words; a filter with anything but types and subtypes is left out, never shown raw
     *  (runs/loop/g05: "(must match: Card.nonLand)" beside Cabal Therapy's own "Choose a nonland card name"). */
    @Test
    public void aNameFilterIsSaidInWords() {
        java.util.function.Function<String, String> name = LlmSpikeGui.PlayerControllerLLM::nameConstraint;
        java.util.function.Function<String, String> cost = LlmSpikeGui.PlayerControllerLLM::plainFilters;
        Assert.assertEquals(name.apply("Card.nonLand"), "a nonland card");
        Assert.assertEquals(name.apply("Creature"), "a creature card");
        Assert.assertEquals(name.apply("Card.Land+nonBasic"), "a nonbasic land card");
        Assert.assertEquals(name.apply("Card.Artifact"), "an artifact card");
        Assert.assertEquals(name.apply("Planeswalker.Nissa"), "a Nissa planeswalker card");
        Assert.assertEquals(name.apply("Card.Elf"), "an Elf card");
        Assert.assertEquals(name.apply("Card.nonLand+nonCreature"), "a nonland noncreature card");
        // A condition that is not a type, a subtype or a colour leaves the name ask to the card's own text.
        Assert.assertNull(name.apply("Creature.cmcEQX"));
        Assert.assertNull(name.apply("Card.Self"));
        Assert.assertNull(name.apply("Card.OppOwn+!token+nonCopiedSpell"));
        Assert.assertNull(name.apply(null));
        // Costs: Force of Will's pitch read "Exile a Card.Blue+Other card from your hand".
        Assert.assertEquals(cost.apply("Pay 1 life, Exile a Card.Blue+Other card from your hand"),
                "Pay 1 life, Exile another blue card from your hand");
        Assert.assertEquals(cost.apply("Exile two Card.Blue+Other cards from your hand"), "Exile two other blue cards from your hand");
        Assert.assertEquals(cost.apply("Sacrifice a Creature.Black"), "Sacrifice a black creature");
        Assert.assertEquals(cost.apply("Sacrifice three Creature.Black"), "Sacrifice three black creatures");
        Assert.assertEquals(cost.apply("Return three Land.Basic to their owner's hand"), "Return three basic lands to their owner's hand");
        Assert.assertEquals(cost.apply("Tap four untapped Creature.withFlying you control"), "Tap four untapped creatures you control");
        Assert.assertEquals(cost.apply("Sacrifice a creature"), "Sacrifice a creature");
        // A stack line (runs/loop/g07 T5, Shallow Grave).
        Assert.assertEquals(cost.apply("ForgeAI returns a Creature.TopGraveyardCreature+YouCtrl card from their graveyard"),
                "ForgeAI returns a creature card from their graveyard");
        // Recorded stack lines across runs/ (2026-10-18 sweep): who controls it, a chosen name, a bare filter.
        Assert.assertEquals(cost.apply("ForgeAI chooses a Card.nonCreature+nonLand card from it."), "ForgeAI chooses a noncreature nonland card from it.");
        Assert.assertEquals(cost.apply("Deals 1 damage to Creature.OppCtrl."), "Deals 1 damage to creatures an opponent controls.");
        Assert.assertEquals(cost.apply("Deals 3 damage to Creature.nonElemental."), "Deals 3 damage to non-Elemental creatures.");
        Assert.assertEquals(cost.apply("They discard all of type: Card.NamedCard card."), "They discard all of type: cards with the chosen name.");
        Assert.assertEquals(cost.apply("Seat searches their library for a Remembered.sameName card, reveals it"),
                "Seat searches their library for a card with the same name, reveals it");
        Assert.assertEquals(cost.apply("deals 0 damage to Creature.ControlledBy TargetedOrController. (X=0)"),
                "deals 0 damage to creatures that player or that planeswalker's controller controls. (X=0)");
        Assert.assertEquals(cost.apply("Seat puts a Creature.YouCtrl card from their hand onto the battlefield"),
                "Seat puts a creature card from their hand onto the battlefield");
        Assert.assertEquals(cost.apply("Deals 2 damage to Creature.withFlying."), "Deals 2 damage to the creatures the card describes.");
        Assert.assertFalse(AskContract.ENGINE_FILTER.matcher(cost.apply("Exile a Card.cmcEQX card")).find());
    }

    // ------------------------------------------------------------------ choices Forge's AI made outright until 2026-10-15

    /** Fact or Fiction's piles, an opening-hand Leyline, colours, a counter kind, a keyword, clash, an optional
     *  static, a card face: each is the seat's, through the live controller. None reaches Forge's AI. */
    @Test
    public void choicesForgesAiUsedToMakeAreTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card bolt = card("Lightning Bolt", me, ZoneType.Library);
        Card bears = card("Grizzly Bears", me, ZoneType.Library);
        Card src = card("Pithing Needle", me, ZoneType.Battlefield);
        card("Grizzly Bears", me, ZoneType.Battlefield);   // one creature: the sacrifice below can be paid once, not twice
        lands(me, "Plains");                               // and the Needle's own {1}
        Script model = new Script("CHOICE: 1")
                .on("Choose a pile", choice("Pile 2"))
                .on("OPENING HAND", p -> "PICKS: NONE")
                .on("Choose colors", picks("blue", "red"))
                .on("kind of counter", choice("Loyalty"))
                .on("Choose a keyword", choice("Trample"))
                .on("CLASH", choice("Bottom"))
                .on("optional effect", choice("No"))
                .on("Choose a card face", choice("Thoughtseize"))
                .on("How many times", choice("1"))
                .on("reduce the cost", choice("1"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        SpellAbility sa = spell(src, me);
        Assert.assertFalse(ctrl.chooseCardsPile(sa, new CardCollection(bolt), new CardCollection(bears), "True"), "pile 2");
        Assert.assertTrue(ctrl.chooseSaToActivateFromOpeningHand(new ArrayList<>(Arrays.asList(spell(bolt, me)))).isEmpty());
        ColorSet cs = ctrl.chooseColors("Choose two colors", sa, 2, 2, ColorSet.fromMask(MagicColor.ALL_COLORS));
        Assert.assertTrue(cs.hasBlue() && cs.hasRed() && cs.countColors() == 2, String.valueOf(cs));
        Assert.assertEquals(ctrl.chooseCounterType(Arrays.asList(forge.game.card.CounterEnumType.P1P1,
                forge.game.card.CounterEnumType.LOYALTY), sa, "Choose a kind of counter", null).getName(),
                forge.game.card.CounterEnumType.LOYALTY.getName());
        Assert.assertEquals(ctrl.chooseKeywordForPump(Arrays.asList("Flying", "Trample"), sa, "Choose a keyword", bears), "Trample");
        Assert.assertFalse(ctrl.willPutCardOnTop(bolt), "bottom");
        Assert.assertFalse(ctrl.confirmStaticApplication(src, PlayerActionConfirmMode.Random, "Apply this optional effect?", null));
        List<ICardFace> faces = new ArrayList<>();
        for (String n : new String[] {"Griselbrand", "Thoughtseize"})
            faces.add(FModel.getMagicDb().getCommonCards().getCard(n).getRules().getMainPart());
        Assert.assertEquals(ctrl.chooseSingleCardFace(sa, faces, "Choose a card face").getName(), "Thoughtseize");
        Assert.assertEquals(ctrl.chooseNumberForKeywordCost(sa, new forge.game.cost.Cost("Sac<1/Creature>", false), null, null, 2), 1);
        // A cost reduction is the seat's when it pays; pricing takes the whole reduction without asking (#225).
        Assert.assertEquals((int) ctrl.inRealPaymentForTest(() -> ctrl.chooseNumberForCostReduction(sa, 0, 2)), 1);
        Assert.assertEquals(ctrl.chooseNumberForCostReduction(sa, 0, 2), 2, "pricing asks nothing");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** Edict-style destroy, a spell chosen for an effect, and "discard two unless you discard an artifact". */
    @Test
    public void destroyAndSpellAndDiscardUnlessAreTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        Card lions = card("Savannah Lions", me, ZoneType.Battlefield);
        Card bolt = card("Lightning Bolt", me, ZoneType.Hand);
        Card petal = card("Lotus Petal", me, ZoneType.Hand);
        card("Mountain", me, ZoneType.Hand);
        Card src = card("Pithing Needle", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1")
                .on("DESTROY", picks("Savannah Lions"))
                .on("Choose a spell", choice("Lightning Bolt"))
                .on("DISCARD", picks("Lotus Petal"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        SpellAbility sa = spell(src, rival);
        Assert.assertEquals(ctrl.choosePermanentsToDestroy(sa, 1, 1, new CardCollection(Arrays.asList(bears, lions)), "Choose a creature to DESTROY").getFirst(), lions);
        SpellAbility boltSa = spell(bolt, me), petalSa = spell(petal, me);
        Assert.assertEquals(ctrl.chooseSingleSpellForEffect(Arrays.asList(petalSa, boltSa), sa, "Choose a spell", null), boltSa);
        Assert.assertEquals(ctrl.chooseCardsToDiscardUnlessType(2, me.getCardsIn(ZoneType.Hand), new String[] {"Artifact"}, sa).size(), 1,
                "one artifact instead of two cards");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** Convoke: which creatures help pay is the seat's (Forge's AI paid nothing so), and each pays a symbol
     *  it can; changing a spell's target (Misdirection-style) is the seat's (Forge's AI never retargeted). */
    @Test
    public void convokeAndANewTargetAreTheModels() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card stoke = card("Stoke the Flames", me, ZoneType.Hand);       // {2}{R}{R}, convoke
        Card goblin = card("Goblin Guide", me, ZoneType.Battlefield);    // red
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);    // green
        card("Savannah Lions", me, ZoneType.Battlefield);
        lands(me, "Mountain", "Forest");   // the {1}{R} the two convokers leave (since harness-epoch-40 it must stay payable)
        Card bolt = card("Lightning Bolt", me, ZoneType.Hand);
        Card goyf = card("Tarmogoyf", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1")
                .on("CONVOKE", picks("Goblin Guide", "Grizzly Bears"))
                .on("CHANGE TARGET", choice("Tarmogoyf"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        SpellAbility sa = spell(stoke, me);
        Map<Card, forge.card.mana.ManaCostShard> paid = ctrl.inRealPaymentForTest(() -> ctrl.chooseCardsForConvokeOrImprovise(sa,
                stoke.getManaCost(), me.getCreaturesInPlay(), false, true, null));
        Assert.assertEquals(paid.size(), 2, String.valueOf(paid));
        Assert.assertEquals(paid.get(goblin), forge.card.mana.ManaCostShard.RED, "the red creature pays a red symbol");
        Assert.assertEquals(paid.get(bears), forge.card.mana.ManaCostShard.GENERIC, "the green one pays generic");
        SpellAbility boltSa = spell(bolt, me);
        boltSa.getTargets().add(rival);
        forge.game.spellability.TargetChoices t = ctrl.chooseNewTargetsFor(boltSa, null, true);
        Assert.assertNotNull(t, "the seat chose a new target");
        Assert.assertTrue(t.contains(goyf), String.valueOf(t));
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    @Test
    public void aSpellWithSeveralTargetsGetsTheModelsNewSet() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card flicker = card("Ghostly Flicker", me, ZoneType.Hand);      // exactly two targets
        Card goblin = card("Goblin Guide", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        Card lions = card("Savannah Lions", me, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1").on("CHANGE TARGETS", picks("Grizzly Bears", "Savannah Lions"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        SpellAbility sa = spell(flicker, me);
        sa.getTargets().add(goblin);
        sa.getTargets().add(bears);
        forge.game.spellability.TargetChoices t = ctrl.chooseNewTargetsFor(sa, null, true);
        Assert.assertNotNull(t, "the seat chose a new set");
        Assert.assertTrue(t.contains(bears) && t.contains(lions) && !t.contains(goblin), String.valueOf(t));

        // One target where the spell needs two: the old targets stay.
        Script one = new Script("CHOICE: 1").on("CHANGE TARGETS", picks("Savannah Lions"));
        LlmSpikeGui.PlayerControllerLLM ctrl1 = seat(g, one, one);
        SpellAbility sa1 = spell(flicker, me);
        sa1.getTargets().add(goblin);
        sa1.getTargets().add(bears);
        Assert.assertNull(ctrl1.chooseNewTargetsFor(sa1, null, true), "a set the spell cannot have keeps the old one");
        Assert.assertTrue(sa1.getTargets().contains(goblin) && sa1.getTargets().contains(bears), String.valueOf(sa1.getTargets()));
        Assert.assertEquals(ctrl.aiDelegationsCount() + ctrl1.aiDelegationsCount(), 0);
    }

    @Test
    public void anEffectPlayedWithoutTheStackHasTheModelsTargets() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card bolt = card("Lightning Bolt", me, ZoneType.Hand);
        card("Goblin Guide", rival, ZoneType.Battlefield);
        Card goyf = card("Tarmogoyf", rival, ZoneType.Battlefield);
        card("Mountain", me, ZoneType.Battlefield);                     // Bolt's {R}
        Script model = new Script("CHOICE: 1").on("Lightning Bolt", choice("Tarmogoyf"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        SpellAbility sa = spell(bolt, me);
        ctrl.playSpellAbilityNoStack(sa, true);
        Card now = g.getCardState(goyf);
        Assert.assertTrue(now.getDamage() == 3 || now.isInZone(ZoneType.Graveyard),
                "the model's target took the damage: " + now + " in " + now.getZone() + ", damage " + now.getDamage());
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    @Test
    public void aDividedSpellKeepsItsPortionsOnItsNewTargets() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card arc = card("Arc Lightning", me, ZoneType.Hand);            // 3 divided among up to three
        Card goblin = card("Goblin Guide", rival, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", rival, ZoneType.Battlefield);
        Card lions = card("Savannah Lions", rival, ZoneType.Battlefield);
        Card goyf = card("Tarmogoyf", rival, ZoneType.Battlefield);
        Script model = new Script("CHOICE: 1")
                .on("CHANGE TARGETS", picks("Savannah Lions", "Tarmogoyf"))
                .on("DIVIDE: which portion goes to Savannah Lions", choice("1"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        SpellAbility sa = spell(arc, me);
        sa.getTargets().add(goblin);
        sa.addDividedAllocation(goblin, 2);
        sa.getTargets().add(bears);
        sa.addDividedAllocation(bears, 1);
        forge.game.spellability.TargetChoices t = ctrl.chooseNewTargetsFor(sa, null, true);
        Assert.assertNotNull(t, "the seat chose a new set");
        Assert.assertEquals(t.getDividedValue(lions), Integer.valueOf(1), "the seat gave the Lions the 1");
        Assert.assertEquals(t.getDividedValue(goyf), Integer.valueOf(2), "and Tarmogoyf the 2 left");
        Assert.assertEquals(t.getTotalDividedValue(), 3);

        // One target holding the whole division (Forked Bolt, up to two): the new target takes all of it.
        Script one = new Script("CHOICE: 1").on("CHANGE TARGETS", picks("Tarmogoyf"));
        LlmSpikeGui.PlayerControllerLLM ctrl1 = seat(g, one, one);
        Card fork = card("Forked Bolt", me, ZoneType.Hand);
        SpellAbility sa1 = spell(fork, me);
        sa1.getTargets().add(goblin);
        sa1.addDividedAllocation(goblin, 2);
        forge.game.spellability.TargetChoices t1 = ctrl1.chooseNewTargetsFor(sa1, null, true);
        Assert.assertNotNull(t1);
        Assert.assertEquals(t1.getDividedValue(goyf), Integer.valueOf(2), String.valueOf(t1));
        Assert.assertEquals(ctrl.aiDelegationsCount() + ctrl1.aiDelegationsCount(), 0);
    }

    /** Berserk on a 3/3: X is the target's power, so the stack says "+3/+0". The resolve line (the seat's GAME
     *  HISTORY) was built after the pump applied, re-read the pumped power and said "+6/+0"; the applied amount was +3
     *  either way (g06 seed 4106 T7: "+5/+0" on the stack, "+10/+0" in history). HARNESS PATCH of MagicStack.java. */
    @Test
    public void aPumpResolveLineSaysWhatTheStackPreviewSays() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card giant = card("Hill Giant", me, ZoneType.Battlefield);
        Assert.assertEquals(giant.getNetPower(), 3, "fixture: a 3-power target");
        LineFindingProbeTest.rivalCasts(g, "Berserk", giant);
        Assert.assertTrue(g.getStack().peekAbility().getStackDescription().contains("+3/+0"),
                "fixture: the stack preview says +3: " + g.getStack().peekAbility().getStackDescription());
        g.getStack().resolveStack();
        Assert.assertEquals(giant.getNetPower(), 6, "the +3 applied");
        String resolveLine = null;
        for (GameLogEntry e : g.getGameLog().getAllEntries()) {
            if (e.type() == GameLogEntryType.STACK_RESOLVE && e.message().contains("Hill Giant")) {
                resolveLine = e.message();
            }
        }
        Assert.assertNotNull(resolveLine, "the resolve reached the game log");
        Assert.assertTrue(resolveLine.contains("+3/+0"), "the resolve line says what was applied: " + resolveLine);
        Assert.assertFalse(resolveLine.contains("+6/+0"), "the resolve line does not re-read the pumped power: " + resolveLine);
        Assert.assertTrue(resolveLine.contains("trample"), resolveLine);
    }

    // ------------------------------------------------------------------ the gate

    /** Runs last: every type the seat asks has a test above that a model answered, and nothing above
     *  asked a type this class does not list. */
    @Test(priority = 1000)
    public void everyOwnedDecisionTypeIsExercisedHere() {
        Set<String> missing = new TreeSet<>(OWNED);
        missing.removeAll(ANSWERED);
        Assert.assertTrue(missing.isEmpty(), "owned decision types no test here drives through the live controller: " + missing);
        Set<String> unknown = new TreeSet<>(ANSWERED);
        unknown.removeAll(OWNED);
        Assert.assertTrue(unknown.isEmpty(), "decision types asked here that OWNED (and GOLDEN-CHECKS.md) do not list: " + unknown);
    }
}
