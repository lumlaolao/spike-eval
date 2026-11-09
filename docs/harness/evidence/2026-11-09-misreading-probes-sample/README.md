# Misreading probes, random sample of 20 rules-pilot puzzles

The pilot (`../2026-11-09-misreading-probes-pilot/`) chose its ten puzzles for being expressible as a probe, so the
share of misreadings a probe can express was unmeasured. This folder draws a random sample, classifies every
misreading before running anything, probes every expressible one as the pilot did, and reports both rates.
Zero-model, no network, no `OPENROUTER_ALLOW_EGRESS`.

**Result in one paragraph.** Expressible: **14 of 20 (70%, Wilson 95% interval 48 to 86%)**; not expressible 5; unclear 1
(counted as not expressible; if it were expressible the rate is 15 of 20). Of the 14 probed, **13 reject every whole
misreading** against Forge's AI, the opponent the suite is scored against (13 of 14, 93%, interval 69 to 99%).
**One puzzle accepts its misreading: RP_034** (see "Finding" below): both of its whole misreadings reach the
acceptance state against Forge's AI on 3 completion seeds and 4 game seeds, and are rejected only when the opponent
is made to answer with the Welding Jar.

## Sample

* Population: the 221 `result: converted` rows with a `misread_line` in `eval/puzzles/rules-pilot/manifest.jsonl`,
  minus RP_001 and the pilot's ten (RP_004, 006, 022, 026, 031, 036, 048, 052, 068, 069) = **210** ids.
* Draw: `random.Random(20261109).sample(sorted(ids), 20)` (ids sorted as strings), in draw order:
  `RP_061 RP_017 RP_152 RP_092 RP_188 RP_047 RP_132 RP_117 RP_060 RP_005 RP_145 RP_034 RP_067 RP_153 RP_071 RP_055
  RP_100 RP_196 RP_096 RP_235`.
* Build: overlay sha `edaca55afdf6`, engine `34a8662b7966`, built once with
  `STAGES="preflight classpath compile" FORGE_HOME=/root/forge OUT=<build> bash eval/harness_acceptance.sh`, from
  commit `379263271a70` (`origin/main`). Manifest and `.pzl` files were not edited.

## Classification (written before any JVM ran)

E = expressible (a different order or choice of the solver's own plays), N = not expressible, U = unclear. The
reason comes from the manifest `misread_line`, `rule`, `note` and the `.pzl` state.

| Puzzle | Class | Reason |
|---|---|---|
| RP_061 | N | Misreader still casts Control Magic or passes: the same play or PASS, the difference is only a belief about the search |
| RP_017 | E | Pay Angel's Grace with City of Brass during casting instead of making the mana first |
| RP_152 | E | Animate Faerie Conclave before Dress Down resolves (or skip the order) |
| RP_092 | E | Cast Jump after blockers instead of before the attack |
| RP_188 | N | "Never casts Folk Medicine": PASS only |
| RP_047 | E | Shock the Bears instead of Tragic Slip |
| RP_132 | E | Charge before Grizzly Bears enters |
| RP_117 | E | Name Stormwild Capridor for Gideon's Sacrifice |
| RP_060 | N | The misreader thinks the token dies, so does nothing: no alternative play exists |
| RP_005 | E | Different trigger targets or a split of Biogenic Upgrade's counters (the `misread_line` names both) |
| RP_145 | E | Sacrifice Vindictive Vampire first |
| RP_034 | E | A different Fiery Confluence mode set (destroy once, or destroy plus destroy the Jar) |
| RP_067 | U | What the misreader does differently is not stated (cast without replicate? do not cast?) |
| RP_153 | E | Duplicant exiles the Bears instead of the Hill Giant |
| RP_071 | N | No combat in the puzzle; the misreader does nothing, or the same Olivia ping |
| RP_055 | N | The same Treachery cast; only the belief about the number of triggers differs |
| RP_100 | E | Char on Platinum Angel instead of Murder |
| RP_196 | E | Song of the Dryads on another permanent |
| RP_096 | E | Hurkyl's Recall on yourself in response to the trigger |
| RP_235 | E | Pillar of Flame without removing the Soul-Scar Mage first (the `misread_line` is just "expects exile"; the `rule` field gives the play) |

Counts: **E 14, N 5, U 1.** Wilson 95% interval for E/20: 48.1 to 85.5% (if U were E: 15/20, 53.1 to 88.8%).
The classification used the manifest text, not what a probe could do: RP_235 and RP_005 were judged expressible from
the rule text and a named alternative, and both probed as such. All six non-E puzzles are the kinds the work plan
listed as not tried (a belief with no different play, a PASS-only line); none was probed.

## Commands

```bash
# proof (control source): the search arm at the gate's budget, one JVM, as in the pilot
#   puzzle_baselines.run_arm(build, pzl, "search", "1", dir, search_env(600))   -> row.line
# every probe, one JVM each (a wrapper called the same code and appended the compact row)
FORGE_HOME=/root/forge python3 eval/puzzles/puzzle_baselines.py --build <build> probe \
  --pzl eval/puzzles/rules-pilot/<RP_nnn>.pzl --line "<line>" [--opponent search] [--completion-seed N]
# menus
FORGE_HOME=/root/forge python3 eval/puzzles/probe_walk.py --build <build> --pzl <pzl> --prefix "<steps>" --options
```

Every JVM ran under the shared machine lock. `--opponent search` was used only for a line with `Opponent:` steps
(RP_034's control and its adversarial misreading rows). All other misreadings used the default `ai` opponent.
`probes.jsonl` has 44 rows in the pilot's shape plus `puzzle`, `role`, `pzl_sha256`, `misreading_index`,
`completion_seed`, `opponent` and `game_seed`. Roles: `control` (the proof line probed, 14),
`misreading` (24), `misreading_vs_search_opponent` (2, RP_034), `misreading_other_game_seed` (3, RP_034),
`variant_not_the_misreading` (1, RP_152). The other probes run while finding menus (sentinels, prefixes, one-step
values, about 70 runs) are not kept.

## Probe table

Control = the proof line probed with a seeded random completion: `accepted` for all 14 (the proofs were `proven`,
`accepted` in the search arm). A misreading below is the WHOLE line, and every one was re-run until the game
consumed it whole (`drift` empty; no `line not consumed` row is cited). Verdict is one seeded completion (seed 0)
unless stated.

| Puzzle | Misreading line (as played) | Verdict | Judgement |
|---|---|---|---|
| RP_017 | `Solver:3:0` (cast Angel's Grace paying with City during casting) | rejected: life 0, winner p1 | good: the City trigger resolves first and kills, as the misreading says; nothing else is castable |
| RP_047 | `Solver:5:0 Solver:3:2` (Shock the Bears) | rejected: Bears not in graveyard | good: the Shock removes the shield, no mana left |
| RP_092 | `Solver:3:2 Solver:3:2 Solver:2:1 Solver:3:2 Solver:3:0 Solver:2:0` (attack, Hill Giant blocks, Jump on the Bears after the block) | rejected: opponent at 20 not 18 | good: the log shows the Bears still blocked (`damage-assign: all 2 -> Hill Giant`) |
| RP_100 | `Solver:5:0 Solver:3:2` (Char on the Angel) | rejected: winner p1 | good: Char's 2 damage kills the solver |
| RP_117 | `Solver:3:1 Solver:2:0 Solver:2:1` (Capridor chosen) | rejected: Capridor gone | good, with a caveat: the log does not name the choice; value 1 = Capridor is inferred from the outcome (the other value is the control and keeps it) |
| RP_132 | `Solver:6:1 Solver:2:1 x5 Solver:5:0 Solver:2:1 x3 Solver:4:0 Solver:3:1 Solver:2:0 x3` (Charge, then Bears, then Fling the Bears at the opponent) | rejected: opponent at 1 | good: the Bears are 2 power, not pumped |
| RP_145 | `Solver:2:0 Solver:4:1 Solver:2:0 Solver:2:0 Solver:3:1` (sacrifice the Vampire first, then Teysa) | rejected: opponent at 2 | good: the Vampire's trigger is not doubled, as the manifest says |
| RP_153 | `Solver:3:0 Solver:3:1 Solver:3:2 Solver:2:0 Solver:3:0 Solver:4:1` (Duplicant exiles the Bears, Fling) | rejected: opponent at 1 | good: Duplicant is 2/2 |
| RP_196 | (a) `Solver:3:0 Solver:5:0` (Song on Craw Wurm); (b) `Solver:3:0 Solver:5:1 Solver:2:1` (Song on a Forest, attack) | rejected (both): opponent at 6 / 3 | good: the other two Forest targets are the same play as (b), not run |
| RP_005 | (a) `Solver:5:0 Solver:2:1 Solver:2:0 Solver:2:0 Solver:2:0 Solver:2:1 Solver:2:1 Solver:2:1` (both triggers tap Hill Giant); (b) the same with both on the Bears; (c) `Solver:5:0 Solver:2:1 Solver:2:0 Solver:2:1 Solver:2:0 Solver:2:0 Solver:2:1 Solver:2:1` (split counters Sharktocrab + Hill Giant) | rejected (all): opponent at 10 / 10 / 4 | good for (a), (b): the untapped blocker takes the 10. (c) puts counters on the opponent's creature, a silly play that the `misread_line` does name |
| RP_152 | `Solver:5:2 Solver:5:4 Solver:5:0 Solver:2:1 Solver:2:1 Solver:2:1` (animate Conclave, let it resolve, then Dress Down, attack) | rejected, with `ai` and with `search` opponent: opponent at 2 | good: `damage-assign: all 2 -> Giant Spider`; Dress Down strips the flying and the reach. Caution: `Solver:5:2 Solver:5:0` is `accepted`, but it casts Dress Down on top of the animation on the stack, so Dress Down resolves first. That is the proof's order, not the misreading (kept as `variant_not_the_misreading`) |
| RP_096 | `Solver:6:0 Solver:2:0 Solver:2:1 Solver:4:3 x8` (Recall on yourself, then pass) | rejected: Ring not on battlefield, Recall not in hand | trivial: the acceptance requires the Ring and the Recall, so any cast of Recall fails it; the manifest note says so. The life clause held in this run (no life loss is listed as failed), which is not what the rule says; not investigated |
| RP_235 | (a) `Solver:4:1 Solver:4:3 Solver:3:2` (Pillar on Merfolk, Mage still there, pass); (b) the same ending `Solver:3:0` (then Flame Slash the Mage) | rejected (both): Merfolk not in exile | good: the Mage turns the damage into counters |
| RP_034 | (a) `Solver:3:0 Solver:3:1 Solver:2:0 Solver:2:1 Solver:2:0` (2 damage, 2 damage, destroy the Golem); (b) `Solver:3:0 Solver:3:1 Solver:2:1 Solver:2:0 Solver:2:1` (2 damage, destroy Golem, destroy Jar) | **accepted** (default `ai` opponent, completion seeds 0, 1, 2; game seeds 1 to 4 for (a)) | **finding, below** |

Proof lines (search arm, budget 600, all `proven`, `accepted`):

| Puzzle | Proof line |
|---|---|
| RP_005 | `Solver:5:0 Solver:2:1 Solver:2:0 Solver:2:0 Solver:2:0 Solver:2:1 Solver:2:0 Solver:2:1` |
| RP_017 | `Solver:3:2 Solver:3:1 Solver:5:0 Solver:2:0` |
| RP_034 | `Solver:3:0 Solver:3:1 Solver:2:1 Solver:2:0 Solver:2:0 Opponent:2:0 Opponent:2:0` |
| RP_047 | `Solver:5:1` |
| RP_092 | `Solver:3:0 Solver:2:0 Solver:2:1` |
| RP_096 | `Solver:6:5 Solver:6:1` then `Solver:2:1` x10 |
| RP_100 | `Solver:5:1` |
| RP_117 | `Solver:3:1 Solver:2:0 Solver:2:0` |
| RP_132 | `Solver:6:4 Solver:6:3 Solver:6:0 Solver:2:1 Solver:2:0 Solver:2:1 Solver:2:1 Solver:2:1 Solver:5:3 Solver:4:3 Solver:5:0 Solver:2:1 Solver:3:1 Solver:2:1 Solver:2:0 Solver:3:1 Solver:2:1` |
| RP_145 | `Solver:2:0 Solver:4:1 Solver:2:1 Solver:2:1 Solver:2:0 Solver:3:1` |
| RP_152 | `Solver:5:3 Solver:5:0 Solver:2:0 Solver:2:0 Solver:2:1 Solver:2:1 Solver:4:3 Solver:4:1 Solver:2:1` |
| RP_153 | `Solver:3:0 Solver:3:2 Solver:3:2 Solver:2:0 Solver:3:0 Solver:4:1` |
| RP_196 | `Solver:3:0 Solver:5:4 Solver:2:1` |
| RP_235 | `Solver:4:0 Solver:2:0 Solver:3:1 Solver:2:0 Solver:3:2` |

## Rates

* Expressible share: 14 of 20 = 70%, Wilson 95% 48.1 to 85.5% (n = 20 from 210).
* Rejected share of the 14 probed puzzles (every whole misreading rejected, against Forge's AI): 13 of 14 = 92.9%,
  Wilson 95% 68.5 to 98.7%. If RP_034 is judged by the adversarial-opponent rows (both rejected), 14 of 14 (78.5 to 100%).
* Per misreading line: 17 of the 19 distinct whole lines probed against the `ai` opponent were rejected; the 2 accepted
  are RP_034's. The intervals above are per puzzle; lines within a puzzle are not independent.

## Finding: RP_034 does not test its rule against Forge's AI

The rule is that one regeneration shield replaces only the first destruction (Welding Jar regenerates the Golem once).
A solver who picks the modes wrongly, destroying the Golem only once, still reaches the acceptance state when the
opponent is Forge's AI, because the AI does not respond with the Welding Jar. Both lines (a) and (b) end `accepted`:
the opponent is dead from the two damage modes, and the single destruction puts the Golem in the graveyard.

* Re-run with 2 more completion seeds (1 and 2): `accepted` for both lines, as for seed 0. The line is complete (the game
  asks nothing after it), so the completion seed has nothing to vary; line (a) was also run on game seeds 2, 3 and 4:
  `accepted`.
* The proof is "against every opponent reply": with the opponent's Jar steps written into the line
  (`... Opponent:2:0 Opponent:2:0`, `--opponent search`) both misreadings are `rejected` (Stone Golem not in the
  graveyard). With the opponent passing or answering at random the same lines are `accepted`. So the puzzle tests the
  rule only against an opponent that regenerates, and the suite's scoring opponent (Forge's AI, `docs/eval/PUZZLES.md`) does not.
* Not determined: whether this is a Forge AI choice of this state or general (the AI passes the Jar in these runs);
  whether the model seat would rather meet the same acceptance by this line (not run: no model). A fix would need the
  acceptance to survive the AI's pass (for example require the opponent to be at `<= 0` and the Golem in the graveyard
  AND the Jar in the graveyard), which is outside this folder; `manifest.jsonl` was not edited.

## What was not determined

* Each rejected verdict is the line plus ONE seeded random completion (seed 0), as in the pilot. Most lines end where
  the misreading has nothing left to play, but this does not prove no completion is accepted. The search-arm follow-up
  (prefix fixed, ask whether ANY completion is accepted) is still open.
* The 6 non-expressible or unclear puzzles were not probed (RP_067 might be expressible; its manifest line does not say).
* The RP_117 and RP_005 menu values are read off outcomes and target lists, not from a log line that names the choice.
* The classification is one author's reading of the manifest text; a different reader could move RP_067, RP_005 or RP_235.
* RP_096's life clause (see the table) and RP_034's AI behaviour were not investigated further.

## Input sha256 of each `eval/puzzles/rules-pilot/<id>.pzl`

* RP_005 `580581465512401025f48eae682d7919d069fdb54b41db3b4d68c7f3b824b1be`
* RP_017 `62ecda107f32bcea5f5352f24956b7949764c47046b07ab3fb046de19624e86b`
* RP_034 `f640f9b7e89aa1d4decbd61db640aedd11cbb7cbf49353f7a5b05f7ae4ab428a`
* RP_047 `9008d2ce892084cb84ab6a6554b00704b44b7391325bfa7723b227d23f44f265`
* RP_092 `a579ce028f64f643562795ea04cd8d7e4b7eef8ac43d614ff42add1d366ab5b8`
* RP_096 `efd05fddea2ea58418aa90fb004a42476189c9213999eb8e7d98c438577386d1`
* RP_100 `3a34b58eaa9567d4b026b5fb58b92b76278e9e3b780473fe22618a6d0f343977`
* RP_117 `d6700679c2e5c886e2039be0121061a850bc406414361665153380520de263d7`
* RP_132 `8d31b975b2f4efd6f082389ca97027c123d18bca40e9d3f80565ec7e8d62965a`
* RP_145 `6794cdd4571738e168329ad07a56ebffa5f197073e9cff65daec324d66d3e033`
* RP_152 `d0294363ec7aec4390a192299e56294165c96db8a7e9d1ef2efb093b941abcc0`
* RP_153 `818b09f559c476eb6aea81925cbc951e517da8773c5d0c944d1961fded13c964`
* RP_196 `a29d25a76aee5d591fc3363f4b2206a8669eb62d05e220438cbd362c970bf06e`
* RP_235 `e3bdeaed102730e0638295404bcc9399e48844376a5ee3156c26ffab3a04c83a`
* RP_061 `a6f2bd3953b25a4fc87ecc5b5f58294434d053f59f6eacd130a1b902e5c18c53`
* RP_188 `a07bfabf7c60dd9d5613af3c078fc27e44b72593155e37361e488fe9f5e1dc7c`
* RP_060 `2c5ce09982f5fc2c7ec54d67344782ae3c9df3017bd78f73830c7033194f547d`
* RP_071 `3b8b59f020bff332951b55ecf0e02740318c7c34f8f1fa4758091f216ea12330`
* RP_055 `fbe2744a9c298b796ad6e50333749aeda9d134483236c79e59329187b37e89d2`
* RP_067 `9724dc73f5b1207a91e7595374446db017158cbb937d46cdc23f7917e1734b5e`

## Effort (the author's tally, not recomputable from this folder)

About 115 result rows were produced by the probe and proof wrapper (14 proofs plus 100 probes), plus roughly 100 more
JVM runs inside `probe_walk` (sentinel and `--options` walks, 6 to 13 per menu) and a few log-reading runs; about 215 JVMs
in all, 10 to 40 s each (a search proof 8 to 40 s, a probe 10 to 15 s), well under the 40-run stop per puzzle (the
largest were RP_132 and RP_034, roughly 25 each). The 44 kept rows are the ones cited above. Per expressible puzzle that
is about 15 JVM runs, against 19 in the pilot. The costly menus were again those with a payment, modes or a target
inside a cast (RP_132, RP_034, RP_005, RP_152); the sample had no puzzle that cost over the cap.
