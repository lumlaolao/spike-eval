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

    @BeforeMethod
    public void faithful() { LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = LlmSpikeGui.ContextProfile.V4_FAITHFUL; }

    @AfterMethod(alwaysRun = true)
    public void legacy() { LlmSpikeGui.PlayerControllerLLM.TEST_PROFILE_OVERRIDE = null; }

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
}
