#!/usr/bin/env python3
"""Engine patches: our changes to Forge's own files, kept as diffs and turned into the patched files on demand.

Forge is GPL, and the repository carries only our changes to it: `engine-patches/<path in Forge>.patch` is a unified
diff against that file at FORGE_COMMIT, opening with a notice (Forge, the licence, the dates of the changes) that diff
tools ignore. This tool reads each upstream original with `git show <FORGE_COMMIT>:<path>` (never the working tree,
which holds whatever install.sh last wrote), applies the diff with `git apply` (exact context, no fuzz) and writes the
patched file to a target root at the same path. A patch that does not apply, or whose original is not at the pin,
stops it with exit 1 and the file named, before anything is written: that is what forces a re-diff on a re-pin.

  python3 eval/engine_patches.py apply --out DIR [--forge F]   # write every patched file at DIR/<path>
  python3 eval/engine_patches.py diff --from DIR [--forge F]   # re-make the patch of every patched file at DIR/<path>

`diff` writes a patch per file found at DIR/<path> for every existing patch, and a new one for any file named with
--path. It keeps a patch's notice and its first date and moves the last date to today. It cannot port a change onto
a new upstream file: a patch that stops applying is re-made by hand (docs/harness/ENGINE-PIN.md). Stdlib only.
"""

import argparse
import datetime
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PATCH_DIR = "engine-patches"
SUFFIX = ".patch"
NOTICE = ("Modified from Forge (https://github.com/Card-Forge/forge), GPL-3.0-or-later; changes marked HARNESS PATCH;\n"
          "see docs/harness/ENGINE-PATCHES.md. Changes dated {first} to {last}.\n"
          "Upstream file: {rel} at FORGE_COMMIT. Made by: python3 eval/engine_patches.py diff\n\n")
DATES = re.compile(r"Changes dated (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})\.")


class PatchError(Exception):
    """A patch that cannot be materialized; the message names the file."""


def forge_home():
    """The Forge checkout: the first of $FORGE_HOME, ~/code/forge and /root/forge that is a git checkout, or None."""
    env = os.path.expanduser(os.path.expandvars(os.environ.get("FORGE_HOME", "")))
    for c in ([env] if env else []) + [str(Path.home() / "code/forge"), "/root/forge"]:
        try:
            if (Path(c) / ".git").exists():
                return Path(c)
        except OSError:  # a candidate this user cannot read (/root/forge for a non-root user) is not a checkout
            continue
    return None


def pinned_commit(repo=REPO):
    return (Path(repo) / "FORGE_COMMIT").read_text().split()[0]


def patch_files(repo=REPO):
    """{path in Forge: patch file} for every patch in the repository, sorted by path."""
    root = Path(repo) / PATCH_DIR
    found = {p.relative_to(root).as_posix()[:-len(SUFFIX)]: p for p in root.rglob("*" + SUFFIX) if p.is_file()}
    return dict(sorted(found.items()))


def original(forge, commit, rel):
    r = subprocess.run(["git", "-C", str(forge), "show", "%s:%s" % (commit, rel)], capture_output=True)
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace") if isinstance(r.stderr, bytes) else str(r.stderr)
        raise PatchError("%s: no upstream original at %s in %s: %s" % (rel, commit[:12], forge, err.strip()))
    return r.stdout


def apply_patch(orig, patch, rel):
    """The patched bytes of `orig` (the upstream file `rel`), or PatchError naming `rel`."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp, "t", rel)
        target.parent.mkdir(parents=True)
        target.write_bytes(orig)
        # The ceiling keeps git from finding a repository above the scratch directory and patching paths in it.
        env = dict(os.environ, GIT_CEILING_DIRECTORIES=tmp)
        r = subprocess.run(["git", "apply", "--whitespace=nowarn", str(Path(patch).resolve())],
                           cwd=str(Path(tmp, "t")), env=env, capture_output=True, text=True)
        if r.returncode != 0:
            raise PatchError("%s: %s does not apply to the upstream original; re-diff it (docs/harness/ENGINE-PIN.md)"
                             "\n%s" % (rel, patch, r.stderr.strip()))
        return target.read_bytes()


def patched_sources(forge, repo=REPO, commit=None):
    """{path in Forge: patched bytes} for every patch in `repo`; {} without a Forge when there is no patch."""
    patches = patch_files(repo)
    if not patches:
        return {}
    if forge is None:
        raise PatchError("%s: no Forge checkout to read the upstream originals from (set FORGE_HOME)"
                         % ", ".join(patches))
    commit = commit or pinned_commit(repo)
    return {rel: apply_patch(original(forge, commit, rel), p, rel) for rel, p in patches.items()}


def materialize(out, forge, repo=REPO):
    """Write every patched file at out/<path>; all are made before any is written. Returns the paths."""
    sources = patched_sources(forge, repo)
    for rel, data in sources.items():
        dest = Path(out, rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return list(sources)


def hunks(text):
    """The added lines of each hunk of a unified diff (without the '+'), the notice before the first '---' ignored."""
    out, body = [], False
    for line in text.splitlines():
        if line.startswith("--- "):
            body = True
        elif body and line.startswith("@@"):
            out.append([])
        elif out and line.startswith("+"):
            out[-1].append(line[1:])
    return out


def rediff(src, forge, repo=REPO, new=()):
    """Re-make the patch of each patched file found at src/<path> (every existing patch, plus `new` paths)."""
    if forge is None:
        raise PatchError("no Forge checkout to diff against (set FORGE_HOME)")
    commit, today = pinned_commit(repo), datetime.date.today().isoformat()
    made = []
    for rel in sorted(set(patch_files(repo)) | set(new)):
        mine = Path(src, rel)
        if not mine.is_file():
            continue
        with tempfile.TemporaryDirectory() as tmp:
            orig = Path(tmp, "orig")
            orig.write_bytes(original(forge, commit, rel))
            r = subprocess.run(["diff", "-u", "--label", "a/" + rel, "--label", "b/" + rel, str(orig), str(mine)],
                               capture_output=True, text=True)
        if r.returncode != 1:
            raise PatchError("%s: %s" % (rel, "identical to upstream: not a patch" if r.returncode == 0 else r.stderr))
        path = Path(repo, PATCH_DIR, rel + SUFFIX)
        old = path.read_text() if path.is_file() else ""
        if old and old[old.find("\n--- ") + 1:] == r.stdout:
            continue  # unchanged: the notice keeps its dates
        dates = DATES.search(old)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(NOTICE.format(first=dates.group(1) if dates else today, last=today, rel=rel) + r.stdout)
        made.append(rel)
    return made


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("apply", "diff"))
    ap.add_argument("--out", help="apply: the root the patched files are written under")
    ap.add_argument("--from", dest="src", help="diff: the root holding the edited files")
    ap.add_argument("--path", action="append", default=[], help="diff: a Forge path to start a new patch for")
    ap.add_argument("--forge", help="the Forge checkout (default: $FORGE_HOME, ~/code/forge, /root/forge)")
    ap.add_argument("--repo", default=str(REPO), help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    forge = Path(a.forge) if a.forge else forge_home()
    try:
        if a.action == "apply":
            if not a.out:
                ap.error("apply needs --out")
            for rel in materialize(a.out, forge, a.repo):
                print("patched %s" % os.path.join(a.out, rel))
        else:
            if not a.src:
                ap.error("diff needs --from")
            for rel in rediff(a.src, forge, a.repo, a.path):
                print("re-made %s/%s%s" % (PATCH_DIR, rel, SUFFIX))
    except PatchError as e:
        print("engine_patches: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
