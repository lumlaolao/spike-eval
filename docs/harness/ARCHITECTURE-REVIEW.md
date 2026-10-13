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
