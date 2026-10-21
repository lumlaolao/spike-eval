# Niche-rules hard puzzles (25, authored 2026-10-21)

Hand-authored boards whose solution turns on one rarely-met rule, five per theme. Each file is a `.pzl` with a neutral
`Description:` (the seat's brief), a `Rule:` line naming the rule and its CR numbers (the seat never reads it), a
`[solution]` written line and an `[acceptance]` state (`eval/PUZZLES.md`).

Every puzzle was checked with the free tools, no model involved (`eval/PUZZLES.md`, "Proving a written line"), against
the bar in `docs/harness/HARD-PUZZLES.md` section 1:

- `puzzle_baselines.py --build B line --pzl <file>`: the written line plays every line and solves against Forge's AI;
  all 25 re-run on the committed files, outputs in `runs/puzzles/niche/final/<name>/`;
- `line --opponent search`: `proven`, the one fixed line beats every opponent reply the engine offers. Search budgets:
  zones and `token_first` 200 (the recheck 600), combat 400, layers 1000, replacement 2000, stack and `torbran` 3000.
  Outputs in `runs/puzzles/niche/<theme>/<run>-search/` (`<run>` below); the five files edited after their first proof
  (four zones boards and `blocker_removed`) were proven again on the committed bytes in `runs/puzzles/niche/recheck/`;
- floors, from `puzzle_baselines.py run --arms random,pass,first,ai,greedy --baselines-only` on a candidate suite:
  random (50 rollouts; the stack theme 20, too few to resolve the bar's "at most 2%"), PASS (4 seeds), first-option (4),
  Forge AI as the solver (4) and greedy-preview (4; the stack theme with its other floors, the other 20 boards in
  `recheck/greedy.json`). Every one solved 0, except `reequip`, Forge AI 1 of 4 (within the bar's "at most 1 of 4"), on
  a line that worked only because the AI opponent did not block. Outputs in `runs/puzzles/niche/<theme>/` (`floors*`,
  `*-floors*`) and `runs/puzzles/niche/recheck/floors.json`;
- `--minimality`, which replays the line with each action removed, against Forge's AI only. Many removals are
  "inconclusive" (the shortened line stalls: later labels no longer match), and the authors replayed those as
  hand-written variant lines, each of which lost. Some removals came back "played" (solved without them): the `pass`
  lines (21 of `crawler`'s 33, all 23 of `mirror`'s, 16 of `zones_redcap`'s 18, three of `blocker_removed`'s four),
  which only let the stack resolve; `humility`'s land drop, four Thuds and Fling, because Forge's AI declined the Royal
  Assassin chump block the worst case needs; and one `torbran` Bombardment, because the AI took the larger damage order.
  Minimality against Forge's AI therefore cannot rule on `humility` or `torbran`. Outputs in
  `runs/puzzles/niche/<theme>/<run>-minimality*/` (combat: `<run>-min/`); `blocker_removed` and `zones_redcap`, edited
  after their first run, were replayed on the committed bytes in `recheck/blocker_removed-minimality/` and
  `recheck/zones_redcap-minimality/` with the same result.

`<run>`, the run-directory name: the file's last word for replacement, zones and layers (`torbran`, `crawler`,
`reequip`); for combat `band_redirect`, `blocker_removed`, `dt_trample`, `late_ds`, `marked_lethal`; for stack
`redcap`, `split-second`, `steam-kin`, `storm-copy`, `token-first` (`split-second` has only `-minimality2`,
`token-first` both `-minimality` and `-minimality2`).

The `runs/` outputs are not committed; each command above reproduces them, with `B` an isolated acceptance build and
`FORGE_HOME` the pinned checkout.

A held-out split and a frozen suite are not made yet: these files are open answers.

| Theme | File | Rule (CR) | L | Opponent's choice |
|---|---|---|---|---|
| combat | `combat/niche_combat_band_redirect.pzl` | banding: the attacker divides the blocker's damage (702.22h, 702.22k) | 11 | block or not |
| combat | `combat/niche_combat_late_double_strike.pzl` | double strike granted in the first-strike step still deals regular damage (702.4d, 510.4) | 11 | a discard (weak) |
| combat | `combat/niche_combat_deathtouch_trample.pzl` | deathtouch makes 1 lethal for trample; pump order (702.2c, 702.19b-d, 510.4) | 10 | block or not |
| combat | `combat/niche_combat_blocker_removed.pzl` | a creature stays blocked when its blocker is removed (509.1h, 510.1c, 702.19d) | 12 | block or not |
| combat | `combat/niche_combat_marked_lethal.pzl` | damage already marked counts toward lethal for trample (702.19b, 120.6) | 9 | block or not |
| replacement | `replacement/niche_replacement_scales.pzl` | the controller orders counter replacements: Scales first (616.1, 616.1e-f) | 28 | which attacker is blocked |
| replacement | `replacement/niche_replacement_torbran.pzl` | the damaged player orders Torbran and Fiery Emancipation: 3x+2 (616.1) | 11 | block, seven orders |
| replacement | `replacement/niche_replacement_leyline.pzl` | Leyline of the Void exiles cards, tokens still die (614.6, 108.2b, 111.6) | 10 | block |
| replacement | `replacement/niche_replacement_palisade.pzl` | redirected damage keeps deathtouch (614.9, 702.2b-c) | 11 | block |
| replacement | `replacement/niche_replacement_consuls.pzl` | Authority of the Consuls checks the permanent after the copy effect (614.12, 616.1c) | 10 | block |
| zones | `zones/niche_zones_flayer.pzl` | a flickered card is a new object; Flayer triggers only from the graveyard (400.7, 702.93a) | 11 | block |
| zones | `zones/niche_zones_redcap.pzl` | persist and counter annihilation; last-known information (702.79a, 704.5q, 603.10a) | 14 | block |
| zones | `zones/niche_zones_hunter.pzl` | Fiend Hunter's linked triggers; Banisher Priest leaving first (607.2a, 610.3b) | 10 | block (2 choice points) |
| zones | `zones/niche_zones_mirror.pzl` | tokens cease to exist off the battlefield; undying on a token copy (111.8, 704.5d) | 13 | block |
| zones | `zones/niche_zones_crawler.pzl` | cast triggers never see "put onto the battlefield" (601.2i, 601.3) | 17 | block |
| layers | `layers/niche_layers_switch.pzl` | P/T switching applies after every other P/T effect (613.4b-d) | 10 | an instant (Lightning Bolt) |
| layers | `layers/niche_layers_reequip.pzl` | an attached Equipment gets a new timestamp; re-equipping in place does nothing (613.7, 701.3b) | 11 | block |
| layers | `layers/niche_layers_humility.pzl` | Humility and Opalescence by timestamp; a flicker resets it (613.4b, 613.7, 613.8) | 8 | a chump block |
| layers | `layers/niche_layers_copy.pzl` | a copy takes copiable values only (707.2, 613.2a) | 11 | block |
| layers | `layers/niche_layers_lattice.pzl` | March of the Machines depends on Mycosynth Lattice (613.8, 613.8a) | 10 (6 without mana taps) | block |
| stack | `stack/niche_stack_token_first.pzl` | ordering simultaneous triggers: the token before the pump (603.3b, 611.2c) | 10 | which of 12 creatures is blocked |
| stack | `stack/niche_stack_steam_kin.pzl` | intervening-if triggers checked on cast and resolution (603.4, 605.3a) | 10 | none |
| stack | `stack/niche_stack_redcap.pzl` | counter annihilation in response to the persist trigger (704.5q, 704.3) | 12 | none |
| stack | `stack/niche_stack_split_second.pzl` | split second shuts off the opponent's fog; it counts for storm (702.61a-b, 702.40a) | 10 | block |
| stack | `stack/niche_stack_storm_copy.pzl` | a copy of a storm spell is not cast and gets no storm copies (702.40a, 707.10) | 10 | none |

L counts the solver's actions on the written line as HARD-PUZZLES.md section 1 does (casts, non-mana activations,
land drops, attacks, and a mana ability where the engine offers it as a choice: `lattice`'s four taps); `leyline`'s
Ashnod's Altar sacrifices are mana abilities the engine does not offer, so its L of 10 leaves them out (14 with them).

Known limits:

- `torbran`: Forge orders several damage replacements from a `HashMap`, so the order offered changes between games; of
  the two search runs kept, `torbran-search` proved and `torbran-search-drift1` drifted, and Forge's AI picks an order
  at random. Provisional until that is made deterministic.
- Short of the bar: `marked_lethal` (L 9), `humility` (L 8), `lattice` (L 6 without its mana taps); the stack theme's
  random floor ran 20 rollouts, not 50; `late_double_strike`'s opponent choice is a discard; `steam_kin`, `stack_redcap`
  and `storm_copy` give the opponent no choice.
- Not searched for: a second solution (uniqueness); `scales`, `torbran` and `leyline` have known alternative lines
  reaching the same total.
- No Forge rules bug was found in any of the 25 boards.
