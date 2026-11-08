package forge.spike;

import java.awt.Graphics2D;
import java.awt.image.BufferedImage;
import java.io.OutputStream;
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
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
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
import forge.card.ColorSet;
import forge.card.MagicColor;
import forge.ai.AiCardMemory;
import forge.ai.ComputerUtilAbility;
import forge.ai.ComputerUtilCombat;
import forge.ai.ComputerUtilMana;
import forge.ai.AiProfileUtil;
import forge.ai.LobbyPlayerAi;
import forge.ai.PlayerControllerAi;
import forge.deck.Deck;
import forge.deck.DeckSection;
import forge.deck.DeckgenUtil;
import forge.game.Game;
import forge.game.GameActionUtil;
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
import forge.game.cost.Cost;
import forge.game.cost.CostPayment;
import forge.game.keyword.Keyword;
import forge.game.phase.PhaseHandler;
import forge.game.phase.PhaseType;
import forge.game.player.DelayedReveal;
import forge.game.player.Player;
import forge.game.player.PlayerActionConfirmMode;
import forge.game.player.RegisteredPlayer;
import forge.game.spellability.AbilitySub;
import forge.game.spellability.OptionalCostValue;
import forge.game.spellability.SpellAbility;
import forge.game.spellability.TargetRestrictions;
import forge.game.zone.ZoneType;
import forge.gamemodes.match.HostedMatch;
import forge.player.LobbyPlayerHuman;
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
    /** The tail of a bridge's no-answer log line: what askModel does with a null (its retry, then the caller's fixed
     *  legal option). One place, so the bridges' lines cannot drift from the retry. */
    String NO_ANSWER = "no answer; the seat re-asks while it has attempts left, then takes its fixed legal option";
    /** True when this backend can take native function definitions (see {@link NativeTools}). */
    default boolean supportsNativeTools() { return false; }
    /** One assistant turn over a whole message list (a JSON array, system preamble excluded), with
     *  `toolsJson` offered. `allowCalls=false` still declares the tools (the list already holds tool
     *  calls) but forbids another call. Null on failure/non-answer, like {@link #ask}. */
    default NativeTools.Turn chat(String messagesJson, String toolsJson, boolean allowCalls) { return null; }
    /** {@link #chat} for an ask that offers `answer`, with that decision's typed legal values. A backend that answers
     *  from them instead of from a conversation ({@link DecisionsBridge}) overrides this; every other bridge ignores
     *  {@code spec}, so what it is sent is unchanged. */
    default NativeTools.Turn chat(String messagesJson, String toolsJson, boolean allowCalls, AnswerTool.Spec spec) {
        return chat(messagesJson, toolsJson, allowCalls);
    }
    /** The provider's own cost of the most recent call in USD (the Decisions API reports `usage.cost`), or -1 when it
     *  reports none. Written on the context_call row as `cost_usd`. */
    default double getLastCostUsd() { return -1; }
    /** HTTP refusals so far under a routing filter (OPENROUTER_MAX_PRICE, OPENROUTER_QUANTIZATIONS), or null when this
     *  bridge has neither. */
    default Integer getRoutingRefusals() { return null; }
    /** Whether the most recent ask that returned no reply failed in TRANSPORT: an HTTP status other than a tool or
     *  routing refusal (402 credits, 401, 429, 5xx), an IO error, or a connection that sent no reply. False when the
     *  provider answered and the answer was empty or unreadable, the deadline ran out while the model reasoned, or a
     *  routing filter refused it: those are the model's decisions lost, and stay scored (issue #369). */
    default boolean lastFailureWasTransport() { return false; }
    /** The OpenAI-compatible `finish_reason` ("stop", "length", ...) of the most recently completed
     *  ask(), or null when the backend doesn't report one (Claude CLI) or nothing has completed yet.
     *  A plain last-value field is sufficient — the controller's ask() path is single-threaded per
     *  seat — and gives the eval-record layer a way to distinguish a clean stop from a token-budget
     *  truncation without re-deriving it from raw_reply. */
    default String getLastFinishReason() { return null; }
    /** Prompt (input) token count reported by the backend's response `usage` object for the most
     *  recently completed ask(), or -1 when the backend reports no usage or nothing has
     *  completed yet. Persisted per-decision in the eval JSONL so Stage-0 calibration can sum true
     *  input/output token cost without re-deriving it. Same last-value single-threaded-per-seat
     *  contract as getLastFinishReason(). */
    default int getLastPromptTokens() { return -1; }
    /** Completion (output) token count from the response `usage` object for the most recent ask(),
     *  or -1 when unreported. See {@link #getLastPromptTokens()}. */
    default int getLastCompletionTokens() { return -1; }
    /** Prompt tokens of the most recent ask() that the provider read from its prompt cache
     *  (`usage.prompt_tokens_details.cached_tokens`), counted inside getLastPromptTokens(); -1 when
     *  unreported. Lets cost_report.py price them at the cache-read rate. */
    default int getLastCachedTokens() { return -1; }
    /** Prompt tokens the provider wrote to its cache on the most recent ask()
     *  (`usage.prompt_tokens_details.cache_write_tokens`, Anthropic models), -1 when unreported. */
    default int getLastCacheWriteTokens() { return -1; }
    /** The provider that served the most recent ask (OpenRouter's response `provider`), or null. */
    default String getLastProvider() { return null; }
    /** Prompt tokens billed since the bridge was made, every send that came back counted (a truncation retry's first
     *  send included), or -1 when the bridge does not count them. Read as a difference around an ask, it is what the
     *  ask spent, which a failed ask's stale getLastPromptTokens() is not. */
    default long getBilledPromptTokens() { return -1; }
    /** Completion tokens billed, as {@link #getBilledPromptTokens()}. */
    default long getBilledCompletionTokens() { return -1; }
}

/**
 * SPIKE (throwaway): launch the real Forge desktop window and WATCH two custom
 * LLM PlayerControllers play a game AGAINST EACH OTHER in the GUI (watched, or with one person seated).
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
    /** One number for "the interface the model plays through" (docs/STANDARDS.md section 8): bumped
     *  whenever what the seat is asked, when it is asked, or what its answer does changes, beside the
     *  per-layer versions. Games across an epoch are not compared. Epoch 2 = the 2026-10-15 bottleneck
     *  work (window rows, intent carry and cancel, X/modes/targets, trigger targets, attack targets,
     *  votes and effect casts, mana sources, model yields, ONE_TIER, neutral asks). Epoch 3: a cost ask
     *  (sacrifice, exile, discard, return, tap) can cancel a play that is not committed yet. Epoch 4:
     *  how a Phyrexian symbol is paid (life or mana) is asked. Epoch 5: engine notices (a named card, a
     *  chosen colour) are shown; an unparsed play or block is said, not read as PASS / NONE; block pairs
     *  keep names with commas; the per-turn play cap is 80 under the faithful profile; a PLAN line at any
     *  decision replaces the plan (a goal and steps); the seat can report a broken question. Epoch 6:
     *  the mana line says its colours over-count; call markup past the budget is recorded as exhausted.
     *  Epoch 7: on a backend with function calling every decision is answered by calling `answer`
     *  (AnswerTool, ANSWER_TOOL=0 opts out) and the plan, cast list, opponent read and card-lines note are
     *  tool arguments, not lines.
     *  Epoch 8: under the faithful profile the system prompt is rewritten from the prompt audit (the deck,
     *  how the interface works, how to read the state, Legacy terms; no coaching, no reasoning cap, no
     *  worked examples), the state legend is said once there instead of in every message (observation
     *  v24), and the decision text is the last thing in each message. The harness shortcuts are off by
     *  default: no mulligan heuristic, no skip heuristics, one tier (no question sent to a no-reasoning
     *  call); each comes back only with MULLIGAN_HEURISTIC=1, SKIP_HEURISTICS=1, ONE_TIER=0.
     *  Epoch 9: the golden checks (GoldenChecksTest, docs/harness/GOLDEN-CHECKS.md): no faithful ask cuts its
     *  question or shows an engine id or CARDNAME; the mulligan and a wide number carry the observation and
     *  a DECISION; retries re-send the whole ask; a divided spell's split is asked from the target hook; a
     *  "(castable)" hand tag means the menu can cast it; discard/delve asks no longer coach.
     *  Epoch 10: a multi-zone exile cost names its zones in words ("graveyard or hand", #109); a tap-cost
     *  menu leaves out what a RAISED mana cost's payment taps (#110); under PLAN_EXECUTE a plan-executed
     *  step's follow-up asks carry that step (#108); PLAN_EXECUTE and TIER_FAST_TYPES are stamped on the
     *  game line (#117). The harness no longer limits the model (docs/harness/MODEL-LIMITS.md): uncut
     *  history, reveals and card text, 16 tool calls an ask, any number of previews, budgets as loop guards
     *  only.
     *  Epoch 11: the answer tool nudges once for a missing call and takes `keep_untapped` on a play; the
     *  faithful mulligan and card-naming asks state facts in plain sentences.
     *  Epoch 12: `steps` is offered (and the play ask mentions it) only under PLAN_EXECUTE, the only
     *  setting that carries the steps out; a tap part with exactly as many candidates as it needs takes
     *  them, and what a tap part names is held while that ability's mana is paid (#111).
     *  Epoch 13: no text alternative on a tool ask (answer-tool-v7): a reply that calls nothing is reminded,
     *  then lost; its prose is never read as the answer.
     *  Epoch 14: no engine fallback where the seat can decide: blocker/attacker damage order is asked; a
     *  mana keep set that cannot pay is asked again, then the payable prefix kept (never the engine's plan);
     *  card naming, modes and targets on error take fixed legal choices; a short cost pick takes the first
     *  legal cards; empty choices return empty without asking Forge's AI.
     *  Epoch 15: a model's filled-in defaults do nothing (answer-tool-v8): `yield` "none" first, an empty
     *  `keep_untapped` is not said, and handles that name no source ask the mana question.
     *  Epoch 16: convoke/improvise, single-target retargeting, redirection and mana from the pool are the seat's.
     *  Epoch 17: every retargeting and effects played without the stack. Epoch 18: delve is the seat's; a
     *  crashed game has no winner. Epoch 19: a yield never passes the seat's own main phase (answer-tool-v9).
     *  Epoch 20: the seat sees the known top of its own library (observation-faithful-v27).
     *  Epoch 21: the first engine patch: the colour an any-colour source makes for generic is the seat's.
     *  Epoch 22: stack text redacted of engine ids, card text without literal \\n (observation-faithful-v28).
     *  Epoch 23: no engine id inside a name (an effect named after its source; observation-faithful-v29).
     *  Epoch 24: a target question's trigger text is redacted; an optional target declined is the seat's decision.
     *  Epoch 25: mana available is the engine's mana per source, not a count of sources (observation-faithful-v30).
     *  Epoch 26: each block names its source (engine, Forge game log, the seat's notes); the opponent's casts and
     *  land drops come from engine events, not log wording (observation-faithful-v31).
     *  Epoch 27: questions name cards without engine ids (one name function); mode and target questions say the
     *  announced X.
     *  Epoch 28: a mulligan at five cards or fewer is the seat's (the floor only with MULLIGAN_HEURISTIC=1).
     *  Epoch 29: the plan is a note, not a commitment: every decision is re-made on the state then (answer-tool-v10).
     *  Epoch 30 (2026-10-16 independent review): no reasoning cap and one answer line on a tool ask (answer-tool-v11);
     *  a dead bridge takes the fixed legal option, never one read from the plan's prose; a lookahead preview
     *  re-deals the opponent's hand first, so it reveals no hidden card (lookahead-preview-v6).
     *  Epoch 31 (2026-10-16, C3 ANT Storm games): a colour the card decides (Chrome Mox) counts in mana available, ability
     *  text names the card (no CARDNAME), and a preview keeps imprinted cards (observation-faithful-v32,
     *  lookahead-preview-v7).
     *  Epoch 32 (2026-10-16, combo lines): a payment asked mid-way (an any-colour source) keeps the AI memory it uses, so
     *  the second Lotus Petal pays (Ad Nauseam); a mana ability with a cost beyond tapping (Lion's Eye Diamond, Spirit
     *  Guides) is on the menu; storm copies' new targets are the seat's and the copies are called copies; exile and an
     *  unordered graveyard are not ordered; a preview plays an alternative cost and holds the stack (lookahead-preview-v8).
     *  Epoch 44 (2026-10-17, game loop g02): the attack outlook says what its number is, "(2 damage)", not "(2)"
     *  (lookahead-preview-v9).
     *  Epoch 33 (2026-10-16): the stack says "not yet resolved", not "pending", carries no Forge debug note, and a copy
     *  effect says how many copies it makes; identical storm copies are not ordered (observation-faithful-v33).
     *  Epoch 34 (2026-10-16, surface checks): a counterspell targets the spell on the stack, not its card (it countered
     *  nothing), and Stifle's abilities are offered; values a cast announces (a Shoal's X, X in a life cost) are the
     *  seat's; a cast at another cost than the card's basic spell is labelled with that cost.
     *  Epoch 35 (2026-10-16, ways to play a card): optional costs (kicker, buyback, entwine, bargain) are asked after the
     *  pick, from the menu and from effects; a spell playable only with one (retrace, jump-start, offering) is offered
     *  with it; emerge is offered and its sacrifice is the seat's; a spell or trigger with several targets is asked for
     *  the set; a creature spell's option shows its rules text; no face-down 2/2 off the battlefield
     *  (observation-faithful-v34).
     *  Epoch 36 (2026-10-16, issues #224/#225): a declared damage assignment order is followed, not asked again; an
     *  "up to" cost reduction is asked only in the real payment.
     *  Epoch 37 (2026-10-16, state facets): the initiative, city's blessing, day/night, phased-out permanents, what a
     *  permanent remembers (named card, chosen type, imprint, token) and counters off the battlefield are shown
     *  (observation-faithful-v35).
     *  Epoch 38 (2026-10-16): optional costs picked together are checked together (two kickers payable alone, not both).
     *  Epoch 39 (2026-10-17, every way to play): pricing an optional cost no longer leaves a Defiler's reduction on the
     *  spell; harmonize is priced with its tap and its power asked from the payable ones; an alternative-cost option
     *  shows the card's text and the cost it pays.
     *  Epoch 40 (2026-10-17, keyword sweep and gauntlet): a cost with exactly as many candidates as it takes is paid
     *  with them, not left to Forge's AI (which declined to exile Altar of the Wretched for a craft); a play that
     *  failed to pay is not offered again in its window; a loyalty ability that adds mana is on the menu; floating
     *  mana is not asked while pricing (StackOverflowError); the log drops Forge's trigger notes
     *  (observation-faithful-v36); a prevent-effect cost names its card; attackers can form bands; exert, enlist,
     *  splice, coin flips, shield counters, dice, stickers, contraptions, attractions, starting hands and assist are the
     *  seat's (Forge's AI decided them), and a spliced spell is retargeted by the seat, not vetoed by Forge's AI; an
     *  ability's costs beyond mana are checked at its least X, and a non-mana X is capped by what can pay it; spree
     *  modes are offered only when payable; convoking creatures are not counted as mana when a land is kept, and a
     *  convoke that leaves the rest unpayable is asked again.
     *  Epoch 41 (2026-10-17, owner: "if it's a legal action we don't disallow it"): what can be paid is the engine's
     *  legality test, not Forge AI's taste (legallyPayable); a cost may be paid with the ability's own target; casualty
     *  candidates are not filtered; who crews, and which creatures sharing a type tap, are the seat's; mana abilities
     *  are on the menu (floating mana); a creature that can block more attackers may.
     *  Epoch 45 (2026-10-17): the guide lists every hand tag and what a mana ability's ACTIVATE does
     *  (observation-faithful-v37, seat-tools-v11); a kept mana source the engine cannot pay around is released and
     *  the cost paid again; a cancelled modal spell keeps no modes; delve's least count is the engine's test payment;
     *  a preview restores every target it set, and a target choice of a play can be previewed (lookahead-preview-v10).
     *  Epoch 46 (2026-10-17): the resolve line of a state-dependent pump (+X where X is the target's power, e.g.
     *  Berserk) is captured before the effect applies (HARNESS PATCH of forge/game/zone/MagicStack.java), so the
     *  seat's GAME HISTORY no longer contradicts the stack preview it was shown (g06 seed 4106 T7, "+5/+0" vs "+10/+0").
     *  Epoch 47 (2026-10-18): the engine's filter syntax is said in words: a cost label read "Exile a Card.Blue+Other
     *  card" (Force of Will) and a card-name ask "(must match: Card.nonLand)" (g05, Cabal Therapy).
     *  Epoch 48 (2026-10-18): the same in the state's stack and history lines (g07, Shallow Grave's "returns a
     *  Creature.TopGraveyardCreature+YouCtrl card"; observation-faithful-v38).
     *  Epoch 49 (2026-10-18): filters say who controls ("creatures an opponent controls"), a chosen or shared name, and
     *  a bare filter as a plural; an unsayable condition reads "the creatures the card describes" (v39). A pick that is
     *  one of several for the same effect ("(3 / 5)", Doomsday) says the cards already picked (g08).
     *  Epoch 50 (2026-10-18): the header before the first turn says "before the first turn (opening hands)", not
     *  "Turn 0 null (active: ? | priority: ?)" (observation-faithful-v40; a sweep of runs/ found it on every mulligan);
     *  no mana reads "none", and a subjectless stack sentence reads "It gains" (v41); "cast CAST", "Choose a Creature to
     *  name" and "top of Seat library" read as English.
     *  Epoch 51 (2026-10-18): a creature type is said in its own plural on an option ("Elves", not Forge's "Elfs"),
     *  and a preview plays a mana ability and shows the mana it adds.
     *  Epoch 52 (2026-10-18, rules puzzle pilot): a spell whose targets are all "up to" is offered with none to target
     *  (Finale of Promise); a source whose tap damage is lethal pays (City of Brass at 1 life, an engine patch); a
     *  block set the engine objects to (menace, "must block") goes back to the seat with the engine's reason.
     *  Epoch 53 (2026-10-18, rules puzzle batch 2): an activated ability's alternative cost is offered (Forge Anew:
     *  equip for {0}); an "up to one" target with a single candidate is asked, with "No target" offered.
     *  Epoch 54 (2026-10-18): the seat clears Forge's static AiCache where Forge's AI does, so answers cached against
     *  this game (isPreventCombatDamageThisTurn) are not read stale; a preview survives a card remembered outside
     *  every copied zone (lookahead-preview-v13, runs/loop/g16).
     *  Epoch 55 (2026-10-18, rules puzzle batch 4): a choice list's section headers ("--PERMANENTS:--", Forge's
     *  divider cards in "choose a source") are not offered as options.
     *  Epoch 56 (2026-10-18): combat damage divided as the attacker chooses (the 2024 rules, Forge's default here) is
     *  asked as amounts per blocker and, with trample, the player, checked against the lethal-first trample rule; it was
     *  an order with damage cascading lethal-first, which hid every other legal split.
     *  Epoch 57 (2026-10-18): "lethal damage" for assignment is the rule's (toughness minus damage, 1 with deathtouch),
     *  not Forge AI's estimate of what kills, which forced legal trample splits past indestructible, protected or
     *  shielded blockers (rules pilot RP_156-RP_160).
     *  Epoch 58 (2026-10-18): a preview no longer fails on a prepared card (Enroot) in any copy, the simulator's internal
     *  ones included: an engine patch to GameCopier (runs/loop/g23), lookahead-preview-v14.
     *  Epoch 59 (2026-10-19, rules puzzle batch 6): a card may pay for its own ability (Mishra's Factory tapping itself
     *  for its animate; Forge AI's mana chooser skipped it), an engine patch; the arms take a legal block set under a
     *  block requirement instead of keeping an illegal one after 64 draws.
     *  Epoch 60 (2026-10-19, runs/loop/g27): a card pays for its own ability only when the two do not both tap it;
     *  epoch 59 offered Rishadan Port's tap ability with Port as the only source, and the activation failed.
     *  Epoch 61 (2026-10-19, runs/loop/g28): a preview aims a counterspell at the spell on the copy's stack, and reports
     *  exile counts (lookahead-preview-v15).
     *  Epoch 62 (2026-10-19, rules pilot batches 7-8): a mana ability's sacrifice cost inside a payment is the seat's
     *  (Thermopod, RP_198); a forced free cast that still costs mana asks whether to pay (Sphere, RP_197); an optional
     *  cast the engine just confirmed is not asked again (suspend, RP_209); setup choices draw nothing (RP_207).
     *  Epoch 63 (2026-10-19, rules pilot batch 10): a play a static forbids (Pithing Needle, Null Rod, Gaddock Teeg) is
     *  not on the menu; the engine's canPlay() leaves those statics to checkRestrictions, which the menu never asked
     *  (Endbringer's ping under a Needle naming it was offered and played, RP_207).
     *  Epoch 64 (2026-10-19, rules pilot batch 12): each target of a divided spell gets at least 1 (CR 601.2d); the split
     *  [2 0] was taken, so Fire at two targets dealt all 2 to one.
     *  Epoch 65 (2026-10-19, pr-review of PR #268): a source whose tap damage is lethal pays last for a harness seat,
     *  not first; at 1 life with City of Brass and Llanowar Elves the payment plan tapped City for {1}.
     *  Epoch 66 (2026-10-20, authored rules puzzles): an attack the engine refuses (Silent Arbiter) is asked once more
     *  with the reason, then made legal; the engine re-asked forever. The puzzle prover targets a copy that may take
     *  new targets (Twincast), as the model seat does; Forge's AI picked it.
     *  Epoch 67 (2026-10-20, benchmark sample v1): a rules puzzle's <puzzle> block, and the Puzzle Goal card
     *  `inspect` reads, are its author's description and turn limit only; Forge's header said "Goal: Survive" on 148
     *  of 526 whose acceptance is `life p1 <= 0`, and its Name and Difficulty lines named the rule being tested.
     *  Epoch 68 (2026-10-20, hard draft h_0): Forge's AI-only payment hints (AIPhyrexianPayment: Gitaxian Probe's
     *  "Never") no longer decide what the seat may cast; a Phyrexian cost payable only with life is paid by the
     *  harness. Probe with no blue source was "(not castable now)" and off the menu.
     *  Epoch 69 (2026-10-20, BENCHMARK-V2 phase 2): a Forge puzzle's brief starts at its goal line: no name, no
     *  difficulty label (the neutral brief). The rules puzzles lost theirs in epoch 67.
     *  Epoch 70 (2026-10-20, LineFindingProbeTest): a play that fails to pay, is cancelled at its cost or is refused
     *  by the stack for want of a target is rolled back by the engine's own rollback: a cast from hand returns to the
     *  hand, the mana paid is refunded and the stack is unfrozen. The engine routine's recovery left the card in the
     *  Stack zone with nothing on the stack.
     *  Epoch 71 (2026-10-24, issue #311, harness-env grader combat.block.must-block): when the seat's blocks are lost or
     *  still illegal after the re-ask with the engine's reason, and a block requirement forbids no blocks (Prized
     *  Unicorn, a lure), the smallest legal block set is taken; "no blocks" stood illegal. It also changes how the
     *  model's own block answers are judged: a set above a global block cap (Silent Arbiter) is refused, and an
     *  at-cap set the engine's requirement check wrongly refuses is accepted when each blocker obeys a requirement
     *  and the block restrictions hold (blocksError).
     *  Epoch 72 (2026-10-24, issue #312, the grader's costs.x): a declined search answered NO at "Cancel Search?" is
     *  asked again once, saying a second decline ends the search; a second decline does (the engine re-asked forever).
     *  Epoch 73 (2026-10-26, frontier5 GLM 5.3 on hard-elf2, answer-tool-v12): a reply's answer is its first `answer`
     *  call that validates. A reply that sent its analysis in one `answer` call and its option in a second was refused
     *  twice and its decision lost to PASS, skipping main phase 1. A play decision on a tool ask reads no text.
     *  Epoch 74 (2026-11-03, answer-tool-v13): the PROPOSE_ACTIONS measurement arm. Off (the default) every prompt and
     *  schema is unchanged; on, the play menu is hidden and the seat proposes its play (docs/harness/ARCHITECTURE.md,
     *  "The propose condition").
     *  Epoch 75 (2026-11-03, answer-tool-v14): under PROPOSE_ACTIONS a play's one target is proposed with it, and suspend,
     *  foretell and the other special actions are in YOUR ACTIVATED ABILITIES (the gauntlet audit found them unproposable).
     *  Epoch 76 (2026-11-04, answer-tool-v15, pr-review #342): under PROPOSE_ACTIONS a cast naming an optional cost
     *  (Kicker) or "normal" is the normal cast, not a cost error; a cost name matches only itself ("flash" is not
     *  Flashback); a cast with no cost is the normal cast only, never the menu's one other way (Evoke); a proposed
     *  target is consumed by its own play's target step only. Unchanged off the arm.
     *  Epoch 77 (2026-11-04): a play whose sub-ability must target is offered only when that target exists (Govern the
     *  Guildless's forecast was offered with no creature in play and failed every upkeep: gauntlet kw28); a sub-ability
     *  after a part that targets (Searing Blaze, Goblin Welder) is left to the cast, as before.
     *  Epoch 78 (2026-11-05, answer-tool-v16): under PROPOSE_ACTIONS the attack and the blocks are proposed by handle
     *  and checked by the engine (CombatUtil, blocksError), refused with the rule they break, four tries, then no attack
     *  or the fixed block fallback; the candidate lists are not shown. Off the arm one fix (pr-review of #349): the menu's
     *  tool check judges each block with the set's other blocks in place, so a block beside a "must be blocked if able"
     *  attacker's blocker is no longer refused (it was judged on a combat with no blocks yet).
     *  Epoch 79 (2026-11-05, pr-review of #349): a text-grammar BLOCKS: line is judged as a set before any block is added
     *  (the global cap, then each pair with the others in place, then blocksError), as a tool answer is; added one by one,
     *  a legal block written before the block that obeys a "must be blocked if able" requirement was dropped. A pair the
     *  blocker can never make (CombatUtil.canBlock without combat) is not used and leaves the name for the next pair.
     *  Epoch 80 (2026-11-05, answer-tool-v17): the PROPOSE_TERSE arm, under PROPOSE_ACTIONS only: a rules refusal of a
     *  play or a combat declaration says only that it is illegal, with no rule, to measure the message's share of
     *  repair. Unchanged off it.
     *  Epoch 81 (2026-11-05, answer-tool-v18): the PROPOSE_NOTEBOOK arm, under PROPOSE_ACTIONS only (REFUSED EARLIER;
     *  docs/harness/ARCHITECTURE.md, "The notebook arm"). Unchanged off it.
     *  Epoch 82 (2026-11-05, PR #359's engine labeller): the PROPOSE_ACTIONS checker's land copies and refusal order
     *  (docs/harness/ARCHITECTURE.md, "The propose condition"). Unchanged off it.
     *  Epoch 83 (2026-11-06, #367): the payment check also tries the "tap N untapped X you control" mana abilities the
     *  engine's greedy test skipped (seatCanPay, tapTypeStep), so a spell Heritage Druid's mana pays (Craterhoof with
     *  Priest of Titania, the Druid and a Forest) is on the menu and tagged castable, and the seat's payment makes it.
     *  Epoch 84 (2026-11-06, #380): "X target creatures" with X announced as 0 takes no target; one was set, and the
     *  stack refused the paid play (Rot-Curse Rakshasa's renew).
     *  Epoch 85 (2026-11-06, #392): a splice set the cast cannot pay together is asked again with its cost, then cast
     *  with no splice; a spliced cast that fails leaves the plain cast offered; a failed payment is told to the seat.
     *  Epoch 86 (2026-11-06, #391): a puzzle's solver is shown its own cards' cost, type line and rules text: the setup
     *  cards in <your_deck> (LlmSpikePuzzle.solverPlan), each later one as it is first seen (YOUR CARDS SINCE THE START,
     *  observation-faithful-v43). PUZ results before and after never pool.
     *  Epoch 87 (2026-11-06, #395): the target ask's preview of a modal spell plays the mode the seat chose
     *  (lookahead-preview-v16); every target of a chosen mode was "preview unavailable" ("failed to target").
     *  Epoch 88 (2026-11-06, #398): a puzzle's FACTS say a side has no combat left before the puzzle's last turn
     *  (observation-faithful-v44); recorded attempts passed citing a lethal "next combat" the game never reached.
     *  PUZ results before and after never pool.
     *  Epoch 89 (2026-11-06, #399): the answer tool's yield says the seat's own main phase asks again only once the stack
     *  is empty, and end-of-turn lasts through this turn's end step (answer-tool-v19). The text arm's YIELD_HINT is a
     *  legacy profile's bytes and keeps its words.
     *  Epoch 90 (2026-11-06, #366): a spell cast as another face counts its targets as that face (targetCandidates), as
     *  the engine casts it: a Disturb cast whose back face is an Aura (Lantern Bearer's Lanterns' Lift) was never on
     *  the menu, its "enchant creature" counted against the creature front face in the graveyard.
     *  Epoch 91 (2026-11-07, #368): a keyword granted to a player is said on its line ("| has hexproof"), and the
     *  engine's "Keyword Effects" object is left out of the command zone (observation-faithful-v45).
     *  Epoch 92 (2026-11-07, #408): the state guide says the player line shows the keywords the engine names; an
     *  unnamed one is not shown, the card or effect that gives it is (observation-faithful-v46).
     *  Epoch 93 (2026-11-07, #269): the puzzle prover (search arm) draws how many repeatable modes to choose, then a
     *  multiset, so "choose up to N" is tried with fewer modes (PuzzleArms.repeatModes); the random arm is unchanged.
     *  Epoch 94 (2026-11-07, #415): a pawprint charm's modes stay within its {P} budget: each mode's label shows its
     *  {P}, the question the budget, an answer over it is refused and asked again, and the arms draw within it.
     *  Epoch 95 (2026-11-07, #269): a source whose tap damage would be lethal pays after every other source, the
     *  sacrifice-cost ones included (ComputerUtilMana patch); it shared their list, unordered among non-creatures.
     *  Epoch 96 (2026-11-08, g33 T2): a harness seat may spend Black Lotus on any spell, an engine patch
     *  (ENGINE-PATCHES.md); a preview's copy pays as a harness seat (lookahead-preview-v17).
     *  Epoch 97 (2026-11-08, #432): a harness seat pays by the rules in five more places, the same engine patch
     *  (HARNESS_SEAT_PAYS_BY_RULES, once ..._MAY_TAKE_LETHAL_TAP_DAMAGE): a creature may be tapped for mana to pay
     *  for the pump spell that targets it, Basalt Monolith may pay for its own "{3}: Untap", a spell may be paid by
     *  sacrificing the source it targets, a card whose drawback a colour avoids (Court Hussar) is cast without that
     *  colour, and a Phyrexian-payment hint is not a refusal on the preview's copy; each is a play that was refused
     *  and is now on the menu or in the preview (lookahead-preview-v18).
     */
    static final String HARNESS_EPOCH = "harness-epoch-97";

    static final class EvalLog {
        private static volatile java.io.PrintWriter out; // volatile: on() reads unsynchronized from both seats
        private static volatile java.io.File file; // the exact file `out` is writing (LlmSpikeFork reads it back)
        /** Whether a row of the open log failed to write (disk full): PrintWriter swallows the IOException, so
         *  RowWriter asks checkError() after every row (issue #427). The game line records it
         *  (eval_log_write_failed) and every scorer refuses the game, since its log may be cut. */
        private static volatile boolean writeFailed;
        private static volatile String logName = "";
        /** The log's writer: each row is followed by checkError(), which PrintWriter otherwise never surfaces. (Not
         *  write(): eval/demo/demo_compare.py ports write() whole into older overlays and its test pins the text.) */
        private static final class RowWriter extends java.io.PrintWriter {
            RowWriter(java.io.Writer w) { super(w, true); }
            @Override public void println(String row) {
                super.println(row);
                if (!writeFailed && checkError()) {
                    writeFailed = true;
                    System.out.println("[EVAL] write failed: " + logName + " (rows may be lost or cut; the game is not scored)");
                }
            }
        }
        static synchronized void init(long seed) {
            String dir = System.getenv("EVAL_OUT");
            if (dir == null || dir.isEmpty()) return;
            initToDir(dir, seed);
        }
        /** Open the per-game jsonl under {@code dir} (env-independent). Production {@link #init} reads
         *  EVAL_OUT; LlmSpikeTest drives this directly since it cannot set an env var in-process. */
        static synchronized void initToDir(String dir, long seed) {
            if (out != null) return;
            writeFailed = false;
            try {
                java.io.File d = new java.io.File(dir);
                d.mkdirs();
                file = new java.io.File(d, "game-" + seed + "-" + System.currentTimeMillis() + ".jsonl");
                logName = file.getName();
                out = new RowWriter(new java.io.FileWriter(file, true));
                // run_batch.py reads this from the game's log to set a crashed attempt's file aside: the
                // name carries the seed but not the seat swap, and both swaps of a seed can run at once.
                System.out.println("[EVAL] jsonl " + file.getName());
            } catch (Exception e) { System.out.println("[EVAL] log init failed: " + e); }
        }
        /** The game log on, its rows written nowhere: a step search (LlmSpikePuzzle.stepSearch) checks each replayed row
         *  through the fork hook, which only a written row reaches, over hundreds of rollouts nobody reads. */
        static synchronized void initDiscard() {
            if (out != null) return;
            writeFailed = false;
            out = new java.io.PrintWriter(java.io.Writer.nullWriter());
        }
        /** Test hook: the log writes to {@code w} (one that throws, to stand in for a full disk). */
        static synchronized void initToWriter(java.io.Writer w, String name) {
            if (out != null) return;
            writeFailed = false;
            logName = name;
            out = new RowWriter(w);
        }
        static synchronized void write(String json) {
            if (out == null) return;
            if (json.startsWith("{\"t\":\"decision\"")) DemoFeed.beforeDecisionRow();  // display only (DEMO_FEED)
            out.println(json);
        }
        static boolean writeFailed() { return writeFailed; }
        static boolean on() { return out != null; }
        /** The file currently being written (null if EVAL_OUT unset). LlmSpikeFork's check mode reads
         *  this back after the game ends to compare the replayed decision stream against FORK_SOURCE. */
        static java.io.File currentFile() { return file; }
        /** Flush + detach the writer so a subsequent {@link #init}/{@link #initToDir} can open a fresh
         *  file. Originally test-only; LlmSpikeFork's rollout mode also uses it deliberately (one fresh
         *  per-game jsonl per rollout replay within a single JVM) — it is exactly flush+detach. */
        static synchronized void closeForTest() {
            if (out != null) { out.flush(); out.close(); out = null; file = null; }
        }
    }

    /** Fork-mode extension point (LlmSpikeFork, Milestone D — eval/EVAL-DESIGN.md §3.1). Null in
     *  every normal run (GUI, headless, tests) — zero overhead, zero behavior change. When
     *  LlmSpikeFork sets it before starting a game, {@link #emitDecisionRow} calls it synchronously
     *  (today, the fork-mode game runs on a single worker thread — see LlmSpikeFork#playDeterministicGame
     *  — so "synchronously" here means "inline with that one thread's call to emitDecisionRow", not a
     *  tested guarantee across concurrently-deciding seats), immediately after each decision row is
     *  written, with a 0-based index in EMISSION order (== the order decision rows land in the jsonl
     *  file, since EvalLog.write is synchronized and both seats' calls funnel through one writer) —
     *  the same "merged decision stream" index FORK_DECISION_INDEX addresses. */
    interface ForkHook {
        void onDecision(int index, Player pl, String seat, String type, int nOptions,
                int choice, List<String> options);
    }
    static volatile ForkHook FORK_HOOK = null;
    static final java.util.concurrent.atomic.AtomicInteger FORK_DECISION_SEQ =
            new java.util.concurrent.atomic.AtomicInteger(-1);

    // ==================== FORK ROLLOUT FORCER (Milestone D rollout — additive, inert) ====================
    // Everything in this fenced block is a no-op in every normal run: FORK_FORCER is null unless
    // LlmSpikeFork's rollout mode, or the keyword sweep (KeywordSurfaceSweepTest.chooseFromMenu), installs
    // one, and forkForce() returns null immediately in that case.
    // The seed-42 floor/floor canary is the regression gate proving inertness.

    /** Fork-mode decision forcer (LlmSpikeFork rollout, EVAL-DESIGN.md §3.1(b)). Consulted by exactly
     *  two decision sites per controller — chooseSpellAbilityToPlay ("play") and mulliganKeepHand
     *  ("mulligan"), in both PlayerControllerLLM and PlayerControllerAiLogging — the only v1 types
     *  whose logged {@code choice} is index-encoded AND whose forced index maps unambiguously to an
     *  engine object. Return {@code >= 0} to force that 0-based index into {@code options} (for
     *  "play" the trailing PASS slot is a valid index), {@code -1} to force PASS/decline/KEEP, or
     *  {@code null} to decide normally. */
    interface ForkForcer {
        Integer forceChoice(int index, Player pl, String seat, String type, int nOptions, List<String> options);
    }
    static volatile ForkForcer FORK_FORCER = null;

    /** Consult FORK_FORCER with the decision index {@code emitDecisionRow} is ABOUT to assign
     *  ({@code FORK_DECISION_SEQ.get() + 1}). This is sound ONLY because fork-mode games run on a
     *  single worker thread (LlmSpikeFork.playDeterministicGame): between this call and the same
     *  decision's own emitDecisionRow no other row can be emitted on that thread. That is a stated
     *  limitation of the fork harness, not a tested cross-thread guarantee — do not install a forcer
     *  in a run where two seats can decide concurrently. Null forcer (every normal run) returns null
     *  with zero side effects. */
    static Integer forkForce(Player pl, String seat, String type, int nOptions, List<String> options) {
        ForkForcer f = FORK_FORCER;
        if (f == null) return null;
        return f.forceChoice(FORK_DECISION_SEQ.get() + 1, pl, seat, type, nOptions, options);
    }

    /** Deterministic first-legal targeting for a fork-FORCED SpellAbility on an ai-log seat. The AI
     *  cast path does no targeting for ordinary spells it did not itself evaluate, and an untargeted
     *  cast fizzles ("Couldn't add to stack, failed to target") — this is the engine gotcha
     *  PlayerControllerLLM.ensureTargetsSet exists for. That method is instance-private and logs
     *  through the LLM seat's evalDec; this static mirror applies only its deterministic core (first
     *  legal candidate, topped up to minTargets), no model call, no jsonl row. The first-legal pick
     *  is a rollout-POLICY caveat (targets are not optimized), matching the floor arm's own
     *  "target(no-model)->first" behavior; verify_keys.py surfaces it via rollout caveats. Called
     *  only from the fork-forced play path in PlayerControllerAiLogging — inert otherwise. */
    static void forceTargetsFirstLegal(Player activator, SpellAbility root) {
        for (SpellAbility sa = root; sa != null; sa = sa.getSubAbility()) {
            try {
                if (!sa.usesTargeting()) continue;
                if (sa.getTargets() != null && !sa.getTargets().isEmpty()) continue; // already targeted
                if (sa.getActivatingPlayer() == null) sa.setActivatingPlayer(activator);
                if (PlayerControllerLLM.noTargetAtX(root, sa)) continue;
                TargetRestrictions tr = sa.getTargetRestrictions();
                if (tr == null) continue;
                List<forge.game.GameObject> cands = PlayerControllerLLM.targetCandidates(sa, tr);
                if (cands == null || cands.isEmpty()) continue; // no legal target -> let engine cancel/handle
                int min = Math.max(1, sa.getMinTargets());
                sa.resetTargets();
                sa.getTargets().add(cands.get(0));
                for (int i = 1; sa.getTargets().size() < min && i < cands.size(); i++)
                    sa.getTargets().add(cands.get(i));
            } catch (Exception ignore) { /* leave targeting to the engine for this ability */ }
        }
    }
    // ==================== end FORK ROLLOUT FORCER block ====================

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
    /** Set when the game thread died on an exception: the game did not finish, so it has no winner. */
    static volatile String gameError = null;

    /** The winner a game line records: none for a capped game or one whose thread crashed (runs/luna-v8/g2
     *  recorded a StackOverflowError as a draw). */
    static String finalWinner(String winner, boolean capped, String error) {
        return capped || error != null ? "invalid" : winner;
    }
    static volatile String lastPlanB = DEFAULT_PLAN_B;

    // What ACTUALLY answered, keyed by bridge label ("<deck-identity>/smart" | "<deck-identity>/fast").
    // Stashed by OpenRouterBridge on every successful reply, read by writeGameSummary.
    //
    // The CONFIGURED spec is not an identity. `SMART_MODEL=openrouter:default_model` against a local
    // mlx server names no checkpoint at all: runs/stage1-student-eval and runs/stage2-student-eval
    // were played by different adapters and are byte-identical in provenance, so nothing in either
    // artifact says which model earned which number. Only the serving endpoint knows, and it tells us
    // in the response's own `model` field — so that is what we record, alongside the spec we asked for.
    static final Map<String, String> servedModels = new ConcurrentHashMap<>();

    // Issue #369: per deck identity (the seat's name, as above), the decisions whose model ask failed in transport after
    // every retry (LLMBridge.lastFailureWasTransport: a 402 when the credits ran out, a 401, an outage), and the trips of
    // the circuit breaker. Each took a fixed legal fallback, so the game is not the model's: aggregate.py counts it
    // invalid, panel_score.py and rating.py refuse it, defect_analyzer.py fails on it.
    static final Map<String, Integer> bridgeDown = new ConcurrentHashMap<>();
    static final Map<String, Integer> breakerTrips = new ConcurrentHashMap<>();
    static int bridgeDownDecisions(String seat) { return bridgeDown.getOrDefault(seat, 0); }
    static int circuitBreakerTrips(String seat) { return breakerTrips.getOrDefault(seat, 0); }

    /** PZL axis: which seat is the puzzle SOLVER, or null outside puzzle mode.
     *
     *  Forge decides who receives the "Puzzle Goal" enforcement card by asking each controller
     *  {@code isGuiPlayer()} (Puzzle.java:119) — the only call site of that method in the entire
     *  engine, verified by grep at the pinned commit. Our controllers extend PlayerControllerAi,
     *  whose base returns false, so a headless puzzle would find no solver, leave `human` null and
     *  NPE inside addGoalEnforcement before the first turn. Rather than fork Puzzle.java (which
     *  would make the adjudicator a COPY of the engine's and free to drift from it — the one thing
     *  the PZL axis must not allow), we point that single predicate at the seat LlmSpikePuzzle
     *  designates, and Forge's own goal enforcement runs verbatim.
     *
     *  Null outside puzzle mode, so every non-puzzle run is byte-identical to before. */
    static volatile LobbyPlayer puzzleSolverSeat = null;

    /** PZL axis: the solver's brief ({@link LlmSpikePuzzle#seatBrief}: a Forge puzzle's goal description, a rules
     *  puzzle's turn limit and description), stated in the solver's system prompt in place of "win the game". Null
     *  outside puzzle mode. */
    static volatile String puzzleBrief = null;

    /** PZL axis: the last turn the puzzle's game can be played in ({@link LlmSpikePuzzle#lastTurn}), so the seat's
     *  FACTS never promise a combat after it (#398). -1 outside puzzle mode. */
    static volatile int puzzleLastTurn = -1;

    /** True only for the seat {@link #puzzleSolverSeat} names. Identity comparison, not name
     *  equality: two seats may share a display name. */
    static boolean isPuzzleSolver(LobbyPlayer lp) {
        LobbyPlayer solver = puzzleSolverSeat;
        return solver != null && lp == solver;
    }

    /** Whether a seat's state records the text of its own cards first seen after setup (SeatObservation, YOUR CARDS
     *  SINCE THE START): only a puzzle's solver, whose deck text lists just its setup cards, and not under DECKLIST=0,
     *  the opt-out from own-card text. An empty setup block is no reason to stop (a solver with only a token at setup
     *  still draws cards it must read); a [state] that could not be read is logged and tracks, so its visible cards
     *  are shown once, as new. A game seat's deck text lists every card it can hold; a puzzle opponent's seat is not
     *  the solver. */
    static boolean tracksOwnCards(boolean puzzleSolver) {
        return puzzleSolver && !"0".equals(env("DECKLIST"));
    }

    /** Our agent's controller: extend the AI, route decisions to Claude, delegate to super on
     *  anything we don't handle or that fails. One instance per seat — all state is per-instance. */
    /**
     * The model's WINDOW ONTO THE GAME, as a named, versioned, hashable object.
     *
     * WHY THIS EXISTS. Everything the model knows arrives through
     * {@link PlayerControllerLLM#buildSnapshot}, and until now its shape was a
     * scatter of hardcoded literals: 10 recent events, 16 intel facts, 30 yard
     * names, 25 cast-history names. Those numbers ARE the measurement — change
     * one and every score before and after is measuring a different agent — yet
     * the game-summary line stamped 30+ provenance fields and NOT ONE of them
     * recorded any of it. {@code overlay_sha} moves when the Java moves, but it
     * is far too coarse to say WHICH knob changed, and it reads "unknown" on any
     * bare {@code run-llm-headless.sh} launch.
     *
     * That is the 88%-lesson failure mode with the detector removed: two runs
     * that cannot be pooled, and no field that says so. So context shape is now
     * a first-class, named profile, stamped as {@code context_profile} +
     * {@code context_sha} on every game summary. Two runs on different profiles
     * are DIFFERENT MEASUREMENTS and the provenance now says which.
     *
     * THE DEFAULT IS {@code v1-compat} AND IT IS BYTE-IDENTICAL TO THE PRE-PROFILE
     * HARNESS. Every literal below is the value that was hardcoded at the call
     * site it replaces, so the seed-42 canary, the ai-teacher prompt pairing
     * (which needs student/teacher prompts byte-equal), and every artifact on
     * disk are untouched. A profile is opt-in via CONTEXT_PROFILE.
     *
     * Selected by {@code CONTEXT_PROFILE=<name>}; individual knobs can be
     * overridden by env for experiments, and any override is folded into the sha
     * so a tweaked profile can never masquerade as the named one.
     */
    static final class ContextProfile {
        /** Sentinel for {@link #recentEvents}: narrate EVERY logged event since this seat's previous
         *  decision, however many that is, instead of a fixed-size tail. See the field doc. */
        static final int EVENTS_SINCE_LAST_DECISION = -1;

        final String name;
        /** How many engine log entries to narrate as RECENT EVENTS.
         *
         *  v1-compat pins 10, which is the single worst information defect in the harness: a Legacy
         *  game here runs to 108 turns (the seed-42 canary), and more than ten loggable events
         *  routinely occur between two of ONE seat's priority windows — a single combat step can
         *  spend that budget. The model is then asked to act on a board whose recent past it
         *  literally cannot see, while the system message tells it "the state snapshot is
         *  authoritative: trust it over your memory of earlier turns." Both halves of that sentence
         *  are load-bearing and the second one is only safe if the first is complete.
         *
         *  {@link #EVENTS_SINCE_LAST_DECISION} closes it by narrating exactly the events this seat
         *  has not yet been shown, which is the quantity the prose always claimed to deliver. */
        final int recentEvents;
        /** Hard ceiling on the since-last-decision window, so a pathological gap (a long opponent
         *  combo turn) cannot blow the context budget. Only consulted when recentEvents is the
         *  sentinel. */
        final int recentEventsMax;
        /** INTEL deque capacity — durable learned facts (revealed hands, named cards). */
        final int intelCap;
        /** Max graveyard names rendered per player. */
        final int yardCap;
        /** Max exile names rendered per player. */
        final int exileCap;
        /** Max distinct names in OPP CAST THIS GAME / OPP LANDS PLAYED. */
        final int castHistoryCap;
        /** At what resolution the past is rendered. See {@link GameHistory.Mode}. */
        final GameHistory.Mode historyMode;
        /** TIERED only: how many of the most recent turns get a FULL per-turn digest before the
         *  harder high-signal-only tier takes over. */
        final int digestTurns;
        /** TIERED only: max distinct entries per digest line, before "...(N more)". */
        final int digestPerTurn;
        /** TIERED only: how many consecutive old turns may share one hard-tier line. Forty turns of
         *  one line each is not a summary, it is the log again. */
        final int compactTurnsPerLine;
        /** Which observation renderer feeds the prompt. {@code false} = the legacy buildSnapshot
         *  path (engine {@code @id}s, capped zones, global log tail). {@code true} = the viewer-aware
         *  {@link SeatObservation} path: seat-local handles, visibility-gated identities, seat-captured
         *  narration with private reveals, full permitted zones. This is a different measurement,
         *  so it is part of the profile identity below. */
        final boolean faithfulObservation;

        private ContextProfile(String name, int recentEvents, int recentEventsMax, int intelCap,
                               int yardCap, int exileCap, int castHistoryCap,
                               GameHistory.Mode historyMode, int digestTurns, int digestPerTurn,
                               int compactTurnsPerLine) {
            this(name, recentEvents, recentEventsMax, intelCap, yardCap, exileCap, castHistoryCap,
                 historyMode, digestTurns, digestPerTurn, compactTurnsPerLine, false);
        }

        private ContextProfile(String name, int recentEvents, int recentEventsMax, int intelCap,
                               int yardCap, int exileCap, int castHistoryCap,
                               GameHistory.Mode historyMode, int digestTurns, int digestPerTurn,
                               int compactTurnsPerLine, boolean faithfulObservation) {
            this.name = name;
            this.recentEvents = recentEvents;
            this.recentEventsMax = recentEventsMax;
            this.intelCap = intelCap;
            this.yardCap = yardCap;
            this.exileCap = exileCap;
            this.castHistoryCap = castHistoryCap;
            this.historyMode = historyMode;
            this.digestTurns = digestTurns;
            this.digestPerTurn = digestPerTurn;
            this.compactTurnsPerLine = compactTurnsPerLine;
            this.faithfulObservation = faithfulObservation;
        }

        /** The pre-profile harness, literal for literal. Do not change these numbers: they are the
         *  definition of every measurement taken before profiles existed. To try something else,
         *  ADD a profile. */
        static final ContextProfile V1_COMPAT =
                new ContextProfile("v1-compat", 10, 10, 16, 30, 30, 25,
                                   GameHistory.Mode.FIXED_TAIL, 0, 0, 0);

        /** Closes the amnesia: the model sees every event since its own previous decision (capped at
         *  60 so one enormous opponent turn cannot dominate the prompt), and remembers twice as many
         *  durable facts. Deliberately changes NOTHING else — a profile that moves five knobs at once
         *  cannot tell you which one moved the number. */
        static final ContextProfile V2_MEMORY =
                new ContextProfile("v2-memory", EVENTS_SINCE_LAST_DECISION, 60, 32, 30, 30, 25,
                                   GameHistory.Mode.SINCE_LAST_DECISION, 0, 0, 0);

        /** The resolution ladder: recent events verbatim, the last 3 turns digested to a line each,
         *  everything older collapsed into high-signal-only lines covering up to 5 turns apiece.
         *  Unlike v2-memory this has no horizon at all — turn 1 of a 100-turn game is still
         *  represented, just cheaply — while reducing growth relative to the full log (not a hard size bound). Every digest carries the
         *  canonical E-id range it covers, so the compaction is visible to the model rather than
         *  looking like events that never happened. */
        static final ContextProfile V3_COMPACT =
                new ContextProfile("v3-compact", EVENTS_SINCE_LAST_DECISION, 40, 32, 30, 30, 25,
                                   GameHistory.Mode.TIERED, 3, 12, 5);

        /** The viewer-aware path (2026-09-06): every prompt is rendered by {@link SeatObservation}
         *  from what this seat may legally see. Zones are rendered in full (the caps are effectively
         *  off, and any cap that does fire is printed as an explicit omission), history is the
         *  seat-captured stream on the v3 ladder, and object handles are seat-local. The legacy
         *  profiles above are untouched; this is a separate measurement with its own sha.
         *
         *  2026-10-15 (docs/harness/MODEL-LIMITS.md): the history is whole. Every event since the
         *  seat's last decision is verbatim (40 bound in 11 asks of runs/compare and runs/frontier),
         *  and every earlier turn is a full digest line, repeats counted, nothing dropped: the 12-entry
         *  digest cut fired 1,431 times and turns older than three kept only high-signal events. */
        static final ContextProfile V4_FAITHFUL =
                new ContextProfile("v4-faithful", EVENTS_SINCE_LAST_DECISION, 10000, 64, 10000, 10000, 10000,
                                   GameHistory.Mode.TIERED, 10000, 10000, 5, true);

        private static final Map<String, ContextProfile> REGISTRY;
        static {
            Map<String, ContextProfile> m = new java.util.LinkedHashMap<>();
            m.put(V1_COMPAT.name, V1_COMPAT);
            m.put(V2_MEMORY.name, V2_MEMORY);
            m.put(V3_COMPACT.name, V3_COMPACT);
            m.put(V4_FAITHFUL.name, V4_FAITHFUL);
            REGISTRY = java.util.Collections.unmodifiableMap(m);
        }

        /** Registered profile names, in declaration order (for the unknown-name error message). */
        static java.util.Set<String> names() { return REGISTRY.keySet(); }

        /** Package-private test seam (same idiom as PlayerControllerLLM's injected-bridge ctor):
         *  build an arbitrary profile so the renderer's cap/gap arithmetic can be driven at its
         *  boundaries without adding throwaway entries to the REGISTRY, which is the operator-facing
         *  list of real, quotable profiles. */
        static ContextProfile forTest(String name, GameHistory.Mode mode, int recentEvents,
                                      int recentEventsMax, int digestTurns, int digestPerTurn,
                                      int compactTurnsPerLine) {
            return new ContextProfile(name, recentEvents, recentEventsMax, 16, 30, 30, 25,
                                      mode, digestTurns, digestPerTurn, compactTurnsPerLine);
        }

        /** Resolve CONTEXT_PROFILE, then apply any per-knob env overrides.
         *
         *  An UNKNOWN name is a hard failure, not a silent fallback to the default: a typo'd
         *  CONTEXT_PROFILE that quietly ran v1-compat would produce a run whose provenance says
         *  v1-compat and whose operator believes it is something else — the precise class of
         *  undetectable mismatch this whole type exists to prevent. */
        static ContextProfile fromEnv() {
            return resolve(System.getenv("CONTEXT_PROFILE")).withEnvOverrides();
        }

        /** Name -> profile, with the rejection rule. Separate from {@link #fromEnv()} purely so the
         *  fatal-on-unknown behavior — this type's core safety claim — is reachable from a test,
         *  which cannot mutate the process environment. */
        static ContextProfile resolve(String want) {
            if (want == null || want.trim().isEmpty()) return V1_COMPAT;
            ContextProfile base = REGISTRY.get(want.trim());
            if (base == null) {
                throw new IllegalArgumentException("unknown CONTEXT_PROFILE '" + want.trim()
                        + "' — known profiles: " + String.join(", ", names()));
            }
            return base;
        }

        /** Per-knob env overrides for experiments. A profile carrying any override reports its name
         *  with a "+ovr" suffix AND a different sha, so an overridden run can never be pooled with
         *  the clean profile by either identifier. */
        private ContextProfile withEnvOverrides() {
            int re  = envInt("CTX_RECENT_EVENTS", recentEvents);
            int rem = envInt("CTX_RECENT_EVENTS_MAX", recentEventsMax);
            int ic  = envInt("CTX_INTEL_CAP", intelCap);
            int yc  = envInt("CTX_YARD_CAP", yardCap);
            int ec  = envInt("CTX_EXILE_CAP", exileCap);
            int cc  = envInt("CTX_CAST_HISTORY_CAP", castHistoryCap);
            int dt  = envInt("CTX_DIGEST_TURNS", digestTurns);
            int dp  = envInt("CTX_DIGEST_PER_TURN", digestPerTurn);
            int ct  = envInt("CTX_COMPACT_TURNS_PER_LINE", compactTurnsPerLine);
            if (re == recentEvents && rem == recentEventsMax && ic == intelCap
                    && yc == yardCap && ec == exileCap && cc == castHistoryCap
                    && dt == digestTurns && dp == digestPerTurn && ct == compactTurnsPerLine) {
                return this;
            }
            return new ContextProfile(name + "+ovr", re, rem, ic, yc, ec, cc,
                                      historyMode, dt, dp, ct, faithfulObservation);
        }

        private static int envInt(String k, int def) {
            try { String v = System.getenv(k); return v == null || v.trim().isEmpty() ? def : Integer.parseInt(v.trim()); }
            catch (Exception e) { return def; }
        }

        /** Canonical serialization — the sha's preimage. Every knob appears, in a fixed order, so
         *  adding a knob later necessarily changes the sha of every profile (which is correct: a
         *  harness with a new context dimension is not the old harness). */
        String canonical() {
            // context.v2 adds the history-mode dimension. The schema version is part of the
            // preimage on purpose: a harness with a new context dimension is not the old harness,
            // so every profile's sha moves. v1-compat's RENDERING is unchanged across that bump —
            // only its identity string is — and CLAUDE.md records the old sha so artifacts written
            // under context.v1 stay matchable.
            //
            // context.v3 adds the observation dimension. Unlike the v2 bump it is applied ONLY to
            // profiles that use the faithful renderer: every legacy profile keeps its context.v2
            // preimage byte-for-byte, so the context_sha stamped on every artifact on disk still
            // matches, while no faithful profile can ever collide with a legacy one.
            String body = "name=" + name
                    + "|history_mode=" + historyMode
                    + "|recent_events=" + recentEvents
                    + "|recent_events_max=" + recentEventsMax
                    + "|intel_cap=" + intelCap
                    + "|yard_cap=" + yardCap
                    + "|exile_cap=" + exileCap
                    + "|cast_history_cap=" + castHistoryCap
                    + "|digest_turns=" + digestTurns
                    + "|digest_per_turn=" + digestPerTurn
                    + "|compact_turns_per_line=" + compactTurnsPerLine;
            return faithfulObservation
                    ? "context.v3|" + body + "|observation=" + SeatObservation.version()
                    : "context.v2|" + body;
        }

        /** 12-hex sha256 over {@link #canonical()} — same width/idiom as modelCfgSha(). */
        String sha() {
            try {
                byte[] d = java.security.MessageDigest.getInstance("SHA-256")
                        .digest(canonical().getBytes(StandardCharsets.UTF_8));
                StringBuilder sb = new StringBuilder();
                for (int i = 0; i < 6; i++) sb.append(String.format("%02x", d[i]));
                return sb.toString();
            } catch (Exception e) { return "unknown"; }
        }

        @Override public String toString() { return name + "/" + sha(); }
    }

    static class PlayerControllerLLM extends PlayerControllerAi {
        // Engine patch (docs/harness/ENGINE-PATCHES.md): Forge's AI payment fixed the colour an any-colour source
        // (Lotus Petal, LED) makes for a generic shard before the colour question. For a model seat it is left
        // open, so chooseColor below asks the seat; the opponent AI and the zero-model arms are unchanged.
        static {
            forge.ai.ComputerUtilMana.HARNESS_SEAT_CHOOSES_ANY_COLOR = p -> p.getController() instanceof PlayerControllerLLM
                    && ((PlayerControllerLLM) p.getController()).choosesOwnColours();
            // Every harness seat, and a preview's copy of the seat (PlayerLookahead.isCopySeat), is spared Forge AI's
            // tastes in payment (ENGINE-PATCHES.md): a lethal-tap-damage source (RP_017), the ability's own card
            // as a source (RP_175), Black Lotus on a cheap spell, and the five of #432 (ENGINE-PATCHES.md, audit table).
            forge.ai.ComputerUtilMana.HARNESS_SEAT_PAYS_BY_RULES = p -> p.getController() instanceof PlayerControllerLLM
                    || PlayerLookahead.isCopySeat(p);
        }
        boolean choosesOwnColours() { return "llm".equals(mode) && rnd == null; }
        private static final int MAX_PLAY_DECISIONS_PER_TURN = 25;
        /** The same loop guard under the faithful profile. It counts both players' windows in a turn, so
         *  25 stopped Storm, Doomsday and Elves turns partway through. 500 (was 80; the most a recorded
         *  turn asked was 15) only ends a seat that loops on one ability. */
        static final int MAX_PLAY_DECISIONS_PER_TURN_FAITHFUL = 500;
        /** Per-game model-call budgets. Loop guards, not decision limits: the most a recorded seat used
         *  was 137 (runs/compare one-tier, 17 turns), and a seat out of budget gets the deterministic
         *  pass. 10000 is far beyond any game a wall-clock cap lets finish (was 320 / 900). */
        static final int SMART_BUDGET = envInt("SMART_BUDGET", 10000);
        static final int FAST_BUDGET  = envInt("FAST_BUDGET", 10000);
        // Eval confound switches: MULLIGAN_HEURISTIC=1 -> the harness keeps 2-4 land hands without asking;
        // SKIP_HEURISTICS=1 -> free-pass shortcuts on windows where the model has options.
        // Owner decision 2026-10-15: no heuristic decides for the model. Both shortcuts are off unless set
        // to 1, for measuring them; the only skips left are the engine's (PASS is the only legal action) and
        // an identical spot already passed (last-sig).
        private static final boolean MULL_HEURISTIC = "1".equals(System.getenv("MULLIGAN_HEURISTIC"));
        // Not final: tests turn it off in-process (an env var cannot be set there).
        static boolean SKIP_HEUR = "1".equals(System.getenv("SKIP_HEURISTICS"));
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
        /** The smart tier's bridge, for the tests of the seat's gates. */
        LLMBridge smartBridge() { return smart; }
        private final LLMBridge fast;     // fast tier — confirms, numbers, cleanup
        private int smartCalls = 0, fastCalls = 0;
        /** The [TALLY] line's mean latency per tier that answered. One tier is one bridge for both (#251): its mean is
         *  printed once, as avgSmart, not again as an avgFast for a tier that made no call. */
        static String latencyAverages(LLMBridge smart, LLMBridge fast) {
            return (smart.getCalls() > 0 ? " avgSmart=" + (smart.getTotalMs() / smart.getCalls()) + "ms" : "")
                    + (fast != smart && fast.getCalls() > 0 ? " avgFast=" + (fast.getTotalMs() / fast.getCalls()) + "ms" : "");
        }
        private int lostToRouting = 0;   // decisions lost after a routing-filter refusal among their retries (budget())
        private int lostToTransport = 0; // decisions lost to a dead bridge (the bridgeDown rule, issue #369; budget())
        /** What this seat's model calls spent in the game: tokens as the provider reported them (an unreported count
         *  adds nothing) and wall time, the retries included. A puzzle result row carries them (budget()). */
        private long spentPromptTokens = 0, spentCompletionTokens = 0, spentModelMs = 0;
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
        // Of lastPromptTokens: read from / written to the provider's prompt cache; -1 when unreported.
        private int lastCachedTokens = -1;
        private String lastProvider = null;
        private int lastCacheWriteTokens = -1;
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
        /** The 0-based option indices a multi-pick decision chose, for the decision row. `choice` on
         *  such a row is the COUNT of picks, so without this the row never said which were picked. */
        private List<Integer> lastPicks = null;
        /** A play answer's `keep_untapped` (handles), for the payment of the play it chose; null when not given. */
        private List<Integer> pendingKeep = null;
        private SpellAbility pendingKeepFor = null;
        private void clearLast() {
            lastPrompt = null; lastReply = null; lastTier = null; lastAskMs = 0; lastFinishReason = null;
            lastPromptTokens = -1; lastCompletionTokens = -1; lastCachedTokens = -1; lastCacheWriteTokens = -1;
            lastSuccessMs = 0; lastRetryMs = 0; lastAttempts = 0; lastFirstFailedReply = null; lastDeclineLogged = false;
            lastShadowChoice = null; lastPicks = null;
            nextAnswer = null; lastAnswer = null; answerVia = null; answerInvalid = 0;
        }

        // per-turn play-loop state (was static in v1 — now per-instance so two seats don't collide)
        private int lastPlayTurn = -1;
        private int playDecisionsThisTurn = 0;
        private String lastAskSig = "";
        private boolean lastWasPass = false;
        /** The play the model chose most recently (cast or activate) with its own words for it, shown
         *  on every later ask that turn whichever tier answers it (faithful profile). The steps of one
         *  action are separate asks, often on different tiers with no shared conversation: runs/loop/g07's
         *  smart tier activated Wirewood Symbiote "to untap Reclamation Sage so it can block", and the
         *  fast tier, asked for the target, called the activation pointless and untapped the opponent's
         *  creature; g10's fast tier undid the Ponder order the smart tier had just chosen. */
        private String actionIntent = null;
        private int actionIntentTurn = -1;
        /** Cancels in the current priority window (a cancelled play re-asks the menu, at most twice). */
        private String cancelKey = "";
        private int cancels = 0;
        /** Plays that failed to pay in the current priority window: not offered again in it. Forge's AI skips a
         *  failed ability the same way (SpellAbility.setSkip); the menu did not, and the keyword gauntlet's seat was
         *  offered the same unpayable craft 999 times in one window. */
        private final java.util.Set<String> failedInWindow = new java.util.HashSet<>();
        static String failedPlayKey(SpellAbility sa) {
            return sa.getHostCard().getId() + "|" + sa.getAlternativeCost() + "|" + sa.getCardStateName() + "|" + sa.getDescription();
        }
        static boolean hasSplices(SpellAbility sa) { return sa.getSplicedCards() != null && !sa.getSplicedCards().isEmpty(); }
        /** The key of {@code play} cast with {@code splices} spliced onto it: its failure is that set's, not the card's. */
        static String spliceKey(String play, Iterable<Card> splices) {
            List<Integer> ids = new ArrayList<>();
            for (Card c : splices) ids.add(c.getId());
            java.util.Collections.sort(ids);
            return play + "|splice " + ids;
        }
        private String cancelNote = null;
        /** Set by {@link LlmCostDecision} for the one cost ask it is making when that ask may be answered
         *  "cancel" (a play not committed yet: CostPayment decides every part before paying any); the ask
         *  clears it, and a cancel answer sets {@link #costCancelled} for the cost decision to read. */
        boolean offerCostCancel = false;
        boolean costCancelled = false;
        /** A pass the MODEL asked for (`YIELD:` on a PASS, offered when SKIP_HEURISTICS=0 under the
         *  faithful profile): until it ends, windows with nothing of the opponent's on the stack are
         *  passed for the seat and logged as `yield:` window rows. This replaces the harness's own
         *  guesses about which windows matter (the upkeep/draw/end-step gate, the own-stack pass, the
         *  interaction filter) with the seat's. Anything the opponent casts or activates still asks. */
        private String yieldUntil = null;
        private int yieldTurn = -1;
        static final String YIELD_HINT = "If you PASS, you may also add a line `YIELD: end-of-turn` or `YIELD: my-next-turn`: "
                + "until then you are not asked in windows where nothing of your opponent's is on the stack "
                + "(anything they cast or activate always asks you; your own main phases always ask too). ";
        /** {@link #YIELD_HINT} for the answer tool: a field of the answer, not a line. */
        static final String YIELD_TOOL_HINT = "If you PASS, you may also give `yield` in your `answer` (end-of-turn or my-next-turn): "
                + "until then you are not asked in windows where nothing of your opponent's is on the stack "
                + "(anything they cast or activate always asks you; your own main phase asks again once the stack is empty; "
                + "end-of-turn lasts through this turn's end step). ";

        /** The play ask's notes under the answer tool: the plan through `set_plan`, the cast list, the
         *  card-interactions note and the opponent read as fields of `answer`. No line is asked for. */
        static String playToolAsks(boolean askPlan, boolean askLines, boolean askProfile) {
            StringBuilder b = new StringBuilder();
            if (askPlan) b.append("First set your plan with `set_plan` (goal: what you are working toward; next: the steps you ")
                    .append("intend, in order, e.g. <land>, cast <card>, hold <card> for their turn). It is a note, not a commitment: ")
                    .append("every later decision is yours on the state then, and you replace the plan when it no longer fits. ");
            // Steps are offered only where PLAN_EXECUTE carries them out; otherwise the offer would be false.
            if (askPlan && PLAN_EXECUTE) b.append("If (and only if) you want the harness to cast steps of that plan for you without ")
                    .append("asking again this turn, give `steps` in your `answer`: ONLY the CAST/ACTIVATE option numbers you intend ")
                    .append("to use, in order; omit anything you are holding. ");
            if (askLines) b.append("Also give `lines` in your `answer`: how the cards in YOUR OWN deck combine (<card> + <card> -> ")
                    .append("<what you do and what you get>, up to 3, each naming two or more of your own cards from the decklist above). ");
            if (askProfile) b.append("Also give `profile` in your `answer`: your read of the OPPONENT in one short line (their ")
                    .append("deck/plan and the cards to respect, e.g. '<archetype>: <their key cards>, respect <card>'). ");
            return b.toString();
        }
        /** A Charm spell whose modes the play loop already chose, so the cast does not ask again. */
        private SpellAbility modesChosenFor = null;
        /** Priority windows the play loop passed without a model call, by reason (see skipWindow). */
        final java.util.Map<String, Integer> windowSkips = new java.util.TreeMap<>();
        // (stack, options) situations we already declined this turn — identical recurrences pass free.
        private final java.util.HashSet<String> passedReactSigs = new java.util.HashSet<>();
        // Turn plan: stated by the model at its first main-phase decision of the turn, injected into
        // every later prompt that turn — so targets/discards/follow-ups execute ONE line, not myopic picks.
        private String turnPlan = "";
        private int planTurn = -1;
        /** The model's own one-line read of the opponent (deck/plan, cards to respect). Refreshed when
         *  empty or >= PROFILE_REFRESH_TURNS old, piggybacked on the plan ask so it costs no extra call. */
        private String oppProfile = "";
        /** PLAN-THEN-EXECUTE (opt-in PLAN_EXECUTE=1). If the model's own turn plan NAMES a card that is
         *  in the current menu, and nothing on the OPPONENT's side has changed since it stated the plan,
         *  the harness executes that step itself instead of asking again -- one smart call per turn
         *  rather than one per priority window. Any interruption (their spell, a life change, a new
         *  permanent) invalidates the plan and forces a re-ask. Off by default. Not final: tests set it. */
        static boolean PLAN_EXECUTE = "1".equals(System.getenv("PLAN_EXECUTE"));
        /** ONE_TIER=1: one bridge answers every decision, with the smart tier's model, system prompt and
         *  reasoning. The two-tier split sends 58% of real decisions in runs/loop g01-g07 (every target,
         *  most instant-speed plays) to a tier with reasoning disabled, which is the harness deciding how
         *  hard the model may think per decision type. A condition: stamped `one_tier` on the game line. */
        static final boolean ONE_TIER = !"0".equals(System.getenv("ONE_TIER"));   // default since 2026-10-15
        /** NO_COACHING=1: the system prompt leaves out PRINCIPLES and {@link #STATIC_REFERENCE}, the
         *  harness's own advice about how to play and read the state. An assistance condition, like a
         *  deck plan (docs/STANDARDS.md section 8): stamped `no_coaching` on the game line. */
        static final boolean NO_COACHING = "1".equals(System.getenv("NO_COACHING"));
        private String planSig;      // opponent-side signature when the plan was stated
        /** When the plan was stated, "T5 MAIN1" (faithful plan line). */
        private String planWhen = "";
        /** The tags that end a PLAN line written on the same line as an answer. */
        private static final String[] PLAN_ENDS = {"CHOICE:", "PICKS:", "BLOCKS:", "ATTACKS:", "ATTACKERS:", "STEPS:",
                "LINES:", "PROFILE:", "ANSWER:"};
        private static final Pattern PLAN_LINE = Pattern.compile("(?i)^[\\s*_>#`-]*PLAN\\s*:(.*)$");

        /** The text of the reply's last line that STARTS with PLAN: (markdown allowed), cut at an
         *  answer tag on the same line; null when there is none. A line merely mentioning a plan
         *  ("my game plan: race") is not a plan statement. */
        static String planStatement(String reply) {
            if (reply == null) return null;
            String found = null;
            for (String ln : reply.split("\\R")) {
                Matcher m = PLAN_LINE.matcher(ln);
                if (m.find()) found = m.group(1);
            }
            if (found == null) return null;
            found = cutAtTags(found, PLAN_ENDS).replaceAll("^[\\s*_`]+|[\\s*_`]+$", "").replaceAll("\\s+", " ").trim();
            return found.isEmpty() ? null : found;
        }

        /** Faithful seats: a PLAN line in any reply replaces the plan, whatever the decision was (the
         *  opponent cast something in their main phase, a card was drawn, a block went badly). A
         *  changed plan drops the old cast list, which belonged to the old plan. Returns true when it
         *  changed. Whitespace-collapsed, never truncated (the C4 defect cut a stated finisher). */
        boolean adoptPlan(String reply) {
            return adoptPlanText(planStatement(reply));
        }

        /** The plan itself, from a PLAN line or from `set_plan` ("GOAL: ... NEXT: ..."). */
        boolean adoptPlanText(String pl) {
            if (pl != null) pl = pl.replaceAll("\\s+", " ").trim();
            if (pl == null || pl.isEmpty()) return false;
            String ph = "";
            try { ph = " " + getGame().getPhaseHandler().getPhase(); } catch (Exception ignore) { }
            planTurn = safeTurn();
            planWhen = "T" + planTurn + ph;           // restated unchanged: still the plan, stated now
            if (pl.equals(turnPlan)) return false;
            boolean revised = !turnPlan.isEmpty();
            turnPlan = pl;
            planSig = oppPlanSig();
            planStepIdx = 0;
            planSteps.clear();
            p("[ROUTER] plan" + (revised ? "(revised)" : "") + ": " + turnPlan);
            return true;
        }
        private static final Pattern PROFILE_LINE = Pattern.compile("(?i)^[\\s*_>#`-]*PROFILE\\s*:(.*)$");
        private static final Pattern LINES_LINE = Pattern.compile("(?i)^[\\s*_>#`-]*LINES\\s*:(.*)$");
        private static final String[] NOTE_ENDS;
        static {
            NOTE_ENDS = java.util.Arrays.copyOf(PLAN_ENDS, PLAN_ENDS.length + 1);
            NOTE_ENDS[PLAN_ENDS.length] = "PLAN:";
        }

        /** The text of the reply's last line that STARTS with the tag {@code line} matches, cut at an
         *  answer tag on the same line; null when there is none. */
        static String tagStatement(String reply, Pattern line) {
            if (reply == null) return null;
            String found = null;
            for (String ln : reply.split("\\R")) {
                Matcher m = line.matcher(ln);
                if (m.find()) found = m.group(1);
            }
            if (found == null) return null;
            found = cutAtTags(found, NOTE_ENDS).replaceAll("^[\\s*_`]+|[\\s*_`]+$", "").replaceAll("\\s+", " ").trim();
            return found.isEmpty() ? null : found;
        }

        /** Faithful seats: a PROFILE or LINES line in any reply replaces that note, like a PLAN line. The
         *  legend tells the seat it may revise its opponent profile "at any time", but a PROFILE line was
         *  read only on the ask that requested it (every 5 turns), and LINES only on its first two asks:
         *  the model's own notes were frozen between asks. Returns true when either changed. */
        boolean adoptNotes(String reply) {
            boolean changed = false;
            String pf = tagStatement(reply, PROFILE_LINE);
            if (pf != null && !pf.equals(oppProfile)) {
                oppProfile = pf;
                profileTurn = safeTurn();
                p("[ROUTER] profile: " + oppProfile);
                changed = true;
            }
            String dl = tagStatement(reply, LINES_LINE);
            if (dl != null && !dl.equals(deckLines)) {
                deckLines = dl;
                deckLinesAsked = true;
                p("[ROUTER] lines: " + deckLines);
                changed = true;
            }
            return changed;
        }
        private int planStepIdx;     // how many plan steps have been executed this turn
        /** The model's EXPLICIT cast list (`STEPS: A, B`), in order. Only this is auto-executed.
         *  Free-text matching was a real bug (2026-10-13): a plan reading "Hold Shallow Grave
         *  (graveyard has no creatures)" matched as a step to CAST, and the harness cast it -- a
         *  misplay the MODEL had explicitly ruled out in the same sentence. Prose cannot distinguish
         *  "cast X" from "hold X", so only an explicit list is honoured. */
        private final java.util.List<String> planSteps = new java.util.ArrayList<>();
        /** The model's own deck-interaction notes, asked ONCE per game and re-injected always. */
        private String deckLines = "";
        private boolean deckLinesAsked;
        /** How many times the LINES ask has been sent. Bounded: a model that never answers it must not
         *  carry the request on every smart prompt for the whole game. */
        private int deckLinesAsks;
        static final int DECK_LINES_MAX_ASKS = 2;
        /** The LINES ask. FORMAT ONLY, deliberately: an example that named a real interaction would be
         *  per-deck coaching, and would make "did the seat find its own lines" unmeasurable. */
        static final String DECK_LINES_ASK = "Also state how the cards in YOUR OWN deck combine, on its own line, as "
                + "`LINES: <card> + <card> -> <what you do and what you get> | <card> + <card> -> ... | ...` "
                + "(as many as you see, each naming two or more of your own cards from the decklist above). ";
        private int profileTurn = -99;
        private static final int PROFILE_REFRESH_TURNS = 5;

        /** The resolved context profile for this process — what the model is allowed to see.
         *  Resolved ONCE from the environment: a profile that could change mid-game would make the
         *  run's own provenance a lie. An unknown CONTEXT_PROFILE throws here (surfacing as
         *  ExceptionInInitializerError naming the known profiles) rather than silently running the
         *  default, because a typo that quietly measures something else is exactly the failure this
         *  type exists to prevent. */
        static final ContextProfile CTX = ContextProfile.fromEnv();
        /** Test seam: a profile that replaces {@link #CTX} for controllers constructed while it is
         *  set (tests cannot mutate the process environment). Never set in production; the game
         *  summary stamps CTX, so a run with this set would mis-stamp its own provenance. */
        static volatile ContextProfile TEST_PROFILE_OVERRIDE = null;
        /** Test-only override for the LOOKAHEAD env flag (env vars cannot be set in-process). Null =
         *  read the environment. Package-private, mirroring TEST_PROFILE_OVERRIDE. */
        static volatile Boolean TEST_LOOKAHEAD_OVERRIDE = null;
        private static boolean lookaheadEnabled() {
            if (TEST_LOOKAHEAD_OVERRIDE != null) return TEST_LOOKAHEAD_OVERRIDE.booleanValue();
            return "1".equals(System.getenv("LOOKAHEAD"));
        }
        static ContextProfile profile() {
            ContextProfile o = TEST_PROFILE_OVERRIDE;
            return o != null ? o : CTX;
        }
        /** The profile this controller was built under; resolved once per controller. */
        private final ContextProfile ctx;
        /** The viewer-aware observation layer, present only under a faithful profile. Everything the
         *  model sees under that profile — state, candidate labels, narration, reveals, INSPECT —
         *  passes through it; null means the legacy buildSnapshot/candidateLabel path is in use. */
        private final SeatObservation observer;

        // Rolling strategic intel (revealed hands, cards we named). Included in every snapshot so it
        // survives the periodic bridge-session refresh — the model keeps what it LEARNED even when the
        // conversation restarts for speed.
        private final java.util.ArrayDeque<String> intel = new java.util.ArrayDeque<>();
        static final boolean MEMORY_TOOLS = "1".equals(System.getenv("MEMORY_TOOLS"));
        /** RULES tool: a read-only, paginated index over the public Comprehensive Rules, so the model
         *  can look up the actual rule text instead of recalling it. Off by default (RULES_TOOL=1 to
         *  enable), so a run without it has byte-identical prompts — the with/without eval toggle.
         *  It rides the MEMORY tool loop, so it requires MEMORY_TOOLS=1. */
        static final boolean RULES_TOOL = "1".equals(System.getenv("RULES_TOOL"));
        /** The rules lookup is offered only beside the other lookups: RULES_TOOL=1 alone is asked for, never wired. */
        static boolean rulesToolWired(boolean memoryTools, boolean rulesTool) { return memoryTools && rulesTool; }
        /** The CR text path: RULES_FILE, else data/rules/MagicCompRules.txt from the working
         *  directory or an ancestor (the acceptance runs from the Forge tree, so it passes RULES_FILE). */
        static final String RULES_FILE = resolveRulesFile();
        private static volatile RulesTool sharedRules;

        static String resolveRulesFile() {
            String env = System.getenv("RULES_FILE");
            if (env != null && !env.trim().isEmpty()) return env.trim();
            java.io.File d = new java.io.File(System.getProperty("user.dir", ".")).getAbsoluteFile();
            for (int i = 0; i < 5 && d != null; i++, d = d.getParentFile()) {
                java.io.File f = new java.io.File(d, "data/rules/MagicCompRules.txt");
                if (f.isFile()) return f.getAbsolutePath();
            }
            return "data/rules/MagicCompRules.txt";
        }

        /** Load the rules text once per JVM (it is static reference data, identical for every seat). */
        static RulesTool rulesTool() {
            RulesTool t = sharedRules;
            if (t == null) {
                synchronized (PlayerControllerLLM.class) {
                    if (sharedRules == null) sharedRules = RulesTool.load(RULES_FILE);
                    t = sharedRules;
                }
            }
            return t;
        }

        /** Effective date of the loaded rules text for provenance — never forces a load when off. */
        static String rulesEffective() {
            if (!RULES_TOOL) return "n/a";
            RulesTool rt = sharedRules;
            return rt != null && rt.available() ? rt.effectiveDate() : "unavailable";
        }
        private final ContextMemory contextMemory = new ContextMemory();
        private int contextCallSeq;
        private int archivedEventCount;

        /** Count of logged events this seat had already been shown at its previous snapshot, so
         *  EVENTS_SINCE_LAST_DECISION can narrate exactly the gap. -1 = no snapshot taken yet, which
         *  means "everything since the game began". Per-seat by construction: two seats have
         *  different decision cadences and therefore different gaps. */
        private int eventWatermark = -1;
        private void remember(String fact) {
            // Faithful path: the seat stream is the durable, uncapped record (E-id, turn attribution,
            // searchable); INTEL below is the legacy capped deque and is not rendered under it.
            if (observer != null) observer.recordReveal(fact);
            if (MEMORY_TOOLS) contextMemory.reveal("T" + safeTurn() + ": " + fact);
            intel.addLast("T" + safeTurn() + ": " + fact);
            // Cap is the profile's now (v1-compat pins the historical 16). Reveals are the "seen but
            // not necessarily cast" half of opponent memory — the cast/land history lines carry the
            // ground-truth half, so INTEL only needs to hold reveals/names, and 8 was evicting
            // early-game hand reveals by mid-game.
            while (intel.size() > ctx.intelCap) intel.removeFirst();
        }
        private int safeTurn() {
            try { return getPlayer().getGame().getPhaseHandler().getTurn(); } catch (Exception e) { return 0; }
        }

        // Baseline arms for eval runs (CONTROLLER_A/B): "llm" (default), "random" (uniform over the
        // same legal menus — anchors the floor of the scale), "floor" (every decision takes the
        // deterministic fallback path — quantifies how much play strength the HARNESS contributes).
        private final String mode;
        // Non-null for the zero-model arms. Set once when the seat is built, except on a replay seat with a handover
        // (step regret): there it becomes the search arm after the K-th replayed decision row (handOverToSearch).
        private java.util.Random rnd;

        PlayerControllerLLM(Game game, Player p, LobbyPlayer lp, String seat, String deckPlan,
                            String smartModel, String fastModel, String mode) {
            this(game, p, lp, seat, deckPlan, oneTierPair(seat, deckPlan, smartModel, fastModel, mode), mode);
        }

        private PlayerControllerLLM(Game game, Player p, LobbyPlayer lp, String seat, String deckPlan,
                                    LLMBridge[] pair, String mode) {
            this(game, p, lp, seat, deckPlan, pair[0], pair[1], mode);
        }

        /** The smart and fast bridges: one shared smart bridge under ONE_TIER, else one per tier. */
        private static LLMBridge[] oneTierPair(String seat, String deckPlan, String smartModel, String fastModel, String mode) {
            // A model seat on the Claude CLI is the development lane: the operator chose it. A retired bare name is refused.
            boolean optedIn = "1".equals(env(ClaudeCliBridge.OPT_IN));
            LLMBridgeFactory.requireCliOptIn(smartModel, mode, optedIn, seat + "/smart");
            LLMBridgeFactory.requireCliOptIn(fastModel, mode, optedIn, seat + "/fast");
            if ("llm".equals(mode)) ReplayBridge.requireBothTiers(smartModel, fastModel);
            // A seat that never asks a model (floor, random, the puzzle arms) gets placeholders on both tiers, whatever
            // they name (a default "haiku", an openrouter: or claude-cli: spec): no bridge to a model is ever made for it.
            if (!"llm".equals(mode))
                return new LLMBridge[] {LLMBridgeFactory.placeholder(seat + "/smart", smartModel),
                        LLMBridgeFactory.placeholder(seat + "/fast", fastModel)};
            LLMBridge smartB = LLMBridgeFactory.create(seat + "/smart", smartModel, framingFor(seat, deckPlan, false, smartModel));
            if ((ChatSessionBridge.ON || ChatSessionBridge.TURN) && smartB instanceof OpenRouterBridge)
                smartB = new ChatSessionBridge((OpenRouterBridge) smartB, !ChatSessionBridge.ON);
            // A script is one line, and a replay one recording: both tiers must read it from one position.
            if (ONE_TIER || smartB instanceof ScriptBridge || smartB instanceof ReplayBridge) return new LLMBridge[] {smartB, smartB};
            return new LLMBridge[] {smartB,
                    LLMBridgeFactory.create(seat + "/fast", fastModel, framingFor(seat, deckPlan, true, fastModel))};
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
            // The random arm and the other zero-model puzzle arms (PuzzleArms: script, pass, first) share
            // the random arm's decision paths; each draws the same one long from MyRandom here.
            this.rnd = PuzzleArms.isArm(this.mode) ? PuzzleArms.forMode(this.mode, MyRandom.getRandom().nextLong(), seat) : null;
            this.smart = smartBridge;
            this.fast  = fastBridge;
            ReplayBridge rb = replayBridge();
            if (rb != null) rb.armHandover(this::handOverToSearch);
            this.ctx = profile();
            // The observer subscribes to the game's event bus (zone changes only, read-only) so
            // handle continuity is tracked as it happens. Legacy profiles never construct one, so
            // their game behaviour and prompt bytes are untouched.
            this.observer = ctx.faithfulObservation ? new SeatObservation(game, p) : null;
            if (observer != null && MEMORY_TOOLS) contextMemory.setInspector(observer::inspect);
            // Display-only: an audience feed for the demo viewer when DEMO_FEED=1 (DemoFeed); off, nothing happens.
            DemoFeed.attach(game, seat, observer);
            contextMemory.setReporter(problem -> p("[REPORT] " + seat + " T" + safeTurn() + ": " + problem));
            contextMemory.offerPreviews(lookaheadEnabled());
            if (rulesToolWired(MEMORY_TOOLS, RULES_TOOL)) {
                RulesTool rt = rulesTool();
                if (rt.available()) {
                    contextMemory.setRulesLookup(RulesTool.instructions(rt.effectiveDate()), rt::lookup);
                    p("[RULES] " + RulesTool.VERSION + " wired (" + rt.ruleCount() + " rules, "
                            + rt.glossaryCount() + " terms, effective " + rt.effectiveDate() + ") from " + rt.sourcePath());
                } else {
                    p("[RULES] RULES_TOOL=1 but the rules text did not load (" + rt.loadError()
                            + "); the tool is left OFF so prompts match a no-rules run");
                }
            }
            if (!"llm".equals(this.mode)) p("[MODE] baseline arm: " + this.mode);
            if (observer != null) p("[OBSERVATION] " + SeatObservation.version() + " (profile " + ctx + ")");
        }

        /** Non-null under a faithful profile; tests use it to drive the live observer directly. */
        SeatObservation observer() { return observer; }

        /** The zero-model arm's draw source (null for llm/floor); LlmSpikePuzzle reads its tape. */
        java.util.Random armRandom() { return rnd; }
        /** The seat's spend so far (BENCHMARK-V2 section 3, the budget recorded per attempt): model calls, their
         *  prompt and completion tokens and milliseconds, and the lookups it made (ContextMemory's count). */
        java.util.Map<String, Object> budget() {
            java.util.Map<String, Object> m = new java.util.LinkedHashMap<>();
            m.put("model_calls", smartCalls + fastCalls);
            m.put("prompt_tokens", spentPromptTokens);
            m.put("completion_tokens", spentCompletionTokens);
            m.put("model_ms", spentModelMs);
            m.put("lookups", contextMemory.lookupsUsed());
            // Issue #378: the decisions a dead bridge lost (a 402 when the credits ran out, an outage; the game line's
            // bridge_down_decisions rule). puzzle_score.py refuses a row with any; puzzle_solve.py --resume replays it.
            m.put("bridge_down_decisions", lostToTransport);
            m.put("eval_log_write_failed", EvalLog.writeFailed());  // issue #427: puzzle_score.py refuses a row with it
            // Under a routing filter (price ceiling, precision list): the decisions lost to it after every retry
            // (puzzle_score.py refuses a row with any), and the HTTP refusals behind them, recovered ones included (a
            // diagnostic; a recovered one lost nothing).
            Integer refused = OpenRouterBridge.routingRefusals(smart, fast);
            if (refused != null) {
                m.put("routing_refusals", lostToRouting);
                m.put("routing_http_refusals", refused);
                // The filters themselves: puzzle_score.py never pools rows run under different ones.
                if (OpenRouterBridge.maxPriceEnv != null && !OpenRouterBridge.maxPriceEnv.isBlank())
                    m.put("openrouter_max_price", OpenRouterBridge.maxPriceEnv);
                if (OpenRouterBridge.quantizationsEnv != null && !OpenRouterBridge.quantizationsEnv.isBlank())
                    m.put("openrouter_quantizations", OpenRouterBridge.quantizationsEnv);
            }
            return m;
        }
        /** Step regret: the replayed seat becomes the search arm, on the game thread, right after its K-th decision row.
         *  Built with no draw from the engine RNG (the search arm's own constructor draws one long, which would shift the
         *  rest of the game against the recorded one; Search never reads its seed). A yield the recording asked for is
         *  dropped: every later window is the search's to choose. Every hook from here takes the arm's branches. */
        private void handOverToSearch() {
            rnd = new PuzzleArms.Search(0L, seat);
            yieldUntil = null;
            p("[REPLAY] handed over to the search arm");
        }

        /** The written line this seat plays (ScriptBridge), or null for any other seat. */
        ScriptBridge scriptBridge() { return smart instanceof ScriptBridge ? (ScriptBridge) smart : null; }
        /** The recording this seat replays (ReplayBridge), or null for any other seat. */
        ReplayBridge replayBridge() { return smart instanceof ReplayBridge ? (ReplayBridge) smart : null; }

        // ---------------- candidate labels: one seam, two renderers ----------------
        // Faithful profile: every menu option is rendered by the observer, so what the model can pick
        // carries the same seat-local @handle and the same visibility gating as the state it just read
        // (`presented` = the engine handed us this candidate, which waives the zone rule but never the
        // face-down rule). Legacy profiles: the static helpers, byte-for-byte as before.
        String cardLabel(Card c) { return observer != null ? observer.describe(c, true) : candidateLabel(c); }
        /** Text an ask reads: whole under the faithful profile (nothing the model decides on is cut
         *  short), {@link #oneLine}'s 90-character cut on legacy profiles, which keep their bytes. */
        private String fit(String s) { return observer != null ? flat(s) : oneLine(s); }
        /** Engine-formatted text ("Spell Pierce (93) - Counter Umezawa's Jitte (40)") with each engine
         *  card id mapped to the seat's handle under the faithful profile, as the narration is. */
        private String seatText(String s) { return observer != null ? observer.redactIds(flat(s)) : s; }
        /** The legal targets of {@code sa} as the engine targets them. When the stack is its only target zone ("counter
         *  target spell", Stifle), the choices are the spells and abilities on the stack themselves, top first, as the
         *  human path offers them (TargetSelection.chooseCardFromStack); otherwise the engine's players and cards.
         *  {@code getAllCandidates} gives a spell's CARD, and a counterspell aimed at the card counters nothing: Luna's
         *  Force of Will "countered" a Dark Ritual that then resolved (C3 Doomsday, whole-game arm, T8). */
        static List<forge.game.GameObject> targetCandidates(SpellAbility sa, TargetRestrictions tr) {
            List<ZoneType> zones = tr.getZone();
            if (zones != null && zones.size() == 1 && zones.get(0) == ZoneType.Stack) {
                List<forge.game.GameObject> out = new ArrayList<>();
                for (forge.game.spellability.SpellAbilityStackInstance si : sa.getHostCard().getGame().getStack())
                    if (sa.canTargetSpellAbility(si.getSpellAbility())) out.add(si.getSpellAbility());
                return out;
            }
            // A spell cast as another face (Disturb's back face, an adventure) targets as that face, as the engine casts it
            // (PlaySpellAbility: setSplitStateToPlayAbility before targeting): counted as Lantern Bearer's front face,
            // Lanterns' Lift's "enchant creature" had no target and the Disturb cast was never offered (#366).
            Card host = sa.getHostCard();
            forge.card.CardStateName cast = sa.isSpell() && host != null && !host.isInPlay() ? sa.getCardStateName() : null;
            forge.card.CardStateName was = host == null ? null : host.getCurrentStateName();
            boolean switched = cast != null && cast != was && host.hasState(cast) && host.setState(cast, false);
            try {
                return new ArrayList<>(tr.getAllCandidates(sa, false));
            } finally {
                if (switched) host.setState(was, false);
            }
        }

        /** A target as the seat reads it: a spell on the stack as its card (the text it had before), an ability on the
         *  stack as "ability of" its source with what it does. */
        String targetLabel(forge.game.GameObject o) {
            if (o instanceof SpellAbility) {
                SpellAbility t = (SpellAbility) o;
                if (t.isSpell()) return labelOf(t.getHostCard());
                String what = observer != null ? observer.redactIds(flat(t.getStackDescription())) : oneLine(t.getStackDescription());
                return "ability of " + labelOf(t.getHostCard()) + (what == null || what.isEmpty() ? "" : ": " + what);
            }
            return labelOf((GameEntity) o);
        }

        /** {@link #targetLabel} for the static, profile-free labels the ai-teacher prompt uses. */
        static String targetEntityLabel(forge.game.GameObject o) {
            if (o instanceof SpellAbility) {
                SpellAbility t = (SpellAbility) o;
                return t.isSpell() ? entityLabel(t.getHostCard()) : "ability of " + entityLabel(t.getHostCard()) + ": " + oneLine(t.getStackDescription());
            }
            return entityLabel((GameEntity) o);
        }

        String labelOf(GameEntity e) {
            if (observer != null && e instanceof Card) {
                Card c = (Card) e;
                String zone = c.getZone() == null ? "?" : String.valueOf(c.getZone().getZoneType());
                return observer.describe(c, true) + " [" + zone + ", " + whose(c) + "]";
            }
            // A target menu of two bare player names left the seat to work out which one it is.
            if (observer != null && e instanceof Player) return e == getPlayer() ? "you (" + seat + ")" : "opponent (" + ((Player) e).getName() + ")";
            return entityLabel(e);
        }
        /** Whose object an option is: runs/loop/g07 untapped the opponent's Broadside Bombardiers from
         *  a menu of eight creatures, six of them its own, none marked. */
        private String whose(Card c) {
            boolean controlled = c.isInPlay() || c.getZone() != null && c.getZone().is(ZoneType.Stack);
            return (controlled ? c.getController() : c.getOwner()) == getPlayer() ? "yours" : "opponent's";
        }
        /** The source of an ask, for the question text. Faithful: the card's rules text and who the
         *  spell targets, because a number, yes/no or pick asked by name alone ("Search your hand?
         *  [Aether Vial]", "Choose a number [Galvanic Discharge]") did not say what it would do. */
        private String srcAsk(SpellAbility sa) {
            if (observer == null || sa == null || sa.getHostCard() == null) return srcOf(sa);
            try {
                Card h = sa.getHostCard();
                String text = flat(h.getOracleText());
                return " [" + safeName(h) + (text.isEmpty() ? "" : ": " + text) + "]" + targetNote(sa, getPlayer());
            } catch (Exception e) { return srcOf(sa); }
        }
        /** {@link #srcAsk} for an ask the engine hands a card instead of an ability. */
        private String cardAsk(Card h) {
            if (h == null) return "";
            String text = flat(h.getOracleText());
            return " [" + safeName(h) + (text.isEmpty() ? "" : ": " + text) + "]";
        }
        /** " (you announced X = 1)" once X is announced for {@code sa}'s spell: the mode and target questions come
         *  after X, and without it the seat chose Kozilek's Command's modes as if X were 0 (runs/luna-modern/g11, T6). */
        static String xNote(SpellAbility sa) {
            try {
                SpellAbility root = sa.getRootAbility();
                if (root.getPayCosts() == null || root.getPayCosts().getCostMana() == null
                        || root.getPayCosts().getCostMana().getAmountOfX() == 0) return "";
                Integer x = root.getXManaCostPaid();
                return x == null ? "" : " (you announced X = " + x + ")";
            } catch (Exception e) { return ""; }
        }
        String labelOf(SpellAbility sa) {
            if (observer == null || sa.getHostCard() == null) return saLabel(sa);
            // Redacted: a trigger's text carries Forge's "[Card: Cityscape Leveler (70), Activator: ...]".
            return observer.redactIds(faithfulSaLabel(sa, observer.shortRef(sa.getHostCard())));
        }

        /** True when every part of {@code sa} only produces mana (a ritual): the one kind of response
         *  to an opponent's spell that changes nothing they did. Unclassifiable -> false (keep asking). */
        static boolean onlyMakesMana(SpellAbility sa) {
            try {
                for (SpellAbility part = sa; part != null; part = part.getSubAbility()) {
                    ApiType api = part.getApi();
                    if (api != ApiType.Mana && api != ApiType.ManaReflected) return false;
                }
                return true;
            } catch (Exception e) { return false; }
        }

        /** {@code seg} up to the first of {@code tags} (case-insensitive), for replies that put several
         *  tagged answers on one line. */
        static String cutAtTags(String seg, String... tags) {
            if (seg == null) return null;
            String up = seg.toUpperCase();
            int cut = seg.length();
            for (String t : tags) { int i = up.indexOf(t); if (i >= 0 && i < cut) cut = i; }
            return seg.substring(0, cut);
        }

        /** What the seat is about to do with {@code sa}: CAST a spell, ACTIVATE an ability. */
        static String verbOf(SpellAbility sa) {
            try { return sa.isSpell() ? "CAST" : "ACTIVATE"; } catch (Exception e) { return "CAST"; }
        }

        /** The faithful-profile option label. The legacy {@link #saLabel} prints the HOST CARD's mana
         *  cost for every option and never says where the card is, which misdescribes exactly the
         *  options a line is built from: an activated ability ("Goblin Bombardment 1{R}" for an ability
         *  whose cost is a creature) and a graveyard cast ("Cabal Therapy {B}" for a flashback whose
         *  cost is a creature). Here:
         *  <ul><li>a spell from hand with no alternative cost: unchanged, {@code <ref> <card mana cost>
         *      [text]} (still the CARD's cost, so a split/adventure face is not yet exact);
         *  <li>a spell from anywhere else, or at an alternative cost: the zone, the alternative cost's
         *      name, and the cost it actually pays;
         *  <li>an ability: no mana cost of the card; its own cost is the "cost:" part of its text.</ul>
         *  {@code CARDNAME}/{@code NICKNAME} placeholders are replaced with the card's name. */
        /** An ability's text with the engine's {@code CARDNAME}/{@code NICKNAME} placeholders replaced by the card's
         *  name. The mana-sources question showed "Sacrifice CARDNAME" for Lotus Petal (runs/c3/ant-storm-default T7). */
        static String named(String text, Card host) {
            if (text == null || host == null) return text;
            String name = host.getName();
            return text.replace("CARDNAME", name).replace("NICKNAME", name);
        }

        /** An alternative cost as the card prints it: Forge's enum names three of them by abbreviation or run-together
         *  words ("WebSlinging", "MTMtE", "BeamMeUp"). */
        static String altCostName(forge.game.spellability.AlternativeCost alt) {
            switch (alt) {
                case WebSlinging: return "Web-slinging";
                case MTMtE: return "More Than Meets the Eye";
                case BeamMeUp: return "Beam me up";
                default: return String.valueOf(alt);
            }
        }

        static String faithfulSaLabel(SpellAbility sa, String hostRef) {
            Card host = sa.getHostCard();
            String name = host == null ? "?" : host.getName();
            String desc = sa.getDescription();
            if (desc == null || desc.isEmpty()) desc = String.valueOf(sa);
            // Not cut (it was at 160): the option is the one place the ability's whole text is read
            // when it is chosen (Green Sun's Zenith lost "into its owner's library").
            // Forge's debugging note on a trigger, removed exactly as the engine builds it (Elder Deep-Fiend's cast
            // trigger kept "[Card: ..., Activator: Seat, SpellAbility: ...]": the pattern below needs an id at its end).
            desc = SeatObservation.engineNotesOut(desc, sa);
            desc = desc.replace("CARDNAME", name).replace("NICKNAME", name).replaceAll("\\s+", " ").trim();
            // A trigger's description carries the engine's own note with its card id ("[Zone Changer:
            // Orcish Bowmasters (37)]", runs/loop/g08): an engine id the faithful profile never shows.
            desc = desc.replaceAll("\\s*\\[[A-Za-z ]+: [^\\]]*\\(\\d+\\)\\]", "").trim();
            desc = typePlurals(desc);
            // A permanent spell's description is Forge's "Fury - Creature 3 / 3", which says nothing of what it does:
            // the option shows the card's rules text instead (Fury's damage was nowhere on the menu).
            try {
                if (host != null && desc.startsWith(name + " - ") && host.getOracleText() != null && !host.getOracleText().isEmpty())
                    desc = host.getOracleText().replace("\\n", " ").replaceAll("\\s+", " ").trim();
            } catch (Exception ignore) { }
            // A cast at another cost (prowl, surge, spectacle, overload, dash, a pitch...) is described by the engine as
            // that cost and its reminder only: Morsel Theft's prowl never said the target loses 3 life, Skewer the
            // Critics' spectacle never said it deals 3 damage. The option shows the text of the face being cast.
            try {
                if (sa.isSpell() && (sa.getAlternativeCost() != null || !sa.isBasicSpell())) {
                    String face = sa.getCardState() != null ? sa.getCardState().getOracleText() : null;
                    if (face == null || face.isEmpty()) face = host.getOracleText();
                    if (face != null && !face.isEmpty()) desc = named(face, host).replace("\\n", " ").replaceAll("\\s+", " ").trim();
                }
            } catch (Exception ignore) { }
            String body = desc.isEmpty() ? "" : " [" + desc + "]";
            boolean spell;
            try { spell = sa.isSpell(); } catch (Exception e) { spell = true; }
            String zone = "";
            try {
                forge.game.zone.Zone z = host.getLastKnownZone();
                boolean usual = z == null || z.is(ZoneType.Stack) || z.is(spell ? ZoneType.Hand : ZoneType.Battlefield);
                if (!usual) {
                    Player zp = z.getPlayer(), actor = sa.getActivatingPlayer();
                    String whose = zp == null || actor == null || zp == actor ? "your " : zp.getName() + "'s ";
                    zone = whose + z.getZoneType().name().toUpperCase();
                }
            } catch (Exception ignore) { }
            if (!spell) return hostRef + (zone.isEmpty() ? "" : " in " + zone) + body;
            String alt = "";
            try { if (sa.getAlternativeCost() != null) alt = altCostName(sa.getAlternativeCost()); } catch (Exception ignore) { }
            // An option offered with an optional cost (retrace from the graveyard) names it.
            try {
                if (alt.isEmpty()) {
                    List<String> names = new ArrayList<>();
                    // A Defiler's payment is named "(to reduce white mana)": the label adds its own brackets.
                    for (forge.game.spellability.OptionalCost o : sa.getOptionalCosts())
                        names.add(o.getName().replaceAll("^\\((.*)\\)$", "$1"));
                    alt = String.join(", ", names);
                }
            } catch (Exception ignore) { }
            // A cast that is not the card's basic spell pays another cost (a pitch spell's static alternative cost has no
            // AlternativeCost, so alt is empty): say that cost, not the mana cost it replaces. Disrupting Shoal's pitch read
            // "CAST Disrupting Shoal {X}{U}{U} [...rather than pay this spell's mana cost.]".
            boolean basic;
            try { basic = sa.isBasicSpell(); } catch (Exception e) { basic = true; }
            if (zone.isEmpty() && alt.isEmpty() && basic) {
                String mc = null;
                try { mc = costStr(host); } catch (Exception ignore) { }
                return hostRef + (mc == null ? "" : " " + mc) + body;
            }
            String cost = "";
            try { if (sa.getPayCosts() != null) cost = plainFilters(paidCostString(sa).replace("CARDNAME", name).replaceFirst("^\\{0\\},?\\s*", "").trim()); } catch (Exception ignore) { }
            return hostRef + (zone.isEmpty() ? "" : " from " + zone) + (alt.isEmpty() ? "" : " (" + alt + ")")
                    + " — cost: " + (cost.isEmpty() ? "none" : cost) + body;
        }
        /** The cast's cost as it will be paid: the coloured mana a Defiler's life payment takes off (the spell's
         *  pips to reduce, CostAdjustment) is left out. The menu read "cost: {W}, Pay 2 life" for a Savannah Lions
         *  that cost 2 life and no mana. */
        static String paidCostString(SpellAbility sa) {
            // Harmonize's reduction is chosen during the cast: the label says it, or {5}{R}{R} beside four lands reads
            // as unpayable.
            if (sa.isHarmonize()) return sa.getPayCosts().toSimpleString() + ", {X} less if you tap a creature you control of power X";
            List<String> pips = sa.getPipsToReduce();
            if (pips.isEmpty() || !sa.isSpell()) return sa.getPayCosts().toSimpleString();
            List<String> parts = new ArrayList<>();
            for (forge.game.cost.CostPart part : sa.getPayCosts().getCostParts()) {
                if (part instanceof forge.game.cost.CostPartMana pm) {
                    forge.game.mana.ManaCostBeingPaid m = new forge.game.mana.ManaCostBeingPaid(pm.getMana());
                    for (String pip : pips) m.decreaseShard(forge.card.mana.ManaCostShard.parseNonGeneric(pip), 1);
                    forge.card.mana.ManaCost left = m.toManaCost();
                    if (!left.isZero() && !left.isNoCost()) parts.add(left.toString());
                } else parts.add(part.toString());
            }
            return String.join(", ", parts);
        }
        List<String> playLabels(List<SpellAbility> options, List<Card> landCards) {
            if (observer == null) return buildPlayLabels(options, landCards);
            List<String> out = new ArrayList<>();
            for (SpellAbility sa : options) out.add(verbOf(sa) + " " + labelOf(sa));
            for (Card lc : landCards) out.add("PLAY LAND " + observer.describe(lc, true));
            out.add("PASS (do nothing)");
            return out;
        }
        /** BLOCKS parsing: a handle reference ("@7") beats a name match when the observer is live, so
         *  duplicate-named creatures are unambiguous; the legacy name match is the fallback. */
        /** The blocker->attacker pairs of a BLOCKS line. A card name may hold a comma ("Ragavan, Nimble
         *  Pilferer"), so splitting on every comma broke such names and the pair was dropped. Between two
         *  arrows the cut is the first comma or semicolon whose left side names an attacker and right
         *  side a blocker (else the last one). Each pair is {blocker text, attacker text}. */
        /** "1 = X; 2 = Y": the numbers a block answer uses. */
        static String numberedList(List<String> labels) {
            StringBuilder b = new StringBuilder();
            for (int i = 0; i < labels.size(); i++) b.append(i == 0 ? "" : "; ").append(i + 1).append(" = ").append(labels.get(i));
            return b.toString();
        }

        List<String[]> blockPairs(String line, Iterable<Card> blockers, Iterable<Card> attackers) {
            List<String[]> out = new ArrayList<>();
            String[] segs = line.split("->");
            if (segs.length < 2) return out;
            String blk = segs[0];
            for (int i = 1; i < segs.length; i++) {
                String seg = segs[i];
                if (i == segs.length - 1) { out.add(new String[] {blk, seg}); break; }
                int cut = -1, last = -1;
                for (int k = 0; k < seg.length() && cut < 0; k++) {
                    char ch = seg.charAt(k);
                    if (ch != ',' && ch != ';') continue;
                    last = k;
                    if (matchCard(attackers, seg.substring(0, k)) != null && matchCard(blockers, seg.substring(k + 1)) != null) cut = k;
                }
                if (cut < 0) cut = last;
                if (cut < 0) { out.add(new String[] {blk, seg}); blk = ""; continue; }
                out.add(new String[] {blk, seg.substring(0, cut)});
                blk = seg.substring(cut + 1);
            }
            return out;
        }

        private Card matchCard(Iterable<Card> cards, String seg) {
            if (observer != null) {
                Matcher m = Pattern.compile("@(\\d+)").matcher(seg);
                if (m.find()) {
                    int h = Integer.parseInt(m.group(1));
                    for (Card c : cards) if (observer.handleOf(c) == h) return c;
                }
            }
            return matchByName(cards, seg.toUpperCase());
        }

        // ---------------- reveal capture (information hooks; ledger kind=information) ----------------
        // The engine tells a controller what was revealed TO its player through these two hooks. The
        // inherited AI implementation only feeds AiCardMemory. Under the faithful profile the reveal is
        // appended to the seat stream (so it gets an E-id, turn attribution, search and archive) as a
        // historical, seat-local fact. Legacy profiles keep the inherited behaviour so their prompt bytes
        // are unchanged; super is always called so any inherited fallback still sees what the AI saw.
        /** What the engine tells the player was chosen (Meddling Mage's or Cabal Therapy's named card, a
         *  chosen colour, a number). The inherited AI ignores it; a human is shown it, so the seat records
         *  it in its stream under the faithful profile (human_decisions.py found the gap). */
        @Override
        public void notifyOfValue(SpellAbility sa, forge.game.GameObject relatedTarget, String value) {
            super.notifyOfValue(sa, relatedTarget, value);
            if (observer == null || value == null || value.isEmpty()) return;
            try {
                observer.recordNotice(forge.util.MessageUtil.formatNotificationMessage(sa, getPlayer(), relatedTarget, value));
            } catch (Exception e) {
                try { observer.recordNotice((sa == null || sa.getHostCard() == null ? "" : safeName(sa.getHostCard()) + ": ") + value); }
                catch (Exception ignore) { }
            }
        }

        @Override
        public void reveal(CardCollectionView cards, ZoneType zone, Player owner, String messagePrefix, boolean addSuffix) {
            super.reveal(cards, zone, owner, messagePrefix, addSuffix);
            if (observer == null || cards == null || cards.isEmpty()) return;
            try {
                StringBuilder b = new StringBuilder();
                for (Card c : cards) { if (b.length() > 0) b.append(", "); b.append(observer.describe(c, true)); }
                observer.recordReveal(revealPrefix(messagePrefix, zone, owner) + b);
            } catch (Exception ignore) { }
        }
        @Override
        public void reveal(List<forge.game.card.CardView> cards, ZoneType zone, forge.game.player.PlayerView owner,
                           String messagePrefix, boolean addSuffix) {
            super.reveal(cards, zone, owner, messagePrefix, addSuffix);
            if (observer == null || cards == null || cards.isEmpty()) return;
            try {
                StringBuilder b = new StringBuilder();
                for (forge.game.card.CardView cv : cards) {
                    if (b.length() > 0) b.append(", ");
                    Card c = cv == null ? null : getGame().findByView(cv);
                    b.append(c != null ? observer.describe(c, true) : String.valueOf(cv == null ? "?" : cv.getName()));
                }
                Player o = null;
                if (owner != null) for (Player pl : getGame().getPlayers()) if (pl.getView() == owner) { o = pl; break; }
                observer.recordReveal(revealPrefix(messagePrefix, zone, o) + b);
            } catch (Exception ignore) { }
        }
        /** Engine text the model reads: whole under the faithful profile, where the 90-character
         *  {@link #oneLine} cut removed what a question turned on; the legacy profiles keep their bytes. */

        private String revealPrefix(String messagePrefix, ZoneType zone, Player owner) {
            String who = owner == null ? "?" : (owner == getPlayer() ? "your" : owner.getName() + "'s");
            String what = messagePrefix == null || messagePrefix.trim().isEmpty() ? "revealed" : fit(messagePrefix);
            return what + " from " + who + " " + (zone == null ? "zone" : zone) + ": ";
        }

        /** See {@link LlmSpikeGui#puzzleSolverSeat}: this is read by Forge at exactly one place,
         *  Puzzle.addGoalEnforcement, to pick the seat that must meet the goal. */
        @Override
        public boolean isGuiPlayer() { return isPuzzleSolver(getLobbyPlayer()); }

        private void p(String s) { System.out.println("[" + seat + "] " + s); }
        static String routeLabel(String model, boolean random) {
            return random ? "random baseline (no model call)" : "asking " + model + "...";
        }
        private static int envInt(String k, int def) {
            try { String v = System.getenv(k); return v == null ? def : Integer.parseInt(v.trim()); }
            catch (Exception e) { return def; }
        }
        /** `nativeTools`: this tier's backend takes function definitions, so the preamble describes
         *  the tools in one paragraph instead of teaching the text grammar. */
        static String framing(String seat, String plan, boolean terse, boolean nativeTools) {
            return framing(seat, plan, terse, nativeTools, !NO_COACHING, false);
        }

        /** {@code coaching} false is the NO_COACHING condition: no PRINCIPLES, no static reference. */
        static String framing(String seat, String plan, boolean terse, boolean nativeTools, boolean coaching) {
            return framing(seat, plan, terse, nativeTools, coaching, false);
        }

        /** The preamble of one tier's bridge for its model spec: native tools and the answer tool when
         *  that backend takes function definitions. */
        static String framingFor(String seat, String plan, boolean terse, String modelSpec) {
            return framing(seat, plan, terse, NativeTools.usedBy(modelSpec), !NO_COACHING, AnswerTool.usedBy(modelSpec));
        }

        /** {@code answerTool}: decisions are answered by calling `answer` (AnswerTool), so the format rules
         *  and the answer contract describe the call, not a final line. */
        static String framing(String seat, String plan, boolean terse, boolean nativeTools, boolean coaching, boolean answerTool) {
            if (profile().faithfulObservation) return seatFraming(seat, plan, nativeTools, coaching, answerTool);
            // Under the faithful profile the fast tier is not told "NO reasoning": every decision text
            // asks it to reason briefly, and 72% of its replies in runs/loop g01-g07 reasoned anyway.
            String format = terse && !profile().faithfulObservation
                    // fast tier answers high-frequency low-stakes spots: reasoning tokens are pure latency there
                    ? "FORMAT: reply with ONLY the final line in EXACTLY the format I request (e.g. `CHOICE: 3`). "
                      + "NO reasoning, no other text — every extra token slows the game down."
                    : "FORMAT: at most two short sentences of reasoning, then a final line in EXACTLY the format I "
                      + "request (e.g. `CHOICE: 3` or `PICKS: 1,4`). I parse ONLY that final line — never write "
                      + "anything after it.";
            if (answerTool) format = terse && !profile().faithfulObservation
                    ? "FORMAT: call `answer` at once; no reasoning text — every extra token slows the game down."
                    : "FORMAT: at most two short sentences of reasoning, then call `answer` with your choice.";
            String base = "You are a world-class Magic: The Gathering (Legacy) player piloting \"" + seat + "\".\n"
                    // PLAN_A=""/PLAN_B="" gives a plan-free eval arm — no expert strategy injected.
                    + (plan == null || plan.isEmpty() ? "" : "GAME PLAN: " + plan + "\n")
                    + "PROTOCOL: I am the Forge rules engine. Each message gives you the CURRENT game state and ONE "
                    + "decision with a numbered list of the ONLY legal options — every option is already "
                    + "engine-validated, so pick purely on strategy. The state snapshot in each message is "
                    + "authoritative: trust it over your memory of earlier turns. "
                    + (MEMORY_TOOLS ? (nativeTools ? NativeTools.instructions() : ContextMemory.instructions())
                        : "You have NO tools and cannot inspect anything yourself — everything you need is in the message; answer directly.\n")
                    + (answerTool ? AnswerTool.instructions() : "")
                    + (coaching ? PRINCIPLES : "")
                    + format + " Play to WIN the game, not just the current decision.";
            // SMART-TIER STATIC APPENDIX (byte-identical for every decision in every game — no game state):
            // a snapshot-format legend (derived from snapshot()/candidateLabel/manaAvailable/landSummary so
            // it is exact), worked CHOICE-reasoning examples, and a Legacy strategy/keyword glossary for the
            // eval decks. Load-bearing reasoning support for high-stakes decisions, so it goes on the smart
            // tier only — the fast tier's contract is terse/no-reasoning, and bloating high-frequency cheap
            // calls would be pure latency there. Because it carries zero volatile state it keeps the system
            // message byte-stable per (seat, tier), the prompt-caching prerequisite once a cache breakpoint
            // is added (see wiki "Prompt Caching for the MTG Harness via OpenRouter").
            // NO_COACHING leaves it out with PRINCIPLES.
            return terse || !coaching ? base : base + "\n\n" + (answerTool ? toolReference() : STATIC_REFERENCE);
        }
        /**
         * The system prompt under the faithful profile (harness-epoch-8, docs/harness/PROMPT-AUDIT.md): the
         * deck, how the interface works, how to read the state, and a reference of Legacy terms. Facts
         * only: no advice on how to play (the old PRINCIPLES), no cap on reasoning, no worked examples, and
         * no text answer contract when decisions are answered by the `answer` tool. Every part is fixed for
         * the game, so the whole prompt caches.
         */
        /** The format the decks are built for (GAME_FORMAT: legacy, modern, standard, pauper, ...); the engine
         *  plays any deck, so the format is what the seat is told and what the game line records. */
        static final String GAME_FORMAT = gameFormat(System.getenv("GAME_FORMAT"));
        static String gameFormat(String env) {
            return env == null || env.isBlank() ? "legacy" : env.trim().toLowerCase(java.util.Locale.ROOT);
        }
        static String formatName(String format) {
            return format.isEmpty() ? "Legacy" : Character.toUpperCase(format.charAt(0)) + format.substring(1);
        }

        static String seatFraming(String seat, String plan, boolean nativeTools, boolean coaching, boolean answerTool) {
            return seatFraming(GAME_FORMAT, seat, plan, nativeTools, coaching, answerTool);
        }

        static String seatFraming(String format, String seat, String plan, boolean nativeTools, boolean coaching, boolean answerTool) {
            String tools = MEMORY_TOOLS ? (nativeTools ? NativeTools.instructions() : ContextMemory.instructions())
                    : "You have no lookup tools: everything you need is in the message.\n";
            String answers = answerTool ? AnswerTool.instructions() : TEXT_ANSWERS;
            String puzzle = puzzleBrief;
            String opening = puzzle != null
                    ? "You are solving a Magic: The Gathering puzzle as the player \"" + seat + "\" against one opponent. "
                      + "The position below was set up by the puzzle's author; meet its goal within its turn limit, or you lose.\n\n"
                      + "<puzzle>\n" + puzzle.trim() + "\n</puzzle>\n\n"
                    : "You are playing Magic: The Gathering, " + formatName(format) + " format, as the player \"" + seat
                      + "\" against one opponent. Your goal is to win the game.\n\n";
            return opening
                    // PLAN_A=""/PLAN_B="" (and DECKLIST=0) give an arm with no deck information.
                    + (plan == null || plan.isEmpty() ? "" : "<your_deck>\n" + plan.trim() + "\n</your_deck>\n\n")
                    + "<how_this_works>\n"
                    + "The Forge rules engine runs the game and asks you whenever you have a decision. Each message holds "
                    + "the current state of the game, then one decision and its options. The engine has already checked "
                    + "the rules: every option listed is legal, so choose on what is best for winning. The state in the "
                    + "newest message is current; earlier messages describe the past.\n"
                    + tools + answers
                    + "</how_this_works>\n\n"
                    + "<reading_the_state>\n" + SeatObservation.guide() + "</reading_the_state>\n"
                    // Terms and keywords, the same for every format and for a puzzle (2026-10-16 review: the Legacy-only
                    // reference also named the eval pool's archetypes, which no other format's seat was told).
                    + (coaching ? "\n<reference>\n" + TERMS_REFERENCE + "</reference>\n" : "");
        }
        /** How a decision is answered when it is answered in text (ANSWER_TOOL=0, or a backend without
         *  function calling). */
        static final String TEXT_ANSWERS =
                "Answer each decision on its own last line, in the form the decision asks for: `CHOICE: <number>` for "
              + "one option, `PICKS: <numbers>` for several (`PICKS: NONE` for none), `BLOCKS: <blocker>-><attacker>, "
              + "...` (or `BLOCKS: NONE`), `KEEP` or `MULLIGAN`, `NUMBER: <n>` or `CARD: <name>`. The harness reads only "
              + "that last line. If it cannot read it, it asks once more; after that it takes the decline or pass "
              + "option.\n";
        /** Terms and keywords: what they mean, not how to play. No list of the eval pool's archetypes: that
         *  told a Legacy seat which decks it could face, which a seat in any other format was not told. */
        static final String TERMS_REFERENCE =
                "Terms: tempo (mana efficiency and board presence); card advantage (more resources than the opponent); "
              + "cantrip (a cheap spell that replaces itself, such as Brainstorm, Ponder or Preordain); combo (a specific "
              + "set of cards that wins outside combat); prison or lock (denying the opponent resources).\n"
              + "Keywords: storm (the spell is copied once for each spell cast before it this turn); delve (exile cards "
              + "from your graveyard to pay generic mana); infect (damage to creatures as -1/-1 counters and to players "
              + "as poison counters; 10 poison loses); energy (a counter resource); reanimation (putting a creature from "
              + "a graveyard onto the battlefield, as with Animate Dead or Reanimate); miracle (cast a card for a cheaper "
              + "cost the turn you draw it). Cascade, prowess, flash, flying, trample, deathtouch, lifelink, hexproof "
              + "and shroud work as printed.\n";
        /** {@link #STATIC_REFERENCE} with its answer contract describing the `answer` call. */
        static String toolReference() {
            int a = STATIC_REFERENCE.indexOf("=== ANSWER CONTRACT"), b = STATIC_REFERENCE.indexOf("=== WORKED EXAMPLES");
            if (a < 0 || b < a) return STATIC_REFERENCE;
            return STATIC_REFERENCE.substring(0, a)
                    + "=== ANSWER CONTRACT (the `answer` call for each decision kind) ===\n"
                    + "- Numbered single pick -> `option`: one number from the Options list (a 'decline/none' option,\n"
                    + "  when offered, is just another number).\n"
                    + "- Choose a subset (discard, reveal, cards-for-effect, scry/surveil keep-on-top, sacrifice, attackers) ->\n"
                    + "  `options`: the numbers, honoring the stated MIN and MAX; an empty list chooses none where MIN is 0.\n"
                    + "  For scry/surveil, list the ones to KEEP ON TOP in order (first = new top card).\n"
                    + "- Declare blockers -> `blocks`: pairs of a blocker number and an attacker number (empty: no blocks).\n"
                    + "- Mulligan -> `keep`: true or false. A number -> `number`. Naming a card -> `card`.\n"
                    + "Your plan -> `set_plan` (goal, next), whenever it is offered and your plan is new or changed.\n"
                    + "An answer outside the legal values is refused with the reason and you may call again once; a\n"
                    + "second illegal call costs you the decision (I take the decline/PASS option for you).\n"
                    + "\n"
                    + STATIC_REFERENCE.substring(b);
        }
        /** The harness's advice on how to play; left out under NO_COACHING. */
        private static final String PRINCIPLES =
                      "PRINCIPLES: (1) Sequence toward your game plan, not the shiniest single play. "
                    + "(2) At instant speed with nothing to gain, PASS. "
                    + "(3) In combat, count damage and their untapped mana before committing. "
                    + "(4) Life, cards, and tempo are resources — spend them to win, not to feel safe. "
                    + "(5) Card selection is fuel: with spare mana, a draw/loot/dig spell, and no better line, "
                    + "CAST IT — repeatedly passing while holding cantrips is how combo decks lose. "
                    + "(6) If your key piece is gone (stripped/bounced), your plan is to FIND another (dig), "
                    + "not to accumulate mana with nothing to spend it on.\n";
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
              + "`mana available: {U:2 R:1 C:3} up to N mana (K untapped sources; Ancient Tomb makes 2)`: the mana\n"
              + "your untapped sources can make right now, as the engine counts it, per colour (dual lands count in\n"
              + "BOTH colours but make one at a time, so colours over-count), the total N, and any source that\n"
              + "makes more than one mana. Use it to check you can pay a line before committing.\n"
              + "`yard(n): ...`: your graveyard (reanimator/delve/flashback fuel; dredge/escape enablers).\n"
              + "`exile(n): ...`: exiled cards (delve fuel gone; adventure/impulse cards may still be playable).\n"
              + "`OPP [name] life L ...`: the opponent's public state — board, lands, `mana available`, yard, and\n"
              + "`hand: N cards` (a COUNT only; their card identities are hidden unless INTEL revealed them).\n"
              + "`OPP CAST THIS GAME: Name xN, ...`: cumulative record of every spell the opponent has actually\n"
              + "CAST this game, even if the card has since been bounced, shuffled away, or exiled — use it to\n"
              + "infer their deck and count their key resources (e.g. how many counterspells are already spent).\n"
              + "`OPP LANDS PLAYED: ...` is the same for their land drops (fetches they cracked are in their yard).\n"
              + "These count only what was CAST/PLAYED: a card you merely SAW (an INTEL reveal, or something\n"
              + "put back by scry/Brainstorm-type effects) is NOT listed here until they actually cast it.\n"
              + "Their untapped `mana available` is your read on open removal/counters — play around it.\n"
              + "`stack (top first): N pending: #k Name (player) -> targets | ...`: spells/abilities waiting to\n"
              + "resolve, top of stack first (it resolves first). `(empty)` means an open priority window with\n"
              + "nothing pending. `-> targets` shows what each is aimed at.\n"
              + "`RECENT EVENTS (oldest->newest)`: the engine's own log of what happened between your decisions\n"
              + "(opponent plays, combat results, discards). `INTEL: ...`: durable facts you learned (revealed\n"
              + "hands, cards you named). `SPENT THIS TURN: ...`: what you have already used this turn (land drop\n"
              + "AVAILABLE/USED, spells cast, attackers declared) — stated, never inferred from a missing option.\n"
              + "`YOUR PLAN (...)`: your own one-line plan and the turn you stated it — execute it, or consciously\n"
              + "deviate and say why; it survives into the opponent's turn, where holding mana matters.\n"
              + "Option lists label each card the same way: `Name (P/T)[loyalty N][tapped]/[untapped][counters]`,\n"
              + "so duplicate-named permanents are always distinguishable — read the tag before you pick.\n"
              + "`@N` after an in-play card (board, lands, and option lists alike) is that specific permanent's\n"
              + "durable object id for this game: `Swamp x2 [untapped @53 @61]` is two distinct Swamps you can\n"
              + "tell apart and track across turns — the SAME id always means the SAME physical card. Numbers in\n"
              + "plain (parentheses) inside RECENT EVENTS lines are raw engine text, NOT these ids.\n"
              + "\n"
              + "=== ANSWER CONTRACT (the exact final line I parse for each decision kind) ===\n"
              + "- Numbered single pick -> `CHOICE: <number>` (one number from the Options list; a 'decline/none'\n"
              + "  option, when offered, is just another number).\n"
              + "- Choose a subset (discard, reveal, cards-for-effect, scry/surveil keep-on-top, sacrifice) ->\n"
              + "  `PICKS: <comma-separated numbers>`, honoring the stated 'choose between MIN and MAX' bound.\n"
              + "  For scry/surveil, list the ones to KEEP ON TOP in order (first = new top card); anything you\n"
              + "  omit goes to the bottom/graveyard.\n"
              + "- Choosing ZERO when the stated MIN is 0 -> write exactly `PICKS: NONE` (an empty list must be\n"
              + "  declared, never implied by a blank line).\n"
              + "- Declare attackers -> `PICKS:` the numbers of the creatures that attack, or `PICKS: NONE` for\n"
              + "  no attack.\n"
              + "- Declare blockers -> `BLOCKS: blocker->attacker, blocker->attacker` (or `BLOCKS: NONE`).\n"
              + "- Mulligan -> exactly one word, `KEEP` or `MULLIGAN`.\n"
              + "I read ONLY that final line; write it verbatim with nothing after it. If I cannot parse it I\n"
              + "will re-ask ONCE; if I still cannot parse it I will choose the decline/PASS option for you — so\n"
              + "a malformed answer costs you the decision.\n"
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
        /** The last decideOne returned -1 because the seat picked its "none" option, not because it failed. */
        private boolean lastExplicitDecline = false;

        private int decideOne(LLMBridge b, String type, String question, List<String> options, String noneLabel) {
            return decideOne(b, type, question, options, noneLabel, null);
        }

        /** A section header Forge puts into a choice list (ChooseSourceEffect's "--PERMANENTS:--" cards, negative id):
         *  not an option; the engine asks again when one is picked (rules pilot RP_108). */
        static boolean isListDivider(Object o) {
            return o instanceof Card && ((Card) o).getId() < 0 && String.valueOf(((Card) o).getName()).startsWith("--");
        }

        /** The puzzle prover's record of what the solver chose where the game log does not say (a mode, a clone pick,
         *  a number, a card named): two solving lines that differ only there are two solutions (PuzzleSearch.unique).
         *  Card handles and the "#1"/"#2" that tell repeated labels apart are dropped, so the same card under another
         *  handle, or two identical triggers in either order, is the same choice (RP_021). */
        private void noteSolverChoice(String type, String label) {
            if (!(rnd instanceof PuzzleArms.Search) || !"Solver".equals(((PuzzleArms.Search) rnd).seat)) return;
            if (type != null && UNIQUENESS_NEUTRAL.contains(type)) return;
            PuzzleArms.Line l = PuzzleArms.searchLine;
            if (l != null && label != null && !label.startsWith("PASS") && !"[]".equals(label)) l.note(choiceKey(type, label));
        }

        /** A solver choice as the uniqueness search compares it: handles, repeat markers, tapped/sick marks, the
         *  object's current size (negative too: Fiend Artisan (-1/1), batch 14d2), a planeswalker's loyalty and counters
         *  dropped (RP_127). */
        static String choiceKey(String type, String label) {
            return (type == null ? "choice" : type) + ": " + label.replaceAll(" ?@\\d+", "")
                    .replaceAll(" #\\d+(?=[,\\]]|$)", "").replaceAll("\\[(un)?tapped\\]|\\[sick\\]", "")
                    .replaceAll(" ?\\(-?\\d+/-?\\d+\\)", "").replaceAll(" ?\\[loyalty -?\\d+\\]", "")
                    .replaceAll("\\[[^\\]]* x\\d+\\]", "");
        }

        /** Choices that make no second solution on their own (rules puzzle batch 3: 18 correct puzzles were called
         *  "multiple" for them): the order of cards moved together or of triggers and replacements, which source paid,
         *  a scry or a clash (top or bottom, batch 14b2), a confirm prompt, a library pick declined and made again,
         *  mana paid from the pool or the colour an any-colour source made (a "choose a color" effect is asked under this type too, a known blind spot). Where one
         *  decides the result, the result
         *  differs in the solver's actions or in a choice that is noted. */
        static final java.util.Set<String> UNIQUENESS_NEUTRAL = new java.util.HashSet<>(java.util.Arrays.asList(
                "order", "trigger-order", "replacement-order", "mana-sources", "mana-from-pool", "color", "scry", "clash",
                "confirm",
                "zone-change", "target", "targets"));   // targets: the log's "targeting [...]" already says them (RP_022)

        /** {@code simulate}: the `simulate` call for this decision (a target choice of a play), or null. */
        private int decideOne(LLMBridge b, String type, String question, List<String> options, String noneLabel,
                              java.util.function.Function<String, String> simulate) {
            lastExplicitDecline = false;
            clearLast();
            // Repeated labels (two blockers sharing a name, two copies of one card as targets) become
            // "label #1"/"label #2" in the prompt AND the evalDec-logged options array, in one place.
            // Indices are untouched, so callers keep indexing their original lists.
            options = disambiguate(options);
            long t0 = System.currentTimeMillis();
            // RANDOM arm: uniform over the same legal menu (incl. the decline option when present).
            if (rnd != null) {
                int n = options.size() + (noneLabel != null ? 1 : 0);
                if (n == 0) return -1;
                int c = PuzzleArms.pick(rnd, n, noneLabel != null);
                int res = c >= options.size() ? -1 : c;
                noteSolverChoice(type, res < 0 ? noneLabel : options.get(res));
                if (type != null) evalDec(type, options.size(), res, tierOf(b == smart), System.currentTimeMillis() - t0, null, options);
                return res;
            }
            int noneIdx = noneLabel != null ? options.size() + 1 : -1; // 1-based menu number of the decline option
            String base = buildDecideOnePrompt(snapshot(), question, options, noneLabel, observer != null);
            for (int attempt = 0; attempt < 2; attempt++) {
                // The retry re-sends the WHOLE prompt (state + menu): a stateless backend has no memory
                // of "the list above", so a bare correction would ask the model to re-answer blind (and
                // the faithful observer's header must be present on EVERY model ask).
                String prompt = attempt == 0 ? base
                        : base + "\n\n(Your previous reply had no parseable CHOICE line. Answer with ONE line only, nothing else: CHOICE: <a number from the list above>.)";
                List<String> menu = new ArrayList<>(options);
                if (noneLabel != null) menu.add(noneLabel);
                offerAnswer(b, AnswerTool.Spec.one(menu.size()).labels(menu));
                String reply = ask(b, prompt, simulate);
                noteAnswerVia(attempt);
                if (answerLost()) return -1;               // two invalid replies: the caller's legal default
                if (reply == null) return -2;              // bridge dead -> caller decides
                AnswerTool.Answer ta = toolAnswer();
                int c = ta != null ? ta.option : textNumber(reply, "CHOICE:");
                if (c == noneIdx && noneIdx > 0) {         // explicit decline (a real model decision)
                    lastExplicitDecline = true;
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
            return decideMany(b, type, question, options, min, max, -1);
        }

        /** Label of the cancel option a cost ask offers before its play is committed. */
        static final String COST_CANCEL_LABEL = "CANCEL this play: pick this number alone to pay nothing; go back to the action menu";

        /** A cost ask with {@link #COST_CANCEL_LABEL} as its last option when {@code canCancel}: a reply
         *  naming it returns that index alone. Null is the caller's legal default, as in decideMany. */
        private List<Integer> decideCost(String type, String question, List<String> labels, int min, int max, boolean canCancel) {
            if (!canCancel) return decideMany(smart, type, question, labels, min, max);
            List<String> withCancel = new ArrayList<>(labels);
            withCancel.add(COST_CANCEL_LABEL);
            List<Integer> picks = decideMany(smart, type, question, withCancel, min, max, labels.size());
            if (picks != null && picks.contains(labels.size())) costCancelled = true;
            return picks;
        }

        /** {@code cancelIdx} >= 0: that option cancels, and a reply naming it is that index alone,
         *  whatever the count. */
        private List<Integer> decideMany(LLMBridge b, String type, String question, List<String> options, int min, int max, int cancelIdx) {
            clearLast();
            // Same repeat-label disambiguation as decideOne (two blockers sharing a name).
            options = disambiguate(options);
            long t0 = System.currentTimeMillis();
            // RANDOM arm: uniformly sized random subset of the same legal menu.
            if (rnd != null) {
                // An order ask (every option, in an order) is searched over its orders; the other arms keep their stream.
                boolean order = ("trigger-order".equals(type) || "order".equals(type))
                        && min == options.size() && max == options.size();
                List<Integer> res = order && rnd instanceof PuzzleArms.Search ? PuzzleArms.permutation(rnd, options.size())
                        : PuzzleArms.subset(rnd, options.size(), Math.max(0, min), Math.min(max, options.size()));
                if (res == null) return null;
                lastPicks = res;
                List<String> picked = new ArrayList<>();
                for (int i : res) picked.add(options.get(i));
                if (!order) java.util.Collections.sort(picked);
                noteSolverChoice(type, String.valueOf(picked));
                if (type != null) evalDec(type, options.size(), res.size(), tierOf(b == smart), System.currentTimeMillis() - t0, null, options);
                return res;
            }
            String base = buildDecideManyPrompt(snapshot(), question, options, min, max, observer != null, null);
            for (int attempt = 0; attempt < 2; attempt++) {
                // The retry re-sends the WHOLE prompt (state + menu), not just a correction: a stateless
                // backend has no memory of "the list above", so a bare correction would ask the model to
                // re-answer blind. Same reason as decideOne.
                String prompt = attempt == 0 ? base
                        : base + "\n\n(Your previous reply had no parseable PICKS line. Answer with ONE line only, nothing else: PICKS: <"
                          + min + " to " + max + " numbers from the list above, comma-separated>.)";
                offerAnswer(b, AnswerTool.Spec.many(options.size(), min, max).cancel(cancelIdx >= 0 ? cancelIdx + 1 : -1).labels(options));
                String reply = ask(b, prompt);
                noteAnswerVia(attempt);
                if (reply == null) return null;
                java.util.LinkedHashSet<Integer> picks = new java.util.LinkedHashSet<>();
                AnswerTool.Answer ta = toolAnswer();
                if (ta != null) picks.addAll(ta.zeroBased());   // validated: in range, distinct, within bounds
                else {
                    String seg = textSegment(reply, "PICKS:");
                    if (seg == null) continue;
                    Matcher m = Pattern.compile("\\d+").matcher(seg);
                    while (m.find()) {
                        int idx = Integer.parseInt(m.group());
                        if (idx >= 1 && idx <= options.size()) picks.add(idx - 1);
                    }
                }
                if (cancelIdx >= 0 && picks.contains(cancelIdx)) {
                    // Returned from a local: evalDec clears lastPicks once its row is written, so with the game
                    // log on, `return lastPicks` handed the caller null and the cost was paid with the first
                    // legal card (Station answered CANCEL and tapped a creature; random gauntlet).
                    List<Integer> cancel = java.util.Collections.singletonList(cancelIdx);
                    lastPicks = cancel;
                    if (type != null) evalDec(type, options.size(), cancelIdx, tierOf(b == smart), 0, null, options);
                    return cancel;
                }
                List<Integer> out = new ArrayList<>(picks);
                if (out.size() > max) out = out.subList(0, max);
                if (out.size() >= min) {
                    // ms=0 -> evalDec substitutes lastSuccessMs (successful-attempt-only time); see decideOne.
                    lastPicks = out;
                    if (type != null) evalDec(type, options.size(), out.size(), tierOf(b == smart), 0, null, options);
                    return out;
                }
            }
            return null; // still short after retry -> caller's legal default
        }

        /** The last attempt of a repeatable-mode ask saw an answer over a pawprint charm's budget, on either lane: on the
         *  tool lane, any refusal over it in that attempt's conversation. The fallback's log reason; reset per attempt,
         *  and cleared by a dead bridge. */
        private boolean modesOverBudget;

        /** The answer spec of a repeatable-mode ask. A pawprint charm's budget is on it three ways (#415, #417): the
         *  tool answer is checked ({@code check}, which calls {@code onRefuse}), the Decisions bridge is told it
         *  ({@code limit}) and trims its composed answer to it ({@code budget}). */
        static AnswerTool.Spec modesSpec(List<String> options, int min, int num, int[] pawCost, int paw, Runnable onRefuse) {
            AnswerTool.Spec spec = AnswerTool.Spec.repeat(options.size(), min, num).labels(options);
            if (pawCost == null) return spec;
            return spec.limit("the picked options' {P} total at most " + paw).budget(pawCost, paw).check(a -> {
                int spent = pawprintsOf(a.zeroBased(), pawCost);
                if (spent <= paw) return null;
                onRefuse.run();
                return "those modes total " + spent + " {P}; at most " + paw + " are allowed.";
            });
        }

        /** The repeatable-mode ask (a Charm that "may choose the same mode more than once"): the model
         *  picks up to `num` indices and the SAME index may appear again. decideMany dedupes via a
         *  LinkedHashSet, so this keeps duplicates instead. Null on bridge-down, or a reply with fewer
         *  than `min` parseable picks, so the caller can take its deterministic legal default. */
        private List<Integer> decideModesRepeat(LLMBridge b, String question, List<String> options, int min, int num,
                                                int[] pawCost, int paw) {
            clearLast();
            long t0 = System.currentTimeMillis();
            if (rnd != null) {                       // an arm: picks WITH replacement (PuzzleArms.repeatModes)
                List<Integer> res = PuzzleArms.repeatModes(rnd, options.size(), min, num, pawCost, paw);
                lastPicks = res;
                List<String> picked = new ArrayList<>();
                for (int i : res) picked.add(options.get(i));
                java.util.Collections.sort(picked);   // the modes chosen, not the order they were named in (RP_034)
                noteSolverChoice("modes", String.valueOf(picked));
                evalDec("modes", options.size(), res.size(), tierOf(b == smart), System.currentTimeMillis() - t0, null, options);
                return res;
            }
            String base = buildDecideManyPrompt(snapshot(), question, options, min, num, observer != null, null);
            String overBudget = null;
            for (int attempt = 0; attempt < 2; attempt++) {
                modesOverBudget = false;
                String prompt = attempt == 0 ? base
                        : overBudget != null ? base + "\n\n(" + overBudget + ")"
                        : base + "\n\n(Answer with ONE line only: PICKS: <" + min + " to " + num
                          + " numbers from the list; the same number may repeat>.)";
                offerAnswer(b, modesSpec(options, min, num, pawCost, paw, () -> modesOverBudget = true));
                String reply = ask(b, prompt);
                noteAnswerVia(attempt);
                if (reply == null) {
                    if (!answerLost()) modesOverBudget = false;   // a dead bridge, not a refusal
                    return null;
                }
                List<Integer> out = new ArrayList<>();
                AnswerTool.Answer ta = toolAnswer();
                if (ta != null) out.addAll(ta.zeroBased());
                else {
                    String seg = textSegment(reply, "PICKS:");
                    if (seg == null) continue;
                    Matcher m = Pattern.compile("\\d+").matcher(seg);
                    while (m.find() && out.size() < Math.max(1, num)) {
                        int idx = Integer.parseInt(m.group());
                        if (idx >= 1 && idx <= options.size()) out.add(idx - 1);
                    }
                }
                // A text answer past a pawprint charm's budget is not a legal choice: refused and asked again (#415).
                int spent = pawCost == null ? 0 : pawprintsOf(out, pawCost);
                if (out.size() >= min && pawCost != null && spent > paw) {   // paw is -1 for any other charm
                    overBudget = "Refused: those modes total " + spent + " {P}; at most " + paw + " are allowed. Choose again.";
                    modesOverBudget = true;
                    p("[ROUTER] modes(repeat): over the {P} budget, " + spent + " of " + paw);
                    continue;
                }
                if (out.size() >= min) {
                    lastPicks = out;
                    evalDec("modes", options.size(), out.size(), tierOf(b == smart), 0, null, options);
                    return out;
                }
            }
            return null;
        }

        /** Even split of `total` over `n`, remainder to the front: the deterministic legal fallback
         *  for a divided spell when the model's allocation does not parse. It never picks a target,
         *  only how to divide an amount the engine already fixed. */
        private static List<Integer> evenSplit(int total, int n) {
            List<Integer> out = new ArrayList<>();
            int base = n > 0 ? total / n : 0, rem = n > 0 ? total % n : 0;
            for (int i = 0; i < n; i++) out.add(base + (i < rem ? 1 : 0));
            return out;
        }

        /** The divided-allocation ask: `n` numbers, in target order, summing to `total`. Null when the
         *  reply does not give exactly `n`, so the caller can split evenly (legal). */
        private List<Integer> decideAllocation(LLMBridge b, String question, List<String> labels, int total) {
            return decideAllocation(b, question, labels, total, null);
        }

        /** Each target of a divided spell or ability gets at least one (CR 601.2d); the split [2 0] was accepted, so
         *  Fire at two targets dealt all 2 to one (rules pilot batch 12). Combat damage may give a creature 0. */
        static final java.util.function.Function<List<Integer>, String> EACH_TARGET_AT_LEAST_ONE = a ->
                a.stream().anyMatch(v -> v < 1) ? "each chosen target must get at least 1 (CR 601.2d)." : null;

        /** {@code rule}: the reason an allocation is not legal, or null (a trampler's lethal-first rule); an answer it
         *  refuses is corrected in the conversation, or asked again with the reason. */
        private List<Integer> decideAllocation(LLMBridge b, String question, List<String> labels, int total,
                                               java.util.function.Function<List<Integer>, String> rule) {
            clearLast();
            int n = labels.size();
            if (rnd instanceof PuzzleArms.Search) {
                // The prover branches over the splits: each amount but the last drawn from what is left.
                List<Integer> out = new ArrayList<>();
                int left = total;
                int least = rule == EACH_TARGET_AT_LEAST_ONE ? 1 : 0;
                for (int i = 0; i < n - 1; i++) {
                    int most = left - least * (n - 1 - i);   // leave the targets after this one their minimum
                    int v = least + rnd.nextInt(Math.max(0, most - least) + 1);
                    out.add(v); left -= v;
                }
                out.add(left);
                return rule == null || rule.apply(out) == null ? out : null;
            }
            if (rnd != null) return evenSplit(total, n);   // RANDOM arm: the same deterministic legal split
            String base = buildDecideManyPrompt(snapshot(), question
                    + " (reply ALLOC: " + n + " numbers that sum to " + total + ")", labels, 1, Math.max(1, total),
                    observer != null, observer != null ? "Reason briefly, then end with EXACTLY: ALLOC: <" + n
                    + " numbers in the order listed, summing to " + total + ">" : null);
            for (int attempt = 0; attempt < 2; attempt++) {
                String prompt = attempt == 0 ? base
                        : base + "\n\n(Answer with ONE line only: ALLOC: <" + n + " numbers summing to " + total + ">.)";
                AnswerTool.Spec spec = AnswerTool.Spec.alloc(n, total).labels(labels);
                if (rule != null) spec.check(ans -> rule.apply(ans.amounts));
                offerAnswer(b, spec);
                String reply = ask(b, prompt);
                noteAnswerVia(attempt);
                if (reply == null) return null;
                AnswerTool.Answer ta = toolAnswer();
                if (ta != null) return new ArrayList<>(ta.amounts);   // validated: n amounts summing to total, and the rule
                String seg = textSegment(reply, "ALLOC:");
                if (seg == null) continue;
                List<Integer> out = new ArrayList<>();
                Matcher m = Pattern.compile("\\d+").matcher(seg);
                while (m.find() && out.size() < n) out.add(Integer.parseInt(m.group()));
                if (out.size() != n) continue;             // the caller re-checks the sum
                String why = rule == null ? null : rule.apply(out);
                if (why == null) return out;
                base = base + "\n\nThat split is not legal: " + why + " Give another.";
            }
            return null;
        }

        /** A pawprint charm's {P} budget ("choose up to five {P} worth of modes"), or -1 for any other modal ability.
         *  Forge's engine does not check it, only its own controllers do (PlayerControllerHuman, CharmAi), so the seat
         *  and the arms must (#415). */
        static int pawprintBudget(SpellAbility sa) {
            if (sa == null || !sa.hasParam("Pawprint")) return -1;
            return forge.game.ability.AbilityUtils.calculateAmount(sa.getHostCard(), sa.getParam("Pawprint"), sa);
        }

        /** Each mode's {P}, in the order of `possible` (0 for a mode with none). */
        static int[] pawprints(List<AbilitySub> possible) {
            int[] out = new int[possible.size()];
            for (int i = 0; i < out.length; i++) {
                AbilitySub ab = possible.get(i);
                out[i] = ab.hasParam("Pawprint")
                        ? forge.game.ability.AbilityUtils.calculateAmount(ab.getHostCard(), ab.getParam("Pawprint"), ab) : 0;
            }
            return out;
        }

        /** The fixed legal modes when the hook fails: within a pawprint charm's budget too (#415). */
        static List<AbilitySub> budgetSafeDefault(SpellAbility sa, List<AbilitySub> possible, int min, int num,
                                                  boolean allowRepeat) {
            int paw;
            int[] cost;
            try {
                paw = allowRepeat ? pawprintBudget(sa) : -1;
                cost = paw >= 0 ? pawprints(possible) : null;
            } catch (Exception ignore) {
                if (allowRepeat && sa != null && sa.hasParam("Pawprint")) return new ArrayList<>();   // unreadable: none
                return defaultModes(possible, min, num, allowRepeat);
            }
            return defaultModes(possible, min, num, allowRepeat, cost, paw);
        }

        /** The {P} the picked modes total. */
        static int pawprintsOf(List<Integer> picks, int[] cost) {
            int total = 0;
            for (int i : picks) total += cost[i];
            return total;
        }

        /** `n` copies of one mode: a forced single mode under allowRepeat ("choose three, you may
         *  choose the same mode more than once") is that mode three times. */
        private static List<AbilitySub> repeated(AbilitySub sub, int n) {
            List<AbilitySub> out = new ArrayList<>();
            for (int i = 0; i < Math.max(1, n); i++) out.add(sub);
            return out;
        }

        /** Deterministic legal modes with no model call, never Forge AI: without allowRepeat the first
         *  `min` distinct modes; with it, distinct first and then repeats of the first mode when the
         *  count exceeds the menu (legal only because allowRepeat permits it). */
        private static List<AbilitySub> defaultModes(List<AbilitySub> possible, int min, int num, boolean allowRepeat) {
            return defaultModes(possible, min, num, allowRepeat, null, -1);
        }

        /** As above; with a {P} budget (a pawprint charm, #415): the fewest modes allowed (none for every pawprint card,
         *  the do-nothing option, docs/STANDARDS.md section 8), taken in order while they fit: legal. */
        private static List<AbilitySub> defaultModes(List<AbilitySub> possible, int min, int num, boolean allowRepeat,
                                                     int[] cost, int budget) {
            List<AbilitySub> out = new ArrayList<>();
            if (allowRepeat && cost != null && budget >= 0) {
                int left = budget, want = Math.max(0, min);
                for (int i = 0; i < possible.size() && out.size() < want; i++) {
                    while (cost[i] <= left && out.size() < want) {
                        out.add(possible.get(i));
                        left -= cost[i];
                        if (cost[i] == 0) break;
                    }
                }
                return out;
            }
            if (!allowRepeat) {
                for (int i = 0; i < Math.min(Math.max(min, 0), possible.size()); i++) out.add(possible.get(i));
                return out;
            }
            int target = Math.max(1, Math.max(num, Math.max(min, 0)));
            for (int i = 0; i < possible.size() && out.size() < target; i++) out.add(possible.get(i));
            while (out.size() < target) out.add(possible.get(0));
            return out;
        }

        /** The second ask of a play decision whose first reply had no parseable choice: the same state
         *  and menu, and the correction. Never the correction alone. */
        static String playRetryPrompt(String stateAndMenu, int passIdx) {
            return stateAndMenu + "\n\n(Your previous reply had no parseable choice. Answer with ONE line only, nothing else: "
                    + "CHOICE: <a number 1.." + passIdx + " from the list above>.)";
        }

        /** If the reply asks for a lookahead preview (`SIMULATE: n`), return the 1-based option
         *  number; else -1. n must be within the castable list. */
        static int parseSimulateOption(String reply, int nSpells) {
            if (reply == null || nSpells <= 0) return -1;
            String seg = lastSegment(reply, "SIMULATE:");
            if (seg == null) return -1;
            Matcher m = Pattern.compile("\\d+").matcher(seg);
            if (!m.find()) return -1;
            int n = Integer.parseInt(m.group());
            return (n >= 1 && n <= nSpells) ? n : -1;
        }

        /** The optional target hint after a `SIMULATE: <n> <target>` request, or null when absent. */
        static String simulateTargetHint(String reply) {
            if (reply == null) return null;
            String seg = lastSegment(reply, "SIMULATE:");
            if (seg == null) return null;
            Matcher m = Pattern.compile("^\\s*\\d+\\s*(.*)$", Pattern.DOTALL).matcher(seg);
            if (!m.matches()) return null;
            String t = m.group(1).replaceAll("\\s+", " ").trim();
            return t.isEmpty() ? null : t;
        }

        // ---------------- full-context serializer ----------------

        /** The state for a prompt. Asked in the middle of a real payment (the colour an any-colour source makes, the
         *  engine patch), rendering it test-pays other spells for their "(castable)" tags, and each test payment
         *  clears and refills the AI memory the real payment is using (PAYS_TAP_COST): the second Lotus Petal was
         *  then skipped and Ad Nauseam's last {1} went unpaid (runs/c3/ant-storm-default T7). The memory is put
         *  back as it was. */
        String snapshot() {
            if (!payingForReal) return renderSnapshot();
            java.util.Map<AiCardMemory.MemorySet, java.util.List<Card>> mem = snapshotAiMemory();
            try { return renderSnapshot(); } finally { restoreAiMemory(mem); }
        }

        private String renderSnapshot() {
            // Building a prompt is pricing from start to end: no hook it reaches may ask the model, whichever hook it is
            // (a pool choice reached from the hand's tags asked, and its own prompt priced the hand again).
            boolean was = PRICING_FOR_SNAPSHOT;
            PRICING_FOR_SNAPSHOT = true;
            try { return renderSnapshotPriced(); } finally { PRICING_FOR_SNAPSHOT = was; }
        }

        private String renderSnapshotPriced() {
            if (observer != null) {
                // Faithful path: capture the engine log into the seat stream (redacted, with private
                // reveals interleaved), render history from THAT stream, then the viewer-aware state.
                // Same watermark discipline as below: only the renderer may move it.
                observer.capture();
                GameHistory.Rendered hist = GameHistory.renderEvents(observer.events(), ctx, eventWatermark);
                observer.setProfile(oppProfile);
                observer.setOwnDeckText(deckPlan);
                observer.setTrackOwnCards(tracksOwnCards(isPuzzleSolver(getLobbyPlayer())));
                observer.setDeckLines(deckLines);
                observer.setPlanWhen(planWhen);
                String s = observer.render(seat, ctx, hist.text, turnPlan, planTurn);
                if (hist.watermark != GameHistory.Rendered.KEEP_WATERMARK) eventWatermark = hist.watermark;
                return s;
            }
            // The watermark comes back FROM the renderer and from nowhere else. An earlier version
            // recounted the log here independently; when rendering threw, the events section was
            // dropped while the watermark still advanced past those events, blinding the seat to
            // them permanently with no notice anywhere. See GameHistory.Rendered.
            GameHistory.Rendered hist = GameHistory.render(getPlayer().getGame(), ctx, eventWatermark);
            String s = buildSnapshot(getPlayer(), seat, intel, turnPlan, planTurn, ctx, hist.text);
            if (hist.watermark != GameHistory.Rendered.KEEP_WATERMARK) eventWatermark = hist.watermark;
            return s;
        }
        /** Shared, state-free snapshot builder (extracted from {@link #snapshot()} so the AI-logging
         *  seat can produce a byte-format-identical game-state prompt). The instance {@code snapshot()}
         *  above just forwards its per-seat fields, so the LLM seat's output is unchanged; the
         *  ai-teacher seat passes an empty intel deque + no turn plan (it accumulates neither). */
        /** The seat's OWN stated plan, kept for as long as it is relevant. It is deliberately NOT
         *  dropped when the turn passes: "hold up UU and counter their payoff" only means anything on
         *  THEIR turn, which a same-turn gate erased. It carries the turn it was stated and is flagged
         *  as possibly stale once the turn has moved on, so the model can follow it or consciously
         *  deviate — and say which. Shared by both renderers so they cannot drift. */
        static String planText(String turnPlan, int planTurn, int nowTurn) {
            if (turnPlan == null || turnPlan.isEmpty()) return "";
            StringBuilder b = new StringBuilder("YOUR PLAN (you stated this at T").append(planTurn).append(" — ");
            b.append(planTurn == nowTurn
                    ? "this turn; execute it, or consciously deviate and say why"
                    : "a previous turn; the board may have changed — re-state it or consciously continue");
            b.append("): ").append(turnPlan).append('\n');
            return b.toString();
        }

        /** The faithful profile's plan line (observation v22). The plan is the seat's own and can be
         *  replaced at ANY decision with a new PLAN line ({@link #adoptPlan}): a goal and the steps
         *  toward it, which change when the opponent acts or a card is drawn. It says when it was
         *  stated, so the model can read what happened since in RECENT EVENTS and judge it. */
        static String revisablePlanText(String turnPlan, String planWhen, int planTurn, int nowTurn) {
            if (turnPlan == null || turnPlan.isEmpty()) return "";
            return "YOUR PLAN (yours, stated " + (planWhen == null || planWhen.isEmpty() ? "at T" + planTurn : "at " + planWhen)
                    + (planTurn == nowTurn ? ", this turn" : ", an earlier turn")
                    + "; events after that happened since. It is a note, not a commitment: check it against the state above at "
                    + "this decision, and when it no longer fits, write a new "
                    + "`PLAN: GOAL: <what you are working toward> NEXT: <the steps>` line before your answer): "
                    + turnPlan + "\n";
        }

        static String buildSnapshot(Player me, String seat, java.util.Collection<String> intel, String turnPlan, int planTurn) {
            // Stateless callers (the ai-teacher logging seat, LlmSpikeFork's enumerate snapshot) have
            // no per-seat decision history, so they render with watermark -1: under FIXED_TAIL that is
            // the historical tail, and under the memory profiles it is "everything so far, capped at
            // recentEventsMax" — NOT the literal 10-event tail. Either way it is a tail, not a gap, so
            // teacher rows do not hash-pair with student rows under a memory profile — one more entry
            // on the documented list alongside the pre-existing empty-intel and no-turn-plan
            // differences.
            return buildSnapshot(me, seat, intel, turnPlan, planTurn, CTX,
                                 GameHistory.render(me.getGame(), CTX, -1).text);
        }

        static String buildSnapshot(Player me, String seat, java.util.Collection<String> intel, String turnPlan,
                                    int planTurn, ContextProfile ctx, String history) {
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
                s.append("  yard(").append(me.getCardsIn(ZoneType.Graveyard).size()).append("): ").append(names(me.getCardsIn(ZoneType.Graveyard), true, ctx.yardCap)).append('\n');
                if (!me.getCardsIn(ZoneType.Exile).isEmpty())
                    s.append("  exile(").append(me.getCardsIn(ZoneType.Exile).size()).append("): ").append(exileNames(me.getCardsIn(ZoneType.Exile), ctx.exileCap)).append('\n');
                if (opp != null) {
                    s.append("OPP [").append(opp.getName()).append("] life ").append(opp.getLife()).append(playerCounters(opp)).append('\n');
                    s.append("  board: ").append(perms(opp)).append('\n');
                    // Opp lands are public info and load-bearing: untapped mana = removal/counter risk,
                    // and utility lands (Wasteland, Karakas) are threats worth playing around.
                    s.append("  lands: ").append(landSummary(opp)).append('\n');
                    s.append("  mana available: ").append(manaAvailable(opp)).append('\n');
                    s.append("  yard(").append(opp.getCardsIn(ZoneType.Graveyard).size()).append("): ").append(names(opp.getCardsIn(ZoneType.Graveyard), true, ctx.yardCap)).append('\n');
                    if (!opp.getCardsIn(ZoneType.Exile).isEmpty())
                        s.append("  exile(").append(opp.getCardsIn(ZoneType.Exile).size()).append("): ").append(exileNames(opp.getCardsIn(ZoneType.Exile), ctx.exileCap)).append('\n');
                    s.append("  hand: ").append(opp.getCardsIn(ZoneType.Hand).size()).append(" cards\n");
                    // Opponent memory (task #8): cumulative CAST/LAND history derived from the engine's
                    // own game log at snapshot time — stateless (no listener, replay-safe, works for the
                    // ai-teacher hashing path too) and immune to the INTEL deque cap. Deliberately
                    // separate from INTEL reveals: "cast" is ground truth about their deck; "seen" is not
                    // (a Brainstorm put-back was seen but may never be cast).
                    String oppCasts = castHistory(g, opp, GameLogEntryType.STACK_ADD, " cast ", ctx.castHistoryCap);
                    if (!oppCasts.isEmpty()) s.append("  OPP CAST THIS GAME: ").append(oppCasts).append('\n');
                    String oppLands = castHistory(g, opp, GameLogEntryType.LAND, " played ", ctx.castHistoryCap);
                    if (!oppLands.isEmpty()) s.append("  OPP LANDS PLAYED: ").append(oppLands).append('\n');
                }
                int stackSize = g.getStack().size();
                s.append("stack (top first): ").append(stackSize == 0 ? "(empty)" : (stackSize + " pending: " + safeStackTop(g))).append('\n');
                // HISTORY: rendered by GameHistory from the engine's own game log, at whatever
                // resolution this run's context profile asks for. Built by the CALLER so the
                // watermark it returns cannot be separated from the text it produced.
                s.append(history);
                if (!intel.isEmpty()) s.append("INTEL: ").append(String.join(" | ", intel)).append('\n');
                // Same-turn plan continuity: the line the model declared at its first main-phase decision.
                if (!turnPlan.isEmpty())
                    s.append(planText(turnPlan, planTurn, ph.getTurn()));
                return s.toString();
            } catch (Exception e) {
                return "== (state unavailable) ==";
            }
        }
        /** Cumulative "<Name> xN, ..." history of one player's casts (STACK_ADD + " cast ") or land
         *  drops (LAND + " played "), derived from the full engine game log at snapshot time.
         *  Names come from the LOG MESSAGE, not the entry's sourceCard — the message records what was
         *  publicly announced (a face-down cast logs as "Morph"), so this can never leak hidden info.
         *  Ordered by count desc then first-seen; capped at {@code cap} distinct names for prompt
         *  economy (v1-compat pins the historical 25). */
        static String castHistory(Game g, Player p, GameLogEntryType type, String verb, int cap) {
            try {
                // Prefix cannot cross-match similarly-named players ("Bob" vs "Bob 2"): the key
                // includes the full verb (" cast "/" played "), so "Bob 2 cast X" never starts
                // with "Bob cast ". Verified against GameLogFormatter's message templates.
                String prefix = p.getName() + verb;
                java.util.LinkedHashMap<String, Integer> counts = new java.util.LinkedHashMap<>();
                List<GameLogEntry> entries = g.getGameLog().getLogEntriesForTypes(EnumSet.of(type)); // newest-first
                for (int i = entries.size() - 1; i >= 0; i--) {                                      // oldest-first
                    String msg = entries.get(i).message();
                    if (msg == null || !msg.startsWith(prefix)) continue;
                    String name = msg.substring(prefix.length());
                    int cut = name.indexOf(" targeting");
                    if (cut >= 0) name = name.substring(0, cut);
                    name = name.trim();
                    if (name.endsWith(".")) name = name.substring(0, name.length() - 1);
                    // LAND messages append the engine card id ("Marsh Flats (98)") — strip it so
                    // repeat plays of same-named cards aggregate into one "Name xN" entry.
                    name = name.replaceAll(" \\(\\d+\\)$", "");
                    if (name.isEmpty()) continue;
                    counts.merge(name, 1, Integer::sum);
                }
                if (counts.isEmpty()) return "";
                java.util.List<java.util.Map.Entry<String, Integer>> es = new java.util.ArrayList<>(counts.entrySet());
                es.sort((a, b) -> b.getValue() - a.getValue());
                StringBuilder b = new StringBuilder();
                int shown = 0;
                for (java.util.Map.Entry<String, Integer> e : es) {
                    if (shown++ == cap) { b.append(", ...(").append(es.size() - cap).append(" more)"); break; }
                    if (b.length() > 0) b.append(", ");
                    b.append(e.getKey());
                    if (e.getValue() > 1) b.append(" x").append(e.getValue());
                }
                return b.toString();
            } catch (Exception e) { return ""; }
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
                        b.append(' ').append(sa.getHostCard() != null ? safeName(sa.getHostCard()) : String.valueOf(sa));
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
            // name -> [untappedCount, tappedCount] + the @ids per state, so the
            // aggregate stays compact but each physical land remains nameable
            // (Wasteland targets, "tap @12 not @31" — 2026-08-02 directive).
            java.util.LinkedHashMap<String, int[]> byName = new java.util.LinkedHashMap<>();
            java.util.LinkedHashMap<String, StringBuilder[]> idsByName = new java.util.LinkedHashMap<>();
            for (Card c : pl.getCardsIn(ZoneType.Battlefield)) {
                if (!c.isLand()) continue;
                total++;
                boolean tapped = c.isTapped();
                if (!tapped) untapped++;
                int[] counts = byName.computeIfAbsent(c.getName(), k -> new int[2]);
                counts[tapped ? 1 : 0]++;
                StringBuilder[] ids = idsByName.computeIfAbsent(c.getName(),
                        k -> new StringBuilder[]{new StringBuilder(), new StringBuilder()});
                try {
                    StringBuilder tgt = ids[tapped ? 1 : 0];
                    if (tgt.length() > 0) tgt.append(' ');
                    tgt.append('@').append(c.getId());
                } catch (Exception ignore) { }
            }
            StringBuilder kinds = new StringBuilder();
            for (Map.Entry<String, int[]> e : byName.entrySet()) {
                int u = e.getValue()[0], t = e.getValue()[1];
                StringBuilder[] ids = idsByName.get(e.getKey());
                if (u > 0) { if (kinds.length() > 0) kinds.append(", "); kinds.append(e.getKey()).append(" x").append(u).append(" [untapped").append(ids[0].length() > 0 ? " " + ids[0] : "").append(']'); }
                if (t > 0) { if (kinds.length() > 0) kinds.append(", "); kinds.append(e.getKey()).append(" x").append(t).append(" [tapped").append(ids[1].length() > 0 ? " " + ids[1] : "").append(']'); }
            }
            return untapped + " untapped / " + total + " total"
                    + (kinds.length() == 0 ? "" : " (" + kinds + ")");
        }
        /** One permanent's mana right now, read from the engine the way Forge's own AI reads it
         *  ({@code ComputerUtilMana.getAvailableManaEstimate}): each mana ability the engine says can be
         *  activated now ({@code canPlay}: untapped, not summoning sick, not suppressed), with the amount
         *  the engine computes for one activation ({@code amountOfManaGenerated}: Ancient Tomb 2, Urza's
         *  Tower 3 beside a Mine and a Power Plant), less any mana it costs to activate. The source makes
         *  the largest of its abilities' amounts (one activation, it taps), in any colour one of them makes
         *  (a filter's colours included, as its ability turns one mana into them). Mana that can be spent only on
         *  some spells (Great Hall of the Biblioplex's any colour, for instants and sorceries) is kept apart, in
         *  the card's own words. Nothing is paid or chosen: this is a read. */
        static final class ManaSource {
            final String name; final int amount; final boolean[] colors = new boolean[6];
            /** Mana abilities whose mana can be spent only on some things (Great Hall: instants and sorceries),
             *  in the card's own words; they are not in the amount or the colours. */
            final List<String> restricted = new ArrayList<>();
            ManaSource(String name, int amount) { this.name = name; this.amount = amount; }
        }
        static boolean canActivateNow(SpellAbility ab, Player pl) {
            try { ab.setActivatingPlayer(pl); return PlayerControllerLLM.playable(ab, pl); } catch (Exception e) { return false; }
        }
        static List<ManaSource> manaSources(Player pl) {
            List<ManaSource> out = new ArrayList<>();
            for (Card card : pl.getCardsIn(ZoneType.Battlefield)) {
                int best = 0; boolean[] can = new boolean[6];
                List<String> restricted = new ArrayList<>();
                try {
                    for (SpellAbility ab : card.getManaAbilities()) {
                        forge.game.spellability.AbilityManaPart mp = ab.getManaPart();
                        if (mp == null) continue;
                        if (!canActivateNow(ab, pl)) continue;
                        String rule = mp.getManaRestrictions(), extra = mp.getExtraManaRestriction();
                        if ((rule != null && !rule.isEmpty()) || (extra != null && !extra.isEmpty())) {
                            restricted.add(named(flat(ab.getDescription()), ab.getHostCard()));
                            continue;
                        }
                        int cost = ab.getPayCosts() != null && ab.getPayCosts().getCostMana() != null
                                ? ab.getPayCosts().getCostMana().convertAmount() : 0;
                        int made = ab.amountOfManaGenerated(true) - cost;
                        // A filter ({1},{T}: two colours) adds no mana but changes its colour: its colours count, its 0 does not.
                        if (made < 0) continue;
                        best = Math.max(best, made);
                        String produced = mp.getOrigProduced();
                        if (produced == null) continue;
                        for (String tok : produced.toUpperCase(Locale.ROOT).split("\\s+")) {
                            switch (tok) {
                                case "W": can[0] = true; break;
                                case "U": can[1] = true; break;
                                case "B": can[2] = true; break;
                                case "R": can[3] = true; break;
                                case "G": can[4] = true; break;
                                case "C": can[5] = true; break;
                                case "ANY": can[0] = can[1] = can[2] = can[3] = can[4] = true; break;
                                default: break; // COMBO is followed by its colours; CHOSEN/SPECIAL name no colour until made
                            }
                        }
                        // A colour the card decides (Chrome Mox: the imprinted card's colours; a chosen colour) is asked of
                        // the engine, as its own payment code does (runs/c3 T11: Chrome Mox imprinting Brainstorm read as no colour).
                        String[] shorts = {"W", "U", "B", "R", "G", "C"};
                        if (ab.getApi() == forge.game.ability.ApiType.ManaReflected) {
                            java.util.Set<String> refl = forge.game.card.CardUtil.getReflectableManaColors(ab);
                            for (int i = 0; i < 6; i++) if (refl.contains(shorts[i]) || refl.contains(MagicColor.toLongString(MagicColor.fromName(shorts[i])))) can[i] = true;
                        } else {
                            for (int i = 0; i < 6; i++) if (!can[i] && mp.canProduce(shorts[i], ab)) can[i] = true;
                        }
                    }
                } catch (Exception ignore) { }
                if (best == 0 && restricted.isEmpty()) continue;
                ManaSource m = new ManaSource(card.getName(), best);
                if (best > 0) System.arraycopy(can, 0, m.colors, 0, 6);
                m.restricted.addAll(restricted);
                out.add(m);
            }
            return out;
        }
        /** [0]=W [1]=U [2]=B [3]=R [4]=G [5]=C: the mana the untapped sources can make in that colour (a
         *  source counts its whole amount under every colour it can make, so the colours add up to more than
         *  can be spent); [6] the number of sources; [7] the total mana they make, plus the mana pool. */
        static int[] untappedManaTally(Player pl) {
            int[] t = new int[8];
            for (ManaSource m : manaSources(pl)) {
                if (m.amount == 0) continue;   // only restricted mana
                for (int i = 0; i < 6; i++) if (m.colors[i]) t[i] += m.amount;
                t[6]++;
                t[7] += m.amount;
            }
            try { t[7] += pl.getManaPool().totalMana(); } catch (Exception ignore) { }
            return t;
        }
        /** "{U:2 R:1 C:4} up to 5 mana (4 untapped sources; Ancient Tomb makes 2)": colours with nothing are
         *  omitted, and a source that makes more than one mana is named with its amount. */
        static String manaAvailable(Player pl) {
            List<ManaSource> srcs = manaSources(pl);
            int[] t = untappedManaTally(pl);
            String[] label = {"W", "U", "B", "R", "G", "C"};
            StringBuilder b = new StringBuilder("{");
            for (int i = 0; i < 6; i++) {
                if (t[i] == 0) continue;
                if (b.length() > 1) b.append(' ');
                b.append(label[i]).append(':').append(t[i]);
            }
            b.append("} up to ").append(t[7]).append(" mana (").append(t[6]).append(" untapped sources");
            int pool = 0;
            try { pool = pl.getManaPool().totalMana(); } catch (Exception ignore) { }
            if (pool > 0) b.append("; ").append(pool).append(" already in the mana pool");
            StringBuilder big = new StringBuilder();
            for (ManaSource m : srcs) {
                if (m.amount < 2) continue;
                big.append(big.length() == 0 ? "; " : ", ").append(m.name).append(" makes ").append(m.amount);
            }
            // Restricted mana, once per kind of card: "Great Hall of the Biblioplex x2: {T}, Pay 1 life: Add ...".
            java.util.LinkedHashMap<String, Integer> kinds = new java.util.LinkedHashMap<>();
            for (ManaSource m : srcs) for (String r : m.restricted) kinds.merge(m.name + "\u0000" + r, 1, Integer::sum);
            StringBuilder lim = new StringBuilder();
            for (java.util.Map.Entry<String, Integer> e : kinds.entrySet()) {
                String[] nr = e.getKey().split("\u0000", 2);
                lim.append(lim.length() == 0 ? "; also mana only for some spells, not counted above: " : "; ")
                        .append(nr[0]).append(e.getValue() > 1 ? " x" + e.getValue() : "").append(": ").append(nr[1]);
            }
            return b.append(big).append(lim).append(')').toString();
        }
        /** Hand listing with mana cost + P/T + a compact affordability tag ("(castable)" /
         *  "(need 1 more B)") reusing the SAME ComputerUtilMana.canPayManaCost check that pre-filters
         *  the real CAST menu (see chooseSpellAbilityToPlay) — replaces "can I cast this?" guesswork
         *  with an answer instead of adding bloat. The color-shortfall breakdown only fires for plain
         *  colored/generic/colorless costs (hybrid/Phyrexian/X costs fall back to a plain
         *  "(not castable now)" — naming a single missing color there would be a guess, not a fact). */
        /** Hand-pricing marker while {@link #affordability} runs: its canPayManaCost check goes through
         *  CostAdjustment.adjust, whose cost-reduction pick re-enters the controller — an ask inside the
         *  pricing walk builds another snapshot and overflows the stack. Hooks that see this are pricing
         *  a display hint, not paying a card: no model ask, and no fallback row — the human controller
         *  takes the first legal option without prompting here (full control off), so the seat does too. */
        private static volatile boolean PRICING_FOR_SNAPSHOT = false;
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
        static String affordability(Player pl, Card c, int[] tally) {
            try {
                SpellAbility sa = c.getFirstSpellAbility();
                if (sa == null) return null;
                if (sa.getActivatingPlayer() == null) sa.setActivatingPlayer(pl);
                // Restored, never cleared: under an outer pricing a cleared flag let the next hook ask (StackOverflowError).
                boolean was = PRICING_FOR_SNAPSHOT;
                PRICING_FOR_SNAPSHOT = true;
                try { if (seatCanPay(sa, pl, 0, false)) return "(castable)"; }
                finally { PRICING_FOR_SNAPSHOT = was; }
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
                if (simple && why.length() == 0 && tally.length > 7 && mc.getCMC() > tally[7])
                    why.append("need ").append(mc.getCMC() - tally[7]).append(" more mana");
                return why.length() > 0 ? "(" + why + ")" : "(not castable now)";
            } catch (Exception e) { return "(not castable now)"; }
        }
        /** Exile-zone listing with face-down masking. Exile is the zone where hidden face-down cards
         *  live long-term (Foretell, face-down "exile until" effects); Card.getName() returns the TRUE
         *  name regardless of facing (masking exists only at the CardView layer), so listing exile
         *  through names() would leak hidden information into the prompt — the exact failure mode
         *  castHistory avoids by reading log messages. Verifier-caught (2026-07-26). */
        private static String exileNames(Iterable<Card> cards, int cap) {
            StringBuilder b = new StringBuilder(); int i = 0;
            for (Card c : cards) {
                if (i++ >= cap) { b.append(", ..."); break; }
                if (b.length() > 0) b.append(", ");
                b.append(c.isFaceDown() ? "a face-down card" : c.getName());
            }
            return b.length() == 0 ? "(none)" : b.toString();
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
                // Same @id the menus carry (candidateLabel) — the snapshot and the
                // targeting menus must name objects identically or the id is noise.
                try { b.append('@').append(c.getId()); } catch (Exception ignore) { }
            }
            return b.length() == 0 ? "(no nonland permanents)" : b.toString();
        }

        // ---------------- existing 4 decisions, now with full context + smart bridge ----------------

        @Override
        public boolean mulliganKeepHand(Player firstPlayer, int cardsToReturn) {
            clearLast();
            lastShadowChoice = shadowMulligan(firstPlayer, cardsToReturn); // all mulligan evalDec paths carry it
            // FORK (rollout, additive — inert unless LlmSpikeFork installed a forcer): forced mulligan.
            // 0 or -1 (decline) == KEEP, 1 == MULLIGAN. Deliberately consulted BEFORE the floor/heuristic
            // guards — the forcer exists to explore the branch the policy would not take — and logged
            // through the same evalDec funnel so the row records what actually happened.
            Integer forkForcedMull = forkForce(player, seat, "mulligan", 2,
                    java.util.Arrays.asList("KEEP", "MULLIGAN"));
            if (forkForcedMull != null) {
                boolean forcedKeep = forkForcedMull.intValue() != 1;
                evalDec("mulligan", 2, forcedKeep ? 0 : 1, "auto", 0, null,
                        java.util.Arrays.asList("KEEP", "MULLIGAN"));
                p("[ROUTER] mulligan: FORK-FORCED " + (forcedKeep ? "KEEP" : "MULLIGAN"));
                return forcedKeep;
            }
            CardCollectionView hand = player.getCardsIn(ZoneType.Hand);
            int lands = CardLists.filter(hand, CardPredicates.LANDS).size();
            int size = hand.size();
            // The 5-card floor is a harness shortcut like the land heuristic below: on only with MULLIGAN_HEURISTIC=1.
            // By default a mulligan at any size is the seat's (runs/luna-std/g13: the harness kept a 1-land five).
            if (MULL_HEURISTIC && size <= 5) {
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
            // With MULLIGAN_HEURISTIC=1 only the extremes (0-1 lands, or 5+ = flood) above five cards reach the model.
            if (!smartOk()) { auto("mulligan(no-model)->KEEP"); return true; } // deterministic legal, not the AI
            StringBuilder n = new StringBuilder();
            for (Card c : hand) { if (n.length() > 0) n.append(", "); n.append(c.getName()); }
            // Shared template (single source; the ai-teacher seat rebuilds byte-identically via the same
            // builder). afterKeep == size: this Forge deals a shrinking hand each mulligan; you keep what you see.
            String basePrompt = buildMulliganPrompt(size, lands, n.toString(),
                    getPlayer() == firstPlayer, observer != null);
            // Faithful: the mulligan is asked with the observation and a DECISION line like every other
            // ask, and its retry re-sends the whole ask (it was a bare hand list, and a bare correction).
            if (observer != null) basePrompt = snapshot() + "\nDECISION: " + basePrompt;
            p("[ROUTER] mulligan: " + lands + "/" + size + " lands -> " + routeLabel(smart.getModel(), rnd != null));
            List<String> mullOpts = java.util.Arrays.asList("KEEP", "MULLIGAN");
            // Retry once on an unparseable reply (matching every decideOne/decideMany site). Read the
            // LAST keyword, not the first: the prompt demands the decision be the final word, and the
            // reasoning body routinely contains "mulligan"/"mulliganing" (e.g. "no reason to mulligan
            // ... KEEP"). First-indexOf matched that prose token and inverted the model's actual choice
            // — observed on 10/31 mulligans in haiku-vs-ai-2, every one a KEEP shipped as a mulligan.
            for (int attempt = 0; attempt < 2; attempt++) {
                String retry = "That reply contained neither KEEP nor MULLIGAN. Answer with ONE word only: KEEP or MULLIGAN.";
                String prompt = attempt == 0 ? basePrompt : observer != null ? basePrompt + "\n\n(" + retry + ")" : retry;
                // The retry is the whole decision again when the tool is offered or under the faithful profile:
                // a one-line correction would reach a stateless backend with no hand in it.
                offerAnswer(smart, AnswerTool.Spec.keep());
                String reply = ask(smart, attempt > 0 && nextAnswer != null && observer == null ? basePrompt + "\n\n" + retry : prompt);
                noteAnswerVia(attempt);
                if (answerLost()) break;   // two invalid replies: the deterministic KEEP below, recorded as unparsed
                // Bridge dead -> the SAME deterministic KEEP the unparsed-after-retry branch below
                // uses, never Forge AI (this file's blanket hard invariant: a dead bridge always takes
                // the deterministic legal default, same ladder as confirm/attackers/surveil).
                if (reply == null) {
                    evalDec("mulligan", 2, 0, "auto", 0, "mulligan(bridge-down)->KEEP", mullOpts);
                    p("[ROUTER] mulligan: KEEP (bridge-down)");
                    return true;
                }
                AnswerTool.Answer ta = toolAnswer();
                boolean keep;
                if (ta != null) keep = ta.keep;
                else {
                    String r = reply.toUpperCase();
                    int k = r.lastIndexOf("KEEP"), mu = r.lastIndexOf("MULLIGAN");
                    if (k < 0 && mu < 0) continue; // no decision token -> retry, then deterministic fallback below
                    keep = k >= 0 && (mu < 0 || k > mu);
                }
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
                if (!okFor("ability-to-play")) { auto("ability-to-play(no-budget)->first[" + host + "]"); return abilities.get(0); }
                List<String> labels = new ArrayList<>();
                for (SpellAbility sa : abilities) labels.add(labelOf(sa));
                String q = "Choose HOW to play " + host + " -- pick the specific cast mode / cost variant.";
                int c = decideOne(bridgeFor("ability-to-play"), "ability-to-play", q, labels, null);
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
                if (defender == null) { auto("attackers(no-defender)->none"); return; } // declare nothing, never the AI
                CardCollection legal = new CardCollection();
                for (Card c : attacker.getCreaturesInPlay()) if (CombatUtil.canAttack(c, defender)) legal.add(c);
                if (legal.isEmpty()) return;
                // ONE creature that can attack is still a decision: attack with it, or do not. This site
                // used to add it to combat without asking ("forced-only-legal"), so a seat with a single
                // creature attacked with it every turn into any board; 59 such attacks and scry/surveil
                // keeps were taken from the model across 24 recorded games (runs/lines/ctl2, blocks). The
                // shortcut came from reading n_options==1 as "forced", which is true of a pick-exactly-
                // one menu and false of a subset: a subset of one candidate has two outcomes.
                // D4 shadow: computed on pre-decision state, before the real
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
                    if (!makeAttackLegal(combat, "no-model")) auto("attackers(no-model)->all-in x" + legal.size());
                    return;
                }
                // Numbered subset pick, same PICKS router as discard: robust to duplicate creature names
                // and substring collisions, and decideMany prepends the full game snapshot — the model sees
                // opp life/board/untapped mana when deciding attacks, not just its own creature list.
                List<String> labels = new ArrayList<>();
                for (Card c : legal) labels.add(c.getName() + " (" + c.getNetPower() + "/" + c.getNetToughness() + ")");
                if (!proposesCombat()) p("[ROUTER] attackers: " + legal.size() + " can attack -> " + routeLabel(smart.getModel(), rnd != null));
                // Poison-aware lethal: a life-total-only hint actively misleads an infect/poison deck
                // (SF-1: deepseek's Infect matchup vs an opponent that never dropped below 20 life the
                // whole game — the real win condition was poison, invisible to the old hint). Compute
                // BOTH numbers and flag whether any legal attacker actually deals poison damage.
                int oppLife = defender.getLife();
                int oppPoison = 0;
                try { oppPoison = defender.getCounters(CounterEnumType.POISON); } catch (Exception ignore) { }
                boolean anyInfect = false;
                for (Card c : legal) { try { if (c.isInfectDamage(defender)) { anyInfect = true; break; } } catch (Exception ignore) { } }
                if (proposesCombat()) {   // no outlook: it names which attackers could be blocked by what
                    proposeAttackers(attacker, combat, legal, labels, shadowAtk,
                            buildAttackersQuestion(defender.getName(), oppLife, oppPoison, anyInfect, ""));
                    return;
                }
                // Shared question template (single source; the ai-teacher seat rebuilds byte-identically).
                String question = buildAttackersQuestion(defender.getName(), oppLife, oppPoison, anyInfect,
                        attackersCeiling(legal, defender, combat));
                // The engine re-asks until the declaration is legal (PhaseHandler: validateAttackers), so a set it
                // refuses (Silent Arbiter's one attacker, a creature that must attack) looped for a seat that kept
                // its answer (authored puzzles x_0, x_15, x_18 searched for hours). Asked once more with the reason;
                // refused again, the engine's own legal declaration is made, like any unanswered decision.
                List<Integer> picks = null;
                boolean refused = false;
                PuzzleArms.Line line = PuzzleArms.searchLine;
                for (int attempt = 0; attempt < 2; attempt++) {
                    int noted = line == null ? 0 : line.choices.size();
                    picks = decideMany(smart, null, question, labels, 0, legal.size());
                    if (picks == null) break;
                    for (int i : picks) combat.addAttacker(legal.get(i), attackTarget(legal.get(i), defender, combat));
                    if (CombatUtil.validateAttackers(combat)) break;
                    // on the record as asked, answered and overruled (its own row type: JSONL stays additive)
                    for (int i : picks) combat.removeFromCombat(legal.get(i));
                    evalDec("attackers-refused", legal.size(), picks.size(), tierOf(true), 0, null, labels);
                    p("[ROUTER] attackers: refused by the engine: " + namesOf(legal, picks));
                    if (line != null) line.dropChoicesAfter(noted);   // a refused set is no solver choice
                    question = question + " " + illegalAttackNote(attacker, combat);
                    picks = null;
                    refused = true;
                }
                if (picks == null) {
                    // Bridge dead or refused twice -> no attack, the fixed legal option, or the engine's legal set
                    // when no attack is not legal. Reading attackers out of the plan's prose, the prior-turn set and
                    // the all-in default were each the harness attacking for a seat that did not answer (acceptance
                    // `gaming`; 2026-10-16 review M2).
                    String why = refused ? "refused twice" : "bridge-down";
                    if (!makeAttackLegal(combat, why)) auto("attackers(" + why + ")->none");
                    return;
                }
                lastShadowChoice = shadowAtk; // re-attach: decideMany's clearLast() nulled it above
                evalDec("attackers", legal.size(), picks.size(), tierOf(true), 0, null, labels); // choice = # declared
                p("[ROUTER] attackers: " + (picks.isEmpty() ? "none" : namesOf(legal, picks)));
                formBands(combat);
            } catch (Exception e) {
                // A failure branch takes a deterministic LEGAL option, never the Forge AI for strategy
                // (#126): declaring no attackers is legal and cannot be the AI's line. The engine
                // resolves combat from an empty set.
                auto("attackers(err)->none");
            }
        }

        /** PROPOSE_NOTEBOOK: this seat's rules refusals in this game, oldest first, as {@link AnswerTool#notebookEntries}
         *  wrote them; one controller plays one game. */
        private final List<String> refusalNotebook = new ArrayList<>();

        /** Notes the rules refusals the seat was told in the ask just made ({@code lastAnswer}) for the later decisions'
         *  REFUSED EARLIER. */
        private void noteRefusals(AnswerTool.Kind k, String where) {
            if (AnswerTool.proposeNotebook() && lastAnswer != null)
                refusalNotebook.addAll(AnswerTool.notebookEntries(lastAnswer.toldRefusals(), k, "turn " + safeTurn() + ", " + where));
        }

        /** REFUSED EARLIER for the next proposal decision: empty off PROPOSE_NOTEBOOK and before any refusal. */
        private String refusedEarlier() { return AnswerTool.notebookSection(refusalNotebook); }

        /** PROPOSE_ACTIONS on a seat that answers by call: the attack and the blocks are proposed, not picked. The windows
         *  asked are the menu's (a seat with no creature that can attack, or none that can block, is not asked). */
        private boolean proposesCombat() { return AnswerTool.proposeActions() && observer != null && answerToolFor(smart); }

        /** The declare-attackers decision's text in place of the numbered creatures (PROPOSE_ACTIONS). */
        static final String PROPOSE_ATTACK_PROMPT = "You are not shown which creatures can attack. Propose your attack: each "
                + "creature that attacks, by handle, and what it attacks (the opponent, or a planeswalker or battle). The rules "
                + "engine checks the whole attack before anything happens.\n";
        /** The declare-blockers decision's text in place of the blocker list (PROPOSE_ACTIONS). */
        static final String PROPOSE_BLOCK_PROMPT = "You are not shown which creatures can block. Propose your blocks: each "
                + "blocker, by handle, and the attacker it blocks. Weigh your life total vs losing blockers. The rules engine "
                + "checks the blocks before anything happens.\n";

        /** PROPOSE_ACTIONS: the attack is proposed by handle and checked by the engine ({@link #attackProposalError}) in one
         *  ask of {@link AnswerTool#MAX_INVALID_PROPOSALS} tries. Lost, no creature attacks (or the engine's legal declaration
         *  when attacking with none breaks a requirement, as on the menu), never Forge's AI. */
        private void proposeAttackers(Player attacker, Combat combat, CardCollection legal, List<String> labels,
                                      Integer shadow, String question) {
            String prompt = snapshot() + refusedEarlier() + "\nPRIORITY: you\nDECISION: " + question + "\n" + PROPOSE_ATTACK_PROMPT;
            Map<Card, GameEntity> declared = new java.util.LinkedHashMap<>();
            AnswerTool.Spec spec = AnswerTool.Spec.attackProposal(labels).check(a -> {
                String err = attackProposalError(a.declared, attacker, combat, declared);
                if (err == null) {
                    a.options = new ArrayList<>();
                    for (Card c : declared.keySet()) { int i = legal.indexOf(c); if (i >= 0) a.options.add(i + 1); }
                }
                return err;
            });
            p("[ROUTER] attackers: proposed (" + legal.size() + " could attack) -> " + routeLabel(smart.getModel(), false));
            offerAnswer(smart, spec);
            String reply = ask(smart, prompt);
            noteAnswerVia(0);
            if (lastAnswer != null) PROPOSAL_ROW.set(proposalRecord(lastAnswer.proposals, AnswerTool.DECLARE_ATTACKERS));
            noteRefusals(AnswerTool.Kind.ATTACK_PROPOSAL, "declare attackers");
            AnswerTool.Answer ta = toolAnswer();
            if (ta == null) {
                String why = answerLost() || reply != null ? "unparsed" : "bridge-down";
                if (!makeAttackLegal(combat, why)) auto("attackers(" + why + ")->none");
                return;
            }
            for (Map.Entry<Card, GameEntity> e : declared.entrySet()) combat.addAttacker(e.getKey(), e.getValue());
            lastShadowChoice = shadow;
            lastPicks = ta.zeroBased();
            evalDec("attackers", legal.size(), declared.size(), tierOf(true), 0, null, labels);
            p("[ROUTER] attackers: " + AnswerTool.renderDeclared(ta.declared, true));
            formBands(combat);
        }

        /** PROPOSE_ACTIONS: the blocks are proposed by handle and checked by the engine ({@link #blockProposalError}) in one
         *  ask of {@link AnswerTool#MAX_INVALID_PROPOSALS} tries. Lost, the fixed fallback ({@link #fallbackBlocks}: none, or
         *  the smallest legal set when a requirement forbids none), never Forge's AI. */
        private void proposeBlockers(Player defender, Combat combat, String incoming, List<String> blkLabels, List<String> atkLabels,
                                     List<Card> blkList, List<Card> atkList, Integer shadow) {
            String prompt = snapshot() + refusedEarlier() + "\nDECISION: COMBAT — declare blockers. Incoming: " + incoming + ". " + PROPOSE_BLOCK_PROMPT;
            List<Card[]> declared = new ArrayList<>();
            AnswerTool.Spec spec = AnswerTool.Spec.blockProposal(blkLabels, atkLabels).check(a -> {
                String err = blockProposalError(a.declared, defender, combat, declared);
                if (err == null) {
                    a.blocks = new ArrayList<>();
                    for (Card[] pr : declared)
                        if (blkList.indexOf(pr[0]) >= 0 && atkList.indexOf(pr[1]) >= 0)
                            a.blocks.add(new int[] {blkList.indexOf(pr[0]) + 1, atkList.indexOf(pr[1]) + 1});
                }
                return err;
            });
            p("[ROUTER] blockers: proposed (" + atkList.size() + " atk / " + blkList.size() + " could block) -> " + routeLabel(smart.getModel(), false));
            offerAnswer(smart, spec);
            String reply = ask(smart, prompt);
            noteAnswerVia(0);
            if (lastAnswer != null) PROPOSAL_ROW.set(proposalRecord(lastAnswer.proposals, AnswerTool.DECLARE_BLOCKERS));
            noteRefusals(AnswerTool.Kind.BLOCK_PROPOSAL, "declare blockers");
            AnswerTool.Answer ta = toolAnswer();
            if (ta == null) {
                auto("blockers(" + (answerLost() || reply != null ? "unparsed" : "bridge-down") + ")->" + fallbackBlocks(defender, combat));
                return;
            }
            for (Card[] pr : declared) combat.addBlocker(pr[1], pr[0]);
            lastShadowChoice = shadow;
            evalDec("blockers", blkLabels.size(), declared.size(), tierOf(true), 0, null, blkLabels);
            p("[ROUTER] blockers: " + (declared.isEmpty() ? "NONE" : AnswerTool.renderDeclared(ta.declared, false)));
        }

        /** The names of {@code cards} at {@code picks}, comma-separated, for the router log. */
        static String namesOf(List<Card> cards, List<Integer> picks) {
            StringBuilder sb = new StringBuilder();
            for (int i : picks) sb.append(sb.length() > 0 ? ", " : "").append(cards.get(i).getName());
            return sb.toString();
        }

        /** Why the engine refuses a declared attack, for the seat's second ask: the global limit when there is one
         *  (Silent Arbiter, Crawlspace), else the restrictions and requirements in general. */
        static String illegalAttackNote(Player attacker, Combat combat) {
            Integer max = null;
            try { max = forge.game.combat.GlobalAttackRestrictions.getGlobalRestrictions(attacker, combat.getDefenders()).getMax(); }
            catch (Exception ignore) { }
            return "That set is not a legal attack: " + (max != null ? "at most " + max + " creature"
                    + (max == 1 ? "" : "s") + " can attack this combat" : "it breaks an attack restriction or requirement"
                    + " (a limit, a creature that must attack, a cost)") + ". Choose again.";
        }

        /** A declaration the engine would refuse is replaced by the engine's legal one ({@code AttackConstraints}: the
         *  set with the fewest requirements broken, a constraint solve, not a strategy), so the engine never re-asks
         *  a seat that cannot answer. True when it replaced the declaration (and recorded that as the one taken). */
        private boolean makeAttackLegal(Combat combat, String why) {
            if (CombatUtil.validateAttackers(combat)) return false;
            for (Card c : new ArrayList<>(combat.getAttackers())) combat.removeFromCombat(c);
            for (Map.Entry<Card, GameEntity> e : combat.getAttackConstraints().getLegalAttackers().getLeft().entrySet())
                combat.addAttacker(e.getKey(), e.getValue());
            auto("attackers(" + why + ", refused)->engine legal x" + combat.getAttackers().size());
            return true;
        }

        /** Bands (CR 702.22): an attacker with banding, or bands with other, may attack together with others
         *  attacking the same player or planeswalker, and the band is blocked as one. The human GUI offers it
         *  when declaring attackers (InputAttack's band mode); the seat declared every attacker alone, so banding
         *  was never used. Each banding attacker asks which of the others join it; a set the engine calls no band
         *  (AttackingBand.isValidBand: one non-banding creature at most) is asked once more, then left alone. */
        private void formBands(Combat combat) {
            java.util.Set<Card> banded = new java.util.HashSet<>();
            for (Card leader : new ArrayList<>(combat.getAttackers())) {
                if (banded.contains(leader)) continue;
                if (!leader.hasKeyword(Keyword.BANDING) && !leader.hasKeyword(Keyword.BANDSWITH)) continue;
                GameEntity def = combat.getDefenderByAttacker(leader);
                List<Card> others = new ArrayList<>();
                for (Card c : combat.getAttackers())
                    if (c != leader && !banded.contains(c) && combat.getDefenderByAttacker(c) == def) others.add(c);
                if (others.isEmpty()) continue;
                List<String> labels = new ArrayList<>();
                for (Card c : others) labels.add(labelOf(c));
                String q = "BANDS: " + labelOf(leader) + " has " + (leader.hasKeyword(Keyword.BANDING) ? "banding" : "bands with other")
                        + ". Choose the attackers that join its band against " + labelOf(def) + " (a band is blocked as one, and"
                        + " you assign the damage dealt to it); PICKS: NONE keeps it attacking alone.";
                for (int attempt = 0; attempt < 2; attempt++) {
                    List<Integer> picks = decideMany(smart, "attack-bands", q + (attempt == 0 ? ""
                            : " That set is not a band: all but one of its creatures need banding."), labels, 0, others.size());
                    if (picks == null || picks.isEmpty()) break;
                    CardCollection band = new CardCollection(leader);
                    for (int i : picks) band.add(others.get(i));
                    if (!forge.game.combat.AttackingBand.isValidBand(band, false)) {
                        if (attempt == 1) auto("attack-bands(not a band)->alone");
                        continue;
                    }
                    forge.game.combat.AttackingBand ab = combat.getBandOfAttacker(leader);
                    for (Card c : band) if (c != leader) combat.addAttacker(c, def, ab);
                    banded.addAll(band);
                    p("[ROUTER] attack-bands: " + band);
                    break;
                }
            }
        }

        // ---- Mechanics Forge's AI decided until 2026-10-17 (ledger: "not in eval decks"). The owner: "cover all the
        // mechanics, every play surface"; each is the seat's now, with a fixed legal option when no answer comes.

        /** Which of {@code cards} the seat picks for {@code type} (any number, none included). */
        private List<Card> pickCards(String type, String question, List<Card> cards) {
            clearLast();
            List<Card> out = new ArrayList<>();
            if (cards == null || cards.isEmpty()) return out;
            if (!smartOk()) { auto(type + "(no-model)->none"); return out; }
            List<String> labels = new ArrayList<>();
            for (Card c : cards) labels.add(cardLabel(c));
            List<Integer> picks = decideMany(smart, type, question, labels, 0, cards.size());
            if (picks == null) { auto(type + "(unparsed)->none"); return out; }
            for (int i : picks) out.add(cards.get(i));
            p("[ROUTER] " + type + ": " + (out.isEmpty() ? "(none)" : names(out, false, 10)));
            return out;
        }

        /** Exert (CR 701.43): which attackers are exerted as they attack. */
        @Override
        public List<Card> exertAttackers(List<Card> attackers) {
            return pickCards("exert", "EXERT: choose the attackers to exert as they attack. An exerted creature gets what its"
                    + " exert ability says and won't untap during your next untap step.", attackers);
        }

        /** Enlist (CR 702.154): which attackers enlist (each taps a non-attacking creature you control and adds its power). */
        @Override
        public List<Card> enlistAttackers(List<Card> attackers) {
            return pickCards("enlist", "ENLIST: choose the attackers that enlist. Each taps a non-attacking creature you control"
                    + " without summoning sickness and gets +X/+0 until end of turn, X that creature's power.", attackers);
        }

        /** Splice onto Arcane (CR 702.47): which cards in hand are spliced onto the spell. Only splices the whole cast can
         *  still pay are offered: an unpayable one fails the cast. Each is checked alone for the menu, and the set the
         *  seat picks is checked together: two payable splices can cost more than the pool holds (#392: Glacial Ray and
         *  Desperate Ritual onto Lava Spike, {2}{R}{R}{R} from {R:3}). Such a set, or one whose cast already failed in this
         *  window, is asked once more, then the spell is cast with no splice (splicing is optional, CR 702.47a). A legality
         *  fix, so under every profile. */
        @Override
        public List<Card> chooseCardsForSplice(SpellAbility sa, List<Card> cards) {
            List<Card> payable = new ArrayList<>();
            for (Card c : cards) if (payableSpliced(spliced(sa, List.of(c)))) payable.add(c);
            if (payable.isEmpty()) { clearLast(); auto("splice(none payable)->none"); return payable; }
            String q = "SPLICE" + srcAsk(sa) + ": choose the cards in your hand to splice onto it. Each adds its"
                    + " effect, you pay its splice cost too, and it stays in your hand.";
            String again = "";
            for (int attempt = 0; attempt < 2; attempt++) {
                List<Card> picked = pickCards("splice", q + again, payable);
                if (picked.isEmpty()) return picked;
                // A set whose cast already failed to pay in this window is not taken again: the plain cast stays offered
                // (#392), so a set the check passes and the payment fails must not be retried without end.
                boolean failed = failedInWindow.contains(spliceKey(failedPlayKey(sa), picked));
                SpellAbility with = spliced(sa, picked);
                if (!failed && (picked.size() < 2 || payableSpliced(with))) return picked;
                String set = names(picked, false, 10), total = with == null ? "?" : String.valueOf(with.getPayCosts().getTotalMana());
                // No cost in the seat's text: the printed total is before reductions, the check is after them.
                again = " You chose " + set + ": " + (failed ? "the spell with them failed when you cast it in this window."
                        : "the spell with all of them costs more than you can pay, so it would not be cast.")
                        + " Choose a set you can pay for together, or none. Another set you cannot pay for casts it with no splice.";
                p("[ROUTER] splice: " + set + (failed ? " failed to pay in this window" : " cannot be paid together (" + total + ")"));
            }
            auto("splice(unpayable set)->none");
            return new ArrayList<>();
        }

        /** A copy of {@code sa} with {@code splices} spliced onto it, or null when the engine cannot build one. */
        private SpellAbility spliced(SpellAbility sa, List<Card> splices) {
            try {
                SpellAbility with = sa.copy();
                for (Card c : splices) forge.game.ability.AbilityUtils.addSpliceEffect(with, c);
                with.setActivatingPlayer(getPlayer());
                return with;
            } catch (Exception e) { return null; }
        }

        private boolean payableSpliced(SpellAbility with) {
            try { return with != null && legallyPayable(with, getPlayer(), false); } catch (Exception e) { return false; }
        }

        /** A coin flip the seat calls (heads or tails), or, with Krark's Thumb, the result it keeps. */
        @Override
        public boolean chooseFlipResult(SpellAbility sa, Player flipper, boolean call) {
            clearLast();
            List<String> labels = call ? Lists.newArrayList("Heads", "Tails") : Lists.newArrayList("Win the flip", "Lose the flip");
            if (!smartOk() && !fastOk()) { auto("flip(no-model)->" + labels.get(0)); return true; }
            String q = call ? "COIN FLIP" + srcAsk(sa) + ": call it." : "COIN FLIP" + srcAsk(sa) + ": choose the result you keep.";
            int c = askOne("flip", q, labels, null, 0);
            p("[ROUTER] flip: " + labels.get(c));
            return c == 0;
        }

        /** Shield counters an effect gives, divided among what it affects (CR 122.1c). */
        @Override
        public Map<GameEntity, Integer> divideShield(Card effectSource, Map<GameEntity, Integer> affected, int shieldAmount) {
            clearLast();
            List<GameEntity> ents = new ArrayList<>(affected.keySet());
            Map<GameEntity, Integer> out = new java.util.HashMap<>();
            if (ents.isEmpty() || shieldAmount <= 0) return out;
            List<Integer> alloc = null;
            if (ents.size() > 1 && (smartOk() || fastOk())) {
                List<String> labels = new ArrayList<>();
                for (GameEntity e : ents) labels.add(labelOf(e));
                alloc = decideAllocation(bridgeFor("shield"), "SHIELD: divide " + shieldAmount + " shield counters from "
                        + labelOf(effectSource) + " among these.", labels, shieldAmount);
                if (alloc != null && (alloc.size() != ents.size() || alloc.stream().mapToInt(Integer::intValue).sum() != shieldAmount)) alloc = null;
                if (alloc != null) evalDec("shield", ents.size(), -1, tierOf(true), 0, null, labels);
            }
            if (alloc == null) { alloc = evenSplit(shieldAmount, ents.size()); auto("shield(" + (ents.size() == 1 ? "forced" : "default") + ")->even split"); }
            for (int i = 0; i < ents.size(); i++) if (alloc.get(i) > 0) out.put(ents.get(i), alloc.get(i));
            return out;
        }

        /** Set when a roll's reroll or modify cost was not paid: the engine asks again while the card that offers it
         *  remains, and it remains until paid, so asking again would loop. Cleared by the next roll's first ask. */
        private boolean rollPaymentFailed = false;

        /** A die to ignore ("roll two dice and ignore one"). */
        @Override
        public Integer chooseRollToIgnore(List<Integer> rolls) {
            clearLast();
            if (rolls == null || rolls.isEmpty()) return null;
            if (rolls.size() == 1 || new java.util.HashSet<>(rolls).size() == 1) { auto("roll-ignore(forced)"); return rolls.get(0); }
            List<String> labels = new ArrayList<>();
            for (Integer r : rolls) labels.add(String.valueOf(r));
            return rolls.get(askOne("roll-ignore", "DICE: choose the roll to ignore.", labels, null, 0));
        }

        /** Which dice to reroll (Monitor Monitor: pay {1} to reroll one or more). */
        @Override
        public List<Integer> chooseDiceToReroll(List<Integer> rolls) {
            clearLast();
            List<Integer> out = new ArrayList<>();
            if (rolls == null || rolls.isEmpty()) return out;
            if (rollPaymentFailed) { rollPaymentFailed = false; auto("roll-reroll(cost not paid)->none"); return out; }
            if (!smartOk()) { auto("roll-reroll(no-model)->none"); return out; }
            List<String> labels = new ArrayList<>();
            for (Integer r : rolls) labels.add(String.valueOf(r));
            List<Integer> picks = decideMany(smart, "roll-reroll", "DICE: choose the dice to reroll (you pay the reroll cost); PICKS: NONE keeps them.",
                    labels, 0, rolls.size());
            if (picks == null) { auto("roll-reroll(unparsed)->none"); return out; }
            for (int i : picks) out.add(rolls.get(i));
            return out;
        }

        /** Which roll to raise or lower by 1 (Xenosquirrels, Night Shift of the Living Dead), or none. */
        @Override
        public Integer chooseRollToModify(List<Integer> rolls) {
            clearLast();
            if (rolls == null || rolls.isEmpty()) return null;
            if (rollPaymentFailed) { rollPaymentFailed = false; auto("roll-modify(cost not paid)->none"); return null; }
            List<String> labels = new ArrayList<>();
            for (Integer r : rolls) labels.add(String.valueOf(r));
            int c = askOne("roll-modify", "DICE: choose a roll to raise or lower by 1 (you pay the cost), or none.", labels,
                    "None (keep the rolls)", -1);
            return c < 0 || c >= rolls.size() ? null : rolls.get(c);
        }

        /** Which roll to swap with a creature's power or toughness (Dicey Sapper-style effects), or none. */
        @Override
        public forge.game.ability.effects.RollDiceEffect.DieRollResult chooseRollToSwap(List<forge.game.ability.effects.RollDiceEffect.DieRollResult> rolls) {
            clearLast();
            if (rolls == null || rolls.isEmpty()) return null;
            List<String> labels = new ArrayList<>();
            for (forge.game.ability.effects.RollDiceEffect.DieRollResult r : rolls) labels.add(String.valueOf(r.getModifiedValue()));
            int c = askOne("roll-swap", "DICE: choose a roll to exchange with power or toughness, or none.", labels, "None (no exchange)", -1);
            return c < 0 || c >= rolls.size() ? null : rolls.get(c);
        }

        /** Whether the swapped roll becomes power or toughness, or neither. */
        @Override
        public String chooseRollSwapValue(List<String> swapChoices, Integer currentResult, int power, int toughness) {
            clearLast();
            if (swapChoices == null || swapChoices.isEmpty()) return null;
            int c = askOne("roll-swap", "DICE: exchange the roll " + currentResult + " with power " + power + " or toughness "
                    + toughness + "?", new ArrayList<>(swapChoices), "Neither", -1);
            return c < 0 || c >= swapChoices.size() ? null : swapChoices.get(c);
        }

        /** The cost of a reroll or a modification the seat chose (Forge's AI never paid one). */
        @Override
        public boolean payCostDuringRoll(Cost cost, SpellAbility sa) {
            clearLast();
            try {
                if (cost == null || !legallyPayable(cost, sa, getPlayer(), true)) {
                    rollPaymentFailed = true;
                    auto("roll-cost(cannot pay)->not paid");
                    return false;
                }
                boolean paid = new CostPayment(cost, sa).payComputerCosts(new LlmCostDecision(getPlayer(), sa, true));
                if (!paid) rollPaymentFailed = true;
                p("[ROUTER] roll-cost: paid " + fit(named(cost.toSimpleString(), sa.getHostCard())) + " -> " + paid);
                return paid;
            } catch (Exception e) { rollPaymentFailed = true; auto("roll-cost(err)->not paid"); return false; }
        }

        /** A planar die roll to ignore (Planechase). */
        @Override
        public forge.game.PlanarDice choosePDRollToIgnore(List<forge.game.PlanarDice> rolls) {
            clearLast();
            if (rolls == null || rolls.isEmpty()) return null;
            if (new java.util.HashSet<>(rolls).size() == 1) { auto("roll-ignore(forced)"); return rolls.get(0); }
            List<String> labels = new ArrayList<>();
            for (forge.game.PlanarDice r : rolls) labels.add(String.valueOf(r));
            return rolls.get(askOne("roll-ignore", "PLANAR DIE: choose the roll to ignore.", labels, null, 0));
        }

        /** Which sticker goes on {@code target} (Unfinity), or none when optional. */
        @Override
        public forge.game.card.sticker.Sticker chooseSticker(List<forge.game.card.sticker.Sticker> options, Card target, SpellAbility sa, boolean isOptional) {
            clearLast();
            if (options == null || options.isEmpty()) return null;
            List<String> labels = new ArrayList<>();
            for (forge.game.card.sticker.Sticker st : options)
                labels.add((st.getTickets() == 0 ? "" : st.getTickets() + " tickets - ") + st.getDescription());
            int c = askOne("sticker", "STICKER" + srcAsk(sa) + ": choose the sticker to put on " + labelOf(target) + ".", labels,
                    isOptional ? "None" : null, isOptional ? -1 : 0);
            return c < 0 || c >= options.size() ? null : options.get(c);
        }

        /** Where a name sticker's word goes in the card's name (CR 123.6a). */
        @Override
        public int chooseStickerNamePosition(forge.game.card.sticker.Sticker sticker, Card target) {
            clearLast();
            if (target.getName() == null || target.getName().isBlank() || target.stickerWouldFillBlank()) return 0;
            int words = target.getName().split(" ").length;
            List<String> labels = new ArrayList<>();
            for (int at = 0; at <= words; at++) labels.add(Card.addStickerWord(target.getName(), sticker.getWord(), at));
            return Math.max(0, askOne("sticker", "STICKER: where does \"" + sticker.getWord() + "\" go in the name?", labels, null, 0));
        }

        /** Which card keeps its stickers when too many would remain (CR 123.9). */
        @Override
        public Card chooseCardToKeepStickers(CardCollectionView options) {
            clearLast();
            if (options == null || options.isEmpty()) return null;
            List<Card> cards = new ArrayList<>(options);
            List<String> labels = new ArrayList<>();
            for (Card c : cards) labels.add(cardLabel(c));
            return cards.get(askOne("sticker", "STICKERS: choose the card that keeps its stickers.", labels, null, 0));
        }

        /** Which contraptions are cranked this turn (Unstable). */
        @Override
        public List<Card> chooseContraptionsToCrank(List<Card> contraptions) {
            return pickCards("crank", "CRANK: choose the contraptions to crank (each does what its cranked ability says).", contraptions);
        }

        /** Which sprocket a contraption is assembled on. */
        @Override
        public int chooseSprocket(Card assignee, List<Integer> sprockets) {
            clearLast();
            if (sprockets == null || sprockets.isEmpty()) return 0;
            int next = (getPlayer().getCrankCounter() % 3) + 1;
            List<String> labels = new ArrayList<>();
            for (Integer sp : sprockets) labels.add(sp + (sp == next ? " (cranked next turn)" : ""));
            return sprockets.get(askOne("sprocket", "SPROCKET: choose the sprocket for " + labelOf(assignee) + ".", labels, null, 0));
        }

        /** An Attraction's or an effect's sector (Unfinity). */
        @Override
        public String chooseSector(Card assignee, String ai, List<String> sectors) {
            clearLast();
            if (sectors == null || sectors.isEmpty()) return null;
            String q = assignee != null ? "SECTOR: choose the sector for " + labelOf(assignee) + "." : "SECTOR: choose a sector for the effect.";
            return sectors.get(askOne("sector", q, new ArrayList<>(sectors), null, 0));
        }

        /** Which of several opening hands is kept (a variant that deals more than one). */
        @Override
        public forge.game.zone.PlayerZone chooseStartingHand(List<forge.game.zone.PlayerZone> zones) {
            clearLast();
            if (zones == null || zones.isEmpty()) return null;
            List<String> labels = new ArrayList<>();
            for (forge.game.zone.PlayerZone z : zones) labels.add(names(new ArrayList<>(z.getCards()), false, 10));
            return zones.get(askOne("starting-hand", "Choose the opening hand you keep.", labels, null, 0));
        }

        /** Assist (CR 702.132): which player is asked to help pay, or none. */
        @Override
        public Player choosePlayerToAssistPayment(FCollectionView<Player> optionList, SpellAbility sa, String title, int max) {
            // Pricing asks too (the menu, the hand tags): an ask there builds a snapshot that prices again
            // (StackOverflowError, keyword sweep). Only the real payment asks; pricing counts no assist.
            if (PRICING_FOR_SNAPSHOT || !payingForReal) return null;
            clearLast();
            if (optionList == null || optionList.isEmpty()) return null;
            List<Player> ps = new ArrayList<>(optionList);
            List<String> labels = new ArrayList<>();
            for (Player pl : ps) labels.add(pl.getName());
            int c = askOne("assist", "ASSIST" + srcAsk(sa) + ": ask a player to help pay up to {" + max + "}, or none.", labels, "None", -1);
            return c < 0 || c >= ps.size() ? null : ps.get(c);
        }

        /** How much of another player's assist spell the seat pays, from what it can pay. */
        @Override
        public boolean helpPayForAssistSpell(forge.game.mana.ManaCostBeingPaid cost, SpellAbility sa, int max, int requested) {
            clearLast();
            int can = 0;
            for (int k = Math.min(max, 20); k > 0; k--) {
                forge.game.mana.ManaCostBeingPaid probe = new forge.game.mana.ManaCostBeingPaid(forge.card.mana.ManaCost.get(k));
                if (seatCanPay(probe, sa, getPlayer(), false)) { can = k; break; }
            }
            if (can == 0) { auto("assist-pay(cannot pay)->0"); return true; }
            List<String> labels = new ArrayList<>();
            for (int k = 0; k <= can; k++) labels.add(String.valueOf(k));
            int pay = Math.max(0, askOne("assist-pay", "ASSIST" + srcAsk(sa) + ": " + sa.getActivatingPlayer().getName() + " asks you to help pay"
                    + " (they asked for {" + requested + "}). How much generic mana do you pay?", labels, null, 0));
            if (pay == 0) return true;
            forge.game.mana.ManaCostBeingPaid assist = new forge.game.mana.ManaCostBeingPaid(forge.card.mana.ManaCost.get(pay));
            if (ComputerUtilMana.payManaCost(assist, sa, getPlayer(), false)) cost.decreaseGenericMana(pay);
            return true;
        }

        /** A vote (Council's Judgment): the seat's, not ComputerUtil.vote. The ledger had it as "not in
         *  the eval decks"; miracles plays Council's Judgment. */
        @Override
        public Object vote(SpellAbility sa, String prompt, List<Object> options,
                           com.google.common.collect.ListMultimap<Object, Player> votes, Player forPlayer, boolean optional) {
            clearLast();
            if (options == null || options.isEmpty()) return null;
            if (options.size() == 1 && !optional) { auto("vote(forced)"); return options.get(0); }
            if (!smartOk() && !fastOk()) { auto("vote(no-model)->first of " + options.size() + " legal"); return options.get(0); }
            List<String> labels = new ArrayList<>();
            for (Object o : options) labels.add(o instanceof GameEntity ? labelOf((GameEntity) o) : String.valueOf(o));
            int c = decideOne(smartOk() ? smart : fast, "vote", "VOTE" + srcAsk(sa) + ": " + flat(prompt), labels, optional ? "Abstain" : null);
            if (c >= 0) return options.get(c);
            if (optional && lastDeclineLogged) return null;
            auto("vote(unparsed)->first of " + options.size() + " legal");
            return options.get(0);
        }

        /** The card the seat just agreed to play when the engine asked "Do you want to play X?" (a Play effect). */
        private Card playConfirmed;

        /** A spell cast by an effect (cascade, suspend, Dreadhorde Arcanist, "you may cast it"): whether
         *  to cast it when it is optional, then X, modes and targets, all the seat's. The inherited version
         *  asked Forge AI whether to cast it and let doTrigger-style AI logic target it (Rift Bolt off
         *  suspend got an AI target). */
        @Override
        public boolean playSaFromPlayEffect(SpellAbility tgtSA) {
            clearLast();
            if (!(tgtSA instanceof forge.game.spellability.Spell)) return true;
            boolean optional = !tgtSA.getPayCosts().isMandatory();
            // The engine has just asked "Do you want to play X?" for this card and the seat said yes: asking again
            // offered the decline twice (suspend, rules pilot RP_209).
            boolean confirmed = playConfirmed != null && playConfirmed == tgtSA.getHostCard();
            playConfirmed = null;
            if (!optional) {
                // A cast the effect makes "if able" can still cost mana (Sphere of Resistance on a free spell). No player
                // is made to activate a mana ability (CR 605.3a): left unpaid, the spell is not cast (rules pilot RP_197).
                String tax = null;
                try {
                    forge.game.mana.ManaCostBeingPaid due = ComputerUtilMana.calculateManaCost(tgtSA.getPayCosts(), tgtSA,
                            getPlayer(), true, 0, false);
                    if (due != null && !due.isPaid()) tax = due.toString();
                } catch (Exception e) { tax = null; }
                if (tax != null) {
                    if (!smartOk() && !fastOk()) { auto("play-from-effect(no-model)->decline"); return false; }
                    List<String> labels = Lists.newArrayList("Cast it, paying " + tax, "Do not pay " + tax + ": it is not cast");
                    int c = decideOne(smartOk() ? smart : fast, "play-from-effect", "An effect casts this now if you pay "
                            + tax + ": " + labelOf(tgtSA) + ". Pay it?", labels, null);
                    if (c != 0) { if (c < 0) auto("play-from-effect(unparsed)->decline"); return false; }
                }
            }
            if (optional && !confirmed) {
                if (!smartOk() && !fastOk()) { auto("play-from-effect(no-model)->decline"); return false; }
                List<String> labels = Lists.newArrayList("Cast it", "Do not cast it");
                // The label already opens with its verb: "An effect lets you cast CAST Rift Bolt" (runs/loop/g02).
                int c = decideOne(smartOk() ? smart : fast, "play-from-effect", observer != null
                        ? "An effect lets you play this now: " + labelOf(tgtSA) + ". Cast it?"
                        : "An effect lets you cast " + verbOf(tgtSA) + " " + labelOf(tgtSA) + " now. Cast it?", labels, null);
                if (c != 0) { if (c < 0) auto("play-from-effect(unparsed)->decline"); return false; }
            }
            if (tgtSA.getActivatingPlayer() == null) tgtSA.setActivatingPlayer(getPlayer());
            forge.game.zone.Zone from = tgtSA.getHostCard() == null ? null : tgtSA.getHostCard().getZone();
            tgtSA = withOptionalCosts(tgtSA);   // a spell cast by an effect may still be kicked (CR 601.2b)
            announceX(tgtSA);
            // From here the seat has chosen to cast it: a cast that then does not happen is counted (playsNotMade).
            if (tgtSA.getApi() == ApiType.Charm && !forge.game.ability.effects.CharmEffect.makeChoices(tgtSA)) {
                playsNotMade++;
                return false;
            }
            ensureTargetsSet(tgtSA);
            // playStack tests the cost with Forge AI's check (ComputerUtilCost.canPayCost), which obeys the AI-only payment
            // hint: hidden, as for every check a harness seat asks (pr-review of #277: an effect-cast Gitaxian Probe).
            final SpellAbility cast = tgtSA;
            boolean played = seatCheck(cast, () -> forge.ai.ComputerUtil.playStack(cast, getPlayer(), getGame()));
            if (played) playSeen(tgtSA, from); else playsNotMade++;
            return played;
        }

        /** Where one declared attacker attacks: the player, or a planeswalker or battle it can attack.
         *  Every attacker used to go at the first defending player, so Jace, the Mind Sculptor, Minsc &
         *  Boo or Karn could never be attacked. Asked only when there is more than one legal defender. */
        private GameEntity attackTarget(Card attacker, Player player, Combat combat) {
            List<GameEntity> defs = new ArrayList<>();
            try { for (GameEntity d : combat.getDefenders()) if (CombatUtil.canAttack(attacker, d)) defs.add(d); }
            catch (Exception e) { return player; }
            if (defs.size() <= 1) return defs.isEmpty() ? player : defs.get(0);
            List<String> labels = new ArrayList<>();
            for (GameEntity d : defs) labels.add(labelOf(d));
            int c = decideOne(smart, "attack-target", "Where does " + labelOf(attacker) + " attack?", labels, null);
            if (c >= 0) return defs.get(c);
            auto("attack-target(unparsed)->player");
            return player;
        }

        /** The engine's reason the last block set was not legal, put into the re-ask; set only during that re-ask. */
        private String blockCorrection = null;

        /** The engine's objection to these blocks (requirements such as "all creatures able to block it do so",
         *  restrictions such as menace), or null. The engine checks them only for its own GUI (InputBlock calls
         *  CombatUtil.validateBlocks), so every other controller's blocks stood whatever they were (rules pilot
         *  RP_007). The pairs (blocker, attacker) are added, checked and removed again. validateBlocks never counts the
         *  blockers against a global cap (Silent Arbiter: "No more than one creature can block each combat"; Mirri,
         *  Weatherlight Duelist's attack trigger: "each opponent can't block with more than one creature"), which
         *  the engine enforces only in CombatUtil.canBlock against the combat so far, so the cap is checked here.
         *  <p>validateBlocks' requirement test also ignores the cap: under Silent Arbiter and Prized Unicorn it refused
         *  every set, naming a creature left unblocking whose requirement the cap forbids obeying. At the cap that
         *  objection is dropped only when (1) every creature in the set blocks an attacker it has a requirement toward,
         *  so no swap obeys more requirements (CR 509.1c, as near as one requirement per blocker can tell), and (2) the
         *  restriction half of validateBlocks holds ({@link #restrictionError}), which its first objection hid. Otherwise
         *  the objection stands (round-3 review of #314: Elves on Lions while Hill Giant must block the Bears; Mogg
         *  Flunkies blocking alone). Counting requirements exactly would mean modelling every lure and must-block form
         *  the engine checks one by one; it has no counter to reuse. */
        static String blocksError(Combat combat, Player defender, List<Card[]> pairs) {
            for (Card[] pr : pairs) combat.addBlocker(pr[1], pr[0]);
            try {
                CardCollection blocking = CardLists.filterControlledBy(combat.getAllBlockers(), defender);
                int cap = forge.game.staticability.StaticAbilityBlockRestrict.blockRestrictNum(defender);
                if (blocking.size() > cap) return blockCapWords(cap);
                String err = CombatUtil.validateBlocks(combat, defender);
                if (err == null || blocking.size() < cap || !namesANonBlocker(err, defender, blocking)) return err;
                for (Card b : blocking) {
                    boolean obeys = false;
                    for (Card a : combat.getAttackersBlockedBy(b)) obeys |= requiredToward(b, a, combat);
                    if (!obeys) return err;
                }
                return restrictionError(combat, blocking);
            }
            catch (Exception e) { return null; }
            finally { for (Card[] pr : pairs) combat.removeBlockAssignment(pr[1], pr[0]); }
        }

        private static boolean namesANonBlocker(String err, Player defender, CardCollection blocking) {
            for (Card c : defender.getCreaturesInPlay()) if (!blocking.contains(c) && err.startsWith(c + " ")) return true;
            return false;
        }

        /** Whether the blocker has a requirement to block this attacker: it blocks each combat if able, it must block
         *  this attacker (provoke), or the attacker's lure covers it. The last two are the engine's own test
         *  (CombatUtil.mustBlockAnAttacker) on a scratch combat holding only this attacker. */
        private static boolean requiredToward(Card blocker, Card attacker, Combat combat) {
            if (forge.game.staticability.StaticAbilityMustBlock.blocksEachCombatIfAble(blocker)) return true;
            Combat scratch = new Combat(combat.getAttackingPlayer());
            scratch.addAttacker(attacker, combat.getDefenderByAttacker(attacker));
            return CombatUtil.mustBlockAnAttacker(blocker, scratch, null);
        }

        /** The restriction half of CombatUtil.validateBlocks (CombatUtil.java:701-728 at the pinned engine), which runs
         *  only after its requirement half finds nothing: the "can't block alone" family, then each blocked attacker's
         *  allowed number of blockers (menace). */
        static String restrictionError(Combat combat, CardCollection blockers) {
            for (Card b : blockers) {
                boolean alone = b.hasKeyword("CARDNAME can't attack or block alone.") || b.hasKeyword("CARDNAME can't block alone.");
                if (blockers.size() < 2 && alone) return b + " can't block alone.";
                if (blockers.size() < 3 && b.hasKeyword("CARDNAME can't block unless at least two other creatures block."))
                    return b + " can't block unless at least two other creatures block.";
                if (b.hasKeyword("CARDNAME can't block unless a creature with greater power also blocks.")) {
                    boolean found = false;
                    for (Card o : blockers) found |= o.getNetPower() > b.getNetPower();
                    if (!found) return b + " can't block unless a creature with greater power also blocks.";
                }
            }
            for (Card a : combat.getAttackers()) {
                int n = combat.getBlockers(a).size();
                if (n > 0 && !CombatUtil.canAttackerBeBlockedWithAmount(a, n, combat))
                    return a + " cannot be blocked with " + n + " creatures you've assigned";
            }
            return null;
        }

        /** Every block set the engine accepts (CombatUtil.validateBlocks) where each blocker blocks one attacker or none,
         *  or null when there are more than {@link #MAX_BLOCK_SETS} combinations to try. An arm that cannot draw a legal
         *  set under a block requirement takes one of these (rules pilot RP_185: ten creatures that must block a Lure). */
        static List<List<Card[]>> legalBlockSets(Combat combat, Player defender, List<Card> blockers, List<Card> attackers) {
            List<List<Card>> options = new ArrayList<>();
            long combos = 1;
            for (Card b : blockers) {
                List<Card> o = new ArrayList<>();
                o.add(null);
                for (Card a : attackers) if (CombatUtil.canBlock(a, b, combat)) o.add(a);
                options.add(o);
                combos *= o.size();
                if (combos > MAX_BLOCK_SETS) return null;
            }
            List<List<Card[]>> out = new ArrayList<>();
            int[] idx = new int[blockers.size()];
            for (long n = 0; n < combos; n++) {
                List<Card[]> pairs = new ArrayList<>();
                for (int i = 0; i < blockers.size(); i++) {
                    Card a = options.get(i).get(idx[i]);
                    if (a != null) pairs.add(new Card[] {blockers.get(i), a});
                }
                if (blocksError(combat, defender, pairs) == null) out.add(pairs);
                for (int i = 0; i < idx.length; i++) { if (++idx[i] < options.get(i).size()) break; idx[i] = 0; }
            }
            return out;
        }
        static final int MAX_BLOCK_SETS = 4096;

        /** Blocks the seat declared that the engine objects to are removed and the seat is asked once more with the
         *  reason; a second illegal set is logged and replaced by {@link #fallbackBlocks}. */
        private void illegalBlocks(Player defender, Combat combat, String err) {
            for (Card b : CardLists.filterControlledBy(combat.getAllBlockers(), defender)) combat.undoBlockingAssignment(b);
            if (blockCorrection == null) {
                blockCorrection = err;
                try { declareBlockers(defender, combat); } finally { blockCorrection = null; }
                return;
            }
            p("[BLOCK-INVALID] the second block set was not legal either (" + err + ")");
            // The set taken is the harness's decision, so it has its own row (the model's answer has the one before).
            auto("blockers(illegal-twice)->" + fallbackBlocks(defender, combat));
        }

        /** The defender's creatures that can block at least one attacker, as a fresh list. */
        static List<Card> ableBlockers(Player defender, Combat combat) {
            List<Card> able = new ArrayList<>();
            for (Card c : defender.getCreaturesInPlay())
                for (Card atk : combat.getAttackers()) if (CombatUtil.canBlock(atk, c, combat)) { able.add(c); break; }
            return able;
        }

        /** A block set {@link #blocksError} accepts (the engine's check and the global block cap), chosen without
         *  randomness, or null when none was found (a caller declares none; an arm keeps its last draw). Up to
         *  {@link #MAX_BLOCK_SETS} combinations, the smallest of every legal set. Past that (a Lure against thirteen
         *  creatures, pr-review of #311), the smallest legal one of the first-attacker set (each creature on the first
         *  attacker it can block) and one all-on-it set per attacker, else a search by increasing size that stops
         *  after {@link #MAX_BLOCK_SETS} checks. */
        static List<Card[]> legalBlockSet(Combat combat, Player defender) {
            List<Card> able = ableBlockers(defender, combat), attackers = combat.getAttackers();
            List<List<Card[]>> legal = legalBlockSets(combat, defender, able, attackers);
            if (legal == null) {
                legal = new ArrayList<>();
                List<Card[]> first = new ArrayList<>();
                for (Card b : able)
                    for (Card a : attackers) if (CombatUtil.canBlock(a, b, combat)) { first.add(new Card[] {b, a}); break; }
                if (blocksError(combat, defender, first) == null) legal.add(first);
                for (Card a : attackers) {
                    List<Card[]> all = new ArrayList<>();
                    for (Card b : able) if (CombatUtil.canBlock(a, b, combat)) all.add(new Card[] {b, a});
                    if (blocksError(combat, defender, all) == null) legal.add(all);
                }
                if (legal.isEmpty()) {
                    int[] budget = {MAX_BLOCK_SETS};
                    for (int k = 1; k <= able.size() && budget[0] > 0; k++) {
                        List<Card[]> found = searchBlocks(combat, defender, able, attackers, k, 0, new ArrayList<>(), budget);
                        if (found != null) return found;
                    }
                }
            }
            return legal.isEmpty() ? null : java.util.Collections.min(legal, java.util.Comparator.comparingInt(List::size));
        }

        /** The first legal set of exactly k blockers from able[from..], each on an attacker it can block; every check
         *  spends one of budget[0], and the search stops when it is spent. */
        private static List<Card[]> searchBlocks(Combat combat, Player defender, List<Card> able, List<Card> attackers,
                                                 int k, int from, List<Card[]> chosen, int[] budget) {
            if (chosen.size() == k) {
                budget[0]--;
                return blocksError(combat, defender, chosen) == null ? new ArrayList<>(chosen) : null;
            }
            for (int i = from; i < able.size(); i++)
                for (Card a : attackers) {
                    if (budget[0] <= 0) return null;
                    if (!CombatUtil.canBlock(a, able.get(i), combat)) continue;
                    chosen.add(new Card[] {able.get(i), a});
                    List<Card[]> found = searchBlocks(combat, defender, able, attackers, k, i + 1, chosen, budget);
                    chosen.remove(chosen.size() - 1);
                    if (found != null) return found;
                }
            return null;
        }

        /** A failure branch's blocks: none, unless a block requirement (a lure, "all creatures able to block it do so",
         *  "blocks each combat if able") forbids that; then {@link #legalBlockSet}. The engine checks blocks only for
         *  its own GUI, so "no blocks" under Prized Unicorn stood illegal (issue #311, harness-env grader
         *  combat.block.must-block). Returns what was declared. */
        private String fallbackBlocks(Player defender, Combat combat) {
            String err = blocksError(combat, defender, new ArrayList<>());
            if (err == null) return "none";
            List<Card[]> pick = legalBlockSet(combat, defender);
            if (pick == null) {
                p("[BLOCK-INVALID] no blocks is not legal (" + err + ") and no legal set was found: none");
                return "none";
            }
            StringBuilder took = new StringBuilder();
            for (Card[] pr : pick) {
                combat.addBlocker(pr[1], pr[0]);
                took.append(took.length() > 0 ? ", " : "").append(pr[0].getName()).append("->").append(pr[1].getName());
            }
            p("[BLOCK-INVALID] no blocks is not legal (" + err + "): took the legal set " + took);
            return "legal " + pick.size();
        }

        @Override
        public void declareBlockers(Player defender, Combat combat) {
            clearLast();
            try {
                CardCollection attackers = combat.getAttackers();
                if (attackers == null || attackers.isEmpty()) return;
                CardCollection blockers = new CardCollection(ableBlockers(defender, combat));
                if (blockers.isEmpty()) return;
                // D4 shadow: capture pool size NOW (the assignment loop below mutates `blockers`), compute
                // the AI's block count into a scratch combat carrying the real attackers (real combat untouched).
                // Held in a LOCAL and attached only at the two genuine (model-consulted) evalDec sites — the
                // auto() returns below (random arm / no-model / bridge-down) are harness-made decisions the
                // student never faced, so labeling them would poison the divergence report with rows whose
                // "student choice" was never a choice at all.
                final Integer shadowBlk = shadowBlockers(defender, combat, blockers.size());
                if (rnd != null) { // random arm: each legal blocker blocks a random attacker or not, 50/50
                    // The prover picks among the legal block sets, one draw for the whole set (each set is one line).
                    if (rnd instanceof PuzzleArms.Search) {
                        List<List<Card[]>> legal = legalBlockSets(combat, defender, new ArrayList<>(blockers), attackers);
                        if (legal != null && !legal.isEmpty()) {
                            List<Card[]> pick = legal.get(rnd.nextInt(legal.size()));
                            for (Card[] pr : pick) combat.addBlocker(pr[1], pr[0]);
                            auto("blockers(search)->" + pick.size() + " of " + legal.size() + " legal sets");
                            return;
                        }
                    }
                    // A draw the engine objects to (menace, a block requirement) is drawn again, and after a few, a
                    // legal set is taken from the engine's own check; a legal first draw leaves the arm's stream as it was.
                    for (int draw = 0; ; draw++) {
                        List<Card[]> pairs = new ArrayList<>();
                        for (Card b : blockers) {
                            if (rnd.nextBoolean()) continue;
                            Card a = attackers.get(rnd.nextInt(attackers.size()));
                            if (CombatUtil.canBlock(a, b, combat)) pairs.add(new Card[] {b, a});
                        }
                        String err = blocksError(combat, defender, pairs);
                        if (err != null && draw >= 3) {
                            List<List<Card[]>> legal = legalBlockSets(combat, defender, new ArrayList<>(blockers), attackers);
                            if (legal != null && !legal.isEmpty()) { pairs = legal.get(rnd.nextInt(legal.size())); err = null; }
                            else if (legal == null && draw >= 63) {   // past MAX_BLOCK_SETS: the seat's fixed legal set
                                List<Card[]> fixed = legalBlockSet(combat, defender);
                                if (fixed != null) { pairs = fixed; err = null; }
                            }
                        }
                        if (err == null || draw >= 63) {
                            for (Card[] pr : pairs) combat.addBlocker(pr[1], pr[0]);
                            if (err != null) p("[BLOCK-INVALID] arm: no legal block set in 64 draws (" + err + ")");
                            auto("blockers(random)->" + pairs.size() + (draw > 0 ? " (draw " + (draw + 1) + ")" : ""));
                            return;
                        }
                    }
                }
                // No model available -> the fixed fallback (fallbackBlocks), never the engine AI.
                if (!smartOk()) { auto("blockers(no-model)->" + fallbackBlocks(defender, combat)); return; }
                StringBuilder atk = new StringBuilder();
                for (Card a : attackers) { if (atk.length() > 0) atk.append(", "); atk.append(observer != null ? observer.shortRef(a) : a.getName() + " (" + a.getNetPower() + "/" + a.getNetToughness() + ")"); }
                StringBuilder blk = new StringBuilder();
                List<String> blkLabels = new ArrayList<>(); // candidate pool captured BEFORE the assignment loop mutates `blockers`
                // A creature that can block more than one attacker may (CR 509.1a): its label says how many, and it is
                // not dropped after its first block (each blocker blocked once, whatever the card said).
                List<Integer> blkLimits = new ArrayList<>();
                for (Card b : blockers) {
                    if (blk.length() > 0) blk.append(", ");
                    int lim = b.canBlockAny() ? attackers.size() : 1 + b.canBlockAdditional();
                    String bl = (observer != null ? observer.shortRef(b) : b.getName() + " (" + b.getNetPower() + "/" + b.getNetToughness() + ")")
                            + (lim > 1 ? " (can block " + (b.canBlockAny() ? "any number of" : "up to " + lim) + " attackers)" : "");
                    blk.append(bl); blkLabels.add(bl); blkLimits.add(lim);
                }
                // Shared template (single source; the ai-teacher seat rebuilds byte-identically via the same builder).
                String prompt = buildBlockersPrompt(snapshot(), atk.toString(), blk.toString(), blockersCeiling(combat, defender));
                // Every other ask names its DECISION; the one-chat history keeps an earlier ask from that
                // line on, so without it a block kept its whole stale state (PromptFidelityTest contract).
                if (observer != null) prompt = prompt.replace("\nCOMBAT — declare blockers.", "\nDECISION: COMBAT — declare blockers.");
                if (blockCorrection != null)
                    prompt += "\n\nThe blocks you declared are not legal (the engine says: " + blockCorrection + "). Declare blockers again.";
                p("[ROUTER] blockers: " + attackers.size() + " atk / " + blockers.size() + " blk -> " + routeLabel(smart.getModel(), rnd != null));
                // The answer tool names blocks by NUMBER: the two lists, numbered, and every pair checked
                // against the engine before it is accepted (an illegal block is corrected in the same
                // conversation instead of being dropped).
                List<String> atkLabels = new ArrayList<>();
                for (Card a : attackers) atkLabels.add(observer != null ? observer.shortRef(a) : a.getName() + " (" + a.getNetPower() + "/" + a.getNetToughness() + ")");
                final List<Card> blkList = new ArrayList<>(blockers), atkList = new ArrayList<>(attackers);
                if (proposesCombat()) { proposeBlockers(defender, combat, atk.toString(), blkLabels, atkLabels, blkList, atkList, shadowBlk); return; }
                AnswerTool.Spec blockSpec = AnswerTool.Spec.blocks(blkLabels, atkLabels, blkLimits).check(ans -> {
                    List<Card[]> pairs = new ArrayList<>();
                    for (int[] pr : ans.blocks) pairs.add(new Card[] {blkList.get(pr[0] - 1), atkList.get(pr[1] - 1)});
                    String cap = blockCapError(defender, pairs);
                    if (cap != null) return "these blocks are not legal together: " + cap;
                    int bad = firstBlockNotAllowed(combat, pairs);   // each pair with the others in place (pr-review of #349)
                    if (bad >= 0) {
                        int[] pr = ans.blocks.get(bad);
                        return "blocker " + pr[0] + " (" + blkLabels.get(pr[0] - 1) + ") cannot block attacker "
                                + pr[1] + " (" + atkLabels.get(pr[1] - 1) + ").";
                    }
                    String err = blocksError(combat, defender, pairs);
                    return err == null ? null : "these blocks are not legal together: " + err;
                });
                String numbered = "\nATTACKERS: " + numberedList(atkLabels) + "\nYOUR BLOCKERS: " + numberedList(blkLabels) + "\n";
                String line = null;
                for (int attempt = 0; attempt < 2 && line == null; attempt++) {
                    // The retry carries the whole decision, as decideOne does.
                    String text = attempt == 0 ? prompt
                            : prompt + "\n\n(Your previous reply had no BLOCKS line. End with EXACTLY: BLOCKS: blocker->attacker, ...   or BLOCKS: NONE)";
                    offerAnswer(smart, blockSpec);
                    String reply = ask(smart, nextAnswer != null ? text + numbered : text);
                    noteAnswerVia(attempt);
                    if (answerLost()) { auto("blockers(unparsed)->" + fallbackBlocks(defender, combat)); return; }
                    if (reply == null) { auto("blockers(bridge-down)->" + fallbackBlocks(defender, combat)); return; }
                    AnswerTool.Answer ta = toolAnswer();
                    if (ta != null) {
                        int assigned = 0;
                        for (int[] pr : ta.blocks) {   // validated, each pair checked with CombatUtil.canBlock
                            Card b = blkList.get(pr[0] - 1), a = atkList.get(pr[1] - 1);
                            combat.addBlocker(a, b); assigned++; blockers.remove(b);
                        }
                        lastShadowChoice = shadowBlk; // genuine model-consulted decision -> label it
                        evalDec("blockers", blkLabels.size(), assigned, "smart", 0, null, blkLabels); // choice = # blocks
                        p("[ROUTER] blockers: " + (assigned == 0 ? "NONE" : "assigned " + assigned));
                        return;
                    }
                    line = textSegment(reply, "BLOCKS:");
                }
                if (line == null) { auto("blockers(unparsed)->" + fallbackBlocks(defender, combat)); return; }
                line = line.split("\\R", 2)[0];
                if (line.trim().replaceAll("[^A-Za-z]", "").equalsIgnoreCase("NONE")) {
                    // A genuine model-consulted decision (the reply came back fine) — must log exactly
                    // one jsonl row like every other ask path, even though it declines to block. This was
                    // the silent-drop gap (report iter-1 D2): the ask() succeeded and counted in the log's
                    // "-> asking" tally, but this branch returned with no evalDec call, so the jsonl side
                    // came up one row short whenever the model chose not to block at all.
                    lastShadowChoice = shadowBlk; // genuine model-consulted decision -> label it
                    evalDec("blockers", blkLabels.size(), 0, "smart", 0, null, blkLabels);
                    p("[ROUTER] blockers: NONE");
                    String none = blocksError(combat, defender, new ArrayList<>());
                    if (none != null) illegalBlocks(defender, combat, none);
                    return;
                }
                // The pairs are gathered and judged as a set before any is added, as the tool's are: added one by one,
                // the verdict depended on the order they were written in (a block beside a "must be blocked if able"
                // attacker's blocker, written first, was refused; pr-review of #349).
                List<Card[]> pairs = new ArrayList<>();
                List<String> written = new ArrayList<>(), dropped = new ArrayList<>();
                Map<Card, Integer> uses = new java.util.HashMap<>();
                int able = blockers.size();
                for (String[] pair : blockPairs(line, blockers, attackers)) {
                    Card b = matchCard(blockers, pair[0]);
                    Card a = matchCard(attackers, pair[1]);
                    String label = flat(pair[0]) + "->" + flat(pair[1]);
                    // A block the blocker can never make (a flyer, "can't block") is not used and leaves its name for the
                    // next pair (pr-review of #351): with one Bears, "Bears->Serra Angel, Bears->Goblin Raider" blocks the Raider.
                    if (a == null || b == null || !CombatUtil.canBlock(a, b)) { dropped.add(label); continue; }
                    pairs.add(new Card[] {b, a});
                    written.add(label);
                    // A blocker at its limit leaves the names matched, so a second "Grizzly Bears" names the other one.
                    if (uses.merge(b, 1, Integer::sum) >= blockLimit(b)) blockers.remove(b);
                }
                String cap = blockCapError(defender, pairs);
                if (cap == null) {
                    for (int bad; (bad = firstBlockNotAllowed(combat, pairs)) >= 0; ) { pairs.remove(bad); dropped.add(written.remove(bad)); }
                    for (Card[] pr : pairs) combat.addBlocker(pr[1], pr[0]);
                } else {
                    dropped.addAll(written);   // over the cap nothing is declared: every pair is not used, and the re-ask says why
                }
                int assigned = cap == null ? pairs.size() : 0;
                lastShadowChoice = shadowBlk; // genuine model-consulted decision -> label it
                // A pair that named no legal block used to vanish; the row says how many were not used.
                evalDec("blockers", able, assigned, "smart", 0,
                        dropped.isEmpty() ? null : "blockers(unparsed-pairs:" + dropped.size() + ")", blkLabels); // choice = # blocks
                p("[ROUTER] blockers: assigned " + assigned + (dropped.isEmpty() ? "" : ", not used: " + String.join("; ", dropped)));
                String err = cap != null ? cap : blocksError(combat, defender, new ArrayList<>());
                if (err != null) illegalBlocks(defender, combat, err);
            } catch (Exception e) {
                // Same rule as declareAttackers (#126): a deterministic LEGAL default, never the AI.
                String took = "none";
                try {
                    for (Card b : CardLists.filterControlledBy(combat.getAllBlockers(), defender)) combat.undoBlockingAssignment(b);
                    took = fallbackBlocks(defender, combat);
                } catch (Exception ignore) { }
                auto("blockers(err)->" + took);
            }
        }

        @Override
        public List<SpellAbility> chooseSpellAbilityToPlay() {
            // IMPORTANT: the engine passes priority ONLY on a null return (see PhaseHandler.mainLoopStep:
            // `if (chosenSa == null) break;`). An EMPTY list means "play zero abilities, then ask again",
            // which spins the priority loop ~999x ("AI looped too much"). So PASS must be null, never empty.
            final List<SpellAbility> PASS = null;
            clearLast();
            // Forge's static AiCache is cleared where Forge's AI chooses a play (AiController.chooseSpellAbilityToPlay);
            // the seat's pricing and combat helpers read it too, so the seat clears it in the same place. Uncleared,
            // answers keyed by this game lasted the whole game and every finished game stayed reachable (prove-v3).
            forge.ai.AiCache.clear();
            boolean chose = false;   // the seat picked a play: an error after this lost it
            try {
                Player me = getPlayer();
                PhaseHandler phh = me.getGame().getPhaseHandler();
                boolean ownMain = phh.isPlayerTurn(me) && (phh.is(PhaseType.MAIN1) || phh.is(PhaseType.MAIN2));
                String winKey = phh.getTurn() + "|" + phh.getPhase() + "|" + safeStackTop(me.getGame());
                if (!winKey.equals(cancelKey)) { cancelKey = winKey; cancels = 0; cancelNote = null; failedInWindow.clear(); }

                // SPEED: on a reactive (not-our-main) priority window, only think when something can
                // actually matter — a spell/ability on the stack, combat (tricks/removal), or the
                // opponent's end step (the classic instant-speed action window for flash/instant decks).
                // Otherwise passing priority is virtually always correct, so pass WITHOUT an LLM call.
                boolean oppEot = phh.is(PhaseType.END_OF_TURN) && !phh.isPlayerTurn(me);
                if (SKIP_HEUR && rnd == null && !ownMain && me.getGame().getStack().isEmpty() && !phh.inCombat() && !oppEot)
                    return skipWindow("window-gate", castableNow(me, ownMain));

                // SPEED: the top of the stack is OUR OWN spell/ability — we just cast it. Don't burn a
                // model call asking "respond to yourself?"; let it resolve. The engine gives us priority
                // again with the updated state (and if the OPPONENT responds, their spell tops the stack
                // and we DO get asked).
                if (SKIP_HEUR && rnd == null && !me.getGame().getStack().isEmpty()) {
                    try {
                        SpellAbility top = me.getGame().getStack().peekAbility();
                        // ...but a TRIGGER of ours is not something we "just cast": it is the engine handing
                        // us a window. "Sacrifice the tokens at the beginning of the end step" on the stack
                        // is exactly when a sacrifice outlet should be used on them, and the seat was never
                        // asked (history/ARCHITECTURE-REVIEW.md problem 3). The windows below still apply: no
                        // instant-speed option -> pass; nothing that interacts -> pass; and an identical
                        // situation already declined this turn is not asked twice (passedReactSigs).
                        if (top != null && top.getActivatingPlayer() == me && !top.isTrigger())
                            return skipWindow("own-stack", castableNow(me, ownMain));
                    } catch (Exception ignore) { }
                }

                // Enumerate castable spells + distinct playable lands (shared with the ai-teacher seat so
                // both build the byte-identical option menu — see enumeratePlayOptions).
                PlayOptions po = enumeratePlayOptions(me, ownMain, faithfulMenu(observer != null, rnd));
                List<SpellAbility> options = po.spells;
                if (!failedInWindow.isEmpty() && options.removeIf(sa -> failedInWindow.contains(failedPlayKey(sa))))
                    p("[ROUTER] play: a play that failed to pay in this window is not offered again");
                List<SpellAbility> landPlays = po.landPlays;
                List<Card> landCards = po.landCards;
                boolean haveLand = !landPlays.isEmpty();
                if (PROPOSE_AUDIT && observer != null) auditProposals(options, landPlays, landCards, me, ownMain);
                // Nothing to do -> pass (deterministic; never Forge's AI).
                // A window whose only plays float mana that changes nothing is passed as an empty one (asking it would
                // make every window a model call). Floating that does change the game (a creature tapped, a trigger, a
                // pool that keeps or burns) is a legal play like any other and is asked: see floatChangesNothing.
                boolean onlyFloat = !options.isEmpty() && options.stream().allMatch(o -> floatChangesNothing(o, me));
                if ((options.isEmpty() || onlyFloat) && !haveLand) return skipWindow("nothing-usable", 0);
                if (!ownMain && (options.isEmpty() || onlyFloat)) return skipWindow("nothing-usable", 0);

                // SPEED (interaction filter): on a mid-turn reactive window (not opp EOT, not combat),
                // only think if we hold something that can actually INTERACT with what's happening —
                // a targeting spell (removal/trick) or a counter. Rituals/eggs/mana spells cast in
                // response to an opponent's play achieve nothing; measured 66% of all play-asks were
                // PASS results, ~15 min/game of declining. Opp EOT and combat still always ask.
                // (rnd == null: the random eval arm bypasses speed filters so its baseline stays a true
                // uniform over the unpruned menu — filters exist to save model calls, which random has none of.)
                // An OPPONENT's spell or ability on the stack is the one window where any real response
                // counts, targeted or not: "exile each opponent's graveyard" (Soul-Guide Lantern) with
                // their Reanimate on the stack neither targets nor counters, and this filter used to pass
                // priority for the seat without asking. With their object on the stack, only pure mana
                // production is treated as not worth a question.
                boolean oppOnStack = false;
                try {
                    SpellAbility top = me.getGame().getStack().isEmpty() ? null : me.getGame().getStack().peekAbility();
                    oppOnStack = top != null && top.getActivatingPlayer() != null && top.getActivatingPlayer() != me;
                } catch (Exception ignore) { }
                if (yieldUntil != null) {
                    boolean over = "end-of-turn".equals(yieldUntil) ? phh.getTurn() != yieldTurn
                            : phh.isPlayerTurn(me) && phh.getTurn() != yieldTurn;
                    if (over) yieldUntil = null;
                    // The seat's own main phase with an empty stack is never yielded away: its land drop and
                    // sorcery-speed plays live there (runs/luna-v8/g3 yielded at its upkeep and lost both turns).
                    else if (!oppOnStack && !(ownMain && me.getGame().getStack().isEmpty()))
                        return skipWindow("yield:" + yieldUntil, options.size());
                }
                if (SKIP_HEUR && rnd == null && !ownMain && !oppEot && !phh.inCombat()) {
                    boolean anyInteractive = false;
                    for (SpellAbility sa : options) {
                        if (oppOnStack && !onlyMakesMana(sa)) { anyInteractive = true; break; }
                        try { // walk the whole ability chain — Counter/targeting often lives on a sub
                            for (SpellAbility part = sa; part != null; part = part.getSubAbility()) {
                                if (part.usesTargeting() || part.getApi() == ApiType.Counter) { anyInteractive = true; break; }
                            }
                        } catch (Exception e) { anyInteractive = true; } // can't classify -> keep asking
                        if (anyInteractive) break;
                    }
                    if (!anyInteractive) return skipWindow("interaction-filter", options.size());
                }

                // pick tier: own main phase = smart sequencing; reactive instant-speed window = fast.
                // Exception: a main with ZERO castable spells (just land-or-pass) is a fast-tier question —
                // measured 15-30s smart calls deciding "play the land". Which land still gets picked by a model.
                boolean landOnly = ownMain && options.isEmpty();
                // ROUTING (opt-in via TIER_FAST_TYPES=play-single): in own main with exactly ONE
                // castable spell and no land, the menu is [cast it, PASS] -- a binary hold-or-fire
                // question, not a sequencing one, so it does not need the reasoning tier.
                boolean playSingle = ownMain && options.size() == 1 && landPlays.isEmpty();
                boolean playSingleFast = playSingle && !smartTierFor("play-single");
                // Responding to an opponent's spell is where counter/hate timing lives, so it takes the
                // tier the routing table gives "play-react": smart unless listed in TIER_FAST_TYPES.
                boolean reactSmart = !ownMain && oppOnStack && rnd == null && smartTierFor("play-react") && smartOk();
                LLMBridge b = ((ownMain && !landOnly && !playSingleFast) || reactSmart) ? smart : fast;
                boolean haveBudget = (b == smart) ? smartOk() : fastOk();

                int turn = phh.getTurn();
                if (turn != lastPlayTurn) { lastPlayTurn = turn; playDecisionsThisTurn = 0; passedReactSigs.clear(); }
                boolean overCap = playDecisionsThisTurn >= (observer != null ? MAX_PLAY_DECISIONS_PER_TURN_FAITHFUL : MAX_PLAY_DECISIONS_PER_TURN);
                if (!haveBudget || overCap) { // exhausted -> develop mana if possible, else pass. Not the AI.
                    // The cap and the budget are different causes and say so (the cap used to read "no-budget").
                    String why = !haveBudget || observer == null ? "no-budget" : "turn-cap";
                    if (haveLand) { auto("play(" + why + "): land " + landCards.get(0).getName()); return Lists.newArrayList(landPlays.get(0)); }
                    auto("play(" + why + ")->PASS");
                    return PASS;
                }

                // Option labels for the decision record, in the exact menu order shown to the model
                // (shared with the ai-teacher seat — see buildPlayLabels/numberedPlayMenu). The numbered
                // menu string and the land/pass boundary indices are derived from them so BOTH seats
                // build the byte-identical menu.
                List<String> playLabels = playLabels(options, landCards);
                String menuStr = numberedPlayMenu(playLabels);
                int landStart = 1 + options.size();                       // 1-based menu number of the first land
                int passIdx = 1 + options.size() + landPlays.size();      // 1-based menu number of PASS

                // FORCED MENU: nothing castable and no land to play, so PASS is the only legal action.
                // Asking the model would be a pure-latency no-op decision, and a turn can hold many
                // such windows. Scoped to the model arm so the floor/random canary arms are untouched.
                if (passIdx == 1 && rnd == null && smartOk()) { auto("play(no-options)->PASS"); return PASS; }

                // The stack is part of the spot: without it, passing on the opponent's Ponder also passed
                // on their Show and Tell in the same phase (same menu), and Force of Will was never
                // offered. The random arm is a uniform baseline over every window, so it is never deduped.
                String sig = turn + "|" + phh.getPhase() + "|" + safeStackTop(me.getGame()) + "|" + boardDigest(me.getGame()) + "|" + menuStr;
                if (rnd == null && sig.equals(lastAskSig) && lastWasPass) return skipWindow("last-sig", options.size()); // identical spot we already passed on
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
                    if (passedReactSigs.contains(reactSig)) return skipWindow("react-sig", options.size());
                }

                if (ownMain) p("[TALLY] smart=" + smartCalls + " fast=" + fastCalls + " auto=" + autoPicks + " ai=" + aiDelegations
                        + " textParses=" + textParsesOnToolAsks
                        + (textFormatLeaks > 0 ? " text_format_leaks=" + textFormatLeaks : "")
                        + latencyAverages(smart, fast));
                p("[ROUTER] play(" + (ownMain ? "main" : "react") + "): " + options.size() + " castable"
                        + (haveLand ? " + " + landPlays.size() + " land" : "") + " -> " + routeLabel(b.getModel(), rnd != null));
                playDecisionsThisTurn++;

                int choice = -1;
                // First main-phase decision of OUR turn: also ask for a one-line turn plan (piggybacked —
                // costs zero extra calls). It rides in the snapshot for every later decision this turn.
                boolean askPlan = !landOnly && planTurn != turn && b == smart; // once per turn, EITHER player's turn
                // The opponent read is refreshed rarely (empty, or >=5 turns old) and rides the same ask,
                // so it never costs an extra model call.
                boolean askProfile = askPlan && (oppProfile.isEmpty() || turn - profileTurn >= PROFILE_REFRESH_TURNS);
                String asks = askPlan
                        ? (observer != null
                           // Faithful (v22): a goal and the steps toward it, replaceable at any decision.
                           ? "First state your plan as `PLAN: GOAL: <what you are working toward> NEXT: <the steps you intend, in order, "
                             + "e.g. <land>, cast <card>, hold <card> for their turn>`. It is a note, not a commitment: every later decision is "
                             + "yours on the state then, and you replace the plan when it no longer fits. "
                           // The example named one eval deck's line (Grixis Reanimator) in every deck's prompt.
                           : "First state your plan for THIS TURN as `PLAN: <one short line>` (the sequence you intend, e.g. "
                             + "'ritual into Reanimate on Griselbrand, hold Petal'). ")
                          // Only where the steps are carried out: otherwise the offer is a promise the harness breaks.
                          + (PLAN_EXECUTE ? "If (and only if) you want the harness to execute steps of that plan for you without asking again this turn, add `STEPS: <card name>, <card name>` listing ONLY cards you intend to CAST, in the order you will cast them; omit anything you are holding. " : "")
                        : "";
                // Ask at most DECK_LINES_MAX_ASKS times, on the model's first real decisions: name the
                // interactions among its OWN cards. It is the model's own note (labelled as such when it
                // is shown back), and an audit field: what it said its lines were vs what it then did.
                boolean askLines = !deckLinesAsked && deckLinesAsks < DECK_LINES_MAX_ASKS && b == smart && rnd == null && observer != null;
                if (askLines) asks += DECK_LINES_ASK;
                if (askProfile) asks += "Also state your read of the OPPONENT as `PROFILE: <one short line>` "
                        + "(their deck/plan and the cards to respect, e.g. "
                        + (observer != null ? "'<archetype>: <their key cards>, respect <card>'). " : "'Grixis Reanimator: Entomb+Reanimate, respect Force of Will'). ");
                // The answer tool: the same notes, asked as `set_plan` and as fields of the answer.
                final boolean playTool = answerToolFor(b);
                if (playTool) asks = playToolAsks(askPlan, askLines, askProfile);
                boolean lookaheadOn = lookaheadEnabled();
                // PLAN-THEN-EXECUTE: run a step of the model's own plan without asking again.
                if (PLAN_EXECUTE && rnd == null && ownMain && !landOnly && planTurn == turn
                        && turnPlan != null && !turnPlan.isEmpty() && getGame().getStack().size() == 0
                        && passIdx > 1) {
                    if (planSig != null && !oppPlanSig().equals(planSig)) {
                        p("[ROUTER] play(plan-invalidated): opponent-side state changed -> re-plan");
                        planSig = null; turnPlan = ""; planTurn = -1; planStepIdx = 0;
                    } else {
                        // Walk the EXPLICIT step list in order. A step no longer in the menu (already
                        // cast, or no longer castable) is skipped rather than jumping to a later step.
                        while (planStepIdx < planSteps.size()) {
                            String step = planSteps.get(planStepIdx);
                            planStepIdx++;
                            int idx = planStepInMenu(step, options);
                            if (idx < 0) continue;
                            SpellAbility planned = options.get(idx);
                            playDecisionsThisTurn++;
                            evalDec("play", passIdx, idx, "plan", 0,
                                    "play(plan-executed)->step" + planStepIdx, playLabels);
                            p("[ROUTER] play: plan step " + planStepIdx + " (" + step + ") -> " + labelOf(planned));
                            // Its follow-up asks carry THIS play, not the last one the model chose by hand (#108).
                            if (observer != null) {
                                actionIntent = verbOf(planned) + " " + labelOf(planned) + ". It is step " + planStepIdx
                                        + " of the STEPS you listed for your plan: \"" + turnPlan + "\"";
                                actionIntentTurn = turn;
                            }
                            ensureTargetsSet(planned);
                            return Lists.newArrayList(planned);
                        }
                    }
                }

                StringBuilder lookCtx = new StringBuilder(); // the previews this decision asked for (text path)
                int textPreviews = 0;
                // Native tools: the preview is a function the model calls, not a line it must reply with.
                // `simulate` indexes the DECISION'S OWN numbered menu, so the numbers the model reads
                // off the prompt are the numbers it passes: the CAST/ACTIVATE options, then the PLAY
                // LAND options, then PASS. A land drop is a
                // first step the model keeps asking for — "play the land, then the spell it pays for"
                // (runs/loop/g02 turn 6: {"option":4,"then":[3]} on PLAY LAND Wasteland then Stoneforge
                // Mystic's ability) — and 13 of 92 requests named one in runs/lines/ctl2/native-lookahead.
                final List<SpellAbility> simOptions = new ArrayList<>(options);
                simOptions.addAll(landPlays);
                // With the answer tool the preview is a function call even without MEMORY_TOOLS.
                final boolean nativeSim = lookaheadOn && !simOptions.isEmpty()
                        && ((MEMORY_TOOLS && NativeTools.enabled() && b.supportsNativeTools()) || playTool);
                final int simTurn = turn;
                final int simPassIdx = passIdx;      // PASS is the last menu line; a preview of it is not an option
                final java.util.function.Function<String, String> simulateTool = argsJson -> {
                    com.google.gson.JsonObject a = NativeTools.arguments(argsJson);
                    int n = -1;
                    try { if (a != null && a.has("option")) n = a.get("option").getAsInt(); } catch (RuntimeException e) { n = -1; }
                    if (n < 1 || n > simPassIdx)
                        return "ERROR: `option` must be a menu number of a CAST, ACTIVATE, PLAY LAND or PASS option (1.." + simPassIdx + ").";
                    String hint = a.has("target") && a.get("target").isJsonPrimitive() ? a.get("target").getAsString().trim() : null;
                    // `then`: further options to play after it, in order, on the same copy (a line).
                    // PASS resolves nothing, so it is never a step in the line; a request that is only
                    // PASS is answered by previewPass() instead of a copy.
                    List<SpellAbility> line = new ArrayList<>();
                    if (n != simPassIdx) line.add(simOptions.get(n - 1));
                    StringBuilder which = new StringBuilder(String.valueOf(n));
                    if (a.has("then") && a.get("then").isJsonArray()) {
                        for (com.google.gson.JsonElement e : a.getAsJsonArray("then")) {
                            int m;
                            try { m = e.getAsInt(); } catch (RuntimeException ex) { m = -1; }
                            if (m < 1 || m > simPassIdx)
                                return "ERROR: every number in `then` must be a menu number of a CAST, ACTIVATE, PLAY LAND or PASS option (1.." + simPassIdx + ").";
                            if (m != simPassIdx) line.add(simOptions.get(m - 1));
                            which.append(", then ").append(m);
                        }
                    }
                    String preview = line.isEmpty() ? PlayerLookahead.previewPass()
                            : PlayerLookahead.previewLine(me.getGame(), me, line, simTurn,
                                  hint == null || hint.isEmpty() ? null : hint);
                    p("[ROUTER] lookahead " + which + ": " + preview);
                    return "LOOKAHEAD (option " + which + "): " + preview;
                };
                AnswerTool.Answer playAnswer = null;  // the tool answer, when the play was answered by a call
                // PROPOSE_ACTIONS: the menu is built as always but not shown; the seat proposes a play and the menu checks
                // it (matchProposal). The windows asked are the same as with the menu, so the two arms see the same spots.
                final boolean propose = AnswerTool.proposeActions() && observer != null && playTool;
                final String shownMenu = propose ? PROPOSE_PROMPT : menuStr;
                // REFUSED EARLIER as it stood before this decision: a retry ask is a new conversation, and this decision's
                // own refusals are noted after it (the header says "earlier decisions").
                final String notebook = refusedEarlier();
                if (rnd != null) choice = 1 + PuzzleArms.pick(rnd, passIdx, true); // random arm: uniform incl. PASS (last)
                // A note is said once, on the next menu: it stayed until the window changed, and a land drop keeps the
                // window, so it was repeated on every menu after it (#392 review).
                final String note = cancelNote;
                cancelNote = null;
                for (int attempt = 0; rnd == null && attempt < 2 && choice < 1; attempt++) {
                    String prompt = (attempt == 0
                            ? (propose ? proposePromptPrefix(snapshot() + notebook, phh.getPhase(), ownMain) : playPromptPrefix(snapshot(), phh.getPhase(), ownMain, menuStr))
                              + (note != null && attempt == 0 ? "NOTE: " + note + "\n" : "")
                              + (!SKIP_HEUR && observer != null && attempt == 0 ? (playTool ? YIELD_TOOL_HINT : YIELD_HINT) : "")
                              + (asks.isEmpty() ? PLAY_PROMPT_TAIL : asks + "Then end with EXACTLY: CHOICE: <number>")
                            // The retry carries the WHOLE decision again. It used to be this one sentence
                            // alone ("...from the list above") sent to a stateless backend with no list
                            // above: in runs/lines/blocks the model spent both tools searching its archive
                            // for the question and the decision was lost (3 of 9 games, one a PASS on its
                            // own main phase). decideOne and decideMany already re-send the state.
                            : playRetryPrompt(playPromptPrefix(snapshot() + notebook, phh.getPhase(), ownMain, shownMenu), passIdx))
                            + lookCtx
                            + (lookaheadOn && attempt == 0 && !nativeSim
                               ? "\nYou may instead reply `SIMULATE: <number>` (optionally `SIMULATE: <number> <target name>`) to see what a CAST or ACTIVATE option (1.." + options.size()
                                 + ") resolves to; you will then be asked again with the result." : "");
                    AnswerTool.Spec playSpec = propose
                            ? AnswerTool.Spec.action(passIdx).labels(playLabels).check(a -> {
                                  int[] hit = {-1};
                                  String err = matchProposal(a.action, options, landPlays, landCards, passIdx, me, ownMain, hit);
                                  if (err == null) a.option = hit[0];
                                  return err;
                              })
                            : AnswerTool.Spec.one(passIdx).labels(playLabels);
                    if (attempt == 0) {
                        playSpec.plan = askPlan;
                        if (askPlan && PLAN_EXECUTE && !options.isEmpty() && !propose) {
                            playSpec.stepOptions = new ArrayList<>();
                            for (int i = 1; i <= options.size(); i++) playSpec.stepOptions.add(i);
                        }
                        playSpec.yield = !SKIP_HEUR && observer != null;
                        playSpec.profile = askProfile;
                        playSpec.lines = askLines;
                    }
                    playSpec.keep = observer != null && !options.isEmpty();
                    offerAnswer(b, playSpec);
                    // The preview takes menu numbers, which this arm does not show: no preview under it (run_batch refuses
                    // --lookahead with --propose-actions).
                    String reply = ask(b, prompt, nativeSim && attempt == 0 && !propose ? simulateTool : null);
                    noteAnswerVia(attempt);
                    if (propose && lastAnswer != null) PROPOSAL_ROW.set(proposalRecord(lastAnswer.proposals, "action"));
                    if (propose) noteRefusals(AnswerTool.Kind.ACTION, "priority (" + phh.getPhase()
                            + (ownMain ? ", your main phase" : ", instant-speed window") + ")");
                    if (answerLost()) break;     // two invalid replies: PASS below, recorded as play(unparsed)
                    AnswerTool.Answer ta = toolAnswer();
                    if (ta != null) {            // the notes are fields of the answer; nothing is read from prose
                        playAnswer = ta;
                        if (askPlan && attempt == 0 && ta.steps != null)
                            for (int n : ta.steps) {
                                String host = null;
                                try { host = safeName(options.get(n - 1).getHostCard()); } catch (Exception ignore) { }
                                if (host != null) planSteps.add(host);
                            }
                        if (askLines && attempt == 0) {
                            deckLinesAsks++;
                            if (ta.lines != null) { deckLines = ta.lines; deckLinesAsked = true; p("[ROUTER] lines: " + deckLines); }
                        }
                        if (askProfile && attempt == 0 && ta.profile != null) {
                            oppProfile = ta.profile;
                            profileTurn = turn;
                            p("[ROUTER] profile: " + oppProfile);
                        }
                        choice = ta.option;
                        // Which mana sources stay untapped, said with the play: used by holdManaSources when this
                        // play is paid, instead of a separate question.
                        pendingKeep = ta.keepUntapped;
                        pendingKeepFor = ta.keepUntapped != null && choice >= 1 && choice <= options.size() ? options.get(choice - 1) : null;
                        continue;
                    }
                    if (reply != null && askPlan && attempt == 0 && observer != null) {
                        // ask() already adopted a PLAN line (adoptPlan); the cast list rides this ask only.
                        String st = planStatement(reply) == null ? null : textSegment(reply, "STEPS:");
                        if (st != null) {
                            st = cutAtTags(st, "CHOICE:");
                            for (String one : st.split(",")) if (!one.trim().isEmpty()) planSteps.add(one.trim());
                        }
                    } else if (reply != null && askPlan && attempt == 0) {
                        String pl = textSegment(reply, "PLAN:");
                        if (pl != null) { // models sometimes co-mingle "PLAN: ... CHOICE: 3" — cut at the tag
                            int cut = pl.toUpperCase().indexOf("CHOICE:");
                            if (cut >= 0) pl = pl.substring(0, cut);
                            if (askLines) pl = cutAtTags(pl, "LINES:");
                            if (!pl.trim().isEmpty()) {
                                // Whitespace-collapsed only — NEVER truncated: this line is re-injected into
                                // every later prompt this turn (YOUR TURN PLAN), and a mid-word cut here is
                                // exactly the C4 defect that chopped a model's own stated finisher clause.
                                turnPlan = pl.replaceAll("\\s+", " ").trim();
                                planTurn = turn;
                                planSig = oppPlanSig();   // plan-then-execute: what "not interrupted" means
                                planStepIdx = 0;
                                planSteps.clear();
                                String st = textSegment(reply, "STEPS:");
                                if (st != null) {
                                    int cutS = st.toUpperCase().indexOf("CHOICE:");
                                    if (cutS >= 0) st = st.substring(0, cutS);
                                    for (String one : st.split(",")) if (!one.trim().isEmpty()) planSteps.add(one.trim());
                                }
                                p("[ROUTER] plan: " + turnPlan);
                            }
                        }
                    }
                    if (reply != null && attempt == 0 && askLines) {
                        deckLinesAsks++; // counted only once the model has actually been shown the ask
                        String dl = textSegment(reply, "LINES:");
                        if (dl != null) {
                            dl = cutAtTags(dl, "CHOICE:", "PLAN:", "PROFILE:", "STEPS:");
                            if (!dl.trim().isEmpty()) {
                                deckLines = dl.replaceAll("\\s+", " ").trim();
                                deckLinesAsked = true;
                                p("[ROUTER] lines: " + deckLines);
                            }
                        }
                    }
                    if (reply != null && attempt == 0 && askProfile) {
                        String pf = textSegment(reply, "PROFILE:");
                        if (pf != null) {
                            int cut = pf.toUpperCase().indexOf("CHOICE:");
                            if (cut >= 0) pf = pf.substring(0, cut);
                            int cut2 = pf.toUpperCase().indexOf("PLAN:");
                            if (cut2 >= 0) pf = pf.substring(0, cut2);
                            if (!pf.trim().isEmpty()) {
                                oppProfile = pf.replaceAll("\\s+", " ").trim();
                                profileTurn = turn;
                                p("[ROUTER] profile: " + oppProfile);
                            }
                        }
                    }
                    if (reply == null) { // bridge dead -> pass: no answer is not a land drop
                        // It used to play a land. A seat whose model never answers then out-developed the
                        // floor seat (acceptance `gaming`): with no answer the harness takes the do-nothing
                        // option, so a broken model cannot score above one that passes.
                        auto("play(bridge-down)->PASS");
                        return PASS;
                    }
                    // G8 lookahead (LOOKAHEAD=1): preview a spell option on a determinized COPY, then
                    // re-ask. As many previews as an ask has tool calls; a preview is not a failed parse.
                    if (lookaheadOn && textPreviews < ContextMemory.TOOLS_PER_ASK && !refuseText("SIMULATE:")) {
                        int simN = parseSimulateOption(reply, options.size());
                        if (simN > 0) {
                            String preview = PlayerLookahead.previewCast(me.getGame(), me, options.get(simN - 1), turn, simulateTargetHint(reply));
                            textPreviews++;
                            lookCtx.append("\nLOOKAHEAD (option ").append(simN).append("): ").append(preview)
                                   .append("\nNow end with EXACTLY: CHOICE: <number>.\n");
                            p("[ROUTER] lookahead " + simN + ": " + preview);
                            attempt--; // not a parse failure
                            continue;
                        }
                    }
                    choice = textNumber(reply, "CHOICE:");
                }
                // FORK (rollout, additive — inert unless LlmSpikeFork installed a forcer): replace the
                // computed choice at exactly one decision index so evalDec below still logs what
                // ACTUALLY happened, and the forced pick takes the SAME branches the normal path would
                // (incl. ensureTargetsSet — the AI cast path does no targeting and an untargeted cast
                // fizzles). forced < 0 or forced == the PASS slot both map to the PASS menu number.
                Integer forkForced = forkForce(me, seat, "play", passIdx, playLabels);
                if (forkForced != null) choice = forkForced.intValue() < 0 ? passIdx : forkForced.intValue() + 1;
                // D4 shadow: the AI's play over the SAME menu (choice resolved, before the row is written
                // and before the chosen SA is targeted). shadowPlay's getAi() eval may set targets/X on the
                // shared candidate SA objects; the SHADOW-guarded chosen.resetTargets() in the cast branch
                // below erases any target it set, and the engine re-announces X at cast time.
                lastShadowChoice = shadowPlay(po);
                // ms=0 -> evalDec substitutes lastSuccessMs (successful-attempt-only time); see decideOne.
                // A reply with no usable CHOICE becomes a PASS; the row says so rather than reading as a real
                // PASS (it used to be identical to one: choice -1, no fallback).
                boolean unparsed = rnd == null && (choice < 1 || choice > passIdx);
                evalDec("play", passIdx, (choice >= 1 && choice < passIdx) ? choice - 1 : -1,
                        tierOf(b == smart), 0, unparsed ? "play(unparsed)->PASS" : null, playLabels);
                if (choice == passIdx || choice < 1 || choice > passIdx) {
                    lastWasPass = !unparsed;
                    if (unparsed) p("[ROUTER] play: no usable CHOICE in the reply -> PASS (unparsed)");
                    else if (reactSig != null) passedReactSigs.add(reactSig); // don't re-ask this situation this turn
                    if (!SKIP_HEUR && observer != null && rnd == null) {
                        // A tool ask's text is never read, answered or not (frontier5, GLM 5.3 on hard-elf2); a null
                        // reply (no reply, pressed twice, out of rounds) is the reply == null PASS before this.
                        String y = playAnswer != null ? playAnswer.yieldUntil
                                : lastAnswer != null ? null : textSegment(lastReply, "YIELD:");
                        if (y != null) {
                            y = y.trim().toLowerCase(java.util.Locale.ROOT);
                            if (y.startsWith("end-of-turn") || y.startsWith("my-next-turn")) {
                                yieldUntil = y.startsWith("end") ? "end-of-turn" : "my-next-turn";
                                yieldTurn = turn;
                                p("[ROUTER] play: PASS, yield until " + yieldUntil);
                            }
                        }
                    }
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
                chose = true;
                // D4 shadow guard (MANDATORY): shadowPlay's AI eval (canPlaySa/canPlayWithSubs) may have set
                // targets AND X on this SAME SA object *and its sub-abilities*; without a full-chain reset,
                // ensureTargetsSet would see a sub-ability "already targeted" (see :1449 below) and skip it —
                // casting a sub-targeted / multi-target spell against the AI's shadow target instead of the
                // LLM's. forge SpellAbility.resetTargets() clears only THIS SA (not recursive), so we walk the
                // whole getSubAbility() chain, and null xManaCostPaid so any shadow-set X is re-announced at
                // cast (X is normally re-announced anyway, but sVar/XAlternative X can retain a stale value).
                // No-op when SHADOW is off.
                if (SHADOW) for (SpellAbility s = chosen; s != null; s = s.getSubAbility()) { s.resetTargets(); s.setXManaCostPaid(null); }
                if (observer != null && rnd == null) {
                    actionIntent = verbOf(chosen) + " " + labelOf(chosen) + ". What you wrote when you chose it: \"" + reasoningOf(lastReply) + "\"";
                    actionIntentTurn = turn;
                }
                // X, then modes, then targets: the order a player announces them (CR 601.2b-c), so a
                // mode's targets exist when targets are asked. Modes used to be chosen in
                // playChosenSpellAbility AFTER targeting, so a targeted mode never got one: Kozilek's
                // Command "Couldn't add to stack, failed to target" 26 times in recorded logs.
                chosen = withOptionalCosts(chosen);
                announceX(chosen);
                if (chosen.getApi() == ApiType.Charm) {
                    if (!forge.game.ability.effects.CharmEffect.makeChoices(chosen)) {
                        p("[ROUTER] play: no legal mode set for " + labelOf(chosen) + " -> PASS");
                        playsNotMade++;
                        return PASS;
                    }
                    modesChosenFor = chosen;
                }
                // AI cast path won't target for us — set legal LLM-chosen targets now. A target ask before
                // the play is committed may be answered "cancel": the model saw the candidates and no
                // longer wants the play (runs/loop/g06 Grindstone, g07 Wirewood Symbiote: "there's no
                // 'don't activate' option"). The menu is asked again, at most twice per window.
                if (!ensureTargetsSet(chosen, observer != null && rnd == null && cancels < 2, true)) {
                    for (SpellAbility s = chosen; s != null; s = s.getSubAbility()) s.resetTargets();
                    dropModes(chosen);
                    cancels++;
                    cancelNote = "you chose " + verbOf(chosen) + " " + labelOf(chosen) + " and then cancelled it when asked for its target. Choose again.";
                    actionIntent = null;
                    p("[ROUTER] play: cancelled " + labelOf(chosen) + " -> re-ask");
                    return chooseSpellAbilityToPlay();
                }
                p("[ROUTER] play: " + (observer == null || chosen.isSpell() ? "casts " : "activates ") + labelOf(chosen));
                return Lists.newArrayList(chosen);
            } catch (Exception e) {
                p("[ROUTER] play: error " + e + " -> PASS");
                if (chose) playsNotMade++;
                return PASS;
            }
        }

        // ---------------- NEW: routed choice callbacks (generic router + AI fallback) ----------------

        @Override
        public <T extends GameEntity> T chooseSingleEntityForEffect(FCollectionView<T> optionList, DelayedReveal delayedReveal,
                SpellAbility sa, String title, boolean isOptional, Player relatedPlayer, Map<String, Object> params) {
            clearLast();
            List<T> opts = new ArrayList<>();
            for (T t : optionList) if (!isListDivider(t)) opts.add(t);
            if (opts.isEmpty()) return null;   // nothing to choose from (Forge's AI was asked; it had nothing either)
            // delayedReveal carries a reveal side-effect: keep the engine's reveal semantics by
            // revealing BEFORE the pick (exactly PlayerControllerAi's order), then route the pick
            // through the model like any other entity choice (#128). The identities being revealed are
            // recorded so a later decision this turn can see what the look/reveal turned up (the C2
            // "opaque reveal" gap).
            if (delayedReveal != null) {
                try {
                    reveal(delayedReveal);
                    List<String> seen = new ArrayList<>();
                    for (forge.game.card.CardView cv : delayedReveal.getCards()) seen.add(cv.getName());
                    if (!seen.isEmpty()) remember("REVEALED (" + zoneLabel(delayedReveal.getZone()) + "): " + String.join(", ", seen));
                } catch (Exception ignore) { }
            }
            if (opts.size() == 1 && !isOptional) { auto("choose-entity(forced-only-legal)" + srcOf(sa)); return opts.get(0); }
            try {
                // Smart budget exhausted is NOT a reason to pick blind: fall to the fast tier so this
                // site stays model-routed (runs showed every "choose-entity(no-budget)" fired only after
                // budget exhaustion). The deterministic auto path remains for floor arms / both budgets gone.
                if (!smartOk() && !fastOk()) { auto("choose-entity(no-budget)->first" + srcOf(sa)); return isOptional ? null : opts.get(0); }
                LLMBridge b = smartOk() ? smart : fast;
                List<String> labels = new ArrayList<>();
                for (T t : opts) labels.add(labelOf(t));
                String q = (title == null || title.isEmpty() ? "Choose one" : title) + srcAsk(sa);
                // The engine names the seat by its label: "Choose card(s) to put on top of Seat library" (Thassa's Oracle,
                // runs/loop/g08); the seat is "you" everywhere else.
                if (observer != null) q = q.replace(getPlayer().getName() + "'s library", "your library")
                        .replace(getPlayer().getName() + " library", "your library");
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
        /** The cards picked so far by the effect asking one pick at a time ({@link #chooseSingleCardForZoneChange}). */
        private final List<String> zonePicks = new ArrayList<>();
        private SpellAbility zonePicksFor;
        /** A declined search answered NO at "Cancel Search?" is asked again by the engine, as often as the pair is
         *  repeated (ChangeZoneEffect: i--; continue). The grader's costs.x looped 61 times (issue #312). The NO is
         *  heard once. {@code declinedPick} is the effect whose last pick was declined: only a confirm about that
         *  pick is its "Cancel Search?" (an Optional search's "Search your library?" has the same mode). A NO to it
         *  sets {@code keptSearching}, the effect whose pick comes back; declined again, it stays set, and that
         *  effect's next "Cancel Search?" is YES. Every pick and every confirm consumes both. */
        private SpellAbility declinedPick, keptSearching;

        @Override
        public Card chooseSingleCardForZoneChange(ZoneType destination, List<ZoneType> origin, SpellAbility sa,
                CardCollection fetchList, DelayedReveal delayedReveal, String selectPrompt, boolean isOptional, Player decider) {
            boolean again = isOptional && sa != null && sa == keptSearching;
            Card r = pickForZoneChange(destination, sa, fetchList, delayedReveal, selectPrompt, isOptional, again);
            boolean declined = r == null && fetchList != null && !fetchList.isEmpty();
            declinedPick = declined ? sa : null;
            keptSearching = declined && again ? sa : null;
            return r;
        }

        private Card pickForZoneChange(ZoneType destination, SpellAbility sa, CardCollection fetchList,
                DelayedReveal delayedReveal, String selectPrompt, boolean isOptional, boolean again) {
            clearLast();
            // Nothing to fetch: nothing to choose. A pick this controller is asked to make is the seat's, whoever
            // the engine names as decider (it was Forge's AI's when decider was another player). A delayedReveal
            // is revealed in the engine's order, then the pick is routed like any other (#128).
            if (fetchList == null || fetchList.isEmpty()) return null;
            if (delayedReveal != null) { try { reveal(delayedReveal); } catch (Exception ignore) { } }
            List<Card> opts = new ArrayList<>(fetchList);
            if (opts.size() == 1 && !isOptional) { auto("zone-change(forced-only-legal)" + srcOf(sa)); return opts.get(0); }
            try {
                if (!okFor("zone-change")) { auto("zone-change(no-budget)->first" + srcOf(sa)); return isOptional ? null : opts.get(0); }
                List<String> labels = new ArrayList<>();
                for (Card c : opts) labels.add(labelOf(c));
                // A pick that is one of several for the same effect ("(3 / 5)": Doomsday, Brainstorm) says the cards
                // already picked: runs/loop/g08 T8 searched its archive for "Doomsday pile selected" and re-read an old
                // state to recall its own first two picks.
                java.util.regex.Matcher kn = selectPrompt == null ? null : java.util.regex.Pattern.compile("\\((\\d+) / \\d+\\)").matcher(selectPrompt);
                boolean later = kn != null && kn.find() && !kn.group(1).equals("1") && sa != null && sa == zonePicksFor;
                if (!later) { zonePicks.clear(); zonePicksFor = sa; }
                String q = (selectPrompt == null || selectPrompt.isEmpty() ? "Choose a card" : selectPrompt)
                        + " -> " + destination + srcAsk(sa)
                        // "reanimate your biggest threat" was appended to Brainstorm, Aether Vial and
                        // Stoneforge picks alike; the faithful profile states the choice and nothing else.
                        + (observer != null ? "." : " (pick the card that best advances your game plan — e.g. reanimate your biggest threat).")
                        + (observer != null && later && !zonePicks.isEmpty() ? " Already picked for this effect: " + String.join(", ", zonePicks) + "." : "")
                        + (again ? " You declined this pick and then chose to keep searching; declining again ends the search." : "");
                int c = decideOne(bridgeFor("zone-change"), "zone-change", q, labels, isOptional ? "Decline / none" : null);
                if (c >= 0) zonePicks.add(labels.get(c));
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
                // Nothing to target: nothing to choose. (Forge's AI was asked here, and every branch below that
                // could not set targets fell back to it; no engine fallback now.)
                if (sa == null || !sa.usesTargeting() || sa.getMaxTargets() < 1) return true;

                // A targeting SUB-ABILITY chain used to bail to the Forge AI for the whole chain.
                // Route every targeting ability in the chain through the same menu (ensureTargetsSet
                // walks it), then succeed if the root's own required targets are set (#124).
                for (SpellAbility sub = sa.getSubAbility(); sub != null; sub = sub.getSubAbility()) {
                    if (!sub.usesTargeting()) continue;
                    try {
                        ensureTargetsSet(sa);
                        if (!sa.usesTargeting() || sa.getTargets().size() >= Math.max(1, sa.getMinTargets())) {
                            p("[ROUTER] targets(chain): " + labelOf(sa));
                            return true;
                        }
                    } catch (Exception ignore) { }
                    return firstLegalTargets(sa, "targets(chain,unset)");
                }
                TargetRestrictions tr = sa.getTargetRestrictions();
                if (tr == null) return firstLegalTargets(sa, "targets(no-restrictions)");
                List<forge.game.GameObject> cands = targetCandidates(sa, tr);
                int min = sa.getMinTargets();
                if (cands == null || cands.isEmpty()) {           // truly no target: the ability cannot be targeted
                    auto("targets(no-candidate)->cannot target " + labelOf(sa));
                    return min == 0;
                }
                int pick = 0; // default: the (only, or first) legal candidate — beats an AI decline
                List<String> labels = new ArrayList<>();
                for (forge.game.GameObject ge : cands) labels.add(targetLabel(ge));
                // Multi-target abilities ("up to two target creatures") used to bail to the Forge AI
                // for the whole target set; a DIVIDED spell ("distribute 5 damage", 132 cards) also
                // needs an allocation per target. Both route through the same menu (#124); the
                // division is the model's, with an even split as the deterministic legal fallback.
                if (sa.getMaxTargets() > 1 || sa.isDividedAsYouChoose()) return chooseManyTargets(sa, cands, labels, min);
                // "Up to one" with one candidate is still a choice: that one, or none (rules pilot RP_045).
                if ((cands.size() > 1 || min == 0) && okFor("targets")) {
                    String q = "Choose the TARGET for " + copyOf(sa) + labelOf(sa) + xNote(sa) + " (the spell/ability resolves against whatever you pick).";
                    int c = decideOne(bridgeFor("targets"), "targets", q, labels, min == 0 ? "No target (decline)" : null);
                    // bridge dead -> the same deterministic path as an unparsed reply (decline when
                    // optional, else the first candidate). It used to be Forge's AI.
                    if (c < 0) {
                        // decideOne's -1 also covers "still unparseable after retry", which decideOne does NOT
                        // log itself (only an explicit decline does, flagged via lastDeclineLogged) — never drop it silently.
                        if (min == 0) {
                            if (!lastDeclineLogged) evalDec("targets", cands.size(), -1, tierOf(smartTierFor("targets")), 0, "targets(unparsed)->decline", labels);
                            sa.resetTargets(); return true;
                        }
                        // required + unanswered -> the first legal candidate, a fixed option and VISIBLE to the
                        // gate (no candidate read out of the plan's prose: 2026-10-16 review M2).
                        evalDec("targets", cands.size(), 0, tierOf(smartTierFor("targets")), 0, "targets(unparsed)->first", labels);
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
                if (!added || sa.getTargets().size() < Math.max(1, min)) return firstLegalTargets(sa, "targets(not-added)"); // safety net
                p("[ROUTER] target: " + targetLabel(cands.get(pick)) + " <- " + labelOf(sa)
                        + (cands.size() == 1 ? " (only legal)" : ""));
                return true;
            } catch (Exception e) {
                return firstLegalTargets(sa, "targets(err:" + e.getClass().getSimpleName() + ")");
            }
        }

        /** Several targets for one ability ("up to two target creatures", "any number of target creatures") or a
         *  divided one ("4 damage divided as you choose"): the seat picks the set, and a division is the seat's, with an
         *  even split as the legal fallback (#124). Both targeting paths use it: the play loop and triggers went through
         *  a one-target ask, so Fury's trigger could take one creature, and the default took Fury itself (surface check). */
        private boolean chooseManyTargets(SpellAbility sa, List<forge.game.GameObject> cands, List<String> labels, int min) {
            int max = sa.getMaxTargets();
            boolean divided = sa.isDividedAsYouChoose();
                int lo = Math.max(divided ? 1 : 0, min), hi = Math.min(Math.max(max, 1), cands.size());
                // Each target of a divided spell gets at least 1 (CR 601.2d), so it has at most as many targets as
                // its total: Rolling Thunder for X=2 among three picks fell back to [1 1 0].
                int dividedTotal = 0;
                if (divided) {
                    try { dividedTotal = forge.game.ability.AbilityUtils.calculateAmount(
                            sa.getHostCard(), sa.getParam("DividedAsYouChoose"), sa); } catch (Exception ignore) { }
                    if (dividedTotal > 0) hi = Math.max(lo, Math.min(hi, dividedTotal));
                }
                // Strive adds its cost for each target beyond the first: as many targets as the cast can pay for.
                Cost strive = keywordCost(sa, "Strive:");
                String striveNote = "";
                if (strive != null && sa.isSpell() && sa.getRootAbility() == sa && hi > 1) {
                    int most = 1 + payableKeywordTimes(sa, strive, hi - 1);
                    if (most < hi) p("[ROUTER] targets: strive can be paid for " + most + " target(s), not " + hi);
                    hi = Math.max(lo, most);
                    striveNote = "; each target beyond the first adds strive " + costText(strive.toSimpleString());
                }
                List<Integer> tp;
                if (cands.size() <= Math.max(1, min)) {         // forced: all candidates are needed
                    tp = new ArrayList<>();
                    for (int i = 0; i < cands.size(); i++) tp.add(i);
                    evalDec("targets", cands.size(), tp.size(), "auto", 0, "forced(multi)", labels);
                } else if (okFor("targets")) {
                    tp = decideMany(bridgeFor("targets"), "targets", "Choose TARGETS for " + labelOf(sa)
                            + " (pick " + lo + " to " + hi + " from the list" + striveNote + ").", labels, lo, hi);
                    if (tp == null || tp.size() < lo) {
                        tp = new ArrayList<>();
                        for (int i = 0; i < lo && i < cands.size(); i++) tp.add(i);
                        evalDec("targets", cands.size(), tp.size(), tierOf(smartTierFor("targets")), 0, "targets(multi,unparsed)->first", labels);
                    }
                } else {
                    tp = new ArrayList<>();
                    for (int i = 0; i < lo && i < cands.size(); i++) tp.add(i);
                    evalDec("targets", cands.size(), tp.size(), "auto", 0, "targets(no-model)->first", labels);
                }
                // clearTargets() (not resetTargets()) is what also computes a divided spell's
                // total from its param, so getDividedValue() is non-null below (#124).
                if (divided) sa.clearTargets(); else sa.resetTargets();
                // Read only now: before clearTargets() a fresh divided spell's total is null, so the
                // split was never asked and the targets had no allocation (GoldenChecksTest).
                int divideTotal = divided && sa.getDividedValue() != null ? sa.getDividedValue() : 0;
                if (divided && dividedTotal > 0 && tp.size() > Math.max(lo, dividedTotal))
                    tp = new ArrayList<>(tp.subList(0, Math.max(lo, dividedTotal)));
                for (int i : tp) sa.getTargets().add(cands.get(i));
                if (min > 0 && sa.getTargets().size() < min) return firstLegalTargets(sa, "targets(multi,short)"); // safety net
                StringBuilder split = new StringBuilder();
                if (divided && divideTotal > 0 && !tp.isEmpty()) {
                    List<String> tl = new ArrayList<>();
                    for (int i : tp) tl.add(labels.get(i));
                    List<Integer> alloc = decideAllocation(bridgeFor("targets"), "Split " + divideTotal
                            + " for " + labelOf(sa) + " among the chosen targets, in order, at least 1 each.", tl,
                            divideTotal, EACH_TARGET_AT_LEAST_ONE);
                    if (alloc == null || alloc.size() != tp.size()
                            || alloc.stream().mapToInt(Integer::intValue).sum() != divideTotal)
                        alloc = evenSplit(divideTotal, tp.size());
                    for (int k = 0; k < tp.size(); k++) {
                        sa.addDividedAllocation(cands.get(tp.get(k)), alloc.get(k));
                        split.append(' ').append(alloc.get(k));
                    }
                }
                p("[ROUTER] targets: " + tp.size() + (split.length() > 0 ? " divided [" + split.toString().trim() + "]" : "") + " -> " + labelOf(sa));
                return true;
        }

        /** The deterministic legal default for targets the routed path could not set: the first candidates the
         *  engine accepts, as many as the ability needs (at least one), recorded as a fallback. Returns whether
         *  the ability's targeting is then satisfied. Never Forge's AI. */
        private boolean firstLegalTargets(SpellAbility sa, String why) {
            try {
                for (SpellAbility s = sa; s != null; s = s.getSubAbility()) {
                    if (!s.usesTargeting() || s.getTargets().size() >= Math.max(1, s.getMinTargets())) continue;
                    TargetRestrictions tr = s.getTargetRestrictions();
                    List<forge.game.GameObject> cands = tr == null ? null : targetCandidates(s, tr);
                    if (cands == null) continue;
                    for (forge.game.GameObject ge : cands) {
                        if (s.getTargets().size() >= Math.max(1, s.getMinTargets())) break;
                        if (!s.getTargets().contains(ge) && s.canTarget(ge)) s.getTargets().add(ge);
                    }
                }
                boolean ok = sa.getTargets().size() >= sa.getMinTargets();
                auto(why + "->" + (ok ? "first legal" : "cannot target"));
                return ok;
            } catch (Exception e) {
                auto(why + "->cannot target (" + e.getClass().getSimpleName() + ")");
                return false;
            }
        }

        /** Set legal, LLM-chosen targets on a spell BEFORE handing it to the AI cast path. That path
         *  (ComputerUtil.handlePlayingSpellAbility) does NOT run targeting for ordinary spells — so an
         *  untargeted spell we picked would fail hasLegalTargeting and fizzle ("Couldn't add to stack").
         *  We walk the ability chain and, for each targeting ability with no target yet, pick from the
         *  engine's own canTarget-validated candidates (LLM chooses when there's a real choice). */
        private void ensureTargetsSet(SpellAbility root) { ensureTargetsSet(root, false, false); }

        /** Whether {@code sa}, part of {@code root}, takes no target at the X announced: "X target creatures" at X=0
         *  (Rot-Curse Rakshasa's renew; a target set anyway made the stack refuse the paid play, issue #380). An
         *  unannounced X is not read, and a fixed count of targets (Banefire's one) is kept at any X. */
        static boolean noTargetAtX(SpellAbility root, SpellAbility sa) {
            return root.getXManaCostPaid() != null && sa.getMaxTargets() < 1;
        }

        /** The `simulate` call of a target choice: the play ({@code root}) previewed with {@code sa} aimed at the
         *  numbered candidate (runs/loop/g03 asked for it twice while aiming Wasteland). Null where the seat has no
         *  native preview. */
        private java.util.function.Function<String, String> targetPreview(SpellAbility root, SpellAbility sa,
                                                                          List<forge.game.GameObject> cands) {
            LLMBridge b = bridgeFor("target");
            if (!lookaheadEnabled() || !((MEMORY_TOOLS && NativeTools.enabled() && b.supportsNativeTools()) || answerToolFor(b)))
                return null;
            return argsJson -> {
                com.google.gson.JsonObject a = NativeTools.arguments(argsJson);
                int n = -1;
                try { if (a != null && a.has("option")) n = a.get("option").getAsInt(); } catch (RuntimeException e) { n = -1; }
                if (n < 1 || n > cands.size())
                    return "ERROR: `option` must be the number of a target (1.." + cands.size() + ").";
                if (!(cands.get(n - 1) instanceof forge.game.GameEntity))
                    return "LOOKAHEAD (target " + n + "): (a preview cannot aim at a spell or ability on the stack)";
                Player me = getPlayer();
                String preview = PlayerLookahead.previewLine(me.getGame(), me, java.util.Collections.singletonList(root),
                        me.getGame().getPhaseHandler().getTurn(), null, sa, (forge.game.GameEntity) cands.get(n - 1));
                p("[ROUTER] lookahead target " + n + ": " + preview);
                return "LOOKAHEAD (target " + n + "): " + preview;
            };
        }

        /** Label of the cancel option a target ask offers before its play is committed. */
        static final String CANCEL_LABEL = "CANCEL: do not take this action; go back to the action menu";

        /** False only when {@code cancellable} and the model chose {@link #CANCEL_LABEL}. */
        /** {@code play}: a play from the seat's priority menu, which a target preview can replay on a copy (a
         *  trigger cannot: runs/loop/g04 T5 previewed a cycling trigger's target and got "unavailable"). */
        private boolean ensureTargetsSet(SpellAbility root, boolean cancellable, boolean play) {
            // A proposed target is for the play it was checked with, consumed here whatever path the loop takes: left
            // pending, a later trigger's or copy's target would be set from it unasked (pr-review #342). Off the play
            // path it is dropped, and a target ask with its list follows instead.
            Object proposed = play ? proposedTarget : null;
            proposedTarget = null;
            for (SpellAbility sa = root; sa != null; sa = sa.getSubAbility()) {
                clearLast(); // per-ability: a single-candidate auto-target must not inherit a prior ability's prompt
                try {
                    if (!sa.usesTargeting()) continue;
                    if (sa.getTargets() != null && !sa.getTargets().isEmpty()) continue; // already targeted
                    // A DIVIDED spell whose total is not known yet ({X}: Rolling Thunder, Fireball) must
                    // NOT be pre-targeted here: with no allocation the engine NPEs at resolution. Leave
                    // it to the engine, which asks chooseTargetsFor once X is set (#124). A fixed-total
                    // divided spell (Pyrotechnics, 4) is known now and is handled below.
                    if (sa.isDividedAsYouChoose()) {
                        int dt = 0;
                        try { dt = forge.game.ability.AbilityUtils.calculateAmount(
                                sa.getHostCard(), sa.getParam("DividedAsYouChoose"), sa); } catch (Exception ignore) { }
                        if (dt <= 0) continue;
                    }
                    if (sa.getActivatingPlayer() == null) sa.setActivatingPlayer(getPlayer());
                    if (noTargetAtX(root, sa)) continue;
                    TargetRestrictions tr = sa.getTargetRestrictions();
                    if (tr == null) continue;
                    List<forge.game.GameObject> cands = targetCandidates(sa, tr);
                    if (cands == null || cands.isEmpty()) continue; // no legal target -> let engine cancel/handle
                    // PROPOSE_ACTIONS: the target the accepted proposal named, checked with it (proposalTarget); still
                    // legal after X and costs, it is set without a list being shown.
                    if (proposed != null) {
                        Object want = proposed;
                        proposed = null;
                        if (sa.getMaxTargets() <= 1 && !sa.isDividedAsYouChoose() && cands.contains(want)) {
                            sa.resetTargets();
                            sa.getTargets().add((forge.game.GameObject) want);
                            p("[ROUTER] target (proposed): " + targetLabel((forge.game.GameObject) want));
                            continue;
                        }
                        p("[ROUTER] target (proposed) no longer legal after X and costs -> asked");
                    }
                    if (sa.getMaxTargets() > 1 || sa.isDividedAsYouChoose()) {
                        List<String> many = new ArrayList<>();
                        for (forge.game.GameObject ge : cands) many.add(targetLabel(ge));
                        chooseManyTargets(sa, cands, many, sa.getMinTargets());
                        continue;
                    }
                    int min = Math.max(1, sa.getMinTargets());
                    int pick = 0; // first legal candidate beats leaving it untargeted (which fizzles)
                    String fb = null; // non-null when the pick is a deterministic fallback, not a model choice
                    if ((cands.size() > 1 || sa.getMinTargets() == 0) && okFor("target")) {
                        List<String> labels = new ArrayList<>();
                        for (forge.game.GameObject ge : cands) labels.add(targetLabel(ge));
                        if (cancellable) labels.add(CANCEL_LABEL);
                        // type=null here -> decideOne never logs internally (incl. its decline branch), so
                        // EVERY exit from this call, including the "optional -> no target" one below, must be
                        // logged by us or it vanishes with zero jsonl/log trace (the exact C5 double-failure shape).
                        int c = decideOne(bridgeFor("target"), null, "Choose the TARGET for " + copyOf(sa) + labelOf(sa) + xNote(sa)
                                + " (the spell/ability resolves against whatever you pick).", labels,
                                sa.getMinTargets() == 0 ? "No target" : null, play ? targetPreview(root, sa, cands) : null);
                        if (c == -1 && sa.getMinTargets() == 0) {
                            // The seat's own "No target" is its decision, not a fallback (runs/luna-std mod-g7 T10 read
                            // it as a lost answer); only an answer that could not be used carries a fallback label.
                            evalDec("target", cands.size(), -1, tierOf(smartTierFor("target")), 0,
                                    lastExplicitDecline ? null : "target(unanswered)->no target", labels);
                            continue; // optional -> deliberately no target
                        }
                        if (cancellable && c == cands.size()) {
                            evalDec("target", labels.size(), c, tierOf(smartTierFor("target")), 0, null, labels);
                            return false;
                        }
                        if (c >= 0) pick = c;
                        else fb = "target(unparsed-or-dead)->first"; // the fixed legal option, never the plan's prose
                    } else if (cands.size() > 1) {
                        fb = "target(no-model)->first";                    // floor arm / budget exhausted
                    } else {
                        // single legal candidate — genuinely forced, no model call was ever attempted for it.
                        fb = "forced(only-legal)";
                    }
                    // clearTargets() also computes a divided spell's total (resetTargets does not),
                    // so the allocation below has a non-null getDividedValue() to split (#124).
                    if (sa.isDividedAsYouChoose()) sa.clearTargets(); else sa.resetTargets();
                    sa.getTargets().add(cands.get(pick));
                    for (int i = 0; sa.getTargets().size() < min && i < cands.size(); i++) // top up if it needs >1
                        if (i != pick) sa.getTargets().add(cands.get(i));
                    // A DIVIDED spell needs an allocation per target or the engine reads a null
                    // divided value and NPEs (seen: Pyrotechnics in a random game, 2026-10-14). Split
                    // the engine's fixed total across the chosen targets: the model's allocation, with
                    // an even split as the deterministic legal fallback (#124).
                    if (sa.isDividedAsYouChoose() && sa.getDividedValue() != null && sa.getDividedValue() > 0
                            && !sa.getTargets().isEmpty()) {
                        int dt = sa.getDividedValue();
                        List<String> tl = new ArrayList<>();
                        for (forge.game.GameObject ge : sa.getTargets()) tl.add(targetLabel(ge));
                        List<Integer> alloc = decideAllocation(bridgeFor("target"), "Split " + dt + " for "
                                + labelOf(sa) + " among the chosen targets, in order, at least 1 each.", tl, dt,
                                EACH_TARGET_AT_LEAST_ONE);
                        if (alloc == null || alloc.size() != tl.size()
                                || alloc.stream().mapToInt(Integer::intValue).sum() != dt)
                            alloc = evenSplit(dt, tl.size());
                        int k = 0;
                        for (forge.game.GameObject ge : sa.getTargets()) sa.addDividedAllocation(ge, alloc.get(k++));
                    }
                    List<String> tgtOpts = new ArrayList<>();
                    for (forge.game.GameObject ge : cands) tgtOpts.add(targetLabel(ge));
                    evalDec("target", cands.size(), pick, fb == null ? tierOf(smartTierFor("target")) : "auto", 0, fb, tgtOpts);
                    p("[ROUTER] target: " + targetLabel(cands.get(pick)) + " <- " + labelOf(sa)
                            + (cands.size() == 1 ? " (only legal)" : ""));
                } catch (Exception e) { /* leave targeting to the engine for this ability */ }
            }
            return true;
        }

        @Override
        public CardCollection chooseCardsToDiscardFrom(Player p, SpellAbility sa, CardCollection validCards,
                int min, int max, CardCollectionView visibleToChooser) {
            clearLast();
            if (validCards == null || validCards.isEmpty()) return new CardCollection();   // nothing to discard
            // p != getPlayer() means WE choose what SOMEONE ELSE discards (Thoughtseize / Cabal Therapy
            // "RevealYouChoose") — one of the most load-bearing picks in the matchup. Route it, don't delegate.
            boolean stripping = p != getPlayer();
            List<Card> opts = new ArrayList<>(validCards);
            if (opts.size() <= min) return firstN(opts, min, "discard(forced)"); // whole hand forced
            try {
                if (!smartOk()) return firstN(opts, min, "discard(no-budget)");
                List<String> labels = new ArrayList<>();
                for (Card c : opts) labels.add(cardLabel(c));
                String q = stripping
                        ? "You are choosing which card(s) " + p.getName() + " must DISCARD from their revealed hand"
                          + srcAsk(sa) + ". Strip whatever most threatens your game plan (their removal/disruption/payoff)."
                        : "You must DISCARD from your hand" + srcAsk(sa) + ". Pick which card(s) to discard"
                          + (observer != null ? "." : " (remember your game plan — e.g. discarding a huge creature you can later reanimate is often correct).");
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

        /** Cleanup-step discard to maximum hand size — a real decision the Forge AI used to make
         *  silently. Deterministic fallback: the first N cards. */
        @Override
        public CardCollectionView chooseCardsToDiscardToMaximumHandSize(int numDiscard) {
            clearLast();
            CardCollection hand = new CardCollection(getPlayer().getCardsIn(ZoneType.Hand));
            if (numDiscard <= 0 || hand.isEmpty()) return super.chooseCardsToDiscardToMaximumHandSize(numDiscard);
            List<Card> opts = new ArrayList<>(hand);
            if (opts.size() <= numDiscard) return firstN(opts, numDiscard, "cleanup-discard(forced)");
            try {
                if (!smartOk()) return firstN(opts, numDiscard, "cleanup-discard(no-budget)");
                List<String> labels = new ArrayList<>();
                for (Card c : opts) labels.add(cardLabel(c));
                String q = "Cleanup step: DISCARD down to your maximum hand size — choose " + numDiscard
                        + " card(s) to discard. Keep your best cards and your game plan in mind.";
                List<Integer> picks = decideMany(smart, "cleanup-discard", q, labels, numDiscard, numDiscard);
                if (picks == null) return firstN(opts, numDiscard, "cleanup-discard(unparsed)");
                CardCollection out = new CardCollection();
                StringBuilder log = new StringBuilder();
                for (int i : picks) { out.add(opts.get(i)); if (log.length() > 0) log.append(", "); log.append(opts.get(i).getName()); }
                p("[ROUTER] cleanup-discard: " + log);
                return out;
            } catch (Exception e) {
                return firstN(opts, numDiscard, "cleanup-discard(err)");
            }
        }

        /** Delve: which graveyard cards, and how many, are exiled to pay generic mana. Delve exiles any number up
         *  to the generic part, so fewer is a real choice (Murktide Regent counts the instants and sorceries it
         *  exiles; a card left behind feeds Tarmogoyf). An affordability test is answered with every card that
         *  could pay, without asking; the seat's real payment asks, with at least what its mana cannot cover. */
        @Override
        public CardCollectionView chooseCardsToDelve(int genericAmount, CardCollection grave) {
            clearLast();
            if (grave == null || grave.isEmpty() || genericAmount <= 0) return new CardCollection();
            List<Card> opts = new ArrayList<>(grave);
            int max = Math.min(opts.size(), genericAmount);
            if (PRICING_FOR_SNAPSHOT || !payingForReal || askingConvoke) return new CardCollection(opts.subList(0, max));
            int min = minDelve(max, genericAmount);
            if (min == opts.size()) { auto("delve(forced-all " + min + ")"); return new CardCollection(opts); }
            List<Integer> picks;
            askingConvoke = true;   // the ask's own snapshot tests affordability, which calls this again
            try {
                picks = askMany("delve", "DELVE: you may exile up to " + max + " card(s) from your graveyard, each paying {1} of the"
                        + " generic cost; the rest is paid with mana" + (min > 0 ? " (your mana leaves at least " + min + " to exile)" : "")
                        + ". Choose which.", cardLabels(opts), min, max);
            } finally {
                askingConvoke = false;
            }
            CardCollection out = new CardCollection();
            for (int i : picks) out.add(opts.get(i));
            p("[ROUTER] delve: " + out.size() + " exiled " + out);
            return out;
        }

        /** The fewest cards delve must exile for the rest of the cost to be paid with mana, by the engine's own test payment
         *  (an estimate of the mana available let Become Immense delve none and fail to pay {1}: random gauntlet). */
        private int minDelve(int max, int genericAmount) {
            if (payingCost == null || payingFor == null) return Math.max(0, Math.min(max, genericAmount + nonGenericOfCurrentPayment() - availableMana()));
            boolean was = PRICING_FOR_SNAPSHOT;
            PRICING_FOR_SNAPSHOT = true;
            try {
                for (int n = 0; n < max; n++) {
                    forge.game.mana.ManaCostBeingPaid rest = new forge.game.mana.ManaCostBeingPaid(payingCost);
                    rest.decreaseGenericMana(n);
                    if (seatCanPay(rest, payingFor, getPlayer(), false)) return n;
                }
                return max;
            } catch (Exception e) {
                return Math.max(0, Math.min(max, genericAmount + nonGenericOfCurrentPayment() - availableMana()));
            } finally { PRICING_FOR_SNAPSHOT = was; }
        }

        /** The coloured part of the spell being paid (set by payManaCost); 0 when unknown. */
        private int nonGenericOfCurrentPayment() {
            try {
                forge.card.mana.ManaCost c = payingCost;
                return c == null ? 0 : c.getCMC() - c.getGenericCost();
            } catch (Exception e) { return 0; }
        }

        private int availableMana() {
            try { return forge.ai.ComputerUtilMana.getAvailableManaEstimate(getPlayer(), false); }
            catch (Exception e) { return 0; }
        }

        /** "Unless" costs — whether to pay for Daze / Force Spike / Spell Pierce / Mana Tithe. The
         *  YES/NO is the model's (it was Forge AI's); the payment mechanics stay the engine's, so an
         *  unaffordable or declined payment returns false rather than risking a broken cast. */
        @Override
        public boolean payCostToPreventEffect(Cost cost, SpellAbility sa, boolean alreadyPaid, FCollectionView<Player> allPayers) {
            clearLast();
            if (cost == null) return false;
            try {
                // An option the seat cannot take is not offered: with nothing to pay with, declining is forced
                // (runs/luna-v8/g1 offered "Pay {2}" for Spell Pierce and "Pay {R}{R}" for Chain Lightning's copy
                // with no mana up, and the model had to point it out).
                if (!legallyPayable(cost, sa, getPlayer(), true)) {
                    evalDec("prevent-effect", 1, 0, "auto", 0, "forced(cannot-pay)->decline " + fit(cost.toSimpleString()), null);
                    p("[ROUTER] prevent-effect: cannot pay " + fit(cost.toSimpleString()) + " -> decline (forced)");
                    return false;
                }
                if (okFor("prevent-effect")) {
                    // Riot's and fabricate's choice is worded as a cost "Put a +1/+1 counter on CARDNAME".
                    String desc = fit(named(costText(cost.toSimpleString()), sa == null ? null : sa.getHostCard()));
                    List<String> labels = Lists.newArrayList("Pay " + desc, "Decline (let it resolve)");
                    String q = "Pay " + desc + " to prevent " + (sa == null ? "the spell" : "the spell " + srcOf(sa))
                            + " from affecting you? Say 1 to pay or 2 to decline.";
                    if (observer != null && sa != null) {
                        // UnlessSwitched: paying is what makes the effect happen (Chain Lightning's copy);
                        // runs/loop/g01 was asked it as "pay to prevent" and guessed it could not pay.
                        boolean pays = sa.hasParam("UnlessSwitched");
                        labels = Lists.newArrayList("Pay " + desc, pays ? "Decline (it does not happen)" : "Decline (let it resolve)");
                        q = (pays ? "Optional payment: pay " + desc + " to make this happen: "
                                  : "Pay " + desc + " to prevent this: ") + seatText(sa.getStackDescription())
                            + srcAsk(sa) + " You can pay it right now.";
                    }
                    int c = decideOne(bridgeFor("prevent-effect"), "prevent-effect", q, labels, null);
                    if (c == 0) {
                        if (!legallyPayable(cost, sa, getPlayer(), true)) { auto("prevent-effect(model)->cannot-pay"); return false; }
                        CostPayment pay = new CostPayment(cost, sa);
                        // The seat's cost decisions: Forge AI chose what an "unless" payment discarded or
                        // sacrificed (Reality Smasher's "unless you discard a card").
                        boolean paid = pay.payComputerCosts(new LlmCostDecision(getPlayer(), sa, true));
                        p("[ROUTER] prevent-effect: paid " + desc + " -> " + paid);
                        return paid;
                    }
                    // decideOne wrote the model's row; a second (auto) row for the same answer double-counted it.
                    if (c == 1) { p("[ROUTER] prevent-effect: declined " + desc); return false; }
                }
            } catch (Exception e) { /* deterministic fallback below */ }
            auto("prevent-effect(default)->decline");
            return false;
        }

        /** The optional additional costs the seat pays on the play it chose (kicker, buyback, entwine, bargain), first of
         *  the values a cast announces (CR 601.2b), as the human path asks them (PlaySpellAbility, then
         *  GameActionUtil.addOptionalCosts). Forge's AI asks them while listing its options
         *  (ComputerUtilAbility.getOriginalAndAltCostAbilities), which this menu does not use, so no kicker, buyback
         *  or entwine was ever paid (surface check). A cost already on the play (the retrace it was offered with) is
         *  not asked again. */
        private SpellAbility withOptionalCosts(SpellAbility sa) {
            try {
                if (sa.getActivatingPlayer() == null) sa.setActivatingPlayer(getPlayer());
                // Listing them clears the spell's reduced pips; a play offered with a Defiler's payment keeps its own.
                List<String> pips = new ArrayList<>(sa.getPipsToReduce());
                List<OptionalCostValue> list = new ArrayList<>(GameActionUtil.getOptionalCostValues(sa));
                sa.getPipsToReduce().addAll(pips);
                List<forge.game.spellability.OptionalCost> on = Lists.newArrayList(sa.getOptionalCosts());
                list.removeIf(v -> on.contains(v.getType()));
                if (list.isEmpty()) return sa;
                List<OptionalCostValue> picked = chooseOptionalCosts(sa, list);
                return picked == null || picked.isEmpty() ? sa : withOptional(sa, picked);
            } catch (Exception e) { p("[ROUTER] optional-costs: " + e + " -> none"); return sa; }
        }

        /** Whether {@code sa} with the picked optional costs together can be paid now. An unpriceable
         *  set is treated as unpayable: a kept set that fails at payment spends the seat's priority
         *  window for nothing, so the offer filter's per-value catch is the direction to mirror - the
         *  re-ask runs, the worst case is the always-legal decline-all fallback. */
        private boolean payableWith(SpellAbility sa, List<OptionalCostValue> offer, List<Integer> picks) {
            try {
                List<OptionalCostValue> set = new ArrayList<>();
                for (int i : picks) set.add(offer.get(i));
                SpellAbility with = withOptional(sa, set);
                with.setActivatingPlayer(getPlayer());
                return legallyPayable(with, getPlayer(), false);
            } catch (Exception e) { p("[ROUTER] optional-costs: " + e + " -> decline"); return false; }
        }

        /** {@code sa} with the optional costs {@code set} added, as GameActionUtil.addOptionalCosts makes it, without
         *  touching {@code sa}. A copy shares its pips-to-reduce list with the original (a memoized supplier the
         *  shallow clone keeps), so pricing a Defiler's "pay 2 life, {W} less" left {W} off the real spell: the
         *  plain Savannah Lions was offered and cast with no land and no life paid (surface check). The copy gets
         *  its own list and the original's is put back. */
        static SpellAbility withOptional(SpellAbility sa, List<OptionalCostValue> set) {
            List<String> before = new ArrayList<>(sa.getPipsToReduce());
            SpellAbility with = GameActionUtil.addOptionalCosts(sa, set);
            if (with != sa) {
                List<String> own = new ArrayList<>(with.getPipsToReduce());
                try {
                    java.lang.reflect.Field f = SpellAbility.class.getDeclaredField("pipsToReduce");
                    f.setAccessible(true);
                    f.set(with, com.google.common.base.Suppliers.memoize(() -> own));
                } catch (ReflectiveOperationException | RuntimeException e) {
                    throw new IllegalStateException("optional costs cannot be priced apart from the spell: " + e, e);
                }
            }
            sa.getPipsToReduce().clear();
            sa.getPipsToReduce().addAll(before);
            return with;
        }

        /** Optional additional costs — kicker / buyback / entwine. The model chooses which to pay;
         *  only costs the seat can actually afford are offered, so a chosen cost cannot fizzle the
         *  cast. Deterministic fallback: decline all (always legal). */
        @Override
        public List<OptionalCostValue> chooseOptionalCosts(SpellAbility chosen, List<OptionalCostValue> optionalCostValues) {
            clearLast();
            if (optionalCostValues == null || optionalCostValues.isEmpty())
                return super.chooseOptionalCosts(chosen, optionalCostValues);
            try {
                if (smartOk()) {
                    List<OptionalCostValue> offer = new ArrayList<>();
                    for (OptionalCostValue v : optionalCostValues) {
                        try {
                            // The whole cast with this cost must stay payable: the cost alone was checked, so Rude
                            // Awakening's entwine was offered with 7 lands for 8 mana and the cast failed (surface check).
                            if (v == null || v.getCost() == null) { offer.add(v); continue; }
                            SpellAbility with = withOptional(chosen, Lists.newArrayList(v));
                            with.setActivatingPlayer(getPlayer());
                            if (legallyPayable(with, getPlayer(), false)) offer.add(v);
                        } catch (Exception ignore) { }
                    }
                    if (offer.isEmpty()) { auto("optional-costs(none payable)->decline-all"); return new ArrayList<>(); }
                    if (!offer.isEmpty()) {
                        List<String> labels = new ArrayList<>();
                        for (OptionalCostValue v : offer) labels.add(fit(named(v.toString(), chosen.getHostCard())));
                        String q = "This spell offers optional additional costs (kicker/buyback/entwine)" + srcAsk(chosen)
                                + ". Pick the ones worth paying for your plan; PICKS: NONE declines them all.";
                        List<Integer> picks = decideMany(smart, "optional-costs", q, labels, 0, offer.size());
                        // Each cost was offered as payable with the spell alone; several together can still be more
                        // than the seat can pay (Ana Battlemage's two kickers, 8 mana from 6 lands, and the cast failed).
                        for (int attempt = 0; picks != null && picks.size() > 1 && !payableWith(chosen, offer, picks); attempt++) {
                            if (attempt == 1) { auto("optional-costs(together unpayable)->decline-all"); return new ArrayList<>(); }
                            picks = decideMany(smart, "optional-costs", q + " That set did not come back payable;"
                                    + " pick a set you can pay.", labels, 0, offer.size());
                        }
                        if (picks != null) {
                            List<OptionalCostValue> out = new ArrayList<>();
                            for (int i : picks) out.add(offer.get(i));
                            StringBuilder log = new StringBuilder();
                            for (OptionalCostValue v : out) { if (log.length() > 0) log.append(", "); log.append(oneLine(v.toString())); }
                            p("[ROUTER] optional-costs: " + (log.length() == 0 ? "(none)" : log));
                            return out;
                        }
                    }
                }
            } catch (Exception e) { /* deterministic fallback below */ }
            auto("optional-costs(default)->decline-all");
            return new ArrayList<>();
        }

        /** Plays the seat chose that did not happen, from the play menu or cast through an effect: forbidden by a static,
         *  no legal modes, unpayable, refused by the stack for want of a target, a land drop the engine refused, or a pick
         *  lost to an error in the play menu. A cancel is the seat's own choice and is not counted. A written line
         *  (ScriptBridge) is void when any of its plays did not happen. */
        private int playsNotMade = 0;
        int playsNotMade() { return playsNotMade; }

        /** The play path for a cast or activation. The inherited implementation hands the ability to
         *  {@code ComputerUtil.handlePlayingSpellAbility}, which pays EVERY cost with a hard-coded
         *  {@code new AiCostDecision(...)} and never consults {@link #getCostDecisionMaker} — so Forge's
         *  AI silently chose which creature Goblin Bombardment or a Cabal Therapy flashback sacrificed
         *  (it takes the "worst" one: with Ajani, Nacatl Pariah and another Cat it sacrifices Ajani).
         *  This runs the same steps as the engine routine at the pinned commit and pays with
         *  {@link LlmCostDecision}, which routes a choosable sacrifice to the model and delegates every
         *  other cost part to the engine's own decision. The cost is read AFTER optional keyword costs
         *  are added, so a sacrifice that arrives that way (Casualty) is covered too. Only an ability
         *  that defers targeting to another player stays on the inherited path.
         *  It returns true on every branch, also when nothing was played, as Forge's AI controller does. The
         *  engine's priority loop asks the seat again either way; true restarts the round of passes from the seat
         *  (PhaseHandler), so false would be an engine-facing change. Whether the play was made is read from
         *  {@link #playsNotMade()} and the game, never from this value (issue #380; docs/STANDARDS.md section 5).
         *  RE-DIFF against {@code handlePlayingSpellAbility} on every engine re-pin (ENGINE-PIN.md). */
        @Override
        public boolean playChosenSpellAbility(SpellAbility sa) {
            boolean ours = false;
            try {
                ours = !sa.isLandAbility() && sa.getHostCard() != null;
                for (SpellAbility part = sa; ours && part != null; part = part.getSubAbility())
                    if (part.hasParam("TargetingPlayer")) ours = false;
            } catch (Exception e) { ours = false; }
            boolean modesChosen = sa == modesChosenFor;
            modesChosenFor = null;
            if (!ours) {
                forge.game.zone.Zone from = sa.getHostCard() == null ? null : sa.getHostCard().getZone();
                int stackBefore = getGame().getStack().size();
                boolean played = super.playChosenSpellAbility(sa);
                // The inherited routine returns true whatever happened: read what it did from the game.
                boolean happened = sa.isLandAbility() ? sa.getHostCard() != null && sa.getHostCard().getZone() != from
                        : getGame().getStack().size() > stackBefore;
                if (happened) playSeen(sa, from); else playsNotMade++;
                return played;
            }

            String chosenKey = failedPlayKey(sa);
            final Player ai = getPlayer();
            final Card source = sa.getHostCard();
            final Game game = source.getGame();
            final forge.game.zone.Zone hz = source.isCopiedSpell() ? null : source.getZone();
            final int hzPos = hz == null ? 0 : hz.getCards().indexOf(source);
            // What a play that does not happen is rolled back to: the zone a cast left, none for an ability (the human
            // path's rollback, PlaySpellAbility.playAbility).
            final forge.game.zone.Zone castFrom = sa.isSpell() ? hz : null;
            source.setSplitStateToPlayAbility(sa);
            if (sa.isSpell() && !source.isCopiedSpell()) {
                sa = forge.game.ability.AbilityUtils.addSpliceEffects(sa);
                // A spliced spell is a new copy with the spliced effects at its end: its targets are the seat's again,
                // all of them (the copied routine asked Forge's AI whether to go on, and dropped the play when it said no).
                if (hasSplices(sa)) {
                    sa.resetTargets();
                    ensureTargetsSet(sa);
                    // A failure of the spliced spell is that set's, not the card's: the plain cast stays offered (#392).
                    chosenKey = spliceKey(chosenKey, sa.getSplicedCards());
                }
                sa.setHostCard(game.getAction().moveToStack(source, sa));
            }
            if (!sa.isCopied()) { sa.resetPaidHash(); sa.setPaidLife(0); }
            sa = forge.game.GameActionUtil.addExtraKeywordCost(sa);
            // As the human path does before paying (PlaySpellAbility.playAbility): an activated ability's own object keeps
            // the mana of its last activation (the stack took a copy), which a rollback would otherwise refund again.
            sa.clearManaPaid();
            sa.getPayingManaAbilities().clear();
            // The engine routine's own check, once the card is on the stack (ComputerUtil.handlePlayingSpellAbility):
            // a static that forbids the play stops it here even if a menu missed it (rules pilot RP_207).
            if (!sa.checkRestrictions(ai)) {
                p("[ROUTER] play: " + labelOf(sa) + " is forbidden by a static -> nothing was played");
                failedInWindow.add(chosenKey);
                playsNotMade++;
                forge.game.GameActionUtil.rollbackAbility(sa, castFrom, hzPos, new CostPayment(sa.getPayCosts(), sa), source);
                return true;
            }
            if (sa.getApi() == ApiType.Charm && !modesChosen && !forge.game.ability.effects.CharmEffect.makeChoices(sa)) {
                playsNotMade++;
                return true;
            }

            final boolean refreeze = game.getStack().isFrozen();
            game.getStack().freezeStack(sa);
            final CostPayment pay = new CostPayment(sa.getPayCosts(), sa);
            // Every cost part is decided before any is paid, so a cost ask can still cancel the play,
            // as a target ask can (runs/loop/g06, g07); the menu is asked again, at most twice per window.
            final LlmCostDecision decision = new LlmCostDecision(ai, sa, false);
            if (observer != null && rnd == null && cancels < 2) decision.offerCancel();
            costCancelled = false;
            if (pay.payComputerCosts(decision)) {
                // The stack refuses a play with no legal target left ("Couldn't add to stack, failed to target").
                boolean reaches = sa.isCopied() || sa.isManaAbility() || game.getStack().hasLegalTargeting(sa);
                game.getStack().addAndUnfreeze(sa);
                if (!reaches) {
                    p("[ROUTER] play: " + labelOf(sa) + " paid but has no legal target -> the stack refused it");
                    failedInWindow.add(chosenKey);
                    playsNotMade++;
                    forge.game.GameActionUtil.rollbackAbility(sa, castFrom, hzPos, pay, source);
                    return true;
                }
                playSeen(sa, hz);
                if (hasSplices(sa))
                    game.getAction().reveal(sa.getSplicedCards(), ai, true, "Computer reveals spliced cards from ");
                return true;
            }
            // A play that did not happen must never read as "casts X" in the log (the audited game
            // recorded a failed activation as a cast).
            dropModes(sa);   // cancelled or unpaid, the play did not happen; its modes go with it
            if (decision.cancelled()) {
                cancels++;
                cancelNote = "you chose " + verbOf(sa) + " " + labelOf(sa) + " and then cancelled it when asked to pay its cost. Choose again.";
                actionIntent = null;
                p("[ROUTER] play: cancelled " + labelOf(sa) + " at its cost -> re-ask");
            } else {
                p("[ROUTER] play: FAILED to pay for " + labelOf(sa) + " -> nothing was played");
                failedInWindow.add(chosenKey);
                playsNotMade++;
                // The seat is told, as after a cancel: a play that silently did not happen read as made (#392). New seat
                // text, so not on a legacy profile (docs/STANDARDS.md section 4: legacy stays byte-identical).
                if (observer != null) {
                    boolean spliced = hasSplices(sa);
                    cancelNote = "you chose " + verbOf(sa) + " " + labelOf(sa)
                            + (spliced ? " with " + names(sa.getSplicedCards(), false, 10) + " spliced onto it" : "")
                            + " and its cost could not be paid, so nothing was played." + (spliced
                            ? " The spell with no splice, or with a set you can pay for, is still on the menu."
                            : " It is not offered again in this priority window.");
                }
            }
            costCancelled = false;
            sa.setSkip(true);
            // The engine's own rollback, not the engine routine's recovery, which moved the card back only when the zone it
            // was cast FROM was the Stack, so a failed cast from hand stayed in the Stack zone with nothing on the stack.
            // The rollback refunds the mana paid and clears the freeze but not the play it was for, so the stack is
            // unfrozen here as PlaySpellAbility.playAbility does; else the next play would stay frozen.
            forge.game.GameActionUtil.rollbackAbility(sa, castFrom, hzPos, pay, source);
            if (!refreeze) game.getStack().unfreezeStack();
            return true;
        }

        /** A modal play that did not happen keeps no modes, as Forge's own rollback does (GameActionUtil.rollbackAbility):
         *  a spree spell cancelled at its target kept both modes' chain with no targets, and the next preview of it
         *  failed to target (keyword gauntlet with lookups on, Explosive Derailment). */
        private void dropModes(SpellAbility sa) {
            if (sa != null && sa.getApi() == ApiType.Charm) {
                sa.setSubAbility(null);
                if (modesChosenFor == sa) modesChosenFor = null;
            }
        }

        /** G7: the card-choosing NON-mana costs (which permanent to sacrifice) go to the model via
         *  {@link LlmCostDecision}; every other cost type stays the engine's. */
        @Override
        public forge.game.cost.CostDecisionMakerBase getCostDecisionMaker(Player player, SpellAbility ability, boolean effect, String prompt) {
            try {
                if (player == getPlayer()) return new LlmCostDecision(player, ability, effect);
            } catch (Exception ignore) { }
            return super.getCostDecisionMaker(player, ability, effect, prompt);
        }

        /** Permanents an ability's tap part has named, held while that ability's mana is paid (#111).
         *  Identity-keyed: the same SpellAbility object reaches both the cost decision and payManaCost. */
        private final Map<SpellAbility, List<Card>> tapPartHolds = new java.util.IdentityHashMap<>();

        void holdForTapPart(SpellAbility sa, Iterable<Card> cards) {
            List<Card> l = tapPartHolds.computeIfAbsent(sa, k -> new ArrayList<>());
            for (Card c : cards) l.add(c);
        }

        void clearTapPartHolds(SpellAbility sa) { tapPartHolds.remove(sa); }

        /** #105: a MANA ability's tap-type cost (Heritage Druid: "Tap three untapped Elves you control")
         *  is paid inside {@code ComputerUtilMana} with a hard-coded {@code AiCostDecision}, so Forge's AI
         *  chose which Elves to tap. When the engine's own test payment of this cost plans to use such an
         *  ability, or, when that plan is null, the first activation {@code tapTypeStep} finds (#367), the
         *  seat activates it first through {@link LlmCostDecision}, keeping out the other sources that
         *  plan taps; the engine then pays from the floating mana, which it always spends first. A source
         *  that also chooses a color stays the engine's, said as a fallback, as is any such source the
         *  engine's real payment uses without the seat having activated it. A mana
         *  ability's own mana cost (a filter paid mid-payment) is left alone: a test payment there
         *  would clear what the outer payment has set aside. */
        @Override
        public boolean payManaCost(forge.card.mana.ManaCost toPay, forge.game.cost.CostPartMana costPartMana,
                SpellAbility sa, String prompt, forge.game.mana.ManaConversionMatrix matrix, boolean effect) {
            Set<Card> said = new HashSet<>();
            int[] lifeDue = {0};   // Phyrexian symbols the seat pays with life, paid once the mana is (CR 601.2h)
            try {
                toPay = phyrexianByLife(toPay, sa, effect, lifeDue);
            } catch (Exception e) {
                p("[ROUTER] mana: phyrexian " + e + " -> engine");
            }
            try {
                if (!sa.isManaAbility()) prepayTapTypeManaAbilities(toPay, sa, effect, said);
            } catch (Exception e) {
                costDecisionFellBack("mana-tap-cost", e.getClass().getSimpleName());
            }
            // Mana already floating (the seat's prepay, or an earlier payment's leftover) was made by a
            // choice already accounted for; only mana the engine's payment makes is new.
            Set<forge.game.mana.Mana> floating = java.util.Collections.newSetFromMap(new java.util.IdentityHashMap<>());
            for (forge.game.mana.Mana m : getPlayer().getManaPool()) floating.add(m);
            List<Card> held = new ArrayList<>(sa.isManaAbility() ? java.util.Collections.emptyList() : holdManaSources(toPay, sa, effect));
            // What this ability's tap part already named stays untapped too (LlmCostDecision.reserve, #111).
            List<Card> tapPart = tapPartHolds.remove(sa);
            if (tapPart != null) {
                for (Card c : tapPart) forge.ai.AiCardMemory.rememberCard(getPlayer(), c, forge.ai.AiCardMemory.MemorySet.HELD_MANA_SOURCES_FOR_NEXT_SPELL);
                held.addAll(tapPart);
            }
            boolean paid;
            boolean wasPaying = payingForReal;
            forge.card.mana.ManaCost wasCost = payingCost;
            SpellAbility wasFor = payingFor;
            payingForReal = true;
            payingCost = toPay;
            payingFor = sa;
            try {
                paid = super.payManaCost(toPay, costPartMana, sa, prompt, matrix, effect);
                // Both the planner and the engine's test payment can call a keep payable that the real payment, which
                // orders sources greedily, then fails: Jegantha's {R} with a Mountain kept for {1} (keyword gauntlet). The
                // cast is legal, so a preference about which source stays untapped never fails it: the engine refunded
                // the spent mana to the pool, and it pays once more with nothing held, recorded.
                if (!paid && !held.isEmpty() && tapPart == null) {
                    for (Card c : held) forge.ai.AiCardMemory.forgetCard(getPlayer(), c, forge.ai.AiCardMemory.MemorySet.HELD_MANA_SOURCES_FOR_NEXT_SPELL);
                    auto("mana-sources(the engine could not pay around the kept sources)->paid without keeping " + held.size());
                    held.clear();
                    sa.setSkip(false);
                    paid = super.payManaCost(toPay, costPartMana, sa, prompt, matrix, effect);
                }
                // The life goes last: paid first, a source that costs life (Horizon Canopy) could then be refused by the
                // engine's payment and the cast fail with the life gone, unrefunded (pr-review of #277). The offer's test
                // again, as a source's own life cost may have left too little; then the mana goes back to the pool.
                if (paid && lifeDue[0] > 0) {
                    int n = lifeDue[0];
                    Player me = getPlayer();
                    if (lifePayable(me, n, effect, sa)) {
                        me.payLife(2 * n, sa, effect);
                        for (int i = 0; i < n; i++) sa.setSpendPhyrexianMana(true);   // once per symbol, as the engine does
                        p("[ROUTER] mana: " + n + " Phyrexian paid with " + (2 * n) + " life for " + labelOf(sa));
                    } else {
                        new forge.game.mana.ManaRefundService(sa).refundManaPaid();
                        p("[ROUTER] mana: " + (2 * n) + " life for the Phyrexian symbols can no longer be paid at "
                                + me.getLife() + " life -> the mana is refunded, nothing is paid");
                        paid = false;
                    }
                }
            } finally {
                payingForReal = wasPaying;
                payingCost = wasCost;
                payingFor = wasFor;
                for (Card c : held) forge.ai.AiCardMemory.forgetCard(getPlayer(), c, forge.ai.AiCardMemory.MemorySet.HELD_MANA_SOURCES_FOR_NEXT_SPELL);
            }
            try {
                for (forge.game.mana.Mana m : sa.getPayingMana()) {
                    Card host = m.getSourceCard();
                    // said.add is false for a host already reported.
                    if (host != null && !floating.contains(m)
                            && host.getManaAbilities().anyMatch(PlayerControllerLLM::hasTapTypeCost) && said.add(host))
                        costDecisionFellBack("mana-tap-cost", host.getName() + " was activated by the engine's payment");
                }
            } catch (Exception e) {
                costDecisionFellBack("mana-tap-cost", e.getClass().getSimpleName());
            }
            return paid;
        }

        /** 2n life the seat may pay for Phyrexian symbols: payable, and never to 0 unless it cannot lose for it. */
        private static boolean lifePayable(Player me, int n, boolean effect, SpellAbility sa) {
            return me.canPayLife(2 * n, effect, sa) && (me.getLife() > 2 * n || me.cantLoseForZeroOrLessLife());
        }

        /** Phyrexian mana is the seat's: how many Phyrexian symbols it pays with 2 life each. Forge's
         *  payment pays one with mana whenever a source can and with life only when none can
         *  (ComputerUtilMana.payManaCost, the saPayment == null branch), so it decided whether Dismember
         *  or Gitaxian Probe cost life. The seat chooses the split here and hands the engine the rest, its
         *  Phyrexian symbols as plain colours. Only splits the seat can pay are offered (the life, and the
         *  rest with mana by the engine's own test payment); with one, nothing is asked. Returns the cost the
         *  engine still pays; the life goes in {@code lifeDue}, which payManaCost pays once the mana is. */
        private forge.card.mana.ManaCost phyrexianByLife(forge.card.mana.ManaCost toPay, SpellAbility sa, boolean effect,
                                                         int[] lifeDue) {
            if (toPay == null || !toPay.hasPhyrexian() || toPay.countX() > 0 || sa == null || sa.isManaAbility()
                    || "floor".equals(mode) || (!smartOk() && !fastOk())) return toPay;
            final Player me = getPlayer();
            final int k = toPay.getPhyrexianCount();
            List<Integer> lifeFor = new ArrayList<>();
            List<String> labels = new ArrayList<>();
            for (int n = 0; n <= k; n++) {
                if (n > 0 && !lifePayable(me, n, effect, sa)) break;
                // What is left after n life is paid by the engine with mana: a split it cannot finish is not offered
                // (Dismember with no land would be a choice that can only fail).
                forge.card.mana.ManaCost rest = withPhyrexian(toPay, n, true);
                if (rest.getCMC() > 0 && ComputerUtilMana.getManaSourcesToPayCost(neededMana(rest, sa, effect), sa, me, effect) == null) continue;
                lifeFor.add(n);
                labels.add(n == 0 ? "pay every Phyrexian symbol with mana (no life)"
                        : "pay " + n + " Phyrexian symbol" + (n == 1 ? "" : "s") + " with " + (2 * n) + " life"
                          + (n < k ? ", the rest with mana" : ""));
            }
            if (lifeFor.isEmpty()) return toPay;
            int n;
            if (lifeFor.size() == 1) {
                // One way to pay. With mana the engine's payment takes it; with life the seat takes it (paid after the mana,
                // in payManaCost), because the engine's payment is Forge's AI and obeys AIPhyrexianPayment ("Never" on
                // Gitaxian Probe, hard draft h_0).
                if (lifeFor.get(0) == 0) return toPay;
                n = lifeFor.get(0);
                auto("phyrexian(forced: payable only with life)->" + (2 * n) + " life" + srcOf(sa));
            } else {
                String what = sa.getHostCard() == null ? "this" : verbOf(sa) + " " + labelOf(sa);
                int c = decideOne(smartOk() ? smart : fast, "phyrexian", "MANA: you are paying " + toPay + " for " + what
                        + ". Each Phyrexian symbol is paid with one mana of its colour or with 2 life. You have "
                        + me.getLife() + " life. How do you pay?", labels, null);
                if (c < 0 || c >= lifeFor.size()) { auto("phyrexian(unparsed)->engine"); return toPay; }
                n = lifeFor.get(c);
            }
            lifeDue[0] = n;   // payManaCost pays it after the mana
            // The rest as plain colours, the cost the split was priced at: the engine cannot pay more of it with life.
            return withPhyrexian(toPay, n, true);
        }

        /** {@code cost} without its first {@code byLife} Phyrexian symbols; with {@code restAsMana} the
         *  others become their plain colour, so a payment of it cannot fall back to life. */
        static forge.card.mana.ManaCost withPhyrexian(forge.card.mana.ManaCost cost, int byLife, boolean restAsMana) {
            StringBuilder sb = new StringBuilder();
            if (cost.getGenericCost() > 0) sb.append(cost.getGenericCost());
            int skipped = 0;
            for (forge.card.mana.ManaCostShard s : cost) {
                String t = s.toShortString();
                if (s.isPhyrexian()) {
                    if (skipped < byLife) { skipped++; continue; }
                    if (restAsMana) t = t.replace("/P", "");
                }
                sb.append(' ').append(t);
            }
            String str = sb.toString().trim();
            return new forge.card.mana.ManaCost(str.isEmpty() ? "0" : str);
        }

        /** Which mana sources stay untapped is the seat's choice: Forge's payment picked the lands, so
         *  it decided whether a Volcanic Island or a Mountain paid for a red spell, and with it whether
         *  blue stayed up for Daze. Asked only when the untapped sources outnumber what the payment needs
         *  and are not all alike. The picks are held (HELD_MANA_SOURCES_FOR_NEXT_SPELL, which the engine's
         *  payment always respects) for this payment only; if the engine cannot pay around them, they are
         *  released and that is logged. Returns the cards held, to be released after the payment. */
        private List<Card> holdManaSources(forge.card.mana.ManaCost toPay, SpellAbility sa, boolean effect) {
            // Convoke and improvise are chosen later in this payment. The engine's test payment counts a creature as a
            // convoker and as a mana source at once (Llanowar Elves), so a land looked spare that was not: Angel of
            // Salvation kept a Plains and failed to pay (keyword gauntlet). While this question is priced, the
            // permanents that may convoke are not counted as mana; the seat's convoke choice still decides later.
            List<Card> convokers = new ArrayList<>();
            if (sa != null && sa.isSpell() && sa.getHostCard() != null) {
                boolean convoke = sa.getHostCard().hasKeyword(Keyword.CONVOKE), improvise = sa.getHostCard().hasKeyword(Keyword.IMPROVISE);
                if (convoke || improvise)
                    for (Card c : getPlayer().getCardsIn(ZoneType.Battlefield))
                        if (!c.isTapped() && ((convoke && c.isCreature()) || (improvise && c.isArtifact()))) convokers.add(c);
            }
            for (Card c : convokers) forge.ai.AiCardMemory.rememberCard(getPlayer(), c, forge.ai.AiCardMemory.MemorySet.HELD_MANA_SOURCES_FOR_NEXT_SPELL);
            try { return holdManaSourcesPriced(toPay, sa, effect, convokers); }
            finally { for (Card c : convokers) forge.ai.AiCardMemory.forgetCard(getPlayer(), c, forge.ai.AiCardMemory.MemorySet.HELD_MANA_SOURCES_FOR_NEXT_SPELL); }
        }

        private List<Card> holdManaSourcesPriced(forge.card.mana.ManaCost toPay, SpellAbility sa, boolean effect, List<Card> convokers) {
            List<Card> none = java.util.Collections.emptyList();
            try {
                if (sa == null || "floor".equals(mode) || (!smartOk() && !fastOk())) return none;
                final Player me = getPlayer();
                CardCollection plan = ComputerUtilMana.getManaSourcesToPayCost(neededMana(toPay, sa, effect), sa, me, effect);
                if (plan == null || plan.isEmpty()) return none;
                List<Card> sources = new ArrayList<>();
                for (Card c : me.getCardsIn(ZoneType.Battlefield)) {
                    if (c == sa.getHostCard() || convokers.contains(c)) continue;
                    // A source the engine can tap for mana now (untapped, not summoning sick), as manaSources reads it.
                    if (c.getManaAbilities().anyMatch(ma -> ma.getPayCosts() != null && ma.getPayCosts().hasTapCost()
                            && canActivateNow(ma, me))) sources.add(c);
                }
                int spare = sources.size() - plan.size();
                java.util.Set<String> kinds = new java.util.HashSet<>();
                for (Card c : sources) kinds.add(c.getName());
                List<Integer> said = pendingKeepFor == sa ? pendingKeep : null;
                pendingKeep = null; pendingKeepFor = null;
                if (spare <= 0 || kinds.size() < 2) return none;
                if (said != null && observer != null) {
                    // The seat said which sources to keep when it chose this play: no second question.
                    List<Card> held = new ArrayList<>();
                    for (int h : said) {
                        Card c = observer.currentCard(h);
                        if (c != null && sources.contains(c) && held.size() < spare) held.add(c);
                    }
                    // Handles that name no untapped source: not a choice the seat made, so the question is asked below.
                    if (held.isEmpty()) {
                        retryNote = "The sources you named with the play are not among your untapped mana sources.";
                    } else if (holdIfPayable(held, toPay, sa, effect)) {
                        p("[ROUTER] mana: keep untapped " + held.size() + " (said with the play) for " + verbOf(sa) + " " + labelOf(sa));
                        return held;
                    } else {
                        // What was said with the play cannot pay: ask the question itself, with the reason.
                        retryNote = "You said to keep " + names(held) + " untapped, but then the rest cannot pay " + toPay + ".";
                    }
                }
                List<String> labels = new ArrayList<>();
                for (Card c : sources) {
                    StringBuilder m = new StringBuilder();
                    c.getManaAbilities().forEach(ma -> { if (m.length() > 0) m.append("; "); m.append(named(flat(ma.getDescription()), c)); });
                    labels.add(labelOf(c) + " — " + m);
                }
                String what = sa.getHostCard() == null ? "this" : verbOf(sa) + " " + labelOf(sa);
                String question = "MANA: you are paying " + toPay + " for " + what + ". It needs " + plan.size() + " of your "
                        + sources.size() + " untapped mana sources. Which should stay untapped after this payment (at most "
                        + spare + ")? Pick none if it does not matter to you which ones are tapped";
                List<Card> held = new ArrayList<>();
                // A keep set the rest cannot pay is explained and asked once more (the model's own choice,
                // corrected); the engine's own plan never replaces it.
                for (int attempt = 0; attempt < 2; attempt++) {
                    String note = retryNote;
                    retryNote = null;
                    List<Integer> picks = decideMany(smartOk() ? smart : fast, "mana-sources",
                            (note == null ? "" : note + " ") + question, labels, 0, spare);
                    if (picks == null || picks.isEmpty()) return none;
                    held = new ArrayList<>();
                    for (int i : picks) held.add(sources.get(i));
                    if (holdIfPayable(held, toPay, sa, effect)) {
                        p("[ROUTER] mana: keep untapped " + held.size() + " for " + what);
                        return held;
                    }
                    if (rnd != null) break;  // the random arm draws again from nothing it learns
                    retryNote = "Keeping " + names(held) + " untapped leaves the other sources unable to pay " + toPay
                            + ". Choose again.";
                }
                retryNote = null;
                // Still unpayable: keep the longest prefix of the seat's own picks that the rest can pay.
                while (!held.isEmpty()) {
                    held.remove(held.size() - 1);
                    if (!held.isEmpty() && holdIfPayable(held, toPay, sa, effect)) break;
                }
                auto("mana-sources(cannot-pay-keeping-them)->kept " + held.size() + " of the picks");
                return held;
            } catch (Exception e) {
                p("[ROUTER] mana: " + e + " -> engine plan");
                return none;
            }
        }

        /** A correction for the next mana-sources ask (the previous keep set could not pay). */
        private String retryNote;

        /** Holds {@code held} for this payment if the remaining sources can still pay {@code toPay};
         *  otherwise holds nothing and returns false. */
        private boolean holdIfPayable(List<Card> held, forge.card.mana.ManaCost toPay, SpellAbility sa, boolean effect) {
            final Player me = getPlayer();
            for (Card c : held) forge.ai.AiCardMemory.rememberCard(me, c, forge.ai.AiCardMemory.MemorySet.HELD_MANA_SOURCES_FOR_NEXT_SPELL);
            // The planner can still be more hopeful than the real payment; payManaCost then pays again without the hold.
            if (ComputerUtilMana.getManaSourcesToPayCost(neededMana(toPay, sa, effect), sa, me, effect) != null) return true;
            for (Card c : held) forge.ai.AiCardMemory.forgetCard(me, c, forge.ai.AiCardMemory.MemorySet.HELD_MANA_SOURCES_FOR_NEXT_SPELL);
            return false;
        }

        private static String names(List<Card> cards) {
            List<String> n = new ArrayList<>();
            for (Card c : cards) n.add(c.getName());
            return String.join(", ", n);
        }

        /** Activates each tap-type mana ability the engine's plan for {@code toPay} uses, or, when that plan is
         *  null, the first activation {@code tapTypeStep} finds (#367); adds to {@code said} each source whose
         *  engine activation it has already reported. */
        private void prepayTapTypeManaAbilities(forge.card.mana.ManaCost toPay, SpellAbility sa, boolean effect, Set<Card> said) {
            final Player me = getPlayer();
            // Without such a source on the battlefield nothing runs, so every other payment is unchanged.
            if (!me.getCardsIn(ZoneType.Battlefield).anyMatch(c -> c.getManaAbilities().anyMatch(PlayerControllerLLM::hasTapTypeCost))) return;
            // A big payment can use the Druid more than once, but the engine's test payment uses each
            // source once. So plan, activate what the plan uses, and plan again: the floating mana is
            // spent first, and the next plan covers what is left. A source the seat cannot activate
            // (said) is not tried again; a round that activates nothing ends it.
            for (int round = 0; round < 8; round++) {
                CardCollection plan = ComputerUtilMana.getManaSourcesToPayCost(neededMana(toPay, sa, effect), sa, me, effect);
                boolean activated = false;
                if (plan == null) {
                    // The engine's greedy test found no payment; the one the menu's check found (#367) starts here.
                    TapTypeStep step = tapTypeStep(neededMana(toPay, sa, effect), sa, me, effect);
                    if (step == null || said.contains(step.ability.getHostCard())) return;
                    // What the paid ability's own tap part named stays out of the Druid's pick too.
                    List<Card> ownTapPart = tapPartHolds.get(sa);
                    if (ownTapPart != null) step.reserved.addAll(ownTapPart);
                    activated = prepayOne(step.ability, step.reserved, toPay, sa, effect, said);
                }
                else for (Card host : plan) {
                    if (said.contains(host)) continue;
                    for (SpellAbility ma : host.getManaAbilities()) {
                        if (!hasTapTypeCost(ma)) continue;
                        CardCollection others = new CardCollection(plan);
                        others.remove(host);
                        activated |= prepayOne(ma, others, toPay, sa, effect, said);
                        break;
                    }
                }
                if (!activated) return;
            }
        }

        /** Activates tap-type mana ability {@code ma} for {@code sa}'s payment, its tap part keeping {@code others}
         *  out; whether it was. A source that cannot be activated so is added to {@code said}, with its reason. */
        private boolean prepayOne(SpellAbility ma, CardCollection others, forge.card.mana.ManaCost toPay, SpellAbility sa,
                boolean effect, Set<Card> said) {
            final Player me = getPlayer();
            Card host = ma.getHostCard();
            ma.setActivatingPlayer(me);
            if (!playable(ma, me) || !CostPayment.canPayAdditionalCosts(ma.getPayCosts(), ma, effect)) {
                said.add(host);
                costDecisionFellBack("mana-tap-cost", host.getName() + " cannot be activated first");
                return false;
            }
            LlmCostDecision decision = new LlmCostDecision(me, ma, effect).forPayment(sa, others);
            forge.game.spellability.AbilityManaPart part = ma.getManaPart();
            if (part == null || part.isAnyMana() || part.isComboMana() || ma.getApi() == ApiType.ManaReflected) {
                // Said here, or not at all when its tap leaves no choice, so the check after
                // the payment does not report it.
                said.add(host);
                if (decision.hasTapChoice(ma.getPayCosts().getCostPartByType(forge.game.cost.CostTapType.class)))
                    costDecisionFellBack("mana-tap-cost", host.getName() + " also chooses a color");
                return false;
            }
            if (new CostPayment(ma.getPayCosts(), ma).payComputerCosts(decision)) {
                getGame().getStack().addAndUnfreeze(ma);
                p("[ROUTER] mana-tap-cost: activated " + host.getName() + " before paying " + toPay);
                return true;
            }
            said.add(host);
            costDecisionFellBack("mana-tap-cost", host.getName() + " payment refused");
            return false;
        }

        /** {@code toPay} as the real payment counts it (ComputerUtilMana.calculateManaCost with
         *  test=false): it already carries the raises CostPayment applied, so they are not applied
         *  again; X is counted, and the reductions are applied once. A fresh object each time, because
         *  a test payment pays it down. */
        private forge.game.mana.ManaCostBeingPaid neededMana(forge.card.mana.ManaCost toPay, SpellAbility sa, boolean effect) {
            forge.game.mana.ManaCostBeingPaid need = new forge.game.mana.ManaCostBeingPaid(toPay);
            if (need.getXcounter() > 0)
                need.setXManaCostPaid(forge.game.ability.AbilityUtils.calculateAmount(sa.getHostCard(),
                        sa.getParamOrDefault("XAlternative", "X"), sa), sa.getXColor() == null ? "1" : sa.getXColor());
            forge.game.cost.CostAdjustment.adjust(need, sa, getPlayer(), null, true, effect);
            // Only the amount is wanted here: the adjustment also records an emerge or offering sacrifice and marks it
            // used, and the real payment then adjusts again over that record and fails (Elder Deep-Fiend, surface check).
            Card emerged = sa.isEmerge() ? sa.getSacrificedAsEmerge() : null, offered = sa.isOffering() ? sa.getSacrificedAsOffering() : null;
            if (emerged != null) { emerged.setUsedToPay(false); sa.resetSacrificedAsEmerge(); }
            if (offered != null) { offered.setUsedToPay(false); sa.resetSacrificedAsOffering(); }
            return need;
        }

        /** A mana ability whose cost names cards to tap (Heritage Druid) or a permanent to sacrifice (Thermopod, rules
         *  pilot RP_198: Forge's AI chose Hostage Taker inside the payment). A sacrifice of the source itself is no
         *  choice. */
        private static boolean hasTapTypeCost(SpellAbility ma) {
            if (ma.getPayCosts() == null) return false;
            if (ma.getPayCosts().hasSpecificCostType(forge.game.cost.CostTapType.class)) return true;
            for (forge.game.cost.CostPart part : ma.getPayCosts().getCostParts())
                if (part instanceof forge.game.cost.CostSacrifice && !((forge.game.cost.CostSacrifice) part).payCostFromSource())
                    return true;
            return false;
        }

        /** Simultaneous-trigger ORDER (trigger targeting stays the engine's — the inherited
         *  orderAndPlaySimultaneousSa calls this virtually, then prepares/plays each). The model
         *  lists the order it wants the triggers to RESOLVE; the engine plays them in
         *  stack-placement order, so the list is reversed before returning. Deterministic
         *  fallback: the as-triggered order. */
        /** Puts the seat's simultaneous triggers on the stack in the seat's order, with the SEAT's
         *  targets and modes. The inherited version set them with {@code brains.doTrigger}, so Forge's
         *  AI chose every trigger target: Orcish Bowmasters' ping, Snapcaster Mage's card, Fury's
         *  damage, and whether an "up to one" trigger (Solitude, Skyclave Apparition) targets at all.
         *  A copy that may take new targets (storm, Twincast) gets them from the seat too: the engine path let Forge's AI
         *  pick them ({@code brains.doTrigger}), so it chose where every Tendrils, Grapeshot or Brain Freeze copy went. */
        @Override
        public void orderAndPlaySimultaneousSa(List<SpellAbility> activePlayerSAs) {
            for (final SpellAbility sa : orderSimultaneousSa(activePlayerSAs)) {
                if (sa.isTrigger() && !sa.isCopied()) {
                    if (prepareTrigger(sa)) seatCheck(sa, () -> forge.ai.ComputerUtil.playStack(sa, getPlayer(), getGame()));
                    else p("[ROUTER] trigger: " + safeName(sa.getHostCard()) + " has no legal target -> not put on the stack");
                } else if (sa.isCopied() && sa.isSpell() && sa.isMayChooseNewTargets()
                        && seatTargetsCopies("llm".equals(mode) && rnd == null, rnd) && !sa.hasParam("TargetingPlayer")) {
                    if (!sa.getHostCard().isInZone(ZoneType.Stack)) sa.setHostCard(getGame().getAction().moveToStack(sa.getHostCard(), sa));
                    else getGame().getStackZone().add(sa.getHostCard());
                    retargetCopy(sa);
                    getGame().getStack().add(sa);
                } else {
                    super.orderAndPlaySimultaneousSa(java.util.Collections.singletonList(sa));
                }
            }
        }

        /** Who targets a copy that may take new targets: the model seat and the puzzle prover (authored z_4: under the
         *  search arm Forge's AI picked the copy's target, so the prover never tried the other one). The other
         *  zero-model arms keep the engine path, so the canaries do not move. */
        static boolean seatTargetsCopies(boolean modelSeat, java.util.Random arm) {
            return modelSeat || arm instanceof PuzzleArms.Search;
        }

        /** "a copy of " for a copied spell under the faithful profile (a storm copy is not the card it copies), else "". */
        private String copyOf(SpellAbility sa) {
            return observer != null && sa != null && sa.isCopied() ? "a copy of " : "";
        }

        /** New targets for a copy, chosen by the seat (it may name the same ones); the old targets stay when its choice is
         *  not a legal set, as the engine's own path does. */
        private void retargetCopy(SpellAbility copy) {
            List<forge.game.spellability.TargetChoices> old = new ArrayList<>();
            for (SpellAbility s = copy; s != null; s = s.getSubAbility()) old.add(s.getTargets());
            boolean ok;
            try {
                for (SpellAbility s = copy; s != null; s = s.getSubAbility()) if (s.usesTargeting()) s.resetTargets();
                if (copy.getActivatingPlayer() == null) copy.setActivatingPlayer(getPlayer());
                ensureTargetsSet(copy);
                ok = true;
                for (SpellAbility s = copy; s != null; s = s.getSubAbility()) if (s.usesTargeting() && !s.isTargetNumberValid()) ok = false;
            } catch (Exception e) { ok = false; }
            if (!ok) {
                int i = 0;
                for (SpellAbility s = copy; s != null; s = s.getSubAbility()) s.setTargets(old.get(i++));
                auto("copy-targets(no legal choice)->kept the original's targets");
            }
        }

        /** A trigger that resolves without using the stack: the same seat targeting, then the engine plays it. */
        @Override
        public boolean playTrigger(Card host, forge.game.trigger.WrappedAbility wrapperAbility, boolean isMandatory) {
            // An optional one (isMandatory false: a decider exists, e.g. Snowfall's static TapsForMana) is
            // a "may" the seat decides — asked here like confirmTrigger asks it when a normal trigger
            // resolves, because a static trigger never reaches a stack item and WrappedAbility.resolve.
            // The seat used to be handed every static as if it had already said yes.
            if (!isMandatory && !confirmTrigger(wrapperAbility)) return false;
            if (!prepareTrigger(wrapperAbility)) return false;
            return playNoStack(wrapperAbility.getActivatingPlayer(), wrapperAbility, true);
        }

        /** An effect played without the stack (an opening-hand ability, a replacement effect, a may-effect from
         *  the opening deck): the seat's modes and targets, then the engine plays it. The inherited version
         *  targeted it with {@code brains.doTrigger}. As there, it is played even when a target has no legal
         *  choice: a replacement that has already replaced its event cannot be undone. */
        @Override
        public void playSpellAbilityNoStack(SpellAbility effectSA, boolean canSetupTargets) {
            if (canSetupTargets && !prepareTrigger(effectSA))
                p("[ROUTER] no-stack: " + safeName(effectSA.getHostCard()) + " has a target with no legal choice");
            playNoStack(getPlayer(), effectSA, true);
        }

        /** {@code ComputerUtil.playNoStack}, paying the seat's costs with {@link LlmCostDecision} where the
         *  engine routine hard-codes {@code AiCostDecision} (as {@link #playChosenSpellAbility} does for a
         *  cast). RE-DIFF against {@code ComputerUtil.playNoStack} on every engine re-pin (ENGINE-PIN.md). */
        private boolean playNoStack(Player who, SpellAbility sa, boolean effect) {
            if (!legallyPayable(sa, who, effect)) return false;
            final Card source = sa.getHostCard();
            if (!effect && sa.isSpell() && !source.isCopiedSpell()) {
                sa.setHostCard(getGame().getAction().moveToStack(source, sa));
                sa = forge.game.GameActionUtil.addExtraKeywordCost(sa);
            }
            final CostPayment pay = new CostPayment(sa.getPayCosts(), sa);
            final forge.game.cost.CostDecisionMakerBase decision = who == getPlayer()
                    ? new LlmCostDecision(who, sa, effect) : new forge.ai.AiCostDecision(who, sa, effect);
            if (!pay.payComputerCosts(decision)) return false;
            forge.game.ability.AbilityUtils.resolve(sa);
            return true;
        }

        /** Modes, then targets, for one of the seat's triggers (PlayerControllerAi.prepareSingleSa with
         *  the seat in place of {@code doTrigger}). False when a required target has no legal choice. */
        private boolean prepareTrigger(SpellAbility sa) {
            if (sa.getApi() == ApiType.Charm) {
                if (!forge.game.ability.effects.CharmEffect.makeChoices(sa)) return false;
                if (sa.hasParam("Random")) sa = sa.getSubAbility();
                if (sa == null) return true;
            }
            if (sa.hasParam("TargetingPlayer")) {
                Player tp = forge.game.ability.AbilityUtils.getDefinedPlayers(sa.getHostCard(), sa.getParam("TargetingPlayer"), sa).get(0);
                sa.setTargetingPlayer(tp);
                return tp.getController().chooseTargetsFor(sa);
            }
            if (sa.getActivatingPlayer() == null) sa.setActivatingPlayer(getPlayer());
            ensureTargetsSet(sa);
            for (SpellAbility part = sa; part != null; part = part.getSubAbility())
                if (part.usesTargeting() && !part.isTargetNumberValid()) return false;
            return true;
        }

        @Override
        public List<SpellAbility> orderSimultaneousSa(List<SpellAbility> activePlayerSAs) {
            clearLast();
            if (activePlayerSAs == null || activePlayerSAs.size() < 2) return activePlayerSAs;
            try {
                if (smartOk()) {
                    List<String> labels = new ArrayList<>();
                    // labelOf: the faithful label is whole (saLabel cut the trigger's text at 160
                    // characters) and carries no engine card id; the legacy profiles keep saLabel.
                    for (SpellAbility sa : activePlayerSAs) labels.add(labelOf(sa));
                    int n = activePlayerSAs.size();
                    // Ten storm copies are the same until each gets its target, which comes after the order: an order
                    // of identical items changes nothing, so it is not a choice (as exile has no order).
                    // Compared without the "@n" handles: each copy is its own card object, with its own handle.
                    if (observer != null && labels.stream().map(l -> l.replaceAll(" @\\d+", "")).distinct().count() == 1) {
                        auto("trigger-order(forced: " + n + " identical, any order is the same)->as-is");
                        return new ArrayList<>(activePlayerSAs);
                    }
                    // Storm makes copies, not triggers; the faithful question says which (and gives no advice).
                    boolean copies = activePlayerSAs.stream().allMatch(SpellAbility::isCopied);
                    String q = observer == null
                            ? "These " + n + " triggers go on the stack at the same time. List them in the order you want them to "
                              + "RESOLVE — the FIRST number you name resolves FIRST (it goes on the stack last). Order them so your "
                              + "plan works out."
                            : "These " + n + (copies ? " copies" : " triggers") + " go on the stack at the same time. List them in "
                              + "the order you want them to RESOLVE — the FIRST number you name resolves FIRST (it goes on the stack last).";
                    List<Integer> picks = decideMany(smart, "trigger-order", q, labels, n, n);
                    if (picks != null && picks.size() == n) {
                        List<SpellAbility> play = new ArrayList<>(n);
                        for (int i = n - 1; i >= 0; i--) play.add(activePlayerSAs.get(picks.get(i)));
                        return play;
                    }
                }
            } catch (Exception e) { /* deterministic fallback below */ }
            auto("trigger-order(default)->as-triggered");
            return new ArrayList<>(activePlayerSAs);
        }

        @Override
        public CardCollectionView chooseCardsForEffect(CardCollectionView sourceList, SpellAbility sa, String title,
                int min, int max, boolean isOptional, Map<String, Object> params) {
            clearLast();
            if (sourceList == null || sourceList.isEmpty()) return new CardCollection();   // nothing to choose from
            List<Card> opts = new ArrayList<>(sourceList);
            if (opts.size() <= Math.max(1, min) && !isOptional) return firstN(opts, min, "cards-for-effect(forced)");
            try {
                if (!smartOk()) {
                    if (isOptional) { auto("cards-for-effect(no-budget)->decline"); return new CardCollection(); }
                    return firstN(opts, min, "cards-for-effect(no-budget)");
                }
                List<String> labels = new ArrayList<>();
                for (Card c : opts) labels.add(cardLabel(c));
                String q = (title == null || title.isEmpty() ? "Choose cards" : title) + srcAsk(sa);
                boolean canCancel = offerCostCancel;
                offerCostCancel = false;
                List<Integer> picks = decideCost("cards-for-effect", q, labels, isOptional ? 0 : min, max, canCancel);
                if (canCancel && picks != null && costCancelled) { p("[ROUTER] cost" + srcOf(sa) + ": CANCEL (the play is not paid for)"); return new CardCollection(); }
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

        /** Route optional hand reveals through the model. Failure takes the first minimum-sized
         *  legal subset, logged as an automatic fallback; it never delegates strategy to Forge AI. */
        @Override
        public CardCollectionView chooseCardsToRevealFromHand(int min, int max, CardCollectionView valid) {
            clearLast();
            if (valid == null || valid.isEmpty()) return CardCollection.EMPTY;
            List<Card> opts = new ArrayList<>(valid);
            // Legality clamps: never reveal more than are valid, nor fewer than min (nor a negative min).
            int effMax = Math.min(max, opts.size());
            int effMin = Math.max(0, Math.min(min, effMax));
            if (effMax <= 0) return CardCollection.EMPTY;                    // reveal-zero: no real decision, no row
            if (opts.size() <= effMin) return firstN(opts, effMin, "reveal(forced)"); // whole valid set forced
            try {
                if (!smartOk()) return firstN(opts, effMin, "reveal(no-budget)");
                List<String> labels = new ArrayList<>();
                for (Card c : opts) labels.add(cardLabel(c));
                String q = "You must REVEAL between " + effMin + " and " + effMax + " card(s) from your hand "
                        + "(they become known to your opponent). Reveal only what the effect requires, and among "
                        + "legal choices prefer what least exposes your game plan.";
                List<Integer> picks = decideMany(smart, "reveal", q, labels, effMin, effMax);
                if (picks == null) return firstN(opts, effMin, "reveal(unparsed-or-dead)");
                CardCollection out = new CardCollection();
                for (int i : picks) out.add(opts.get(i));
                p("[ROUTER] reveal: " + (out.isEmpty() ? "(none)" : names(out, false, 10)));
                return out;
            } catch (Exception e) {
                return firstN(opts, effMin, "reveal(err)");
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
                // One card peeked is still a decision (keep it, or send it away): scry 1 and surveil 1
                // are most scrys and surveils. It used to be kept on top without asking; see declareAttackers.
                if (!smartOk()) { auto(verb + "(no-model)->keep-all-top"); return ImmutablePair.of(new CardCollection(opts), new CardCollection()); }
                List<String> labels = new ArrayList<>();
                for (Card c : opts) labels.add(cardLabel(c));
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
                if (observer != null) observer.learnOwnTop(namesOf(toTop), true, opts.size());
                p("[ROUTER] " + verb + ": keep-on-top " + (toTop.isEmpty() ? "(none)" : log) + "; -> " + destName + " x" + toOther.size());
                return ImmutablePair.of(toTop, toOther);
            } catch (Exception e) {
                auto(verb + "(err)->keep-all-top");
                return ImmutablePair.of(new CardCollection(opts), new CardCollection());
            }
        }

        private static List<String> namesOf(List<Card> cards) {
            List<String> n = new ArrayList<>();
            for (Card c : cards) n.add(c.getName());
            return n;
        }

        /** Every shuffle of this seat's library asks its controller (Player.shuffle); what the seat knew of the
         *  order is gone. The shuffle itself is the inherited one, unchanged. */
        @Override
        public CardCollectionView cheatShuffle(CardCollectionView list) {
            if (observer != null) observer.forgetOwnTop();
            return super.cheatShuffle(list);
        }

        /** The top-of-library order this seat just chose (names, top first) and the ability that asked,
         *  for the shuffle question that can follow it; see confirmAction. */
        private String lastTopOrder;
        private SpellAbility lastTopOrderSa;

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
                // No order exists to choose: exile has none, and a graveyard's matters only when the engine says a card
                // cares (isGraveyardOrdered), as Forge's own human controller decides. Doomsday asked the seat to order
                // the eight cards it exiles, mid-combo (GoldenChecksTest.doomsdaysPileAndItsOrderAreTheSeats).
                if (rnd == null && (source == null || source.getApi() != ApiType.ReorderZone)
                        && (destinationZone == ZoneType.Exile
                            || (destinationZone == ZoneType.Graveyard && !getGame().isGraveyardOrdered(getPlayer())))) {
                    auto("order(forced: " + destinationZone + " has no order to choose)->as-is");
                    return cards;
                }
                List<Card> opts = new ArrayList<>(cards);
                List<String> labels = new ArrayList<>();
                for (Card c : opts) labels.add(cardLabel(c));
                // The engine moves the returned list to the top of a deck one card at a time, so the LAST
                // card ends on top; "first = goes in/on top first" was read as "first = top", and in
                // runs/loop/g09 four Ponders each buried the card the seat meant to draw. Under the
                // faithful profile the question is asked top-down and the answer reversed, as Forge's
                // own human controller does; legacy profiles keep their bytes and their literal wording.
                boolean topDown = observer != null && orderGoesOnTop(destinationZone, source);
                String q = "Put these " + opts.size() + " card(s)" + srcAsk(source) + " into " + destinationZone
                        + " in your preferred order. List ALL " + opts.size() + " of them "
                        + (topDown ? "from the TOP down: the first card you name will be the top card, drawn first."
                                   : "(first = goes in/on top first).");
                List<Integer> picks = decideMany(smart, "order", q, labels, opts.size(), opts.size());
                if (picks == null || picks.size() != opts.size()) { auto("order(unparsed)->as-is"); return cards; }
                CardCollection out = new CardCollection();
                StringBuilder log = new StringBuilder();
                for (int i : picks) { out.add(opts.get(i)); if (log.length() > 0) log.append(", "); log.append(opts.get(i).getName()); }
                p("[ROUTER] order" + srcOf(source) + " -> " + destinationZone + (topDown ? " (top first)" : "") + ": " + log);
                if (topDown) {
                    lastTopOrder = log.toString(); lastTopOrderSa = source;
                    // Rearranging the top (Ponder) replaces those cards; a put-back (Brainstorm) goes above them.
                    observer.learnOwnTop(namesOf(out), true,
                            source != null && source.getApi() == ApiType.RearrangeTopOfLibrary ? opts.size() : 0);
                    java.util.Collections.reverse(out);
                }
                return out;
            } catch (Exception e) {
                auto("order(err)->as-is");
                return cards;
            }
        }

        /** Whether the cards of an order end on top of the library, so the LAST one returned is the top.
         *  orderedMoveToTopOfLibrary reads only LibraryPosition / RevealedLibraryPosition, and with neither
         *  it says "top", which is wrong for Dig's rest pile: DigEffect places that pile at LibraryPosition2
         *  (default -1, the bottom: Goblin Ringleader, Flow State; 0, the top: Cruel Fate, issue #107). Dig
         *  asks for its chosen pile and its rest pile with the same SpellAbility, so when both go to the
         *  library the pile cannot be told apart, and the literal wording is kept: even both at 0 puts the
         *  rest pile above the chosen one, so "the top card, drawn first" would be false for the chosen pile.
         *  Only position 0 is the top card; a deeper position keeps the literal wording too. */
        private boolean orderGoesOnTop(ZoneType destinationZone, SpellAbility sa) {
            if (destinationZone != ZoneType.Library || sa == null) return false;
            ApiType api = sa.getApi();
            if (api == ApiType.RearrangeTopOfLibrary) return true;
            if (api == ApiType.ChangeZone || api == ApiType.ChangeZoneAll) return orderedMoveToTopOfLibrary(destinationZone, sa);
            if (api == ApiType.Dig) {
                boolean chosenHere = ZoneType.smartValueOf(sa.getParamOrDefault("DestinationZone", "Hand")) == ZoneType.Library;
                boolean restHere = ZoneType.smartValueOf(sa.getParamOrDefault("DestinationZone2", "Library")) == ZoneType.Library;
                if (chosenHere == restHere) return false;
                return Integer.parseInt(sa.getParamOrDefault(restHere ? "LibraryPosition2" : "LibraryPosition", "-1")) == 0;
            }
            if (api == ApiType.DigUntil) {
                // DigUntilEffect: the revealed pile goes to RevealedLibraryPosition (absent = 0, the top), or,
                // when nothing was found, to NoneFoundDestination at NoneFoundLibraryPosition.
                boolean revealedHere = ZoneType.smartValueOf(sa.getParam("RevealedDestination")) == ZoneType.Library;
                boolean noneHere = ZoneType.smartValueOf(sa.getParam("NoneFoundDestination")) == ZoneType.Library;
                boolean revealedTop = forge.game.ability.AbilityUtils.calculateAmount(sa.getHostCard(), sa.getParam("RevealedLibraryPosition"), sa) == 0;
                boolean noneTop = forge.game.ability.AbilityUtils.calculateAmount(sa.getHostCard(), sa.getParam("NoneFoundLibraryPosition"), sa) == 0;
                if (revealedHere && noneHere) return revealedTop && noneTop;
                return noneHere ? noneTop : revealedTop;
            }
            return false;
        }

        /** Optional decisions are model choices; engine execution and target selection happen later. */
        private boolean optionalDecision(String type, SpellAbility sa, String question) {
            return optionalDecision(type, sa, question, false);
        }

        /** {@code routed}: take the tier from the routing table (smart unless {@code type} is listed in
         *  TIER_FAST_TYPES) instead of always the fast tier. */
        private boolean optionalDecision(String type, SpellAbility sa, String question, boolean routed) {
            clearLast();
            // A routed type whose tier is out of budget drops to the fast tier rather than to an
            // automatic NO: running out of smart calls must not silently decline every "may".
            boolean useRouted = routed && okFor(type);
            if (!useRouted && !fastOk()) { auto(type + "(no-model)->NO"); return false; }
            try {
                // Faithful: the source card's rules text too, not only the ability's label ("Snuff Out"'s
                // pay-4-life option is in its rules text, not in the destroy ability it labels).
                String source = sa == null ? "" : "\nAbility: " + labelOf(sa) + (observer != null ? "\nSource:" + srcAsk(sa) : "");
                int c = decideOne(useRouted ? bridgeFor(type) : fast, type, question + source,
                        Lists.newArrayList("YES (accept)", "NO (decline)"), null);
                if (c >= 0) {
                    // Said, every time: an accepted or declined "may" is a decision like any other, and a
                    // silent one made the audited game's missed Ajani flip impossible to trace.
                    p("[ROUTER] " + type + srcOf(sa) + ": " + (c == 0 ? "YES" : "NO"));
                    return c == 0;
                }
                auto(type + "(unparsed)->NO");
            } catch (Exception e) {
                auto(type + "(err)->NO");
            }
            return false;
        }

        /** The rules text of the trigger behind {@code wrapper}, from the engine, or "" when it has none.
         *  The seat used to be asked "Accept this optional triggered ability? Ability: Ajani, Nacatl
         *  Pariah 1{W}" — a yes/no about an effect it was never shown. */
        static String triggerText(forge.game.trigger.WrappedAbility wrapper) {
            try {
                String t = wrapper.getTrigger() == null ? "" : wrapper.getTrigger().toString();
                if (t == null || t.trim().isEmpty()) t = wrapper.getStackDescription();
                return t == null ? "" : flat(t);
            } catch (Exception e) { return ""; }
        }

        @Override
        public boolean confirmTrigger(forge.game.trigger.WrappedAbility wrapper) {
            clearLast();
            if (wrapper.isMandatory()) { auto("confirm-trigger(mandatory)->YES"); return true; }
            String text = observer == null ? "" : triggerText(wrapper);
            return optionalDecision("confirm-trigger", wrapper.getWrappedAbility(),
                    (text.isEmpty() ? "" : "OPTIONAL TRIGGER: \"" + text + "\"\n")
                    + "Accept this optional triggered ability? Declining does not resolve it. "
                    + "Any costs and targets are separate choices.", observer != null);
        }

        @Override
        public boolean confirmPayment(forge.game.cost.CostPart costPart, String prompt, SpellAbility sa) {
            return optionalDecision("confirm-payment", sa,
                    (prompt == null ? "Pay this optional cost?" : prompt)
                    + (costPart == null ? "" : "\nCost: " + costPart.toString()));
        }

        @Override
        public boolean confirmReplacementEffect(forge.game.replacement.ReplacementEffect replacementEffect,
                SpellAbility effectSA, GameEntity affected, String question) {
            // The engine supplies the same question to the human controller. Do not inspect
            // replacement internals or mutate the affected object while answering it.
            return optionalDecision("confirm-replacement", effectSA,
                    question == null ? "Apply this optional replacement effect?" : question);
        }

        /** BidLife's invitation to top the high bid (BidLifeEffect asks every player each round). The
         *  human is asked (InputConfirm); not overridden, it inherited Forge's AI, whose rule
         *  (!self, life > bid + 5) decided in the seat's place with no question. */
        @Override
        public boolean confirmBidAction(SpellAbility sa, PlayerActionConfirmMode mode, String message,
                int bid, Player winner) {
            return confirmAction(sa, mode, message, null, null, null);
        }

        @Override
        public boolean confirmAction(SpellAbility sa, PlayerActionConfirmMode mode, String message,
                List<String> options, Card cardToShow, Map<String, Object> params) {
            // "Cancel Search?" is the confirm about the pick just declined; after a NO that sent the pick back and a
            // second decline, the decline stands (issue #312).
            boolean cancelSearch = mode == PlayerActionConfirmMode.ChangeZoneGeneral && sa != null && sa == declinedPick;
            boolean declinedTwice = cancelSearch && sa == keptSearching;
            declinedPick = null; keptSearching = null;
            if (declinedTwice) {
                clearLast();
                lastTopOrderSa = null;
                auto("confirm(declined-again)->YES cancel search" + srcOf(sa));
                return true;
            }
            boolean yes = askConfirm(sa, mode, message, cardToShow);
            if (cancelSearch && !yes) keptSearching = sa;
            return yes;
        }

        private boolean askConfirm(SpellAbility sa, PlayerActionConfirmMode mode, String message, Card cardToShow) {
            clearLast();
            // The "may shuffle" after an ordering goes to the fast tier, a separate conversation that never
            // saw the order: in runs/loop/g10 it answered YES to Ponder's shuffle and undid the order the
            // smart tier had just chosen. State the order it would undo; consumed by this ask either way.
            String undoes = "";
            // Which question this is comes from the ability (MayShuffle on the rearrange, or a Shuffle effect), not
            // from the wording of the engine's message.
            if (lastTopOrderSa != null && lastTopOrderSa == sa
                    && (sa.hasParam("MayShuffle") || sa.getApi() == forge.game.ability.ApiType.Shuffle))
                undoes = " You just put these on top of that library, top first: " + lastTopOrder
                        + ". Shuffling randomizes the whole library, those cards included.";
            lastTopOrderSa = null;
            if (!fastOk()) { auto("confirm(no-model)->NO"); return false; } // deterministic decline, not the AI
            try {
                String q = "Yes/No decision" + srcAsk(sa) + ": " + (message == null ? String.valueOf(mode) : message) + undoes;
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
                playConfirmed = yes && sa != null && sa.getApi() == ApiType.Play ? cardToShow : null;
                p("[ROUTER] confirm(" + oneLine(message == null ? String.valueOf(mode) : message) + "): " + (yes ? "YES" : "NO"));
                return yes;
            } catch (Exception e) {
                auto("confirm(err)->NO"); return false;
            }
        }

        @Override
        public int chooseNumber(SpellAbility sa, String title, int min, int max) {
            clearLast();
            if (max < min) throw new IllegalArgumentException("empty numeric range");
            if (max == min) { auto("number(forced)->" + min); return min; }
            if (sa != null && sa.isHarmonize() && HARMONIZE_TITLE.equals(title)) return chooseHarmonizePower(sa, min, max);
            if (!fastOk()) { auto("number(no-model)->" + min); return min; }
            try {
                if ((long) max - min <= 12) {
                    List<Integer> values = new ArrayList<>();
                    for (long v = min; v <= max; v++) values.add((int) v);
                    return chooseNumber(sa, title, values, null);
                }
                if (rnd != null) {
                    int value = (int) rnd.nextLong((long) min, (long) max + 1);
                    noteSolverChoice("number", (title == null ? "" : title + " ") + value);
                    auto("number(random)->" + value); return value;
                }
                // A range is a legal domain, not a three-option approximation. Avoid constructing
                // billions of options or overflowing at Integer.MAX_VALUE.
                String base = snapshot() + (observer != null ? "\nDECISION: " : "\n") + (title == null ? "Choose a number" : title)
                        + srcAsk(sa) + "\nEvery integer from " + min + " through " + max
                        + " inclusive is legal. Reply with exactly NUMBER: <integer>.";
                for (int attempt = 0; attempt < 2 && fastOk(); attempt++) {
                    offerAnswer(fast, AnswerTool.Spec.number(min, max));
                    String reply = ask(fast, base + (attempt == 0 ? "" : "\nInvalid answer. Use exactly NUMBER: <integer> within the range."));
                    noteAnswerVia(attempt);
                    if (reply == null) break;
                    AnswerTool.Answer ta = toolAnswer();
                    Integer value = ta != null ? ta.number : parseNumberReply(reply, min, max);
                    if (value != null) {
                        // Numeric values are not option indexes; the explicit domain is in options.
                        evalDec("number-value", -1, value, tierOf(false), 0, null,
                                Lists.newArrayList("integer range [" + min + ", " + max + "]"));
                        return value;
                    }
                }
                auto("number(unparsed)->" + min); return min;
            } catch (Exception e) {
                auto("number(err)->" + min); return min;
            }
        }

        static Integer parseNumberReply(String reply, int min, int max) {
            if (reply == null || !reply.trim().matches("NUMBER: *-?[0-9]+")) return null;
            try {
                int value = Integer.parseInt(reply.trim().substring(7).trim());
                return value >= min && value <= max ? value : null;
            } catch (NumberFormatException e) { return null; }
        }

        @Override
        public int chooseNumber(SpellAbility sa, String title, int min, int max, Map<String, Object> params) {
            // Engine params contain internal objects/AI hints, not additional legal alternatives.
            return chooseNumber(sa, title, min, max);
        }

        @Override
        public int chooseNumber(SpellAbility sa, String title, List<Integer> options, Player relatedPlayer) {
            clearLast();
            if (options == null || options.isEmpty()) throw new IllegalArgumentException("empty numeric options");
            int fallback = options.get(0);
            if (options.size() == 1) { auto("number(forced)->" + fallback); return fallback; }
            if (!fastOk()) { auto("number(no-model)->" + fallback); return fallback; }
            List<String> labels = new ArrayList<>();
            for (Integer value : options) labels.add(String.valueOf(value));
            try {
                int c = decideOne(fast, "number", (title == null ? "Choose a number" : title) + srcAsk(sa), labels, null);
                if (c >= 0) return options.get(c);
            } catch (Exception e) { /* deterministic fallback below, never Forge AI */ }
            auto("number(unparsed)->" + fallback); return fallback;
        }

        @Override
        public boolean chooseBinary(SpellAbility sa, String question, BinaryChoiceType kind, Boolean defaultValue) {
            clearLast();
            boolean fallback = Boolean.TRUE.equals(defaultValue);
            if (!fastOk()) { auto("binary(no-model)->" + fallback); return fallback; }
            // Same true/false ordering as PlayerControllerHuman's confirmation dialog.
            List<String> labels;
            switch (kind) {
                case HeadsOrTails: labels = Lists.newArrayList("Heads", "Tails"); break;
                case TapOrUntap: labels = Lists.newArrayList("Tap", "Untap"); break;
                case PlayOrDraw: labels = Lists.newArrayList("Play", "Draw"); break;
                case OddsOrEvens: labels = Lists.newArrayList("Odds", "Evens"); break;
                case UntapOrLeaveTapped: labels = Lists.newArrayList("Untap", "Leave tapped"); break;
                case LeftOrRight: labels = Lists.newArrayList("Left", "Right"); break;
                case AddOrRemove: labels = Lists.newArrayList("Add counter", "Remove counter"); break;
                case IncreaseOrDecrease: labels = Lists.newArrayList("Increase", "Decrease"); break;
                default: throw new IllegalArgumentException("unsupported binary choice " + kind);
            }
            try {
                int c = decideOne(fast, "binary", String.valueOf(question) + srcAsk(sa), labels, null);
                if (c >= 0) return c == 0;
            } catch (Exception e) { /* deterministic fallback below */ }
            auto("binary(unparsed)->" + fallback); return fallback;
        }

        @Override
        public boolean chooseBinary(SpellAbility sa, String question, BinaryChoiceType kind, Map<String, Object> params) {
            return chooseBinary(sa, question, kind, (Boolean) null);
        }

        // ---------------- G7: choices from the engine's own legal option list ----------------
        // Each of these is a real decision the Forge AI used to make silently. The model picks from
        // the engine-provided legal set (so an illegal choice is impossible); the fallback is a
        // deterministic legal option, never Forge AI.

        /** Play or draw after winning the toss. Forge AI always plays first; the model may choose to
         *  draw. Deterministic fallback: play first (the pre-existing behaviour). */
        @Override
        public Player chooseStartingPlayer(boolean isFirstGame) {
            clearLast();
            try {
                if (fastOk()) {
                    List<String> labels = Lists.newArrayList("Play first", "Draw first");
                    int c = decideOne(fast, "starting-player", "You won the roll. Play first, or draw first?", labels, null);
                    if (c == 0) return getPlayer();
                    if (c == 1) for (Player o : getPlayer().getOpponents()) return o;
                }
            } catch (Exception e) { /* deterministic fallback below */ }
            auto("starting-player(default)->play");
            return getPlayer();
        }

        /** Colour choice (Painter's Servant, protection, Caves of Koilos-style). Legal set from the
         *  engine. Deterministic fallback: the first colour in the set -- and the fallback says HOW
         *  MANY colours were legal, because only one of them is a forced pick. A multi-colour set the
         *  model was not asked about is a decision the HARNESS took: that is how four Painter's Servant
         *  colour choices in runs/loop/g06 were taken with no row a reader could tell apart from a
         *  forced one (match_review counts "forced" as free and reports the rest as other-automatic). */
        @Override
        public byte chooseColor(String message, SpellAbility sa, ColorSet colors) {
            clearLast();
            int legal = colors == null ? 0 : colors.countColors();
            try {
                if (legal >= 2 && smartOk()) {
                    List<MagicColor.Color> present = new ArrayList<>();
                    for (MagicColor.Color c : colors) present.add(c);
                    if (present.size() >= 2) {
                        List<String> labels = new ArrayList<>();
                        for (MagicColor.Color c : present) labels.add(c.getName());
                        // Inside a payment the colour is for that cost: say which (a mana ability's own text does not).
                        String paying = payingForReal && payingCost != null && payingFor != null && payingFor != sa
                                ? " (you are paying " + payingCost + " for " + labelOf(payingFor) + ")" : "";
                        int pick = decideOne(smart, "color",
                                "Choose a color" + (message == null || message.isEmpty() ? "" : ": " + fit(message)) + srcAsk(sa) + paying,
                                labels, null);
                        if (pick >= 0 && pick < present.size()) return present.get(pick).getColorMask();
                    }
                }
            } catch (Exception e) { /* deterministic fallback below */ }
            auto(legal < 2 ? "color(forced)->first" : "color(default)->first of " + legal + " legal");
            return firstColorMask(colors);
        }

        /** As chooseColor, with colourless allowed. */
        @Override
        public byte chooseColorAllowColorless(String message, Card card, ColorSet colors) {
            clearLast();
            try {
                if (colors != null && colors.countColors() >= 2 && smartOk()) {
                    List<MagicColor.Color> present = new ArrayList<>();
                    for (MagicColor.Color c : colors) present.add(c);
                    if (present.size() >= 2) {
                        List<String> labels = new ArrayList<>();
                        for (MagicColor.Color c : present) labels.add(c.getName());
                        int pick = decideOne(smart, "color-colorless",
                                "Choose a color (colorless allowed)" + (message == null || message.isEmpty() ? "" : ": " + fit(message))
                                + (observer != null ? cardAsk(card) : ""),
                                labels, null);
                        if (pick >= 0 && pick < present.size()) return present.get(pick).getColorMask();
                    }
                }
            } catch (Exception e) { /* deterministic fallback below */ }
            auto("color-colorless(default)->first");
            return firstColorMask(colors);
        }

        /** Type/card naming (Cavern of Souls, Engineered Plague, Meddling Mage-style). Legal set from
         *  the engine. Deterministic fallback: the first valid type. */
        @Override
        public String chooseSomeType(String kindOfType, SpellAbility sa, java.util.Collection<String> validTypes, boolean isOptional) {
            clearLast();
            try {
                if (validTypes != null && validTypes.size() >= 2 && smartOk()) {
                    List<String> opts = new ArrayList<>(validTypes);
                    // Cavern of Souls read "Choose a Creature to name": it asks for a creature TYPE (runs/loop/g04, g05).
                    int pick = decideOne(smart, "some-type", (observer != null
                            ? "Choose a " + kindOfType.toLowerCase(Locale.ROOT) + " type"
                            : "Choose a " + kindOfType + " to name") + srcAsk(sa), opts, null);
                    if (pick >= 0 && pick < opts.size()) return opts.get(pick);
                }
            } catch (Exception e) { /* deterministic fallback below */ }
            if (validTypes == null || validTypes.isEmpty()) return super.chooseSomeType(kindOfType, sa, validTypes, isOptional);
            auto(validTypes == null || validTypes.size() < 2
                    ? "some-type(forced)->first"
                    : "some-type(default)->first of " + validTypes.size() + " legal");
            return validTypes.iterator().next();
        }

        /** Protection colour (Mother of Runes / Giver of Runes in Death and Taxes). Legal set from the
         *  engine. Deterministic fallback: the first choice. */
        @Override
        public String chooseProtectionType(SpellAbility sa, List<String> choices) {
            clearLast();
            try {
                if (choices != null && choices.size() >= 2 && smartOk()) {
                    int pick = decideOne(smart, "protection", "Choose what to protect from" + srcAsk(sa), new ArrayList<>(choices), null);
                    if (pick >= 0 && pick < choices.size()) return choices.get(pick);
                }
            } catch (Exception e) { /* deterministic fallback below */ }
            if (choices == null || choices.isEmpty()) return super.chooseProtectionType(sa, choices);
            auto("protection(default)->first");
            return choices.get(0);
        }

        /** First colour mask in an engine ColorSet (deterministic fallback; white if empty). */
        private static byte firstColorMask(ColorSet colors) {
            try {
                if (colors != null) for (MagicColor.Color c : colors) return c.getColorMask();
            } catch (Exception ignore) { }
            return MagicColor.WHITE;
        }

        /** Route "name a card" from an explicit list (Alhammarret, ChooseFromList effects).
         *  Small list -> numbered menu; big list -> free-form name validated against it. */
        @Override
        public String chooseCardName(SpellAbility sa, List<ICardFace> faces, String message) {
            clearLast();
            try {
                // No face to name: nothing to choose (Forge's AI was asked here before; no engine fallback).
                if (faces == null || faces.isEmpty()) { auto("card-name(list:empty)->none"); return ""; }
                if (rnd != null) { // random arm: uniform over the list — never the model, never the AI
                    String pick = faces.get(rnd.nextInt(faces.size())).getName();
                    noteSolverChoice("card-name", pick);
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
                    int c = decideOne(smart, "card-name", "NAME A CARD" + srcAsk(sa) + ": " + fit(message)
                            + (observer != null ? targetNote(sa, getPlayer()) : ""), labels, null);
                    if (c >= 0) { p("[ROUTER] card-name: " + labels.get(c) + srcOf(sa)); return labels.get(c); }
                    // Bridge-down (c==-2) or still-unparseable-after-retry (c==-1): same deterministic
                    // first-face default the no-model branch above uses, never Forge AI.
                    auto("card-name(list:unparsed)->" + faces.get(0).getName());
                    return faces.get(0).getName();
                }
                String want = askCardName(sa, message, null, name -> {
                    for (ICardFace f : faces) if (f.getName().equalsIgnoreCase(name)) return true;
                    return false;
                });
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
                auto("card-name(list:err)->first");
                return faces == null || faces.isEmpty() ? "" : faces.get(0).getName();
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
                    auto("card-name(no-pool)->none");
                    return "";
                }
                String want = askCardName(sa, message, valid, name -> {
                    ICardFace f = StaticData.instance().getCommonCards().getFaceByName(name);
                    return f != null && (cpp == null || cpp.test(f));
                });
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
                auto("card-name(no-pool)->none");   // no card in the database matches: nothing can be named
                return "";
            } catch (Exception e) {
                String first = firstNameMatching(cpp);
                auto("card-name(err)->" + (first.isEmpty() ? "none" : first));
                return first;
            }
        }

        /** The first card name in the database that {@code cpp} accepts, or "" when none does. */
        private static String firstNameMatching(java.util.function.Predicate<ICardFace> cpp) {
            try {
                return StaticData.instance().getCommonCards().streamAllFaces().filter(cpp == null ? f -> true : cpp)
                        .findFirst().map(ICardFace::getName).orElse("");
            } catch (Exception e) {
                return "";
            }
        }

        private static final Set<String> CARD_TYPE_WORDS = Set.of("Artifact", "Battle", "Creature", "Enchantment",
                "Instant", "Kindred", "Land", "Planeswalker", "Sorcery", "Tribal", "Permanent", "Spell");
        /** Words that read before the type: supertypes and colours ("legendary", "blue"). */
        private static final Set<String> ADJECTIVE_WORDS = Set.of("Basic", "Legendary", "Snow", "White", "Blue", "Black",
                "Red", "Green", "Colorless", "Multicolor");

        /** Conditions said after the noun ("a creature you control"). "ControlledBy" takes its next word, below. */
        private static final Map<String, String> POSTFIX_WORDS = Map.of(
                "YouCtrl", "you control", "OppCtrl", "an opponent controls", "YouOwn", "you own", "OppOwn", "an opponent owns",
                "sameName", "with the same name", "NamedCard", "with the chosen name");

        /** One alternative of an engine filter ("Card.nonLand", "Card.Blue+Other", "Creature.OppCtrl", "Planeswalker.Nissa")
         *  as {words before the noun, the noun, "complete" or "partial", words after the noun}: {"nonland", "card"},
         *  {"other blue", "card"}, {"", "creature", .., "an opponent controls"}, {"Nissa", "planeswalker"}. A property
         *  that is none of these (cmcEQX, Self, ManaCost=...) is left out and the result is "partial". */
        static String[] filterParts(String alt) {
            String[] seg = alt.trim().split("\\.", 2);
            List<String> props = new ArrayList<>();
            if (!seg[0].equals("Card")) props.add(seg[0]);
            if (seg.length > 1) props.addAll(java.util.Arrays.asList(seg[1].split("\\+")));
            List<String> adjectives = new ArrayList<>(), subtypes = new ArrayList<>(), types = new ArrayList<>(), after = new ArrayList<>();
            boolean other = false, complete = true;
            for (String w : props) {
                if (w.equals("Other")) { other = true; continue; }
                if (POSTFIX_WORDS.containsKey(w)) { after.add(POSTFIX_WORDS.get(w)); continue; }
                boolean non = w.startsWith("non") && w.length() > 3 && Character.isUpperCase(w.charAt(3));
                String core = non ? w.substring(3) : w;
                boolean type = CARD_TYPE_WORDS.contains(core), adjective = ADJECTIVE_WORDS.contains(core);
                boolean subtype = !type && !adjective && core.matches("[A-Z][a-z]+") && forge.card.CardType.isASubType(core);
                if (!type && !adjective && !subtype) { complete = false; continue; }
                if (non) adjectives.add(subtype ? "non-" + core : "non" + core.toLowerCase(Locale.ROOT));
                else if (adjective) adjectives.add(core.toLowerCase(Locale.ROOT));
                else if (type) types.add(core.toLowerCase(Locale.ROOT));
                else subtypes.add(core);
            }
            List<String> before = new ArrayList<>();
            if (other) before.add("other");
            before.addAll(adjectives);
            before.addAll(subtypes);
            return new String[] {String.join(" ", before), types.isEmpty() ? "card" : String.join(" ", types),
                    complete ? "complete" : "partial", String.join(" ", after)};
        }

        private static String withArticle(String phrase) {
            if (phrase.startsWith("other ")) return "an" + phrase;      // "another blue card"
            return ("aeiou".indexOf(Character.toLowerCase(phrase.charAt(0))) >= 0 ? "an " : "a ") + phrase;
        }

        private static String plural(String noun) {
            return noun.endsWith("y") ? noun.substring(0, noun.length() - 1) + "ies" : noun + "s";
        }

        private static final Pattern TYPE_PLURAL = Pattern.compile("\\b([A-Z][a-z]+)s\\b");

        /** A creature type in its own plural: Forge's text adds an "s" to the type, so Heritage Druid's cost read "Tap
         *  three untapped Elfs you control" (runs/loop/g10). Forge's type list has the plural it should have used. */
        static String typePlurals(String text) {
            Matcher m = TYPE_PLURAL.matcher(text);
            StringBuilder out = new StringBuilder();
            while (m.find()) {
                String one = m.group(1), own = forge.card.CardType.getPluralType(one);
                boolean wrong = !own.equals(one) && !own.equals(m.group()) && forge.card.CardType.isASubType(one);
                m.appendReplacement(out, Matcher.quoteReplacement(wrong ? own : m.group()));
            }
            m.appendTail(out);
            return out.toString();
        }

        /** The engine's filter for a name in words ("a nonland card", "a Nissa planeswalker card"), or null when an
         *  alternative has a condition that cannot be said in words: the raw filter was shown to the seat as
         *  "(must match: Card.nonLand)" (runs/loop/g05, Cabal Therapy). */
        static String nameConstraint(String valid) {
            if (valid == null || valid.trim().isEmpty()) return null;
            List<String> alternatives = new ArrayList<>();
            for (String alt : valid.trim().split(",")) {
                String[] f = filterParts(alt);
                if (!f[2].equals("complete")) return null;
                String noun = f[1].equals("card") ? "card" : f[1] + " card";
                alternatives.add(withArticle(f[0].isEmpty() ? noun : f[0] + " " + noun) + (f[3].isEmpty() ? "" : " " + f[3]));
            }
            return String.join(" or ", alternatives);
        }

        /** Forge's filter syntax: a capitalised word, a dot, then a property ("Card.nonLand", "Eldrazi.Spawn",
         *  "Creature.cmcEQX"). The same pattern is the lint in AskContract and match_review.py (test_match_review
         *  keeps the three equal). */
        static final String ENGINE_FILTER_REGEX = "\\b[A-Z][a-z]+\\.[a-z]*[A-Z!][A-Za-z]";
        private static final Pattern COST_FILTER = Pattern.compile(
                "\\b(?:(an?|one|two|three|four|five|six|seven|eight|nine|ten|X|\\d+) )?((?:untapped|tapped|other) )?([A-Z][a-z]+\\.[a-z]*[A-Z!][A-Za-z!+=<>]*)(?: (cards?)| (TargetedOrController|TargetedController|Targeted))?");

        /** Engine text (a cost, a stack line, a log line) with every engine filter in words: Force of Will's pitch read "Exile a Card.Blue+Other card
         *  from your hand" in ten recorded rows; now "Exile another blue card from your hand". A count above one makes
         *  the noun plural; a condition that cannot be said in words is left out (the option quotes the card's text). */
        static String plainFilters(String text) {
            if (text == null || text.indexOf('.') < 0) return text;
            Matcher m = COST_FILTER.matcher(text);
            StringBuilder out = new StringBuilder();
            while (m.find()) {
                String token = m.group(3);
                String target = m.group(5);
                if (target != null && token.endsWith("ControlledBy")) token = token.substring(0, token.length() - "ControlledBy".length()) + "ControlledByTarget";
                String[] f = filterParts(token.replace("+ControlledByTarget", "").replace(".ControlledByTarget", ""));
                String after = f[3];
                if (token.contains("ControlledByTarget"))
                    after = (after.isEmpty() ? "" : after + " ") + (target.equals("TargetedOrController")
                            ? "that player or that planeswalker's controller controls" : "that player controls");
                // "YouCtrl" on a card in a hand or graveyard means "yours", which the line already says ("from their
                // graveyard"): the control words are dropped there.
                String rest = text.substring(m.end());
                if (rest.startsWith(" from ") || rest.startsWith(" in "))
                    after = after.replaceAll("(?:^| )(?:you control|an opponent controls|you own|an opponent owns)", "").trim();
                String count = m.group(1);
                boolean bare = count == null, one = !bare && (count.equals("a") || count.equals("an") || count.equals("one") || count.equals("1"));
                String noun = f[1].equals("card") || m.group(4) == null ? f[1] : f[1] + " card";
                noun = one ? noun : plural(noun);
                String words = (m.group(2) == null ? "" : m.group(2)) + f[0];  // "untapped " stays before the filter's words
                String phrase = (words.trim().isEmpty() ? noun : words.trim() + " " + noun) + (after.isEmpty() ? "" : " " + after);
                String said = bare ? (f[2].equals("complete") ? phrase : "the " + phrase + " the card describes")
                        : one && !count.equals("one") && !count.equals("1") ? withArticle(phrase) : count + " " + phrase;
                m.appendReplacement(out, Matcher.quoteReplacement(said));
            }
            m.appendTail(out);
            return out.toString();
        }

        /** A cost as the seat reads it: under the faithful profile its filters are said in words; a legacy profile keeps
         *  the engine's text byte for byte (docs/STANDARDS.md section 4). */
        private String costText(String engineText) {
            return observer != null ? plainFilters(engineText) : engineText;
        }

        /** Shared free-form "CARD: <name>" ask (one retry). Returns the raw name or null. `exists`
         *  (nullable): whether a name is one the caller would accept, checked on a tool answer. */
        private String askCardName(SpellAbility sa, String message, String valid, java.util.function.Predicate<String> exists) {
            String mustBe = nameConstraint(valid);
            if (observer != null && mustBe == null && valid != null && !valid.isEmpty())
                p("[ROUTER] card-name: the filter " + valid + " cannot be said in words; the ask leaves it to the card's text");
            String q = snapshot() + "\nDECISION: NAME A CARD" + srcAsk(sa) + ": " + fit(message)
                    + (observer == null ? (valid != null && !valid.isEmpty() ? " (must match: " + fit(valid) + ")" : "")
                       : mustBe != null ? " (it must be the name of " + mustBe + ")" : "")
                    + (observer != null
                        ? "." + targetNote(sa, getPlayer())
                            + " "   // facts only under the faithful profile (PROMPT-AUDIT.md C3): no advice on how to choose
                        : ". Use everything you've seen this game — revealed hands, their deck's archetype — to name "
                            + "the card most likely there / most dangerous to your plan. ")
                    + "End with EXACTLY: CARD: <exact English card name>";
            for (int attempt = 0; attempt < 2; attempt++) {
                // The retry re-sends the whole ask under the faithful profile or with the tool, as decideOne does: a
                // stateless backend asked the bare correction answered with no state and no question. With the tool a
                // name that is not a card is corrected in the same conversation.
                AnswerTool.Spec spec = AnswerTool.Spec.card();
                if (exists != null) spec.check(a -> exists.test(a.card) ? null : "\"" + a.card + "\" is not a card that can be named here.");
                String retry = "That was not a legal or recognizable card name here. Answer with ONE line only: CARD: <exact card name>";
                boolean tool = offerAnswer(smart, spec) != null;
                String reply = ask(smart, attempt == 0 ? q : (tool || observer != null) ? q + "\n\n(" + retry + ")" : retry);
                noteAnswerVia(attempt);
                if (reply == null) return null;
                AnswerTool.Answer ta = toolAnswer();
                if (ta != null) return ta.card;
                String seg = textSegment(reply, "CARD:");
                if (seg != null) {
                    String want = seg.replace("\"", "").replace("`", "").trim();
                    if (!want.isEmpty()) return want;
                }
            }
            return null;
        }

        static List<AbilitySub> pick(List<AbilitySub> modes, List<Integer> picks) {
            List<AbilitySub> out = new ArrayList<>();
            for (int i : picks) out.add(modes.get(i));
            return out;
        }

        /** Whether {@code sa} can be paid with the added costs of {@code modes} (spree, tiered). */
        boolean modesPayable(SpellAbility sa, List<AbilitySub> modes) {
            return modesPayable(sa, modes, getPlayer());
        }
        static boolean modesPayable(SpellAbility sa, List<AbilitySub> modes, Player me) {
            try {
                Cost total = sa.getPayCosts().copy();
                for (AbilitySub ab : modes) if (ab.hasParam("ModeCost")) total.add(new Cost(ab.getParam("ModeCost"), false));
                Cost esc = escalateCost(sa);
                if (esc != null) for (int i = 1; i < modes.size(); i++) total.add(esc);
                boolean was = PRICING_FOR_SNAPSHOT;
                PRICING_FOR_SNAPSHOT = true;
                try { return legallyPayable(total, sa, me, false); } finally { PRICING_FOR_SNAPSHOT = was; }
            } catch (Exception e) { return false; }
        }

        /** Escalate's cost, paid once for each mode beyond the first (CostAdjustment's "Escalate" raise), or null. */
        static Cost escalateCost(SpellAbility sa) { return keywordCost(sa, "Escalate:"); }

        /** The cost of the host's keyword written {@code prefix}{cost} (Escalate:1 W, Strive:2 W), or null. */
        static Cost keywordCost(SpellAbility sa, String prefix) {
            Card host = sa == null ? null : sa.getHostCard();
            if (host == null) return null;
            for (forge.game.keyword.KeywordInterface ki : host.getKeywords()) {
                String o = ki.getOriginal();
                if (o.startsWith(prefix)) return new Cost(o.substring(prefix.length()), false);
            }
            return null;
        }

        /** Route modal spells ("choose one —") through the numbered menu. */
        @Override
        public List<AbilitySub> chooseModeForAbility(SpellAbility sa, List<AbilitySub> possible, int min, int num, boolean allowRepeat) {
            clearLast();
            try {
                // possible==null/empty is a degenerate engine state (a modal ability with nothing to
                // choose from) and has no legal candidate to default to. allowRepeat ("choose three,
                // you may choose the same mode more than once") IS routed: the model picks the modes,
                // repeats included, so a repeatable Charm is no longer the Forge AI's (#127).
                if (possible == null || possible.isEmpty()) { auto("modes(empty)->none"); return new ArrayList<>(); }
                // Spree and tiered modes each add a cost (ModeCost, added at payment by CostAdjustment): only the modes
                // the cast can pay with are offered, and the set is checked together (Dance of the Tumbleweeds' +{1}
                // mode was chosen with two lands for its {1}{G} and the cast failed, keyword gauntlet).
                final boolean priced = possible.stream().anyMatch(ab -> ab.hasParam("ModeCost")) || escalateCost(sa) != null;
                if (possible.stream().anyMatch(ab -> ab.hasParam("ModeCost"))) {
                    possible = new ArrayList<>(possible);
                    possible.removeIf(ab -> !modesPayable(sa, List.of(ab)));
                    if (possible.isEmpty()) { auto("modes(none payable)->none"); return new ArrayList<>(); }
                }
                // Escalate adds its cost for each mode beyond the first: as many modes as the cast can pay for are offered
                // (Borrowed Grace's both modes were chosen with mana for one, and the cast failed; random gauntlet).
                if (escalateCost(sa) != null && !allowRepeat) {
                    int most = Math.min(num, possible.size());
                    while (most > Math.max(1, min) && !modesPayable(sa, possible.subList(0, most))) most--;
                    if (most < num) p("[ROUTER] modes: escalate can be paid for " + most + " mode(s), not " + num);
                    num = most;
                }
                if (possible.size() == 1 && min >= 1) { auto("modes(forced-only-legal)"); return repeated(possible.get(0), Math.max(min, Math.min(num, 1))); } // forced
                final int paw = allowRepeat ? pawprintBudget(sa) : -1;
                final int[] pawCost = paw >= 0 ? pawprints(possible) : null;
                // No budget: deterministic legal modes (distinct first, then repeats), never Forge AI.
                if (!smartOk()) {
                    List<AbilitySub> defPick = defaultModes(possible, min, num, allowRepeat, pawCost, paw);
                    auto("modes(no-budget)->first" + defPick.size());
                    return defPick;
                }
                List<String> labels = new ArrayList<>();
                String host = sa.getHostCard() == null ? "" : safeName(sa.getHostCard());
                for (int mi = 0; mi < possible.size(); mi++) {
                    AbilitySub ab = possible.get(mi);
                    labels.add((ab.hasParam("ModeCost") ? "+ " + costText(new Cost(ab.getParam("ModeCost"), false).toSimpleString()) + " — " : "")
                            + (pawCost != null ? "{P}".repeat(pawCost[mi]) + " — " : "")
                            + (observer != null ? fit(ab.getDescription()).replace("CARDNAME", host).replace("NICKNAME", host) : oneLine(ab.getDescription())));
                }
                if (allowRepeat) {
                    List<Integer> rp = decideModesRepeat(smart, "MODAL SPELL — choose mode(s) for " + labelOf(sa) + xNote(sa)
                            + " (you may choose the same mode more than once"
                            + (paw >= 0 ? "; the modes' {P} may total at most " + paw : "") + ").", labels, Math.max(0, min), num,
                            pawCost, paw);
                    if (rp == null || rp.size() < min) {
                        List<AbilitySub> defPick = defaultModes(possible, min, num, true, pawCost, paw);
                        auto((modesOverBudget ? "modes(repeat,over-budget)->" : "modes(repeat,unparsed)->first") + defPick.size());
                        return defPick;
                    }
                    List<AbilitySub> out = new ArrayList<>();
                    StringBuilder log = new StringBuilder();
                    for (int i : rp) { out.add(possible.get(i)); if (log.length() > 0) log.append(" + "); log.append(labels.get(i)); }
                    p("[ROUTER] modes(repeat): " + log);
                    return out;
                }
                String mq = "MODAL SPELL — choose mode(s) for " + labelOf(sa) + xNote(sa)
                        + (escalateCost(sa) != null ? "; each mode beyond the first adds escalate " + costText(escalateCost(sa).toSimpleString())
                           : priced ? "; each mode adds its cost" : "") + ".";
                List<Integer> picks = decideMany(smart, "modes", mq, labels, Math.max(0, min), Math.min(num, possible.size()));
                for (int attempt = 0; priced && picks != null && picks.size() > 1 && !modesPayable(sa, pick(possible, picks)); attempt++) {
                    if (attempt == 1) { picks = List.of(0); auto("modes(together unpayable)->first payable"); break; }
                    picks = decideMany(smart, "modes", mq + " That set's costs together are more than you can pay; pick a set you can pay.",
                            labels, Math.max(0, min), Math.min(num, possible.size()));
                }
                // decideMany's null covers BOTH bridge-down and still-unparseable-after-retry — same
                // deterministic legal default as the no-budget branch above, never Forge AI.
                if (picks == null || picks.size() < min) {
                    List<AbilitySub> defPick = defaultModes(possible, min, num, false);
                    auto("modes(unparsed)->first" + defPick.size());
                    return defPick;
                }
                List<AbilitySub> out = new ArrayList<>();
                StringBuilder log = new StringBuilder();
                for (int i : picks) { out.add(possible.get(i)); if (log.length() > 0) log.append(" + "); log.append(labels.get(i)); }
                p("[ROUTER] modes: " + log);
                return out;
            } catch (Exception e) {
                List<AbilitySub> defPick = possible == null ? new ArrayList<>() : budgetSafeDefault(sa, possible, min, num, allowRepeat);
                auto("modes(err)->first" + defPick.size());
                return defPick;
            }
        }

        // ---------------- choices Forge's AI used to make outright (never overridden) ----------------
        // Work plan priority 0 (A), 2026-10-15: each was PlayerControllerAi's own decision with no row. Each is
        // now asked of the seat; with no answer it takes a fixed legal option, recorded. Never Forge's AI.

        /** One option of {@code labels}, or -1 for {@code noneLabel}; {@code dflt} when the seat cannot answer. */
        private int askOne(String type, String question, List<String> labels, String noneLabel, int dflt) {
            if (labels.size() == 1 && noneLabel == null) { auto(type + "(forced-only-legal)"); return 0; }
            if (smartOk() || fastOk()) {
                int c = decideOne(smartOk() ? smart : fast, type, question, labels, noneLabel);
                if (c >= 0) return c;
                if (c == -1 && noneLabel != null && lastDeclineLogged) return -1;
            }
            auto(type + "(no-answer)->" + (dflt < 0 ? "none" : "first"));
            return dflt;
        }

        /** Between {@code min} and {@code max} of {@code labels}; the first {@code min} when the seat cannot answer. */
        private List<Integer> askMany(String type, String question, List<String> labels, int min, int max) {
            if (smartOk() || fastOk()) {
                List<Integer> picks = decideMany(smartOk() ? smart : fast, type, question, labels, min, max);
                if (picks != null && picks.size() >= min && picks.size() <= max) return picks;
            }
            List<Integer> first = new ArrayList<>();
            for (int i = 0; i < min && i < labels.size(); i++) first.add(i);
            auto(type + "(no-answer)->first " + first.size());
            return first;
        }

        /** Fact or Fiction-style: which pile the seat takes. */
        @Override
        public boolean chooseCardsPile(SpellAbility sa, CardCollectionView pile1, CardCollectionView pile2, String faceUp) {
            clearLast();
            List<String> labels = java.util.Arrays.asList("Pile 1: " + pileLabel(pile1, faceUp), "Pile 2: " + pileLabel(pile2, faceUp));
            return askOne("pile", "Choose a pile" + srcAsk(sa) + ".", labels, null, 0) == 0;
        }

        private String pileLabel(CardCollectionView pile, String faceUp) {
            if (pile == null || pile.isEmpty()) return "(empty)";
            if ("False".equals(faceUp)) return pile.size() + " face-down card" + (pile.size() == 1 ? "" : "s");
            List<String> n = new ArrayList<>();
            for (Card c : pile) n.add(labelOf(c));
            return String.join(", ", n);
        }

        /** Leyline / Chancellor / Gemstone Caverns: which opening-hand abilities the seat uses. */
        @Override
        public List<SpellAbility> chooseSaToActivateFromOpeningHand(List<SpellAbility> usable) {
            clearLast();
            if (usable == null || usable.isEmpty()) return new ArrayList<>();
            List<String> labels = new ArrayList<>();
            for (SpellAbility a : usable) labels.add(labelOf(a));
            List<SpellAbility> out = new ArrayList<>();
            for (int i : askMany("opening-hand", "OPENING HAND: these cards in your hand can be used as the game "
                    + "begins. Choose which to use (any number, none is allowed).", labels, 0, labels.size()))
                out.add(usable.get(i));
            return out;
        }

        /** Several colours at once (between min and max of the options). */
        @Override
        public ColorSet chooseColors(String message, SpellAbility sa, int min, int max, ColorSet options) {
            clearLast();
            List<MagicColor.Color> present = new ArrayList<>();
            if (options != null) for (MagicColor.Color c : options) present.add(c);
            if (present.isEmpty()) return ColorSet.C;
            List<String> labels = new ArrayList<>();
            for (MagicColor.Color c : present) labels.add(c.getName());
            int lo = Math.max(0, Math.min(min, present.size())), hi = Math.max(lo, Math.min(max, present.size()));
            byte mask = 0;
            for (int i : askMany("colors", "Choose colors" + (message == null || message.isEmpty() ? "" : ": " + fit(message))
                    + srcAsk(sa) + ".", labels, lo, hi))
                mask |= present.get(i).getColorMask();
            return ColorSet.fromMask(mask);
        }

        /** Which kind of counter. */
        @Override
        public CounterType chooseCounterType(List<CounterType> options, SpellAbility sa, String prompt, Map<String, Object> params) {
            clearLast();
            if (options == null || options.isEmpty()) return null;
            List<String> labels = new ArrayList<>();
            for (CounterType t : options) labels.add(t.getName());
            return options.get(askOne("counter-type", (prompt == null || prompt.isEmpty() ? "Choose a kind of counter" : fit(prompt))
                    + srcAsk(sa) + ".", labels, null, 0));
        }

        /** Which of several replacement effects applies first. */
        @Override
        public forge.game.replacement.ReplacementEffect chooseSingleReplacementEffect(List<forge.game.replacement.ReplacementEffect> possible) {
            clearLast();
            if (possible == null || possible.isEmpty()) return null;
            List<String> labels = new ArrayList<>();
            for (forge.game.replacement.ReplacementEffect re : possible)
                labels.add(safeName(re.getHostCard()) + ": " + named(fit(String.valueOf(re.getDescription())), re.getHostCard()));
            return possible.get(askOne("replacement-order", "Several replacement effects apply to the same event. "
                    + "Choose which applies first.", labels, null, 0));
        }

        /** Which of several static abilities applies (as replacement order, for statics). */
        @Override
        public forge.game.staticability.StaticAbility chooseSingleStaticAbility(List<forge.game.staticability.StaticAbility> possible) {
            if (PRICING_FOR_SNAPSHOT) {
                return possible == null || possible.isEmpty() ? null : possible.get(0);   // display pricing, not a payment: above
            }
            clearLast();
            if (possible == null || possible.isEmpty()) return null;
            // One candidate left in the cost-reduction loop: the engine's human controller takes it
            // without prompting (no full control), so the seat does too — silently, never an auto row
            // and never a one-option ask the model has no decision inside.
            if (possible.size() == 1) return possible.get(0);
            List<String> labels = new ArrayList<>();
            for (forge.game.staticability.StaticAbility st : possible)
                labels.add(safeName(st.getHostCard()) + ": " + fit(String.valueOf(st)));
            return possible.get(askOne("static-order", "Several static abilities apply. Choose which applies.", labels, null, 0));
        }

        /** Edict-style destroy: which permanents the seat destroys. */
        @Override
        public CardCollectionView choosePermanentsToDestroy(SpellAbility sa, int min, int max, CardCollectionView valid, String message) {
            clearLast();
            if (valid == null || valid.isEmpty()) return new CardCollection();
            List<Card> opts = new ArrayList<>(valid);
            int lo = Math.min(min, opts.size()), hi = Math.max(lo, Math.min(max, opts.size()));
            if (lo == opts.size()) { auto("destroy(forced-all)"); return new CardCollection(opts); }
            CardCollection out = new CardCollection();
            for (int i : askMany("destroy", (message == null || message.isEmpty() ? "Choose permanents to DESTROY" : fit(message))
                    + srcAsk(sa) + " (choose " + lo + (hi > lo ? " to " + hi : "") + ").", cardLabels(opts), lo, hi))
                out.add(opts.get(i));
            return out;
        }

        /** One card from each of several categories ("choose a creature, an artifact, ..."). */
        @Override
        public CardCollection chooseCardsForEffectMultiple(Map<String, CardCollection> validMap, SpellAbility sa, String title, boolean isOptional) {
            clearLast();
            CardCollection out = new CardCollection();
            if (validMap == null) return out;
            for (Map.Entry<String, CardCollection> e : validMap.entrySet()) {
                List<Card> opts = new ArrayList<>(e.getValue());
                opts.removeAll(out);
                if (opts.isEmpty()) continue;
                int c = askOne("cards-for-effect", (title == null || title.isEmpty() ? "Choose" : fit(title)) + srcAsk(sa)
                        + " — " + e.getKey() + ".", cardLabels(opts), isOptional ? "None" : null, isOptional ? -1 : 0);
                if (c >= 0) out.add(opts.get(c));
            }
            return out;
        }

        /** Which keyword a pump grants. */
        @Override
        public String chooseKeywordForPump(List<String> options, SpellAbility sa, String prompt, Card tgtCard) {
            clearLast();
            if (options == null || options.isEmpty()) return null;
            return options.get(askOne("keyword", (prompt == null || prompt.isEmpty() ? "Choose a keyword" : fit(prompt))
                    + (tgtCard != null ? " for " + labelOf(tgtCard) : "") + srcAsk(sa) + ".", new ArrayList<>(options), null, 0));
        }

        /** Which spell an effect acts on. */
        @Override
        public SpellAbility chooseSingleSpellForEffect(List<SpellAbility> spells, SpellAbility sa, String title, Map<String, Object> params) {
            clearLast();
            if (spells == null || spells.isEmpty()) return null;
            List<String> labels = new ArrayList<>();
            for (SpellAbility x : spells) labels.add(labelOf(x));
            return spells.get(askOne("spell-for-effect", (title == null || title.isEmpty() ? "Choose a spell or ability" : fit(title))
                    + srcAsk(sa) + ".", labels, null, 0));
        }

        /** Several spells an effect acts on: the seat picks them one at a time. */
        @Override
        public List<SpellAbility> chooseSpellAbilitiesForEffect(List<SpellAbility> spells, SpellAbility sa, String title, int num,
                Map<String, Object> params) {
            clearLast();
            List<SpellAbility> out = new ArrayList<>();
            if (spells == null || spells.isEmpty()) return out;
            List<String> labels = new ArrayList<>();
            for (SpellAbility x : spells) labels.add(labelOf(x));
            int n = Math.min(num, spells.size());
            for (int i : askMany("spell-for-effect", (title == null || title.isEmpty() ? "Choose spells or abilities" : fit(title))
                    + srcAsk(sa) + " (choose " + n + ").", labels, n, n))
                out.add(spells.get(i));
            return out;
        }

        /** Clash: whether the revealed card goes on top of the library. */
        @Override
        public boolean willPutCardOnTop(Card c) {
            clearLast();
            return askOne("clash", "CLASH: you revealed " + labelOf(c) + " from the top of your library. Put it back on top, "
                    + "or on the bottom?", java.util.Arrays.asList("Top", "Bottom"), null, 0) == 0;
        }

        /** An optional static ability ("you may"): whether it applies. */
        @Override
        public boolean confirmStaticApplication(Card hostCard, PlayerActionConfirmMode mode, String message, String logic) {
            clearLast();
            return askOne("confirm-static", (message == null || message.isEmpty() ? "Apply this optional effect?" : fit(message))
                    + (hostCard != null ? " (" + labelOf(hostCard) + ")" : ""), java.util.Arrays.asList("Yes", "No"), null, 0) == 0;
        }

        /** "Discard N cards unless you discard a card of a type": the seat picks the cards. */
        @Override
        public CardCollectionView chooseCardsToDiscardUnlessType(int num, CardCollectionView hand, String[] unlessTypes, SpellAbility sa) {
            clearLast();
            if (hand == null || hand.isEmpty()) return new CardCollection();
            List<Card> opts = new ArrayList<>(hand);
            String types = unlessTypes == null ? "" : String.join(" or ", unlessTypes).toLowerCase();
            List<String> labels = cardLabels(opts);
            List<Integer> picks = askMany("discard-unless", "DISCARD" + srcAsk(sa) + ": discard " + num + " card"
                    + (num == 1 ? "" : "s") + ", or a single " + types + " card instead. Choose the cards.", labels, 1,
                    Math.min(num, opts.size()));
            CardCollection out = new CardCollection();
            for (int i : picks) out.add(opts.get(i));
            boolean single = out.size() == 1 && unlessTypes != null && out.getFirst().isValid(unlessTypes, getPlayer(), null, sa);
            if (!single && out.size() < Math.min(num, opts.size())) {   // not a legal discard: fill to the count
                for (Card c : opts) { if (out.size() >= Math.min(num, opts.size())) break; if (!out.contains(c)) out.add(c); }
                auto("discard-unless(short)->filled to " + out.size());
            }
            return out;
        }

        /** Which face of a card (a predicate over the database, or a list). */
        @Override
        public ICardFace chooseSingleCardFace(SpellAbility sa, List<ICardFace> faces, String message) {
            clearLast();
            if (faces == null || faces.isEmpty()) return null;
            List<String> labels = new ArrayList<>();
            for (ICardFace f : faces) labels.add(f.getName());
            return faces.get(askOne("card-face", (message == null || message.isEmpty() ? "Choose a card face" : fit(message))
                    + srcAsk(sa) + ".", labels, null, 0));
        }

        /** Which state of a card (a split, adventure or double-faced card's half). */
        @Override
        public forge.game.card.CardState chooseSingleCardState(SpellAbility sa, List<forge.game.card.CardState> states, String message,
                Map<String, Object> params) {
            clearLast();
            if (states == null || states.isEmpty()) return null;
            List<String> labels = new ArrayList<>();
            for (forge.game.card.CardState st : states) labels.add(st.getName() + " — " + fit(String.valueOf(st.getOracleText())));
            return states.get(askOne("card-state", (message == null || message.isEmpty() ? "Choose" : fit(message))
                    + srcAsk(sa) + ".", labels, null, 0));
        }

        /** How many times to pay a keyword cost (casualty and the like); 0..max. The engine passes
         *  Integer.MAX_VALUE for a pay-any-number cost (Multikicker, Replicate, Squad:
         *  GameActionUtil.addExtraKeywordCost), so a huge range is stated like chooseNumber's, never
         *  enumerated — building 0..2147483647 labels would exhaust the heap before any ask went out. */
        @Override
        public int chooseNumberForKeywordCost(SpellAbility sa, Cost cost, forge.game.keyword.KeywordInterface keyword, String prompt, int max) {
            clearLast();
            if (max <= 0) return 0;
            // Only the counts the cast can pay for are offered: conspire was answered "pay it" with no two creatures to
            // tap, and the whole cast failed (random gauntlet). The engine's own test, every part of the cost.
            int payable = payableKeywordTimes(sa, cost, max);
            if (payable < max) {
                p("[ROUTER] keyword-cost: " + cost.toSimpleString() + " can be paid " + payable + " time(s), not " + max);
                max = payable;
                if (max <= 0) { auto("keyword-cost(none payable)->0"); return 0; }
            }
            // The menu cut: a numbered option line per candidate keeps the ask inside the readable
            // card-decision shape the model already answers (two digits of menu, like the other asks);
            // past 12 the enumeration is unreadable (the OOM why is the javadoc's above).
            if (max > 12) return chooseWideKeywordCost(sa, cost, prompt, max);
            List<String> labels = new ArrayList<>();
            for (int i = 0; i <= max; i++) labels.add(String.valueOf(i));
            int c = askOne("keyword-cost", (prompt == null || prompt.isEmpty() ? "How many times do you pay " + costText(cost.toSimpleString()) : fit(prompt))
                    + srcAsk(sa) + "?", labels, null, 0);
            return Math.max(0, c);
        }

        /** The most times, up to {@code max}, that {@code cost} can be added to {@code sa}'s cost and still be paid.
         *  Each added copy only makes the total harder to pay, so the search doubles until a count fails and then
         *  halves between: it costs about twice the answer, never the engine's range (Squad passes Integer.MAX_VALUE,
         *  and a random answer of 1,212,328,817 squads hung the payment). A cost that is still payable 1024 times
         *  adds nothing a payment can run out of, and the engine's range stands. */
        static int payableKeywordTimes(SpellAbility sa, Cost cost, int max) {
            if (sa == null || cost == null || sa.getPayCosts() == null || sa.getActivatingPlayer() == null) return max;
            java.util.function.IntPredicate ok = n -> {
                try {
                    Cost total = sa.getPayCosts().copy();
                    for (int i = 0; i < n; i++) total.add(cost);
                    boolean was = PRICING_FOR_SNAPSHOT;
                    PRICING_FOR_SNAPSHOT = true;
                    try { return legallyPayable(total, sa, sa.getActivatingPlayer(), false); } finally { PRICING_FOR_SNAPSHOT = was; }
                } catch (Exception e) { return true; }   // cannot price it: offer the engine's range
            };
            int lo = 0, hi = 1;     // ok(lo) is assumed (paying none adds nothing)
            while (ok.test(hi)) {
                if (hi >= max || hi >= 1024) return max;
                lo = hi;
                hi = (int) Math.min(max, 2L * hi);
            }
            while (hi - lo > 1) { int mid = (lo + hi) >>> 1; if (ok.test(mid)) lo = mid; else hi = mid; }
            return lo;
        }

        /** The 0..max "any number of times" ask when the engine's own range is bigger than the menu:
         *  the range is stated (a legal domain, like the spinner the human controller shows and
         *  chooseNumber's wide-number asks), the number a resource statement, answered NUMBER:. */
        private int chooseWideKeywordCost(SpellAbility sa, Cost cost, String prompt, int max) {
            if (!fastOk() && !smartOk()) { auto("keyword-cost(no-model)->0"); return 0; }
            LLMBridge b = smartOk() ? smart : fast;
            String base = snapshot() + (observer != null ? "\nDECISION: " : "\n")
                    + (prompt == null || prompt.isEmpty() ? "How many times do you pay " + costText(cost.toSimpleString()) : fit(prompt))
                    + srcAsk(sa) + "\nEvery integer from 0 through " + max
                    + " inclusive is legal. Reply with exactly NUMBER: <integer>.";
            try {
                for (int attempt = 0; attempt < 2 && (b == smart ? smartOk() : fastOk()); attempt++) {
                    offerAnswer(b, AnswerTool.Spec.number(0, max));
                    String reply = ask(b, base + (attempt == 0 ? "" : "\nInvalid answer. Use exactly NUMBER: <integer> within the range."));
                    noteAnswerVia(attempt);
                    if (reply == null) break;
                    AnswerTool.Answer ta = toolAnswer();
                    Integer value = ta != null ? ta.number : parseNumberReply(reply, 0, max);
                    if (value != null) {
                        evalDec("keyword-cost", -1, value, tierOf(b == smart), 0, null,
                                Lists.newArrayList("integer range [0, " + max + "]"));
                        p("[ROUTER] keyword-cost: " + value);
                        return Math.max(0, value);
                    }
                }
            } catch (Exception e) { /* deterministic fallback below, never Forge AI */ }
            auto("keyword-cost(unparsed)->0");
            return 0;
        }

        /** Harmonize's power, from the powers the cast can be paid with (the engine offers 0 to the largest power,
         *  and a power with no creature to tap, or one the mana cannot cover after it, fails the cast). The tap that
         *  follows (addKeywordCost) is then this choice, not a second question. */
        private int harmonizeChosen = -1;
        private int chooseHarmonizePower(SpellAbility sa, int min, int max) {
            harmonizeChosen = -1;
            List<Integer> powers = harmonizePowers(sa, getPlayer());
            powers.removeIf(n -> n < min || n > max);
            if (powers.isEmpty()) { auto("harmonize(none payable)->" + min); return min; }
            int pick;
            if (powers.size() == 1) { auto("harmonize(only payable)->" + powers.get(0)); pick = powers.get(0); }
            else {
                List<String> labels = new ArrayList<>();
                for (int n : powers) labels.add(n == 0 ? "0: tap no creature, pay the full cost"
                        : n + ": tap a creature with power " + n + ", the spell costs {" + n + "} less");
                pick = powers.get(askOne("harmonize", "HARMONIZE" + srcAsk(sa) + ": choose the power of the creature to tap.",
                        labels, null, labels.size() - 1));
            }
            harmonizeChosen = pick;
            return pick;
        }

        /** Whether to pay an optional keyword cost (one time). */
        @Override
        public boolean addKeywordCost(SpellAbility sa, Cost cost, forge.game.keyword.KeywordInterface keyword, String prompt) {
            if (sa != null && sa.isHarmonize() && harmonizeChosen >= 0) {
                boolean tap = harmonizeChosen > 0;
                harmonizeChosen = -1;
                auto("harmonize(tap follows the power chosen)->" + (tap ? "YES" : "NO"));
                return tap;
            }
            return chooseNumberForKeywordCost(sa, cost, keyword, prompt, 1) == 1;
        }

        /** How much a cost reduction reduces by (min..max). */
        @Override
        public int chooseNumberForCostReduction(SpellAbility sa, int min, int max) {
            // Pricing (the menu, the hand tags, an affordability test) takes the whole reduction without asking: an ask
            // there builds a snapshot, whose pricing reaches this ask again (Mana Matrix: StackOverflowError, #225).
            if (PRICING_FOR_SNAPSHOT || !payingForReal || askingConvoke) return max;
            clearLast();
            if (max <= min) return min;
            List<String> labels = new ArrayList<>();
            for (int i = min; i <= max; i++) labels.add(String.valueOf(i));
            return min + Math.max(0, askOne("cost-reduction", "Choose how much to reduce the cost by" + srcAsk(sa) + ".",
                    labels, null, labels.size() - 1));
        }

        /** Spellskite-style redirection: which target of which spell is redirected. */
        @Override
        public org.apache.commons.lang3.tuple.Pair<forge.game.spellability.SpellAbilityStackInstance, forge.game.GameObject> chooseTarget(
                SpellAbility saSrc, List<org.apache.commons.lang3.tuple.Pair<forge.game.spellability.SpellAbilityStackInstance, forge.game.GameObject>> allTargets) {
            clearLast();
            if (allTargets == null || allTargets.isEmpty()) return null;
            List<String> labels = new ArrayList<>();
            for (org.apache.commons.lang3.tuple.Pair<forge.game.spellability.SpellAbilityStackInstance, forge.game.GameObject> t : allTargets)
                labels.add(safeName(t.getLeft().getSourceCard()) + " — its target " + labelOf((GameEntity) (t.getRight() instanceof GameEntity ? t.getRight() : null)));
            return allTargets.get(askOne("redirect", "REDIRECT" + srcAsk(saSrc) + ": choose which spell's target to change.", labels, null, 0));
        }

        /** Change the target of a spell or ability (Misdirection, Deflecting Swat): whether, and to what. Forge's AI
         *  never did (it returned null: targets unchanged). A single-target ability is asked; any other keeps its
         *  targets, recorded. */
        @Override
        public forge.game.spellability.TargetChoices chooseNewTargetsFor(SpellAbility ability,
                java.util.function.Predicate<forge.game.GameObject> filter, boolean optional) {
            clearLast();
            SpellAbility sa = ability instanceof forge.game.trigger.WrappedAbility
                    ? ((forge.game.trigger.WrappedAbility) ability).getWrappedAbility() : ability;
            if (sa == null || !sa.usesTargeting()) return null;
            forge.game.spellability.TargetChoices old = sa.getTargets();
            TargetRestrictions tr = sa.getTargetRestrictions();
            if (tr == null || old.isEmpty()) return null;
            if (old.size() > 1 || sa.getMaxTargets() > 1) return chooseNewTargetSet(sa, tr, old, filter);
            forge.game.GameObject current = old.get(0);
            List<forge.game.GameObject> cands = new ArrayList<>();
            for (forge.game.GameObject ge : targetCandidates(sa, tr))
                if (ge != current && (filter == null || filter.test(ge)) && sa.canTarget(ge)) cands.add(ge);
            if (cands.isEmpty()) { auto("new-targets(none-legal)->unchanged"); return null; }
            List<String> labels = new ArrayList<>();
            for (forge.game.GameObject ge : cands) labels.add(targetLabel(ge));
            int c = askOne("new-targets", "CHANGE TARGET: " + labelOf(sa) + " targets "
                    + targetLabel(current) + ". Choose its new target"
                    + (optional ? ", or keep it" : "") + ".", labels, optional ? "Keep the current target" : null, optional ? -1 : 0);
            if (c < 0) return null;
            int portion = old.getTotalDividedValue();
            sa.resetTargets();
            sa.getTargets().add(cands.get(c));
            if (sa.isDividedAsYouChoose()) sa.addDividedAllocation(cands.get(c), portion);   // the whole division moves
            return sa.getTargets();
        }

        /** A spell or copy with several targets: the seat picks the whole new set, up to as many as it had
         *  (as a human chooses). Picking none, or a set the spell cannot have, keeps the old targets. A divided
         *  spell keeps its portions: exactly as many new targets, each taking one old portion the seat assigns. */
        private forge.game.spellability.TargetChoices chooseNewTargetSet(SpellAbility sa, TargetRestrictions tr,
                forge.game.spellability.TargetChoices old, java.util.function.Predicate<forge.game.GameObject> filter) {
            List<GameEntity> cands = new ArrayList<>();
            sa.resetTargets();   // a candidate is judged without the old set (unique-target restrictions)
            try {
                for (GameEntity ge : tr.getAllCandidates(sa, false))
                    if ((filter == null || filter.test(ge)) && sa.canTarget(ge)) cands.add(ge);
            } finally { sa.setTargets(old); }
            if (cands.isEmpty()) { auto("new-targets(none-legal)->unchanged"); return null; }
            boolean divided = sa.isDividedAsYouChoose();
            int max = old.size();
            List<String> labels = new ArrayList<>();
            List<String> now = new ArrayList<>();
            for (GameEntity ge : cands) labels.add(labelOf(ge) + (old.contains(ge) ? " (a current target)" : ""));
            for (forge.game.GameObject o : old) now.add(o instanceof GameEntity ? labelOf((GameEntity) o) : String.valueOf(o));
            List<Integer> picks = askMany("new-targets", "CHANGE TARGETS: " + labelOf(sa) + " targets "
                    + String.join(", ", now) + (divided
                        ? " (divided " + old.getDividedValues() + "). Choose exactly " + max + " new targets; each takes one"
                          + " of those portions"
                        : ". Choose its new targets, up to " + max + " (at least " + sa.getMinTargets() + ")")
                    + ", or none to keep them.", labels, 0, max);
            if (picks.isEmpty()) return null;
            if (divided && picks.size() != max) { auto("new-targets(divided-count)->unchanged"); return null; }
            List<Integer> portions = divided ? new ArrayList<>(old.getDividedValues()) : null;
            sa.resetTargets();
            for (int i : picks) {
                GameEntity ge = cands.get(i);
                if (!sa.canTarget(ge)) { sa.setTargets(old); auto("new-targets(illegal-set)->unchanged"); return null; }
                sa.getTargets().add(ge);
                if (divided) sa.addDividedAllocation(ge, portions.remove(portionFor(ge, portions)));
            }
            if (!sa.isTargetNumberValid()) { sa.setTargets(old); auto("new-targets(too-few)->unchanged"); return null; }
            return sa.getTargets();
        }

        /** Which of the old portions a new target of a divided spell takes; the only kind left needs no ask. */
        private int portionFor(GameEntity ge, List<Integer> portions) {
            if (new java.util.HashSet<>(portions).size() == 1) return 0;
            List<String> labels = new ArrayList<>();
            for (Integer v : portions) labels.add(String.valueOf(v));
            return askOne("new-targets", "DIVIDE: which portion goes to " + labelOf(ge) + "?", labels, null, 0);
        }

        /** Which mana in the pool pays (several of different kinds float). */
        @Override
        public forge.game.mana.Mana chooseManaFromPool(List<forge.game.mana.Mana> manaChoices) {
            clearLast();
            if (manaChoices == null || manaChoices.isEmpty()) return null;
            java.util.Set<String> kinds = new java.util.HashSet<>();
            for (forge.game.mana.Mana m : manaChoices) kinds.add(m.toString());
            if (kinds.size() == 1) return manaChoices.get(0);   // all alike: nothing to choose
            // Pricing (the menu, the hand tags) takes the first without asking: an ask builds a snapshot, whose
            // pricing reached this ask again (floating {R}{G} from Lukka's +1: StackOverflowError, keyword gauntlet).
            if (PRICING_FOR_SNAPSHOT || !payingForReal) return manaChoices.get(0);
            List<String> labels = new ArrayList<>();
            for (forge.game.mana.Mana m : manaChoices)
                labels.add(m + (m.getSourceCard() == null ? "" : " (from " + safeName(m.getSourceCard()) + ")"));
            return manaChoices.get(askOne("mana-from-pool", "MANA POOL: choose which floating mana pays.", labels, null, 0));
        }

        /** True while this seat's real mana payment runs (payManaCost). The engine also calls the convoke chooser
         *  from affordability tests (CostAdjustment, test=true) without telling the controller which. */
        private boolean payingForReal = false;
        /** The mana cost the seat's real payment is paying, while it runs. */
        private forge.card.mana.ManaCost payingCost = null;
        /** The spell or ability the seat's real payment is paying for, while it runs. */
        private SpellAbility payingFor = null;
        private boolean askingConvoke = false;

        /** Test seam: runs {@code f} as inside this seat's real mana payment. */
        <T> T inRealPaymentForTest(java.util.function.Supplier<T> f) {
            boolean was = payingForReal;
            payingForReal = true;
            try { return f.get(); } finally { payingForReal = was; }
        }

        /** Convoke / improvise: which untapped creatures or artifacts help pay, each for a symbol it can pay
         *  (a creature pays generic or a symbol of its colour; an artifact, generic). Forge's AI paid nothing so. */
        @Override
        public Map<Card, forge.card.mana.ManaCostShard> chooseCardsForConvokeOrImprovise(SpellAbility sa, forge.card.mana.ManaCost manaCost,
                CardCollectionView untappedCards, boolean artifacts, boolean creatures, Integer maxReduction) {
            clearLast();
            Map<Card, forge.card.mana.ManaCostShard> out = new java.util.HashMap<>();
            if (untappedCards == null || untappedCards.isEmpty() || manaCost == null) return out;
            List<Card> opts = new ArrayList<>();
            for (Card c : untappedCards) if ((artifacts && c.isArtifact()) || (creatures && c.isCreature())) opts.add(c);
            if (opts.isEmpty()) return out;
            // The symbols still to pay, coloured first: each chosen card takes the first one it can pay.
            List<forge.card.mana.ManaCostShard> shards = new ArrayList<>();
            for (forge.card.mana.ManaCostShard sh : manaCost) if (!sh.isGeneric()) shards.add(sh);
            for (int i = 0; i < manaCost.getGenericCost(); i++) shards.add(forge.card.mana.ManaCostShard.GENERIC);
            int max = Math.min(opts.size(), maxReduction != null ? Math.min(maxReduction, shards.size()) : shards.size());
            if (max <= 0) return out;
            // An affordability test (the menu asking "can this be cast?"): every helper that could pay is counted,
            // nothing is asked. The real payment asks the seat.
            List<Integer> picks;
            // A nested call (the ask's own snapshot tests affordability, which calls this again) is a probe too.
            if (PRICING_FOR_SNAPSHOT || !payingForReal || askingConvoke) {
                picks = new ArrayList<>();
                for (int i = 0; i < max; i++) picks.add(i);
            } else {
            String how = creatures && artifacts ? "creatures or artifacts" : creatures ? "creatures (convoke)" : "artifacts (improvise)";
                askingConvoke = true;
                try {
                    String q = "CONVOKE/IMPROVISE: you are paying " + manaCost + " for " + labelOf(sa) + ". Tap untapped "
                            + how + " to pay part of it (up to " + max + "; none pays it all with mana). Choose which.";
                    picks = askMany("convoke", q, cardLabels(opts), 0, max);
                    // The rest must stay payable with the sources left (what this payment holds untapped is not
                    // among them): the keyword gauntlet's Angel of Salvation kept a land on the strength of its
                    // convokers, then convoked fewer, and the payment failed. Asked again once, then all convoke.
                    for (int attempt = 0; !restPayable(sa, assignConvoke(opts, picks, shards, creatures), shards); attempt++) {
                        if (attempt == 1) {
                            picks = new ArrayList<>();
                            for (int i = 0; i < max; i++) picks.add(i);
                            auto("convoke(rest unpayable)->all " + max);
                            break;
                        }
                        picks = askMany("convoke", q + " With those, the rest cannot be paid by your other sources; choose more.",
                                cardLabels(opts), 0, max);
                    }
                } finally {
                    askingConvoke = false;
                }
            }
            return assignConvoke(opts, picks, shards, creatures);
        }

        /** Each picked card pays the first symbol it can (a creature: generic or its colour; an artifact: generic). */
        private static Map<Card, forge.card.mana.ManaCostShard> assignConvoke(List<Card> opts, List<Integer> picks,
                List<forge.card.mana.ManaCostShard> shards, boolean creatures) {
            Map<Card, forge.card.mana.ManaCostShard> out = new java.util.HashMap<>();
            List<forge.card.mana.ManaCostShard> left = new ArrayList<>(shards);
            for (int i : picks) {
                Card c = opts.get(i);
                for (java.util.Iterator<forge.card.mana.ManaCostShard> it = left.iterator(); it.hasNext(); ) {
                    forge.card.mana.ManaCostShard sh = it.next();
                    boolean ok = sh.isGeneric() || (creatures && c.isCreature() && c.getColor().hasAnyColor(sh.getColorMask()));
                    if (ok) { out.put(c, sh); it.remove(); break; }
                }
            }
            return out;
        }

        /** Whether the symbols {@code convoked} leaves can be paid by the sources this payment may still tap. */
        private boolean restPayable(SpellAbility sa, Map<Card, forge.card.mana.ManaCostShard> convoked, List<forge.card.mana.ManaCostShard> shards) {
            List<forge.card.mana.ManaCostShard> left = new ArrayList<>(shards);
            for (forge.card.mana.ManaCostShard sh : convoked.values()) left.remove(sh);
            int generic = 0;
            StringBuilder sb = new StringBuilder();
            for (forge.card.mana.ManaCostShard sh : left) { if (sh.isGeneric()) generic++; else sb.append(' ').append(sh.toShortString()); }
            String str = ((generic > 0 ? String.valueOf(generic) : "") + sb).trim();
            if (str.isEmpty()) return true;
            Player me = getPlayer();
            for (Card c : convoked.keySet()) forge.ai.AiCardMemory.rememberCard(me, c, forge.ai.AiCardMemory.MemorySet.HELD_MANA_SOURCES_FOR_NEXT_SPELL);
            try {
                return ComputerUtilMana.getManaSourcesToPayCost(new forge.game.mana.ManaCostBeingPaid(new forge.card.mana.ManaCost(str)), sa, me, false) != null;
            } catch (Exception e) {
                return true;
            } finally {
                for (Card c : convoked.keySet()) forge.ai.AiCardMemory.forgetCard(me, c, forge.ai.AiCardMemory.MemorySet.HELD_MANA_SOURCES_FOR_NEXT_SPELL);
            }
        }

        /** An attack or block tax (Propaganda, Ghostly Prison): whether to pay it. Paying is the engine's mechanics.
         *  The engine's prompt carries raw engine ids ("declare Grizzly Bears (2) an attacker"); the faithful ask
         *  renders them as seat handles (seatText), the release keeps their profile bytes. */
        @Override
        public boolean payCombatCost(Card card, Cost cost, SpellAbility sa, String prompt) {
            clearLast();
            if (!legallyPayable(sa, getPlayer(), sa != null && sa.isTrigger())) { auto("combat-cost(cannot-pay)->no"); return false; }
            int c = askOne("combat-cost", (prompt == null || prompt.isEmpty() ? "Pay " + costText(cost.toSimpleString()) : seatText(prompt))
                    + " for " + labelOf(card) + "? If you do not pay, it does not attack or block.",
                    java.util.Arrays.asList("Pay", "Do not pay"), null, 0);
            return c == 0 && super.payCombatCost(card, cost, sa, prompt);
        }

        // REMAINING INVISIBLE SURFACE (documented, not logged): mana tapping/payment. The per-source
        // which-land-to-tap choices happen entirely inside ComputerUtilMana (forge-ai) and never cross the
        // PlayerController boundary (the controller only sees payManaCost(cost)->boolean), so they cannot
        // be surfaced without modifying the pinned engine.

        /** Null-safe label snapshot (take it BEFORE calling super when super may mutate/reorder). */
        private List<String> cardLabels(Iterable<Card> cards) {
            List<String> out = new ArrayList<>();
            try { if (cards != null) for (Card c : cards) out.add(c == null ? "(null)" : labelOf(c)); }
            catch (Exception ignore) { }
            return out;
        }
        /** A card's name in a question: the engine's, without an engine id (Forge names an effect
         *  "Devourer of Destiny (48)'s Effect", runs/luna-modern/g11). A face-down card keeps the engine's empty name. */
        static String safeName(Card c) {
            try { return c == null ? "?" : SeatObservation.withoutEngineId(c, c.getName()); } catch (Exception e) { return "?"; }
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

        /** Damage-assignment order among multiple blockers of one attacker: the seat's choice. It was
         *  Forge's AiBlockController's, only logged (tier=ai); a damage order decides which blocker dies. */
        @Override
        public CardCollection orderBlockers(Card attacker, CardCollection blockers) {
            return orderByModel(blockers, "COMBAT DAMAGE ORDER: " + safeName(attacker) + " is blocked by "
                    + (blockers == null ? 0 : blockers.size()) + " creatures. List all of them in the order "
                    + safeName(attacker) + " assigns its combat damage: each must be dealt lethal damage before the next "
                    + "in your order can be dealt any", "blockers of " + safeName(attacker));
        }

        /** A blocker added mid-combat joins the damage order: the seat orders the whole list again. */
        @Override
        public CardCollection orderBlocker(Card attacker, Card blocker, CardCollection oldBlockers) {
            CardCollection all = new CardCollection(oldBlockers);
            if (blocker != null && !all.contains(blocker)) all.add(blocker);
            return orderByModel(all, "COMBAT DAMAGE ORDER: " + safeName(blocker) + " now also blocks "
                    + safeName(attacker) + ". List all " + all.size() + " blockers in the order " + safeName(attacker)
                    + " assigns its combat damage: each must be dealt lethal damage before the next in your order can be "
                    + "dealt any", "insert " + safeName(blocker) + " vs " + safeName(attacker));
        }

        /** Damage-assignment order when one blocker blocks multiple attackers: the seat's choice. */
        @Override
        public CardCollection orderAttackers(Card blocker, CardCollection attackers) {
            return orderByModel(attackers, "COMBAT DAMAGE ORDER: " + safeName(blocker) + " blocks "
                    + (attackers == null ? 0 : attackers.size()) + " attackers. List all of them in the order "
                    + safeName(blocker) + " assigns its combat damage: each must be dealt lethal damage before the next "
                    + "in your order can be dealt any", "attackers on " + safeName(blocker));
        }

        /** A full ordering of {@code cards} chosen by the seat. One card, or no model, keeps the order
         *  given (tier=auto, logged); never Forge's AI. */
        private CardCollection orderByModel(CardCollection cards, String question, String what) {
            clearLast();
            if (cards == null || cards.size() <= 1) return cards;
            List<Card> opts = new ArrayList<>(cards);
            List<String> labels = cardLabels(opts);
            try {
                if (smartOk() || fastOk()) {
                    List<Integer> picks = decideMany(smartOk() ? smart : fast, "damage-order", question, labels,
                            opts.size(), opts.size());
                    if (picks != null && picks.size() == opts.size() && new java.util.HashSet<>(picks).size() == opts.size()) {
                        CardCollection r = new CardCollection();
                        for (int i : picks) r.add(opts.get(i));
                        p("[ROUTER] damage-order " + what + " -> " + orderSummary(r));
                        return r;
                    }
                }
            } catch (Exception e) {
                p("[ROUTER] damage-order: " + e);
            }
            auto("damage-order(no-answer)->as-given " + what);
            return cards;
        }

        // ---------------- report C1: assignCombatDamage and choosePermanentsToSacrifice are routed ----------------
        // Their forced shapes are deterministic tier "auto"; their real choices go through decideOne/decideMany.

        /** Combat damage distribution (per creature per damage step). Report C1: this used to hand
         *  EVERY case — including the single-blocker, no-trample case that has exactly one legal
         *  distribution — to Forge's built-in combat AI. Now routed: a single blocker with no trample is
         *  a forced, deterministic `tier=auto` (all damage to the one blocker, no choice exists); a
         *  single blocker WITH trample is a genuine strategic choice (max-trample-through vs. dump
         *  everything on the blocker for overkill/value) and gets a compact two-option decideOne menu.
         *  "Lethal" is the rule's, {@link #lethalDamage} (CR 702.19b), not Forge AI's estimate of what kills
         *  (getEnoughDamageToKill counted indestructible, protection, shields and doublers: rules pilot RP_156-RP_160).
         *  MULTI-BLOCKER (double/triple block etc.): report C1 follow-up — this used to be the
         *  "[AI-EXOTIC-DAMAGE]" escape hatch that handed the WHOLE decision to Forge's AI via
         *  `super.assignCombatDamage`, including routine cases (e.g. two blockers, 1 damage — there is
         *  no genuinely "exotic" math there, just an ordering choice). That branch is gone: NO
         *  combat-damage path may reach Forge AI. Instead we ask the model to PRIORITIZE the blockers
         *  (one bounded decideMany call, full permutation, reusing the same PICKS plumbing as
         *  orderMoveToZoneList above), then assign damage deterministically in that order — lethal
         *  amount (lethalDamage, the rule's) to each blocker before spilling over to the next, with any
         *  leftover tramples through if the attacker has trample and a defender exists, otherwise piles
         *  onto the last blocker in the chosen order. No-model / unparsed-after-retry falls back to the
         *  engine's own blocker order (tier=auto, "damage-assign(auto-in-order)") — still never Forge AI. */
        @Override
        public Map<Card, Integer> assignCombatDamage(Card attacker, CardCollectionView blockers, CardCollectionView remaining,
                int damageDealt, GameEntity defender, boolean overrideOrder) {
            clearLast();
            try {
                if (overrideOrder && blockers != null && !blockers.isEmpty() && damageDealt > 0) {
                    Map<Card, Integer> free = assignFreeDivision(attacker, blockers, damageDealt, defender);
                    if (free != null) return free;
                }
                if (blockers != null && blockers.size() == 1) {
                    Card single = blockers.getFirst();
                    boolean trample = defender != null && attacker.hasKeyword(Keyword.TRAMPLE);
                    int[] split = trample
                            ? trampleSplit(damageDealt, lethalDamage(attacker, single))
                            : new int[]{damageDealt, 0};
                    int lethal = split[0], trampleOver = split[1];
                    Map<Card, Integer> r = new java.util.HashMap<>();
                    if (trampleOver <= 0) { // no trample, or not enough damage to kill+overflow -> one legal split
                        r.put(single, damageDealt);
                        evalDec("damage-assign", 1, 0, "auto", 0,
                                "forced(only-legal) all " + damageDealt + " -> " + safeName(single),
                                java.util.Arrays.asList(cardLabel(single) + ": all " + damageDealt + " dmg"));
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
                    return assignMultiBlockerDamage(attacker, blockers, damageDealt, defender, overrideOrder);
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

        /** Combat damage divided as the attacker's controller chooses (the 2024 rules; Forge's default here, GameRules
         *  orderCombatants false): any amounts on the blockers, and with trample on the defending player once every
         *  blocker has lethal. Asked as amounts, not as an order with damage cascading lethal-first, which hid every other
         *  legal split (found writing rules puzzles, batch 5). Null when there is only one legal split (a single blocker
         *  without trample, or not enough damage to trample over); an answer that cannot be used cascades lethal-first. */
        private Map<Card, Integer> assignFreeDivision(Card attacker, CardCollectionView blockers, int damageDealt, GameEntity defender) {
            List<Card> bl = new ArrayList<>(blockers);
            boolean trample = defender != null && attacker.hasKeyword(Keyword.TRAMPLE);
            List<Integer> lethal = new ArrayList<>();
            int lethalSum = 0;
            for (Card b : bl) { int l = lethalDamage(attacker, b); lethal.add(l); lethalSum += l; }
            if (bl.size() == 1 && (!trample || lethalSum >= damageDealt)) return null;
            List<String> labels = new ArrayList<>();
            for (int i = 0; i < bl.size(); i++) labels.add(cardLabel(bl.get(i)) + " (lethal damage: " + lethal.get(i) + ")");
            if (trample) labels.add(defender + " (the defending player: only once every blocker above has lethal)");
            final int nb = bl.size();
            java.util.function.Function<List<Integer>, String> rule = a -> {
                if (!trample || a.get(nb) == 0) return null;
                for (int i = 0; i < nb; i++)
                    if (a.get(i) < lethal.get(i))
                        return "with trample, damage goes to the player only once every blocker has lethal ("
                                + safeName(bl.get(i)) + " needs " + lethal.get(i) + ").";
                return null;
            };
            String q = "COMBAT DAMAGE ASSIGNMENT: " + safeName(attacker) + " deals " + damageDealt + " combat damage"
                    + (nb > 1 ? " and is blocked by " + nb + " creatures" : " to its blocker " + safeName(bl.get(0)))
                    + ". Divide it as you choose among the blockers" + (trample ? " and, with trample, the defending player" : "")
                    + ". Give an amount for each, in the order listed, summing to " + damageDealt + ".";
            List<Integer> a = decideAllocation(fastOk() ? fast : smart, q, labels, damageDealt, rule);
            boolean used = a != null && a.size() == labels.size() && a.stream().mapToInt(Integer::intValue).sum() == damageDealt
                    && rule.apply(a) == null;
            Map<Card, Integer> r = new java.util.HashMap<>();
            if (used) {
                for (int i = 0; i < nb; i++) r.put(bl.get(i), a.get(i));
                if (trample && a.get(nb) > 0) r.put(null, a.get(nb));
            } else {
                int left = damageDealt;                    // the fixed legal split: lethal-first in the given order
                for (int i = 0; i < nb; i++) { int v = Math.min(lethal.get(i), left); r.put(bl.get(i), v); left -= v; }
                if (left > 0) { if (trample) r.put(null, left); else r.put(bl.get(nb - 1), r.get(bl.get(nb - 1)) + left); }
            }
            evalDec("damage-assign", labels.size(), used ? 0 : -1, tierOf(!fastOk()), 0,
                    used ? null : "damage-assign(unusable)->lethal-first", labels);
            p("[ROUTER] damage-assign: " + r.entrySet().stream().map(e -> (e.getKey() == null ? String.valueOf(defender)
                    : safeName(e.getKey())) + "=" + e.getValue()).collect(java.util.stream.Collectors.joining(", ")));
            return r;
        }

        /** Multi-blocker damage split, model-prioritized (see assignCombatDamage's javadoc above).
         *  Returns a lethal-first assignment in the model's (or, on failure, the engine's) blocker
         *  order — every path deterministic Java, never Forge's combat AI. */
        private Map<Card, Integer> assignMultiBlockerDamage(Card attacker, CardCollectionView blockers,
                int damageDealt, GameEntity defender, boolean overrideOrder) {
            List<Card> opts = new ArrayList<>(blockers);
            List<String> labels = cardLabels(opts);
            List<Integer> order = null;
            // overrideOrder false: the damage assignment order was declared already (legacy ordering; the engine passes
            // the blockers in it), so damage follows it and is not asked again (issue #224).
            if (!overrideOrder) {
                order = new ArrayList<>();
                for (int i = 0; i < opts.size(); i++) order.add(i);
                auto("damage-assign(forced: the declared damage order)");
            } else if (fastOk()) {
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
                    int lethal = lethalDamage(attacker, b);
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

        /** Values the cast announces, the seat's choices, as the human cast path announces them
         *  (PlaySpellAbility.announceValuesLikeX; RE-DIFF on every engine re-pin): first each {@code Announce$} variable
         *  (a Shoal's X, a multikicker count), then an X in any cost part. The AI cast path announces nothing, so the
         *  engine paid whatever X the ability happened to hold: Green Sun's Zenith resolved at X=1..3 with no question,
         *  a planned Kozilek's Command at X=1 failed, and Disrupting Shoal's pitch and Toxic Deluge's life payment ran
         *  at X=0 (surface check, 2026-10-16). Mana X is offered up to the most the seat can pay; any other X over the
         *  engine's bounds. */
        private void announceX(SpellAbility sa) {
            try {
                if (sa.isCopied() || sa.isWrapper() || sa.getPayCosts() == null) return;
                boolean needX = true;
                String announce = sa.getParam("Announce");
                if (announce != null) {
                    for (String v : announce.split(",")) {
                        String var = v.trim();
                        org.apache.commons.lang3.Range<Integer> range = forge.game.ability.AbilityUtils.getAnnouncementBounds(sa, var);
                        int value = announced(sa, var, range.getMinimum(), range.getMaximum(),
                                range.getMaximum() < 99 ? " (up to " + range.getMaximum() + ")" : "");
                        if ("X".equalsIgnoreCase(var)) { needX = false; sa.setXManaCostPaid(value); }
                        else { sa.setSVar(var, Integer.toString(value)); sa.getHostCard().setSVar(var, Integer.toString(value)); }
                    }
                }
                if (!needX || !sa.getPayCosts().hasXInAnyCostPart()) return;
                String xVar = sa.getParamOrDefault("XAlternative", sa.getSVar("X"));
                boolean replacedXshard = sa.isSpell() && sa.getHostCard().getManaCost().countX() > 0 && !sa.getPayCosts().hasXInAnyCostPart();
                if (!(("Count$xPaid".equals(xVar) && !replacedXshard) || xVar.isEmpty())) return;
                forge.game.cost.CostPartMana mana = sa.getPayCosts().getCostMana();
                int min, max;
                String note;
                if (mana != null && mana.getAmountOfX() > 0) {
                    min = Math.max(0, mana.getXMin());
                    // The most X the engine says it can pay. ComputerUtilMana.determineLeftoverMana answered 0
                    // and 1 with three Forests for Green Sun's Zenith while canPayManaCost(X=2) is true.
                    max = min;
                    while (max < 99 && seatCanPay(sa, getPlayer(), max + 1, false)) max++;
                    note = " (you can pay for X up to " + max + ")";
                } else {
                    org.apache.commons.lang3.Range<Integer> range = forge.game.ability.AbilityUtils.getAnnouncementBounds(sa, "X");
                    min = range.getMinimum(); max = range.getMaximum();
                    // The engine's bounds do not ask whether the cost can be paid: Arcee, Sharpshooter's "remove X
                    // +1/+1 counters" was announced at X=1 with none on it, and the payment failed (keyword gauntlet).
                    int best = min - 1;
                    for (int k = min; k <= Math.min(max, 99); k++) {
                        sa.setXManaCostPaid(k);
                        if (!forge.game.cost.CostPayment.canPayAdditionalCosts(sa.getPayCosts(), sa, false)) break;
                        best = k;
                    }
                    if (best >= min) max = best;
                    note = max < 99 ? " (up to " + max + ")" : "";
                }
                sa.setXManaCostPaid(announced(sa, "X", min, max, note));
            } catch (Exception e) { p("[ROUTER] X: " + e + " -> engine default"); }
        }

        /** One announced value: forced when the range is one number, else the seat's ({@link #chooseNumber}). */
        private int announced(SpellAbility sa, String var, int min, int max, String note) {
            int x = min >= max ? min : !smartOk() && !fastOk() ? min
                    : chooseNumber(sa, "Choose " + var + " for " + verbOf(sa) + " " + labelOf(sa) + note, min, max);
            if (min >= max) auto("announce-" + var.toLowerCase() + "(forced)->" + x);
            p("[ROUTER] " + var + " = " + x + " for " + labelOf(sa));
            return x;
        }

        /** X / Multikicker / BidLife announcement. VALUE semantics: choice carries the announced
         *  NUMBER itself (not a menu index); -1 when super returns null — and that null is itself the
         *  AI's decision (it aborts the cast, PlaySpellAbility.java:757), so it is returned unchanged.
         *  Options enumerate min..max only when the range is small; max can be Integer.MAX_VALUE. */
        @Override
        public Integer announceRequirements(SpellAbility ability, int min, int max, String announce) {
            // The seat's choice now (it was Forge AI's, a tier=ai row). For the seat the engine reaches
            // this only from ChooseNumberEffect (ChooseAnyNumber, e.g. Mind's Aglow) and BidLife; mana
            // X is announced by announceX in the play loop.
            if (max < min) return null;
            return chooseNumber(ability, "Announce " + announce + srcAsk(ability), min, max);
        }

        /** Edict-style sacrifice (Sacrifice/Balance effects, Annihilator, Emerge/Offering costs) —
         *  ROUTED (report C1). This used to run entirely on Forge's built-in AI (no model
         *  consultation, no [ROUTER] line) — which in one tournament game sacrificed the
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
            // Emerge and offering: the engine asks for the creature while it works out the cost (CostAdjustment), 0 or 1.
            // The sacrifice is what lets the spell be cast that way (no creature, no emerge: the payment refuses), so the
            // seat names one. An affordability test is answered with the one that lowers the cost most, without asking,
            // as Delve's is: answering "none" there kept Elder Deep-Fiend's emerge off the menu (surface check).
            if (sa != null && (sa.isEmerge() || sa.isOffering()) && max == 1) {
                Card most = opts.get(0);
                for (Card c : opts) if (c.getCMC() > most.getCMC()) most = c;
                if (!payingForReal || askingConvoke) return new CardCollection(most);
                // Only a creature that leaves the rest payable: a smaller one can make the cast fail at payment.
                int left = availableMana() - (sa.getPayCosts() == null || sa.getPayCosts().getTotalMana() == null ? 0 : sa.getPayCosts().getTotalMana().getCMC());
                List<Card> keep = new ArrayList<>();
                for (Card c : opts) if (left + c.getCMC() >= 0) keep.add(c);
                if (!keep.isEmpty()) opts = keep;
                if (opts.size() == 1) return firstN(opts, 1, "sacrifice(forced, " + (sa.isEmerge() ? "emerge" : "offering") + ")" + srcOf(sa));
                List<String> labels = new ArrayList<>();
                for (Card c : opts) labels.add(cardLabel(c));
                List<Integer> pick = decideCost("sacrifice", "COST: to cast " + labelOf(sa) + " you SACRIFICE one of these; the cost is"
                        + " reduced by its mana value" + (sa.isOffering() ? " (offering)" : " (emerge)") + ". Choose which.", labels, 1, 1, false);
                Card c = pick != null && pick.size() == 1 ? opts.get(pick.get(0)) : most;
                if (pick == null || pick.size() != 1) auto("sacrifice(unparsed, emerge)->the largest reduction");
                return new CardCollection(c);
            }
            if (opts.size() <= min) return firstN(opts, min, "sacrifice(forced)" + srcOf(sa)); // whole set forced, no real choice
            try {
                if (!smartOk()) return firstN(opts, min, "sacrifice(no-budget)");
                List<String> labels = new ArrayList<>();
                for (Card c : opts) labels.add(cardLabel(c));
                // A COST is something the seat chose to pay; an edict is something done to it. Different
                // questions: the edict framing ("keep your best threats") is wrong for a sacrifice outlet,
                // where WHICH permanent leaves is often the whole point of the play.
                boolean asCost = message != null && message.startsWith(LlmCostDecision.COST_MESSAGE);
                String q = asCost
                        ? "COST: to pay for" + srcOf(sa) + " (" + (observer != null ? seatText(String.valueOf(sa)) : oneLine(String.valueOf(sa))) + ") you must SACRIFICE "
                          + (min == max ? String.valueOf(min) : min + "-" + max) + " of these permanents. "
                          + "It leaves the battlefield now, as the cost is paid, before the ability resolves. Choose which."
                        : "You must SACRIFICE " + (min == max ? String.valueOf(min) : min + "-" + max)
                        + " permanent(s)" + srcAsk(sa)
                        + (message == null || message.isEmpty() ? "" : ": " + fit(message))
                        + ". Choose what to give up — keep your best remaining threats/blockers/mana sources.";
                boolean canCancel = asCost && offerCostCancel;
                offerCostCancel = false;
                List<Integer> picks = decideCost("sacrifice", q, labels, min, max, canCancel);
                if (picks == null) return firstN(opts, min, "sacrifice(unparsed)");
                if (canCancel && costCancelled) { p("[ROUTER] sacrifice" + srcOf(sa) + ": CANCEL (the play is not paid for)"); return new CardCollection(); }
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
        static final long[] BRIDGE_RETRY_BACKOFF_MS = { 500L, 2000L };
        /** Every model call funnels through here — also the single capture point for the decision record:
         *  store the full prompt/reply/tier/finish_reason so evalDec can attach them (a retry overwrites,
         *  so the LAST attempt's prompt/raw_reply are what get logged). */
        private String ask(LLMBridge b, String prompt) { return ask(b, prompt, null); }

        /** `simulate`: when non-null and native tools are on, the decision also offers a lookahead
         *  preview as a tool; it maps the call's JSON arguments to the preview text. */
        /** Test seam: sees every prompt the seat asks (PromptFidelityTest's per-type contract). Null in play. */
        static volatile java.util.function.Consumer<String> TEST_ASK_TAP = null;
        /** Test seam: sees the type and tier of every decision row (GoldenChecksTest's per-type coverage). */
        static volatile java.util.function.BiConsumer<String, String> TEST_DECISION_TAP = null;

        /** Test runs only ({@code -Dharness.surface.report=<file>}): the engine hooks each decision row came through,
         *  "hook<TAB>asked" or "hook<TAB>auto", written when the JVM exits. Acceptance's `surfaces` stage compares it
         *  with the ownership ledger, so a hook the seat answers is proven by a test that reached it (owner,
         *  2026-10-16: every surface of the engine tested). Off in play: one null check per decision row. */
        static final String SURFACE_REPORT = System.getProperty("harness.surface.report");
        static final java.util.Set<String> SURFACES = new java.util.concurrent.ConcurrentSkipListSet<>();
        private static java.util.Set<String> hookNames;
        static {
            if (SURFACE_REPORT != null) Runtime.getRuntime().addShutdownHook(new Thread(() -> {
                // The engine's own lists of ways to play a card, so the gate compares with the engine, not a copy of it.
                for (forge.game.spellability.AlternativeCost a : forge.game.spellability.AlternativeCost.values()) SURFACES.add("known\tplay:alt:" + a);
                for (forge.game.spellability.OptionalCost o : forge.game.spellability.OptionalCost.values()) SURFACES.add("known\tplay:opt:" + o);
                try { java.nio.file.Files.write(java.nio.file.Paths.get(SURFACE_REPORT), SURFACES); } catch (Exception ignore) { }
            }));
        }

        /** Test runs only: how the seat's chosen play was played ("play:alt:Flashback", "play:opt:Kicker1",
         *  "play:zone:Graveyard", "play:state:Secondary", "play:land", "play:ability", "play:other-cost"), so each way
         *  the engine lets a card be played is proven by a test that played it from the menu. */
        private static void playSeen(SpellAbility sa, forge.game.zone.Zone from) {
            if (SURFACE_REPORT == null || sa == null) return;
            try {
                List<String> k = new ArrayList<>();
                k.add(sa.isLandAbility() ? "land" : sa.isSpell() ? "spell" : "ability");
                if (sa.getAlternativeCost() != null) k.add("alt:" + sa.getAlternativeCost());
                else if (sa.isSpell() && !sa.isBasicSpell()) k.add("other-cost");
                for (forge.game.spellability.OptionalCost o : sa.getOptionalCosts()) k.add("opt:" + o);
                if (from != null && !from.is(ZoneType.Hand) && !from.is(ZoneType.Battlefield)) k.add("zone:" + from.getZoneType());
                forge.card.CardStateName st = sa.getCardStateName();
                if (st != null && st != forge.card.CardStateName.Original) k.add("state:" + st);
                for (String x : k) SURFACES.add("play:" + x + "\tasked");
            } catch (Exception ignore) { }
        }

        private static void surfaceSeen(String tier) {
            if (hookNames == null) {
                java.util.Set<String> names = new java.util.HashSet<>();
                for (java.lang.reflect.Method m : forge.game.player.PlayerController.class.getMethods()) names.add(m.getName());
                hookNames = names;
            }
            String kind = "smart".equals(tier) || "fast".equals(tier) ? "asked" : "auto";
            String me = PlayerControllerLLM.class.getName();
            StackWalker.getInstance().forEach(f -> {
                // A cost choice (which creature to sacrifice) is made in the cost decision maker the hook hands out.
                if (f.getClassName().equals(LlmCostDecision.class.getName())) { SURFACES.add("getCostDecisionMaker\t" + kind); return; }
                if (!f.getClassName().equals(me)) return;
                String m = f.getMethodName();
                if (m.startsWith("lambda$")) m = m.substring(7, Math.max(7, m.indexOf('$', 7)));
                if (hookNames.contains(m)) SURFACES.add(m + "\t" + kind);
            });
        }

        // ---------------- the answer tool (AnswerTool) ----------------
        /** The answer session the NEXT ask() offers, set by a decision site through {@link #offerAnswer};
         *  ask() takes it. Null: the ask is answered in text. */
        private AnswerTool.Ask nextAnswer;
        /** The answer session of the most recent ask(), or null when it offered none. A decision site
         *  reads {@code lastAnswer.answer} instead of parsing the reply. */
        AnswerTool.Ask lastAnswer;
        /** The answer tool applies to this bridge: on (ANSWER_TOOL is not 0), native tools on, a model
         *  seat, and a backend that answers by call (an `openrouter:`, `jev:` or `claude-cli:` spec). */
        private boolean answerToolFor(LLMBridge b) {
            return AnswerTool.enabled() && NativeTools.enabled() && "llm".equals(mode) && rnd == null
                    && b != null && b.supportsNativeTools();
        }
        /** Offers `answer` (with `spec`) on the next ask of {@code b}, or nothing when the tool does not
         *  apply. `set_plan` rides along wherever a plan may be stated: any smart ask under the faithful
         *  profile (as PLAN: lines are read there), and the play ask that asks for the turn plan. */
        private AnswerTool.Ask offerAnswer(LLMBridge b, AnswerTool.Spec spec) {
            lastAnswer = null;
            if (!answerToolFor(b)) { nextAnswer = null; return null; }
            AnswerTool.Ask a = new AnswerTool.Ask(spec, spec.plan || (observer != null && b == smart));
            a.planSink = this::adoptPlanText;
            nextAnswer = a;
            return a;
        }
        /** Text read for a decision on an ask that offered `answer`: must stay 0. A tool answer is read from the
         *  typed {@link AnswerTool.Answer}; the text grammar is only for a backend with no function calling. */
        int textParsesOnToolAsks = 0;
        /** {@link #parseTagNumber} for a decision site: refused (-1) on an ask that offered `answer`. */
        private int textNumber(String reply, String key) {
            if (refuseText(key)) return -1;
            return parseTagNumber(reply, key);
        }
        /** {@link #lastSegment} for a decision site: refused (null) on an ask that offered `answer`. */
        private String textSegment(String reply, String key) {
            if (refuseText(key)) return null;
            return lastSegment(reply, key);
        }
        private boolean refuseText(String key) {
            if (lastAnswer == null) return false;
            textParsesOnToolAsks++;
            p("[TEXT-PARSE] refused: a decision tried to read " + key + " from the text of a tool ask");
            return true;
        }
        /** The validated answer of the last ask, when it came from the tool. */
        private AnswerTool.Answer toolAnswer() {
            return lastAnswer != null && lastAnswer.answered() ? lastAnswer.answer : null;
        }
        /** The last ask offered the tool and the model spent its correction: the caller's fallback, no re-ask. */
        private boolean answerLost() { return lastAnswer != null && lastAnswer.lost; }
        /** How the current decision was answered, for its row: "tool", "nudged", "text", "reask" (a second full ask),
         *  "lost" (two invalid replies); null when the answer tool was not offered. */
        private String answerVia = null;
        /** Tool asks that still carried a text answer format (AnswerTool.textFormatLeak): must stay 0. */
        int textFormatLeaks = 0;
        private int answerInvalid = 0;
        /** Records how the ask just made was answered; attempt > 0 is a full re-ask. */
        private void noteAnswerVia(int attempt) {
            if (lastAnswer == null) return;
            answerInvalid += lastAnswer.invalid;
            answerVia = lastAnswer.lost ? "lost" : attempt > 0 ? "reask" : lastAnswer.via;
        }

        private String ask(LLMBridge b, String prompt, java.util.function.Function<String, String> simulate) {
            prompt = withIntent(prompt);
            if (b instanceof ChatSessionBridge) {
                try { ((ChatSessionBridge) b).atTurn(getPlayer().getGame().getPhaseHandler().getTurn()); } catch (Exception ignore) { }
            }
            AnswerTool.Ask answer = nextAnswer;
            nextAnswer = null;
            lastAnswer = answer;
            // The decision text's own format line ("end with EXACTLY: CHOICE: <number>") becomes the tool's.
            if (answer != null) {
                prompt = AnswerTool.rephrase(prompt, answer.spec, answer.plan);
                // Every decision goes through the call: a text format left in a tool ask is a harness defect.
                String leak = AnswerTool.textFormatLeak(prompt);
                if (leak != null) { textFormatLeaks++; p("[TEXT-FORMAT-LEAK] a tool ask offers a text answer: ..." + leak + "..."); }
            } else if (answerToolFor(b)) {
                // A decision site that did not offer `answer` on a backend that calls: a text ask, the same defect.
                textFormatLeaks++;
                p("[TEXT-FORMAT-LEAK] a text ask on a tool backend (no offerAnswer before it)");
            } else if (observer != null) {
                // The faithful text lane caps no reasoning either (2026-10-16 review M1); the shared question
                // builders keep their bytes for the legacy profiles and the ai-teacher pairing.
                prompt = prompt.replace("Reason briefly, then end with", "End with");
            }
            java.util.function.Consumer<String> tap = TEST_ASK_TAP;
            if (tap != null) tap.accept(prompt);
            String reply = answer != null ? askAnswer(b, prompt, simulate, answer) : askOnce(b, prompt, simulate);
            // PLAN and note lines are read from a TEXT reply only; a tool answer's plan came through set_plan.
            if (observer != null && b == smart && reply != null && (answer == null || !answer.answered())) { adoptPlan(reply); adoptNotes(reply); }
            // ONE_CHAT / TURN_CHAT: the conversation keeps each answered decision (a tool round or a dead bridge is
            // not one); one answered by a call is kept as its calls, results and reasoning.
            if (b instanceof ChatSessionBridge) {
                ChatSessionBridge chat = (ChatSessionBridge) b;
                if (answer != null && answer.transcript != null) chat.rememberAsk(answer.transcript);
                else if (answer == null) chat.remember(prompt, reply);
            }
            return reply;
        }

        /** An ask answered through `answer`: the lookups (when MEMORY_TOOLS) and the answer share one
         *  conversation. Returns the answer rendered in the text grammar's words (also the row's raw_reply),
         *  a text reply, or null (no reply, or the answer was lost). */
        private String askAnswer(LLMBridge b, String prompt, java.util.function.Function<String, String> simulate,
                                 AnswerTool.Ask answer) {
            String user = prompt;
            AnswerTool.Lookups lookups = null;
            if (MEMORY_TOOLS) {
                archiveEvents();
                user = contextMemory.beginNative(prompt);
                lookups = contextMemory.lookups(simulate);
            } else if (simulate != null || lookaheadEnabled()) {
                lookups = AnswerTool.previewOnly(simulate);
            }
            String reply = AnswerTool.converse(user, answer, nativeModel(b, answer.spec), lookups, contextAudit());
            if (answer.answered()) lastReply = reply;   // the row records the answer, not the call's markup
            return reply;
        }

        /** Archive the existing player-narration stream before its lossy prompt compaction.
         *  Never attach raw engine objects or omniscient evaluation records to this archive. */
        private void archiveEvents() {
            try {
                // Faithful path: archive the SEAT stream (redacted, reveals included), never the raw
                // global log, so the archive can hold nothing the observation could not.
                List<GameHistory.Ev> events;
                if (observer != null) { observer.capture(); events = observer.events(); }
                else events = GameHistory.collect(getPlayer().getGame());
                for (int i = archivedEventCount; i < events.size(); i++) {
                    GameHistory.Ev event = events.get(i);
                    contextMemory.observe("[E" + event.id + "] T" + event.turn + " " + event.type + ": " + event.msg);
                    archivedEventCount = i + 1;
                }
            } catch (Exception e) {
                contextMemory.observe("HISTORY UNAVAILABLE at T" + safeTurn() + "; archive may be incomplete.");
            }
        }

        private java.util.function.BiConsumer<String, String> contextAudit() {
            return (command, result) -> EvalLog.write(
                    "{\"t\":\"context_tool\",\"version\":\"" + ContextMemory.VERSION + "\",\"seat\":\"" + jsonEscape(seat)
                    + "\",\"turn\":" + safeTurn() + ",\"call\":" + contextCallSeq
                    + ",\"request\":\"" + jsonEscape(command) + "\",\"result\":\"" + jsonEscape(result) + "\"}");
        }

        /** One native model turn over bridge {@code b}, with the decision row's bookkeeping and a
         *  context_call transcript row. */
        /** The additive `reasoning` field of a context_call row: the model's readable reasoning, when the
         *  provider returned it in plain text. */
        static String reasoningField(NativeTools.Turn t) {
            String r = t == null ? null : NativeTools.reasoningOf(t.messageJson);
            return r == null ? "" : ",\"reasoning\":\"" + jsonEscape(r) + "\"";
        }

        /** The additive `cost_usd` field of a context_call row: the provider's own cost of the call, when it reports one. */
        static String costField(LLMBridge b) {
            double c = b.getLastCostUsd();
            return c < 0 ? "" : ",\"cost_usd\":" + java.math.BigDecimal.valueOf(c).toPlainString();
        }

        /** {@code spec}: the answer ask's typed legal values, handed to the bridge beside the messages (a decision
         *  model answers from them, DecisionsBridge); null on a lookup-only ask. */
        private ContextMemory.NativeModel nativeModel(LLMBridge b, AnswerTool.Spec spec) {
            return (user, messages, tools, allowCalls, round) -> {
                if (!b.supportsNativeTools()) return null;
                if (!(b == smart ? smartOk() : fastOk())) return null;
                long start = System.currentTimeMillis();
                NativeTools.Turn[] got = new NativeTools.Turn[1];
                String text = askModel(b, user, () -> {
                    got[0] = b.chat(messages, tools, allowCalls, spec);
                    return got[0] == null ? null : got[0].text();
                });
                EvalLog.write("{\"t\":\"context_call\",\"version\":\"" + ContextMemory.VERSION
                        + "\",\"protocol\":\"native\",\"tools_version\":\"" + NativeTools.VERSION
                        + "\",\"seat\":\"" + jsonEscape(seat) + "\",\"turn\":" + safeTurn()
                        + ",\"call\":" + (++contextCallSeq) + ",\"round\":" + round
                        + ",\"tier\":\"" + (b == smart ? "smart" : "fast")
                        + "\",\"elapsed_ms\":" + (System.currentTimeMillis() - start)
                        + ",\"prompt_tokens\":" + lastPromptTokens + ",\"completion_tokens\":" + lastCompletionTokens + cacheFields(lastCachedTokens, lastCacheWriteTokens) + costField(b)
                        + (lastProvider == null ? "" : ",\"provider\":\"" + jsonEscape(lastProvider) + "\"")
                        + ",\"prompt\":\"" + jsonEscape(user) + "\""
                        // Later rounds carry the tool exchanges; the first is just the prompt above.
                        + (round == 0 ? "" : ",\"messages\":" + messages)
                        + ",\"raw_reply\":" + (text == null ? "null" : "\"" + jsonEscape(text) + "\"")
                        + reasoningField(got[0]) + "}");
                // A turn of tool calls is not a discarded answer: the next round must not record it
                // as first_failed_reply or bill its time to retry_ms.
                if (got[0] != null && !got[0].calls.isEmpty()) lastReply = null;
                return text == null ? null : got[0];
            };
        }

        private String askOnce(LLMBridge b, String prompt, java.util.function.Function<String, String> simulate) {
            if (!MEMORY_TOOLS || !"llm".equals(mode)) return askModel(b, prompt);
            archiveEvents();
            java.util.function.BiConsumer<String, String> audit = contextAudit();
            if (NativeTools.enabled() && b.supportsNativeTools()) {
                // Native function calling: same archive, same executor, same budget; the request is a
                // structured call instead of a line of text the reply must consist of.
                // If the provider refuses tools mid-ask, this one decision gets no model answer (its
                // caller takes the deterministic fallback); the seat's later decisions use the text loop.
                return contextMemory.askNative(prompt, nativeModel(b, null), simulate, audit);
            }
            return contextMemory.ask(prompt, request -> {
                if (!(b == smart ? smartOk() : fastOk())) return null;
                long start = System.currentTimeMillis();
                String response = askModel(b, request);
                // Every memory-mode call, including the final action, has a complete transcript.
                // These rows are supplementary: do not sum them with decision-row token counts.
                EvalLog.write("{\"t\":\"context_call\",\"version\":\"" + ContextMemory.VERSION
                        + "\",\"seat\":\"" + jsonEscape(seat) + "\",\"turn\":" + safeTurn()
                        + ",\"call\":" + (++contextCallSeq) + ",\"tier\":\"" + (b == smart ? "smart" : "fast")
                        + "\",\"elapsed_ms\":" + (System.currentTimeMillis() - start)
                        + ",\"prompt_tokens\":" + lastPromptTokens + ",\"completion_tokens\":" + lastCompletionTokens + cacheFields(lastCachedTokens, lastCacheWriteTokens)
                        + (lastProvider == null ? "" : ",\"provider\":\"" + jsonEscape(lastProvider) + "\"")
                        + ",\"prompt\":\"" + jsonEscape(request) + "\",\"raw_reply\":"
                        + (response == null ? "null" : "\"" + jsonEscape(response) + "\"") + "}");
                if (ContextMemory.toolLine(response) != null) lastReply = null; // a tool round, not a failed answer
                return response;
            }, audit);
        }

        private String askModel(LLMBridge b, String prompt) {
            return askModel(b, prompt, () -> b.ask(prompt));
        }

        /** `call` is the round-trip itself, so a native tool turn gets the same retry, timing and
         *  health bookkeeping as a plain ask. `prompt` is what the decision row records. */
        private String askModel(LLMBridge b, String prompt, java.util.function.Supplier<String> call) {
            long tAsk = System.currentTimeMillis();
            Integer routing0 = b.getRoutingRefusals();
            long billedPrompt0 = b.getBilledPromptTokens(), billedCompletion0 = b.getBilledCompletionTokens();
            if (b == smart) smartCalls++; else fastCalls++;
            lastPrompt = prompt;
            lastTier = (b == smart) ? "smart" : "fast";
            lastAttempts++;
            long tSub = System.currentTimeMillis();
            String reply = call.get();
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
                reply = call.get();
                subMs = System.currentTimeMillis() - tSub;
            }
            // Per-seat health signal: observability for a bridge that's gone dark for many consecutive
            // DECISIONS (e.g. an OpenRouter route degrading for the rest of a match) without changing
            // behavior — callers still take their deterministic legal fallback either way. Fires the
            // loud marker once per streak, not once per failed decision.
            if (reply == null) {
                Integer routing1 = b.getRoutingRefusals();
                if (routing0 != null && routing1 != null && routing1 > routing0) lostToRouting++;
                consecutiveBridgeFailures++;
                if (b.lastFailureWasTransport()) { bridgeDown.merge(seat, 1, Integer::sum); lostToTransport++; }
                if (consecutiveBridgeFailures >= 5 && !circuitBreakerTripped) {
                    circuitBreakerTripped = true;
                    breakerTrips.merge(seat, 1, Integer::sum);
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
                    if (lastFirstFailedReply == null) lastFirstFailedReply = headAndTail(lastReply, 250);
                    lastRetryMs += lastSuccessMs;
                }
                lastSuccessMs = subMs; // only the truly-final successful sub-attempt's own duration
            }
            lastReply = reply;
            lastFinishReason = b.getLastFinishReason();
            lastPromptTokens = b.getLastPromptTokens();
            lastCompletionTokens = b.getLastCompletionTokens();
            lastCachedTokens = b.getLastCachedTokens();
            lastCacheWriteTokens = b.getLastCacheWriteTokens();
            lastProvider = b.getLastProvider();
            // What this ask spent: the bridge's billing when it keeps one (every send, retries included); else the last
            // call's usage, and only when it answered, since a failed ask leaves the previous call's count in place.
            if (billedPrompt0 >= 0) {
                spentPromptTokens += b.getBilledPromptTokens() - billedPrompt0;
                spentCompletionTokens += b.getBilledCompletionTokens() - billedCompletion0;
            } else if (reply != null) {
                spentPromptTokens += Math.max(0, lastPromptTokens);
                spentCompletionTokens += Math.max(0, lastCompletionTokens);
            }
            long askMs = System.currentTimeMillis() - tAsk;
            spentModelMs += askMs;
            lastAskMs += askMs; // summed over every attempt; retained for the
                                                             // ms<=0 legacy substitution guard in evalDec
            return reply;
        }
        /** Cap a string at n chars for a jsonl field that must stay small (diagnostic reply snippets).
         *  Package-private (not private) so LlmSpikeTest can exercise it directly. */
        /** The first and last n characters: a discarded reply's answer line, when it has one, is at the
         *  end, which a plain head cap dropped. */
        static String headAndTail(String s, int n) {
            return (s == null || s.length() <= 2 * n + 5) ? s : s.substring(0, n) + " ... " + s.substring(s.length() - n);
        }

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
        /** A cost decision the model should have made fell through to the engine's AI. Counted and
         *  logged like every other delegation, so the ai=0 invariant sees it. */
        void costDecisionFellBack(String kind, String why) { noteAi(kind + "(" + why + ")"); }
        /** A cost decision the seat did not answer, paid with a fixed legal choice (not Forge's AI). */
        void costDecisionDefault(String what) { auto(what); }

        private void noteAi(String where) {
            aiDelegations++;
            p("[FALLBACK->AI] " + where + "  (ai=" + aiDelegations + " smart=" + smartCalls + " fast=" + fastCalls + " auto=" + autoPicks + ")");
            evalDec(firstWord(where), -1, -1, "ai", 0, where);
        }
        private void auto(String what) { autoPicks++; p("[AUTO-LEGAL] " + what); evalDec(firstWord(what), -1, -1, "auto", 0, what); }

        /** The prompt with the most recent play and the model's reasoning for it, placed just before the
         *  decision. Not on the play menu itself (that is where a new play is chosen), not on a legacy
         *  profile, and not after the turn in which the play was chosen. */
        String withIntent(String prompt) {
            if (observer == null || actionIntent == null || actionIntentTurn != safeTurn()
                    || prompt.contains("\nDECISION: You have priority")) return prompt;
            // Only the model's own words carry over, never an old state: the snapshot above is current,
            // and events since the play (a response, a trigger) are in it.
            String block = "\nTHE PLAY YOU CHOSE MOST RECENTLY (T" + actionIntentTurn + "): " + actionIntent
                    + "\n(The state above is current; if it has changed since you chose that play, decide on the current state.)\n";
            int at = prompt.lastIndexOf("\nPRIORITY: you\nDECISION:");
            if (at < 0) at = prompt.lastIndexOf("\nDECISION:");
            return at < 0 ? prompt + block : prompt.substring(0, at) + block + prompt.substring(at);
        }

        /** What makes a new spot even when the stack top and the menu look the same again: every player's
         *  life, poison, zone sizes and mana pool, the stack's size, and each permanent by id with its tapped
         *  state, counters and power/toughness. Sizes alone (until 2026-10-16, review LOW) missed an untap,
         *  a counter or a pump between two windows, and the second window was passed for the seat. */
        static String boardDigest(Game g) {
            StringBuilder b = new StringBuilder();
            try {
                b.append(g.getStack().size()).append(';');
                for (Player pl : g.getPlayers()) {
                    b.append(pl.getLife()).append(',').append(pl.getPoisonCounters()).append(',')
                     .append(pl.getCardsIn(ZoneType.Hand).size()).append(',')
                     .append(pl.getCardsIn(ZoneType.Graveyard).size()).append(',')
                     .append(pl.getCardsIn(ZoneType.Exile).size()).append(',')
                     .append(pl.getCardsIn(ZoneType.Library).size()).append(',')
                     .append(pl.getManaPool().totalMana()).append('[');
                    for (Card c : pl.getCardsIn(ZoneType.Battlefield))
                        b.append(c.getId()).append(c.isTapped() ? "t" : "u").append(c.getCounters())
                         .append(c.isCreature() ? c.getNetPower() + "/" + c.getNetToughness() : "").append(' ');
                    b.append("];");
                }
            } catch (Exception ignore) { }
            return b.toString();
        }

        /** The model's reply without its answer and tag lines: what it said about why. */
        static String reasoningOf(String reply) {
            if (reply == null) return "";
            // The plan line stays (it is often the whole reason); the answer and the side notes go.
            String r = reply.replaceAll("(?im)^\\s*(LINES|PROFILE|STEPS):.*$", "").replaceAll("(?i)CHOICE:\\s*\\d+", "");
            return flat(r); // whole: the 800-character cut fired on 14 carried intents in runs/compare
        }

        /** Passes priority for the seat without a model call and records it as a `t:"window"` row
         *  (not a decision row, so decision counts and the canaries do not move). These shortcuts
         *  wrote nothing before, so no tool could say how many windows the harness took for the
         *  model. `castable` is how many spells or abilities the seat could have used (-1 when not
         *  counted); a skip with castable > 0 is the harness deciding to do nothing. */
        private List<SpellAbility> skipWindow(String reason, int castable) {
            windowSkips.merge(reason, 1, Integer::sum);
            if (EvalLog.on()) {
                try {
                    Game g = getGame();
                    PhaseHandler ph = g.getPhaseHandler();
                    EvalLog.write("{\"t\":\"window\",\"seat\":\"" + jsonEscape(seat) + "\",\"turn\":" + ph.getTurn()
                            + ",\"phase\":\"" + ph.getPhase() + "\",\"active\":" + ph.isPlayerTurn(getPlayer())
                            + ",\"reason\":\"" + reason + "\",\"castable\":" + castable
                            + ",\"stack\":" + g.getStack().size()
                            + ",\"stack_top\":\"" + jsonEscape(safeStackTop(g)) + "\"}");
                } catch (Exception ignore) { }
            }
            return null; // PASS: the engine passes priority only on null
        }

        /** Spells and abilities the seat could use right now, for a skipped window's row. Counted
         *  only for a model seat with EVAL_OUT set: the floor arm never builds a menu, and its canary
         *  must keep exercising exactly the code it pins. */
        private int castableNow(Player me, boolean ownMain) {
            if (!EvalLog.on() || rnd != null || !smartOk()) return -1;
            try { return enumeratePlayOptions(me, ownMain).spells.size(); } catch (Exception e) { return -1; }
        }
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
            java.util.function.BiConsumer<String, String> decTap = TEST_DECISION_TAP;
            if (decTap != null) decTap.accept(type, tier);
            if (SURFACE_REPORT != null) surfaceSeen(tier);
            if (!EvalLog.on()) return;
            // Log choke point: several callers log their own labels list after decideOne/decideMany
            // disambiguated only its local copy — dedupe HERE so the logged options always match the
            // prompt the model saw. disambiguate is idempotent, so double application is a no-op.
            if (options != null) options = disambiguate(options);
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
                    lastPromptTokens, lastCompletionTokens, lastShadowChoice, lastPicks, answerVia, answerInvalid, lastCachedTokens, lastCacheWriteTokens);
            lastPicks = null;
        }
        /** Tier label for asked decisions: random arm decisions are harness-made, not model-made. */
        /** Tier ROUTING table: decision TYPES sent to the FAST tier. Empty (the default) keeps the
         *  historical behaviour exactly -- every strategic decision is asked of the smart tier.
         *
         *  Classification decisions over a menu the harness has ALREADY legalised (which target, which
         *  card leaves a zone, pay-or-decline, which ability of a card) do not need the reasoning tier.
         *  Measured 2026-10-13 on deepseek-v4.1-flash: a smart-tier decision cost ~7s because ~95% of
         *  its completion was hidden reasoning feeding a ~50-token answer, while the same prompt with
         *  reasoning disabled answered in ~1.3s. PLANNING decisions (play, attackers, mulligan) stay
         *  smart, because sequencing and combat are exactly what reasoning is for.
         *
         *  Opt in with, e.g.:
         *    TIER_FAST_TYPES="targets,target,zone-change,prevent-effect,ability-to-play"  */
        static final java.util.Set<String> FAST_TIER_TYPES = parseFastTierTypes();

        private static java.util.Set<String> parseFastTierTypes() {
            String v = System.getenv("TIER_FAST_TYPES");
            if (v == null || v.trim().isEmpty()) return java.util.Collections.emptySet();
            java.util.Set<String> out = new java.util.HashSet<>();
            for (String p : v.split(",")) if (!p.trim().isEmpty()) out.add(p.trim().toLowerCase(java.util.Locale.ROOT));
            return out;
        }

        /** True when a decision type belongs on the smart (reasoning) tier. */
        static boolean smartTierFor(String type) {
            return type == null || !FAST_TIER_TYPES.contains(type.toLowerCase(java.util.Locale.ROOT));
        }

        /** The opt-in fast-tier type list exactly as the routing table holds it, for the game line:
         *  lowercased, de-duplicated, sorted, comma-joined; empty when the switch is off. TIER_FAST_TYPES
         *  moves which decisions get the reasoning tier, so games played with it on and off are different
         *  conditions and must never pool (docs/STANDARDS.md section 4). */
        static String tierFastTypesLabel() {
            return String.join(",", new java.util.TreeSet<>(FAST_TIER_TYPES));
        }

        /** The bridge that handles a decision type, and its budget gate -- one source of truth, so the
         *  routing table cannot drift from the tier actually used. */
        private LLMBridge bridgeFor(String type) { return smartTierFor(type) ? smart : fast; }
        private boolean okFor(String type) { return smartTierFor(type) ? smartOk() : fastOk(); }

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
         *      DOES apply to the student's real cast — its payManaCost override still pays through it. A stale
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
            //   * `chain` covers the MENU (po.spells, every castable entry, + po.landPlays capped at 4) and their sub-chains. The
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
                    // AI picked an ability OUTSIDE our play menu (an activated/mana ability the menu
                    // excludes, or a land past the 4-land cap): this is NOT a pass. Return null so build_dagger_examples drops the row
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
        static String costStr(Card c) {
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
        /** Who the spell's targets are, for a decision asked after targeting (a name, a number). A
         *  name ask that does not say whose hand it reaches is answered as if it were the opponent's:
         *  runs/loop/g08 targeted itself with Cabal Therapy to bin Griselbrand and then named Ajani. */
        static String targetNote(SpellAbility sa, Player me) {
            try {
                List<String> who = new ArrayList<>();
                for (forge.game.spellability.TargetChoices tc : sa.getAllTargetChoices()) {
                    for (forge.game.GameObject o : tc) {
                        String n = o == me ? "YOU (" + me.getName() + ")"
                                : o instanceof Card ? ((Card) o).getName() : o.toString();
                        if (!who.contains(n)) who.add(n);
                    }
                }
                return who.isEmpty() ? "" : " This spell targets: " + String.join(", ", who) + ".";
            } catch (Exception e) { return ""; }
        }
        private static String srcOf(SpellAbility sa) {
            try { return sa != null && sa.getHostCard() != null ? " [" + safeName(sa.getHostCard()) + "]" : ""; }
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
            // Persistent object identity (2026-08-02 user directive): the engine's
            // per-game card id, for IN-PLAY cards only, "@42". Two same-name
            // same-state permanents become distinct objects the model can track
            // across turns and name as targets — the within-menu " #N" ordinals
            // from disambiguate() cannot do either (they are per-menu, not
            // per-object). Battlefield-only keeps hand/yard text stable.
            try { if (c.isInPlay()) s.append('@').append(c.getId()); } catch (Exception ignore) { }
            return s.toString();
        }
        private static String entityLabel(GameEntity e) {
            if (e instanceof Card) return candidateLabel((Card) e) + " [" + ((Card) e).getZone() + "]";
            return e.getName();
        }
        private static String saLabel(SpellAbility sa) {
            String host = sa.getHostCard() != null ? safeName(sa.getHostCard()) : "?";
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
        /** Tolerant of markdown wrapping, missing colons and trailing prose after the number
         *  ("CHOICE: 3 (final answer)"). The number after the reply's last {@code key} (CHOICE:) or ANSWER: that is
         *  followed closely by a number: a degenerate reply (runs/compare one-tier seed 82, T11) said
         *  "CHOICE: 4" and then, in 8,000 characters of repetition, "Final Answer: ..." with a 3
         *  somewhere after it, and the 3 was played. */
        static int parseTagNumber(String reply, String key) {
            if (reply == null) return -1;
            int found = -1;
            // The last line whose tag is followed closely by the number wins ("CHOICE: 2 ... Answer: 5"
            // is 5). A tag followed by words is not an answer, so degenerate text after the real one
            // ("Final Answer: \"NCAA: 3") cannot override it.
            for (String ln : stripMarkdown(reply).split("\\R")) {
                String seg = lastSegmentAny(ln, key, "ANSWER:");
                if (seg == null) continue;
                Matcher m = TAG_NUMBER.matcher(seg);
                if (m.find()) found = Integer.parseInt(m.group(1));
            }
            return found;
        }
        /** A number right after an answer tag: punctuation, and the word "option", may come between. */
        private static final Pattern TAG_NUMBER = Pattern.compile("(?i)^[^\\p{L}\\d\\n]{0,6}?(?:option\\s*#?\\s*)?(\\d+)");
        private static Card matchByName(Iterable<Card> cards, String upperSeg) {
            for (Card c : cards) if (upperSeg.contains(c.getName().toUpperCase())) return c;
            return null;
        }
        /** Opponent-side signature: the facts whose change means our plan was interrupted. Our OWN
         *  plays change our own side, so they are deliberately excluded -- otherwise the first executed
         *  step would invalidate the plan that ordered it. */
        private String oppPlanSig() {
            try {
                Player opp = null;
                for (Player o : getPlayer().getOpponents()) { opp = o; break; }
                if (opp == null) return "";
                return opp.getName() + "|" + opp.getLife() + "|" + opp.getCardsIn(ZoneType.Hand).size()
                        + "|" + opp.getCardsIn(ZoneType.Battlefield).size()
                        + "|" + getGame().getStack().size();
            } catch (Exception e) { return ""; }
        }

        /** Menu index of an explicit plan step, by EXACT (case-insensitive) card name, or -1. Exact
         *  matching, never substring: "Bolt" must not fire "Lightning Bolt". */
        private int planStepInMenu(String step, List<SpellAbility> options) {
            List<String> names = new ArrayList<>();
            for (SpellAbility sa : options) {
                String n = null;
                try { n = sa.getHostCard() == null ? null : safeName(sa.getHostCard()); } catch (Exception ignore) { }
                names.add(n);
            }
            return planStepIndex(names, step);
        }

        /** Pure core of {@link #planStepInMenu}: index of `step` in `cardNames` by EXACT
         *  case-insensitive name, or -1. Exact and never substring -- "Bolt" must not fire
         *  "Lightning Bolt", and a wrong auto-cast is a real misplay, not a cosmetic one.
         *  State- and Game-free so it is directly testable. */
        static int planStepIndex(List<String> cardNames, String step) {
            String want = step == null ? "" : step.trim().toUpperCase(Locale.ROOT);
            if (want.isEmpty()) return -1;
            for (int i = 0; i < cardNames.size(); i++) {
                String n = cardNames.get(i);
                if (n != null && n.toUpperCase(Locale.ROOT).equals(want)) return i;
            }
            return -1;
        }

        /** Lethal damage for assigning combat damage (CR 702.19b, 510.1c): the blocker's toughness (or what Forge's
         *  getLethal says, e.g. power under Zilortha) minus damage already marked and assigned, and 1 at most when the
         *  attacker has deathtouch (CR 702.2c). Whether the damage would kill (indestructible, protection, a shield
         *  counter, a doubler) does not enter: rules pilot RP_156-RP_160 forced splits the rules allow. */
        static int lethalDamage(Card attacker, Card blocker) {
            int l = Math.max(0, blocker.getLethalDamage());
            return attacker != null && attacker.hasKeyword(Keyword.DEATHTOUCH) ? Math.min(l, 1) : l;
        }

        /** Pure trample-split math used by assignCombatDamage's single-blocker/trample menu: how much
         *  of `damageDealt` is lethal-to-the-blocker vs. tramples over to the defender. `killDamage` is the
         *  rule's lethal damage ({@link #lethalDamage}); it may exceed damageDealt, and then nothing tramples over.
         *  Returns {lethal, trampleOver}. */
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

        /** The faithful menu (every land, every mana ability that floats) for a model seat and for the puzzle prover:
         *  a prover with a smaller menu than the model's misses lines, and calls a puzzle unique that has a second
         *  solution (rules pilot RP_017: City of Brass tapped before Angel's Grace was never on the search arm's menu).
         *  The other zero-model arms keep the legacy menu, so the canaries do not move. */
        static boolean faithfulMenu(boolean modelSeat, java.util.Random arm) {
            return modelSeat || arm instanceof PuzzleArms.Search;
        }

        /** Enumerate the play menu exactly as {@link #chooseSpellAbilityToPlay} builds it — castable,
         *  affordable, legally-targetable non-land/non-mana spells (every one), plus distinct
         *  playable lands by name (capped at 4, only on the player's own main phase). Factored out of
         *  the LLM seat's play method so the ai-teacher seat builds the byte-identical option list; the
         *  LLM method calls this in place, so its output is unchanged. Read-only w.r.t. real game state
         *  (the only mutation, {@code setActivatingPlayer} inside the target-legality probe, mirrors the
         *  original inline code). */
        static PlayOptions enumeratePlayOptions(Player me, boolean ownMain) {
            return enumeratePlayOptions(me, ownMain, false);
        }

        /** {@code everyLand}: offer every distinct playable land, not the first 4 (the faithful seat;
         *  a five-land-type hand had its fifth land unofferable). The legacy and teacher menus keep
         *  the cap so their bytes, and the zero-model canaries, do not move. */
        static PlayOptions enumeratePlayOptions(Player me, boolean ownMain, boolean everyLand) {
            List<SpellAbility> options = new ArrayList<>();
            // getSpellAbilities drops a spell's alternative costs whenever the spell is also castable
            // normally, leaving the AI to re-expand them later; this menu never did, so Unmask, Force of
            // Will and every other "rather than pay this spell's mana cost" spell went unoffered
            // (runs/loop/g08: Unmask in hand beside four black cards, never on the menu). Expand here.
            List<SpellAbility> candidates = new ArrayList<>();
            java.util.Set<SpellAbility> altCosts = new java.util.HashSet<>();
            for (SpellAbility sa : ComputerUtilAbility.getSpellAbilities(
                    ComputerUtilAbility.getAvailableCards(me.getGame(), me), me)) {
                candidates.add(sa);
                // An activated ability has alternative costs too (Forge Anew: equip for {0}, rules pilot RP_026).
                if ((!sa.isSpell() && !sa.isActivatedAbility()) || sa.isManaAbility() || sa.getAlternativeCost() != null) continue;
                try {
                    for (SpellAbility alt : GameActionUtil.getAlternativeCosts(sa, me, false)) {
                        candidates.add(alt); altCosts.add(alt);
                    }
                } catch (Exception ignore) { /* the base spell is still offered */ }
            }
            // A spell playable only WITH an optional cost is offered with it: retrace and jump-start cast from the
            // graveyard (SpellAbility.canPlayWithOptionalCost), an offering lowers a cost the seat cannot otherwise pay.
            // Its plain cast is not on the menu, so the cost asked after the pick (withOptionalCosts) never came up.
            for (SpellAbility sa : new ArrayList<>(candidates)) {
                if (!sa.isSpell() || altCosts.contains(sa)) continue;
                try {
                    sa.setActivatingPlayer(me);
                    if (offerable(sa, me, false)) continue;
                    for (OptionalCostValue v : GameActionUtil.getOptionalCostValues(sa)) {
                        SpellAbility with = withOptional(sa, Lists.newArrayList(v));
                        with.setActivatingPlayer(me);
                        if (offerable(with, me, true)) { candidates.add(with); altCosts.add(with); }
                    }
                } catch (Exception ignore) { /* not offered */ }
            }
            // A plain mana ability is a legal play too (float mana before a ritual, tap a land in response to its
            // destruction): offered once per card name and ability, so twenty lands are five options, not twenty.
            java.util.Set<String> floats = new java.util.HashSet<>();
            for (SpellAbility sa : candidates) {
                // Every castable entry is offered: a cap here hides spells from the seat without a log
                // line (issue #106).
                if (offerable(sa, me, altCosts.contains(sa))) options.add(sa);
                else if (everyLand && costlyManaAbility(sa, me)) options.add(sa);
                else if (everyLand && floatableMana(sa, me) && floats.add(sa.getHostCard().getName() + "|" + sa.getDescription())) options.add(sa);
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
                        if (!everyLand && landPlays.size() >= 4) break;
                    }
                }
            }
            return new PlayOptions(options, landPlays, landCards);
        }

        /** The engine's own "may this be played now": canPlay() leaves out the statics that forbid a play (Pithing Needle,
         *  Null Rod, Gaddock Teeg, Rule of Law), which Forge checks only as the play is made (checkRestrictions); the
         *  seat's menu offered Endbringer's ping under a Needle naming it and the harness played it (rules pilot RP_207,
         *  batch 10). */
        static boolean playable(SpellAbility sa, Player me) {
            if (sa.getActivatingPlayer() == null) sa.setActivatingPlayer(me);
            if (!sa.canPlay()) return false;
            // A spell's restrictions are about it on the stack, cast from its zone, as Forge's AI asks them
            // (AiController.canPlaySa): asked about the card in hand, Drannith Magistrate's "not cast from their hand"
            // hid every hand spell, and Grafdigger's Cage's graveyard origin never matched a flashback.
            Card host = sa.getHostCard();
            if (sa.isSpell() && host != null) {
                Card lki = forge.game.card.CardCopyService.getLKICopy(host);
                lki.setLKICMC(-1);
                lki.setLastKnownZone(me.getGame().getStackZone());
                lki.setCastFrom(host.getZone());
                lki.setSplitStateToPlayAbility(sa);  // the face being cast (an adventure, a split half, an MDFC back)
                return sa.checkRestrictions(lki, me);
            }
            return sa.checkRestrictions(me);
        }

        /** A mana ability whose cost is more than tapping (Lion's Eye Diamond's sacrifice and discard, Lotus Petal's
         *  sacrifice, a Spirit Guide exiled from hand) is a decision a combo turns on: crack LED in response to your own
         *  Infernal Tutor to be hellbent when it resolves. Offered on the faithful seat's menu when it can be activated
         *  now; one that only taps stays the payment's (asking it would flood the menu with floating mana). */
        static boolean costlyManaAbility(SpellAbility sa, Player me) {
            if (!sa.isManaAbility() || sa.getPayCosts() == null) return false;
            forge.game.cost.Cost c = sa.getPayCosts();
            // A loyalty ability that adds mana (Lukka, Bound to Ruin's +1) changes loyalty: a play of its own, not a
            // payment step, and it was never on the menu.
            boolean beyondTap = c.hasSpecificCostType(forge.game.cost.CostSacrifice.class)
                    || c.hasSpecificCostType(forge.game.cost.CostDiscard.class)
                    || c.hasSpecificCostType(forge.game.cost.CostExile.class)
                    || c.hasSpecificCostType(forge.game.cost.CostPutCounter.class)
                    || c.hasSpecificCostType(forge.game.cost.CostRemoveCounter.class) || sa.isPwAbility();
            if (!beyondTap) return false;
            try { sa.setActivatingPlayer(me); return playable(sa, me) && legallyPayable(sa, me, false); }
            catch (Exception e) { return false; }
        }

        /** Card hints for Forge's AI that its payment check obeys: AIPhyrexianPayment ("Never" on Gitaxian Probe and
         *  Mental Misstep, "OnFatalDamage.N" on Gut Shot) refuses to pay Phyrexian mana with life. They are advice to
         *  the AI, not rules, so a harness seat's checks run with them hidden: with no blue source, Probe was "(not
         *  castable now)" and off the menu though 2 life pays it (hard draft h_0). Restored after every check. */
        static final String AI_PHYREXIAN_HINT = "AIPhyrexianPayment";

        /** {@code check} with {@code sa}'s AI payment hint hidden: whether a harness seat can pay, by the rules only. */
        private static boolean seatCheck(SpellAbility sa, java.util.function.BooleanSupplier check) {
            String hint = sa == null ? null : sa.getParam(AI_PHYREXIAN_HINT);
            if (hint == null) return check.getAsBoolean();
            sa.removeParam(AI_PHYREXIAN_HINT);
            try { return check.getAsBoolean(); } finally { sa.putParam(AI_PHYREXIAN_HINT, hint); }
        }
        static boolean seatCanPay(SpellAbility sa, Player p, int extra, boolean effect) {
            return seatCanPay(sa.getPayCosts(), sa, p, extra, effect);
        }
        static boolean seatCanPay(Cost cost, SpellAbility sa, Player p, int extra, boolean effect) {
            return seatCheck(sa, () -> ComputerUtilMana.canPayManaCost(cost, sa, p, extra, effect)
                    || (hasTapTypeManaSource(p)
                        && tapTypeStep(ComputerUtilMana.calculateManaCost(cost, sa, p, true, extra, effect), sa, p, effect) != null));
        }
        static boolean seatCanPay(forge.game.mana.ManaCostBeingPaid cost, SpellAbility sa, Player p, boolean effect) {
            return seatCheck(sa, () -> ComputerUtilMana.canPayManaCost(cost, sa, p, effect)
                    || (hasTapTypeManaSource(p) && tapTypeStep(cost, sa, p, effect) != null));
        }

        /** The first activation of a payment that starts with a "tap N untapped X you control" mana ability, which
         *  Forge's greedy test payment skipped; null if none (#367; the board is in ProposeActionsTest). Each such
         *  ability the seat's payment activates itself ({@code simulableTapPart}) is tried with each set of cards its
         *  tap part may take, its mana spent on {@code need} and those cards tapped while the engine's test pays the
         *  rest, up to {@link #TAP_TYPE_DEPTH} activations and {@link #TAP_TYPE_BUDGET} tests; every card is untapped
         *  again before it returns. */
        static TapTypeStep tapTypeStep(forge.game.mana.ManaCostBeingPaid need, SpellAbility sa, Player me, boolean effect) {
            return tapTypeSearch(need, sa, me, effect, new int[] {TAP_TYPE_BUDGET}, TAP_TYPE_DEPTH);
        }
        static final int TAP_TYPE_DEPTH = 3, TAP_TYPE_BUDGET = 48;

        /** A tap-type mana ability to activate first, and the cards the rest of the payment still taps (its tap part
         *  must leave them alone). */
        static final class TapTypeStep {
            final SpellAbility ability;
            final CardCollection taps = new CardCollection(), reserved = new CardCollection();
            TapTypeStep(SpellAbility ability) { this.ability = ability; }
        }

        static boolean hasTapTypeManaSource(Player me) {
            return me.getCardsIn(ZoneType.Battlefield).anyMatch(c -> c.getManaAbilities().anyMatch(ma ->
                    ma.getPayCosts() != null && ma.getPayCosts().hasSpecificCostType(forge.game.cost.CostTapType.class)));
        }

        private static TapTypeStep tapTypeSearch(forge.game.mana.ManaCostBeingPaid need, SpellAbility sa, Player me,
                boolean effect, int[] budget, int depth) {
            if (depth <= 0) return null;
            // A card the paid ability taps for its own {T} is never one of them, as in the engine's
            // checkTapTypeCost (Elvish Lyrist's {G}, {T}: pr-review of #375).
            Card ownTap = sa.getPayCosts() != null && sa.getPayCosts().hasTapCost() ? sa.getHostCard() : null;
            for (Card host : new CardCollection(me.getCardsIn(ZoneType.Battlefield))) {
                for (SpellAbility ma : host.getManaAbilities()) {
                    forge.game.cost.CostTapType part = simulableTapPart(ma, sa, me, effect);
                    if (part == null) continue;
                    int n = part.getAbilityAmount(ma);
                    CardCollection cands = CardLists.filter(CardLists.getValidCards(me.getCardsIn(ZoneType.Battlefield),
                            part.getType().split(";"), me, host, ma), CardPredicates.CAN_TAP);
                    if (!part.canTapSource) cands.remove(host);
                    if (ownTap != null) cands.remove(ownTap);
                    if (n <= 0 || cands.size() < n) continue;
                    // Cards that make no mana of their own by tapping go first: tapping them costs the payment nothing.
                    cands.sort(java.util.Comparator.comparingInt(c -> c.getManaAbilities().anyMatch(m ->
                            m.getPayCosts() != null && m.getPayCosts().hasTapCost()) ? 1 : 0));
                    forge.game.mana.ManaCostBeingPaid rest = new forge.game.mana.ManaCostBeingPaid(need);
                    if (!spendPredicted(rest, ComputerUtilMana.predictMana(ma, me, nextShard(rest)), me)) continue;
                    int[] idx = new int[n];
                    for (int i = 0; i < n; i++) idx[i] = i;
                    do {
                        if (budget[0]-- <= 0) return null;
                        TapTypeStep step = new TapTypeStep(ma);
                        for (int i : idx) step.taps.add(cands.get(i));
                        if (ma.getPayCosts().hasTapCost()) step.taps.add(host);
                        for (Card c : step.taps) c.setTapped(true);
                        try {
                            if (rest.isPaid()) {
                                // No test pays the rest, so PAYS_TAP_COST still names an earlier try's cards, which the
                                // tap part's engine pick would avoid.
                                AiCardMemory.clearMemorySet(me, AiCardMemory.MemorySet.PAYS_TAP_COST);
                                if (ownTapPartsPayable(sa, step.reserved, effect)) return step;
                                continue;
                            }
                            if (ComputerUtilMana.canPayManaCost(rest, sa, me, effect)) {
                                Set<Card> plan = AiCardMemory.getMemorySet(me, AiCardMemory.MemorySet.PAYS_TAP_COST);
                                if (plan != null) step.reserved.addAll(plan);
                                if (ownTapPartsPayable(sa, step.reserved, effect)) return step;
                                step.reserved.clear();   // that plan leaves sa's own tap unpaid: look deeper
                            }
                            TapTypeStep next = tapTypeSearch(rest, sa, me, effect, budget, depth - 1);
                            if (next != null) {
                                step.reserved.addAll(next.reserved);
                                step.reserved.addAll(next.taps);
                                return step;
                            }
                        } finally {
                            for (Card c : step.taps) c.setTapped(false);
                        }
                    } while (nextCombination(idx, cands.size()));
                }
            }
            return null;
        }

        /** Whether {@code sa}'s own tap parts can still be paid beside a payment the search found: its taps are tapped
         *  already, and {@code restTaps} (what the rest of the payment taps) is tapped for the check. Its {T} is not
         *  asked: the real payment pays it before the mana (Cost.sort), so the host is tapped by then (Elvish Hunter,
         *  pr-review of #375), and the search never takes it ({@code ownTap}). */
        private static boolean ownTapPartsPayable(SpellAbility sa, java.util.Collection<Card> restTaps, boolean effect) {
            Cost c = sa.getPayCosts();
            if (c == null || !c.hasSpecificCostType(forge.game.cost.CostTapType.class)) return true;
            List<Card> tapped = new ArrayList<>();
            for (Card card : restTaps) if (!card.isTapped()) { card.setTapped(true); tapped.add(card); }
            try {
                for (forge.game.cost.CostPart part : c.getCostParts())
                    if (part instanceof forge.game.cost.CostTapType && !part.canPay(sa, sa.getActivatingPlayer(), effect)) return false;
                return true;
            } finally { for (Card card : tapped) card.setTapped(false); }
        }

        /** {@code ma}'s tap part, when the search may try it: activatable now, a cost of that tap part and at most
         *  {T}, mana of fixed colors {@code sa} may spend. */
        private static forge.game.cost.CostTapType simulableTapPart(SpellAbility ma, SpellAbility sa, Player me, boolean effect) {
            Cost c = ma.getPayCosts();
            if (c == null || ma.getApi() == ApiType.ManaReflected) return null;
            forge.game.cost.CostTapType tapPart = null;
            for (forge.game.cost.CostPart part : c.getCostParts()) {
                if (part instanceof forge.game.cost.CostTapType && tapPart == null) tapPart = (forge.game.cost.CostTapType) part;
                else if (!(part instanceof forge.game.cost.CostTap)) return null;
            }
            if (tapPart == null || tapPart.getType().contains("+withTotalPowerGE") || tapPart.getType().contains("sharesCreatureTypeWith"))
                return null;
            forge.game.spellability.AbilityManaPart mana = ma.getManaPart();
            if (mana == null || mana.isAnyMana() || mana.isComboMana() || !mana.meetsManaRestrictions(sa)) return null;
            ma.setActivatingPlayer(me);
            return playable(ma, me) && CostPayment.canPayAdditionalCosts(c, ma, effect) ? tapPart : null;
        }

        private static forge.card.mana.ManaCostShard nextShard(forge.game.mana.ManaCostBeingPaid cost) {
            List<forge.card.mana.ManaCostShard> unpaid = cost.getUnpaidShards();
            return unpaid.isEmpty() ? forge.card.mana.ManaCostShard.GENERIC : unpaid.get(0);
        }

        /** Spends {@code produced} ("G G G", as ComputerUtilMana.predictMana says it) on {@code cost}, as the engine's
         *  test payment does; whether any of it was needed. */
        private static boolean spendPredicted(forge.game.mana.ManaCostBeingPaid cost, String produced, Player me) {
            boolean used = false;
            for (String unit : produced.trim().split("\\s+")) {
                if (unit.isEmpty()) continue;
                if (unit.chars().allMatch(Character::isDigit)) {
                    for (int i = Integer.parseInt(unit); i > 0; i--) used |= cost.ai_payMana("1", me.getManaPool());
                } else {
                    used |= cost.ai_payMana(forge.card.MagicColor.toShortString(unit), me.getManaPool());
                }
            }
            return used;
        }

        /** The next {@code idx.length}-of-{@code n} combination, in lexicographic order; false after the last. */
        private static boolean nextCombination(int[] idx, int n) {
            int k = idx.length, i = k - 1;
            while (i >= 0 && idx[i] == n - k + i) i--;
            if (i < 0) return false;
            idx[i]++;
            for (int j = i + 1; j < k; j++) idx[j] = idx[j - 1] + 1;
            return true;
        }

        /** Whether the engine allows paying {@code cost} now: its mana and every other part, by the engine's own tests
         *  (the last line of ComputerUtilCost.canPayCost). The rest of that method is Forge AI's taste, not the rules: it
         *  refused, half the time at random, a planeswalker ability that spends all its loyalty, refused ward costs and
         *  casualty creatures it would not pay, and kept a tax it expects later. A legal option is the model's to take or
         *  leave (owner, 2026-10-17: "if it's a legal action we don't disallow it"). */
        static boolean legallyPayable(Cost cost, SpellAbility sa, Player p, boolean effect) {
            if (sa.getActivatingPlayer() == null) sa.setActivatingPlayer(p);
            return seatCanPay(cost, sa, p, 0, effect)
                    && forge.game.cost.CostPayment.canPayAdditionalCosts(cost, sa, effect, p);
        }
        static boolean legallyPayable(SpellAbility sa, Player p, boolean effect) { return legallyPayable(sa.getPayCosts(), sa, p, effect); }

        /** Whether activating mana ability {@code sa} now is a pass in all but name: a land's ability that costs only
         *  {T} and mana and only adds mana, into a pool that empties without burn, with no trigger on a tap or on mana
         *  added anywhere. Anything else floating does (a creature that can no longer block, Manabarbs, Upwelling's
         *  pool, mana burn) is the seat's to weigh. */
        static boolean floatChangesNothing(SpellAbility sa, Player me) {
            if (!sa.isManaAbility() || sa.getPayCosts() == null || costlyManaAbility(sa, me)) return false;
            try {
                Card host = sa.getHostCard();
                if (host == null || !host.isLand() || host.isCreature() || !onlyMakesMana(sa)) return false;
                for (forge.game.cost.CostPart part : sa.getPayCosts().getCostParts())
                    if (!(part instanceof forge.game.cost.CostTap) && !(part instanceof forge.game.cost.CostPartMana)) return false;
                if (forge.game.staticability.StaticAbilityUnspentMana.hasManaBurn(me)
                        || !forge.game.staticability.StaticAbilityUnspentMana.getManaToKeep(me).isEmpty()) return false;
                for (Card c : me.getGame().getCardsIn(java.util.EnumSet.of(ZoneType.Battlefield, ZoneType.Command)))
                    for (forge.game.trigger.Trigger t : c.getTriggers()) {
                        forge.game.trigger.TriggerType m = t.getMode();
                        if (m == forge.game.trigger.TriggerType.TapsForMana || m == forge.game.trigger.TriggerType.ManaAdded
                                || m == forge.game.trigger.TriggerType.Taps || m == forge.game.trigger.TriggerType.TapAll) return false;
                    }
                return true;
            } catch (Exception e) { return false; }
        }

        /** A mana ability that only taps (or costs mana), activatable now: floating mana is legal, so it is on the menu. */
        static boolean floatableMana(SpellAbility sa, Player me) {
            if (!sa.isManaAbility() || sa.getPayCosts() == null) return false;
            try { sa.setActivatingPlayer(me); return playable(sa, me) && legallyPayable(sa, me, false); }
            catch (Exception e) { return false; }
        }

        // ---------------- PROPOSE_ACTIONS: a proposed play, checked against the menu the seat is not shown ----------------

        /** PROPOSE_AUDIT=1: at every play decision, each menu play is turned into the proposal the guide describes and
         *  checked to land on that play ({@link #auditProposals}); a miss is logged "[PROPOSE-AUDIT] MISS". A checker
         *  that refuses a legal play would score a seat down for the harness's fault (Jericho's valid-action oracle
         *  has such false negatives). Diagnostic only, and a condition: it registers handles early, so what a seat reads may differ; the game line
         *  stamps it (`propose_audit`), so an audited game never pools with a plain one. */
        static final boolean PROPOSE_AUDIT = "1".equals(System.getenv("PROPOSE_AUDIT"));
        int auditChecked, auditMissed;

        /** The proposal for menu play {@code sa}, built from what the seat is shown (its handle, its ability's number in
         *  YOUR ACTIVATED ABILITIES, the way it is cast), or null when the abilities list does not show it. */
        AnswerTool.Proposal proposalFor(SpellAbility sa) {
            Card c = sa.getHostCard();
            observer.describe(c, false);
            AnswerTool.Proposal p = new AnswerTool.Proposal();
            p.card = observer.handleOf(c);
            if (sa.isLandAbility()) { p.verb = "play_land"; return p; }
            p.targets = firstTarget(sa);
            if (sa.isSpell()) {
                p.verb = "cast";
                p.cost = castWay(sa);
                if (!faceOf(sa).equals(c.getName())) p.face = faceOf(sa);
                return p;
            }
            p.verb = "activate";
            List<SpellAbility> abs = proposalAbilities(c);
            int i = abs.indexOf(sa);
            if (i < 0 && sa.getOriginalAbility() != null) i = abs.indexOf(sa.getOriginalAbility());
            for (int j = 0; i < 0 && j < abs.size(); j++)
                if (java.util.Objects.equals(abs.get(j).getDescription(), sa.getDescription())) i = j;
            if (i < 0) return null;
            p.ability = i + 1;
            return p;
        }

        /** The first legal target of a play whose one target a proposal names ({@link #proposalTarget}), as the seat
         *  would write it; null for any other play. */
        List<String> firstTarget(SpellAbility play) {
            SpellAbility part = singleTargetPart(play, getPlayer());
            if (part == null) return null;
            List<forge.game.GameObject> cands = targetCandidates(part, part.getTargetRestrictions());
            if (cands.isEmpty()) return null;
            forge.game.GameObject t = cands.get(0);
            if (t instanceof Card) { observer.describe((Card) t, false); return List.of("@" + observer.handleOf((Card) t)); }
            if (t instanceof Player) return List.of(t == getPlayer() ? "you" : "opponent");
            return null;
        }

        /** {@link #PROPOSE_AUDIT}: every play on this menu, proposed as the seat would, must land on itself (or on a play
         *  with the same label: a mana ability stands for every copy). */
        void auditProposals(List<SpellAbility> options, List<SpellAbility> landPlays, List<Card> landCards, Player me, boolean ownMain) {
            // matchProposal leaves the target of the last play it accepts pending for ensureTargetsSet: restored, or the
            // seat's next targeted cast in a menu game takes the audit's target unasked (ProposeActionsTest).
            Object pending = proposedTarget;
            try { auditEach(options, landPlays, landCards, me, ownMain); }
            finally { proposedTarget = pending; }
        }

        private void auditEach(List<SpellAbility> options, List<SpellAbility> landPlays, List<Card> landCards, Player me, boolean ownMain) {
            List<SpellAbility> all = new ArrayList<>(options);
            all.addAll(landPlays);
            List<String> labels = playLabels(options, landCards);
            int passIdx = 1 + all.size();
            for (int i = 0; i < all.size(); i++) {
                SpellAbility sa = all.get(i);
                String why;
                try {
                    AnswerTool.Proposal p = proposalFor(sa);
                    if (p == null) why = "not in YOUR ACTIVATED ABILITIES";
                    else {
                        int[] hit = {-1};
                        // A copy: an accepted proposal of another land's copy takes that menu entry's place.
                        String err = matchProposal(p, new ArrayList<>(options), landPlays, landCards, passIdx, me, ownMain, hit);
                        SpellAbility got = hit[0] >= 1 && hit[0] <= all.size() ? all.get(hit[0] - 1) : null;
                        // The same play: the same label, or a cast of the same card the same way (a may-play permission
                        // listed twice: Armor of Thorns at sorcery speed, where its sacrifice does not apply, kw27).
                        boolean same = hit[0] == i + 1 || got != null && (labels.get(hit[0] - 1).equals(labels.get(i))
                                || got.isSpell() && sa.isSpell() && got.getHostCard() == sa.getHostCard()
                                   && java.util.Objects.equals(castWay(got), castWay(sa)) && faceOf(got).equals(faceOf(sa)));
                        why = err != null ? p.render() + " -> " + err
                                : same ? null
                                : p.render() + " -> option " + hit[0] + " (" + (hit[0] >= 1 && hit[0] <= labels.size() ? labels.get(hit[0] - 1) : "?") + ")";
                    }
                } catch (Exception e) { why = "error " + e; }
                auditChecked++;
                if (why != null) { auditMissed++; p("[PROPOSE-AUDIT] MISS " + labels.get(i) + " :: " + why); }
            }
            p("[PROPOSE-AUDIT] checked=" + auditChecked + " missed=" + auditMissed);
        }

        /** The play decision's text in place of the numbered menu (PROPOSE_ACTIONS). */
        static final String PROPOSE_PROMPT = "You are not shown the legal plays. Propose ONE: play a land, cast a spell, activate "
                + "an ability, or pass. The rules engine checks it before anything happens.\n";

        /** The play prompt up to the reasoning tail, under PROPOSE_ACTIONS: the state and the decision, no menu. */
        static String proposePromptPrefix(String snapshot, PhaseType phase, boolean ownMain) {
            return snapshot + "\nDECISION: You have priority (" + phase
                    + (ownMain ? ", your main phase" : ", instant-speed window") + "). " + PROPOSE_PROMPT;
        }

        /** The activated abilities a proposal numbers for {@code c}, in a fixed order: its non-mana abilities as its
         *  current face lists them, then its mana abilities. SeatObservation's YOUR ACTIVATED ABILITIES shows this list,
         *  and a proposed `ability` N is its Nth entry. */
        static List<SpellAbility> proposalAbilities(Card c) {
            List<SpellAbility> out = new ArrayList<>();
            try {
                // Activated abilities and the special actions a card offers from its zone (suspend, foretell, plot): the
                // menu offers both as ACTIVATE, and the gauntlet audit found the special actions unproposable.
                for (SpellAbility sa : c.getSpellAbilities())
                    if (!sa.isSpell() && !sa.isLandAbility() && !sa.isManaAbility() && !sa.isTrigger()) out.add(sa);
                for (SpellAbility sa : c.getManaAbilities()) out.add(sa);
            } catch (Exception ignore) { /* a card the engine cannot list: none */ }
            return out;
        }

        /** A land whose abilities only tap it for mana: its mana is spent through payment, so the abilities list leaves
         *  it out (twenty lands would be twenty lines). Its abilities are still numbered and can still be proposed. */
        static boolean onlyTapsForMana(Card c, List<SpellAbility> abs) {
            if (!c.isLand()) return false;
            for (SpellAbility sa : abs) {
                if (!sa.isManaAbility() || sa.getPayCosts() == null) return false;
                for (forge.game.cost.CostPart part : sa.getPayCosts().getCostParts())
                    if (!(part instanceof forge.game.cost.CostTap)) return false;
            }
            return true;
        }

        /** An ability as the card states it: its cost and effect, the engine's placeholders and notes removed. */
        static String abilityText(SpellAbility sa) {
            String d = sa.getDescription();
            if (d == null || d.isEmpty()) d = String.valueOf(sa);
            d = SeatObservation.engineNotesOut(d, sa);
            return named(d, sa.getHostCard()).replaceAll("\\s*\\[[A-Za-z ]+: [^\\]]*\\(\\d+\\)\\]", "").replaceAll("\\s+", " ").trim();
        }

        /** The way a menu cast pays, by the name a proposal's `cost` gives it: an alternative cost's name, an optional
         *  cost's name, "alternative" for a "rather than pay this spell's mana cost" cast; null for the normal cast. */
        static String castWay(SpellAbility sa) {
            try {
                if (sa.getAlternativeCost() != null) return altCostName(sa.getAlternativeCost());
                // A prototype cast is a keyword's spell, basic to the engine (Arcane Proxy: PROPOSE_AUDIT, kw18).
                if (sa.isKeyword(forge.game.keyword.Keyword.PROTOTYPE)) return "Prototype";
                // A cast a card allows at instant speed (Armor of Thorns: "sacrifice it at the next cleanup step"),
                // a may-play option with its own text (PROPOSE_AUDIT, kw27). A proposal names it "flash".
                if (sa.getMayPlayOption() != null && sa.getMayPlay() != null && sa.getMayPlay().hasParam("MayPlayWithFlash"))
                    return "flash";
                List<String> names = new ArrayList<>();
                for (forge.game.spellability.OptionalCost o : sa.getOptionalCosts()) names.add(optionalCostName(o));
                if (!names.isEmpty()) return String.join(", ", names);
                return sa.isBasicSpell() ? null : "alternative";
            } catch (Exception e) { return null; }
        }

        /** True when {@code cost} names no way to cast that the menu tells apart: a word for the normal cast ("normal",
         *  "none") or an optional additional cost the card has (kicker, buyback, entwine), which the menu's normal cast
         *  carries and the cast then asks about, as it does under the menu. Refusing it would call a legal cast a rules
         *  error (pr-review #342). A cast the menu lists only with that cost still matches by its name. */
        static boolean namesTheNormalCast(String cost, Card c, List<SpellAbility> options) {
            String want = cost.toLowerCase(java.util.Locale.ROOT).trim();
            if (want.isEmpty() || "normal".equals(want) || "none".equals(want)) return true;
            for (SpellAbility o : options) if (o.isSpell() && o.getHostCard() == c && costMatches(o, cost)) return false;
            try {
                for (SpellAbility sa : c.getAllSpellAbilities())
                    if (sa.isSpell())
                        for (OptionalCostValue v : GameActionUtil.getOptionalCostValues(sa))
                            if (optionalCostName(v.getType()).toLowerCase(java.util.Locale.ROOT).equals(want)) return true;
            } catch (Exception ignore) { }
            return false;
        }

        /** An optional cost's name as the card prints it ("Kicker", "Buyback"), without the engine's brackets. */
        static String optionalCostName(forge.game.spellability.OptionalCost o) {
            return o.getName().replaceAll("^\\((.*)\\)$", "$1");
        }

        static boolean costMatches(SpellAbility sa, String cost) {
            if (cost == null) return true;
            String way = castWay(sa);
            if (way == null) return false;
            String w = way.toLowerCase(java.util.Locale.ROOT), c = cost.toLowerCase(java.util.Locale.ROOT).trim();
            // "alternative" is a "rather than pay this spell's mana cost" cast: a pitch spell or a named alternative cost,
            // never a flash or prototype cast or an optional cost.
            if ("alternative".equals(c)) return "alternative".equals(w) || sa.getAlternativeCost() != null;
            // A cast with several optional costs is named by any one of them; a name never matches a longer one ("flash"
            // is not "Flashback").
            return w.equals(c) || java.util.Arrays.asList(w.split(", ")).contains(c);
        }

        /** The face or half a cast is of, by name (the card's own name for a one-faced card). */
        static String faceOf(SpellAbility sa) {
            try { if (sa.getCardState() != null && sa.getCardState().getName() != null) return sa.getCardState().getName(); } catch (Exception ignore) { }
            return sa.getHostCard() == null ? "" : sa.getHostCard().getName();
        }

        static boolean sameAbility(SpellAbility o, SpellAbility want, Card c) {
            if (o.isSpell() || o.getHostCard() != c) return false;
            return o == want || o.getOriginalAbility() == want
                    || java.util.Objects.equals(o.getDescription(), want.getDescription()) && o.isManaAbility() == want.isManaAbility();
        }

        private String refOf(Card c) { return observer == null ? safeName(c) : observer.shortRef(c); }

        /** The target an accepted proposal named (a Card or Player), set by {@link #withTarget} and used by
         *  ensureTargetsSet for the play's one targeting step. Null when none is pending. */
        Object proposedTarget;

        /** The proposal matched {@code play} (hit[0] set): its target is checked with it. A play with one targeting
         *  step and one target, whose candidates are cards and players, must name it in `targets`; a wrong one is refused
         *  for the rule it breaks ({@link #targetRejection}) and no list is shown. Any other play (modes, several targets,
         *  divided amounts, a spell on the stack as the target) keeps its target ask with the candidates. Null when legal. */
        private String withTarget(AnswerTool.Proposal pr, SpellAbility play, Player me, int[] hit) {
            String err;
            boolean was = PRICING_FOR_SNAPSHOT;
            PRICING_FOR_SNAPSHOT = true;
            try { err = proposalTarget(pr, play, me); }
            catch (Exception e) { err = null; proposedTarget = null; }   // cannot be checked: the target ask stays
            finally { PRICING_FOR_SNAPSHOT = was; }
            if (err != null) hit[0] = -1;
            return err;
        }

        /** The one targeting step a proposal names its target for: a play with exactly one part that targets, one target
         *  and no division, never a modal spell (its modes come first; their targets are asked after). Null for any
         *  other play, which keeps its target ask. One rule for the checker and for the audit's proposals. */
        private static SpellAbility singleTargetPart(SpellAbility play, Player me) {
            if (play.getApi() == ApiType.Charm) return null;
            SpellAbility part = null;
            for (SpellAbility s = play; s != null; s = s.getSubAbility()) if (s.usesTargeting()) { if (part != null) return null; part = s; }
            if (part == null || part.getMaxTargets() > 1 || part.isDividedAsYouChoose()) return null;
            if (part.getActivatingPlayer() == null) part.setActivatingPlayer(me);
            return part.getTargetRestrictions() == null ? null : part;
        }

        private String proposalTarget(AnswerTool.Proposal pr, SpellAbility play, Player me) {
            proposedTarget = null;
            SpellAbility part = singleTargetPart(play, me);
            if (part == null) return null;
            TargetRestrictions tr = part.getTargetRestrictions();
            List<forge.game.GameObject> cands = targetCandidates(part, tr);
            for (forge.game.GameObject o : cands) if (!(o instanceof Card) && !(o instanceof Player)) return null;
            String what = refOf(play.getHostCard());
            if (pr.targets == null || pr.targets.isEmpty()) {
                if (part.getMinTargets() == 0) return null;   // "up to one": no target is a legal choice
                return "FORMAT: this play targets: give its target in `targets`, a handle such as \"@7\", or \"you\" or \"opponent\".";
            }
            if (pr.targets.size() > 1) return "FORMAT: this play has one target; give one in `targets`.";
            String name = pr.targets.get(0);
            Object t = targetNamed(name, me);
            if (t == null) return "FORMAT: no object or player \"" + name + "\" is in view; give a handle such as \"@7\", or \"you\" or \"opponent\".";
            if (!cands.contains(t)) return targetRejection(part, t, me, what);
            proposedTarget = t;
            return null;
        }

        /** A proposal's target by what it says: "@N" (a visible object), "you", "opponent" or a player's name. */
        private Object targetNamed(String name, Player me) {
            String n = name.trim();
            java.util.regex.Matcher m = Pattern.compile("@\\s*(\\d+)").matcher(n);
            if (m.find()) return observer == null ? null : observer.currentCard(Integer.parseInt(m.group(1)));
            String low = n.toLowerCase(java.util.Locale.ROOT);
            if (low.equals("you") || low.equals("me") || low.equals("myself") || n.equals(me.getName())) return me;
            for (Player o : me.getGame().getPlayers())
                if (o != me && (low.equals("opponent") || low.equals("the opponent") || n.equalsIgnoreCase(o.getName()))) return o;
            return null;
        }

        /** Why {@code t} is not a legal target of {@code part}: the static ability that forbids it (hexproof, shroud,
         *  protection, a Leyline), the zone, or the kind of object the card's text targets, which restates the
         *  card, not the legal set. Always "ILLEGAL (target)". */
        private String targetRejection(SpellAbility part, Object t, Player me, String what) {
            String ref = t instanceof Card ? refOf((Card) t) : t == me ? "you" : "your opponent";
            try {
                forge.game.staticability.StaticAbility sta =
                        forge.game.staticability.StaticAbilityCantTarget.cantTarget((forge.game.GameEntity) t, part);
                if (sta != null) {
                    String d = sta.toString();
                    if (d == null || d.isEmpty()) d = sta.getParam("Description");
                    d = d == null ? "" : SeatObservation.engineNotesOut(d, part).replaceAll("\\s*\\(.*$", "").trim();
                    return "ILLEGAL (target): " + ref + " cannot be the target of " + what
                            + (d.isEmpty() ? "." : " (" + named(d, sta.getHostCard()) + ").");
                }
            } catch (Exception ignore) { }
            if (t instanceof Card) {
                Card c = (Card) t;
                List<ZoneType> zones = part.getTargetRestrictions().getZone();
                if (c.getZone() != null && zones != null && !zones.contains(c.getZone().getZoneType()))
                    return "ILLEGAL (target): " + ref + " is in " + c.getZone().getZoneType().name().toLowerCase(java.util.Locale.ROOT)
                            + ", not a zone " + what + " targets in.";
                if (c.hasKeyword(forge.game.keyword.Keyword.SHROUD))
                    return "ILLEGAL (target): " + ref + " has shroud.";
                if (c.hasKeyword(forge.game.keyword.Keyword.HEXPROOF) && c.getController() != me)
                    return "ILLEGAL (target): " + ref + " has hexproof.";
            }
            return "ILLEGAL (target): " + ref + " is not something " + what + " can target (its text says what it targets).";
        }

        /** PROPOSE_ACTIONS: the 1-based menu number {@code pr} names on this decision's hidden menu, in {@code hit[0]}, and
         *  null; or the rejection sent back. The menu is the oracle: a proposal is accepted exactly when it names a play
         *  the menu would have offered, so the golden checks that cover the menu cover this arm. A miss is explained by the
         *  first engine check the play fails ({@link #proposalRejection}): its rule category, never the legal plays.
         *  A land's mana proposed from a copy the menu does not list replaces that name's entry in {@code options}. */
        String matchProposal(AnswerTool.Proposal pr, List<SpellAbility> options, List<SpellAbility> landPlays,
                             List<Card> landCards, int passIdx, Player me, boolean ownMain, int[] hit) {
            proposedTarget = null;
            if ("pass".equals(pr.verb)) { hit[0] = passIdx; return null; }
            Card c = observer == null ? null : observer.currentCard(pr.card);
            if (c == null) return notInView(pr.card);
            if ("play_land".equals(pr.verb)) {
                // One land per name is on the menu: a copy in the same zone stands for this one.
                for (int i = 0; i < landCards.size(); i++) {
                    Card lc = landCards.get(i);
                    if (lc == c || lc.getName().equals(c.getName()) && lc.getZone() == c.getZone()) { hit[0] = 1 + options.size() + i; return null; }
                }
                return proposalRejection(pr, c, null, me, ownMain);
            }
            if ("activate".equals(pr.verb)) {
                List<SpellAbility> abs = proposalAbilities(c);
                if (abs.isEmpty()) return "ILLEGAL (type): " + refOf(c) + " has no activated ability.";
                int n = pr.ability == null ? 1 : pr.ability;
                if (pr.ability == null && abs.size() > 1)
                    return "FORMAT: " + refOf(c) + " has " + abs.size() + " activated abilities; say which with `ability`, its number in YOUR ACTIVATED ABILITIES.";
                if (n > abs.size())
                    return "FORMAT: " + refOf(c) + " has " + abs.size() + (abs.size() == 1 ? " activated ability" : " activated abilities") + "; there is no ability " + n + ".";
                SpellAbility want = abs.get(n - 1);
                for (int i = 0; i < options.size(); i++) if (sameAbility(options.get(i), want, c)) { hit[0] = 1 + i; return withTarget(pr, options.get(i), me, hit); }
                // A mana ability is on the menu once per card name and ability. Another copy's entry shows the play is
                // allowed, when this copy could be activated itself (a tapped copy, or one in the hand, is refused for its
                // own rule), and this copy's ability takes the entry's place: copies differ (an aura such as Utopia
                // Sprawl, an awakened land), and the copy proposed is the one tapped (pr-review of #360).
                if (want.isManaAbility() && c.getController() == me && playable(want, me))
                    for (int i = 0; i < options.size(); i++) {
                        SpellAbility o = options.get(i);
                        if (o.isManaAbility() && o.getHostCard() != null && o.getHostCard().getName().equals(c.getName())
                                && java.util.Objects.equals(o.getDescription(), want.getDescription())) {
                            want.setActivatingPlayer(me);
                            options.set(i, want);
                            hit[0] = 1 + i;
                            return withTarget(pr, want, me, hit);
                        }
                    }
                return proposalRejection(pr, c, want, me, ownMain);
            }
            // cast
            if (pr.cost != null && namesTheNormalCast(pr.cost, c, options)) { pr = pr.copy(); pr.cost = null; }
            List<Integer> hits = new ArrayList<>();
            for (int i = 0; i < options.size(); i++) {
                SpellAbility o = options.get(i);
                if (o.isSpell() && o.getHostCard() == c && (pr.face == null || faceOf(o).equalsIgnoreCase(pr.face.trim()))
                        && costMatches(o, pr.cost)) hits.add(i);
            }
            if (hits.size() > 1 && pr.face == null) {
                // No face named: the card's own (front) face.
                List<Integer> front = new ArrayList<>();
                for (int i : hits) if (faceOf(options.get(i)).equals(c.getName())) front.add(i);
                if (!front.isEmpty()) hits = front;
            }
            if (!hits.isEmpty() && pr.cost == null) {
                // No cost named: the normal cast, when it is legal; otherwise the seat names the way it means, even when
                // that way is the only cast on the menu (Evoke, a pitch spell): the harness never picks it (#342).
                List<Integer> normal = new ArrayList<>();
                for (int i : hits) if (castWay(options.get(i)) == null) normal.add(i);
                if (normal.isEmpty())
                    return "FORMAT: say which way you cast " + refOf(c) + " with `cost` (the name the card prints, or \"alternative\").";
                hits = normal;
            }
            if (!hits.isEmpty()) { hit[0] = 1 + hits.get(0); return withTarget(pr, options.get(hits.get(0)), me, hit); }
            return proposalRejection(pr, c, null, me, ownMain);
        }

        /** Why the engine does not allow {@code pr} now: the first of its checks the play fails, as
         *  "ILLEGAL (category): one sentence". The categories are fixed words (timing, cost, zone, target, land drop,
         *  control, type, restriction) so a run can count them. The sentence says which rule, never what is legal
         *  instead (a detailed error becomes a hidden menu; Kaggle Game Arena returns only "illegal"). Pricing runs with
         *  PRICING_FOR_SNAPSHOT set, so no check can reach a model ask. */
        String proposalRejection(AnswerTool.Proposal pr, Card c, SpellAbility want, Player me, boolean ownMain) {
            boolean was = PRICING_FOR_SNAPSHOT;
            PRICING_FOR_SNAPSHOT = true;
            // rejection sets the seat as the activator to ask the engine; the ability keeps the one it had (an
            // opponent's ability is never left with the seat as its activator).
            Player activator = want == null ? null : want.getActivatingPlayer();
            try { return rejection(pr, c, want, me, ownMain); }
            catch (Exception e) { return "ILLEGAL (restriction): the rules do not allow this play now."; }
            finally {
                PRICING_FOR_SNAPSHOT = was;
                if (want != null) want.setActivatingPlayer(activator);
            }
        }

        private String rejection(AnswerTool.Proposal pr, Card c, SpellAbility want, Player me, boolean ownMain) {
            String r = refOf(c);
            Game game = me.getGame();
            ZoneType zone = c.getZone() == null ? null : c.getZone().getZoneType();
            String zoneWord = zone == null ? "that zone" : "your " + zone.name().toLowerCase(java.util.Locale.ROOT);
            boolean emptyStack = game.getStack().isEmpty();
            if ("play_land".equals(pr.verb)) {
                if (!c.isLand()) return "ILLEGAL (type): " + r + " is not a land; only a land is played, and a spell is cast.";
                if (zone == ZoneType.Battlefield) return "ILLEGAL (zone): " + r + " is already on the battlefield.";
                if (c.getOwner() != me) return "ILLEGAL (control): " + r + " is not yours to play.";
                if (!ownMain || !emptyStack) return "ILLEGAL (timing): a land is played only in your own main phase, with an empty stack.";
                if (me.getLandsPlayedThisTurn() >= me.getMaxLandPlays())
                    return "ILLEGAL (land drop): you have played " + me.getLandsPlayedThisTurn() + (me.getLandsPlayedThisTurn() == 1 ? " land" : " lands")
                            + " this turn, as many as you may.";
                if (zone != ZoneType.Hand) return "ILLEGAL (zone): a land is played from your hand; " + r + " is in " + zoneWord + ".";
                return "ILLEGAL (restriction): a rule or an effect does not allow playing " + r + " now.";
            }
            if ("activate".equals(pr.verb)) {
                SpellAbility sa = want;
                sa.setActivatingPlayer(me);
                forge.game.spellability.SpellAbilityRestriction restr = sa.getRestrictions();
                // Who may activate it (its controller, or its owner outside the battlefield; a few say any player).
                if (c.getController() != me && (restr == null || !restr.checkActivatorRestrictions(c, sa)))
                    return zone == ZoneType.Battlefield ? "ILLEGAL (control): you do not control " + r + "."
                            : "ILLEGAL (control): " + r + " is not yours; only its owner can activate its abilities there.";
                forge.game.cost.Cost cost = sa.getPayCosts();
                if (zone == ZoneType.Battlefield && cost != null && cost.hasTapCost()) {
                    if (c.isTapped()) return "ILLEGAL (cost): " + r + " is tapped, and this ability's cost taps it.";
                    if (c.isCreature() && c.isSick())
                        return "ILLEGAL (cost): " + r + " came under your control this turn, so it cannot pay a {T} cost (summoning sickness).";
                }
                if (!sa.isManaAbility() && game.getStack().isSplitSecondOnStack())
                    return "ILLEGAL (timing): a spell with split second is on the stack; only mana abilities can be activated until it resolves.";
                // canPlay answers the zone, timing, own limits and non-mana cost as one; asked apart, the category is the
                // rule broken. canCastTiming is asked without Forge's StaticAbilityCastWithFlash.anyWithFlashNeedsInfo
                // exemption (a flash grant that depends on a choice): such a play reads as timing, a label only.
                boolean inZone = restr == null || restr.checkZoneRestrictions(c, sa);
                if (inZone && !sa.canCastTiming(c, me))
                    return "ILLEGAL (timing): " + (sa.isPwAbility() ? "a loyalty ability" : "this ability")
                            + " is activated only as a sorcery: in your own main phase, with an empty stack.";
                if (inZone && restr != null && !restr.checkTimingRestrictions(c, sa))
                    return "ILLEGAL (timing): this ability's own timing (a phase or step, or whose turn it is) does not allow it now.";
                if (!playable(sa, me)) {
                    if (!inZone) return "ILLEGAL (zone): this ability cannot be activated while " + r + " is in " + zoneWord + ".";
                    if (restrictionsAllow(restr, c, sa) && !canPayRest(sa)) return REST_OF_COST;
                    return "ILLEGAL (restriction): this ability cannot be activated now: its own limits (such as once each turn) or an effect stop it.";
                }
                return costOrTarget(sa, r, me, false);
            }
            // cast
            if (c.isLand() && !c.isModal()) return "ILLEGAL (type): " + r + " is a land: it is played with play_land, not cast.";
            if (zone == ZoneType.Battlefield) return "ILLEGAL (zone): " + r + " is on the battlefield; a spell is cast from your hand, or from another zone only when a card allows it.";
            if (zone == ZoneType.Stack) return "ILLEGAL (zone): " + r + " is already on the stack.";
            // Before the ways to cast it are looked through: an opponent's card is refused for whose it is, not for a
            // cost or face it lacks. A card an effect lets the seat play (an opponent's card it exiled) is its to cast.
            // Shaman's Trance makes every graveyard the seat's to cast from (CR 109.5; SpellAbilityRestriction).
            if (c.getOwner() != me && c.getController() != me && zone != ZoneType.Hand && c.mayPlay(me).isEmpty()
                    && !(zone == ZoneType.Graveyard && me.hasKeyword("Shaman's Trance")))
                return "ILLEGAL (control): " + r + " is not yours to cast.";
            SpellAbility sa = null;
            // Every face's spells and their alternative costs. getAllPossibleAbilities is empty outside a window the card
            // could be cast in (Grizzly Bears at combat: ProposeActionsTest), which is exactly when a reason is needed.
            List<SpellAbility> spells = new ArrayList<>();
            for (SpellAbility o : c.getAllSpellAbilities()) {
                if (!o.isSpell()) continue;
                spells.add(o);
                try { spells.addAll(GameActionUtil.getAlternativeCosts(o, me, false)); } catch (Exception ignore) { }
            }
            for (SpellAbility o : spells) {
                if (pr.face != null && !faceOf(o).equalsIgnoreCase(pr.face.trim())) continue;
                if (pr.cost == null && castWay(o) == null || pr.cost != null && costMatches(o, pr.cost)) { sa = o; break; }
                if (sa == null) sa = o;
            }
            if (sa == null && pr.face != null) return "FORMAT: " + r + " has no face named \"" + pr.face + "\".";
            if (sa == null) return "ILLEGAL (type): " + r + " is not a spell that can be cast.";
            // The loop above already looked through every spell and its alternative costs for this name.
            if (pr.cost != null && !costMatches(sa, pr.cost)) return "ILLEGAL (cost): " + r + " cannot be cast for a cost named \"" + pr.cost + "\".";
            sa.setActivatingPlayer(me);
            if (game.getStack().isSplitSecondOnStack())
                return "ILLEGAL (timing): a spell with split second is on the stack; no spell can be cast until it resolves.";
            if (!sa.canCastTiming(c, me))
                return "ILLEGAL (timing): " + r + " can be cast only when you could cast a sorcery: in your own main phase, with an empty stack.";
            if (!playable(sa, me)) {
                // Spell.canPlay asks the non-mana cost too: Fling with no creature, Dread Return's flashback short of
                // three, an escape short of cards to exile break the cost rule, not the zone or a restriction.
                Card host = sa instanceof forge.game.spellability.Spell
                        ? java.util.Objects.requireNonNullElse(((forge.game.spellability.Spell) sa).getAlternateHost(c), c) : c;
                if (restrictionsAllow(sa.getRestrictions(), host, sa) && !canPayRest(sa)) return REST_OF_COST;
                if (zone != ZoneType.Hand) return "ILLEGAL (zone): " + r + " cannot be cast from " + zoneWord + (pr.cost == null ? " its normal way" : " that way") + ".";
                return "ILLEGAL (restriction): a rule or an effect does not allow casting " + r + " now.";
            }
            return costOrTarget(sa, r, me, pr.cost != null || castWay(sa) != null);
        }

        static final String REST_OF_COST =
                "ILLEGAL (cost): you cannot pay the rest of its cost now (what it taps, sacrifices, discards, exiles or removes).";

        /** The non-mana part of the cost can be paid now (Forge's own check, which canPlay makes too). */
        private static boolean canPayRest(SpellAbility sa) {
            return sa.getPayCosts() == null || forge.game.cost.CostPayment.canPayAdditionalCosts(sa.getPayCosts(), sa, false);
        }

        /** Every rule but the cost allows the play: the zone, the timing and the play's own limits (Forge's
         *  SpellAbilityRestriction.canPlay, the half of canPlay before the cost). */
        private static boolean restrictionsAllow(forge.game.spellability.SpellAbilityRestriction restr, Card host, SpellAbility sa) {
            try { return restr == null || restr.canPlay(host, sa); } catch (Exception e) { return false; }
        }

        /** The cost and target checks the play menu makes (offerable), as a rejection. */
        private String costOrTarget(SpellAbility sa, String r, Player me, boolean alt) {
            if (!(alt ? legallyPayable(sa, me, false) : seatCanPay(sa, me, 0, false))) {
                String delta = "";
                try {
                    if (sa.isSpell()) {
                        String a = affordability(me, sa.getHostCard(), untappedManaTally(me));
                        if (a != null && a.startsWith("(need")) delta = " " + a;
                    }
                } catch (Exception ignore) { }
                return "ILLEGAL (cost): you cannot pay its mana cost now" + delta + ".";
            }
            if (!canPayRest(sa)) return REST_OF_COST;
            if (sa.usesTargeting()) {
                TargetRestrictions tr = sa.getTargetRestrictions();
                if (tr != null && targetCandidates(sa, tr).size() < sa.getMinTargets())
                    return "ILLEGAL (target): it has no legal target now.";
            }
            return "ILLEGAL (restriction): the engine does not allow " + r + " to be played this way now.";
        }

        // ---- PROPOSE_ACTIONS combat: the attack and the blocks, proposed by handle and checked by the engine. The verdict
        // is the engine's own (CombatUtil.canAttack / validateAttackers, CombatUtil.canBlock / blocksError: the menu's checks);
        // the sentence only names which rule failed, never what is legal instead.

        /** The attack {@code decl} proposes ({attacker handle, defender as given}): null when the engine allows it, with
         *  {@code out} holding each attacker and what it attacks; else "FORMAT: ..." or "ILLEGAL (category): ...". The
         *  combat is left as it was. */
        String attackProposalError(List<String[]> decl, Player me, Combat combat, Map<Card, GameEntity> out) {
            out.clear();
            Map<Card, GameEntity> want = new java.util.LinkedHashMap<>();
            for (String[] p : decl) {
                Card c = observer == null ? null : observer.currentCard(Integer.parseInt(p[0]));
                if (c == null) return notInView(p[0]);
                GameEntity d = defenderNamed(p[1], me, combat);
                if (d == null) return "FORMAT: no player, planeswalker or battle \"" + p[1] + "\" is in view; give \"opponent\" or a handle such as \"@7\".";
                if (!combat.getDefenders().contains(d))
                    return "ILLEGAL (combat): " + entityRef(d, me) + " cannot be attacked: a creature attacks an opponent, or a "
                            + "planeswalker or battle it may attack.";
                if (!CombatUtil.canAttack(c, d)) return combatWhy(() -> attackerRejection(c, d, me), refOf(c) + " to attack");
                want.put(c, d);
            }
            // The whole attack is tried on a scratch combat: removeFromCombat leaves an empty band behind in the real one.
            Combat trial = new Combat(me);
            for (Card c : combat.getAttackers()) trial.addAttacker(c, combat.getDefenderByAttacker(c));
            for (Map.Entry<Card, GameEntity> e : want.entrySet()) trial.addAttacker(e.getKey(), e.getValue());
            if (!CombatUtil.validateAttackers(trial)) return combatWhy(() -> attackSetRejection(me, trial, want), "this attack");
            out.putAll(want);
            return null;
        }

        private static String notInView(Object handle) {
            return "FORMAT: no object @" + handle + " is in view now; use a handle from the state above.";
        }

        /** The blocks {@code decl} proposes ({blocker handle, attacker handle}): null when the engine allows them, with
         *  {@code out} holding the pairs {blocker, attacker}; else "FORMAT: ..." or "ILLEGAL (category): ...". Each pair is
         *  checked as the menu checks it ({@link #firstBlockNotAllowed}: CombatUtil.canBlock with the set's other blocks in
         *  place), then the set ({@link #blocksError}). */
        String blockProposalError(List<String[]> decl, Player me, Combat combat, List<Card[]> out) {
            out.clear();
            List<Card[]> pairs = new ArrayList<>();
            Map<Card, Integer> uses = new java.util.HashMap<>();
            for (String[] p : decl) {
                Card b = observer == null ? null : observer.currentCard(Integer.parseInt(p[0]));
                if (b == null) return notInView(p[0]);
                Card a = observer.currentCard(Integer.parseInt(p[1]));
                if (a == null) return notInView(p[1]);
                if (!combat.isAttacking(a)) return "ILLEGAL (combat): " + refOf(a) + " is not attacking.";
                int limit = blockLimit(b);
                if (uses.merge(b, 1, Integer::sum) > limit)
                    return "ILLEGAL (number): " + refOf(b) + " can block only " + (limit == 1 ? "one attacker." : limit + " attackers.");
                pairs.add(new Card[] {b, a});
            }
            String cap = blockCapError(me, pairs);
            if (cap != null) return "ILLEGAL (number): " + cap;
            int bad = firstBlockNotAllowed(combat, pairs);
            if (bad >= 0) {
                Card b = pairs.get(bad)[0], a = pairs.get(bad)[1];
                return combatWhy(() -> withBlocks(combat, pairs, bad, () -> blockerRejection(a, b, me, combat)), refOf(b) + " to block " + refOf(a));
            }
            String err = blocksError(combat, me, pairs);
            if (err != null) return combatWhy(() -> blockSetRejection(err, me, combat, pairs), "these blocks");
            out.addAll(pairs);
            return null;
        }

        /** The first pair the engine does not allow, judged as the declaration stands: CombatUtil.canBlock with every other
         *  pair of the set in place, so a requirement another blocker obeys (CR 509.1c: "must be blocked if able", a lure)
         *  does not refuse this one; -1 when every pair is allowed. Judged alone, on a combat with no blocks yet, a creature
         *  beside a must-be-blocked attacker's blocker was refused (pr-review of #349). The combat is left as it was. */
        static int firstBlockNotAllowed(Combat combat, List<Card[]> pairs) {
            for (int i = 0; i < pairs.size(); i++) {
                Card b = pairs.get(i)[0], a = pairs.get(i)[1];
                if (!withBlocks(combat, pairs, i, () -> CombatUtil.canBlock(a, b, combat))) return i;
            }
            return -1;
        }

        /** {@code f} with the blocks of {@code pairs} but the one at {@code except} added to the combat, then removed. */
        private static <T> T withBlocks(Combat combat, List<Card[]> pairs, int except, java.util.function.Supplier<T> f) {
            List<Card[]> on = new ArrayList<>();
            try {
                for (int i = 0; i < pairs.size(); i++) if (i != except) { combat.addBlocker(pairs.get(i)[1], pairs.get(i)[0]); on.add(pairs.get(i)); }
                return f.get();
            } finally {
                for (Card[] pr : on) combat.removeBlockAssignment(pr[1], pr[0]);
            }
        }

        /** A global block cap (Silent Arbiter) the set exceeds, as {@link #blocksError} words it, or null. Checked before
         *  the pairs, which the cap would otherwise refuse one by one without saying why. */
        static String blockCapError(Player defender, List<Card[]> pairs) {
            java.util.Set<Card> blocking = new java.util.HashSet<>();
            for (Card[] pr : pairs) blocking.add(pr[0]);
            int cap = forge.game.staticability.StaticAbilityBlockRestrict.blockRestrictNum(defender);
            return blocking.size() > cap ? blockCapWords(cap) : null;
        }

        /** How many attackers {@code b} may block (CR 509.1a): one, more when a card says so, or any number. */
        static int blockLimit(Card b) { return b.canBlockAny() ? Integer.MAX_VALUE : 1 + b.canBlockAdditional(); }

        /** The words of a global block cap's objection; {@link #blockSetRejection} reads its "no more than" start. */
        static String blockCapWords(int cap) {
            return "no more than " + cap + " creature" + (cap == 1 ? "" : "s") + " can block each combat.";
        }

        /** A combat rejection's sentence, worked out with no model ask possible; the verdict is already the engine's, so a
         *  failure here changes only the words. */
        private String combatWhy(java.util.function.Supplier<String> why, String what) {
            boolean was = PRICING_FOR_SNAPSHOT;
            PRICING_FOR_SNAPSHOT = true;
            try { String r = why.get(); if (r != null) return r; }
            catch (Exception ignore) { }
            finally { PRICING_FOR_SNAPSHOT = was; }
            return "ILLEGAL (restriction): the rules do not allow " + what + " now.";
        }

        /** What a proposed attack names: nothing or "opponent" (the defending player), a player's name, or a handle. */
        private GameEntity defenderNamed(String name, Player me, Combat combat) {
            String n = name == null ? "" : name.trim();
            if (n.isEmpty()) { for (Player p : combat.getDefendingPlayers()) return p; return null; }
            Object t = targetNamed(n.matches("\\d+") ? "@" + n : n, me);
            return t instanceof GameEntity ? (GameEntity) t : null;
        }

        private String entityRef(GameEntity e, Player me) {
            return e == me ? "you" : e instanceof Card ? refOf((Card) e) : e instanceof Player ? "your opponent" : String.valueOf(e);
        }

        /** " (Ensnaring Bridge @6: its text)": the static ability that forbids it and where it comes from ("its text" when
         *  that is {@code subject} itself). */
        private String staticNote(forge.game.staticability.StaticAbility st, Card subject) {
            Card host = st.getHostCard();
            String d = st.toString();
            if (d == null || d.isEmpty()) d = st.getParam("Description");
            d = d == null ? "" : named(d, host).replaceAll("\\s*\\(.*$", "").replaceAll("\\.+$", "").trim();
            String src = host == null ? "" : host == subject ? "its text" : refOf(host);
            return src.isEmpty() && d.isEmpty() ? "" : " (" + src + (d.isEmpty() || src.isEmpty() ? "" : ": ") + d + ")";
        }

        /** Why {@code c} cannot attack {@code d} alone (CombatUtil.canAttack is false), in the engine's order. */
        private String attackerRejection(Card c, GameEntity d, Player me) {
            String r = refOf(c);
            if (c.getZone() == null || c.getZone().getZoneType() != ZoneType.Battlefield || c.isPhasedOut())
                return "ILLEGAL (zone): " + r + " is not on the battlefield; only a creature on the battlefield attacks.";
            if (c.getController() != me) return "ILLEGAL (control): you do not control " + r + ".";
            if (!c.isCreature() || c.isBattle()) return "ILLEGAL (type): " + r + " is not a creature; only creatures attack.";
            if (c.isTapped()) return "ILLEGAL (tapped): " + r + " is tapped, and a tapped creature cannot attack.";
            if (CombatUtil.isAttackerSick(c, d))
                return "ILLEGAL (summoning sickness): " + r + " has not been under your control continuously since your most "
                        + "recent turn began, and it has no haste.";
            if (c.isGoaded())
                return "ILLEGAL (requirement): " + r + " is goaded: it attacks a player other than the one who goaded it, if able.";
            if (c.hasKeyword("CARDNAME can't attack.") || c.hasKeyword("CARDNAME can't attack or block."))
                return "ILLEGAL (restriction): " + r + " can't attack.";
            if (c.isDetained()) return "ILLEGAL (restriction): " + r + " is detained, so it can't attack or block.";
            for (Card ca : c.getGame().getCardsIn(ZoneType.STATIC_ABILITIES_SOURCE_ZONES))
                for (forge.game.staticability.StaticAbility st : ca.getStaticAbilities()) {
                    if (!st.checkConditions(forge.game.staticability.StaticAbilityMode.CantAttack)
                            || !forge.game.staticability.StaticAbilityCantAttackBlock.applyCantAttackAbility(st, c, d)) continue;
                    if (st.isKeyword(Keyword.DEFENDER)) return "ILLEGAL (defender): " + r + " has defender, so it cannot attack.";
                    return "ILLEGAL (restriction): " + r + " can't attack " + entityRef(d, me) + staticNote(st, c) + ".";
                }
            return null;
        }

        /** Why an attack of creatures that can each attack is not legal as a whole (CombatUtil.validateAttackers): a limit
         *  on how many attack, a creature's own restriction (alone, with others), or a requirement it leaves unmet. */
        private String attackSetRejection(Player me, Combat combat, Map<Card, GameEntity> want) {
            forge.game.combat.GlobalAttackRestrictions gr =
                    forge.game.combat.GlobalAttackRestrictions.getGlobalRestrictions(me, combat.getDefenders());
            if (gr.getMax() != null && want.size() > gr.getMax())
                return "ILLEGAL (number): no more than " + gr.getMax() + " creature" + (gr.getMax() == 1 ? "" : "s") + " can attack this combat.";
            for (Map.Entry<GameEntity, Integer> e : gr.getDefenderMax().entrySet()) {
                int n = 0;
                for (GameEntity d : want.values()) if (d == e.getKey()) n++;
                if (n > e.getValue())
                    return "ILLEGAL (number): no more than " + e.getValue() + " creature" + (e.getValue() == 1 ? "" : "s")
                            + " can attack " + entityRef(e.getKey(), me) + " this combat.";
            }
            forge.game.combat.AttackConstraints ac = combat.getAttackConstraints();
            for (Card c : want.keySet()) {
                forge.game.combat.AttackRestriction res = ac.getRestrictions().get(c);
                java.util.Set<forge.game.combat.AttackRestrictionType> v = res == null ? null : res.getViolation(want);
                if (v != null && !v.isEmpty()) return "ILLEGAL (restriction): " + refOf(c) + attackRestrictionWords(v.iterator().next()) + ".";
            }
            Map<Card, GameEntity> best = ac.getLegalAttackers().getLeft();
            for (Map.Entry<Card, forge.game.combat.AttackRequirement> e : ac.getRequirements().entrySet()) {
                Card c = e.getKey();
                forge.game.combat.AttackRequirement req = e.getValue();
                if (req == null || req.countViolations(want.get(c), want) <= req.countViolations(best.get(c), best)) continue;
                return want.containsKey(c)
                        ? "ILLEGAL (requirement): " + refOf(c) + " must attack a particular player, planeswalker or battle if able."
                        : "ILLEGAL (requirement): " + refOf(c) + " attacks this combat if able, and this attack leaves it out.";
            }
            return "ILLEGAL (requirement): this attack breaks an attack requirement (a creature or player that must be attacked if able).";
        }

        private static String attackRestrictionWords(forge.game.combat.AttackRestrictionType t) {
            switch (t) {
                case ONLY_ALONE: return " can attack only alone";
                case NOT_ALONE: return " can't attack alone";
                case NEED_TWO_OTHERS: return " can't attack unless at least two other creatures attack";
                case NEED_GREATER_POWER: return " can't attack unless a creature with greater power also attacks";
                case NEED_BLACK_OR_GREEN: return " can't attack unless a black or green creature also attacks";
                default: return " can't attack";
            }
        }

        /** Why {@code b} cannot block {@code a} (CombatUtil.canBlock is false), in the engine's order. */
        private String blockerRejection(Card a, Card b, Player me, Combat combat) {
            String r = refOf(b), ra = refOf(a);
            if (b.getZone() == null || b.getZone().getZoneType() != ZoneType.Battlefield || b.isPhasedOut())
                return "ILLEGAL (zone): " + r + " is not on the battlefield; only a creature on the battlefield blocks.";
            if (b.getController() != me) return "ILLEGAL (control): you do not control " + r + ".";
            if (!b.isCreature() || b.isBattle()) return "ILLEGAL (type): " + r + " is not a creature; only creatures block.";
            if (combat.getDefendingPlayerRelatedTo(a) != me)
                return "ILLEGAL (combat): " + ra + " is not attacking you or a planeswalker or battle you protect.";
            if (b.isTapped() && !forge.game.staticability.StaticAbilityCantAttackBlock.canBlockTapped(b))
                return "ILLEGAL (tapped): " + r + " is tapped, and a tapped creature cannot block.";
            if (b.hasKeyword("CARDNAME can't block.") || b.hasKeyword("CARDNAME can't attack or block."))
                return "ILLEGAL (restriction): " + r + " can't block.";
            if (b.isDetained()) return "ILLEGAL (restriction): " + r + " is detained, so it can't attack or block.";
            CardCollection sources = new CardCollection(b);
            sources.addAll(b.getGame().getCardsIn(ZoneType.STATIC_ABILITIES_SOURCE_ZONES));
            for (Card ca : sources)
                for (forge.game.staticability.StaticAbility st : ca.getStaticAbilities())
                    if (st.checkConditions(forge.game.staticability.StaticAbilityMode.CantBlock)
                            && forge.game.staticability.StaticAbilityCantAttackBlock.applyCantBlockAbility(st, b))
                        return "ILLEGAL (restriction): " + r + " can't block" + staticNote(st, b) + ".";
            if ((b.hasKeyword("CARDNAME can't attack or block alone.") || b.hasKeyword("CARDNAME can't block alone."))
                    && me.getCreaturesInPlay().size() < 2)
                return "ILLEGAL (restriction): " + r + " can't block alone.";
            CardCollection byOrder = new CardCollection(a);
            byOrder.add(b);
            byOrder.addAll(a.getGame().getCardsIn(ZoneType.STATIC_ABILITIES_SOURCE_ZONES));
            for (boolean anyBlocker : new boolean[] {true, false})
                for (Card ca : byOrder)
                    for (forge.game.staticability.StaticAbility st : ca.getStaticAbilities()) {
                        if (!st.checkConditions(forge.game.staticability.StaticAbilityMode.CantBlockBy)
                                || !forge.game.staticability.StaticAbilityCantAttackBlock.applyCantBlockByAbility(st, a, anyBlocker ? null : b)) continue;
                        if (anyBlocker) return "ILLEGAL (evasion): " + ra + " can't be blocked" + staticNote(st, a) + ".";
                        if (st.isKeyword(Keyword.FLYING))
                            return "ILLEGAL (evasion): " + ra + " has flying; only a creature with flying or reach can block it, and " + r + " has neither.";
                        if (st.getKeyword() != null)
                            return "ILLEGAL (evasion): " + ra + " has " + st.getKeyword().getTitle() + ", so " + r + " cannot block it.";
                        return "ILLEGAL (restriction): " + r + " cannot block " + ra + staticNote(st, b) + ".";
                    }
            if (CombatUtil.mustBlockAnAttacker(b, combat, null))
                return "ILLEGAL (requirement): " + r + " is required to block another attacker (a lure or a must-block effect), so it "
                        + "cannot block " + ra + ".";
            return null;
        }

        /** {@link #blocksError}'s objection to a set of blocks that are each legal, in the record's categories: a block cap
         *  or an attacker blocked by the wrong number of creatures (menace) is "number", a block requirement left unmet
         *  "requirement", a creature that can't block alone or without others "restriction". */
        private String blockSetRejection(String err, Player me, Combat combat, List<Card[]> pairs) {
            if (err.startsWith("no more than")) return "ILLEGAL (number): " + err;
            if (err.contains("cannot be blocked with")) {
                for (Card a : combat.getAttackers()) {
                    int n = 0;
                    for (Card[] pr : pairs) if (pr[1] == a) n++;
                    if (n == 0 || CombatUtil.canAttackerBeBlockedWithAmount(a, n, me)) continue;
                    org.apache.commons.lang3.tuple.Pair<Integer, Integer> mm =
                            forge.game.staticability.StaticAbilityCantAttackBlock.getMinMaxBlocker(a, me);
                    return n < mm.getLeft()
                            ? "ILLEGAL (number): " + refOf(a) + " can't be blocked except by " + mm.getLeft() + " or more creatures"
                              + (a.hasKeyword(Keyword.MENACE) ? " (menace)" : "") + "; you assigned " + n + "."
                            : "ILLEGAL (number): " + refOf(a) + " can't be blocked by more than " + mm.getRight() + " creature"
                              + (mm.getRight() == 1 ? "" : "s") + "; you assigned " + n + ".";
                }
            }
            String text = err;
            List<Card> named = new ArrayList<>(me.getCreaturesInPlay());
            named.addAll(combat.getAttackers());
            for (Card c : named) text = text.replace(c.toString(), refOf(c));
            return (text.contains(" must ") ? "ILLEGAL (requirement): " : "ILLEGAL (restriction): ") + text;
        }

        /** The decision row's proposal fields: every `answer` call of the ask in order, its proposal under {@code field}
         *  (`action`, `declare_attackers` or `declare_blockers`), its verdict and category, and `first_try_legal`: whether
         *  the first well-formed proposal was legal (null when none was). A FORMAT error is not a rules error (an unknown
         *  handle or field), so `first_try_legal` measures the rules only. */
        static String proposalRecord(List<String[]> calls, String field) {
            StringBuilder b = new StringBuilder(",\"proposals\":[");
            Boolean firstLegal = null;
            for (int i = 0; i < calls.size(); i++) {
                String[] c = calls.get(i);
                com.google.gson.JsonObject a = NativeTools.arguments(c[0]);
                com.google.gson.JsonElement act = a == null ? null : a.get(field);
                String err = c[1];
                String verdict = err == null ? "accepted" : err.startsWith("ILLEGAL (") ? "illegal" : "format";
                String category = err != null && err.startsWith("ILLEGAL (") ? err.substring(9, Math.max(9, err.indexOf(')'))) : null;
                if (firstLegal == null && !"format".equals(verdict)) firstLegal = err == null;
                b.append(i == 0 ? "" : ",").append("{\"").append(field).append("\":").append(act == null ? "null" : act.toString())
                 .append(",\"verdict\":\"").append(verdict).append('"');
                if (category != null) b.append(",\"category\":\"").append(jsonEscape(category)).append('"');
                if (err != null) b.append(",\"error\":\"").append(jsonEscape(err)).append('"');
                // PROPOSE_TERSE: `error` is the engine's diagnosis; the seat was told only this.
                String shown = AnswerTool.shownRefusal(err, "action".equals(field) ? AnswerTool.Kind.ACTION
                        : AnswerTool.DECLARE_ATTACKERS.equals(field) ? AnswerTool.Kind.ATTACK_PROPOSAL : AnswerTool.Kind.BLOCK_PROPOSAL);
                if (shown != null && !shown.equals(err)) b.append(",\"error_shown\":\"").append(jsonEscape(shown)).append('"');
                b.append('}');
            }
            return b.append("],\"first_try_legal\":").append(firstLegal).toString();
        }

        /** Whether the play menu offers {@code sa} now: playable (timing, restrictions), payable, and with
         *  a legal target when it targets. {@code alt}: an alternative cost, which is rarely mana alone
         *  (exile a card, pay life, return an Island), so it is checked as a whole cost; the normal cast
         *  keeps its historical mana check. */
        static boolean offerable(SpellAbility sa, Player me, boolean alt) {
            if (sa.isLandAbility() || sa.isManaAbility()) return false;
            if (!playable(sa, me)) return false;
            // The cost check prices the menu and the hand tags (SeatObservation.handList's castTag):
            // its adjust consults the controller on cost reductions (CostAdjustment's reduction loop),
            // and an ask inside a snapshot build re-enters renderSnapshot forever. See PRICING_FOR_SNAPSHOT.
            boolean wasPricing = PRICING_FOR_SNAPSHOT;
            PRICING_FOR_SNAPSHOT = true;
            boolean payable;
            try {
                payable = alt ? legallyPayable(sa, me, false) : seatCanPay(sa, me, 0, false);
                // The rest of the cost by the engine's own test, at the least X it allows: Arcee, Sharpshooter's
                // "{1}, remove X +1/+1 counters (X at least 1)" was offered with no counter on it and failed to pay.
                if (payable && !alt && sa.getPayCosts() != null) {
                    Integer x = sa.getXManaCostPaid();
                    forge.game.cost.CostPartMana cm = sa.getPayCosts().getCostMana();
                    if (sa.getPayCosts().hasXInAnyCostPart()) sa.setXManaCostPaid(cm == null ? 0 : Math.max(0, cm.getXMin()));
                    try { payable = forge.game.cost.CostPayment.canPayAdditionalCosts(sa.getPayCosts(), sa, false); }
                    finally { sa.setXManaCostPaid(x); }
                }
            } finally {
                PRICING_FOR_SNAPSHOT = wasPricing;
            }
            // A spree spell is cast with at least one mode, and each adds its cost: offered only when one is payable.
            if (payable && sa.getApi() == ApiType.Charm) {
                try {
                    List<AbilitySub> modes = forge.game.ability.effects.CharmEffect.makePossibleOptions(sa);
                    if (modes.stream().anyMatch(ab -> ab.hasParam("ModeCost")))
                        payable = modes.stream().anyMatch(ab -> modesPayable(sa, List.of(ab), me));
                } catch (Exception ignore) { }
            }
            // Harmonize's reduction (tap a creature, the spell costs that much less) is chosen during the cast: priced
            // at full, Channeled Dragonfire's harmonize was never offered with four Mountains and a 3-power creature.
            if (!payable && sa.isHarmonize()) payable = harmonizePowers(sa, me).stream().anyMatch(n -> n > 0);
            if (!payable) return false;
            // Skip a play a part of which has no legal target: casting it fails ("Couldn't add to stack, failed to
            // target"): Animate Dead with an empty graveyard, Govern the Guildless's forecast with no creature in play
            // (its target is in a sub-ability: gauntlet kw28). Only the first part that targets is counted: counted
            // with no first target, Searing Blaze's "creature that player controls" has none and a legal cast would be
            // hidden (pr-review of #343).
            for (SpellAbility part = sa; part != null; part = part.getSubAbility()) {
                if (!part.usesTargeting()) continue;
                try {
                    if (part.getActivatingPlayer() == null) part.setActivatingPlayer(me);
                    TargetRestrictions tr = part.getTargetRestrictions();
                    // Only the targets it must have: "up to one target" casts with none (Finale of Promise, RP_006).
                    if (tr != null && targetCandidates(part, tr).size() < part.getMinTargets()) return false;
                } catch (Exception ignore) { /* can't evaluate -> offer it, prior behavior */ }
                break; // only the first part that targets: a later one's targets may depend on it (Searing Blaze)
            }
            return true;
        }

        /** The engine's harmonize step: the power chosen, the creature-tap cost it adds and the reduction it gives
         *  (GameActionUtil.addKeywordCosts, CardFactoryUtil's Harmonize ReduceCost). */
        static final String HARMONIZE_TITLE = "Choose power of creature to tap";
        static SpellAbility harmonizedWith(SpellAbility sa, int n) {
            SpellAbility with = sa.copy();
            with.getPayCosts().add(new forge.game.cost.Cost("tapXType<1/Creature.powerEQ" + n + "/creature for Harmonize>", false));
            with.setOptionalKeywordAmount(sa.getKeyword(), n);
            return with;
        }

        /** The powers a harmonize cast can be paid with now, 0 (no creature tapped) included when the full cost is. */
        static List<Integer> harmonizePowers(SpellAbility sa, Player me) {
            List<Integer> out = new ArrayList<>();
            boolean was = PRICING_FOR_SNAPSHOT;
            PRICING_FOR_SNAPSHOT = true;
            try {
                if (sa.getActivatingPlayer() == null) sa.setActivatingPlayer(me);
                if (legallyPayable(sa, me, false)) out.add(0);
                java.util.TreeSet<Integer> powers = new java.util.TreeSet<>();
                for (Card c : me.getCreaturesInPlay()) if (c.getNetPower() > 0 && c.canTap()) powers.add(c.getNetPower());
                // The reduction counts the power from the card's cast spell (Count$OptionalKeywordAmount reads
                // getCastSA()), which the engine sets only once the card is on its way to the stack.
                Card host = sa.getHostCard();
                SpellAbility cast = host.getCastSA();
                for (int n : powers) {
                    try {
                        SpellAbility with = harmonizedWith(sa, n);
                        with.setActivatingPlayer(me);
                        host.setCastSA(with);
                        if (legallyPayable(with, me, false)) out.add(n);
                    } catch (Exception ignore) { /* not this power */ }
                    finally { host.setCastSA(cast); }
                }
            } catch (Exception ignore) { /* nothing priced */ }
            finally { PRICING_FOR_SNAPSHOT = was; }
            return out;
        }

        /** The faithful hand tag: "(castable)" only when the play menu would offer the card now, so the
         *  observation never calls a card castable that the menu cannot cast (a creature on the
         *  opponent's turn was "(castable)" because its mana was there). A card the menu offers only at
         *  an alternative cost says so; payable mana with no way to cast it now says that. Otherwise
         *  the {@link #affordability} tag. */
        static String castTag(Player pl, Card c, int[] tally) {
            String base = affordability(pl, c, tally);
            boolean normal = false, alt = false;
            String optionalName = null;
            try {
                for (SpellAbility sa : ComputerUtilAbility.getSpellAbilities(new CardCollection(c), pl)) {
                    if (!sa.isSpell()) continue;
                    if (sa.getActivatingPlayer() == null) sa.setActivatingPlayer(pl);
                    // An alternative cost the engine lists as its own spell (Endurance's evoke): the menu offers
                    // it with the same check (enumeratePlayOptions), so the tag must count it too.
                    if (sa.getAlternativeCost() != null) { if (offerable(sa, pl, false)) alt = true; continue; }
                    if (offerable(sa, pl, false)) { normal = true; break; }
                    for (SpellAbility a : GameActionUtil.getAlternativeCosts(sa, pl, false))
                        if (offerable(a, pl, true)) alt = true;
                    // The menu also offers the cast with an optional cost that makes it playable (an offering):
                    // the tag says which, or the hand reads "need 3 more mana" beside a castable option.
                    for (OptionalCostValue v : GameActionUtil.getOptionalCostValues(sa)) {
                        SpellAbility with = withOptional(sa, Lists.newArrayList(v));
                        with.setActivatingPlayer(pl);
                        if (optionalName == null && offerable(with, pl, true)) optionalName = v.getType().getName();
                    }
                }
            } catch (Exception e) { return base; }
            if (normal) return "(castable)";
            if (alt) return "(castable by an alternative cost)";
            if (optionalName != null) return "(castable only with " + optionalName + ")";
            return "(castable)".equals(base) ? "(payable, not castable now)" : base;
        }

        /** The play menu's option labels in menu order: "CAST &lt;saLabel&gt;" per spell, "PLAY LAND
         *  &lt;name&gt;" per land, then the trailing "PASS (do nothing)". Shared by the LLM seat's log row
         *  and the ai-teacher seat so the {@code options} array is byte-identical. */
        static List<String> buildPlayLabels(List<SpellAbility> options, List<Card> landCards) {
            List<String> playLabels = new ArrayList<>();
            for (SpellAbility sa : options) playLabels.add("CAST " + saLabel(sa));
            for (Card lc : landCards) playLabels.add("PLAY LAND " + lc.getName());
            playLabels.add("PASS (do nothing)");
            return disambiguate(playLabels);
        }

        /** Returns the list UNCHANGED unless a label repeats; only then are the repeated entries
         *  suffixed " #1", " #2"... in occurrence order, so two copies of one card stop being the
         *  same menu string (2 of puz-v1's 25 positions show the model two identical CAST lines —
         *  the model literally cannot say which one it means, and the suite survived only by a
         *  select_suite guard plus luck). ORDINALS, not Forge ids, deliberately: decideOne/decideMany
         *  see only pre-built strings and can never do better than an ordinal, while buildPlayLabels
         *  has the real Card — a hybrid would make the two call sites inconsistent, and the teacher/
         *  student prompt pairing (process_metrics state_key) needs them byte-identical. This is a
         *  DATA change, not a contract change: the menu is baked into row prompts by the game, so
         *  contract_sha and all existing artifacts are untouched. */
        static List<String> disambiguate(List<String> labels) {
            java.util.Map<String, Integer> counts = new java.util.HashMap<>();
            for (String s : labels) counts.merge(s, 1, Integer::sum);
            boolean anyDup = false;
            for (int v : counts.values()) if (v > 1) { anyDup = true; break; }
            if (!anyDup) return labels;
            java.util.Map<String, Integer> seen = new java.util.HashMap<>();
            List<String> out = new ArrayList<>(labels.size());
            for (String s : labels) {
                if (counts.get(s) > 1) out.add(s + " #" + seen.merge(s, 1, Integer::sum));
                else out.add(s);
            }
            return out;
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
            return buildDecideOnePrompt(snapshot, question, options, noneLabel, false);
        }

        /** {@code full}: options are not cut at 90 characters (the faithful profile; legacy keeps its bytes). */
        static String buildDecideOnePrompt(String snapshot, String question, List<String> options, String noneLabel, boolean full) {
            options = disambiguate(options); // idempotent; keeps prompt == logged options at every caller
            StringBuilder menu = new StringBuilder();
            int n = 1;
            for (String o : options) menu.append("  ").append(n++).append(") ").append(full ? flat(o) : oneLine(o)).append('\n');
            if (noneLabel != null) menu.append("  ").append(n++).append(") ").append(noneLabel).append('\n');
            return snapshot + "\nPRIORITY: you\nDECISION: " + question + "\nOptions:\n" + menu
                    + "Reason briefly, then end with EXACTLY: CHOICE: <number>";
        }

        /** The exact attempt-0 prompt {@link #decideMany} sends (PICKS-style, min..max). */
        static String buildDecideManyPrompt(String snapshot, String question, List<String> options, int min, int max) {
            return buildDecideManyPrompt(snapshot, question, options, min, max, false, null);
        }

        /** {@code full}: options are not cut (faithful). {@code tail}: replaces the PICKS tail for an ask
         *  with its own answer format (the divided allocation asked ALLOC: and then ended "PICKS:"). */
        static String buildDecideManyPrompt(String snapshot, String question, List<String> options, int min, int max,
                                            boolean full, String tail) {
            options = disambiguate(options); // idempotent; keeps prompt == logged options at every caller
            StringBuilder menu = new StringBuilder();
            int n = 1;
            for (String o : options) menu.append("  ").append(n++).append(") ").append(full ? flat(o) : oneLine(o)).append('\n');
            // An ask with its own answer format (the ALLOC split) is not a subset ask: under the faithful
            // profile it does not say "choose between 1 and <total> of these" over fewer options.
            String header = full && tail != null ? "Options:" : "Choose between " + min + " and " + max + " of these (by number):";
            return snapshot + "\nPRIORITY: you\nDECISION: " + question + "\n" + header + "\n" + menu
                    + (tail != null ? tail : "Reason briefly, then end with EXACTLY: PICKS: <comma-separated numbers>"
                    + (min == 0 ? " (or PICKS: NONE to choose zero)" : ""));
        }

        /** The LLM seat's bespoke mulligan prompt body (not a decideOne/Many template — a KEEP/MULLIGAN
         *  free-text ask). {@code handNames} is the comma-joined card-name list both seats build the same
         *  way. Shared so the ai-teacher seat's reconstruction can never drift from the LLM seat's wire
         *  format. See {@link #mulliganKeepHand} (LLM seat) and PlayerControllerAiLogging.mulliganKeepHand. */
        static String buildMulliganPrompt(int size, int lands, String handNames, boolean onThePlay) {
            return buildMulliganPrompt(size, lands, handNames, onThePlay, false);
        }

        /** {@code neutral}: without the combo-deck advice, which was shown to Burn and Death & Taxes too
         *  (the faithful profile). */
        static String buildMulliganPrompt(int size, int lands, String handNames, boolean onThePlay, boolean neutral) {
            // The faithful (neutral) ask: the facts in plain sentences (PROMPT-AUDIT.md C3, D1).
            if (neutral)
                return "MULLIGAN: you are " + (onThePlay ? "on the play (you go first)" : "on the draw (the opponent goes first, and you "
                        + "draw on your first turn)") + ". This hand has " + size + " cards, " + lands + " of them lands: " + handNames
                        + ". A mulligan gives you a new hand of " + Math.max(0, size - 1) + " cards (each mulligan costs one card, and "
                        + "you cannot go below 5); keeping plays this hand. Keep or mulligan? End with exactly one word: KEEP or MULLIGAN.";
            return "MULLIGAN DECISION. You are " + (onThePlay ? "ON THE PLAY (you go first)" : "ON THE DRAW (opponent goes first; you draw an extra card turn 1)")
                    + ". This is a " + size + "-card hand with " + lands + " land(s): " + handNames
                    + ". If you MULLIGAN you will look at a NEW hand of only " + Math.max(0, size - 1)
                    + " cards (each mulligan costs you a card), and you can never go below 5. If you KEEP you play this "
                    + size + "-card hand as-is. " + (neutral ? "" : "Keeping a workable hand almost always beats shrinking it. ")
                    + (neutral ? "" : "For a combo deck, a hand with your enablers + a payoff is a KEEP even on few lands; only ship a hand "
                    + "that genuinely cannot function. ") + "Keep or mulligan? End with EXACTLY one word: KEEP or MULLIGAN.";
        }

        /** The attackers decideMany QUESTION (the {@code question} arg to {@link #buildDecideManyPrompt},
         *  which the LLM seat wraps and the ai-teacher seat wraps identically). Bundles the poison-aware
         *  lethal hint so its template is single-sourced. {@code oppLife}/{@code oppPoison} are the
         *  defender's life / poison counters; {@code anyInfect} = any legal attacker deals poison damage. */
        static String buildAttackersQuestion(String defenderName, int oppLife, int oppPoison, boolean anyInfect) {
            return buildAttackersQuestion(defenderName, oppLife, oppPoison, anyInfect, "");
        }

        /** G8 lookahead (LOOKAHEAD=1): public-info combat ceiling for the attackers decision, computed
         *  from the engine's own combat maths — the total unblocked damage if every legal attacker
         *  attacks, and which attackers cannot be blocked at all. Empty (byte-identical default) when
         *  lookahead is off. Both seats compute it from the same state, so their prompts stay identical. */
        static String attackersCeiling(CardCollection legal, Player defender, Combat combat) {
            if (!lookaheadEnabled() || legal == null || legal.isEmpty() || defender == null) return "";
            try {
                int dmg = ComputerUtilCombat.sumDamageIfUnblocked(legal, defender);
                // Which of the defender's creatures that can block now could block each attacker (#166: the old
                // line asked CombatUtil.canBeBlocked, i.e. "can this be blocked at all", and told the seat a
                // flying Emrakul "can be blocked" when the only untapped creature could not block a flyer).
                List<Card> blockers = new ArrayList<>();
                for (Card b : defender.getCreaturesInPlay()) {
                    try { if (CombatUtil.canBlock(b, combat)) blockers.add(b); } catch (Exception ignore) { }
                }
                StringBuilder unblockable = new StringBuilder(), blockable = new StringBuilder();
                for (Card c : legal) {
                    try {
                        StringBuilder by = new StringBuilder();
                        if (CombatUtil.canBeBlocked(c, combat, defender))
                            for (Card b : blockers)
                                if (CombatUtil.canBlock(c, b, combat)) by.append(by.length() == 0 ? "" : ", ").append(b.getName());
                        if (by.length() == 0) {
                            // The number says what it is: a bare "(2)" read as an engine id (runs/loop/g02 T1, LEAK).
                            unblockable.append(unblockable.length() == 0 ? "" : ", ").append(c.getName()).append(" (")
                                    .append(ComputerUtilCombat.damageIfUnblocked(c, defender, combat, false)).append(" damage)");
                        } else {
                            blockable.append(blockable.length() == 0 ? "" : "; ").append(c.getName()).append(" by ").append(by);
                        }
                    } catch (Exception ignore) { }
                }
                return " OUTLOOK (engine, all unblocked): " + dmg + " damage."
                        + (unblockable.length() > 0 ? " No creature of theirs that can block now could block: " + unblockable + "." : "")
                        + (blockable.length() > 0 ? " Could be blocked: " + blockable + "." : "");
            } catch (Exception e) { return ""; }
        }

        /** Adds the optional combat outlook to the shared attackers question. */
        static String buildAttackersQuestion(String defenderName, int oppLife, int oppPoison, boolean anyInfect, String outlook) {
            int poisonNeeded = Math.max(0, 10 - oppPoison);
            // Facts only (harness-epoch-8, PROMPT-AUDIT.md C3): what lethal is, not what to do about it.
            String lethalHint = "LETHAL: they are at " + oppLife + " life and " + oppPoison + "/10 poison, so " + oppLife
                    + " damage or " + poisonNeeded + " poison wins"
                    + (anyInfect ? " (infect attackers deal poison, not life loss)" : "") + ".";
            return "COMBAT: declare attackers against " + defenderName + " (life " + oppLife
                    + "). Choose which creatures attack; you may attack with none. " + lethalHint
                    + (outlook == null ? "" : outlook);
        }

        /** The LLM seat's bespoke blockers prompt (BLOCKS: wire format, not a decideOne/Many template).
         *  {@code atk}/{@code blk} are the comma-joined attacker / blocker "Name (P/T)" lists both seats
         *  build identically. Shared so the ai-teacher's reconstruction can never drift. See
         *  {@link #declareBlockers} (LLM seat) and PlayerControllerAiLogging.declareBlockers. */
        static String buildBlockersPrompt(String snapshot, String atk, String blk) {
            return buildBlockersPrompt(snapshot, atk, blk, "");
        }

        /** G8 lookahead (LOOKAHEAD=1): the damage the defender faces if nothing blocks, from the
         *  engine's own combat maths. Empty (byte-identical default) when lookahead is off. Both seats
         *  compute it from the same state, so their prompts stay identical. */
        static String blockersCeiling(Combat combat, Player defender) {
            if (!lookaheadEnabled() || combat == null || defender == null) return "";
            try {
                CardCollection atks = combat.getAttackers();
                if (atks == null || atks.isEmpty()) return "";
                return " INCOMING (engine, if nothing blocks): " + ComputerUtilCombat.sumDamageIfUnblocked(atks, defender) + " damage.";
            } catch (Exception e) { return ""; }
        }

        /** Adds the optional incoming-damage outlook to the shared blockers prompt. */
        static String buildBlockersPrompt(String snapshot, String atk, String blk, String outlook) {
            return snapshot + "\nCOMBAT — declare blockers. Incoming: " + atk + ". Your blockers: " + blk + ". "
                    + "Assign blocks (each blocker blocks one attacker). Weigh your life total vs losing blockers. "
                    + (outlook == null ? "" : outlook)
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
            try { host = sa.getHostCard() != null ? safeName(sa.getHostCard()) : "?"; } catch (Exception ignore) { }
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
        /** The plan the seat's system prompt carries in {@code <your_deck>}. */
        String plan() { return plan; }
        @Override
        public Player createIngamePlayer(Game game, int id) {
            // A player of a preview COPY is the engine AI's, never the model's (PlayerLookahead.inPreview).
            if (PlayerLookahead.inPreview()) return super.createIngamePlayer(game, id);
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

        /** See {@link LlmSpikeGui#puzzleSolverSeat}. Needed here too: the Forge-AI FLOOR arm of the
         *  PZL axis runs as "ai-log", not plain "ai" — a stock LobbyPlayerAi builds an unmodifiable
         *  PlayerControllerAi that can never report itself as the solver. */
        @Override
        public boolean isGuiPlayer() { return isPuzzleSolver(getLobbyPlayer()); }

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
                // Same log-choke dedupe as evalDec: shadow rows' options must match their prompt
                // (built via buildDecideOne/ManyPrompt, which disambiguates). Idempotent.
                if (options != null) options = PlayerControllerLLM.disambiguate(options);
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
                prompt = PlayerControllerLLM.buildMulliganPrompt(size, lands, n.toString(),
                        getPlayer() == firstPlayer);
            } catch (Exception ignore) { }
            // FORK (rollout, additive — inert unless LlmSpikeFork installed a forcer): forced mulligan
            // on the ai-log seat. 0 or -1 (decline) == KEEP, 1 == MULLIGAN. Skips super entirely — the
            // forced decision REPLACES the AI's, and the row logs what actually happened.
            Integer forced = LlmSpikeGui.forkForce(getPlayer(), seat, "mulligan", 2,
                    java.util.Arrays.asList("KEEP", "MULLIGAN"));
            if (forced != null) {
                boolean forcedKeep = forced.intValue() != 1;
                emit("mulligan", 2, forcedKeep ? 0 : 1, java.util.Arrays.asList("KEEP", "MULLIGAN"), prompt);
                return forcedKeep;
            }
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
            // FORK (rollout, additive — inert unless LlmSpikeFork installed a forcer): forced play on
            // the ai-log seat. Skips super entirely (the forced decision replaces the AI's). A forced
            // SPELL gets deterministic first-legal targets via forceTargetsFirstLegal — the AI cast
            // path does no targeting for a spell it did not evaluate, and an untargeted cast fizzles.
            // forced == -1 or the PASS slot -> pass priority with null (never an empty list — empty
            // spins the 999-iteration priority loop, see chooseSpellAbilityToPlay's own PASS note).
            if (genuine && labels != null && po != null) {
                Integer forced = LlmSpikeGui.forkForce(getPlayer(), seat, "play", labels.size(), labels);
                if (forced != null) {
                    int f = forced.intValue();
                    int nSpells = po.spells.size();
                    int passSlot = labels.size() - 1;
                    if (f < 0 || f >= passSlot) {
                        emit("play", labels.size(), -1, labels, prompt);
                        return null;
                    }
                    SpellAbility chosen = f < nSpells ? po.spells.get(f) : po.landPlays.get(f - nSpells);
                    if (f < nSpells) LlmSpikeGui.forceTargetsFirstLegal(getPlayer(), chosen);
                    emit("play", labels.size(), f, labels, prompt);
                    return Lists.newArrayList(chosen);
                }
            }
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
            try { if (optionList != null) for (T t : optionList) if (!PlayerControllerLLM.isListDivider(t)) opts.add(t); } catch (Exception ignore) { }
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
            List<forge.game.GameObject> cands = null; List<String> labels = null; String prompt = null;
            try {
                if (sa != null && sa.usesTargeting() && sa.getMaxTargets() == 1) {
                    boolean subTargets = false;
                    for (SpellAbility sub = sa.getSubAbility(); sub != null; sub = sub.getSubAbility())
                        if (sub.usesTargeting()) { subTargets = true; break; }
                    if (!subTargets) {
                        TargetRestrictions tr = sa.getTargetRestrictions();
                        if (tr != null) cands = PlayerControllerLLM.targetCandidates(sa, tr);
                    }
                }
                if (cands != null && cands.size() > 1) {
                    labels = new ArrayList<>();
                    for (forge.game.GameObject ge : cands) labels.add(PlayerControllerLLM.targetEntityLabel(ge));
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
                        String q = PlayerControllerLLM.buildAttackersQuestion(defender.getName(), oppLife, oppPoison, anyInfect,
                                PlayerControllerLLM.attackersCeiling(legal, defender, combat));
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
                    List<Card> blockers = PlayerControllerLLM.ableBlockers(defender, combat);
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
                        prompt = PlayerControllerLLM.buildBlockersPrompt(snap(), atk.toString(), blk.toString(),
                                PlayerControllerLLM.blockersCeiling(combat, defender));
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

    /** CONTROLLER_A/B value for a seat a person plays in the GUI, on Forge's own human controller (play-vs-llm.sh).
     *  Only {@link #buildPlayers(boolean)} with a window builds one; {@link #seatFor}, which the puzzle runner calls,
     *  refuses it. */
    static final String HUMAN = "human";
    static final String NO_WINDOW = "a human seat needs the GUI to ask its player: run play-vs-llm.sh (or "
            + "run-llm-spike.sh), not a headless, fork or puzzle run";

    /** Map a CONTROLLER_A/B value to its LobbyPlayer ("human" refused: see {@link #HUMAN}): "ai" = plain Forge AI
     *  (untouched), "ai-log" = the Forge AI that logs every decision as an ai-teacher SFT row, anything else = the LLM
     *  seat / one of its baseline arms (llm|random|floor), which carries the plan + model specs + arm mode.
     *  No AI profile: the compiled AiProps defaults, as every seat was built before the switch. */
    static LobbyPlayer seatFor(String ctl, String name, String plan, String smartModel, String fastModel) {
        return seatFor(ctl, name, plan, smartModel, fastModel, "");
    }

    /** As the five-argument {@code seatFor}, with the Forge AI profile of an "ai" / "ai-log" seat (AI_PROFILE_A /
     *  AI_PROFILE_B). Empty (the default) leaves the seat exactly as it was built before the switch existed:
     *  {@code new LobbyPlayerAi(name, null)}, whose empty profile name makes {@code AiProfileUtil.getAIProp} return the
     *  compiled default of every AiProps key, which is none of Forge's four shipped profiles (issue #381; every
     *  recorded STR-AI score met that opponent). A name is applied the way {@code GamePlayerUtil.createAiPlayer}
     *  applies it ({@code setAiProfile}), after it is checked against the profiles Forge loaded
     *  ({@link #aiProfileRefusal}); a profile on a seat that is no Forge AI is refused. */
    static LobbyPlayer seatFor(String ctl, String name, String plan, String smartModel, String fastModel,
                               String aiProfile) {
        if (HUMAN.equals(ctl)) throw new IllegalStateException(NO_WINDOW);
        boolean forgeAi = "ai".equals(ctl) || "ai-log".equals(ctl);
        String profile = aiProfile(aiProfile);
        if (!profile.isEmpty()) {
            if (!forgeAi) throw new IllegalStateException("AI_PROFILE names '" + profile + "' for the seat " + name
                    + ", which is controller '" + ctl + "', not a Forge AI (ai, ai-log): drop the profile");
            String refusal = aiProfileRefusal(profile, shippedAiProfiles());
            if (refusal != null) throw new IllegalStateException(refusal);
        }
        if (forgeAi) {
            LobbyPlayerAi seat = "ai".equals(ctl) ? new LobbyPlayerAi(name, null) : new LobbyPlayerAiLogging(name);
            if (!profile.isEmpty()) seat.setAiProfile(profile);
            return seat;
        }
        return new LobbyPlayerLLM(name, plan, smartModel, fastModel, ctl);
    }

    /** The profiles Forge loaded ({@code FModel.initialize} runs {@code AiProfileUtil.loadAllProfiles}); empty when
     *  none were loaded, which {@link #aiProfileRefusal} then reports. */
    static List<String> shippedAiProfiles() {
        try {
            return AiProfileUtil.getAvailableProfiles();
        } catch (RuntimeException e) {
            return new ArrayList<>();
        }
    }

    /** Why {@code profile} cannot seat a Forge AI, or null when it can (or is empty: the compiled defaults). An unknown
     *  name would not fail inside Forge: {@code getAIProp} falls back silently to the compiled defaults, so a typo
     *  would play the default arm under another name. Forge's "Random" entries are refused too: they change the
     *  profile between games, which no condition can name. */
    static String aiProfileRefusal(String profile, List<String> shipped) {
        if (profile == null || profile.trim().isEmpty()) return null;
        if (shipped.contains(profile)) return null;
        return "AI profile '" + profile + "' is not one Forge loaded (" + (shipped.isEmpty() ? "none were loaded"
                : String.join(", ", new java.util.TreeSet<>(shipped))) + "); leave AI_PROFILE_A/B empty for the "
                + "compiled-default opponent";
    }

    /** An AI_PROFILE_A / AI_PROFILE_B value as every site reads it: trimmed, "" when unset. */
    static String aiProfile(String raw) {
        return raw == null ? "" : raw.trim();
    }

    /** The game line's two profile keys, from the same {@link #env} read the seats use (issue #381): a condition, ""
     *  for the compiled AiProps defaults. */
    static String aiProfileStamp() {
        return ",\"ai_profile_a\":\"" + jsonEscape(aiProfile(env("AI_PROFILE_A"))) + "\""
                + ",\"ai_profile_b\":\"" + jsonEscape(aiProfile(env("AI_PROFILE_B"))) + "\"";
    }

    /** Why a model seat ({@code ctl} llm) of identity {@code ident} cannot be built: a tier with no model named
     *  (SMART_MODEL_X / FAST_MODEL_X, else SMART_MODEL / FAST_MODEL). It used to default to the Claude CLI's opus and
     *  haiku, a lane the operator never chose. Null when every tier is named, or the seat asks no model. */
    static String missingModel(String ctl, String ident, Map<String, String> env) {
        if (!"llm".equals(ctl)) return null;
        List<String> missing = new ArrayList<>();
        for (String tier : new String[] {"SMART", "FAST"}) {
            String v = env.get(tier + "_MODEL_" + ident);
            if (v == null) v = env.get(tier + "_MODEL");
            if (v == null || v.trim().isEmpty()) missing.add(tier + "_MODEL_" + ident);
        }
        return missing.isEmpty() ? null : "the model seat " + ident + " names no model (" + String.join(", ", missing)
                + " unset, nor SMART_MODEL / FAST_MODEL): name one; there is no default model";
    }

    /** A game or puzzle where identity {@code ctl}'s model seat runs on the Claude CLI (`claude-cli:`): the development
     *  lane, stamped `dev_lane` and never part of a benchmark score (owner, 2026-10-24). */
    static boolean devLane(String ctl, String smart, String fast) {
        return "llm".equals(ctl) && (ClaudeCliBridge.isSpec(smart) || ClaudeCliBridge.isSpec(fast));
    }

    /** Test hook: the environment {@link #env} and {@link #buildPlayers()} read instead of the process's, for the seat
     *  gates' call sites. */
    static volatile Map<String, String> envOverride;

    /** An environment variable, from {@link #envOverride} when a test set one. */
    static String env(String key) {
        Map<String, String> o = envOverride;
        return o != null ? o.get(key) : System.getenv(key);
    }

    /** Shared seat construction for GUI + headless runners. Env:
     *  DECK_A/B, DECK_A_NAME/B_NAME, PLAN_A/B ("" = plan-free arm),
     *  SMART_MODEL_A/B + FAST_MODEL_A/B (fall back to SMART_MODEL/FAST_MODEL),
     *  CONTROLLER_A/B = llm|random|floor|ai|ai-log|human (GUI only), SWAP_SEATS=1 (paired-design seat flip). */
    static List<RegisteredPlayer> buildPlayers() {
        return buildPlayers(false);
    }

    /** {@code allowHuman}: only the GUI match has a window to ask a person, so only it may seat CONTROLLER_A/B=human. */
    static List<RegisteredPlayer> buildPlayers(boolean allowHuman) {
        Map<String, String> o = envOverride;
        return buildPlayers(o != null ? o : System.getenv(), allowHuman);
    }

    /** The GUI match's rules: canonical Constructed built explicitly (mana burn off, no ante) rather than
     *  HostedMatch.getDefaultRules(), which reads the local desktop prefs, so a GUI match is reproducible and plays
     *  as headless does (LlmSpikeHeadless: {@code new GameRules(GameType.Constructed)}). With a person seated, Forge's
     *  "AI can't play these cards well" list is not shown: no Forge AI plays the model's seat. */
    static GameRules guiRules(int gamesPerMatch, boolean human) {
        GameRules rules = new GameRules(GameType.Constructed);
        rules.setGamesPerMatch(gamesPerMatch);
        if (human) rules.setWarnAboutAICards(false);
        return rules;
    }

    /** The seat a person plays (CONTROLLER_A/B=human), or null in a match of harness seats only. */
    static RegisteredPlayer humanSeat(List<RegisteredPlayer> players) {
        for (RegisteredPlayer rp : players) if (rp.getPlayer() instanceof LobbyPlayerHuman) return rp;
        return null;
    }

    private static List<RegisteredPlayer> buildPlayers(Map<String, String> env, boolean allowHuman) {
        String home = System.getProperty("user.home");
        String pathA = env.getOrDefault("DECK_A", home + "/Downloads/Deck - Grixis Reanimator.txt");
        String pathB = env.getOrDefault("DECK_B", home + "/Downloads/Deck - Mardu Energy.txt");
        String nameA = env.getOrDefault("DECK_A_NAME", "Grixis Reanimator");
        String nameB = env.getOrDefault("DECK_B_NAME", "Mardu Energy");
        String smart = env.getOrDefault("SMART_MODEL", "opus");
        String fast  = env.getOrDefault("FAST_MODEL", "haiku");
        String smartA = env.getOrDefault("SMART_MODEL_A", smart);
        String smartB = env.getOrDefault("SMART_MODEL_B", smart);
        String fastA  = env.getOrDefault("FAST_MODEL_A", fast);
        String fastB  = env.getOrDefault("FAST_MODEL_B", fast);
        // Per-deck strategy resolution: an explicit PLAN_A/B env wins if SET (PLAN_A="" is the
        // deliberate plan-free cited arm); otherwise each deck's own <deck>.plan sidecar drives its
        // seat, so strategy generalizes per deck-identity instead of being hardcoded per seat.
        String planA = env.get("PLAN_A");
        // An explicit deck with no sidecar gets no plan text, never the default decks' plans: a Standard
        // deck was told it was Grixis Reanimator. Its decklist and card texts still follow below.
        if (planA == null) planA = loadPlanSidecar(pathA, env.get("DECK_A") == null ? DEFAULT_PLAN_A : "");
        String planB = env.get("PLAN_B");
        if (planB == null) planB = loadPlanSidecar(pathB, env.get("DECK_B") == null ? DEFAULT_PLAN_B : "");
        lastPlanA = planA; lastPlanB = planB;
        String ctlA = env.getOrDefault("CONTROLLER_A", "llm");
        String ctlB = env.getOrDefault("CONTROLLER_B", "llm");
        if (HUMAN.equals(ctlA) || HUMAN.equals(ctlB)) {
            if (!allowHuman) throw new IllegalStateException(NO_WINDOW);
            if (HUMAN.equals(ctlA) && HUMAN.equals(ctlB))
                throw new IllegalStateException("only one human seat per match: the GUI asks one person");
        }
        String profileA = aiProfile(env.get("AI_PROFILE_A")), profileB = aiProfile(env.get("AI_PROFILE_B"));
        for (String[] s : new String[][] {{ctlA, "A", profileA}, {ctlB, "B", profileB}}) {
            if (HUMAN.equals(s[0]) && !s[2].isEmpty()) throw new IllegalStateException("AI_PROFILE_" + s[1]
                    + " names '" + s[2] + "' for the human seat: a person plays no Forge AI, drop the profile");
            String missing = missingModel(s[0], s[1], env);
            if (missing != null) throw new IllegalStateException(missing);
        }
        // Before any deck is loaded: a retired bare name, or a claude-cli: seat the operator did not choose.
        boolean optedIn = "1".equals(env.get(ClaudeCliBridge.OPT_IN));
        for (String[] s : new String[][] {{ctlA, smartA, "A"}, {ctlA, fastA, "A"}, {ctlB, smartB, "B"}, {ctlB, fastB, "B"}})
            LLMBridgeFactory.requireCliOptIn(s[1], s[0], optedIn, "seat " + s[2]);

        Deck deckA, deckB;
        try { deckA = loadDeck(nameA, pathA); deckB = loadDeck(nameB, pathB); }
        catch (Exception e) {
            // EVAL MODE (EVAL_OUT set): a silent random-deck substitution would poison an entire batch with
            // games that aren't the matchup we claim to be measuring. Fail the run loudly instead.
            String evalOut = env.get("EVAL_OUT");
            if (evalOut != null && !evalOut.isEmpty())
                throw new RuntimeException("[DECK] eval-mode deck load failed (EVAL_OUT set); refusing random-deck fallback: " + e, e);
            System.out.println("[DECK] failed to load decklists (" + e + ") -> random color decks");
            deckA = DeckgenUtil.getRandomColorDeck(true); deckB = DeckgenUtil.getRandomColorDeck(true);
        }

        // Give each seat its own decklist so it knows what its cards do (a real player does). Run
        // condition tied to DECK_A/B (whose DECK_SHA is stamped); DECKLIST=0 restores the pre-decklist
        // prompt bytes for strict comparability with older runs.
        if (!"0".equals(env.getOrDefault("DECKLIST", "1"))) {
            String listA = decklist(deckA), listB = decklist(deckB);
            if (!listA.isEmpty()) planA = (planA == null || planA.isEmpty() ? "" : planA + "\n")
                    + "YOUR DECKLIST (you know your own deck's contents; only the draw order is hidden): " + listA;
            if (!listB.isEmpty()) planB = (planB == null || planB.isEmpty() ? "" : planB + "\n")
                    + "YOUR DECKLIST (you know your own deck's contents; only the draw order is hidden): " + listB;
            // Card rules text, from Forge's own card DB, so the model reads what its cards do rather
            // than recalling them. Same DECKLIST gate (this IS the decklist's expansion).
            String textA = cardText(deckA), textB = cardText(deckB);
            if (!textA.isEmpty()) planA += "\nYOUR CARDS (what each card in your deck does):\n" + textA;
            if (!textB.isEmpty()) planB += "\nYOUR CARDS (what each card in your deck does):\n" + textB;
            lastPlanA = planA; lastPlanB = planB;
        }

        RegisteredPlayer a = new RegisteredPlayer(deckA).setPlayer(HUMAN.equals(ctlA) ? new LobbyPlayerHuman(nameA)
                : seatFor(ctlA, nameA, planA, smartA, fastA, profileA));
        RegisteredPlayer b = new RegisteredPlayer(deckB).setPlayer(HUMAN.equals(ctlB) ? new LobbyPlayerHuman(nameB)
                : seatFor(ctlB, nameB, planB, smartB, fastB, profileB));
        boolean swap = "1".equals(env.get("SWAP_SEATS"));
        List<RegisteredPlayer> players = swap ? Lists.newArrayList(b, a) : Lists.newArrayList(a, b);
        System.out.println("[SEATS] A=" + nameA + "(" + ctlA + "," + smartA + ") B=" + nameB + "(" + ctlB + "," + smartB
                + ")" + (swap ? " [SWAPPED]" : ""));
        return players;
    }

    /**
     * Infra defaults (max output tokens, timeout, OpenRouter "reasoning" hint, temperature) keyed by a
     * case-insensitive SUBSTRING match against the model id (e.g. "deepseek" matches
     * "deepseek/deepseek-v4-pro"). ONE uniform row serves every key: the budget must not depend on
     * which model is playing, or an eval compares models under different infra and the row with the
     * smaller cap is the one whose answer is truncated and then retried with reasoning DISABLED --
     * measured, that is a different question, not a slower one. The per-model values this table used
     * to carry (4000/8000/16000 smart, 512/1024 fast) came from HARNESS-ANALYSIS.md D1/F2, where one
     * flat 2048-tok/90s/1-retry config starved verbose reasoners (deepseek, gpt, kimi) while being
     * needlessly generous for terse ones; a generous uniform cap answers the starvation without making
     * the harness part of the measurement.
     *
     * History worth keeping: every finish_reason=length -> bridge-down conversion in the 2026-07-21
     * gpt-5/deepseek sweep (8 and 10 events, matching sweep-verdicts.json exactly) was on the FAST
     * tier, and the [TRUNCATED->RETRY] path never fired once, because isNonAnswer(content) ran BEFORE
     * the truncation-retry check and an empty content with a length finish is indistinguishable from a
     * plain non-answer. That ordering was an architecture bug and is fixed in OpenRouterBridge.ask():
     * the finish_reason=="length" retry now runs first, and for a row whose fast tier disables
     * reasoning it retries with reasoning off rather than a bigger budget (see the
     * {@code fastReasoningEffort} field).
     *
     * Env OPENROUTER_MODEL_CFG (JSON) can add rows or override individual fields on existing ones
     * without a rebuild, e.g. {@code {"mistral":{"maxTokens":6000},"deepseek":{"timeoutMs":150000}}}.
     * A malformed value logs once and the built-in table below is used untouched — additive override,
     * never a hard replacement.
     */
    static final class ModelCfg {

        final int maxTokens;
        final long timeoutMs;
        final String reasoningEffort; // nullable — smart tier only
        final Double temperature;     // nullable — omitted from the request body when null
        final int fastMaxTokens;
        final long fastTimeoutMs;
        /** Reasoning effort for the FAST tier, nullable. A reasoning model applies its own default when
         *  this is absent, which for a mechanical answer ("which target?", "keep or mulligan?") is pure
         *  latency: the hidden reasoning can consume the whole max_tokens budget before any answer token,
         *  provoking a truncation-retry at double the budget. Measured 2026-10-13 on
         *  deepseek-v4.1-flash: a fast-tier decision burned 4000 tokens of reasoning, retried at 8000,
         *  and took 182s for one number.
         *
         *  "none" is the sentinel that DISABLES reasoning (reasoning:{enabled:false}), except for a
         *  MANDATORY_REASONING_FLOOR model, which refuses that and is sent its lowest effort; measured 1.3s /
         *  65 completion tokens vs ~7s / ~850 with any effort value, because ~95% of the completion was
         *  hidden reasoning for a ~50-token answer. Effort values ("low"/"minimal") do NOT reliably cut
         *  it. Reasoning is kept ON for the smart tier, where planning and timing actually need it. */
        final String fastReasoningEffort;

        ModelCfg(int maxTokens, long timeoutMs, String reasoningEffort, Double temperature,
                 int fastMaxTokens, long fastTimeoutMs) {
            this(maxTokens, timeoutMs, reasoningEffort, temperature, fastMaxTokens, fastTimeoutMs, null);
        }

        ModelCfg(int maxTokens, long timeoutMs, String reasoningEffort, Double temperature,
                 int fastMaxTokens, long fastTimeoutMs, String fastReasoningEffort) {
            this.maxTokens = maxTokens;
            this.timeoutMs = timeoutMs;
            this.reasoningEffort = reasoningEffort;
            this.temperature = temperature;
            this.fastMaxTokens = fastMaxTokens;
            this.fastTimeoutMs = fastTimeoutMs;
            this.fastReasoningEffort = fastReasoningEffort;
        }

        private static final Map<String, ModelCfg> TABLE = buildTable();

        /**
         * The effective table, rendered canonically: every row, every field, key-sorted.
         *
         * This is what {@code model_cfg_sha} hashes. It used to hash the OPENROUTER_MODEL_CFG env
         * string alone and return the constant "builtin" when that was unset -- which is to say the
         * built-in values, the ones almost every run actually uses, were never in provenance at all.
         * Changing a timeout or a token cap moved no sha and invalidated no artifact, so two runs
         * with materially different infra were indistinguishable on disk. That is the same failure
         * ContextProfileTest exists to prevent: something shaped like identity that identifies
         * nothing. It matters concretely here -- an OpenRouter timeout is outcome-determining (an
         * unbounded one emptied the whole PZL LLM arm), and until now no artifact recorded it.
         *
         * <p>The {@code : , |} delimiters are unescaped. Every built-in value is a number or a short
         * keyword, but {@code reasoningEffort} can be set to arbitrary text through
         * {@code OPENROUTER_MODEL_CFG}, so an operator CAN feed a delimiter in and make two distinct
         * tables render alike. Provenance only, self-inflicted, and not a trust boundary -- but
         * escape the fields here before this string is ever used for anything but a hash.
         */
        static String canonical() {
            StringBuilder sb = new StringBuilder("modelcfg.v1|");
            for (String key : new java.util.TreeSet<>(TABLE.keySet())) {
                ModelCfg c = TABLE.get(key);
                sb.append(key).append(':')
                  .append(c.maxTokens).append(',').append(c.timeoutMs).append(',')
                  .append(c.reasoningEffort).append(',').append(c.temperature).append(',')
                  .append(c.fastMaxTokens).append(',').append(c.fastTimeoutMs).append(',')
                  .append(c.fastReasoningEffort).append('|');
            }
            // The floor changes the reasoning sent wherever "none" is, so it is part of the condition.
            sb.append("floor");
            for (Map.Entry<String, String> e : new java.util.TreeMap<>(OpenRouterBridge.MANDATORY_REASONING_FLOOR).entrySet())
                sb.append(':').append(e.getKey()).append('=').append(e.getValue());
            return sb.append('|').toString();
        }

        private static Map<String, ModelCfg> buildTable() {
            Map<String, ModelCfg> t = new java.util.LinkedHashMap<>();
            // ONE uniform config for every row: the answer budget must not depend on which model is
            // playing, or an eval compares models under different infra, and the row with the smaller
            // cap is the one whose answer is truncated and then retried with reasoning DISABLED --
            // measured, that is a different question, not a slower one. It cannot be fixed by capping
            // reasoning separately: OpenRouter's `reasoning.max_tokens` is Anthropic-style and the
            // authorised model does not accept it (its model entry has no supports_max_tokens, and
            // supported_efforts is max/high/low, so the "medium" this table used to send was not a
            // value the model has). max_tokens IS the thinking budget, not just the answer budget:
            // OpenRouter derives reasoning from it through the effort fraction and no effort hint is
            // sent, so the cap decides how long the model may think. Measured on ONE deck (Elves, the
            // same seed pair) at 16000 vs 6000 via OPENROUTER_MODEL_CFG, no rebuild: max completion
            // 14989 vs 4600, p90 4876 vs 3978, median 1382 vs 1203. The cap clips the TAIL -- exactly
            // the hard decisions -- which is why raising it bought thinking rather than headroom. 16000
            // still clipped: 9 of ~1,770 deepseek calls in runs/compare hit it, every one a hard main
            // phase (8 castable + 2 lands), and the truncation retry then answered it with reasoning
            // OFF -- the hardest decisions decided without thinking. 32000 (2026-10-15), with the
            // timeout doubled to 300s so the longer think can finish (16000 tokens took 80-130s); separating the two budgets properly needs an explicit,
            // supported reasoning.effort, which is the next step. A model that still overruns is
            // truncated once and retried with reasoning off, which the game log and match_review
            // already report per game, so the tail is visible rather than silent.
            //
            // The fast tier (two-tier runs only; ONE_TIER uses this smart row everywhere) keeps reasoning
            // off -- that is what the two-tier arm IS; a model that refuses that gets its lowest effort
            // (MANDATORY_REASONING_FLOOR) -- but its answer is no longer cut: 1024 tokens
            // truncated 2 of 170 fast calls in runs/compare/two-tier, and the retry then asked again
            // at the same 1024. 8192 tokens and 120s (docs/harness/MODEL-LIMITS.md).
            ModelCfg uniform = new ModelCfg(32000, 300000, null, null, 8192, 120000, "none");
            // THE TIMEOUT IS LOAD-BEARING: LlmSpikeHeadless sizes its post-cap shutdown grace from
            // maxDecisionMs() (three attempts of the slowest timeout), and run_batch.py's backstop budgets
            // for that with a hardcoded GRACE_S = 1000 (it cannot call the Java side). 300s gives a
            // grace of about 912s, inside that budget. Raising any timeout further REQUIRES raising GRACE_S too, or
            // capped games get killed before an in-flight ask lands — the orphaned-row silent drop
            // that defect_analyzer.py exists to catch. graceFitsRunBatchBudget() in LlmSpikeTest
            // asserts this mechanically so it cannot be forgotten.
            for (String key : new String[]{"opus", "sonnet", "haiku", "deepseek", "gpt", "kimi", "default"})
                t.put(key, uniform);
            // A model that cannot be told to stop reasoning (grok-4.7, Sonnet 5.5, the Gemini models, ...)
            // takes this row too: send() replaces its "none" with MANDATORY_REASONING_FLOOR's lowest effort,
            // on the fast tier and the truncation retry alike. Keep it per model id, never per family: a
            // grok-wide row would send "low" to grok-build-0.1 (no efforts) and to grok-4.3 (can turn it off).
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
                        longField(fields, "fastTimeoutMs", base.fastTimeoutMs),
                        strField(fields, "fastReasoningEffort", base.fastReasoningEffort));
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

        /**
         * The longest ONE DECISION can legitimately take -- which is not one timeout.
         *
         * A null reply sends PlayerControllerLLM.ask() into its retry loop: up to
         * BRIDGE_RETRY_BACKOFF_MS.length additional attempts, each getting a FULL fresh timeout,
         * plus the backoffs between them. So the real worst case is 3 x the slowest configured
         * timeout, not 1 -- 542.5s today, against a post-cap grace built from
         * maxConfiguredTimeoutMs() + 10s = 190s. A game capped while a decision was on its second
         * or third attempt therefore got System.exit()'d with the ask in flight and no jsonl row
         * written: precisely the silent drop that grace exists to prevent, arriving through the one
         * path its arithmetic did not consider.
         *
         * Pre-existing, but reached more often after 9899f58f made OpenRouter timeouts real -- a
         * timeout is now the common way to get the null that starts the retry loop, where before
         * the call simply ran long and returned.
         */
        static long maxDecisionMs() {
            long backoff = 0;
            for (long b : PlayerControllerLLM.BRIDGE_RETRY_BACKOFF_MS) backoff += b;
            long attempts = PlayerControllerLLM.BRIDGE_RETRY_BACKOFF_MS.length + 1;
            return maxConfiguredTimeoutMs() * attempts + backoff;
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
    static String oneLine(String s) {
        if (s == null) return "";
        s = s.replaceAll("\\s+", " ").trim();
        return truncateWords(s, 90);
    }

    /** {@link #oneLine} without the 90-character cut. The faithful profile uses it wherever the cut
     *  removed what a decision turns on: Sneak Attack's INSPECT text lost "sacrifice the creature at
     *  the beginning of the next end step", and a ten-card reveal kept three names (runs/loop/g04). */
    static String flat(String s) {
        // Oracle text keeps its line breaks as a literal backslash-n; shown raw, the seat read "\n{T}: ...".
        return s == null ? "" : s.replace("\\n", " ").replaceAll("\\s+", " ").trim();
    }

    /** The additive prompt-cache fields of a jsonl row; empty when the provider reported neither. */
    static String cacheFields(int cachedTokens, int cacheWriteTokens) {
        return (cachedTokens >= 0 ? ",\"cached_tokens\":" + cachedTokens : "")
                + (cacheWriteTokens >= 0 ? ",\"cache_write_tokens\":" + cacheWriteTokens : "");
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
                retryMs, attempts, finishReason, firstFailedReply, promptTokens, completionTokens, null, null);
    }
    /** `picks`: additive, present only on multi-pick rows (0-based indices into `options`). */
    static void emitDecisionRow(Player pl, String seat, String type, int nOptions, int choice, String tier,
            long ms, String fallback, List<String> options, String prompt, String rawReply,
            long retryMs, int attempts, String finishReason, String firstFailedReply,
            int promptTokens, int completionTokens, Integer shadowChoice, List<Integer> picks) {
        emitDecisionRow(pl, seat, type, nOptions, choice, tier, ms, fallback, options, prompt, rawReply, retryMs,
                attempts, finishReason, firstFailedReply, promptTokens, completionTokens, shadowChoice, picks, null, 0, -1, -1);
    }
    /** The proposal fields of the next decision row (PROPOSE_ACTIONS), set by the play decision on its own thread. */
    static final ThreadLocal<String> PROPOSAL_ROW = new ThreadLocal<>();

    /** `cachedTokens` / `cacheWriteTokens`: additive, written only when the provider reported them.
     *  `answerVia`: additive, present only when the ask offered the answer tool (AnswerTool): "tool",
     *  "text", "reask" or "lost"; `answer_invalid` counts the invalid calls corrected on the way. */
    static void emitDecisionRow(Player pl, String seat, String type, int nOptions, int choice, String tier,
            long ms, String fallback, List<String> options, String prompt, String rawReply,
            long retryMs, int attempts, String finishReason, String firstFailedReply,
            int promptTokens, int completionTokens, Integer shadowChoice, List<Integer> picks,
            String answerVia, int answerInvalid, int cachedTokens, int cacheWriteTokens) {
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
                    .append(",\"shadow_choice\":").append(shadowChoice == null ? "null" : shadowChoice);
            if (picks != null) sb.append(",\"picks\":").append(picks.toString().replace(" ", ""));
            if (answerVia != null) sb.append(",\"answer_via\":\"").append(answerVia).append("\",\"answer_invalid\":").append(answerInvalid);
            // PROPOSE_ACTIONS: the play's proposals, set by the play decision just before its row (additive fields).
            String proposals = PROPOSAL_ROW.get();
            if (proposals != null) { sb.append(proposals); PROPOSAL_ROW.remove(); }
            sb.append(cacheFields(cachedTokens, cacheWriteTokens));
            sb.append('}');
            // The write, the index increment, and the hook dispatch must be one atomic unit
            // with respect to concurrently-deciding seats: fork addressing depends on
            // index == emission order == file line order. EvalLog.write is static synchronized,
            // so synchronizing on EvalLog.class here extends that same monitor (re-entrant)
            // around the increment -- two threads can no longer interleave between a write and
            // its incrementAndGet, which would silently address the wrong decision row.
            // Unconditional increment: FORK_DECISION_SEQ must track every decision row (this is
            // the ONLY place it's incremented) even with no hook installed -- check mode reads
            // it afterward to report the real decision count. Inert overhead on non-fork runs.
            synchronized (EvalLog.class) {
                EvalLog.write(sb.toString());
                int idx = FORK_DECISION_SEQ.incrementAndGet();
                ForkHook hook = FORK_HOOK; // read once: volatile, and null in every non-fork run
                if (hook != null) {
                    try { hook.onDecision(idx, pl, seat, type, nOptions, choice, options); } catch (Throwable ignore) { }
                }
            }
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
        // The mulligan answer is the upper-case word. Any "keep" in prose ("keep mana open") made a
        // reply cut off mid-thought look answered, so its truncation retry never ran.
        if (Pattern.compile("\\b(KEEP|MULLIGAN)\\b").matcher(content).find()) return true;
        String u = content.toUpperCase(Locale.ROOT);
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
    static final class OpenRouterBridge implements ChatSessionBridge.MessagesBridge {
        private static final String API_KEY = System.getenv("OPENROUTER_API_KEY");
        /** The real endpoint, when OPENROUTER_BASE_URL is unset (DecisionsBridge derives its own from it). */
        static final String DEFAULT_BASE_URL = "https://openrouter.ai/api/v1";
        private static final String BASE_URL = System.getenv().getOrDefault("OPENROUTER_BASE_URL", DEFAULT_BASE_URL);
        // Explicit operator override: when set, pins EVERY model's timeout to this one value
        // (matches prior behavior exactly). Unset -> each bridge uses its own per-tier/per-model
        // default from ModelCfg instead of one flat number — Infra-1/Infra-2.
        static final Long TIMEOUT_MS_ENV = parseLongOrNull(System.getenv("OPENROUTER_TIMEOUT_MS"));
        private static final String HTTP_REFERER = System.getenv("OPENROUTER_HTTP_REFERER");
        /** OpenRouter provider sort ("latency", "throughput", "price"); empty = OpenRouter's default routing. */
        static final String PROVIDER_SORT = providerSort(System.getenv("OPENROUTER_PROVIDER_SORT"));
        static String providerSort(String env) {
            if (env == null || env.isBlank()) return "latency";
            return "default".equalsIgnoreCase(env.trim()) ? "" : env.trim().toLowerCase(java.util.Locale.ROOT);
        }
        private static final String HTTP_TITLE = System.getenv("OPENROUTER_HTTP_TITLE");
        /** OPENROUTER_MAX_PRICE as read at class-init; not final so a test can construct a bridge under a value. */
        static String maxPriceEnv = System.getenv("OPENROUTER_MAX_PRICE");
        /** OPENROUTER_QUANTIZATIONS as read at class-init; not final so a test can construct a bridge under a value. */
        static String quantizationsEnv = System.getenv("OPENROUTER_QUANTIZATIONS");
        /** OpenRouter's precision names (provider routing docs, 2026-10-26). */
        static final java.util.Set<String> QUANTIZATIONS = java.util.Set.of("int4", "int8", "fp4", "mxfp4", "nvfp4",
                "fp6", "fp8", "mxfp8", "fp16", "bf16", "fp32", "unknown");
        /**
         * The {@code "quantizations":[...]} field for {@code model}, or null. OPENROUTER_QUANTIZATIONS holds
         * {"<model>":["fp8","bf16",...]} (eval/puzzle_solve.py --quantizations): OpenRouter then serves the model only
         * from an endpoint at one of those precisions, so an open-weight model plays at its maker's released precision,
         * never on a host that compresses it further (owner, 2026-10-26). Unset, or a model it does not name: no filter.
         * Unreadable, empty, or a precision OpenRouter does not name: refused, never dropped.
         */
        static String quantizationsField(String env, String model) {
            if (env == null || env.isBlank()) return null;
            try {
                String field = null;   // every entry is checked, as maxPriceField does
                for (Map.Entry<String, com.google.gson.JsonElement> e
                        : com.google.gson.JsonParser.parseString(env).getAsJsonObject().entrySet()) {
                    List<String> q = new ArrayList<>();
                    for (com.google.gson.JsonElement v : e.getValue().getAsJsonArray()) {
                        String name = v.getAsJsonPrimitive().isString() ? v.getAsString() : "";
                        if (!QUANTIZATIONS.contains(name)) throw new IllegalArgumentException("unknown precision " + v);
                        q.add("\"" + name + "\"");
                    }
                    if (q.isEmpty()) throw new IllegalArgumentException("empty");
                    if (e.getKey().equals(model)) field = "\"quantizations\":[" + String.join(",", q) + "]";
                }
                return field;
            } catch (RuntimeException e) {
                throw new IllegalArgumentException("OPENROUTER_QUANTIZATIONS is not {\"<model>\":[\"fp8\",...]} with "
                        + "precisions from " + new java.util.TreeSet<>(QUANTIZATIONS) + ": " + env, e);
            }
        }

        /**
         * The {@code "max_price":{...}} field for {@code model}, or null. OPENROUTER_MAX_PRICE holds
         * {"<model>":{"prompt":P,"completion":C}} in USD per million tokens (eval/puzzle_solve.py --max-price-at-list
         * writes it from the list prices); OpenRouter then routes only to an endpoint at or under it, so latency
         * routing never lands on a priority tier (GPT-6.1 Sol's is 6x list, eval/pricing/prices.json `max`; owner,
         * 2026-10-25). Unset, or a model it does not name: no ceiling. Unreadable: refused, so a run never goes out
         * believing it is capped.
         */
        static String maxPriceField(String env, String model) {
            if (env == null || env.isBlank()) return null;
            try {
                String field = null;   // every entry is checked, not only this model's: one bad entry is a bad variable
                for (Map.Entry<String, com.google.gson.JsonElement> e
                        : com.google.gson.JsonParser.parseString(env).getAsJsonObject().entrySet()) {
                    com.google.gson.JsonObject o = e.getValue().getAsJsonObject();
                    java.math.BigDecimal prompt = o.get("prompt").getAsBigDecimal(), completion = o.get("completion").getAsBigDecimal();
                    if (prompt.signum() <= 0 || completion.signum() <= 0) throw new IllegalArgumentException("not positive");
                    if (e.getKey().equals(model))
                        // Strings: OpenRouter's schema types max_price's fields so (openapi.json, ProviderPreferences).
                        field = "\"max_price\":{\"prompt\":\"" + prompt.toPlainString() + "\",\"completion\":\""
                                + completion.toPlainString() + "\"}";
                }
                return field;
            } catch (RuntimeException e) {
                throw new IllegalArgumentException("OPENROUTER_MAX_PRICE is not {\"<model>\":{\"prompt\":P,\"completion\":C}} "
                        + "with P and C above 0 (USD per million tokens): " + env, e);
            }
        }
        private static Long parseLongOrNull(String v) {
            try { return v == null ? null : Long.parseLong(v.trim()); } catch (Exception e) { return null; }
        }

        private final String label;
        private final String model;
        private final String preamble;
        private final HttpClient client;
        private final ModelCfg cfg;
        private final String baseUrl;
        private final String maxPrice;   // maxPriceField for this bridge's model, or null
        private final String quantizations;   // quantizationsField for this bridge's model, or null
        private int routingRefusals;     // requests refused under maxPrice or quantizations ([ROUTING-FILTER])
        @Override public Integer getRoutingRefusals() { return maxPrice == null && quantizations == null ? null : routingRefusals; }

        /** For a puzzle row (budget()): the HTTP refusals both tiers had under their routing filter, or null when
         *  neither has one (the row then has no such key, as before the filters existed). */
        static Integer routingRefusals(LLMBridge smart, LLMBridge fast) {
            Integer n = null;
            for (LLMBridge b : smart == fast ? new LLMBridge[] {smart} : new LLMBridge[] {smart, fast}) {
                Integer c = b == null ? null : b.getRoutingRefusals();
                if (c != null) n = (n == null ? 0 : n) + c;
            }
            return n;
        }
        private final boolean fastTier; // label is always "<seat>/smart" or "<seat>/fast" — see PlayerControllerLLM ctor
        private final long timeoutMs;
        private long totalMs = 0;
        private int calls = 0;
        private volatile String lastFinishReason;
        private volatile int lastPromptTokens = -1;
        private volatile int lastCompletionTokens = -1;
        private volatile long billedPromptTokens = 0, billedCompletionTokens = 0;
        private volatile int lastCachedTokens = -1, lastCacheWriteTokens = -1;
        private volatile String lastProvider = null;
        // Test-only instrumentation (see lastSendCount()). A bridge belongs to one seat/tier and
        // ask() is never called concurrently on it, so the non-atomic ++ is safe; volatile is only
        // so a test thread reads what the calling thread wrote.
        private volatile int lastSendCount = 0;

        /** "Bearer <key>" for a real endpoint with a key; null for loopback (never send a key on-box)
         *  or when there is no key. Verifier BLOCKER 2026-09-07: this used to send the key everywhere. */
        static String authorizationHeader(String apiKey, String endpoint) {
            if (apiKey == null || apiKey.isEmpty()) return null;
            if (LLMBridgeFactory.isLoopbackEndpoint(endpoint)) return null;
            return "Bearer " + apiKey;
        }

        OpenRouterBridge(String label, String model, String preamble) {
            this(label, model, preamble, BASE_URL, ModelCfg.forModel(model));
        }

        private OpenRouterBridge(String label, String model, String preamble, String baseUrl, ModelCfg cfg) {
            this(label, model, preamble, baseUrl, timeoutFor(label, cfg));
        }

        /**
         * Test seam. BASE_URL is a {@code static final} read of OPENROUTER_BASE_URL at class-init,
         * so a test can never retarget the production constructor at a local server -- which left
         * the part of this class most worth testing (ONE deadline shared by the original send and
         * the truncation retry) covered by eye only. Package-private on purpose: production code
         * takes the 3-arg constructor above and its env-derived endpoint.
         */
        OpenRouterBridge(String label, String model, String preamble, String baseUrl, long timeoutMs) {
            // A LOCAL endpoint needs no key and must never be sent one (an invariant the reviews check:
            // "loopback endpoints never receive an API key", .claude/skills/pr-review). Demanding a key here
            // made the free local lane unusable for GAMES while it worked for puzzles, which is backwards: games
            // are the only instrument that can measure a memory/context change, and they are the ones that most
            // need to be free to repeat. Any non-loopback endpoint still requires the key.
            // Enforced here, not only at the factory, so no construction path can egress without
            // consent -- including the package-private test constructor above.
            LLMBridgeFactory.requireOpenRouterAccess(baseUrl, System.getenv("OPENROUTER_ALLOW_EGRESS"), API_KEY, label);
            this.label = label;
            this.model = model;
            this.preamble = preamble;
            this.baseUrl = baseUrl;
            this.fastTier = label != null && label.endsWith("/fast");
            this.cfg = ModelCfg.forModel(model);
            this.maxPrice = maxPriceField(maxPriceEnv, model);
            this.quantizations = quantizationsField(quantizationsEnv, model);
            this.timeoutMs = timeoutMs;
            this.client = HttpClient.newBuilder()
                    .connectTimeout(Duration.ofMillis(timeoutMs))
                    .build();
        }

        /** Per-tier deadline, or the flat OPENROUTER_TIMEOUT_MS override when an operator pins one. */
        private static long timeoutFor(String label, ModelCfg cfg) {
            boolean fast = label != null && label.endsWith("/fast");
            long base = fast ? cfg.fastTimeoutMs : cfg.timeoutMs;
            return TIMEOUT_MS_ENV != null ? TIMEOUT_MS_ENV : base;
        }

        /** HTTP round-trips the last ask() spent. A truncation retry makes it 2. Tests read this. */
        int lastSendCount() { return lastSendCount; }

        @Override public String ask(String prompt) {
            return askMessages("{\"role\":\"user\",\"content\":\"" + jsonEscape(prompt) + "\"}");
        }

        /** A plain-text ask over a whole message list (comma-separated objects after the system
         *  preamble): the one-chat conversation ({@link ChatSessionBridge}). */
        @Override public String askMessages(String messages) {
            OrResult r = complete(messages, null, false);
            return r == null ? null : r.content;
        }

        /** False once the provider has refused a request BECAUSE it carried tools (see send()). */
        @Override public boolean supportsNativeTools() { return !nativeRefused; }
        private volatile boolean nativeRefused = false;
        /** The last complete() failed in transport (LLMBridge.lastFailureWasTransport); set by send() and the IO catch. */
        private volatile boolean transportFailure = false;
        @Override public boolean lastFailureWasTransport() { return transportFailure; }

        /** A 4xx whose body talks about tools / functions is the provider saying this model or route
         *  has no function calling -- not a transient failure. Package-private for NativeToolsTest. */
        static boolean refusesTools(int status, String body) {
            if (status < 400 || status >= 500 || status == 401 || status == 403 || status == 408 || status == 429) return false;
            // Only the wordings that mean "no function calling here". A 400 that merely mentions tools
            // (a context-length breakdown, a malformed tool message) must not end native calling.
            String b = body == null ? "" : body.toLowerCase(java.util.Locale.ROOT);
            return (b.contains("support") && (b.contains("tool") || b.contains("function calling")))
                    && (b.contains("no endpoints") || b.contains("not support") || b.contains("unsupported")
                        || b.contains("n't support"));
        }

        @Override public NativeTools.Turn chat(String messagesJson, String toolsJson, boolean allowCalls) {
            if (nativeRefused) { transportFailure = false; return null; } // refused once: do not send it again
            String m = messagesJson.trim();
            // The caller hands a JSON array; send() splices its elements after the system preamble.
            OrResult r = complete(m.substring(1, m.length() - 1), toolsJson, allowCalls);
            return r == null ? null : r.turn;
        }

        /** One ask: a completion plus the truncation retry, under ONE deadline. `messages` is the
         *  comma-separated message objects after the system preamble. `toolsJson == null` is the
         *  plain-text call every pre-native run made, byte for byte. */
        private OrResult complete(String messages, String toolsJson, boolean allowCalls) {
            long t0 = System.currentTimeMillis();
            // ONE deadline for the whole call, retry included. run_batch.py's post-cap grace
            // budgets for ModelCfg.maxConfiguredTimeoutMs() as "the longest a single decision can
            // take", and model_cfg_sha now records the value each run used; letting the truncation
            // retry below start a fresh budget would quietly make the real figure 2x both. The
            // Claude lane already works this way (one deadline, destroyForcibly on expiry) -- the
            // two lanes now mean the same thing by the same word.
            final long deadline = t0 + timeoutMs;
            lastSendCount = 0;
            transportFailure = false;
            try {
                int maxTokens = fastTier ? cfg.fastMaxTokens : cfg.maxTokens;
                // reasoning_effort/temperature are smart-tier-only hints — the fast tier's contract is
                // "terse, no extended reasoning" (see framing()), so never ask a model to reason harder there
                // (a model that cannot stop reasoning is held at its lowest effort, MANDATORY_REASONING_FLOOR).
                String reasoningEffort = fastTier ? cfg.fastReasoningEffort : cfg.reasoningEffort;
                Double temperature = cfg.temperature;

                OrResult r = send(messages, toolsJson, allowCalls, maxTokens, reasoningEffort, temperature, deadline);
                if (r == null) return null; // transport/HTTP-status failure, already logged by send()
                bill(r);
                r = r.withContent(collapseDegenerateRepetition(r.content)); // F2 residual (usage carried through)

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
                if ("length".equals(r.finishReason) && !r.hasCalls() && !looksLikeDecisionTag(r.content)) {
                    // A REASONER that burns the whole budget on hidden reasoning and emits no answer
                    // token (measured 2026-10-13: a 16000-token smart completion, retried at 32000,
                    // ~3 minutes of the 8-minute game cap) does not need MORE room to think -- that is
                    // exactly what it just spent. Asking it to stop reasoning gets an answer in ~1s
                    // (measured: enabled=false -> 65 completion tokens, 1.3s). So for a row we know is
                    // a reasoner, the retry DISABLES reasoning; every other model keeps the historic
                    // double-the-budget retry, which is right for a genuinely long answer.
                    // A call whose reasoning was already off ("none": the fast tier) gets nothing from
                    // turning it off again; it needs room for its answer, so it takes the doubled budget.
                    boolean reasoner = !"none".equals(reasoningEffort)
                            && (reasoningEffort != null || cfg.fastReasoningEffort != null);
                    int retryTokens = Math.min(maxTokens * 2, 64000);
                    String retryEffort = reasoningEffort;
                    // A model that refuses reasoning-off is sent its lowest effort instead (send()), and still
                    // reasons there, so its retry keeps the whole budget rather than the 4096 an answer needs.
                    boolean floored = !"none".equals(reasoningFloor(model));
                    if (reasoner) {
                        retryTokens = floored ? maxTokens : Math.min(Math.max(maxTokens, 1024), 4096);
                        retryEffort = "none";
                    }
                    System.out.println("[OPENROUTER " + label + "] [TRUNCATED->RETRY"
                            + (reasoner ? (floored ? "-LOW-REASONING" : "-NO-REASONING") : "")
                            + "] finish_reason=length @ max_tokens=" + maxTokens + " -> retrying at " + retryTokens
                            + (reasoner ? (floored ? " with its lowest reasoning effort" : " with reasoning disabled") : ""));
                    OrResult retry;
                    try {
                        retry = send(messages, toolsJson, allowCalls, retryTokens, retryEffort, temperature, deadline);
                    } catch (Exception e) {
                        // The retry's own failure, even a reset connection, leaves the first answer as the one kept.
                        System.out.println("[OPENROUTER " + label + "] [TRUNCATED->RETRY] the retry failed: " + e);
                        retry = null;
                    }
                    if (retry != null) bill(retry);
                    // Only adopt the retry if it's not itself a non-answer: a truncated-but-substantive
                    // original reply is a better final result than a garbled/empty retry, and either way
                    // the isNonAnswer() check right below still gets the final say.
                    if (retry != null && (retry.hasCalls() || !isNonAnswer(retry.content)))
                        r = retry.withContent(collapseDegenerateRepetition(retry.content));
                    else transportFailure = false; // the answer kept is the first, which the provider sent
                }
                // A turn that is only tool calls has no text and is not a non-answer.
                if (!r.hasCalls() && isNonAnswer(r.content)) {
                    System.out.println("[OPENROUTER " + label + "] non-answer -> treating as bridge-down");
                    lastFinishReason = r.finishReason;
                    lastPromptTokens = r.promptTokens; lastCompletionTokens = r.completionTokens;
                    lastCachedTokens = r.cachedTokens; lastCacheWriteTokens = r.cacheWriteTokens;
                    return null;
                }
                lastFinishReason = r.finishReason;
                lastPromptTokens = r.promptTokens; lastCompletionTokens = r.completionTokens;
                    lastCachedTokens = r.cachedTokens; lastCacheWriteTokens = r.cacheWriteTokens;
                long dur = System.currentTimeMillis() - t0;
                totalMs += dur; calls++;
                System.out.println("[OPENROUTER " + label + "] replied in " + dur + " ms"
                        + (r.hasCalls() ? " with " + r.turn.calls.size() + " tool call(s)" : ""));
                return r;
            } catch (Exception e) {
                System.out.println("[OPENROUTER " + label + "] error: " + e);
                transportFailure = HttpDeadline.transportError(e);
                return null;
            }
        }

        /** One HTTP round-trip. Returns null (transport/HTTP failure already logged) or a result
         *  carrying both the completion text and its finish_reason. */
        private OrResult send(String messages, String toolsJson, boolean allowCalls, int maxTokens,
                              String reasoningEffort, Double temperature, long deadline) throws Exception {
            long remaining = deadline - System.currentTimeMillis();
            if (remaining <= 0) {
                System.out.println("[OPENROUTER " + label + "] budget exhausted before send ("
                        + timeoutMs + " ms deadline already spent)");
                return null;
            }
            lastSendCount++;
            StringBuilder body = new StringBuilder();
            body.append("{\"model\":\"").append(jsonEscape(model)).append("\",");
            body.append("\"messages\":[");
            if (preamble != null && !preamble.isEmpty()) {
                body.append("{\"role\":\"system\",\"content\":\"").append(jsonEscape(preamble)).append("\"},");
            }
            body.append(messages);
            body.append("],\"max_tokens\":").append(maxTokens);
            // Route by latency, not price (OpenRouter's default weighs price): the same model on every
            // endpoint, but Luna calls stalled for 5 minutes with ordinary-sized answers (runs/luna-v8/g3,
            // runs/luna-v9). OPENROUTER_PROVIDER_SORT=default sends no preference.
            // A tool request also routes only to endpoints that honour every parameter sent: without it
            // OpenRouter may pick one that ignores tool_choice "none" (the seat-tools-v5 defect, a call made
            // on a round that offered none). An endpoint that refuses is the existing 404 downgrade path.
            // ONE "provider" object: a body with the key twice is read last-wins by some parsers, which
            // drops the latency sort on every tool request (provider audit, 2026-10-24).
            List<String> provider = new ArrayList<>();
            if (!PROVIDER_SORT.isEmpty()) provider.add("\"sort\":\"" + jsonEscape(PROVIDER_SORT) + "\"");
            if (toolsJson != null) provider.add("\"require_parameters\":true");
            if (maxPrice != null) provider.add(maxPrice);
            if (quantizations != null) provider.add(quantizations);
            if (!provider.isEmpty()) body.append(",\"provider\":{").append(String.join(",", provider)).append("}");
            // Every ask resends the same preamble, tools and chat history, and nothing marked it
            // cacheable, so Anthropic models billed all of it at the full input rate (a Haiku 5.5 game
            // averaged 10K prompt tokens a call). Top-level cache_control is automatic caching: the
            // breakpoint follows the end of the conversation. DeepSeek and OpenAI cache on their own.
            if (cachesPrompt(model)) body.append(",\"cache_control\":{\"type\":\"ephemeral\"}");
            if ("none".equals(reasoningEffort))
                reasoningEffort = reasoningFloor(model);   // "" sends no reasoning field
            if ("none".equals(reasoningEffort))
                // Measured 2026-10-13: a mechanical answer costs ~848 completion tokens of which ~800 are
                // hidden reasoning, and "low"/"minimal" do not reduce that -- only disabling does (65
                // tokens, 1.3s). "none" is OUR sentinel for that, kept distinct from effort values.
                body.append(",\"reasoning\":{\"enabled\":false}");
            else if (reasoningEffort != null && !reasoningEffort.isEmpty())
                body.append(",\"reasoning\":{\"effort\":\"").append(jsonEscape(reasoningEffort)).append("\"}");
            if (temperature != null)
                body.append(",\"temperature\":").append(temperature);
            if (toolsJson != null)
                body.append(",\"tools\":").append(toolsJson)
                    // An answer ask (it offers `answer`) is forced where that keeps the model's reasoning
                    // (AnswerTool.toolChoiceFor): then a call is the only way to reply.
                    .append(",\"tool_choice\":\"").append(!allowCalls ? "none"
                            : toolsJson.contains("\"name\":\"" + AnswerTool.NAME + "\"") ? AnswerTool.toolChoiceFor(model) : "auto").append("\"");
            body.append("}");

            HttpRequest.Builder rb = HttpRequest.newBuilder()
                    .uri(URI.create(baseUrl + "/chat/completions"))
                    .header("Content-Type", "application/json");
            // Send the key only to a real endpoint. A loopback run legitimately has none (see the
            // constructor) and MUST NOT receive one even when the operator's shell happens to carry
            // OPENROUTER_API_KEY (secrets.env is auto-loaded by the eval drivers): a local stub is
            // cleartext HTTP, and the acceptance suite's fake server refuses any Authorization header.
            // The decision is a pure function so LoopbackKeyTest can exercise it with a fake key.
            String auth = authorizationHeader(API_KEY, baseUrl);
            if (auth != null) rb.header("Authorization", auth);
            if (HTTP_REFERER != null && !HTTP_REFERER.isEmpty()) rb.header("HTTP-Referer", HTTP_REFERER);
            if (HTTP_TITLE != null && !HTTP_TITLE.isEmpty()) rb.header("X-Title", HTTP_TITLE);

            String sentBody = body.toString();
            HttpRequest req = rb.POST(HttpRequest.BodyPublishers.ofString(sentBody, StandardCharsets.UTF_8))
                    .timeout(HttpDeadline.requestTimeout(remaining))
                    .build();
            // HttpRequest.timeout() above bounds only the wait for the response HEADERS; the body
            // read is unbounded, and OpenRouter dribbles keepalives for the whole generation. See
            // HttpDeadline for the measurement and the consequence.
            // Every round-trip, used or not (a truncated first completion, a refusal), exactly as it crossed.
            HttpResponse<String> resp = Transcript.send("openrouter", label, client, req, sentBody, remaining);
            if (resp == null) {
                String why = HttpDeadline.LAST_STOP.get();
                // A connection that sent no headers, or went silent, gave no reply; the wall-clock deadline is the
                // model's time spent (a reasoner that never answered).
                transportFailure = HttpDeadline.gaveNoReply(why);
                if (transportFailure)
                    System.out.println("[OPENROUTER " + label + "] stalled connection (" + why + " for " + HttpDeadline.IDLE_MS
                            + " ms) -> retrying instead of waiting out the " + timeoutMs + " ms deadline");
                else
                    System.out.println("[OPENROUTER " + label + "] wall-clock timeout after " + remaining
                            + " ms (deadline " + timeoutMs + " ms)");
                return null;
            }
            if (resp.statusCode() != 200) {
                System.out.println("[OPENROUTER " + label + "] HTTP " + resp.statusCode() + ": " + oneLine(resp.body()));
                int st = resp.statusCode();
                String lower = resp.body() == null ? "" : resp.body().toLowerCase(java.util.Locale.ROOT);
                // The filter's own fields, not the word "provider": OpenRouter relays every upstream's 400 as
                // "Provider returned error", and that request error is the model's (pr-review of #327).
                boolean filterShape = st == 400 && !lower.contains("provider returned error")
                        && (lower.contains("max_price") || lower.contains("quantization")
                            || lower.contains("provider preferences") || lower.contains("routing requirements"));
                if ((maxPrice != null || quantizations != null) && (st == 404 || st >= 500 || filterShape)) {
                    // Under a routing filter a 404 can mean no endpoint the filter allows, not no function calling,
                    // and a 5xx the allowed endpoints down with the others it would fail over to ruled out (a filter
                    // can leave one: five frontier models had one endpoint at list, 2026-10-25). OpenRouter's reply to
                    // such an exclusion is not recorded, so both count: native tools stay, the log names the filter,
                    // and the row counts it, so puzzle_score.py refuses to score decisions the model never made (an
                    // over-count costs a replay). A 400 naming the filter's fields is its own shape refused; any other
                    // 400 (a relayed upstream one included) is about the request, a 429 a rate limit: scored as without.
                    routingRefusals++;
                    System.out.println("[OPENROUTER " + label + "] [ROUTING-FILTER] HTTP " + st + " under "
                            + (maxPrice == null ? "" : maxPrice + " ") + (quantizations == null ? "" : quantizations)
                            + ": no endpoint the filter allows took the request; native tools kept");
                    return null;
                }
                // A refusal of function calling is an answer (native tools end, native_tools_downgraded says so);
                // any other status is the transport failing: 402 credits, 401, 429, 5xx.
                boolean toolRefusal = toolsJson != null && refusesTools(resp.statusCode(), resp.body());
                transportFailure = !toolRefusal;
                if (toolRefusal) {
                    // Retrying the same request cannot work. Stop offering tools on this bridge so the
                    // seat keeps its lookups through the text grammar, and say so on the game line.
                    nativeRefused = true;
                    NativeTools.DOWNGRADED.add(label);
                    System.out.println("[OPENROUTER " + label + "] [NATIVE-TOOLS->TEXT] the provider refused function "
                            + "calling for " + model + "; this seat/tier uses the text tool grammar from here on");
                }
                return null;
            }
            String served = extractServedModel(resp.body());
            if (served != null) servedModels.put(label, served);
            lastProvider = extractServedProvider(resp.body());

            String content = extractJsonString(resp.body(), "\"content\":\"");
            String finishReason = extractJsonString(resp.body(), "\"finish_reason\":\"");
            // OpenAI-compatible usage object: {"usage":{"prompt_tokens":N,"completion_tokens":M,...}}.
            // -1 when the field is absent. Additive persistence for Stage-0 token-cost calibration.
            int promptTokens = extractJsonInt(resp.body(), "\"prompt_tokens\":");
            int completionTokens = extractJsonInt(resp.body(), "\"completion_tokens\":");
            int cachedTokens = extractJsonInt(resp.body(), "\"cached_tokens\":");
            int cacheWriteTokens = extractJsonInt(resp.body(), "\"cache_write_tokens\":");
            if (toolsJson != null) {
                // A native turn is structured (tool calls, a null content): parse it, do not scan it.
                NativeTools.Turn turn = NativeTools.parseTurn(resp.body());
                if (turn == null) {
                    System.out.println("[OPENROUTER " + label + "] unparseable native turn: " + oneLine(resp.body()));
                    return null;
                }
                return new OrResult(turn.content, finishReason, promptTokens, completionTokens, turn)
                        .withCache(cachedTokens, cacheWriteTokens);
            }
            return new OrResult(content, finishReason, promptTokens, completionTokens, null)
                    .withCache(cachedTokens, cacheWriteTokens);
        }

        /** PROMPT_CACHE=0 turns it off, to reproduce a run made before caching. */
        static final boolean PROMPT_CACHE = !"0".equals(System.getenv("PROMPT_CACHE"));
        /** Anthropic models cache only a prefix marked with cache_control; others cache on their own. */
        static boolean cachesPrompt(String model) {
            return PROMPT_CACHE && model != null && model.startsWith("anthropic/");
        }

        /** Models whose reasoning cannot be turned off (reasoning.mandatory=true, OpenRouter /api/v1/models, read
         *  2026-10-24), mapped to the lowest effort each lists; "" for one that lists none (send no field). A
         *  reasoning:{enabled:false} is refused by these, so the truncation retry and the fast tier ask for this. */
        static final Map<String, String> MANDATORY_REASONING_FLOOR = Map.ofEntries(
                Map.entry("x-ai/grok-4.7", "low"), Map.entry("x-ai/grok-4.6", "low"),
                Map.entry("x-ai/grok-build-0.1", ""),
                Map.entry("anthropic/claude-sonnet-5.5", "low"), Map.entry("anthropic/claude-opus-5.5", "low"),
                Map.entry("openai/gpt-6.1-sol-pro", "low"), Map.entry("openai/gpt-6.1-sol", "low"),
                Map.entry("google/gemini-4-argon", "low"), Map.entry("google/gemini-3.8-flash", "low"),
                Map.entry("meta/muse-wonder", "minimal"), Map.entry("meta/muse-spark-1.3", "minimal"),
                Map.entry("z-ai/glm-5.3-prime", "low"), Map.entry("z-ai/glm-5.3", "low"));

        /** What "none" becomes for {@code model}: "none" itself, or a mandatory reasoner's floor. */
        static String reasoningFloor(String model) {
            return model == null ? "none"
                    : MANDATORY_REASONING_FLOOR.getOrDefault(model.toLowerCase(java.util.Locale.ROOT), "none");
        }

        private static final class OrResult {
            final String content, finishReason;
            final int promptTokens, completionTokens;
            final NativeTools.Turn turn; // null on the plain-text path
            int cachedTokens = -1, cacheWriteTokens = -1;
            OrResult(String content, String finishReason, int promptTokens, int completionTokens, NativeTools.Turn turn) {
                this.content = content; this.finishReason = finishReason;
                this.promptTokens = promptTokens; this.completionTokens = completionTokens;
                this.turn = turn;
            }
            boolean hasCalls() { return turn != null && !turn.calls.isEmpty(); }
            OrResult withCache(int cached, int written) { cachedTokens = cached; cacheWriteTokens = written; return this; }
            OrResult withContent(String c) {
                return new OrResult(c, finishReason, promptTokens, completionTokens,
                        turn == null ? null : new NativeTools.Turn(c, turn.messageJson, turn.calls))
                        .withCache(cachedTokens, cacheWriteTokens);
            }
        }

        /** The endpoint every openrouter: spec in this JVM talked to. Provenance only — a run against
         *  a local mlx server and a run against openrouter.ai are different experiments even when the
         *  model spec string is identical. */
        static String baseUrl() { return BASE_URL; }

        @Override public String getLabel() { return label; }
        @Override public String getModel() { return model; }
        @Override public long getTotalMs() { return totalMs; }
        @Override public int getCalls() { return calls; }
        @Override public String getLastFinishReason() { return lastFinishReason; }
        @Override public int getLastPromptTokens() { return lastPromptTokens; }
        @Override public int getLastCompletionTokens() { return lastCompletionTokens; }
        @Override public long getBilledPromptTokens() { return billedPromptTokens; }
        @Override public long getBilledCompletionTokens() { return billedCompletionTokens; }
        /** One send that came back, billed whether or not its reply is used (pr-review of #282). */
        private void bill(OrResult r) {
            billedPromptTokens += Math.max(0, r.promptTokens);
            billedCompletionTokens += Math.max(0, r.completionTokens);
        }
        @Override public int getLastCachedTokens() { return lastCachedTokens; }
        @Override public String getLastProvider() { return lastProvider; }
        @Override public int getLastCacheWriteTokens() { return lastCacheWriteTokens; }
    }

    /** Factory: parses a model spec like "haiku" (Claude CLI) or
     *  "openrouter:anthropic/claude-3.5-sonnet" (OpenRouter API). */
    static final class LLMBridgeFactory {
        static LLMBridge create(String label, String spec, String preamble) {
            if (spec == null || spec.isEmpty())
                throw new IllegalArgumentException("model spec required for " + label);
            // A "cli:" lane other than claude-cli: (Claude-CLI:, cli:, claude_cli:) is a typo, not a model.
            if (spec.toLowerCase(Locale.ROOT).contains("cli:") && !ClaudeCliBridge.isSpec(spec))
                throw new IllegalArgumentException("'" + spec + "' names a cli: lane but is not " + ClaudeCliBridge.PREFIX
                        + "<model> (" + label + ")");
            // A written solution line played through the model seat (ScriptBridge): no model, nothing leaves.
            if (spec.startsWith(ScriptBridge.PREFIX)) {
                // "script:relaxed:<file>" is a minimality replay (puzzle_baselines.py); the spec is the row's solver model.
                String rest = spec.substring(ScriptBridge.PREFIX.length());
                boolean relaxed = rest.startsWith(ScriptBridge.RELAXED);
                ScriptBridge b = ScriptBridge.fromFile(label, relaxed ? rest.substring(ScriptBridge.RELAXED.length()) : rest);
                b.relaxed = relaxed;
                return b;
            }
            // A recorded attempt's model turns served again (ReplayBridge): no model, nothing leaves.
            if (spec.startsWith(ReplayBridge.PREFIX)) return ReplayBridge.fromSpec(label, spec.substring(ReplayBridge.PREFIX.length()));
            // The greedy-preview floor: zero-model, the seat's own one-ply previews (GreedyBridge).
            if (spec.startsWith(GreedyBridge.PREFIX)) return new GreedyBridge(label);
            if (spec.startsWith("openrouter:")) {
                // Explicit egress consent: every OpenRouter call ships the full game snapshot
                // (board, hand, deck plan) to a third-party API. Holding an API key is a
                // configuration fact, not consent — require the dedicated opt-in flag too.
                //
                // LOOPBACK IS EXEMPT, matching eval/replay_match.egress_allowed, which has always
                // exempted it. A request to localhost leaves nothing and receives no key, so gating
                // it protects nobody — and the cost of gating it is worse than nothing: it forces an
                // operator running a FREE local model to set the egress flag out of habit, which is
                // exactly how a consent flag stops meaning consent. The gate must fire on real
                // egress and only on real egress.
                requireEgressConsent(System.getenv("OPENROUTER_BASE_URL"),
                                     System.getenv("OPENROUTER_ALLOW_EGRESS"), label);
                String model = spec.substring("openrouter:".length());
                return new OpenRouterBridge(label, model, preamble);
            }
            // A decision model on OpenRouter's Decisions API (DecisionsBridge): the same egress gate and key rules.
            if (DecisionsBridge.isSpec(spec))
                return decisions(label, spec, preamble, System.getenv("OPENROUTER_BASE_URL"), System.getenv("OPENROUTER_API_KEY"),
                        System.getenv("OPENROUTER_ALLOW_EGRESS"), PlayerControllerLLM.MEMORY_TOOLS,
                        PlayerControllerLLM.lookaheadEnabled(), AnswerTool.enabled() && NativeTools.enabled());
            // A Claude model through the Claude Code CLI, answering by structured output (ClaudeCliBridge).
            if (ClaudeCliBridge.isSpec(spec))
                return claudeCli(label, spec, preamble, "1".equals(env(ClaudeCliBridge.OPT_IN)), PlayerControllerLLM.MEMORY_TOOLS, PlayerControllerLLM.lookaheadEnabled(),
                        AnswerTool.enabled() && NativeTools.enabled(), ChatSessionBridge.ON || ChatSessionBridge.TURN);
            // The bare-name text lane (ClaudeBridge) is retired (owner, 2026-10-25): it answered in text, inherited the
            // whole shell, was unstamped and unpriced.
            throw new IllegalArgumentException(retired(spec, label));
        }

        /** The prefixes the factory routes; any other spec was the retired bare-name lane. Mirrors
         *  eval/cost_report.py KNOWN_PREFIXES (eval/test_cost_report.py checks the two lists agree). */
        static final String[] ROUTED_PREFIXES = {"openrouter:", DecisionsBridge.PREFIX, ClaudeCliBridge.PREFIX,
                ScriptBridge.PREFIX, GreedyBridge.PREFIX, ReplayBridge.PREFIX};

        /** True for a spec no lane takes: a bare Claude name such as `opus`, once the CLI text lane. */
        static boolean isBare(String spec) {
            if (spec == null || spec.isEmpty()) return false;
            for (String p : ROUTED_PREFIXES) if (spec.startsWith(p)) return false;
            return true;
        }

        static String retired(String spec, String label) {
            return "'" + spec + "': the bare Claude CLI text lane is retired (owner, 2026-10-25); use " + ClaudeCliBridge.PREFIX
                    + "<model> with " + ClaudeCliBridge.OPT_IN + "=1 (" + label + ")";
        }

        /** The bridge of a seat that never asks a model (floor, random, the puzzle arms): it starts nothing, answers
         *  nothing, and says so if it is ever asked ([PLACEHOLDER-ASKED], a defect eval/gauntlet_scan.py fails on). */
        static LLMBridge placeholder(String label, String spec) {
            return new LLMBridge() {
                @Override public String ask(String prompt) {
                    System.out.println("[PLACEHOLDER-ASKED] " + label + " (" + spec + ") asked; a seat that asks no model "
                            + "reached it -> no answer");
                    return null;
                }
                @Override public String getLabel() { return label; }
                @Override public String getModel() { return spec; }
                @Override public long getTotalMs() { return 0; }
                @Override public int getCalls() { return 0; }
            };
        }

        /** A `claude-cli:` seat: a development lane on the operator's own Claude subscription (owner, 2026-10-24), so it is
         *  refused first unless the operator chose it ({@code optedIn}: ClaudeCliBridge.OPT_IN=1). Phase 1 offers it no
         *  lookups and no preview, and asks each decision in a fresh process (ONE_CHAT and TURN_CHAT wrap only an
         *  OpenRouterBridge), so a seat run with any of them on is refused rather than recorded as a condition it never
         *  had. Package-private so a test can pass the flags. */
        static LLMBridge claudeCli(String label, String spec, String preamble, boolean optedIn, boolean memoryTools,
                                   boolean lookahead, boolean answerTool, boolean chat) {
            if (!optedIn) throw new IllegalStateException("a " + ClaudeCliBridge.PREFIX + " seat runs the Claude Code CLI on the "
                    + "operator's own Claude subscription: it is for local development only and never part of a benchmark "
                    + "score (owner, 2026-10-24). Set " + ClaudeCliBridge.OPT_IN + "=1 to choose it (" + label + ")");
            refuseAnswerOnly(ClaudeCliBridge.PREFIX, memoryTools, lookahead, answerTool, label);
            if (chat) throw new IllegalStateException("a " + ClaudeCliBridge.PREFIX + " seat asks each decision in a fresh "
                    + "process: run it with ONE_CHAT=0 and TURN_CHAT=0 (no --one-chat, no --turn-chat) (" + label + ")");
            return new ClaudeCliBridge(label, spec.substring(ClaudeCliBridge.PREFIX.length()), preamble);
        }

        /** A model seat ({@code mode} llm) may not name a retired bare Claude name, and one on the Claude CLI
         *  (`claude-cli:`) is local development on the operator's own subscription (owner, 2026-10-24), used only when
         *  chosen ({@code optedIn}: ClaudeCliBridge.OPT_IN=1). A seat that asks no model (floor, random, the puzzle arms)
         *  keeps whatever placeholder it names. */
        static void requireCliOptIn(String spec, String mode, boolean optedIn, String label) {
            if (!"llm".equals(mode)) return;
            if (isBare(spec)) throw new IllegalArgumentException(retired(spec, label));
            if (optedIn || !ClaudeCliBridge.isSpec(spec)) return;
            throw new IllegalStateException("a " + ClaudeCliBridge.PREFIX + " seat ('" + spec + "') runs the Claude Code CLI on "
                    + "the operator's own Claude subscription: it is for local development only and never part of a benchmark "
                    + "score (owner, 2026-10-24). Set " + ClaudeCliBridge.OPT_IN + "=1 to choose it (" + label + ")");
        }

        /** A seat whose backend can give only the `answer` (and `set_plan`) call: a lookup offered to it would record a
         *  condition it never had, and without the answer tool (ANSWER_TOOL=0 or NATIVE_TOOLS=0) every decision would be
         *  a text ask it cannot take, so every decision would be lost. */
        static void refuseAnswerOnly(String prefix, boolean memoryTools, boolean lookahead, boolean answerTool, String label) {
            if (memoryTools) throw new IllegalStateException("a " + prefix + " seat cannot call lookups: run it with "
                    + "MEMORY_TOOLS=0 (no --memory-tools) (" + label + ")");
            if (lookahead) throw new IllegalStateException("a " + prefix + " seat cannot call simulate: run it with "
                    + "LOOKAHEAD=0 (no --lookahead) (" + label + ")");
            if (!answerTool) throw new IllegalStateException("a " + prefix + " seat answers only through the `answer` call: run "
                    + "it with ANSWER_TOOL and NATIVE_TOOLS on (no --text-answers, no --text-tools) (" + label + ")");
        }

        /** A `jev:` seat. It cannot call a lookup, so a seat run with them on is refused here rather than recorded
         *  as a condition it never had. Package-private so a test can pass the endpoint and the flags. */
        static LLMBridge decisions(String label, String spec, String preamble, String baseUrl, String apiKey, String consent,
                                   boolean memoryTools, boolean lookahead, boolean answerTool) {
            // Consent first: a refusal for the seat's flags must never hide a missing consent (pr-review of #305).
            DecisionsBridge b = new DecisionsBridge(label, spec.substring(DecisionsBridge.PREFIX.length()), preamble, baseUrl,
                    apiKey, consent);
            refuseAnswerOnly(DecisionsBridge.PREFIX, memoryTools, lookahead, answerTool, label);
            return b;
        }

        /** The egress consent ({@link #requireEgressConsent}) and, for a real endpoint, a key: what any bridge to
         *  OpenRouter needs before it is built. */
        static void requireOpenRouterAccess(String baseUrl, String consent, String apiKey, String label) {
            requireEgressConsent(baseUrl, consent, label);
            if ((apiKey == null || apiKey.isEmpty()) && !isLoopbackEndpoint(baseUrl))
                throw new IllegalStateException("OPENROUTER_API_KEY env var is required for an OpenRouter backend (" + label + ")");
        }

        /**
         * Refuses unless {@code baseUrl} is loopback or {@code consent} is the literal "1".
         *
         * Both values are passed in rather than read from the environment so BOTH branches are
         * reachable from a test. A consent gate whose refusal path can only be exercised by
         * mutating the process environment is a gate nobody has ever seen fire.
         *
         * OpenRouterBridge's constructor calls this too, so the boundary is enforced by
         * CONSTRUCTION rather than by each caller remembering to ask first -- the factory used to
         * be the only check, which meant the package-private test constructor added alongside
         * HttpDeadline could reach a non-loopback endpoint without consent.
         */
        static void requireEgressConsent(String baseUrl, String consent, String label) {
            if (isLoopbackEndpoint(baseUrl) || "1".equals(consent)) return;
            throw new IllegalStateException(
                    "openrouter: backend sends full game state to openrouter.ai; "
                    + "set OPENROUTER_ALLOW_EGRESS=1 to consent (" + label + ")");
        }

        /** True when {@code url} names a loopback host. Mirrors eval/replay_match.py's
         *  LOCAL_HOSTS/endpoint_host pair: a URL with no parseable host (a bare "host/path", an unset
         *  OPENROUTER_BASE_URL, which means the real openrouter.ai) is NON-local, so the gate fails closed. */
        static boolean isLoopbackEndpoint(String url) {
            if (url == null || url.trim().isEmpty()) return false;   // unset => the real API
            try {
                String host = new java.net.URI(url.trim()).getHost();
                if (host == null) return false;
                host = host.toLowerCase(Locale.ROOT);
                if (host.startsWith("[") && host.endsWith("]")) host = host.substring(1, host.length() - 1);
                return "localhost".equals(host) || "127.0.0.1".equals(host) || "::1".equals(host);
            } catch (Exception e) {
                return false;                                        // unparseable => fail closed
            }
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
        if (fallback.isEmpty()) System.out.println("[PLAN] no sidecar for " + deckPath + ": no plan text (the decklist still follows)");
        return fallback;
    }

    /** Per-card rules text for a seat's deck, "  Name {cost} — oracle text", one per distinct card,
     *  from Forge's OWN card database (no external file, no network). Lets the model read what its
     *  cards do instead of recalling them. Empty on any failure. */
    static String cardText(Deck d) { return cardText(d, false); }

    /** {@link #cardText(Deck)}, with each card's type line after its cost when {@code typeLine} ("  Lava Spike {R} |
     *  Sorcery - Arcane — ..."): a puzzle's solver reads Arcane, Equipment or Aura nowhere else (#391). That form also
     *  drops a land's "no cost" and the card script's literal line breaks ("\n"), which the deck form still prints. */
    static String cardText(Deck d, boolean typeLine) {
        if (d == null) return "";
        try {
            java.util.TreeMap<String, String> lines = new java.util.TreeMap<>();
            for (java.util.Map.Entry<forge.item.PaperCard, Integer> e : d.getMain()) {
                forge.item.PaperCard pc = e.getKey();
                String n = pc.getName();
                if (n == null || n.isEmpty() || lines.containsKey(n)) continue;
                StringBuilder b = new StringBuilder("  ").append(n);
                try {
                    forge.card.CardRules r = pc.getRules();
                    if (r != null) {
                        String mc = r.getManaCost() == null ? "" : r.getManaCost().toString();
                        if (!mc.isEmpty() && !(typeLine && r.getManaCost().isNoCost())) b.append(' ').append(mc);
                        if (typeLine && r.getType() != null) b.append(" | ").append(r.getType());
                        String t = r.getOracleText();
                        if (t != null && !t.isEmpty()) {
                            // Whole: a 400-character cut ended 7 cards of the deck pool mid-sentence
                            // (Animate Dead, Tamiyo, Ajani, Minsc & Boo, ...).
                            if (typeLine) t = t.replace("\\n", " ");   // the card script's line breaks, as cardTextLine
                            t = t.replaceAll("\\s+", " ").trim();
                            b.append(" — ").append(t);
                        }
                    }
                } catch (Exception ignore) { }
                lines.put(n, b.toString());
            }
            if (lines.isEmpty()) return "";
            StringBuilder b = new StringBuilder();
            for (String l : lines.values()) b.append(l).append('\n');
            return b.toString();
        } catch (Exception e) { return ""; }
    }
    static String decklist(Deck d) {
        if (d == null) return "";
        try {
            java.util.TreeMap<String, Integer> counts = new java.util.TreeMap<>();
            for (java.util.Map.Entry<forge.item.PaperCard, Integer> e : d.getMain()) {
                counts.merge(e.getKey().getName(), e.getValue(), Integer::sum);
            }
            StringBuilder b = new StringBuilder();
            for (java.util.Map.Entry<String, Integer> e : counts.entrySet()) {
                if (b.length() > 0) b.append(", ");
                b.append(e.getValue()).append("x ").append(e.getKey());
            }
            return b.toString();
        } catch (Exception e) { return ""; }
    }

    /** The endpoint's OWN name for the model that produced a chat-completion response, or null when
     *  it named none. This is the provenance that the configured spec cannot supply: an alias like
     *  "default_model" is resolved server-side, so only the response says which checkpoint answered.
     *
     *  Only the HEAD of the body is searched — everything before "choices" — so a model id quoted
     *  inside the completion text can never win the substring match and mislabel the whole run. A
     *  server that omits the field yields null, and the game line records "unknown": the honest
     *  reading, not a confident wrong answer. Package-private for LlmSpikeTest. */
    /** The provider OpenRouter routed a response to (its top-level `provider`), or null. Head only, as for the model. */
    static String extractServedProvider(String responseBody) {
        if (responseBody == null) return null;
        int choicesAt = responseBody.indexOf("\"choices\"");
        String head = choicesAt >= 0 ? responseBody.substring(0, choicesAt) : responseBody;
        String p = extractJsonString(head, "\"provider\":\"");
        return (p == null || p.isEmpty()) ? null : p;
    }

    static String extractServedModel(String responseBody) {
        if (responseBody == null) return null;
        int choicesAt = responseBody.indexOf("\"choices\"");
        String head = choicesAt >= 0 ? responseBody.substring(0, choicesAt) : responseBody;
        String served = extractJsonString(head, "\"model\":\"");
        return (served == null || served.isEmpty()) ? null : served;
    }

    /** Resolved sampling temperature for a model SPEC, for the game line. "n/a" for the Claude CLI
     *  backend (no temperature knob there); "default" when the spec's ModelCfg row leaves it unset and
     *  the request body omits the field entirely — which is a real, distinct configuration, not zero. */
    static String resolvedTemperature(String spec) {
        if (spec == null || !spec.startsWith("openrouter:")) return "n/a";
        try {
            Double t = ModelCfg.forModel(spec.substring("openrouter:".length())).temperature;
            return t == null ? "default" : String.valueOf(t);
        } catch (Exception e) { return "unknown"; }
    }

    /** sha256[:12] of the OPENROUTER_MODEL_CFG override JSON, or "builtin" when it is unset.
     *  The per-model max_tokens / timeout / reasoning-effort / temperature table is load-bearing for
     *  what the model was able to produce, and it lives entirely in an env var — without this hash two
     *  runs on materially different sampling configs are indistinguishable in the artifact. */
    static String modelCfgSha() {
        try {
            byte[] d = java.security.MessageDigest.getInstance("SHA-256")
                    .digest(ModelCfg.canonical().getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < 6; i++) sb.append(String.format("%02x", d[i]));
            return sb.toString();
        } catch (Exception e) { return "unknown"; }
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
        winner = finalWinner(winner, capped, gameError);

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

        // --- resolved-identity provenance: "which model, on which endpoint, actually played?" ---
        // The served_* fields are keyed by deck-identity NAME (the bridge label's seat is the deck
        // name), so like every other per-identity field they survive SWAP_SEATS unchanged.
        String smartASpec = System.getenv().getOrDefault("SMART_MODEL_A", smartDef);
        String smartBSpec = System.getenv().getOrDefault("SMART_MODEL_B", smartDef);
        String fastASpec  = System.getenv().getOrDefault("FAST_MODEL_A", fastDef);
        String fastBSpec  = System.getenv().getOrDefault("FAST_MODEL_B", fastDef);
        String nativeSeats = NativeTools.seats(PlayerControllerLLM.MEMORY_TOOLS, System.getenv().getOrDefault("CONTROLLER_A", "llm"), smartASpec, fastASpec, System.getenv().getOrDefault("CONTROLLER_B", "llm"), smartBSpec, fastBSpec);
        String answerSeats = AnswerTool.seats(System.getenv().getOrDefault("CONTROLLER_A", "llm"), smartASpec, fastASpec,
                System.getenv().getOrDefault("CONTROLLER_B", "llm"), smartBSpec, fastBSpec);
        boolean anyOpenRouter = smartASpec.startsWith("openrouter:") || smartBSpec.startsWith("openrouter:")
                || fastASpec.startsWith("openrouter:") || fastBSpec.startsWith("openrouter:");
        // A jev: seat (DecisionsBridge) talks to the same base, at its Decisions endpoint; it sends no provider sort.
        boolean jevA = DecisionsBridge.isSpec(smartASpec) || DecisionsBridge.isSpec(fastASpec);
        boolean jevB = DecisionsBridge.isSpec(smartBSpec) || DecisionsBridge.isSpec(fastBSpec);
        boolean anyDecisions = jevA || jevB;
        boolean cliA = ClaudeCliBridge.isSpec(smartASpec) || ClaudeCliBridge.isSpec(fastASpec);
        boolean cliB = ClaudeCliBridge.isSpec(smartBSpec) || ClaudeCliBridge.isSpec(fastBSpec);
        // Gate the accessor so a zero-model floor/random run doesn't class-init the OpenRouter bridge
        // just to report an endpoint it never contacted.
        String baseUrl = anyOpenRouter || anyDecisions ? OpenRouterBridge.baseUrl() : "n/a";

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
                // Caching changes what a run costs, not what the model sees (the provider's output is the same).
                + ",\"prompt_cache\":" + (OpenRouterBridge.PROMPT_CACHE ? 1 : 0)
                + ",\"plan_a\":\"" + jsonEscape(lastPlanA) + "\""
                + ",\"plan_b\":\"" + jsonEscape(lastPlanB) + "\""
                + ",\"smart_budget\":" + PlayerControllerLLM.smartBudget()
                + ",\"fast_budget\":" + PlayerControllerLLM.fastBudget()
                + ",\"claude_deadline_ms\":" + ClaudeCliBridge.deadlineMs()
                + ",\"openrouter_timeout_ms\":\"" + (OpenRouterBridge.TIMEOUT_MS_ENV == null ? ""
                        : String.valueOf(OpenRouterBridge.TIMEOUT_MS_ENV)) + "\""
                + ",\"game_cap_ms\":" + capMs
                // Whether the wall-clock cap stopped it (#250): its winner is then "invalid" and win_condition is whatever
                // the engine held, which no reader can tell from a finished draw.
                + ",\"capped\":" + capped
                + ",\"game_format\":\"" + jsonEscape(PlayerControllerLLM.GAME_FORMAT) + "\""
                + ",\"openrouter_provider_sort\":\"" + (anyOpenRouter ? (OpenRouterBridge.PROVIDER_SORT.isEmpty() ? "default" : OpenRouterBridge.PROVIDER_SORT) : "n/a") + "\""
                // The routing filters are conditions (aggregate.PROVENANCE_FIELDS): at list an open-weight model may
                // find only a 4-bit host. A batch scrubs both and refuses them in --env, so a game names one only when
                // something else set it. The bridge's own reads, so the line and budget() see one value.
                + ",\"openrouter_max_price\":\"" + jsonEscape(OpenRouterBridge.maxPriceEnv == null ? "" : OpenRouterBridge.maxPriceEnv) + "\""
                + ",\"openrouter_quantizations\":\"" + jsonEscape(OpenRouterBridge.quantizationsEnv == null ? "" : OpenRouterBridge.quantizationsEnv) + "\""
                + (gameError == null ? "" : ",\"game_error\":\"" + jsonEscape(gameError) + "\"")
                // Decisions lost to a dead model bridge and circuit-breaker trips, per identity, 0 when none (issue #369)
                + ",\"bridge_down_decisions_a\":" + bridgeDownDecisions(nameA)
                + ",\"bridge_down_decisions_b\":" + bridgeDownDecisions(nameB)
                + ",\"circuit_breaker_trips_a\":" + circuitBreakerTrips(nameA)
                + ",\"circuit_breaker_trips_b\":" + circuitBreakerTrips(nameB)
                // A row of this log failed to write (issue #427): the log may be cut, so every scorer refuses the game.
                + ",\"eval_log_write_failed\":" + EvalLog.writeFailed()
                // The Forge AI profile of each seat, "" for the compiled AiProps defaults (issue #381): a condition.
                + aiProfileStamp()
                + ",\"engine_sha\":\"" + jsonEscape(System.getenv().getOrDefault("ENGINE_SHA", "unknown")) + "\""
                + ",\"overlay_sha\":\"" + jsonEscape(System.getenv().getOrDefault("OVERLAY_SHA", "unknown")) + "\""
                + ",\"deck_sha\":\"" + jsonEscape(System.getenv().getOrDefault("DECK_SHA", "unknown")) + "\""
                + ",\"discipline\":\"" + jsonEscape(System.getenv().getOrDefault("RUN_DISCIPLINE", "unspecified")) + "\""
                // Additive (schema is additive-only): resolved model identity + sampling + seed bank.
                + ",\"served_smart_a\":\"" + jsonEscape(servedModels.getOrDefault(nameA + "/smart", "unknown")) + "\""
                + ",\"served_smart_b\":\"" + jsonEscape(servedModels.getOrDefault(nameB + "/smart", "unknown")) + "\""
                + ",\"served_fast_a\":\"" + jsonEscape(servedModels.getOrDefault(nameA + "/fast", "unknown")) + "\""
                + ",\"served_fast_b\":\"" + jsonEscape(servedModels.getOrDefault(nameB + "/fast", "unknown")) + "\""
                + ",\"openrouter_base_url\":\"" + jsonEscape(baseUrl) + "\""
                + ",\"checkpoint_sha\":\"" + jsonEscape(System.getenv().getOrDefault("CHECKPOINT_SHA", "unknown")) + "\""
                + ",\"model_cfg_sha\":\"" + jsonEscape(modelCfgSha()) + "\""
                + ",\"temperature_a\":\"" + jsonEscape(resolvedTemperature(smartASpec)) + "\""
                + ",\"temperature_b\":\"" + jsonEscape(resolvedTemperature(smartBSpec)) + "\""
                + ",\"memory_tools\":" + PlayerControllerLLM.MEMORY_TOOLS
                + ",\"memory_tools_version\":\"" + ContextMemory.VERSION + "\""
                // Native function calling rides the memory-tool loop, so it is in effect only with it.
                // True when ANY tier of ANY model-driven seat got its tools natively; the seats line
                // says exactly which, and the downgraded line which of those lost it mid-game.
                + ",\"native_tools\":" + !nativeSeats.isEmpty()
                + ",\"native_tools_seats\":\"" + jsonEscape(nativeSeats) + "\""
                + ",\"native_tools_downgraded\":\"" + jsonEscape(String.join(",", new java.util.TreeSet<>(NativeTools.DOWNGRADED))) + "\""
                + ",\"native_tools_version\":\"" + NativeTools.VERSION + "\""
                // How a decision is answered: a call to `answer` (AnswerTool) or a text tag line. True when
                // ANY tier of ANY model-driven seat answered through the tool; never pooled with text games.
                + ",\"answer_tool\":" + !answerSeats.isEmpty()
                + ",\"answer_tool_seats\":\"" + jsonEscape(answerSeats) + "\""
                + ",\"answer_tool_version\":\"" + AnswerTool.protocolVersion() + "\""
                + ",\"one_tier\":" + PlayerControllerLLM.ONE_TIER
                + ",\"no_coaching\":" + PlayerControllerLLM.NO_COACHING
                + ",\"one_chat\":" + ChatSessionBridge.ON
                + ",\"one_chat_version\":\"" + ChatSessionBridge.stamp() + "\""
                + ",\"turn_chat\":" + (ChatSessionBridge.TURN && !ChatSessionBridge.ON)
                + ",\"turn_chat_version\":\"" + ChatSessionBridge.TURN_VERSION + "\""
                // The questions a jev: seat is asked, by DecisionsBridge: per identity, and only for a jev: one, so
                // its opponent's condition and every other seat's line are unchanged.
                + (jevA ? ",\"decisions_bridge_version_a\":\"" + DecisionsBridge.VERSION + "\"" : "")
                + (jevB ? ",\"decisions_bridge_version_b\":\"" + DecisionsBridge.VERSION + "\"" : "")
                // A claude-cli: seat's bridge (ClaudeCliBridge) and thinking budget, per identity and only for such a
                // seat, as above; and the game's development-lane label (owner, 2026-10-24: never a benchmark score).
                + (cliA ? ",\"claude_cli_bridge_version_a\":\"" + ClaudeCliBridge.VERSION + "\""
                        + ",\"claude_cli_thinking_tokens_a\":\"" + ClaudeCliBridge.thinkingStamp() + "\"" : "")
                + (cliB ? ",\"claude_cli_bridge_version_b\":\"" + ClaudeCliBridge.VERSION + "\""
                        + ",\"claude_cli_thinking_tokens_b\":\"" + ClaudeCliBridge.thinkingStamp() + "\"" : "")
                + (devLane(System.getenv().getOrDefault("CONTROLLER_A", "llm"), smartASpec, fastASpec)
                        || devLane(System.getenv().getOrDefault("CONTROLLER_B", "llm"), smartBSpec, fastBSpec)
                        ? ",\"dev_lane\":\"" + ClaudeCliBridge.DEV_LANE + "\"" : "")
                + ",\"harness_epoch\":\"" + HARNESS_EPOCH + "\""
                + ",\"lookahead\":" + PlayerControllerLLM.lookaheadEnabled()
                + ",\"lookahead_version\":\"" + PlayerLookahead.VERSION + "\""
                // Opt-in switches that change how the seat plays (issue #117). PLAN_EXECUTE makes the
                // harness play steps of the model's own stated plan; TIER_FAST_TYPES moves decision
                // types off the reasoning tier. Both stamp what is on, so a batch cannot pool an
                // on-game with an off-game, and a legacy line that carries neither reads MISSING.
                + ",\"plan_execute\":" + PlayerControllerLLM.PLAN_EXECUTE
                + ",\"propose_actions\":" + AnswerTool.proposeActions()
                + ",\"propose_terse\":" + AnswerTool.proposeTerse()
                + ",\"propose_notebook\":" + AnswerTool.proposeNotebook()
                + ",\"propose_audit\":" + PlayerControllerLLM.PROPOSE_AUDIT
                + ",\"shadow_ai\":" + PlayerControllerLLM.SHADOW
                + ",\"tier_fast_types\":\"" + jsonEscape(PlayerControllerLLM.tierFastTypesLabel()) + "\""
                // Wired, not asked for: RULES_TOOL=1 without MEMORY_TOOLS=1 offers nothing (pr-review of PR #272).
                + ",\"rules_tool\":" + PlayerControllerLLM.rulesToolWired(PlayerControllerLLM.MEMORY_TOOLS, PlayerControllerLLM.RULES_TOOL)
                + ",\"rules_tool_version\":\"" + RulesTool.VERSION + "\""
                + ",\"rules_effective\":\"" + jsonEscape(PlayerControllerLLM.rulesEffective()) + "\""
                + ",\"seed_bank\":\"" + jsonEscape(System.getenv().getOrDefault("SEED_BANK", "unregistered")) + "\""
                // Context shape: WHAT THE MODEL COULD SEE. Two runs on different profiles are different
                // measurements and must never be pooled; before this field there was no way to tell.
                + ",\"context_profile\":\"" + jsonEscape(PlayerControllerLLM.CTX.name) + "\""
                + ",\"context_sha\":\"" + jsonEscape(PlayerControllerLLM.CTX.sha()) + "\""
                // The faithful state's own version (eval/versions.py lists every stamp; this one was only in the log).
                + ",\"observation_version\":\"" + jsonEscape(SeatObservation.version()) + "\""
                // Reasoning effort is part of the player's identity, like temperature: the same
                // model at low and at max is two different players and must never pool.
                + ",\"claude_effort_smart\":\"" + jsonEscape(ClaudeCliBridge.effortLabel(false)) + "\""
                + ",\"claude_effort_fast\":\"" + jsonEscape(ClaudeCliBridge.effortLabel(true)) + "\""
                + "}");
    }

    public static void main(String[] args) {
        // Fail at LAUNCH on a bad CONTEXT_PROFILE. PlayerControllerLLM.CTX is a static initializer,
        // so in a floor/random-only game the class may not load until the summary is written at
        // exit — where the surrounding catch(Exception) cannot catch an ExceptionInInitializerError
        // and an automated driver waiting on the exit line would hang instead of seeing the error.
        ContextProfile.fromEnv();
        GuiBase.setInterface(new GuiDesktop());
        Singletons.initializeOnce(true);
        Singletons.getControl().initialize();
        new Thread(() -> {
            try { Thread.sleep(3500); } catch (InterruptedException ignored) {}
            SwingUtilities.invokeLater(LlmSpikeGui::startGuiMatch);
            // Opt-in autonomous window recording (no macOS Screen-Recording permission needed).
            String rec = System.getenv("RECORD");
            if (rec != null && !rec.trim().isEmpty() && !"0".equals(rec.trim())) {
                try { Thread.sleep(5000); } catch (InterruptedException ignored) {} // let the match window render
                WindowRecorder recorder = WindowRecorder.startFromEnv();
                if (recorder != null) Runtime.getRuntime().addShutdownHook(new Thread(recorder::stop, "recorder-stop"));
            }
        }, "llm-spike-launcher").start();
    }

    private static void startGuiMatch() {
        System.out.println("[LLM-SEAM] building the GUI match...");
        final long seed = applySeed();
        EvalLog.init(seed);
        List<RegisteredPlayer> players = buildPlayers(true);
        // MATCH_GAMES sets games-per-match (default 3 = watch/demo best-of-3; tournament sets 1 for bo1).
        int gpm = 3;
        try { String g = System.getenv("MATCH_GAMES"); if (g != null && !g.trim().isEmpty()) gpm = Math.max(1, Integer.parseInt(g.trim())); }
        catch (Exception ignore) {}
        final boolean bo1 = gpm == 1;
        // play-vs-llm.sh: a person's seat gets Forge's match screen to play from.
        RegisteredPlayer human = humanSeat(players);
        GameRules rules = guiRules(gpm, human != null);
        final HostedMatch hosted = GuiBase.getInterface().hostMatch();
        hosted.startMatch(rules, EnumSet.of(GameType.Constructed), players, human,
                human == null ? null : GuiBase.getInterface().getNewGuiGame());
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
