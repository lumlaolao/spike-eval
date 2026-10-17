#!/usr/bin/env python3
"""Own-card line report: did the seat get to make, and make, the decisions its lines depend on?

Counts, from recorded games (EVAL_OUT JSONL), the decision classes that were silent or impossible
before the 2026-10-13 action-fidelity fixes (docs/harness/ARCHITECTURE-REVIEW.md problems 1-4):

  sacrifice        which permanent paid a sacrifice cost, and who chose (model tier / forced / fallback)
  confirm-trigger  optional triggers offered, and how many were accepted
  activations      ACTIVATE plays, and casts from outside the hand (flashback etc.)
  own-trigger asks reactive play decisions taken while the seat's own trigger was on the stack
  lines            the seat's own LINES statement, if it made one

These are per-decision counts, so they mean something at low n. They say what the seat DID, not
whether it was right. Stdlib only.

  python3 eval/line_report.py runs/lines/v11-mardu
"""
import argparse
import glob
import json
import os
import re
import sys
from collections import Counter


def rows(path):
    with open(path, errors="replace") as f:
        for line in f:
            try:
                yield json.loads(line)
            except ValueError:
                continue


def chosen(r):
    opts, c = r.get("options") or [], r.get("choice")
    return opts[c] if isinstance(c, int) and 0 <= c < len(opts) else None


def name_of(label):
    s = re.sub(r"^(CAST|ACTIVATE|PLAY LAND)\s+", "", label or "")
    return re.split(r"\s+@\d+|\s+\[|\s+\d*\{|\s+from |\s+in your |\s+—", s, maxsplit=1)[0].strip()


def report(paths):
    tot = Counter()
    sac, trig, acts, alt, lines = Counter(), Counter(), Counter(), Counter(), []
    games = 0
    for path in paths:
        for r in rows(path):
            if r.get("t") == "game":
                games += 1
                continue
            t, tier = r.get("type"), r.get("tier")
            if t == "sacrifice":
                fb = r.get("fallback")
                who = "forced" if fb and "forced" in fb else ("fallback" if fb else "model:" + str(tier))
                if tier == "ai":
                    who = "FORGE-AI"
                tot["sacrifice/" + who] += 1
                picks = r.get("picks")
                if who.startswith("model") and isinstance(picks, list):
                    for i in picks:
                        if isinstance(i, int) and 0 <= i < len(r.get("options") or []):
                            sac[name_of(r["options"][i])] += 1
                elif fb:
                    m = re.search(r"->\s*(.+)$", fb)
                    if m:
                        sac["(" + who + ") " + m.group(1).strip()] += 1
            elif t == "confirm-trigger":
                if tier == "auto":
                    tot["confirm-trigger/auto"] += 1
                    continue
                c = r.get("choice")
                ans = "YES" if c == 0 else ("NO" if c == 1 else "unparsed")
                tot["confirm-trigger/" + ans + ":" + str(tier)] += 1
                m = re.search(r'OPTIONAL TRIGGER: "([^"]{0,90})', r.get("prompt") or "")
                trig[(ans, m.group(1) if m else "(no text)")] += 1
            elif t == "play":
                lab = chosen(r)
                if lab and lab.startswith("ACTIVATE"):
                    acts[name_of(lab)] += 1
                    tot["play/activate"] += 1
                if lab and " from " in lab.split("[")[0] and lab.startswith("CAST"):
                    alt[name_of(lab) + " <-" + lab.split("[")[0].split(" from ", 1)[1].split("—")[0].strip()] += 1
                    tot["play/cast-outside-hand"] += 1
                offered = [o for o in (r.get("options") or []) if " from " in o.split("[")[0] and o.startswith("CAST")]
                if offered:
                    tot["play/outside-hand-cast-OFFERED"] += 1
                reply = r.get("raw_reply") or ""
                i = reply.upper().rfind("LINES:")
                if i >= 0:
                    seg = reply[i + 6:]
                    for stop in ("CHOICE:", "PLAN:", "PROFILE:", "STEPS:"):
                        j = seg.upper().find(stop)
                        if j >= 0:
                            seg = seg[:j]
                    lines.append((os.path.basename(path), " ".join(seg.split())[:400]))
    print("games: %d   files: %d" % (games, len(paths)))
    print("\ndecision counts (per-decision; they say what the seat did, not whether it was right):")
    for k in sorted(tot):
        print("  %-42s %d" % (k, tot[k]))
    for title, c in (("sacrificed for a cost", sac), ("ACTIVATE plays", acts), ("casts from outside the hand", alt)):
        if c:
            print("\n%s:" % title)
            for k, v in c.most_common(20):
                print("  %3d  %s" % (v, k))
    if trig:
        print("\noptional triggers (answer, text):")
        for (ans, text), v in sorted(trig.items(), key=lambda kv: -kv[1])[:20]:
            print("  %3d  %-8s %s" % (v, ans, text))
    if lines:
        print("\nLINES statements (the seat's own, verbatim):")
        for f, l in lines:
            print("  %s: %s" % (f, l))
    return tot


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="JSONL files or run directories")
    a = ap.parse_args(argv)
    files = []
    for p in a.paths:
        files += sorted(glob.glob(os.path.join(p, "*.jsonl"))) if os.path.isdir(p) else [p]
    if not files:
        ap.error("no JSONL found")
    report(files)
    return 0


if __name__ == "__main__":
    sys.exit(main())
