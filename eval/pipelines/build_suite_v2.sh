#!/bin/bash
# Freeze puz-v2 from ALL verified keys to date, via the canonical pipeline.
#
# Sources (dedup by id, mulligan excluded — binary floor >=0.5, measured
# 0.81 "always KEEP"; never fold into a near-zero-floor suite):
#   runs/overnight-1, runs/overnight-2       (the original verified keys)
#   runs/mine-round-{4,5,6,7}/exhaustive-positions.jsonl
#   runs/mine-round-4pilot + runs/mine-round-4 cloud shards (rollout keys)
#
# Canonical three-step pipeline, order load-bearing (see CLAUDE.md):
#   shuffle -> select --min-options 3 --require-single-answer --filters-only
#           -> shuffle --balance-salts 400
#
# Usage: bash eval/pipelines/build_suite_v2.sh [OUT_PREFIX]
#        default OUT_PREFIX=eval/suites/puz-v2
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
cd "$REPO"
OUT=${1:-eval/suites/puz-v2}
W=$(mktemp -d)
log(){ echo "[$(date +%H:%M:%S)] $*"; }

python3 - "$W" <<'PY'
import glob, json, sys
w = sys.argv[1]
srcs  = sorted(glob.glob('runs/overnight-1/*positions*.jsonl'))
srcs += sorted(glob.glob('runs/overnight-2/*positions*.jsonl'))
srcs += sorted(glob.glob('runs/mine-round-*/exhaustive-positions.jsonl'))
srcs += sorted(glob.glob('runs/mine-round-*/rollout-positions.jsonl'))
srcs += sorted(glob.glob('runs/mine-round-4pilot/rollout-positions.jsonl'))
seen, keys = set(), []
per_src = {}
for f in srcs:
    n = 0
    for line in open(f):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get('status') != 'verified':
            continue
        if r.get('category') == 'mulligan':
            continue
        rid = r.get('id')
        if rid in seen:
            continue
        seen.add(rid)
        keys.append(r)
        n += 1
    if n:
        per_src[f] = n
for f, n in per_src.items():
    print('  %4d new verified keys from %s' % (n, f))
print('TOTAL distinct verified non-mulligan keys: %d' % len(keys))
with open(w + '/all-keys.jsonl', 'w') as out:
    for r in keys:
        out.write(json.dumps(r, sort_keys=True) + '\n')
PY

log "step 1/3: shuffle"
python3 eval/shuffle_suite.py "$W/all-keys.jsonl" --out "$W/shuffled.jsonl"
log "step 2/3: select (min-options 3, single answer, filters only)"
python3 eval/select_suite.py "$W/shuffled.jsonl" --out "$W/filtered.jsonl" \
  --min-options 3 --require-single-answer --filters-only
log "step 3/3: shuffle with balance salts"
python3 eval/shuffle_suite.py "$W/filtered.jsonl" --out "$OUT.jsonl" --balance-salts 400
log "frozen: $OUT.jsonl ($(grep -c . "$OUT.jsonl") positions, sha256 $(shasum -a 256 "$OUT.jsonl" | cut -c1-16)...)"
log "NOW: build the manifest ($OUT.json) with floors from the final"
log "     select/shuffle output, the exact command above, and known_limits"
log "     INCLUDING: cloud-minted rollout keys were verified on linux-x86_64"
log "     replays of darwin-mined games (probe_matches enforced byte-identical"
log "     menus; the seed-42 full-game canary shows an identity-label flip"
log "     across platforms, so cross-platform full-game outcomes are NOT"
log "     assumed interchangeable — only replay-validated forks are used)."
