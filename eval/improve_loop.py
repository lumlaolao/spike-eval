#!/usr/bin/env python3
"""improve_loop.py — one iteration of the harness self-improvement loop.

Launches a small batch of games by shelling out to run_batch.py (which in
turn invokes `bash run-llm-headless.sh` from FORGE_HOME — see run_batch.py
and README.md's env-var table for the exact conventions this reuses rather
than duplicates: GAME_SEED, EVAL_OUT, CONTROLLER_A/B, SMART_MODEL_A/B,
FAST_MODEL_A/B, per-game jsonl + stdout .log placement, --exploratory
guard-skipping, provenance stamping). After the batch, runs
defect_analyzer.py over the freshly written output directory and writes
metrics.json + summary.txt next to the game files.

Intended cycle (see eval/README.md's "Improvement loop" section):

    fix harness -> rebuild -> python3 eval/improve_loop.py -> read metrics.json
    -> fix harness -> rebuild -> rerun ...

Config (flags, each overridable via the paired env var):
  --games N            number of seeds to play (default 2; IMPROVE_GAMES)
  --seat-a/--seat-b     model spec per seat, used for BOTH the smart- and
                        fast-tier model (default
                        openrouter:google/gemini-3.5-flash for both — cheap;
                        IMPROVE_SEAT_A / IMPROVE_SEAT_B). Ignored in
                        --controller floor (no model is ever called).
  --controller floor|llm  floor = deterministic legal-fallback controller,
                        free, no API key needed, ALWAYS runs. llm = real
                        model calls (IMPROVE_CONTROLLER).
  --seed BASE           first seed; games use BASE, BASE+1, ... (default 1;
                        IMPROVE_SEED)
  --deck PATH           mirror decklist for both seats (default
                        eval/decks/mardu-energy.txt; IMPROVE_DECK)
  --out DIR             output dir (default runs/improve/<UTC-timestamp>/)

Runs in run_batch.py's --exploratory mode (this loop is for fast harness
iteration, not citable win-rate stats — see run_batch.py/eval/README.md for
the cited-mode mirror/heuristic/jobs guards this deliberately skips).

Refuses to run (exit 2) in --controller llm when either seat spec uses the
openrouter: backend and OPENROUTER_API_KEY is unset in the environment —
floor mode never needs a key and always works.
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import puzzle_format  # noqa: E402  forge_home: the Forge checkout, $FORGE_HOME first
DEFAULT_SEAT_MODEL = "openrouter:google/gemini-3.5-flash"
DEFAULT_DECK = HERE / "decks" / "mardu-energy.txt"


def uses_openrouter(spec):
    return isinstance(spec, str) and spec.startswith("openrouter:")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--games", type=int,
                    default=int(os.environ.get("IMPROVE_GAMES", "2")),
                    help="number of seeds to play (default 2)")
    ap.add_argument("--seat-a", default=os.environ.get("IMPROVE_SEAT_A", DEFAULT_SEAT_MODEL),
                    help="model spec for seat A, smart+fast tier "
                         "(default %s)" % DEFAULT_SEAT_MODEL)
    ap.add_argument("--seat-b", default=os.environ.get("IMPROVE_SEAT_B", DEFAULT_SEAT_MODEL),
                    help="model spec for seat B, smart+fast tier "
                         "(default %s)" % DEFAULT_SEAT_MODEL)
    ap.add_argument("--controller", choices=["floor", "llm"],
                    default=os.environ.get("IMPROVE_CONTROLLER", "floor"),
                    help="floor (free, deterministic, no key needed) or llm "
                         "(default floor)")
    ap.add_argument("--seed", type=int, default=int(os.environ.get("IMPROVE_SEED", "1")),
                    help="base seed; games use seed, seed+1, ... (default 1)")
    ap.add_argument("--deck", default=os.environ.get("IMPROVE_DECK", str(DEFAULT_DECK)),
                    help="mirror decklist for both seats (default %s)" % DEFAULT_DECK)
    ap.add_argument("--out", default=None,
                    help="output dir (default runs/improve/<UTC-timestamp>/)")
    ap.add_argument("--forge-home", default=puzzle_format.forge_checkout(),
                    help="the Forge checkout holding run-llm-headless.sh (default: $FORGE_HOME as given if set, else "
                         "~/code/forge or /root/forge; puzzle_format.forge_checkout)")
    ap.add_argument("--cap-minutes", type=float, default=None,
                    help="passthrough to run_batch.py --cap-minutes "
                         "(default: derived by run_batch.py)")
    ap.add_argument("--skip-analysis", action="store_true",
                    help="don't run defect_analyzer.py after the batch")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the run_batch.py command and exit without "
                         "launching anything (or calling defect_analyzer)")
    return ap.parse_args(argv)


def refuse(msg):
    print("improve_loop: REFUSING: " + msg, file=sys.stderr)
    sys.exit(2)


def build_run_batch_cmd(args, out_dir):
    seeds_spec = (str(args.seed) if args.games == 1
                 else "%d..%d" % (args.seed, args.seed + args.games - 1))
    cmd = [sys.executable, str(HERE / "run_batch.py"),
           "--seeds", seeds_spec,
           "--exploratory",
           "--deck", str(args.deck),
           "--controller-a", args.controller,
           "--controller-b", args.controller,
           "--out", str(out_dir),
           "--forge-home", args.forge_home]
    if args.controller == "llm":
        cmd += ["--smart-a", args.seat_a, "--smart-b", args.seat_b,
                "--fast-a", args.seat_a, "--fast-b", args.seat_b]
    if args.cap_minutes is not None:
        cmd += ["--cap-minutes", str(args.cap_minutes)]
    return cmd


def main(argv=None):
    args = parse_args(argv)

    if args.games < 1:
        refuse("--games must be >= 1 (got %d)" % args.games)

    if args.controller == "llm":
        needs_key = uses_openrouter(args.seat_a) or uses_openrouter(args.seat_b)
        if needs_key and not os.environ.get("OPENROUTER_API_KEY"):
            refuse(
                "--controller llm needs an OpenRouter model call "
                "(seat-a=%s seat-b=%s) but OPENROUTER_API_KEY is not set in "
                "the environment. Export OPENROUTER_API_KEY (and "
                "OPENROUTER_ALLOW_EGRESS=1 — the harness's separate consent "
                "gate for sending game state to a third-party API) before "
                "running llm mode, or use --controller floor (free, "
                "deterministic, no key needed)." % (args.seat_a, args.seat_b))
    else:
        print("[improve_loop] controller=floor: no API key needed, no network calls.")

    if not Path(args.deck).expanduser().is_file():
        refuse("deck file does not exist: %s (pass --deck PATH)" % args.deck)

    if args.out:
        out_dir = Path(args.out).expanduser().resolve()
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        out_dir = REPO_ROOT / "runs" / "improve" / ts
    print("[improve_loop] out=%s" % out_dir)

    cmd = build_run_batch_cmd(args, out_dir)

    if args.dry_run:
        print("[improve_loop] DRY RUN — would launch:")
        print("  " + " ".join(cmd))
        print("[improve_loop] DRY RUN — would then analyze with:")
        print("  %s %s %s --json %s --summary %s"
             % (sys.executable, HERE / "defect_analyzer.py", out_dir,
                out_dir / "metrics.json", out_dir / "summary.txt"))
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    print("[improve_loop] launching: %s" % " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(HERE))
    # run_batch.py: 0 = all games ok, 1 = some games had problems (timeout /
    # nonzero exit / no game line) but still worth analyzing, 2 = a guard
    # refused before anything launched (bad deck, bad forge-home, ...).
    if proc.returncode not in (0, 1):
        print("[improve_loop] run_batch.py failed (exit %d); nothing to analyze."
             % proc.returncode, file=sys.stderr)
        return proc.returncode

    if args.skip_analysis:
        print("[improve_loop] --skip-analysis: done. Analyze later with:")
        print("  python3 %s %s" % (HERE / "defect_analyzer.py", out_dir))
        return 0

    metrics_path = out_dir / "metrics.json"
    summary_path = out_dir / "summary.txt"
    print("[improve_loop] analyzing -> %s" % metrics_path)
    a_proc = subprocess.run([sys.executable, str(HERE / "defect_analyzer.py"),
                             str(out_dir), "--json", str(metrics_path),
                             "--summary", str(summary_path)])
    print()
    print("[improve_loop] done. games in %s; metrics in %s (summary %s)"
         % (out_dir, metrics_path, summary_path))
    if a_proc.returncode == 1:
        print("[improve_loop] defect_analyzer flagged a HARD INVARIANT "
             "FAILURE (tier=ai>0 and/or silent decision drops) — read %s, "
             "fix the harness, rebuild, and rerun." % summary_path)
    elif a_proc.returncode not in (0, 1):
        print("[improve_loop] defect_analyzer.py errored (exit %d) — see "
             "output above." % a_proc.returncode, file=sys.stderr)
    return a_proc.returncode


if __name__ == "__main__":
    sys.exit(main())
