# The match model — persistent state, continuity, and memory for the LLM seat

**Status:** built and acceptance-gated (PRs #46–#58, #77). Observation version `observation-faithful-v14`.

## Why

The seat could already *see* the board, but it played like a stranger to its own game: it could not
follow a card across a zone change, replayed land drops, dropped its plan the moment the turn passed,
re-derived combat arithmetic, forgot what the opponent kept doing, and confused the log with what it
could do now. Each of those is a **harness** failure, not a model failure, and each now has a
harness-side answer.

## What the seat is told (all deterministic, all labelled)

| Block | Answers | Source |
|---|---|---|
| `@N` handles + `(was @k)` | which object is which | `SeatObservation` (G2) |
| `KNOWN IN HIDDEN ZONES` | "I unsummoned their creature, so a Thoughtseize takes it" | public moves + reveals; dropped on a seen exit |
| `SPENT THIS TURN` | land drop AVAILABLE/USED, spells cast, attackers declared | `Player.getLandsPlayedThisTurn` etc. |
| `FACTS` | both next-combat clocks + their instant-speed mana | `ComputerUtilCombat` / `ComputerUtilMana` |
| `RECURRING (cast 2+ times)` | what the opponent keeps doing | the seat's cast stream |
| `YOUR PLAN` | the model's own plan, kept across the turn boundary, flagged stale | model-authored |
| `YOUR OPPONENT PROFILE` | the model's own read of them (labelled unverified) | model-authored |
| `GAME HISTORY (compacted)` | verbatim recent + per-turn digests, each citing `[E<n>]` | `GameHistory.Mode.TIERED` |
| `INSPECT @N` → `history:` | where an object has been | the per-object trail |
| `YOUR CARD INTERACTIONS` | the model's own statement of how its cards combine (labelled unverified) | model-authored, asked at most twice |
| option labels | what each option costs and which zone it is cast from | the ability's own cost (`getPayCosts`) |
| `OPPONENT CARDS YOU HAVE SEEN` | what each opponent card the seat has identified actually does | card database text, first seen onward |

Two rules hold throughout:

- **Nothing is asserted that the seat could not know.** `KNOWN IN HIDDEN ZONES` and the object trail
  are derived only from the seat stream. `GameStateEvaluator` is **never** consulted: it is
  perfect-information (the copier's hidden-info pruning is disabled) and non-adversarial.
- **History is visibly history.** The archive is labelled historical, the current observation carries
  its own id, and the legend says so, so the past is never mistaken for the present.

## Always on

The **play** entry points default to it (`run-llm-headless.sh`, `run-llm-spike.sh`):
`CONTEXT_PROFILE=v4-faithful`, `MEMORY_TOOLS=1`, `RULES_TOOL=1`. Each is a default, not a lock:

```bash
CONTEXT_PROFILE=v1-compat MEMORY_TOOLS=0 RULES_TOOL=0 ./run-llm-headless.sh   # the old baseline
```

The **engine** default stays `v1-compat`, deliberately: `ContextProfileTest` pins
`V1_COMPAT.sha()`, and the ai-teacher/student prompt pairing needs it byte-stable.

The **eval runner** (`run_batch.py`) therefore **pins every condition key explicitly**
(`CONTEXT_PROFILE`, `MEMORY_TOOLS`, `RULES_TOOL`) rather than relying on "unset → JVM default", so a
batch can never inherit the play scripts' always-on defaults into a baseline arm. Deck paths are
resolved to absolute paths up front: the game runs from the Forge tree, so a relative path fails to
load and — because Forge starts an AWT thread — the JVM lingers and burns the whole `--cap-minutes`
instead of exiting.

## How this is measured

**M2 — capability probes** (`MatchModelProbeTest`): one deterministic probe per failure class
(same-name distinctness, hidden-zone memory, object history, stated legality, plan lifetime, engine
arithmetic, recurring threats, history labelling, self-description). These assert what the seat is
*told*; they are a regression net, not evidence of better play.

**M3 — Sighted vs Restricted** (price each tool). Run the same seeds with the blocks on and off, one
lever at a time, and compare. The harness makes this one env var each:

```bash
# full match model (v4-faithful, tools on) vs the historical baseline (v1-compat, tools off)
python3 eval/run_batch.py --deck-a eval/decks/grixis-reanimator.txt \
  --deck-b eval/decks/mardu-energy.txt --seeds 42,43 --games-per-seed 2 \
  --context-profile v4-faithful --memory-tools --out runs/ab/match-on
python3 eval/run_batch.py --deck-a eval/decks/grixis-reanimator.txt \
  --deck-b eval/decks/mardu-energy.txt --seeds 42,43 --games-per-seed 2 \
  --context-profile v1-compat             --out runs/ab/match-off
python3 eval/aggregate.py runs/ab/match-on runs/ab/match-off
```

A finer ablation flips ONE block at a time via the per-knob overrides
(`CTX_RECENT_EVENTS`, …) plus `LOOKAHEAD` / `RULES_TOOL`, which is how each tool's marginal value is
priced.

**M4 — paired A/B on the permitted model.** Same decks, same seeds, seats swapped, `match-on` vs
`match-off`; read the paired win rate with `eval/aggregate.py` (it reports the paired statistic, not a
pooled average, because the runs are a paired design). Pairing across an observation-version bump is
**invalid** — `context_sha` differs, so the two arms are different measurements by construction.

**Knowing–doing check** (cheap error detector): `run_batch.py` stores the model's `PLAN`/`PROFILE`
and its chosen action per decision. A decision whose stated plan and action disagree is worth an
audit — PokerSkill/ToolPoker found this gap is where models look competent and play badly.

## Not done (explicit)

- The multi-turn sampled rollout (EVAL-DESIGN Phase 1.6) is still open; `PlayerLookahead` is the
  foundation.
- The knowing–doing check is now a tool, `eval/plan_audit.py` (plan-vs-action over recorded JSONL or a
  game log; flags a card the plan says to hold that was played the same turn). It is lexical and
  reports a rate with its trivial baseline; it is not an acceptance gate.
