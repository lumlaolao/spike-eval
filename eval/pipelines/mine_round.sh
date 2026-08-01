#!/bin/bash
# Mining round pipeline: gen -> mine -> exhaustive -> weighted-random rollout.
#
# Ported from the overnight-2 job script (the round whose RANDOM sampling beat
# 3-4-band targeting ~2x on usable keys — see CLAUDE.md "Sample the rollout
# lane RANDOMLY"). Structure preserved deliberately; the phases encode
# measured lessons:
#   - gen phase: ai-log vs ai-log exploratory, 10 matchups x 20 seeds in the
#     20000-29999 mining band (reserved for exactly this — eval/seed_policy.py)
#   - mining: --max-per-category 100000 (uncapped; selection happens later,
#     reproducibly, in select_sample.py)
#   - exhaustive lane first: free, zero engine time
#   - rollout lane: category-weighted RANDOM sample, --screen-n 8 two-pass,
#     n_per_option 45 (the measured power floor for the mulligan bar),
#     --strict so an infra-broken round refuses to publish a yield number
#
# Usage: bash eval/pipelines/mine_round.sh ROUND [JOBS]
#   ROUND     integer round id; output goes to runs/mine-round-$ROUND and the
#             gen seed base is 20000 + ROUND*1000 (each round gets a fresh
#             200-seed slice of the mining band; rounds 0-9 fit)
#   JOBS      verify/gen parallelism (default 6; see eval/CONCURRENCY.md — 12
#             is sanctioned for model-free runs, count JVMs across ALL
#             concurrent batches)
set -u

ROUND=${1:?usage: mine_round.sh ROUND [JOBS]}
JOBS=${2:-6}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
D=$REPO/eval/decks
W=$REPO/runs/mine-round-$ROUND
SEED_BASE=$((20000 + ROUND * 1000))
SELECT_SEED=$((20260731 + ROUND))
mkdir -p "$W"
cd "$REPO"
log(){ echo "[$(date +%H:%M:%S)] $*"; }

# The matchup table. Display names MUST be the registered spellings from
# eval/holdouts.json — "ANT Storm" (vs registered "Ant Storm") cost 30/320
# positions in round 3, silently, at hour 6. Preflight below catches that
# BEFORE generation instead.
MATCHUPS=$(cat <<'EOF'
Death and Taxes|death-and-taxes|Burn|burn|dnt-burn
Miracles|miracles|Goblins|goblins|miracles-gob
UR Delver|ur-delver|Elves|elves|delver-elves
Mardu Energy|mardu-energy|Miracles|miracles|mardu-miracles
Lands|lands|Burn|burn|lands-burn
Infect|infect|Death and Taxes|death-and-taxes|infect-dnt
Goblins|goblins|UR Delver|ur-delver|gob-delver
Elves|elves|Miracles|miracles|elves-miracles
Painter|painter|Death and Taxes|death-and-taxes|painter-dnt
Burn|burn|Goblins|goblins|burn-gob
EOF
)

# Derive the seed span FROM the matchup count so editing MATCHUPS can never
# silently misallocate seeds (each matchup consumes 20 seeds).
N_MATCHUPS=$(printf '%s\n' "$MATCHUPS" | grep -c '|')
SEED_SPAN=$((N_MATCHUPS * 20))
if [ "$SEED_SPAN" -ne 200 ]; then
  log "note: $N_MATCHUPS matchups -> seed span $SEED_SPAN (round layout changed from the canonical 10x20)"
fi
if [ "$SEED_BASE" -lt 20000 ] || [ "$((SEED_BASE + SEED_SPAN - 1))" -gt 29999 ]; then
  log "FATAL: round $ROUND's seed slice $SEED_BASE-$((SEED_BASE+SEED_SPAN-1)) leaves the 20000-29999 mining band"
  exit 1
fi

# ---- preflight BEFORE any generation: catch the deck-name typo in seconds,
# not at hour 6. Build a minimal jsonl carrying every display name in the
# matchup table and run the same resolver the rollout lane will use.
PRE=$W/preflight-matchups.jsonl
: > "$PRE"
echo "$MATCHUPS" | while IFS='|' read -r NA SA NB SB TAG; do
  [ -n "$NA" ] || continue
  printf '{"id":"preflight-%s","source":{"deck_a":"%s","deck_b":"%s"}}\n' "$TAG" "$NA" "$NB" >> "$PRE"
done
python3 eval/preflight.py "$PRE" --seeds "$SEED_BASE-$((SEED_BASE+199))" || {
  log "FATAL: preflight failed — fix the matchup table / environment before spending compute"
  exit 1
}

if [ ! -s /tmp/forge_cp.txt ]; then
  log "regenerating classpath (no games running)"
  ( cd "$HOME/code/forge" && mvn -q -o -pl forge-gui-desktop -am \
      dependency:build-classpath -Dmdep.outputFile=/tmp/forge_cp.txt )
fi

gen(){ # $1 name-a $2 slug-a $3 name-b $4 slug-b $5 lo $6 hi $7 tag
  # Resume: a matchup dir with >= 20 games is complete (20 seeds, 1 game per
  # seed in exploratory mode); run_batch itself resumes per-seed on re-run.
  local n
  n=$(find "$W/games/$7" -name 'game-*.jsonl' 2>/dev/null | wc -l | tr -d ' ')
  [ "$n" -ge 20 ] && { log "skip gen $7 ($n games, complete)"; return; }
  python3 eval/run_batch.py --seeds "$5..$6" --exploratory --jobs "$JOBS" \
    --controller-a ai-log --controller-b ai-log \
    --deck-a "$D/$2.txt" --deck-b "$D/$4.txt" --name-a "$1" --name-b "$3" \
    --cap-minutes 12 --out "$W/games/$7" > "$W/gen-$7.log" 2>&1
  n=$(find "$W/games/$7" -name 'game-*.jsonl' 2>/dev/null | wc -l | tr -d ' ')
  [ "$n" = "0" ] && { log "FATAL: $7 produced 0 games"; tail -5 "$W/gen-$7.log"; exit 1; }
  log "generated $7 ($n games)"
}

log "PHASE A: corpus generation, round $ROUND (seeds $SEED_BASE-$((SEED_BASE+199)))"
i=0
echo "$MATCHUPS" | while IFS='|' read -r NA SA NB SB TAG; do
  [ -n "$NA" ] || continue
  LO=$((SEED_BASE + i * 20)); HI=$((LO + 19))
  gen "$NA" "$SA" "$NB" "$SB" "$LO" "$HI" "$TAG"
  i=$((i + 1))
done
log "PHASE A done: $(find "$W/games" -name 'game-*.jsonl' | wc -l) games"

log "PHASE B: mining (all categories, uncapped)"
python3 eval/mine_puzzles.py "$W/games" --out "$W/candidates.jsonl" \
  --max-per-category 100000 > "$W/mine.txt" 2>&1
log "PHASE B done: $(wc -l < "$W/candidates.jsonl") candidates"

# Preflight again on the REAL candidates (they carry the deck names the
# rollout lane will resolve) — cheap, and catches anything gen renamed.
python3 eval/preflight.py "$W/candidates.jsonl" || {
  log "FATAL: candidate preflight failed"; exit 1; }

log "PHASE C: exhaustive lane (free)"
python3 eval/verify_keys.py "$W/candidates.jsonl" \
  --out "$W/exhaustive-positions.jsonl" \
  --lane exhaustive --driver none \
  --summary "$W/exhaustive-summary.json" > "$W/exhaustive.txt" 2>&1
tail -3 "$W/exhaustive.txt"

log "PHASE D: rollout lane, category-weighted RANDOM sample (seed $SELECT_SEED)"
python3 eval/pipelines/select_sample.py "$W/candidates.jsonl" \
  --out "$W/rollout-sample.jsonl" --seed "$SELECT_SEED"
python3 eval/verify_keys.py "$W/rollout-sample.jsonl" \
  --out "$W/rollout-positions.jsonl" \
  --lane rollout --driver engine --screen-n 8 --n-per-option 45 \
  --jobs "$JOBS" --strict \
  --summary "$W/rollout-summary.json" > "$W/rollout.txt" 2>&1
RC=$?
cat "$W/rollout.txt"
if [ "$RC" -eq 4 ]; then
  log "ROUND $ROUND REFUSED BY HEALTH GATE: infra_failure_rate > 0.05 — the"
  log "yield number is a harness measurement, not a mining measurement."
  log "See by_driver_reason in $W/rollout-summary.json"
  exit 4
fi
log "MINE-ROUND-$ROUND COMPLETE (verify exit $RC)"
