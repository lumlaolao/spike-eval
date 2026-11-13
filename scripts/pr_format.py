#!/usr/bin/env python3
"""Commit and PR format: the one place that knows how they are named and structured
(docs/STANDARDS.md section 2). Stdlib only.

  python3 scripts/pr_format.py check-commit <file-with-message>   # the commit-msg hook
  python3 scripts/pr_format.py check-branch [BASE]                # every commit on this branch
  python3 scripts/pr_format.py check-pr <number>                  # title, body sections, review record
  python3 scripts/pr_format.py new-pr [BASE]                      # print a title and body skeleton
  python3 scripts/pr_format.py record-review <number> <sha reviewed> <skill> <verdict> [note]
  python3 scripts/pr_format.py record-acceptance <number> <sha tested> <OUT dir> <passed> <total> [note]
  python3 scripts/pr_format.py head <number>                      # the head sha the gate would check
  python3 scripts/pr_format.py state <number>                     # "OPEN false": state and draft

Every GitHub call is a REST `gh api` call (GraphQL is refused in Claude cloud sessions).

`check-pr --gate` also requires what a merge needs: all three review verdicts for the PR's current
head commit, none BLOCKER or HIGH, an acceptance run named in Evidence when code changed, and no
changed eval script still on eval/UNTESTED.txt at the PR head.
Exit 0 clean, 1 with one line per problem.
"""
import functools
import json
import os
from pathlib import Path
import re
import subprocess
import sys

TYPES = ("feat", "fix", "perf", "eval", "data", "test", "docs", "refactor", "chore", "build")
SUBJECT = re.compile(r"^(%s)(\([^()\n]{1,40}\))?: \S.*$" % "|".join(TYPES))
SUBJECT_MAX = 100
EXEMPT_SUBJECTS = ("Merge ", "Revert ")
SECTIONS = ("What", "Why", "Evidence", "Provenance", "Review", "Not done")
SKILLS = ("pr-review", "simplify", "standards")
# Each skill reports in its own words; the gate refuses the blocking ones.
SKILL_VERDICTS = {"pr-review": ("CLEAN", "LOW", "MEDIUM", "HIGH", "BLOCKER"),
                  "simplify": ("CLEAN", "SUGGESTIONS"),
                  "standards": ("CLEAN", "VIOLATIONS")}
VERDICTS = tuple(sorted(set(v for vs in SKILL_VERDICTS.values() for v in vs)))
BLOCKING = ("HIGH", "BLOCKER", "VIOLATIONS")
REVIEW_LINE = re.compile(r"^- (%s) @ ([0-9a-f]{7,40}): (%s)\b(.*)$" % ("|".join(SKILLS), "|".join(VERDICTS)), re.M)
CODE_PATHS = ("forge-overlay/", "engine-patches/", "eval/", "scripts/", "install.sh", "play-vs-llm.sh",
              "setup.sh")
# `- acceptance @ <head sha>: OUT=<dir> <N>/<N> stages PASS ...`
ACCEPTANCE = re.compile(r"^- acceptance @ ([0-9a-f]{7,40}): OUT=(\S+) (\d+)/(\d+) stages PASS\b", re.M)


@functools.lru_cache(maxsize=None)
def all_stages():
    """The stage names a full run executes: ALL_STAGES in eval/harness_acceptance.sh, its one source."""
    script = Path(__file__).resolve().parents[1] / "eval" / "harness_acceptance.sh"
    m = re.search(r'^ALL_STAGES="([^"]+)"', script.read_text(encoding="utf-8"), re.M)
    if not m:
        sys.exit("pr_format: no ALL_STAGES=\"...\" line in %s" % script)
    return tuple(m.group(1).split())


def full_stage_count():
    """PASS lines a full run writes: one per stage, and canary writes two (canary-floor, canary-random)."""
    return len(all_stages()) + ("canary" in all_stages())


def full_pass(passed, total):
    """A run is the gate's evidence only when every line passed and it ran every stage: a STAGES subset is never
    (PR #357 recorded "4/4 stages PASS"; pr-review of #358: 18 would let a run without preflight through)."""
    return passed == total and int(total) >= full_stage_count()


def overlay_sha_here(repo):
    """(this checkout's overlay hash, None) as run_batch.py stamps it (eval/benchmark_version.py, asked in its own
    interpreter the way harness_acceptance.sh asks it: scripts/ does not import eval/ modules), or (None, why)."""
    p = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import benchmark_version as b; "
                        "print(b.overlay_sha_of_checkout(b.Path(sys.argv[2])))", str(Path(repo) / "eval"), str(repo)],
                       capture_output=True, text=True)
    out = p.stdout.strip()
    if p.returncode != 0 or not out:
        return None, (p.stderr.strip().splitlines() or ["exit %d" % p.returncode])[-1]
    return out, None


def head_problems(outdir, sha):
    """What keeps the run in `outdir` from being evidence for commit `sha`. The run's head.txt (harness_acceptance.sh:
    the commit it tested, then its count of changed files) must name `sha` with no change. A run from before head.txt
    is checked as far as it can be: its overlay-sha.txt against this checkout's overlay hash, when this checkout is at
    `sha` with no change (benchmark_version, the hash run_batch.py stamps)."""
    head = outdir / "head.txt"
    if head.is_file():
        words = head.read_text(encoding="utf-8", errors="replace").split()
        if not words or not same_commit(words[0], sha):
            return ["%s says the run tested %s, not %s" % (head, (words or ["nothing"])[0][:9], sha[:9])]
        if len(words) > 1 and words[1] != "0":
            return ["%s says the run's checkout had %s uncommitted change(s)" % (head, words[1])]
        return []
    built = outdir / "overlay-sha.txt"
    if not built.is_file():
        return ["%s has neither head.txt nor overlay-sha.txt: which code it tested cannot be checked" % outdir]
    repo = Path(__file__).resolve().parents[1]
    local = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout
    if not same_commit(local, sha) or dirty.strip():
        return ["%s has no head.txt; its overlay-sha.txt can be checked only from a clean checkout at %s (this one is "
                "%s%s)" % (outdir, sha[:9], local[:9], ", with changes" if dirty.strip() else "")]
    mine, err = overlay_sha_here(repo)
    if mine is None:
        return ["this checkout's overlay hash could not be made (%s)" % err]
    recorded = built.read_text(encoding="utf-8", errors="replace").strip()
    if recorded == mine:
        return []
    return ["%s records overlay %s, the overlay at %s is %s" % (built, recorded, sha[:9], mine)]


def summary_problems(outdir, passed, total, sha=None):
    """What keeps `<out>/summary.txt` from being a full pass matching the typed counts (standards of #357), and, given
    the commit it is recorded for, from being that commit's run (head_problems)."""
    path = Path(outdir)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[1] / path      # a run's OUT is often repo-relative (runs/...)
    heads = head_problems(path, sha) if sha is not None and path.is_dir() else []
    path = path / "summary.txt"
    if not path.is_file():
        return ["%s not found: record-acceptance reads the run's own summary" % path]
    lines = [l for l in path.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
    problems = ["%s holds a FAIL line: %s" % (path, l[:80]) for l in lines if l.startswith("FAIL")]
    for st in all_stages():
        if not any(re.match(r"PASS %s[-:]" % re.escape(st), l) for l in lines):
            problems.append("%s has no PASS line for stage %s" % (path, st))
    n = sum(l.startswith("PASS") for l in lines)
    if (passed, total) != (str(n), str(len(lines))):
        problems.append("%s/%s typed, but %s records %d/%d" % (passed, total, path, n, len(lines)))
    return problems + heads


TRAILER = re.compile(r"^(Co-Authored-By|Claude-Session|Signed-off-by): ", re.I)
# A generated signature footer is not a why: alone it satisfies nothing (pr-review finding). The
# observed shapes: the plain sentence, the markdown-italic one, the emoji link one.
SIGNATURE = re.compile(r"^\s*(?:🤖\s*)?_?Generated with "
                       r"(?:\[Claude Code\]\([^)]*\)|Claude Code)_?\s*$", re.I)
# The `new-pr` skeleton's placeholders, matched exactly: a shape (any `<...>` line) also caught real
# HTML such as `<img src=...>` and `<details open>`.
PH_WHY = "<the evidence it was needed: file:line, a run directory, a count>"
PH_OUT = "OUT=/tmp/<name>"
PH_COUNT = "<N>/<N>"
PH_JAVA = "<J> Java"
PH_PYTHON = "<P> Python"
PH_MEASURE = "<measurement, next to its trivial baseline>"
PH_PROVENANCE = "<which stamps moved, or: none>"
PH_REVIEW = "<one line>"
PH_NOT_DONE = "<left out on purpose, and what would reopen it>"
PLACEHOLDERS = (PH_WHY, PH_OUT, PH_COUNT, PH_JAVA, PH_PYTHON, PH_MEASURE, PH_PROVENANCE, PH_REVIEW, PH_NOT_DONE)
CODE_SPAN = re.compile(r"`[^`\n]*`")
# Refused: an `openrouter:` spec, and any slug under `models` in eval/pricing/prices.json. The
# loopback stub (`fake/`) is no model: it sends nothing out, and the gauntlet docs name it.
PRICES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "eval", "pricing", "prices.json")
OPENROUTER_SPEC = re.compile(r"\bopenrouter:(?!fake/)[A-Za-z0-9][\w.:/-]*[\w/]", re.I)
# `git commit -v` hands the hook the diff below this line; it is not part of the message.
SCISSORS = re.compile(r"^# -+ >8 -+$", re.M)


@functools.lru_cache(maxsize=None)
def model_slug_pattern():
    """One pattern over every slug priced in prices.json, matched as a whole token (not inside a
    URL or path). An unreadable prices.json leaves the `openrouter:` check alone."""
    try:
        with open(PRICES, encoding="utf-8") as f:
            slugs = sorted((m for m in json.load(f)["models"] if not m.startswith("fake/")), key=len, reverse=True)
    except (OSError, ValueError, KeyError):
        slugs = []
    return re.compile(r"(?<![\w./-])(%s)(?![\w/-]|\.\w)" % "|".join(map(re.escape, slugs)) if slugs else r"(?!)",
                      re.I)


def identifier_problems(lines, where):
    """One refusal per model identifier named in `lines`; git trailer lines name a model by design."""
    out = []
    for line in lines:
        if TRAILER.match(line):
            continue
        out += [m.group(0) for m in OPENROUTER_SPEC.finditer(line)]
        out += [m.group(1) for m in model_slug_pattern().finditer(OPENROUTER_SPEC.sub("", line))]
    return ["%s names the model spec or priced slug %s; name the model in prose instead (\"Luna\") or its role "
            "(STANDARDS section 2)" % (where, ident)
            for ident in dict.fromkeys(out)]


def subject_problems(subject):
    out = []
    if subject.startswith(EXEMPT_SUBJECTS):
        return out
    if not SUBJECT.match(subject):
        out.append("subject is not `type(scope): what is now true` with type in %s: %r" % ("/".join(TYPES), subject))
    if len(subject) > SUBJECT_MAX:
        out.append("subject is %d characters (max %d)" % (len(subject), SUBJECT_MAX))
    if subject.endswith("."):
        out.append("subject ends with a period")
    return out


def commit_problems(message):
    # '#' lines are git template comments, stripped before the audit: a body written only of
    # them reads as no body (heading-only bodies refuse; refuse-not-bless is the safe direction).
    lines = [l for l in SCISSORS.split(message, 1)[0].splitlines() if not l.startswith("#")]
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return ["empty commit message"]
    out = subject_problems(lines[0]) + identifier_problems(lines, "the message")
    # a merge/revert subject is exempt from the body checks like the subject checks: a merge's
    # why lives in its second parent, a revert's in the message it cites
    if lines[0].startswith(EXEMPT_SUBJECTS):
        return out
    body = [l for l in lines[1:] if l.strip() and not TRAILER.match(l) and not SIGNATURE.match(l)]
    if not body:
        out.append("a commit with no body carries no why; say why, what was measured, and "
                   "what would have caught it (STANDARDS section 2)")
        return out
    if lines[1].strip():
        out.append("no blank line between the subject and the body")
    for l in lines[2:]:
        if len(l) > SUBJECT_MAX and " " in l.strip() and not TRAILER.match(l) and "://" not in l:
            out.append("body line over %d characters: %r" % (SUBJECT_MAX, l[:60] + "..."))
            break
    return out


def sections(body):
    """{heading: text} for the `## Heading` blocks of a PR body, in order of appearance."""
    out, cur = {}, None
    for line in (body or "").splitlines():
        m = re.match(r"^## +(.+?)\s*$", line)
        if m:
            cur = m.group(1)
            out[cur] = ""
        elif cur is not None:
            out[cur] += line + "\n"
    return out


def reviews(body):
    """Latest recorded verdict per agent: {agent: (sha, verdict, note)}."""
    out = {}
    for agent, sha, verdict, note in REVIEW_LINE.findall(sections(body).get("Review", "")):
        out[agent] = (sha, verdict, note.strip(" -:"))
    return out


def same_commit(a, b):
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def pr_problems(title, body, head=None, files=(), gate=False, commits=(), untested=()):
    out = ["title: " + p for p in subject_problems(title)]
    out += identifier_problems([title], "the title") + identifier_problems((body or "").splitlines(), "the body")
    secs = sections(body)
    present = [s for s in secs if s in SECTIONS]
    for s in SECTIONS:
        if s not in secs:
            out.append("body has no `## %s` section" % s)
        elif not secs[s].strip() or re.fullmatch(r"\s*(TODO|TBD|\.\.\.)\s*", secs[s], re.I):
            out.append("`## %s` is empty" % s)
        elif (ph := next((p for p in PLACEHOLDERS if p in CODE_SPAN.sub("", secs[s])), None)):
            out.append("`## %s` still holds a skeleton placeholder: %s" % (s, ph))
    if present != [s for s in SECTIONS if s in secs]:
        out.append("sections are out of order; expected %s" % ", ".join(SECTIONS))
    if not gate:
        return out
    # `## What`'s bullets, each with its wrapped lines joined: a subject is listed when a bullet is it, or starts with it.
    bullets = [" ".join(b.split()) for b in re.split(r"(?m)^\s*-\s+", secs.get("What", ""))[1:]]
    for sha, message in commits:
        out += ["commit %s: %s" % (sha[:9], p) for p in commit_problems(message)]
        # The body is the record the gate merges on: a commit pushed after review (#343) is listed like the first. A
        # merge commit is not a change of its own (pr_commits drops two-parent ones; a "Merge " subject from a caller
        # without parents is skipped here); a revert is, so it is listed.
        subject = " ".join(message.strip().split("\n", 1)[0].split())
        if subject and not subject.startswith("Merge ") and not any(
                b == subject or b.startswith(subject + " ") for b in bullets):
            out.append("`## What` does not list commit %s (%s); list every commit subject" % (sha[:9], subject))
    got = reviews(body)
    for agent in SKILLS:
        if agent not in got:
            out.append("no `%s` verdict recorded in `## Review`" % agent)
            continue
        sha, verdict, _ = got[agent]
        if head and not same_commit(head, sha):
            out.append("%s reviewed %s but the PR head is %s; review the current head" % (agent, sha[:9], head[:9]))
        if verdict not in SKILL_VERDICTS[agent]:
            out.append("%s verdict %s is not one of %s" % (agent, verdict, "/".join(SKILL_VERDICTS[agent])))
        if verdict in BLOCKING:
            out.append("%s verdict is %s; fix it and review again" % (agent, verdict))
    # eval/UNTESTED.txt only shrinks (docs/STANDARDS.md section 3, #247): a listed script the PR changes gets its test
    # and leaves the list in the same PR. `untested` is the list at the PR head, so the PR that removes a name passes.
    for f in files:
        name = f.rsplit("/", 1)[-1]
        if f.startswith("eval/") and f.endswith(".py") and name in untested:
            out.append("%s is changed but still on eval/UNTESTED.txt; give it eval/tests/test_%s and take it off the list"
                       % (f, name))
    if any(f.startswith(CODE_PATHS) for f in files):
        runs = ACCEPTANCE.findall(secs.get("Evidence", ""))
        if not runs:
            out.append("code changed but `## Evidence` has no line "
                       "`- acceptance @ <head sha>: OUT=<dir> <N>/<N> stages PASS`")
        elif not any(full_pass(passed, total) and (not head or same_commit(head, sha))
                     for sha, _, passed, total in runs):
            out.append("no acceptance run in `## Evidence` is a full pass (every stage, at least %d) for the PR head %s"
                       % (full_stage_count(), (head or "")[:9]))
    return out


def run(*cmd, input=None):
    return subprocess.run(cmd, check=True, stdout=subprocess.PIPE, universal_newlines=True, input=input).stdout


def base_ref(arg=None):
    return arg or "origin/main"


def branch_commits(base):
    """The branch's own commits, base..HEAD, merges left out. Fetch the base first: a stale
    local origin/main walks main's own earlier history too (the merge gate instead walks
    GitHub's PR commit list and cannot see a stale local ref)."""
    out = run("git", "log", "--no-merges", "--format=%H%x1f%B%x1e", "%s..HEAD" % base)
    return [tuple(c.strip("\n").split("\x1f", 1)) for c in out.split("\x1e") if c.strip()]


# GitHub is read and written over REST (`gh api`) only: `gh pr view/edit/merge` use GraphQL, which
# Claude cloud sessions are refused (issue #118). `{owner}/{repo}` is filled in by gh from the remote.
PULLS = "repos/{owner}/{repo}/pulls/%s"


def gh_pages(path):
    """Every item of a paginated REST list (100 per page)."""
    out, page = [], 1
    while True:
        got = json.loads(run("gh", "api", "%s?per_page=100&page=%d" % (path, page)))
        out += got
        if len(got) < 100:
            return out
        page += 1


def gh_pr(number):
    """The PR as the gate reads it: title, body, head sha, state, draft, changed paths, commits."""
    path = PULLS % number
    pull = json.loads(run("gh", "api", path))
    commits = []
    for c in gh_pages(path + "/commits"):
        headline, _, rest = c["commit"]["message"].partition("\n")
        commits.append({"oid": c["sha"], "messageHeadline": headline, "messageBody": rest.strip("\n"),
                        "parents": len(c.get("parents") or [])})
    return {"title": pull["title"], "body": pull.get("body") or "", "headRefOid": pull["head"]["sha"],
            "state": pull["state"].upper(), "isDraft": bool(pull.get("draft")),
            "files": [{"path": f["filename"]} for f in gh_pages(path + "/files")], "commits": commits}


def untested_at_head(pr):
    """The script names on eval/UNTESTED.txt as the PR head has it (not as this checkout has it). A script the PR
    deletes is off it there too: eval/tests/test_untested_list.py fails a name that is listed but gone."""
    text = run("gh", "api", "-H", "Accept: application/vnd.github.raw",
               "repos/{owner}/{repo}/contents/eval/UNTESTED.txt?ref=%s" % pr["headRefOid"])
    if text.lstrip().startswith("{"):   # the JSON wrapper, not the file: no name would match and the check would pass
        sys.exit("check-pr: eval/UNTESTED.txt came back as JSON, not its text; the gate cannot read the list")
    return untested_names(text)


def untested_names(text):
    """The names eval/UNTESTED.txt lists: every line but blanks and # comments."""
    return {ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")}


def pr_commits(pr):
    """(sha, full message) of the PR's commits as GitHub holds them, merges left out as check-branch leaves them out
    (`git log --no-merges`): a merge is known by its parents, whatever its subject (#353)."""
    return [(c["oid"], c["messageHeadline"] + ("\n\n" + c["messageBody"] if c.get("messageBody") else ""))
            for c in pr.get("commits") or [] if c.get("parents", 1) < 2]


def merge_problems(pr):
    """A merge is not listed in `## What`, but its message is still published: it may not name a model spec or priced
    slug (pr-review of #354: a typed merge made on GitHub never meets the commit-msg hook)."""
    return ["commit %s: %s" % (c["oid"][:9], p) for c in pr.get("commits") or [] if c.get("parents", 1) >= 2
            for p in identifier_problems((c["messageHeadline"] + "\n" + (c.get("messageBody") or "")).splitlines(),
                                         "the message")]


def skeleton(base):
    commits = branch_commits(base)
    subjects = [m.splitlines()[0] for _, m in commits]
    title = subjects[-1] if len(subjects) == 1 else (subjects[0] if subjects else "type(scope): what is now true")
    head = run("git", "rev-parse", "--short=9", "HEAD").strip()
    body = ["## What", ""] + ["- " + s for s in reversed(subjects)] + [
        "", "## Why", "", PH_WHY, "",
        "## Evidence", "", "- acceptance @ %s: %s %s stages PASS, %s, %s" % (head, PH_OUT, PH_COUNT, PH_JAVA, PH_PYTHON),
        "- " + PH_MEASURE, "",
        "## Provenance", "", PH_PROVENANCE, "",
        "## Review", ""] + ["- %s @ %s: <%s> %s" % (a, head, "|".join(SKILL_VERDICTS[a]), PH_REVIEW) for a in SKILLS] + [
        "", "## Not done", "", PH_NOT_DONE, ""]
    return title, "\n".join(body)


def record_review(number, reviewed, agent, verdict, note):
    if agent not in SKILLS or verdict not in SKILL_VERDICTS[agent]:
        sys.exit("skill must be one of %s; verdicts: %s" % (", ".join(SKILLS), "; ".join(
            "%s %s" % (s, "/".join(v)) for s, v in SKILL_VERDICTS.items())))
    record_line(number, reviewed, "Review", "- %s @" % agent,
                "- %s @ {sha}: %s%s" % (agent, verdict, (" " + note) if note else ""))


def replace_section(body, section, text):
    """`body` with the `## section` block replaced by `text`; the heading is matched as a line."""
    m = re.search(r"(?m)^## +%s[ \t]*$" % re.escape(section), body)
    if not m:
        return None
    nxt = re.search(r"(?m)^## ", body[m.end():])
    end = m.end() + nxt.start() if nxt else len(body)
    return body[:m.start()] + "## %s\n\n%s\n\n" % (section, text.strip("\n")) + body[end:]


def updated_body(body, section, prefix, line):
    """The PR body with `line` put in `## section` in place of the line starting with `prefix` (or
    appended), and any `(pending ...)` placeholder there dropped. None when the section is missing."""
    secs = sections(body)
    if section not in secs:
        return None
    kept = [l for l in secs[section].splitlines()
            if l.strip() and not l.startswith(prefix) and not l.lower().startswith("(pending")]
    return replace_section(body, section, "\n".join(kept + [line]))


def record_line(number, reviewed, section, prefix, template):
    """Record a line for the commit that was actually examined. Refused unless that commit is still
    the PR head: a verdict typed after a push would otherwise bless a commit nobody looked at."""
    pr = gh_pr(number)
    if not same_commit(pr["headRefOid"], reviewed) or len(reviewed) < 7:
        sys.exit("PR #%s head is %s but the commit examined was %s; examine the head and record again"
                 % (number, pr["headRefOid"][:9], reviewed[:9]))
    line = template.replace("{sha}", pr["headRefOid"][:9])
    body = updated_body(pr["body"] or "", section, prefix, line)
    if body is None:
        sys.exit("PR #%s has no `## %s` section" % (number, section))
    run("gh", "api", "-X", "PATCH", PULLS % number, "--input", "-", input=json.dumps({"body": body}))
    print(line)


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    problems = []
    if cmd == "check-commit" and args:
        problems = commit_problems(open(args[0], encoding="utf-8", errors="replace").read())
    elif cmd == "check-branch":
        for sha, msg in branch_commits(base_ref(args[0] if args else None)):
            problems += ["%s: %s" % (sha[:9], p) for p in commit_problems(msg)]
    elif cmd == "check-pr" and args:
        gate = "--gate" in args
        pr = gh_pr([a for a in args if a != "--gate"][0])
        problems = pr_problems(pr["title"], pr["body"], pr["headRefOid"], [f["path"] for f in pr.get("files") or []],
                               gate, pr_commits(pr) if gate else (), untested_at_head(pr) if gate else ()) + (
                                   merge_problems(pr) if gate else [])
    elif cmd == "new-pr":
        title, body = skeleton(base_ref(args[0] if args else None))
        print(title + "\n\n" + body)
        return 0
    elif cmd == "head" and args:
        print(gh_pr(args[0])["headRefOid"])
        return 0
    elif cmd == "state" and args:
        pr = gh_pr(args[0])
        print("%s %s" % (pr["state"], str(pr["isDraft"]).lower()))
        return 0
    elif cmd == "record-acceptance" and len(args) >= 5:
        if not (args[3].isdigit() and args[4].isdigit()) or not full_pass(args[3], args[4]):
            sys.exit("record-acceptance: %s/%s is not a full pass of all %d stage lines; a STAGES subset is reported "
                     "as prose in Evidence, never as the gate's line" % (args[3], args[4], full_stage_count()))
        problems = summary_problems(args[2], args[3], args[4], args[1])
        if problems:
            sys.exit("record-acceptance: " + "; ".join(problems))
        record_line(args[0], args[1], "Evidence", "- acceptance @",
                    "- acceptance @ {sha}: OUT=%s %s/%s stages PASS%s" % (
                        args[2], args[3], args[4], (", " + " ".join(args[5:])) if args[5:] else ""))
        return 0
    elif cmd == "record-review" and len(args) >= 4:
        record_review(args[0], args[1], args[2], args[3], " ".join(args[4:]))
        return 0
    else:
        print(__doc__)
        return 2
    for p in problems:
        print("FORMAT: " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
