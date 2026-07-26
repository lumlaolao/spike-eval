# Cloud runbook — GPU generation on RunPod / Vast.ai / AWS

Plain-language guide for going from zero to a running data-generation box and
back, without surprises on the bill. Placeholders you must replace are written
`<LIKE_THIS>`.

**The golden rule: a running pod/instance costs money every hour it exists,
whether or not it is doing anything. The last step of every session is STOP
THE POD (or instance).**

One bootstrap works everywhere:

```bash
bash cloud/bootstrap_gpu.sh --dry-run    # read the plan first
bash cloud/bootstrap_gpu.sh              # detect environment + set up
```

It detects the environment from facts (root vs sudo, GPU, `/workspace`
persistent volume, container vs VM) and branches on those; `--provider
runpod|vast|aws|generic` overrides the hinting if detection ever guesses
wrong. The old `cloud/bootstrap_g5.sh` still works — it is now a thin wrapper
that calls `bootstrap_gpu.sh --aws`.

## Which provider?

| Provider | Status | Why |
|---|---|---|
| **RunPod** | **use this now** | no quota approval, per-second billing, GPUs in minutes |
| Vast.ai | fallback | often cheapest, but marketplace hosts vary in reliability |
| AWS g5 | **pending quota approval** | keep the section below for when the quota lands |

## Cost expectations

RunPod (ballparks — GPU spot prices move; check the deploy page):

| What | Rate (July 2026 ballpark) |
|---|---|
| RTX 4090 24GB (community cloud) | $0.69/hr (measured 2026-07-25; the only high-stock card) |
| A40 / A6000 48GB | ~$0.40–0.80/hr |
| Volume disk while pod **runs** | $0.10/GB/mo |
| Volume disk while pod is **STOPPED** | $0.20/GB/mo — **the rate doubles**; a stopped pod is not free |
| Network volume (attachable, survives pod termination) | $0.07/GB/mo, no stopped premium |

Those storage rates are RunPod's published model as of mid-2026 — verify
current numbers at runpod.io/pricing before relying on them. Practical
upshot: a 100 GB volume on a stopped pod ≈ $20/mo. For gaps longer than a
few days, rsync results home and **terminate** (the bootstrap rebuilds a
fresh pod in ~15 min), or keep the model on a network volume instead.

AWS (once quota lands): g5.xlarge spot ~$0.44/hr, on-demand ~$1.01/hr,
200 GB gp3 ~$16/mo even while stopped.

Track every session in the local ledger so the dashboard budget bar stays
honest:

```bash
# on your laptop, after each session (hours * rate = what you spent)
echo '{"ts": '"$(date +%s)"', "what": "runpod RTX4090", "hours": 2.5, "rate": 0.69}' \
  >> ~/code/mtg-forge-llm-agent/cloud/aws_ledger.jsonl
```

## RunPod quickstart

> **Two RunPod runbooks exist on purpose.** This one covers *generation /
> self-play* pods, where the repo + harness must live on the pod (rsync'd
> with the exclusions below). For *training-only* jobs, use
> `docs/runpod-training/` instead — it ships a ~3 MB job tarball (dataset +
> config only) so no repo, key, or credential ever touches rented hardware.

### 1. Sign up + credit

runpod.io → sign up → Billing → add credit (prepaid; $10–25 is plenty to
start). No quota requests, no approval wait.

### 2. Deploy a pod

Console → Pods → Deploy:

1. **GPU**: RTX 4090 (24 GB) is the sweet spot for the student model;
   A40/A6000 if you want 48 GB headroom. "Community Cloud" is cheaper,
   "Secure Cloud" more reliable.
2. **Template**: "RunPod PyTorch" (any recent CUDA version — the template
   ships NVIDIA drivers + Python; the bootstrap installs the rest).
3. **Volume**: set **Volume Disk** to ~100 GB, mount path `/workspace`
   (the default). This is the persistent part — repos, model, and results
   live there and survive a stop.
4. Deploy, wait for "Running".

### 3. SSH in

Pod page → Connect → "SSH over exposed TCP" shows the exact command, shaped
like:

```bash
ssh root@<POD_IP> -p <POD_PORT> -i ~/.ssh/id_ed25519
```

(Add your SSH public key in RunPod Settings → SSH Keys *before* deploying;
containers run as `root` — that is normal here.)

### 4. Get code + model onto the pod (from the laptop)

```bash
# code (repo is private, so rsync beats cloning). NEVER let secrets.env or
# .git (which could carry credential helpers/remotes) land on rented hardware:
rsync -av -e "ssh -p <POD_PORT> -i ~/.ssh/id_ed25519" \
  --exclude secrets.env --exclude '.git' \
  --exclude runs --exclude models --exclude datasets --exclude .venv-mlx \
  --exclude '.claude' --exclude '*.mp4' \
  ~/code/mtg-forge-llm-agent/ root@<POD_IP>:/workspace/mtg-forge-llm-agent/

# student model — vLLM wants the HuggingFace-format dir (config.json +
# safetensors), NOT the MLX-fused one:
rsync -av -e "ssh -p <POD_PORT> -i ~/.ssh/id_ed25519" \
  <LOCAL_HF_MODEL_DIR>/ root@<POD_IP>:/workspace/models/student/
```

### 5. Bootstrap (on the pod)

```bash
cd /workspace/mtg-forge-llm-agent
bash cloud/bootstrap_gpu.sh --dry-run   # read the plan
bash cloud/bootstrap_gpu.sh             # ~10-20 min on first run
```

Safe to re-run any time (it skips finished steps) — and re-running is the
fix after a pod restart (`/tmp/forge_cp.txt` dies with the container; the
bootstrap restores it from the `/workspace` cache).

It ends with a **seed-42 canary game**: identity A must win in 108 turns.
If that fails, the engine on the box does not match the validated baseline —
stop and investigate before generating any data.

Then start the model server in tmux (survives SSH drops):

```bash
tmux new -s vllm 'bash /workspace/serve_student.sh'
# detach: Ctrl-b then d      reattach: tmux attach -t vllm
```

Optionally re-run the bootstrap's LLM smoke test once the server is up — it
prints the exact commands. It will ask you to set `OPENROUTER_ALLOW_EGRESS=1`
yourself: with the localhost endpoint no data leaves the machine, but the
harness's consent gate applies to every `openrouter:` spec and the scripts
never set it for you.

### 6. Start a generation run

```bash
tmux new -s dagger
cd /workspace/mtg-forge-llm-agent
export OPENROUTER_ALLOW_EGRESS=1 OPENROUTER_API_KEY=local-only
export OPENROUTER_BASE_URL=http://127.0.0.1:8080/v1

python3 eval/run_batch.py --exploratory --shadow-ai \
  --seeds 9100..9102 --pairs --jobs <N> --cap-minutes 8 \
  --deck-a eval/decks/<DECK_A>.txt --deck-b eval/decks/<DECK_B>.txt \
  --name-a <DECK_A> --name-b <DECK_B> \
  --controller-a llm --controller-b ai \
  --smart-a openrouter:default_model --fast-a openrouter:default_model \
  --out runs/<RUN_NAME>/<DECK_A>-vs-<DECK_B>
# detach with Ctrl-b d; check progress with: tmux attach -t dagger
```

`--jobs <N>`: vLLM handles concurrent requests properly (unlike the local
mlx server), so 2–4 parallel JVMs are reasonable; start with 2 and watch
`free -h` (~1 GB RSS per JVM on top of vLLM's host RAM).

### 7. rsync results back (on the laptop)

```bash
rsync -av -e "ssh -p <POD_PORT> -i ~/.ssh/id_ed25519" \
  root@<POD_IP>:/workspace/mtg-forge-llm-agent/runs/<RUN_NAME>/ \
  ~/code/mtg-forge-llm-agent/runs/<RUN_NAME>-cloud/
```

rsync periodically during long runs, not only at the end — pods can die too.

### 8. STOP THE POD

Console → your pod → **Stop**. Remember: a stopped pod still bills volume
storage at the doubled rate (see cost table). For multi-day gaps: rsync
everything home, then **Terminate** — the bootstrap rebuilds from nothing in
one command.

Finally, append the session to the ledger (see "Cost expectations") so the
dashboard's budget bar reflects reality.

## CPU-only teacher-corpus generation — measure before you rent

Teacher corpora (`--controller-a ai-log --controller-b ai`) are **engine vs
engine**: Forge's own AI plays both seats and the `ai-log` seat writes
`tier="ai-teacher"` decision rows. There are **zero model calls**, so this
workload:

- **needs no GPU** — it is pure CPU, one JVM per concurrent game;
- **needs no secrets on the pod** — no `OPENROUTER_API_KEY`, no
  `OPENROUTER_ALLOW_EGRESS`, no egress at all. If you do rent a box for this,
  nothing sensitive has to touch it beyond the repo itself.

**Renting for this is usually a net loss.** Measured on the 18-core / 24 GB
laptop, 2026-07-26 (round-2 corpus, engine `c1c64a5`):

| Measurement | Value |
|---|---|
| 100 games (50 seeds × `--pairs`) at `--jobs 6` | 4 min 01 s wall, 0 problems |
| Per-game cost | ~14.5 s per game per slot |
| Per-JVM CPU | ~1 core (`ps` shows ~100% per `LlmSpikeHeadless`) |
| Per-JVM RSS | ~0.66 GB measured (12 JVMs = 7.9 GB) |
| `--jobs 12` on 18 cores / 24 GB | 21 games/min, but 56–65% **sys** time |

**Do not size `--jobs` from macOS load average.** At `--jobs 8` the 1-minute load
average reads ~26 on an 18-core box, which looks badly oversubscribed; `ps` shows
only 8 cores actually busy. Darwin's load average counts blocked and GC threads,
so it over-reports for JVM fleets. Size from `%CPU` per process (~1 core each)
and from RAM, not from load average.

**But don't size from `%CPU` alone either.** Pushing to `--jobs 12` on the
strength of idle cores drove system time to 56–65% with the memory compressor
holding ~6 GB — the JVMs' own 7.9 GB was not the problem, contention with
everything else on a 24 GB laptop was. Useful work fell to ~10–33% user time.
The batch still completed cleanly (`rc=0`, 0 problems), so this is a throughput
tax, not a correctness risk. CLAUDE.md's **4–8** guidance is the right default;
treat anything above it as needing a before/after measurement on the *same*
matchup, not just free cores.

Per-game wall time varies a lot by *matchup* (a Burn-vs-Elves game is not the
same length as Burn-vs-UR-Delver), so do not benchmark a `--jobs` change by
comparing two different matchups — compare the same matchup, or a whole corpus.

Now price the pod. Fixed cost **before the first game starts** is pod create +
rsync + `bootstrap_gpu.sh` (JDK/maven install, Forge clone at the `FORGE_COMMIT`
pin, full maven build) — §5 measures that bootstrap at 10–20 min, so call the
round trip 25–35 min.

Then notice what the per-JVM-per-core result above implies: throughput is set by
**core count**, and a 16-vCPU pod has no meaningful edge over an 18-core laptop.
Renting buys roughly 1.0× the local throughput while costing 25–35 minutes of
setup — the payback period is infinite, not merely long. Cloud vCPUs are usually
*slower* per core than Apple Silicon, so the realistic case is worse than parity.
(This comparison is arithmetic from the local measurements plus §5's stated
bootstrap time; per-game throughput on a pod has not been measured directly.)

**A pod only pays off when it brings more cores than you have, or when the wall
clock is not the constraint** — a 64-vCPU box for a very large corpus, or any pod
at all when you want the laptop free or the corpus built overnight. For a
same-size-or-smaller core count, generate locally.

The round-2 teacher corpus was generated locally for this reason, on a directive
that had assumed a 2–3 hour local runtime. Measured: the driver produced 1,023
games in ~62 min wall (09:29→10:31, `--jobs` 8 then 12), for a 1,200-game corpus
across 16 archetypes. No pod was rented and nothing was added to the ledger.

### Verify the output, not just the exit code

`run_batch.py` reported `completed: 50  problems: 0` for a batch that had
silently merged two games into one file: both swap variants of a seed started in
the same millisecond and derived the same `game-<seed>-<ms>.jsonl` name, so 115
decisions from two different games interleaved in a single file. Nothing in the
batch summary reflected it.

After any large batch, scan for it directly — a complete game file holds exactly
one `t=="game"` row:

```bash
python3 - <<'PY'
import json, glob
for f in glob.glob('runs/<RUN_NAME>/*/game-*.jsonl'):
    n = sum(1 for l in open(f) if l.strip() and json.loads(l).get('t') == 'game')
    if n > 1:
        print('COLLISION (%d games in one file): %s' % (n, f))
PY
```

Fix by deleting the merged file and re-running that seed at `--jobs 1`. Files
with **zero** game rows are different and benign — they are in-flight or killed
games, which `load_complete_games` already skips and `run_batch.py` re-runs on
resume.

### If you do exceed the break-even

Same flow as the GPU quickstart with the GPU parts dropped:

1. Deploy any cheap pod (§2). GPU choice is irrelevant — pick on vCPU count and
   price. As of 2026-07-25 the RTX 5090 community listing at $0.69/hr places
   reliably; **RTX 4090 placement fails** with image
   `runpod/pytorch:1.0.3-cu1281-torch291-ubuntu2404`. "machine does not have the
   resources" is transient — retry.
2. rsync the repo (§4). Keep every exclusion; no model dir and no `secrets.env`
   are needed.
3. `bash cloud/bootstrap_gpu.sh` in tmux (§5). Its seed-42 canary (identity A
   wins in 108 turns) must pass before you generate anything — a corpus from a
   box whose engine does not match the pin is not comparable to local runs.
4. Run the batch under tmux with `--controller-a ai-log --controller-b ai` and
   **no** `--smart-a`/`--fast-a` model flags. Start at `--jobs 8` and only raise
   it if load average stays below core count.
5. rsync results back (§7), then **terminate** the pod (§8) and append the hours
   to `cloud/aws_ledger.jsonl`.

## Vast.ai note

Same shape as RunPod: marketplace GPUs, container templates, root, no sudo.
Differences that matter:

- Pick a **PyTorch/CUDA template** and check the host's reliability score;
  Vast hosts are individual machines with real variance.
- The persistent-volume story varies per offer — if `/workspace` (or an
  equivalent mount) is absent, the bootstrap warns and uses `$HOME`, which
  **dies with the instance**; rsync results home aggressively.
- Bootstrap: `bash cloud/bootstrap_gpu.sh --provider vast` (detection would
  get it right anyway; the flag just sharpens the warnings).
- Billing continues while an instance exists; destroy instances you are not
  using.

## AWS g5 (pending quota approval)

Kept for when the quota request (8 vCPUs G-class spot + on-demand,
us-east-1) is approved. Everything below still works; the launcher script
is now the wrapper `cloud/bootstrap_g5.sh` (same behavior).

### Prerequisites (one-time)

- Quota approved: "Running On-Demand G and VT instances" and/or "All G and
  VT Spot Instance Requests" ≥ 8 vCPUs in us-east-1 (g5.xlarge is 4 vCPUs,
  g5.2xlarge is 8).
- An EC2 **key pair**: EC2 console → Key Pairs → Create key pair → ED25519,
  `.pem` → save as `~/.ssh/mtg-gpu.pem`, `chmod 400` it.

### Launch

EC2 → Launch instance: name `mtg-dagger-gpu`; AMI "Deep Learning OSS Nvidia
Driver AMI GPU PyTorch (Ubuntu 22.04)"; type `g5.xlarge` (or `g5.2xlarge`);
Advanced → Purchasing option → **Request Spot Instances**; your key pair;
SSH (22) from **My IP** only — never open 8080 (the model server binds
127.0.0.1); 200 GB gp3 storage. Copy the public IP after launch.

### Setup and run

```bash
ssh -i ~/.ssh/mtg-gpu.pem ubuntu@<INSTANCE_IP>
# from the laptop: rsync code to ~/mtg-forge-llm-agent and the HF model to
# ~/models/student (same rsync forms as RunPod, user ubuntu@, no -p port)
cd ~/mtg-forge-llm-agent
bash cloud/bootstrap_g5.sh --dry-run && bash cloud/bootstrap_g5.sh
tmux new -s vllm 'bash ~/serve_student.sh'
```

Then run/rsync exactly as in RunPod steps 6–7 (paths under `$HOME` instead
of `/workspace`).

### Stop

EC2 console → Instance state → **Stop instance**, or:

```bash
aws ec2 stop-instances --instance-ids <INSTANCE_ID> --region us-east-1
aws ec2 describe-instances --instance-ids <INSTANCE_ID> --region us-east-1 \
  --query 'Reservations[].Instances[].State.Name' --output text  # expect: stopping/stopped
```

Stop = compute billing ends, disk (~$16/mo for 200 GB) persists. Terminate =
$0/mo, full re-bootstrap next time. Pure spot requests sometimes only offer
Terminate — rsync first, then terminate.

## Local dashboard

On the laptop, `python3 cloud/dashboard.py` serves http://localhost:8377 —
DAgger run status, live match/heartbeat feed, corpus stats, budget bar (from
the ledger above), training log tail, incident watch. Two things to know:

- **Paths are per-job.** The shipped defaults point at the current run
  (`runs/d4-dagger-r1`, job `f9e4d46b`). For the next run, copy
  `cloud/dashboard_config.example.json` to `cloud/dashboard_config.json`
  (git-ignored) and update `runs_dir` / `dagger_dir` — otherwise those panels
  will truthfully say "no data" about a finished job forever.
- **Real AWS cost is opt-in.** Set `"aws_ce": true` in the config to also
  fetch actual month-to-date spend via Cost Explorer. Off by default because
  each query is a real (tiny) AWS charge: $0.01/request, cached 1 hour here.
  Without it the budget panel runs on the ledger alone. (RunPod/Vast spend
  has no API panel — the ledger is the tracker.)

## If something fails

| Symptom | Likely cause / fix |
|---|---|
| `Permission denied (publickey)` on SSH | RunPod: key not added in Settings → SSH Keys before deploy. AWS: user must be `ubuntu`, key must be the launch key pair, `chmod 400` |
| SSH hangs forever | RunPod: use the exact Connect command (TCP port differs per pod). AWS: security group doesn't allow 22 from your current IP |
| bootstrap dies: "neither root nor a sudo binary" | unusual VM image without sudo — run as root or install sudo manually |
| bootstrap dies: "container without a working GPU" | you deployed a CPU pod/template — terminate, deploy a GPU pod. Never install drivers inside a container |
| bootstrap warns: "no /workspace volume" (RunPod/Vast) | pod deployed without a volume — everything lands in `$HOME` and dies with the pod; redeploy with a volume unless this is a throwaway |
| bootstrap dies at "apt-get update failed" | no network route (AWS: launched without public IP) or provider apt mirror hiccup — retry, then relaunch |
| bootstrap dies at "Forge HEAD is ... but pin is ..." | Forge upstream moved and the pinned commit didn't fetch — do not override; `git -C <FORGE_HOME> fetch origin <PIN_SHA>` then re-run |
| Maven build fails | `java -version` must say 17; first build needs network for dependencies; re-run bootstrap (it resumes) |
| every game exits 1 instantly | `/tmp/forge_cp.txt` missing (post-reboot / pod-restart classic) — re-run the bootstrap, it restores from the WORK_ROOT cache |
| canary FAILS (not A/108) | engine mismatch or harness drift — do NOT generate data; compare `FORGE_COMMIT`, re-run bootstrap, debug locally if it persists |
| `pip install vllm` fails | `nvidia-smi` broken → wrong template/AMI; or no network |
| vLLM OOMs / won't load model | model too big for the GPU's VRAM — smaller GPU than planned, quantize, or lower `--max-model-len` in `serve_student.sh` |
| games run but 0 model calls | server not on :8080, or `OPENROUTER_BASE_URL` unset in the shell that started the run — the per-cell validation quarantines such games |
| pod/instance vanished mid-run | spot reclaim (AWS) or host failure (Vast) — results survive only if rsynced; redeploy, re-bootstrap, resume (the batch driver skips finished games) |
| bill higher than expected | something still exists: RunPod stopped pods bill volume at the DOUBLED rate; AWS: check us-east-1 for running instances and stray volumes |
