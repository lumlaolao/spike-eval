# Player harness: direction and implementation record

The goal is a trustworthy MTG-playing evaluation environment. The agent must be
able to understand the current position, distinguish individual objects, remember
what it legally observed, retrieve evidence, and maintain its own plans and
hypotheses. A poor interface must not masquerade as poor play. Assistance from the
harness must be explicit and measurable.

This is the working summary of the user's September 2026 request. Training and
leaderboard expansion come after observation and action fidelity.

## Recovery before development

The saved overlay was older than the adjacent `/Users/lumlumlam/code/forge`
checkout. On 2026-09-06, recovered all 12 Java source/test files in its `forge.spike`
package into the overlay. `recovery-manifest.json` records the original hashes and
previous overlay hashes. The adjacent checkout was left untouched. Subsequent
changes are made only in the overlay.

Recovered work includes `ContextProfile`, `GameHistory`, visible battlefield IDs
and duplicate-label handling, replay rollouts, puzzle support, HTTP deadlines,
and associated tests. These are inherited implementations, not newly completed
features. The old `HANDOFF.md` rollout-stub statement is superseded by this recovery.
Compilation and tests do not certify their strategic correctness or fairness.

## Required information contract

1. **Current observation:** active player, priority holder, phase/step, life and
   other player counters, resources, legal choices, all visible zones, complete
   stack with targets, combat assignments, attachments, continuous effects and
   pending mandatory choices. Missing/truncated/unavailable fields must be explicit.
2. **Object identity:** distinguish same-name copies; tie action candidates to the
   visible objects; distinguish physical-card identity from a new rules object
   after a zone change. Do not track identities through a hidden shuffle. IDs must
   not encode hidden deck order. Public face-down characteristics must not reveal
   the underlying face. Remembered identity is historical evidence, not permission
   to inspect a currently hidden engine object.
3. **History:** append-only, seat-scoped observations with source IDs and turn/
   decision attribution. Capture events before summary truncation. Preserve legal
   private reveals only for the recipient. Record action proposals separately from
   accepted actions, resolutions, cancellations and fallbacks.
4. **Memory:** current facts, verbatim recent evidence, lossy summaries with source
   ranges, and an exact searchable archive. Summarization must never destroy the
   underlying evidence. Preserve memory across backend resets and tier changes;
   reset it between games. Eventually use a bounded context budget independent of
   game length, with a disk-backed archive for long games.
5. **Strategy:** agent-authored plans and archetype hypotheses cite observations
   and remain explicitly uncertain. Later evidence can contradict them. A known
   archetype library, if provided, is a versioned assistance condition shared by
   comparable agents, not hidden coaching or access to the opponent's decklist.
6. **Tools:** typed, bounded operations with recorded requests/results, budgets,
   timing and failures. Start with observation/history/card lookup. Every supported
   backend must receive the same tools and information. Report agent-plus-tools
   performance separately from unassisted play.
7. **Fair simulation:** distinguish rules preview, sampled imperfect-information
   rollout and omniscient offline analysis. Agent-facing rollouts may use only its
   information set and disclosed assumptions about unknown cards. They must not
   read the actual hidden hand/library order, consume live RNG, mutate live state,
   or secretly use Forge's strategic policy. Report rollout policy, sample count,
   uncertainty, cost and unsupported decisions. Never expose the recovered offline
   fork mechanism as a player tool merely because it can simulate a full game.
8. **Action ownership:** audit all controller hooks, automatic choices, omitted
   legal alternatives and AI delegation. `ai=0` only covers instrumented paths;
   it does not certify that an agent made every strategic choice. Keep decisions,
   parsing failures, deterministic fallbacks and engine rejection distinguishable.

## Implemented in this continuation

`MEMORY_TOOLS=1` enables `ContextMemory` for LLM seats. Default is off. It is shared
by that seat's smart/fast calls, but never by opponents. It archives the existing
filtered narration stream before `GameHistory` compaction, every model observation,
reveal facts passed to `remember`, and agent-authored notebook entries. The archive
is currently in JVM memory, not persistent storage; process exit loses it. JSONL
call/tool traces support analysis but are not an agent-facing archive endpoint.

The prompt includes a brief with the latest four reveals and four notebook entries.
Omissions are stated; the archive retains older entries. `M<n>` identifies a seat's
archive entry. Recovered `E<n>` references identify entries in the filtered
narration stream, not every event in the engine's full log.

The model may emit exactly one command instead of its normal action:

```text
MEMORY SEARCH 0 Force of Will
MEMORY READ 12 0
MEMORY NOTE 12 Opponent may be holding a counter; reconsider after new evidence.
```

Search is literal and case-insensitive, ten results per page; its offset counts
matching entries. READ takes a numeric M-id and character offset, returning at most
2,000 characters and a next cursor. Notes are limited to 500 characters and must
cite an existing observation or reveal; citations do not verify their reasoning.
Old notes remain searchable as explicitly labelled hypotheses.

There are at most two executed tools per top-level ask and 64 per seat/game.
Existing parse retries may start another ask, but cannot reset the game budget.
Every additional model round counts against its smart/fast call budget. A request
beyond the limit returns no action and enters the existing fallback/retry handling;
it must never be misparsed as an action. Bridge transport retries retain their
existing behavior.

`context_call` JSONL rows contain actual augmented prompts, raw replies, seat,
tier, call number, elapsed time and backend-reported token counts. `context_tool`
rows contain commands and exact results, including denied calls. Game rows include
`memory_tools` and `memory_tools_version`. Aggregation partitions experiments on
these, on `context_profile`/`context_sha`, on the tool stamps (`native_tools*`,
`rules_*`, `lookahead*`) and on the two opt-in play switches (`plan_execute`,
`tier_fast_types`): every game-line key is either part of the experiment key
or an outcome (`eval/test_experiments.py` reads the line's writer to check this).
Separate run directories are still the clearest way to keep conditions apart.

**Cost limitation:** context-call token fields describe the last transport attempt
reported by the existing bridge. They are not complete billing for internal
transport/truncation retries. Do not sum context-call tokens with decision-row
tokens, which overlap. Tool deliberation also passes through existing retry-latency
accounting; a dedicated total-compute report is still needed before tool-mode scores
can be cited as cost-comparable.

**Visibility limitation:** this layer has no direct hidden-state access, but accepts
the existing observation renderer and narrated log as input. Their visibility
correctness is not yet certified. Remembered reveals can already have been truncated
by callers before `remember`. Retrieval cannot recover text never captured. The
archive does not independently capture every engine event or every private reveal.

## Implemented 2026-09-07 (continuation of work order A, plus gates G7/G10)

**Viewer-aware observation profile `CONTEXT_PROFILE=v4-faithful`** (`SeatObservation.java`;
work order A's design, completed after that session ran out of budget mid-wiring). Every
prompt under this profile is rendered from what the seat may legally see: seat-local
`@N` handles assigned on first sight (never engine ids, never deck order), a legend on
every observation, own hand listed, opponent hand/library as counts only, face-down
identities gated by the engine's own look permission, zones rendered in full with explicit
omission notices, stack with sources/controllers/targets, combat assignments, attachments,
counters and damage, and a `NOT VISIBLE` line. A zone change makes a new object and a new
handle; `(was @k)` is shown only when the whole path ran through zones the viewer could see,
so nothing is linked across a hidden shuffle. Narration is captured per seat with engine
ids mapped to handles or dropped and every hidden face-down name redacted. Menu options
(play, targets, discards, sacrifice, attackers, blockers, damage order, reveal, tuck, modes)
are rendered by the same layer, so an option and the state name the same object; BLOCKS
replies may cite `@N` and are matched by handle first. Both `reveal` controller hooks are
overridden: reveals delivered to the seat become `[private reveal to you]` stream entries
(E-id, turn, searchable, archived); engine-presented hidden candidates are recorded the
same way. `INSPECT @N` / `INSPECT <card name>` is a third memory tool sharing the MEMORY
budget: current public characteristics, an explicit HISTORICAL answer once the object is
gone or hidden, or public card-database text labelled as such. Legacy profiles
(`v1-compat`, `v2-memory`, `v3-compact`) are untouched byte-for-byte; `v4-faithful` has its
own `context.v3|...|observation=observation-faithful-v9` identity and sha.

`SeatObservationTest` drives the live implementation against real engine games: two
worlds differing only in the opponent's hand and library produce identical observations,
narration and INSPECT results; own-hand vs opponent-count; duplicate copies get distinct
stable handles; battlefield to graveyard links the predecessor while a path through the
library does not; a control change keeps the handle and moves the object to the new
controller's board; an opponent's morph is `face-down Creature` to us and named to its
controller, in labels, state, INSPECT and narration; reveals through the controller hook
are seat-local; menu labels and state share handles while the legacy profile still uses
the static labels; INSPECT is budgeted through ContextMemory.

**Ownership ledger (G7 first stage):** `controller-ownership.tsv` + `ControllerOwnershipTest`
+ `OWNERSHIP.md`. 130 hooks classified; after numeric/binary/confirmation routing and the
2026-10-12 legal-list, card-subset, unless-cost, optional-cost and cost-decision routing **3**
silent Forge-AI strategic/payment decisions reachable in Legacy play remain (down from the
original 18) and are enumerated and pinned as a maximum; the 3 are no-seam engine mechanics
(established by reading `forge`). The AI cheat-shuffle hook
the LLM seat inherits is pinned inert on both launch paths.

**Acceptance suite (G10):** `bash eval/harness_acceptance.sh` (`ACCEPTANCE.md`): isolated
compile, all Java and Python tests with a declared-vs-run count gate, pinned zero-model
canaries with a class-load proof that the compiled build ran, and two loopback fake-model
games (`eval/fake_model_server.py`): legacy profile with memory tools, an injected timeout
and its retry, no key on the wire; and the faithful profile with prompt-content assertions.

Still not done from work order A: menu labels trust the engine's candidate set (`presented`
waives the zone rule for a card the engine hands to a chooser; the reveal is recorded, not
gated, so an engine effect that offers hidden cards without reveal semantics would be
shown; verified legitimate for discard/scry/surveil, not exhaustively for every
`chooseCardsForEffect`/`orderMoveToZoneList` caller); the `notifyOfValue` information channel; a structured
(non-text) observation schema; capture of engine events the log formatter never writes;
the observer's zone-change subscription is read-only but its handle registry lives in JVM
memory only.

## Additional defects fixed during validation

**2026-10-12 — narration redaction (G1/G3).** The faithful observer no longer builds a
game-wide dictionary of hidden face-down card names and string-replaces them out of
narration. Redaction is anchored to the object an event names via the engine's
`Name (id)` format: a hidden reference loses its name and its id, a visible reference
maps to a handle, and a public card that shares a hidden card's name is never erased.
`SeatObservation.VERSION` moved to `observation-faithful-v2` (faithful `context_sha`
`7896dbb44d9f` -> `8b5409f50ab1`); two adversarial tests added. `render()`'s
`CAST THIS GAME`/`LANDS PLAYED` now also come from the redacted seat stream instead of
the legacy raw-log `castHistory` (byte-identical on public data). See
`OBSERVATION-REVIEW.md` for the repair, evidence and remaining limits.

**2026-10-13 — known hidden locations (`observation-faithful-v3`).** The faithful observer now
keeps a **`KNOWN IN HIDDEN ZONES`** ledger: a card the seat *saw* move into an opponent's hidden
zone (a public `battlefield/graveyard/exile → hand/library` move, e.g. "return target creature to
its owner's hand") or that the engine *presented* from a hidden zone (a revealed hand / searched
library) is recorded as seat **knowledge**, and the fact is dropped the instant the card is seen
leaving that zone (a cast, a discard). This is the chain the earlier review flagged as missing —
*unsummon their creature, then Thoughtseize it away* — and it is deliberately a knowledge claim,
never engine truth: it is derived only from what the viewer could see, is capped (12 facts), and
says so in its own header. `SeatObservation.VERSION` moved to `observation-faithful-v3`; two tests
cover it (`seenMovesIntoHiddenZonesAreKnownThenInvalidated`,
`presentedCardsFromHiddenZonesAreRecordedAsKnown`).

**2026-10-13 — explicit "spent this turn" state (`observation-faithful-v4`).** The faithful renderer
now emits a `SPENT THIS TURN:` line — land drop AVAILABLE/USED (with the count and the cap), spells
cast, and attackers declared — as **explicit facts**. A model cannot attend to gaps, so legality must
be stated rather than inferred from a missing menu entry; this targets the "played a second land"
class directly. It is rendered last, beside the decision, because that is where attention sits.
`SeatObservation.VERSION` moved to `observation-faithful-v4`; one test covers it
(`spentThisTurnIsExplicitAndFlipsWhenALandIsPlayed`).

**2026-10-13 — the plan survives the turn boundary (`observation-faithful-v5`).** The seat's own
one-line plan used to be dropped the moment the turn passed (`planTurn == turn && isPlayerTurn`), so
*"hold up UU and counter their payoff"* — a plan that only means anything on the **opponent's** turn —
was erased exactly when it mattered. Now the plan is rendered for as long as it exists, labelled with
the turn it was stated, and flagged as possibly stale once the turn has moved on
(`YOUR PLAN (you stated this at T5 — a previous turn; the board may have changed — re-state it or
consciously continue): …`). The plan is also **asked for once per turn on either player's turn**
(previously only at the model's first main-phase decision), piggybacked on an existing decision so it
costs no extra model call. One shared `planText` helper is used by both renderers so they cannot drift;
one test covers the boundary (`thePlanSurvivesTheTurnBoundaryAndIsMarkedStale`).

**2026-10-13 — engine facts (`observation-faithful-v6`).** The faithful renderer now prints a
`FACTS (engine estimates, not truth):` line built from Forge's OWN arithmetic: each side's
next-combat clock (`CombatUtil.canAttackNextTurn` + `ComputerUtilCombat.sumDamageIfUnblocked`, so it
is meaningful at every priority window, not only inside declare-attackers) and the opponent's
instant-speed mana (`ComputerUtilMana.getAvailableManaEstimate`). It uses only public primitives and
deliberately never consults `GameStateEvaluator`, which is perfect-information and non-adversarial.
One test covers it (`engineFactsStateBothClocksAndTheirInstantSpeedMana`). `SeatObservation.VERSION`
moved to `observation-faithful-v6`.

**2026-10-13 — recurring threats + event-id drill-down (`observation-faithful-v7`).** Two smaller
close-outs. (a) The opponent block now prints `RECURRING (cast 2+ times): <Name> xN, ...` — a name
they have played more than once, which a human notices ("they keep casting Entomb") and which the
model otherwise re-derives from a long log; it is the deterministic half of opponent modelling.
(b) The MEMORY tool instructions now explain that the compacted `GAME HISTORY` cites canonical event
ids as `[E<n>]` and that `MEMORY SEARCH 0 [E<n>]` fetches that event's archived text — a digest the
model can see but cannot drill into is only half a memory. Tests:
`repeatedOpponentPlayIsSurfacedAsRecurring`, `toolInstructionsExplainHowToDrillIntoACitedEvent`.
`SeatObservation.VERSION` moved to `observation-faithful-v7`.

**2026-10-13 — object history (`observation-faithful-v8`).** P1b: each observed object now carries a
bounded zone-transition **trail** (`T3: battlefield -> graveyard`), inherited from the predecessor when
a move is publicly linked, so an object keeps ONE history even though Forge builds a new `Card` (and
the seat a new handle) on every move. `INSPECT @N` returns it (`history: ...`), which is the
object-level half of continuity: without it the model could only see that a *new* thing appeared. One
test covers it (`inspectShowsAnObjectsZoneTransitionHistory`).

**2026-10-13 — model-authored opponent profile (`observation-faithful-v9`).** P6b: the seat can write
its own one-line read of the opponent (`PROFILE: ...`), which is re-injected every decision as
`YOUR OPPONENT PROFILE (you wrote this; unverified — revise it as you learn): ...`. It is refreshed
only when empty or >=5 turns old, and it RIDES the same piggybacked ask as the turn plan — so it costs
no extra model call. It is a hypothesis, labelled as such, never an engine fact (the deterministic
half is the `RECURRING` line). Test:
`opponentProfileIsShownOnlyWhenTheModelWroteOneAndIsLabelledUnverified`.

**2026-10-13 — options say what they are (`observation-faithful-v11`).** Four changes to what the
seat is shown, from `ARCHITECTURE-REVIEW.md` problems 2, 4 and 9:

- **Option labels carry the option's own cost and zone.** The legacy label printed the host card's
  mana cost on every option, so an ability paid with a creature read `CAST Goblin Bombardment 1{R}`
  and a graveyard flashback paid with a creature read `CAST Cabal Therapy {B}`. Under the faithful
  profile an ability is `ACTIVATE <ref> [<cost>: <effect>]` and a spell cast from outside the hand
  or at an alternative cost is `CAST <ref> from your GRAVEYARD (Flashback) — cost: Sacrifice a
  creature [...]`. A hand spell at its printed cost is unchanged. `CARDNAME` placeholders are
  replaced with the card's name. A hand spell still shows the card's printed cost, so a split or
  adventure face is not yet exact. Legacy-profile prompts, labels and tiers are byte-identical (the
  new `[ROUTER]` log lines appear under every profile).
- **An optional trigger is asked with its text**, on the tier the routing table gives
  `confirm-trigger` (smart unless listed in `TIER_FAST_TYPES`; fast if the smart budget is spent), and
  every optional yes/no is logged
  (`[ROUTER] confirm-trigger [Ajani, Nacatl Pariah]: YES`). It used to read "Accept this optional
  triggered ability? Ability: Ajani, Nacatl Pariah 1{W}" on the no-reasoning tier, unlogged.
- **Reveals carry their age** (`T3: (8 turns ago) ...`) as a fact. The harness does not call a reveal
  stale; it cannot know.
- **`LINES:`** — the seat is asked (at most twice per game, riding an existing smart call) to state
  how its own cards combine; the answer is shown back as `YOUR CARD INTERACTIONS (you wrote this from
  your own decklist; unverified)`. The ask shows a format only: a test fails if it names any card
  from `eval/decks/`. This is an audit field first; whether it changes play is unmeasured.

Tests: `LineFindingProbeTest` (labels, trigger text, LINES ask), `SeatObservationTest`
(`oldRevealsAreStampedWithTheirAge`, `deckInteractionsAreShownOnlyAfterTheModelWritesThem`).

**2026-10-13 — the seat reads the opponent's cards (`observation-faithful-v12`).** An opponent's
card was a name, a P/T and a keyword list (`Orcish Bowmasters 1/1 [flash]`); its rules text was
available only through `INSPECT`, which was called zero times in the four recorded games checked. The
observation now prints `OPPONENT CARDS YOU HAVE SEEN (printed rules text from the card database; N
cards)`: one line per distinct opponent card the seat has identified — in a public zone, on the
stack, or known to be in a hidden zone — with cost, type and oracle text. A card stays listed after
it leaves (its text does not stop being known). Not listed: tokens, basic lands, cards in zones the
seat cannot see, and cards already printed in the seat's own deck text. Capped at 30 cards with an
explicit omission notice. It is the printed card and nothing else: no interaction is stated.
Tests: `opponentCardsTheSeatHasSeenAreShownWithTheirRulesText`,
`opponentCardTextPersistsIsNotDuplicatedAndIgnoresHiddenZones` (the second also re-asserts that two
worlds differing only in the opponent's hidden cards render identically).

**2026-10-14 — what follows a choice says what the choice was (`observation-faithful-v13`).**
Three asks lost the context of the decision they follow (runs/loop/g08-g10, `GAME-LOOP-LOG.md`):
a card-name ask (Cabal Therapy) now states the spell's targets, so naming for a self-targeted
Therapy is not read as naming for the opponent's hand; a top-of-library order (Ponder, Brainstorm)
is asked from the top down and reversed before the engine places it, since the engine puts the
LAST card returned on top; and the "may shuffle" that follows such an order states the order it
would undo. Legacy profiles keep their bytes. Tests: `aNameAskStatesWhoTheSpellTargets`,
`aLibraryOrderIsAskedTopDownAndTheFirstPickEndsOnTop`,
`theShuffleAskAfterAnOrderSaysWhatTheShuffleWouldUndo`.

**2026-10-14 — a Dig pile that goes on top is ordered top-down too (`observation-faithful-v14`).**
The top-down order covered Ponder-style rearranges and ChangeZone moves but not `Dig` or
`DigUntil` (issue #107): Cruel Fate's "put the rest on top in any order" was still asked
"first = goes in/on top first", so the card named first ended lowest. A Dig or DigUntil order is now
asked top-down when every library pile the ask could be for lands at position 0. Dig asks for its
chosen and its rest pile with one SpellAbility, so a Dig with two library piles keeps the literal
wording (even two top piles: the rest pile lands above the chosen one).
Legacy profiles keep their bytes. Tests: `aDigRestPileToTheTopIsAskedTopDownAndTheFirstPickEndsOnTop`,
`aDigOrderIsAskedTopDownOnlyWhenEveryLibraryPileItCouldBeLandsOnTop`.

**2026-10-15 — asks carry what they turn on (`observation-faithful-v15`).** From the per-decision
prompt audit of runs/loop g01-g07 (`HANDOFF-PERFECT-HARNESS.md`). Faithful profile only; legacy
profiles keep their bytes:

- **Options say whose object they are**: `[Battlefield, yours]` / `[Battlefield, opponent's]`, and a
  player option reads `you (Seat)` / `opponent (Rival)`. g07 untapped the opponent's creature from a
  menu of eight, six its own, none marked.
- **No 90-character cut** on menu options, private reveals, stack descriptions and INSPECT rules text
  (`LlmSpikeGui.flat`). Sneak Attack's INSPECT text had lost its sacrifice clause; a ten-card reveal
  kept three names.
- **The fast tier is not told "NO reasoning"** while every decision text says "Reason briefly"
  (all 268 fast asks in g01-g07 carried both). It may now write the same two sentences the smart tier
  does; its API reasoning setting is unchanged.
- **A number, yes/no, pick, order, discard or name ask states its source's rules text** and who the
  spell targets, not the card's name alone ("Choose a number [Galvanic Discharge]").
- **An "unless" payment that makes the effect happen is asked as that** (Chain Lightning's copy was
  asked "pay to prevent"), with whether the seat can pay. A card-type choice is asked as one, not
  "Choose a Card to name". The divided-damage ask ends with its own `ALLOC:` format.

Tests: `PromptFidelityTest` (whose object, fast-tier framing, whole INSPECT text, number source text).

**2026-10-15 — the steps of one action share its intent, and a play can be cancelled
(`observation-faithful-v16`).** The engine asks for a play, then its target, cost, number or order
as separate questions, often on different tiers with no shared conversation (each OpenRouter ask is
stateless). runs/loop/g07: the smart tier activated Wirewood Symbiote "to untap Reclamation Sage so
it can block"; the fast tier, asked for the target, called the activation pointless and untapped the
opponent's creature. g08 and g10 were the same shape (a sacrifice "I didn't choose", a shuffle that
undid the Ponder order). Under the faithful profile:

- every ask after a play (not the play menu itself) carries `THE PLAY YOU CHOSE MOST RECENTLY (T<n>)`
  with the option and what the model wrote when it chose it, for the rest of that turn. Only the
  model's own words carry over, never an older state: the snapshot in the same prompt is current and
  says so. This is ROOT-INTERFACES R4 (issue #108) at the root rather than per ask;
- a target ask asked before the play is committed offers `CANCEL: do not take this action`; the play
  menu is then asked again with a note saying what was cancelled (at most twice per window).

Tests: `PromptFidelityTest.theTargetAskCarriesTheReasonForThePlay`, `cancellingAtTheTargetStepAsksTheMenuAgain`.

**2026-10-15 — X, modes, then targets, all the seat's (`observation-faithful-v17`).** The AI cast
path never announces X (`PlaySpellAbility.announceValuesLikeX` is the human path), so the engine paid
whatever X the ability held: Green Sun's Zenith resolved at X=1..3 in recorded games with no question.
The play loop now asks X (from XMin up to the most the engine says it can pay), then a modal spell's
modes, then targets, the order of CR 601.2b-c. Modes used to be chosen at cast, after targeting, so a
targeted mode never got a target (Kozilek's Command "failed to target" 26 times in recorded logs).
`announceRequirements` (Mind's Aglow, life bids) is the model's too (ledger row `model`). Option
labels are no longer cut at 160 characters. Tests: `xIsTheSeatsChoiceUpToWhatItCanPay`,
`everyChosenModeOfAModalSpellIsTargeted`.

### Tool protocol: `evidence-tools-v2` and native function calling (2026-10-13)

`eval/tool_audit.py` over the three controlled arms (`runs/lines/ctl`, 48 games, 2,994 model calls)
counts 43 tool requests. **30 were refused** with `ERROR: use exactly one MEMORY/INSPECT/RULES
command line`: the reply was not the bare command and nothing else. In 25 of the 30 it held other
text (a sentence of reasoning first, usually); in 5 it was a valid command with only blank lines
around it. The smart tier is instructed to write "at most two short sentences of reasoning, then a
final line", so the harness asked for the sentence and then rejected the request because of it; a refusal also spent one of the seat's two
tools. Twice the model wrote its own function-call markup into the text reply.

Two changes, both in the tool layer (the observation is unchanged, `observation-faithful-v12`):

- **`evidence-tools-v2`** (the text grammar; now the fallback).
  `ContextMemory.toolLine` executes the first command line wherever it sits in the reply. A search
  with no literal match falls back to entries holding every word of the query and says so
  (`Rival's hand` did not occur in `saw Rival hand [...]`). A reply that holds a tool line is still
  never handed to the `CHOICE` parser. Stamped as `memory_tools_version`; v1 and v2 games are
  different conditions.
- **Native function calling** (`NativeTools.java`; **the standard**, on by default wherever the
  backend supports it, `NATIVE_TOOLS=0` to opt out). The same tools go out as function definitions
  and come back as `tool_calls`. `ContextMemory.askNative` turns each call into the canonical
  command line and runs it through the same `execute`, under the same budget (2 per decision, 64
  per game), in at most three model rounds per decision like the text loop. A malformed call is
  answered with an error and costs nothing. `simulate` is offered at play decisions under
  `LOOKAHEAD=1`: once per decision, one of that decision's two calls, not counted against the 64.
  `rules_lookup` takes an `action` (search / read / section / term / toc) that maps onto the
  `RULES` grammar. A reply that is a text-grammar command instead of a call is refused, not parsed.
  If a provider refuses function calling (a 4xx naming tools), that bridge drops to the text
  grammar for the rest of the game and the game line says so (`native_tools_downgraded`). The text grammar is not sent. Stamped
  `native_tools` / `native_tools_seats` (which tiers of which seats) / `native_tools_version` on
  the game line; `context_call` rows carry
  `protocol: native` and, after the first round, the message list. Only `openrouter:` bridges
  support it (`LLMBridge.supportsNativeTools`); a Claude CLI seat keeps the text grammar.
  `run_batch.py --memory-tools` means native on `openrouter:` seats; `--text-tools` is the opt-out.

Measured with the tool-use suite (`eval/tool_use_suite.py`, invented cards so the deciding fact
exists only behind a tool; deepseek-v4.1-flash, 3 trials, `runs/tooluse/deepseek-v4`). **Reach**
means the deciding fact actually came back from a tool, not merely that a tool was called:

| protocol | tier | reach | answered correctly | refused for form / attempts | restraint |
|---|---|---|---|---|---|
| text v1 (strict) | smart | 4/30 (13%) | 11/30 (37%) | 33 / 48 | 27/27 |
| text v2 (lenient) | smart | 12/30 (40%) | 17/30 (57%) | 0 / 32 | 27/27 |
| native | smart | 26/30 (87%) | 27/30 (90%) | 1 / 75 | 27/27 |
| text v1 (strict) | fast | 7/30 (23%) | 10/30 (33%) | 6 / 31 | 26/27 |
| text v2 (lenient) | fast | 9/30 (30%) | 10/30 (33%) | 0 / 37 | 26/27 |
| native | fast | 18/30 (60%) | 18/30 (60%) | 1 / 72 | 23/27 |

Baselines: answering by chance 38%; a never-call policy reaches 0%; an always-call policy has 0%
restraint; with the fact printed on screen the model answers 100% (smart) and 83% (fast). On the
smart tier every ask whose fact came back was answered correctly (4 of 4, 12 of 12, 26 of 26), and
asks whose fact did not come back were answered correctly 13 times in 48 (below the 38% of
chance), so reach is what decides the answer here. Scenario by scenario, native reached the fact more often than text v1 in 9 of 10
scenarios on the smart tier and never less often (sign test p=0.004); 6 of 10 and never less on the
fast tier (p=0.03). Thirty asks per cell are three trials of ten scenarios, not thirty independent
samples, and runs differ: the previous run of the same suite (`deepseek-v3`, before the search
stopped returning the decision being asked, rescored with `--rescore`) gave 9, 14 and 23 of 30
on the smart tier. The order of the three protocols is the same in both; the size is not.

What it does not show: the two-step scenario (search, then inspect what the search found) was
reached 0/3 by every protocol; the fast tier asked for nothing in 6 of its 12 native asks about an
unknown permanent and got 7 of those 12 wrong; and native calling costs restraint on the fast tier
(23/27). The single native "refused for form" per tier is the model writing its call markup into
the text once calls were forbidden, which is refused rather than parsed.

Tests: `NativeToolsTest` (21, including a real HTTP round trip on loopback and equality of the Java
definitions with `eval/tooluse/tools.json`), `eval/test_tool_use_suite.py`, `eval/test_tool_audit.py`;
acceptance stage `nativetool`.

### Lookahead preview v2 (2026-10-13)

`lookahead-preview-v2`, stamped `lookahead` / `lookahead_version` on the game line (v1 was never
stamped). Three changes, each from what the first arms that could ask for a preview showed
(`runs/lines/ctl/native-lookahead`: 38 requests, none answered):

- **Previews work when a sideboard holds a characteristic-defining card**
  (`PlayerLookahead.UncopiedEffects`; ARCHITECTURE-REVIEW problem 13). Sixteen of the 38 died here.
- **Activated abilities are previewed**, not only spells. Nineteen of the 38 were for one. A land
  drop is previewed since v3 and a mana ability since v12 (its report shows the mana pool). When a cost holds a choice (sacrifice a creature) the
  engine pays it on the copy and the report says so: "The engine chose what to sacrifice for this
  preview; in the real play YOU choose".
- **The report names what happened.** `if you activate Goblin Bombardment [assuming target: Rival]:
  you board 2 -> 1; graveyard 0 -> 1; left the battlefield: Elite Vanguard; Rival life 20 -> 19`.
  v1 gave only life, board count and graveyard count. Added: permanents that left or entered, by
  name (the battlefield is public), and hand and library sizes (sizes of hidden zones are public,
  their contents are never named). If the copy cannot pay for the option the preview says so
  instead of reporting "no change".

- **A line can be previewed.** `simulate` takes `then`: up to two more option numbers played after
  the first, in order, on the same copy. `if you cast Lightning Bolt [assuming target: Rival], then
  cast Lightning Bolt [...]: ... Rival life 20 -> 14`; with mana for only one, `... Rival life 20 ->
  17. After that the engine could NOT pay for or play Lightning Bolt on the copy, so the line stops
  there`. Each step must be an option on the menu now (a card the first play would draw or create
  cannot be named).

Still one preview per decision (lifted in harness-epoch-7: any number, and a line of any length; see
MODEL-LIMITS.md). It is an estimate: libraries are
shuffled on the copy, the opponent's hand is not determinized, and the opponent is not given a turn.
Tests: `LookaheadProbeTest` (10). Loopback Mardu mirror: 104 of 104 previews return a result.

### Lookahead preview v3-v4 (2026-10-13)

Both come from loop games read after the v2 arms, and the doc had not recorded v3.

**v3: `simulate` indexes the decision's own menu, and a land drop may start a line.** The option
numbers the model passes are the numbers it reads off the prompt: the menu is the CAST and ACTIVATE
options followed by the PLAY LAND ones and then PASS. A line may begin with a land
drop, played on the copy. Before v3 only spells and abilities were addressable, so the seat was
refused for passing the numbers it had been shown (runs/loop/g02 T4-5: `option` 3 on a two-spell menu
with two land options after it).

**v4: `target` may be the label the board printed, not only the bare name.** The seat sends what it
reads off the board, and the board prints `Name P/T [tags] @handle` ("@N = your seat's handle for one
object"). `pickTarget` asked only whether the candidate's *name* contained the hint, and a label is
longer than the name, so a label never matched and the pick fell silently to `defaultTarget`.
runs/loop/g05 T5: the seat asked to preview Nomads en-Kor targeting "Cephalid Illusionist 1/1
[untapped] [sick] @75", and the report came back "[assuming target: Nomads en-Kor]: you no change" —
a no-op preview of the ability the whole game turned on; it then spent its one allowed retry on the
same request and was refused ("one preview per decision"). The match now runs both ways (name
contains hint, or hint contains name) and the longest name wins, so a name that is a prefix of
another cannot take the pick.

Tests: `LookaheadProbeTest` (13: `previewCastHonoursATargetHintThatQuotesThePrintedLabel`,
`aLineThatStartsWithALandDropIsPreviewed`, `previewCastHonoursATargetHint`).

Hand-reveal bridge/parse failures now choose the first minimum-sized legal subset,
log an automatic fallback, and never call Forge AI. Empty reveal pools return empty.
The regression uses a 1-to-3-card reveal range to catch accidental over-revealing.
Other controller fallbacks still require the ownership audit below.

Random-arm route logs now say `random baseline (no model call)`. They previously
claimed `asking opus/haiku` despite never consulting a model, producing 58 false
missing-call reports in the random canary. The defect analyzer was not modified.

### Memory across decisions: who compacts what (2026-10-15)

The seat is asked one decision at a time, but a turn is one line of thought: the creature it means
to kill in main phase one is still the point at the block and at the end step. Three layers keep
that line without mixing past and present:

- **Facts are compacted by code, never by the model.** Each ask carries the current state rendered
  fresh, the game history compacted deterministically (`GameHistory` TIERED, every entry citing
  `[E<n>]`), and the full log stays searchable (`memory_search` / `memory_read`). A model-written
  summary of what happened can be wrong and then reads as fact; a rendered one cannot.
- **The model compacts only its own thinking.** The plan (`YOUR PLAN`), the notebook (`memory_note`)
  and the opponent profile are the model's, labelled as its own, unverified.
- **The plan changes whenever the model wants (observation v22).** It is a goal and the steps toward
  it, not a script: when the opponent casts an instant in their main phase or a card is drawn, the
  model writes a new `PLAN: GOAL: ... NEXT: ...` line at that decision, whatever the decision is. The
  plan line says when it was stated, so the model can read what happened since and judge it. A
  changed plan drops the old `STEPS:` cast list. The harness never judges a play against the plan;
  whether a play made sense (the Dark Ritual cast and then passed) is for the blunder judge to read
  afterwards, not for the harness to block.

`ONE_CHAT=1` (`one-chat-v1`, stamped `one_chat`) is the other answer, kept as a **condition**, not
the default: one conversation per seat, earlier questions kept as their DECISION part and the
model's answer, the state only in the newest message (`ChatSessionBridge`). It keeps the steps of
one action in one conversation, at the price of more prompt tokens and the risk the model reads an
old state as current, which is why the default stays a fresh state per ask with the model's own
plan carried forward.

**The seat can say a question is broken** (`report_unclear_decision`, seat-tools v6). If the harness
is right, a model reading a question after its own play can tell when the options do not fit (a
"use the ability?" that does not follow from the spell it cast, a garbled option). It records the
complaint, changes nothing, and the decision is still answered. `match_review.py` lists each one as
`REPORT`; reading them is part of the game loop. Not offered in the tool-use suite (it is not a
lookup), so suite results stay comparable.

**No MCP server for now.** The seat's tools are native function calls inside one process
(`NativeTools`); an MCP server would put a protocol boundary between the controller and the engine
it reads, for the benefit of clients we do not have. It becomes worth it when an outside agent
(Claude Code, another lab's harness) is to play a seat directly; then it wraps the same executor
and the same per-decision budget, and the controller stays the one place a decision is asked.

### Answer tool: `answer-tool-v2` (2026-10-15)

Decisions were answered by a final text line (`CHOICE: n`, `PICKS: …`, `BLOCKS: …`, `KEEP`) scraped from
prose. Four more line formats rode on the play ask (`PLAN:`, `LINES:`, `YIELD:`, the opponent profile).
That grammar was the largest source of harness defects: a model writing `CHOICE` and `PICKS` together, a
lower-case "keep" in prose read as an answer, a cut-off reply losing its last line.

Now every decision is answered by calling `answer` (`AnswerTool.java`), and the plan by `set_plan`.

- **One fixed schema.** v1, written first, built a new `answer` schema from each decision's legal values
  (enums of the option numbers). Tool definitions are the first thing in the prompt-cache prefix, so
  that discarded the cache on every call. And without `strict`, an enum in a schema is not enforced;
  it only repeats the options the decision text lists. v2 has one schema with every field optional, and
  validates each call against the decision it answers.
- **Corrections in the same conversation.** An illegal call (an option off the list, a wrong count, the
  wrong field) is answered with the reason, and the model calls again. A second illegal call ends the
  ask (`answer_via: "lost"`) and the caller takes its deterministic fallback.
- **A constant tool list.** `set_plan` is offered on every ask. A lookahead seat declares `simulate` on
  every ask, and refuses it on a decision with no play menu (`NativeTools.NO_PREVIEW`).
- **No text alternative.** A reply that calls nothing is reminded up to `MAX_NUDGES` (2) times in the same
  conversation, then the decision is lost; its prose is never read as the answer (answer-tool-v7). A text
  format left in a tool ask is a `[TEXT-FORMAT-LEAK]` (`AnswerTool.textFormatLeak`).
- **Rows.** `answer_via` is `tool`, `nudged`, `reask` or `lost` (`text` only on rows recorded before v7). `answer_invalid` counts the corrected
  calls. `raw_reply` holds the answer rendered in the old line format, so existing readers still work.
- **Measure** with `python3 eval/tool_use_suite.py --arms native,native-answer` before games. A
  per-decision arm (`ANSWER_SCHEMA=per-decision`) is kept for a side-by-side measurement of
  illegal-call rate and cost.

## Next implementation gates

| Priority | Work | Evidence required before claiming complete | Status 2026-09-07 |
| --- | --- | --- | --- |
| P0 | Seat-aware visibility audit and structured observation schema | Two worlds differing only in hidden state produce identical observations and tool results; reveal, face-down, exile, shuffle and controller-change fixtures | Text observation done under `v4-faithful` with paired-world, face-down, hidden-path, control-change and reveal tests (`SeatObservationTest`); no structured schema yet; exile fixtures thin |
| P0 | Structured action/outcome event capture | Agent sees intervening actions exactly once or receives explicit loss metadata; proposed actions cannot be mistaken for resolved ones | Seat stream captures the engine log once per entry with redaction; proposals vs resolutions still not separated |
| P0 | Controller ownership and legal-menu audit | Inventory every hook; tests for omitted legal alternatives and silent AI choices; attributable fallback records | Ledger + test done; 13 silent AI decisions remain (numeric/binary/confirmation routed); payment execution, trigger targeting and legal-menu completeness untested |
| P1 | Object registry and object inspection | Same-name copies, blink/new incarnation, control change, tokens, attachments, target and combat references; no hidden-zone identity tracking | Done for copies, zone re-entry, control change, attachments, targets, combat, INSPECT; tokens untested |
| P1 | Durable knowledge and bounded summarization | Full-game reset/restart recovery, source-preserving compression, explicit stale knowledge, bounded prompts and retrievable full reveals | Reveals retrievable in full; archive still in-JVM; prompts not yet bounded independent of game length |
| P1 | Tool protocol and eval accounting | Native typed adapters or strict equivalent, per-decision budgets across retries, exact request/token accounting, disjoint experiment grouping | Eval grouping/accounting done (EVAL-IMPLEMENTATION.md); tools remain text commands; INSPECT added to the shared budget |
| P2 | Strategy notebook and optional archetype evidence library | Contradiction/update tests, no opponent deck-label leakage, assistance version and ablations | Notes only |
| P2 | Player-safe simulation adapter | Live-state/RNG equality before/after calls, hidden-world invariance, disclosed rollout policy and assumptions, unsupported cases refused | Not started |
| P3 | Frozen harness evaluation suite | Observation/comprehension, identity, memory/retrieval, legal actions, puzzles and full-game play; fixed seeds, baselines, confidence intervals | Substantially covered: the acceptance suite (10 stages, fixed seed-42 canaries + loopback games) plus `SeatObservationTest` (observation/identity/tokens/face-down), `ContextMemoryTest` (memory/retrieval/notes), `LlmSpikeTest` (routing/parsing) and the ownership ledger test; **full-game play baselines with CIs** remain the piece not yet frozen as a suite |

The harness is **not yet certified fair**. Recovery plus memory tools are a concrete
first stage, not completion of this entire contract.
