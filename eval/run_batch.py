#!/usr/bin/env python3
"""Batch driver for the MTG Forge LLM eval.

Orchestrates N headless games by invoking `bash run-llm-headless.sh` from
FORGE_HOME with per-game env vars (GAME_SEED, SWAP_SEATS, EVAL_OUT, ...).
Each game is a separate JVM process. Resumable: seed/swap combos whose JSONL
in --out already contains a final game line are skipped.

Examples:
  python3 eval/run_batch.py --seeds 1..20 --pairs --out runs/mirror-01 \
      --controller-a llm --controller-b llm --smart-a haiku --smart-b haiku
  python3 eval/run_batch.py --seeds 3,7,42 --out runs/spot --jobs 2
"""

import argparse
import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PRINT_LOCK = threading.Lock()


def log(msg):
    with PRINT_LOCK:
        print(msg, flush=True)


def parse_seeds(spec):
    """Accept '1..20' (inclusive range) or '3,7,42' (explicit list) or '5'."""
    spec = spec.strip()
    m = re.fullmatch(r"(-?\d+)\.\.(-?\d+)", spec)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if hi < lo:
            raise argparse.ArgumentTypeError("seed range end < start: %s" % spec)
        return list(range(lo, hi + 1))
    try:
        return [int(s) for s in spec.split(",") if s.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError("bad --seeds spec: %s" % spec)


def has_game_line(out_dir, seed, swap):
    """True if some game-<seed>-*.jsonl in out_dir has a final game line
    matching this seed/swap combo. Defensive: skip unparseable lines."""
    for path in Path(out_dir).glob("game-%d-*.jsonl" % seed):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(rec, dict) or rec.get("t") != "game":
                        continue
                    if rec.get("seed") == seed and bool(rec.get("swap", False)) == swap:
                        return True
        except OSError:
            continue
    return False


def build_env(args, seed, swap, out_dir):
    env = os.environ.copy()
    env["GAME_SEED"] = str(seed)
    # Explicitly clear SWAP_SEATS when not swapping so an inherited value
    # can't silently flip seats.
    if swap:
        env["SWAP_SEATS"] = "1"
    else:
        env.pop("SWAP_SEATS", None)
    env["EVAL_OUT"] = str(out_dir)
    if args.cap_minutes > 0:
        env["GAME_CAP_MS"] = str(int(args.cap_minutes * 60_000))

    passthrough = {
        "SMART_MODEL_A": args.smart_a,
        "SMART_MODEL_B": args.smart_b,
        "FAST_MODEL_A": args.fast_a,
        "FAST_MODEL_B": args.fast_b,
        "CONTROLLER_A": args.controller_a,
        "CONTROLLER_B": args.controller_b,
        "DECK_A": args.deck_a,
        "DECK_B": args.deck_b,
        "PLAN_A": args.plan_a,
        "PLAN_B": args.plan_b,
    }
    for key, val in passthrough.items():
        if val is not None:
            env[key] = val
    for kv in args.env or []:
        if "=" not in kv:
            raise SystemExit("--env expects KEY=VALUE, got: %s" % kv)
        k, _, v = kv.partition("=")
        env[k] = v
    return env


def run_one(args, seed, swap, out_dir):
    """Run one game. Returns (seed, swap, status, exit_code)."""
    env = build_env(args, seed, swap, out_dir)
    log_path = out_dir / ("game-%d-%d.log" % (seed, 1 if swap else 0))
    # In-game cap is GAME_CAP_MS; the subprocess timeout below is a backstop
    # for a wedged JVM (2x the cap plus grace for JVM startup / deck load).
    timeout = args.cap_minutes * 60 * 2 + 300 if args.cap_minutes > 0 else None
    log("[start] seed=%d swap=%d -> %s" % (seed, swap, log_path.name))
    try:
        with open(log_path, "w", encoding="utf-8") as lf:
            proc = subprocess.run(
                ["bash", "run-llm-headless.sh"],
                cwd=str(args.forge_home),
                env=env,
                stdout=lf,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        code = proc.returncode
    except subprocess.TimeoutExpired:
        log("[timeout] seed=%d swap=%d killed after backstop timeout" % (seed, swap))
        return (seed, swap, "timeout", -1)
    except OSError as e:
        log("[error] seed=%d swap=%d failed to launch: %s" % (seed, swap, e))
        return (seed, swap, "launch-error", -1)

    if code != 0:
        log("[fail] seed=%d swap=%d exit=%d (see %s)" % (seed, swap, code, log_path))
        return (seed, swap, "nonzero-exit", code)
    # Exit 0 but no game line means the game crashed before summarizing;
    # aggregate.py will count the file (if any) as invalid.
    if not has_game_line(out_dir, seed, swap):
        log("[warn] seed=%d swap=%d exited 0 but wrote no game line" % (seed, swap))
        return (seed, swap, "no-game-line", code)
    log("[done] seed=%d swap=%d" % (seed, swap))
    return (seed, swap, "ok", code)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", required=True, type=parse_seeds,
                    help="'1..20' inclusive range, or '3,7,42' explicit list")
    ap.add_argument("--pairs", action="store_true",
                    help="play each seed twice with SWAP_SEATS flipped (paired design)")
    ap.add_argument("--jobs", type=int, default=1,
                    help="parallel game processes (each a separate JVM). "
                         "claude CLI rate limits usually cap useful values at 1-2. default 1")
    ap.add_argument("--out", required=True, help="output dir for JSONL + stdout logs")
    ap.add_argument("--forge-home", default=os.path.expanduser("~/code/forge"),
                    help="dir containing run-llm-headless.sh (default ~/code/forge)")
    ap.add_argument("--cap-minutes", type=float, default=30.0,
                    help="per-game wall-clock cap in minutes; sets GAME_CAP_MS. "
                         "0 disables. default 30")
    # per-seat pass-through (only exported when given, so runner defaults apply)
    ap.add_argument("--smart-a", help="SMART_MODEL_A")
    ap.add_argument("--smart-b", help="SMART_MODEL_B")
    ap.add_argument("--fast-a", help="FAST_MODEL_A")
    ap.add_argument("--fast-b", help="FAST_MODEL_B")
    ap.add_argument("--controller-a", choices=["llm", "random", "floor", "ai"],
                    help="CONTROLLER_A")
    ap.add_argument("--controller-b", choices=["llm", "random", "floor", "ai"],
                    help="CONTROLLER_B")
    ap.add_argument("--deck-a", help="DECK_A")
    ap.add_argument("--deck-b", help="DECK_B")
    ap.add_argument("--plan-a", help="PLAN_A")
    ap.add_argument("--plan-b", help="PLAN_B")
    ap.add_argument("--env", action="append", metavar="KEY=VALUE",
                    help="extra env var for the runner (repeatable)")
    args = ap.parse_args()

    args.forge_home = Path(args.forge_home).expanduser().resolve()
    runner = args.forge_home / "run-llm-headless.sh"
    if not runner.is_file():
        raise SystemExit("run-llm-headless.sh not found in --forge-home: %s" % args.forge_home)

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.jobs > 2:
        log("WARNING: --jobs=%d — claude CLI rate limits usually make >2 "
            "parallel games counterproductive (decisions time out and fall "
            "back to auto/ai, polluting the eval)." % args.jobs)

    swaps = [False, True] if args.pairs else [False]
    combos = [(seed, swap) for seed in args.seeds for swap in swaps]

    todo, skipped = [], []
    for seed, swap in combos:
        if has_game_line(out_dir, seed, swap):
            skipped.append((seed, swap))
        else:
            todo.append((seed, swap))
    if skipped:
        log("Resuming: skipping %d/%d combos already complete: %s"
            % (len(skipped), len(combos),
               ", ".join("%d/swap%d" % (s, w) for s, w in skipped)))
    if not todo:
        log("Nothing to do.")
        return 0

    log("Running %d games (jobs=%d, out=%s, forge_home=%s)"
        % (len(todo), args.jobs, out_dir, args.forge_home))

    results = []
    if args.jobs <= 1:
        for seed, swap in todo:
            results.append(run_one(args, seed, swap, out_dir))
    else:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futs = [pool.submit(run_one, args, seed, swap, out_dir)
                    for seed, swap in todo]
            for fut in as_completed(futs):
                results.append(fut.result())

    ok = [r for r in results if r[2] == "ok"]
    bad = [r for r in results if r[2] != "ok"]
    log("")
    log("=== batch summary ===")
    log("completed: %d  skipped(resume): %d  problems: %d"
        % (len(ok), len(skipped), len(bad)))
    for seed, swap, status, code in sorted(bad):
        log("  seed=%d swap=%d status=%s exit=%d" % (seed, swap, status, code))
    log("Aggregate with: python3 %s %s"
        % (Path(__file__).with_name("aggregate.py"), out_dir))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
