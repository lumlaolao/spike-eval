#!/usr/bin/env bash
# Pack a self-contained Runpod training job: data + config + bootstrap, no secrets.
#
#   bash docs/runpod-training/pack-job.sh <dataset-mlx-dir> <job-name>
#   bash docs/runpod-training/pack-job.sh datasets/stage2-local-v0-mlx stage2-runpod-v0
#
# Produces runs/runpod/<job-name>/job.tar.gz containing exactly what the pod needs:
#   data/train.jsonl  data/valid.jsonl  config.yaml  pod-bootstrap.sh
#
# Deliberately NOT included: secrets.env, the Forge overlay, the Java harness, any
# API key or git credential. A training pod is rented hardware; it gets the rows and
# the hyperparameters and nothing else.
set -euo pipefail

DATA_DIR="${1:?usage: pack-job.sh <dataset-mlx-dir> <job-name>}"
JOB_NAME="${2:?usage: pack-job.sh <dataset-mlx-dir> <job-name>}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERE="$REPO_ROOT/docs/runpod-training"
CONFIG="${CONFIG:-$REPO_ROOT/docs/aws-training/axolotl-qwen3-8b-qlora.yaml}"
OUT_DIR="$REPO_ROOT/runs/runpod/$JOB_NAME"

[[ -f "$DATA_DIR/train.jsonl" ]] || { echo "no train.jsonl in $DATA_DIR" >&2; exit 1; }
[[ -f "$DATA_DIR/valid.jsonl" ]] || { echo "no valid.jsonl in $DATA_DIR" >&2; exit 1; }
[[ -f "$CONFIG" ]] || { echo "no axolotl config at $CONFIG" >&2; exit 1; }

# The mlx dataset dirs sometimes carry a meta.jsonl the trainer must not read;
# copy only the two files by name rather than globbing the directory.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/job/data"
cp "$DATA_DIR/train.jsonl" "$DATA_DIR/valid.jsonl" "$STAGE/job/data/"
cp "$CONFIG"               "$STAGE/job/config.yaml"
cp "$HERE/pod-bootstrap.sh" "$STAGE/job/"

# Provenance travels with the job: which rows, which config, which harness commit.
# A cloud run you cannot trace back to a dataset is an eval you cannot defend.
cat > "$STAGE/job/MANIFEST.txt" <<EOF
job:            $JOB_NAME
packed:         $(date -u +%Y-%m-%dT%H:%M:%SZ)
dataset_dir:    $DATA_DIR
train_rows:     $(wc -l < "$DATA_DIR/train.jsonl" | tr -d ' ')
valid_rows:     $(wc -l < "$DATA_DIR/valid.jsonl" | tr -d ' ')
train_sha256:   $(shasum -a 256 "$DATA_DIR/train.jsonl" | cut -d' ' -f1)
valid_sha256:   $(shasum -a 256 "$DATA_DIR/valid.jsonl" | cut -d' ' -f1)
config:         $(basename "$CONFIG")
config_sha256:  $(shasum -a 256 "$CONFIG" | cut -d' ' -f1)
harness_commit: $(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)
forge_commit:   $(cat "$REPO_ROOT/FORGE_COMMIT" 2>/dev/null || echo unknown)
EOF

mkdir -p "$OUT_DIR"
tar -czf "$OUT_DIR/job.tar.gz" -C "$STAGE" job
cp "$STAGE/job/MANIFEST.txt" "$OUT_DIR/MANIFEST.txt"

echo "packed: $OUT_DIR/job.tar.gz ($(du -h "$OUT_DIR/job.tar.gz" | cut -f1))"
sed 's/^/  /' "$OUT_DIR/MANIFEST.txt"
echo
echo "next: bash docs/runpod-training/launch-pod.sh --job $JOB_NAME --smoke"
