#!/usr/bin/env python3
"""The episode spec of the harness-building environment (docs/harness/HARNESS-ENV-FRAMEWORK.md, section 2).

An episode spec (eval/harness_env/episode-v0.json) says what the agent is given (`copy`: repository paths into the
sandbox, each with a reason), what it may never see (`withheld` globs over the repository, `forbidden_tokens` and
`forbidden_path_fragments` that must not appear anywhere in the sandbox), what it may run (`agent`), how long
(`caps`), how it says it is finished (`done`), what it submits (`submission`) and which grader reads it (`grader`).

This checks the spec's shape and its self-consistency: a copy may not release a path that is always withheld (our
overlay, the ownership ledger, the gap files, the history), no destination may leave the sandbox, the task statement
must be delivered, the caps must be positive, the network closed. Given the repository it also checks every copy
matches a file. It cannot tell whether a released file *describes* our solution in words; the sandbox builder's
token scan (harness_env_sandbox.py) is the second net, and reading the released files is the third.

Usage: harness_env_spec.py <episode.json> [<repo>]   exit 0 valid, 1 with one problem per line.
"""
import json
import os
import re
import subprocess
import sys

FORMAT = "harness-env-episode/0"
REQUIRED = {"format": str, "id": str, "task": str, "copy": list, "make_dirs": list, "withheld": list,
            "forbidden_tokens": list, "forbidden_path_fragments": list, "engine": dict, "agent": dict,
            "caps": dict, "done": dict, "submission": dict, "grader": dict}
# Never released, whatever a spec says: the solution, the hidden truth the grader scores against, and the history
# that records both (SWE-bench issue 465: agents read the fix from future commits).
ALWAYS_WITHHELD = (".git/**", "forge-overlay/**", "engine-patches/**", "docs/**", "eval/**")
# Of those, never released even file by file (the task statement and decklists come out of eval/ by name).
NEVER_RELEASED = (".git/**", "forge-overlay/**", "engine-patches/**", "docs/harness/controller-ownership.tsv",
                  "eval/*_gaps.txt")
NETWORKS = ("none", "loopback")
ON_CAP = ("grade-last-checkpoint",)


class SpecError(ValueError):
    pass


def matches(path, pattern):
    """Glob over repository-relative paths: `**` crosses directories, `*` and `?` do not. `d/**` matches `d`."""
    if pattern.endswith("/**") and path == pattern[:-3]:
        return True
    rx, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            rx += ".*"
            i += 2
        elif pattern[i] == "*":
            rx += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            rx += "[^/]"
            i += 1
        else:
            rx += re.escape(pattern[i])
            i += 1
    return re.fullmatch(rx, path) is not None


def _escapes(dest):
    if not dest or os.path.isabs(dest):
        return True
    return os.path.normpath(dest).split(os.sep)[0] == ".."


def repo_files(repo):
    """Repository-relative paths of the files a repository holds: git's tracked and not-ignored files when `repo`
    is the top of a work tree (a clone's uncommitted runs/ are not part of it), every regular file otherwise;
    never `.git`."""
    try:
        top = subprocess.run(["git", "-C", repo, "rev-parse", "--show-toplevel"], capture_output=True,
                             check=True, text=True).stdout.strip()
        if os.path.realpath(top) == os.path.realpath(repo):
            listed = subprocess.run(["git", "-C", repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                                    capture_output=True, check=True).stdout.decode("utf-8").split("\0")
            return sorted(set(f for f in listed if f and os.path.isfile(os.path.join(repo, f))))
    except (OSError, subprocess.CalledProcessError):
        pass
    out = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            out.append(os.path.relpath(os.path.join(root, f), repo).replace(os.sep, "/"))
    return sorted(out)


def expand_copy(spec, repo, files=None):
    """[(source, destination)] for every file a copy entry releases; a destination ending in `/` keeps the name.
    An entry naming one file with no wildcard takes it even when git ignores it (the downloaded Comprehensive Rules
    text, eval/fetch_comp_rules.py); a wildcard matches only the files repo_files lists."""
    files = repo_files(repo) if files is None else files
    out = []
    for c in spec.get("copy", []):
        excl = c.get("exclude", [])
        hits = [f for f in files if matches(f, c["from"]) and not any(matches(f, e) for e in excl)]
        if not hits and not any(ch in c["from"] for ch in "*?[") and os.path.isfile(os.path.join(repo, c["from"])):
            hits = [c["from"]]
        for f in hits:
            out.append((f, c["to"] + os.path.basename(f) if c["to"].endswith("/") else c["to"]))
    return out


def problems(spec, repo=None):
    """Every way `spec` is malformed or inconsistent (empty when it is usable)."""
    out = []
    for key, kind in REQUIRED.items():
        if key not in spec:
            out.append("missing key: " + key)
        elif not isinstance(spec[key], kind):
            out.append("%s must be a %s" % (key, kind.__name__))
    # Go on with empty values for what is missing, so one run names every problem.
    spec = {k: spec[k] if isinstance(spec.get(k), kind) else kind() for k, kind in REQUIRED.items()}
    if spec["format"] != FORMAT:
        out.append("format is %r, this reader knows %r" % (spec["format"], FORMAT))
    for w in ALWAYS_WITHHELD:
        if w not in spec["withheld"]:
            out.append("withheld must include " + w)
    for key in ("forbidden_tokens", "forbidden_path_fragments"):
        if not spec[key] or not all(isinstance(t, str) and t for t in spec[key]):
            out.append(key + " must be a non-empty list of non-empty strings")
    sources = []
    for c in spec["copy"]:
        if not isinstance(c, dict) or not isinstance(c.get("from"), str) or not isinstance(c.get("to"), str):
            out.append("copy entry needs string `from` and `to`: %r" % (c,))
            continue
        sources.append(c["from"])
        if _escapes(c["to"]):
            out.append("copy destination escapes the sandbox: " + c["to"])
        if c["from"].endswith("**"):
            out.append("copy releases a whole tree, release files by name: " + c["from"])
        for n in NEVER_RELEASED:
            if matches(c["from"], n):
                out.append("copy releases an always-withheld path: %s (%s)" % (c["from"], n))
    for d in spec["make_dirs"]:
        if not isinstance(d, str) or _escapes(d):
            out.append("make_dirs entry escapes the sandbox: %r" % (d,))
    if spec["task"] not in sources:
        out.append("the task statement %s is not copied into the sandbox" % spec["task"])
    caps = spec["caps"]
    for k in ("wall_hours", "idle_minutes", "checkpoint_minutes"):
        v = caps.get(k)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
            out.append("caps.%s must be a positive number" % k)
    if spec["agent"].get("network") not in NETWORKS:
        out.append("agent.network must be one of %s (the grader's instances and our code are on the internet)"
                   % (NETWORKS,))
    done = spec["done"]
    if not (done.get("file") or done.get("tool")):
        out.append("done needs a `file` or a `tool`: the episode ends only when the agent says so")
    if done.get("on_cap") not in ON_CAP:
        out.append("done.on_cap must be one of %s" % (ON_CAP,))
    if not isinstance(spec["grader"].get("dev_slice", False), bool):
        out.append("grader.dev_slice must be true or false (a public dev slice is off unless the owner says so)")
    if not spec["submission"].get("path") or _escapes(spec["submission"]["path"]):
        out.append("submission.path must be a sandbox-relative directory")
    elif spec["submission"]["path"] not in spec["make_dirs"]:
        out.append("submission.path must be one of make_dirs (the agent writes it)")
    if repo is not None:
        files = repo_files(repo)
        for c in spec["copy"]:
            if isinstance(c, dict) and isinstance(c.get("from"), str) and not expand_copy({"copy": [c]}, repo, files):
                out.append("copy matches no file: " + c["from"])
    return out


def load(path, repo=None):
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    found = problems(spec, repo)
    if found:
        raise SpecError("\n".join(found))
    return spec


def main(argv):
    if len(argv) not in (2, 3):
        print(__doc__)
        return 2
    try:
        load(argv[1], argv[2] if len(argv) == 3 else None)
    except SpecError as e:
        print(e)
        return 1
    print("ok: " + argv[1])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
