# Notices and acknowledgements

## License

Copyright (C) 2026 mtg-forge-llm-agent contributors.

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public
License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later
version. It is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See [LICENSE](LICENSE) for the full text.

## Forge

This project is an add-on to [Forge](https://github.com/Card-Forge/forge), the open-source Magic: The Gathering
rules engine, Copyright (C) 2011- the Forge Team and contributors, licensed under the GNU General Public License
v3.0 or later. Forge is the engine every game here runs on; the harness only decides for a player.

- **Forge is not included in this repository.** `setup.sh` clones it from GitHub at the tested commit named in
  `FORGE_COMMIT`, and `install.sh` copies this project's files into that checkout before it is built.
- **The code under `forge-overlay/`** is compiled into Forge, extends Forge's classes, and is licensed under
  GPL-3.0-or-later like Forge itself.
- **The files under `engine-patches/`** are patches to a few Forge source files, so the work they produce is a
  modified version of Forge, distributed under GPL-3.0-or-later. Every change is marked `HARNESS PATCH` in the
  code, and each one is listed, with what it changes, why, and when, in
  [docs/harness/ENGINE-PATCHES.md](docs/harness/ENGINE-PATCHES.md).
- This project is not affiliated with or endorsed by the Forge project.

## Magic: The Gathering

mtg-forge-llm-agent is unofficial Fan Content permitted under the
[Fan Content Policy](https://company.wizards.com/en/legal/fancontentpolicy). Not approved/endorsed by Wizards.
Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC.

Magic: The Gathering, card names, card text, mana symbols and the Comprehensive Rules are trademarks or copyrights of
Wizards of the Coast LLC, a subsidiary of Hasbro, Inc. This project is not affiliated with Wizards of the Coast or
Hasbro.

- **Comprehensive Rules:** not included. `setup.sh` downloads the official text from Wizards
  (`eval/fetch_comp_rules.py`, which checks its sha256) for the optional rules-lookup tool.
- **Card names and rules text** appear in decklists, puzzles, prompts and test fixtures as needed to describe game
  states. Card images are not included, apart from the card art visible in the README's screenshot of Forge
  (`docs/images/play-vs-llm.png`); the demo viewer can fetch images from Scryfall at run time.

## Other sources

- **Scryfall** ([scryfall.com](https://scryfall.com)): card data and images fetched at run time by the demo viewer
  (`eval/demo_cards.py`), under Scryfall's API terms. Not affiliated with this project.
- **Rules questions** in `puzzles/modern/` are transcribed or lightly paraphrased from public rulings and judge
  answers: RulesGuru, Cranial Insertion, Ask a Magic Judge, Gatherer rulings (via Scryfall), Wizards release notes,
  Judge Foundry and EDHREC. Every item records its `source_name` and `source_url`; the credit and the copyright in
  each answer belong to those sources and their authors.
- **Puzzles derived from Forge's built-in puzzle files** (`eval/suites/puzzles-forge-builtin-v1.json`) are
  referenced by file name and hash, not copied; Forge ships them, and each entry names its original author and
  source (Possibility Storm and others).
- **Decklists** in `eval/decks/` are published tournament results; [eval/decks/SOURCES.md](eval/decks/SOURCES.md)
  names each event and player.
