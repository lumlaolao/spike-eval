#!/usr/bin/env bash
# Runs ON the pod. Unpacked from job.tar.gz by launch-pod.sh, then started detached.
#
#   bash /workspace/job/pod-bootstrap.sh [--smoke]
#
# Contract with the local side (launch-pod.sh / fetch-adapter.sh):
#   /workspace/STATUS      one word, polled by the local side:
#                          BOOTSTRAP | DOWNLOADING | TRAINING | PACKING | DONE | FAILED
#   /workspace/bootstrap.log   full transcript
#   /workspace/adapter.tar.gz  the deliverable, written only on success
#
# Never exits early on a bare `set -e` without recording FAILED — a silent death
# looks identical to "still training" from the outside, and you find out by
# reading a bill. Same reasoning as the harness's silent-drop invariant.
set -uo pipefail

JOB_DIR="/workspace/job"
OUT_DIR="/workspace/out"
STATUS_FILE="/workspace/STATUS"
LOG="/workspace/bootstrap.log"

AXOLOTL_PIN="${AXOLOTL_PIN:-0.18.0}"     # released 2026-07-17; bump deliberately, not casually
BASE_MODEL="${BASE_MODEL:-Qwen/Qwen3-8B}"

SMOKE=0
[[ "${1:-}" == "--smoke" ]] && SMOKE=1

exec > >(tee -a "$LOG") 2>&1

status() { echo "$1" > "$STATUS_FILE"; echo "=== [$(date -u +%H:%M:%S)] $1 ==="; }
die()    { echo "FATAL: $*"; status FAILED; exit 1; }
trap 'rc=$?; [[ $rc -ne 0 ]] && [[ "$(cat "$STATUS_FILE" 2>/dev/null)" != "DONE" ]] && status FAILED; exit $rc' EXIT

status BOOTSTRAP

# --- provenance: which card did we ACTUALLY get? -----------------------------
# `--gpu-id` is a request, not a guarantee. A run that silently landed on a
# different card is not wall-clock-comparable to the run before it.
echo "--- host ---"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv || echo "nvidia-smi unavailable"
echo "vCPUs: $(nproc)  RAM: $(free -g 2>/dev/null | awk '/^Mem:/{print $2"GB"}')"
echo "--- job manifest ---"
cat "$JOB_DIR/MANIFEST.txt" 2>/dev/null || echo "(no manifest)"

# --- deps --------------------------------------------------------------------
# All three quirks below were hit live on runpod/pytorch:1.0.3-cu1281 (Ubuntu
# 24.04), 2026-07-26:
#   1. PEP 668: system pip refuses to install (externally-managed-environment)
#      -> venv. FULLY ISOLATED, not --system-site-packages: axolotl pins its
#      own torch, and mixing it with the image's torch-linked packages
#      (torchaudio/torchvision) produces undefined-symbol crashes at import.
#      Letting pip build one coherent stack costs ~2 min of datacenter
#      bandwidth and removes the whole class of ABI clashes.
#   2. flash-attn's build imports torch inside pip's isolated build env and
#      dies; a 20-step QLoRA run doesn't need it (nor deepspeed on 1 GPU), so
#      base axolotl only. Re-add extras deliberately if a multi-GPU run needs
#      them (with --no-build-isolation for flash-attn).
#   3. The image carries a dangling /snap/bin/accelerate shim that shadows the
#      venv's real one -> PATH must put the venv first when axolotl launches.
status BOOTSTRAP
VENV=/workspace/venv
python3 -m venv "$VENV" || die "venv creation failed"
export PATH="$VENV/bin:$PATH"
pip install --no-cache-dir --quiet "axolotl==${AXOLOTL_PIN}" \
  || die "axolotl install failed"
pip install --no-cache-dir --quiet "huggingface_hub[cli]" || die "hf hub install failed"
echo "axolotl: $(pip show axolotl 2>/dev/null | awk '/^Version:/{print $2}')"

# --- base model --------------------------------------------------------------
# Pulled here, never uploaded from home: 16.4GB over datacenter bandwidth is
# 1-3 min, over a domestic uplink it is an hour of paid GPU idle.
status DOWNLOADING
export HF_HOME="${HF_HOME:-/workspace/hf}"
# `hf` is the current entrypoint; `huggingface-cli` is the pre-0.34 name and is
# still what ships in some images. Try both rather than guess which one landed.
if command -v hf >/dev/null 2>&1; then
  hf download "$BASE_MODEL" || die "base model download failed"
elif command -v huggingface-cli >/dev/null 2>&1; then
  huggingface-cli download "$BASE_MODEL" || die "base model download failed"
else
  python -c "
from huggingface_hub import snapshot_download
snapshot_download('$BASE_MODEL')" || die "base model download failed"
fi

# --- config ------------------------------------------------------------------
CONFIG="$JOB_DIR/config.yaml"
cp "$CONFIG" /workspace/config.active.yaml
CONFIG=/workspace/config.active.yaml

if [[ $SMOKE -eq 1 ]]; then
  # Smoke = prove the path end-to-end for ~$0.10, not prove the model learns.
  # 20 steps, frequent saves, so checkpoint + pack + retrieval all get exercised.
  echo "SMOKE MODE: capping at 20 steps"
  {
    echo ""
    echo "# --- smoke overrides (launch-pod.sh --smoke) ---"
    echo "max_steps: 20"
    echo "save_steps: 10"
    echo "eval_steps: 10"
  } >> "$CONFIG"
  # num_epochs and evals_per_epoch/saves_per_epoch conflict with the *_steps
  # forms; strip the epoch-relative keys so axolotl reads an unambiguous schedule.
  sed -i '/^evals_per_epoch:/d; /^saves_per_epoch:/d' "$CONFIG"
fi

# --- train -------------------------------------------------------------------
status TRAINING
mkdir -p "$OUT_DIR"
# auto_resume_from_checkpoints in the config picks up /workspace/out on a relaunch,
# which is what makes a Community Cloud pod vanishing survivable rather than fatal.
axolotl train "$CONFIG" || die "axolotl train failed"

# --- pack the deliverable ----------------------------------------------------
status PACKING
[[ -d "$OUT_DIR" ]] || die "no output dir after training"
cd /workspace
# Adapter only (~200-600MB), not the merged 16GB model: merging happens locally
# in convert-to-mlx.sh, and shipping 16GB back over croc is a waste of both ends.
tar -czf adapter.tar.gz \
    --exclude='*/optimizer.pt' \
    --exclude='*/scheduler.pt' \
    --exclude='*/rng_state*' \
    -C /workspace out bootstrap.log \
  || die "packing failed"

echo "adapter.tar.gz: $(du -h /workspace/adapter.tar.gz | cut -f1)"
status DONE
echo "training complete — fetch with: bash docs/runpod-training/fetch-adapter.sh <job>"
