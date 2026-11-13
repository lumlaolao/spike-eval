"""scripts/pr_format.py: the accepted examples are real subjects from this repository's history.
Lives in eval/tests/ so the acceptance pytests stage (every eval/tests/test_*.py) runs it."""
import importlib.util
import io
import json
import os
import re
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock
from pathlib import Path
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)

sys.path.insert(0, os.path.join(str(evalpath.REPO), "scripts"))
import pr_format as pf  # noqa: E402

BODY = """## What

- feat(tools): native calls
- the seat's tools are native function calls

## Why

30 of 43 tool requests were refused (eval/tool_audit.py runs/lines/ctl/control).

## Evidence

- acceptance @ abc123456: OUT=/tmp/acc-native-1 19/19 stages PASS, 278 Java, 141 Python

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
                  "Merge pull request #79 from lumlaolao/obs/v12-opponent-card-text"):
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
        with open(os.path.join(str(evalpath.EVAL),
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

    def test_record_acceptance_refuses_a_subset(self):
        # Refused before it touches GitHub: a subset or a partial pass is never written as the gate's line.
        for passed, total in (("4", "4"), ("18", "19")):
            with self.assertRaises(SystemExit) as cm:
                pf.main(["record-acceptance", "1", "abc1234", "/tmp/x", passed, total])
            self.assertIn("not a full pass", str(cm.exception.code))
        self.assertGreaterEqual(pf.full_stage_count(), 19)

    def test_record_acceptance_reads_the_runs_own_summary(self):
        # The typed counts must match the run's own summary.txt: a PASS line per stage in ALL_STAGES, no FAIL line
        # (standards of #357).
        stages = pf.all_stages()
        self.assertIn("canary", stages)
        full = ["PASS %s: ok" % (st if st != "canary" else "canary-floor") for st in stages] + ["PASS canary-random: ok"]
        with tempfile.TemporaryDirectory() as d:
            summary = Path(d) / "summary.txt"
            summary.write_text("\n".join(full) + "\n")
            self.assertEqual(pf.summary_problems(d, str(len(full)), str(len(full))), [])
            self.assertTrue(pf.summary_problems(d, "20", "20"))                    # typed counts that are not the run's
            summary.write_text("\n".join(full[:-3] + ["FAIL lines: x"]) + "\n")
            p = pf.summary_problems(d, str(len(full)), str(len(full)))
            self.assertTrue(any("FAIL" in x for x in p) and any("no PASS line" in x for x in p), p)
        self.assertTrue(any("summary.txt" in x for x in pf.summary_problems("/nonexistent/out", "19", "19")))

    def test_a_missing_stage_list_stops_the_gate(self):
        # Fail closed: with no ALL_STAGES line the gate has no bar, so it stops rather than accept any N/N.
        pf.all_stages.cache_clear()
        try:
            with mock.patch.object(Path, "read_text", return_value="STAGES=x\n"):
                with self.assertRaises(SystemExit):
                    pf.all_stages()
        finally:
            pf.all_stages.cache_clear()

    def test_code_changes_need_an_acceptance_run_in_evidence(self):
        line = "- acceptance @ abc123456: OUT=/tmp/acc-native-1 19/19 stages PASS, 278 Java, 141 Python"

        def gate(evidence, files, head="abc123456789"):
            return pf.pr_problems("docs: x", BODY.replace(line, evidence), head, files, gate=True)
        # Saying the word is not evidence; neither is a partial pass, nor a pass on another commit.
        self.assertTrue(any("has no line" in p for p in gate("- no acceptance run was made", ["forge-overlay/a.java"])))
        # An engine patch is compiled into every game like the overlay is.
        self.assertTrue(any("has no line" in p for p in gate("- tried it", ["engine-patches/forge-ai/a.java.patch"])))
        self.assertTrue(any("full pass" in p for p in gate(line.replace("19/19", "18/19"), ["eval/x.py"])))
        # A subset run is not the full suite, however green (PR #357 recorded "4/4 stages PASS" for four stages).
        self.assertTrue(any("full pass" in p for p in gate(line.replace("19/19", "4/4"), ["eval/x.py"])))
        # pr-review of #358: 18 lines is a run that skipped a stage (canary writes two), so the bar is 19, not 18.
        self.assertEqual(pf.full_stage_count(), len(pf.all_stages()) + 1)
        self.assertTrue(any("full pass" in p for p in gate(line.replace("19/19", "18/18"), ["eval/x.py"])))
        self.assertTrue(any("full pass" in p for p in gate(line.replace("abc123456", "fff000111"), ["scripts/pr_merge.sh"])))
        self.assertEqual(gate(line, ["install.sh"]), [])
        self.assertEqual(gate("- read it twice", ["docs/a.md"]), [])
        # Every launcher at the root, so a new one cannot skip the acceptance requirement unnoticed.
        for launcher in sorted(p.name for p in evalpath.REPO.glob("*.sh")):
            self.assertTrue(any("has no line" in p for p in gate("- tried it", [launcher])), launcher)

    def test_a_changed_script_still_on_the_untested_list_fails_the_gate(self):
        # #247: the list only shrinks, so a PR that changes a listed script gives it a test and takes it off.
        t, head = "feat(tools): native calls", "abc123456789"
        listed = {"line_report.py", "model_sweep.py"}
        p = pf.pr_problems(t, BODY, head, ["eval/line_report.py"], gate=True, untested=listed)
        self.assertTrue(any("eval/line_report.py" in x and "UNTESTED.txt" in x for x in p), p)
        # A script in a folder under eval/ is held to the list the same way.
        p = pf.pr_problems(t, BODY, head, ["eval/demo/demo_cards.py"], gate=True, untested={"demo_cards.py"})
        self.assertTrue(any("eval/demo/demo_cards.py" in x for x in p), p)
        # Off the list at the head (the PR removed it), a script not listed, a file that is not a script: nothing.
        for files, names in ((["eval/line_report.py"], {"model_sweep.py"}), (["eval/aggregate.py"], listed),
                             (["eval/UNTESTED.txt"], listed)):
            self.assertEqual(pf.pr_problems(t, BODY, head, files, gate=True, untested=names), [], files)
        # The check is the gate's: a plain check-pr does not read the list.
        self.assertEqual(pf.pr_problems(t, BODY, head, ["eval/line_report.py"], untested=listed), [])

    def test_the_gate_reads_the_untested_list_at_the_pr_head(self):
        with mock.patch.object(pf, "run", return_value="# a comment\nline_report.py\n\nmodel_sweep.py\n") as run:
            self.assertEqual(pf.untested_at_head({"headRefOid": "abc123456789"}), {"line_report.py", "model_sweep.py"})
        self.assertIn("eval/UNTESTED.txt?ref=abc123456789", " ".join(run.call_args[0]))
        # Without the raw media type gh returns the JSON wrapper, which would match no name: the gate stops instead.
        self.assertIn("Accept: application/vnd.github.raw", run.call_args[0])
        with mock.patch.object(pf, "run", return_value='{"name": "UNTESTED.txt", "content": "bGluZV9yZXBvcnQucHk="}'):
            with self.assertRaises(SystemExit):
                pf.untested_at_head({"headRefOid": "abc123456789"})
        # The gate reads the list as eval/tests/test_untested_list.py does.
        spec = importlib.util.spec_from_file_location("untested_list", evalpath.EVAL / "tests" / "test_untested_list.py")
        listing = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(listing)
        self.assertEqual(pf.untested_names((evalpath.EVAL / "UNTESTED.txt").read_text()), listing.UNTESTED)

    def test_check_pr_gate_hands_the_heads_list_to_the_gate(self):
        pr = {"title": "feat(tools): native calls", "body": BODY, "headRefOid": "abc123456789",
              "files": [{"path": "eval/line_report.py"}], "commits": []}
        out = io.StringIO()
        with mock.patch.object(pf, "gh_pr", return_value=pr), \
                mock.patch.object(pf, "untested_at_head", return_value={"line_report.py"}), redirect_stdout(out):
            rc = pf.main(["check-pr", "410", "--gate"])
        self.assertEqual(rc, 1, out.getvalue())
        self.assertIn("eval/line_report.py is changed but still on eval/UNTESTED.txt", out.getvalue())

    def test_the_gate_refuses_a_what_that_misses_a_commit(self):
        # #343 (standards, round 3): `## What` was written at the first commit and missed the three review-driven ones.
        t = "feat(tools): native calls"
        commits = [("1a976281aa", "feat(tools): native calls\n\nwhy"), ("2b0000000a", "fix(tools): a later fix\n\nwhy"),
                   ("3c0000000a", "Merge branch 'main' into feat/x")]
        p = pf.pr_problems(t, BODY, "abc123456789", [], gate=True, commits=commits)
        self.assertEqual([x for x in p if "`## What`" in x],
                         ["`## What` does not list commit 2b0000000 (fix(tools): a later fix); list every commit subject"])
        listed = BODY.replace("- feat(tools): native calls\n", "- feat(tools): native calls\n- fix(tools): a later fix\n")
        self.assertEqual(pf.pr_problems(t, listed, "abc123456789", [], gate=True, commits=commits), [])
        # A bullet, not a substring: "fix(tools): a later fix" is not listed by "fix(tools): a later fixture".
        longer = BODY.replace("- feat(tools): native calls\n", "- feat(tools): native calls\n- fix(tools): a later fixture\n")
        self.assertTrue(any("2b0000000" in x for x in pf.pr_problems(t, longer, "abc123456789", [], gate=True, commits=commits)))
        # A subject wrapped across two lines of its bullet is still the subject.
        wrapped = BODY.replace("- feat(tools): native calls\n", "- feat(tools): native calls\n- fix(tools): a later\n  fix\n")
        self.assertEqual(pf.pr_problems(t, wrapped, "abc123456789", [], gate=True, commits=commits), [])

    def test_a_merge_commit_with_a_typed_subject_is_not_asked_for_in_what(self):
        # #353 (standards): 50b7d17 merged main under "eval(propose): merge main; ..." and the gate demanded it in
        # `## What`, though check-branch (`git log --no-merges`) leaves it out. A merge is known by its parents.
        pr = {"commits": [
            {"oid": "1a976281aa", "messageHeadline": "feat(tools): native calls", "messageBody": "why", "parents": 1},
            {"oid": "50b7d17fea", "messageHeadline": "eval(propose): merge main; the arm moves", "messageBody": "",
             "parents": 2}]}
        self.assertEqual([sha for sha, _ in pf.pr_commits(pr)], ["1a976281aa"])
        self.assertEqual(pf.pr_problems("feat(tools): native calls", BODY, "abc123456789", [], gate=True,
                                        commits=pf.pr_commits(pr)), [])
        self.assertEqual(pf.merge_problems(pr), [])
        # Left out of `## What`, a merge's message is still audited for a model spec (pr-review of #354).
        pr["commits"][1]["messageBody"] = "ran openrouter:openai/gpt-6-luna"
        self.assertTrue(any(p.startswith("commit 50b7d17fe: the message names the model spec")
                            for p in pf.merge_problems(pr)), pf.merge_problems(pr))

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
        evidence_only = BODY.replace("- acceptance @ abc123456: OUT=/tmp/acc-native-1 19/19 stages PASS",
                                     "- acceptance @ abc123456: OUT=/tmp/<name> <N>/<N> stages PASS")
        self.assertTrue(any("`## Evidence` still holds" in p for p in pf.pr_problems("docs: x", evidence_only)))
        java_left = BODY.replace("19/19 stages PASS", "19/19 stages PASS, <J> Java")
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
        root = os.path.join(str(evalpath.REPO), "scripts")
        for name in ("pr_format.py", "pr_merge.sh"):
            with open(os.path.join(root, name), encoding="utf-8") as f:
                text = f.read()
            code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
            hit = re.search(r"""["']?gh["']?,? +["']?pr["']?\b""", code)
            self.assertIsNone(hit, "%s still calls `gh pr` (GraphQL): %r" % (name, hit and code[hit.start():hit.start() + 40]))


if __name__ == "__main__":
    unittest.main()
