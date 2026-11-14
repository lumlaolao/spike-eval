package forge.spike;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
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
        return NativeTools.Turn.ofCalls(Arrays.asList(cs));
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

    /** A tool ask carried "Reason briefly, then call `answer` (...)" and then the ANSWER line saying it again
     *  (2026-10-16 review M1): a reasoning cap and the instruction twice. The text tail is removed; the one
     *  answer line is the ANSWER line the ask appends. */
    @Test
    public void aToolAskCapsNoReasoningAndSaysHowToAnswerOnce() {
        String text = "Keep or mulligan? Reason briefly, then end with EXACTLY: KEEP or MULLIGAN\n";
        String out = AnswerTool.rephrase(text, AnswerTool.Spec.keep(), false);
        Assert.assertEquals(out, "Keep or mulligan?\n");
        Assert.assertNull(AnswerTool.textFormatLeak(out));
        String play = "Options:\n  1) PASS\n" + LlmSpikeGui.PlayerControllerLLM.PLAY_PROMPT_TAIL;
        Assert.assertFalse(AnswerTool.rephrase(play, AnswerTool.Spec.one(1), false).contains("Reason"));
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

    /** A yield says when the seat is still asked as the play loop does it (#399): its own main phase asks only once the
     *  stack is empty (its own triggers pending are passed), and end-of-turn covers this turn's end step. */
    @Test
    public void theYieldSaysWhenTheSeatIsStillAsked() {
        String fixed = new AnswerTool.Ask(AnswerTool.Spec.one(2), false).definitions().toString();
        AnswerTool.Spec play = AnswerTool.Spec.one(3);
        play.yield = true;  // the play ask's own schema (playSpec.yield): the copy a per-decision seat is sent
        String perDecision = AnswerTool.definition(play).toString();
        for (String text : new String[] {fixed, perDecision, LlmSpikeGui.PlayerControllerLLM.YIELD_TOOL_HINT}) {
            Assert.assertFalse(text.contains("your own main phases always ask too"), text);
            Assert.assertTrue(text.contains("your own main phase asks again once the stack is empty"), text);
            Assert.assertTrue(text.contains("end-of-turn lasts through this turn's end step"), text);
        }
        Assert.assertTrue(LlmSpikeGui.PlayerControllerLLM.YIELD_HINT.contains("your own main phases always ask too"),
                "the text arm's hint is a legacy profile's bytes: it keeps its words (#399)");
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

    /** A seat that keeps its conversation (ONE_CHAT, TURN_CHAT) replays an answered ask as it happened. Every tool
     *  call in it needs its result, or the next request is not a valid tool-call sequence; the answering
     *  message with its reasoning must be there (OpenRouter: pass reasoning_details back unchanged, in order). */
    @Test
    public void anAnsweredAskKeepsItsCallsResultsAndReasoning() {
        AnswerTool.Ask ask = new AnswerTool.Ask(AnswerTool.Spec.one(4), true);
        ask.planSink = p -> { };
        NativeTools.Turn t = calls(new NativeTools.Call("c1", "set_plan", "{\"goal\":\"win\",\"next\":\"storm\"}"),
                new NativeTools.Call("c2", "answer", "{\"option\":2}"));
        JsonObject withReasoning = JsonParser.parseString(t.messageJson).getAsJsonObject();
        withReasoning.add("reasoning_details", JsonParser.parseString("[{\"type\":\"reasoning.encrypted\",\"data\":\"abc\"}]"));
        NativeTools.Turn turn = new NativeTools.Turn(null, withReasoning.toString(), t.calls);
        Assert.assertEquals(AnswerTool.converse("DECISION", ask, (u, m, tools, allow, r) -> turn, null, (a, b) -> { }), "CHOICE: 2");
        JsonArray tr = ask.transcript;
        Assert.assertNotNull(tr);
        Assert.assertEquals(tr.get(1).toString(), withReasoning.toString(), "the answering message is kept byte for byte");
        Assert.assertEquals(tr.size(), 4, "question, the answering message once, then one result per call: " + tr);
        Assert.assertEquals(tr.get(2).getAsJsonObject().get("role").getAsString(), "tool");
        Assert.assertEquals(tr.get(3).getAsJsonObject().get("role").getAsString(), "tool");
        java.util.Set<String> results = new java.util.HashSet<>();
        for (JsonElement m : tr) if (m.getAsJsonObject().has("tool_call_id")) results.add(m.getAsJsonObject().get("tool_call_id").getAsString());
        Assert.assertEquals(results, java.util.Set.of("c1", "c2"), "a result for every call");
        Assert.assertTrue(tr.get(tr.size() - 1).getAsJsonObject().get("content").getAsString().startsWith("Answer recorded: CHOICE: 2")
                || tr.get(tr.size() - 2).getAsJsonObject().get("content").getAsString().startsWith("Answer recorded: CHOICE: 2"), tr.toString());
    }

    /** frontier5, GLM 5.3 on hard-elf2 (2026-10-26): one reply held `answer {lines}` (no option) and then
     *  `answer {option: 14}`. Only the first was read, so the legal second was refused ("one answer per
     *  decision"), the reply counted invalid, and after the retry did the same the decision was lost to PASS:
     *  main phase 1 skipped, the board lost. The first call in a reply that validates is the answer. */
    @Test
    public void aLegalAnswerAfterAnIncompleteOneInTheSameReplyIsTheAnswer() {
        AnswerTool.Ask ask = new AnswerTool.Ask(AnswerTool.Spec.one(3), false);
        String reply = AnswerTool.converse("DECISION", ask, (user, messages, tools, allow, r) -> calls(
                new NativeTools.Call("c1", "answer", "{\"lines\":\"Heritage Druid + Nettle Sentinel ramp\"}"),
                new NativeTools.Call("c2", "answer", "{\"option\":2,\"why\":\"start the engine\"}")),
                null, (req, res) -> { });
        Assert.assertEquals(reply, "WHY: start the engine\nCHOICE: 2");
        Assert.assertEquals(ask.via, "tool");
        Assert.assertEquals(ask.invalid, 0, "a reply that answered is not an invalid one");
        Assert.assertFalse(ask.lost);
        java.util.Map<String, String> results = toolResults(ask);
        Assert.assertEquals(results.keySet(), java.util.Set.of("c1", "c2"), "a result for every call, the unused one too");
        // Its fields (here its `lines`) are not merged into the answer, and its result says so rather than "call again".
        Assert.assertTrue(results.get("c1").startsWith("Not used: a later `answer` call"), results.get("c1"));
    }

    /** A legal `answer` call followed by an illegal one: the first is the answer, and every call has a result. */
    @Test
    public void aLegalAnswerBeforeAnIllegalOneIsTheAnswer() {
        AnswerTool.Ask ask = new AnswerTool.Ask(AnswerTool.Spec.one(3), false);
        String reply = AnswerTool.converse("DECISION", ask, (user, messages, tools, allow, r) -> calls(
                new NativeTools.Call("c1", "answer", "{\"option\":1}"),
                new NativeTools.Call("c2", "answer", "{\"option\":9}")),
                null, (req, res) -> { });
        Assert.assertEquals(reply, "CHOICE: 1");
        Assert.assertEquals(ask.invalid, 0);
        Assert.assertEquals(toolResults(ask).keySet(), java.util.Set.of("c1", "c2"));
    }

    private static java.util.Map<String, String> toolResults(AnswerTool.Ask ask) {
        java.util.Map<String, String> out = new java.util.HashMap<>();
        for (JsonElement e : ask.transcript) {
            JsonObject m = e.getAsJsonObject();
            if (m.has("tool_call_id")) out.put(m.get("tool_call_id").getAsString(), m.get("content").getAsString());
        }
        return out;
    }

    /** Two illegal `answer` calls in ONE reply are one invalid reply, not two: the decision is asked again. */
    @Test
    public void illegalAnswersInOneReplyCountAsOneInvalidReply() {
        AnswerTool.Ask ask = new AnswerTool.Ask(AnswerTool.Spec.one(2), false);
        NativeTools.Turn[] script = {
                calls(new NativeTools.Call("c1", "answer", "{\"option\":9}"), new NativeTools.Call("c2", "answer", "{\"option\":8}")),
                calls(new NativeTools.Call("c3", "answer", "{\"option\":1}")),
        };
        int[] round = {0};
        String reply = AnswerTool.converse("DECISION", ask, (user, messages, tools, allow, r) -> script[round[0]++],
                null, (req, res) -> { });
        Assert.assertEquals(reply, "CHOICE: 1");
        Assert.assertEquals(ask.invalid, 1);
        Assert.assertFalse(ask.lost);
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

    /** `steps` asks the harness to cast the plan's steps without asking again, which only PLAN_EXECUTE
     *  does. Without it the play ask must not offer steps: the offer would be a promise the harness breaks. */
    @Test
    public void stepsAreOfferedOnlyWhereTheyAreCarriedOut() {
        boolean was = LlmSpikeGui.PlayerControllerLLM.PLAN_EXECUTE;
        try {
            LlmSpikeGui.PlayerControllerLLM.PLAN_EXECUTE = false;
            String off = LlmSpikeGui.PlayerControllerLLM.playToolAsks(true, false, false);
            Assert.assertTrue(off.contains("set_plan") && !off.contains("`steps`"), off);
            LlmSpikeGui.PlayerControllerLLM.PLAN_EXECUTE = true;
            Assert.assertTrue(LlmSpikeGui.PlayerControllerLLM.playToolAsks(true, false, false).contains("`steps`"));
        } finally {
            LlmSpikeGui.PlayerControllerLLM.PLAN_EXECUTE = was;
        }
        // A play decision that does not offer steps ignores them rather than losing the answer.
        Assert.assertEquals(AnswerTool.validate(AnswerTool.Spec.one(3), "{\"option\":1,\"steps\":[1]}").answer.steps, null);
    }

    /** Luna fills every optional field with a default (the probe of 2026-10-15, and 175 of 189 recorded answers):
     *  "yield":"end-of-turn", "keep_untapped":[], "keep":false, "number":0. Those defaults must do nothing: no
     *  yield (it took the seat out of its own windows), and no "the engine chooses" for the lands. */
    @Test
    public void aModelsFilledInDefaultsDoNothing() {
        String filled = "{\"option\":%d,\"options\":[],\"blocks\":[],\"amounts\":[],\"keep\":false,\"number\":0,\"card\":\"\","
                + "\"steps\":[],\"yield\":\"%s\",\"profile\":\"\",\"lines\":\"\",\"keep_untapped\":[],\"why\":\"Bolt is lethal.\"}";
        AnswerTool.Spec play = AnswerTool.Spec.one(3);
        play.yield = true;
        play.keep = true;
        AnswerTool.Answer cast = AnswerTool.validate(play, String.format(filled, 1, "none")).answer;
        Assert.assertEquals(cast.option, Integer.valueOf(1));
        Assert.assertNull(cast.yieldUntil);
        Assert.assertNull(cast.keepUntapped, "an empty keep_untapped is not said: the seat is asked when it matters");
        Assert.assertNull(cast.profile);
        Assert.assertEquals(AnswerTool.validate(play, String.format(filled, 3, "none")).answer.yieldUntil, null, "PASS with none");
        Assert.assertEquals(AnswerTool.validate(play, String.format(filled, 3, "end-of-turn")).answer.yieldUntil, "end-of-turn");
        // The schema's first yield value is the neutral one a model that fills every field takes.
        com.google.gson.JsonArray e = new AnswerTool.Ask(play, false).definitions().get(0).getAsJsonObject()
                .getAsJsonObject("function").getAsJsonObject("parameters").getAsJsonObject("properties")
                .getAsJsonObject("yield").getAsJsonArray("enum");
        Assert.assertEquals(e.get(0).getAsString(), "none");
    }

    /** A call is forced (tool_choice "required") only where that is measured to keep the model's reasoning:
     *  OpenAI models. Anthropic's 5.5 models refuse forcing with a 400, and a forced call skips thinking. */
    @Test
    public void aCallIsForcedOnlyWhereForcingKeepsTheReasoning() {
        if (System.getenv("ANSWER_TOOL_CHOICE") != null) throw new org.testng.SkipException("ANSWER_TOOL_CHOICE is set");
        Assert.assertEquals(AnswerTool.toolChoiceFor("openai/gpt-6-luna"), "required");
        Assert.assertEquals(AnswerTool.toolChoiceFor("openrouter:openai/gpt-6-luna"), "required");
        Assert.assertEquals(AnswerTool.toolChoiceFor("anthropic/claude-opus-5.5"), "auto");
        Assert.assertEquals(AnswerTool.toolChoiceFor("deepseek/deepseek-v4.1-flash"), "auto");
    }

    /** set_plan's schema says `goal` is at most 300 characters and `next` at most 500, but a non-strict tool's
     *  maxLength is advisory and nothing enforced it: a long plan was carried in every later prompt of the game. The
     *  harness does not cut the model's words, so an over-long plan is refused, by field and length, and the call
     *  may be made again; one at the limit is kept whole. */
    @Test
    public void aPlanLongerThanItsSchemaSaysIsRefusedByFieldAndNotRecorded() {
        AnswerTool.Ask ask = new AnswerTool.Ask(AnswerTool.Spec.one(3), false);
        List<String> plans = new ArrayList<>();
        ask.planSink = plans::add;
        String longNext = "cast a card ".repeat(170).trim();
        String refused = ask.note(new NativeTools.Call("p", "set_plan",
                "{\"goal\":\"win\",\"next\":\"" + longNext + "\"}"));
        Assert.assertEquals(refused, "ERROR: `next` is at most 500 characters; yours is " + longNext.length()
                + ". The plan was not recorded: call set_plan again with a shorter one.");
        Assert.assertTrue(ask.note(new NativeTools.Call("p", "set_plan", "{\"goal\":\"" + "g".repeat(301)
                + "\",\"next\":\"attack\"}")).startsWith("ERROR: `goal` is at most 300 characters; yours is 301."));
        Assert.assertTrue(plans.isEmpty(), "nothing recorded: " + plans);
        String atLimit = "n".repeat(500);
        Assert.assertTrue(ask.note(new NativeTools.Call("p", "set_plan", "{\"goal\":\"" + "g".repeat(300)
                + "\",\"next\":\"" + atLimit + "\"}")).startsWith("Plan recorded."));
        Assert.assertEquals(plans, List.of("GOAL: " + "g".repeat(300) + " NEXT: " + atLimit));
        // The schema's numbers are the ones enforced.
        JsonObject props = AnswerTool.setPlanDefinition().getAsJsonObject("function")
                .getAsJsonObject("parameters").getAsJsonObject("properties");
        Assert.assertEquals(props.getAsJsonObject("goal").get("maxLength").getAsInt(), AnswerTool.PLAN_GOAL_CHARS);
        Assert.assertEquals(props.getAsJsonObject("next").get("maxLength").getAsInt(), AnswerTool.PLAN_NEXT_CHARS);
    }

    /** A reply that writes a call as text on a round whose lookups are open was told "No lookups are available
     *  for this decision any more" (CALL_ANSWER), which was false; it is told why nothing ran. With the lookups
     *  closed, CALL_ANSWER is still what it hears. */
    @Test
    public void aCallWrittenAsTextIsToldItMustBeAFunctionCallNotThatLookupsAreGone() {
        String markup = "<tool_call>{\"name\":\"simulate\",\"arguments\":{\"option\":1}}</tool_call>";
        JsonObject said = new JsonObject();
        said.addProperty("role", "assistant");
        said.addProperty("content", markup);
        NativeTools.Turn asText = new NativeTools.Turn(markup, said.toString(), java.util.Collections.emptyList());
        for (boolean open : new boolean[] {true, false}) {
            AnswerTool.Ask ask = new AnswerTool.Ask(AnswerTool.Spec.one(3), false);
            ask.planSink = p -> { };
            List<String> lastUser = new ArrayList<>();
            String reply = AnswerTool.converse("DECISION", ask, (user, messages, tools, allow, r) -> {
                JsonArray m = JsonParser.parseString(messages).getAsJsonArray();
                lastUser.add(m.get(m.size() - 1).getAsJsonObject().get("content").getAsString());
                return r == 0 ? asText : calls(new NativeTools.Call("c", "answer", "{\"option\":2}"));
            }, AnswerTool.previewOnly(open ? args -> "preview" : null), (req, res) -> { });
            Assert.assertEquals(reply, "CHOICE: 2");
            Assert.assertEquals(lastUser.get(1), open ? AnswerTool.CALL_AS_FUNCTION : AnswerTool.CALL_ANSWER);
        }
        Assert.assertFalse(AnswerTool.CALL_AS_FUNCTION.contains("No lookups"), AnswerTool.CALL_AS_FUNCTION);
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

    /** A reply that explains its choice in text but calls nothing is asked, in the same conversation, for the
     *  call; its prose is never parsed as the answer. */
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

        // Text is never read as the answer: replies that keep calling nothing are reminded MAX_NUDGES times,
        // then the decision is lost (the caller's fixed legal option), even when the prose looks like one.
        AnswerTool.Ask never = new AnswerTool.Ask(AnswerTool.Spec.one(5), false);
        int[] rounds = {0};
        String text = AnswerTool.converse("DECISION", never, (user, messages, tools, allow, r) -> {
            rounds[0]++;
            return new NativeTools.Turn("CHOICE: 2", "{\"role\":\"assistant\",\"content\":\"CHOICE: 2\"}", new ArrayList<>());
        }, null, (req, res) -> { });
        Assert.assertNull(text);
        Assert.assertTrue(never.lost);
        Assert.assertEquals(never.via, "lost");
        Assert.assertEquals(rounds[0], 1 + AnswerTool.MAX_NUDGES);
    }

    /** A play can say which mana sources stay untapped when it is paid, by handle; an empty list or no list
     *  means the seat is asked when the choice matters. */
    @Test
    public void aPlaySaysWhichManaSourcesStayUntapped() {
        AnswerTool.Spec play = AnswerTool.Spec.one(3);
        play.keep = true;
        AnswerTool.Answer a = AnswerTool.validate(play, "{\"option\":1,\"keep_untapped\":[\"@12\",\"Volcanic Island @16\"]}").answer;
        Assert.assertEquals(a.keepUntapped, List.of(12, 16));
        Assert.assertTrue(AnswerTool.render(play, a).endsWith("KEEP UNTAPPED: @12, @16"), AnswerTool.render(play, a));
        // Empty is not said (v8): the seat is asked when the choice matters, never "the engine chooses".
        Assert.assertNull(AnswerTool.validate(play, "{\"option\":1,\"keep_untapped\":[]}").answer.keepUntapped);
        Assert.assertNull(AnswerTool.validate(play, "{\"option\":1}").answer.keepUntapped);
        Assert.assertNotNull(AnswerTool.validate(play, "{\"option\":1,\"keep_untapped\":\"@12\"}").error);
        // Not a play: the field is ignored, as the other decision kinds' notes are.
        Assert.assertNull(AnswerTool.validate(AnswerTool.Spec.one(3), "{\"option\":1,\"keep_untapped\":[\"@12\"]}").answer.keepUntapped);
    }
}
