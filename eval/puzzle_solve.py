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

An openrouter: model sends each puzzle's position to openrouter.ai: the egress opt-in
(OPENROUTER_ALLOW_EGRESS=1, normally in secrets.env) is required and never set here.
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


def spent(out_root):
    prices = cost_report.load_prices()
    return sum(cost_report.report_dir(str(d), prices)["usd"] for d in Path(out_root).glob("*") if d.is_dir())


def condition(a):
    """Every setting that changes what the seat sees, pinned explicitly (never inherited from the shell),
    as run_batch.py pins a game's: the same seat as the games it is compared with."""
    return {"CONTEXT_PROFILE": a.context_profile, "MEMORY_TOOLS": "1" if a.memory_tools else "0",
            "RULES_TOOL": "1" if a.rules_tool else "0", "NATIVE_TOOLS": "1", "ANSWER_TOOL": "1",
            "LOOKAHEAD": "0", "MULLIGAN_HEURISTIC": "0", "SKIP_HEURISTICS": "0"}


def solve_puzzle(build, rec, model, k, out_root, suite_sha, cond):
    out = Path(out_root) / rec["id"]
    out.mkdir(parents=True, exist_ok=True)
    # cost_report prices a directory's calls from its manifest when there is no game row (a puzzle has none).
    (out / "batch-manifest.json").write_text(json.dumps(
        {"condition": {"smart_a": model, "fast_a": model, "puzzle": rec["id"], "suite_sha256": suite_sha, "env": cond}}))
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
    ap.add_argument("--forge", default=None)
    a = ap.parse_args(argv)

    manifest = pf.load_suite(a.suite)
    problems = pf.validate_suite(manifest)
    if problems or not manifest.get("frozen"):
        raise SystemExit("suite does not validate or is not frozen: %s" % "; ".join(problems[:5]))
    load_secrets()
    # A loopback endpoint (the acceptance fake model) sends nothing off the machine, as the JVM's own check says.
    if (a.model.startswith("openrouter:") and os.environ.get("OPENROUTER_ALLOW_EGRESS") != "1"
            and not is_loopback(os.environ.get("OPENROUTER_BASE_URL", ""))):
        raise SystemExit("OPENROUTER_ALLOW_EGRESS=1 is not set: an openrouter: model sends each position to "
                         "openrouter.ai, and that needs the owner's opt-in (secrets.env)")
    ids = set(a.ids.split(",")) if a.ids else None
    todo = [r for r in manifest["puzzles"] if r["split"] == a.split and r["status"] == "active"
            and (ids is None or r["id"] in ids)]
    build = pb.Build(a.build, a.forge)
    out_root = Path(a.out)
    out_root.mkdir(parents=True, exist_ok=True)
    print("[puzzles] %d active %s puzzles, k=%d, model %s, budget $%.2f (spent $%.2f)"
          % (len(todo), a.split, a.k, a.model, a.budget_usd, spent(out_root)), flush=True)

    def work(rec):
        if spent(out_root) >= a.budget_usd:
            return rec["id"], None, []
        return solve_puzzle(build, rec, a.model, a.k, out_root, manifest["suite_sha256"], condition(a))

    skipped = 0
    with ThreadPoolExecutor(max(1, a.jobs)) as ex:
        for pid, rc, rows in ex.map(work, todo):
            if rc is None:
                skipped += 1
                print("[budget] not started: %s" % pid, flush=True)
                continue
            solved = sum(1 for r in rows if ps.attempt_success(r)[0]) if rows else 0
            print("%-28s rc=%s attempts=%d solved=%d  [cost] $%.3f" % (pid, rc, len(rows), solved, spent(out_root)),
                  flush=True)
    print("[cost] $%.3f over %d puzzles (%d not started for budget)" % (spent(out_root), len(todo) - skipped, skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
