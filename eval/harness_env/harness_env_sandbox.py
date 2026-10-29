#!/usr/bin/env python3
"""Builds the agent's starting sandbox for the harness-building environment, and proves nothing withheld is in it.

`build` copies exactly what the episode spec releases (harness_env_spec.expand_copy) into an empty directory,
creates the agent's empty directories, and returns a manifest (each file's sha256 and source), which the episode
runner keeps OUTSIDE the sandbox: the manifest names repository paths, and the sandbox must not.

`leak_problems` checks a built sandbox five ways, because each catches a leak the others miss:
  1. every file is in the manifest with the same bytes, and the agent's directories are empty (a stray file);
  2. no file has the bytes of a withheld repository file (a withheld file copied under another name);
  3. no file holds a forbidden token: our class names, our repository's name, our markers (a solution described or
     pasted into a released document);
  4. no path holds a forbidden fragment (`forge/spike`, the gap files) and nothing is a symlink (a way back out);
  5. a `.git`, if any, is a fresh repository: one commit, no remote (SWE-bench issue 465: agents read the fix
     from future commits with `git log --all`).
It cannot see a leak by paraphrase (a released document explaining our design in other words); reading what is
released is the remaining net, and the spec releases five named things.

`engine_problems` checks the engine the agent gets: a checkout at the pin, no local edit, and none of our overlay
installed, neither our seat (`forge/spike`) nor the grader (`forge/harnessenv`). The working checkout has both:
`install.sh` copies the overlay into it, and its engine patches are compiled into target/, which git ignores.

Usage:
  harness_env_sandbox.py build <episode.json> <repo> <dest> <manifest.json>
  harness_env_sandbox.py check <episode.json> <repo> <dest> <manifest.json>
  harness_env_sandbox.py engine <forge checkout> <pin>
Exit 0 clean, 1 with one problem per line.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import harness_env_spec as hs


class BuildError(RuntimeError):
    pass


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(spec, repo, dest):
    """Copy the released files into the empty `dest`; return the manifest."""
    if os.path.exists(dest) and os.listdir(dest):
        raise BuildError("refusing to build into a non-empty directory: " + dest)
    pairs = hs.expand_copy(spec, repo)
    targets = [d for _, d in pairs]
    if len(set(targets)) != len(targets):
        raise BuildError("two released files land on the same path")
    os.makedirs(dest, exist_ok=True)
    files = {}
    for src, dst in pairs:
        out = os.path.join(dest, dst)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copyfile(os.path.join(repo, src), out)
        files[dst] = sha256(out)
    for d in spec["make_dirs"]:
        os.makedirs(os.path.join(dest, d), exist_ok=True)
    return {"format": "harness-env-manifest/0", "episode": spec["id"], "files": files,
            "sources": {dst: src for src, dst in pairs}, "make_dirs": list(spec["make_dirs"])}


def _walk(dest):
    """(relative path, absolute path, is symlink) of every entry under `dest` but `.git`."""
    for root, dirs, files in os.walk(dest):
        for name in list(dirs):
            full = os.path.join(root, name)
            if os.path.islink(full):
                yield os.path.relpath(full, dest), full, True
                dirs.remove(name)
            elif name == ".git" and root == dest:
                dirs.remove(name)
        for name in files:
            full = os.path.join(root, name)
            yield os.path.relpath(full, dest).replace(os.sep, "/"), full, os.path.islink(full)


def _git_problems(dest):
    gitdir = os.path.join(dest, ".git")
    if not os.path.exists(gitdir):
        return []
    if not os.path.isdir(gitdir):
        return [".git is a file pointing elsewhere (a worktree of another repository)"]
    out = []

    def git(*args):
        return subprocess.run(["git", "-C", dest, *args], capture_output=True, text=True)

    n = git("rev-list", "--all", "--count")
    count = int(n.stdout.strip() or 0) if n.returncode == 0 else 0
    if count > 1:
        out.append(".git holds %d commits: the sandbox repository must start with one" % count)
    if git("remote").stdout.strip():
        out.append(".git has a remote: " + git("remote", "-v").stdout.strip().splitlines()[0])
    if git("stash", "list").stdout.strip():
        out.append(".git holds a stash")
    # A rewritten history keeps its old objects until they are pruned; `git cat-file` would still read them.
    if git("fsck", "--unreachable", "--no-reflogs").stdout.strip():
        out.append(".git holds unreachable objects (an old history not pruned)")
    return out


def leak_problems(spec, repo, dest, manifest):
    """Every way the sandbox at `dest` holds something the spec withholds (empty when clean)."""
    out = []
    released = set(manifest.get("sources", {}).values())
    withheld = {}
    for f in hs.repo_files(repo):
        if f in released or not any(hs.matches(f, w) for w in spec["withheld"]):
            continue
        path = os.path.join(repo, f)
        if os.path.getsize(path) > 0:
            withheld.setdefault(sha256(path), f)
    agent_dirs = tuple(d.rstrip("/") + "/" for d in manifest.get("make_dirs", []))
    tokens = [t.encode("utf-8") for t in spec["forbidden_tokens"]]
    seen = set()
    for rel, full, link in _walk(dest):
        if link:
            out.append("%s is a symlink (a path out of the sandbox)" % rel)
            continue
        seen.add(rel)
        for frag in spec["forbidden_path_fragments"]:
            if frag in rel:
                out.append("%s: the path holds the forbidden fragment %r" % (rel, frag))
        digest = sha256(full)
        if rel.startswith(agent_dirs):
            out.append("%s: the agent's directories must start empty" % rel)
        elif rel not in manifest["files"]:
            out.append("%s is not in the manifest" % rel)
        elif digest != manifest["files"][rel]:
            out.append("%s differs from the manifest" % rel)
        if digest in withheld:
            out.append("%s has the same bytes as withheld %s" % (rel, withheld[digest]))
        with open(full, "rb") as fh:
            data = fh.read()
        for t in tokens:
            if t in data:
                out.append("%s holds the forbidden token %r" % (rel, t.decode("utf-8")))
    for rel in sorted(set(manifest["files"]) - seen):
        out.append("%s is in the manifest but missing" % rel)
    return out + _git_problems(dest)


def engine_problems(forge, pin):
    """Every way `forge` is not a clean engine checkout at `pin` with none of our overlay installed."""
    out = []
    head = subprocess.run(["git", "-C", forge, "rev-parse", "HEAD"], capture_output=True, text=True)
    if head.returncode != 0:
        return ["%s is not a git checkout" % forge]
    if head.stdout.strip() != pin:
        out.append("engine HEAD %s is not the pin %s" % (head.stdout.strip(), pin))
    dirty = subprocess.run(["git", "-C", forge, "status", "--porcelain"], capture_output=True, text=True).stdout
    for line in dirty.splitlines():
        out.append("engine working tree differs from the pin: " + line.strip())
    for top in sorted(os.listdir(forge)):
        if not top.startswith("forge"):
            continue
        for pkg in ("forge/spike", "forge/harnessenv"):
            for root in ("src/main/java", "src/test/java", "target/classes", "target/test-classes"):
                sub = root + "/" + pkg
                if os.path.isdir(os.path.join(forge, top, sub)):
                    out.append("our overlay is installed in the engine: %s/%s" % (top, sub))
    return out


def _report(found):
    for p in found:
        print(p)
    return 1 if found else 0


def main(argv):
    if len(argv) == 6 and argv[1] in ("build", "check"):
        spec = hs.load(argv[2], argv[3])
        if argv[1] == "build":
            manifest = build(spec, argv[3], argv[4])
            with open(argv[5], "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=1, sort_keys=True)
        else:
            with open(argv[5], encoding="utf-8") as f:
                manifest = json.load(f)
        found = leak_problems(spec, argv[3], argv[4], manifest)
        if not found:
            print("sandbox %s: %d released files, no leak found" % (argv[4], len(manifest["files"])))
        return _report(found)
    if len(argv) == 4 and argv[1] == "engine":
        found = engine_problems(argv[2], argv[3])
        if not found:
            print("engine %s: clean at %s" % (argv[2], argv[3]))
        return _report(found)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
