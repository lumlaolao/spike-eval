#!/usr/bin/env python3
"""Select a SCOREABLE subset of verified positions.

Why this exists
---------------
The first real suite (46 verified keys, 30 distinct) could be answered without
reading the board. A policy that replies "1" every time scored **0.70**
combined and **0.92** on the exhaustive lane, against a 0.35 random baseline.
That is not a model result waiting to happen, it is a broken measurement: the
engine emits menu options in its own order, the strong play tends to land at
slot 0, and nothing shuffles them.

Two ways to fix that. Shuffling at presentation time is the general answer, but
it is a prompt-contract redesign -- `prompt_contract.render` uses
`row["prompt"]` VERBATIM, so the numbered menu is baked into the string the
model sees, and `pass_option_index` is hardcoded to `n_options - 1`. Changing
that changes `contract_sha` and every artifact's comparability.

This module takes the other route, which needs no contract change at all:
choose *which* positions go in the suite so the answer position is already
balanced. It is strictly a selector. It never edits a key, never re-scores, and
never relaxes a gate -- a position it drops is one nobody may quote, not one
that becomes easier.

The cost is honest and worth stating: balancing SHRINKS the suite. A pool that
is 92% answer-at-slot-0 cannot yield a large balanced suite. A small suite with
a floor at chance is worth more than a large one a constant can beat -- the
project's own rule is to prefer metrics whose trivial baseline is near zero.

What "balanced" means here
--------------------------
The target is `always_first <= random_baseline` (each position's random
baseline is 1/n_options; the suite's is their mean). Positions whose answer is
NOT at slot 0 are all kept, then answer-at-slot-0 positions are admitted up to
the largest count that still satisfies the target:

    k / (M + k) <= t   ->   k <= t*M / (1 - t)

with M = number of non-first-answer positions and t the target ratio. Ties are
broken deterministically by position id, so the same pool always yields the
same suite.

Duplicates are collapsed first (same option text = same decision mined from two
seeds), because a repeated position inflates the count while measuring one
skill twice.
"""

import argparse
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import verify_keys as vk  # noqa: E402  (floors live there; do not re-derive)


def answer_index(row):
    """First option index scoring 10, or None when the row has no usable key.

    'First' matters: a key may mark several options 10 (the CI-overlap tie
    rule in score_from_wp does exactly that). A model answering the earliest
    correct slot is the strongest constant policy, so that is the index the
    positional floor must be measured against.
    """
    ak = row.get("answer_key") or {}
    scores = ak.get("option_scores")
    if not isinstance(scores, list) or not scores:
        return None
    for i, s in enumerate(scores):
        if s == 10:
            return i
    return None


def option_signature(row):
    """Identity of a DECISION, not of a row: option text before the
    reminder-text bracket, so the same position mined from two seeds collapses
    to one entry."""
    return tuple(str(o).split(" [")[0] for o in (row.get("options") or []))


def dedupe(rows):
    """Keep one row per distinct option signature, lowest id first so the
    choice does not depend on input order."""
    best = {}
    for r in sorted(rows, key=lambda r: str(r.get("id") or "")):
        sig = option_signature(r)
        if sig and sig not in best:
            best[sig] = r
    return [best[k] for k in sorted(best, key=lambda s: str(best[s].get("id") or ""))]


def random_baseline(rows):
    """Mean 1/n_options over rows — what uniform guessing scores."""
    ns = [len(r.get("options") or []) for r in rows]
    ns = [n for n in ns if n]
    if not ns:
        return None
    return sum(1.0 / n for n in ns) / len(ns)


def max_first_allowed(n_non_first, target):
    """Largest k with k/(n_non_first+k) <= target. 0 when target <= 0."""
    if target is None or target <= 0.0:
        return 0
    if target >= 1.0:
        return None                      # unbounded
    # +1e-9 before flooring: the exact value is often just under an integer in
    # binary (t=1/3, M=10 gives 4.999999999999999, not 5), and truncating there
    # would drop a position the constraint actually admits.
    return int(math.floor(target * n_non_first / (1.0 - target) + 1e-9))


def is_pass_answer(row):
    """True when answering PASS scores 10 on this position.

    Note "ANY option scoring 10", not "the first". A key may mark several
    options 10 -- score_from_wp's CI-overlap tie rule does exactly that -- and
    a model that always replies PASS banks the 10 whenever the PASS slot is one
    of them, regardless of what else ties. This MUST match
    verify_keys.suite_composition's always_pass definition: when it did not
    (this used first-10-only), the selector believed it had 2 PASS answers
    while the floor it was optimising against counted 5, and it shipped a suite
    at always_pass 0.33 against a 0.27 target while reporting success. A second
    drifting copy of a rule is worse than no copy.
    """
    scores = (row.get("answer_key") or {}).get("option_scores")
    if not isinstance(scores, list):
        return False
    for i, o in enumerate(row.get("options") or []):
        if i < len(scores) and scores[i] == 10 and \
                str(o).strip().upper().startswith("PASS"):
            return True
    return False


def _max_total(n_first, n_pass, n_other, t_first, t_pass):
    """Largest suite size N satisfying BOTH floors at once.

    Balancing position alone is not enough, and this is not hypothetical: on
    the first real suite it pushed always_first 0.63 -> 0.27 while dragging
    always_pass 0.23 -> 0.33. PASS is the last option, so throwing out
    answer-at-slot-0 positions preferentially keeps PASS answers -- and a suite
    where a third of the answers are PASS re-creates the original 88% failure
    exactly. Fixing one floor by breaking another is not progress.

    Positions fall in three disjoint groups (a first-slot answer is never the
    PASS slot unless n_options == 1): `first` (answer at slot 0), `pass`
    (non-first, answer is PASS) and `other`. All of `other` is always kept; the
    other two are admitted up to their caps. Scan N downward and take the
    largest feasible one -- the numbers here are tens, so this is free.
    """
    for total in range(n_first + n_pass + n_other, n_other - 1, -1):
        if total <= 0:
            continue
        a = min(n_first, int(math.floor(t_first * total + 1e-9)))
        c = min(n_pass, int(math.floor(t_pass * total + 1e-9)))
        if n_other + a + c >= total:
            return total, a, c
    return n_other, 0, 0


def select(rows, target=None, dedupe_first=True, target_pass=None):
    """-> (selected_rows, report). Pure; no I/O."""
    usable = [r for r in rows
              if r.get("status") == "verified" and answer_index(r) is not None]
    pool = dedupe(usable) if dedupe_first else list(usable)

    before = vk.suite_composition(
        [{"verified": True, "options": r.get("options"),
          "option_scores": (r.get("answer_key") or {}).get("option_scores")}
         for r in pool])

    if target is None:
        target = random_baseline(pool)

    if target_pass is None:
        target_pass = random_baseline(pool)

    first = [r for r in pool if answer_index(r) == 0]
    passers = [r for r in pool if answer_index(r) != 0 and is_pass_answer(r)]
    other = [r for r in pool
             if answer_index(r) != 0 and not is_pass_answer(r)]
    total, cap_a, cap_c = _max_total(
        len(first), len(passers), len(other),
        target if target is not None else 1.0,
        target_pass if target_pass is not None else 1.0)
    # Select EXACTLY `total`. Taking other+first[:cap_a]+passers[:cap_c]
    # outright can overshoot `total` (the caps are computed AT `total`, and
    # their sum may exceed it), which silently breaks the very ratios the
    # solver just satisfied -- it shipped a suite at always_pass 0.33 against a
    # 0.27 target. Fill up to `total` and stop.
    n_a = max(0, min(cap_a, total - len(other)))
    n_c = max(0, min(cap_c, total - len(other) - n_a))
    selected = sorted(other + first[:n_a] + passers[:n_c],
                      key=lambda r: str(r.get("id") or ""))

    after = vk.suite_composition(
        [{"verified": True, "options": r.get("options"),
          "option_scores": (r.get("answer_key") or {}).get("option_scores")}
         for r in selected])

    report = {
        "schema": "suite-selection.v1",
        "target_always_first": target,
        "target_always_pass": target_pass,
        "n_in": len(rows),
        "n_verified_usable": len(usable),
        "n_after_dedupe": len(pool),
        "n_selected": len(selected),
        "n_dropped_for_balance": (len(first) - n_a) + (len(passers) - n_c),
        "n_dropped_first": len(first) - n_a,
        "n_dropped_pass": len(passers) - n_c,
        "before": before,
        "after": after,
    }
    return selected, report


def _read(paths):
    rows = []
    for p in paths:
        try:
            text = Path(p).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print("cannot read %s: %s" % (p, exc), file=sys.stderr)
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Select a positionally-balanced puzzle suite. "
                    "Never edits or re-scores a key -- it only chooses which "
                    "verified positions may be quoted.")
    ap.add_argument("positions", nargs="+", help="verified positions jsonl")
    ap.add_argument("--out", required=True, help="selected positions jsonl")
    ap.add_argument("--report", default=None, help="selection report json")
    ap.add_argument("--target-always-first", type=float, default=None,
                    help="ceiling for the always-first floor "
                         "(default: the pool's own random baseline)")
    ap.add_argument("--target-always-pass", type=float, default=None,
                    help="ceiling for the always-PASS floor (default: the "
                         "pool's random baseline). Balanced JOINTLY with "
                         "--target-always-first: fixing position alone drove "
                         "always_pass 0.23 -> 0.33 on the first real suite.")
    ap.add_argument("--no-dedupe", action="store_true",
                    help="keep duplicate positions (NOT recommended: they "
                         "inflate the count while measuring one skill twice)")
    args = ap.parse_args(argv)

    rows = _read(args.positions)
    selected, report = select(rows, args.target_always_first,
                              dedupe_first=not args.no_dedupe,
                              target_pass=args.target_always_pass)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(str(out), "w", encoding="utf-8") as f:
        for r in selected:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    rp = Path(args.report) if args.report else out.parent / "selection.json"
    with open(str(rp), "w", encoding="utf-8") as f:
        f.write(json.dumps(report, sort_keys=True, indent=2) + "\n")

    b, a = report["before"], report["after"]

    def fmt(x):
        return "n/a" if x is None else ("%.2f" % x)
    print("pool %d -> selected %d (dropped %d answer-at-slot-0 for balance)"
          % (report["n_after_dedupe"], report["n_selected"],
             report["n_dropped_for_balance"]), file=sys.stderr)
    print("  always_first %s -> %s   (random %s -> %s)"
          % (fmt(b.get("always_first_baseline")),
             fmt(a.get("always_first_baseline")),
             fmt(b.get("random_baseline")), fmt(a.get("random_baseline"))),
          file=sys.stderr)
    print("  always_pass  %s -> %s"
          % (fmt(b.get("always_pass_baseline")),
             fmt(a.get("always_pass_baseline"))), file=sys.stderr)
    # Compare against the targets that were USED. The selected suite's own
    # random baseline shifts with its n_options mix, so checking against the
    # post-hoc value made this warn on a target it had met (0.2667 achieved
    # vs 0.2732 asked) -- and a warning that cries wolf gets ignored.
    missed = []
    af, ap = a.get("always_first_baseline"), a.get("always_pass_baseline")
    tf, tp = report.get("target_always_first"), report.get("target_always_pass")
    if af is not None and tf is not None and af > tf + 1e-9:
        missed.append("always_first %.3f > %.3f" % (af, tf))
    if ap is not None and tp is not None and ap > tp + 1e-9:
        missed.append("always_pass %.3f > %.3f" % (ap, tp))
    if missed:
        print("WARNING: target not reached (%s) — the pool is too skewed. "
              "The selected suite is still beatable by a constant; widen the "
              "candidate pool rather than quoting a score against it."
              % "; ".join(missed), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
