#!/usr/bin/env bash
# Frontier run on our 3 hardest puzzles, 1 attempt each, every model but Astra, Fable and Opus.
# Jev 1.14 runs through its own bridge (spec jev:typesafe/jev-1.14, branch feat/jev-decisions-bridge, not merged
# yet); it is listed as JEV below and runs only once that bridge is on main. Jev Router is out (owner, 2026-10-24).
# Nothing runs until the owner starts it. Needs OPENROUTER_ALLOW_EGRESS=1 (the owner's, never set here).
#   bash notes/frontier3-plan.sh            # all models, 5 at a time
#   RESUME=1 bash notes/frontier3-plan.sh   # pick up where a stopped run left off
#   ONLY="x-ai/grok-4.7 openai/gpt-6-luna" bash notes/frontier3-plan.sh
# The 3 boards (gen2 suite, open split): Luna failed each, furthest from the goal, three families.
set -u
REPO=${REPO:-/home/user/hpv3}
SUITE=$REPO/eval/suites/puzzles-hard-gen2-v1.json
IDS=hard-splice2-325cd33e0a57,hard-hoofdrain-60c5afda2f25,hard-drain2-0ab6f6ae05d9
OUT=${OUT:-$REPO/runs/puzzles/frontier3}
PAR=${PAR:-5}   # each JVM takes 2 GB; this box has 15
# model  cap-in-USD (the chart's "high" for 3 puzzles, rounded up)
MODELS="
x-ai/grok-4.7 7.00
x-ai/grok-4.6 7.00
x-ai/grok-build-0.1 3.00
x-ai/grok-4.3 3.50
anthropic/claude-sonnet-5.5 9.00
anthropic/claude-haiku-5.5 0.50
openai/gpt-6.1-sol-pro 9.00
openai/gpt-6.1-sol 9.00
openai/gpt-6-luna 0.50
google/gemini-4-argon 9.00
google/gemini-3.8-flash 4.00
meta/muse-wonder 18.00
meta/muse-spark-1.3 4.50
deepseek/deepseek-v4-pro 1.20
deepseek/deepseek-v4.1-flash 1.20
moonshotai/kimi-k3 9.00
moonshotai/kimi-k2.6 3.50
z-ai/glm-5.3-prime 10.00
z-ai/glm-5.3 2.50
mistralai/mistral-large-4-0 4.50
mistralai/mistral-medium-3-5 9.00
"
JEV="typesafe/jev-1.14 0.50"   # $0.042/M input, output free: about $0.05 for 3 puzzles
[ "${OPENROUTER_ALLOW_EGRESS:-}" = 1 ] || { echo "refused: the owner's egress opt-in is not set"; exit 2; }
run_one() {
  local model=$1 cap=$2 dir=$OUT/${1//\//_}
  local spec="openrouter:$model"; [[ $model == typesafe/jev-* ]] && spec="jev:$model"
  python3 "$REPO/eval/puzzle_solve.py" --suite "$SUITE" --split open --ids "$IDS" --k 1 \
    --model "$spec" --budget-usd "$cap" --out "$dir" ${RESUME:+--resume} > "$dir.log" 2>&1
  echo "$model exit $? ($(python3 "$REPO/eval/cost_report.py" "$dir" 2>/dev/null | tail -1))"
}
mkdir -p "$OUT"
printf "%s\n%s\n" "$MODELS" "$JEV" | while read -r m cap; do
  [ -n "$m" ] || continue
  [ -z "${ONLY:-}" ] || [[ " $ONLY " == *" $m "* ]] || continue
  while [ "$(jobs -rp | wc -l)" -ge "$PAR" ]; do wait -n; done
  run_one "$m" "$cap" &
done
wait
python3 "$REPO/eval/puzzle_score.py" --suite "$SUITE" --split open "$OUT"/*/ 2>&1 | tail -30
