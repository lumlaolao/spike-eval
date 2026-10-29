"""Every eval script has eval/tests/test_<name>.py or is on eval/UNTESTED.txt, and the list only names
scripts that still exist and still lack a test (docs/STANDARDS.md section 3)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402  (every eval script folder on sys.path)

TESTS = evalpath.EVAL / "tests"
UNTESTED = {ln.strip() for ln in (evalpath.EVAL / "UNTESTED.txt").read_text().splitlines()
            if ln.strip() and not ln.startswith("#")}


def scripts():
    return sorted(p for p in evalpath.EVAL.rglob("*.py")
                  if "__pycache__" not in p.parts and TESTS not in p.parents)


class UntestedListTest(unittest.TestCase):

    def test_every_script_has_a_test_or_is_listed(self):
        missing = [str(p.relative_to(evalpath.EVAL)) for p in scripts()
                   if not (TESTS / ("test_" + p.name)).is_file() and p.name not in UNTESTED]
        self.assertEqual(missing, [], "add eval/tests/test_<name>.py; the list never grows")

    def test_the_list_names_only_scripts_that_exist_and_lack_a_test(self):
        names = {p.name for p in scripts()}
        self.assertEqual(sorted(UNTESTED - names), [], "listed but gone")
        self.assertEqual(sorted(n for n in UNTESTED if (TESTS / ("test_" + n)).is_file()), [],
                         "has a test now: take it off the list")

    def test_script_names_are_unique_so_a_test_name_means_one_script(self):
        seen = {}
        for p in scripts():
            seen.setdefault(p.name, []).append(str(p.relative_to(evalpath.EVAL)))
        self.assertEqual({n: ps for n, ps in seen.items() if len(ps) > 1}, {})


if __name__ == "__main__":
    unittest.main()
