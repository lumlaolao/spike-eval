#!/usr/bin/env bash
# bootstrap_g5.sh — one-command setup for a fresh AWS g5.xlarge / g5.2xlarge
# running the "Deep Learning OSS Nvidia Driver AMI (Ubuntu 22.04)".
#
# What it does (in order):
#   1. preflight checks (OS, disk, GPU driver)
#   2. apt: OpenJDK 17, Maven, git, python3-venv, xvfb (fallback for AWT init)
#   3. clone mtg-forge-llm-agent (or use an rsync'd copy)
#   4. clone Forge and check out EXACTLY the commit pinned in FORGE_COMMIT
#   5. overlay the spike files (install.sh) + patch the launcher's macOS JAVA_HOME
#   6. build Forge (forge-gui-desktop + deps)
#   7. generate /tmp/forge_cp.txt (the launcher does
#      CP="target/classes:$(cat /tmp/forge_cp.txt)" and exits 1 without it;
#      /tmp is wiped on reboot, so we also keep a cached copy in $HOME)
#   8. create a vLLM venv + a serve_student.sh helper (OpenAI-compatible
#      endpoint on port 8080, served model name "default_model")
#   9. smoke tests: seed-42 floor mirror canary (expect: identity A wins,
#      108 turns) and, if a model is being served AND the user has opted in
#      to the egress gate, a single-game LLM smoke test.
#
# Idempotent: every step checks its own postcondition and skips work already
# done. Safe to re-run after any failure or after a reboot.
#
# Usage:
#   bash bootstrap_g5.sh              # full run
#   bash bootstrap_g5.sh --dry-run    # print the plan, touch nothing
#   SKIP_SMOKE=1 bash bootstrap_g5.sh # skip the smoke-test section
#
# Overridable knobs (env vars):
#   AGENT_HOME      where mtg-forge-llm-agent lives     (default: $HOME/mtg-forge-llm-agent)
#   AGENT_REPO_URL  git URL for the agent repo          (REQUIRED if $AGENT_HOME absent)
#   FORGE_HOME      where the Forge checkout lives      (default: $HOME/forge)
#   FORGE_REPO_URL  git URL for Forge                   (default: upstream Card-Forge)
#   VLLM_VENV       vLLM virtualenv path                (default: $HOME/vllm-venv)
#   MODEL_DIR       HF-format student model directory   (default: $HOME/models/student)
#   VLLM_PORT       serving port                        (default: 8080)
#   MAX_MODEL_LEN   vLLM context length                 (default: 8192)

set -euo pipefail

# ---------------------------------------------------------------- config ----
AGENT_HOME="${AGENT_HOME:-$HOME/mtg-forge-llm-agent}"
AGENT_REPO_URL="${AGENT_REPO_URL:-}"                 # e.g. git@github.com:YOU/mtg-forge-llm-agent.git
FORGE_HOME="${FORGE_HOME:-$HOME/forge}"
FORGE_REPO_URL="${FORGE_REPO_URL:-https://github.com/Card-Forge/forge.git}"
VLLM_VENV="${VLLM_VENV:-$HOME/vllm-venv}"
MODEL_DIR="${MODEL_DIR:-$HOME/models/student}"
VLLM_PORT="${VLLM_PORT:-8080}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-8192}"
CP_FILE=/tmp/forge_cp.txt
CP_CACHE="$HOME/.forge_cp.cache.txt"
SKIP_SMOKE="${SKIP_SMOKE:-0}"

# --------------------------------------------------------------- helpers ----
step()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()    { printf '\033[1;32m    OK: %s\033[0m\n' "$*"; }
skip()  { printf '\033[1;33m    SKIP (already done): %s\033[0m\n' "$*"; }
die()   { printf '\n\033[1;31mFATAL: %s\033[0m\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------- dry run ----
if [ "${1:-}" = "--dry-run" ]; then
  cat <<PLAN
DRY RUN — plan only, nothing will be executed.

  1. Preflight: verify Ubuntu 22.04, >=40GB free disk, nvidia-smi works.
  2. apt-get install: openjdk-17-jdk maven git curl python3-venv xvfb
  3. Agent repo   -> $AGENT_HOME
       clone from: ${AGENT_REPO_URL:-"(NOT SET — will fail unless dir already exists, e.g. rsync'd from your laptop)"}
  4. Forge repo   -> $FORGE_HOME
       clone from: $FORGE_REPO_URL
       checkout EXACTLY the commit in $AGENT_HOME/FORGE_COMMIT (hard fail on mismatch)
  5. Overlay spike files: FORGE_HOME=$FORGE_HOME bash $AGENT_HOME/install.sh
       then patch run-llm-headless.sh's hardcoded macOS JAVA_HOME for Linux
  6. Build Forge: mvn -pl forge-gui-desktop -am -DskipTests compile  (NOT offline; fresh box)
  7. Classpath: mvn dependency:build-classpath -Dmdep.outputFile=$CP_FILE
       (+ cached copy at $CP_CACHE; restored automatically after reboot)
  8. vLLM: venv at $VLLM_VENV, helper script ~/serve_student.sh serving
       $MODEL_DIR as "default_model" on port $VLLM_PORT (OpenAI-compatible /v1)
  9. Smoke tests (SKIP_SMOKE=$SKIP_SMOKE):
       a. seed-42 floor mirror canary — EXPECT identity A wins in 108 turns
       b. single LLM game vs floor via openrouter:default_model at
          http://127.0.0.1:$VLLM_PORT/v1 — ONLY if the server is up AND you
          have exported OPENROUTER_ALLOW_EGRESS=1 yourself (localhost-only
          endpoint, but the consent gate is honored regardless).

Nothing was executed. Re-run without --dry-run to apply.
PLAN
  exit 0
fi

# ------------------------------------------------------------- preflight ----
step "1/9 Preflight"
[ "$(uname -s)" = "Linux" ] || die "this script targets Ubuntu 22.04 on EC2; detected $(uname -s). Run it ON the instance, not your laptop."
if [ -r /etc/os-release ]; then
  . /etc/os-release
  case "${VERSION_ID:-}" in
    22.04) ok "Ubuntu 22.04" ;;
    *) echo "    WARNING: expected Ubuntu 22.04, found ${PRETTY_NAME:-unknown}. Continuing, but apt package names may differ." ;;
  esac
fi
free_gb=$(df -Pk "$HOME" | awk 'NR==2 {print int($4/1024/1024)}')
[ "$free_gb" -ge 40 ] || die "only ${free_gb}GB free in \$HOME — need >=40GB (Forge build + model + vLLM). Grow the EBS volume."
ok "${free_gb}GB free disk"
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  ok "GPU driver: $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
else
  echo "    WARNING: nvidia-smi missing or failing. Forge build + canary will still work; vLLM will NOT. Wrong AMI?"
fi

# ------------------------------------------------------------------- apt ----
step "2/9 System packages (OpenJDK 17, Maven, git, python3-venv, xvfb)"
need_pkgs=()
for p in openjdk-17-jdk maven git curl python3-venv xvfb; do
  dpkg -s "$p" >/dev/null 2>&1 || need_pkgs+=("$p")
done
if [ "${#need_pkgs[@]}" -eq 0 ]; then
  skip "all packages present"
else
  sudo apt-get update -y || die "apt-get update failed — check network / IAM / NAT."
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "${need_pkgs[@]}" \
    || die "apt-get install failed for: ${need_pkgs[*]}"
  ok "installed: ${need_pkgs[*]}"
fi
java -version 2>&1 | grep -q 'version "17' || die "java 17 not the default java. Fix with: sudo update-alternatives --config java"
ok "java 17 active"

# ---------------------------------------------------------- agent repo ------
step "3/9 mtg-forge-llm-agent -> $AGENT_HOME"
if [ -f "$AGENT_HOME/install.sh" ] && [ -f "$AGENT_HOME/FORGE_COMMIT" ]; then
  skip "repo present at $AGENT_HOME"
elif [ -n "$AGENT_REPO_URL" ]; then
  git clone "$AGENT_REPO_URL" "$AGENT_HOME" || die "clone of $AGENT_REPO_URL failed (private repo? set up an SSH key or use HTTPS token, or rsync the repo from your laptop to $AGENT_HOME instead)."
  ok "cloned"
else
  die "no repo at $AGENT_HOME and AGENT_REPO_URL is not set.
  Either:  AGENT_REPO_URL=git@github.com:YOU/mtg-forge-llm-agent.git bash $0
  Or rsync from your laptop:
    rsync -av --exclude runs --exclude models ~/code/mtg-forge-llm-agent/ ubuntu@<INSTANCE_IP>:mtg-forge-llm-agent/"
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
# The launcher hardcodes a macOS Homebrew JAVA_HOME. install.sh re-copies it on
# every run, so re-apply this patch every time (idempotent by construction).
for launcher in run-llm-headless.sh run-llm-spike.sh; do
  f="$FORGE_HOME/$launcher"
  [ -f "$f" ] || continue
  if grep -q '/opt/homebrew/opt/openjdk@17' "$f"; then
    sed -i 's|^export JAVA_HOME=/opt/homebrew/opt/openjdk@17.*$|export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-17-openjdk-amd64}"|' "$f" \
      || die "failed to patch JAVA_HOME in $f"
  fi
  grep -q '/opt/homebrew' "$f" && die "$f still references /opt/homebrew after patching — launcher format changed upstream; patch it by hand."
done
ok "overlay installed, launchers patched for Linux"

# ---------------------------------------------------------------- build -----
step "6/9 Build Forge (this takes several minutes on first run)"
if [ -d "$FORGE_HOME/forge-gui-desktop/target/classes/forge/spike" ] \
   && [ "$FORGE_HOME/forge-gui-desktop/target/classes/forge/spike" -nt "$FORGE_HOME/forge-gui-desktop/src/main/java/forge/spike/LlmSpikeHeadless.java" ]; then
  skip "spike classes newer than sources"
else
  ( cd "$FORGE_HOME" && mvn -q -pl forge-gui-desktop -am -DskipTests compile ) \
    || die "Maven build failed. Common causes: java != 17 (java -version), no network for first dependency download. Full log above."
  [ -d "$FORGE_HOME/forge-gui-desktop/target/classes/forge/spike" ] \
    || die "build 'succeeded' but forge/spike classes are missing — overlay not compiled. Did install.sh run against the right FORGE_HOME?"
  ok "built"
fi

# ------------------------------------------------------------- classpath ----
step "7/9 Classpath file $CP_FILE (launcher hard-requires it; /tmp dies on reboot)"
# only trust the cache if it actually looks like a classpath (guards against
# a truncated copy from an interrupted earlier run)
if [ ! -s "$CP_FILE" ] && [ -s "$CP_CACHE" ] && grep -q '\.jar' "$CP_CACHE"; then
  cp "$CP_CACHE" "$CP_FILE"
  ok "restored $CP_FILE from cache (post-reboot path)"
fi
if [ ! -s "$CP_FILE" ]; then
  ( cd "$FORGE_HOME" && mvn -q -pl forge-gui-desktop -am dependency:build-classpath -Dmdep.outputFile="$CP_FILE" ) \
    || die "dependency:build-classpath failed."
fi
[ -s "$CP_FILE" ] || die "$CP_FILE is missing or EMPTY after generation. The launcher will exit 1 on every game (this exact failure once marched through 32 cells reporting success). Do not proceed."
cp "$CP_FILE" "$CP_CACHE"
ok "$CP_FILE present ($(wc -c < "$CP_FILE") bytes), cached to $CP_CACHE"

# ----------------------------------------------------------------- vllm -----
step "8/9 vLLM venv + serve_student.sh"
if [ -x "$VLLM_VENV/bin/vllm" ]; then
  skip "vLLM already installed at $VLLM_VENV"
else
  python3 -m venv "$VLLM_VENV" || die "venv creation failed (python3-venv installed?)"
  "$VLLM_VENV/bin/pip" install --upgrade pip >/dev/null || die "pip upgrade failed"
  "$VLLM_VENV/bin/pip" install vllm || die "pip install vllm failed — check GPU driver / CUDA (nvidia-smi) and network."
  ok "vLLM $("$VLLM_VENV/bin/vllm" --version 2>/dev/null || echo installed)"
fi
cat > "$HOME/serve_student.sh" <<EOF
#!/usr/bin/env bash
# Serve the student model with an OpenAI-compatible endpoint on port $VLLM_PORT.
# The harness talks to it via:
#   OPENROUTER_BASE_URL=http://127.0.0.1:$VLLM_PORT/v1
#   SMART_MODEL=openrouter:default_model  FAST_MODEL=openrouter:default_model
# Run inside tmux/screen so it survives SSH disconnects:  tmux new -s vllm 'bash ~/serve_student.sh'
set -euo pipefail
MODEL_DIR="\${MODEL_DIR:-$MODEL_DIR}"
[ -d "\$MODEL_DIR" ] || { echo "FATAL: model dir \$MODEL_DIR not found. Upload it first:" >&2
  echo "  rsync -av <local-model-dir>/ ubuntu@<INSTANCE_IP>:${MODEL_DIR#"$HOME"/}/" >&2; exit 1; }
exec "$VLLM_VENV/bin/vllm" serve "\$MODEL_DIR" \\
  --served-model-name default_model \\
  --port $VLLM_PORT --host 127.0.0.1 \\
  --max-model-len $MAX_MODEL_LEN \\
  --gpu-memory-utilization 0.90
EOF
chmod +x "$HOME/serve_student.sh"
if [ -d "$MODEL_DIR" ]; then
  ok "serve_student.sh written; model found at $MODEL_DIR"
else
  echo "    NOTE: model dir $MODEL_DIR not present yet — upload it, then: tmux new -s vllm 'bash ~/serve_student.sh'"
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
    echo "    Start it first:  tmux new -s vllm 'bash ~/serve_student.sh'"
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
  1. upload the student model (if not done):    see cloud/README.md step 4
  2. start the server:                          tmux new -s vllm 'bash ~/serve_student.sh'
  3. start a run:                               see cloud/README.md step 6
  4. rsync results back, then STOP THE INSTANCE (cloud/README.md steps 7-8)
DONE
