# Misreading probes, second random sample of 20 rules-pilot puzzles

The first random sample (`../2026-11-09-misreading-probes-sample/`) measured how many misreadings a probe can express
and how many of those the puzzle rejects. This folder repeats it on a second, disjoint sample, classifying all 20
before any JVM ran. Zero-model, no network, no `OPENROUTER_ALLOW_EGRESS`.

**Result in one paragraph.** Expressible: **13 of 20 (65%, Wilson 95% interval 43 to 82%)**; not expressible 3; unclear 4
(counted as not expressible; if all four were expressible the rate is 17 of 20). Of the 13 probed, **all 13 reject every
whole misreading** against Forge's AI (13 of 13, 100%, interval 77 to 100%). One of the 13, RP_066, is rejected by
arithmetic of its acceptance (any X below 5 gives fewer than five Mites), not by its rule; without it, 12 of 12 (100%,
76 to 100%). No puzzle in this sample accepts a misreading. RP_183 first looked like one, but its "accepted" line was
the proof's order, not the misreading (see "Caution"). The proof has no `Opponent:` step, so there was no
search-opponent arm to run.

## Sample

* Population: the 221 `result: converted` rows with a `misread_line` in `eval/puzzles/rules-pilot/manifest.jsonl`,
  minus RP_001, the pilot's ten (RP_004, 006, 022, 026, 031, 036, 048, 052, 068, 069) and the first sample's twenty
  (RP_005, 017, 034, 047, 055, 060, 061, 067, 071, 092, 096, 100, 117, 132, 145, 152, 153, 188, 196, 235) = **190** ids.
* Draw: `random.Random(20261110).sample(sorted(ids), 20)` (ids sorted as strings), in draw order:
  `RP_109 RP_214 RP_066 RP_192 RP_232 RP_234 RP_165 RP_236 RP_218 RP_133 RP_114 RP_072 RP_191 RP_118 RP_084 RP_183
  RP_186 RP_142 RP_053 RP_126`.
* Build: overlay sha `edaca55afdf6`, engine `34a8662b7966`, built once from commit `344f1dc38e72` (`origin/main`) with
  `STAGES="preflight classpath compile" FORGE_HOME=/root/forge OUT=<build> bash eval/harness_acceptance.sh` (PASS
  preflight, classpath, compile). Manifest and `.pzl` files were not edited.

## Classification (written before any JVM ran)

E = expressible (a different order or choice of the solver's own plays), N = not expressible (a belief with no
different play, or PASS only), U = unclear (the manifest does not say what the misreader does differently). Reason
from the manifest `misread_line`, `rule`, `note` and the `.pzl` state.

| Puzzle | Class | Reason |
|---|---|---|
| RP_109 | E | Attack Liliana, then Hero's Downfall her, instead of Downfall first |
| RP_214 | E | Mutate Gemrazer under the face-down creature |
| RP_066 | E | White Sun's Twilight with X below 5 (or not cast) |
| RP_192 | E | Return Grizzly Bears instead of Splinterfright |
| RP_232 | U | "expects trigger": what is played differently is not stated |
| RP_234 | U | "expects 2 life gained": the cast is not stated to differ |
| RP_165 | N | "never attempts it": PASS only |
| RP_236 | U | "expects both": which Wurm Monstrous Rage targets is not stated |
| RP_218 | E | Cast only one of Shivan Meteor (suspend) and Lightning Bolt |
| RP_133 | E | Come Back Wrong on the opponent's Flametongue Kavu |
| RP_114 | E | Virulent Wound on Grizzly Bears |
| RP_072 | U | Which card Body Double copies instead is not stated (the note says one line's pick was forced) |
| RP_191 | E | Pay mana for the Flame Rift cast through Oskar instead of discarding |
| RP_118 | N | Belief that the curse stays; the same plays, or no Ring (PASS only) |
| RP_084 | E | Sauron returns something other than Kozilek |
| RP_183 | E | Blink Kess with Cloudshift first, or stop after the first Bolt |
| RP_186 | E | Counterspell on Abrupt Decay |
| RP_142 | E | Put Lord of the Pit in with Kaalia instead of Master of Cruelties |
| RP_053 | N | "sees no line to exactly two": a belief, no different play |
| RP_126 | E | Rise from the Grave on the Ox instead of escaping it |

Counts: **E 13, N 3, U 4.** Wilson 95% for E/20: 43.3 to 81.9%; if U were E, 17/20 (62.1 to 96.8%).
The four U are the ones whose `misread_line` is a belief ("expects ...") with no stated play; the classification is
one author's reading of the manifest, and a different reader could move RP_232, RP_234, RP_236 or RP_072. None of the
seven non-E puzzles was probed.

## Commands

```bash
# proof (control source): the search arm at the gate's budget, one JVM, as in the first sample
#   puzzle_baselines.run_arm(build, pzl, "search", "1", dir, search_env(600))   -> row.line
# every probe, one JVM each (a wrapper called probe_env and run_arm and appended the compact row)
FORGE_HOME=/root/forge python3 eval/puzzles/puzzle_baselines.py --build <build> probe \
  --pzl eval/puzzles/rules-pilot/<RP_nnn>.pzl --line "<line>" [--opponent search] [--completion-seed N] [--seed N]
# menus
FORGE_HOME=/root/forge python3 eval/puzzles/probe_walk.py --build <build> --pzl <pzl> --prefix "<steps>" --options
```

Every JVM ran under the shared machine lock. A control whose proof has an `Opponent:` step (RP_192, RP_114) ran with
`--opponent search`; every other control and every misreading used the default `ai` opponent. `probes.jsonl` has 38
rows in the first sample's shape (`puzzle`, `role`, `line`, `cli_last_line`, `build_overlay_sha`, `pzl_sha256`,
`completion_seed`, `opponent`, `row`, `router`, `game_seed`, `misreading_index`) plus `label`. Roles: `control` (13),
`misreading` (18), `misreading_other_game_seed` (3, RP_183 line (d), game seeds 2 to 4),
`variant_not_the_misreading` (4, RP_183 line (c), game seeds 1 to 4). The other probes run while finding menus (sentinels,
prefixes, `--options` walks, a few scratch lines) are not kept.

## Probe table

Control = the proof line probed with a seeded random completion: `accepted` for all 13 (the search arm proved each,
`proven`, budget 600). A misreading below is the WHOLE line: each was re-run until the game consumed it whole (`drift`
empty; no `line not consumed` row is cited). One seeded completion (seed 0), game seed 1, unless stated. Menu values
were read with `probe_walk`; a payment is one binary draw per land.

| Puzzle | Misreading line (as played) | Verdict | Judgement |
|---|---|---|---|
| RP_109 | `Solver:3:2 Solver:3:2 Solver:2:1 Solver:3:1 Solver:3:0 Solver:3:1` (pass, pass, attack Liliana with the Giant, Downfall Liliana) | rejected: opponent at 20 not 17 | good: `assigned Hill Giant to attack Liliana`, Downfall resolves, no damage reaches the player. Caution: the same ordering but attacking the Opponent (`...Solver:2:1 Solver:3:0` as the defender, a shorter line whose random finish casts the Downfall) is `accepted`: it plays the proof's plays in another order, not the misreading |
| RP_214 | `Solver:4:0 Solver:2:1` (Gemrazer under the face-down creature) | rejected: Ornithopter not in graveyard | good: value 1 is the face-down creature as the top card per the menu text |
| RP_066 | (a) `Solver:3:1 Solver:3:0 Solver:6:4` (X=4); (b) `Solver:3:2` (not cast); (c) `Solver:3:1 Solver:3:0 Solver:6:0` (X=0) | rejected (all): Mite tokens not 5 | **acceptance arithmetic**: only X=5 can give five tokens whatever the rule, so any lower X or no cast fails the count |
| RP_192 | `Solver:2:0 Solver:3:0 Solver:2:1` (return Bears, attack) | rejected: opponent at 3 | good: `damage-assign: all 4 -> Savannah Lions (forced)`, the Bears token has no trample |
| RP_218 | (a) `Solver:4:0 Solver:3:2 Solver:3:2` (suspend Meteor, then pass); (b) `Solver:4:1 Solver:2:1` (Bolt, nothing else) | rejected (both): opponent at 17 not 20 / Meteor not suspended | good for both. A line `Solver:4:0 Solver:3:2` that stops one pass short is `accepted`, because the random finish casts the Bolt later: that is why (a) has the second pass. Observation: after the Bolt no suspend is offered (the game asks nothing more), see "What was not determined" |
| RP_133 | `Solver:3:1 Solver:3:0 Solver:2:1` (Come Back Wrong on the opponent's Kavu) | rejected: White Knight not in exile | good: the log shows the cast on Flametongue Kavu @14 |
| RP_114 | `Solver:2:0 Solver:2:0` (Wound on Grizzly Bears) | rejected: winner not p0, Scryer not in graveyard | good |
| RP_191 | `Solver:8:1 Solver:2:0 Solver:2:0 Solver:2:0` (discard cast, then pay mana for the Oskar cast) | rejected: opponent at 4 | good: two Rifts, 8 damage, as the manifest says |
| RP_084 | `Solver:5:1 Solver:5:1 Solver:4:0 Solver:5:0 Solver:4:0 Solver:3:0 Solver:2:0 Solver:2:0` (return Grizzly Bears) | rejected: Kozilek not on battlefield | good, with a caveat: the pick menu holds only Bears and Kozilek, since Darksteel Colossus never reaches the graveyard, so "picks Colossus" has no play; Bears stands for it. The same pick menu holds under another milled order (checked once, not kept) |
| RP_183 | (a) `Solver:8:0 Solver:2:0 Solver:2:0 Solver:2:0 Solver:6:0 Solver:3:1 Solver:4:3 Solver:4:3` (blink first, one Bolt, pass); (b) `Solver:8:5 Solver:8:3 Solver:3:1 Solver:4:3 Solver:4:3 Solver:4:3` (one Bolt, never blink, pass); (c) `Solver:8:0 Solver:2:0 Solver:2:0 Solver:2:0 Solver:6:0 Solver:3:1 Solver:4:0 Solver:3:1`; (d) `Solver:8:0 Solver:2:0 Solver:2:0 Solver:2:0 Solver:6:5 Solver:6:0 Solver:3:1` (Cloudshift resolves first, then one Bolt: the true misreading) | (a), (b) rejected: opponent at 3; (c) `accepted` on game seeds 1 to 4, but it is the proof's order (see "Caution"; kept as `variant_not_the_misreading`); **(d) rejected on game seeds 1, 2, 3, 4** (completion seed 0): `life p1 <= 0 (was 3)` | good for (a), (b), (d). (c): after Cloudshift is cast the caster has priority with it still on the stack, and the 6-option menu at step 5 is that priority; `Solver:6:0` casts the first Bolt in response, so it resolves before Cloudshift (old Kess's use), the blink then renews Kess and the second Bolt uses the new Kess. (d) passes first (`Solver:6:5`), so Cloudshift resolves, then casts one Bolt; the game offers no second Bolt (a sentinel `Solver:99:0` after it is not consumed), so Kess's once-per-turn limit is honoured and the puzzle tests its rule |
| RP_186 | `Solver:5:1 Solver:2:0 x4` (Counterspell on Abrupt Decay, all four lands tapped) | rejected: Bears not on battlefield | good: Counterspell targets the Decay, which resolves |
| RP_142 | `Solver:2:1 Solver:3:0` (Lord of the Pit with Kaalia) | rejected: opponent at 11 | good: 2 + 7 = 9 damage, Master's trigger absent |
| RP_126 | `Solver:5:0 Solver:9:0` (Rise from the Grave on the Ox) | rejected: no +1/+1 counter | good: the game asks nothing after the target |

Proof lines (found by the search arm, budget 600; each probed above as the control):

| Puzzle | Proof line |
|---|---|
| RP_066 | `Solver:3:1 Solver:3:0 Solver:6:5` |
| RP_084 | `Solver:5:1 Solver:5:1 Solver:4:0 Solver:5:0 Solver:4:0 Solver:3:0 Solver:2:0 Solver:2:1` |
| RP_109 | `Solver:3:0 Solver:3:1 Solver:2:1 Solver:2:0` |
| RP_114 | `Solver:2:0 Solver:2:1 Opponent:2:0` |
| RP_126 | `Solver:5:1 Solver:2:0 Solver:2:0 Solver:2:0 Solver:2:0 Solver:2:1` |
| RP_133 | `Solver:3:1 Solver:3:0 Solver:2:0 Solver:3:1` |
| RP_142 | `Solver:2:1 Solver:3:1` |
| RP_183 | `Solver:8:5 Solver:8:3 Solver:3:1 Solver:4:0 Solver:2:0 Solver:2:1 Solver:4:1 Solver:3:1` |
| RP_186 | `Solver:5:0 Solver:2:0 Solver:2:1` |
| RP_191 | `Solver:8:1 Solver:2:0 Solver:2:0 Solver:2:1 Solver:2:0` |
| RP_192 | `Solver:2:0 Solver:3:1 Solver:2:1 Opponent:2:0` |
| RP_214 | `Solver:4:0 Solver:2:0` |
| RP_218 | `Solver:4:0 Solver:3:0 Solver:2:1` |

## Rates (each recomputable from the table and `probes.jsonl`)

* Expressible: 13/20 = 65.0% (Wilson 95% 43.3 to 81.9%).
* Reject (puzzle level, every whole misreading rejected against the `ai` opponent): 13/13 = 100% (77.2 to 100%).
  Without RP_066 (rejected by the count): 12/12 (75.8 to 100%).
* Accept: 0/13 = 0% (0 to 22.8%). All 18 distinct whole misreading lines probed against `ai` were rejected (21 rows
  with the other game seeds). Lines within a puzzle are not independent.
* Pooled over both samples: the first sample was a simple random draw of 20 from the 210 ids that remain after RP_001 and
  the pilot's ten, and this one a simple random draw of 20 from the 190 that remain after the first, so the two are
  disjoint draws from the same 210-id population and the 40 are one simple random sample of it (the 11 chosen
  or special ids are outside that population). Expressible 27/40 = 67.5% (52.0 to 79.9%; 5 of the 40 were unclear and
  counted as not expressible). Probed puzzles that reject every misreading: 26/27 = 96.3% (81.7 to 99.3%);
  without the two acceptance-wording ones (RP_096, RP_066) 24/25 = 96.0% (80.5 to 99.3%). Puzzles that accept a
  misreading against Forge's AI: 1/27 = 3.7% (0.7 to 18.3%), RP_034 only. Wilson treats each puzzle as
  independent; the sampling is without replacement, so these are slightly wide.

## Caution: RP_183's first "accepted" line was the proof's order

The first draft of this folder reported that RP_183 accepts "blink Kess first, then two Bolts" and suspected a Forge
deviation from Kess's once-per-turn limit. That was wrong. Line (c) reads as blink then two Bolts, but after Cloudshift
is cast the caster keeps priority with Cloudshift still on the stack. The 6-option menu at step 5 is that priority
(0 to 3 Bolt casts, 4 Mountain, 5 PASS), so `Solver:6:0` casts the first Bolt in response: it resolves BEFORE
Cloudshift, using the old Kess's one cast; Cloudshift then blinks Kess and the second Bolt uses the new Kess. That is the
proof's order, a variant, not the misreading.

How it was caught: `probe_walk.py --prefix "Solver:8:0 Solver:2:0 Solver:2:0 Solver:2:0" --options` and the same prefix
with `Solver:6:5` (PASS) appended show the same 6-option menu, so passing resolved Cloudshift and gave a fresh main-phase
menu. The true misreading, Cloudshift resolved first and then Bolt, is line (d), with the PASS: `rejected` on game seeds
1 to 4. After the blink resolves and one Bolt is cast the game offers no second Bolt, so Forge honours the limit: there
is no engine deviation, and RP_183 does test its rule. Line (c)'s four rows are kept, relabelled
`variant_not_the_misreading`.

Method: a probe line that casts a spell and then another must pass first if the misreading needs the first spell
resolved, since the caster keeps priority (as RP_152 in the first sample, `variant_not_the_misreading`).

## What was not determined

* Each rejected verdict is the line plus ONE seeded random completion (seed 0), as in the earlier folders. Most lines
  end where the game asks nothing more, but this does not prove no completion is accepted. The search-arm follow-up
  (prefix fixed, ask whether ANY completion is accepted) is still open.
* RP_218: after the Bolt no suspend is offered (the game asks nothing more after `Solver:4:1 Solver:2:1`), though the
  rule makes suspending a special action allowed after the Bolt. Not investigated; the puzzle's proof order (suspend,
  then Bolt) is unaffected.
* RP_084's "picks Darksteel Colossus" has no play in the menu (Colossus is not in the graveyard to pick); Bears
  stands for it. RP_109's misreading (attack Liliana, then Downfall her) is rejected, but the same ordering aimed at
  the player is accepted: a second solution of the same plays, found by the menu walk, not a misreading.
* RP_192 and RP_114 have `Opponent:` steps in the proof; their misreadings were rejected under `ai`, so the
  search-opponent arm the task asks only for accepted misreadings was not run for them.
* The seven non-E puzzles (RP_232, 234, 236, 072 unclear; RP_165, 118, 053 not expressible) were not probed.
* The classification is one author's reading of the manifest text.

## Input sha256 of each `eval/puzzles/rules-pilot/<id>.pzl`

* RP_109 `ab22f19a0430b86ce5e2f4a9dbe4bc39c00c18b209a76c735e8d1632fa07f71d`
* RP_214 `afe3861891c6764d1ac97bf34338677f63701ea8b5845eca1cf6f87de1d64d68`
* RP_066 `9aca72e6f1638a2698e4711037e348d22a61a0d44fc3617947311d285aa3e156`
* RP_192 `2d09e2f673b93b4e596ad994f59ba382ff854710e4ce592fb84847ba7184ceda`
* RP_232 `a79e0986af56873ad77f61715615851010f7bb14b38c552cb3f13807d91bd669`
* RP_234 `b8d942d60aab2e8ddbb466d47985edea8b5cbba091a628355bd3f0a019b632bb`
* RP_165 `3618d1c91c5699d0fffdae0ad931c572904fe340b7b4b9e8e7e715856aa9d4a8`
* RP_236 `c1ebd92f1214118b34ce742c0d146bae6a4223148b5f9a80e9187603dcdac35e`
* RP_218 `e86ba6c5522b80076564370ad465c4d326edc55bcee7734d39de56001043e8e5`
* RP_133 `5c17d1453b0fd40874993e7836c9bc24b2b6813dc7ef17225b93dfb6a524c722`
* RP_114 `7f85675f5c0f7b71d7331ce9b40bb3a50ef91e9c1975fc80622b0142583b89bc`
* RP_072 `3d159afd1ba880eadfd2ba27dc0ab405d909a46b1734a2d95933cf5d994e9ce1`
* RP_191 `f9c438a187fe0c0b3a714ea8ca6eb363f606d6b44591448bbc8e073dcc7c6acf`
* RP_118 `b8ae23a2adbc15d83220103209e95a07785705c7cd93aae9781b1f1b1e6e3683`
* RP_084 `5b2c43a87fae2862400318412bdd8e194dc9e53f70356fbbd8b131ed1c92cc8f`
* RP_183 `abf063311d5255283ebfb54f674d2624219fe16f517564407c6f7c89d7aa8c54`
* RP_186 `1e63ac2832f90edb822d8e8dad934f3fd2e84d8af362a9f8f15385514ac5132d`
* RP_142 `3366e22efaa80ba7aaa032bca8b07be357c39a962b568e02729736f53bed6e74`
* RP_053 `f55a9f301a9a18724b3f6dfc50a761cc8e5d61b80c171e59be788d4e66278d34`
* RP_126 `3e30eed2f6f715ee13e19d2af0f3305d0b3e7e99b24b13e552ba732ac5e8a170`

## Effort (the author's tally, not recomputable from this folder)

Roughly 13 proofs, 13 controls, 38 kept rows in all, and about 190 more JVM runs in `probe_walk` (sentinels,
`--options` walks) and scratch lines; about 12 to 25 per puzzle, except **RP_183, about 75 JVM runs, over the stop of
40 per puzzle** (plus about 10 more JVM runs afterwards to re-check line (c), the menus and line (d) on four game seeds): the Kess menu has an eight-value main menu, a three-draw mana payment and two near-identical target
menus, and I kept walking after the lines were already found, to learn why the second Bolt was offered. The other
twelve stayed under 40 (largest RP_109 about 32 and RP_126 about 25).
