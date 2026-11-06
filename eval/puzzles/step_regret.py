#!/usr/bin/env python3
"""Step regret (step 2): the decision of a recorded puzzle attempt that lost a winnable position, and its floor.

For one recorded attempt (a `game-<seed>-*.jsonl` written by the model seat, its `puzzle-results.jsonl` row and
`batch-manifest.json` beside it), position K is the game after the seat's first K decision rows. The Java runner
(LlmSpikePuzzle.stepSearch: REPLAY_HANDOVER=K) replays those rows exactly (ReplayBridge) and hands the same seat to the
search arm for the rest of the game, against the recorded opponent (Forge AI) on the recorded seed. Its verdict:
`proven` (a winning line found and replayed), `unwinnable` (every line played, none capped), `not_found` (the budget ran
out: no verdict) or `drift` (the replay was not the recorded game).

Against a deterministic opponent a lost position is never won back, so W(K) only goes from true to false. The scan
runs from the end: the position before each model decision, last first, until one is proven. If the position after
that decision is `unwinnable`, the decision is the regret step; if positions between are `not_found`, the regret step
is only bracketed. At the regret step the floor is the share of its options that keep the win when forced
(REPLAY_FORCE=o): exact when every option is decided, else a bracket [proven/n, (n - unwinnable)/n]. v1 forces only a
play or single-choice decision (a multi-pick, block or order decision is replayed but its floor is not determined).

Zero-model and loopback: nothing leaves the machine. Rows (additive JSONL) go to <out>/step-regret.jsonl:
`t: "step_regret"` per searched position or forced option, `t: "step_regret_summary"` per attempt.

  python3 eval/puzzles/step_regret.py <attempt dir or game-<seed>-*.jsonl> --out runs/step-regret/x --budget 600
"""
import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)

VERSION = "step-regret-v1"
MODEL_TIERS = ("smart", "fast")
DECIDED = ("proven", "unwinnable")


# ------------------------------------------------------------------ reading an attempt

def game_logs(path, seed=None):
    """The attempt's game logs: the file itself, or each `game-<seed>-*.jsonl` of a directory (one seed, if given)."""
    p = Path(path)
    if p.is_file():
        return [p]
    logs = sorted(p.glob("game-*-*.jsonl"))
    if seed is not None:
        logs = [g for g in logs if g.name.startswith("game-%s-" % seed)]
    if not logs:
        raise SystemExit("no game-<seed>-*.jsonl in %s" % path)
    return logs


def seed_of(log):
    m = re.match(r"^game-(-?\d+)-", Path(log).name)
    if not m:
        raise SystemExit("%s: no seed in the name (game-<seed>-*.jsonl)" % log)
    return int(m.group(1))


def decision_rows(log, seat="Solver"):
    """The seat's decision rows in order: what REPLAY_HANDOVER counts."""
    out = []
    for line in Path(log).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("t") == "decision" and r.get("seat") == seat:
            out.append(r)
    return out


def result_row(log):
    """The attempt's puzzle-results.jsonl row for its seed, or None."""
    f = Path(log).parent / "puzzle-results.jsonl"
    if not f.is_file():
        return None
    seed = seed_of(log)
    rows = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = [r for r in rows if str(r.get("seed")) == str(seed)]
    if len(rows) > 1:
        raise SystemExit("%d result rows for seed %s in %s (one is needed)" % (len(rows), seed, f))
    return rows[0] if rows else None


def manifest(log):
    f = Path(log).parent / "batch-manifest.json"
    return json.loads(f.read_text()) if f.is_file() else {}


def positions(rows):
    """The positions the scan searches: the position before each model decision (K = rows before it), in order, and
    the end (K = every row). Each is (index, K, the decision row taken from it, or None at the end)."""
    out = [(i, k, r) for i, (k, r) in enumerate((k, r) for k, r in enumerate(rows) if r.get("tier") in MODEL_TIERS)]
    out.append((len(out), len(rows), None))
    return out


# ------------------------------------------------------------------ the scan and the floor

def backward_scan(pos, search, jobs=1):
    """Search positions from the end until one is proven (W only goes from true to false), `jobs` at a time.
    Returns {index: verdict row}. A drift stops the scan: what came before it is no longer the recorded game."""
    results = {}
    i = len(pos) - 1
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        while i >= 0:
            batch = list(range(i, max(-1, i - jobs), -1))
            for j, res in zip(batch, ex.map(lambda j: search(pos[j][1]), batch)):
                results[j] = res
            if any(results[j].get("verdict") in ("proven", "drift") for j in batch):
                break
            i -= jobs
    return results


def regret(pos, results):
    """The regret step from the scan's results: the last proven position i and the first `unwinnable` one after it j.
    Exact (regret at decision i) when j == i + 1, else a bracket of decisions [i, j - 1]."""
    proven = [j for j, r in results.items() if r.get("verdict") == "proven"]
    lost = sorted(j for j, r in results.items() if r.get("verdict") == "unwinnable")
    # The scan's two ends, whatever it found: the latest position proven winnable and the earliest proven lost.
    out = {"regret_index": None, "regret_k": None, "regret_bracket": None, "winnable_at_start": None,
           "last_proven_k": pos[max(proven)][1] if proven else None, "first_unwinnable_k": pos[lost[0]][1] if lost else None}
    if any(r.get("verdict") == "drift" for r in results.values()):
        out["status"] = "drift"
        return out
    if not proven:
        out["status"] = "never_proven"   # no position on the scan was proven winnable
        out["winnable_at_start"] = False if results.get(0, {}).get("verdict") == "unwinnable" else None
        return out
    i = max(proven)
    after = sorted(j for j, r in results.items() if j > i and r.get("verdict") == "unwinnable")
    if i == len(pos) - 1:
        out["status"] = "won_at_end"     # the recorded end is winnable: no decision lost the win
        return out
    if not after:
        out["status"] = "undetermined"   # nothing after the last proven position was proven lost
        out["regret_bracket"] = [pos[i][1], None]
        return out
    j = after[0]
    out["winnable_at_start"] = True if i == 0 else None
    if j == i + 1:
        out.update(status="exact", regret_index=i, regret_k=pos[i][1])
    else:
        out.update(status="bracket", regret_bracket=[pos[i][1], pos[j - 1][1]])
    return out


def floor(option_rows, bound):
    """The share of the n options that keep the win: exact when every option is decided, else a bracket."""
    proven = sum(1 for r in option_rows if r.get("verdict") == "proven")
    lost = sum(1 for r in option_rows if r.get("verdict") == "unwinnable")
    if not bound:
        return {"floor": None, "floor_bracket": None, "undetermined_options": None}
    undecided = bound - proven - lost
    return {"floor": proven / bound if undecided == 0 else None,
            "floor_bracket": [proven / bound, (bound - lost) / bound],
            "undetermined_options": undecided, "options_kept": proven, "options_lost": lost}


# ------------------------------------------------------------------ running the Java side

class Runner:
    """Launches LlmSpikePuzzle's step search on an isolated build (puzzle_baselines.Build), one JVM per call."""

    def __init__(self, build, log, pzl, env, out, budget, lines, cap_ms):
        self.build, self.log, self.pzl, self.env, self.out = build, Path(log).resolve(), Path(pzl).resolve(), env, Path(out)
        self.budget, self.lines, self.cap_ms = budget, lines, cap_ms
        self.n = 0
        self.resume = False

    def __call__(self, k, force=None, budget=None):
        self.n += 1
        budget = budget or self.budget
        d = self.out / ("k%03d%s%s" % (k, "" if force is None else "-f%s" % force,
                                       "" if budget == self.budget else "-b%d" % budget))
        env = dict(self.env)
        env.update({"PUZZLE": str(self.pzl), "CONTROLLER_A": "llm", "CONTROLLER_B": "ai",
                    "SMART_MODEL_A": "replay:" + str(self.log), "FAST_MODEL_A": "replay:" + str(self.log),
                    "PZL_SEEDS": str(seed_of(self.log)), "REPLAY_HANDOVER": str(k),
                    "PZL_SEARCH_BUDGET": str(budget), "PZL_SEARCH_LINES": str(self.lines),
                    "GAME_CAP_MS": str(self.cap_ms)})
        if force is not None:
            env["REPLAY_FORCE"] = str(force)
        if self.resume and read_jsonl(d / "step-search.jsonl"):
            return read_jsonl(d / "step-search.jsonl")   # this search, already run on this build into this dir
        (d / "step-search.jsonl").unlink(missing_ok=True)
        t0 = time.time()
        rc = self.build.run(env, d, timeout_s=6 * 3600)
        rows = read_jsonl(d / "step-search.jsonl")
        trim(d / "jvm.log")
        if not rows:
            return [{"k": k, "verdict": "error", "error": "JVM exit %s and no row (see %s/jvm.log)" % (rc, d),
                     "wall_s": round(time.time() - t0, 1)}]
        for r in rows:
            r["wall_s"] = round(time.time() - t0, 1)
        return rows


def read_jsonl(f):
    f = Path(f)
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()] if f.is_file() else []


def trim(f, keep=200):
    """A search logs every rollout's routing lines: only the tail is kept (the disk is shared)."""
    try:
        lines = Path(f).read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) > keep:
            Path(f).write_text("\n".join(["... %d lines dropped ..." % (len(lines) - keep)] + lines[-keep:]) + "\n")
    except OSError:
        pass


def option_label(row, value, bound):
    """The label of search value `value` at a recorded decision row (PASS or the decline is the last value)."""
    opts = row.get("options") or []
    if value is None or value < 0:
        return None
    if value < len(opts):
        return opts[value]
    return "(decline)" if value == bound - 1 else None


def refine(pos, results, search, budget, most=3):
    """The scan found no exact regret step: the positions just before the earliest one proven lost are searched again
    with `budget`, earliest-lost first, while they come back lost (at most `most`). Updates `results`; returns the
    searches made."""
    reg = regret(pos, results)
    if not budget or reg["status"] in ("exact", "drift", "won_at_end") or reg["first_unwinnable_k"] is None:
        return 0
    j = min(j for j, r in results.items() if r.get("verdict") == "unwinnable") - 1
    n = 0
    while j >= 0 and n < most and results.get(j, {}).get("verdict") != "proven":
        r = dict(search(pos[j][1], budget=budget)[0], refined=True)
        results[j] = r
        n += 1
        if r.get("verdict") != "unwinnable":
            break
        j -= 1
    return n


def analyse(log, search, jobs=1, emit=print, refine_budget=None, refine_most=3):
    """One attempt: the backward scan, a deeper search at its boundary (refine_budget), then the floor at an exact
    regret step. `search(k, force=None, budget=None)` -> rows."""
    rows = decision_rows(log)
    res = result_row(log) or {}
    pos = positions(rows)
    base = {"version": VERSION, "id": res.get("id"), "seed": seed_of(log), "source_game": str(log),
            "pzl_sha256": res.get("pzl_sha256"), "recorded_status": res.get("status"),
            "recorded_epoch": res.get("harness_epoch")}
    t0 = time.time()
    summary = dict(base, t="step_regret_summary", decision_rows=len(rows), model_decisions=len(pos) - 1)
    if res.get("status") == "solved":
        # The model's own line is the witness that every position on it is winnable: there is no regret step.
        summary.update(status="solved", searches=0, wall_s=0.0)
        emit(summary)
        return summary
    scanned = backward_scan(pos, lambda k: search(k)[0], jobs)
    refined = refine(pos, scanned, search, refine_budget, refine_most)
    for j in sorted(scanned):
        i, k, row = pos[j]
        emit(dict(base, t="step_regret", kind="position", index=i, k=k,
                  next_decision=None if row is None else {"type": row.get("type"), "turn": row.get("turn"),
                                                          "phase": row.get("phase"), "options": row.get("options"),
                                                          "choice": row.get("choice")},
                  **{x: scanned[j].get(x) for x in ("verdict", "replays", "budget", "capped_rollouts", "exhausted",
                                                    "line", "actions", "drift", "wall_s", "refined")}))
    reg = regret(pos, scanned)
    summary.update(reg, searches=len(scanned) + refined, refined_searches=refined,
                   verdicts={v: sum(1 for r in scanned.values() if r.get("verdict") == v)
                             for v in ("proven", "unwinnable", "not_found", "drift", "error")})
    if reg["status"] == "exact":
        i, k, row = pos[reg["regret_index"]]
        opts = search(k, "all")
        bound = opts[0].get("bound")
        if opts[0].get("refused") or opts[0].get("verdict") in ("drift", "error"):
            summary.update(floor=None, floor_refused=opts[0].get("refused") or opts[0].get("drift") or opts[0].get("error"))
        else:
            for o in opts:
                emit(dict(base, t="step_regret", kind="option", k=k, forced=o.get("forced"),
                          label=option_label(row, o.get("forced"), bound), model_choice=o.get("forced") == o.get("model_value"),
                          **{x: o.get(x) for x in ("verdict", "replays", "capped_rollouts", "exhausted", "line", "actions",
                                                   "wall_s")}))
            model_value = opts[0].get("model_value")
            summary.update(floor(opts, bound), regret_decision={
                "type": row.get("type"), "turn": row.get("turn"), "phase": row.get("phase"), "n_options": bound,
                "options": row.get("options"), "model_value": model_value,
                "model_label": option_label(row, model_value, bound),
                "kept_win": [option_label(row, o.get("forced"), bound) for o in opts if o.get("verdict") == "proven"]})
            mv = [o for o in opts if o.get("forced") == model_value]
            # The model's own option, forced, is the position after the regret step: it must not be proven.
            summary["model_option_verdict"] = mv[0].get("verdict") if mv else None
        summary["searches"] += len(opts)
    summary["wall_s"] = round(time.time() - t0, 1)
    emit(summary)
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("attempt", help="an attempt dir (each game-<seed>-*.jsonl) or one game log")
    ap.add_argument("--seed", type=int, help="only this seed's game log of the dir")
    ap.add_argument("--out", required=True, help="where the searches and step-regret.jsonl go")
    ap.add_argument("--pzl", help="the board (default: the manifest's .pzl beside the attempt)")
    ap.add_argument("--build", help="dir with classes/ and classpath.txt (default: the newest acceptance build of this "
                                    "checkout)")
    ap.add_argument("--allow-stale-build", action="store_true")
    ap.add_argument("--budget", type=int, default=600, help="rollouts per search (default 600)")
    ap.add_argument("--lines", type=int, default=32, help="winning lines tried per choice point (default 32)")
    ap.add_argument("--cap-ms", type=int, default=60000, help="wall-clock cap per rollout (default 60000)")
    ap.add_argument("--refine-budget", type=int, help="when the scan names no exact step, search the positions just "
                                                       "before the earliest lost one again with this budget")
    ap.add_argument("--refine-most", type=int, default=3, help="at most this many refined searches (default 3)")
    ap.add_argument("--resume", action="store_true", help="reuse each search already in --out (same build only)")
    ap.add_argument("--jobs", type=int, default=1, help="JVMs at once (positions searched together; default 1)")
    a = ap.parse_args(argv)
    import puzzle_baselines as pb   # the isolated-build launcher (imported here: the tests need no build)
    build = pb.Build(a.build, allow_stale=a.allow_stale_build)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    sink = out / "step-regret.jsonl"

    def emit(row):
        row.setdefault("build_overlay_sha", build.overlay_sha)
        with open(sink, "a") as f:
            f.write(json.dumps(row) + "\n")
        print(json.dumps({k: row.get(k) for k in ("t", "kind", "k", "forced", "verdict", "status", "regret_k", "floor",
                                                  "floor_bracket", "wall_s") if row.get(k) is not None}), flush=True)

    for log in game_logs(a.attempt, a.seed):
        m = manifest(log)
        pzl = a.pzl or (Path(log).parent / m.get("record", {}).get("pzl_file", ""))
        if not Path(pzl).is_file():
            raise SystemExit("no board for %s: pass --pzl" % log)
        env = dict(m.get("condition", {}).get("env", {}))
        run = Runner(build, log, pzl, env, out / Path(log).stem, a.budget, a.lines, a.cap_ms)
        if a.resume:
            # A game not searched yet has nothing to reuse; one that was must have run on this build.
            if any(run.out.rglob("build.json")) and not pb.ran_on(run.out, build):
                raise SystemExit("--resume: %s was not run on build %s" % (run.out, build.overlay_sha))
            run.resume = True

        def search(k, force=None, budget=None, run=run):
            return run(k, force, budget)
        analyse(log, search, a.jobs, emit, a.refine_budget, a.refine_most)


if __name__ == "__main__":
    main()
