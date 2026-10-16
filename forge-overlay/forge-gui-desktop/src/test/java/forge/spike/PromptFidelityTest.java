package forge.spike;

import forge.game.Game;
import forge.game.card.Card;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.zone.ZoneType;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import static forge.spike.LineFindingProbeTest.Recorder;
import static forge.spike.LineFindingProbeTest.bareGame;
import static forge.spike.LineFindingProbeTest.card;
import static forge.spike.LineFindingProbeTest.seat;

/**
 * What a decision is asked WITH, under the faithful profile: each test is a case where the prompt
 * left out what the decision turned on (runs/loop g01-g07), checked against the live controller.
 */
public class PromptFidelityTest {
    @BeforeClass
    public void init() { new LineFindingProbeTest().initializeModel(); }

    /** Every prompt any scenario in this class asks, checked against the ask contract after it. */
    private final java.util.List<String> asked = java.util.Collections.synchronizedList(new java.util.ArrayList<>());

    @BeforeMethod
    public void faithful() {
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL;
        asked.clear();
        LlmSpikeGui.PlayerControllerLLM.TEST_ASK_TAP = asked::add;
    }

    @AfterMethod(alwaysRun = true)
    public void legacy() {
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null;
        LlmSpikeGui.PlayerControllerLLM.TEST_ASK_TAP = null;
    }

    /** docs/STANDARDS.md section 8, per decision type: every ask any scenario here makes (play, X,
     *  modes, targets, costs, numbers, votes, attack targets, triggers, mana, blocks...) keeps the ask
     *  contract (AskContract). A scenario that adds an ask adds it here. */
    @AfterMethod(alwaysRun = true)
    public void everyAskKeepsTheContract(org.testng.ITestResult result) {
        if (result.isSuccess()) AskContract.check(result.getName(), asked);
    }

    static String decisions(Recorder r) {
        StringBuilder b = new StringBuilder();
        for (String p : r.prompts) {
            int i = p.lastIndexOf("DECISION:");
            b.append(i < 0 ? "(no DECISION)" : p.substring(i, Math.min(p.length(), i + 300))).append("\n---\n");
        }
        return b.toString();
    }

    static String promptWith(Recorder r, String needle) {
        for (String p : r.prompts) if (p.contains(needle)) return p;
        Assert.fail("no prompt contains " + needle + ": " + r.prompts);
        return null;
    }

    /** runs/loop/g07: eight creatures, six the seat's, none marked, and it untapped the opponent's. */
    @Test
    public void targetOptionsSayWhoseObjectEachIs() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Mountain", me, ZoneType.Battlefield);
        card("Lightning Bolt", me, ZoneType.Hand);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Savannah Lions", rival, ZoneType.Battlefield);
        Recorder smart = new Recorder("CHOICE: 1"), fast = new Recorder("CHOICE: 1");
        seat(g, smart, fast).chooseSpellAbilityToPlay();
        Recorder asked = smart.asked("DECISION: Choose the TARGET") ? smart : fast;
        String p = promptWith(asked, "DECISION: Choose the TARGET");
        Assert.assertTrue(p.matches("(?s).*Grizzly Bears[^\\n]*\\[Battlefield, yours\\].*"), p);
        Assert.assertTrue(p.matches("(?s).*Savannah Lions[^\\n]*\\[Battlefield, opponent's\\].*"), p);
        Assert.assertTrue(p.contains("you (Seat)") && p.contains("opponent (Rival)"), p);
    }

    /** The fast tier was told "NO reasoning" by its system prompt and "Reason briefly" by every decision. */
    @Test
    public void theFastTierIsNotToldNotToReason() {
        Assert.assertFalse(LlmSpikeGui.PlayerControllerLLM.framing("Seat", "", true, false).contains("NO reasoning"));
        LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null;
        Assert.assertTrue(LlmSpikeGui.PlayerControllerLLM.framing("Seat", "", true, false).contains("NO reasoning"),
                "legacy profiles keep their bytes");
    }

    /** Sneak Attack's INSPECT text was cut at 90 characters, before "sacrifice the creature". */
    @Test
    public void inspectReturnsTheWholeRulesText() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Sneak Attack", me, ZoneType.Hand);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        String r = ctrl.observer().inspect("Sneak Attack");
        Assert.assertTrue(r.contains("Sacrifice the creature at the beginning of the next end step"), r);
    }

    /** runs/compare two-tier seed 82: INSPECT "Cavern of Souls @11" (an option copied as read) was
     *  told no such card exists; the handle names the object. */
    @Test
    public void inspectOfANameWithItsHandleFindsTheObject() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card cavern = card("Cavern of Souls", me, ZoneType.Battlefield);
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        String ref = ctrl.observer().shortRef(cavern);
        String r = ctrl.observer().inspect(ref);
        Assert.assertTrue(r.contains("(current)") && r.contains("Cavern of Souls"), ref + " -> " + r);
    }

    /** "Choose a number [Galvanic Discharge]" did not say what the number does. */
    @Test
    public void aNumberIsAskedWithTheSourcesRulesText() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card gd = card("Galvanic Discharge", me, ZoneType.Hand);
        Recorder smart = new Recorder("CHOICE: 2"), fast = new Recorder("CHOICE: 2");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, smart, fast);
        int v = ctrl.chooseNumber(gd.getFirstSpellAbility(), "amount of energy to pay", 0, 3);
        Assert.assertEquals(v, 1);
        String p = promptWith(fast, "amount of energy to pay");
        Assert.assertTrue(p.contains("[Galvanic Discharge: Choose target creature or planeswalker."), p);
    }

    /** runs/loop/g07: the reason for a play did not reach the ask for its target. */
    @Test
    public void theTargetAskCarriesTheReasonForThePlay() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Mountain", me, ZoneType.Battlefield);
        card("Lightning Bolt", me, ZoneType.Hand);
        card("Savannah Lions", rival, ZoneType.Battlefield);
        Recorder smart = new Recorder("Bolt the Lions before they attack.\nCHOICE: 1"), fast = new Recorder("CHOICE: 1");
        seat(g, smart, fast).chooseSpellAbilityToPlay();
        Recorder asked = smart.asked("DECISION: Choose the TARGET") ? smart : fast;
        String p = promptWith(asked, "DECISION: Choose the TARGET");
        Assert.assertTrue(p.contains("THE PLAY YOU CHOSE MOST RECENTLY (T"), p);
        Assert.assertTrue(p.contains("Bolt the Lions before they attack."), p);
        Assert.assertTrue(p.indexOf("THE PLAY YOU CHOSE") < p.indexOf("DECISION: Choose the TARGET"), "shown before the decision");
        Assert.assertFalse(promptWith(asked, "DECISION: You have priority").contains("THE PLAY YOU CHOSE"),
                "the play menu is where a new play is chosen, so it does not carry the old one");
    }

    /** Issue #108: under PLAN_EXECUTE the harness casts a step of the model's STEPS list without an ask,
     *  and the target ask for that step carried the PREVIOUS play and its reason as "the play you chose
     *  most recently": the Shock's target was asked under "cast Lightning Bolt ... Bolt the Lions". */
    @Test
    public void aPlanExecutedStepsTargetAskCarriesThatStep() {
        boolean was = LlmSpikeGui.PlayerControllerLLM.PLAN_EXECUTE;
        LlmSpikeGui.PlayerControllerLLM.PLAN_EXECUTE = true;
        try {
            Game g = bareGame(PhaseType.MAIN1);
            Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
            card("Mountain", me, ZoneType.Battlefield);
            card("Mountain", me, ZoneType.Battlefield);
            Card bolt = card("Lightning Bolt", me, ZoneType.Hand);
            card("Shock", me, ZoneType.Hand);
            card("Savannah Lions", rival, ZoneType.Battlefield);
            card("Grizzly Bears", rival, ZoneType.Battlefield);
            Recorder smart = new Recorder("Bolt the Lions first.\nPLAN: burn both of their creatures\nSTEPS: Lightning Bolt, Shock\nCHOICE: 1"),
                    fast = new Recorder("CHOICE: 1");
            LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, smart, fast);
            Assert.assertEquals(ctrl.chooseSpellAbilityToPlay().get(0).getHostCard(), bolt, "fixture: the model chose the Bolt");
            g.getAction().moveToGraveyard(bolt, null); // it resolved; the plan's next step is the Shock
            smart.prompts.clear();
            fast.prompts.clear();

            java.util.List<forge.game.spellability.SpellAbility> next = ctrl.chooseSpellAbilityToPlay();
            Assert.assertEquals(next.get(0).getHostCard().getName(), "Shock", "fixture: the plan step was not executed");
            Assert.assertFalse(smart.asked("DECISION: You have priority") || fast.asked("DECISION: You have priority"),
                    "fixture: a plan-executed step asks no play menu");
            Recorder asked = smart.asked("DECISION: Choose the TARGET") ? smart : fast;
            String p = promptWith(asked, "DECISION: Choose the TARGET");
            String carried = p.substring(p.indexOf("THE PLAY YOU CHOSE"), p.lastIndexOf("DECISION:"));
            Assert.assertTrue(carried.contains("Shock"), carried);
            Assert.assertFalse(carried.contains("Bolt the Lions first."), "the Bolt's reason was carried to the Shock: " + carried);
            Assert.assertTrue(carried.contains("burn both of their creatures"), "the plan the step belongs to: " + carried);
        } finally {
            LlmSpikeGui.PlayerControllerLLM.PLAN_EXECUTE = was;
        }
    }

    /** runs/loop/g06 and g07: at the target step the model no longer wanted the play and had no way
     *  back ("there's no 'don't activate' option"). Cancelling re-asks the menu, which says so. */
    @Test
    public void cancellingAtTheTargetStepAsksTheMenuAgain() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Mountain", me, ZoneType.Battlefield);
        card("Lightning Bolt", me, ZoneType.Hand);
        card("Savannah Lions", rival, ZoneType.Battlefield);
        Recorder model = new Recorder("CHOICE: 1") {
            @Override public String ask(String prompt) {
                prompts.add(prompt);
                if (prompt.contains("DECISION: Choose the TARGET")) {
                    java.util.regex.Matcher m = java.util.regex.Pattern.compile("(?m)^\\s*(\\d+)\\) CANCEL:").matcher(prompt);
                    Assert.assertTrue(m.find(), "the target ask offers cancel: " + prompt);
                    return "I no longer want this.\nCHOICE: " + m.group(1);
                }
                return prompt.contains("NOTE: you chose") ? "CHOICE: 2" : "CHOICE: 1"; // then PASS
            }
        };
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        Assert.assertNull(ctrl.chooseSpellAbilityToPlay(), "after cancelling, the model passed");
        Assert.assertTrue(model.asked("NOTE: you chose CAST Lightning Bolt"), decisions(model));
        Assert.assertTrue(me.getCardsIn(ZoneType.Hand).size() == 1, "nothing was cast");
    }

    /** A model answering a cost ask with cancel: nothing is paid, the play is not made, and the menu
     *  is asked again with a note saying so. */
    private static void cancelsAtTheCost(Game g, String costAsk, String play) {
        Recorder model = new Recorder("CHOICE: 1") {
            @Override public String ask(String prompt) {
                prompts.add(prompt);
                if (prompt.contains(costAsk)) {
                    java.util.regex.Matcher m = java.util.regex.Pattern.compile("(?m)^\\s*(\\d+)\\) CANCEL this play").matcher(prompt);
                    Assert.assertTrue(m.find(), "the cost ask offers cancel: " + prompt);
                    return "Not worth it after all.\nPICKS: " + m.group(1);
                }
                if (prompt.contains("DECISION: You have priority"))
                    return prompt.contains("NOTE: you chose") ? "CHOICE: " + passIndex(prompt) : "CHOICE: " + optionIndex(prompt, play);
                return "CHOICE: 1";
            }
        };
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        java.util.List<forge.game.spellability.SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, decisions(model));
        Assert.assertTrue(ctrl.playChosenSpellAbility(r.get(0)));
        Assert.assertTrue(model.asked(costAsk), decisions(model));
        Assert.assertTrue(g.getStack().isEmpty(), "nothing was put on the stack");
        Assert.assertNull(ctrl.chooseSpellAbilityToPlay(), "after cancelling, the model passed");
        Assert.assertTrue(model.asked("NOTE: you chose " + play), decisions(model));
        Assert.assertTrue(promptWith(model, "NOTE: you chose " + play).contains("when asked to pay its cost"), decisions(model));
    }

    private static int optionIndex(String prompt, String label) {
        java.util.regex.Matcher m = java.util.regex.Pattern.compile("(?m)^\\s*(\\d+)\\) " + java.util.regex.Pattern.quote(label)).matcher(prompt);
        Assert.assertTrue(m.find(), "the menu offers " + label + ": " + prompt);
        return Integer.parseInt(m.group(1));
    }

    private static int passIndex(String prompt) {
        java.util.regex.Matcher m = java.util.regex.Pattern.compile("(?m)^\\s*(\\d+)\\) PASS").matcher(prompt);
        Assert.assertTrue(m.find(), "the menu offers PASS: " + prompt);
        return Integer.parseInt(m.group(1));
    }

    /** CostPayment decides every cost part before paying any, so the sacrifice a cost takes is asked
     *  while the play can still be cancelled, as its target is. */
    @Test
    public void cancellingAtTheSacrificeCostAsksTheMenuAgain() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card bombardment = card("Goblin Bombardment", me, ZoneType.Battlefield);
        Card lions = card("Savannah Lions", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        card("Savannah Lions", rival, ZoneType.Battlefield);
        cancelsAtTheCost(g, "you must SACRIFICE", "ACTIVATE Goblin Bombardment");
        Assert.assertTrue(lions.isInPlay() && bears.isInPlay() && bombardment.isInPlay(), "nothing was sacrificed");
    }

    /** The same for a discard cost, asked through LlmCostDecision's card-choosing route. */
    @Test
    public void cancellingAtTheDiscardCostAsksTheMenuAgain() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Forest", me, ZoneType.Battlefield);
        card("Survival of the Fittest", me, ZoneType.Battlefield);
        card("Llanowar Elves", me, ZoneType.Hand);
        card("Grizzly Bears", me, ZoneType.Hand);
        card("Elvish Mystic", me, ZoneType.Library);
        cancelsAtTheCost(g, "you must DISCARD", "ACTIVATE Survival of the Fittest");
        Assert.assertEquals(me.getCardsIn(ZoneType.Hand).size(), 2, "nothing was discarded");
        Assert.assertTrue(me.getCardsIn(ZoneType.Battlefield).anyMatch(c -> c.getName().equals("Forest") && !c.isTapped()),
                "the Forest was not tapped");
    }

    /** Forge's payment paid a Phyrexian symbol with mana whenever it could; how it is paid is the
     *  seat's, and only splits it can pay are offered. */
    private static Player castsProbePaying(String answer) {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Island", me, ZoneType.Battlefield);
        card("Gitaxian Probe", me, ZoneType.Hand);
        Recorder model = new Recorder("CHOICE: 1") {
            @Override public String ask(String prompt) {
                prompts.add(prompt);
                if (prompt.contains("Each Phyrexian symbol")) {
                    Assert.assertTrue(prompt.contains("with mana (no life)") && prompt.contains("with 2 life"), prompt);
                    return "CHOICE: " + optionIndex(prompt, answer);
                }
                return "CHOICE: 1";
            }
        };
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        java.util.List<forge.game.spellability.SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, decisions(model));
        Assert.assertTrue(ctrl.playChosenSpellAbility(r.get(0)));
        Assert.assertTrue(model.asked("Each Phyrexian symbol"), decisions(model));
        Assert.assertFalse(g.getStack().isEmpty(), "Gitaxian Probe was cast");
        return me;
    }

    @Test
    public void phyrexianManaPaidWithLifeIsTheSeatsChoice() {
        Player me = castsProbePaying("pay 1 Phyrexian symbol with 2 life");
        Assert.assertEquals(me.getLife(), 18);
        Assert.assertTrue(me.getCardsIn(ZoneType.Battlefield).anyMatch(c -> c.getName().equals("Island") && !c.isTapped()), "the Island stayed untapped");
    }

    @Test
    public void phyrexianManaPaidWithManaIsTheSeatsChoice() {
        Player me = castsProbePaying("pay every Phyrexian symbol with mana");
        Assert.assertEquals(me.getLife(), 20);
        Assert.assertTrue(me.getCardsIn(ZoneType.Battlefield).anyMatch(c -> c.getName().equals("Island") && c.isTapped()), "the Island paid");
    }

    @Test
    public void withPhyrexianDropsTheSymbolsPaidWithLife() {
        forge.card.mana.ManaCost c = new forge.card.mana.ManaCost("1 B/P B/P");
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.withPhyrexian(c, 1, false).getShortString(), new forge.card.mana.ManaCost("1 B/P").getShortString());
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.withPhyrexian(c, 0, true).getShortString(), new forge.card.mana.ManaCost("1 B B").getShortString());
        Assert.assertTrue(LlmSpikeGui.PlayerControllerLLM.withPhyrexian(new forge.card.mana.ManaCost("U/P"), 1, false).isZero());
    }

    /** NO_COACHING is an assistance condition: the system prompt loses the harness's advice
     *  (PRINCIPLES) and the static reference, and keeps the protocol and the answer format. */
    @Test
    public void withoutCoachingTheSystemPromptHasNoPrinciplesOrReference() {
        String coached = LlmSpikeGui.PlayerControllerLLM.framing("Seat", null, false, true, true);
        String bare = LlmSpikeGui.PlayerControllerLLM.framing("Seat", null, false, true, false);
        // Faithful (harness-epoch-8): no advice in either; NO_COACHING also drops the Legacy reference. Both
        // keep how the interface works and how to read the state, which are information, not coaching.
        Assert.assertTrue(coached.contains("<legacy_reference>"), coached);
        Assert.assertFalse(bare.contains("<legacy_reference>"), bare);
        for (String p : new String[] {coached, bare}) {
            Assert.assertFalse(p.contains("PRINCIPLES") || p.contains("WORKED EXAMPLES"), p);
            Assert.assertTrue(p.contains("<how_this_works>") && p.contains(SeatObservation.GUIDE), p);
        }
    }

    /** BLOCKS pairs were split on every comma, so a legendary name ("Ragavan, Nimble Pilferer")
     *  broke its pair and the block was dropped without a trace. */
    @Test
    public void aBlockNamingACardWithACommaIsUsed() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card thalia = card("Thalia, Guardian of Thraben", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        Card ragavan = card("Ragavan, Nimble Pilferer", rival, ZoneType.Battlefield);
        Card lions = card("Savannah Lions", rival, ZoneType.Battlefield);
        Recorder model = new Recorder("BLOCKS: Thalia, Guardian of Thraben->Ragavan, Nimble Pilferer, Grizzly Bears->Savannah Lions");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        forge.game.combat.Combat combat = new forge.game.combat.Combat(rival);
        combat.addAttacker(ragavan, me);
        combat.addAttacker(lions, me);
        ctrl.declareBlockers(me, combat);
        Assert.assertTrue(combat.getBlockers(ragavan).contains(thalia), "Thalia blocks Ragavan: " + combat.getBlockers(ragavan));
        Assert.assertTrue(combat.getBlockers(lions).contains(bears), "Bears block Lions: " + combat.getBlockers(lions));
    }

    /** A reply with no BLOCKS line was read as "no blocks"; it is asked once more, whole. */
    @Test
    public void aBlockReplyWithNoBlocksLineIsAskedAgain() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_BLOCKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card bears = card("Grizzly Bears", me, ZoneType.Battlefield);
        Card lions = card("Savannah Lions", rival, ZoneType.Battlefield);
        Recorder model = new Recorder("x") {
            @Override public String ask(String prompt) {
                prompts.add(prompt);
                return prompts.size() == 1 ? "I will block the Lions with the Bears." : "BLOCKS: Grizzly Bears->Savannah Lions";
            }
        };
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        forge.game.combat.Combat combat = new forge.game.combat.Combat(rival);
        combat.addAttacker(lions, me);
        ctrl.declareBlockers(me, combat);
        Assert.assertEquals(model.prompts.size(), 2);
        Assert.assertTrue(combat.getBlockers(lions).contains(bears));
    }

    /** The engine tells a player what was chosen (a named card, a colour). A human is shown it; the
     *  seat was not, so a Meddling Mage's name reached the model only if the game log said it. */
    @Test
    public void aNoticeOfAChosenValueReachesTheSeatsPrompt() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        card("Lightning Bolt", me, ZoneType.Hand);
        Recorder model = new Recorder("CHOICE: 2");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        ctrl.notifyOfValue(null, null, "Brainstorm");
        ctrl.chooseSpellAbilityToPlay();
        String p = promptWith(model, "DECISION: You have priority");
        Assert.assertTrue(p.contains("[notice to you]") && p.contains("Brainstorm"), p);
    }

    /** Green Sun's Zenith resolved at X=1..3 in recorded games with no question: the AI cast path
     *  never announces X. The seat now asks it, up to what it can pay, before targets. */
    @Test
    public void xIsTheSeatsChoiceUpToWhatItCanPay() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        for (int i = 0; i < 3; i++) card("Forest", me, ZoneType.Battlefield);
        card("Green Sun's Zenith", me, ZoneType.Hand);
        card("Llanowar Elves", me, ZoneType.Library);
        Recorder model = new Recorder("CHOICE: 1") {
            @Override public String ask(String prompt) {
                prompts.add(prompt);
                return prompt.contains("DECISION: Choose X for") ? "CHOICE: 3" : "CHOICE: 1";
            }
        };
        java.util.List<forge.game.spellability.SpellAbility> r = seat(g, model, model).chooseSpellAbilityToPlay();
        Assert.assertNotNull(r);
        String p = promptWith(model, "DECISION: Choose X for CAST Green Sun's Zenith");
        Assert.assertTrue(p.contains("(you can pay for X up to 2)") && p.contains("3) 2"), p);
        Assert.assertEquals(r.get(0).getXManaCostPaid(), Integer.valueOf(2));
    }

    /** A modal spell's modes are chosen before its targets, so every chosen targeted mode gets one.
     *  Modes used to be chosen at cast, after targeting: "Couldn't add to stack, failed to target". */
    @Test
    public void everyChosenModeOfAModalSpellIsTargeted() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Badlands", me, ZoneType.Battlefield);
        card("Badlands", me, ZoneType.Battlefield);
        card("Mountain", me, ZoneType.Battlefield);
        card("Kolaghan's Command", me, ZoneType.Hand);
        card("Savannah Lions", rival, ZoneType.Battlefield);
        card("Grizzly Bears", rival, ZoneType.Hand);
        Recorder model = new Recorder("CHOICE: 1") {
            @Override public String ask(String prompt) {
                prompts.add(prompt);
                return prompt.contains("PICKS:") && !prompt.contains("DECISION: You have priority") ? "PICKS: 2,4" : "CHOICE: 1";
            }
        };
        java.util.List<forge.game.spellability.SpellAbility> r = seat(g, model, model).chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, decisions(model));
        int targeted = 0;
        for (forge.game.spellability.SpellAbility part = r.get(0); part != null; part = part.getSubAbility()) {
            if (!part.usesTargeting()) continue;
            Assert.assertFalse(part.getTargets().isEmpty(), "mode without a target: " + part + "\n" + decisions(model));
            targeted++;
        }
        Assert.assertEquals(targeted, 2, decisions(model));
    }

    /** Flametongue Kavu enters: its "4 damage to target creature" trigger is targeted by the seat.
     *  The inherited orderAndPlaySimultaneousSa had Forge AI's doTrigger choose every trigger target. */
    @Test
    public void aTriggersTargetIsTheSeatsChoice() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card lions = card("Savannah Lions", rival, ZoneType.Battlefield);
        card("Grizzly Bears", rival, ZoneType.Battlefield);
        Card ftk = card("Flametongue Kavu", me, ZoneType.Hand);
        Recorder model = new Recorder("CHOICE: 1") {
            @Override public String ask(String prompt) {
                prompts.add(prompt);
                java.util.regex.Matcher m = java.util.regex.Pattern.compile("(?m)^\\s*(\\d+)\\) Savannah Lions").matcher(prompt);
                return prompt.contains("DECISION: Choose the TARGET") && m.find() ? "CHOICE: " + m.group(1) : "CHOICE: 1";
            }
        };
        seat(g, model, model);
        g.getAction().moveToPlay(ftk, null, null);
        g.getTriggerHandler().runWaitingTriggers();
        g.getStack().addAllTriggeredAbilitiesToStack();
        Assert.assertTrue(model.asked("DECISION: Choose the TARGET for"), decisions(model));
        Assert.assertFalse(promptWith(model, "DECISION: Choose the TARGET for").matches("(?s).*\\(\\d+\\)\\].*"),
                "an engine card id leaked into the trigger's label (runs/loop/g08: [Zone Changer: ... (37)])");
        forge.game.spellability.SpellAbility top = g.getStack().peekAbility();
        Assert.assertNotNull(top, "the trigger is on the stack");
        Assert.assertTrue(top.getTargets().contains(lions), "the seat's target was used: " + top.getTargets());
    }

    /** Every attacker went at the first defending player; a planeswalker could never be attacked. */
    @Test
    public void anAttackerCanAttackAPlaneswalker() {
        Game g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        card("Grizzly Bears", me, ZoneType.Battlefield);
        Card jace = card("Jace, the Mind Sculptor", rival, ZoneType.Battlefield);
        Recorder model = new Recorder("PICKS: 1") {
            @Override public String ask(String prompt) {
                prompts.add(prompt);
                if (!prompt.contains("DECISION: Where does")) return "PICKS: 1";
                java.util.regex.Matcher m = java.util.regex.Pattern.compile("(?m)^\\s*(\\d+)\\) Jace").matcher(prompt);
                return m.find() ? "CHOICE: " + m.group(1) : "CHOICE: 1";
            }
        };
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        forge.game.combat.Combat combat = new forge.game.combat.Combat(me);
        ctrl.declareAttackers(me, combat);
        Assert.assertEquals(combat.getAttackers().size(), 1, decisions(model));
        Assert.assertEquals(combat.getDefenderByAttacker(combat.getAttackers().get(0)), jace, decisions(model));
    }

    /** Survival of the Fittest's "discard a creature card" was chosen by Forge AI's getCardsToDiscard. */
    @Test
    public void aDiscardCostIsTheSeatsChoice() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Forest", me, ZoneType.Battlefield);
        card("Survival of the Fittest", me, ZoneType.Battlefield);
        Card bears = card("Grizzly Bears", me, ZoneType.Hand);
        card("Savannah Lions", me, ZoneType.Hand);
        card("Llanowar Elves", me, ZoneType.Library);
        Recorder model = new Recorder("CHOICE: 1") {
            @Override public String ask(String prompt) {
                prompts.add(prompt);
                if (prompt.contains("you must DISCARD")) {
                    java.util.regex.Matcher m = java.util.regex.Pattern.compile("(?m)^\\s*(\\d+)\\) Grizzly Bears").matcher(prompt);
                    return m.find() ? "PICKS: " + m.group(1) : "PICKS: 1";
                }
                return "CHOICE: 1";
            }
        };
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        java.util.List<forge.game.spellability.SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, decisions(model));
        ctrl.playChosenSpellAbility(r.get(0));
        Assert.assertTrue(model.asked("you must DISCARD 1"), decisions(model));
        Assert.assertTrue(me.getCardsIn(ZoneType.Graveyard).contains(bears), "the seat's pick was discarded");
    }

    /** Council's Judgment's vote was ComputerUtil.vote's. */
    @Test
    public void aVoteIsTheSeatsChoice() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card cj = card("Council's Judgment", me, ZoneType.Hand);
        Card a = card("Savannah Lions", rival, ZoneType.Battlefield);
        Card b = card("Grizzly Bears", rival, ZoneType.Battlefield);
        Recorder model = new Recorder("CHOICE: 2");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        java.util.List<Object> opts = new java.util.ArrayList<>(java.util.Arrays.asList(a, b));
        Object v = ctrl.vote(cj.getFirstSpellAbility(), "Vote for a nonland permanent", opts,
                com.google.common.collect.ArrayListMultimap.create(), me, false);
        Assert.assertEquals(v, b);
        Assert.assertTrue(model.asked("DECISION: VOTE [Council's Judgment"), decisions(model));
    }

    /** Forge's payment chose the lands. Keeping Volcanic Island up (blue for Daze) taps the Mountain. */
    @Test
    public void theSeatChoosesWhichManaSourcesStayUntapped() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card volc = card("Volcanic Island", me, ZoneType.Battlefield);
        Card mountain = card("Mountain", me, ZoneType.Battlefield);
        card("Lightning Bolt", me, ZoneType.Hand);
        Recorder model = new Recorder("CHOICE: 1") {
            @Override public String ask(String prompt) {
                prompts.add(prompt);
                if (prompt.contains("DECISION: MANA:")) {
                    java.util.regex.Matcher m = java.util.regex.Pattern.compile("(?m)^\\s*(\\d+)\\) Volcanic Island").matcher(prompt);
                    return m.find() ? "PICKS: " + m.group(1) : "PICKS: NONE";
                }
                if (prompt.contains("DECISION: Choose the TARGET")) {
                    java.util.regex.Matcher m = java.util.regex.Pattern.compile("(?m)^\\s*(\\d+)\\) opponent").matcher(prompt);
                    return m.find() ? "CHOICE: " + m.group(1) : "CHOICE: 1";
                }
                return "CHOICE: 1";
            }
        };
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        java.util.List<forge.game.spellability.SpellAbility> r = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(r, decisions(model));
        ctrl.playChosenSpellAbility(r.get(0));
        Assert.assertTrue(model.asked("DECISION: MANA: you are paying"), decisions(model));
        Assert.assertFalse(volc.isTapped(), "the source the seat kept is untapped");
        Assert.assertTrue(mountain.isTapped(), "the other source paid");
    }

    /** runs/luna-std/g6, T15: the command zone listed "The Wondrous Wasp (53)'s Effect": Forge names an effect
     *  object after its source with the source's engine id inside the name. */
    @Test
    public void anEffectsNameCarriesNoEngineId() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card wasp = card("Lightning Bolt", me, ZoneType.Battlefield);
        wasp.setName("The Wondrous Wasp");
        Card effect = card("Lightning Bolt", me, ZoneType.Command);
        effect.setName(wasp + "'s Effect");                  // how Forge names it: "The Wondrous Wasp (N)'s Effect"
        effect.setEffectSource(wasp);
        Assert.assertTrue(effect.getName().matches("The Wondrous Wasp \\(\\d+\\)'s Effect"), effect.getName());
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, new Recorder("CHOICE: 1"), new Recorder("CHOICE: 1"));
        String ref = ctrl.observer().shortRef(effect);
        Assert.assertFalse(ref.contains("(53)"), "an engine id in a name: " + ref);
        Assert.assertTrue(ref.contains("The Wondrous Wasp's Effect"), ref);
    }

    /** runs/luna-modern/g11, T2: a question's title read "[Devourer of Destiny (48)'s Effect]": the questions named
     *  cards with the engine's raw name, not the observation's. Every card name in a question now goes through one
     *  function, which drops the id and reveals nothing (a face-down card keeps its empty name). */
    @Test
    public void aQuestionNamesAnEffectWithoutItsEngineId() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        Card devourer = card("Devourer of Destiny", me, ZoneType.Battlefield);
        Card effect = card("Lightning Bolt", me, ZoneType.Command);
        effect.setName(devourer + "'s Effect");
        effect.setEffectSource(devourer);
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.safeName(effect), "Devourer of Destiny's Effect");
        Card morph = card("Exalted Angel", rival, ZoneType.Battlefield);
        morph.turnFaceDown(true);
        Assert.assertFalse(LlmSpikeGui.PlayerControllerLLM.safeName(morph).contains("Exalted"),
                "a face-down card's name was revealed");
    }

    /** runs/luna-modern/g11, T6: the seat announced X = 1 for Kozilek's Command, then chose its modes "as X is 0":
     *  the mode and target questions did not say what X was. They do once it is announced. */
    @Test
    public void theModeAndTargetQuestionsSayTheAnnouncedX() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        forge.game.spellability.SpellAbility cmd = card("Kozilek's Command", me, ZoneType.Hand).getFirstSpellAbility();
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.xNote(cmd), "", "nothing before X is announced");
        cmd.setXManaCostPaid(1);
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.xNote(cmd), " (you announced X = 1)");
        forge.game.spellability.SpellAbility bolt = card("Lightning Bolt", me, ZoneType.Hand).getFirstSpellAbility();
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.xNote(bolt), "", "no X in the cost");
    }

    /** runs/luna-v9/g5, T7: the stack read "Aether Vial (4) - You may put a creature card ...", the engine id 4
     *  unredacted, and the seat answered "Vial is at four counters" with three on it. Card text also showed a
     *  literal "\n" ("...on Aether Vial.\n{T}: ..."). */
    @Test
    public void theStackShowsNoEngineIdAndCardTextNoEscapes() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        Card vial = card("Aether Vial", me, ZoneType.Battlefield);
        vial.addCounterInternal(forge.game.card.CounterEnumType.CHARGE, 3, me, false, null, null);
        forge.game.spellability.SpellAbility tap = null;
        for (forge.game.spellability.SpellAbility sa : vial.getSpellAbilities()) if (!sa.isTrigger() && sa.getPayCosts() != null && sa.getPayCosts().hasTapCost()) tap = sa;
        Assert.assertNotNull(tap, "Aether Vial's {T} ability");
        tap.setActivatingPlayer(me);
        g.getStack().add(tap);
        String raw = g.getStack().peek().getStackDescription();
        Assert.assertTrue(raw.contains("(" + vial.getId() + ")"), "the engine's own text carries the id: " + raw);
        Recorder model = new Recorder("CHOICE: 1");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        String stack = ctrl.observer().stack();
        Assert.assertFalse(stack.matches("(?s).*Aether Vial \\(\\d+\\).*"), "an engine id in the stack: " + stack);
        Assert.assertTrue(stack.contains("Aether Vial @"), "the id became the seat's handle: " + stack);
        Assert.assertFalse(LlmSpikeGui.flat("on Aether Vial.\\n{T}: You may").contains("\\n"));
    }

    /** runs/luna-v8/g2: the seat kept Chain Lightning on top with a surveil, and the next prompt said nothing about
     *  its library; only a history of 53 "engine presented ... for a choice" lines. A player knows what it put on
     *  top: the state shows it, drops a drawn card, follows a Ponder's new order and forgets it all on a shuffle. */
    @Test
    public void theSeatKnowsWhatItPutOnTopOfItsLibrary() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Chain Lightning", me, ZoneType.Library);   // index 0 is the top
        card("Island", me, ZoneType.Library);
        card("Lightning Bolt", me, ZoneType.Library);
        card("Mountain", me, ZoneType.Library);
        Card ponder = card("Ponder", me, ZoneType.Hand);
        Recorder model = new Recorder("PICKS: 1");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        SeatObservation obs = ctrl.observer();
        Assert.assertEquals(obs.ownTopText(), "", "nothing is known before the seat looks");

        forge.game.card.CardCollection top1 = new forge.game.card.CardCollection(me.getCardsIn(ZoneType.Library).get(0));
        ctrl.arrangeForSurveil(top1);
        Assert.assertEquals(obs.ownTopText(), "  library top (known to you, top first): 1) Chain Lightning\n");

        // Ponder: the seat orders the top three, top first: Bolt, Island, Chain Lightning.
        Recorder order = new Recorder("PICKS: 3,2,1");
        LlmSpikeGui.PlayerControllerLLM ctrl2 = seat(g, order, order);
        forge.game.card.CardCollection three = new forge.game.card.CardCollection(me.getCardsIn(ZoneType.Library).subList(0, 3));
        forge.game.card.CardCollectionView placed = ctrl2.orderMoveToZoneList(three, ZoneType.Library, ponder.getFirstSpellAbility());
        for (Card c : three) me.getZone(ZoneType.Library).remove(c);
        for (Card c : placed) me.getZone(ZoneType.Library).add(c, 0);   // what the engine does with the reply
        Assert.assertEquals(ctrl2.observer().ownTopText(),
                "  library top (known to you, top first): 1) Lightning Bolt, 2) Island, 3) Chain Lightning\n");

        me.drawCard();
        Assert.assertEquals(ctrl2.observer().ownTopText(),
                "  library top (known to you, top first): 1) Island, 2) Chain Lightning\n", "the drawn Bolt is gone");
        me.shuffle(null);
        Assert.assertEquals(ctrl2.observer().ownTopText(), "", "a shuffle forgets the order");
    }

    /** With the harness's shortcuts off, the seat says when it does not want to be asked: a PASS with
     *  YIELD: end-of-turn passes later empty-stack windows that turn, and an opponent's spell still asks. */
    @Test
    public void aYieldTheModelAskedForPassesQuietWindowsButNotTheOpponentsSpells() {
        boolean was = LlmSpikeGui.PlayerControllerLLM.SKIP_HEUR;
        LlmSpikeGui.PlayerControllerLLM.SKIP_HEUR = false;
        try {
            Game g = bareGame(PhaseType.MAIN1);
            Player me = g.getPlayers().get(0);
            card("Mountain", me, ZoneType.Battlefield);
            card("Lightning Bolt", me, ZoneType.Hand);
            Recorder model = new Recorder("Nothing to do.\nCHOICE: 2\nYIELD: end-of-turn");
            LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
            Assert.assertNull(ctrl.chooseSpellAbilityToPlay());
            Assert.assertEquals(model.prompts.size(), 1);
            Assert.assertTrue(model.prompts.get(0).contains("YIELD: end-of-turn"), "the yield is offered");
            g.getPhaseHandler().devModeSet(PhaseType.END_OF_TURN, me);
            Assert.assertNull(ctrl.chooseSpellAbilityToPlay());
            Assert.assertEquals(model.prompts.size(), 1, "a quiet window inside the yield was asked");
            Assert.assertEquals(ctrl.windowSkips.get("yield:end-of-turn"), Integer.valueOf(1));
            LineFindingProbeTest.rivalCasts(g, "Ponder", null);
            g.getPhaseHandler().devModeSet(PhaseType.MAIN1, me);
            ctrl.chooseSpellAbilityToPlay();
            Assert.assertEquals(model.prompts.size(), 2, "the opponent's spell did not break the yield");
        } finally {
            LlmSpikeGui.PlayerControllerLLM.SKIP_HEUR = was;
        }
    }

    /** runs/luna-v8/g3, turns 9 and 11: the seat passed at its own upkeep with YIELD: end-of-turn ("keep both
     *  Volcanics open"), and the yield passed its own main phases too: no land drop, no spell, a discard to hand
     *  size. A yield never passes the seat's own main phase; its other quiet windows stay passed. */
    @Test
    public void aYieldAtTheSeatsOwnUpkeepStillAsksInItsMainPhase() {
        boolean was = LlmSpikeGui.PlayerControllerLLM.SKIP_HEUR;
        LlmSpikeGui.PlayerControllerLLM.SKIP_HEUR = false;
        try {
            Game g = bareGame(PhaseType.UPKEEP);
            Player me = g.getPlayers().get(0);
            card("Mountain", me, ZoneType.Battlefield);
            card("Lightning Bolt", me, ZoneType.Hand);
            card("Mountain", me, ZoneType.Hand);
            Recorder model = new Recorder("Keep mana open.\nCHOICE: 99\nYIELD: end-of-turn");
            LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
            Assert.assertNull(ctrl.chooseSpellAbilityToPlay());
            Assert.assertEquals(model.prompts.size(), 1);
            g.getPhaseHandler().devModeSet(PhaseType.MAIN1, me);
            ctrl.chooseSpellAbilityToPlay();
            Assert.assertEquals(model.prompts.size(), 2, "the seat's own main phase was passed by its yield");
            g.getPhaseHandler().devModeSet(PhaseType.END_OF_TURN, me);
            ctrl.chooseSpellAbilityToPlay();
            Assert.assertEquals(model.prompts.size(), 2, "its end step is still inside the yield");
        } finally {
            LlmSpikeGui.PlayerControllerLLM.SKIP_HEUR = was;
        }
    }

    /** The plan and profile asks named Grixis Reanimator's line in every deck's prompt, and the
     *  mulligan ask gave combo advice to Burn. Under the faithful profile no ask names a deck's line. */
    @Test
    public void noAskCoachesOneDecksLine() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        card("Mountain", me, ZoneType.Battlefield);
        card("Lightning Bolt", me, ZoneType.Hand);
        Recorder model = new Recorder("CHOICE: 2");
        seat(g, model, model).chooseSpellAbilityToPlay();
        String p = promptWith(model, "DECISION: You have priority");
        for (String banned : new String[] {"Griselbrand", "Reanimate", "Petal", "Grixis", "Entomb", "Force of Will"})
            Assert.assertFalse(p.contains(banned), "the play ask names " + banned);
        Assert.assertFalse(LlmSpikeGui.PlayerControllerLLM.buildMulliganPrompt(7, 1, "x", true, true).contains("combo deck"));
        Assert.assertFalse(LlmSpikeGui.PlayerControllerLLM.buildMulliganPrompt(7, 1, "x", true, true).contains("workable hand"),
                "the faithful mulligan states the facts, not advice (PROMPT-AUDIT.md C3)");
        String neutral = LlmSpikeGui.PlayerControllerLLM.buildMulliganPrompt(7, 1, "Island, Brainstorm, Ponder", false, true);
        Assert.assertTrue(neutral.contains("on the draw") && neutral.contains("new hand of 6 cards") && neutral.endsWith("KEEP or MULLIGAN."),
                "plain sentences, the facts, and the answer words (PROMPT-AUDIT.md D1): " + neutral);
        Assert.assertTrue(LlmSpikeGui.PlayerControllerLLM.buildMulliganPrompt(7, 1, "x", true).contains("combo deck"),
                "legacy profiles keep their bytes");
    }

    /** docs/STANDARDS.md section 8, the golden check over several decision types at once: every ask
     *  carries the current observation and a DECISION, and no option it offers is cut short. */
    @Test
    public void everyAskCarriesTheStateAndNoCutOption() {
        Game g = bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0), rival = g.getPlayers().get(1);
        for (int i = 0; i < 3; i++) card("Forest", me, ZoneType.Battlefield);
        card("Mountain", me, ZoneType.Battlefield);
        card("Green Sun's Zenith", me, ZoneType.Hand);
        card("Lightning Bolt", me, ZoneType.Hand);
        card("Llanowar Elves", me, ZoneType.Library);
        card("Savannah Lions", rival, ZoneType.Battlefield);
        Recorder model = new Recorder("CHOICE: 1");
        LlmSpikeGui.PlayerControllerLLM ctrl = seat(g, model, model);
        ctrl.chooseSpellAbilityToPlay();                                   // play, X, then a pick
        ctrl.chooseNumber(null, "Choose a number", 0, 3);                  // number
        Assert.assertTrue(model.prompts.size() >= 3, decisions(model));
        java.util.regex.Pattern cut = java.util.regex.Pattern.compile("(?m)^\\s+\\d+\\) [^\\n]*\\.\\.\\.\\]?$");
        for (String p : model.prompts) {
            Assert.assertTrue(p.contains("== Turn ") && p.contains("| priority: "), "no observation: " + p);
            Assert.assertTrue(p.contains("DECISION:"), "no decision: " + p);
            Assert.assertFalse(cut.matcher(p).find(), "an option is cut short: " + p);
        }
    }

    /** #166: the attack outlook said "Every attacker can be blocked" for a flying attacker when the only
     *  creature that could block was a ground creature. It now names who could block each attacker. */
    @Test
    public void theAttackOutlookNamesTheBlockersTheDefenderActuallyHas() {
        LlmSpikeGui.PlayerControllerLLM.TEST_LOOKAHEAD_OVERRIDE = true;
        try {
            Game g = bareGame(PhaseType.COMBAT_DECLARE_ATTACKERS);
            Player me = g.getPlayers().get(0), opp = g.getPlayers().get(1);
            Card angel = card("Serra Angel", me, ZoneType.Battlefield);
            Card bear = card("Grizzly Bears", me, ZoneType.Battlefield);
            card("Grizzly Bears", opp, ZoneType.Battlefield);
            forge.game.card.CardCollection legal = new forge.game.card.CardCollection();
            legal.add(angel);
            legal.add(bear);
            String outlook = LlmSpikeGui.PlayerControllerLLM.attackersCeiling(legal, opp, new forge.game.combat.Combat(me));
            Assert.assertTrue(outlook.contains("None of their creatures that can block now could block: Serra Angel (4)"), outlook);
            Assert.assertTrue(outlook.contains("Could be blocked: Grizzly Bears by Grizzly Bears"), outlook);
            Assert.assertFalse(outlook.contains("Every attacker can be blocked"), outlook);
        } finally {
            LlmSpikeGui.PlayerControllerLLM.TEST_LOOKAHEAD_OVERRIDE = null;
        }
    }
}
