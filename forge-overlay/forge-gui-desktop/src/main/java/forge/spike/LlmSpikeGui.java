package forge.spike;

import java.awt.Graphics2D;
import java.awt.image.BufferedImage;
import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;

import javax.imageio.ImageIO;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.EnumSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import javax.swing.SwingUtilities;

import org.apache.commons.lang3.tuple.ImmutablePair;

import com.google.common.collect.Lists;

import forge.GuiDesktop;
import forge.LobbyPlayer;
import forge.Singletons;
import forge.StaticData;
import forge.card.ICardFace;
import forge.ai.AiCardMemory;
import forge.ai.ComputerUtilAbility;
import forge.ai.ComputerUtilCombat;
import forge.ai.ComputerUtilMana;
import forge.ai.LobbyPlayerAi;
import forge.ai.PlayerControllerAi;
import forge.deck.Deck;
import forge.deck.DeckSection;
import forge.deck.DeckgenUtil;
import forge.game.Game;
import forge.game.GameEntity;
import forge.game.GameLogEntry;
import forge.game.GameLogEntryType;
import forge.game.GameRules;
import forge.game.GameType;
import forge.game.ability.ApiType;
import forge.game.card.Card;
import forge.game.card.CardCollection;
import forge.game.card.CardCollectionView;
import forge.game.card.CardLists;
import forge.game.card.CardPredicates;
import forge.game.card.CounterEnumType;
import forge.game.card.CounterType;
import forge.game.combat.Combat;
import forge.game.combat.CombatUtil;
import forge.game.keyword.Keyword;
import forge.game.phase.PhaseHandler;
import forge.game.phase.PhaseType;
import forge.game.player.DelayedReveal;
import forge.game.player.Player;
import forge.game.player.PlayerActionConfirmMode;
import forge.game.player.RegisteredPlayer;
import forge.game.spellability.AbilitySub;
import forge.game.spellability.SpellAbility;
import forge.game.spellability.TargetRestrictions;
import forge.game.zone.ZoneType;
import forge.gamemodes.match.HostedMatch;
import forge.gui.GuiBase;
import forge.view.FFrame;
import forge.view.FView;
import forge.util.ITriggerEvent;
import forge.util.MyRandom;
import forge.util.collect.FCollectionView;

/** Backend-agnostic LLM bridge. The controller sends a full snapshot on every ask,
 *  so implementations may be stateless (API) or persistent (CLI) — correctness does
 *  not depend on the bridge remembering prior turns. */
interface LLMBridge {
    /** Send a prompt, return the model's text reply, or null on failure/non-answer. */
    String ask(String prompt);
    String getLabel();
    String getModel();
    long getTotalMs();
    int getCalls();
    // No close(): there is no game-end teardown site — both launchers rely on JVM exit
    // to reap CLI subprocesses (see the System.exit comment in LlmSpikeHeadless.main).
    /** One-shot reset for stateful bridges (CLI); stateless backends (API) can no-op. */
    default void restart() { }
    /** The OpenAI-compatible `finish_reason` ("stop", "length", ...) of the most recently completed
     *  ask(), or null when the backend doesn't report one (Claude CLI) or nothing has completed yet.
     *  A plain last-value field is sufficient — the controller's ask() path is single-threaded per
     *  seat — and gives the eval-record layer a way to distinguish a clean stop from a token-budget
     *  truncation without re-deriving it from raw_reply. */
    default String getLastFinishReason() { return null; }
    /** Prompt (input) token count reported by the backend's response `usage` object for the most
     *  recently completed ask(), or -1 when the backend reports no usage (Claude CLI) or nothing has
     *  completed yet. Persisted per-decision in the eval JSONL so Stage-0 calibration can sum true
     *  input/output token cost without re-deriving it. Same last-value single-threaded-per-seat
     *  contract as getLastFinishReason(). */
    default int getLastPromptTokens() { return -1; }
    /** Completion (output) token count from the response `usage` object for the most recent ask(),
     *  or -1 when unreported. See {@link #getLastPromptTokens()}. */
    default int getLastCompletionTokens() { return -1; }
}

/**
 * SPIKE (throwaway): launch the real Forge desktop window and WATCH two custom
 * LLM PlayerControllers play a game AGAINST EACH OTHER, in spectator mode.
 *
 * v2 of the Opus-vs-Opus variant. Goals of this pass:
 *   1. FULL CONTEXT — every decision prompt carries a compact snapshot of the whole
 *      game state (both boards, hands, graveyards, life, lands, stack) plus a per-seat
 *      deck game-plan framing sent once into a persistent per-seat conversation (chat style).
 *   2. ROUTE (NEARLY) ALL DECISIONS TO THE LLM — beyond mulligan/play/attack/block, a single
 *      generic decision router is adapted onto the high-value choice callbacks: discard
 *      (the Faithless-Looting / reanimator-fuel fix), single & multi entity selection,
 *      cards-for-effect, confirmAction, chooseNumber. Spell TARGETING is routed too:
 *      chosen spells get engine-validated, LLM-picked targets set BEFORE the AI cast path
 *      (ensureTargetsSet — that path runs no targeting for ordinary spells and would fizzle),
 *      and a chooseTargetsFor override covers the remaining engine-initiated targeting paths.
 *   3. MODEL TIERING — a fast model (haiku) handles high-frequency low-stakes spots; the
 *      smart model (opus) handles load-bearing sequencing/combat/targets. A per-game call
 *      budget and try/catch->AI on every override guarantee it never hangs or crashes.
 *
 * Run with working dir = forge-gui-desktop/ so assets resolve to ../forge-gui/res.
 */
public final class LlmSpikeGui {
    private LlmSpikeGui() {}

    /** Eval-mode JSONL logger (opt-in via EVAL_OUT=<dir>). One file per game; every decision and the
     *  final game summary land here so batch runs produce a dataset, not scrollback. */
    static final class EvalLog {
        private static volatile java.io.PrintWriter out; // volatile: on() reads unsynchronized from both seats
        static synchronized void init(long seed) {
            String dir = System.getenv("EVAL_OUT");
            if (dir == null || dir.isEmpty()) return;
            initToDir(dir, seed);
        }
        /** Open the per-game jsonl under {@code dir} (env-independent). Production {@link #init} reads
         *  EVAL_OUT; LlmSpikeTest drives this directly since it cannot set an env var in-process. */
        static synchronized void initToDir(String dir, long seed) {
            if (out != null) return;
            try {
                java.io.File d = new java.io.File(dir);
                d.mkdirs();
                out = new java.io.PrintWriter(new java.io.FileWriter(
                        new java.io.File(d, "game-" + seed + "-" + System.currentTimeMillis() + ".jsonl"), true), true);
            } catch (Exception e) { System.out.println("[EVAL] log init failed: " + e); }
        }
        static synchronized void write(String json) { if (out != null) out.println(json); }
        static boolean on() { return out != null; }
        /** Test-only: flush + detach the writer so a subsequent {@link #initToDir} can open a fresh file. */
        static synchronized void closeForTest() {
            if (out != null) { out.flush(); out.close(); out = null; }
        }
    }

    /** The seed applySeed() actually used. Read by the D4 shadow labeler to derive its ephemeral RNG
     *  streams, so shadow draws differ across games in a batch instead of replaying one fixed stream.
     *  Set at the single seeding point both runners share (GUI + headless). */
    static long SEED_USED = 0L;

    /** GAME_SEED env: seed Forge's global RNG (shuffles, coin flip) for reproducible games.
     *  Returns the seed used (random if unset) so it can be recorded. */
    static long applySeed() {
        long seed;
        try {
            String v = System.getenv("GAME_SEED");
            seed = (v == null || v.isEmpty()) ? new java.util.Random().nextLong() : Long.parseLong(v.trim());
        } catch (Exception e) { seed = new java.util.Random().nextLong(); }
        MyRandom.setRandom(new java.util.Random(seed));
        SEED_USED = seed;
        System.out.println("[SEED] " + seed);
        return seed;
    }

    // Default strategy framings, shared with the headless runner (env PLAN_A/PLAN_B override both).
    static final String DEFAULT_PLAN_A =
            "Grixis Reanimator combo: mulligan aggressively toward a hand that can DISCARD a huge creature "
            + "(Griselbrand, Atraxa Grand Unifier, Archon of Cruelty) via Faithless Looting / Cabal Therapy / Unmask, "
            + "then REANIMATE it turn 1-2 with Reanimate / Animate Dead / Shallow Grave (use Dark Ritual + Lotus Petal for speed). "
            + "Griselbrand's draw-7 refuels and protects. Strip disruption with Thoughtseize/Cabal Therapy. Win fast before they set up.";
    static final String DEFAULT_PLAN_B =
            "Mardu Energy aggro-disruption: deploy efficient threats (Ajani, Ocelot Pride, Guide of Souls, Amped Raptor, "
            + "Voice of Victory), attack every turn and race their life total. Disrupt the combo with Thoughtseize, Cabal Therapy, "
            + "Orcish Bowmasters, and Swords to Plowshares; Wasteland/Karakas hit their mana. Against Reanimator, prioritize stripping "
            + "or removing their fatty and pressuring life so they can't durdle.";

    // The plans actually injected this run (env override > per-deck <deck>.plan sidecar > default above),
    // stashed by buildPlayers so the game-summary provenance records what the model really saw — not a
    // re-derivation that would miss the sidecar. See loadPlanSidecar / writeGameSummary.
    static volatile String lastPlanA = DEFAULT_PLAN_A;
    static volatile String lastPlanB = DEFAULT_PLAN_B;

    /** Our agent's controller: extend the AI, route decisions to Claude, delegate to super on
     *  anything we don't handle or that fails. One instance per seat — all state is per-instance. */
    static class PlayerControllerLLM extends PlayerControllerAi {
        private static final int MAX_PLAY_DECISIONS_PER_TURN = 25;
        private static final int SMART_BUDGET = envInt("SMART_BUDGET", 320); // per-game load-bearing LLM calls
        private static final int FAST_BUDGET  = envInt("FAST_BUDGET", 900);  // per-game cheap LLM calls
        // Eval confound switches: MULLIGAN_HEURISTIC=0 -> the model decides ALL mulligans (no 2-4 land
        // auto-keep); SKIP_HEURISTICS=0 -> no free-pass shortcuts (every priority window with options
        // reaches the model). Defaults preserve the fast demo behavior.
        private static final boolean MULL_HEURISTIC = !"0".equals(System.getenv("MULLIGAN_HEURISTIC"));
        private static final boolean SKIP_HEUR = !"0".equals(System.getenv("SKIP_HEURISTICS"));
        // D4 DAgger shadow-labeling: when SHADOW_AI=1, four overrides also compute the Forge-AI's choice
        // over the SAME option menu the student sees and stash it in lastShadowChoice, which emitDecisionRow
        // writes as an additive `shadow_choice` field on the STUDENT'S existing decision row (never a
        // sibling row — a sibling would inflate the defect_analyzer's model_consulted count and break the
        // 1:1 silent-drop reconciliation). Every shadow helper is guarded `if (!SHADOW) return null` and
        // wrapped in try/catch(Throwable)->null so a forge-ai throw can NEVER affect the student's decision
        // or the game trajectory.
        //
        // MEASURED (do not assume otherwise): floor+SHADOW_AI=1 is NOT a free labeler. Floor mode
        // short-circuits every play at `play(no-budget)->PASS` and returns BEFORE the shadow hook, which
        // needs the enumerated menu (`po`) floor never builds — a seed-42 floor game yields 997 play rows
        // and ZERO play labels (only the 2 mulligan rows label, that path being pre-budget). This is
        // correct, not a gap to close: DAgger's premise is labeling states the STUDENT'S policy visits,
        // and floor states aren't student states. build_dagger_examples already drops these rows anyway
        // (no prompt -> drop_no_prompt). Real D4 data therefore requires llm-controller games, which
        // cost student inference — budget DAgger rounds accordingly.
        private static final boolean SHADOW = "1".equals(System.getenv("SHADOW_AI"));

        private final String seat;        // player label (for logs)
        private final String deckPlan;    // strategy framing for this seat
        private final LLMBridge smart;    // smart tier — sequencing, combat, targets
        private final LLMBridge fast;     // fast tier — confirms, numbers, cleanup
        private int smartCalls = 0, fastCalls = 0;
        private int autoPicks = 0;      // deterministic LEGAL fallback (a sane engine-supplied option, NOT Forge's AI brain)
        private int aiDelegations = 0;  // true handoff to Forge's AI for a mechanic we don't model — we want this ~0
        // Per-seat bridge-health tracking (ask(), see below): consecutive no-reply outcomes across
        // decisions, and whether the loud [CIRCUIT-BREAKER] marker has already fired for the CURRENT
        // streak (reset the moment a reply succeeds, so it can fire again on a later streak).
        private int consecutiveBridgeFailures = 0;
        private boolean circuitBreakerTripped = false;

        // Decision-record provenance (per-controller, so the two seats never cross-contaminate): the FULL
        // prompt + raw reply of the most recent model call for the decision currently being made. Cleared at
        // the start of every decision entry point / decideOne / decideMany, so a stale prompt from a previous
        // decision can never be attributed to a later one. null == no model consulted for this decision.
        private String lastPrompt = null;
        private String lastReply  = null;
        private String lastTier   = null; // "smart"|"fast" of the captured call (informational)
        private long lastAskMs = 0; // wall-clock summed over ALL ask() attempts since clearLast() (kept for the
                                     // ms<=0 legacy substitution guard below; latency_ms itself now uses lastSuccessMs)
        // ADDITIVE (Group-2 observability): the last ask()'d bridge's finish_reason (see
        // LLMBridge.getLastFinishReason), null for backends that don't report one.
        private String lastFinishReason = null;
        // ADDITIVE (Stage-0 token calibration): prompt/completion token counts from the last ask()'d
        // bridge's response usage object (LLMBridge.getLast{Prompt,Completion}Tokens), -1 when the
        // backend reports none (Claude CLI) or no model was consulted for this decision.
        private int lastPromptTokens = -1;
        private int lastCompletionTokens = -1;
        // ADDITIVE (Group-2): latency split — the SUCCESSFUL final attempt's own wall time (this is what
        // jsonl latency_ms now reports) vs. everything else (failed bridge sub-attempts, backoff waits,
        // and any earlier caller-level ask() round-trip whose reply was superseded by a later retry) which
        // is dead time folded into jsonl retry_ms instead. lastAttempts counts every individual model
        // round-trip (bridge-level + caller-level retries) made for the current decision.
        private long lastSuccessMs = 0;
        private long lastRetryMs = 0;
        private int lastAttempts = 0;
        // First reply that came back but was superseded by a later ask() (e.g. a caller re-asked after
        // failing to parse it) — capped at 500 chars, null if every attempt this decision either failed
        // outright or the very first attempt was the one that stuck.
        private String lastFirstFailedReply = null;
        // Set by decideOne() the moment it logs an explicit "decline" jsonl row itself (the noneLabel path)
        // so callers handling decideOne's -1 return value can tell "already logged" apart from "still
        // unparseable after retry, never logged" without guessing — see decideOne below.
        private boolean lastDeclineLogged = false;
        // D4 DAgger: the Forge-AI's shadow label over THIS decision's menu (same encoding as `choice`),
        // or null when SHADOW is off, the shadow computation threw, or this decision has no shadow path.
        // Reset every decision so a stale shadow can never attach to a later row (same discipline as lastPrompt).
        private Integer lastShadowChoice = null;
        private void clearLast() {
            lastPrompt = null; lastReply = null; lastTier = null; lastAskMs = 0; lastFinishReason = null;
            lastPromptTokens = -1; lastCompletionTokens = -1;
            lastSuccessMs = 0; lastRetryMs = 0; lastAttempts = 0; lastFirstFailedReply = null; lastDeclineLogged = false;
            lastShadowChoice = null;
        }

        // per-turn play-loop state (was static in v1 — now per-instance so two seats don't collide)
        private int lastPlayTurn = -1;
        private int playDecisionsThisTurn = 0;
        private String lastAskSig = "";
        private boolean lastWasPass = false;
        // (stack, options) situations we already declined this turn — identical recurrences pass free.
        private final java.util.HashSet<String> passedReactSigs = new java.util.HashSet<>();
        // Turn plan: stated by the model at its first main-phase decision of the turn, injected into
        // every later prompt that turn — so targets/discards/follow-ups execute ONE line, not myopic picks.
        private String turnPlan = "";
        // Names of the attackers the model actually declared last time declareAttackers got a real
        // model answer (empty until then). Consulted by the bridge-down fallback below (report C3) so a
        // dead bridge mid-combat degrades toward the prior, model-chosen attack plan instead of a blind
        // all-in that can undo an explicitly-stated single-attacker (Exalted etc.) line.
        private final java.util.Set<String> lastDeclaredAttackerNames = new java.util.HashSet<>();
        private int planTurn = -1;

        // Game-log types worth narrating to the model (skip PHASE/MANA/INFORMATION noise).
        private static final java.util.Set<GameLogEntryType> EVENT_TYPES = EnumSet.of(
                GameLogEntryType.TURN, GameLogEntryType.MULLIGAN, GameLogEntryType.ZONE_CHANGE,
                GameLogEntryType.DAMAGE, GameLogEntryType.LIFE, GameLogEntryType.LAND,
                GameLogEntryType.DISCARD, GameLogEntryType.COMBAT, GameLogEntryType.STACK_ADD,
                GameLogEntryType.STACK_RESOLVE, GameLogEntryType.GAME_OUTCOME);

        // Rolling strategic intel (revealed hands, cards we named). Included in every snapshot so it
        // survives the periodic bridge-session refresh — the model keeps what it LEARNED even when the
        // conversation restarts for speed.
        private final java.util.ArrayDeque<String> intel = new java.util.ArrayDeque<>();
        private void remember(String fact) {
            intel.addLast("T" + safeTurn() + ": " + fact);
            while (intel.size() > 8) intel.removeFirst();
        }
        private int safeTurn() {
            try { return getPlayer().getGame().getPhaseHandler().getTurn(); } catch (Exception e) { return 0; }
        }

        // Baseline arms for eval runs (CONTROLLER_A/B): "llm" (default), "random" (uniform over the
        // same legal menus — anchors the floor of the scale), "floor" (every decision takes the
        // deterministic fallback path — quantifies how much play strength the HARNESS contributes).
        private final String mode;
        private final java.util.Random rnd; // non-null only in random mode

        PlayerControllerLLM(Game game, Player p, LobbyPlayer lp, String seat, String deckPlan,
                            String smartModel, String fastModel, String mode) {
            this(game, p, lp, seat, deckPlan,
                    LLMBridgeFactory.create(seat + "/smart", smartModel, framing(seat, deckPlan, false)),
                    LLMBridgeFactory.create(seat + "/fast",  fastModel,  framing(seat, deckPlan, true)),
                    mode);
        }

        /** Package-private test seam: inject bridges directly instead of building them via
         *  LLMBridgeFactory from a model spec string. Lets LlmSpikeTest exercise decision-routing logic
         *  (e.g. the bridge-down fallback ladder) against a fake in-package LLMBridge deterministically,
         *  without spawning a real CLI process or making a real network call. Production code always
         *  goes through the String-model constructor above, which just delegates here. */
        PlayerControllerLLM(Game game, Player p, LobbyPlayer lp, String seat, String deckPlan,
                            LLMBridge smartBridge, LLMBridge fastBridge, String mode) {
            super(game, p, lp);
            this.seat = seat;
            this.deckPlan = deckPlan;
            this.mode = mode == null || mode.isEmpty() ? "llm" : mode;
            this.rnd = "random".equals(this.mode) ? new java.util.Random(MyRandom.getRandom().nextLong()) : null;
            this.smart = smartBridge;
            this.fast  = fastBridge;
            if (!"llm".equals(this.mode)) p("[MODE] baseline arm: " + this.mode);
        }

        private void p(String s) { System.out.println("[" + seat + "] " + s); }
        private static int envInt(String k, int def) {
            try { String v = System.getenv(k); return v == null ? def : Integer.parseInt(v.trim()); }
            catch (Exception e) { return def; }
        }
        private static String framing(String seat, String plan, boolean terse) {
            String format = terse
                    // fast tier answers high-frequency low-stakes spots: reasoning tokens are pure latency there
                    ? "FORMAT: reply with ONLY the final line in EXACTLY the format I request (e.g. `CHOICE: 3`). "
                      + "NO reasoning, no other text — every extra token slows the game down."
                    : "FORMAT: at most two short sentences of reasoning, then a final line in EXACTLY the format I "
                      + "request (e.g. `CHOICE: 3` or `PICKS: 1,4`). I parse ONLY that final line — never write "
                      + "anything after it.";
            String base = "You are a world-class Magic: The Gathering (Legacy) player piloting \"" + seat + "\".\n"
                    // PLAN_A=""/PLAN_B="" gives a plan-free eval arm — no expert strategy injected.
                    + (plan == null || plan.isEmpty() ? "" : "GAME PLAN: " + plan + "\n")
                    + "PROTOCOL: I am the Forge rules engine. Each message gives you the CURRENT game state and ONE "
                    + "decision with a numbered list of the ONLY legal options — every option is already "
                    + "engine-validated, so pick purely on strategy. The state snapshot in each message is "
                    + "authoritative: trust it over your memory of earlier turns. You have NO tools and cannot "
                    + "inspect anything yourself — everything you need is in the message; answer directly.\n"
                    + "PRINCIPLES: (1) Sequence toward your game plan, not the shiniest single play. "
                    + "(2) At instant speed with nothing to gain, PASS. "
                    + "(3) In combat, count damage and their untapped mana before committing. "
                    + "(4) Life, cards, and tempo are resources — spend them to win, not to feel safe. "
                    + "(5) Card selection is fuel: with spare mana, a draw/loot/dig spell, and no better line, "
                    + "CAST IT — repeatedly passing while holding cantrips is how combo decks lose. "
                    + "(6) If your key piece is gone (stripped/bounced), your plan is to FIND another (dig), "
                    + "not to accumulate mana with nothing to spend it on.\n"
                    + format + " Play to WIN the game, not just the current decision.";
            // SMART-TIER STATIC APPENDIX (byte-identical for every decision in every game — no game state):
            // a snapshot-format legend (derived from snapshot()/candidateLabel/manaAvailable/landSummary so
            // it is exact), worked CHOICE-reasoning examples, and a Legacy strategy/keyword glossary for the
            // eval decks. Load-bearing reasoning support for high-stakes decisions, so it goes on the smart
            // tier only — the fast tier's contract is terse/no-reasoning, and bloating high-frequency cheap
            // calls would be pure latency there. Because it carries zero volatile state it keeps the system
            // message byte-stable per (seat, tier), the prompt-caching prerequisite once a cache breakpoint
            // is added (see wiki "Prompt Caching for the MTG Harness via OpenRouter").
            return terse ? base : base + "\n\n" + STATIC_REFERENCE;
        }
        /** See framing(): byte-stable, game-state-free reference appended to the smart-tier system
         *  message. Kept as one constant so it is literally identical across seats/games. */
        private static final String STATIC_REFERENCE =
                "=== SNAPSHOT LEGEND (how to read the game state in each message) ===\n"
              + "Header `== Turn N PHASE (active: NAME) ==`: the turn number, current phase/step, and whose\n"
              + "turn it is. PHASE is one of UPKEEP, DRAW, MAIN1, COMBAT_BEGIN, COMBAT_DECLARE_ATTACKERS,\n"
              + "COMBAT_DECLARE_BLOCKERS, COMBAT_DAMAGE, MAIN2, END_OF_TURN, CLEANUP. `active:` is the player\n"
              + "whose turn it is — if it is not you, you are acting at instant speed on their turn.\n"
              + "`YOU [seat] life L | energy E, poison P/10`: your life total, then optional counters — `energy`\n"
              + "only shows when >0; `poison P/10` always shows (10 poison = you lose; infect/poison decks race it).\n"
              + "`hand(n): Name {cost} P/T (tag), ...`: your hand. `{cost}` is the mana cost in brace notation\n"
              + "(`{2}{B}` = two generic + one black; omitted for lands). `P/T` power/toughness shows for\n"
              + "creatures only. The affordability `(tag)` is a real payment check, not a guess: `(castable)` =\n"
              + "you can pay right now; `(need 1 more B)` = short exactly that color; `(not castable now)` = a\n"
              + "cost too complex to break down (hybrid/Phyrexian/X) or short on total mana.\n"
              + "`board: ...`: your NONLAND permanents (lands are summarized separately). Each: `Name(P/T)` for\n"
              + "creatures, `[loyalty N]` for planeswalkers, `[T]` = tapped, `[sick]` = summoning-sick (a creature\n"
              + "that cannot attack or use {T} abilities yet), and `[+1/+1 x2]`-style counters. `(no nonland\n"
              + "permanents)` means an empty board.\n"
              + "`lands: U untapped / T total (Name xU [untapped], Name xK [tapped])`: your land count and the\n"
              + "per-copy tap breakdown — duplicate-named lands are listed separately by tap state so you can\n"
              + "target the right copy.\n"
              + "`mana available: {U:2 R:1 C:1} (K untapped sources)`: how many untapped sources CAN make each\n"
              + "color right now (dual lands count in BOTH colors but can only make one at a time — a deliberate\n"
              + "over-count), and the count of distinct untapped mana permanents. Use it to check you can pay a\n"
              + "line before committing.\n"
              + "`yard(n): ...`: your graveyard (reanimator/delve/flashback fuel; dredge/escape enablers).\n"
              + "`OPP [name] life L ...`: the opponent's public state — board, lands, `mana available`, yard, and\n"
              + "`hand: N cards` (a COUNT only; their card identities are hidden unless INTEL revealed them).\n"
              + "Their untapped `mana available` is your read on open removal/counters — play around it.\n"
              + "`stack (top first): N pending: #k Name (player) -> targets | ...`: spells/abilities waiting to\n"
              + "resolve, top of stack first (it resolves first). `(empty)` means an open priority window with\n"
              + "nothing pending. `-> targets` shows what each is aimed at.\n"
              + "`RECENT EVENTS (oldest->newest)`: the engine's own log of what happened between your decisions\n"
              + "(opponent plays, combat results, discards). `INTEL: ...`: durable facts you learned (revealed\n"
              + "hands, cards you named). `YOUR TURN PLAN (...)`: the one-line plan you stated earlier this turn —\n"
              + "execute it or consciously deviate.\n"
              + "Option lists label each card the same way: `Name (P/T)[loyalty N][tapped]/[untapped][counters]`,\n"
              + "so duplicate-named permanents are always distinguishable — read the tag before you pick.\n"
              + "\n"
              + "=== ANSWER CONTRACT (the exact final line I parse for each decision kind) ===\n"
              + "- Numbered single pick -> `CHOICE: <number>` (one number from the Options list; a 'decline/none'\n"
              + "  option, when offered, is just another number).\n"
              + "- Choose a subset (discard, reveal, cards-for-effect, scry/surveil keep-on-top, sacrifice) ->\n"
              + "  `PICKS: <comma-separated numbers>`, honoring the stated 'choose between MIN and MAX' bound.\n"
              + "  For scry/surveil, list the ones to KEEP ON TOP in order (first = new top card); anything you\n"
              + "  omit goes to the bottom/graveyard.\n"
              + "- Declare attackers -> `PICKS:` the numbers of the creatures that attack (empty/none = no attack).\n"
              + "- Declare blockers -> `BLOCKS: blocker->attacker, blocker->attacker` (or `BLOCKS: NONE`).\n"
              + "- Mulligan -> exactly one word, `KEEP` or `MULLIGAN`.\n"
              + "I read ONLY that final line; write it verbatim with nothing after it.\n"
              + "\n"
              + "=== WORKED EXAMPLES (format + the kind of reasoning that wins; illustrative, not this game) ===\n"
              + "Example A - sequencing a cantrip before a threat: 'I have UU open, a Brainstorm, and a\n"
              + "reanimation target still in hand. Digging first can find the missing piece and I lose nothing by\n"
              + "casting it now.' -> `CHOICE: 2`\n"
              + "Example B - combat math before attacking: 'They have one 2/2 untapped blocker and no open mana.\n"
              + "My 3/3 and 2/1 both attack: they can only block one, so at least 2 damage connects and a trade is\n"
              + "fine for me on tempo.' -> `PICKS: 1,3`\n"
              + "Example C - passing correctly: 'It is their end step, my only instant is a sorcery-speed threat,\n"
              + "and holding up nothing gains me a card next turn. Nothing to do here.' -> `CHOICE: 4` (the PASS /\n"
              + "decline option).\n"
              + "Example D - not over-committing into removal: 'They hold 2 cards and UU untapped in a counter\n"
              + "deck. I bait with the cheaper threat first rather than jamming my key payoff into an open\n"
              + "counterspell.' -> `CHOICE: 1`\n"
              + "\n"
              + "=== LEGACY GLOSSARY (mechanics + the eval-deck archetypes you will face) ===\n"
              + "Tempo: trading mana-efficiency and board presence for initiative — cheap threats + disruption\n"
              + "(Delver). Card advantage: ending up with more resources than the opponent. Cantrip: a cheap spell\n"
              + "that replaces itself (Brainstorm, Ponder, Preordain) — chains dig you to your key cards. Combo:\n"
              + "assemble a specific interaction to win outside combat (Storm, Sneak & Show). Prison/lock:\n"
              + "denying the opponent resources (Lands' Tabernacle + Wasteland).\n"
              + "Keywords: Storm (copies the spell once per spell cast this turn — cast the cheap enablers FIRST,\n"
              + "count storm before the payoff). Delve (exile graveyard cards to pay generic mana). Cascade /\n"
              + "Prowess / Flash / Flying / Trample / Deathtouch / Lifelink / Hexproof / Shroud (untargetable) /\n"
              + "Infect (deals damage as -1/-1 counters to creatures and POISON to players; 10 poison kills).\n"
              + "Energy: a counter resource spent by Mardu Energy payoffs. Reanimate: cheat a big creature from\n"
              + "the graveyard into play (Animate Dead, Reanimator) — sometimes DISCARDING the fatty is the setup,\n"
              + "not a loss. Miracle: cast a card for its cheaper cost the turn you draw it (Miracles/Terminus).\n"
              + "Archetypes in this pool: ANT/Storm (ritual mana -> Ad Nauseam -> lethal storm count; protect the\n"
              + "combo turn, count mana and storm exactly). Burn (Goblin Guide/Swiftspear + direct damage; every\n"
              + "point of reach to 0 matters, do not waste burn on blockers unless forced). Death & Taxes\n"
              + "(Thalia/Mother of Runes hatebears + Wasteland/Rishadan Port mana denial; tax and grind). Elves\n"
              + "(mana-dork snowball into Craterhoof-style overrun; develop then alpha strike). Goblins (Lackey/\n"
              + "Ringleader tribal aggro-value; recur bodies, go wide). Grixis Reanimator (discard + Animate Dead\n"
              + "on Atraxa/Archon early; find a target and an enabler, protect the turn). Infect (Glistener Elf/\n"
              + "Blighted Agent + pump; ONE connection near 10 poison is lethal — protect the creature, count\n"
              + "poison). Lands (Wasteland/Port + Tabernacle prison into Marit Lage; deny mana, grind inevitability).\n"
              + "Mardu Energy (aggressive energy payoffs; curve out, use energy at the right beat). Miracles\n"
              + "(Brainstorm/Ponder set up Terminus/Entreat wraths; control the board, win late). Sneak & Show\n"
              + "(Show and Tell / Sneak Attack a huge threat like Emrakul years early; force it through the counter\n"
              + "window). UR Delver (Delver of Secrets + cantrips + burn + counters; tempo — protect the clock,\n"
              + "counter their key spell, race).\n"
              + "General rule for all of them: know YOUR deck's win condition, advance it every turn, and use each\n"
              + "decision to protect the line that actually wins — not to react locally.";

        // ---------------- generic decision router ----------------

        /** Ask a bridge to pick ONE option (1-based in the prompt); returns 0-based index, or -1 for
         *  none/parse-failure. `noneLabel` (nullable) adds an extra "decline" option. */
        private int decideOne(LLMBridge b, String type, String question, List<String> options, String noneLabel) {
            clearLast();
            long t0 = System.currentTimeMillis();
            // RANDOM arm: uniform over the same legal menu (incl. the decline option when present).
            if (rnd != null) {
                int n = options.size() + (noneLabel != null ? 1 : 0);
                if (n == 0) return -1;
                int c = rnd.nextInt(n);
                int res = c >= options.size() ? -1 : c;
                if (type != null) evalDec(type, options.size(), res, tierOf(b == smart), System.currentTimeMillis() - t0, null, options);
                return res;
            }
            int noneIdx = noneLabel != null ? options.size() + 1 : -1; // 1-based menu number of the decline option
            String base = buildDecideOnePrompt(snapshot(), question, options, noneLabel);
            for (int attempt = 0; attempt < 2; attempt++) {
                String prompt = attempt == 0 ? base
                        : "That reply had no parseable choice. Answer with ONE line only, nothing else: CHOICE: <a number from the list above>.";
                String reply = ask(b, prompt);
                if (reply == null) return -2;              // bridge dead -> caller decides
                int c = parseTagNumber(reply, "CHOICE:");
                if (c == noneIdx && noneIdx > 0) {         // explicit decline (a real model decision)
                    if (type != null) { // tells the caller its -1 return is already a logged row, not a silent drop
                        evalDec(type, options.size(), -1, tierOf(b == smart), 0, null, options);
                        lastDeclineLogged = true;
                    }
                    return -1;
                }
                if (c >= 1 && c <= options.size()) {
                    // ms=0 -> evalDec substitutes lastSuccessMs (the successful attempt's own time only,
                    // never the summed dead-retry time; see the latency-split fields near clearLast()).
                    if (type != null) evalDec(type, options.size(), c - 1, tierOf(b == smart), 0, null, options);
                    return c - 1;
                }
            }
            return -1;                                      // still unparseable after retry -> caller's legal default (caller logs the fallback)
        }

        /** Ask a bridge to pick BETWEEN min..max options; returns 0-based indices, or null on failure. */
        private List<Integer> decideMany(LLMBridge b, String type, String question, List<String> options, int min, int max) {
            clearLast();
            long t0 = System.currentTimeMillis();
            // RANDOM arm: uniformly sized random subset of the same legal menu.
            if (rnd != null) {
                List<Integer> all = new ArrayList<>();
                for (int i = 0; i < options.size(); i++) all.add(i);
                java.util.Collections.shuffle(all, rnd);
                int lo = Math.max(0, min), hi = Math.min(max, options.size());
                if (hi < lo) return null;
                List<Integer> res = new ArrayList<>(all.subList(0, lo + (hi > lo ? rnd.nextInt(hi - lo + 1) : 0)));
                if (type != null) evalDec(type, options.size(), res.size(), tierOf(b == smart), System.currentTimeMillis() - t0, null, options);
                return res;
            }
            String base = buildDecideManyPrompt(snapshot(), question, options, min, max);
            for (int attempt = 0; attempt < 2; attempt++) {
                String prompt = attempt == 0 ? base
                        : "That reply had no parseable PICKS line. Answer with ONE line only, nothing else: PICKS: <"
                          + min + " to " + max + " numbers from the list above, comma-separated>.";
                String reply = ask(b, prompt);
                if (reply == null) return null;
                String seg = lastSegment(reply, "PICKS:");
                if (seg == null) continue;
                java.util.LinkedHashSet<Integer> picks = new java.util.LinkedHashSet<>();
                Matcher m = Pattern.compile("\\d+").matcher(seg);
                while (m.find()) {
                    int idx = Integer.parseInt(m.group());
                    if (idx >= 1 && idx <= options.size()) picks.add(idx - 1);
                }
                List<Integer> out = new ArrayList<>(picks);
                if (out.size() > max) out = out.subList(0, max);
                if (out.size() >= min) {
                    // ms=0 -> evalDec substitutes lastSuccessMs (successful-attempt-only time); see decideOne.
                    if (type != null) evalDec(type, options.size(), out.size(), tierOf(b == smart), 0, null, options);
                    return out;
                }
            }
            return null; // still short after retry -> caller's legal default
        }

        // ---------------- full-context serializer ----------------

        private String snapshot() { return buildSnapshot(getPlayer(), seat, intel, turnPlan, planTurn); }
        /** Shared, state-free snapshot builder (extracted from {@link #snapshot()} so the AI-logging
         *  seat can produce a byte-format-identical game-state prompt). The instance {@code snapshot()}
         *  above just forwards its per-seat fields, so the LLM seat's output is unchanged; the
         *  ai-teacher seat passes an empty intel deque + no turn plan (it accumulates neither). */
        static String buildSnapshot(Player me, String seat, java.util.Collection<String> intel, String turnPlan, int planTurn) {
            try {
                Game g = me.getGame();
                PhaseHandler ph = g.getPhaseHandler();
                Player opp = null;
                for (Player o : me.getOpponents()) { opp = o; break; }
                StringBuilder s = new StringBuilder();
                s.append("== Turn ").append(ph.getTurn()).append(' ').append(ph.getPhase())
                        .append(" (active: ").append(ph.getPlayerTurn() != null ? ph.getPlayerTurn().getName() : "?").append(") ==\n");
                s.append("YOU [").append(seat).append("] life ").append(me.getLife()).append(playerCounters(me)).append('\n');
                s.append("  hand(").append(me.getCardsIn(ZoneType.Hand).size()).append("): ").append(handList(me, me.getCardsIn(ZoneType.Hand))).append('\n');
                s.append("  board: ").append(perms(me)).append('\n');
                s.append("  lands: ").append(landSummary(me)).append('\n');
                s.append("  mana available: ").append(manaAvailable(me)).append('\n');
                s.append("  yard(").append(me.getCardsIn(ZoneType.Graveyard).size()).append("): ").append(names(me.getCardsIn(ZoneType.Graveyard), true, 30)).append('\n');
                if (opp != null) {
                    s.append("OPP [").append(opp.getName()).append("] life ").append(opp.getLife()).append(playerCounters(opp)).append('\n');
                    s.append("  board: ").append(perms(opp)).append('\n');
                    // Opp lands are public info and load-bearing: untapped mana = removal/counter risk,
                    // and utility lands (Wasteland, Karakas) are threats worth playing around.
                    s.append("  lands: ").append(landSummary(opp)).append('\n');
                    s.append("  mana available: ").append(manaAvailable(opp)).append('\n');
                    s.append("  yard(").append(opp.getCardsIn(ZoneType.Graveyard).size()).append("): ").append(names(opp.getCardsIn(ZoneType.Graveyard), true, 30)).append('\n');
                    s.append("  hand: ").append(opp.getCardsIn(ZoneType.Hand).size()).append(" cards\n");
                }
                int stackSize = g.getStack().size();
                s.append("stack (top first): ").append(stackSize == 0 ? "(empty)" : (stackSize + " pending: " + safeStackTop(g))).append('\n');
                // HISTORY: the engine's own game log — narrates what happened BETWEEN this seat's
                // decisions (opponent's plays, combat results, discards) and survives session refreshes.
                try {
                    List<GameLogEntry> ev = g.getGameLog().getLogEntriesForTypes(EVENT_TYPES); // newest-first
                    int n = Math.min(10, ev.size());
                    if (n > 0) {
                        s.append("RECENT EVENTS (oldest->newest):\n");
                        for (int i = n - 1; i >= 0; i--) s.append("  - ").append(oneLine(ev.get(i).message())).append('\n');
                    }
                } catch (Exception ignore) { }
                if (!intel.isEmpty()) s.append("INTEL: ").append(String.join(" | ", intel)).append('\n');
                // Same-turn plan continuity: the line the model declared at its first main-phase decision.
                if (planTurn == ph.getTurn() && ph.isPlayerTurn(me) && !turnPlan.isEmpty())
                    s.append("YOUR TURN PLAN (stated earlier this turn — execute it or consciously deviate): ").append(turnPlan);
                return s.toString();
            } catch (Exception e) {
                return "== (state unavailable) ==";
            }
        }
        /** Full stack contents, top-of-stack first: each item's source, activating player, and its
         *  current targets (if any) — not just the top card. Also doubles as the reactive dedup
         *  signature key (a richer signature only sharpens that cache: two situations that share a
         *  top card but differ in targets no longer collapse into the same "already declined" bucket). */
        private static String safeStackTop(Game g) {
            try {
                int n = g.getStack().size();
                if (n == 0) return "(empty)";
                StringBuilder b = new StringBuilder();
                int idx = n;
                for (forge.game.spellability.SpellAbilityStackInstance si : g.getStack()) {
                    SpellAbility sa = si.getSpellAbility();
                    if (b.length() > 0) b.append(" | ");
                    b.append('#').append(idx--);
                    try {
                        b.append(' ').append(sa.getHostCard() != null ? sa.getHostCard().getName() : String.valueOf(sa));
                    } catch (Exception e) { b.append(" ?"); }
                    try {
                        Player who = sa.getActivatingPlayer();
                        if (who != null) b.append(" (").append(who.getName()).append(')');
                    } catch (Exception ignore) { }
                    try {
                        if (sa.getTargets() != null && !sa.getTargets().isEmpty()) {
                            StringBuilder t = new StringBuilder();
                            for (Object o : sa.getTargets()) {
                                if (t.length() > 0) t.append(", ");
                                t.append(o instanceof Card ? ((Card) o).getName() : (o instanceof Player ? ((Player) o).getName() : String.valueOf(o)));
                            }
                            if (t.length() > 0) b.append(" -> ").append(t);
                        }
                    } catch (Exception ignore) { }
                }
                return b.toString();
            } catch (Exception e) { return "?"; }
        }
        /** Per-COPY land listing, tap state included, never deduped by name: "Volcanic Island x2
         *  [untapped], Volcanic Island x1 [tapped]". A prior version deduped to distinct names only,
         *  so 3 identical-named lands (2 untapped, 1 tapped) rendered as one indistinguishable
         *  "Volcanic Island" — the QF-3 misfire where the model asked for "the untapped Volcanic
         *  Island," got a 3-way tie in the option list, and wasted a Rishadan Port activation on the
         *  tapped copy. Grouped (not one line per card) so the count still reads at a glance. */
        static String landSummary(Player pl) {
            int untapped = 0, total = 0;
            java.util.LinkedHashMap<String, int[]> byName = new java.util.LinkedHashMap<>(); // name -> [untappedCount, tappedCount]
            for (Card c : pl.getCardsIn(ZoneType.Battlefield)) {
                if (!c.isLand()) continue;
                total++;
                boolean tapped = c.isTapped();
                if (!tapped) untapped++;
                int[] counts = byName.computeIfAbsent(c.getName(), k -> new int[2]);
                counts[tapped ? 1 : 0]++;
            }
            StringBuilder kinds = new StringBuilder();
            for (Map.Entry<String, int[]> e : byName.entrySet()) {
                int u = e.getValue()[0], t = e.getValue()[1];
                if (u > 0) { if (kinds.length() > 0) kinds.append(", "); kinds.append(e.getKey()).append(" x").append(u).append(" [untapped]"); }
                if (t > 0) { if (kinds.length() > 0) kinds.append(", "); kinds.append(e.getKey()).append(" x").append(t).append(" [tapped]"); }
            }
            return untapped + " untapped / " + total + " total"
                    + (kinds.length() == 0 ? "" : " (" + kinds + ")");
        }
        /** [0]=W [1]=U [2]=B [3]=R [4]=G [5]=C untapped-source counts, [6]=count of distinct untapped
         *  permanents that produce ANY mana. Best-effort: basic land types (including on non-basic
         *  "true duals" like Volcanic Island, which carry Island+Mountain per rule 305.6) map directly
         *  to color; any other declared mana ability (Wasteland's {C}, a mana dork's Combo/Any) is read
         *  from its raw Produced$ string, NEVER simulated/paid — this is prompt context, not a mana
         *  solver, so it can never trigger a chosen-color/filter-land side effect from a read-only
         *  render path. A dual land counts in both its colors' buckets (it can only actually make one
         *  at a time) — an intentional, documented over-count, not a payment guarantee. */
        private static int[] untappedManaTally(Player pl) {
            int[] t = new int[7];
            for (Card card : pl.getCardsIn(ZoneType.Battlefield)) {
                if (card.isTapped()) continue;
                boolean any = false;
                try {
                    var ty = card.getType();
                    if (ty.hasSubtype("Plains"))   { t[0]++; any = true; }
                    if (ty.hasSubtype("Island"))   { t[1]++; any = true; }
                    if (ty.hasSubtype("Swamp"))    { t[2]++; any = true; }
                    if (ty.hasSubtype("Mountain")) { t[3]++; any = true; }
                    if (ty.hasSubtype("Forest"))   { t[4]++; any = true; }
                } catch (Exception ignore) { }
                try {
                    for (SpellAbility ab : card.getManaAbilities()) {
                        forge.game.spellability.AbilityManaPart mp = ab.getManaPart();
                        if (mp == null) continue;
                        String produced = mp.getOrigProduced();
                        if (produced == null) continue;
                        for (String tok : produced.toUpperCase(Locale.ROOT).split("\\s+")) {
                            switch (tok) {
                                case "W": t[0]++; any = true; break;
                                case "U": t[1]++; any = true; break;
                                case "B": t[2]++; any = true; break;
                                case "R": t[3]++; any = true; break;
                                case "G": t[4]++; any = true; break;
                                case "C": t[5]++; any = true; break;
                                case "ANY": t[0]++; t[1]++; t[2]++; t[3]++; t[4]++; any = true; break;
                                default: break; // COMBO/CHOSEN/SPECIAL/COLORIDENTITY etc. — no single color to name, safe undercount
                            }
                        }
                    }
                } catch (Exception ignore) { }
                if (any) t[6]++;
            }
            return t;
        }
        /** "{U:2 R:1 C:1} (3 untapped sources)" — colors with zero sources are omitted. */
        private static String manaAvailable(Player pl) {
            int[] t = untappedManaTally(pl);
            String[] label = {"W", "U", "B", "R", "G", "C"};
            StringBuilder b = new StringBuilder("{");
            for (int i = 0; i < 6; i++) {
                if (t[i] == 0) continue;
                if (b.length() > 1) b.append(' ');
                b.append(label[i]).append(':').append(t[i]);
            }
            return b.append('}').append(" (").append(t[6]).append(" untapped sources)").toString();
        }
        /** Hand listing with mana cost + P/T + a compact affordability tag ("(castable)" /
         *  "(need 1 more B)") reusing the SAME ComputerUtilMana.canPayManaCost check that pre-filters
         *  the real CAST menu (see chooseSpellAbilityToPlay) — replaces "can I cast this?" guesswork
         *  with an answer instead of adding bloat. The color-shortfall breakdown only fires for plain
         *  colored/generic/colorless costs (hybrid/Phyrexian/X costs fall back to a plain
         *  "(not castable now)" — naming a single missing color there would be a guess, not a fact). */
        private static String handList(Player pl, Iterable<Card> cards) {
            int[] tally = untappedManaTally(pl);
            StringBuilder b = new StringBuilder(); int i = 0;
            for (Card c : cards) {
                if (i++ >= 40) { b.append(", ..."); break; }
                if (b.length() > 0) b.append(", ");
                b.append(c.getName());
                try {
                    String mc = costStr(c);
                    if (mc != null && !c.isLand()) b.append(' ').append(mc);
                } catch (Exception ignore) { }
                if (c.isCreature()) b.append(' ').append(c.getNetPower()).append('/').append(c.getNetToughness());
                if (!c.isLand()) {
                    try {
                        String afford = affordability(pl, c, tally);
                        if (afford != null) b.append(' ').append(afford);
                    } catch (Exception ignore) { }
                }
            }
            return b.length() == 0 ? "(empty)" : b.toString();
        }
        /** "(castable)" when the real payment check passes; otherwise a best-effort "(need N more X)"
         *  per short color (never simulates a payment plan, just compares raw pip counts to the
         *  untapped-mana tally) — or a non-committal "(not castable now)" when the cost isn't a plain
         *  colored/generic/colorless one, or no color deficit explains the failure (e.g. total mana is
         *  short, not any one color). Never blocks a cast the engine would actually allow — this is a
         *  hint appended to the label, nothing more. */
        private static String affordability(Player pl, Card c, int[] tally) {
            try {
                SpellAbility sa = c.getFirstSpellAbility();
                if (sa == null) return null;
                if (sa.getActivatingPlayer() == null) sa.setActivatingPlayer(pl);
                if (ComputerUtilMana.canPayManaCost(sa, pl, 0, false)) return "(castable)";
            } catch (Exception e) { return null; }
            try {
                forge.card.mana.ManaCost mc = c.getManaCost();
                if (mc == null) return "(not castable now)";
                int[] need = new int[6]; // W U B R G C
                boolean simple = true;
                for (forge.card.mana.ManaCostShard shard : mc) {
                    if (shard == forge.card.mana.ManaCostShard.WHITE) need[0]++;
                    else if (shard == forge.card.mana.ManaCostShard.BLUE) need[1]++;
                    else if (shard == forge.card.mana.ManaCostShard.BLACK) need[2]++;
                    else if (shard == forge.card.mana.ManaCostShard.RED) need[3]++;
                    else if (shard == forge.card.mana.ManaCostShard.GREEN) need[4]++;
                    else if (shard == forge.card.mana.ManaCostShard.COLORLESS) need[5]++;
                    else simple = false; // hybrid/Phyrexian/X/snow — deficit too ambiguous to name a single color
                }
                StringBuilder why = new StringBuilder();
                if (simple) {
                    String[] label = {"W", "U", "B", "R", "G", "C"};
                    for (int i = 0; i < 6; i++) {
                        if (need[i] > tally[i]) {
                            if (why.length() > 0) why.append(", ");
                            why.append("need ").append(need[i] - tally[i]).append(" more ").append(label[i]);
                        }
                    }
                }
                return why.length() > 0 ? "(" + why + ")" : "(not castable now)";
            } catch (Exception e) { return "(not castable now)"; }
        }
        private static String names(Iterable<Card> cards, boolean withPT, int cap) {
            StringBuilder b = new StringBuilder(); int i = 0;
            for (Card c : cards) {
                if (i++ >= cap) { b.append(", ..."); break; }
                if (b.length() > 0) b.append(", ");
                b.append(c.getName());
                if (withPT && c.isCreature()) b.append('(').append(c.getNetPower()).append('/').append(c.getNetToughness()).append(')');
            }
            return b.length() == 0 ? "(none)" : b.toString();
        }
        /** " | energy 3, poison 1/10" for player-level counters the model must see (Mardu ENERGY runs
         *  on them; poison is ALWAYS shown, even at 0/10 — poison is a second win condition every
         *  game, and hiding it at 0 is exactly how a life-only "COUNT LETHAL" hint misleads an infect
         *  matchup: the model never learns poison is even being tracked until it's already relevant). */
        private static String playerCounters(Player pl) {
            StringBuilder b = new StringBuilder();
            try {
                int en = pl.getCounters(CounterEnumType.ENERGY);
                if (en > 0) b.append("energy ").append(en);
                int po = pl.getCounters(CounterEnumType.POISON);
                if (b.length() > 0) b.append(", ");
                b.append("poison ").append(po).append("/10");
            } catch (Exception ignore) { }
            return b.length() == 0 ? "" : " | " + b;
        }
        /** permanents with P/T, tapped/sick flags, loyalty, and counters (+1/+1 etc.). */
        private static String perms(Player pl) {
            StringBuilder b = new StringBuilder();
            for (Card c : pl.getCardsIn(ZoneType.Battlefield)) {
                if (c.isLand()) continue; // lands summarized separately for `me`; keep opp board tight too
                if (b.length() > 0) b.append(", ");
                b.append(c.getName());
                if (c.isCreature()) b.append('(').append(c.getNetPower()).append('/').append(c.getNetToughness()).append(')');
                if (c.isPlaneswalker()) { try { b.append("[loyalty ").append(c.getCurrentLoyalty()).append(']'); } catch (Exception ignore) { } }
                if (c.isTapped()) b.append("[T]");
                if (c.isSick() && c.isCreature()) b.append("[sick]");
                try { // non-loyalty counters: "+1/+1 x2" etc. — P/T above already includes them, but the model should see WHY
                    com.google.common.collect.Multiset<CounterType> cts = c.getCounters();
                    if (cts != null && !cts.isEmpty()) {
                        StringBuilder cb = new StringBuilder();
                        for (com.google.common.collect.Multiset.Entry<CounterType> e : cts.entrySet()) {
                            if (e.getElement().is(CounterEnumType.LOYALTY) || e.getCount() <= 0) continue;
                            if (cb.length() > 0) cb.append(", ");
                            cb.append(e.getElement().getName()).append(" x").append(e.getCount());
                        }
                        if (cb.length() > 0) b.append('[').append(cb).append(']');
                    }
                } catch (Exception ignore) { }
            }
            return b.length() == 0 ? "(no nonland permanents)" : b.toString();
        }

        // ---------------- existing 4 decisions, now with full context + smart bridge ----------------

        @Override
        public boolean mulliganKeepHand(Player firstPlayer, int cardsToReturn) {
            clearLast();
            lastShadowChoice = shadowMulligan(firstPlayer, cardsToReturn); // all mulligan evalDec paths carry it
            CardCollectionView hand = player.getCardsIn(ZoneType.Hand);
            int lands = CardLists.filter(hand, CardPredicates.LANDS).size();
            int size = hand.size();
            // HARD FLOOR: never spiral below 5 cards. A functional 5-card hand beats mulliganing to oblivion —
            // this is the guardrail against a model that likes to ship every imperfect hand.
            if (size <= 5) {
                p("[ROUTER] mulligan: FLOOR keep (" + lands + "/" + size + ")");
                evalDec("mulligan", 2, 0, "auto", 0, "mulligan(floor-keep " + lands + "/" + size + ")",
                        java.util.Arrays.asList("KEEP", "MULLIGAN"));
                return true;
            }
            if (rnd != null) { auto("mulligan(random)->KEEP"); return true; } // random arm: keep 7 always
            // A 2-4 land opener is a routine, near-always-keepable hand -> keep deterministically, no LLM.
            // (Disabled by MULLIGAN_HEURISTIC=0 for confound-free eval runs — the model owns mulligans.)
            if (MULL_HEURISTIC && lands >= 2 && lands <= 4) {
                p("[ROUTER] mulligan: heuristic KEEP (" + lands + "/" + size + " lands)");
                evalDec("mulligan", 2, 0, "auto", 0, "mulligan(heuristic-keep " + lands + "/" + size + " lands)",
                        java.util.Arrays.asList("KEEP", "MULLIGAN"));
                return true;
            }
            // Only the extremes (0-1 lands, or 5+ = flood) reach the model, and only above the 5-card floor.
            if (!smartOk()) { auto("mulligan(no-model)->KEEP"); return true; } // deterministic legal, not the AI
            StringBuilder n = new StringBuilder();
            for (Card c : hand) { if (n.length() > 0) n.append(", "); n.append(c.getName()); }
            // Shared template (single source; the ai-teacher seat rebuilds byte-identically via the same
            // builder). afterKeep == size: this Forge deals a shrinking hand each mulligan; you keep what you see.
            String basePrompt = buildMulliganPrompt(size, lands, n.toString());
            p("[ROUTER] mulligan: " + lands + "/" + size + " lands -> asking " + smart.getModel() + "...");
            List<String> mullOpts = java.util.Arrays.asList("KEEP", "MULLIGAN");
            // Retry once on an unparseable reply (matching every decideOne/decideMany site). Read the
            // LAST keyword, not the first: the prompt demands the decision be the final word, and the
            // reasoning body routinely contains "mulligan"/"mulliganing" (e.g. "no reason to mulligan
            // ... KEEP"). First-indexOf matched that prose token and inverted the model's actual choice
            // — observed on 10/31 mulligans in haiku-vs-ai-2, every one a KEEP shipped as a mulligan.
            for (int attempt = 0; attempt < 2; attempt++) {
                String prompt = attempt == 0 ? basePrompt
                        : "That reply contained neither KEEP nor MULLIGAN. Answer with ONE word only: KEEP or MULLIGAN.";
                String reply = ask(smart, prompt);
                // Bridge dead -> the SAME deterministic KEEP the unparsed-after-retry branch below
                // uses, never Forge AI (this file's blanket hard invariant: a dead bridge always takes
                // the deterministic legal default, same ladder as confirm/attackers/surveil).
                if (reply == null) {
                    evalDec("mulligan", 2, 0, "auto", 0, "mulligan(bridge-down)->KEEP", mullOpts);
                    p("[ROUTER] mulligan: KEEP (bridge-down)");
                    return true;
                }
                String r = reply.toUpperCase();
                int k = r.lastIndexOf("KEEP"), mu = r.lastIndexOf("MULLIGAN");
                if (k < 0 && mu < 0) continue; // no decision token -> retry, then deterministic fallback below
                boolean keep = k >= 0 && (mu < 0 || k > mu);
                evalDec("mulligan", 2, keep ? 0 : 1, "smart", 0, null, mullOpts);
                p("[ROUTER] mulligan: " + (keep ? "KEEP" : "MULLIGAN"));
                return keep;
            }
            // Unparseable after retry: deterministic KEEP (never Forge AI), logged visibly so the
            // analyzer counts it — matches the no-model default at the top of this method.
            evalDec("mulligan", 2, 0, "auto", 0, "mulligan(unparsed)->KEEP", mullOpts);
            p("[ROUTER] mulligan: KEEP (unparsed after retry)");
            return true;
        }

        /** Chooses WHICH cast-mode/variant of a card the engine has ALREADY decided to play (MDFC
         *  face, a kicked/AlternateAdditionalCost cost bundle, a Discover/"cast while searching"
         *  form, etc.) — see PlaySpellAbility.chooseOptionalAdditionalCosts, PlayEffect,
         *  DiscoverEffect, and ChangeZoneEffect.handleCastWhileSearching, all of which call this on
         *  an AI-driven seat (the GUI-only InputPassPriority/InputPayMana call sites never fire for
         *  a non-human controller). This IS a real strategic choice — which face of an MDFC, whether
         *  to pay the extra cost for an upgraded mode — not a rules technicality, so it's ROUTED like
         *  every other decideOne site rather than merely observed. It was the file's last unlogged
         *  Forge-AI delegation: previously a bare `super.*` call (PlayerControllerAi's own
         *  implementation is just `abilities.get(0)`) with zero jsonl/log trace. In practice
         *  `abilities` is almost always exactly the one SpellAbility already chosen upstream
         *  (GameActionUtil.getAdditionalCostSpell seeds it with `[sa]` and only grows it for a
         *  card with an AlternateAdditionalCost keyword) — the forced-only-legal short-circuit
         *  below means the routing costs nothing on that hot path. WHETHER to play hostCard at all
         *  was already decided upstream by chooseSingleEntityForEffect + confirmAction (both already
         *  routed), so there's no decline option here either — matches the default AI's own
         *  never-null behavior whenever abilities is non-empty. */
        @Override
        public SpellAbility getAbilityToPlay(Card hostCard, List<SpellAbility> abilities, ITriggerEvent triggerEvent) {
            clearLast();
            if (abilities == null || abilities.isEmpty()) return null; // callers already guard this; preserve the contract
            String host = hostCard != null ? hostCard.getName() : "?";
            if (abilities.size() == 1) { auto("ability-to-play(forced-only-legal)[" + host + "]"); return abilities.get(0); }
            try {
                if (!smartOk()) { auto("ability-to-play(no-budget)->first[" + host + "]"); return abilities.get(0); }
                List<String> labels = new ArrayList<>();
                for (SpellAbility sa : abilities) labels.add(saLabel(sa));
                String q = "Choose HOW to play " + host + " -- pick the specific cast mode / cost variant.";
                int c = decideOne(smart, "ability-to-play", q, labels, null);
                if (c == -2) { auto("ability-to-play(bridge-down)->first[" + host + "]"); return abilities.get(0); }
                if (c < 0) { auto("ability-to-play(unparsed)->first[" + host + "]"); return abilities.get(0); }
                p("[ROUTER] ability-to-play: " + labels.get(c));
                return abilities.get(c);
            } catch (Exception e) {
                auto("ability-to-play(err)->first[" + host + "]");
                return abilities.get(0);
            }
        }

        @Override
        public void declareAttackers(Player attacker, Combat combat) {
            clearLast();
            try {
                Player defender = null;
                for (Player pl : combat.getDefendingPlayers()) { defender = pl; break; }
                if (defender == null) { noteAi("declareAttackers(no-defender)"); super.declareAttackers(attacker, combat); return; }
                CardCollection legal = new CardCollection();
                for (Card c : attacker.getCreaturesInPlay()) if (CombatUtil.canAttack(c, defender)) legal.add(c);
                if (legal.isEmpty()) return;
                // Exactly one legal attacker: forced-only-legal, same convention as every other
                // single-candidate site in this file (choose-entity/zone-change/targets all short-circuit
                // to tier=auto without ever calling the model when only one candidate exists). This site
                // used to fall through to decideMany even at n_options==1, logging tier=smart — the
                // forced-pick mislabel the defect_analyzer's forced_pick_mislabels check flagged in every
                // one of the 8 model-sweep games (always at this exact site, e.g. gpt-5 turn 4).
                if (legal.size() == 1) {
                    Card only = legal.getFirst();
                    combat.addAttacker(only, defender);
                    lastDeclaredAttackerNames.clear();
                    lastDeclaredAttackerNames.add(only.getName());
                    auto("attackers(forced-only-legal)->attack " + only.getName());
                    return;
                }
                // D4 shadow: genuine choice (n_options>1). Compute on pre-decision state, before the real
                // combat is mutated, into a scratch combat (real combat untouched). Stash in a LOCAL: the
                // decideMany() call below begins with clearLast() (nulling lastShadowChoice) BEFORE this
                // decision's row is emitted, so we re-attach the shadow label to lastShadowChoice immediately
                // before the evalDec("attackers") site — mirroring how the play path sets it right before its
                // own evalDec. (The no-model/bridge-down paths below emit no evalDec row, so they need nothing.)
                Integer shadowAtk = shadowAttackers(attacker, combat, legal.size());
                // No model available (floor arm / budget) -> deterministic ALL-IN, the classic goldfish
                // baseline. Never the engine AI.
                if (!smartOk()) {
                    for (Card c : legal) combat.addAttacker(c, defender);
                    auto("attackers(no-model)->all-in x" + legal.size());
                    return;
                }
                // Numbered subset pick, same PICKS router as discard: robust to duplicate creature names
                // and substring collisions, and decideMany prepends the full game snapshot — the model sees
                // opp life/board/untapped mana when deciding attacks, not just its own creature list.
                List<String> labels = new ArrayList<>();
                for (Card c : legal) labels.add(c.getName() + " (" + c.getNetPower() + "/" + c.getNetToughness() + ")");
                p("[ROUTER] attackers: " + legal.size() + " can attack -> asking " + smart.getModel() + "...");
                // Poison-aware lethal: a life-total-only hint actively misleads an infect/poison deck
                // (SF-1: deepseek's Infect matchup vs an opponent that never dropped below 20 life the
                // whole game — the real win condition was poison, invisible to the old hint). Compute
                // BOTH numbers and flag whether any legal attacker actually deals poison damage.
                int oppLife = defender.getLife();
                int oppPoison = 0;
                try { oppPoison = defender.getCounters(CounterEnumType.POISON); } catch (Exception ignore) { }
                boolean anyInfect = false;
                for (Card c : legal) { try { if (c.isInfectDamage(defender)) { anyInfect = true; break; } } catch (Exception ignore) { } }
                // Shared question template (single source; the ai-teacher seat rebuilds byte-identically).
                List<Integer> picks = decideMany(smart, null,
                        buildAttackersQuestion(defender.getName(), oppLife, oppPoison, anyInfect),
                        labels, 0, legal.size());
                if (picks == null) {
                    // Bridge dead -> intent-aware fallback (report C3), still fully deterministic, never
                    // the AI: prefer creatures the model's OWN stated turn plan already named (e.g. "attack
                    // with Blighted Agent alone") over a blind all-in that can silently undo an exalted/
                    // alpha-strike-avoidance line the model just committed to; failing that, prefer the
                    // attacker set it actually declared last time it got a real answer; only default to
                    // all-in if neither signal exists.
                    List<Integer> named = planNamedIndices(legal);
                    if (!named.isEmpty()) {
                        for (int i : named) combat.addAttacker(legal.get(i), defender);
                        auto("attackers(bridge-down)->plan-named x" + named.size());
                        return;
                    }
                    List<Card> prior = new ArrayList<>();
                    for (Card c : legal) if (lastDeclaredAttackerNames.contains(c.getName())) prior.add(c);
                    if (!prior.isEmpty()) {
                        for (Card c : prior) combat.addAttacker(c, defender);
                        auto("attackers(bridge-down)->prior-turn x" + prior.size());
                        return;
                    }
                    for (Card c : legal) combat.addAttacker(c, defender);
                    auto("attackers(bridge-down)->all-in x" + legal.size());
                    return;
                }
                StringBuilder chosen = new StringBuilder();
                lastDeclaredAttackerNames.clear();
                for (int i : picks) {
                    combat.addAttacker(legal.get(i), defender);
                    lastDeclaredAttackerNames.add(legal.get(i).getName());
                    if (chosen.length() > 0) chosen.append(", ");
                    chosen.append(legal.get(i).getName());
                }
                lastShadowChoice = shadowAtk; // re-attach: decideMany's clearLast() nulled it above
                evalDec("attackers", legal.size(), picks.size(), tierOf(true), 0, null, labels); // choice = # declared
                p("[ROUTER] attackers: " + (picks.isEmpty() ? "none" : chosen.toString()));
            } catch (Exception e) {
                noteAi("declareAttackers(err " + e + ")");
                super.declareAttackers(attacker, combat);
            }
        }

        @Override
        public void declareBlockers(Player defender, Combat combat) {
            clearLast();
            try {
                CardCollection attackers = combat.getAttackers();
                if (attackers == null || attackers.isEmpty()) return;
                CardCollection blockers = new CardCollection();
                for (Card c : defender.getCreaturesInPlay())
                    for (Card atk : attackers) if (CombatUtil.canBlock(atk, c, combat)) { blockers.add(c); break; }
                if (blockers.isEmpty()) return;
                // D4 shadow: capture pool size NOW (the assignment loop below mutates `blockers`), compute
                // the AI's block count into a scratch combat carrying the real attackers (real combat untouched).
                // Held in a LOCAL and attached only at the two genuine (model-consulted) evalDec sites — the
                // auto() returns below (random arm / no-model / bridge-down) are harness-made decisions the
                // student never faced, so labeling them would poison the divergence report with rows whose
                // "student choice" was never a choice at all.
                final Integer shadowBlk = shadowBlockers(defender, combat, blockers.size());
                if (rnd != null) { // random arm: each legal blocker blocks a random attacker or not, 50/50
                    int assignedR = 0;
                    for (Card b : blockers) {
                        if (rnd.nextBoolean()) continue;
                        Card a = attackers.get(rnd.nextInt(attackers.size()));
                        if (CombatUtil.canBlock(a, b, combat)) { combat.addBlocker(a, b); assignedR++; }
                    }
                    auto("blockers(random)->" + assignedR);
                    return;
                }
                // No model available -> deterministic NO BLOCKS (the fallback policy), never the engine AI.
                if (!smartOk()) { auto("blockers(no-model)->none"); return; }
                StringBuilder atk = new StringBuilder();
                for (Card a : attackers) { if (atk.length() > 0) atk.append(", "); atk.append(a.getName()).append(" (").append(a.getNetPower()).append('/').append(a.getNetToughness()).append(')'); }
                StringBuilder blk = new StringBuilder();
                List<String> blkLabels = new ArrayList<>(); // candidate pool captured BEFORE the assignment loop mutates `blockers`
                for (Card b : blockers) {
                    if (blk.length() > 0) blk.append(", ");
                    String bl = b.getName() + " (" + b.getNetPower() + "/" + b.getNetToughness() + ")";
                    blk.append(bl); blkLabels.add(bl);
                }
                // Shared template (single source; the ai-teacher seat rebuilds byte-identically via the same builder).
                String prompt = buildBlockersPrompt(snapshot(), atk.toString(), blk.toString());
                p("[ROUTER] blockers: " + attackers.size() + " atk / " + blockers.size() + " blk -> asking " + smart.getModel() + "...");
                String reply = ask(smart, prompt);
                if (reply == null) { auto("blockers(bridge-down)->none"); return; }
                String line = lastSegment(reply, "BLOCKS:");
                if (line == null || line.toUpperCase().contains("NONE")) {
                    // A genuine model-consulted decision (the reply came back fine) — must log exactly
                    // one jsonl row like every other ask path, even though it declines to block. This was
                    // the silent-drop gap (report iter-1 D2): the ask() succeeded and counted in the log's
                    // "-> asking" tally, but this branch returned with no evalDec call, so the jsonl side
                    // came up one row short whenever the model chose not to block at all.
                    lastShadowChoice = shadowBlk; // genuine model-consulted decision -> label it
                    evalDec("blockers", blkLabels.size(), 0, "smart", 0, null, blkLabels);
                    p("[ROUTER] blockers: NONE");
                    return;
                }
                int assigned = 0;
                for (String pair : line.split(",")) {
                    int arrow = pair.indexOf("->"); if (arrow < 0) continue;
                    Card b = matchByName(blockers, pair.substring(0, arrow).toUpperCase());
                    Card a = matchByName(attackers, pair.substring(arrow + 2).toUpperCase());
                    if (a != null && b != null && CombatUtil.canBlock(a, b, combat)) {
                        combat.addBlocker(a, b); assigned++; blockers.remove(b);
                    }
                }
                lastShadowChoice = shadowBlk; // genuine model-consulted decision -> label it
                evalDec("blockers", blockers.size() + assigned, assigned, "smart", 0, null, blkLabels); // choice = # blocks
                p("[ROUTER] blockers: assigned " + assigned);
            } catch (Exception e) {
                noteAi("declareBlockers(err " + e + ")");
                super.declareBlockers(defender, combat);
            }
        }

        @Override
        public List<SpellAbility> chooseSpellAbilityToPlay() {
            // IMPORTANT: the engine passes priority ONLY on a null return (see PhaseHandler.mainLoopStep:
            // `if (chosenSa == null) break;`). An EMPTY list means "play zero abilities, then ask again",
            // which spins the priority loop ~999x ("AI looped too much"). So PASS must be null, never empty.
            final List<SpellAbility> PASS = null;
            clearLast();
            try {
                Player me = getPlayer();
                PhaseHandler phh = me.getGame().getPhaseHandler();
                boolean ownMain = phh.isPlayerTurn(me) && (phh.is(PhaseType.MAIN1) || phh.is(PhaseType.MAIN2));

                // SPEED: on a reactive (not-our-main) priority window, only think when something can
                // actually matter — a spell/ability on the stack, combat (tricks/removal), or the
                // opponent's end step (the classic instant-speed action window for flash/instant decks).
                // Otherwise passing priority is virtually always correct, so pass WITHOUT an LLM call.
                boolean oppEot = phh.is(PhaseType.END_OF_TURN) && !phh.isPlayerTurn(me);
                if (SKIP_HEUR && rnd == null && !ownMain && me.getGame().getStack().isEmpty() && !phh.inCombat() && !oppEot) return PASS;

                // SPEED: the top of the stack is OUR OWN spell/ability — we just cast it. Don't burn a
                // model call asking "respond to yourself?"; let it resolve. The engine gives us priority
                // again with the updated state (and if the OPPONENT responds, their spell tops the stack
                // and we DO get asked).
                if (SKIP_HEUR && rnd == null && !me.getGame().getStack().isEmpty()) {
                    try {
                        SpellAbility top = me.getGame().getStack().peekAbility();
                        if (top != null && top.getActivatingPlayer() == me) return PASS;
                    } catch (Exception ignore) { }
                }

                // Enumerate castable spells + distinct playable lands (shared with the ai-teacher seat so
                // both build the byte-identical option menu — see enumeratePlayOptions).
                PlayOptions po = enumeratePlayOptions(me, ownMain);
                List<SpellAbility> options = po.spells;
                List<SpellAbility> landPlays = po.landPlays;
                List<Card> landCards = po.landCards;
                boolean haveLand = !landPlays.isEmpty();
                // Nothing to do -> pass (deterministic; never Forge's AI).
                if (options.isEmpty() && !haveLand) return PASS;
                if (!ownMain && options.isEmpty()) return PASS;

                // SPEED (interaction filter): on a mid-turn reactive window (not opp EOT, not combat),
                // only think if we hold something that can actually INTERACT with what's happening —
                // a targeting spell (removal/trick) or a counter. Rituals/eggs/mana spells cast in
                // response to an opponent's play achieve nothing; measured 66% of all play-asks were
                // PASS results, ~15 min/game of declining. Opp EOT and combat still always ask.
                // (rnd == null: the random eval arm bypasses speed filters so its baseline stays a true
                // uniform over the unpruned menu — filters exist to save model calls, which random has none of.)
                if (SKIP_HEUR && rnd == null && !ownMain && !oppEot && !phh.inCombat()) {
                    boolean anyInteractive = false;
                    for (SpellAbility sa : options) {
                        try { // walk the whole ability chain — Counter/targeting often lives on a sub
                            for (SpellAbility part = sa; part != null; part = part.getSubAbility()) {
                                if (part.usesTargeting() || part.getApi() == ApiType.Counter) { anyInteractive = true; break; }
                            }
                        } catch (Exception e) { anyInteractive = true; } // can't classify -> keep asking
                        if (anyInteractive) break;
                    }
                    if (!anyInteractive) return PASS;
                }

                // pick tier: own main phase = smart sequencing; reactive instant-speed window = fast.
                // Exception: a main with ZERO castable spells (just land-or-pass) is a fast-tier question —
                // measured 15-30s smart calls deciding "play the land". Which land still gets picked by a model.
                boolean landOnly = ownMain && options.isEmpty();
                LLMBridge b = (ownMain && !landOnly) ? smart : fast;
                boolean haveBudget = (b == smart) ? smartOk() : fastOk();

                int turn = phh.getTurn();
                if (turn != lastPlayTurn) { lastPlayTurn = turn; playDecisionsThisTurn = 0; passedReactSigs.clear(); }
                boolean overCap = playDecisionsThisTurn >= MAX_PLAY_DECISIONS_PER_TURN;
                if (!haveBudget || overCap) { // exhausted -> develop mana if possible, else pass. Not the AI.
                    if (haveLand) { auto("play(no-budget): land " + landCards.get(0).getName()); return Lists.newArrayList(landPlays.get(0)); }
                    auto("play(no-budget)->PASS");
                    return PASS;
                }

                // Option labels for the decision record, in the exact menu order shown to the model
                // (shared with the ai-teacher seat — see buildPlayLabels/numberedPlayMenu). The numbered
                // menu string and the land/pass boundary indices are derived from them so BOTH seats
                // build the byte-identical menu.
                List<String> playLabels = buildPlayLabels(options, landCards);
                String menuStr = numberedPlayMenu(playLabels);
                int landStart = 1 + options.size();                       // 1-based menu number of the first land
                int passIdx = 1 + options.size() + landPlays.size();      // 1-based menu number of PASS

                String sig = turn + "|" + phh.getPhase() + "|" + menuStr;
                if (sig.equals(lastAskSig) && lastWasPass) return PASS; // identical spot we already passed on
                lastAskSig = sig;

                // SPEED: reactive windows recur across phases with the SAME stack and SAME options
                // (e.g. "activate Wasteland?" at upkeep, draw, main, combat...). Once we decline a
                // situation this turn, every identical recurrence passes free. The phase/combat-step
                // MUST be part of the key (report B1): without it, a decline at COMBAT_BEGIN (empty
                // stack) dedupes against the later declare-blockers/pre-damage window (also an empty
                // stack, same menu) and silently swallows a re-ask the model never got to take —
                // SF-1 lost a turn-8 lethal Berserk exactly this way.
                String reactSig = null;
                if (SKIP_HEUR && rnd == null && !ownMain) {
                    reactSig = phh.getPhase() + "|" + safeStackTop(me.getGame()) + "|" + me.getGame().getStack().size() + "|" + menuStr;
                    if (passedReactSigs.contains(reactSig)) return PASS;
                }

                if (ownMain) p("[TALLY] smart=" + smartCalls + " fast=" + fastCalls + " auto=" + autoPicks + " ai=" + aiDelegations
                        + (smart.getCalls() > 0 ? " avgSmart=" + (smart.getTotalMs() / smart.getCalls()) + "ms" : "")
                        + (fast.getCalls() > 0 ? " avgFast=" + (fast.getTotalMs() / fast.getCalls()) + "ms" : ""));
                p("[ROUTER] play(" + (ownMain ? "main" : "react") + "): " + options.size() + " castable"
                        + (haveLand ? " + " + landPlays.size() + " land" : "") + " -> asking " + b.getModel() + "...");
                playDecisionsThisTurn++;

                int choice = -1;
                // First main-phase decision of OUR turn: also ask for a one-line turn plan (piggybacked —
                // costs zero extra calls). It rides in the snapshot for every later decision this turn.
                boolean askPlan = ownMain && !landOnly && planTurn != turn && b == smart; // fast tier is terse-only
                if (rnd != null) choice = 1 + rnd.nextInt(passIdx); // random arm: uniform incl. PASS
                for (int attempt = 0; rnd == null && attempt < 2 && choice < 1; attempt++) {
                    String prompt = attempt == 0
                            ? playPromptPrefix(snapshot(), phh.getPhase(), ownMain, menuStr)
                              + (askPlan
                                 ? "First state your plan for THIS TURN as `PLAN: <one short line>` (the sequence you intend, e.g. 'ritual into Reanimate on Griselbrand, hold Petal'), then end with EXACTLY: CHOICE: <number>"
                                 : PLAY_PROMPT_TAIL)
                            : "That reply had no parseable choice. Answer with ONE line only: CHOICE: <a number 1.." + passIdx + " from the list above>.";
                    String reply = ask(b, prompt);
                    if (reply != null && askPlan && attempt == 0) {
                        String pl = lastSegment(reply, "PLAN:");
                        if (pl != null) { // models sometimes co-mingle "PLAN: ... CHOICE: 3" — cut at the tag
                            int cut = pl.toUpperCase().indexOf("CHOICE:");
                            if (cut >= 0) pl = pl.substring(0, cut);
                            if (!pl.trim().isEmpty()) {
                                // Whitespace-collapsed only — NEVER truncated: this line is re-injected into
                                // every later prompt this turn (YOUR TURN PLAN), and a mid-word cut here is
                                // exactly the C4 defect that chopped a model's own stated finisher clause.
                                turnPlan = pl.replaceAll("\\s+", " ").trim();
                                planTurn = turn;
                                p("[ROUTER] plan: " + turnPlan);
                            }
                        }
                    }
                    if (reply == null) { // bridge dead -> develop or pass, not the AI
                        if (haveLand) { auto("play(bridge-down): land " + landCards.get(0).getName()); return Lists.newArrayList(landPlays.get(0)); }
                        auto("play(bridge-down)->PASS");
                        return PASS;
                    }
                    choice = parseTagNumber(reply, "CHOICE:");
                }
                // D4 shadow: the AI's play over the SAME menu (choice resolved, before the row is written
                // and before the chosen SA is targeted). shadowPlay's getAi() eval may set targets/X on the
                // shared candidate SA objects; the SHADOW-guarded chosen.resetTargets() in the cast branch
                // below erases any target it set, and the engine re-announces X at cast time.
                lastShadowChoice = shadowPlay(po);
                // ms=0 -> evalDec substitutes lastSuccessMs (successful-attempt-only time); see decideOne.
                evalDec("play", passIdx, (choice >= 1 && choice < passIdx) ? choice - 1 : -1,
                        tierOf(ownMain), 0, null, playLabels);
                if (choice == passIdx || choice < 1 || choice > passIdx) {
                    lastWasPass = true;
                    if (reactSig != null) passedReactSigs.add(reactSig); // don't re-ask this situation this turn
                    p("[ROUTER] play: PASS");
                    return PASS;
                }
                lastWasPass = false;
                if (haveLand && choice >= landStart && choice < passIdx) {
                    int li = choice - landStart;
                    p("[ROUTER] play: land " + landCards.get(li).getName());
                    return Lists.newArrayList(landPlays.get(li));
                }
                SpellAbility chosen = options.get(choice - 1);
                // D4 shadow guard (MANDATORY): shadowPlay's AI eval (canPlaySa/canPlayWithSubs) may have set
                // targets AND X on this SAME SA object *and its sub-abilities*; without a full-chain reset,
                // ensureTargetsSet would see a sub-ability "already targeted" (see :1449 below) and skip it —
                // casting a sub-targeted / multi-target spell against the AI's shadow target instead of the
                // LLM's. forge SpellAbility.resetTargets() clears only THIS SA (not recursive), so we walk the
                // whole getSubAbility() chain, and null xManaCostPaid so any shadow-set X is re-announced at
                // cast (X is normally re-announced anyway, but sVar/XAlternative X can retain a stale value).
                // No-op when SHADOW is off.
                if (SHADOW) for (SpellAbility s = chosen; s != null; s = s.getSubAbility()) { s.resetTargets(); s.setXManaCostPaid(null); }
                ensureTargetsSet(chosen); // AI cast path won't target for us — set legal LLM-chosen targets now
                p("[ROUTER] play: casts " + saLabel(chosen));
                return Lists.newArrayList(chosen);
            } catch (Exception e) {
                p("[ROUTER] play: error " + e + " -> PASS");
                return PASS;
            }
        }

        // ---------------- NEW: routed choice callbacks (generic router + AI fallback) ----------------

        @Override
        public <T extends GameEntity> T chooseSingleEntityForEffect(FCollectionView<T> optionList, DelayedReveal delayedReveal,
                SpellAbility sa, String title, boolean isOptional, Player relatedPlayer, Map<String, Object> params) {
            clearLast();
            List<T> opts = new ArrayList<>();
            for (T t : optionList) opts.add(t);
            if (opts.isEmpty())
                return isOptional ? null : super.chooseSingleEntityForEffect(optionList, delayedReveal, sa, title, isOptional, relatedPlayer, params);
            // delayedReveal carries a reveal side-effect — keep the engine's reveal semantics (the pick
            // itself still delegates to the engine AI below; routing the pick ITSELF through the model
            // is a mechanics-level change out of scope here). But the identities being revealed ARE
            // known to us right now (scry/Ponder/Sensei's Divining Top-style "look at top N" effects use
            // exactly this signature) — record them via the existing INTEL/remember() pattern so a later
            // decision this turn can at least see what the library-look/reveal turned up, instead of the
            // information vanishing the instant this call returns (the C2 "opaque reveal" gap).
            if (delayedReveal != null) {
                try {
                    List<String> seen = new ArrayList<>();
                    for (forge.game.card.CardView cv : delayedReveal.getCards()) seen.add(cv.getName());
                    if (!seen.isEmpty()) remember("REVEALED (" + zoneLabel(delayedReveal.getZone()) + "): " + String.join(", ", seen));
                } catch (Exception ignore) { }
                return aiDelegate("choose-entity(delayedReveal)" + srcOf(sa),
                        super.chooseSingleEntityForEffect(optionList, delayedReveal, sa, title, isOptional, relatedPlayer, params));
            }
            if (opts.size() == 1 && !isOptional) { auto("choose-entity(forced-only-legal)" + srcOf(sa)); return opts.get(0); }
            try {
                // Smart budget exhausted is NOT a reason to pick blind: fall to the fast tier so this
                // site stays model-routed (runs showed every "choose-entity(no-budget)" fired only after
                // budget exhaustion). The deterministic auto path remains for floor arms / both budgets gone.
                if (!smartOk() && !fastOk()) { auto("choose-entity(no-budget)->first" + srcOf(sa)); return isOptional ? null : opts.get(0); }
                LLMBridge b = smartOk() ? smart : fast;
                List<String> labels = new ArrayList<>();
                for (T t : opts) labels.add(entityLabel(t));
                String q = (title == null || title.isEmpty() ? "Choose one" : title) + srcOf(sa);
                int c = decideOne(b, "entity", q, labels, isOptional ? "Decline / none" : null);
                if (c == -2) { auto("choose-entity(bridge-down)->first" + srcOf(sa)); return isOptional ? null : opts.get(0); }
                if (c < 0) { // decideOne's -1 also covers "still unparseable after retry", which (unlike an
                             // explicit decline) decideOne does NOT log itself — never drop that case silently.
                    if (isOptional) { if (!lastDeclineLogged) auto("choose-entity(unparsed)->decline" + srcOf(sa)); return null; }
                    auto("choose-entity(unparsed)->first" + srcOf(sa)); return opts.get(0);
                }
                p("[ROUTER] choose-entity: " + labels.get(c) + srcOf(sa));
                return opts.get(c);
            } catch (Exception e) {
                auto("choose-entity(err)->first" + srcOf(sa)); return isOptional ? null : opts.get(0);
            }
        }

        /** Route the reanimation / tutor target: "which card leaves this zone" (Reanimate, Animate Dead,
         *  Entomb, Unmarked Grave, fetch effects...). THE key strategic pick for the reanimator deck. */
        @Override
        public Card chooseSingleCardForZoneChange(ZoneType destination, List<ZoneType> origin, SpellAbility sa,
                CardCollection fetchList, DelayedReveal delayedReveal, String selectPrompt, boolean isOptional, Player decider) {
            clearLast();
            if (fetchList == null || fetchList.isEmpty() || delayedReveal != null || decider != getPlayer())
                return super.chooseSingleCardForZoneChange(destination, origin, sa, fetchList, delayedReveal, selectPrompt, isOptional, decider);
            List<Card> opts = new ArrayList<>(fetchList);
            if (opts.size() == 1 && !isOptional) { auto("zone-change(forced-only-legal)" + srcOf(sa)); return opts.get(0); }
            try {
                if (!smartOk()) { auto("zone-change(no-budget)->first" + srcOf(sa)); return isOptional ? null : opts.get(0); }
                List<String> labels = new ArrayList<>();
                for (Card c : opts) labels.add(entityLabel(c));
                String q = (selectPrompt == null || selectPrompt.isEmpty() ? "Choose a card" : selectPrompt)
                        + " -> " + destination + srcOf(sa)
                        + " (pick the card that best advances your game plan — e.g. reanimate your biggest threat).";
                int c = decideOne(smart, "zone-change", q, labels, isOptional ? "Decline / none" : null);
                if (c == -2) { auto("zone-change(bridge-down)->first" + srcOf(sa)); return isOptional ? null : opts.get(0); }
                if (c < 0) { // decideOne's -1 also covers "still unparseable after retry", which (unlike an
                             // explicit decline) decideOne does NOT log itself — never drop that case silently.
                    if (isOptional) { if (!lastDeclineLogged) auto("zone-change(unparsed)->decline" + srcOf(sa)); return null; }
                    auto("zone-change(unparsed)->first" + srcOf(sa)); return opts.get(0);
                }
                p("[ROUTER] zone-change: " + labels.get(c) + " -> " + destination + srcOf(sa));
                return opts.get(c);
            } catch (Exception e) {
                auto("zone-change(err)->first" + srcOf(sa)); return isOptional ? null : opts.get(0);
            }
        }

        /** Route single-target spell/ability TARGETING (removal, Reanimate, Thoughtseize, Wasteland...).
         *  Since WE already chose to cast the spell and the engine reports a legal target, we always SET one
         *  (LLM picks when there's a real choice; auto-take the only candidate otherwise). We never hand it to
         *  Forge's AI targeting brain, which sometimes DECLINES a legal target and fizzles the cast
         *  ("Couldn't add to stack, failed to target"). Every candidate is pre-validated by sa.canTarget(). */
        @Override
        public boolean chooseTargetsFor(SpellAbility sa) {
            clearLast();
            try {
                if (sa == null || !sa.usesTargeting() || sa.getMaxTargets() != 1)
                    return super.chooseTargetsFor(sa);
                for (SpellAbility sub = sa.getSubAbility(); sub != null; sub = sub.getSubAbility())
                    if (sub.usesTargeting()) return super.chooseTargetsFor(sa);
                TargetRestrictions tr = sa.getTargetRestrictions();
                if (tr == null) return super.chooseTargetsFor(sa);
                List<GameEntity> cands = tr.getAllCandidates(sa, false);
                int min = sa.getMinTargets();
                if (cands == null || cands.isEmpty()) return super.chooseTargetsFor(sa); // truly no target -> engine cancels
                int pick = 0; // default: the (only, or first) legal candidate — beats an AI decline
                List<String> labels = new ArrayList<>();
                for (GameEntity ge : cands) labels.add(entityLabel(ge));
                if (cands.size() > 1 && smartOk()) {
                    String q = "Choose the TARGET for " + saLabel(sa) + " (the spell/ability resolves against whatever you pick).";
                    int c = decideOne(smart, "targets", q, labels, min == 0 ? "No target (decline)" : null);
                    // bridge dead -> AI targeting is an intentional, DOCUMENTED exception to "never Forge AI"
                    // for this one site (see class comment above); still log it so it's never silent.
                    if (c == -2) return aiDelegate("targets(bridge-down)->ai" + srcOf(sa), super.chooseTargetsFor(sa));
                    if (c < 0) {
                        // decideOne's -1 also covers "still unparseable after retry", which decideOne does NOT
                        // log itself (only an explicit decline does, flagged via lastDeclineLogged) — never drop it silently.
                        if (min == 0) {
                            if (!lastDeclineLogged) evalDec("targets", cands.size(), -1, tierOf(true), 0, "targets(unparsed)->decline", labels);
                            sa.resetTargets(); return true;
                        }
                        // required + unparsed -> intent-aware fallback (report C3): prefer a candidate the
                        // model's own stated turn PLAN already named over a blind first-candidate default;
                        // make the fallback VISIBLE to the gate either way.
                        List<Integer> named = planNamedIndices(cands);
                        if (!named.isEmpty()) {
                            pick = named.get(0);
                            evalDec("targets", cands.size(), pick, tierOf(true), 0, "targets(unparsed)->plan-named", labels);
                        } else {
                            evalDec("targets", cands.size(), 0, tierOf(true), 0, "targets(unparsed)->first", labels);
                        }
                    } else pick = c;                                          // decideOne logged the success line
                } else if (cands.size() > 1) {
                    // real choice existed but no model consulted (floor arm / budget exhausted)
                    evalDec("targets", cands.size(), pick, "auto", 0, "targets(no-model)->first", labels);
                } else {
                    // single legal candidate — genuinely forced, no model call was ever made for it; tag it
                    // as such (never tier=smart/null-fallback, which reads as an indistinguishable dropped call).
                    evalDec("targets", 1, 0, "auto", 0, "forced(only-legal)", labels);
                }
                sa.resetTargets();
                boolean added = sa.getTargets().add(cands.get(pick));
                if (!added || sa.getTargets().size() < Math.max(1, min)) return super.chooseTargetsFor(sa); // safety net
                p("[ROUTER] target: " + entityLabel(cands.get(pick)) + " <- " + saLabel(sa)
                        + (cands.size() == 1 ? " (only legal)" : ""));
                return true;
            } catch (Exception e) {
                return super.chooseTargetsFor(sa);
            }
        }

        /** Set legal, LLM-chosen targets on a spell BEFORE handing it to the AI cast path. That path
         *  (ComputerUtil.handlePlayingSpellAbility) does NOT run targeting for ordinary spells — so an
         *  untargeted spell we picked would fail hasLegalTargeting and fizzle ("Couldn't add to stack").
         *  We walk the ability chain and, for each targeting ability with no target yet, pick from the
         *  engine's own canTarget-validated candidates (LLM chooses when there's a real choice). */
        private void ensureTargetsSet(SpellAbility root) {
            for (SpellAbility sa = root; sa != null; sa = sa.getSubAbility()) {
                clearLast(); // per-ability: a single-candidate auto-target must not inherit a prior ability's prompt
                try {
                    if (!sa.usesTargeting()) continue;
                    if (sa.getTargets() != null && !sa.getTargets().isEmpty()) continue; // already targeted
                    if (sa.getActivatingPlayer() == null) sa.setActivatingPlayer(getPlayer());
                    TargetRestrictions tr = sa.getTargetRestrictions();
                    if (tr == null) continue;
                    List<GameEntity> cands = tr.getAllCandidates(sa, false);
                    if (cands == null || cands.isEmpty()) continue; // no legal target -> let engine cancel/handle
                    int min = Math.max(1, sa.getMinTargets());
                    int pick = 0; // first legal candidate beats leaving it untargeted (which fizzles)
                    String fb = null; // non-null when the pick is a deterministic fallback, not a model choice
                    if (cands.size() > 1 && smartOk()) {
                        List<String> labels = new ArrayList<>();
                        for (GameEntity ge : cands) labels.add(entityLabel(ge));
                        // type=null here -> decideOne never logs internally (incl. its decline branch), so
                        // EVERY exit from this call, including the "optional -> no target" one below, must be
                        // logged by us or it vanishes with zero jsonl/log trace (the exact C5 double-failure shape).
                        int c = decideOne(smart, null, "Choose the TARGET for " + saLabel(sa)
                                + " (the spell resolves against whatever you pick).", labels,
                                sa.getMinTargets() == 0 ? "No target" : null);
                        if (c == -1 && sa.getMinTargets() == 0) {
                            evalDec("target", cands.size(), -1, tierOf(true), 0, "target(optional-decline-or-unparsed)", labels);
                            continue; // optional -> deliberately no target
                        }
                        if (c >= 0) pick = c;
                        else { // -2/unparsed -> intent-aware fallback (report C3): prefer a plan-named
                               // candidate over a blind first-candidate default; VISIBLE either way.
                            List<Integer> named = planNamedIndices(cands);
                            if (!named.isEmpty()) { pick = named.get(0); fb = "target(unparsed-or-dead)->plan-named"; }
                            else fb = "target(unparsed-or-dead)->first";
                        }
                    } else if (cands.size() > 1) {
                        fb = "target(no-model)->first";                    // floor arm / budget exhausted
                    } else {
                        // single legal candidate — genuinely forced, no model call was ever attempted for it.
                        fb = "forced(only-legal)";
                    }
                    sa.resetTargets();
                    sa.getTargets().add(cands.get(pick));
                    for (int i = 0; sa.getTargets().size() < min && i < cands.size(); i++) // top up if it needs >1
                        if (i != pick) sa.getTargets().add(cands.get(i));
                    List<String> tgtOpts = new ArrayList<>();
                    for (GameEntity ge : cands) tgtOpts.add(entityLabel(ge));
                    evalDec("target", cands.size(), pick, fb == null ? tierOf(true) : "auto", 0, fb, tgtOpts);
                    p("[ROUTER] target: " + entityLabel(cands.get(pick)) + " <- " + saLabel(sa)
                            + (cands.size() == 1 ? " (only legal)" : ""));
                } catch (Exception e) { /* leave targeting to the engine for this ability */ }
            }
        }

        @Override
        public CardCollection chooseCardsToDiscardFrom(Player p, SpellAbility sa, CardCollection validCards,
                int min, int max, CardCollectionView visibleToChooser) {
            clearLast();
            if (validCards == null || validCards.isEmpty())
                return super.chooseCardsToDiscardFrom(p, sa, validCards, min, max, visibleToChooser);
            // p != getPlayer() means WE choose what SOMEONE ELSE discards (Thoughtseize / Cabal Therapy
            // "RevealYouChoose") — one of the most load-bearing picks in the matchup. Route it, don't delegate.
            boolean stripping = p != getPlayer();
            List<Card> opts = new ArrayList<>(validCards);
            if (opts.size() <= min) return firstN(opts, min, "discard(forced)"); // whole hand forced
            try {
                if (!smartOk()) return firstN(opts, min, "discard(no-budget)");
                List<String> labels = new ArrayList<>();
                for (Card c : opts) labels.add(candidateLabel(c));
                String q = stripping
                        ? "You are choosing which card(s) " + p.getName() + " must DISCARD from their revealed hand"
                          + srcOf(sa) + ". Strip whatever most threatens your game plan (their removal/disruption/payoff)."
                        : "You must DISCARD from your hand" + srcOf(sa) + ". Pick which card(s) to discard "
                          + "(remember your game plan — e.g. discarding a huge creature you can later reanimate is often correct).";
                List<Integer> picks = decideMany(smart, null, q, labels, min, max);
                if (picks == null) return firstN(opts, min, "discard(unparsed)");
                CardCollection out = new CardCollection();
                StringBuilder log = new StringBuilder();
                for (int i : picks) { out.add(opts.get(i)); if (log.length() > 0) log.append(", "); log.append(opts.get(i).getName()); }
                if (stripping) remember("saw " + p.getName() + " hand [" + names(opts, false, 10) + "], stripped " + log);
                evalDec("discard", opts.size(), picks.size(), tierOf(true), 0, null, labels);
                p("[ROUTER] discard" + (stripping ? "(strip " + p.getName() + ")" : "") + ": " + log);
                return out;
            } catch (Exception e) {
                return firstN(opts, min, "discard(err)");
            }
        }

        @Override
        public CardCollectionView chooseCardsForEffect(CardCollectionView sourceList, SpellAbility sa, String title,
                int min, int max, boolean isOptional, Map<String, Object> params) {
            clearLast();
            if (sourceList == null || sourceList.isEmpty())
                return super.chooseCardsForEffect(sourceList, sa, title, min, max, isOptional, params);
            List<Card> opts = new ArrayList<>(sourceList);
            if (opts.size() <= Math.max(1, min) && !isOptional) return firstN(opts, min, "cards-for-effect(forced)");
            try {
                if (!smartOk()) {
                    if (isOptional) { auto("cards-for-effect(no-budget)->decline"); return new CardCollection(); }
                    return firstN(opts, min, "cards-for-effect(no-budget)");
                }
                List<String> labels = new ArrayList<>();
                for (Card c : opts) labels.add(candidateLabel(c));
                String q = (title == null || title.isEmpty() ? "Choose cards" : title) + srcOf(sa);
                List<Integer> picks = decideMany(smart, "cards-for-effect", q, labels, isOptional ? 0 : min, max);
                // decideMany's null covers BOTH bridge-down AND still-unparseable-after-retry, and never logs
                // itself either way — the isOptional branch must not silently vanish (the C5 double-failure shape).
                if (picks == null) {
                    if (isOptional) { auto("cards-for-effect(unparsed-or-dead)->decline"); return new CardCollection(); }
                    return firstN(opts, min, "cards-for-effect(unparsed)");
                }
                CardCollection out = new CardCollection();
                for (int i : picks) out.add(opts.get(i));
                return out;
            } catch (Exception e) {
                if (isOptional) { auto("cards-for-effect(err)->decline"); return new CardCollection(); }
                return firstN(opts, min, "cards-for-effect(err)");
            }
        }

        /** ROUTED (audit follow-up): the last unrouted PlayerController choose-cards hook. The player
         *  must REVEAL between min and max cards from their hand — a public-information cost/effect
         *  (e.g. "reveal a green card", "reveal your hand"). Forge's own default
         *  (PlayerControllerAi.chooseCardsToRevealFromHand) is a bare {@code valid.subList(0,
         *  min(max,size))}: no strategy, zero jsonl/log trace — the same "opaque, untraced default"
         *  shape scry/surveil had. Now routed like every other choose-subset decision (discard/
         *  cards-for-effect), with min/max clamped to what's legally available. Bridge-DOWN is the one
         *  documented Forge-AI exception (identical convention to chooseTargetsFor): delegate to super
         *  WITH the [FALLBACK->AI] marker so the reconciliation gate counts it instead of the decision
         *  silently vanishing. The no-model (floor/budget) and forced paths stay deterministic (auto),
         *  so the ai_tier hard invariant holds for every non-bridge-down run. */
        @Override
        public CardCollectionView chooseCardsToRevealFromHand(int min, int max, CardCollectionView valid) {
            clearLast();
            if (valid == null || valid.isEmpty())
                return super.chooseCardsToRevealFromHand(min, max, valid);
            List<Card> opts = new ArrayList<>(valid);
            // Legality clamps: never reveal more than are valid, nor fewer than min (nor a negative min).
            int effMax = Math.min(max, opts.size());
            int effMin = Math.max(0, Math.min(min, effMax));
            if (effMax <= 0) return CardCollection.EMPTY;                    // reveal-zero: no real decision, no row
            if (opts.size() <= effMin) return firstN(opts, effMin, "reveal(forced)"); // whole valid set forced
            try {
                if (!smartOk()) return firstN(opts, effMin, "reveal(no-budget)");
                List<String> labels = new ArrayList<>();
                for (Card c : opts) labels.add(candidateLabel(c));
                String q = "You must REVEAL between " + effMin + " and " + effMax + " card(s) from your hand "
                        + "(they become known to your opponent). Reveal only what the effect requires, and among "
                        + "legal choices prefer what least exposes your game plan.";
                List<Integer> picks = decideMany(smart, "reveal", q, labels, effMin, effMax);
                // decideMany returns null on bridge-down OR still-unparseable-after-retry — either way the
                // model gave us nothing usable. Documented Forge-AI exception (cf. chooseTargetsFor): hand to
                // super WITH the [FALLBACK->AI] marker (a tier=ai jsonl row) so the analyzer counts it.
                if (picks == null)
                    return aiDelegate("reveal(bridge-down)->ai", super.chooseCardsToRevealFromHand(min, max, valid));
                CardCollection out = new CardCollection();
                for (int i : picks) out.add(opts.get(i));
                p("[ROUTER] reveal: " + (out.isEmpty() ? "(none)" : names(out, false, 10)));
                return out;
            } catch (Exception e) {
                return aiDelegate("reveal(err)->ai", super.chooseCardsToRevealFromHand(min, max, valid));
            }
        }

        /** Scry — ROUTED (report C2). Was previously unhandled by this class (fell through to
         *  PlayerControllerAi's default, running entirely on Forge's built-in AI with zero jsonl/log
         *  trace and the revealed cards never shown as a real decision) — the exact "library-manipulation
         *  opaque" gap the report calls out (SF-2's bare Preordain scry with no preceding [ROUTER] line
         *  at all). See {@link #arrangeTopN} for the shared implementation. */
        @Override
        public ImmutablePair<CardCollection, CardCollection> arrangeForScry(CardCollection topN) {
            return arrangeTopN(topN, "scry", "BOTTOM of your library");
        }

        /** Surveil — ROUTED (report C2), same shape as scry (see {@link #arrangeTopN}) but the
         *  "send away" pile is the graveyard instead of the library bottom. */
        @Override
        public ImmutablePair<CardCollection, CardCollection> arrangeForSurveil(CardCollection topN) {
            return arrangeTopN(topN, "surveil", "GRAVEYARD");
        }

        /** Shared scry/surveil router: partition a top-of-library peek into "keep on top" (ordered —
         *  index 0 becomes the new top card; see GameAction.scry/surveil, which reverses this list
         *  before pushing each card back) vs. a discard pile (library bottom for scry, graveyard for
         *  surveil). Forge's own default logic for both (PlayerControllerAi.arrangeFor{Scry,Surveil})
         *  is identical modulo the destination, so one router serves both. Deterministic legal fallback
         *  on no-budget/unparsed/bridge-down/error: keep everything on top in the order the engine
         *  handed it to us — never Forge's AI judgment call. */
        private ImmutablePair<CardCollection, CardCollection> arrangeTopN(CardCollection topN, String verb, String destName) {
            clearLast();
            if (topN == null || topN.isEmpty()) return ImmutablePair.of(new CardCollection(), new CardCollection());
            List<Card> opts = new ArrayList<>(topN);
            try {
                // Exactly one card peeked: forced-only-legal, same convention as every other
                // single-candidate site (choose-entity/zone-change/targets/attackers) — never call the
                // model at n_options==1. This was the surveil forced-pick mislabel the defect_analyzer
                // flagged (tier=smart at n_options==1) in every model-sweep game that surveiled a
                // single card; scry shares this method so it's covered too.
                if (opts.size() == 1) {
                    auto(verb + "(forced-only-legal)->keep-all-top");
                    return ImmutablePair.of(new CardCollection(opts), new CardCollection());
                }
                if (!smartOk()) { auto(verb + "(no-model)->keep-all-top"); return ImmutablePair.of(new CardCollection(opts), new CardCollection()); }
                List<String> labels = new ArrayList<>();
                for (Card c : opts) labels.add(candidateLabel(c));
                String q = "You looked at the top " + opts.size() + " card(s) of your library (" + verb + "). For each, "
                        + "decide KEEP ON TOP or send it to the " + destName + ". List the ones to KEEP ON TOP, in order "
                        + "(first = new top card) — everything you don't list goes to the " + destName + ".";
                List<Integer> picks = decideMany(smart, verb, q, labels, 0, opts.size());
                if (picks == null) { // unparsed after retry, or bridge dead -> deterministic keep-all-top, never Forge AI
                    auto(verb + "(unparsed)->keep-all-top");
                    return ImmutablePair.of(new CardCollection(opts), new CardCollection());
                }
                CardCollection toTop = new CardCollection();
                java.util.Set<Card> kept = new java.util.HashSet<>();
                for (int i : picks) { Card c = opts.get(i); toTop.add(c); kept.add(c); }
                CardCollection toOther = new CardCollection();
                for (Card c : opts) if (!kept.contains(c)) toOther.add(c);
                StringBuilder log = new StringBuilder();
                for (Card c : toTop) { if (log.length() > 0) log.append(", "); log.append(c.getName()); }
                remember(verb + " revealed [" + names(opts, false, 10) + "], kept on top: " + (toTop.isEmpty() ? "(none)" : log.toString()));
                p("[ROUTER] " + verb + ": keep-on-top " + (toTop.isEmpty() ? "(none)" : log) + "; -> " + destName + " x" + toOther.size());
                return ImmutablePair.of(toTop, toOther);
            } catch (Exception e) {
                auto(verb + "(err)->keep-all-top");
                return ImmutablePair.of(new CardCollection(opts), new CardCollection());
            }
        }

        /** Generic "choose the ORDER" hook — Ponder, Brainstorm/Dig put-backs, Sensei's Divining Top,
         *  and any other effect that hands the engine a fixed set of cards and asks for a permutation
         *  before they move to `destinationZone` — ROUTED (report C2). This ran unconditionally on
         *  Forge's built-in AI before, so a card the model just SAW via a scry/Ponder-style reveal could
         *  be silently reordered by the engine's own judgment with zero model input and zero jsonl/log
         *  trace. Reuses decideMany's PICKS plumbing with min=max=size: ALL n cards must be named, in
         *  the order they should move (first pick = whatever "first" means for that destination, e.g.
         *  new top-of-library). Deterministic fallback keeps the engine-given order, never Forge AI. */
        @Override
        public CardCollectionView orderMoveToZoneList(CardCollectionView cards, ZoneType destinationZone, SpellAbility source) {
            clearLast();
            if (cards == null || cards.size() <= 1) return cards; // nothing to order, no real choice
            try {
                if (!smartOk()) { auto("order(no-model)->as-is"); return cards; }
                List<Card> opts = new ArrayList<>(cards);
                List<String> labels = new ArrayList<>();
                for (Card c : opts) labels.add(candidateLabel(c));
                String q = "Put these " + opts.size() + " card(s)" + srcOf(source) + " into " + destinationZone
                        + " in your preferred order. List ALL " + opts.size() + " of them (first = goes in/on top first).";
                List<Integer> picks = decideMany(smart, "order", q, labels, opts.size(), opts.size());
                if (picks == null || picks.size() != opts.size()) { auto("order(unparsed)->as-is"); return cards; }
                CardCollection out = new CardCollection();
                StringBuilder log = new StringBuilder();
                for (int i : picks) { out.add(opts.get(i)); if (log.length() > 0) log.append(", "); log.append(opts.get(i).getName()); }
                p("[ROUTER] order" + srcOf(source) + " -> " + destinationZone + ": " + log);
                return out;
            } catch (Exception e) {
                auto("order(err)->as-is");
                return cards;
            }
        }

        @Override
        public boolean confirmAction(SpellAbility sa, PlayerActionConfirmMode mode, String message,
                List<String> options, Card cardToShow, Map<String, Object> params) {
            clearLast();
            if (!fastOk()) { auto("confirm(no-model)->NO"); return false; } // deterministic decline, not the AI
            try {
                String q = "Yes/No decision" + srcOf(sa) + ": " + (message == null ? String.valueOf(mode) : message);
                List<String> yn = Lists.newArrayList("YES (do it)", "NO (decline)");
                int c = decideOne(fast, "confirm", q, yn, null);
                // UNPARSED and BRIDGE-DOWN both take the same deterministic decline default — a dead
                // bridge must never fall through to Forge's AI here (this was the hard-invariant
                // violation: 6 tier=ai rows in the gpt-5 model-sweep, every single one confirm(bridge-down)
                // — the fast-tier bridge going dark mid-game and this site being the one place left that
                // still asked super for a real decision instead of taking the same safe default the
                // unparsed branch already used). Make the fallback VISIBLE to the gate either way.
                if (c == -1) evalDec("confirm", 2, 1, tierOf(false), 0, "confirm(unparsed)->NO", yn);
                else if (c == -2) evalDec("confirm", 2, 1, "auto", 0, "confirm(bridge-down)->NO", yn);
                boolean yes = (c == 0);
                p("[ROUTER] confirm(" + oneLine(message == null ? String.valueOf(mode) : message) + "): " + (yes ? "YES" : "NO"));
                return yes;
            } catch (Exception e) {
                return aiDelegate("confirm(err)", super.confirmAction(sa, mode, message, options, cardToShow, params));
            }
        }

        @Override
        public int chooseNumber(SpellAbility sa, String title, int min, int max) {
            clearLast(); // BEFORE any early return: auto() below attaches whatever prompt/reply is currently
                         // captured, and a stale one from a prior decision must never leak into this row.
            if (max <= min) { auto("number(forced-only-legal)->" + min); return min; } // forced, deterministic
            if (!fastOk()) { auto("number(no-model)->" + min); return min; } // deterministic legal minimum
            try {
                List<String> opts = new ArrayList<>();
                if (max - min <= 12) { for (int v = min; v <= max; v++) opts.add(String.valueOf(v)); }
                else { opts.add(String.valueOf(min)); opts.add(String.valueOf((min + max) / 2)); opts.add(String.valueOf(max)); }
                String q = (title == null ? "Choose a number" : title) + " (" + min + ".." + max + ")" + srcOf(sa);
                int c = decideOne(fast, "number", q, opts, null);
                // Bridge-down AND still-unparseable-after-retry both take the deterministic legal
                // minimum — same default as the no-budget branch above, never Forge AI.
                if (c == -2) { auto("number(bridge-down)->" + min); return min; }
                if (c < 0) { auto("number(unparsed)->" + min); return min; }
                int val = Integer.parseInt(opts.get(c));
                p("[ROUTER] number(" + oneLine(title) + "): " + val);
                return val;
            } catch (Exception e) {
                return aiDelegate("number(err)", super.chooseNumber(sa, title, min, max));
            }
        }

        /** Route "name a card" from an explicit list (Alhammarret, ChooseFromList effects).
         *  Small list -> numbered menu; big list -> free-form name validated against it. */
        @Override
        public String chooseCardName(SpellAbility sa, List<ICardFace> faces, String message) {
            clearLast();
            try {
                if (faces == null || faces.isEmpty())
                    return aiDelegate("card-name(list:empty)", super.chooseCardName(sa, faces, message));
                if (rnd != null) { // random arm: uniform over the list — never the model, never the AI
                    String pick = faces.get(rnd.nextInt(faces.size())).getName();
                    auto("card-name(random)->" + pick);
                    return pick;
                }
                if (!smartOk()) { // floor arm / budget: first face, deterministic — not the AI brain
                    auto("card-name(no-model)->" + faces.get(0).getName());
                    return faces.get(0).getName();
                }
                if (faces.size() <= 20) {
                    List<String> labels = new ArrayList<>();
                    for (ICardFace f : faces) labels.add(f.getName());
                    int c = decideOne(smart, "card-name", "NAME A CARD" + srcOf(sa) + ": " + oneLine(message), labels, null);
                    if (c >= 0) { p("[ROUTER] card-name: " + labels.get(c) + srcOf(sa)); return labels.get(c); }
                    // Bridge-down (c==-2) or still-unparseable-after-retry (c==-1): same deterministic
                    // first-face default the no-model branch above uses, never Forge AI.
                    auto("card-name(list:unparsed)->" + faces.get(0).getName());
                    return faces.get(0).getName();
                }
                String want = askCardName(sa, message, null);
                if (want != null) {
                    for (ICardFace f : faces)
                        if (f.getName().equalsIgnoreCase(want)) {
                            evalDec("card-name", faces.size(), -1, tierOf(true), 0, null,
                                    java.util.Arrays.asList("<free-form: any legal card name>"));
                            p("[ROUTER] card-name: " + f.getName() + srcOf(sa)); return f.getName();
                        }
                }
                // want==null (bridge-down/unparsed) or no face matched it: same deterministic first-face
                // default, never Forge AI.
                auto("card-name(list:no-match)->" + faces.get(0).getName());
                return faces.get(0).getName();
            } catch (Exception e) {
                return aiDelegate("card-name(list:err)", super.chooseCardName(sa, faces, message));
            }
        }

        /** Route "name a card" against a predicate — THE Cabal Therapy path ("name a nonland card").
         *  Free-form LLM answer, validated by the engine's own face lookup + predicate. The persistent
         *  conversation means the model remembers what it saw with an earlier Thoughtseize/Therapy. */
        @Override
        public String chooseCardName(SpellAbility sa, java.util.function.Predicate<ICardFace> cpp, String valid, String message) {
            clearLast();
            try {
                // random/floor arms: pick from the predicate-filtered card universe the same way the
                // engine's own randomChoice path does — never the model, never the AI brain.
                if (rnd != null || !smartOk()) {
                    try {
                        List<ICardFace> pool = new ArrayList<>();
                        StaticData.instance().getCommonCards().streamAllFaces()
                                .filter(cpp == null ? f -> true : cpp).limit(500).forEach(pool::add);
                        if (!pool.isEmpty()) {
                            String pick = (rnd != null ? pool.get(rnd.nextInt(pool.size())) : pool.get(0)).getName();
                            auto("card-name(" + (rnd != null ? "random" : "no-model") + ")->" + pick);
                            return pick;
                        }
                    } catch (Exception ignore) { }
                    return aiDelegate("card-name(no-pool)", super.chooseCardName(sa, cpp, valid, message));
                }
                String want = askCardName(sa, message, valid);
                if (want != null) {
                    ICardFace face = StaticData.instance().getCommonCards().getFaceByName(want);
                    if (face != null && (cpp == null || cpp.test(face))) {
                        remember("named " + face.getName() + srcOf(sa));
                        evalDec("card-name", -1, -1, tierOf(true), 0, null,
                                java.util.Arrays.asList("<free-form: any legal card name>"));
                        p("[ROUTER] card-name: " + face.getName() + srcOf(sa));
                        return face.getName();
                    }
                }
                // Bridge-down (want==null), unparseable, or the named card didn't match the predicate:
                // same deterministic pool-first pick as the no-model branch above, never Forge AI. Only
                // a genuinely empty pool (no card in the whole database matches the predicate — should
                // not happen for any real "name a nonland card"-style effect) falls through to
                // card-name(no-pool)'s AI delegation below.
                try {
                    List<ICardFace> pool = new ArrayList<>();
                    StaticData.instance().getCommonCards().streamAllFaces()
                            .filter(cpp == null ? f -> true : cpp).limit(500).forEach(pool::add);
                    if (!pool.isEmpty()) {
                        String pick = pool.get(0).getName();
                        auto("card-name(no-match)->" + pick);
                        return pick;
                    }
                } catch (Exception ignore) { }
                return aiDelegate("card-name(no-pool)", super.chooseCardName(sa, cpp, valid, message));
            } catch (Exception e) {
                return aiDelegate("card-name(err)", super.chooseCardName(sa, cpp, valid, message));
            }
        }

        /** Shared free-form "CARD: <name>" ask (one retry). Returns the raw name or null. */
        private String askCardName(SpellAbility sa, String message, String valid) {
            String q = snapshot() + "\nDECISION: NAME A CARD" + srcOf(sa) + ": " + oneLine(message)
                    + (valid != null && !valid.isEmpty() ? " (must match: " + oneLine(valid) + ")" : "")
                    + ". Use everything you've seen this game — revealed hands, their deck's archetype — to name "
                    + "the card most likely there / most dangerous to your plan. "
                    + "End with EXACTLY: CARD: <exact English card name>";
            for (int attempt = 0; attempt < 2; attempt++) {
                String reply = ask(smart, attempt == 0 ? q
                        : "That was not a legal or recognizable card name here. Answer with ONE line only: CARD: <exact card name>");
                if (reply == null) return null;
                String seg = lastSegment(reply, "CARD:");
                if (seg != null) {
                    String want = seg.replace("\"", "").replace("`", "").trim();
                    if (!want.isEmpty()) return want;
                }
            }
            return null;
        }

        /** Route modal spells ("choose one —") through the numbered menu. */
        @Override
        public List<AbilitySub> chooseModeForAbility(SpellAbility sa, List<AbilitySub> possible, int min, int num, boolean allowRepeat) {
            clearLast();
            try {
                // allowRepeat (choose the same mode more than once) is a mechanic this router does not
                // model — an intentional, documented scope-out, same shape as chooseSingleEntityForEffect's
                // delayedReveal exception above. possible==null/empty is a degenerate engine state (a
                // modal ability with nothing to choose from) with no legal candidate to default to either.
                if (possible == null || possible.isEmpty() || allowRepeat)
                    return aiDelegate("modes(repeat/empty)", super.chooseModeForAbility(sa, possible, min, num, allowRepeat));
                if (possible.size() == 1 && min >= 1) { auto("modes(forced-only-legal)"); return Lists.newArrayList(possible.get(0)); } // forced
                // No budget: deterministic first-`min` modes, never Forge AI.
                if (!smartOk()) {
                    List<AbilitySub> defPick = new ArrayList<>();
                    for (int i = 0; i < Math.min(Math.max(min, 0), possible.size()); i++) defPick.add(possible.get(i));
                    auto("modes(no-budget)->first" + defPick.size());
                    return defPick;
                }
                List<String> labels = new ArrayList<>();
                for (AbilitySub ab : possible) labels.add(oneLine(ab.getDescription()));
                List<Integer> picks = decideMany(smart, "modes", "MODAL SPELL — choose mode(s) for " + saLabel(sa) + ".",
                        labels, Math.max(0, min), Math.min(num, possible.size()));
                // decideMany's null covers BOTH bridge-down and still-unparseable-after-retry — same
                // deterministic first-`min` default as the no-budget branch above, never Forge AI.
                if (picks == null || picks.size() < min) {
                    List<AbilitySub> defPick = new ArrayList<>();
                    for (int i = 0; i < Math.min(Math.max(min, 0), possible.size()); i++) defPick.add(possible.get(i));
                    auto("modes(unparsed)->first" + defPick.size());
                    return defPick;
                }
                List<AbilitySub> out = new ArrayList<>();
                StringBuilder log = new StringBuilder();
                for (int i : picks) { out.add(possible.get(i)); if (log.length() > 0) log.append(" + "); log.append(labels.get(i)); }
                p("[ROUTER] modes: " + log);
                return out;
            } catch (Exception e) {
                return aiDelegate("modes(err)", super.chooseModeForAbility(sa, possible, min, num, allowRepeat));
            }
        }

        // ---------------- Goal A: log-only observation of decisions Forge's AI makes ----------------
        // These surfaces previously emitted NO eval line and ran invisibly on the engine AI (super).
        // Each override below calls super, OBSERVES the result, emits an evalDec line with tier "ai",
        // and returns super's result object UNCHANGED — behavior is bit-identical; only the JSONL gains
        // lines. They deliberately do NOT call noteAi()/bump aiDelegations: these are observations of
        // pre-existing AI behavior, not new fallbacks, so "[TALLY] ai=0" stays a meaningful invariant.
        // REMAINING INVISIBLE SURFACE (documented, not logged): mana tapping/payment. The per-source
        // which-land-to-tap choices happen entirely inside ComputerUtilMana (forge-ai) and never cross
        // the PlayerController boundary — the controller only sees payManaCost(cost)->boolean — so they
        // cannot be surfaced without modifying the pinned engine.

        /** Emit a log-only "AI observed" decision line. Never throws, never alters game flow. */
        private void observeAiDec(String type, int nOptions, int choice, long t0, String desc, List<String> labels) {
            try {
                clearLast(); // no model was consulted for this decision — prompt/raw_reply must log null
                evalDec(type, nOptions, choice, "ai", System.currentTimeMillis() - t0, desc, labels);
            } catch (Exception ignore) { }
        }
        /** Null-safe label snapshot (take it BEFORE calling super when super may mutate/reorder). */
        private static List<String> cardLabels(Iterable<Card> cards) {
            List<String> out = new ArrayList<>();
            try { if (cards != null) for (Card c : cards) out.add(c == null ? "(null)" : entityLabel(c)); }
            catch (Exception ignore) { }
            return out;
        }
        private static String safeName(Card c) {
            try { return c == null ? "?" : c.getName(); } catch (Exception e) { return "?"; }
        }
        /** "A > B > C" summary of an ordered card sequence (null-safe). */
        private static String orderSummary(Iterable<Card> cards) {
            try {
                StringBuilder b = new StringBuilder();
                if (cards != null) for (Card c : cards) { if (b.length() > 0) b.append(" > "); b.append(safeName(c)); }
                return b.length() == 0 ? "(none)" : b.toString();
            } catch (Exception e) { return "?"; }
        }

        /** Mulligan bottoming (London/Houston tuck) — MODEL-ROUTED. This used to run invisibly on
         *  Forge AI after EVERY model mulligan, which could silently bottom the exact cards the
         *  model's keep-decision was based on (observed: AI bottoming Orcish Bowmasters to keep
         *  filler). Now the same brain that chose the mulligan chooses what goes under.
         *  choice = count of bottomed cards (COUNT-style, like discard/attackers). Failure branches
         *  bottom the FIRST cardsToReturn cards — deterministic legal, never super (= Forge AI).
         *  cardsToReturn == 0 (the kept 7 / free first draw) is a no-op — skip the line as noise. */
        @Override
        public CardCollectionView tuckCardsViaMulligan(CardCollectionView hand, int cardsToReturn) {
            if (cardsToReturn <= 0 || hand == null) return super.tuckCardsViaMulligan(hand, cardsToReturn);
            clearLast();
            List<Card> opts = new ArrayList<>(hand);
            if (opts.size() <= cardsToReturn) return firstN(opts, cardsToReturn, "mulligan-bottom(forced-all)");
            try {
                if (!smartOk()) return firstN(opts, cardsToReturn, "mulligan-bottom(no-model)");
                List<String> labels = cardLabels(hand);
                String q = "You mulliganed and must put EXACTLY " + cardsToReturn + " card(s) from this hand on the "
                        + "BOTTOM of your library. Pick what to bottom so the kept cards match why you kept this hand "
                        + "(mana balance + your game plan; excess/duplicate lands and the least castable spells usually go under).";
                List<Integer> picks = decideMany(smart, null, q, labels, cardsToReturn, cardsToReturn);
                if (picks == null || picks.size() != cardsToReturn)
                    return firstN(opts, cardsToReturn, "mulligan-bottom(unparsed)");
                CardCollection out = new CardCollection();
                StringBuilder log = new StringBuilder();
                for (int i : picks) { out.add(opts.get(i)); if (log.length() > 0) log.append(", "); log.append(opts.get(i).getName()); }
                evalDec("mulligan-bottom", labels.size(), picks.size(), tierOf(true), 0, null, labels);
                p("[ROUTER] mulligan-bottom: " + log);
                return out;
            } catch (Exception e) {
                return firstN(opts, cardsToReturn, "mulligan-bottom(err)");
            }
        }

        /** Damage-assignment order among multiple blockers of one attacker. choice = 0 by convention
         *  (no menu index exists — the AI's chosen ORDER is summarized in the fallback field). Input
         *  labels snapshotted pre-super: AiBlockController may reorder into a new list. */
        @Override
        public CardCollection orderBlockers(Card attacker, CardCollection blockers) {
            List<String> labels = cardLabels(blockers);
            long t0 = System.currentTimeMillis();
            CardCollection r = super.orderBlockers(attacker, blockers);
            observeAiDec("damage-order", labels.size(), 0, t0,
                    "damage-order(ai) blockers of " + safeName(attacker) + " -> " + orderSummary(r), labels);
            return r;
        }

        /** A blocker added mid-combat gets inserted into an existing damage order. */
        @Override
        public CardCollection orderBlocker(Card attacker, Card blocker, CardCollection oldBlockers) {
            List<String> labels = cardLabels(oldBlockers);
            try { labels.add(0, "(new blocker) " + safeName(blocker)); } catch (Exception ignore) { }
            long t0 = System.currentTimeMillis();
            CardCollection r = super.orderBlocker(attacker, blocker, oldBlockers);
            observeAiDec("damage-order", labels.size(), 0, t0,
                    "damage-order(ai) insert " + safeName(blocker) + " vs " + safeName(attacker) + " -> " + orderSummary(r), labels);
            return r;
        }

        /** Damage-assignment order when one blocker blocks multiple attackers. */
        @Override
        public CardCollection orderAttackers(Card blocker, CardCollection attackers) {
            List<String> labels = cardLabels(attackers);
            long t0 = System.currentTimeMillis();
            CardCollection r = super.orderAttackers(blocker, attackers);
            observeAiDec("damage-order", labels.size(), 0, t0,
                    "damage-order(ai) attackers on " + safeName(blocker) + " -> " + orderSummary(r), labels);
            return r;
        }

        // ---------------- report C1: the two hardest "Goal A" surfaces are now ROUTED, not observed --
        // assignCombatDamage and choosePermanentsToSacrifice below used to live entirely in the
        // log-only-observation bucket above (bit-identical to super, tier="ai", no [ROUTER] line, no
        // model consultation ever). They no longer do: the common/forced shapes are deterministic
        // tier="auto" and the genuine choices go through decideOne/decideMany like every other routed
        // decision in this file, so they DO contribute to smart/fast/auto — only assignCombatDamage's
        // documented multi-blocker/banding exotic-split fallback still calls observeAiDec.

        /** Combat damage distribution (per creature per damage step). Report C1: this used to hand
         *  EVERY case — including the single-blocker, no-trample case that has exactly one legal
         *  distribution — to Forge's built-in combat AI. Now routed: a single blocker with no trample is
         *  a forced, deterministic `tier=auto` (all damage to the one blocker, no choice exists); a
         *  single blocker WITH trample is a genuine strategic choice (max-trample-through vs. dump
         *  everything on the blocker for overkill/value) and gets a compact two-option decideOne menu.
         *  `getEnoughDamageToKill` is Forge's pure lethal-damage-math helper (deathtouch/indestructible/
         *  prevention aware) — reused the same way `ComputerUtilMana.canPayManaCost` already is elsewhere
         *  in this file, NOT an AI strategy call.
         *  MULTI-BLOCKER (double/triple block etc.): report C1 follow-up — this used to be the
         *  "[AI-EXOTIC-DAMAGE]" escape hatch that handed the WHOLE decision to Forge's AI via
         *  `super.assignCombatDamage`, including routine cases (e.g. two blockers, 1 damage — there is
         *  no genuinely "exotic" math there, just an ordering choice). That branch is gone: NO
         *  combat-damage path may reach Forge AI. Instead we ask the model to PRIORITIZE the blockers
         *  (one bounded decideMany call, full permutation, reusing the same PICKS plumbing as
         *  orderMoveToZoneList above), then assign damage deterministically in that order — lethal
         *  amount (via getEnoughDamageToKill) to each blocker before spilling over to the next, with any
         *  leftover tramples through if the attacker has trample and a defender exists, otherwise piles
         *  onto the last blocker in the chosen order. No-model / unparsed-after-retry falls back to the
         *  engine's own blocker order (tier=auto, "damage-assign(auto-in-order)") — still never Forge AI. */
        @Override
        public Map<Card, Integer> assignCombatDamage(Card attacker, CardCollectionView blockers, CardCollectionView remaining,
                int damageDealt, GameEntity defender, boolean overrideOrder) {
            clearLast();
            try {
                if (blockers != null && blockers.size() == 1) {
                    Card single = blockers.getFirst();
                    boolean trample = defender != null && attacker.hasKeyword(Keyword.TRAMPLE);
                    int[] split = trample
                            ? trampleSplit(damageDealt, ComputerUtilCombat.getEnoughDamageToKill(single, damageDealt, attacker, true))
                            : new int[]{damageDealt, 0};
                    int lethal = split[0], trampleOver = split[1];
                    Map<Card, Integer> r = new java.util.HashMap<>();
                    if (trampleOver <= 0) { // no trample, or not enough damage to kill+overflow -> one legal split
                        r.put(single, damageDealt);
                        evalDec("damage-assign", 1, 0, "auto", 0,
                                "forced(only-legal) all " + damageDealt + " -> " + safeName(single),
                                java.util.Arrays.asList(candidateLabel(single) + ": all " + damageDealt + " dmg"));
                        p("[ROUTER] damage-assign: all " + damageDealt + " -> " + safeName(single) + " (forced)");
                        return r;
                    }
                    List<String> opts = java.util.Arrays.asList(
                            "MAX TRAMPLE: " + lethal + " (lethal) -> " + safeName(single) + ", " + trampleOver + " -> " + defender.getName(),
                            "ALL TO BLOCKER: " + damageDealt + " -> " + safeName(single) + " (no damage tramples through)");
                    if (!fastOk()) {
                        r.put(single, lethal); r.put(null, trampleOver);
                        auto("damage-assign(no-model)->max-trample");
                        return r;
                    }
                    int c = decideOne(fast, "damage-assign", "COMBAT DAMAGE ASSIGNMENT: " + safeName(attacker)
                            + " deals " + damageDealt + " (has trample), single blocker " + safeName(single)
                            + ". Choose how to split it.", opts, null);
                    if (c == 1) { r.put(single, damageDealt); }
                    else { // c==0, or -1/-2 unparsed/bridge-down -> deterministic max-trample, never Forge AI
                        r.put(single, lethal); r.put(null, trampleOver);
                        if (c == -2) auto("damage-assign(bridge-down)->max-trample");
                        else if (c < 0) auto("damage-assign(unparsed)->max-trample");
                    }
                    p("[ROUTER] damage-assign: " + (c == 1 ? "all to blocker" : "max-trample"));
                    return r;
                }
                if (blockers != null && blockers.size() > 1) {
                    return assignMultiBlockerDamage(attacker, blockers, damageDealt, defender);
                }
            } catch (Exception ignore) {
                // fall through to the deterministic no-blockers/unexpected-shape default below
            }
            // blockers == null/empty, or an unexpected shape threw above: no legal creature to assign
            // to (should not happen in practice — combat damage assignment is only invoked with at
            // least one blocker) but the safe, deterministic, non-AI default is "nothing to assign".
            Map<Card, Integer> r = new java.util.HashMap<>();
            if (damageDealt > 0 && defender != null) r.put(null, damageDealt); // no blocker to eat it -> tramples/goes to defender
            evalDec("damage-assign", 0, 0, "auto", 0, "damage-assign(no-blockers)->defender", null);
            return r;
        }

        /** Multi-blocker damage split, model-prioritized (see assignCombatDamage's javadoc above).
         *  Returns a lethal-first assignment in the model's (or, on failure, the engine's) blocker
         *  order — every path deterministic Java, never Forge's combat AI. */
        private Map<Card, Integer> assignMultiBlockerDamage(Card attacker, CardCollectionView blockers,
                int damageDealt, GameEntity defender) {
            List<Card> opts = new ArrayList<>(blockers);
            List<String> labels = cardLabels(opts);
            List<Integer> order = null;
            if (fastOk()) {
                String q = "COMBAT DAMAGE ASSIGNMENT: " + safeName(attacker) + " deals " + damageDealt
                        + " damage and is blocked by " + opts.size() + " creatures. List ALL " + opts.size()
                        + " of them in the ORDER you want damage assigned — the first gets lethal damage "
                        + "first, then any leftover cascades to the next in your list, and so on"
                        + (defender != null && attacker.hasKeyword(Keyword.TRAMPLE)
                                ? " (with trample, any damage left over after all blockers die tramples through to "
                                  + defender.getName() + ")." : ".");
                List<Integer> picks = decideMany(fast, "damage-assign", q, labels, opts.size(), opts.size());
                if (picks != null && picks.size() == opts.size()) order = picks;
            }
            if (order == null) {
                order = new ArrayList<>();
                for (int i = 0; i < opts.size(); i++) order.add(i);
                auto("damage-assign(auto-in-order)");
            }
            Map<Card, Integer> r = new java.util.HashMap<>();
            int remainingDmg = damageDealt;
            StringBuilder log = new StringBuilder();
            Card last = null;
            for (int idx : order) {
                Card b = opts.get(idx);
                last = b;
                int assign;
                if (remainingDmg <= 0) {
                    assign = 0;
                } else {
                    int lethal = ComputerUtilCombat.getEnoughDamageToKill(b, remainingDmg, attacker, true);
                    assign = Math.min(lethal, remainingDmg);
                }
                r.put(b, assign);
                remainingDmg -= assign;
                if (log.length() > 0) log.append(", ");
                log.append(safeName(b)).append('=').append(assign);
            }
            if (remainingDmg > 0) {
                boolean trample = defender != null && attacker.hasKeyword(Keyword.TRAMPLE);
                if (trample) {
                    r.put(null, remainingDmg);
                    log.append(", ").append(defender.getName()).append('=').append(remainingDmg);
                } else if (last != null) {
                    r.put(last, r.get(last) + remainingDmg);
                    log.append(" (+").append(remainingDmg).append(" excess -> ").append(safeName(last)).append(')');
                }
            }
            p("[ROUTER] damage-assign: " + opts.size() + " blockers, order=" + orderSummary(order, opts) + " -> " + log);
            return r;
        }

        /** "A > B > C" summary of a chosen index order against its option list (null-safe). */
        private static String orderSummary(List<Integer> order, List<Card> opts) {
            try {
                StringBuilder b = new StringBuilder();
                for (int i : order) { if (b.length() > 0) b.append(" > "); b.append(safeName(opts.get(i))); }
                return b.length() == 0 ? "(none)" : b.toString();
            } catch (Exception e) { return "?"; }
        }

        /** X / Multikicker / BidLife announcement. VALUE semantics: choice carries the announced
         *  NUMBER itself (not a menu index); -1 when super returns null — and that null is itself the
         *  AI's decision (it aborts the cast, PlaySpellAbility.java:757), so it is returned unchanged.
         *  Options enumerate min..max only when the range is small; max can be Integer.MAX_VALUE. */
        @Override
        public Integer announceRequirements(SpellAbility ability, int min, int max, String announce) {
            long t0 = System.currentTimeMillis();
            Integer v = super.announceRequirements(ability, min, max, announce);
            try {
                long span = (long) max - (long) min + 1L;
                List<String> labels = new ArrayList<>();
                if (span >= 1 && span <= 12) { for (int i = min; i <= max; i++) labels.add(String.valueOf(i)); }
                else labels.add("announce " + announce + " [" + min + ".." + (max == Integer.MAX_VALUE ? "unbounded" : String.valueOf(max)) + "]");
                int nOpts = (span >= 1 && span <= 10000) ? (int) span : -1;
                observeAiDec("announce-x", nOpts, v == null ? -1 : v.intValue(), t0,
                        "announce-x(ai) " + announce + "=" + v + " [" + min + ".." + max + "]" + srcOf(ability), labels);
            } catch (Exception ignore) { }
            return v;
        }

        /** Edict-style sacrifice (Sacrifice/Balance effects, Annihilator, Emerge/Offering costs) —
         *  ROUTED (report C1). This used to run entirely on Forge's built-in AI (`observeAiDec`-only,
         *  no model consultation, no [ROUTER] line) — which in one tournament game sacrificed the
         *  model's single best remaining threat one turn before it lost. Mirrors
         *  chooseCardsToDiscardFrom's failure ladder: whole-set-forced or no budget -> deterministic
         *  first-N; genuinely unparsed/bridge-down -> the same deterministic first-N — never Forge AI
         *  for a real choice. choice = count picked (COUNT-style, matches discard/attackers). */
        @Override
        public CardCollectionView choosePermanentsToSacrifice(SpellAbility sa, int min, int max,
                CardCollectionView validTargets, String message) {
            clearLast();
            if (validTargets == null || validTargets.isEmpty())
                return super.choosePermanentsToSacrifice(sa, min, max, validTargets, message);
            List<Card> opts = new ArrayList<>(validTargets);
            if (opts.size() <= min) return firstN(opts, min, "sacrifice(forced)"); // whole set forced, no real choice
            try {
                if (!smartOk()) return firstN(opts, min, "sacrifice(no-budget)");
                List<String> labels = new ArrayList<>();
                for (Card c : opts) labels.add(candidateLabel(c));
                String q = "You must SACRIFICE " + (min == max ? String.valueOf(min) : min + "-" + max)
                        + " permanent(s)" + srcOf(sa)
                        + (message == null || message.isEmpty() ? "" : ": " + oneLine(message))
                        + ". Choose what to give up — keep your best remaining threats/blockers/mana sources.";
                List<Integer> picks = decideMany(smart, "sacrifice", q, labels, min, max);
                if (picks == null) return firstN(opts, min, "sacrifice(unparsed)");
                CardCollection out = new CardCollection();
                StringBuilder log = new StringBuilder();
                for (int i : picks) { out.add(opts.get(i)); if (log.length() > 0) log.append(", "); log.append(opts.get(i).getName()); }
                p("[ROUTER] sacrifice" + srcOf(sa) + ": " + log);
                return out;
            } catch (Exception e) {
                return firstN(opts, min, "sacrifice(err)");
            }
        }

        // ---------------- budget + parse helpers ----------------

        // Pure budget PEEKS — no side effect. Counters are bumped in ask(), so [TALLY] counts
        // actual model calls, not budget checks (paths that check then dedupe/delegate don't inflate it).
        // FLOOR arm: budgets read as exhausted, so every site takes its deterministic fallback.
        private boolean smartOk() { return !"floor".equals(mode) && smartCalls < SMART_BUDGET; }
        private boolean fastOk()  { return !"floor".equals(mode) && fastCalls  < FAST_BUDGET;  }
        // Short exponential backoff between retries (0.5s, 2s) — long enough to let a flaky route or a
        // CLI process recover, short enough not to stack into a real hang across up to 3 attempts.
        private static final long[] BRIDGE_RETRY_BACKOFF_MS = { 500L, 2000L };
        /** Every model call funnels through here — also the single capture point for the decision record:
         *  store the full prompt/reply/tier/finish_reason so evalDec can attach them (a retry overwrites,
         *  so the LAST attempt's prompt/raw_reply are what get logged). */
        private String ask(LLMBridge b, String prompt) {
            long tAsk = System.currentTimeMillis();
            if (b == smart) smartCalls++; else fastCalls++;
            lastPrompt = prompt;
            lastTier = (b == smart) ? "smart" : "fast";
            lastAttempts++;
            long tSub = System.currentTimeMillis();
            String reply = b.ask(prompt);
            long subMs = System.currentTimeMillis() - tSub;
            // Dead/unresponsive bridge (process exit, stream EOF, deadline timeout, IO error — a parse
            // failure returns text, never null): restart stateful backends and re-ask, up to 2 more
            // times (3 attempts total) with short backoff, before letting callers take their
            // deterministic fallback. restart() matters most for the CLI timeout path, where the old
            // process is still alive and may later emit a stale result line a naive re-ask would misread
            // as the new answer. API backends no-op the restart, so for them each retry is deliberately
            // just another HTTP attempt. Budget-wise this still counts as the one call bumped above
            // (multiple round-trips, one decision) — bounded, never loops indefinitely.
            for (int attempt = 0; reply == null && attempt < BRIDGE_RETRY_BACKOFF_MS.length; attempt++) {
                lastRetryMs += subMs; // that failed sub-attempt's own time is dead time, not think-time
                long wait = BRIDGE_RETRY_BACKOFF_MS[attempt];
                p("[BRIDGE " + b.getLabel() + "] no reply (attempt " + (attempt + 2) + "/"
                        + (BRIDGE_RETRY_BACKOFF_MS.length + 1) + ") -> restarting session, retrying in " + wait + "ms");
                b.restart();
                try { Thread.sleep(wait); lastRetryMs += wait; } catch (InterruptedException ie) { Thread.currentThread().interrupt(); break; }
                lastAttempts++;
                tSub = System.currentTimeMillis();
                reply = b.ask(prompt);
                subMs = System.currentTimeMillis() - tSub;
            }
            // Per-seat health signal: observability for a bridge that's gone dark for many consecutive
            // DECISIONS (e.g. an OpenRouter route degrading for the rest of a match) without changing
            // behavior — callers still take their deterministic legal fallback either way. Fires the
            // loud marker once per streak, not once per failed decision.
            if (reply == null) {
                consecutiveBridgeFailures++;
                if (consecutiveBridgeFailures >= 5 && !circuitBreakerTripped) {
                    circuitBreakerTripped = true;
                    p("[CIRCUIT-BREAKER] " + consecutiveBridgeFailures + " consecutive bridge failures on "
                            + b.getLabel() + " -> deterministic fallback in effect until it recovers");
                }
                lastRetryMs += subMs; // the final sub-attempt of this ask() call also failed -> dead time
            } else {
                consecutiveBridgeFailures = 0;
                circuitBreakerTripped = false;
                // A prior top-level ask() call THIS SAME DECISION already produced a reply, and the caller
                // is asking again (e.g. an unparseable CHOICE/PICKS/KEEP reply) — that earlier "success" is
                // retroactively dead time, and its text is the first genuinely-discarded reply worth
                // keeping for diagnosis (jsonl first_failed_reply, capped).
                if (lastReply != null) {
                    if (lastFirstFailedReply == null) lastFirstFailedReply = cap(lastReply, 500);
                    lastRetryMs += lastSuccessMs;
                }
                lastSuccessMs = subMs; // only the truly-final successful sub-attempt's own duration
            }
            lastReply = reply;
            lastFinishReason = b.getLastFinishReason();
            lastPromptTokens = b.getLastPromptTokens();
            lastCompletionTokens = b.getLastCompletionTokens();
            lastAskMs += System.currentTimeMillis() - tAsk; // summed over every attempt; retained for the
                                                             // ms<=0 legacy substitution guard in evalDec
            return reply;
        }
        /** Cap a string at n chars for a jsonl field that must stay small (diagnostic reply snippets).
         *  Package-private (not private) so LlmSpikeTest can exercise it directly. */
        static String cap(String s, int n) {
            return (s == null || s.length() <= n) ? s : s.substring(0, n);
        }

        // In-JVM accessors so the game-summary line records the SAME budget/heuristic values this
        // controller actually ran with (they are private static final; do not recompute env off-JVM).
        static int smartBudget() { return SMART_BUDGET; }
        static int fastBudget()  { return FAST_BUDGET; }
        static boolean mullHeuristic() { return MULL_HEURISTIC; }
        static boolean skipHeuristics() { return SKIP_HEUR; }
        // Package-private instance accessors (mirrors the static ones above) so LlmSpikeTest can assert
        // a decision took the deterministic-fallback/never-Forge-AI path without needing EVAL_OUT wired
        // up to inspect jsonl output.
        int autoPicksCount() { return autoPicks; }
        int aiDelegationsCount() { return aiDelegations; }

        /** Record + loudly log a genuine handoff to Forge's built-in AI. Grep "[FALLBACK->AI]" to audit how
         *  often the engine brain — not the LLM — actually made a call. Goal: keep this near zero. */
        private void noteAi(String where) {
            aiDelegations++;
            p("[FALLBACK->AI] " + where + "  (ai=" + aiDelegations + " smart=" + smartCalls + " fast=" + fastCalls + " auto=" + autoPicks + ")");
            evalDec(firstWord(where), -1, -1, "ai", 0, where);
        }
        private <T> T aiDelegate(String where, T result) { noteAi(where); return result; }
        private void auto(String what) { autoPicks++; p("[AUTO-LEGAL] " + what); evalDec(firstWord(what), -1, -1, "auto", 0, what); }
        private static String firstWord(String s) {
            if (s == null || s.isEmpty()) return "?";
            int i = 0;
            while (i < s.length() && (Character.isLetterOrDigit(s.charAt(i)) || s.charAt(i) == '-')) i++;
            return i == 0 ? "?" : s.substring(0, i);
        }
        /** Legacy 6-arg form (auto/ai fallbacks and any un-migrated caller): no enumerable option menu, so
         *  options logs as []. prompt/raw_reply still reflect whatever the current decision captured in ask()
         *  (null for paths that never reached the model — floor/random, heuristic keeps, budget declines). */
        private void evalDec(String type, int nOptions, int choice, String tier, long ms, String fallback) {
            evalDec(type, nOptions, choice, tier, ms, fallback, null);
        }
        /** One JSONL line per decision when EVAL_OUT is set — the per-decision dataset for eval runs.
         *  NEW fields (options/prompt/raw_reply) make every line self-describing: the exact menu the decider
         *  chose among, the full prompt sent to the model (incl. the snapshot state), and the model's raw
         *  final text. prompt/raw_reply are the LAST model attempt for this decision (captured in ask()),
         *  or null when no model was consulted. */
        private void evalDec(String type, int nOptions, int choice, String tier, long ms, String fallback, List<String> options) {
            if (!EvalLog.on()) return;
            // Many call sites pass ms=0 because their timing happens inside ask(); when a model
            // round-trip WAS captured for this decision (lastPrompt != null — clearLast() guards
            // every path), substitute lastSuccessMs — the SUCCESSFUL final attempt's own wall time only,
            // never dead retry time (that lives in retry_ms below) — so real model calls never log
            // latency_ms=0 (which the analyzer excludes from latency stats). Restrict this to the
            // model tiers: an auto/ai fallback that fires AFTER a failed ask() also has lastPrompt
            // set, but its row means "deterministic/Forge-AI decision" and must not inherit the
            // wasted model round-trip's latency (it would pollute the ai/auto latency stats).
            if (ms <= 0 && lastPrompt != null && ("smart".equals(tier) || "fast".equals(tier))) ms = lastSuccessMs;
            emitDecisionRow(getPlayer(), seat, type, nOptions, choice, tier, ms, fallback, options,
                    lastPrompt, lastReply, lastRetryMs, lastAttempts, lastFinishReason, lastFirstFailedReply,
                    lastPromptTokens, lastCompletionTokens, lastShadowChoice);
        }
        /** Tier label for asked decisions: random arm decisions are harness-made, not model-made. */
        private String tierOf(boolean smartTier) { return rnd != null ? "auto" : (smartTier ? "smart" : "fast"); }
        // ---------------- D4 DAgger shadow labeling (SHADOW_AI=1) ----------------
        // Each helper computes the Forge-AI's choice over the SAME menu the student just faced, encoded
        // identically to the student's own `choice` for that type, and returns it for lastShadowChoice.
        // ALL are guarded `if (!SHADOW) return null` and wrapped try/catch(Throwable)->null: the shadow is
        // pure observation and must NEVER mutate the real game object or throw into the student's path.
        // The AI's choice is computed on the SAME player (getAi() is this seat's own brains) — DAgger's
        // "what would the expert do in the state the student actually reached" query.
        //
        // FOUR isolation axes, all mandatory for the bit-reproducibility invariant (shadow must produce an
        // IDENTICAL winner+turn-count with vs without SHADOW_AI=1). Axes 3 and 4 were found by pre-commit
        // audit AFTER axes 1-2 were believed sufficient — do not trim this list without re-running the
        // random-arm canary below:
        //   1. STATE: combat helpers write only a SCRATCH Combat (pattern: getPredictedCombat); the real
        //      combat/game objects are never mutated.
        //   2. RNG: the Forge AI's attacker/blocker/play evaluators draw from the shared static MyRandom
        //      (AiAttackController/AiBlockController/isSafeToHoldLandDrop all call percentTrue). Left
        //      un-isolated, shadow's extra draws would advance the game's RNG stream and desync every later
        //      shuffle/coin-flip. shadowRun() swaps in an EPHEMERAL, deterministically-seeded Random and
        //      restores the game RNG in a finally — shadow consumes ZERO of the game's RNG.
        //   3. AI CARD MEMORY: chooseSpellAbilityToPlay reserves mana sources that OUTLIVE the call and that
        //      the student's own cast then honors (this seat IS a PlayerControllerAi). See shadowRun.
        //   4. SVARS: the AI writes scoring SVars onto the real candidate SAs. See shadowPlay.
        //
        // VALIDATING THIS: use the RANDOM arm, never floor. floor short-circuits every play before the menu
        // is built, so a floor A/B exercises ONLY shadowMulligan — which is pure reads and cannot perturb
        // anything, i.e. the one path that proves nothing. random reaches shadowPlay/shadowAttackers/
        // shadowBlockers while staying zero-model and deterministic:
        //   GAME_SEED=42 CONTROLLER_A=random CONTROLLER_B=random [SHADOW_AI=1] bash run-llm-headless.sh
        // Pinned results (seeds 42, 7): B|23|AllOpponentsLost and B|19|AllOpponentsLost, identical with and
        // without the flag, decision-row counts identical, zero labels when the flag is off.

        /** Monotonic per-seat shadow counter: makes each shadow call's ephemeral RNG stream distinct.
         *  Without it every decision in a turn (and BOTH seats) replayed the identical stream, so the
         *  AI's percentTrue(50) tie-breaks resolved the same way corpus-wide — a silent label bias. */
        private long shadowSeq = 0L;

        /** Run an isolated shadow computation, then restore EVERY channel the Forge AI can write through.
         *  Returns null on any throw (the shadow never affects the game).
         *
         *  Restoring the RNG alone is NOT enough — measured, not assumed. Two channels:
         *
         *   1. RNG: the AI's evaluators draw from the shared static MyRandom. Swap in an ephemeral seeded
         *      Random so shadow consumes ZERO of the game's stream, restore in the finally.
         *   2. AiCardMemory: chooseSpellAbilityToPlay reserves mana sources
         *      (AiController:770 -> HELD_MANA_SOURCES_FOR_NEXT_SPELL) and the combat/fog evaluators write
         *      MANDATORY_ATTACKERS / TRICK_ATTACKERS / CHOSEN_FOG_EFFECT / PAYS_*_COST. These SURVIVE the
         *      call, and PlayerControllerLLM extends PlayerControllerAi, so ComputerUtilMana:935's
         *      isManaSourceReserved check (which early-returns only for NON-PlayerControllerAi controllers)
         *      DOES apply to the student's real cast — the student does not override payManaCost. A stale
         *      shadow reservation would therefore change which lands the student taps, and the reserved
         *      branch short-circuits before a percentTrue draw, desyncing the RNG too. Snapshot every
         *      MemorySet before the call and restore it after. getMemorySet returns the LIVE set, so the
         *      snapshot copies.
         *
         *  The ephemeral seed mixes game seed + seat + turn + a monotonic counter, never the game RNG, so
         *  shadow draws stay deterministic (reproducible labels) yet cost the game stream nothing. */
        private Integer shadowRun(java.util.concurrent.Callable<Integer> body) {
            if (!SHADOW) return null;
            java.util.Random savedRng = MyRandom.getRandom();
            java.util.Map<AiCardMemory.MemorySet, java.util.List<Card>> savedMem = snapshotAiMemory();
            try {
                long turn = 0;
                try { turn = getPlayer().getGame().getPhaseHandler().getTurn(); } catch (Throwable ignore) { }
                long seatMix = 0L;
                try { seatMix = getPlayer().getName().hashCode() & 0xFFFFFFFFL; } catch (Throwable ignore) { }
                MyRandom.setRandom(new java.util.Random(
                        SEED_USED * 1000003L + seatMix * 31L + turn * 131L + (++shadowSeq)));
                return body.call();
            } catch (Throwable t) {
                return null;
            } finally {
                MyRandom.setRandom(savedRng);   // game RNG stream restored byte-for-byte
                restoreAiMemory(savedMem);      // AI reservations/markers rolled back
            }
        }

        /** Copy every AiCardMemory set (getMemorySet hands back the live collection). Null on any failure,
         *  which restoreAiMemory treats as "nothing to restore" rather than clobbering real state. */
        private java.util.Map<AiCardMemory.MemorySet, java.util.List<Card>> snapshotAiMemory() {
            try {
                java.util.Map<AiCardMemory.MemorySet, java.util.List<Card>> snap =
                        new java.util.EnumMap<>(AiCardMemory.MemorySet.class);
                for (AiCardMemory.MemorySet ms : AiCardMemory.MemorySet.values()) {
                    java.util.Set<Card> live = AiCardMemory.getMemorySet(getPlayer(), ms);
                    snap.put(ms, live == null ? null : new java.util.ArrayList<>(live));
                }
                return snap;
            } catch (Throwable t) { return null; }
        }

        /** Restore the snapshot taken by snapshotAiMemory: clear each set, re-add exactly what was there. */
        private void restoreAiMemory(java.util.Map<AiCardMemory.MemorySet, java.util.List<Card>> snap) {
            if (snap == null) return;
            try {
                for (java.util.Map.Entry<AiCardMemory.MemorySet, java.util.List<Card>> e : snap.entrySet()) {
                    if (e.getValue() == null) continue;
                    AiCardMemory.clearMemorySet(getPlayer(), e.getKey());
                    for (Card c : e.getValue()) AiCardMemory.rememberCard(getPlayer(), c, e.getKey());
                }
            } catch (Throwable ignore) { }
        }

        /** Shadow label for chooseSpellAbilityToPlay: the AI's play as a menu index (or -1 for PASS),
         *  reusing the ai-log seat's indexOfPlay mapper. SAFE: chooseSpellAbilityToPlay's only real
         *  game-object mutation is canPlaySa setting targets/X on the SAME candidate SA objects the
         *  student enumerates — the caller's mandatory `chosen.resetTargets()` guard (in the cast branch)
         *  erases any such shadow-set target before ensureTargetsSet re-derives the LLM's targets, and the
         *  engine re-announces X at cast time (PlaySpellAbility.announceValuesLikeX). */
        private Integer shadowPlay(final PlayOptions po) {
            // MUST be first: everything below touches real SAs, so none of it may run when the labeler is
            // off. (An earlier revision snapshotted before delegating to shadowRun's own !SHADOW guard,
            // which silently mutated SAs in every non-shadow config — the exact class of bug this method
            // exists to prevent.)
            if (!SHADOW) return null;
            // The AI's play evaluation writes SVars on the REAL candidate SAs it scores — DamageDealAi /
            // PumpAi do root.setSVar("EnergyToPay", ...) and AiController reads it back in the number/cost
            // path. resetTargets() clears TargetChoices but never SVars, so a shadow-written SVar would ride
            // onto the student's actual cast (deck B is Mardu Energy — a live pool, not a hypothetical).
            //
            // RESTORE THE DELTA, NEVER THE MAP. getSVars() returns a MERGED view (host card + card state,
            // then the SA's own on top) while setSVars() REPLACES the SA's private map. So the obvious
            // snapshot/restore round-trip (setSVars(getSVars())) permanently flattens the card's whole SVar
            // table into the SA — and since getSVar() consults the private map first, every later card SVar
            // change or card-state switch (transform/MDFC/adventure) would be shadowed for the rest of the
            // game. Instead: diff merged-before against merged-after, and undo only what changed, via
            // removeSVar/setSVar (which touch the private map alone).
            //
            // KNOWN RESIDUAL COVERAGE GAPS (accepted; the delta below is surgical, not total):
            //   * `chain` covers the MENU (po.spells + po.landPlays, capped 12+4) and their sub-chains. The
            //     AI additionally walks getAvailableCards()/getPossibleETBCounters() over graveyard, exile
            //     and library-top, so SVar writes to SAs OUTSIDE the menu are not rolled back. The menu is
            //     what the student can actually cast, so those are the SAs that matter for the cast itself.
            //   * CARD-level SVars are untouched: ChangeZoneAi does source.setSVar("AIPreferenceOverride")
            //     on the Card, not the SA, and nothing here rolls that back.
            // If a future DAgger corpus shows unexplained divergence, widen the snapshot to the same source
            // the AI walks before suspecting anything else.
            final java.util.List<SpellAbility> chain = new ArrayList<>();
            for (List<SpellAbility> grp : java.util.Arrays.asList(po.spells, po.landPlays))
                if (grp != null) for (SpellAbility s : grp)
                    for (SpellAbility w = s; w != null; w = w.getSubAbility()) chain.add(w);
            final java.util.List<java.util.Map<String, String>> savedSVars = new ArrayList<>();
            for (SpellAbility s : chain) {
                java.util.Map<String, String> m = null;
                try { m = new java.util.HashMap<>(s.getSVars()); } catch (Throwable ignore) { }
                savedSVars.add(m);
            }
            try {
            return shadowRun(new java.util.concurrent.Callable<Integer>() {
                public Integer call() {
                    // AiController.chooseSpellAbilityToPlayFromList runs the eval on a separate
                    // "Game AI Eval" thread with future.get(game.getAITimeout(), SECONDS) and returns NULL
                    // on Timeout/Execution/Interrupted (AiController:1685-1688). A null from THAT path is
                    // "the expert didn't answer", not "the expert passed" — minting it as -1/PASS would
                    // teach the student to pass under exactly the states the AI finds hardest, and
                    // over-passing is this student's known pathology. Time the call and withhold the label
                    // when the elapsed time says we hit the timeout.
                    long t0 = System.nanoTime();
                    List<SpellAbility> r = getAi().chooseSpellAbilityToPlay();
                    long elapsedMs = (System.nanoTime() - t0) / 1_000_000L;
                    if (r == null || r.isEmpty()) {
                        long timeoutMs = 5_000L;
                        try { timeoutMs = getPlayer().getGame().getAITimeout() * 1000L; } catch (Throwable ignore) { }
                        // 90% of the budget: a genuine "nothing worth casting" verdict returns fast.
                        if (elapsedMs >= (timeoutMs * 9L) / 10L) {
                            // KNOWN RESIDUAL RISK (accepted, not solved): on timeout Forge cancels the
                            // "Game AI Eval" thread and its own source comment warns the thread may leave
                            // "partially mutated shared state". A thread that outlives this call draws from
                            // the RESTORED game RNG and could touch SAs after our SVar/target rollback.
                            // Withholding the label is the cheap half of the fix; the expensive half is
                            // aborting the game. This is loud on purpose — if it ever fires in a real
                            // corpus run, treat that game as suspect rather than trusting the invariant.
                            p("[SHADOW] play eval hit the AI timeout (" + elapsedMs + "ms) — label withheld;"
                              + " this game's isolation guarantee is WEAKENED, review before using its rows");
                            return null;
                        }
                        return -1; // genuine PASS — same as the student's play-PASS (-1)
                    }
                    int idx = PlayerControllerAiLogging.indexOfPlay(r.get(0), po);
                    // AI picked an ability OUTSIDE our capped play menu (activated/mana ability the 12-spell
                    // menu excludes): this is NOT a pass. Return null so build_dagger_examples drops the row
                    // instead of minting a false CHOICE:PASS target — mirroring the ai-log seat's
                    // "ai-choice-not-in-menu" fallback (which build_ai_teacher_examples drops via drop_fallback).
                    // Only a genuine empty AI reply maps to -1/PASS.
                    return idx < 0 ? null : idx;
                }
            });
            } finally {
                for (int i = 0; i < chain.size(); i++) {
                    java.util.Map<String, String> before = savedSVars.get(i);
                    if (before == null) continue;
                    SpellAbility sa = chain.get(i);
                    try {
                        java.util.Map<String, String> after = new java.util.HashMap<>(sa.getSVars());
                        for (java.util.Map.Entry<String, String> e : after.entrySet()) {
                            String k = e.getKey(), was = before.get(k);
                            if (was != null && was.equals(e.getValue())) continue; // untouched
                            // Drop the SA-private entry; the merged view then falls back to the card.
                            sa.removeSVar(k);
                            // Only re-assert when the fallback doesn't already reproduce the original
                            // (i.e. the original really was an SA-private value). A key absent before
                            // (was == null) was AI-added: removing it is the whole restore.
                            if (was != null && !was.equals(sa.getSVar(k))) sa.setSVar(k, was);
                        }
                        // Keys the AI deleted outright: re-assert if the fallback no longer yields them.
                        for (java.util.Map.Entry<String, String> e : before.entrySet()) {
                            if (after.containsKey(e.getKey())) continue;
                            if (!e.getValue().equals(sa.getSVar(e.getKey())))
                                sa.setSVar(e.getKey(), e.getValue());
                        }
                    } catch (Throwable ignore) { }
                }
            }
        }

        /** Shadow label for declareAttackers: the AI's attacker COUNT, computed into a SCRATCH Combat so
         *  the real combat is never touched (proven pattern: AiController.getPredictedCombat). */
        private Integer shadowAttackers(final Player attacker, final Combat real, final int legalCount) {
            return shadowRun(new java.util.concurrent.Callable<Integer>() {
                public Integer call() {
                    Combat scratch = new Combat(attacker);
                    getAi().declareAttackers(attacker, scratch);
                    CardCollection atks = scratch.getAttackers();
                    int n = atks == null ? 0 : atks.size();
                    return Math.min(n, legalCount); // COUNT encoding, matches the student's `attackers` choice
                }
            });
        }

        /** Shadow label for declareBlockers: the AI's block COUNT, computed into a SCRATCH Combat that
         *  carries the SAME real attackers (copied via addAttacker, a pure data-structure op) so the AI
         *  blocks against the true board; the real combat is never touched. */
        private Integer shadowBlockers(final Player defender, final Combat real, final int poolSize) {
            return shadowRun(new java.util.concurrent.Callable<Integer>() {
                public Integer call() {
                    Combat scratch = new Combat(real.getAttackingPlayer());
                    for (Card atk : real.getAttackers())
                        scratch.addAttacker(atk, real.getDefenderByAttacker(atk));
                    getAi().declareBlockersFor(defender, scratch);
                    CardCollection blk = scratch.getAllBlockers();
                    int n = blk == null ? 0 : blk.size();
                    return Math.min(n, poolSize); // COUNT encoding, matches the student's `blockers` choice
                }
            });
        }

        /** Shadow label for mulliganKeepHand: 0=keep, 1=mulligan. SAFE: super.mulliganKeepHand is a pure
         *  read (!ComputerUtil.wantMulligan) — no game or SA mutation. Isolated for uniformity. */
        private Integer shadowMulligan(final Player firstPlayer, final int cardsToReturn) {
            return shadowRun(new java.util.concurrent.Callable<Integer>() {
                public Integer call() {
                    boolean keep = PlayerControllerLLM.super.mulliganKeepHand(firstPlayer, cardsToReturn);
                    return keep ? 0 : 1; // same 0/1 encoding as the student's mulligan `choice`
                }
            });
        }

        /** Deterministic legal fallback for "pick some cards": take the first n (a legal set), never the AI. */
        private CardCollection firstN(List<Card> opts, int n, String why) {
            CardCollection out = new CardCollection();
            for (int i = 0; i < n && i < opts.size(); i++) out.add(opts.get(i));
            auto(why + " -> " + names(out, false, 10));
            return out;
        }

        /** "{2}{B}{B}"-style cost, or null if none. getShortString() already includes braces. */
        private static String costStr(Card c) {
            try {
                if (c.getManaCost() == null) return null;
                String mc = c.getManaCost().getShortString();
                if (mc == null || mc.isEmpty() || "-1".equals(mc) || "0".equals(mc)) return null;
                // getShortString mixes formats ("1 {W}", "{B}", "4BBBB") — normalize to compact "{1}{W}" style
                mc = mc.replace(" ", "");
                return mc.startsWith("{") || mc.contains("{") ? mc : "{" + mc + "}";
            } catch (Exception e) { return null; }
        }
        /** "Library"/"Library/Hand" — join for the REVEALED intel line's zone tag. */
        private static String zoneLabel(java.util.Set<ZoneType> zones) {
            if (zones == null || zones.isEmpty()) return "revealed";
            StringBuilder b = new StringBuilder();
            for (ZoneType z : zones) { if (b.length() > 0) b.append('/'); b.append(z); }
            return b.toString();
        }
        private static String srcOf(SpellAbility sa) {
            try { return sa != null && sa.getHostCard() != null ? " [" + sa.getHostCard().getName() + "]" : ""; }
            catch (Exception e) { return ""; }
        }
        /** Shared candidate label for EVERY target/entity option list (chooseSingleEntityForEffect,
         *  chooseSingleCardForZoneChange, chooseTargetsFor/ensureTargetsSet, and via entityLabel/
         *  cardLabels every discard/cards-for-effect/sacrifice/damage-order menu too): name + creature
         *  P/T + planeswalker loyalty + non-loyalty counters + [tapped]/[untapped] whenever the
         *  candidate is currently on the battlefield. One factored builder so duplicate-named
         *  permanents are ALWAYS distinguishable, everywhere targeting happens — the report's QF-3
         *  misfire class (three unlabeled "Volcanic Island" options, one already tapped, an
         *  unforced/unrecoverable misplay no amount of model reasoning could fix) becomes impossible. */
        static String candidateLabel(Card c) {
            StringBuilder s = new StringBuilder(c.getName());
            if (c.isCreature()) s.append(" (").append(c.getNetPower()).append('/').append(c.getNetToughness()).append(')');
            if (c.isPlaneswalker()) { try { s.append("[loyalty ").append(c.getCurrentLoyalty()).append(']'); } catch (Exception ignore) { } }
            try { if (c.isInPlay()) s.append(c.isTapped() ? "[tapped]" : "[untapped]"); } catch (Exception ignore) { }
            try {
                com.google.common.collect.Multiset<CounterType> cts = c.getCounters();
                if (cts != null && !cts.isEmpty()) {
                    StringBuilder cb = new StringBuilder();
                    for (com.google.common.collect.Multiset.Entry<CounterType> e : cts.entrySet()) {
                        if (e.getElement().is(CounterEnumType.LOYALTY) || e.getCount() <= 0) continue;
                        if (cb.length() > 0) cb.append(", ");
                        cb.append(e.getElement().getName()).append(" x").append(e.getCount());
                    }
                    if (cb.length() > 0) s.append('[').append(cb).append(']');
                }
            } catch (Exception ignore) { }
            return s.toString();
        }
        private static String entityLabel(GameEntity e) {
            if (e instanceof Card) return candidateLabel((Card) e) + " [" + ((Card) e).getZone() + "]";
            return e.getName();
        }
        private static String saLabel(SpellAbility sa) {
            String host = sa.getHostCard() != null ? sa.getHostCard().getName() : "?";
            String cost = "";
            try {
                String mc = sa.getHostCard() != null ? costStr(sa.getHostCard()) : null;
                if (mc != null) cost = " " + mc;
            } catch (Exception ignore) { }
            String desc = sa.getDescription();
            if (desc == null || desc.isEmpty()) desc = String.valueOf(sa);
            desc = desc.replaceAll("\\s+", " ").trim();
            desc = truncateWords(desc, 160); // word-boundary + ellipsis, not a raw mid-word/mid-number cut (C4)
            return host + cost + (desc.isEmpty() ? "" : " [" + desc + "]");
        }
        /** Strip markdown wrapping (fenced/inline code, bold, italics) that some models wrap their
         *  final tag line in ("**CHOICE: 3**", "`CHOICE: 3`") — a literal substring search on `key`
         *  would otherwise still find it (the letters are untouched), but stripping first keeps the
         *  downstream separator-handling below simple and uniform. */
        static String stripMarkdown(String s) {
            if (s == null) return null;
            s = s.replace("```", "").replace("`", "");
            s = s.replace("**", "").replace("__", "");
            return s;
        }
        // Tags whose colon is treated as optional (matched as a bare word, then any of ": - <space>"
        // before the value is skipped) — deliberately just the three menu tags named in the hardening
        // requirement. Everything else (PLAN:, CARD:, and the ANSWER: synonym below) keeps requiring
        // its literal colon, since "answer"/"plan"/"card" are common English words that would otherwise
        // false-match inside ordinary reasoning prose.
        private static final java.util.Set<String> FLEXIBLE_SEPARATOR_KEYS =
                new java.util.HashSet<>(java.util.Arrays.asList("CHOICE:", "PICKS:", "BLOCKS:"));
        /** Find the line(s) matching ANY of `keys` (case-insensitive) and return the text after the
         *  LAST such match in the whole reply, separator stripped — i.e. genuine last-occurrence-wins
         *  across every accepted spelling, not just the primary one (so degenerate repetition of the
         *  primary tag still doesn't hide a later reply expressed via a synonym). Markdown wrapping is
         *  stripped from the whole reply first (see stripMarkdown). */
        static String lastSegmentAny(String reply, String... keys) {
            reply = stripMarkdown(reply);
            String found = null;
            for (String ln : reply.split("\\R")) {
                String upper = ln.toUpperCase(Locale.ROOT);
                for (String key : keys) {
                    boolean flexible = FLEXIBLE_SEPARATOR_KEYS.contains(key);
                    String bare = (flexible && key.endsWith(":")) ? key.substring(0, key.length() - 1) : key;
                    int i = upper.indexOf(bare.toUpperCase(Locale.ROOT));
                    if (i >= 0) {
                        found = ln.substring(i + bare.length()).replaceFirst("^\\s*[:\\-]?\\s*", "");
                        break; // this line already matched one of the keys; don't double-match a second key on it
                    }
                }
            }
            return found;
        }
        static String lastSegment(String reply, String key) {
            return lastSegmentAny(reply, key);
        }
        /** Last CHOICE/ANSWER number in the reply, tolerant of markdown wrapping, missing colons, and
         *  trailing prose after the number (the first digit run in the matched segment wins — a
         *  reasoning aside after the number, e.g. "CHOICE: 3 (final answer)", must not confuse it). */
        static int parseTagNumber(String reply, String key) {
            String seg = lastSegmentAny(reply, key, "ANSWER:");
            if (seg == null) return -1;
            Matcher m = Pattern.compile("\\d+").matcher(seg);
            return m.find() ? Integer.parseInt(m.group()) : -1;
        }
        private static Card matchByName(Iterable<Card> cards, String upperSeg) {
            for (Card c : cards) if (upperSeg.contains(c.getName().toUpperCase())) return c;
            return null;
        }
        /** Indices (menu order) of every candidate the stated turn PLAN names by card name — the
         *  intent-aware bridge-down fallback (report C3): tokenizing the model's own already-captured
         *  plan and matching it against the live legal candidates turns a dead-bridge default from
         *  "blindly override whatever the model just said" into "do what it already told us it wanted".
         *  Empty when there's no plan yet this turn, or the plan doesn't name anything on offer here.
         *  Thin wrapper over the pure, unit-testable {@link #namedIndices}. */
        private List<Integer> planNamedIndices(List<? extends GameEntity> cands) {
            List<String> names = new ArrayList<>();
            for (GameEntity ge : cands) {
                try { names.add(ge.getName()); } catch (Exception e) { names.add(null); }
            }
            return namedIndices(names, turnPlan);
        }
        /** Pure core of {@link #planNamedIndices}: indices (list order) of every name in
         *  `candidateNames` that appears (case-insensitive substring) in `planText`. State-free and
         *  Game-object-free so it's directly unit-testable. Empty when `planText` is null/blank. */
        static List<Integer> namedIndices(List<String> candidateNames, String planText) {
            List<Integer> out = new ArrayList<>();
            if (planText == null || planText.isEmpty()) return out;
            String upper = planText.toUpperCase();
            for (int i = 0; i < candidateNames.size(); i++) {
                String n = candidateNames.get(i);
                if (n != null && upper.contains(n.toUpperCase())) out.add(i);
            }
            return out;
        }
        /** Pure trample-split math used by assignCombatDamage's single-blocker/trample menu: how much
         *  of `damageDealt` is lethal-to-the-blocker vs. tramples over to the defender. `killDamage` is
         *  whatever ComputerUtilCombat.getEnoughDamageToKill reports (may exceed damageDealt when the
         *  blocker can't be killed with what's available — e.g. indestructible without deathtouch, or
         *  simply not enough damage). Returns {lethal, trampleOver}; trampleOver is 0 whenever the
         *  blocker can't be killed outright, since there's nothing to legally trample over then. */
        static int[] trampleSplit(int damageDealt, int killDamage) {
            int lethal = Math.min(Math.max(damageDealt, 0), Math.max(killDamage, 0));
            int trampleOver = damageDealt - lethal;
            return new int[]{lethal, trampleOver};
        }

        /** Immutable holder for the play menu's three parallel enumerations (castable spells, the
         *  land-play abilities, and their host land cards), returned by {@link #enumeratePlayOptions}. */
        static final class PlayOptions {
            final List<SpellAbility> spells;
            final List<SpellAbility> landPlays;
            final List<Card> landCards;
            PlayOptions(List<SpellAbility> spells, List<SpellAbility> landPlays, List<Card> landCards) {
                this.spells = spells; this.landPlays = landPlays; this.landCards = landCards;
            }
        }

        /** Enumerate the play menu exactly as {@link #chooseSpellAbilityToPlay} builds it — castable,
         *  affordable, legally-targetable non-land/non-mana spells (capped at 12), plus distinct
         *  playable lands by name (capped at 4, only on the player's own main phase). Factored out of
         *  the LLM seat's play method so the ai-teacher seat builds the byte-identical option list; the
         *  LLM method calls this in place, so its output is unchanged. Read-only w.r.t. real game state
         *  (the only mutation, {@code setActivatingPlayer} inside the target-legality probe, mirrors the
         *  original inline code). */
        static PlayOptions enumeratePlayOptions(Player me, boolean ownMain) {
            List<SpellAbility> options = new ArrayList<>();
            for (SpellAbility sa : ComputerUtilAbility.getSpellAbilities(
                    ComputerUtilAbility.getAvailableCards(me.getGame(), me), me)) {
                if (sa.isLandAbility() || sa.isManaAbility()) continue;
                if (!sa.canPlay()) continue;
                if (!ComputerUtilMana.canPayManaCost(sa, me, 0, false)) continue;
                // Skip a targeted spell that currently has NO legal target — casting it just fizzles
                // ("Couldn't add to stack, failed to target"), e.g. Animate Dead with an empty graveyard.
                if (sa.usesTargeting()) {
                    try {
                        if (sa.getActivatingPlayer() == null) sa.setActivatingPlayer(me);
                        TargetRestrictions tr = sa.getTargetRestrictions();
                        if (tr != null && tr.getAllCandidates(sa, false).size() < Math.max(1, sa.getMinTargets())) continue;
                    } catch (Exception ignore) { /* can't evaluate -> offer it, prior behavior */ }
                }
                options.add(sa);
                if (options.size() >= 12) break;
            }
            // Distinct lands we could play this main phase (by name), each offered as its own option so the
            // model chooses WHICH land — colored sources, fetchlands, painlands all matter for sequencing.
            List<SpellAbility> landPlays = new ArrayList<>();
            List<Card> landCards = new ArrayList<>();
            if (ownMain) {
                CardCollection lands = ComputerUtilAbility.getAvailableLandsToPlay(me.getGame(), me);
                if (lands != null) {
                    java.util.HashSet<String> seen = new java.util.HashSet<>();
                    for (Card lc : lands) {
                        if (!seen.add(lc.getName())) continue;
                        SpellAbility lsa = null;
                        for (SpellAbility sa : lc.getAllPossibleAbilities(me, true)) if (sa.isLandAbility()) { lsa = sa; break; }
                        if (lsa != null) { landPlays.add(lsa); landCards.add(lc); }
                        if (landPlays.size() >= 4) break;
                    }
                }
            }
            return new PlayOptions(options, landPlays, landCards);
        }

        /** The play menu's option labels in menu order: "CAST &lt;saLabel&gt;" per spell, "PLAY LAND
         *  &lt;name&gt;" per land, then the trailing "PASS (do nothing)". Shared by the LLM seat's log row
         *  and the ai-teacher seat so the {@code options} array is byte-identical. */
        static List<String> buildPlayLabels(List<SpellAbility> options, List<Card> landCards) {
            List<String> playLabels = new ArrayList<>();
            for (SpellAbility sa : options) playLabels.add("CAST " + saLabel(sa));
            for (Card lc : landCards) playLabels.add("PLAY LAND " + lc.getName());
            playLabels.add("PASS (do nothing)");
            return playLabels;
        }

        // ---- shared prompt-template builders --------------------------------------------------------
        // The LLM seat and the ai-teacher seat MUST send the byte-identical (snapshot + question +
        // numbered-menu) prompt for a given decision, or the process-metrics state-hash join
        // (eval/process_metrics.py state_key hashes `prompt`+options) never pairs teacher and student
        // rows, and the ai-teacher SFT user-message loses the decision framing + menu entirely. These
        // builders are the single source of that template — decideOne/decideMany/chooseSpellAbilityToPlay
        // call them so the ai-teacher's reconstruction is identical by construction, not by copy.

        /** 1-based numbered play menu ("  N) label\n" per line); labels shown RAW (already formatted by
         *  {@link #buildPlayLabels}). Matches {@link #chooseSpellAbilityToPlay}'s inline menu build. */
        static String numberedPlayMenu(List<String> playLabels) {
            StringBuilder menu = new StringBuilder();
            int n = 1;
            for (String o : playLabels) menu.append("  ").append(n++).append(") ").append(o).append('\n');
            return menu.toString();
        }

        /** The reasoning tail of the (non-plan) play prompt — the branch the ai-teacher always uses. */
        static final String PLAY_PROMPT_TAIL = "Reason briefly, then end with EXACTLY: CHOICE: <number>";

        /** The play prompt up to (not including) the reasoning tail, so the ai-teacher's play row prompt
         *  is byte-identical to the LLM seat's non-plan play prompt for the same state + menu. */
        static String playPromptPrefix(String snapshot, PhaseType phase, boolean ownMain, String menuStr) {
            return snapshot + "\nDECISION: You have priority (" + phase
                    + (ownMain ? ", your main phase" : ", instant-speed window") + "). Choose ONE action:\n" + menuStr;
        }

        /** The exact attempt-0 prompt {@link #decideOne} sends: snapshot + question + numbered options
         *  (options via {@code oneLine}, the trailing decline option raw) + the CHOICE tail. */
        static String buildDecideOnePrompt(String snapshot, String question, List<String> options, String noneLabel) {
            StringBuilder menu = new StringBuilder();
            int n = 1;
            for (String o : options) menu.append("  ").append(n++).append(") ").append(oneLine(o)).append('\n');
            if (noneLabel != null) menu.append("  ").append(n++).append(") ").append(noneLabel).append('\n');
            return snapshot + "\nPRIORITY: you\nDECISION: " + question + "\nOptions:\n" + menu
                    + "Reason briefly, then end with EXACTLY: CHOICE: <number>";
        }

        /** The exact attempt-0 prompt {@link #decideMany} sends (PICKS-style, min..max). */
        static String buildDecideManyPrompt(String snapshot, String question, List<String> options, int min, int max) {
            StringBuilder menu = new StringBuilder();
            int n = 1;
            for (String o : options) menu.append("  ").append(n++).append(") ").append(oneLine(o)).append('\n');
            return snapshot + "\nPRIORITY: you\nDECISION: " + question + "\nChoose between " + min + " and " + max
                    + " of these (by number):\n" + menu
                    + "Reason briefly, then end with EXACTLY: PICKS: <comma-separated numbers>";
        }

        /** The LLM seat's bespoke mulligan prompt body (not a decideOne/Many template — a KEEP/MULLIGAN
         *  free-text ask). {@code handNames} is the comma-joined card-name list both seats build the same
         *  way. Shared so the ai-teacher seat's reconstruction can never drift from the LLM seat's wire
         *  format. See {@link #mulliganKeepHand} (LLM seat) and PlayerControllerAiLogging.mulliganKeepHand. */
        static String buildMulliganPrompt(int size, int lands, String handNames) {
            return "MULLIGAN DECISION. This is a " + size + "-card hand with " + lands + " land(s): " + handNames
                    + ". If you MULLIGAN you will look at a NEW hand of only " + Math.max(0, size - 1)
                    + " cards (each mulligan costs you a card), and you can never go below 5. If you KEEP you play this "
                    + size + "-card hand as-is. Keeping a workable hand almost always beats shrinking it. "
                    + "For a combo deck, a hand with your enablers + a payoff is a KEEP even on few lands; only ship a hand "
                    + "that genuinely cannot function. Keep or mulligan? End with EXACTLY one word: KEEP or MULLIGAN.";
        }

        /** The attackers decideMany QUESTION (the {@code question} arg to {@link #buildDecideManyPrompt},
         *  which the LLM seat wraps and the ai-teacher seat wraps identically). Bundles the poison-aware
         *  lethal hint so its template is single-sourced. {@code oppLife}/{@code oppPoison} are the
         *  defender's life / poison counters; {@code anyInfect} = any legal attacker deals poison damage. */
        static String buildAttackersQuestion(String defenderName, int oppLife, int oppPoison, boolean anyInfect) {
            int poisonNeeded = Math.max(0, 10 - oppPoison);
            String lethalHint = "COUNT LETHAL: opp life " + oppLife + ", poison " + oppPoison + "/10 -- lethal needs "
                    + oppLife + " dmg OR " + poisonNeeded + " poison"
                    + (anyInfect ? " (infect attackers deal poison, not life loss)" : "")
                    + ". If your unblocked/unstoppable damage meets either number, attack with everything needed and WIN NOW.";
            return "COMBAT — declare attackers vs " + defenderName + " (life " + oppLife
                    + "). Pick which creatures attack (attacking with 0 is legal and sometimes correct — "
                    + "weigh their untapped mana and possible blocks). " + lethalHint;
        }

        /** The LLM seat's bespoke blockers prompt (BLOCKS: wire format, not a decideOne/Many template).
         *  {@code atk}/{@code blk} are the comma-joined attacker / blocker "Name (P/T)" lists both seats
         *  build identically. Shared so the ai-teacher's reconstruction can never drift. See
         *  {@link #declareBlockers} (LLM seat) and PlayerControllerAiLogging.declareBlockers. */
        static String buildBlockersPrompt(String snapshot, String atk, String blk) {
            return snapshot + "\nCOMBAT — declare blockers. Incoming: " + atk + ". Your blockers: " + blk + ". "
                    + "Assign blocks (each blocker blocks one attacker). Weigh your life total vs losing blockers. "
                    + "End with EXACTLY: BLOCKS: blocker->attacker, blocker->attacker   (or BLOCKS: NONE)";
        }

        /** Stable match key for a play-menu SpellAbility: {@code hostName|api|description}. Used by the
         *  ai-teacher seat to map Forge AI's returned SpellAbility back to a menu index when object
         *  identity does not hold (the AI enumerates its own SA instances). Duplicate-name / MDFC / modal
         *  cards can collide on this key — {@link PlayerControllerAiLogging#indexOfPlay} resolves that by
         *  taking the first not-yet-claimed slot and flags genuine ambiguity rather than guessing wrong. */
        static String playKey(SpellAbility sa) {
            if (sa == null) return "|null|";
            String host = "?";
            try { host = sa.getHostCard() != null ? sa.getHostCard().getName() : "?"; } catch (Exception ignore) { }
            String api = "?";
            try { api = sa.getApi() != null ? sa.getApi().toString() : "?"; } catch (Exception ignore) { }
            String desc = "";
            try { desc = sa.getDescription() == null ? "" : sa.getDescription().replaceAll("\\s+", " ").trim(); }
            catch (Exception ignore) { }
            return host + "|" + api + "|" + desc;
        }
    }

    /** LobbyPlayer whose in-game Player uses OUR controller, carrying seat + plan + models + arm. */
    static class LobbyPlayerLLM extends LobbyPlayerAi {
        private final String plan, smartModel, fastModel, mode;
        LobbyPlayerLLM(String name, String plan, String smartModel, String fastModel) {
            this(name, plan, smartModel, fastModel, "llm");
        }
        LobbyPlayerLLM(String name, String plan, String smartModel, String fastModel, String mode) {
            super(name, null); this.plan = plan; this.smartModel = smartModel; this.fastModel = fastModel; this.mode = mode;
        }
        @Override
        public Player createIngamePlayer(Game game, int id) {
            Player p = new Player(getName(), game, id);
            p.setFirstController(new PlayerControllerLLM(game, p, this, getName(), plan, smartModel, fastModel, mode));
            return p;
        }
    }

    /**
     * D0 — Forge-AI distillation seat. A sibling of {@link PlayerControllerLLM} that lets Forge's
     * built-in heuristic AI actually make each decision (via {@code super.*}) while logging that
     * decision in the SAME jsonl schema the LLM seat emits, tagged {@code tier="ai-teacher"}, so the
     * AI's argmax becomes an SFT label. Per method: (a) build the byte-format-identical option menu the
     * LLM seat would build (reusing {@code enumeratePlayOptions}/{@code buildPlayLabels}/{@code saLabel}/
     * {@code entityLabel}/{@code candidateLabel}); (b) call super for the AI's real choice; (c) map the
     * returned object / combat mutation back to a menu index (or COUNT, matching the LLM seat's own
     * per-type encoding); (d) write the row through {@link LlmSpikeGui#emitDecisionRow} with the FULL
     * templated prompt the LLM seat would have sent — reconstructed via the shared prompt-template
     * builders ({@code playPromptPrefix}/{@code buildDecideOnePrompt}/{@code buildDecideManyPrompt}),
     * so {@code prompt} = snapshot + the decision question + the numbered option menu, byte-identical to
     * the LLM seat's non-plan prompt for the same state. This is load-bearing: eval/process_metrics.py's
     * teacher-decision-match join hashes {@code prompt}+options, and make_sft_data builds the SFT
     * user-message from {@code prompt} — a bare board snapshot would never pair with a student row and
     * would strip the menu from the training example. For the void combat methods (declareAttackers /
     * declareBlockers) the prompt (incl. its snapshot) is captured BEFORE {@code super} so it reflects
     * pre-decision state, not the AI's own just-made choice. The ai seat carries no model, no intel, and
     * no turn plan, so it always uses the non-plan template and an empty-intel snapshot; against a
     * plan/intel-carrying LLM run those specific rows will not hash-pair (a documented coverage limit,
     * not a correctness bug). Game behavior is bit-identical to a plain {@code PlayerControllerAi} seat —
     * every override returns super's result unchanged and only reads the resulting state.
     *
     * <p>Invariant discipline: an ai-teacher row is emitted only for a GENUINE enumerable choice
     * ({@code n_options > 1}); forced single-option spots are skipped (no learning signal). Index-style
     * rows (ability-to-play, entity, zone-change, targets, number, card-name, mulligan) carry a choice in
     * {@code [0, n_options)}; COUNT-style rows (attackers, blockers, discard, sacrifice, modes) carry a
     * subset size in {@code [0, n_options]} — exactly the encoding the LLM seat logs. The play row is the
     * one special case: a PASS is logged as {@code choice = -1} (n_options still {@code = labels.size()}),
     * identical to the LLM seat's own play-PASS encoding ({@code chooseSpellAbilityToPlay}'s
     * {@code (choice>=1 && choice<passIdx) ? choice-1 : -1}), so that the two seats' PASS rows reconcile
     * in the decision-match join (process_metrics maps a play {@code choice==-1} to the same PASS token).
     * A choice that cannot be mapped into the menu (AI picked outside our capped enumeration, an
     * MDFC/duplicate-key collision with no free slot) is logged as a NON-enumerable diagnostic
     * ({@code n_options = -1}, choice {@code -1}) rather than a wrong index — never a degenerate
     * enumerable {@code choice = -1} on an index-style row.
     *
     * <p>Deferred (documented, low-value or fiddly): assignCombatDamage's map→menu reconciliation, the
     * bundled target/X sub-choices baked into a returned play SpellAbility (announceRequirements /
     * chooseTargetsFor beyond the single-target case), scry/surveil ordering, and the free-form
     * (predicate) chooseCardName variant.
     */
    static class PlayerControllerAiLogging extends PlayerControllerAi {
        private final String seat;
        private static final java.util.Collection<String> NO_INTEL = java.util.Collections.emptyList();

        PlayerControllerAiLogging(Game game, Player p, LobbyPlayer lp, String seat) {
            super(game, p, lp);
            this.seat = seat;
        }

        /** The game-state prompt the LLM seat would have sent for this decision (empty intel / no turn
         *  plan, which the AI seat never accumulates). */
        private String snap() {
            try { return PlayerControllerLLM.buildSnapshot(getPlayer(), seat, NO_INTEL, "", -1); }
            catch (Exception e) { return "== (state unavailable) =="; }
        }
        private void emit(String type, int nOptions, int choice, List<String> options, String prompt) {
            emit(type, nOptions, choice, null, options, prompt);
        }
        private void emit(String type, int nOptions, int choice, String fallback, List<String> options, String prompt) {
            try {
                emitDecisionRow(getPlayer(), seat, type, nOptions, choice, "ai-teacher", 0, fallback, options,
                        prompt, null, 0, 0, null, null, -1, -1);
            } catch (Exception ignore) { }
        }

        // ---------------- return-value decisions (map returned object -> index) ----------------

        @Override
        public boolean mulliganKeepHand(Player firstPlayer, int cardsToReturn) {
            // Reconstruct the LLM seat's bespoke mulligan prompt from the (unchanged-by-super) hand.
            String prompt = null;
            try {
                CardCollectionView hand = getPlayer().getCardsIn(ZoneType.Hand);
                int lands = CardLists.filter(hand, CardPredicates.LANDS).size();
                int size = hand.size();
                StringBuilder n = new StringBuilder();
                for (Card c : hand) { if (n.length() > 0) n.append(", "); n.append(c.getName()); }
                prompt = PlayerControllerLLM.buildMulliganPrompt(size, lands, n.toString());
            } catch (Exception ignore) { }
            boolean keep = super.mulliganKeepHand(firstPlayer, cardsToReturn);
            emit("mulligan", 2, keep ? 0 : 1, java.util.Arrays.asList("KEEP", "MULLIGAN"), prompt);
            return keep;
        }

        @Override
        public SpellAbility getAbilityToPlay(Card hostCard, List<SpellAbility> abilities, ITriggerEvent triggerEvent) {
            List<String> labels = null; String prompt = null;
            try {
                if (abilities != null && abilities.size() > 1) {
                    labels = new ArrayList<>();
                    for (SpellAbility sa : abilities) labels.add(PlayerControllerLLM.saLabel(sa));
                    String host = hostCard != null ? hostCard.getName() : "?";
                    String q = "Choose HOW to play " + host + " -- pick the specific cast mode / cost variant.";
                    prompt = PlayerControllerLLM.buildDecideOnePrompt(snap(), q, labels, null);
                }
            } catch (Exception ignore) { }
            SpellAbility r = super.getAbilityToPlay(hostCard, abilities, triggerEvent);
            try {
                if (labels != null) {
                    int idx = indexByIdentity(abilities, r);
                    if (idx < 0) idx = indexByKey(abilities, r);
                    if (idx >= 0) emit("ability-to-play", abilities.size(), idx, labels, prompt);
                    else emit("ability-to-play", -1, -1, "ai-choice-not-in-menu", labels, prompt);
                }
            } catch (Exception ignore) { }
            return r;
        }

        @Override
        public List<SpellAbility> chooseSpellAbilityToPlay() {
            // Enumerate + snapshot + build the play prompt BEFORE super, so the prompt reflects the true
            // pre-decision state (super only decides; the cast happens later in the engine).
            PlayerControllerLLM.PlayOptions po = null; List<String> labels = null; String prompt = null; boolean genuine = false;
            try {
                Player me = getPlayer();
                PhaseHandler phh = me.getGame().getPhaseHandler();
                boolean ownMain = phh.isPlayerTurn(me) && (phh.is(PhaseType.MAIN1) || phh.is(PhaseType.MAIN2));
                po = PlayerControllerLLM.enumeratePlayOptions(me, ownMain);
                // Only a genuine choice: at least one castable spell or land besides PASS. A window whose
                // only option is PASS carries no learning signal and would flood the log.
                if (!(po.spells.isEmpty() && po.landCards.isEmpty())) {
                    genuine = true;
                    labels = PlayerControllerLLM.buildPlayLabels(po.spells, po.landCards);
                    String menuStr = PlayerControllerLLM.numberedPlayMenu(labels);
                    prompt = PlayerControllerLLM.playPromptPrefix(snap(), phh.getPhase(), ownMain, menuStr)
                            + PlayerControllerLLM.PLAY_PROMPT_TAIL;
                }
            } catch (Exception ignore) { }
            List<SpellAbility> r = super.chooseSpellAbilityToPlay(); // the AI's real decision, returned unchanged
            try {
                if (genuine && labels != null) {
                    int choice;
                    if (r == null || r.isEmpty()) {
                        choice = -1; // AI passed priority — mirrors the LLM seat's play-PASS encoding (choice=-1)
                    } else {
                        choice = indexOfPlay(r.get(0), po);
                        if (choice < 0) { emit("play", -1, -1, "ai-choice-not-in-menu", labels, prompt); return r; }
                    }
                    // n_options = labels.size() (incl. the trailing PASS slot) == the LLM seat's passIdx.
                    emit("play", labels.size(), choice, labels, prompt);
                }
            } catch (Exception ignore) { }
            return r;
        }

        @Override
        public <T extends GameEntity> T chooseSingleEntityForEffect(FCollectionView<T> optionList, DelayedReveal delayedReveal,
                SpellAbility sa, String title, boolean isOptional, Player relatedPlayer, Map<String, Object> params) {
            List<T> opts = new ArrayList<>();
            try { if (optionList != null) for (T t : optionList) opts.add(t); } catch (Exception ignore) { }
            List<String> labels = null; String prompt = null;
            try {
                // delayedReveal != null is delegated (not routed) by the LLM seat -> no "entity" row there.
                if (opts.size() > 1 && delayedReveal == null) {
                    labels = new ArrayList<>();
                    for (T t : opts) labels.add(PlayerControllerLLM.entityLabel(t));
                    String q = (title == null || title.isEmpty() ? "Choose one" : title) + PlayerControllerLLM.srcOf(sa);
                    prompt = PlayerControllerLLM.buildDecideOnePrompt(snap(), q, labels, isOptional ? "Decline / none" : null);
                }
            } catch (Exception ignore) { }
            T r = super.chooseSingleEntityForEffect(optionList, delayedReveal, sa, title, isOptional, relatedPlayer, params);
            try {
                if (labels != null) {
                    int idx = -1;
                    for (int i = 0; i < opts.size(); i++) if (opts.get(i) == r) { idx = i; break; }
                    if (idx >= 0) emit("entity", opts.size(), idx, labels, prompt);
                    // r == null (isOptional decline) or unmatched -> no enumerable row (never choice=-1)
                }
            } catch (Exception ignore) { }
            return r;
        }

        @Override
        public Card chooseSingleCardForZoneChange(ZoneType destination, List<ZoneType> origin, SpellAbility sa,
                CardCollection fetchList, DelayedReveal delayedReveal, String selectPrompt, boolean isOptional, Player decider) {
            List<Card> opts = fetchList == null ? new ArrayList<>() : new ArrayList<>(fetchList);
            List<String> labels = null; String prompt = null;
            try {
                if (opts.size() > 1 && decider == getPlayer() && delayedReveal == null) {
                    labels = new ArrayList<>();
                    for (Card c : opts) labels.add(PlayerControllerLLM.entityLabel(c));
                    String q = (selectPrompt == null || selectPrompt.isEmpty() ? "Choose a card" : selectPrompt)
                            + " -> " + destination + PlayerControllerLLM.srcOf(sa)
                            + " (pick the card that best advances your game plan — e.g. reanimate your biggest threat).";
                    prompt = PlayerControllerLLM.buildDecideOnePrompt(snap(), q, labels, isOptional ? "Decline / none" : null);
                }
            } catch (Exception ignore) { }
            Card r = super.chooseSingleCardForZoneChange(destination, origin, sa, fetchList, delayedReveal, selectPrompt, isOptional, decider);
            try {
                if (labels != null) {
                    int idx = -1;
                    for (int i = 0; i < opts.size(); i++) if (opts.get(i) == r) { idx = i; break; }
                    if (idx >= 0) emit("zone-change", opts.size(), idx, labels, prompt);
                }
            } catch (Exception ignore) { }
            return r;
        }

        @Override
        public int chooseNumber(SpellAbility sa, String title, int min, int max) {
            List<String> opts = null; String prompt = null;
            try {
                if (max > min && (max - min) <= 12) {
                    opts = new ArrayList<>();
                    for (int i = min; i <= max; i++) opts.add(String.valueOf(i));
                    String q = (title == null ? "Choose a number" : title) + " (" + min + ".." + max + ")"
                            + PlayerControllerLLM.srcOf(sa);
                    prompt = PlayerControllerLLM.buildDecideOnePrompt(snap(), q, opts, null);
                }
            } catch (Exception ignore) { }
            int v = super.chooseNumber(sa, title, min, max);
            try {
                if (opts != null && v >= min && v <= max) emit("number", opts.size(), v - min, opts, prompt);
            } catch (Exception ignore) { }
            return v;
        }

        @Override
        public String chooseCardName(SpellAbility sa, List<ICardFace> faces, String message) {
            // Mirror the LLM seat's threshold: a numbered menu only for faces.size() <= 20 (above that the
            // LLM seat switches to a free-form path — out of scope here).
            List<String> labels = null; String prompt = null;
            try {
                if (faces != null && faces.size() > 1 && faces.size() <= 20) {
                    labels = new ArrayList<>();
                    for (ICardFace f : faces) labels.add(f.getName());
                    String q = "NAME A CARD" + PlayerControllerLLM.srcOf(sa) + ": " + oneLine(message);
                    prompt = PlayerControllerLLM.buildDecideOnePrompt(snap(), q, labels, null);
                }
            } catch (Exception ignore) { }
            String r = super.chooseCardName(sa, faces, message);
            try {
                if (labels != null && r != null) {
                    int idx = -1;
                    for (int i = 0; i < faces.size(); i++) if (r.equalsIgnoreCase(faces.get(i).getName())) { idx = i; break; }
                    if (idx >= 0) emit("card-name", faces.size(), idx, labels, prompt);
                }
            } catch (Exception ignore) { }
            return r;
        }

        // ---------------- target-setting (read sa.getTargets() after super) ----------------

        @Override
        public boolean chooseTargetsFor(SpellAbility sa) {
            List<GameEntity> cands = null; List<String> labels = null; String prompt = null;
            try {
                if (sa != null && sa.usesTargeting() && sa.getMaxTargets() == 1) {
                    boolean subTargets = false;
                    for (SpellAbility sub = sa.getSubAbility(); sub != null; sub = sub.getSubAbility())
                        if (sub.usesTargeting()) { subTargets = true; break; }
                    if (!subTargets) {
                        TargetRestrictions tr = sa.getTargetRestrictions();
                        if (tr != null) cands = tr.getAllCandidates(sa, false);
                    }
                }
                if (cands != null && cands.size() > 1) {
                    labels = new ArrayList<>();
                    for (GameEntity ge : cands) labels.add(PlayerControllerLLM.entityLabel(ge));
                    String q = "Choose the TARGET for " + PlayerControllerLLM.saLabel(sa)
                            + " (the spell/ability resolves against whatever you pick).";
                    prompt = PlayerControllerLLM.buildDecideOnePrompt(snap(), q, labels,
                            sa.getMinTargets() == 0 ? "No target (decline)" : null);
                }
            } catch (Exception ignore) { }
            boolean r = super.chooseTargetsFor(sa);
            try {
                if (labels != null) {
                    GameEntity chosen = null;
                    if (sa.getTargets() != null) {
                        for (Object o : sa.getTargets()) { if (o instanceof GameEntity) { chosen = (GameEntity) o; break; } }
                    }
                    if (chosen != null) {
                        int idx = -1;
                        for (int i = 0; i < cands.size(); i++) if (cands.get(i) == chosen) { idx = i; break; }
                        // Identity failure on a duplicate-name candidate -> SKIP rather than guess the first
                        // same-named index (matches indexOfPlay's discipline; per LOW review finding). In
                        // practice sa.getTargets() returns the live candidate object, so identity holds.
                        if (idx >= 0) emit("targets", cands.size(), idx, labels, prompt);
                    }
                }
            } catch (Exception ignore) { }
            return r;
        }

        // ---------------- COUNT-style subset decisions (choice = #selected, matches LLM seat) ----------

        @Override
        public void declareAttackers(Player attacker, Combat combat) {
            // Capture the menu + prompt BEFORE super so the snapshot reflects pre-combat state, not the
            // attackers the AI is about to declare (the void-method state-leak the review flagged).
            List<String> labels = null; int nLegal = 0; String prompt = null;
            try {
                Player defender = null;
                for (Player pl : combat.getDefendingPlayers()) { defender = pl; break; }
                if (defender != null) {
                    CardCollection legal = new CardCollection();
                    for (Card c : attacker.getCreaturesInPlay()) if (CombatUtil.canAttack(c, defender)) legal.add(c);
                    nLegal = legal.size();
                    if (nLegal > 1) {
                        labels = new ArrayList<>();
                        for (Card c : legal) labels.add(c.getName() + " (" + c.getNetPower() + "/" + c.getNetToughness() + ")");
                        int oppLife = defender.getLife();
                        int oppPoison = 0;
                        try { oppPoison = defender.getCounters(CounterEnumType.POISON); } catch (Exception ignore) { }
                        boolean anyInfect = false;
                        for (Card c : legal) { try { if (c.isInfectDamage(defender)) { anyInfect = true; break; } } catch (Exception ignore) { } }
                        // Shared question template (single source; byte-identical to the LLM seat's).
                        String q = PlayerControllerLLM.buildAttackersQuestion(defender.getName(), oppLife, oppPoison, anyInfect);
                        prompt = PlayerControllerLLM.buildDecideManyPrompt(snap(), q, labels, 0, nLegal);
                    }
                }
            } catch (Exception ignore) { }
            super.declareAttackers(attacker, combat);
            try {
                if (labels != null) {
                    CardCollection atks = combat.getAttackers();
                    int declared = atks == null ? 0 : atks.size();
                    if (declared > nLegal) declared = nLegal;
                    emit("attackers", nLegal, declared, labels, prompt);
                }
            } catch (Exception ignore) { }
        }

        @Override
        public void declareBlockers(Player defender, Combat combat) {
            // Capture the menu + bespoke blocker prompt BEFORE super (same void-method state-leak fix).
            List<String> labels = null; int nPool = 0; String prompt = null;
            try {
                CardCollection attackers = combat.getAttackers();
                if (attackers != null && !attackers.isEmpty()) {
                    CardCollection blockers = new CardCollection();
                    for (Card c : defender.getCreaturesInPlay())
                        for (Card atk : attackers) if (CombatUtil.canBlock(atk, c, combat)) { blockers.add(c); break; }
                    nPool = blockers.size();
                    if (nPool > 0) {
                        labels = new ArrayList<>();
                        StringBuilder blk = new StringBuilder();
                        for (Card b : blockers) {
                            String bl = b.getName() + " (" + b.getNetPower() + "/" + b.getNetToughness() + ")";
                            labels.add(bl);
                            if (blk.length() > 0) blk.append(", ");
                            blk.append(bl);
                        }
                        StringBuilder atk = new StringBuilder();
                        for (Card a : attackers) {
                            if (atk.length() > 0) atk.append(", ");
                            atk.append(a.getName()).append(" (").append(a.getNetPower()).append('/').append(a.getNetToughness()).append(')');
                        }
                        // Shared template (single source; byte-identical to the LLM seat's).
                        prompt = PlayerControllerLLM.buildBlockersPrompt(snap(), atk.toString(), blk.toString());
                    }
                }
            } catch (Exception ignore) { }
            super.declareBlockers(defender, combat);
            try {
                if (labels != null && nPool > 0) {
                    CardCollection all = combat.getAllBlockers();
                    int assigned = all == null ? 0 : all.size();
                    if (assigned > nPool) assigned = nPool;
                    emit("blockers", nPool, assigned, labels, prompt);
                }
            } catch (Exception ignore) { }
        }

        @Override
        public CardCollection chooseCardsToDiscardFrom(Player p, SpellAbility sa, CardCollection validCards,
                int min, int max, CardCollectionView visibleToChooser) {
            List<Card> opts = validCards == null ? new ArrayList<>() : new ArrayList<>(validCards);
            List<String> labels = null; String prompt = null;
            try {
                if (opts.size() > min) { // genuine choice (whole-hand-forced has no learning signal)
                    labels = new ArrayList<>();
                    for (Card c : opts) labels.add(PlayerControllerLLM.candidateLabel(c));
                    boolean stripping = p != getPlayer();
                    String q = stripping
                            ? "You are choosing which card(s) " + p.getName() + " must DISCARD from their revealed hand"
                              + PlayerControllerLLM.srcOf(sa) + ". Strip whatever most threatens your game plan (their removal/disruption/payoff)."
                            : "You must DISCARD from your hand" + PlayerControllerLLM.srcOf(sa) + ". Pick which card(s) to discard "
                              + "(remember your game plan — e.g. discarding a huge creature you can later reanimate is often correct).";
                    prompt = PlayerControllerLLM.buildDecideManyPrompt(snap(), q, labels, min, max);
                }
            } catch (Exception ignore) { }
            CardCollection r = super.chooseCardsToDiscardFrom(p, sa, validCards, min, max, visibleToChooser);
            try {
                if (labels != null) emit("discard", opts.size(), r == null ? 0 : r.size(), labels, prompt);
            } catch (Exception ignore) { }
            return r;
        }

        @Override
        public CardCollectionView choosePermanentsToSacrifice(SpellAbility sa, int min, int max,
                CardCollectionView validTargets, String message) {
            List<Card> opts = validTargets == null ? new ArrayList<>() : new ArrayList<>(validTargets);
            List<String> labels = null; String prompt = null;
            try {
                if (opts.size() > min) {
                    labels = new ArrayList<>();
                    for (Card c : opts) labels.add(PlayerControllerLLM.candidateLabel(c));
                    String q = "You must SACRIFICE " + (min == max ? String.valueOf(min) : min + "-" + max)
                            + " permanent(s)" + PlayerControllerLLM.srcOf(sa)
                            + (message == null || message.isEmpty() ? "" : ": " + oneLine(message))
                            + ". Choose what to give up — keep your best remaining threats/blockers/mana sources.";
                    prompt = PlayerControllerLLM.buildDecideManyPrompt(snap(), q, labels, min, max);
                }
            } catch (Exception ignore) { }
            CardCollectionView r = super.choosePermanentsToSacrifice(sa, min, max, validTargets, message);
            try {
                if (labels != null) emit("sacrifice", opts.size(), r == null ? 0 : r.size(), labels, prompt);
            } catch (Exception ignore) { }
            return r;
        }

        @Override
        public List<AbilitySub> chooseModeForAbility(SpellAbility sa, List<AbilitySub> possible, int min, int num, boolean allowRepeat) {
            List<String> labels = null; String prompt = null;
            try {
                if (possible != null && possible.size() > 1 && !allowRepeat) {
                    labels = new ArrayList<>();
                    for (AbilitySub ab : possible) labels.add(oneLine(ab.getDescription()));
                    String q = "MODAL SPELL — choose mode(s) for " + PlayerControllerLLM.saLabel(sa) + ".";
                    prompt = PlayerControllerLLM.buildDecideManyPrompt(snap(), q, labels,
                            Math.max(0, min), Math.min(num, possible.size()));
                }
            } catch (Exception ignore) { }
            List<AbilitySub> r = super.chooseModeForAbility(sa, possible, min, num, allowRepeat);
            try {
                if (labels != null) emit("modes", possible.size(), r == null ? 0 : r.size(), labels, prompt);
            } catch (Exception ignore) { }
            return r;
        }

        // ---------------- index-mapping helpers ----------------

        /** Map Forge AI's returned play SpellAbility back to a menu index over {@code po}: a land play
         *  matches its host land card by name (into the land block after the spells); a spell matches by
         *  object identity first, then by {@code hostName|api|description} key. Returns -1 when the AI's
         *  choice is not in the (capped) enumeration — the caller then logs a non-enumerable diagnostic,
         *  never a wrong index. */
        static int indexOfPlay(SpellAbility chosen, PlayerControllerLLM.PlayOptions po) {
            if (chosen == null) return -1;
            try {
                if (chosen.isLandAbility()) {
                    String name = chosen.getHostCard() != null ? chosen.getHostCard().getName() : null;
                    if (name != null)
                        for (int i = 0; i < po.landCards.size(); i++)
                            if (name.equals(po.landCards.get(i).getName())) return po.spells.size() + i;
                    return -1;
                }
            } catch (Exception ignore) { }
            int id = indexByIdentity(po.spells, chosen);
            if (id >= 0) return id;
            return indexByKey(po.spells, chosen);
        }
        static int indexByIdentity(List<SpellAbility> list, SpellAbility t) {
            if (t == null || list == null) return -1;
            for (int i = 0; i < list.size(); i++) if (list.get(i) == t) return i;
            return -1;
        }
        static int indexByKey(List<SpellAbility> list, SpellAbility t) {
            if (t == null || list == null) return -1;
            String key = PlayerControllerLLM.playKey(t);
            for (int i = 0; i < list.size(); i++) if (PlayerControllerLLM.playKey(list.get(i)).equals(key)) return i;
            return -1;
        }
    }

    /** LobbyPlayer whose in-game Player uses the AI-logging controller (CONTROLLER_A/B = "ai-log"). */
    static class LobbyPlayerAiLogging extends LobbyPlayerAi {
        LobbyPlayerAiLogging(String name) { super(name, null); }
        @Override
        public Player createIngamePlayer(Game game, int id) {
            Player p = new Player(getName(), game, id);
            p.setFirstController(new PlayerControllerAiLogging(game, p, this, getName()));
            return p;
        }
    }

    /** Map a CONTROLLER_A/B value to its LobbyPlayer: "ai" = plain Forge AI (untouched), "ai-log" = the
     *  Forge AI that logs every decision as an ai-teacher SFT row, anything else = the LLM seat / one of
     *  its baseline arms (llm|random|floor), which carries the plan + model specs + arm mode. */
    static LobbyPlayer seatFor(String ctl, String name, String plan, String smartModel, String fastModel) {
        if ("ai".equals(ctl)) return new LobbyPlayerAi(name, null);
        if ("ai-log".equals(ctl)) return new LobbyPlayerAiLogging(name);
        return new LobbyPlayerLLM(name, plan, smartModel, fastModel, ctl);
    }

    /** Shared seat construction for GUI + headless runners. Env:
     *  DECK_A/B, DECK_A_NAME/B_NAME, PLAN_A/B ("" = plan-free arm),
     *  SMART_MODEL_A/B + FAST_MODEL_A/B (fall back to SMART_MODEL/FAST_MODEL),
     *  CONTROLLER_A/B = llm|random|floor|ai, SWAP_SEATS=1 (paired-design seat flip). */
    static List<RegisteredPlayer> buildPlayers() {
        String home = System.getProperty("user.home");
        String pathA = System.getenv().getOrDefault("DECK_A", home + "/Downloads/Deck - Grixis Reanimator.txt");
        String pathB = System.getenv().getOrDefault("DECK_B", home + "/Downloads/Deck - Mardu Energy.txt");
        String nameA = System.getenv().getOrDefault("DECK_A_NAME", "Grixis Reanimator");
        String nameB = System.getenv().getOrDefault("DECK_B_NAME", "Mardu Energy");
        String smart = System.getenv().getOrDefault("SMART_MODEL", "opus");
        String fast  = System.getenv().getOrDefault("FAST_MODEL", "haiku");
        String smartA = System.getenv().getOrDefault("SMART_MODEL_A", smart);
        String smartB = System.getenv().getOrDefault("SMART_MODEL_B", smart);
        String fastA  = System.getenv().getOrDefault("FAST_MODEL_A", fast);
        String fastB  = System.getenv().getOrDefault("FAST_MODEL_B", fast);
        // Per-deck strategy resolution: an explicit PLAN_A/B env wins if SET (PLAN_A="" is the
        // deliberate plan-free cited arm); otherwise each deck's own <deck>.plan sidecar drives its
        // seat, so strategy generalizes per deck-identity instead of being hardcoded per seat.
        String planA = System.getenv("PLAN_A");
        if (planA == null) planA = loadPlanSidecar(pathA, DEFAULT_PLAN_A);
        String planB = System.getenv("PLAN_B");
        if (planB == null) planB = loadPlanSidecar(pathB, DEFAULT_PLAN_B);
        lastPlanA = planA; lastPlanB = planB;
        String ctlA = System.getenv().getOrDefault("CONTROLLER_A", "llm");
        String ctlB = System.getenv().getOrDefault("CONTROLLER_B", "llm");

        Deck deckA, deckB;
        try { deckA = loadDeck(nameA, pathA); deckB = loadDeck(nameB, pathB); }
        catch (Exception e) {
            // EVAL MODE (EVAL_OUT set): a silent random-deck substitution would poison an entire batch with
            // games that aren't the matchup we claim to be measuring. Fail the run loudly instead.
            String evalOut = System.getenv("EVAL_OUT");
            if (evalOut != null && !evalOut.isEmpty())
                throw new RuntimeException("[DECK] eval-mode deck load failed (EVAL_OUT set); refusing random-deck fallback: " + e, e);
            System.out.println("[DECK] failed to load decklists (" + e + ") -> random color decks");
            deckA = DeckgenUtil.getRandomColorDeck(true); deckB = DeckgenUtil.getRandomColorDeck(true);
        }

        RegisteredPlayer a = new RegisteredPlayer(deckA).setPlayer(seatFor(ctlA, nameA, planA, smartA, fastA));
        RegisteredPlayer b = new RegisteredPlayer(deckB).setPlayer(seatFor(ctlB, nameB, planB, smartB, fastB));
        boolean swap = "1".equals(System.getenv("SWAP_SEATS"));
        List<RegisteredPlayer> players = swap ? Lists.newArrayList(b, a) : Lists.newArrayList(a, b);
        System.out.println("[SEATS] A=" + nameA + "(" + ctlA + "," + smartA + ") B=" + nameB + "(" + ctlB + "," + smartB
                + ")" + (swap ? " [SWAPPED]" : ""));
        return players;
    }

    /**
     * Per-model infra defaults (max output tokens, timeout, OpenRouter "reasoning effort" hint,
     * temperature) keyed by a case-insensitive SUBSTRING match against the model id (e.g. "deepseek"
     * matches "deepseek/deepseek-v4-pro"). Evidence: HARNESS-ANALYSIS.md D1/F2 — one flat
     * 2048-tok/90s/1-retry config applied uniformly starves verbose reasoners (deepseek, gpt, kimi)
     * while being needlessly generous for terse ones, cleanly splitting the field into models that
     * never fail and models that fail constantly on IDENTICAL infra. `fastMaxTokens`/`fastTimeoutMs`
     * are the separate, smaller caps the "fast" tier (confirms/numbers/cleanup) uses; they default to
     * the same flat value for every row, but a row MAY override them (see deepseekGpt below) when
     * evidence shows the flat default starves that model even on the fast tier.
     *
     * Investigation (2026-07-21, gpt-5/deepseek model-sweep games): every finish_reason=length ->
     * bridge-down conversion for both models (8 for gpt-5, 10 for deepseek — matching sweep-verdicts.json
     * exactly) was on the FAST tier (type=confirm, type=play), NEVER the smart tier (0 non-answers there
     * across both games' full logs). The OpenRouterBridge.ask() [TRUNCATED->RETRY] doubling path never
     * fired even once (grep the .log: zero occurrences) despite 8-10 finish_reason=length events per
     * game, because isNonAnswer(r.content) runs BEFORE the truncation-retry check and a fully-empty
     * `content` (finish_reason=length with the reasoning budget exhausted before any output token) is
     * indistinguishable from a plain non-answer — it short-circuits to bridge-down first, so the retry
     * was dead code for exactly the case it exists to handle. That ordering WAS an architecture bug,
     * out of scope for a config-table-only fix at the time this note was written; it has since been
     * fixed directly in OpenRouterBridge.ask() (the finish_reason=="length" truncation-retry check now
     * runs BEFORE isNonAnswer()) — this table's per-model headroom values remain independently useful
     * (they widen the budget the retry doubles from) but are no longer the only lever. What IS fixable
     * purely from this table: the fast tier's request never sends a
     * `reasoning` hint at all for ANY model (OpenRouterBridge.ask() unconditionally nulls it out for
     * fastTier — "the fast tier's contract is terse, no extended reasoning"), yet gpt-5/deepseek are
     * native reasoning models that still spend hidden reasoning tokens server-side by default even
     * with no explicit reasoning param. At the flat DEFAULT_FAST_MAX_TOKENS=512 that hidden reasoning
     * alone exhausts the whole budget, leaving zero tokens for the one-line CHOICE/PICKS answer, every
     * time. kimi (no reasoning-effort row value either) and grok show far fewer/zero such fast-tier
     * failures at the same flat 512 cap, so the fix is model-specific: give ONLY the reasoning-heavy
     * deepseek/gpt row a much larger fastMaxTokens (enough headroom to survive the hidden reasoning
     * burn and still emit the final line) and a longer fastTimeoutMs to match the larger generation.
     * Reasoning-effort tuning was the other evidence-supported option, but is not reachable from this
     * table alone: fast-tier calls null out reasoningEffort unconditionally regardless of what a
     * ModelCfg row sets, so "set effort low for fast-tier decisions" would require touching
     * OpenRouterBridge.ask() itself — an architecture change explicitly out of scope for this pass.
     *
     * Env OPENROUTER_MODEL_CFG (JSON) can add rows or override individual fields on existing ones
     * without a rebuild, e.g. {@code {"mistral":{"maxTokens":6000},"deepseek":{"timeoutMs":150000}}}.
     * A malformed value logs once and the built-in table below is used untouched — additive override,
     * never a hard replacement.
     */
    static final class ModelCfg {
        static final int DEFAULT_FAST_MAX_TOKENS = 512;
        static final long DEFAULT_FAST_TIMEOUT_MS = 30000;

        final int maxTokens;
        final long timeoutMs;
        final String reasoningEffort; // nullable — smart tier only
        final Double temperature;     // nullable — omitted from the request body when null
        final int fastMaxTokens;
        final long fastTimeoutMs;

        ModelCfg(int maxTokens, long timeoutMs, String reasoningEffort, Double temperature,
                 int fastMaxTokens, long fastTimeoutMs) {
            this.maxTokens = maxTokens;
            this.timeoutMs = timeoutMs;
            this.reasoningEffort = reasoningEffort;
            this.temperature = temperature;
            this.fastMaxTokens = fastMaxTokens;
            this.fastTimeoutMs = fastTimeoutMs;
        }

        private static final Map<String, ModelCfg> TABLE = buildTable();

        private static Map<String, ModelCfg> buildTable() {
            Map<String, ModelCfg> t = new java.util.LinkedHashMap<>();
            // Evidence-grounded starting values (HARNESS-ANALYSIS.md §5 Infra-1): deepseek/gpt hit the
            // 2048-token cap hard and need reasoning headroom; kimi's own median latency already
            // exceeds the flat 90s deadline; grok needs more room than the default but not as much as
            // the worst reasoners; everything else keeps the original flat config.
            // fastMaxTokens=4000/fastTimeoutMs=60000 (vs. the flat 512/30000 default): gpt-5 and deepseek
            // are native reasoning models that burn hidden reasoning tokens on the FAST tier too, even
            // though the fast-tier request never sends a `reasoning` hint — at 512 tokens that reasoning
            // alone ate the whole budget every time (8/8 gpt-5 and 10/10 deepseek finish_reason=length
            // fast-tier events came back with EMPTY content), so no room was ever left for the one-line
            // CHOICE/PICKS answer. See the class javadoc above for the full investigation.
            ModelCfg deepseekGpt = new ModelCfg(16000, 90000, "medium", null, 4000, 60000);
            ModelCfg kimi        = new ModelCfg(16000, 150000, null, null, ModelCfg.DEFAULT_FAST_MAX_TOKENS, ModelCfg.DEFAULT_FAST_TIMEOUT_MS);
            ModelCfg grok        = new ModelCfg(8000, 90000, null, null, ModelCfg.DEFAULT_FAST_MAX_TOKENS, ModelCfg.DEFAULT_FAST_TIMEOUT_MS);
            ModelCfg dflt        = new ModelCfg(4000, 90000, null, null, ModelCfg.DEFAULT_FAST_MAX_TOKENS, ModelCfg.DEFAULT_FAST_TIMEOUT_MS);
            t.put("deepseek", deepseekGpt);
            t.put("gpt", deepseekGpt);
            t.put("kimi", kimi);
            t.put("grok", grok);
            t.put("default", dflt);
            try { applyEnvOverride(t); }
            catch (Exception e) { System.out.println("[MODEL-CFG] failed to parse OPENROUTER_MODEL_CFG, using built-in defaults: " + e); }
            return t;
        }

        /** Minimal hand-rolled parse (matches this file's existing extractJsonString style — no JSON
         *  library dependency) for a flat {@code {"key": {"field":value, ...}, ...}} shape. */
        private static void applyEnvOverride(Map<String, ModelCfg> t) {
            String json = System.getenv("OPENROUTER_MODEL_CFG");
            if (json == null || json.isEmpty()) return;
            Matcher rows = Pattern.compile("\"([^\"]+)\"\\s*:\\s*\\{([^}]*)\\}").matcher(json);
            while (rows.find()) {
                String key = rows.group(1).toLowerCase(Locale.ROOT);
                String fields = rows.group(2);
                ModelCfg base = t.containsKey(key) ? t.get(key) : t.get("default");
                ModelCfg merged = new ModelCfg(
                        intField(fields, "maxTokens", base.maxTokens),
                        longField(fields, "timeoutMs", base.timeoutMs),
                        strField(fields, "reasoningEffort", base.reasoningEffort),
                        doubleField(fields, "temperature", base.temperature),
                        intField(fields, "fastMaxTokens", base.fastMaxTokens),
                        longField(fields, "fastTimeoutMs", base.fastTimeoutMs));
                t.put(key, merged);
                System.out.println("[MODEL-CFG] override applied for \"" + key + "\" from OPENROUTER_MODEL_CFG");
            }
        }
        private static int intField(String body, String name, int def) {
            Matcher m = Pattern.compile("\"" + name + "\"\\s*:\\s*(\\d+)").matcher(body);
            return m.find() ? Integer.parseInt(m.group(1)) : def;
        }
        private static long longField(String body, String name, long def) {
            Matcher m = Pattern.compile("\"" + name + "\"\\s*:\\s*(\\d+)").matcher(body);
            return m.find() ? Long.parseLong(m.group(1)) : def;
        }
        private static String strField(String body, String name, String def) {
            Matcher m = Pattern.compile("\"" + name + "\"\\s*:\\s*\"([^\"]*)\"").matcher(body);
            return m.find() ? m.group(1) : def;
        }
        private static Double doubleField(String body, String name, Double def) {
            Matcher m = Pattern.compile("\"" + name + "\"\\s*:\\s*([0-9.]+)").matcher(body);
            return m.find() ? Double.valueOf(m.group(1)) : def;
        }

        /** Case-insensitive substring match against the model id; falls back to the "default" row. */
        static ModelCfg forModel(String modelId) {
            if (modelId != null) {
                String m = modelId.toLowerCase(Locale.ROOT);
                for (Map.Entry<String, ModelCfg> e : TABLE.entrySet()) {
                    if (!"default".equals(e.getKey()) && m.contains(e.getKey())) return e.getValue();
                }
            }
            return TABLE.get("default");
        }

        /** Largest per-request timeout configured for any known model, either tier (e.g. 150s for
         *  kimi's smart tier). Used by LlmSpikeHeadless to size its post-wall-clock-cap shutdown
         *  grace window: a "-> asking" line is logged BEFORE the blocking HTTP call, so if the cap
         *  fires while that call is still in flight and the grace window is shorter than the call's
         *  own timeout, System.exit() kills the JVM before the reply lands and PlayerControllerLLM
         *  .evalDec() ever runs for it — a real ask the log claims happened with no jsonl row to
         *  match, i.e. exactly the defect_analyzer.py silent-drop reconciliation gap. Does NOT
         *  account for the flat OPENROUTER_TIMEOUT_MS / CLAUDE_DEADLINE_MS operator overrides
         *  (out of scope here); a run using one of those above this value can still race the cap. */
        static long maxConfiguredTimeoutMs() {
            long max = 0;
            for (ModelCfg c : TABLE.values()) max = Math.max(max, Math.max(c.timeoutMs, c.fastTimeoutMs));
            return max;
        }
    }

    /**
     * PERSISTENT per-(seat,model) bridge to Claude via the CLI stream-json mode. One long-lived
     * `claude -p --model <model> ...` process per instance, its own conversation (chat memory).
     * A one-time `preamble` (persona + deck plan + output contract) is prepended to the first ask.
     */
    static final class ClaudeBridge implements LLMBridge {
        private static final String BIN = System.getenv().getOrDefault("CLAUDE_BIN", "claude");
        private static final String DEADLINE_ENV = System.getenv("CLAUDE_DEADLINE_MS");
        private static final long DEADLINE_MS = parseLong(DEADLINE_ENV, 90000);
        // SPEED: the persistent conversation's prefill grows with every ask, so calls get slower as the
        // game goes on. Restart the CLI session every N asks (re-priming the preamble); the per-message
        // snapshot + the controller's INTEL line carry the state that matters across the reset.
        private static final int RESET_EVERY = (int) parseLong(System.getenv("CLAUDE_RESET_EVERY"), 40);
        private static long parseLong(String v, long def) { try { return v == null ? def : Long.parseLong(v.trim()); } catch (Exception e) { return def; } }
        static long deadlineMs() { return DEADLINE_MS; } // in-JVM baseline value for the game-summary provenance line
        private final String label;
        private final String model;
        private final String preamble;
        // CLAUDE_DEADLINE_MS, if set, is an explicit operator override that pins EVERY model to the
        // same deadline (matches prior behavior exactly); otherwise each instance gets its own
        // per-model default from ModelCfg (e.g. a slower-reasoning model gets more time) — Infra-1.
        private final long deadlineMs;
        private boolean primed = false;
        private Process proc;
        private BufferedReader out;
        private BufferedWriter in;
        private int asksSinceStart = 0;
        private long totalMs = 0; private int calls = 0; // latency accounting, read by [TALLY]

        ClaudeBridge(String label, String model, String preamble) {
            this.label = label;
            this.model = model; // per-seat/per-tier; no global CLAUDE_MODEL clobber (it broke A/B comparisons)
            this.preamble = preamble;
            this.deadlineMs = (DEADLINE_ENV != null && !DEADLINE_ENV.isEmpty()) ? DEADLINE_MS : ModelCfg.forModel(model).timeoutMs;
        }

        private void ensureStarted() throws Exception {
            if (proc != null && proc.isAlive()) return;
            System.out.println("[CLAUDE " + label + "] starting persistent " + model + " session...");
            // --strict-mcp-config: don't load the user's MCP servers into the bridge session — they add
            // startup time and system-prompt tokens that get re-processed on EVERY call, for zero benefit.
            ProcessBuilder pb = new ProcessBuilder(BIN, "-p", "--model", model, "--strict-mcp-config",
                    "--input-format", "stream-json", "--output-format", "stream-json", "--verbose");
            pb.redirectErrorStream(false);
            proc = pb.start();
            out = new BufferedReader(new InputStreamReader(proc.getInputStream(), StandardCharsets.UTF_8));
            in  = new BufferedWriter(new OutputStreamWriter(proc.getOutputStream(), StandardCharsets.UTF_8));
            primed = false;
            final Process fproc = proc;
            Thread errDrain = new Thread(() -> {
                try (BufferedReader er = new BufferedReader(new InputStreamReader(fproc.getErrorStream(), StandardCharsets.UTF_8))) {
                    while (er.readLine() != null) { }
                } catch (Exception ignore) { }
            }, "claude-stderr-" + label);
            errDrain.setDaemon(true);
            errDrain.start();
        }

        @Override public synchronized String ask(String prompt) {
            long t0 = System.currentTimeMillis();
            try {
                if (RESET_EVERY > 0 && asksSinceStart >= RESET_EVERY) {
                    System.out.println("[CLAUDE " + label + "] refreshing session after " + asksSinceStart + " asks (bounds context growth)");
                    reset();
                }
                ensureStarted();
                asksSinceStart++;
                String full = (!primed && preamble != null) ? (preamble + "\n\n" + prompt) : prompt;
                primed = true;
                in.write("{\"type\":\"user\",\"message\":{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\""
                        + jsonEscape(full) + "\"}]}}");
                in.write("\n"); in.flush();
                long deadline = System.currentTimeMillis() + deadlineMs;
                while (System.currentTimeMillis() < deadline) {
                    if (!out.ready()) {
                        if (!proc.isAlive()) { System.out.println("[CLAUDE " + label + "] process exited"); reset(); return null; }
                        Thread.sleep(20); continue;
                    }
                    String line = out.readLine();
                    if (line == null) { reset(); return null; }
                    if (line.contains("\"type\":\"result\"")) {
                        String r = extractJsonString(line, "\"result\":\"");
                        if (isNonAnswer(r)) {
                            System.out.println("[CLAUDE " + label + "] non-answer result -> treating as bridge-down");
                            return null; // caller's retry/fallback path will log an honest bridge-down reason
                        }
                        long dur = System.currentTimeMillis() - t0;
                        totalMs += dur; calls++;
                        System.out.println("[CLAUDE " + label + "] replied in " + dur + " ms");
                        return r;
                    }
                }
                System.out.println("[CLAUDE " + label + "] timed out"); return null;
            } catch (Exception e) {
                System.out.println("[CLAUDE " + label + "] error: " + e); reset(); return null;
            }
        }

        /** Force a fresh CLI session (controller's one-shot dead-bridge retry). Necessary because
         *  ask()'s TIMEOUT path deliberately does NOT reset() — the process stays alive with a reply
         *  possibly still coming, so re-asking that same session could read the stale prior result
         *  line. The next ask() after this respawns the process and re-primes the preamble. */
        @Override public synchronized void restart() { reset(); }

        @Override public String getLabel() { return label; }
        @Override public String getModel() { return model; }
        @Override public long getTotalMs() { return totalMs; }
        @Override public int getCalls() { return calls; }

        private void reset() {
            try { if (proc != null) proc.destroyForcibly(); } catch (Exception ignore) { }
            proc = null; out = null; in = null; asksSinceStart = 0;
        }
    }

    /** Cut at the last whitespace boundary at-or-before `max` chars and append "..." — never a raw
     *  mid-word/mid-number cut (C4: a hard substring(0,N) silently dropped load-bearing clauses, e.g.
     *  severing a pump spell's own "+4/+4 if it's your main phase" text mid-digit). A no-op when `s`
     *  already fits. */
    static String truncateWords(String s, int max) {
        if (s == null || s.length() <= max) return s;
        int cut = s.lastIndexOf(' ', max);
        if (cut <= 0) cut = max; // no whitespace to break on -> fall back to a hard cut rather than emit nothing
        return s.substring(0, cut).stripTrailing() + "...";
    }

    /** Collapse whitespace and truncate (word-boundary + ellipsis) for compact logging. */
    private static String oneLine(String s) {
        if (s == null) return "";
        s = s.replaceAll("\\s+", " ").trim();
        return truncateWords(s, 90);
    }

    /** Single JSONL decision-row writer, shared by the LLM seat's {@code evalDec} and the ai-teacher
     *  seat's {@code emit} so both emit byte-format-identical rows (same keys, same order). The LLM seat
     *  passes its captured per-decision model provenance (prompt/raw_reply/retry_ms/attempts/…); the
     *  ai-teacher seat, which never consults a model, passes prompt = the snapshot the LLM seat would
     *  have sent, raw_reply = null, and the model-only fields as null/-1/0. */
    /** Legacy 18-arg form (ai-teacher seat + any un-migrated caller): no shadow label, logs shadow_choice=null. */
    static void emitDecisionRow(Player pl, String seat, String type, int nOptions, int choice, String tier,
            long ms, String fallback, List<String> options, String prompt, String rawReply,
            long retryMs, int attempts, String finishReason, String firstFailedReply,
            int promptTokens, int completionTokens) {
        emitDecisionRow(pl, seat, type, nOptions, choice, tier, ms, fallback, options, prompt, rawReply,
                retryMs, attempts, finishReason, firstFailedReply, promptTokens, completionTokens, null);
    }
    static void emitDecisionRow(Player pl, String seat, String type, int nOptions, int choice, String tier,
            long ms, String fallback, List<String> options, String prompt, String rawReply,
            long retryMs, int attempts, String finishReason, String firstFailedReply,
            int promptTokens, int completionTokens, Integer shadowChoice) {
        if (!EvalLog.on()) return;
        try {
            PhaseHandler ph = pl.getGame().getPhaseHandler();
            StringBuilder sb = new StringBuilder(256);
            sb.append("{\"t\":\"decision\",\"seat\":\"").append(jsonEscape(seat))
                    .append("\",\"turn\":").append(ph.getTurn()).append(",\"phase\":\"").append(ph.getPhase())
                    .append("\",\"type\":\"").append(type).append("\",\"n_options\":").append(nOptions)
                    .append(",\"choice\":").append(choice)
                    .append(",\"tier\":\"").append(tier).append("\",\"latency_ms\":").append(ms)
                    .append(",\"fallback\":")
                    .append(fallback == null ? "null" : "\"" + jsonEscape(fallback) + "\"")
                    .append(",\"options\":[");
            if (options != null) {
                for (int i = 0; i < options.size(); i++) {
                    if (i > 0) sb.append(',');
                    sb.append('"').append(jsonEscape(options.get(i))).append('"');
                }
            }
            sb.append("],\"prompt\":")
                    .append(prompt == null ? "null" : "\"" + jsonEscape(prompt) + "\"")
                    .append(",\"raw_reply\":")
                    .append(rawReply == null ? "null" : "\"" + jsonEscape(rawReply) + "\"")
                    .append(",\"retry_ms\":").append(retryMs)
                    .append(",\"attempts\":").append(attempts)
                    .append(",\"finish_reason\":")
                    .append(finishReason == null ? "null" : "\"" + jsonEscape(finishReason) + "\"")
                    .append(",\"first_failed_reply\":")
                    .append(firstFailedReply == null ? "null" : "\"" + jsonEscape(firstFailedReply) + "\"")
                    .append(",\"prompt_tokens\":").append(promptTokens < 0 ? "null" : promptTokens)
                    .append(",\"completion_tokens\":").append(completionTokens < 0 ? "null" : completionTokens)
                    // D4 DAgger additive field: the Forge-AI's shadow label over this row's menu, or null.
                    .append(",\"shadow_choice\":").append(shadowChoice == null ? "null" : shadowChoice)
                    .append('}');
            EvalLog.write(sb.toString());
        } catch (Exception ignore) { }
    }

    /** JSON string escape (minimal, no Unicode escapes above U+001F). */
    static String jsonEscape(String s) {
        StringBuilder b = new StringBuilder(s.length() + 16);
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"':  b.append("\\\""); break;
                case '\\': b.append("\\\\"); break;
                case '\n': b.append("\\n"); break;
                case '\r': b.append("\\r"); break;
                case '\t': b.append("\\t"); break;
                default: if (c < 0x20) b.append(String.format("\\u%04x", (int) c)); else b.append(c);
            }
        }
        return b.toString();
    }

    /** Extract the string value after `key` in a JSON-like line, honoring simple backslash escapes.
     *  Not a full parser — sufficient for the single-string fields we care about. When the key ends
     *  with the value's opening quote (e.g. {@code "content":"}), whitespace between the colon and
     *  the quote is tolerated: OpenRouter emits compact JSON but OpenAI-compatible local servers
     *  (mlx_lm.server / Python json.dumps) emit {@code "content": "..."} with a space, which the old
     *  exact-substring match silently missed — every local-server reply then read as content=null →
     *  non-answer → bridge-down (found 2026-07-22: the stage-1 student eval played 30 games with the
     *  model never consulted once). A non-string value after the key (e.g. {@code "content": null})
     *  returns null, which is the honest bridge-down path. */
    static String extractJsonString(String line, String key) {
        boolean quoted = key.endsWith(":\"");
        String bare = quoted ? key.substring(0, key.length() - 1) : key;
        int k = line.indexOf(bare);
        if (k < 0) return null;
        int i = k + bare.length();
        if (quoted) {
            while (i < line.length() && Character.isWhitespace(line.charAt(i))) i++;
            if (i >= line.length() || line.charAt(i) != '"') return null;
            i++;
        }
        StringBuilder b = new StringBuilder();
        while (i < line.length()) {
            char c = line.charAt(i++);
            if (c == '\\' && i < line.length()) {
                char n = line.charAt(i++);
                switch (n) {
                    case 'n': b.append('\n'); break;
                    case 'r': b.append('\r'); break;
                    case 't': b.append('\t'); break;
                    case '"': b.append('"'); break;
                    case '\\': b.append('\\'); break;
                    case '/': b.append('/'); break;
                    case 'u': if (i + 4 <= line.length()) { b.append((char) Integer.parseInt(line.substring(i, i + 4), 16)); i += 4; } break;
                    default: b.append(n);
                }
            } else if (c == '"') break;
            else b.append(c);
        }
        return b.toString();
    }

    /** Minimal integer extractor for a JSON number field (e.g. {@code "prompt_tokens":123}) — the
     *  numeric analogue of {@link #extractJsonString}, matching this file's dependency-free parse
     *  style. Returns -1 when the key is absent or the value isn't an integer. Used for the response
     *  usage token counts. */
    static int extractJsonInt(String line, String key) {
        if (line == null) return -1;
        int k = line.indexOf(key);
        if (k < 0) return -1;
        int i = k + key.length();
        while (i < line.length() && Character.isWhitespace(line.charAt(i))) i++;
        int start = i;
        if (i < line.length() && line.charAt(i) == '-') i++;
        while (i < line.length() && Character.isDigit(line.charAt(i))) i++;
        if (i == start || (i == start + 1 && line.charAt(start) == '-')) return -1;
        try { return Integer.parseInt(line.substring(start, i)); } catch (Exception e) { return -1; }
    }

    /** True if the backend returned a null/empty string or a known non-answer (rate-limit message,
     *  auth error, etc.). These must NOT be parsed as model decisions; returning null routes them
     *  through the honest bridge-down fallback path instead of logging a fake smart/fast decision.
     *  Keep the pattern list minimal and literal to avoid false positives. Package-private (not
     *  private) so LlmSpikeTest can exercise it directly -- see OpenRouterBridge.ask() for why this
     *  must run AFTER the finish_reason=="length" truncation-retry check, not before. */
    static boolean isNonAnswer(String r) {
        if (r == null || r.isEmpty()) return true;
        String u = r.toUpperCase(Locale.ROOT);
        if (u.contains("YOU'VE HIT YOUR SESSION LIMIT")) return true;
        if (u.contains("RATE LIMIT") || u.contains("RATE-LIMIT")) return true;
        if (u.contains("UNABLE TO LOCATE A JAVA RUNTIME")) return true; // env/launch failure, not a Magic decision
        return false;
    }

    /** Heuristic: does this reply already contain one of the decision-tag keywords the controller's
     *  parsers look for (CHOICE/PICKS/BLOCKS/KEEP/MULLIGAN/ANSWER)? Used only to decide whether a
     *  finish_reason=="length" truncation is worth an expensive retry — if the tag already made it
     *  out before the model ran out of budget, the truncation was harmless and not worth re-asking.
     *  The four numeric tags (CHOICE/PICKS/BLOCKS/ANSWER) require an actual digit somewhere after the
     *  keyword, not just the bare word: PARSE RE-ASK INVESTIGATION (runs/improve/iter-2, game-112
     *  decision idx 124) found a real fast-tier reply truncated to "...CHOICE: " with NO digit yet
     *  written. The old bare-substring check still matched "CHOICE" and reported "already answered",
     *  which skipped the doubled-budget truncation retry for exactly the reply that most needed it and
     *  pushed the cost onto a full controller-level re-ask instead. KEEP/MULLIGAN carry no numeric
     *  value (the word itself is the whole answer) so they keep the plain substring check. */
    static boolean looksLikeDecisionTag(String content) {
        if (content == null) return false;
        String u = content.toUpperCase(Locale.ROOT);
        if (u.contains("KEEP") || u.contains("MULLIGAN")) return true;
        for (String tag : new String[]{"CHOICE", "PICKS:", "BLOCKS:", "ANSWER:"}) {
            int i = u.indexOf(tag);
            if (i >= 0 && Pattern.compile("\\d").matcher(u.substring(i)).find()) return true;
        }
        return false;
    }

    /** Cheap degenerate-generation guard (F2 residual; evidence: tournament QF-1 decision_line 87, a
     *  fast-tier gemini reply that degenerated into 157 consecutive repetitions of "CHOICE: 1", 2390
     *  chars). The reply still parsed correctly (parseTagNumber takes the LAST occurrence) so this is
     *  purely a cost/latency guard, not a correctness fix. Collapses any run of MORE THAN 10
     *  consecutive identical lines that are each <=20 chars down to a single occurrence, so a stuck
     *  model can't burn its whole token budget (and bloat the jsonl raw_reply field) repeating one
     *  line. Logs [DEGENERATE-REPLY] once per call (a summary, not once per removed line) so a run
     *  full of degenerate replies doesn't itself spam the console.
     *  No stop-sequence was added to the OpenRouter request body to complement this: the
     *  repeated text is whatever tag the model just emitted (CHOICE/PICKS/BLOCKS/...), which differs
     *  per call site, so there is no single literal stop string that reliably catches "the model is
     *  about to repeat itself" without also risking clipping a legitimate first occurrence of that
     *  same tag (exactly the "\nCHOICE:" trap called out for this task) -- this collapse is the safer,
     *  request-shape-independent fix. */
    static String collapseDegenerateRepetition(String reply) {
        if (reply == null || reply.indexOf('\n') < 0) return reply;
        String[] lines = reply.split("\n", -1);
        StringBuilder out = new StringBuilder(reply.length());
        int collapsedRuns = 0, collapsedLines = 0;
        int i = 0;
        while (i < lines.length) {
            String line = lines[i];
            int runEnd = i + 1;
            if (line.length() <= 20) {
                while (runEnd < lines.length && lines[runEnd].equals(line)) runEnd++;
            }
            int runLen = runEnd - i;
            if (out.length() > 0) out.append('\n');
            out.append(line);
            if (runLen > 10) { // "repeated more than 10 times" -- run length (occurrence count) > 10
                collapsedRuns++;
                collapsedLines += runLen - 1;
            }
            i = runEnd;
        }
        if (collapsedRuns == 0) return reply;
        System.out.println("[DEGENERATE-REPLY] collapsed " + collapsedLines + " repeated line(s) across "
                + collapsedRuns + " run(s), " + reply.length() + " -> " + out.length() + " chars");
        return out.toString();
    }

    /** Stateless HTTP bridge to any model reachable through OpenRouter (or any
     *  OpenAI-compatible chat-completions endpoint). Each ask sends the preamble
     *  as a system message and the full snapshot as a user message — no conversation
     *  history is maintained because the controller's prompt already contains the
     *  entire game state. */
    static final class OpenRouterBridge implements LLMBridge {
        private static final String API_KEY = System.getenv("OPENROUTER_API_KEY");
        private static final String BASE_URL = System.getenv().getOrDefault("OPENROUTER_BASE_URL",
                "https://openrouter.ai/api/v1");
        // Explicit operator override: when set, pins EVERY model's timeout to this one value
        // (matches prior behavior exactly). Unset -> each bridge uses its own per-tier/per-model
        // default from ModelCfg instead of one flat number — Infra-1/Infra-2.
        private static final Long TIMEOUT_MS_ENV = parseLongOrNull(System.getenv("OPENROUTER_TIMEOUT_MS"));
        private static final String HTTP_REFERER = System.getenv("OPENROUTER_HTTP_REFERER");
        private static final String HTTP_TITLE = System.getenv("OPENROUTER_HTTP_TITLE");
        private static Long parseLongOrNull(String v) {
            try { return v == null ? null : Long.parseLong(v.trim()); } catch (Exception e) { return null; }
        }

        private final String label;
        private final String model;
        private final String preamble;
        private final HttpClient client;
        private final ModelCfg cfg;
        private final boolean fastTier; // label is always "<seat>/smart" or "<seat>/fast" — see PlayerControllerLLM ctor
        private final long timeoutMs;
        private long totalMs = 0;
        private int calls = 0;
        private volatile String lastFinishReason;
        private volatile int lastPromptTokens = -1;
        private volatile int lastCompletionTokens = -1;

        OpenRouterBridge(String label, String model, String preamble) {
            if (API_KEY == null || API_KEY.isEmpty())
                throw new IllegalStateException("OPENROUTER_API_KEY env var is required for OpenRouter backend");
            this.label = label;
            this.model = model;
            this.preamble = preamble;
            this.fastTier = label != null && label.endsWith("/fast");
            this.cfg = ModelCfg.forModel(model);
            long base = fastTier ? cfg.fastTimeoutMs : cfg.timeoutMs;
            this.timeoutMs = TIMEOUT_MS_ENV != null ? TIMEOUT_MS_ENV : base;
            this.client = HttpClient.newBuilder()
                    .connectTimeout(Duration.ofMillis(timeoutMs))
                    .build();
        }

        @Override public String ask(String prompt) {
            long t0 = System.currentTimeMillis();
            try {
                int maxTokens = fastTier ? cfg.fastMaxTokens : cfg.maxTokens;
                // reasoning_effort/temperature are smart-tier-only hints — the fast tier's contract is
                // "terse, no extended reasoning" (see framing()), so never ask a model to reason harder there.
                String reasoningEffort = fastTier ? null : cfg.reasoningEffort;
                Double temperature = cfg.temperature;

                OrResult r = send(prompt, maxTokens, reasoningEffort, temperature);
                if (r == null) return null; // transport/HTTP-status failure, already logged by send()
                r = new OrResult(collapseDegenerateRepetition(r.content), r.finishReason,
                        r.promptTokens, r.completionTokens); // F2 residual (usage carried through)

                // TRUNCATION RECOVERY (Infra-3): MUST run BEFORE isNonAnswer() below -- ordering bug
                // (HARNESS-ANALYSIS.md F2; see the class javadoc above for the full 2026-07-21
                // investigation). A fully-empty `content` whose token budget was entirely consumed by
                // hidden reasoning is indistinguishable from a genuine non-answer, so when isNonAnswer()
                // ran first it always won the race and this retry was dead code for exactly the case it
                // exists to handle (0 [TRUNCATED->RETRY] firings across 18 finish_reason=length events
                // in the gpt-5/deepseek sweep). Retry ONCE at double the budget for ANY length-truncated
                // reply that hasn't already produced a recognizable decision tag -- empty or partial --
                // BEFORE ever asking "is this a non-answer". Only once finish_reason isn't "length" (or
                // the tag already made it out) do we fall through to the non-answer/bridge-down check.
                if ("length".equals(r.finishReason) && !looksLikeDecisionTag(r.content)) {
                    int retryTokens = Math.min(maxTokens * 2, 64000);
                    System.out.println("[OPENROUTER " + label + "] [TRUNCATED->RETRY] finish_reason=length @ max_tokens="
                            + maxTokens + " -> retrying at " + retryTokens);
                    OrResult retry = send(prompt, retryTokens, reasoningEffort, temperature);
                    // Only adopt the retry if it's not itself a non-answer: a truncated-but-substantive
                    // original reply is a better final result than a garbled/empty retry, and either way
                    // the isNonAnswer() check right below still gets the final say.
                    if (retry != null && !isNonAnswer(retry.content))
                        r = new OrResult(collapseDegenerateRepetition(retry.content), retry.finishReason,
                                retry.promptTokens, retry.completionTokens);
                }
                if (isNonAnswer(r.content)) {
                    System.out.println("[OPENROUTER " + label + "] non-answer -> treating as bridge-down");
                    lastFinishReason = r.finishReason;
                    lastPromptTokens = r.promptTokens; lastCompletionTokens = r.completionTokens;
                    return null;
                }
                lastFinishReason = r.finishReason;
                lastPromptTokens = r.promptTokens; lastCompletionTokens = r.completionTokens;
                long dur = System.currentTimeMillis() - t0;
                totalMs += dur; calls++;
                System.out.println("[OPENROUTER " + label + "] replied in " + dur + " ms");
                return r.content;
            } catch (Exception e) {
                System.out.println("[OPENROUTER " + label + "] error: " + e);
                return null;
            }
        }

        /** One HTTP round-trip. Returns null (transport/HTTP failure already logged) or a result
         *  carrying both the completion text and its finish_reason. */
        private OrResult send(String prompt, int maxTokens, String reasoningEffort, Double temperature) throws Exception {
            StringBuilder body = new StringBuilder();
            body.append("{\"model\":\"").append(jsonEscape(model)).append("\",");
            body.append("\"messages\":[");
            if (preamble != null && !preamble.isEmpty()) {
                body.append("{\"role\":\"system\",\"content\":\"").append(jsonEscape(preamble)).append("\"},");
            }
            body.append("{\"role\":\"user\",\"content\":\"").append(jsonEscape(prompt)).append("\"}");
            body.append("],\"max_tokens\":").append(maxTokens);
            if (reasoningEffort != null && !reasoningEffort.isEmpty())
                body.append(",\"reasoning\":{\"effort\":\"").append(jsonEscape(reasoningEffort)).append("\"}");
            if (temperature != null)
                body.append(",\"temperature\":").append(temperature);
            body.append("}");

            HttpRequest.Builder rb = HttpRequest.newBuilder()
                    .uri(URI.create(BASE_URL + "/chat/completions"))
                    .header("Authorization", "Bearer " + API_KEY)
                    .header("Content-Type", "application/json");
            if (HTTP_REFERER != null && !HTTP_REFERER.isEmpty()) rb.header("HTTP-Referer", HTTP_REFERER);
            if (HTTP_TITLE != null && !HTTP_TITLE.isEmpty()) rb.header("X-Title", HTTP_TITLE);

            HttpRequest req = rb.POST(HttpRequest.BodyPublishers.ofString(body.toString(), StandardCharsets.UTF_8))
                    .timeout(Duration.ofMillis(timeoutMs))
                    .build();
            HttpResponse<String> resp = client.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() != 200) {
                System.out.println("[OPENROUTER " + label + "] HTTP " + resp.statusCode() + ": " + oneLine(resp.body()));
                return null;
            }
            String content = extractJsonString(resp.body(), "\"content\":\"");
            String finishReason = extractJsonString(resp.body(), "\"finish_reason\":\"");
            // OpenAI-compatible usage object: {"usage":{"prompt_tokens":N,"completion_tokens":M,...}}.
            // -1 when the field is absent. Additive persistence for Stage-0 token-cost calibration.
            int promptTokens = extractJsonInt(resp.body(), "\"prompt_tokens\":");
            int completionTokens = extractJsonInt(resp.body(), "\"completion_tokens\":");
            return new OrResult(content, finishReason, promptTokens, completionTokens);
        }

        private static final class OrResult {
            final String content, finishReason;
            final int promptTokens, completionTokens;
            OrResult(String content, String finishReason, int promptTokens, int completionTokens) {
                this.content = content; this.finishReason = finishReason;
                this.promptTokens = promptTokens; this.completionTokens = completionTokens;
            }
        }

        @Override public String getLabel() { return label; }
        @Override public String getModel() { return model; }
        @Override public long getTotalMs() { return totalMs; }
        @Override public int getCalls() { return calls; }
        @Override public String getLastFinishReason() { return lastFinishReason; }
        @Override public int getLastPromptTokens() { return lastPromptTokens; }
        @Override public int getLastCompletionTokens() { return lastCompletionTokens; }
    }

    /** Factory: parses a model spec like "haiku" (Claude CLI) or
     *  "openrouter:anthropic/claude-3.5-sonnet" (OpenRouter API). */
    static final class LLMBridgeFactory {
        static LLMBridge create(String label, String spec, String preamble) {
            if (spec == null || spec.isEmpty())
                throw new IllegalArgumentException("model spec required for " + label);
            if (spec.startsWith("openrouter:")) {
                // Explicit egress consent: every OpenRouter call ships the full game snapshot
                // (board, hand, deck plan) to a third-party API. Holding an API key is a
                // configuration fact, not consent — require the dedicated opt-in flag too.
                if (!"1".equals(System.getenv("OPENROUTER_ALLOW_EGRESS")))
                    throw new IllegalStateException(
                            "openrouter: backend sends full game state to openrouter.ai; "
                            + "set OPENROUTER_ALLOW_EGRESS=1 to consent (" + label + ")");
                String model = spec.substring("openrouter:".length());
                return new OpenRouterBridge(label, model, preamble);
            }
            // Legacy/default: bare model name -> Claude CLI bridge.
            return new ClaudeBridge(label, spec, preamble);
        }
    }

    /** Parse a plain "N Cardname" decklist (blank line separates mainboard from sideboard). */
    static Deck loadDeck(String name, String path) throws Exception {
        Deck deck = new Deck(name);
        DeckSection section = DeckSection.Main;
        int mainCt = 0, sideCt = 0;
        Pattern entry = Pattern.compile("^(\\d+)\\s+(.*\\S)\\s*$");
        for (String raw : Files.readAllLines(Paths.get(path), StandardCharsets.UTF_8)) {
            String line = raw.trim();
            if (line.isEmpty()) { section = DeckSection.Sideboard; continue; }
            if (line.startsWith("#") || line.equalsIgnoreCase("sideboard")) { section = DeckSection.Sideboard; continue; }
            Matcher m = entry.matcher(line);
            if (!m.find()) continue;
            int qty = Integer.parseInt(m.group(1));
            deck.getOrCreate(section).add(m.group(2).trim(), qty);
            if (section == DeckSection.Main) mainCt += qty; else sideCt += qty;
        }
        System.out.println("[DECK] loaded '" + name + "': " + mainCt + " main, " + sideCt + " sideboard  (" + path + ")");
        return deck;
    }

    /** Per-deck strategy sidecar: for deck file foo.txt, read foo.plan (or foo.txt.plan) if present.
     *  Lets each deck-identity carry its own game plan WITHOUT polluting the decklist parser, where a
     *  leading '#'/blank line is a section marker (see loadDeck). Env PLAN_A/B still overrides, and
     *  PLAN_A="" still yields the plan-free cited arm — this only governs the unset case. */
    static String loadPlanSidecar(String deckPath, String fallback) {
        if (deckPath == null) return fallback;
        for (String cand : new String[]{ deckPath.replaceFirst("\\.txt$", "") + ".plan", deckPath + ".plan" }) {
            try {
                var pp = Paths.get(cand);
                if (Files.exists(pp)) {
                    String s = new String(Files.readAllBytes(pp), StandardCharsets.UTF_8).trim();
                    if (!s.isEmpty()) { System.out.println("[PLAN] sidecar -> " + cand); return s; }
                }
            } catch (Exception e) { System.out.println("[PLAN] sidecar read failed (" + cand + "): " + e); }
        }
        return fallback;
    }

    /** Machine-readable game-summary JSONL line + human GAME OVER block, shared by the headless and GUI
     *  runners so both emit the IDENTICAL identity-keyed record the eval/tournament drivers parse. All
     *  per-identity fields (deck/controller/model/plan) are keyed to deck-identity A/B, never post-swap. */
    static void writeGameSummary(Game game, long seed, long durMs, long capMs, boolean capped) {
        String nameA = System.getenv().getOrDefault("DECK_A_NAME", "Grixis Reanimator");
        String nameB = System.getenv().getOrDefault("DECK_B_NAME", "Mardu Energy");
        String winner = "draw"; int turns = -1; String winCond = "?";
        try {
            if (game != null && game.getOutcome() != null) {
                turns = game.getOutcome().getLastTurnNumber();
                winCond = String.valueOf(game.getOutcome().getWinCondition());
                var w = game.getOutcome().getWinningLobbyPlayer();
                // unknown-name winner should be unreachable; map to "invalid" so the A|B|draw|invalid contract holds
                if (w != null) winner = nameA.equals(w.getName()) ? "A" : (nameB.equals(w.getName()) ? "B" : "invalid");
            }
        } catch (Exception ignore) { }
        if (capped) winner = "invalid";

        System.out.println("========== GAME OVER ==========");
        System.out.println("outcome: " + winner + " after " + turns + " turns (" + winCond + ") in " + (durMs / 1000) + "s");
        System.out.println("--- full game log ---");
        try {
            if (game != null)
                for (GameLogEntry e : game.getGameLog().getAllEntries())
                    System.out.println("  " + e.type() + ": " + e.message());
        } catch (Exception e) { System.out.println("  (log unavailable: " + e + ")"); }
        System.out.println("===============================");

        String smartDef = System.getenv().getOrDefault("SMART_MODEL", "opus");
        String fastDef  = System.getenv().getOrDefault("FAST_MODEL", "haiku");
        EvalLog.write("{\"t\":\"game\",\"seed\":" + seed
                + ",\"swap\":" + "1".equals(System.getenv("SWAP_SEATS"))
                + ",\"deck_a\":\"" + jsonEscape(nameA) + "\""
                + ",\"deck_b\":\"" + jsonEscape(nameB) + "\""
                + ",\"controller_a\":\"" + jsonEscape(System.getenv().getOrDefault("CONTROLLER_A", "llm")) + "\""
                + ",\"controller_b\":\"" + jsonEscape(System.getenv().getOrDefault("CONTROLLER_B", "llm")) + "\""
                + ",\"smart_a\":\"" + jsonEscape(System.getenv().getOrDefault("SMART_MODEL_A", smartDef)) + "\""
                + ",\"smart_b\":\"" + jsonEscape(System.getenv().getOrDefault("SMART_MODEL_B", smartDef)) + "\""
                + ",\"fast_a\":\"" + jsonEscape(System.getenv().getOrDefault("FAST_MODEL_A", fastDef)) + "\""
                + ",\"fast_b\":\"" + jsonEscape(System.getenv().getOrDefault("FAST_MODEL_B", fastDef)) + "\""
                + ",\"winner\":\"" + winner + "\",\"turns\":" + turns
                + ",\"win_condition\":\"" + jsonEscape(winCond) + "\""
                + ",\"duration_ms\":" + durMs
                + ",\"mulligan_heuristic\":" + (PlayerControllerLLM.mullHeuristic() ? 1 : 0)
                + ",\"skip_heuristics\":" + (PlayerControllerLLM.skipHeuristics() ? 1 : 0)
                + ",\"plan_a\":\"" + jsonEscape(lastPlanA) + "\""
                + ",\"plan_b\":\"" + jsonEscape(lastPlanB) + "\""
                + ",\"smart_budget\":" + PlayerControllerLLM.smartBudget()
                + ",\"fast_budget\":" + PlayerControllerLLM.fastBudget()
                + ",\"claude_deadline_ms\":" + ClaudeBridge.deadlineMs()
                + ",\"game_cap_ms\":" + capMs
                + ",\"engine_sha\":\"" + jsonEscape(System.getenv().getOrDefault("ENGINE_SHA", "unknown")) + "\""
                + ",\"overlay_sha\":\"" + jsonEscape(System.getenv().getOrDefault("OVERLAY_SHA", "unknown")) + "\""
                + ",\"deck_sha\":\"" + jsonEscape(System.getenv().getOrDefault("DECK_SHA", "unknown")) + "\""
                + ",\"discipline\":\"" + jsonEscape(System.getenv().getOrDefault("RUN_DISCIPLINE", "unspecified")) + "\""
                + "}");
    }

    public static void main(String[] args) {
        GuiBase.setInterface(new GuiDesktop());
        Singletons.initializeOnce(true);
        Singletons.getControl().initialize();
        new Thread(() -> {
            try { Thread.sleep(3500); } catch (InterruptedException ignored) {}
            SwingUtilities.invokeLater(LlmSpikeGui::startSpectatorMatch);
            // Opt-in autonomous window recording (no macOS Screen-Recording permission needed).
            String rec = System.getenv("RECORD");
            if (rec != null && !rec.trim().isEmpty() && !"0".equals(rec.trim())) {
                try { Thread.sleep(5000); } catch (InterruptedException ignored) {} // let the match window render
                WindowRecorder recorder = WindowRecorder.startFromEnv();
                if (recorder != null) Runtime.getRuntime().addShutdownHook(new Thread(recorder::stop, "recorder-stop"));
            }
        }, "llm-spike-launcher").start();
    }

    private static void startSpectatorMatch() {
        System.out.println("[LLM-SEAM] building LLM-vs-LLM spectator match...");
        final long seed = applySeed();
        EvalLog.init(seed);
        List<RegisteredPlayer> players = buildPlayers();
        // MATCH_GAMES sets games-per-match (default 3 = watch/demo best-of-3; tournament sets 1 for bo1).
        int gpm = 3;
        try { String g = System.getenv("MATCH_GAMES"); if (g != null && !g.trim().isEmpty()) gpm = Math.max(1, Integer.parseInt(g.trim())); }
        catch (Exception ignore) {}
        final boolean bo1 = gpm == 1;
        // Canonical Constructed rules built explicitly (mana-burn off, no ante, gamesPerMatch as set) rather
        // than HostedMatch.getDefaultRules(), which reads the local desktop FModel prefs. This matches what
        // the headless eval runner already does (LlmSpikeHeadless: `new GameRules(GameType.Constructed)`),
        // so a GUI match is reproducible and independent of whatever prefs the desktop install happens to have.
        GameRules rules = new GameRules(GameType.Constructed);
        rules.setGamesPerMatch(gpm);
        final HostedMatch hosted = GuiBase.getInterface().hostMatch();
        hosted.startMatch(rules, EnumSet.of(GameType.Constructed), players, (RegisteredPlayer) null, null);
        System.out.println("[LLM-SEAM] match started (games-per-match=" + gpm + "). Watch the Forge window.");

        // Tournament / automation: with AUTO_EXIT=1, watch the match and when it ends emit the identity-keyed
        // game-summary line (same one headless writes), give the recorder a beat to capture the final board,
        // then exit(0) so the driver can parse the winner and launch the next match. SIGINT-safe: the recorder
        // shutdown hook still finalizes the mp4 on exit.
        if ("1".equals(System.getenv("AUTO_EXIT"))) {
            // Backstop cap only (drivers set GAME_CAP_MS explicitly per game); 2h default just bounds a
            // wedged manual run. The headless runner defaults 3h — both are overridable, drivers set both.
            long capEnv = 7200000L;
            try { String c = System.getenv("GAME_CAP_MS"); if (c != null && !c.trim().isEmpty()) capEnv = Long.parseLong(c.trim()); }
            catch (Exception ignore) {}
            final long capMs = capEnv;
            final long t0 = System.currentTimeMillis();
            Thread mon = new Thread(() -> {
                Game g = null;
                boolean over = false;
                while (System.currentTimeMillis() - t0 < capMs) {
                    try { over = hosted.isMatchOver(); } catch (Exception e) { over = false; }
                    // The single-game-outcome fallback is only a valid "match over" signal in bo1; in a
                    // multi-game match game 1's outcome must NOT trigger exit, so gate it on bo1.
                    if (!over && bo1) { try { g = hosted.getGame(); over = g != null && g.getOutcome() != null; } catch (Exception e) {} }
                    if (over) break;
                    try { Thread.sleep(1500); } catch (InterruptedException e) { break; }
                }
                boolean capped = !over;
                long dur = System.currentTimeMillis() - t0;
                try { g = hosted.getGame(); } catch (Exception e) {}
                try { Thread.sleep(4000); } catch (InterruptedException ignore) {} // let the recorder capture the result screen
                try { writeGameSummary(g, seed, dur, capMs, capped); }
                catch (Exception e) { System.out.println("[AUTO_EXIT] summary failed: " + e); }
                System.out.println("[AUTO_EXIT] match over (capped=" + capped + ") -> exiting for driver");
                // Force-halt backstop: System.exit runs shutdown hooks (incl. the recorder's mp4
                // finalize). A buggy JVM hook — observed: ImageIO StreamCloser throwing during
                // teardown — can hang the exit indefinitely, stranding the JVM and blocking the
                // driver. Give the hooks 12s (the recorder finalizes in ~1-2s) then hard-halt.
                Thread forceHalt = new Thread(() -> {
                    try { Thread.sleep(12000); } catch (InterruptedException ignore) {}
                    System.out.println("[AUTO_EXIT] shutdown stalled >12s -> Runtime.halt(0)");
                    Runtime.getRuntime().halt(0);
                }, "force-halt");
                forceHalt.setDaemon(true);
                forceHalt.start();
                System.exit(0);
            }, "auto-exit-monitor");
            mon.setDaemon(true);
            mon.start();
        }
    }

    /**
     * Autonomous window recorder. Paints the Forge {@link FFrame} to a BufferedImage on a fixed-fps timer
     * and pipes PNG frames straight into an ffmpeg subprocess (image2pipe -> H.264 mp4). Because the Swing
     * app renders ITSELF into the image, this needs NO macOS Screen-Recording (TCC) permission and captures
     * exactly the Forge window — nothing else on screen. Opt-in via env RECORD=1; RECORD_OUT sets the output
     * path (default ~/Downloads/forge-game-<ts>.mp4), RECORD_FPS the frame rate (default 8, clamped 1..30).
     */
    static final class WindowRecorder {
        private final int fps;
        private final String outPath;
        private volatile boolean running;
        private Process ff;
        private OutputStream ffIn;
        private Thread thread;
        private int w, h;

        private WindowRecorder(int fps, String outPath) { this.fps = fps; this.outPath = outPath; }

        static WindowRecorder startFromEnv() {
            int fps = 8;
            try { String f = System.getenv("RECORD_FPS"); if (f != null) fps = Math.max(1, Math.min(30, Integer.parseInt(f.trim()))); }
            catch (Exception ignore) {}
            String out = System.getenv("RECORD_OUT");
            if (out == null || out.isEmpty())
                out = System.getProperty("user.home") + "/Downloads/forge-game-" + System.currentTimeMillis() + ".mp4";
            WindowRecorder r = new WindowRecorder(fps, out);
            return r.start() ? r : null;
        }

        private boolean start() {
            try {
                FFrame frame = waitForFrame(20000);
                if (frame == null) { System.out.println("[REC] no Forge frame appeared; recording disabled"); return false; }
                int[] wh = sizeOf(frame);
                w = wh[0]; h = wh[1];
                ProcessBuilder pb = new ProcessBuilder("ffmpeg", "-y",
                        "-f", "image2pipe", "-framerate", String.valueOf(fps), "-i", "-",
                        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", outPath);
                pb.redirectErrorStream(true);
                pb.redirectOutput(ProcessBuilder.Redirect.appendTo(new java.io.File(outPath + ".ffmpeg.log")));
                ff = pb.start();
                ffIn = ff.getOutputStream();
                running = true;
                thread = new Thread(this::loop, "window-recorder");
                thread.setDaemon(true);
                thread.start();
                System.out.println("[REC] recording Forge window " + w + "x" + h + " @ " + fps + "fps -> " + outPath);
                return true;
            } catch (Exception e) {
                System.out.println("[REC] failed to start: " + e);
                return false;
            }
        }

        private void loop() {
            long frameNs = 1_000_000_000L / fps;
            while (running && ff.isAlive()) {
                long t0 = System.nanoTime();
                try {
                    BufferedImage img = capture();
                    if (img != null) { ImageIO.write(img, "png", ffIn); ffIn.flush(); }
                } catch (Exception e) { break; } // broken pipe / ffmpeg gone
                long restMs = (frameNs - (System.nanoTime() - t0)) / 1_000_000L;
                if (restMs > 0) { try { Thread.sleep(restMs); } catch (InterruptedException ie) { break; } }
            }
        }

        private BufferedImage capture() throws Exception {
            final FFrame frame = FView.SINGLETON_INSTANCE.getFrame();
            if (frame == null || !frame.isShowing()) return null;
            final BufferedImage[] out = new BufferedImage[1];
            Runnable r = () -> {
                try {
                    int cw = Math.max(2, frame.getWidth() & ~1), ch = Math.max(2, frame.getHeight() & ~1);
                    BufferedImage bi = new BufferedImage(cw, ch, BufferedImage.TYPE_INT_RGB);
                    Graphics2D g = bi.createGraphics();
                    frame.printAll(g); // render the whole frame (skinned title bar + match view) into the image
                    g.dispose();
                    out[0] = bi;
                } catch (Exception ignore) {}
            };
            if (SwingUtilities.isEventDispatchThread()) r.run(); else SwingUtilities.invokeAndWait(r);
            BufferedImage bi = out[0];
            if (bi == null) return null;
            if (bi.getWidth() != w || bi.getHeight() != h) { // window resized -> scale to the fixed encode size
                BufferedImage fixed = new BufferedImage(w, h, BufferedImage.TYPE_INT_RGB);
                Graphics2D g = fixed.createGraphics();
                g.drawImage(bi, 0, 0, w, h, null);
                g.dispose();
                return fixed;
            }
            return bi;
        }

        void stop() {
            if (!running && ff == null) return;
            running = false;
            try { if (thread != null) thread.join(3000); } catch (Exception ignore) {}
            try { if (ffIn != null) { ffIn.flush(); ffIn.close(); } } catch (Exception ignore) {} // EOF -> ffmpeg finalizes moov
            try { if (ff != null) ff.waitFor(); } catch (Exception ignore) {}
            System.out.println("[REC] recording finalized -> " + outPath);
        }

        private static int[] sizeOf(FFrame frame) throws Exception {
            final int[] wh = new int[2];
            Runnable r = () -> { wh[0] = Math.max(2, frame.getWidth() & ~1); wh[1] = Math.max(2, frame.getHeight() & ~1); };
            if (SwingUtilities.isEventDispatchThread()) r.run(); else SwingUtilities.invokeAndWait(r);
            return wh;
        }

        private static FFrame waitForFrame(long timeoutMs) {
            long deadline = System.currentTimeMillis() + timeoutMs;
            while (System.currentTimeMillis() < deadline) {
                try {
                    FFrame f = FView.SINGLETON_INSTANCE.getFrame();
                    if (f != null && f.isShowing() && f.getWidth() > 0) return f;
                } catch (Exception ignore) {}
                try { Thread.sleep(200); } catch (InterruptedException ie) { return null; }
            }
            return null;
        }
    }
}
