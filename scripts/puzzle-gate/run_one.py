"""Run one puzzle under one arm pair with an isolated build: run_one.py BUILD PZL OUT CTL_A CTL_B [KEY=VAL...]"""
import os, sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))
import puzzle_baselines as pb
build, pzl, out, a, b = sys.argv[1:6]
env = {"PUZZLE": pzl, "CONTROLLER_A": a, "CONTROLLER_B": b, "PZL_SEEDS": "1"}
for kv in sys.argv[6:]:
    k, v = kv.split("=", 1); env[k] = v
bd = pb.Build(build, os.environ.get("FORGE_HOME", "/root/forge"))
rc = bd.run(env, out, timeout_s=3600)
print("rc", rc)
for r in pb.read_results(out):
    print(json.dumps({k: r.get(k) for k in ("status", "solved", "acceptance", "acceptance_failed", "line",
                      "search_replays", "opponent_branches_beaten", "actions", "search_drift")}))
