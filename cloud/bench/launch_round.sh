#!/bin/bash
# Sharded cloud confirm pass for a mining round: N instances, each runs
# verify_keys --shard i/N against the SAME sample, results to S3, merged
# locally. Model-free by construction (no keys in payload — the security
# invariant); the panel screen runs LOCALLY before this.
#
# Prereqs (all landed 2026-07-31/08-01): baked AMI (bake_ami.sh), 128-vCPU
# quota, verify_keys --shard/--resume (shard-merge equality is TESTED),
# eval/preflight.py green on the sample, panel screen if desired
# (eval/pipelines/screen_candidates.py).
#
# Per-shard instance: c7i.8xlarge (32 vCPU, ~$1.43/h on-demand). The instance
# uploads its incremental out.jsonl every 5 min, so a dead shard loses at
# most 5 min of work — relaunch just that shard with the same args and
# verify_keys --resume finishes it.
#
# Usage:
#   bash cloud/bench/launch_round.sh SAMPLE.jsonl ROUND [N_SHARDS] [--dry-run]
#   --dry-run prints the plan and touches ZERO AWS APIs.
set -euo pipefail

REGION=${REGION:-us-east-2}
JOBS_PER_SHARD=${JOBS_PER_SHARD:-24}
DEADMAN_MIN=${DEADMAN_MIN:-240}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
PIN=$(cat "$REPO/FORGE_COMMIT"); PIN12=${PIN:0:12}

SAMPLE=${1:?usage: launch_round.sh SAMPLE.jsonl ROUND [N_SHARDS] [--dry-run]}
ROUND=${2:?usage: launch_round.sh SAMPLE.jsonl ROUND [N_SHARDS] [--dry-run]}
NSHARDS=${3:-4}
DRY=${4:-}
[ "$NSHARDS" = "--dry-run" ] && { NSHARDS=4; DRY=--dry-run; }
N_POS=$(grep -c . "$SAMPLE")
log(){ echo "[$(date +%H:%M:%S)] $*"; }

log "plan: round $ROUND, $N_POS positions, $NSHARDS x c7i.8xlarge shards, "
log "      --jobs $JOBS_PER_SHARD each, deadman ${DEADMAN_MIN}min, pin $PIN12"
if [ "$DRY" = "--dry-run" ]; then
  log "dry-run: no AWS calls made. Would ship: forge-overlay, launcher, eval/,"
  log "  FORGE_COMMIT, $(basename "$SAMPLE"); launch $NSHARDS instances; poll"
  log "  s3://forge-bench-results-<acct>/results/round-$ROUND/shard-*/summary.json;"
  log "  merge into runs/mine-round-$ROUND/rollout-positions.jsonl"
  exit 0
fi

ACCT=$(aws sts get-caller-identity --query Account --output text)
BUCKET=forge-bench-results-$ACCT
ROLE=forge-bench-instance
SG=$(aws ec2 describe-security-groups --region "$REGION" \
     --filters Name=group-name,Values=forge-bench-noinbound \
     --query 'SecurityGroups[0].GroupId' --output text)
AMI=$(cat "$HERE/.last-ami" 2>/dev/null || true)
[ -n "$AMI" ] || { log "no baked AMI (run bake_ami.sh)"; exit 1; }
AMI_NAME=$(aws ec2 describe-images --region "$REGION" --image-ids "$AMI" \
  --query 'Images[0].Name' --output text)
case "$AMI_NAME" in
  forge-bench-$PIN12-*) : ;;
  *) log "REFUSING: AMI $AMI_NAME does not carry pin $PIN12"; exit 1 ;;
esac

# Local preflight gate: a cloud round launched past a red preflight is the
# ANT-Storm night with a bill attached.
python3 "$REPO/eval/preflight.py" "$SAMPLE" --skip-classpath || {
  log "REFUSING: preflight failed on the sample"; exit 1; }

# ---- payload: overlay + launcher + eval (code, holdouts, decks) ------------
PAYLOAD=$(mktemp -d)/payload.tgz
( cd "$REPO" && COPYFILE_DISABLE=1 tar --exclude '._*' --exclude 'eval/__pycache__' \
    -czf "$PAYLOAD" forge-overlay run-llm-headless.sh FORGE_COMMIT \
    eval/*.py eval/holdouts.json eval/decks eval/plans eval/schemas 2>/dev/null || true )
aws s3 cp "$PAYLOAD" "s3://$BUCKET/payload/round-$ROUND.tgz" --region "$REGION"
aws s3 cp "$SAMPLE" "s3://$BUCKET/payload/round-$ROUND-sample.jsonl" --region "$REGION"
log "payload + sample uploaded"

IIDS=()
for SHARD in $(seq 0 $((NSHARDS - 1))); do
  UD=$(mktemp)
  cat > "$UD" <<EOF
#!/bin/bash
shutdown -h +$DEADMAN_MIN
exec > /var/log/round.log 2>&1
set -x
# Full corretto + X client libs: devel/headless alone leaves libawt_xawt
# unresolvable and Forge dies in GuiDesktop.<clinit> (probe 2026-08-01).
dnf install -y java-17-amazon-corretto xorg-x11-server-Xvfb libX11 libXext \
    libXrender libXtst libXi libXrandr libXinerama alsa-lib fontconfig freetype >/dev/null 2>&1
Xvfb :99 -screen 0 1280x800x24 &
export DISPLAY=:99
mkdir -p /opt/agent && cd /opt/agent
aws s3 cp s3://$BUCKET/payload/round-$ROUND.tgz payload.tgz && tar xzf payload.tgz
aws s3 cp s3://$BUCKET/payload/round-$ROUND-sample.jsonl sample.jsonl
cp -r forge-overlay/forge-gui-desktop/src/main/java/forge/spike /opt/forge/forge-gui-desktop/src/main/java/forge/
find /opt/forge -name '._*' -delete
cp run-llm-headless.sh /opt/forge/run-llm-headless.sh && chmod +x /opt/forge/run-llm-headless.sh
cd /opt/forge
mvn -pl forge-gui-desktop -am install -DskipTests -Dcheckstyle.skip=true -Dmaven.javadoc.skip=true > /tmp/build.log 2>&1 \
  || { echo BUILD_FAILED > /tmp/fail.txt; grep ERROR /tmp/build.log | head -5 >> /tmp/fail.txt; \
       aws s3 cp /tmp/fail.txt s3://$BUCKET/results/round-$ROUND/shard-$SHARD/FAILED.txt; shutdown -h now; }
mvn -pl forge-gui-desktop -am org.apache.maven.plugins:maven-dependency-plugin:3.6.0:build-classpath \
    -Dmdep.outputFile=/tmp/forge_cp.txt > /tmp/cp.log 2>&1
[ "\$(wc -c < /tmp/forge_cp.txt)" -lt 5000 ] && { echo CLASSPATH_TOO_SMALL > /tmp/fail.txt; \
  aws s3 cp /tmp/fail.txt s3://$BUCKET/results/round-$ROUND/shard-$SHARD/FAILED.txt; shutdown -h now; }
cd /opt/agent
# incremental uploader + flight recorder: a dead shard loses <=5 min of
# rollouts, and a STALLED shard remains diagnosable from S3 (the 84% stall
# of 2026-08-01 left no df/ps/vk.log evidence — never again).
( while true; do sleep 300; \
    aws s3 cp out.jsonl s3://$BUCKET/results/round-$ROUND/shard-$SHARD/out.partial.jsonl 2>/dev/null; \
    { date -u; df -h / /tmp | tail -2; ps -eo pid,etime,pcpu,rss,comm --sort=-rss | head -12; \
      tail -20 /tmp/vk.log 2>/dev/null; ls -laS /opt/agent/verify-keys-work 2>/dev/null | head -5; } > /tmp/flight.txt 2>&1; \
    aws s3 cp /tmp/flight.txt s3://$BUCKET/results/round-$ROUND/shard-$SHARD/flight.txt 2>/dev/null; done ) &
python3 eval/verify_keys.py sample.jsonl --out out.jsonl \
  --lane rollout --driver engine --forge-home /opt/forge \
  --screen-n 8 --n-per-option 45 --jobs $JOBS_PER_SHARD \
  --shard $SHARD/$NSHARDS --strict --summary summary.json > /tmp/vk.log 2>&1
RC=\$?
aws s3 cp out.jsonl s3://$BUCKET/results/round-$ROUND/shard-$SHARD/out.jsonl
aws s3 cp summary.json s3://$BUCKET/results/round-$ROUND/shard-$SHARD/summary.json
tail -50 /tmp/vk.log > /tmp/vk-tail.txt; echo "verify_keys rc=\$RC" >> /tmp/vk-tail.txt
aws s3 cp /tmp/vk-tail.txt s3://$BUCKET/results/round-$ROUND/shard-$SHARD/vk-tail.txt
aws s3 cp /var/log/round.log s3://$BUCKET/results/round-$ROUND/shard-$SHARD/round.log
shutdown -h now
EOF
  IID=$(aws ec2 run-instances --region "$REGION" --image-id "$AMI" \
    --instance-type c7i.8xlarge --iam-instance-profile Name="$ROLE" \
    --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=48,VolumeType=gp3,DeleteOnTermination=true}' \
    --security-group-ids "$SG" \
    --instance-initiated-shutdown-behavior terminate \
    --user-data "file://$UD" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=forge-round-$ROUND-shard-$SHARD}]" \
    --query 'Instances[0].InstanceId' --output text)
  IIDS+=("$IID")
  log "shard $SHARD/$NSHARDS: $IID"
done

log "polling for $NSHARDS shard summaries (typ. sized by slowest shard)"
OUTDIR=$REPO/runs/mine-round-$ROUND/cloud
mkdir -p "$OUTDIR"
DONE=0
for i in $(seq 1 $((DEADMAN_MIN * 2))); do
  DONE=0
  for SHARD in $(seq 0 $((NSHARDS - 1))); do
    aws s3 ls "s3://$BUCKET/results/round-$ROUND/shard-$SHARD/summary.json" >/dev/null 2>&1 && DONE=$((DONE + 1))
    aws s3 ls "s3://$BUCKET/results/round-$ROUND/shard-$SHARD/FAILED.txt" >/dev/null 2>&1 && {
      log "SHARD $SHARD FAILED:"; aws s3 cp "s3://$BUCKET/results/round-$ROUND/shard-$SHARD/FAILED.txt" -; }
  done
  [ "$DONE" = "$NSHARDS" ] && break
  sleep 30
done
[ "$DONE" = "$NSHARDS" ] || { log "TIMEOUT: only $DONE/$NSHARDS shards reported"; exit 1; }

for SHARD in $(seq 0 $((NSHARDS - 1))); do
  aws s3 cp "s3://$BUCKET/results/round-$ROUND/shard-$SHARD/out.jsonl" "$OUTDIR/shard-$SHARD.jsonl"
  aws s3 cp "s3://$BUCKET/results/round-$ROUND/shard-$SHARD/summary.json" "$OUTDIR/shard-$SHARD-summary.json"
done
cat "$OUTDIR"/shard-*.jsonl > "$REPO/runs/mine-round-$ROUND/rollout-positions.jsonl"
log "merged $(wc -l < "$REPO/runs/mine-round-$ROUND/rollout-positions.jsonl") rows -> runs/mine-round-$ROUND/rollout-positions.jsonl"
log "verified: $(grep -c '"status": "verified"' "$REPO/runs/mine-round-$ROUND/rollout-positions.jsonl" || true)"
log "ROUND $ROUND CLOUD CONFIRM COMPLETE — instances self-terminate"
