#!/usr/bin/env python3
"""Run a PZL suite through the headless Forge puzzle runner and summarize.

This measures the axis's FLOOR: what a solver that does not understand the position
scores. Per the project's "a score without its floor is not a score" rule, no PZL
number may be quoted until this has been executed for the trivial arms.

The arms, cheapest first:
  floor   -- passes on every play decision (never builds a play menu at all)
  random  -- uniform choice over the engine's real legal menu, so it exercises the
             full play/attack/block paths that `floor` short-circuits past
  ai-log  -- Forge's own heuristic AI. NOT a trivial baseline: it is the "can a
             non-LLM agent already do this" reference. Note it must be ai-log and
             not ai, since a stock PlayerControllerAi cannot be designated as the
             goal-bearing seat (see LlmSpikePuzzle.requireOverridableSolver).

TAINT. A puzzle's libraries are hand-authored and usually near-empty, so a player can
lose by decking out for reasons unrelated to the puzzle's line -- handing the solver a
free win, or denying it a real one. The runner records each side's GameLossReason;
rows where either side was Milled are counted separately here and EXCLUDED from the
headline solve rate rather than silently pooled.

    python3 eval/run_pzl.py --arm floor --out runs/pzl/floor
    python3 eval/run_pzl.py --arm random --jobs 6 --out runs/pzl/random
"""
import argparse
import collections
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORGE_HOME = os.environ.get("FORGE_HOME", os.path.expanduser("~/code/forge"))
LAUNCHER = os.path.join(FORGE_HOME, "run-llm-headless.sh")

_print_lock = threading.Lock()


def load_suite(path):
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def run_one(row, arm, opp, seed, cap_ms, timeout_s):
    """-> (result dict, raw stdout). result is None if the JVM produced no row."""
    pid = row["source_file"][:-4] if row["source_file"].endswith(".pzl") else row["source_file"]
    env = dict(os.environ)
    env.update({
        "PUZZLE": pid,
        "CONTROLLER_A": arm,
        "CONTROLLER_B": opp,
        "GAME_SEED": str(seed),
        "GAME_CAP_MS": str(cap_ms),
        # The 4 upstream-only puzzles are vendored in this repo, not in the pinned checkout.
        "PZL_REPO": REPO,
    })
    # No EVAL_OUT: every worker would append to one file concurrently. The parent
    # collects PZL_RESULT off stdout and writes the jsonl itself, single-threaded.
    env.pop("EVAL_OUT", None)
    try:
        proc = subprocess.run(["bash", LAUNCHER], env=env, cwd=FORGE_HOME,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=timeout_s)
        out = proc.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"").decode("utf-8", "replace")
        return {"id": row["id"], "source_file": row["source_file"], "status": "invalid",
                "solved": False, "error": "harness timeout after %ds" % timeout_s}, out
    return parse_result(out, row), out


def parse_result(out, row):
    """Extract the runner's PZL_RESULT row from a JVM's stdout.

    A run that produced no result line is INVALID, never a failure: the difference
    between "the solver could not solve it" and "the JVM died / the goal was refused /
    the file was missing" is the difference between a score and a bug, and collapsing
    them would quietly depress every solve rate.
    """
    for line in out.splitlines():
        if line.startswith("PZL_RESULT "):
            try:
                res = json.loads(line[len("PZL_RESULT "):])
            except ValueError as exc:
                return {"id": row["id"], "status": "invalid", "solved": False,
                        "error": "unparseable PZL_RESULT: %s" % exc}
            res["suite_id"] = row["id"]
            res["difficulty"] = row.get("difficulty")
            res["recency_band"] = row.get("recency_band")
            return res
    tail = "\n".join(out.strip().splitlines()[-3:])
    return {"id": row["id"], "source_file": row.get("source_file"), "status": "invalid",
            "solved": False, "error": "no PZL_RESULT line; tail: " + tail}


def summarize(results, arm, quiet=False):
    """-> summary dict. Solve rate is reported over UNTAINTED, scoreable rows only."""
    by_status = collections.Counter(r.get("status", "?") for r in results)
    tainted = [r for r in results if r.get("tainted_by_deckout")]
    taint_side = collections.Counter(r.get("taint_side") for r in tainted)
    scoreable = [r for r in results
                 if r.get("status") in ("solved", "failed") and not r.get("tainted_by_deckout")]
    solved = [r for r in scoreable if r.get("solved")]
    # A solve among the tainted rows is the free-win case specifically.
    free_wins = [r for r in tainted if r.get("solved")]
    no_enforce = [r for r in results if r.get("status") not in ("invalid",)
                  and not r.get("goal_enforcement_installed", True)]
    loss_reasons = collections.Counter(r.get("solver_loss_reason") or "-" for r in results)

    out = collections.OrderedDict([
        ("arm", arm),
        ("n_attempted", len(results)),
        ("status", dict(by_status)),
        ("n_tainted_by_deckout", len(tainted)),
        ("taint_side", dict(taint_side)),
        ("n_free_wins_from_taint", len(free_wins)),
        ("n_goal_enforcement_missing", len(no_enforce)),
        ("n_scoreable", len(scoreable)),
        ("n_solved", len(solved)),
        ("solve_rate", round(len(solved) / float(len(scoreable)), 4) if scoreable else None),
        ("solver_loss_reasons", dict(loss_reasons)),
    ])
    if quiet:
        return out
    print("\n===== PZL floor: arm=%s =====" % arm)
    print("attempted        : %d" % len(results))
    print("status           : %s" % ", ".join("%s=%d" % kv for kv in sorted(by_status.items())))
    print("tainted (deckout): %d  %s" % (len(tainted), dict(taint_side)))
    print("  of those, WINS : %d   <- free wins; not evidence of solving" % len(free_wins))
    if no_enforce:
        print("NO GOAL CARD     : %d rows -- UNSCOREABLE, harness fault, not a result" % len(no_enforce))
    print("scoreable        : %d  (untainted, goal enforced)" % len(scoreable))
    print("SOLVED           : %d" % len(solved))
    print("solve rate       : %s" % ("%.4f" % out["solve_rate"] if scoreable else "n/a"))
    print("solver loss why  : %s"
          % ", ".join("%s=%d" % kv for kv in sorted(loss_reasons.items())))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default=os.path.join(REPO, "eval/suites/pzl-v1.jsonl"))
    ap.add_argument("--arm", default="floor", help="solver arm: floor|random|ai-log|llm")
    ap.add_argument("--opponent", default="ai")
    ap.add_argument("--out", required=True, help="output dir for results.jsonl + summary.json")
    ap.add_argument("--jobs", type=int, default=4,
                    help="parallel JVMs. CLAUDE.md: floor/random 4-8 (CPU-burst-bound)")
    ap.add_argument("--seed", type=int, default=20000,
                    help="eval-reserved block 20000-29999 (eval/seed_policy.py)")
    ap.add_argument("--cap-ms", type=int, default=120000)
    ap.add_argument("--limit", type=int, default=0, help="first N puzzles only (smoke test)")
    args = ap.parse_args(argv)

    if not os.path.isfile(LAUNCHER):
        raise SystemExit("launcher not found: %s (run install.sh)" % LAUNCHER)
    rows = load_suite(args.suite)
    if args.limit:
        rows = rows[:args.limit]
    os.makedirs(args.out, exist_ok=True)
    print("[pzl] %d puzzles, arm=%s, opponent=%s, jobs=%d, seed=%d"
          % (len(rows), args.arm, args.opponent, args.jobs, args.seed))

    # Harness timeout must exceed the in-JVM cap plus model init (~10s card DB load).
    timeout_s = args.cap_ms / 1000.0 + 120
    results = []
    done = [0]

    def work(row):
        res, out = run_one(row, args.arm, args.opponent, args.seed, args.cap_ms, timeout_s)
        with _print_lock:
            done[0] += 1
            mark = {"solved": "S", "failed": ".", "invalid": "!", "unscoreable": "?"}.get(
                res.get("status"), "!")
            if res.get("tainted_by_deckout"):
                mark = mark.lower() if mark == "S" else "~"
            sys.stdout.write(mark)
            if done[0] % 50 == 0:
                sys.stdout.write(" %d/%d\n" % (done[0], len(rows)))
            sys.stdout.flush()
            if res.get("error"):
                logp = os.path.join(args.out, "fail-%s.log" % res.get("id", "unknown"))
                with open(logp, "w", encoding="utf-8") as fh:
                    fh.write(out)
        return res

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for res in pool.map(work, rows):
            results.append(res)
    print()

    resp = os.path.join(args.out, "results.jsonl")
    with open(resp, "w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    summary = summarize(results, args.arm)
    summary["suite"] = os.path.basename(args.suite)
    summary["opponent_arm"] = args.opponent
    summary["seed"] = args.seed
    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print("\nwrote %s" % resp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
