# Cloud QLoRA on AWS — cheapest-possible map (not yet executed)

Design doc + runnable configs for moving the Stage-2 LoRA training (Qwen3-8B
4-bit, ~13.5k rows, the job `models/qwen-mtg-lora-stage2-local-v0` runs locally
in ~7h) onto AWS at minimum cost. Written 2026-07-23; prices are us-east-1
ballparks from that date — **always check current spot prices before launch**.

## TL;DR

- **~$1–2 per training run** on a spot `g5.xlarge` (A10G 24GB), ~2.5–3.5h
  wall-clock (~2–3× faster than the M5 Pro). An L40S (`g6e.xlarge`) spot cuts
  it to ~1–1.5h for about the same total dollars.
- **No containers needed.** The Deep Learning AMI ships NVIDIA drivers + CUDA;
  a pinned pip install of axolotl on top is reproducible enough for a
  one-person project. (Containers-vs-not verdict at the bottom.)
- The real tax is **format conversion**: cloud trains PyTorch/PEFT, the harness
  serves MLX 4-bit. `convert-to-mlx.sh` scripts the round-trip; the trained-vs-
  served models are no longer bit-identical (bnb-nf4 quant ≠ MLX quant) — spot-
  probe the fused MLX model before trusting an eval.
- Honesty note: **AWS is not the cheapest place to do this.** Runpod/Vast/Lambda
  rent the same silicon ~2–3× cheaper with zero quota friction. Use AWS if you
  want the account/IAM/S3 ecosystem; use a GPU broker if you only want cheap.

## Cost table (this exact job: 7,600 steps, eff. batch 4, seq 4096)

| Instance | GPU | On-demand | Spot (typ.) | Est. run time | Est. spot cost |
|---|---|---:|---:|---:|---:|
| g6.xlarge  | L4 24GB    | ~$0.80/h | ~$0.25–0.35 | ~3.5–4.5h | **~$1.00–1.40** |
| g5.xlarge  | A10G 24GB  | ~$1.01/h | ~$0.35–0.45 | ~2.5–3.5h | **~$1.00–1.50** |
| g6e.xlarge | L40S 48GB  | ~$1.86/h | ~$0.60–0.90 | ~1.0–1.5h | **~$0.90–1.40** |
| p5.48xl 8×H100 | overkill | ~$55/h | — | ~10 min | don't |

Plus: EBS 100GB gp3 ≈ $0.01/h while up; S3 for a 40MB dataset + ~200MB
adapter ≈ pennies/month. Total per run ≈ **the price of a coffee refill**.

The three cost levers, in order: **(1) spot instead of on-demand** (60–70% off),
**(2) auto-terminate the instant training ends** (user-data ends with
`shutdown`, instance shutdown behavior = terminate — you cannot forget a
running box), **(3) right-size the GPU** (24GB is enough for 8B QLoRA with
grad-checkpointing; paying for 80GB is charity).

## One-time setup (~15 min + a quota wait)

1. **Quota**: fresh accounts have 0 spot vCPUs for G instances. Request
   Service Quotas → EC2 → "All G and VT Spot Instance Requests" ≥ 8 vCPUs.
   Usually approved in minutes-to-hours.
2. **S3 bucket + IAM role**: `aws s3 mb s3://<you>-mtg-qlora`; create an
   instance role with S3 read/write on that bucket only
   (`launch-spot.sh` assumes role name `mtg-qlora-ec2`).
3. **Key pair** for optional SSH debugging: `aws ec2 create-key-pair ...`.
4. Optional but recommended: a $10 billing alarm (Budgets → cost budget).

## Per-run workflow

```
local:  python3 eval/make_sft_data.py ... --out-dir datasets/stage2-local-v0
local:  aws s3 sync datasets/stage2-local-v0-mlx s3://<bucket>/data/stage2-local-v0/
local:  bash docs/aws-training/launch-spot.sh          # ~$1, returns instance id
        # instance bootstraps, trains, syncs checkpoints to s3 every save,
        # uploads final adapter, terminates ITSELF. Watch:
local:  aws s3 ls s3://<bucket>/runs/stage2-local-v0/  --recursive | tail
local:  bash docs/aws-training/convert-to-mlx.sh       # PEFT -> fused MLX 4-bit
local:  # -> models/qwen-mtg-sft-<tag>-fused, serve + eval as usual
```

Spot interruption (rare at these sizes, ~<5%/day): relaunch `launch-spot.sh`;
the user-data script finds the newest checkpoint in S3 and passes
`--resume_from_checkpoint`. Nothing is lost but the minutes since last save.

## Files here

| file | what |
|---|---|
| `launch-spot.sh` | one-command spot launch: AMI lookup, spot options, IAM role, user-data injection, auto-terminate wiring |
| `user-data.sh` | instance bootstrap: pinned deps → pull data from S3 → axolotl QLoRA → checkpoint sync → final upload → self-terminate |
| `axolotl-qwen3-8b-qlora.yaml` | the training config — mirrors the local MLX run (rank 16 / scale 20 / dropout 0.05, eff. batch 4, seq 4096, mask-prompt ≡ `train_on_inputs: false`, ~2.5 epochs) |
| `convert-to-mlx.sh` | local post-step: merge PEFT adapter into HF Qwen3-8B → `mlx_lm.convert -q` → fused 4-bit MLX model |

## Containers: needed or not?

**Not for this.** What containers would buy — pinned CUDA/driver matrix,
identical env across many machines, CI reuse — is already covered by (a) the
Deep Learning AMI's maintained driver stack and (b) a `pip install` pinned in
`user-data.sh`. The costs would be real: an ECR repo, image build/push time,
keeping the image's CUDA in sync with the AMI, and slower cold-starts.

Revisit if any of these become true: several people launch these runs; you run
on >1 cloud and want one artifact; or a training env breakage costs you more
than an hour twice in one month. Then: one `Dockerfile` FROM
`winglian/axolotl` pinned by digest, pushed to ECR, and `user-data.sh` shrinks
to `docker run`.

## Fidelity caveats (read before comparing cloud-trained evals to local ones)

1. **Quantization mismatch**: local MLX trains LoRA on MLX-4bit weights; cloud
   QLoRA trains on bnb-nf4. After merge + `mlx_lm.convert -q` the served model
   ≈ but ≠ the local pipeline's product. Re-run the held-out replay-match
   (`eval/replay_match.py`) before believing any cross-pipeline delta.
2. **Tokenizer/template**: keep `chat_template: qwen3` and
   `enable_thinking: false` semantics identical to serving; targets contain no
   think blocks (format policy), so the template must not inject them.
3. Checkpoint-selection discipline is unchanged: pick by full-valid-set loss
   (axolotl `evals_per_epoch` + `load_best_model_at_end`), not single noisy
   spot reads.
