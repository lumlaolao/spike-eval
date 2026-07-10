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

| Decision | Forge hook | Notes |
|---|---|---|
| Mulligan | `mulliganKeepHand` | heuristic-gated (only escalates flood/screw hands) |
| What to play | `chooseSpellAbilityToPlay()` | **not** `getAbilityToPlay` — the AI's own play loop lives here |
| Attacks | `declareAttackers` | offered only engine-legal attackers (`CombatUtil.canAttack`) |
| Blocks | `declareBlockers` | offered only legal blockers (`CombatUtil.canBlock`) |

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
4. **`chooseSpellAbilityToPlay` fires on every priority** → a naive version hit the model ~102×/game.
   Guards: main-phase-only gate, dedupe of identical repeated pass-spots, a per-turn hard cap
   (→ fall back to AI), and try/catch → AI on any error. Never hangs, never crashes.

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

## Files

- `forge-overlay/…/main/…/spike/LlmSpikeGui.java` — GUI launcher, persistent Claude bridge, the router.
- `forge-overlay/…/test/…/spike/LlmSpikeTest.java` — headless proof of the injection seam.
- `run-llm-spike.sh` — launcher (goes in the Forge repo root; set by `install.sh`).
- `install.sh` — copies the overlay + launcher into a Forge checkout (`$FORGE_HOME`).

## Status

Throwaway spike, not production. Next: route targeting (`chooseTargetsFor`) and `confirmAction`;
put a cheaper model on the executor tier; measure win-rate vs the Forge AI.
