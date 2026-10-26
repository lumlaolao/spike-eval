#!/usr/bin/env python3
"""Watch a running or finished eval/puzzle_solve.py --out directory: what each puzzle has decided, spent and flagged.

    python3 eval/puzzle_watch.py runs/puzzles/luna-open                  # one table, then a totals line
    python3 eval/puzzle_watch.py runs/puzzles/luna-open --follow         # one line per event until the run is over

Per puzzle: state (waiting / running / done / not started, when the run ended first, e.g. on its budget), attempts
solved, decisions by tier, text parses, cost so far, the last decision and its answer, elapsed time, and these flags:
  ai               a decision Forge's AI made (a decision row with tier ai, or ai>0 in a [TALLY] line)
  <gauntlet line>  each kind of line eval/gauntlet_scan.py fails on (FALLBACK->AI, (unparsed, TEXT-PARSE,
                   TEXT-FORMAT-LEAK, Couldn't add to stack, failed to target, FAILED to pay, AI looped), and EXCEPTION
                   for its Exception / Error / threw lines (stack frames not counted)
  NO-ANSWER        a model reply that was no answer (defect_analyzer's non-answer and no-reply lines, [JEV-NO-ANSWER],
                   [CLAUDE-CLI-NO-ANSWER])
  LOST, TAKEN      a decision whose answer was lost, or that the harness took (match_review.classify_auto)
  TOOL-REFUSED, TOOL-FAILED   a lookup eval/tool_audit.py classes so (a --memory-tools run)
  BLOCK-INVALID    a block set left illegal (defect_analyzer: no legal set was found)
  CAP-OUTLIVED     the game thread outlived the cap, so the JVM played no further attempts
  SHORT            a done puzzle with fewer than k result rows: puzzle_score refuses it, so its score is lost
  UNSCOREABLE      a result row puzzle_score refuses (the goal was not enforced)
  PRICE-CEILING    a result row with decisions lost under --max-price-at-list's ceiling: not scored, replayed on --resume
  STALLED          a running puzzle whose files have not grown for --stall minutes (possibly stuck)

Cost: the provider's own cost of each call (`cost_usd` on the context_call rows, written for a jev: seat), or
eval/cost_report.py's pricing of the rows at each model's dearest endpoint, whichever is higher, so it is never below
the figure puzzle_solve.py checks --budget-usd against (an openrouter: seat reports no cost on its rows).

--follow exits 0 when the run is over (puzzle_solve.py wrote `finished` to puzzle-run.json, or, for a run made before
that file, every puzzle directory is done), 4 at once when puzzle_solve.py wrote that it was stopped (an exception or
an interrupt), and 3 when nothing in the run has grown for --stall minutes while it is not over (a dead or hung run).
It reads only: it never writes into the run directory and calls no network.

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
import gauntlet_scan  # noqa: E402
import match_review  # noqa: E402
import puzzle_score  # noqa: E402
import puzzle_solve  # noqa: E402
import tool_audit  # noqa: E402

NO_ANSWER = (defect_analyzer._NON_ANSWER_RE, defect_analyzer._BRIDGE_NO_REPLY_RE, re.compile(r"\[(?:JEV|CLAUDE-CLI)-NO-ANSWER\]"))
CAP_OUTLIVED = "outlived the cap"
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


def game_files(d):
    """One game-*.jsonl per attempt, oldest first."""
    return sorted(glob.glob(os.path.join(d, "game-*.jsonl")), key=os.path.getmtime)


def provider_cost(paths):
    """The provider's own cost summed over every context_call row that reports one."""
    usd = 0.0
    for path in paths:
        for line in log_lines(path):
            if '"cost_usd"' in line:
                try:
                    usd += json.loads(line).get("cost_usd") or 0.0
                except ValueError:
                    pass
    return usd


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


def flags_of(lines, rows, tools):
    """{kind: (count, the latest line that shows it)} for each flag in the module docstring read from the files."""
    found = collections.OrderedDict()

    def add(kind, items, example=lambda x: x):
        if items:
            found[kind] = (len(items), example(items[-1])[:160])
    tallies = [l for l in lines if "[TALLY]" in l]
    ai_tally = max([int(m.group(1)) for m in (re.search(r"\bai=(\d+)", l) for l in tallies) if m] or [0])
    ai_rows = [r for r in rows if r.get("tier") == "ai"]
    if ai_rows or ai_tally:
        found["ai"] = (max(len(ai_rows), ai_tally), tallies[-1] if ai_tally else "a decision row with tier ai")
    bad = collections.OrderedDict()
    for l in lines:
        m = gauntlet_scan.BAD.search(l)
        if m and "JAVA_TOOL" not in l and not l.lstrip().startswith("at "):
            kind = "EXCEPTION" if m.group(0) in ("Exception", "Error", "threw") else m.group(0)
            bad.setdefault(kind, []).append(l)
    for kind, ls in bad.items():
        add(kind, ls)
    add("NO-ANSWER", [l for l in lines if any(r.search(l) for r in NO_ANSWER)])
    auto = [(r, match_review.classify_auto(r)) for r in rows if r.get("tier") == "auto"]
    decision = lambda r: "%s decision: %s" % (r.get("type"), r.get("fallback") or "answer lost")  # noqa: E731
    add("LOST", [r for r in rows if r.get("answer_via") == "lost"] + [r for r, c in auto if c == "LOST"], decision)
    add("TAKEN", [r for r, c in auto if c == "TAKEN"], decision)
    for kind in ("REFUSED", "FAILED"):
        n = tools["by_outcome"][kind]
        if n:
            found["TOOL-" + kind] = (n, tools["examples"].get(kind, "")[:160])
    add("BLOCK-INVALID", [l for l in lines if defect_analyzer._ILLEGAL_BLOCKS_RE.match(l)])
    add("CAP-OUTLIVED", [l for l in lines if CAP_OUTLIVED in l])
    return found


def activity(d):
    """When anything in the puzzle's directory last grew (the JVM log or a game file)."""
    times = [os.path.getmtime(p) for p in glob.glob(os.path.join(d, "*")) if os.path.isfile(p)]
    return max(times) if times else None


def puzzle(d, k, finished, now, stall_s, prices):
    log = os.path.join(d, "jvm.log")
    lines = log_lines(log)
    files = game_files(d)
    rows = [r for f in files for r in defect_analyzer.parse_jsonl_file(f)[0]]
    res = results(d)
    last = activity(d)
    manifest = os.path.join(d, "batch-manifest.json")
    start = os.path.getmtime(manifest) if os.path.isfile(manifest) else last
    tail = next((l for l in reversed(lines) if l.strip()), "")
    # Build.run opens jvm.log as the JVM starts, and a resume renames it (set_aside_partial) until the replay starts.
    if not os.path.isfile(log):
        state = "not started" if finished else "waiting"
    elif finished or (k and len(res) >= k):
        state = "done"
    elif not k and res and tail.startswith("PZL_RESULT") and now - last >= QUIET_DONE_S:
        state = "done"
    else:
        state = "running"
    flags = flags_of(lines, rows, tool_audit.audit(files))
    if state == "running" and last and now - last > stall_s:
        flags["STALLED"] = (1, "no new log line for %d min" % ((now - last) // 60))
    if state == "done" and k and len(res) < k:
        flags["SHORT"] = (k - len(res), "%d of %d attempts: puzzle_score refuses the puzzle" % (len(res), k))
    solved = 0
    refused = [r for r in res if r.get("price_ceiling_refusals")]   # decisions lost to the ceiling: replayed on --resume
    if refused:
        flags["PRICE-CEILING"] = (len(refused), "%d decisions lost under the price ceiling"
                                  % sum(r["price_ceiling_refusals"] for r in refused))
    for r in res:
        if r.get("price_ceiling_refusals"):
            continue
        try:
            solved += 1 if puzzle_score.attempt_success(r)[0] else 0
        except puzzle_score.Refused as e:  # a goal the engine did not enforce: a harness defect, not a result
            flags["UNSCOREABLE"] = (flags.get("UNSCOREABLE", (0, ""))[0] + 1, str(e)[:160])
    tiers = collections.Counter(r.get("tier") for r in rows)
    return {"id": os.path.basename(d), "state": state, "attempts": len(res), "solved": solved,
            "tiers": {t: tiers.get(t, 0) for t in TIERS}, "text_parses": flags.get("TEXT-PARSE", (0,))[0],
            "cost": max(provider_cost(files), cost_report.report_dir(d, prices)["usd"]),
            "rows": rows, "flags": flags, "last": last,
            "elapsed": ((last if state == "done" else now) - start) if start else 0}


def snapshot(out, stall_s, now=None):
    now = time.time() if now is None else now
    run = read_json(os.path.join(out, puzzle_solve.RUN_FILE)) or {}
    prices = cost_report.budget_prices(cost_report.load_prices())
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
    if run.get("stopped"):
        line += "; puzzle_solve.py STOPPED: %s" % run["stopped"]
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
    """The lines for what changed between two snapshots: each new decision, each flag that grew, the cost when it
    moved, each puzzle done. A decision line carries no cost: one read late would show a later total."""
    was = {p["id"]: p for p in (before or {"puzzles": []})["puzzles"]}
    stamp = time.strftime("%H:%M:%S", time.localtime(snap["now"]))
    lines = []
    for p in snap["puzzles"]:
        old = was.get(p["id"])
        seen = len(old["rows"]) if old and len(old["rows"]) <= len(p["rows"]) else 0
        for i, r in enumerate(p["rows"][seen:], seen + 1):
            lines.append("%s %s decision %d %s %s -> %s" % (
                stamp, p["id"], i, r.get("tier"), r.get("type"), answer_of(r)[:80]))
        for k, (n, example) in p["flags"].items():
            if not old or old["flags"].get(k, (0, ""))[0] < n:
                lines.append("%s %s FLAG %s x%d: %s" % (stamp, p["id"], k, n, example))
        if p["state"] == "done" and (not old or old["state"] != "done"):
            lines.append("%s %s DONE %s: solved %d/%d attempts, $%.4f" % (
                stamp, p["id"], "solved" if p["solved"] else "failed", p["solved"], p["attempts"], p["cost"]))
        elif old and abs(p["cost"] - old["cost"]) > 1e-12:
            lines.append("%s %s cost $%.4f" % (stamp, p["id"], p["cost"]))
    return lines


def over(snap):
    """The run is over: puzzle_solve.py said so, or (a run without its file) every puzzle directory is done."""
    if "finished" in snap["run"]:
        return bool(snap["run"]["finished"])
    return bool(snap["puzzles"]) and all(p["state"] == "done" for p in snap["puzzles"])


def dead_for(out, snap, since):
    """Seconds since anything in the run grew (since the watcher started, for a run with nothing in it yet), or None
    while it is over."""
    if over(snap):
        return None
    times = [p["last"] for p in snap["puzzles"] if p["last"]]
    run_file = os.path.join(out, puzzle_solve.RUN_FILE)
    if os.path.isfile(run_file):
        times.append(os.path.getmtime(run_file))
    return snap["now"] - max(times or [since])


def follow(out, interval, stall_s, emit=print, sleep=time.sleep):
    since = time.time()
    before = None
    while True:
        snap = snapshot(out, stall_s)
        for line in events(before, snap):
            emit(line)
        before = snap
        if snap["run"].get("stopped"):
            emit("[watch] STOPPED: " + totals(snap))
            return 4
        if over(snap):
            emit("[watch] run over: " + totals(snap))
            return 0
        quiet = dead_for(out, snap, since)
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
