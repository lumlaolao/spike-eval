#!/usr/bin/env python3
"""Scripted agents for dry runs of the episode runner (harness_env_episode.py); no model is involved.

  copy-ours  copies our overlay's main sources (the seat, never its tests), our marked engine patches as the files
             they make (eval/engine_patches.py, from the Forge checkout) and the ABI adapter (HarnessEnvOurs) into
             the sandbox's overlay/, names the adapter's factory in harness-env.properties and writes DONE: the
             submission should score about what ours scores.
  nothing    writes DONE and nothing else: the submission should score 0.

What these dry runs cannot establish: anything about an agent that builds a seat. They check the runner's plumbing
(sandbox, DONE, export, build, grading outside) on a submission we already know; they say nothing about whether the
sandbox holds enough to build a seat from, whether TASK.md and SPEC.md are understood, how long a real episode runs
or how an agent ends one, nor whether the hidden grader is robust to a seat written to fool it (copy-ours is our own
trusted code, graded with --trust-candidate; framework section 4.7).

Run with the sandbox as the working directory, as the runner does.
Usage: harness_env_agents.py copy-ours <repo> [<forge checkout>] | nothing
(the Forge checkout defaults to $FORGE_HOME, ~/code/forge, /root/forge; copy-ours stops if a patch does not apply)
"""
import os
import shutil
import sys

import engine_patches
import harness_env_grade as hg

FACTORY = "forge.spike.HarnessEnvOurs$Ours"


def copy_ours(repo, box, forge=None):
    overlay = os.path.join(box, "overlay")
    engine_patches.materialize(overlay, forge or engine_patches.forge_home(), repo)
    src_root = os.path.join(repo, "forge-overlay")
    for j in hg._java_files(src_root):
        if "/src/main/" in "/" + j:
            dest = os.path.join(overlay, j)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(os.path.join(src_root, j), dest)
    dest = os.path.join(overlay, "forge-gui-desktop", "src", "main", "java", "forge", "spike", "HarnessEnvOurs.java")
    shutil.copyfile(os.path.join(repo, hg.OURS_ADAPTER), dest)
    with open(os.path.join(overlay, "harness-env.properties"), "w", encoding="utf-8") as f:
        f.write("seat.factory=%s\n" % FACTORY)
    with open(os.path.join(box, "DONE"), "w", encoding="utf-8") as f:
        f.write("copied our overlay and its ABI adapter\n")


def nothing(box):
    with open(os.path.join(box, "DONE"), "w", encoding="utf-8") as f:
        f.write("nothing\n")


def main(argv):
    if len(argv) in (3, 4) and argv[1] == "copy-ours":
        copy_ours(argv[2], os.getcwd(), argv[3] if len(argv) == 4 else None)
        return 0
    if len(argv) == 2 and argv[1] == "nothing":
        nothing(os.getcwd())
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
