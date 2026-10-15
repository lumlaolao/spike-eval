#!/usr/bin/env python3
"""Zero-model baselines, reference lines and verification for PUZ puzzles (eval/PUZZLES.md).

Every arm here is free: no model is called and nothing leaves the machine.

  random  uniform over the same legal menus the model sees, N rollouts on seeds base..base+N-1
  pass    the do-nothing option wherever one exists (PASS, decline, no attack/block), else the first
  first   the first option of every menu
  floor   the harness's own no-model fallback (all-in attack, no blocks, PASS) -- an anchor
  ai      Forge's AI in the solver seat (CONTROLLER_A=ai-log) -- the tiering anchor

  python3 eval/puzzle_baselines.py run    --suite S.json [--ids a,b] [--rollouts 200] [-j 4] --write S.json
  python3 eval/puzzle_baselines.py verify --suite S.json [-j 4]

`run` measures every arm, takes the shortest solving random rollout as the reference line when the
puzzle has none (searching further seeds with --search N if the baseline rollouts found none),
replays that line in a fresh JVM, and sets tier and status. A puzzle random solves more than 20% of
the time is dropped. `verify` replays every stored line and exits 1 if an active puzzle's line no
longer reaches the goal (an engine bump retires such a puzzle).

The Java side runs against an ISOLATED build: --build DIR holding classes/ and classpath.txt, as
eval/harness_acceptance.sh writes them under runs/harness-acceptance/<stamp>/ (the newest one is the
default). It never compiles and never touches the Forge checkout beyond reading it.
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import puzzle_format as pf  # noqa: E402

REPO = pf.REPO

ADD_OPENS = [
    "java.desktop/java.beans", "java.desktop/javax.swing.border", "java.desktop/javax.swing.event",
    "java.desktop/sun.swing", "java.desktop/java.awt.image", "java.desktop/java.awt.color",
    "java.desktop/sun.awt.image", "java.desktop/javax.swing", "java.desktop/java.awt",
    "java.desktop/java.awt.font", "java.base/java.util", "java.base/java.lang",
    "java.base/java.lang.reflect", "java.base/java.text", "java.base/jdk.internal.misc",
    "java.base/sun.nio.ch", "java.base/java.nio", "java.base/java.math",
    "java.base/java.util.concurrent", "java.base/java.net",
]

# Arm -> CONTROLLER_A. "ai" cannot sit in the solver seat (LlmSpikePuzzle.requireOverridableSolver);
# ai-log is the same Forge AI with logging.
ARM_CONTROLLER = {"random": "random", "pass": "pass", "first": "first", "floor": "floor", "ai": "ai-log"}
DEFAULT_SEEDS = {"pass": "1..4", "first": "1..4", "floor": "1..4", "ai": "1..4"}
SEARCH_BASE = 1_000_000


# ------------------------------------------------------------------ Java launcher

class Build:
    """An isolated compiled build: classes dir + classpath file + the Forge checkout to run from."""

    def __init__(self, build_dir=None, forge=None, java=None):
        self.dir = Path(build_dir) if build_dir else newest_acceptance_build()
        self.classes = self.dir / "classes"
        cp_file = self.dir / "classpath.txt"
        if not (self.classes / "forge/spike/LlmSpikePuzzle.class").is_file():
            raise SystemExit("no compiled overlay in %s (run eval/harness_acceptance.sh with "
                             "STAGES=\"classpath compile\" first)" % self.classes)
        if not cp_file.is_file():
            raise SystemExit("no classpath.txt in %s" % self.dir)
        self.classpath = cp_file.read_text().strip()
        self.forge = Path(forge or os.environ.get("FORGE_HOME") or Path.home() / "code/forge")
        if not (self.forge / "forge-gui-desktop").is_dir():
            raise SystemExit("Forge checkout not found at %s (set FORGE_HOME)" % self.forge)
        self.java = java or os.environ.get("JAVA", "java")

    def argfile(self, workdir):
        args = []
        for o in ADD_OPENS:
            args += ["--add-opens", o + "=ALL-UNNAMED"]
        args += ["-Xmx2048m", "-Dio.netty.tryReflectionSetAccessible=true", "-Dfile.encoding=UTF-8",
                 "-Djava.util.Arrays.useLegacyMergeSort=true", "-Dsun.java2d.d3d=false",
                 "-Dapple.awt.UIElement=true", "-Djava.awt.headless=true",
                 "-cp", "%s:%s" % (self.classes, self.classpath)]
        path = Path(workdir) / "java.args"
        path.write_text("\n".join(args) + "\n")
        return path

    def run(self, env_extra, out_dir, timeout_s=3600):
        """One JVM running LlmSpikeHeadless with env_extra; returns its exit code. Logs in out_dir."""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("OPENROUTER", "PZL_", "FORK_"))}
        # Zero-model by construction: no egress opt-in, no API key, and the model spec points at a
        # closed loopback port, so even a path that wrongly asked a model would fail at once and
        # send nothing anywhere (loopback needs no egress consent; see LLMBridgeFactory).
        env.update({"EVAL_OUT": str(out_dir), "SMART_MODEL": "openrouter:zero-model/none",
                    "FAST_MODEL": "openrouter:zero-model/none", "OPENROUTER_BASE_URL": "http://127.0.0.1:9/v1"})
        env.update({k: str(v) for k, v in env_extra.items()})
        argfile = self.argfile(out_dir)
        with open(out_dir / "jvm.log", "w") as log:
            try:
                return subprocess.call([self.java, "@%s" % argfile, "forge.spike.LlmSpikeHeadless"],
                                       cwd=str(self.forge / "forge-gui-desktop"), env=env,
                                       stdout=log, stderr=subprocess.STDOUT, timeout=timeout_s)
            except subprocess.TimeoutExpired:
                return -9


def newest_acceptance_build():
    root = REPO / "runs/harness-acceptance"
    cands = sorted(p for p in root.glob("*") if (p / "classes/forge/spike/LlmSpikePuzzle.class").is_file()
                   and (p / "classpath.txt").is_file()) if root.is_dir() else []
    if not cands:
        raise SystemExit("no isolated build under %s; pass --build DIR" % root)
    return cands[-1]


def read_results(out_dir):
    f = Path(out_dir) / "puzzle-results.jsonl"
    if not f.is_file():
        return []
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]


def run_arm(build, pzl_path, arm, seeds, out_dir, extra_env=None):
    """All rollouts of one arm on one puzzle, in one JVM. Returns the result rows."""
    env = {"PUZZLE": str(Path(pzl_path).resolve()), "CONTROLLER_A": ARM_CONTROLLER.get(arm, arm),
           "CONTROLLER_B": "ai", "PZL_SEEDS": seeds, "GAME_CAP_MS": "120000"}
    env.update(extra_env or {})
    rc = build.run(env, out_dir)
    rows = read_results(out_dir)
    if rc != 0 and not rows:
        raise RuntimeError("%s arm on %s: JVM exit %s and no rows (see %s/jvm.log)" % (arm, pzl_path, rc, out_dir))
    return rows


def seed_count(spec):
    n = 0
    for part in spec.split(","):
        part = part.strip()
        if ".." in part:
            a, b = part.split("..")
            n += int(b) - int(a) + 1
        elif part:
            n += 1
    return n


# ------------------------------------------------------------------ pure decisions

def summarize(rows, seeds_spec):
    """{solved, n, rate, seeds, invalid, tainted, unscoreable} for one arm's rows."""
    n = len(rows)
    solved = sum(1 for r in rows if r.get("status") == "solved" and not r.get("tainted_by_deckout"))
    return {
        "solved": solved, "n": n, "rate": (solved / n) if n else None, "seeds": seeds_spec,
        "invalid": sum(1 for r in rows if r.get("status") == "invalid"),
        "tainted": sum(1 for r in rows if r.get("tainted_by_deckout")),
        "unscoreable": sum(1 for r in rows if r.get("status") == "unscoreable"),
    }


def choose_line(rows):
    """The shortest solving rollout (fewest solver actions, then fewest draws, then lowest seed), or None."""
    good = [r for r in rows if r.get("status") == "solved" and not r.get("tainted_by_deckout") and r.get("tape")]
    if not good:
        return None
    best = min(good, key=lambda r: (r.get("solver_actions", 99), r.get("draws", 10 ** 9), r.get("seed", 0)))
    return {"arm": best.get("solver_arm", "random"), "seed": best["seed"], "tape": best["tape"],
            "actions": best.get("actions", []), "solver_actions": best.get("solver_actions"),
            "draws": best.get("draws"), "log_sha256": best.get("log_sha256")}


def check_replay(line, row):
    """Problems with a script-arm replay of `line` (empty = the line verifies)."""
    if row is None:
        return ["no replay row"]
    p = []
    if row.get("status") != "solved":
        p.append("replay status %s, not solved" % row.get("status"))
    if row.get("tainted_by_deckout"):
        p.append("replay solved by a deck-out")
    if row.get("script_drift"):
        p.append("tape drift: %s" % row["script_drift"])
    if row.get("script_exhausted"):
        p.append("tape exhausted before the game ended")
    if row.get("script_unconsumed"):
        p.append("%s draws of the tape unused (the game ended differently)" % row["script_unconsumed"])
    if line.get("log_sha256") and row.get("log_sha256") != line["log_sha256"]:
        p.append("replayed game log differs from the recorded one")
    return p


def tier_for(ai_rate, line_actions):
    """Tier 1: Forge's AI solves it (and random does not -- dropped puzzles never get here). Tier 2: the
    AI fails and the reference line takes <= 3 actions. Tier 3: the AI fails and it takes 4 or more."""
    if ai_rate is None or line_actions is None:
        return None
    if ai_rate >= 0.5:
        return 1
    return 2 if line_actions <= 3 else 3


def decide_status(rec):
    """(status, flags) from the record's baselines, line and verification."""
    flags = []
    base = rec.get("baselines", {})
    if any(b.get("unscoreable") for b in base.values()):
        flags.append("unscoreable")
    rnd = base.get("random", {})
    if rnd.get("rate") is not None and rnd["rate"] > pf.RANDOM_DROP_THRESHOLD:
        flags.append("random_above_threshold")
    if any(b.get("invalid") for b in base.values()):
        flags.append("capped_rollouts")
    if (rec.get("verification") or {}).get("status") != "verified":
        flags.append("no_verified_line")
    if "unscoreable" in flags or "random_above_threshold" in flags:
        return "dropped", flags
    if "no_verified_line" in flags or any(a not in base for a in pf.REQUIRED_BASELINES):
        return "candidate", flags
    return "active", flags


# ------------------------------------------------------------------ workflows

def baseline_puzzle(build, rec, out_root, rollouts, random_base, search, arms):
    pzl = REPO / rec["pzl"]
    pid = rec["id"]
    base = dict(rec.get("baselines") or {})
    random_rows = []
    for arm in arms:
        seeds = ("%d..%d" % (random_base, random_base + rollouts - 1)) if arm == "random" else DEFAULT_SEEDS[arm]
        rows = run_arm(build, pzl, arm, seeds, out_root / pid / arm)
        if arm == "random":
            random_rows = rows
        s = summarize(rows, seeds)
        base[arm] = {k: v for k, v in s.items() if v or k in ("solved", "n", "rate", "seeds")}
    rec = dict(rec, baselines=base)
    if not (rec.get("reference_line") or {}).get("tape"):
        line = choose_line(random_rows)
        if line is None and search > 0:
            seeds = "%d..%d" % (SEARCH_BASE, SEARCH_BASE + search - 1)
            line = choose_line(run_arm(build, pzl, "random", seeds, out_root / pid / "search"))
        if line is not None:
            rec["reference_line"] = line
    rec = verify_puzzle(build, rec, out_root)
    line = rec.get("reference_line") or {}
    ai = base.get("ai", {}).get("rate")
    rec["tier"] = tier_for(ai, line.get("solver_actions")) if rec["verification"]["status"] == "verified" else None
    rec["status"], rec["flags"] = decide_status(rec)
    return rec


def verify_puzzle(build, rec, out_root):
    line = rec.get("reference_line") or {}
    stamp = datetime.date.today().isoformat()
    if not line.get("tape"):
        return dict(rec, verification={"status": "unverified", "reason": "no reference line", "date": stamp})
    out = Path(out_root) / rec["id"] / "verify"
    tape = Path(tempfile.mkdtemp(prefix="pzl-tape-")) / "tape.txt"
    tape.write_text(line["tape"])
    rows = run_arm(build, REPO / rec["pzl"], "script", str(line["seed"]), out, {"PZL_TAPE": str(tape)})
    problems = check_replay(line, rows[0] if rows else None)
    engine = (REPO / "FORGE_COMMIT").read_text().strip() if (REPO / "FORGE_COMMIT").is_file() else "unknown"
    ver = {"status": "verified" if not problems else "failed", "engine_sha": engine, "date": stamp,
           "replay_log_sha256": rows[0].get("log_sha256") if rows else None}
    if problems:
        ver["problems"] = problems
    return dict(rec, verification=ver)


def cmd_run(a):
    build = Build(a.build, a.forge)
    manifest = pf.load_suite(a.suite)
    if manifest.get("frozen") and not a.write:
        raise SystemExit("suite is frozen; baselines change its hash. Write to a new manifest with --write")
    ids = set(a.ids.split(",")) if a.ids else None
    out_root = Path(a.out_dir or REPO / "runs/puzzles" / ("%s-baselines" % manifest["suite"]))
    arms = [x for x in a.arms.split(",") if x]
    todo = [r for r in manifest["puzzles"] if ids is None or r["id"] in ids]

    def work(rec):
        try:
            return baseline_puzzle(build, rec, out_root, a.rollouts, a.random_base, a.search, arms)
        except Exception as e:  # one broken puzzle must not lose the others' results
            return dict(rec, status="candidate", flags=sorted(set(rec.get("flags", []) + ["runner_error"])),
                        runner_error=str(e))

    with ThreadPoolExecutor(max(1, a.jobs)) as ex:
        done = {r["id"]: r for r in ex.map(work, todo)}
    manifest = dict(manifest, frozen=False, suite_sha256=None)
    manifest["puzzles"] = [done.get(r["id"], r) for r in manifest["puzzles"]]
    for r in manifest["puzzles"]:
        if r["id"] in done:
            b = r.get("baselines", {})
            print("%-24s %-9s tier=%s random=%s pass=%s first=%s floor=%s ai=%s line=%s %s" % (
                r["id"], r["status"], r.get("tier"),
                *[("%d/%d" % (b[x]["solved"], b[x]["n"])) if x in b else "-" for x in pf.BASELINE_ARMS],
                (r.get("reference_line") or {}).get("solver_actions"), ",".join(r.get("flags", []))))
    Path(a.write or a.suite).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return 0


def cmd_verify(a):
    build = Build(a.build, a.forge)
    manifest = pf.load_suite(a.suite)
    out_root = Path(a.out_dir or REPO / "runs/puzzles" / ("%s-verify" % manifest["suite"]))
    todo = [r for r in manifest["puzzles"] if (r.get("reference_line") or {}).get("tape")]
    with ThreadPoolExecutor(max(1, a.jobs)) as ex:
        res = list(ex.map(lambda r: verify_puzzle(build, r, out_root), todo))
    bad = 0
    for r in res:
        v = r["verification"]
        active = r.get("status") == "active"
        if v["status"] != "verified" and active:
            bad += 1
        print("%-24s %-7s %s %s" % (r["id"], r.get("status"), v["status"], "; ".join(v.get("problems", []))))
    print("%d line(s) replayed, %d active puzzle(s) failed" % (len(res), bad))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", default=None, help="dir with classes/ and classpath.txt (default: newest acceptance build)")
    ap.add_argument("--forge", default=None, help="Forge checkout (default $FORGE_HOME or ~/code/forge)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--suite", required=True)
    r.add_argument("--ids", default=None)
    r.add_argument("--rollouts", type=int, default=200)
    r.add_argument("--random-base", type=int, default=1)
    r.add_argument("--search", type=int, default=0, help="extra random seeds to search for a line")
    r.add_argument("--arms", default="random,pass,first,floor,ai")
    r.add_argument("-j", "--jobs", type=int, default=4)
    r.add_argument("--out-dir", default=None)
    r.add_argument("--write", default=None, help="manifest to write (default: --suite in place)")
    v = sub.add_parser("verify")
    v.add_argument("--suite", required=True)
    v.add_argument("-j", "--jobs", type=int, default=4)
    v.add_argument("--out-dir", default=None)
    a = ap.parse_args(argv)
    return cmd_run(a) if a.cmd == "run" else cmd_verify(a)


if __name__ == "__main__":
    sys.exit(main())
