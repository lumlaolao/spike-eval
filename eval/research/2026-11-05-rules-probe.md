# A static, engine-labelled rules probe: is it worth building? (2026-11-05)

Question: the propose arm made Forge a type checker for plays (ARCHITECTURE.md, "The propose condition"). Does that
give a cheap, static, auto-labelled rules-knowledge benchmark, items of the form (board state, proposed play) ->
legal? and if not, which rule, that needs no game? This note covers prior work, what the repository's recorded rows
already hold, a prototype (`eval/rules_probe.py`), a small development-lane pilot on Claude Haiku 5.5, and the design
of the engine labeller the prototype does not have.

**Answer: build it, but as a diagnostic beside PUZ and the propose arm, never as a headline score, and only with the
engine labeller (section 6).** The legal/illegal label is exact and free. The negatives mined from recorded rows are
mostly trivial: a state-reading heuristic with three rules scores 0.80 balanced accuracy on them. Of the 57,193 illegal
positions, 3,025 (5.3%) are subtle, and the categories of mined negatives can only be inferred. Haiku 5.5 scored
0.958 on a 48-item pilot against a floor of 0.68, 10 of 10 on the subtle items: the mined items separate a model
from the floor, not strong models from each other. The value is in negatives built on purpose and labelled by the
engine with its category, sorted by a difficulty that a floor checks, and the label is only as exact as the miner
that reads the menu (the pilot found 5 wrong labels in 60, now fixed). Section 8 says what was not determined.

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
  and is not covered; the label is Forge's, so a Forge rules bug becomes a wrong label (the 2026-11-03 note, Jericho's
  validity oracle has false negatives); the category is "the first check the play fails" in Forge's order
  (`LlmSpikeGui.rejection`), which is one valid answer where the rules may admit several.

## 2. The data the repository already holds

Mined with `python3 eval/rules_probe.py mine <run dirs> --out <items>.jsonl` (FORGE_HOME=/root/forge for card types)
over the main checkout's `runs/propose`, `runs/demo` and `runs/puzzles` (read only), then
`python3 eval/rules_probe.py stats <items>.jsonl`. 1,606 game files, 453 distinct puzzles. Every number below is from
the final miner (the one committed); section 4 says how the first version's label errors were found.

| | count | how |
|---|---|---|
| items (state, play, label) | 197,253 | every non-pass menu play (legal), every hand card the menu could not cast or play (illegal), every board permanent a target ask left out (illegal), every refused proposal; exact duplicates removed |
| distinct prompt states | 33,875 | sha256 of the state text |
| distinct positions | 143,391 | `position_key`: the state without handles, plan, history and log; two attempts at one puzzle collapse |
| legal / illegal | 110,472 / 86,781 | 20,787 of the legal are mana abilities (`[{T}: Add ...]`), left out by `balance` |
| play / target items | 161,194 / 36,059 | |
| illegal positions | 57,193 | over 500 card names |
| engine-verdict negatives | 10 | the only proposals the propose arm refused (section 2.2) |

By source: puzzles 170,854 items, propose runs 25,131 (the keyword-gauntlet audit games and the Haiku pilots), demo
1,268. Epochs: harness-epoch-70 139,131, 72 26,505, 74 22,246, 75 2,885, 71 708, unknown 5,778 (demo games without a
game line). The rows do not carry the Forge commit (`engine_sha` is "unknown" on the game lines read), so every item
carries the repository's `FORGE_COMMIT` at mining with `engine_pin_source: "repo-FORGE_COMMIT-at-mining"`.

### 2.1 Rule-category balance and difficulty of the negatives

Counted over distinct positions (`balance --per-cell 1000000`, which keeps one item per position; the first item of
each position is counted):

| kind / category (source) / difficulty | positions |
|---|---|
| play / timing (inferred) / trivial | 29,284 |
| target / target (engine) / trivial | 13,877 |
| play / cost (inferred) / trivial | 9,768 |
| play / cost (inferred) / subtle | 2,227 |
| play / unknown (inferred) / trivial or none | 722 |
| play / restriction-or-target (inferred) / subtle | 524 |
| play / land drop (inferred) / trivial | 485 |
| play / timing-or-restriction, timing-or-cost (inferred) / subtle | 204 |
| target / target (engine) / subtle | 70 |
| play / timing-or-cost (inferred) / trivial | 20 |
| play / timing, cost (engine) | 10 |

How a category is inferred (`_infer_cast`, in Forge's order of checks): a card script says the card is neither an
instant nor has flash and the window is not a sorcery window -> timing; else the hand's affordability tag says
"need ..." -> cost; else "payable, not castable now" -> restriction-or-target. A land: outside a sorcery window ->
timing; with the land drop used -> land drop. "Trivial" means one line of the state settles it: mana value above the
mana available, a land after the land drop or outside your main phase, a non-instant outside a sorcery window, a
permanent of a kind (land, creature, other) the target ask offered none of (a creature for "target player").
"Subtle" means it does not: a colour shortfall with enough total mana, a payable card the engine still refuses in a
sorcery window, a creature refused where another creature was a legal target.

- **The balance is poor**: timing and target make 75% of illegal positions (43,161 of 57,193); zone, control and
  type never occur (a menu never lists a card in the wrong zone, so its absence cannot make one); restriction appears
  only inside "-or-" categories.
- **Subtle negatives are 3,885 items, 3,025 positions (5.3% of illegal positions), over 200 card names.** Most come
  from the keyword gauntlet's random-policy games (1,889 of the subtle positions are from `runs/propose`, whose audit
  games draw 552 cards); the generated hard-puzzle families give few (grave 365, landfall 325, copy 237).
- **Target negatives are mostly a type mismatch**: 13,877 of 13,947 target positions are trivial; only 10 of the
  subtle 70 show a keyword (hexproof, shroud, protection, ward) on the board line.

### 2.2 Negatives the models made themselves

`python3 eval/proposal_audit.py runs/propose/<block> --json` on the four propose blocks (haiku-rules-propose,
haiku-rules-propose-v2, e75-haiku-rules-propose, e75-haiku-hard-propose): 468 proposals, 44 format errors, 10
refused. The 10: Lava Spike, Rite of Flame and Gitaxian Probe at the wrong time (timing 5), and cost names a card
does not have (Flashback on Desperate Ritual, Spectacle on Glacial Ray, "alternative" for Mutagenic Growth; cost 5),
all from `runs/propose/e75-haiku-hard-propose`. **Natural negatives are 2.4% of well-formed proposals** (10 of 424):
a set made of the plays models actually get wrong would take thousands of propose decisions per hundred items. They
are the most valuable items (a real model's real mistake, with the engine's category and sentence) and should be
kept whenever a propose run makes them.

### 2.3 The trivial floor

`rules_probe.heuristic` reads the state only: a land needs a sorcery window and an unused land drop; a card whose mana
value exceeds the mana available, or a creature (a P/T in the hand line) outside a sorcery window, is illegal; a land
is not a legal target; everything else is legal. On every distinct position (114,382 items after `balance`, equal
legal and illegal), counted by a scratch script over `rules_probe.heuristic`:

| | items | floor calls illegal |
|---|---|---|
| balanced accuracy | 114,382 | **0.801** (recall legal 0.980, illegal 0.622) |
| play, trivial negatives | 39,906 | 75.3% |
| play, subtle negatives | 2,955 | 2.4% |
| target, trivial negatives | 13,877 | 39.1% |
| target, subtle negatives | 70 | 5.7% |

**A set drawn from the mined distribution has a floor of 0.80**, so a model at 0.85 would know almost nothing beyond
reading mana and phase. The subtle stratum's floor is near 0.5 (it calls 2.4% of them illegal, and 98% of legal items
legal), which is where a score would mean something.

## 3. The prototype: `eval/rules_probe.py`

Stdlib, tested by `eval/tests/test_rules_probe.py` (28 tests, written first from recorded rows; two were checked by
breaking the code: removing the hand-tag stripping and the "(was @N)" handling each turns one test red).

- **Item** (`rules-probe-item-v1`): `state` (the recorded prompt up to its DECISION line, hand legality tags removed),
  `proposal` (`verb` cast / play_land / activate / target, `card` handle, `name`, and for a target its `source` and
  `source_text`), `label`, `label_source` (menu, menu-absence, engine-verdict), `category`, `category_source` (engine
  or inferred), `reason` (the engine's sentence, for engine verdicts), `difficulty`, `id` (sha256 of state and
  proposal), `provenance` (`harness_epoch`, `observation_version`, `overlay_sha`, `engine_pin`, `engine_pin_source`,
  `source_run`, `source_file`, `row`, `puzzle`, `propose_actions`). A written set stores each state once.
- **Commands**: `mine`, `stats`, `balance` (one item per position, at most N per kind/category/difficulty cell, as
  many legal as illegal), `ask` (the Claude CLI development lane: refuses without `CLAUDE_CLI_SEAT=1`, refuses Opus),
  `score` (accuracy with its Wilson interval, balanced accuracy, recall per class, the always-legal and the
  state-reading floors on the same items, category accuracy on engine-categorised items only).
- **Why the state is cut at DECISION and the tags removed**: the menu and the hand's "(castable)" / "(need 1 more R)"
  tags are the engine's answer (`LlmSpikeGui.affordability`, `AskContract.HAND_TAG`); the test
  `test_the_state_given_to_a_model_carries_no_legality_tag_and_no_menu` pins it.
- **The labels' soundness rests on two harness properties**: the play menu lists every legal play (owner rule; "a
  legal action is never hidden"), and the target list lists every legal target. Both hold for what the engine can
  do, but **not for how the menu writes it**: the menu lists one PLAY LAND a land name (`runs/demo/
  luna-elves-goblins-s9191` row 63 offers Mountain @18 with @20, @21 and @25 in hand; spells are listed a copy each,
  counted over 400 game files of `runs/puzzles` and `runs/propose`), so a second copy of a land is not illegal. The
  miner treats it as covered; a test pins it.

## 4. Pilot: Claude Haiku 5.5 (development lane, not a score)

`python3 eval/rules_probe.py balance <items> --per-cell 2 --seed 1105`, on the first miner's output, gave 60 items: 30
illegal (2 per kind/category/difficulty cell, so subtle cells are over-represented against the mined distribution)
and 30 legal, from five epochs (mixed on purpose for a pilot; a score would take one). `CLAUDE_CLI_SEAT=1 python3
eval/rules_probe.py ask <set> --model haiku`, card text from Forge's card scripts prepended for the proposed card, no
rules lookup; notional CLI cost $0.066. Files (container-local, not committed): the scratchpad `set-pc2.jsonl` and
`haiku-pc2.jsonl`.

**The pilot found label errors in the first miner: 5 of the 60 items (8%) were wrong**, all read by hand: a second
Mountain labelled illegal (the one-land-a-name menu above; Haiku had answered "legal", rightly), Memnite as a land play
(a zero-cost spell has no cost in the hand line, like a land), and three items whose card name swallowed the next
hand entry because of an earlier handle "(was @N)" in the hand line. All three faults are fixed and tested
(`test_a_second_land_of_a_name_the_menu_offers_is_not_an_illegal_item`,
`test_a_zero_cost_spell_is_a_cast_not_a_land_when_its_script_is_known`,
`test_an_earlier_handle_in_the_hand_line_does_not_merge_two_cards`); the residual error rate of the final miner was
not measured. Seven legal activations changed only their `name` field (a P/T dropped from it), so their id moved.

Scored on the 48 items the final miner reproduces with the same id and label (`rules_probe.score`):

| | n | Haiku correct | state-reading floor correct |
|---|---|---|---|
| all (balanced accuracy) | 48 | **0.958** (Wilson 0.86-0.99 on accuracy) | 0.68 |
| legal | 23 | 22 | 23 |
| illegal, trivial | 9 | 9 | 8 |
| illegal, subtle | 10 | 10 | 1 |
| illegal, engine verdict or unknown | 6 | 5 | 0 |
| category, on engine-categorised illegal items Haiku called illegal | 8 | 8 | n/a |

Haiku's two errors: it called Craterhoof Behemoth castable where the state's own estimate reads "up to 8 mana" and
the engine refuses the cast (the "(not castable now)" tag, category unknown: the estimate counts mana from creatures
that may not be able to tap), and called a castable Cylian Elf a cost error.

**What it shows**: the items discriminate against the floor (0.958 against 0.68) but not among strong models: Haiku is
near the ceiling, including on the subtle stratum (10 of 10). With 48 items the interval is 0.13 wide, so "near the
ceiling" is all it can say. A probe that separates models needs harder negatives than the mined ones (section 6), and
the Craterhoof item shows a fairness question: an item where the state's printed estimate and the engine disagree
tests the observation as much as the rules.

## 5. What it would add to the benchmark

- **A cheap rules diagnostic beside the propose arm.** The propose arm measures rules errors in play, but models make
  few (2.4% of proposals) and only where they wander; the probe asks the same question on chosen positions, at a cost
  of one short call an item and no game.
- **Not a headline.** The three scores (STR-AI, STR-HELDOUT, PUZ) measure play. A static legality probe measures a
  precondition of play; it belongs in the scorecard's diagnostics with its floor beside it.
- **Contamination:** the items are fresh positions from games, not published questions, and can be redrawn (a
  new seed of the keyword gauntlet is a new set for free). Rules-pilot boards are adapted from RulesGuru questions,
  whose licence forbids AI training; a released set should leave those boards out or ask.

## 6. The engine labeller (designed, not built)

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
   0.958 on mined items says the v1 set must be harder than the mined distribution to be worth a model's call.
4. Do not mine more of the current distribution: 197k items add little over a few thousand, and 94% of their negatives
   are trivial (53,782 of 57,193 illegal positions). If mined items are wanted, the free source with the most card
   variety is the keyword gauntlet with the random policy (`bash eval/keyword_gauntlet.sh`, no model, 552 cards),
   which gave most of the subtle positions here.
5. Audit labels before trusting any set: read a sample of every new negative class by hand, as the pilot did; three
   miner faults were found only that way.

## 8. What was not determined

- **Label noise.** I read 36 sampled negatives by hand while building the miner, and the pilot's 60 items after it;
  the pilot found 5 wrong labels (8%) that the 36 had not, all from how the menu and the hand line are written, not
  from the engine. They are fixed; the final miner's residual rate is not measured. A Forge rules bug would also be a
  wrong label, and no judge reviewed any item.
- **Categories of mined negatives** are inferred (section 2.1); only 10 + 27,780 items carry an engine category
  (the refused proposals and the target asks), and the target ones say only "target".
- **Whether the probe predicts play.** No correlation between probe accuracy and STR or PUZ was measured; with one
  model in the pilot it cannot be.
- **The pilot's size** resolves only large differences (48 scored items: a Wilson interval 0.13 wide at 0.958), the
  items mix five epochs and over-sample subtle cells, so its rates are not the mined distribution's, and one model
  says nothing about whether the probe ranks models.
- **Activated abilities' negatives** are not mined: a menu row does not list the abilities a card has but cannot use
  now (the propose arm's YOUR ACTIVATED ABILITIES does; only its refused proposals were used).
- **Licensing** of boards adapted from RulesGuru for any released set.
