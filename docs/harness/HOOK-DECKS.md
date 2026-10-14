# Hook decks: probe decks that make the AI-owned decisions reachable

`ROOT-INTERFACES.md` lists the decision hooks the Forge AI still answers (the 29 `strategic`
`ai-inherited` rows, plus the seat's three partial-override scope-outs). Most are dormant in the
16-deck archetype pool: nothing in it casts a "choose a pile" spell, so `chooseCardsPile` never runs.
These decks exist to change that — one per hook, each built so the hook's card is actually castable,
so a game read (or a `defect-hunter`) can exercise it.

They are **probe decks, not tuned archetypes**: use them to reach a hook and read the decision, not
to compute a win rate. Each is exactly 60 cards and every card name resolves in Forge's own card DB
(the `Name:` line of its script under `forge-gui/res/cardsfolder/`).

| Deck (`eval/decks/`) | Hook it reaches | Where the decision is today | Engine effect that calls it | Issue |
|---|---|---|---|---|
| `hook-piles` | `chooseCardsPile` | Forge AI | `TwoPilesEffect` | — |
| `hook-redirect-copy` | `chooseSingleSpellForEffect`, `chooseNewTargetsFor`, `chooseTarget` | Forge AI | `CopySpellAbilityEffect`, `ChangeTargetsEffect` | — |
| `hook-repeat-modes` | `allowRepeat` modes | **fixed (#127)** — routed via `decideModesRepeat` | `Charm` with `CanRepeatModes$ True` | **#127** (this deck is its repro) |
| `hook-clash` | `willPutCardOnTop` | Forge AI | `ClashEffect` | — |
| `hook-exert` | `exertAttackers`, `enlistAttackers` | Forge AI | `PhaseHandler` | (with the map) |
| `hook-flip` | `chooseFlipResult` | Forge AI | `FlipCoinEffect` | — |
| `hook-discard-unless` | `chooseCardsToDiscardUnlessType` | Forge AI | `DiscardEffect` (`UnlessType`) | — |
| `hook-destroy-choice` | `choosePermanentsToDestroy` | Forge AI | `SacrificeEffect` (`Destroy$ True`) | — |
| `hook-play-exile` | `playSaFromPlayEffect` | Forge AI | `PlayEffect` (`DB$ Play`) | — |
| `hook-venture` | `chooseSingleCardFace` | Forge AI | `VentureEffect` | — |
| `hook-counter-type` | `chooseCounterType` | Forge AI | `CountersPutOrRemoveEffect` / `CountersMoveEffect` | — |
| `hook-two-colors` | `chooseColors` | Forge AI | `ChooseColorEffect` (`TwoColors$ True`) | — |
| `hook-dig-multiple` | `chooseCardsForEffectMultiple` | Forge AI | `DigMultipleEffect` | — |

## The cards, and why each deck reaches its hook

- **`hook-piles`** — `Fact or Fiction`, `Gifts Ungiven`, `Sphinx of Uthuun` use `DB$ TwoPiles`, which
  calls `getController().chooseCardsPile(...)` (`TwoPilesEffect.java:111`).
- **`hook-redirect-copy`** — `Fork`/`Twincast`/`Reiterate` (`CopySpellAbilityEffect`) and
  `Bolt Bend`/`Misdirection`/`Wild Ricochet` (`ChangeTargetsEffect`).
- **`hook-repeat-modes`** — `Fiery Confluence` and `Eldrazi Confluence` are `Charm` with
  `CharmNum$ 3` and `CanRepeatModes$ True`; the seat's router bails on `allowRepeat`.
- **`hook-clash`** — `Lash Out`, `Broken Ambitions`, `Release the Ants` each clash (`ClashEffect`).
- **`hook-exert`** — six Amonkhet exert creatures and an enlist soldier split `declareAttackers`
  (owned) from `exertAttackers`/`enlistAttackers` (not).
- **`hook-flip`** — `Fiery Gambit`, `Desperate Gambit`, `Mana Clash`, `Chance Encounter` flip coins;
  `Krark's Thumb` makes the flip a choice (`chooseFlipResult`).
- **`hook-discard-unless`** — `Thirst for Knowledge` / `Thirst for Discovery` / `Compulsive Research`
  are "discard a card unless you discard an artifact" (`DiscardEffect` with `UnlessType`).
- **`hook-destroy-choice`** — `Imperial Edict`, `Wei Assassins`, `Burning of Xinye` use
  `DB$ Sacrifice` with `Destroy$ True`, which takes the `choosePermanentsToDestroy` branch of
  `SacrificeEffect:146` instead of the owned `choosePermanentsToSacrifice`.
- **`hook-play-exile`** — `Light Up the Stage` and `Outpost Siege` use `DB$ Play`; `Conspicuous Snoop`
  and `Bomat Courier` do the same from exile, all through `PlayEffect -> playSaFromPlayEffect`.
- **`hook-venture`** — the dungeon cards (`Radiant Solar`, `Triumphant Adventurer`, `Nadaar`,
  `Intrepid Outlander`, `Shortcut Seeker`, `Immovable Rod`) use `DB$ Venture` (`VentureEffect`).
- **`hook-counter-type`** — `Nesting Grounds` and `Resourceful Defense` move counters, so the counter
  type is a choice (`CountersMoveEffect:300`); `Walking Ballista` supplies real counters.
- **`hook-two-colors`** — `Seal of the Guildpact`, `Tablet of the Guilds`, `Desert Cenote` use
  `DB$ ChooseColor` with `TwoColors$ True`, i.e. `chooseColors` (two), not the single `chooseColor`
  the seat owns.
- **`hook-dig-multiple`** — `Harper Recruiter` and `Ojer Kaslem, Deepest Growth` use `DB$ DigMultiple`
  (look at the top N, split into hand and library), i.e. `chooseCardsForEffectMultiple`.

## Running one

```bash
# zero-model smoke (does the deck load?): floor mirror, ~12s
GAME_SEED=42 CONTROLLER_A=floor CONTROLLER_B=floor \
  DECK_A=$PWD/eval/decks/hook-piles.txt DECK_B=$PWD/eval/decks/hook-piles.txt \
  bash run-llm-headless.sh
# reach the hook itself (floor short-circuits plays): the random arm has budget and the full menu
GAME_SEED=42 CONTROLLER_A=random CONTROLLER_B=random \
  DECK_A=$PWD/eval/decks/hook-piles.txt DECK_B=$PWD/eval/decks/hook-piles.txt \
  bash run-llm-headless.sh
```

`eval/tournament.py` pairs every `eval/decks/*.txt` with its `.plan` blurb, so these decks join the
tournament pool automatically. If you want them out of the headline pool, move them under
`eval/decks/hooks/` and pass `--deck` explicitly (the coverage audit scores `eval/decks/*.txt` at the
top level only).

## Verified

- Card-name resolution: all 60 names per deck (13 decks) resolve in Forge's card DB.
- Load test (`hook-piles`, 2026-10-14): a zero-model floor mirror, seed 42, ran to turn 108 with no
  load error and the deck's cards in the log (`Fact or Fiction`, `Gifts Ungiven`, 26 `Island`; none
  of the default Grixis Reanimator cards). Floor short-circuits plays, so it proves the deck **loads**,
  not that the hook fired.

## Not done

- No model game has been read on these decks yet, so no defect is filed from a game — issues #126 and #127 are fixed from the inventory; #128 is still open.
- Nine hooks still lack a deck: `chooseSpellAbilitiesForEffect` (VillainousChoice / Doctor Who cards —
  buildable but off-archetype), `chooseCardsForSplice`, `chooseCardsForZoneChange`,
  `chooseSingleStaticAbility`, `chooseSingleReplacementEffect`, `confirmStaticApplication`,
  `chooseStartingHand`, `chooseSaToActivateFromOpeningHand`; and `chooseKeywordForPump` is
  **unreachable** (no card uses `PumpEffect`'s `KWChoice`).
