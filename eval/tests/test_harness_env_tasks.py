"""harness_env_tasks.py turns merged PRs into tasks and says why it skips the rest, on a throwaway history."""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import harness_env_tasks as ht

JAVA_BEFORE = '''class T {
    @Test
    public void old() { assertTrue(true); }
}
'''
JAVA_AFTER = '''class T {
    @Test
    public void old() { assertTrue(true); }

    @Test(groups = "x")
    @Override
    public void fresh() { assertTrue(fixed()); }
}
'''
PY_TEST = '''import unittest
import src


class CaseTest(unittest.TestCase):
    def test_value(self):
        self.assertEqual(src.value(), 2)
'''
JAVA_PATH = "forge-overlay/forge-gui-desktop/src/test/java/forge/spike/T.java"
MAIN_PATH = "forge-overlay/forge-gui-desktop/src/main/java/forge/spike/M.java"


def vcs(repo, *args):
    subprocess.run(["git", "-C", repo] + list(args), check=True, capture_output=True)


def write(repo, path, text):
    full = os.path.join(repo, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(text)


def merge_pr(repo, n, files):
    """Merge a branch of one commit that writes `files` ({path: text}) with a GitHub-style merge message."""
    vcs(repo, "checkout", "-q", "-b", "b%d" % n)
    for path, text in files.items():
        write(repo, path, text)
    vcs(repo, "add", "-A")
    vcs(repo, "commit", "-q", "-m", "change %d\n\nFixes #%d" % (n, n + 100))
    vcs(repo, "checkout", "-q", "master")
    vcs(repo, "merge", "-q", "--no-ff", "-m", "Merge pull request #%d from x/b%d" % (n, n), "b%d" % n)


def fetch(path):
    n = int(path.split("/")[1])
    if path.startswith("pulls/"):
        return {"title": "pr %d" % n, "body": "Fixes #%d" % (n + 100)}
    return {"title": "issue %d" % n, "body": "the seat does the wrong thing %d" % n}


def make_history(repo):
    vcs(repo, "init", "-q", "-b", "master")
    vcs(repo, "config", "user.email", "a@b")
    vcs(repo, "config", "user.name", "a")
    write(repo, "src.py", "def value():\n    return 1\n")
    write(repo, JAVA_PATH, JAVA_BEFORE)
    write(repo, "README.md", "x\n")
    vcs(repo, "add", "-A")
    vcs(repo, "commit", "-q", "-m", "base")
    merge_pr(repo, 1, {"src.py": "def value():\n    return 2\n", "eval/tests/test_src.py": PY_TEST,
                       "docs/note.md": "n\n"})
    merge_pr(repo, 2, {"docs/only.md": "docs\n"})
    merge_pr(repo, 3, {"src.py": "def value():\n    return 3\n"})
    merge_pr(repo, 4, {JAVA_PATH: JAVA_AFTER, MAIN_PATH: "class M {}\n"})
    merge_pr(repo, 5, {"README.md": "y\n", "eval/tests/test_src.py": PY_TEST.replace("2)", "2)  # c")})


class TasksTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo = cls.tmp.name
        make_history(cls.repo)
        cls.doc = ht.build(cls.repo, [1, 2, 3, 4, 5, 6], fetch)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def task(self, n):
        return next(t for t in self.doc["tasks"] if t["pr"] == n)

    def test_a_red_then_green_pr_becomes_a_task_with_its_parts(self):
        t = self.task(1)
        self.assertEqual(t["issue"], 101)
        self.assertEqual(t["issue_link"], "closing")
        self.assertEqual(t["gold_files"], ["src.py"])
        self.assertEqual(t["doc_files"], ["docs/note.md"])
        self.assertEqual(t["test_files"], ["eval/tests/test_src.py"])
        self.assertEqual([(x["module"], x["method"], x["change"]) for x in t["targets"]],
                         [("test_src", "CaseTest.test_value", "added")])
        self.assertEqual(len(t["fix_commits"]), 1)
        self.assertEqual(t["gold_lines"], 2)
        self.assertFalse(t["needs_jvm"])
        self.assertEqual(t["base_sha"], ht.git(self.repo, "rev-parse", t["merge_sha"] + "^1").strip())
        self.assertIn("wrong thing 101", t["prompt"])

    def test_a_java_task_names_only_the_methods_it_added(self):
        t = self.task(4)
        self.assertEqual([(x["class"], x["method"]) for x in t["targets"]], [("forge.spike.T", "fresh")])
        self.assertEqual(t["java_test_classes"], ["forge.spike.T"])
        self.assertTrue(t["needs_jvm"])

    def test_every_other_pr_is_skipped_with_its_reason(self):
        why = {s["pr"]: s["reason"] for s in self.doc["skipped"]}
        self.assertIn("no test method", why[2])
        self.assertIn("no test method", why[3])
        self.assertIn("no merge commit", why[6])
        self.assertEqual(sorted(why), [2, 3, 5, 6])
        self.assertTrue(why[5])  # a comment-only change to a test is a changed test, but it fixes no source

    def test_a_pr_without_an_issue_text_is_skipped(self):
        doc = ht.build(self.repo, [1], lambda p: {"title": "t", "body": "no link"} if p.startswith("pulls") else None)
        self.assertEqual(doc["tasks"], [])
        self.assertIn("no closing issue", doc["skipped"][0]["reason"])

    def test_the_agent_view_leaves_out_the_gold_patch_and_the_history(self):
        t = self.task(1)
        text = repr(ht.agent_view(t))
        for secret in ("'src.py'", "gold", "merge_sha", "fix_commits", t["merge_sha"], t["head_sha"]):
            self.assertNotIn(secret, text)

    def test_issue_links_prefer_closing_keywords_then_the_why_section(self):
        body = "## What\n\nstuff\n\n## Why\n\n#269, item 1: bad\nsee org/repo#5\n\n## Evidence\n#999\n"
        self.assertEqual(ht.linked_issues(body, ["Closes #7"]), [(7, "closing"), (269, "referenced")])

    def test_test_method_parsers(self):
        self.assertEqual(sorted(ht.java_tests(JAVA_AFTER)), ["fresh", "old"])
        self.assertEqual(sorted(ht.python_tests("def test_a():\n    pass\nclass A:\n    def test_b(self):\n        pass\n")),
                         ["A.test_b", "test_a"])
        braces = 'class A {\n @Test\n void a() { s("}"); /* } */ }\n @Test\n void b() { }\n}\n'
        self.assertEqual(sorted(ht.java_tests(braces)), ["a", "b"])
        self.assertNotEqual(ht.java_tests(braces)["a"], ht.java_tests(braces.replace('"}"', '"x"'))["a"])
        self.assertEqual(ht.python_style(PY_TEST), "unittest")
        self.assertEqual(ht.python_style("def test_a():\n    pass\n"), "script")


if __name__ == "__main__":
    unittest.main()
