package forge.spike;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import forge.card.MagicColor;
import forge.game.Game;
import forge.game.card.Card;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.spellability.SpellAbility;
import forge.game.zone.ZoneType;

import org.testng.Assert;
import org.testng.ITestResult;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import static forge.spike.GoldenChecksTest.Script;
import static forge.spike.GoldenChecksTest.choice;
import static forge.spike.GoldenChecksTest.opt;
import static forge.spike.LineFindingProbeTest.bareGame;
import static forge.spike.LineFindingProbeTest.card;
import static forge.spike.LineFindingProbeTest.seat;

/**
 * Review 2026-10-16 item S3, the four resolution-side surfaces: the seat is asked through the real engine
 * path, its answer is what the game does, and Forge's AI decides nothing. Replacement order asks when two
 * replacers match one damage event; BidLife announces the starting bid and its top-bid invitations ask; a
 * generic shard over two floats of different colours names which mana pays; an optional static trigger's
 * play is the seat's may.
 */
public class SurfaceResolutionChoicesTest {

    /** The ask types this wave puts on the seat's surface; the gate below fails until the model
     *  has answered each through the live controller. */
    static final Set<String> HOOKS = new TreeSet<>(Arrays.asList(
            "replacement-order", "mana-from-pool", "confirm-trigger", "confirm", "number-value"));

    /** Types a model answered in this class (tier smart or fast), across every test. */
    static final Set<String> ANSWERED = Collections.synchronizedSet(new TreeSet<>());

    private final List<String> asked = new ArrayList<>();
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
        // This class owns its types the way the effects family does (the gate below): taps land
        // in this class's set only. GoldenChecksTest.ANSWERED stays untouched — the single acceptance
        // JVM runs the classes alphabetically, so golden's gate always runs BEFORE these tests and its
        // OWNED list cannot name types answered here (measured 2026-10-16: priorities do not reorder
        // across classes); golden's everyOwnedDecisionTypeIsExercisedHere guards its own class only.
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

    private static final Pattern OPTION_LINE = Pattern.compile("(?m)^\\s*(\\d+)\\) (.*)$");
    /** Labels of one ask's numbered option lines (the observations and options the seat reads). */
    static List<String> optionLabels(String prompt) {
        List<String> out = new ArrayList<>();
        Matcher m = OPTION_LINE.matcher(prompt.substring(Math.max(0, prompt.lastIndexOf("DECISION:"))));
        while (m.find() && out.size() < 50) out.add(m.group(2).trim());
        return out;
    }

    private void source(Card c) { sourceName = c.getName(); sourceText = c.getOracleText(); }

    // ------------------------------------------------------------------ 1. chooseSingleReplacementEffect

    private static final String CIRCLE = "Circle of Protection: Red";

    /** Two Circle of Protection: Red activations each arm a prevention against the same red source; both
     *  match the seat's one damage event, both are offered, and the seat's answer spends exactly one: the
     *  damage is prevented and the other replacer stays armed. Goblin Bombardment's sacrifice cost is the
     *  red source named; two Plains keep the {W} cost out of the mana questions (one untapped source kind). */
    @Test
    public void theSeatPicksWhichOfTwoReplacersSpends() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        for (int i = 0; i < 2; i++) card(CIRCLE, me, ZoneType.Battlefield);
        for (int i = 0; i < 2; i++) card("Plains", me, ZoneType.Battlefield);
        card("Mountain", me, ZoneType.Battlefield);
        card("Goblin Bombardment", me, ZoneType.Battlefield);
        card("Memnite", me, ZoneType.Battlefield);
        String[] next = {"ACTIVATE " + CIRCLE};
        Script model = new Script("CHOICE: 1")
                .on("You have priority", p -> choice(next[0]).apply(p))
                .on("Choose a source", choice("Goblin Bombardment"))
                .on("COST: to pay for", p -> "PICKS: " + opt(p, "Memnite"))
                .on("Choose the TARGET", choice("you (Seat)"))
                .on("Several replacement effects apply", p -> "CHOICE: 1");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);

        for (int i = 0; i < 2; i++) {          // both activations arm one prevention each
            List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
            Assert.assertNotNull(r, "CoP:Red not offered: " + decisionsTail(model));
            Assert.assertEquals(r.get(0).getHostCard().getName(), CIRCLE, "the picked play");
            ctrl.playChosenSpellAbility(r.get(0));
            g.getStack().resolveStack();       // the ChooseSource ask names the red source, once per activation
        }
        next[0] = "ACTIVATE Goblin Bombardment";
        List<SpellAbility> boom = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(boom, "Goblin Bombardment not offered");
        ctrl.playChosenSpellAbility(boom.get(0)); // sacrifice Memnite as the cost, aim the damage at the seat
        g.getStack().resolveStack();           // the damage event matches both arms: the ask

        Assert.assertEquals(inGraveyard(me, "Memnite"), 1, "the cost was the Memnite the seat named");
        String ask = PromptFidelityTest.promptWith(model, "Several replacement effects apply");
        List<String> replacers = new ArrayList<>();
        for (String l : optionLabels(ask)) if (l.contains(CIRCLE)) replacers.add(l);
        Assert.assertEquals(replacers.size(), 2, "the menu did not offer BOTH replacers: " + replacers);
        Assert.assertEquals(me.getLife(), 20, "the damage the chosen replacer prevented");
        Assert.assertEquals(armedEffects(me), 1, "the other replacer stayed armed");
        Assert.assertEquals(tokensLeft(me), 1, "the chosen arm was used and left the game");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
        Assert.assertEquals(model.prompts.stream().filter(q -> q.contains("Several replacement effects")).count(), 1L,
                "the one damage event was asked once, not once per replacer");
    }

    /** Three arms are armed; the first damage spends one, and the next damage of the same source is asked
     *  its own, smaller menu — an incident never spends, or bundles, silently. */
    @Test
    public void theNextIncidentAsksItsOwnReplacementMenus() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        for (int i = 0; i < 3; i++) card(CIRCLE, me, ZoneType.Battlefield);
        for (int i = 0; i < 3; i++) card("Plains", me, ZoneType.Battlefield);
        card("Mountain", me, ZoneType.Battlefield);
        card("Goblin Bombardment", me, ZoneType.Battlefield);
        card("Memnite", me, ZoneType.Battlefield);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        List<Integer> menuSizes = new ArrayList<>();
        String[] next = {"ACTIVATE " + CIRCLE};
        String[] victim = {"Memnite"};
        Script model = new Script("CHOICE: 1")
                .on("You have priority", p -> choice(next[0]).apply(p))
                .on("Choose a source", choice("Goblin Bombardment"))
                .on("COST: to pay for", p -> "PICKS: " + opt(p, victim[0]))
                .on("Choose the TARGET", choice("you (Seat)"))
                .on("Several replacement effects apply", p -> {
                    menuSizes.add(menuOptions(p, CIRCLE));
                    return "CHOICE: 1"; });
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);

        for (int i = 0; i < 3; i++) {
            List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
            Assert.assertNotNull(r, "CoP:Red not offered: " + decisionsTail(model));
            ctrl.playChosenSpellAbility(r.get(0));
            g.getStack().resolveStack();
        }
        for (String v : new String[] {"Memnite", "Grizzly Bears"}) {
            victim[0] = v;
            next[0] = "ACTIVATE Goblin Bombardment";
            List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
            Assert.assertNotNull(r, "Goblin Bombardment not offered");
            ctrl.playChosenSpellAbility(r.get(0));
            g.getStack().resolveStack();
        }
        Assert.assertEquals(inGraveyard(me, "Grizzly Bears"), 1, "the second cost was the Bears the seat named");
        Assert.assertEquals(menuSizes, java.util.Arrays.asList(3, 2),
                "each incident asked its own menu of the arms still armed");
        Assert.assertEquals(me.getLife(), 20, "both damages were prevented by the seat's picks");
        Assert.assertEquals(armedEffects(me), 1, "one arm armed remains");
        Assert.assertEquals(tokensLeft(me), 1, "two picks were spent: the two used arms left, one token remains");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    /** The number of this menu's options whose label names the CoP effect copies (disambiguation aside). */
    private static int menuOptions(String prompt, String needle) {
        int n = 0;
        for (String l : optionLabels(prompt)) if (l.contains(needle)) n++;
        return n;
    }

    /** Armed prevention effect tokens live in Command zone ("<card>'s Effect"). */
    private static int armedEffects(Player me) {
        int n = 0;
        for (Card c : me.getZone(ZoneType.Command)) if (c.getName().contains(CIRCLE)) n++;
        return n;
    }

    /** Zone changes copy the card object, so a created handle's zone check lies: the handle still
     *  points at the slot the object left. What moves is the NAME. Counts by name in the yard. */
    private static int inGraveyard(Player p, String name) {
        int n = 0;
        for (Card c : p.getZone(ZoneType.Graveyard)) if (c.getName().equals(name)) n++;
        return n;
    }

    /** The one effect token still owned: the spent arm leaves the game (its zone-change copy is not
     *  in any player zone), so across live zones exactly one remains. Only tokens count — not the
     *  enchantments themselves — so the name must still carry the token suffix. */
    private static int tokensLeft(Player me) {
        int n = 0;
        for (ZoneType z : new ZoneType[] {ZoneType.Command, ZoneType.Exile, ZoneType.Graveyard,
                ZoneType.Battlefield, ZoneType.Library, ZoneType.Hand})
            for (Card c : me.getZone(z))
                if (c.getName().contains(CIRCLE) && c.getName().contains("'s Effect")) n++;
        return n;
    }

    private static String decisionsTail(Script model) {
        return model.prompts.size() > 0 ? model.prompts.get(model.prompts.size() - 1) : "(no asks)";
    }

    // ------------------------------------------------------------------ 2. announceRequirements

    /** Pain's Reward: the starting bid of ANY number is announced by the seat; the top-bid invitation is
     *  a seat ask (the AI rival declines — its life is under the bid + 5); the announced number is the
     *  life gone and the winner drew four. */
    @Test
    public void theBidLifeStartIsAnnouncedAndPaidByTheSeat() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        for (int i = 0; i < 3; i++) card("Swamp", me, ZoneType.Battlefield);
        for (int i = 0; i < 4; i++) card("Plains", me, ZoneType.Library); // the winner's draw has real cards to pull
        Card reward = card("Pain's Reward", me, ZoneType.Hand);
        source(reward);
        rival.setLife(3, null);
        source(reward);
        int hand = me.getCardsIn(ZoneType.Hand).size();
        Script model = new Script("")
                .on("You have priority", choice("CAST Pain's Reward"))
                .on("Choose a starting bid", p -> "NUMBER: 3")
                .on("top bid", choice("NO (decline)"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, "Pain's Reward was not offered");
        Assert.assertEquals(r.get(0).getHostCard().getName(), "Pain's Reward", "the picked play");
        ctrl.playChosenSpellAbility(r.get(0));
        while (!g.getStack().isEmpty()) g.getStack().resolveStack();
        Assert.assertEquals(inGraveyard(me, "Pain's Reward"), 1, "the reward resolved");

        String ask = PromptFidelityTest.promptWith(model, "Choose a starting bid");
        Assert.assertTrue(ask.contains("Every integer from 0 through 2147483647"), "the open range was not stated:\n" + ask);
        Assert.assertTrue(model.asked("top bid"), "the seat was never invited to top its own bid");
        Assert.assertEquals(rival.getLife(), 3, "the rival bid nothing");
        Assert.assertEquals(me.getLife(), 17, "the announced bid (3) is exactly the life gone");
        Assert.assertEquals(me.getCardsIn(ZoneType.Hand).size(), hand + 3, "the spell left the hand, the winner drew the four");
        Assert.assertEquals(me.getCardsIn(ZoneType.Library).size(), 0, "the four drew out of the seat's library");
        Assert.assertEquals(rival.getCardsIn(ZoneType.Hand).size(), 0, "the loser drew nothing");
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0, "the seat's bid decisions were the seat's");
    }

    // ------------------------------------------------------------------ 3. chooseManaFromPool

    /** Bone Saw's {1} equip over a blue and a red float: the two colours tie on every weight rule the engine
     *  applies to a generic shard (colourless, converge, restricted, snow), so the seat names the mana.
     *  The floats pre-exist mid-payment as runs/c3/ant-storm-default T7 recorded them — the play menu offers
     *  no mana ability, so a two-coloured float set is the state the seat carries into a payment. */
    @Test
    public void aGenericShardOverTwoColoredFloatsNamesTheManaTheSeatSays() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card island = card("Island", me, ZoneType.Battlefield);
        Card mountain = card("Mountain", me, ZoneType.Battlefield);
        Card saw = card("Bone Saw", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        me.getManaPool().addManaNoEvent(new forge.game.mana.Mana(MagicColor.BLUE, island, null, me));
        me.getManaPool().addManaNoEvent(new forge.game.mana.Mana(MagicColor.RED, mountain, null, me));
        island.setTapped(true); mountain.setTapped(true);
        Assert.assertEquals(poolOf(me), "UR", "fixture: two coloured floats");
        List<String> poolAsks = new ArrayList<>();
        Script model = new Script("CHOICE: 1")
                .on("You have priority", choice("ACTIVATE Bone Saw"))
                .on("Choose the TARGET", choice("Grizzly Bears"))
                .on("MANA POOL", p -> { poolAsks.add(p); return "CHOICE: " + opt(p, "R (from Mountain)"); });
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        List<SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, "the equip was offered: " + decisionsTail(model));
        Assert.assertEquals(r.get(0).getHostCard().getName(), "Bone Saw", "the picked play");
        ctrl.playChosenSpellAbility(r.get(0));
        g.getStack().resolveStack();

        Assert.assertFalse(poolAsks.isEmpty(), "the pool tie was never asked");
        String ask = poolAsks.get(poolAsks.size() - 1); // the paying one (earlier asks are test-payments the engine refunds)
        String decision = ask.substring(ask.lastIndexOf("DECISION:"));
        Assert.assertTrue(optionLabels(ask).stream().anyMatch(l -> l.startsWith("U (from Island)"))
                        && optionLabels(ask).stream().anyMatch(l -> l.startsWith("R (from Mountain)")),
                "the two tied floats did not both show:\n" + decision);
        Assert.assertEquals(saw.getAttachedTo(), bears, "the saw resolved onto the creature");
        Assert.assertEquals(poolOf(me), "U", "the named float paid, the other still floats: " + me.getManaPool());
        boolean redSpent = false;
        for (forge.game.mana.Mana m : r.get(0).getPayingMana()) redSpent |= (m.getColor() & MagicColor.RED) != 0;
        Assert.assertTrue(redSpent, "the mana the seat named is the one used: " + r.get(0).getPayingMana());
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    private static String poolOf(Player me) {
        StringBuilder b = new StringBuilder();
        for (forge.game.mana.Mana m : me.getManaPool()) b.append(MagicColor.toShortString(m.getColor()));
        return b.toString();
    }

    // ------------------------------------------------------------------ 4. playTrigger

    /** Snowfall: whenever an Island of its controller is tapped for mana, its controller MAY add an extra
     *  {U}. The seat says PLAY. */
    @Test
    public void snowfallsOptionalTriggerPlaysWhenTheSeatSays() {
        snowfallDirections(true);
    }

    /** The same seat says SKIP: the trigger stays down and only the tapped Island's own mana enters the
     *  pool (the harness used to play every optional static it was handed, without asking). */
    @Test
    public void snowfallsOptionalTriggerStaysDownWhenTheSeatDeclines() {
        snowfallDirections(false);
    }

    private void snowfallDirections(boolean accept) {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card snowfall = card("Snowfall", me, ZoneType.Battlefield);
        Card island = card("Island", me, ZoneType.Battlefield);
        // The engine registers a card's active triggers on zone changes and phase steps; the bare
        // fixture add does neither, so reset exactly what a phase step does before the mana ability.
        g.getTriggerHandler().resetActiveTriggers();
        source(snowfall);
        Script model = new Script("CHOICE: 1")
                .on("OPTIONAL TRIGGER", choice(accept ? "YES (accept)" : "NO (decline)"));
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);

        SpellAbility mana = island.getManaAbilities().iterator().next();
        mana.setActivatingPlayer(me);
        ctrl.playSpellAbilityNoStack(mana, true);
        Assert.assertTrue(model.asked("Accept this optional triggered ability"),
                "the may was asked: " + decisionsTail(model));
        String ask = PromptFidelityTest.promptWith(model, "Accept this optional triggered ability");
        Assert.assertTrue(ask.contains("Whenever an Island is tapped for mana"), "the trigger's own text was missing:\n" + ask);
        boolean snowfallMana = false;
        for (forge.game.mana.Mana m : me.getManaPool())
            if (m.getSourceCard() != null && m.getSourceCard().getName().equals("Snowfall")) snowfallMana = true;
        Assert.assertEquals(snowfallMana, accept, "the extra {U} the seat " + (accept ? "declined" : "played"));
        Assert.assertEquals(me.getManaPool().totalMana(), accept ? 2 : 1, "the pool: " + poolOf(me));
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
    }

    // ------------------------------------------------------------------ the gate

    /** Runs last: a model has answered every surface hook this class owns through the live controller. */
    @Test(priority = 1000)
    public void theSurfaceHooksAreAskedAndAnsweredByTheModel() {
        Set<String> missing = new TreeSet<>(HOOKS);
        missing.removeAll(ANSWERED);
        Assert.assertTrue(missing.isEmpty(),
                "surface hooks no test here drives through the live controller: " + missing);
    }
}
