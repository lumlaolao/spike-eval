"""Per-condition tool use and time for a run dir with off/ and on/ (what puzzle_score.py does not report).

    python3 eval/puzzles/rules-pilot/benchmark-v1/tool_use.py runs/puzzles/bench-v1-e67 > tool-use-e67.txt
"""
import collections, glob, json, os, re, sys, statistics
root = sys.argv[1] if len(sys.argv) > 1 else "runs/puzzles/bench-v1"
for cond in ("off", "on"):
    tools, per_puzzle, secs, solved, n = collections.Counter(), {}, [], 0, 0
    lookup_attempts = 0
    for d in sorted(glob.glob(os.path.join(root, cond, "*"))):
        pid = os.path.basename(d); c = k = 0
        for r in (json.loads(l) for f in glob.glob(d + "/puzzle-results.jsonl") for l in open(f)):
            k += 1; c += bool(r.get("solved"))
        games = sorted(glob.glob(d + "/game-*.jsonl"))
        for g in games:
            used = False; t0 = t1 = None
            for l in open(g):
                r = json.loads(l)
                if r.get("t") == "context_tool":
                    m = re.match(r"TOOL CALL (\w+)", str(r.get("request") or ""))
                    name = m.group(1) if m else "?"
                    if name not in ("answer", "set_plan"): tools[name] += 1; used |= name == "rules_lookup"
                if r.get("t") == "context_call":
                    secs.append(r.get("elapsed_ms", 0) / 1000.0)
            lookup_attempts += used
        per_puzzle[pid] = (c, k); solved += c; n += k
    print("== %s: %d/%d attempts solved (%.0f%%), %d puzzles" % (cond, solved, n, 100.0 * solved / max(n, 1), len(per_puzzle)))
    print("   lookups:", dict(tools.most_common()), "| attempts that called rules_lookup:", lookup_attempts)
    if secs: print("   model time per call: median %.1fs, total %.0f min" % (statistics.median(secs), sum(secs) / 60))
    globals()[cond] = per_puzzle
if "off" in globals() and "on" in globals():
    diff = [(p, off[p], on.get(p)) for p in sorted(off) if on.get(p) and off[p][0] != on[p][0]]
    print("== puzzles where the conditions differ (off c/k -> on c/k):")
    for p, a, b in diff: print("   %-14s %d/%d -> %d/%d" % (p, a[0], a[1], b[0], b[1]))
