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

- acceptance @ abc123456: OUT=/tmp/acc-native-1 12/12 stages PASS, 278 Java, 141 Python

## Provenance

memory_tools_version evidence-tools-v2; native_tools stamped.

## Review

- pr-review @ abc123456: LOW two nits, fixed
- simplify @ abc123456: SUGGESTIONS applied 8 of 10
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
        missing = pf.pr_problems(t, BODY.replace("- simplify @ abc123456: SUGGESTIONS applied 8 of 10\n", ""), "abc123456789", [], gate=True)
        self.assertTrue(any("no `simplify` verdict" in p for p in missing))
        blocking = pf.pr_problems(t, BODY.replace("LOW two nits, fixed", "HIGH budget is spent twice"), "abc123456789", [], gate=True)
        self.assertTrue(any("verdict is HIGH" in p for p in blocking))
        # Without --gate the same body is only checked for form.
        self.assertEqual(pf.pr_problems(t, BODY, "fff000111222", [], gate=False), [])

    def test_code_changes_need_an_acceptance_run_in_evidence(self):
        line = "- acceptance @ abc123456: OUT=/tmp/acc-native-1 12/12 stages PASS, 278 Java, 141 Python"

        def gate(evidence, files, head="abc123456789"):
            return pf.pr_problems("docs: x", BODY.replace(line, evidence), head, files, gate=True)
        # Saying the word is not evidence; neither is a partial pass, nor a pass on another commit.
        self.assertTrue(any("has no line" in p for p in gate("- no acceptance run was made", ["forge-overlay/a.java"])))
        self.assertTrue(any("full pass" in p for p in gate(line.replace("12/12", "11/12"), ["eval/x.py"])))
        self.assertTrue(any("full pass" in p for p in gate(line.replace("abc123456", "fff000111"), ["scripts/pr_merge.sh"])))
        self.assertEqual(gate(line, ["install.sh"]), [])
        self.assertEqual(gate("- read it twice", ["docs/a.md"]), [])

    def test_the_gate_checks_the_commits_on_the_pr_and_each_skill_vocabulary(self):
        good = [("1a976281aa", "feat(tools): native calls\n\nwhy")]
        bad = good + [("deadbeef00", "update stuff")]
        t = "feat(tools): native calls"
        self.assertEqual(pf.pr_problems(t, BODY, "abc123456789", [], gate=True, commits=good), [])
        self.assertTrue(any(p.startswith("commit deadbeef0") for p in
                            pf.pr_problems(t, BODY, "abc123456789", [], gate=True, commits=bad)))
        # A standards VIOLATIONS verdict blocks; a pr-review verdict in simplify's words is refused.
        v = pf.pr_problems(t, BODY.replace("standards @ abc123456: CLEAN", "standards @ abc123456: VIOLATIONS two rules"), "abc123456789", [], gate=True)
        self.assertTrue(any("verdict is VIOLATIONS" in p for p in v))
        w = pf.pr_problems(t, BODY.replace("pr-review @ abc123456: LOW", "pr-review @ abc123456: SUGGESTIONS"), "abc123456789", [], gate=True)
        self.assertTrue(any("is not one of" in p for p in w))

    def test_missing_empty_and_misordered_sections(self):
        self.assertTrue(any("no `## Why`" in p for p in pf.pr_problems("docs: x", BODY.replace("## Why", "## Reason"))))
        self.assertTrue(any("`## Not done` is empty" in p for p in pf.pr_problems("docs: x", BODY.replace("MCP server; see the note.", "TODO"))))
        swapped = BODY.replace("## What", "## TMP").replace("## Why", "## What").replace("## TMP", "## Why")
        self.assertTrue(any("out of order" in p for p in pf.pr_problems("docs: x", swapped)))

    def test_an_unfilled_skeleton_is_refused_and_names_each_placeholder(self):
        # The skeleton new-pr prints used to pass the form check: every section held some text.
        title, body = pf.skeleton("HEAD")
        problems = pf.pr_problems("docs: x", body)
        for section in ("Why", "Evidence", "Provenance", "Not done"):
            self.assertTrue(any("`## %s` still holds a skeleton placeholder" % section in p for p in problems),
                            (section, problems))
        # Only the acceptance line left unfilled on a docs-only PR (the gate's acceptance check is skipped).
        evidence_only = BODY.replace("- acceptance @ abc123456: OUT=/tmp/acc-native-1 12/12 stages PASS",
                                     "- acceptance @ abc123456: OUT=/tmp/<name> <N>/<N> stages PASS")
        self.assertTrue(any("`## Evidence` still holds" in p for p in pf.pr_problems("docs: x", evidence_only)))
        # Prose, HTML and autolinks with angle brackets are not placeholders.
        for line in ("The <details> block stays; `List<String>` is fine.", "<details>", "</details>",
                     "<!-- reviewer note -->", "- <https://github.com/x/y/actions/runs/1>", "<br>"):
            self.assertEqual(pf.pr_problems("docs: x", BODY.replace("MCP server; see the note.", line)), [], line)

    def test_the_skeleton_offers_each_skill_its_own_verdicts(self):
        review = pf.sections(pf.skeleton("HEAD")[1])["Review"]
        for skill in pf.SKILLS:
            self.assertIn("- %s @ " % skill, review)
            self.assertIn("<%s>" % "|".join(pf.SKILL_VERDICTS[skill]), review)

    def test_recording_replaces_the_line_by_heading_and_survives_odd_notes(self):
        body = BODY.replace("## Why\n", "## Why\n\nThe `## Review` section was empty before.\n")
        new = pf.updated_body(body, "Review", "- pr-review @", "- pr-review @ abc123456: MEDIUM reach 87% vs 40%")
        self.assertIn("The `## Review` section was empty before.", new)        # prose mentioning the heading survives
        self.assertEqual(pf.reviews(new)["pr-review"], ("abc123456", "MEDIUM", "reach 87% vs 40%"))
        self.assertEqual(pf.reviews(new)["standards"][1], "CLEAN")              # the other lines are kept
        self.assertEqual(list(pf.sections(new)), list(pf.sections(body)))
        pending = BODY.replace("- pr-review @ abc123456: LOW two nits, fixed\n", "(pending: being reviewed)\n")
        self.assertNotIn("(pending", pf.updated_body(pending, "Review", "- pr-review @", "- pr-review @ abc123456: CLEAN"))
        self.assertIsNone(pf.updated_body("## What\n\nx\n", "Review", "- x", "- y"))

    def test_latest_verdict_per_agent_is_read(self):
        self.assertEqual(pf.reviews(BODY)["pr-review"], ("abc123456", "LOW", "two nits, fixed"))


if __name__ == "__main__":
    unittest.main()
