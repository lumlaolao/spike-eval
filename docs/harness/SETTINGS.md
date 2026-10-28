# Settings and operations

Every environment variable the launchers read, how the GUI is run and recorded, and the limits on running games side
by side. `eval/run_batch.py` sets the variables from its flags; the root scripts (`run-llm-headless.sh`,
`run-llm-spike.sh`, `play-vs-llm.sh`) read them as they are. The reason behind each default is in `ARCHITECTURE.md`.

## Environment variables

| Var | Default | What |
|---|---|---|
| `SMART_MODEL` / `FAST_MODEL`, `_A` / `_B` | none: a model seat that names no model is refused (the JVM, `run_batch.py`) | model per tier and seat: `openrouter:<model>`, `jev:<model>` (a decision model), or `claude-cli:<model>` (Claude Code in print mode, answering by structured output: a development lane, only with `CLAUDE_CLI_SEAT=1`). A bare Claude name (`haiku`, `opus`), the retired text lane, is refused |
| `CONTROLLER_A/B` | `llm` | `random` (uniform over the same menus), `floor` (fixed legal fallbacks), `ai` (Forge's AI), `human` (a person on Forge's match screen; GUI only, one seat, `play-vs-llm.sh`) |
| `CONTEXT_PROFILE` | `v4-faithful` (`run_batch.py`); `v1-compat` if unset in a raw launch | the observation shape; `v1-compat` (coaching, a turn cap) runs only as a named arm |
| `MEMORY_TOOLS` / `LOOKAHEAD` / `RULES_TOOL` | 0 | the lookups, the `simulate` preview and attack outlook, the rules lookup (needs `MEMORY_TOOLS=1` too) |
| `NATIVE_TOOLS` / `ANSWER_TOOL` | 1 | tool calls for lookups and answers; 0 is the text protocol, for measurement only |
| `ONE_TIER` | 1 | every decision on the reasoning tier; `run_batch.py --two-tier` is a measured arm |
| `MULLIGAN_HEURISTIC` / `SKIP_HEURISTICS` / `PLAN_EXECUTE` | 0 | harness shortcuts, measurement arms only |
| `GAME_SEED`, `SWAP_SEATS` | random, 0 | seeded shuffles; `1` seats identity B first (the paired flip) |
| `DECK_A/B`, `DECK_A_NAME/B_NAME`, `PLAN_A/B` | eval decks | decklists, seat labels, deck framing (`PLAN_A=""` for none) |
| `EVAL_OUT` | off | directory for the per-decision and game JSONL |
| `TRANSCRIPTS` | 0 (1 in `puzzle_solve.py` and `play-vs-llm.sh`) | with `EVAL_OUT`, keeps every HTTP request and response byte for byte (`eval/PUZZLES.md`, "Keeping a run") |
| `FORGE_CP_FILE` | `/tmp/forge_cp.txt` | Forge's classpath list: written by `setup.sh`, read by `run-llm-spike.sh`, `run-llm-headless.sh` and `play-vs-llm.sh` |
| `GAME_CAP_MS` | 3 h headless | wall-clock cap; a capped game is invalid, never a draw |
| `SMART_BUDGET` / `FAST_BUDGET` | 10000 | per-game call caps: loop guards, not decision limits (`docs/harness/MODEL-LIMITS.md`) |
| `OPENROUTER_BASE_URL`, `OPENROUTER_TIMEOUT_MS`, `OPENROUTER_IDLE_MS` | openrouter.ai, per model, 90000 | endpoint (recorded on the game line), call deadline, how long a silent connection may stall. A `jev:` decision-model seat posts to the same base's `/alpha/decisions` (its `/v1` replaced) with a 120000 ms deadline unless `OPENROUTER_TIMEOUT_MS` is set |
| `OPENROUTER_MODEL_CFG` | none | JSON override of the per-model config (`ModelCfg`: max tokens, timeouts, reasoning effort) |
| `CLAUDE_CLI_SEAT` | unset | `1` chooses a model seat on the Claude CLI (`claude-cli:`): Claude Code on the operator's own Claude subscription, for local development only and never part of a benchmark score (owner, 2026-10-24). Without it the JVM, `run_batch.py` and `puzzle_solve.py` refuse the spec; cited mode, `--panel` and `panel_score.py` refuse it always, and its games and puzzle rows are stamped `dev_lane` |
| `CLAUDE_CLI_ALLOW_API_KEY` | unset | `1` lets a `claude-cli:` seat's CLI log in with `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`; unset, those are dropped from its environment and it uses the subscription login |
| `CLAUDE_BIN` | `claude` | the Claude Code CLI a `claude-cli:` seat runs |
| `CLAUDE_BRIDGE_EFFORT`, `_SMART` / `_FAST` | unset (the CLI's default) | the CLI's `--effort` per tier, stamped `claude_effort_smart` / `_fast`; never read from Claude Code's own `CLAUDE_EFFORT`. For a `claude-cli:` seat the drivers pin it from `--claude-cli-effort` |

## The GUI and recording

A person sits in one seat with `bash play-vs-llm.sh` (`CONTROLLER_A=human`); with `CLAUDE_CLI_SEAT=1 bash play-vs-llm.sh
--model claude-cli:opus` the other seat is Claude Code on your own subscription, the development lane. `--commentary`
prints each decision as it is made (`eval/play_commentary.py`, which also reads a finished game) and `--why` its
reason; both show the seat's hidden choices and `--why` names its hand. Games against a person are never part of a
benchmark score (`ARCHITECTURE.md`, section 11).

The GUI (`bash "$FORGE_HOME/run-llm-spike.sh"`, each model seat's `SMART_MODEL` / `FAST_MODEL` named: there is no
default model) shows a spectator match of two harness seats (`play-vs-llm.sh` seats a person); `RECORD=1 RECORD_OUT=game.mp4 RECORD_FPS=8`
captures the window to H.264 in-process (no screen-recording permission; `ffmpeg` on `PATH`). Watchdog GUI runs with
`( sleep N; pkill -INT -f LlmSpikeGui ) &` (SIGINT, so the recorder finalizes the file). GUI matches are best of
three; only the headless runner, or the GUI with `AUTO_EXIT=1`, writes the game line.

## Running games side by side

- **Parallel games:** openrouter games 6 to 10 JVMs (RAM-bound, about 1 GB each, counted across all batches); floor 4
  to 8; Claude CLI specs 1. Never rebuild the checkout while a game runs. `eval/CONCURRENCY.md`.
- **A capped game** grants in-flight asks a grace window sized to the slowest model timeout before the JVM exits;
  `run_batch.py`'s backstop stays wider than cap plus grace.
- **The shadow labeller** (`SHADOW_AI`) has its own two-gate check: `bash eval/shadow_canary.sh`.
- `secrets.env` (git-ignored, mode 600, written by `setup.sh`; template `secrets.env.example`) is read by the eval
  drivers and `play-vs-llm.sh`; none of them writes the egress consent.
