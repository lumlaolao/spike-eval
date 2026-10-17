#!/usr/bin/env python3
"""Plan-vs-action audit: does the seat do what it just said it would do?

Reads recorded games (EVAL_OUT JSONL, or a game log) and joins each stated turn plan with the play
actions the same seat took in the same turn. It flags one thing with confidence and reports one
thing as context:

  HOLD-VIOLATION  the plan says to hold / keep / save / play-later a card, and that card was cast or
                  activated in the same turn. Seven of these in one game is how plan-then-execute's
                  prose matching was caught (docs/harness/ARCHITECTURE-REVIEW.md, problem 5).
  unplanned       an action on a card the plan never names. NOT a defect by itself: plans are one
                  line. It is reported as a rate so a change in it is visible.

The match is lexical (clause + card name), not a judgment of play quality. It cannot see a plan that
is wrong, only an action that contradicts the plan as written. Stdlib only.

  python3 eval/plan_audit.py runs/some-arm/            # every *.jsonl in the directory
  python3 eval/plan_audit.py --log /tmp/gui-spike.log  # a game log without EVAL_OUT
"""
import argparse
import glob
import json
import os
import re
import sys

# `later` alone is not a hold signal: "use it later" describes a use, not a decision to hold
# (runs/loop/g03 T9 flagged "playing Wasteland ... use it later to destroy their land" as a hold on
# Wasteland the seat then played). "hold"/"save"/"keep"/"next" carry the instruction; a bare
# "later" does not.
HOLD = re.compile(
    r"\b(hold|holding|held|keep|keeping|save|saving|next|dead|don'?t (?:cast|play|use)|"
    r"not (?:cast|play|use)|without (?:casting|using))\b", re.I)
# `and` coordinating two VERBS joins two instructions: "play Wasteland and hold Swords to
# Plowshares" holds the second, and a play of the first is not a violation (runs/loop/g02 T6). Split
# only when a verb follows, so that `and` joining two OBJECTS of one verb ("hold Swords to
# Plowshares and Goblin Bombardment") stays one clause and the hold still covers both -- splitting
# those suppressed real violations (runs/lines/ctl2/native-lookahead).
# `so` introduces the consequence, not the instruction: "keep targeting Illusionist to mill the
# library so Narcomoebas and Dread Return fill the graveyard" holds the Illusionist and merely names
# the payoff, so the Dread Return play is not a violation (runs/loop/g05 T5).
VERBS = (r"hold|holding|held|keep|keeping|save|saving|cast|casts|casting|play|plays|playing|"
         r"attack|attacks|attacking|deploy|deploys|deploying|activate|activates|activating|"
         r"use|uses|using|discard|discards|target|targets|sacrifice|sacrifices")
CLAUSE = re.compile(r"[;.]\s|,\s|\bthen\b|\bwhile\b|\bbut\b|\bso\b|\band\s+(?=(?:%s)\b)" % VERBS, re.I)
LOG_PLAN = re.compile(r"^\[(?P<seat>[^\]]+)\] \[ROUTER\] plan: (?P<plan>.*)$")
LOG_PLAY = re.compile(r"^\[(?P<seat>[^\]]+)\] \[ROUTER\] play: (?P<how>casts|plan step \d+(?: \([^)]*\))? ->) (?P<label>.*)$")


def card_name(label):
    """The card a menu option or log label refers to: 'CAST Shallow Grave @6 1{B} [..]' -> Shallow Grave."""
    s = re.sub(r"^(CAST|ACTIVATE|PLAY LAND)\s+", "", (label or "").strip())
    s = re.split(r"\s+@\d+|\s+\[|\s+\d*\{|\s+\(from |\s+—", s, maxsplit=1)[0]
    s = re.sub(r"\s+\d+/\d+$", "", s)          # creature P/T
    return s.strip()


def clauses(plan):
    return [c.strip() for c in CLAUSE.split(plan or "") if c and c.strip()]


def mentions(text, name):
    return bool(name) and name.lower() in (text or "").lower()


def held_in(plan, name):
    """The clause of `plan` that both names `name` and says to hold it, else None."""
    for c in clauses(plan):
        if not mentions(c, name):
            continue
        # Search the clause WITHOUT the card's own name: "Animate Dead" is not a reason to hold it.
        rest = re.sub(re.escape(name), " ", c, flags=re.I)
        # A clause that says to do it now is not a hold, whatever it says the play is for ("Crack Windswept Heath for
        # an untapped Forest now to improve next turn's mana", runs/loop/g01 T7).
        if re.search(r"\bnow\b", rest, re.I):
            continue
        if HOLD.search(rest):
            return c
    return None


def passes_first(plan):
    """True when the plan opens by saying to pass this window ('Pass this window; next, ...')."""
    c = clauses(plan)
    return bool(c) and re.match(r"(?i)\s*pass\b", c[0]) is not None


def audit_events(events):
    """events: ordered dicts {kind: 'plan'|'play', seat, turn, plan|card, how}. `turn` may be a
    counter rather than the game turn; a plan governs the same seat's plays until its next plan."""
    out = {"plans": 0, "plays": 0, "planned_plays": 0, "named": 0, "hold_violations": [], "executed_by_harness": 0}
    current = {}
    for e in events:
        if e["kind"] == "plan":
            current[e["seat"]] = e
            out["plans"] += 1
            continue
        out["plays"] += 1
        p = current.get(e["seat"])
        if not p or p["turn"] != e["turn"]:
            continue
        out["planned_plays"] += 1
        if e.get("how") == "plan":
            out["executed_by_harness"] += 1
        if mentions(p["plan"], e["card"]):
            out["named"] += 1
        clause = held_in(p["plan"], e["card"])
        # A plan that opens with "pass" and is then EXECUTED by the harness is the same defect.
        if not clause and e.get("how") == "plan" and passes_first(p["plan"]):
            clause = clauses(p["plan"])[0]
        if clause:
            out["hold_violations"].append({"seat": e["seat"], "turn": e["turn"], "card": e["card"],
                                           "how": e.get("how", ""), "clause": clause, "plan": p["plan"],
                                           "src": e.get("src", "")})
    return out


LOG_MAIN = re.compile(r"^\[(?P<seat>[^\]]+)\] \[ROUTER\] play\(main\)")


def events_from_log(path):
    """Router lines carry no turn number. `play(main)` is printed only on a seat's own main phase, so
    the turn counter advances whenever the seat printing it changes."""
    ev, turn, main_seat = [], 0, None
    with open(path, errors="replace") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\n")
            m = LOG_MAIN.match(line)
            if m:
                if m["seat"] != main_seat:
                    main_seat, turn = m["seat"], turn + 1
                continue
            m = LOG_PLAN.match(line)
            if m:
                ev.append({"kind": "plan", "seat": m["seat"], "turn": turn, "plan": m["plan"]})
                continue
            m = LOG_PLAY.match(line)
            if m:
                ev.append({"kind": "play", "seat": m["seat"], "turn": turn,
                           "card": card_name(m["label"]), "how": "plan" if m["how"].startswith("plan step") else "model",
                           "src": "%s:%d" % (os.path.basename(path), n)})
    return ev


def last_segment(reply, tag):
    i = (reply or "").upper().rfind(tag)
    if i < 0:
        return None
    seg = reply[i + len(tag):]
    for stop in ("CHOICE:", "STEPS:", "PROFILE:", "LINES:"):
        j = seg.upper().find(stop)
        if j >= 0:
            seg = seg[:j]
    return " ".join(seg.split())


def events_from_jsonl(path):
    ev = []
    with open(path, errors="replace") as f:
        for n, line in enumerate(f, 1):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            # Since the answer tool (2026-10-15) the plan is a `set_plan` call, on a context_call row of the ask that
            # precedes the decision row; reading only the text grammar's PLAN: line saw no plan in any current game.
            if r.get("t") == "context_call" and "TOOL CALL set_plan " in str(r.get("raw_reply") or ""):
                try:
                    args = json.loads(str(r["raw_reply"]).split("TOOL CALL set_plan ", 1)[1].split("\nTOOL CALL ", 1)[0])
                except (ValueError, IndexError):
                    args = {}
                plan = " ".join(str(args.get(k) or "") for k in ("goal", "next")).strip()
                if plan:
                    ev.append({"kind": "plan", "seat": r.get("seat"), "turn": int(r.get("turn") or 0), "plan": plan})
                continue
            if r.get("type") != "play":
                continue
            seat, turn = r.get("seat"), r.get("turn")
            plan = last_segment(r.get("raw_reply"), "PLAN:")
            if plan:
                ev.append({"kind": "plan", "seat": seat, "turn": turn, "plan": plan})
            opts, choice = r.get("options") or [], r.get("choice")
            if isinstance(choice, int) and 0 <= choice < len(opts) and not opts[choice].startswith("PASS"):
                if opts[choice].startswith("PLAY LAND"):
                    continue                     # a land drop is not a spell the plan can "hold"
                ev.append({"kind": "play", "seat": seat, "turn": turn, "card": card_name(opts[choice]),
                           "how": "plan" if r.get("tier") == "plan" else "model",
                           "src": "%s:%d" % (os.path.basename(path), n)})
    return ev


def pct(a, b):
    return "n/a" if not b else "%.1f%%" % (100.0 * a / b)


def report(name, res, verbose=True):
    hv = res["hold_violations"]
    print("%s: plans=%d plays=%d in-plan-turn=%d harness-executed=%d" % (
        name, res["plans"], res["plays"], res["planned_plays"], res["executed_by_harness"]))
    print("  HOLD-VIOLATIONS: %d of %d plays made in a turn with a plan (%s); trivial baseline 0"
          % (len(hv), res["planned_plays"], pct(len(hv), res["planned_plays"])))
    print("  plays naming a card the plan names: %d of %d (%s); the rest are 'unplanned' (context, not a defect)"
          % (res["named"], res["planned_plays"], pct(res["named"], res["planned_plays"])))
    if verbose:
        for v in hv:
            print("    - %s T%s %s [%s] %s\n        plan clause: %s" % (
                v["seat"], v["turn"], v["card"], "harness-executed" if v["how"] == "plan" else "model-chosen",
                v["src"], v["clause"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="JSONL files or directories of them")
    ap.add_argument("--log", action="append", default=[], help="a game log (no EVAL_OUT)")
    ap.add_argument("--json", action="store_true", help="print one JSON object instead of text")
    ap.add_argument("--fail-on-violation", action="store_true", help="exit 1 if any HOLD-VIOLATION is found")
    a = ap.parse_args(argv)
    files = []
    for p in a.paths:
        files += sorted(glob.glob(os.path.join(p, "*.jsonl"))) if os.path.isdir(p) else [p]
    total = {"plans": 0, "plays": 0, "planned_plays": 0, "named": 0, "hold_violations": [], "executed_by_harness": 0}
    per = {}
    for path, loader in [(f, events_from_jsonl) for f in files] + [(l, events_from_log) for l in a.log]:
        res = audit_events(loader(path))
        per[path] = res
        for k in total:
            total[k] = total[k] + res[k]
    if not per:
        ap.error("no input")
    if a.json:
        print(json.dumps({"total": total, "files": per}, indent=1))
    else:
        for path, res in per.items():
            if res["plans"] or res["plays"]:
                report(os.path.basename(path), res)
        if len(per) > 1:
            report("TOTAL (%d files)" % len(per), total, verbose=False)
    return 1 if a.fail_on_violation and total["hold_violations"] else 0


if __name__ == "__main__":
    sys.exit(main())
