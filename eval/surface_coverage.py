#!/usr/bin/env python3
"""Every engine hook the seat answers is proven by a test that reached it (owner, 2026-10-16).

The Java tests run with -Dharness.surface.report=<file>; each decision row records the PlayerController hooks
on its call stack, "hook<TAB>asked" (the model was asked) or "hook<TAB>auto" (a forced or fallback pick).
This compares that report with the ownership ledger:

  - a decision hook (kind strategic or payment, impl LLM) must be reached with the model asked;
  - a hook listed in the gap file (eval/surface_gaps.txt, "hook<TAB>reason") is not yet proven: it is
    reported, and the list may only shrink: a listed hook that is now asked is a stale gap and fails, so
    the list is kept true;
  - information and mechanics hooks (reveal, notices, the shuffle) make no decision and are proven by
    their own tests, not here.

Usage: surface_coverage.py <surfaces.tsv> <controller-ownership.tsv> <surface_gaps.txt>
Exit 0 when every decision hook is asked or a listed gap; 1 otherwise, with the problems printed.
"""
import sys

DECISION_KINDS = {"strategic", "payment"}


def read_report(path):
    seen = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        hook, kind = line.split("\t")
        seen.setdefault(hook, set()).add(kind)
    return seen


def decision_hooks(ledger_path):
    hooks = set()
    for i, line in enumerate(open(ledger_path, encoding="utf-8")):
        f = line.rstrip("\n").split("\t")
        if i == 0 or len(f) < 6:
            continue
        if f[1] == "LLM" and f[2] in DECISION_KINDS:
            hooks.add(f[0].split("[")[0])
    return hooks


def read_gaps(path):
    gaps = {}
    for line in open(path, encoding="utf-8"):
        if not line.strip() or line.startswith("#"):
            continue
        hook, _, reason = line.rstrip("\n").partition("\t")
        gaps[hook] = reason
    return gaps


def check(report, hooks, gaps):
    """(problems, asked, open_gaps)"""
    problems = []
    asked = sorted(h for h in hooks if "asked" in report.get(h, ()))
    for h in sorted(hooks):
        if h in asked:
            if h in gaps:
                problems.append("STALE GAP: %s is now asked by a test; remove it from the gap file" % h)
        elif h not in gaps:
            how = "only forced or fallback picks" if h in report else "never reached"
            problems.append("UNPROVEN: %s (%s) has no test that asks the model" % (h, how))
    for h in sorted(gaps):
        if h not in hooks:
            problems.append("UNKNOWN GAP: %s is not a decision hook in the ledger" % h)
    return problems, asked, sorted(h for h in gaps if h in hooks and h not in asked)


def main(argv):
    if len(argv) != 4:
        print(__doc__)
        return 2
    report, hooks, gaps = read_report(argv[1]), decision_hooks(argv[2]), read_gaps(argv[3])
    problems, asked, open_gaps = check(report, hooks, gaps)
    print("surfaces: %d decision hooks, %d proven asked, %d open gaps (%s)"
          % (len(hooks), len(asked), len(open_gaps), ", ".join(open_gaps) or "none"))
    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
