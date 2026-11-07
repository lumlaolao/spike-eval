package forge.spike;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.testng.Assert;
import org.testng.annotations.AfterClass;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.Executors;
import java.util.function.Function;

import forge.game.Game;
import forge.game.card.Card;
import forge.game.phase.PhaseType;
import forge.game.player.Player;
import forge.game.spellability.SpellAbility;
import forge.game.zone.ZoneType;

/**
 * The Decisions-API seat (`jev:` specs, DecisionsBridge): a decision model that answers typed questions and never
 * writes text or calls a tool. Its typed answer becomes the harness's own `answer` call, so AnswerTool validates it
 * as it does any model's. Every server here is loopback; nothing in this file can reach openrouter.ai.
 */
@Test(singleThreaded = true) // the captured request list and the scripted reply are shared
public class DecisionsBridgeTest {

    private static final List<String> bodies = new CopyOnWriteArrayList<>();
    private static final List<String> auths = new CopyOnWriteArrayList<>();
    private static final List<String> paths = new CopyOnWriteArrayList<>();
    /** The reply to each request, from its body. */
    private static volatile Function<JsonObject, String> reply = q -> "{}";
    private static volatile int status = 200;
    private HttpServer server;
    private String base;

    @BeforeClass
    public void startServer() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.setExecutor(Executors.newCachedThreadPool());
        // Answers nothing for 1.5 s: past a 100 ms bridge deadline.
        server.createContext("/slow/", ex -> {
            read(ex);
            try { Thread.sleep(1500); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
            respond(ex, 200, "{}");
        });
        server.createContext("/", ex -> {
            String body = read(ex);
            bodies.add(body);
            paths.add(ex.getRequestURI().getPath());
            String a = ex.getRequestHeaders().getFirst("Authorization");
            auths.add(a == null ? "" : a);
            String out;
            try { out = reply.apply(JsonParser.parseString(body).getAsJsonObject()); }
            catch (RuntimeException e) { out = "{}"; }
            respond(ex, status, out);
        });
        server.start();
        base = "http://127.0.0.1:" + server.getAddress().getPort() + "/api/v1";
    }

    @AfterClass(alwaysRun = true)
    public void stopServer() {
        if (server != null) server.stop(0);
    }

    @BeforeMethod
    public void reset() {
        bodies.clear(); auths.clear(); paths.clear();
        status = 200;
        reply = q -> "{}";
    }

    /** The request's own timeout must never beat the deadline loop (issue #388): every call that runs out its time
     *  stops at the deadline, none ends in the client's HttpTimeoutException. */
    @Test(timeOut = 20000)
    public void everyTimedOutCallStopsAtTheDeadlineNeverTheRequestsOwnTimeout() {
        DecisionsBridge b = new DecisionsBridge("Seat/smart", "typesafe/jev-1.14", "p",
                base.replace("/api/v1", "/slow/api/v1"), 100, null, null);
        for (int i = 0; i < 40; i++) {
            Assert.assertNull(b.chat(messages(USER), "[]", true,
                    AnswerTool.Spec.one(2).labels(List.of("CAST Lava Spike", "PASS"))));
            Assert.assertEquals(HttpDeadline.LAST_STOP.get(), "deadline", "call " + i);
        }
    }

    private DecisionsBridge bridge() { return bridge(null); }

    private DecisionsBridge bridge(String apiKey) {
        return new DecisionsBridge("Seat/smart", "typesafe/jev-1.14", "PREAMBLE: you pilot the seat.", base, 5000, apiKey, null);
    }

    private static final String USER = "== Turn 3 MAIN1 ==\nDECISION: Choose ONE action:\n  1) CAST Lava Spike\n  2) PASS";

    private static String messages(String user) {
        return "[" + NativeTools.message("user", user) + "]";
    }

    /** A choice answer for question {@code key}: {@code pick} with probability 0.8. */
    private static String choice(String key, String pick) {
        return "{\"id\":\"gen-dec-1\",\"model\":\"typesafe/jev-1.14-20261020\",\"provider\":\"TypeSafe\","
                + "\"answers\":{\"" + key + "\":{\"type\":\"choice\",\"choice\":\"" + pick + "\",\"confidence\":0.6,"
                + "\"probabilities\":{\"" + pick + "\":0.8}}},"
                + "\"usage\":{\"input_tokens\":476,\"output_tokens\":70,\"cost\":0.000019992}}";
    }

    /** Noul answers: question key -> probability of yes. */
    private static String nouls(JsonObject request, double... p) {
        StringBuilder b = new StringBuilder("{\"answers\":{");
        int i = 0;
        for (String k : request.getAsJsonObject("questions").keySet()) {
            b.append(i == 0 ? "" : ",").append("\"").append(k).append("\":{\"type\":\"noul\",\"noul\":").append(p[i]).append("}");
            i++;
        }
        return b.append("},\"usage\":{\"input_tokens\":100,\"output_tokens\":0,\"cost\":0.0000042}}").toString();
    }

    private static NativeTools.Call onlyAnswer(NativeTools.Turn t) {
        Assert.assertNotNull(t, "no turn: the bridge gave no answer");
        Assert.assertEquals(t.calls.size(), 1, t.messageJson);
        Assert.assertEquals(t.calls.get(0).name, AnswerTool.NAME);
        return t.calls.get(0);
    }

    // ------------------------------------------------------------------ the request

    /** One request per ask, to the Decisions endpoint: the model, the seat's preamble and prompt as the state, and one
     *  choice question whose criteria are every option, keyed by its number. No messages, no tools. */
    @Test
    public void aChoiceAskSendsOneDecisionsRequestWithEveryOptionAndNoTools() {
        reply = q -> choice("decision", "2");
        AnswerTool.Spec spec = AnswerTool.Spec.one(2).labels(List.of("CAST Lava Spike @4", "PASS (do nothing)"));
        bridge().chat(messages(USER), "[{\"type\":\"function\"}]", true, spec);

        Assert.assertEquals(bodies.size(), 1);
        Assert.assertEquals(paths.get(0), "/api/alpha/decisions");
        JsonObject req = JsonParser.parseString(bodies.get(0)).getAsJsonObject();
        Assert.assertEquals(req.get("model").getAsString(), "typesafe/jev-1.14");
        Assert.assertTrue(req.get("state").isJsonObject(), "the docs send state as an object: " + req.get("state"));
        String state = req.getAsJsonObject("state").get("game").getAsString();
        Assert.assertTrue(state.startsWith("PREAMBLE: you pilot the seat."), state);
        Assert.assertTrue(state.contains(USER), state);
        Assert.assertFalse(req.has("tools") || req.has("messages") || req.has("tool_choice"), bodies.get(0));
        JsonObject qs = req.getAsJsonObject("questions");
        Assert.assertEquals(qs.size(), 1, qs.toString());
        JsonObject q = qs.getAsJsonObject("decision");
        Assert.assertEquals(q.get("type").getAsString(), "choice");
        Assert.assertFalse(q.get("instructions").getAsString().isEmpty());
        JsonObject crit = q.getAsJsonObject("criteria");
        Assert.assertEquals(crit.keySet(), new java.util.LinkedHashSet<>(List.of("1", "2")));
        Assert.assertEquals(crit.get("1").getAsString(), "CAST Lava Spike @4");
        Assert.assertEquals(crit.get("2").getAsString(), "PASS (do nothing)");
    }

    /** Jev's choice is the option of that number, as an `answer` call AnswerTool accepts like any model's. */
    @Test
    public void jevsChoiceBecomesAnAnswerCallThatAnswerToolValidates() {
        reply = q -> choice("decision", "2");
        AnswerTool.Spec spec = AnswerTool.Spec.one(3).labels(List.of("a", "b", "c"));
        DecisionsBridge b = bridge();
        AnswerTool.Ask ask = new AnswerTool.Ask(spec, false);
        String rendered = AnswerTool.converse(USER, ask, (u, m, tools, allow, round) -> b.chat(m, tools, allow, spec),
                null, (x, y) -> { });
        Assert.assertTrue(ask.answered(), "AnswerTool did not accept the converted answer");
        Assert.assertEquals(ask.answer.option, Integer.valueOf(2));
        Assert.assertEquals(ask.via, "tool");
        Assert.assertTrue(rendered.endsWith("CHOICE: 2"), rendered);
    }

    // ------------------------------------------------------------------ other decision kinds

    /** Choose k of n: one noul per option, the likeliest taken, as many as say yes but never outside the decision's
     *  bounds; the picks are listed most likely first (an ordering decision reads them in that order). */
    @Test
    public void aMultiPickTakesTheLikeliestOptionsWithinTheDecisionsBounds() {
        AnswerTool.Spec spec = AnswerTool.Spec.many(5, 2, 3).labels(List.of("a", "b", "c", "d", "e"));
        reply = q -> nouls(q, 0.9, 0.2, 0.7, 0.6, 0.55);   // four say yes: the top three
        NativeTools.Call c = onlyAnswer(bridge().chat(messages(USER), "[]", true, spec));
        Assert.assertEquals(AnswerTool.validate(spec, c.arguments).answer.options, List.of(1, 3, 4));
        JsonObject req = JsonParser.parseString(bodies.get(0)).getAsJsonObject();
        Assert.assertEquals(req.getAsJsonObject("questions").size(), 5);
        for (String k : req.getAsJsonObject("questions").keySet())
            Assert.assertEquals(req.getAsJsonObject("questions").getAsJsonObject(k).get("type").getAsString(), "noul");

        reply = q -> nouls(q, 0.1, 0.2, 0.3, 0.45, 0.05);  // none says yes: still the two the decision requires
        c = onlyAnswer(bridge().chat(messages(USER), "[]", true, spec));
        Assert.assertEquals(AnswerTool.validate(spec, c.arguments).answer.options, List.of(4, 3));
    }

    /** The pure selection, including the cancel option, which is taken alone or not at all. */
    @Test
    public void theTopPicksHonourTheCountsAndTakeCancelAlone() {
        Assert.assertEquals(DecisionsBridge.topPicks(new double[] {0.9, 0.8, 0.7}, 0, 2, -1), List.of(1, 2));
        Assert.assertEquals(DecisionsBridge.topPicks(new double[] {0.1, 0.2}, 0, 2, -1), List.of());
        Assert.assertEquals(DecisionsBridge.topPicks(new double[] {0.6, 0.7, 0.95}, 1, 2, 3), List.of(3));
        Assert.assertEquals(DecisionsBridge.topPicks(new double[] {0.6, 0.7, 0.65}, 1, 2, 3), List.of(2, 1));
    }

    /** At most one pick is one choice question (with "none" when none is legal), not independent nouls. */
    @Test
    public void aPickOfAtMostOneIsOneChoiceWithNoneWhenNoneIsLegal() {
        AnswerTool.Spec spec = AnswerTool.Spec.many(2, 0, 1).labels(List.of("Glacial Ray @6", "Lava Spike @7"));
        reply = q -> choice("decision", "0");
        NativeTools.Call c = onlyAnswer(bridge().chat(messages(USER), "[]", true, spec));
        Assert.assertEquals(AnswerTool.validate(spec, c.arguments).answer.options, List.of());
        JsonObject crit = JsonParser.parseString(bodies.get(0)).getAsJsonObject().getAsJsonObject("questions")
                .getAsJsonObject("decision").getAsJsonObject("criteria");
        Assert.assertEquals(crit.keySet(), new java.util.LinkedHashSet<>(List.of("0", "1", "2")));

        AnswerTool.Spec exactlyOne = AnswerTool.Spec.many(3, 1, 1).cancel(3).labels(List.of("a", "b", "CANCEL this play"));
        reply = q -> choice("decision", "3");
        c = onlyAnswer(bridge().chat(messages(USER), "[]", true, exactlyOne));
        Assert.assertEquals(AnswerTool.validate(exactlyOne, c.arguments).answer.options, List.of(3));
        Assert.assertFalse(bodies.get(1).contains("\"0\":"), "none is not legal when one pick is required");
    }

    /** Keep or mulligan is a noul. */
    @Test
    public void keepOrMulliganIsANoul() {
        AnswerTool.Spec spec = AnswerTool.Spec.keep();
        reply = q -> nouls(q, 0.3);
        NativeTools.Call c = onlyAnswer(bridge().chat(messages(USER), "[]", true, spec));
        Assert.assertEquals(AnswerTool.validate(spec, c.arguments).answer.keep, Boolean.FALSE);
        reply = q -> nouls(q, 0.7);
        c = onlyAnswer(bridge().chat(messages(USER), "[]", true, spec));
        Assert.assertEquals(AnswerTool.validate(spec, c.arguments).answer.keep, Boolean.TRUE);
    }

    /** A number from a small range is a choice over the range; a range too wide to list gets no answer, and
     *  nothing is sent. */
    @Test
    public void aSmallNumberRangeIsAChoiceAndAWideOneGetsNoAnswer() {
        AnswerTool.Spec spec = AnswerTool.Spec.number(0, 4);
        reply = q -> choice("decision", "3");
        NativeTools.Call c = onlyAnswer(bridge().chat(messages(USER), "[]", true, spec));
        Assert.assertEquals(AnswerTool.validate(spec, c.arguments).answer.number, Integer.valueOf(3));
        JsonObject crit = JsonParser.parseString(bodies.get(0)).getAsJsonObject().getAsJsonObject("questions")
                .getAsJsonObject("decision").getAsJsonObject("criteria");
        Assert.assertEquals(crit.keySet(), new java.util.LinkedHashSet<>(List.of("0", "1", "2", "3", "4")));

        Assert.assertNull(bridge().chat(messages(USER), "[]", true, AnswerTool.Spec.number(0, 1000)));
        Assert.assertEquals(bodies.size(), 1, "a decision it cannot ask must not be sent");
    }

    /** chooseWideKeywordCost asks for 0..Integer.MAX_VALUE (Squad, Multikicker, Replicate): the range's size overflowed
     *  an int, went negative, passed the cap and listed values until the heap ran out. */
    @Test(timeOut = 10000)
    public void anUnboundedNumberIsRefusedWithoutListingIt() {
        Assert.assertNotNull(DecisionsBridge.compose(AnswerTool.Spec.number(0, Integer.MAX_VALUE)).refusal);
        Assert.assertNotNull(DecisionsBridge.compose(AnswerTool.Spec.number(Integer.MIN_VALUE, Integer.MAX_VALUE)).refusal);
        Assert.assertNull(bridge().chat(messages(USER), "[]", true, AnswerTool.Spec.number(0, Integer.MAX_VALUE)));
        Assert.assertTrue(bodies.isEmpty());
    }

    /** Issue #369: a 402 (credits gone) is the transport failing; a 200 whose answers cannot be read is the model's
     *  reply, and a refused question is never sent. Only the first counts against the game. */
    @Test
    public void onlyATransportFailureIsADeadBridge() {
        AnswerTool.Spec spec = AnswerTool.Spec.one(2).labels(List.of("CAST Lava Spike @4", "PASS (do nothing)"));
        DecisionsBridge b = bridge();
        status = 402;
        reply = q -> "{\"error\":{\"message\":\"This request would exceed your available credits\"}}";
        Assert.assertNull(b.chat(messages(USER), "[]", true, spec));
        Assert.assertTrue(b.lastFailureWasTransport(), "a 402 is the transport failing");
        status = 200;
        reply = q -> "{\"answers\":{\"unknown\":{\"type\":\"noul\"}}}";
        Assert.assertNull(b.chat(messages(USER), "[]", true, spec));
        Assert.assertFalse(b.lastFailureWasTransport(), "malformed answers on a 200 are the model's");
        reply = q -> "not json at all";
        Assert.assertNull(b.chat(messages(USER), "[]", true, spec));
        Assert.assertFalse(b.lastFailureWasTransport(), "an unreadable 200 body is the model's, not an IO error");
        Assert.assertNull(b.chat(messages(USER), "[]", true, AnswerTool.Spec.card()));
        Assert.assertFalse(b.lastFailureWasTransport(), "a question it cannot put is not sent");
        status = 402;
        Assert.assertNull(b.chat(messages(USER), "[]", true, spec));
        Assert.assertNull(b.ask(USER));
        Assert.assertFalse(b.lastFailureWasTransport(), "a text ask is never sent, whatever came before");
        status = 200;
        // A Decisions endpoint that sends no headers within the idle window gave no reply.
        reply = q -> { try { Thread.sleep(3000); } catch (InterruptedException ignore) { } return "{}"; };
        long idle = HttpDeadline.IDLE_MS;
        HttpDeadline.IDLE_MS = 500;
        try {
            Assert.assertNull(b.chat(messages(USER), "[]", true, spec));
            Assert.assertTrue(b.lastFailureWasTransport(), "no headers is no reply");
        } finally {
            HttpDeadline.IDLE_MS = idle;
        }
    }

    /** Naming a card has no closed list of options: no answer, nothing sent, the harness's fixed option follows. */
    @Test
    public void namingACardGetsNoAnswerAndSendsNothing() {
        Assert.assertNull(bridge().chat(messages(USER), "[]", true, AnswerTool.Spec.card()));
        Assert.assertTrue(bodies.isEmpty());
    }

    /** A text ask, or a native ask with no decision attached, cannot be put to a decision model. */
    @Test
    public void anAskWithoutTheDecisionsValuesGetsNoAnswer() {
        DecisionsBridge b = bridge();
        Assert.assertNull(b.ask(USER));
        Assert.assertNull(b.chat(messages(USER), "[]", true));
        Assert.assertTrue(bodies.isEmpty());
    }

    /** Blocks: one choice per blocker (an attacker, or no block), answered in parallel. */
    @Test
    public void eachBlockerIsOneChoiceOverTheAttackersOrNoBlock() {
        AnswerTool.Spec spec = AnswerTool.Spec.blocks(List.of("Wall", "Bear"), List.of("Goblin", "Ogre"));
        reply = q -> "{\"answers\":{\"blocker1\":{\"type\":\"choice\",\"choice\":\"2\"},"
                + "\"blocker2\":{\"type\":\"choice\",\"choice\":\"0\"}},\"usage\":{\"input_tokens\":10,\"output_tokens\":0}}";
        NativeTools.Call c = onlyAnswer(bridge().chat(messages(USER), "[]", true, spec));
        List<int[]> blocks = AnswerTool.validate(spec, c.arguments).answer.blocks;
        Assert.assertEquals(blocks.size(), 1);
        Assert.assertEquals(blocks.get(0), new int[] {1, 2});
        JsonObject qs = JsonParser.parseString(bodies.get(0)).getAsJsonObject().getAsJsonObject("questions");
        Assert.assertEquals(qs.keySet(), new java.util.LinkedHashSet<>(List.of("blocker1", "blocker2")));
        Assert.assertEquals(qs.getAsJsonObject("blocker1").getAsJsonObject("criteria").keySet(),
                new java.util.LinkedHashSet<>(List.of("0", "1", "2")));
    }

    /** Modes that may repeat: one choice per mode of how many times to pick it, so a repeat and a total above the
     *  number of modes are both answers it can give; the total is held within the decision's bounds. */
    @Test
    public void repeatableModesAreAskedAsACountPerMode() {
        AnswerTool.Spec spec = AnswerTool.Spec.repeat(2, 3, 3).labels(List.of("Deal 1 damage", "Gain 2 life"));
        reply = q -> {
            JsonObject qs = q.getAsJsonObject("questions");
            Assert.assertEquals(qs.keySet(), new java.util.LinkedHashSet<>(List.of("count1", "count2")), qs.toString());
            Assert.assertEquals(qs.getAsJsonObject("count1").getAsJsonObject("criteria").keySet(),
                    new java.util.LinkedHashSet<>(List.of("0", "1", "2", "3")));
            return "{\"answers\":{\"count1\":{\"type\":\"choice\",\"choice\":\"2\",\"probabilities\":{\"2\":0.7}},"
                    + "\"count2\":{\"type\":\"choice\",\"choice\":\"1\",\"probabilities\":{\"1\":0.6}}}}";
        };
        NativeTools.Call c = onlyAnswer(bridge().chat(messages(USER), "[]", true, spec));
        Assert.assertEquals(AnswerTool.validate(spec, c.arguments).answer.options, List.of(1, 1, 2));
        // A limit the site checks (a pawprint charm's {P} budget, #415) is stated in each count question; none is
        // stated when there is none.
        AnswerTool.Spec paw = AnswerTool.Spec.repeat(3, 0, 5).labels(List.of("{P} — A", "{P}{P} — B", "{P}{P}{P} — C"))
                .limit("the picked options' {P} total at most 5");
        Assert.assertTrue(DecisionsBridge.compose(paw).questions.toString()
                .contains("more than once; the picked options' {P} total at most 5. How many"));
        Assert.assertFalse(DecisionsBridge.compose(spec).questions.toString().contains("total at most"));
        // Too many: the surer counts are kept. Too few: the likeliest mode is repeated up to the minimum.
        Assert.assertEquals(DecisionsBridge.repeatPicks(new int[] {3, 2}, new double[] {0.9, 0.4}, 1, 3), List.of(1, 1, 1));
        Assert.assertEquals(DecisionsBridge.repeatPicks(new int[] {0, 1}, new double[] {0.9, 0.8}, 3, 3), List.of(2, 2, 2));
    }

    /** A blocker that may block several attackers gets a yes/no per attacker, so it can block two. */
    @Test
    public void aBlockerThatCanBlockSeveralIsAskedPerAttacker() {
        AnswerTool.Spec spec = AnswerTool.Spec.blocks(List.of("Hundred-Handed One", "Bear"), List.of("Goblin", "Ogre"),
                List.of(2, 1));
        reply = q -> {
            JsonObject qs = q.getAsJsonObject("questions");
            Assert.assertEquals(qs.keySet(), new java.util.LinkedHashSet<>(List.of("block1_1", "block1_2", "blocker2")),
                    qs.toString());
            return "{\"answers\":{\"block1_1\":{\"type\":\"noul\",\"noul\":0.8},\"block1_2\":{\"type\":\"noul\",\"noul\":0.7},"
                    + "\"blocker2\":{\"type\":\"choice\",\"choice\":\"0\"}}}";
        };
        NativeTools.Call c = onlyAnswer(bridge().chat(messages(USER), "[]", true, spec));
        List<int[]> blocks = AnswerTool.validate(spec, c.arguments).answer.blocks;
        Assert.assertEquals(blocks.size(), 2, c.arguments);
        Assert.assertEquals(blocks.get(0), new int[] {1, 1});
        Assert.assertEquals(blocks.get(1), new int[] {1, 2});
    }

    /** Dividing an amount among a few targets is a choice over every split; a split that cannot be listed gets none. */
    @Test
    public void aSmallDivisionIsAChoiceOverEverySplit() {
        AnswerTool.Spec spec = AnswerTool.Spec.alloc(2, 3).labels(List.of("Opponent", "Goblin"));
        reply = q -> {
            JsonObject crit = q.getAsJsonObject("questions").getAsJsonObject("decision").getAsJsonObject("criteria");
            Assert.assertEquals(crit.size(), 4, crit.toString());   // 3+0, 2+1, 1+2, 0+3
            for (String k : crit.keySet()) if (crit.get(k).getAsString().startsWith("2 to Opponent")) return choice("decision", k);
            return "{}";
        };
        NativeTools.Call c = onlyAnswer(bridge().chat(messages(USER), "[]", true, spec));
        Assert.assertEquals(AnswerTool.validate(spec, c.arguments).answer.amounts, List.of(2, 1));
        Assert.assertNull(bridge().chat(messages(USER), "[]", true, AnswerTool.Spec.alloc(6, 20).labels(
                List.of("a", "b", "c", "d", "e", "f"))));
    }

    // ------------------------------------------------------------------ failures

    /** A non-200 is no answer (the seat's retry, then its fixed legal option). */
    @Test
    public void aNon200GivesNoAnswer() {
        status = 503;
        reply = q -> "{\"error\":{\"message\":\"overloaded\"}}";
        Assert.assertNull(bridge().chat(messages(USER), "[]", true, AnswerTool.Spec.one(2).labels(List.of("a", "b"))));
        Assert.assertEquals(bodies.size(), 1);
    }

    /** An answer that is not the question's type, is missing, or names an option the criteria do not have, is no answer. */
    @Test
    public void aMalformedAnswerOrAnOptionNotInTheCriteriaGivesNoAnswer() {
        AnswerTool.Spec spec = AnswerTool.Spec.one(2).labels(List.of("a", "b"));
        for (String bad : new String[] {
                choice("decision", "7"),
                choice("other", "1"),
                "{\"answers\":{\"decision\":{\"type\":\"noul\",\"noul\":0.9}}}",
                "not json",
                "{\"answers\":{}}"}) {
            reply = q -> bad;
            Assert.assertNull(bridge().chat(messages(USER), "[]", true, spec), bad);
        }
        reply = q -> "{\"answers\":{\"option1\":{\"type\":\"noul\",\"noul\":\"yes\"}}}";
        Assert.assertNull(bridge().chat(messages(USER), "[]", true, AnswerTool.Spec.many(2, 0, 2).labels(List.of("a", "b"))));
    }

    // ------------------------------------------------------------------ egress, key, bill

    /** A loopback endpoint never receives the key, even when the shell carries one. */
    @Test
    public void noApiKeyIsSentToALoopbackEndpoint() {
        reply = q -> choice("decision", "1");
        bridge("sk-or-test-not-a-key").chat(messages(USER), "[]", true, AnswerTool.Spec.one(2).labels(List.of("a", "b")));
        Assert.assertEquals(auths, List.of(""));
    }

    /** A real endpoint needs the owner's egress consent, enforced at construction as for OpenRouterBridge. The consent
     *  is passed in, so the refusal is seen whatever this shell carries; nothing is sent either way. */
    @Test
    public void aNonLoopbackEndpointNeedsEgressConsent() {
        for (String consent : new String[] {null, "", "0", "yes"}) {
            try {
                new DecisionsBridge("Seat/smart", "typesafe/jev-1.14", "p", "https://openrouter.ai/api/v1", 5000, "sk-or-x", consent);
                Assert.fail("constructed a bridge to openrouter.ai with consent " + consent);
            } catch (IllegalStateException e) {
                Assert.assertTrue(e.getMessage().contains("OPENROUTER_ALLOW_EGRESS"), e.getMessage());
            }
        }
        // A loopback endpoint needs no consent and no key.
        new DecisionsBridge("Seat/smart", "typesafe/jev-1.14", "p", base, 5000, null, null);
        // The factory's path checks consent first, whatever else the seat is refused for (acceptance runs the tests with
        // MEMORY_TOOLS=1, which a jev: seat refuses: that refusal must not hide a missing consent).
        for (boolean memoryTools : new boolean[] {false, true}) {
            try {
                LlmSpikeGui.LLMBridgeFactory.decisions("Seat/smart", DecisionsBridge.PREFIX + "typesafe/jev-1.14", "p",
                        "https://openrouter.ai/api/v1", "sk-or-x", null, memoryTools, false, true);
                Assert.fail("the factory built a jev: seat without consent");
            } catch (IllegalStateException e) {
                Assert.assertTrue(e.getMessage().contains("OPENROUTER_ALLOW_EGRESS"), e.getMessage());
            }
        }
    }

    /** The factory makes a jev: spec a DecisionsBridge (checked on a loopback base, where nothing needs consent). */
    @Test
    public void theFactoryRoutesAJevSpecToTheDecisionsBridge() {
        LLMBridge b = LlmSpikeGui.LLMBridgeFactory.decisions("Seat/smart", DecisionsBridge.PREFIX + "typesafe/jev-1.14", "p",
                base, null, null, false, false, true);
        Assert.assertTrue(b instanceof DecisionsBridge, String.valueOf(b));
        Assert.assertEquals(b.getModel(), "typesafe/jev-1.14");
        try {
            LlmSpikeGui.LLMBridgeFactory.decisions("Seat/smart", DecisionsBridge.PREFIX + "typesafe/jev-1.14", "p", base,
                    null, null, true, false, true);
            Assert.fail("a jev: seat was built with MEMORY_TOOLS on");
        } catch (IllegalStateException e) {
            Assert.assertTrue(e.getMessage().contains("MEMORY_TOOLS"), e.getMessage());
        }
    }

    /** Without the answer tool (ANSWER_TOOL=0 or NATIVE_TOOLS=0) a jev: seat is never offered `answer`, so every
     *  decision would be a text ask it cannot take: lost, silently. It is refused when built. */
    @Test
    public void aJevSeatWithoutTheAnswerToolIsRefused() {
        try {
            LlmSpikeGui.LLMBridgeFactory.decisions("Seat/smart", DecisionsBridge.PREFIX + "typesafe/jev-1.14", "p", base,
                    null, null, false, false, false);
            Assert.fail("a jev: seat was built with the answer tool off");
        } catch (IllegalStateException e) {
            Assert.assertTrue(e.getMessage().contains("ANSWER_TOOL") && e.getMessage().contains("NATIVE_TOOLS"), e.getMessage());
        }
    }

    /** The Decisions endpoint sits beside the chat endpoint: OPENROUTER_BASE_URL's /v1 becomes /alpha/decisions. */
    @Test
    public void theDecisionsEndpointIsDerivedFromTheOpenRouterBase() {
        Assert.assertEquals(DecisionsBridge.endpoint("https://openrouter.ai/api/v1"), "https://openrouter.ai/api/alpha/decisions");
        Assert.assertEquals(DecisionsBridge.endpoint("http://127.0.0.1:8/api/v1/"), "http://127.0.0.1:8/api/alpha/decisions");
        Assert.assertEquals(DecisionsBridge.endpoint(null), "https://openrouter.ai/api/alpha/decisions");
    }

    /** The bill: input and output tokens as the seat's prompt and completion tokens (cost_report prices them), and the
     *  provider's own usage.cost summed beside them. */
    @Test
    public void usageIsRecorded() {
        reply = q -> choice("decision", "1");
        DecisionsBridge b = bridge();
        AnswerTool.Spec spec = AnswerTool.Spec.one(2).labels(List.of("a", "b"));
        b.chat(messages(USER), "[]", true, spec);
        b.chat(messages(USER), "[]", true, spec);
        Assert.assertEquals(b.getLastPromptTokens(), 476);
        Assert.assertEquals(b.getLastCompletionTokens(), 70);
        Assert.assertEquals(b.getBilledPromptTokens(), 952L);
        Assert.assertEquals(b.getBilledCompletionTokens(), 140L);
        Assert.assertEquals(b.getCostUsd(), 2 * 0.000019992, 1e-12);
        Assert.assertEquals(b.getLastProvider(), "TypeSafe");
        Assert.assertEquals(b.getCalls(), 2);
        Assert.assertEquals(b.getLastCostUsd(), 0.000019992, 1e-12);
        // The context_call row carries the provider's own figure for the call.
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.costField(b), ",\"cost_usd\":0.000019992");
    }

    /** A call that fails after one that answered leaves no usage of its own: its row must not repeat the last bill. */
    @Test
    public void aFailedCallReportsNoUsageOfItsOwn() {
        reply = q -> choice("decision", "1");
        DecisionsBridge b = bridge();
        AnswerTool.Spec spec = AnswerTool.Spec.one(2).labels(List.of("a", "b"));
        b.chat(messages(USER), "[]", true, spec);
        status = 503;
        Assert.assertNull(b.chat(messages(USER), "[]", true, spec));
        Assert.assertEquals(b.getLastPromptTokens(), -1);
        Assert.assertEquals(b.getLastCompletionTokens(), -1);
        Assert.assertEquals(b.getLastCostUsd(), -1.0);
        Assert.assertEquals(LlmSpikeGui.PlayerControllerLLM.costField(b), "");
        Assert.assertEquals(b.getBilledPromptTokens(), 476L);
    }

    /** The [JEV] line says how large the state was, so a prompt's size can be read from the log. */
    @Test
    public void theLogLineGivesTheStateSize() {
        reply = q -> choice("decision", "1");
        String log = LineFindingProbeTest.logOf(() -> bridge().chat(messages(USER), "[]", true,
                AnswerTool.Spec.one(2).labels(List.of("a", "b"))));
        Assert.assertTrue(log.matches("(?s).*\\[JEV Seat/smart\\].*state_bytes=\\d+.*"), log);
    }

    // ------------------------------------------------------------------ the seat's conditions

    /** Jev cannot call a lookup, so a seat that offers them would be a condition it never had; without the answer
     *  tool every decision would be a text ask it cannot take. */
    @Test
    public void aJevSeatRefusesLookupsLookaheadAndTextAnswers() {
        LlmSpikeGui.LLMBridgeFactory.refuseAnswerOnly(DecisionsBridge.PREFIX, false, false, true, "Seat/smart");
        String[] named = {"MEMORY_TOOLS", "LOOKAHEAD", "ANSWER_TOOL"};
        boolean[][] flags = {{true, false, true}, {false, true, true}, {false, false, false}};
        for (int i = 0; i < flags.length; i++) {
            boolean[] f = flags[i];
            try {
                LlmSpikeGui.LLMBridgeFactory.refuseAnswerOnly(DecisionsBridge.PREFIX, f[0], f[1], f[2], "Seat/smart");
                Assert.fail("accepted " + Arrays.toString(f));
            } catch (IllegalStateException e) {
                Assert.assertTrue(e.getMessage().contains(named[i]), e.getMessage());
            }
        }
    }

    /** A jev: seat answers through `answer` (the stamp and the preamble say so); it gets no native lookups. */
    @Test
    public void aJevSeatAnswersThroughTheAnswerToolWithoutLookups() {
        Assert.assertEquals(DecisionsBridge.VERSION, "decisions-bridge-v2", "move this pin WITH DecisionsBridge.VERSION");
        String spec = DecisionsBridge.PREFIX + "typesafe/jev-1.14";
        Assert.assertTrue(AnswerTool.usedBy(spec));
        Assert.assertFalse(NativeTools.usedBy(spec));
        Assert.assertTrue(bridge().supportsNativeTools(), "the seat offers `answer` only to a bridge that takes a call");
    }

    /** Through the live controller: the seat hands the decision's own legal values to the bridge (no prompt text is
     *  parsed), the play menu and the sacrifice cost each go out as one choice, and what Jev chose is what happens. */
    @Test
    public void theLiveSeatAsksJevWithEachDecisionsOwnOptions() {
        new LineFindingProbeTest().initializeModel();
        Game g = LineFindingProbeTest.bareGame(PhaseType.MAIN1);
        Player me = g.getPlayers().get(0);
        LineFindingProbeTest.card("Goblin Bombardment", me, ZoneType.Battlefield);
        Card angel = LineFindingProbeTest.card("Serra Angel", me, ZoneType.Battlefield);
        Card bears = LineFindingProbeTest.card("Grizzly Bears", me, ZoneType.Battlefield);
        List<String> asked = new ArrayList<>();
        // Activate the Bombardment, sacrifice the Bears, aim at the opponent: each picked by its label.
        reply = q -> {
            JsonObject crit = q.getAsJsonObject("questions").getAsJsonObject("decision").getAsJsonObject("criteria");
            asked.add(crit.toString());
            for (String want : new String[] {"Goblin Bombardment", "Grizzly Bears", "Rival"})
                for (String k : crit.keySet()) if (crit.get(k).getAsString().contains(want)) return choice("decision", k);
            return choice("decision", crit.keySet().iterator().next());
        };
        DecisionsBridge b = bridge();
        LlmSpikeGui.PlayerControllerLLM ctrl = new LlmSpikeGui.PlayerControllerLLM(g, me,
                new forge.ai.LobbyPlayerAi("Seat", null), "Seat", "", b, b, "llm");
        me.dangerouslySetController(ctrl);

        List<SpellAbility> chosen = ctrl.chooseSpellAbilityToPlay();
        Assert.assertNotNull(chosen, "the seat passed instead of activating: " + asked);
        ctrl.playChosenSpellAbility(chosen.get(0));
        Assert.assertTrue(asked.size() >= 2, asked.toString());
        Assert.assertFalse(bears.isInZone(ZoneType.Battlefield), "the creature Jev chose was not sacrificed: " + asked);
        Assert.assertTrue(angel.isInZone(ZoneType.Battlefield), asked.toString());
        Assert.assertEquals(ctrl.aiDelegationsCount(), 0);
        Assert.assertEquals(ctrl.textParsesOnToolAsks, 0);
    }

    private static String read(HttpExchange ex) throws IOException {
        try (InputStream in = ex.getRequestBody()) { return new String(in.readAllBytes(), StandardCharsets.UTF_8); }
    }

    private static void respond(HttpExchange ex, int code, String json) throws IOException {
        byte[] b = json.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().add("Content-Type", "application/json");
        ex.sendResponseHeaders(code, b.length);
        try (OutputStream os = ex.getResponseBody()) { os.write(b); }
    }
}
