"""scripts/pr_format.py: the accepted examples are real subjects from this repository's history.
Lives in eval/ so the acceptance suite (`unittest discover -s eval`) runs it."""
import json
import os
import re
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
        self.assertEqual(pf.commit_problems("# comment\n\nfix(a): b\n\nwhy it was needed\n"), [])

    def test_a_subject_only_commit_carrying_no_why_fails_the_check(self):
        for msg in ("fix(a): b\n", "fix(a): b\n\nClaude-Session: ses_x\n"):
            self.assertTrue(any("no body" in p for p in pf.commit_problems(msg)), msg)
        self.assertEqual(pf.commit_problems("Merge pull request #79 from a/branch-name"), [])
        self.assertEqual(pf.commit_problems('Revert "fix(a): the old shape"\n\nThis reverts commit abc1234.\n'), [])

    def test_a_signature_footer_alone_is_not_a_body(self):
        for msg in ("fix(a): b\n\nGenerated with Claude Code\n",
                    "fix(a): b\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n\n"
                    "Co-Authored-By: Claude <noreply@anthropic.com>\n",
                    "fix(a): b\n\n_Generated with Claude Code_\n"):
            self.assertTrue(any("no body" in p for p in pf.commit_problems(msg)), msg)
        self.assertEqual(pf.commit_problems("fix(a): b\n\nwhy it was needed\n\n" +
                                             "Generated with Claude Code\n"), [])

    def test_a_complete_pr_passes_the_gate_for_its_head(self):
        self.assertEqual(pf.pr_problems("feat(tools): the seat's tools are native function calls", BODY,
                                        "abc123456789", ["eval/x.py"], gate=True), [])

    def test_the_no_body_refusal_quotes_section_2s_sentence_verbatim(self):
        """The doc is the single source: a rewrite of section 2's sentence fails this pin
        and forces re-pinning."""
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "docs", "STANDARDS.md"), encoding="utf-8") as doc:
            lines = list(doc)
        bullet_idx = [i for i, l in enumerate(lines) if l.startswith("- The body says why")]
        self.assertEqual(len(bullet_idx), 1, "section 2's sentence is found once")
        headings = [l.rstrip() for l in lines[:bullet_idx[0]] if l.startswith("## ")]
        self.assertEqual(headings[-1] if headings else "",
                         "## 2. Commits and PRs (checked by `scripts/pr_format.py`)")
        span = lines[bullet_idx[0]].split("says ", 1)[1].split(". Wrap", 1)[0]
        self.assertGreater(len(span), 20, span)
        refusal = [p for p in pf.commit_problems("fix(a): b\n") if "no body" in p]
        self.assertEqual(len(refusal), 1)
        self.assertIn(span, refusal[0], refusal[0])

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
        for section in ("Why", "Evidence", "Provenance", "Review", "Not done"):
            self.assertTrue(any("`## %s` still holds a skeleton placeholder" % section in p for p in problems),
                            (section, problems))
        # Only the acceptance line left unfilled on a docs-only PR (the gate's acceptance check is skipped).
        evidence_only = BODY.replace("- acceptance @ abc123456: OUT=/tmp/acc-native-1 12/12 stages PASS",
                                     "- acceptance @ abc123456: OUT=/tmp/<name> <N>/<N> stages PASS")
        self.assertTrue(any("`## Evidence` still holds" in p for p in pf.pr_problems("docs: x", evidence_only)))
        java_left = BODY.replace("12/12 stages PASS", "12/12 stages PASS, <J> Java")
        self.assertTrue(any("`## Evidence` still holds" in p for p in pf.pr_problems("docs: x", java_left)))
        # Prose, HTML and autolinks with angle brackets are not placeholders.
        for line in ("The <details> block stays; `List<String>` is fine.", "<details>", "</details>",
                     "<!-- reviewer note -->", "- <https://github.com/x/y/actions/runs/1>", "<br>",
                     '<img src="https://x/y.png" width="400">', "<details open>", '<p align="center">',
                     "The skeleton's `<one line>` is now refused."):
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

    def test_a_model_identifier_is_refused_and_a_trailer_is_not(self):
        """CLAUDE.md: model specs and priced slugs never go in commits or PRs; a name in prose is fine."""
        got = pf.commit_problems("fix(a): b\n\nthe seat now plays openrouter:vendor/some-model-1.\n")
        self.assertEqual(len(got), 1, got)
        self.assertIn("openrouter:vendor/some-model-1;", got[0])
        self.assertIn("name the model in prose instead", got[0])
        self.assertEqual(pf.commit_problems("fix(a): b\n\nLuna played the seat.\n"), [])  # a name in prose passes
        with open(pf.PRICES, encoding="utf-8") as f:
            slug = sorted(m for m in json.load(f)["models"] if not m.startswith("fake/"))[0]
        for prose in ("measured on %s." % slug, "measured on `%s`" % slug.upper()):
            self.assertTrue(any(slug in p.lower() for p in pf.commit_problems("fix(a): b\n\n%s\n" % prose)), prose)
        self.assertTrue(any(p.startswith("the title names") for p in pf.pr_problems("fix(a): use " + slug, BODY)))
        self.assertTrue(any(p.startswith("the body names") for p in
                            pf.pr_problems("docs: x", BODY.replace("MCP server; see the note.", "`%s` left" % slug))))
        # Trailers name a model by design; prose that names none, or only the `openrouter:` prefix, passes.
        self.assertEqual(pf.commit_problems("fix(a): b\n\nwhy it was needed\n\nCo-Authored-By: %s <x@y>\n"
                                            "Claude-Session: openrouter:%s\n" % (slug, slug)), [])
        self.assertEqual(pf.commit_problems("fix(a): b\n\nthe authorised model; any `openrouter:` spec is "
                                            "refused; see eval/pricing/prices.json.\n"), [])
        # Inside a URL or a path the slug is not prose; the loopback stub is no model; the diff
        # `git commit -v` appends below the scissors line is not the message.
        for prose in ("https://openrouter.ai/%s" % slug, "runs/%s/game.jsonl" % slug,
                      "the `openrouter:fake/harness-model` arm and fake/harness-model games"):
            self.assertEqual(pf.commit_problems("fix(a): b\n\nsee %s\n" % prose), [], prose)
        self.assertEqual(pf.commit_problems("fix(a): b\n\nwhy it was needed\n"
                                            "# ------------------------ >8 ------------------------\n"
                                            "+  openrouter:%s\n" % slug), [])

    def test_latest_verdict_per_agent_is_read(self):
        self.assertEqual(pf.reviews(BODY)["pr-review"], ("abc123456", "LOW", "two nits, fixed"))


class RestOnlyTest(unittest.TestCase):
    """Issue #118: `gh pr view` / `gh pr edit` / `gh pr merge` go over GraphQL, which Claude cloud
    sessions are refused (HTTP 403), so the merge gate could not run where most agents work. Every
    GitHub read and write goes through `gh api` REST routes instead."""

    def setUp(self):
        self.calls = []
        self.real_run = pf.run
        files = [{"filename": "eval/f%d.py" % i} for i in range(100)] + [{"filename": "README.md"}]
        commits = [{"sha": "c1" * 20, "commit": {"message": "fix(eval): one\n\nbody line\n\nFixes #1"}},
                   {"sha": "c2" * 20, "commit": {"message": "docs: two"}}]
        pull = {"title": "fix(eval): t", "body": BODY, "state": "open", "draft": False,
                "head": {"sha": "ab" * 20}}

        def fake(*cmd, **kw):
            self.calls.append((cmd, kw))
            path = cmd[cmd.index("api") + 1] if "api" in cmd else ""
            m = re.search(r"[?&]page=(\d+)", path)
            page = int(m.group(1)) if m else 1
            if "/files" in path:
                return json.dumps(files[(page - 1) * 100:page * 100])
            if "/commits" in path:
                return json.dumps(commits if page == 1 else [])
            return json.dumps(pull)
        pf.run = fake

    def tearDown(self):
        pf.run = self.real_run

    def test_a_pr_is_read_over_rest_into_the_same_shape(self):
        pr = pf.gh_pr(7)
        for cmd, _ in self.calls:
            self.assertEqual(cmd[:2], ("gh", "api"), cmd)
            self.assertIn("repos/{owner}/{repo}/pulls/7", cmd[2])
        self.assertEqual(pr["headRefOid"], "ab" * 20)
        self.assertEqual((pr["title"], pr["body"], pr["state"], pr["isDraft"]), ("fix(eval): t", BODY, "OPEN", False))
        self.assertEqual(len(pr["files"]), 101, "the second page of files was not read")
        self.assertEqual(pr["files"][-1]["path"], "README.md")
        self.assertEqual(pf.pr_commits(pr), [("c1" * 20, "fix(eval): one\n\nbody line\n\nFixes #1"),
                                             ("c2" * 20, "docs: two")])

    def test_a_recorded_line_is_written_over_rest(self):
        pf.record_line(7, "ab" * 20, "Review", "- pr-review @", "- pr-review @ {sha}: CLEAN")
        cmd, kw = self.calls[-1]
        self.assertEqual(cmd[:2], ("gh", "api"), cmd)
        self.assertIn("PATCH", cmd)
        self.assertIn("repos/{owner}/{repo}/pulls/7", cmd)
        self.assertIn("- pr-review @ %s: CLEAN" % ("ab" * 20)[:9], json.loads(kw["input"])["body"])

    def test_no_script_calls_a_graphql_gh_command(self):
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
        for name in ("pr_format.py", "pr_merge.sh"):
            with open(os.path.join(root, name), encoding="utf-8") as f:
                text = f.read()
            code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
            hit = re.search(r"""["']?gh["']?,? +["']?pr["']?\b""", code)
            self.assertIsNone(hit, "%s still calls `gh pr` (GraphQL): %r" % (name, hit and code[hit.start():hit.start() + 40]))


if __name__ == "__main__":
    unittest.main()
