# Hard puzzles: finding and building positions that take 10 to 15 actions (plan, 2026-10-20)

**Status: proposed, for the owner's review.** Part of [BENCHMARK-V2.md](BENCHMARK-V2.md) (section 5 points here).
The owner's ask: puzzles that need at least 10 to 15 actions, with complex state, that cannot be simplified or
walked through blindly; both found online and built by us. Sources read today are listed at the end.

## 1. The bar: what "hard" means here, and how each part is measured

A puzzle is T3-hard only when every row holds. Each row is a check the gate runs, not a judgement.

| Property | Check | Threshold |
|---|---|---|
| **Long** | `L`: solver actions on the proven line (spells cast, non-mana abilities activated, attacks and blocks declared, each with its targets and modes; a mana ability counts when the engine offered it as a choice, since the order then matters) | `L >= 10`; `D >= 8` decision points with two or more options |
| **Complex state** | objects on the battlefield and in hand at the start (Possibility Storm's median is 16; our rules puzzles' median is 4) | at least 12, with at least 3 opponent permanents |
| **Not walkable blindly** | the zero-model arms all fail: random (50 rollouts), PASS, first-option, Forge AI (4 seeds), and a new **greedy-preview** arm (one-ply `simulate`, take the option that lowers the opponent's life most) | random at most 2%, Forge AI at most 1 of 4, the others 0 |
| **Not simplifiable** | **minimality**: the proven line replayed with any one solver action removed must fail; **exactness**: the line ends with the opponent at 0 or less by at most 3, and no mana floats unused at the end | every action necessary |
| **Has a trap** | at least one line that differs in one early choice and ends with the opponent at 1 to 3 life, found by the deviation search | one or more, recorded as `trap_lines` |
| **Adversarial** | the opponent has at least one real choice (a block, a free activation, an instant stated in hand) and the proof beats every reply | `opponent_choice_points >= 1`, all branches beaten |
| **Proven in the engine** | the line replays to the acceptance state on the pinned engine; opponent branches proven from that line; a second solution searched for and not found within a stated budget | `proven`, `unique-within-budget N` |
| **Told nothing** | the seat's brief is the goal, the turn limit and a neutral description; no name, label, hint or solution text | the neutral brief of V2 section 3 |

Why these rows: Lichess keeps only "only-move" lines, checked recursively with the engine (the Puzzler
method); DeepMind's puzzle generator rewards uniqueness (the gap between the best and second-best line) and
counter-intuitiveness, measured as the search depth at which the engine first finds the solution; the
greedy-preview arm is our analogue of "found at depth 1". Possibility Storm's rules make every solution beat
"all possible outcomes, such as your opponent's actions and blocking decisions"; its hardest puzzle had fewer
than five correct answers because of "opponent's choice". Minimality is what stops a 12-action puzzle from
being a 3-action puzzle with nine free plays attached.

## 2. Finding them online

Everything public with a published solution is contaminated by assumption (HF guidebook) and goes to the
public pool with perturbed copies (V1); it still supplies the T3 shape we lack, and transcribing it is far
cheaper than inventing it.

| Source | Size | Board as | Solutions | In Forge already | Worth |
|---|---|---|---|---|---|
| **Possibility Storm** (possibilitystorm.com, 2017 to now) | 310 numbered; 74 Mythic, 108 Rare of Forge's 282 | image only | Patreon, $1 a month for the whole archive | 282 files (276 scoreable candidates) | the main T3 source: the ones random and the Forge AI cannot solve are already on disk as 255 unproven candidates (the 21 active today are the easy ones, 1 to 8 actions) |
| **Perplexing Chimera** (Sean Uy, GatheringMagic then CoolStuffInc, 2015 to 2020) | 300, the archive still online | **text** (zones listed, life, hand) | in the next article; the last one has 11 steps | 49 files (48 candidates) | about 250 to transcribe from text; many are two-turn (`Turns:2`, survive then win) |
| **Pauper Puzzles** (pauperpuzzles.wordpress.com, 2017) | about 30 | image | on the blog | 29 | done |
| **MTG Puzzles** (mtgpuzzles.com, 2023) | 10 | image, with a text checklist of starting conditions | not shown | 8 | the last 2 |
| **Mind Twists** (StarCityGames, Dayv Doberne) | at least 22 (11 columns, two puzzles each) | text in the article | the next column | 0 | transcribe; older cards |
| **Puzzling Magic** (GatheringMagic) and **Inquest "Dead Man's Hand"** | unknown; 3 scans | text / image | with the puzzle | 1 + 3 | small |
| **Magic: The Puzzling** (Rosewater 1996, 25 puzzles plus Garfield's) and the Duelist columns | about 25 + | print | in the book | 0 | old cards Forge has; needs the book |
| **Duels of the Planeswalkers 2012 to 2015 challenges** | 10 to 20 a game | in-game; solutions written up by players | yes | 0 | reconstruct from solution write-ups; low priority |
| Forum and Reddit puzzles | many | mixed | mixed | 0 | skip: no verification, no unique solutions |

**Transcription pipeline** (the engine is the checker, never the model): the solution is written as the
`[solution]` script first (section 4, item 1), the board second; a model may draft the `.pzl` from the image or
text, a person checks card names, counters and tapped state, and the script arm must replay to the acceptance
state on the first try or the transcription is wrong (or Forge has a rules gap, which is noted). Budget about
15 minutes a puzzle once the script arm exists. Possibility Storm's own rules are the defaults (first main
phase, opponent with no hand and no lands unless stated, libraries irrelevant, all blocks beaten).

**Owner decisions here:** (a) the $1 Possibility Storm supporter tier for the solution archive, internal use
only (V1 decision 5 stands: nothing published); (b) Perplexing Chimera used the same way (fan content, no
licence stated); (c) model-drafted transcriptions allowed, with the engine replay and a human name check as
the acceptance.

## 3. Building our own

Each family names the mechanism that forces the length and the rule that stops the puzzle collapsing into a
short one. Every family is built in two modes: **authored** (a person writes the board and the script; about
70% of our authored drafts have passed the gate) and **generated** (the template's numbers drawn at random,
the script computed, the gate run; an unlimited uncontaminated supply, HF's "rule-based generation", capped
at a share of the suite so no one trick dominates).

| Family | Mechanism for length | What stops simplification | Decks and cards we already have |
|---|---|---|---|
| **Ritual storm** | each ritual, cantrip and tutor is an action; the kill needs a storm count or exact mana | exact mana with Lion's Eye Diamond and Infernal Tutor timing (hellbent), Past in Flames for a second pass; the library's top cards stated as known (Brainstorm seen them) or the line kept off draws | `eval/decks/ant-storm`, `doomsday` |
| **Elf mana engine** | Heritage Druid taps three elves, Nettle Sentinel untaps on each green spell, Glimpse of Nature draws per creature: 15 or more actions with a known library top | the order of taps and casts is the puzzle; one wrong tap strands a Sentinel | `eval/decks/elves` |
| **Untap and ping interleave** | a source that untaps on each spell (Thermo-Alchemist, Electrostatic Field) pinging between spells | the count is exact only when the ping is taken before every spell; `h_0` is the 7-action seed of this family | `h_0`, `h_1` drafts |
| **Sacrifice and drain loops** | Ashnod's Altar, Blood Artist, Zulaport Cutthroat, recursion (Reassembling Skeleton), Treasures: finite resources converted in the right order | exact life drain; a wrong order leaves one mana short | new |
| **Combat with blockers and pumps** (the Possibility Storm shape) | pre-combat setup, then a trick per blocker after blocks | the opponent has 3 to 5 blockers and a free ability; lethal must hold against every block assignment, so the solve is a strategy, not a line | `PS_*` boards as models |
| **Two-turn survive then win** (the Perplexing Chimera shape) | survive at exactly N, keep the resources the next turn needs | spending a card to survive comfortably loses the kill | `PC_*` boards as models |
| **Rule stacking** | three or four of the rules puzzles' tricks composed on one board, each needed in order | the line needs every rule; the `RP_*` boards are the parts | the 526 rules puzzles |
| **Adversarial hand** | the opponent is stated to hold one instant (a counter, a removal spell) with the mana for it | the proof must beat the reply at every window: play around it or bait it | any family above |

Rules that apply to every board: no slack (exact mana, exact damage, no spare pump); every card in hand either
used on the line or a red herring that a trap line uses; the library filled with Colossal Dreadmaw (or stated
known cards) so draws decide nothing; the opponent with no mana abilities on its blockers (a pointless tap
opened a false second solution in RP_192); combat puzzles start in MAIN1, others in MAIN2 so an optional attack
is not a second solution; the acceptance state names the win (`life p1 <= 0`), not the path.

**Mined positions** (V1 phase 3, `eval/mine_puzzles.py`'s `lethal-now` category): the start of the kill turn in
free Forge-AI-versus-AI games on the combo decks above, with the library order fixed by the seed, as a
third supply once the script and prover work; and every position where the model failed a T2 or T3 attempt.

## 4. What the gate needs first (overlay only, tests first, no paid runs)

The prover today is UCT over uniform random completions: it found 4 of 25 Forge puzzles at budget 200, and not
the 7-action `h_0` at 600. A 12-action line with three options a step is one line in five hundred thousand;
no rollout budget finds it. The gate for long puzzles therefore starts from a written line and uses search
only for what search is good at: opponent replies, deviations and minimality.

1. **The script arm** (`CONTROLLER_A=script` reading a `[solution]` section): each line names an action as the
   seat's menu would label it ("cast Dark Ritual", "activate Heritage Druid: tap Elf A, Elf B, Elf C", "attack
   with X, Y", "target opponent"); the arm takes the option whose label matches, and a line with no match is a
   drift, never a guess. The existing `script` arm replays random draw tapes; this one replays actions.
   Tests: a scripted line reaches the acceptance state; a misspelt or reordered script drifts; a script that
   skips a needed action fails. This alone proves solvability of any line a person can write.
   **Done (2026-10-20):** a model spec `script:<file>` (`ScriptBridge.java`) instead of a new controller, so the line
   plays the faithful seat's own menus; `puzzle_baselines.py line`; acceptance stage `lines`. h_1 is solved by its
   16-line written solution against Forge's AI (`eval/puzzles/hard/h_1.pzl`; the `lines` stage, recorded in
   ACCEPTANCE.md). h_0's line needs Gitaxian Probe paid with life, which the menu did not offer (Forge's AI-only
   `AIPhyrexianPayment$ Never` hint decided castability): a harness defect, fixed in harness-epoch-68. h_0 is
   solved by its 11-line written solution against Forge's AI (`eval/puzzles/hard/h_0.pzl`; the `lines` stage).
2. **The prover from a line**: `PuzzleSearch.prove` already enumerates every opponent alternative along a
   winning line; run it from the script's line, so a long puzzle's adversarial proof costs only the opponent
   branches. The deviation search (`unique`) runs at each solver step with a stated budget and reports
   `unique-within-budget N`, never "unique".
   **Done for the proof (2026-10-20):** `puzzle_baselines.py line --opponent search` proves a fixed line against
   every reply. Not done: a line that must answer a reply differently (one line per branch), and the deviation
   search from a written line.
3. **Minimality**: replay the script with each solver action removed in turn (the rest attempted as written);
   every removal must fail. Written to the record as `necessary_actions`.
   **Done (2026-10-20):** `puzzle_baselines.py line --minimality` (`minimality.json`). A removal is replayed relaxed
   (a decision the line does not fit takes the do-nothing answer); only a game played out and lost shows the action
   is needed; a drift, a capped game, or a line that stalled with lines unplayed is inconclusive (eval/PUZZLES.md).
4. **The greedy-preview arm**: a zero-model policy over `PlayerLookahead`'s one-ply preview, taking the option
   whose preview lowers the opponent's life most (then the one that adds most power). The "depth 1" floor; it
   must fail on T3 and is reported with the other floors.
   **Done (2026-10-20):** the `greedy` baseline arm (`GreedyBridge`, spec `greedy:`, LOOKAHEAD=1). Measured with
   `puzzle_baselines.py run --suite eval/suites/greedy-check-v0.json --arms greedy,pass,random --rollouts 20`
   (greedy and pass on seeds 1-4, random on 20; runs
   `/tmp/puzzle-scratch/greedy-run2` and, for greedy at 30963f4, `greedy-run3`, not kept): greedy 0/4 on
   h_0, h_1 and RP_243 and 4/4 on RP_141 and RP_035; pass 0/4 on all five; random 1/20 on h_0, RP_035 and RP_243,
   5/20 on RP_141 (so RP_141 is too easy for a hard tier) and 0/20 on h_1.
5. **The arms see the faithful seat's menu**: floating mana and sacrifice-cost mana abilities are offered only
   to the faithful seat today (rules pilot, harness bug 5), so a storm or Altar line cannot be scripted or
   proven. The arms and the seat must share one menu.
6. **The record**: `L`, `D`, `board_objects`, `opponent_choice_points`, `necessary_actions`, `trap_lines` and
   the proof budget in `puzzle.v1` (additive), and the T3 rule of V2 section 4 reads them.
7. **Prover budget**: iterative deepening on line length and a wall-clock budget recorded with the rollout
   budget, for the puzzles that have no written line (the unproven Forge candidates).

## 5. Phases and targets

| Phase | Work | Yield aimed at | Cost |
|---|---|---|---|
| A | Items 1 to 6 above; the `h_0` and `h_1` drafts proven through the script arm as the first two T3 puzzles | the gate | $0, about a week |
| B | Possibility Storm: the 255 unproven candidates, each given its published solution as a script (decision a), gated | 100 T3 puzzles, public pool | $1 a month; transcription time |
| C | Perplexing Chimera pilot: 20 transcribed from text, yield and minutes measured; then 100 | 60 T3, many two-turn | time |
| D | Our own: 2 authored boards per family (16), then the generators at 10 instances per template (80) | 50 or more uncontaminated T3 for the held-out split | $0 |
| E | Mined combo-deck positions: 20 as a pilot | 10 | $0 |
| F | Luna pilot: 30 T3 (10 from each of B, C, D), k=5, C0; every failure read | the tier separates (expected under 40%); the trap lines show what it falls for | about $0.30 |

Suite targets for v2: at least 150 proven T3 puzzles, at least 50 of them ours, 30% held out and private
(V2 decision D). Until phase A lands, nothing here is proven and nothing is scored.

## Sources

- Possibility Storm: https://www.possibilitystorm.com/ (310 puzzles, images, supporter tiers);
  https://www.possibilitystorm.com/rules-and-assumptions/ (the rules quoted above);
  https://beta.mtggoldfish.com/articles/possibility-storm-rivals-of-ixalan-7 and
  https://beta.mtggoldfish.com/articles/possibility-storm-2017-a-year-of-puzzling-in-review (hardest puzzle,
  human solve times).
- Perplexing Chimera: https://www.coolstuffinc.com/a/seanuy-12242020-what-the-cat-dragged-in-one-final-magic-the-gathering-puzzle
  (the 300th puzzle, text board, 11-step solution, archive notes).
- MTG Puzzles: https://mtgpuzzles.com/ and https://mtgpuzzles.com/about (10 puzzles, fan-content terms).
- Mind Twists: https://articles.starcitygames.com/articles/mind-twists-1/ to .../mind-twists-11/.
- Magic: The Puzzling: https://mtg.wiki/page/Magic:_The_Puzzling.
- Forge's puzzle folder (`forge-gui/res/puzzle`): PS 282, PC 49, PP 29, MTGP 8, INQ 3, PM 1, tutorials 3;
  `Difficulty:` Mythic 74, Rare 108, Uncommon 91, Common 4, Special 5.
- Lichess puzzle criteria (community, not official): https://lichess.org/forum/general-chess-discussion/puzzles-with-more-than-one-good-solution;
  the Puzzler project https://github.com/ornicar/lichess-puzzler.
- Generating Creative Chess Puzzles (DeepMind, NeurIPS 2025): https://arxiv.org/abs/2510.23881 (uniqueness as the
  best-minus-second-best gap with a 0.5 threshold, recursive on the solver's moves; counter-intuitiveness as the
  engine's critical depth).
- Sokoban hardness by generated states: https://sol.sbc.org.br/index.php/sbgames/article/download/45410/45171/ [secondary].
- Our own numbers: `eval/suites/puzzles-forge-builtin-v1.json` (26 active, 1 to 8 actions),
  `runs/puzzles/prove-all-e64-b200.json` (4 proven of 25), `eval/puzzles/rules-pilot/manifest.jsonl` (526 converted,
  453 at two actions or fewer), `eval/puzzles/rules-pilot/REPORT.md` (harness bug 5, RP_192, RP_204).
