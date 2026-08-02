#!/bin/bash
# First-boot probe of a baked forge-bench AMI: canary + throughput, TO S3.
#
# The 2026-07-29 throughput benchmark was lost to the EC2 serial console
# (which never populates for short-lived instances), so cloud parallel
# throughput was never measured and every cloud cost estimate rests on it.
# This probe's FIRST real output — before any other work — is:
#   1. the seed-42 floor-mirror canary ("A after 108 turns"); if it does not
#      reproduce bit-exactly on x86-64 Linux, that is an architecture caveat
#      that belongs in the suite manifest's known_limits, and
#   2. games/hour at --jobs 1, 16, 32,
# both written to s3://<bucket>/results/probe/ via the instance role.
#
# Uploads the volatile half (overlay + launcher + decks) as payload.tgz with
# COPYFILE_DISABLE (AppleDouble ._* files break checkstyle — paid-for lesson).
# NO SECRETS in the payload or on the box; model-free work only.
#
# Cost: c7i.8xlarge (32 vCPU, ~$1.43/h) for ~20-30 min ≈ $0.60.
# Usage: bash cloud/bench/probe_ami.sh [AMI_ID]   (default: cloud/bench/.last-ami)
set -euo pipefail

REGION=${REGION:-us-east-2}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
PIN=$(cat "$REPO/FORGE_COMMIT"); PIN12=${PIN:0:12}
ACCT=$(aws sts get-caller-identity --query Account --output text)
BUCKET=forge-bench-results-$ACCT
ROLE=forge-bench-instance
SG_NAME=forge-bench-noinbound
AMI=${1:-$(cat "$HERE/.last-ami" 2>/dev/null || true)}
[ -n "$AMI" ] || { echo "usage: probe_ami.sh AMI_ID (or run bake_ami.sh first)"; exit 2; }
log(){ echo "[$(date +%H:%M:%S)] $*"; }

# Refuse an AMI whose name does not carry the current pin.
AMI_NAME=$(aws ec2 describe-images --region "$REGION" --image-ids "$AMI" \
  --query 'Images[0].Name' --output text)
case "$AMI_NAME" in
  forge-bench-$PIN12-*) log "AMI $AMI ($AMI_NAME) matches pin $PIN12" ;;
  *) log "REFUSING: AMI name '$AMI_NAME' does not carry current pin $PIN12"; exit 1 ;;
esac

# ---- payload: volatile half only -------------------------------------------
PAYLOAD=$(mktemp -d)/payload.tgz
( cd "$REPO" && COPYFILE_DISABLE=1 tar --exclude '._*' -czf "$PAYLOAD" \
    forge-overlay run-llm-headless.sh eval/decks )
aws s3 cp "$PAYLOAD" "s3://$BUCKET/payload/probe-$PIN12.tgz" --region "$REGION"
log "payload uploaded ($(du -h "$PAYLOAD" | cut -f1))"

SG=$(aws ec2 describe-security-groups --region "$REGION" \
     --filters Name=group-name,Values="$SG_NAME" \
     --query 'SecurityGroups[0].GroupId' --output text)
STAMP=$(date +%Y%m%d-%H%M%S)

UD=$(mktemp)
cat > "$UD" <<EOF
#!/bin/bash
shutdown -h +${DEADMAN_MIN:-45}   # deadman: nothing here is worth a forgotten instance
exec > /var/log/probe.log 2>&1
set -x
T0=\$(date +%s)
# Status uploader from second zero: two probe attempts died with their
# diagnostics because uploads only happened at the END — a hang uploads
# nothing. Every 60s the logs-so-far land in S3.
( while true; do sleep 60; tar czf /tmp/status.tgz /var/log/probe.log /tmp/canary.log /tmp/build.log /tmp/g-*.log 2>/dev/null; \
    aws s3 cp /tmp/status.tgz s3://$BUCKET/results/probe/$STAMP-status.tgz 2>/dev/null; done ) &
# forge.GuiDesktop.initializeScreenScale touches AWT screen state even in the
# headless runner; macOS provides it natively, bare Linux throws
# HeadlessException (measured, probe 2026-07-31). One shared Xvfb serves all
# concurrent games. (Baked into the NEXT AMI; installed here for this one.)
# Xvfb alone is NOT enough (measured 2026-08-01): Corretto's headful AWT
# (libawt_xawt) dlopens the X CLIENT libs, which the server package does not
# pull on AL2023 — "no headful library support was found" with Xvfb running.
dnf install -y xorg-x11-server-Xvfb libX11 libXext libXrender libXtst libXi \
    libXrandr libXinerama alsa-lib fontconfig freetype 2>&1 | tail -3
# AL2023 ships corretto as headless + full SEPARATELY; devel alone may leave
# libawt_xawt.so absent = "no headful library support was found", verbatim.
dnf install -y java-17-amazon-corretto 2>&1 | tail -2
JHOME=\$(dirname \$(dirname \$(readlink -f \$(command -v java))))
echo "JHOME=\$JHOME"
ls "\$JHOME/lib/libawt_xawt.so" 2>&1
ldd "\$JHOME/lib/libawt_xawt.so" 2>&1 | grep -i 'not found' && echo AWT_XAWT_DEPS_MISSING || echo AWT_XAWT_DEPS_OK
command -v Xvfb || { echo "XVFB_MISSING after xorg-x11-server-Xvfb; trying alternates"; \
  dnf install -y Xvfb 2>&1 | tail -2; dnf install -y xorg-server-Xvfb 2>&1 | tail -2; }
command -v Xvfb && echo XVFB_PRESENT || echo XVFB_STILL_MISSING
Xvfb :99 -screen 0 1280x800x24 > /tmp/xvfb.log 2>&1 &
sleep 2
export DISPLAY=:99
ls -la /tmp/.X11-unix/ 2>&1 | head -3; tail -3 /tmp/xvfb.log 2>/dev/null
# Pure-AWT micro-probe: isolates the JVM/display question from Forge entirely
cat > /tmp/P.java <<'JEOF'
public class P { public static void main(String[] a) throws Exception {
  System.out.println("isHeadless=" + java.awt.GraphicsEnvironment.isHeadless());
  System.out.println("screen=" + java.awt.GraphicsEnvironment.getLocalGraphicsEnvironment().getDefaultScreenDevice());
} }
JEOF
(cd /tmp && javac P.java && java P) 2>&1 | tail -3
cd /opt/agent 2>/dev/null || mkdir -p /opt/agent && cd /opt/agent
aws s3 cp s3://$BUCKET/payload/probe-$PIN12.tgz payload.tgz
tar xzf payload.tgz
cp -r forge-overlay/forge-gui-desktop/src/main/java/forge/spike \
      /opt/forge/forge-gui-desktop/src/main/java/forge/
find /opt/forge -name '._*' -delete
# At the forge ROOT, exactly as install.sh places it: the launcher does
# cd "\$(dirname \$0)/forge-gui-desktop" itself. The 07-29 run put it inside
# the module dir — and since its throughput output was lost to the serial
# console, that placement bug was never observed until this probe.
cp run-llm-headless.sh /opt/forge/run-llm-headless.sh && chmod +x /opt/forge/run-llm-headless.sh
cd /opt/forge
# Overlay build on the warm m2 — should be ~90s (the whole point of the AMI).
mvn -pl forge-gui-desktop -am install -DskipTests -Dcheckstyle.skip=true -Dmaven.javadoc.skip=true > /tmp/build.log 2>&1 \
  || { echo "BUILD_FAILED" > /tmp/probe.txt; grep ERROR /tmp/build.log | head -5 >> /tmp/probe.txt; \
       aws s3 cp /tmp/probe.txt s3://$BUCKET/results/probe/$STAMP.txt; shutdown -h now; }
# -am required: without it forge-core resolves from m2 with an unflattened
# \${revision} parent and dies (measured, bake 2026-07-31); the byte gate
# below catches last-module-wins either way.
mvn -pl forge-gui-desktop -am org.apache.maven.plugins:maven-dependency-plugin:3.6.0:build-classpath \
    -Dmdep.outputFile=/tmp/forge_cp.txt > /tmp/cp.log 2>&1
CPB=\$(wc -c < /tmp/forge_cp.txt || echo 0)
[ "\$CPB" -lt 5000 ] && { echo "CLASSPATH_TOO_SMALL \$CPB" > /tmp/probe.txt; \
  aws s3 cp /tmp/probe.txt s3://$BUCKET/results/probe/$STAMP.txt; shutdown -h now; }
PROBE_LEVELS="${PROBE_LEVELS:-1 16 32}"
{
echo "probe ami=$AMI pin=$PIN nproc=\$(nproc) build_s=\$(( \$(date +%s) - T0 )) cp_bytes=\$CPB"
# Default decks (Grixis Reanimator vs Mardu Energy): the 108-turn canary pin
# is DEFINED on them, and floor mirrors on untested decks risk the unbounded
# game-loop pathology measured on floor-sneak-and-show 2026-08-01 (13 GB
# logs). Do not pass DECK_A/DECK_B here.
# 1) CANARY FIRST (the thing the last run lost): seed-42 floor mirror.
( cd /opt/forge && GAME_SEED=42 CONTROLLER_A=floor CONTROLLER_B=floor \
  timeout 600 bash run-llm-headless.sh > /tmp/canary.log 2>&1 )
echo "canary: \$(grep -m1 'outcome:' /tmp/canary.log || echo NO_OUTCOME)"
echo "--- canary log tail ---"; tail -8 /tmp/canary.log
# 2) throughput at several parallelism levels
for N in \$PROBE_LEVELS; do
  t0=\$(date +%s)
  for i in \$(seq 1 \$N); do
    ( cd /opt/forge && GAME_SEED=\$((100+i)) CONTROLLER_A=floor CONTROLLER_B=floor \
      timeout 600 bash run-llm-headless.sh > /tmp/g-\$N-\$i.log 2>&1 ) &
  done
  wait
  e=\$(( \$(date +%s) - t0 ))
  ok=\$(grep -l 'outcome:' /tmp/g-\$N-*.log 2>/dev/null | wc -l)
  echo "throughput jobs=\$N ok=\$ok/\$N wall=\${e}s games_per_hour=\$(python3 -c "print('%.0f'%(\$N*3600.0/max(\$e,1)))" 2>/dev/null || echo '?')"
done
echo "probe_total_s \$(( \$(date +%s) - T0 ))"
} > /tmp/probe.txt 2>&1
aws s3 cp /tmp/probe.txt s3://$BUCKET/results/probe/$STAMP.txt
shutdown -h now
EOF

log "launching probe instance c7i.8xlarge from $AMI"
IID=$(aws ec2 run-instances --region "$REGION" --image-id "$AMI" \
  --instance-type "${PROBE_ITYPE:-c7i.8xlarge}" --iam-instance-profile Name="$ROLE" \
  --security-group-ids "$SG" \
  --instance-initiated-shutdown-behavior terminate \
  --user-data "file://$UD" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=forge-bench-probe}]" \
  --query 'Instances[0].InstanceId' --output text)
log "probe $IID launched (terminates itself); polling for s3://$BUCKET/results/probe/$STAMP.txt"

for i in $(seq 1 80); do
  if aws s3 ls "s3://$BUCKET/results/probe/$STAMP.txt" >/dev/null 2>&1; then
    echo "---- probe result ----"
    aws s3 cp "s3://$BUCKET/results/probe/$STAMP.txt" -
    exit 0
  fi
  sleep 30
done
log "TIMEOUT: no probe result after 40 min; check instance $IID"; exit 1
