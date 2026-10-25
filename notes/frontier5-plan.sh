#!/usr/bin/env bash
# Frontier run on our 5 hardest puzzles, 1 attempt each, every model but Astra, Fable and Opus (owner, 2026-10-25).
# Jev 1.14 runs through its own bridge (spec jev:typesafe/jev-1.14, on main since PR #305), listed as JEV below.
# Jev Router is out (owner, 2026-10-24).
# Nothing runs until the owner starts it. Needs OPENROUTER_ALLOW_EGRESS=1 (the owner's, never set here).
#   bash notes/frontier5-plan.sh            # all models, 5 at a time
#   RESUME=1 bash notes/frontier5-plan.sh   # pick up where a stopped run left off
#   ONLY="x-ai/grok-4.7 openai/gpt-6-luna" bash notes/frontier5-plan.sh
#   REPO=/path/to/main-checkout bash notes/frontier5-plan.sh   # REPO must be at origin/main
# Watch it (one table per model; --follow streams decisions, flags and finished puzzles):
#   for d in runs/puzzles/frontier5/*/; do python3 eval/puzzle_watch.py "$d"; done
# The 5 boards (gen2 suite, open split): the board Luna fell furthest short on (opponent life left over
# starting life, among attempts where Luna made decisions) in each of the 5 families Luna solved at most
# once: atog 15/15 left, hoofdrain 54/65, splice2 13/21, drain2 11/21, elf2 24/59 (demo-recordings 8849d80).
set -u
REPO=${REPO:-$(git rev-parse --show-toplevel)}   # a checkout of main
BUILD=${BUILD:-}   # an isolated build of REPO; built below when unset
FORGE=${FORGE_HOME:-$HOME/forge}; FORGE=${FORGE//\$HOME/$HOME}   # an environment may hold a literal "$HOME/forge"
[ -d "$FORGE/forge-gui-desktop" ] || { echo "refused: no Forge checkout at $FORGE (set FORGE_HOME)"; exit 2; }
SUITE=$REPO/eval/suites/puzzles-hard-gen2-v1.json
IDS=hard-atog-b8053a21d4a8,hard-hoofdrain-60c5afda2f25,hard-splice2-325cd33e0a57,hard-drain2-516c13f84175,hard-elf2-2753b3ca0d06
OUT=${OUT:-$REPO/runs/puzzles/frontier5}
PAR=${PAR:-5}   # each JVM takes 2 GB (-Xmx2048m); this box has 15; one JVM per model (--jobs 1)
# model  cap-in-USD: 5 attempts (the 3-attempt caps x 5/3) at the "high" profile (730K prompt half cached, 212K output) priced at each
# model's `max` in eval/pricing/prices.json (its dearest endpoint; budgets check there since PR #303), +10%.
# Sum of caps: about $486 (5/3 of the 3-board $291.50). Expected spend about $62 at list (5/3 of $37). The gap is
# mostly priority tiers (GPT-6.1 Sol's "ultrafast" is 6x list). The owner kept these caps and asked for a price
# ceiling as well (2026-10-25): every chat model runs with puzzle_solve.py --max-price-at-list, so OpenRouter routes
# only to an endpoint at or under its list price, and the preflight below refuses a model with no such endpoint.
MODELS="
x-ai/grok-4.7 24.17
x-ai/grok-4.6 24.17
x-ai/grok-build-0.1 10.00
x-ai/grok-4.3 11.67
anthropic/claude-sonnet-5.5 17.50
anthropic/claude-haiku-5.5 1.67
openai/gpt-6.1-sol-pro 95.84
openai/gpt-6.1-sol 95.84
openai/gpt-6-luna 1.67
google/gemini-4-argon 29.17
google/gemini-3.8-flash 11.67
meta/muse-wonder 32.50
meta/muse-spark-1.3 8.34
deepseek/deepseek-v4-pro 9.17
deepseek/deepseek-v4.1-flash 4.17
moonshotai/kimi-k3 38.34
moonshotai/kimi-k2.6 8.34
z-ai/glm-5.3-prime 17.50
z-ai/glm-5.3 17.50
mistralai/mistral-large-4-0 9.17
mistralai/mistral-medium-3-5 16.67
"
JEV="typesafe/jev-1.14 0.85"   # $0.042/M input, output free: about $0.01 for 5 puzzles
[ "${OPENROUTER_ALLOW_EGRESS:-}" = 1 ] || { echo "refused: the owner's egress opt-in is not set"; exit 2; }
# The run must play main's harness: puzzle_solve.py otherwise takes the newest build under
# runs/harness-acceptance, which can be an unmerged branch's.
git -C "$REPO" fetch -q origin main || { echo "refused: cannot fetch origin/main"; exit 2; }
HEAD_SHA=$(git -C "$REPO" rev-parse HEAD)
[ "$HEAD_SHA" = "$(git -C "$REPO" rev-parse origin/main)" ] && [ -z "$(git -C "$REPO" status --porcelain -- eval forge-overlay)" ] \
  || { echo "refused: $REPO is not a clean checkout of origin/main"; exit 2; }
if [ -z "$BUILD" ]; then
  BUILD=/tmp/frontier5-build-${HEAD_SHA:0:7}
  ( cd "$REPO" && STAGES="preflight classpath compile" OUT=$BUILD FORGE_HOME=$FORGE \
      bash eval/harness_acceptance.sh > "$BUILD.log" 2>&1 ) || { echo "refused: build failed, see $BUILD.log"; exit 2; }
fi
echo "main $HEAD_SHA, build $BUILD"
# The ceiling makes OpenRouter refuse a request no endpoint at or under list can serve, which would lose every
# decision of that model's puzzles. So: one free GET per model, and stop before anything is paid for.
printf "%s\n" "$MODELS" | awk 'NF==2{print $1}' | python3 -c '
import json, sys, urllib.request
prices = json.load(open(sys.argv[1]))["models"]
bad = []
for m in sys.stdin.read().split():
    eps = json.load(urllib.request.urlopen("https://openrouter.ai/api/v1/models/%s/endpoints" % m, timeout=30))
    ok = [e for e in eps["data"]["endpoints"] if "tools" in (e.get("supported_parameters") or [])
          and float(e["pricing"]["prompt"]) <= prices[m]["prompt"] and float(e["pricing"]["completion"]) <= prices[m]["completion"]]
    print("%-32s %d endpoint(s) that take tools at or under list" % (m, len(ok)))
    bad += [] if ok else [m]
sys.exit("refused: no endpoint at list for %s (update eval/pricing/prices.json or drop the model)" % bad if bad else 0)
' "$REPO/eval/pricing/prices.json" || exit 2
run_one() {
  local model=$1 cap=$2 dir=$OUT/${1//\//_}
  local spec="openrouter:$model" ceiling=--max-price-at-list
  [[ $model == typesafe/jev-* ]] && spec="jev:$model" ceiling=""   # the Decisions API takes no provider object
  python3 "$REPO/eval/puzzle_solve.py" --suite "$SUITE" --split open --ids "$IDS" --k 1 --jobs 1 \
    --build "$BUILD" --forge "$FORGE" \
    --model "$spec" --budget-usd "$cap" $ceiling --out "$dir" ${RESUME:+--resume} > "$dir.log" 2>&1
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
# One score per model (never pooled), k=1, on the 5 boards: a pilot line, not a suite score.
for d in "$OUT"/*/; do
  echo "== $(basename "$d")"
  python3 "$REPO/eval/puzzle_score.py" --suite "$SUITE" --split open --k 1 --ids "$IDS" "$d" 2>&1 | tail -3
done
