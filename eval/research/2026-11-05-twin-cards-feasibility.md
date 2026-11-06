# Twin and invented cards: feasibility (read-only study, 2026-11-05)

Sources: repo at `origin/main` (read with `git show`), Forge checkout (`$FORGE_HOME`) (pinned). Nothing was built or run
except read-only Python over existing `runs/` files (`cost_report.py`, result-row counts).

## 1. Loading a new card script

**Discovery is by folder walk, not by edition.**
- `CardStorageReader.collectCardFiles` recurses through every subfolder of `res/cardsfolder` and takes every `*.txt`
  (forge-core/.../CardStorageReader.java:386-407). It skips only dot-folders and `upcoming/` on non-dev builds (:397, :401).
- A `cardsfolder.zip`, if present, is read as well (:97-105). None exists in the Forge checkout, so loose files are used.
- So a script in `res/cardsfolder/zz_twins/vexing_spark.txt` is loaded. With `LOAD_CARD_SCRIPTS_LAZILY=false`, which
  the harness sets (LlmSpikeHeadless.java:65-69), it is loaded eagerly.
- `StaticData` puts every rule it reads into `regularRules` (forge-core/.../StaticData.java:114-128). It then calls
  `CardDb.initialize(..., enableUnknownCards)` (:139).

**A card with no set still gets a printing.**
- `CardDb.addUnassignedCardPrints` (forge-core/.../card/CardDb.java:625-641) handles any rule with no edition entry:
  - when `enableUnknownCards` is on, it adds the card to the `UNKNOWN` set with one stderr line ("was not assigned to
    any set") (:633-635);
  - a card from the user custom folder goes to the `USER` set instead (:638-640).
- `enableUnknownCards` is `FPref.UI_LOAD_UNKNOWN_CARDS`, which defaults to true (ForgePreferences.java:59; FModel.java:88).
  The harness does not override it (LlmSpikeHeadless.java:65-69).
- **So no edition file is strictly required.** The fallback does depend on a preference, though: a profile whose
  `forge.preferences` sets it false would drop every twin.

**The robust form is an edition file.**
- Add `forge-overlay/forge-gui/res/editions/Spike Twins.txt`, with this `[metadata]`:
  - `Code=ZTW` (any unused code);
  - `Date=2026-01-01` (in the past; with a future date and a CORE/EXPANSION type it becomes the "upcoming set",
    CardDb.java:614-620);
  - `Name=Spike Twins`;
  - `Type=Custom_Set` (parsed by `Type.valueOf(type.toUpperCase())`, CardEdition.java:814-817; enum :55-71).
- Then a `[cards]` section with lines like `1 S Vexing Spark`.
- `CUSTOM_SET` also makes `ImageFetcher` skip any art download (forge-gui/.../ImageFetcher.java:329).
- An alternative is Forge's per-user custom folder (`USER_CUSTOM_CARDS_DIR`, FModel.java:223). It lives in the user
  profile, outside the repository and the pin, so it is not recommended for a benchmark.

**A puzzle names a card by name only.**
- `GameState` resolves each `.pzl` zone entry with `getCommonCards().getCard(name, setCode, artID)`, where
  `|Set:XXX` is optional (forge-game/.../GameState.java:1305).
- So `p0hand=Vexing Spark;Shock` works.
- **Trap:** an unknown name is printed to stderr and silently skipped (`continue`, :1306-1308). A twin that was not
  installed gives a different board, and nothing fails. The twin pipeline must check for this; the gap is worth
  fixing in `LlmSpikePuzzle` for every puzzle.

**Installing.**
- `install.sh:16` runs `cp -Rv "$HERE/forge-overlay/." "$FORGE_HOME/"`. Overlay files under
  `forge-overlay/forge-gui/res/cardsfolder/...` and `.../res/editions/...` would therefore be copied into the checkout.
- Today the overlay has no `forge-gui/res` files, only `forge-gui-desktop/src/**` and the two launchers.
- The runs read `res` from the checkout: the launchers and acceptance `cd "$FORGE/forge-gui-desktop"`
  (harness_acceptance.sh:312, :393, :1152; run-llm-headless.sh:3, :17). So a twin is live only after `install.sh`.
- Two gaps:
  - `install.sh` never deletes, so a removed twin stays in the checkout.
  - `benchmark_version.overlay_files` hashes only `forge-overlay/*/src/main/**` (eval/benchmark_version.py:83-89).
    Card scripts and editions would sit outside `overlay_sha`, and outside the build check `puzzle_baselines.py`
    applies.

**A rule question.**
- CLAUDE.md says "never a whole Forge file in the repository". A twin's script is mostly a copy of a Forge script.
- Recommendation for rename and shift twins: commit a manifest (original name, twin name, parameter edits), and have
  `install.sh` generate the scripts from the pinned checkout's originals, as `engine_patches.py` does. They would be
  listed in ENGINE-PATCHES.md or a sibling.
- Hand-written invented cards are new files and can be committed as scripts.

## 2. What the seat sees, and possible leaks

**On-board cards.**
- `inspect @h` prints `describe(c)`, the type line, the cost and `c.getOracleText()` (SeatObservation.java:1460-1463).
- Menus and logs use the in-game name.
- Oracle text is the script's literal `Oracle:` line (CardRules.java:820, `face.setOracleText(value)`), and it usually
  contains the card's own name: `Oracle:...{T}: Thermo-Alchemist deals 1 damage...`.
  - In a survey of the 133 distinct cards used across the gen, gen2 and gen3 boards, almost every script names its
    card exactly twice: `Name:` and `Oracle:`.
  - Ability descriptions use `CARDNAME`, which Forge substitutes with the current name.
- **The twin must rewrite `Name:`, `Oracle:` and any literal self-reference.** Then the seat sees the new name and the
  same rules text.

**Database lookups.**
- `inspect "<name>"` and the opponent card-text lines go through `getCommonCards().getCard(name)` and print
  `CardRules.getOracleText()` (SeatObservation.java:295-305, :1471-1490). They work for a twin.
- They also still answer for the original name. A model that recognises the text could `inspect "Lightning Bolt"`.
  That is reading plus a guess, not a leak; the tool-use audit can count such lookups.

**Puzzle text.**
- The seat's brief for an `[acceptance]` puzzle is its `Description:` (LlmSpikePuzzle.java:159-168), which names
  cards.
- The Puzzle Goal card's oracle text is replaced by the brief (:196-200).
- `Name:` and `Rule:` (e.g. "Goblin Guide is a trap", "City of Brass's damage is a triggered ability") are not shown.
  The transform should still rewrite them.
- `[acceptance]` lines name cards (`count p1 Graveyard "Blood Moon" = 1`, `tokens ... "Marit Lage"`; 436 `count` lines
  in the repository's puzzles).
- So do `NamedCard:` (rules pilot), `[solution]` lines (`cast Lava Spike`, `activate X ... Add {U}`) and the
  `# search:`/`# trap:` comments.

**Name-keyed logic.**
- In the overlay: no card-name constants decide anything. The only match is `planStepIndex`, which compares exact
  in-game names (LlmSpikeGui.java:9068-9090).
- In the engine:
  - `named` properties compare names (CardProperty.java:63-69). Rite of Flame's script has `Card.namedRite of Flame`
    (lines 3, 5, 6), so that string must be rewritten too.
  - `forge-ai` and `forge-game` hard-code about 55 names: Dark Depths, Living End, Pithing Needle, Squee, Puzzle Goal
    and others. None is among the 133 generated-suite cards; the generator must refuse to twin any card on that list.
  - The AI's same-name comparisons (`CardPredicates.nameEquals(other.getName())`) are kept by a consistent bijective
    rename.
  - `AILogic$` and `AIPreference` live in the script and are copied unchanged, so Forge's AI sees the same card.
- **Ordering.** Hand sort by name (Zone.java:58-64) applies only when `isOrderedZone()` is true. `PlayerControllerLLM`
  extends `PlayerControllerAi` (LlmSpikeGui.java:855), whose default is false (PlayerController.java:394; the
  override is in PlayerControllerHuman only). Menus therefore keep `.pzl` and card-id order, and a rename should not
  reorder options. The first experiment checks this.
- No rulings, Scryfall or art data is read by the seat. The `rules_lookup` corpus (MagicCompRules) contains printed
  card names in examples, which could only help the original, not the twin. There are no images headless, and a
  CUSTOM_SET is never fetched. Localisation files are keyed by name, but the harness forces en-US.

## 3. Puzzle pipeline changes for a twin suite

- **gen-v1** (`eval/suites/puzzles-hard-gen-v1.json`): 116 active boards (open 84, held out 32). Every floor is 0
  (random 0/20; pass, first, greedy and AI 0/4).
  - The proof is the `written` arm: the `[solution]` is replayed against Forge's AI (puzzle_baselines.py:477-487),
    with `replay_log_sha256`. That hash changes under a rename because the log carries names.
  - **PuzzleSearch proofs (`arm: search`) are on the rules suites**, e.g. `puzzles-rules-sample-v1`: 29 active,
    `"line": "Solver:3:2 Solver:3:1 ..."`, index-based. They replay identically only when menu order is unchanged,
    so prove them again regardless.
- Twin generator (`eval/puzzles/puzzle_twin.py`) inputs and outputs:
  - Input: a frozen suite, a name map (or an automatic mapping from a word list with a collision check against every
    `Name:` in the pinned cardsfolder), and a kind (`rename` or `shift`).
  - Output: twin `.pzl` files in `eval/puzzles/twins/<suite>/`, with every name rewritten in each zone line, the
    `Description`, `Rule`, `Name`, `[solution]`, `[acceptance]` quoted names, `NamedCard:` and `# search`/`# trap`
    comments; plus the twin card manifest.
  - Refusals:
    - a card on Forge's hard-coded name list;
    - a card whose script names another card;
    - a token-name dependency (Marit Lage);
    - a twin name that is a prefix of another option label on the same board, because ScriptBridge matches "label
      starts with";
    - a name that collides with a real card.
- `puzzle_format.py` needs:
  - a `twin_of` field and a source kind (`SOURCE_KINDS`, :54);
  - the split taken from the original: today `split` must equal `split_for(id)` (:70-75, enforced at :368). Without
    this, a twin of a held-out puzzle could land in the open split, and the reverse.
- `puzzle_score.py --paired-with` refuses a different `suite_sha256` or a different id set (:466-482). It needs a
  `--twin-map` mode that pairs `twin_of` to `id` across two suites.
- Suite metadata:
  - a new frozen suite `puzzles-hard-gen-twin-v1.json` (or `rules-twin-v1`), with `twin_of`, `twin_kind`,
    `twin_cards_sha` (hash of the installed twin scripts and edition), the original's `suite_sha256` and a canary;
  - and either `overlay_files` extended to `forge-gui/res/**`, or a separate stamp in `versions.py` and VERSIONS.md.
- Proof re-run for each twin:
  - `puzzle_baselines.py line` (written) or `prove` (search) on the twin `.pzl`;
  - then the zero-model floors (`run --baselines-only`);
  - then a **name-mapped log comparison**: the twin's game log, with names mapped back, must equal the original's log
    at the same seed. This is stronger than "floors match", which is trivial on gen-v1 because every floor is 0;
  - plus a check that no `non-existent card` line was printed.

## 4. Minimal plan

**Files to add.**
1. `eval/puzzles/puzzle_twin.py`, the rename transform above, and `eval/tests/test_puzzle_twin.py`. The test is pure
   Python and covers:
   - round trip;
   - every name position rewritten, with no original name left anywhere in the twin `.pzl` (grep);
   - collision and prefix refusals;
   - split inherited.
2. `eval/twins/manifest.json` (original → twin, edits) and an `install.sh` step (`eval/twin_cards.py apply --forge`).
   The step writes `forge-gui/res/cardsfolder/zz_twins/*.txt` from the pinned originals, rewriting `Name:`,
   `Oracle:`, the literal name and `named<Name>`, plus `forge-gui/res/editions/Spike Twins.txt`.
   - Alternative: commit the scripts under `forge-overlay/forge-gui/res/...`, but see the rule question in section 1.
3. A Java test (`TwinCardsTest`, `PuzzleSetupTest` style, using its `gameOf`):
   - each twin resolves by name;
   - its oracle text equals the original's with the name substituted;
   - each twin board puts down exactly the cards its `.pzl` lists.
4. `puzzle_format.py` (`twin_of`, inherited split) and `puzzle_score.py` (`--twin-map`).
5. ARCHITECTURE.md, ENGINE-PATCHES.md (or a sibling), the PUZZLES.md section and a version stamp.

**First free experiment (zero-model; run only when no paid games are running).**
- Take 10 to 12 open gen-v1 boards across ping, splice, drain, elf and storm, plus the 29 active rules-sample puzzles.
- `install.sh`, then acceptance `preflight classpath compile`.
- For each twin: `puzzle_baselines.py line` (gen) or `prove --budget 600` (rules), and `run --baselines-only`.
- Pass if:
  - every line solves;
  - every rules twin is `proven`;
  - the floors are unchanged within their intervals (identical where seeded);
  - the name-mapped game logs are identical;
  - no `non-existent card` appears in stderr.
- What would make it fail: a hidden name dependency, a menu-order change, or an uninstalled twin. Include one
  deliberately uninstalled twin as a negative control.

**Paid comparison (GPT-6 Luna, with `--budget-usd`).**
- Measured unit costs:
  - rules puzzles: `runs/puzzles/bench-v1-e67/on` is 145 Luna attempts for $0.20, about $0.0014 each;
  - hard generated boards: `gen3-luna` is 44 attempts for $1.01, about $0.023 each (WORK-PLAN.md:47).
- Arm A, rules-sample. 29 originals plus 29 twins at k=5, 290 attempts, **about $0.40 (cap $0.60)**.
  - Luna solved these at a per-puzzle mean of about 0.85 (bench-v1-e67/on: 22 of 29 at 1.0). There is room for a
    drop, and these puzzles hinge on card rulings, where recall matters most.
- Arm B, gen-v1. The 84 open boards and their twins at k=1, 168 attempts, **about $3.90**.
  - Luna solves only about 10% of generated boards (gen2: 15/156; gen3: 3/44), so the paired delta is near the floor
    and needs this size.
  - Start with a block of 12 pairs (about $0.55) and read it first.
- A dev-lane pilot on `claude-cli:` (opt-in, never scored) can come before either arm.
- Report the paired delta with its bootstrap interval, and Forge's AI on originals and twins as the control (its rate
  must not move).

**Main risks.**
- Silent card drop when a twin is not installed (GameState.java:1306-1308).
- Twin scripts sit outside `overlay_sha`, and `install.sh` never deletes.
- Name-keyed engine logic. It is absent for the 133 generated-suite cards but present for about 55 hard-coded names
  and for `Card.named...` scripts. The generator must refuse such cards or rewrite them.
- Description, acceptance, NamedCard and solution text are all name-bearing. One missed rewrite leaks the original
  name or breaks the line.
- Label-prefix ambiguity in written lines.
- Leaving `UNKNOWN`-set loading to a preference, if no edition file is shipped.
- The model may still recognise the rules text: rename twins measure name recall, not text recall. Parameter-shift
  and invented cards are what make a puzzle contamination-proof. They need per-family solver changes (the name-keyed
  number tables `RITUALS`/`BURN`/`FREE`, puzzle_gen.py:66-71) and new gates.
- Floor effects on the hard suites.
- RulesGuru and Forge-builtin positions are third-party. Their twins should be generated into `runs/`, not committed,
  unless the licence allows.

## Verdict

Feasible and cheap.
- Forge loads any `.txt` under `res/cardsfolder` recursively. It gives a set-less card an `UNKNOWN` printing by
  default (a `Type=Custom_Set` edition file makes this independent of the preference). A `.pzl` creates cards purely
  by name. `install.sh`'s `cp -R` already delivers overlay `res/` files.
- The seat reads only the script's name and `Oracle:` text, so a twin with those two lines (and any `named` reference)
  rewritten is seen exactly as intended.
- The harness has no name-keyed shortcuts, and the 133 generated-suite cards hit none of the engine's hard-coded
  names (only Rite of Flame needs its self-reference rewritten).
- The work is mostly Python plumbing:
  - a twin transform that rewrites every name-bearing field of the `.pzl`;
  - `twin_of` with the split inherited in `puzzle_format.py`;
  - a `--twin-map` pairing in `puzzle_score.py`;
  - version coverage for the scripts;
  - a check that turns Forge's silent "non-existent card" skip into a failure.
- The free proof replay plus a name-mapped log comparison is a strong validity gate.
- The paid recall-vs-reading test is about $0.40 on the 29 rules-sample puzzles at k=5, where Luna's solve rate
  (about 0.85) leaves room for a measurable drop. That is a better first target than the hard generated suites, where
  Luna's solve rate of about 10% makes the delta hard to see.
