#!/usr/bin/env bash
# Poll a training pod, pull the adapter back, and delete the pod.
#
#   bash docs/runpod-training/fetch-adapter.sh stage2-runpod-v0
#   bash docs/runpod-training/fetch-adapter.sh stage2-runpod-v0 --wait   # block until DONE
#
# Deleting on success is the point: a finished pod that stays up bills GPU-hours
# for nothing, and a *stopped* one still bills its volume at the doubled idle rate.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
JOB="${1:?usage: fetch-adapter.sh <job-name> [--wait]}"
WAIT=0; [[ "${2:-}" == "--wait" ]] && WAIT=1

RUN_DIR="$REPO_ROOT/runs/runpod/$JOB"
[[ -f "$RUN_DIR/pod.env" ]] || { echo "no pod.env in $RUN_DIR -- was this job launched?" >&2; exit 1; }
# shellcheck disable=SC1091
source "$RUN_DIR/pod.env"

SSH_OPTS=(-p "$SSH_PORT" -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)
remote() { ssh "${SSH_OPTS[@]}" "root@$SSH_HOST" "$@"; }

get_status() { remote 'cat /workspace/STATUS 2>/dev/null || echo UNREACHABLE'; }

STATUS="$(get_status)"
if [[ $WAIT -eq 1 ]]; then
  echo "=== waiting for DONE (hard stop $TERMINATE_AT) ==="
  # UNREACHABLE is not treated as failure: a pod mid-reboot, or a laptop that
  # changed networks, both look like this and both recover. Only FAILED and the
  # server-side terminate deadline actually end the wait.
  while [[ "$STATUS" != "DONE" && "$STATUS" != "FAILED" ]]; do
    printf '\r  %s  [%s]        ' "$STATUS" "$(date -u +%H:%M:%S)"
    sleep 60
    STATUS="$(get_status)"
  done
  echo
fi

echo "status: $STATUS"

if [[ "$STATUS" == "FAILED" ]]; then
  echo "=== training failed; pulling the log so the pod can be killed ==="
  scp "${SSH_OPTS[@]}" "root@$SSH_HOST:/workspace/bootstrap.log" "$RUN_DIR/bootstrap.log" || true
  echo "log: $RUN_DIR/bootstrap.log"
  echo "tail:"; tail -30 "$RUN_DIR/bootstrap.log" 2>/dev/null | sed 's/^/  /'
  echo
  echo "pod left UP for debugging -- it is still billing. delete with:"
  echo "  runpodctl pod delete $POD_ID"
  exit 1
fi

if [[ "$STATUS" != "DONE" ]]; then
  echo "not finished yet. re-run with --wait to block, or:"
  echo "  ssh -p $SSH_PORT -i $SSH_KEY root@$SSH_HOST 'tail -f /workspace/bootstrap.log'"
  exit 0
fi

echo "=== fetching adapter ==="
scp "${SSH_OPTS[@]}" "root@$SSH_HOST:/workspace/adapter.tar.gz" "$RUN_DIR/adapter.tar.gz"
scp "${SSH_OPTS[@]}" "root@$SSH_HOST:/workspace/bootstrap.log"  "$RUN_DIR/bootstrap.log" || true

tar -xzf "$RUN_DIR/adapter.tar.gz" -C "$RUN_DIR"
echo "adapter -> $RUN_DIR/out/"

# Only delete once the bytes are verifiably on local disk. Deleting a pod whose
# artifact failed to transfer turns a $1 run into a $1 run you have to redo.
if [[ -s "$RUN_DIR/adapter.tar.gz" ]]; then
  echo "=== deleting pod $POD_ID ==="
  runpodctl pod delete "$POD_ID" && echo "pod deleted -- billing stopped"
else
  echo "adapter.tar.gz is empty; leaving pod UP (still billing): $POD_ID" >&2
  exit 1
fi

cat <<EOF

=== done ===
  adapter:  $RUN_DIR/out/
  next:     bash docs/aws-training/convert-to-mlx.sh   # PEFT -> merged -> MLX 4-bit fused
  then:     serve + eval/replay_match.py as usual

  reminder: cloud-trained (bnb-nf4) != locally-trained (MLX 4bit). Byte-diff one
  rendered example against the mlx_lm server render before trusting an eval delta.
  See docs/aws-training/README.md "Fidelity caveats".
EOF
