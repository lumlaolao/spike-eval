#!/usr/bin/env python3
"""harness_env_task_grade.py: scores a candidate worktree against one harness-env task (harness_env_tasks.py).

The reward is 1 only if every target test passes (the tests the PR added or changed) and no other test of the
test classes the task names fails, is skipped or goes missing; else 0. Partial-credit fields are recorded beside it
(they are for curricula and diagnostics, not the reward):
  target_fraction   target tests passed / target tests
  others_fraction   other tests of those classes passed / those tests (1.0 when the classes hold no others)
  partial           0 if the build fails, else 0.7 * target_fraction + 0.3 * others_fraction
The trivial baseline of every task is the base state's score: 0 by construction (`validate` proves it), so a
score of 1 means the tests the PR added now pass, never that nothing was tested.

Result keys, stated once: a test is `<class>.<method>` for Java (class fully qualified), `<module>.<Class.method>` for
a Python TestCase method and `<module>.<test_fn>` for a module-level function; a failed TestNG fixture is
`<key><config>`. A declared test with no result is missing, which counts against the candidate.

The grader's own repository (`--repo`, at the task's merge commit) supplies the test files, their support files, the
read-only inputs they read (READONLY_DATA; the gold patch's own files excepted), the Comprehensive Rules text, the
Python runner, the TestNG reporter and the engine-patch tool. A candidate that changed, emptied or deleted a test
scores 0 (`tests_modified`); one whose parent directory is a symlink out of the worktree scores 0 (`Tampered`).

A Java task compiles the candidate's overlay with javac over the pinned engine (engine patches applied by the
grader's own eval/engine_patches.py, reading the candidate's engine-patches/ as data), then runs the named TestNG
classes in one JVM. A Python task runs every test of the named files in a runner of the grader's. Results come from
a file with a per-run nonce that the grader's reporter or runner writes; stdout and testng-results.xml are never
read. The acceptance script has no class filter and needs `mvn`, which this grader never runs: the dependency
classpath comes from a `deps.txt` an earlier acceptance run wrote (--deps-file, HARNESS_DEPS_FILE, or the newest
under a runs/ tree).

NOT A SECURITY SANDBOX: the candidate's code runs as the invoking user. Closed and open forgeries, and what
isolation-grade needs: eval/research/2026-11-07-harness-env-tasks.md, "Status". The engine checkout is only guarded
(the JVM starts in a scratch tree; the grade fails if the checkout's HEAD or `git status` changed or any file under
$FORGE_HOME bar .git is newer than the grade's start).

Usage:
  harness_env_task_grade.py grade <tasks.json> <worktree> --task-id pr403 [--out DIR] [--deps-file F] [--forge F] [--repo .]
  harness_env_task_grade.py validate <tasks.json> --task-id pr403 --work DIR [--repo .] [--deps-file F]
  harness_env_task_grade.py prepare <tasks.json> <dest> --task-id pr403 [--gold] [--repo .]
`--repo` is the grader's own repository (it holds the merge commit); --task-id takes the manifest id or the
agent's opaque id. `prepare` writes the base tree with the PR's tests and ONE fresh commit, no history, no remote.
`validate` is the well-posedness check: base + the PR's tests must score 0, base + gold patch + tests must score 1.
Run any JVM step under the shared lock (CLAUDE.md). Exit 0 graded (read `score`), 2 setup error, 1 validate failed.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import harness_env_tasks as het
import engine_patches

DEFAULT_FORGE = "/root/forge"
# Homebrew's JDK prefixes, in setup.sh's order (eval/tests/test_setup.py keeps the lists equal).
JDK_DIRS = ("/opt/homebrew/opt/openjdk@17", "/usr/local/opt/openjdk@17")
OVERLAY_REL = "forge-overlay/forge-gui-desktop/src"
ADDOPENS = ("java.desktop/java.beans java.desktop/javax.swing.border java.desktop/javax.swing.event "
            "java.desktop/sun.swing java.desktop/java.awt.image java.desktop/java.awt.color "
            "java.desktop/sun.awt.image java.desktop/javax.swing java.desktop/java.awt java.desktop/java.awt.font "
            "java.base/java.util java.base/java.lang java.base/java.lang.reflect java.base/java.text "
            "java.base/jdk.internal.misc java.base/sun.nio.ch java.base/java.nio java.base/java.math "
            "java.base/java.util.concurrent java.base/java.net").split()
WEIGHT_TARGET = 0.7
ENV_KEEP = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "JAVA_HOME")
SPEC_FLAG = "@SPEC@"  # in a JVM command line: replaced by the property naming the run's spec file
REPORTER_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "HarnessGraderReporter.java")
REPORTER_CLASS = "HarnessGraderReporter"
# The grader's own copy: the candidate's eval/engine_patches.py is never executed (its patch diffs are read as data).
GRADER_ENGINE_PATCHES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine_patches.py")


class SetupError(RuntimeError):
    pass


# ---------------------------------------------------------------- scoring (pure)

def score(targets, results, declared=None, compile_ok=True):
    """targets: [(key, ...)] keys; results: {key: 'PASS'|'FAIL'|'SKIP'|...} of every test run in the task's classes;
    declared: keys the classes declare (a declared test absent from results is missing, not passed)."""
    target_keys = list(targets)
    target_set = set(target_keys)
    # `results` keys nobody declared (a failed TestNG fixture, `<key><config>`, or a test the source scan missed) join
    # the declared ones as "others": they must pass, so a broken fixture is a regression, not an ignored extra.
    declared = set(declared or []) | set(results) | target_set
    others = sorted(k for k in declared if k not in target_set)
    t_pass = [k for k in target_keys if results.get(k) == "PASS"]
    o_pass = [k for k in others if results.get(k) == "PASS"]
    regressions = [k for k in others if results.get(k) != "PASS"]
    t_frac = len(t_pass) / len(target_keys) if target_keys else 0.0
    o_frac = len(o_pass) / len(others) if others else 1.0
    ok = bool(compile_ok and target_keys and len(t_pass) == len(target_keys) and not regressions)
    return {
        "score": 1 if ok else 0,
        "compile_ok": bool(compile_ok),
        "targets_total": len(target_keys), "targets_passed": len(t_pass),
        "target_fraction": round(t_frac, 4),
        "others_total": len(others), "others_passed": len(o_pass), "others_fraction": round(o_frac, 4),
        "partial": round(WEIGHT_TARGET * t_frac + (1 - WEIGHT_TARGET) * o_frac, 4) if compile_ok else 0.0,
        "failed_targets": [k for k in target_keys if k not in t_pass],
        "regressions": regressions,
    }


# ---------------------------------------------------------------- the Forge guard

class ForgeGuard:
    """Proves the engine checkout was only read: same HEAD, same `git status`, no file anywhere in it (bar .git)
    newer than the guard's start. `close()` removes the marker; call it in a finally."""

    def __init__(self, forge):
        self.forge = forge
        self.marker = tempfile.NamedTemporaryFile(prefix="forge-guard-", delete=False)
        self.marker.close()
        os.utime(self.marker.name, None)
        time.sleep(1.1)
        self.before = self._snap()

    def _snap(self):
        def run(*a):
            p = subprocess.run(["git", "-C", self.forge] + list(a), capture_output=True, text=True)
            return p.stdout
        return run("rev-parse", "HEAD") + run("status", "--porcelain")

    def newer(self):
        mark = os.stat(self.marker.name).st_mtime
        for root, dirs, files in os.walk(self.forge):
            dirs[:] = [d for d in dirs if d != ".git"]
            for f in files:
                p = os.path.join(root, f)
                try:
                    if os.lstat(p).st_mtime > mark:
                        return p
                except OSError:
                    pass
        return None

    def problems(self):
        out = []
        if self._snap() != self.before:
            out.append("the Forge checkout's HEAD or git status changed")
        n = self.newer()
        if n:
            out.append("a file in the Forge checkout is newer than the grade's start: " + n)
        return out

    def close(self):
        try:
            os.unlink(self.marker.name)
        except OSError:
            pass


def refuse_forge_paths(forge, *paths):
    f = os.path.realpath(forge)
    for p in paths:
        rp = os.path.realpath(p)
        if rp == f or rp.startswith(f + os.sep):
            raise SetupError("%s is inside the Forge checkout %s: the grader never writes there" % (p, forge))


# ---------------------------------------------------------------- the candidate's tree: restore, purge, check

class Tampered(Exception):
    """The candidate's tree is arranged so the grader cannot trust a write into it (a symlinked parent directory)."""


def restore_files(task, repo, root, paths, must_exist=True):
    """Write each of `paths` under `root` exactly as the task's merge commit in the GRADER's repository has it. A path
    absent at that commit is an error (must_exist) or is deleted from root (the gold patch removes it). -> the paths
    whose content in root differed. A parent directory that is a symlink out of root raises Tampered."""
    changed = []
    real_root = os.path.realpath(root)
    for path in paths:
        blob = het.read_blob(repo, task["merge_sha"], path)
        dest = os.path.join(root, path)
        if blob is None:
            if must_exist:
                raise SetupError("%s is not in %s of the grader's repository %s" % (path, task["merge_sha"][:9], repo))
            if os.path.lexists(dest):
                os.unlink(dest)
            continue
        try:
            with open(dest, "rb") as f:
                same = f.read() == blob and not os.path.islink(dest)
        except OSError:
            same = False
        if not same:
            changed.append(path)
        if os.path.islink(dest):
            os.unlink(dest)
        parent = os.path.realpath(os.path.dirname(dest))
        if parent != real_root and not parent.startswith(real_root + os.sep):
            raise Tampered("%s has a parent directory that leaves the worktree (%s)" % (path, parent))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(blob)
    return changed


# Files a test reads and never writes. They are restored from the merge commit like the tests (a candidate edit is
# silently undone, not penalised), except the ones the gold patch itself changes: those are the fix. The covered set:
# every tracked `.md`; every `.json`, `.txt`, `.tsv` and `.csv` under eval/ and docs/; every `.pzl` and `.jsonl` under
# eval/ (puzzle files and recorded runs the Java tests read); and the tree eval/decks/.
READONLY_DATA = re.compile(r"^(.*\.md|(eval|docs)/.*\.(json|txt|tsv|csv)|eval/.*\.(pzl|jsonl)|eval/decks/.*)$")


def readonly_inputs(task, repo):
    own = set(task["test_files"]) | set(task["gold_files"])
    tree = het.git(repo, "ls-tree", "-r", "--name-only", task["merge_sha"]).splitlines()
    return [p for p in tree if p not in own and READONLY_DATA.match(p)]


def purge_bytecode(worktree):
    """Delete every __pycache__ and *.pyc: a planted UNCHECKED_HASH pyc is run in place of the source it names."""
    for root, dirs, files in os.walk(worktree):
        for d in [d for d in dirs if d == "__pycache__"]:
            dirs.remove(d)
            p = os.path.join(root, d)
            if os.path.islink(p):
                os.unlink(p)
            else:
                shutil.rmtree(p, ignore_errors=True)
        for f in files:
            if f.endswith((".pyc", ".pyo")):
                os.unlink(os.path.join(root, f))


def clean_env(**extra):
    """The child's environment: an allowlist, so no key (OPENROUTER_API_KEY and its kin) reaches candidate code."""
    env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
    env.update(extra)
    return env


class ResultChannel:
    """Where a child writes its results: a file the grader chose outside the worktree, holding this run's random
    nonce. The child learns the path and the nonce from a spec file it reads and deletes before candidate code runs;
    the grader reads nothing from stdout and nothing from a file with another nonce. Same-user candidate code can
    still find them (see the module docstring); this stops the casual forgery, not a determined one."""

    def __init__(self, scratch):
        d = tempfile.mkdtemp(prefix="chan-", dir=scratch)
        self.nonce = secrets.token_hex(16)
        self.spec = os.path.join(d, "spec")
        self.result = os.path.join(d, "result")

    def write_spec(self, props=False, **fields):
        """JSON for the Python runner; `key=value` lines (java.util.Properties) for the Java reporter."""
        fields = dict(fields, nonce=self.nonce, result=self.result)
        with open(self.spec, "w") as f:
            if props:
                f.write("".join("%s=%s\n" % kv for kv in fields.items()))
            else:
                json.dump(fields, f)

    def read(self):
        """-> (the payload's dict, or None, a note). A missing, malformed or wrongly-nonced file is no result."""
        try:
            with open(self.result, encoding="utf-8") as f:
                payload = json.load(f)
        except OSError:
            return None, "no result file was written"
        except ValueError:
            return None, "the result file is malformed"
        if not isinstance(payload, dict) or payload.get("nonce") != self.nonce:
            return None, "the result file does not carry this run's nonce"
        return payload, ""


# ---------------------------------------------------------------- Java

def common_root(repo):
    """The working directory that owns `repo`'s git objects (the main checkout, for a linked worktree), or None."""
    p = subprocess.run(["git", "-C", repo, "rev-parse", "--git-common-dir"], capture_output=True, text=True)
    if p.returncode != 0:
        return None
    return os.path.dirname(os.path.realpath(os.path.join(repo, p.stdout.strip())))


def find_deps(explicit, repo):
    cands = [explicit, os.environ.get("HARNESS_DEPS_FILE")]
    for r in (repo, common_root(repo)):
        if r:
            found = sorted(glob.glob(os.path.join(r, "runs", "harness-acceptance", "*", "deps.txt")),
                           key=os.path.getmtime)
            cands += found[::-1]
    for c in cands:
        if c and os.path.isfile(c) and os.path.getsize(c) > 0:
            return c
    raise SetupError("no deps.txt (dependency classpath): pass --deps-file or set HARNESS_DEPS_FILE; "
                     "an acceptance run writes one, and this grader never runs mvn")


def find_rules(repo):
    """The Comprehensive Rules text is Wizards' and untracked: it comes from the grader's own checkout, never from
    the candidate's tree. (A test that derives the repository root from this path then reads the grader's checkout.)"""
    cands = [os.path.join(r, "data", "rules", "MagicCompRules.txt") for r in (repo, common_root(repo)) if r]
    return next((c for c in cands if os.path.isfile(c)), cands[0])


def java_home():
    """JAVA_HOME, then Homebrew's JDK, then the javac on PATH. On macOS /usr/bin/javac is a stub that only says no
    Java runtime is installed, so a Mac with Homebrew's JDK and no JAVA_HOME must not reach it."""
    homes = [os.environ.get("JAVA_HOME", "")] + [os.path.join(b, "libexec/openjdk.jdk/Contents/Home") for b in JDK_DIRS]
    for jh in homes:
        if jh and os.access(os.path.join(jh, "bin", "javac"), os.X_OK):
            return jh
    javac = shutil.which("javac")
    if not javac:
        raise SetupError("no javac on PATH and no JAVA_HOME")
    return os.path.dirname(os.path.dirname(os.path.realpath(javac)))


def java_sources(worktree, patched_engine):
    ov = os.path.join(worktree, OVERLAY_REL)
    files = sorted(glob.glob(os.path.join(ov, "main/java/forge/spike/*.java")) +
                   glob.glob(os.path.join(ov, "test/java/forge/spike/*.java")))
    for root, _, names in os.walk(os.path.join(worktree, "forge-overlay")):
        if "/src/main/java/" in root + "/" and "/forge/spike" not in root:
            files += [os.path.join(root, n) for n in sorted(names) if n.endswith(".java")]
    for root, _, names in os.walk(patched_engine):
        files += [os.path.join(root, n) for n in sorted(names) if n.endswith(".java")]
    for root, _, names in os.walk(os.path.join(ov, "test/java/forge/harnessenv")):
        files += [os.path.join(root, n) for n in sorted(names) if n.endswith(".java")]
    return files


def supplied_sources(repo, rev):
    """{path} the installed engine classes were built from, at `rev` of the grader's repository: overlay main
    sources and engine-patch diffs."""
    tree = het.git(repo, "ls-tree", "-r", "--name-only", rev, "forge-overlay", "engine-patches").splitlines()
    return {p for p in tree if (p.startswith("forge-overlay/") and "/src/main/java/" in p and p.endswith(".java"))
            or (p.startswith("engine-patches/") and p.endswith(".java" + engine_patches.SUFFIX))}


def missing_supplied_sources(keep, worktree):
    """Of `keep` (the supplied sources the gold patch keeps: present at the base and at the merge commit), those the
    candidate's tree lacks. Deleting one makes javac fall back to the installed build of the class, which may hold
    the fix."""
    return sorted(p for p in keep if not os.path.isfile(os.path.join(worktree, p)))


def class_prefix(source):
    """(package dir, class name) of the .class files a supplied source compiles to."""
    rel = source[len("engine-patches/"):-len(engine_patches.SUFFIX)] if source.startswith("engine-patches/") else source
    rel = rel.split("/src/main/java/", 1)[1]
    return os.path.dirname(rel), os.path.basename(rel)[:-len(".java")]


def filtered_engine_classes(forge, supplied, dest):
    """A hard-linked (else copied) copy of every module's target/classes under dest/<module>, without any class the
    overlay or an engine patch supplies (`supplied`, source paths): forge/spike/** whole, and the other supplied classes one by one. javac then
    fails on a deleted source instead of linking the installed build of it. -> the module dirs, in classpath order."""
    drop_dirs = {"forge/spike"}
    drop_classes = {class_prefix(p) for p in supplied}
    mods = []
    for m in ("forge-gui-desktop", "forge-core", "forge-game", "forge-ai", "forge-gui"):
        src_root = os.path.join(forge, m, "target", "classes")
        dst_root = os.path.join(dest, m)
        for root, dirs, files in os.walk(src_root):
            rel = os.path.relpath(root, src_root)
            rel = "" if rel == "." else rel
            dirs[:] = [d for d in dirs if os.path.join(rel, d) not in drop_dirs]
            os.makedirs(os.path.join(dst_root, rel), exist_ok=True)
            for f in files:
                if (rel, f.split("$")[0].split(".")[0]) in drop_classes:
                    continue
                s, d = os.path.join(root, f), os.path.join(dst_root, rel, f)
                try:
                    os.link(s, d)
                except OSError:
                    shutil.copy2(s, d)
        mods.append(dst_root)
    return mods


def compile_reporter(javac, cp, scratch):
    """The grader's own TestNG reporter (eval/harness_env/HarnessGraderReporter.java), compiled outside the worktree."""
    dest = os.path.join(scratch, "reporter")
    os.makedirs(dest)
    r = subprocess.run([javac, "--release", "17", "-proc:none", "-nowarn", "-cp", cp, "-d", dest, REPORTER_SRC],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SetupError("the grader's own reporter does not compile: " + (r.stdout + r.stderr)[-300:])
    return dest


def pin_repo(task, repo, worktree, dest):
    """What the engine-patch tool reads as its repository: the patches are the candidate's (data), the pin
    (FORGE_COMMIT) is the grader's, at the merge commit."""
    os.makedirs(dest)
    commit = het.read_blob(repo, task["merge_sha"], "FORGE_COMMIT")
    if commit is None:
        raise SetupError("FORGE_COMMIT is not in %s of the grader's repository" % task["merge_sha"][:9])
    with open(os.path.join(dest, "FORGE_COMMIT"), "wb") as f:
        f.write(commit)
    if os.path.isdir(os.path.join(worktree, "engine-patches")):
        shutil.copytree(os.path.join(worktree, "engine-patches"), os.path.join(dest, "engine-patches"))
    return dest


def is_engine_entry(entry):
    """A classpath entry that is an older build of one of the engine's own modules (forge-core-*.jar, ...)."""
    return os.path.basename(entry).startswith("forge")


def runtime_classpath(reporter, deps, classes, engine_mods):
    """The grader's reporter and the non-engine dependency jars (TestNG) come BEFORE the candidate's classes, so a
    class the candidate compiled can never replace one of theirs (foreign_classes also refuses it outright). The
    engine's own jars in `deps` (old builds of forge-core, -game, -ai, -gui) are dropped: `engine_mods` holds those
    modules' current classes, and an old jar ahead of the candidate's classes would beat a correct fix."""
    kept = [e for e in deps.split(":") if e and not is_engine_entry(e)]
    return ":".join([reporter] + kept + [classes] + list(engine_mods))


def foreign_classes(classes_dir, deps):
    """The classes the candidate's sources compiled to that they may not define: any outside the forge.* packages
    (the overlay is Forge's `forge` package tree; `org/testng/TestNG` is not theirs) and any with the name of a
    class in a dependency jar or directory other than an engine one (the .m2 holds older builds of the engine's
    modules). A source can declare a package its path does not show, so this reads the output, not the paths.
    -> sorted class paths."""
    made = set()
    for root, _, files in os.walk(classes_dir):
        made.update(os.path.relpath(os.path.join(root, f), classes_dir) for f in files if f.endswith(".class"))
    bad = {c for c in made if not c.startswith("forge" + os.sep)}
    for entry in deps.split(":"):
        if is_engine_entry(entry):
            continue  # an older build of the engine's own module: replacing its classes is what an overlay fix does
        if entry.endswith(".jar") and os.path.isfile(entry):
            with zipfile.ZipFile(entry) as z:
                bad |= made & set(z.namelist())
        elif os.path.isdir(entry):
            bad |= {c for c in made if os.path.isfile(os.path.join(entry, c))}
    return sorted(bad)


def write_argfile(path, files):
    """A javac @argfile: one double-quoted path per line (a space in a path would otherwise split it in two)."""
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join('"%s"\n' % p.replace("\\", "\\\\").replace('"', '\\"') for p in files))


def run_java(task, worktree, out, forge, deps_file, repo, scratch, timeout=3000):
    """Compile the candidate's overlay and run the task's test classes. -> (results, declared, compile_ok, notes)."""
    jh = java_home()
    javac, java = os.path.join(jh, "bin", "javac"), os.path.join(jh, "bin", "java")
    os.makedirs(out, exist_ok=True)
    classes, engine_src = os.path.join(scratch, "classes"), os.path.join(scratch, "patched-engine")
    os.makedirs(classes)
    base_src, merge_src = (supplied_sources(repo, task[k]) for k in ("base_sha", "merge_sha"))
    gone = missing_supplied_sources(base_src & merge_src, worktree)
    if gone:
        return {}, [], False, ["the candidate deleted source files the installed engine already holds a build of "
                               "(javac would link the old build): " + ", ".join(gone)]
    with open(deps_file) as f:
        deps = f.read().strip()
    engine_mods = filtered_engine_classes(forge, base_src | merge_src, os.path.join(scratch, "engine-classes"))
    cp = ":".join(engine_mods + [deps])
    pin = pin_repo(task, repo, worktree, os.path.join(scratch, "pin"))
    ep = subprocess.run([sys.executable, GRADER_ENGINE_PATCHES, "apply", "--repo", pin,
                         "--forge", forge, "--out", engine_src], capture_output=True, text=True)
    if ep.returncode != 0:
        return {}, [], False, ["engine patches do not apply: " + (ep.stdout + ep.stderr).strip()[-300:]]
    argfile = os.path.join(scratch, "sources.txt")
    write_argfile(argfile, java_sources(worktree, engine_src))
    jc = subprocess.run([javac, "--release", "17", "-encoding", "UTF-8", "-proc:none", "-nowarn", "-cp", cp,
                         "-d", classes, "@" + argfile], capture_output=True, text=True)
    with open(os.path.join(out, "javac.log"), "w") as f:
        f.write(jc.stdout + jc.stderr)
    if jc.returncode != 0:
        errs = [l for l in (jc.stdout + jc.stderr).splitlines() if "error:" in l][:3]
        return {}, [], False, ["javac failed: " + " | ".join(errs)]
    foreign = foreign_classes(classes, deps)
    if foreign:
        raise Tampered("the candidate's sources define classes they may not (outside the forge packages, or named "
                       "like a dependency's): " + ", ".join(foreign[:5]))
    reporter = compile_reporter(javac, deps, scratch)
    classes_fq = ["forge.spike." + c.split(".")[-1] if "." not in c else c for c in task["java_test_classes"]]
    declared = []
    for fq in classes_fq:
        src = os.path.join(worktree, OVERLAY_REL, "test/java", fq.replace(".", "/") + ".java")
        if os.path.isfile(src):
            with open(src, encoding="utf-8") as f:
                declared += ["%s.%s" % (fq, m) for m in het.java_tests(f.read())]
    # Forge finds its resources (../forge-gui/res) from the working directory: a scratch tree laid out like the
    # module tree, whose forge-gui/res is a farm of symlinks to the checkout's files, keeps the JVM's cwd out of it.
    cwdroot = os.path.join(scratch, "cwdroot")
    cwd = os.path.join(cwdroot, "forge-gui-desktop")
    os.makedirs(cwd)
    shutil.copytree(os.path.join(forge, "forge-gui", "res"), os.path.join(cwdroot, "forge-gui", "res"),
                    copy_function=os.symlink)
    cmd = [java, "-XX:ActiveProcessorCount=1", "-Dapple.awt.UIElement=true", "-Xmx2048m"]
    for o in ADDOPENS:
        cmd += ["--add-opens", o + "=ALL-UNNAMED"]
    cmd += ["-Dharness.ownership.tsv=" + os.path.join(worktree, "docs/harness/controller-ownership.tsv"),
            "-Dharness.surface.report=" + os.path.join(out, "surfaces.tsv"),
            "-Dharness.rules.file=" + find_rules(repo),
            "-Dharness.decks.dir=" + os.path.join(worktree, "eval/decks"),
            "-Dharness.tooluse.json=" + os.path.join(worktree, "eval/tooluse/tools.json"),
            SPEC_FLAG,
            "-cp", runtime_classpath(reporter, deps, classes, engine_mods), "org.testng.TestNG", "-listener", REPORTER_CLASS,
            "-testclass", ",".join(classes_fq), "-d", os.path.join(out, "testng")]
    results, jnotes = run_jvm(cmd, cwd, clean_env(MEMORY_TOOLS="1"), out, timeout, scratch)
    return results, declared, True, jnotes


def parse_reporter(payload):
    """The reporter's payload -> {'forge.spike.X.method': 'PASS'|'FAIL'|'SKIP'}, plus config failures
    ('forge.spike.X.<config>'). A method run several times (a data provider) fails if any run did."""
    out = {}
    for status, is_config, cls, method in payload["results"]:
        key = "%s.%s" % (cls, method)
        if is_config:
            if status != "PASS":
                out[key + "<config>"] = status
            continue
        out[key] = status if out.get(key) in (None, "PASS") else out[key]
    return out


def run_jvm(cmd, cwd, env, out, timeout, scratch):
    """Run the test JVM and read what the grader's own reporter wrote to the channel (in `scratch`). The SPEC_FLAG in
    `cmd` is replaced by the property naming this run's spec file. Nothing else is read: not testng-results.xml, not
    stdout. A crash, a timeout or a file with another nonce leaves no result: score 0. -> (results, notes)."""
    ch = ResultChannel(scratch)
    ch.write_spec(props=True)
    cmd = ["-Dharness.grader.spec=" + ch.spec if a == SPEC_FLAG else a for a in cmd]
    shutil.rmtree(os.path.join(out, "testng"), ignore_errors=True)
    try:
        r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
        with open(os.path.join(out, "testng.log"), "w") as f:
            f.write(r.stdout + r.stderr)
    except subprocess.TimeoutExpired:
        return {}, ["test JVM timed out after %ds: scored 0" % timeout]
    payload, note = ch.read()
    if payload is None:
        return {}, ["test JVM exited %d and left no usable result (%s): scored 0" % (r.returncode, note)]
    try:
        return parse_reporter(payload), []
    except (KeyError, TypeError, ValueError):
        return {}, ["the reporter's payload is malformed: scored 0"]


# ---------------------------------------------------------------- Python

# Runs in the child as `python -I -B <this file> <spec>`: isolated mode keeps the current directory and the
# environment's PYTHON* variables out, and this file lives in the grader's scratch, so a candidate json.py or
# unittest.py cannot shadow the stdlib here (sys.path holds the stdlib first; the candidate's directories are
# appended after it). Each test is run and recorded by this runner: an early sys.exit() in imported code or inside a
# test is a FAIL, never a pass. Results go to the channel file only. All the work is in main(): the nonce, the result
# path and the results are locals of its frames, so `import __main__` finds none of them. Code that inspects frames
# (sys._getframe, gc.get_objects) still can; see the note's Status.
PY_RUNNER = r'''
import importlib, json, os, sys, traceback, unittest


def main():
    with open(sys.argv[1]) as f:
        spec = json.load(f)
    os.unlink(sys.argv[1])
    nonce, out, module, names = spec["nonce"], spec["result"], spec["module"], spec["names"]
    sys.pycache_prefix = spec["pycache"]
    sys.path.extend([spec["tests_dir"], spec["worktree"]])
    res = {}

    def finish():
        with open(out, "w") as f:
            json.dump({"nonce": nonce, "results": res}, f)
        sys.stdout.flush()
        os._exit(0)

    class R(unittest.TestResult):
        def _k(self, t):
            i = t.id()
            return i[len(module) + 1:] if i.startswith(module + ".") else i
        def addSuccess(self, t): res[self._k(t)] = "PASS"
        def addFailure(self, t, e): res[self._k(t)] = "FAIL"
        def addError(self, t, e): res[self._k(t)] = "FAIL"
        def addSkip(self, t, r): res[self._k(t)] = "SKIP"
        def addSubTest(self, t, s, e):
            if e is not None: res[self._k(t)] = "FAIL"

    try:
        mod = importlib.import_module(module)
    except BaseException:  # SystemExit included: an import that quits has run nothing
        traceback.print_exc()
        res.update({n: "FAIL" for n in names})
        finish()
    try:
        unittest.defaultTestLoader.loadTestsFromModule(mod).run(R())
    except BaseException:
        traceback.print_exc()
    fails = getattr(mod, "FAILURES", None)  # the script style's own tally: a check() that fails appends here
    for n in names:
        if "." in n:
            continue
        fn = getattr(mod, n, None)
        before = len(fails) if isinstance(fails, list) else 0
        try:
            fn()
            res[n] = "FAIL" if isinstance(fails, list) and len(fails) > before else "PASS"
        except unittest.SkipTest:
            res[n] = "SKIP"
        except BaseException:  # a test that calls sys.exit() fails, whatever the code
            traceback.print_exc()
            res[n] = "FAIL"
    finish()


main()
'''


def run_python(task, worktree, scratch, timeout=1200):
    """-> (results {key: status}, declared keys, notes); a test file that times out has no results (score 0)."""
    results, declared, notes = {}, [], []
    runner = os.path.join(scratch, "runner.py")
    with open(runner, "w") as f:
        f.write(PY_RUNNER)
    pycache = os.path.join(scratch, "pycache")
    os.makedirs(pycache, exist_ok=True)
    purge_bytecode(worktree)
    for pf in task["python_tests"]:
        path = os.path.join(worktree, pf["file"])
        if not os.path.isfile(path):
            notes.append("missing test file " + pf["file"])
            continue
        with open(path, encoding="utf-8") as f:
            names = sorted(het.python_tests(f.read()))
        declared += ["%s.%s" % (pf["module"], n) for n in names]
        ch = ResultChannel(scratch)
        ch.write_spec(tests_dir=os.path.dirname(path), worktree=worktree, module=pf["module"], names=names,
                      pycache=pycache)
        try:
            p = subprocess.run([sys.executable, "-I", "-B", runner, ch.spec], cwd=worktree, env=clean_env(),
                               capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            notes.append("%s timed out after %ds: scored 0" % (pf["file"], timeout))
            continue
        payload, note = ch.read()
        got = payload.get("results") if payload else None
        if not isinstance(got, dict):
            tail = (p.stdout + p.stderr).strip()[-200:]
            notes.append("%s left no usable result (%s): %s" % (pf["file"], note or "malformed", tail))
            continue
        for k, v in got.items():
            results["%s.%s" % (pf["module"], k)] = v if v in ("PASS", "FAIL", "SKIP") else "FAIL"
        bad = sorted(k for k, v in got.items() if v != "PASS")
        if bad:
            notes.append("%s: %d test(s) not passing, e.g. %s" % (pf["file"], len(bad), bad[0]))
    return results, declared, notes


# ---------------------------------------------------------------- grade

def target_key(t):
    return "%s.%s" % (t["class"] if t["kind"] == "java" else t["module"], t["method"])


def pick(doc, task_id):
    for t in doc["tasks"]:
        if task_id in (t["id"], t.get("view_id")):
            return t
    raise SetupError("no task %s in the manifest" % task_id)


def grade(task, worktree, out, forge=DEFAULT_FORGE, deps_file=None, repo=None):
    """Score a candidate tree. `repo` is the grader's own repository (it holds the merge commit the tests come from)."""
    worktree = os.path.abspath(worktree)
    out = os.path.abspath(out)
    refuse_forge_paths(forge, worktree, out)
    if not os.path.isdir(worktree):
        raise SetupError("no worktree " + worktree)
    if not repo:
        raise SetupError("grade needs the grader's repository (--repo): the tests are restored from its history")
    os.makedirs(out, exist_ok=True)
    for stale in ("result.json", "javac.log"):  # an earlier grade's files must not read as this one's
        if os.path.lexists(os.path.join(out, stale)):
            os.unlink(os.path.join(out, stale))
    guard = ForgeGuard(forge) if task["needs_jvm"] and os.path.isdir(forge) else None
    scratch = tempfile.mkdtemp(prefix="harness-env-grade-")  # the grader's, outside the worktree and out
    modified, results, declared, notes, compile_ok = [], {}, [], [], True
    try:
        try:
            modified = restore_files(task, repo, worktree, task["test_files"])
            restore_files(task, repo, worktree, readonly_inputs(task, repo))
            if task["java_test_classes"]:
                deps = find_deps(deps_file, repo)
                r, d, compile_ok, n = run_java(task, worktree, out, forge, deps, repo, scratch)
                results.update(r)
                declared += d
                notes += n
            if task["python_tests"]:
                r, d, n = run_python(task, worktree, scratch)
                results.update(r)
                declared += d
                notes += n
        except Tampered as e:
            results, declared, notes = {}, [], ["the candidate's tree is refused (score 0): %s" % e]
        res = score([target_key(t) for t in task["targets"]], results, declared, compile_ok)
        forge_problems = guard.problems() if guard else []
    finally:
        if guard:
            guard.close()
        shutil.rmtree(scratch, ignore_errors=True)
    problems = list(forge_problems)
    if modified:
        problems.append("the candidate changed the task's test files (restored, score 0): " + ", ".join(modified))
    if problems:
        res["score"], res["partial"] = 0, 0.0
        notes += problems
    res.update({"task": task["id"], "worktree": worktree, "notes": notes, "tests_modified": modified,
                "forge_untouched": not forge_problems,
                "graded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    with open(os.path.join(out, "result.json"), "w") as f:
        json.dump(res, f, indent=1, sort_keys=True)
        f.write("\n")
    return res


# ---------------------------------------------------------------- prepare / validate

def prepare(task, repo, dest, gold=False):
    """The base tree plus the PR's test files (and, with gold, the PR's source files), as ONE fresh commit: the
    sandbox shares no object, ref or remote with `repo`, so no log or show of it can reach the fix."""
    if os.path.exists(dest) and os.listdir(dest):
        raise SetupError("refusing a non-empty destination " + dest)
    os.makedirs(dest, exist_ok=True)
    # the tree id, not the commit id: `git archive <commit>` records the commit in a tar header comment
    arch = subprocess.Popen(["git", "-C", repo, "archive", "--format=tar", task["base_sha"] + "^{tree}"],
                            stdout=subprocess.PIPE)
    untar = subprocess.run(["tar", "-x", "-C", dest], stdin=arch.stdout, capture_output=True)
    arch.stdout.close()
    if arch.wait() != 0 or untar.returncode != 0:
        raise SetupError("git archive of the base tree failed: " + untar.stderr.decode()[-200:])
    restore_files(task, repo, dest, task["test_files"])
    if gold:
        restore_files(task, repo, dest, task["gold_files"], must_exist=False)
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "task start", "--no-gpg-sign"]):
        het.git(dest, "-c", "user.name=task", "-c", "user.email=task@invalid", *args)


def provenance(forge, repo):
    """What a validation ran under: the grader's file hash, the engine checkout's HEAD, the grader's repository HEAD
    and the time (UTC). A validation is only evidence for the grader and the engine that produced it."""
    def head(path):
        p = subprocess.run(["git", "-C", path, "rev-parse", "HEAD"], capture_output=True, text=True)
        return p.stdout.strip() if p.returncode == 0 else None
    with open(os.path.abspath(__file__), "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return {"grader_sha256": digest, "engine_head": head(forge), "repo_head": head(repo),
            "validated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def validate(task, repo, work, forge, deps_file):
    """Red on base + tests, green with the gold patch. -> dict with both scores and `well_posed`."""
    dest = os.path.join(work, task["id"] + "-wt")
    out = {"task": task["id"], "gold_lines": task["gold_lines"]}
    shutil.rmtree(dest, ignore_errors=True)
    try:
        prepare(task, repo, dest, gold=False)
        red = grade(task, dest, os.path.join(work, task["id"] + "-red"), forge, deps_file, repo)
        restore_files(task, repo, dest, task["gold_files"], must_exist=False)
        green = grade(task, dest, os.path.join(work, task["id"] + "-green"), forge, deps_file, repo)
    finally:
        shutil.rmtree(dest, ignore_errors=True)
    out["red"] = {k: red[k] for k in ("score", "partial", "compile_ok", "targets_passed", "targets_total",
                                      "failed_targets", "regressions", "notes")}
    out["green"] = {k: green[k] for k in out["red"]}
    out["well_posed"] = red["score"] == 0 and green["score"] == 1 and red["forge_untouched"] and green["forge_untouched"]
    # A red that fails only because nothing compiled proves little: record which kind of red it is.
    out["red_kind"] = "build" if not red["compile_ok"] else "tests"
    out.update(provenance(forge, repo))
    with open(os.path.join(work, task["id"] + "-validation.json"), "w") as f:
        json.dump(out, f, indent=1, sort_keys=True)
        f.write("\n")
    return out


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("grade", "validate", "prepare"):
        p = sub.add_parser(name)
        p.add_argument("tasks")
        p.add_argument("--task-id", required=True)
        p.add_argument("--forge", default=os.path.expanduser(os.path.expandvars(os.environ.get("FORGE_HOME") or DEFAULT_FORGE)))
        p.add_argument("--deps-file")
        p.add_argument("--repo", default=os.getcwd())
        if name == "grade":
            p.add_argument("worktree")
            p.add_argument("--out", default=None)
        elif name == "prepare":
            p.add_argument("dest")
            p.add_argument("--gold", action="store_true")
        else:
            p.add_argument("--work", required=True)
    a = ap.parse_args(argv)
    try:
        with open(a.tasks, encoding="utf-8") as f:
            task = pick(json.load(f), a.task_id)
        if a.cmd == "prepare":
            prepare(task, a.repo, a.dest, a.gold)
            print("prepared", a.dest)
            return 0
        if a.cmd == "grade":
            out = a.out or tempfile.mkdtemp(prefix="harness-env-grade-")
            res = grade(task, a.worktree, out, a.forge, a.deps_file, a.repo)
            print(json.dumps(res, indent=1, sort_keys=True))
            return 0
        os.makedirs(a.work, exist_ok=True)
        refuse_forge_paths(a.forge, a.work)
        res = validate(task, a.repo, a.work, a.forge, a.deps_file)
        print(json.dumps(res, indent=1, sort_keys=True))
        return 0 if res["well_posed"] else 1
    except (SetupError, het.TaskError) as e:
        print("error:", e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
