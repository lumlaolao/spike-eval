#!/usr/bin/env python3
"""Run one model on one split of a frozen PUZ suite, k attempts per puzzle (eval/PUZZLES.md).

    python3 eval/puzzle_solve.py --suite eval/suites/puzzles-forge-builtin-v1.json --split open \\
        --model openrouter:openai/gpt-6-luna --budget-usd 1.00 --out runs/puzzles/luna-open

Each active puzzle of the split gets one JVM with PZL_SEEDS = the k attempt seeds, CONTROLLER_A=llm and
the model on both tiers; every result row is stamped with the suite's sha256, so puzzle_score.py
refuses to pool it with another suite version. The run stops starting puzzles once the directory's
estimated spend (eval/cost_report.py) reaches --budget-usd, which is required, and prints the cost so
far after each puzzle. Then score it:

    python3 eval/puzzle_score.py --suite <suite> --split open runs/puzzles/luna-open

--resume continues a stopped run (eval/PUZZLES.md, "Running a model").

An openrouter: model sends each puzzle's position to openrouter.ai: the egress opt-in
(OPENROUTER_ALLOW_EGRESS=1, normally in secrets.env) is required and never set here. So does a
jev: decision model (OpenRouter's Decisions API, DecisionsBridge), which also cannot take --memory-tools.
"""

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cost_report  # noqa: E402
import puzzle_baselines as pb  # noqa: E402
import puzzle_format as pf  # noqa: E402
import puzzle_score as ps  # noqa: E402

# The attempt seeds: k distinct seeds per puzzle, the same for every solver, outside every reserved band.
ATTEMPT_BASE = 1
RUN_FILE = "puzzle-run.json"


def load_secrets():
    path = pf.REPO / "secrets.env"
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def is_loopback(url):
    """True when url names this machine (LlmSpikeGui.LLMBridgeFactory.isLoopback)."""
    from urllib.parse import urlparse
    try:
        return (urlparse(url).hostname or "") in ("localhost", "127.0.0.1", "::1")
    except ValueError:
        return False


def spent(out_root, bound=False):
    """What the run's directories cost at the list price; `bound`: at each model's dearest endpoint, as the budget
    is checked."""
    prices = cost_report.load_prices()
    prices = cost_report.budget_prices(prices) if bound else prices
    return sum(cost_report.report_dir(str(d), prices)["usd"] for d in Path(out_root).glob("*") if d.is_dir())


def over_budget(out_root, budget_usd):
    """The budget is checked at each model's dearest endpoint, never at the list price."""
    return spent(out_root, bound=True) >= budget_usd


def condition(a):
    """Every setting that changes what the seat sees, pinned explicitly (never inherited from the shell),
    as run_batch.py pins a game's: the same seat as the games it is compared with."""
    return {"CONTEXT_PROFILE": a.context_profile, "MEMORY_TOOLS": "1" if a.memory_tools else "0",
            "RULES_TOOL": "1" if a.rules_tool else "0", "NATIVE_TOOLS": "1", "ANSWER_TOOL": "1",
            "LOOKAHEAD": "0", "MULLIGAN_HEURISTIC": "0", "SKIP_HEURISTICS": "0"}


def already_done(out, k, suite_sha):
    """True when `out` holds a result row on this suite for each of the k attempt seeds: a resumed run skips it."""
    seeds = {r.get("seed") for r in pb.read_results(out) if r.get("suite_sha256") == suite_sha}
    return all(s in seeds for s in range(ATTEMPT_BASE, ATTEMPT_BASE + k))


def set_aside_partial(out):
    """A puzzle cut off part way is played again from its first seed; its rows so far and its JVM log are renamed,
    so a scorer never counts them and the replay does not overwrite them, and its game rows stay, so cost_report
    still prices what was spent."""
    out = Path(out)
    n = max(len(list(out.glob("puzzle-results.partial*.jsonl"))), len(list(out.glob("jvm.partial*.log"))))
    for name, aside in (("puzzle-results.jsonl", "puzzle-results.partial%d.jsonl"), ("jvm.log", "jvm.partial%d.log")):
        if (out / name).is_file():
            (out / name).rename(out / (aside % n))


def prepare(out, k, suite_sha, resume):
    """"done" when a resumed run already has this puzzle's k attempts; otherwise "run", a partial set aside."""
    if resume and already_done(out, k, suite_sha):
        return "done"
    if resume:
        set_aside_partial(out)
    return "run"


def write_json(path, obj):
    """Written whole, then moved, so a restart (or a reader, eval/puzzle_watch.py) never sees half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, path)


def write_manifest(out, model, suite_sha, puzzle_id, cond, build=None):
    """The condition a puzzle's directory was played under; cost_report prices its calls from it when there is no
    game row (a puzzle has none). `build`: puzzle_baselines.Build.provenance(), the build it ran on."""
    write_json(Path(out) / "batch-manifest.json", {"condition": {"smart_a": model, "fast_a": model, "puzzle": puzzle_id,
                                                                 "suite_sha256": suite_sha, "env": cond},
                                                   "build": build})


def write_run(out_root, ids, k, budget_usd, finished, stopped=None, build=None):
    """RUN_FILE: what this invocation will play, so eval/puzzle_watch.py can show the puzzles not started yet, tell
    when each is done (k result rows) and when the run is over (`finished`, written after the last puzzle) or was
    stopped part way (`stopped`: the exception or interrupt that ended it). `build`: the build the whole run uses
    (Build.provenance(), with stale_build_allowed when --allow-stale-build let an old one run)."""
    run = {"ids": ids, "k": k, "budget_usd": budget_usd, "finished": finished}
    if stopped is not None:
        run["stopped"] = stopped
    if build is not None:
        run["build"] = build
    write_json(Path(out_root) / RUN_FILE, run)


def resume_conflicts(out_root, ids, model, suite_sha, cond):
    """The puzzle directories under out_root made under another model, suite or seat condition: a resume into
    them would pool two conditions in one score (STANDARDS section 4)."""
    bad, now = [], {"smart_a": model, "suite_sha256": suite_sha, "env": cond}
    for pid in ids:
        f = Path(out_root) / pid / "batch-manifest.json"
        if not f.is_file():
            continue
        try:
            was = json.loads(f.read_text()).get("condition") or {}
        except ValueError:
            bad.append("%s: unreadable batch-manifest.json" % pid)
            continue
        diff = [key for key in now if was.get(key) != now[key]]
        if diff:
            bad.append("%s: %s differ" % (pid, ", ".join(diff)))
    return bad


def solve_puzzle(build, rec, model, k, out_root, suite_sha, cond):
    out = Path(out_root) / rec["id"]
    write_manifest(out, model, suite_sha, rec["id"], cond, build.provenance())
    env = dict(cond, PUZZLE=str(pf.pzl_path(rec)), CONTROLLER_A="llm", CONTROLLER_B="ai",
               PZL_SEEDS="%d..%d" % (ATTEMPT_BASE, ATTEMPT_BASE + k - 1), PZL_SUITE_SHA256=suite_sha,
               SMART_MODEL_A=model, FAST_MODEL_A=model, GAME_CAP_MS="1800000")
    for key in ("OPENROUTER_API_KEY", "OPENROUTER_ALLOW_EGRESS", "OPENROUTER_BASE_URL"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    rc = build.run(env, out, timeout_s=4 * 3600, keep_model_env=True)
    rows = pb.read_results(out)
    return rec["id"], rc, rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", required=True)
    ap.add_argument("--split", required=True, choices=pf.SPLITS)
    ap.add_argument("--model", required=True)
    ap.add_argument("--k", type=int, default=4)
    ap.add_argument("--budget-usd", type=float, required=True)
    ap.add_argument("--ids", default=None, help="only these puzzle ids (comma-separated): a pilot, not a score")
    ap.add_argument("--out", required=True)
    ap.add_argument("-j", "--jobs", type=int, default=2)
    ap.add_argument("--context-profile", default="v4-faithful")
    ap.add_argument("--memory-tools", action="store_true")
    ap.add_argument("--rules-tool", action="store_true")
    ap.add_argument("--build", default=None)
    ap.add_argument("--allow-stale-build", action="store_true",
                    help="run --build although it was not compiled from this checkout's overlay (recorded)")
    ap.add_argument("--forge", default=None)
    ap.add_argument("--resume", action="store_true",
                    help="skip puzzles whose k attempts on this suite are already in --out (a run stopped part way)")
    a = ap.parse_args(argv)
    if cost_report.is_decisions(a.model) and a.memory_tools:
        raise SystemExit("--memory-tools with a %s model: a decision model cannot call a lookup, so the run would record "
                         "MEMORY_TOOLS=1 for a seat that never used it (the JVM refuses it too)" % cost_report.DECISIONS_PREFIX)
    if a.rules_tool and not a.memory_tools:
        raise SystemExit("--rules-tool needs --memory-tools: the seat offers the rules lookup only with the lookup "
                         "tools on, so --rules-tool alone records RULES_TOOL=1 for puzzles that never had it")
    # cost_report prices an unpriced model at $0, so --budget-usd would never stop it (run_batch.py refuses too).
    if cost_report.model_id(a.model) and cost_report.price_of(cost_report.load_prices(), a.model) is None:
        raise SystemExit("--budget-usd needs a price for %s in eval/pricing/prices.json"
                         % cost_report.model_id(a.model))

    manifest = pf.load_suite(a.suite)
    problems = pf.validate_suite(manifest)
    if problems or not manifest.get("frozen"):
        raise SystemExit("suite does not validate or is not frozen: %s" % "; ".join(problems[:5]))
    load_secrets()
    # A loopback endpoint (the acceptance fake model) sends nothing off the machine, as the JVM's own check says.
    if (a.model.startswith(cost_report.API_PREFIXES) and os.environ.get("OPENROUTER_ALLOW_EGRESS") != "1"
            and not is_loopback(os.environ.get("OPENROUTER_BASE_URL", ""))):
        raise SystemExit("OPENROUTER_ALLOW_EGRESS=1 is not set: an openrouter: or jev: model sends each position to "
                         "openrouter.ai, and that needs the owner's opt-in (secrets.env)")
    ids = set(a.ids.split(",")) if a.ids else None
    todo = [r for r in manifest["puzzles"] if r["split"] == a.split and r["status"] == "active"
            and (ids is None or r["id"] in ids)]
    if a.resume:
        bad = resume_conflicts(a.out, [r["id"] for r in todo], a.model, manifest["suite_sha256"], condition(a))
        if bad:
            raise SystemExit("--resume into a directory made under another condition (%d puzzles): %s"
                             % (len(bad), "; ".join(bad[:5])))
    build = pb.Build(a.build, a.forge, allow_stale=a.allow_stale_build)
    prov = build.provenance()
    out_root = Path(a.out)
    out_root.mkdir(parents=True, exist_ok=True)
    print("[puzzles] %d active %s puzzles, k=%d, model %s, budget $%.2f (spent $%.2f; $%.2f at the dearest "
          "endpoint, which the budget is checked at)"
          % (len(todo), a.split, a.k, a.model, a.budget_usd, spent(out_root), spent(out_root, bound=True)), flush=True)

    ids = [r["id"] for r in todo]
    write_run(out_root, ids, a.k, a.budget_usd, finished=False, build=prov)

    def work(rec):
        out = out_root / rec["id"]
        if prepare(out, a.k, manifest["suite_sha256"], a.resume) == "done":
            return rec["id"], "done", pb.read_results(out)
        if over_budget(out_root, a.budget_usd):
            return rec["id"], None, []
        return solve_puzzle(build, rec, a.model, a.k, out_root, manifest["suite_sha256"], condition(a))

    skipped = 0
    try:
        with ThreadPoolExecutor(max(1, a.jobs)) as ex:
            for pid, rc, rows in ex.map(work, todo):
                if rc is None:
                    skipped += 1
                    print("[budget] not started: %s ($%.2f at the dearest endpoint)"
                          % (pid, spent(out_root, bound=True)), flush=True)
                    continue
                solved = sum(1 for r in rows if ps.attempt_success(r)[0]) if rows else 0
                print("%-28s rc=%s attempts=%d solved=%d  [cost] $%.3f"
                      % (pid, rc, len(rows), solved, spent(out_root)), flush=True)
    except BaseException as e:  # an interrupt too: a watcher must not wait --stall minutes to learn the run ended
        write_run(out_root, ids, a.k, a.budget_usd, finished=False, stopped=repr(e), build=prov)
        raise
    write_run(out_root, ids, a.k, a.budget_usd, finished=True, build=prov)
    print("[cost] $%.3f over %d puzzles (%d not started for budget)" % (spent(out_root), len(todo) - skipped, skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
