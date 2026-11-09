"""One puzzle's prover run for the AI-blind sweep: `drive.py <id> <ai|search> <unique budget>` prints one JSON row.

Runs puzzle_baselines.run_arm with the prover's environment and the opponent named, keeps the row fields the README
cites, and deletes the raw run's files except puzzle-results.jsonl and jvm.log. It cannot say why a second line is
accepted; the README does that per puzzle.
Paths: the repository is four levels up; BUILD is the acceptance OUT dir (`--build` of puzzle_baselines.py); raw
runs go under RUNS (default ./raw). Run from this folder.
"""
import sys, json, time, os, shutil
from pathlib import Path
REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / 'eval/puzzles')); sys.path.insert(0, str(REPO / 'eval'))
import puzzle_baselines as pb
BUILD, RUNS = os.environ['BUILD'], Path(os.environ.get('RUNS', 'raw'))
ID, OPP, UB = sys.argv[1], sys.argv[2], sys.argv[3]
build = pb.Build(BUILD)
pzl = REPO / 'eval/puzzles/rules-pilot/%s.pzl' % ID
out = RUNS / ('%s-%s' % (ID, OPP))
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
