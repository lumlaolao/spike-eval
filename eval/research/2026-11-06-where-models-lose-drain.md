# Where models lose won drain puzzles: an exact board model for the drain family, every recorded attempt (2026-11-06)

**Question.** The splice board's model (`2026-11-06-step-regret-board-model.md`,
`2026-11-06-where-models-lose-splice.md`) labelled where models lose won splice puzzles. Extend the exact board model
to one more generated family and repeat the survey there. For each failed attempt: which decision is the regret step
(the last position from which the puzzle is still winnable, before the first lost one), what kind of decision it is,
its floor (the share of legal options there that keep the win, which is what a uniformly random choice at that step
keeps), and how far it is from the end. Validate first: every position of every solved attempt must be winnable, the
shortfall must never shrink along an attempt, and every winnable verdict at a regret step must survive an engine
witness.

**Family: drain (the drain and drain2 generators, which share `puzzle_gen.drain_search`).** It has the most recorded
failed model attempts of the generated families on this machine after splice (46 failed, 22 solved; the next are elf
and elf2 with 22 failed, fling 9, ping 6, storm 2; hoofdrain's 44 failed attempts end in a Craterhoof attack, which a
prompt-state model would have to carry through combat). It is also exactly modellable from the prompt: one turn, one
phase and its end step, no combat, an opponent with nothing, and every card's effect is a fixed count (each drainer
on the battlefield drains 1 per death, the dying one included).

**Answer so far: the model is exact on every check, and the step is mostly early and at a gate a random chooser
often passes.** The model labels all 68 attempts (0 refused): every one of the 1241 positions of the 22 solved
attempts is winnable (the trivial baseline "every position is lost" gets 0 of them), the shortfall never shrinks along
any of the 68, and all 46 failed attempts have an exact regret step. All 46 steps have an engine witness (the
recorded decisions to K, then the model's winning line, zero-model, the attempt's seed): **46 proven, 0 failed, 0
drift**. The kinds:

- **16 land: the Swamp played while Bloodghast is still on the battlefield**, so its landfall return (only from the
  graveyard, as the land enters) is wasted. 11 at the first decision, the rest at decision 2. All Luna (epoch 70).
- **13 skeleton: Reassembling Skeleton cast or returned for {1}{B}** (4 cast, 9 returned) when Gravecrawler, recast
  for {B} while Carrion Feeder is out, drains more with the same mana.
- **9 drainer: a drainer sacrificed** while a body that drains more with it alive was still to die.
- **4 response: a sacrifice while a body is still on the stack** (a Blood Artist being cast, 1; a Skeleton return, 3):
  the death drains without it.
- **3 feeder: Carrion Feeder sacrificed while still needed** (the Zombie Gravecrawler is recast with, 2; the last
  outlet, 1).
- **1 yield: a pass that yields in the end step with lethal still to sacrifice** (deepseek v4 pro). After a yield
  the harness asks again this turn only in the seat's main phase on an empty stack (`LlmSpikeGui`, the yield window
  skip), so it was never asked again. Every recorded yield was read to find this.

The step is early in most attempts: median model decision 4.5 (range 0 to 57), 14% of the way through (IQR 3% to
49%), with a median 28 more decisions after it (minimum 0; decisions after the step = n - K - 1, as in the splice note). Only 4 of 46 are within 7 decisions of the end. At every
step the position is exactly winnable (shortfall 0) and the choice leaves it 1 to 5 short (2 in 18, 1 and 3 in 13
each). The floor averages 0.54 (median 0.60, range 0.20 to 1.00): a uniformly random choice at the step keeps the win
about half the time, more than at splice's steps (0.37).

**The prompt never shows Bloodghast's rules text, and the land mistake is a timing error about it.** None of the 16
land games has the word "Landfall" anywhere in the game file. Puzzle prompts before harness epoch 86 did not show the
seat its own cards' text, and the brief is the turn limit and a card list. Yet at 11 of the 16 steps the seat's own
WHY names the landfall return from memory, and several plan it for later: "the landfall trigger can return Bloodghast
after its first sacrifice", "play the Swamp first to trigger Bloodghast's landfall return after sacrificing it". The
text says when it works ("Whenever a land you control enters, you may return Bloodghast from your graveyard"). Whether
showing it changes the play is an arm, not this note (Not determined).

## What was run (free: Python, then zero-model JVM games; no model called)

```bash
find /home/user -path '*/runs/*' -name puzzle-results.jsonl   # rows whose id is drain_* / drain2_*, solver_arm llm,
                                                              # a model lane (not greedy:, not script:)
python3 eval/puzzles/step_regret_board.py <the 65 attempt dirs> --out runs/where-lose-drain/board.jsonl
python3 eval/puzzles/step_regret_board.py <the 65 attempt dirs> --scripts runs/where-lose-drain/scripts --regret-only
python3 eval/puzzles/puzzle_baselines.py --build <build> --allow-stale-build line --pzl <the attempt's .pzl, sha-checked
  against its result row> --script runs/where-lose-drain/scripts/<game>-k<K>.txt --seeds <seed> \
  --out-dir runs/where-lose-drain/witness/<game>-k<K>
python3 eval/puzzles/step_regret_board.py <the 65 attempt dirs> --witnesses runs/where-lose-drain/witness
```

The attempt dirs (68 attempts; the main checkout, relative to it): `runs/puzzles/hard-gen-luna-check/hard-drain-*`
(Luna, epoch 70, 21), `runs/puzzles/hard-gen-luna-top10/hard-drain-*` (Luna, epoch 70, 6: three of the check's boards
at seeds 1 and 2), `runs/puzzles/hard-gen2-luna/hard-drain2-*` (Luna, epoch 70, 14), `runs/puzzles/frontier5/`,
`frontier5-fill/`, `frontier5-long/` (`hard-drain2-516c13f84175`, one attempt per model, epoch 72, 23), and
`runs/propose/e75-haiku-hard-{menu,propose}/hard-drain-*` (Haiku 5.5 on the CLI lane, epoch 75, 4, all solved; a
development lane, never a score). A copy of one luna-check attempt under `/home/user/demorec` was dropped as a
duplicate. Every attempt had the same tools off (`v4-faithful`, `MEMORY_TOOLS=0`, `LOOKAHEAD=0`); the Haiku cells
differ by propose arm and are never pooled.

The witnesses: 46 games, 2 JVMs, under the shared Forge lock, 4 minutes. Builds: the epoch-72 attempts on their own
build (`/tmp/frontier5-build-a782588`, overlay `39de3c6f271e`, 6 games). No epoch-70 build is left, so the 40 epoch-70
attempts ran on the epoch-82 build the splice witnesses used (`sr-wt/runs/harness-acceptance/sr-build`, overlay
`c57ec70bb79d`). Every one matched its recording question for question up to its step, so the build difference
changed nothing there. Outputs (git-ignored, 2.3 MB after the game files and JVM logs were deleted):
`runs/where-lose-drain/board.jsonl`, `jobs.json`, `scripts/`, `witness/*/puzzle-results.jsonl`,
`witness-verdicts.json`.

### The model (`eval/puzzles/board_drain.py`, `board-drain-v1`; `step_regret_board.py` now `-v3`)

`step_regret_board.family_of` picks the board's model from the result row's id (splice and splice2 as before, drain
and drain2 here, anything else refused), and `--regret-only` writes one witness script per attempt, at its step, at
any question kind. The drain model reads the seat's prompt: the hand, the battlefield, the graveyard, untapped Swamps,
the floating {B} and {C}, the land drop, every stack item (a creature spell, a Skeleton return, Bloodghast's trigger,
each drain trigger and its target, the outlets' no-op abilities), the opponent's life and the step (the second main
phase or the end step). It values the position by an exhaustive memoised search over every legal sequence: casts,
sacrifices to each outlet, Skeleton returns, the land drop, a pass that resolves the top item, and the main phase's
pass to the end step. Responses are modelled too, not assumed away. A sacrifice question is the outlet with the
victim pending; a trigger-order or target question is rebuilt from the activation's prompt with the recorded
sacrifice applied, then checked against the question's own prompt (battlefield, graveyard, opponent's life).
Assumptions (each refused when it fails, never guessed):

1. Every card is in `puzzle_gen.DRAIN_COSTS` (plus Ashnod's Altar), one copy each, every land a Swamp; the only life
   loss is a death trigger, each drainer on the battlefield as a creature dies draining 1, the dying one included.
2. The opponent has no cards, mana or permanents; the turn ending loses (`Turns:1`).
3. Casts and the land drop need an empty stack in the main phase; sacrifices and Skeleton returns are instant speed,
   the end step included (the pool empties there). A pass that yields leaves the seat asked again this turn only in
   its main phase on an empty stack, so the end step is never asked (this came from the deepseek attempt below).
4. Bloodghast's trigger exists only if it is in the graveyard as the land enters; Gravecrawler is cast from the
   graveyard only while Carrion Feeder is out (the only other Zombie); each Skeleton return returns it if it is in
   the graveyard then.
5. At a trigger-order or scry question nothing that matters is undecided; a target question decides only whether
   Blood Artist's drain hits the opponent.

The witness writer (`board_drain.walk`) plays the model's line from K. At each step it takes a move that keeps the
shortfall, resolving first wherever that keeps it. It writes "pass" only in a window the seat is asked, the rule
`drain_lines` uses (an outlet with a creature, or a payable Skeleton return), answers the sacrifice, trigger-order,
target, scry and Bloodghast questions each move raises, and stops where the killing drain resolves. Where only CANCEL
keeps the win at a sacrifice question (11 of the 16 sacrifice steps), the script cancels and plays the line from the
menu. `witness_verdict` is the splice one, read with this family's prompt and pick lists. Each change was a test
written red first (`eval/tests/test_board_drain.py`, 20 tests): the parse, the end step, the yield, responses, the
question rows, the witness line, CANCEL, the taxonomy (`board_drain.regret_kind`), and the value equal to
`puzzle_gen.drain_search`'s exhaustive maximum on 12 generated boards from an empty stack.

## Validation

| Check | Result | Trivial baseline |
|---|---|---|
| Attempts labelled | 68 of 68 (0 refused, 0 no model decision) | |
| Positions of the 22 solved attempts called winnable | 1241 of 1241 (0 model errors) | "all lost": 0 of 1241 |
| Shortfall never shrinks along an attempt | 68 of 68 | |
| Failed attempts with an exact regret step | 46 of 46 (0 non-monotone, 0 lost at the start) | |
| Regret steps whose winnable side is engine-witnessed | 46 proven, 0 failed, 0 drift | "all lost": 0 |

One model error was found and fixed during the survey. The first model read a yield as mattering only on an empty
stack, so the deepseek attempt (whose last decisions are end-step passes with drain triggers on the stack) read as
winnable to the end though it failed. The game file's `window` rows (`reason: yield:end-of-turn`) and
`LlmSpikeGui`'s yield skip show the seat is never asked again that turn outside its empty main phase. Modelling that
moved the attempt to an exact step at its yielding pass (K=62), and the witness there (PASS without the yield, then
sacrifices in the end step) solved.

How independent the witnesses are: none of the 46 scripts is the board's own `[solution]` line for line. Each is the
recorded prefix, then a line from a position the solution does not pass through (or a different order of the same
deaths). The end step is exercised by the yield witness, CANCEL by 11 and Bloodghast's trigger by the land steps'
lines.

## Coverage and the regret steps, per condition (never pooled)

| Model | Lane | Epoch | Run group | Attempts | Solved | Failed | Kinds at the step | Floors (kept / options) | Mean floor | K / model decisions |
|---|---|---|---|---|---|---|---|---|---|---|
| GPT-6 Luna | openrouter | 70 | hard-gen-luna-check | 21 | 1 | 20 | drainer 6, skeleton 5, land 4, response 3, feeder 2 | 1/4 3/5 4/6 3/4 3/4 1/5 3/5 4/5 2/5 1/5 2/5 1/5 2/4 3/5 3/6 1/5 2/3 1/4 1/3 1/4 | 0.46 | 30/46 0/26 0/22 3/32 10/35 28/35 4/36 7/50 29/41 21/50 15/31 15/41 8/24 2/29 0/25 19/36 31/52 24/26 15/38 14/43 |
| GPT-6 Luna | openrouter | 70 | hard-gen-luna-top10 | 6 | 0 | 6 | skeleton 3, land 2, feeder 1 | 2/3 2/3 3/6 3/6 3/5 2/3 | 0.60 | 11/43 9/41 2/43 2/49 4/30 24/37 |
| GPT-6 Luna | openrouter | 70 | hard-gen2-luna | 14 | 0 | 14 | land 10, skeleton 2, drainer 2 | 3/5 3/5 3/4 5/7 3/7 4/7 6/8 1/5 3/5 4/7 5/8 3/4 3/5 5/7 | 0.61 | 2/26 0/42 22/53 0/32 5/42 0/45 0/36 28/43 0/29 0/40 0/44 11/74 0/43 2/31 |
| GPT-6 Luna | openrouter | 72 | frontier5-fill | 1 | 0 | 1 | drainer | 1/5 | 0.20 | 40/63 |
| Claude Haiku 5.5 | openrouter | 72 | frontier5-fill | 1 | 0 | 1 | skeleton | 5/7 | 0.71 | 2/45 |
| Grok 4.3 | openrouter | 72 | frontier5 | 1 | 0 | 1 | skeleton | 5/7 | 0.71 | 2/44 |
| jev 1.14 | jev | 72 | frontier5-fill | 1 | 0 | 1 | skeleton | 3/5 | 0.60 | 4/8 |
| Mistral Medium 3.5 | openrouter | 72 | frontier5-fill | 1 | 0 | 1 | response | 1/5 | 0.20 | 2/37 |
| DeepSeek v4 pro | openrouter | 72 | frontier5-fill | 1 | 0 | 1 | yield | 2/2 | 1.00 | 57/58 |
| 16 other models (Sonnet 5.5 twice) | openrouter | 72 | frontier5, -fill, -long | 17 | 17 | 0 | | | | |
| Claude Haiku 5.5 | CLI | 75 | propose menu / propose | 2 / 2 | 4 | 0 | | | | |
| **All** | | | | **68** | **22** | **46** | **land 16, skeleton 13, drainer 9, response 4, feeder 3, yield 1** | | **0.54** | median 4.5 |

K is the model decision's index in the attempt / the attempt's model decisions (sacrifice, trigger-order, target and
scry questions count; the attempts are long because each death is three to five decisions). The floor is over the
options shown. At the yield step both shown options keep the win (sacrifice now, or PASS without the yield); the
seat lost through the yield field, which no option count sees.

Rates only where a cell has 6 or more failed attempts (Wilson 95%):

- `hard-gen-luna-check` (n=20): drainer 6 (0.15 to 0.52), skeleton 5 (0.11 to 0.47), land 4 (0.08 to 0.42),
  response 3 (0.05 to 0.36), feeder 2 (0.03 to 0.30). Solve rate 1 of 21 (0.01 to 0.23).
- `hard-gen2-luna` (n=14): land 10 (0.45 to 0.88), skeleton 2, drainer 2 (each 0.04 to 0.40). Solve rate 0 of 14 (0.00
  to 0.22). The drain2 generator keeps boards whose plain plans ("land first" among them) fall 1 to 3 short, so the
  land trap is built into more of them; that, not the model, may be why land dominates here.
- `hard-gen-luna-top10` (n=6) replays three of the check's boards at two seeds, so its attempts are not independent of
  the check's. `1ae4ca88e330`: skeleton in all three attempts; `343f1afaa5c8`: land in both top10 seeds, skeleton
  (later) in the check; `668e60a7c453`: skeleton in the check and one top10 seed, feeder in the other.
- Every other model: 1 failed attempt on one board (`drain2_516c13f84175`), n too small. On that board 17 of 23
  attempts solved, 16 models. Its six failures are four kinds (skeleton 3, drainer, response, yield), so no claim that
  one model loses differently from another.

By kind (all cells; the floor is the share of options that keep the win; K the model decision index):

| Kind | Steps | Mean floor | K (sorted) | Short after the step |
|---|---|---|---|---|
| land | 16 | 0.61 | 0 x11, 2 x5 | 2 (10), 3 (5), 1 (1) |
| skeleton | 13 | 0.70 | 2 2 3 4 4 7 9 10 11 11 22 24 31 | 1 (6), 3 (5), 2 (2) |
| drainer | 9 | 0.30 | 5 8 15 15 15 21 28 30 40 | 2 (4), 1 (3), 5 (2) |
| response | 4 | 0.21 | 2 14 19 28 | 1 (3), 2 (1) |
| feeder | 3 | 0.42 | 4 24 29 | 3 (2), 2 (1) |
| yield | 1 | 1.00 | 57 | 3 |

The sacrifice kinds (drainer, response, feeder: 16 steps) have low floors (only CANCEL keeps the win at 11). The
play-menu kinds (land, skeleton) have high ones: most options there are another sacrifice or tapping a Swamp, which
keep it. Along the recorded line before the step, a uniform chooser at the same positions would have kept the win
at every one with probability median 0.22 (IQR 0.009 to 0.81, 46 attempts). That is along the seat's own path and on
failed attempts only, so it says how narrow the earlier gates were, not how a random player scores.

## What the prompt showed at the step (6 steps read)

1. **land** (`runs/puzzles/hard-gen2-luna/hard-drain2-0ab6f6ae05d9/`, K=0, Luna, epoch 70). The board reads
   `Bloodghast 2/1 [untapped] @5, Carrion Feeder 1/1 ...`; the hand `Swamp @4`; the menu `PLAY LAND Swamp @4`.
   Nowhere does the prompt give Bloodghast's text, and no line of the game file contains "Landfall". The reply: "Play
   the Swamp to enable Bloodghast's landfall return and add one more black mana source", with Bloodghast on the
   battlefield. Across the 16 land steps, 11 WHYs name the landfall return and 5 give mana as the reason.
2. **skeleton** (`runs/puzzles/hard-gen-luna-check/hard-drain-0dbe59d4513a/`, K=3). Five untapped Swamps, a Cruel
   Celebrant trigger on the stack, `Gravecrawler {B} 2/1 @1 (payable, not castable now)` in hand, and the menu offers
   `ACTIVATE Reassembling Skeleton ... [{1}{B}: Return ...]`. The seat's own plan says "repeatedly cast and sacrifice
   Gravecrawler for all five black mana", then it returns the Skeleton, spending 2 of the 5. Gravecrawler's
   graveyard-cast text appears only on a menu that can cast it (empty stack, Feeder out), and the hand line shows no
   rules text; that Carrion Feeder is a Zombie is never shown by the engine (the seat states it in its own notes).
3. **drainer** (`runs/puzzles/hard-gen-luna-check/hard-drain-40008bbe48e5/`, K=22). The pool `{B:1}`, Gravecrawler in
   the graveyard, all three drainers' triggers on the stack with their text. The reply sacrifices Blood Artist "and
   leaves Feeder as the Zombie needed to recast Gravecrawler": the {B} should have recast Gravecrawler first, while
   all three drainers were alive. The facts are on the screen: a model mistake.
4. **response** (`runs/puzzles/frontier5-fill/mistralai_mistral-medium-3-5__hard-drain2-516c13f84175/`, K=2). The
   stack reads `#1 spell Blood Artist 0/1 @15 (you): Blood Artist - Creature 0 / 1`. The reply: "sacrifice Viscera
   Seer ... This will trigger the death effects of Blood Artist", which is still a spell. Visible: a model mistake.
5. **feeder** (`runs/puzzles/hard-gen-luna-check/hard-drain-7e1014a37b38/`, K=24). Carrion Feeder is the only outlet
   left, with Cruel Celebrant and Zulaport Cutthroat beside it; the reply sacrifices the Feeder "so Cruel Celebrant
   and Zulaport Cutthroat remain to trigger on its death and on any further sacrifices", though no outlet remains.
6. **yield** (`runs/puzzles/frontier5-fill/deepseek_deepseek-v4-pro__hard-drain2-516c13f84175/`, K=62, END_OF_TURN).
   The stack holds a drain trigger and the Feeder's counter, and the menu is the Feeder's sacrifice or PASS. The
   seat's plan: "PASS now ... Then at the next end-of-turn priority, sacrifice Zulaport Cutthroat". It passed with
   `YIELD: end-of-turn`, was never asked again, and lost at 3 life short. The hint under the menu is accurate ("until
   then you are not asked in windows where nothing of your opponent's is on the stack ... your own main phases always
   ask too"). It does not say that in the seat's own end step this means never again this turn.

**Harness defect candidates** (not filed):

- *The seat is not given its own cards' rules text in a puzzle* (the splice note's candidate, fixed since in harness
  epoch 86 by `YOUR CARDS`; every attempt here predates it). Here it bears on the largest kind: Bloodghast's landfall
  text is in no prompt of any land game, and the seat's recall gets its timing wrong. Carrion Feeder's Zombie type
  (what Gravecrawler's recursion depends on) is never stated by the engine, and Gravecrawler's own text appears only
  when it is castable from the graveyard.
- *A yield in the seat's own end step forfeits the rest of the turn with no warning.* The skip is the documented yield
  behaviour, but at an end-step window of the seat's own turn it means never being asked again. In a one-turn puzzle
  that is the game, and the hint does not say so. One attempt; worth a look, not a finding.

## Not determined

- **The lost side of each step and the floors' other options.** Witnessed: the kept option keeps the win. That the
  seat's choice loses (and that the options the model calls losing lose) is the model's: a loss needs every line
  played, which a script cannot show. The model's agreement with `drain_search` on generated boards and the 1241
  solved positions support it but do not prove it in the engine. The exact search (`step_regret.py`) could decide
  the late positions; it was not run.
- **Whether the missing card text causes the land mistake.** The arm is the same boards with `YOUR CARDS` (epoch 86 or
  later). Until then the missing text and the timing error only co-occur, and 11 of 16 seats recalled the ability
  anyway.
- **A model's ability.** Only Luna epoch 70 has cells big enough for rates. Every other model is one attempt at one
  board, n too small.
- **Families still without a model:** elf and elf2 (22 failed attempts; `elf_search` exists), hoofdrain (44 failed;
  needs the Craterhoof attack), fling, ping, storm, and the 42 splice2 attempts the splice model refuses.
