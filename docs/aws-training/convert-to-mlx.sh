#!/usr/bin/env bash
# Local post-step: cloud PEFT adapter -> fused MLX 4-bit model the harness can serve.
# Usage: BUCKET=<bucket> [RUN_TAG=stage2-local-v0] bash convert-to-mlx.sh
# Requires: .venv-mlx (mlx_lm) + pip install transformers peft torch (cpu is fine, slow-ok)
set -euo pipefail
BUCKET="${BUCKET:?}"; RUN_TAG="${RUN_TAG:-stage2-local-v0}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="$REPO/models/cloud-$RUN_TAG"; mkdir -p "$WORK"

aws s3 sync "s3://$BUCKET/runs/$RUN_TAG/final" "$WORK/final"

# 1) merge LoRA into full-precision HF weights (bf16). ~16GB download first time.
python3 - <<PY
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
import torch, glob, os
base = "Qwen/Qwen3-8B"
adapter = sorted(glob.glob("$WORK/final") + glob.glob("$WORK/final/checkpoint-*"))[-1]
m = AutoModelForCausalLM.from_pretrained(base, torch_dtype=torch.bfloat16)
m = PeftModel.from_pretrained(m, adapter)
m = m.merge_and_unload()
m.save_pretrained("$WORK/merged")
AutoTokenizer.from_pretrained(base).save_pretrained("$WORK/merged")
PY

# 2) quantize to MLX 4-bit (matches the serving stack's format)
"$REPO/.venv-mlx/bin/python" -m mlx_lm convert \
  --hf-path "$WORK/merged" -q --q-bits 4 \
  --mlx-path "$REPO/models/qwen-mtg-sft-cloud-$RUN_TAG-fused"

echo "serve: .venv-mlx/bin/python -m mlx_lm server --model models/qwen-mtg-sft-cloud-$RUN_TAG-fused \\"
echo "         --prompt-cache-size 8 --prompt-cache-bytes 1500000000"
echo "REMEMBER: re-run eval/replay_match.py held-out probe before trusting cross-pipeline deltas"
echo "cleanup:  rm -rf $WORK/merged   # 16GB of bf16 you don't need after conversion"
