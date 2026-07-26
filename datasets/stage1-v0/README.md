# stage1-v0 — winning-trajectory SFT data

MLX chat-format SFT set built by `eval/make_sft_data.py` from winning
MTG Forge harness trajectories. One example per model-answered decision
from a seat the LLM piloted **and won**.

## Exact command

```
python3 eval/make_sft_data.py runs/stage0-calibration:STAGE0 runs/ab-quality-1/fix:FIX
```

## Sources

- `runs/stage0-calibration` (build STAGE0)
- `runs/ab-quality-1/fix` (build FIX)

## Counts

| split | examples | smart | fast | games |
|-------|---------:|------:|-----:|------:|
| train | 878 | 520 | 358 | 19 |
| valid | 106 | 50 | 56 | 2 |
| **total** | **984** | **570** | **414** | **21** |

Smart-tier rows are the primary set. Fast-tier rows use the terse FORMAT
line (no STATIC_REFERENCE) and are tagged `tier="fast"` in `meta.jsonl`
so a downstream mix weight can be tuned.

## Files

- `train.jsonl` / `valid.jsonl` — MLX chat format:
  `{"messages":[{"role":"system",...},{"role":"user",...},{"role":"assistant",...}]}`.
- `meta.jsonl` — per-example provenance aligned by line. Ordered as the
  train block followed by the valid block; each carries `split` and a
  1-based `line` within its block, so `meta` rows with `split="train"`
  align line-for-line to `train.jsonl` (likewise valid). Fields:
  source_dir, source_file, seed, swap, winner, llm_identity, seat,
  model, build, build_commit, engine_sha, overlay_sha, deck_sha, tier,
  decision_index, type, choice.

## Construction contract

- **system** = the game's exact teacher preamble, reconstructed
  programmatically from `framing()` / `STATIC_REFERENCE` in
  `LlmSpikeGui.java` (byte-exact, self-checked against known char
  counts). STAGE0 smart rows get the STATIC_REFERENCE appendix; FIX
  smart rows and all fast rows do not.
- **user** = the decision row `prompt` (verbatim state snapshot).
- **assistant** = the decision row `raw_reply` (verbatim winning reply).
  Replies are never rewritten.

## Hygiene

- Only COMPLETE games (a `t:game` summary line present) are read;
  in-flight files appended by live batches are skipped.
- Reruns of a (seed, swap) are deduped keeping the newest file.
- Rows dropped for quality: 0 no-reply, 0 fallback, 1 truncated.
- Near-duplicate `(tier, seat, normalized-state, reply)` examples
  deduped: **12 dropped**. State is normalized for the dedup KEY only
  by collapsing space-preceded Forge object ids ` (<digits>)` -> ` (#)`;
  emitted prompts stay verbatim.
- Train/valid split is BY GAME (both swap variants of a seed together,
  leakage-free), deterministic via a stable hash of the game key.
