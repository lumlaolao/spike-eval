#!/usr/bin/env python3
"""The menu at the end of a misreading-probe prefix. Free: no model. Reading the output: docs/eval/PUZZLES.md, "The
misreading probe".

  python3 eval/puzzles/probe_walk.py --build DIR --pzl P.pzl --prefix "Solver:4:0" [--options] [--opponent search]

One invocation can start dozens of JVMs: hold the machine lock ($HOME/.forge-agent-lock, see the defect-hunt skill)
around the whole command. The build is the caller's; this never compiles.
"""

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import puzzle_baselines as pb  # noqa: E402

SENTINEL_BOUNDS = (99, 98)   # the second only if the game really asks for 99 options
MAX_OPTIONS = 64             # one JVM per value: a bigger menu is asked for with --max-options
# PuzzleArms.Line.draw: "draw <at>: line has <step>, game asked <seat>:<bound>"
DRIFT = re.compile(r"^draw (\d+): line has \S+, game asked (Solver|Opponent):(\d+)$")
NOT_CONSUMED = re.compile(r"^line not consumed: (\d+) of (\d+) steps$")


class WalkError(Exception):
    """The menu could not be read; the message says why."""


def join(prefix, *steps):
    return " ".join(prefix.split() + list(steps))


def next_decision(row, n_prefix):
    """(seat, bound) of the first decision after the prefix, from the sentinel probe's row; None when the sentinel
    was matched (the game really asked for that many options). The drift is read first: probeRun records it before
    the random completion, which can still hit the draw cap or the wall clock, so a stopped row with a drift has
    a menu; one without a usable drift is refused."""
    if not row:
        raise WalkError("the sentinel probe wrote no result row")
    if row.get("kind") != "probe":
        raise WalkError("the sentinel run wrote a %r row, not a probe row" % row.get("kind"))
    drift = row.get("search_drift") or ""
    m = DRIFT.match(drift)
    if m:
        at = int(m.group(1))
        if at != n_prefix:
            raise WalkError("the prefix itself drifted at step %d of %d: %s" % (at, n_prefix, drift))
        return m.group(2), int(m.group(3))
    if row.get("probe_stopped") or row.get("status") == "invalid":
        raise WalkError("the sentinel probe was ended by the draw cap or the wall clock before the sentinel "
                        "step: " + pb.probe_verdict(row))
    m = NOT_CONSUMED.match(drift)
    if m:
        got = int(m.group(1))
        if got == n_prefix:
            raise WalkError("the game ends after the prefix: no decision follows it")
        raise WalkError("the game ended after %d of the prefix's %d steps" % (got, n_prefix))
    if drift:
        raise WalkError("unreadable drift message: %r" % drift)
    return None


def router_lines(out_dir):
    """The [ROUTER] lines of a probe's jvm.log, in order, as the part after the tag."""
    log = Path(out_dir) / "jvm.log"
    if not log.is_file():
        return []
    return [l.split("[ROUTER] ", 1)[1].strip() for l in log.read_text(errors="replace").splitlines() if "[ROUTER] " in l]


def first_new(runs):
    """For each run's router lines, the first line that differs from the other runs' (r[shared]): the prefix's own
    plays and the menu's header are skipped because every run prints them alike. When that line is a "target:" line
    the next "play:" line after it is appended ("target: ... | play: ..."), so two values that differ only in the
    target still differ on screen. Any other line is shown as is, so a draw that is not a play (mana, scry) shows
    its own line and not the random completion's later play. None for a run with no line past the shared ones;
    "(no difference)" for every run when the logs are identical (or only one ran)."""
    shared = 0
    while all(len(r) > shared for r in runs) and len({r[shared] for r in runs}) == 1:
        shared += 1
    if all(len(r) == shared for r in runs):
        return ["(no difference)"] * len(runs)
    out = []
    for r in runs:
        if len(r) <= shared:
            out.append(None)
            continue
        line = r[shared]
        if line.startswith("target:"):
            play = next((l for l in r[shared + 1:] if l.startswith("play: ")), None)
            line = "%s | %s" % (line, play) if play else line
        out.append(line)
    return out


def walk(build, pzl, prefix, opponent="ai", seed=1, completion_seed=0, out_dir=None, options=False,
         max_options=MAX_OPTIONS, run=pb.run_arm, say=print):
    """Reads the menu after `prefix`. Returns (seat, bound, rows) where rows is [(value, router line, verdict)] and
    empty without `options`. `run` is run_arm's signature; tests pass a fake."""
    prefix = " ".join(prefix.split())
    n_prefix = len(prefix.split())
    out = Path(out_dir or pb.REPO / "runs/puzzles/walk" / Path(pzl).stem)

    def probe(line, sub):
        pb.check_probe_line(line)
        d = out / sub
        d.mkdir(parents=True, exist_ok=True)
        rows = run(build, pzl, "search", str(seed), d, pb.probe_env(line, completion_seed, opponent))
        return (rows[-1] if rows else None), d

    for sentinel in SENTINEL_BOUNDS:
        row, _ = probe(join(prefix, "Solver:%d:0" % sentinel), "sentinel")
        found = next_decision(row, n_prefix)
        if found:
            break
    else:
        raise WalkError("the game asks for both %s options at once: not a menu this can read" % (SENTINEL_BOUNDS,))
    seat, bound = found
    say("%s prefix %r (seed %s, completion seed %s, opponent %s): next decision %s:%d (%d options)"
        % (Path(pzl).stem, prefix, seed, completion_seed, opponent, seat, bound, bound))
    if not options:
        return seat, bound, []
    if bound > max_options:
        raise WalkError("%d options is more than --max-options %d (one JVM each)" % (bound, max_options))
    runs, verdicts = [], []
    for v in range(bound):
        row, d = probe(join(prefix, "%s:%d:%d" % (seat, bound, v)), "value-%d" % v)
        runs.append(router_lines(d))
        verdicts.append(pb.probe_verdict(row))
    rows = list(zip(range(bound), first_new(runs), verdicts))
    for v, line, verdict in rows:
        say("  %2d  %s  ->  %s" % (v, line or "(no router line)", verdict))
    return seat, bound, rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", default=None, help="dir with classes/ and classpath.txt (as puzzle_baselines.py)")
    ap.add_argument("--allow-stale-build", action="store_true")
    ap.add_argument("--forge", default=None, help="Forge checkout (default $FORGE_HOME or ~/code/forge)")
    ap.add_argument("--pzl", required=True, help="a puzzle with an [acceptance] section")
    ap.add_argument("--prefix", default="", help='the steps so far, "Seat:bound:value ..." (default: none)')
    ap.add_argument("--options", action="store_true", help="run one probe per value of the next decision")
    ap.add_argument("--max-options", type=int, default=MAX_OPTIONS)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--completion-seed", type=int, default=0)
    ap.add_argument("--opponent", choices=("ai", "search"), default="ai",
                    help="search is needed for a prefix with an Opponent: step, or to read an Opponent decision")
    ap.add_argument("--out-dir", default=None, help="one subdirectory per probe (default runs/puzzles/walk/<puzzle>)")
    a = ap.parse_args(argv)
    pzl = Path(a.pzl).resolve()
    pb.require_acceptance_section(pzl)
    build = pb.Build(a.build, a.forge, allow_stale=a.allow_stale_build)
    try:
        walk(build, pzl, a.prefix, a.opponent, a.seed, a.completion_seed, a.out_dir, a.options, a.max_options)
    except (WalkError, RuntimeError) as e:
        print("probe_walk: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
