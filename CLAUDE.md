# spike-eval: LLM agents playing Magic: The Gathering on the Forge engine

A model plays full Magic games inside the real Forge rules engine. `PlayerControllerLLM` routes every decision the
engine asks a player (89 decision hooks, 44 ways to play a card) to the model; the engine validates every option, so an
illegal move is impossible. The goal is a general MTG-play **benchmark**, not a stronger student.

Read first: [docs/harness/WORK-PLAN.md](docs/harness/WORK-PLAN.md) (open work), [docs/harness/ARCHITECTURE.md](docs/harness/ARCHITECTURE.md)
(every part and why), [docs/STANDARDS.md](docs/STANDARDS.md) (what the review skills enforce),
[docs/harness/VERSIONS.md](docs/harness/VERSIONS.md) (every version stamp, generated). Path-scoped rules load from
`.claude/rules/` when you touch the overlay (`forge-overlay.md`), the eval (`eval.md`) or the seat's prompts and tools
(`seat-prompts.md`).

## The benchmark (the deliverable)

Three headline scores, each with its confidence interval and the trivial baseline it beats (a score without its floor
is not a score; `.claude/rules/eval.md`, "the 88% lesson"):
1. **STR-AI**: win rate against Forge's AI on the frozen panels (`eval/suites/panels-v1.json`, `eval/panel_score.py`).
2. **STR-HELDOUT**: the same on seeds blocked from every training corpus (`eval/seed_policy.py`).
3. **PUZ**: the puzzle suite (`docs/eval/PUZZLES.md`, `eval/puzzles/puzzle_score.py`).

## The one rule that matters

**`forge-overlay/` is the source of truth.** Never edit the Forge checkout (`/root/forge` here; `$FORGE_HOME`): it is
overwritten. Edit the overlay, `FORGE_HOME=/root/forge bash install.sh`, build, run acceptance. The checkout is pinned
(`FORGE_COMMIT`); results compare only within one engine version. Engine patches are diffs in `engine-patches/` (never a
whole Forge file in the repository; `install.sh` applies them), marked `HARNESS PATCH` and listed in
`docs/harness/ENGINE-PATCHES.md`.

## Owner rules (non-negotiable)

- **Game model: GPT-6 Luna** (`openrouter:openai/gpt-6-luna`) for every seat and tier unless the owner names another.
  Never Opus.
- **Exception: the `claude-cli:` lane** (Claude Code `claude -p`, e.g. Opus 5.5) is for local development on the owner's
  subscription, only with its explicit opt-in (`CLAUDE_CLI_SEAT=1`), and never in a benchmark score (owner,
  2026-10-24). The bare-name CLI text lane (`opus`, `haiku`) is retired (owner, 2026-10-25).
- **Egress:** any `openrouter:` spec sends the game state to openrouter.ai. The factory refuses without
  `OPENROUTER_ALLOW_EGRESS=1`; that consent is the user's. Never set it yourself. Loopback (fake-model) runs need none.
- **Every decision is answered by a tool call** (`answer`, one fixed schema; `set_plan` for the plan). Zero text
  parsing: on a tool ask the text parsers refuse and log `[TEXT-PARSE]`, which fails acceptance. A reply that calls
  nothing is reminded `AnswerTool.MAX_NUDGES` times, then the decision is lost to a fixed legal option. `tool_choice`
  is "required" for OpenAI models, "auto" elsewhere (`AnswerTool.toolChoiceFor`).
- **The seat's lookups are native function calls** (`NativeTools.java`, mirrored in `eval/tooluse/tools.json`); the
  text grammar survives only for a backend without function calling. A `REFUSED` request in `eval/tool_audit.py` is a
  harness defect.
- **Never fall back to Forge's AI** for a choice the seat owns: `[TALLY] ... ai=0`. A legal action is never hidden,
  except under the `PROPOSE_ACTIONS` measurement arm (exploratory only, never a score; owner, 2026-11-04).
- **No heuristics decide for the model.** The only skips: the menu is PASS only, or the identical spot was passed.

## Cost of paid runs

A one-tier Luna game costs $0.02 to $0.05 and takes 20 to 90 minutes. `python3 eval/cost_report.py <runs>` prices
any run (`--plan N` for N more). Always pass `run_batch.py --budget-usd X`. Start with a small block (about six games
per arm), read it, then decide; say the expected cost before any paid run. Play games one at a time with the
`game-loop` skill; a batch is not how the harness is tested.

## Commands

```bash
FORGE_HOME=/root/forge bash eval/harness_acceptance.sh        # everything: compile, all tests, canaries, fake-model games
STAGES="preflight classpath compile javatests" FORGE_HOME=/root/forge bash eval/harness_acceptance.sh   # a subset
bash eval/keyword_gauntlet.sh                                  # 29 free fake-model games over 564 cards
python3 eval/tests/test_<name>.py                                    # one Python test file
python3 eval/run_batch.py --seeds N --exploratory --budget-usd 0.40 --deck eval/decks/<d>.txt \
  --controller-a llm --controller-b ai --smart-a openrouter:openai/gpt-6-luna --fast-a openrouter:openai/gpt-6-luna \
  --memory-tools --lookahead --out runs/loop/gNN               # one paid game (the game-loop skill's form)
```

Zero-model canaries (in acceptance): seed 42 floor mirror wins A in 108 turns, random mirror A in 55 (Linux pins). Floor
never reaches the play menu; use the random arm for play, attack and block paths. Before trusting a green gate, say what
would have made it fail.

## Where things are

- `forge-overlay/.../spike/LlmSpikeGui.java`: `PlayerControllerLLM` (every hook), the bridges, `EvalLog`.
  `SeatObservation.java` (the state the seat reads), `AnswerTool.java`, `NativeTools.java`, `PlayerLookahead.java`.
  `LlmSpikeHeadless.java` runs a game with no GUI and writes the game line.
- `eval/`: launchers and scoring (`run_batch.py`, `aggregate.py`, `panel_score.py`, `puzzle_score.py`), the review
  sheet (`match_review.py`, `tool_audit.py`, `plan_audit.py`, `defect_analyzer.py`), the gauntlet.
- `docs/harness/`: architecture, game-loop log, acceptance records, golden checks, prompt craft, versions.
- `docs/harness/SETTINGS.md`: the env-var table; `docs/harness/LESSONS.md`: the lessons the code is built on.
- Root launchers: `setup.sh` (fresh clone to a built Forge at the pin and `secrets.env`), `play-vs-llm.sh` (a person
  against the seat, `CONTROLLER_A=human`, never scored; `eval/play_commentary.py`), and the game launchers
  `run-llm-headless.sh` / `run-llm-spike.sh` (in `forge-overlay/`; run from `$FORGE_HOME` after `install.sh`).

## Skills (required, user decision 2026-10-13)

| When | Skill |
|---|---|
| before every commit, PR title/body, and merge | `pr-format` |
| on every PR, before merging | `pr-review`, then `simplify`, then `standards` (fresh subagents where possible) |
| whenever a game is played | `game-loop` (one match: play, read, fix, next) |
| whenever game logs are reviewed | `harness-audit` |
| filling or working the defect backlog | `defect-hunt`, `defect-fix` |

Recording verdicts and the merge gate: `docs/STANDARDS.md` section 1. Once per clone: `bash scripts/install_hooks.sh`.

## Keep the documents true

A change to how the harness works (a decision path, a tool, the prompt, a bridge, a default, an eval rule) updates
`docs/harness/ARCHITECTURE.md` in the same PR, bumps the version stamp that covers it (`python3 eval/versions.py
--write`), and records in `docs/harness/ACCEPTANCE.md` an acceptance run on an earlier head that changed something (the
final head's run goes in the PR's `## Evidence`; `docs/STANDARDS.md` section 7). Commit subjects and body
lines stay at 100 characters or fewer (`scripts/pr_format.py`). Model specs and priced slugs never go in commits or
PRs; a model's name in prose is fine (owner, 2026-10-21).

## Before committing

Stage, then have the diff reviewed (`pr-review`, or the `verifier` agent where installed). Land through a PR and
`scripts/pr_merge.sh`.
