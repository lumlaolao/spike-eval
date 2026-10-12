# mtg-forge-llm-agent

> September 2026 continuation: see [Player harness direction and status](docs/harness/PLAYER-HARNESS.md). Newer Java sources were recovered from the adjacent Forge checkout; older status claims below may be superseded. `MEMORY_TOOLS=1` enables experimental evidence retrieval and a seat-local strategy notebook.

A spike that lets a **frontier LLM (Claude) play full games of Magic: The Gathering** inside
[Forge](https://github.com/Card-Forge/forge), the open-source MTG rules engine — rendered live
in Forge's desktop GUI, with the model driving one seat via `claude -p` (Claude Code subscription
auth, no API key).

This repo holds only the **custom files** (an overlay you drop into a Forge checkout). It does not
redistribute Forge itself (GPL).

## What works (verified)

- **Injection with no engine fork.** Subclass one abstract class, `forge.game.player.PlayerController`
  (~80 decision methods), via the `IGameEntitiesFactory.createIngamePlayer` → `Player.setFirstController`
  seam. `LlmSpikeTest` proves it headless; `LlmSpikeGui` renders it in the Swing GUI.
- **A real LLM plays a complete game.** Opus drove mulligan, land drops, spell casting, and attacks
  through one full Constructed game — coherent play, zero errors, zero illegal moves.
- **Reasoner/executor tiering** keeps it usable:
  - **Tier 1 (code/heuristic):** trivial spots decided locally (e.g. 3–4 land opener = snap-keep;
    non-main-phase priority → delegate to Forge AI).
  - **Tier 3 (reasoner):** load-bearing spots (mulligan edge cases, what-to-play, attacks, blocks)
    → Claude.
- **Persistent warm bridge.** One long-lived `claude -p --model opus --input-format stream-json
  --output-format stream-json` process, reused per decision (~3–8s/call, model-inference-bound)
  instead of a fresh `claude -p` cold-start each time (6–28s).

## Decisions routed to the LLM

Nearly every strategic decision now flows through the model, split across two tiers — a **smart**
model (`SMART_MODEL`, default opus) for load-bearing calls and a **fast** model (`FAST_MODEL`,
default haiku) for high-frequency low-stakes spots.

| Decision | Forge hook | Tier | Notes |
|---|---|---|---|
| Mulligan | `mulliganKeepHand` | smart | hard floor (never below 5 cards); auto-keeps 2–4 land openers; only flood/screw reach the model |
| What to play / land drop | `chooseSpellAbilityToPlay()` | smart (own main) / fast (reactive) | offers each legal spell **and each distinct land** as a numbered option; **not** `getAbilityToPlay` |
| Attacks | `declareAttackers` | smart | numbered subset pick (`PICKS:`) over engine-legal attackers (`CombatUtil.canAttack`), with the full game snapshot — robust to duplicate names |
| Blocks | `declareBlockers` | smart | only legal blockers (`CombatUtil.canBlock`), full snapshot, `BLOCKS: a->b` pairs |
| Spell targets | `ensureTargetsSet` (pre-cast) + `chooseTargetsFor` | smart | engine-validated candidates only (`getAllCandidates`/`canTarget`); fixes the fizzle class ("Couldn't add to stack, failed to target") |
| Discard | `chooseCardsToDiscardFrom` | smart | the Reanimator fix — pitch a fatty you can later reanimate; also covers **stripping the opponent's hand** (Thoughtseize/Cabal Therapy "you choose") |
| Reanimate / tutor target | `chooseSingleCardForZoneChange` | smart | pick *which* card leaves the zone (Reanimate, Animate Dead, fetches) |
| Single/multi entity pick | `chooseSingleEntityForEffect` | smart | AI loops this for multi-target, so one override covers both |
| Cards for effect | `chooseCardsForEffect` | smart | min/max-bounded multi-pick |
| Name a card | `chooseCardName` (both variants) | smart | Cabal Therapy / Pithing Needle — free-form name validated by the engine's face lookup + predicate; chat memory recalls earlier Thoughtseize reveals |
| Modal spells | `chooseModeForAbility` | smart | numbered mode menu (`PICKS:`) |
| Yes/No | `confirmAction` | fast | |
| Numbers / X | `chooseNumber` | fast | |

Every branch that can't reach the model (budget/parse/bridge failure) takes a **deterministic legal
fallback** — PASS, develop a land, or the first legal option — *not* Forge's AI. A per-turn `[TALLY]`
prints `smart/fast/auto/ai` counts and every genuine AI handoff logs `[FALLBACK->AI]`, so you can prove
the LLM is really driving (validation runs show `ai=0`).

## Hard-won lessons (baked into the code)

1. **`getAbilityToPlay` is a red herring.** When you extend `PlayerControllerAi`, own-turn play
   selection routes through `chooseSpellAbilityToPlay()` (called every priority; `PhaseHandler`),
   not `getAbilityToPlay`.
2. **Constrain the model to the engine's legal list.** Enumerate legal/castable options
   (`ComputerUtilAbility` + `ComputerUtilMana.canPayManaCost`, `CombatUtil.canAttack/canBlock`) and
   only ever act on those — a hallucinated/illegal choice is silently dropped, never an illegal move.
3. **Structured answer channel.** Free-form replies leak: a model that decides "NONE" while *reasoning*
   about a creature made a naive name-match attack with it. Force a final line
   (`ATTACKERS: …`, `BLOCKS: a->b`, `CHOICE: n`) and parse only that.
4. **`chooseSpellAbilityToPlay` fires on every priority** → a naive version hits the model ~100×/game.
   Guards: skip the model entirely on pointless instant-speed windows (empty stack, not in combat),
   dedupe of identical repeated pass-spots, a per-turn hard cap, and try/catch on any error — each
   falling to a deterministic legal PASS/land, never a hang or crash.
5. **Pass priority with `null`, never an empty list.** `PhaseHandler.mainLoopStep` breaks the priority
   loop only on `chosenSa == null`; returning an empty `List` means "play zero abilities, then ask
   again" and spins the loop the full 999 iterations (`AI looped too much`) while the match crawls.
   This one bug turned a clean game into 112 loop-warnings — the single highest-impact fix here.
6. **Don't fall back to the AI brain for *strategy*.** Budget/parse/bridge failures take a
   deterministic legal option (PASS, develop a land, first legal pick), so the engine AI never
   quietly makes a load-bearing call. Only truly mechanical spots (mana payment) still delegate —
   and every delegation is counted + logged.
7. **The AI cast path doesn't target for you.** `PlayerControllerAi.playChosenSpellAbility` →
   `ComputerUtil.handlePlayingSpellAbility` runs no targeting for ordinary spells (the deferred
   targeting runnable exists only for `TargetingPlayer` cards), so a spell you return untargeted
   fails `hasLegalTargeting` at `MagicStack` and fizzles ("Couldn't add to stack, failed to
   target"). Set engine-validated targets on the chosen `SpellAbility` (walk the sub-ability chain)
   *before* returning it from `chooseSpellAbilityToPlay` — and don't offer spells with zero legal
   targets in the first place.

## Cost / latency reality

Even gated, a full Opus game is dozens of calls × ~$0.06 × ~3–8s. For volume, put a cheaper model
on the executor tier (`CLAUDE_MODEL=haiku`) and reserve the strong model for genuinely close spots.

Speed levers baked in: the fast tier replies with **only** the final line (zero reasoning tokens),
bridge sessions auto-refresh every `CLAUDE_RESET_EVERY` asks (default 40) so the conversation's
prefill doesn't grow unboundedly (an `INTEL:` line in every snapshot carries revealed-hand/named-card
memory across the reset), MCP servers are excluded from bridge sessions (`--strict-mcp-config`),
and empty reactive priority windows pass without any model call (except the opponent's end step —
the classic instant-speed action window). `[TALLY]` prints running `avgSmart`/`avgFast` latency.

## Tuning (env vars)

| Var | Default | What |
|---|---|---|
| `SMART_MODEL` / `FAST_MODEL` | `opus` / `haiku` | model per tier |
| `SMART_BUDGET` / `FAST_BUDGET` | 320 / 900 | per-game call caps |
| `CLAUDE_DEADLINE_MS` | 90000 | per-call timeout |
| `CLAUDE_RESET_EVERY` | 40 | bridge session refresh cadence (0 = never) |
| `DECK_A` / `DECK_B` | Downloads decklists | decklist paths ("N Cardname" format) |
| `DECK_A_NAME` / `DECK_B_NAME` | Grixis Reanimator / Mardu Energy | seat labels |
| `PLAN_A` / `PLAN_B` | built-in | strategy framing per seat |
| `DECKLIST` | `1` (on) | append each seat's own decklist to its plan (a real player knows their deck); `0` restores the pre-decklist prompt bytes |
| `RECORD` / `RECORD_OUT` / `RECORD_FPS` | off | window recording (see below) |
| `GAME_SEED` | random | seeds Forge's RNG — same seed = same shuffles/coin flip (bit-reproducible with non-LLM arms) |
| `SWAP_SEATS` | 0 | `1` seats identity B first — the paired-design flip |
| `SMART_MODEL_A/B`, `FAST_MODEL_A/B` | inherit `SMART_MODEL`/`FAST_MODEL` | per-seat models for A/B comparisons |
| `CONTROLLER_A/B` | `llm` | baseline arms: `random` (uniform over the same legal menus), `floor` (deterministic fallbacks only — measures the harness's own strength), `ai` (pure Forge AI) |
| `EVAL_OUT` | off | directory for per-decision + game-summary JSONL (the eval dataset) |
| `MULLIGAN_HEURISTIC` / `SKIP_HEURISTICS` | 1 | set `0` for confound-free eval runs (model owns all mulligans / no free-pass shortcuts) |
| `GAME_CAP_MS` | 3h (headless) | wall-clock cap; a capped game is recorded **invalid**, never a draw |
| `CHECKPOINT_SHA` | `unknown` | identity of the weights under test, stamped on the game line. Set it via `run_batch.py --checkpoint <path-or-tag>` rather than by hand |
| `SEED_BANK` | `unregistered` | which declared band (`eval/seed_registry.json`) the seeds came from; `run_batch.py` sets it |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | endpoint for `openrouter:` specs; recorded on the game line, since a local server and openrouter.ai are different experiments even for an identical model string |

## Multi-backend models (OpenRouter and beyond)

The harness is no longer Claude-CLI-only. `SMART_MODEL` / `FAST_MODEL` (and the per-seat `_A`/`_B` variants) accept backend-prefixed specs:

| Spec | Backend | Example |
|---|---|---|
| `haiku`, `opus`, `sonnet` | Claude CLI (`claude -p`) | `SMART_MODEL=opus` |
| `openrouter:<model>` | OpenRouter API | `SMART_MODEL=openrouter:anthropic/claude-3.5-sonnet` |

For OpenRouter, set `OPENROUTER_API_KEY` **and** `OPENROUTER_ALLOW_EGRESS=1`. The second flag is a deliberate consent gate: every OpenRouter call ships the full game snapshot (board, both players' known state, deck plan) to a third-party API, and having a key configured is not the same as agreeing to send this session's data. The factory throws at startup if the flag is missing. Optional tuning: `OPENROUTER_BASE_URL` (default `https://openrouter.ai/api/v1`), `OPENROUTER_TIMEOUT_MS` (default 90000), `OPENROUTER_HTTP_REFERER`, `OPENROUTER_HTTP_TITLE`.

**Memory / state:** every ask still sends the full game snapshot plus the `INTEL` line, so API backends can be stateless. No conversation history is required for the model to play coherently — the prompt already contains the board, hands, graveyards, stack, recent events, and remembered reveals.

This makes mixed-model tournaments straightforward, e.g.:

```bash
SMART_MODEL_A=openrouter:anthropic/claude-3.5-sonnet \
SMART_MODEL_B=openrouter:openai/gpt-4o \
OPENROUTER_API_KEY=... OPENROUTER_ALLOW_EGRESS=1 bash run-llm-headless.sh
```

## Eval mode

The review that motivated this (see git history) found the demo harness unsuitable as a model
eval: no reproducibility, no baselines, no dataset, expert plans confounding the measurement.
Those are now addressed: seeded games (floor-vs-floor at seed 42 reproduces **bit-identically**,
108 turns / 12 seconds), identity-keyed A/B arms with seat-swapped pairing, per-decision JSONL,
three cheap baseline anchors (a full `floor` game runs in ~12s, `random` in ~1min — anchor
distributions are essentially free), plan-free / heuristic-free confound switches, and a pinned
engine version (`FORGE_COMMIT`, checked by `install.sh`).

Batch tooling lives in [`eval/`](eval/): `run_batch.py` (paired seat-swapped games, resumable)
and `aggregate.py` (Wilson CIs, paired bootstrap on the win-rate delta, per-tier latency/fallback
stats). See `eval/README.md` for the paired-mirror design and the statistical power math.
Note: only the **headless** runner writes the game-summary line — `EVAL_OUT` on a GUI run yields
decision lines only, which `aggregate.py` treats as a crashed/invalid game.

## Build & run

Requires JDK 17 (Forge targets 17) and the [`claude`](https://claude.com/claude-code) CLI logged in.

```bash
# 1. Clone Forge
git clone --depth 1 https://github.com/Card-Forge/forge.git ~/code/forge

# 2. Overlay these files
FORGE_HOME=~/code/forge ./install.sh

# 3. Prove the seam headless (optional)
cd ~/code/forge
JAVA_HOME=$(/usr/libexec/java_home -v 17) \
  mvn -q -pl forge-gui-desktop -am test -Dtest=LlmSpikeTest \
  -Dsurefire.failIfNoSpecifiedTests=false -Dcheckstyle.skip=true

# 4. Watch it play in the GUI (Opus by default; CLAUDE_MODEL=haiku for faster/cheaper)
JAVA_HOME=$(/usr/libexec/java_home -v 17) \
  mvn -q -pl forge-gui-desktop -am -DskipTests -Dcheckstyle.skip=true compile \
  dependency:build-classpath -Dmdep.outputFile=/tmp/forge_cp.txt
bash ~/code/forge/run-llm-spike.sh
```

`run-llm-spike.sh` launches the Forge desktop window with a **spectator** LLM-Agent-vs-Forge-AI match
(both seats non-human → `HostedMatch` auto-opens a spectator view). Every decision prints a
`[ROUTER]` / `[CLAUDE]` line to the console.

## Recording the game (no screen-recording permission needed)

Set `RECORD=1` and the launcher captures the Forge window straight to an H.264 mp4:

```bash
RECORD=1 RECORD_OUT=~/Downloads/game.mp4 RECORD_FPS=8 bash ~/code/forge/run-llm-spike.sh
```

- `RECORD` — any non-empty, non-`0` value enables recording.
- `RECORD_OUT` — output path (default `~/Downloads/forge-game-<ts>.mp4`); an `<out>.ffmpeg.log` sits beside it.
- `RECORD_FPS` — frame rate, clamped 1–30 (default 8).

It works by painting the Swing `FFrame` into a `BufferedImage` on the EDT (`frame.printAll`) and piping
PNG frames into `ffmpeg -f image2pipe` — the app renders *itself*, so this needs **no macOS Screen-Recording
(TCC) permission** and captures only the Forge window. `ffmpeg` must be on `PATH`. A JVM shutdown hook
closes the pipe so ffmpeg finalizes the moov atom cleanly on exit (including SIGINT/SIGTERM).

## Files

- `forge-overlay/…/main/…/spike/LlmSpikeGui.java` — GUI launcher, persistent Claude bridge, the router.
- `forge-overlay/…/test/…/spike/LlmSpikeTest.java` — headless proof of the injection seam.
- `run-llm-spike.sh` — launcher (goes in the Forge repo root; set by `install.sh`).
- `install.sh` — copies the overlay + launcher into a Forge checkout (`$FORGE_HOME`).

## Status

Spike, but a capable one. Nearly all strategic decisions are LLM-driven across a smart/fast tier
split; `confirmAction`, discard, entity/card selection, reanimation targets, mulligan, and land choice
are routed; deterministic legal fallbacks keep the engine AI out of load-bearing calls (`ai=0` in
validation). Validated end-to-end with sonnet: mulligan → land sequencing → Faithless Looting →
discard Griselbrand → Dark Ritual → Reanimate target → combat, with 0 priority-loops / 0 AI-fallbacks /
0 exceptions. Next: hand-roll `chooseTargetsFor` for the rare multi-target spells that still delegate;
measure win-rate; trim per-decision latency.
