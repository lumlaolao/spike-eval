# Hook decks: probe decks that make the AI-owned decisions reachable

`ROOT-INTERFACES.md` lists the decision hooks the Forge AI still answers (the 29 `strategic`
`ai-inherited` rows, plus the seat's three partial-override scope-outs). Most are dormant in the
16-deck archetype pool: nothing in it casts a "choose a pile" spell, so `chooseCardsPile` never runs.
These decks exist to change that — one per hook family, each built so the hook's card is actually
castable, so a game read (or a `defect-hunter`) can exercise it.

They are **probe decks, not tuned archetypes**: use them to reach a hook and read the decision, not
to compute a win rate. Each is exactly 60 cards and every card name resolves in Forge's own card DB
(the `Name:` line of its script under `forge-gui/res/cardsfolder/`).

| Deck (`eval/decks/`) | Hook it reaches | Where the decision is today | Engine effect that calls it | Issue |
|---|---|---|---|---|
| `hook-piles` | `chooseCardsPile` | Forge AI | `TwoPilesEffect` | — |
| `hook-redirect-copy` | `chooseSingleSpellForEffect`, `chooseNewTargetsFor`, `chooseTarget` | Forge AI | `CopySpellAbilityEffect`, `ChangeTargetsEffect` | — |
| `hook-repeat-modes` | `allowRepeat` modes | Forge AI (`chooseModeForAbility:3596`) | `Charm` with `CanRepeatModes$ True` | **#127** |
| `hook-clash` | `willPutCardOnTop` | Forge AI | `ClashEffect` | — |
| `hook-exert` | `exertAttackers`, `enlistAttackers` | Forge AI | `PhaseHandler` | (with the map) |
| `hook-flip` | `chooseFlipResult` | Forge AI | `FlipCoinEffect` | — |
| `hook-discard-unless` | `chooseCardsToDiscardUnlessType` | Forge AI | `DiscardEffect` (`UnlessType`) | — |

## The cards, and why each deck reaches its hook

- **`hook-piles`** — `Fact or Fiction`, `Gifts Ungiven`, `Sphinx of Uthuun` all use `DB$ TwoPiles`,
  which calls `getController().chooseCardsPile(...)` (`TwoPilesEffect.java:111`).
- **`hook-redirect-copy`** — `Fork`/`Twincast`/`Reiterate` (`CopySpellAbilityEffect`) and
  `Bolt Bend`/`Misdirection`/`Wild Ricochet` (`ChangeTargetsEffect`) both ask the seat to make the
  choice and currently get the AI's.
- **`hook-repeat-modes`** — `Fiery Confluence` and `Eldrazi Confluence` are `Charm` with
  `CharmNum$ 3` and `CanRepeatModes$ True`; the seat's router bails on `allowRepeat`.
- **`hook-clash`** — `Lash Out`, `Broken Ambitions`, `Release the Ants` each clash (`ClashEffect`).
- **`hook-exert`** — the six Amonkhet exert creatures and the enlist soldier split `declareAttackers`
  (owned) from `exertAttackers`/`enlistAttackers` (not).
- **`hook-flip`** — `Fiery Gambit`, `Desperate Gambit`, `Mana Clash`, `Chance Encounter` flip coins;
  `Krark's Thumb` makes the flip a choice (`chooseFlipResult`).
- **`hook-discard-unless`** — `Thirst for Knowledge` / `Thirst for Discovery` / `Compulsive Research`
  are "discard a card unless you discard an artifact" (`DiscardEffect` with `UnlessType`); the
  artifact lands make the "unless" live.

## Running one

```bash
# zero-model smoke (does the deck load?): floor mirror, ~12s
GAME_SEED=42 CONTROLLER_A=floor CONTROLLER_B=floor \
  DECK_A=$PWD/eval/decks/hook-piles.txt DECK_B=$PWD/eval/decks/hook-piles.txt \
  bash run-llm-headless.sh
# then a real read, per game-loop: one LLM game on the deck, read the rows for the hook's type
```

`eval/tournament.py` pairs every `eval/decks/*.txt` with its `.plan` blurb, so these decks join the
tournament pool automatically. If you want them out of the headline pool, move them under
`eval/decks/hooks/` and pass `--deck` explicitly (the coverage audit scores `eval/decks/*.txt` at the
top level only).

## Verified

- Card-name resolution: all 60 names per deck resolve in Forge's card DB (the loader's own key).
- Load test (`hook-piles`, 2026-10-14): a zero-model floor mirror, seed 42, ran to turn 108 with no
  load error and the deck's cards in the log (`Fact or Fiction`, `Gifts Ungiven`, 26 `Island`; none
  of the default Grixis Reanimator cards). Floor short-circuits plays, so it proves the deck **loads**,
  not that the hook fired.

## Not done

- No model game has been read on these decks yet, so no defect is filed from a game — the hooks'
  issues (#126, #127, #128) come from the code inventory. The floor arm cannot reach a pile/mode
  choice; use the `random` arm (zero-model, full menu) or one LLM game to read the hook.
- Six hooks remain uncovered by a deck (`choosePermanentsToDestroy`, `chooseColors`,
  `chooseCounterType`, `chooseKeywordForPump`, `chooseSingleCardFace`, `playSaFromPlayEffect`; the
  last needs a `DB$ Play` card the cardsfolder does not currently carry as a probe target).
