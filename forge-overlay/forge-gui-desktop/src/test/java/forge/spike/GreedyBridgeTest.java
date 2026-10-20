package forge.spike;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import org.testng.Assert;
import org.testng.annotations.Test;

/**
 * The greedy-preview floor (docs/harness/HARD-PUZZLES.md section 4, item 4): a zero-model seat that previews every
 * cast and activation on its priority menu with the seat's own `simulate` tool and takes the one that lowers the
 * opponent's life most, else passes. The "depth 1" player: a puzzle it solves needs no planning.
 */
public class GreedyBridgeTest {

    private static final String MENU = "== Turn 1 MAIN1 (active: Solver | priority: you) ==\n\n"
            + "STACK (engine, top first): (empty)\n"
            + "DECISION: You have priority (MAIN1, your main phase). Choose ONE action:\n"
            + "  1) CAST Dark Ritual @1 {B} [Add {B}{B}{B}.]\n"
            + "  2) CAST Lava Spike @2 {R} [Lava Spike deals 3 damage to target player or planeswalker.]\n"
            + "  3) ACTIVATE Swamp @3 [{T}: Add {B}.]\n"
            + "  4) PASS (do nothing)\n"
            + "ANSWER: call `answer` with `option`: one number from the list.";
    private static final String TOOLS = "[{\"type\":\"function\",\"function\":{\"name\":\"simulate\"}},"
            + "{\"type\":\"function\",\"function\":{\"name\":\"answer\"}}]";

    private static JsonArray user(String prompt) {
        JsonArray a = new JsonArray();
        JsonObject u = new JsonObject();
        u.addProperty("role", "user");
        u.addProperty("content", prompt);
        a.add(u);
        return a;
    }

    /** The conversation after the bridge's preview turn, each call answered with {@code results[i]}. */
    private static JsonArray afterPreviews(String prompt, NativeTools.Turn previews, String... results) {
        JsonArray a = user(prompt);
        a.add(JsonParser.parseString(previews.messageJson));
        for (int i = 0; i < previews.calls.size(); i++) a.add(NativeTools.toolResult(previews.calls.get(i).id, results[i]));
        return a;
    }

    private static JsonObject args(NativeTools.Turn t) {
        Assert.assertEquals(t.calls.size(), 1);
        Assert.assertEquals(t.calls.get(0).name, AnswerTool.NAME);
        return JsonParser.parseString(t.calls.get(0).arguments).getAsJsonObject();
    }

    @Test
    public void everyCastAndActivationIsPreviewedThenTheLargestDropIsTaken() {
        GreedyBridge g = new GreedyBridge("Solver/smart");
        NativeTools.Turn previews = g.chat(user(MENU).toString(), TOOLS, true);
        Assert.assertEquals(previews.calls.size(), 2, "the two casts; a land's mana ability and PASS are not previewed");
        for (NativeTools.Call c : previews.calls) Assert.assertEquals(c.name, NativeTools.SIMULATE);
        Assert.assertEquals(JsonParser.parseString(previews.calls.get(1).arguments).getAsJsonObject().get("option").getAsInt(), 2);
        NativeTools.Turn answer = g.chat(afterPreviews(MENU, previews,
                "cast Dark Ritual: you mana pool empty -> BBB; graveyard 0 -> 1; Opponent no change",
                "cast Lava Spike [assuming target: Opponent]: you graveyard 0 -> 1; Opponent life 11 -> 8").toString(), TOOLS, true);
        Assert.assertEquals(args(answer).get("option").getAsInt(), 2);
    }

    /** A preview resolves what is already on the stack too, so with a spell there every option seemed to deal its damage
     *  and the floor cast and tapped for nothing (pr-review of #279: Mutagenic Growth "Opponent life 11 -> 8" was the
     *  Lava Spike beneath it). With the stack not empty it previews nothing and passes. */
    @Test
    public void withSomethingOnTheStackItPasses() {
        GreedyBridge g = new GreedyBridge("Solver/smart");
        String busy = MENU.replace("STACK (engine, top first): (empty)", "STACK (engine, top first): 1 not yet resolved: Lava Spike @2");
        NativeTools.Turn t = g.chat(user(busy).toString(), TOOLS, true);
        Assert.assertEquals(args(t).get("option").getAsInt(), 4, "no previews, a pass");
        Assert.assertEquals(g.chat(user(MENU).toString(), TOOLS, true).calls.get(0).name, NativeTools.SIMULATE);
        // Without the faithful profile's STACK line the stack is not known to be empty: it passes (pr-review of #279).
        String legacy = MENU.replace("STACK (engine, top first): (empty)\n", "");
        Assert.assertEquals(args(new GreedyBridge("x").chat(user(legacy).toString(), TOOLS, true)).get("option").getAsInt(), 4);
    }

    /** The target is the one the chosen play's preview assumed, and the floor never cancels its own play (pr-review of
     *  #279: a creature target with no "opponent" option took CANCEL, twice a window). */
    @Test
    public void aTargetIsTheOneThePreviewAssumedAndNeverCancel() {
        GreedyBridge g = new GreedyBridge("Solver/smart");
        NativeTools.Turn previews = g.chat(user(MENU).toString(), TOOLS, true);
        g.chat(afterPreviews(MENU, previews, "cast Dark Ritual: Opponent no change",
                "cast Lava Spike [assuming target: Thermo-Alchemist]: you graveyard 0 -> 1; Opponent life 11 -> 10").toString(), TOOLS, true);
        String target = "== Turn 1 MAIN1 (active: Solver | priority: you) ==\n\nDECISION: Choose the TARGET for Lava Spike @2\n"
                + "Options:\n  1) Grizzly Bears 2/2 @7 [Battlefield, opponent's]\n  2) Thermo-Alchemist 0/3 @5 [Battlefield, yours]\n"
                + "  3) " + LlmSpikeGui.PlayerControllerLLM.CANCEL_LABEL + "\n"
                + "ANSWER: call `answer` with `option`: one number from the list.";
        Assert.assertEquals(args(g.chat(user(target).toString(), TOOLS, true)).get("option").getAsInt(), 2, "the assumed target");
        // The preview's target is for that ask only: the next target ask has no assumption.
        Assert.assertEquals(args(g.chat(user(target).toString(), TOOLS, true)).get("option").getAsInt(), 1);
        String other = target.replace("Thermo-Alchemist 0/3 @5", "Llanowar Elves 1/1 @5");
        Assert.assertEquals(args(new GreedyBridge("x").chat(user(other).toString(), TOOLS, true)).get("option").getAsInt(), 1,
                "no assumption: the first option that is not CANCEL");
    }

    @Test
    public void nothingThatHurtsTheOpponentIsAPass() {
        GreedyBridge g = new GreedyBridge("Solver/smart");
        NativeTools.Turn previews = g.chat(user(MENU).toString(), TOOLS, true);
        NativeTools.Turn answer = g.chat(afterPreviews(MENU, previews,
                "cast Dark Ritual: you mana pool empty -> BBB; Opponent no change",
                "(preview unavailable: the engine could not pay for or play this option on the copy)",
                "activate Swamp: you life 20 -> 19; Opponent no change").toString(), TOOLS, true);
        Assert.assertEquals(args(answer).get("option").getAsInt(), 4, "the seat's own life loss is not the opponent's");
    }

    @Test
    public void withoutAPreviewToolItPasses() {
        GreedyBridge g = new GreedyBridge("Solver/smart");
        NativeTools.Turn answer = g.chat(user(MENU).toString(), "[{\"type\":\"function\",\"function\":{\"name\":\"answer\"}}]", true);
        Assert.assertEquals(args(answer).get("option").getAsInt(), 4);
    }

    @Test
    public void otherDecisionsAreAnsweredTowardTheOpponent() {
        GreedyBridge g = new GreedyBridge("Solver/smart");
        String target = "== Turn 1 MAIN1 (active: Solver | priority: you) ==\n\nDECISION: Choose the TARGET for Lava Spike @2\n"
                + "Options:\n  1) you (Solver)\n  2) opponent (Opponent)\n"
                + "ANSWER: call `answer` with `option`: one number from the list.";
        Assert.assertEquals(args(g.chat(user(target).toString(), TOOLS, true)).get("option").getAsInt(), 2);
        String attack = "== Turn 1 COMBAT_DECLARE_ATTACKERS (active: Solver | priority: you) ==\n\n"
                + "DECISION: COMBAT: declare attackers against Opponent (life 11).\n"
                + "Choose between 0 and 2 of these (by number):\n  1) Goblin Electromancer (2/2)\n  2) Thermo-Alchemist (0/3)\n"
                + "ANSWER: call `answer` with `options`: the numbers you pick, between 0 and 2 (an empty list for none).";
        Assert.assertEquals(args(g.chat(user(attack).toString(), TOOLS, true)).getAsJsonArray("options").toString(), "[1,2]",
                "every creature that can attack does");
    }

    /** Only a target ask is answered toward the opponent; a tax, a "you may" or a cast from an effect keeps the
     *  do-nothing answer (pr-review of #279: "never CANCEL" on every single pick paid every tax). */
    @Test
    public void aQuestionThatIsNotATargetKeepsTheDoNothingAnswer() {
        String tax = "== Turn 1 COMBAT_DECLARE_ATTACKERS (active: Solver | priority: you) ==\n\nDECISION: Pay {1} to attack with "
                + "Goblin Electromancer?\nOptions:\n  1) Pay {1}\n  2) Do not pay\n"
                + "ANSWER: call `answer` with `option`: one number from the list.";
        Assert.assertEquals(args(new GreedyBridge("x").chat(user(tax).toString(), TOOLS, true)).get("option").getAsInt(), 2);
    }

    /** A two-target play takes both targets its preview assumed, not the first two candidates. */
    @Test
    public void aMultiTargetAskTakesEveryTargetThePreviewAssumed() {
        GreedyBridge g = new GreedyBridge("Solver/smart");
        NativeTools.Turn previews = g.chat(user(MENU).toString(), TOOLS, true);
        g.chat(afterPreviews(MENU, previews, "cast Dark Ritual: Opponent no change",
                "cast Lava Spike [assuming target: Opponent, Grizzly Bears]: you graveyard 0 -> 1; Opponent life 11 -> 9").toString(),
                TOOLS, true);
        String two = "== Turn 1 MAIN1 (active: Solver | priority: you) ==\n\nDECISION: Choose TARGETS for Arc Trail @2\n"
                + "Choose between 2 and 2 of these (by number):\n  1) you (Solver)\n  2) Thermo-Alchemist 0/3 @5 [Battlefield, yours]\n"
                + "  3) opponent (Opponent)\n  4) Grizzly Bears 2/2 @7 [Battlefield, opponent's]\n"
                + "ANSWER: call `answer` with `options`: the numbers you pick, between 2 and 2 (an empty list for none).";
        Assert.assertEquals(args(g.chat(user(two).toString(), TOOLS, true)).getAsJsonArray("options").toString(), "[3,4]");
    }

    private static String targetAsk(String what, String... options) {
        StringBuilder b = new StringBuilder("== Turn 1 MAIN1 (active: Solver | priority: you) ==\n\nDECISION: Choose the TARGET for "
                + what + "\nOptions:\n");
        for (int i = 0; i < options.length; i++) b.append("  ").append(i + 1).append(") ").append(options[i]).append("\n");
        return b.append("ANSWER: call `answer` with `option`: one number from the list.").toString();
    }

    /** With nothing assumed (a trigger's target), the opponent's card is taken over the seat's own, which the engine
     *  lists first; with only its own, an "up to one" ask declines (pr-review of #279: the first card took a removal). */
    @Test
    public void withNoAssumptionTheOpponentsCardIsTakenNotTheSeatsOwn() {
        String ask = targetAsk("Electrostatic Pummeler's trigger", "Grizzly Bears 2/2 @3 [Battlefield, yours]",
                "Abyssal Persecutor 6/6 @9 [Battlefield, opponent's]", LlmSpikeGui.PlayerControllerLLM.CANCEL_LABEL);
        Assert.assertEquals(args(new GreedyBridge("x").chat(user(ask).toString(), TOOLS, true)).get("option").getAsInt(), 2);
        String upTo = targetAsk("Skyclave Apparition's trigger", "Grizzly Bears 2/2 @3 [Battlefield, yours]", "No target");
        Assert.assertEquals(args(new GreedyBridge("x").chat(user(upTo).toString(), TOOLS, true)).get("option").getAsInt(), 2,
                "declining beats the seat's own creature");
    }

    /** An "up to two" ask has no "No target" row (the empty list declines): with only the seat's own creatures it picks
     *  none (pr-review of #279: the multi ask still took the seat's own creature). */
    @Test
    public void anUpToAskWithOnlyTheSeatsOwnCardsPicksNone() {
        String ask = "== Turn 1 MAIN1 (active: Solver | priority: you) ==\n\nDECISION: Choose TARGETS for Frost Breath @2\n"
                + "Choose between 0 and 2 of these (by number):\n  1) Grizzly Bears 2/2 @3 [Battlefield, yours]\n"
                + "  2) Thermo-Alchemist 0/3 @5 [Battlefield, yours]\n"
                + "ANSWER: call `answer` with `options`: the numbers you pick, between 0 and 2 (an empty list for none).";
        Assert.assertEquals(args(new GreedyBridge("x").chat(user(ask).toString(), TOOLS, true)).getAsJsonArray("options").toString(), "[]");
    }

    /** Only the menu's own "CANCEL:" row is never taken: a card named Cancel is a target like any other. */
    @Test
    public void aCardNamedCancelCanBeTheTarget() {
        String ask = targetAsk("Ashes to Ashes", "Grizzly Bears 2/2 @3 [Battlefield, yours]",
                "Cancel @11 [Graveyard, opponent's]", LlmSpikeGui.PlayerControllerLLM.CANCEL_LABEL);
        Assert.assertEquals(args(new GreedyBridge("x").chat(user(ask).toString(), TOOLS, true)).get("option").getAsInt(), 2);
    }

    /** The cost ask's "CANCEL this play:" row is the menu's own too, and the old {@code startsWith("cancel")} kept it
     *  out (pr-review of #280): here it is the only unshown row beside the seat's own card. */
    @Test
    public void theCostAsksCancelRowIsNeverTaken() {
        String ask = targetAsk("Ashes to Ashes", "Grizzly Bears 2/2 @3 [Battlefield, yours]",
                LlmSpikeGui.PlayerControllerLLM.COST_CANCEL_LABEL);
        Assert.assertEquals(args(new GreedyBridge("x").chat(user(ask).toString(), TOOLS, true)).get("option").getAsInt(), 1);
    }

    /** A label reworded without its colon keeps its whole text as the row to skip; an empty prefix would match every
     *  option and the floor would never take a target (pr-review of #281). */
    @Test
    public void aCancelLabelWithoutAColonIsMatchedWhole() {
        Assert.assertEquals(GreedyBridge.ownRow("CANCEL this play (pay nothing)"), "cancel this play (pay nothing)");
        Assert.assertEquals(GreedyBridge.ownRow("CANCEL: do not take this action"), "cancel:");
    }

    /** A chain (Arc Trail: a root and a sub-ability, one target ask each) gets the assumed targets in turn, and a name on
     *  both sides takes the opponent's copy. */
    @Test
    public void aChainsAsksShareTheAssumedTargetsAndTheOpponentsCopyIsTaken() {
        GreedyBridge g = new GreedyBridge("Solver/smart");
        NativeTools.Turn previews = g.chat(user(MENU).toString(), TOOLS, true);
        g.chat(afterPreviews(MENU, previews, "cast Dark Ritual: Opponent no change",
                "cast Lava Spike [assuming target: Opponent, Grizzly Bears]: you graveyard 0 -> 1; Opponent life 11 -> 9").toString(),
                TOOLS, true);
        String root = targetAsk("Arc Trail", "you (Solver)", "opponent (Opponent)", "Grizzly Bears 2/2 @3 [Battlefield, yours]",
                "Grizzly Bears 2/2 @7 [Battlefield, opponent's]");
        Assert.assertEquals(args(g.chat(user(root).toString(), TOOLS, true)).get("option").getAsInt(), 2);
        String sub = targetAsk("Arc Trail (the second target)", "Grizzly Bears 2/2 @3 [Battlefield, yours]",
                "Grizzly Bears 2/2 @7 [Battlefield, opponent's]");
        Assert.assertEquals(args(g.chat(user(sub).toString(), TOOLS, true)).get("option").getAsInt(), 2, "the opponent's Bears");
    }

    @Test
    public void theFactoryBuildsIt() {
        Assert.assertTrue(LlmSpikeGui.LLMBridgeFactory.create("Solver/smart", "greedy:", "") instanceof GreedyBridge);
    }
}
