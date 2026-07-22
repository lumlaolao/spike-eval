# RUNBOOK — ab-quality-1 (OLD vs FIX) + stage0-calibration

Launched 2026-07-21 by the eval-runner agent. Three background `run_batch.py`
batches, all `--exploratory` (non-mirror decks + Forge-AI opponent + `--jobs>1`
all forbid cited mode), all LLM seat = identity **A "UR Delver"** vs opponent
seat = identity **B "Burn"**. Decks are the certification pairing
(`eval/decks/ur-delver.txt` piloted by the LLM, `eval/decks/burn.txt`).

| batch | dir | build | model (smart+fast) | opponent | seeds | games | jobs | cap |
|---|---|---|---|---|---|---|---|---|
| A-OLD | `runs/ab-quality-1/old` | OLD (pre-PR#5, `820f2fc`) | `openrouter:google/gemini-3.5-flash` | Forge AI (`ai`) | 301..310 ×swap | 20 | 2 | 20m |
| A-FIX | `runs/ab-quality-1/fix` | FIX (`c3ec3d2`, TASKS 1+2+4) | `openrouter:google/gemini-3.5-flash` | Forge AI (`ai`) | 301..310 ×swap | 20 | 2 | 20m |
| B-Stage0 | `runs/stage0-calibration` | STAGE0 (`339f457`, +grown preamble) | `openrouter:deepseek/deepseek-v4-pro` | Forge AI (`ai`) | 501..525 ×swap | 50 | 3 | 30m |

**A-OLD and A-FIX share the identical seed set (301..310, both swaps)** so the
A/B is paired seed-for-seed. Stage 0 uses a disjoint seed block (501..525).

## How the three builds run concurrently without a rebuild

Only the `forge.spike` package differs between OLD/FIX/STAGE0; the pinned Forge
engine classes in `~/code/forge/.../target/classes` are byte-identical across
all three (see each `runs/ab-builds/*/BUILD-INFO.txt`). So:

- **STAGE0** is what is currently compiled in `~/code/forge/.../target/classes`,
  so B-Stage0 runs the stock `~/code/forge/run-llm-headless.sh` unchanged
  (`--forge-home ~/code/forge`).
- **OLD / FIX** run from shadow forge-homes in the agent scratchpad
  (`$SCRATCH/forge-OLD`, `$SCRATCH/forge-FIX`): each is a dir holding a
  `forge-gui-desktop` **symlink** to the real one plus a copy of
  `run-llm-headless.sh` whose `CP=` line PREPENDS that build's
  `runs/ab-builds/<ARM>/spike-classes` so it shadows `target/classes`'s spike
  classes. `chdir()` through the symlink lands the JVM in the real
  `forge-gui-desktop` (physical cwd), so `target/classes` and `../forge-gui/res`
  resolve correctly; the absolute prepended dir wins for the spike package only.

**NEVER run `mvn compile` / `install.sh` while these batches run** — it would
swap `target/classes` under every live JVM (see `eval/CONCURRENCY.md` hazard #2).

### ⚠ Arm identity = the DIRECTORY, not the provenance stamp
Because OLD/FIX shadow the *compiled classes* but share `~/code/forge`'s
installed spike *source* (via the symlink), all three arms' game lines carry the
SAME `overlay_sha` (`6c3b0bde7cd3`), and OLD/FIX carry `engine_sha=unknown`
(their scratch forge-home is not a git repo). This is cosmetic: the runtime
bytecode differs correctly (verified — OLD decision rows LACK
`prompt_tokens`/`completion_tokens`, the TASK-4 field; FIX/STAGE0 have them).
Do NOT aggregate `old/` and `fix/` into one directory and do NOT tell the arms
apart by provenance — **the arm is which out-dir the file lives in.**

## Check completion (one-liners)

```bash
# per-batch: completed games / expected, and whether the orchestrator is alive.
# NOTE the jsonl is COMPACT json — match '"t":"game"' with NO space after the
# colon (a `"t": "game"` pattern silently matches nothing).
for d in runs/ab-quality-1/old runs/ab-quality-1/fix runs/stage0-calibration; do
  done=$(grep -l '"t":"game"' "$d"/game-*.jsonl 2>/dev/null | wc -l | tr -d ' ')
  pid=$(cat "$d/batch.pid" 2>/dev/null)
  alive=$(kill -0 "$pid" 2>/dev/null && echo RUNNING || echo EXITED)
  echo "$d: $done game lines | pid $pid $alive"
done

# a batch is fully done when batch.log ends with '=== batch summary ==='
tail -6 runs/ab-quality-1/old/batch.log
# live JVM count:
pgrep -fl LlmSpikeHeadless | wc -l
```

Expected totals: old=20, fix=20, stage0-calibration=50 game lines.
Live JVM count: `pgrep -c -f LlmSpikeHeadless`.

## A/B win rate — paired-seed comparison + sign test

```bash
python3 eval/analyze_ab_stage0.py ab runs/ab-quality-1/old runs/ab-quality-1/fix
```
Reports, over the LLM identity "UR Delver":
- each arm's standalone win rate vs Forge AI with a Wilson 95% CI (from
  `aggregate.py`);
- the paired table over games decisive in **both** arms at the same
  `(seed, swap)`: `both win / both loss / FIX-only win / OLD-only win`;
- the **net win-rate delta (FIX − OLD)** and an **exact two-sided McNemar/sign
  test** on the discordant pairs (FIX-only vs OLD-only). With ≤5 discordant
  pairs the test cannot reach p<0.05 — the script says so; treat small runs as
  directional. A positive net delta with p small = FIX improved play, not just
  plumbing.

Each arm's own seat-swap paired-bootstrap CI (LLM vs the AI opponent):
```bash
python3 eval/aggregate.py runs/ab-quality-1/old      # then .../fix
```
Note `aggregate.py`'s **fallback gate** may exit 2 on these dirs: FIX/STAGE0 log
every no-op priority PASS as a `tier=auto` row (OLD did not), which can push the
auto-fraction of LLM-identity decisions over the 10% ceiling even though the
model is playing every real decision. That is a logging-verbosity artifact, not
degraded play — pass `--max-fallback-rate 1.0` to `aggregate.py` to silence the
gate for these runs, or just read `analyze_ab_stage0.py`'s win-rate output which
does not enforce it.

## Stage 0 — tokens-per-game and cost

```bash
python3 eval/analyze_ab_stage0.py stage0 runs/stage0-calibration
```
Sums per-game `prompt_tokens` + `completion_tokens` over decision rows (null
rows — floor/auto/ai — ignored), then prints mean / median / p90 tokens-per-game
and model-calls-per-game, and costs the corpus three ways:
`openrouter_no_cache`, `deepseek_direct_no_cache`, `deepseek_direct_cache`
(static preamble = a cache HIT on every call after the first per game), each as
total / per-game / **per-10k-games**, plus the direct cache saving %.

**Pricing is PLACEHOLDER** (`--or-in/--or-out/--dd-in/--dd-cache-in/--dd-out`,
USD per 1M tokens; `--preamble-tokens`, default 2300). deepseek-v4-pro is a
forward roster model — plug real per-1M quotes in before quoting dollars:
```bash
python3 eval/analyze_ab_stage0.py stage0 runs/stage0-calibration \
  --or-in 0.30 --or-out 1.20 --dd-in 0.27 --dd-cache-in 0.07 --dd-out 1.10 \
  --preamble-tokens 2300
```
This Stage-0 run went via **OpenRouter** (no DeepSeek-direct key on this
machine). Token counts are provider-identical, so the direct-pricing columns are
valid projections; the eventual 10k-game run goes **provider-direct** to realize
the cache discount.

## Re-launch / resume

`run_batch.py` is resumable: a `(seed, swap)` whose jsonl already has a final
game line is skipped. To resume a killed batch, re-run the exact command below
(same `--out`). Source secrets first: `set -a; . secrets.env; set +a`.

```bash
# A-OLD (from repo root, secrets sourced):
python3 eval/run_batch.py --seeds 301..310 --pairs --exploratory --jobs 2 \
  --controller-a llm --controller-b ai \
  --smart-a openrouter:google/gemini-3.5-flash --fast-a openrouter:google/gemini-3.5-flash \
  --deck-a eval/decks/ur-delver.txt --deck-b eval/decks/burn.txt \
  --name-a "UR Delver" --name-b "Burn" --cap-minutes 20 \
  --forge-home "$SCRATCH/forge-OLD" --out runs/ab-quality-1/old
# A-FIX: swap forge-FIX + out=.../fix. B-Stage0: forge-home ~/code/forge,
#   --smart-a/--fast-a openrouter:deepseek/deepseek-v4-pro, seeds 501..525,
#   --jobs 3 --cap-minutes 30, --out runs/stage0-calibration.
```
`$SCRATCH` = the agent scratchpad; the shadow forge-homes are ephemeral. If gone,
recreate: `mkdir` the dir, symlink `forge-gui-desktop -> ~/code/forge/forge-gui-desktop`,
copy `run-llm-headless.sh` and prepend `runs/ab-builds/<ARM>/spike-classes:` to its `CP=`.
