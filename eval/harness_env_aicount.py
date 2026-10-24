#!/usr/bin/env python3
"""The hidden grader's engine patch that counts decisions a seat hands to Forge's AI
(docs/harness/HARNESS-ENV-FRAMEWORK.md, section 4.4).

A seat hands a decision to Forge's AI in two ways: it does not override the hook (the engine calls the inherited
PlayerControllerAi method) or it calls `super.hook(...)` itself. Both enter a PlayerControllerAi decision method.
This writes a copy of the engine's own PlayerControllerAi.java (from the pinned commit, never the working tree) with
one line at the top of every method whose name is a decision hook in the ownership ledger:
`forge.harnessenv.grader.AiCounter.enter("<hook>", this);`. The grader compiles it first on the classpath, and
AiCounter counts an entry when `this` controls the seat being graded. It also counts a seat that asks the model and
then lets the AI decide anyway. A submission that ships its own marked patch of PlayerControllerAi.java has the
counter written into that copy's text instead (`write(..., source=)`), so one javac never sees two
PlayerControllerAi.

What it cannot see: a seat that calls Forge's AI helper classes directly (ComputerUtilCard.getBestCreatureAI and
such) without entering a PlayerControllerAi method; the offered and carried-out checks are the net for those.

Usage: harness_env_aicount.py <forge checkout> <ledger tsv> <out dir>   writes <out>/forge/ai/PlayerControllerAi.java
"""
import collections
import os
import re
import subprocess
import sys

import surface_coverage

ENGINE_PATH = "forge-ai/src/main/java/forge/ai/PlayerControllerAi.java"
CALL = 'forge.harnessenv.grader.AiCounter.enter("%s", this);'
# A method that is not abstract, at any indentation, its name possibly on the line after its return type, its
# parameters over any number of lines (one level of nested parentheses, for annotations), up to its opening brace.
SIGNATURE = re.compile(r"^[ \t]*(?:public|protected)\s+(?!abstract\b)(?:(?:final|synchronized)\s+)*(?:<[^>]*>\s+)?"
                       r"[\w.<>\[\], ?]+?\s+(\w+)\s*\((?:[^()]|\([^()]*\))*\)[^{;]*\{", re.M)


def patch(source, hooks):
    """The patched source. A counter goes on a new line right after the brace that opens each hook method's body;
    abstract methods and everything else are untouched. counts() says which hooks got one."""
    out, last = [], 0
    for m in SIGNATURE.finditer(source):
        if m.group(1) not in hooks:
            continue
        out.append(source[last:m.end()])
        out.append("\n" + " " * 8 + CALL % m.group(1))
        last = m.end()
    out.append(source[last:])
    return "".join(out)


def counts(source, hooks):
    """A Counter of the counters patch() puts on each hook name in `source` (an overload is a second count)."""
    return collections.Counter(m.group(1) for m in SIGNATURE.finditer(source) if m.group(1) in hooks)


def summary(counted):
    """'N hooks, M methods' for a counts() Counter: hook names, and methods with each overload apart."""
    return "%d hooks, %d methods" % (len(counted), sum(counted.values()))


def engine_source(forge):
    """The pinned engine's PlayerControllerAi.java (HEAD of the checkout, never its working tree)."""
    return subprocess.run(["git", "-C", forge, "show", "HEAD:" + ENGINE_PATH], capture_output=True, text=True,
                          check=True).stdout


def write(forge, ledger, out_dir, source=None):
    """Patch `source` (the text of a submission's own marked copy) or, without it, the pinned engine's file;
    (patched file, counts() of the source patched)."""
    src = engine_source(forge) if source is None else source
    hooks = surface_coverage.decision_hooks(ledger)
    dest = os.path.join(out_dir, "forge", "ai", "PlayerControllerAi.java")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(patch(src, hooks))
    return dest, counts(src, hooks)


def main(argv):
    if len(argv) != 4:
        print(__doc__)
        return 2
    dest, counted = write(argv[1], argv[2], argv[3])
    print("%s: %s counted" % (dest, summary(counted)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
