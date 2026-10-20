package forge.spike;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Arrays;

import org.testng.Assert;
import org.testng.annotations.Test;

/**
 * The script arm (docs/harness/HARD-PUZZLES.md section 4, item 1): a written solution line answers the faithful
 * seat's own questions by the labels the seat is shown. A line with no matching option is a drift, never a guess.
 * The questions below are copied from benchmark-v1 game rows (runs/puzzles/bench-v1-e67, RP_243) and the g3x
 * games, so the parser reads what the seat really prints.
 */
public class ScriptBridgeTest {

    private static final String PLAY_MAIN2 = "== Turn 1 MAIN2 (active: Solver | priority: you) ==\n"
            + "YOU [Solver] life 20\n\n"
            + "DECISION: You have priority (MAIN2, your main phase). Choose ONE action:\n"
            + "  1) CAST Divination @1 2{U} [Draw two cards.]\n"
            + "  2) CAST Snakeform @2 2{G/U} [Until end of turn, target creature loses all abilities and becomes a"
            + " green Snake with base power and toughness 1/1. Draw a card.]\n"
            + "  3) ACTIVATE Island @3 [{T}: Add {U}.]\n"
            + "  4) PASS (do nothing)\n"
            + "If you PASS, you may also give `yield` in your `answer`.\n"
            + "ANSWER: call `answer` with `option`: one number from the list.";

    private static final String TARGET = "== Turn 1 MAIN2 (active: Solver | priority: you) ==\n\n"
            + "DECISION: Choose the TARGET for Snakeform @2 2{G/U} [Until end of turn, target creature loses all"
            + " abilities.] (the spell/ability resolves against whatever you pick).\n"
            + "Options:\n"
            + "  1) Thing in the Ice 0/4 [untapped] [Ice x2] [defender] @4 [Battlefield, yours]\n"
            + "  2) Grizzly Bears 2/2 [untapped] @5 [Battlefield, yours]\n"
            + "  3) CANCEL: do not take this action; go back to the action menu\n"
            + "ANSWER: call `answer` with `option`: one number from the list.";

    private static final String TWO_TUNDRA = "== Turn 3 UPKEEP (active: Seat | priority: you) ==\n\n"
            + "DECISION: You have priority (UPKEEP, instant-speed window). Choose ONE action:\n"
            + "  1) ACTIVATE Tundra @25 [{T}: Add {W}.]\n"
            + "  2) ACTIVATE Tundra @25 [{T}: Add {U}.]\n"
            + "  3) ACTIVATE Mox Ruby @28 [{T}: Add {R}.]\n"
            + "  4) PASS (do nothing)\n"
            + "ANSWER: call `answer` with `option`: one number from the list.";

    private static final String TWO_ISLANDS = "== Turn 1 MAIN1 (active: Solver | priority: you) ==\n\n"
            + "DECISION: You have priority (MAIN1, your main phase). Choose ONE action:\n"
            + "  1) ACTIVATE Island @3 [{T}: Add {U}.]\n"
            + "  2) ACTIVATE Island @6 [{T}: Add {U}.]\n"
            + "  3) PASS (do nothing)\n"
            + "ANSWER: call `answer` with `option`: one number from the list.";

    private static final String ATTACK = "== Turn 1 COMBAT_DECLARE_ATTACKERS (active: Solver | priority: you) ==\n\n"
            + "DECISION: COMBAT: declare attackers against Opponent (life 11). Choose which creatures attack; you may"
            + " attack with none.\n"
            + "Choose between 0 and 2 of these (by number):\n"
            + "  1) Goblin Electromancer (4/4)\n"
            + "  2) Thermo-Alchemist (0/3)\n"
            + "ANSWER: call `answer` with `options`: the numbers you pick, between 0 and 2 (an empty list for none).";

    private static final String MANA = "== Turn 1 MAIN1 (active: Solver | priority: you) ==\n\n"
            + "DECISION: MANA: you are paying {1}{B} for CAST Animate Dead @9 1{B} [Animate Dead]. It needs 2 of your"
            + " 4 untapped mana sources. Which should stay untapped after this payment (at most 2)? Pick none if it"
            + " does not matter to you which ones are tapped\n"
            + "Choose between 0 and 2 of these (by number):\n"
            + "  1) Swamp [untapped] @3 [Battlefield, yours] — {T}: Add {B}.\n"
            + "  2) Mountain [untapped] @4 [Battlefield, yours] — {T}: Add {R}.\n"
            + "ANSWER: call `answer` with `options`: the numbers you pick, between 0 and 2 (an empty list for none).";

    private static final String X = "== Turn 1 MAIN1 (active: Solver | priority: you) ==\n\n"
            + "DECISION: Choose X for CAST Prismatic Ending @34 {X}{W} (you can pay for X up to 11)\n"
            + "Options:\n"
            + "  1) 0\n  2) 1\n  3) 10\n  4) 11\n"
            + "ANSWER: call `answer` with `option`: one number from the list.";

    private static final String BLOCKS = "== Turn 2 COMBAT_DECLARE_BLOCKERS (active: Opponent | priority: you) ==\n\n"
            + "DECISION: COMBAT — declare blockers. Incoming: Tajic, Blade of the Legion 2/2 @1, Grizzly Bears 2/2 @2.\n"
            + "ATTACKERS: 1 = Tajic, Blade of the Legion 2/2 @1; 2 = Grizzly Bears 2/2 @2\n"
            + "YOUR BLOCKERS: 1 = Typhoid Rats 1/1 @3; 2 = Boggart Ram-Gang 3/3 @4\n"
            + "ANSWER: call `answer` with `blocks`: pairs of a blocker number and an attacker number (an empty list for"
            + " no blocks).";

    private static String messages(String... userThenTool) {
        JsonArray a = new JsonArray();
        JsonObject u = new JsonObject();
        u.addProperty("role", "user");
        u.addProperty("content", userThenTool[0]);
        a.add(u);
        for (int i = 1; i < userThenTool.length; i++) {
            JsonObject t = new JsonObject();
            t.addProperty("role", "tool");
            t.addProperty("tool_call_id", "s" + i);
            t.addProperty("content", userThenTool[i]);
            a.add(t);
        }
        return a.toString();
    }

    /** The `answer` arguments the bridge called with. */
    private static JsonObject answer(ScriptBridge b, String... userThenTool) {
        NativeTools.Turn t = b.chat(messages(userThenTool), "[]", true);
        Assert.assertNotNull(t, "the script bridge always answers a tool ask");
        Assert.assertEquals(t.calls.size(), 1);
        Assert.assertEquals(t.calls.get(0).name, AnswerTool.NAME);
        Assert.assertTrue(JsonParser.parseString(t.messageJson).getAsJsonObject().has("tool_calls"));
        return JsonParser.parseString(t.calls.get(0).arguments).getAsJsonObject();
    }

    private static ScriptBridge script(String... lines) { return new ScriptBridge("Solver/smart", Arrays.asList(lines)); }

    @Test
    public void aLineTakesTheOptionItsLabelStartsWith() {
        ScriptBridge b = script("cast Divination", "cast Snakeform", "Thing in the Ice");
        Assert.assertEquals(answer(b, PLAY_MAIN2).get("option").getAsInt(), 1);
        Assert.assertEquals(answer(b, PLAY_MAIN2).get("option").getAsInt(), 2);
        Assert.assertEquals(answer(b, TARGET).get("option").getAsInt(), 1);
        Assert.assertNull(b.drift);
        Assert.assertEquals(b.unconsumed(), 0);
        Assert.assertTrue(b.played());
        Assert.assertEquals(b.report().get("script_played"), true);
    }

    @Test
    public void aPlayMenuWithNoMatchPassesAndKeepsTheLine() {
        ScriptBridge b = script("cast Lightning Bolt");
        Assert.assertEquals(answer(b, PLAY_MAIN2).get("option").getAsInt(), 4, "PASS is the wait");
        Assert.assertNull(b.drift, "waiting at a priority window is not a drift");
        Assert.assertEquals(b.waits, 1);
        Assert.assertEquals(b.unconsumed(), 1, "the line is still to be played: the game ends with it unconsumed");
    }

    @Test
    public void aTargetWithNoMatchIsADriftNamingTheLineAndTheOptions() {
        ScriptBridge b = script("Grizzly Bear", "Thing in the Ice");
        answer(b, TARGET.replace("Grizzly Bears 2/2", "Grizzlies 2/2"));
        Assert.assertNotNull(b.drift, "a misspelt target is a drift, never a guess");
        Assert.assertTrue(b.drift.contains("Grizzly Bear"), b.drift);
        Assert.assertTrue(b.drift.contains("Thing in the Ice 0/4"), "the drift lists what was offered: " + b.drift);
        Assert.assertEquals(b.unconsumed(), 2, "nothing after the drift is consumed");
        Assert.assertFalse(b.played());
        Assert.assertEquals(answer(b, TARGET).get("option").getAsInt(), 3, "after a drift: CANCEL, not a target");
    }

    @Test
    public void aReorderedLineDriftsAtTheFirstDecisionItDoesNotFit() {
        // Divination's line came after Snakeform's target: Snakeform's target ask cannot take "cast Divination".
        ScriptBridge b = script("cast Snakeform", "cast Divination", "Thing in the Ice");
        Assert.assertEquals(answer(b, PLAY_MAIN2).get("option").getAsInt(), 2);
        answer(b, TARGET);
        Assert.assertNotNull(b.drift);
        Assert.assertTrue(b.drift.contains("cast Divination"), b.drift);
    }

    @Test
    public void dotsMatchTheRestOfALabelInOrder() {
        ScriptBridge b = script("activate Tundra ... Add {U}");
        Assert.assertEquals(answer(b, TWO_TUNDRA).get("option").getAsInt(), 2);
        Assert.assertNull(b.drift);
    }

    @Test
    public void twoDifferentLabelsMatchingIsAmbiguousButTwinsTakeTheFirst() {
        ScriptBridge b = script("activate Tundra");
        answer(b, TWO_TUNDRA);
        Assert.assertNotNull(b.drift, "W or U is a choice the line must make");
        Assert.assertTrue(b.drift.contains("ambiguous"), b.drift);
        ScriptBridge twins = script("activate Island");
        Assert.assertEquals(answer(twins, TWO_ISLANDS).get("option").getAsInt(), 1, "labels equal but for the handle");
        Assert.assertNull(twins.drift);
    }

    /** The attack menu tells twins apart by "#N", not by a handle (rules pilot RP_141: "Canopy Gorger (6/5) #1"
     *  and "#2"): twins all the same, so "Canopy Gorger; Canopy Gorger" attacks with both. */
    @Test
    public void attackersNumberedAsTwinsAreTwins() {
        String twins = ATTACK.replace("Goblin Electromancer (4/4)", "Canopy Gorger (6/5) #1")
                .replace("Thermo-Alchemist (0/3)", "Canopy Gorger (6/5) #2");
        ScriptBridge b = script("Canopy Gorger; Canopy Gorger");
        Assert.assertEquals(answer(b, twins).getAsJsonArray("options").toString(), "[1,2]");
        Assert.assertNull(b.drift);
    }

    @Test
    public void anExactLabelWinsOverLongerOnes() {
        ScriptBridge b = script("1", "11");
        Assert.assertEquals(answer(b, X).get("option").getAsInt(), 2);
        Assert.assertEquals(answer(b, X).get("option").getAsInt(), 4);
        Assert.assertNull(b.drift);
    }

    @Test
    public void aPickListNamesEachPickAndNoneIsEmpty() {
        ScriptBridge b = script("Goblin Electromancer; Thermo-Alchemist", "none");
        JsonArray picks = answer(b, ATTACK).getAsJsonArray("options");
        Assert.assertEquals(picks.toString(), "[1,2]");
        Assert.assertEquals(answer(b, ATTACK).getAsJsonArray("options").size(), 0);
        Assert.assertNull(b.drift);
    }

    @Test
    public void aManaPaymentNotScriptedLeavesTheChoiceToTheEngine() {
        ScriptBridge b = script("cast Divination");
        Assert.assertEquals(answer(b, MANA).getAsJsonArray("options").size(), 0);
        Assert.assertNull(b.drift);
        Assert.assertEquals(b.unconsumed(), 1);
        ScriptBridge keep = script("Mountain", "cast Divination");
        Assert.assertEquals(answer(keep, MANA).getAsJsonArray("options").toString(), "[2]", "a written untap is kept");
        Assert.assertEquals(keep.unconsumed(), 1);
    }

    @Test
    public void aLineForALaterStepWaitsAtEarlierWindows() {
        ScriptBridge b = script("at COMBAT_DECLARE_BLOCKERS: cast Divination");
        Assert.assertEquals(answer(b, PLAY_MAIN2).get("option").getAsInt(), 4, "MAIN2 is not the step named");
        Assert.assertEquals(answer(b, PLAY_MAIN2.replace("MAIN2", "COMBAT_DECLARE_BLOCKERS")).get("option").getAsInt(), 1);
        Assert.assertNull(b.drift);
        ScriptBridge turn = script("at T2 MAIN2: cast Divination");
        Assert.assertEquals(answer(turn, PLAY_MAIN2).get("option").getAsInt(), 4, "turn 1 is not turn 2");
        Assert.assertEquals(answer(turn, PLAY_MAIN2.replace("Turn 1", "Turn 2")).get("option").getAsInt(), 1);
    }

    @Test
    public void blocksNumbersAndKeepAreWritten() {
        ScriptBridge b = script("Boggart Ram-Gang -> Grizzly Bears; Typhoid Rats -> Tajic", "none");
        JsonArray blocks = answer(b, BLOCKS).getAsJsonArray("blocks");
        Assert.assertEquals(blocks.toString(), "[{\"blocker\":2,\"attacker\":2},{\"blocker\":1,\"attacker\":1}]");
        Assert.assertEquals(answer(b, BLOCKS).getAsJsonArray("blocks").size(), 0);
        String number = "== Turn 1 MAIN1 (active: Solver | priority: you) ==\n\nDECISION: Choose a number.\n"
                + "ANSWER: call `answer` with `number`: an integer from 0 through 5.";
        String amounts = "== Turn 1 MAIN1 (active: Solver | priority: you) ==\n\nDECISION: Divide 3 damage.\n"
                + "ANSWER: call `answer` with `amounts`: 2 numbers in the order listed, summing to 3.";
        ScriptBridge c = script("4", "2, 1");
        Assert.assertEquals(answer(c, number).get("number").getAsInt(), 4);
        Assert.assertEquals(answer(c, amounts).getAsJsonArray("amounts").toString(), "[2,1]");
        Assert.assertNull(c.drift);
    }

    @Test
    public void aDecisionAfterTheLastLineIsExhaustedNotAGuess() {
        ScriptBridge b = script("cast Snakeform");
        answer(b, PLAY_MAIN2);
        answer(b, TARGET);
        Assert.assertTrue(b.exhausted);
        Assert.assertNotNull(b.drift, "a target the line does not name is the line's to name");
        ScriptBridge done = script("cast Divination");
        answer(done, PLAY_MAIN2);
        Assert.assertEquals(answer(done, PLAY_MAIN2).get("option").getAsInt(), 4, "after the line, priority passes");
        Assert.assertFalse(done.exhausted);
    }

    @Test
    public void anAnswerTheHarnessRefusedIsADrift() {
        ScriptBridge b = script("Goblin Electromancer; Thermo-Alchemist", "cast Divination");
        answer(b, ATTACK);
        answer(b, ATTACK, "ERROR: pick at most 1. Nothing was done. Call `answer` again with a legal value.");
        Assert.assertNotNull(b.drift);
        Assert.assertTrue(b.drift.contains("refused"), b.drift);
    }

    @Test
    public void theFactoryReadsTheSolutionSectionOfAPuzzle() throws Exception {
        File pzl = File.createTempFile("script", ".pzl");
        pzl.deleteOnExit();
        Files.write(pzl.toPath(), ("[metadata]\nName:x\n[state]\nturn=1\n[solution]\n# the line\ncast Divination\n\n"
                + "  cast Snakeform  \nThing in the Ice\n[acceptance]\nlife p1 <= 0\n").getBytes(StandardCharsets.UTF_8));
        LLMBridge b = LlmSpikeGui.LLMBridgeFactory.create("Solver/smart", "script:" + pzl.getPath(), "");
        Assert.assertTrue(b instanceof ScriptBridge, String.valueOf(b));
        Assert.assertEquals(((ScriptBridge) b).lines, Arrays.asList("cast Divination", "cast Snakeform", "Thing in the Ice"));
        Assert.assertTrue(b.supportsNativeTools());
    }
}
