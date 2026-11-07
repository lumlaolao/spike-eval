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

How a Java task is run: the overlay of the CANDIDATE worktree is compiled with javac into a scratch directory over
the pinned engine's classes (the same compile as eval/harness_acceptance.sh, engine patches applied to the pinned
originals by the worktree's own eval/engine_patches.py), then the named TestNG classes run in one JVM with the same
system properties. The acceptance script has no class filter (STAGES picks stages, not classes), and its compile
stage needs `mvn` for the classpath, which this grader never runs: it takes the dependency classpath from a
`deps.txt` an earlier acceptance run wrote (--deps-file, HARNESS_DEPS_FILE, or the newest under a runs/ tree).
A Python task runs its test file in a subprocess (TestCase methods by unittest, module-level test_ functions by the file's own main).

It never writes to the Forge checkout ($FORGE_HOME, /root/forge): the engine is read for its classes and sources
only; a `git status` and a "no file under target/ is newer" check run before and after, and a change fails the grade.

Usage:
  harness_env_task_grade.py grade <tasks.json> <worktree> --task-id pr403 [--out DIR] [--deps-file F] [--forge F]
  harness_env_task_grade.py validate <tasks.json> --task-id pr403 --work DIR [--repo .] [--deps-file F]
  harness_env_task_grade.py prepare <tasks.json> <dest> --task-id pr403 [--gold] [--repo .]
`validate` is the well-posedness check: base + the PR's tests must score 0, base + gold patch + tests must score 1.
Run any JVM step under the shared lock (CLAUDE.md). Exit 0 graded (read `score`), 2 setup error, 1 validate failed.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import harness_env_tasks as het

DEFAULT_FORGE = "/root/forge"
OVERLAY_REL = "forge-overlay/forge-gui-desktop/src"
ADDOPENS = ("java.desktop/java.beans java.desktop/javax.swing.border java.desktop/javax.swing.event "
            "java.desktop/sun.swing java.desktop/java.awt.image java.desktop/java.awt.color "
            "java.desktop/sun.awt.image java.desktop/javax.swing java.desktop/java.awt java.desktop/java.awt.font "
            "java.base/java.util java.base/java.lang java.base/java.lang.reflect java.base/java.text "
            "java.base/jdk.internal.misc java.base/sun.nio.ch java.base/java.nio java.base/java.math "
            "java.base/java.util.concurrent java.base/java.net").split()
WEIGHT_TARGET = 0.7


class SetupError(RuntimeError):
    pass


# ---------------------------------------------------------------- scoring (pure)

def score(targets, results, declared=None, compile_ok=True):
    """targets: [(key, ...)] keys; results: {key: 'PASS'|'FAIL'|'SKIP'|...} of every test run in the task's classes;
    declared: keys the classes declare (a declared test absent from results is missing, not passed)."""
    target_keys = [t for t in targets]
    declared = set(declared or []) | set(results) | set(target_keys)
    others = sorted(k for k in declared if k not in set(target_keys))
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


def parse_testng(xml_path):
    """{'forge.spike.X.method': 'PASS'|'FAIL'|'SKIP'}, plus config failures ('forge.spike.X.<config>')."""
    out = {}
    try:
        root = ET.parse(xml_path).getroot()
    except (OSError, ET.ParseError):
        return out
    for cls in root.iter("class"):
        cname = cls.get("name", "")
        for m in cls.iter("test-method"):
            key = "%s.%s" % (cname, m.get("name"))
            status = m.get("status", "")
            if m.get("is-config") == "true":
                if status != "PASS":
                    out[key + "<config>"] = status
                continue
            prior = out.get(key)
            out[key] = status if prior in (None, "PASS") else prior
    return out


# ---------------------------------------------------------------- the Forge guard

class ForgeGuard:
    """Proves the engine checkout was only read: same HEAD, same `git status`, no file under */target/ newer."""

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
        for target in glob.glob(os.path.join(self.forge, "forge-*", "target")):
            for root, _, files in os.walk(target):
                for f in files:
                    p = os.path.join(root, f)
                    try:
                        if os.stat(p).st_mtime > os.stat(self.marker.name).st_mtime:
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
            out.append("a file under the engine's target/ is newer than the grade's start: " + n)
        os.unlink(self.marker.name)
        return out


def refuse_forge_paths(forge, *paths):
    f = os.path.realpath(forge)
    for p in paths:
        rp = os.path.realpath(p)
        if rp == f or rp.startswith(f + os.sep):
            raise SetupError("%s is inside the Forge checkout %s: the grader never writes there" % (p, forge))


# ---------------------------------------------------------------- Java

def find_deps(explicit, repo):
    cands = [explicit, os.environ.get("HARNESS_DEPS_FILE")]
    roots = [repo]
    p = subprocess.run(["git", "-C", repo, "rev-parse", "--git-common-dir"], capture_output=True, text=True)
    if p.returncode == 0:
        roots.append(os.path.dirname(os.path.realpath(os.path.join(repo, p.stdout.strip()))))
    for r in roots:
        found = sorted(glob.glob(os.path.join(r, "runs", "harness-acceptance", "*", "deps.txt")), key=os.path.getmtime)
        cands += found[::-1]
    for c in cands:
        if c and os.path.isfile(c) and os.path.getsize(c) > 0:
            return c
    raise SetupError("no deps.txt (dependency classpath): pass --deps-file or set HARNESS_DEPS_FILE; "
                     "an acceptance run writes one, and this grader never runs mvn")


def find_rules(worktree):
    cands = [os.path.join(worktree, "data", "rules", "MagicCompRules.txt")]
    p = subprocess.run(["git", "-C", worktree, "rev-parse", "--git-common-dir"], capture_output=True, text=True)
    if p.returncode == 0:
        root = os.path.dirname(os.path.realpath(os.path.join(worktree, p.stdout.strip())))
        cands.append(os.path.join(root, "data", "rules", "MagicCompRules.txt"))
    for c in cands:
        if os.path.isfile(c):
            return c
    return cands[0]


def java_home():
    jh = os.environ.get("JAVA_HOME", "")
    if not (jh and os.access(os.path.join(jh, "bin", "javac"), os.X_OK)):
        javac = shutil.which("javac")
        if not javac:
            raise SetupError("no javac on PATH and no JAVA_HOME")
        jh = os.path.dirname(os.path.dirname(os.path.realpath(javac)))
    return jh


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


def run_java(task, worktree, out, forge, deps_file, repo, timeout=3000):
    """Compile the candidate's overlay and run the task's test classes. -> (results, declared, compile_ok, notes)."""
    jh = java_home()
    javac, java = os.path.join(jh, "bin", "javac"), os.path.join(jh, "bin", "java")
    os.makedirs(out, exist_ok=True)
    classes, engine_src = os.path.join(out, "classes"), os.path.join(out, "patched-engine")
    shutil.rmtree(classes, ignore_errors=True)
    shutil.rmtree(engine_src, ignore_errors=True)
    os.makedirs(classes)
    notes = []
    mods = "".join("%s/%s/target/classes:" % (forge, m) for m in
                   ("forge-gui-desktop", "forge-core", "forge-game", "forge-ai", "forge-gui"))
    with open(deps_file) as f:
        cp = mods + f.read().strip()
    ep = subprocess.run([sys.executable, os.path.join(worktree, "eval", "engine_patches.py"), "apply",
                         "--forge", forge, "--out", engine_src], capture_output=True, text=True)
    if ep.returncode != 0:
        return {}, [], False, ["engine patches do not apply: " + (ep.stdout + ep.stderr).strip()[-300:]]
    srcs = java_sources(worktree, engine_src)
    argfile = os.path.join(out, "sources.txt")
    with open(argfile, "w") as f:
        f.write("\n".join(srcs) + "\n")
    jc = subprocess.run([javac, "--release", "17", "-encoding", "UTF-8", "-proc:none", "-nowarn", "-cp", cp,
                         "-d", classes, "@" + argfile], capture_output=True, text=True)
    with open(os.path.join(out, "javac.log"), "w") as f:
        f.write(jc.stdout + jc.stderr)
    if jc.returncode != 0:
        errs = [l for l in (jc.stdout + jc.stderr).splitlines() if "error:" in l][:3]
        return {}, [], False, ["javac failed: " + " | ".join(errs)]
    shutil.rmtree(engine_src, ignore_errors=True)
    classes_fq = ["forge.spike." + c.split(".")[-1] if "." not in c else c for c in task["java_test_classes"]]
    declared = []
    for fq in classes_fq:
        src = os.path.join(worktree, OVERLAY_REL, "test/java", fq.replace(".", "/") + ".java")
        if os.path.isfile(src):
            with open(src, encoding="utf-8") as f:
                declared += ["%s.%s" % (fq, m) for m in het.java_tests(f.read())]
    # Forge finds its resources (../forge-gui/res) from the working directory: a scratch directory laid out
    # like the module tree, whose sibling forge-gui is a link to the checkout's, keeps the JVM out of the checkout.
    cwd = os.path.join(out, "cwdroot", "forge-gui-desktop")
    shutil.rmtree(os.path.join(out, "cwdroot"), ignore_errors=True)
    os.makedirs(cwd)
    os.symlink(os.path.join(forge, "forge-gui"), os.path.join(out, "cwdroot", "forge-gui"))
    tsv = os.path.join(worktree, "docs/harness/controller-ownership.tsv")
    cmd = [java, "-XX:ActiveProcessorCount=1", "-Dapple.awt.UIElement=true", "-Xmx2048m"]
    for o in ADDOPENS:
        cmd += ["--add-opens", o + "=ALL-UNNAMED"]
    cmd += ["-Dharness.ownership.tsv=" + tsv,
            "-Dharness.surface.report=" + os.path.join(out, "surfaces.tsv"),
            "-Dharness.rules.file=" + find_rules(worktree),
            "-Dharness.decks.dir=" + os.path.join(worktree, "eval/decks"),
            "-Dharness.tooluse.json=" + os.path.join(worktree, "eval/tooluse/tools.json"),
            "-cp", classes + ":" + cp, "org.testng.TestNG", "-testclass", ",".join(classes_fq),
            "-d", os.path.join(out, "testng")]
    env = {k: v for k, v in os.environ.items() if k not in ("EVAL_OUT", "OPENROUTER_ALLOW_EGRESS", "OPENROUTER_API_KEY")}
    env["MEMORY_TOOLS"] = "1"
    try:
        r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
        with open(os.path.join(out, "testng.log"), "w") as f:
            f.write(r.stdout + r.stderr)
    except subprocess.TimeoutExpired:
        notes.append("test JVM timed out after %ds" % timeout)
    results = parse_testng(os.path.join(out, "testng", "testng-results.xml"))
    shutil.rmtree(classes, ignore_errors=True)  # disk is tight; the log and the results stay
    return results, declared, True, notes


# ---------------------------------------------------------------- Python

PY_RUNNER = r'''
import json, sys, unittest
tests_dir, module = sys.argv[1], sys.argv[2]
sys.path.insert(0, tests_dir)
res = {}
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
suite = unittest.defaultTestLoader.loadTestsFromName(module)
suite.run(R())
print("RESULT " + json.dumps(res))
'''


def run_python(task, worktree, timeout=1200):
    """-> (results {key: status}, declared keys, notes). Key: '<module>.<name>' (name = Class.method or test_fn)."""
    results, declared, notes = {}, [], []
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for pf in task["python_tests"]:
        path = os.path.join(worktree, pf["file"])
        if not os.path.isfile(path):
            notes.append("missing test file " + pf["file"])
            continue
        with open(path, encoding="utf-8") as f:
            names = sorted(het.python_tests(f.read()))
        declared += ["%s.%s" % (pf["module"], n) for n in names]
        tests_dir = os.path.dirname(path)
        cases = [n for n in names if "." in n]
        funcs = [n for n in names if "." not in n]
        if cases or not funcs:  # TestCase methods are collected by unittest, one result each
            p = subprocess.run([sys.executable, "-c", PY_RUNNER, tests_dir, pf["module"]], cwd=worktree, env=env,
                               capture_output=True, text=True, timeout=timeout)
            m = re.search(r"^RESULT (.*)$", p.stdout, re.M)
            for k, v in (json.loads(m.group(1)) if m else {}).items():
                results["%s.%s" % (pf["module"], k)] = v
        if funcs:  # module-level test_ functions are run by the file's own main(): they pass or fail whole
            p = subprocess.run([sys.executable, path], cwd=worktree, env=env, capture_output=True, text=True,
                               timeout=timeout)
            for n in funcs:
                results["%s.%s" % (pf["module"], n)] = "PASS" if p.returncode == 0 else "FAIL"
            if p.returncode != 0:
                notes.append("%s exited %d: %s" % (pf["file"], p.returncode, (p.stdout + p.stderr).strip()[-200:]))
    return results, declared, notes


# ---------------------------------------------------------------- grade

def target_key(t):
    if t["kind"] == "java":
        return "%s.%s" % (t["class"], t["method"])
    return "%s.%s" % (t["module"], t["method"])


def pick(doc, task_id):
    for t in doc["tasks"]:
        if t["id"] == task_id:
            return t
    raise SetupError("no task %s in the manifest" % task_id)


def grade(task, worktree, out, forge=DEFAULT_FORGE, deps_file=None, repo=None):
    worktree = os.path.abspath(worktree)
    out = os.path.abspath(out)
    refuse_forge_paths(forge, worktree, out)
    if not os.path.isdir(worktree):
        raise SetupError("no worktree " + worktree)
    guard = ForgeGuard(forge) if task["needs_jvm"] and os.path.isdir(forge) else None
    results, declared, notes, compile_ok = {}, [], [], True
    if task["java_test_classes"]:
        deps = find_deps(deps_file, repo or worktree)
        r, d, compile_ok, n = run_java(task, worktree, out, forge, deps, repo or worktree)
        results.update(r)
        declared += d
        notes += n
    if task["python_tests"]:
        r, d, n = run_python(task, worktree)
        results.update(r)
        declared += d
        notes += n
    res = score([target_key(t) for t in task["targets"]], results, declared, compile_ok)
    problems = guard.problems() if guard else []
    if problems:
        res["score"], res["partial"] = 0, 0.0
        notes += problems
    res.update({"task": task["id"], "worktree": worktree, "notes": notes,
                "forge_untouched": not problems, "graded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "result.json"), "w") as f:
        json.dump(res, f, indent=1, sort_keys=True)
        f.write("\n")
    return res


# ---------------------------------------------------------------- prepare / validate

def prepare(task, repo, dest, gold=False):
    """Worktree at the base sha, plus the PR's test files (and, with gold, the PR's source files)."""
    if os.path.exists(dest) and os.listdir(dest):
        raise SetupError("refusing a non-empty destination " + dest)
    het.git(repo, "worktree", "add", "--detach", dest, task["base_sha"])
    apply_files(task, repo, dest, task["test_files"])
    if gold:
        apply_files(task, repo, dest, task["gold_files"])


def apply_files(task, repo, dest, paths):
    for p in paths:
        exists = subprocess.run(["git", "-C", repo, "cat-file", "-e", "%s:%s" % (task["merge_sha"], p)],
                                capture_output=True).returncode == 0
        if exists:
            het.git(dest, "checkout", task["merge_sha"], "--", p)
        elif os.path.exists(os.path.join(dest, p)):
            os.unlink(os.path.join(dest, p))


def remove_worktree(repo, dest):
    subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", dest], capture_output=True)
    shutil.rmtree(dest, ignore_errors=True)


def validate(task, repo, work, forge, deps_file):
    """Red on base + tests, green with the gold patch. -> dict with both scores and `well_posed`."""
    dest = os.path.join(work, task["id"] + "-wt")
    out = {"task": task["id"], "gold_lines": task["gold_lines"]}
    try:
        prepare(task, repo, dest, gold=False)
        red = grade(task, dest, os.path.join(work, task["id"] + "-red"), forge, deps_file, repo)
        apply_files(task, repo, dest, task["gold_files"])
        green = grade(task, dest, os.path.join(work, task["id"] + "-green"), forge, deps_file, repo)
    finally:
        remove_worktree(repo, dest)
    out["red"] = {k: red[k] for k in ("score", "partial", "compile_ok", "targets_passed", "targets_total",
                                      "failed_targets", "regressions", "notes")}
    out["green"] = {k: green[k] for k in out["red"]}
    out["well_posed"] = red["score"] == 0 and green["score"] == 1 and red["forge_untouched"] and green["forge_untouched"]
    # A red that fails only because nothing compiled proves little: record which kind of red it is.
    out["red_kind"] = "build" if not red["compile_ok"] else "tests"
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
