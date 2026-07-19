# Eval campaign 2: LLM vs Forge AI — 2026-07-18

The first honest measurement of the haiku pilot against a *competent* opponent.
Campaigns 1 measured haiku against weak bars — uniform-random play (20/20) and
its own deterministic fallback (19/19); neither says whether the agent plays
*good* Magic, because random and first-legal-option are both bad Magic. Forge's
built-in AI is a decades-tuned, genuinely competent opponent. This is the first
citable answer to "how good is the LLM pilot, actually?"

**Headline: haiku loses to Forge AI 1/20 (5.0%), Wilson 95% [0.9%, 23.6%].**
That is not a failure of the experiment — it is the experiment working. We now
have a real gap with a confidence interval, and a clean, learning-usable corpus
of the games that produced it.

## The citable batch: `haiku-vs-ai-2`

```
discipline=cited  engine_sha=c1c64a53586459cd0d4c70f27abd1aa8bbc92891
overlay_sha=762aca814fdf  deck_sha=4e7be5785b40
mulligan_heuristic=0  skip_heuristics=0  plan_a=""  plan_b=""
smart_budget=320  fast_budget=900  claude_deadline_ms=90000  game_cap_ms=7200000
```

10 seed-pairs, seats swapped, identity-keyed winner, `jobs=1`. Wilson 95% CIs;
paired bootstrap at the seed level (10,000 resamples, seed 20260715). Both
seats of both decks ran the Mardu Energy mirror (`eval/decks/mardu-energy.txt`);
`Identity-A` = haiku (`CONTROLLER_A=llm SMART_A=FAST_A=haiku`), `Identity-B` =
Forge AI (`CONTROLLER_B=ai`).

| arm | result | Wilson 95% CI | paired delta [95% CI] | seat-1 bias | data integrity |
|---|---|---|---|---|---|
| haiku[haiku/haiku] vs ai | haiku **1/20 = 5.0%** | [0.9%, 23.6%] | −90 pts [−100, −70] | 45.0% | 20/20 decisive, 0 invalid, **0 contaminated** |

Per-seed: focal (haiku) swept both 0 · split 1 · lost both 9. The single win
was seed 2 (swapped seat), a 17-turn game. Games ran 44 s–30 min wall-clock
(median ~13 min); every game reached a decisive result — no draws, no caps, no
crashes.

### This result is trustworthy (the contamination bug is fixed)

The **first** attempt at this batch (`runs/haiku-vs-ai/`, `overlay_sha=532c01a82b16`)
was poisoned: mid-run the Claude CLI began returning
`"You've hit your session limit · resets 3pm"`, and the pre-fix bridge parsed
that error text as a model reply — 324 of 405 model-consulted decisions (80%)
were rate-limit strings logged as tier `smart`, `fallback=null`. That 0/20 was
noise, not a measurement, and is retained only for diagnosis.

The re-run closes the hole at three layers, and the corpus proves it held:

- **Bridge (`isNonAnswer`)** rejects rate-limit/error strings before they can be
  logged as a decision → they route through the honest bridge-down fallback.
- **Analyzer (`analyze_corpus.py`)** now exits 2 (data-quality failure) on *any*
  contaminated reply, with a hermetic test fixture and a Java/Python pattern-list
  parity test.
- **Result**: `analyze_corpus.py runs/haiku-vs-ai-2` exits 0. Coverage:
  model-consulted 1363, full round-trip (prompt+reply) 1363, prompt-without-reply
  (bridge died) 0, **contaminated 0**. The session limit had reset and every
  reply is genuine haiku reasoning (smart p50 16.0 s, p90 34.7 s, max 85.7 s —
  under the 90 s deadline, so no decision was lost to timeout).

## The floor-vs-ai baseline (`floor-vs-ai`, `overlay_sha=3ca3280e1fc2`)

Same engine/deck/discipline, `game_cap_ms=900000`, no model arm. floor **0/20 =
0.0%** vs Forge AI [0.0%, 16.1%]. This is the deterministic-fallback anchor: when
no model drives the seat, the harness's legal-move plumbing loses every game to
Forge AI. It is the floor of the scale the LLM result sits above — barely, but
measurably (5.0% vs 0.0%).

## What the corpus says (1,441 decisions, all clean)

- **Volume is dominated by `play`** (1,122 of 1,441; 78%). The rest:
  attackers 73, target 71, damage-assign 54, mulligan 31, blockers 25,
  card-name 21, mulligan-bottom 16, confirm 14, discard 9, entity 5.
- **The routed strategic surfaces were ~100% model-driven.** Zero smart/fast-tier
  fallbacks across the whole batch. Of the 72 fallback-gate lines (5.0%, gate
  ceiling 10%), **70 are structurally-Forge-AI surfaces, not haiku failures**:
  54 `damage-assign` (combat damage-assignment order) and 16 `mulligan-bottom`,
  both of which this binary still runs on Forge AI by construction. The only
  genuinely-routed fallbacks were 1 `card-name(no-match)` and 1 `auto`.
- **Why the fallback rate looks higher than campaign 1 (0.4% → 5.0%).** It is not
  regression — it is *honesty*. The new instrumentation makes previously-invisible
  Forge-AI surfaces (`damage-assign`, `mulligan-bottom`) emit decision lines for
  the first time. Campaign 1's 0.4% simply never counted them.

## Instrumentation: first runtime validation

This batch is the first to actually *exercise* the invisible-surface emitters
(campaign 1's floor-vs-ai never triggered them — floor games end before
multi-blocker combat or mulligan bottoming). Confirmed firing: 54 `damage-assign`,
16 `mulligan-bottom`, and 5 `entity` (the choose-entity routing change). The
`mulligan-bottom` observer immediately earned its keep: it caught Forge AI
**bottoming premium cards** (e.g. Orcish Bowmasters) out of hands haiku had just
reasoned into keeping — the keep-decision and the bottom-decision were being made
by different brains. That surface is now model-routed on `main` (commit
`6eb9e56`); it takes effect at the next install+compile and will move those 16
lines from the `ai` column into the model column.

## Reproduce

```bash
# clean floor-vs-ai baseline (no model cost, ~3 minutes)
python3 eval/run_batch.py --seeds 1..10 --pairs --deck eval/decks/mardu-energy.txt \
  --controller-a floor --controller-b ai

# the citable haiku-vs-ai batch (needs claude CLI quota; ~4-5 h)
python3 eval/run_batch.py --seeds 1..10 --pairs --deck eval/decks/mardu-energy.txt \
  --controller-a llm --smart-a haiku --fast-a haiku --controller-b ai \
  --out runs/haiku-vs-ai-2

python3 eval/aggregate.py runs/haiku-vs-ai-2
python3 eval/analyze_corpus.py runs/haiku-vs-ai-2   # exits 2 if any reply is contaminated
```

## Dataset additions

- `runs/haiku-vs-ai-2/`: 20 games, 1,441 decision lines, 0 malformed, 0
  contaminated. The first clean haiku-vs-Forge-AI corpus; the first to exercise
  the `damage-assign` / `mulligan-bottom` / `entity` instrumentation at runtime.
- `runs/floor-vs-ai/`: 20 games, 93 decision lines, deterministic-fallback anchor.
  The new AI-surface emitters were wired but never fired here (floor games end
  before the relevant board states occur).
- `runs/haiku-vs-ai/`: 20 games, **contaminated** (rate-limit strings parsed as
  replies); retained for diagnosis only. Do NOT use for win-rate claims —
  `analyze_corpus.py` exits 2 on it.
