#!/bin/bash
# The keyword gauntlet: every mechanic in full games, at zero model cost.
#
# eval/gauntlet/kw00..kw27.txt hold the cards KeywordSurfaceSweepTest sweeps (up to three per Forge keyword, 553
# cards, two copies of twenty each plus four of each basic), and kw28.txt Forge's twelve Forecast cards (not a keyword,
# so the sweep never drew one: docs/harness/ARCHITECTURE.md, "Forecast"). Each deck's LLM seat plays the next deck's Forge AI,
# answering every decision through the answer tool from the loopback fake model in its `first` policy, which takes
# the first option of every ask: it casts what it can, attacks, blocks, and pays. Its triggers, combat, upkeep and
# end-step mechanics reach the seat the way a game does, which the sweep's single plays cannot.
# eval/gauntlet_scan.py then fails on anything that is a harness defect: a choice Forge's AI made, an answer the
# seat could not read, a play that failed to pay or to target, a crash, a game that did not finish. A failed target on
# a card only the Forge AI opponent's deck holds is the AI's own play: listed apart, not counted.
#
# 1996 World Champion is left out: its "discard your hand: search for a card named 1996 World Champion" can be
# activated with an empty hand forever, and the `first` policy does.
#
# GAUNTLET_POLICY=random answers every ask with a uniformly random legal answer instead (seeded per deck), so the
# targets, picks, orders, splits, numbers, blocks and mulligans `first` never gives reach the harness too.
# GAUNTLET_SEED=<salt> draws a different random game per deck (the seed is the salt plus the deck name).
# GAUNTLET_TOOLS=1 gives the seat its lookups (MEMORY_TOOLS=1 LOOKAHEAD=1); the random policy then previews, inspects
# and searches at random before some answers, so every card's play is simulated by the lookahead preview too.
#
# usage: bash eval/keyword_gauntlet.sh [out-dir] [parallel]   (needs a passing harness_acceptance.sh compile)
# The build is GAUNTLET_BUILD (a harness_acceptance.sh OUT), else the newest one whose compile passed (it wrote
# overlay-sha.txt); its overlay hash must be this checkout's, so a green gauntlet is about the code a paid game runs.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
FORGE="${FORGE_HOME:-$HOME/code/forge}"
OUT="${1:-$REPO/runs/keyword-gauntlet/$(date -u +%Y%m%dT%H%M%SZ)}"
PAR="${2:-4}"
BUILD_HOW="STAGES=\"preflight classpath compile\" FORGE_HOME=$FORGE bash eval/harness_acceptance.sh"
if [ -n "${GAUNTLET_BUILD:-}" ]; then RUN="$GAUNTLET_BUILD"
else
  newest="$(ls -t "$REPO"/runs/harness-acceptance/*/overlay-sha.txt 2>/dev/null | head -1)"
  RUN="${newest%/overlay-sha.txt}"
fi
CLASSES="$RUN/classes"
CP_FILE="$RUN/classpath.txt"
[ -n "$RUN" ] && [ -d "$CLASSES" ] && [ -f "$CP_FILE" ] && [ -s "$RUN/overlay-sha.txt" ] \
  || { echo "no passing acceptance build${RUN:+ at $RUN}: compile first: $BUILD_HOW"; exit 2; }
# Each game runs from the checkout: without it every game exits 1 and the run says so only at the end.
[ -d "$FORGE/forge-gui-desktop" ] || { echo "no Forge checkout at $FORGE: set FORGE_HOME"; exit 2; }
BUILT="$(head -1 "$RUN/overlay-sha.txt")"
# The command prints nothing and exits 1 for an "unknown" hash, so unknown never equals unknown.
HERE_SHA="$(python3 "$REPO/eval/benchmark_version.py" --overlay-sha --forge "$FORGE" 2>/dev/null)"
[ -n "$HERE_SHA" ] && [ "$BUILT" != unknown ] && [ "$BUILT" = "$HERE_SHA" ] || {
  echo "build $RUN was compiled from overlay $BUILT, this checkout's is ${HERE_SHA:-unreadable}: compile first: $BUILD_HOW"
  exit 2; }
JAVA="${JAVA_HOME:+$JAVA_HOME/bin/}java"
mkdir -p "$OUT" && OUT="$(cd "$OUT" && pwd)" # absolute: each game runs from the Forge checkout
POLICY="${GAUNTLET_POLICY:-first}"
SALT="${GAUNTLET_SEED:-}"
TOOLS="${GAUNTLET_TOOLS:-0}"
# The JVM flags of the game launchers (run-llm-headless.sh) and the acceptance canaries: eval/tests/test_launchers.py
# keeps the four sets equal. -Xmx is lower here, where four games run side by side.
JVM_FLAGS="--add-opens java.desktop/java.beans=ALL-UNNAMED --add-opens java.desktop/javax.swing.border=ALL-UNNAMED \
--add-opens java.desktop/javax.swing.event=ALL-UNNAMED --add-opens java.desktop/sun.swing=ALL-UNNAMED \
--add-opens java.desktop/java.awt.image=ALL-UNNAMED --add-opens java.desktop/java.awt.color=ALL-UNNAMED \
--add-opens java.desktop/sun.awt.image=ALL-UNNAMED --add-opens java.desktop/javax.swing=ALL-UNNAMED \
--add-opens java.desktop/java.awt=ALL-UNNAMED --add-opens java.desktop/java.awt.font=ALL-UNNAMED \
--add-opens java.base/java.util=ALL-UNNAMED --add-opens java.base/java.lang=ALL-UNNAMED \
--add-opens java.base/java.lang.reflect=ALL-UNNAMED --add-opens java.base/java.text=ALL-UNNAMED \
--add-opens java.base/jdk.internal.misc=ALL-UNNAMED --add-opens java.base/sun.nio.ch=ALL-UNNAMED \
--add-opens java.base/java.nio=ALL-UNNAMED --add-opens java.base/java.math=ALL-UNNAMED \
--add-opens java.base/java.util.concurrent=ALL-UNNAMED --add-opens java.base/java.net=ALL-UNNAMED \
-Xmx3g -Dio.netty.tryReflectionSetAccessible=true -Dfile.encoding=UTF-8 -Djava.util.Arrays.useLegacyMergeSort=true \
-Dsun.java2d.d3d=false -Dapple.awt.UIElement=true"
export REPO FORGE OUT CLASSES CP_FILE JAVA POLICY SALT TOOLS JVM_FLAGS
game() { # $1 deck A tag, $2 deck B tag
  local dir="$OUT/g-$1"; rm -rf "$dir"; mkdir -p "$dir"
  python3 "$REPO/eval/fake_model_server.py" --port 0 --policy "$POLICY" --seed "$SALT$1" --answer-tool --log "$dir/fake_requests.jsonl" \
      >"$dir/fake_server.out" 2>&1 &
  local fp=$! port=""
  for i in $(seq 1 20); do port="$(awk '/^LISTENING/{print $2}' "$dir/fake_server.out" 2>/dev/null)"; [ -n "$port" ] && break; sleep 0.5; done
  ( cd "$FORGE/forge-gui-desktop" && env GAME_SEED=7 EVAL_OUT="$dir" DECK_A="$REPO/eval/gauntlet/$1.txt" DECK_B="$REPO/eval/gauntlet/$2.txt" \
      DECK_A_NAME="$1" DECK_B_NAME="$2-ai" GAME_CAP_MS=300000 CONTROLLER_A=llm CONTROLLER_B=ai \
      SMART_MODEL=openrouter:fake/harness-model FAST_MODEL=openrouter:fake/harness-model \
      OPENROUTER_BASE_URL="http://127.0.0.1:$port/v1" OPENROUTER_TIMEOUT_MS=4000 CONTEXT_PROFILE=v4-faithful \
      SMART_BUDGET=100000 FAST_BUDGET=100000 MULLIGAN_HEURISTIC=0 SKIP_HEURISTICS=0 MEMORY_TOOLS="$TOOLS" LOOKAHEAD="$TOOLS" \
      "$JAVA" $JVM_FLAGS -cp "$CLASSES:$(cat "$CP_FILE")" forge.spike.LlmSpikeHeadless ) >"$dir/game.log" 2>&1
  echo $? >"$dir/exit_code"
  kill $fp 2>/dev/null; wait $fp 2>/dev/null
}
export -f game
n=$(ls "$REPO"/eval/gauntlet/kw*.txt | wc -l)
for i in $(seq 0 $((n - 1))); do printf "kw%02d kw%02d\n" "$i" $(( (i + 1) % n )); done | xargs -P "$PAR" -n 2 bash -c 'game "$0" "$1"'
python3 "$REPO/eval/gauntlet_scan.py" "$OUT"
