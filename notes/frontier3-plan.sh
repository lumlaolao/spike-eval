#!/usr/bin/env bash
# Frontier run on our 3 hardest puzzles, 1 attempt each, every model but Astra, Fable and Opus.
# Jev 1.14 runs through its own bridge (spec jev:typesafe/jev-1.14, on main since PR #305), listed as JEV below.
# Jev Router is out (owner, 2026-10-24).
# Nothing runs until the owner starts it. Needs OPENROUTER_ALLOW_EGRESS=1 (the owner's, never set here).
#   bash notes/frontier3-plan.sh            # all models, 5 at a time
#   RESUME=1 bash notes/frontier3-plan.sh   # pick up where a stopped run left off
#   ONLY="x-ai/grok-4.7 openai/gpt-6-luna" bash notes/frontier3-plan.sh
# The 3 boards (gen2 suite, open split): Luna failed each, furthest from the goal, three families.
set -u
REPO=${REPO:-$(git rev-parse --show-toplevel)}   # a checkout of main
SUITE=$REPO/eval/suites/puzzles-hard-gen2-v1.json
IDS=hard-splice2-325cd33e0a57,hard-hoofdrain-60c5afda2f25,hard-drain2-0ab6f6ae05d9
OUT=${OUT:-$REPO/runs/puzzles/frontier3}
PAR=${PAR:-5}   # each JVM takes 2 GB; this box has 15
# model  cap-in-USD: 3 attempts at the "high" profile (730K prompt half cached, 212K output) priced at each
# model's `max` in eval/pricing/prices.json (its dearest endpoint; budgets check there since PR #303), +10%.
# Sum of caps: about $291 (was $125 at list prices). Expected spend is unchanged (about $37 at list). The gap is
# mostly priority tiers (GPT-6.1 Sol's "ultrafast" is 6x list). Owner choice: keep these caps, or have the bridge
# send provider.max_price at list so routing never lands on a dearer tier, and size the caps at list again.
MODELS="
x-ai/grok-4.7 14.50
x-ai/grok-4.6 14.50
x-ai/grok-build-0.1 6.00
x-ai/grok-4.3 7.00
anthropic/claude-sonnet-5.5 10.50
anthropic/claude-haiku-5.5 1.00
openai/gpt-6.1-sol-pro 57.50
openai/gpt-6.1-sol 57.50
openai/gpt-6-luna 1.00
google/gemini-4-argon 17.50
google/gemini-3.8-flash 7.00
meta/muse-wonder 19.50
meta/muse-spark-1.3 5.00
deepseek/deepseek-v4-pro 5.50
deepseek/deepseek-v4.1-flash 2.50
moonshotai/kimi-k3 23.00
moonshotai/kimi-k2.6 5.00
z-ai/glm-5.3-prime 10.50
z-ai/glm-5.3 10.50
mistralai/mistral-large-4-0 5.50
mistralai/mistral-medium-3-5 10.00
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
