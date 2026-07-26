#!/usr/bin/env bash
# EC2 user-data: bootstrap -> train -> upload -> SELF-TERMINATE.
# Placeholders __BUCKET__ / __RUN_TAG__ are injected by launch-spot.sh.
set -uxo pipefail
exec > /var/log/train-bootstrap.log 2>&1

BUCKET=__BUCKET__
RUN_TAG=__RUN_TAG__
WORK=/workspace
mkdir -p $WORK/data $WORK/out
cd $WORK

# ---- guard rail: whatever happens below, the box dies within 8 hours.
echo "shutdown -h now" | at now + 480 minutes || (sleep 28800 && shutdown -h now) &

# ---- deps (pinned; DLAMI provides driver + CUDA + python3.10)
python3 -m venv venv && . venv/bin/activate
pip install --no-cache-dir "axolotl[flash-attn]==0.8.0" "awscli"  # pin = repeatability

# ---- data + config
aws s3 sync "s3://$BUCKET/data/$RUN_TAG/" $WORK/data/           # train.jsonl + valid.jsonl
aws s3 cp   "s3://$BUCKET/config/axolotl-qwen3-8b-qlora.yaml" $WORK/config.yaml \
  || aws s3 cp "s3://$BUCKET/config.yaml" $WORK/config.yaml

# ---- resume support: newest checkpoint from a previous (interrupted) spot run
# (config sets auto_resume_from_checkpoints: true, so axolotl picks up the
# newest checkpoint present in output_dir — no CLI flag needed)
LATEST=$(aws s3 ls "s3://$BUCKET/runs/$RUN_TAG/checkpoints/" 2>/dev/null \
         | awk '{print $2}' | tr -d / | sort -t- -k2 -n | tail -1)
if [ -n "$LATEST" ]; then
  aws s3 sync "s3://$BUCKET/runs/$RUN_TAG/checkpoints/$LATEST" "$WORK/out/$LATEST"
fi

# ---- background checkpoint sync every 5 min (spot-interruption insurance)
( while true; do
    aws s3 sync $WORK/out "s3://$BUCKET/runs/$RUN_TAG/checkpoints" \
      --exclude "*" --include "checkpoint-*/*" --quiet || true
    sleep 300
  done ) &
SYNC_PID=$!

# ---- train
axolotl train $WORK/config.yaml
RC=$?

# ---- final upload: adapter + logs (+ marker with exit code)
kill $SYNC_PID || true
aws s3 sync $WORK/out "s3://$BUCKET/runs/$RUN_TAG/final" --quiet
aws s3 cp /var/log/train-bootstrap.log "s3://$BUCKET/runs/$RUN_TAG/final/bootstrap.log"
echo "$RC" | aws s3 cp - "s3://$BUCKET/runs/$RUN_TAG/final/EXIT_CODE"

# ---- die (shutdown behavior = terminate -> billing stops here)
shutdown -h now
