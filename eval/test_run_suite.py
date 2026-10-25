"""run_suite: a suite's student and teacher specs are checked before the plan is printed."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_suite  # noqa: E402

SUITE = Path(__file__).resolve().parent / "suites" / "stage3-v1.json"


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


if __name__ == "__main__":
    unittest.main()
