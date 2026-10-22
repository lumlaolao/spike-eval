#!/usr/bin/env python3
"""Scripted agents for dry runs of the episode runner (harness_env_episode.py); no model is involved.

  copy-ours  copies our overlay's main sources (the seat and its marked engine patches, never its tests) and the
             ABI adapter (HarnessEnvOurs) into the sandbox's overlay/, names the adapter's factory in
             harness-env.properties and writes DONE: the submission should score about what ours scores.
  nothing    writes DONE and nothing else: the submission should score 0.

Run with the sandbox as the working directory, as the runner does.
Usage: harness_env_agents.py copy-ours <repo> | nothing
"""
import os
import shutil
import sys

ADAPTER = os.path.join("forge-overlay", "forge-gui-desktop", "src", "test", "java", "forge", "spike", "HarnessEnvOurs.java")
FACTORY = "forge.spike.HarnessEnvOurs$Ours"


def copy_ours(repo, box):
    overlay = os.path.join(box, "overlay")
    src_root = os.path.join(repo, "forge-overlay")
    for root, _, files in os.walk(src_root):
        rel = os.path.relpath(root, src_root)
        if "/src/main/" not in "/" + rel.replace(os.sep, "/") + "/":
            continue
        for f in files:
            if f.endswith(".java"):
                dest = os.path.join(overlay, rel, f)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copyfile(os.path.join(root, f), dest)
    dest = os.path.join(overlay, "forge-gui-desktop", "src", "main", "java", "forge", "spike", "HarnessEnvOurs.java")
    shutil.copyfile(os.path.join(repo, ADAPTER), dest)
    with open(os.path.join(overlay, "harness-env.properties"), "w", encoding="utf-8") as f:
        f.write("seat.factory=%s\n" % FACTORY)
    with open(os.path.join(box, "DONE"), "w", encoding="utf-8") as f:
        f.write("copied our overlay and its ABI adapter\n")


def nothing(box):
    with open(os.path.join(box, "DONE"), "w", encoding="utf-8") as f:
        f.write("nothing\n")


def main(argv):
    if len(argv) == 3 and argv[1] == "copy-ours":
        copy_ours(argv[2], os.getcwd())
        return 0
    if len(argv) == 2 and argv[1] == "nothing":
        nothing(os.getcwd())
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
