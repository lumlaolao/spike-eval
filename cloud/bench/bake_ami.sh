#!/bin/bash
# Bake the two-layer forge-bench AMI: STABLE HALF ONLY.
#
#   layer 1 (this AMI): AL2023 + corretto-17 + maven + git + vanilla Forge
#     cloned AND `mvn install`ed at the pinned FORGE_COMMIT + the warm ~/.m2
#     that install produces. ~90s of build but ~10 min of dnf/maven downloads
#     — exactly the part that never changes between rounds.
#   layer 2 (boot-time, NOT in the AMI): the forge-overlay + eval payload,
#     copied in fresh per round because it is the volatile half.
#
# Naming: forge-bench-<FORGE_COMMIT[:12]>-<YYYYMMDD>. Launchers must refuse an
# AMI whose embedded pin differs from the repo's FORGE_COMMIT.
#
# Also ensures, idempotently: the private results bucket, the zero-privilege
# instance role (s3 Get on payload/*, Put on results/* ONLY), and a
# zero-inbound security group. NO SECRETS anywhere: this instance runs
# model-free work only (see cloud/README.md security posture).
#
# Cost: one c7i.xlarge for ~15-20 min (~$0.06) + AMI snapshot ~4-5 GB
# (~$0.25/mo). Usage: bash cloud/bench/bake_ami.sh
set -euo pipefail

REGION=${REGION:-us-east-2}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
PIN=$(cat "$REPO/FORGE_COMMIT")
PIN12=${PIN:0:12}
ACCT=$(aws sts get-caller-identity --query Account --output text)
BUCKET=forge-bench-results-$ACCT
ROLE=forge-bench-instance
SG_NAME=forge-bench-noinbound
STAMP=$(date +%Y%m%d)
AMI_NAME=forge-bench-$PIN12-$STAMP
RUNID=$(date +%H%M%S)   # per-run marker key: a stale marker must never satisfy a rerun
log(){ echo "[$(date +%H:%M:%S)] $*"; }

# ---- results bucket (private; account-level Block Public Access stays on) --
if ! aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION"
  aws s3api put-public-access-block --bucket "$BUCKET" \
    --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  log "created private bucket $BUCKET"
else
  log "bucket $BUCKET exists"
fi

# ---- instance role: exactly payload-read + results-write -------------------
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document '{
    "Version":"2012-10-17","Statement":[{"Effect":"Allow",
    "Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  log "created role $ROLE"
fi
aws iam put-role-policy --role-name "$ROLE" --policy-name forge-bench-s3 \
  --policy-document "{
  \"Version\":\"2012-10-17\",\"Statement\":[
   {\"Effect\":\"Allow\",\"Action\":\"s3:GetObject\",\"Resource\":\"arn:aws:s3:::$BUCKET/payload/*\"},
   {\"Effect\":\"Allow\",\"Action\":\"s3:PutObject\",\"Resource\":\"arn:aws:s3:::$BUCKET/results/*\"}]}"
if ! aws iam get-instance-profile --instance-profile-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$ROLE" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$ROLE" --role-name "$ROLE"
  log "created instance profile $ROLE"; sleep 10   # IAM propagation
fi

# ---- zero-inbound security group ------------------------------------------
VPC=$(aws ec2 describe-vpcs --region "$REGION" --filters Name=is-default,Values=true \
      --query 'Vpcs[0].VpcId' --output text)
SG=$(aws ec2 describe-security-groups --region "$REGION" \
     --filters Name=group-name,Values="$SG_NAME" Name=vpc-id,Values="$VPC" \
     --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo None)
if [ "$SG" = "None" ] || [ -z "$SG" ]; then
  SG=$(aws ec2 create-security-group --region "$REGION" --vpc-id "$VPC" \
       --group-name "$SG_NAME" --description "forge bench: no inbound" \
       --query GroupId --output text)
  log "created SG $SG (no ingress rules added — default deny)"
else
  log "SG $SG exists"
fi

# ---- skip if today's AMI already exists ------------------------------------
EXISTING=$(aws ec2 describe-images --region "$REGION" --owners self \
  --filters Name=name,Values="$AMI_NAME" --query 'Images[0].ImageId' --output text)
if [ "$EXISTING" != "None" ] && [ -n "$EXISTING" ]; then
  log "AMI $AMI_NAME already exists: $EXISTING"; exit 0
fi

AL2023=$(aws ssm get-parameter --region "$REGION" \
  --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --query Parameter.Value --output text)

# ---- bake userdata: stable half only, marker to S3 when done ---------------
UD=$(mktemp)
cat > "$UD" <<EOF
#!/bin/bash
exec > /var/log/bake.log 2>&1
set -x
# Xvfb: forge.GuiDesktop touches AWT screen state even headless; bare Linux
# needs a virtual display (HeadlessException measured, probe 2026-07-31).
dnf install -y java-17-amazon-corretto-devel java-17-amazon-corretto maven git tar gzip \
    xorg-x11-server-Xvfb libX11 libXext libXrender libXtst libXi \
    libXrandr libXinerama alsa-lib fontconfig freetype
cd /opt
git clone --filter=blob:none --no-checkout https://github.com/Card-Forge/forge.git
cd forge && git checkout $PIN
# INSTALL, not compile: build-classpath resolves siblings as SNAPSHOT jars from
# the local m2 only after install (the 1232-byte-classpath lesson).
mvn -pl forge-gui-desktop -am install -DskipTests -Dcheckstyle.skip=true -Dmaven.javadoc.skip=true
CPOK=fail
# WITH -am, matching the daily-proven local command: without it, maven resolves
# forge-core from m2 where its descriptor still references the unflattened
# \${revision} parent and dies in 0.7s (measured, bake 2026-07-31). With
# -pl forge-gui-desktop -am the reactor ends at forge-gui-desktop, so the
# shared outputFile lands on the right module; the >=5000-byte gate below
# still catches any last-module-wins degeneracy.
mvn -pl forge-gui-desktop -am org.apache.maven.plugins:maven-dependency-plugin:3.6.0:build-classpath \
    -Dmdep.outputFile=/tmp/forge_cp.txt && [ "\$(wc -c < /tmp/forge_cp.txt)" -ge 5000 ] && CPOK=ok
echo "$PIN" > /opt/forge/.forge-bench-pin
echo "bake pin=$PIN classpath=\$CPOK \$(date -u +%FT%TZ)" > /tmp/bake-marker
# ALWAYS ship the full log: the first bake failed and its only diagnostics
# died with the instance — the serial-console lesson, relearned.
aws s3 cp /var/log/bake.log s3://$BUCKET/results/bake/$AMI_NAME.$RUNID.log
aws s3 cp /tmp/bake-marker s3://$BUCKET/results/bake/$AMI_NAME.$RUNID.marker
EOF

log "launching bake instance (c7i.xlarge, $AL2023)"
IID=$(aws ec2 run-instances --region "$REGION" --image-id "$AL2023" \
  --instance-type c7i.xlarge --iam-instance-profile Name="$ROLE" \
  --security-group-ids "$SG" \
  --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=16,VolumeType=gp3,DeleteOnTermination=true}' \
  --instance-initiated-shutdown-behavior stop \
  --user-data "file://$UD" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=forge-bench-bake}]" \
  --query 'Instances[0].InstanceId' --output text)
log "bake instance $IID launched; waiting for S3 marker (typ. 10-15 min)"

for i in $(seq 1 60); do
  if aws s3 ls "s3://$BUCKET/results/bake/$AMI_NAME.$RUNID.marker" >/dev/null 2>&1; then
    aws s3 cp "s3://$BUCKET/results/bake/$AMI_NAME.$RUNID.marker" - | sed 's/^/[marker] /'
    break
  fi
  [ "$i" = 60 ] && { log "TIMEOUT waiting for bake marker; terminating $IID"; \
    aws ec2 terminate-instances --region "$REGION" --instance-ids "$IID" >/dev/null; exit 1; }
  sleep 30
done
if aws s3 cp "s3://$BUCKET/results/bake/$AMI_NAME.$RUNID.marker" - | grep -q 'classpath=fail'; then
  log "BAKE FAILED: degenerate classpath on the baked build; last 40 log lines:"
  aws s3 cp "s3://$BUCKET/results/bake/$AMI_NAME.$RUNID.log" - 2>/dev/null | tail -40 || true
  aws ec2 terminate-instances --region "$REGION" --instance-ids "$IID" >/dev/null; exit 1
fi

log "stopping $IID for imaging"
aws ec2 stop-instances --region "$REGION" --instance-ids "$IID" >/dev/null
aws ec2 wait instance-stopped --region "$REGION" --instance-ids "$IID"
AMI=$(aws ec2 create-image --region "$REGION" --instance-id "$IID" \
  --name "$AMI_NAME" --description "Forge@$PIN12 prebuilt (mvn install, warm m2). Overlay applied at boot." \
  --query ImageId --output text)
log "creating $AMI ($AMI_NAME); waiting until available"
aws ec2 wait image-available --region "$REGION" --image-ids "$AMI"
aws ec2 terminate-instances --region "$REGION" --instance-ids "$IID" >/dev/null
log "DONE: AMI $AMI ($AMI_NAME); bake instance terminated"
echo "$AMI" > "$HERE/.last-ami"
