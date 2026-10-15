#!/usr/bin/env python3
"""Review ONE match: everything in it that the model did not get to decide, or decided blind.

The loop this serves (.claude/skills/game-loop): play one game, read this sheet and the game, fix
what the harness got wrong, play the next. It is the mechanical half; the reading is the other.

For one recorded game it lists, each with its JSONL line so the row can be opened:

  TAKEN     the harness decided without asking and the choice was not forced: a subset decision
            with one candidate (attack with the one creature, keep the one scried card), a heuristic
  LOST      the model was asked and its answer was not used: a fallback after a model call
            (bridge-down, unparsed, no reply), i.e. a decision that cost time and then went to a
            deterministic default
  SKIP      a priority window the harness passed without asking while the seat had a spell or
            ability it could use (`t:"window"` rows; their `reason` names the shortcut)
  REPORT    the seat said a question was broken (report_unclear_decision): not a defect until read,
            but every one is read; a true one is a harness defect, a false one a model mistake
  TOOL      a tool request that was not served: REFUSED, FAILED, empty, bad-args, exhausted
  SLOW      a decision that needed more than one model attempt, or was cut off (finish_reason=length)
  LOG       anomalies in the game log: an AI fallback, an exception, a payment that failed

It does not judge play. A sheet with nothing on it means the harness stayed out of the way, not
that the model played well. Forced picks (one legal option in a pick-exactly-one menu) are counted
and not listed. Stdlib only.

  python3 eval/match_review.py runs/loop/g01                 # every game in the directory
  python3 eval/match_review.py runs/loop/g01 --fail-on-defect # exit 1 if anything is TAKEN, LOST, SKIP or FAILED
"""
import argparse
import glob
import json
import os
import re
import sys
from collections import Counter

import tool_audit
from defect_analyzer import SUBSET_TYPES

FORCED = re.compile(r"forced|only-legal|\(forced\)", re.I)
# A fallback that follows a model call: the answer was asked for and not used.
LOST = re.compile(r"bridge-down|unparsed|no-reply|budget-exhausted|\(err\)|parse", re.I)
# A decision made by a rule of thumb in the harness instead of by the seat.
# turn-cap: the per-turn loop guard stopped the seat asking (a combo turn cut short).
HEURISTIC = re.compile(r"heuristic|turn-cap", re.I)
# A choice the seat could have made and was not asked about: the harness's own fallback records how
# many options were legal, and more than one is not a forced pick (runs/loop/g06: four Painter's
# Servant colour choices recorded as "color(default)->first" against a five-colour legal set).
UNASKED_CHOICE = re.compile(r"of (\d+) legal", re.I)
LOG_PATTERNS = (("AI fallback", r"\[FALLBACK->AI\]"), ("exception", r"Exception"), ("preview failed", r"preview failed"),
                ("payment failed", r"FAILED to pay"), ("circuit breaker", r"CIRCUIT-BREAKER"),
                ("provider error", r"\] HTTP [45]\d\d"), ("truncated reply", r"TRUNCATED->RETRY"),
                ("native tools dropped", r"NATIVE-TOOLS->TEXT"))


def classify_auto(row):
    """TAKEN / LOST / forced / other for one decision row whose tier is auto."""
    fb = str(row.get("fallback") or "")
    if LOST.search(fb):
        return "LOST"
    if HEURISTIC.search(fb):
        return "TAKEN"
    m = UNASKED_CHOICE.search(fb)
    if m and int(m.group(1)) >= 2:
        return "TAKEN"
    if FORCED.search(fb):
        # One candidate in a SUBSET decision is not forced (attack or not, keep or not).
        return "TAKEN" if row.get("type") in SUBSET_TYPES else "forced"
    return "other"


def review(path):
    rows = []
    with open(path, errors="replace") as f:
        for n, line in enumerate(f, 1):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            r["_line"] = n
            rows.append(r)
    game = next((r for r in rows if r.get("t") == "game"), None)
    out = {"file": path, "game": game, "findings": [], "counts": Counter()}

    def add(kind, r, text):
        out["findings"].append((kind, r.get("_line", 0), r.get("turn"), text))
        out["counts"][kind] += 1
    for r in rows:
        t = r.get("t")
        if t == "decision":
            tier = r.get("tier")
            if tier in ("smart", "fast"):
                out["counts"]["model decisions"] += 1
                # The answer must be the one used: the last CHOICE in the reply against the recorded
                # pick (9,570 replies checked over runs/loop and runs/lines: 2 differed, both attackers).
                # Only a single-pick question has a CHOICE to compare: on a PICKS (subset) question the
                # row's choice is not an option index, and a stray "CHOICE: 1" written beside the real
                # "PICKS: 1" read as a lost answer (runs/combo/haiku-ant-storm: 3 false findings).
                said = [] if "PICKS:" in str(r.get("prompt") or "")[-1500:] else \
                    re.findall(r"CHOICE:\s*(\d+)", str(r.get("raw_reply") or ""))
                ch, n = r.get("choice"), r.get("n_options") or 0
                if said and isinstance(ch, int) and ch >= 0 and n > 1 and r.get("type") != "attackers" \
                        and int(said[-1]) - 1 != ch and int(said[-1]) <= n:
                    add("LOST", r, "%s: the reply said CHOICE %s but option %d was used" % (r.get("type"), said[-1], ch + 1))
                # A model row with a failure fallback: asked, and the answer (or part of it) not used
                # (an unparsed play read as PASS, a block pair that named no legal block).
                if LOST.search(str(r.get("fallback") or "")):
                    add("LOST", r, "%s: %s" % (r.get("type"), r.get("fallback")))
                # `attempts` counts tool rounds too; a decision is slow when time was actually wasted.
                if (r.get("retry_ms") or 0) > 0 or r.get("finish_reason") == "length":
                    add("SLOW", r, "%s: %s attempts, %sms wasted, finish=%s" % (
                        r.get("type"), r.get("attempts"), r.get("retry_ms"), r.get("finish_reason")))
            elif tier == "ai":
                add("LOST", r, "%s decided by the FORGE AI: %s" % (r.get("type"), r.get("fallback")))
            elif tier == "auto":
                kind = classify_auto(r)
                out["counts"]["forced picks" if kind == "forced" else "other automatic"] += kind in ("forced", "other")
                if kind in ("TAKEN", "LOST"):
                    add(kind, r, "%s: %s" % (r.get("type"), r.get("fallback")))
        elif t == "window":
            if (str(r.get("reason") or "").startswith("yield:") and r.get("active")
                    and r.get("phase") in ("MAIN1", "MAIN2") and not r.get("stack")):
                # A yield never covers the seat's own main phase (runs/luna-v8/g3 lost 15 of them, and
                # with them its land drops): a harness pass, not the seat's.
                add("SKIP", r, "own %s passed by a yield (%s) with %d usable" % (
                    r.get("phase"), r.get("reason"), r.get("castable") or 0))
            elif str(r.get("reason") or "").startswith("yield:"):
                out["counts"]["windows the seat yielded"] += 1      # a pass the model asked for
            elif r.get("reason") == "last-sig":
                # The same turn, phase, stack, board and menu the seat just passed on (its key covers all
                # of them): the seat's own answer repeated, not a guess (runs/loop/g09 T6: Rift Bolt's
                # suspend trigger offered the identical upkeep spot twice).
                out["counts"]["windows repeating a spot the seat passed"] += 1
            elif (r.get("castable") or 0) > 0:
                add("SKIP", r, "%s window passed without asking (%s) with %d usable" % (
                    r.get("phase"), r.get("reason"), r["castable"]))
            else:
                out["counts"]["windows with nothing usable"] += 1
        elif t == "context_tool":
            if tool_audit.tool_name(r.get("request")) in tool_audit.ANSWER_TOOLS:
                # The decision answered by a call (AnswerTool), not a lookup; a lost answer is a
                # fallback on its decision row, read above.
                out["counts"]["answer-tool calls"] += 1
                continue
            if tool_audit.tool_name(r.get("request")) == "report_unclear_decision" \
                    and not str(r.get("result") or "").startswith("ERROR"):
                args = str(r.get("request")).split(" ", 3)[-1]
                try:
                    args = json.loads(args).get("problem", args)
                except (ValueError, AttributeError):
                    pass
                add("REPORT", r, " ".join(str(args).split())[:140])
                out["counts"]["tool requests"] += 1
                continue
            kind = tool_audit.classify(r.get("request"), r.get("result"))
            out["counts"]["tool requests"] += 1
            if kind != "served":
                add("TOOL", r, "%s  %s -> %s" % (kind, " ".join(str(r.get("request")).split())[:70],
                                                 " ".join(str(r.get("result")).split())[:90]))
                out["counts"]["tool " + kind] += 1
    log = None
    if game:
        log = os.path.join(os.path.dirname(path), "game-%s-%d.log" % (game.get("seed"), 1 if game.get("swap") else 0))
    if log and os.path.isfile(log):
        with open(log, errors="replace") as f:
            text = f.read()
        # One controller per seat. A second banner for the same seat is a preview copy that was given
        # the seat's model (runs/loop/g01: it answered attack and trigger questions for the copy).
        for seat, n in sorted(Counter(re.findall(r"(?m)^\[([^\]]+)\] \[OBSERVATION\]", text)).items()):
            if n > 1:
                out["findings"].append(("LOG", 0, None, "seat %s: the model controller was built %d times (preview copies) (%s)"
                                        % (seat, n, os.path.basename(log))))
                out["counts"]["LOG"] += 1
        for name, pat in LOG_PATTERNS:
            n = len(re.findall(pat, text))
            if n:
                out["findings"].append(("LOG", 0, None, "%s x%d  (%s)" % (name, n, os.path.basename(log))))
                out["counts"]["LOG"] += 1
    return out


def defects(r):
    """The findings that mean the harness got in the way, as opposed to cost or noise."""
    c = r["counts"]
    return c["TAKEN"] + c["LOST"] + c["SKIP"] + c["tool FAILED"] + c["tool REFUSED"]


def render(r):
    g = r["game"] or {}
    c = r["counts"]
    lines = ["== %s  seed %s swap %s  winner %s  turns %s  %s" % (
        os.path.basename(r["file"]), g.get("seed"), g.get("swap"), g.get("winner", "(unfinished)"), g.get("turns"),
        g.get("win_condition", "")),
        "   model decisions %d | forced picks %d | tool requests %d | TAKEN %d  LOST %d  SKIP %d  TOOL %d  SLOW %d" % (
            c["model decisions"], c["forced picks"], c["tool requests"], c["TAKEN"], c["LOST"], c["SKIP"], c["TOOL"],
            c["SLOW"]) + ("  REPORT %d" % c["REPORT"] if c["REPORT"] else "")]
    grouped = Counter()
    first = {}
    for kind, line, turn, text in r["findings"]:
        key = (kind, re.sub(r"@?\d+", "#", text))
        grouped[key] += 1
        first.setdefault(key, (line, turn, text))
    order = {"LOST": 0, "TAKEN": 1, "SKIP": 2, "REPORT": 3, "TOOL": 4, "LOG": 5, "SLOW": 6}
    for (kind, _), n in sorted(grouped.items(), key=lambda kv: (order.get(kv[0][0], 9), -kv[1])):
        line, turn, text = first[(kind, _)]
        where = (" line %d" % line if line else "") + (" T%s" % turn if turn is not None else "")
        lines.append("   %-5s x%-3d%s  %s" % (kind, n, where, text[:150]))
    if len(lines) == 2:
        lines.append("   nothing taken, lost or unserved")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="run directories or game JSONL files")
    ap.add_argument("--fail-on-defect", action="store_true")
    a = ap.parse_args(argv)
    files = []
    for p in a.paths:
        files += sorted(glob.glob(os.path.join(p, "game-*.jsonl"))) if os.path.isdir(p) else [p]
    if not files:
        ap.error("no game JSONL found")
    bad = 0
    for f in files:
        r = review(f)
        print(render(r))
        bad += defects(r)
    return 1 if a.fail_on_defect and bad else 0


if __name__ == "__main__":
    sys.exit(main())
