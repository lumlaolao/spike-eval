#!/usr/bin/env bash
# GPT-6 Luna, one attempt on each of the 44 open boards of the third hard suite. Not run until the owner starts it.
# Expected about $1 at list: Luna's gen2 run was $3.62 for 156 boards (demo-recordings, puzzles/hard-gen2-luna).
# The budget is checked at Luna's dearest tool-taking endpoint (2x list, eval/pricing/prices.json), so the cap is
# $2.50 to leave the expected spend room. Same condition as the gen2 run (the default profile, no lookup tools).
#   bash notes/gen3-luna-plan.sh
#   RESUME=1 bash notes/gen3-luna-plan.sh                    # pick up a stopped run
#   python3 eval/puzzle_watch.py runs/puzzles/gen3-luna --follow   # watch it (once puzzle_watch is on main)
set -u
REPO=${REPO:-$(git rev-parse --show-toplevel)}   # a checkout of main
OUT=${OUT:-$REPO/runs/puzzles/gen3-luna}
CAP=${CAP:-2.50}
BUILD=${BUILD:-}
FORGE=${FORGE_HOME:-$HOME/forge}; FORGE=${FORGE//\$HOME/$HOME}   # an environment may hold a literal "$HOME/forge"
[ -d "$FORGE/forge-gui-desktop" ] || { echo "refused: no Forge checkout at $FORGE (set FORGE_HOME)"; exit 2; }
[ "${OPENROUTER_ALLOW_EGRESS:-}" = 1 ] || { echo "refused: the owner's egress opt-in is not set"; exit 2; }
git -C "$REPO" fetch -q origin main || { echo "refused: cannot fetch origin/main"; exit 2; }
HEAD_SHA=$(git -C "$REPO" rev-parse HEAD)
[ "$HEAD_SHA" = "$(git -C "$REPO" rev-parse origin/main)" ] && [ -z "$(git -C "$REPO" status --porcelain -- eval forge-overlay)" ] \
  || { echo "refused: $REPO is not a clean checkout of origin/main"; exit 2; }
if [ -z "$BUILD" ]; then
  BUILD=/tmp/gen3-build-${HEAD_SHA:0:7}
  ( cd "$REPO" && STAGES="preflight classpath compile" OUT=$BUILD FORGE_HOME=$FORGE \
      bash eval/harness_acceptance.sh > "$BUILD.log" 2>&1 ) || { echo "refused: build failed, see $BUILD.log"; exit 2; }
fi
echo "main $HEAD_SHA, build $BUILD, cap \$$CAP"
mkdir -p "$(dirname "$OUT")"
python3 "$REPO/eval/puzzle_solve.py" --suite "$REPO/eval/suites/puzzles-hard-gen3-v1.json" --split open --k 1 \
  --jobs 2 --build "$BUILD" --forge "$FORGE" --model openrouter:openai/gpt-6-luna \
  --budget-usd "$CAP" --out "$OUT" ${RESUME:+--resume} 2>&1 | tee "$OUT.log"
python3 "$REPO/eval/puzzle_score.py" --suite "$REPO/eval/suites/puzzles-hard-gen3-v1.json" --split open --k 1 "$OUT"
python3 "$REPO/eval/cost_report.py" "$OUT" | tail -3
