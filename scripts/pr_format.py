#!/usr/bin/env python3
"""Commit and PR format: the one place that knows how they are named and structured
(docs/STANDARDS.md section 2). Stdlib only.

  python3 scripts/pr_format.py check-commit <file-with-message>   # the commit-msg hook
  python3 scripts/pr_format.py check-branch [BASE]                # every commit on this branch
  python3 scripts/pr_format.py check-pr <number>                  # title, body sections, review record
  python3 scripts/pr_format.py new-pr [BASE]                      # print a title and body skeleton
  python3 scripts/pr_format.py record-review <number> <skill> <verdict> [note]

`check-pr --gate` also requires what a merge needs: all three review verdicts for the PR's current
head commit, none BLOCKER or HIGH, and an acceptance run named in Evidence when code changed.
Exit 0 clean, 1 with one line per problem.
"""
import json
import re
import subprocess
import sys

TYPES = ("feat", "fix", "perf", "eval", "data", "test", "docs", "refactor", "chore", "build")
SUBJECT = re.compile(r"^(%s)(\([^()\n]{1,40}\))?: \S.*$" % "|".join(TYPES))
SUBJECT_MAX = 100
SECTIONS = ("What", "Why", "Evidence", "Provenance", "Review", "Not done")
SKILLS = ("pr-review", "simplify", "standards")
VERDICTS = ("CLEAN", "LOW", "MEDIUM", "HIGH", "BLOCKER")
BLOCKING = ("HIGH", "BLOCKER")
REVIEW_LINE = re.compile(r"^- (%s) @ ([0-9a-f]{7,40}): (%s)\b(.*)$" % ("|".join(SKILLS), "|".join(VERDICTS)), re.M)
CODE_PATHS = ("forge-overlay/", "eval/")
ACCEPTANCE = re.compile(r"harness_acceptance|acceptance", re.I)
TRAILER = re.compile(r"^(Co-Authored-By|Claude-Session|Signed-off-by): ", re.I)


def subject_problems(subject):
    out = []
    if subject.startswith(("Merge ", "Revert ")):
        return out
    if not SUBJECT.match(subject):
        out.append("subject is not `type(scope): what is now true` with type in %s: %r" % ("/".join(TYPES), subject))
    if len(subject) > SUBJECT_MAX:
        out.append("subject is %d characters (max %d)" % (len(subject), SUBJECT_MAX))
    if subject.endswith("."):
        out.append("subject ends with a period")
    return out


def commit_problems(message):
    lines = [l for l in message.splitlines() if not l.startswith("#")]
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return ["empty commit message"]
    out = subject_problems(lines[0])
    if lines[0].startswith("Merge "):
        return out
    if len(lines) > 1 and lines[1].strip():
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


def pr_problems(title, body, head=None, files=(), gate=False):
    out = ["title: " + p for p in subject_problems(title)]
    secs = sections(body)
    present = [s for s in secs if s in SECTIONS]
    for s in SECTIONS:
        if s not in secs:
            out.append("body has no `## %s` section" % s)
        elif not secs[s].strip() or re.fullmatch(r"\s*(TODO|TBD|\.\.\.)\s*", secs[s], re.I):
            out.append("`## %s` is empty" % s)
    if present != [s for s in SECTIONS if s in secs]:
        out.append("sections are out of order; expected %s" % ", ".join(SECTIONS))
    if not gate:
        return out
    got = reviews(body)
    for agent in SKILLS:
        if agent not in got:
            out.append("no `%s` verdict recorded in `## Review`" % agent)
            continue
        sha, verdict, _ = got[agent]
        if head and not head.startswith(sha) and not sha.startswith(head):
            out.append("%s reviewed %s but the PR head is %s; review the current head" % (agent, sha[:9], head[:9]))
        if verdict in BLOCKING:
            out.append("%s verdict is %s; fix it and review again" % (agent, verdict))
    if any(f.startswith(CODE_PATHS) for f in files) and not ACCEPTANCE.search(secs.get("Evidence", "")):
        out.append("code changed but `## Evidence` names no acceptance run")
    return out


def run(*cmd):
    return subprocess.run(cmd, check=True, stdout=subprocess.PIPE, universal_newlines=True).stdout


def base_ref(arg=None):
    return arg or "origin/main"


def branch_commits(base):
    out = run("git", "log", "--no-merges", "--format=%H%x1f%B%x1e", "%s..HEAD" % base)
    return [tuple(c.strip("\n").split("\x1f", 1)) for c in out.split("\x1e") if c.strip()]


def gh_pr(number):
    return json.loads(run("gh", "pr", "view", str(number), "--json", "title,body,headRefOid,files,number"))


def skeleton(base):
    commits = branch_commits(base)
    subjects = [m.splitlines()[0] for _, m in commits]
    title = subjects[-1] if len(subjects) == 1 else (subjects[0] if subjects else "type(scope): what is now true")
    head = run("git", "rev-parse", "--short=9", "HEAD").strip()
    body = ["## What", ""] + ["- " + s for s in reversed(subjects)] + [
        "", "## Why", "", "<the evidence it was needed: file:line, a run directory, a count>", "",
        "## Evidence", "", "- acceptance: `OUT=/tmp/<name>` <N>/<N> stages PASS, <J> Java, <P> Python",
        "- <measurement, next to its trivial baseline>", "",
        "## Provenance", "", "<which stamps moved, or: none>", "",
        "## Review", ""] + ["- %s @ %s: <CLEAN|LOW|MEDIUM|HIGH|BLOCKER> <one line>" % (a, head) for a in SKILLS] + [
        "", "## Not done", "", "<left out on purpose, and what would reopen it>", ""]
    return title, "\n".join(body)


def record_review(number, agent, verdict, note):
    if agent not in SKILLS or verdict not in VERDICTS:
        sys.exit("agent must be one of %s and verdict one of %s" % (", ".join(SKILLS), ", ".join(VERDICTS)))
    pr = gh_pr(number)
    line = "- %s @ %s: %s%s" % (agent, pr["headRefOid"][:9], verdict, (" " + note) if note else "")
    body = pr["body"] or ""
    secs = sections(body)
    if "Review" not in secs:
        sys.exit("PR #%s has no `## Review` section" % number)
    kept = [l for l in secs["Review"].splitlines() if not l.startswith("- %s @" % agent) and l.strip()]
    new_review = "\n".join(kept + [line]) + "\n\n"
    start = body.index("## Review")
    end = body.find("\n## ", start + 1)
    body = body[:start] + "## Review\n\n" + new_review + (body[end + 1:] if end >= 0 else "")
    subprocess.run(["gh", "pr", "edit", str(number), "--body", body], check=True, stdout=subprocess.DEVNULL)
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
        problems = pr_problems(pr["title"], pr["body"], pr["headRefOid"], [f["path"] for f in pr.get("files") or []], gate)
    elif cmd == "new-pr":
        title, body = skeleton(base_ref(args[0] if args else None))
        print(title + "\n\n" + body)
        return 0
    elif cmd == "record-review" and len(args) >= 3:
        record_review(args[0], args[1], args[2], " ".join(args[3:]))
        return 0
    else:
        print(__doc__)
        return 2
    for p in problems:
        print("FORMAT: " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
