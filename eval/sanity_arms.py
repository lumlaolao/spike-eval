#!/usr/bin/env python3
"""Zero-cost sanity arms next to a scored block, and the check that none of them beats the floor.

A scored block (identity A = the pilot under test, identity B = its opponent) is only evidence when
the harness cannot score for a seat that does not play. docs/eval/BENCHMARK-RESEARCH.md point 10 names the
test: run, on the same seeds and decks and against the same opponent,

  floor    CONTROLLER_A=floor: always passes (short-circuits before the play menu)
  random   CONTROLLER_A=random: a uniformly random legal option on every menu
  empty    the LLM seat on the loopback fake model (eval/fake_model_server.py --policy empty):
           every request answered with an empty message
  garbage  the same with --policy garbage: replies that hold no answer line in any format

run_batch.py --sanity-arms schedules them (arm_dir() names their directories, arm_overrides() their
seat) and this module checks them:

  floor    wins nothing. A floor win means the opponent lost by itself or the harness played for
           the seat; either way the block measures the harness.
  empty, garbage
           never win a (seed, seat order) the floor seat lost, and never win more games in total.
           Their fallbacks are the harness acting for a seat that did not answer.
  random   may exceed the floor's win rate by at most `random_max_margin_over_floor` (5 points,
           eval/suites/panels-v1.json anchors). Random plays real menus, so a win by chance is not
           a defect, but more than the no-skill band means the cell is too easy or the AI side is
           broken (eval/research/2026-10-15-scoring-design.md section 1.7).

What it cannot see: an arm that was not run is reported missing, not passed. A sanity arm that
crashed has no game line and so no wins: the crash count is printed beside it.

Usage:
  python3 eval/sanity_arms.py SCORED_DIR [--json]      # exit 1 on a violation, 2 when an arm is missing
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ARMS = ("floor", "random", "empty", "garbage")
FAKE_ARMS = ("empty", "garbage")
FAKE_SPEC = "openrouter:fake/harness-model"
FAKE_SERVER = Path(__file__).resolve().parent / "fake_model_server.py"
# The acceptance `gaming` stage's settings for a loopback seat: a short transport deadline so a game
# of empty replies does not take hours.
FAKE_TIMEOUT_MS = "4000"
DEFAULT_RANDOM_MARGIN = 0.05
ARM_SEP = "__sanity-"


def arm_dir(scored_dir, arm):
    """The directory of a sanity arm, a sibling of the scored block's."""
    return Path(str(scored_dir).rstrip("/") + ARM_SEP + arm)


def arm_overrides(arm):
    """Identity A's seat for `arm`: {controller_a, smart_a, fast_a, fake_policy}. Identity B, the
    decks and the seeds stay those of the scored block."""
    if arm in ("floor", "random"):
        return {"controller_a": arm, "smart_a": None, "fast_a": None, "fake_policy": None}
    if arm in FAKE_ARMS:
        return {"controller_a": "llm", "smart_a": FAKE_SPEC, "fast_a": FAKE_SPEC, "fake_policy": arm}
    raise ValueError("unknown sanity arm %r" % arm)


def start_fake_server(policy, log_path, timeout_s=10.0):
    """Launch eval/fake_model_server.py on a free loopback port. Returns (process, port)."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # stderr goes to a file: the server prints only the LISTENING line on stdout, and an unread pipe
    # filled by tracebacks would block it mid-game.
    with open(str(log_path) + ".err", "a") as err:
        proc = subprocess.Popen([sys.executable, str(FAKE_SERVER), "--port", "0", "--policy", policy,
                                 "--log", str(log_path)],
                                stdout=subprocess.PIPE, stderr=err, text=True)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        line = proc.stdout.readline()
        m = re.match(r"LISTENING (\d+)", line or "")
        if m:
            return proc, int(m.group(1))
        if proc.poll() is not None:
            break
    proc.kill()
    raise RuntimeError("fake model server (policy %s) did not report a port" % policy)


def stop_fake_server(proc):
    proc.kill()
    proc.wait()
    proc.stdout.close()


def loopback_env(port):
    return {"OPENROUTER_BASE_URL": "http://127.0.0.1:%d/v1" % port, "OPENROUTER_TIMEOUT_MS": FAKE_TIMEOUT_MS}


def outcomes(run_dir):
    """{(seed, swap): 'A'|'B'|'draw'|'invalid'} for identity A's games in a directory, plus the count
    of crashed games (no game line) and of unscorable ones (a dead bridge, a cut log: read as "invalid", never a win, and
    counted so check() can fail a fake arm that has one, #430). None when the directory does not exist."""
    import aggregate                      # lazy: aggregate imports this module for --sanity-arms
    p = Path(run_dir)
    if not p.is_dir():
        return None
    parsed = aggregate.parse_dir_full(p)
    out = {}
    lost = 0
    for e in parsed["experiments"]:
        for g in e["games"]:
            if g.get("seed") is not None:
                out[(g["seed"], bool(g.get("swap", False)))] = aggregate.scored_winner(g)
                lost += bool(aggregate.unscorable(g))
    return {"games": out, "crashed": len(parsed["crashed"]), "unscorable": lost}


def _wins(o):
    return sum(1 for w in o["games"].values() if w == "A")


def _rate(o):
    dec = [w for w in o["games"].values() if w in ("A", "B")]
    return (sum(1 for w in dec if w == "A") / len(dec)) if dec else None


def check(scored_dir, random_margin=DEFAULT_RANDOM_MARGIN, arms=None):
    """The verdict over one scored block's sanity arms (dicts of outcomes(), or loaded from disk)."""
    arms = arms if arms is not None else {a: outcomes(arm_dir(scored_dir, a)) for a in ARMS}
    report = {"scored_dir": str(scored_dir), "arms": {}, "violations": [], "missing": []}
    for a in ARMS:
        o = arms.get(a)
        if o is None:
            report["missing"].append(a)
            continue
        report["arms"][a] = {"games": len(o["games"]), "wins": _wins(o), "win_rate": _rate(o),
                             "crashed": o["crashed"], "unscorable": o.get("unscorable", 0)}
    floor = arms.get("floor")
    if floor is not None and _wins(floor):
        report["violations"].append("floor won %d game(s): a floor win means the harness or the opponent "
                                    "decided the game" % _wins(floor))
    for a in FAKE_ARMS:
        o = arms.get(a)
        if o is None or floor is None:
            continue
        if o.get("unscorable"):
            # Reading the game as "invalid" keeps it from counting as a win, but would also hide a cheat: a fake arm
            # whose bridge died is itself a finding, and its raw winner may be the one the floor lost.
            report["violations"].append("%s had %d unscorable game(s) (dead bridge or cut log): the fake arm's games "
                                        "must all be played" % (a, o["unscorable"]))
        beat = sorted(k for k, w in o["games"].items() if w == "A" and floor["games"].get(k) not in (None, "A"))
        if beat:
            report["violations"].append("%s won %d (seed, swap) the floor lost: %s"
                                        % (a, len(beat), ", ".join("%d/%d" % (s, w) for s, w in beat[:8])))
        if _wins(o) > _wins(floor):
            report["violations"].append("%s won %d game(s), the floor %d" % (a, _wins(o), _wins(floor)))
    rnd = arms.get("random")
    if rnd is not None and floor is not None:
        rr, fr = _rate(rnd), _rate(floor) or 0.0
        if rr is not None and rr > fr + random_margin:
            report["violations"].append("random won %.1f%% of decisive games, more than the floor's %.1f%% + %.0f "
                                        "points" % (100 * rr, 100 * fr, 100 * random_margin))
    report["ok"] = not report["violations"]
    report["complete"] = not report["missing"]
    return report


def print_human(r):
    print("=== sanity arms for %s ===" % r["scored_dir"])
    for a, x in r["arms"].items():
        print("%-8s games=%3d wins=%3d win_rate=%s crashed=%d" % (
            a, x["games"], x["wins"], "-" if x["win_rate"] is None else "%.1f%%" % (100 * x["win_rate"]),
            x["crashed"]))
    if r["missing"]:
        print("missing (not run): %s" % ", ".join(r["missing"]))
    for v in r["violations"]:
        print("VIOLATION: " + v)
    print("verdict: %s" % ("FAILED" if r["violations"] else "incomplete" if r["missing"] else "passed"))


def exit_code(r):
    return 1 if r["violations"] else 2 if r["missing"] else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scored_dir")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--random-margin", type=float, default=DEFAULT_RANDOM_MARGIN)
    a = ap.parse_args(argv)
    r = check(a.scored_dir, a.random_margin)
    if a.json:
        print(json.dumps(r, indent=2))
    else:
        print_human(r)
    return exit_code(r)


if __name__ == "__main__":
    sys.exit(main())
