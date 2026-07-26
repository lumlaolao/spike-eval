#!/usr/bin/env bash
# bootstrap_g5.sh — backward-compatible wrapper.
#
# The bootstrap is provider-agnostic now: cloud/bootstrap_gpu.sh detects the
# environment (root/sudo, GPU, /workspace volume, container, DLAMI markers)
# and works on AWS DLAMI, RunPod, Vast.ai, and bare Ubuntu 22.04. This
# wrapper keeps the old AWS entry point alive: it forwards every argument,
# adding the --aws provider hint.
set -euo pipefail
exec bash "$(cd "$(dirname "$0")" && pwd)/bootstrap_gpu.sh" --aws "$@"
