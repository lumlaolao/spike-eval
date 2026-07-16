# mtg-forge-llm-agent

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
