# Architecture review: why the seat does not play its own cards together

**Date:** 2026-10-13. **Engine:** `6c1810dd0b42`. **Observation at review time:** `observation-faithful-v10`.
**Scope:** the LLM decision loop in `forge-overlay/.../forge/spike/`. Overlay code is cited by
method name, because line numbers move with every change; engine code is cited by `file:line` at
the pinned commit. Log line numbers refer to `/tmp/gui-spike.log` (seed 42, Grixis Reanimator vs
Mardu Energy, 2026-10-13 00:04), copied to `docs/harness/evidence/gui-spike-20261013.log`. Every claim below cites a log
line, a `file:line`, an engine card script, or a probe that drives the live controller against the
real engine (`LineFindingProbeTest`, output tagged `[PROBE-x]`).

## Verdict

The owner's complaint is that the seat does not reason in sequences over its own cards. The evidence
says **most of that is the harness, not the model, and not (yet) the architecture**:

- The two flagship lines (Cabal Therapy flashback + Ajani; Goblin Bombardment + doomed tokens) are
  **not executable through the current action interface**. The harness mislabels the option, lets
  Forge's AI pick the sacrificed creature (it sacrifices Ajani himself), and never offers the
  response window. No model can find a line the interface cannot express.
- The plan problem is real but narrower than "plan-then-act is flawed": prose was being *executed*,
  which is a harness bug (fixed in #75). What remains is a design gap: the plan has no validity
  check, and nobody compares it with the action taken.
- What is left for "the model" cannot be measured until the two points above are fixed. Today a
  misplay and a harness override are indistinguishable in the log.

So the hypothesis handed to this review (structured plan contract + sequence simulation + forced
self-statement) is **partly refuted as a first step**: it would have been built on an action layer
that silently substitutes Forge's AI at exactly the decisions those lines depend on.

## Part 1. Ranked problems

Ranking is by how much of the observed misplay each one explains, then by how certain the evidence is.

### 1. Sacrifice-as-cost is chosen by Forge's AI, silently (harness bug; layer: decision loop / ownership)

- **Engine path.** The seat's chosen ability is played by the inherited
  `PlayerControllerAi.playChosenSpellAbility` → `ComputerUtil.handlePlayingSpellAbility`, which pays
  with a hard-coded `new AiCostDecision(ai, sa, false)` (`forge-ai/.../ComputerUtil.java:129`). It
  never calls `getCostDecisionMaker`, so `LlmCostDecision` is bypassed for every cast and
  activation. `AiCostDecision.visit(CostSacrifice)` → `ComputerUtil.chooseSacrificeType` →
  `ComputerUtilCard.getWorstAI`.
- **Probe A** (Bombardment + Serra Angel, Grizzly Bears, Memnite):
  `modelAsks duringPay=0 sacrificeAsked=false … memnite=false aiDelegations=0`. The AI chose; the
  `ai=0` invariant did not notice.
- **Probe D** (Cabal Therapy in graveyard, Ajani + Savannah Lions in play): flashback cast →
  `sacrificeAsked=false ajaniAlive=false lionsAlive=true`. **The harness sacrifices Ajani**, so the
  Therapy-flips-Ajani line is impossible even if the model chooses it.
- **Log.** `/tmp/gui-spike.log` has five Bombardment activations (lines 295, 343, 427, 458, 464)
  and zero `sacrifice` lines.
- **Docs are wrong here.** `OWNERSHIP.md` and `COMPLETION-GATES.md` G7 list `getCostDecisionMaker`
  as model-owned "e.g. Cabal Therapy flashback". That holds only for the two
  `PlaySpellAbility.java` call sites (183, 683), not for the path a play takes.

### 2. The play menu misdescribes activated abilities and graveyard casts (harness bug; layer: observation)

- `PlayerControllerLLM.saLabel` prints the **host card's mana cost**, not the ability's cost, and
  no zone; every option is prefixed `CAST`.
- **Probe D menu:** `CAST Cabal Therapy {B} [Choose a nonland card name. …]` for a flashback from
  the graveyard whose real cost is `Sac<1/Creature>` (`cardsfolder/c/cabal_therapy.txt`,
  `K:Flashback:Sac<1/Creature>`). Nothing says graveyard, flashback, or sacrifice.
- **Log 295:** `play: casts Goblin Bombardment @29 1{R} [Sacrifice a creature: …]` for an activation
  that costs no mana.
- Consequence: the seat is asked to find a line whose defining property (the cost is a creature) is
  absent from the option it must pick.

### 3. The seat cannot respond to its own stack objects (harness bug; layer: decision loop)

- `chooseSpellAbilityToPlay` (the `SKIP_HEUR` block at its top): if the top of the stack was
  activated by the seat, return PASS without asking. The line above it also auto-passes the seat's own end step when the stack is empty.
- **Probe B:** own ability on the stack → `respondAsked=false returned=PASS`.
- This is the owner's Voice of Victory test (item 4). Mobilize's delayed "sacrifice them" trigger is
  the seat's own; responding with Bombardment is unreachable. The same value is available in main 2
  (the tokens are doomed either way), so the damage is recoverable, but the specific window is
  closed, and so is every "hold priority" line (flashback in response to your own spell, sacrifice
  in response to your own removal).

### 4. Optional triggers are an opaque yes/no on the no-reasoning tier (harness bug; layer: observation + routing)

- Ajani's flip is optional in the engine (`OptionalDecider$ You`,
  `a/ajani_nacatl_pariah_ajani_nacatl_avenger.txt`), so it arrives as `confirmTrigger`.
- **Probe C question, verbatim:** `DECISION: Accept this optional triggered ability? … Ability:
  Ajani, Nacatl Pariah 1{W}`. The trigger's text ("exile Ajani, then return him transformed") is not
  shown. It is asked on the **fast** tier (`optionalDecision`, reasoning disabled per `LATENCY.md`).
- `optionalDecision` prints no `[ROUTER]` line on a parsed answer, so the decision is invisible.
- **Log 1243-1249:** a second Ajani is cast into the legend rule (a real flip line: the dying Ajani
  is "another Cat"); an unlogged fast-tier reply follows; Ajani attacks as a creature afterwards.
  The flip was probably offered once and declined. *Not provable*: no `EVAL_OUT` was set.

### 5. A prose plan was executed as if it were a step list (harness bug, fixed in #75; residual design gap; layer: decision loop)

- `/tmp/gui-spike.log` predates #75 by seven minutes. It has 14 `plan step` executions; **7
  contradict the sentence they came from** (not 1, as reported):
  line 139→152 (`Hold Shallow Grave` → cast), 115→121 and 252→260 (`holding … Wasteland` /
  `Wasteland is dead … hold it` → activated), 187→196 (`play Wasteland later`), 45→53, 314→337
  (`Pass this window` → Animate Dead on the opponent's Hexing Squelcher), 447→456 (`holding Goblin
  Bombardment for reach` → activated).
- #75 restricts execution to an explicit `STEPS:` list matched by exact name. Residual gaps:
  1. steps are bare card names: no target, no mode, no sacrifice, no "then attack";
  2. invalidation is opponent-side only (`oppPlanSig`, checked in `chooseSpellAbilityToPlay`); a draw or a failed step on our
     side does not void the plan (`LATENCY.md` says so itself);
  3. a step that is no longer legal is skipped silently (`planStepInMenu` returning -1) and the next one fires;
  4. nothing records whether the action taken matches the plan stated.

### 6. Most decisions are made without reasoning, including the ones that carry the lines (design gap; layer: routing)

- Final tallies in the log: Mardu `smart=7 fast=22`, Grixis `smart=4 fast=9`. Earlier in the same
  match: `smart=11 fast=58`. 182 `play(react)` windows vs 112 `play(main)`.
- Every reactive window, every single-option main, every target, and every optional trigger goes to
  the tier with `reasoning.enabled=false`. Sacrifice-outlet timing, flashback timing and trigger
  acceptance all live there.
- The reasoning tier is asked for a one-line plan once per turn and is not asked again unless the
  menu has two or more castables.

### 7. No way to evaluate a line before committing (design gap; layer: tools)

- `PlayerLookahead.previewCast` refuses anything that is not a spell (`:98`), previews one option,
  once per decision (the `lookCtx` guard in `chooseSpellAbilityToPlay`), and reports only life/board/graveyard *counts*
  (`publicDelta`). A two-card interaction cannot appear in its output by construction.
- `LOOKAHEAD` is off by default. In the audited game: 0 lookahead rounds, 0 MEMORY/RULES/INSPECT
  calls.

### 8. The log cannot support an audit (harness bug; layer: tooling)

- No sacrifice line (problem 1), no confirm-trigger line (problem 4), and real play scripts do not
  set `EVAL_OUT`, so prompts and hands are not recorded. The Ajani/Therapy game cannot be audited
  for that reason.
- `Mardu Energy AI failed to play Goblin Bombardment (94)` (log 345) is an engine `println`, not a
  harness event: the seat targeted its own Voice of Victory, the AI's sacrifice filter excludes the
  target, nothing else was eligible, the payment failed. **Nothing was sacrificed**; the earlier
  report that it "paid the sacrifice cost for nothing" is wrong. The harness recorded it as
  `play: casts`.

### 9. Stale knowledge (weak evidence; model limitation at most; layer: context)

- `runs/haiku-vs-floor/game-6-1784366341278.jsonl`: both `card-name` replies cite "T3 intel" at
  turns 11 and 17.
- Two corrections to the report: (a) both replies **do** end with a `CARD:` tag; `choice=-1` with
  `n_options=-1` is the schema for free-text rows (`chooseCardName`'s free-form branch), not a parse failure.
  (b) The opponent was the **floor** arm, which never casts, so a T3 hand read was still mostly true
  at T17, and the model said "14 turns passed" itself.
- This is a July run on engine `c1c64a53` and a legacy context profile. It says little about the
  current harness. An age stamp is cheap and factual; "likely stale" is a judgment the harness
  cannot support.

### What could not be determined

- Whether the model would find the Therapy/Ajani line once the interface allows it. No recorded
  game exists where it was possible.
- Whether the seat declined Ajani's flip at log 1247 or was never asked (no prompts recorded).
- How much play quality problems 1-4 cost. That needs paired games after the fixes.

## Part 2. Design decision

Research summary (full citations at the end; each was fetched and checked unless marked).

| Question | What the evidence says |
|---|---|
| Should the plan be structured and harness-executed? | Yes. Cicero conditions on a structured intent and filters output against it (plan consistency 76% → 93%). DipLLM's factorisation of a turn into ordered sub-decisions, with no training, took an 8B model from 0.2% to 9.9% against DipNet. ReWOO and LLMCompiler show one planning call plus harness execution is the cheap-model shape. |
| How long should a plan bind? | Not across new information. Cicero recomputes intents on every message. Bind within our own uninterrupted sequence; void on any engine event. |
| Does stating the right plan make the model act on it? | No. Schmied et al. 2025: 87% of rationales correct, 21% of the following actions optimal. A correct rationale does not bind the action; the harness has to. |
| Should the engine evaluate lines? | Yes, where the answer does not depend on hidden cards. PokéChamp (LLM proposes, engine transitions, 8B model beats GPT-4o baseline 64%) is the closest analogue. Long et al. 2010: determinized evaluation is sound when outcomes barely depend on hidden information and exploitable when they do. Cowling et al. 2012: never one determinization. |
| Will the model join two card texts unaided? | Not reliably. Press et al. (compositionality gap does not shrink with scale), Bateni et al. 2025 (LLMs weak at positive card synergy). But no result says a two-hop join is impossible; the supported fix is to externalise the intermediate fact. |
| Does forcing the model to state its interactions help? | Weakest of the four. mage-bench found a self-explanation field added nothing; Schmied shows stated reasoning is not acted on. Useful as an audit field, not as a mechanism. |
| How should blunders be found? | mage-bench: an LLM judge per decision (91% coverage vs 50% batched). With a rules engine there is a cheaper signal: diff the stated plan against the action taken. |

### Options

| | Option | For | Against |
|---|---|---|---|
| A | Fix the action interface first: model-owned costs, honest option labels, own-stack windows, trigger text | Explains problems 1-4 with engine reproductions; deterministic tests; no new model calls except decisions that were being taken from the model | Changes what the model sees (version bump); does not by itself make the model find lines |
| B | Engine-computed interaction facts ("this cost sacrifices a creature; you control a permanent that triggers when a creature dies") | Externalises the join (Press, Bateni); deterministic from card scripts | No prior art for general trigger pairing; it is an assistance condition and must be versioned and ablatable |
| C | Model-authored `LINES:` primer (the uncommitted work) | Cheap; costs no extra call | Weakest evidence; the shipped prompt example names the Therapy/Ajani line, which is hidden coaching for one deck |
| D | Structured plan contract: typed steps, validated against the legal menu at plan time, voided on any change, with a plan-vs-action audit | Strong evidence (Cicero, DipLLM, Schmied) | Larger change; worthless until A lands because the executor would still mis-pay costs |
| E | Sequence simulation: `SIMULATE` over an ordered list of steps on a determinized copy | Only way a two-card line shows up before committing (PokéChamp, LATS) | The copy's seat is played by Forge's AI for costs and triggers, so the preview has problem 1 unless the same fix is applied there; hidden-information outcomes must be flagged |
| F | Full search (MCTS/ISMCTS) with the LLM as policy/value | Strongest play in the literature | 40-730 s per move in Schultz et al.; needs a value model; not this project |

### What gets built, in order

1. **A, in full.** It is the largest explained share of the misplay and the precondition for
   everything else. Includes the missing log lines (problem 8).
2. **The in-flight work, corrected.** Keep the reveal age as a fact (drop "likely stale"). Keep the
   `LINES:` primer only with a deck-neutral example and a bounded number of asks; it is labelled as
   the model's own note and is the audit field the research says it is good for.
3. **Plan-vs-action audit** (the measurable half of D): an offline report over recorded games that
   flags decisions where the action contradicts the stated plan. It is the instrument for deciding
   whether the rest of D is worth building.
4. **D and E are planned, not built in this pass**, and are gated on a measurement after 1-3: if
   the seat still misses own-card lines once it can execute them, build E (auto-simulated, not
   offered) and the typed plan.

### Explicitly not building

- **F** (tree search). Cost and the missing value model.
- **A curated combo database** (Commander Spellbook style). It is deck-specific coaching and covers
  game-ending combos, not value lines.
- **Per-deck prompt hints.** Any example text that names a real line from an eval deck.
- **Blunder scoring by an LLM judge.** The engine diff is cheaper and reproducible; revisit if the
  audit proves too coarse.

## Part 3. What was built, and what it measured

| PR | Change | Review problem |
|---|---|---|
| #76 | The model chooses what a cost sacrifices on the real play path; failed plays are logged as failed | 1, 8 |
| #77 | `observation-faithful-v11`: option labels carry the option's own cost and zone; optional triggers carry their text and are logged; reveal age; bounded, format-only `LINES:` note | 2, 4, 9 |
| #78 | The seat may respond to its own trigger on the stack; `eval/plan_audit.py` | 3, 5 |
| #79 | `observation-faithful-v12`: rules text of every opponent card the seat has seen; any non-mana response to an opponent's spell is offered, on the reasoning tier; `eval/line_report.py` | two more, found after this review was written (below) |

### Two problems found after Part 1

**10. The seat never read the opponent's cards** (harness gap; layer: observation). A live prompt at
turn 12 showed `Orcish Bowmasters 1/1 [untapped] [sick] [flash]` and `Karakas x1`: name, P/T and
keywords, no rules text. The text was available through `INSPECT`; in the four recorded games checked
it was called zero times. Fixed in #79.

**11. An untargeted answer to an opponent's spell was never offered** (harness bug; layer: decision
loop). The reactive filter in `chooseSpellAbilityToPlay` asked the model only if an option targeted
or countered. Probe: opponent casts Reanimate on Griselbrand, seat controls Soul-Guide Lantern
(`{T}, Sacrifice: Exile each opponent's graveyard`, `cardsfolder/s/soul_guide_lantern.txt`) →
`asked=0 returned=PASS`. Fixed in #79 (`anUntargetedAnswerToAnOpponentsSpellIsOffered`).

### Measurement A: Mardu Energy (model) vs Forge AI, mirror, observation v11

`runs/lines/v11-mardu`: seeds 42-45 paired, 8 games, 0 capped, `openrouter:deepseek/deepseek-v4.1-flash`,
`overlay_sha edb3c41e5cd6`, `context_sha 0895f75a1fbb`, exploratory discipline, load average 3-5 on 18
cores. Reproduce:

```bash
python3 eval/line_report.py runs/lines/v11-mardu
python3 eval/plan_audit.py runs/lines/v11-mardu
python3 eval/latency_report.py --compact runs/lines/v11-mardu
```

| What | Before (the audited game / engine probes) | v11, 8 games |
|---|---|---|
| Ajani's flip offered → accepted | never transformed in 19 turns; the one probable offer was an unlogged, textless yes/no | **2 offered, 2 accepted** |
| Ajani, Nacatl Avenger loyalty activations | 0 | **9** (including the ultimate) |
| Sacrifice-as-cost chosen by the model | 0 of 5 (Forge AI chose all, silently) | **1 of 1** (a Cat token to Goblin Bombardment) |
| Goblin Bombardment: offered / activated | — | 110 / 1 |
| Cabal Therapy cast | 0 | 0 |
| Plays the harness made against the plan | 7 | 0 |
| Plays the MODEL made against its own plan (`plan_audit.py`) | 2 of 42 | 9 of 65 (13.8%; trivial baseline 0) |
| Model calls per turn | — | 4.3 (159 smart, 278 fast over 102 turns) |
| Won by the model | — | 4 of 8 |

How to read it:

- **The Ajani line now happens.** It was offered twice and taken twice, and the planeswalker was
  then used nine times. Before the fixes the question had no text and no log line.
- **Cabal Therapy was never castable.** It was in hand at 31 play decisions and every one reads
  `need 1 more B` in the observation; Thoughtseize was uncastable at 123 decisions for the same
  reason. The seat fetched or kept white/red sources while holding black spells. That is a
  sequencing question about land choice, and it is open (below).
- **Bombardment was offered 110 times and used once.** In the one stated reason on record the seat
  wrote "we need gas, not a 1-damage sacrifice of our own" creature. That is a choice, not an
  inability; whether it is a good one is not something this run can say.
- **The seat states its own lines when asked.** Verbatim from the recorded `LINES:` answers:
  "Voice of Victory + Goblin Bombardment -> mobilize Warriors on attack, sac them for extra reach";
  "Ajani, Nacatl Pariah + Ocelot Pride -> ... Ajani flips when Cats die"; "Thoughtseize + Cabal
  Therapy -> strip their hand, flashback Therapy for double discard". So the join is within this
  model's reach when it is asked for. Stating a line and playing it are different things: the
  13.8% plan-contradiction rate is the measured size of that gap, with the caveat that the audit
  is lexical (one of the nine reads "play Wasteland ... and hold the Thoughtseizes" and is a false
  positive).
- **4 of 8 is not evidence** about strength in either direction. `eval/aggregate.py` also prints
  `FAILED FALLBACK GATE 13.1%` for this run: all 66 flagged rows are `tier=auto` forced-only-legal
  decisions and the keep-7 mulligan heuristic, none is a failed model call or an AI delegation.

### Measurement B: Mardu Energy (model) vs Forge AI on Grixis Reanimator, observation v12

`runs/lines/v12-mardu-vs-reanimator`: seeds 42-45 paired, 8 games, 0 capped, same model,
`context_sha 44f97a930af3`, built from commit `2e251bb0`. Not poolable with A (different observation
version and a different opponent deck). Same three report commands.

| What | v12, 8 games |
|---|---|
| Cabal Therapy flashback: offered / cast | 14 / **3** |
| Sacrifice-as-cost chosen by the model | **3 of 3** (Cat Warrior Token, Cat Warrior Token, Cat Token) |
| Ajani's flip offered → accepted | **2 → 2**, both directly after a flashback sacrifice |
| Ajani, Nacatl Avenger loyalty activations | 6 |
| Play prompts carrying opponent card text | 301 of 313 |
| Reasoning-tier asks on the opponent's turn | 8 |
| Plays the model made against its own plan | 7 of 55 (12.7%) |
| Model calls per turn | 4.9 (156 smart, 255 fast over 84 turns) |
| Won by the model | 8 of 8 |

**The line the owner said the seat never found, found twice.** From
`runs/lines/v12-mardu-vs-reanimator/game-*.log`, two different games, consecutive router lines:

```
play: casts Cabal Therapy @85 from your GRAVEYARD (Flashback) — cost: Sacrifice a creature [...]
sacrifice [Cabal Therapy]: Cat Warrior Token
confirm-trigger [Ajani, Nacatl Pariah]: YES
card-name: Reanimate [Cabal Therapy]
play: activates Ajani, Nacatl Avenger @88 [0: Create a 2/1 white Cat Warrior creature token. ...]
```

Each of those five lines depends on a fix in this review: the flashback is labelled with its real
cost (problem 2), the model picks the Cat instead of Forge AI picking Ajani (problem 1), the flip
is offered with its text and logged (problem 4), and the name it gives Therapy is one of the
opponent's cards it has now read (problem 10). It named Entomb, Reanimate, Animate Dead and Atraxa
across the run.

Limits on what B shows:

- **8 of 8 is not a strength claim.** Forge's AI pilots this deck poorly: the engine marks three of
  its enablers `AI:RemoveDeck` (Unmask, Faithless Looting, Lotus Petal), and by a grep of the game
  logs it cast a reanimation spell in only 3 of the 8 games. This opponent is closer to a goldfish
  than the mirror was. The win column is reported, not used.
- **The reactive windows cost little here**: 8 reasoning-tier asks on the opponent's turn in 84
  turns. 4.9 calls per turn against A's 4.3 is across different matchups and is not a comparison.
- **The Soul-Guide Lantern spot was not exercised.** Neither deck plays an untargeted answer; that
  fix rests on its engine test, not on a game.

### What this says about the hypothesis

The brief's hypothesis was that plan-then-act is structurally unable to produce good Magic and
should be replaced by a plan contract, sequence simulation and a forced self-statement.

- **Supported:** a prose plan must not be executed (7 harness contradictions in one game), and a
  stated plan does not bind the model (13.8% after the harness stopped interfering).
- **Not supported as the first cause:** every flagship missed line traced to the action interface
  (problems 1-4, 10, 11), not to the loop's shape. With those fixed, the same loop and the same
  model played Therapy-flashback-into-Ajani twice in eight games and accepted the flip all four
  times it was offered across both runs.
- **Still open:** whether a typed plan and sequence simulation add anything once the interface is
  honest. The plan-contradiction rate and the unused sacrifice outlet are the two numbers to move.

### Not determined

- Play strength. Eight games cannot show it; a quality verdict needs dozens of paired games.
- Whether the `LINES:` note changes play. It was on in every game here; there is no arm without it.
- Whether the land choices that left black spells uncastable come from the fetch-target decision
  (routed to the no-reasoning tier by `TIER_FAST_TYPES=...,zone-change`) or from the keep/mulligan.
- `ComputerUtil.playStack` / `playNoStack` still pay with Forge's `AiCostDecision`.

### Next

1. An arm without `LINES:` and an arm with `zone-change` on the reasoning tier, same seeds, to price
   each.
2. If the plan-contradiction rate holds, build the typed plan (WS3-P3): steps validated against the
   legal menu, voided on any engine event.
3. Sequence simulation (WS3-P4) only if the sacrifice outlet stays unused in spots where an engine
   preview would show it winning.

## Part 4. Controlled arms: before and after, and the sequence hypothesis

Parts 1-3 measured the fixed build on its own. This part compares builds and arms on the **same
paired games**: Mardu Energy (deepseek-v4.1-flash, `v4-faithful`, `MEMORY_TOOLS=1`) against Forge AI
on the same deck, seeds 42-49 with both seatings (16 games per arm), no game capped. Machine load
was 0.2-0.3 per core throughout. Reproduce any row with
`python3 eval/line_compare.py NAME=runs/lines/ctl/<arm> ...`, `eval/plan_audit.py` and
`eval/line_report.py`. The arms differ in observation version, so they are compared, never pooled.

| arm | build | what differs |
|---|---|---|
| `prefix-mirror` | `adf94657` (before PR #76), observation v10 | the pre-fix harness |
| `control` | `7b248caf`, observation v12 | PRs #76-#79 |
| `planexec` | same + `PLAN_EXECUTE=1` | the harness may run steps of the model's stated plan |
| `lookahead` | same + `LOOKAHEAD=1` | the model may reply `SIMULATE: <n>` for a preview |

### What the fixes changed (measured per decision)

| | prefix | control | planexec | lookahead |
|---|---|---|---|---|
| plays whose cost is a sacrifice (Goblin Bombardment, Cabal Therapy flashback) | 10 | 9 | 13 | 12 |
| of those, the sacrificed permanent was chosen by the **model** (or forced: one legal choice) | **0** | 9 | 13 | 12 |
| optional triggers put to the model **with their text** | 0 of 27 | 46 of 46 | 44 of 44 | 32 of 32 |
| games in which Ajani flipped to the planeswalker | 3 | 7 | 5 | 6 |
| loyalty activations | 12 | 20 | 11 | 13 |
| model calls per turn | 4.3 | 4.2 | 3.9 | 4.0 |

In the pre-fix arm there is no `sacrifice` decision row at all: all 10 sacrifices were picked by
Forge AI through the hard-coded `AiCostDecision` (problem 1). After the fix every one is the
model's or forced. That is the claim the PRs make, and it holds on every arm. Ajani flipped in 3 of
16 pre-fix games against 7, 5 and 6 of 16 after; that direction matches the fix, and at this size
it is not distinguishable from chance (3/16 vs 18/48 pooled, Fisher p=0.23).

Not every model-owned sacrifice is a good one. In `planexec` seed 48, turn 13, the model flashed
back Cabal Therapy with Ajani and Guide of Souls on the battlefield, wrote "Ajani's token value is
largely spent, so I feed Ajani to Therapy", and sacrificed Ajani
(`runs/lines/ctl/planexec/game-48-1791912207547.jsonl`, the `sacrifice` row). The prompt showed
both permanents and the cost. That is a model choice, recorded as one, which is the point of the
fix; whether it was a mistake is a puzzle candidate, not a harness defect.

### What the fixes did not change: who wins

| arm | model wins | Wilson 95% | paired against `control` (only-this / only-control / same) |
|---|---|---|---|
| prefix | 5 of 16 (31%) | 14-56% | 2 / 3 / 11, sign test p=1.0 |
| control | 6 of 16 (38%) | 18-61% | |
| planexec | 6 of 16 (38%) | 18-61% | 2 / 2 / 12, p=1.0 |
| lookahead | 6 of 16 (38%) | 18-61% | 3 / 3 / 10, p=1.0 |

Trivial baseline: a seat that always passes loses every game of this matchup. Sixteen paired games
cannot resolve a difference smaller than roughly 35 points, so this table says only that nothing
large moved. Against Grixis Reanimator the question is unanswerable for a different reason: the
pre-fix build won 8 of 8 there and so did v12 (`prefix-reanimator`, `v12-mardu-vs-reanimator`), a
ceiling. The line events still differ in the expected direction (Ajani flipped in 0 of 8 pre-fix
games and 2 of 8 after; Therapy flashbacks with Ajani on the battlefield 0 and 2, Ajani kept both
times).

### The sequence hypothesis: not confirmed, and not yet tested

The hypothesis was that the seat loses because it does not reason in sequences over its own cards
and the turn ahead, and that giving it a way to plan or look ahead would fix that.

- **Plan-then-execute did nothing measurable.** Same wins as control; plan contradictions 12 of 167
  plays (7.2%) against 14 of 174 (8.0%) in control and 11 of 150 (7.3%) pre-fix. The harness
  executed 15 plan steps in 16 games, because the model mostly declined to hand them over
  ("STEPS: (none -- I want to choose Thoughtseize's target myself)").
- **Lookahead did nothing measurable, because it was barely used.** The model asked for a preview 7
  times in 16 games (900 model calls by `tool_audit.py`'s count, the one used below). Same wins; contradictions 14 of 155 (9.0%).
- **The seat does state sequences.** It wrote a plan in 109-137 turns per arm and its plays
  contradicted the plan's own hold clauses in 7-9% of plays in every arm, pre-fix included. Stating
  the line is not the missing piece.

So the two arms did not test "does lookahead help"; they tested "does offering lookahead as an
optional text reply help", and the answer to that is no. Offered and used are different things:

### Why offered was not used: the tool protocol

`python3 eval/tool_audit.py runs/lines/ctl/control runs/lines/ctl/planexec runs/lines/ctl/lookahead`:
in 48 games and 2,994 model calls the model made 43 tool requests, and the harness **refused 30 of
them (70%)** for their form, answered with `ERROR: use exactly one MEMORY/INSPECT/RULES command
line`: 25 held other text beside a valid command (the sentence the smart tier's own instructions
ask for), 5 were a valid command with only blank lines around it. Ten
requests were served. In the pre-fix arm all 6 requests were refused. Twice the model wrote its
native function-call markup into the text reply. This is a harness defect (problem 12, layer:
tools), found by reading `context_tool` rows rather than by hypothesis.

It was then measured in isolation with the tool-use suite (`eval/tool_use_suite.py`; invented
cards, so the deciding fact exists only behind a tool; `runs/tooluse/deepseek-v4`). The table,
baselines and sign tests are in `PLAYER-HARNESS.md`, "Tool protocol": on the smart tier the
deciding fact came back from a tool in 26 of 30 asks with native calling, 12 of 30 with the
repaired grammar and 4 of 30 with the shipped one (never-call policy: 0), and every ask whose fact
came back was answered correctly. The suite's refusal rate under the shipped grammar (33 of 48)
matches the games' (30 of 43), which is the reason to believe the suite measures the same thing
the games showed. Two earlier runs are not the published one: the first had three wrong fixtures (a
lookup any reasonable agent would make had no answer), and the review of this change found that
"reach" was being credited for calling a tool whether or not the fact came back, and that a search
returned the decision that was asking. Both were fixed before the run quoted here.

The suite also shows what native calling does not fix. On the fast tier (reasoning off) the model
asked for nothing in 6 of 12 native asks about a permanent whose text it had never seen, and got 7
of the 12 wrong; on the smart tier it inspected all 12. Reach is a model property the harness can
raise, not guarantee, which is why opponent card text is pushed into the observation (v12) rather
than left to a lookup.

### The same sixteen games on the new tool protocols

Three more arms on the same paired seeds (build `3119ad94978e`, the tree before review fixes that
changed the round bound from four to three and stopped a preview counting against the per-game
allowance; observation v12 unchanged; no game capped; load 0.2 per core):

| arm | tool requests | served | refused for form | previews | model wins | plan contradictions |
|---|---|---|---|---|---|---|
| `control` (text grammar v1) | 15 | 4 | **11** | n/a | 6 of 16 | 14 of 174 (8.0%) |
| `planexec` (v1) | 15 | 3 | **9** | n/a | 6 of 16 | 12 of 167 (7.2%) |
| `lookahead` (v1, `LOOKAHEAD=1`) | 13 | 3 | **10** | 7 | 6 of 16 | 14 of 155 (9.0%) |
| `textv2` (repaired grammar) | 7 | 7 | 0 | n/a | 6 of 16 | 7 of 151 (4.6%) |
| `native` | **213** | 202 | 0 | n/a | 6 of 16 | 11 of 170 (6.5%) |
| `native-lookahead` (`LOOKAHEAD=1`) | 150 | 146 | 0 | **35** | 5 of 16 | 16 of 154 (10.4%) |

`python3 eval/tool_audit.py runs/lines/ctl/<arm>`, `eval/line_compare.py`, `eval/plan_audit.py`.

- **The protocol was the reason the tools went unused.** With native calls the seat made about 13
  requests a game where it had made under one, 174 of them `inspect`, and none was refused for its
  form. Repairing the text grammar removed the refusals but not the reluctance (7 requests in 16
  games): the model reaches for a function, not for a reply format.
- **Lookahead was finally exercised** (35 previews against 7), which makes `native-lookahead` the
  first arm that tests the hypothesis rather than the offer.
- **Nothing moved in the results.** 6, 6 and 5 wins of 16; every paired comparison is at most 4 games
  either way (sign tests p=1.0). Plan contradictions did not fall with lookahead (10.4%, the highest
  of any arm, on 154 plays; not distinguishable from 6.5% at this size).

So the measured state of the hypothesis: with lookahead available as a tool and used about twice a
game, the seat did not win more or contradict its plans less in sixteen paired games. That is not
evidence that lookahead cannot help (the sample resolves about 35 points and a preview covers one
spell, not a line), but the specific claim "the seat loses because it has no way to look ahead" is
not supported. What the logs do show is narrower and already fixed: decisions the seat was never
given (problems 1-4), text it was never shown (problem 11), and lookups it asked for and was
refused (problem 12).

### Decision

1. Native function calling is the standard tool protocol (`CLAUDE.md`), on by default wherever the
   backend supports it. `simulate` is one of the tools.
2. The text grammar is repaired (`evidence-tools-v2`) and kept only as the fallback.
3. The sequence hypothesis is not supported at this size and stays open at a larger one. The next
   measurement is not another sixteen games: it is a preview that covers a line (two or more of the
   seat's own plays), tested first in the tool-use suite, then on enough pairs to resolve ten points.
4. An MCP server and a sandboxed workspace are not being built
   (`docs/harness/NOTES-mcp-and-workspace.md`).

## References

Planning: Wang et al. 2023 (arXiv 2305.04091); Xu et al. 2023, ReWOO (2305.18323); Kim et al. 2024,
LLMCompiler (2312.04511); Liang et al. 2022 (2209.07753); Singh et al. 2022 (2209.11302); Wang et
al. 2023, Voyager (2305.16291); Liu et al. 2023, LLM+P (2304.11477); Sun et al. 2023, AdaPlanner
(2305.16653); Meta FAIR 2022, Cicero (Science, doi 10.1126/science.ade9097); Xu et al. 2025, DipLLM
(2506.09655).

Search: Schultz et al. 2024 (2412.12119); Hao et al. 2023, RAP (2305.14992); Zhou et al. 2023, LATS
(2310.04406); Karten et al. 2025, PokéChamp (2503.04094); Long et al. 2010 (AAAI, pp. 134-140);
Cowling, Ward, Powley 2012 (IEEE TCIAIG; volume and pages unverified); Frank & Basin 1998 (read via
Long et al.).

Synergy: Commander Spellbook backend (github.com/SpaceCowMedia/commander-spellbook-backend); Bateni
et al. 2025 (2508.19484); Hu et al. 2024, PokéLLMon (2402.01118).

Composition and state tracking: Press et al. 2023 (2210.03350); Dziri et al. 2023 (2305.18654);
Kim & Schuster 2023 (2305.02363); Tang et al. 2026 (2605.30233; small models, no reasoning tokens);
Merrill & Sabharwal 2024 (2310.07923; worst-case expressivity, not a claim about two-step joins);
Fu et al. 2025, AbsenceBench (2506.11440).

Audit: Schmied et al. 2025, "LLMs are Greedy Agents" (2504.16078); Lin et al. 2026, ToolPoker
(2602.00528); Li et al. 2026, PokerSkill (2605.30094; a retrieval paper, not a knowing-doing paper);
GregorStocks/mage-bench; mattshoe/mtg-battle-bots (no plan object, no simulation, no blunder score).

Corrections to the brief: "LLMs are Greedy Agents" is Schmied et al., not Paglieri; no repository
named `mtg-bench` with a blunder index was found (the term is mage-bench's); Tang et al. is 2026.
