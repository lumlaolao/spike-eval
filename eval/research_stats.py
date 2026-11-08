#!/usr/bin/env python3
"""The two descriptive statistics of the 2026-11-06 research notes that read board-model rows.

    python3 eval/research_stats.py menu <board.jsonl> [...]            # menu-position-at-regret-steps
    python3 eval/research_stats.py loss-awareness <board.jsonl> [...]  # loss-awareness-regex-fails

Input: the rows `eval/puzzles/step_regret_board.py` / `board_drain.py` write (git-ignored under `runs/`), each naming
its `source_game`. A row whose game file is not on this machine is skipped and counted. Descriptive only (no model, no
JVM): a count over recorded attempts, pooled over whatever models are in the rows (docs/STANDARDS.md section 6).
"""
import json
import os
import re
import sys

from fact_citations import read_rows

CLAIMS_WIN = re.compile(
    r"lethal|finish (them|the opponent)|win (this|the game)|enough (damage|drain|to (kill|win))|exactly enough"
    r"|reach(es)? (0|zero)|deal(s)? the (final|remaining)|for the win", re.I)
CONCEDES = re.compile(
    r"cannot|can't|no (way|line|remaining)|not enough|out of reach|impossible|short of|unreachable", re.I)
WHY = re.compile(r"WHY:\s*(.*)")


def decisions(game_rows):
    return [r for r in game_rows if r.get("t") == "decision"]


def menu_row(floor, regret_k, decision_rows):
    """The decision row that is the regret step: every floor option on its menu, an integer pick equal to the
    model's label; of several, the one whose index is nearest `regret_k`."""
    found = []
    for i, row in enumerate(decision_rows):
        options = row.get("options") or []
        try:
            pick = int(row.get("choice"))
        except (TypeError, ValueError):
            continue
        if all(o in options for o in floor["options"]) and 0 <= pick < len(options) \
                and options[pick] == floor["model_label"]:
            found.append((abs(i - regret_k), i, row))
    found.sort(key=lambda t: (t[0], t[1]))
    return (found[0][2], len(found)) if found else (None, 0)


def menu_step(floor, row):
    """(menu size, kept positions, losing positions, pick position) for one matched step."""
    options = row["options"]
    kept = [i for i, o in enumerate(options) if floor["options"].get(o) is True]
    lost = [i for i, o in enumerate(options) if floor["options"].get(o) is False]
    return len(options), kept, lost, options.index(floor["model_label"])


def menu_stats(steps):
    """Counts over matched steps; the chance values are the expectation for a uniform pick among the losing options."""
    n = len(steps)
    out = {"steps": n, "earliest_losing": 0, "earliest_losing_chance": 0.0, "before_every_kept": 0,
           "before_every_kept_chance": 0.0, "top_pick": 0, "top_pick_chance": 0.0, "pick_pos": 0.0, "kept_pos": 0.0}
    for size, kept, lost, pick in steps:
        out["earliest_losing"] += pick == min(lost)
        out["earliest_losing_chance"] += 1 / len(lost)
        out["before_every_kept"] += pick < min(kept)
        out["before_every_kept_chance"] += sum(1 for p in lost if p < min(kept)) / len(lost)
        out["top_pick"] += pick == 0
        out["top_pick_chance"] += 1 / size
        out["pick_pos"] += pick / (size - 1)
        out["kept_pos"] += sum(kept) / len(kept) / (size - 1)
    if n:
        out["pick_pos"] /= n
        out["kept_pos"] /= n
    return {k: round(v, 2) if isinstance(v, float) else v for k, v in out.items()}


def menu(board_rows):
    steps, skipped, unmatched, multi = [], 0, 0, 0
    for r in board_rows:
        if not r.get("floor"):
            continue
        if not os.path.exists(r["source_game"]):
            skipped += 1
            continue
        row, n = menu_row(r["floor"], r["regret_k"], decisions(read_rows(r["source_game"])))
        if row is None:
            unmatched += 1
            continue
        multi += n > 1
        steps.append(menu_step(r["floor"], row))
    return dict(menu_stats(steps), game_missing=skipped, unmatched=unmatched, several_rows=multi)


def loss_awareness(board_rows):
    """Per position (joined to the k-th non-auto decision row) the WHY sentence against the two patterns; and, per
    failed attempt, whether no WHY after the regret step matches CONCEDES."""
    cells = {True: [0, 0, 0], False: [0, 0, 0]}
    attempts = failed = never = skipped = 0
    for r in board_rows:
        if not r.get("positions"):
            continue
        if not os.path.exists(r["source_game"]):
            skipped += 1
            continue
        attempts += 1
        auto = [x for x in decisions(read_rows(r["source_game"])) if x.get("fallback") in (None, "None")]
        concede_after = False
        for q in r["positions"]:
            if q["k"] >= len(auto):
                continue
            m = WHY.search(str(auto[q["k"]].get("raw_reply") or ""))
            if not m:
                continue
            c = cells[bool(q["winnable"])]
            c[0] += 1
            c[1] += bool(CLAIMS_WIN.search(m.group(1)))
            c[2] += bool(CONCEDES.search(m.group(1)))
            if r.get("regret_k") is not None and q["k"] > r["regret_k"] and CONCEDES.search(m.group(1)):
                concede_after = True
        if r.get("regret_k") is not None:
            failed += 1
            never += not concede_after
    return {"winnable": cells[True], "lost": cells[False], "cell_is": "[replies with a reason, claims a win, concedes]",
            "attempts": attempts, "failed_with_regret_step": failed, "never_concede_after_step": never,
            "game_missing": skipped}


def main(argv):
    if len(argv) < 2 or argv[0] not in ("menu", "loss-awareness"):
        print(__doc__)
        return 2
    for path in argv[1:]:
        rows = read_rows(path)
        fn = menu if argv[0] == "menu" else loss_awareness
        print(path, json.dumps(fn(rows), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
