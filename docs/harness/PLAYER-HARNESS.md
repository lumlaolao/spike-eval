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
`memory_tools` and `memory_tools_version`; aggregation now recognizes these and
recovered `context_profile`/`context_sha` as provenance conditions. Mixed conditions
are flagged by the existing provenance reporter. Use separate run directories:
existing aggregation deduplicates seed/seat pairs and does not partition experiments.

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
own `context.v3|...|observation=observation-faithful-v8` identity and sha.

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

Hand-reveal bridge/parse failures now choose the first minimum-sized legal subset,
log an automatic fallback, and never call Forge AI. Empty reveal pools return empty.
The regression uses a 1-to-3-card reveal range to catch accidental over-revealing.
Other controller fallbacks still require the ownership audit below.

Random-arm route logs now say `random baseline (no model call)`. They previously
claimed `asking opus/haiku` despite never consulting a model, producing 58 false
missing-call reports in the random canary. The defect analyzer was not modified.

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
