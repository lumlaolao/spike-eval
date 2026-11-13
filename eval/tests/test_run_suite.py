"""run_suite: a suite's student and teacher specs are checked before the plan is printed."""
import contextlib
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import run_suite  # noqa: E402

SUITE = evalpath.EVAL / "suites" / "stage3-v1.json"


class RetiredLaneTest(unittest.TestCase):
    """The bare Claude CLI text lane is retired (owner, 2026-10-25): a bare name as the student or the teacher is an
    error that says what to use instead."""

    def test_a_bare_teacher_or_student_is_an_error(self):
        for argv in (["--teacher", "opus"], ["--student", "haiku"]):
            errors = run_suite.spec_errors(run_suite.build_parser().parse_args([str(SUITE)] + argv))
            self.assertTrue(any("retired" in e and "claude-cli:<model>" in e for e in errors), errors)

    def test_the_default_student_and_a_claude_cli_teacher_are_accepted(self):
        args = run_suite.build_parser().parse_args([str(SUITE), "--teacher", "claude-cli:claude-opus-5-5"])
        self.assertEqual(run_suite.spec_errors(args), [])


class BudgetTest(unittest.TestCase):
    """run_batch.py refuses a paid model with no --budget-usd: the commands a suite prints carry one."""

    def plan(self, *argv):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["run_suite.py", str(SUITE), "--json", *argv]), \
                contextlib.redirect_stdout(out):
            run_suite.main()
        return json.loads(out.getvalue())

    def test_every_run_command_carries_the_budget_or_a_placeholder_and_a_warning(self):
        for argv, want in (((), "<USD>"), (("--budget-usd", "12.5"), "12.5")):
            rep = self.plan(*argv)
            runs = [e["run"] for e in rep["plan"] if e.get("run")]
            self.assertTrue(runs)
            for cmd in runs:
                self.assertEqual(cmd[cmd.index("--budget-usd") + 1], want, cmd)
            self.assertEqual(any(w.startswith("no --budget-usd") for w in rep["warnings"]), want == "<USD>")

    def test_a_student_with_no_price_on_file_is_named(self):
        rep = self.plan()                                       # the default student, openrouter:default_model
        self.assertTrue(any("has no price" in w and "default_model" in w for w in rep["warnings"]), rep["warnings"])
        rep = self.plan("--student", "openrouter:openai/gpt-6-luna")
        self.assertFalse(any("has no price" in w for w in rep["warnings"]), rep["warnings"])


if __name__ == "__main__":
    unittest.main()
