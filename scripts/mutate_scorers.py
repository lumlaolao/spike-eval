#!/usr/bin/env python3
"""Mutation-test the scoring modules: does their own test file notice a one-site change to the code?

For a target module (default: the scorers behind STR-AI, STR-HELDOUT and PUZ) the script lists every single-site
mutant, samples up to N of them with a seeded generator, and for each one overlays the mutated module on a temp
copy of the repository and runs only that module's test file with `python3 -I`. A mutant the test file fails on is
KILLED, one it passes on SURVIVED (a test gap, or an equivalent mutant: a human judges which), a timeout or a
death by signal is ERROR (left out of the score: no test failed). Stdlib only; no model call, no JVM; the temp copy lives under
`tempfile.mkdtemp()` (set TMPDIR to choose where) and is deleted at the end.

Operators (one site each): comparison flip (`<`/`<=`, `>`/`>=`, `==`/`!=`, `is`/`is not`, `in`/`not in`), `and`/`or`,
`+`/`-`, `*`/`/`, numeric constant (0/1 swap, else +1), `True`/`False`, `not x` to `x`, `return v` to `return None`,
an `if` body to `pass`, `continue` to `pass`.

Before any target the tool checks itself (`self_check`): a planted equivalent mutant (`x + 0`) must survive and a
planted bug must be killed, and the unmutated copy of every target must pass its own tests; if not it exits 2.

    python3 scripts/mutate_scorers.py                       # every default target, 40 mutants each, seed 0
    python3 scripts/mutate_scorers.py eval/sprt.py --max 10 --json out.json --recheck
"""
import argparse
import ast
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_TARGETS = ("eval/aggregate.py", "eval/panel_score.py", "eval/puzzles/puzzle_score.py", "eval/sanity_arms.py",
                   "eval/rating.py", "eval/sprt.py", "eval/seed_policy.py", "eval/rl/eval_only.py")
COPY_DIRS = ("eval", "docs", "scripts", "forge-overlay")   # forge-overlay is 3 MB: some tests read its Java as text
KILLED, SURVIVED, ERROR = "killed", "survived", "error"

_SWAP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
         ast.Is: ast.IsNot, ast.IsNot: ast.Is, ast.In: ast.NotIn, ast.NotIn: ast.In}
_ARITH = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult}


class Site:
    """One mutation: its source line, kind, a description, and `apply()`, which edits the tree it was found in."""

    def __init__(self, node, kind, desc, apply):
        self.line, self.kind, self.desc, self.apply = node.lineno, kind, desc, apply


def _constant(v):
    if isinstance(v, bool):
        return not v
    return 1 if v == 0 else 0 if v == 1 else v + 1


def _trivial_if(node):
    """An `if` the guard mutant would leave unchanged or break: a `__name__` guard, a body that is only pass/docs."""
    if "__name__" in ast.dump(node.test):
        return True
    return all(isinstance(s, ast.Pass) or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
               for s in node.body)


def sites(tree):
    """Every mutation site of a parsed module, in a fixed order (walk order, then operand order)."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                if type(op) in _SWAP:
                    new = _SWAP[type(op)]
                    out.append(Site(node, "compare", "%s -> %s" % (type(op).__name__, new.__name__),
                                    lambda n=node, i=i, new=new: n.ops.__setitem__(i, new())))
        elif isinstance(node, ast.BoolOp):
            new = ast.Or if isinstance(node.op, ast.And) else ast.And
            out.append(Site(node, "boolop", "%s -> %s" % (type(node.op).__name__, new.__name__),
                            lambda n=node, new=new: setattr(n, "op", new())))
        elif isinstance(node, ast.BinOp) and type(node.op) in _ARITH:
            new = _ARITH[type(node.op)]
            out.append(Site(node, "arith", "%s -> %s" % (type(node.op).__name__, new.__name__),
                            lambda n=node, new=new: setattr(n, "op", new())))
        elif isinstance(node, ast.Constant) and isinstance(node.value, (bool, int, float)):
            new = _constant(node.value)
            out.append(Site(node, "constant", "%r -> %r" % (node.value, new),
                            lambda n=node, new=new: setattr(n, "value", new)))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            # `not not x` is `bool(x)`: the guard's negation is gone
            out.append(Site(node, "not", "not x -> bool(x)",
                            lambda n=node: setattr(n, "operand", ast.UnaryOp(op=ast.Not(), operand=n.operand))))
        elif isinstance(node, ast.Return) and node.value is not None and not (
                isinstance(node.value, ast.Constant) and node.value.value is None):
            out.append(Site(node, "return", "return v -> return None", lambda n=node: setattr(n, "value", None)))
        elif isinstance(node, ast.If) and not _trivial_if(node):
            out.append(Site(node, "guard", "if-body -> pass", lambda n=node: setattr(n, "body", [ast.Pass()])))
        for field, value in ast.iter_fields(node):
            if isinstance(value, list):
                for i, child in enumerate(value):
                    if isinstance(child, ast.Continue):
                        out.append(Site(child, "continue", "continue -> pass",
                                        lambda v=value, i=i: v.__setitem__(i, ast.Pass())))
    return out


def mutant_source(source, index):
    """(source of mutant `index`, its Site); an index into `sites(ast.parse(source))`."""
    tree = ast.parse(source)
    site = sites(tree)[index]
    site.apply()
    ast.fix_missing_locations(tree)
    return ast.unparse(tree), site


def sample(source, n, seed):
    """Up to `n` site indexes, drawn with a seeded generator, in source order."""
    total = len(sites(ast.parse(source)))
    return sorted(random.Random(seed).sample(range(total), min(n, total))), total


# ---------------------------------------------------------------- running a mutant

RUNNER = ("import runpy, sys; sys.path.insert(0, sys.argv[1]); sys.argv = sys.argv[2:]; "
          "runpy.run_path(sys.argv[0], run_name='__main__')")


def _env(tmp):
    keep = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL")}
    keep["TMPDIR"] = tmp    # the test's own temp files: removed with the run, shared with no other run
    keep["PYTHONHASHSEED"] = "0"            # set order must not decide whether a mutant is caught (run to run)
    keep["PYTHONDONTWRITEBYTECODE"] = "1"   # a same-size mutant must never read a stale .pyc
    return keep


def run_test(root, test, timeout):
    """Run one test file in `root` with `python3 -I`; returns (status, seconds, last output lines).

    `-I` drops the script's folder from sys.path (some tests import a sibling test), so the test's folder is put
    back by a one-line runner; nothing else about the isolated mode changes.
    """
    t0 = time.time()
    cmd = [sys.executable, "-I", "-c", RUNNER, str(Path(test).parent), str(test)]
    tmp = tempfile.mkdtemp(prefix="run-", dir=root)
    log = Path(tmp) / "output.txt"   # a file, not a pipe: a server the test leaves behind would hold a pipe open
    try:
        with open(log, "w") as sink:
            proc = subprocess.Popen(cmd, cwd=root, env=_env(tmp), stdout=sink, stderr=subprocess.STDOUT,
                                    text=True, start_new_session=True)
            try:
                proc.wait(timeout=timeout)
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)   # our own child's group only: the test and what it started
            except ProcessLookupError:
                pass
            proc.wait()
        out = log.read_text(errors="replace")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if timed_out:
        return ERROR, time.time() - t0, "timeout after %ss" % timeout
    tail = "\n".join(out.strip().splitlines()[-3:])
    if proc.returncode < 0:   # the run died by a signal (the out-of-memory killer): no test failed, so no kill
        return ERROR, time.time() - t0, "died by signal %d: %s" % (-proc.returncode, tail)
    return (SURVIVED if proc.returncode == 0 else KILLED), time.time() - t0, tail


def run_confirmed(root, test, timeout):
    """`run_test` twice: a verdict must repeat. A test that fails once and passes once is a flaky run (a loaded
    machine, a clock), and counting it as a kill would raise the mutation score by chance; a pass that does not
    repeat would hide a kill the same way. The runs that disagree are ERROR, never counted."""
    first = run_test(root, test, timeout)
    if first[0] == ERROR:
        return first
    again = run_test(root, test, timeout)
    if again[0] != first[0]:
        return ERROR, first[1] + again[1], "flaky: %s, then %s: %s" % (first[0], again[0], first[2])
    return first[0], first[1] + again[1], first[2]


def is_secret(name):
    """A root file that holds credentials or machine state: never copied (the copy is a scratch dir others may read)."""
    low = name.lower()
    return low == "secrets.env" or low.startswith(".env") or low == ".forge-home" or "secret" in low


def make_copy(parent, repo=REPO):
    root = Path(tempfile.mkdtemp(prefix="mutate-", dir=parent))
    for d in COPY_DIRS:
        shutil.copytree(repo / d, root / d, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", "secrets.env", ".env*", ".forge-home", "*secret*"))
    for f in repo.iterdir():   # root files (FORGE_COMMIT, CLAUDE.md, ...) that a test reads
        if f.is_file() and not is_secret(f.name):
            shutil.copy2(f, root / f.name)
    return root


def tests_for(target):
    """The module's own test file; the convention `eval/tests/test_<stem>.py` (docs/STANDARDS.md section 3)."""
    return Path("eval/tests") / ("test_%s.py" % Path(target).stem)


def other_tests(root, target):
    """Every other test file that mentions the module by name (for `--recheck` of a survivor)."""
    stem = Path(target).stem
    own = tests_for(target).name
    pat = re.compile(r"\b%s\b" % re.escape(stem))
    return [p.relative_to(root) for p in sorted((root / "eval/tests").glob("test_*.py"))
            if p.name != own and pat.search(p.read_text(encoding="utf-8", errors="replace"))]


def mutate_file(root, target, n, seed, timeout, recheck):
    path = root / target
    original = path.read_text(encoding="utf-8")
    test = tests_for(target)
    picked, total = sample(original, n, seed)
    rows = []
    # a test that already fails on the unmutated copy would "kill" every survivor, so only passing ones count
    rechecks = ([t for t in other_tests(root, target) if run_confirmed(root, t, timeout)[0] == SURVIVED]
                if recheck else [])
    try:
        for k in picked:
            src, site = mutant_source(original, k)
            path.write_text(src, encoding="utf-8")
            status, secs, tail = run_confirmed(root, test, timeout)
            row = {"file": target, "site": k, "line": site.line, "kind": site.kind, "desc": site.desc,
                   "status": status, "seconds": round(secs, 1), "tail": tail}
            if status == SURVIVED and recheck:
                row["killed_by_other"] = [str(t) for t in rechecks
                                          if run_confirmed(root, t, timeout)[0] == KILLED]
            rows.append(row)
    finally:
        path.write_text(original, encoding="utf-8")
    return {"file": target, "sites": total, "sampled": len(picked), "rows": rows}


def score(rows):
    killed = sum(r["status"] == KILLED for r in rows)
    survived = sum(r["status"] == SURVIVED for r in rows)
    return killed, survived, sum(r["status"] == ERROR for r in rows)


def wilson(k, n, z=1.96):
    """95% Wilson interval for k of n. Local on purpose: `aggregate.wilson_ci` is one of the files being mutated."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return max(0.0, c - h), min(1.0, c + h)


def weighted(results):
    """Site-weighted kill rate: sum(sites_i * p_i) / sum(sites_i), p_i = killed/(killed + survived) of file i.

    The interval is the stratified normal one, se = sqrt(sum(sites_i^2 * p_i(1-p_i)/n_i)) / sum(sites_i): rough
    where a file has p_i near 0 or 1 (its variance is then underestimated), so read it as a floor on the spread.
    """
    total = sum(r["sites"] for r in results)
    est = var = 0.0
    for r in results:
        k, s, _ = score(r["rows"])
        if k + s:
            p = k / (k + s)
            est += r["sites"] * p
            var += r["sites"] ** 2 * p * (1 - p) / (k + s)
    est /= max(1, total)
    se = var ** 0.5 / max(1, total)
    return est, max(0.0, est - 1.96 * se), min(1.0, est + 1.96 * se)


# ---------------------------------------------------------------- the tool's own check

TOY = "def clamp(x, lo, hi):\n    if x < lo:\n        return lo\n    if x > hi:\n        return hi\n    return x\n"
TOY_TEST = ("import sys, toy\n"
            "ok = toy.clamp(5, 0, 3) == 3 and toy.clamp(-1, 0, 3) == 0 and toy.clamp(2, 0, 3) == 2\n"
            "sys.exit(0 if ok else 1)\n")


def self_check(parent, timeout=30):
    """Planted mutants on a toy module: the equivalent one must survive, the bug must be killed.

    Returns a list of (name, expected, got); the caller refuses to report when any differs.
    """
    root = Path(tempfile.mkdtemp(prefix="mutate-self-", dir=parent))
    try:
        (root / "toy.py").write_text(TOY)
        test = root / "test_toy.py"
        test.write_text(TOY_TEST)
        planted = [("unmutated toy passes", SURVIVED, TOY),
                   ("planted equivalent (x + 0) survives", SURVIVED, TOY.replace("return x\n", "return x + 0\n")),
                   ("planted bug (clamp returns lo for x > hi) is killed", KILLED,
                    TOY.replace("return hi", "return lo")),
                   ("planted hang is an error, not a kill", ERROR,
                    TOY.replace("    return x\n", "    while True:\n        pass\n"))]
        results = []
        for name, want, src in planted:
            (root / "toy.py").write_text(src)
            got = run_test(root, test.name, 3 if want == ERROR else timeout)[0]
            results.append((name, want, got))
        return results
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- report

def markdown(results):
    lines = ["| File | Sites | Sampled | Killed | Survived | Error | Kill rate (95% Wilson) |", "|---|---|---|---|---|---|---|"]
    tk = ts = 0
    for r in results:
        k, s, e = score(r["rows"])
        tk, ts = tk + k, ts + s
        lo, hi = wilson(k, k + s)
        lines.append("| %s | %d | %d | %d | %d | %d | %.0f%% (%.0f to %.0f) |" % (
            r["file"], r["sites"], r["sampled"], k, s, e, 100 * k / max(1, k + s), 100 * lo, 100 * hi))
    lo, hi = wilson(tk, tk + ts)
    lines.append("| all (unweighted: up to --max sampled per file) | | | %d | %d | | %.0f%% (%.0f to %.0f) |" % (
        tk, ts, 100 * tk / max(1, tk + ts), 100 * lo, 100 * hi))
    w, wlo, whi = weighted(results)
    lines.append("| all (weighted by each file's sites) | %d | | | | | %.0f%% (%.0f to %.0f) |" % (
        sum(r["sites"] for r in results), 100 * w, 100 * wlo, 100 * whi))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("targets", nargs="*", help="module paths relative to the repository root")
    ap.add_argument("--max", type=int, default=40, help="mutants per file (default 40)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--timeout", type=int, default=60, help="seconds per mutant run")
    ap.add_argument("--json", help="write every mutant row here")
    ap.add_argument("--repo", type=Path, default=REPO, help="the tree to copy and mutate (default: this repository)")
    ap.add_argument("--recheck", action="store_true", help="rerun each survivor against the other tests that name the module")
    args = ap.parse_args(argv)
    targets = args.targets or [t for t in DEFAULT_TARGETS if (REPO / t).is_file()]
    parent = tempfile.gettempdir()
    bad = [r for r in self_check(parent) if r[1] != r[2]]
    if bad:
        print("self-check FAILED: %s" % bad, file=sys.stderr)
        return 2
    root = make_copy(parent, args.repo.resolve())
    try:
        for t in targets:   # the unmutated copy must pass first, or every mutant would count as killed
            if not (root / tests_for(t)).is_file():
                print("no test file %s for %s" % (tests_for(t), t), file=sys.stderr)
                return 2
            status, _, tail = run_confirmed(root, tests_for(t), args.timeout)
            if status != SURVIVED:
                print("baseline: %s fails unmutated in the copy: %s" % (tests_for(t), tail), file=sys.stderr)
                return 2
        results = []
        for t in targets:
            results.append(mutate_file(root, t, args.max, args.seed, args.timeout, args.recheck))
            k, s, e = score(results[-1]["rows"])
            print("%s: killed %d survived %d error %d" % (t, k, s, e), flush=True)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(markdown(results))
    if args.json:
        Path(args.json).write_text(json.dumps({"seed": args.seed, "max": args.max, "results": results}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
