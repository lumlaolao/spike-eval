#!/usr/bin/env python3
"""Forge's human controller as the definition of "a decision" (docs/STANDARDS.md section 8).

A PlayerController method that asks a human player something (a GUI choice, a confirm, a number,
an Input* prompt) is a decision. Every such method must be the seat's in the ownership ledger
(docs/harness/controller-ownership.tsv, impl LLM), or be listed with why it is not: its reach is
`none` (not reachable in the eval formats) or `rare`, which the ledger's notes explain. A decision
the human controller asks, inherited from Forge's AI in core Legacy play, is the harness deciding.

This replaces the ledger's hand-written strategic/mechanics labels with something read from the
engine: the label can be wrong, the human controller asking the player cannot.

  python3 eval/human_decisions.py [--forge-home DIR]     # exit 1 on a core decision not the seat's

Stdlib only. Reads the pinned Forge checkout's PlayerControllerHuman.java.
"""
import argparse
import csv
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "..", "docs", "harness", "controller-ownership.tsv")
HUMAN = os.path.join("forge-gui", "src", "main", "java", "forge", "player", "PlayerControllerHuman.java")

# What asking the human looks like in PlayerControllerHuman.
PROMPT = re.compile(r"getGui\(\)\.(one|oneOrNone|many|order|getInteger|confirm|getChoices|assignGenericAmount|insertInList)\b"
                    r"|new\s+Input(Confirm|Select|Pay|Attack|Block|PassPriority|Proliferate|Choose)\w*"
                    r"|\bInputSelect\w*\.")
METHOD = re.compile(r"^\s{4}(?:@Override\s+)?(?:public|protected|private)\s+(?:static\s+)?(?:final\s+)?"
                    r"(?:<[^>]+>\s+)?[\w<>\[\],.? ]+?\s+(\w+)\s*\(", re.M)


def methods(src):
    """name -> list of bodies (overloads kept apart), for the class's own members."""
    out = {}
    for m in METHOD.finditer(src):
        name, i = m.group(1), src.find("{", m.end())
        semi = src.find(";", m.end())
        if i < 0 or (0 <= semi < i):
            continue                                  # abstract or interface declaration
        depth, j = 0, i
        while j < len(src):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out.setdefault(name, []).append(src[i:j + 1])
    return out


def prompting(src):
    """Names of methods that ask the human, directly or through another method of the class."""
    ms = methods(src)
    asks = {n for n, bodies in ms.items() if any(PROMPT.search(b) for b in bodies)}
    changed = True
    while changed:
        changed = False
        for n, bodies in ms.items():
            if n in asks:
                continue
            if any(re.search(r"\b%s\s*\(" % re.escape(k), b) for b in bodies for k in asks):
                asks.add(n)
                changed = True
    return asks


def ledger():
    rows = {}
    with open(LEDGER, newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            rows.setdefault(r["signature"].split("[", 1)[0], []).append(r)
    return rows


def audit(forge_home):
    with open(os.path.join(forge_home, HUMAN), errors="replace") as f:
        src = f.read()
    asks, led = prompting(src), ledger()
    result = {"owned": [], "explained": [], "unowned": []}
    for name in sorted(asks):
        for r in led.get(name, []):                     # only PlayerController hooks are in the ledger
            if r["impl"] == "LLM" or r["ownership"] == "wrapper":   # a wrapper is owned through what it calls
                result["owned"].append((name, r))
            elif r["legacy_reach"] in ("none", "rare"):
                result["explained"].append((name, r))
            else:
                result["unowned"].append((name, r))
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--forge-home", default=os.environ.get("FORGE_HOME", os.path.expanduser("~/code/forge")))
    a = ap.parse_args(argv)
    res = audit(a.forge_home)
    print("human-controller decisions: %d the seat's, %d not reachable/rare (ledger says why), %d unowned in core play"
          % (len(res["owned"]), len(res["explained"]), len(res["unowned"])))
    for name, r in res["unowned"]:
        print("  UNOWNED %-34s impl=%s kind=%s  %s" % (name, r["impl"], r["kind"], r["notes"][:110]))
    return 1 if res["unowned"] else 0


if __name__ == "__main__":
    sys.exit(main())
