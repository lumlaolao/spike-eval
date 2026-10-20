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
        Assert.assertEquals(previews.calls.size(), 3, "options 1-3; PASS is not previewed");
        for (NativeTools.Call c : previews.calls) Assert.assertEquals(c.name, NativeTools.SIMULATE);
        Assert.assertEquals(JsonParser.parseString(previews.calls.get(1).arguments).getAsJsonObject().get("option").getAsInt(), 2);
        NativeTools.Turn answer = g.chat(afterPreviews(MENU, previews,
                "cast Dark Ritual: you mana pool empty -> BBB; graveyard 0 -> 1; Opponent no change",
                "cast Lava Spike [assuming target: Opponent]: you graveyard 0 -> 1; Opponent life 11 -> 8",
                "activate Swamp: you mana pool empty -> B; Opponent no change").toString(), TOOLS, true);
        Assert.assertEquals(args(answer).get("option").getAsInt(), 2);
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

    @Test
    public void theFactoryBuildsIt() {
        Assert.assertTrue(LlmSpikeGui.LLMBridgeFactory.create("Solver/smart", "greedy:", "") instanceof GreedyBridge);
    }
}
