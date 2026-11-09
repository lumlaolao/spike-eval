#!/usr/bin/env python3
"""The menu at the end of a misreading-probe prefix (docs/eval/PUZZLES.md, "The misreading probe"). Free: no model.

  python3 eval/puzzles/probe_walk.py --build DIR --pzl P.pzl --prefix "Solver:4:0" [--options] [--opponent search]

A probe line is "Seat:bound:value ..." and the bound of a step is the number of options at that decision, which is
not written anywhere until the game is run. This runs the prefix followed by a sentinel step the game cannot match
(`Solver:99:0`), and reads the next decision's seat and bound from the drift the probe reports ("draw K: line has
Solver:99:0, game asked Solver:B"). With --options it then runs the prefix plus each value 0..B-1 as its own probe
and prints, for each, the play it takes (the first [ROUTER] line of its jvm.log that differs from the other values')
and the probe verdict (puzzle_baselines.probe_verdict). One JVM per probe, run through puzzle_baselines.run_arm
and probe_env; the build and the lock around a JVM are the caller's (this never compiles).

The verdict of a value is for the prefix plus that one step plus ONE seeded random completion: a menu, not a proof.
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


def next_decision(row, n_prefix, sentinel):
    """(seat, bound) of the first decision after the prefix, from the sentinel probe's row; None when the sentinel
    was matched (the game really asked for that many options)."""
    if not row:
        raise WalkError("the sentinel probe wrote no result row")
    if row.get("probe_stopped") or row.get("status") == "invalid":
        raise WalkError("the sentinel probe was ended by the draw cap or the wall clock: " + pb.probe_verdict(row))
    drift = row.get("search_drift") or ""
    m = DRIFT.match(drift)
    if m:
        at = int(m.group(1))
        if at != n_prefix:
            raise WalkError("the prefix itself drifted at step %d of %d: %s" % (at, n_prefix, drift))
        return m.group(2), int(m.group(3))
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
    """For each run's router lines, the play it takes: the first "play: ..." line past the lines every run shares (the
    prefix's own plays and the menu's header, which all values print alike), else the first line past them (a cast
    prints its "target:" line before its "play: casts"). None for a run with no line past them, and for every run
    when the values are all the same play or only one ran."""
    shared = 0
    while all(len(r) > shared for r in runs) and len({r[shared] for r in runs}) == 1:
        shared += 1
    return [next((l for l in r[shared:] if l.startswith("play: ")), r[shared]) if len(r) > shared else None
            for r in runs]


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
        found = next_decision(row, n_prefix, sentinel)
        if found:
            break
    else:
        raise WalkError("the game asks for both %s options at once: not a menu this can read" % (SENTINEL_BOUNDS,))
    seat, bound = found
    say("%s prefix %r: next decision %s:%d (%d options)" % (Path(pzl).stem, prefix, seat, bound, bound))
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
    if not re.search(r"^\s*\[acceptance\]\s*$", pzl.read_text(encoding="utf-8"), re.I | re.M):
        raise SystemExit("%s has no [acceptance] section: a probe has nothing to read its final state against" % pzl)
    if a.prefix.strip():
        pb.check_probe_line(a.prefix)
    build = pb.Build(a.build, a.forge, allow_stale=a.allow_stale_build)
    try:
        walk(build, pzl, a.prefix, a.opponent, a.seed, a.completion_seed, a.out_dir, a.options, a.max_options)
    except WalkError as e:
        print("probe_walk: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
