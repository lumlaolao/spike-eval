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

### game of 2026-10-15  seed 81  Sneak and Show (GPT-6 Luna) vs UR Delver (Forge AI)  lost T9  $0.018  answer-tool-v8

First game with no text alternative (`answer-tool-v8`, `tool_choice` "required" on Luna). `runs/luna-v8/g1`.

- Harness: 24 of 24 model decisions came back as `answer` calls, 0 nudges, 0 filler yields, 0 Forge-AI
  decisions.
- The game: Show and Tell was Spell Pierced; Delver won on turn 9. A fair loss.
- FIXED  an unless cost the seat could not pay ("Pay {2}", "Pay {R}{R}") was offered as a choice; it is
  now a forced decline, not asked.
- FIXED  each answer to an unless cost was recorded twice (the answer row and an auto row).

### game of 2026-10-15  seed 82  UR Delver (GPT-6 Luna) vs Sneak and Show (Forge AI)  CRASHED T6  $0.03  harness-epoch-17

`runs/luna-v8/g2`, the build of #188. 43 model decisions, all by `answer` call; 0 taken, lost or skipped;
0 Forge-AI decisions. The seat was ahead (opponent at 12, two Dragon's Rage Channelers) when the game
thread died.
- FIXED  StackOverflowError on turn 6: six cards in the graveyard, more than Murktide Regent's generic
  5, so delve asked the seat inside an affordability test, and the ask's snapshot tested Murktide
  again. Test `delveWithMurktideInHandDoesNotRecurse` fails on main with the same error.
- FIXED  delve was not the seat's: every affordability test wrote an auto row exiling the whole
  graveyard (17 by turn 4), a real payment exiled all of it unasked, and a larger graveyard forced
  exactly the generic amount. The seat now chooses how many and which, in the real payment only.
- FIXED  the crash was one log line, a game line with winner "draw" and exit 0, and the sheet passed.
  Now: 80 stack frames, `game_error` with winner "invalid", exit 3 (run_batch replays it), and
  `defect_analyzer.py` fails on `game_crash_count`.
- OPEN   one Luna call hit the 300 s deadline and was retried twice (T4 play, 300 s lost).
- clean  mulligan, plays, targets, surveil (each answer took effect), the fetch, attacks.

### game of 2026-10-15  seed 83  UR Delver (GPT-6 Luna) vs Sneak and Show (Forge AI)  lost T24  $0.07  harness-epoch-18

`runs/luna-v8/g3`, the build of #189. 73 model decisions by `answer` call, 0 taken or lost, 0 Forge-AI
decisions, no delve noise (the affordability tests are silent now). The sheet read clean, and was wrong.
- FIXED  a yield passed the seat's own main phases: 15 of them in 12 turns, the first main phase on
  turns 3, 7, 9, 11, 13 and 21. The seat yielded at its own upkeep to keep its mana open ("keep both
  Volcanics open") and lost its land drop and every sorcery-speed play with it: 6 lands in 12 turns, two
  discards to hand size. A yield now never passes the seat's own main phase (answer-tool-v9).
  Test `aYieldAtTheSeatsOwnUpkeepStillAsksInItsMainPhase`.
- FIXED  `match_review.py` counted those windows as the seat's yields; each is now a SKIP (15 here).
- OPEN   recorded runs were hit the same way: `runs/compare/*` up to 27 own main phases a game,
  `runs/prompt-ab/*` up to 17. Their comparisons are confounded by it.
- OPEN   a second 300 s Luna timeout (T23 play), retried.

### game of 2026-10-15  seed 84  UR Delver (GPT-6 Luna) vs Sneak and Show (Forge AI)  lost T13  $0.07  harness-epoch-19

`runs/luna-v9/g4`, the build of #191 (answer-tool-v9). 68 model decisions by `answer` call; TAKEN 0,
LOST 0, SKIP 0; 0 Forge-AI decisions; analyzer PASS.
- WORKS  the yield fix: 16 yielded windows, none of them the seat's own main phase; every seat turn with
  something usable had a main-phase ask (turn 12 had no land and nothing castable).
- OPEN   one Luna ask took 3 attempts (T8 play, 31 s lost).
- clean  mulligan, plays, targets, attacks.

### game of 2026-10-15  seed 85  Death and Taxes (GPT-6 Luna) vs Grixis Reanimator (Forge AI)  WON T13  $0.06  harness-epoch-21

`runs/luna-v9/g5`, the build of #195. 80 model decisions by `answer` call; TAKEN 0, LOST 0, SKIP 0; 0 Forge-AI
decisions; analyzer PASS. The first Luna win in these games (Thalia, Stoneforge into Batterskull, Vial).
- FIXED  engine ids in the state: the stack read "Aether Vial (4) - You may put a creature card ...", and on
  T7 the seat answered "Vial is at four counters" with three on it. Also "[Zone Changer: Stoneforge Mystic
  (30)]" in trigger lines and "[notice to you] Lotus Petal (111) picked {U}" in events (g4). The stack and
  the notices are now redacted like every other engine message. Test
  `theStackShowsNoEngineIdAndCardTextNoEscapes`; the ask contract checks the whole prompt; the sheet
  reports LEAK (19 here).
- FIXED  card text showed the oracle's literal "\n" ("...on Aether Vial.\n{T}: ...", 34 prompts).
- clean  mulligan, Vial counters (asked every upkeep), Stoneforge and Recruiter searches, Wasteland target,
  attacks.

### game of 2026-10-15  seed 86  Standard Dimir Aggro (GPT-6 Luna) vs Standard UW Control (Forge AI)  WON T25  $0.09  harness-epoch-22

`runs/luna-std/g6`: the first Standard game (MTGO Challenge top-8 lists, #198), on the build with latency routing
(#197). 120 model decisions by `answer` call; TAKEN 0, LOST 0, SKIP 0; 0 Forge-AI decisions; analyzer PASS.
- WORKS  latency routing: 153 calls, p50 6.7 s, p99 26 s, max 28 s, all served by Azure (before: p50 8.6 s, max 352 s).
- FIXED  "The Wondrous Wasp (53)'s Effect" in the command zone (22 prompts): an effect named after its source with
  the engine id inside the name. Names drop it. Test `anEffectsNameCarriesNoEngineId`.
- clean  mulligan, plays, Kaito emblems, attacks; format named in the system prompt; decks loaded 60+15.

### game of 2026-10-15  seed 87  Modern UrzaTron (GPT-6 Luna) vs Modern Rakdos Delirium (Forge AI)  WON T14  $0.04  harness-epoch-23

`runs/luna-modern/g7`: the first Modern game. 64 model decisions by `answer` call; TAKEN 0, SKIP 0; 0 Forge-AI
decisions; analyzer PASS.
- FIXED  the seat's "No target" for Cityscape Leveler's up-to-one target (T10) was logged with the fallback
  "target(optional-decline-or-unparsed)", and the sheet called it LOST. A decline now carries no fallback; only an
  answer that could not be used does. Test `anOptionalTargetTheSeatDeclinesIsItsDecisionNotAFallback`.
- FIXED  the target question pasted the trigger's engine text "[Card: Cityscape Leveler (70), Activator: Seat, ...]"
  (also Devourer of Destiny (50), T6); ability labels are now redacted like the stack.
- checked  "announce-x(forced)->0" twice for Kozilek's Command: the engine's own payment check (canPayManaCost at
  each X) allowed only 0, so the X was right.
- FIXED  (#202, found after the game, owner: "it wasnt realizing lands can make more than 1 mana") the mana line
  counted untapped sources, not mana: Tron read 3 instead of 7, Ancient Tomb 1, a summoning-sick dork 1. It is now
  the engine's mana per source (canPlay, amountOfManaGenerated), as Forge's AI reads it.

### game of 2026-10-15  seed 88  Pauper Madness Burn (GPT-6 Luna) vs Pauper Mono-Blue Delver (Forge AI)  WON T11  $0.02  harness-epoch-24

`runs/luna-pauper/g8`: the first Pauper game. 38 model decisions by `answer` call; TAKEN 0, LOST 0, SKIP 0; 0
Forge-AI decisions; analyzer PASS.
- clean  mulligan, burn targets, madness discards, attacks.

### game of 2026-10-15  seed 89  Standard Mono-Green Landfall (GPT-6 Luna) vs Standard Boros Dragons (Forge AI)  LOST T12  $0.02  harness-epoch-24

`runs/luna-std/g9`. 29 model decisions by `answer` call, 10 forced; TAKEN 0, LOST 0, SKIP 0; 0 Forge-AI decisions;
analyzer PASS.
- OPEN   one ask took 3 attempts (T9 play); the log's one exception is a provider's cut-off stream
  ("chunked transfer encoding"), retried.
- clean  plays, attacks, blocks.

### game of 2026-10-15  seed 90  Standard UW Control (GPT-6 Luna) vs Standard UR Aggro (Forge AI)  LOST T35  $0.13  harness-epoch-24

`runs/luna-std/g10`. 148 model decisions by `answer` call, 38 forced; TAKEN 0, LOST 0, SKIP 0; 0 Forge-AI
decisions; analyzer PASS. Luna stabilised at 2 life with Jace and Pinnacle Starcage, then lost to a Grove's Bounty
on Elusive Otter through two counterspells.
- FIXED  (#202) two Great Halls of the Biblioplex read "{W:2 U:2 B:2 R:2 G:2 C:2}": their any colour costs 1 life
  and is for instants and sorceries only. Restricted mana is now listed apart, in the card's words.
- FIXED  (provenance PR) the opponent's player name in these games was "ForgeAI" (set by the game script), so engine
  log lines like "ForgeAI scries 1" could read as a harness or AI message. The script names it "Rival" now, and every
  block of the prompt names its source.
- WORKS  the mana-source keep question, 11 times; once the keep set could not pay {6}{W}{W}, was asked again,
  and the second set was trimmed to what could pay (logged as a fallback).
- OPEN   two asks took 3 attempts; one provider stream cut off and was retried.

### game of 2026-10-16  seed 91  Modern UrzaTron (GPT-6 Luna) vs Modern UR Cutter Prowess (Forge AI)  LOST T11  $0.03  harness-epoch-26

`runs/luna-modern/g11`, on the merged #202/#204 build. 35 model decisions by `answer` call; TAKEN 0, LOST 0,
SKIP 0; 0 Forge-AI decisions; analyzer PASS.
- WORKS  the mana line from the engine: Tron read "{C:9} up to 9 mana ... Urza's Tower makes 3", Ugin's Labyrinth
  "makes 2", Eldrazi Temple's mana listed apart as only for some spells; the seat cast Devourer "using the Eldrazi
  Temple's restricted mana, then Karn with the remaining unrestricted mana".
- FIXED  a question's title read "[Devourer of Destiny (48)'s Effect]" (T2, LEAK): the questions named cards by the
  engine's raw name. One name function now serves every question. Test `aQuestionNamesAnEffectWithoutItsEngineId`.
- FIXED  the seat announced X = 1 for Kozilek's Command (T6), then chose its modes "as X is 0": the mode and target
  questions did not say X. They do now. Test `theModeAndTargetQuestionsSayTheAnnouncedX`.
- OPEN   three asks took 2-3 attempts (T10, 67 s lost).

### game of 2026-10-16  seed 92  Pauper Mono-Blue Delver (GPT-6 Luna) vs Pauper Madness Burn (Forge AI)  WON T11  $0.02  harness-epoch-27

`runs/luna-pauper/g12`. 44 model decisions by `answer` call; TAKEN 0, LOST 0, SKIP 0; 0 Forge-AI decisions;
analyzer PASS.
- WORKS  the known library top and the shuffle note: the seat declined Ponder's shuffle "to preserve Brainstorm as
  the known top card", and later kept Lórien Revealed uncycled "rather than shuffle away" a known Deep Analysis.
- noted  95 forced `static-order` rows (one legal order each): recorded, not decided for the seat.

### game of 2026-10-16  seed 93  Standard Boros Dragons (GPT-6 Luna) vs Standard Mono-Green Landfall (Forge AI)  WON T14  $0.03  harness-epoch-27

`runs/luna-std/g13`. 50 model decisions by `answer` call; TAKEN 0, LOST 0, SKIP 0; 0 Forge-AI decisions; analyzer
PASS.
- FIXED  after two mulligans the harness kept a one-land five on its own ("mulligan(floor-keep 1/5)"): a hard floor
  that never asked the seat below six cards. It is now a measurement switch like the land heuristic
  (`MULLIGAN_HEURISTIC=1`); by default the seat decides at any size. Test `aFiveCardHandIsStillTheSeatsMulligan`.
  The README also said these switches default to on; they have been off since 2026-10-15.
- clean  Sacred Foundry and Multiversal Passage life payments, warp, Burst Lightning double-up on a 4/4, attacks.


### C3 ANT Storm  2026-10-16  seed 4101  ANT Storm mirror (GPT-6 Luna vs Forge AI), three arms  $0.77 (+$0.07 killed)  harness-epoch-30

`runs/c3/ant-storm-{default,turn,game}` (review C3: stateless default vs `--turn-chat` vs `--one-chat`, the same seed and
decks). Default WON T59 (209 model decisions); turn window LOST T28 (118); whole-game chat WON T11 (74). TAKEN 0, LOST 0,
SKIP 0 and 0 Forge-AI decisions in all three; no `LINE` (passing with mana floating) in any. One game per arm says nothing
about win rates. A first default-arm game was killed at T19 by my shell's 30-minute background limit, not by the harness
(`runs/c3/killed-ant-storm-default-t19`, kept as a record only).
- FIXED  the mana-sources and restricted-mana texts showed the engine's placeholder: "{T}, Sacrifice CARDNAME: Add one
  mana" for Lotus Petal (3 asks across the games). `named()` puts the card's name in; `match_review` now lists a placeholder
  as LEAK. Found by the ask contract in a new golden check (`ritualThenPetalThenAdNauseamIsPaid`).
- FIXED  Chrome Mox imprinting Brainstorm makes blue, the menu offered Ponder {U} as castable, but "mana available" said
  {B:1}: a reflected or chosen colour was read as no colour. Now asked of the engine (`CardUtil.getReflectableManaColors`,
  `canProduce`). Test `aReflectedColourIsCounted` (observation-faithful-v32).
- FIXED  the same Ponder's preview said the copy could not pay: the engine's game copy drops imprinted cards. Imprints are
  copied onto the preview copy. Test `aPreviewKeepsAnImprintedMoxsColour` (lookahead-preview-v7). Told twice it had no
  blue, the seat cracked Polluted Delta instead of casting Ponder (default T11). The turn arm's Tendrils preview failed
  the same way (T9, "Didn't find what to pay for {1}").
- FIXED  (2026-10-16, after the read) default T7: {B:3} floating and two Lotus Petals, Ad Nauseam chosen, then "Didn't
  find what to pay for {1}" and nothing was cast: the seat's turn-4 kill, and why the game lasted 59 turns. Cause: the
  engine patch asks the seat mid-payment which colour a Petal makes; rendering that prompt test-paid Tendrils for its
  "(castable)" tag, and the test payment cleared and refilled the AI memory the real payment used (PAYS_TAP_COST), so the
  second Petal read as already used. The prompt is rendered with that memory saved and restored. Test
  `ritualThenPetalThenAdNauseamIsPaid` (fails without the fix).
- noted  turn arm T17: `simulate` asked during a fetchland's library choice, refused by design (no play menu). Watch whether
  the turn window invites previews outside the play menu.
- noted  provider: one dropped connection (`chunked transfer encoding`), retried in 16 s; one 300 s timeout on the
  whole-game arm's first play.

### C3 Doomsday  2026-10-16  seed 4102  Doomsday mirror (GPT-6 Luna vs Forge AI), three arms  $0.67  harness-epoch-31

`runs/c3/doomsday-{default,turn,game}`. Default LOST T24 (106 model decisions). The turn-window and whole-game-chat games
were cut off at my shell's two-hour ceiling (T37 with 112 decisions, T32 with 81), so they have no result; their decisions
are read for coherence only. TAKEN 0, LOST 0, SKIP 0, 0 Forge-AI decisions, no `LINE` in any.
- FIXED  (2026-10-16) provider stalls: 3 to 5 calls a game got no reply until the 300 s deadline ("wall-clock timeout after
  300000 ms", once "header parser received no bytes"), then were retried: 15-25 minutes of every game. `HttpDeadline` now
  gives up early on a silent connection: no response headers for 90 s (OpenRouter sends them at once for a live request),
  or keep-alive bytes seen and then none for 90 s (`OPENROUTER_IDLE_MS`). A reply that sends nothing until it is done is
  never cut (the second rule waits for a first keep-alive). The 300 s budget stays for reasoning. Tests in
  `OpenRouterTimeoutTest` (four servers: no headers, keep-alives that stop, keep-alives throughout, silent until done).
- FIXED  (2026-10-16) a preview of Force of Will cast for its alternative cost was "unavailable": the copy matched the
  hard cast. The copy's alternative cost is found and played; and the copy now holds the stack, so a counterspell is
  previewed against the spell it answers (the engine's copy has none: the report said the opponent was unchanged).
  Test `aForceOfWillPitchIsPreviewed` (lookahead-preview-v8).

### Combo-line review of the C3 games, 2026-10-16 (no new games; owner: exhaust the recorded games first)

Doomsday never resolved for the seat and ANT's Tutor-LED line never came up, so each was played out in a golden check:
- FIXED  the menu offered no mana ability, so Lion's Eye Diamond was usable only as payment: no LED in response to the
  seat's own Infernal Tutor (hellbent), no Spirit Guide. A mana ability with a cost beyond tapping is offered on the faithful
  menu (`costlyManaAbility`). Test `lionsEyeDiamondCanBeCrackedInResponseToInfernalTutor`.
- FIXED  storm copies' new targets were Forge's AI's (`brains.doTrigger` on the engine path): each copy's target is now the
  seat's ("Choose the TARGET for a copy of ..."), and the order question calls them copies, without the old "Order them so
  your plan works out" advice. Test `tendrilsStormCopiesResolveAndTheirTargetsAreTheSeats`.
- FIXED  Doomsday asked the seat to order the eight cards it exiles. Exile, and a graveyard whose order cannot matter, have
  no order: recorded as forced, as Forge's own human controller does for the graveyard. Test
  `doomsdaysPileAndItsOrderAreTheSeats` (the five, their order top-down, the library exactly that pile).
- clean  Ad Nauseam: every repeat is asked, with current life and the cards drawn so far
  (`adNauseamRepeatsAsOftenAsTheSeatSays`).
- noted  `simulate` asked during non-play decisions twice more (turn T13, game T3), refused by design.
- FIXED  the one `report_unclear_decision` in the C3 games (killed ANT default game, T3): with its own Duress on the stack,
  Luna read the faithful stack line "1 pending: ..." as a Duress choice it was owed, and reported a menu of only Cabal
  Ritual and PASS as wrong. The line now says "1 not yet resolved (it resolves when both players pass in a row)"
  (observation-faithful-v33). Test `aSpellOnTheStackIsSaidToBeUnresolved` (fails on main).
- explained, all fixed  the 4 previews "the engine could not pay for or play": 2 a Chrome Mox's imprint lost on the copy
  (Ponder off a Brainstorm-imprinted Mox; Tendrils with a Dark Ritual-imprinted Mox; epoch 31), 2 a Force of Will pitch
  (epoch 32).
- clean  the 13 `color(forced)` picks (a Petal or Mox paying a one-colour cost: the engine offers one colour), the 7
  forced replacement orders, the forced discards and reveals; both logged exceptions are provider transport errors
  (no headers, broken chunking) retried by the bridge; the ~30 s losses are OpenRouter HTTP 502s, retried; the 300 s
  losses are the stalls now cut at 90 s (`OPENROUTER_IDLE_MS`).
- FIXED  (owner: "labels should say what they are") the whole-game ANT arm, T11: ten storm copies were put to the seat to
  order, "These 10 triggers", each labelled `Tendrils of Agony @149 2{B}{B} [...]` like the card in hand; the copies are
  identical until each gets its target, so the order is no choice and is not asked. The stack line carried Forge's debug
  notes ("[Card: ..., Activator: Seat, SpellAbility: ...]", "[Zone Changer: Chrome Mox @18]") and did not say how many
  copies storm makes; now "-> makes N copies" from the engine's count, no notes. `tendrilsStormCopiesResolveAndTheirTargetsAreTheSeats`.
- FIXED  (found by the surface checks, then confirmed in the record) the seat's counterspells countered nothing. Doomsday
  whole-game arm, T8: `Seat cast Force of Will targeting [Dark Ritual @58]`, `Force of Will @59 - Counter.`, then
  `Dark Ritual @58 - ForgeAI adds {B}{B}{B}`. The target was the spell's card (`getAllCandidates`); Forge counters only
  targeted spell abilities. Stack targets are now the stack's spell abilities (`targetCandidates`), so Stifle's abilities
  are offered too. Tests `aCounterspellCountersTheSpell` (failed before the fix), `stifleCountersAnAbilityItNames`.
- FIXED  values a cast announces were not asked: Disrupting Shoal's pitch ran at X = 0 and failed to pay, Toxic Deluge's
  life X was 0. The pitch option was labelled with the mana cost it replaces (`Disrupting Shoal {X}{U}{U}`; Force of Will
  likewise). Tests `aShoalsXIsAnnouncedByTheSeat`, `toxicDelugesLifeIsTheSeats` (harness-epoch-34).
- Result: every defect found in the recorded C3 games is fixed. No LINE, TAKEN, LOST or SKIP in any of them.

### g01  2026-10-17  seed 4101  Elves (seat) vs Elves (Forge AI)  won T11  $0.05  harness-epoch-43
- Time: 91 min. 48 min model answers (70 decisions, median 11 s, longest 4.4 min), 40 min in 8 provider stalls (OpenRouter
  kept the connection open with keepalives and no answer until the 300 s deadline; each retry answered in about 10 s),
  3 HTTP 5xx retried. Elves asks many questions: 29 decisions on T5 alone.
- clean  TAKEN 0, LOST 0, SKIP 0, ai=0, tools 10/10 served. Forced: 5 trigger orders of two identical Nettle Sentinel
  untap triggers, 3 damage assignments with one legal split. One malformed `answer` (not JSON), corrected in the same
  conversation.
- clean  floating mana from the menu: T5 Gaea's Cradle tapped before Green Sun's Zenith for X=2.
- FIXED  (setup) the Forge checkout build refused an unused import of ComputerUtilCost left by #240; `java_imports.py`
  counted its mention in a javadoc sentence as a use. It now blanks comments and strings as checkstyle does.
- FIXED  (sheet) `plan_audit.py` read plans only from the text grammar's PLAN: line, so it reported plans=0 for every
  answer-tool game; it reads `set_plan` calls now (17 here). A clause that says "now" is no longer read as a hold ("Crack
  Windswept Heath ... now to improve next turn's mana" was flagged).

### g02  2026-10-17  seed 4102  Burn (seat) vs Death and Taxes (Forge AI)  lost T12  $0.02  harness-epoch-43
- Time: 22 min. 16 min model answers (23 decisions), one 300 s provider stall, one HTTP 5xx retried.
- clean  TAKEN 0, LOST 0, SKIP 0, ai=0; plans 8, hold violations 0. Forced: a suspended Rift Bolt's cast (one
  ability), one entity, one damage split. No blocks asked T10 and T12: the seat had no creature (Swiftspear died
  attacking into Mirran Crusader T9). T11 main passed as nothing-usable: correct, Thalia's tax made Price of Progress
  cost three of four Mountains, so Eidolon's {R}{R} could not be paid.
- FIXED  the attack outlook's "None of their creatures that can block now could block: Goblin Guide (2)": the bare
  number (damage if unblocked) read as an engine id (the sheet's LEAK). Now "(2 damage)", lookahead-preview-v9,
  harness-epoch-44.
- noted  `simulate` asked during the suspended Rift Bolt's cast decision: refused by design (previews are of the
  priority menu), as in earlier games.
- checked  a fetch's library search lists the whole library in the event history: a player sees it when searching,
  and the library is shuffled at once; the known-in-hidden-zones record does not carry it.

### between g02 and g03  2026-10-17  free checks while the games ran
- FIXED  the keyword gauntlet with lookups on (`GAUNTLET_TOOLS=1`, random previews, inspects and searches) crashed two
  games with a StackOverflowError: affordability and offerable cleared PRICING_FOR_SNAPSHOT when done instead of
  restoring it, so under an outer pricing the next pool-mana choice asked the seat, and its prompt priced the hand
  again. The flag is restored everywhere, a prompt build is pricing from start to end, and the convoke chooser
  honours it. Test `pricingInsidePricingKeepsTheGuardOn` (failed before the fix); both games replay to the end.
- FIXED  (fake model) "header parser received no bytes" x13 on the request after a lookup: the stub closed each
  connection without saying so; it now sends `Connection: close`.
- FIXED  (sheet) `match_review.py` prints a TIME line: elapsed, model answer time, waits to the call deadline,
  stalls cut early, provider errors.
- clean  random-answer gauntlet with new seeds (s3): 28 games, 0 defect lines.
- FIXED  tools gauntlet (e44-tools-t2): a kept mana source left a payment the engine could not make (Jegantha with
  Forest floated and Island kept); the cost was not paid. The hold is released and the cost paid again, as an auto
  row. Test `aKeptSourceLeavesAPaymentTheEngineMakes`.
- FIXED  tools gauntlet (e44-tools-t2): a modal spell cancelled at its targets kept its chosen modes, and the next
  cast of it skipped the mode question. Test `aCancelledModalSpellKeepsNoModes`.
- FIXED  random gauntlet (e44-random-s4, kw05): Become Immense with {U}{G} floating and three lands asked delve with
  "between 0 and 2", the seat exiled none, and {1} could not be paid. The least count now comes from the engine's test
  payment. Test `delveMustExileWhatTheManaCannotPay` (the kw05 state; fails with the old estimate).
- Guide v37 / seat-tools-v10: every hand tag and the mana-ability ACTIVATE are described. harness-epoch-45.

### g03  2026-10-17  seed 4103  Death and Taxes (seat) vs Mardu Energy (Forge AI)  WON T20  $0.12  harness-epoch-44
- Time: 64 min. 53 min model answers (188 calls, 129 decisions), 10 min in two waits to the 300 s call deadline (both
  retried and answered), no provider errors. The cost is above earlier games because the game ran 20 turns.
- clean  TAKEN 0, LOST 0, SKIP 0, ai=0, textParses=0; inspect served 11, simulate served 20; plans 27.
- FIXED  `simulate` refused three times outside the priority menu (T8 x2 while aiming Wasteland at Scrubland or
  Karakas, T14 at Aether Vial's "Search your hand?"). A target choice of a play is now previewed at the numbered target
  (lookahead-preview-v10, seat-tools-v11). Test `aTargetChoiceOfAPlayCanBePreviewed`. A yes/no confirmation still has
  nothing to preview and is refused with the reason.
- FIXED  (found reading the preview code for the above) a preview set targets on every ability of a spell's chain and
  restored only the first: Arc Trail's "1 damage to any other target" kept the preview's pick, and the real cast skips
  an ability that already has a target, so the seat would not have been asked. Test
  `aPreviewLeavesNoTargetOnAnyAbilityOfTheChain` (failed before the fix).
- model  plan_audit HOLD-VIOLATIONS 3 of 22: each a play the model chose against its own plan clause (Wasteland kept
  "available" then used, Vial at three counters, Mother of Runes). Coherence, not a harness defect. T14 the model
  activated a four-counter Vial meaning to put in Thalia (mana value 2), then declined the search: a puzzle candidate.
- (fake model) the random gauntlet policy now previews targets of a play as well.

### g04  2026-10-17  seed 4104  Goblins (seat) vs Miracles (Forge AI)  WON T11  $0.06  harness-epoch-45
- Time: 12 min, all in 94 model answers; no waits to the deadline, no provider errors.
- clean  TAKEN 0, LOST 0, SKIP 0, ai=0; plans 11, hold violations 0; tools REFUSED 0.
- FIXED  the new target preview was offered for a triggered ability's target (T5, a cycling trigger aiming at Goblin
  Lackey or Goblin Matron) and came back "preview unavailable": a trigger cannot be replayed on the copy. The preview
  is offered only while aiming a play from the priority menu; elsewhere `simulate` is refused with the reason
  (lookahead-preview-v11).

### epoch-45 gauntlets  2026-10-17
- `first` 28 games, random (s5) 28 games, random with lookups (t3) 28 games: 0 defect lines each.
- FIXED  (script) the gauntlet run without `FORGE_HOME` played 28 games that all exited 1; it now stops at once when
  the checkout is missing.

### g05  2026-10-17  seed 4105  Cephalid Breakfast (seat) vs Lands (Forge AI)  WON T14  $0.12  harness-epoch-45
- Time: 20 min, 158 model answers; no waits to the deadline, no provider errors. First game on the Mac clone.
- FIXED  (setup) the Mac's random canary pin was stale since epochs 40-41; re-measured B/50/273 and re-pinned (commit
  487b6022). No code change.
- clean  TAKEN 0, LOST 0, SKIP 0, ai=0; tools 10/10 served. The combo turn (Cabal Therapy flashback sacrificing
  Thassa's Oracle, Dread Return on it with an empty library) ran as chosen.
- FIXED  (2026-10-18, found reviewing this record) the card-name ask showed the engine's filter: "(must match:
  Card.nonLand)". It now says "(it must be the name of a nonland card)", and Force of Will's cost label, which read
  "Exile a Card.Blue+Other card", says "another blue card". AskContract and match_review flag the syntax now.
  Tests `aNameFilterIsSaidInWords`, `aNamedCardIsTheModels`. harness-epoch-47.
- model  the first Cabal Therapy name was Dark Depths, a land, though the ask said "nonland"; the refusal corrected it
  in the same conversation.
- model  cast Thassa's Oracle with cards still in the library (it resolved without winning); won anyway by the
  flashback line above.

### g06  2026-10-17  seed 4106  Sneak and Show (seat) vs Infect (Forge AI)  LOST T7  $0.03  harness-epoch-45
- Time: 6 min, 54 model calls; no waits, no provider errors. Mulligan to 5 with two lands; no second red source, so
  Sneak Attack stayed in hand. Lost to Blighted Agent pumped by Might, Berserk and Vines (14 poison).
- FIXED  Berserk's resolve line in GAME HISTORY said "+10/+0" while the stack had said "+5/+0" and +5 applied:
  MagicStack built the line after the pump, re-reading the pumped power. Engine patch 3 (`MagicStack.java`) captures
  it before. Test `aPumpResolveLineSaysWhatTheStackPreviewSays` (failed before). harness-epoch-46.
- model  PUZZLE candidate: T5 the plan cast Sneak Attack "leaving Volcanic untapped" with no red source in the arithmetic; T4 the fetch
  took a basic Island over Volcanic Island (rows 98, 137, 145).
- model  T6 Brainstorm put back City of Traitors, the land its own plan meant to play next.
- clean  TAKEN 0, LOST 0, SKIP 0, ai=0; plan audit 0 of 5 hold violations.

### g07  2026-10-18  seed 4107  UR Delver (seat) vs Grixis Reanimator (Forge AI)  WON T10  $0.03  harness-epoch-47
- Time: 7 min, 54 model answers; no waits, no provider errors.
- clean  TAKEN 0, LOST 0, SKIP 0, ai=0; tools REFUSED 0 (simulate 3, memory 2); plans 7, hold violations 1.
- FIXED  the new LEAK check found the opponent's Shallow Grave on the stack as "returns a
  Creature.TopGraveyardCreature+YouCtrl card from their graveyard" (2 rows, T5): stack and history lines now say
  filters in words too ("returns a creature from their graveyard"), in `SeatObservation.redact`.
  Test `aStackLineSaysItsFilterInWords`. observation-faithful-v38, harness-epoch-48.

### filter sweep  2026-10-18  every recorded decision prompt under runs/ (18,613 files)
- FIXED  engine filters in stack lines that the first translation lost meaning on: Duress "chooses a
  Card.nonCreature+nonLand card" (1407 rows), Cabal Therapy "discard all of type: Card.NamedCard card" (480), "a
  Remembered.sameName card" (582), Boiling Earth "Deals 1 damage to Creature.OppCtrl." (239), Bonfire "to
  Creature.ControlledBy TargetedOrController" (200). Control, a chosen or shared name and a bare filter are said in
  words; an unsayable condition reads "the creatures the card describes". observation-faithful-v39, harness-epoch-49.

### g08  2026-10-18  seed 4108  Doomsday (seat) vs Painter (Forge AI)  WON T10  $0.05  harness-epoch-48
- Time: 12 min, 76 model answers; two provider stream errors (chunked transfer cut), both retried and answered.
- clean  TAKEN 0, LOST 0, SKIP 0, ai=0; tools REFUSED 0; plans 13, hold violations 0. The combo played out: Doomsday
  resolved with a five-card pile the seat chose and ordered, and Thassa's Oracle won.
- FIXED  the pile is picked one card per question ("(3 / 5)"), and no question said which cards were already in it:
  T8 the seat searched its archive for "Doomsday pile selected" (empty) and re-read an old state to recall its own
  picks. Each later pick now says "Already picked for this effect: ...". Test `doomsdaysPileAndItsOrderAreTheSeats`
  (extended). harness-epoch-49.
- FIXED  (sheet) match_review counted the model's lookup rounds as retries ("5 attempts, 12100ms wasted"); it now
  subtracts them and says "3 lookups, 1 retried".

### g09  2026-10-18  seed 4109  ANT Storm (seat) vs Food Chain (Forge AI)  lost T13  $0.06  harness-epoch-49
- Time: 9 min, 90 model answers; no waits, no provider errors.
- clean  TAKEN 0, LOST 0, SKIP 0, ai=0; tools REFUSED 0. One empty lookup (T2, "ForgeAI hand revealed") came while
  naming a card for Cabal Therapy, before the hand is revealed: correct. Nothing new on the sheet.

### sweep for other engine words  2026-10-18  every decision prompt under runs/
- FIXED  the header before the first turn read "== Turn 0 null (active: ? | priority: ?) ==" on every mulligan ask
  (still in g07-g09). It says "before the first turn (opening hands)" and "none yet" now. Test
  `theHeaderBeforeTheFirstTurnSaysSo`. observation-faithful-v40, harness-epoch-50.
- checked  "stated at T3 null" in the plan line appears only in older runs (fixed before); "(X=2)" annotations on
  stack lines are the engine's announced X, readable and kept; no "$", "%s" or SVar names reach a prompt.

### open items re-checked  2026-10-18
- FIXED  `inspect "Cat Warrior Token"` (OPEN since 2026-10-14): a token has no database entry, so a name found
  nothing. A name that matches a current object the seat can see now answers as its handle would. Test
  `tokensGetTheirOwnIdentityAndRender` (extended). Folded into observation-faithful-v40, harness-epoch-50.
- closed by later work, not re-opened: previews of PLAY LAND and PASS (lookahead v3/v4), the mulligan heuristic
  (off by default since 2026-10-15), the 4000-token cut-offs (32000 since harness-epoch-7), the one-preview budget
  (removed, MODEL-LIMITS.md), Heritage Druid's tapped Elves (asked, `LlmSpikeTest` issue #105). The provider 300 s
  stalls and stream cut-offs are the provider's, retried by the bridge.
- FIXED  memory search gave up when no entry held every word of the query (g08: "Doomsday pile selected", Matches=0).
  It now lists the entries holding some of the words, most first, and says so. Test
  `aSearchWithNoLiteralMatchFallsBackToEveryWord` (extended). evidence-tools-v4.
- checked  naming a card (Cabal Therapy, Pithing Needle) accepts only card-database names, so a token's name is
  refused although `inspect` now answers by it. Test `aTokenNameIsNotACardName`.
- FIXED  (sweep of g05-g09 prompts) "mana available: {} up to 0 mana (0 untapped sources; each counts its mana under
  every colour ...)" on 149 rows reads "none (no untapped mana sources)"; Shallow Grave's stack line "... to the
  battlefield. gains haste" reads "It gains haste". Test `aStackLineSaysItsFilterInWords` (extended).
  observation-faithful-v41.
- FIXED  (every distinct question wording in g01-g09, read by hand) "An effect lets you cast CAST Rift Bolt" (the
  verb doubled), "Choose a Creature to name" for Cavern of Souls (it asks for a creature type), "Choose card(s) to
  put on top of Seat library" for Thassa's Oracle (the seat by its label, not "your"). Tests
  `castingFromAnEffectIsTheModels`, `aTypeIsTheModels`, `anEntityPickIsTheModels` (extended). Faithful profile only;
  folded into harness-epoch-50.


### g10  2026-10-18  seed 4110  Elves (seat) vs Burn (Forge AI)  WON T8  $0.02  harness-epoch-50
Sheet clean: 33 model decisions, TAKEN 0, LOST 0, SKIP 0, TOOL 3 of 3 served, ai_tier_count=0. The seat cast Glimpse
of Nature twice, chained Heritage Druids, and on turn 8 tapped Gaea's Cradle for Green Sun's Zenith X=8 (Craterhoof).
- FIXED  the ACTIVATE option read "Tap three untapped Elfs you control" (Forge's plural adds an "s"; six rows, turn 6
  on). An option says a creature type in Forge's own plural ("Elves"). Test `aTypeIsSaidInItsOwnPlural`.
- FIXED  `simulate {"option":2,"then":[4,6]}` (cast Heritage Druid, cast Heritage Druid, tap Gaea's Cradle) was
  refused whole because option 6, an ACTIVATE option, is a mana ability. A preview plays a mana ability like any step
  and its report shows the mana pool. Test `aTapAbilityAndAManaAbilityArePreviewed` (renamed).
  lookahead-preview-v12. Both folded into harness-epoch-51.
- clean  mulligan, blocks, the X announcement, the Zenith search, attackers, two lookups served.

### g11  2026-10-18  seed 4111  Lands (seat) vs UR Tempo (Forge AI)  WON T21  $0.13  harness-epoch-51
Sheet clean: 164 model decisions, 20 forced picks, TAKEN 0, LOST 0, SKIP 0, TOOL 8 of 8 served (inspect 1,
simulate 7), ai_tier_count=0, 22 min. The seat held Rishadan Port on the opponent's lands, then on turn 21 copied
Dark Depths with Thespian's Stage for Marit Lage, which survived its upkeep trigger and attacked for 20.
- clean  the combo line, the Port activations, the Marit Lage upkeep trigger, mulligan, blocks. First of three.

### g12  2026-10-18  seed 4112  Esper Blink (seat) vs Mardu Energy (Forge AI)  WON T18  $0.15  harness-epoch-51
Sheet clean: 110 model decisions, 18 forced picks, TAKEN 0, LOST 0, SKIP 0, TOOL 11 of 11 served (inspect 2,
simulate 9), ai_tier_count=0, 20 min. The seat exiled Orcish Bowmasters with Emperor of Bones, Solitude'd Hexing
Squelcher through ward, Ephemerated Overlord with its trigger on the stack, and saved Emperor from Swords to
Plowshares with a second Ephemerate.
- checked  "play: cancelled Ephemerate @122 -> re-ask" (line 352): the model chose the cast, then the cancel option at
  targeting ("neither target benefits"); the rebound trigger it declined was the other copy (@9). Model's choice.
- checked  plan_audit HOLD-VIOLATION T14 (Ephemerate against "keep Ephemerate available"): model-chosen, not a defect.
- clean  blink targets, Solitude evoke-free cast, impending Overlord, mana held. Second of three.

### g13  2026-10-18  seed 4113  Death and Taxes (seat) vs TES (Forge AI)  WON T14  $0.05  harness-epoch-51
Sheet clean: 67 model decisions, 4 forced picks, TAKEN 0, LOST 0, SKIP 0, TOOL 7 of 7 served (inspect 2,
simulate 5), ai_tier_count=0, 10 min. Forge's AI went off with Tendrils of Agony early; the seat survived it and won.
- checked  passes with "2 castable" on turns 2-4 (Aether Vial at 0-1 counters, no one-drop in hand, Plains): the
  model's reasons name the counters and the hand; the menu held everything legal.
- checked  `counter-type(forced-only-legal)` x3 (Vial's upkeep counter: charge is the only kind).
- clean  mulligan, Vial triggers, yields. Third clean game in a row on epoch 51; epoch 52 (rules pilot fixes:
  "up to" targets, lethal tap damage, whole-block-set legality) restarts the count.

### g14  2026-10-18  seed 4114  Pauper White Weenie (seat) vs Rakdos Delirium (Forge AI)  WON T14  $0.03  harness-epoch-52
Sheet clean: 43 model decisions, 2 forced picks, TAKEN 0, LOST 0, SKIP 0, TOOL 6 of 6 served (simulate 6),
ai_tier_count=0, 8 min. First game on epoch 52; no [BLOCK-INVALID] and no block sent back.
- not exercised  the whole-block-set check: Moonshadow (menace) never came down; one block ask, answered NONE.
- clean  attacks with the Inspectors and Raffine's Informant, the block ask, previews. First clean game on epoch 52
  (epoch 53, from rules puzzle batch 2, is built and starts the count again once installed).

### g15  2026-10-18  seed 4115  The Rock (seat) vs Broodscale (Forge AI)  WON T15  $0.07  harness-epoch-53
Sheet clean: 87 model decisions, 13 forced picks, TAKEN 0, LOST 0, SKIP 0, TOOL 6 of 6 served (simulate 6),
ai_tier_count=0, 14 min. First game on epoch 53.
- checked  13 forced picks: 7 `color(forced)` (a dual paying a shard only one of its colours fits: Badgermole Cub,
  Abrupt Decay), 5 `replacement-order(forced-only-legal)` (one option each), 1 counter-type.
- not exercised  the whole-block-set check again (no block ask with menace or a requirement).
- clean  Abrupt Decay on Basking Broodscale, earthbend target, previews. First clean game on epoch 53.

### g16  2026-10-18  seed 4116  Mono-Green Landfall (seat) vs Legacy Hogaak (Forge AI)  WON T14  $0.07  harness-epoch-53
64 model decisions, 35 forced picks, TAKEN 0, LOST 0, SKIP 0, ai_tier_count=0, 11 min. TOOL 5 of 7 served.
- FIXED  two `simulate` calls failed, "Couldn't map Enroot (151)" (lines 134, 169, T6-T8): Carnivorous Cultivator
  enters prepared and an effect remembers its prepared spell, which no copied zone held, so Forge's GameCopier threw.
  The preview parks such remembered cards, and static effects that affect them, for the copy and restores them
  exactly. Test `aPreparedCardsRememberedSpellDoesNotBreakThePreview` (the fixture builds the end state; the game's
  route to it is not reproduced). lookahead-preview-v13.
- FIXED  (found tracing the prover's heap) Forge's static AiCache was never cleared for a model seat, so answers
  keyed by the game lasted the whole game. The seat clears it where Forge's AI does. Test
  `theSeatClearsForgesAiCacheWhereForgesAiDoes`. Both folded into harness-epoch-54.
- checked  35 forced picks: 25 counter-type (Mossborn Hydra and landfall counters, one kind each), 6
  replacement-order (one option), 2 damage-assign to a single blocker, 1 target, 1 graveyard order.
- not exercised  the whole-block-set check: Moonshadow attacked into one possible blocker; the seat declared none.

### g17  2026-10-18  seed 4117  Burn (seat) vs Goblins (Forge AI)  WON T11  $0.04  harness-epoch-54
Sheet clean: 61 model decisions, 0 forced picks, TAKEN 0, LOST 0, SKIP 0, TOOL 6 of 6 served (simulate 6),
ai_tier_count=0, 9 min. First game on epoch 54 (AiCache cleared at the seat's priority ask).
- checked  plan_audit HOLD-VIOLATION T11 Fireblast against "only if it creates lethal": it was the lethal turn.
- clean  burn targets, previews. First clean game on epoch 54.

### g18  2026-10-18  seed 4118  Pauper Mono-Blue Delver (seat) vs Broodscale (Forge AI)  LOST T18  $0.04  harness-epoch-54
Sheet clean: 58 model decisions, 11 forced picks, TAKEN 0, LOST 0, SKIP 0, TOOL 5 of 5 served (simulate 5),
ai_tier_count=0, 9 min. First loss of the run.
- checked  plan_audit HOLD-VIOLATIONS x4 (Counterspell T7, T8, Dispel T9, Sleep of the Dead T15): each is the spell
  the plan held mana for, cast; the matcher reads "keep mana for X" as a hold. Model-chosen.
- checked  `exile-cost(forced-only-legal)->3` (Sleep of the Dead's escape: exactly three other cards in the graveyard),
  5 damage-assign to a single blocker, 3 single-target picks, 2 graveyard orders.
- clean  two block asks (NONE; one block), counter targets, escape cast. Second clean game on epoch 54.

### g19  2026-10-18  seed 4119  Legacy Boros (seat) vs Legacy Elves (Forge AI)  WON T10  $0.03  harness-epoch-54
Sheet clean: 35 model decisions, 7 forced picks (each one legal option: a target, two counter kinds, Amped Raptor's
cast pick, Thalia's one ability, a colour, a replacement order), TAKEN 0, LOST 0, SKIP 0, TOOL 2 of 2 served,
ai_tier_count=0, 5 min. Third clean game in a row on epoch 54 (g17-g19); epoch 55 (choice-list dividers) restarts
the count. The whole-block-set check is still not exercised in a real game (no block asks).

### g20  2026-10-18  seed 4120  Modern Urzatron (seat) vs Death and Taxes (Forge AI)  WON T15  $0.05  harness-epoch-55
Sheet clean: 60 model decisions, 6 forced picks (2 single targets, 2 damage-assigns to one blocker, a counter kind, a
replacement order; optional costs with none payable), TAKEN 0, LOST 0, SKIP 0, TOOL 5 of 5 served, ai_tier_count=0,
12 min. First clean game on epoch 55.
- note  plan_audit change (commit "holding mana for a card..."): its message says the g10-g19 flags fell from 9 to 3;
  measured with the previous script it is 8 to 3.

### g21  2026-10-18  seed 4121  Goblins (seat) vs Pauper White Weenie (Forge AI)  LOST T12  $0.06  harness-epoch-55
Sheet clean: 74 model decisions, 9 forced picks (4 single damage-assigns to one blocker, 4 counter kinds, a graveyard
order), TAKEN 0, LOST 0, SKIP 0, TOOL 3 of 3 served (inspect 3), ai_tier_count=0, 10 min. Two block asks, both NONE
(model's choice with one attacker against three blockers). Second clean game on epoch 55; epoch 56 (combat damage
asked as amounts under the free-division rule) is built from what the rules puzzles found and restarts the count.

### g22  2026-10-18  seed 4122  Modern Rakdos Delirium (seat) vs Pioneer The Rock (Forge AI)  WON T11  $0.07  harness-epoch-56
Sheet clean: 66 model decisions, 4 forced picks (3 single-option replacement orders; all 5 to a lone non-trample
blocker), TAKEN 0, LOST 0, SKIP 0, TOOL 11 of 11 served (inspect 3, simulate 8), ai_tier_count=0, 9 min. First clean
game on epoch 56. No multi-blocker or trample assignment came up, so the new amounts ask was not exercised live.

### g23  2026-10-18  seed 4123  Mono-Green Landfall (seat) vs Pauper White Weenie (Forge AI)  WON T14  $0.11  harness-epoch-56
87 model decisions, 29 forced picks, TAKEN 0, LOST 0, SKIP 0, ai_tier_count=0, 14 min. TOOL 10 of 12 served.
- exercised  the amounts ask, live: a trampler over three blockers, "ForgeAI=51, Martyr's Soul=2, Leonardo=3, Raffine's
  Informant=2" (each blocker its lethal, the rest over). First real game to meet it.
- FIXED  two `simulate` calls failed again with "Couldn't map Enroot" (T12, T14), on a build carrying epoch 54's
  harness-side parking: this time inside the simulator's own copy (GameStateEvaluator.simulateUpcomingCombatThisTurn),
  which the harness cannot reach. GameCopier is now patched (ENGINE-PATCHES.md) and the parking is gone. The fixture
  test fails without the patch; a cast-preview test that passed without it was dropped. harness-epoch-58.
- checked  29 forced picks: 17 counter kinds, 4 replacement orders, 6 single-blocker damage-assigns, 2 single targets.

### g24  2026-10-18  seed 4124  Mono-Green Landfall (seat) vs Modern Rakdos Delirium (Forge AI)  WON T17  $0.07  harness-epoch-58
Sheet clean: 64 model decisions, 7 forced picks (each a single non-trample blocker: Mightform Harmonizer doubled to 16
into Moonshadow had no trample, so all 16 to it was the only split), TAKEN 0, LOST 0, SKIP 0, TOOL 5 of 5 served,
ai_tier_count=0, 7 min. First game on epoch 58; no preview failed (Carnivorous Cultivator was in the deck but the
failing state did not recur, so the GameCopier patch was not exercised live).

### g25  2026-10-18  seed 4125  Legacy Elves (seat) vs Goblins (Forge AI)  WON T10  $0.05  harness-epoch-58
Sheet clean: 55 model decisions, 5 forced picks (2 single targets, 3 damage-assigns to a lone non-trample blocker),
TAKEN 0, LOST 0, SKIP 0, TOOL 10 of 10 served (simulate 10), ai_tier_count=0, 8 min. Second clean game on epoch 58.

### g26  2026-10-19  seed 4126  Miracles (seat) vs Legacy Hogaak (Forge AI)  WON T28  $0.32  harness-epoch-58
Sheet clean: 269 model decisions, 20 forced picks (11 Counterbalance reveals of the one top card, 4 identical trigger
pairs, 2 single targets, a cost it could not pay), TAKEN 0, LOST 0, SKIP 0, TOOL 37 of 37 served (inspect 11,
simulate 25, memory_search 1), ai_tier_count=0, 39 min: the longest and most expensive game of the run, a control
mirror of patience, still inside the $0.40 budget. Third clean game in a row on epoch 58 (g24-g26).
- checked  plan_audit HOLD-VIOLATION T20 Counterbalance: the clause holds "the known top sequence", Counterbalance only
  named inside its parenthesis; a matcher artefact, model-chosen.

### g27  2026-10-19  seed 4127  Lands (seat) vs Modern Urzatron (Forge AI)  lost T26  $0.13  harness-epoch-59
127 model decisions, 15 forced picks, TAKEN 0, LOST 0, SKIP 0, TOOL 12 of 14 served, ai_tier_count=0, 20 min.
- FIXED  T18 "ACTIVATE Rishadan Port [{1}, {T}: Tap target land.]" offered with Port the only untapped source; the two
  simulate calls on it came back "preview unavailable" and the engine logged "Didn't find what to pay". Epoch 59's
  self-pay patch let Port tap for the {1} its own {T} cost needs. A card pays for its own ability now only when the
  two do not both tap it (epoch 60). Test: GoldenChecksTest.aCardThatMustTapForItsAbilityCannotAlsoTapForItsMana,
  watched failing without the fix.
- checked  plan_audit HOLD-VIOLATION T7 and T9 Wasteland: the plan recovers Wasteland with Life from the Loam next
  turn, which presumes it is sacrificed now; model-chosen, a matcher artefact.

### g28  2026-10-19  seed 4128  Vintage Oath of Druids (seat) vs Standard UW Control (Forge AI)  WON T9  $0.08  harness-epoch-60
95 model decisions, 19 forced picks, TAKEN 0, LOST 0, SKIP 0, TOOL 8 of 9 served, ai_tier_count=0, 16 min.
- FIXED  T6 (jsonl:308) the seat previewed Force of Negation's pitch against Seam Rip and got "preview unavailable":
  the preview took a counterspell's targets from the engine's card candidates, empty for a permanent spell. Aimed at
  the card instead it fizzled on the copy and showed Seam Rip resolving. It is now aimed at the copy's stack entry,
  and the report counts exile (lookahead-preview-v15, epoch 61). Test:
  LookaheadProbeTest.aForceOfNegationPitchOnTheOpponentsTurnIsPreviewed, watched failing first. The seat cast Mental
  Misstep instead, which was its choice with the preview missing.
- FIXED (test bridge)  GoldenChecksToolTest's block re-asks failed since epoch 59: ToolAnswerBridge showed the script
  only the first message, not the rejected answer's reason a model reads. Now 828/828.
- checked  plan_audit HOLD-VIOLATION T5 Stock Up: the clause weighs "develop with Stock Up or hold up protection";
  model-chosen, a matcher artefact.
