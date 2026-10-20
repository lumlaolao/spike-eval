# mtg-forge-llm-agent

Language models play full games of Magic: The Gathering inside [Forge](https://github.com/Card-Forge/forge), the
open-source rules engine, and the games are scored as a benchmark. This repository holds only the overlay that drops
into a Forge checkout; it does not redistribute Forge (GPL).

- **How a model plays:** `PlayerControllerLLM` (in `forge-overlay/.../spike/LlmSpikeGui.java`) answers every decision
  the engine asks a player: 89 decision hooks and 44 ways to play a card, each proven by a test. The engine checks
  every option, so an illegal move is impossible. The model answers by calling one `answer` function and may look
  things up with native tool calls (card text, its archive, a preview of an option). Forge's own AI never decides for
  the seat (`[TALLY] ... ai=0`).
- **What the model sees:** the faithful observation (`SeatObservation.java`): the state from the engine, Forge's game
  log, the opponent's cards it has seen, and its own notes, each block labelled by its source.
- **The benchmark:** STR-AI (win rate against Forge's AI on frozen panels), STR-HELDOUT (the same on held-out seeds),
  PUZ (Forge's puzzles). `eval/EVAL-DESIGN.md`, `eval/SCORING.md`, `eval/PUZZLES.md`.
- **Status (2026-10-17):** the harness is complete by its own gates (`docs/harness/ACCEPTANCE.md`); real Luna games are
  being played and read one at a time (`docs/harness/GAME-LOOP-LOG.md`); the paid benchmark blocks wait on the owner's
  scoring decisions (`docs/harness/WORK-PLAN.md`).

## Build and check

Requires JDK 17, Maven, and Python 3 (stdlib only).

```bash
git clone https://github.com/Card-Forge/forge.git /root/forge && git -C /root/forge checkout $(cat FORGE_COMMIT)
FORGE_HOME=/root/forge bash install.sh                                   # copy the overlay in
(cd /root/forge && mvn -q -pl forge-gui-desktop -am install -o -DskipTests) # build (checkstyle runs here)
FORGE_HOME=/root/forge bash eval/harness_acceptance.sh                   # compile, every test, canaries, fake-model games
bash eval/keyword_gauntlet.sh                                            # 28 free games over 553 cards
```

`eval/harness_acceptance.sh` never touches the checkout (it builds into `runs/harness-acceptance/<ts>/`). Its
zero-model canaries are bit-reproducible: the seed-42 floor mirror wins A in 108 turns, the random mirror A in 59.

## Play a game

```bash
python3 eval/run_batch.py --seeds 4101 --exploratory --budget-usd 0.40 --deck eval/decks/burn.txt \
  --controller-a llm --controller-b ai \
  --smart-a openrouter:openai/gpt-6-luna --fast-a openrouter:openai/gpt-6-luna \
  --memory-tools --lookahead --out runs/loop/g01
python3 eval/match_review.py runs/loop/g01        # what the harness decided, lost, skipped; where the time went
```

An `openrouter:` model sends the game state to openrouter.ai: set `OPENROUTER_API_KEY` and, as your own consent,
`OPENROUTER_ALLOW_EGRESS=1` (the factory refuses without it). A Luna game costs $0.02 to $0.05 and takes 20 to 90
minutes. `run_batch.py` pins the condition, stamps provenance, resumes, retries a crashed game once, and stops at
`--budget-usd`. `eval/cost_report.py` prices any run.

The GUI (`bash /root/forge/run-llm-spike.sh`) shows a spectator match; `RECORD=1 RECORD_OUT=game.mp4 RECORD_FPS=8`
captures the window to H.264 in-process (no screen-recording permission; `ffmpeg` on `PATH`). Watchdog GUI runs with
`( sleep N; pkill -INT -f LlmSpikeGui ) &` (SIGINT, so the recorder finalizes the file). GUI matches are best of
three; only the headless runner writes the game line.

## Settings (env vars; `run_batch.py` sets these from its flags)

| Var | Default | What |
|---|---|---|
| `SMART_MODEL` / `FAST_MODEL`, `_A` / `_B` | none in `run_batch.py` (it refuses an unnamed llm seat) | model per tier and seat: `openrouter:<model>`, or a Claude CLI name (`haiku`, `opus`) |
| `CONTROLLER_A/B` | `llm` | `random` (uniform over the same menus), `floor` (fixed legal fallbacks), `ai` (Forge's AI) |
| `CONTEXT_PROFILE` | `v4-faithful` (`run_batch.py`); `v1-compat` if unset in a raw launch | the observation shape; `v1-compat` (coaching, a turn cap) runs only as a named arm |
| `MEMORY_TOOLS` / `LOOKAHEAD` / `RULES_TOOL` | 0 | the lookups, the `simulate` preview and attack outlook, the rules lookup (needs `MEMORY_TOOLS=1` too) |
| `NATIVE_TOOLS` / `ANSWER_TOOL` | 1 | tool calls for lookups and answers; 0 is the text protocol, for measurement only |
| `ONE_TIER` | 1 | every decision on the reasoning tier; `run_batch.py --two-tier` is a measured arm |
| `MULLIGAN_HEURISTIC` / `SKIP_HEURISTICS` / `PLAN_EXECUTE` | 0 | harness shortcuts, measurement arms only |
| `GAME_SEED`, `SWAP_SEATS` | random, 0 | seeded shuffles; `1` seats identity B first (the paired flip) |
| `DECK_A/B`, `DECK_A_NAME/B_NAME`, `PLAN_A/B` | eval decks | decklists, seat labels, deck framing (`PLAN_A=""` for none) |
| `EVAL_OUT` | off | directory for the per-decision and game JSONL |
| `GAME_CAP_MS` | 3 h headless | wall-clock cap; a capped game is invalid, never a draw |
| `SMART_BUDGET` / `FAST_BUDGET` | 10000 | per-game call caps: loop guards, not decision limits (`docs/harness/MODEL-LIMITS.md`) |
| `OPENROUTER_BASE_URL`, `OPENROUTER_TIMEOUT_MS`, `OPENROUTER_IDLE_MS` | openrouter.ai, per model, 90000 | endpoint (recorded on the game line), call deadline, how long a silent connection may stall |
| `OPENROUTER_MODEL_CFG` | none | JSON override of the per-model config (`ModelCfg`: max tokens, timeouts, reasoning effort) |

## Operations

- **Parallel games:** openrouter games 6 to 10 JVMs (RAM-bound, about 1 GB each, counted across all batches); floor 4
  to 8; Claude CLI specs 1. Never rebuild the checkout while a game runs. `eval/CONCURRENCY.md`.
- **A capped game** grants in-flight asks a grace window sized to the slowest model timeout before the JVM exits;
  `run_batch.py`'s backstop stays wider than cap plus grace.
- **The shadow labeller** (`SHADOW_AI`) has its own two-gate check: `bash eval/shadow_canary.sh`.
- `secrets.env` (git-ignored; template `secrets.env.example`) is read by the eval drivers.

## Lessons the code is built on

1. **Pass priority with `null`, never an empty list.** An empty list means "play nothing, then ask again", and the
   engine's priority loop spins 999 times.
2. **Constrain the model to the engine's legal list,** and offer only what can be paid: a count, mode or target the
   cast cannot pay fails the whole cast.
3. **Answers are tool calls, never text.** A free-form reply that reasoned about a creature it decided not to use was
   read as an attack with it; text grammars refused 30 of 43 tool requests for their form. Zero text parsing.
4. **The AI cast path does no targeting.** Set engine-validated targets on the chosen ability before returning it, or
   the cast fizzles.
5. **Never fall back to Forge's AI.** A failure takes a fixed legal option, recorded; Forge's AI quietly made
   load-bearing calls before.
6. **No model ask while pricing.** Building a prompt prices the hand; a hook asked from there builds another prompt,
   forever. The guard is saved and restored, never cleared.
7. **Every number reports its floor.** A replay match of 88% was 10 points over doing nothing.

## Where the documents are

| Document | What |
|---|---|
| `CLAUDE.md`, `.claude/rules/`, `.claude/skills/` | instructions for agents working here |
| `docs/STANDARDS.md` | the standards the review skills enforce |
| `docs/harness/ARCHITECTURE.md` | every part of the harness and why it is built that way |
| `docs/harness/WORK-PLAN.md`, `REVIEW-2026-10-16.md` | open work and its record |
| `docs/harness/GAME-LOOP-LOG.md` | what each real game found |
| `docs/harness/ACCEPTANCE.md`, `GOLDEN-CHECKS.md`, `VERSIONS.md` | gate records, surface coverage, version stamps |
| `docs/harness/PROMPT-CRAFT.md`, `PROMPT-AUDIT.md`, `MODEL-LIMITS.md` | how the seat's prompts and tools are written |
| `docs/harness/INDEX.md` | every document under `docs/harness/`, current or historical |
| `eval/README.md`, `EVAL-DESIGN.md`, `SCORING.md`, `PUZZLES.md` | the eval |
