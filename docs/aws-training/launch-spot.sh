#!/usr/bin/env bash
# Launch a spot GPU instance that trains and TERMINATES ITSELF.
# Usage: BUCKET=<you>-mtg-qlora [ITYPE=g5.xlarge] [MAXPRICE=0.55] bash launch-spot.sh
set -euo pipefail

BUCKET="${BUCKET:?set BUCKET=<s3 bucket name>}"
ITYPE="${ITYPE:-g5.xlarge}"          # g6.xlarge (L4, cheapest) | g5.xlarge (A10G) | g6e.xlarge (L40S)
MAXPRICE="${MAXPRICE:-0.55}"         # spot ceiling $/h — you pay market, this only caps spikes
REGION="${AWS_REGION:-us-east-1}"
RUN_TAG="${RUN_TAG:-stage2-local-v0}"
ROLE_NAME="${ROLE_NAME:-mtg-qlora-ec2}"   # instance role with RW on $BUCKET only
KEY_NAME="${KEY_NAME:-}"             # optional ssh key for debugging

HERE="$(cd "$(dirname "$0")" && pwd)"

# Latest AWS Deep Learning AMI (PyTorch, Ubuntu 22.04) — drivers + CUDA preinstalled.
AMI=$(aws ssm get-parameter --region "$REGION" \
  --name /aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id \
  --query Parameter.Value --output text)

# Inject bucket/tag into user-data, then base64 it.
UD=$(sed -e "s|__BUCKET__|$BUCKET|g" -e "s|__RUN_TAG__|$RUN_TAG|g" "$HERE/user-data.sh" | base64)

aws ec2 run-instances --region "$REGION" \
  --image-id "$AMI" \
  --instance-type "$ITYPE" \
  --instance-market-options "MarketType=spot,SpotOptions={MaxPrice=$MAXPRICE,SpotInstanceType=one-time}" \
  --instance-initiated-shutdown-behavior terminate \
  --iam-instance-profile "Name=$ROLE_NAME" \
  --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=100,VolumeType=gp3,DeleteOnTermination=true}' \
  ${KEY_NAME:+--key-name "$KEY_NAME"} \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=mtg-qlora-$RUN_TAG}]" \
  --user-data "$UD" \
  --count 1 \
  --query 'Instances[0].InstanceId' --output text

echo "watch progress:  aws s3 ls s3://$BUCKET/runs/$RUN_TAG/ --recursive | tail"
echo "kill manually:   aws ec2 terminate-instances --instance-ids <id>"
# Self-termination: user-data ends with 'shutdown -h now' and shutdown
# behavior is 'terminate' -> billing stops the moment training finishes.
