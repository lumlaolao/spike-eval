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

## Levers, in order of expected value

1. **Tier routing** (not done; the next real win — and now the ONLY remaining big one). The
   measurement shows 26–30 of 40 decisions went to the **smart** tier (~7 s each, so ~4 smart calls
   per turn ≈ 28 s/turn) while the fast tier got 1–5. `play` (19–22) and `target`/`targets` (7–8) are
   the bulk. The question to settle with data is which of those are *planning* (smart) and which are
   *classification over a legal menu* (fast). `targets` and `prevent-effect` are *mechanical*
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
  --smart-a openrouter:deepseek/deepseek-v4.1-flash --context-profile v4-faithful --memory-tools \
  --out runs/perf/<arm> --cap-minutes 8
```

Then read `retry_ms`, `attempts`, `completion_tokens` and decisions-per-minute from the JSONL. The
acceptance suite's `canary` stages already guard the zero-model paths; latency work should not move
them.

## Provenance

`reasoningEffort`, `fastReasoningEffort`, `fastMaxTokens`, `fastTimeoutMs` are all in
`ModelCfg.canonical()`, so `model_cfg_sha` moves with them and runs configured differently cannot
pool. The changes above each carry a re-pinned `ContextProfileTest` literal for that reason.
