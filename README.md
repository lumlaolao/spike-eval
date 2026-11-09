# spike-eval

A benchmark for how well language models play Magic: The Gathering.

The model plays full games inside [Forge](https://github.com/Card-Forge/forge), the open-source rules engine. Every
decision the engine asks of a player goes to the model: mulligans, land drops, spells, targets, attacks, blocks,
triggers. The engine checks every option before it is offered, so the model cannot make an illegal move, and Forge's
own AI never decides for it.

![One decision in the replay viewer: the seat's board, its plan, two simulate previews and its answer](docs/images/seat-decision.webp)

This is a research project and still changing. Scores compare only within one engine version (`FORGE_COMMIT`) and one
harness version (`docs/harness/VERSIONS.md`).

## Quick start: play against the model

You need Java 17+, Maven 3.8.1+, git and Python 3, on macOS or Linux (WSL2 on Windows), and an
[OpenRouter](https://openrouter.ai) key.

```bash
git clone https://github.com/lumlaolao/spike-eval.git && cd spike-eval
bash setup.sh                                         # fetches and builds Forge, asks for your key
bash play-vs-llm.sh                                   # your Burn against the model on Mardu Energy
bash play-vs-llm.sh --deck elves --vs ur-delver       # any eval/decks/<name>.txt, or a path to a decklist
bash play-vs-llm.sh --commentary --why                # shows its decisions and reasons as it plays
```

`setup.sh` clones Forge at the pinned commit into `~/code/forge` (about 1 GB; `--forge-dir` for elsewhere), adds the
harness and builds it. The first run takes about 10 minutes. It then asks for your key and whether games may be sent
to openrouter.ai, and writes both to `secrets.env`, which git ignores. Run it again after a `git pull`.

`play-vs-llm.sh` opens Forge's window, so it needs a display. A game costs about $0.02 to $0.05 with the default
model, and the model may think for minutes on a hard decision. Games against a person are never scored.

## Record a game

```bash
python3 eval/run_batch.py --seeds 4101 --exploratory --budget-usd 0.40 --deck eval/decks/burn.txt \
  --controller-a llm --controller-b ai \
  --smart-a openrouter:openai/gpt-6-luna --fast-a openrouter:openai/gpt-6-luna \
  --memory-tools --lookahead --out runs/loop/g01
python3 eval/match_review.py runs/loop/g01        # what the harness decided, lost or skipped, and where the time went
```

An `openrouter:` model sends the game state to openrouter.ai. Nothing is sent unless `OPENROUTER_API_KEY` and your
own consent, `OPENROUTER_ALLOW_EGRESS=1`, are set. A game takes 20 to 90 minutes. `run_batch.py` stops at
`--budget-usd`, and `eval/cost_report.py` prices any run. Every setting is in `docs/harness/SETTINGS.md`.

## The benchmark

| Score | What it measures |
|---|---|
| STR-AI | win rate against Forge's AI on a frozen set of deck matchups |
| STR-HELDOUT | the same, on seeds kept out of every training corpus |
| PUZ | frozen puzzle suites, judged by Forge's puzzle mode |

Each score is reported with its confidence interval and the trivial baseline it has to beat. The design is in
`docs/eval/EVAL-DESIGN.md`, `docs/eval/SCORING.md` and `docs/eval/PUZZLES.md`.

## How it works

- **The seat.** `PlayerControllerLLM` (`forge-overlay/.../spike/LlmSpikeGui.java`) implements Forge's player
  interface: 89 decision hooks and 44 ways to play a card, each with a test.
- **What the model sees.** The game state from the engine, Forge's game log, the opponent's cards it has seen, and
  its own notes (`SeatObservation.java`).
- **How it answers.** One `answer` function call per decision. It can also call tools to read a card, search its
  archive of the game, or preview what an option would do.
- **The engine.** Forge is not in this repository. `install.sh` copies `forge-overlay/` into a Forge checkout and
  applies the small diffs in `engine-patches/`, each listed in `docs/harness/ENGINE-PATCHES.md`.

`docs/harness/ARCHITECTURE.md` covers every part and why it is built that way.

## Build and test by hand

`setup.sh` does all of this for you. The Python code uses only the standard library.

```bash
export FORGE_HOME=~/code/forge
git clone https://github.com/Card-Forge/forge.git "$FORGE_HOME" && git -C "$FORGE_HOME" checkout $(cat FORGE_COMMIT)
bash install.sh                                       # copy the overlay in, apply engine-patches/
(cd "$FORGE_HOME" && mvn -q -pl forge-gui-desktop -am install -DskipTests)    # checkstyle runs here; -o once built
(cd "$FORGE_HOME" && mvn -q -pl forge-gui-desktop -am dependency:build-classpath -Dmdep.outputFile=${FORGE_CP_FILE:-/tmp/forge_cp.txt})
bash eval/harness_acceptance.sh                       # compile, every test, canaries, fake-model games
bash eval/keyword_gauntlet.sh                         # 29 free games over 564 cards
```

Acceptance needs no API key and costs nothing: its games use fixed players or a fake model on your own machine.
It builds into `runs/harness-acceptance/<ts>/` and never touches the checkout.

## Where things are

| Path | What |
|---|---|
| `forge-overlay/` | the harness: Java compiled into Forge, with its tests |
| `engine-patches/` | diffs to a few Forge files |
| `eval/` | launchers, scoring, decks, puzzles and the Python tests |
| `docs/harness/` | architecture, settings, lessons, version stamps ([index](docs/harness/INDEX.md)) |
| `docs/eval/` | the benchmark's design, scoring and puzzle format |
| `CLAUDE.md`, `.claude/` | instructions for coding agents working in this repository |

The documents cite run directories (`runs/loop/g26`, `runs/c3/...`) for their numbers. Those recorded games are not
part of this repository; `runs/` is where your own runs land.

## Contributing

Changes land through pull requests; [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/STANDARDS.md](docs/STANDARDS.md)
say how. To report a security problem, see [SECURITY.md](SECURITY.md).

## License

GPL-3.0-or-later ([LICENSE](LICENSE)), like Forge, into which the harness is compiled. [NOTICE.md](NOTICE.md) says
what is and is not in this repository and credits every source. This project is not affiliated with the Forge project.

Unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards. Portions of the
materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC.
