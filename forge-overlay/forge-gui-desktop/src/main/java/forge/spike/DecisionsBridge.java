package forge.spike;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.function.Function;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.google.gson.JsonPrimitive;

/**
 * A seat played by a decision model on OpenRouter's Decisions API (`jev:<model>` specs, first `typesafe/jev-1.14`).
 * A decision model writes no text and calls no tool: it answers typed questions (a `choice` among keyed options, a
 * `noul` yes/no probability) about a `state`. Owner decision 2026-10-24: its typed answer is turned into the
 * harness's own `answer` call, so AnswerTool validates it, the row records it and the fallback applies, exactly as
 * for a model that calls (docs/harness/ARCHITECTURE.md, "The Decisions bridge").
 *
 * <p>Each ask is one request: the state is the seat's system preamble and the ask's messages as the harness renders
 * them; the questions come from the decision's typed legal values ({@link AnswerTool.Spec}), never from the prompt
 * text. A decision with no closed option list (a card name, a wide number range, a large division) is not sent: the
 * bridge gives no answer, says so in the log, and the seat takes its fixed legal option (never Forge's AI).
 *
 * <p>What it cannot do: look anything up (the seat runs without MEMORY_TOOLS and LOOKAHEAD, refused at construction),
 * state a plan, yield, or write a reason. Egress, key and loopback rules are OpenRouterBridge's.
 */
final class DecisionsBridge implements LLMBridge {
    static final String PREFIX = "jev:";
    /** v1 (2026-10-24): one request per ask; choice / noul composition per decision kind. Stamped on the game line of a
     *  game with a jev: seat and on a jev: solver's puzzle row (eval/versions.py); pinned in DecisionsBridgeTest. */
    static final String VERSION = "decisions-bridge-v1";
    /** The most values a number or a division is listed as. A wider one is not asked (no answer, logged). */
    static final int MAX_LISTED = 40;
    private static final long TIMEOUT_MS = parseLong(System.getenv("OPENROUTER_TIMEOUT_MS"), 120000L);

    private final String label, model, preamble, endpoint, apiKey;
    private final long timeoutMs;
    private final HttpClient client;
    private long totalMs = 0;
    private int calls = 0;
    private volatile int lastPromptTokens = -1, lastCompletionTokens = -1;
    private volatile long billedPromptTokens = 0, billedCompletionTokens = 0;
    private volatile double costUsd = 0;
    private volatile String lastProvider;

    /** Production: OPENROUTER_API_KEY and OPENROUTER_ALLOW_EGRESS from the environment. */
    DecisionsBridge(String label, String model, String preamble, String baseUrl) {
        this(label, model, preamble, baseUrl, TIMEOUT_MS, System.getenv("OPENROUTER_API_KEY"),
                System.getenv("OPENROUTER_ALLOW_EGRESS"));
    }

    /** Test seam: every input passed in, so the egress refusal is reachable whatever the shell carries. */
    DecisionsBridge(String label, String model, String preamble, String baseUrl, long timeoutMs, String apiKey, String consent) {
        LlmSpikeGui.LLMBridgeFactory.requireEgressConsent(baseUrl, consent, label);
        if ((apiKey == null || apiKey.isEmpty()) && !LlmSpikeGui.LLMBridgeFactory.isLoopbackEndpoint(baseUrl))
            throw new IllegalStateException("OPENROUTER_API_KEY env var is required for the Decisions backend (" + label + ")");
        this.label = label;
        this.model = model;
        this.preamble = preamble;
        this.endpoint = endpoint(baseUrl);
        this.apiKey = apiKey;
        this.timeoutMs = timeoutMs;
        this.client = HttpClient.newBuilder().connectTimeout(Duration.ofMillis(timeoutMs)).build();
    }

    static boolean isSpec(String spec) { return spec != null && spec.startsWith(PREFIX); }

    /** The Decisions endpoint beside the chat one: `<base without /v1>/alpha/decisions` (OpenRouter serves chat at
     *  /api/v1 and decisions at /api/alpha/decisions). Unset is the real openrouter.ai, as for OpenRouterBridge. */
    static String endpoint(String baseUrl) {
        String b = baseUrl == null || baseUrl.trim().isEmpty() ? "https://openrouter.ai/api/v1" : baseUrl.trim();
        while (b.endsWith("/")) b = b.substring(0, b.length() - 1);
        if (b.endsWith("/v1")) b = b.substring(0, b.length() - 3);
        return b + "/alpha/decisions";
    }

    /** A decision model cannot call a lookup: a seat offered them would record a condition it never had. */
    static void refuseLookups(boolean memoryTools, boolean lookahead, String label) {
        if (memoryTools) throw new IllegalStateException("a " + PREFIX + " seat cannot call lookups: run it with "
                + "MEMORY_TOOLS=0 (no --memory-tools) (" + label + ")");
        if (lookahead) throw new IllegalStateException("a " + PREFIX + " seat cannot call simulate: run it with "
                + "LOOKAHEAD=0 (no --lookahead) (" + label + ")");
    }

    /** The seat offers `answer` only to a bridge that answers by call; this one's answer is always that call. */
    @Override public boolean supportsNativeTools() { return true; }

    /** A text ask has no typed options to put to the model. */
    @Override public String ask(String prompt) {
        noAnswer("a text ask (no `answer` offered)");
        return null;
    }

    @Override public NativeTools.Turn chat(String messagesJson, String toolsJson, boolean allowCalls) {
        noAnswer("an ask without the decision's typed options");
        return null;
    }

    @Override public NativeTools.Turn chat(String messagesJson, String toolsJson, boolean allowCalls, AnswerTool.Spec spec) {
        if (spec == null) return chat(messagesJson, toolsJson, allowCalls);
        Composed q = compose(spec);
        if (q.refusal != null) { noAnswer(spec.kind + " (" + q.refusal + ")"); return null; }
        long t0 = System.currentTimeMillis();
        JsonObject body = new JsonObject();
        body.addProperty("model", model);
        body.addProperty("state", state(messagesJson));
        body.add("questions", q.questions);
        JsonObject resp = send(body.toString());
        totalMs += System.currentTimeMillis() - t0;
        if (resp == null) return null;
        JsonObject args;
        try {
            JsonElement ans = resp.get("answers");
            args = ans != null && ans.isJsonObject() ? q.read.apply(ans.getAsJsonObject()) : null;
        } catch (RuntimeException e) {
            args = null;
        }
        if (args == null) {
            System.out.println("[JEV " + label + "] [JEV-NO-ANSWER] malformed answers for " + spec.kind + ": "
                    + LlmSpikeGui.oneLine(String.valueOf(resp.get("answers"))));
            return null;
        }
        calls++;
        System.out.println("[JEV " + label + "] " + VERSION + " " + spec.kind + " -> answer " + args + " (input_tokens="
                + lastPromptTokens + ", cost so far $" + String.format(java.util.Locale.ROOT, "%.6f", costUsd) + ")");
        List<NativeTools.Call> one = new ArrayList<>();
        one.add(new NativeTools.Call("jev" + calls, AnswerTool.NAME, args.toString()));
        return NativeTools.Turn.ofCalls(one);
    }

    private void noAnswer(String why) {
        System.out.println("[JEV " + label + "] [JEV-NO-ANSWER] " + why + ": not put to a decision model -> "
                + "no answer; the seat takes its fixed legal option");
    }

    /** The state: the system preamble, then each message's text in order (the decision, and on a correction round
     *  the harness's reason the last answer was refused). */
    private String state(String messagesJson) {
        StringBuilder b = new StringBuilder(preamble == null ? "" : preamble);
        try {
            for (JsonElement m : JsonParser.parseString(messagesJson).getAsJsonArray()) {
                JsonElement c = m.isJsonObject() ? m.getAsJsonObject().get("content") : null;
                if (c == null || !c.isJsonPrimitive()) continue;
                if (b.length() > 0) b.append("\n\n");
                b.append(c.getAsString());
            }
        } catch (RuntimeException e) {
            b.append("\n\n").append(messagesJson);
        }
        return b.toString();
    }

    // ------------------------------------------------------------------ composing the questions

    /** The questions for one decision and how their answers become `answer` arguments; or why it cannot be asked. */
    static final class Composed {
        final JsonObject questions = new JsonObject();
        Function<JsonObject, JsonObject> read;
        String refusal;
        static Composed refused(String why) { Composed c = new Composed(); c.refusal = why; return c; }
    }

    private static final String GAME = "The state is a Magic: The Gathering game as the player sees it; it ends with ";
    private static final String SITUATION = GAME + "one decision and its numbered legal options.";

    static Composed compose(AnswerTool.Spec s) {
        Composed c = new Composed();
        switch (s.kind) {
            case ONE: {
                if (s.labels.size() != s.n) return Composed.refused("its options have no labels");
                c.questions.add("decision", choiceQuestion(SITUATION + " Which option should the player take?", s.labels, false));
                c.read = a -> args("option", new JsonPrimitive(choiceOf(a, "decision", 1, s.n)));
                return c;
            }
            case MANY:
            case MANY_REPEAT: {
                if (s.labels.size() != s.n) return Composed.refused("its options have no labels");
                int max = Math.min(s.max, s.n);
                if (max <= 1) {
                    // At most one pick: one choice among them (and "none" where none is legal), not n independent nouls.
                    c.questions.add("decision", choiceQuestion(SITUATION + " The player picks " + (s.min == 0 ? "at most" : "exactly")
                            + " one of them. Which?", s.labels, s.min == 0));
                    c.read = a -> {
                        int k = choiceOf(a, "decision", s.min == 0 ? 0 : 1, s.n);
                        JsonArray picks = new JsonArray();
                        if (k > 0) picks.add(k);
                        return args("options", picks);
                    };
                    return c;
                }
                for (int i = 1; i <= s.n; i++) {
                    JsonObject crit = new JsonObject();
                    crit.addProperty("true", "Pick option " + i + ": " + s.labels.get(i - 1));
                    crit.addProperty("false", "Do not pick option " + i + ".");
                    c.questions.add("option" + i, question("noul", SITUATION + " The player picks between " + s.min + " and "
                            + max + " of them" + (s.cancel > 0 ? " (option " + s.cancel + " cancels and is picked alone)" : "")
                            + "; where the picks are listed in order, the likeliest comes first. Should option " + i
                            + " be one of the picks?", crit));
                }
                c.read = a -> {
                    double[] p = new double[s.n];
                    for (int i = 1; i <= s.n; i++) p[i - 1] = noulOf(a, "option" + i);
                    JsonArray picks = new JsonArray();
                    for (int k : topPicks(p, s.min, max, s.cancel)) picks.add(k);
                    return args("options", picks);
                };
                return c;
            }
            case KEEP: {
                JsonObject crit = new JsonObject();
                crit.addProperty("true", "Keep this hand.");
                crit.addProperty("false", "Mulligan: shuffle it away and draw a new hand.");
                c.questions.add("keep", question("noul", GAME + "the player's opening hand. Should the player keep this hand?", crit));
                c.read = a -> args("keep", new JsonPrimitive(noulOf(a, "keep") >= 0.5));
                return c;
            }
            case NUMBER: {
                if (s.max - s.min + 1 > MAX_LISTED)
                    return Composed.refused("a number from " + s.min + " through " + s.max + " has more than " + MAX_LISTED + " values");
                JsonObject crit = new JsonObject();
                for (int v = s.min; v <= s.max; v++) crit.addProperty(Integer.toString(v), "The number " + v + ".");
                c.questions.add("decision", question("choice", GAME + "a decision that asks for a number from " + s.min
                        + " through " + s.max + ". Which number?", crit));
                c.read = a -> args("number", new JsonPrimitive(choiceOf(a, "decision", s.min, s.max)));
                return c;
            }
            case BLOCKS: {
                for (int b = 1; b <= s.n; b++) {
                    JsonObject crit = new JsonObject();
                    crit.addProperty("0", "Do not block with " + s.labels.get(b - 1) + ".");
                    for (int at = 1; at <= s.m; at++) crit.addProperty(Integer.toString(at), "Block " + s.labels2.get(at - 1) + ".");
                    c.questions.add("blocker" + b, question("choice", GAME + "the player's declare-blockers decision. Which "
                            + "attacker, if any, should the player's blocker " + s.labels.get(b - 1) + " block? Every blocker "
                            + "is decided at once.", crit));
                }
                c.read = a -> {
                    JsonArray blocks = new JsonArray();
                    for (int b = 1; b <= s.n; b++) {
                        int at = choiceOf(a, "blocker" + b, 0, s.m);
                        if (at == 0) continue;
                        JsonObject pair = new JsonObject();
                        pair.addProperty("blocker", b);
                        pair.addProperty("attacker", at);
                        blocks.add(pair);
                    }
                    return args("blocks", blocks);
                };
                return c;
            }
            case ALLOC: {
                if (s.labels.size() != s.n) return Composed.refused("its targets have no labels");
                List<int[]> splits = new ArrayList<>();
                if (!splits(s.n, new int[s.n], 0, s.max, splits))
                    return Composed.refused(s.max + " divided among " + s.n + " targets has more than " + MAX_LISTED + " splits");
                List<String> labels = new ArrayList<>();
                for (int[] sp : splits) {
                    StringBuilder b = new StringBuilder();
                    for (int i = 0; i < s.n; i++) b.append(i == 0 ? "" : ", ").append(sp[i]).append(" to ").append(s.labels.get(i));
                    labels.add(b.toString());
                }
                c.questions.add("decision", choiceQuestion(SITUATION + " The decision divides " + s.max + " among "
                        + s.n + " targets. Which division?", labels, false));
                c.read = a -> {
                    JsonArray am = new JsonArray();
                    for (int x : splits.get(choiceOf(a, "decision", 1, splits.size()) - 1)) am.add(x);
                    return args("amounts", am);
                };
                return c;
            }
            case CARD:
                return Composed.refused("naming a card has no closed list of options");
            default:
                return Composed.refused("no mapping");
        }
    }

    /** Every way to divide {@code left} among the slots from {@code i}; false once there would be more than MAX_LISTED. */
    private static boolean splits(int n, int[] cur, int i, int left, List<int[]> out) {
        if (i == n - 1) {
            if (out.size() >= MAX_LISTED) return false;
            cur[i] = left;
            out.add(cur.clone());
            return true;
        }
        for (int v = left; v >= 0; v--) {
            cur[i] = v;
            if (!splits(n, cur, i + 1, left - v, out)) return false;
        }
        return true;
    }

    private static JsonObject question(String type, String instructions, JsonObject criteria) {
        JsonObject q = new JsonObject();
        q.addProperty("type", type);
        q.addProperty("instructions", instructions);
        q.add("criteria", criteria);
        return q;
    }

    /** A choice question over 1-based options ("0" for none when {@code none}). */
    private static JsonObject choiceQuestion(String instructions, List<String> labels, boolean none) {
        JsonObject crit = new JsonObject();
        if (none) crit.addProperty("0", "None of them.");
        for (int i = 0; i < labels.size(); i++) crit.addProperty(Integer.toString(i + 1), labels.get(i));
        return question("choice", instructions, crit);
    }

    /** The option key a choice answer took, as a number in [lo, hi]; throws (no answer) when it is not one of them. */
    private static int choiceOf(JsonObject answers, String key, int lo, int hi) {
        JsonObject a = answers.getAsJsonObject(key);
        if (a == null || !"choice".equals(a.get("type").getAsString())) throw new IllegalArgumentException("no choice " + key);
        int k = Integer.parseInt(a.get("choice").getAsString().trim());
        if (k < lo || k > hi) throw new IllegalArgumentException("choice " + k + " is not in the criteria");
        return k;
    }

    /** A noul answer's probability of yes; throws (no answer) when it is missing or not a probability. */
    private static double noulOf(JsonObject answers, String key) {
        JsonObject a = answers.getAsJsonObject(key);
        if (a == null || !"noul".equals(a.get("type").getAsString())) throw new IllegalArgumentException("no noul " + key);
        JsonPrimitive v = a.getAsJsonPrimitive("noul");
        if (v == null || !v.isNumber()) throw new IllegalArgumentException("noul " + v + " is not a number");
        double p = v.getAsDouble();
        if (!(p >= 0 && p <= 1)) throw new IllegalArgumentException("noul " + p + " is not a probability");
        return p;
    }

    private static JsonObject args(String field, JsonElement v) {
        JsonObject o = new JsonObject();
        o.add(field, v);
        return o;
    }

    /**
     * The 1-based picks from each option's probability of yes ({@code p[0]} is option 1): every option at 0.5 or above,
     * likeliest first, but never fewer than {@code min} or more than {@code max}. The cancel option (1-based, or -1) is
     * picked alone when it is at 0.5 or above and the likeliest of all; otherwise it is never picked.
     */
    static List<Integer> topPicks(double[] p, int min, int max, int cancel) {
        List<Integer> cands = new ArrayList<>();
        for (int i = 1; i <= p.length; i++) if (i != cancel) cands.add(i);
        cands.sort(Comparator.comparingDouble((Integer i) -> -p[i - 1]).thenComparingInt(i -> i));
        if (cancel >= 1 && cancel <= p.length && p[cancel - 1] >= 0.5
                && (cands.isEmpty() || p[cancel - 1] >= p[cands.get(0) - 1])) return List.of(cancel);
        int yes = 0;
        for (int i : cands) if (p[i - 1] >= 0.5) yes++;
        int k = Math.max(min, Math.min(yes, max));
        return new ArrayList<>(cands.subList(0, Math.min(k, cands.size())));
    }

    // ------------------------------------------------------------------ the request

    /** One POST; the parsed body, or null (non-200, timeout, unparseable), logged. Usage is billed when present. */
    private JsonObject send(String body) {
        try {
            HttpRequest.Builder rb = HttpRequest.newBuilder().uri(URI.create(endpoint))
                    .header("Content-Type", "application/json");
            // Never a key to loopback (OpenRouterBridge's rule, LoopbackKeyTest).
            String auth = LlmSpikeGui.OpenRouterBridge.authorizationHeader(apiKey, endpoint);
            if (auth != null) rb.header("Authorization", auth);
            HttpRequest req = rb.POST(HttpRequest.BodyPublishers.ofString(body, StandardCharsets.UTF_8))
                    .timeout(Duration.ofMillis(timeoutMs)).build();
            HttpResponse<String> resp = HttpDeadline.send(client, req, HttpResponse.BodyHandlers.ofString(), timeoutMs);
            if (resp == null) {
                System.out.println("[JEV " + label + "] [JEV-NO-ANSWER] no response within " + timeoutMs + " ms ("
                        + HttpDeadline.LAST_STOP.get() + ")");
                return null;
            }
            if (resp.statusCode() != 200) {
                System.out.println("[JEV " + label + "] [JEV-NO-ANSWER] HTTP " + resp.statusCode() + ": " + LlmSpikeGui.oneLine(resp.body()));
                return null;
            }
            JsonObject o = JsonParser.parseString(resp.body()).getAsJsonObject();
            bill(o);
            return o;
        } catch (Exception e) {
            System.out.println("[JEV " + label + "] [JEV-NO-ANSWER] error: " + e);
            return null;
        }
    }

    /** usage.input_tokens / output_tokens are the seat's prompt / completion tokens (eval/cost_report.py prices them);
     *  usage.cost, the provider's own figure, is summed beside them. A response is billed whether or not its answers
     *  are usable. */
    private void bill(JsonObject o) {
        if (o.has("provider") && o.get("provider").isJsonPrimitive()) lastProvider = o.get("provider").getAsString();
        if (o.has("model") && o.get("model").isJsonPrimitive()) LlmSpikeGui.servedModels.put(label, o.get("model").getAsString());
        JsonElement u = o.get("usage");
        if (u == null || !u.isJsonObject()) { lastPromptTokens = -1; lastCompletionTokens = -1; return; }
        JsonObject us = u.getAsJsonObject();
        lastPromptTokens = us.has("input_tokens") ? us.get("input_tokens").getAsInt() : -1;
        lastCompletionTokens = us.has("output_tokens") ? us.get("output_tokens").getAsInt() : -1;
        billedPromptTokens += Math.max(0, lastPromptTokens);
        billedCompletionTokens += Math.max(0, lastCompletionTokens);
        if (us.has("cost") && us.get("cost").isJsonPrimitive()) costUsd += us.get("cost").getAsDouble();
    }

    private static long parseLong(String v, long dflt) {
        try { return v == null ? dflt : Long.parseLong(v.trim()); } catch (NumberFormatException e) { return dflt; }
    }

    /** The provider's own reported cost of every request that came back, in USD. */
    double getCostUsd() { return costUsd; }

    @Override public String getLabel() { return label; }
    @Override public String getModel() { return model; }
    @Override public long getTotalMs() { return totalMs; }
    @Override public int getCalls() { return calls; }
    @Override public int getLastPromptTokens() { return lastPromptTokens; }
    @Override public int getLastCompletionTokens() { return lastCompletionTokens; }
    @Override public long getBilledPromptTokens() { return billedPromptTokens; }
    @Override public long getBilledCompletionTokens() { return billedCompletionTokens; }
    @Override public String getLastProvider() { return lastProvider; }
}
