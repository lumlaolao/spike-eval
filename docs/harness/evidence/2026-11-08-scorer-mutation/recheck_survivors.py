#!/usr/bin/env python3
"""Add `killed_by_other` to the survivors of a `mutate_scorers.py --json` run, without rerunning the killed mutants.

    python3 recheck_survivors.py <run.json> <tree> <out.json>

For each file, the other test files that name the module and pass unmutated are run (twice, as the tool does) against
each surviving mutant of `<tree>` (the tree the run mutated); the ones that fail list under `killed_by_other`. The
same calls `scripts/mutate_scorers.py --recheck` makes, applied to the survivors only (the full `--recheck` run is
about twice as long).
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))
import mutate_scorers as m  # noqa: E402

src, tree, out = sys.argv[1:4]
run = json.load(open(src))
root = m.make_copy(tempfile.gettempdir(), Path(tree))
try:
    for result in run["results"]:
        target = result["file"]
        path = root / target
        original = path.read_text()
        others = [o for o in m.other_tests(root, target) if m.run_confirmed(root, o, 60)[0] == m.SURVIVED]
        try:
            for row in result["rows"]:
                if row["status"] != m.SURVIVED:
                    continue
                path.write_text(m.mutant_source(original, row["site"])[0])
                row["killed_by_other"] = [str(o) for o in others if m.run_confirmed(root, o, 60)[0] == m.KILLED]
                print(target, row["line"], row["killed_by_other"], flush=True)
        finally:
            path.write_text(original)
finally:
    shutil.rmtree(root, ignore_errors=True)
json.dump(run, open(out, "w"), indent=1)
