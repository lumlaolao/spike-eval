#!/bin/bash
# D4 shadow-labeler validation gates. Run BOTH before trusting any SHADOW_AI change.
#
#   bash eval/shadow_canary.sh            # both gates
#   bash eval/shadow_canary.sh noop       # gate 2 only (needs a clean git tree for the spike file)
#   bash eval/shadow_canary.sh onoff      # gate 1 only
#
# GATE 1 — on/off: SHADOW_AI=1 must not perturb the game (identical winner|turns|win_condition and
#   identical decision-row counts), and must produce labels ONLY when set.
# GATE 2 — no-op: with SHADOW_AI unset, this build must behave EXACTLY like the committed pre-D4 build.
#
# WHY GATE 2 EXISTS. Gate 1 alone is not sufficient and this is not hypothetical: an SVar
# snapshot/restore placed before the `if (!SHADOW) return null` guard mutated SpellAbilities in EVERY
# config, and gate 1 stayed green because both arms were equally broken. A shadow-vs-noshadow A/B is
# structurally blind to any defect that fires on both arms; only a comparison against the committed
# baseline sees it.
#
# WHY THE RANDOM ARM, NEVER FLOOR. floor short-circuits every play at `play(no-budget)->PASS` before
# the play menu is built, so a floor A/B exercises ONLY shadowMulligan — which is pure reads and cannot
# perturb anything. It is the one path that proves nothing. `smartOk()` is false only for mode=="floor",
# so the random arm has budget, reaches shadowPlay/shadowAttackers/shadowBlockers, and is still
# zero-model, deterministic and hermetic.
#
# SCRIPTING RULES (a broken gate reports PASS, which is worse than no gate):
#   * absolute paths — the build helper changes directory and relative paths then resolve wrong
#   * no `declare -A` — macOS ships bash 3.2
#   * never `grep -c X f || echo 0` — grep -c prints "0" AND exits 1, so that emits "0\n0"
#   * ASSERT the intended build is actually installed (SHADOW ref count) and abort if not
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
FORGE="${FORGE_HOME:-$HOME/code/forge}"
SPIKE_REL=forge-overlay/forge-gui-desktop/src/main/java/forge/spike/LlmSpikeGui.java
SPIKE="$REPO/$SPIKE_REL"
INSTALLED="$FORGE/forge-gui-desktop/src/main/java/forge/spike/LlmSpikeGui.java"
WORK="${TMPDIR:-/tmp}/shadow-canary-$$"
SEEDS="${SEEDS:-42 7}"
MODE="${1:-both}"
: "${JAVA_HOME:=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home}"
export JAVA_HOME PATH="$JAVA_HOME/bin:$PATH"

mkdir -p "$WORK"
if pgrep -x java >/dev/null 2>&1; then
  echo "ABORT: game JVMs are live — never rebuild while games run (shared target/classes)"; exit 1
fi

refs() { # $1 = file; count SHADOW mentions without the `|| echo 0` trap
  local n; [ -f "$1" ] || { echo 0; return; }
  n=$(grep -c SHADOW "$1" 2>/dev/null); echo "${n:-0}"
}
build() {
  ( cd "$REPO" && bash install.sh >/dev/null 2>&1 ) || return 1
  ( cd "$FORGE" && mvn -q -pl forge-gui-desktop -am -o -DskipTests -Dcheckstyle.skip=true compile >/dev/null 2>&1 )
}
play() { # $1 = tag, $2 = seed, $3 = shadow(0|1) -> "winner|turns|cond ROWS=n LABELS=n"
  local out="$WORK/$1"; rm -rf "$out"; mkdir -p "$out"
  if [ "$3" = "1" ]; then
    ( cd "$FORGE" && env GAME_SEED=$2 SHADOW_AI=1 CONTROLLER_A=random CONTROLLER_B=random \
        EVAL_OUT="$out" bash run-llm-headless.sh >"$out/game.log" 2>&1 )
  else
    ( cd "$FORGE" && env -u SHADOW_AI GAME_SEED=$2 CONTROLLER_A=random CONTROLLER_B=random \
        EVAL_OUT="$out" bash run-llm-headless.sh >"$out/game.log" 2>&1 )
  fi
  python3 - "$out" <<'PY'
import json, sys, glob
res=None; rows=0; lab=0
for p in glob.glob(sys.argv[1]+'/*.jsonl'):
    for line in open(p, errors='replace'):
        try: o=json.loads(line)
        except Exception: continue
        if not isinstance(o, dict): continue
        if o.get('t')=='game': res='%s|%s|%s'%(o.get('winner'),o.get('turns'),o.get('win_condition'))
        elif o.get('t')=='decision':
            rows+=1
            if o.get('shadow_choice') is not None: lab+=1
print('%s ROWS=%d LABELS=%d' % (res, rows, lab))
PY
}

FAIL=0

if [ "$MODE" = "both" ] || [ "$MODE" = "onoff" ]; then
  echo "=== GATE 1: shadow on/off (random arm)"
  build || { echo "ABORT: build failed"; exit 1; }
  [ "$(refs "$INSTALLED")" -ge 1 ] 2>/dev/null || { echo "ABORT: installed build has no shadow code"; exit 1; }
  for s in $SEEDS; do
    off=$(play "onoff-off-$s" $s 0); on=$(play "onoff-on-$s" $s 1)
    echo "  seed $s off: $off"
    echo "  seed $s on : $on"
    off_res=${off%% ROWS=*}; on_res=${on%% ROWS=*}
    off_rows=$(printf '%s' "$off" | sed 's/.*ROWS=\([0-9]*\).*/\1/')
    on_rows=$(printf '%s' "$on"  | sed 's/.*ROWS=\([0-9]*\).*/\1/')
    off_lab=$(printf '%s' "$off" | sed 's/.*LABELS=\([0-9]*\).*/\1/')
    on_lab=$(printf '%s' "$on"   | sed 's/.*LABELS=\([0-9]*\).*/\1/')
    if [ "$off_res" = "$on_res" ] && [ "$off_rows" = "$on_rows" ] && [ "$off_lab" = "0" ] && [ "$on_lab" -gt 0 ]; then
      echo "  >>> seed $s GATE 1: PASS"
    else
      echo "  >>> seed $s GATE 1: *** FAIL *** (result/rows must match; off must label 0; on must label >0)"
      FAIL=1
    fi
  done
fi

if [ "$MODE" = "both" ] || [ "$MODE" = "noop" ]; then
  echo "=== GATE 2: no-op vs committed baseline (random arm, SHADOW_AI unset)"
  BACKUP="$WORK/spike.current.java"
  cp "$SPIKE" "$BACKUP" || exit 1
  # Restores the working spike file, THEN removes the workdir — never the other way round: the
  # backup lives in the workdir, so cleaning up first would leave the tree checked out at HEAD
  # (silently discarding uncommitted work) if the script aborted mid-gate.
  restore() {
    if [ -f "$BACKUP" ]; then
      cp "$BACKUP" "$SPIKE" && build
      echo "  restored overlay refs=$(refs "$SPIKE") installed=$(refs "$INSTALLED")"
    fi
    rm -rf "$WORK" 2>/dev/null
  }
  trap restore EXIT
  ( cd "$REPO" && git checkout HEAD -- "$SPIKE_REL" ) || { echo "ABORT: git checkout failed"; exit 1; }
  # Does the working tree actually differ from HEAD? If not, gate 2 is vacuous (HEAD vs HEAD)
  # and the swap discriminator below has nothing to discriminate.
  if cmp -s "$BACKUP" "$SPIKE"; then DIFF_EXPECTED=0; else DIFF_EXPECTED=1; fi
  build || { echo "ABORT: baseline build failed"; exit 1; }
  BASE_REFS=$(refs "$INSTALLED")
  BASE_SHA=$(shasum -a 256 "$INSTALLED" | cut -d' ' -f1)
  echo "  baseline installed refs=$BASE_REFS sha=${BASE_SHA:0:12}"
  BASE=""
  for s in $SEEDS; do r=$(play "noop-base-$s" $s 0); echo "  seed $s base: $r"; BASE="$BASE$s	$r
"; done
  cp "$BACKUP" "$SPIKE" || { echo "ABORT: could not restore working file"; exit 1; }
  build || { echo "ABORT: working build failed"; exit 1; }
  CUR_REFS=$(refs "$INSTALLED")
  CUR_SHA=$(shasum -a 256 "$INSTALLED" | cut -d' ' -f1)
  echo "  working installed refs=$CUR_REFS sha=${CUR_SHA:0:12}"
  # The assertion that turns "I ran an A/B" into "I ran an A/B against the builds I meant to".
  # An earlier revision of this gate silently failed to swap builds and reported PASS while
  # comparing the baseline against itself. Discriminator history: SHADOW ref count (0 vs 16)
  # worked only while the shadow feature was uncommitted — once committed, BOTH builds carry
  # 16 refs and the count can't distinguish them (2026-07-26: exactly this fired a spurious
  # FAIL against an unrelated snapshot change). Now: compare installed-file checksums, which
  # differ whenever the working tree differs from HEAD; ref counts stay as informational output.
  if [ "$DIFF_EXPECTED" = 1 ] && [ "$BASE_SHA" = "$CUR_SHA" ]; then
    echo "  *** INCONCLUSIVE: working tree differs from HEAD but the installed file checksum did"
    echo "      not change across the swap — the build swap did not take effect, so this gate"
    echo "      would compare a build against itself."
    FAIL=1
  fi
  if [ "$DIFF_EXPECTED" = 0 ]; then
    echo "  (note: working tree == HEAD for the spike file; gate 2 is vacuous this run)"
  fi
  for s in $SEEDS; do
    cur=$(play "noop-cur-$s" $s 0)
    base=$(printf '%s' "$BASE" | awk -F'\t' -v k="$s" '$1==k{print $2}')
    echo "  seed $s cur : $cur"
    if [ -n "$base" ] && [ "$cur" = "$base" ]; then echo "  >>> seed $s GATE 2: PASS"
    else echo "  >>> seed $s GATE 2: *** FAIL *** base=[$base] cur=[$cur]"; FAIL=1; fi
  done
fi

echo "########## shadow canary: $([ $FAIL = 0 ] && echo PASS || echo FAIL)"
# NOTE: no cleanup here — gate 2's EXIT trap owns removing $WORK, after it has restored the
# working spike file from the backup that lives inside it. When only gate 1 ran there is no
# trap, so clean up that case explicitly.
[ "$MODE" = "onoff" ] && rm -rf "$WORK" 2>/dev/null
exit $FAIL
