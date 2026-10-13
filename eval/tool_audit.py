#!/usr/bin/env python3
"""Tool audit: what happened to every tool request the seat made in recorded games.

Reads `context_tool` / `context_call` rows (MEMORY_TOOLS=1 games) and sorts each request into:

  served      the tool ran and returned something
  REFUSED     the harness rejected the request's FORM (text grammar: the reply was not exactly one
              command line; native: the arguments were not valid). The model asked; the harness
              did not listen. This is a harness defect by construction.
  FAILED      the tool itself broke (a lookahead preview that threw). A harness defect: the model
              asked correctly and got nothing. Sixteen of sixteen spell previews in one arm went this
              way unnoticed while they were counted as served.
  empty       the tool ran and found nothing (`Matches=0`), the target did not exist, or the option
              was one the tool does not cover (a preview of a non-spell option)
  bad-args    the tool ran and the arguments were wrong (an id out of range, a non-integer)
  exhausted   the request came after the per-ask budget was spent, so the decision got no answer

and reports how often the seat asked at all. It says nothing about whether asking was wise.
Stdlib only.

  python3 eval/tool_audit.py runs/lines/ctl/control runs/lines/ctl/lookahead
  python3 eval/tool_audit.py --json runs/some-arm
"""
import argparse
import glob
import json
import os
import sys
from collections import Counter

# Refusals of a request's FORM, by the message the harness answers with (ContextMemory.execute,
# ContextMemory.askNative, NativeTools.command).
FORM_ERRORS = ("ERROR: use exactly one", "ERROR: tool arguments", "ERROR: unknown tool", "ERROR: tool call has no name",
               "ERROR: malformed MEMORY command", "ERROR: a tool request must be a function call", "ERROR: `")


OUTCOMES = ("served", "REFUSED", "FAILED", "empty", "bad-args", "exhausted")


def classify(request, result):
    result = result or ""
    # Outcomes are read from how the result STARTS: a served READ may quote any of these phrases.
    first = result.split("\n", 1)[0]
    if result.startswith("ERROR: tool budget exhausted"):
        return "exhausted"
    if "must be the number of" in first:                 # a well-formed call naming an option that is not there
        return "bad-args"
    if result.startswith(FORM_ERRORS) or (result.startswith("ERROR:") and " needs " in result.split("\n", 1)[0]):
        return "REFUSED"
    if "(preview failed" in first or result.startswith("ERROR: preview"):
        return "FAILED"
    if "supports spell options only" in first or "a preview covers CAST and ACTIVATE" in first \
            or "(no preview)" in first or "(preview unavailable" in first:
        return "empty"
    if result.startswith("Matches=0") or (result.startswith("ERROR:") and (
            "found no visible object" in result or "is not available" in result or "no card named" in result)):
        return "empty"
    if result.startswith("ERROR"):
        return "bad-args"
    return "served"


def tool_name(request):
    r = (request or "").strip()
    if r.startswith("TOOL CALL "):
        return r.split()[2] if len(r.split()) > 2 else "?"
    for line in r.splitlines():
        w = line.strip().strip("`").split()
        if w and w[0] in ("MEMORY", "INSPECT", "RULES"):
            return {"INSPECT": "inspect", "RULES": "rules_lookup"}.get(w[0], "memory_" + (w[1].lower() if len(w) > 1 else "?"))
    return "?"


def audit(paths):
    out = {"games": 0, "model_calls": 0, "requests": 0, "by_outcome": Counter(), "by_tool": Counter(),
           "native_syntax_in_text": 0, "protocols": Counter(), "examples": {}}
    for path in paths:
        with open(path, errors="replace") as f:
            for n, line in enumerate(f, 1):
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                t = r.get("t")
                if t == "game":
                    out["games"] += 1
                elif t == "context_call":
                    out["model_calls"] += 1
                    out["protocols"][r.get("protocol") or "text"] += 1
                elif t == "context_tool":
                    req, res = r.get("request") or "", r.get("result") or ""
                    kind = classify(req, res)
                    out["requests"] += 1
                    out["by_outcome"][kind] += 1
                    out["by_tool"][(tool_name(req), kind)] += 1
                    # The model writing its own function-call markup into a TEXT reply.
                    if not req.startswith("TOOL CALL ") and ("invoke name=" in req or "<tool_call" in req or '"tool_calls"' in req):
                        out["native_syntax_in_text"] += 1
                    out["examples"].setdefault(kind, "%s:%d  %s  ->  %s" % (
                        os.path.basename(path), n, " ".join(req.split())[:110], " ".join(res.split())[:90]))
    return out


def pct(a, b):
    return "n/a" if not b else "%.0f%%" % (100.0 * a / b)


def report(name, a):
    print("%s: %d games, %d model calls (%s), %d tool requests (%.1f per 100 model calls)" % (
        name, a["games"], a["model_calls"], ", ".join("%s %d" % kv for kv in sorted(a["protocols"].items())) or "none",
        a["requests"], 100.0 * a["requests"] / max(1, a["model_calls"])))
    for kind in OUTCOMES:
        n = a["by_outcome"].get(kind, 0)
        print("  %-10s %3d  (%s of requests)%s" % (kind, n, pct(n, a["requests"]),
                                                   "   e.g. " + a["examples"][kind] if kind in a["examples"] and kind != "served" else ""))
    if a["native_syntax_in_text"]:
        print("  %d text request(s) carried the model's own function-call markup" % a["native_syntax_in_text"])
    tools = sorted(set(t for t, _ in a["by_tool"]))
    if tools:
        print("  by tool: " + "; ".join("%s %s" % (t, ", ".join(
            "%s %d" % (k, a["by_tool"][(t, k)]) for k in OUTCOMES if a["by_tool"].get((t, k))))
            for t in tools))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="run directories or JSONL files")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fail-on-refused", action="store_true",
                    help="exit 1 if any request was refused for its form or any tool failed (both are harness defects)")
    a = ap.parse_args(argv)
    per, allfiles = {}, []
    for p in a.paths:
        files = sorted(glob.glob(os.path.join(p, "*.jsonl"))) if os.path.isdir(p) else [p]
        if not files:
            ap.error("no JSONL in %s" % p)
        per[p] = audit(files)
        allfiles += files
    total = audit(allfiles)
    if a.json:
        def plain(x):
            return dict(x, by_outcome=dict(x["by_outcome"]), protocols=dict(x["protocols"]),
                        by_tool={"%s/%s" % k: v for k, v in x["by_tool"].items()})
        print(json.dumps({"total": plain(total), "runs": {k: plain(v) for k, v in per.items()}}, indent=1))
    else:
        for p, res in per.items():
            report(p.rstrip("/"), res)
        if len(per) > 1:
            report("TOTAL", total)
    return 1 if a.fail_on_refused and (total["by_outcome"].get("REFUSED") or total["by_outcome"].get("FAILED")) else 0


if __name__ == "__main__":
    sys.exit(main())
