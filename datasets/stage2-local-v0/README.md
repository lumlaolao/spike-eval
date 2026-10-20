# stage2-local-v0 — multi-source SFT mix

> **2026-10-20:** the training examples (`train.jsonl`, `valid.jsonl`) were removed with the rest of the training
> setup; the repository is a benchmark now. This `meta.jsonl` stays: it records which seeds the corpus used, and
> the held-out seed guard (`eval/seed_registry.py`, `eval/check_holdouts.py`) refuses them for evaluation.

Built by `eval/make_sft_data.py` (Stage-2 multi-source mode). Mixes
ai-log self-play teacher rows (win-filtered, canonical terse targets),
prebuilt CoT/puzzle slices, and (optionally) Stage-1 winning
trajectories, under CLI weights + a stratified global cap.

## Exact command

```
python3 eval/make_sft_data.py --source ai-teacher:runs/d1-selfplay --source prebuilt:datasets/puzzle-cot-v0 --source prebuilt:datasets/stage1-v1-smart-mlx --out-dir datasets/stage2-local-v0 --cap 13500 --ai-filter win+rare
```

## Totals

- examples: **13500** (train 12168 / valid 1332)
- by source: ai-teacher=12041, prebuilt=1459
- split: BY GAME for ai-teacher/win (both swaps together); prebuilt
  slices keep their original split. split_seed=20260722 val_frac=0.1.
- ai-teacher targets are choice-only terse (no reasoning, no <think>);
  count-style policy = `first-k`.

## Counts by (source, deck, type)

| source | deck | type | n |
|---|---|---|---:|
| ai-teacher | Ant Storm | discard | 113 |
| ai-teacher | Ant Storm | targets | 45 |
| ai-teacher | Ant Storm | zone-change | 156 |
| ai-teacher | Burn | attackers | 156 |
| ai-teacher | Burn | blockers | 156 |
| ai-teacher | Burn | discard | 9 |
| ai-teacher | Burn | mulligan | 156 |
| ai-teacher | Burn | play | 156 |
| ai-teacher | Burn | sacrifice | 1 |
| ai-teacher | Burn | targets | 24 |
| ai-teacher | Burn | zone-change | 83 |
| ai-teacher | Cephalid Breakfast | attackers | 156 |
| ai-teacher | Cephalid Breakfast | blockers | 156 |
| ai-teacher | Cephalid Breakfast | entity | 155 |
| ai-teacher | Cephalid Breakfast | mulligan | 106 |
| ai-teacher | Cephalid Breakfast | play | 156 |
| ai-teacher | Cephalid Breakfast | zone-change | 156 |
| ai-teacher | Death and Taxes | attackers | 156 |
| ai-teacher | Death and Taxes | blockers | 156 |
| ai-teacher | Death and Taxes | discard | 4 |
| ai-teacher | Death and Taxes | entity | 3 |
| ai-teacher | Death and Taxes | modes | 156 |
| ai-teacher | Death and Taxes | mulligan | 156 |
| ai-teacher | Death and Taxes | play | 156 |
| ai-teacher | Death and Taxes | sacrifice | 21 |
| ai-teacher | Death and Taxes | zone-change | 156 |
| ai-teacher | Doomsday | attackers | 43 |
| ai-teacher | Doomsday | blockers | 17 |
| ai-teacher | Doomsday | discard | 156 |
| ai-teacher | Doomsday | entity | 156 |
| ai-teacher | Doomsday | mulligan | 10 |
| ai-teacher | Doomsday | play | 156 |
| ai-teacher | Doomsday | targets | 2 |
| ai-teacher | Doomsday | zone-change | 156 |
| ai-teacher | Elves | attackers | 156 |
| ai-teacher | Elves | blockers | 156 |
| ai-teacher | Elves | discard | 2 |
| ai-teacher | Elves | entity | 1 |
| ai-teacher | Elves | mulligan | 156 |
| ai-teacher | Elves | play | 156 |
| ai-teacher | Elves | sacrifice | 37 |
| ai-teacher | Elves | zone-change | 156 |
| ai-teacher | Food Chain | attackers | 156 |
| ai-teacher | Food Chain | blockers | 156 |
| ai-teacher | Food Chain | entity | 156 |
| ai-teacher | Food Chain | mulligan | 156 |
| ai-teacher | Food Chain | play | 156 |
| ai-teacher | Food Chain | targets | 4 |
| ai-teacher | Goblins | attackers | 156 |
| ai-teacher | Goblins | blockers | 156 |
| ai-teacher | Goblins | entity | 63 |
| ai-teacher | Goblins | mulligan | 156 |
| ai-teacher | Goblins | play | 156 |
| ai-teacher | Goblins | sacrifice | 35 |
| ai-teacher | Goblins | targets | 20 |
| ai-teacher | Goblins | zone-change | 156 |
| ai-teacher | Grixis Reanimator | attackers | 64 |
| ai-teacher | Grixis Reanimator | blockers | 147 |
| ai-teacher | Grixis Reanimator | discard | 156 |
| ai-teacher | Grixis Reanimator | entity | 156 |
| ai-teacher | Grixis Reanimator | mulligan | 69 |
| ai-teacher | Grixis Reanimator | play | 156 |
| ai-teacher | Grixis Reanimator | targets | 16 |
| ai-teacher | Infect | attackers | 156 |
| ai-teacher | Infect | blockers | 156 |
| ai-teacher | Infect | discard | 11 |
| ai-teacher | Infect | mulligan | 155 |
| ai-teacher | Infect | play | 155 |
| ai-teacher | Infect | sacrifice | 4 |
| ai-teacher | Infect | zone-change | 155 |
| ai-teacher | Lands | discard | 155 |
| ai-teacher | Lands | mulligan | 29 |
| ai-teacher | Lands | play | 155 |
| ai-teacher | Lands | targets | 23 |
| ai-teacher | Mardu Energy | attackers | 155 |
| ai-teacher | Mardu Energy | blockers | 140 |
| ai-teacher | Mardu Energy | discard | 155 |
| ai-teacher | Mardu Energy | entity | 31 |
| ai-teacher | Mardu Energy | mulligan | 155 |
| ai-teacher | Mardu Energy | play | 155 |
| ai-teacher | Miracles | attackers | 155 |
| ai-teacher | Miracles | blockers | 155 |
| ai-teacher | Miracles | mulligan | 155 |
| ai-teacher | Miracles | play | 155 |
| ai-teacher | Miracles | zone-change | 155 |
| ai-teacher | Painter | attackers | 155 |
| ai-teacher | Painter | blockers | 126 |
| ai-teacher | Painter | entity | 155 |
| ai-teacher | Painter | modes | 155 |
| ai-teacher | Painter | mulligan | 155 |
| ai-teacher | Painter | play | 155 |
| ai-teacher | Painter | zone-change | 155 |
| ai-teacher | Sneak and Show | attackers | 72 |
| ai-teacher | Sneak and Show | entity | 155 |
| ai-teacher | Sneak and Show | mulligan | 155 |
| ai-teacher | Sneak and Show | play | 155 |
| ai-teacher | Sneak and Show | targets | 7 |
| ai-teacher | Sneak and Show | zone-change | 155 |
| ai-teacher | UR Delver | attackers | 155 |
| ai-teacher | UR Delver | blockers | 79 |
| ai-teacher | UR Delver | mulligan | 155 |
| ai-teacher | UR Delver | play | 155 |
| ai-teacher | UR Delver | targets | 1 |
| ai-teacher | UR Delver | zone-change | 155 |
| prebuilt | puzzle-cot-v0 | prebuilt | 122 |
| prebuilt | stage1-v1-smart-mlx | prebuilt | 1337 |

## Mix / cap

```
{
  "per_source_caps": {},
  "global_cap": 13500,
  "raw": {
    "ai-teacher:runs/d1-selfplay": 539143,
    "prebuilt:datasets/puzzle-cot-v0": 122,
    "prebuilt:datasets/stage1-v1-smart-mlx": 1337
  },
  "after_source_cap": {
    "ai-teacher:runs/d1-selfplay": 539143,
    "prebuilt:datasets/puzzle-cot-v0": 122,
    "prebuilt:datasets/stage1-v1-smart-mlx": 1337
  },
  "final": {
    "prebuilt:datasets/puzzle-cot-v0": 122,
    "prebuilt:datasets/stage1-v1-smart-mlx": 1337,
    "ai-teacher:runs/d1-selfplay": 12041
  },
  "raw_pre_dedup": {
    "ai-teacher:runs/d1-selfplay": 539213,
    "prebuilt:datasets/puzzle-cot-v0": 122,
    "prebuilt:datasets/stage1-v1-smart-mlx": 1337
  },
  "dedup_dropped": 70
}
```

## Hygiene (per source)

- **ai-teacher:runs/d1-selfplay**: drop_blocks-underdetermined=3604, drop_fallback=820, emit_blocks-none=4494, emit_choice=98809, emit_count-all=12183, emit_count-first-k=7194, emit_count-none=1418, emit_from_losing_seat=10609, emit_mulligan-word=6372, emit_pass=408743, skip_game_draw_or_invalid=2, skip_loser_common_type=439208
- **prebuilt:datasets/puzzle-cot-v0**: emit=122
- **prebuilt:datasets/stage1-v1-smart-mlx**: emit=1337

## Canonical ai-teacher targets

Assistant text is the exact final line the harness would parse
(`CHOICE:`/`PICKS:`/`BLOCKS:`/`KEEP`/`MULLIGAN`), derived from
`LlmSpikeGui.java`. PASS (choice=-1, play) -> `CHOICE: <n_options>`;
decideOne decline -> `CHOICE: <n_options+1>`; subset size k ->
`PICKS: 1..k` (exact at k in {0,n}; representative first-k
otherwise); blockers k>0 dropped (pairing not logged); unknown
types dropped.

## Warnings (3)

- runs/d1-selfplay[food-chain__vs__burn]: game-5800-1784842024435.jsonl: 2 game lines in one file -> skipped (cannot attribute decisions)
- runs/d1-selfplay[grixis-reanimator__vs__elves]: game-3046-1784838276426.jsonl: 2 game lines in one file -> skipped (cannot attribute decisions)
- runs/d1-selfplay[miracles__vs__painter]: game-4645-1784846773004.jsonl: 2 game lines in one file -> skipped (cannot attribute decisions)
