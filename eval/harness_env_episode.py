#!/usr/bin/env python3
"""Runs one episode of the harness-building environment end to end (docs/harness/HARNESS-ENV-FRAMEWORK.md,
sections 2, 3 and 4): build the sandbox, hand it to an agent, wait for the agent to say it is finished, export what
it built, grade it outside the sandbox, write a scored report.

1. The sandbox is built from the episode spec (harness_env_sandbox.build) and checked for leaks; a sandbox that
   leaks is never handed to an agent (`ended_by: refused`).
2. It becomes a fresh git repository with one commit and no remote, hooks pointed at an empty directory.
3. The agent command runs with the sandbox as its working directory and a scrubbed environment (no provider key,
   no egress flag). The episode ends only when the agent writes DONE (`ended_by: agent`, its one line recorded);
   an agent that exits without it is `exit`; one still running at the cap is stopped (`cap`) and its last state
   graded. These are reported apart and never pooled.
4. Every checkpoint interval the overlay is copied outside the sandbox. Checkpoints are graded only after the
   episode ends (the dense reward between them), never while the agent can see a score.
5. The overlay is exported and graded by `grade_fn(submission, out_dir)`; the default builds the submission with the
   hidden grader and its Forge-AI counter (harness_env_grade.compile_command) and runs the instance grader on the
   seat its harness-env.properties names (GraderSuite), scoring by hook family (harness_env_grade.calibrated).

What it does not do: contain the agent. The runner is the episode's bookkeeping; the isolation (no network, the
read-only engine, a uid that cannot read the grader) is the container's (framework section 2), not this process's.

Usage: harness_env_episode.py <episode.json> <out> -- <agent command...>
       [--cap-s N] [--checkpoint-s N] [--grade-checkpoints] [--classpath <txt>] [--forge <checkout>] [--set <json>]
"""
import json
import os
import shutil
import subprocess
import sys
import time

import harness_env_grade as hgrade
import harness_env_sandbox as sb
import harness_env_spec as hs

KEEP_ENV = ("PATH", "LANG", "LC_ALL", "JAVA_HOME", "TZ")


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
    env = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
    home = os.path.join(out, "agent-home")
    os.makedirs(home, exist_ok=True)
    env["HOME"] = home
    return env


def snapshot(src, dest):
    if os.path.isdir(src):
        shutil.copytree(src, dest, symlinks=True)
    else:
        os.makedirs(dest)


def run_episode(spec_path, repo, agent_cmd, out, grade_fn=None, cap_s=None, checkpoint_s=None, poll_s=1.0,
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
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
    leaks = (leak_check or sb.leak_problems)(spec, repo, sandbox, manifest)
    if leaks:
        report.update(ended_by="refused", leaks=leaks)
        return _write(out, report)
    if dev_slice and dev_set:
        with open(dev_set, encoding="utf-8") as f:
            if json.load(f).get("split") != "dev":
                raise ValueError("a dev slice must be a dev split (harness_env_generate.py --split dev)")
        os.makedirs(os.path.join(sandbox, "dev"), exist_ok=True)
        shutil.copyfile(dev_set, os.path.join(sandbox, "dev", "instances.json"))
    fresh_repository(sandbox, os.path.join(out, "empty-hooks"))
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
    grade_fn = grade_fn or jvm_grader()
    report["score"] = grade_fn(submission, os.path.join(out, "grade"))
    if grade_checkpoints:
        for i, c in enumerate(checkpoints):
            c["score"] = grade_fn(c["path"], os.path.join(out, "grade-checkpoint-%03d" % i))
    report["checkpoints"] = checkpoints
    return _write(out, report)


def _write(out, report):
    with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, sort_keys=True, default=str)
    return report


def jvm_grader(classpath_file=None, forge=None, java_home=None, set_file=None):
    """The hidden grader as a grade_fn: intake, then (when the submission names a seat factory) build and run the
    instance grader on that seat, scored by hook family. A submission with no Java, or no factory, scores 0."""
    def grade(submission, out):
        os.makedirs(out, exist_ok=True)
        info = hgrade.intake(submission, forge)
        if info["java_files"] == 0 or not info["factory"]:
            return {"benchmark": 0.0, "dense": 0.0, "intake": info,
                    "reason": "no Java" if info["java_files"] == 0 else "no seat.factory in harness-env.properties"}
        jh = java_home or os.environ.get("JAVA_HOME", "/usr")
        aipatch, _ = hgrade.harness_env_aicount.write(forge, os.path.join(hgrade.REPO, hgrade.LEDGER), os.path.join(out, "aipatch"))
        classes = os.path.join(out, "classes")
        os.makedirs(classes, exist_ok=True)
        with open(os.path.join(out, "javac.log"), "w", encoding="utf-8") as log:
            rc = subprocess.run(hgrade.compile_command(os.path.join(jh, "bin", "javac"), classpath_file, submission, hgrade.REPO,
                                                       aipatch, classes), stdout=log, stderr=subprocess.STDOUT).returncode
        if rc != 0:
            return {"benchmark": 0.0, "dense": 0.0, "intake": info, "reason": "did not compile with the grader (javac rc=%d)" % rc}
        report = os.path.join(out, "env-grade.tsv")
        with open(os.path.join(out, "run.log"), "w", encoding="utf-8") as log:
            subprocess.run(hgrade.instances_command(os.path.join(jh, "bin", "java"), classes, classpath_file, hgrade.REPO, report,
                                                    seats="candidate=" + info["factory"], set_file=set_file),
                           cwd=os.path.join(forge, "forge-gui-desktop"), stdout=log, stderr=subprocess.STDOUT, timeout=hgrade.RUN_TIMEOUT_S)
        if not os.path.isfile(report):
            return {"benchmark": 0.0, "dense": 0.0, "intake": info, "reason": "the grader wrote no report"}
        rows = hgrade.read_env_report(report)
        scores = hgrade.env_scores(hgrade.env_table(rows, by="hook"), hgrade.ai_gated(rows))["candidate"]
        scores["instances"] = len(rows)
        scores["passed"] = sum(1 for r in rows if r.get("pass") == "1")
        scores["intake"] = info
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
    cpfile = forge = set_file = None
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
        else:
            print(__doc__)
            return 2
    report = run_episode(spec, hgrade.REPO, agent_cmd, out, grade_fn=jvm_grader(cpfile, forge, set_file=set_file), **opts)
    print(json.dumps({k: report.get(k) for k in ("episode", "ended_by", "wall_s", "done", "score")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
