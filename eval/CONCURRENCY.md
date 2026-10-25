# Concurrency: how many Forge/LLM games can this Mac safely run at once

**What holds now:** the ceilings in section 7, counted across every batch running on the machine. Count
them yourself: `run_batch.py`'s `warn_parallel_jobs()` compares only its own batch's `--jobs` with them
(above 8 for a no-LLM batch, above 10 for `openrouter:`) and only warns. When a rebuild is safe:
`.claude/rules/forge-overlay.md`. The rest of this file is the measurement record of 2026-07-21 on the
owner's Mac (24 GiB); its `file:line` citations are to the code of that date, and a machine with other
memory scales the RAM-bound ceilings with it.

Measured 2026-07-21 against the **live** processes of an in-progress
`model_sweep.py` run (PID 17835 driving `run_batch.py` PID 18517, which had
spawned one `LlmSpikeHeadless` JVM, PID 18519, playing the grok-4.5-vs-floor
game). No benchmark games were launched to produce these numbers — this is a
read+measure pass only.

## 1. Hardware budget

```
hw.ncpu                   18   (Apple M5 Pro)
hw.perflevel0.physicalcpu  6   (P-cores)
hw.perflevel1.physicalcpu 12   (E-cores)
hw.memsize                24 GiB unified memory
```

At measurement time: 81% system-wide memory free (vm_stat/memory_pressure),
so roughly 19 GiB free with everything else already running. "Everything
else" on this machine day-to-day: several `claude` CLI sessions (this agent
plus background ones — RSS 240–800 MB each, low idle CPU), Claude desktop
app (Electron, several helper processes, ~1–1.3 GB combined), Chrome (a
dozen renderer helpers, 150–450 MB each), Steam + helpers (~1 GB combined),
iTerm2. None of this is CPU-heavy at idle, but it already occupies several
GB of RAM headroom that any concurrency plan needs to leave alone.

## 2. Actual per-game footprint (measured on the live JVM)

`pgrep -fl LlmSpikeHeadless` → one process, PID 18519, launched via
`run-llm-headless.sh` with `-Xmx4096m`.

30-second `ps` sampling (10s intervals):

| t    | RSS      | VSZ      | %CPU |
|------|----------|----------|------|
| 0s   | 878 MB   | 421 GB (mmap'd, not committed) | 0.0% |
| 10s  | 879 MB   | "        | 0.1% |
| 20s  | 881 MB   | "        | 0.6% |
| 30s  | 889 MB   | "        | 0.2% |

`top -pid 18519 -stats th` → **61 threads**, almost all parked.

**Only ~21% of the requested 4096m heap is actually resident**, and CPU
usage is near-zero. This matches the game's nature: an LLM-controlled seat
spends its wall-clock time blocked on network I/O (OpenRouter HTTP round
trip or a local `claude` CLI subprocess), not computing.

**Floor vs. LLM duration, quantified** (from `runs/improve/validate/` and
`runs/improve/iter-2/`, using the epoch-ms embedded in each `.jsonl`
filename as start time and the paired `.log` mtime as end time):

| Run                  | Start → end            | Duration |
|----------------------|-------------------------|----------|
| floor-vs-floor seed41 | 12:53:33.5 → 12:54:00   | **~27s** |
| floor-vs-floor seed42 | 12:55:48.1 → 12:56:13   | **~25s** |
| LLM game (iter-2) 111 | 13:35:14.6 → 13:47:32   | **~737s (12m17s)** |
| LLM game (iter-2) 112 | 13:47:35.9 → 14:01:22   | **~826s (13m46s)** |

Floor-vs-floor games are **~28–30x shorter** than LLM games, and are
CPU-bound (Forge AI search pegs a core for the full ~25s), while LLM games
are almost entirely idle wall-clock spent waiting on a provider — confirmed
by the 0–0.6% CPU reading above during an in-progress LLM game.

**Implication:** RAM, not CPU, is the binding constraint for LLM-game
concurrency; CPU is the binding constraint for floor-vs-floor concurrency
(and only for ~25s bursts per game).

## 3. Tooling concurrency support (code read)

- **`eval/run_batch.py`** already has a `--jobs N` flag
  (`run_batch.py:396-398`) implemented via `ThreadPoolExecutor`
  (`run_batch.py:501-509`) — each job is a separate `subprocess.run(["bash",
  "run-llm-headless.sh"], ...)`, i.e. a separate JVM. **Cited mode refuses a seat on the Claude CLI** (a bare
  claude-CLI spec or `claude-cli:`: a development lane, owner 2026-10-24; it forced `--jobs 1` for them before that);
  all-`openrouter:` runs may be parallel since 2026-10-17, and it prints a warning above
  `--jobs 2` even in `--exploratory` mode (`run_batch.py:475-478`,
  `eval/README.md:87`). This guard is deliberate and already correctly
  targets the real hazard (see the `claude` CLI point below) — it's not a
  guess.
- **Isolation that's already fine:** `run_one()` (`run_batch.py:196-231`)
  writes `game-<seed>-<swap>.log`/`.jsonl` under `out_dir`, keyed purely by
  `(seed, swap)`. `build_env()` (`run_batch.py:151-193`) returns a **fresh
  env dict per call**, not a shared mutable one. So `--jobs > 1` across
  *different* seed/swap combos is filesystem- and env-race-free by
  construction. `model_sweep.py`'s `sweep_one()` (`model_sweep.py:70-120`)
  already isolates each model into its own `out_dir/<model-name>/`
  subdirectory, so nothing there collides either.
- **`eval/model_sweep.py`** is currently strictly sequential — a plain
  `for i, spec in enumerate(models): verdicts.append(sweep_one(...))` loop
  (`model_sweep.py:139-140`). No `--jobs` flag exists.
- **`eval/improve_loop.py`** shells out to `run_batch.py` once per
  invocation (`improve_loop.py:100-116`, `162`); it inherits whatever
  `--jobs` behavior `run_batch.py` has but never sets one itself (implicit
  default 1).

### Shared-state hazards found

1. **`/tmp/forge_cp.txt` (shared classpath file) — real hazard, unguarded.**
   `run-llm-headless.sh:12`: `CP="target/classes:$(cat /tmp/forge_cp.txt)"`.
   It's read fresh at every JVM launch and is otherwise inert (10.5 KB, pure
   jar-path list) — fine for concurrent *reads*. But it's regenerated by
   `mvn dependency:build-classpath -Dmdep.outputFile=/tmp/forge_cp.txt`
   (`README.md:174`), and nothing stops that command from running while
   games are in flight. A rebuild mid-batch races a `cat` against a
   `mvn`-in-progress write of the same file — no lock, no guard anywhere in
   `run_batch.py`/`model_sweep.py`/`improve_loop.py`.
2. **Shared `forge-gui-desktop/target/classes` (and the sibling
   `forge-core`/`forge-game`/`forge-ai`/`forge-gui` module `target/classes`
   dirs) — the real concurrency hazard, unguarded.** `run-llm-headless.sh:10`
   does `cd "$(dirname "$0")/forge-gui-desktop"` before launching, so **every
   concurrent JVM shares the same CWD and the same compiled-classes
   directories** on the classpath (`run-llm-headless.sh:12`, first path
   segment `target/classes`). Running `mvn compile`/`install.sh` while any
   game (LLM or floor) is mid-flight can overwrite `.class` files out from
   under a running JVM's classloader — `NoClassDefFoundError`/verification
   errors or silently-stale behavior are both possible. **This is the one
   hazard worth fixing before raising concurrency**, and it exists even
   today at `--jobs 1` if a rebuild happens to land mid-game; it just gets
   more likely to bite as more games overlap in time.
3. **Forge's shared per-user profile/cache dir — present but low-risk for
   this workload.** No `forge.profile.properties` override exists next to
   the installed spike (`forge-gui-desktop/forge.profile.properties` is
   absent), so `ForgeProfileProperties.getDefaultDirs()`
   (`forge/forge-gui/.../ForgeProfileProperties.java:160-183`) falls back to
   the platform default — confirmed on disk at
   `~/Library/Application Support/Forge/` (11 timestamped `forge.*.log`
   files found there). `ForgeConstants.java:246-252` derives
   `USER_PREFS_DIR`, `LOG_FILE`, `NETWORK_LOGS_DIR`, `ACHIEVEMENTS_DIR` from
   that **one shared directory, independent of which repo/worktree launched
   the JVM**. This is shared across *every* concurrent JVM regardless of
   `--jobs`. It's read-mostly and already warm (card DB/pics cache is
   pre-populated from prior runs) and the main log filename is
   timestamp-keyed per launch, so collisions are unlikely for the pure
   headless spike path — `LlmSpikeHeadless.java`'s `main()` never calls a
   preferences-`save()` (that only happens via
   `ForgeProfileProperties.setUserDir/setCacheDir`,
   `ForgeProfileProperties.java:88-91`, not exercised here). Flag as
   low-probability, not zero — anything that *does* start writing
   quest/achievement/preference state concurrently would hit this.
4. **Output file/seed collisions — not a real hazard as built.** Covered
   above: `(out_dir, seed, swap)` keying plus per-call fresh env dicts make
   this safe. `SWAP_SEATS` is explicitly cleared per-call
   (`run_batch.py:160-163`) rather than inherited, so no leak between
   parallel workers.
5. **GAME_SEED/port collisions — no evidence of a listener.** `jetty-server`
   and `org.jupnp` jars are on the classpath (transitively, for Forge's LAN
   multiplayer feature) but `LlmSpikeHeadless.java`'s `main()` never touches
   networking beyond the LLM bridges — no socket bind observed in the 74
   lines of that file. Treat as low risk; this was a static read, not a
   runtime trace with two concurrent JVMs.
6. **The `claude` CLI — the actual reason cited mode pins `--jobs 1`,
   confirmed by code.** `LlmSpikeGui.java:2463-2498`: any model spec **not**
   prefixed `openrouter:` is run via
   `new ProcessBuilder(BIN, "-p", "--model", model, "--strict-mcp-config", ...)`
   where `BIN` defaults to `"claude"` (`LlmSpikeGui.java:2463`) — i.e. it
   shells out to the same local Claude Code CLI that this very agent and
   the user's other sessions run as. `openrouter:`-prefixed specs instead go
   through a direct `HttpClient` call to `openrouter.ai`
   (`LlmSpikeGui.java:2664-2805`), never touching the local CLI. **The
   current live sweep uses only `openrouter:`-prefixed specs**
   (`model_sweep.py:32-41` — even the opus/fable-5 rows are
   `openrouter:anthropic/claude-opus-4.8` and
   `openrouter:anthropic/claude-fable-5`), so *this particular sweep* does
   not contend with the user's interactive Claude Code sessions at all. The
   hazard is real only when a run uses **bare** specs like `haiku`/`opus`
   (as `eval/README.md:115-116`'s example does) — that's exactly the
   scenario `--jobs 1` in cited mode, and the `--jobs 2` warning in
   exploratory mode, are protecting against.
7. **macOS App Nap / headless AWT — not applicable.** `LlmSpikeHeadless`
   never creates a window (`GuiDesktop` headless interface, no `JFrame`
   shown), so it isn't a foreground/occluded GUI app and isn't a candidate
   for App Nap throttling. No action needed.

## 4. API-side limits

Per OpenRouter's current docs (see sources): **paid models have no
OpenRouter-imposed RPM limit** — a 429 on a paid model comes from the
*upstream provider*, not OpenRouter itself. Free (`:free`-suffixed) model
variants are capped at 20 req/min and 50–1,000 req/day depending on
lifetime credit purchases — none of the models in `model_sweep.py`'s
roster use `:free` variants, so that ceiling doesn't apply here. The
practical constraint is therefore **upstream provider queueing/latency**,
not a hard request-count wall, for as long as the OpenRouter key is on a
paid tier.

This lines up with the measured behavior: an LLM game is ~99% idle CPU,
waiting on exactly one thing — the provider's response. Kimi-k3's ~50s/
decision latency (per the task's context) means a kimi game simply takes
longer wall-clock per decision; it does not consume more local CPU/RAM
while waiting, so running it *concurrently* with a faster model (rather
than serially, as `model_sweep.py` does today) costs nothing locally and
saves real wall-clock time on the sweep.

**Practical ceiling:** local RAM/CPU allow far more parallel LLM games than
is useful; the softer limits are (a) not wanting to burst so many
simultaneous requests at one provider that its queue/latency degrades for
all of them, and (b) — only when bare (non-`openrouter:`) specs are used —
not wanting to starve the user's interactive `claude` CLI sessions.

## 5. Recommendations

> **Superseded upward by §7 (2026-07-21 evening):** the first real parallel
> run validated these numbers with headroom to spare; the current ceilings
> are cheap-LLM **6–10**, mixed **4–6**, floor **4–8**, bare-CLI still **1**.
> The table below is kept as the original conservative derivation.

| Scenario | Safe `--jobs` | Why |
|---|---|---|
| Floor-vs-floor (both controllers `floor`) | **4–6** | CPU-bound, ~25s bursts. 6 P-cores + 12 E-cores comfortably absorb 4–6 concurrent ~25s AI-search bursts alongside existing desktop load; RAM is a non-issue (~1 GB/JVM × 6 ≈ 6 GB of 24 GB). |
| Cheap-LLM (e.g. both seats `openrouter:google/gemini-3.5-flash`) | **4–6** | Latency-bound, ~0.2–0.6% CPU/JVM, ~900 MB RSS/JVM measured. CPU is a non-issue; RAM for 6 JVMs (~5–6 GB) leaves comfortable headroom. Paid-tier OpenRouter has no hard RPM wall, so the limiter is just "don't be rude to one provider's queue" — 4–6 concurrent requests to a fast, cheap model is unremarkable. |
| Mixed sweep (one seat floor, one seat LLM) — today's `model_sweep.py` pattern | **3–4** for `run_batch.py`; **2–3 different models at once** if `model_sweep.py` grows a `--jobs` flag | Same low per-JVM footprint; the floor seat adds brief CPU bursts. Because every model in the roster is `openrouter:`-prefixed, there's no `claude`-CLI contention to worry about — the constraint is just RAM/CPU headroom, which supports more than the current strictly-sequential 1. |
| Expensive reasoners / **any bare (non-`openrouter:`) model spec** (e.g. plain `haiku`, `opus`, `fable-5`) | **1** (2 only with caution) | These route through the local `claude` CLI (`LlmSpikeGui.java:2463-2498`) and directly compete with the user's own interactive Claude Code sessions for the same CLI/account. This matches — and validates — the existing cited-mode guard forcing `--jobs 1` (`run_batch.py:298-301`). Don't raise this one without first moving those model tiers to `openrouter:`-routed equivalents. |

### Hazards to fix before raising concurrency

1. **Build-vs-run race on `target/classes`** (`run-llm-headless.sh:10-12`) —
   real, unguarded, gets worse with more concurrent games. Fix: a simple
   lockfile (e.g. `flock /tmp/forge_build.lock -c 'mvn ...'`) that
   `install.sh`/manual rebuilds must hold, and a cheap guard in
   `run_batch.py` that snapshots `target/classes`'s mtime at batch start and
   warns/refuses if it changes mid-batch. Until fixed: **never rebuild
   while any game is running**, regardless of `--jobs`.
2. **`/tmp/forge_cp.txt` regeneration mid-run** (`README.md:174`) — same
   category as #1, same fix (hold the same lock around
   `dependency:build-classpath`).
3. **Bare (non-`openrouter:`) model specs under `--jobs > 1`** — not a
   filesystem race, but a real resource-contention hazard against the
   user's own Claude Code sessions. Cited mode already blocks this
   (`--jobs 1` forced); exploratory mode only warns above `--jobs 2`
   (`run_batch.py:475-478`) — consider hard-refusing `--jobs > 1` in
   exploratory mode too when any model spec lacks the `openrouter:` prefix.
4. Everything else investigated (output-file keying, `SWAP_SEATS` leakage,
   port binding, App Nap) checked out as **not** a real hazard for this
   workload.

### `-Xmx` recommendation

Measured steady-state RSS on the live LLM game: **~850–890 MB**, against a
requested `-Xmx4096m` (`run-llm-headless.sh:36`, also in the raw `ps` output
for PID 18519). Only ~21% of the reserved heap is in use. Recommend
lowering to **`-Xmx1536m`** (roughly 1.7x the observed peak, leaving margin
for a deeper game state than this one sample happened to reach) rather than
cutting all the way to the observed value from a single mid-run sample.
This directly multiplies safe concurrent-JVM headroom: at 4096m requested,
a naive "worst case all JVMs hit their cap" budget supports ~24GB/4GB = 6
JVMs; at 1536m it supports ~24GB/1.5GB ≈ 16. (Actual committed RSS is much
lower than either cap in practice, per the measurements above — this is a
safety-margin change, not a response to observed OOM risk.) Before locking
this in, sample RSS across a couple of *full* games (not just one snapshot
mid-run) to confirm no late-game spike (e.g. many permanents/large board
states) pushes past 1.5 GB.

### Should `model_sweep.py` gain a `--jobs` flag?

Yes — sketch, not implemented:

```python
ap.add_argument("--jobs", type=int, default=1,
                help="run this many models' sweep_one() calls in parallel "
                     "(each spawns its own run_batch.py -> JVM). Safe range "
                     "2-3 given measured per-JVM footprint (~900MB RSS, "
                     "near-zero CPU while network-bound); do not raise above "
                     "3 without checking every DEFAULT_MODELS entry still "
                     "uses the openrouter: prefix (see eval/CONCURRENCY.md).")
...
if args.jobs <= 1:
    verdicts = [sweep_one(spec, args, args.seed + i) for i, spec in enumerate(models)]
else:
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futs = {pool.submit(sweep_one, spec, args, args.seed + i): spec
                for i, spec in enumerate(models)}
        verdicts = [futs_result_in_original_order(...)]  # preserve roster order for the printed table
```

Mirrors `run_batch.py`'s existing `ThreadPoolExecutor` pattern
(`run_batch.py:505-509`). No new isolation work needed: `sweep_one()`
already gives each model its own `out_dir` subdirectory
(`model_sweep.py:72-73`) and its own `run_batch.py` subprocess/JVM, and each
model gets a distinct seed (`args.seed + i`, `model_sweep.py:140`) so
there's no seed collision either. The only precondition is hazard #1/#2
above (don't rebuild mid-sweep) and hazard #3 (verify no roster entry is a
bare spec before enabling `--jobs > 1`).

## 6. Do you need containers (Docker/OrbStack/Apple containerization)?

**Verdict: no.** Containerize nothing for this project as it stands.

**What containers would buy:** true filesystem isolation per job (own
`target/classes`, own `/tmp/forge_cp.txt`-equivalent, own Forge profile/cache
dir — solving hazards #1–#3 above by construction), reproducible JVM
environment, and cgroup-enforced CPU/memory caps per game.

**What they'd cost on this Mac:**
- Docker Desktop/OrbStack on Apple Silicon run a Linux VM
  (Virtualization.framework-backed). That VM reserves a fixed slice of the
  24 GB unified memory for itself *before* any container starts — directly
  cutting into the same RAM budget this report is trying to stretch across
  more concurrent JVMs.
- No Metal passthrough is needed here (the spike is headless AWT, no
  rendering), so that's not a real cost — but bind-mounting the `res/`
  asset tree (thousands of small files Forge reads at startup) through
  gRPC-FUSE/virtiofs is measurably slower than native disk, adding to every
  JVM's already-nontrivial startup time.
- The netty/native-lib classpath already carries both `osx-x86_64` and
  `osx-aarch_64` variants — fine on bare metal, but inside a container
  you'd want a native `arm64` Linux image to avoid Rosetta/QEMU emulation
  tax; that's one more thing to get right and keep matching the JDK 17
  build already in use (`run-llm-headless.sh:5`,
  `/opt/homebrew/opt/openjdk@17`).
- JVM heap sizing would need to account for the container's memory limit
  *and* the VM's overhead *and* the `-Xmx` value — three layers of memory
  bookkeping instead of one, for a workload that (per section 2) is barely
  using a quarter of a 4 GB heap on bare metal already.
- Solo project, ad hoc research harness: the ongoing cost of maintaining
  Dockerfiles/images/compose config and keeping them in sync with the live
  `~/code/forge` checkout is real maintenance overhead for a problem that,
  per the actual measured hazards, has two fixes (a build lock, and a
  `--jobs` cap when bare model specs are in play) that cost a few lines of
  shell/Python.

**The same isolation, cheaply, without containers** — this is the
recommended path, given the actual hazards found in section 3 are narrow:

1. **Build lock** (fixes hazard #1/#2, the only genuinely dangerous race):
   a single `flock`-guarded wrapper around `mvn compile` /
   `dependency:build-classpath` in `install.sh`, so a rebuild simply waits
   (or refuses) instead of racing running JVMs. This alone removes the need
   for filesystem isolation between concurrent games — they're reading the
   same `target/classes`, but never while it's being written.
2. **Per-job seed/out isolation already exists** — `run_batch.py`'s
   `(out_dir, seed, swap)` keying and per-call env dicts (section 3) mean no
   per-job working directory is needed for correctness today.
3. **If true FS isolation is ever wanted** (e.g. testing a `forge` code
   change against in-flight games), the cheap non-container option is a
   second `git worktree` of the forge checkout with its own `target/`, run
   from its own `--forge-home`, rather than a container — same isolation
   benefit, zero VM tax, reuses tooling (`git worktree add`) already implied
   by this very task running inside `.claude/worktrees/harness-fixes`.
4. **A minimal streamlined runner, if `model_sweep.py`/`run_batch.py` need
   more parallelism than today:** the `--jobs` sketch in section 5 plus the
   build lock in (1) is the entire design — no new per-job workspace,
   classpath file, or cache directory is needed because the measured
   hazards don't actually require it. Keep the shared `/tmp/forge_cp.txt`
   and shared Forge profile dir; just stop them from being *written* while
   games run.

## 7. Addendum (2026-07-21 evening): first real parallel run — recommendations raised

The §5 table was derived from a single live JVM plus static reads. That
evening, the A/B + Stage 0 launch provided the first real multi-batch
measurement on this machine (Apple M5 Pro, 18 cores, 24 GiB):

- **7 concurrent JVMs** across three simultaneous `run_batch.py` batches
  (A/B old `--jobs 2` + A/B fix `--jobs 2`, gemini-3.5-flash; Stage 0
  `--jobs 3`, deepseek-v4-pro — all `openrouter:`-routed), alongside the
  usual desktop load (Claude sessions, Chrome, etc.).
- **RAM stayed ~75% free at peak**; ~1 GB RSS per JVM held (§2's single-JVM
  measurement generalized cleanly to 7).
- **Zero backstop kills, zero invariant failures** in the 40 completed A/B
  games (`runs/ab-quality-1/*/batch.log` — both end in clean summaries).
  Note the widened backstop (`run_batch.py` `run_one()`, loop-followups
  commit `c3ec3d2`: `cap×2 + 200s grace + 600s startup/shutdown`) was in
  effect — it absorbs the parallel-startup contention that killed 2 games
  in the first 5-JVM run under the old `cap×2 + 300` formula.

**Raised recommendations** (all still assume no rebuild mid-run, §5 hazard
list unchanged):

| Scenario | Safe `--jobs` | Basis |
|---|---|---|
| Cheap `openrouter:` LLM games | **6–10** | Network-bound; 7 proven with 75% RAM free ⇒ ~10 fits inside a "keep ≥25% RAM free" budget at 1 GB/JVM. Above 10, memory pressure risk on 24 GiB and provider-queue courtesy both start to bite. |
| Mixed sweep (floor seat + LLM seat) | **4–6** | Floor seat adds brief CPU bursts on top of the RAM budget. |
| Floor-vs-floor | **4–8** | CPU-burst-bound (~25s/game); 18 logical cores absorb 8 with desktop load intact. |
| Any bare (non-`openrouter:`) spec | **1** (unchanged) | Local claude CLI contention — not a RAM/CPU question. |

**Count JVMs across ALL concurrently running batches, not per batch** — the
budget is machine-wide (today's 7 = 2+2+3). `run_batch.py`'s `--jobs`
warning is now spec-aware and encodes these thresholds
(`warn_parallel_jobs()`): bare specs warn at `--jobs ≥2`, no-LLM batches
above 8, `openrouter:` batches above 10. Before a big batch, sanity-check
free RAM (`vm_stat`) — the ceilings assume the usual desktop load, not a
machine already under memory pressure.

---
*Sources for OpenRouter rate-limit behavior: [API Credit & Rate Limits](https://openrouter.ai/docs/api_reference/limits), [OpenRouter Rate Limits – What You Need to Know](https://openrouter.zendesk.com/hc/en-us/articles/39501163636379-OpenRouter-Rate-Limits-What-You-Need-to-Know).*
