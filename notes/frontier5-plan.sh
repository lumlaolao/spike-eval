#!/usr/bin/env bash
# Frontier run on our 5 hardest puzzles, 1 attempt each, every model but Astra, Fable and Opus (owner, 2026-10-25).
# Jev 1.14 runs through its own bridge (spec jev:typesafe/jev-1.14, on main since PR #305), listed as JEV below.
# Jev Router is out (owner, 2026-10-24).
# Nothing runs until the owner starts it. Needs OPENROUTER_ALLOW_EGRESS=1 (the owner's, never set here).
#   STAGE=1 bash notes/frontier5-plan.sh   # the trial: board 1 only, every model (about $12); read it first
#   RESUME=1 bash notes/frontier5-plan.sh  # then the other 4 boards (board 1 is skipped), or a stopped run
#   bash notes/frontier5-plan.sh           # all 5 boards in one go, no trial
#   DRY=1 STAGE=1 bash notes/frontier5-plan.sh   # the free checks, then print each command instead of running it
# Land every PR the run needs before the trial: a resume refuses a build other than the trial's. Run the trial and
# the resume close together: if a closed model's list price moves in between, the preflight stops the resume, and
# a new prices.json makes a new ceiling, which --resume refuses for that model's trial board (a new condition). Then
# play that model again with ONLY="<model>" OUT=<a new directory> (the ceiling of a precision-filtered model does not
# depend on its list price, so it is unaffected).
# Run it from a checkout of this notes branch with REPO=<a clean checkout of origin/main> (notes/ is not on main);
# secrets.env (the owner's key and egress opt-in) is read from $REPO.
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
case "${STAGE:-}" in
  "") ;;
  1) IDS=${IDS%%,*} ;;   # the trial: the first board only
  *) echo "refused: STAGE is 1 (the board-1 trial) or unset (all boards)"; exit 2 ;;
esac
# A second launch into the same directories must say it continues them, or the trial's board would be played twice.
if [ -z "${RESUME:-}" ] && [ -z "${DRY:-}" ] && ls -d "$OUT"/*/ >/dev/null 2>&1; then
  echo "refused: $OUT already holds a run; RESUME=1 continues it (after the trial, RESUME=1 plays the other boards)"
  exit 2
fi
PAR=${PAR:-4}   # one JVM per model (--jobs 1), -Xmx2048m heap and about 3 GB resident: 4 fit a 15 GB box
# model  cap-in-USD: 5 attempts (the 3-attempt caps x 5/3) at the "high" profile (730K prompt half cached, 212K output) priced at each
# model's `max` in eval/pricing/prices.json (its dearest endpoint; budgets check there since PR #303), +10%.
# Sum of caps: about $489 (5/3 of the 3-board $291.50, plus Haiku 5.5 recomputed: its 1.67 was Luna's figure,
# and at its `max`, the long-context tier, the rule above gives 4.42; Fable pre-run check, 2026-10-26). Expected spend about $62 at list (5/3 of $37). The gap is
# mostly priority tiers (GPT-6.1 Sol's "ultrafast" is 6x list). The owner kept these caps and asked for a price
# ceiling as well (2026-10-25): a closed model runs with puzzle_solve.py --max-price-at-list, so OpenRouter routes
# only to an endpoint at or under its list price. An open-weight model (a third column) runs instead at its maker's
# released precision, never on a host that compresses it further (owner, 2026-10-26): its list price is a 4-bit
# host's, so it takes --quantizations and no ceiling. The released weights (huggingface.co/api/models, 2026-10-26):
# DeepSeek V4 Pro and V4.1 Flash and GLM 5.3 are fp8 checkpoints; Kimi K3 is packed 4-bit, which Moonshot serves as
# mxfp4; Kimi K2.6 is packed int4, which Moonshot serves as int4. K2.6 takes int4 only (owner, 2026-10-26): the
# board-1 trial routed it to the one bf16 host, Crusoe, at about 18 tokens/s, and it reached the win after the
# 30-minute game cap. That attempt is kept under frontier5/; K2.6 runs again under frontier5-kimi-k2.6-int4/. The preflight refuses a model with no endpoint
# its filter allows.
MODELS="
x-ai/grok-4.7 24.17
x-ai/grok-4.6 24.17
x-ai/grok-build-0.1 10.00
x-ai/grok-4.3 11.67
anthropic/claude-sonnet-5.5 17.50
anthropic/claude-haiku-5.5 4.42
openai/gpt-6.1-sol-pro 95.84
openai/gpt-6.1-sol 95.84
openai/gpt-6-luna 1.67
google/gemini-4-argon 29.17
google/gemini-3.8-flash 11.67
meta/muse-wonder 32.50
meta/muse-spark-1.3 8.34
deepseek/deepseek-v4-pro 9.17 fp8,bf16,fp16
deepseek/deepseek-v4.1-flash 4.17 fp8,bf16,fp16
moonshotai/kimi-k3 38.34 mxfp4,fp8,bf16,fp16
moonshotai/kimi-k2.6 8.34 int4
z-ai/glm-5.3-prime 17.50
z-ai/glm-5.3 17.50 fp8,bf16,fp16
mistralai/mistral-large-4-0 9.17
mistralai/mistral-medium-3-5 16.67
"
JEV="typesafe/jev-1.14 0.85"   # $0.042/M input, output free: about $0.01 for 5 puzzles
# DRY sends no game anywhere (only the public price lookups below), so it needs no opt-in; a real run does.
[ "${OPENROUTER_ALLOW_EGRESS:-}" = 1 ] || [ -n "${DRY:-}" ] || { echo "refused: the owner's egress opt-in is not set"; exit 2; }
# The run must play main's harness: puzzle_solve.py otherwise takes the newest build under
# runs/harness-acceptance, which can be an unmerged branch's.
git -C "$REPO" fetch -q origin main || { echo "refused: cannot fetch origin/main"; exit 2; }
HEAD_SHA=$(git -C "$REPO" rev-parse HEAD)
[ "$HEAD_SHA" = "$(git -C "$REPO" rev-parse origin/main)" ] && [ -z "$(git -C "$REPO" status --porcelain -- eval forge-overlay)" ] \
  || { echo "refused: $REPO is not a clean checkout of origin/main"; exit 2; }
if [ -z "$BUILD" ] && [ -n "${DRY:-}" ]; then BUILD="<built at run time>"
elif [ -z "$BUILD" ]; then
  BUILD=/tmp/frontier5-build-${HEAD_SHA:0:7}
  ( cd "$REPO" && STAGES="preflight classpath compile" OUT=$BUILD FORGE_HOME=$FORGE \
      bash eval/harness_acceptance.sh > "$BUILD.log" 2>&1 ) || { echo "refused: build failed, see $BUILD.log"; exit 2; }
fi
echo "main $HEAD_SHA, build $BUILD"
# Read by the JVM and not pinned by puzzle_solve's condition: a value in the owner's shell must not reach the seat.
unset ANSWER_TOOL_CHOICE PROMPT_CACHE
# The ceiling makes OpenRouter refuse a request no endpoint at or under list can serve, which would lose every
# decision of that model's puzzles. So: one free GET per model, and stop before anything is paid for.
printf "%s\n" "$MODELS" | awk 'NF>=2{print $1, (NF>2 ? $3 : "-")}' | ONLY="${ONLY:-}" python3 -c '
import json, sys, urllib.request
prices = json.load(open(sys.argv[1]))["models"]
bad = []
# A list price that moved since prices.json was checked makes a stale ceiling (deepseek-v4-pro rose 72% in one day,
# 2026-10-25): every request above it would be refused. Live list must equal the file.
live = {x["id"]: x["pricing"] for x in json.load(urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=60))["data"]}
import os
only = os.environ.get("ONLY", "").split()
for m, quant in (ln.split() for ln in sys.stdin.read().splitlines() if ln.strip()):
    if only and m not in only:
        continue
    eps = json.load(urllib.request.urlopen("https://openrouter.ai/api/v1/models/%s/endpoints" % m, timeout=30))
    # require_parameters is sent too: the endpoint must take tools and tool_choice, not tools alone.
    tools = [e for e in eps["data"]["endpoints"] if {"tools", "tool_choice"} <= set(e.get("supported_parameters") or [])]
    if quant != "-":   # an open-weight model: an endpoint at an allowed precision, any price (no ceiling)
        ok = [e for e in tools if (e.get("quantization") or "unknown") in quant.split(",")]
    else:
        ok = [e for e in tools
              if float(e["pricing"]["prompt"]) <= prices[m]["prompt"] and float(e["pricing"]["completion"]) <= prices[m]["completion"]]
    drift = [k for k in ("prompt", "completion") if abs(float(live[m][k]) - prices[m][k]) > 1e-12]
    print("%-32s %d endpoint(s) that take tools and tool_choice %s%s" % (m, len(ok),
          "at " + quant if quant != "-" else "at or under list",
          ("; LIST PRICE MOVED: %s" % live[m] + ("" if quant == "-" else " (no ceiling: cost estimate only)")) if drift else ""))
    # A moved list price stops only a model whose ceiling it is; for a precision-filtered one it is a cost note.
    bad += [] if ok and not (drift and quant == "-") else [m]
sys.exit("refused: no endpoint the filter allows, or a moved list price, for %s (update eval/pricing/prices.json or drop the model)" % bad if bad else 0)
' "$REPO/eval/pricing/prices.json" || exit 2
run_one() {
  local model=$1 cap=$2 quant=${3:-} dir=$OUT/${1//\//_}
  local spec="openrouter:$model" filter=(--max-price-at-list)
  [ -z "$quant" ] || filter=(--quantizations "$quant")   # an open-weight model: its released precision, no ceiling
  [[ $model == typesafe/jev-* ]] && spec="jev:$model" filter=()   # the Decisions API takes no provider object
  # A 90-minute game cap (owner, 2026-10-26): two trial games reached the win after 30. The cap is a condition, so a
  # resume replays every board played at the trial's 30 in full (puzzle_solve --game-cap-minutes, PR #329).
  local cmd=(python3 "$REPO/eval/puzzle_solve.py" --suite "$SUITE" --split open --ids "$IDS" --k 1 --jobs 1
    --game-cap-minutes 90 --build "$BUILD" --forge "$FORGE" --model "$spec" --budget-usd "$cap" ${filter[@]+"${filter[@]}"} --out "$dir" ${RESUME:+--resume})
  if [ -n "${DRY:-}" ]; then echo "${cmd[*]}"; return; fi
  "${cmd[@]}" >> "$dir.log" 2>&1   # appended: the trial's progress lines survive the resume
  echo "$model exit $? ($(tail -1 "$dir.log"))"   # puzzle_solve's last line: [cost] over its puzzles
}
mkdir -p "$OUT"
# The loop reads from a process substitution, not a pipe, so its jobs are this shell's and the wait below waits for
# them (behind a pipe the loop is a subshell, and the trial's summary printed while models still played: Fable
# pre-run check, 2026-10-26). A poll, not `wait -n`, which macOS's bash 3.2 lacks.
while read -r m cap quant; do
  [ -n "$m" ] || continue
  [ -z "${ONLY:-}" ] || [[ " $ONLY " == *" $m "* ]] || continue
  while [ "$(jobs -rp | wc -l)" -ge "$PAR" ]; do sleep 10; done
  run_one "$m" "$cap" "$quant" &
done < <(printf "%s\n%s\n" "$MODELS" "$JEV")
wait
[ -z "${DRY:-}" ] || exit 0
if [ -n "${STAGE:-}" ]; then
  # The trial's reading: one table per model (decisions by tier, AI fallbacks, text parses, refused lookups,
  # ROUTING-FILTER, exceptions, cost). Every model must be clean before RESUME=1 plays the other boards.
  for d in "$OUT"/*/; do python3 "$REPO/eval/puzzle_watch.py" "$d" 2>&1 | tail -6; done
  echo "trial done: read the tables above, then RESUME=1 bash notes/frontier5-plan.sh plays boards 2 to 5"
  exit 0
fi
# One score per model (never pooled), k=1, on the 5 boards: a pilot line, not a suite score.
for d in "$OUT"/*/; do
  echo "== $(basename "$d")"
  python3 "$REPO/eval/puzzle_score.py" --suite "$SUITE" --split open --k 1 --ids "$IDS" "$d" 2>&1 | tail -3
done
