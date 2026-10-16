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
import forge.game.card.Card;
import forge.game.card.CardCollection;
import forge.game.combat.Combat;
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
            "confirm-static", "card-face", "keyword-cost", "cost-reduction", "discard-unless", "convoke", "new-targets"));

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
            boolean paid = ctrl.playChosenSpellAbility(r.get(0));
            Assert.assertTrue(paid, "the cast was paid (floating=" + floating + ")");
            Assert.assertTrue(adn.isInZone(ZoneType.Stack), "Ad Nauseam is on the stack (floating=" + floating + "), petals: "
                    + petal1.getZone() + " " + petal2.getZone());
        }
    }

    /** The same turn as it was played: Dark Ritual, then a Lotus Petal cast this turn, then Ad Nauseam paid with
     *  the Ritual's mana and both Petals. */
    @Test
    public void ritualThenPetalThenAdNauseamIsPaid() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Swamp", me, ZoneType.Battlefield);
        card("Lotus Petal", me, ZoneType.Battlefield);
        card("Dark Ritual", me, ZoneType.Hand);
        card("Lotus Petal", me, ZoneType.Hand);
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
            Assert.assertTrue(ctrl.playChosenSpellAbility(r.get(0)), play + " paid; pool " + me.getManaPool().totalMana());
            if (!play.endsWith("Ad Nauseam")) { while (!g.getStack().isEmpty()) g.getStack().resolveStack(); }
        }
        Assert.assertTrue(adn.isInZone(ZoneType.Stack), "Ad Nauseam is on the stack");
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
        Script model = new Script("CHOICE: 1").on("An effect lets you cast", choice("Do not cast it"));
        Assert.assertFalse(seat(g, model, model).playSaFromPlayEffect(spell(bolt, me)));
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
        GameEntity got = seat(g, model, model).chooseSingleEntityForEffect(opts, null, spell(src, me), "Choose a creature", false, null, null);
        Assert.assertEquals(got, b);
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
        Script model = new Script("CHOICE: 1").on("to name", choice("Elf"));
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
                // The free-form ask (either protocol) names what the name must match.
                if (prompt.contains("(must match:")) return free++ == 0 ? "Something with an activated ability." : "CARD: Grim Lavamancer";
                return "CHOICE: " + opt(prompt, "Thoughtseize");
            }
        };
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Assert.assertEquals(ctrl.chooseCardName(spell(src, me), faces, "Choose a card name."), "Thoughtseize");
        Assert.assertEquals(ctrl.chooseCardName(spell(src, me), f -> true, "Card.nonLand", "Choose a nonland card name."), "Grim Lavamancer");
        Assert.assertEquals(model.prompts.size(), 3, "the list ask, the free ask and its retry");
        source(src);
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
        Assert.assertEquals(ctrl.chooseNumberForCostReduction(sa, 0, 2), 1);
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
