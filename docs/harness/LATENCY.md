# Per-decision latency — measurements, knobs, and what to do next

A turn can hold dozens of model decisions, so seconds-per-decision decides whether a game finishes.
This is what was measured, on `deepseek/deepseek-v4.1-flash` through OpenRouter, one mirror-Burn game
per arm (so treat the throughput column as *indicative*, not a rate).

## What was measured (2026-10-13)

| arm | turns in the 8-min cap | decisions | smart calls | truncation retries | retry time |
|---|---|---|---|---|---|
| before: fast tier sent **no** reasoning param | 7 | 40 | 26 | **4** | **182,518 ms** |
| fast tier → `reasoning.effort="minimal"` | 8 | 40 | 30 | **0** | **0 ms** |
| …plus smart tier `effort="low"` | 9 | 54 | 39 | 3 | — |

Prompt ≈5.1–5.5k tokens; smart-tier completion mean ≈1.3–2.3k, max ≈8.5–13k.

## The bug that dominated (fixed, PR #62)

The fast tier answered **mechanical** questions (which target? keep or mulligan?) but sent **no
`reasoning` parameter**, so a reasoning model spent its entire 4000-token budget on *hidden* reasoning
before emitting any answer token → `finish_reason=length` → truncation retry at double the budget →
**two calls and 182 seconds for one number**. Setting `fastReasoningEffort="minimal"` removed the
whole class: 4 retries → 0.

This is the shape to look for in general: **a reasoning model asked for one token will still reason
first.** Any tier whose contract is "terse" must say so explicitly.

## The decisive measurement (what "we want reasoning, right?" comes down to)

Replaying a **real** smart-tier play prompt under each setting (2026-10-13):

| setting | latency | completion tokens | of which hidden reasoning |
|---|---|---|---|
| `effort=medium` (smart default) | 6.8–7.3 s | 815–848 | 770–804 |
| `effort=low` | 5.9–9.6 s | 759–1025 | 714–999 |
| `effort=minimal` | 7.6–10.4 s | 910–1247 | 875–1194 |
| **no `reasoning` key** | **17.3–23.7 s** | 2704–4019 | 2657–3965 |
| **`reasoning.enabled=false`** | **1.3–12.7 s** | **65–263** | **0** |

**~95 % of every completion was hidden reasoning for a ~50-token answer**, and `effort` does *not*
reliably reduce it — only disabling does. `reasoning.max_tokens` is not honoured either (it went up).
So the design question has a clean answer: **reasoning belongs on the smart tier, where planning,
sequencing and counterspell timing live, and nowhere else.** The fast tier's job is classification
over a menu the harness already legalised.

`reasoning.enabled=false` is wired as the sentinel `"none"` (distinct from effort values, which some
routes reject).

## The unbounded tail

The smart tier can also **overrun** its own budget: a real game hit
`[TRUNCATED->RETRY] finish_reason=length @ max_tokens=16000 -> retrying at 32000`, i.e. a completion
that spent *16 000 tokens thinking and never answered*, then 32 000 more — minutes of the 8-minute cap.
Giving a reasoner more room to think is exactly the wrong response. For a row known to be a reasoner
the retry now **disables reasoning** and keeps a modest budget (~1 s); every other model keeps the
historic double-the-budget retry, which is right for a genuinely long answer.

## The playtest (4 games per arm, vs the FORGE AI, mirror Burn, 20-min cap)

Both arms are **the LLM against the Forge AI** — a fixed, competent, model-free opponent, so a win
means something. (`--pairs` swaps seats, so each arm plays both sides of the play/draw.) **All 8 games
finished; 0 capped.**

| arm | LLM won | smart | fast | **plan** | comp median | comp max | retry time | calls/turn |
|---|---|---|---|---|---|---|---|---|
| base (no levers) | 2/4 | 59 | 19 | 0 | 250 | 3,118 | **410 s** | 2.0 |
| **opt (levers on)** | **3/4** | **28** | 40 | **8** | **47** | 3,725 | **32 s** | 1.9 |

Both arms produced **the same 39 turns** across their 4 games — the same amount of Magic, with:

- **smart calls halved (59 → 28)**, and plan-then-execute firing **8 times**;
- **completion median down 81 % (250 → 47)**;
- **retry time down 92 % (410 s → 32 s)**;
- by type: `target` median **168 → 6**, `prevent-effect` **515 → 25**.

**On quality: 3/4 vs 2/4 is ONE game apart and is NOT evidence.** Four games per arm can show an arm is
broken; it cannot show an arm is 5 % weaker. A quality verdict needs dozens of games per arm (hours of
paid runs). What this pilot does rule out is a *catastrophic* regression from the levers.

## The arms, end to end (measured, 1 seed each — mechanisms solid, turns=noise)

| arm | turns | decisions | smart | fast | **plan** | auto | comp median | comp max |
|---|---|---|---|---|---|---|---|---|
| baseline | 7 | 40 | 26 | 5 | – | 9 | 720 | 6,564 |
| + fast tier disables reasoning | 8 | 40 | 30 | 1 | – | 9 | 488 | 8,519 |
| + tier routing | 9 | 58 | 24 | 23 | – | 11 | 103 | 15,539 |
| + smart budget 4000 | 8 | 44 | 20 | 11 | – | 13 | 175 | 3,784 |
| **A: + `play-single` routing** | **11** | 65 | 18 | **37** | – | 10 | **49** | 3,314 |
| **B: A + plan-then-execute** | 10 | 63 | **10** | 34 | **7** | 12 | **26** | 3,642 |

What each lever bought, in mechanism terms:

- **`play-single`** took `play`'s median completion from **309 → 80 tokens** and moved 37 of 65
  decisions to the (reasoning-disabled) fast tier. It was the single biggest *token* reduction.
- **plan-then-execute fired 7 times in one game** and **halved the smart calls (18 → 10)** — exactly
  the "one smart call per turn" shape. It invalidates on interruption (1 firing here) and those steps
  are logged `tier=plan`, so the hit rate is visible rather than assumed.

**Caveat, stated plainly:** one seed per arm. The turn counts (7–11) are noise; the per-decision
numbers (tier split, completion median/max, retries, plan firings) are the signal. **Neither lever has
a QUALITY measurement yet** — that needs `--pairs` over several seeds with the puzzle corpus, which is
the next step before either becomes a default.

## The four arms (measured, 1 seed — the MECHANISMS are solid, the throughput column is noise)

| arm | turns/8min | decisions | smart | fast | completion median | completion MAX | retries |
|---|---|---|---|---|---|---|---|
| 1 baseline | 7 | 40 | 26 | 5 | 720 | 6,564 | 1 |
| 2 + fast tier disables reasoning | 8 | 40 | 30 | 1 | 488 | 8,519 | 0 |
| 3 + tier routing | 9 | 58 | 24 | 23 | 103 | **15,539** | 2 |
| 4 + smart budget 4000 | 8 | 44 | 20 | 11 | 175 | **3,784** | 1 |

Read the *mechanism* columns, not the turn counts: with one seed per arm, 7–9 turns is noise.

What is solid: **reasoning off on the fast tier made classification essentially free** —
`target` and `prevent-effect` answer with a **median of 6 completion tokens** (vs ~800 of hidden
reasoning on the smart tier) — and **the smart cap bounded the worst case 4×** (15,539 → 3,784 tokens).
The overruns that remain now fire `TRUNCATED->RETRY-NO-REASONING` (5 times in one game) and cost
seconds, not the three minutes the old double-the-budget retry cost.

The same measurement located the real cost, and it is **not** classification:

| type | n | median completion | max |
|---|---|---|---|
| `play` | 34 | 394 | **15,539** |
| `target` | 7 | 6 | 467 |
| `prevent-effect` | 8 | 6 | 40 |

**`play` is ~60 % of decisions**, and a single one generated **15,539 tokens — about 50 seconds — for an
answer that is normally 394.** Hence the 16000 → 4000 smart budget (≈10× headroom over the median) plus
the no-reasoning overrun retry. After both, `play`'s worst case is 3,661 tokens.

## Levers, in order of expected value

1. **Adopt A + B for real play, after a quality check.** Both are built, opt-in, and measured to work
   (`TIER_FAST_TYPES=...,play-single` and `PLAN_EXECUTE=1`). Before either becomes the default, run a
   paired quality comparison — plan-then-execute executes the model's OWN stated intent, but its
   interruption signature is opponent-side only, so it cannot notice that a step stopped being right
   for a reason on our own side (a draw, a changed board).
2. **`play` remains the cost centre** even after A and B. It is ~60 % of decisions and the sole source of the
   multi-second tail. Two ways down: (a) route a *play* window to the fast tier when the menu offers no
   real sequencing choice, or (b) **plan-then-execute** — one smart call per turn that states the
   sequence, which the harness then executes step by step, re-planning only on interruption. (b) is the
   5–10× win and the architecture the original plan flagged. `targets` and `prevent-effect` are *mechanical*
   decisions and are sent to the **smart** tier — 13 of 40 decisions in the measurement, i.e. ~33 % of
   the expensive calls. A target menu with exactly one legal candidate is **forced** and should never
   reach a model at all. Safe version: auto when the menu is forced/single-option; route to `fast`
   when it is mechanical; keep `smart` for genuinely contested choices. This changes behaviour, so it
   needs its own acceptance run and re-pinned canaries.
2. **Smart-tier reasoning effort.** `reasoningEffort` is a per-model knob (default `medium` for
   deepseek/gpt). Lowering it cuts completion tokens but trades play quality — one measured game at
   `low` did *more* turns but also showed 3 truncation retries, so **do not infer a win from one
   seed**. Measure paired before changing the default.
3. **Output-length discipline.** The prompt asks for "at most two short sentences of reasoning", but
   a reasoning model's thinking is not in `content` — it is hidden tokens. Only the reasoning-effort
   knob actually bounds it.
4. **Prompt tokens (~5.1k).** Not the bottleneck here (prefill was not where the seconds went), but
   deepseek caches repeated prefixes server-side, so keeping the stable prefix *stable* is free money.
5. **Everything is tunable without a rebuild** via `OPENROUTER_MODEL_CFG`, e.g.

```bash
# per-model infra override (maxTokens, timeoutMs, reasoningEffort, temperature,
#                            fastMaxTokens, fastTimeoutMs, fastReasoningEffort)
OPENROUTER_MODEL_CFG='{"deepseek":{"reasoningEffort":"low","fastMaxTokens":1024}}' \
  python3 eval/run_batch.py ... --context-profile v4-faithful --memory-tools
```

## How to measure a change properly

Single games are dominated by variance (arm 3 did 54 decisions vs arm 2's 40 on the same seed). Use
the paired design and enough seeds that the per-decision distribution is stable:

```bash
python3 eval/run_batch.py --seeds 42..51 --pairs --jobs 1 --deck eval/decks/burn.txt \
  --smart-a openrouter:deepseek/deepseek-v4.1-flash --fast-a openrouter:deepseek/deepseek-v4.1-flash \
  --controller-b ai --context-profile v4-faithful --memory-tools \
  --out runs/perf/<arm> --cap-minutes 8
```

Then read `retry_ms`, `attempts`, `completion_tokens` and decisions-per-minute from the JSONL. The
acceptance suite's `canary` stages already guard the zero-model paths; latency work should not move
them.

## Provenance

`reasoningEffort`, `fastReasoningEffort`, `fastMaxTokens`, `fastTimeoutMs` are all in
`ModelCfg.canonical()`, so `model_cfg_sha` moves with them and runs configured differently cannot
pool. The changes above each carry a re-pinned `ContextProfileTest` literal for that reason.
