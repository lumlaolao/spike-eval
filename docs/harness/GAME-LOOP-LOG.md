# Game loop log

One entry per match played under `.claude/skills/game-loop`: what the sheet and the reading found,
what was fixed, what is open. Newest last. Read the tail before playing.

### block of 2026-10-13  seeds 50-52  Mardu Energy (seat) vs Mardu Energy (Forge AI)  10 games started, 6 finished  $0.35  build a76bd78d6421

Played as two arms of a small block before the loop was one game at a time; read game by game.

- FIXED  One creature that can attack was attacked with, unasked (`attackers(forced-only-legal)`):
  TAKEN x6 in seed 50 swap 1 alone, the seat's Guide of Souls every turn. Test
  `aSingleCreatureThatCanAttackIsTheModelsDecisionNotAForcedAttack`. Commit 37cff95a.
- FIXED  Scry 1 / surveil 1 kept on top, unasked. Same cause: `n_options==1` read as "forced" for a
  subset decision. Test `aSingleSurveiledCardIsTheModelsDecision`. `defect_analyzer.py` no longer
  calls the correct behaviour a mislabel.
- FIXED  The retry of a play decision carried no state and no menu. Seed 50 swap 0 turn 6: the model
  used both tools searching its archive for the question; LOST after 56 s. Also seed 51 turn 12 (a
  PASS on its own main phase) and seed 51 turn 7. Test
  `theRetryOfAPlayDecisionCarriesTheStateAndTheMenuAgain`.
- FIXED  With its calls spent the model wrote call markup as text and the ask returned nothing. It
  is told once that the tools are spent. Test
  `aSeatThatWritesCallMarkupAfterItsToolsAreSpentIsAskedOnceMoreAndAnswers`.
- FIXED  Tool rounds were recorded as failed attempts (`first_failed_reply`, `retry_ms`), so a fifth
  of decisions looked retried.
- OPEN   The seat asks to preview `PLAY LAND` and `PASS` (bad-args: 13 and 6 of 92 requests in
  runs/lines/ctl2/native-lookahead). A line that starts with a land drop cannot be asked for.
- OPEN   `mulligan(heuristic-keep n/7 lands)`: the harness keeps 2-5 land hands without asking.
  The loop now plays with `MULLIGAN_HEURISTIC=0`; whether the default should change is undecided.
- OPEN   Smart-tier replies cut off at 4000 tokens (`truncated reply` x5 to x8 a game): the budget
  goes on reasoning and the retry costs 20-55 s. Not yet read closely.
- OPEN   `inspect "Cat Warrior Token"` finds nothing: a token has no card-database entry, and the
  model asked by name instead of by handle.

### g01  2026-10-13  seed 53  Mardu Energy (seat) vs Burn (Forge AI)  won T10  $0.03  build 4b4ecc16

Sheet: TAKEN 0, LOST 0, TOOL 0 (3 requests, all served). The fixes of the block held: the mulligan,
both surveils and every attack were asked of the model.

- FIXED  **A preview asked the model to play a game that did not exist.** Read in the log: before
  each `lookahead` line the seat was asked `attackers: 2 can attack` and `confirm-trigger [Guide of
  Souls]`, in its main phase, and an `[OBSERVATION]` banner appeared each time (8 in a one-seat
  game). The engine scores a previewed copy by simulating the coming combat on a second copy, and
  that copy's seat was built with the full model controller. The calls were real (money, 10-25 s a
  preview) and their answers were written to the decision log as attackers, confirm-trigger and
  damage-assign rows (7 attacker rows in a game with 5 attack steps). Every player the engine
  creates during a preview is now an engine-AI player. Test
  `everyPlayerCreatedDuringAPreviewIsCreatedInsideThePreview`; the `lookahead` acceptance stage
  fails on a second model controller; `match_review.py` reports it under LOG.
  Consequence for earlier numbers: decision counts, model calls per turn and cost in every
  `LOOKAHEAD=1` arm so far include these phantom decisions.
- clean  Turn-2 discard looked wrong (8 cards after a land drop) and was not: Goblin Guide's trigger
  had put a land in the seat's hand.
- OPEN   One smart-tier reply was cut off at 4000 tokens on a five-option turn (26 s).

### g02  2026-10-13  seed 54  Death & Taxes (seat) vs Grixis Reanimator (Forge AI)  won T12  $0.02  build b8bf473c

Sheet: TAKEN 0, LOST 0, SLOW 0. A decisive win: the seat kept a 5-land hand, played Stoneforge Mystic
into Batterskull, Wastelanded their Underground Sea, then attacked for lethal. The two TOOL rows were
the whole of the reading.

- FIXED  `simulate` refused a PLAY LAND option, so the seat could not preview the line it kept asking
  for (TOOL bad-args, line 14, T6): it called `simulate {"option":4,"then":[3]}` — PLAY LAND Wasteland,
  then Stoneforge Mystic's ability — and got "`option` must be the number of a CAST or ACTIVATE option
  (1..3)". The tool's numbers are now the decision's own menu numbers (CAST/ACTIVATE then PLAY LAND;
  PASS is not previewable), and a land drop may start a line, played on the copy so it can pay for a
  later step. 13 of 92 preview requests named a PLAY LAND option in runs/lines/ctl2/native-lookahead
  and all were refused. Test `aLineThatStartsWithALandDropIsPreviewed`. Stamps: seat-tools-v3,
  lookahead-preview-v3.
- OPEN   the other half of item 1 is unchanged: a spell that was not already on the menu when the land
  is played (the spell the land enables) still cannot be named in one call, so "land, then that spell"
  is only previewable if the spell was a menu option at the time of the decision.
- FIXED  the land preview first went through `GameSimulator.simulateSpellAbility` and the copy's land
  branch recursed with the LIVE ability, so the AI picker replayed it and the heap ran out
  (acceptance nativetool stage, 2 of 19 previews, OutOfMemoryError in `Spell.<init>`). The copy's own
  land ability is played directly instead.
- OPEN   the same turn's other tool call, `memory_search "ForgeAI deck"`, returned Matches=0 (line 15,
  T6). The opponent's deck identity is hidden information and not in the seat's archive; a word-split
  fallback would return the entries that mention "ForgeAI". Low; not fixed.
- OPEN   one smart-tier reply was cut off at 4000 tokens on the T6 five-option turn, retried with
  reasoning disabled: 66 s + 20 s on that one decision (log line 57). Open item 3, unchanged.
- NOTE   plan_audit reports one HOLD-VIOLATION at T6 and it is a false positive: the clause "play
  Wasteland and hold Swords to Plowshares" holds Swords, and the splitter does not split on "and".
- clean  mulligan (asked, 5 lands kept), all three attack steps, targets, the served Wasteland preview.

### g03  2026-10-13  seed 55  Goblins (seat) vs UR Delver (Forge AI)  lost T14  $0.05  build 5ae9bd3b

Sheet: TAKEN 0, LOST 0, SLOW 0; one TOOL row. Two flipped Delvers and a Bolt closed it out; the
seat's own line (Aether Vial, Goblin Matron into Krenko, Wasteland on a Volcanic Island) was sound.

- FIXED  the seat's `simulate {"option":3}` on a menu whose 3 was PASS was refused, and the refusal
  was counted as a form `TOOL REFUSED` because the message shipped in #86 opens with a backticked
  argument name ("ERROR: `option` ..."), which `tool_audit.FORM_ERRORS` claims. A call naming a menu
  option the preview does not cover is a bad argument, not a form refusal. Test in
  `test_tool_audit.test_a_refusal_is_about_form_not_about_what_the_tool_found` (runs/loop/g03:211).
- FIXED  `plan_audit` reported a HOLD-VIOLATION at T9 that was a false positive, the second in two
  games (g02 T6 was the first). The clause splitter did not split "play Wasteland and hold Swords",
  so the hold on Swords was charged to Wasteland; and a bare "later" ("use it later") counted as a
  hold. It now splits an `and` that joins two verbs, keeps an `and` that joins two objects of one
  hold, and no longer treats "later" alone as a hold. Test
  `test_plan_audit.test_a_play_the_plan_endorses_is_not_a_hold_violation`. On the recorded arms
  (`eval/plan_audit.py runs/lines/ctl2/<arm>`): textv2 12 -> 7 of 131, native 18 -> 15 of 173,
  native-lookahead 10 -> 7 of 162; every case removed in `native` was the same "cast X and hold Y"
  false positive.
- OPEN   item 1's other half, PASS: the seat asked to preview PASS (the tool says PASS cannot be
  previewed; 6 of 92 requests in runs/lines/ctl2/native-lookahead, 1 here). Whether the harness should
  answer "PASS: no change" or keep refusing is a call for the owner.
- NOTE   engine noise, not the harness: `X Did not have activator set in SpellAbilityRestriction.canPlay()`
  (Force of Will, Scalding Tarn, Delver of Secrets) is the ForgeAI enumerating its own hand, and the
  engine self-heals by setting the activator. 4 here, 1 in g01, 0 in g02.
- clean  mulligan (asked, 5 lands kept), every land drop, the Vial / Matron / Krenko sequence, the
  Wasteland target, all attack steps.

### g04  2026-10-13  seed 56  Sneak and Show (seat) vs Miracles (Forge AI)  won T14  $0.05  build 21011774

Sheet: TAKEN 0, LOST 0, 4 TOOL exhausted, one SLOW, 5 truncations in the log. 553 s for the game
(g02 266 s, g03 306 s). The seat won by Sneak-Attacking Emrakul, Griselbrand and Atraxa in.

- OPEN   item 3 is worst on this deck, and the retry mechanism is now clear enough to name. The
  smart tier's 4000-token cap truncates; the truncation retry shares the ask's single 90 s deadline;
  if the first attempt was slow the retry is starved and the ask goes to bridge-down, which the
  caller answers with a WHOLE new attempt. T8 (log lines 57-63; jsonl:14 attempts=3 retry_ms=90509):
  truncation at ~67 s -> retry cut at 22616 ms of the 90000 ms deadline -> non-answer -> bridge-down
  -> the same ask retried (70 s + 31 s) and answered. The model took ~160 s to decide to play a land.
- NOTE   the cap bites by deck: 5 truncations here against 1 in g02 and g03. Sneak and Show's
  decisions (a Sneak Attack line, the Show and Tell choice) reason longer, which is the shape the
  owner is weighing for the eval across models.
- MODEL  4 TOOL exhausted (17% of requests): the prompt says "Tool calls available this ask: 2", the
  model spent both -- often re-reading the same evidence (165, a 43-character observation) in four
  different decisions -- and then asked for a third on the same decision (jsonl:71, 87). The budget is
  stated and the over-ask is the model's; recorded, not fixed.
- clean  mulligan (asked, 3 lands kept), the Show and Tell and Sneak Attack lines, the Atraxa ETB
  pick, Griselbrand's draw-7, Emrakul's annihilator, all attack steps, the one forced number.

### g05  2026-10-13  seed 57  Cephalid Breakfast (seat) vs Lands (Forge AI)  won T5  $0.05  build 21011774

Sheet: TAKEN 0, LOST 0; 2 TOOL bad-args; 1 SLOW (7993 ms); 1 truncation in the log. The seat won on
turn 5 (WinsGameSpellEffect) by milling its library with the Nomads en-Kor / Cephalid Illusionist
loop and flashing back Dread Return for Thassa's Oracle with an empty library.

- FIXED  the preview ignored the target the seat named and reported the source instead. It asked for
  `simulate {"option": 1, "target": "Cephalid Illusionist 1/1 [untapped] [sick] @75"}` (jsonl:57)
  and the report came back "if you activate Nomads en-Kor [assuming target: Nomads en-Kor]: you no
  change" -- a no-op on the ability the whole game turned on. `PlayerLookahead.pickTarget` asked only
  whether the candidate's NAME contained the hint, and the seat sends the label the board printed,
  which is longer than the name, so a label never matched and the pick fell silently to
  `defaultTarget`. The match now runs both ways and the longest name wins. Test
  `LookaheadProbeTest.previewCastHonoursATargetHintThatQuotesThePrintedLabel`. Stamp
  lookahead-preview-v4.
- FIXED  plan_audit charged a hold to a card its clause named only as the consequence: "keep
  targeting Illusionist to mill the library so Narcomoebas and Dread Return fill the graveyard" held
  the Illusionist, and the Dread Return flashback is that plan succeeding. The splitter now breaks on
  `so`. Test `test_a_hold_is_not_charged_to_the_card_a_consequence_clause_names`. No arm rate moved
  (all ten arms in ARCHITECTURE-REVIEW Part 4 identical), so no published number changed.
- OPEN   item 3 (truncation, shared 90 s deadline) -- one more occurrence here: line 170 of the
  log, [TRUNCATED->RETRY-NO-REASONING] at 4000, then replied 22154 ms. Single, not starved.
- OPEN   the other half of the friction, a cost call: with the target honoured the seat's first ask
  answers it, but "one preview per decision" still refused a corrected retry (jsonl:59, 119). Whether
  a second preview should be allowed when the seat names a different target is a budget decision, not
  a bug.
- OPEN   the mill loop cost 17 `order` decisions on the SMART tier -- 16% of the game's 105 model
  calls, for the graveyard order of three milled cards, which the seat itself called irrelevant
  ("Order doesn't change the outcome"). `order` is not in TIER_FAST_TYPES, so it goes to the slow,
  expensive model; routing it to the fast tier is a tradeoff for the owner, not fixed here.
- MODEL  one of those order decisions took 2 attempts (7993 ms): the first reply reasoned about the
  mill order and omitted the required `PICKS:` tag. The retry recovered it.
- NOTE  "hidden has neither ManaCost nor Color" at the Brainstorm preview (line 48) -- engine warning
  during a copy-preview of a cantrip. Not investigated; harmless-looking.
- clean  mulligan (asked, 4 lands kept), the combo execution, Force of Will reserves, all target
  choices (every activate correctly targeted Cephalid Illusionist), Dread Return flashback into
  Thassa's Oracle.

### g05 follow-up  2026-10-13  the two items the owner picked up

- FIXED  PASS previews (item 1's other half). `simulate` now accepts the PASS option and answers "if
  you pass, nothing resolves: the board, hands, life and graveyards are unchanged. A preview cannot
  play out the opponent's turn." without making a copy. The tool description and
  `eval/tooluse/tools.json` say so, `seat-tools-v3 -> v4` with the acceptance pin moved, and the
  acceptance's fake model now asks for PASS every third preview, so the path is proved end to end.
  Tests: `LookaheadProbeTest.previewPassSaysNothingResolvesAndDoesNotPlayTheOpponentsTurn`,
  the fake server's PASS assertion, and the nativetool stage's "no PASS preview was served" check.
- FIXED  the 4000-token cap (item 3), as ONE uniform budget for every model: smart 8000/150s with no
  reasoning hint, fast 1024/45s with reasoning off, replacing the per-model 4000/8000/16000 table.
  Two findings decided it. OpenRouter's `reasoning.max_tokens` is Anthropic-style and the authorised
  model does not accept it (its model entry has no `supports_max_tokens`), so reasoning cannot be
  capped separately; and the harness was sending deepseek `effort:"medium"`, which is NOT in its
  `supported_efforts` (max/high/low) — the model's default is high, so the hint was steering with a
  value the model does not have. 8000 is 2x the measured smart p99 (3811 of 5346 completions across
  runs/lines and runs/loop; median 710). `model_cfg_sha` moves, so runs either side are not pooled.
  Tests: `modelCfg_everyRowGetsOneBudgetSoAnEvalComparesModelsUnderOneInfra`,
  `everyModelSpecResolvesToTheSameConfig`, and the canonical-string pin.
- OPEN   the one-preview budget: after a preview, the seat's corrected retry is still refused
  ("one preview per decision"). A cost call, not a bug.
- FIXED  `order` decisions now go to the fast tier (`...,order` in the game-loop command's
  TIER_FAST_TYPES, with the reason stated there): 17 of g05's 105 model calls went on an order the
  seat itself called irrelevant, over an already-legalised menu. It changes which model answers, so
  it waited for the owner's call.

### g06  2026-10-13  seed 58  Painter (seat) vs Infect (Forge AI)  lost T12  $0.08  build 8ad60d78

Sheet: TAKEN 0, LOST 0, TOOL 0 (8 of 8 served), SLOW 0; 3 truncations. 921 s.

- OPEN   the cap data point g06 existed for. At the uniform 8000, 3 of 33 smart calls truncated and
  were retried at 4096 with reasoning disabled. Smart completions on this deck: median 2042, max 7615
  -- the cap sits AT the tail, and 5 of 33 exceeded 4000, so all five would have truncated under the
  old deepseek cap. 8000 was picked as 2x the CROSS-RUN p99 (3811), which understates a deck that
  reasons this long. 16000 is the value with headroom; the owner's call before g07.
- DEFECT a colour choice was taken by the harness four times ("color(default)->first", T5/7/9/11) with
  no model row and no options recorded -- on the deck whose signature card, Painter's Servant, is a
  colour-naming one. `chooseColor` asks only when `colors.countColors() >= 2 && smartOk()`, and the
  auto row records neither, so a forced single-colour set cannot be told from a decision taken because
  the smart budget was gone. `match_review` does not count it either (TAKEN 0), so the sheet missed
  it. FIXED: the fallbacks now record the legal-set size -- "color(forced)->first" for a single legal
  colour, "color(default)->first of N legal" otherwise -- and match_review counts an unasked
  multi-option choice as TAKEN (UNASKED_CHOICE). Tests: classify_auto unit test plus the harness
  compile. Games recorded before this still read "color(default)->first" with no count.
- PUZZLE T11-ish: the plan said "Hold Lotus Petal (no mana to pay {3} for Grindstone)" and the model
  played it (jsonl:134). The stated hold was not honoured.
- NOTE   plan_audit's other hit, "keep Grindstone + keys untapped" against playing Grindstone, reads
  as a false positive: "keep X untapped" holds the ACTIVATION, and casting the artifact does not tap
  it. A third FP class, noted not fixed.
- NOTE   the turn-by-turn read of the Saga chapters and the Karn/Tezzeret loyalty abilities was NOT
  done this pass; only the sheet, the truncations, the colour autos and the two plan clauses were
  examined. The next session on this deck should start there.
- clean  mulligan (asked, twice), the tool sheet (8 requests, all served), attackers/blockers.

### g07  2026-10-13  seed 59  Elves (seat) vs Food Chain (Forge AI)  won T14  $0.10  build 4eca84d4

Sheet: TAKEN 0, LOST 0, TOOL 1 (9 of 10 served), SLOW 0; 1 truncation. 938 s.

- FINDING the cap IS the model's thinking budget, and the evidence is that the observed completion
  maximum tracks the cap across three datasets and three decks: the cross-run sample at a 4000 cap,
  max 4000 (clipped); g06 at 8000, max 7615 (0.95 of it); g07 at 16000, max 14989 (0.94). OpenRouter
  derives the reasoning budget from max_tokens through the effort fraction, deepseek's default effort
  is "high", and the harness sends NO effort at all (the unsupported "medium" was removed rather than
  replaced), so the cap sets how hard the model thinks. Raising the cap bought thinking rather than
  headroom: g07 cost $0.10 against g05's $0.05 over a comparable 101 model calls. And the 8000 value
  was circular -- it came from a cross-run p99 of 3811 measured against a 4000 cap, i.e. 0.95 of that
  cap. This is the eval-validity problem in its sharpest form: two models run at different caps were
  never thinking equally hard.
- CONFIRMED the cap IS the thinking budget, and the experiment settled it. The same deck at two caps,
  one seed pair, only OPENROUTER_MODEL_CFG changed (`runs/loop/cap6000`, seed 60, Elves/Food Chain;
  `model_cfg_sha` 4a33b2fd against g07's 23c942bc):

  | | median | p90 | max | over 4000 |
  |---|---|---|---|---|
  | cap 16000 (g07) | 1382 | 4876 | 14989 | 7 of 41 |
  | cap 6000 (cap6000) | 1203 | 3978 | 4600 | 3 of 35 |

  The maximum tracks the cap and the median barely moves: the cap clips the TAIL, which is exactly the
  hard decisions. OpenRouter derives the reasoning budget from max_tokens through the effort fraction,
  deepseek's default effort is "high", and the harness sends no effort hint (the unsupported "medium"
  was removed rather than replaced). So two models run at different caps were never thinking equally
  hard, and the earlier sizing was circular -- 8000 came from a cross-run p99 of 3811 measured against
  a 4000 cap, i.e. 0.95 of that cap. 16000 stands because it does not clip; the real fix is an
  explicit, supported `reasoning.effort` that separates the thinking budget from the answer budget,
  and that needs the per-model supported_efforts from OpenRouter's models endpoint.
- OPEN   the one-preview budget bit again: `simulate {"option": 2, "then": [5]}` refused with "one
  preview per decision" (jsonl:6). Second game running, so it stops being marginal.
- NOTE   the new unasked-choice TAKEN rule was NOT exercised here: g07's only auto rows were three
  `damage-assign | forced(only-legal)`, correctly counted as forced. Elves and Food Chain raise no
  colour or type decisions, so the rule still rests on its unit test alone.
- NOTE   as in g06, the turn-by-turn read of the attacker/blocker steps was not done; the sheet, the
  auto rows, the truncations and the token distribution were.
- clean  the tool sheet (9 of 10 served, nothing REFUSED/FAILED/empty/exhausted), mulligan, the one
  target.
- FIXED  a bug shipped in #89, found by reading OpenRouter's model metadata rather than by a game:
  the uniform fastReasoningEffort="none" sends reasoning:{enabled:false}, which a MANDATORY-
  reasoning model rejects. x-ai/grok-4.7 reports mandatory=true (supported_efforts
  xhigh/high/medium/low), so every fast-tier call on that row would have failed -- and the fast
  tier is high-frequency. grok now gets the lowest effort it supports ("low") there; every
  other row keeps the disable. The uniformity test asserts the exception explicitly.
- FINDING for the next change: "high" is supported by all three OpenRouter families (deepseek
  max/high/low, kimi max/high/low, grok xhigh/high/medium/low), so an explicit, uniform
  reasoning.effort is feasible -- that would STATE the thinking budget instead of leaving it an
  emergent product of the cap.

### g08  2026-10-14  seed 61  Grixis Reanimator (seat) vs Mardu Energy (Forge AI)  lost T5  claude -p haiku  build b425298 + uncommitted 69aa75e  runs/loop/g08

First games in a cloud container (Linux, JDK 17, Xvfb) and on the Claude CLI lane (`--smart-a haiku`).
Sheet: TAKEN 0, LOST 0, TOOL 0, SLOW 0; analyzer PASS. Everything below came from reading the rows.

- FIXED  alternative costs never reached the play menu. Unmask sat in hand beside four black cards
  from T1 ("Unmask isn't offered as an option", jsonl:17). getSpellAbilities drops alternative costs
  when the spell is also castable normally; the menu never re-expanded them. Hid Force of Will,
  Force of Negation, Daze, Fireblast in seven pool decks. Test: aSpellCastableOnlyByItsAlternativeCostIsOffered.
- FIXED  the card an exile cost takes (Unmask, Force of Will, Food Chain) was Forge AI's, no row.
  Test: anExileFromHandCostIsTheSeatsChoice.
- FIXED  Cabal Therapy targeted the seat itself to bin Griselbrand (jsonl:25, :37); the separate name
  ask never said who the target was and named Ajani, then Amped Raptor, cards in the opponent's hand
  (jsonl:28, :41). Test: aNameAskStatesWhoTheSpellTargets. observation-faithful-v13.
- FIXED  a claude -p seat recorded prompt_tokens=-1 on every row and served_*=unknown; the CLI's
  result line carries both. Test: theCliResultLineGivesTheServedModelAndTokens.
- FIXED  infra: a startup error (missing DECK_A) left the JVM idle for 5 minutes; md5 (macOS-only) made
  the acceptance classpath guard pass on anything on Linux; a pytest needed an uncommitted corpus.
- OPEN   fast and smart tiers are separate CLI conversations: the fast tier chose the Cabal Therapy
  flashback and the smart tier, asked for its sacrifice, said "which I didn't choose" (jsonl:39).
  The prompt carried the cost and source; the answer was right. Watch for it on other cost asks.
- OPEN   the seat-42 canaries read floor decisions=1081 (pinned 1068) and random 16 turns (pinned 19)
  in this container on untouched main and on a955cd8, the commit that pinned them, under JDK 17 and
  21. The pin is environment-specific, not stale; the cause (OS, locale, preferences) is not found.
- NOTE   the B seat (Forge AI) is stamped smart_b=opus on the game line though no model plays it.
- clean  mulligan (asked), targets, blocks, the auto rows (one forced colour, one forced entity).

### g09  2026-10-14  seed 62  UR Delver (seat) vs Burn (Forge AI)  lost T5  claude -p haiku  build a33efdc (uncommitted at play time)  runs/loop/g09

Sheet: TAKEN 0, LOST 0, TOOL 0, SLOW 0; analyzer PASS. served_smart_a=claude-haiku-5-5, 2.15M prompt
tokens over 28 calls (`python3 eval/cost_report.py runs/loop/g09`; the persistent CLI conversation is re-sent each ask).

- FIXED  Ponder's order was reversed. Four Ponders: each time the seat said "put Chain Lightning on
  top so I draw it now", named it first, and drew something else; it was still in the next Ponder's
  top three (jsonl:26, :37, :45, :51). The engine moves the list to the top one card at a time, so
  the last card ends on top; "(first = goes in/on top first)" read as first = top. Faithful prompt now
  asks top-down and the router reverses, as Forge's human controller does.
  Test: aLibraryOrderIsAskedTopDownAndTheFirstPickEndsOnTop.
- FIXED  Daze cast by its alternative cost (the g08 fix working, jsonl:7), but which Island the
  return cost bounced was Forge AI's. Test: aReturnToHandCostIsTheSeatsChoice.
- OPEN   CostTapType (Heritage Druid taps three Elves) is still paid by Forge AI: AiCostDecision keeps
  its "already tapped for this payment" set private, so a subclass cannot copy its legality safely.
  (Fixed later by #105: the seat activates the Druid before the engine pays.)
- clean  mulligan, Bolt target (Eidolon), blocks, the shuffle confirms.

### g10  2026-10-14  seed 63  Sneak and Show (seat) vs UR Delver (Forge AI)  won T8  claude -p haiku  build 6b7b261  runs/loop/g10

Sheet: TAKEN 0, LOST 0, TOOL 0, SLOW 0; analyzer PASS. 20 calls, 1.25M prompt tokens.

- CONFIRMED the Ponder order fix: "order [Ponder] -> Library (top first): Brainstorm, Spell Pierce,
  Volcanic Island" (log:57), i.e. the seat's named-first card on top.
- FIXED  ...and then the fast tier said YES to Ponder's shuffle (jsonl:11, "CHOICE: 1", no reasoning),
  undoing the order the smart tier had chosen two calls earlier; it is a separate conversation and
  never saw the order. The shuffle ask now states the order it would undo.
  Test: theShuffleAskAfterAnOrderSaysWhatTheShuffleWouldUndo.
- clean  mulligan, Show and Tell's put-in (Emrakul, asked), both scries, attacks.


### g08  2026-10-15  seed 71  Mardu Energy (seat) vs UR Delver (Forge AI)  draw (cap) T13  deepseek-v4.1-flash one-tier  build feat/one-tier..docs/bottleneck-contract  runs/loop/g08

First game on the bottleneck stack (#143-#155): SKIP_HEURISTICS=0, --one-tier. Sheet: TAKEN 0, LOST 0,
SKIP 0, TOOL 0, SLOW 1; analyzer PASS; $0.15.

- WORKS  no harness-guessed passes: windows were 100 nothing-usable and 13 yield:end-of-turn (the seat
  asked for 3 yields). 16 asks carried THE PLAY YOU CHOSE MOST RECENTLY; Bowmasters' trigger target was
  the seat's; one mana-sources ask ("keep both Scrublands, tap Plateau").
- FIXED  an engine card id leaked into the trigger option ("[Zone Changer: Orcish Bowmasters (37)]").
- FIXED  the 20-minute cap ended the game at turn 13 (54 calls, ~23 s each): recipe cap 45 min.

### g09  2026-10-15  seed 72  Elves (seat) vs Burn (Forge AI)  lost T16  deepseek-v4.1-flash one-tier  build fix/engine-id-leak  runs/loop/g09

Full game inside the 45-minute cap: 91 calls, $0.21. Sheet: TAKEN 0, LOST 0, TOOL 0, SLOW 1 (a 150 s
provider stall on attackers); analyzer PASS.

- WORKS  Green Sun's Zenith's X was the seat's (X=2, asked up to what it could pay); 38 windows passed on
  the seat's own YIELD (33 end-of-turn, 5 my-next-turn); three mana-sources asks; no harness-guessed pass.
- FIXED  the sheet called a last-sig repeat a SKIP: T6 upkeep, Rift Bolt's suspend trigger offered the
  identical spot (stack, board, menu) the seat had just passed on. It is now counted as a repeat.

### game of 2026-10-15  seed 81  Sneak and Show (GPT-6 Luna) vs UR Delver (Forge AI)  lost T9  $0.011  harness-epoch-7

First game with the answer tool (`answer-tool-v2`) and prompt caching, on the new default game model.

- Harness: 20 of 20 model decisions came back as `answer` calls, 0 illegal calls, 0 taken, lost or
  skipped, 0 Forge-AI decisions. The plan was set through `set_plan` 7 times and followed the game
  (replanned after the counter). 70% of prompt tokens were read from the provider's cache. No lookups
  were made.
- The game: Show and Tell on turn 6 was countered by Force of Will. Delver and Lightning Bolts won
  on turn 9. A fair loss; nothing for the harness to fix.
- OPEN  Luna writes no visible text beside its calls, so `raw_reply` holds only the rendered
  answer. Its reasoning is not recorded anywhere, which makes misplays hard to read.
