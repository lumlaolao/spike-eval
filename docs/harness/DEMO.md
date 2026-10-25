# The demo: watching the seat play

To explain the system behind it (the stack, one decision end to end, the numbers and where each comes from), read
`EXPLAINER.md` first.

A browser viewer for one game, recorded or running: both players' zones as the seat's prompt showed them, every
decision with what the model was asked, what it looked up, what it planned, the reasoning its provider returned,
what it answered, and the game objects it named. It reads the game's `EVAL_OUT` JSONL and nothing else, so it needs no
engine and no model, and it cannot change what the seat sees or decides. Card images come from a local cache that
`demo_cards.py prefetch` fills once from Scryfall; the server itself sends nothing out unless started with
`--fetch-images`, which sends each missing card's name to api.scryfall.com.

## macOS and Linux

Everything here runs on both; the commands are the same. Once per machine:

| | macOS | Linux |
|---|---|---|
| Python | 3.9 or newer: the system `python3` or Homebrew's (the demo is stdlib only; tests run on 3.9) | `python3` 3.9 or newer |
| JDK 17 (side by side, Forge window) | `brew install openjdk@17`; the launchers, acceptance and `demo_compare.py` find it at Homebrew's path (Apple Silicon; `demo_compare.py` also Intel), or set `JAVA_HOME` | the `javac` on `PATH`, or `JAVA_HOME` |
| Forge checkout | `$FORGE_HOME` as given, else `~/code/forge` | `$FORGE_HOME`, else `~/code/forge`, else `/root/forge` (`puzzle_format.forge_home`), or `--forge <dir>` |
| Forge built once (side by side, Forge window) | Maven and `FORGE_HOME=... bash install.sh` then a build, as in the README; after a reboot, regenerate `/tmp/forge_cp.txt` (the launcher says how) | the same |
| card images | if `demo_cards.py prefetch` prints a certificate hint, run python.org's `Install Certificates.command` once | nothing |
| browser | Chrome, Firefox or Safari 16.2+ | Chrome or Firefox |

`demo_compare.py` quotes every path it writes into its stand-in launcher; the repository and the Forge checkout
themselves should sit at paths without spaces, as acceptance's compile stage assumes. The Forge window
(`run-llm-spike.sh`) is the desktop app: on macOS it opens like any app; on a Linux server it needs a display.

## The recorded games (another machine)

The demo's recordings live on the data-only branch `demo-recordings` (never merged into `main`): the paid side by
side (`pair-4201`), the walkthrough game (`g33`), the free rehearsal pair and the card image cache, so a fresh
machine replays everything offline. Its README lists what each one is.

```bash
git fetch origin demo-recordings:demo-recordings && git worktree add ../demo-recordings demo-recordings
python3 eval/demo_server.py --game ../demo-recordings/pair-4201/now --game ../demo-recordings/pair-4201/then \
    --image-cache ../demo-recordings/cards
```

## Replay a recorded game (free, offline)

```bash
python3 eval/demo_cards.py prefetch runs/loop/g33       # once, with network: cache the card images it shows
python3 eval/demo_server.py --game runs/loop/g33        # a run directory or a game-*.jsonl
# open http://127.0.0.1:8765/   (#d=37 opens decision 37; the URL follows the cursor, so a moment can be bookmarked)
```

Keys: left and right arrows step one decision, Home and End jump, space plays at the chosen speed ("real time"
waits each decision's recorded latency). The filter chips narrow the decision list (plays, combat, tool calls, plans,
model asked, not asked, flags). Any run directory with a `game-*.jsonl` works, at any epoch: a prompt from before the
faithful observation draws with fewer fields, and a block the parser does not know stays readable in the full prompt.

## Follow a running game

```bash
mkdir -p runs/demo/live
python3 eval/demo_server.py --live runs/demo/live &
# then start any game with EVAL_OUT="$PWD/runs/demo/live" (run-llm-headless.sh, run-llm-spike.sh, or run_batch.py --out);
# the launchers change directory, so EVAL_OUT and DECK_A/DECK_B are absolute paths (run_batch.py makes them so)
```

Card images: a game in progress cannot be prefetched, so either prefetch an earlier game with the same decks
(`demo_cards.py prefetch <run>`) or start the server with `--fetch-images`, which sends each new card's name to
api.scryfall.com. The page waits for the file, follows it row by row (the JVM flushes every row), shows how long the model has been
thinking since the last row, and ends on the game row. A newer game written into the same directory (the real game
after the rehearsal) replaces it on screen. A loopback fake-model game (`eval/fake_model_server.py
--policy random --answer-tool`, no egress, no cost) is the rehearsal; a paid game needs the owner's go-ahead, a stated
cost, and `OPENROUTER_API_KEY` and `OPENROUTER_ALLOW_EGRESS=1` set by the owner in their own shell.

## Both hands: the audience feed

Run the game with `DEMO_FEED=1` (with `run_batch.py`: `--exploratory --env DEMO_FEED=1`) and the JVM writes
`feed-<seed>-<ms>.jsonl` beside the game JSONL: engine truth after every game event. The viewer then draws the
opponent's real hand at every decision, in a dashed gold box labelled "audience view (engine truth)" with what the
seat itself sees ("4 cards (hidden)"). The seat never reads the feed: its prompts are byte-identical with the feed
on and off (`DemoFeedTest`). A game recorded without the feed shows the opponent's hand face down, as the seat saw it.

## Forge's own window beside the browser

`run-llm-spike.sh` (the GUI launcher) opens Forge's match window as a spectator, which shows both hands. The
highlights (what the model is thinking about) are drawn in the browser only; Forge's window is the plain board.

```bash
python3 eval/demo_server.py --live runs/demo/live &
CONTROLLER_A=llm CONTROLLER_B=ai GAME_SEED=4201 \
  SMART_MODEL=openrouter:openai/gpt-6-luna FAST_MODEL=openrouter:openai/gpt-6-luna \
  DECK_A="$PWD/eval/decks/vintage-esper-control.txt" DECK_B="$PWD/eval/decks/pioneer-red-deck-wins.txt" \
  DECK_A_NAME="Vintage Esper Control" DECK_B_NAME="Pioneer Red Deck Wins" \
  DEMO_FEED=1 EVAL_OUT="$PWD/runs/demo/live" \
  bash "${FORGE_HOME:-$HOME/code/forge}/run-llm-spike.sh"
```

Every variable in that game line matters: without the models and decks the launcher falls back to its own defaults
(both seats on another model, decks from ~/Downloads, the seats named after them). It is a paid game (CLAUDE.md:
about $0.02 to $0.05, 20 to 90 minutes, with no budget cap in the GUI; stop it with Ctrl-C) and starts only with
`OPENROUTER_API_KEY` and `OPENROUTER_ALLOW_EGRESS=1` set by the owner in their own shell (without the key the seats
are not built). For a free rehearsal, run the seat on the loopback fake model instead:

```bash
python3 eval/fake_model_server.py --port 0 --policy first --answer-tool &    # prints LISTENING <port>: use it below
SMART_MODEL=openrouter:fake/harness-model FAST_MODEL=openrouter:fake/harness-model \
  OPENROUTER_BASE_URL=http://127.0.0.1:<port>/v1 CONTROLLER_A=llm CONTROLLER_B=ai GAME_SEED=42 \
  DECK_A="$PWD/eval/decks/burn.txt" DECK_B="$PWD/eval/decks/burn.txt" DECK_A_NAME=Burn DECK_B_NAME=BurnB \
  DEMO_FEED=1 EVAL_OUT="$PWD/runs/demo/live" bash "${FORGE_HOME:-$HOME/code/forge}/run-llm-spike.sh"
```

Put the Forge window and the browser side by side: the window is the board, the browser is the agent's log, plan
and highlights. The **log only** button (or `&log` in the URL, e.g. `#d=37&log`) hides the page's board so the log
and plan get the width. On a fresh Forge home, Forge may open a bug-report dialog at start-up (a default gauntlet
file it cannot load); click Discard once, and it may ask once whether to download a card image index; Not Now is
fine.

## Side by side: the current harness against a real older one

`eval/demo_compare.py` plays the same model, seed and decks against Forge's AI twice: on this checkout and on an
older commit of the harness on the same engine pin (refused otherwise). Each arm runs its own engine patches: build-then and build-now
compile, into each arm's isolated classes, that tree's overlay copy of every engine Java source either tree patches, or
the stock source when it has none (listed in the build's `engine-patches.txt`), so neither inherits what `install.sh`
last put in the checkout. Resource patches (`forge-overlay/forge-gui/res`) are not pinned: both arms read the checkout's.
`run` refuses a build whose pinned engine files (list or content) no longer match the trees. Both arms play the faithful profile (`--context-profile v4-faithful`). The decks come from the fixed sets in
`eval/demo/decks.json` (`demo_compare.py decks` lists them): control and storm decks for the model, aggro decks for
Forge's AI.

```bash
python3 eval/demo_compare.py build-now                       # isolated build of this checkout
python3 eval/demo_compare.py build-then --commit e1324cfd    # the 2026-10-14 harness, with the feed ported in
python3 eval/demo_compare.py run --seed 4201 --deck-a vintage-esper-control --deck-b pioneer-red-deck-wins \
    --fake --budget-usd 0.01 --out runs/demo/pair-rehearsal                  # free loopback rehearsal
python3 eval/demo_server.py --game runs/demo/pair-rehearsal/now --game runs/demo/pair-rehearsal/then   # replay it
```

build-then also copies the current price list into the older worktree (its own lacks newer models, and its
`run_batch.py` refuses `--budget-usd` without a price). To redo one arm that failed, run the same command with
`--arm then` (or `now`) and the same `--out`: that arm alone plays, with the whole budget.

To watch both arms play live rather than replay them, add `--parallel`: the two arms start at once, each in its own
JVM, and the command prints the viewer line to start first (`demo_server.py --live OUT/now --live OUT/then`). Without
it the arms play one after the other. Stopping the command stops both games.

A paid pair (`--model openrouter:openai/gpt-6-luna --budget-usd 0.40`, split between the two arms; two one-tier games at CLAUDE.md's $0.02 to $0.05 each) needs the owner's
egress consent. Two games open in watch mode: each pane is a clear board (life, library and hand counts; hand,
battlefield and lands rows; graveyard and exile as stacks of card images) with the stack and combat on one line between
the players and a scrollable log of the model's decisions under it. One play button runs both games to their end side by
side, each on its own recorded clock (from the start again if both have ended); space toggles it. **full detail** (or
`&full` in the URL) switches to the full view, where the two panes step in lockstep by turn and phase, and the **first divergence** button
jumps both to the first decision the two harnesses answer differently (`demo_events.divergence`): the same menu
answered with a different option, a different option taken where the harnesses list different menus, or different
spots (the games had already parted). Where the menus differ but both took the same option, the scan goes on and the
button's tooltip notes where the menus first differed. A pick only its notes tell apart (two abilities of one card)
must match with its notes word for word, so a reworded note reads as a divergence; one of two identical copies
matches either. Against e1324cfd they differ from the first main phase: the
current harness lists mana abilities and that commit does not. The rehearsal in the block above (after build-now and
build-then, written to `runs/demo/pair-rehearsal`) parts at decision 5, turn 1: the current harness taps Mox Ruby, an
option that commit never lists, and that commit plays Urza's Saga. Menus are compared without what
each version adds when it renders an option (`@handles`, bracketed notes); answers to equal menus by index. Two games of different seeds or decks are flagged as
"not a side by side". One pair illustrates; it measures nothing.

## Puzzle mode: the written line, then the model

A puzzle run is replayed like a game. Its pane shows the brief the seat was told (the result row's `goal_oracle`, else the .pzl's Description), who played,
how it ended (from `puzzle-results.jsonl`; `demo_puzzle.py` reads it), and behind a reveal the puzzle's intended line
(its `Rule:` note and `[solution]` steps), which the seat never sees. When the written line played cleanly (no wait,
fallback or drift in the script, and the .pzl's hash matching the run's), each decision names the solution step that
answered it and the reveal marks that step; otherwise the reveal shows the line without steps.

```bash
P=eval/puzzles/hard/gen/drain_3c09484e5e54.pzl       # an open-split generated hard puzzle (Luna missed it)
DEMO_FEED=1 python3 eval/puzzle_baselines.py line --pzl $P --out-dir runs/demo/puzzle-line          # free: no model
DEMO_FEED=1 python3 eval/puzzle_solve.py --suite eval/suites/puzzles-hard-gen-v1.json --split open \
    --ids hard-drain-3c09484e5e54 --k 1 --model openrouter:openai/gpt-6-luna --budget-usd 0.05 \
    --out runs/demo/puzzle-luna                                                                    # paid: cents
python3 eval/demo_server.py --game runs/demo/puzzle-line --game runs/demo/puzzle-luna/hard-drain-3c09484e5e54
```

Both commands use the newest acceptance build under `runs/harness-acceptance` compiled from this checkout's overlay
(its `overlay-sha.txt`), or `--build DIR`; a build of another overlay is refused unless `--allow-stale-build`.
The paid attempt
needs the owner's egress consent; `--model openrouter:fake/...` with a loopback `OPENROUTER_BASE_URL` rehearses it
for free. Side by side, **first divergence** jumps to the first decision where the model left the written line.
Copy a recording with its file times kept (a clone, `cp -p`, `rsync -t`): where a directory holds several games of one
seed, each is matched to its result row by the files' times (`demo_puzzle.result_row`).
A puzzle pair opens in full detail, not watch mode, since the puzzle panel (brief, result, reveal) lives there.
Demo puzzles come from the open split: a held-out puzzle's line is not copied into a recording (eval/PUZZLES.md).

Recorded already, on the `demo-recordings` branch: this puzzle's written line (`puzzles/drain-3c09-written-line`)
and Luna's attempt that missed it, the opponent left at 2 life (`puzzles/hard-gen-luna-check/runs/hard-drain-3c09484e5e54`),
plus a board Luna solved. `--hard-results ../demo-recordings/puzzles/hard-gen-luna-check` puts that check's PUZ
pilot on the eval page: 20 of 68 boards solved, one attempt each, pass@1 0.294 (95% CI 0.19 to 0.40), floors 0;
scored by `puzzle_score.py` and labelled a pilot, never pooled with PUZ.

## The benchmark and the gate (`/eval.html`)

```bash
python3 eval/demo_server.py --game runs/loop/g33 --acceptance /tmp/acc-<sha>    # an acceptance OUT dir
# open http://127.0.0.1:8765/eval.html  (also linked from the viewer's top bar)
```

`demo_eval.py` builds the page from files on disk: every stage of that acceptance run as its `summary.txt` reports
it (the canary stages themselves fail when a pin moves), PUZ computed by `puzzle_score.score` from the recorded Luna runs in
`eval/puzzles/rules-pilot/benchmark-v1/` (each condition's headline, 95% interval and floors, as a forest plot), and
STR-AI and STR-HELDOUT shown as not yet run, with the mirror panel's size (16 cells, 1600 games) and its cost ($32 to
$80, at CLAUDE.md's $0.02 to $0.05 a game). Every other number on the page is read from a scorer or a summary.

## A walkthrough (about ten minutes)

1. `/eval.html`: what the benchmark measures, each score next to its floor, and the gate a change must pass.
2. The viewer on `runs/loop/g33`: `#d=11` (a simulate preview before a play), `#d=37` (blockers, with the
   highlighted attackers), the plan panel, then End (the win), and the summary strip (`ai=0`, `textParses=0`, cost).
3. A game with the feed: both hands, P/T and damage chips, the opponent's real hand in the audience box.
4. The side by side: press play and watch both games, then **full detail** and **first divergence**.
5. A hard puzzle: the written line solving it (reveal the intended line), then the model's attempt beside it, and
   **first divergence** to where the model left the line.
6. Live: a running game (a loopback rehearsal, or a paid game the owner started), with the Forge window beside the
   browser, which frames the model's cards.

Fallback: everything in steps 1 to 5 is a recording and works offline once the images are prefetched.

## What is on screen, and where it comes from

| Pane | Source |
|---|---|
| board, life, counters, stack, combat, FACTS | the decision row's `prompt` (`demo_state.parse_prompt`); a row with no prompt (`tier: auto`) shows the seat's last prompt and says so |
| card images | Scryfall's printed card by exact name (`demo_cards.py`, cached in `~/.cache/mtg-forge-demo/cards`); what the game changed (P/T, tags, counters, `@handle`) is in the caption under the image; with the audience feed, P/T, damage, counter and combat chips sit on the card's corners as in Forge's own panel; a token or unknown name keeps its text tile |
| a row the model was not asked | labelled by `match_review.classify_auto`: forced (one legal choice), TAKEN or LOST (the harness decided: a defect), or other; a `tier: ai` row is Forge's AI choosing for the seat, an `ai=0` breach |
| opponent's hand | with the audience feed, the real cards in a labelled audience-only box; without it, face-down tiles, as the seat saw it |
| decision text and options, the chosen one marked | the decision row (`options`, `choice`, `picks`) and the `answer` call |
| what the model did, in order | the `context_call` rows (latency, provider, tokens, reasoning) and their `context_tool` rows (each tool's arguments and result) |
| why | the `answer` call's `why` |
| flags | `fallback`, `answer_invalid`, `first_failed_reply` on the decision row |
| plan | the seat's latest `set_plan`, else the YOUR PLAN line of its prompt; opponent profile and card interactions from the prompt |
| summary strip | the `[TALLY]` line of the console log (`ai=0`, `textParses=0`), `match_review.py` counts, `cost_report.py`, the game row's version stamps |

Highlights (`demo_highlights.py`), strongest first: **chosen** (the option or block the answer selected), **looked up**
(an `inspect` target, the options a `simulate` previewed), **plan** (names in the plan), **mentioned** (names and
`@handles` in the reasoning or `why`); a red inner frame marks what the engine put in front of the seat (combat, the
stack). Hover a card for the evidence. Only objects visible in that decision's prompt are ever highlighted: a guess in
the reasoning about the opponent's hand stays text.

## Never during a demo

Build Forge, run acceptance, or set `OPENROUTER_ALLOW_EGRESS` on the owner's behalf.
