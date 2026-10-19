#!/bin/bash
R=$(cd "$(dirname "$0")/../.." && pwd)
mkdir -p "${SCRATCH:-/tmp/puzzle-scratch}/rp10" "${SCRATCH:-/tmp/puzzle-scratch}/rp10v"
# gate10.sh ID : search arm then pass arm on the build named in $BUILD_FILE (default $S/epoch62-build.txt),
#   summary to $S/rp10/ID.summary
#   V_<name> : scratch variant $S/rp10v/<name>.pzl ; R_<ID>[:seeds] : random arm, logs only
S=${SCRATCH:-/tmp/puzzle-scratch}
D=$S/rp10
pz() { local id=$1; if [[ $id == V_* ]]; then echo $S/rp10v/${id#V_}.pzl; else echo $R/eval/puzzles/rules-pilot/$id.pzl; fi; }
# jvms: java processes only (a shell whose command line names the pattern must not count)
jvms() { for p in $(pgrep -f "rp10/.*java.args"); do ps -o comm= -p $p; done | grep -c '\(^\|/\)java$'; }   # macOS ps prints the full path
# wait for no HOLD and fewer than 2 JVMs; serialize starts with a lock (a directory: mkdir is atomic),
# kept until our JVM shows up
startgate() {  # $1 = tag ; prints a starts line
  until mkdir $S/rp10/start.lock 2>/dev/null; do sleep 2; done
  trap 'rmdir "$S/rp10/start.lock" 2>/dev/null' EXIT   # a killed run must not hold the lock
  while [ -e $S/HOLD ] || [ $(jvms) -ge 2 ]; do sleep 20; done
  echo "start-$1 $(date +%s) hold=$( [ -e $S/HOLD ] && echo Y || echo N) jvms=$(jvms)"
}
release() { n0=$1; for i in $(seq 1 30); do [ $(jvms) -gt $n0 ] && break; sleep 1; done; sleep 2; rmdir $S/rp10/start.lock; }
if [[ $1 == R_* ]]; then
  id=${1#R_}; seeds=${2:-1,2,3,4}; P=$(pz $id); rm -rf $D/$id-rand
  startgate rand >> $D/$id.starts; B=$(cat ${BUILD_FILE:-$S/epoch62-build.txt}); echo "build-rand $B" >> $D/$id.starts
  n0=$(jvms); python3 $R/scripts/puzzle-gate/run_one.py $B $P $D/$id-rand random search PZL_SEEDS=$seeds GAME_CAP_MS=60000 > $D/$id.rand.out 2>&1 &
  release $n0; wait; exit 0
fi
id=$1; P=$(pz $id); rm -rf $D/$id-search $D/$id-pass; : > $D/$id.starts
t0=$(date +%s)
startgate search >> $D/$id.starts; B=$(cat ${BUILD_FILE:-$S/epoch62-build.txt}); echo "build-search $B" >> $D/$id.starts
n0=$(jvms); python3 $R/scripts/puzzle-gate/run_one.py $B $P $D/$id-search search search PZL_SEARCH_BUDGET=600 GAME_CAP_MS=60000 > $D/$id.search.out 2>&1 &
release $n0; wait
startgate pass >> $D/$id.starts; B=$(cat ${BUILD_FILE:-$S/epoch62-build.txt}); echo "build-pass $B" >> $D/$id.starts
n0=$(jvms); python3 $R/scripts/puzzle-gate/run_one.py $B $P $D/$id-pass pass search GAME_CAP_MS=60000 > $D/$id.pass.out 2>&1 &
release $n0; wait
python3 - $D/$id $(( $(date +%s) - t0 )) > $D/$id.summary <<'PY'
import json,sys
b=sys.argv[1]
for arm in ("search","pass"):
    try:
        rows=[json.loads(l) for l in open(b+"-"+arm+"/puzzle-results.jsonl")]
    except Exception as e:
        print(arm,"NO RESULTS",e); continue
    for r in rows:
        keep={k:r.get(k) for k in ("status","acceptance","acceptance_failed","line","search_replays","opponent_branches_beaten","actions","search_drift")}
        keep.update({k:v for k,v in r.items() if k.startswith("uniqueness")})
        print(arm, json.dumps(keep))
print("secs", sys.argv[2])
PY
