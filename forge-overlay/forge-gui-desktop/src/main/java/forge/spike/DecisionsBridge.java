package forge.spike;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
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
 * bridge gives no answer, says so in the log, and the seat takes its fixed legal option (never Forge's AI). Every such
 * bound is listed in docs/harness/MODEL-LIMITS.md.
 *
 * <p>What it cannot do: look anything up (the seat runs without MEMORY_TOOLS and LOOKAHEAD, refused at construction),
 * state a plan, yield, or write a reason. Egress, key and loopback rules are OpenRouterBridge's.
 */
final class DecisionsBridge implements LLMBridge {
    static final String PREFIX = "jev:";
    /** v1 (2026-10-24): one request per ask; choice / noul composition per decision kind. Stamped on the game line of a
     *  game with a jev: seat and on a jev: solver's puzzle row (eval/versions.py); pinned in DecisionsBridgeTest and in
     *  eval/harness_acceptance.sh. */
    static final String VERSION = "decisions-bridge-v1";
    /** The most values a number, a division or a mode's count is listed as. A wider one is not asked (no answer,
     *  logged). docs/harness/MODEL-LIMITS.md. */
    static final int MAX_LISTED = 40;
    /** The call deadline: OPENROUTER_TIMEOUT_MS when an operator pins one, else two minutes. */
    private static final long TIMEOUT_MS = LlmSpikeGui.OpenRouterBridge.TIMEOUT_MS_ENV != null
            ? LlmSpikeGui.OpenRouterBridge.TIMEOUT_MS_ENV : 120000L;

    private final String label, model, preamble, endpoint, apiKey;
    private final long timeoutMs;
    private final HttpClient client;
    private long totalMs = 0;
    private int calls = 0;
    private volatile int lastPromptTokens = -1, lastCompletionTokens = -1;
    private volatile double lastCostUsd = -1;
    private volatile long billedPromptTokens = 0, billedCompletionTokens = 0;
    private volatile double costUsd = 0;
    private volatile String lastProvider;

    DecisionsBridge(String label, String model, String preamble, String baseUrl, String apiKey, String consent) {
        this(label, model, preamble, baseUrl, TIMEOUT_MS, apiKey, consent);
    }

    /** Every input passed in, so the egress refusal is reachable whatever the shell carries. */
    DecisionsBridge(String label, String model, String preamble, String baseUrl, long timeoutMs, String apiKey, String consent) {
        LlmSpikeGui.LLMBridgeFactory.requireOpenRouterAccess(baseUrl, consent, apiKey, label);
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
        String b = baseUrl == null || baseUrl.trim().isEmpty() ? LlmSpikeGui.OpenRouterBridge.DEFAULT_BASE_URL : baseUrl.trim();
        while (b.endsWith("/")) b = b.substring(0, b.length() - 1);
        if (b.endsWith("/v1")) b = b.substring(0, b.length() - 3);
        return b + "/alpha/decisions";
    }

    /** A decision model cannot call a lookup: a seat offered them would record a condition it never had. Nor can it
     *  answer without the answer tool (ANSWER_TOOL=0 or NATIVE_TOOLS=0): every decision would be a text ask, which a
     *  decision model cannot take, so every decision would be lost. */
    static void refuseConditions(boolean memoryTools, boolean lookahead, boolean answerTool, String label) {
        if (memoryTools) throw new IllegalStateException("a " + PREFIX + " seat cannot call lookups: run it with "
                + "MEMORY_TOOLS=0 (no --memory-tools) (" + label + ")");
        if (lookahead) throw new IllegalStateException("a " + PREFIX + " seat cannot call simulate: run it with "
                + "LOOKAHEAD=0 (no --lookahead) (" + label + ")");
        if (!answerTool) throw new IllegalStateException("a " + PREFIX + " seat answers only through the `answer` call: run "
                + "it with ANSWER_TOOL and NATIVE_TOOLS on (no --text-answers, no --text-tools) (" + label + ")");
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
        // The docs send `state` as an object or an array (hub.md, tutorial.md); the game is one text field of it.
        JsonObject state = new JsonObject();
        state.addProperty("game", state(messagesJson));
        JsonObject body = new JsonObject();
        body.addProperty("model", model);
        body.add("state", state);
        body.add("questions", q.questions);
        int stateBytes = state.toString().getBytes(StandardCharsets.UTF_8).length;
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
        System.out.println("[JEV " + label + "] " + VERSION + " " + spec.kind + " -> answer " + args + " (state_bytes="
                + stateBytes + ", input_tokens=" + lastPromptTokens + ", cost so far $"
                + String.format(java.util.Locale.ROOT, "%.6f", costUsd) + ")");
        List<NativeTools.Call> one = new ArrayList<>();
        one.add(new NativeTools.Call("jev" + calls, AnswerTool.NAME, args.toString()));
        return NativeTools.Turn.ofCalls(one);
    }

    private void noAnswer(String why) {
        System.out.println("[JEV " + label + "] [JEV-NO-ANSWER] " + why + ": not put to a decision model -> "
                + "no answer; the seat takes its fixed legal option");
    }

    /** The game text: the system preamble, then each message's text in order (the decision, and on a correction round
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
            case MANY: {
                if (s.labels.size() != s.n) return Composed.refused("its options have no labels");
                int max = Math.min(s.max, s.n);
                if (max <= 1) {
                    // At most one pick: one choice among them (and "none" where none is legal), not n independent nouls.
                    c.questions.add("decision", choiceQuestion(SITUATION + " The player picks " + (s.min == 0 ? "at most" : "exactly")
                            + " one of them. Which?", s.labels, s.min == 0));
                    c.read = a -> {
                        int k = choiceOf(a, "decision", s.min == 0 ? 0 : 1, s.n);
                        return args("options", ints(k > 0 ? Collections.singletonList(k) : Collections.emptyList()));
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
                    return args("options", ints(topPicks(p, s.min, max, s.cancel)));
                };
                return c;
            }
            case MANY_REPEAT: {
                // A mode may be picked more than once: one choice per mode of how many times.
                if (s.labels.size() != s.n) return Composed.refused("its options have no labels");
                if (s.max + 1L > MAX_LISTED) return Composed.refused("up to " + s.max + " picks of a mode is more than "
                        + MAX_LISTED + " counts");
                for (int i = 1; i <= s.n; i++) {
                    JsonObject crit = new JsonObject();
                    for (int k = 0; k <= s.max; k++)
                        crit.addProperty(Integer.toString(k), k == 0 ? "Do not pick it." : "Pick it " + k + (k == 1 ? " time." : " times."));
                    c.questions.add("count" + i, question("choice", SITUATION + " The player picks between " + s.min + " and "
                            + s.max + " in all, and the same option may be picked more than once. How many times should "
                            + "option " + i + " (" + s.labels.get(i - 1) + ") be picked?", crit));
                }
                c.read = a -> {
                    int[] counts = new int[s.n];
                    double[] sure = new double[s.n];
                    for (int i = 1; i <= s.n; i++) {
                        counts[i - 1] = choiceOf(a, "count" + i, 0, s.max);
                        sure[i - 1] = probabilityOf(a, "count" + i, counts[i - 1]);
                    }
                    return args("options", ints(repeatPicks(counts, sure, s.min, s.max)));
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
                // Long arithmetic: chooseWideKeywordCost asks for 0..Integer.MAX_VALUE, whose int size overflows.
                if ((long) s.max - (long) s.min + 1L > MAX_LISTED)
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
                    String blocker = s.labels.get(b - 1);
                    if (s.blockLimit(b) > 1) {
                        // A blocker that may block several attackers: a yes/no for each, so it can block more than one.
                        for (int at = 1; at <= s.m; at++) {
                            JsonObject crit = new JsonObject();
                            crit.addProperty("true", blocker + " blocks " + s.labels2.get(at - 1) + ".");
                            crit.addProperty("false", blocker + " does not block " + s.labels2.get(at - 1) + ".");
                            c.questions.add("block" + b + "_" + at, question("noul", GAME + "the player's declare-blockers "
                                    + "decision. The player's " + blocker + " can block up to " + s.blockLimit(b) + " attackers. "
                                    + "Should it block " + s.labels2.get(at - 1) + "? Every blocker is decided at once.", crit));
                        }
                        continue;
                    }
                    JsonObject crit = new JsonObject();
                    crit.addProperty("0", "Do not block with " + blocker + ".");
                    for (int at = 1; at <= s.m; at++) crit.addProperty(Integer.toString(at), "Block " + s.labels2.get(at - 1) + ".");
                    c.questions.add("blocker" + b, question("choice", GAME + "the player's declare-blockers decision. Which "
                            + "attacker, if any, should the player's blocker " + blocker + " block? Every blocker "
                            + "is decided at once.", crit));
                }
                c.read = a -> {
                    JsonArray blocks = new JsonArray();
                    for (int b = 1; b <= s.n; b++) {
                        List<Integer> ats = new ArrayList<>();
                        if (s.blockLimit(b) > 1) {
                            double[] p = new double[s.m];
                            for (int at = 1; at <= s.m; at++) p[at - 1] = noulOf(a, "block" + b + "_" + at);
                            ats = topPicks(p, 0, Math.min(s.blockLimit(b), s.m), -1);
                            Collections.sort(ats);
                        } else {
                            int at = choiceOf(a, "blocker" + b, 0, s.m);
                            if (at > 0) ats.add(at);
                        }
                        for (int at : ats) {
                            JsonObject pair = new JsonObject();
                            pair.addProperty("blocker", b);
                            pair.addProperty("attacker", at);
                            blocks.add(pair);
                        }
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

    /** The probability a choice answer gave its chosen key; its `confidence` when it lists none, else 1. */
    private static double probabilityOf(JsonObject answers, String key, int chosen) {
        JsonObject a = answers.getAsJsonObject(key);
        JsonElement probs = a.get("probabilities");
        if (probs != null && probs.isJsonObject() && probs.getAsJsonObject().has(Integer.toString(chosen)))
            return probs.getAsJsonObject().get(Integer.toString(chosen)).getAsDouble();
        JsonElement conf = a.get("confidence");
        return conf != null && conf.isJsonPrimitive() ? conf.getAsDouble() : 1.0;
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

    private static JsonArray ints(List<Integer> xs) {
        JsonArray a = new JsonArray();
        for (int x : xs) a.add(x);
        return a;
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

    /**
     * The picks of a repeatable choice from each option's count ({@code counts[0]} is option 1) and the probability the
     * model gave that count, in option order. Above {@code max}, the surest counts are kept first; below {@code min},
     * the surest option picked at least once (else option 1) is picked again until the minimum.
     */
    static List<Integer> repeatPicks(int[] counts, double[] sure, int min, int max) {
        List<Integer> order = new ArrayList<>();
        for (int i = 1; i <= counts.length; i++) order.add(i);
        order.sort(Comparator.comparingDouble((Integer i) -> -sure[i - 1]).thenComparingInt(i -> i));
        int[] kept = new int[counts.length];
        int total = 0;
        for (int i : order) {
            int take = Math.min(counts[i - 1], max - total);
            kept[i - 1] = take;
            total += take;
        }
        int pad = 1;
        for (int i : order) if (counts[i - 1] > 0) { pad = i; break; }
        while (total < min) { kept[pad - 1]++; total++; }
        List<Integer> out = new ArrayList<>();
        for (int i = 1; i <= kept.length; i++) for (int k = 0; k < kept[i - 1]; k++) out.add(i);
        return out;
    }

    // ------------------------------------------------------------------ the request

    /** One POST; the parsed body, or null (non-200, timeout, unparseable), logged. Usage is billed when present. */
    private JsonObject send(String body) {
        // A call that fails has no usage of its own: its row must not repeat the last call's bill.
        lastPromptTokens = -1;
        lastCompletionTokens = -1;
        lastCostUsd = -1;
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
     *  usage.cost, the provider's own figure, is the call's `cost_usd` and summed beside them. A response is billed
     *  whether or not its answers are usable. */
    private void bill(JsonObject o) {
        if (o.has("provider") && o.get("provider").isJsonPrimitive()) lastProvider = o.get("provider").getAsString();
        if (o.has("model") && o.get("model").isJsonPrimitive()) LlmSpikeGui.servedModels.put(label, o.get("model").getAsString());
        JsonElement u = o.get("usage");
        if (u == null || !u.isJsonObject()) return;
        JsonObject us = u.getAsJsonObject();
        lastPromptTokens = us.has("input_tokens") ? us.get("input_tokens").getAsInt() : -1;
        lastCompletionTokens = us.has("output_tokens") ? us.get("output_tokens").getAsInt() : -1;
        billedPromptTokens += Math.max(0, lastPromptTokens);
        billedCompletionTokens += Math.max(0, lastCompletionTokens);
        if (us.has("cost") && us.get("cost").isJsonPrimitive()) {
            lastCostUsd = us.get("cost").getAsDouble();
            costUsd += lastCostUsd;
        }
    }

    /** The provider's own reported cost of every request that came back, in USD. */
    double getCostUsd() { return costUsd; }

    @Override public double getLastCostUsd() { return lastCostUsd; }
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
