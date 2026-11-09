#!/usr/bin/env python3
"""harness_env_tasks.py: turns merged "red test, then fix" PRs into small, verifiable harness-env tasks.

A task is built from one merged PR (SWE-style, from this repository's own history):
  - start state: the repository at the merge's first parent (`base_sha`) plus the PR's test files;
  - prompt: the text of the issue the PR closes (the gold patch, the PR body and the fix commits stay out of it);
  - reward: the target tests (the test methods the PR added or changed) pass, and the other tests of those
    classes still pass (harness_env_task_grade.py scores it);
  - gold patch: the non-test, non-doc files the merge changed, kept in the manifest for validation and for
    sizing, never shown to the agent.

A PR qualifies only if it is red-then-green on its face: it has a merge commit with two parents, it adds or changes
at least one test method, it changes at least one source file (not only docs or tests), and a closing issue gives it
a prompt. Everything else is listed under `skipped` with the reason. Whether the base state really fails the targets
is not known from history; `harness_env_task_grade.py validate` proves it (red on base, green with the gold patch).

Usage:
  harness_env_tasks.py build --prs 403,407,409 [--repo .] [--rev HEAD] [--out tasks.json] [--no-github]
  harness_env_tasks.py recent --limit 40 [--repo .] [--rev HEAD] [--out tasks.json]   # the last N first-parent merges
`--rev` names the commit whose first-parent history is read (git objects only, no checkout of it).
Each task carries `red_kind` and `validated` from eval/harness_env/validation/<id>-validation.json (null until
`harness_env_task_grade.py validate` has been run and its JSON committed there).
Reads git history and GitHub REST (`gh api`, GH_REPO or --github-repo); stdlib only; makes no model call.
"""
import argparse
import ast
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

SCHEMA = "harness-env-tasks/2"
VALIDATION_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "validation")
DEFAULT_GH_REPO = "lumlaolao/spike-eval"

JAVA_TEST_DIR = re.compile(r"(?:^|/)src/test/java/(.+)\.java$")
PY_TEST = re.compile(r"^eval/tests/(test_[^/]+)\.py$")
DOC_PATH = re.compile(r"^(.*\.md|LICENSE)$")  # prose only: a data file under docs/ (a .tsv) is source
CLOSES = re.compile(r"\b(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?)\s+#(\d+)", re.I)
WHY_REF = re.compile(r"(?<![\w/])#(\d+)")
JAVA_TEST_METHOD = re.compile(r"@Test\b[^\n]*\n(?:\s*@\w+[^\n]*\n)*\s*(?:public\s+|protected\s+|private\s+)?void\s+(\w+)\s*\(")


class TaskError(RuntimeError):
    pass


# ---------------------------------------------------------------- git

def git(repo, *args):
    p = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True)
    if p.returncode != 0:
        raise TaskError("git %s: %s" % (" ".join(args), p.stderr.strip()))
    return p.stdout


def read_blob(repo, rev, path):
    """The bytes of `path` at `rev` in `repo`, or None if it does not exist there."""
    p = subprocess.run(["git", "-C", repo, "show", "%s:%s" % (rev, path)], capture_output=True)
    return p.stdout if p.returncode == 0 else None


def git_show(repo, rev, path):
    blob = read_blob(repo, rev, path)
    return None if blob is None else blob.decode("utf-8", "replace")


def merge_commit(repo, pr, rev="HEAD"):
    out = git(repo, "log", rev, "--first-parent", "--grep=^Merge pull request #%d from" % pr, "-1",
              "--format=%H").strip()
    return out or None


def changed_files(repo, base, merge):
    """[(status, path)] of the merge against its first parent (a rename is a delete and an add)."""
    out = git(repo, "diff", "--name-status", "--no-renames", base, merge)
    rows = []
    for line in out.splitlines():
        status, _, path = line.partition("\t")
        rows.append((status[0], path))
    return rows


def numstat(repo, base, merge, paths):
    if not paths:
        return 0
    out = git(repo, "diff", "--numstat", "--no-renames", base, merge, "--", *paths)
    total = 0
    for line in out.splitlines():
        a, d, _ = line.split("\t", 2)
        total += (int(a) if a.isdigit() else 0) + (int(d) if d.isdigit() else 0)
    return total


# ---------------------------------------------------------------- test methods

def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _body_end(text, start):
    """Index just past the brace block that opens at or after `start`, skipping strings, chars and comments."""
    i, depth, n = text.find("{", start), 0, len(text)
    if i < 0:
        return n
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            i = text.find("\n", i)
            i = n if i < 0 else i
        elif text.startswith("/*", i):
            i = text.find("*/", i)
            i = n if i < 0 else i + 1
        elif text.startswith('"""', i):
            i = text.find('"""', i + 3)
            i = n if i < 0 else i + 2
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            i = j
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def java_tests(text):
    """{method: digest of its text, annotations to closing brace} for every @Test method of a Java source."""
    out = {}
    for m in JAVA_TEST_METHOD.finditer(text):
        out[m.group(1)] = _digest(text[m.start():_body_end(text, m.end())])
    return out


def python_tests(text):
    """{name: digest}: `Class.test_x` for a TestCase method, `test_x` for a module-level function."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    out = {}

    def seg(node):
        return ast.get_source_segment(text, node) or ""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            out[node.name] = _digest(seg(node))
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name.startswith("test"):
                    out["%s.%s" % (node.name, sub.name)] = _digest(seg(sub))
    return out


def python_style(text):
    """'unittest' if the file has a TestCase class, else 'script' (module-level test_ functions run by main())."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return "script"
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and any("TestCase" in ast.unparse(b) for b in node.bases):
            return "unittest"
    return "script"


def classify(path):
    """('java_test'|'python_test'|'doc'|'source', detail): detail is the class or module of a test file, else None."""
    m = JAVA_TEST_DIR.search(path)
    if m:
        return "java_test", m.group(1).replace("/", ".")
    m = PY_TEST.match(path)
    if m:
        return "python_test", m.group(1)
    if DOC_PATH.match(path):
        return "doc", None
    return "source", None


def changed_tests(repo, base, merge, path, kind):
    """The tests of one test file the PR added or changed: ({name: 'added'|'changed'}, all names at the merge)."""
    after = git_show(repo, merge, path)
    if after is None:
        return {}, []
    before = git_show(repo, base, path) or ""
    parse = java_tests if kind == "java_test" else python_tests
    a, b = parse(after), parse(before)
    targets = {}
    for name, dig in a.items():
        if name not in b:
            targets[name] = "added"
        elif b[name] != dig:
            targets[name] = "changed"
    return targets, sorted(a)


# ---------------------------------------------------------------- GitHub

def gh_api(path, repo_slug):
    p = subprocess.run(["gh", "api", "repos/%s/%s" % (repo_slug, path)], capture_output=True, text=True)
    if p.returncode != 0:
        raise TaskError("gh api %s: %s" % (path, p.stderr.strip()[:200]))
    return json.loads(p.stdout)


def linked_issues(pr_body, commit_messages):
    """[(number, 'closing'|'referenced')]: issues the PR closes by keyword, then the first one its Why names."""
    found = []
    for text in [pr_body or ""] + list(commit_messages):
        for m in CLOSES.finditer(text):
            if int(m.group(1)) not in [n for n, _ in found]:
                found.append((int(m.group(1)), "closing"))
    why = re.search(r"(?ms)^## Why\s*$(.*?)(?=^## |\Z)", pr_body or "")
    for m in WHY_REF.finditer(why.group(1) if why else ""):
        if int(m.group(1)) not in [n for n, _ in found]:
            found.append((int(m.group(1)), "referenced"))
    return found


# ---------------------------------------------------------------- one task

def build_task(repo, pr, fetch=None, rev="HEAD"):
    """-> (task dict, None) or (None, reason). `fetch(path)` returns REST JSON (None: no GitHub, history only)."""
    merge = merge_commit(repo, pr, rev)
    if not merge:
        return None, "no merge commit for #%d on the first-parent history" % pr
    parents = git(repo, "rev-list", "--parents", "-n", "1", merge).split()[1:]
    if len(parents) != 2:
        return None, "merge %s has %d parents (not a merge of a branch)" % (merge[:9], len(parents))
    base, head = parents
    fix_commits = git(repo, "rev-list", "--no-merges", "--reverse", "%s..%s" % (base, head)).split()
    if not fix_commits:
        return None, "the branch holds no commits of its own"
    files = changed_files(repo, base, merge)
    test_files, support, gold, docs = [], [], [], []
    java_classes, py_files, targets = [], [], []
    for status, path in files:
        kind, detail = classify(path)
        if kind in ("java_test", "python_test"):
            if status == "D":
                continue
            found, every = changed_tests(repo, base, merge, path, kind)
            test_files.append(path)
            if not every:
                support.append(path)  # a test-dir helper holding no test (AskContract)
                continue
            if kind == "java_test":
                java_classes.append(detail)
                for name, how in sorted(found.items()):
                    targets.append({"kind": "java", "class": detail, "method": name, "change": how, "file": path})
            else:
                style = python_style(git_show(repo, merge, path) or "")
                py_files.append({"file": path, "module": detail, "style": style})
                for name, how in sorted(found.items()):
                    targets.append({"kind": "python", "file": path, "module": detail, "method": name,
                                    "change": how, "style": style})
        elif kind == "doc":
            docs.append(path)
        else:
            gold.append(path)
    if not targets:
        return None, "no test method added or changed (no red test)"
    if not gold:
        return None, "no source file changed besides tests and docs (nothing to fix)"
    msgs = [git(repo, "log", "-1", "--format=%B", c) for c in fix_commits]
    pr_json = fetch("pulls/%d" % pr) if fetch else None
    body = (pr_json or {}).get("body") or ""
    title = (pr_json or {}).get("title") or git(repo, "log", "-1", "--format=%s", head).strip()
    issues = linked_issues(body, msgs)
    issue_no, prompt, link = None, None, None
    for n, how in issues:
        iss = fetch("issues/%d" % n) if fetch else None
        if iss and "pull_request" not in iss and (iss.get("body") or iss.get("title")):
            issue_no, link = n, how
            prompt = "# %s\n\n%s" % (iss.get("title", "").strip(), (iss.get("body") or "").strip())
            break
    if prompt is None:
        return None, ("no closing issue with text (links: %s)" % ([n for n, _ in issues] or "none")) if fetch else "GitHub not read"
    merged_at = git(repo, "log", "-1", "--format=%cI", merge).strip()
    gold_lines = numstat(repo, base, merge, gold)
    task = {
        "id": "pr%d" % pr,
        "pr": pr, "pr_title": title, "issue": issue_no, "issue_link": link,
        "prompt_quality": "ok" if link == "closing" else "weak",
        "prompt_note": "" if link == "closing" else (
            "the issue is only referenced in the PR's Why, and may be a multi-item tracking issue "
            "(the prompt is its whole text, not the one item this PR fixes)"),
        "base_sha": base, "merge_sha": merge, "head_sha": head, "fix_commits": fix_commits,
        "merged_at": merged_at,
        "prompt": prompt,
        "test_files": test_files, "support_files": support,
        "java_test_classes": sorted(set(java_classes)),
        "python_tests": py_files,
        "targets": targets,
        "gold_files": gold, "doc_files": docs,
        "gold_lines": gold_lines, "gold_file_count": len(gold),
        "needs_jvm": bool(java_classes),
        "grade_command": "python3 eval/harness_env/harness_env_task_grade.py grade <task.json> <worktree> --task-id pr%d" % pr,
        "gold_hidden": True,
        "view_id": opaque_id(base, merge),
        "red_kind": None, "validated": None,
    }
    return task, None


def build(repo, prs, fetch=None, rev="HEAD", validation_dir=VALIDATION_DIR):
    tasks, skipped = [], []
    for pr in prs:
        try:
            task, why = build_task(repo, pr, fetch, rev)
        except TaskError as e:
            task, why = None, "error: %s" % e
        if task:
            attach_validation(task, validation_dir)
            tasks.append(task)
        else:
            skipped.append({"pr": pr, "reason": why})
    return {"schema": SCHEMA, "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "repo_head": git(repo, "rev-parse", rev).strip(), "tasks": tasks, "skipped": skipped}


def recent_prs(repo, limit, rev="HEAD"):
    """The PR numbers of the last `limit` first-parent merges reachable from `rev` (a non-PR merge is dropped)."""
    out = git(repo, "log", rev, "--first-parent", "--merges", "-n", str(limit), "--format=%s")
    nums = (re.match(r"Merge pull request #(\d+) from", line) for line in out.splitlines())
    return [int(m.group(1)) for m in nums if m]


def opaque_id(base, merge):
    """A short id that names the task and nothing else: the agent never sees the PR number or either sha."""
    return "t" + hashlib.sha256(("%s:%s" % (base, merge)).encode()).hexdigest()[:10]


def attach_validation(task, validation_dir):
    """Fill `red_kind` and `validated` from the committed validate output of this task, if there is one."""
    path = os.path.join(validation_dir, task["id"] + "-validation.json")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        v = json.load(f)
    task["red_kind"] = v.get("red_kind")
    task["validated"] = {"well_posed": bool(v.get("well_posed"))}


def agent_view(task):
    """What the agent sees: an opaque id, the prompt and the grading rule. Never the PR number, a sha, the gold
    patch or the fix commits, and no base sha either (the sandbox is the base tree with no history to look it up in)."""
    view = {"id": task["view_id"], "prompt": task["prompt"], "test_files": task["test_files"],
            "grade_command": task["grade_command"].replace(task["id"], task["view_id"])}
    view["targets"] = [{k: t[k] for k in t if k != "change"} for t in task["targets"]]
    return view


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("build", "recent"):
        p = sub.add_parser(name)
        p.add_argument("--repo", default=os.getcwd())
        p.add_argument("--rev", default="HEAD")
        p.add_argument("--out", default="-")
        p.add_argument("--github-repo", default=os.environ.get("GH_REPO", DEFAULT_GH_REPO))
        p.add_argument("--no-github", action="store_true", help="history only: a PR without an issue text is skipped")
        if name == "build":
            p.add_argument("--prs", required=True, help="comma-separated PR numbers")
        else:
            p.add_argument("--limit", type=int, default=40)
    a = ap.parse_args(argv)
    prs = [int(x) for x in a.prs.split(",") if x.strip()] if a.cmd == "build" else recent_prs(a.repo, a.limit, a.rev)
    fetch = None if a.no_github else (lambda path: gh_api(path, a.github_repo))
    doc = build(a.repo, prs, fetch, a.rev)
    text = json.dumps(doc, indent=1, sort_keys=True) + "\n"
    if a.out == "-":
        sys.stdout.write(text)
    else:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
    print("%d tasks, %d skipped" % (len(doc["tasks"]), len(doc["skipped"])), file=sys.stderr)
    for s in doc["skipped"]:
        print("  skipped #%d: %s" % (s["pr"], s["reason"]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
