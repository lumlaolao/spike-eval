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
  rules errors; `categories`: the rule each refused proposal broke (the engine's diagnosis, recorded under
  PROPOSE_TERSE too, where the seat is told only "ILLEGAL").
- `repair`: the decisions a rules refusal opened, and whether each became a play, a pass (always legal, so not rules
  knowledge) or nothing; the same action sent again after a refusal (`exact_repeats`). `refusal_text`: terse or
  detailed, from the rows' `error_shown`. Few decisions are opened on most positions: say the n beside any rate.
- Each rate has its Wilson 95% interval (`*_ci`).

The attack and the blocks are proposed under the same arm (harness-epoch-78, `declare_attackers` / `declare_blockers`).
Their rows (`type` attackers / blockers) get their own sheets, `attack` and `block` beside the play sheet, with the same
fields: an empty declaration is a combat proposal's pass (attacking or blocking with none is almost always legal), so
their headline is first-try legality of non-empty declarations. They are never pooled with the play headline.

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
    if not isinstance(row, dict) or not isinstance(row.get("proposals"), list):
        return False
    # AnswerTool counts invalid replies, not calls (a reply may hold two refused calls): `answer_invalid` says how many.
    try:
        invalid = int(row.get("answer_invalid") or 0)
    except (TypeError, ValueError):
        invalid = 0
    return row.get("answer_via") == "lost" and invalid >= TRIES


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
                        r["_game"] = str(f)   # one file is one game: repair's later-decision repeats stay in it
                        yield r


COMBAT_FIELDS = {"declare_attackers": "attack", "declare_blockers": "block"}
KINDS = {"attackers": "attack", "blockers": "block"}  # a row's `type` -> its sheet; anything else is a play
# What a decision lost to four refusals became: the arm's fixed fallback for each kind (render and match_review).
LOST_AS = {"play": "played as PASS", "attack": "declared none",
           "block": "took the fixed fallback blocks (none, or the smallest legal set under a requirement)"}


def kind_of(row):
    return KINDS.get(row.get("type"), "play")


def verb_of(p):
    """A proposal's verb: the play's, or for a combat declaration "attack" / "block", and "pass" when it is empty."""
    if not isinstance(p, dict):
        return None
    for field, verb in COMBAT_FIELDS.items():
        if isinstance(p.get(field), list):
            return verb if p[field] else "pass"
    a = p.get("action")
    return a.get("verb") if isinstance(a, dict) else None


def move_of(p):
    """What a proposal proposed, the same for two identical proposals: its `action`, or its combat declaration."""
    if not isinstance(p, dict):
        return None
    for field in ("action",) + tuple(COMBAT_FIELDS):
        if field in p:
            return {field: p[field]}
    return None


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
    out["repair"] = repair(decisions)
    shown = [p for r in decisions for p in r["proposals"] if p.get("verdict") == "illegal"]
    out["refusal_text"] = (None if not shown else "terse" if all(p.get("error_shown") for p in shown)
                           else "detailed" if not any(p.get("error_shown") for p in shown) else "mixed")
    return out


def repair(decisions):
    """After a rules refusal: decisions whose first well-formed proposal was refused by a rule (`opened`), and what
    each became: a play (`to_play`; for combat, a non-empty declaration), a pass (`to_pass`; for combat, an empty
    one: always legal, so a repair to it is not rules knowledge), or nothing accepted (`unrepaired`);
    `within`: the share accepted by try k, counted as legal_within counts tries. `exact_repeats`: refused proposals
    whose next try in the same decision sent the same action (Gumaan 2026's repeat), out of `followed`, the refused
    proposals with a next try. `refused_again`: of the distinct actions refused in a game (`refused_distinct`), those
    refused again in a later decision of the same game: the refusal taught nothing that lasted past its decision.
    Rows are read in file order, which is the game's order."""
    out = {"opened": 0, "to_play": 0, "to_pass": 0, "unrepaired": 0, "followed": 0, "exact_repeats": 0}
    at = Counter()
    first, again = {}, set()   # (game, action) -> the decision it was first refused at; those refused in a later one
    for i, r in enumerate(decisions):
        ps = r["proposals"]
        for p in ps:
            if p.get("verdict") != "illegal":
                continue
            key = (r.get("_game"), json.dumps(move_of(p), sort_keys=True))
            if key not in first:
                first[key] = i
            elif first[key] < i:
                again.add(key)
        for p, nxt in zip(ps, ps[1:]):
            if p.get("verdict") == "illegal":
                out["followed"] += 1
                out["exact_repeats"] += move_of(p) == move_of(nxt)
        formed = [p for p in ps if p.get("verdict") != "format"]
        if not formed or formed[0].get("verdict") != "illegal":
            continue
        out["opened"] += 1
        k = next((j for j, p in enumerate(ps) if p.get("verdict") == "accepted"), None)
        if k is None:
            out["unrepaired"] += 1
            continue
        out["to_pass" if verb_of(ps[k]) == "pass" else "to_play"] += 1
        at[k] += 1
    out["refused_distinct"], out["refused_again"] = len(first), len(again)
    n = out["opened"]
    out["to_play_share"] = round(out["to_play"] / n, 4) if n else None
    out["to_play_ci"] = [round(x, 4) for x in wilson_ci(out["to_play"], n)] if n else None
    out["within"] = {str(k + 1): round(sum(at[i] for i in range(k + 1)) / n, 4) if n else None for k in range(1, TRIES)}
    out["exact_repeat_share"] = round(out["exact_repeats"] / out["followed"], 4) if out["followed"] else None
    return out


def audit(paths):
    """Per seat: the play sheet, and an `attack` / `block` sheet when the seat proposed combat."""
    by_seat = defaultdict(lambda: defaultdict(list))
    for r in rows(paths):
        by_seat[r.get("seat", "?")][kind_of(r)].append(r)
    out = {}
    for seat, kinds in sorted(by_seat.items()):
        out[seat] = summarize(kinds.get("play", []))
        for kind in ("attack", "block"):
            if kinds.get(kind):
                out[seat][kind] = summarize(kinds[kind])
    return out


def render_one(s, what, null_move, fallback):
    return [
        "  HEADLINE first-try legal, %s: %s %s (n=%d)"
        % (what, s["first_try_legal_nonpass"], s["first_try_legal_nonpass_ci"], s["nonpass_first_proposals"]),
        "  the same, a malformed first try counted as not legal: %s %s"
        % (s["first_try_legal_nonpass_strict"], s["first_try_legal_nonpass_strict_ci"]),
        "  first-try legal, all: %s %s  (%s first: %s; a seat that only does that scores 1.0)"
        % (s["first_try_legal"], s["first_try_legal_ci"], null_move, s["pass_first_share"]),
        "  legal within 1/2/3/4 tries: %s" % " / ".join(str(s["legal_within"][str(k)]) for k in range(1, TRIES + 1)),
        "  lost (all %d tries refused, %s): %d; lost another way: %d; format errors: %d"
        % (TRIES, fallback, s["lost"], s["lost_other"], s["format_errors"]),
    ] + (["  refused by rule: " + ", ".join("%s %d" % kv for kv in s["categories"].items())]
         if s["categories"] else []) + repair_lines(s)


def repair_lines(s):
    rp = s["repair"]
    if not rp["opened"] and not rp["refused_distinct"]:
        return []
    return ["  repair after a rules refusal (%s text): %d opened; to a move %d %s %s, to the null move %d, "
            "none accepted %d; accepted by try 2/3/4: %s; exact repeats %d of %d; "
            "refused again in a later decision %d of %d"
            % (s["refusal_text"], rp["opened"], rp["to_play"], rp["to_play_share"], rp["to_play_ci"], rp["to_pass"],
               rp["unrepaired"], " / ".join(str(rp["within"][str(k)]) for k in range(2, TRIES + 1)),
               rp["exact_repeats"], rp["followed"], rp["refused_again"], rp["refused_distinct"])]


def render(sheet):
    if not sheet:
        return "no proposal rows (not a PROPOSE_ACTIONS run?)"
    lines = []
    for seat, s in sheet.items():
        lines.append("%s: %d play decisions, %d proposals" % (seat, s["decisions"], s["proposals"]))
        lines += render_one(s, "non-pass", "pass", LOST_AS["play"])
        for kind in ("attack", "block"):
            if kind in s:
                k = s[kind]
                lines.append("%s %s: %d decisions, %d proposals" % (seat, kind, k["decisions"], k["proposals"]))
                lines += render_one(k, "non-empty", "an empty declaration", LOST_AS[kind])
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
