import sys, json, time, os, shutil
from pathlib import Path
sys.path.insert(0, '/home/user/aiblind-wt/eval/puzzles'); sys.path.insert(0,'/home/user/aiblind-wt/eval')
import puzzle_baselines as pb
ID, OPP, UB = sys.argv[1], sys.argv[2], sys.argv[3]
build = pb.Build('/home/user/aiblind-build')
pzl = Path('/home/user/aiblind-wt/eval/puzzles/rules-pilot/%s.pzl' % ID)
out = Path('/home/user/aiblind-runs/raw/%s-%s' % (ID, OPP))
env = {"CONTROLLER_B": OPP, "PZL_SEARCH_BUDGET": "600", "GAME_CAP_MS": "60000", "PZL_UNIQUE_BUDGET": UB}
t=time.time()
rows = pb.run_arm(build, pzl, "search", "1", out, env)
r = rows[-1] if rows else {}
keep = {k: v for k, v in r.items() if k in ("status","acceptance","acceptance_failed","line","solver_actions","search_replays","opponent_choice_points","opponent_branches_beaten","search_drift","search_exhausted","pzl_sha256","duration_ms","turns") or k.startswith("uniqueness")}
keep.update(puzzle=ID, opponent=OPP, wall_s=round(time.time()-t), overlay=build.overlay_sha)
print(json.dumps(keep))
for f in out.iterdir():
    if f.name not in ("puzzle-results.jsonl","jvm.log"):
        shutil.rmtree(f, ignore_errors=True) if f.is_dir() else f.unlink()
