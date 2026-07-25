# DAgger round 1 — deferred fixes & follow-ups

Living list of things noticed mid-run and deliberately deferred (editing a running
bash script corrupts it; fixes land when the driver is idle). Ordered by dependency.

## 1. Watchdog: busy ≠ dead (REQUIRED before straggler pass)
The heartbeat probe queues behind sequential generations (`--decode-concurrency 1`),
times out at 60s, and 3 strikes kill the game — so the watchdog **systematically
censors the longest games**. Evidence: `infect-vs-sneak-and-show` seed 9100 killed
3× consecutively; `goblins-vs-miracles` similar. Fix in `dagger_round1_v2.sh`
(`~/.claude/jobs/f9e4d46b/tmp/`): before counting a strike, check whether
`$SCRATCH/mlx.log` grew in the last 60s — actively generating means busy, not dead.

## 2. Watchdog: pause during planned reloads
Per-cell server recycles (~30s model reload) log spurious UNHEALTHY strikes
(`rss=deadKB` entries). Driver should touch `$SCRATCH/reloading` before
`start_server` and remove it after; heartbeat skips probes while it exists.

## 3. Straggler pass (after 1+2)
Re-run the driver; resume fills only the missing games. Known short cells:
`goblins-vs-miracles` 5/6, `infect-vs-sneak-and-show` 5/6 — final list via
`grep INCOMPLETE driver.log`. Without fix #1 the same seeds fail the same way.

## 4. AWS g5 first-contact verification (on quota approval)
Every GPU-side step of `cloud/bootstrap_g5.sh` is unverified until a real
instance exists (quota cases 178495592200835 / 178495592800636, watcher polling).
Confirm with user before launching (bills their card). Acceptance: seed-42 floor
canary = A/108 + LLM smoke test with >0 model-backed decisions on the box.

## 5. Corpus close-out
Final audit (games / labels-by-type / zero contamination), then dataset build
with `--count-style exact-only`, retrain, re-eval on frozen seeds 501–525 only
(this run's capped-game win rates are a biased pool — do not quote them).

## Banked lessons this run (already in journal/CLAUDE.md, kept here for context)
- Cell completion gate must check EXPECTED GAME COUNT, not just validate existing
  files (the missing-classpath failure marched through 32 cells reporting success).
- Raw eval jsonl is COMPACT (`"t":"game"`); any grep with `": "` never matches.
- `run_batch --exploratory` does not absolutize deck paths — pass absolute paths.
- /tmp does not survive reboots: `forge_cp.txt`, driver scripts, logs must live
  in durable dirs (driver regenerates the classpath at startup now).
- mlx server: concurrent requests batch at dim 2 and crash the generator —
  `--decode-concurrency 1 --prompt-concurrency 1` is mandatory on this stack.
