# Paths: the repository is four levels up; BUILD is the acceptance OUT dir (`--build` of puzzle_baselines.py);
# raw runs go under RUNS (default ./raw). Run from this folder.
import sys, json, os, shutil
from pathlib import Path
REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / 'eval/puzzles')); sys.path.insert(0, str(REPO / 'eval'))
import puzzle_baselines as pb
BUILD, RUNS = os.environ['BUILD'], Path(os.environ.get('RUNS', 'raw'))
ID=sys.argv[1]
S=json.loads(open('res-search-%s.json'%ID).read().strip().splitlines()[-1])
A=json.loads(open('res-ai-%s.json'%ID).read().strip().splitlines()[-1])
line=S['line']; solver=' '.join(s for s in line.split() if s.startswith('Solver'))
build=pb.Build(BUILD); pzl=REPO/'eval/puzzles/rules-pilot/%s.pzl'%ID
res={}
for cs in (0,1,2):
    out=RUNS/('%s-probe%d'%(ID,cs))
    rows=pb.run_arm(build,pzl,"search","1",out,pb.probe_env(solver,cs,"ai"))
    res[cs]=pb.probe_verdict(rows[-1] if rows else None)
    shutil.rmtree(out,ignore_errors=True)
print(json.dumps(dict(puzzle=ID,search_line=line,search_status=S['status'],search_acceptance=S.get('acceptance'),
  opp_steps=sum(1 for s in line.split() if s.startswith('Opponent')),solver_only_line=solver,
  ai_main_line=A['line'],same_solver_as_ai_main=(solver==A['line']),probe_ai_verdicts=res)))
