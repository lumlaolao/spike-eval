# Player lookahead: an engine-simulation tool for the playing seat

Design + work order for the capability the user asked for: *the model should be able to call
the Forge engine on a position, look a few moves into the future, and reason on the result* —
without cheating. Status: **design, not built.** This is the G8 gate ("safe simulation",
`history/COMPLETION-GATES.md`).

## Why this is the highest-leverage play-quality item

The harness today decides each position from a single static snapshot. A real strong player
**looks ahead**: "if I attack here, does the block kill me on the crackback?" The engine can
answer that exactly — it already does, offline, in `LlmSpikeFork.runRollout()` — but the
playing seat has no way to ask. Exposing it (fairly) is worth more than any single routed
hook.

## The safety contract this must satisfy (PLAYER-HARNESS.md §7)

An agent-facing rollout may use **only the seat's information set** and must disclose what it
assumed. It **must not**: read the actual hidden hand/library order; consume live RNG; mutate
live state; use Forge's strategic policy without saying so. It must report the rollout policy,
sample count, uncertainty, and refuse unsupported decisions. A **rules preview**, a **sampled
imperfect-information rollout**, and **omniscient offline analysis** are three different things
and must be labelled as such — never blur them.

## What already exists (reuse, don't rebuild)

| Piece | Where | Use |
| --- | --- | --- |
| Hidden-zone determinization | `LlmSpikeFork.determinizeHiddenZones(Player, boolean)` | re-deals the hidden library order from the seat's info set (`FORK_ROLLOUT_DETERMINIZE=library`) |
| Rollout engine | `LlmSpikeFork.runRollout()` | fork at a decision index, apply an option, run to completion, emit WP per option |
| Fork addressing | `LlmSpikeFork` + `LlmSpikeGui.ForkHook` (`emitDecisionRow`) | replay-to-index prefix, the interception seam already validated |
| Tool grammar + budget | `ContextMemory` (`MEMORY …`, `INSPECT …`, `TOOLS_PER_ASK=2`, `TOOLS_PER_GAME=64`) | where a `SIMULATE` command slots in |
| Per-decision menu | `PlayerControllerLLM.decideOne/decideMany` | the enumerated legal options the model already sees |

## Design

### Command surface

```
SIMULATE <option-number>        # option number from THIS decision's menu
```

One lookahead per ask; shares the `ContextMemory` tool budget (and a stricter per-game cap,
proposed 8) plus a hard wall-clock cap (proposed 20 s). Returns a compact report, e.g.:

```
SIMULATE 3 (attack with Ragavan + Dragon's Rage Channeler):
  8 samples, opponent policy = Forge AI, library re-dealt from YOUR information set
  won 6/8 (75%); opponent's hand assumed unknown (re-dealt)
  typical loss line: they block Ragavan, untap, Bolt your face, swing 4
  CAVEAT: sampled single-seat rollout, not ground truth; combo lines undervalued
```

### Mechanics (v1 scope)

1. **Only where the fork is sound.** v1 restricts to the decision types the fork already
   supports and that fire with an **empty stack**: `play`, `mulligan`, `attackers`, `blockers`.
   Targets/discard/sacrifice/modes are mid-stack and are **refused** with an explicit
   "lookahead unsupported for this decision type" (never silently approximate).
2. **Fork from the seat's information set.** Determinize every hidden zone (opponent hand +
   both libraries) by re-dealing from the seat's known information; never read the live order.
   Reuse `determinizeHiddenZones`; extend it to the opponent's hand (a v1 gap it already flags).
3. **Apply the option, run N samples.** N configurable (default 8) with `rollout_seed =
   mix64(base_seed, option_i, sample_j)`; continuation policy disclosed (Forge AI / `floor`).
4. **No live side effects.** The fork runs on a copied/forked game; the live game's RNG and
   state are untouched (assert this — see acceptance below).
5. **Report honestly.** Sample count, policy, determinization, win fraction with its interval,
   and the standing caveats (`rollout_policy_caveat`, `single_deal_hidden_info` /
   `determinized_library_hidden_info`) — the same vocabulary the PUZ answer keys use.

### Where the code goes

- **Extract a reusable entry point** from `LlmSpikeFork`: `static RolloutReport rolloutFromLive(Game, Player seat, int optionIdx, int samples, RolloutPolicy)`. Today the rollout is only reachable through the `FORK_MODE=rollout` CLI; the tool needs an in-process call. Keep the CLI path working (it is the offline answer-key generator).
- **New `PlayerLookahead`** (or a method on `PlayerControllerLLM`): parses `SIMULATE`, maps the option number to the current menu, refuses unsupported types, calls `rolloutFromLive`, formats the report, and accounts the budget.
- **`ContextMemory`**: recognise `SIMULATE ` in `isToolRequest`/`execute` alongside `MEMORY`/`INSPECT`, passing the live menu labels so the report can name the option.
- **`framing()`**: add one line to the tool instructions when the tool is enabled
  (`LOOKAHEAD=1`), so the model knows it may ask.

### Flags and provenance

- `LOOKAHEAD=1` enables the tool (default **off** until validated); a `context` knob records it
  so a lookahead-enabled run can never be silently compared with one without it.
- The tool's own cost is counted separately from decision tokens (the existing "do not sum
  context-call tokens" rule) and reported as CPU time.

## Acceptance criteria (each must be shown, not asserted)

1. **Hidden-world invariance** — two games differing only in the opponent's hidden hand/library
   produce **byte-identical** lookahead reports (the paired-world test `SeatObservationTest`
   already uses this shape). Shown since lookahead-preview-v6 (2026-10-16):
   `LookaheadProbeTest.hiddenCardsDoNotChangeThePreview`. Until then the preview copy kept the
   opponent's real hand, which an independent review found (H2).
2. **No live side effects** — the live game's `winner`/turn/RNG stream are unchanged with the
   tool on vs off (a floor canary with the tool forcibly invoked).
3. **No hidden-order read** — a fixture where the real top card differs between worlds leaves
   the report unchanged (the report must depend only on the info set).
4. **Budget** — a runaway model cannot exceed the per-game cap; requests past the cap return a
   refusal, never an action.
5. **Refusal** — mid-stack decision types return the explicit unsupported message.
6. **Honest labelling** — the report carries policy + sample count + caveat; a 0-sample or
   determinize-noop run is refused, not relabelled (the fork already has this guard).

## Non-goals (v2+ / never)

- Full ISMCTS (many sampled worlds). The preview re-deals the opponent's hand and every library once,
  from a fixed seed (v6); it samples one world, not many.
- Omniscient analysis using the real hidden order — **never** exposed as a player tool.
- Using Forge's actual strategic policy silently — the continuation policy is disclosed.
- Public-information *hidden* reasoning — out of scope.

## Combat lookahead (attackers + blockers DONE)

Combat is where games are decided, and the engine already exposes the primitives (read
2026-10-12), so no new simulation is needed:

- `ComputerUtilCombat.sumDamageIfUnblocked(attackers, attacked)` — total unblocked damage from a set;
- `ComputerUtilCombat.damageIfUnblocked(attacker, attacked, combat, withoutAbilities)` — per attacker;
- `CombatUtil.canBeBlocked(attacker, combat, defendingPlayer)` — evasive vs blockable.

**Implemented (2026-10-12), both gated by `LOOKAHEAD` (empty by default → prompt bytes unchanged;
both seats compute from the same state, so the byte-identical shared-template discipline holds):**

- attackers: `attackersCeiling(...)` appends `OUTLOOK (engine, all unblocked): N damage. Cannot be
  blocked: <names> (dmg).` to the shared attackers question.
- blockers: `blockersCeiling(...)` appends `INCOMING (engine, if nothing blocks): N damage.` to the
  shared blockers prompt.

Next: extend the same treatment to a per-attacker breakdown at the block decision (what each block
saves), and the sampled multi-turn rollout.

## Effort

The in-process entry point, tool grammar/budget/report, copy + determinization, targeting and target
hints are **built and validated** (this session). Remaining effort is the combat lookahead above,
multi-target spells beyond the minimum, and the sampled multi-turn rollout (EVAL-DESIGN Phase 1.6's
"state_export + fork-from-snapshot").

## Implementation findings (2026-10-12)

Recorded while scoping the code so the next session starts from facts, not guesses.

- **`LlmSpikeFork.runRollout()` cannot serve the live seat.** It is *replay-from-index*: it builds
  a fresh `Match` and replays a deterministic prefix from turn 1. A live game's prefix contains LLM
  decisions and is not reproducible, so the tool needs a **mid-game** mechanism. Its own docs mark
  `GameCopier` mid-stack forking "plan B, v1-restricted to empty-stack decisions" — which is exactly
  the tool's v1 scope.
- **CONFIRMED (2026-10-12, `LookaheadProbeTest`).** `new GameCopier(bareGame).makeCopy()` succeeds
  on a game built the way the observer tests build one, and the copy is a **distinct object graph**
  (its players are not the live players) — a simulation on it cannot alias/mutate the live game. The
  necessary condition for the tool is met and is now pinned by that test.
- **RNG isolation implemented and proven (2026-10-12, `PlayerLookahead.copyForPreview`).** Forge draws
  from a process-wide `MyRandom`; shuffling the copy's hidden libraries would advance that shared
  stream and change the LIVE game's future draws. `copyForPreview` installs a fresh `Random` for the
  copy's shuffles and restores the live reference in a `finally`. `LookaheadProbeTest` proves: the
  live RNG stream is **bit-identical** after a preview (the next `nextInt` equals a fresh stream's),
  the live library order is unchanged, and the determinized order is deterministic for a fixed seed.
  This is the safety-critical foundation; what remains is applying a specific option and wiring the
  `SIMULATE` tool to the seat.
- **A reusable primitive exists in `forge.ai.simulation`.** `GameCopier(origGame).makeCopy(...)`
  clones a live game; `new GameSimulator(controller, origGame, aiPlayer, advanceToPhase)` copies,
  optionally resolves the stack (`GameSimulator.resolveStack(game, opponent)`), and
  `simulateSpellAbility(sa)` returns a `GameStateEvaluator.Score`. So an **immediate-resolution
  preview** ("if I do X, resolve the stack from here, the board/life delta is Y") is buildable today
  on a copy, cheaply.
- **But that is a heuristic board score, not a win probability, and not a multi-turn rollout.**
  `GameStateEvaluator` is Forge's static evaluator; it does not play out future turns. A real
  "look N turns ahead" needs running turns on the copy — more machinery, more risk, and the
  determinization caveat below applies to every future turn it plays, not just the first.
- **Determinization must wrap the COPY, not the live game.** `determinizeHiddenZones` re-deals the
  hidden library order from `MyRandom`; on a copy that also means the opponent's hand, and it must
  happen *before* any resolution the copy performs, or the preview reads the real hidden order and
  is no longer player-safe. This is the single most important correctness point.
- **Option-application implemented + proven (2026-10-12, `PlayerLookahead.previewCast`).** It plays a
  spell on a determinized copy (`GameSimulator.simulateSpellAbility(sa, false)` -> determinize the
  copy's libraries -> `resolveStack`), and reports the public-only delta. Demonstration from the test:
  `[LOOKAHEAD] if you cast Lightning Bolt: you graveyard 0 -> 1; Rival life 20 -> 17` — while the LIVE
  opponent stays at 20. **Wired (2026-10-12):** behind `LOOKAHEAD=1`, the play decision accepts
  `SIMULATE: <n>` for a spell option, runs this preview, and re-asks (at most one preview per
  decision). Off by default, so existing prompt bytes are unchanged. Proven in-process by
  `g8LookaheadPreviewsASpellThenReasks` and by a fake-server game (`--policy simulate`) that ran the
  real path 253 times. **Targeting spells are now covered too (fixed 2026-10-12):** a real game
  showed the preview reported "no change" for Chain Lightning because the live option has no target
  until the model commits, and the engine will not choose one (`resolve=true` leaves it untargeted;
  a `SpellAbilityChoicesIterator` interceptor probe did not take effect). `previewCast` now sets
  **first-legal targets on the option itself — preferring an opposing player, then an opposing
  permanent — resolves, and RESTORES the option's own targets in a `finally`** (transient decision
  scaffolding on an ability not yet on the stack; the real cast's `ensureTargetsSet` overwrites
  targets anyway, and a test pins that the option is left untargeted). The assumed target is
  disclosed. Real game loop:
  `if you cast Lightning Bolt [assuming target: BurnB]: you graveyard 1 -> 2; BurnB life 20 -> 17`
  — 273 rounds, 0 refusals, 0 errors. The model can also **aim** the preview: `SIMULATE: <n> <target
  name>` matches a legal candidate by name (`if you cast Lightning Bolt [assuming target: Elite
  Vanguard]: ... Rival board 2 -> 1`); with no hint the disclosed default is the first opposing
  player, then an opposing permanent. Still open: multi-target spells beyond the minimum; and the
  preview is **best-effort** — casting some deck's spells (Grixis Reanimator) throws a caught
  `RuntimeException` (reported as `(preview failed: RuntimeException)`), so a preview can fail
  without affecting the game. The `lookahead` acceptance stage exercises the path continuously.
