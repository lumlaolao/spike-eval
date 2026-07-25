# Cloud QLoRA on Runpod — setup, code delivery, and what it costs (not yet executed)

Runpod counterpart to `docs/aws-training/`, which ends with the honest admission
that *"AWS is not the cheapest place to do this. Runpod/Vast/Lambda rent the same
silicon ~2–3× cheaper with zero quota friction."* This is that path, made runnable.

Written 2026-07-25. Prices are Runpod **list** prices read off runpod.io/pricing
that day (page's own "Updated July 17, 2026"). Per-region Community Cloud prices in
the console can be lower — **re-check `runpodctl gpu list` before launch.**

**Status: written, not executed.** Every script here is unrun — validating them
costs real GPU-hours on a real account, which needs an API key this repo doesn't
have. Treat the wall-clock numbers as estimates with stated derivations, not
measurements. The first real run should be a `--smoke` launch (below), not a
7,600-step job.

## TL;DR

- **~$0.85–1.40 per Stage-2 training run**, and — the useful part — that range is
  nearly the *same on every GPU worth using*. Cheap GPUs are cheap-and-slow, fast
  ones fast-and-pricey; total cost lands within ~40% across a 10× spread in
  hourly rate. **So pick for wall-clock and availability, not price.**
  Default recommendation: **RTX 4090, ~1.5h, ~$1.10.**
- **Don't put credentials on a Community Cloud pod.** The training job needs the
  dataset and a YAML — not the Forge engine, not `secrets.env`, not a GitHub
  token. Ship a job tarball, not a repo clone. See "Getting code onto the pod".
- **Never upload the base model from home.** 16.4 GB from a laptop is a slow hour;
  `hf download Qwen/Qwen3-8B` on the pod is 1–3 min on datacenter bandwidth.
- **The money leak is idle storage, not compute.** A *stopped* pod still bills its
  volume at $0.20/GB/mo. A 60 GB volume left stopped is ~$12/mo — ten training
  runs — for a pod doing nothing. **`pod delete`, don't `pod stop`.**
- `--terminate-after` is Runpod's version of the AWS self-terminate wiring, and
  it's better: a hard deadline enforced server-side, so a hung job cannot bill
  past it. Always set it.

## One-time setup (~5 min, no quota wait)

Unlike AWS there is no quota request — this is the "zero friction" the AWS doc
was pointing at.

```bash
brew install runpod/runpodctl/runpodctl     # already done: runpodctl 2.7.2
runpodctl doctor                            # prompts for API key, writes ~/.runpod/config.toml
runpodctl user                              # confirm auth + credit balance
```

Get the key at <https://www.runpod.io/console/user/settings> → API Keys. Give it
the narrowest scope that still allows pod create/delete.

Then, for rsync-based transfers (optional but recommended — see below):

```bash
runpodctl ssh add-key --key-file ~/.ssh/id_ed25519.pub
runpodctl ssh list-keys
```

A billing alarm is worth 30 seconds: console → Billing → set a low balance
auto-top-up threshold, or simply keep the balance small. Runpod bills against
prepaid credit, so **a small balance is itself the spend cap** — a strictly
better safety property than AWS's post-hoc budget alerts.

## Cost: the Stage-2 job

The job being priced is the one `models/qwen-mtg-lora-stage2-local-v0` runs
locally in ~7.0h on the M5 Pro: Qwen3-8B 4-bit QLoRA, rank 16 / scale 20,
7,600 iters at effective batch 4, `max_seq_length` 4096, grad checkpointing.

Measured job size (this repo, 2026-07-25):

| quantity | value | source |
|---|---:|---|
| train rows | 12,168 | `datasets/stage2-local-v0-mlx/train.jsonl` |
| mean tokens/row | ~896 | Qwen3 tokenizer over a 200-row sample (median 926, p90 1,033, max 1,176) |
| example-passes | 30,400 | 7,600 iters × eff. batch 4 |
| **total train tokens** | **~27.2M** | 30,400 × 896 |
| local throughput | ~1,080 tok/s | 27.2M ÷ 7.0h — the anchor for every estimate below |

27M tokens is a *small* job. That is why the answer is "about a dollar" and why
GPU choice barely moves it.

### Per-run cost by GPU

Speedups are estimated relative to the measured M5 Pro anchor, cross-checked
against the AWS doc's independent A10G/L4 estimates (2.5–4.5h), which this table
reproduces. They are **not measured on Runpod.**

| GPU | VRAM | $/hr (list) | est. vs M5 Pro | est. run | **est. cost** |
|---|---:|---:|---:|---:|---:|
| **RTX A5000** | 24 GB | $0.27 | 2.0–2.5× | 2.8–3.5h | **$0.76–0.95** |
| A40 | 48 GB | $0.44 | 2.5–3.0× | 2.3–2.8h | $1.01–1.23 |
| **RTX 4090** | 24 GB | $0.69 | 4.0–5.0× | 1.4–1.8h | **$0.97–1.24** |
| RTX A6000 | 48 GB | $0.53 | 2.5–3.0× | 2.3–2.8h | $1.22–1.48 |
| RTX 3090 | 24 GB | $0.50 | 2.3–2.8× | 2.5–3.0h | $1.25–1.50 |
| L40S | 48 GB | $0.99 | 4.5–5.5× | 1.3–1.6h | $1.29–1.54 |
| L4 | 24 GB | $0.39 | 1.6–2.0× | 3.5–4.4h | $1.37–1.72 |
| A100 PCIe | 80 GB | $1.39 | 5.0–6.0× | 1.2–1.4h | $1.63–1.95 |
| H100 PCIe | 80 GB | $2.89 | 8–10× | 0.7–0.9h | $2.02–2.53 |

Add per-run fixed overhead — pod boot, pinned pip install, 16.4 GB model pull —
of roughly 8–12 min at the pod's hourly rate: **+$0.04 (A5000) to +$0.14 (4090)**.
On the short/fast GPUs this overhead is a *third* of the bill, which is the one
argument for a network volume (below).

**Read of the table:** the cheapest option (A5000, ~$0.85) and the fastest
sensible one (4090, ~$1.10) differ by ~$0.25 — less than the cost of one
mistaken run. Buy the 2× wall-clock. Only 80 GB cards are actually poor value
here: 24 GB is sufficient for 8B QLoRA with grad checkpointing, so an 80 GB card
is charity, exactly as the AWS doc concluded.

### Storage — where the real waste is

| line | rate | this job |
|---|---:|---|
| Container disk | $0.10/GB/mo | 30 GB for ~3h ≈ **$0.01** |
| Volume disk (running) | $0.10/GB/mo | 60 GB for ~3h ≈ **$0.02** |
| Volume disk (**idle/stopped**) | **$0.20/GB/mo** | 60 GB stopped = **$0.39/day, ~$12/mo** |
| Network volume (standard, <1 TB) | $0.07/GB/mo | 100 GB = **$7.00/mo** |

Two conclusions:

1. **Delete pods, don't stop them.** A stopped pod bills volume at *double* the
   running rate. Leaving one stopped for a month costs more than ten training runs.
   `launch-pod.sh` sets `--terminate-after` so this can't happen by forgetting.
2. **Skip the network volume until cadence justifies it.** A 100 GB network volume
   is $7/mo ≈ 6 training runs. It only pays for itself if you re-pull the 16 GB
   base model often enough that the ~$0.05–0.14/run download overhead exceeds
   $7/mo — i.e. **~50+ runs/month**. Below that, re-download each time. (A smaller
   30 GB volume holding just the HF cache is $2.10/mo and breaks even nearer
   ~15–20 runs/month, if you get there.)

### What a realistic month looks like

| activity | qty | unit | subtotal |
|---|---:|---:|---:|
| Stage-2 / D4 training runs | 4 | ~$1.10 | $4.40 |
| Fused-model decision-match evals (`replay_match.py` against a pod-hosted vLLM) | 4 | ~$0.20 | $0.80 |
| DAgger self-play rounds (student in the loop, ~1,000 games) | 2 | ~$2.50 | $5.00 |
| Storage | — | — | ~$0.10 |
| | | **total** | **~$10–15/mo** |

Confidence: high on training (derived from a measured local anchor and a measured
token count), **low on the self-play line** — it depends on Forge JVM parallelism
against the pod's vCPU count, not on the GPU, and this repo has no measurement of
LLM-in-the-loop game throughput on rented hardware. A 4090 pod has only 6 vCPUs;
`eval/CONCURRENCY.md` wants 6–10 JVMs for cheap LLM games. **If you move self-play
to Runpod, price a CPU-heavy pod separately, or keep self-play local and rent only
the GPU for training.** Keeping self-play local is the recommended default —
the Forge harness, its overlay build, and `secrets.env` all live here, and moving
them onto rented hardware buys nothing the M5 Pro can't already do.

## Getting code onto the pod

The important realization: **a training pod does not need this repo.** It needs
`train.jsonl`, `valid.jsonl`, one axolotl YAML, and a bootstrap script. It does not
need the Forge engine, the Java overlay, the eval drivers, or any credential.

Measured, not estimated (`pack-job.sh` on `stage2-local-v0-mlx`, 2026-07-25): the
whole job is **3.0 MB compressed** — 49 MB of jsonl that gzips ~16×. That reframes
the question from "how do I clone a private repo onto a rented box" to "how do I
ship 3 MB", which barely needs an answer at all. Any transport works; pick the one
that puts the fewest credentials on rented hardware.

| approach | verdict |
|---|---|
| **Job tarball via `runpodctl send/receive`** | **Recommended.** croc p2p, no cloud storage, no bucket, no credentials on the pod. `--code` makes it scriptable (no relaying a codephrase by hand). |
| **rsync over direct TCP SSH** | **Recommended when you need resumable/repeated syncs.** Deterministic, no timing coordination. Needs `--ports 22/tcp` and an SSH key on the account. |
| `git clone` the private repo | **Avoid on Community Cloud.** Requires putting a `repo`-scoped GitHub PAT on someone else's hardware to fetch code the job doesn't need. `secrets.env` is git-ignored so it wouldn't travel — but the token would. |
| Bake code into a Docker image | Overkill, same verdict as the AWS doc. An ECR/registry round-trip to ship a YAML and a jsonl is not worth it for a one-person project. |
| Base model from home | **Never.** 16.4 GB up from a laptop vs. 1–3 min on the pod. Pull from HF on the pod. |
| Adapter results back | `runpodctl send` from the pod (~200–600 MB), or push to a private HF repo. |

Runpod proxy SSH (`ssh.runpod.io`) does **not** support scp/sftp/rsync — only an
interactive shell. For rsync you need a direct TCP port, which is why
`launch-pod.sh` passes `--ports '22/tcp'`. On Community Cloud that also wants
`--public-ip`. If a given pod won't give you direct TCP, fall back to
`send`/`receive`, which works over the relay regardless.

## Per-run workflow

```bash
# 0. one-time
runpodctl doctor

# 1. build the dataset locally (unchanged from the existing pipeline)
python3 eval/make_sft_data.py --source ai-teacher --ai-filter win+rare \
        --out-dir datasets/stage2-local-v0

# 2. pack a self-contained job tarball (data + config + train script, no secrets)
bash docs/runpod-training/pack-job.sh datasets/stage2-local-v0-mlx stage2-runpod-v0

# 3. smoke first — 20 steps on the cheapest GPU, ~$0.10, proves the whole path
bash docs/runpod-training/launch-pod.sh --job stage2-runpod-v0 --smoke

# 4. the real run
bash docs/runpod-training/launch-pod.sh --job stage2-runpod-v0 \
        --gpu "NVIDIA GeForce RTX 4090" --max-hours 3

# 5. pull the adapter back when it finishes
bash docs/runpod-training/fetch-adapter.sh stage2-runpod-v0

# 6. local post-step — PEFT -> merged -> MLX 4-bit fused (reuses the AWS script)
bash docs/aws-training/convert-to-mlx.sh
```

Everything downstream of step 6 — serve, `eval/replay_match.py`, the
decision-match and win-rate evals — is unchanged.

## Gotchas

1. **All the AWS doc's fidelity caveats still apply, unchanged.** Cloud trains
   bnb-nf4 PEFT; the harness serves MLX 4-bit. The trained and served models are
   not bit-identical. Qwen3's `enable_thinking=false` injects an *empty*
   `<think></think>` sentinel. Byte-diff one rendered training example against the
   `mlx_lm` server's render before trusting any cross-pipeline eval delta. See
   `docs/aws-training/README.md` §"Fidelity caveats" — it is the same trap here.
2. **Checkpoint discipline is unchanged**: select on full-valid-set loss, never on
   the noisy 25-batch in-training reads (`val_batches: 25` bounced 0.14↔0.90 in
   one local run). The axolotl config already sets `load_best_model_at_end`.
3. **Community Cloud pods can disappear.** Less abruptly than AWS spot (no 2-minute
   notice contract), but they are consumer hardware in someone's rack.
   `auto_resume_from_checkpoints: true` in the axolotl config is load-bearing, and
   the bootstrap syncs checkpoints out as they're written. Same posture the AWS doc
   took for >20% spot disruption.
4. **`--terminate-after` is a wall-clock guillotine, not a "finish first" flag.**
   Set it to ~2× the estimated run, or a job that stalls on a slow model download
   gets beheaded mid-training. Too tight is a wasted run; too loose is a wasted
   dollar. 2× is the right side to err on.
5. **Verify the GPU you got.** `--gpu-id` is a request; check `nvidia-smi` in the
   bootstrap log before trusting a wall-clock comparison between runs. A run that
   silently landed on a different card is not comparable to the one before it —
   the same discipline `FORGE_COMMIT` enforces for engine versions.

## Known-unverified spots (fix these on the first smoke run)

Being specific about what is *not* known beats a blanket "untested" disclaimer.
Everything below is reasoning from `runpodctl --help` output, not from a live pod:

| what | risk | how you'll know |
|---|---|---|
| The JSON shape `launch-pod.sh` parses out of `runpodctl pod get` to find the SSH host/port | **Highest.** `runtime.ports[]` with `privatePort`/`publicPort`/`ip` is inferred, not confirmed. | Launcher prints "no direct-TCP ssh endpoint appeared" and leaves the pod up. Fix: `runpodctl pod get <id> -o json` by hand, adjust the parse. |
| `--image runpod/pytorch:1.0.3-cu1281-torch291-ubuntu2404` | Low — it's the tag in runpodctl's own `pod create --help` example, but tags age. | Pod create fails fast, before billing much. |
| Community Cloud giving a direct TCP port at all | Medium. May need `--public-ip`. | Same failure path as row 1; fall back to `runpodctl send`/`receive`. |
| axolotl 0.18.0 accepting this YAML unchanged | Medium — the config was written for the AWS path and never run either. `lora_alpha: 320` in particular is deliberate (see its NOTE) and looks wrong to anyone skimming. | Smoke run fails in <5 min at config-parse time. |
| Every wall-clock number in the cost table | They are extrapolations from one measured local anchor. | The smoke run's `steps/sec` gives you the real number; multiply out and correct the table. |

This is why step 3 of the workflow is a 20-step `--smoke` launch for ~$0.10. It
exercises pod create → SSH → transfer → deps → model download → train → pack →
fetch → delete, and every row above fails inside it if it's going to fail.
