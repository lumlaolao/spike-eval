package forge.spike;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.testng.Assert;
import org.testng.SkipException;
import org.testng.annotations.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/** The answer tool: every decision is answered by one call, checked against that decision. */
public class AnswerToolTest {

    private static NativeTools.Turn calls(NativeTools.Call... cs) {
        JsonObject msg = new JsonObject();
        msg.addProperty("role", "assistant");
        JsonArray tcs = new JsonArray();
        for (NativeTools.Call c : cs) {
            JsonObject tc = new JsonObject();
            tc.addProperty("id", c.id);
            tc.addProperty("type", "function");
            JsonObject f = new JsonObject();
            f.addProperty("name", c.name);
            f.addProperty("arguments", c.arguments);
            tc.add("function", f);
            tcs.add(tc);
        }
        msg.add("tool_calls", tcs);
        return new NativeTools.Turn(null, msg.toString(), Arrays.asList(cs));
    }

    /** Tool definitions open the prompt-cache prefix: one decision's list must be byte-identical to the
     *  next one's, whatever kind each is, or every call rewrites the whole cache. */
    @Test
    public void everyDecisionOffersTheSameToolsSoTheCacheHolds() {
        String one = new AnswerTool.Ask(AnswerTool.Spec.one(4), false).definitions().toString();
        String blocks = new AnswerTool.Ask(AnswerTool.Spec.blocks(List.of("Bear"), List.of("Goblin", "Elf")), true).definitions().toString();
        String keep = new AnswerTool.Ask(AnswerTool.Spec.keep(), true).definitions().toString();
        Assert.assertEquals(blocks, one);
        Assert.assertEquals(keep, one);
        Assert.assertTrue(one.contains("\"name\":\"answer\"") && one.contains("\"name\":\"set_plan\""));
    }

    /** The tool-use suite measures the same two functions the seat is offered (eval/tooluse/answer_tools.json,
     *  next to the lookups' tools.json). */
    @Test
    public void theFixedFunctionsMatchTheToolUseSuite() throws IOException {
        String path = System.getProperty("harness.tooluse.json");
        if (path == null) throw new SkipException("-Dharness.tooluse.json not set");
        JsonArray suite = JsonParser.parseString(new String(Files.readAllBytes(Paths.get(path).resolveSibling("answer_tools.json")),
                StandardCharsets.UTF_8)).getAsJsonArray();
        Assert.assertEquals(suite, new AnswerTool.Ask(AnswerTool.Spec.one(2), false).definitions(),
                "AnswerTool's fixed functions differ from eval/tooluse/answer_tools.json");
    }

    @Test
    public void aCallIsCheckedAgainstItsDecisionAndAMistakeIsExplained() {
        AnswerTool.Spec s = AnswerTool.Spec.one(3);
        Assert.assertEquals(AnswerTool.validate(s, "{\"option\":2}").answer.option, Integer.valueOf(2));
        Assert.assertTrue(AnswerTool.validate(s, "{\"option\":4}").error.contains("1-3"));
        // The fixed schema names every field, so the model can pick the wrong one: it is told the right one.
        String wrong = AnswerTool.validate(s, "{\"options\":[2]}").error;
        Assert.assertTrue(wrong.contains("`option`") && wrong.contains("options"), wrong);
        // Another kind's field beside the right one is ignored, not a lost decision.
        Assert.assertEquals(AnswerTool.validate(s, "{\"option\":1,\"options\":[]}").answer.option, Integer.valueOf(1));
        Assert.assertNotNull(AnswerTool.validate(s, "{\"option\":1,\"bogus\":1}").error);

        AnswerTool.Spec many = AnswerTool.Spec.many(4, 1, 2);
        Assert.assertEquals(AnswerTool.validate(many, "{\"options\":[3,1]}").answer.zeroBased(), List.of(2, 0));
        Assert.assertTrue(AnswerTool.validate(many, "{\"options\":[]}").error.contains("between 1 and 2"));
        Assert.assertEquals(AnswerTool.validate(AnswerTool.Spec.keep(), "{\"keep\":false}").answer.keep, Boolean.FALSE);
    }

    /** One illegal call gets the reason and one more round in the same conversation; the tool list the
     *  model sees does not change between those rounds. A plan set alongside is kept. */
    @Test
    public void anIllegalCallIsCorrectedInTheSameConversation() {
        AnswerTool.Ask ask = new AnswerTool.Ask(AnswerTool.Spec.one(3), false);
        List<String> plans = new ArrayList<>();
        ask.planSink = plans::add;
        List<String> toolLists = new ArrayList<>();
        List<String> lastMessages = new ArrayList<>();
        NativeTools.Turn[] script = {
                calls(new NativeTools.Call("c1", "set_plan", "{\"goal\":\"win\",\"next\":\"attack\"}"),
                      new NativeTools.Call("c2", "answer", "{\"option\":7}")),
                calls(new NativeTools.Call("c3", "answer", "{\"option\":3}")),
        };
        int[] round = {0};
        String reply = AnswerTool.converse("DECISION", ask, (user, messages, tools, allow, r) -> {
            toolLists.add(tools);
            lastMessages.add(messages);
            return script[round[0]++];
        }, null, (req, res) -> { });
        Assert.assertEquals(reply, "CHOICE: 3");
        Assert.assertEquals(ask.via, "tool");
        Assert.assertEquals(ask.invalid, 1);
        Assert.assertEquals(plans, List.of("GOAL: win NEXT: attack"));
        Assert.assertEquals(toolLists.get(1), toolLists.get(0));
        JsonArray second = JsonParser.parseString(lastMessages.get(1)).getAsJsonArray();
        String last = second.get(second.size() - 1).getAsJsonObject().get("content").getAsString();
        Assert.assertTrue(last.startsWith("ERROR: option 7 is not on the list"), last);
    }

    @Test
    public void twoIllegalCallsLoseTheDecisionToTheCallersFallback() {
        AnswerTool.Ask ask = new AnswerTool.Ask(AnswerTool.Spec.one(2), false);
        String reply = AnswerTool.converse("DECISION", ask,
                (user, messages, tools, allow, r) -> calls(new NativeTools.Call("c" + r, "answer", "{\"option\":9}")),
                null, (req, res) -> { });
        Assert.assertNull(reply);
        Assert.assertTrue(ask.lost);
        Assert.assertEquals(ask.via, "lost");
    }

    /** A seat with the lookahead preview declares `simulate` on every ask, so a decision without a play
     *  menu has the same tools as one with; a preview there is refused with the reason. */
    @Test
    public void thePreviewIsDeclaredEverywhereAndRefusedWhereThereIsNothingToPreview() {
        AnswerTool.Lookups none = AnswerTool.previewOnly(null);
        AnswerTool.Lookups some = AnswerTool.previewOnly(args -> "preview");
        Assert.assertEquals(none.definitions().toString(), some.definitions().toString());
        Assert.assertFalse(none.open(0));
        Assert.assertTrue(none.serve(new NativeTools.Call("c", NativeTools.SIMULATE, "{\"option\":1}"), false)
                .startsWith(NativeTools.NO_PREVIEW));
    }

    /** A model whose reasoning is encrypted leaves its `why` on the row, before the answer line every
     *  reader looks for; readable reasoning is kept, encrypted reasoning is not. */
    @Test
    public void theRecordKeepsWhyAndReadableReasoning() {
        AnswerTool.Ask ask = new AnswerTool.Ask(AnswerTool.Spec.one(3), false);
        String reply = AnswerTool.converse("DECISION", ask, (user, messages, tools, allow, r) ->
                calls(new NativeTools.Call("c1", "answer", "{\"option\":2,\"why\":\"Bolt kills their only blocker.\"}")),
                null, (req, res) -> { });
        Assert.assertEquals(reply, "WHY: Bolt kills their only blocker.\nCHOICE: 2");
        Assert.assertEquals(NativeTools.reasoningOf("{\"role\":\"assistant\",\"reasoning\":\"They have no blockers.\"}"),
                "They have no blockers.");
        Assert.assertEquals(NativeTools.reasoningOf("{\"role\":\"assistant\",\"reasoning\":null,\"reasoning_details\":"
                + "[{\"type\":\"reasoning.summary\",\"summary\":\"Count lethal first.\"}]}"), "Count lethal first.");
        Assert.assertNull(NativeTools.reasoningOf("{\"role\":\"assistant\",\"reasoning\":null,\"reasoning_details\":"
                + "[{\"type\":\"reasoning.encrypted\",\"data\":\"gAAAA\"}]}"));
    }

    /** A reply that explains its choice in text but calls nothing is asked once, in the same conversation,
     *  for the call; the caller's full re-ask is no longer needed. */
    @Test
    public void aReplyThatCallsNothingIsAskedForTheCallInTheSameConversation() {
        AnswerTool.Ask ask = new AnswerTool.Ask(AnswerTool.Spec.one(5), false);
        List<String> seen = new ArrayList<>();
        int[] round = {0};
        String reply = AnswerTool.converse("DECISION", ask, (user, messages, tools, allow, r) -> {
            seen.add(messages);
            return round[0]++ == 0 ? new NativeTools.Turn("I choose option 5.", "{\"role\":\"assistant\",\"content\":\"I choose option 5.\"}",
                    new ArrayList<>()) : calls(new NativeTools.Call("c1", "answer", "{\"option\":5}"));
        }, null, (req, res) -> { });
        Assert.assertEquals(reply, "CHOICE: 5");
        Assert.assertEquals(ask.via, "nudged");
        JsonArray second = JsonParser.parseString(seen.get(1)).getAsJsonArray();
        Assert.assertEquals(second.get(1).getAsJsonObject().get("content").getAsString(), "I choose option 5.");
        Assert.assertEquals(second.get(2).getAsJsonObject().get("content").getAsString(), AnswerTool.ANSWER_BY_CALL);

        // A second reply that calls nothing goes to the text parser, as before.
        AnswerTool.Ask twice = new AnswerTool.Ask(AnswerTool.Spec.one(5), false);
        String text = AnswerTool.converse("DECISION", twice, (user, messages, tools, allow, r) ->
                new NativeTools.Turn("CHOICE: 2", "{\"role\":\"assistant\",\"content\":\"CHOICE: 2\"}", new ArrayList<>()),
                null, (req, res) -> { });
        Assert.assertEquals(text, "CHOICE: 2");
        Assert.assertEquals(twice.via, "text");
    }
}
