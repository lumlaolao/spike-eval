# Rules pilot: first 20 triage conversions

Source: the ranked list in `eval/puzzles/rules/TRIAGE.md`. #2 was skipped by the constraints (its deciding choice
is how trample damage is divided, which the search arm splits evenly), so #21 was taken instead. Twenty questions
were attempted as `RP_001` to `RP_020`. One line per question is in `manifest.jsonl`; #2 has its own line too.
Gate: the search arm (budget 600, cap 60 s) must prove the puzzle with a unique solution, the pass arm must
fail, and the proven line must exercise the rule. Each puzzle's behaviour was also checked in random-arm game
logs (30 seeds).

## Counts

| result | n | puzzles |
|---|---|---|
| converted | 18 | 001, 002*, 003-010, 012-016, 018, 019, 020* |
| Forge disagreement | 1 | 011 |
| dropped | 2 | 017 (cannot be proven here), triage #2 (constraint, not built) |

\* These pass the gate with a caveat. The search arm never branches trigger order, so their "unique" does not
cover orders, and they are proven only because Forge's default order happens to be the right one. The misread
orders were confirmed to fail with the random arm, which does shuffle trigger order.

Uniqueness was exhaustive (exhausted = deviations) for every converted puzzle except RP_005 (6 of 8). The pass
arm was rejected on all 20. Rough effort: 104 minutes over 21 lines, about 5 minutes per puzzle. The time
included two rounds of diagnosing harness behaviour.

## Forge disagreements

- **RP_011** (Vraska, Betrayal's Sting with Doubling Season). CR 616.1 lets the controller order Compleated and
  Doubling Season (10 or 8 loyalty). Forge never asks: both replacement-order calls have one option. Even so,
  the applied order differs between games with identical decisions: casts that paid life ended at 8 in some
  random-arm games and at 10 in others. The search proof depends on an order the engine picked, not on any
  choice the solver made.

## Dropped

- **RP_017** (City of Brass + Angel's Grace). The arms never get the winning line. Angel's Grace is not on the
  menu (castable 0 with an untapped City of Brass at 1 life). Floating a land's mana before casting is offered only
  to the faithful seat. The puzzle is kept for model evaluation, but it has no proof.
- **Triage #2** (deathtouch + trample). Not built: the deciding choice is the trample damage division.

## Grammar needs

None. Every acceptance state used `life`, `count`, `tokens`, `counters` or `winner`. One trap: tokens are named
`"Goblin Token"` and `"Treasure Token"` in the engine (Marit Lage keeps its own name), so the `tokens ... "Goblin"`
example in `eval/PUZZLES.md` never matches.

## What made puzzles fail the gate (and the fixes)

- **Second solutions from extra actions:**
  - An optional land drop from a drawn card (RP_006): fixed with a non-land library.
  - A spell cast and then answered by the solver's own sacrifice (RP_003, Unsummon): fixed by removing the spell.
  - Optional attacks: fixed by starting non-combat puzzles in MAIN2.
- **Hidden actions in the baseline arms' menu:**
  - Sacrifice-cost mana abilities such as Ashnod's Altar are offered only to the faithful seat. Fixed by using
    Spawning Pit.
  - Finale of Promise was hidden (RP_006); see the harness bugs below.
  - Vraska could not be cast with exactly 4+B available.
- **Requirements the arms ignore:** a Lure-forced block was declined, so RP_007's first proof ran through an
  illegal no-block branch. It was rebuilt with the solver defending.
- **Choices the search does not branch:** trigger order (RP_002 and RP_020 carry the caveat above), and damage
  division (the constraint).

## Harness and prover bugs found

1. **Trigger order is never searched.** `PuzzleArms.subset` with lo == hi == n always returns the identity order
   for the search arm. `orderSimultaneousSa` therefore never branches, and uniqueness cannot see a wrong order.
   Evidence: the RP_002 line `Solver:2:1 x3` holds only attack choices.
2. **Spells with optional targets are hidden.** `offerable` requires `targetCandidates >= max(1, minTargets)`
   and checks the targets at X = 0. Finale of Promise (TargetMin 0, targets with mana value up to X) was never
   offered even with six Mountains. Evidence: `scratchpad/rp/RP_006-rand/game-1-*.jsonl`, `castable:0`, with
   Dark Ritual and Empty the Warrens in the graveyard.
3. **The random and search arms ignore blocking requirements.** `declareBlockers` takes a 50/50 per blocker,
   and Forge does not re-check requirements for a controller. A creature lured by Lure went unblocked. Evidence:
   the first RP_007 proof had the opponent declining the block.
4. **Angel's Grace is not offered** with City of Brass at 1 life (`castable:0`), probably because
   `ComputerUtilMana.canPayManaCost` (AI mana planning) avoids the painful source. Evidence:
   `scratchpad/rp/RP_017-rand/game-1-*.jsonl`.
5. **The baseline arms' menu differs from the faithful seat's.** Costly mana abilities and floating mana are
   gated on `observer != null`, so a proof cannot use Ashnod's Altar or mana-first lines.
