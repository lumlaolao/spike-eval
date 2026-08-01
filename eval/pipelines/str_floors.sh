#!/bin/bash
# STR floor arms: what a policy that does NOT think achieves vs Forge's
# heuristic AI, plus the ai-vs-ai mirror that calibrates what "50%" means.
#
# Ported from the 2026-07-29 job script with three fixes (each was a real
# defect found by verifying the script against disk):
#   FIX 1  the resume guard tested $out/summary.json, which run_batch.py never
#          writes (0 matches under runs/str-floors) — dead code, so a re-run
#          repeated completed decks. Now guards on the game-file count (>=159:
#          floor-doomsday and three random-arm decks legitimately settle at
#          159/160), and partially-complete decks re-enter run_batch, which
#          resumes per-seed on its own.
#   FIX 2  the final aggregate call passed --out (no such flag) and pointed at
#          the PARENT dir, where the non-recursive glob finds 0 games. Now a
#          per-deck loop writes $out/aggregate.json each. For the pooled
#          number use `python3 eval/aggregate.py runs/str-floors --pool`
#          (deck-clustered CI; a pooled scalar over a 26x per-deck spread is
#          near-meaningless alone).
#   FIX 3  the header reasoned for --jobs 6 while invoking --jobs 12; commit
#          f3d96228 sanctioned 12 for model-free cited runs (measured: 6 JVMs
#          left ~8 cores idle). Comment now matches the invocation.
#
# STR-AI has never had a floor next to it, which by this repo's own rule ("a
# score without its floor is not a score") means it has never been a score.
# Identical seeds for every deck and every arm, so arms are PAIRED: a
# difference between them cannot be a difference in the games dealt.
#
# CITED mode (no --exploratory): mirror decks, heuristics forced off, plans
# forbidden, held-out seeds enforced — these numbers are quotable. --pairs
# plays each seed from both seats, so play/draw advantage cannot masquerade
# as skill. The ai mirror arm should land near 50%; if it reads 60/40 the
# harness has a seat bias and every STR number is suspect.
#
# Usage: bash eval/pipelines/str_floors.sh ARM [JOBS]
#   ARM   random | floor | ai | all
#         random = the floor that matters (random plays the real menus)
#         floor  = lower bound (short-circuits before the play menu is built)
#         ai     = ai-vs-ai mirror (seat/play-draw bias calibration)
#   JOBS  run_batch parallelism (default 12 — model-free games are CPU-pure;
#         count JVMs across ALL concurrent batches, eval/CONCURRENCY.md §7)
set -u

ARM=${1:?usage: str_floors.sh random|floor|ai|all [JOBS]}
JOBS=${2:-12}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
D=$REPO/eval/decks
W=$REPO/runs/str-floors
mkdir -p "$W"
cd "$REPO"
log(){ echo "[$(date +%H:%M:%S)] $*"; }

SEEDS_LO=7100; SEEDS_HI=7179          # 80 seeds, inside stage3-v1's STR band
DECKS="ant-storm burn cephalid-breakfast death-and-taxes doomsday elves \
food-chain goblins grixis-reanimator infect lands mardu-energy miracles \
painter sneak-and-show ur-delver"

run_arm(){  # $1 = controller-a
  local ctl=$1
  for deck in $DECKS; do
    local out="$W/$ctl-$deck"
    # FIX 1: guard on games actually on disk, not a file nothing writes.
    local have
    have=$(ls "$out"/game-*.jsonl 2>/dev/null | wc -l | tr -d ' ')
    [ "$have" -ge 159 ] && { log "skip $ctl/$deck ($have games, complete)"; continue; }
    [ "$have" -gt 0 ] && log "resume $ctl/$deck ($have/160 games present)"
    python3 eval/run_batch.py --seeds "$SEEDS_LO..$SEEDS_HI" --pairs --jobs "$JOBS" \
      --controller-a "$ctl" --controller-b ai \
      --deck-a "$D/$deck.txt" --deck-b "$D/$deck.txt" \
      --name-a "$ctl" --name-b "forge-ai" \
      --cap-minutes 10 --out "$out" > "$W/log-$ctl-$deck.txt" 2>&1
    local n
    n=$(find "$out" -name 'game-*.jsonl' 2>/dev/null | wc -l | tr -d ' ')
    log "$ctl vs ai / $deck: $n games"
    [ "$n" = "0" ] && { log "FATAL: $ctl/$deck produced 0 games"; tail -5 "$W/log-$ctl-$deck.txt"; exit 1; }
    # FIX 2: aggregate per deck dir (the glob is non-recursive by design).
    python3 eval/aggregate.py "$out" --json > "$out/aggregate.json" 2>"$W/agg-$ctl-$deck.err" \
      || log "aggregate failed for $ctl/$deck — see $W/agg-$ctl-$deck.err"
  done
}

case "$ARM" in
  random|floor|ai) run_arm "$ARM" ;;
  all) run_arm random; run_arm floor; run_arm ai ;;
  *) echo "unknown arm: $ARM (want random|floor|ai|all)"; exit 2 ;;
esac
log "STR-FLOORS ARM '$ARM' COMPLETE"
log "pooled view: python3 eval/aggregate.py $W --pool   (per-deck rows primary)"
