#!/usr/bin/env python3
"""Count, over recorded games, two harness statements the seat acts on (#398, #399).

    python3 eval/fact_citations.py <runs dir> [...]       # JSON counts on stdout

1. The FACTS clock after a 1-turn puzzle's combat (#398). A game is counted when it has a turn-1 decision in MAIN2 or
   END_OF_TURN. It is grouped by the solver's clause in those decisions' prompts: `lethal` (some prompt reads
   `your next combat: ... = LETHAL`), `clock` (a clock that is never lethal) or `none` (no clock). It "cites a later
   combat" when one of those decisions' `raw_reply` matches CITES_LATER_COMBAT.
2. Yield-passed windows in the seat's own main phase (#399): `t: window` rows whose reason starts `yield:`, with
   `active` true, phase MAIN1 or MAIN2, something on the stack and something castable.

Descriptive only: the counts pool every model and epoch found under the given directories (docs/STANDARDS.md
section 8), and a regex over replies is a lower bound on what a seat relied on.
"""
import glob
import json
import os
import re
import sys

LETHAL_CLOCK = re.compile(r"your next combat: [^|]*= LETHAL")
CITES_LATER_COMBAT = re.compile(
    r"next combat|next attack|next turn|combat estimate|attack for lethal|lethal (in|next) combat"
    r"|future turn|following turn", re.I)
LATE_PHASES = ("MAIN2", "END_OF_TURN")


def late_decisions(rows):
    """The turn-1 decisions after combat that carry a prompt."""
    return [r for r in rows if r.get("t") == "decision" and r.get("prompt")
            and r.get("turn") == 1 and r.get("phase") in LATE_PHASES]


def clock_group(decisions):
    """`lethal`, `clock` or `none`, from the solver's FACTS clause in these decisions."""
    if any(LETHAL_CLOCK.search(r["prompt"]) for r in decisions):
        return "lethal"
    if any("your next combat:" in r["prompt"] for r in decisions):
        return "clock"
    return "none"


def cites_later_combat(decisions):
    return any(CITES_LATER_COMBAT.search(str(r.get("raw_reply") or "")) for r in decisions)


def is_own_main_yield_skip(row):
    return (row.get("t") == "window" and str(row.get("reason") or "").startswith("yield:") and bool(row.get("active"))
            and row.get("phase") in ("MAIN1", "MAIN2") and (row.get("stack") or 0) > 0
            and (row.get("castable") or 0) > 0)


def read_rows(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def scan(roots):
    """Counts over every game-*.jsonl under the roots."""
    clock = {g: {"games": 0, "cite": 0} for g in ("lethal", "clock", "none")}
    out = {"games": 0, "games_with_late_decision": 0, "clock": clock, "yield_windows": 0, "yield_games": 0}
    seen = set()
    for root in roots:
        for path in glob.glob(os.path.join(root, "**", "game-*.jsonl"), recursive=True):
            real = os.path.realpath(path)
            if real in seen:
                continue
            seen.add(real)
            rows = read_rows(path)
            if not any(r.get("t") == "decision" and r.get("prompt") for r in rows):
                continue
            out["games"] += 1
            skips = sum(is_own_main_yield_skip(r) for r in rows)
            out["yield_windows"] += skips
            out["yield_games"] += skips > 0
            late = late_decisions(rows)
            if not late:
                continue
            out["games_with_late_decision"] += 1
            group = clock[clock_group(late)]
            group["games"] += 1
            group["cite"] += cites_later_combat(late)
    return out


def main(argv):
    if not argv:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    print(json.dumps(scan(argv), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
