#!/usr/bin/env python3
"""Zero-model baselines, reference lines and verification for PUZ puzzles (docs/eval/PUZZLES.md).

Every arm here is free: no model is called and nothing leaves the machine.

  random  uniform over the same legal menus the model sees, N rollouts on seeds base..base+N-1
  pass    the do-nothing option wherever one exists (PASS, decline, no attack/block), else the first
  first   the first option of every menu
  floor   the harness's own no-model fallback (all-in attack, no blocks, PASS) -- an anchor
  greedy  the model seat with no model, previewing each play and taking the one that lowers the opponent's life
          most (GreedyBridge): the one-move-deep floor a hard puzzle must stay above
  ai      Forge's AI in the solver seat (CONTROLLER_A=ai-log) -- the tiering anchor

  python3 eval/puzzles/puzzle_baselines.py run    --suite S.json [--ids a,b] [--rollouts 200] [-j 4] --write S.json
  python3 eval/puzzles/puzzle_baselines.py verify --suite S.json [-j 4]
  python3 eval/puzzles/puzzle_baselines.py prove  --suite S.json [--ids a,b] [--budget 2000] [-j 4] --write S.json
  python3 eval/puzzles/puzzle_baselines.py line   --pzl P.pzl [--script L.txt] [--seeds 1..3] [--opponent search]
                                          [--minimality]

`run` measures every arm, takes the shortest solving random rollout as the reference line when the
puzzle has none (searching further seeds with --search N if the baseline rollouts found none),
replays that line in a fresh JVM, and sets tier and status. A puzzle random solves more than 20% of
the time is dropped. `verify` replays every stored line and exits 1 if an active puzzle's line no
longer reaches the goal (an engine bump retires such a puzzle).

`prove` runs the search prover (CONTROLLER_A=search, PuzzleSearch.java; docs/eval/BENCHMARK-V1.md, phase 2) and
stores its result as the record's `proof`: "proven" means a strategy that beats every opponent choice the
engine offers (with --opponent ai, only the Forge AI's), every leaf a Forge-scored solve. "not_found" is a
spent budget, never a claim that the puzzle is unsolvable.

`line` plays a puzzle's written solution (its `[solution]` section, one line per decision in the words the seat is
shown; ScriptBridge.java) through the model seat, and exits 0 only when every seed played the whole line, drifted
nowhere and solved. That proves a line a person can write, however long, without a search finding it. With
`--opponent search` the prover plays the line against every reply the engine offers (exit 0 when proven). With
`--minimality` each action is removed in turn (a relaxed replay, `script:relaxed:<file>`); exit 0 only when every
removal is a game played out and lost. A drift, the cap, a deck-out or a stalled line is inconclusive, never
"necessary": it cannot show an action is needed, only fail to.

The Java side runs against an ISOLATED build: --build DIR holding classes/ and classpath.txt, as
eval/harness_acceptance.sh writes them under runs/harness-acceptance/<stamp>/ with overlay-sha.txt, the hash of the
overlay sources it compiled. A build whose hash is not this checkout's is refused unless --allow-stale-build (recorded
in each run directory's build.json); with no --build, the newest matching build by compile time is used. It never
compiles and never touches the Forge checkout beyond reading it.
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)
import benchmark_version  # noqa: E402
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
ARM_CONTROLLER = {"random": "random", "pass": "pass", "first": "first", "floor": "floor", "ai": "ai-log",
                  "greedy": "llm"}
GREEDY_SPEC = "greedy:"  # GreedyBridge.PREFIX
DEFAULT_SEEDS = {"pass": "1..4", "first": "1..4", "floor": "1..4", "ai": "1..4", "greedy": "1..4"}
SEARCH_BASE = 1_000_000
ACCEPTANCE_ROOT = REPO / "runs/harness-acceptance"
# What a build was compiled from: benchmark_version.overlay_sha_of_checkout at compile time, the hash run_batch.py
# stamps as a game line's overlay_sha (eval/harness_acceptance.sh, compile stage).
BUILD_SHA_FILE = "overlay-sha.txt"
BUILD_HOW = 'STAGES="classpath compile" FORGE_HOME=<forge> bash eval/harness_acceptance.sh'


def checkout_overlay_sha():
    return benchmark_version.overlay_sha_of_checkout(REPO)


def recorded_overlay_sha(build_dir):
    """The overlay hash a build recorded when it was compiled, or None (a build from before it recorded one)."""
    try:
        return (Path(build_dir) / BUILD_SHA_FILE).read_text().strip() or None
    except OSError:
        return None


# ------------------------------------------------------------------ Java launcher

class Build:
    """An isolated compiled build: classes dir + classpath file + the Forge checkout to run from."""

    def __init__(self, build_dir=None, forge=None, java=None, allow_stale=False):
        """Only a build compiled from this checkout's overlay runs: --build DIR is refused when its recorded hash is
        missing or another, unless allow_stale (--allow-stale-build, recorded in provenance()); with no DIR, the
        newest matching build under ACCEPTANCE_ROOT, by when it was compiled."""
        self.checkout_sha = checkout_overlay_sha()
        # Absolute: the JVM runs from the Forge checkout, where a relative classes path would miss and
        # the stale classes installed there would load in its place.
        self.dir = (Path(build_dir) if build_dir else newest_acceptance_build(self.checkout_sha)).resolve()
        self.classes = self.dir / "classes"
        cp_file = self.dir / "classpath.txt"
        if not (self.classes / "forge/spike/LlmSpikePuzzle.class").is_file():
            raise SystemExit("no compiled overlay in %s (run eval/harness_acceptance.sh with "
                             "STAGES=\"classpath compile\" first)" % self.classes)
        if not cp_file.is_file():
            raise SystemExit("no classpath.txt in %s" % self.dir)
        recorded = recorded_overlay_sha(self.dir)
        self.overlay_sha = recorded or "unknown"
        self.stale = recorded != self.checkout_sha or self.checkout_sha == "unknown"
        if self.stale and not allow_stale:
            raise SystemExit("build %s was compiled from overlay %s, this checkout's is %s: it would run another "
                             "harness under this checkout's name. Build one (%s) or pass --allow-stale-build"
                             % (self.dir, recorded or "(not recorded)", self.checkout_sha, BUILD_HOW))
        self.classpath = cp_file.read_text().strip()
        self.forge = Path(forge or os.environ.get("FORGE_HOME") or Path.home() / "code/forge")
        if not (self.forge / "forge-gui-desktop").is_dir():
            raise SystemExit("Forge checkout not found at %s (set FORGE_HOME)" % self.forge)
        self.java = java or os.environ.get("JAVA", "java")

    def provenance(self):
        """Which build ran, what it was compiled from, and whether --allow-stale-build let a mismatch run."""
        return {"build_dir": str(self.dir), "build_overlay_sha": self.overlay_sha,
                "checkout_overlay_sha": self.checkout_sha, "stale_build_allowed": self.stale}

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

    def run(self, env_extra, out_dir, timeout_s=3600, keep_model_env=False):
        """One JVM running LlmSpikeHeadless with env_extra; returns its exit code. Logs in out_dir.
        Zero-model unless keep_model_env (puzzle_solve.py, which passes the model and key in env_extra)."""
        out_dir = Path(out_dir).resolve()  # the JVM runs from the Forge checkout
        out_dir.mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("OPENROUTER", "PZL_", "FORK_"))}
        env.pop("PROPOSE_ACTIONS", None)   # a condition: only puzzle_solve.py --propose-actions sets it
        env.pop("PROPOSE_AUDIT", None)     # a diagnostic that is a condition (handles): never inherited by a puzzle
        env["EVAL_OUT"] = str(out_dir)
        if not keep_model_env:
            # Zero-model by construction: no egress opt-in, no API key, and the model spec points at a
            # closed loopback port, so even a path that wrongly asked a model would fail at once and
            # send nothing anywhere (loopback needs no egress consent; see LLMBridgeFactory).
            env.update({"SMART_MODEL": "openrouter:zero-model/none", "FAST_MODEL": "openrouter:zero-model/none",
                        "OPENROUTER_BASE_URL": "http://127.0.0.1:9/v1"})
        env.update({k: str(v) for k, v in env_extra.items()})
        (out_dir / "build.json").write_text(json.dumps(self.provenance()) + "\n")
        argfile = self.argfile(out_dir)
        with open(out_dir / "jvm.log", "w") as log:
            try:
                return subprocess.call([self.java, "@%s" % argfile, "forge.spike.LlmSpikeHeadless"],
                                       cwd=str(self.forge / "forge-gui-desktop"), env=env,
                                       stdout=log, stderr=subprocess.STDOUT, timeout=timeout_s)
            except subprocess.TimeoutExpired:
                return -9


def newest_acceptance_build(want):
    """The most recently compiled complete build under ACCEPTANCE_ROOT whose recorded overlay hash is `want`.
    By compile time, never by name: the last name was an old ps-* build (puzzle-build-staleness)."""
    cands = [] if want == "unknown" or not ACCEPTANCE_ROOT.is_dir() else [
        p for p in ACCEPTANCE_ROOT.glob("*") if (p / "classes/forge/spike/LlmSpikePuzzle.class").is_file()
        and (p / "classpath.txt").is_file() and recorded_overlay_sha(p) == want]
    if not cands:
        raise SystemExit("no build under %s compiled from this checkout's overlay (%s); build one: OUT=%s/<name> %s"
                         % (ACCEPTANCE_ROOT, want, ACCEPTANCE_ROOT, BUILD_HOW))
    return max(cands, key=lambda p: (p / BUILD_SHA_FILE).stat().st_mtime)


def ran_on(out_dir, build):
    """True when every Build.run under out_dir (its build.json) ran this build's compiled overlay, and one did: a
    resume reuses rows only then. A build with no recorded hash is never known to be the one that ran."""
    runs = list(Path(out_dir).rglob("build.json"))
    if not runs or build.overlay_sha == "unknown":
        return False
    try:
        return all(json.loads(p.read_text()).get("build_overlay_sha") == build.overlay_sha for p in runs)
    except ValueError:
        return False


def read_results(out_dir):
    f = Path(out_dir) / "puzzle-results.jsonl"
    if not f.is_file():
        return []
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]


RESUME = False


def reusable(build, out_dir, seeds):
    """The rows an earlier run left in out_dir, when the same build (its classes dir and the overlay compiled into
    it) ran exactly these seeds; else None."""
    args = Path(out_dir) / "java.args"
    rows = read_results(out_dir)
    if not args.is_file() or not any(l.startswith(str(build.classes) + ":")
                                     for l in args.read_text().splitlines()) or not ran_on(out_dir, build):
        return None
    want = sorted(expand_seeds(seeds))
    return rows if sorted(r.get("seed") for r in rows) == want else None


def run_arm(build, pzl_path, arm, seeds, out_dir, extra_env=None):
    """All rollouts of one arm on one puzzle, in one JVM. Returns the result rows.

    With RESUME (run --resume), rows the same build already wrote for exactly these seeds are reused."""
    if RESUME and not extra_env:
        rows = reusable(build, out_dir, seeds)
        if rows is not None:
            return rows
    env = {"PUZZLE": str(Path(pzl_path).resolve()), "CONTROLLER_A": ARM_CONTROLLER.get(arm, arm),
           "CONTROLLER_B": "ai", "PZL_SEEDS": seeds, "GAME_CAP_MS": "120000"}
    if arm == "greedy":
        env.update(greedy_env())
    env.update(extra_env or {})
    # LlmSpikePuzzle appends its rows: without this a re-run read the last run's rows (another build, say).
    (Path(out_dir) / "puzzle-results.jsonl").unlink(missing_ok=True)
    rc = build.run(env, out_dir)
    rows = read_results(out_dir)
    if rc != 0 and not rows:
        raise RuntimeError("%s arm on %s: JVM exit %s and no rows (see %s/jvm.log)" % (arm, pzl_path, rc, out_dir))
    return rows


def expand_seeds(spec):
    out = []
    for part in spec.split(","):
        part = part.strip()
        if ".." in part:
            a, b = part.split("..")
            out += range(int(a), int(b) + 1)
        elif part:
            out.append(int(part))
    return out


def seed_count(spec):
    return len(expand_seeds(spec))


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


def choose_ai_line(rows):
    """A solving rollout of Forge's AI in the solver seat, when no random rollout solved. The AI's line is
    no tape (its choices are not draws), so it is replayed by seed and must reproduce the same game log:
    the proof that this position is solvable in this engine, for a puzzle random never solves."""
    good = [r for r in rows if r.get("status") == "solved" and not r.get("tainted_by_deckout")]
    if not good:
        return None
    best = min(good, key=lambda r: (r.get("solver_actions", 99), r.get("seed", 0)))
    return {"arm": "ai", "seed": best["seed"], "solver_actions": best.get("solver_actions"),
            "actions": best.get("actions", []), "log_sha256": best.get("log_sha256")}


SEARCH_LINE_BUDGET = 600   # the rules-pilot gate's budget (scripts/puzzle-gate/gate10.sh)


def search_env(budget):
    """A proof against every opponent reply, with no uniqueness search (that is the gate's, not the line's)."""
    return {"CONTROLLER_B": "search", "PZL_SEARCH_BUDGET": str(budget), "PZL_UNIQUE_BUDGET": "0",
            "GAME_CAP_MS": "60000"}


def choose_search_line(row):
    """A rules puzzle's reference line from a search row: proven against every reply, its acceptance state held."""
    if not row or row.get("status") != "proven" or row.get("acceptance") != "accepted" or not row.get("line"):
        return None
    return {"arm": "search", "seed": row.get("seed", 1), "budget": row.get("search_budget", SEARCH_LINE_BUDGET),
            "line": row["line"], "solver_actions": row.get("solver_actions")}


def check_search_replay(line, row):
    """Problems with proving a search line again (empty = it verifies): proven and accepted. The line may differ: a
    rollout capped under load steers the search elsewhere, and the puzzle is proven all the same."""
    if row is None:
        return ["no replay row"]
    p = []
    if row.get("status") != "proven":
        p.append("replay status %s, not proven" % row.get("status"))
    if row.get("acceptance") != "accepted":
        p.append("acceptance %s" % row.get("acceptance"))
    return p


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


def check_written_replay(line, row):
    """Problems with a replay of a board's written [solution] against Forge's AI (empty = the line verifies)."""
    verdict = line_verdict(row)
    return [] if verdict == "played" else ["written line: " + verdict]


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

def baseline_puzzle(build, rec, out_root, rollouts, random_base, search, arms, baselines_only=False):
    pzl = pf.pzl_path(rec)
    pid = rec["id"]
    base = dict(rec.get("baselines") or {})
    random_rows, ai_rows = [], []
    for arm in arms:
        seeds = ("%d..%d" % (random_base, random_base + rollouts - 1)) if arm == "random" else DEFAULT_SEEDS[arm]
        rows = run_arm(build, pzl, arm, seeds, out_root / pid / arm)
        if arm == "random":
            random_rows = rows
        elif arm == "ai":
            ai_rows = rows
        s = summarize(rows, seeds)
        base[arm] = {k: v for k, v in s.items() if v or k in ("solved", "n", "rate", "seeds")}
        if arm == "greedy":
            base[arm]["condition"] = greedy_condition()
    rec = dict(rec, baselines=base)
    if not baselines_only:  # a suite already proven (re-tiering) measures the floors and the AI's rate only
        if not pf.has_line(rec.get("reference_line")):
            line = choose_line(random_rows)
            if line is None and search > 0:
                seeds = "%d..%d" % (SEARCH_BASE, SEARCH_BASE + search - 1)
                line = choose_line(run_arm(build, pzl, "random", seeds, out_root / pid / "search"))
            if line is None:
                line = choose_ai_line(ai_rows)
            if line is None and (rec.get("source") or {}).get("kind") == "rules":
                rows = run_arm(build, pzl, "search", "1", out_root / pid / "prove", search_env(SEARCH_LINE_BUDGET))
                line = choose_search_line(rows[-1] if rows else None)
            if line is not None:
                rec["reference_line"] = line
        rec = verify_puzzle(build, rec, out_root)
    # The v1 tier and the status follow the new rates on both paths (pr-review of #280: --baselines-only kept a
    # status its new random rate contradicted).
    verified = (rec.get("verification") or {}).get("status") == "verified"
    rec["tier"] = tier_for(base.get("ai", {}).get("rate"),
                           (rec.get("reference_line") or {}).get("solver_actions")) if verified else None
    rec["status"], rec["flags"] = decide_status(rec)
    return apply_difficulty(rec)


def proven_pzl_text(rec):
    """The record's pzl text, or None when it cannot be read or is not the file its pzl_sha256 names: FORGE_HOME at
    another checkout would measure a record from nothing, or from a different file of the same name."""
    path = pf.pzl_path(rec)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return None if rec.get("pzl_sha256") and pf.sha256_file(path) != rec["pzl_sha256"] else text


def refuse_unproven(puzzles, what):
    """Stop before any game when proven_pzl_text refuses a record: the games would measure, and the manifest
    record, another file (pr-review of #281)."""
    bad = [r["id"] for r in puzzles if proven_pzl_text(r) is None]
    if bad:
        raise SystemExit("%d pzl file(s) unreadable or not the one recorded, nothing %s: %s"
                         % (len(bad), what, ", ".join(bad[:5])))


def apply_difficulty(rec):
    """The record's measured difficulty, its v2 tier and its skill tags (BENCHMARK-V2.md section 4), recomputed from
    its proof and its Forge AI rate; all three null without a proven proof or the recorded pzl (an empty board would
    read as board_objects=0, and an old tier would stand beside a new proof). Additive: `tier` (v1) is untouched."""
    proof = rec.get("proof") or {}
    text = proven_pzl_text(rec)
    if text is None:
        print("%s: pzl unreadable or not the recorded one (%s); no difficulty" % (rec.get("id"), pf.pzl_path(rec)),
              file=sys.stderr)
        return dict(rec, difficulty=None, tier_v2=None, skills=None)
    d = pf.difficulty(proof, text)
    ai = ((rec.get("baselines") or {}).get("ai") or {}).get("rate")
    return dict(rec, difficulty=d, tier_v2=pf.tier_v2(d, ai),
                skills=pf.skill_tags(d, proof.get("actions"), (rec.get("source") or {}).get("kind")) if d else None)


def verify_puzzle(build, rec, out_root):
    line = rec.get("reference_line") or {}
    stamp = datetime.date.today().isoformat()
    if not pf.has_line(line):
        return dict(rec, verification={"status": "unverified", "reason": "no reference line", "date": stamp})
    out = Path(out_root) / rec["id"] / "verify"
    check = check_replay
    if line.get("arm") == "ai":
        rows = run_arm(build, pf.pzl_path(rec), "ai", str(line["seed"]), out)
    elif line.get("arm") == "search":
        rows = run_arm(build, pf.pzl_path(rec), "search", str(line["seed"]), out, search_env(line["budget"]))
        check = check_search_replay
    elif line.get("arm") == "written":
        rows = run_arm(build, pf.pzl_path(rec), "written", str(line["seed"]), out, line_env(pf.pzl_path(rec), "ai"))
        check = check_written_replay
    else:
        tape = Path(tempfile.mkdtemp(prefix="pzl-tape-")) / "tape.txt"
        tape.write_text(line["tape"])
        rows = run_arm(build, pf.pzl_path(rec), "script", str(line["seed"]), out, {"PZL_TAPE": str(tape)})
    problems = check(line, rows[0] if rows else None)
    engine = (REPO / "FORGE_COMMIT").read_text().strip() if (REPO / "FORGE_COMMIT").is_file() else "unknown"
    ver = {"status": "verified" if not problems else "failed", "engine_sha": engine, "date": stamp,
           "replay_log_sha256": rows[0].get("log_sha256") if rows else None}
    if problems:
        ver["problems"] = problems
    return dict(rec, verification=ver)


def proof_from_row(row, opponent, engine_sha, date):
    """The record's `proof` from the search runner's one result row (None when the run wrote none)."""
    if not row:
        return None
    keep = ("search_replays", "search_budget", "search_lines_per_point", "opponent_choice_points",
            "opponent_branches_beaten", "search_drift", "line", "solver_actions", "actions", "log_sha256",
            "duration_ms", "seed", "refutation_line", "refutation_opponent_log", "search_exhausted",
            "acceptance", "acceptance_failed", "turns")
    proof = {"status": row.get("status"), "opponent": opponent, "engine_sha": engine_sha, "date": date}
    proof.update({k: row[k] for k in row
                  if (k in keep or k.startswith("uniqueness")) and row[k] not in (None, "")})
    return proof


def prove_puzzle(build, rec, out_root, budget, opponent, seed=1, unique_budget=None):
    out = Path(out_root) / rec["id"] / ("prove-%s" % opponent)
    env = {"PUZZLE": str(pf.pzl_path(rec).resolve()), "CONTROLLER_A": "search", "CONTROLLER_B": opponent,
           "PZL_SEEDS": str(seed), "GAME_CAP_MS": "60000", "PZL_SEARCH_BUDGET": str(budget)}
    if unique_budget is not None:
        env["PZL_UNIQUE_BUDGET"] = str(unique_budget)
    (out / "puzzle-results.jsonl").unlink(missing_ok=True)
    build.run(env, out, timeout_s=max(3600, budget * 10))
    rows = read_results(out)
    engine = (REPO / "FORGE_COMMIT").read_text().strip() if (REPO / "FORGE_COMMIT").is_file() else "unknown"
    proof = proof_from_row(rows[-1] if rows else None, opponent, engine, datetime.date.today().isoformat())
    return apply_difficulty(dict(rec, proof=proof or {"status": "not_found", "opponent": opponent,
                                                      "error": "no result row"}))


def cmd_prove(a):
    manifest = pf.load_suite(a.suite)
    if manifest.get("frozen") and not a.write:
        raise SystemExit("suite is frozen; a proof changes its records. Write to a new manifest with --write")
    ids = set(a.ids.split(",")) if a.ids else None
    out_root = Path(a.out_dir or REPO / "runs/puzzles" / ("%s-proofs" % manifest["suite"]))
    todo = [r for r in manifest["puzzles"] if ids is None or r["id"] in ids]
    refuse_unproven(todo, "played")
    build = Build(a.build, a.forge, allow_stale=a.allow_stale_build)
    manifest = dict(manifest, frozen=False, suite_sha256=None)
    target = Path(a.write or a.suite)
    done = {}
    with ThreadPoolExecutor(max(1, a.jobs)) as ex:
        futs = [ex.submit(prove_puzzle, build, r, out_root, a.budget, a.opponent, 1, a.unique_budget) for r in todo]
        for f in as_completed(futs):
            r = f.result()
            done[r["id"]] = r
            m = dict(manifest, puzzles=[done.get(x["id"], x) for x in manifest["puzzles"]])
            target.write_text(json.dumps(m, indent=2) + "\n", encoding="utf-8")
            p = r["proof"]
            print("%-24s %-10s rollouts=%s opponent_points=%s replies_beaten=%s actions=%s tier_v2=%s" % (
                r["id"], p.get("status"), p.get("search_replays"), p.get("opponent_choice_points"),
                p.get("opponent_branches_beaten"), p.get("solver_actions"), r.get("tier_v2")), flush=True)
    return 0


def greedy_condition():
    """What a greedy rate was measured under: the arm reads the seat's prompt (the STACK line, previews), so its rate
    moves with the interface. The pinned env and the source's stamps, stored beside the rate; a floor and a model run
    compare only within one harness epoch (pr-review/standards of #279)."""
    import versions  # eval/versions.py: the stamps, read from the overlay source
    stamps = {field: value for _, value, field, _ in versions.read_stamps()}
    return dict({f: stamps.get(f) for f in ("harness_epoch", "observation_version", "lookahead_version")}, env=greedy_env())


def greedy_env():
    """The greedy-preview floor (GreedyBridge): the model seat with no model, its own `simulate` previews on
    (LOOKAHEAD=1), every other setting as a paid run pins it with the tools off."""
    import puzzle_solve  # here: puzzle_solve imports this module
    cond = puzzle_solve.condition(argparse.Namespace(context_profile="v4-faithful", memory_tools=False, rules_tool=False))
    return dict(cond, SMART_MODEL_A=GREEDY_SPEC, FAST_MODEL_A=GREEDY_SPEC, LOOKAHEAD="1")


def line_verdict(row):
    """"played" when a written-line game played every line, drifted nowhere and solved cleanly; else why not.
    A drift is checked first: after one, the do-nothing answers can still stumble into the acceptance state."""
    if not row:
        return "no result row"
    if row.get("opponent_arm") == "search":
        # The prover's row (LlmSpikePuzzle.search): proven means the one line beat every reply the engine offers. The
        # line may win before its end under the main line's replies (LlmSpikePuzzle.wonAsWritten): lines unplayed are
        # allowed there, a drift or a play that did not happen is not.
        if row.get("status") == "proven" and "script_played" in row and not row.get("script_drift") \
                and not row.get("plays_not_made"):
            return "proven"
        if row.get("refutation_line"):
            how = row.get("refutation_status") or "lost"
            if row.get("refutation_script_drift"):
                how = "drift: " + row["refutation_script_drift"]
            elif row.get("refutation_plays_not_made"):
                how = "%d plays the line chose did not happen" % row["refutation_plays_not_made"]
            return "beaten by %s (%s)" % (row["refutation_line"], how)
        if row.get("script_drift"):
            return "drift: " + row["script_drift"]
        if row.get("plays_not_made"):
            return "%d plays the line chose did not happen" % row["plays_not_made"]
        if row.get("status") == "proven":
            return "proven, but no written-line fields on the row"
        return str(row.get("status"))
    if "script_played" not in row:
        return "not a written-line row"
    if row.get("script_drift"):
        return "drift: " + row["script_drift"]
    if row.get("script_unconsumed"):
        return "%d lines unplayed" % row["script_unconsumed"]
    if row.get("tainted_by_deckout"):
        return "solved by a deck-out" if row.get("status") == "solved" else "decided by a deck-out"
    if row.get("status") != "solved":
        return "%s %s" % (row.get("status"), row.get("acceptance_failed") or "")
    # A play the line chose can still not happen (unpayable, or forbidden by a static): the seat counts those.
    if "plays_not_made" not in row:
        return "no plays_not_made on the row (a build before the seat counted them)"
    if row.get("plays_not_made"):
        return "%d plays the line chose did not happen" % row["plays_not_made"]
    return "played" if row.get("script_played") else "not played"


def line_env(script, opponent, budget=200, relaxed=False):
    """The model seat with the written line `script` as its model (ScriptBridge), under the condition a paid run
    pins with the tools off (puzzle_solve.condition): the line plays the menus the model is shown. Against the
    search opponent the prover tries every reply on the line, within `budget` rollouts. `relaxed`: a minimality
    replay ("script:relaxed:<file>"), where a decision the line does not fit takes the do-nothing answer."""
    import puzzle_solve  # here: puzzle_solve imports this module
    cond = puzzle_solve.condition(argparse.Namespace(context_profile="v4-faithful", memory_tools=False, rules_tool=False))
    spec = "script:" + ("relaxed:" if relaxed else "") + str(Path(script).resolve())
    env = dict(cond, CONTROLLER_A="llm", CONTROLLER_B=opponent, SMART_MODEL_A=spec, FAST_MODEL_A=spec,
               GAME_CAP_MS="300000", ONE_TIER="1")
    if opponent == "search":
        env["PZL_SEARCH_BUDGET"] = str(budget)
    return env


_JAVA_TRIM = "".join(map(chr, range(33)))  # what String.trim() strips: every character up to U+0020


def script_lines(path):
    """The lines ScriptBridge.fromFile plays: the [solution] section when the file has sections, else the whole file;
    blank and # lines skipped. Split and trimmed as Java's readAllLines and trim() do (only \\n and \\r; only
    characters up to a space), so the indices match the row's script_actions."""
    text = Path(path).read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    raw = [l.strip(_JAVA_TRIM) for l in text.split("\n")]
    header = lambda l: l.startswith("[") and l.endswith("]") and len(l) > 2  # noqa: E731
    sectioned = any(header(l) for l in raw)
    out, inside = [], not sectioned
    for l in raw:
        if header(l):
            inside = l.lower() == "[solution]"
        elif inside and l and not l.startswith("#"):
            out.append(l)
    return out


def action_groups(n, action_lines):
    """The line indices of each action: the line that opened it (ScriptBridge's script_actions) and the lines after it,
    up to the next. A line before the first action stands alone."""
    starts = set(action_lines)
    groups = []
    for i in range(n):
        if i in starts or not any(s <= i for s in starts):
            groups.append([i])
        else:
            groups[-1].append(i)
    return groups


def removal_verdict(row):
    """What one removal shows (a relaxed replay, ScriptBridge.relaxed): "fails" when the game was played to its end and
    not solved, "played" when it solved anyway (the action is not needed), else "inconclusive: <why>" (no row, the cap,
    a deck-out, a drift, a play that did not happen, a line that stalled). Only "fails" shows the action is needed
    (pr-review of #278: a drift read as necessary)."""
    if not row:
        return "inconclusive: no result row"
    if row.get("tainted_by_deckout"):
        return "inconclusive: decided by a deck-out"
    if row.get("status") not in ("solved", "failed"):
        return "inconclusive: %s" % row.get("status")
    if row.get("script_drift"):
        return "inconclusive: drift: " + row["script_drift"]
    if row.get("plays_not_made"):
        return "inconclusive: %d plays the line chose did not happen" % row["plays_not_made"]
    if row.get("status") == "solved":
        return "played"
    # A line that stalled behind a play that can never fit again (a source the removed action would have untapped)
    # lost without playing the rest: that shows nothing about the action (pr-review of #278: h_0 without Lava Spike
    # took 1 of 9 lines).
    if row.get("script_unconsumed"):
        return "inconclusive: stalled, %d lines unplayed" % row["script_unconsumed"]
    return "fails"


def minimality(build, pzl, script, out, seed):
    """Each action of a played line removed in turn, the rest as written as far as it goes (a relaxed replay): every
    removal must fail. Returns the record written to out/minimality.json; "inconclusive" counts the removals that
    showed nothing either way."""
    lines = script_lines(script)
    full = run_arm(build, pzl, "written", seed, out / "line", line_env(script, "ai"))
    row = full[-1] if full else None
    rec = {"line": line_verdict(row), "seed": seed, "actions": 0, "necessary": 0, "unnecessary": [], "inconclusive": [],
           "removals": []}
    if rec["line"] == "played" and row.get("script_lines") != len(lines):
        rec["line"] = "script_lines read %d lines, ScriptBridge %s" % (len(lines), row.get("script_lines"))
    if rec["line"] == "played":
        # The row's writer quotes every list item (LlmSpikePuzzle.toJson): the indices come back as strings.
        groups = action_groups(len(lines), [int(i) for i in row.get("script_actions") or []])
        rec["actions"] = len(groups)
        for k, g in enumerate(groups):
            d = out / ("minus-%d" % (k + 1))
            d.mkdir(parents=True, exist_ok=True)
            rest = [l for i, l in enumerate(lines) if i not in g]
            if rest:
                left = d / "line.txt"
                # Sectioned, so a line that looks like a header cannot make ScriptBridge.fromFile read none.
                left.write_text("[solution]\n" + "".join(l + "\n" for l in rest), encoding="utf-8")
                rows = run_arm(build, pzl, "written", seed, d, line_env(left, "ai", relaxed=True))
            else:  # the line without its only action is the do-nothing line: the pass arm
                rows = run_arm(build, pzl, "pass", seed, d)
            r = rows[-1] if rows else {}
            v = removal_verdict(r)
            removed = [lines[i] for i in g]
            # What the relaxed replay passed over, so a reader can see which decisions took the do-nothing answer.
            rec["removals"].append({"removed": removed, "verdict": v, "skipped": r.get("script_skipped"),
                                    "unplayed": r.get("script_unconsumed")})
            if v == "played":
                rec["unnecessary"].append(removed)
            elif v == "fails":
                rec["necessary"] += 1
            else:
                rec["inconclusive"].append(removed)
    out.mkdir(parents=True, exist_ok=True)
    (out / "minimality.json").write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    return rec


def cmd_line(a):
    build = Build(a.build, a.forge, allow_stale=a.allow_stale_build)
    pzl = Path(a.pzl).resolve()
    out = Path(a.out_dir or REPO / "runs/puzzles/lines" / pzl.stem)
    seeds = sorted(expand_seeds(a.seeds))
    if a.minimality:
        if a.opponent != "ai":
            raise SystemExit("--minimality replays against Forge's AI only: drop --opponent")
        rec = minimality(build, pzl, Path(a.script or pzl), out, seeds[0])
        print("%-24s seed %s line %s; %d of %d actions necessary%s%s" % (
            pzl.stem, seeds[0], rec["line"], rec["necessary"], rec["actions"],
            "".join("; not needed: " + " / ".join(u) for u in rec["unnecessary"]),
            "".join("; inconclusive: " + " / ".join(u) for u in rec["inconclusive"])))
        return 0 if rec["line"] == "played" and rec["necessary"] == rec["actions"] else 1
    # The prover plays one engine seed per proof: pass it that seed, the one read back.
    want, ok, spec = ((seeds[:1], "proven", str(seeds[0])) if a.opponent == "search"
                      else (seeds, "played", a.seeds))
    rows = run_arm(build, pzl, "written", spec, out, line_env(a.script or pzl, a.opponent, a.budget))
    verdicts = {r.get("seed"): line_verdict(r) for r in rows}
    for seed in want:
        print("%-24s seed %-4s %s" % (pzl.stem, seed, verdicts.get(seed, "no result row")), flush=True)
    return 0 if all(verdicts.get(s) == ok for s in want) else 1


def cmd_run(a):
    manifest = pf.load_suite(a.suite)
    if manifest.get("frozen") and not a.write:
        raise SystemExit("suite is frozen; baselines change its hash. Write to a new manifest with --write")
    ids = set(a.ids.split(",")) if a.ids else None
    out_root = Path(a.out_dir or REPO / "runs/puzzles" / ("%s-baselines" % manifest["suite"]))
    arms = [x for x in a.arms.split(",") if x]
    todo = [r for r in manifest["puzzles"] if ids is None or r["id"] in ids]
    refuse_unproven(todo, "played")
    build = Build(a.build, a.forge, allow_stale=a.allow_stale_build)

    def work(rec):
        try:
            return baseline_puzzle(build, rec, out_root, a.rollouts, a.random_base, a.search, arms, a.baselines_only)
        except Exception as e:  # one broken puzzle must not lose the others' results
            return dict(rec, status="candidate", flags=sorted(set(rec.get("flags", []) + ["runner_error"])),
                        runner_error=str(e))

    global RESUME
    RESUME = a.resume
    manifest = dict(manifest, frozen=False, suite_sha256=None)
    target = Path(a.write or a.suite)
    done = {}

    def save():  # after every puzzle, so a killed run keeps what it measured
        m = dict(manifest, puzzles=[done.get(r["id"], r) for r in manifest["puzzles"]])
        target.write_text(json.dumps(m, indent=2) + "\n", encoding="utf-8")
        return m

    with ThreadPoolExecutor(max(1, a.jobs)) as ex:
        for rec in as_completed([ex.submit(work, r) for r in todo]):
            r = rec.result()
            done[r["id"]] = r
            save()
    manifest = save()
    for r in manifest["puzzles"]:
        if r["id"] in done:
            b = r.get("baselines", {})
            print("%-24s %-9s tier=%s random=%s pass=%s first=%s floor=%s ai=%s greedy=%s line=%s %s" % (
                r["id"], r["status"], r.get("tier"),
                *[("%d/%d" % (b[x]["solved"], b[x]["n"])) if x in b else "-" for x in pf.BASELINE_ARMS],
                (r.get("reference_line") or {}).get("solver_actions"), ",".join(r.get("flags", []))))
    return 0


def cmd_retier(a):
    """Difficulty, tier_v2 and skills recomputed for every record from what it already holds (no game is played)."""
    manifest = pf.load_suite(a.suite)
    if manifest.get("frozen") and not a.write:
        raise SystemExit("suite is frozen; re-tiering changes its records. Write to a new manifest with --write")
    refuse_unproven(manifest["puzzles"], "written")
    puzzles = [apply_difficulty(r) for r in manifest["puzzles"]]
    Path(a.write or a.suite).write_text(json.dumps(dict(manifest, puzzles=puzzles, frozen=False, suite_sha256=None),
                                                   indent=2) + "\n", encoding="utf-8")
    counts = {}
    for r in puzzles:
        counts[r.get("tier_v2")] = counts.get(r.get("tier_v2"), 0) + 1
    print("tier_v2: " + ", ".join("%s=%d" % (k, counts[k]) for k in sorted(counts, key=str)))
    return 0


def cmd_verify(a):
    build = Build(a.build, a.forge, allow_stale=a.allow_stale_build)
    manifest = pf.load_suite(a.suite)
    out_root = Path(a.out_dir or REPO / "runs/puzzles" / ("%s-verify" % manifest["suite"]))
    todo = [r for r in manifest["puzzles"] if pf.has_line(r.get("reference_line"))]
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
    ap.add_argument("--build", default=None,
                    help="dir with classes/ and classpath.txt (default: the newest acceptance build of this checkout)")
    ap.add_argument("--allow-stale-build", action="store_true",
                    help="run --build although it was not compiled from this checkout's overlay (recorded in build.json)")
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
    r.add_argument("--baselines-only", action="store_true",
                   help="measure the arms only (no line search, no verification), then recompute the v1 tier, "
                        "the status and tier_v2")
    r.add_argument("--resume", action="store_true",
                   help="reuse an arm's rows when this build already ran exactly its seeds in --out-dir")
    v = sub.add_parser("verify")
    v.add_argument("--suite", required=True)
    v.add_argument("-j", "--jobs", type=int, default=4)
    v.add_argument("--out-dir", default=None)
    pr = sub.add_parser("prove")
    pr.add_argument("--suite", required=True)
    pr.add_argument("--ids", default=None)
    pr.add_argument("--budget", type=int, default=2000, help="rollouts the search may play per puzzle")
    pr.add_argument("--opponent", choices=("search", "ai"), default="search",
                    help="search: beat every opponent choice; ai: beat the Forge AI's")
    pr.add_argument("--unique-budget", type=int, default=None,
                    help="rollouts for the uniqueness search (default: the runner's, the proof budget; 0 skips it)")
    pr.add_argument("-j", "--jobs", type=int, default=4)
    pr.add_argument("--out-dir", default=None)
    pr.add_argument("--write", default=None, help="manifest to write (default: --suite in place)")
    ln = sub.add_parser("line", help="play a puzzle's written solution through the model seat (exit 1 unless played)")
    ln.add_argument("--pzl", required=True, help="the puzzle; its [solution] section is the line unless --script")
    ln.add_argument("--script", default=None, help="a file of solution lines (default: the puzzle's [solution])")
    ln.add_argument("--opponent", choices=("ai", "search"), default="ai",
                    help="ai: Forge's AI replies; search: prove the line beats every reply the engine offers")
    ln.add_argument("--budget", type=int, default=200, help="--opponent search: rollouts the prover may play")
    ln.add_argument("--seeds", default="1")
    ln.add_argument("--minimality", action="store_true",
                    help="replay the line with each action removed in turn: exit 0 only when every removal fails")
    ln.add_argument("--out-dir", default=None)
    rt = sub.add_parser("retier", help="recompute difficulty, tier_v2 and skills from stored proofs and baselines")
    rt.add_argument("--suite", required=True)
    rt.add_argument("--write", default=None)
    a = ap.parse_args(argv)
    return {"run": cmd_run, "verify": cmd_verify, "prove": cmd_prove, "line": cmd_line, "retier": cmd_retier}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
