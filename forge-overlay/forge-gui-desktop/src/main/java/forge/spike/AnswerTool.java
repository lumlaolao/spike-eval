package forge.spike;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.function.BiConsumer;
import java.util.function.Consumer;
import java.util.function.Function;
import java.util.regex.Pattern;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

/**
 * The seat answers a decision by CALLING a function whose schema is built from that decision's legal
 * values, instead of writing a tag line (CHOICE:/PICKS:/BLOCKS:/KEEP/NUMBER:/CARD:/ALLOC:) the harness
 * scrapes out of prose. Its notes (the plan, the cast list, the opponent read, the card-interactions
 * note) are function arguments too. Owner decision 2026-10-15: "the agent should be able to interact
 * directly with the choices it needs ... text parsing is how we got into this mess."
 *
 * The standard wherever native tools are on (an `openrouter:` seat); ANSWER_TOOL=0 is the opt-out, and
 * the text tags survive only as that opt-out and as the protocol of a backend with no function calling
 * (none since the Claude CLI text lane was retired). A text reply in a tool ask is still accepted that round (counted as
 * `answer_via: "text"`), so a model that ignores the tool loses nothing; the comparison is measured
 * (docs/harness/PLAYER-HARNESS.md, "Answer tool").
 *
 * `tool_choice` stays "auto" on every round: forcing a call skips extended thinking on the Claude models
 * that accept it and is a 400 on Opus/Sonnet 5.5 (eval/research/2026-10-15-tool-protocol-and-action-parsing.md).
 * An invalid call (an option out of range, a wrong count, an illegal block) is answered in the SAME
 * conversation with the validation error and one more round; a second invalid call ends the ask with
 * no answer (`lost`), and the caller takes its deterministic fallback.
 */
final class AnswerTool {
    private AnswerTool() { }

    /** v1 (2026-10-15): per-decision `answer` schema, `set_plan` note, same-conversation correction.
     *  v2: one fixed `answer` schema for every decision, validated against the decision; `set_plan`
     *  always offered. Tool definitions open the prompt-cache prefix, so a schema that changed every
     *  decision discarded the cache on every call (docs/harness/PROMPT-CRAFT.md rule 19).
     *  v3: an optional `why` (one sentence, for the record), and readable reasoning kept on the row.
     *  v4: a reply that calls nothing is asked once, in the same conversation, for the call (`nudged`).
     *  v5: a play's `keep_untapped` says which mana sources stay untapped when it is paid (work plan item 6).
     *  v6: `steps` promises nothing a decision does not offer; a play offers it only under PLAN_EXECUTE.
     *  v7: no text alternative: a reply that calls nothing is reminded up to MAX_NUDGES times, then the decision
     *  is lost; prose is never parsed as the answer. A text format left in a tool ask is logged and counted.
     *  v8: a model's filled-in defaults do nothing: `yield` has "none" as its first value, and an empty
     *  `keep_untapped` means not said (Luna filled every field: 175 of 189 answers yielded to end of turn).
     *  v10: `set_plan` says the plan is a note, not a commitment: each decision is re-made on the state then (Anthropic,
     *  OpenAI, Google and xAI agent guides: the model gets the environment's result at every step).
     *  v11: a tool ask carries no "Reason briefly" and one answer line, the appended ANSWER line (review M1).
     *  v12: a reply's answer is its first `answer` call that validates, not its first `answer` call; a reply whose
     *  every `answer` call is illegal is one invalid reply; an illegal call before the answer is "Not used", never
     *  merged into it (frontier5, GLM 5.3 on hard-elf2, 2026-10-26).
     *  v13 (2026-11-03): the PROPOSE_ACTIONS condition. A play decision is answered with `action`, a proposed play
     *  (verb, card handle, ability, face, cost) that the engine checks against the legal plays it does not show; a
     *  rejected proposal is told its rule category and may be repaired, up to {@link #MAX_INVALID_PROPOSALS} tries.
     *  Off, the schema and every ask are unchanged.
     *  v14 (2026-11-03): a proposal names its target (`action.targets`), checked with the play; Prototype and "flash"
     *  are named costs; a proposal answered with a menu field is shown an example. Still unchanged off the arm.
     *  v15 (2026-11-04): `action.cost` says an optional cost such as Kicker is asked when the spell is cast. */
    static final String VERSION = "answer-tool-v15";
    static final String NAME = "answer";
    static final String SET_PLAN = "set_plan";
    /** Model rounds one answer ask may take: the lookup rounds, the answer, one correction, one reminder. */
    static final int MAX_ROUNDS = ContextMemory.TOOLS_PER_ASK + 3;

    /** On unless ANSWER_TOOL=0, wherever native tools are on (mirrors NATIVE_TOOLS). */
    static boolean enabled() { return !"0".equals(System.getenv("ANSWER_TOOL")); }

    /** ANSWER_TOOL_STRICT=1: send `strict: true` with a schema reduced to what strict modes accept
     *  (no numeric, length or array-size constraints; optional fields nullable and required). Off by
     *  default: strict support is per OpenRouter endpoint, and Anthropic compiles each new strict schema
     *  (a per-decision schema is new almost every time). The harness validates every call either way. */
    static boolean strict() { return "1".equals(System.getenv("ANSWER_TOOL_STRICT")); }

    /** ANSWER_SCHEMA=per-decision: the v1 arm, a schema built from each decision's legal values. Off by
     *  default; it is kept so the two can be measured against each other. */
    static boolean perDecision() { return "per-decision".equals(System.getenv("ANSWER_SCHEMA")); }

    /** PROPOSE_ACTIONS=1: the measurement arm that hides the play menu. The seat proposes its play as an `action` and
     *  the engine checks it like a type checker (docs/harness/ARCHITECTURE.md, "The propose condition"). It ablates
     *  the owner rule "a legal action is never hidden" on purpose, with the owner's go-ahead (2026-11-03); off by
     *  default, pinned and stamped by eval/run_batch.py, never pooled with menu runs. */
    static boolean proposeActions() { return PROPOSE_FOR_TEST != null ? PROPOSE_FOR_TEST : "1".equals(System.getenv("PROPOSE_ACTIONS")); }
    /** Tests only: the condition on or off without the environment variable (null: read the environment). */
    static volatile Boolean PROPOSE_FOR_TEST = null;

    /** Invalid `answer` calls a proposal may make before its decision is lost (two elsewhere). Repair gains level off
     *  after two to five rounds of checker feedback (COPRA; Goedel-Prover-V2); Kaggle Game Arena allows four tries. */
    static final int MAX_INVALID_PROPOSALS = 4;

    /** True when a seat on this model spec answers through the tool. A decision model (`jev:`, DecisionsBridge) and a
     *  Claude CLI seat (`claude-cli:`, ClaudeCliBridge) have no lookups but answer through `answer` too: the typed
     *  answer, or the CLI's structured output, is turned into the call. */
    static boolean usedBy(String modelSpec) {
        return enabled() && (NativeTools.usedBy(modelSpec)
                || NativeTools.enabled() && (DecisionsBridge.isSpec(modelSpec) || ClaudeCliBridge.isSpec(modelSpec)));
    }

    /** The version stamped on the game line: what a tool-answer game was played under. */
    static String protocolVersion() {
        String env = System.getenv("ANSWER_TOOL_CHOICE");
        return VERSION + (perDecision() ? "+per-decision" : "") + (strict() ? "+strict" : "") + (proposeActions() ? "+propose" : "")
                + (env == null || env.trim().isEmpty() ? "" : "+choice=" + env.trim());
    }

    /**
     * The `tool_choice` an answer ask sends to {@code model} (an OpenRouter id such as "openai/gpt-6-luna"):
     * "required" where forcing a call is measured to keep the model's reasoning, so the call is the only way
     * to reply; "auto" elsewhere. Constant per model, so the cache prefix holds (PROMPT-CRAFT.md rule 19).
     * <ul>
     *   <li>OpenAI models: "required". Probe of 2026-10-15 on GPT-6 Luna, one decision, two calls each:
     *       auto 23-24 reasoning tokens, required 18-66, a named function 0 (naming the tool skips the
     *       reasoning, so it is never used).</li>
     *   <li>Anthropic models: "auto". Opus 5.5, Sonnet 5.5 and Fable 5.1 reject forced tool use with a 400, and
     *       on the models that accept it a forced call skips thinking (platform.claude.com, Define tools).</li>
     *   <li>Others: "auto" until measured the same way.</li>
     * </ul>
     * ANSWER_TOOL_CHOICE=auto|required overrides it for every model (stamped in {@link #protocolVersion}).
     */
    static String toolChoiceFor(String model) {
        String env = System.getenv("ANSWER_TOOL_CHOICE");
        if ("auto".equals(env) || "required".equals(env)) return env;
        String m = model == null ? "" : model.replaceFirst("^openrouter:", "");
        return m.startsWith("openai/") ? "required" : "auto";
    }

    /** Which tiers of which model-driven seats answer through the tool, e.g. "A/smart,B/fast". */
    static String seats(String controllerA, String smartA, String fastA, String controllerB, String smartB, String fastB) {
        List<String> out = new ArrayList<>();
        if ("llm".equals(controllerA)) { if (usedBy(smartA)) out.add("A/smart"); if (usedBy(fastA)) out.add("A/fast"); }
        if ("llm".equals(controllerB)) { if (usedBy(smartB)) out.add("B/smart"); if (usedBy(fastB)) out.add("B/fast"); }
        return String.join(",", out);
    }

    /** What the system preamble says about answering, in place of the text format rules. */
    static String instructions() {
        return "ANSWERS: you answer every decision by calling the `answer` function, giving the field that decision "
                + "asks for: `option` (one number from its list), `options` (several numbers), `blocks`, `amounts`, "
                + "`keep`, `number` or `card`. Each decision ends by saying which field it takes and what is legal. Think "
                + "first, then call it once; the call ends the decision. If the values are not legal you are told why and "
                + "may call again once. Set or replace your plan with `set_plan` (a goal and the next steps) at any "
                + "decision, alongside the answer.\n"
                + (proposeActions() ? PROPOSE_INSTRUCTIONS : "");
    }

    /** The preamble's paragraph on proposing a play, under PROPOSE_ACTIONS only. */
    static final String PROPOSE_INSTRUCTIONS = "PLAYS: when you have priority you are not shown a list of legal plays. "
            + "Propose one with `action`: `verb` (play_land, cast, activate or pass), `card` (the handle of the card or "
            + "permanent, such as \"@12\"), and for an ability `ability` (its number in YOUR ACTIVATED ABILITIES). Give `face` "
            + "to cast one face or half of a card, and `cost` to cast it another way the card allows (its name as the card "
            + "prints it, such as \"Flashback\" or \"Prototype\", \"alternative\" for a \"rather than pay this spell's mana cost\" "
            + "cost, or \"flash\" for a cast the card allows at a time you could not otherwise cast it). "
            + "The rules engine checks the proposal. If the rules do not allow it now you are told which rule it breaks "
            + "(timing, cost, zone, target, land drop, control, restriction) and may propose again, up to "
            + MAX_INVALID_PROPOSALS + " tries in all; after that you pass. A malformed proposal (an unknown handle or "
            + "ability number) is told what is wrong. Give a play's target in `targets` (a handle, or \"you\" or \"opponent\"); "
            + "the engine checks it with the play. Modes, X and costs, and the targets of a play with several targets or "
            + "modes, are asked after the play is accepted.\n";


    enum Kind { ONE, MANY, MANY_REPEAT, ALLOC, KEEP, BLOCKS, NUMBER, CARD, ACTION }

    /** The legal values of one decision. Built by the decision site; numbers are 1-based, as shown. */
    static final class Spec {
        final Kind kind;
        final int n;          // options (ONE/MANY), amounts (ALLOC), blockers (BLOCKS)
        final int min, max;   // picks (MANY), range (NUMBER), total in max (ALLOC)
        final int m;          // attackers (BLOCKS)
        int cancel = -1;      // MANY: the 1-based option that cancels and must be picked alone
        /** Steps of the plan the harness may cast for the seat: the 1-based CAST/ACTIVATE options. */
        List<Integer> stepOptions;
        boolean yield, profile, lines, plan;
        /** A play decision: `keep_untapped` (the mana sources to leave untapped when the chosen play is paid). */
        boolean keep;
        /** Site-level legality the schema cannot express (a block a blocker cannot make, a card name
         *  that does not exist): an error message, or null when legal. */
        Function<Answer, String> check;
        List<String> labels = new ArrayList<>(), labels2 = new ArrayList<>();

        private Spec(Kind kind, int n, int min, int max, int m) {
            this.kind = kind; this.n = n; this.min = min; this.max = max; this.m = m;
        }
        static Spec one(int n) { return new Spec(Kind.ONE, n, 1, 1, 0); }
        static Spec many(int n, int min, int max) { return new Spec(Kind.MANY, n, Math.max(0, min), Math.min(max, n), 0); }
        static Spec repeat(int n, int min, int max) { return new Spec(Kind.MANY_REPEAT, n, Math.max(0, min), Math.max(1, max), 0); }
        static Spec alloc(int n, int total) { return new Spec(Kind.ALLOC, n, total, total, 0); }
        static Spec keep() { return new Spec(Kind.KEEP, 2, 1, 1, 0); }
        static Spec blocks(List<String> blockers, List<String> attackers) { return blocks(blockers, attackers, null); }
        /** {@code limits}: how many attackers each blocker may block (1 unless it can block more; null: all 1). */
        static Spec blocks(List<String> blockers, List<String> attackers, List<Integer> limits) {
            Spec s = new Spec(Kind.BLOCKS, blockers.size(), 0, blockers.size(), attackers.size());
            s.labels = new ArrayList<>(blockers); s.labels2 = new ArrayList<>(attackers);
            s.blockLimits = limits == null ? null : new ArrayList<>(limits);
            return s;
        }
        List<Integer> blockLimits;
        int blockLimit(int oneBased) { return blockLimits == null ? 1 : Math.max(1, blockLimits.get(oneBased - 1)); }
        int blockSlots() { int t = 0; for (int b = 1; b <= n; b++) t += Math.min(blockLimit(b), Math.max(1, m)); return t; }
        static Spec number(int min, int max) { return new Spec(Kind.NUMBER, 0, min, max, 0); }
        static Spec card() { return new Spec(Kind.CARD, 0, 0, 0, 0); }
        /** A play decision under PROPOSE_ACTIONS: answered with `action`. {@code n} is the hidden menu's size; the
         *  site's {@link #check} names the option a proposal matches (sets {@link Answer#option}) or rejects it. */
        static Spec action(int n) { return new Spec(Kind.ACTION, n, 1, 1, 0); }
        Spec cancel(int oneBased) { this.cancel = oneBased; return this; }
        Spec check(Function<Answer, String> c) { this.check = c; return this; }
        Spec labels(List<String> l) { this.labels = new ArrayList<>(l); return this; }
    }

    /** A validated answer. Only the fields of its kind are set. */
    static final class Answer {
        Integer option;            // ONE
        List<Integer> options;     // MANY / MANY_REPEAT (1-based, as given)
        List<Integer> amounts;     // ALLOC
        Boolean keep;              // KEEP
        List<int[]> blocks;        // BLOCKS: {blocker, attacker}, 1-based
        Integer number;            // NUMBER
        String card;               // CARD
        List<Integer> steps;       // play: the cast list, 1-based options
        String yieldUntil, profile, lines;
        /** Optional one-sentence explanation, for the game record (a model whose reasoning is encrypted
         *  otherwise leaves no trace of why it chose). */
        String why;
        /** `keep_untapped`: handles of mana sources to leave untapped when the chosen play is paid; null
         *  when not given or empty (the seat is asked when there is a choice). */
        List<Integer> keepUntapped;
        /** ACTION: the proposed play, as given (before the site's check matches it to an option). */
        Proposal action;
        /** 0-based picks, for the sites that index their own lists. */
        List<Integer> zeroBased() {
            List<Integer> out = new ArrayList<>();
            for (int i : options) out.add(i - 1);
            return out;
        }
    }

    /** A proposed play (PROPOSE_ACTIONS): what the seat says it does, checked by the engine before anything happens. */
    static final class Proposal {
        String verb;          // play_land | cast | activate | pass
        Integer card;         // the handle, without "@"
        Integer ability;      // activate: 1-based, in YOUR ACTIVATED ABILITIES
        String face, cost;    // cast: a face or half by name; another way to cast it, by name
        List<String> targets; // the play's target: a handle, "you" or "opponent"
        Proposal copy() {
            Proposal p = new Proposal();
            p.verb = verb; p.card = card; p.ability = ability; p.face = face; p.cost = cost; p.targets = targets;
            return p;
        }
        /** One line for the row and the history: "cast @12 (Flashback)". */
        String render() {
            if ("pass".equals(verb)) return "pass";
            StringBuilder b = new StringBuilder(verb).append(" @").append(card);
            if (ability != null) b.append(" ability ").append(ability);
            if (face != null) b.append(" face \"").append(face).append('"');
            if (cost != null) b.append(" (").append(cost).append(')');
            if (targets != null && !targets.isEmpty()) b.append(" targeting ").append(String.join(", ", targets));
            return b.toString();
        }
    }

    static final List<String> PROPOSE_VERBS = List.of("play_land", "cast", "activate", "pass");

    /** The fields of `action`, read from its schema so proposalOf and the schema cannot disagree. */
    static final java.util.Set<String> ACTION_FIELDS = actionProperty().getAsJsonObject("properties").keySet();

    /** The `action` property of the fixed schema (PROPOSE_ACTIONS only). */
    static JsonObject actionProperty() {
        JsonObject act = obj("type", "object", "description", "Only on a play decision that asks you to propose an action: the "
                + "play you propose. The rules engine checks it and tells you which rule it breaks if it is not legal now.");
        JsonObject ap = new JsonObject();
        JsonObject verb = obj("type", "string", "description", "play_land, cast, activate or pass.");
        JsonArray ve = new JsonArray();
        for (String v : PROPOSE_VERBS) ve.add(v);
        verb.add("enum", ve);
        ap.add("verb", verb);
        ap.add("card", obj("type", "string", "description", "The handle of the card or permanent, such as \"@12\". Not used with pass."));
        ap.add("ability", obj("type", "integer", "description", "With activate: the ability's number in YOUR ACTIVATED ABILITIES "
                + "(1 when the card has one)."));
        ap.add("face", obj("type", "string", "description", "Optional, with cast: the face or half to cast, by name, when the card has more than one."));
        ap.add("cost", obj("type", "string", "description", "Optional, with cast: another way to cast it that the card allows, "
                + "by the name the card prints (\"Flashback\", \"Evoke\", \"Overload\", \"Prototype\"...), \"alternative\" for a "
                + "\"rather than pay this spell's mana cost\" cost, or \"flash\" for a cast the card allows when you could not "
                + "otherwise cast it. Omit it to cast it normally; an optional additional cost such as Kicker is asked when you cast it."));
        JsonObject tg = obj("type", "array", "description", "With cast or activate, when the play targets: its target, by handle "
                + "(\"@7\"), or \"you\" or \"opponent\" for a player. The engine checks it with the play.");
        tg.add("items", obj("type", "string"));
        ap.add("targets", tg);
        act.add("properties", ap);
        JsonArray req = new JsonArray();
        req.add("verb");
        act.add("required", req);
        act.addProperty("additionalProperties", false);
        return act;
    }

    // ------------------------------------------------------------------ schema

    private static JsonObject obj(String... kv) {
        JsonObject o = new JsonObject();
        for (int i = 0; i < kv.length; i += 2) o.addProperty(kv[i], kv[i + 1]);
        return o;
    }

    private static JsonObject intEnum(int lo, int hi, String description) {
        JsonObject p = obj("type", "integer", "description", description);
        JsonArray e = new JsonArray();
        for (int i = lo; i <= hi; i++) e.add(i);
        p.add("enum", e);
        return p;
    }

    private static JsonObject text(String description, int maxLength) {
        JsonObject p = obj("type", "string", "description", description);
        p.addProperty("minLength", 1);
        p.addProperty("maxLength", maxLength);
        return p;
    }

    private static String numbered(List<String> labels) {
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < labels.size(); i++) b.append(i == 0 ? "" : "; ").append(i + 1).append(" = ").append(labels.get(i));
        return b.toString();
    }

    /** The one `answer` function every decision is answered with. Every field is optional in the
     *  schema; which one a decision needs is said in the decision itself, and {@link #validate} checks
     *  the call against that decision and explains any mistake. */
    static JsonObject fixedDefinition() {
        JsonObject props = new JsonObject();
        props.add("option", obj("type", "integer", "description", "For a decision that asks for ONE option from a numbered "
                + "list: that option's number. A decline/none/PASS option, when listed, is just another number."));
        JsonObject many = obj("type", "array", "description", "For a decision that asks you to choose several options (or none) "
                + "from a numbered list (discard, reveal, sacrifice, scry/surveil, attackers): the numbers, in the order you want "
                + "them, within the minimum and maximum the decision states. An empty list chooses none.");
        many.add("items", obj("type", "integer"));
        props.add("options", many);
        JsonObject pair = obj("type", "object");
        JsonObject pp = new JsonObject();
        pp.add("blocker", obj("type", "integer", "description", "The number of your blocking creature, from the decision's blocker list."));
        pp.add("attacker", obj("type", "integer", "description", "The number of the attacker it blocks, from the decision's attacker list."));
        pair.add("properties", pp);
        JsonArray req = new JsonArray();
        req.add("blocker"); req.add("attacker");
        pair.add("required", req);
        pair.addProperty("additionalProperties", false);
        JsonObject blocks = obj("type", "array", "description", "For a declare-blockers decision: each block as a blocker number "
                + "and an attacker number; each blocker once, or up to the number of attackers its label says it can block."
                + " An empty list declares no blocks.");
        blocks.add("items", pair);
        props.add("blocks", blocks);
        JsonObject amounts = obj("type", "array", "description", "For a decision that divides an amount (damage, counters) among "
                + "listed targets: one number per target, in the order listed, adding up to the stated total.");
        amounts.add("items", obj("type", "integer"));
        props.add("amounts", amounts);
        props.add("keep", obj("type", "boolean", "description", "For a mulligan decision: true to keep this hand, false to mulligan."));
        props.add("number", obj("type", "integer", "description", "For a decision that asks for a number (X, a count): an integer in "
                + "the range the decision states."));
        props.add("card", obj("type", "string", "description", "For a decision that asks you to name a card: its exact English name."));
        // Under PROPOSE_ACTIONS only, so the default schema's bytes (and every prompt-cache prefix) do not move.
        if (proposeActions()) props.add("action", actionProperty());
        JsonObject steps = obj("type", "array", "description", "Optional, only on a play decision that says it takes `steps`: "
                + "the CAST/ACTIVATE option numbers of your plan, in order, for the harness to cast without asking again this "
                + "turn. Omit anything you are holding. Elsewhere it is ignored.");
        steps.add("items", obj("type", "integer"));
        props.add("steps", steps);
        JsonObject y = obj("type", "string", "description", "Optional, only with PASS on a play decision. \"none\" (the default): "
                + "you are asked at every window as usual. \"end-of-turn\" or \"my-next-turn\": until then you are not asked in "
                + "windows where nothing of your opponent's is on the stack (anything they cast or activate always asks you; "
                + "your own main phases always ask too).");
        JsonArray e = new JsonArray();
        // "none" first: a model that fills every field takes the first value (Luna did, 175 of 189 answers).
        e.add("none"); e.add("end-of-turn"); e.add("my-next-turn");
        y.add("enum", e);
        props.add("yield", y);
        props.add("profile", obj("type", "string", "description", "Optional, when the decision asks for it: your read of the "
                + "OPPONENT in one short line (their deck and plan, and the cards to respect)."));
        props.add("lines", obj("type", "string", "description", "Optional, when the decision asks for it: how cards in YOUR OWN "
                + "deck combine, in one or two short lines."));
        JsonObject keep = obj("type", "array", "description", "Optional, on a play decision whose choice casts or activates "
                + "something with a mana cost: the mana sources to leave UNTAPPED when it is paid, by handle (\"@12\"), for "
                + "example to keep blue up for a counterspell. Omit it, or leave it empty, and you are asked when the choice "
                + "matters.");
        keep.add("items", obj("type", "string"));
        props.add("keep_untapped", keep);
        props.add("why", obj("type", "string", "description", "Optional: one sentence on why you chose this, kept in the "
                + "game record for the people reviewing it. It does not change the answer."));
        JsonObject params = new JsonObject();
        params.addProperty("type", "object");
        params.add("properties", props);
        params.add("required", new JsonArray());
        params.addProperty("additionalProperties", false);
        if (strict()) params = strictify(params);
        JsonObject f = new JsonObject();
        if (strict()) f.addProperty("strict", true);
        f.addProperty("name", NAME);
        f.addProperty("description", "Answer the current decision. Every decision is answered with this one call: give the field "
                + "the decision asks for (one option number, a list of option numbers, blocks, amounts, keep, a number or a card "
                + "name) and the optional notes it invites. The call ends the decision and the engine carries it out. If the "
                + "values are not legal for this decision you are told why, nothing happens, and you may call once more. Think "
                + "before calling; the arguments hold only the answer. Lookups made before it do not count against it.");
        f.add("parameters", params);
        JsonObject tool = new JsonObject();
        tool.addProperty("type", "function");
        tool.add("function", f);
        return tool;
    }

    /** The field each kind of decision is answered with. */
    static String fieldOf(Kind k) {
        switch (k) {
            case ONE: return "option";
            case MANY: case MANY_REPEAT: return "options";
            case ALLOC: return "amounts";
            case KEEP: return "keep";
            case BLOCKS: return "blocks";
            case NUMBER: return "number";
            case CARD: return "card";
            case ACTION: return "action";
            default: return null;
        }
    }

    /** The `answer` function for one decision (the ANSWER_SCHEMA=per-decision arm). */
    static JsonObject definition(Spec s) {
        JsonObject props = new JsonObject();
        List<String> required = new ArrayList<>();
        String what;
        switch (s.kind) {
            case ONE:
                props.add("option", intEnum(1, s.n, "The number of your choice from the decision's numbered list (1-" + s.n + ")."));
                required.add("option");
                what = "your choice: one option number";
                break;
            case MANY:
            case MANY_REPEAT: {
                JsonObject items = intEnum(1, s.n, null);
                items.remove("description");
                JsonObject arr = obj("type", "array", "description", "Option numbers from the decision's list"
                        + (s.kind == Kind.MANY_REPEAT ? "; the same number may repeat" : ", each at most once")
                        + ", in the order you want them (" + bounds(s) + ")"
                        + (s.cancel > 0 ? "; option " + s.cancel + " cancels the play and must be the only number" : "")
                        + ". An empty list chooses none" + (s.min > 0 ? " (not allowed here)" : "") + ".");
                arr.add("items", items);
                if (s.kind == Kind.MANY) arr.addProperty("uniqueItems", true);
                arr.addProperty("minItems", s.cancel > 0 ? Math.min(1, s.min) : s.min);
                arr.addProperty("maxItems", s.kind == Kind.MANY ? s.max : Math.max(s.max, 1));
                props.add("options", arr);
                required.add("options");
                what = "the options you pick (" + bounds(s) + ")";
                break;
            }
            case ALLOC: {
                JsonObject items = obj("type", "integer");
                items.addProperty("minimum", 0);
                JsonObject arr = obj("type", "array", "description", s.n + " amounts, one per listed target in the order listed, summing to " + s.max + ".");
                arr.add("items", items);
                arr.addProperty("minItems", s.n);
                arr.addProperty("maxItems", s.n);
                props.add("amounts", arr);
                required.add("amounts");
                what = "how you divide " + s.max;
                break;
            }
            case KEEP:
                props.add("keep", obj("type", "boolean", "description", "true to KEEP this hand, false to MULLIGAN."));
                required.add("keep");
                what = "keep or mulligan";
                break;
            case BLOCKS: {
                JsonObject pair = obj("type", "object");
                JsonObject pp = new JsonObject();
                pp.add("blocker", intEnum(1, s.n, "Your blocker: " + numbered(s.labels) + "."));
                pp.add("attacker", intEnum(1, s.m, "The attacker it blocks: " + numbered(s.labels2) + "."));
                pair.add("properties", pp);
                JsonArray req = new JsonArray();
                req.add("blocker"); req.add("attacker");
                pair.add("required", req);
                pair.addProperty("additionalProperties", false);
                JsonObject arr = obj("type", "array", "description", "Each block as a blocker number and an attacker number; "
                        + "each blocker once, or up to the number of attackers its label says it can block. An empty list declares no blocks.");
                arr.add("items", pair);
                arr.addProperty("maxItems", s.blockSlots());
                props.add("blocks", arr);
                required.add("blocks");
                what = "your blocks";
                break;
            }
            case NUMBER: {
                JsonObject p = obj("type", "integer", "description", "An integer from " + s.min + " through " + s.max + ".");
                p.addProperty("minimum", s.min);
                p.addProperty("maximum", s.max);
                props.add("number", p);
                required.add("number");
                what = "the number";
                break;
            }
            case CARD:
                props.add("card", text("The exact English name of the card you name.", 200));
                required.add("card");
                what = "the card you name";
                break;
            case ACTION:
                props.add("action", actionProperty());
                required.add("action");
                what = "the play you propose";
                break;
            default:
                throw new IllegalStateException(String.valueOf(s.kind));
        }
        if (s.stepOptions != null && !s.stepOptions.isEmpty()) {
            JsonObject items = new JsonObject();
            items.addProperty("type", "integer");
            JsonArray e = new JsonArray();
            for (int i : s.stepOptions) e.add(i);
            items.add("enum", e);
            JsonObject arr = obj("type", "array", "description", "Optional, only if you want the harness to cast steps of your plan "
                    + "for you without asking again this turn: the CAST/ACTIVATE option numbers you intend to use, in order. "
                    + "Omit anything you are holding.");
            arr.add("items", items);
            arr.addProperty("uniqueItems", true);
            props.add("steps", arr);
        }
        if (s.yield) {
            JsonObject y = obj("type", "string", "description", "Optional, with PASS only. \"none\" (the default): you are asked "
                    + "as usual. Otherwise, until then you are not asked in windows where nothing of your opponent's is on the stack "
                    + "(anything they cast or activate always asks you; your own main phases always ask too).");
            JsonArray e = new JsonArray();
            e.add("none"); e.add("end-of-turn"); e.add("my-next-turn");
            y.add("enum", e);
            props.add("yield", y);
        }
        if (s.profile) props.add("profile", text("Your read of the OPPONENT in one short line: their deck and plan, and the cards to respect.", 300));
        if (s.lines) props.add("lines", text("The interactions among YOUR OWN deck's cards you mean to use, in one or two short lines.", 500));
        JsonObject params = new JsonObject();
        params.addProperty("type", "object");
        params.add("properties", props);
        JsonArray req = new JsonArray();
        for (String r : required) req.add(r);
        params.add("required", req);
        params.addProperty("additionalProperties", false);
        if (strict()) params = strictify(params);
        JsonObject f = new JsonObject();
        f.addProperty("name", NAME);
        f.addProperty("description", "Answer THIS decision with " + what + ". The values are this decision's legal ones; "
                + "the call ends the decision. Reason before calling, not in the arguments.");
        f.add("parameters", params);
        if (strict()) f.addProperty("strict", true);
        JsonObject tool = new JsonObject();
        tool.addProperty("type", "function");
        tool.add("function", f);
        return tool;
    }

    private static String bounds(Spec s) {
        return s.min == s.max ? "exactly " + s.min : "between " + s.min + " and " + s.max;
    }

    /** `set_plan`: the seat's plan as a goal and the next steps; not a lookup and not the answer. */
    static JsonObject setPlanDefinition() {
        JsonObject props = new JsonObject();
        props.add("goal", text("What you are working toward.", 300));
        props.add("next", text("The steps you intend, in order (e.g. a land, cast a card, hold a card for their turn).", 500));
        JsonObject params = new JsonObject();
        params.addProperty("type", "object");
        params.add("properties", props);
        JsonArray req = new JsonArray();
        req.add("goal"); req.add("next");
        params.add("required", req);
        params.addProperty("additionalProperties", false);
        if (strict()) params = strictify(params);
        JsonObject f = new JsonObject();
        f.addProperty("name", SET_PLAN);
        f.addProperty("description", "State or replace YOUR plan, a note and not a commitment: it is shown back to you on "
                + "every later decision with the turn you set it, and each decision is still yours on the state then. Call "
                + "it whenever the plan no longer fits, at any decision, alongside your answer. It does not answer the "
                + "decision.");
        f.add("parameters", params);
        if (strict()) f.addProperty("strict", true);
        JsonObject tool = new JsonObject();
        tool.addProperty("type", "function");
        tool.add("function", f);
        return tool;
    }

    private static final java.util.Set<String> NOT_STRICT = java.util.Set.of(
            "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "uniqueItems", "multipleOf");

    /** The schema a strict mode accepts: unsupported constraints removed (the harness still checks
     *  them), and every optional property made nullable and listed as required (OpenAI's rule). */
    static JsonObject strictify(JsonObject schema) {
        JsonObject out = new JsonObject();
        for (java.util.Map.Entry<String, JsonElement> e : schema.entrySet()) {
            if (NOT_STRICT.contains(e.getKey())) continue;
            JsonElement v = e.getValue();
            if (("properties".equals(e.getKey())) && v.isJsonObject()) {
                JsonObject ps = new JsonObject();
                for (java.util.Map.Entry<String, JsonElement> p : v.getAsJsonObject().entrySet())
                    ps.add(p.getKey(), p.getValue().isJsonObject() ? strictify(p.getValue().getAsJsonObject()) : p.getValue());
                out.add("properties", ps);
            } else if ("items".equals(e.getKey()) && v.isJsonObject()) {
                out.add("items", strictify(v.getAsJsonObject()));
            } else {
                out.add(e.getKey(), v.deepCopy());
            }
        }
        if (out.has("properties")) {
            JsonArray req = out.has("required") ? out.getAsJsonArray("required") : new JsonArray();
            java.util.Set<String> has = new java.util.HashSet<>();
            for (JsonElement r : req) has.add(r.getAsString());
            JsonObject ps = out.getAsJsonObject("properties");
            for (String k : new ArrayList<>(ps.keySet())) {
                if (has.contains(k)) continue;
                JsonObject nullable = new JsonObject();
                JsonArray any = new JsonArray();
                any.add(ps.get(k));
                any.add(obj("type", "null"));
                nullable.add("anyOf", any);
                ps.add(k, nullable);
                req.add(k);
            }
            out.add("required", req);
        }
        return out;
    }

    // ------------------------------------------------------------------ validation

    /** A validated answer, or the error to send back. */
    static final class Result {
        final Answer answer; final String error;
        private Result(Answer a, String e) { answer = a; error = e; }
        static Result ok(Answer a) { return new Result(a, null); }
        static Result bad(String e) { return new Result(null, e); }
    }

    private static boolean absent(JsonObject o, String k) { return !o.has(k) || o.get(k).isJsonNull(); }

    private static Integer intOf(JsonElement e) {
        if (e == null || e.isJsonNull() || !e.isJsonPrimitive()) return null;
        try {
            java.math.BigDecimal d = e.getAsJsonPrimitive().isString() ? new java.math.BigDecimal(e.getAsString().trim()) : e.getAsBigDecimal();
            return d.intValueExact();
        } catch (RuntimeException ex) { return null; }
    }

    private static List<Integer> intList(JsonObject o, String k) {
        if (absent(o, k) || !o.get(k).isJsonArray()) return null;
        List<Integer> out = new ArrayList<>();
        for (JsonElement e : o.getAsJsonArray(k)) {
            Integer i = intOf(e);
            if (i == null) return null;
            out.add(i);
        }
        return out;
    }

    private static String str(JsonObject o, String k) {
        if (absent(o, k) || !o.get(k).isJsonPrimitive()) return null;
        String s = o.get(k).getAsString().replaceAll("\\s+", " ").trim();
        return s.isEmpty() ? null : s;
    }

    /** Checks a call's arguments against the decision. Pure: the error goes back to the model. */
    static Result validate(Spec s, String argumentsJson) {
        JsonObject a = NativeTools.arguments(argumentsJson);
        if (a == null) return Result.bad("the arguments are not a JSON object.");
        String field = fieldOf(s.kind);
        // A proposal answered with a menu field (`option`, `keep`): 9 of 48 Haiku plays did it (runs/propose,
        // 2026-11-03), so the error shows the shape and says there is no list.
        if (s.kind == Kind.ACTION && absent(a, field))
            return Result.bad("FORMAT: this decision is answered with `action`, the play you propose, such as {\"verb\": "
                    + "\"cast\", \"card\": \"@12\"} or {\"verb\": \"pass\"}; there is no numbered list to pick from"
                    + (a.keySet().isEmpty() ? "" : " (you gave " + String.join(", ", a.keySet()) + ")") + ".");
        if (field != null && absent(a, field))
            return Result.bad("this decision is answered with `" + field + "`" + (a.keySet().isEmpty() ? "" : "; you gave "
                    + String.join(", ", a.keySet())) + ".");
        Answer out = new Answer();
        switch (s.kind) {
            case ONE: {
                Integer o = intOf(a.get("option"));
                if (o == null) return Result.bad("the `option` value must be one integer from the list.");
                if (o < 1 || o > s.n) return Result.bad("option " + o + " is not on the list; the options are 1-" + s.n + ".");
                out.option = o;
                break;
            }
            case MANY:
            case MANY_REPEAT: {
                List<Integer> os = intList(a, "options");
                if (os == null) return Result.bad("the `options` value must be a list of option numbers (an empty list for none).");
                for (int o : os) if (o < 1 || o > s.n) return Result.bad("option " + o + " is not on the list; the options are 1-" + s.n + ".");
                if (s.kind == Kind.MANY && new LinkedHashSet<>(os).size() != os.size()) return Result.bad("each option may be picked once.");
                if (s.cancel > 0 && os.contains(s.cancel)) {
                    if (os.size() != 1) return Result.bad("option " + s.cancel + " cancels the play and must be the only number.");
                } else if (os.size() < s.min || os.size() > s.max) {
                    return Result.bad("pick " + bounds(s) + " options; you picked " + os.size() + ".");
                }
                out.options = os;
                break;
            }
            case ALLOC: {
                List<Integer> am = intList(a, "amounts");
                if (am == null) return Result.bad("the `amounts` value must be a list of integers.");
                if (am.size() != s.n) return Result.bad("give exactly " + s.n + " amounts, one per target in order; you gave " + am.size() + ".");
                int sum = 0;
                for (int x : am) { if (x < 0) return Result.bad("amounts cannot be negative."); sum += x; }
                if (sum != s.max) return Result.bad("the amounts must sum to " + s.max + "; yours sum to " + sum + ".");
                out.amounts = am;
                break;
            }
            case KEEP: {
                JsonElement k = a.get("keep");
                if (k == null || !k.isJsonPrimitive() || !k.getAsJsonPrimitive().isBoolean()) return Result.bad("the `keep` value must be true or false.");
                out.keep = k.getAsBoolean();
                break;
            }
            case BLOCKS: {
                if (absent(a, "blocks") || !a.get("blocks").isJsonArray()) return Result.bad("the `blocks` value must be a list (empty for no blocks).");
                List<int[]> pairs = new ArrayList<>();
                java.util.Map<Integer, Integer> used = new java.util.HashMap<>();
                java.util.Set<String> seen = new java.util.HashSet<>();
                for (JsonElement e : a.getAsJsonArray("blocks")) {
                    if (!e.isJsonObject()) return Result.bad("each block is an object with `blocker` and `attacker`.");
                    Integer b = intOf(e.getAsJsonObject().get("blocker")), at = intOf(e.getAsJsonObject().get("attacker"));
                    if (b == null || at == null) return Result.bad("each block needs an integer `blocker` and `attacker`.");
                    if (b < 1 || b > s.n) return Result.bad("blocker " + b + " is not on the list; your blockers are 1-" + s.n + ".");
                    if (at < 1 || at > s.m) return Result.bad("attacker " + at + " is not on the list; the attackers are 1-" + s.m + ".");
                    if (!seen.add(b + ">" + at)) return Result.bad("blocker " + b + " blocks attacker " + at + " twice.");
                    if (used.merge(b, 1, Integer::sum) > s.blockLimit(b))
                        return Result.bad("blocker " + b + " is used " + used.get(b) + " times; it can block "
                                + (s.blockLimit(b) == 1 ? "one attacker." : "at most " + s.blockLimit(b) + " attackers."));
                    pairs.add(new int[] {b, at});
                }
                out.blocks = pairs;
                break;
            }
            case NUMBER: {
                Integer v = intOf(a.get("number"));
                if (v == null) return Result.bad("the `number` value must be an integer.");
                if (v < s.min || v > s.max) return Result.bad(v + " is out of range; choose from " + s.min + " through " + s.max + ".");
                out.number = v;
                break;
            }
            case CARD: {
                String c = str(a, "card");
                if (c == null) return Result.bad("the `card` value must be a card name.");
                out.card = c.replace("\"", "").replace("`", "").trim();
                break;
            }
            case ACTION: {
                String err = proposalOf(a, out);
                if (err != null) return Result.bad(err);
                break;
            }
            default:
                return Result.bad("unsupported decision.");
        }
        if (s.stepOptions != null && !absent(a, "steps")) {
            List<Integer> st = intList(a, "steps");
            if (st == null) return Result.bad("the `steps` value must be a list of option numbers.");
            for (int x : st) if (!s.stepOptions.contains(x)) return Result.bad("step " + x + " is not a CAST/ACTIVATE option of this list.");
            out.steps = st;
        }
        if (s.yield && !absent(a, "yield")) {
            String y = str(a, "yield");
            // "none", or a blank value, is the default a model fills in when it fills every field: no yield.
            if (y != null && !"none".equals(y)) {
                if (!"end-of-turn".equals(y) && !"my-next-turn".equals(y))
                    return Result.bad("the `yield` value is none, end-of-turn or my-next-turn.");
                out.yieldUntil = y;
            }
        }
        if (s.profile) out.profile = str(a, "profile");
        if (s.lines) out.lines = str(a, "lines");
        if (s.keep && !absent(a, "keep_untapped")) {
            if (!a.get("keep_untapped").isJsonArray()) return Result.bad("`keep_untapped` is a list of handles such as \"@12\".");
            List<Integer> hs = new ArrayList<>();
            for (JsonElement e : a.getAsJsonArray("keep_untapped")) {
                java.util.regex.Matcher m = e.isJsonPrimitive() ? java.util.regex.Pattern.compile("@?(\\d+)").matcher(e.getAsString()) : null;
                if (m == null || !m.find()) return Result.bad("`keep_untapped` is a list of handles such as \"@12\".");
                hs.add(Integer.parseInt(m.group(1)));
            }
            // An empty list is what a model that fills every field sends: not said, so the seat is asked when it matters.
            out.keepUntapped = hs.isEmpty() ? null : hs;
        }
        out.why = str(a, "why");
        // The fixed schema names every field; another decision kind's field beside the right one is ignored.
        JsonObject known = (perDecision() ? definition(s) : fixedDefinition())
                .getAsJsonObject("function").getAsJsonObject("parameters").getAsJsonObject("properties");
        for (String k : a.keySet()) if (!known.has(k)) return Result.bad("unknown field `" + k + "`.");
        if (s.check != null) {
            String err;
            try { err = s.check.apply(out); } catch (RuntimeException e) { err = "not legal here."; }
            if (err != null) return Result.bad(err);
        }
        return Result.ok(out);
    }

    /** Reads `action` into {@code out.action}: the form only (the site checks the rules). An error, or null. Every
     *  form error starts "FORMAT:", which the proposal record keeps apart from rule errors. */
    static String proposalOf(JsonObject a, Answer out) {
        if (!a.get("action").isJsonObject()) return "FORMAT: `action` is an object with `verb` and, unless you pass, `card`.";
        JsonObject o = a.getAsJsonObject("action");
        for (String k : o.keySet())
            if (!ACTION_FIELDS.contains(k)) return "FORMAT: `action` has no field `" + k + "`.";
        Proposal p = new Proposal();
        p.verb = str(o, "verb");
        if (p.verb != null) p.verb = p.verb.toLowerCase(java.util.Locale.ROOT).replace(' ', '_').replace('-', '_');
        if (p.verb == null || !PROPOSE_VERBS.contains(p.verb)) return "FORMAT: `verb` is play_land, cast, activate or pass.";
        if (!"pass".equals(p.verb)) {
            String c = str(o, "card");
            java.util.regex.Matcher m = c == null ? null : Pattern.compile("@?\\s*(\\d+)").matcher(c);
            if (m == null || !m.find()) return "FORMAT: `card` is a handle such as \"@12\".";
            p.card = Integer.parseInt(m.group(1));
            if (!absent(o, "ability")) {
                Integer ab = intOf(o.get("ability"));
                if (ab == null || ab < 1) return "FORMAT: `ability` is a number from YOUR ACTIVATED ABILITIES, 1 or more.";
                p.ability = ab;
            }
            p.face = str(o, "face");
            p.cost = str(o, "cost");
            if (!absent(o, "targets")) {
                if (!o.get("targets").isJsonArray()) return "FORMAT: `targets` is a list such as [\"@7\"] or [\"opponent\"].";
                p.targets = new ArrayList<>();
                for (JsonElement e : o.getAsJsonArray("targets")) {
                    if (!e.isJsonPrimitive()) return "FORMAT: each target is a handle such as \"@7\", or \"you\" or \"opponent\".";
                    String t = e.getAsString().trim();
                    if (!t.isEmpty()) p.targets.add(t);
                }
            }
        }
        out.action = p;
        return null;
    }

    /** The answer in the text grammar's words, for the decision row's raw_reply and the one-chat history:
     *  the same line the text protocol would have scraped, so every reader of the row stays valid. */
    static String render(Spec s, Answer a) {
        StringBuilder b = new StringBuilder();
        switch (s.kind) {
            case ONE: b.append("CHOICE: ").append(a.option); break;
            case MANY: case MANY_REPEAT: b.append("PICKS: ").append(a.options.isEmpty() ? "NONE" : join(a.options)); break;
            case ALLOC: b.append("ALLOC: ").append(join(a.amounts)); break;
            case KEEP: b.append(a.keep ? "KEEP" : "MULLIGAN"); break;
            case BLOCKS: {
                b.append("BLOCKS: ");
                if (a.blocks.isEmpty()) b.append("NONE");
                for (int i = 0; i < a.blocks.size(); i++) {
                    int[] p = a.blocks.get(i);
                    b.append(i == 0 ? "" : ", ").append(label(s.labels, p[0])).append("->").append(label(s.labels2, p[1]));
                }
                break;
            }
            case NUMBER: b.append("NUMBER: ").append(a.number); break;
            case CARD: b.append("CARD: ").append(a.card); break;
            // The matched option last, as a CHOICE line: every reader of a play row keeps working.
            case ACTION: b.append("PROPOSED: ").append(a.action.render()).append("\nCHOICE: ").append(a.option); break;
            default: break;
        }
        if (a.steps != null && !a.steps.isEmpty()) b.append("\nSTEPS: options ").append(join(a.steps));
        if (a.yieldUntil != null) b.append("\nYIELD: ").append(a.yieldUntil);
        if (a.profile != null) b.append("\nPROFILE: ").append(a.profile);
        if (a.lines != null) b.append("\nLINES: ").append(a.lines);
        if (a.keepUntapped != null) {
            b.append("\nKEEP UNTAPPED: ");
            for (int i = 0; i < a.keepUntapped.size(); i++) b.append(i == 0 ? "@" : ", @").append(a.keepUntapped.get(i));
        }
        return b.toString();
    }

    private static String label(List<String> labels, int oneBased) {
        return oneBased >= 1 && oneBased <= labels.size() ? labels.get(oneBased - 1) : "#" + oneBased;
    }

    private static String join(List<Integer> xs) {
        StringBuilder b = new StringBuilder();
        for (int x : xs) b.append(b.length() == 0 ? "" : ",").append(x);
        return b.toString();
    }

    // ------------------------------------------------------------------ the ask

    private static final JsonObject FIXED = fixedDefinition();
    private static final JsonObject SET_PLAN_FIXED = setPlanDefinition();

    /** The per-ask line that tells the model how to answer. */
    static String instruction(Spec s, boolean plan) {
        String how;
        switch (s.kind) {
            case ONE: how = "`option`: one number from the list"; break;
            case MANY: case MANY_REPEAT: how = "`options`: the numbers you pick, " + bounds(s) + (s.min == 0 ? " (an empty list for none)" : ""); break;
            case ALLOC: how = "`amounts`: " + s.n + " numbers in the order listed, summing to " + s.max; break;
            case KEEP: how = "`keep`: true to keep, false to mulligan"; break;
            case BLOCKS: how = "`blocks`: pairs of a blocker number and an attacker number (an empty list for no blocks)"; break;
            case NUMBER: how = "`number`: an integer from " + s.min + " through " + s.max; break;
            case CARD: how = "`card`: the exact card name"; break;
            case ACTION: how = "`action`: the play you propose (`verb`, and `card` unless you pass)"; break;
            default: how = "the decision's values"; break;
        }
        return "ANSWER: call `answer` with " + how + "." + (plan ? " Call `set_plan` too if your plan is new or has changed." : "")
                + " Lookups do not count against the answer.\n";
    }

    /** The text-format instructions in a decision text, removed from a tool ask: its one answer line is the
     *  `ANSWER:` line the ask appends ({@link #instruction}). The question builders are shared with the
     *  ai-teacher seat and the text lane and stay byte-identical; only this ask's wire text changes. Their
     *  "Reason briefly" goes with them: no reasoning cap on a tool ask (2026-10-16 review M1). */
    private static final Pattern TAIL = Pattern.compile(
            " ?(?:Reason briefly, then |Then |Now |[Tt]hen )?(?:end|End|reply|Reply) with (?:EXACTLY|exactly)[^\\n]*");
    private static final Pattern RETRY = Pattern.compile(
            "\\(?(?:Your previous reply had no|Your previous reply contained no|Answer with ONE line only|That reply contained neither"
            + "|That was not a legal or recognizable card name|Invalid answer\\. Use exactly)[^\\n]*");

    /** A format hint inside a question ("(reply ALLOC: 2 numbers that sum to 3)"). */
    private static final Pattern INLINE_FORMAT = Pattern.compile(" ?\\(reply [A-Z]+: [^)\\n]*\\)");

    /** The faithful plan line's own instruction (LlmSpikeGui.revisablePlanText). */
    static final String PLAN_LINE_HINT = "write a new `PLAN: GOAL: <what you are working toward> NEXT: <the steps>` line before your answer";

    static String rephrase(String prompt, Spec s) { return rephrase(prompt, s, false); }

    /** `plan`: this ask offers `set_plan`, so the plan line points at it. */
    static String rephrase(String prompt, Spec s, boolean plan) {
        if (prompt == null) return null;
        prompt = prompt.replace(PLAN_LINE_HINT, plan ? "call `set_plan` with a new goal and next steps"
                : "call `set_plan` at a decision that offers it");
        String out = TAIL.matcher(prompt).replaceAll("");
        out = INLINE_FORMAT.matcher(out).replaceAll("");
        return RETRY.matcher(out).replaceAll("(Your previous reply did not call `answer` with a legal value. Call `answer` now.)");
    }

    /** A text answer format left in a tool ask: a way to answer other than `answer`, which a model will take
     *  (owner, 2026-10-15). Tags the text grammar reads (`CHOICE: 2`, `PICKS:`, `STEPS:` ...), "end with
     *  exactly", "KEEP or MULLIGAN", and the text tool grammar. */
    private static final Pattern TEXT_FORMAT = Pattern.compile(
            "\\b(?:CHOICE|PICKS|BLOCKS|ALLOC|ATTACKS|ATTACKERS|SIMULATE|STEPS|YIELD|PROFILE|LINES|NUMBER|CARD):\\s*<"
            + "|`(?:PLAN|STEPS|YIELD|PROFILE|LINES|SIMULATE|CHOICE|PICKS|CARD|NUMBER):"
            + "|(?:[Ee]nd|[Rr]eply|[Aa]nswer) with (?:EXACTLY|exactly)"
            + "|KEEP or MULLIGAN|instead of an action, you may reply");

    /** The first text answer format in {@code prompt}, or null when the only way to answer is a call. */
    static String textFormatLeak(String prompt) {
        if (prompt == null) return null;
        java.util.regex.Matcher m = TEXT_FORMAT.matcher(prompt);
        if (!m.find()) return null;
        int a = Math.max(0, m.start() - 60), b = Math.min(prompt.length(), m.end() + 60);
        return prompt.substring(a, b).replace('\n', ' ');
    }

    /** One decision's answer session: what is offered, and what came back. */
    static final class Ask {
        /** The ask's messages once answered by a call, ending in the answer's tool result (null otherwise). */
        JsonArray transcript;
        final Spec spec;
        final boolean plan;
        /** Receives "GOAL: ... NEXT: ..." from `set_plan`. */
        Consumer<String> planSink = p -> { };
        Answer answer;
        /** "tool" (answered by a valid call), "nudged" (a valid call after one reminder that the reply called
         *  nothing), "text" (a text reply after that reminder, read by the text parser), "lost" (two invalid
         *  calls). Null while unanswered or when the bridge returned nothing. */
        String via;
        /** The reply called nothing once and was asked, in the same conversation, for the call. */
        boolean nudged;
        int nudges;
        int invalid;
        boolean lost;
        /** ACTION: every `answer` call in order, {arguments, error or null when accepted}. */
        final List<String[]> proposals = new ArrayList<>();
        /** Text the model wrote alongside its answer call: its reasoning, kept for the row and the
         *  carried play intent. */
        String content;

        Ask(Spec spec, boolean plan) { this.spec = spec; this.plan = plan; }

        /** The same two functions on every ask (fixed schema), so the tool list never breaks the cache. */
        JsonArray definitions() {
            JsonArray a = new JsonArray();
            if (!perDecision()) {
                a.add(FIXED);
                a.add(SET_PLAN_FIXED);
                return a;
            }
            a.add(definition(spec));
            if (plan) a.add(setPlanDefinition());
            return a;
        }

        boolean answered() { return answer != null; }

        /** The row's raw_reply: the model's text, its `why`, then the answer in the old line format (last, where
         *  every existing reader looks for it). */
        String render() {
            String r = AnswerTool.render(spec, answer);
            if (answer.why != null) r = "WHY: " + answer.why + "\n" + r;
            return content == null || content.trim().isEmpty() ? r : content.trim() + "\n" + r;
        }

        /** A note call: the result text for the model. */
        String note(NativeTools.Call c) {
            if (!SET_PLAN.equals(c.name)) return "ERROR: unknown tool `" + c.name + "`.";
            if (!plan && perDecision()) return "ERROR: set_plan is not offered at this decision.";
            JsonObject a = NativeTools.arguments(c.arguments);
            String goal = a == null ? null : str(a, "goal"), next = a == null ? null : str(a, "next");
            if (goal == null || next == null) return "ERROR: set_plan needs `goal` and `next`.";
            planSink.accept("GOAL: " + goal + " NEXT: " + next);
            return "Plan recorded. It does not answer the decision: call `answer`.";
        }
    }

    /** The lookups an answer ask also offers (ContextMemory's executor and budget), or none. */
    interface Lookups {
        JsonArray definitions();
        /** True when a lookup may still run in this round. */
        boolean open(int round);
        /** The result of one lookup call; refused with the budget error when not open. */
        String serve(NativeTools.Call c, boolean open);
    }

    /** The lookahead preview alone, for an answer ask on a seat without MEMORY_TOOLS (LOOKAHEAD=1):
     *  as many previews as the ask has calls (MODEL-LIMITS.md), on rounds whose result can still be read. Offered on EVERY ask of such
     *  a seat, so the tool list (the start of the cache prefix) never changes; `simulate` is null on a
     *  decision with no play menu, and a call there is refused with the reason. */
    static Lookups previewOnly(Function<String, String> simulate) {
        int[] used = {0};
        return new Lookups() {
            public JsonArray definitions() {
                JsonArray out = new JsonArray();
                for (JsonElement t : NativeTools.definitions(false, false, true))
                    if (NativeTools.SIMULATE.equals(t.getAsJsonObject().getAsJsonObject("function").get("name").getAsString())) out.add(t);
                return out;
            }
            public boolean open(int round) { return simulate != null && used[0] < ContextMemory.TOOLS_PER_ASK && round < ContextMemory.TOOLS_PER_ASK; }
            public String serve(NativeTools.Call c, boolean open) {
                if (!NativeTools.SIMULATE.equals(c.name)) return "ERROR: unknown tool `" + c.name + "`. Call `answer`.";
                if (simulate == null) return NativeTools.NO_PREVIEW + " Call `answer`.";
                if (!open) return "ERROR: no lookups left for this decision. Call `answer`.";
                String r;
                try { r = simulate.apply(c.arguments); } catch (RuntimeException e) { r = "ERROR: preview failed: " + e; }
                if (r == null) r = "ERROR: preview returned nothing.";
                if (!r.startsWith("ERROR:")) used[0]++;
                return r;
            }
        };
    }

    static final String CALL_ANSWER = "No lookups are available for this decision any more. Answer it now by calling `answer`.";
    /** Reminders for replies that call nothing, before the decision is lost. Text is never read as an answer on
     *  a tool ask (owner, 2026-10-15: every decision goes through the tool call). */
    static final int MAX_NUDGES = 2;
    /** The reminder for a reply that called nothing. */
    static final String ANSWER_BY_CALL = "You did not call `answer`, so nothing was done. Call `answer` now with your choice; "
            + "the decision's last line says which field it takes.";

    /**
     * One decision over native function calling, answered by `answer`. Notes are applied first, then the
     * answer (the first `answer` call in a turn that validates; it ends the ask), then lookups. Returns the
     * answer rendered as text, or null: no reply, two invalid replies, {@link #MAX_NUDGES} reminders that
     * still called nothing, or no answer within {@link #MAX_ROUNDS}. A reply's prose is never parsed as the
     * answer; a decision lost for want of a call is {@code lost}, so the caller takes its fixed legal
     * option and does not ask the whole decision again.
     */
    static String converse(String user, Ask ask, ContextMemory.NativeModel model, Lookups lookups,
                           BiConsumer<String, String> audit) {
        JsonArray tools = lookups == null ? new JsonArray() : lookups.definitions();
        tools.addAll(ask.definitions());
        String toolsJson = tools.toString();
        String first = user + (user.endsWith("\n") ? "" : "\n") + instruction(ask.spec, ask.plan);
        JsonArray messages = new JsonArray();
        messages.add(NativeTools.message("user", first));
        boolean pressed = false;
        boolean proposal = ask.spec.kind == Kind.ACTION;
        int maxInvalid = proposal ? MAX_INVALID_PROPOSALS : 2;
        // A proposal's extra tries get their own rounds, so the lookups it may make are not taken away.
        int rounds = MAX_ROUNDS + maxInvalid - 2;
        for (int round = 0; round < rounds; round++) {
            boolean open = lookups != null && lookups.open(round);
            // tool_choice "auto" every round: the answer must stay callable, and forcing is not an option.
            NativeTools.Turn turn = model.turn(first, messages.toString(), toolsJson, true, round);
            if (turn == null) return null;
            if (turn.calls.isEmpty()) {
                if (ContextMemory.toolLine(turn.content) != null || NativeTools.looksLikeCallMarkup(turn.content)) {
                    audit.accept(turn.content, "ERROR: a tool request must be a function call; no tool executed.");
                    if (pressed) return null;
                    pressed = true;
                    messages.add(NativeTools.message("user", CALL_ANSWER));
                    continue;
                }
                // A reply that explains its choice but calls nothing ("I choose option 5") is asked, in this
                // conversation, for the call. Its prose is never read as the answer: a text alternative is one a
                // model will take (owner, 2026-10-15), so after the reminders the decision is lost, not parsed.
                if (ask.nudges < MAX_NUDGES) {
                    ask.nudges++;
                    ask.nudged = true;
                    messages.add(JsonParser.parseString(turn.messageJson));
                    messages.add(NativeTools.message("user", ANSWER_BY_CALL));
                    audit.accept(turn.content, "NUDGE: no `answer` call; asked for one in the same conversation.");
                    continue;
                }
                audit.accept(turn.content, "LOST: no `answer` call after " + MAX_NUDGES + " reminders.");
                ask.lost = true;
                ask.via = "lost";
                return null;
            }
            messages.add(JsonParser.parseString(turn.messageJson));
            String[] results = new String[turn.calls.size()];
            for (int i = 0; i < results.length; i++) {
                NativeTools.Call c = turn.calls.get(i);
                if (SET_PLAN.equals(c.name)) { results[i] = ask.note(c); audit.accept(c.render(), results[i]); }
            }
            // An illegal `answer` call before a legal one is not the answer (v12); a reply whose every `answer`
            // call is illegal is one invalid reply.
            boolean calledAnswer = false;
            for (int i = 0; i < results.length; i++) {
                NativeTools.Call c = turn.calls.get(i);
                if (!NAME.equals(c.name)) continue;
                calledAnswer = true;
                Result r = validate(ask.spec, c.arguments);
                if (proposal) ask.proposals.add(new String[] {c.arguments, r.answer == null ? r.error : null});
                if (r.answer == null) {
                    results[i] = "ERROR: " + r.error + " Nothing was done. Call `answer` again with a legal value.";
                    audit.accept(c.render(), results[i]);
                    continue;
                }
                ask.answer = r.answer;
                ask.via = ask.nudged ? "nudged" : "tool";
                ask.content = turn.content;
                audit.accept(c.render(), "ANSWER ACCEPTED");
                // The whole ask as it went, for a seat that keeps its conversation (ChatSessionBridge): the
                // answering message is already in `messages`; a result for every call it made follows, so
                // the history is a valid tool-call sequence with the model's reasoning in place.
                // An illegal `answer` call before this one is not merged into it: its fields are not used.
                for (int j = 0; j < results.length; j++) {
                    String res = j == i ? "Answer recorded: " + ask.render()
                            : j < i && NAME.equals(turn.calls.get(j).name)
                                    ? "Not used: a later `answer` call in this reply answered the decision."
                            : results[j] != null ? results[j] : "Not run: the decision was answered in this reply.";
                    messages.add(NativeTools.toolResult(turn.calls.get(j).id, res));
                }
                ask.transcript = messages;
                return ask.render();
            }
            if (calledAnswer) {
                ask.invalid++;
                if (ask.invalid >= maxInvalid) { ask.lost = true; ask.via = "lost"; return null; }
            }
            boolean refused = false;
            for (int i = 0; i < results.length; i++) {
                if (results[i] != null) continue;
                NativeTools.Call c = turn.calls.get(i);
                if (lookups == null) results[i] = "ERROR: unknown tool `" + c.name + "`. Call `answer`.";
                else { results[i] = lookups.serve(c, open); if (!open) refused = true; }
                audit.accept(c.render(), results[i]);
            }
            for (int i = 0; i < results.length; i++) messages.add(NativeTools.toolResult(turn.calls.get(i).id, results[i]));
            if (refused && !calledAnswer) {
                if (pressed) return null;
                pressed = true;
                messages.add(NativeTools.message("user", CALL_ANSWER));
            }
        }
        return null;
    }
}
