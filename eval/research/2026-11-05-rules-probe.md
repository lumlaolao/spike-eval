# A static, engine-labelled rules probe: is it worth building? (2026-11-05)

Question: the propose arm made Forge a type checker for plays (ARCHITECTURE.md, "The propose condition"). Does that
give a cheap, static, auto-labelled rules-knowledge benchmark, items of the form (board state, proposed play) ->
legal? and if not, which rule, that needs no game? This note covers prior work, what the repository's recorded rows
already hold, a prototype (`eval/rules_probe.py`), a small development-lane pilot on Claude Haiku 5.5, and the design
of the engine labeller the prototype does not have.

**Answer: build it, but as a diagnostic beside PUZ and the propose arm, never as a headline score, and only with the
engine labeller (section 6).** The legal/illegal label is exact and free. The negatives mined from recorded rows are
mostly trivial: a state-reading heuristic with three rules scores 0.80 balanced accuracy on them. Of 55,879 illegal
positions, 3,013 (5.4%) are subtle, and the categories of mined negatives can only be inferred. Haiku 5.5 scored
accuracy 0.944 (Wilson 95% 0.85-0.98) on 54 pilot items against the floor's 0.70 (0.57-0.81), 10 of 10 on the subtle
items: the mined items separate a model from the floor, not strong models from each other. The value is in negatives
built on purpose and labelled by the engine with its category, sorted by a difficulty that a floor checks, and the
label is only as exact as the miner that reads the menu (the pilot found 5 wrong items in 60, Wilson 95% 4%-18%, now
fixed). Section 8 says what was not determined.

**Shows** means a source or a run reports it directly; **Inference** is my reading.

---

## 1. Prior work

### MTG rules question sets
- **MTG-Eval** (Hugging Face): 80,032 synthetic QA pairs (27,104 from official rulings, 26,702 card descriptions,
  26,226 combo interactions), 4,001 test examples; answers graded by GPT-4 on a 1-5 scale in the companion project.
  **Shows** (README): https://huggingface.co/datasets/jakeboggs/MTG-Eval/raw/main/README.md ,
  https://github.com/JakeBoggs/Large-Language-Models-for-Magic-the-Gathering . Labels are generated text, scored by a
  model; nothing checks an answer against a rules engine.
- **RulesGuru**: a judge-oriented database of about 7,000 rules questions with "procedural generation of question
  variations", an API, questions submitted by anyone and "reviewed for accuracy by our team of admins"; the licence
  welcomes personal use but **not AI training** or commercial use. **Shows**: https://github.com/KingSupernova31/RulesGuru ,
  https://rulesguru.org/about . This repository's rules-pilot puzzles cite RulesGuru question ids in their `Rule:` line
  (`eval/puzzles/rules-pilot/*.pzl`), so items mined from games on those boards inherit that provenance (section 8).
- **WUBRG-Bench** (a Reddit post, July 2026): a harness over RulesGuru's API; the author suspects the questions are in
  training data; commenters say the score means little unless the model gets the rules text or a rules lookup.
  **Shows (snapshot only, not verified further)**:
  https://reddit.sentinel-team.org/posts/1uhlzck/snapshots/2026-07-03T03%3A00%3A16.73148Z
- **MTG Bench** (MTG Auto Deck, June 2026): no rules engine; an LLM judges whether a turn was legal. **Shows**:
  https://mtgautodeck.com/articles/mtg-bench/ (via the 2026-11-03 note, section 3).
- A 2025 Manifold market used RulesGuru as a test set, with 20 unreleased questions held back for it, which is the
  contamination answer a public question pool needs. **Shows**: https://manifold.markets/IsaacKing/will-ai-be-able-to-accurately-answe
- **Not found**: a peer-reviewed, judge-validated MTG rules benchmark, or any rules-knowledge set labelled by an engine
  (Forge, XMage or another). The 2026-11-03 note's search reached the same result.

### Legality in other games
- **Chess legality as a probe.** "Reasoning or Reciting?" (Wu et al., 2023) asks whether a 4-move opening is legal,
  and in a counterfactual swaps knights and bishops; models fall well below their default-rules accuracy on the
  counterfactual. **Shows** (task design; I did not read the appendix numbers): https://arxiv.org/html/2307.02477v3 .
  **Inference:** a recall-able rule set (chess openings) inflates legality accuracy; counterfactual or unseen positions
  are what measure the rule.
- **Illegal-move rates in play**: SPIN-Bench counts games lost to illegal moves and illegal moves per turn, more in
  chess than in simpler games (https://arxiv.org/pdf/2503.12349); a 2024 puzzle benchmark had GPT-4o propose an illegal
  move 12.7% of the time (https://www.theregister.com/2024/06/04/chess_puzzle_benchmark_llm); ChessBenchLLM reports
  Legal% on the first try (https://chessbenchllm.onrender.com/methodology). Kaggle Game Arena and PTCG-Bench (illegal
  rate 3.3% with legal-action masking, 15.9% without) are in the 2026-11-03 note, sections 2-3.
- **State tracking judged by legal-move sets**: Harang et al. (ICML 2025 workshop) compare the legal-move distribution
  implied by a model's predicted state with the true one, instead of matching strings. **Shows** (abstract only):
  https://icml.cc/virtual/2025/49114 . Othello-GPT (Li et al., ICLR 2023) measured legal-move accuracy of a model
  trained on synthetic games, and a 2025 revisit found near-perfect one-hop legal prediction with enough data
  (https://arxiv.org/pdf/2210.13382 , https://arxiv.org/pdf/2503.04421).
- **Rules of card games**: Bateni, Pratt and Whitehead (AIIDE) generate rule-understanding datasets from Solitaire
  variants because variants can be made at will (https://ojs.aaai.org/index.php/AIIDE/article/view/36804).

### Contamination and saturation
- LiveBench refreshes questions monthly and scores against objective answers to stay contamination-limited
  (https://huggingface.co/papers/2406.19314.md). GSM-Symbolic regenerates items from templates and reports drops when
  only numbers change (https://arxiv.org/pdf/2410.05229); a 2026 re-analysis finds most of those drops not
  significant (https://arxiv.org/pdf/2605.28700). **Inference:** a generator does not by itself prove contamination
  resistance; it makes it testable, because a fresh set can be drawn and compared with the old one.

### What an engine-labelled set adds, and lacks
- **Adds:** an exact label with no judge and no human review (the menu is the engine's enumeration); unlimited fresh
  items from any board the engine can build, so a leaked set can be replaced; the category of a refusal from the same
  checker the propose arm uses, so the static probe and the in-game measurement share one definition of "illegal".
- **Lacks:** the judge-question genre (layers, replacement-effect ordering, "what happens") is not a legality question
  and is not covered; the label is the propose checker's, so it inherits that checker's defects and Forge's rules
  bugs as wrong labels (the 2026-11-03 note, Jericho's validity oracle has false negatives; section 9.3 found two such
  classes); the category is "the first check the play fails" in Forge's order
  (`LlmSpikeGui.rejection`), which is one valid answer where the rules may admit several.

## 2. The data the repository already holds

Every number in this section comes from the committed miner and two commands, run in this worktree:

```
FORGE_HOME=/root/forge python3 eval/rules_probe.py mine /home/user/mtg-forge-llm-agent/runs/propose \
    /home/user/mtg-forge-llm-agent/runs/demo /home/user/mtg-forge-llm-agent/runs/puzzles \
    --out runs/rules-probe/items-all.jsonl
python3 eval/rules_probe.py stats runs/rules-probe/items-all.jsonl --floor
```

The three run directories (the main checkout's, read only) hold 13,399 `game-*.jsonl` files; 1,599 of them (453
distinct puzzles) contain a play or target row that yields an item. `runs/rules-probe/` is this worktree's
(git-ignored) copy of the output.

| | count | how |
|---|---|---|
| items (state, play, label) | 189,613 | every non-pass menu play (legal), every hand card the menu could not cast or play (illegal), every board permanent a first-choice target ask left out (illegal), every refused proposal; exact duplicates removed |
| distinct prompt states | 32,895 | sha256 of the state text |
| distinct positions | 139,680 | `position_key`: the state without handles, plan, history and log; two attempts at one puzzle collapse |
| legal / illegal | 106,012 / 83,601 | 20,721 of the legal are mana abilities (`[{T}: Add ...]`), left out by `balance` and the floor |
| play / target items | 160,556 / 29,057 | |
| illegal positions | 55,879 | 495 card names |
| engine-verdict negatives | 10 | the only proposals the propose arm refused (section 2.2) |

By source: puzzles 163,597 items, propose runs 24,779 (the keyword-gauntlet audit games and the Haiku pilots), demo
1,237. Epochs: harness-epoch-70 133,650, 72 25,135, 74 21,924, 75 2,855, 71 708, unknown 5,341 (demo games without a
game line). The rows do not carry the Forge commit (`engine_sha` is "unknown" on the game lines read), so every item
carries the repository's `FORGE_COMMIT` at mining with `engine_pin_source: "repo-FORGE_COMMIT-at-mining"`.

### 2.1 Rule-category balance and difficulty of the negatives

`stats` field `illegal_positions_by_cell` (over `position_pool`, the pool `balance` draws from: one item per
position, the first by id):

| kind / category (source) / difficulty | positions |
|---|---|
| play / timing (inferred) / trivial | 29,284 |
| target / target (engine) / trivial | 12,577 |
| play / cost (inferred) / trivial | 9,743 |
| play / cost (inferred) / subtle | 2,227 |
| play / restriction-or-target (inferred) / subtle | 524 |
| play / land drop (inferred) / trivial | 485 |
| play / unknown (inferred) / none | 374 |
| play / unknown (inferred) / trivial | 373 |
| play / timing-or-restriction (inferred) / subtle | 114 |
| play / timing-or-cost (inferred) / subtle | 90 |
| target / target (engine) / subtle | 58 |
| play / timing-or-cost (inferred) / trivial | 20 |
| play / timing (engine) | 5 |
| play / cost (engine) | 5 |

How a category is inferred (`_infer_cast`, in Forge's order of checks): a card script says the card is neither an
instant nor has flash and the window is not a sorcery window -> timing; else the hand's affordability tag says
"need ..." -> cost; else "payable, not castable now" -> restriction-or-target. A land: outside a sorcery window ->
timing; with the land drop used -> land drop. "Trivial" means one line of the state settles it: mana value above the
mana available, a land after the land drop or outside your main phase, a non-instant outside a sorcery window, a
permanent of a kind (land, creature, other) the target ask offered none of (a creature for "target player").
"Subtle" means it does not: a colour shortfall with enough total mana, a payable card the engine still refuses in a
sorcery window, a creature refused where another creature was a legal target.

What is left out on purpose, because the absence is the harness's and not the engine's: a second copy of a land the
menu offers (it lists one PLAY LAND a land name), a card chosen earlier in the same priority window (a play that
failed to pay is not offered again there, `failedInWindow`), and a target ask an earlier choice of the same play
narrowed (X announced, a second or "another" target, a sacrifice in the text).

- **The balance is poor**: timing and target make 75% of illegal positions (41,861 of 55,879);
  zone, control and type never occur (a menu never lists a card in the wrong zone, so its absence cannot make one); restriction appears
  only inside "-or-" categories.
- **Subtle negatives are 3,013 positions (5.4% of 55,879)**; 52,482 (93.9%) are trivial and 384 have no
  difficulty (category unknown or an engine verdict). `stats` field `subtle_positions_by_source`: 1,880 come from the
  keyword gauntlet's random-policy games in `runs/propose` (552 cards); the generated hard-puzzle families give few
  (grave 365, landfall 325, copy 237).
- **Target negatives are mostly a type mismatch**: 12,577 of 12,635 target positions are trivial.

### 2.2 Negatives the models made themselves

`python3 eval/proposal_audit.py runs/propose/<block> --json` on the four propose blocks (haiku-rules-propose,
haiku-rules-propose-v2, e75-haiku-rules-propose, e75-haiku-hard-propose): 468 proposals, 44 format errors, 10
refused. The 10: Lava Spike, Rite of Flame and Gitaxian Probe at the wrong time (timing 5), and cost names a card
does not have (Flashback on Desperate Ritual, Spectacle on Glacial Ray, "alternative" for Mutagenic Growth; cost 5),
all from `runs/propose/e75-haiku-hard-propose`. **Natural negatives are 2.4% of well-formed proposals** (10 of 424,
the refusal rate of first and later tries together; Wilson 95% 1.3%-4.3%): a set made of the plays models actually
get wrong would take thousands of propose decisions per hundred items. They are the most valuable items (a real
model's real mistake, with the engine's category and sentence) and should be kept whenever a propose run makes them.

### 2.3 The trivial floor

`rules_probe.heuristic` reads the state only: a land needs a sorcery window and an unused land drop; a card whose mana
value exceeds the mana available, or a creature (a P/T in the hand line) outside a sorcery window, is illegal; a land
is not a legal target; everything else is legal. `stats --floor` scores it over `position_pool`, the positions
`balance` draws from (one item per position, mana abilities left out; 120,841 positions):

| stratum | positions | floor right |
|---|---|---|
| balanced accuracy, all | 120,841 | **0.801** (recall legal 0.979, illegal 0.622) |
| legal play | 61,611 | 97.8% |
| legal target | 3,351 | 99.1% |
| illegal play, trivial | 39,905 | 75.3% |
| illegal play, subtle | 2,955 | 2.4% |
| illegal target, trivial | 12,577 | 36.7% |
| illegal target, subtle | 58 | 1.7% |

**A set drawn from the mined distribution has a floor of 0.80**, so a model at 0.85 would know almost nothing beyond
reading mana and phase. On the subtle strata the floor is right on about 2% of negatives against about 98% of legal
items, so its balanced accuracy there is near 0.5, which is where a score would mean something.

## 3. The prototype: `eval/rules_probe.py`

Stdlib, tested by `eval/tests/test_rules_probe.py` (34 tests, written first from recorded rows and each seen red
before the code that passes it; removing the hand-tag stripping or the "(was @N)" handling also turns a test red).

- **Item** (`rules-probe-item-v1`): `state` (the recorded prompt up to its DECISION line, hand legality tags, the
  seat's plan and its last play's note removed), `proposal` (`verb` cast / play_land / activate / target, `card`
  handle, `name`, and for a target its `source` and `source_text`), `label`, `label_source` (menu, menu-absence,
  engine-verdict), `category`, `category_source` (engine or inferred), `reason` (the engine's sentence, for engine
  verdicts), `difficulty`, `id` (sha256 of state and proposal), `provenance` (`harness_epoch`, `observation_version`,
  `overlay_sha`, `engine_pin`, `engine_pin_source`, `source_run`, `source_file`, `row`, `puzzle`, `propose_actions`).
  A written set stores each state once.
- **Commands**: `mine`, `stats` (`--floor` adds the floor per stratum), `balance` (one item per position, at most N
  per kind/category/difficulty cell, as many legal as illegal), `ask` (the Claude CLI development lane: a
  `claude-cli:<model>` spec of a listed Haiku or Sonnet model, refused without `CLAUDE_CLI_SEAT=1`
  (`cost_report.cli_opt_in_refusal`); the child gets the allow-listed environment of `ClaudeCliBridge.childEnv`, so
  an API key or a cloud provider's switch never reaches it unless `CLAUDE_CLI_ALLOW_API_KEY=1`), and `score`
  (accuracy with its Wilson interval, balanced accuracy, recall per class, the always-legal and the state-reading
  floors on the same items, category accuracy on engine-categorised items only).
- **Why the state is cut at DECISION and the tags and notes removed**: the menu and the hand's "(castable)" /
  "(need 1 more R)" tags are the engine's answer (`LlmSpikeGui.affordability`, `AskContract.HAND_TAG`), and 17 of the
  mined states quoted the seat's plan or its last play, which can name the answer.
- **The labels' soundness rests on two harness properties**: the play menu lists every legal play (owner rule; "a
  legal action is never hidden"), and the target list lists every legal target. Both hold for what the engine can
  do, but **not always for how the menu writes it** (the three exclusions of section 2.1). The menu's one PLAY LAND a
  land name is seen in `runs/demo/luna-elves-goblins-s9191` row 63 (Mountain @18 offered with @20, @21 and @25 in
  hand); spells are listed a copy each (counted over 400 game files each of `runs/puzzles` and `runs/propose`).
- The card index finds a script by a file name guessed from the card's name; a card whose script is named otherwise
  (split and double-faced cards) reads as unknown, and its category falls back to the affordability tag.

## 4. Pilot: Claude Haiku 5.5 (development lane, not a score)

`python3 eval/rules_probe.py balance <items> --per-cell 2 --seed 1105`, on the first miner's output, gave 60 items: 30
illegal (2 per kind/category/difficulty cell, so subtle cells are over-represented against the mined distribution)
and 30 legal, from five epochs (mixed on purpose for a pilot; a score would take one). `CLAUDE_CLI_SEAT=1 python3
eval/rules_probe.py ask <set> --model haiku` (the first version's form; the committed one takes
`claude-cli:claude-haiku-5-5`), card text from Forge's card scripts prepended for the proposed card, no rules lookup;
notional CLI cost $0.066. Files (git-ignored, this worktree): `runs/rules-probe/pilot-1105/set-pc2.jsonl` (the 60),
`haiku-pc2.jsonl` (the answers).

**The pilot found label errors in the first miner: 5 of the 60 items were wrong (8%; Wilson 95% 3.6%-18.1%, the
share of pilot items whose label or question was wrong)**, all read by hand: a second Mountain labelled illegal (the
one-land-a-name menu; Haiku had answered "legal", rightly), Memnite as a land play (a zero-cost spell has no cost in
the hand line, like a land), and three items whose card name swallowed the next hand entry because of an earlier
handle "(was @N)" in the hand line. All three faults are fixed and tested
(`test_a_second_land_of_a_name_the_menu_offers_is_not_an_illegal_item`,
`test_a_zero_cost_spell_is_a_cast_not_a_land_when_its_script_is_known`,
`test_an_earlier_handle_in_the_hand_line_does_not_merge_two_cards`). A sixth item, a legal target ask of Krenko, Mob
Boss, is no longer mined (its source sacrifices); the final miner's residual error rate was not measured.

`python3 eval/rules_probe.py score runs/rules-probe/pilot-1105/set-pc2.jsonl \
runs/rules-probe/pilot-1105/haiku-pc2.jsonl --reproduced-by runs/rules-probe/items-all.jsonl` scores the 54 items the committed miner reproduces
(`rules_probe.reproduced`: the same file, row, verb and handle with the same label, and a name without a handle in
it). The states scored are the pilot's own, as Haiku was asked them: they still carry the seat's plan line and its
last play's note, which the committed miner now strips.

| | n | Haiku correct | state-reading floor correct |
|---|---|---|---|
| all | 54 | 51: accuracy **0.944** (Wilson 95% 0.85-0.98), balanced 0.946 | 38: accuracy 0.704 (Wilson 95% 0.57-0.81), balanced 0.68 |
| legal | 29 | 27 | 29 |
| illegal, trivial | 9 | 9 | 8 |
| illegal, subtle | 10 | 10 | 1 |
| illegal, engine verdict or unknown | 6 | 5 | 0 |
| category, on engine-categorised illegal items Haiku called illegal | 8 | 8 | n/a |

Haiku's three errors: it called Craterhoof Behemoth castable where the state's own estimate reads "up to 8 mana" and
the engine refuses the cast (the "(not castable now)" tag, category unknown: the estimate counts mana from creatures
that may not be able to tap), and called a castable Cylian Elf and a legal Siege-Gang Commander activation cost
errors.

**What it shows**: the items discriminate against the floor (0.944 against 0.70) but not among strong models: Haiku is
near the ceiling, including on the subtle stratum (10 of 10). With 54 items the interval is 0.13 wide, so "near the
ceiling" is all it can say. A probe that separates models needs harder negatives than the mined ones (section 6), and
the Craterhoof item shows a fairness question: an item where the state's printed estimate and the engine disagree
tests the observation as much as the rules.

## 5. What it would add to the benchmark

- **A cheap rules diagnostic beside the propose arm.** The propose arm measures rules errors in play, but models make
  few (2.4% of well-formed proposals, Wilson 95% 1.3%-4.3%) and only where they wander; the probe asks the same question on chosen positions, at a cost
  of one short call an item and no game.
- **Not a headline.** The three scores (STR-AI, STR-HELDOUT, PUZ) measure play. A static legality probe measures a
  precondition of play; it belongs in the scorecard's diagnostics with its floor beside it.
- **Contamination:** the items are fresh positions from games, not published questions, and can be redrawn (a
  new seed of the keyword gauntlet is a new set for free). Rules-pilot boards are adapted from RulesGuru questions,
  whose licence forbids AI training; a released set should leave those boards out or ask.

## 6. The engine labeller (designed, not built)

*Built later the same day: section 9; how the labeller is actually run (a class named Export, not a test the
acceptance runner runs, in shards) is section 9.1 and the class comment of `RulesProbeLabelExport`.*

Mined rows give exact legal/illegal labels but only inferred categories, and they give negatives only where a game
happened to reach them. A labeller that runs the propose checker on a built board fixes both:

1. **Entry point**: a TestNG class in `forge-overlay/forge-gui-desktop/src/test/java/forge/spike/`
   (`RulesProbeLabelTest`), run by acceptance's TestNG runner with `-Dharness.probe.out=<file>`, so it needs no new
   launcher. It builds each board the way `PuzzleSetupTest.boardOf` does (a `.pzl` file, `LlmSpikePuzzle.applyBoard`
   on a "Game-" thread), with `AnswerTool.PROPOSE_FOR_TEST = true` as `ProposeActionsTest` sets it.
2. **Candidates**: for the solver at the board's priority, every card in every zone x every verb (cast, play_land,
   activate with each ability number, each named cast way the card has, and the wrong ones: "Flashback" on a card
   without it), and for each targeted play each object and player as its target.
3. **Label**: `matchProposal` against `enumeratePlayOptions` (the `verdict` helper of `ProposeActionsTest`): null is
   legal, "ILLEGAL (category): sentence" is the engine's category and reason, "FORMAT:" is dropped. The state is the
   seat's snapshot (`ctrl.snapshot()`), the same text a propose seat reads.
4. **Near misses on purpose**: from each board, variants the `.pzl` format can express in one line: move the board to
   the opponent's turn or to combat (a sorcery at instant speed), tap one land (an unpayable cost), set "lands played
   1" (a second land), give a target creature hexproof or protection, put a spell with split second on the stack. Each
   variant is labelled by the engine, never by the edit that made it, so an edit that does not break the play gives a
   legal item, which is the contrast pair a probe needs.
5. **Sources**: the 1,372 `.pzl` boards under `eval/puzzles` (hard 447, rules-pilot 925 with drafts), and positions
   saved from free keyword-gauntlet games (552 cards).

Cost: one JVM, seconds a board; no model. I did not build it here (time); `ProposeActionsTest` already pins every
rejection category on a constructed board, which is the evidence it would work.

## 7. Recommendation and next steps

1. Build the engine labeller (section 6) and draw a v1 set of about 400 items: 200 illegal stratified by engine
   category with a subtle quota (at least half), 200 legal drawn from the same positions (contrast pairs), one epoch,
   the state-reading floor reported beside every score.
2. Keep every engine-verdict negative a propose run produces (they are the models' own errors) as a separate stratum.
3. Score it, in the development lane first, on Haiku; then on Luna only with the owner's consent (paid). Haiku's
   0.944 on mined items says the v1 set must be harder than the mined distribution to be worth a model's call.
4. Do not mine more of the current distribution: 190k items add little over a few thousand, and 94% of their negatives
   are trivial (52,482 of 55,879 illegal positions, section 2.1). If mined items are wanted, the free source with the
   most card variety is the keyword gauntlet with the random policy (`bash eval/keyword_gauntlet.sh`, no model, 552 cards),
   which gave most of the subtle positions here.
5. Audit labels before trusting any set: read a sample of every new negative class by hand, as the pilot did; three
   miner faults were found only that way.

## 8. What was not determined

- **Label noise.** I read 36 sampled negatives by hand while building the miner, and the pilot's 60 items after it;
  the pilot found 5 wrong items (8%, Wilson 95% 4%-18%) that the 36 had not; review then found two more harness
  behaviours that make an absence the harness's (a play that failed to pay is not offered again in its window; a
  target list narrowed by an earlier choice), now excluded, all from how the menu and the hand line are written, not
  from the engine. They are fixed; the final miner's residual rate is not measured. A Forge rules bug would also be a
  wrong label, and no judge reviewed any item.
- **Categories of mined negatives** are inferred (section 2.1); only 10 + 24,852 items carry an engine category
  (the refused proposals and the target asks), and the target ones say only "target".
- **Whether the probe predicts play.** No correlation between probe accuracy and STR or PUZ was measured; with one
  model in the pilot it cannot be.
- **The pilot's size** resolves only large differences (54 scored items: a Wilson interval 0.13 wide at 0.944), the
  items mix five epochs and over-sample subtle cells, so its rates are not the mined distribution's, and one model
  says nothing about whether the probe ranks models.
- **Activated abilities' negatives** are not mined: a menu row does not list the abilities a card has but cannot use
  now (the propose arm's YOUR ACTIVATED ABILITIES does; only its refused proposals were used).
- **Licensing** of boards adapted from RulesGuru for any released set.

---

## 9. The engine labeller, built (2026-11-05)

**Provenance.** Every number in this section comes from one export on a `harness-epoch-80` build, before PR #360
(epoch 82) changed the propose checker; it was not re-run after. Under #360 a copy that cannot pay its own cost is
refused (a tapped Mountain's `{T}` is now `cost`), so the export drops no `SUBSTITUTED` rows (`substituted_dropped`
is always 0), and the category order moved (control before cast ways; the rest of the cost before zone or
restriction). The commands below, run at a later build, give a different set.

**Answer: the labeller works, but its labels are the propose checker's, and the audit found two classes of wrong
ones.** Of 60 negatives drawn from the final set and read by hand, 56 were illegal (Wilson 95% 0.84-0.97 over the
sample; per-cell draws, so not an estimate over the set's 524,040 negatives); all four errors are legal plays the
checker refuses because the play menu does not offer them (9.3). Categories were right on 51 of 60. Of 30 legal
items, 30 were legal. Over the 1,371 boards the labeller gives 572,952 items with every rejection category the engine
names, and 7,995 contrast pairs (one play, legal on a board and illegal on a one-line variant, or the reverse). Most
negatives are trivial by construction (every card with every verb), so a floor that reads zones scores 0.849 balanced
accuracy over the set; on a 96-item contrast set drawn to strip that out, Haiku 5.5 scored balanced 0.968 against
that floor's 0.683 balanced. The set separates a model from the floor; Haiku is near the ceiling again.

*What moved after review of PR #359:* the position key (9.2) kept two same-name cards of one state apart (823 old
positions had merged two labels), which moved every position count, the subtle stratum and the contrast set; the
zone floor (0.849) did not move. The first version of this section reported 60 of 60 negatives right on two samples
drawn per cell, half of them from an export before the priority fix; both samples are now drawn from the final set
by a committed sampler, and they found the four wrong labels. The Haiku numbers are on the new draw (34 of its 96
items were in the old one).

### 9.1 What was built

- `RulesProbeLabelExport` (`forge-overlay/forge-gui-desktop/src/test/java/forge/spike/`, named Export so acceptance
  compiles it and never runs it): what it proposes, what it drops, the variants, how to run it and why in shards are
  in its class comment. `RulesProbeLabelTest` holds the always-on tests: one built board and its variants labelled
  with known legal and illegal items and categories, the export's rows, and the output path refused inside the
  repository (outside `runs/`) or the Forge checkout.
- Defects found while building it, each a test: the seat named "Seat" beside the player "Solver" (`parse_window`
  read every board as the opponent's turn); "Keyword Effects", the engine object holding a player's hexproof under
  Leyline of Sanctity, offered as a card (effect objects are left out); the checker's mana-ability fallback accepting
  a tapped Mountain's `{T}` ability because an untapped copy was on the menu (in this run such rows were dropped,
  `SUBSTITUTED`, with some legal items, a second untapped copy's ability among them; #360 fixed the fallback after
  this run, and the labeller no longer drops any); the opponent holding priority in
  the opp-turn and split-second variants (the solver is now given priority). The first three were seen red before
  their fix; the seat name was asserted with its fix.
- `eval/rules_probe.py`: `engine` (rows to items), `engine-report` (boards, copies, drops, conflicts, from the rows),
  `audit-sample` (positions of one label, round robin over cells), `zone_floor`, `contrast_pairs`,
  `balance --contrast`, and in `score` the contrast pairs right on both sides and a category baseline.

Run directory: `runs/rules-probe/engine-label-1105/` in the main clone (git-ignored): `raw-0..5.jsonl.gz`
(the labeller's rows), `items.jsonl.gz`, `stats.json`, `report.json`, `set-contrast.jsonl` (the 96 items),
`haiku-contrast.jsonl` (Haiku's answers), `audit-illegal-1105.txt`, `audit-illegal-2026.txt`, `audit-legal-1105.txt`
(the samples), `audit-verdicts.jsonl` (each sampled id with its verdict and, for a wrong one, why) and
`issue-drafts/` (the three defect reports of 9.3, written to the defect-hunt format and not yet filed). Commands, from
the repository root, after the export of the class comment (`-Dharness.probe.out=$PWD/runs/rules-probe/<name>/raw-K.jsonl.gz`):

```
python3 eval/rules_probe.py engine <dir>/raw-[0-5].jsonl.gz --out <dir>/items.jsonl.gz
python3 eval/rules_probe.py stats <dir>/items.jsonl.gz --floor --json > <dir>/stats.json
python3 eval/rules_probe.py engine-report <dir>/raw-[0-5].jsonl.gz > <dir>/report.json
python3 eval/rules_probe.py balance <dir>/items.jsonl.gz --per-cell 5 --seed 1105 --contrast --out <dir>/set-contrast.jsonl
python3 eval/rules_probe.py audit-sample <dir>/items.jsonl.gz --label illegal --n 30 --seed 1105   # and --seed 2026
python3 eval/rules_probe.py audit-sample <dir>/items.jsonl.gz --label legal --n 30 --seed 1105
CLAUDE_CLI_SEAT=1 python3 eval/rules_probe.py ask <dir>/set-contrast.jsonl --model claude-cli:claude-haiku-5-5 \
    --out <dir>/haiku-contrast.jsonl
python3 eval/rules_probe.py score <dir>/set-contrast.jsonl <dir>/haiku-contrast.jsonl
python3 eval/rules_probe.py count <dir>/items.jsonl.gz --label illegal --name "Lantern Bearer" --category target
python3 eval/rules_probe.py count <dir>/items.jsonl.gz --label illegal --reason-has "mana cost" \
    --solver-board-has "Heritage Druid"
```

The export was run twice, before and after the review's changes; every shard's rows and drop counts were the same.

### 9.2 The set

From `report.json` (`engine-report`, the rows and the labeller's summary rows):

| | count |
|---|---|
| `.pzl` files under `eval/puzzles` | 1,372, all read by the labeller (`boards_labelled`) |
| boards with a state | 1,371: `rules-pilot/batch14-drafts/d2_9` throws in Forge's setup in all 7 of its variants (`failed`) |
| distinct boards | 1,042: 329 drafts are copies of a released rules-pilot board (`boards_copying_another`) |
| board variants put down | 9,959 (`state_rows`); 7,718 distinct states; 34 `summon-sick` edits change nothing the state shows |
| rows written | 640,507, after 11,179 FORMAT and 13,684 substituted rows were dropped |
| conflicting labels | 0: no state and proposal carry two labels (`label_conflicts`) |

From `stats.json` (`stats --floor`):

| | count |
|---|---|
| items | 572,952: legal 48,912 (10,792 of them mana abilities), illegal 524,040; 1,060 card names among negatives |
| positions | 410,136; `position_pool` (mana abilities out) 399,859: legal 30,639, illegal 369,220 |
| positions with two labels | 0 (`position_label_conflicts`) |

A position is the item without its handles, for an engine item with each object named by what the state says of it
(side, zone, its entry: "Swamp [tapped]", "Hill Giant 3/3 [untapped] [hexproof]"). The first key stripped handles
and named no zone, so a Swamp in hand and one on the battlefield were one position with whichever label came first:
823 such positions had merged two labels (pr-review of #359).

Illegal items by engine category: cost 134,468, zone 130,807, target 127,432, type 118,302, timing 11,269, control
981, restriction 635, land drop 146.

**The floors.** The note's state-reading floor (`heuristic`, section 2.3) cannot see zones: balanced accuracy
**0.550** over the positions (recall legal 0.983, illegal 0.118). `zone_floor` adds where things are (its docstring):
**0.849** balanced (recall legal 0.966, illegal 0.733). Any score on this set must beat 0.849.

**Difficulty**, defined by that floor: *trivial* when `zone_floor` calls a negative illegal, *subtle* when it does not,
*card-text* when it is subtle only because it names a way to cast the card lacks. Positions: trivial 270,603,
card-text 80,273, subtle **18,344** (5.0% of illegal positions); the zone floor is right on none of the last two by
construction. `subtle_positions_by_reason` (`stats`, `reason_kind`): a target the spell's text rules out 9,477; split
second on the stack 2,630; a sorcery-speed spell at instant speed 1,791; a target in a zone the spell does not target
1,433; a static forbids the target 1,242; mana of the wrong colour or amount 561; an ability's own limits or an
effect 540; a `{T}` cost of a tapped or sick permanent 387; a sorcery-speed ability 148; the rest under 135 each.

**Contrast pairs** (`contrast_pairs_by_variant`): 7,995. Split second 2,696, opponent's turn 1,561, combat 1,561,
Leyline 1,338, a tapped land 521, a sick creature 165, a land played 146, a hexproof creature 7. In all 7
`opp-hexproof` pairs the edit made a Craterhoof Behemoth cast legal. The first version of this section read that as
the engine at work (Gladecover Scout is an Elf, and Priest of Titania counts Elves). In the one pair checked by hand
(`hard/gen2/hoofdrain_4f7ac995ab62`), Craterhoof was already castable on the base board: Priest of Titania makes 4,
Heritage Druid taps itself, Nettle Sentinel and Fyndhorn Elves for GGG, and two Forests make 9. So the base label is
wrong, which is the second class in 9.3.

### 9.3 Label audit (section 7 item 5)

Two samples of 30 negatives (`audit-sample --label illegal`, seeds 1105 and 2026) and one of 30 legal items
(`--label legal`, seed 1105), all from the final set, read by hand against the Comprehensive Rules, the state and
the card's text. The sampler draws round robin over cells, so every cell is sampled about equally whatever its size.
In each negative sample, 13 cells (kind, category, difficulty) gave 2 items and 4 gave 1 (play/zone and target/target,
trivial and subtle). The legal sample drew 1 per (kind, verb, variant, mana ability) cell over 30 cells (the sampler's CELLS line names the
mana flag since review round 3; the first printout merged the two and read 22 cells). One negative
was drawn in both samples. Verdicts, with the reason for each miss: `audit-verdicts.jsonl`.

| sample | n | label right | Wilson 95% | category right | Wilson 95% |
|---|---|---|---|---|---|
| negatives, seed 1105 | 30 | 28 | 0.79-0.98 | 26 | 0.70-0.95 |
| negatives, seed 2026 | 30 | 28 | 0.79-0.98 | 25 | 0.66-0.93 |
| negatives pooled (59 distinct) | 60 | 56 | 0.84-0.97 | 51 | 0.74-0.92 |
| legal, seed 1105 | 30 | 30 | 0.89-1.00 | n/a | |

The intervals are over the sample. Because cells are drawn equally, a cell's errors weigh by the cell's share of the
sample, not of the set; the rates are not estimates over the 524,040 negatives.

**The four wrong labels**, two classes, both legal plays the menu does not offer, so the checker refuses them:
- *Disturb onto an Aura* (`rules-pilot/authored-drafts/y_105`, base and opp-hexproof): Lantern Bearer's Disturb casts
  Lanterns' Lift, an Aura, from the graveyard for {2}{U}; with UUU floating and Grizzly Bears to enchant it is legal
  (it is that board's own solution), and the checker answers "it has no legal target now". In the set: 12
  positions of that board (the cast named as Disturb, as "alternative" and as the face, on the four variants where the
  target is the refusal; its timing refusals are right), `count --label illegal --name "Lantern Bearer" --category
  target` (9.1).
- *Mana from Heritage Druid* (`hard/gen2/hoofdrain_1760166562b2` base, `hoofdrain_4962cb5b4d19` summon-sick):
  Craterhoof Behemoth is payable (Priest of Titania's Elves plus Heritage Druid's "tap three untapped Elves", which
  has no {T} symbol, so a sick Druid can pay it), and the checker answers "you cannot pay its mana cost now". At
  most 194 mana-cost negatives on 47 boards have Heritage Druid on the solver's battlefield (188 of them Craterhoof;
  `count --label illegal --reason-has "mana cost" --solver-board-has "Heritage Druid"`, 9.1); not all are wrong, and
  they are a large share of the small play/cost/subtle cell (948 positions).

Both are the menu's (`enumeratePlayOptions`, `seatCanPay`), so a menu seat on those boards cannot make the play
either: "a legal action is never hidden" does not hold there. Both are written up for a fix in the defect-hunt
format (`issue-drafts/` in the run directory: `not-offered`, sev:high each), with a third, `not-shown` (sev:low): the
state lists "Keyword Effects", the engine object holding a player's hexproof, as a card with a handle in the command
zone (`SeatObservation`'s command lines). Filing them is pending. The checker's own defects (the category order, the
mana-ability copy fallback) were fixed by PR #360 (epoch 82), after this run.

**The category misses** (5 among the 56 right labels, 9 counting the wrong labels): at epoch 80, `rejection` asked
`playable` before it checked a non-mana cost and answered "restriction" or "zone" whatever made it fail (#360 now
checks the rest of the cost first): Dread Return's flashback with too few creatures, Colossal Dreadmaw's escape under Underworld Breach, Spawning Pit with no charge counters and Eldritch
Evolution with no creature to sacrifice are cost; Reassembling Skeleton's ability on the battlefield is zone. The
propose arm's category counts from that epoch inherit this.

One legal item is flagged, not counted wrong: The One Ring activated in a cleanup step (`rules-pilot/RP_096`,
opp-leyline). It is legal with the priority the state gives the solver, but in a cleanup step no player normally gets
priority (CR 514.3): the board's own premise, which the labeller keeps.

### 9.4 Haiku 5.5 on a contrast set (development lane, not a score)

`balance --per-cell 5 --seed 1105 --contrast` drew 96 items: 52 negatives with a contrast partner, 5 per (kind,
category, difficulty) cell where the cell had 5 (11 cells), and their 44 legal partners. `ask` with
`claude-cli:claude-haiku-5-5` and `CLAUDE_CLI_SEAT=1`, card text prepended, no rules lookup; notional CLI cost $0.045.
One epoch (harness-epoch-80), one engine pin.

| | n | Haiku | zone floor | state-reading floor |
|---|---|---|---|---|
| all: accuracy | 96 | 93: **0.969** (Wilson 95% 0.91-0.99) | 65: 0.677 | 61: 0.635 |
| all: balanced accuracy | 96 | **0.968** | **0.683** | 0.653 |
| legal | 44 | 42 | 33 | |
| illegal, trivial | 32 | 31 | 32 | |
| illegal, subtle | 20 | 20 | 0 | |
| contrast pairs, both sides right | 52 | 46 (Wilson 95% 0.77-0.95) | 14 (0.17-0.40) | |
| category, on negatives Haiku called illegal | 51 | 25: 0.49 (0.36-0.62) | always the commonest category: 0.20 | |

Haiku's three errors: Stoke the Flames at an opponent under Leyline of Sanctity called legal, and two legal casts
called illegal (Glacial Ray at the opponent; Fury Sliver, which Hostage Taker lets the solver cast with any mana).
One item of the 96 is a known wrong label (Craterhoof on `hoofdrain_4f7ac995ab62` base, 9.2): Haiku agreed with the
label, so by the rules it is one more Haiku error and its pair is not a contrast pair. Category: 0.49 against 0.20
for always naming the commonest category, on a category the audit found unreliable (above).

**What it shows:** the set separates a model from the floor (balanced 0.968 against 0.683; 46 of 52 pairs against 14)
and does not yet separate strong models: with 96 items the interval is about 0.08 wide.

### 9.5 What was not determined

- **The wrong labels' rate over the set.** The audit found two classes; it cannot say how many items each holds
  beyond the bounds above, nor what other classes a larger sample would find. Until the two menu defects are fixed,
  leave out Disturb casts and boards with Heritage Druid, or relabel them by hand (the two `not-offered` reports in
  `issue-drafts/`, 9.3).
- **The categories' reliability** beyond the audit: the ordering fault in `rejection` was fixed by PR #360 after
  this run; the categories at epoch 82 were not re-measured.
  Score categories on timing, land drop and target only, or not at all.
- **The leak** that limits a JVM to about 375 boards was not found.
- **Whether the variants are fair positions.** A board built for its main phase, moved to combat or the opponent's
  turn with the solver given priority (and a board set in a cleanup step), is legal to ask about but not a position a
  game reaches that way.
- **A stronger floor.** `zone_floor` knows no card text; one that read type lines and printed cast ways would move
  most card-text negatives and about 1,800 subtle ones to trivial. The subtle stratum is "what this floor misses".
- **Model separation**, **correlation with play**, **licensing** (section 8) are unchanged: one model, 96 items.
