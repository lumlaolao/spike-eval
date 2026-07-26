#!/usr/bin/env bash
# bootstrap_gpu.sh — provider-agnostic one-command setup for a cloud GPU box.
#
# Supported environments (detected by FACTS, not provider names):
#   - AWS Deep Learning OSS Nvidia Driver AMI, Ubuntu 22.04 (sudo user)
#   - RunPod PyTorch template   (container: root, no sudo, /workspace volume)
#   - Vast.ai PyTorch templates (container: similar assumptions)
#   - generic bare Ubuntu 22.04 with NVIDIA drivers already installed
#
# What it does (in order):
#   1. detect environment facts (root/sudo, GPU, /workspace, container, DLAMI)
#   2. apt: OpenJDK 17, Maven, git, tmux, python3-venv, xvfb — only what's
#      missing; NEVER attempts NVIDIA driver installs (containers ship them;
#      bare boxes must bring their own)
#   3. clone mtg-forge-llm-agent (or use an rsync'd copy)
#   4. clone Forge and check out EXACTLY the commit pinned in FORGE_COMMIT
#   5. overlay the spike files (install.sh) + patch the launcher's macOS
#      JAVA_HOME for Linux
#   6. build Forge (offline first, network fallback)
#   7. generate /tmp/forge_cp.txt (the launcher does
#      CP="target/classes:$(cat /tmp/forge_cp.txt)" and exits 1 without it;
#      /tmp dies on reboot/restart, so a cached copy lives on WORK_ROOT)
#   8. vLLM venv + serve_student.sh (OpenAI-compatible endpoint on :8080,
#      served model name "default_model")
#   9. smoke tests: seed-42 floor mirror canary (expected: identity A wins,
#      108 turns — BLOCKING) and, when a server is up AND the user has
#      exported the egress opt-in themselves, a single-game LLM smoke test.
#
# Everything lands under WORK_ROOT: /workspace when a persistent volume
# exists (RunPod/Vast — survives pod stop), else $HOME.
#
# Idempotent: every step checks its own postcondition and skips finished
# work. Safe to re-run after any failure, reboot, or pod restart.
#
# Usage:
#   bash bootstrap_gpu.sh                       # detect + full run
#   bash bootstrap_gpu.sh --dry-run             # print the plan, touch nothing
#   bash bootstrap_gpu.sh --provider runpod     # override/simulate detection
#   SKIP_SMOKE=1 bash bootstrap_gpu.sh          # skip the smoke-test section
#
# --provider values: aws | runpod | vast | generic
#   In a real run, facts still rule — the flag only adjusts hints and the
#   expectations we warn about. With --dry-run, the flag SIMULATES that
#   provider's typical facts so the plan can be reviewed from anywhere.
#
# Overridable knobs (env vars):
#   WORK_ROOT       base dir for repos/models/venv (default: /workspace if
#                   present, else $HOME)
#   AGENT_HOME      agent repo dir        (default: $WORK_ROOT/mtg-forge-llm-agent)
#   AGENT_REPO_URL  git URL for the agent repo (REQUIRED if $AGENT_HOME absent)
#   FORGE_HOME      Forge checkout dir    (default: $WORK_ROOT/forge)
#   FORGE_REPO_URL  git URL for Forge     (default: upstream Card-Forge)
#   VLLM_VENV       vLLM virtualenv       (default: $WORK_ROOT/vllm-venv)
#   MODEL_DIR       HF-format student model dir (default: $WORK_ROOT/models/student)
#   VLLM_PORT       serving port          (default: 8080)
#   MAX_MODEL_LEN   vLLM context length   (default: 8192)

set -euo pipefail

# ---------------------------------------------------------------- args ------
DRY_RUN=0
PROVIDER=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --provider)
      [ $# -ge 2 ] || { echo "FATAL: --provider needs a value (aws|runpod|vast|generic)" >&2; exit 2; }
      PROVIDER="$2"; shift ;;
    --provider=*) PROVIDER="${1#--provider=}" ;;
    --aws) PROVIDER="aws" ;;                       # hint used by bootstrap_g5.sh
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "FATAL: unknown flag '$1' (try --help)" >&2; exit 2 ;;
  esac
  shift
done
case "$PROVIDER" in ""|aws|runpod|vast|generic) : ;;
  *) echo "FATAL: --provider must be aws|runpod|vast|generic, got '$PROVIDER'" >&2; exit 2 ;;
esac

# --------------------------------------------------------------- helpers ----
step()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()    { printf '\033[1;32m    OK: %s\033[0m\n' "$*"; }
skip()  { printf '\033[1;33m    SKIP (already done): %s\033[0m\n' "$*"; }
warn()  { printf '\033[1;33m    WARNING: %s\033[0m\n' "$*"; }
die()   { printf '\n\033[1;31mFATAL: %s\033[0m\n' "$*" >&2; exit 1; }

# ------------------------------------------------------------- detection ----
# Facts, not provider names. Each is yes/no.
detect_facts() {
  # NB: no bare `[ cond ] && x=yes` here — under set -e a false condition in
  # a trailing AND-list aborts the whole script.
  FACT_ROOT=no;      if [ "$(id -u)" = "0" ]; then FACT_ROOT=yes; fi
  FACT_SUDO=no;      if command -v sudo >/dev/null 2>&1; then FACT_SUDO=yes; fi
  FACT_GPU=no
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    FACT_GPU=yes
    GPU_NAME="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
  else
    GPU_NAME="(none detected)"
  fi
  FACT_WORKSPACE=no; if [ -d /workspace ]; then FACT_WORKSPACE=yes; fi
  FACT_CONTAINER=no
  if [ -f /.dockerenv ] || grep -qsE 'docker|containerd|kubepods' /proc/1/cgroup; then
    FACT_CONTAINER=yes
  fi
  FACT_DLAMI=no
  if [ -d /opt/aws ] || [ -d /opt/dlami ] || [ -f /etc/dlami/dlami-manifest ]; then
    FACT_DLAMI=yes
  fi
}

# --dry-run --provider X reviews the plan for a box you are NOT on yet:
# substitute that provider's typical facts for the local probe.
simulate_facts() {
  case "$1" in
    aws)     FACT_ROOT=no;  FACT_SUDO=yes; FACT_GPU=yes; FACT_WORKSPACE=no;  FACT_CONTAINER=no;  FACT_DLAMI=yes ;;
    runpod|vast)
             FACT_ROOT=yes; FACT_SUDO=no;  FACT_GPU=yes; FACT_WORKSPACE=yes; FACT_CONTAINER=yes; FACT_DLAMI=no ;;
    generic) FACT_ROOT=no;  FACT_SUDO=yes; FACT_GPU=yes; FACT_WORKSPACE=no;  FACT_CONTAINER=no;  FACT_DLAMI=no ;;
  esac
  GPU_NAME="(simulated: $1)"
}

detect_facts
if [ "$DRY_RUN" = "1" ] && [ -n "$PROVIDER" ]; then
  simulate_facts "$PROVIDER"
fi

# Derived: how to run privileged commands.
SUDO=""
if [ "$FACT_ROOT" = "yes" ]; then
  SUDO=""
elif [ "$FACT_SUDO" = "yes" ]; then
  SUDO="sudo"
else
  [ "$DRY_RUN" = "1" ] || die "neither root nor a sudo binary — cannot install packages. (Container templates run as root; VM images need a sudo-capable user.)"
fi

# ---------------------------------------------------------------- config ----
if [ -z "${WORK_ROOT:-}" ]; then
  if [ "$FACT_WORKSPACE" = "yes" ]; then WORK_ROOT=/workspace; else WORK_ROOT="$HOME"; fi
fi
AGENT_HOME="${AGENT_HOME:-$WORK_ROOT/mtg-forge-llm-agent}"
AGENT_REPO_URL="${AGENT_REPO_URL:-}"
FORGE_HOME="${FORGE_HOME:-$WORK_ROOT/forge}"
FORGE_REPO_URL="${FORGE_REPO_URL:-https://github.com/Card-Forge/forge.git}"
VLLM_VENV="${VLLM_VENV:-$WORK_ROOT/vllm-venv}"
MODEL_DIR="${MODEL_DIR:-$WORK_ROOT/models/student}"
VLLM_PORT="${VLLM_PORT:-8080}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-8192}"
CP_FILE=/tmp/forge_cp.txt
CP_CACHE="$WORK_ROOT/.forge_cp.cache.txt"
SERVE_SCRIPT="$WORK_ROOT/serve_student.sh"
SKIP_SMOKE="${SKIP_SMOKE:-0}"
PKGS=(openjdk-17-jdk maven git curl tmux python3-venv xvfb)

facts_table() {
  cat <<FACTS
  facts ($( [ "$DRY_RUN" = "1" ] && [ -n "$PROVIDER" ] && echo "SIMULATED for --provider $PROVIDER" || echo "probed locally" )):
    root user:          $FACT_ROOT $( [ "$FACT_ROOT" = "no" ] && echo "(will use sudo: $FACT_SUDO)" )
    GPU (nvidia-smi):   $FACT_GPU  $GPU_NAME
    /workspace volume:  $FACT_WORKSPACE
    container:          $FACT_CONTAINER
    AWS DLAMI markers:  $FACT_DLAMI
  resulting layout:
    WORK_ROOT   $WORK_ROOT $( [ "$FACT_WORKSPACE" = "yes" ] && echo "(persistent volume — survives pod stop)" )
    agent repo  $AGENT_HOME
    Forge       $FORGE_HOME  (pinned to \$AGENT_HOME/FORGE_COMMIT, hard fail on drift)
    vLLM venv   $VLLM_VENV  -> serves $MODEL_DIR as "default_model" on :$VLLM_PORT
    classpath   $CP_FILE (cache: $CP_CACHE)
FACTS
}

# --------------------------------------------------------------- dry run ----
if [ "$DRY_RUN" = "1" ]; then
  echo "DRY RUN — plan only, nothing will be executed."
  facts_table
  cat <<PLAN
  plan:
    1. Preflight: Linux, >=40GB free under WORK_ROOT, GPU check.
       NEVER installs NVIDIA drivers: container templates already have them;
       on a bare VM without nvidia-smi only build+canary work (vLLM will not).
    2. apt-get install (only missing): ${PKGS[*]}
       privilege: $( [ "$FACT_ROOT" = "yes" ] && echo "running as root, no sudo needed" || echo "via sudo" )
    3. Agent repo -> $AGENT_HOME
       clone from: ${AGENT_REPO_URL:-"(NOT SET — fails unless dir already exists, e.g. rsync'd)"}
    4. Forge -> $FORGE_HOME @ exact FORGE_COMMIT pin
    5. Overlay spike files (install.sh) + Linux JAVA_HOME launcher patch
    6. Build Forge: mvn offline first, network fallback (loud fail)
    7. Classpath file -> $CP_FILE, cached at $CP_CACHE (restored after reboot/pod restart)
    8. vLLM venv -> $VLLM_VENV; helper $SERVE_SCRIPT (binds 127.0.0.1:$VLLM_PORT)
    9. Smoke tests (SKIP_SMOKE=$SKIP_SMOKE):
       a. seed-42 floor mirror canary — BLOCKING: identity A wins, 108 turns
       b. single LLM game via openrouter:default_model at http://127.0.0.1:$VLLM_PORT/v1
          — only if the server answers AND you exported OPENROUTER_ALLOW_EGRESS=1
          yourself (localhost endpoint; the consent gate is honored regardless).
  Nothing was executed. Re-run without --dry-run to apply.
PLAN
  exit 0
fi

# ------------------------------------------------------------- preflight ----
step "1/9 Preflight"
[ "$(uname -s)" = "Linux" ] || die "this script targets Linux GPU boxes; detected $(uname -s). Run it ON the box, not your laptop."
facts_table
if [ -n "$PROVIDER" ]; then
  case "$PROVIDER" in
    runpod|vast)
      [ "$FACT_WORKSPACE" = "yes" ] || warn "--provider $PROVIDER but no /workspace volume: everything goes to \$HOME and DIES WITH THE POD. Attach a volume unless this is intentional."
      ;;
    aws)
      [ "$FACT_DLAMI" = "yes" ] || warn "--provider aws but no DLAMI markers found — wrong AMI, or markers moved."
      ;;
  esac
fi
mkdir -p "$WORK_ROOT" 2>/dev/null || die "cannot create WORK_ROOT=$WORK_ROOT"
free_gb=$(df -Pk "$WORK_ROOT" | awk 'NR==2 {print int($4/1024/1024)}')
[ "$free_gb" -ge 40 ] || die "only ${free_gb}GB free under $WORK_ROOT — need >=40GB (Forge build + model + vLLM). Grow the volume/disk."
ok "${free_gb}GB free under $WORK_ROOT"
if [ "$FACT_GPU" = "yes" ]; then
  ok "GPU: $GPU_NAME"
elif [ "$FACT_CONTAINER" = "yes" ]; then
  die "container without a working GPU (nvidia-smi failed). This template has no GPU attached — pick a GPU pod/template. NEVER install drivers inside a container."
else
  warn "nvidia-smi missing/failing. Build + canary will work; vLLM will NOT. Install the NVIDIA driver via your distro/cloud image (this script never touches drivers)."
fi

# ------------------------------------------------------------------- apt ----
step "2/9 System packages (${PKGS[*]})"
command -v apt-get >/dev/null 2>&1 || die "apt-get not found — this script supports Debian/Ubuntu userlands only."
need_pkgs=()
for p in "${PKGS[@]}"; do
  dpkg -s "$p" >/dev/null 2>&1 || need_pkgs+=("$p")
done
if [ "${#need_pkgs[@]}" -eq 0 ]; then
  skip "all packages present"
else
  $SUDO apt-get update -y || die "apt-get update failed — check network."
  $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y "${need_pkgs[@]}" \
    || die "apt-get install failed for: ${need_pkgs[*]}"
  ok "installed: ${need_pkgs[*]}"
fi
# Make Java 17 this shell's JDK even if the image defaults to another version.
if ! java -version 2>&1 | grep -q 'version "17'; then
  JDK17_DIR=""
  for d in /usr/lib/jvm/java-17-openjdk-amd64 /usr/lib/jvm/java-17-openjdk-arm64 /usr/lib/jvm/*17*; do
    if [ -x "$d/bin/java" ]; then JDK17_DIR="$d"; break; fi
  done
  [ -n "$JDK17_DIR" ] || die "no Java 17 JDK found after install (java -version: $(java -version 2>&1 | head -1)). Fix: $SUDO update-alternatives --config java"
  export JAVA_HOME="$JDK17_DIR"
  export PATH="$JAVA_HOME/bin:$PATH"
fi
java -version 2>&1 | grep -q 'version "17' || die "java 17 still not active in this shell."
JDK_HOME="${JAVA_HOME:-$(dirname "$(dirname "$(readlink -f "$(command -v javac)")")")}"
ok "java 17 active (JDK at $JDK_HOME)"

# ---------------------------------------------------------- agent repo ------
step "3/9 mtg-forge-llm-agent -> $AGENT_HOME"
if [ -f "$AGENT_HOME/install.sh" ] && [ -f "$AGENT_HOME/FORGE_COMMIT" ]; then
  skip "repo present at $AGENT_HOME"
elif [ -n "$AGENT_REPO_URL" ]; then
  git clone "$AGENT_REPO_URL" "$AGENT_HOME" || die "clone of $AGENT_REPO_URL failed (private repo? deploy key / HTTPS token, or rsync the repo to $AGENT_HOME instead)."
  ok "cloned"
else
  die "no repo at $AGENT_HOME and AGENT_REPO_URL is not set.
  Either:  AGENT_REPO_URL=git@github.com:YOU/mtg-forge-llm-agent.git bash $0
  Or rsync from your laptop (see cloud/README.md for the provider-specific SSH forms):
    rsync -av --exclude runs --exclude models --exclude .venv-mlx --exclude '.claude' \\
      ~/code/mtg-forge-llm-agent/ <SSH_TARGET>:$AGENT_HOME/"
fi
PINNED_COMMIT=$(tr -d '[:space:]' < "$AGENT_HOME/FORGE_COMMIT")
[ -n "$PINNED_COMMIT" ] || die "$AGENT_HOME/FORGE_COMMIT is empty — cannot pin the engine version."
ok "engine pin: $PINNED_COMMIT"

# --------------------------------------------------------------- forge ------
step "4/9 Forge -> $FORGE_HOME @ $PINNED_COMMIT (exactly)"
if [ ! -d "$FORGE_HOME/.git" ]; then
  # full clone (not --depth 1): we must be able to check out the pinned commit
  git clone "$FORGE_REPO_URL" "$FORGE_HOME" || die "clone of $FORGE_REPO_URL failed."
  ok "cloned"
fi
CUR=$(git -C "$FORGE_HOME" rev-parse HEAD 2>/dev/null || echo unknown)
if [ "$CUR" != "$PINNED_COMMIT" ]; then
  git -C "$FORGE_HOME" cat-file -e "$PINNED_COMMIT^{commit}" 2>/dev/null \
    || git -C "$FORGE_HOME" fetch origin "$PINNED_COMMIT" \
    || git -C "$FORGE_HOME" fetch origin \
    || die "could not fetch pinned commit $PINNED_COMMIT from $FORGE_REPO_URL"
  git -C "$FORGE_HOME" checkout --force "$PINNED_COMMIT" || die "checkout of pinned commit $PINNED_COMMIT failed."
fi
CUR=$(git -C "$FORGE_HOME" rev-parse HEAD)
[ "$CUR" = "$PINNED_COMMIT" ] || die "Forge HEAD is $CUR but pin is $PINNED_COMMIT — eval results would not be comparable. Refusing to continue."
ok "Forge at pinned commit"

# -------------------------------------------------------------- overlay -----
step "5/9 Overlay spike files (install.sh) + Linux launcher patch"
( cd "$AGENT_HOME" && FORGE_HOME="$FORGE_HOME" bash install.sh ) || die "install.sh failed — see output above."
# The launcher hardcodes a macOS Homebrew JAVA_HOME. install.sh re-copies it
# every run, so re-apply this patch every time (idempotent by construction).
for launcher in run-llm-headless.sh run-llm-spike.sh; do
  f="$FORGE_HOME/$launcher"
  [ -f "$f" ] || continue
  if grep -q '/opt/homebrew/opt/openjdk@17' "$f"; then
    sed -i "s|^export JAVA_HOME=/opt/homebrew/opt/openjdk@17.*\$|export JAVA_HOME=\"\${JAVA_HOME:-$JDK_HOME}\"|" "$f" \
      || die "failed to patch JAVA_HOME in $f"
  fi
  if grep -q '/opt/homebrew' "$f"; then
    die "$f still references /opt/homebrew after patching — launcher format changed upstream; patch it by hand."
  fi
done
ok "overlay installed, launchers patched for Linux (JDK: $JDK_HOME)"

# ---------------------------------------------------------------- build -----
step "6/9 Build Forge (first run takes several minutes)"
if [ -d "$FORGE_HOME/forge-gui-desktop/target/classes/forge/spike" ] \
   && [ "$FORGE_HOME/forge-gui-desktop/target/classes/forge/spike" -nt "$FORGE_HOME/forge-gui-desktop/src/main/java/forge/spike/LlmSpikeHeadless.java" ]; then
  skip "spike classes newer than sources"
else
  # offline first (re-runs with a warm ~/.m2 need no network), then online
  ( cd "$FORGE_HOME" && mvn -q -o -pl forge-gui-desktop -am -DskipTests compile ) \
    || ( cd "$FORGE_HOME" && mvn -q -pl forge-gui-desktop -am -DskipTests compile ) \
    || die "Maven build failed (offline AND online). Common causes: java != 17 (java -version), no network for first dependency download. Full log above."
  [ -d "$FORGE_HOME/forge-gui-desktop/target/classes/forge/spike" ] \
    || die "build 'succeeded' but forge/spike classes are missing — overlay not compiled. Did install.sh run against the right FORGE_HOME?"
  ok "built"
fi

# ------------------------------------------------------------- classpath ----
step "7/9 Classpath file $CP_FILE (launcher hard-requires it; /tmp dies on reboot/restart)"
# only trust the cache if it actually looks like a classpath (guards against
# a truncated copy from an interrupted earlier run)
if [ ! -s "$CP_FILE" ] && [ -s "$CP_CACHE" ] && grep -q '\.jar' "$CP_CACHE"; then
  cp "$CP_CACHE" "$CP_FILE"
  ok "restored $CP_FILE from cache (post-reboot/pod-restart path)"
fi
if [ ! -s "$CP_FILE" ]; then
  ( cd "$FORGE_HOME" && mvn -q -pl forge-gui-desktop -am dependency:build-classpath -Dmdep.outputFile="$CP_FILE" ) \
    || die "dependency:build-classpath failed."
fi
[ -s "$CP_FILE" ] || die "$CP_FILE is missing or EMPTY after generation. The launcher will exit 1 on every game (this exact failure once marched through 32 cells reporting success). Do not proceed."
cp "$CP_FILE" "$CP_CACHE.tmp" && mv "$CP_CACHE.tmp" "$CP_CACHE"   # atomic: never a truncated cache
ok "$CP_FILE present ($(wc -c < "$CP_FILE") bytes), cached to $CP_CACHE"

# ----------------------------------------------------------------- vllm -----
step "8/9 vLLM venv + serve_student.sh"
if [ -x "$VLLM_VENV/bin/vllm" ]; then
  skip "vLLM already installed at $VLLM_VENV"
elif [ "$FACT_GPU" != "yes" ]; then
  warn "skipping vLLM install: no working GPU on this box (see step 1)."
else
  python3 -m venv "$VLLM_VENV" || die "venv creation failed (python3-venv installed?)"
  "$VLLM_VENV/bin/pip" install --upgrade pip >/dev/null || die "pip upgrade failed"
  "$VLLM_VENV/bin/pip" install vllm || die "pip install vllm failed — check GPU driver/CUDA (nvidia-smi) and network."
  ok "vLLM $("$VLLM_VENV/bin/vllm" --version 2>/dev/null || echo installed)"
fi
cat > "$SERVE_SCRIPT.tmp" <<EOF
#!/usr/bin/env bash
# Serve the student model with an OpenAI-compatible endpoint on port $VLLM_PORT.
# The harness talks to it via:
#   OPENROUTER_BASE_URL=http://127.0.0.1:$VLLM_PORT/v1
#   SMART_MODEL=openrouter:default_model  FAST_MODEL=openrouter:default_model
# Run inside tmux so it survives SSH disconnects:  tmux new -s vllm 'bash $SERVE_SCRIPT'
set -euo pipefail
MODEL_DIR="\${MODEL_DIR:-$MODEL_DIR}"
[ -d "\$MODEL_DIR" ] || { echo "FATAL: model dir \$MODEL_DIR not found. Upload it first (see cloud/README.md)." >&2; exit 1; }
exec "$VLLM_VENV/bin/vllm" serve "\$MODEL_DIR" \\
  --served-model-name default_model \\
  --port $VLLM_PORT --host 127.0.0.1 \\
  --max-model-len $MAX_MODEL_LEN \\
  --gpu-memory-utilization 0.90
EOF
mv "$SERVE_SCRIPT.tmp" "$SERVE_SCRIPT"   # atomic: never a half-written executable
chmod +x "$SERVE_SCRIPT"
if [ -d "$MODEL_DIR" ]; then
  ok "$SERVE_SCRIPT written; model found at $MODEL_DIR"
else
  warn "model dir $MODEL_DIR not present yet — upload it, then: tmux new -s vllm 'bash $SERVE_SCRIPT'"
fi

# ---------------------------------------------------------------- smoke -----
step "9/9 Smoke tests"
if [ "$SKIP_SMOKE" = "1" ]; then
  skip "SKIP_SMOKE=1"
else
  SMOKE_DIR=$(mktemp -d /tmp/smoke.XXXXXX)

  run_headless() {  # run_headless <eval_out_subdir> <extra env...>
    local out="$SMOKE_DIR/$1"; shift
    mkdir -p "$out"
    ( cd "$FORGE_HOME" && env EVAL_OUT="$out" "$@" bash run-llm-headless.sh ) \
      > "$out/console.log" 2>&1 \
      || ( cd "$FORGE_HOME" && env EVAL_OUT="$out" "$@" xvfb-run -a bash run-llm-headless.sh ) \
      >> "$out/console.log" 2>&1 \
      || { echo "    console tail:"; tail -30 "$out/console.log"; return 1; }
  }

  check_game_row() {  # check_game_row <eval_out_subdir> <expect: canary|any>
    python3 - "$SMOKE_DIR/$1" "$2" <<'PY'
import glob, json, sys
out, mode = sys.argv[1], sys.argv[2]
rows = []
for f in glob.glob(out + "/*.jsonl"):
    for line in open(f):
        line = line.strip()
        if not line: continue
        try: r = json.loads(line)
        except ValueError: continue
        if r.get("t") == "game": rows.append(r)
if not rows:
    sys.exit("FAIL: no game-summary row found in %s/*.jsonl — the game did not finish." % out)
g = rows[-1]
print("    game row: winner=%s turns=%s win_condition=%s" % (g.get("winner"), g.get("turns"), g.get("win_condition")))
if mode == "canary":
    if g.get("winner") != "A" or g.get("turns") != 108:
        sys.exit("FAIL: seed-42 floor mirror canary expects identity A winning in 108 turns, got winner=%s turns=%s.\n"
                 "The engine/harness on this box does NOT match the validated baseline — results are not comparable. STOP."
                 % (g.get("winner"), g.get("turns")))
print("    PASS")
PY
  }

  echo "--- 9a. seed-42 floor mirror canary (expect: A wins, 108 turns; ~30s) ---"
  run_headless canary GAME_SEED=42 CONTROLLER_A=floor CONTROLLER_B=floor \
    || die "canary game failed to run — see $SMOKE_DIR/canary/console.log"
  check_game_row canary canary || die "canary check failed"

  echo "--- 9b. single-game LLM smoke test (llm vs floor, student model) ---"
  if [ "${OPENROUTER_ALLOW_EGRESS:-}" != "1" ]; then
    echo "    SKIPPED: OPENROUTER_ALLOW_EGRESS is not set to 1."
    echo "    The harness's consent gate applies to every openrouter: spec, even a"
    echo "    localhost vLLM endpoint (no data leaves this machine in that case)."
    echo "    To run it, decide for yourself and re-run just this test:"
    echo "      export OPENROUTER_ALLOW_EGRESS=1 OPENROUTER_API_KEY=local-only"
    echo "      export OPENROUTER_BASE_URL=http://127.0.0.1:$VLLM_PORT/v1"
    echo "      SKIP_SMOKE=0 bash $0"
  elif ! curl -sf -m 5 "http://127.0.0.1:$VLLM_PORT/v1/models" >/dev/null; then
    echo "    SKIPPED: no model server answering on 127.0.0.1:$VLLM_PORT."
    echo "    Start it first:  tmux new -s vllm 'bash $SERVE_SCRIPT'"
  else
    run_headless llm GAME_SEED=42 CONTROLLER_A=llm CONTROLLER_B=floor \
      SMART_MODEL=openrouter:default_model FAST_MODEL=openrouter:default_model \
      OPENROUTER_BASE_URL="http://127.0.0.1:$VLLM_PORT/v1" \
      OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-local-only}" \
      OPENROUTER_ALLOW_EGRESS=1 \
      GAME_CAP_MS=1200000 \
      || die "LLM smoke game failed — see $SMOKE_DIR/llm/console.log (is vLLM healthy? nvidia-smi?)"
    check_game_row llm any || die "LLM smoke game produced no game-summary row"
    # the game must actually have exercised the model, not fallbacks:
    python3 - "$SMOKE_DIR/llm" <<'PY'
import glob, json, sys
model = 0
for f in glob.glob(sys.argv[1] + "/*.jsonl"):
    for line in open(f):
        try: r = json.loads(line)
        except ValueError: continue
        if r.get("t") == "decision" and r.get("tier") in ("smart", "fast") and (r.get("completion_tokens") or 0) > 0:
            model += 1
if model == 0:
    sys.exit("FAIL: 0 decisions carried model tokens — the LLM was never actually consulted (server misconfigured?).")
print("    PASS: %d model-backed decisions" % model)
PY
  fi
  echo "smoke artifacts kept in $SMOKE_DIR"
fi

printf '\n\033[1;32mBOOTSTRAP COMPLETE.\033[0m\n'
cat <<DONE
Next steps:
  1. upload the student model (if not done):    see cloud/README.md
  2. start the server:                          tmux new -s vllm 'bash $SERVE_SCRIPT'
  3. start a run:                               see cloud/README.md
  4. rsync results back, then STOP THE POD / INSTANCE (billing!)
DONE
