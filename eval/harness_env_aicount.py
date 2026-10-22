#!/usr/bin/env python3
"""The hidden grader's engine patch that counts decisions a seat hands to Forge's AI
(docs/harness/HARNESS-ENV-FRAMEWORK.md, section 4.4).

A seat hands a decision to Forge's AI in two ways: it does not override the hook (the engine calls the inherited
PlayerControllerAi method) or it calls `super.hook(...)` itself. Both enter a PlayerControllerAi decision method.
This writes a copy of the engine's own PlayerControllerAi.java (from the pinned commit, never the working tree) with
one line at the top of every method whose name is a decision hook in the ownership ledger:
`forge.harnessenv.grader.AiCounter.enter("<hook>", this);`. The grader compiles it first on the classpath, and
AiCounter counts an entry when `this` controls the seat being graded. It also counts a seat that asks the model and
then lets the AI decide anyway.

What it cannot see: a seat that calls Forge's AI helper classes directly (ComputerUtilCard.getBestCreatureAI and
such) without entering a PlayerControllerAi method; the offered and carried-out checks are the net for those.

Usage: harness_env_aicount.py <forge checkout> <ledger tsv> <out dir>   writes <out>/forge/ai/PlayerControllerAi.java
"""
import os
import re
import subprocess
import sys

import surface_coverage

ENGINE_PATH = "forge-ai/src/main/java/forge/ai/PlayerControllerAi.java"
CALL = 'forge.harnessenv.grader.AiCounter.enter("%s", this);'
SIGNATURE = re.compile(r"^    (?:public|protected)\s+(?!abstract\b)(?:final\s+)?(?:<[^>]*>\s+)?[\w.<>\[\], ?]+?\s+(\w+)\s*\(")


def decision_hooks(ledger):
    return surface_coverage.decision_hooks(ledger)


def patch(source, hooks):
    """(patched source, sorted hook names counted). A counter goes on the line after the brace that opens each
    hook method's body; abstract methods and everything else are untouched."""
    lines = source.split("\n")
    out, counted, i = [], [], 0
    while i < len(lines):
        line = lines[i]
        m = SIGNATURE.match(line)
        if m and m.group(1) in hooks and "abstract " not in line:
            j = i
            while j < len(lines) and not lines[j].rstrip().endswith("{"):
                if lines[j].rstrip().endswith(";"):
                    break
                j += 1
            if j < len(lines) and lines[j].rstrip().endswith("{"):
                out.extend(lines[i:j + 1])
                out.append(" " * 8 + CALL % m.group(1))
                counted.append(m.group(1))
                i = j + 1
                continue
        out.append(line)
        i += 1
    return "\n".join(out), sorted(set(counted))


def write(forge, ledger, out_dir):
    src = subprocess.run(["git", "-C", forge, "show", "HEAD:" + ENGINE_PATH], capture_output=True, text=True, check=True).stdout
    patched, counted = patch(src, decision_hooks(ledger))
    dest = os.path.join(out_dir, "forge", "ai", "PlayerControllerAi.java")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(patched)
    return dest, counted


def main(argv):
    if len(argv) != 4:
        print(__doc__)
        return 2
    dest, counted = write(argv[1], argv[2], argv[3])
    print("%s: %d decision methods counted" % (dest, len(counted)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
