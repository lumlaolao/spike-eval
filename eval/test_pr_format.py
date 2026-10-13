"""scripts/pr_format.py: the accepted examples are real subjects from this repository's history.
Lives in eval/ so the acceptance suite (`unittest discover -s eval`) runs it."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import pr_format as pf  # noqa: E402

BODY = """## What

- the seat's tools are native function calls

## Why

30 of 43 tool requests were refused (eval/tool_audit.py runs/lines/ctl/control).

## Evidence

- acceptance: `OUT=/tmp/acc-native-1` 12/12 stages PASS, 278 Java, 141 Python

## Provenance

memory_tools_version evidence-tools-v2; native_tools stamped.

## Review

- pr-review @ abc123456: LOW two nits, fixed
- simplify @ abc123456: CLEAN
- standards @ abc123456: CLEAN

## Not done

MCP server; see the note.
"""


class PrFormatTest(unittest.TestCase):
    def test_real_subjects_from_history_pass(self):
        for s in ("feat(obs v12): the seat reads the opponent's cards; untargeted answers are offered",
                  "fix(play): the seat may respond to its own trigger on the stack",
                  "eval: plan_audit.py, a plan-vs-action audit over recorded games",
                  "Merge pull request #79 from SeanCThomas0/obs/v12-opponent-card-text"):
            self.assertEqual(pf.subject_problems(s), [], s)

    def test_bad_subjects_are_named(self):
        self.assertTrue(pf.subject_problems("update SeatObservation"))
        self.assertTrue(pf.subject_problems("feature: new thing"))
        self.assertTrue(pf.subject_problems("fix(play): the seat responds."))
        self.assertTrue(pf.subject_problems("fix: " + "x" * 120))
        self.assertTrue(pf.subject_problems("fix:no space"))

    def test_commit_body_rules(self):
        self.assertEqual(pf.commit_problems("fix(a): b\n\nwhy it was needed\n\nCo-Authored-By: X <x@y>\n"), [])
        self.assertTrue(pf.commit_problems("fix(a): b\nno blank line\n"))
        self.assertTrue(pf.commit_problems("fix(a): b\n\n" + "word " * 30 + "\n"))
        self.assertEqual(pf.commit_problems("fix(a): b\n\nhttps://example.com/" + "x" * 120 + "\n"), [])
        self.assertEqual(pf.commit_problems("# comment\n\nfix(a): b\n"), [])

    def test_a_complete_pr_passes_the_gate_for_its_head(self):
        self.assertEqual(pf.pr_problems("feat(tools): the seat's tools are native function calls", BODY,
                                        "abc123456789", ["eval/x.py"], gate=True), [])

    def test_the_gate_refuses_a_stale_missing_or_blocking_review(self):
        t = "feat(tools): native calls"
        stale = pf.pr_problems(t, BODY, "fff000111222", ["eval/x.py"], gate=True)
        self.assertEqual(len([p for p in stale if "review the current head" in p]), 3)
        missing = pf.pr_problems(t, BODY.replace("- simplify @ abc123456: CLEAN\n", ""), "abc123456789", [], gate=True)
        self.assertTrue(any("no `simplify` verdict" in p for p in missing))
        blocking = pf.pr_problems(t, BODY.replace("LOW two nits, fixed", "HIGH budget is spent twice"), "abc123456789", [], gate=True)
        self.assertTrue(any("verdict is HIGH" in p for p in blocking))
        # Without --gate the same body is only checked for form.
        self.assertEqual(pf.pr_problems(t, BODY, "fff000111222", [], gate=False), [])

    def test_code_changes_need_an_acceptance_run_in_evidence(self):
        body = BODY.replace("- acceptance: `OUT=/tmp/acc-native-1` 12/12 stages PASS, 278 Java, 141 Python", "- read it twice")
        self.assertTrue(any("acceptance" in p for p in pf.pr_problems("docs: x", body, "abc123456789", ["forge-overlay/a.java"], gate=True)))
        self.assertEqual(pf.pr_problems("docs: x", body, "abc123456789", ["docs/a.md"], gate=True), [])

    def test_missing_empty_and_misordered_sections(self):
        self.assertTrue(any("no `## Why`" in p for p in pf.pr_problems("docs: x", BODY.replace("## Why", "## Reason"))))
        self.assertTrue(any("`## Not done` is empty" in p for p in pf.pr_problems("docs: x", BODY.replace("MCP server; see the note.", "TODO"))))
        swapped = BODY.replace("## What", "## TMP").replace("## Why", "## What").replace("## TMP", "## Why")
        self.assertTrue(any("out of order" in p for p in pf.pr_problems("docs: x", swapped)))

    def test_latest_verdict_per_agent_is_read(self):
        self.assertEqual(pf.reviews(BODY)["pr-review"], ("abc123456", "LOW", "two nits, fixed"))


if __name__ == "__main__":
    unittest.main()
