#!/usr/bin/env python3
"""Runs one episode of the harness-building environment end to end (docs/harness/HARNESS-ENV-FRAMEWORK.md,
sections 2, 3 and 4): build the sandbox, hand it to an agent, wait for the agent to say it is finished, export what
it built, grade it outside the sandbox, write a scored report.

1. The sandbox is built from the episode spec (harness_env_sandbox.build) and checked for leaks; a sandbox that
   leaks is never handed to an agent (`ended_by: refused`). manifest.json, what was checked, is written either way.
2. It becomes a fresh git repository with one commit and no remote, hooks pointed at an empty directory.
3. The agent command runs with the sandbox as its working directory and a scrubbed environment (no provider key,
   no egress flag). The episode ends only when the agent writes DONE (`ended_by: agent`, its one line recorded);
   an agent that exits without it is `exit`; one still running at the cap is stopped (`cap`) and its last state
   graded. These are reported apart and never pooled.
4. Every checkpoint interval the overlay is copied outside the sandbox. Checkpoints are graded only after the
   episode ends (the dense reward between them), never while the agent can see a score.
5. The overlay is exported and graded by `grade_fn(submission, out_dir)`: from the command line, jvm_grader (its
   docstring says what it refuses and when it withholds a score).

What it does not do: contain the agent, or keep the candidate away from the grader. The runner is the episode's
bookkeeping; the isolation (no network, the read-only engine, a uid that cannot read the grader) is the container's
(framework section 2), not this process's. The candidate's seat runs inside the grader's JVM, where it could rewrite
the report (framework section 4.7, an open hole): without --trust-candidate a submission gets no score, only
`status: unscored`.

Usage: harness_env_episode.py <episode.json> <out> --classpath <classpath.txt> --forge <checkout>
       (--set <held-out set.json> | --cards <cardsfolder>) [--trust-candidate]
       [--cap-s N] [--checkpoint-s N] [--grade-checkpoints] -- <agent command...>
"""
import json
import os
import shutil
import subprocess
import sys
import time

import harness_env_aicount
import harness_env_generate as hgen
import harness_env_grade as hgrade
import harness_env_sandbox as sb
import harness_env_spec as hs

UNSCORED = "unscored: in-JVM grader, see HARNESS-ENV-FRAMEWORK.md 4.7 (pass --trust-candidate to accept the hole)"


def _git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="agent", GIT_AUTHOR_EMAIL="agent@harness-env", GIT_COMMITTER_NAME="agent",
               GIT_COMMITTER_EMAIL="agent@harness-env")
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def fresh_repository(sandbox, hooks_dir):
    os.makedirs(hooks_dir, exist_ok=True)
    _git(sandbox, "init", "-q")
    _git(sandbox, "config", "core.hooksPath", hooks_dir)
    _git(sandbox, "add", "-A")
    _git(sandbox, "commit", "-q", "--allow-empty", "-m", "initial task environment")


def agent_env(out):
    env = hgrade.grader_env()
    home = os.path.join(out, "agent-home")
    os.makedirs(home, exist_ok=True)
    env["HOME"] = home
    return env


def snapshot(src, dest):
    if os.path.isdir(src):
        shutil.copytree(src, dest, symlinks=True)
    else:
        os.makedirs(dest)


def run_episode(spec_path, repo, agent_cmd, out, grade_fn, cap_s=None, checkpoint_s=None, poll_s=1.0,
                grade_checkpoints=False, dev_set=None, dev_slice=None, leak_check=None):
    """Run one episode; return the report (also written to out/report.json)."""
    spec = hs.load(spec_path, repo)
    cap_s = cap_s if cap_s is not None else spec["caps"]["wall_hours"] * 3600
    checkpoint_s = checkpoint_s if checkpoint_s is not None else spec["caps"]["checkpoint_minutes"] * 60
    dev_slice = spec["grader"].get("dev_slice", False) if dev_slice is None else dev_slice
    os.makedirs(out, exist_ok=True)
    sandbox = os.path.join(out, "sandbox")
    report = {"episode": spec["id"], "agent": agent_cmd}
    manifest = sb.build(spec, repo, sandbox)
    _write_manifest(out, manifest)
    leaks = (leak_check or sb.leak_problems)(spec, repo, sandbox, manifest)
    if leaks:
        report.update(ended_by="refused", leaks=leaks)
        return _write(out, report)
    if dev_slice and dev_set:
        with open(dev_set, encoding="utf-8") as f:
            if json.load(f).get("split") != "dev":
                raise ValueError("a dev slice must be a dev split (harness_env_generate.py --split dev)")
        dest = os.path.join(sandbox, "dev", "instances.json")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(dev_set, dest)
        manifest["files"]["dev/instances.json"] = sb.sha256(dest)
    fresh_repository(sandbox, os.path.join(out, "empty-hooks"))
    _write_manifest(out, manifest)
    # Checked again as handed over: the dev slice and the repository were added after the first check.
    leaks = (leak_check or sb.leak_problems)(spec, repo, sandbox, manifest)
    if leaks:
        report.update(ended_by="refused", leaks=leaks)
        return _write(out, report)
    overlay = os.path.join(sandbox, spec["submission"]["path"])
    done_file = os.path.join(sandbox, spec["done"]["file"])
    started = time.time()
    checkpoints, next_cp = [], started + checkpoint_s
    with open(os.path.join(out, "agent.log"), "w", encoding="utf-8") as log:
        proc = subprocess.Popen(agent_cmd, cwd=sandbox, env=agent_env(out), stdout=log, stderr=subprocess.STDOUT)
        ended_by = None
        while ended_by is None:
            now = time.time()
            if os.path.exists(done_file):
                ended_by = "agent"
            elif proc.poll() is not None:
                ended_by = "agent" if os.path.exists(done_file) else "exit"
            elif now - started > cap_s:
                ended_by = "cap"
            elif now >= next_cp:
                dest = os.path.join(out, "checkpoints", "%03d" % len(checkpoints))
                snapshot(overlay, dest)
                checkpoints.append({"path": dest, "t": round(now - started, 3)})
                next_cp = now + checkpoint_s
            else:
                time.sleep(poll_s)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    ended_at = time.time()
    report.update(ended_by=ended_by, wall_s=round(ended_at - started, 3), ended_at=ended_at)
    if os.path.exists(done_file):
        with open(done_file, encoding="utf-8", errors="replace") as f:
            text = f.read().strip()
        report["done"] = text.splitlines()[0] if text else ""
    submission = os.path.join(out, "submission")
    snapshot(overlay, submission)
    report["score"] = grade_fn(submission, os.path.join(out, "grade"))
    if grade_checkpoints:
        for i, c in enumerate(checkpoints):
            c["score"] = grade_fn(c["path"], os.path.join(out, "grade-checkpoint-%03d" % i))
    report["checkpoints"] = checkpoints
    return _write(out, report)


def _write_manifest(out, manifest):
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)


def _write(out, report):
    with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, sort_keys=True, default=str)
    return report


def jvm_grader(classpath_file, forge, java_home=None, set_file=None, cards=None, trust_candidate=False):
    """The hidden grader as a grade_fn, on a generated held-out set: `set_file` (split "heldout"), or one drawn from
    `cards` (a cardsfolder) with $HARNESS_ENV_SEED when grading, after the agent has stopped. Never the committed
    hand catalog, which is in a seen copy of the repository (HARNESS-ENV-FRAMEWORK.md section 2).
    The classpath file, the engine checkout (a git checkout whose HEAD holds PlayerControllerAi.java, which the
    counter patches) and the set file are checked here, so a wrong one stops the episode before the agent starts.

    Intake refuses what scores 0 (harness_env_grade.refusal); the submission is built with the grader; then, only
    with `trust_candidate`, its seat is run in the grader's JVM and scored by hook family. Without it the result is
    `status: unscored` with no number: the seat shares the grader's JVM and could rewrite the report (framework
    section 4.7). javac and java get harness_env_grade.grader_env, never the operator's environment."""
    if not (classpath_file and os.path.isfile(classpath_file)):
        raise ValueError("grading needs --classpath <classpath.txt> (an acceptance run's): %s" % classpath_file)
    if not (forge and os.path.isdir(forge)):
        raise ValueError("grading needs --forge <the engine checkout>: %s" % forge)
    try:
        harness_env_aicount.engine_source(forge)
    except (OSError, subprocess.CalledProcessError) as e:
        # git's own reason, so a checkout git refuses for "dubious ownership" does not read as no checkout.
        said = ((e.stderr or "").strip() if isinstance(e, subprocess.CalledProcessError) else "") or str(e)
        raise ValueError("--forge must be a git checkout whose HEAD has %s: %s (git: %s)"
                         % (harness_env_aicount.ENGINE_PATH, forge, said)) from None
    seed = None
    if set_file:
        try:
            with open(set_file, encoding="utf-8") as f:
                loaded = json.load(f)
        except (OSError, ValueError) as e:
            raise ValueError("--set %s is not a readable JSON set: %s" % (set_file, e)) from None
        split = loaded.get("split") if isinstance(loaded, dict) else None
        if split != "heldout":
            raise ValueError("an episode is graded on a held-out set; %s is split %r" % (set_file, split))
    elif cards:
        seed = os.environ.get("HARNESS_ENV_SEED")
        if not seed:
            raise ValueError("--cards draws a held-out set and needs $HARNESS_ENV_SEED")
    else:
        raise ValueError("an episode is graded on a generated set: pass --set <json> or --cards <cardsfolder>")

    def grade(submission, out):
        os.makedirs(out, exist_ok=True)
        info = hgrade.intake(submission, forge)
        base = {"intake": info, "trust_candidate": trust_candidate}
        refused = hgrade.refusal(info)
        if refused:
            return dict(base, benchmark=0.0, dense=0.0, reason=refused)
        jh = java_home or os.environ.get("JAVA_HOME", "/usr")
        env = hgrade.grader_env(jh)
        rc, classes, _, missing = hgrade.build_classes(submission, classpath_file, forge, out,
                                                       os.path.join(jh, "bin", "javac"), env)
        if missing:
            return dict(base, benchmark=0.0, dense=0.0, reason="counter incomplete: " + ", ".join(missing))
        if rc != 0:
            return dict(base, benchmark=0.0, dense=0.0, reason="did not compile with the grader (javac rc=%d)" % rc)
        if not trust_candidate:
            return dict(base, benchmark=None, dense=None, status=UNSCORED)
        sf = set_file or os.path.join(out, "set.json")
        if not set_file:
            with open(sf, "w", encoding="utf-8") as f:
                json.dump(hgen.generate(hgen.index(cards), seed), f, indent=1, sort_keys=True)
        with open(sf, encoding="utf-8") as f:
            base["set_sha"] = json.load(f).get("set_sha")
        report = hgrade.run_instances(os.path.join(jh, "bin", "java"), classes, classpath_file, forge, out,
                                      seats="candidate=" + info["factory"], set_file=sf, env=env)
        if not os.path.isfile(report):
            return dict(base, benchmark=0.0, dense=0.0, reason="the grader wrote no report")
        rows = hgrade.read_env_report(report)
        if hgrade.counter_off(rows):
            return dict(base, benchmark=0.0, dense=0.0, reason="counter off: the Forge-AI counter was not loaded (ai=-1)")
        scores = hgrade.env_scores(hgrade.env_table(rows, by="hook"), hgrade.ai_gated(rows))["candidate"]
        scores.update(base, instances=len(rows), passed=sum(1 for r in rows if r.get("pass") == "1"))
        return scores
    return grade


def main(argv):
    if "--" not in argv:
        print(__doc__)
        return 2
    cut = argv.index("--")
    head, agent_cmd = argv[1:cut], argv[cut + 1:]
    if len(head) < 2 or not agent_cmd:
        print(__doc__)
        return 2
    spec, out, rest = head[0], head[1], head[2:]
    opts = {"cap_s": None, "checkpoint_s": None, "grade_checkpoints": False}
    cpfile = forge = set_file = cards = None
    trust = False
    while rest:
        flag = rest.pop(0)
        if flag == "--cap-s":
            opts["cap_s"] = float(rest.pop(0))
        elif flag == "--checkpoint-s":
            opts["checkpoint_s"] = float(rest.pop(0))
        elif flag == "--grade-checkpoints":
            opts["grade_checkpoints"] = True
        elif flag == "--classpath":
            cpfile = rest.pop(0)
        elif flag == "--forge":
            forge = rest.pop(0)
        elif flag == "--set":
            set_file = rest.pop(0)
        elif flag == "--cards":
            cards = rest.pop(0)
        elif flag == "--trust-candidate":
            trust = True
        else:
            print(__doc__)
            return 2
    try:
        grade_fn = jvm_grader(cpfile, forge, set_file=set_file, cards=cards, trust_candidate=trust)
    except ValueError as e:
        print(e)
        return 2
    report = run_episode(spec, hgrade.REPO, agent_cmd, out, grade_fn=grade_fn, **opts)
    print(json.dumps({k: report.get(k) for k in ("episode", "ended_by", "wall_s", "done", "score")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
