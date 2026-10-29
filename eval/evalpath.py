"""Puts every folder of eval scripts on sys.path, so any script imports another by its module name.

The scripts live in eval/ and its family folders (demo/, harness_env/, puzzles/) and import across
them (run_batch imports puzzle_format; puzzle_watch imports match_review). A script outside eval/
first puts eval/ on sys.path, then imports this module.
"""
import sys
from pathlib import Path

EVAL = Path(__file__).resolve().parent
REPO = EVAL.parent
DIRS = (EVAL, EVAL / "demo", EVAL / "harness_env", EVAL / "puzzles")

for _d in reversed(DIRS):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))
