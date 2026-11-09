# Misreading probes, pilot on 10 rules-pilot puzzles

Ten converted puzzles from `eval/puzzles/rules-pilot/manifest.jsonl` (RP_002 onward, each with a `misread_line`),
each with its proof line, a control run of that proof, and the whole misreading written as a probe line.
Zero-model, no network. `probes.jsonl` holds 53 rows in the shape of `../2026-11-09-pzl-probe/rp001-probes.jsonl`
(`line`, `cli_last_line`, `build_overlay_sha`, the result row's probe fields, the first `[ROUTER]` lines) plus
`puzzle`, `role` (`control`, `misreading`, `onestep`), `pzl_sha256` and, on misreading rows, `misreading_index`. The other probes run while finding
the menu indices (about 140 runs, sentinel and prefix probes) are not kept: they only read a bound or a next decision.

**Build:** overlay sha `edaca55afdf6`, engine `34a8662b7966`, built once with
`STAGES="preflight classpath compile" FORGE_HOME=/root/forge OUT=<build> bash eval/harness_acceptance.sh`.

**Commands**

```bash
# the proof line: the search arm at the gate's budget, as RP_001's was produced. No subcommand prints one puzzle's
# line, so a three-line wrapper called puzzle_baselines.run_arm(build, pzl, "search", "1", dir, search_env(600))
# and printed the row's `line`
# every probe, one JVM each
FORGE_HOME=/root/forge python3 eval/puzzles/puzzle_baselines.py --build <build> probe \
  --pzl eval/puzzles/rules-pilot/<RP_nnn>.pzl --line "<line>" --out-dir <dir> [--opponent search]
```

`--opponent search` only for the two proofs that contain an `Opponent:` step (RP_022, RP_031). Every misreading
used the default opponent (it has no opponent step).

## How a menu index was found

The line is `Seat:bound:value`, one step per decision the game draws; **bound is the number of options at that
decision**. Three tools, all from the probe itself:

* a one-step probe `Solver:N:i` for each `i`: the first `[ROUTER] play:` / `target:` line (and `actions`) name the
  choice taken at that menu (kept as `role: onestep`);
* a sentinel step `Solver:99:0` after a prefix: the verdict is `drift: draw K: line has Solver:99:0, game asked
  Solver:B`, which gives the bound of the next decision without guessing (not kept);
* the proof line's own `[ROUTER]` log, to align its draws with plays.

What the draws were in these puzzles: a main-phase menu is `[castable spells..., one mana tap per land type, PASS]`
(PASS last); paying a cost with a choice is one binary draw per land (0 tap, 1 keep untapped); a scry is one
binary draw; attackers are one binary draw per creature (1 attacks); modes of a "choose one or two" spell are one
binary draw per mode; a "may" trigger is a binary draw (0 yes). A game that stops asking draws before the
line ends is reported `drift: line not consumed`, so every misreading below was re-run as the exact line that is
consumed whole (no `not consumed` row is cited).

## Result

No whole misreading was accepted: 10 of 10 puzzles reject their misreading, and 10 of 10 proofs are accepted
as controls. **Every rejection comes from the misreading's own plays** (the failed acceptance lines name exactly what
the misread play leaves undone); none is a line that stopped short or a random finish. Each verdict is one line plus one seeded completion.

**Selection.** The ten were chosen by reading the manifest for misreadings expressible as a different order or
choice of the solver's own plays; none was tried and dropped. So 10 of 10 says nothing about the share of the 221
`misread_line`s a probe can express. For RP_022 and RP_031 the proof (control) ran with `--opponent search` and
the misreading with the default `ai` opponent; the rows do not record the opponent arm.

| Puzzle | Rule (short) | Misreading line (as played) | Verdict | Judgement |
|---|---|---|---|---|
| RP_004 | Marked damage stays; lethal damage kills a creature that loses indestructible | `Solver:5:1 Solver:3:2 Solver:2:0 Solver:2:0` (Shock the Gargoyle, no Turn to Frog) | rejected: Gargoyle not in graveyard | good: Shock is the misreading's play; only the Gargoyle line fails |
| RP_006 | Storm counts spells cast before it; Finale casts one at a time in your order | `Solver:3:0 Solver:5:4 Solver:2:0 Solver:2:0 Solver:3:0 Solver:2:0 Solver:2:0 Solver:2:0` (Finale, Empty the Warrens before Evermind) | rejected: 4 Goblins, not 6 | good: the four Goblins are the misreading's own count, as the manifest predicts |
| RP_022 | Twinstrike's hellbent "destroy instead" beats the Swans' replacements | `Solver:6:0 Solver:2:0 x5 Solver:2:1 Solver:2:1 Solver:3:0` (Twinstrike first with Bears in hand, then Bears); variant `...3:2` passes instead | rejected (both): Swans not dead, hand 4 or 5 | good: the Swans prevent and draw; both the cast-Bears-after and the pass-after variants fail |
| RP_026 | Players get priority between first-strike and regular damage | (a) `Solver:2:0 Solver:2:1 Solver:2:1 Solver:2:1` (equip Jadar in main phase, attack with both); (b) `Solver:2:1 x4 Solver:2:0 Solver:2:1` (equip after attackers, before first-strike damage) | rejected (both): `winner p0 (was p1)` | good: both orders are the misreading's, each ends at the same state |
| RP_031 | A resolved spell is in the graveyard before SBA, so Pyroclasm kills the Blob | `Solver:4:1 Solver:5:3` (Shock one Bear) | rejected: one Bear left | good: the second Mountain cannot cast Pyroclasm, so the line is complete as played |
| RP_036 | Umbra armor replaces destruction, not a sacrifice | `Solver:4:0` (Murder; three Swamps, no mana left) | rejected: Treefolk survives | good: the whole misreading is the one cast; nothing else is castable |
| RP_048 | Fiend Hunter leaving before its trigger resolves exiles the Bears for good | `Solver:5:3 Solver:5:0 Solver:2:0 x4 Solver:4:1 Solver:3:2 Solver:3:2 Solver:2:0 Solver:3:0` (let the trigger resolve, then Compelling Deterrence on Fiend Hunter) | rejected: Bears not exiled (Fiend Hunter in hand holds) | good: the failing line is exactly the Bears returning |
| RP_052 | A "creatures you control get +1/+0" effect misses a creature stolen later | `Solver:5:1 Solver:2:1 x3 Solver:2:0 Solver:2:0 Solver:3:0 Solver:2:1 Solver:2:1 Solver:2:1` (Preordain, then Act of Treason, attack with both) | rejected: `winner p0 (was p1)` | good: order is the misreading's; both creatures attack |
| RP_068 | Instructions run in order: the Hydra is smaller after the sacrifice | `Solver:4:0 Solver:2:0 x3 Solver:6:5 Solver:2:1 Solver:6:0 Solver:5:0` (Entish Restoration only, no Rancor, fetch 2) | rejected: 5 Forests, not 6 | good: the misreading is the omission, the fetch is played out (`2:1` does not cancel the search) |
| RP_069 | Lethal damage keeps a creature on the battlefield until SBA after the spell | `Solver:5:0 Solver:2:1 Solver:2:1 Solver:2:0 Solver:2:0 Solver:2:0 Solver:2:0 Solver:2:1` (Flame of Anor, draw two + destroy artifact, no Flame Slash) | rejected: life 0, not 2 | good: only the life line fails; the Crawler triggers kill the solver, as the manifest says |

Proof lines (all `proven`, `accepted`, search arm, budget 600) and control rows are in `probes.jsonl`
(`role: control`); the proofs are:

| Puzzle | Proof line |
|---|---|
| RP_004 | `Solver:5:0` |
| RP_006 | `Solver:3:0 Solver:5:4 Solver:2:0 Solver:2:0 Solver:3:1 Solver:2:0 Solver:2:0 Solver:2:0 Solver:2:0` |
| RP_022 | `Solver:6:1 Solver:2:1 Solver:2:0 Solver:2:1 Solver:2:1 Solver:2:1 Solver:2:1 Solver:5:4 Solver:5:3 Solver:5:3 Solver:4:0 Solver:2:0 Opponent:2:0` |
| RP_026 | `Solver:2:1 x6 Solver:2:0 Solver:2:1` |
| RP_031 | `Solver:4:0 Opponent:2:0` |
| RP_036 | `Solver:4:2 Solver:4:1 Solver:2:1` |
| RP_048 | `Solver:5:3 Solver:5:0 Solver:2:0 x4 Solver:4:1 Solver:3:2 Solver:3:0 Solver:2:0 Solver:2:0` |
| RP_052 | `Solver:5:0 Solver:2:1 Solver:2:0 x3 Solver:2:1 Solver:3:0 Solver:2:0 Solver:2:0 Solver:2:1 Solver:2:1` |
| RP_068 | `Solver:4:1 Solver:3:1 Solver:3:2 Solver:3:0 Solver:2:0 Solver:2:0 Solver:2:1 Solver:6:2 Solver:5:1 Solver:4:2` |
| RP_069 | `Solver:5:1 Solver:2:1 Solver:2:0 x3 Solver:2:1 Solver:4:1 Solver:4:1 Solver:3:2 Solver:3:0 Solver:2:1 Solver:2:0 Solver:2:0` |

(`xN` abbreviates repeated steps in this table only; `probes.jsonl` has every step.)

Input sha256 of each `eval/puzzles/rules-pilot/<id>.pzl`:

* RP_004 `b7ab0e68ea14213b530bbe802511332267c82feea06914e4d0a1250cb35c5b95`
* RP_006 `cf2feae93964b09218c8a26b6d61ac2137189f0cc76b3e681b83de4edcf65852`
* RP_022 `65b50a66343cfe9d37a848136ab91d11416e8a4d1aa8b1202f1ba9a4eaad565e`
* RP_026 `5fa1e30e7abc8734306a67a99225f361ce1b5ee39bc7737eb8fe28e650215a9d`
* RP_031 `b4ebc2d361becf5e8c10e0cb0ea5cbf24658c5ee08c3d128e62afafe7aeebb45`
* RP_036 `5ff178ff469c07f7e5bbde41b8355c8e1b2f416af365b5502253058607a0a4aa`
* RP_048 `41001980f699aba0ec4df50463bf8302ab5232a630481cae2eb85af8f8f4ffdd`
* RP_052 `8f584e79c6754320fb5963595ac31902a974e85c3d55dfe97b705f948b5641c8`
* RP_068 `16d3c7a68dafe909f410401c70378822bec52414dc2380528afc31c791c89ab9`
* RP_069 `254b6fa5c9178199b3101142aa9759e677896300cda70c9b14c8113805961606`

## What each verdict does and does not say

A `rejected` is this line plus ONE seeded random completion (seed 0) failing the acceptance state. For these ten
the line ends at, or one trivial PASS before, the point where the misreading has nothing left to play (RP_031,
RP_036, RP_004: no mana left; RP_068, RP_069, RP_052: the spells are cast and combat declared). They are not proof
that no completion of the misreading is accepted: RP_022 and RP_026 have a second variant that was also run, and
the rest are one line each. The search-arm follow-up (prefix fixed, ask whether ANY completion is accepted) is
still open.

Two cautions from running them:

* The one-step probe of the proof's own first move (`Solver:4:1` on RP_036, `Solver:4:0` on RP_031) is `accepted`
  with a random completion. That is the RP_001 lesson again: a one-step verdict says nothing about the rule.
* RP_006's index for "Empty the Warrens first" was inferred from the probe itself (`3:0` instead of `3:1` at the order
  choice produced `cast Empty the Warrens, Evermind, Empty the Warrens` in `actions`); the log names the plays, not the menu.

## Effort

Wall-clock per JVM: about 40 s for a search proof, about 10 to 15 s for a probe. JVM runs per puzzle (search
proof + control + misreading + the menu-finding probes): RP_036 8, RP_006 7, RP_026 8, RP_048 11, RP_031 15,
RP_004 20, RP_068 20, RP_022 26, RP_069 32, RP_052 44 (about 190 in all, 40 minutes of JVM time). These counts
and times are the author's tally: only the 53 rows above are kept, so they cannot be recomputed from this folder. A single
decision menu (one spell, one target) costs 6 to 8 runs; a spell with a mana payment, scry or modes costs 4 to 6
more draws, found one at a time. RP_052 was the worst because the payment, scry and attackers each took a binary
draw whose meaning had to be read off the log.
