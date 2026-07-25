#!/usr/bin/env bash
# Launch a Runpod GPU pod, ship the job over SSH, start training detached.
#
#   bash docs/runpod-training/launch-pod.sh --job stage2-runpod-v0 --smoke
#   bash docs/runpod-training/launch-pod.sh --job stage2-runpod-v0 \
#          --gpu "NVIDIA GeForce RTX 4090" --max-hours 3
#
# Cost posture, in order of how much money each saves:
#   1. --terminate-after is a server-side wall-clock guillotine. A hung job cannot
#      bill past it, even if this laptop is asleep. Always set. Default 2x estimate.
#   2. The pod is DELETED, never stopped: a stopped pod bills its volume at
#      $0.20/GB/mo -- double the running rate -- for doing nothing.
#   3. No network volume by default. 100GB is $7/mo, ~6 training runs.
#
# NOT YET EXECUTED against a real account. Run --smoke first (~$0.10).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

JOB=""
GPU="${RUNPOD_GPU:-NVIDIA GeForce RTX 4090}"
CLOUD="${RUNPOD_CLOUD:-COMMUNITY}"
MAX_HOURS=""
SMOKE=0
CONTAINER_GB=30      # Qwen3-8B is 16.4GB; the 20GB default does not fit it
VOLUME_GB=60         # dataset + checkpoints + adapter, mounted at /workspace
SSH_KEY="${RUNPOD_SSH_KEY:-$HOME/.ssh/id_ed25519}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --job)         JOB="$2"; shift 2 ;;
    --gpu)         GPU="$2"; shift 2 ;;
    --cloud)       CLOUD="$2"; shift 2 ;;
    --max-hours)   MAX_HOURS="$2"; shift 2 ;;
    --volume-gb)   VOLUME_GB="$2"; shift 2 ;;
    --smoke)       SMOKE=1; shift ;;
    -h|--help)     sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 1 ;;
  esac
done

[[ -n "$JOB" ]] || { echo "--job is required" >&2; exit 1; }
JOB_TAR="$REPO_ROOT/runs/runpod/$JOB/job.tar.gz"
[[ -f "$JOB_TAR" ]] || { echo "no job tarball at $JOB_TAR -- run pack-job.sh first" >&2; exit 1; }
command -v runpodctl >/dev/null || { echo "runpodctl not installed" >&2; exit 1; }
runpodctl user >/dev/null 2>&1 || { echo "runpodctl not authenticated -- run: runpodctl doctor" >&2; exit 1; }

# Smoke runs are 20 steps; anything over an hour means something hung.
[[ -z "$MAX_HOURS" ]] && { [[ $SMOKE -eq 1 ]] && MAX_HOURS=1 || MAX_HOURS=4; }
TERMINATE_AT="$(python3 -c "
import datetime
print((datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=$MAX_HOURS)).strftime('%Y-%m-%dT%H:%M:%SZ'))")"

POD_NAME="mtg-$JOB-$(date +%m%d-%H%M)"

echo "=== launching ==="
printf '  job:        %s (%s)\n' "$JOB" "$(du -h "$JOB_TAR" | cut -f1)"
printf '  gpu:        %s (%s)\n' "$GPU" "$CLOUD"
printf '  hard stop:  %s (+%sh)\n' "$TERMINATE_AT" "$MAX_HOURS"
printf '  mode:       %s\n' "$([[ $SMOKE -eq 1 ]] && echo SMOKE || echo FULL)"
echo

CREATE_JSON="$(runpodctl pod create \
  --name "$POD_NAME" \
  --image "runpod/pytorch:1.0.3-cu1281-torch291-ubuntu2404" \
  --gpu-id "$GPU" \
  --cloud-type "$CLOUD" \
  --gpu-count 1 \
  --container-disk-in-gb "$CONTAINER_GB" \
  --volume-in-gb "$VOLUME_GB" \
  --volume-mount-path /workspace \
  --ports '22/tcp' \
  --ssh \
  --terminate-after "$TERMINATE_AT" \
  -o json)"

POD_ID="$(echo "$CREATE_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id') or d.get('podId') or '')")"
[[ -n "$POD_ID" ]] || { echo "could not parse pod id from:"; echo "$CREATE_JSON"; exit 1; }

echo "pod: $POD_ID"
echo "$POD_ID" > "$REPO_ROOT/runs/runpod/$JOB/POD_ID"
echo
echo "!! if anything below fails, the pod is still billing. kill it with:"
echo "     runpodctl pod delete $POD_ID"
echo

# --- wait for SSH ------------------------------------------------------------
# Direct TCP, not the ssh.runpod.io proxy: the proxy gives an interactive shell
# only and does not carry scp/sftp/rsync, which is exactly what we need here.
echo "=== waiting for ssh (up to 10 min) ==="
SSH_HOST=""; SSH_PORT=""
for i in $(seq 1 60); do
  INFO="$(runpodctl pod get "$POD_ID" -o json 2>/dev/null || true)"
  read -r SSH_HOST SSH_PORT <<<"$(echo "$INFO" | python3 -c "
import sys, json
try: d = json.load(sys.stdin)
except Exception: raise SystemExit
for p in (d.get('runtime') or {}).get('ports') or d.get('ports') or []:
    if str(p.get('privatePort')) == '22' and p.get('ip') and p.get('publicPort'):
        print(p['ip'], p['publicPort']); break
" 2>/dev/null)"
  [[ -n "${SSH_HOST:-}" && -n "${SSH_PORT:-}" ]] && break
  printf '\r  ... %ds' $((i*10)); sleep 10
done
echo
[[ -n "${SSH_HOST:-}" ]] || {
  echo "no direct-TCP ssh endpoint appeared." >&2
  echo "the pod is RUNNING AND BILLING. either:" >&2
  echo "  runpodctl pod delete $POD_ID          # give up, stop the meter" >&2
  echo "  runpodctl ssh info $POD_ID            # inspect, then transfer manually" >&2
  exit 1
}

SSH_OPTS=(-p "$SSH_PORT" -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)
echo "ssh: root@$SSH_HOST:$SSH_PORT"

for i in $(seq 1 30); do
  ssh "${SSH_OPTS[@]}" "root@$SSH_HOST" true 2>/dev/null && break
  printf '\r  handshake ... %ds' $((i*5)); sleep 5
done
echo

# --- ship the job ------------------------------------------------------------
# ~50MB of jsonl + a YAML. No credentials, no repo, no engine: see README
# "Getting code onto the pod" for why a private-repo clone is the wrong shape here.
echo "=== shipping job ==="
scp "${SSH_OPTS[@]}" "$JOB_TAR" "root@$SSH_HOST:/workspace/job.tar.gz"
ssh "${SSH_OPTS[@]}" "root@$SSH_HOST" "cd /workspace && tar -xzf job.tar.gz && ls job/"

# --- start training detached -------------------------------------------------
# nohup + setsid so training survives this laptop closing its lid.
echo "=== starting training ==="
SMOKE_FLAG=""; [[ $SMOKE -eq 1 ]] && SMOKE_FLAG="--smoke"
ssh "${SSH_OPTS[@]}" "root@$SSH_HOST" \
  "cd /workspace && setsid nohup bash job/pod-bootstrap.sh $SMOKE_FLAG > /workspace/nohup.log 2>&1 < /dev/null & echo started"

cat > "$REPO_ROOT/runs/runpod/$JOB/pod.env" <<EOF
POD_ID=$POD_ID
SSH_HOST=$SSH_HOST
SSH_PORT=$SSH_PORT
SSH_KEY=$SSH_KEY
TERMINATE_AT=$TERMINATE_AT
EOF

cat <<EOF

=== launched ===
  watch:   ssh -p $SSH_PORT -i $SSH_KEY root@$SSH_HOST 'tail -f /workspace/bootstrap.log'
  status:  ssh -p $SSH_PORT -i $SSH_KEY root@$SSH_HOST 'cat /workspace/STATUS'
  fetch:   bash docs/runpod-training/fetch-adapter.sh $JOB
  KILL:    runpodctl pod delete $POD_ID

  hard stop at $TERMINATE_AT regardless. fetch-adapter.sh deletes the pod on success.
EOF
