#!/usr/bin/env python3
"""The PROPOSE_ACTIONS sheet: how legal a seat's proposed plays were when it was not shown the legal plays.

Usage: python3 eval/proposal_audit.py <run dir or decision .jsonl> [...] [--json]

Reads the play rows the arm writes (`proposals`, `first_try_legal`: docs/harness/ARCHITECTURE.md, "The propose
condition") and reports, per seat:

- `first_try_legal_nonpass`: the HEADLINE. Of decisions whose first well-formed proposal was not a pass, the share that
  was legal. A pass is always legal, so the all-proposals rate has a trivial floor of 100% for a seat that only passes
  (the 88% lesson, .claude/rules/eval.md); its pass share is printed beside it, never alone.
- `first_try_legal_nonpass_strict`: the same, with a malformed first try that names a play (a card not in view, an
  ability out of range, a missing target) counted as not legal; `nonpass_first_proposals` is the headline's n.
- `first_try_legal`: over every decision with a well-formed proposal, passes included, beside `pass_first_share`.
- `legal_within`: the share of decisions with a legal proposal by the 1st, 2nd, 3rd and 4th try.
- `lost`: decisions whose four tries were all refused (played as PASS), and `lost_other`: decisions with no accepted
  proposal lost another way (no `answer` call after a refusal); `format_errors`: malformed proposals, not counted as
  rules errors; `categories`: the rule each refused proposal broke.
- Each rate has its Wilson 95% interval (`*_ci`).

Stdlib only. A row from a menu game has no `proposals` and is skipped.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import evalpath  # noqa: F401
from aggregate import wilson_ci

TRIES = 4  # AnswerTool.MAX_INVALID_PROPOSALS (test_proposal_audit pins the two equal)


def lost_to_refusals(row):
    """True when a play decision was lost because every one of its tries was refused (a rules or a form error): the
    model's error, played as PASS by the arm. A decision lost any other way (no `answer` call after a refusal) stays a
    lost answer. match_review's PROPOSAL finding and aggregate's harness-decided gate share this rule."""
    ps = row.get("proposals") if isinstance(row, dict) else None
    return (isinstance(ps, list) and len(ps) >= TRIES
            and not any(isinstance(p, dict) and p.get("verdict") == "accepted" for p in ps))


def rows(paths):
    """Every decision row with proposals, from run directories or .jsonl files."""
    for p in paths:
        p = Path(p)
        files = sorted(p.rglob("*.jsonl")) if p.is_dir() else [p]
        for f in files:
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line.startswith("{"):
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if r.get("t") == "decision" and isinstance(r.get("proposals"), list):
                        yield r


def verb_of(p):
    a = p.get("action") if isinstance(p, dict) else None
    return a.get("verb") if isinstance(a, dict) else None


def summarize(decisions):
    """The sheet for one seat's proposal rows."""
    n = len(decisions)
    out = {"decisions": n, "proposals": 0, "format_errors": 0, "lost": 0, "lost_other": 0, "categories": {}}
    first, first_nonpass, strict, pass_first = [], [], [], 0
    within = [0] * TRIES
    cats = Counter()
    for r in decisions:
        ps = r["proposals"]
        out["proposals"] += len(ps)
        out["format_errors"] += sum(1 for p in ps if p.get("verdict") == "format")
        cats.update(p.get("category") for p in ps if p.get("verdict") == "illegal")
        accepted_at = next((i for i, p in enumerate(ps) if p.get("verdict") == "accepted"), None)
        if accepted_at is None:
            out["lost" if lost_to_refusals(r) else "lost_other"] += 1
        else:
            for k in range(accepted_at, TRIES):
                within[k] += 1
        if ps and verb_of(ps[0]) not in (None, "pass"):
            strict.append(ps[0].get("verdict") == "accepted")
        formed = [p for p in ps if p.get("verdict") != "format"]
        if formed:
            legal = formed[0].get("verdict") == "accepted"
            first.append(legal)
            if verb_of(formed[0]) == "pass":
                pass_first += 1
            else:
                first_nonpass.append(legal)
    rate = lambda xs: round(sum(xs) / len(xs), 4) if xs else None
    out["first_try_legal"] = rate(first)
    out["pass_first_share"] = round(pass_first / len(first), 4) if first else None
    out["first_try_legal_nonpass"] = rate(first_nonpass)
    out["nonpass_first_proposals"] = len(first_nonpass)
    out["first_try_legal_nonpass_strict"] = rate(strict)
    for key, xs in (("first_try_legal_nonpass", first_nonpass), ("first_try_legal_nonpass_strict", strict),
                    ("first_try_legal", first)):
        out[key + "_ci"] = [round(x, 4) for x in wilson_ci(sum(xs), len(xs))] if xs else None
    out["legal_within"] = {str(k + 1): round(within[k] / n, 4) if n else None for k in range(TRIES)}
    out["categories"] = dict(cats.most_common())
    return out


def audit(paths):
    by_seat = defaultdict(list)
    for r in rows(paths):
        by_seat[r.get("seat", "?")].append(r)
    return {seat: summarize(rs) for seat, rs in sorted(by_seat.items())}


def render(sheet):
    if not sheet:
        return "no proposal rows (not a PROPOSE_ACTIONS run?)"
    lines = []
    for seat, s in sheet.items():
        lines.append("%s: %d play decisions, %d proposals" % (seat, s["decisions"], s["proposals"]))
        lines.append("  HEADLINE first-try legal, non-pass: %s %s (n=%d)"
                     % (s["first_try_legal_nonpass"], s["first_try_legal_nonpass_ci"], s["nonpass_first_proposals"]))
        lines.append("  the same, a malformed first try counted as not legal: %s %s"
                     % (s["first_try_legal_nonpass_strict"], s["first_try_legal_nonpass_strict_ci"]))
        lines.append("  first-try legal, all: %s %s  (pass first: %s; a seat that only passes scores 1.0)"
                     % (s["first_try_legal"], s["first_try_legal_ci"], s["pass_first_share"]))
        lines.append("  legal within 1/2/3/4 tries: %s" % " / ".join(str(s["legal_within"][str(k)]) for k in range(1, TRIES + 1)))
        lines.append("  lost (all %d tries refused, played as PASS): %d; lost another way: %d; format errors: %d"
                     % (TRIES, s["lost"], s["lost_other"], s["format_errors"]))
        if s["categories"]:
            lines.append("  refused by rule: " + ", ".join("%s %d" % kv for kv in s["categories"].items()))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    sheet = audit(a.paths)
    print(json.dumps(sheet, indent=2) if a.json else render(sheet))
    return 0


if __name__ == "__main__":
    sys.exit(main())
