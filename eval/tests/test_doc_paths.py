"""Every repository path named in the files agents read first exists (docs/STANDARDS.md section 7): in a
backtick span, alone or as a word of a command, and in a fenced code block. A dead path there sends every
agent that reads it nowhere, and the command lines are the ones copied; layout moves left several."""
import os
import re
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)

REPO = evalpath.REPO
DOCS = (["CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md", "README.md", "docs/STANDARDS.md"]
        + [str(p.relative_to(REPO)) for g in (".claude/rules/*.md", ".claude/skills/*/SKILL.md")
           for p in sorted(REPO.glob(g))])
# Paths under the repository's own folders; root files (setup.sh, ...) are not checked, since a bare
# launcher name can mean the copy installed in $FORGE_HOME. Placeholders (<name>, *, ?, {a,b}, $VAR, ...) are skipped.
TOP = ("eval/", "docs/", "scripts/", "forge-overlay/", "engine-patches/", ".claude/", "data/", "datasets/")
SPAN = re.compile(r"`([^`]+)`")
PLACEHOLDER = re.compile(r"[<>*?{}$]|\.\.")  # ... loses its last dot to the sentence strip


def tracked():
    """What a fresh clone has: tracked files and their folders (a local, ignored file does not count)."""
    try:
        out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    have = set()
    for f in out.splitlines():
        parts = f.split("/")
        have.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    return have


def words(line, fenced):
    for chunk in ([line] if fenced else SPAN.findall(line)):
        for w in re.split(r"[\s,;|]+", chunk):
            w = w.strip("\"'()[]").split("=", 1)[-1].split("::")[0].split("#")[0]
            # a trailing line or range (`:12`, `:12-40`) and sentence punctuation, then brackets once more
            yield re.sub(r"(:\d+(-\d+)?)?[.:]?$", "", w).strip("\"'()[]")


def named_paths():
    for doc in DOCS:
        fenced = False
        for n, line in enumerate((REPO / doc).read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("```"):
                fenced = not fenced
                continue
            for p in words(line, fenced):
                if p.startswith(TOP) and not PLACEHOLDER.search(p):
                    yield doc, n, p.rstrip("/")


def paths_in(text, fenced=False):
    return [w for w in words(text, fenced) if w.startswith(TOP) and not PLACEHOLDER.search(w)]


class DocPathsTest(unittest.TestCase):

    def test_how_a_path_is_read_out_of_a_span(self):
        cases = {
            "`eval/run_batch.py:12-40`": ["eval/run_batch.py"],
            "`eval/tests/test_rating.py::RatingTest::test_x`": ["eval/tests/test_rating.py"],
            "`cat eval/UNTESTED.txt|wc -l`": ["eval/UNTESTED.txt"],
            "`eval/a.py,eval/b.py;scripts/c.sh`": ["eval/a.py", "eval/b.py", "scripts/c.sh"],
            "`(eval/run_batch.py).`": ["eval/run_batch.py"],
            "`FOO=1 python3 eval/x.py --out=docs/y.md`": ["eval/x.py", "docs/y.md"],
            "`.claude/skills/{a,b}/SKILL.md` `eval/test_?.py` `eval/<name>.py` `eval/...`": [],
            "plain eval/not_in_a_span.py": [],
        }
        for text, want in cases.items():
            self.assertEqual(paths_in(text), want, text)
        self.assertEqual(paths_in("bash scripts/x.sh eval/y.py", fenced=True), ["scripts/x.sh", "eval/y.py"])

    def test_every_named_path_exists(self):
        found = list(named_paths())
        self.assertGreater(len(found), 150, "the scan found almost nothing: is the pattern still right?")
        have = tracked()
        exists = (lambda p: p in have) if have is not None else (lambda p: (REPO / p).exists())
        dead = ["%s:%d %s" % (d, n, p) for d, n, p in found if not exists(p)]
        self.assertEqual(dead, [])


if __name__ == "__main__":
    unittest.main()
