# Settings and operations

The variables that set a game's condition and models, how the GUI is run and recorded, and operations: parallel
games, the game cap, the shadow labeller, `secrets.env`. `eval/run_batch.py` sets the variables from its flags and
stamps the condition on the game line; `play-vs-llm.sh` sets the seats, decks and transcripts itself. A Default
below is the JVM's (and `run_batch.py`'s where it says so). The two raw launchers, `run-llm-headless.sh` and
`run-llm-spike.sh`, pass the shell's values through but first default four of their own: `CONTEXT_PROFILE=v4-faithful`,
`MEMORY_TOOLS=1`, `RULES_TOOL=1` and `RULES_FILE` (the repository's `data/rules/MagicCompRules.txt`), so a raw launch
with none of them set plays with the lookups and the rules tool on. The variables the scripts read for their own setup
(`FORGE_HOME`, `JAVA_HOME`) are documented in the scripts; the recorder's are below.

## Environment variables

| Var | Default | What |
|---|---|---|
| `SMART_MODEL` / `FAST_MODEL`, `_A` / `_B` | none: a model seat that names no model is refused (the JVM, `run_batch.py`) | model per tier and seat: `openrouter:<model>`, `jev:<model>` (a decision model), or `claude-cli:<model>` (Claude Code in print mode, answering by structured output: a development lane, only with `CLAUDE_CLI_SEAT=1`). A bare Claude name (`haiku`, `opus`), the retired text lane, is refused |
| `CONTROLLER_A/B` | `llm` | `random` (uniform over the same menus), `floor` (fixed legal fallbacks), `ai` (Forge's AI), `human` (a person on Forge's match screen; GUI only, one seat, `play-vs-llm.sh`) |
| `AI_PROFILE_A` / `AI_PROFILE_B` | empty: Forge's compiled `AiProps` defaults, the opponent of every recorded STR-AI score | the shipped Forge AI profile (`Cautious`, `Default`, `Experimental`, `Reckless`; `forge-gui/res/ai/*.ai`) for that identity's `ai` / `ai-log` seat. An unknown name, a "Random" entry, or a profile on any other seat (a `human` seat included) is refused at start; a panel or a cited run is refused (`run_batch.py`) and `panel_score.py` fails a cell that carries one. Stamped `ai_profile_a` / `ai_profile_b` and never pooled (`run_batch.py --ai-profile-a/-b`; ARCHITECTURE.md, "Zero-model arms and canaries") |
| `CONTEXT_PROFILE` | `v4-faithful` (`run_batch.py` and the two raw launchers); `v1-compat` for a JVM started without it | the observation shape; `v1-compat` (coaching, a turn cap) runs only as a named arm; `v5-cache` is v4-faithful in the cache order (ARCHITECTURE.md section 4) |
| `MEMORY_TOOLS` / `LOOKAHEAD` / `RULES_TOOL` | 0 (the two raw launchers: `MEMORY_TOOLS` and `RULES_TOOL` 1) | the lookups, the `simulate` preview and attack outlook, the rules lookup (needs `MEMORY_TOOLS=1` too) |
| `NATIVE_TOOLS` / `ANSWER_TOOL` | 1 | tool calls for lookups and answers; 0 is the text protocol, for measurement only |
| `ONE_TIER` | 1 | every decision on the reasoning tier; `run_batch.py --two-tier` is a measured arm |
| `MULLIGAN_HEURISTIC` / `SKIP_HEURISTICS` / `PLAN_EXECUTE` | 0 | harness shortcuts, measurement arms only |
| `PROPOSE_ACTIONS` | 0 | the hidden-menu arm: the seat proposes each play, its attack and its blocks (`answer.action`, `declare_attackers`, `declare_blockers`) and the engine checks them (`run_batch.py` / `puzzle_solve.py --propose-actions`; ARCHITECTURE.md, "The propose condition") |
| `PROPOSE_TERSE` | 0 | with `PROPOSE_ACTIONS` only: a rules refusal says "ILLEGAL: that play is not legal now." with no rule (stamped `propose_terse`; `run_batch.py` / `puzzle_solve.py --propose-terse`; ARCHITECTURE.md, "The propose condition") |
| `PROPOSE_NOTEBOOK` | 0 | with `PROPOSE_ACTIONS` only: each play, attack and blocks decision lists the game's earlier refused proposals that the seat was told, each as it was told (REFUSED EARLIER, every one; stamped `propose_notebook`; `run_batch.py` / `puzzle_solve.py --propose-notebook`; ARCHITECTURE.md, "The propose condition") |
| `PROPOSE_AUDIT` | 0 | diagnostic, and a condition (stamped `propose_audit`; it registers handles early; `run_batch.py` pins it off, puzzles strip it, so it runs only in the keyword gauntlet): at every play decision, proposes each menu play and logs `[PROPOSE-AUDIT] MISS` for any that does not land on itself (ARCHITECTURE.md, "The checker audit") |
| `GAME_SEED`, `SWAP_SEATS` | random, 0 | seeded shuffles; `1` seats identity B first (the paired flip) |
| `DECK_A/B`, `DECK_A_NAME/B_NAME`, `PLAN_A/B` | eval decks | decklists, seat labels, deck framing (`PLAN_A=""` for none) |
| `EVAL_OUT` | off | directory for the per-decision and game JSONL |
| `TRANSCRIPTS` | 0 (1 in `puzzle_solve.py` and `play-vs-llm.sh`) | with `EVAL_OUT`, keeps every HTTP request and response byte for byte (`docs/eval/PUZZLES.md`, "Keeping a run") |
| `FORGE_CP_FILE` | `/tmp/forge_cp.txt` | Forge's classpath list: written by `setup.sh`, read by `run-llm-spike.sh`, `run-llm-headless.sh` and `play-vs-llm.sh` |
| `GAME_CAP_MS` | 3 h headless | wall-clock cap; a capped game is invalid, never a draw, and its game line says `"capped": true` |
| `SMART_BUDGET` / `FAST_BUDGET` | 10000 | per-game call caps: loop guards, not decision limits (`docs/harness/MODEL-LIMITS.md`) |
| `OPENROUTER_BASE_URL`, `OPENROUTER_TIMEOUT_MS`, `OPENROUTER_IDLE_MS` | openrouter.ai, per model, 90000 | endpoint (recorded on the game line), call deadline (stamped `openrouter_timeout_ms`, a condition: a shell value is its own experiment), how long a silent connection may stall (scrubbed like `ANSWER_SCHEMA`, below, with one exception named there). A `jev:` decision-model seat posts to the same base's `/alpha/decisions` (its `/v1` replaced) with a 120000 ms deadline unless `OPENROUTER_TIMEOUT_MS` is set |
| `OPENROUTER_MODEL_CFG` | none | JSON override of the per-model config (`ModelCfg`: max tokens, timeouts, reasoning effort) |
| `run_batch.AMBIENT_CONDITION_KEYS` | the JVM's defaults | `OPENROUTER_MODEL_CFG`, `SMART_BUDGET`, `FAST_BUDGET`, `OPENROUTER_TIMEOUT_MS`, `CLAUDE_DEADLINE_MS`, `OPENROUTER_PROVIDER_SORT`, `PROMPT_CACHE`, `PUZZLE`, `FORK_MODE`, `REPLAY_HANDOVER`: settings no `run_batch.py` flag sets that change what a seat is given or how its calls go; how a batch treats them is in ARCHITECTURE.md (`run_batch.py`) |
| `ANSWER_SCHEMA`, `ANSWER_TOOL_CHOICE`, `DECKLIST` | fixed schema, per model, 1 | measurement arms: a per-decision schema, a forced `tool_choice`, and `DECKLIST=0`'s pre-decklist prompt; these, `ONE_CHAT_TURNS` and `OPENROUTER_IDLE_MS` are in `run_batch.CONDITION_ENV_KEYS`, which `run_batch.py`, the puzzle runner, the tournament, `play-vs-llm.sh` and the RL environment scrub, so they run the default (except that the RL environment, never scored, sets `OPENROUTER_IDLE_MS` to its call deadline: `mtg_env.py`); `run-llm-headless.sh`, `run-llm-spike.sh` and a direct JVM launch pass the shell's value through, and are never a scored run. The whole list is `run_batch.CONDITION_ENV_KEYS` (`eval/run_batch.py`); the rows of this page and the next section give each name |
| `CLAUDE_CLI_SEAT` | unset | `1` chooses a model seat on the Claude CLI (`claude-cli:`): Claude Code on the operator's own Claude subscription, for local development only and never part of a benchmark score (owner, 2026-10-24). Without it the JVM, `run_batch.py` and `puzzle_solve.py` refuse the spec; cited mode, `--panel` and `panel_score.py` refuse it always, and its games and puzzle rows are stamped `dev_lane` |
| `CLAUDE_CLI_ALLOW_API_KEY` | unset | `1` lets a `claude-cli:` seat's CLI log in with `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`; unset, those are dropped from its environment and it uses the subscription login |
| `CLAUDE_BIN` | `claude` | the Claude Code CLI a `claude-cli:` seat runs |
| `CLAUDE_BRIDGE_EFFORT`, `_SMART` / `_FAST` | unset (the CLI's default) | the CLI's `--effort` per tier, stamped `claude_effort_smart` / `_fast`; never read from Claude Code's own `CLAUDE_EFFORT`. For a `claude-cli:` seat the drivers pin it from `--claude-cli-effort` |
| `CLAUDE_CLI_THINKING_TOKENS` | unset (the CLI's default, stamped "cli-default") | a `claude-cli:` seat's thinking budget, set in the child as `MAX_THINKING_TOKENS` (the child never inherits that variable); pinned by the drivers from `--claude-cli-thinking-tokens`, stamped `claude_cli_thinking_tokens_a` / `_b` |
| `CLAUDE_DEADLINE_MS` | per model (`ModelCfg`) | a CLI call's deadline; past it the process is killed and the decision has no answer. Stamped `claude_deadline_ms`. A puzzle game never takes it from the shell: `puzzle_solve.py --call-timeout-minutes` sets it and records `record.call_timeout_ms` |

## Other variables the code reads

Read by the code named; the table above holds the ones a game's condition is usually set by.

| Var | Read by | Default | What |
|---|---|---|---|
| `ONE_CHAT`, `TURN_CHAT` | `ChatSessionBridge.java` | 0 | 1: the seat's decisions are one conversation for the game (`ONE_CHAT`) or for the game turn (`TURN_CHAT`); conditions (ARCHITECTURE.md, the chat seats) |
| `NO_COACHING` | `LlmSpikeGui.java` | 0 | 1: the system prompt leaves out PRINCIPLES and the static reference (the harness's own advice); an assistance condition, stamped `no_coaching` |
| `ANSWER_TOOL_STRICT` | `AnswerTool.java` | 0 | 1: the `answer` schema is sent as strict (per OpenRouter endpoint); the harness validates every call either way |
| `TIER_FAST_TYPES` | `LlmSpikeGui.java` | empty | comma-separated decision types sent to the fast tier when two tiers run; a condition |
| `GAME_FORMAT` | `LlmSpikeGui.java` | `legacy` | the format the seat is told the decks are built for, recorded on the game line (the engine plays any deck) |
| `CTX_RECENT_EVENTS`, `CTX_RECENT_EVENTS_MAX`, `CTX_INTEL_CAP`, `CTX_YARD_CAP`, `CTX_EXILE_CAP`, `CTX_CAST_HISTORY_CAP`, `CTX_DIGEST_TURNS`, `CTX_DIGEST_PER_TURN`, `CTX_COMPACT_TURNS_PER_LINE` | `LlmSpikeGui.java` (`ContextProfile.withEnvOverrides`) | the profile's | overrides of one window or cap of the observation; each changes what the seat sees, so a run with one is its own condition |
| `OPENROUTER_API_KEY` | `LlmSpikeGui.java` | none | the provider key (`secrets.env`); never sent to a loopback endpoint and never recorded |
| `OPENROUTER_ALLOW_EGRESS` | `LlmSpikeGui.java` | unset | `1` is the user's consent to send game state to openrouter.ai; without it an `openrouter:` spec is refused. Never set by an agent (CLAUDE.md) |
| `OPENROUTER_MAX_PRICE`, `OPENROUTER_QUANTIZATIONS` | `LlmSpikeGui.java` | none | a price ceiling and a precision filter in the provider routing (`puzzle_solve.py --max-price-at-list` / `--quantizations` set them); stamped on the row, scrubbed by a batch |
| `OPENROUTER_HTTP_REFERER`, `OPENROUTER_HTTP_TITLE` | `LlmSpikeGui.java` | none | the `HTTP-Referer` and `X-Title` headers sent to OpenRouter (attribution only) |
| `ENGINE_SHA`, `OVERLAY_SHA`, `DECK_SHA`, `CHECKPOINT_SHA` | `LlmSpikeGui.java`, `LlmSpikeFork.java` | `unknown` | provenance `run_batch.py` sets and the game line records; a fork compares them with its source game's |
| `RUN_DISCIPLINE`, `SEED_BANK` | `LlmSpikeGui.java` | `unspecified`, `unregistered` | provenance `run_batch.py` sets (`discipline`, `seed_bank` on the game line) |
| `MATCH_GAMES` | `LlmSpikeGui.java` | 3 | games per GUI match (1 for a best-of-one) |
| `RULES_FILE` | `LlmSpikeGui.java` | found from the working directory | the Comprehensive Rules text the rules tool reads |
| `DEMO_FEED` | `DemoFeed.java` | 0 | 1: the seats write the demo's live feed beside the game log (`docs/harness/DEMO.md`) |
| `PUZZLE_DIRS` | `LlmSpikePuzzle.java` | Forge's puzzle folder | the folders (path-separated) where `PUZZLE` names are looked up |
| `PZL_SEEDS` | `LlmSpikePuzzle.java` | one seed | the rollout seeds (`1..20`, `3,9`) many rollouts of a puzzle run in one JVM |
| `PZL_TAPE`, `PZL_TAPE_ALL` | `LlmSpikePuzzle.java` | none, 0 | the draw tape a script arm replays; 1 keeps every rollout's tape, not only a single run's |
| `PZL_SEARCH_BUDGET`, `PZL_SEARCH_LINES`, `PZL_SEARCH_MAX_DRAWS`, `PZL_UNIQUE_BUDGET` | `LlmSpikePuzzle.java`, `PuzzleArms.java` | 2000, 32, 3000, the search budget | the search arm's replays in all, lines tried per choice point, draws per random completion, and the replays spent looking for a second solution |
| `PZL_PROBE`, `PZL_PROBE_COMPLETION_SEED` | `LlmSpikePuzzle.java` | none, 0 | the misreading probe: a line prefix to replay and the seed of its completion |
| `PZL_SUITE_SHA256` | `LlmSpikePuzzle.java` | none | the frozen suite an attempt ran for, recorded on its row (`puzzle_score.py` refuses to pool versions) |
| `PZL_ALLOW_UNENFORCED_GOAL` | `LlmSpikePuzzle.java` | 0 | 1: run a puzzle whose goal Forge does not enforce, with a warning (otherwise refused) |
| `REPLAY_FORCE` | `LlmSpikePuzzle.java` | empty | with `REPLAY_HANDOVER`: the step search's forced first steps |
| `FORK_SOURCE`, `FORK_DECISION_INDEX` | `LlmSpikeFork.java` | none | the recorded game a fork replays and the decision it forks at |
| `FORK_ROLLOUT_N`, `FORK_ROLLOUT_SEED_BASE`, `FORK_ROLLOUT_OPTION`, `FORK_ROLLOUT_J_OFFSET`, `FORK_ROLLOUT_DETERMINIZE`, `FORK_ROLLOUT_OUT` | `LlmSpikeFork.java` | 16, the game seed, `all`, 0, `library`, `EVAL_OUT` | rollouts per option, their seeds, which option, the key offset, whether the library is redrawn (`library` or `none`; anything else is refused), and where the rollouts go |
| `JAVA` | `eval/puzzles/puzzle_baselines.py`, `eval/rl/mtg_env.py` | `java` | the JVM the puzzle runner and the RL environment start |
| `HARNESS_ENV_SEED` | `eval/harness_env/harness_env_generate.py`, `harness_env_episode.py` | none | the secret seed of the held-out task set; required for the held-out split |
| `HARNESS_DEPS_FILE` | `eval/harness_env/harness_env_task_grade.py` | found in the repository | the dependency classpath file the grader compiles against |
| `GH_REPO` | `eval/harness_env/harness_env_tasks.py` | this repository | the GitHub repository the task miner reads issues from |
| `IMPROVE_GAMES`, `IMPROVE_SEAT_A`, `IMPROVE_SEAT_B`, `IMPROVE_CONTROLLER`, `IMPROVE_SEED`, `IMPROVE_DECK`, `IMPROVE_BUDGET_USD` | `eval/improve_loop.py` | 2, its default seat, `floor`, 1, its deck, none | the defaults of the matching `improve_loop.py` flags |

## The GUI and recording

A person sits in one seat with `bash play-vs-llm.sh` (README, "Play against the model"; the design, `ARCHITECTURE.md`,
section 11). With `CLAUDE_CLI_SEAT=1 bash play-vs-llm.sh --model claude-cli:opus` the other seat is Claude Code on your
own subscription, the development lane. `--commentary` (`eval/play_commentary.py`, which also reads a finished game)
and `--why` show the seat's hidden choices; `--why` names its hand.

The GUI (`bash "$FORGE_HOME/run-llm-spike.sh"`, each model seat's `SMART_MODEL` / `FAST_MODEL` named: there is no
default model) shows a spectator match of two harness seats (`play-vs-llm.sh` seats a person); `RECORD=1 RECORD_OUT=game.mp4 RECORD_FPS=8`
captures the window to H.264 in-process (no screen-recording permission; `ffmpeg` on `PATH`). Watchdog GUI runs with
`( sleep N; pkill -INT -f LlmSpikeGui ) &` (SIGINT, so the recorder finalizes the file). GUI matches are best of
three; only the headless runner, or the GUI with `AUTO_EXIT=1`, writes the game line.

## Operations

- **Parallel games:** openrouter games 6 to 10 JVMs (RAM-bound, about 1 GB each, counted across all batches); floor 4
  to 8; Claude CLI specs 1. Never rebuild the checkout while a game runs. `docs/eval/CONCURRENCY.md`.
- **A capped game** grants in-flight asks a grace window sized to the slowest model timeout before the JVM exits;
  `run_batch.py`'s backstop stays wider than cap plus grace.
- **The shadow labeller** (`SHADOW_AI`, `run_batch.py --shadow-ai`) has its own two-gate check: `bash eval/shadow_canary.sh`.
  It is a condition, stamped `shadow_ai` on the game line: an AI timeout during its side evaluation weakens the
  game's isolation (`[SHADOW] ... WEAKENED`), so a shadow-labelled game never pools with a plain one.
- `secrets.env` (git-ignored, mode 600, written by `setup.sh`; template `secrets.env.example`) is read by the eval
  drivers and `play-vs-llm.sh`; none of them writes the egress consent.
