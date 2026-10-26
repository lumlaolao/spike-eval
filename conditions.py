"""Kept games and solves per recorded condition (root, game cap, call deadline, token cap) for chosen.json.
Run from the repository root: python3 <this branch>/conditions.py chosen.json"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

chosen = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "chosen.json"))
games, solved = Counter(), Counter()
for v in chosen.values():
    if v.get("root") == "stopped":
        key = ("stopped",)
    else:
        rows = Path(v["rows"].split("/runs/puzzles/", 1)[-1].join(["runs/puzzles/", ""]))
        m = re.search(r"partial(\d+)", rows.name)
        manifest = rows.parent / ("batch-manifest.partial%s.json" % m.group(1) if m else "batch-manifest.json")
        r = json.loads(manifest.read_text()).get("record") or {}
        key = (v["root"], r.get("game_cap_ms"), r.get("call_timeout_ms"), "own max" if r.get("max_tokens") else None)
    games[key] += 1
    solved[key] += v["status"] == "solved"
for key in sorted(games, key=str):
    print(key, "%d of %d solved" % (solved[key], games[key]))
