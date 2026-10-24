#!/usr/bin/env python3
"""Watch a running or finished eval/puzzle_solve.py --out directory: what each puzzle has decided, spent and flagged.

    python3 eval/puzzle_watch.py runs/puzzles/luna-open                  # one table, then a totals line
    python3 eval/puzzle_watch.py runs/puzzles/luna-open --follow         # one line per event until the run is over

Per puzzle: state (waiting / running / done / not started, when the run ended first, e.g. on its budget), attempts
solved, decisions by tier, text parses, cost so far, the last decision and its answer, elapsed time, and flags for
what acceptance treats as a defect: a decision Forge's AI made (ai>0), a [TEXT-PARSE], a lost answer, an exception,
a block set left illegal ([BLOCK-INVALID] ... no legal set), a result row the scorer refuses (unscoreable), and a
running puzzle whose files have not grown for --stall minutes (possibly stuck).

Cost: a jev: seat's bridge logs the provider's own running cost on every answer (DecisionsBridge, "cost so far $x"),
and that is summed. An openrouter: seat logs no cost line ("replied in N ms"), so its rows are priced by
eval/cost_report.py, the estimate puzzle_solve.py checks the budget against (at the list price, not the dearest).

--follow exits 0 when the run is over (puzzle_solve.py wrote `finished` to puzzle-run.json, or, for a run made before
that file, every puzzle directory is done), and 3 when nothing in the run has grown for --stall minutes while it is
not over (a dead or hung run). It reads only: it never writes into the run directory and calls no network.

What it cannot see: a JVM killed before it wrote anything, a call that left no row, and (for a run without
puzzle-run.json) the puzzles not started yet or how many attempts each will make; such a puzzle is called done when
its log ends on a result line and has been quiet for a minute.
"""
import argparse
import collections
import glob
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cost_report  # noqa: E402
import defect_analyzer  # noqa: E402
import demo_events  # noqa: E402
import match_review  # noqa: E402
import puzzle_score  # noqa: E402
import puzzle_solve  # noqa: E402

JEV_COST = re.compile(r"^\[JEV ([^\]]+)\] \S+ \S+ -> answer .*cost so far \$([0-9.]+)\)\s*$")
EXCEPTION = re.compile(r"Exception|Error\b|threw")
NO_ANSWER = re.compile(r"\[JEV-NO-ANSWER\]|^\[OPENROUTER [^\]]+\] non-answer")
# A log of a run without puzzle-run.json is done when it ends on its last result line and stays quiet this long.
QUIET_DONE_S = 60
TIERS = ("smart", "fast", "auto", "ai")


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def log_lines(path):
    try:
        with open(path, errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return []


def decision_rows(d):
    """The decision rows of every attempt, in file order (one game-*.jsonl per seed); a half-written line is skipped."""
    rows = []
    for path in sorted(glob.glob(os.path.join(d, "game-*.jsonl")), key=os.path.getmtime):
        for line in log_lines(path):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("t") == "decision":
                rows.append(r)
    return rows


def results(d):
    """The result rows so far; a line still being written is left for the next look."""
    rows = []
    for line in log_lines(os.path.join(d, "puzzle-results.jsonl")):
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def answer_of(row):
    """What the decision chose, in the option labels the seat read."""
    if (row.get("n_options") or 0) < 0:
        return str(row.get("choice"))
    options = row.get("options") or []
    chosen = [str(options[i]) for i in demo_events.chosen_indexes(row) if i < len(options)]
    return ", ".join(chosen) if chosen else "PASS / none"


def jev_cost(lines):
    """The provider's cost summed over each bridge (label) of the log. A bridge's figure is its own running total, and
    each attempt builds new bridges, so a figure lower than the one before it starts a new total."""
    done, now = collections.Counter(), {}
    for line in lines:
        m = JEV_COST.match(line)
        if m:
            label, usd = m.group(1), float(m.group(2))
            if usd < now.get(label, 0.0):
                done[label] += now[label]
            now[label] = usd
    return sum(done.values()) + sum(now.values()) if now else None


def flags_of(lines, rows):
    """{kind: (count, the latest line that shows it)} for every defect acceptance fails on."""
    found = collections.OrderedDict()

    def add(kind, n, example):
        if n:
            found[kind] = (n, example[:160])
    ai_rows = [r for r in rows if r.get("tier") == "ai"]
    tallies = [l for l in lines if "[TALLY]" in l]
    ai_tally = max([int(m.group(1)) for m in (re.search(r"\bai=(\d+)", l) for l in tallies) if m] or [0])
    add("ai", max(len(ai_rows), ai_tally), tallies[-1] if ai_tally else "a decision row with tier ai")
    text = [l for l in lines if "[TEXT-PARSE]" in l]
    add("TEXT-PARSE", len(text), text[-1] if text else "")
    lost = [r for r in rows if r.get("answer_via") == "lost"
            or (r.get("tier") == "auto" and match_review.classify_auto(r) == "LOST")]
    add("LOST", len(lost), "%s decision: %s" % (lost[-1].get("type"), lost[-1].get("fallback") or "answer lost")
        if lost else "")
    no_answer = [l for l in lines if NO_ANSWER.search(l)]
    add("NO-ANSWER", len(no_answer), no_answer[-1] if no_answer else "")
    exc = [l for l in lines if EXCEPTION.search(l) and "JAVA_TOOL" not in l and not l.lstrip().startswith("at ")]
    add("EXCEPTION", len(exc), exc[-1] if exc else "")
    blocks = [l for l in lines if defect_analyzer._ILLEGAL_BLOCKS_RE.match(l)]
    add("BLOCK-INVALID", len(blocks), blocks[-1] if blocks else "")
    return found


def activity(d):
    """When anything in the puzzle's directory last grew (the JVM log or a game file)."""
    times = [os.path.getmtime(p) for p in glob.glob(os.path.join(d, "*")) if os.path.isfile(p)]
    return max(times) if times else None


def puzzle(d, k, finished, now, stall_s, prices):
    lines = log_lines(os.path.join(d, "jvm.log"))
    rows = decision_rows(d)
    res = results(d)
    last = activity(d)
    manifest = os.path.join(d, "batch-manifest.json")
    start = os.path.getmtime(manifest) if os.path.isfile(manifest) else last
    tail = next((l for l in reversed(lines) if l.strip()), "")
    if not lines and not rows:
        state = "not started" if finished else "waiting"
    elif finished or (k and len(res) >= k) or "outlived the cap" in tail:
        state = "done"
    elif not k and res and tail.startswith("PZL_RESULT") and now - last >= QUIET_DONE_S:
        state = "done"
    else:
        state = "running"
    flags = flags_of(lines, rows)
    if state == "running" and last and now - last > stall_s:
        flags["STALLED"] = (1, "no new log line for %d min" % ((now - last) // 60))
    cost = jev_cost(lines)
    if cost is None:
        cost = cost_report.report_dir(d, prices)["usd"]
    tiers = collections.Counter(r.get("tier") for r in rows)
    text_parses = sum(1 for l in lines if "[TEXT-PARSE]" in l)
    solved = 0
    for r in res:
        try:
            solved += 1 if puzzle_score.attempt_success(r)[0] else 0
        except puzzle_score.Refused as e:  # a goal the engine did not enforce: a harness defect, not a result
            flags["UNSCOREABLE"] = (flags.get("UNSCOREABLE", (0, ""))[0] + 1, str(e)[:160])
    return {"id": os.path.basename(d), "state": state, "attempts": len(res), "solved": solved,
            "tiers": {t: tiers.get(t, 0) for t in TIERS}, "text_parses": text_parses, "cost": cost,
            "rows": rows, "flags": flags, "last": last,
            "elapsed": ((last if state == "done" else now) - start) if start else 0}


def snapshot(out, stall_s, now=None):
    now = time.time() if now is None else now
    run = read_json(os.path.join(out, puzzle_solve.RUN_FILE)) or {}
    prices = cost_report.load_prices()
    dirs = sorted(p for p in glob.glob(os.path.join(out, "*")) if os.path.isdir(p))
    ids = [os.path.basename(p) for p in dirs]
    ids += [i for i in run.get("ids") or [] if i not in ids]
    puzzles = [puzzle(os.path.join(out, i), run.get("k"), bool(run.get("finished")), now, stall_s, prices)
               for i in ids]
    return {"run": run, "puzzles": puzzles, "now": now}


def mmss(s):
    return "%d:%02d" % (s // 60, s % 60)


def last_decision(p):
    if not p["rows"]:
        return "-"
    r = p["rows"][-1]
    return "%s -> %s" % (r.get("type"), answer_of(r))


def totals(snap):
    ps = snap["puzzles"]
    run = snap["run"]
    cost = sum(p["cost"] for p in ps)
    budget = run.get("budget_usd")
    line = "%d/%d puzzles done, %d solved (attempts %d/%d), cost $%.4f" % (
        sum(1 for p in ps if p["state"] == "done"), len(ps), sum(1 for p in ps if p["solved"]),
        sum(p["solved"] for p in ps), sum(p["attempts"] for p in ps), cost)
    line += (" of the $%.2f budget" % budget) if budget is not None else " (no budget recorded: no puzzle-run.json)"
    flagged = sum(1 for p in ps if p["flags"])
    return line + ("; %d puzzle(s) flagged" % flagged if flagged else "; no flags")


def table(snap):
    head = "%-28s %-8s %-7s %-17s %-5s %-10s %-7s %-34s %s" % (
        "puzzle", "state", "solved", "smart/fast/auto/ai", "text", "cost", "elapsed", "last decision", "flags")
    out = [head]
    for p in snap["puzzles"]:
        solved = "%d/%d" % (p["solved"], p["attempts"]) if p["attempts"] else "-"
        tiers = "/".join(str(p["tiers"][t]) for t in TIERS)
        flags = " ".join("%s x%d" % (k, n) if k != "STALLED" else "STALLED?" for k, (n, _) in p["flags"].items())
        out.append("%-28s %-8s %-7s %-17s %-5d $%-9.4f %-7s %-34s %s" % (
            p["id"][:28], p["state"], solved, tiers, p["text_parses"], p["cost"], mmss(p["elapsed"]),
            last_decision(p)[:34], flags or "-"))
    for p in snap["puzzles"]:
        for k, (n, example) in p["flags"].items():
            out.append("  %s %s: %s" % (p["id"], k, example))
    out.append(totals(snap))
    return "\n".join(out)


def events(before, snap):
    """The lines for what changed between two snapshots: each new decision, each flag that grew, each puzzle done."""
    was = {p["id"]: p for p in (before or {"puzzles": []})["puzzles"]}
    stamp = time.strftime("%H:%M:%S", time.localtime(snap["now"]))
    lines = []
    for p in snap["puzzles"]:
        old = was.get(p["id"])
        seen = len(old["rows"]) if old and len(old["rows"]) <= len(p["rows"]) else 0
        for i, r in enumerate(p["rows"][seen:], seen + 1):
            lines.append("%s %s decision %d %s %s -> %s  [cost $%.4f]" % (
                stamp, p["id"], i, r.get("tier"), r.get("type"), answer_of(r)[:80], p["cost"]))
        for k, (n, example) in p["flags"].items():
            if not old or old["flags"].get(k, (0, ""))[0] < n:
                lines.append("%s %s FLAG %s x%d: %s" % (stamp, p["id"], k, n, example))
        if p["state"] == "done" and (not old or old["state"] != "done"):
            lines.append("%s %s DONE %s: solved %d/%d attempts, $%.4f" % (
                stamp, p["id"], "solved" if p["solved"] else "failed", p["solved"], p["attempts"], p["cost"]))
    return lines


def over(snap):
    """The run is over: puzzle_solve.py said so, or (a run without its file) every puzzle directory is done."""
    if "finished" in snap["run"]:
        return bool(snap["run"]["finished"])
    return bool(snap["puzzles"]) and all(p["state"] == "done" for p in snap["puzzles"])


def dead_for(out, snap):
    """Seconds since anything in the run grew, or None while it is over."""
    if over(snap):
        return None
    times = [p["last"] for p in snap["puzzles"] if p["last"]]
    run_file = os.path.join(out, puzzle_solve.RUN_FILE)
    if os.path.isfile(run_file):
        times.append(os.path.getmtime(run_file))
    return snap["now"] - max(times) if times else None


def follow(out, interval, stall_s, emit=print, sleep=time.sleep):
    before = None
    while True:
        snap = snapshot(out, stall_s)
        for line in events(before, snap):
            emit(line)
        before = snap
        if over(snap):
            emit("[watch] run over: " + totals(snap))
            return 0
        quiet = dead_for(out, snap)
        if quiet is not None and quiet > stall_s:
            emit("[watch] DEAD? nothing in the run has grown for %d min and it is not over: %s"
                 % (quiet // 60, totals(snap)))
            return 3
        sleep(interval)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", help="a puzzle_solve.py --out directory")
    ap.add_argument("--follow", action="store_true", help="stream one line per event until the run is over")
    ap.add_argument("--interval", type=float, default=10.0, help="seconds between looks (--follow)")
    ap.add_argument("--stall", type=float, default=10.0, help="minutes without a new log line that flag a puzzle "
                                                              "(and, with --follow, the whole run) as stuck")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.out):
        raise SystemExit("not a directory: %s" % a.out)
    if a.follow:
        return follow(a.out, a.interval, a.stall * 60, emit=lambda l: print(l, flush=True))
    print(table(snapshot(a.out, a.stall * 60)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
